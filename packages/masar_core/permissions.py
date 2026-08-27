"""فهرس الصلاحيات ومصفوفة الأدوار.

مبدأ حاكم (§2 من المتطلبات): الصلاحية تُطبَّق في **ثلاث طبقات**:

1. **الواجهة** — إخفاء الأزرار (راحة استخدام فقط، ليست أمانًا).
2. **الخادم** — ``require(permission)`` على كل موجّه، وفحص النطاق على كل كائن.
3. **قاعدة البيانات** — سياسات ``ROW LEVEL SECURITY`` مبنية على متغيرات الجلسة.

الطبقة الثالثة هي الضمانة الحقيقية: حتى لو أخطأ استعلام في طبقة الخادم، لا يعيد
المحرك صفوفًا خارج نطاق المستخدم.
"""

from __future__ import annotations

from dataclasses import dataclass

from .constants import Role


@dataclass(frozen=True, slots=True)
class Permission:
    key: str
    name_ar: str
    group: str
    #: هل تتطلب هذه الصلاحية سببًا مكتوبًا عند التنفيذ؟
    requires_reason: bool = False


def _p(key: str, name_ar: str, group: str, requires_reason: bool = False) -> Permission:
    return Permission(key, name_ar, group, requires_reason)


PERMISSIONS: tuple[Permission, ...] = (
    # ---------------------------------------------------------- المستخدمون --
    _p("users.read", "عرض المستخدمين", "المستخدمون"),
    _p("users.write", "إنشاء وتعديل المستخدمين", "المستخدمون"),
    _p("users.disable", "تعطيل مستخدم", "المستخدمون", True),
    _p("roles.read", "عرض الأدوار والصلاحيات", "المستخدمون"),
    _p("roles.write", "إنشاء وتعديل الأدوار", "المستخدمون", True),

    # ------------------------------------------------------ البيانات الرئيسية --
    _p("geo.read", "عرض المناطق والمدن", "البيانات الرئيسية"),
    _p("geo.write", "إدارة المناطق والمدن", "البيانات الرئيسية"),
    _p("hubs.read", "عرض مراكز الانطلاق", "البيانات الرئيسية"),
    _p("hubs.write", "إدارة مراكز الانطلاق", "البيانات الرئيسية"),
    _p("facilities.read", "عرض الجهات الصحية", "البيانات الرئيسية"),
    _p("facilities.write", "إدارة الجهات الصحية", "البيانات الرئيسية"),
    _p("drivers.read", "عرض السائقين", "البيانات الرئيسية"),
    _p("drivers.write", "إدارة السائقين", "البيانات الرئيسية"),
    _p("vehicles.read", "عرض المركبات والصناديق", "البيانات الرئيسية"),
    _p("vehicles.write", "إدارة المركبات والصناديق", "البيانات الرئيسية"),
    _p("settings.read", "عرض الإعدادات والقيود", "الإعدادات"),
    _p("settings.write", "تعديل الإعدادات والقيود", "الإعدادات", True),
    _p("integrations.read", "عرض التكاملات", "الإعدادات"),
    _p("integrations.write", "إدارة التكاملات والمفاتيح", "الإعدادات", True),

    # ----------------------------------------------------------- التخطيط ----
    _p("schedule.upload", "رفع الجدول الأسبوعي الوطني", "التخطيط"),
    _p("schedule.read", "عرض الجداول المرفوعة", "التخطيط"),
    _p("schedule.commit", "اعتماد الاستيراد وإنشاء الشحنات", "التخطيط"),
    _p("plan.optimize", "تشغيل محرك المسارات", "التخطيط"),
    _p("plan.read", "عرض الخطط والمعاينة", "التخطيط"),
    _p("plan.approve", "اعتماد الخطة", "التخطيط"),
    _p("plan.dispatch", "إرسال الخطة لمراكز الانطلاق", "التخطيط"),
    _p("plan.compare", "مقارنة الخطط والخطة المرجعية", "التخطيط"),
    _p("driver_estimation.read", "عرض تقدير السائقين", "التخطيط"),

    # ----------------------------------------------------------- التشغيل ----
    _p("routes.read", "عرض الرحلات", "التشغيل"),
    _p("routes.assign", "إسناد الرحلات للسائقين", "التشغيل"),
    _p("routes.unassign", "إزالة سائق من رحلة", "التشغيل", True),
    _p("routes.publish", "نشر خطة اليوم", "التشغيل"),
    _p("routes.modify_published", "تعديل رحلة منشورة", "التشغيل", True),
    _p("routes.execute", "تنفيذ محطات الرحلة (سائق)", "التشغيل"),
    _p("shipments.read", "عرض الشحنات", "التشغيل"),
    _p("shipments.cancel", "إلغاء شحنة قبل الالتقاط", "التشغيل", True),
    _p("ondemand.create", "إنشاء طلب فوري", "التشغيل"),
    _p("ondemand.review", "مراجعة الطلبات الفورية", "التشغيل"),
    _p("ondemand.cancel_own", "إلغاء الطلب الخاص قبل الالتقاط", "التشغيل", True),
    _p("documents.upload", "رفع مستندات الالتقاط والتسليم", "التشغيل"),
    _p("documents.read", "الاطلاع على المستندات", "التشغيل"),
    _p("tracking.read", "عرض مواقع السائقين", "التشغيل"),
    _p("tracking.publish", "إرسال موقع السائق", "التشغيل"),
    _p("alerts.read", "عرض التنبيهات", "التشغيل"),
    _p("alerts.act", "تسجيل إجراء على تنبيه", "التشغيل", True),
    _p("exceptions.record", "تسجيل حالة استثنائية", "التشغيل"),
    _p("exceptions.resolve", "حسم الحالة الاستثنائية", "التشغيل", True),

    # ------------------------------------------------------------ الرقابة ---
    _p("temperature.read", "عرض قراءات الحرارة", "الرقابة"),
    _p("temperature.ingest", "استقبال قراءات من الحساسات", "الرقابة"),
    _p("reports.read", "عرض التقارير ومؤشرات الأداء", "الرقابة"),
    _p("reports.export", "تصدير التقارير", "الرقابة"),
    _p("reports.national", "التقارير الوطنية والإقليمية", "الرقابة"),
    _p("audit.read", "الاطلاع على سجل التدقيق", "الرقابة"),
    _p("hub_changes.monitor", "مراقبة تعديلات مراكز الانطلاق", "الرقابة"),

    # -------------------------------------------------------------- خطر ----
    _p("data.hard_delete", "حذف نهائي للبيانات التشغيلية", "خطر", True),
    _p("data.archive", "أرشفة واستعادة", "خطر", True),
)

