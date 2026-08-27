"""تنفيذ الرحلة من تطبيق السائق (§18/§19) — الأحداث، المستندات، الاستثناءات.

قواعد ثابتة مطبَّقة هنا وفي قاعدة البيانات معًا:
* لا يبدأ السائق رحلة غير منشورة، ولا قبل تاريخها.
* التسلسل: بدء ← وصلت ← التقطت ← وصلت ← سلّمت. لا قفز.
* التصوير **بعد** الالتقاط أو التسليم، و«وصلت» لا تتطلب صورة.
* كل حدث يُربط بالوقت الفعلي والموقع وهوية السائق.
* الأحداث المخزّنة دون اتصال تُزامَن بمعرّف عميل يمنع التكرار.
* لا حقل لإدخال درجة الحرارة يدويًا — غير موجود في هذه الواجهة أصلًا.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pgwire
from masar_core.constants import (
    EXCEPTION_KEEPS_OBLIGATION_OPEN,
    EXCEPTION_REQUIRES_PROOF,
    AlertType,
    AuditAction,
    ExceptionReason,
    RouteStatus,
    Severity,
    ShipmentStatus,
    StopStatus,
)
from masar_core.errors import Conflict, Forbidden, NotFound, ValidationError
from masar_core.state_machine import (
    assert_can_cancel_before_pickup,
    assert_delivery_after_pickup,
    assert_route_completable,
    assert_route_startable,
    shipment_sm,
)
from masar_db.driver import SecurityContext, session, transaction

from . import alerts, audit, events, storage


def _driver_route(conn: pgwire.Connection, context: SecurityContext, route_id: str) -> Any:
    route = conn.fetch_one(
        "SELECT id::text AS id, reference, status, service_date, "
        "driver_id::text AS driver_id, hub_id::text AS hub_id, "
        "planned_start_at, planned_end_at FROM routes WHERE id = $1::uuid FOR UPDATE",
        [route_id],
    )
    if route is None:
        raise NotFound("الرحلة غير موجودة")
    if context.role == "DRIVER" and route["driver_id"] != context.driver_id:
        raise Forbidden("هذه الرحلة ليست مسندة إليك")
    return route


def my_routes(
    context: SecurityContext, *, service_date: dt.date | None = None, days: int = 7
) -> list[Any]:
    """رحلات السائق المنشورة فقط (§5: يرى الرحلات المسندة إليه فقط)."""
    if not context.driver_id:
        raise Forbidden("هذا الحساب غير مرتبط بسجل سائق")
    with session(context) as conn:
        return conn.fetch_all(
            """
            SELECT r.id::text AS id, r.reference, r.service_date, r.status,
                   r.planned_start_at, r.planned_end_at, r.actual_start_at,
                   r.actual_end_at, r.distance_km, r.shipment_count, r.pickup_count,
                   r.delivery_count, r.is_long_haul, h.name_ar AS hub_name_ar,
                   h.lat AS hub_lat, h.lon AS hub_lon, v.plate_number, b.code AS box_code,
                   (SELECT count(*) FROM route_stops st
                    WHERE st.route_id = r.id AND st.status = 'DONE') AS completed_stops,
                   (SELECT count(*) FROM route_stops st
                    WHERE st.route_id = r.id AND st.kind <> 'HUB_START') AS total_stops
            FROM routes r
            JOIN hubs h ON h.id = r.hub_id
            LEFT JOIN vehicles v ON v.id = r.vehicle_id
            LEFT JOIN boxes b ON b.id = r.box_id
            WHERE r.driver_id = $1::uuid
              AND r.status IN ('PUBLISHED','IN_PROGRESS','COMPLETED')
              AND ($2::date IS NULL OR r.service_date = $2::date)
              AND r.service_date >= current_date - $3::int
            ORDER BY r.service_date DESC, r.sequence_in_day
            """,
            [context.driver_id, service_date, days],
        )


def start_route(
    context: SecurityContext,
    route_id: str,
    *,
    lat: float | None = None,
    lon: float | None = None,
    occurred_at: dt.datetime | None = None,
    client_event_id: str | None = None,
    was_offline: bool = False,
    ip_address: str | None = None,
) -> dict[str, Any]:
    now = occurred_at or dt.datetime.now(dt.timezone.utc)
    with transaction(context) as conn:
        route = _driver_route(conn, context, route_id)
        today = dt.datetime.now(dt.timezone.utc).astimezone(
            dt.timezone(dt.timedelta(hours=3))).date()
        assert_route_startable(route["status"], route["service_date"], today)

        conn.execute(
            "UPDATE routes SET status = 'IN_PROGRESS', actual_start_at = $1::timestamptz "
            "WHERE id = $2::uuid",
            [now, route_id],
        )
        conn.execute(
            "UPDATE shipments SET status = 'IN_PROGRESS' WHERE route_id = $1::uuid "
            "AND status = 'PUBLISHED'",
            [route_id],
        )
        shipments = conn.fetch_all(
            "SELECT id::text AS id FROM shipments WHERE route_id = $1::uuid", [route_id])
        for shipment in shipments:
            _record_event(
                conn, context, shipment["id"], "ROUTE_STARTED",
                route_id=route_id, occurred_at=now, lat=lat, lon=lon,
                client_event_id=f"{client_event_id}:{shipment['id']}" if client_event_id else None,
                was_offline=was_offline,
            )
        events.publish(
            conn, events.TOPIC_ROUTE,
            {"route_id": route_id, "action": "STARTED",
             "at": now.isoformat(), "lat": lat, "lon": lon},
            hub_id=route["hub_id"], driver_id=route["driver_id"],
        )
        audit.record(
            conn, context, AuditAction.SHIPMENT_STATUS_CHANGE,
            entity_type="route", entity_id=route_id, entity_label=route["reference"],
            new_value={"status": "IN_PROGRESS", "at": now.isoformat()},
            ip_address=ip_address,
        )
    return {"route_id": route_id, "status": "IN_PROGRESS", "started_at": now}


def _record_event(
    conn: pgwire.Connection,
    context: SecurityContext,
    shipment_id: str,
    event_type: str,
    *,
    route_id: str | None = None,
    route_stop_id: str | None = None,
    occurred_at: dt.datetime,
    lat: float | None = None,
    lon: float | None = None,
    accuracy_m: float | None = None,
    client_event_id: str | None = None,
    was_offline: bool = False,
    payload: dict[str, Any] | None = None,
) -> str | None:
    """يسجل حدثًا. يعيد ``None`` إن كان مكررًا (مزامنة بعد انقطاع)."""
    # مفتاح العميل يمنع التكرار عند إعادة المزامنة. يُستخدم ON CONFLICT لا
    # استثناء، حتى لا تُجهض المعاملة المحيطة عند إعادة إرسال حدث سبق تسجيله.
    return conn.fetch_value(
        """
        INSERT INTO shipment_events (
            shipment_id, route_id, route_stop_id, event_type, occurred_at,
            lat, lon, accuracy_m, driver_id, actor_user_id, client_event_id,
            was_offline, payload
        ) VALUES ($1::uuid,$2::uuid,$3::uuid,$4,$5::timestamptz,$6,$7,$8,
                  $9::uuid,$10::uuid,$11,$12,$13::jsonb)
        ON CONFLICT (driver_id, client_event_id) WHERE client_event_id IS NOT NULL
        DO NOTHING
        RETURNING id::text
        """,
        [shipment_id, route_id, route_stop_id, event_type, occurred_at,
         lat, lon, accuracy_m, context.driver_id, context.user_id,
         client_event_id, was_offline, pgwire.Jsonb(payload or {})],
    )


def _stop_and_shipment(
    conn: pgwire.Connection, context: SecurityContext, stop_id: str
) -> tuple[Any, Any, Any]:
    stop = conn.fetch_one(
        "SELECT st.id::text AS id, st.route_id::text AS route_id, st.sequence, st.kind, "
        "st.status, st.shipment_id::text AS shipment_id, st.label_ar, "
        "st.planned_arrival_at, st.window_from, st.window_to "
        "FROM route_stops st WHERE st.id = $1::uuid FOR UPDATE",
        [stop_id],
    )
    if stop is None:
        raise NotFound("المحطة غير موجودة")
    route = _driver_route(conn, context, stop["route_id"])
    shipment = None
    if stop["shipment_id"]:
        shipment = conn.fetch_one(
            "SELECT id::text AS id, reference, status, hub_id::text AS hub_id, "
            "actual_pickup_at, actual_dropoff_at, sla_deadline, pickup_window_to "
            "FROM shipments WHERE id = $1::uuid FOR UPDATE",
            [stop["shipment_id"]],
        )
    return stop, route, shipment


def _assert_sequence(conn: pgwire.Connection, stop: Any) -> None:
    """يمنع القفز فوق محطة سابقة غير محسومة."""
    pending = conn.fetch_one(
        "SELECT sequence, label_ar FROM route_stops WHERE route_id = $1::uuid "
        "AND sequence < $2 AND kind <> 'HUB_START' "
        "AND status NOT IN ('DONE','SKIPPED','FAILED') ORDER BY sequence LIMIT 1",
        [stop["route_id"], stop["sequence"]],
    )
    if pending:
        raise Conflict(
            f"لا يمكن تنفيذ هذه المحطة قبل حسم المحطة السابقة "
            f"({pending['sequence']}: {pending['label_ar']})"
        )


def mark_arrived(
    context: SecurityContext,
    stop_id: str,
    *,
    lat: float | None = None,
    lon: float | None = None,
    accuracy_m: float | None = None,
    occurred_at: dt.datetime | None = None,
    client_event_id: str | None = None,
    was_offline: bool = False,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """«وصلت» — لا تتطلب تصويرًا (§18)."""
    now = occurred_at or dt.datetime.now(dt.timezone.utc)
    with transaction(context) as conn:
        stop, route, shipment = _stop_and_shipment(conn, context, stop_id)
        if route["status"] != RouteStatus.IN_PROGRESS:
            raise Conflict("يجب بدء الرحلة أولًا")
        if stop["status"] != StopStatus.PENDING:
            raise Conflict(f"حالة المحطة {stop['status']} لا تسمح بتسجيل الوصول")
        _assert_sequence(conn, stop)

        conn.execute(
            "UPDATE route_stops SET status = 'ARRIVED', actual_arrival_at = $1::timestamptz, "
            "actual_lat = $2, actual_lon = $3 WHERE id = $4::uuid",
            [now, lat, lon, stop_id],
        )

        target_status = (
            ShipmentStatus.ARRIVED_PICKUP if stop["kind"] == "PICKUP"
            else ShipmentStatus.ARRIVED_DELIVERY
        )
        shipment_sm.check(shipment["status"], target_status)
        column = (
            "actual_pickup_arrival" if stop["kind"] == "PICKUP"
            else "actual_dropoff_arrival"
        )
        conn.execute(
            f"UPDATE shipments SET status = $1, {column} = $2::timestamptz "
            "WHERE id = $3::uuid",
            [target_status, now, shipment["id"]],
        )
        _record_event(
            conn, context, shipment["id"],
            "ARRIVED_PICKUP" if stop["kind"] == "PICKUP" else "ARRIVED_DELIVERY",
            route_id=route["id"], route_stop_id=stop_id, occurred_at=now,
            lat=lat, lon=lon, accuracy_m=accuracy_m,
            client_event_id=client_event_id, was_offline=was_offline,
        )
        events.publish(
            conn, events.TOPIC_SHIPMENT,
            {"shipment_id": shipment["id"], "reference": shipment["reference"],
             "status": target_status, "stop_id": stop_id, "at": now.isoformat()},
            hub_id=route["hub_id"], driver_id=route["driver_id"],
        )
    return {"stop_id": stop_id, "status": "ARRIVED", "shipment_status": target_status}


def mark_picked_up(
    context: SecurityContext,
    stop_id: str,
    *,
    lat: float | None = None,
    lon: float | None = None,
    piece_count: int | None = None,
    occurred_at: dt.datetime | None = None,
    client_event_id: str | None = None,
    was_offline: bool = False,
    ip_address: str | None = None,
) -> dict[str, Any]:
    now = occurred_at or dt.datetime.now(dt.timezone.utc)
    with transaction(context) as conn:
        stop, route, shipment = _stop_and_shipment(conn, context, stop_id)
        if stop["kind"] != "PICKUP":
            raise ValidationError("هذه ليست محطة التقاط")
        if stop["status"] != StopStatus.ARRIVED:
            raise Conflict("سجّل «وصلت» قبل تسجيل الالتقاط")

        shipment_sm.check(shipment["status"], ShipmentStatus.PICKED_UP)
        conn.execute(
            "UPDATE route_stops SET status = 'DONE', actual_completed_at = $1::timestamptz "
            "WHERE id = $2::uuid",
            [now, stop_id],
        )
        breached = bool(shipment["pickup_window_to"] and now > shipment["pickup_window_to"])
        conn.execute(
            "UPDATE shipments SET status = 'PICKED_UP', actual_pickup_at = $1::timestamptz, "
            "pickup_window_breached = $2, piece_count = coalesce($3, piece_count) "
            "WHERE id = $4::uuid",
            [now, breached, piece_count, shipment["id"]],
        )
        conn.execute(
            "INSERT INTO custody_transfers (shipment_id, from_party, to_party, "
            "from_entity_id, to_entity_id, occurred_at, lat, lon) "
            "VALUES ($1::uuid,'FACILITY','DRIVER',NULL,$2::uuid,$3::timestamptz,$4,$5)",
            [shipment["id"], context.driver_id, now, lat, lon],
        )
        _record_event(
            conn, context, shipment["id"], "PICKED_UP",
            route_id=route["id"], route_stop_id=stop_id, occurred_at=now,
            lat=lat, lon=lon, client_event_id=client_event_id, was_offline=was_offline,
            payload={"piece_count": piece_count, "window_breached": breached},
        )
        events.publish(
            conn, events.TOPIC_SHIPMENT,
            {"shipment_id": shipment["id"], "reference": shipment["reference"],
             "status": "PICKED_UP", "at": now.isoformat()},
            hub_id=route["hub_id"], driver_id=route["driver_id"],
        )
        if breached:
            alerts.raise_alert(
                conn, AlertType.PICKUP_LATE,
                title_ar="التقاط بعد نهاية النافذة",
                body_ar=(f"الشحنة {shipment['reference']} التُقطت بعد نهاية النافذة "
                         f"المحددة"),
                shipment_id=shipment["id"], route_id=route["id"],
                hub_id=route["hub_id"], driver_id=route["driver_id"],
            )
    return {"stop_id": stop_id, "shipment_status": "PICKED_UP",
            "pickup_window_breached": breached}


def mark_delivered(
    context: SecurityContext,
    stop_id: str,
    *,
    lat: float | None = None,
    lon: float | None = None,
    receiver_name: str | None = None,
    occurred_at: dt.datetime | None = None,
    client_event_id: str | None = None,
    was_offline: bool = False,
    ip_address: str | None = None,
) -> dict[str, Any]:
    now = occurred_at or dt.datetime.now(dt.timezone.utc)
    with transaction(context) as conn:
        stop, route, shipment = _stop_and_shipment(conn, context, stop_id)
        if stop["kind"] != "DELIVERY":
            raise ValidationError("هذه ليست محطة تسليم")
        if stop["status"] != StopStatus.ARRIVED:
            raise Conflict("سجّل «وصلت» قبل تسجيل التسليم")

        assert_delivery_after_pickup(shipment["actual_pickup_at"], now)
        shipment_sm.check(shipment["status"], ShipmentStatus.DELIVERED)

        sla_breached = bool(shipment["sla_deadline"] and now > shipment["sla_deadline"])
        delay = (
            int((now - shipment["sla_deadline"]).total_seconds() / 60)
            if sla_breached else 0
        )
        conn.execute(
            "UPDATE route_stops SET status = 'DONE', actual_completed_at = $1::timestamptz "
            "WHERE id = $2::uuid",
            [now, stop_id],
        )
        conn.execute(
            "UPDATE shipments SET status = 'DELIVERED', actual_dropoff_at = $1::timestamptz, "
            "sla_breached = $2, delay_minutes = $3, delivery_obligation_open = false "
            "WHERE id = $4::uuid",
            [now, sla_breached, delay, shipment["id"]],
        )
        conn.execute(
            "INSERT INTO custody_transfers (shipment_id, from_party, to_party, "
            "from_entity_id, occurred_at, lat, lon) "
            "VALUES ($1::uuid,'DRIVER','FACILITY',$2::uuid,$3::timestamptz,$4,$5)",
            [shipment["id"], context.driver_id, now, lat, lon],
        )
        _record_event(
            conn, context, shipment["id"], "DELIVERED",
            route_id=route["id"], route_stop_id=stop_id, occurred_at=now,
            lat=lat, lon=lon, client_event_id=client_event_id, was_offline=was_offline,
            payload={"receiver_name": receiver_name, "sla_breached": sla_breached},
        )
        # الشحنة تُغلق تلقائيًا بعد التسليم
        conn.execute(
            "UPDATE shipments SET status = 'COMPLETED' WHERE id = $1::uuid "
            "AND status = 'DELIVERED'",
            [shipment["id"]],
        )
        events.publish(
            conn, events.TOPIC_SHIPMENT,
            {"shipment_id": shipment["id"], "reference": shipment["reference"],
             "status": "COMPLETED", "sla_breached": sla_breached, "at": now.isoformat()},
            hub_id=route["hub_id"], driver_id=route["driver_id"],
        )
        if sla_breached:
            alerts.raise_alert(
                conn, AlertType.SLA_BREACHED,
                title_ar="تسليم بعد الموعد النهائي",
                body_ar=f"الشحنة {shipment['reference']} سُلّمت متأخرة {delay} دقيقة",
                shipment_id=shipment["id"], route_id=route["id"],
                hub_id=route["hub_id"], driver_id=route["driver_id"],
                context_data={"delay_minutes": delay},
            )
        _maybe_complete_route(conn, context, route)
    return {"stop_id": stop_id, "shipment_status": "COMPLETED",
            "sla_breached": sla_breached, "delay_minutes": delay}


def _maybe_complete_route(
    conn: pgwire.Connection, context: SecurityContext, route: Any
) -> None:
    statuses = [
        row["status"] for row in conn.fetch_all(
            "SELECT status FROM shipments WHERE route_id = $1::uuid", [route["id"]])
    ]
    if not statuses:
        return
    try:
        assert_route_completable(statuses)
    except Exception:
        return
    conn.execute(
        "UPDATE routes SET status = 'COMPLETED', actual_end_at = now() "
        "WHERE id = $1::uuid AND status = 'IN_PROGRESS'",
        [route["id"]],
    )
    events.publish(
        conn, events.TOPIC_ROUTE,
        {"route_id": route["id"], "action": "COMPLETED"},
        hub_id=route["hub_id"], driver_id=route["driver_id"],
    )


def upload_document(
    context: SecurityContext,
    *,
    shipment_id: str,
    doc_kind: str,
    content: bytes,
    declared_type: str | None,
    original_name: str | None = None,
    route_stop_id: str | None = None,
    exception_id: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    captured_at: dt.datetime | None = None,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """رفع مستند الالتقاط أو التسليم — **بعد** تسجيل الحدث لا قبله (§18)."""
    if doc_kind not in ("PICKUP_PROOF", "DELIVERY_PROOF", "EXCEPTION_PROOF",
                        "TEMPERATURE_LOG", "OTHER"):
        raise ValidationError("نوع مستند غير معروف")

    content_type = storage.validate_upload(content, declared_type)
    digest = storage.sha256(content)

    with transaction(context) as conn:
        shipment = conn.fetch_one(
            "SELECT id::text AS id, reference, status, hub_id::text AS hub_id, "
            "route_id::text AS route_id, actual_pickup_at, actual_dropoff_at "
            "FROM shipments WHERE id = $1::uuid",
            [shipment_id],
        )
        if shipment is None:
            raise NotFound("الشحنة غير موجودة أو خارج نطاقك")

        if doc_kind == "PICKUP_PROOF" and shipment["actual_pickup_at"] is None:
            raise Conflict("لا يمكن رفع مستند الالتقاط قبل تسجيل الالتقاط")
        if doc_kind == "DELIVERY_PROOF" and shipment["actual_dropoff_at"] is None:
            raise Conflict("لا يمكن رفع مستند التسليم قبل تسجيل التسليم")

        existing = conn.fetch_one(
            "SELECT id::text AS id, storage_key FROM documents "
            "WHERE shipment_id = $1::uuid AND sha256 = $2 AND doc_kind = $3",
            [shipment_id, digest, doc_kind],
        )
        if existing:
            return {"document_id": existing["id"], "duplicate": True}

        key = storage.build_key(f"documents/{doc_kind.lower()}", original_name or "", content_type)
        storage.get_store().put(key, content, content_type)

        document_id = conn.fetch_value(
            "INSERT INTO documents (shipment_id, route_id, route_stop_id, exception_id, "
            "doc_kind, storage_key, original_name, content_type, byte_size, sha256, "
            "captured_at, lat, lon, uploaded_by) "
            "VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,$6,$7,$8,$9,$10,"
            "$11::timestamptz,$12,$13,$14::uuid) RETURNING id::text",
            [shipment_id, shipment["route_id"], route_stop_id, exception_id, doc_kind,
             key, original_name, content_type, len(content), digest,
             captured_at or dt.datetime.now(dt.timezone.utc), lat, lon, context.user_id],
        )
        _record_event(
            conn, context, shipment_id, "DOCUMENT_UPLOADED",
            route_id=shipment["route_id"], route_stop_id=route_stop_id,
            occurred_at=captured_at or dt.datetime.now(dt.timezone.utc),
            lat=lat, lon=lon,
            payload={"document_id": document_id, "doc_kind": doc_kind},
        )
        audit.record(
            conn, context, AuditAction.DOCUMENT_UPLOAD,
            entity_type="document", entity_id=document_id,
            entity_label=f"{doc_kind}/{shipment['reference']}",
            new_value={"sha256": digest, "bytes": len(content), "type": content_type},
            ip_address=ip_address,
        )
    return {"document_id": document_id, "content_type": content_type,
            "byte_size": len(content), "sha256": digest, "duplicate": False}


def read_document(context: SecurityContext, document_id: str) -> tuple[bytes, str, str]:
    """يقرأ مستندًا بعد فحص النطاق — لا وصول مباشر للملفات (§29)."""
    with transaction(context) as conn:
        document = conn.fetch_one(
            "SELECT d.id::text AS id, d.storage_key, d.content_type, d.original_name, "
            "d.shipment_id::text AS shipment_id "
            "FROM documents d WHERE d.id = $1::uuid",
            [document_id],
        )
        if document is None:
            raise NotFound("المستند غير موجود أو خارج نطاق صلاحياتك")
        audit.record(
            conn, context, AuditAction.DOCUMENT_ACCESS,
            entity_type="document", entity_id=document_id,
        )
    content = storage.get_store().get(document["storage_key"])
    return content, document["content_type"], document["original_name"] or "document"


def record_exception(
    context: SecurityContext,
    *,
    shipment_id: str,
    reason: str,
    note: str | None = None,
    route_stop_id: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    occurred_at: dt.datetime | None = None,
    client_event_id: str | None = None,
    was_offline: bool = False,
    has_proof: bool = False,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """تسجيل حالة استثنائية (§19)."""
    if reason not in set(ExceptionReason):
        raise ValidationError(f"سبب استثناء غير معروف: {reason}")
    if reason in EXCEPTION_REQUIRES_PROOF and not has_proof:
        raise ValidationError(
            f"سبب «{reason}» يتطلب رفع إثبات (صورة أو مستند) مع التسجيل",
            requires_proof=True,
        )

    now = occurred_at or dt.datetime.now(dt.timezone.utc)
    keeps_obligation = reason in EXCEPTION_KEEPS_OBLIGATION_OPEN

    with transaction(context) as conn:
        # منع التكرار عند إعادة المزامنة **قبل** أي كتابة. بقية الإجراءات
        # (وصول/التقاط/تسليم) تحميها آلة الحالة تلقائيًا لأن إعادة تنفيذها
        # انتقال غير مسموح؛ أما الاستثناء فإعادته من الحالة EXCEPTION إلى
        # نفسها مقبولة، فكان الحدث المعاد يُنشئ **سجل استثناء ثانيًا** ويُحتسب
        # مطبَّقًا. الفحص هنا يجعل الطابور القابل لإعادة الإرسال آمنًا فعلًا.
        if client_event_id:
            existing = conn.fetch_one(
                "SELECT id::text AS id FROM shipment_events "
                "WHERE client_event_id = $1 AND shipment_id = $2::uuid LIMIT 1",
                [str(client_event_id), shipment_id],
            )
            if existing is not None:
                raise Conflict(
                    "حدث سبق تسجيله بنفس معرّف العميل — تجاهُل إعادة المزامنة",
                    client_event_id=str(client_event_id),
                )

        shipment = conn.fetch_one(
            "SELECT id::text AS id, reference, status, hub_id::text AS hub_id, "
            "route_id::text AS route_id, driver_id::text AS driver_id "
            "FROM shipments WHERE id = $1::uuid FOR UPDATE",
            [shipment_id],
        )
        if shipment is None:
            raise NotFound("الشحنة غير موجودة أو خارج نطاقك")

        if shipment["status"] != ShipmentStatus.EXCEPTION:
            shipment_sm.check(shipment["status"], ShipmentStatus.EXCEPTION)
            conn.execute(
                "UPDATE shipments SET status = 'EXCEPTION', "
                "delivery_obligation_open = $1 WHERE id = $2::uuid",
                [keeps_obligation, shipment_id],
            )

        exception_id = conn.fetch_value(
            "INSERT INTO shipment_exceptions (shipment_id, route_id, route_stop_id, "
            "hub_id, reason, note, occurred_at, lat, lon, reported_by, "
            "reported_by_driver, keeps_obligation) "
            "VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,$6,$7::timestamptz,$8,$9,"
            "$10::uuid,$11::uuid,$12) RETURNING id::text",
            [shipment_id, shipment["route_id"], route_stop_id, shipment["hub_id"],
             reason, note, now, lat, lon, context.user_id, context.driver_id,
             keeps_obligation],
        )
        if route_stop_id:
            conn.execute(
                "UPDATE route_stops SET status = 'FAILED' WHERE id = $1::uuid",
                [route_stop_id],
            )
        _record_event(
            conn, context, shipment_id, "EXCEPTION_RECORDED",
            route_id=shipment["route_id"], route_stop_id=route_stop_id,
            occurred_at=now, lat=lat, lon=lon,
            client_event_id=client_event_id, was_offline=was_offline,
            payload={"reason": reason, "note": note, "exception_id": exception_id},
        )

        alert_type = {
            ExceptionReason.SAMPLES_NOT_READY: AlertType.SAMPLES_NOT_READY,
            ExceptionReason.NO_SAMPLES: AlertType.PICKUP_FAILED,
            ExceptionReason.FACILITY_CLOSED: AlertType.PICKUP_FAILED,
            ExceptionReason.NO_STAFF: AlertType.DELIVERY_FAILED,
            ExceptionReason.LOCATION_UNREACHABLE: AlertType.DELIVERY_FAILED,
            ExceptionReason.TEMPERATURE_BREACH: AlertType.TEMPERATURE_BREACH,
        }.get(reason, AlertType.DELIVERY_FAILED)

        alerts.raise_alert(
            conn, alert_type,
            title_ar="حالة استثنائية",
            body_ar=(f"الشحنة {shipment['reference']}: {reason}"
                     + (f" — {note}" if note else "")),
            shipment_id=shipment_id, route_id=shipment["route_id"],
            hub_id=shipment["hub_id"], driver_id=shipment["driver_id"],
            dedupe_key=f"EXC:{exception_id}",
            context_data={"exception_id": exception_id, "reason": reason,
                          "keeps_obligation": keeps_obligation},
        )
        audit.record(
            conn, context, AuditAction.EXCEPTION_RECORD,
            entity_type="shipment_exception", entity_id=exception_id,
            entity_label=shipment["reference"],
            new_value={"reason": reason, "note": note},
            ip_address=ip_address,
        )
        events.publish(
            conn, events.TOPIC_SHIPMENT,
            {"shipment_id": shipment_id, "status": "EXCEPTION", "reason": reason},
            hub_id=shipment["hub_id"], driver_id=shipment["driver_id"],
        )

    return {
        "exception_id": exception_id,
        "shipment_id": shipment_id,
        "status": ShipmentStatus.EXCEPTION,
        "keeps_obligation_open": keeps_obligation,
        "requires_proof": reason in EXCEPTION_REQUIRES_PROOF,
    }


def resolve_exception(
    context: SecurityContext,
    exception_id: str,
    *,
    action_taken: str,
    new_shipment_status: str,
    resolution: str | None = None,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """يحسم الاستثناء بقرار المشرف — لا حسم بلا إجراء مسجّل (§19)."""
    with transaction(context) as conn:
        record = conn.fetch_one(
            "SELECT e.id::text AS id, e.shipment_id::text AS shipment_id, e.reason, "
            "e.status, e.hub_id::text AS hub_id, s.status AS shipment_status, "
            "s.reference FROM shipment_exceptions e "
            "JOIN shipments s ON s.id = e.shipment_id WHERE e.id = $1::uuid FOR UPDATE",
            [exception_id],
        )
        if record is None:
            raise NotFound("الحالة الاستثنائية غير موجودة")
        if record["status"] == "RESOLVED":
            raise Conflict("الحالة محسومة مسبقًا")
        context.require_hub(record["hub_id"])

        if new_shipment_status == ShipmentStatus.CANCELLED_BEFORE_PICKUP:
            shipment = conn.fetch_one(
                "SELECT actual_pickup_at FROM shipments WHERE id = $1::uuid",
                [record["shipment_id"]])
            assert_can_cancel_before_pickup(
                ShipmentStatus.PICKED_UP if shipment["actual_pickup_at"]
                else record["shipment_status"]
            )

        shipment_sm.check(
            record["shipment_status"], new_shipment_status, reason=action_taken)

        conn.execute(
            "UPDATE shipment_exceptions SET status = 'RESOLVED', action_taken = $1, "
            "resolution = $2, resolved_by = $3::uuid, resolved_at = now() "
            "WHERE id = $4::uuid",
            [action_taken, resolution, context.user_id, exception_id],
        )
        assignments = ["status = $1"]
        params: list[Any] = [new_shipment_status]
        if new_shipment_status in (ShipmentStatus.COMPLETED, ShipmentStatus.DELIVERED,
                                   ShipmentStatus.CANCELLED_BEFORE_PICKUP,
                                   ShipmentStatus.FAILED):
            assignments.append("delivery_obligation_open = false")
        if new_shipment_status == ShipmentStatus.CANCELLED_BEFORE_PICKUP:
            params.append(action_taken)
            assignments.append(f"cancel_reason = ${len(params)}")
        elif new_shipment_status == ShipmentStatus.FAILED:
            params.append(action_taken)
            assignments.append(f"failure_reason = ${len(params)}")
        params.append(record["shipment_id"])
        conn.execute(
            f"UPDATE shipments SET {', '.join(assignments)} "
            f"WHERE id = ${len(params)}::uuid",
            params,
        )

        conn.execute(
            "UPDATE alerts SET resolved_at = now(), action_note = $1, "
            "acknowledged_by = coalesce(acknowledged_by, $2::uuid), "
            "acknowledged_at = coalesce(acknowledged_at, now()) "
            "WHERE dedupe_key = $3 AND resolved_at IS NULL",
            [action_taken, context.user_id, f"EXC:{exception_id}"],
        )
        audit.record(
            conn, context, AuditAction.EXCEPTION_ACTION,
            entity_type="shipment_exception", entity_id=exception_id,
            entity_label=record["reference"],
            old_value={"status": record["shipment_status"]},
            new_value={"status": new_shipment_status, "action_taken": action_taken},
            reason=action_taken, ip_address=ip_address,
        )
        events.publish(
            conn, events.TOPIC_SHIPMENT,
            {"shipment_id": record["shipment_id"], "status": new_shipment_status,
             "exception_resolved": exception_id},
            hub_id=record["hub_id"],
        )
    return {"exception_id": exception_id, "shipment_status": new_shipment_status}


def sync_offline_events(
    context: SecurityContext, events_payload: list[dict[str, Any]]
) -> dict[str, Any]:
    """مزامنة الأحداث المخزّنة أثناء انقطاع الإنترنت (§18).

    كل حدث يحمل ``client_event_id`` فريدًا؛ إعادة الإرسال لا تُنشئ سجلًا
    مكررًا. تُعالَج الأحداث بترتيب وقت حدوثها الفعلي لا بترتيب وصولها.
    """
    from masar_core.timeutil import parse_datetime

    ordered = sorted(
        events_payload,
        key=lambda item: str(item.get("occurred_at") or ""),
    )
    results: list[dict[str, Any]] = []
    applied = skipped = failed = 0

    for item in ordered:
        action = str(item.get("action") or "").upper()
        client_event_id = item.get("client_event_id")
        try:
            occurred_at = parse_datetime(item["occurred_at"], field="وقت الحدث")
            common = {
                "lat": item.get("lat"), "lon": item.get("lon"),
                "occurred_at": occurred_at,
                "client_event_id": client_event_id,
                "was_offline": True,
            }
            if action == "START_ROUTE":
                start_route(context, item["route_id"], **common)
            elif action == "ARRIVED":
                mark_arrived(context, item["stop_id"], **common)
            elif action == "PICKED_UP":
                mark_picked_up(context, item["stop_id"],
                               piece_count=item.get("piece_count"), **common)
            elif action == "DELIVERED":
                mark_delivered(context, item["stop_id"],
                               receiver_name=item.get("receiver_name"), **common)
            elif action == "EXCEPTION":
                record_exception(
                    context, shipment_id=item["shipment_id"],
                    reason=item["reason"], note=item.get("note"),
                    route_stop_id=item.get("stop_id"),
                    has_proof=bool(item.get("has_proof")), **common)
            else:
                raise ValidationError(f"إجراء غير معروف في المزامنة: {action}")
            applied += 1
            results.append({"client_event_id": client_event_id, "status": "APPLIED"})
        except Conflict as exc:
            # حالة سبق تطبيقها — ليست خطأ في المزامنة
            skipped += 1
            results.append({"client_event_id": client_event_id,
                            "status": "SKIPPED", "message": exc.message})
        except Exception as exc:
            failed += 1
            results.append({"client_event_id": client_event_id,
                            "status": "FAILED", "message": str(exc)})

    return {"applied": applied, "skipped": skipped, "failed": failed, "results": results}
