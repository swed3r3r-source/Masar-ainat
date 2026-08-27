"""التنبيهات (§24): إنشاء، تصنيف، ربط بالكيان المتأثر، وتسجيل الإجراء."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import pgwire
from masar_core.constants import ALERT_DEFAULT_SEVERITY, AlertType, AuditAction, Severity
from masar_core.errors import Conflict, NotFound
from masar_db.driver import SecurityContext, session, transaction

from . import audit, events

logger = logging.getLogger("masar.alerts")


def raise_alert(
    conn: pgwire.Connection,
    alert_type: str,
    *,
    title_ar: str,
    body_ar: str,
    severity: str | None = None,
    shipment_id: str | None = None,
    route_id: str | None = None,
    hub_id: str | None = None,
    region_id: str | None = None,
    driver_id: str | None = None,
    responsible_user_id: str | None = None,
    dedupe_key: str | None = None,
    context_data: dict[str, Any] | None = None,
    is_test_data: bool = False,
) -> str | None:
    """ينشئ تنبيهًا داخل معاملة قائمة. يعيد ``None`` إن كان مكررًا مفتوحًا."""
    if not any((shipment_id, route_id, hub_id)):
        raise ValueError("التنبيه يجب أن يرتبط بشحنة أو رحلة أو مركز انطلاق")

    resolved_severity = severity or ALERT_DEFAULT_SEVERITY.get(alert_type, Severity.MEDIUM)
    key = dedupe_key or f"{alert_type}:{shipment_id or ''}:{route_id or ''}:{hub_id or ''}"

    # التكرار يُمنع بـ ON CONFLICT لا باستثناء، لأن أي خطأ داخل معاملة
    # PostgreSQL يُجهضها بالكامل ولا يمكن متابعة العمل بعده.
    alert_id = conn.fetch_value(
        """
        INSERT INTO alerts (
            alert_type, severity, title_ar, body_ar, shipment_id, route_id,
            hub_id, region_id, driver_id, responsible_user_id, context,
            dedupe_key, is_test_data
        ) VALUES ($1,$2,$3,$4,$5::uuid,$6::uuid,$7::uuid,$8::uuid,$9::uuid,
                  $10::uuid,$11::jsonb,$12,$13)
        ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL AND resolved_at IS NULL
        DO NOTHING
        RETURNING id::text
        """,
        [alert_type, resolved_severity, title_ar, body_ar, shipment_id, route_id,
         hub_id, region_id, driver_id, responsible_user_id,
         pgwire.Jsonb(context_data or {}), key, is_test_data],
    )
    if alert_id is None:
        return None

    events.publish(
        conn, events.TOPIC_ALERT,
        {"alert_id": alert_id, "alert_type": alert_type, "severity": resolved_severity,
         "title_ar": title_ar, "body_ar": body_ar,
         "shipment_id": shipment_id, "route_id": route_id},
        hub_id=hub_id, region_id=region_id, driver_id=driver_id,
        user_id=responsible_user_id,
    )

    # الإشعار الخارجي يُكتب في **نفس المعاملة**: إن تراجع التنبيه لم تُرسل
    # رسالة عن حدث لم يقع، وإن ثبت وصل الإشعار حتمًا. التنبيه داخل النظام
    # وحده لا يكفي لحدث حرج يقع بعد انتهاء الدوام.
    if resolved_severity in (Severity.HIGH, Severity.CRITICAL):
        _notify_alert(
            conn,
            {"id": alert_id, "alert_type": alert_type, "severity": resolved_severity,
             "title_ar": title_ar, "body_ar": body_ar, "shipment_id": shipment_id,
             "route_id": route_id, "hub_id": hub_id, "is_test_data": is_test_data},
        )
    return alert_id


def _notify_alert(conn: pgwire.Connection, alert: dict[str, Any]) -> None:
    """يبني مستلمي التنبيه من بيانات المسؤولين ويضعه في صندوق الصادر.

    الإخفاق هنا **لا يُسقط التنبيه**: تسجيل الحدث أهم من إرسال الرسالة، فلو
    تعذّر بناء قائمة المستلمين بقي التنبيه في النظام وظهر في الشاشة.
    """
    from . import notifications

    try:
        recipients: list[tuple[str, str]] = []
        rows = conn.fetch_all(
            """
            SELECT DISTINCT u.email, u.phone
            FROM users u
            JOIN user_scopes s ON s.user_id = u.id
            WHERE u.is_active AND u.role IN ('HUB_SUPERVISOR', 'CONTROL_TOWER')
              AND s.scope_type = 'HUB' AND s.scope_id = $1::uuid
            """,
            [alert["hub_id"]],
        ) if alert.get("hub_id") else []

        for row in rows:
            if row["email"]:
                recipients.append(("EMAIL", row["email"]))
            if row["phone"]:
                recipients.append(("SMS", row["phone"]))

        if recipients:
            notifications.enqueue_for_alert(conn, alert, recipients=recipients)
    except Exception:
        logger.exception("تعذّر إنشاء إشعار خارجي للتنبيه %s", alert.get("id"))


def list_alerts(
    context: SecurityContext,
    *,
    hub_id: str | None = None,
    severity: str | None = None,
    alert_type: str | None = None,
    only_open: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Any], int]:
    clauses = ["1=1"]
    params: list[Any] = []

    def add(clause: str, value: Any) -> None:
        params.append(value)
        clauses.append(clause.replace("?", f"${len(params)}"))

    if only_open:
        clauses.append("a.resolved_at IS NULL")
    if hub_id:
        add("a.hub_id = ?::uuid", hub_id)
    if severity:
        add("a.severity = ?", severity)
    if alert_type:
        add("a.alert_type = ?", alert_type)

    where = " AND ".join(clauses)
    with session(context) as conn:
        total = int(conn.fetch_value(
            f"SELECT count(*) FROM alerts a WHERE {where}", params) or 0)
        rows = conn.fetch_all(
            "SELECT a.id::text AS id, a.alert_type, a.severity, a.title_ar, a.body_ar, "
            "a.shipment_id::text AS shipment_id, a.route_id::text AS route_id, "
            "a.hub_id::text AS hub_id, a.driver_id::text AS driver_id, a.context, "
            "a.created_at, a.acknowledged_at, a.resolved_at, a.action_note, "
            "s.reference AS shipment_reference, r.reference AS route_reference, "
            "h.name_ar AS hub_name_ar, d.full_name AS driver_name, "
            "u.full_name AS acknowledged_by_name "
            "FROM alerts a "
            "LEFT JOIN shipments s ON s.id = a.shipment_id "
            "LEFT JOIN routes r ON r.id = a.route_id "
            "LEFT JOIN hubs h ON h.id = a.hub_id "
            "LEFT JOIN drivers d ON d.id = a.driver_id "
            "LEFT JOIN users u ON u.id = a.acknowledged_by "
            f"WHERE {where} "
            "ORDER BY CASE a.severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 "
            "WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 3 ELSE 4 END, a.created_at DESC "
            f"LIMIT {int(limit)} OFFSET {int(offset)}",
            params,
        )
    return rows, total


def acknowledge(
    context: SecurityContext, alert_id: str, *, ip_address: str | None = None
) -> dict[str, Any]:
    with transaction(context) as conn:
        row = conn.fetch_one(
            "UPDATE alerts SET acknowledged_by = $1::uuid, acknowledged_at = now() "
            "WHERE id = $2::uuid AND acknowledged_at IS NULL "
            "RETURNING id::text AS id, alert_type",
            [context.user_id, alert_id],
        )
        if row is None:
            raise Conflict("التنبيه غير موجود أو مستلَم مسبقًا")
    return {"alert_id": alert_id, "acknowledged": True}


def resolve(
    context: SecurityContext,
    alert_id: str,
    action_note: str,
    *,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """يُغلق التنبيه بإجراء مسجّل — لا إغلاق بلا إجراء (§24/§27)."""
    with transaction(context) as conn:
        alert = conn.fetch_one(
            "SELECT id::text AS id, alert_type, hub_id::text AS hub_id, "
            "shipment_id::text AS shipment_id, route_id::text AS route_id, resolved_at "
            "FROM alerts WHERE id = $1::uuid FOR UPDATE",
            [alert_id],
        )
        if alert is None:
            raise NotFound("التنبيه غير موجود")
        if alert["resolved_at"] is not None:
            raise Conflict("التنبيه مغلق مسبقًا")

        conn.execute(
            "UPDATE alerts SET resolved_at = now(), action_note = $1, "
            "acknowledged_by = coalesce(acknowledged_by, $2::uuid), "
            "acknowledged_at = coalesce(acknowledged_at, now()) WHERE id = $3::uuid",
            [action_note, context.user_id, alert_id],
        )
        audit.record(
            conn, context, AuditAction.EXCEPTION_ACTION,
            entity_type="alert", entity_id=alert_id, entity_label=alert["alert_type"],
            new_value={"resolved": True, "action_note": action_note},
            reason=action_note, ip_address=ip_address,
        )
    return {"alert_id": alert_id, "resolved": True}


def summary_by_hub(context: SecurityContext) -> list[Any]:
    with session(context) as conn:
        return conn.fetch_all(
            "SELECT a.hub_id::text AS hub_id, h.name_ar AS hub_name_ar, "
            "count(*) FILTER (WHERE a.severity = 'CRITICAL') AS critical, "
            "count(*) FILTER (WHERE a.severity = 'HIGH') AS high, "
            "count(*) FILTER (WHERE a.severity = 'MEDIUM') AS medium, "
            "count(*) AS total "
            "FROM alerts a LEFT JOIN hubs h ON h.id = a.hub_id "
            "WHERE a.resolved_at IS NULL GROUP BY a.hub_id, h.name_ar "
            "ORDER BY critical DESC, high DESC"
        )


# ------------------------------------------------- المراقب الدوري ---------

def scan_operational_alerts(
    context: SecurityContext | None = None, *, now: dt.datetime | None = None
) -> dict[str, int]:
    """فحص دوري ينتج تنبيهات SLA والتأخير وتوقف التتبع.

    يُشغَّل من مهمة خلفية كل دقيقة. كل تنبيه يحمل السبب والكيان المتأثر.
    """
    context = context or SecurityContext.system("ALERT_SCANNER")
    now = now or dt.datetime.now(dt.timezone.utc)
    counters = {
        "pickup_approaching": 0, "pickup_late": 0, "delivery_late": 0,
        "sla_at_risk": 0, "sla_breached": 0, "tracking_stale": 0,
        "route_without_driver": 0,
    }

    with transaction(context) as conn:
        # اقتراب موعد الالتقاط
        rows = conn.fetch_all(
            """
            SELECT s.id::text AS id, s.reference, s.hub_id::text AS hub_id,
                   s.route_id::text AS route_id, s.driver_id::text AS driver_id,
                   s.planned_pickup_arrival
            FROM shipments s
            WHERE s.status IN ('PUBLISHED','IN_PROGRESS')
              AND s.actual_pickup_arrival IS NULL
              AND s.planned_pickup_arrival BETWEEN $1::timestamptz
                  AND $1::timestamptz + interval '20 minutes'
            """,
            [now],
        )
        for row in rows:
            if raise_alert(
                conn, AlertType.PICKUP_WINDOW_APPROACHING,
                title_ar="اقتراب موعد الالتقاط",
                body_ar=f"الشحنة {row['reference']} موعد التقاطها خلال ٢٠ دقيقة",
                shipment_id=row["id"], route_id=row["route_id"], hub_id=row["hub_id"],
                driver_id=row["driver_id"],
            ):
                counters["pickup_approaching"] += 1

        # تأخر الالتقاط
        rows = conn.fetch_all(
            """
            SELECT s.id::text AS id, s.reference, s.hub_id::text AS hub_id,
                   s.route_id::text AS route_id, s.driver_id::text AS driver_id,
                   s.pickup_window_to
            FROM shipments s
            WHERE s.status IN ('PUBLISHED','IN_PROGRESS','ARRIVED_PICKUP')
              AND s.actual_pickup_at IS NULL
              AND s.pickup_window_to < $1::timestamptz - interval '10 minutes'
            """,
            [now],
        )
        for row in rows:
            minutes = int((now - row["pickup_window_to"]).total_seconds() / 60)
            if raise_alert(
                conn, AlertType.PICKUP_LATE,
                title_ar="تأخر الالتقاط",
                body_ar=(f"الشحنة {row['reference']} تجاوزت نهاية نافذة الالتقاط "
                         f"بـ {minutes} دقيقة ولم يُسجَّل التقاط"),
                shipment_id=row["id"], route_id=row["route_id"], hub_id=row["hub_id"],
                driver_id=row["driver_id"],
                context_data={"late_minutes": minutes},
            ):
                counters["pickup_late"] += 1

        # تأخر التسليم: التُقطت العينة فعلًا وتجاوز الوصول المخطط للتسليم مهلته.
        # هذا تنبيه مستقل عن SLA: قد يتأخر التسليم عن الخطة وما زال أمامه متسع
        # قبل الموعد النهائي — والتشغيل يحتاج معرفة ذلك مبكرًا لا بعد فوات SLA.
        rows = conn.fetch_all(
            """
            SELECT s.id::text AS id, s.reference, s.hub_id::text AS hub_id,
                   s.route_id::text AS route_id, s.driver_id::text AS driver_id,
                   s.planned_dropoff_arrival
            FROM shipments s
            WHERE s.status IN ('PICKED_UP','ARRIVED_DELIVERY','IN_PROGRESS')
              AND s.actual_pickup_at IS NOT NULL
              AND s.actual_dropoff_at IS NULL
              AND s.planned_dropoff_arrival IS NOT NULL
              AND s.planned_dropoff_arrival < $1::timestamptz - interval '10 minutes'
            """,
            [now],
        )
        for row in rows:
            minutes = int((now - row["planned_dropoff_arrival"]).total_seconds() / 60)
            if raise_alert(
                conn, AlertType.DELIVERY_LATE,
                title_ar="تأخر التسليم",
                body_ar=(f"الشحنة {row['reference']} تجاوزت وقت التسليم المخطط "
                         f"بـ {minutes} دقيقة ولم يُسجَّل تسليم"),
                shipment_id=row["id"], route_id=row["route_id"], hub_id=row["hub_id"],
                driver_id=row["driver_id"], context_data={"late_minutes": minutes},
            ):
                counters["delivery_late"] += 1

        # خطر تجاوز SLA
        rows = conn.fetch_all(
            """
            SELECT s.id::text AS id, s.reference, s.hub_id::text AS hub_id,
                   s.route_id::text AS route_id, s.driver_id::text AS driver_id,
                   s.sla_deadline
            FROM shipments s
            WHERE s.status IN ('PICKED_UP','ARRIVED_DELIVERY','IN_PROGRESS','PUBLISHED')
              AND s.actual_dropoff_at IS NULL
              AND s.sla_deadline BETWEEN $1::timestamptz
                  AND $1::timestamptz + interval '30 minutes'
            """,
            [now],
        )
        for row in rows:
            minutes = int((row["sla_deadline"] - now).total_seconds() / 60)
            if raise_alert(
                conn, AlertType.SLA_AT_RISK,
                title_ar="اقتراب تجاوز SLA",
                body_ar=(f"الشحنة {row['reference']} يتبقى على موعدها النهائي "
                         f"{minutes} دقيقة ولم تُسلَّم"),
                shipment_id=row["id"], route_id=row["route_id"], hub_id=row["hub_id"],
                driver_id=row["driver_id"], context_data={"remaining_minutes": minutes},
            ):
                counters["sla_at_risk"] += 1

        # تجاوز SLA فعلي
        rows = conn.fetch_all(
            """
            SELECT s.id::text AS id, s.reference, s.hub_id::text AS hub_id,
                   s.route_id::text AS route_id, s.driver_id::text AS driver_id,
                   s.sla_deadline
            FROM shipments s
            WHERE s.actual_dropoff_at IS NULL
              AND s.status NOT IN ('CANCELLED_BEFORE_PICKUP','REJECTED','COMPLETED',
                                   'DELIVERED','DRAFT','VALIDATED')
              AND s.sla_deadline < $1::timestamptz
            """,
            [now],
        )
        for row in rows:
            minutes = int((now - row["sla_deadline"]).total_seconds() / 60)
            conn.execute(
                "UPDATE shipments SET sla_breached = true, delay_minutes = $1 "
                "WHERE id = $2::uuid",
                [minutes, row["id"]],
            )
            if raise_alert(
                conn, AlertType.SLA_BREACHED,
                title_ar="تجاوز SLA",
                body_ar=(f"الشحنة {row['reference']} تجاوزت موعدها النهائي "
                         f"بـ {minutes} دقيقة"),
                shipment_id=row["id"], route_id=row["route_id"], hub_id=row["hub_id"],
                driver_id=row["driver_id"], context_data={"breach_minutes": minutes},
            ):
                counters["sla_breached"] += 1

        # توقف تحديث موقع السائق
        rows = conn.fetch_all(
            """
            SELECT r.id::text AS id, r.reference, r.hub_id::text AS hub_id,
                   r.driver_id::text AS driver_id, d.full_name,
                   p.recorded_at
            FROM routes r
            JOIN drivers d ON d.id = r.driver_id
            LEFT JOIN driver_last_position p ON p.driver_id = r.driver_id
            WHERE r.status = 'IN_PROGRESS'
              AND (p.recorded_at IS NULL
                   OR p.recorded_at < $1::timestamptz - interval '3 minutes')
            """,
            [now],
        )
        for row in rows:
            last = row["recorded_at"].isoformat() if row["recorded_at"] else "لا يوجد"
            if raise_alert(
                conn, AlertType.TRACKING_STALE,
                title_ar="توقف تحديث موقع السائق",
                body_ar=(f"السائق {row['full_name']} في الرحلة {row['reference']} — "
                         f"آخر تحديث موقع: {last}"),
                route_id=row["id"], hub_id=row["hub_id"], driver_id=row["driver_id"],
                context_data={"last_position_at": last},
            ):
                counters["tracking_stale"] += 1

        # رحلات منشورة بلا سائق (لا ينبغي أن تحدث — شبكة أمان)
        rows = conn.fetch_all(
            "SELECT id::text AS id, reference, hub_id::text AS hub_id FROM routes "
            "WHERE status IN ('PLANNED','ASSIGNED') AND driver_id IS NULL "
            "AND service_date = ($1::timestamptz AT TIME ZONE 'Asia/Riyadh')::date",
            [now],
        )
        for row in rows:
            if raise_alert(
                conn, AlertType.ROUTE_WITHOUT_DRIVER,
                title_ar="رحلة اليوم بلا سائق",
                body_ar=f"الرحلة {row['reference']} مجدولة اليوم ولم يُسند لها سائق",
                route_id=row["id"], hub_id=row["hub_id"],
            ):
                counters["route_without_driver"] += 1

    return counters
