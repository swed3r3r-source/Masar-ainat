"""فحص سريع لمحرك التخطيط على مسألة تركيبية — لا يمس قاعدة البيانات."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from masar_core.operational_settings import SettingsResolver  # noqa: E402
from masar_opt.engine import (  # noqa: E402
    HubInput,
    ShipmentInput,
    VehicleInput,
    build_problem,
    run_engine,
)
from masar_opt.exact import solve_exact, solve_single_vehicle_exact  # noqa: E402
from masar_opt.model import to_datetime  # noqa: E402
from masar_opt.objective import objective_vector  # noqa: E402
from masar_opt.solver import SolveOptions  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=3))
DAY = dt.date(2026, 9, 6)


def at(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=TZ)


HUB = HubInput("hub-ryd", "H-RYD-1", "مركز الرياض", 24.7136, 46.6753, at(6), at(20))

# مواقع حقيقية تقريبية داخل الرياض
PLACES = [
    ("fac-1", "HEALTH_CENTER", "مركز صحي النسيم", 24.7743, 46.8172),
    ("fac-2", "HEALTH_CENTER", "مركز صحي العزيزية", 24.5701, 46.7386),
    ("fac-3", "HEALTH_CENTER", "مركز صحي الشفا", 24.5620, 46.6800),
    ("fac-4", "HEALTH_CENTER", "مركز صحي الملز", 24.6690, 46.7300),
    ("fac-5", "HEALTH_CENTER", "مركز صحي السويدي", 24.6060, 46.6320),
    ("lab-1", "LABORATORY", "المختبر الإقليمي", 24.6877, 46.7219),
]


def build(shipment_count: int = 5, vehicle_count: int = 2):
    shipments = []
    for index in range(shipment_count):
        facility_id, facility_type, name, lat, lon = PLACES[index % (len(PLACES) - 1)]
        window_center = at(7 + index)
        shipments.append(ShipmentInput(
            shipment_id=f"shp-{index + 1}",
            reference=f"SHP-{index + 1:04d}",
            hub_id=HUB.hub_id,
            pickup_facility_id=facility_id,
            pickup_facility_type=facility_type,
            pickup_name=name,
            pickup_lat=lat, pickup_lon=lon,
            pickup_window_from=window_center - dt.timedelta(minutes=15),
            pickup_window_to=window_center + dt.timedelta(minutes=15),
            pickup_service_minutes=10,
            dropoff_facility_id="lab-1",
            dropoff_facility_type="LABORATORY",
            dropoff_name="المختبر الإقليمي",
            dropoff_lat=24.6877, dropoff_lon=46.7219,
            dropoff_service_minutes=10,
            sla_deadline=window_center + dt.timedelta(hours=3),
        ))

    vehicles = [
        VehicleInput(
            hub_id=HUB.hub_id, label=f"خانة سائق {i + 1}",
            earliest_start=at(6), latest_end=at(20), max_shift_minutes=600,
        )
        for i in range(vehicle_count)
    ]

    effective = SettingsResolver().effective()
    return build_problem(
        service_date=DAY, hubs=[HUB], shipments=shipments, vehicles=vehicles,
        effective_settings=effective, routing_provider_name="haversine",
    )


def main() -> int:
    print("=" * 70)
    print("فحص محرك التخطيط")
    print("=" * 70)

    problem = build(shipment_count=5, vehicle_count=3)
    result = run_engine(
        problem,
        options=SolveOptions(time_limit_seconds=3.0, seed=1),
        available_drivers_by_hub={HUB.hub_id: 4},
    )

    m = result.metrics
    print(f"\nالشحنات: {m['shipment_count']} · المخططة: {m['planned_shipment_count']} "
          f"· غير القابلة للتخطيط: {m['unplannable_count']}")
    print(f"الرحلات: {m['route_count']} · السائقون المستخدمون: {m['drivers_used']} "
          f"· الموصى به: {m['drivers_required']} · الحد الأدنى النظري: "
          f"{m['drivers_theoretical_minimum']}")
    print(f"المسافة: {m['total_distance_km']} كم · القيادة: "
          f"{m['total_drive_minutes']} د · الانتظار: {m['total_wait_minutes']} د")
    print(f"التكلفة: {m['estimated_cost']} · زمن الحل: {m['solve_ms']} مللي ثانية")
    print(f"مزوّد الطرق: {m['routing_provider']} (تقديري: {m['routing_estimated']})")

    print("\nالرحلات:")
    for route in result.solution.used_routes():
        vehicle = problem.vehicles[route.vehicle_index]
        ev = route.evaluation
        print(f"  • {vehicle.label}: {len(route.sequence)} محطة · "
              f"{ev.distance_km:.1f} كم · عمل {ev.working_minutes/60:.2f} س · "
              f"بداية {to_datetime(ev.start_at).astimezone(TZ):%H:%M} "
              f"نهاية {to_datetime(ev.end_at).astimezone(TZ):%H:%M}")
        for timing in ev.timings:
            node = problem.nodes[timing.node_index]
            print(f"      {to_datetime(timing.arrival).astimezone(TZ):%H:%M} "
                  f"{node.label}  (انتظار {timing.wait_minutes:.0f} د، "
                  f"مقطع {timing.leg_km:.1f} كم / {timing.leg_minutes:.0f} د)")

    print(f"\nالتحذيرات ({len(result.warnings)}):")
    for warning in result.warnings[:8]:
        print(f"  [{warning.severity}] {warning.warning_type}")
        print(f"      السبب: {warning.reason_ar}")
        print(f"      المتأثر: {warning.affected_entity_ar}")
        print(f"      الإجراء: {warning.suggested_action_ar}")

    if result.improvement:
        imp = result.improvement
        print(f"\nمقابل خطة الأساس ({imp['baseline_label_ar']}):")
        for key in ("drivers", "drive_minutes", "distance_km", "cost"):
            row = imp[key]
            print(f"  {key}: {row['baseline']} ← {row['optimized']} "
                  f"({row['improvement_pct']}%)")

    print("\nتقدير السائقين:")
    for estimate in result.estimations:
        print(f"  المركز {estimate.hub_id}: نظري {estimate.theoretical_minimum} · "
              f"موصى به {estimate.recommended} · متوفر {estimate.available} · "
              f"مستخدم {estimate.used} · الفارق {estimate.gap}")
        for item in estimate.justification:
            print(f"      + {item['drivers']} — {item['label_ar']}: {item['detail_ar']}")

    # -------------------------------------------------- التحقق بالحل المضبوط
    print("\n" + "=" * 70)
    print("التحقق: مقارنة الاستدلال بالحل المضبوط على مسألة صغيرة")
    print("=" * 70)
    small = build(shipment_count=3, vehicle_count=2)
    heuristic = run_engine(
        small, options=SolveOptions(time_limit_seconds=2.0, seed=7),
        compute_reference_plan=False,
    )
    heuristic_vector = objective_vector(heuristic.solution, small)
    exact = solve_exact(small)
    if exact is None:
        print("  المسألة أكبر من حدود الحل المضبوط")
    else:
        exact_vector = objective_vector(exact, small)
        print(f"  الاستدلال : سائقون {heuristic_vector[1]:.0f} · "
              f"قيادة {heuristic_vector[2]:.1f} د · مسافة {heuristic_vector[3]:.2f} كم")
        print(f"  المضبوط   : سائقون {exact_vector[1]:.0f} · "
              f"قيادة {exact_vector[2]:.1f} د · مسافة {exact_vector[3]:.2f} كم")
        gap = (heuristic_vector[2] - exact_vector[2]) / max(exact_vector[2], 1e-9) * 100
        print(f"  الفجوة في زمن القيادة: {gap:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
