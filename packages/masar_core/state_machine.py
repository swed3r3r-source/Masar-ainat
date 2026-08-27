"""آلات الحالة — تمنع الانتقالات غير المنطقية (§21).

التطبيق مزدوج: هذا الملف يفرض القاعدة في طبقة الخدمة، ودالة
``masar_shipment_transition_guard`` في الترحيل 0002 تفرض نفس القاعدة داخل
قاعدة البيانات عبر ``TRIGGER``. أي كتابة مباشرة على الجدول — حتى من خارج
التطبيق — تُرفض إن خالفت الانتقال.
"""

from __future__ import annotations

from dataclasses import dataclass

from .constants import (
    IN_CUSTODY_STATUSES,
    PlanStatus,
    RouteStatus,
    ShipmentStatus as S,
)
from .errors import InvalidTransition


@dataclass(frozen=True, slots=True)
class Transition:
    source: str
    target: str
    #: الصلاحية المطلوبة لتنفيذ هذا الانتقال
    permission: str
    #: هل يتطلب سببًا مكتوبًا؟
    requires_reason: bool = False
    #: وصف عربي للانتقال (يظهر في سجل التدقيق وفي واجهة التتبع)
    label_ar: str = ""


# ============================================== آلة حالة الشحنة ==============

_SHIPMENT_TRANSITIONS: tuple[Transition, ...] = (
    # مسار الجدولة
    Transition(S.DRAFT, S.VALIDATED, "schedule.commit", label_ar="اجتاز التحقق"),
    Transition(S.DRAFT, S.REJECTED, "schedule.commit", True, "استُبعد في التحقق"),
    Transition(S.VALIDATED, S.UNPLANNABLE, "plan.optimize", label_ar="تعذر التخطيط"),
    Transition(S.VALIDATED, S.PLANNED, "plan.optimize", label_ar="أُدرج في رحلة"),
    Transition(S.UNPLANNABLE, S.PLANNED, "plan.optimize", label_ar="أُعيد التخطيط بنجاح"),
    Transition(S.UNPLANNABLE, S.VALIDATED, "plan.optimize", label_ar="أُعيد للتخطيط"),
    # إعادة تشغيل المحرك على يوم مسودة قد تُخرج شحنة كانت مخططة: غياب سائق،
    # أو تغيّر إعداد، أو إضافة شحنة أولى بالأسبقية. هذا **ناتج تخطيط مشروع**
    # يجب أن يُسجَّل بسببه لا أن يُسقط التشغيل كله. (اليوم المنشور محمي أصلًا
    # بفحص مستقل يمنع إعادة التشغيل عليه.)
    Transition(S.PLANNED, S.UNPLANNABLE, "plan.optimize",
               label_ar="خرج من الخطة بعد إعادة التشغيل"),
    Transition(S.PLANNED, S.VALIDATED, "plan.optimize", label_ar="أُعيد للتخطيط"),

    # مسار الطلب الفوري
    Transition(S.DRAFT, S.PENDING_APPROVAL, "ondemand.create", label_ar="أُرسل للمراجعة"),
    Transition(S.PENDING_APPROVAL, S.PENDING_ASSIGNMENT, "ondemand.review",
               label_ar="اعتُمد الطلب"),
    Transition(S.PENDING_APPROVAL, S.REJECTED, "ondemand.review", True, "رُفض الطلب"),
    Transition(S.PENDING_APPROVAL, S.CANCELLED_BEFORE_PICKUP, "ondemand.cancel_own",
               True, "أُلغي قبل المراجعة"),
    Transition(S.PENDING_ASSIGNMENT, S.PLANNED, "plan.optimize", label_ar="أُدرج في رحلة"),
    Transition(S.PENDING_ASSIGNMENT, S.UNPLANNABLE, "plan.optimize",
               label_ar="تعذر الإدراج"),

    # الإسناد والنشر
    Transition(S.PLANNED, S.ASSIGNED, "routes.assign", label_ar="أُسند لسائق"),
    Transition(S.PLANNED, S.PENDING_ASSIGNMENT, "routes.unassign", True,
               "أُعيد لقائمة الانتظار"),
    Transition(S.ASSIGNED, S.PENDING_ASSIGNMENT, "routes.unassign", True,
               "أُزيل السائق"),
    Transition(S.ASSIGNED, S.PUBLISHED, "routes.publish", label_ar="نُشر لليوم"),
    Transition(S.PUBLISHED, S.PENDING_ASSIGNMENT, "routes.unassign", True,
               "أُزيل السائق بعد النشر"),
    Transition(S.PUBLISHED, S.IN_PROGRESS, "routes.execute", label_ar="بدأت الرحلة"),

    # التنفيذ
    Transition(S.IN_PROGRESS, S.ARRIVED_PICKUP, "routes.execute",
               label_ar="وصل لموقع الالتقاط"),
    Transition(S.ARRIVED_PICKUP, S.PICKED_UP, "routes.execute", label_ar="تم الالتقاط"),
    Transition(S.PICKED_UP, S.ARRIVED_DELIVERY, "routes.execute",
               label_ar="وصل لموقع التسليم"),
    Transition(S.ARRIVED_DELIVERY, S.DELIVERED, "routes.execute", label_ar="تم التسليم"),
    Transition(S.DELIVERED, S.COMPLETED, "routes.execute", label_ar="اكتملت الشحنة"),

    # الإلغاء قبل الالتقاط — من أي حالة قبل PICKED_UP فقط
    Transition(S.PLANNED, S.CANCELLED_BEFORE_PICKUP, "shipments.cancel", True,
               "أُلغيت قبل الالتقاط"),
    Transition(S.ASSIGNED, S.CANCELLED_BEFORE_PICKUP, "shipments.cancel", True,
               "أُلغيت قبل الالتقاط"),
    Transition(S.PUBLISHED, S.CANCELLED_BEFORE_PICKUP, "shipments.cancel", True,
               "أُلغيت قبل الالتقاط"),
    Transition(S.IN_PROGRESS, S.CANCELLED_BEFORE_PICKUP, "shipments.cancel", True,
               "أُلغيت قبل الالتقاط"),
    Transition(S.ARRIVED_PICKUP, S.CANCELLED_BEFORE_PICKUP, "shipments.cancel", True,
               "أُلغيت عند الموقع قبل الالتقاط"),
    Transition(S.PENDING_ASSIGNMENT, S.CANCELLED_BEFORE_PICKUP, "shipments.cancel", True,
               "أُلغيت قبل الإسناد"),
    Transition(S.VALIDATED, S.CANCELLED_BEFORE_PICKUP, "shipments.cancel", True,
               "أُلغيت قبل التخطيط"),
    Transition(S.UNPLANNABLE, S.CANCELLED_BEFORE_PICKUP, "shipments.cancel", True,
               "أُلغيت بعد تعذر التخطيط"),

    # الاستثناءات — من أي حالة تنفيذية
    Transition(S.PUBLISHED, S.EXCEPTION, "exceptions.record", label_ar="حالة استثنائية"),
    Transition(S.IN_PROGRESS, S.EXCEPTION, "exceptions.record", label_ar="حالة استثنائية"),
    Transition(S.ARRIVED_PICKUP, S.EXCEPTION, "exceptions.record",
               label_ar="تعذر الالتقاط"),
    Transition(S.PICKED_UP, S.EXCEPTION, "exceptions.record", label_ar="حالة استثنائية"),
    Transition(S.ARRIVED_DELIVERY, S.EXCEPTION, "exceptions.record",
               label_ar="تعذر التسليم"),

    # حسم الاستثناء
    Transition(S.EXCEPTION, S.IN_PROGRESS, "exceptions.resolve", True, "استُؤنف التنفيذ"),
    Transition(S.EXCEPTION, S.ARRIVED_PICKUP, "exceptions.resolve", True,
               "استُؤنف عند الالتقاط"),
    Transition(S.EXCEPTION, S.PICKED_UP, "exceptions.resolve", True,
               "استُؤنف بعد الالتقاط"),
    Transition(S.EXCEPTION, S.ARRIVED_DELIVERY, "exceptions.resolve", True,
               "استُؤنف عند التسليم"),
    Transition(S.EXCEPTION, S.PENDING_ASSIGNMENT, "exceptions.resolve", True,
               "أُعيدت الجدولة"),
    Transition(S.EXCEPTION, S.FAILED, "exceptions.resolve", True, "أُغلقت كفاشلة"),
    Transition(S.EXCEPTION, S.CANCELLED_BEFORE_PICKUP, "exceptions.resolve", True,
               "أُلغيت قبل الالتقاط"),
    Transition(S.EXCEPTION, S.COMPLETED, "exceptions.resolve", True,
               "أُغلقت بعد حسم الاستثناء"),
    Transition(S.EXCEPTION, S.DELIVERED, "exceptions.resolve", True,
               "سُلّمت بعد حسم الاستثناء"),
)

