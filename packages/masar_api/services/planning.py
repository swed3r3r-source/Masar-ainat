"""خدمة التخطيط: تشغيل المحرك، حفظ الخطة كمسودة دائمة، الاعتماد والإرسال."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pgwire
from masar_core.config import get_config
from masar_core.constants import (
    AuditAction,
    PlanStatus,
    RouteStatus,
    ShipmentStatus,
    UnplannableReason,
)
from masar_core.errors import Conflict, NotFound, ValidationError
from masar_core.timeutil import DEFAULT_TZ, combine_local, to_local
from masar_db.driver import SecurityContext, session, transaction
from masar_opt.engine import (
    HubInput,
    PlanResult,
    ShipmentInput,
    VehicleInput,
    build_problem,
    run_engine,
)
from masar_opt.model import NodeKind, to_datetime
from masar_opt.solver import SolveOptions

from . import audit, events, settings as settings_service

PLANNABLE_STATUSES = (
    ShipmentStatus.VALIDATED,
    ShipmentStatus.PENDING_ASSIGNMENT,
    ShipmentStatus.UNPLANNABLE,
    ShipmentStatus.PLANNED,
)


def _load_hub(conn: pgwire.Connection, hub_id: str) -> Any:
    hub = conn.fetch_one(
        "SELECT id::text AS id, code, name_ar, lat, lon, working_hours, "
        "region_id::text AS region_id, city_id::text AS city_id "
        "FROM hubs WHERE id = $1::uuid AND is_active",
        [hub_id],
    )
    if hub is None:
        raise NotFound("مركز الانطلاق غير موجود أو غير مفعّل")
    return hub


def _hub_working_window(hub: Any, day: dt.date, timezone_name: str) -> tuple[dt.datetime, dt.datetime]:
    weekday_keys = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    hours = hub["working_hours"] or {}
    entry = hours.get(weekday_keys[day.weekday()]) if isinstance(hours, dict) else None
    if entry and isinstance(entry, (list, tuple)) and len(entry) == 2:
        from masar_core.timeutil import parse_time

        opens = combine_local(day, parse_time(entry[0]), timezone_name)
        closes = combine_local(day, parse_time(entry[1]), timezone_name)
    else:
        opens = combine_local(day, dt.time(6, 0), timezone_name)
        closes = combine_local(day, dt.time(22, 0), timezone_name)
    return opens, closes


def available_drivers(conn: pgwire.Connection, hub_id: str, day: dt.date) -> list[Any]:
    """السائقون المتاحون فعليًا: مفعّلون، غير مجازين، ورخصتهم سارية (HC-20)."""
    return conn.fetch_all(
        """
        SELECT d.id::text AS id, d.code, d.full_name, d.shift_start, d.shift_end
        FROM drivers d
        WHERE d.hub_id = $1::uuid
          AND d.is_active
          AND d.employment_status = 'ACTIVE'
          AND (d.license_expiry IS NULL OR d.license_expiry >= $2::date)
          AND NOT EXISTS (
              SELECT 1 FROM availability_exceptions a
              WHERE a.entity_type = 'DRIVER' AND a.entity_id = d.id
                AND NOT a.is_available
                AND $2::date BETWEEN a.from_date AND a.to_date)
        ORDER BY d.code
        """,
        [hub_id, day],
    )


def available_vehicles(conn: pgwire.Connection, hub_id: str, day: dt.date) -> list[Any]:
    return conn.fetch_all(
        """
        SELECT v.id::text AS id, v.plate_number, v.has_cooling
        FROM vehicles v
        WHERE v.hub_id = $1::uuid AND v.is_active AND v.status IN ('AVAILABLE','IN_USE')
          AND NOT EXISTS (
              SELECT 1 FROM availability_exceptions a
              WHERE a.entity_type = 'VEHICLE' AND a.entity_id = v.id
                AND NOT a.is_available
                AND $2::date BETWEEN a.from_date AND a.to_date)
        ORDER BY v.plate_number
        """,
        [hub_id, day],
    )


def _load_shipments(
    conn: pgwire.Connection, hub_id: str, day: dt.date
) -> list[Any]:
    return conn.fetch_all(
        """
        SELECT s.id::text AS id, s.reference, s.hub_id::text AS hub_id,
               s.service_type, s.request_kind, s.temperature_mode, s.piece_count,
               s.pickup_facility_id::text AS pickup_facility_id,
               s.pickup_facility_type, s.pickup_lat, s.pickup_lon,
               s.pickup_window_from, s.pickup_window_to, s.pickup_service_minutes,
               s.dropoff_facility_id::text AS dropoff_facility_id,
               s.dropoff_facility_type, s.dropoff_lat, s.dropoff_lon,
               s.dropoff_service_minutes, s.sla_deadline, s.status,
               pf.name_ar AS pickup_name, df.name_ar AS dropoff_name
        FROM shipments s
        JOIN facilities pf ON pf.id = s.pickup_facility_id
        JOIN facilities df ON df.id = s.dropoff_facility_id
        WHERE s.hub_id = $1::uuid AND s.service_date = $2::date
          AND s.status = ANY($3::text[])
        ORDER BY s.pickup_window_from, s.reference
        """,
        [hub_id, day, list(PLANNABLE_STATUSES)],
    )


def run_planning(
    context: SecurityContext,
    *,
    hub_ids: list[str],
    dates: list[dt.date],
    plan_name: str | None = None,
    import_id: str | None = None,
    time_limit_seconds: float | None = None,
    routing_provider: str | None = None,
    fallback_to_estimate: bool = True,
    baseline_plan_id: str | None = None,
    seed: int | None = None,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """يشغّل المحرك لكل (مركز، يوم) ويحفظ النتيجة كمسودة خطة دائمة."""
    cfg = get_config()
    if not hub_ids or not dates:
        raise ValidationError("يجب تحديد مركز انطلاق واحد على الأقل ويوم واحد على الأقل")

    reference = f"PLN-{dt.datetime.now(dt.timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
    name = plan_name or f"خطة {min(dates).isoformat()} — {max(dates).isoformat()}"

    with transaction(context) as conn:
        plan_id = conn.fetch_value(
            "INSERT INTO plans (reference, name_ar, status, scope_type, period_start, "
            "period_end, import_id, baseline_plan_id, parameters, created_by, is_test_data) "
            "VALUES ($1,$2,'DRAFT','KINGDOM',$3::date,$4::date,$5::uuid,$6::uuid,"
            "$7::jsonb,$8::uuid,$9) RETURNING id::text",
            [reference, name, min(dates), max(dates), import_id, baseline_plan_id,
             pgwire.Jsonb({
                 "hub_ids": hub_ids,
                 "dates": [d.isoformat() for d in dates],
                 "time_limit_seconds": time_limit_seconds,
                 "routing_provider": routing_provider or cfg.routing.provider,
                 "seed": seed or cfg.optimizer.random_seed,
             }),
             context.user_id, cfg.allow_test_data and context.role == "ADMIN"],
        )
        conn.execute(
            "UPDATE plans SET status = 'OPTIMIZING' WHERE id = $1::uuid", [plan_id])

    totals: dict[str, Any] = {
        "shipment_count": 0, "planned_shipment_count": 0, "route_count": 0,
        "unplannable_count": 0, "warning_count": 0, "total_distance_km": 0.0,
        "total_drive_minutes": 0.0, "total_service_minutes": 0.0,
        "total_wait_minutes": 0.0, "total_working_minutes": 0.0,
        "estimated_cost": 0.0, "drivers_used": 0, "drivers_required": 0,
        "drivers_available": 0, "drivers_theoretical_minimum": 0,
        "day_count": len(dates), "hub_count": len(hub_ids), "solve_ms": 0,
        "long_haul_route_count": 0, "unassigned_route_count": 0,
    }
    routing_estimated = False
    routing_provider_used = ""
    per_day: list[dict[str, Any]] = []
    failure: str | None = None

    tasks = [(hub_id, day) for hub_id in hub_ids for day in dates]

    try:
        for outcome in _run_tasks(
            context, plan_id, tasks,
            time_limit_seconds=time_limit_seconds,
            routing_provider=routing_provider,
            fallback_to_estimate=fallback_to_estimate,
            seed=seed,
        ):
            per_day.append(outcome["summary"])
            metrics = outcome["metrics"]
            for key in (
                "shipment_count", "planned_shipment_count", "route_count",
                "unplannable_count", "warning_count", "drivers_used",
                "drivers_required", "drivers_available",
                "drivers_theoretical_minimum", "long_haul_route_count",
                "unassigned_route_count", "solve_ms",
            ):
                totals[key] += metrics.get(key, 0)
            for key in (
                "total_distance_km", "total_drive_minutes", "total_service_minutes",
                "total_wait_minutes", "total_working_minutes", "estimated_cost",
            ):
                totals[key] += float(metrics.get(key, 0.0))
            routing_estimated = routing_estimated or metrics.get("routing_estimated", False)
            routing_provider_used = metrics.get("routing_provider", routing_provider_used)
    except Exception as exc:
        failure = str(exc)
        with transaction(context) as conn:
            conn.execute(
                "UPDATE plans SET status = 'FAILED', failure_reason = $1 WHERE id = $2::uuid",
                [failure[:2000], plan_id],
            )
        raise

    for key in ("total_distance_km", "total_drive_minutes", "total_service_minutes",
                "total_wait_minutes", "total_working_minutes", "estimated_cost"):
        totals[key] = round(totals[key], 3)

    with transaction(context) as conn:
        settings_snapshot = {
            hub_id: settings_service.effective_for_hub(conn, hub_id) for hub_id in hub_ids
        }
        conn.execute(
            "UPDATE plans SET status = 'OPTIMIZED', metrics = $1::jsonb, "
            "settings_snapshot = $2::jsonb, engine_name = $3, engine_version = $4, "
            "routing_provider = $5, routing_estimated = $6, solve_ms = $7 "
            "WHERE id = $8::uuid",
            [pgwire.Jsonb({**totals, "per_day": per_day}),
             pgwire.Jsonb(settings_snapshot),
             "masar-opt", "1.0.0", routing_provider_used, routing_estimated,
             totals["solve_ms"], plan_id],
        )
        audit.record(
            conn, context, AuditAction.OPTIMIZER_RUN,
            entity_type="plan", entity_id=plan_id, entity_label=reference,
            new_value={
                "hub_ids": hub_ids, "dates": [d.isoformat() for d in dates],
                "routes": totals["route_count"], "unplannable": totals["unplannable_count"],
                "solve_ms": totals["solve_ms"],
            },
            ip_address=ip_address,
        )
        events.publish(
            conn, events.TOPIC_PLAN,
            {"plan_id": plan_id, "reference": reference, "status": "OPTIMIZED"},
        )

    return {
        "plan_id": plan_id,
        "reference": reference,
        "status": PlanStatus.OPTIMIZED,
        "metrics": totals,
        "per_day": per_day,
        "routing_estimated": routing_estimated,
    }


# ================================================ التوازي على المراكز ======

def _worker_initializer() -> None:
    """يُشغَّل مرة في كل عملية عاملة قبل أول مهمة.

    تجميعة الاتصالات كائن على مستوى الوحدة؛ لو ورثتها العملية الابنة عن أمها
    لتشاركت سوكِت قاعدة بيانات واحدة بين عمليتين — وهو فساد صامت في البروتوكول.
    ``reset=True`` يجبر كل عامل على فتح تجميعته الخاصة.
    """
    from masar_db.driver import get_pool

    get_pool(reset=True)


def _plan_hub_day_task(payload: dict[str, Any]) -> dict[str, Any]:
    """نقطة دخول العملية العاملة — يجب أن تكون على مستوى الوحدة كي تُنتقى."""
    return _plan_hub_day(
        payload["context"], payload["plan_id"], payload["hub_id"], payload["day"],
        time_limit_seconds=payload["time_limit_seconds"],
        routing_provider=payload["routing_provider"],
        fallback_to_estimate=payload["fallback_to_estimate"],
        seed=payload["seed"],
    )


def _run_tasks(
    context: SecurityContext,
    plan_id: str,
    tasks: list[tuple[str, dt.date]],
    *,
    time_limit_seconds: float | None,
    routing_provider: str | None,
    fallback_to_estimate: bool,
    seed: int | None,
) -> list[dict[str, Any]]:
    """يحلّ مسائل (مركز × يوم) — متوازية إن سمح الإعداد، وإلا بالتتابع.

    كل مسألة **مستقلة تمامًا**: شحناتها وسائقوها وإعداداتها تخص مركزًا ويومًا
    واحدًا، ولا يوجد قيد يربط رحلة في مركز برحلة في مركز آخر. لذلك التفكيك
    صحيح رياضيًا لا تقريبًا: الناتج المجمّع مطابق للتشغيل المتتابع.

    التوازي بالعمليات لا بالخيوط: المحرك عمل حسابي خالص في بايثون، وقفل
    المفسّر العام (GIL) يجعل الخيوط بلا فائدة هنا. تُستخدم طريقة ``spawn`` لا
    ``fork`` كي لا ترث العملية الابنة سوكِتات قاعدة البيانات المفتوحة.

    الحفظ يتم داخل كل عامل في معاملته الخاصة — كما في التشغيل المتتابع
    تمامًا — فلا يتغيّر أي حدّ معاملات، ولا تُنقل مصفوفات السفر الضخمة بين
    العمليات.
    """
    payloads = [
        {
            "context": context, "plan_id": plan_id, "hub_id": hub_id, "day": day,
            "time_limit_seconds": time_limit_seconds,
            "routing_provider": routing_provider,
            "fallback_to_estimate": fallback_to_estimate,
            "seed": seed,
        }
        for hub_id, day in tasks
    ]

    workers = _worker_count(len(payloads))
    if workers <= 1:
        return [_plan_hub_day_task(payload) for payload in payloads]

    import concurrent.futures
    import multiprocessing

    context_mp = multiprocessing.get_context("spawn")
    results: list[dict[str, Any] | None] = [None] * len(payloads)
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=workers, mp_context=context_mp, initializer=_worker_initializer,
    ) as pool:
        futures = {
            pool.submit(_plan_hub_day_task, payload): index
            for index, payload in enumerate(payloads)
        }
        for future in concurrent.futures.as_completed(futures):
            # الاستثناء يُعاد رفعه هنا فيصل إلى معالج الفشل في ``run_planning``
            # وتُوسم الخطة FAILED بسببها — لا فشل صامت في عملية بعيدة.
            results[futures[future]] = future.result()

    return [result for result in results if result is not None]


def _worker_count(task_count: int) -> int:
    """عدد العمليات الفعلي: إعداد المستخدم مقيّدًا بعدد المهام وأنوية الجهاز."""
    import os

    configured = int(get_config().optimizer.workers or 1)
    if configured <= 1 or task_count <= 1:
        return 1
    available = os.cpu_count() or 1
    return max(1, min(configured, task_count, available))


def _plan_hub_day(
    context: SecurityContext,
    plan_id: str,
    hub_id: str,
    day: dt.date,
    *,
    time_limit_seconds: float | None,
    routing_provider: str | None,
    fallback_to_estimate: bool,
    seed: int | None,
) -> dict[str, Any]:
    cfg = get_config()
    with session(context) as conn:
        hub = _load_hub(conn, hub_id)
        effective = settings_service.effective_for_hub(conn, hub_id)
        shipment_rows = _load_shipments(conn, hub_id, day)
        drivers = available_drivers(conn, hub_id, day)
        vehicles_available = available_vehicles(conn, hub_id, day)

    timezone_name = cfg.timezone
    opens_at, closes_at = _hub_working_window(hub, day, timezone_name)

    hub_input = HubInput(
        hub_id=hub["id"], code=hub["code"], name_ar=hub["name_ar"],
        lat=hub["lat"], lon=hub["lon"], opens_at=opens_at, closes_at=closes_at,
    )
    shipment_inputs = [
        ShipmentInput(
            shipment_id=row["id"], reference=row["reference"], hub_id=row["hub_id"],
            pickup_facility_id=row["pickup_facility_id"],
            pickup_facility_type=row["pickup_facility_type"],
            pickup_name=row["pickup_name"],
            pickup_lat=row["pickup_lat"], pickup_lon=row["pickup_lon"],
            pickup_window_from=row["pickup_window_from"],
            pickup_window_to=row["pickup_window_to"],
            pickup_service_minutes=float(row["pickup_service_minutes"]),
            dropoff_facility_id=row["dropoff_facility_id"],
            dropoff_facility_type=row["dropoff_facility_type"],
            dropoff_name=row["dropoff_name"],
            dropoff_lat=row["dropoff_lat"], dropoff_lon=row["dropoff_lon"],
            dropoff_service_minutes=float(row["dropoff_service_minutes"]),
            sla_deadline=row["sla_deadline"],
            piece_count=int(row["piece_count"]),
            service_type=row["service_type"],
            temperature_mode=row["temperature_mode"],
            is_on_demand=row["request_kind"] == "ON_DEMAND",
        )
        for row in shipment_rows
    ]

    max_shift_minutes = float(effective["max_shift_hours"]) * 60.0
    slot_count = max(len(drivers), 1)
    # خانات إضافية لكشف العجز: إن احتاج المحرك أكثر من المتاح ظهر ذلك رقمًا
    slot_count = max(slot_count, min(len(shipment_inputs), slot_count * 2))
    vehicle_inputs = [
        VehicleInput(
            hub_id=hub["id"], label=f"خانة {index + 1}",
            earliest_start=opens_at, latest_end=closes_at,
            max_shift_minutes=max_shift_minutes,
        )
        for index in range(slot_count)
    ]

    if not shipment_inputs:
        return {
            "summary": {
                "hub_id": hub_id, "hub_code": hub["code"],
                "service_date": day.isoformat(), "shipment_count": 0,
                "route_count": 0, "unplannable_count": 0,
            },
            "metrics": {"drivers_available": len(drivers)},
        }

    problem = build_problem(
        service_date=day, hubs=[hub_input], shipments=shipment_inputs,
        vehicles=vehicle_inputs, effective_settings=effective,
        routing_provider_name=routing_provider,
        fallback_to_estimate=fallback_to_estimate,
        timezone_name=timezone_name,
    )
    options = SolveOptions(
        time_limit_seconds=float(
            time_limit_seconds or effective.get("solve_time_limit_seconds")
            or cfg.optimizer.time_limit_seconds),
        seed=int(seed or cfg.optimizer.random_seed),
    )
    result = run_engine(
        problem, options=options,
        available_drivers_by_hub={hub_id: len(drivers)},
    )

    persist_plan_day(context, plan_id, hub, day, result, len(drivers))

    summary = {
        "hub_id": hub_id,
        "hub_code": hub["code"],
        "hub_name_ar": hub["name_ar"],
        "service_date": day.isoformat(),
        "shipment_count": result.metrics["shipment_count"],
        "route_count": result.metrics["route_count"],
        "unplannable_count": result.metrics["unplannable_count"],
        "drivers_used": result.metrics["drivers_used"],
        "drivers_available": len(drivers),
        "vehicles_available": len(vehicles_available),
        "total_distance_km": result.metrics["total_distance_km"],
        "improvement": result.metrics.get("improvement"),
    }
    metrics = dict(result.metrics)
    metrics["drivers_available"] = len(drivers)
    return {"summary": summary, "metrics": metrics}


def persist_plan_day(
    context: SecurityContext,
    plan_id: str,
    hub: Any,
    day: dt.date,
    result: PlanResult,
    drivers_available: int,
) -> None:
    """يحفظ نتيجة يوم واحد: رحلات ومحطات وتحذيرات وتقدير سائقين."""
    problem = result.problem
    with transaction(context) as conn:
        # مسودة دائمة: تُستبدل نتيجة اليوم نفسه إن أُعيد التشغيل
        conn.execute(
            "DELETE FROM plan_warnings WHERE plan_id = $1::uuid AND hub_id = $2::uuid",
            [plan_id, hub["id"]],
        )
        old_day = conn.fetch_one(
            "SELECT id::text AS id, is_published FROM plan_days "
            "WHERE plan_id = $1::uuid AND hub_id = $2::uuid AND service_date = $3::date",
            [plan_id, hub["id"], day],
        )
        if old_day and old_day["is_published"]:
            raise Conflict("لا يمكن إعادة تشغيل المحرك على يوم منشور — اسحب النشر أولًا")
        if old_day:
            conn.execute(
                "UPDATE routes SET status = 'CANCELLED', cancel_reason = 'إعادة تشغيل المحرك' "
                "WHERE plan_day_id = $1::uuid AND status IN ('DRAFT','PLANNED')",
                [old_day["id"]],
            )
            conn.execute("DELETE FROM plan_days WHERE id = $1::uuid", [old_day["id"]])

        plan_day_id = conn.fetch_value(
            "INSERT INTO plan_days (plan_id, hub_id, service_date, metrics) "
            "VALUES ($1::uuid,$2::uuid,$3::date,$4::jsonb) RETURNING id::text",
            [plan_id, hub["id"], day, pgwire.Jsonb(result.metrics)],
        )
        # لاحقة الخطة تجعل مرجع الرحلة فريدًا عبر عمليات التخطيط المتعددة
        # لنفس المركز واليوم (إعادة تشغيل المحرك، أو خطة بديلة للمقارنة).
        plan_suffix = str(conn.fetch_value(
            "SELECT reference FROM plans WHERE id = $1::uuid", [plan_id]) or "")[-6:]

        route_ids: dict[int, str] = {}
        planned_shipments: set[str] = set()

        for sequence, route in enumerate(result.solution.used_routes(), start=1):
            vehicle = problem.vehicles[route.vehicle_index]
            evaluation = route.evaluation
            route_reference = (
                f"RT-{day:%Y%m%d}-{hub['code']}-{plan_suffix}-{sequence:03d}")
            last_timing = evaluation.timings[-1] if evaluation.timings else None
            end_node = problem.nodes[last_timing.node_index] if last_timing else None

            route_id = conn.fetch_value(
                """
                INSERT INTO routes (
                    reference, plan_id, plan_day_id, hub_id, region_id, service_date,
                    status, sequence_in_day, start_lat, start_lon, start_node_kind,
                    planned_start_at, planned_end_at, end_lat, end_lon,
                    distance_km, drive_minutes, service_minutes, wait_minutes,
                    working_minutes, estimated_cost, shipment_count, pickup_count,
                    delivery_count, is_long_haul, max_hub_distance_km,
                    facility_classes, mixing_exemption_used, is_test_data
                ) VALUES (
                    $1,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$6::date,'PLANNED',$7,
                    $8,$9,'HUB',$10::timestamptz,$11::timestamptz,$12,$13,
                    $14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25::text[],$26,$27
                ) RETURNING id::text
                """,
                [
                    route_reference, plan_id, plan_day_id, hub["id"], hub["region_id"], day,
                    sequence,
                    problem.nodes[vehicle.start_node].lat,
                    problem.nodes[vehicle.start_node].lon,
                    to_datetime(evaluation.start_at), to_datetime(evaluation.end_at),
                    end_node.lat if end_node else None,
                    end_node.lon if end_node else None,
                    round(evaluation.distance_km, 3), round(evaluation.drive_minutes, 2),
                    round(evaluation.service_minutes, 2), round(evaluation.wait_minutes, 2),
                    round(evaluation.working_minutes, 2),
                    round(
                        evaluation.distance_km * problem.settings.cost_per_km
                        + problem.settings.cost_per_driver_day, 2),
                    len({problem.nodes[i].shipment_index for i in route.sequence}),
                    sum(1 for i in route.sequence
                        if problem.nodes[i].kind is NodeKind.PICKUP),
                    sum(1 for i in route.sequence
                        if problem.nodes[i].kind is NodeKind.DELIVERY),
                    evaluation.is_long_haul, round(evaluation.max_hub_distance_km, 3),
                    sorted(evaluation.mixing_classes), evaluation.mixing_exemption_used,
                    False,
                ],
            )
            route_ids[route.vehicle_index] = route_id

            # محطة البداية من مركز الانطلاق
            conn.execute(
                "INSERT INTO route_stops (route_id, sequence, kind, hub_id, lat, lon, "
                "label_ar, planned_departure_at) "
                "VALUES ($1::uuid,0,'HUB_START',$2::uuid,$3,$4,$5,$6::timestamptz)",
                [route_id, hub["id"], problem.nodes[vehicle.start_node].lat,
                 problem.nodes[vehicle.start_node].lon,
                 f"مركز الانطلاق: {hub['name_ar']}",
                 to_datetime(evaluation.start_at)],
            )

            for position, timing in enumerate(evaluation.timings, start=1):
                node = problem.nodes[timing.node_index]
                shipment = problem.shipment_of_node(timing.node_index)
                conn.execute(
                    """
                    INSERT INTO route_stops (
                        route_id, sequence, kind, facility_id, shipment_id, lat, lon,
                        label_ar, planned_arrival_at, planned_service_start,
                        planned_departure_at, window_from, window_to, service_minutes,
                        wait_minutes, leg_distance_km, leg_minutes, leg_is_estimated
                    ) VALUES (
                        $1::uuid,$2,$3,$4::uuid,$5::uuid,$6,$7,$8,
                        $9::timestamptz,$10::timestamptz,$11::timestamptz,
                        $12::timestamptz,$13::timestamptz,$14,$15,$16,$17,$18
                    )
                    """,
                    [
                        route_id, position,
                        "PICKUP" if node.kind is NodeKind.PICKUP else "DELIVERY",
                        node.facility_id,
                        shipment.shipment_id if shipment else None,
                        node.lat, node.lon, node.label,
                        to_datetime(timing.arrival), to_datetime(timing.service_start),
                        to_datetime(timing.departure),
                        to_datetime(node.window_from) if node.window_from else None,
                        to_datetime(node.window_to) if node.window_to else None,
                        round(node.service_minutes, 2), round(timing.wait_minutes, 2),
                        round(timing.leg_km, 3), round(timing.leg_minutes, 2),
                        problem.travel.is_estimated,
                    ],
                )

                if shipment and node.kind is NodeKind.PICKUP:
                    # تفريغ سبب تعذر التخطيط يجري **مع تغيير الحالة** لا قبله:
                    # قيد ``shipments_unplannable_reason`` يشترط وجود سبب ما
                    # دامت الحالة UNPLANNABLE، فإفراغه هنا — والحالة لم تتغير
                    # بعد — كان يخرق القيد ويُسقط إعادة التشغيل كلها حين تصبح
                    # شحنة كانت متعذرة قابلة للتخطيط.
                    conn.execute(
                        "UPDATE shipments SET route_id = $1::uuid, "
                        "planned_pickup_arrival = $2::timestamptz, "
                        "planned_pickup_at = $3::timestamptz "
                        "WHERE id = $4::uuid",
                        [route_id, to_datetime(timing.arrival),
                         to_datetime(timing.service_end), shipment.shipment_id],
                    )
                    planned_shipments.add(shipment.shipment_id)
                elif shipment and node.kind is NodeKind.DELIVERY:
                    conn.execute(
                        "UPDATE shipments SET planned_dropoff_arrival = $1::timestamptz, "
                        "planned_dropoff_at = $2::timestamptz WHERE id = $3::uuid",
                        [to_datetime(timing.arrival), to_datetime(timing.service_end),
                         shipment.shipment_id],
                    )

        # تحديث حالات الشحنات المخططة
        for shipment_id in planned_shipments:
            conn.execute(
                "UPDATE shipments SET status = 'PLANNED', "
                "unplannable_reason = NULL, unplannable_detail = NULL "
                "WHERE id = $1::uuid "
                "AND status IN ('VALIDATED','PENDING_ASSIGNMENT','UNPLANNABLE','PLANNED')",
                [shipment_id],
            )

        # الشحنات غير القابلة للتخطيط — بسبب مسجَّل إلزاميًا (HC-19)
        for item in result.unplannable:
            conn.execute(
                "UPDATE shipments SET status = 'UNPLANNABLE', unplannable_reason = $1, "
                "unplannable_detail = $2, route_id = NULL WHERE id = $3::uuid "
                "AND status IN ('VALIDATED','PENDING_ASSIGNMENT','PLANNED','UNPLANNABLE')",
                [item["reason"], item["message_ar"], item["shipment_id"]],
            )

        # التحذيرات
        for warning in result.warnings:
            route_id = route_ids.get(warning.route_index) if warning.route_index is not None else None
            conn.execute(
                "INSERT INTO plan_warnings (plan_id, route_id, shipment_id, hub_id, "
                "warning_type, severity, reason_ar, affected_entity_ar, "
                "suggested_action_ar, context) "
                "VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,$6,$7,$8,$9,$10::jsonb)",
                [plan_id, route_id, warning.shipment_id, hub["id"],
                 warning.warning_type, warning.severity, warning.reason_ar,
                 warning.affected_entity_ar, warning.suggested_action_ar,
                 pgwire.Jsonb(warning.context)],
            )

        # تقدير السائقين
        for estimate in result.estimations:
            conn.execute(
                "INSERT INTO driver_estimations (plan_id, hub_id, service_date, "
                "theoretical_minimum, recommended, available, used, gap, "
                "workload_minutes, justification, sla_impact) "
                "VALUES ($1::uuid,$2::uuid,$3::date,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb) "
                "ON CONFLICT (plan_id, hub_id, service_date) DO UPDATE SET "
                "theoretical_minimum = EXCLUDED.theoretical_minimum, "
                "recommended = EXCLUDED.recommended, available = EXCLUDED.available, "
                "used = EXCLUDED.used, gap = EXCLUDED.gap, "
                "workload_minutes = EXCLUDED.workload_minutes, "
                "justification = EXCLUDED.justification, sla_impact = EXCLUDED.sla_impact",
                [plan_id, hub["id"], day, estimate.theoretical_minimum,
                 estimate.recommended, drivers_available, estimate.used,
                 drivers_available - estimate.recommended, estimate.workload_minutes,
                 pgwire.Jsonb(estimate.justification), pgwire.Jsonb(estimate.sla_impact)],
            )


# ---------------------------------------------------------- الاستعلامات ----

def get_plan(context: SecurityContext, plan_id: str) -> dict[str, Any]:
    with session(context) as conn:
        plan = conn.fetch_one(
            "SELECT p.id::text AS id, p.reference, p.name_ar, p.status, p.period_start, "
            "p.period_end, p.metrics, p.settings_snapshot, p.engine_name, "
            "p.engine_version, p.routing_provider, p.routing_estimated, p.solve_ms, "
            "p.baseline_plan_id::text AS baseline_plan_id, p.created_at, p.approved_at, "
            "p.dispatched_at, p.failure_reason, u.full_name AS created_by_name, "
            "a.full_name AS approved_by_name "
            "FROM plans p LEFT JOIN users u ON u.id = p.created_by "
            "LEFT JOIN users a ON a.id = p.approved_by WHERE p.id = $1::uuid",
            [plan_id],
        )
        if plan is None:
            raise NotFound("الخطة غير موجودة")

        days = conn.fetch_all(
            "SELECT d.id::text AS id, d.hub_id::text AS hub_id, h.code AS hub_code, "
            "h.name_ar AS hub_name_ar, d.service_date, d.is_published, d.published_at, "
            "d.metrics, u.full_name AS published_by_name "
            "FROM plan_days d JOIN hubs h ON h.id = d.hub_id "
            "LEFT JOIN users u ON u.id = d.published_by "
            "WHERE d.plan_id = $1::uuid ORDER BY d.service_date, h.code",
            [plan_id],
        )
        routes = conn.fetch_all(
            """
            SELECT r.id::text AS id, r.reference, r.plan_day_id::text AS plan_day_id,
                   r.hub_id::text AS hub_id, h.code AS hub_code, h.name_ar AS hub_name_ar,
                   r.service_date, r.status, r.sequence_in_day,
                   r.driver_id::text AS driver_id, d.full_name AS driver_name,
                   r.vehicle_id::text AS vehicle_id, v.plate_number,
                   r.box_id::text AS box_id, b.code AS box_code,
                   r.planned_start_at, r.planned_end_at, r.actual_start_at, r.actual_end_at,
                   r.distance_km, r.drive_minutes, r.service_minutes, r.wait_minutes,
                   r.working_minutes, r.estimated_cost, r.shipment_count, r.pickup_count,
                   r.delivery_count, r.is_long_haul, r.max_hub_distance_km,
                   r.end_lat, r.end_lon, r.start_lat, r.start_lon,
                   r.mixing_exemption_used, r.published_at, r.assigned_at
            FROM routes r
            JOIN hubs h ON h.id = r.hub_id
            LEFT JOIN drivers d ON d.id = r.driver_id
            LEFT JOIN vehicles v ON v.id = r.vehicle_id
            LEFT JOIN boxes b ON b.id = r.box_id
            WHERE r.plan_id = $1::uuid AND r.status <> 'CANCELLED'
            ORDER BY r.service_date, h.code, r.sequence_in_day
            """,
            [plan_id],
        )
        warnings = conn.fetch_all(
            "SELECT w.id::text AS id, w.route_id::text AS route_id, "
            "w.shipment_id::text AS shipment_id, w.hub_id::text AS hub_id, "
            "w.warning_type, w.severity, w.reason_ar, w.affected_entity_ar, "
            "w.suggested_action_ar, w.occurred_at, w.context, "
            "r.reference AS route_reference, s.reference AS shipment_reference "
            "FROM plan_warnings w "
            "LEFT JOIN routes r ON r.id = w.route_id "
            "LEFT JOIN shipments s ON s.id = w.shipment_id "
            "WHERE w.plan_id = $1::uuid "
            "ORDER BY CASE w.severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 "
            "WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 3 ELSE 4 END, w.occurred_at",
            [plan_id],
        )
        estimations = conn.fetch_all(
            "SELECT e.hub_id::text AS hub_id, h.code AS hub_code, h.name_ar AS hub_name_ar, "
            "e.service_date, e.theoretical_minimum, e.recommended, e.available, e.used, "
            "e.gap, e.workload_minutes, e.justification, e.sla_impact "
            "FROM driver_estimations e JOIN hubs h ON h.id = e.hub_id "
            "WHERE e.plan_id = $1::uuid ORDER BY e.service_date, h.code",
            [plan_id],
        )
        unplannable = conn.fetch_all(
            "SELECT s.id::text AS id, s.reference, s.service_date, s.status, "
            "s.unplannable_reason, s.unplannable_detail, s.hub_id::text AS hub_id, "
            "pf.name_ar AS pickup_name, df.name_ar AS dropoff_name, "
            "s.pickup_window_from, s.pickup_window_to, s.sla_deadline "
            "FROM shipments s "
            "JOIN facilities pf ON pf.id = s.pickup_facility_id "
            "JOIN facilities df ON df.id = s.dropoff_facility_id "
            "WHERE s.status = 'UNPLANNABLE' AND s.service_date BETWEEN $1::date AND $2::date "
            "ORDER BY s.service_date, s.reference",
            [plan["period_start"], plan["period_end"]],
        )

    return {
        "plan": dict(plan),
        "days": [dict(row) for row in days],
        "routes": [dict(row) for row in routes],
        "warnings": [dict(row) for row in warnings],
        "estimations": [dict(row) for row in estimations],
        "unplannable": [dict(row) for row in unplannable],
    }


def get_route_detail(context: SecurityContext, route_id: str) -> dict[str, Any]:
    with session(context) as conn:
        route = conn.fetch_one(
            "SELECT r.*, r.id::text AS id, r.hub_id::text AS hub_id, "
            "r.driver_id::text AS driver_id, r.vehicle_id::text AS vehicle_id, "
            "r.box_id::text AS box_id, r.plan_id::text AS plan_id, "
            "h.name_ar AS hub_name_ar, h.code AS hub_code, h.lat AS hub_lat, h.lon AS hub_lon, "
            "d.full_name AS driver_name, d.phone AS driver_phone, v.plate_number, b.code AS box_code "
            "FROM routes r JOIN hubs h ON h.id = r.hub_id "
            "LEFT JOIN drivers d ON d.id = r.driver_id "
            "LEFT JOIN vehicles v ON v.id = r.vehicle_id "
            "LEFT JOIN boxes b ON b.id = r.box_id WHERE r.id = $1::uuid",
            [route_id],
        )
        if route is None:
            raise NotFound("الرحلة غير موجودة")

        stops = conn.fetch_all(
            "SELECT st.id::text AS id, st.sequence, st.kind, st.label_ar, st.lat, st.lon, "
            "st.facility_id::text AS facility_id, st.shipment_id::text AS shipment_id, "
            "st.planned_arrival_at, st.planned_service_start, st.planned_departure_at, "
            "st.window_from, st.window_to, st.service_minutes, st.wait_minutes, "
            "st.leg_distance_km, st.leg_minutes, st.leg_is_estimated, st.status, "
            "st.actual_arrival_at, st.actual_completed_at, "
            "f.name_ar AS facility_name, f.facility_type, f.address, "
            "f.contact_name, f.contact_phone, "
            "s.reference AS shipment_reference, s.status AS shipment_status, "
            "s.sla_deadline, s.piece_count, s.temperature_mode "
            "FROM route_stops st "
            "LEFT JOIN facilities f ON f.id = st.facility_id "
            "LEFT JOIN shipments s ON s.id = st.shipment_id "
            "WHERE st.route_id = $1::uuid ORDER BY st.sequence",
            [route_id],
        )
        shipments = conn.fetch_all(
            "SELECT s.id::text AS id, s.reference, s.status, s.piece_count, "
            "s.temperature_mode, s.sla_deadline, s.planned_pickup_at, s.planned_dropoff_at, "
            "s.actual_pickup_at, s.actual_dropoff_at, s.sla_breached, "
            "pf.name_ar AS pickup_name, df.name_ar AS dropoff_name "
            "FROM shipments s "
            "JOIN facilities pf ON pf.id = s.pickup_facility_id "
            "JOIN facilities df ON df.id = s.dropoff_facility_id "
            "WHERE s.route_id = $1::uuid ORDER BY s.reference",
            [route_id],
        )
        warnings = conn.fetch_all(
            "SELECT warning_type, severity, reason_ar, affected_entity_ar, "
            "suggested_action_ar, context FROM plan_warnings WHERE route_id = $1::uuid",
            [route_id],
        )
        violations = conn.fetch_all(
            "SELECT rule_code, detail_ar FROM app.verify_route_feasibility($1::uuid)",
            [route_id],
        )

    return {
        "route": dict(route),
        "stops": [dict(row) for row in stops],
        "shipments": [dict(row) for row in shipments],
        "warnings": [dict(row) for row in warnings],
        "feasibility_violations": [dict(row) for row in violations],
    }


def approve_plan(
    context: SecurityContext, plan_id: str, *,
    acknowledge_estimated: bool = False,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """يعتمد خطة — مع بوابة صريحة للخطط المبنية على أزمنة تقديرية (§23).

    خطة مبنية على Haversine ليست «أقل دقة قليلًا»: القياس على حالة الحدود
    الشمالية أظهر انحرافًا +28٪ في المسافة و+64٪ في الزمن، في اتجاهين
    متعاكسين (تضخيم بين المدن، تقليل داخل البلدة). اعتمادها للتشغيل الفعلي
    يعني الالتزام بـSLA على أزمنة غير حقيقية.

    لذلك:

    * في **الإنتاج**: الاعتماد مرفوض قطعًا. الحل تفعيل مزوّد طرق حقيقي،
      لا تجاوز البوابة.
    * خارج الإنتاج: مسموح بشرط إقرار صريح (``acknowledge_estimated``) —
      كي لا يمر الاعتماد بالنقر المعتاد، ويُسجَّل الإقرار في سجل التدقيق.
    """
    from masar_core.config import get_config

    with transaction(context) as conn:
        plan = conn.fetch_one(
            "SELECT id::text AS id, reference, status, routing_estimated "
            "FROM plans WHERE id = $1::uuid",
            [plan_id],
        )
        if plan is None:
            raise NotFound("الخطة غير موجودة")
        if plan["status"] != PlanStatus.OPTIMIZED:
            raise Conflict(f"لا يمكن اعتماد خطة حالتها {plan['status']}")

        if plan["routing_estimated"]:
            if get_config().is_production:
                raise Conflict(
                    "لا يمكن اعتماد خطة مبنية على أزمنة قيادة تقديرية للتشغيل "
                    "الفعلي. المزوّد الحالي تقديري (خط مستقيم × معامل التفافية)، "
                    "والانحراف المقيس عن الطرق الحقيقية يبلغ +28٪ مسافةً و+64٪ "
                    "زمنًا. فعّل مزوّد طرق حقيقيًا (MASAR_ROUTING_PROVIDER=osrm) "
                    "ثم أعد تشغيل المحرك.")
            if not acknowledge_estimated:
                raise Conflict(
                    "هذه الخطة مبنية على أزمنة تقديرية لا على شبكة طرق حقيقية. "
                    "الاعتماد يتطلب إقرارًا صريحًا (acknowledge_estimated) "
                    "يُسجَّل في سجل التدقيق باسم من أقرّه.")

        blocking = conn.fetch_value(
            "SELECT count(*) FROM plan_warnings "
            "WHERE plan_id = $1::uuid AND severity = 'CRITICAL' "
            "AND warning_type = 'UNPLANNABLE_SHIPMENT'",
            [plan_id],
        )
        conn.execute(
            "UPDATE plans SET status = 'APPROVED', approved_by = $1::uuid, "
            "approved_at = now() WHERE id = $2::uuid",
            [context.user_id, plan_id],
        )
        audit.record(
            conn, context, AuditAction.PLAN_APPROVE,
            entity_type="plan", entity_id=plan_id, entity_label=plan["reference"],
            new_value={"unplannable_warnings": int(blocking or 0),
                       "routing_estimated": plan["routing_estimated"]},
            ip_address=ip_address,
        )
        events.publish(conn, events.TOPIC_PLAN,
                       {"plan_id": plan_id, "status": "APPROVED"})
    return {"plan_id": plan_id, "status": PlanStatus.APPROVED,
            "unplannable_warnings": int(blocking or 0)}


def dispatch_plan(
    context: SecurityContext, plan_id: str, *, ip_address: str | None = None
) -> dict[str, Any]:
    """يرسل الخطة لمراكز الانطلاق — تصبح مرئية للمشرفين للإسناد."""
    with transaction(context) as conn:
        plan = conn.fetch_one(
            "SELECT id::text AS id, reference, status FROM plans WHERE id = $1::uuid",
            [plan_id],
        )
        if plan is None:
            raise NotFound("الخطة غير موجودة")
        if plan["status"] != PlanStatus.APPROVED:
            raise Conflict("يجب اعتماد الخطة قبل إرسالها لمراكز الانطلاق")

        hubs = conn.fetch_all(
            "SELECT DISTINCT hub_id::text AS hub_id FROM plan_days WHERE plan_id = $1::uuid",
            [plan_id],
        )
        conn.execute(
            "UPDATE plans SET status = 'DISPATCHED', dispatched_at = now() "
            "WHERE id = $1::uuid",
            [plan_id],
        )
        for hub in hubs:
            events.publish(
                conn, events.TOPIC_PLAN,
                {"plan_id": plan_id, "reference": plan["reference"], "status": "DISPATCHED"},
                hub_id=hub["hub_id"],
            )
        audit.record(
            conn, context, AuditAction.PLAN_DISPATCH,
            entity_type="plan", entity_id=plan_id, entity_label=plan["reference"],
            new_value={"hub_count": len(hubs)}, ip_address=ip_address,
        )
    return {"plan_id": plan_id, "status": PlanStatus.DISPATCHED, "hub_count": len(hubs)}


def list_plans(context: SecurityContext, limit: int = 50) -> list[Any]:
    with session(context) as conn:
        return conn.fetch_all(
            "SELECT p.id::text AS id, p.reference, p.name_ar, p.status, p.period_start, "
            "p.period_end, p.metrics, p.routing_estimated, p.solve_ms, p.created_at, "
            "p.approved_at, p.dispatched_at, u.full_name AS created_by_name "
            "FROM plans p LEFT JOIN users u ON u.id = p.created_by "
            f"ORDER BY p.created_at DESC LIMIT {int(limit)}"
        )


def compare_plans(context: SecurityContext, plan_a: str, plan_b: str) -> dict[str, Any]:
    """مقارنة خطتين على نفس المقاييس (§25 «مقارنة الخطط»)."""
    with session(context) as conn:
        rows = conn.fetch_all(
            "SELECT id::text AS id, reference, name_ar, metrics, period_start, period_end "
            "FROM plans WHERE id = ANY($1::uuid[])",
            [[plan_a, plan_b]],
        )
    plans = {row["id"]: row for row in rows}
    if plan_a not in plans or plan_b not in plans:
        raise NotFound("إحدى الخطتين غير موجودة")

    keys = [
        ("route_count", "عدد الرحلات"),
        ("drivers_used", "السائقون المستخدمون"),
        ("planned_shipment_count", "الشحنات المخططة"),
        ("unplannable_count", "غير القابلة للتخطيط"),
        ("total_distance_km", "المسافة (كم)"),
        ("total_drive_minutes", "زمن القيادة (دقيقة)"),
        ("total_wait_minutes", "الانتظار (دقيقة)"),
        ("estimated_cost", "التكلفة التقديرية"),
    ]
    comparison = []
    for key, label in keys:
        left = float(plans[plan_a]["metrics"].get(key) or 0)
        right = float(plans[plan_b]["metrics"].get(key) or 0)
        comparison.append({
            "key": key, "label_ar": label,
            "plan_a": left, "plan_b": right,
            "delta": round(right - left, 3),
            "delta_pct": round((right - left) / left * 100, 2) if left else None,
        })
    return {
        "plan_a": dict(plans[plan_a]),
        "plan_b": dict(plans[plan_b]),
        "comparison": comparison,
    }
