"""محرك تحسين المسارات — بناء بالإدراج ثم تحسين بـ ALNS.

**لماذا هذه الطريقة؟** (§10 يطلب تبرير الاختيار)

المسألة هي *Pickup and Delivery Problem with Time Windows* متعدد المستودعات
(PDPTW / multi-depot)، وهي NP-صعبة. الاختيار مبني على حجم المشكلة:

| حجم المسألة | الطريقة | السبب |
|---|---|---|
| ≤ ٩ عقد في رحلة واحدة | **بحث مضبوط** (تعداد مع تشذيب Branch & Bound) في ``exact.py`` | يعطي الحل الأمثل المؤكد، ويُستخدم كمرجع للتحقق من جودة الاستدلال في الاختبارات |
| مئات إلى آلاف الشحنات | **إدراج بالندم (Regret-k) + ALNS** | معيار صناعي مثبت لـ PDPTW (Ropke & Pisinger 2006)؛ يعطي حلولًا قريبة من الأمثل ضمن مهلة زمنية محددة، ويدعم الإدراج الديناميكي للطلبات الفورية بنفس الشيفرة |

**لماذا ليس نموذجًا لغويًا؟** (§10 يمنعه صراحة) — ترتيب المسار هنا ناتج عن
نموذج رياضي: كل حل يُبنى من مصفوفة أزمنة، ويمر على ``evaluate_route`` الذي
يفحص القيود الصلبة عدديًا. كل قرار قابل للتفسير والتكرار (بذرة عشوائية ثابتة).

**تركيب OR-Tools:** الواجهة ``SolverBackend`` في ``backends.py`` تسمح بتركيب
CP-SAT كخلفية بديلة دون تغيير أي متصل.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

from masar_core.constants import UnplannableReason

from .evaluate import RouteEvaluation, Violation, evaluate_route, shipment_prescreen
from .model import NodeKind, Problem, ShipmentSpec
from .objective import (
    STRICT_LEVELS,
    RoutePlan,
    Solution,
    compare_level,
    is_better,
    objective_vector,
    soft_scalar,
)


@dataclass(slots=True)
class InsertionCandidate:
    route_index: int
    pickup_position: int
    delivery_position: int
    delta: float
    evaluation: RouteEvaluation
    sequence: list[int]


@dataclass(slots=True)
class SolveOptions:
    time_limit_seconds: float = 25.0
    seed: int = 20260826
    #: عدد المستويات في حساب الندم (2 = الفرق بين أفضل وثاني أفضل رحلة)
    regret_k: int = 3
    #: حدود حجم الهدم في ALNS كنسبة من عدد الشحنات
    min_destroy_ratio: float = 0.10
    max_destroy_ratio: float = 0.35
    #: حرارة البداية للتلدين المحاكى (نسبة من القيمة المرنة الابتدائية)
    initial_temperature_ratio: float = 0.05
    cooling_rate: float = 0.9985
    #: تعطيل التحسين (للاختبارات التي تريد نتيجة البناء فقط)
    improvement_enabled: bool = True
    max_iterations: int = 100_000
    #: أقصى عدد مواضع إدراج تُقيَّم تقييمًا كاملًا لكل رحلة (ترشيح هندسي أوّلي)
    #: صفر = بلا ترشيح (تقييم كل الأزواج). القيمة تحكم المفاضلة بين الدقة والزمن.
    max_positions_per_route: int = 12


class RouteOptimizer:
    """يبني حلًا صالحًا ثم يحسّنه ضمن مهلة زمنية."""

    def __init__(self, problem: Problem, options: SolveOptions | None = None) -> None:
        self.problem = problem
        self.options = options or SolveOptions()
        self.random = random.Random(self.options.seed)
        self.baseline: dict[int, int] | None = None
        self.iterations = 0
        self.improvements = 0
        self._deadline = 0.0

    # ------------------------------------------------------------ الواجهة --
    def solve(self, baseline: dict[int, int] | None = None) -> Solution:
        self.baseline = baseline
        self._deadline = time.monotonic() + self.options.time_limit_seconds

        solution = self._construct()
        if self.options.improvement_enabled and solution.used_routes():
            solution = self._improve(solution)
        self._rebuild_evaluations(solution)
        return solution

    # ------------------------------------------------------------ البناء --
    def _empty_solution(self) -> Solution:
        return Solution(
            routes=[
                RoutePlan(vehicle.index, [], RouteEvaluation(feasible=True))
                for vehicle in self.problem.vehicles
            ]
        )

    def _construct(self) -> Solution:
        """إدراج بالندم: في كل جولة تُدرج الشحنة الأكثر «ندمًا» على تأجيلها."""
        solution = self._empty_solution()
        pending: list[int] = []

        # فحص مبدئي: الشحنات المستحيلة رياضيًا تُستبعد بسبب مسجّل قبل أي بحث
        for shipment in self.problem.shipments:
            vehicle = self._reference_vehicle(shipment)
            violation = shipment_prescreen(shipment, self.problem, vehicle) if vehicle else None
            if violation is not None:
                solution.unassigned[shipment.index] = violation
            elif vehicle is None:
                solution.unassigned[shipment.index] = Violation(
                    "PRE-00", UnplannableReason.NO_FEASIBLE_DRIVER,
                    f"الشحنة {shipment.reference}: لا يوجد سائق متاح في مركز الانطلاق "
                    f"{shipment.hub_id}",
                    shipment_index=shipment.index,
                )
            else:
                pending.append(shipment.index)

        # ذاكرة مؤقتة لمواضع الإدراج. إعادة حساب كل المرشحين لكل شحنة في كل
        # جولة تجعل البناء من الدرجة O(S² × V × L²) — وهو ما جعل ٨٠ شحنة
        # تستغرق دقائق في قياس السيناريو ٤٣. بعد إدراج شحنة لا تتغير إلا
        # **الرحلة التي عُدِّلت**، فيكفي إعادة حساب مرشحيها وحدها.
        cache: dict[int, list[InsertionCandidate]] = {
            index: self._insertion_candidates(solution, index) for index in pending
        }

        while pending:
            # حارس المهلة: عند تجاوزها لا يُترك البناء يعمل بلا حد. نكمل بإدراج
            # «أول ما يصلح» — أضعف جودةً وأسرع كثيرًا، ولا يخلّ بالشرعية لأن كل
            # مرشح يمر على ``evaluate_route`` كما هو. البديل (الاستمرار) يعني
            # خطة لا تصل في وقتها التشغيلي، وهو فشل أشد.
            if time.monotonic() > self._deadline:
                self._first_fit_remainder(solution, pending)
                break

            best_shipment: int | None = None
            best_candidate: InsertionCandidate | None = None
            best_regret = -math.inf

            for shipment_index in pending:
                candidates = cache.get(shipment_index) or []
                if not candidates:
                    continue
                candidates.sort(key=lambda c: c.delta)
                top = candidates[0]
                regret = 0.0
                for rank in range(1, min(self.options.regret_k, len(candidates))):
                    regret += candidates[rank].delta - top.delta
                if len(candidates) == 1:
                    # لا بديل: أعلى ندم ممكن — يجب إدراجها الآن
                    regret = math.inf
                if regret > best_regret or (
                    regret == best_regret and best_candidate is not None
                    and top.delta < best_candidate.delta
                ):
                    best_regret = regret
                    best_shipment = shipment_index
                    best_candidate = top

            if best_shipment is None or best_candidate is None:
                # لا شيء قابل للإدراج: سجّل السبب لكل متبقٍ
                for shipment_index in pending:
                    solution.unassigned[shipment_index] = self._explain_rejection(
                        solution, shipment_index
                    )
                break

            self._apply_candidate(solution, best_shipment, best_candidate)
            pending.remove(best_shipment)
            cache.pop(best_shipment, None)

            touched = best_candidate.route_index
            for shipment_index in pending:
                fresh = self._insertion_candidates(
                    solution, shipment_index, only_route=touched, also_empty=True)
                covered = {c.route_index for c in fresh} | {touched}
                kept = [c for c in cache.get(shipment_index, [])
                        if c.route_index not in covered]
                cache[shipment_index] = kept + fresh

        return solution

    def _first_fit_remainder(self, solution: Solution, pending: list[int]) -> None:
        """إكمال البناء بعد انتهاء المهلة: أول موضع صالح لكل شحنة متبقية."""
        for shipment_index in list(pending):
            candidates = self._insertion_candidates(solution, shipment_index, limit=1)
            if candidates:
                self._apply_candidate(solution, shipment_index, candidates[0])
            else:
                solution.unassigned[shipment_index] = self._explain_rejection(
                    solution, shipment_index)

    def _reference_vehicle(self, shipment: ShipmentSpec):
        for vehicle in self.problem.vehicles:
            if shipment.hub_id is None or vehicle.hub_id == shipment.hub_id:
                return vehicle
        return None

    def _position_pairs(
        self, sequence: list[int], start_node: int, locked: int,
        pickup_node: int, delivery_node: int,
    ) -> list[tuple[int, int]]:
        """يرتّب أزواج (موضع الالتقاط، موضع التسليم) بحدّ أدنى هندسي رخيص.

        الغرض ترشيح لا بتّ: الحد الأدنى مبني على زيادة المسافة وحدها، ثم
        يُقيَّم أفضل ``max_positions_per_route`` زوجًا تقييمًا **كاملًا** عبر
        ``evaluate_route``. لا يمكن لهذا الترشيح أن يقبل رحلة غير شرعية —
        كل ما قد يفعله هو تفويت موضع أرخص، وهو ثمن معلن مقابل زمن الحل،
        ويتكفّل ALNS لاحقًا بتعويض معظمه.
        """
        km = self.problem.travel.km
        length = len(sequence)
        top_k = self.options.max_positions_per_route

        def node_before(position: int) -> int:
            return start_node if position == 0 else sequence[position - 1]

        def detour(position: int, node: int) -> float:
            previous = node_before(position)
            if position >= length:            # الإضافة في نهاية الرحلة
                return km(previous, node)
            following = sequence[position]
            return km(previous, node) + km(node, following) - km(previous, following)

        pairs: list[tuple[float, int, int]] = []
        for pickup_pos in range(locked, length + 1):
            pickup_detour = detour(pickup_pos, pickup_node)
            for delivery_pos in range(pickup_pos + 1, length + 2):
                if delivery_pos == pickup_pos + 1:
                    previous = node_before(pickup_pos)
                    bound = km(previous, pickup_node) + km(pickup_node, delivery_node)
                    if pickup_pos < length:
                        following = sequence[pickup_pos]
                        bound += km(delivery_node, following) - km(previous, following)
                else:
                    bound = pickup_detour + detour(delivery_pos - 1, delivery_node)
                pairs.append((bound, pickup_pos, delivery_pos))

        if top_k > 0 and len(pairs) > top_k:
            pairs = sorted(pairs)[:top_k]
        else:
            pairs.sort()
        return [(pickup_pos, delivery_pos) for _bound, pickup_pos, delivery_pos in pairs]

    def _insertion_candidates(
        self, solution: Solution, shipment_index: int, *,
        limit: int | None = None, only_route: int | None = None,
        also_empty: bool = False,
    ) -> list[InsertionCandidate]:
        """مواضع الإدراج الصالحة للشحنة، مرتبة بكلفة الزيادة."""
        shipment = self.problem.shipments[shipment_index]
        candidates: list[InsertionCandidate] = []
        #: المركبات الفارغة المتطابقة في المواصفات تعطي نفس المرشح حرفيًا.
        #: توليدها كلها يضاعف زمن البناء بعدد السائقين الاحتياطيين بلا فائدة
        #: (٦١ مركبة ⇒ ٦١ مرشحًا متطابقًا)، فنكتفي بممثّل واحد لكل توقيع.
        seen_empty: set[tuple] = set()

        for route in solution.routes:
            if only_route is not None and route.vehicle_index != only_route:
                # ``also_empty`` يضمن أن تبقى «رحلة جديدة» خيارًا مطروحًا في كل
                # جولة. بدونه، بعد امتلاء الرحلة الممثِّلة للمركبات الفارغة لا
                # يبقى في الذاكرة أي مرشح لفتح رحلة أخرى، فتُرفض شحنات صالحة.
                if not (also_empty and not route.sequence):
                    continue
            vehicle = self.problem.vehicles[route.vehicle_index]
            if shipment.hub_id is not None and vehicle.hub_id != shipment.hub_id:
                continue
            if not route.sequence:
                signature = (vehicle.hub_id, vehicle.start_node, vehicle.earliest_start,
                             vehicle.latest_end, vehicle.max_shift_minutes,
                             vehicle.max_long_haul)
                if signature in seen_empty:
                    continue
                seen_empty.add(signature)
            base_cost = self._route_cost(route.evaluation) if route.sequence else 0.0
            locked = len(vehicle.locked_prefix)

            for pickup_pos, delivery_pos in self._position_pairs(
                route.sequence, vehicle.start_node, locked,
                shipment.pickup_node, shipment.delivery_node,
            ):
                sequence = list(route.sequence)
                sequence.insert(pickup_pos, shipment.pickup_node)
                sequence.insert(delivery_pos, shipment.delivery_node)
                evaluation = evaluate_route(self.problem, vehicle, sequence)
                if not evaluation.feasible:
                    continue
                new_cost = self._route_cost(evaluation)
                opening_penalty = (
                    self.problem.settings.cost_per_driver_day
                    if not route.sequence else 0.0
                )
                candidates.append(InsertionCandidate(
                    route_index=route.vehicle_index,
                    pickup_position=pickup_pos,
                    delivery_position=delivery_pos,
                    delta=new_cost - base_cost + opening_penalty,
                    evaluation=evaluation,
                    sequence=sequence,
                ))
                if limit is not None and len(candidates) >= limit:
                    return candidates
        return candidates

    def _route_cost(self, evaluation: RouteEvaluation) -> float:
        """كلفة رحلة واحدة بترتيب أهداف المستويات المرنة."""
        settings = self.problem.settings
        return (
            evaluation.drive_minutes
            + evaluation.distance_km * settings.cost_per_km * 0.5
            + evaluation.wait_minutes * 0.3
        )

    def _apply_candidate(
        self, solution: Solution, shipment_index: int, candidate: InsertionCandidate
    ) -> None:
        route = next(r for r in solution.routes if r.vehicle_index == candidate.route_index)
        route.sequence = candidate.sequence
        route.evaluation = candidate.evaluation
        solution.assignment[shipment_index] = candidate.route_index
        solution.unassigned.pop(shipment_index, None)

    def _explain_rejection(self, solution: Solution, shipment_index: int) -> Violation:
        """يشرح لماذا تعذّر إدراج الشحنة — لا يُترك سبب فارغ أبدًا (HC-19)."""
        shipment = self.problem.shipments[shipment_index]
        reasons: list[Violation] = []
        for route in solution.routes:
            vehicle = self.problem.vehicles[route.vehicle_index]
            if shipment.hub_id is not None and vehicle.hub_id != shipment.hub_id:
                continue
            sequence = list(route.sequence)
            sequence.append(shipment.pickup_node)
            sequence.append(shipment.delivery_node)
            evaluation = evaluate_route(self.problem, vehicle, sequence)
            if evaluation.violations:
                reasons.append(evaluation.violations[0])

        if not reasons:
            return Violation(
                "ASSIGN-00", UnplannableReason.NO_FEASIBLE_DRIVER,
                f"الشحنة {shipment.reference}: لا يوجد مركز انطلاق أو سائق مطابق",
                shipment_index=shipment_index,
            )

        # السبب الأكثر تكرارًا هو الأدق تشخيصًا
        counts: dict[str, tuple[int, Violation]] = {}
        for violation in reasons:
            count, first = counts.get(violation.rule, (0, violation))
            counts[violation.rule] = (count + 1, first)
        rule, (_, sample) = max(counts.items(), key=lambda item: item[1][0])
        return Violation(
            rule, sample.reason,
            f"الشحنة {shipment.reference}: تعذر إدراجها في أي رحلة متاحة — "
            f"السبب الغالب: {sample.message_ar}",
            shipment_index=shipment_index,
            slack_minutes=sample.slack_minutes,
        )

    # ---------------------------------------------------------- التحسين --
    def _improve(self, solution: Solution) -> Solution:
        """ALNS: هدم وإعادة بناء مع قبول تلدين محاكى على المستويات المرنة."""
        options = self.options
        current = solution
        best = solution.copy()
        best_vector = objective_vector(best, self.problem, baseline=self.baseline)
        current_vector = best_vector

        temperature = max(
            soft_scalar(current_vector, self.problem) * options.initial_temperature_ratio,
            1e-3,
        )

        removal_operators = (
            self._remove_random,
            self._remove_worst,
            self._remove_related,
            self._remove_route,
        )
        operator_weights = [1.0] * len(removal_operators)
        operator_scores = [0.0] * len(removal_operators)
        operator_uses = [0] * len(removal_operators)

        assignable = [
            index for index in range(len(self.problem.shipments))
            if index not in solution.unassigned or True
        ]
        if len(assignable) < 2:
            return solution

        while time.monotonic() < self._deadline and self.iterations < options.max_iterations:
            self.iterations += 1

            candidate = current.copy()
            count = max(1, int(len(assignable) * self.random.uniform(
                options.min_destroy_ratio, options.max_destroy_ratio)))
            operator_index = self._pick_operator(operator_weights)
            operator_uses[operator_index] += 1
            removed = removal_operators[operator_index](candidate, count)
            if not removed:
                continue

            self._repair(candidate, removed)
            candidate_vector = objective_vector(
                candidate, self.problem, baseline=self.baseline)

            accepted = False
            if is_better(candidate_vector, current_vector):
                accepted = True
                operator_scores[operator_index] += 4.0
            else:
                level = compare_level(candidate_vector, current_vector)
                # لا يُقبل أي تدهور في المستويات الصارمة
                if level >= STRICT_LEVELS or level == -1:
                    delta = (
                        soft_scalar(candidate_vector, self.problem)
                        - soft_scalar(current_vector, self.problem)
                    )
                    if delta <= 0 or self.random.random() < math.exp(-delta / temperature):
                        accepted = True
                        operator_scores[operator_index] += 1.0

            if accepted:
                current = candidate
                current_vector = candidate_vector
                if is_better(candidate_vector, best_vector):
                    best = candidate.copy()
                    best_vector = candidate_vector
                    self.improvements += 1
                    operator_scores[operator_index] += 9.0

            temperature = max(temperature * options.cooling_rate, 1e-6)

            # تحديث أوزان المشغّلات كل ١٠٠ تكرار (تكيّف ALNS)
            if self.iterations % 100 == 0:
                for index in range(len(operator_weights)):
                    if operator_uses[index]:
                        reward = operator_scores[index] / operator_uses[index]
                        operator_weights[index] = 0.8 * operator_weights[index] + 0.2 * max(reward, 0.05)
                    operator_scores[index] = 0.0
                    operator_uses[index] = 0

        return best

    def _pick_operator(self, weights: list[float]) -> int:
        total = sum(weights)
        pick = self.random.random() * total
        cumulative = 0.0
        for index, weight in enumerate(weights):
            cumulative += weight
            if pick <= cumulative:
                return index
        return len(weights) - 1

    # ------------------------------------------------- مشغّلات الهدم ----
    def _assigned_shipments(self, solution: Solution) -> list[int]:
        return list(solution.assignment.keys())

    def _remove_shipments(self, solution: Solution, shipment_indices: list[int]) -> None:
        by_route: dict[int, set[int]] = {}
        for shipment_index in shipment_indices:
            route_index = solution.assignment.pop(shipment_index, None)
            if route_index is None:
                continue
            by_route.setdefault(route_index, set()).add(shipment_index)

        for route_index, removed in by_route.items():
            route = next(r for r in solution.routes if r.vehicle_index == route_index)
            nodes = self.problem.nodes
            route.sequence = [
                node_index for node_index in route.sequence
                if nodes[node_index].shipment_index not in removed
            ]
            vehicle = self.problem.vehicles[route.vehicle_index]
            route.evaluation = evaluate_route(self.problem, vehicle, route.sequence)

    def _remove_random(self, solution: Solution, count: int) -> list[int]:
        assigned = self._assigned_shipments(solution)
        pool = assigned + list(solution.unassigned.keys())
        if not pool:
            return []
        chosen = self.random.sample(assigned, min(count, len(assigned))) if assigned else []
        chosen += list(solution.unassigned.keys())
        self._remove_shipments(solution, chosen)
        return list(dict.fromkeys(chosen))

    def _remove_worst(self, solution: Solution, count: int) -> list[int]:
        """يزيل الشحنات الأعلى كلفة إزالة (الأكثر إفسادًا للمسار)."""
        assigned = self._assigned_shipments(solution)
        if not assigned:
            return list(solution.unassigned.keys())
        scored: list[tuple[float, int]] = []
        for shipment_index in assigned:
            route_index = solution.assignment[shipment_index]
            route = next(r for r in solution.routes if r.vehicle_index == route_index)
            vehicle = self.problem.vehicles[route.vehicle_index]
            nodes = self.problem.nodes
            trimmed = [
                node for node in route.sequence
                if nodes[node].shipment_index != shipment_index
            ]
            after = evaluate_route(self.problem, vehicle, trimmed)
            saving = self._route_cost(route.evaluation) - self._route_cost(after)
            scored.append((saving, shipment_index))
        scored.sort(reverse=True)
        chosen = [index for _, index in scored[:count]] + list(solution.unassigned.keys())
        self._remove_shipments(solution, chosen)
        return list(dict.fromkeys(chosen))

    def _remove_related(self, solution: Solution, count: int) -> list[int]:
        """هدم Shaw: يزيل شحنات متقاربة مكانيًا وزمنيًا لإتاحة إعادة تجميعها."""
        assigned = self._assigned_shipments(solution)
        if not assigned:
            return list(solution.unassigned.keys())
        seed = self.random.choice(assigned)
        seed_shipment = self.problem.shipments[seed]
        travel = self.problem.travel

        def relatedness(index: int) -> float:
            other = self.problem.shipments[index]
            spatial = (
                travel.km(seed_shipment.pickup_node, other.pickup_node)
                + travel.km(seed_shipment.delivery_node, other.delivery_node)
            )
            temporal = abs(seed_shipment.sla_deadline - other.sla_deadline)
            return spatial + temporal * 0.5

        ranked = sorted(assigned, key=relatedness)
        chosen = ranked[:count] + list(solution.unassigned.keys())
        self._remove_shipments(solution, chosen)
        return list(dict.fromkeys(chosen))

    def _remove_route(self, solution: Solution, count: int) -> list[int]:
        """يفرّغ رحلة كاملة — المشغّل الوحيد القادر على تقليل عدد السائقين."""
        used = [route for route in solution.routes if route.sequence]
        if not used:
            return []
        target = min(used, key=lambda r: len(r.sequence))
        nodes = self.problem.nodes
        chosen = list({
            nodes[node_index].shipment_index for node_index in target.sequence
        })
        chosen += list(solution.unassigned.keys())
        self._remove_shipments(solution, chosen)
        return list(dict.fromkeys(chosen))

    # -------------------------------------------------- إعادة البناء ----
    def _repair(self, solution: Solution, shipment_indices: list[int]) -> None:
        pending = [
            index for index in shipment_indices
            if index not in solution.assignment
        ]
        self.random.shuffle(pending)

        # نفس منطق الذاكرة المؤقتة في ``_construct``: إعادة حساب كل المرشحين
        # لكل شحنة مهدومة في كل جولة كانت تجعل **دورة ALNS واحدة** تستغرق
        # دقائق على مسألة من ٣٠٠ شحنة (قياس السيناريو ٤٣).
        cache: dict[int, list[InsertionCandidate]] = {
            index: self._insertion_candidates(solution, index) for index in pending
        }

        while pending:
            best_shipment: int | None = None
            best_candidate: InsertionCandidate | None = None
            best_regret = -math.inf

            for shipment_index in pending:
                candidates = cache.get(shipment_index) or []
                if not candidates:
                    continue
                candidates.sort(key=lambda c: c.delta)
                top = candidates[0]
                regret = sum(
                    candidates[rank].delta - top.delta
                    for rank in range(1, min(self.options.regret_k, len(candidates)))
                )
                if len(candidates) == 1:
                    regret = math.inf
                if regret > best_regret:
                    best_regret = regret
                    best_shipment = shipment_index
                    best_candidate = top

            if best_shipment is None or best_candidate is None:
                for shipment_index in pending:
                    if shipment_index not in solution.unassigned:
                        solution.unassigned[shipment_index] = self._explain_rejection(
                            solution, shipment_index)
                return

            self._apply_candidate(solution, best_shipment, best_candidate)
            pending.remove(best_shipment)
            cache.pop(best_shipment, None)

            touched = best_candidate.route_index
            for shipment_index in pending:
                fresh = self._insertion_candidates(
                    solution, shipment_index, only_route=touched, also_empty=True)
                covered = {c.route_index for c in fresh} | {touched}
                kept = [c for c in cache.get(shipment_index, [])
                        if c.route_index not in covered]
                cache[shipment_index] = kept + fresh

    def _rebuild_evaluations(self, solution: Solution) -> None:
        """إعادة تقييم نهائية — لا تُعاد نتيجة لم تُفحص مرة أخيرة."""
        for route in solution.routes:
            vehicle = self.problem.vehicles[route.vehicle_index]
            route.evaluation = evaluate_route(
                self.problem, vehicle, route.sequence, stop_on_first_violation=False
            )


def solve(problem: Problem, options: SolveOptions | None = None,
          baseline: dict[int, int] | None = None) -> tuple[Solution, RouteOptimizer]:
    optimizer = RouteOptimizer(problem, options)
    return optimizer.solve(baseline), optimizer
