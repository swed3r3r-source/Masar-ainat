"""مراقبة درجة الحرارة (§20) عبر محوّل واضح + بيئة محاكاة معزولة.

مبدأ حاكم: **لا تُعرض المحاكاة على أنها تكامل حقيقي.** كل قراءة تحمل
``source``؛ وقراءات ``SIMULATION`` تُوسَم في كل واجهة ولا تدخل تقارير
الامتثال. عند غياب حساس فعلي لا تُختلق قيمة — تُعرض الحالة ``NO_SENSOR``.

السائق لا يُدخل الحرارة يدويًا إطلاقًا (§18)؛ لا يوجد مسار API يسمح بذلك
لدور ``DRIVER``.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from typing import Any, Protocol

import pgwire
from masar_core.config import get_config
from masar_core.constants import AlertType, AuditAction, TemperatureSource, TemperatureStatus
from masar_core.errors import DependencyUnavailable, Forbidden, NotFound, ValidationError
from masar_db.driver import SecurityContext, session, transaction

from . import alerts, audit, events

#: النطاقات الافتراضية إن لم تُعرّف في جدول temperature_ranges
FALLBACK_RANGES: dict[str, tuple[float, float]] = {
    "AMBIENT": (15.0, 30.0),
    "CHILLED": (2.0, 8.0),
    "FROZEN": (-25.0, -15.0),
    "DEEP_FROZEN": (-80.0, -60.0),
    "CONTROLLED": (2.0, 25.0),
}


class TemperatureProvider(Protocol):
    name: str
    is_real: bool

    def fetch(self, sensor_codes: list[str]) -> list[dict[str, Any]]: ...


class NoProvider:
    """لا يوجد مزوّد حساسات — الحالة الافتراضية والصادقة."""

    name = "none"
    is_real = False

    def fetch(self, sensor_codes: list[str]) -> list[dict[str, Any]]:
        raise DependencyUnavailable(
            "لا يوجد مزوّد حساسات حرارة مُعدّ. الحالة المعروضة NO_SENSOR وهي صحيحة، "
            "وليست عطلًا. لتفعيل التكامل اضبط MASAR_TEMPERATURE_PROVIDER."
        )


class HttpTemperatureProvider:
    """محوّل مزوّد خارجي عبر HTTP — الواجهة جاهزة للتوصيل.

    ⚠️ لم يكن ممكنًا اختباره في هذه البيئة لعدم توفر خدمة حساسات حقيقية.
    البنية (المفتاح، المهلة، تحويل الوحدات) مكتوبة، والتفعيل يحتاج عنوان
    الخدمة ومفتاحها ثم اختبار تكامل حقيقي قبل الاعتماد.
    """

    name = "http"
    is_real = True

    def __init__(self) -> None:
        cfg = get_config().temperature
        if not cfg.ingest_url:
            raise DependencyUnavailable("MASAR_TEMPERATURE_URL غير محدد")
        self.url = cfg.ingest_url
        self.api_key = cfg.ingest_api_key

    def fetch(self, sensor_codes: list[str]) -> list[dict[str, Any]]:
        import json
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            f"{self.url}?sensors={','.join(sensor_codes)}",
            headers={"Authorization": f"Bearer {self.api_key or ''}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise DependencyUnavailable(
                f"تعذر الوصول إلى مزوّد الحساسات: {exc}"
            ) from exc
        return payload.get("readings", [])


class SimulationProvider:
    """محاكاة اختبارية — تعمل فقط عند ``MASAR_TEMPERATURE_SIMULATION=1``."""

    name = "simulation"
    is_real = False

    def __init__(self, seed: int = 20260826) -> None:
        if not get_config().temperature.simulation_enabled:
            raise DependencyUnavailable(
                "محاكاة الحرارة معطّلة. فعّلها بـ MASAR_TEMPERATURE_SIMULATION=1 "
                "في بيئة الاختبار فقط."
            )
        self.random = random.Random(seed)

    def fetch(self, sensor_codes: list[str]) -> list[dict[str, Any]]:
        now = dt.datetime.now(dt.timezone.utc)
        return [
            {
                "sensor_code": code,
                "celsius": round(5.0 + math.sin(now.timestamp() / 600 + index) * 2.5, 2),
                "recorded_at": now.isoformat(),
            }
            for index, code in enumerate(sensor_codes)
        ]


def build_provider(name: str | None = None) -> TemperatureProvider:
    cfg = get_config().temperature
    key = (name or cfg.provider).lower()
    if key == "http":
        return HttpTemperatureProvider()
    if key == "simulation":
        return SimulationProvider()
    return NoProvider()


def provider_status() -> dict[str, Any]:
    """يعلن حالة التكامل بصراحة — يُعرض في الواجهة (§20/§34)."""
    cfg = get_config().temperature
    try:
        provider = build_provider()
        available = not isinstance(provider, NoProvider)
        name = provider.name
        is_real = provider.is_real
        message = None
    except DependencyUnavailable as exc:
        available = False
        name = cfg.provider
        is_real = False
        message = exc.message
    return {
        "provider": name,
        "available": available,
        "is_real_integration": is_real,
        "is_simulation": name == "simulation",
        "message_ar": message or (
            "تكامل حساسات حقيقي مفعّل" if is_real
            else "لا يوجد تكامل حساسات حقيقي — تُعرض الحالة NO_SENSOR"
        ),
        "stale_after_seconds": cfg.stale_after_seconds,
    }


def _range_for(conn: pgwire.Connection, mode: str) -> tuple[float, float]:
    row = conn.fetch_one(
        "SELECT min_celsius, max_celsius FROM temperature_ranges "
        "WHERE mode = $1 AND is_active",
        [mode],
    )
    if row:
        return float(row["min_celsius"]), float(row["max_celsius"])
    return FALLBACK_RANGES.get(mode, FALLBACK_RANGES["AMBIENT"])


def ingest_readings(
    context: SecurityContext,
    readings: list[dict[str, Any]],
    *,
    source: str = TemperatureSource.SENSOR,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """يستقبل قراءات ويقارنها بالنطاق المطلوب وينشئ تنبيهًا عند المخالفة."""
    if context.role == "DRIVER":
        raise Forbidden(
            "السائق لا يُدخل درجة الحرارة — القراءات تأتي من الحساس أو المزوّد"
        )
    if source not in set(TemperatureSource):
        raise ValidationError("مصدر قراءة غير معروف")

    accepted = breaches = 0
    with transaction(context) as conn:
        for reading in readings[:1000]:
            sensor_code = reading.get("sensor_code")
            sensor = None
            if sensor_code:
                sensor = conn.fetch_one(
                    "SELECT s.id::text AS id, s.box_id::text AS box_id, "
                    "s.vehicle_id::text AS vehicle_id FROM sensors s WHERE s.code = $1",
                    [sensor_code],
                )
            box_id = reading.get("box_id") or (sensor["box_id"] if sensor else None)
            if not box_id:
                continue

            try:
                celsius = float(reading["celsius"])
            except (KeyError, TypeError, ValueError):
                continue

            recorded_at = reading.get("recorded_at") or dt.datetime.now(dt.timezone.utc)
            if isinstance(recorded_at, str):
                from masar_core.timeutil import parse_datetime

                recorded_at = parse_datetime(recorded_at, field="وقت القراءة")

            # الشحنات المرتبطة بهذا الصندوق والجارية الآن
            shipments = conn.fetch_all(
                "SELECT id::text AS id, reference, temperature_mode, "
                "route_id::text AS route_id, hub_id::text AS hub_id, "
                "driver_id::text AS driver_id FROM shipments "
                "WHERE box_id = $1::uuid AND status IN "
                "('PICKED_UP','ARRIVED_DELIVERY','IN_PROGRESS','ARRIVED_PICKUP')",
                [box_id],
            )
            targets = list(shipments) or [None]

            for shipment in targets:
                mode = shipment["temperature_mode"] if shipment else "AMBIENT"
                minimum, maximum = _range_for(conn, mode)
                if celsius > maximum:
                    status, kind = TemperatureStatus.BREACH_HIGH, "HIGH"
                elif celsius < minimum:
                    status, kind = TemperatureStatus.BREACH_LOW, "LOW"
                else:
                    status, kind = TemperatureStatus.IN_RANGE, None

                conn.execute(
                    "INSERT INTO temperature_readings (sensor_id, box_id, shipment_id, "
                    "route_id, celsius, humidity_pct, recorded_at, source, status, "
                    "is_test_data) VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,$6,"
                    "$7::timestamptz,$8,$9,$10)",
                    [sensor["id"] if sensor else None, box_id,
                     shipment["id"] if shipment else None,
                     shipment["route_id"] if shipment else None,
                     celsius, reading.get("humidity_pct"), recorded_at, source, status,
                     source == TemperatureSource.SIMULATION],
                )
                accepted += 1

                if kind and shipment:
                    breaches += _handle_breach(
                        conn, shipment, box_id, sensor, celsius, minimum, maximum,
                        kind, recorded_at, source,
                    )
                elif shipment:
                    _close_open_breach(conn, shipment["id"], recorded_at)

            if sensor:
                conn.execute(
                    "UPDATE sensors SET last_seen_at = $1::timestamptz WHERE id = $2::uuid",
                    [recorded_at, sensor["id"]],
                )
    return {"accepted": accepted, "breaches": breaches, "source": source}


def _handle_breach(
    conn: pgwire.Connection, shipment: Any, box_id: str, sensor: Any,
    celsius: float, minimum: float, maximum: float, kind: str,
    recorded_at: dt.datetime, source: str,
) -> int:
    open_breach = conn.fetch_one(
        "SELECT id::text AS id, min_celsius, max_celsius FROM temperature_breaches "
        "WHERE shipment_id = $1::uuid AND ended_at IS NULL AND breach_kind = $2 "
        "ORDER BY started_at DESC LIMIT 1",
        [shipment["id"], kind],
    )
    if open_breach:
        conn.execute(
            "UPDATE temperature_breaches SET min_celsius = least(min_celsius, $1), "
            "max_celsius = greatest(max_celsius, $1) WHERE id = $2::uuid",
            [celsius, open_breach["id"]],
        )
        return 0

    breach_id = conn.fetch_value(
        "INSERT INTO temperature_breaches (shipment_id, box_id, route_id, sensor_id, "
        "started_at, min_celsius, max_celsius, required_min_c, required_max_c, "
        "breach_kind, is_test_data) "
        "VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::timestamptz,$6,$6,$7,$8,$9,$10) "
        "RETURNING id::text",
        [shipment["id"], box_id, shipment["route_id"],
         sensor["id"] if sensor else None, recorded_at, celsius, minimum, maximum,
         kind, source == TemperatureSource.SIMULATION],
    )
    label = "أعلى من" if kind == "HIGH" else "أقل من"
    alerts.raise_alert(
        conn, AlertType.TEMPERATURE_BREACH,
        title_ar="مخالفة درجة حرارة",
        body_ar=(
            f"الشحنة {shipment['reference']}: القراءة {celsius}°م {label} النطاق "
            f"المطلوب [{minimum}, {maximum}]"
            + (" — مصدر محاكاة" if source == "SIMULATION" else "")
        ),
        shipment_id=shipment["id"], route_id=shipment["route_id"],
        hub_id=shipment["hub_id"], driver_id=shipment["driver_id"],
        dedupe_key=f"TEMP:{breach_id}",
        context_data={"celsius": celsius, "required": [minimum, maximum],
                      "breach_id": breach_id, "source": source},
        is_test_data=source == TemperatureSource.SIMULATION,
    )
    return 1


def _close_open_breach(
    conn: pgwire.Connection, shipment_id: str, recorded_at: dt.datetime
) -> None:
    conn.execute(
        "UPDATE temperature_breaches SET ended_at = $1::timestamptz, "
        "duration_minutes = EXTRACT(EPOCH FROM ($1::timestamptz - started_at)) / 60 "
        "WHERE shipment_id = $2::uuid AND ended_at IS NULL",
        [recorded_at, shipment_id],
    )


def shipment_temperature(context: SecurityContext, shipment_id: str) -> dict[str, Any]:
    cfg = get_config().temperature
    with session(context) as conn:
        shipment = conn.fetch_one(
            "SELECT id::text AS id, reference, temperature_mode, box_id::text AS box_id "
            "FROM shipments WHERE id = $1::uuid",
            [shipment_id],
        )
        if shipment is None:
            raise NotFound("الشحنة غير موجودة")
        minimum, maximum = _range_for(conn, shipment["temperature_mode"])
        readings = conn.fetch_all(
            "SELECT celsius, humidity_pct, recorded_at, source, status "
            "FROM temperature_readings WHERE shipment_id = $1::uuid "
            "ORDER BY recorded_at DESC LIMIT 500",
            [shipment_id],
        )
        breaches = conn.fetch_all(
            "SELECT id::text AS id, started_at, ended_at, duration_minutes, "
            "min_celsius, max_celsius, breach_kind, action_taken, resolved_at "
            "FROM temperature_breaches WHERE shipment_id = $1::uuid "
            "ORDER BY started_at DESC",
            [shipment_id],
        )
        custody = conn.fetch_all(
            "SELECT from_party, to_party, occurred_at, lat, lon "
            "FROM custody_transfers WHERE shipment_id = $1::uuid ORDER BY occurred_at",
            [shipment_id],
        )

    if not readings:
        status = TemperatureStatus.NO_SENSOR
        message = (
            "لا توجد قراءات حرارة لهذه الشحنة — لم يُربط حساس أو لم يُفعّل التكامل. "
            "هذه ليست قيمة صفرية بل غياب قياس."
        )
    else:
        latest = readings[0]
        age = (dt.datetime.now(dt.timezone.utc) - latest["recorded_at"]).total_seconds()
        status = (
            TemperatureStatus.STALE if age > cfg.stale_after_seconds
            else latest["status"]
        )
        message = None

    return {
        "shipment_id": shipment_id,
        "reference": shipment["reference"],
        "temperature_mode": shipment["temperature_mode"],
        "required_range": {"min": minimum, "max": maximum},
        "status": status,
        "message_ar": message,
        "provider": provider_status(),
        "readings": [dict(row) for row in readings],
        "breaches": [dict(row) for row in breaches],
        "custody_chain": [dict(row) for row in custody],
        "has_simulated_data": any(r["source"] == "SIMULATION" for r in readings),
    }


def resolve_breach(
    context: SecurityContext, breach_id: str, action_taken: str,
    *, ip_address: str | None = None,
) -> dict[str, Any]:
    with transaction(context) as conn:
        row = conn.fetch_one(
            "SELECT id::text AS id, shipment_id::text AS shipment_id, resolved_at "
            "FROM temperature_breaches WHERE id = $1::uuid FOR UPDATE",
            [breach_id],
        )
        if row is None:
            raise NotFound("سجل المخالفة غير موجود")
        conn.execute(
            "UPDATE temperature_breaches SET action_taken = $1, resolved_by = $2::uuid, "
            "resolved_at = now() WHERE id = $3::uuid",
            [action_taken, context.user_id, breach_id],
        )
        conn.execute(
            "UPDATE alerts SET resolved_at = now(), action_note = $1, "
            "acknowledged_by = coalesce(acknowledged_by, $2::uuid), "
            "acknowledged_at = coalesce(acknowledged_at, now()) "
            "WHERE dedupe_key = $3 AND resolved_at IS NULL",
            [action_taken, context.user_id, f"TEMP:{breach_id}"],
        )
        audit.record(
            conn, context, AuditAction.EXCEPTION_ACTION,
            entity_type="temperature_breach", entity_id=breach_id,
            new_value={"action_taken": action_taken}, reason=action_taken,
            ip_address=ip_address,
        )
    return {"breach_id": breach_id, "resolved": True}


def poll_provider(context: SecurityContext | None = None) -> dict[str, Any]:
    """مهمة دورية تسحب القراءات من المزوّد المُعدّ (إن وُجد)."""
    context = context or SecurityContext.system("TEMPERATURE_POLLER")
    try:
        provider = build_provider()
    except DependencyUnavailable as exc:
        return {"polled": 0, "skipped_reason": exc.message}
    if isinstance(provider, NoProvider):
        return {"polled": 0, "skipped_reason": "لا يوجد مزوّد مُعدّ"}

    with session(context) as conn:
        codes = [
            row["code"] for row in conn.fetch_all(
                "SELECT code FROM sensors WHERE is_active AND box_id IS NOT NULL")
        ]
    if not codes:
        return {"polled": 0, "skipped_reason": "لا توجد حساسات مربوطة بصناديق"}

    readings = provider.fetch(codes)
    source = (
        TemperatureSource.SIMULATION if provider.name == "simulation"
        else TemperatureSource.SENSOR
    )
    return ingest_readings(context, readings, source=source)