SHIPMENT_TRANSITIONS: dict[tuple[str, str], Transition] = {
    (t.source, t.target): t for t in _SHIPMENT_TRANSITIONS
}


# ============================================= آلة حالة الرحلة ==============

R = RouteStatus

_ROUTE_TRANSITIONS: tuple[Transition, ...] = (
    Transition(R.DRAFT, R.PLANNED, "plan.optimize", label_ar="اعتُمدت في الخطة"),
    Transition(R.PLANNED, R.ASSIGNED, "routes.assign", label_ar="أُسندت لسائق"),
    Transition(R.ASSIGNED, R.PLANNED, "routes.unassign", True, "أُزيل السائق"),
    Transition(R.ASSIGNED, R.PUBLISHED, "routes.publish", label_ar="نُشرت"),
    Transition(R.PUBLISHED, R.ASSIGNED, "routes.unassign", True, "سُحب النشر"),
    Transition(R.PUBLISHED, R.IN_PROGRESS, "routes.execute", label_ar="بدأ التنفيذ"),
    Transition(R.IN_PROGRESS, R.COMPLETED, "routes.execute", label_ar="اكتملت"),
    Transition(R.PLANNED, R.CANCELLED, "routes.unassign", True, "أُلغيت"),
    Transition(R.ASSIGNED, R.CANCELLED, "routes.unassign", True, "أُلغيت"),
    Transition(R.PUBLISHED, R.CANCELLED, "routes.unassign", True, "أُلغيت بعد النشر"),
    Transition(R.IN_PROGRESS, R.CANCELLED, "routes.unassign", True, "أُلغيت أثناء التنفيذ"),
)