PERMISSION_INDEX: dict[str, Permission] = {p.key: p for p in PERMISSIONS}


def _keys(*prefixes_or_keys: str) -> set[str]:
    out: set[str] = set()
    for item in prefixes_or_keys:
        if item.endswith(".*"):
            prefix = item[:-1]
            out.update(k for k in PERMISSION_INDEX if k.startswith(prefix))
        else:
            if item not in PERMISSION_INDEX:
                raise KeyError(f"صلاحية غير معرّفة: {item}")
            out.add(item)
    return out


#: مصفوفة الأدوار الافتراضية. الأدوار المخصصة تُخزَّن في قاعدة البيانات وتَرِث منها.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    Role.ADMIN: frozenset(PERMISSION_INDEX) - {"data.hard_delete"},

    Role.CENTRAL_PLANNER: frozenset(_keys(
        "geo.read", "hubs.read", "facilities.read", "drivers.read", "vehicles.read",
        "settings.read",
        "schedule.upload", "schedule.read", "schedule.commit",
        "plan.optimize", "plan.read", "plan.approve", "plan.dispatch", "plan.compare",
        "driver_estimation.read",
        "routes.read", "shipments.read",
        "tracking.read", "alerts.read",
        "temperature.read",
        "reports.read", "reports.export", "reports.national",
        "hub_changes.monitor", "audit.read",
        "documents.read",
    )),

    Role.HUB_SUPERVISOR: frozenset(_keys(
        "geo.read", "hubs.read", "facilities.read", "drivers.read", "vehicles.read",
        "settings.read", "plan.read", "driver_estimation.read",
        "routes.read", "routes.assign", "routes.unassign", "routes.publish",
        "routes.modify_published",
        "shipments.read", "shipments.cancel",
        "ondemand.review",
        "documents.read",
        "tracking.read",
        "alerts.read", "alerts.act",
        "exceptions.record", "exceptions.resolve",
        "temperature.read",
        "reports.read", "reports.export",
    )),

    Role.CONTROL_TOWER: frozenset(_keys(
        "geo.read", "hubs.read", "facilities.read", "drivers.read",
        "routes.read", "shipments.read",
        "ondemand.review",
        "tracking.read", "alerts.read", "alerts.act",
        "temperature.read", "documents.read",
        "reports.read", "reports.national",
    )),

    Role.DRIVER: frozenset(_keys(
        "routes.read", "routes.execute",
        "shipments.read",
        "documents.upload",
        "tracking.publish",
        "exceptions.record",
        "alerts.read",
    )),

    # ملاحظة: قراءة الجهات مسموحة، لكن **سياسة RLS** تحصر ما يراه فعليًا في
    # جهته + جهات التسليم الممكنة (مختبرات وبنوك دم). الصلاحية تفتح الشاشة،
    # وقاعدة البيانات تحدد الصفوف.
    Role.EXTERNAL_REQUESTER: frozenset(_keys(
        "ondemand.create", "ondemand.cancel_own",
        "shipments.read",
        "facilities.read",
        "alerts.read",
    )),

    Role.AUDITOR: frozenset(_keys(
        "geo.read", "hubs.read", "facilities.read", "drivers.read", "vehicles.read",
        "settings.read", "plan.read", "routes.read", "shipments.read",
        "reports.read", "reports.national", "reports.export",
        "audit.read", "hub_changes.monitor", "temperature.read",
    )),

    Role.INTEGRATION: frozenset(_keys(
        "shipments.read", "ondemand.create", "temperature.ingest", "tracking.publish",
    )),
}


