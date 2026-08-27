"""تقدير عدد السائقين المطلوب (§15) — بحدود دنيا مبرهنة لا بأرقام ثابتة.

يُنتج التقدير أربعة أرقام وسببًا لكل سائق إضافي:

* **الحد الأدنى النظري** — أكبر حدّ أدنى مبرهن رياضيًا، ولا يمكن لأي خطة
  مهما بلغت جودتها أن تنزل تحته.
* **العدد الموصى به عمليًا** — ما استخدمه المحرك فعليًا + احتياطي للطلبات
  الفورية.
* **المتوفر** و**المستخدم** والفارق بينهما.
* **أثر تقليل أو زيادة السائقين على SLA** — يُحسب بإعادة حل المسألة بعدد
  أقل، لا بالتخمين.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .evaluate import evaluate_route
from .model import NodeKind, Problem, ProblemSettings
from .objective import Solution
from .solver import RouteOptimizer, SolveOptions

#: تسمية عربية لأسباب الحاجة إلى سائق إضافي
REASON_LABELS = {
    "WORKLOAD": "حجم العمل الإجمالي مقابل طول الوردية",
    "MIXING": "قيد منع خلط أنواع الجهات على السائق نفسه",
    "TIME_WINDOWS": "تعارض النوافذ الزمنية (شحنات لا يمكن تسلسلها)",
    "LONG_HAUL": "الرحلات البعيدة وحد الرحلة البعيدة الواحدة يوميًا",
    "GEOGRAPHY": "تباعد جغرافي يفرض مسارات منفصلة",
    "ON_DEMAND_BUFFER": "احتياطي للطلبات الفورية",
}


@dataclass(slots=True)
class DriverEstimate:
    hub_id: str
    service_date: str
    theoretical_minimum: int
    recommended: int
    available: int
    used: int
    gap: int
    workload_minutes: float
    justification: list[dict[str, object]] = field(default_factory=list)
    sla_impact: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "hub_id": self.hub_id,
            "service_date": self.service_date,
            "theoretical_minimum": self.theoretical_minimum,
            "recommended": self.recommended,
            "available": self.available,
            "used": self.used,
            "gap": self.gap,
            "workload_minutes": round(self.workload_minutes, 1),
            "justification": self.justification,
            "sla_impact": self.sla_impact,
        }


def _shipment_workload(problem: Problem, shipment_index: int) -> float:
    """أدنى زمن لا مفر منه لخدمة شحنة: خدمة الطرفين + القيادة بينهما."""
    shipment = problem.shipments[shipment_index]
    nodes = problem.nodes
    settings = problem.settings
    leg = max(
        problem.travel.minutes(shipment.pickup_node, shipment.delivery_node),
        settings.min_event_gap_minutes,
    )
    return (
        nodes[shipment.pickup_node].service_minutes
        + nodes[shipment.delivery_node].service_minutes
        + leg
    )


def lower_bound_workload(problem: Problem, shipment_indices: list[int]) -> int:
    """LB1: إجمالي العمل الحتمي ÷ طول الوردية."""
    if not shipment_indices:
        return 0
    total = sum(_shipment_workload(problem, index) for index in shipment_indices)
    shift = problem.settings.max_shift_minutes
    return max(1, math.ceil(total / shift)) if total > 0 else 0


def lower_bound_mixing(problem: Problem, shipment_indices: list[int]) -> tuple[int, dict[str, int]]:
    """LB2: قيد عدم الخلط يفصل العمل إلى مجموعات لا تتقاسم سائقًا.

    مبرهن: إذا كان صنفان لا يجوز جمعهما على سائق واحد، فالحد الأدنى هو مجموع
    الحدود الدنيا لكل صنف على حدة.
    """
    settings = problem.settings
    if not settings.enforce_facility_mixing_rule:
        return lower_bound_workload(problem, shipment_indices), {}

    from .evaluate import RESTRICTED_MIXING_CLASSES

    buckets: dict[str, float] = {}
    for index in shipment_indices:
        shipment = problem.shipments[index]
        pickup_class = problem.nodes[shipment.pickup_node].mixing_class or "OTHER"
        if (
            pickup_class not in RESTRICTED_MIXING_CLASSES
            or pickup_class in settings.mixing_exempt_classes
        ):
            pickup_class = "_MIXABLE"
        buckets[pickup_class] = buckets.get(pickup_class, 0.0) + _shipment_workload(
            problem, index)

    shift = settings.max_shift_minutes
    per_class = {
        name: max(1, math.ceil(minutes / shift))
        for name, minutes in buckets.items() if minutes > 0
    }
    return sum(per_class.values()), per_class


def lower_bound_time_conflicts(problem: Problem, shipment_indices: list[int]) -> int:
    """LB3: أكبر مجموعة شحنات لا يمكن لسائق واحد أن يخدم اثنتين منها.

    شحنتان متعارضتان إذا استحال — بأي ترتيب — خدمتهما معًا ضمن النوافذ وSLA.
    الحد الأدنى هو حجم أكبر «زمرة تعارض»؛ نحسب حدًا أدنى لها بخوارزمية
    جشعة (تلوين تسلسلي)، وهو حدّ أدنى صحيح دائمًا حتى لو لم يكن الأكبر.
    """
    if len(shipment_indices) < 2:
        return len(shipment_indices)

    vehicle = problem.vehicles[0] if problem.vehicles else None
    if vehicle is None:
        return 0

    def compatible(a: int, b: int) -> bool:
        sa, sb = problem.shipments[a], problem.shipments[b]
        for order in (
            [sa.pickup_node, sa.delivery_node, sb.pickup_node, sb.delivery_node],
            [sb.pickup_node, sb.delivery_node, sa.pickup_node, sa.delivery_node],
            [sa.pickup_node, sb.pickup_node, sa.delivery_node, sb.delivery_node],
            [sa.pickup_node, sb.pickup_node, sb.delivery_node, sa.delivery_node],
            [sb.pickup_node, sa.pickup_node, sb.delivery_node, sa.delivery_node],
            [sb.pickup_node, sa.pickup_node, sa.delivery_node, sb.delivery_node],
        ):
            if evaluate_route(problem, vehicle, order).feasible:
                return True
        return False

    # زمرة جشعة: نبني مجموعة كل عنصرين فيها متعارضان
    clique: list[int] = []
    for candidate in shipment_indices:
        if all(not compatible(candidate, member) for member in clique):
            clique.append(candidate)
    return max(1, len(clique))


def estimate_drivers(
    problem: Problem,
    solution: Solution,
    *,
    hub_id: str,
    service_date: str,
    available_drivers: int,
    on_demand_buffer_ratio: float = 0.15,
    evaluate_sla_impact: bool = True,
) -> DriverEstimate:
    """يحسب التقدير الكامل مع تبرير كل سائق إضافي."""
    shipment_indices = [
        shipment.index for shipment in problem.shipments
        if shipment.hub_id in (None, hub_id)
    ]
    planned = [index for index in shipment_indices if index not in solution.unassigned]

    lb_workload = lower_bound_workload(problem, planned)
    lb_mixing, per_class = lower_bound_mixing(problem, planned)
    lb_conflicts = lower_bound_time_conflicts(problem, planned)

    theoretical = max(lb_workload, lb_mixing, lb_conflicts)
    used = len([route for route in solution.used_routes()
                if problem.vehicles[route.vehicle_index].hub_id == hub_id])

    buffer = math.ceil(used * on_demand_buffer_ratio) if used else 0
    recommended = used + buffer

    workload_minutes = sum(_shipment_workload(problem, index) for index in planned)

    justification: list[dict[str, object]] = [{
        "code": "WORKLOAD",
        "label_ar": REASON_LABELS["WORKLOAD"],
        "drivers": lb_workload,
        "detail_ar": (
            f"إجمالي العمل الحتمي {workload_minutes / 60:.1f} ساعة "
            f"÷ وردية {problem.settings.max_shift_minutes / 60:.1f} ساعة"
        ),
    }]

    if lb_mixing > lb_workload:
        breakdown = "، ".join(f"{name}: {count}" for name, count in sorted(per_class.items()))
        justification.append({
            "code": "MIXING",
            "label_ar": REASON_LABELS["MIXING"],
            "drivers": lb_mixing - lb_workload,
            "detail_ar": (
                f"قيد عدم الخلط يفصل العمل إلى مجموعات مستقلة ({breakdown}) "
                f"فيرتفع الحد الأدنى من {lb_workload} إلى {lb_mixing}"
            ),
        })

    if lb_conflicts > max(lb_workload, lb_mixing):
        justification.append({
            "code": "TIME_WINDOWS",
            "label_ar": REASON_LABELS["TIME_WINDOWS"],
            "drivers": lb_conflicts - max(lb_workload, lb_mixing),
            "detail_ar": (
                f"وُجدت {lb_conflicts} شحنة لا يمكن لسائق واحد خدمة أي اثنتين منها "
                "معًا ضمن النوافذ وSLA"
            ),
        })

    long_haul_routes = len([
        route for route in solution.used_routes()
        if route.evaluation.is_long_haul
        and problem.vehicles[route.vehicle_index].hub_id == hub_id
    ])
    if long_haul_routes > 0:
        justification.append({
            "code": "LONG_HAUL",
            "label_ar": REASON_LABELS["LONG_HAUL"],
            "drivers": max(0, long_haul_routes - problem.settings.max_long_haul_per_driver_per_day),
            "detail_ar": (
                f"{long_haul_routes} رحلة بعيدة، والحد المسموح "
                f"{problem.settings.max_long_haul_per_driver_per_day} لكل سائق يوميًا"
            ),
        })

    if used > theoretical:
        justification.append({
            "code": "GEOGRAPHY",
            "label_ar": REASON_LABELS["GEOGRAPHY"],
            "drivers": used - theoretical,
            "detail_ar": (
                f"استخدم المحرك {used} سائقًا مقابل حد أدنى نظري {theoretical}؛ "
                "الفارق ناتج عن التباعد الجغرافي وتوزيع النوافذ الزمنية"
            ),
        })

    if buffer:
        justification.append({
            "code": "ON_DEMAND_BUFFER",
            "label_ar": REASON_LABELS["ON_DEMAND_BUFFER"],
            "drivers": buffer,
            "detail_ar": f"احتياطي {on_demand_buffer_ratio:.0%} للطلبات الفورية",
        })

    sla_impact: dict[str, object] = {}
    if evaluate_sla_impact and used > 1:
        sla_impact = _sla_impact(problem, hub_id, used)

    return DriverEstimate(
        hub_id=hub_id,
        service_date=service_date,
        theoretical_minimum=theoretical,
        recommended=recommended,
        available=available_drivers,
        used=used,
        gap=available_drivers - recommended,
        workload_minutes=workload_minutes,
        justification=justification,
        sla_impact=sla_impact,
    )


def _sla_impact(problem: Problem, hub_id: str, used: int) -> dict[str, object]:
    """يعيد الحل بعدد أقل من السائقين لقياس الأثر الفعلي على SLA."""
    import copy
    import dataclasses

    scenarios: list[dict[str, object]] = []
    for delta in (-1, -2):
        target = used + delta
        if target < 1:
            continue
        reduced = copy.copy(problem)
        kept = [
            vehicle for vehicle in problem.vehicles
            if vehicle.hub_id != hub_id
        ] + [
            vehicle for vehicle in problem.vehicles
            if vehicle.hub_id == hub_id
        ][:target]
        # إعادة الترقيم إلزامية: ``RoutePlan.vehicle_index`` يُفهرس داخل قائمة
        # مركبات المسألة نفسها. تمرير مركبات بأرقامها الأصلية بعد الحذف يجعل
        # الحلّال يقرأ خارج حدود القائمة (خطأ حقيقي كشفه اختبار السيناريو ١٤).
        reduced.vehicles = [
            dataclasses.replace(vehicle, index=position)
            for position, vehicle in enumerate(kept)
        ]
        optimizer = RouteOptimizer(
            reduced, SolveOptions(time_limit_seconds=3.0, improvement_enabled=False))
        result = optimizer.solve()
        scenarios.append({
            "drivers": target,
            "delta": delta,
            "unplannable": len(result.unassigned),
            "detail_ar": (
                f"بـ {target} سائقًا تصبح {len(result.unassigned)} شحنة غير قابلة للتخطيط"
            ),
        })
    return {"scenarios": scenarios, "baseline_drivers": used}
