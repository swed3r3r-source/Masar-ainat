"""حلّ مضبوط للحالات الصغيرة — مرجع التحقق من جودة الاستدلال.

الغرض ليس التشغيل اليومي بل **إثبات صحة الاستدلال**: في الاختبارات نحلّ نفس
المسألة الصغيرة بالطريقتين ونتحقق أن ALNS بلغ الأمثل. هذا هو ما يجعل عبارة
«الخطة محسّنة» قابلة للتحقق بدل أن تكون ادعاءً.

الخوارزمية: تفريع وتحديد (Branch & Bound) على ترتيب المحطات، مع تشذيب:
* أسبقية الالتقاط قبل التسليم (لا تُولَّد الفروع المخالفة أصلًا).
* قطع النافذة الزمنية: أي وصول يتجاوز نهاية النافذة يقطع الفرع فورًا.
* حدّ أدنى متفائل: كلفة الجزء المبني + أرخص خروج من كل عقدة متبقية.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

from .evaluate import RouteEvaluation, evaluate_route
from .model import NodeKind, Problem, VehicleSpec
from .objective import RoutePlan, Solution, objective_vector


@dataclass(slots=True)
class ExactResult:
    sequence: list[int]
    evaluation: RouteEvaluation
    cost: float
    nodes_explored: int
    proven_optimal: bool


def _route_cost(evaluation: RouteEvaluation, problem: Problem) -> float:
    settings = problem.settings
    return (
        evaluation.drive_minutes
        + evaluation.distance_km * settings.cost_per_km * 0.5
        + evaluation.wait_minutes * 0.3
    )


def solve_single_vehicle_exact(
    problem: Problem,
    vehicle: VehicleSpec,
    shipment_indices: list[int],
    *,
    node_limit: int = 12,
    max_explored: int = 2_000_000,
) -> ExactResult | None:
    """يعيد أفضل تسلسل ممكن لمركبة واحدة، أو ``None`` إذا لا يوجد حل صالح."""
    nodes_to_visit: list[int] = []
    for shipment_index in shipment_indices:
        shipment = problem.shipments[shipment_index]
        nodes_to_visit.append(shipment.pickup_node)
        nodes_to_visit.append(shipment.delivery_node)

    if len(nodes_to_visit) > node_limit:
        return None

    pickup_of: dict[int, int] = {}
    for shipment_index in shipment_indices:
        shipment = problem.shipments[shipment_index]
        pickup_of[shipment.delivery_node] = shipment.pickup_node

    best: ExactResult | None = None
    explored = 0
    exhausted = False

    def cheapest_outgoing(node_index: int, remaining: frozenset[int]) -> float:
        if not remaining:
            return 0.0
        return min(problem.travel.minutes(node_index, other) for other in remaining)

    def recurse(sequence: list[int], remaining: frozenset[int], visited: set[int]) -> None:
        nonlocal best, explored, exhausted
        explored += 1
        if explored > max_explored:
            exhausted = True
            return

        if not remaining:
            evaluation = evaluate_route(problem, vehicle, sequence)
            if evaluation.feasible:
                cost = _route_cost(evaluation, problem)
                if best is None or cost < best.cost:
                    best = ExactResult(list(sequence), evaluation, cost, explored, True)
            return

        # حد أدنى متفائل على الجزء المتبقي
        if best is not None and sequence:
            prefix = evaluate_route(
                problem, vehicle, sequence, stop_on_first_violation=True, partial=True)
            if not prefix.feasible:
                return
            lower_bound = _route_cost(prefix, problem) + sum(
                cheapest_outgoing(node, remaining - {node}) for node in remaining
            )
            if lower_bound >= best.cost:
                return
        elif sequence:
            prefix = evaluate_route(
                problem, vehicle, sequence, stop_on_first_violation=True, partial=True)
            if not prefix.feasible:
                return

        for node_index in sorted(remaining):
            if node_index in pickup_of and pickup_of[node_index] not in visited:
                continue  # التسليم قبل الالتقاط — فرع غير مولَّد أصلًا
            sequence.append(node_index)
            visited.add(node_index)
            recurse(sequence, remaining - {node_index}, visited)
            visited.discard(node_index)
            sequence.pop()

    recurse([], frozenset(nodes_to_visit), set())
    if best is not None:
        best.proven_optimal = not exhausted
        best.nodes_explored = explored
    return best


def solve_exact(
    problem: Problem,
    *,
    max_shipments: int = 7,
    max_vehicles: int = 3,
) -> Solution | None:
    """حل مضبوط متعدد المركبات بتعداد كل التقسيمات — للحالات الصغيرة جدًا فقط."""
    shipments = list(range(len(problem.shipments)))
    vehicles = problem.vehicles
    if len(shipments) > max_shipments or len(vehicles) > max_vehicles:
        return None

    best_solution: Solution | None = None
    best_vector: tuple[float, ...] | None = None

    # كل إسناد ممكن للشحنات على المركبات (بما فيها ترك شحنة بلا إسناد)
    options_per_shipment = [list(range(len(vehicles))) + [-1] for _ in shipments]
    for assignment in itertools.product(*options_per_shipment):
        groups: dict[int, list[int]] = {}
        unassigned_indices: list[int] = []
        valid = True
        for shipment_index, vehicle_index in enumerate(assignment):
            if vehicle_index == -1:
                unassigned_indices.append(shipment_index)
                continue
            shipment = problem.shipments[shipment_index]
            vehicle = vehicles[vehicle_index]
            if shipment.hub_id is not None and vehicle.hub_id != shipment.hub_id:
                valid = False
                break
            groups.setdefault(vehicle_index, []).append(shipment_index)
        if not valid:
            continue

        solution = Solution(
            routes=[RoutePlan(v.index, [], RouteEvaluation(feasible=True)) for v in vehicles]
        )
        feasible = True
        for vehicle_index, group in groups.items():
            result = solve_single_vehicle_exact(problem, vehicles[vehicle_index], group)
            if result is None:
                feasible = False
                break
            route = solution.routes[vehicle_index]
            route.sequence = result.sequence
            route.evaluation = result.evaluation
            for shipment_index in group:
                solution.assignment[shipment_index] = vehicles[vehicle_index].index
        if not feasible:
            continue

        from .evaluate import Violation
        from masar_core.constants import UnplannableReason

        for shipment_index in unassigned_indices:
            solution.unassigned[shipment_index] = Violation(
                "EXACT", UnplannableReason.NO_FEASIBLE_DRIVER,
                f"الشحنة {problem.shipments[shipment_index].reference}: "
                "تُركت بلا إسناد في الحل المضبوط",
                shipment_index=shipment_index,
            )

        vector = objective_vector(solution, problem)
        if best_vector is None or vector < best_vector:
            best_vector = vector
            best_solution = solution

    return best_solution
