"""تقييم الرحلة: بناء الجدول الزمني وفحص القيود الصلبة.

هذا الملف هو **المرجع الوحيد** لشرعية أي رحلة في النظام. المحرك يستدعيه أثناء
البحث، وخدمة النشر تستدعيه مرة أخرى كفحص مستقل قبل النشر، وتستدعيه الاختبارات
مباشرة. لا يوجد مسار في النظام يُنتج رحلة دون المرور به.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from masar_core.constants import UnplannableReason

from .model import Node, NodeKind, Problem, ProblemSettings, ShipmentSpec, VehicleSpec


@dataclass(slots=True)
class StopTiming:
    node_index: int
    arrival: float
    service_start: float
    service_end: float
    departure: float
    wait_minutes: float
    leg_minutes: float
    leg_km: float
    #: هل أُخِّر الانطلاق من المحطة السابقة لتفادي الوصول المبكر؟
    departure_delayed: bool = False


@dataclass(slots=True)
class Violation:
    rule: str
    reason: UnplannableReason | str
    message_ar: str
    node_index: int | None = None
    shipment_index: int | None = None
    slack_minutes: float | None = None


@dataclass(slots=True)
class RouteEvaluation:
    feasible: bool
    violations: list[Violation] = field(default_factory=list)
    timings: list[StopTiming] = field(default_factory=list)
    start_at: float = 0.0
    end_at: float = 0.0
    working_minutes: float = 0.0
    drive_minutes: float = 0.0
    service_minutes: float = 0.0
    wait_minutes: float = 0.0
    distance_km: float = 0.0
    max_hub_distance_km: float = 0.0
    is_long_haul: bool = False
    mixing_classes: frozenset[str] = frozenset()
    mixing_exemption_used: bool = False
    #: أقل هامش تبقّى قبل خرق أي نافذة أو SLA (لقياس هشاشة الخطة)
    min_slack_minutes: float = float("inf")

    @property
    def first_violation(self) -> Violation | None:
        return self.violations[0] if self.violations else None


#: الأصناف التي يمنع القيد خلطها على السائق نفسه (§12/13)
RESTRICTED_MIXING_CLASSES = frozenset({"PRIMARY_CARE", "SECONDARY_CARE"})


def evaluate_route(
    problem: Problem,
    vehicle: VehicleSpec,
    sequence: list[int],
    *,
    stop_on_first_violation: bool = True,
    partial: bool = False,
) -> RouteEvaluation:
    """يبني الجدول الزمني لتسلسل محطات ويفحص كل القيود الصلبة.

    ``sequence`` قائمة فهارس العقد بدون عقدة البداية (تُضاف ضمنيًا).

    ``partial=True`` يُستخدم لتقييم بادئة مسار أثناء البحث المضبوط: يفحص كل
    القيود الزمنية والتسلسلية لكنه لا يشترط اكتمال أزواج الالتقاط/التسليم،
    لأن البقية لم تُضَف بعد. كل مقاييس الزمن تتصاعد مع طول المسار، فالفحص
    على البادئة يبقى تشذيبًا صحيحًا.
    """
    settings = problem.settings
    travel = problem.travel
    nodes = problem.nodes
    eps = settings.epsilon_minutes

    evaluation = RouteEvaluation(feasible=True)
    violations = evaluation.violations

    def fail(v: Violation) -> RouteEvaluation:
        evaluation.feasible = False
        violations.append(v)
        return evaluation

    # ---------------------------------------------------- HC-01: الأسبقية --
    seen_pickup: set[int] = set()
    seen_delivery: set[int] = set()
    for position, node_index in enumerate(sequence):
        node = nodes[node_index]
        if node.kind is NodeKind.PICKUP:
            seen_pickup.add(node.shipment_index)
        elif node.kind is NodeKind.DELIVERY:
            if node.shipment_index not in seen_pickup:
                shipment = problem.shipments[node.shipment_index]
                v = Violation(
                    "HC-01", UnplannableReason.NO_FEASIBLE_DRIVER,
                    f"الشحنة {shipment.reference}: محطة التسليم في الموضع {position + 1} "
                    "قبل محطة الالتقاط",
                    node_index=node_index, shipment_index=node.shipment_index,
                )
                if stop_on_first_violation:
                    return fail(v)
                evaluation.feasible = False
                violations.append(v)
            seen_delivery.add(node.shipment_index)

    # كل شحنة في الرحلة يجب أن يكون لها التقاط وتسليم (لا يُفحص على البادئة)
    incomplete = set() if partial else seen_pickup.symmetric_difference(seen_delivery)
    if incomplete:
        shipment = problem.shipments[next(iter(incomplete))]
        v = Violation(
            "HC-01", UnplannableReason.NO_FEASIBLE_DRIVER,
            f"الشحنة {shipment.reference}: الرحلة تحتوي طرفًا واحدًا فقط "
            "(التقاط بلا تسليم أو العكس)",
            shipment_index=shipment.index,
        )
        if stop_on_first_violation:
            return fail(v)
        evaluation.feasible = False
        violations.append(v)

    # ------------------------------------------- HC-11: ترتيب مُولَّد إجباري --
    position_of: dict[int, int] = {n: i for i, n in enumerate(sequence)}
    for shipment_index in seen_pickup:
        shipment = problem.shipments[shipment_index]
        if not shipment.delivery_before_pickups:
            continue
        delivery_pos = position_of.get(shipment.delivery_node)
        if delivery_pos is None:
            continue
        for later_index in shipment.delivery_before_pickups:
            later = problem.shipments[later_index]
            pickup_pos = position_of.get(later.pickup_node)
            if pickup_pos is not None and pickup_pos < delivery_pos:
                v = Violation(
                    "HC-11", UnplannableReason.NO_FEASIBLE_DRIVER,
                    f"الجهة نفسها: يجب تسليم عينات الالتقاط الأول "
                    f"({shipment.reference}) قبل تنفيذ الالتقاط الثاني "
                    f"({later.reference})",
                    shipment_index=shipment_index,
                )
                if stop_on_first_violation:
                    return fail(v)
                evaluation.feasible = False
                violations.append(v)

    # ---------------------------------- HC-13: تجانس أصناف جهات الالتقاط --
    pickup_classes = {
        nodes[i].mixing_class
        for i in sequence
        if nodes[i].kind is NodeKind.PICKUP and nodes[i].mixing_class
    }
    restricted = (pickup_classes & RESTRICTED_MIXING_CLASSES) - settings.mixing_exempt_classes
    evaluation.mixing_classes = frozenset(pickup_classes)
    evaluation.mixing_exemption_used = bool(pickup_classes & settings.mixing_exempt_classes)
    if settings.enforce_facility_mixing_rule and len(restricted) > 1:
        v = Violation(
            "HC-13", UnplannableReason.MIXING_CONSTRAINT,
            "خلط ممنوع على السائق نفسه بين أصناف جهات الالتقاط: "
            + "، ".join(sorted(restricted)),
        )
        if stop_on_first_violation:
            return fail(v)
        evaluation.feasible = False
        violations.append(v)

    if not sequence:
        evaluation.start_at = vehicle.earliest_start
        evaluation.end_at = vehicle.earliest_start
        return evaluation

    # --------------------------------------------------- الجدول الزمني ----
    start_node = vehicle.start_node
    # تجهيز في المركز قبل الانطلاق (يُحتسب ضمن الوردية)
    hub_load = settings.hub_load_minutes if nodes[start_node].kind is NodeKind.START else 0.0

    # حساب أوّلي لإمكانية تأخير الانطلاق: لا نخطط للوصول قبل بداية النافذة
    depart_from_start = vehicle.earliest_start + hub_load
    first_node = nodes[sequence[0]]
    first_leg = max(
        travel.minutes(start_node, sequence[0], depart_from_start),
        settings.min_event_gap_minutes,
    )
    if not settings.allow_early_arrival and first_node.window_from is not None:
        earliest_useful_departure = first_node.window_from - first_leg
        if earliest_useful_departure > depart_from_start:
            depart_from_start = earliest_useful_departure

    shift_start = depart_from_start - hub_load
    current_node = start_node
    current_time = depart_from_start

    total_drive = 0.0
    total_service = 0.0
    total_wait = 0.0
    total_km = 0.0
    max_hub_km = 0.0
    passed_long_haul = False

    hub_index = start_node
    for position, node_index in enumerate(sequence):
        node = nodes[node_index]
        leg_minutes_raw = travel.minutes(current_node, node_index, current_time)
        leg_minutes = max(leg_minutes_raw, settings.min_event_gap_minutes)
        leg_km = travel.km(current_node, node_index)

        arrival = current_time + leg_minutes
        departure_delayed = False

        # لا نخطط للوصول قبل بداية النافذة: نؤخر الانطلاق بدل الانتظار
        if (
            not settings.allow_early_arrival
            and node.window_from is not None
            and arrival + eps < node.window_from
        ):
            arrival = node.window_from
            departure_delayed = True
            wait = 0.0
        else:
            wait = max(0.0, (node.window_from or arrival) - arrival)

        service_start = arrival + wait

        # ------------------------------------------- HC-02: نافذة الالتقاط --
        if node.window_to is not None and service_start > node.window_to + eps:
            shipment = problem.shipments[node.shipment_index] if node.shipment_index >= 0 else None
            reason = (
                UnplannableReason.IMPOSSIBLE_PICKUP_WINDOW
                if node.kind is NodeKind.PICKUP
                else UnplannableReason.IMPOSSIBLE_SLA
            )
            v = Violation(
                "HC-02", reason,
                f"{node.label}: بداية الخدمة المخططة تتجاوز نهاية النافذة بـ "
                f"{service_start - node.window_to:.1f} دقيقة",
                node_index=node_index,
                shipment_index=shipment.index if shipment else None,
                slack_minutes=node.window_to - service_start,
            )
            if stop_on_first_violation:
                return fail(v)
            evaluation.feasible = False
            violations.append(v)

        if node.window_to is not None:
            evaluation.min_slack_minutes = min(
                evaluation.min_slack_minutes, node.window_to - service_start
            )

        service_end = service_start + node.service_minutes

        # ------------------------------------------------- HC-03: SLA ------
        if node.kind is NodeKind.DELIVERY and node.shipment_index >= 0:
            shipment = problem.shipments[node.shipment_index]
            if service_end > shipment.sla_deadline + eps:
                v = Violation(
                    "HC-03", UnplannableReason.IMPOSSIBLE_SLA,
                    f"الشحنة {shipment.reference}: التسليم المخطط يتجاوز "
                    f"الموعد النهائي بـ {service_end - shipment.sla_deadline:.1f} دقيقة",
                    node_index=node_index, shipment_index=shipment.index,
                    slack_minutes=shipment.sla_deadline - service_end,
                )
                if stop_on_first_violation:
                    return fail(v)
                evaluation.feasible = False
                violations.append(v)
            evaluation.min_slack_minutes = min(
                evaluation.min_slack_minutes, shipment.sla_deadline - service_end
            )

        # القاعدة التشغيلية: لا تبدأ الحركة التالية قبل مرور مدة من موعد الالتقاط
        departure = service_end
        if node.kind is NodeKind.PICKUP and node.window_from is not None:
            appointment = node.window_from + (
                (node.window_to - node.window_from) / 2.0
                if node.window_to is not None else 0.0
            )
            departure = max(
                departure, appointment + settings.post_pickup_departure_minutes
            )

        hub_km = travel.km(hub_index, node_index)
        max_hub_km = max(max_hub_km, hub_km)

        # ---------------------- HC-16: منع تنقلات المدينة بعد رحلة بعيدة ----
        # يُطبَّق على **محطات الالتقاط فقط**: القصد منع إضافة عمليات جمع
        # قصيرة داخل المدينة بعد أن قطع السائق مسافة بعيدة. أما تسليم ما
        # جُمع فعلًا — وهو دائمًا في المدينة حيث المختبر — فهو المقصد الحتمي
        # للرحلة ولا يجوز اعتباره «تنقلًا غير منطقي»، وإلا استحال تنفيذ أي
        # رحلة بعيدة أصلًا.
        if (
            settings.post_long_haul_policy == "NO_CITY_HOPPING"
            and passed_long_haul
            and node.kind is NodeKind.PICKUP
        ):
            if hub_km + 1e-9 < settings.post_long_haul_min_stop_km:
                v = Violation(
                    "HC-16", UnplannableReason.LONG_HAUL_LIMIT,
                    f"{node.label}: التقاط قريب من المركز ({hub_km:.1f} كم) بعد "
                    f"تجاوز مسافة الرحلة البعيدة — تنقل مدينة غير منطقي",
                    node_index=node_index,
                )
                if stop_on_first_violation:
                    return fail(v)
                evaluation.feasible = False
                violations.append(v)
        if hub_km >= settings.long_haul_km:
            passed_long_haul = True

        evaluation.timings.append(StopTiming(
            node_index=node_index,
            arrival=arrival,
            service_start=service_start,
            service_end=service_end,
            departure=departure,
            wait_minutes=wait,
            leg_minutes=leg_minutes,
            leg_km=leg_km,
            departure_delayed=departure_delayed,
        ))

        total_drive += leg_minutes
        total_service += node.service_minutes
        total_wait += wait
        total_km += leg_km
        current_node = node_index
        current_time = departure

    # ------------------------------------------ العودة للمركز (اختيارية) --
    end_time = current_time
    if settings.require_return_to_hub:
        back_minutes = travel.minutes(current_node, start_node, current_time)
        total_drive += back_minutes
        total_km += travel.km(current_node, start_node)
        end_time = current_time + back_minutes
    elif settings.count_return_leg_in_shift:
        end_time = current_time + travel.minutes(current_node, start_node, current_time)

    # ------------------------------------------ HC-05: مدة الوردية --------
    working = end_time - shift_start
    if working > vehicle.max_shift_minutes + eps:
        v = Violation(
            "HC-05", UnplannableReason.SHIFT_LIMIT_EXCEEDED,
            f"مدة الوردية {working / 60:.2f} ساعة تتجاوز الحد "
            f"{vehicle.max_shift_minutes / 60:.2f} ساعة",
            slack_minutes=vehicle.max_shift_minutes - working,
        )
        if stop_on_first_violation:
            return fail(v)
        evaluation.feasible = False
        violations.append(v)

    if end_time > vehicle.latest_end + eps:
        v = Violation(
            "HC-05", UnplannableReason.OUTSIDE_WORKING_HOURS,
            "نهاية الرحلة المخططة بعد نهاية أوقات عمل المركز",
            slack_minutes=vehicle.latest_end - end_time,
        )
        if stop_on_first_violation:
            return fail(v)
        evaluation.feasible = False
        violations.append(v)

    evaluation.start_at = shift_start
    evaluation.end_at = end_time
    evaluation.working_minutes = working
    evaluation.drive_minutes = total_drive
    evaluation.service_minutes = total_service
    evaluation.wait_minutes = total_wait
    evaluation.distance_km = total_km
    evaluation.max_hub_distance_km = max_hub_km
    evaluation.is_long_haul = max_hub_km >= settings.long_haul_km
    return evaluation


def quick_feasible(problem: Problem, vehicle: VehicleSpec, sequence: list[int]) -> bool:
    return evaluate_route(problem, vehicle, sequence, stop_on_first_violation=True).feasible


def shipment_prescreen(
    shipment: ShipmentSpec,
    problem: Problem,
    vehicle: VehicleSpec,
) -> Violation | None:
    """فحص جدوى مبدئي لشحنة منفردة قبل أي محاولة إدراج (§9: فحص جدوى كامل).

    يكشف الحالات المستحيلة رياضيًا مهما كان الترتيب: نافذة مقلوبة، SLA
    قبل أقرب وصول ممكن، أو موقع لا يمكن بلوغه ضمن الوردية.
    """
    nodes = problem.nodes
    settings = problem.settings
    pickup = nodes[shipment.pickup_node]
    delivery = nodes[shipment.delivery_node]
    travel = problem.travel

    if pickup.window_from is not None and pickup.window_to is not None:
        if pickup.window_to < pickup.window_from:
            return Violation(
                "PRE-01", UnplannableReason.IMPOSSIBLE_PICKUP_WINDOW,
                f"الشحنة {shipment.reference}: نهاية نافذة الالتقاط قبل بدايتها",
                shipment_index=shipment.index,
            )

    # أسرع مسار ممكن: المركز ← الالتقاط ← التسليم
    to_pickup = max(
        travel.minutes(vehicle.start_node, shipment.pickup_node),
        settings.min_event_gap_minutes,
    )
    pickup_to_delivery = max(
        travel.minutes(shipment.pickup_node, shipment.delivery_node),
        settings.min_event_gap_minutes,
    )
    earliest_arrival = vehicle.earliest_start + settings.hub_load_minutes + to_pickup

    if pickup.window_to is not None and earliest_arrival > pickup.window_to + settings.epsilon_minutes:
        return Violation(
            "PRE-02", UnplannableReason.IMPOSSIBLE_PICKUP_WINDOW,
            f"الشحنة {shipment.reference}: أقرب وصول ممكن من مركز الانطلاق "
            f"({earliest_arrival - pickup.window_to:.0f} دقيقة بعد نهاية النافذة) "
            "يتجاوز نافذة الالتقاط حتى لو كانت الرحلة مخصصة لها وحدها",
            shipment_index=shipment.index,
            slack_minutes=pickup.window_to - earliest_arrival,
        )

    service_start = max(earliest_arrival, pickup.window_from or earliest_arrival)
    appointment = (
        pickup.window_from + ((pickup.window_to - pickup.window_from) / 2.0)
        if pickup.window_from is not None and pickup.window_to is not None
        else None
    )
    departure = service_start + pickup.service_minutes
    if appointment is not None:
        departure = max(departure, appointment + settings.post_pickup_departure_minutes)
    earliest_delivery_end = departure + pickup_to_delivery + delivery.service_minutes

    if earliest_delivery_end > shipment.sla_deadline + settings.epsilon_minutes:
        return Violation(
            "PRE-03", UnplannableReason.IMPOSSIBLE_SLA,
            f"الشحنة {shipment.reference}: أبكر تسليم ممكن يتأخر "
            f"{earliest_delivery_end - shipment.sla_deadline:.0f} دقيقة عن الموعد النهائي "
            "حتى مع رحلة مخصصة لها وحدها",
            shipment_index=shipment.index,
            slack_minutes=shipment.sla_deadline - earliest_delivery_end,
        )

    # الوردية تُقاس من أنسب لحظة انطلاق (يجوز للسائق تأخير الانطلاق ليصل مع
    # بداية النافذة) لا من أبكر لحظة ممكنة نظريًا.
    latest_useful_departure = (
        (pickup.window_from or earliest_arrival) - to_pickup - settings.hub_load_minutes
    )
    departure_from_start = max(vehicle.earliest_start, latest_useful_departure)
    dedicated_working = earliest_delivery_end - departure_from_start
    if dedicated_working > vehicle.max_shift_minutes + settings.epsilon_minutes:
        return Violation(
            "PRE-04", UnplannableReason.SHIFT_LIMIT_EXCEEDED,
            f"الشحنة {shipment.reference}: تنفيذها منفردة يستهلك "
            f"{dedicated_working / 60:.1f} ساعة ويتجاوز حد الوردية",
            shipment_index=shipment.index,
        )

    if shipment.sla_deadline <= (pickup.window_from or 0):
        return Violation(
            "PRE-05", UnplannableReason.SLA_BEFORE_PICKUP_WINDOW,
            f"الشحنة {shipment.reference}: موعد التسليم النهائي قبل بداية نافذة الالتقاط",
            shipment_index=shipment.index,
        )
    return None
