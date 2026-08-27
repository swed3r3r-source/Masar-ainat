"""واجهة خلفيات الحل — نقطة تركيب OR-Tools أو أي حلّال آخر.

المحرك الأصلي (``native_alns``) مبني ومختبَر في هذا المستودع. تركيب خلفية
جديدة يتم بتحقيق ``SolverBackend`` وتسجيلها في ``BACKENDS``؛ لا يتغير أي
متصل لأن جميع الخلفيات تعيد نفس نوع ``Solution`` وتمر على نفس
``evaluate_route`` قبل القبول.
"""

from __future__ import annotations

from typing import Protocol

from masar_core.errors import DependencyUnavailable

from .model import Problem
from .objective import Solution
from .solver import RouteOptimizer, SolveOptions


class SolverBackend(Protocol):
    name: str
    version: str

    def solve(
        self,
        problem: Problem,
        options: SolveOptions,
        baseline: dict[int, int] | None = None,
    ) -> tuple[Solution, dict[str, object]]:
        """يعيد (الحل، بيانات تشخيصية)."""
        ...


class NativeAlnsBackend:
    """الخلفية الافتراضية: إدراج بالندم + ALNS مع تلدين محاكى."""

    name = "native_alns"
    version = "1.0.0"

    def solve(
        self,
        problem: Problem,
        options: SolveOptions,
        baseline: dict[int, int] | None = None,
    ) -> tuple[Solution, dict[str, object]]:
        optimizer = RouteOptimizer(problem, options)
        solution = optimizer.solve(baseline)
        return solution, {
            "iterations": optimizer.iterations,
            "improvements": optimizer.improvements,
            "seed": options.seed,
        }


class OrToolsBackend:
    """خلفية OR-Tools (CP-SAT / Routing) — **غير مفعّلة في هذه البيئة**.

    بيئة التطوير الحالية بلا وصول إلى مستودعات الحزم، فلم يكن ممكنًا تثبيت
    ``ortools`` ولا تشغيل هذه الخلفية ولا اختبارها. لذلك هي تُصرّح بعدم
    توفرها بدل أن تدّعي عملًا لم يُختبر.

    لتفعيلها في بيئة الإنتاج:

    1. ``pip install ortools``
    2. ``MASAR_OPTIMIZER_BACKEND=ortools``
    3. تنفيذ ``solve`` ببناء ``RoutingIndexManager`` مع بُعدَي الزمن والمسافة،
       وقيود ``AddPickupAndDelivery`` و``CumulVar`` للنوافذ، ثم تحويل الناتج
       إلى ``Solution`` وتمريره على ``evaluate_route`` قبل القبول.
    """

    name = "ortools"
    version = "unavailable"

    def solve(self, problem, options, baseline=None):
        raise DependencyUnavailable(
            "خلفية OR-Tools غير مثبّتة في هذه البيئة. "
            "الخلفية الفعالة والمختبَرة هي native_alns. "
            "راجع docs/07-optimization-engine.md لخطوات التفعيل.",
            backend="ortools",
        )


BACKENDS: dict[str, SolverBackend] = {
    NativeAlnsBackend.name: NativeAlnsBackend(),
    OrToolsBackend.name: OrToolsBackend(),
}


def get_backend(name: str | None = None) -> SolverBackend:
    from masar_core.config import get_config

    key = (name or get_config().optimizer.backend).lower()
    backend = BACKENDS.get(key)
    if backend is None:
        raise DependencyUnavailable(f"خلفية حلّ غير معروفة: {key}")
    return backend
