"""الطلبات الفورية (§7): إنشاء، مراجعة، إدراج ديناميكي، إسناد، إلغاء.

الطلب الفوري لا يدخل التحسين الأسبوعي؛ الخطة الأسبوعية تبقى مرجعًا ثابتًا.
بدلًا من ذلك يُجرَّب إدراجه في جداول السائقين النشطين — ومن موقع السائق
الحالي إن كان قد بدأ العمل (HC-17).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pgwire
from masar_core.config import get_config
from masar_core.constants import AlertType, AuditAction, ShipmentStatus
from masar_core.errors import Conflict, Forbidden, NotFound, ValidationError
from masar_core.state_machine import assert_can_cancel_before_pickup, shipment_sm
from masar_db.driver import SecurityContext, session, transaction
from masar_opt.dynamic import plan_on_demand_insertion
from masar_opt.engine import HubInput, ShipmentInput, VehicleInput, build_problem
from masar_opt.evaluate import RouteEvaluation, evaluate_route
from masar_opt.model import NodeKind, to_datetime
from masar_opt.objective import RoutePlan, Solution

from . import alerts, audit, events, settings as settings_service


def create_request(
    context: SecurityContext,
    *,
    pickup_facility_id: str,
    dropoff_facility_id: str,
    pickup_window_from: dt.datetime,
    pickup_window_to: dt.datetime,
    sla_deadline: dt.datetime,
    service_type: str = "URGENT",
    piece_count: int = 1,
    temperature_mode: str = "AMBIENT",
    sample_types: list[str] | None = None,
    notes: str | None = None,
    contact_name: str | None = None,
    contact_phone: str | None = None,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """ينشئ طلبًا فوريًا بحالة PENDING_APPROVAL."""
    if context.role == "EXTERNAL_REQUESTER":
        if not context.facility_id:
            raise Forbidden("هذا الحساب غير مرتبط بجهة صحية")
        if pickup_facility_id != context.facility_id:
            raise Forbidden("يمكنك إنشاء طلب لجهتك فقط")

    if pickup_window_to < pickup_window_from:
        raise ValidationError("نهاية نافذة الالتقاط قبل بدايتها")
    if sla_deadline <= pickup_window_from:
        raise ValidationError("الموعد النهائي للتسليم قبل بداية نافذة الالتقاط")
    if pickup_facility_id == dropoff_facility_id:
        raise ValidationError("جهة الالتقاط وجهة التسليم متطابقتان")

    with transaction(context) as conn:
        facilities = conn.fetch_all(
            "SELECT id::text AS id, code, name_ar, facility_type, lat, lon, address, "
            "service_minutes, region_id::text AS region_id, city_id::text AS city_id, "
            "default_hub_id::text AS default_hub_id, contact_name, contact_phone "
            "FROM facilities WHERE id = ANY($1::uuid[]) AND is_active",
            [[pickup_facility_id, dropoff_facility_id]],
        )
        by_id = {row["id"]: row for row in facilities}
        pickup = by_id.get(pickup_facility_id)
        dropoff = by_id.get(dropoff_facility_id)
        if pickup is None or dropoff is None:
            raise NotFound("إحدى الجهتين غير مسجّلة أو غير مفعّلة")
        if not pickup["default_hub_id"]:
            raise ValidationError(
                f"الجهة {pickup['name_ar']} بلا مركز انطلاق افتراضي — "
                "حدّده في البيانات الرئيسية"
            )

        service_date = pickup_window_from.astimezone(
            dt.timezone(dt.timedelta(hours=3))).date()
        reference = f"ODR-{dt.datetime.now(dt.timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"

        shipment_id = conn.fetch_value(
            """
            INSERT INTO shipments (
                reference, request_kind, service_type, status,
                region_id, city_id, hub_id,
                pickup_facility_id, pickup_facility_type, pickup_contact_name,
                pickup_contact_phone, pickup_address, pickup_lat, pickup_lon,
                pickup_window_from, pickup_window_to, pickup_service_minutes,
                dropoff_facility_id, dropoff_facility_type, dropoff_contact_name,
                dropoff_contact_phone, dropoff_address, dropoff_lat, dropoff_lon,
                sla_deadline, dropoff_service_minutes,
                piece_count, sample_types, temperature_mode, service_date,
                requested_by, requester_facility_id, notes
            ) VALUES (
                $1,'ON_DEMAND',$2,'PENDING_APPROVAL',
                $3::uuid,$4::uuid,$5::uuid,
                $6::uuid,$7,$8,$9,$10,$11,$12,
                $13::timestamptz,$14::timestamptz,$15,
                $16::uuid,$17,$18,$19,$20,$21,$22,
                $23::timestamptz,$24,
                $25,$26::text[],$27,$28::date,
                $29::uuid,$30::uuid,$31
            ) RETURNING id::text
            """,
            [
                reference, service_type,
                pickup["region_id"], pickup["city_id"], pickup["default_hub_id"],
                pickup["id"], pickup["facility_type"],
                contact_name or pickup["contact_name"],
                contact_phone or pickup["contact_phone"], pickup["address"],
                pickup["lat"], pickup["lon"],
                pickup_window_from, pickup_window_to, pickup["service_minutes"],
                dropoff["id"], dropoff["facility_type"],
                dropoff["contact_name"], dropoff["contact_phone"], dropoff["address"],
                dropoff["lat"], dropoff["lon"],
                sla_deadline, dropoff["service_minutes"],
                piece_count, sample_types or [], temperature_mode, service_date,
                context.user_id, context.facility_id or pickup["id"], notes,
            ],
        )
        alerts.raise_alert(
            conn, AlertType.NEW_ON_DEMAND_REQUEST,
            title_ar="طلب فوري جديد",
            body_ar=(f"طلب فوري {reference} من {pickup['name_ar']} "
                     f"إلى {dropoff['name_ar']} — بانتظار المراجعة"),
            shipment_id=shipment_id, hub_id=pickup["default_hub_id"],
            context_data={"reference": reference},
        )
        audit.record(
            conn, context, AuditAction.MASTER_DATA_CREATE,
            entity_type="shipment", entity_id=shipment_id, entity_label=reference,
            new_value={"request_kind": "ON_DEMAND", "status": "PENDING_APPROVAL"},
            ip_address=ip_address,
        )
        events.publish(
            conn, events.TOPIC_ON_DEMAND,
            {"shipment_id": shipment_id, "reference": reference,
             "status": "PENDING_APPROVAL"},
            hub_id=pickup["default_hub_id"],
        )
    return {"shipment_id": shipment_id, "reference": reference,
            "status": ShipmentStatus.PENDING_APPROVAL}


def review_request(
    context: SecurityContext,
    shipment_id: str,
    *,
    approve: bool,
    reason: str | None = None,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """مراجعة برج التحكم أو المشرف المخوّل (§7 خطوة ٢-٣)."""
    if not approve and not (reason or "").strip():
        raise ValidationError("الرفض يتطلب سببًا مكتوبًا")

    with transaction(context) as conn:
        shipment = conn.fetch_one(
            "SELECT id::text AS id, reference, status, hub_id::text AS hub_id, "
            "requester_facility_id::text AS requester_facility_id "
            "FROM shipments WHERE id = $1::uuid FOR UPDATE",
            [shipment_id],
        )
        if shipment is None:
            raise NotFound("الطلب غير موجود")
        if shipment["status"] != ShipmentStatus.PENDING_APPROVAL:
            raise Conflict(f"الطلب بحالة {shipment['status']} ولا يقبل المراجعة")

        target = (
            ShipmentStatus.PENDING_ASSIGNMENT if approve else ShipmentStatus.REJECTED
        )
        shipment_sm.check(shipment["status"], target, reason=reason)

        if approve:
            conn.execute(
                "UPDATE shipments SET status = $1, approved_by = $2::uuid, "
                "approved_at = now() WHERE id = $3::uuid",
                [target, context.user_id, shipment_id],
            )
        else:
            conn.execute(
                "UPDATE shipments SET status = $1, rejection_reason = $2 "
                "WHERE id = $3::uuid",
                [target, reason, shipment_id],
            )
        audit.record(
            conn, context,
            AuditAction.ON_DEMAND_APPROVE if approve else AuditAction.ON_DEMAND_REJECT,
            entity_type="shipment", entity_id=shipment_id,
            entity_label=shipment["reference"],
            old_value={"status": shipment["status"]}, new_value={"status": target},
            reason=reason, ip_address=ip_address,
        )
        events.publish(
            conn, events.TOPIC_ON_DEMAND,
            {"shipment_id": shipment_id, "reference": shipment["reference"],
             "status": target, "reason": reason},
            hub_id=shipment["hub_id"],
        )
    return {"shipment_id": shipment_id, "status": target}


def insertion_options(context: SecurityContext, shipment_id: str) -> dict[str, Any]:
    """يفحص إمكانية إدراج الطلب في جداول السائقين النشطين دون خرق قيد."""
    cfg = get_config()
    with session(context) as conn:
        shipment = conn.fetch_one(
            """
            SELECT s.id::text AS id, s.reference, s.status, s.hub_id::text AS hub_id,
                   s.service_date, s.pickup_facility_id::text AS pickup_facility_id,
                   s.pickup_facility_type, s.pickup_lat, s.pickup_lon,
                   s.pickup_window_from, s.pickup_window_to, s.pickup_service_minutes,
                   s.dropoff_facility_id::text AS dropoff_facility_id,
                   s.dropoff_facility_type, s.dropoff_lat, s.dropoff_lon,
                   s.dropoff_service_minutes, s.sla_deadline, s.piece_count,
                   s.service_type, s.temperature_mode,
                   pf.name_ar AS pickup_name, df.name_ar AS dropoff_name
            FROM shipments s
            JOIN facilities pf ON pf.id = s.pickup_facility_id
            JOIN facilities df ON df.id = s.dropoff_facility_id
            WHERE s.id = $1::uuid
            """,
            [shipment_id],
        )
        if shipment is None:
            raise NotFound("الطلب غير موجود")
        if shipment["status"] not in (ShipmentStatus.PENDING_ASSIGNMENT,
                                      ShipmentStatus.UNPLANNABLE):
            raise Conflict(
                f"الطلب بحالة {shipment['status']} — يجب اعتماده أولًا")

        hub = conn.fetch_one(
            "SELECT id::text AS id, code, name_ar, lat, lon FROM hubs WHERE id = $1::uuid",
            [shipment["hub_id"]],
        )
        effective = settings_service.effective_for_hub(conn, shipment["hub_id"])

        routes = conn.fetch_all(
            """
            SELECT r.id::text AS id, r.reference, r.status,
                   r.driver_id::text AS driver_id, d.full_name AS driver_name,
                   r.planned_start_at, r.planned_end_at, r.actual_start_at,
                   r.start_lat, r.start_lon,
                   p.lat AS current_lat, p.lon AS current_lon, p.recorded_at
            FROM routes r
            LEFT JOIN drivers d ON d.id = r.driver_id
            LEFT JOIN driver_last_position p ON p.driver_id = r.driver_id
            WHERE r.hub_id = $1::uuid AND r.service_date = $2::date
              AND r.status IN ('ASSIGNED','PUBLISHED','IN_PROGRESS')
            ORDER BY r.sequence_in_day
            """,
            [shipment["hub_id"], shipment["service_date"]],
        )
        stops_by_route: dict[str, list[Any]] = {}
        if routes:
            for row in conn.fetch_all(
                """
                SELECT st.route_id::text AS route_id, st.id::text AS id, st.sequence,
                       st.kind, st.status, st.lat, st.lon, st.label_ar,
                       st.service_minutes, st.window_from, st.window_to,
                       st.shipment_id::text AS shipment_id,
                       s.reference, s.sla_deadline,
                       f.facility_type, f.id::text AS facility_id
                FROM route_stops st
                LEFT JOIN shipments s ON s.id = st.shipment_id
                LEFT JOIN facilities f ON f.id = st.facility_id
                WHERE st.route_id = ANY($1::uuid[]) AND st.kind <> 'HUB_START'
                ORDER BY st.route_id, st.sequence
                """,
                [[route["id"] for route in routes]],
            ):
                stops_by_route.setdefault(row["route_id"], []).append(row)

    if not routes:
        return {
            "shipment_id": shipment_id, "feasible": False, "options": [],
            "rejections": [{
                "rule": "DYN-00",
                "message_ar": "لا توجد رحلات نشطة في هذا المركز اليوم لإدراج الطلب فيها",
            }],
        }

    # بناء مسألة تضم الرحلات الحالية + الطلب الجديد
    hub_input = HubInput(
        hub_id=hub["id"], code=hub["code"], name_ar=hub["name_ar"],
        lat=hub["lat"], lon=hub["lon"],
        opens_at=min(r["planned_start_at"] for r in routes),
        closes_at=max(r["planned_end_at"] for r in routes) + dt.timedelta(hours=6),
    )

    shipment_inputs: list[ShipmentInput] = []
    route_shipment_map: dict[str, list[str]] = {}
    seen: set[str] = set()
    for route in routes:
        for stop in stops_by_route.get(route["id"], []):
            if not stop["shipment_id"] or stop["shipment_id"] in seen:
                continue
            seen.add(stop["shipment_id"])
            route_shipment_map.setdefault(route["id"], []).append(stop["shipment_id"])

    with session(context) as conn:
        existing = conn.fetch_all(
            """
            SELECT s.id::text AS id, s.reference, s.hub_id::text AS hub_id,
                   s.pickup_facility_id::text AS pickup_facility_id,
                   s.pickup_facility_type, s.pickup_lat, s.pickup_lon,
                   s.pickup_window_from, s.pickup_window_to, s.pickup_service_minutes,
                   s.dropoff_facility_id::text AS dropoff_facility_id,
                   s.dropoff_facility_type, s.dropoff_lat, s.dropoff_lon,
                   s.dropoff_service_minutes, s.sla_deadline, s.piece_count,
                   s.service_type, s.temperature_mode, s.status,
                   pf.name_ar AS pickup_name, df.name_ar AS dropoff_name
            FROM shipments s
            JOIN facilities pf ON pf.id = s.pickup_facility_id
            JOIN facilities df ON df.id = s.dropoff_facility_id
            WHERE s.id = ANY($1::uuid[])
            """,
            [list(seen)] if seen else [[]],
        ) if seen else []

    index_of: dict[str, int] = {}
    for row in list(existing) + [shipment]:
        index_of[row["id"]] = len(shipment_inputs)
        shipment_inputs.append(ShipmentInput(
            shipment_id=row["id"], reference=row["reference"], hub_id=hub["id"],
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
            is_on_demand=row["id"] == shipment_id,
        ))

    vehicle_inputs: list[VehicleInput] = []
    for route in routes:
        started = route["actual_start_at"] is not None
        # HC-17: بعد بدء السائق يُحسب المسار من موقعه الحالي
        start_lat = route["current_lat"] if started and route["current_lat"] else None
        start_lon = route["current_lon"] if started and route["current_lon"] else None
        vehicle_inputs.append(VehicleInput(
            hub_id=hub["id"],
            label=f"{route['reference']} — {route['driver_name'] or 'بلا سائق'}",
            earliest_start=(
                route["recorded_at"] if started and route["recorded_at"]
                else route["planned_start_at"]
            ),
            latest_end=route["planned_end_at"] + dt.timedelta(hours=6),
            max_shift_minutes=float(effective["max_shift_hours"]) * 60.0,
            driver_id=route["driver_id"],
            start_lat=start_lat, start_lon=start_lon,
        ))

    problem = build_problem(
        service_date=shipment["service_date"], hubs=[hub_input],
        shipments=shipment_inputs, vehicles=vehicle_inputs,
        effective_settings=effective, fallback_to_estimate=True,
    )

    # إعادة بناء الحل الحالي من المحطات المحفوظة
    solution = Solution(
        routes=[RoutePlan(v.index, [], RouteEvaluation(feasible=True))
                for v in problem.vehicles]
    )
    for position, route in enumerate(routes):
        sequence: list[int] = []
        locked = 0
        for stop in stops_by_route.get(route["id"], []):
            if not stop["shipment_id"]:
                continue
            spec = problem.shipments[index_of[stop["shipment_id"]]]
            node = spec.pickup_node if stop["kind"] == "PICKUP" else spec.delivery_node
            if stop["status"] in ("DONE", "SKIPPED", "FAILED"):
                locked += 1
                continue  # المحطات المنفَّذة تُستبعد من إعادة الترتيب
            sequence.append(node)
            solution.assignment[spec.index] = position
        problem.vehicles[position].locked_prefix = tuple()
        solution.routes[position].sequence = sequence
        solution.routes[position].evaluation = evaluate_route(
            problem, problem.vehicles[position], sequence, stop_on_first_violation=False)

    result = plan_on_demand_insertion(problem, solution, index_of[shipment_id])
    return {
        "shipment_id": shipment_id,
        "reference": shipment["reference"],
        "feasible": result.feasible,
        "options": [
            {
                "route_id": routes[option.route_index]["id"],
                "route_reference": routes[option.route_index]["reference"],
                "driver_id": option.driver_id,
                "driver_name": routes[option.route_index]["driver_name"],
                "route_started": routes[option.route_index]["actual_start_at"] is not None,
                "computed_from": (
                    "الموقع الحالي للسائق"
                    if routes[option.route_index]["actual_start_at"] is not None
                    else "مركز الانطلاق"
                ),
                "added_minutes": round(option.added_minutes, 1),
                "added_km": round(option.added_km, 2),
                "new_end_at": to_datetime(option.new_end_at).isoformat(),
                "pickup_position": option.pickup_position,
                "delivery_position": option.delivery_position,
                "min_slack_minutes": round(min(option.min_slack_minutes, 9999), 1),
            }
            for option in result.options
        ],
        "rejections": [
            {"rule": v.rule, "reason": str(v.reason), "message_ar": v.message_ar}
            for v in result.rejections
        ],
    }



def recompute_route_timeline(conn: pgwire.Connection, route_id: str) -> int:
    """يعيد حساب الجدول الزمني لرحلة ويكتبه على محطاتها وشحناتها.

    يُستدعى بعد الإدراج الديناميكي: المحطات الجديدة تُكتب بلا أزمنة مخططة،
    والمحطات التالية لها تتغيّر أزمنتها فعليًا. بلا هذه الخطوة تبقى الشحنة
    الفورية **بلا وقت تسليم مخطط**، فلا يُقاس تأخرها ولا تظهر في مقارنة الخطة
    بالتنفيذ — أي أن الطلب الفوري يدخل التشغيل خارج الرقابة.

    الحساب يمر بـ ``evaluate_route`` نفسه لا بمنطق موازٍ، فلا يمكن أن يختلف
    عن المرجع الذي يحكم شرعية الرحلة.
    """
    route = conn.fetch_one(
        "SELECT r.id::text AS id, r.hub_id::text AS hub_id, r.service_date, "
        "r.planned_start_at, r.actual_start_at, h.lat AS hub_lat, h.lon AS hub_lon, "
        "h.code AS hub_code, h.name_ar AS hub_name "
        "FROM routes r JOIN hubs h ON h.id = r.hub_id WHERE r.id = $1::uuid",
        [route_id],
    )
    if route is None:
        return 0

    stops = conn.fetch_all(
        "SELECT st.id::text AS id, st.sequence, st.kind, st.shipment_id::text AS shipment_id, "
        "st.service_minutes, s.pickup_facility_id::text AS pickup_facility_id, "
        "s.pickup_facility_type, s.pickup_lat, s.pickup_lon, s.pickup_window_from, "
        "s.pickup_window_to, s.pickup_service_minutes, "
        "s.dropoff_facility_id::text AS dropoff_facility_id, s.dropoff_facility_type, "
        "s.dropoff_lat, s.dropoff_lon, s.dropoff_service_minutes, s.sla_deadline, "
        "s.reference, s.piece_count, s.service_type, s.temperature_mode "
        "FROM route_stops st LEFT JOIN shipments s ON s.id = st.shipment_id "
        "WHERE st.route_id = $1::uuid ORDER BY st.sequence",
        [route_id],
    )
    ordered = [stop for stop in stops if stop["shipment_id"]]
    if not ordered:
        return 0

    effective = settings_service.effective_for_hub(conn, route["hub_id"])
    start_at = route["actual_start_at"] or route["planned_start_at"]

    seen: dict[str, Any] = {}
    shipment_inputs: list[ShipmentInput] = []
    for stop in ordered:
        if stop["shipment_id"] in seen:
            continue
        seen[stop["shipment_id"]] = len(shipment_inputs)
        shipment_inputs.append(ShipmentInput(
            shipment_id=stop["shipment_id"], reference=stop["reference"],
            hub_id=route["hub_id"],
            pickup_facility_id=stop["pickup_facility_id"],
            pickup_facility_type=stop["pickup_facility_type"],
            pickup_name=stop["reference"],
            pickup_lat=stop["pickup_lat"], pickup_lon=stop["pickup_lon"],
            pickup_window_from=stop["pickup_window_from"],
            pickup_window_to=stop["pickup_window_to"],
            pickup_service_minutes=float(stop["pickup_service_minutes"]),
            dropoff_facility_id=stop["dropoff_facility_id"],
            dropoff_facility_type=stop["dropoff_facility_type"],
            dropoff_name=stop["reference"],
            dropoff_lat=stop["dropoff_lat"], dropoff_lon=stop["dropoff_lon"],
            dropoff_service_minutes=float(stop["dropoff_service_minutes"]),
            sla_deadline=stop["sla_deadline"],
            piece_count=int(stop["piece_count"] or 1),
            service_type=stop["service_type"], temperature_mode=stop["temperature_mode"],
            is_on_demand=True,
        ))

    hub_input = HubInput(
        hub_id=route["hub_id"], code=route["hub_code"], name_ar=route["hub_name"],
        lat=route["hub_lat"], lon=route["hub_lon"],
        opens_at=start_at, closes_at=start_at + dt.timedelta(hours=24),
    )
    vehicle_input = VehicleInput(
        hub_id=route["hub_id"], label="إعادة حساب",
        earliest_start=start_at, latest_end=start_at + dt.timedelta(hours=24),
        max_shift_minutes=float(effective["max_shift_hours"]) * 60.0 * 4,
    )
    problem = build_problem(
        service_date=route["service_date"], hubs=[hub_input],
        shipments=shipment_inputs, vehicles=[vehicle_input],
        effective_settings=effective, fallback_to_estimate=True,
    )

    sequence: list[int] = []
    for stop in ordered:
        spec = problem.shipments[seen[stop["shipment_id"]]]
        sequence.append(spec.pickup_node if stop["kind"] == "PICKUP"
                        else spec.delivery_node)

    evaluation = evaluate_route(problem, problem.vehicles[0], sequence,
                                stop_on_first_violation=False)
    if not evaluation.timings:
        return 0

    updated = 0
    for stop, timing in zip(ordered, evaluation.timings):
        arrival = to_datetime(timing.arrival)
        departure = to_datetime(timing.departure)
        conn.execute(
            "UPDATE route_stops SET planned_arrival_at = $1::timestamptz, "
            "planned_service_start = $1::timestamptz, "
            "planned_departure_at = $2::timestamptz WHERE id = $3::uuid",
            [arrival, departure, stop["id"]],
        )
        column = ("planned_pickup_arrival" if stop["kind"] == "PICKUP"
                  else "planned_dropoff_arrival")
        conn.execute(
            f"UPDATE shipments SET {column} = $1::timestamptz WHERE id = $2::uuid",
            [arrival, stop["shipment_id"]],
        )
        updated += 1
    return updated


def assign_on_demand(
    context: SecurityContext,
    shipment_id: str,
    *,
    route_id: str,
    pickup_position: int,
    delivery_position: int,
    reason: str | None = None,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """يدرج الطلب الفوري في رحلة قائمة ويصل للسائق فورًا (§7 خطوات ٥-٦)."""
    with transaction(context) as conn:
        shipment = conn.fetch_one(
            "SELECT id::text AS id, reference, status, hub_id::text AS hub_id, "
            "pickup_facility_id::text AS pickup_facility_id, pickup_lat, pickup_lon, "
            "pickup_window_from, pickup_window_to, pickup_service_minutes, "
            "dropoff_facility_id::text AS dropoff_facility_id, dropoff_lat, dropoff_lon, "
            "dropoff_service_minutes, sla_deadline FROM shipments "
            "WHERE id = $1::uuid FOR UPDATE",
            [shipment_id],
        )
        if shipment is None:
            raise NotFound("الطلب غير موجود")
        if shipment["status"] not in (ShipmentStatus.PENDING_ASSIGNMENT,
                                      ShipmentStatus.UNPLANNABLE):
            raise Conflict(f"الطلب بحالة {shipment['status']} ولا يقبل الإسناد")

        route = conn.fetch_one(
            "SELECT id::text AS id, reference, hub_id::text AS hub_id, status, "
            "driver_id::text AS driver_id, vehicle_id::text AS vehicle_id, "
            "box_id::text AS box_id, service_date FROM routes WHERE id = $1::uuid FOR UPDATE",
            [route_id],
        )
        if route is None:
            raise NotFound("الرحلة غير موجودة")
        context.require_hub(route["hub_id"])
        if route["status"] not in ("ASSIGNED", "PUBLISHED", "IN_PROGRESS"):
            raise Conflict(f"الرحلة بحالة {route['status']} ولا تقبل الإدراج")
        if route["driver_id"] is None:
            raise Conflict("لا يمكن الإدراج في رحلة بلا سائق")

        pickup_names = conn.fetch_one(
            "SELECT (SELECT name_ar FROM facilities WHERE id = $1::uuid) AS pickup_name, "
            "(SELECT name_ar FROM facilities WHERE id = $2::uuid) AS dropoff_name",
            [shipment["pickup_facility_id"], shipment["dropoff_facility_id"]],
        )

        # إزاحة تسلسل المحطات لإفساح المواضع الجديدة
        conn.execute(
            "UPDATE route_stops SET sequence = sequence + 1000 WHERE route_id = $1::uuid "
            "AND sequence >= $2",
            [route_id, pickup_position],
        )
        conn.execute(
            "INSERT INTO route_stops (route_id, sequence, kind, facility_id, shipment_id, "
            "lat, lon, label_ar, window_from, window_to, service_minutes) "
            "VALUES ($1::uuid,$2,'PICKUP',$3::uuid,$4::uuid,$5,$6,$7,"
            "$8::timestamptz,$9::timestamptz,$10)",
            [route_id, pickup_position, shipment["pickup_facility_id"], shipment_id,
             shipment["pickup_lat"], shipment["pickup_lon"],
             f"التقاط (فوري): {pickup_names['pickup_name']}",
             shipment["pickup_window_from"], shipment["pickup_window_to"],
             shipment["pickup_service_minutes"]],
        )
        conn.execute(
            "INSERT INTO route_stops (route_id, sequence, kind, facility_id, shipment_id, "
            "lat, lon, label_ar, window_to, service_minutes) "
            "VALUES ($1::uuid,$2,'DELIVERY',$3::uuid,$4::uuid,$5,$6,$7,"
            "$8::timestamptz,$9)",
            [route_id, delivery_position, shipment["dropoff_facility_id"], shipment_id,
             shipment["dropoff_lat"], shipment["dropoff_lon"],
             f"تسليم (فوري): {pickup_names['dropoff_name']}",
             shipment["sla_deadline"], shipment["dropoff_service_minutes"]],
        )
        # إعادة ترقيم متسلسل
        conn.execute(
            """
            WITH ordered AS (
                SELECT id, row_number() OVER (ORDER BY sequence, created_at) - 1 AS position
                FROM route_stops WHERE route_id = $1::uuid
            )
            UPDATE route_stops SET sequence = ordered.position
            FROM ordered WHERE route_stops.id = ordered.id
            """,
            [route_id],
        )

        violations = conn.fetch_all(
            "SELECT rule_code, detail_ar FROM app.verify_route_feasibility($1::uuid)",
            [route_id],
        )
        if violations:
            raise Conflict(
                "الإدراج يخرق قيودًا صلبة: "
                + "؛ ".join(f"{v['rule_code']}: {v['detail_ar']}" for v in violations),
                violations=[dict(v) for v in violations],
            )

        shipment_sm.check(shipment["status"], ShipmentStatus.PLANNED)
        conn.execute(
            "UPDATE shipments SET status = 'PLANNED', route_id = $1::uuid, "
            "driver_id = $2::uuid, vehicle_id = $3::uuid, box_id = $4::uuid "
            "WHERE id = $5::uuid",
            [route_id, route["driver_id"], route["vehicle_id"], route["box_id"], shipment_id],
        )
        # الشحنة تلحق بحالة الرحلة التي أُدرجت فيها. الإدراج في رحلة **جارية**
        # يجب أن يصل بها إلى IN_PROGRESS: بلوغ PUBLISHED فقط كان يتركها عالقة —
        # لا يوجد انتقال PUBLISHED ← ARRIVED_PICKUP لأن المرور بـ IN_PROGRESS
        # يحدث عند بدء الرحلة، وهو حدث وقع قبل أن تُدرج هذه الشحنة. النتيجة
        # كانت محطة لا يستطيع السائق تنفيذها **وتحجب كل ما بعدها** لأن الترتيب
        # مُلزم — أي أن الطلب الفوري كان يشلّ الرحلة التي أُدرج فيها.
        target = (
            ShipmentStatus.IN_PROGRESS if route["status"] == "IN_PROGRESS"
            else ShipmentStatus.PUBLISHED if route["status"] == "PUBLISHED"
            else ShipmentStatus.ASSIGNED
        )
        # التدرّج خطوةً خطوة كي تمر الشحنة بكل انتقال مسموح ويوثَّق كل منها
        conn.execute(
            "UPDATE shipments SET status = 'ASSIGNED' WHERE id = $1::uuid", [shipment_id])
        if target in (ShipmentStatus.PUBLISHED, ShipmentStatus.IN_PROGRESS):
            conn.execute(
                "UPDATE shipments SET status = 'PUBLISHED' WHERE id = $1::uuid",
                [shipment_id])
        if target == ShipmentStatus.IN_PROGRESS:
            conn.execute(
                "UPDATE shipments SET status = 'IN_PROGRESS' WHERE id = $1::uuid",
                [shipment_id])

        conn.execute(
            "UPDATE routes SET shipment_count = shipment_count + 1, "
            "pickup_count = pickup_count + 1, delivery_count = delivery_count + 1 "
            "WHERE id = $1::uuid",
            [route_id],
        )

        # إعادة حساب الجدول الزمني: الإدراج يغيّر أزمنة كل ما بعده فعليًا،
        # وتركها كما كانت يجعل الخطة المعروضة تخالف الخطة المنفَّذة.
        recompute_route_timeline(conn, route_id)
        audit.record(
            conn, context, AuditAction.ROUTE_ASSIGN,
            entity_type="shipment", entity_id=shipment_id,
            entity_label=shipment["reference"],
            new_value={"route_id": route_id, "driver_id": route["driver_id"],
                       "kind": "ON_DEMAND_INSERTION"},
            reason=reason, ip_address=ip_address,
        )
        events.publish(
            conn, events.TOPIC_ON_DEMAND,
            {"shipment_id": shipment_id, "reference": shipment["reference"],
             "status": target, "route_id": route_id, "action": "INSERTED"},
            hub_id=route["hub_id"], driver_id=route["driver_id"],
        )
    return {"shipment_id": shipment_id, "route_id": route_id, "status": target}


def cancel_request(
    context: SecurityContext,
    shipment_id: str,
    reason: str,
    *,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """إلغاء قبل الالتقاط — يظهر لدى السائق تلقائيًا (§7 خطوة ٩)."""
    with transaction(context) as conn:
        shipment = conn.fetch_one(
            "SELECT id::text AS id, reference, status, hub_id::text AS hub_id, "
            "driver_id::text AS driver_id, route_id::text AS route_id, "
            "requester_facility_id::text AS requester_facility_id, actual_pickup_at "
            "FROM shipments WHERE id = $1::uuid FOR UPDATE",
            [shipment_id],
        )
        if shipment is None:
            raise NotFound("الطلب غير موجود")

        if context.role == "EXTERNAL_REQUESTER":
            if shipment["requester_facility_id"] != context.facility_id:
                raise Forbidden("لا يمكنك إلغاء طلب جهة أخرى")

        assert_can_cancel_before_pickup(
            ShipmentStatus.PICKED_UP if shipment["actual_pickup_at"]
            else shipment["status"]
        )
        shipment_sm.check(
            shipment["status"], ShipmentStatus.CANCELLED_BEFORE_PICKUP, reason=reason)

        conn.execute(
            "UPDATE shipments SET status = 'CANCELLED_BEFORE_PICKUP', cancel_reason = $1 "
            "WHERE id = $2::uuid",
            [reason, shipment_id],
        )
        conn.execute(
            "UPDATE route_stops SET status = 'SKIPPED' WHERE shipment_id = $1::uuid "
            "AND status = 'PENDING'",
            [shipment_id],
        )
        alerts.raise_alert(
            conn, AlertType.REQUEST_CANCELLED,
            title_ar="إلغاء طلب قبل الالتقاط",
            body_ar=f"أُلغي الطلب {shipment['reference']} — السبب: {reason}",
            shipment_id=shipment_id, route_id=shipment["route_id"],
            hub_id=shipment["hub_id"], driver_id=shipment["driver_id"],
        )
        audit.record(
            conn, context, AuditAction.SHIPMENT_CANCEL,
            entity_type="shipment", entity_id=shipment_id,
            entity_label=shipment["reference"],
            old_value={"status": shipment["status"]},
            new_value={"status": "CANCELLED_BEFORE_PICKUP"},
            reason=reason, ip_address=ip_address,
        )
        events.publish(
            conn, events.TOPIC_SHIPMENT,
            {"shipment_id": shipment_id, "reference": shipment["reference"],
             "status": "CANCELLED_BEFORE_PICKUP", "reason": reason},
            hub_id=shipment["hub_id"], driver_id=shipment["driver_id"],
        )
    return {"shipment_id": shipment_id, "status": ShipmentStatus.CANCELLED_BEFORE_PICKUP}


def list_requests(
    context: SecurityContext,
    *,
    status: str | None = None,
    hub_id: str | None = None,
    limit: int = 100,
) -> list[Any]:
    clauses = ["s.request_kind = 'ON_DEMAND'"]
    params: list[Any] = []
    if status:
        params.append(status)
        clauses.append(f"s.status = ${len(params)}")
    if hub_id:
        params.append(hub_id)
        clauses.append(f"s.hub_id = ${len(params)}::uuid")

    with session(context) as conn:
        return conn.fetch_all(
            "SELECT s.id::text AS id, s.reference, s.status, s.service_type, "
            "s.service_date, s.pickup_window_from, s.pickup_window_to, s.sla_deadline, "
            "s.piece_count, s.temperature_mode, s.notes, s.rejection_reason, "
            "s.cancel_reason, s.created_at, s.route_id::text AS route_id, "
            "s.driver_id::text AS driver_id, d.full_name AS driver_name, "
            "pf.name_ar AS pickup_name, df.name_ar AS dropoff_name, "
            "h.name_ar AS hub_name_ar, r.reference AS route_reference "
            "FROM shipments s "
            # LEFT JOIN مقصود: مرجع الرؤية هو سياسة ``shipments_read`` على صف
            # الشحنة نفسه. الربط الداخلي بجدول لا يراه الدور (مقدم الطلب
            # الخارجي لا يرى ``hubs``) كان **يُسقط طلبه هو** من قائمته بصمت —
            # لا رفضًا معلنًا بل اختفاءً، وهو أسوأ أنواع الفشل.
            "LEFT JOIN facilities pf ON pf.id = s.pickup_facility_id "
            "LEFT JOIN facilities df ON df.id = s.dropoff_facility_id "
            "LEFT JOIN hubs h ON h.id = s.hub_id "
            "LEFT JOIN drivers d ON d.id = s.driver_id "
            "LEFT JOIN routes r ON r.id = s.route_id "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY s.created_at DESC LIMIT {int(limit)}",
            params,
        )
