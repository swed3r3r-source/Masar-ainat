"""دالة الهدف المرتبة معجميًا (Lexicographic) — §11.

الترتيب المطبَّق حرفيًا كما في المتطلبات:

0. **سلامة العينات والقيود الصلبة** — ليست بندًا في الدالة بل شرط وجود:
   أي حل يخرق قيدًا صلبًا لا يدخل فضاء البحث أصلًا (``evaluate_route``).
1. **منع تجاوز SLA** — قيد صلب أيضًا؛ الشحنة التي لا يمكن تسليمها ضمن SLA
   تُصنَّف غير قابلة للتخطيط ولا تُدرج في رحلة بخرق.
2. تقليل الطلبات غير القابلة للتخطيط.
3. تقليل عدد السائقين والمركبات.
4. تقليل زمن القيادة.
5. تقليل المسافة.
6. تقليل التكلفة.
7. تقليل الانتظار غير الضروري.
8. تحقيق توزيع عادل للعمل.
9. تقليل تعديل الخطة الأصلية بعد نشرها.

المقارنة معجمية حقيقية وليست بأوزان مجمّعة، فلا يمكن لتقليل المسافة أن
«يشتري» زيادة في عدد السائقين، ولا للعدالة أن تخرق SLA.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .evaluate import RouteEvaluation, Violation
from .model import Problem

#: أسماء مستويات الهدف بالترتيب — تُعرض في تتبع الهدف بالخطة
OBJECTIVE_LEVELS: tuple[tuple[str, str], ...] = (
    ("unplannable", "الطلبات غير القابلة للتخطيط"),
    ("vehicles", "عدد السائقين/المركبات"),
    ("drive_minutes", "زمن القيادة (دقيقة)"),
    ("distance_km", "المسافة (كم)"),
    ("cost", "التكلفة التقديرية (ريال)"),
    ("wait_minutes", "الانتظار غير الضروري (دقيقة)"),
    ("fairness", "مؤشر عدم عدالة التوزيع"),
    ("deviation", "الانحراف عن الخطة المرجعية"),
)

#: تسامح عددي لاعتبار قيمتين عشريتين متساويتين
EPSILON = 1e-6


@dataclass(slots=True)
class RoutePlan:
    """رحلة داخل الحل."""

    vehicle_index: int
    sequence: list[int]
    evaluation: RouteEvaluation

    @property
    def is_empty(self) -> bool:
        return not self.sequence


@dataclass(slots=True)
class Solution:
    routes: list[RoutePlan] = field(default_factory=list)
    unassigned: dict[int, Violation] = field(default_factory=dict)
    #: خريطة الشحنة → فهرس الرحلة (تسريع البحث)
    assignment: dict[int, int] = field(default_factory=dict)

    def used_routes(self) -> list[RoutePlan]:
        return [route for route in self.routes if route.sequence]

    def copy(self) -> "Solution":
        return Solution(
            routes=[
                RoutePlan(r.vehicle_index, list(r.sequence), r.evaluation)
                for r in self.routes
            ],
            unassigned=dict(self.unassigned),
            assignment=dict(self.assignment),
        )


def fairness_index(working_minutes: list[float]) -> float:
    """مؤشر عدم العدالة = الانحراف المعياري لأحمال العمل.

    صفر يعني توزيعًا متساويًا تمامًا. اختير الانحراف المعياري لأنه يعاقب
    التطرف أكثر من الفارق بين الأعلى والأدنى، وهو ما يطابق مفهوم العدالة
    التشغيلية (§14).
    """
    if len(working_minutes) < 2:
        return 0.0
    mean = sum(working_minutes) / len(working_minutes)
    variance = sum((value - mean) ** 2 for value in working_minutes) / len(working_minutes)
    return math.sqrt(variance)


def objective_vector(
    solution: Solution,
    problem: Problem,
    *,
    baseline: dict[int, int] | None = None,
) -> tuple[float, ...]:
    """يبني متجه الهدف المرتب."""
    settings = problem.settings
    used = solution.used_routes()

    drive = sum(r.evaluation.drive_minutes for r in used)
    distance = sum(r.evaluation.distance_km for r in used)
    wait = sum(r.evaluation.wait_minutes for r in used)
    working = [r.evaluation.working_minutes for r in used]

    cost = (
        distance * settings.cost_per_km
        + len(used) * settings.cost_per_driver_day
        + (sum(working) / 60.0) * settings.cost_per_hour
    )

    fairness = fairness_index(working) * settings.fairness_weight

    deviation = 0.0
    if baseline:
        for shipment_index, route_index in baseline.items():
            if solution.assignment.get(shipment_index) != route_index:
                deviation += 1.0

    return (
        float(len(solution.unassigned)),
        float(len(used)),
        drive,
        distance,
        cost,
        wait,
        fairness,
        deviation,
    )


def is_better(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    """مقارنة معجمية: هل ``a`` أفضل من ``b``؟"""
    for left, right in zip(a, b):
        if left < right - EPSILON:
            return True
        if left > right + EPSILON:
            return False
    return False


def compare_level(a: tuple[float, ...], b: tuple[float, ...]) -> int:
    """يعيد فهرس أول مستوى يختلف فيه المتجهان، أو -1 عند التطابق."""
    for index, (left, right) in enumerate(zip(a, b)):
        if abs(left - right) > EPSILON:
            return index
    return -1


#: المستويات «الصلبة عمليًا» — لا يقبل البحث تدهورًا فيها إطلاقًا
STRICT_LEVELS = 2  # عدد غير القابل للتخطيط + عدد المركبات


def soft_scalar(vector: tuple[float, ...], problem: Problem) -> float:
    """قيمة عددية للمستويات المرنة فقط — تُستخدم في قبول التلدين المحاكى.

    لا تُستخدم أبدًا للمقارنة النهائية بين حلين؛ تلك تبقى معجمية.
    """
    _, _, drive, distance, cost, wait, fairness, deviation = vector
    return drive + distance * 0.5 + cost * 0.05 + wait * 0.3 + fairness + deviation * 5.0


def explain(vector: tuple[float, ...]) -> list[dict[str, object]]:
    """يترجم متجه الهدف إلى بنود مقروءة (تُحفظ في ``plans.objective_trace``)."""
    return [
        {"level": index + 1, "key": key, "name_ar": name, "value": round(value, 3)}
        for index, ((key, name), value) in enumerate(zip(OBJECTIVE_LEVELS, vector))
    ]