ROUTE_TRANSITIONS: dict[tuple[str, str], Transition] = {
    (t.source, t.target): t for t in _ROUTE_TRANSITIONS
}


# ============================================== آلة حالة الخطة ==============

P = PlanStatus

_PLAN_TRANSITIONS: tuple[Transition, ...] = (
    Transition(P.DRAFT, P.OPTIMIZING, "plan.optimize", label_ar="بدأ التحسين"),
    Transition(P.OPTIMIZING, P.OPTIMIZED, "plan.optimize", label_ar="انتهى التحسين"),
    Transition(P.OPTIMIZING, P.FAILED, "plan.optimize", label_ar="فشل المحرك"),
    Transition(P.OPTIMIZED, P.OPTIMIZING, "plan.optimize", label_ar="إعادة تشغيل"),
    Transition(P.OPTIMIZED, P.APPROVED, "plan.approve", label_ar="اعتُمدت"),
    Transition(P.APPROVED, P.DISPATCHED, "plan.dispatch", label_ar="أُرسلت للمراكز"),
    Transition(P.APPROVED, P.OPTIMIZING, "plan.optimize", label_ar="سُحب الاعتماد"),
    Transition(P.DISPATCHED, P.SUPERSEDED, "plan.approve", label_ar="استُبدلت بخطة أحدث"),
    Transition(P.OPTIMIZED, P.SUPERSEDED, "plan.optimize", label_ar="استُبدلت"),
    Transition(P.FAILED, P.OPTIMIZING, "plan.optimize", label_ar="إعادة محاولة"),
)

PLAN_TRANSITIONS: dict[tuple[str, str], Transition] = {
    (t.source, t.target): t for t in _PLAN_TRANSITIONS
}