#: أنواع نطاق البيانات التي تُقيّد بها كل صلاحية قراءة
SCOPE_RULES: dict[str, str] = {
    Role.ADMIN: "ALL",
    Role.CENTRAL_PLANNER: "ALL",
    Role.CONTROL_TOWER: "ALL",
    Role.AUDITOR: "ALL",
    Role.HUB_SUPERVISOR: "OWN_HUBS",
    Role.DRIVER: "OWN_ROUTES",
    Role.EXTERNAL_REQUESTER: "OWN_FACILITY_REQUESTS",
    Role.INTEGRATION: "OWN_FACILITY_REQUESTS",
}


def permissions_for(role: str) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: str, permission: str, extra: set[str] | None = None) -> bool:
    if extra and permission in extra:
        return True
    return permission in permissions_for(role)


def requires_reason(permission: str) -> bool:
    p = PERMISSION_INDEX.get(permission)
    return bool(p and p.requires_reason)


def matrix_rows() -> list[dict[str, object]]:
    """يبني مصفوفة الصلاحيات الكاملة (تُستخدم في الوثائق وفي API)."""
    rows: list[dict[str, object]] = []
    for perm in PERMISSIONS:
        row: dict[str, object] = {
            "key": perm.key,
            "name_ar": perm.name_ar,
            "group": perm.group,
            "requires_reason": perm.requires_reason,
        }
        for role in Role:
            row[role.value] = perm.key in permissions_for(role)
        rows.append(row)
    return rows
