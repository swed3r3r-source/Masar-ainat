"""الإدراج الديناميكي للطلبات الفورية (§7 / HC-17).

الطلب الفوري **لا يدخل** التحسين الأسبوعي الأصلي؛ تلك الخطة تبقى مرجعًا
ثابتًا للمقارنة. بدل ذلك يُجرَّب إدراجه في جداول السائقين النشطين على نسخة
حية، بحيث:

* إن كان السائق قد بدأ العمل، يُحسب المسار **من موقعه الحالي** لا من المركز.
* المحطات المنفَّذة مسبقًا مقفلة ولا يُعاد ترتيبها.
* لا يُقبل الإدراج إن خرق أي قيد صلب لأي شحنة قائمة — تُعاد الأسباب صراحةً.
"""

from __future__ import annotations

from dataclasses import dataclass

from masar_core.constants import UnplannableReason

from .evaluate import Violation, evaluate_route
from .model import Node, NodeKind, Problem, ShipmentSpec
from .objective import RoutePlan, Solution
from .solver import InsertionCandidate, RouteOptimizer, SolveOptions


@dataclass(slots=True)
class InsertionOption:
    route_index: int
    driver_id: str | None
    vehicle_label: str
    added_minutes: float
    added_km: float
    new_end_at: float
    pickup_position: int
    delivery_position: int
    sequence: list[int]
    #: أقل هامش متبقٍ لأي شحنة في الرحلة بعد الإدراج (كلما زاد كان أأمن)
    min_slack_minutes: float


@dataclass(slots=True)
class InsertionResult:
    feasible: bool
    options: list[InsertionOption]
    rejections: list[Violation]

    @property
    def best(self) -> InsertionOption | None:
        return self.options[0] if self.options else None


def plan_on_demand_insertion(
    problem: Problem,
    solution: Solution,
    shipment_index: int,
    *,
    max_options: int = 5,
) -> InsertionResult:
    """يقيّم إدراج طلب فوري في كل الرحلات الجارية ويرتب الخيارات."""
    optimizer = RouteOptimizer(problem, SolveOptions(improvement_enabled=False))
    shipment = problem.shipments[shipment_index]

    options: list[InsertionOption] = []
    rejections: list[Violation] = []

    for route in solution.routes:
        vehicle = problem.vehicles[route.vehicle_index]
        if shipment.hub_id is not None and vehicle.hub_id != shipment.hub_id:
            continue

        before = route.evaluation
        locked = len(vehicle.locked_prefix)
        best_for_route: InsertionCandidate | None = None
        route_rejection: Violation | None = None

        for pickup_pos in range(locked, len(route.sequence) + 1):
            for delivery_pos in range(pickup_pos + 1, len(route.sequence) + 2):
                sequence = list(route.sequence)
                sequence.insert(pickup_pos, shipment.pickup_node)
                sequence.insert(delivery_pos, shipment.delivery_node)
                evaluation = evaluate_route(problem, vehicle, sequence)
                if not evaluation.feasible:
                    if route_rejection is None and evaluation.violations:
                        route_rejection = evaluation.violations[0]
                    continue
                delta = evaluation.drive_minutes - before.drive_minutes
                if best_for_route is None or delta < best_for_route.delta:
                    best_for_route = InsertionCandidate(
                        route_index=route.vehicle_index,
                        pickup_position=pickup_pos,
                        delivery_position=delivery_pos,
                        delta=delta,
                        evaluation=evaluation,
                        sequence=sequence,
                    )

        if best_for_route is not None:
            evaluation = best_for_route.evaluation
            options.append(InsertionOption(
                route_index=route.vehicle_index,
                driver_id=vehicle.driver_id,
                vehicle_label=vehicle.label or f"رحلة {route.vehicle_index + 1}",
                added_minutes=evaluation.working_minutes - before.working_minutes,
                added_km=evaluation.distance_km - before.distance_km,
                new_end_at=evaluation.end_at,
                pickup_position=best_for_route.pickup_position,
                delivery_position=best_for_route.delivery_position,
                sequence=best_for_route.sequence,
                min_slack_minutes=(
                    evaluation.min_slack_minutes
                    if evaluation.min_slack_minutes != float("inf") else 1e9
                ),
            ))
        elif route_rejection is not None:
            rejections.append(route_rejection)

    # الترتيب: أقل إضافة زمنية، ثم أعلى هامش أمان
    options.sort(key=lambda option: (option.added_minutes, -option.min_slack_minutes))
    del optimizer

    if not options and not rejections:
        rejections.append(Violation(
            "DYN-00", UnplannableReason.NO_FEASIBLE_DRIVER,
            f"الشحنة {shipment.reference}: لا توجد رحلة نشطة في مركز الانطلاق المطلوب",
            shipment_index=shipment_index,
        ))

    return InsertionResult(
        feasible=bool(options),
        options=options[:max_options],
        rejections=rejections,
    )


def make_current_position_node(
    index: int, lat: float, lon: float, hub_id: str, label: str = "الموقع الحالي للسائق"
) -> Node:
    """عقدة بداية تمثل الموقع اللحظي للسائق (HC-17)."""
    return Node(
        index=index,
        kind=NodeKind.START,
        lat=lat,
        lon=lon,
        label=label,
        service_minutes=0.0,
        hub_id=hub_id,
    )