# ==================================================== واجهة الفحص ===========

class StateMachine:
    def __init__(self, name: str, table: dict[tuple[str, str], Transition]) -> None:
        self.name = name
        self.table = table

    def allowed_targets(self, source: str) -> list[str]:
        return sorted({t for (s, t) in self.table if s == source})

    def can(self, source: str, target: str) -> bool:
        return (source, target) in self.table

    def transition(self, source: str, target: str) -> Transition:
        try:
            return self.table[(source, target)]
        except KeyError:
            raise InvalidTransition(
                f"انتقال غير مسموح في {self.name}: {source} ← {target}. "
                f"الانتقالات المتاحة من {source}: "
                f"{', '.join(self.allowed_targets(source)) or 'لا شيء'}"
            ) from None

    def check(
        self,
        source: str,
        target: str,
        *,
        reason: str | None = None,
    ) -> Transition:
        """يتحقق من صحة الانتقال ومن وجود سبب مكتوب عند اللزوم."""
        transition = self.transition(source, target)
        if transition.requires_reason and not (reason or "").strip():
            raise InvalidTransition(
                f"الانتقال {source} ← {target} يتطلب سببًا مكتوبًا"
            )
        return transition


shipment_sm = StateMachine("الشحنة", SHIPMENT_TRANSITIONS)
route_sm = StateMachine("الرحلة", ROUTE_TRANSITIONS)
plan_sm = StateMachine("الخطة", PLAN_TRANSITIONS)


# ================================================= قواعد عبر-كيانية =========

def assert_can_cancel_before_pickup(current_status: str) -> None:
    """§21: لا يمكن الإلغاء كـ«إلغاء قبل الالتقاط» بعد تسجيل الالتقاط."""
    if current_status in IN_CUSTODY_STATUSES or current_status in (
        S.DELIVERED, S.COMPLETED
    ):
        raise InvalidTransition(
            "لا يمكن تسجيل «إلغاء قبل الالتقاط» بعد تسجيل الالتقاط — "
            f"الحالة الحالية: {current_status}. استخدم مسار الاستثناء."
        )


def assert_route_completable(shipment_statuses: list[str]) -> None:
    """§21: لا يمكن إكمال الرحلة مع وجود شحنة غير محسومة."""
    from .constants import TERMINAL_SHIPMENT_STATUSES

    unresolved = [s for s in shipment_statuses if s not in TERMINAL_SHIPMENT_STATUSES]
    if unresolved:
        raise InvalidTransition(
            "لا يمكن إكمال الرحلة: توجد شحنات غير محسومة بحالات "
            f"{', '.join(sorted(set(unresolved)))}"
        )


def assert_delivery_after_pickup(picked_up_at, delivering_at) -> None:
    """§21: لا يمكن التسليم قبل الالتقاط."""
    if picked_up_at is None:
        raise InvalidTransition("لا يمكن تسجيل التسليم قبل تسجيل الالتقاط")
    if delivering_at is not None and delivering_at < picked_up_at:
        raise InvalidTransition(
            "زمن التسليم أسبق من زمن الالتقاط — بيانات غير متسقة"
        )


def assert_route_startable(route_status: str, planned_date, today) -> None:
    """§5/§21: لا يبدأ السائق رحلة غير منشورة ولا قبل تاريخها."""
    if route_status != RouteStatus.PUBLISHED:
        raise InvalidTransition(
            f"لا يمكن بدء رحلة حالتها {route_status} — يجب أن تكون PUBLISHED"
        )
    if planned_date > today:
        raise InvalidTransition(
            f"لا يمكن بدء الرحلة قبل تاريخها المخطط ({planned_date})"
        )


def export_diagram(sm: StateMachine) -> str:
    """يولّد مخطط Mermaid لآلة الحالة (يُستخدم في التوثيق وفي الواجهة)."""
    lines = ["stateDiagram-v2"]
    for (source, target), transition in sorted(sm.table.items()):
        label = transition.label_ar or ""
        lines.append(f"    {source} --> {target}: {label}")
    return "\n".join(lines)
