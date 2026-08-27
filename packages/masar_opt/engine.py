"""منسّق المحرك: يبني المسألة، يشغّل الخلفية، يُخرج خطة قابلة للتفسير.

المخرج ليس مجرد ترتيب محطات، بل:
* مقاييس كاملة تطابق ما تطلبه شاشة المعاينة (§22).
* تحذيرات، كل واحد منها بسبب وجهة متأثرة وإجراء مقترح (§34).
* تتبّع لكل مستوى من مستويات الهدف (§11) — لتفسير قرار المحرك.
* نسبة تحسين **مقابل خطة أساس معلنة** — أو لا نسبة إطلاقًا إن لم توجد.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from masar_core.constants import FACILITY_MIXING_CLASS, Severity, WarningType
from masar_core.errors import DependencyUnavailable, OptimizationFailed

from .backends import get_backend
from .estimation import DriverEstimate, estimate_drivers
from .evaluate import Violation, evaluate_route
from .model import (
    Node,
    NodeKind,
    Problem,
    ProblemSettings,
    ShipmentSpec,
    VehicleSpec,
    from_datetime,
    to_datetime,
)
from .objective import Solution, explain, objective_vector
from .routing import build_provider, build_travel_matrix
from .solver import RouteOptimizer, SolveOptions

ENGINE_NAME = "masar-opt"
ENGINE_VERSION = "1.0.0"


# ===================================================== مدخلات المحرك ========

@dataclass(slots=True)
class HubInput:
    hub_id: str
    code: str
    name_ar: str
    lat: float
    lon: float
    opens_at: dt.datetime
    closes_at: dt.datetime


@dataclass(slots=True)
class ShipmentInput:
    shipment_id: str
    reference: str
    hub_id: str
    pickup_facility_id: str
    pickup_facility_type: str
    pickup_name: str
    pickup_lat: float
    pickup_lon: float
    pickup_window_from: dt.datetime
    pickup_window_to: dt.datetime
    pickup_service_minutes: float
    dropoff_facility_id: str
    dropoff_facility_type: str
    dropoff_name: str
    dropoff_lat: float
    dropoff_lon: float
    dropoff_service_minutes: float
    sla_deadline: dt.datetime
    piece_count: int = 1
    service_type: str = "ROUTINE"
    temperature_mode: str = "AMBIENT"
    is_on_demand: bool = False
    priority: int = 0


@dataclass(slots=True)
class VehicleInput:
    hub_id: str
    label: str
    earliest_start: dt.datetime
    latest_end: dt.datetime
    max_shift_minutes: float
    driver_id: str | None = None
    vehicle_id: str | None = None
    start_lat: float | None = None
    start_lon: float | None = None


@dataclass(slots=True)
class PlanWarning:
    warning_type: str
    severity: str
    reason_ar: str
    affected_entity_ar: str
    suggested_action_ar: str
    route_index: int | None = None
    shipment_id: str | None = None
    hub_id: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PlanResult:
    solution: Solution
    problem: Problem
    metrics: dict[str, Any]
    warnings: list[PlanWarning]
    unplannable: list[dict[str, Any]]
    objective_trace: list[dict[str, Any]]
    estimations: list[DriverEstimate]
    solve_ms: int
    engine_name: str = ENGINE_NAME
    engine_version: str = ENGINE_VERSION
    routing_provider: str = ""
    routing_estimated: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)
    baseline_kind: str = "NONE"
    improvement: dict[str, Any] | None = None


# ================================================== بناء نموذج المسألة ======

def build_problem(
    *,
    service_date: dt.date,
    hubs: Sequence[HubInput],
    shipments: Sequence[ShipmentInput],
    vehicles: Sequence[VehicleInput],
    effective_settings: dict[str, Any],
    routing_provider_name: str | None = None,
    fallback_to_estimate: bool = False,
    timezone_name: str = "Asia/Riyadh",
) -> Problem:
    settings = ProblemSettings.from_effective(effective_settings)

    nodes: list[Node] = []
    hub_start_index: dict[str, int] = {}
    for hub in hubs:
        hub_start_index[hub.hub_id] = len(nodes)
        nodes.append(Node(
            index=len(nodes), kind=NodeKind.START,
            lat=hub.lat, lon=hub.lon, label=f"مركز الانطلاق: {hub.name_ar}",
            hub_id=hub.hub_id, external_id=hub.hub_id,
        ))

    shipment_specs: list[ShipmentSpec] = []
    for shipment in shipments:
        pickup_index = len(nodes)
        nodes.append(Node(
            index=pickup_index, kind=NodeKind.PICKUP,
            lat=shipment.pickup_lat, lon=shipment.pickup_lon,
            label=f"التقاط: {shipment.pickup_name}",
            service_minutes=shipment.pickup_service_minutes,
            window_from=from_datetime(shipment.pickup_window_from),
            window_to=from_datetime(shipment.pickup_window_to),
            shipment_index=len(shipment_specs),
            facility_id=shipment.pickup_facility_id,
            facility_type=shipment.pickup_facility_type,
            mixing_class=FACILITY_MIXING_CLASS.get(shipment.pickup_facility_type, "OTHER"),
            hub_id=shipment.hub_id,
            external_id=shipment.shipment_id,
        ))
        delivery_index = len(nodes)
        nodes.append(Node(
            index=delivery_index, kind=NodeKind.DELIVERY,
            lat=shipment.dropoff_lat, lon=shipment.dropoff_lon,
            label=f"تسليم: {shipment.dropoff_name}",
            service_minutes=shipment.dropoff_service_minutes,
            window_to=from_datetime(shipment.sla_deadline),
            shipment_index=len(shipment_specs),
            facility_id=shipment.dropoff_facility_id,
            facility_type=shipment.dropoff_facility_type,
            mixing_class=FACILITY_MIXING_CLASS.get(shipment.dropoff_facility_type, "OTHER"),
            hub_id=shipment.hub_id,
            external_id=shipment.shipment_id,
        ))
        shipment_specs.append(ShipmentSpec(
            index=len(shipment_specs),
            shipment_id=shipment.shipment_id,
            reference=shipment.reference,
            pickup_node=pickup_index,
            delivery_node=delivery_index,
            sla_deadline=from_datetime(shipment.sla_deadline),
            piece_count=shipment.piece_count,
            service_type=shipment.service_type,
            temperature_mode=shipment.temperature_mode,
            hub_id=shipment.hub_id,
            priority=shipment.priority,
            is_on_demand=shipment.is_on_demand,
        ))

    _apply_same_facility_ordering(shipment_specs, nodes)

    vehicle_specs: list[VehicleSpec] = []
    for vehicle in vehicles:
        if vehicle.start_lat is not None and vehicle.start_lon is not None:
            start_index = len(nodes)
            nodes.append(Node(
                index=start_index, kind=NodeKind.START,
                lat=vehicle.start_lat, lon=vehicle.start_lon,
                label=f"نقطة بداية: {vehicle.label}",
                hub_id=vehicle.hub_id,
            ))
        else:
            start_index = hub_start_index[vehicle.hub_id]
        vehicle_specs.append(VehicleSpec(
            index=len(vehicle_specs),
            hub_id=vehicle.hub_id,
            start_node=start_index,
            earliest_start=from_datetime(vehicle.earliest_start),
            latest_end=from_datetime(vehicle.latest_end),
            max_shift_minutes=vehicle.max_shift_minutes,
            max_long_haul=int(effective_settings.get(
                "max_long_haul_per_driver_per_day", 1)),
            driver_id=vehicle.driver_id,
            vehicle_id=vehicle.vehicle_id,
            label=vehicle.label,
        ))

    problem = Problem(
        nodes=nodes,
        shipments=shipment_specs,
        vehicles=vehicle_specs,
        settings=settings,
        service_date=service_date,
        meta={"hub_ids": [hub.hub_id for hub in hubs]},
    )

    provider = build_provider(routing_provider_name)
    problem.travel = build_travel_matrix(
        nodes, provider,
        peak_periods=(
            effective_settings.get("peak_periods")
            if effective_settings.get("use_time_dependent_travel") else None
        ),
        peak_multiplier=float(effective_settings.get("peak_travel_multiplier", 1.0)),
        timezone_name=timezone_name,
        fallback_to_estimate=fallback_to_estimate,
    )
    return problem


def _apply_same_facility_ordering(
    shipments: list[ShipmentSpec], nodes: list[Node]
) -> None:
    """HC-11: التقاطان من نفس الجهة في اليوم — الأول يُسلَّم قبل تنفيذ الثاني.

    يُولَّد القيد آليًا من البيانات (لا يُكتب يدويًا)، بترتيب الالتقاطين حسب
    بداية النافذة الزمنية.
    """
    by_facility: dict[str, list[ShipmentSpec]] = {}
    for shipment in shipments:
        facility = nodes[shipment.pickup_node].facility_id
        if facility:
            by_facility.setdefault(facility, []).append(shipment)

    for group in by_facility.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda s: nodes[s.pickup_node].window_from or 0.0)
        for position, shipment in enumerate(group[:-1]):
            later = tuple(other.index for other in group[position + 1:])
            shipment.delivery_before_pickups = later


# ======================================================== تشغيل المحرك ======

def run_engine(
    problem: Problem,
    *,
    options: SolveOptions | None = None,
    backend_name: str | None = None,
    baseline_assignment: dict[int, int] | None = None,
    available_drivers_by_hub: dict[str, int] | None = None,
    compute_reference_plan: bool = True,
) -> PlanResult:
    options = options or SolveOptions()
    backend = get_backend(backend_name)

    started = time.monotonic()
    try:
        solution, diagnostics = backend.solve(problem, options, baseline_assignment)
    except DependencyUnavailable:
        raise
    except Exception as exc:  # pragma: no cover - شبكة أمان
        raise OptimizationFailed(
            f"فشل محرك التحسين: {exc}", backend=backend.name
        ) from exc
    solve_ms = int((time.monotonic() - started) * 1000)

    # فحص مستقل بعد الحل — لا نثق بمخرج المحرك دون إعادة تحقق
    for route in solution.routes:
        vehicle = problem.vehicles[route.vehicle_index]
        recheck = evaluate_route(
            problem, vehicle, route.sequence, stop_on_first_violation=False)
        if not recheck.feasible:
            raise OptimizationFailed(
                "فشل فحص ما بعد الحل: المحرك أنتج رحلة تخرق قيدًا صلبًا — "
                + "؛ ".join(v.message_ar for v in recheck.violations[:3]),
                route_index=route.vehicle_index,
            )
        route.evaluation = recheck

    vector = objective_vector(solution, problem, baseline=baseline_assignment)
    warnings = build_warnings(problem, solution)
    unplannable = [
        {
            "shipment_id": problem.shipments[index].shipment_id,
            "reference": problem.shipments[index].reference,
            "rule": violation.rule,
            "reason": str(violation.reason),
            "message_ar": violation.message_ar,
            "slack_minutes": violation.slack_minutes,
        }
        for index, violation in sorted(solution.unassigned.items())
    ]

    improvement = None
    baseline_kind = "NONE"
    if baseline_assignment:
        baseline_kind = "PREVIOUS_PLAN"
    elif compute_reference_plan:
        reference = _reference_plan(problem)
        if reference is not None:
            baseline_kind = "GREEDY_REFERENCE"
            improvement = _improvement(reference, vector)

    estimations: list[DriverEstimate] = []
    for hub_id in problem.meta.get("hub_ids", []):
        available = (available_drivers_by_hub or {}).get(hub_id, 0)
        estimations.append(estimate_drivers(
            problem, solution,
            hub_id=hub_id,
            service_date=problem.service_date.isoformat(),
            available_drivers=available,
        ))

    metrics = build_metrics(
        problem, solution, vector, warnings, estimations, solve_ms, improvement
    )

    return PlanResult(
        solution=solution,
        problem=problem,
        metrics=metrics,
        warnings=warnings,
        unplannable=unplannable,
        objective_trace=explain(vector),
        estimations=estimations,
        solve_ms=solve_ms,
        routing_provider=problem.travel.provider,
        routing_estimated=problem.travel.is_estimated,
        diagnostics=dict(diagnostics),
        baseline_kind=baseline_kind,
        improvement=improvement,
    )


def _reference_plan(problem: Problem) -> tuple[float, ...] | None:
    """خطة أساس جشعة تمثل «التخطيط اليدوي التقريبي» للمقارنة (§26).

    تُبنى بإدراج بسيط أول-ما-يصلح بلا ندم وبلا تحسين — أي ما يقارب ما يفعله
    مخطط بشري يوزّع الشحنات بالترتيب. **لا تُعرض نسبة تحسين بدونها.**
    """
    try:
        optimizer = RouteOptimizer(problem, SolveOptions(
            improvement_enabled=False, regret_k=1, time_limit_seconds=5.0))
        solution = optimizer.solve()
        return objective_vector(solution, problem)
    except Exception:  # pragma: no cover
        return None


def _improvement(baseline: tuple[float, ...], optimized: tuple[float, ...]) -> dict[str, Any]:
    def pct(base: float, new: float) -> float | None:
        if base <= 0:
            return None
        return round((base - new) / base * 100.0, 2)

    return {
        "baseline_kind": "GREEDY_REFERENCE",
        "baseline_label_ar": "خطة أساس جشعة (إدراج أول-ما-يصلح بلا تحسين)",
        "drivers": {"baseline": baseline[1], "optimized": optimized[1],
                    "improvement_pct": pct(baseline[1], optimized[1])},
        "drive_minutes": {"baseline": round(baseline[2], 1), "optimized": round(optimized[2], 1),
                          "improvement_pct": pct(baseline[2], optimized[2])},
        "distance_km": {"baseline": round(baseline[3], 2), "optimized": round(optimized[3], 2),
                        "improvement_pct": pct(baseline[3], optimized[3])},
        "cost": {"baseline": round(baseline[4], 2), "optimized": round(optimized[4], 2),
                 "improvement_pct": pct(baseline[4], optimized[4])},
        "unplannable": {"baseline": baseline[0], "optimized": optimized[0]},
    }


def build_metrics(
    problem: Problem,
    solution: Solution,
    vector: tuple[float, ...],
    warnings: list[PlanWarning],
    estimations: list[DriverEstimate],
    solve_ms: int,
    improvement: dict[str, Any] | None,
) -> dict[str, Any]:
    """المقاييس المطلوبة في شاشة المعاينة (§22) — من مصدر واحد لا من مصادر متعددة."""
    used = solution.used_routes()
    return {
        "shipment_count": len(problem.shipments),
        "planned_shipment_count": len(solution.assignment),
        "route_count": len(used),
        "day_count": 1,
        "hub_count": len(set(problem.vehicles[r.vehicle_index].hub_id for r in used)) if used else 0,
        "drivers_required": sum(e.recommended for e in estimations),
        "drivers_theoretical_minimum": sum(e.theoretical_minimum for e in estimations),
        "drivers_available": sum(e.available for e in estimations),
        "drivers_used": len(used),
        "unassigned_route_count": len([r for r in used if not problem.vehicles[r.vehicle_index].driver_id]),
        "unplannable_count": len(solution.unassigned),
        "warning_count": len(warnings),
        "total_distance_km": round(sum(r.evaluation.distance_km for r in used), 3),
        "total_drive_minutes": round(sum(r.evaluation.drive_minutes for r in used), 2),
        "total_service_minutes": round(sum(r.evaluation.service_minutes for r in used), 2),
        "total_wait_minutes": round(sum(r.evaluation.wait_minutes for r in used), 2),
        "total_working_minutes": round(sum(r.evaluation.working_minutes for r in used), 2),
        "estimated_cost": round(vector[4], 2),
        "fairness_index": round(vector[6], 3),
        "long_haul_route_count": len([r for r in used if r.evaluation.is_long_haul]),
        "solve_ms": solve_ms,
        "engine": f"{ENGINE_NAME} {ENGINE_VERSION}",
        "routing_provider": problem.travel.provider,
        "routing_estimated": problem.travel.is_estimated,
        "improvement": improvement,
    }


def build_warnings(problem: Problem, solution: Solution) -> list[PlanWarning]:
    """يبني التحذيرات — كل تحذير بسبب وجهة متأثرة وإجراء مقترح (§22/§34)."""
    warnings: list[PlanWarning] = []
    settings = problem.settings

    if problem.travel.is_estimated:
        warnings.append(PlanWarning(
            warning_type=WarningType.ESTIMATED_TRAVEL_TIME,
            severity=Severity.HIGH,
            reason_ar=(
                f"أزمنة القيادة تقديرية (مزوّد {problem.travel.provider}) وليست "
                "أزمنة طريق حقيقية"
            ),
            affected_entity_ar="الخطة بأكملها",
            suggested_action_ar=(
                "فعّل مزوّد طرق حقيقيًا (OSRM أو مزوّد تجاري) قبل اعتماد الخطة، "
                "أو اعتمدها صراحةً كخطة تقديرية"
            ),
            context={"provider": problem.travel.provider},
        ))

    for shipment_index, violation in solution.unassigned.items():
        shipment = problem.shipments[shipment_index]
        warnings.append(PlanWarning(
            warning_type=WarningType.UNPLANNABLE_SHIPMENT,
            severity=Severity.CRITICAL,
            reason_ar=violation.message_ar,
            affected_entity_ar=f"الشحنة {shipment.reference}",
            suggested_action_ar=_suggested_action(violation),
            shipment_id=shipment.shipment_id,
            hub_id=shipment.hub_id,
            context={"rule": violation.rule, "reason": str(violation.reason)},
        ))

    for route in solution.used_routes():
        vehicle = problem.vehicles[route.vehicle_index]
        evaluation = route.evaluation
        label = vehicle.label or f"رحلة {route.vehicle_index + 1}"

        if not vehicle.driver_id:
            warnings.append(PlanWarning(
                warning_type=WarningType.UNASSIGNED_ROUTE,
                severity=Severity.HIGH,
                reason_ar="الرحلة مبنية لكنها بلا سائق مُسند",
                affected_entity_ar=label,
                suggested_action_ar="أسند سائقًا من شاشة الإسناد قبل نشر خطة اليوم",
                route_index=route.vehicle_index,
                hub_id=vehicle.hub_id,
            ))

        shift_usage = evaluation.working_minutes / vehicle.max_shift_minutes
        if shift_usage > 0.92:
            warnings.append(PlanWarning(
                warning_type=WarningType.SHIFT_NEAR_LIMIT,
                severity=Severity.MEDIUM,
                reason_ar=(
                    f"الوردية مستهلكة بنسبة {shift_usage:.0%} "
                    f"({evaluation.working_minutes / 60:.1f} من "
                    f"{vehicle.max_shift_minutes / 60:.1f} ساعة)"
                ),
                affected_entity_ar=label,
                suggested_action_ar=(
                    "أي تأخير بسيط سيخرق حد الوردية — انقل شحنة إلى رحلة أخرى "
                    "أو أضف سائقًا"
                ),
                route_index=route.vehicle_index,
                hub_id=vehicle.hub_id,
                context={"usage_pct": round(shift_usage * 100, 1)},
            ))

        if evaluation.is_long_haul:
            warnings.append(PlanWarning(
                warning_type=WarningType.LONG_HAUL_ROUTE,
                severity=Severity.LOW,
                reason_ar=(
                    f"رحلة بعيدة: أقصى مسافة من المركز {evaluation.max_hub_distance_km:.0f} كم "
                    f"(حد الرحلة البعيدة {settings.long_haul_km:.0f} كم)"
                ),
                affected_entity_ar=label,
                suggested_action_ar=(
                    "لا يجوز إسناد رحلة بعيدة ثانية لنفس السائق في اليوم نفسه"
                ),
                route_index=route.vehicle_index,
                hub_id=vehicle.hub_id,
                context={"max_hub_km": round(evaluation.max_hub_distance_km, 1)},
            ))

        if evaluation.mixing_exemption_used:
            warnings.append(PlanWarning(
                warning_type=WarningType.MIXED_FACILITY_EXEMPTION_USED,
                severity=Severity.INFO,
                reason_ar="استُخدم استثناء خلط الأنواع (بنك الدم) في هذه الرحلة",
                affected_entity_ar=label,
                suggested_action_ar="تحقق من مطابقة الاستثناء للسياسة التشغيلية المعتمدة",
                route_index=route.vehicle_index,
                hub_id=vehicle.hub_id,
            ))

        for timing in evaluation.timings:
            if timing.wait_minutes > settings.max_wait_minutes_per_stop:
                node = problem.nodes[timing.node_index]
                shipment = problem.shipment_of_node(timing.node_index)
                warnings.append(PlanWarning(
                    warning_type=WarningType.LONG_WAIT,
                    severity=Severity.LOW,
                    reason_ar=(
                        f"انتظار {timing.wait_minutes:.0f} دقيقة عند {node.label} "
                        f"(الحد المقبول {settings.max_wait_minutes_per_stop:.0f})"
                    ),
                    affected_entity_ar=f"{label} — {node.label}",
                    suggested_action_ar=(
                        "راجع نافذة الالتقاط مع الجهة أو أعد ترتيب المحطة"
                    ),
                    route_index=route.vehicle_index,
                    shipment_id=shipment.shipment_id if shipment else None,
                    hub_id=vehicle.hub_id,
                    context={"wait_minutes": round(timing.wait_minutes, 1)},
                ))

        if evaluation.min_slack_minutes != float("inf") and evaluation.min_slack_minutes < 10:
            warnings.append(PlanWarning(
                warning_type=WarningType.SLA_TIGHT,
                severity=Severity.MEDIUM,
                reason_ar=(
                    f"أقل هامش زمني في الرحلة {evaluation.min_slack_minutes:.0f} دقيقة فقط"
                ),
                affected_entity_ar=label,
                suggested_action_ar=(
                    "الخطة هشّة أمام أي تأخير — راجع توزيع المحطات أو وسّع النافذة"
                ),
                route_index=route.vehicle_index,
                hub_id=vehicle.hub_id,
                context={"min_slack_minutes": round(evaluation.min_slack_minutes, 1)},
            ))

    # عدالة التوزيع
    used = solution.used_routes()
    if len(used) > 1:
        loads = [route.evaluation.working_minutes for route in used]
        spread = max(loads) - min(loads)
        if spread > 0.35 * max(loads):
            warnings.append(PlanWarning(
                warning_type=WarningType.UNBALANCED_WORKLOAD,
                severity=Severity.LOW,
                reason_ar=(
                    f"الفارق بين أثقل رحلة وأخفها {spread / 60:.1f} ساعة "
                    f"({spread / max(loads):.0%} من أثقل رحلة)"
                ),
                affected_entity_ar=f"{len(used)} رحلة في هذا اليوم",
                suggested_action_ar=(
                    "ارفع وزن عدالة التوزيع في إعدادات التخطيط وأعد التشغيل، "
                    "أو أعد توزيع الشحنات يدويًا"
                ),
                context={"spread_minutes": round(spread, 1)},
            ))

    return warnings


def _suggested_action(violation: Violation) -> str:
    reason = str(violation.reason)
    actions = {
        "IMPOSSIBLE_PICKUP_WINDOW": (
            "وسّع نافذة الالتقاط مع الجهة، أو انقل الشحنة إلى مركز انطلاق أقرب"
        ),
        "IMPOSSIBLE_SLA": (
            "راجع موعد التسليم النهائي مع الجهة المستقبلة، أو أضف سائقًا في هذا المركز"
        ),
        "SLA_BEFORE_PICKUP_WINDOW": "صحّح البيانات: موعد التسليم قبل نافذة الالتقاط",
        "SHIFT_LIMIT_EXCEEDED": "أضف سائقًا، أو قسّم الشحنة على يومين، أو راجع حد الوردية",
        "MIXING_CONSTRAINT": (
            "أضف سائقًا مخصصًا لهذا الصنف، أو راجع سياسة الاستثناء في الإعدادات"
        ),
        "LONG_HAUL_LIMIT": "خصّص سائقًا للرحلة البعيدة ولا تضف له محطات مدينة بعدها",
        "NO_FEASIBLE_DRIVER": "أضف سائقًا متاحًا في مركز الانطلاق المسؤول",
        "MISSING_COORDINATES": "أكمل إحداثيات الجهة في البيانات الرئيسية",
        "FACILITY_NOT_REGISTERED": "سجّل الجهة في البيانات الرئيسية ثم أعد الرفع",
        "ROUTING_SERVICE_UNAVAILABLE": "تحقق من خدمة الطرق ثم أعد تشغيل المحرك",
    }
    return actions.get(reason, "راجع بيانات الشحنة والقيود التشغيلية لهذا المركز")
