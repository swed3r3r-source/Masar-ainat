"""الإسناد والنشر وتعديل الرحلات المنشورة (§16 / §17)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pgwire
from masar_core.constants import AlertType, AuditAction, RouteStatus, ShipmentStatus, Severity
from masar_core.errors import Conflict, NotFound, ValidationError
from masar_core.timeutil import haversine_km
from masar_db.driver import SecurityContext, session, transaction

from . import alerts, audit, events, settings as settings_service


# ------------------------------------------------------ اقتراح السائقين ----

def suggest_drivers(context: SecurityContext, route_id: str) -> dict[str, Any]:
    """يقترح السائقين المناسبين مع سبب قبول أو رفض كل واحد (§16)."""
    with session(context) as conn:
        route = conn.fetch_one(
            "SELECT r.id::text AS id, r.hub_id::text AS hub_id, r.service_date, "
            "r.planned_start_at, r.planned_end_at, r.is_long_haul, r.status, "
            "r.working_minutes, r.start_lat, r.start_lon, r.end_lat, r.end_lon "
            "FROM routes r WHERE r.id = $1::uuid",
            [route_id],
        )
        if route is None:
            raise NotFound("الرحلة غير موجودة")
        context.require_hub(route["hub_id"])

        effective = settings_service.effective_for_hub(conn, route["hub_id"])
        max_long_haul = int(effective["max_long_haul_per_driver_per_day"])
        max_routes = int(effective["max_routes_per_driver_per_day"])
        max_shift_minutes = float(effective["max_shift_hours"]) * 60.0

        drivers = conn.fetch_all(
            """
            SELECT d.id::text AS id, d.code, d.full_name, d.phone,
                   d.employment_status, d.license_expiry, d.shift_start, d.shift_end,
                   coalesce(load.route_count, 0)      AS assigned_routes,
                   coalesce(load.long_haul_count, 0)  AS assigned_long_haul,
                   coalesce(load.working_minutes, 0)  AS assigned_minutes,
                   load.last_end_at, load.last_end_lat, load.last_end_lon,
                   coalesce(week.week_minutes, 0)     AS week_minutes,
                   coalesce(week.week_routes, 0)      AS week_routes
            FROM drivers d
            LEFT JOIN (
                SELECT driver_id,
                       count(*) AS route_count,
                       count(*) FILTER (WHERE is_long_haul) AS long_haul_count,
                       sum(working_minutes) AS working_minutes,
                       max(planned_end_at) AS last_end_at,
                       (array_agg(end_lat ORDER BY planned_end_at DESC))[1] AS last_end_lat,
                       (array_agg(end_lon ORDER BY planned_end_at DESC))[1] AS last_end_lon
                FROM routes
                WHERE service_date = $2::date
                  AND status IN ('ASSIGNED','PUBLISHED','IN_PROGRESS','COMPLETED')
                  AND id <> $3::uuid
                GROUP BY driver_id
            ) load ON load.driver_id = d.id
            LEFT JOIN (
                SELECT driver_id, sum(working_minutes) AS week_minutes,
                       count(*) AS week_routes
                FROM routes
                WHERE service_date BETWEEN $2::date - 6 AND $2::date
                  AND status IN ('ASSIGNED','PUBLISHED','IN_PROGRESS','COMPLETED')
                GROUP BY driver_id
            ) week ON week.driver_id = d.id
            WHERE d.hub_id = $1::uuid AND d.is_active
            ORDER BY d.code
            """,
            [route["hub_id"], route["service_date"], route_id],
        )

        unavailable = {
            row["entity_id"]
            for row in conn.fetch_all(
                "SELECT entity_id::text AS entity_id FROM availability_exceptions "
                "WHERE entity_type = 'DRIVER' AND NOT is_available "
                "AND $1::date BETWEEN from_date AND to_date",
                [route["service_date"]],
            )
        }
        conflicts = {
            row["driver_id"]: row["reference"]
            for row in conn.fetch_all(
                "SELECT driver_id::text AS driver_id, reference FROM routes "
                "WHERE service_date = $1::date AND id <> $2::uuid "
                "AND driver_id IS NOT NULL "
                "AND status IN ('ASSIGNED','PUBLISHED','IN_PROGRESS') "
                "AND active_window && app.route_time_range($3::timestamptz, $4::timestamptz)",
                [route["service_date"], route_id,
                 route["planned_start_at"], route["planned_end_at"]],
            )
        }

    candidates: list[dict[str, Any]] = []
    for driver in drivers:
        blockers: list[str] = []
        notes: list[str] = []

        if driver["employment_status"] != "ACTIVE":
            blockers.append(f"حالة التوظيف: {driver['employment_status']}")
        if driver["id"] in unavailable:
            blockers.append("مسجّل كغير متاح في هذا اليوم (إجازة/استثناء)")
        if driver["license_expiry"] and driver["license_expiry"] < route["service_date"]:
            blockers.append(f"الرخصة منتهية بتاريخ {driver['license_expiry']}")
        if driver["id"] in conflicts:
            blockers.append(f"تعارض زمني مع الرحلة {conflicts[driver['id']]}")
        if route["is_long_haul"] and int(driver["assigned_long_haul"]) >= max_long_haul:
            blockers.append(
                f"لديه {driver['assigned_long_haul']} رحلة بعيدة اليوم "
                f"والحد {max_long_haul} (قيد HC-15)"
            )
        if int(driver["assigned_routes"]) >= max_routes:
            blockers.append(f"بلغ الحد الأقصى للرحلات اليومية ({max_routes})")

        total_minutes = float(driver["assigned_minutes"] or 0) + float(
            route["working_minutes"] or 0)
        if total_minutes > max_shift_minutes:
            blockers.append(
                f"مجموع العمل {total_minutes / 60:.1f} ساعة يتجاوز حد الوردية "
                f"{max_shift_minutes / 60:.1f} ساعة"
            )

        chain_km = None
        if driver["last_end_lat"] is not None and route["start_lat"] is not None:
            chain_km = round(haversine_km(
                float(driver["last_end_lat"]), float(driver["last_end_lon"]),
                float(route["start_lat"]), float(route["start_lon"])), 1)
            notes.append(
                f"ستبدأ الرحلة على بعد {chain_km} كم من نهاية رحلته السابقة"
            )

        candidates.append({
            "driver_id": driver["id"],
            "code": driver["code"],
            "full_name": driver["full_name"],
            "phone": driver["phone"],
            "eligible": not blockers,
            "blockers": blockers,
            "notes": notes,
            "assigned_routes_today": int(driver["assigned_routes"]),
            "assigned_long_haul_today": int(driver["assigned_long_haul"]),
            "assigned_minutes_today": round(float(driver["assigned_minutes"] or 0), 1),
            "week_minutes": round(float(driver["week_minutes"] or 0), 1),
            "week_routes": int(driver["week_routes"] or 0),
            "chain_distance_km": chain_km,
        })

    # ترتيب العدالة: الأقل عملًا أسبوعيًا أولًا (§14)
    candidates.sort(key=lambda c: (not c["eligible"], c["week_minutes"], c["code"]))
    return {"route_id": route_id, "candidates": candidates}


# ------------------------------------------------------------- الإسناد ----

def assign_route(
    context: SecurityContext,
    route_id: str,
    *,
    driver_id: str,
    vehicle_id: str | None = None,
    box_id: str | None = None,
    reason: str | None = None,
    force: bool = False,
    ip_address: str | None = None,
) -> dict[str, Any]:
    with transaction(context) as conn:
        route = conn.fetch_one(
            "SELECT id::text AS id, reference, hub_id::text AS hub_id, service_date, "
            "status, driver_id::text AS driver_id, planned_start_at, planned_end_at, "
            "is_long_haul, working_minutes FROM routes WHERE id = $1::uuid FOR UPDATE",
            [route_id],
        )
        if route is None:
            raise NotFound("الرحلة غير موجودة")
        context.require_hub(route["hub_id"])
        if route["status"] not in (RouteStatus.PLANNED, RouteStatus.ASSIGNED,
                                   RouteStatus.PUBLISHED):
            raise Conflict(f"لا يمكن الإسناد ورحلة حالتها {route['status']}")

        was_published = route["status"] == RouteStatus.PUBLISHED
        if was_published and not (reason or "").strip():
            raise ValidationError(
                "تعديل رحلة منشورة يتطلب سببًا مكتوبًا (§17)"
            )

        driver = conn.fetch_one(
            "SELECT id::text AS id, full_name, hub_id::text AS hub_id, is_active, "
            "employment_status, license_expiry FROM drivers WHERE id = $1::uuid",
            [driver_id],
        )
        if driver is None:
            raise NotFound("السائق غير موجود")
        if driver["hub_id"] != route["hub_id"]:
            raise ValidationError("السائق تابع لمركز انطلاق مختلف")

        blockers = _assignment_blockers(conn, route, driver, route_id)
        if blockers and not force:
            raise Conflict(
                "تعذر الإسناد: " + "؛ ".join(blockers), blockers=blockers
            )

        if vehicle_id:
            vehicle = conn.fetch_one(
                "SELECT id::text AS id, hub_id::text AS hub_id, status, is_active "
                "FROM vehicles WHERE id = $1::uuid",
                [vehicle_id],
            )
            if vehicle is None or not vehicle["is_active"]:
                raise NotFound("المركبة غير موجودة أو غير مفعّلة")
            if vehicle["hub_id"] != route["hub_id"]:
                raise ValidationError("المركبة تابعة لمركز انطلاق مختلف")
            if vehicle["status"] in ("MAINTENANCE", "OUT_OF_SERVICE"):
                raise Conflict(f"المركبة في حالة {vehicle['status']}")

        # سلسلة الرحلات: تبدأ الرحلة التالية من نهاية السابقة (HC-10)
        previous = conn.fetch_one(
            "SELECT id::text AS id, end_lat, end_lon, planned_end_at FROM routes "
            "WHERE driver_id = $1::uuid AND service_date = $2::date AND id <> $3::uuid "
            "AND status IN ('ASSIGNED','PUBLISHED','IN_PROGRESS','COMPLETED') "
            "AND planned_end_at <= $4::timestamptz "
            "ORDER BY planned_end_at DESC LIMIT 1",
            [driver_id, route["service_date"], route_id, route["planned_start_at"]],
        )

        try:
            conn.execute(
                "UPDATE routes SET driver_id = $1::uuid, vehicle_id = $2::uuid, "
                "box_id = $3::uuid, assigned_by = $4::uuid, assigned_at = now(), "
                "previous_route_id = $5::uuid, "
                "start_node_kind = CASE WHEN $5::uuid IS NULL THEN 'HUB' "
                "  ELSE 'PREVIOUS_ROUTE_END' END, "
                "status = CASE WHEN status = 'PLANNED' THEN 'ASSIGNED' ELSE status END "
                "WHERE id = $6::uuid",
                [driver_id, vehicle_id, box_id, context.user_id,
                 previous["id"] if previous else None, route_id],
            )
        except pgwire.PgError as exc:
            if "routes_driver_no_overlap" in str(exc):
                raise Conflict(
                    "تعارض إسناد: للسائق رحلة أخرى متداخلة زمنيًا في هذا اليوم"
                ) from exc
            if "routes_vehicle_no_overlap" in str(exc):
                raise Conflict("المركبة مسندة لرحلة أخرى متداخلة زمنيًا") from exc
            raise

        conn.execute(
            "UPDATE shipments SET driver_id = $1::uuid, vehicle_id = $2::uuid, "
            "box_id = $3::uuid, status = CASE WHEN status = 'PLANNED' THEN 'ASSIGNED' "
            "ELSE status END WHERE route_id = $4::uuid AND status IN "
            "('PLANNED','ASSIGNED','PENDING_ASSIGNMENT')",
            [driver_id, vehicle_id, box_id, route_id],
        )

        audit.record(
            conn, context, AuditAction.ROUTE_ASSIGN,
            entity_type="route", entity_id=route_id, entity_label=route["reference"],
            old_value={"driver_id": route["driver_id"]},
            new_value={"driver_id": driver_id, "vehicle_id": vehicle_id,
                       "box_id": box_id, "forced": force,
                       "blockers_overridden": blockers if force else []},
            reason=reason, ip_address=ip_address,
        )
        events.publish(
            conn, events.TOPIC_ROUTE,
            {"route_id": route_id, "reference": route["reference"],
             "action": "ASSIGNED", "driver_id": driver_id},
            hub_id=route["hub_id"], driver_id=driver_id,
        )

        if was_published:
            _record_revision(
                conn, context, route_id,
                change_kind="REASSIGN_DRIVER", reason=reason or "",
                before={"driver_id": route["driver_id"]},
                after={"driver_id": driver_id},
            )
            alerts.raise_alert(
                conn, AlertType.PUBLISHED_ROUTE_MODIFIED,
                title_ar="تعديل على رحلة منشورة",
                body_ar=f"أُسندت الرحلة {route['reference']} لسائق جديد بعد النشر",
                route_id=route_id, hub_id=route["hub_id"], driver_id=driver_id,
                context_data={"reason": reason},
            )

    return {"route_id": route_id, "driver_id": driver_id,
            "overridden_blockers": blockers if force else []}


def _assignment_blockers(
    conn: pgwire.Connection, route: Any, driver: Any, route_id: str
) -> list[str]:
    blockers: list[str] = []
    effective = settings_service.effective_for_hub(conn, route["hub_id"])

    if not driver["is_active"] or driver["employment_status"] != "ACTIVE":
        blockers.append("السائق غير مفعّل أو حالة توظيفه لا تسمح")
    if driver["license_expiry"] and driver["license_expiry"] < route["service_date"]:
        blockers.append("رخصة السائق منتهية")

    unavailable = conn.fetch_value(
        "SELECT count(*) FROM availability_exceptions WHERE entity_type = 'DRIVER' "
        "AND entity_id = $1::uuid AND NOT is_available "
        "AND $2::date BETWEEN from_date AND to_date",
        [driver["id"], route["service_date"]],
    )
    if unavailable:
        blockers.append("السائق مسجّل كغير متاح في هذا اليوم")

    overlap = conn.fetch_one(
        "SELECT reference FROM routes WHERE driver_id = $1::uuid AND id <> $2::uuid "
        "AND status IN ('ASSIGNED','PUBLISHED','IN_PROGRESS') "
        "AND active_window && app.route_time_range($3::timestamptz, $4::timestamptz) LIMIT 1",
        [driver["id"], route_id, route["planned_start_at"], route["planned_end_at"]],
    )
    if overlap:
        blockers.append(f"تعارض زمني مع الرحلة {overlap['reference']}")

    if route["is_long_haul"]:
        long_haul = conn.fetch_value(
            "SELECT count(*) FROM routes WHERE driver_id = $1::uuid "
            "AND service_date = $2::date AND id <> $3::uuid AND is_long_haul "
            "AND status IN ('ASSIGNED','PUBLISHED','IN_PROGRESS','COMPLETED')",
            [driver["id"], route["service_date"], route_id],
        )
        limit = int(effective["max_long_haul_per_driver_per_day"])
        if int(long_haul or 0) >= limit:
            blockers.append(
                f"قيد HC-15: للسائق {long_haul} رحلة بعيدة اليوم والحد {limit}"
            )

    total = conn.fetch_value(
        "SELECT coalesce(sum(working_minutes), 0) FROM routes WHERE driver_id = $1::uuid "
        "AND service_date = $2::date AND id <> $3::uuid "
        "AND status IN ('ASSIGNED','PUBLISHED','IN_PROGRESS','COMPLETED')",
        [driver["id"], route["service_date"], route_id],
    )
    combined = float(total or 0) + float(route["working_minutes"] or 0)
    max_minutes = float(effective["max_shift_hours"]) * 60.0
    if combined > max_minutes:
        blockers.append(
            f"قيد HC-05: مجموع العمل {combined / 60:.1f} ساعة يتجاوز الحد "
            f"{max_minutes / 60:.1f} ساعة"
        )
    return blockers


def unassign_route(
    context: SecurityContext, route_id: str, reason: str, *, ip_address: str | None = None
) -> dict[str, Any]:
    """إزالة السائق: تعود الرحلة والشحنات إلى PENDING_ASSIGNMENT (§16)."""
    with transaction(context) as conn:
        route = conn.fetch_one(
            "SELECT id::text AS id, reference, hub_id::text AS hub_id, status, "
            "driver_id::text AS driver_id FROM routes WHERE id = $1::uuid FOR UPDATE",
            [route_id],
        )
        if route is None:
            raise NotFound("الرحلة غير موجودة")
        context.require_hub(route["hub_id"])
        if route["driver_id"] is None:
            raise Conflict("لا يوجد سائق مسند لهذه الرحلة")
        if route["status"] == RouteStatus.IN_PROGRESS:
            raise Conflict(
                "لا يمكن إزالة السائق من رحلة جارية — أنهها أو سجّل استثناء أولًا")

        previous_driver = route["driver_id"]
        new_status = RouteStatus.PLANNED
        conn.execute(
            "UPDATE routes SET driver_id = NULL, vehicle_id = NULL, box_id = NULL, "
            "status = $1, assigned_at = NULL, previous_route_id = NULL, "
            "start_node_kind = 'HUB' WHERE id = $2::uuid",
            [new_status, route_id],
        )
        conn.execute(
            "UPDATE shipments SET driver_id = NULL, vehicle_id = NULL, box_id = NULL, "
            "status = 'PENDING_ASSIGNMENT' WHERE route_id = $1::uuid "
            "AND status IN ('ASSIGNED','PUBLISHED','PLANNED')",
            [route_id],
        )
        audit.record(
            conn, context, AuditAction.ROUTE_UNASSIGN,
            entity_type="route", entity_id=route_id, entity_label=route["reference"],
            old_value={"driver_id": previous_driver, "status": route["status"]},
            new_value={"driver_id": None, "status": new_status},
            reason=reason, ip_address=ip_address,
        )
        events.publish(
            conn, events.TOPIC_ROUTE,
            {"route_id": route_id, "action": "UNASSIGNED", "reason": reason},
            hub_id=route["hub_id"], driver_id=previous_driver,
        )
        alerts.raise_alert(
            conn, AlertType.ROUTE_WITHOUT_DRIVER,
            title_ar="رحلة بلا سائق",
            body_ar=f"أُزيل السائق من الرحلة {route['reference']} — السبب: {reason}",
            route_id=route_id, hub_id=route["hub_id"],
            context_data={"previous_driver_id": previous_driver},
        )
    return {"route_id": route_id, "status": new_status}


# -------------------------------------------------------------- النشر ----

def publish_day(
    context: SecurityContext,
    *,
    hub_id: str,
    service_date: dt.date,
    plan_id: str | None = None,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """نشر خطة يوم واحد بشكل مستقل (§17)."""
    context.require_hub(hub_id)
    with transaction(context) as conn:
        day = conn.fetch_one(
            "SELECT d.id::text AS id, d.plan_id::text AS plan_id, d.is_published "
            "FROM plan_days d JOIN plans p ON p.id = d.plan_id "
            "WHERE d.hub_id = $1::uuid AND d.service_date = $2::date "
            "AND ($3::uuid IS NULL OR d.plan_id = $3::uuid) "
            "AND p.status IN ('APPROVED','DISPATCHED') "
            "ORDER BY p.created_at DESC LIMIT 1",
            [hub_id, service_date, plan_id],
        )
        if day is None:
            raise NotFound(
                "لا توجد خطة معتمدة لهذا المركز في هذا اليوم — "
                "تأكد من اعتماد التخطيط المركزي للخطة وإرسالها"
            )

        routes = conn.fetch_all(
            "SELECT id::text AS id, reference, status, driver_id::text AS driver_id "
            "FROM routes WHERE plan_day_id = $1::uuid AND status <> 'CANCELLED'",
            [day["id"]],
        )
        without_driver = [r["reference"] for r in routes if r["driver_id"] is None]
        if without_driver:
            raise Conflict(
                "لا يمكن نشر اليوم: توجد رحلات بلا سائق — "
                + "، ".join(without_driver),
                routes_without_driver=without_driver,
            )
        if not routes:
            raise Conflict("لا توجد رحلات في هذا اليوم لنشرها")

        published = 0
        for route in routes:
            if route["status"] != RouteStatus.ASSIGNED:
                continue
            conn.execute(
                "UPDATE routes SET status = 'PUBLISHED', published_by = $1::uuid, "
                "published_at = now() WHERE id = $2::uuid",
                [context.user_id, route["id"]],
            )
            conn.execute(
                "UPDATE shipments SET status = 'PUBLISHED' WHERE route_id = $1::uuid "
                "AND status = 'ASSIGNED'",
                [route["id"]],
            )
            events.publish(
                conn, events.TOPIC_ROUTE,
                {"route_id": route["id"], "reference": route["reference"],
                 "action": "PUBLISHED", "service_date": service_date.isoformat()},
                hub_id=hub_id, driver_id=route["driver_id"],
            )
            published += 1

        conn.execute(
            "UPDATE plan_days SET is_published = true, published_at = now(), "
            "published_by = $1::uuid WHERE id = $2::uuid",
            [context.user_id, day["id"]],
        )
        audit.record(
            conn, context, AuditAction.ROUTE_PUBLISH,
            entity_type="plan_day", entity_id=day["id"],
            entity_label=f"{hub_id}@{service_date}",
            new_value={"published_routes": published,
                       "service_date": service_date.isoformat()},
            ip_address=ip_address,
        )
    return {"hub_id": hub_id, "service_date": service_date.isoformat(),
            "published_routes": published, "plan_day_id": day["id"]}


def unpublish_day(
    context: SecurityContext, *, hub_id: str, service_date: dt.date, reason: str,
    ip_address: str | None = None,
) -> dict[str, Any]:
    context.require_hub(hub_id)
    with transaction(context) as conn:
        day = conn.fetch_one(
            "SELECT id::text AS id FROM plan_days WHERE hub_id = $1::uuid "
            "AND service_date = $2::date AND is_published ORDER BY published_at DESC LIMIT 1",
            [hub_id, service_date],
        )
        if day is None:
            raise NotFound("لا يوجد يوم منشور بهذه المواصفات")
        started = conn.fetch_value(
            "SELECT count(*) FROM routes WHERE plan_day_id = $1::uuid "
            "AND status IN ('IN_PROGRESS','COMPLETED')",
            [day["id"]],
        )
        if int(started or 0):
            raise Conflict("لا يمكن سحب النشر بعد بدء التنفيذ")

        conn.execute(
            "UPDATE routes SET status = 'ASSIGNED' WHERE plan_day_id = $1::uuid "
            "AND status = 'PUBLISHED'",
            [day["id"]],
        )
        conn.execute(
            "UPDATE shipments SET status = 'ASSIGNED' WHERE route_id IN "
            "(SELECT id FROM routes WHERE plan_day_id = $1::uuid) AND status = 'PUBLISHED'",
            [day["id"]],
        )
        conn.execute(
            "UPDATE plan_days SET is_published = false, published_at = NULL "
            "WHERE id = $1::uuid",
            [day["id"]],
        )
        audit.record(
            conn, context, AuditAction.ROUTE_MODIFY_PUBLISHED,
            entity_type="plan_day", entity_id=day["id"],
            new_value={"unpublished": True}, reason=reason, ip_address=ip_address,
        )
    return {"hub_id": hub_id, "service_date": service_date.isoformat(), "unpublished": True}


# --------------------------------------------- تعديل رحلة منشورة (§17) ----

def _record_revision(
    conn: pgwire.Connection,
    context: SecurityContext,
    route_id: str,
    *,
    change_kind: str,
    reason: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> int:
    revision = int(conn.fetch_value(
        "SELECT coalesce(max(revision_number), 0) + 1 FROM route_revisions "
        "WHERE route_id = $1::uuid",
        [route_id],
    ) or 1)
    diff = {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in set(before) | set(after)
        if before.get(key) != after.get(key)
    }
    conn.execute(
        "INSERT INTO route_revisions (route_id, revision_number, changed_by, reason, "
        "change_kind, before_snapshot, after_snapshot, diff_summary, notified_driver) "
        "VALUES ($1::uuid,$2,$3::uuid,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,true)",
        [route_id, revision, context.user_id, reason, change_kind,
         pgwire.Jsonb(before), pgwire.Jsonb(after), pgwire.Jsonb(diff)],
    )
    return revision


def modify_published_route(
    context: SecurityContext,
    route_id: str,
    *,
    change_kind: str,
    reason: str,
    add_shipment_ids: list[str] | None = None,
    remove_shipment_ids: list[str] | None = None,
    new_order: list[str] | None = None,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """يعدل رحلة منشورة مع تسجيل الفرق والسبب وإشعار السائق فورًا."""
    from .planning import get_route_detail

    with transaction(context) as conn:
        route = conn.fetch_one(
            "SELECT id::text AS id, reference, hub_id::text AS hub_id, status, "
            "driver_id::text AS driver_id, service_date FROM routes "
            "WHERE id = $1::uuid FOR UPDATE",
            [route_id],
        )
        if route is None:
            raise NotFound("الرحلة غير موجودة")
        context.require_hub(route["hub_id"])
        if route["status"] not in (RouteStatus.PUBLISHED, RouteStatus.IN_PROGRESS):
            raise Conflict("هذه العملية مخصصة للرحلات المنشورة أو الجارية")

        before_stops = conn.fetch_all(
            "SELECT id::text AS id, sequence, kind, label_ar, shipment_id::text AS shipment_id "
            "FROM route_stops WHERE route_id = $1::uuid ORDER BY sequence",
            [route_id],
        )
        before = {"stops": [dict(row) for row in before_stops]}

        if remove_shipment_ids:
            done = conn.fetch_all(
                "SELECT s.reference FROM shipments s WHERE s.id = ANY($1::uuid[]) "
                "AND s.status IN ('PICKED_UP','ARRIVED_DELIVERY','DELIVERED','COMPLETED')",
                [remove_shipment_ids],
            )
            if done:
                raise Conflict(
                    "لا يمكن إزالة شحنات بدأ تنفيذها: "
                    + "، ".join(row["reference"] for row in done)
                )
            conn.execute(
                "DELETE FROM route_stops WHERE route_id = $1::uuid "
                "AND shipment_id = ANY($2::uuid[])",
                [route_id, remove_shipment_ids],
            )
            conn.execute(
                "UPDATE shipments SET status = 'PENDING_ASSIGNMENT', route_id = NULL, "
                "driver_id = NULL WHERE id = ANY($1::uuid[]) "
                "AND status IN ('PUBLISHED','ASSIGNED','IN_PROGRESS')",
                [remove_shipment_ids],
            )

        if new_order:
            for position, stop_id in enumerate(new_order, start=1):
                conn.execute(
                    "UPDATE route_stops SET sequence = $1 WHERE id = $2::uuid "
                    "AND route_id = $3::uuid",
                    [position + 1000, stop_id, route_id],
                )
            for position, stop_id in enumerate(new_order, start=1):
                conn.execute(
                    "UPDATE route_stops SET sequence = $1 WHERE id = $2::uuid "
                    "AND route_id = $3::uuid",
                    [position, stop_id, route_id],
                )

        _recount_route(conn, route_id)

        after_stops = conn.fetch_all(
            "SELECT id::text AS id, sequence, kind, label_ar, shipment_id::text AS shipment_id "
            "FROM route_stops WHERE route_id = $1::uuid ORDER BY sequence",
            [route_id],
        )
        after = {"stops": [dict(row) for row in after_stops]}

        violations = conn.fetch_all(
            "SELECT rule_code, detail_ar FROM app.verify_route_feasibility($1::uuid)",
            [route_id],
        )
        if violations:
            raise Conflict(
                "التعديل يخرق قيودًا صلبة: "
                + "؛ ".join(f"{v['rule_code']}: {v['detail_ar']}" for v in violations),
                violations=[dict(v) for v in violations],
            )

        revision = _record_revision(
            conn, context, route_id,
            change_kind=change_kind, reason=reason, before=before, after=after,
        )
        audit.record(
            conn, context, AuditAction.ROUTE_MODIFY_PUBLISHED,
            entity_type="route", entity_id=route_id, entity_label=route["reference"],
            old_value=before, new_value=after, reason=reason, ip_address=ip_address,
        )
        events.publish(
            conn, events.TOPIC_ROUTE,
            {"route_id": route_id, "reference": route["reference"],
             "action": "MODIFIED", "revision": revision, "reason": reason},
            hub_id=route["hub_id"], driver_id=route["driver_id"],
        )
        alerts.raise_alert(
            conn, AlertType.PUBLISHED_ROUTE_MODIFIED,
            title_ar="تعديل رحلة منشورة",
            body_ar=f"عُدّلت الرحلة {route['reference']} (مراجعة {revision}) — {reason}",
            route_id=route_id, hub_id=route["hub_id"], driver_id=route["driver_id"],
            severity=Severity.LOW,
        )
    return {"route_id": route_id, "revision": revision}


def _recount_route(conn: pgwire.Connection, route_id: str) -> None:
    conn.execute(
        """
        UPDATE routes SET
            shipment_count = sub.shipments,
            pickup_count   = sub.pickups,
            delivery_count = sub.deliveries
        FROM (
            SELECT count(DISTINCT shipment_id) AS shipments,
                   count(*) FILTER (WHERE kind = 'PICKUP') AS pickups,
                   count(*) FILTER (WHERE kind = 'DELIVERY') AS deliveries
            FROM route_stops WHERE route_id = $1::uuid
        ) sub
        WHERE routes.id = $1::uuid
        """,
        [route_id],
    )


def list_routes(
    context: SecurityContext,
    *,
    hub_id: str | None = None,
    service_date: dt.date | None = None,
    status: str | None = None,
    driver_id: str | None = None,
    limit: int = 200,
) -> list[Any]:
    clauses = ["r.status <> 'CANCELLED'"]
    params: list[Any] = []

    def add(clause: str, value: Any) -> None:
        params.append(value)
        clauses.append(clause.replace("?", f"${len(params)}"))

    if hub_id:
        add("r.hub_id = ?::uuid", hub_id)
    if service_date:
        add("r.service_date = ?::date", service_date)
    if status:
        add("r.status = ?", status)
    if driver_id:
        add("r.driver_id = ?::uuid", driver_id)

    with session(context) as conn:
        return conn.fetch_all(
            "SELECT r.id::text AS id, r.reference, r.hub_id::text AS hub_id, "
            "h.name_ar AS hub_name_ar, r.service_date, r.status, r.sequence_in_day, "
            "r.driver_id::text AS driver_id, d.full_name AS driver_name, "
            "v.plate_number, b.code AS box_code, r.planned_start_at, r.planned_end_at, "
            "r.actual_start_at, r.actual_end_at, r.distance_km, r.working_minutes, "
            "r.shipment_count, r.pickup_count, r.delivery_count, r.is_long_haul, "
            "r.end_lat, r.end_lon "
            "FROM routes r JOIN hubs h ON h.id = r.hub_id "
            "LEFT JOIN drivers d ON d.id = r.driver_id "
            "LEFT JOIN vehicles v ON v.id = r.vehicle_id "
            "LEFT JOIN boxes b ON b.id = r.box_id "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY r.service_date DESC, h.code, r.sequence_in_day LIMIT {int(limit)}",
            params,
        )
