"""التتبع المباشر لمواقع السائقين (§23)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pgwire
from masar_core.config import get_config
from masar_core.errors import Forbidden, ValidationError
from masar_core.timeutil import haversine_km, validate_coordinates
from masar_db.driver import SecurityContext, session, transaction

from . import events


def publish_positions(
    context: SecurityContext, points: list[dict[str, Any]]
) -> dict[str, Any]:
    """يستقبل دفعة مواقع من تطبيق السائق (تعمل أيضًا بعد انقطاع الاتصال)."""
    if not context.driver_id:
        raise Forbidden("هذا الحساب غير مرتبط بسجل سائق")
    if not points:
        return {"accepted": 0, "rejected": 0}

    accepted = rejected = 0
    latest: dict[str, Any] | None = None

    with transaction(context) as conn:
        for point in points[:500]:
            try:
                lat, lon = validate_coordinates(
                    point.get("lat"), point.get("lon"),
                    enforce_ksa_bounds=False, label="موقع السائق")
                recorded_at = point.get("recorded_at")
                if isinstance(recorded_at, str):
                    from masar_core.timeutil import parse_datetime

                    recorded_at = parse_datetime(recorded_at, field="وقت التسجيل")
                recorded_at = recorded_at or dt.datetime.now(dt.timezone.utc)
            except ValidationError:
                rejected += 1
                continue

            conn.execute(
                "INSERT INTO driver_positions (driver_id, route_id, lat, lon, speed_kmh, "
                "heading_deg, accuracy_m, battery_pct, recorded_at) "
                "VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8,$9::timestamptz)",
                [context.driver_id, point.get("route_id"), lat, lon,
                 point.get("speed_kmh"), point.get("heading_deg"),
                 point.get("accuracy_m"), point.get("battery_pct"), recorded_at],
            )
            accepted += 1
            latest = {"lat": lat, "lon": lon, "recorded_at": recorded_at,
                      "route_id": point.get("route_id")}

        if latest:
            hub_id = conn.fetch_value(
                "SELECT hub_id::text FROM drivers WHERE id = $1::uuid", [context.driver_id])
            events.publish(
                conn, events.TOPIC_POSITION,
                {"driver_id": context.driver_id, "lat": latest["lat"],
                 "lon": latest["lon"], "route_id": latest["route_id"],
                 "recorded_at": latest["recorded_at"].isoformat()},
                hub_id=hub_id, driver_id=context.driver_id,
            )
    return {"accepted": accepted, "rejected": rejected}


def live_positions(
    context: SecurityContext, *, hub_id: str | None = None
) -> list[dict[str, Any]]:
    """آخر موقع لكل سائق نشط مع وسم قِدَم البيانات."""
    stale_seconds = get_config().tracking_stale_seconds
    params: list[Any] = []
    clause = ""
    if hub_id:
        params.append(hub_id)
        clause = f"AND d.hub_id = ${len(params)}::uuid"

    with session(context) as conn:
        rows = conn.fetch_all(
            f"""
            SELECT p.driver_id::text AS driver_id, d.full_name, d.code, d.phone,
                   d.hub_id::text AS hub_id, h.name_ar AS hub_name_ar,
                   p.lat, p.lon, p.speed_kmh, p.heading_deg, p.recorded_at, p.received_at,
                   p.route_id::text AS route_id, r.reference AS route_reference,
                   r.status AS route_status,
                   EXTRACT(EPOCH FROM (now() - p.recorded_at)) AS age_seconds,
                   (SELECT count(*) FROM route_stops st
                    WHERE st.route_id = r.id AND st.status = 'DONE') AS completed_stops,
                   (SELECT count(*) FROM route_stops st
                    WHERE st.route_id = r.id AND st.kind <> 'HUB_START') AS total_stops
            FROM driver_last_position p
            JOIN drivers d ON d.id = p.driver_id
            JOIN hubs h ON h.id = d.hub_id
            LEFT JOIN routes r ON r.id = p.route_id
            WHERE d.is_active {clause}
            ORDER BY d.code
            """,
            params,
        )
    result = []
    for row in rows:
        age = float(row["age_seconds"] or 0)
        result.append({
            **dict(row),
            "age_seconds": round(age),
            "is_stale": age > stale_seconds,
            "stale_threshold_seconds": stale_seconds,
        })
    return result


def route_track(
    context: SecurityContext, route_id: str, *, max_points: int = 2000
) -> dict[str, Any]:
    """المسار المنفَّذ فعليًا مقابل المسار المخطط، مع قياس الانحراف."""
    with session(context) as conn:
        route = conn.fetch_one(
            "SELECT id::text AS id, reference, driver_id::text AS driver_id, "
            "service_date, actual_start_at, actual_end_at, status "
            "FROM routes WHERE id = $1::uuid",
            [route_id],
        )
        if route is None:
            from masar_core.errors import NotFound

            raise NotFound("الرحلة غير موجودة")

        planned = conn.fetch_all(
            "SELECT sequence, kind, label_ar, lat, lon, planned_arrival_at, "
            "actual_arrival_at, actual_completed_at, status "
            "FROM route_stops WHERE route_id = $1::uuid ORDER BY sequence",
            [route_id],
        )
        positions = conn.fetch_all(
            "SELECT lat, lon, speed_kmh, recorded_at FROM driver_positions "
            "WHERE route_id = $1::uuid ORDER BY recorded_at "
            f"LIMIT {int(max_points)}",
            [route_id],
        )

    # الانحراف: أقصى مسافة بين نقطة فعلية وأقرب مقطع مخطط (تقريب بالنقاط)
    deviation_km = 0.0
    if positions and len(planned) > 1:
        planned_points = [(float(p["lat"]), float(p["lon"])) for p in planned]
        for position in positions:
            nearest = min(
                haversine_km(float(position["lat"]), float(position["lon"]), lat, lon)
                for lat, lon in planned_points
            )
            deviation_km = max(deviation_km, nearest)

    return {
        "route": dict(route),
        "planned_stops": [dict(row) for row in planned],
        "actual_track": [
            {"lat": float(p["lat"]), "lon": float(p["lon"]),
             "speed_kmh": float(p["speed_kmh"]) if p["speed_kmh"] is not None else None,
             "recorded_at": p["recorded_at"].isoformat()}
            for p in positions
        ],
        "max_deviation_km": round(deviation_km, 2),
        "point_count": len(positions),
    }
