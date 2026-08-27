"""التقارير ومؤشرات الأداء (§26).

قاعدة حاكمة (§28/§34): **مصدر بيانات موحّد للمؤشرات.** كل رقم في لوحات
المعلومات وفي التفاصيل يأتي من نفس الاستعلام الأساسي ``_base_cte`` مع
مرشّحات مختلفة، فلا يمكن أن يختلف عدّاد الشحنات بين شاشة وأخرى.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from typing import Any

import pgwire
from masar_core.errors import ValidationError
from masar_db.driver import SecurityContext, session

#: تعريف موحّد لمجموعة الشحنات محل التحليل
_BASE_CTE = """
WITH scoped AS (
    SELECT s.*,
           pf.name_ar        AS pickup_name,
           pf.facility_type  AS pickup_type,
           df.name_ar        AS dropoff_name,
           df.facility_type  AS dropoff_type,
           h.name_ar         AS hub_name_ar,
           h.code            AS hub_code,
           c.name_ar         AS city_name_ar,
           rg.name_ar        AS region_name_ar,
           d.full_name       AS driver_name,
           d.code            AS driver_code,
           CASE WHEN s.actual_dropoff_at IS NOT NULL AND s.sla_deadline IS NOT NULL
                THEN EXTRACT(EPOCH FROM (s.actual_dropoff_at - s.sla_deadline)) / 60
                ELSE NULL END AS delivery_delay_minutes,
           CASE WHEN s.actual_pickup_at IS NOT NULL AND s.pickup_window_to IS NOT NULL
                THEN EXTRACT(EPOCH FROM (s.actual_pickup_at - s.pickup_window_to)) / 60
                ELSE NULL END AS pickup_delay_minutes
    FROM shipments s
    JOIN facilities pf ON pf.id = s.pickup_facility_id
    JOIN facilities df ON df.id = s.dropoff_facility_id
    JOIN hubs h        ON h.id  = s.hub_id
    JOIN cities c      ON c.id  = s.city_id
    JOIN regions rg    ON rg.id = s.region_id
    LEFT JOIN drivers d ON d.id = s.driver_id
    WHERE {filters}
)
"""

ALLOWED_GROUPINGS = {
    "hub": ("hub_id::text", "hub_name_ar"),
    "region": ("region_id::text", "region_name_ar"),
    "city": ("city_id::text", "city_name_ar"),
    "driver": ("driver_id::text", "driver_name"),
    "facility": ("pickup_facility_id::text", "pickup_name"),
    "facility_type": ("pickup_type", "pickup_type"),
    "service_type": ("service_type", "service_type"),
    "status": ("status", "status"),
    "date": ("service_date::text", "service_date::text"),
}


def _build_filters(
    *,
    date_from: dt.date | None,
    date_to: dt.date | None,
    region_id: str | None,
    city_id: str | None,
    hub_id: str | None,
    driver_id: str | None,
    facility_type: str | None,
    service_type: str | None,
    status: str | None,
    request_kind: str | None,
    include_test_data: bool,
) -> tuple[str, list[Any]]:
    clauses = ["1=1"]
    params: list[Any] = []

    def add(clause: str, value: Any) -> None:
        params.append(value)
        clauses.append(clause.replace("?", f"${len(params)}"))

    if date_from:
        add("s.service_date >= ?::date", date_from)
    if date_to:
        add("s.service_date <= ?::date", date_to)
    if region_id:
        add("s.region_id = ?::uuid", region_id)
    if city_id:
        add("s.city_id = ?::uuid", city_id)
    if hub_id:
        add("s.hub_id = ?::uuid", hub_id)
    if driver_id:
        add("s.driver_id = ?::uuid", driver_id)
    if facility_type:
        add("pf.facility_type = ?", facility_type)
    if service_type:
        add("s.service_type = ?", service_type)
    if status:
        add("s.status = ?", status)
    if request_kind:
        add("s.request_kind = ?", request_kind)
    if not include_test_data:
        clauses.append("NOT s.is_test_data")
    return " AND ".join(clauses), params


def kpi_summary(context: SecurityContext, **filters: Any) -> dict[str, Any]:
    """المؤشرات الرئيسية — المصدر الوحيد لأرقام لوحات المعلومات."""
    where, params = _build_filters(**_normalize_filters(filters))
    query = _BASE_CTE.format(filters=where) + """
    SELECT
        count(*)                                                        AS shipment_count,
        count(*) FILTER (WHERE status = 'COMPLETED')                    AS completed_count,
        count(*) FILTER (WHERE status = 'CANCELLED_BEFORE_PICKUP')      AS cancelled_count,
        count(*) FILTER (WHERE status = 'FAILED')                       AS failed_count,
        count(*) FILTER (WHERE status = 'EXCEPTION')                    AS exception_count,
        count(*) FILTER (WHERE status = 'UNPLANNABLE')                  AS unplannable_count,
        count(*) FILTER (WHERE request_kind = 'ON_DEMAND')              AS on_demand_count,
        count(*) FILTER (WHERE sla_breached)                            AS sla_breached_count,
        count(*) FILTER (WHERE pickup_window_breached)                  AS pickup_breached_count,
        count(*) FILTER (WHERE actual_dropoff_at IS NOT NULL)           AS delivered_count,
        count(*) FILTER (WHERE actual_pickup_at IS NOT NULL)            AS picked_up_count,
        count(*) FILTER (WHERE delivery_obligation_open)                AS open_obligation_count,
        round(avg(delivery_delay_minutes) FILTER
              (WHERE delivery_delay_minutes > 0)::numeric, 1)           AS avg_delivery_delay,
        round(avg(pickup_delay_minutes) FILTER
              (WHERE pickup_delay_minutes > 0)::numeric, 1)             AS avg_pickup_delay,
        count(DISTINCT route_id) FILTER (WHERE route_id IS NOT NULL)    AS route_count,
        count(DISTINCT driver_id) FILTER (WHERE driver_id IS NOT NULL)  AS driver_count,
        count(DISTINCT hub_id)                                          AS hub_count,
        count(DISTINCT service_date)                                    AS day_count,
        sum(piece_count)                                                AS piece_count
    FROM scoped
    """
    with session(context) as conn:
        row = conn.fetch_one(query, params)

    data = dict(row) if row else {}
    delivered = int(data.get("delivered_count") or 0)
    picked = int(data.get("picked_up_count") or 0)
    total = int(data.get("shipment_count") or 0)

    data["sla_compliance_pct"] = (
        round((delivered - int(data.get("sla_breached_count") or 0)) / delivered * 100, 2)
        if delivered else None
    )
    data["pickup_window_compliance_pct"] = (
        round((picked - int(data.get("pickup_breached_count") or 0)) / picked * 100, 2)
        if picked else None
    )
    data["completion_rate_pct"] = (
        round(int(data.get("completed_count") or 0) / total * 100, 2) if total else None
    )
    return data


def grouped_report(
    context: SecurityContext, *, group_by: str = "hub", **filters: Any
) -> list[dict[str, Any]]:
    """تقرير مُجمَّع حسب بُعد مسموح (قائمة بيضاء تمنع حقن SQL)."""
    if group_by not in ALLOWED_GROUPINGS:
        raise ValidationError(
            f"تجميع غير مدعوم: {group_by}",
            allowed=sorted(ALLOWED_GROUPINGS),
        )
    key_expr, label_expr = ALLOWED_GROUPINGS[group_by]
    where, params = _build_filters(**_normalize_filters(filters))
    query = _BASE_CTE.format(filters=where) + f"""
    SELECT
        {key_expr}   AS group_key,
        {label_expr} AS group_label,
        count(*)                                                     AS shipment_count,
        count(*) FILTER (WHERE status = 'COMPLETED')                 AS completed_count,
        count(*) FILTER (WHERE sla_breached)                         AS sla_breached_count,
        count(*) FILTER (WHERE pickup_window_breached)               AS pickup_breached_count,
        count(*) FILTER (WHERE status = 'FAILED')                    AS failed_count,
        count(*) FILTER (WHERE status = 'CANCELLED_BEFORE_PICKUP')   AS cancelled_count,
        count(*) FILTER (WHERE status = 'UNPLANNABLE')               AS unplannable_count,
        count(*) FILTER (WHERE request_kind = 'ON_DEMAND')           AS on_demand_count,
        count(DISTINCT route_id)                                     AS route_count,
        count(DISTINCT driver_id)                                    AS driver_count,
        round(avg(delivery_delay_minutes) FILTER
              (WHERE delivery_delay_minutes > 0)::numeric, 1)        AS avg_delivery_delay,
        round(avg(pickup_delay_minutes) FILTER
              (WHERE pickup_delay_minutes > 0)::numeric, 1)          AS avg_pickup_delay
    FROM scoped
    WHERE {key_expr} IS NOT NULL
    GROUP BY 1, 2
    ORDER BY shipment_count DESC
    """
    with session(context) as conn:
        rows = conn.fetch_all(query, params)

    result = []
    for row in rows:
        item = dict(row)
        delivered = int(item["completed_count"] or 0)
        item["sla_compliance_pct"] = (
            round((delivered - int(item["sla_breached_count"] or 0)) / delivered * 100, 2)
            if delivered else None
        )
        result.append(item)
    return result


def route_metrics(context: SecurityContext, **filters: Any) -> dict[str, Any]:
    """مقاييس الرحلات: المسافة والزمن واستغلال السائق والرحلات البعيدة."""
    normalized = _normalize_filters(filters)
    clauses = ["r.status <> 'CANCELLED'"]
    params: list[Any] = []

    def add(clause: str, value: Any) -> None:
        params.append(value)
        clauses.append(clause.replace("?", f"${len(params)}"))

    if normalized["date_from"]:
        add("r.service_date >= ?::date", normalized["date_from"])
    if normalized["date_to"]:
        add("r.service_date <= ?::date", normalized["date_to"])
    if normalized["hub_id"]:
        add("r.hub_id = ?::uuid", normalized["hub_id"])
    if normalized["region_id"]:
        add("r.region_id = ?::uuid", normalized["region_id"])
    if normalized["driver_id"]:
        add("r.driver_id = ?::uuid", normalized["driver_id"])
    if not normalized["include_test_data"]:
        clauses.append("NOT r.is_test_data")

    where = " AND ".join(clauses)
    with session(context) as conn:
        totals = conn.fetch_one(
            f"""
            SELECT count(*) AS route_count,
                   count(*) FILTER (WHERE is_long_haul) AS long_haul_count,
                   count(*) FILTER (WHERE driver_id IS NULL) AS unassigned_count,
                   count(DISTINCT driver_id) AS driver_count,
                   round(sum(distance_km)::numeric, 2) AS total_distance_km,
                   round(sum(drive_minutes)::numeric, 1) AS total_drive_minutes,
                   round(sum(service_minutes)::numeric, 1) AS total_service_minutes,
                   round(sum(wait_minutes)::numeric, 1) AS total_wait_minutes,
                   round(sum(working_minutes)::numeric, 1) AS total_working_minutes,
                   round(sum(estimated_cost)::numeric, 2) AS total_cost,
                   round(avg(working_minutes)::numeric, 1) AS avg_working_minutes,
                   sum(pickup_count) AS pickup_points,
                   sum(delivery_count) AS delivery_points
            FROM routes r WHERE {where}
            """,
            params,
        )
        per_driver = conn.fetch_all(
            f"""
            SELECT r.driver_id::text AS driver_id, d.full_name AS driver_name,
                   d.code AS driver_code, count(*) AS route_count,
                   count(*) FILTER (WHERE r.is_long_haul) AS long_haul_count,
                   round(sum(r.distance_km)::numeric, 2) AS distance_km,
                   round(sum(r.working_minutes)::numeric, 1) AS working_minutes,
                   sum(r.pickup_count) AS pickup_points,
                   sum(r.delivery_count) AS delivery_points,
                   count(*) FILTER (WHERE r.actual_start_at IS NOT NULL) AS started_routes,
                   count(*) FILTER (WHERE r.status = 'COMPLETED') AS completed_routes
            FROM routes r JOIN drivers d ON d.id = r.driver_id
            WHERE {where} AND r.driver_id IS NOT NULL
            GROUP BY 1,2,3 ORDER BY working_minutes DESC
            """,
            params,
        )

    workloads = [float(row["working_minutes"] or 0) for row in per_driver]
    fairness = None
    if len(workloads) > 1:
        mean = sum(workloads) / len(workloads)
        variance = sum((value - mean) ** 2 for value in workloads) / len(workloads)
        fairness = {
            "mean_minutes": round(mean, 1),
            "std_dev_minutes": round(variance ** 0.5, 1),
            "max_minutes": round(max(workloads), 1),
            "min_minutes": round(min(workloads), 1),
            "spread_pct": round((max(workloads) - min(workloads)) / max(workloads) * 100, 1)
            if max(workloads) else None,
        }

    return {
        "totals": dict(totals) if totals else {},
        "per_driver": [dict(row) for row in per_driver],
        "fairness": fairness,
    }


def exception_report(context: SecurityContext, **filters: Any) -> list[dict[str, Any]]:
    normalized = _normalize_filters(filters)
    clauses = ["1=1"]
    params: list[Any] = []
    if normalized["date_from"]:
        params.append(normalized["date_from"])
        clauses.append(f"e.occurred_at >= ${len(params)}::date")
    if normalized["date_to"]:
        params.append(normalized["date_to"])
        clauses.append(f"e.occurred_at < ${len(params)}::date + 1")
    if normalized["hub_id"]:
        params.append(normalized["hub_id"])
        clauses.append(f"e.hub_id = ${len(params)}::uuid")

    with session(context) as conn:
        return [dict(row) for row in conn.fetch_all(
            f"""
            SELECT e.reason,
                   count(*) AS total,
                   count(*) FILTER (WHERE e.status = 'RESOLVED') AS resolved,
                   count(*) FILTER (WHERE e.keeps_obligation) AS keeps_obligation,
                   round(avg(EXTRACT(EPOCH FROM (e.resolved_at - e.occurred_at)) / 60)
                         ::numeric, 1) AS avg_resolution_minutes
            FROM shipment_exceptions e
            WHERE {' AND '.join(clauses)}
            GROUP BY e.reason ORDER BY total DESC
            """,
            params,
        )]


def temperature_report(context: SecurityContext, **filters: Any) -> dict[str, Any]:
    normalized = _normalize_filters(filters)
    clauses = ["1=1"]
    params: list[Any] = []
    if normalized["date_from"]:
        params.append(normalized["date_from"])
        clauses.append(f"b.started_at >= ${len(params)}::date")
    if normalized["date_to"]:
        params.append(normalized["date_to"])
        clauses.append(f"b.started_at < ${len(params)}::date + 1")

    with session(context) as conn:
        breaches = conn.fetch_all(
            f"""
            SELECT b.id::text AS id, b.breach_kind, b.started_at, b.ended_at,
                   b.duration_minutes, b.min_celsius, b.max_celsius,
                   b.required_min_c, b.required_max_c, b.action_taken, b.resolved_at,
                   b.is_test_data, s.reference AS shipment_reference,
                   s.temperature_mode, h.name_ar AS hub_name_ar
            FROM temperature_breaches b
            LEFT JOIN shipments s ON s.id = b.shipment_id
            LEFT JOIN hubs h ON h.id = s.hub_id
            WHERE {' AND '.join(clauses)}
            ORDER BY b.started_at DESC LIMIT 500
            """,
            params,
        )
        coverage = conn.fetch_one(
            """
            SELECT count(DISTINCT s.id) AS shipments_total,
                   count(DISTINCT s.id) FILTER (WHERE t.shipment_id IS NOT NULL)
                        AS shipments_with_readings,
                   count(*) FILTER (WHERE t.source = 'SIMULATION') AS simulated_readings,
                   count(*) FILTER (WHERE t.source = 'SENSOR') AS sensor_readings
            FROM shipments s
            LEFT JOIN temperature_readings t ON t.shipment_id = s.id
            WHERE s.temperature_mode <> 'AMBIENT'
            """
        )
    return {
        "breaches": [dict(row) for row in breaches],
        "coverage": dict(coverage) if coverage else {},
    }


def plan_vs_execution(context: SecurityContext, **filters: Any) -> dict[str, Any]:
    """مقارنة الخطة الأصلية بالتنفيذ الفعلي (§26)."""
    where, params = _build_filters(**_normalize_filters(filters))
    query = _BASE_CTE.format(filters=where) + """
    SELECT
        count(*) FILTER (WHERE planned_pickup_at IS NOT NULL)  AS planned_shipments,
        count(*) FILTER (WHERE actual_pickup_at IS NOT NULL)   AS executed_pickups,
        count(*) FILTER (WHERE actual_dropoff_at IS NOT NULL)  AS executed_deliveries,
        round(avg(EXTRACT(EPOCH FROM (actual_pickup_at - planned_pickup_at)) / 60)
              ::numeric, 1)                                    AS avg_pickup_deviation,
        round(avg(EXTRACT(EPOCH FROM (actual_dropoff_at - planned_dropoff_at)) / 60)
              ::numeric, 1)                                    AS avg_delivery_deviation,
        count(*) FILTER (WHERE actual_pickup_at IS NOT NULL
              AND abs(EXTRACT(EPOCH FROM (actual_pickup_at - planned_pickup_at))) <= 900)
                                                               AS pickups_within_15min,
        count(*) FILTER (WHERE route_id IS NOT NULL)           AS assigned_shipments
    FROM scoped
    """
    with session(context) as conn:
        row = conn.fetch_one(query, params)
        revisions = conn.fetch_one(
            "SELECT count(*) AS revision_count, count(DISTINCT route_id) AS routes_modified "
            "FROM route_revisions"
        )
        reasons = conn.fetch_all(
            "SELECT change_kind, count(*) AS total, "
            "array_agg(DISTINCT left(reason, 120)) AS sample_reasons "
            "FROM route_revisions GROUP BY change_kind ORDER BY total DESC"
        )
    data = dict(row) if row else {}
    planned = int(data.get("planned_shipments") or 0)
    data["plan_adherence_pct"] = (
        round(int(data.get("pickups_within_15min") or 0) / planned * 100, 2)
        if planned else None
    )
    data["revisions"] = dict(revisions) if revisions else {}
    data["revision_reasons"] = [dict(r) for r in reasons]
    return data


def hub_modification_monitor(context: SecurityContext, **filters: Any) -> list[dict[str, Any]]:
    """مراقبة تعديلات مراكز الانطلاق على الخطة (§5 للتخطيط المركزي)."""
    with session(context) as conn:
        return [dict(row) for row in conn.fetch_all(
            """
            SELECT h.id::text AS hub_id, h.name_ar AS hub_name_ar, h.code AS hub_code,
                   count(rv.*) AS revision_count,
                   count(DISTINCT rv.route_id) AS routes_modified,
                   count(*) FILTER (WHERE rv.change_kind = 'REASSIGN_DRIVER')
                        AS driver_reassignments,
                   count(*) FILTER (WHERE rv.change_kind = 'ADD_STOP') AS stops_added,
                   count(*) FILTER (WHERE rv.change_kind = 'REMOVE_STOP') AS stops_removed,
                   max(rv.changed_at) AS last_change_at,
                   array_agg(DISTINCT left(rv.reason, 100)) FILTER (WHERE rv.reason <> '')
                        AS reasons
            FROM route_revisions rv
            JOIN routes r ON r.id = rv.route_id
            JOIN hubs h ON h.id = r.hub_id
            GROUP BY h.id, h.name_ar, h.code
            ORDER BY revision_count DESC
            """
        )]


def driver_capacity_monitor(context: SecurityContext, **filters: Any) -> list[dict[str, Any]]:
    """كشف الزيادة غير المبررة في عدد السائقين (§5 للتخطيط المركزي)."""
    normalized = _normalize_filters(filters)
    params: list[Any] = []
    clauses = ["1=1"]
    if normalized["date_from"]:
        params.append(normalized["date_from"])
        clauses.append(f"e.service_date >= ${len(params)}::date")
    if normalized["date_to"]:
        params.append(normalized["date_to"])
        clauses.append(f"e.service_date <= ${len(params)}::date")

    with session(context) as conn:
        rows = conn.fetch_all(
            f"""
            SELECT h.id::text AS hub_id, h.name_ar AS hub_name_ar, e.service_date,
                   e.theoretical_minimum, e.recommended, e.available, e.used, e.gap,
                   e.workload_minutes, e.justification
            FROM driver_estimations e JOIN hubs h ON h.id = e.hub_id
            WHERE {' AND '.join(clauses)}
            ORDER BY (e.used - e.theoretical_minimum) DESC, e.service_date DESC
            """,
            params,
        )
    result = []
    for row in rows:
        item = dict(row)
        excess = int(item["used"]) - int(item["theoretical_minimum"])
        justified = sum(
            int(reason.get("drivers") or 0)
            for reason in (item["justification"] or [])
            if reason.get("code") in ("MIXING", "TIME_WINDOWS", "LONG_HAUL", "GEOGRAPHY")
        )
        item["excess_drivers"] = excess
        item["justified_excess"] = justified
        item["unjustified_excess"] = max(0, excess - justified)
        item["flag"] = "غير مبرر" if item["unjustified_excess"] > 0 else "مبرر"
        result.append(item)
    return result


def export_csv(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> bytes:
    """تصدير CSV بترويسات عربية وبترميز يفتحه Excel بلا تشويه."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in columns])
    for row in rows:
        writer.writerow([_csv_value(row.get(key)) for key, _ in columns])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return "، ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "نعم" if value else "لا"
    return str(value)


def _normalize_filters(filters: dict[str, Any]) -> dict[str, Any]:
    return {
        "date_from": filters.get("date_from"),
        "date_to": filters.get("date_to"),
        "region_id": filters.get("region_id"),
        "city_id": filters.get("city_id"),
        "hub_id": filters.get("hub_id"),
        "driver_id": filters.get("driver_id"),
        "facility_type": filters.get("facility_type"),
        "service_type": filters.get("service_type"),
        "status": filters.get("status"),
        "request_kind": filters.get("request_kind"),
        "include_test_data": bool(filters.get("include_test_data", False)),
    }
