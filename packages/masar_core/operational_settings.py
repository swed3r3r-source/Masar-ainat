"""القيم التشغيلية القابلة للإعداد (§13) وآلية حلّها هرميًا.

مبدأ حاكم (§2): **لا قيمة تشغيلية مكتوبة داخل الكود.** كل قيمة هنا لها:
* مفتاح ثابت، ونوع، ومدى مسموح، ووحدة، ووصف عربي.
* قيمة افتراضية على مستوى المملكة.
* إمكانية التجاوز على أي مستوى: `KINGDOM → REGION → CITY → HUB`.

الحل يتم بمبدأ **الأخص يفوز**: قيمة المركز تتقدم على المدينة، والمدينة على
المنطقة، والمنطقة على المملكة.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .errors import ValidationError

ScopeType = Literal["KINGDOM", "REGION", "CITY", "HUB"]

#: ترتيب الأخصّية — الأكبر يفوز
SCOPE_PRECEDENCE: dict[str, int] = {"KINGDOM": 0, "REGION": 1, "CITY": 2, "HUB": 3}


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    name_ar: str
    kind: str                      # int | float | bool | str | list[str]
    default: Any
    unit_ar: str = ""
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] | None = None
    group_ar: str = "عام"
    description_ar: str = ""


SETTING_SPECS: tuple[SettingSpec, ...] = (
    # ------------------------------------------------- نوافذ الالتقاط ------
    SettingSpec(
        "pickup_window_before_minutes", "سماحية الوصول قبل الموعد", "int", 15, "دقيقة",
        0, 240, group_ar="نوافذ الالتقاط",
        description_ar="كم دقيقة قبل الموعد المتفق عليه تُفتح نافذة الالتقاط.",
    ),
    SettingSpec(
        "pickup_window_after_minutes", "سماحية الوصول بعد الموعد", "int", 15, "دقيقة",
        0, 240, group_ar="نوافذ الالتقاط",
        description_ar="كم دقيقة بعد الموعد تبقى نافذة الالتقاط مفتوحة.",
    ),
    SettingSpec(
        "allow_early_arrival", "السماح بالوصول قبل بداية النافذة", "bool", False,
        group_ar="نوافذ الالتقاط",
        description_ar="عند التعطيل يُترجم الوصول المبكر إلى انتظار يُحتسب ضمن الوردية.",
    ),
    SettingSpec(
        "post_pickup_departure_minutes", "الفاصل بعد موعد الالتقاط قبل الحركة التالية",
        "int", 15, "دقيقة", 0, 120, group_ar="نوافذ الالتقاط",
        description_ar="القاعدة التشغيلية: لا تبدأ الحركة التالية قبل مرور هذه المدة "
                       "من الموعد المخطط للالتقاط.",
    ),

    # ------------------------------------------------------- زمن الطريق ---
    SettingSpec(
        "min_event_gap_minutes", "الحد الأدنى للفاصل بين حدثين", "int", 0, "دقيقة",
        0, 120, group_ar="زمن الطريق",
        description_ar="حدّ أدنى يُطبَّق فوق زمن الطريق الحقيقي ولا يحل محله. "
                       "عرعر ٢٠ · المحافظات ١٠ · الرياض ٠ (زمن الطريق فقط).",
    ),
    SettingSpec(
        "use_time_dependent_travel", "استخدام أزمنة قيادة متغيرة بالذروة", "bool", False,
        group_ar="زمن الطريق",
        description_ar="يفعّل مصفوفات زمن منفصلة لفترات الذروة بدل مصفوفة واحدة.",
    ),
    SettingSpec(
        "peak_periods", "فترات الذروة", "list[str]",
        ["06:30-09:00", "15:30-18:30"], group_ar="زمن الطريق",
        description_ar="فترات تُستخدم فيها مصفوفة زمن الذروة.",
    ),
    SettingSpec(
        "peak_travel_multiplier", "معامل زمن الذروة", "float", 1.45, "×", 1.0, 4.0,
        group_ar="زمن الطريق",
        description_ar="يُستخدم فقط عند تعذر الحصول على مصفوفة ذروة حقيقية من مزوّد الطرق.",
    ),

    # ------------------------------------------------------- الوردية ------
    SettingSpec(
        "max_shift_hours", "الحد الأقصى للوردية", "float", 10.0, "ساعة", 1.0, 16.0,
        group_ar="الوردية",
        description_ar="من بداية أول رحلة حتى آخر تسليم.",
    ),
    SettingSpec(
        "count_return_leg_in_shift", "احتساب زمن العودة ضمن الوردية", "bool", False,
        group_ar="الوردية",
        description_ar="افتراضيًا لا يُحتسب لأن الرحلة تنتهي عند آخر تسليم.",
    ),
    SettingSpec(
        "require_return_to_hub", "إلزام العودة إلى مركز الانطلاق", "bool", False,
        group_ar="الوردية",
        description_ar="القاعدة الافتراضية: العودة غير إلزامية.",
    ),
    SettingSpec(
        "max_routes_per_driver_per_day", "أقصى عدد رحلات للسائق يوميًا", "int", 3, "رحلة",
        1, 10, group_ar="الوردية",
        description_ar="الرحلة الثانية تبدأ من موقع آخر تسليم لا من مركز الانطلاق "
                       "(§12/HC-10)، وهذا الحد يقيّد عدد الرحلات المتسلسلة في اليوم.",
    ),
    SettingSpec(
        "break_after_hours", "استراحة إلزامية بعد", "float", 0.0, "ساعة", 0.0, 8.0,
        group_ar="الوردية",
        description_ar="صفر يعني بلا استراحة إلزامية في نموذج التخطيط.",
    ),
    SettingSpec(
        "break_duration_minutes", "مدة الاستراحة", "int", 30, "دقيقة", 0, 120,
        group_ar="الوردية",
        description_ar="تُطبَّق فقط عند ضبط «استراحة إلزامية بعد» بقيمة أكبر من صفر.",
    ),

    # -------------------------------------------------- الرحلات البعيدة ---
    SettingSpec(
        "long_haul_km", "تعريف الرحلة البعيدة", "float", 150.0, "كم", 10.0, 2000.0,
        group_ar="الرحلات البعيدة",
        description_ar="مسافة الطريق من مركز الانطلاق إلى أبعد نقطة في الرحلة.",
    ),
    SettingSpec(
        "max_long_haul_per_driver_per_day", "أقصى رحلات بعيدة للسائق يوميًا", "int", 1,
        "رحلة", 0, 5, group_ar="الرحلات البعيدة",
        description_ar="قيد HC-15: يمنع إسناد رحلتين بعيدتين للسائق نفسه في اليوم.",
    ),
    SettingSpec(
        "post_long_haul_policy", "سياسة ما بعد الرحلة البعيدة", "str", "NO_CITY_HOPPING",
        choices=("NONE", "NO_CITY_HOPPING", "END_SHIFT"), group_ar="الرحلات البعيدة",
        description_ar="NO_CITY_HOPPING تمنع إضافة تنقلات مدينة قصيرة بعد رحلة بعيدة.",
    ),
    SettingSpec(
        "post_long_haul_min_stop_km", "أدنى مسافة لمحطة بعد رحلة بعيدة", "float", 25.0,
        "كم", 0.0, 500.0, group_ar="الرحلات البعيدة",
        description_ar="يُطبَّق عند اختيار NO_CITY_HOPPING.",
    ),

    # -------------------------------------------------- قيود نوع الجهة ----
    SettingSpec(
        "enforce_facility_mixing_rule", "منع خلط أنواع الجهات", "bool", True,
        group_ar="قيود الجهات",
        description_ar="يمنع جمع المستشفيات والمراكز الصحية على السائق نفسه.",
    ),
    SettingSpec(
        "mixing_exempt_facility_types", "أنواع مستثناة من قيد الخلط", "list[str]",
        ["BLOOD_BANK"], group_ar="قيود الجهات",
        description_ar="بنك الدم مستثنى افتراضيًا (§12/14).",
    ),

    # ------------------------------------------------------ مدة الخدمة ----
    SettingSpec(
        "default_service_minutes", "مدة الخدمة الافتراضية", "int", 10, "دقيقة", 1, 180,
        group_ar="مدة الخدمة",
        description_ar="تُستخدم فقط إن لم تُحدَّد مدة خدمة للجهة نفسها.",
    ),
    SettingSpec(
        "hub_load_minutes", "مدة التجهيز في مركز الانطلاق", "int", 15, "دقيقة", 0, 120,
        group_ar="مدة الخدمة",
        description_ar="تُحتسب ضمن الوردية قبل انطلاق أول مقطع من مركز الانطلاق.",
    ),

    # ------------------------------------------------------------- SLA ----
    SettingSpec(
        "sla_risk_threshold_minutes", "عتبة إنذار اقتراب SLA", "int", 30, "دقيقة", 5, 240,
        group_ar="SLA والتنبيهات",
        description_ar="كم دقيقة قبل الموعد النهائي يُرفع تنبيه «SLA في خطر».",
    ),
    SettingSpec(
        "pickup_approaching_minutes", "إنذار اقتراب موعد الالتقاط", "int", 20, "دقيقة",
        5, 180, group_ar="SLA والتنبيهات",
        description_ar="كم دقيقة قبل بداية نافذة الالتقاط يُرفع تنبيه اقتراب الموعد.",
    ),
    SettingSpec(
        "pickup_late_grace_minutes", "سماحية تأخر الالتقاط قبل التنبيه", "int", 10,
        "دقيقة", 0, 120, group_ar="SLA والتنبيهات",
        description_ar="مهلة تسامح بعد نهاية النافذة قبل اعتبار الالتقاط متأخرًا.",
    ),
    SettingSpec(
        "tracking_stale_seconds", "عتبة توقف تحديث الموقع", "int", 180, "ثانية", 30, 3600,
        group_ar="SLA والتنبيهات",
        description_ar="بعد هذه المدة بلا تحديث موقع يُرفع تنبيه توقف التتبع.",
    ),

    # -------------------------------------------------------- التحسين ----
    SettingSpec(
        "solve_time_limit_seconds", "مهلة تشغيل المحرك", "float", 25.0, "ثانية", 1.0,
        1800.0, group_ar="التحسين",
        description_ar="سقف زمن البحث؛ عند تجاوزه يكمل المحرك بإدراج أول-ما-يصلح دون أي تنازل عن القيود الصلبة.",
    ),
    SettingSpec(
        "fairness_weight", "وزن عدالة التوزيع", "float", 1.0, "", 0.0, 100.0,
        group_ar="التحسين",
        description_ar="هدف ثانوي؛ لا يُسمح له بخرق SLA أو أي قيد صلب.",
    ),
    SettingSpec(
        "cost_per_km", "تكلفة الكيلومتر", "float", 1.10, "ريال", 0.0, 100.0,
        group_ar="التحسين",
        description_ar="تُستخدم في مستوى التكلفة من متجه الأهداف وفي تقارير المقارنة.",
    ),
    SettingSpec(
        "cost_per_driver_day", "تكلفة يوم السائق", "float", 420.0, "ريال", 0.0, 10000.0,
        group_ar="التحسين",
        description_ar="كلفة فتح رحلة جديدة — هي ما يجعل المحرك يفضّل تقليل السائقين.",
    ),
    SettingSpec(
        "cost_per_hour", "تكلفة ساعة التشغيل", "float", 0.0, "ريال", 0.0, 5000.0,
        group_ar="التحسين",
        description_ar="صفر افتراضيًا: أجر السائق يوميّ لا ساعيّ في النموذج الحالي.",
    ),
    SettingSpec(
        "max_wait_minutes_per_stop", "أقصى انتظار مقبول عند محطة", "int", 45, "دقيقة",
        0, 480, group_ar="التحسين",
        description_ar="تجاوزه يولّد تحذيرًا LONG_WAIT ولا يمنع الخطة.",
    ),

    # -------------------------------------------------------- الحرارة ----
    SettingSpec(
        "temperature_breach_grace_minutes", "سماحية مخالفة الحرارة قبل التنبيه", "int",
        5, "دقيقة", 0, 60, group_ar="الحرارة",
        description_ar="مدة استمرار القراءة خارج النطاق قبل تسجيلها مخالفة (تتجاهل الارتفاعات اللحظية عند فتح الصندوق).",
    ),
)

SETTING_INDEX: dict[str, SettingSpec] = {s.key: s for s in SETTING_SPECS}

DEFAULTS: dict[str, Any] = {s.key: s.default for s in SETTING_SPECS}


def coerce(key: str, raw: Any) -> Any:
    """يحول قيمة خام إلى نوعها ويتحقق من مداها."""
    spec = SETTING_INDEX.get(key)
    if spec is None:
        raise ValidationError(f"مفتاح إعداد غير معروف: {key}")
    try:
        if spec.kind == "int":
            value: Any = int(raw)
        elif spec.kind == "float":
            value = float(raw)
        elif spec.kind == "bool":
            if isinstance(raw, bool):
                value = raw
            else:
                value = str(raw).strip().lower() in ("1", "true", "yes", "on", "نعم")
        elif spec.kind == "list[str]":
            if isinstance(raw, str):
                value = [item.strip() for item in raw.split(",") if item.strip()]
            else:
                value = [str(item) for item in raw]
        else:
            value = str(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"قيمة غير صالحة للإعداد {key}: {raw!r}", expected=spec.kind
        ) from exc

    if spec.kind in ("int", "float"):
        if spec.minimum is not None and value < spec.minimum:
            raise ValidationError(
                f"قيمة {spec.name_ar} أقل من الحد الأدنى {spec.minimum}", key=key
            )
        if spec.maximum is not None and value > spec.maximum:
            raise ValidationError(
                f"قيمة {spec.name_ar} أعلى من الحد الأقصى {spec.maximum}", key=key
            )
    if spec.choices and value not in spec.choices:
        raise ValidationError(
            f"قيمة {spec.name_ar} يجب أن تكون إحدى: {', '.join(spec.choices)}", key=key
        )
    return value


@dataclass(slots=True)
class SettingOverride:
    key: str
    value: Any
    scope_type: str
    scope_id: str | None


class SettingsResolver:
    """يحلّ القيم الفعالة لنطاق تشغيلي محدد.

    الاستخدام::

        resolver = SettingsResolver(overrides)
        effective = resolver.effective(region_id=..., city_id=..., hub_id=...)
        effective["max_shift_hours"]
    """

    def __init__(self, overrides: list[SettingOverride] | None = None) -> None:
        self.overrides = overrides or []

    def effective(
        self,
        *,
        region_id: str | None = None,
        city_id: str | None = None,
        hub_id: str | None = None,
    ) -> dict[str, Any]:
        applicable_ids = {
            "KINGDOM": {None},
            "REGION": {region_id} if region_id else set(),
            "CITY": {city_id} if city_id else set(),
            "HUB": {hub_id} if hub_id else set(),
        }
        best: dict[str, tuple[int, Any]] = {
            key: (-1, value) for key, value in DEFAULTS.items()
        }
        for override in self.overrides:
            ids = applicable_ids.get(override.scope_type, set())
            if override.scope_type != "KINGDOM" and override.scope_id not in ids:
                continue
            if override.scope_type == "KINGDOM" and override.scope_id is not None:
                continue
            rank = SCOPE_PRECEDENCE[override.scope_type]
            current = best.get(override.key)
            if current is None or rank >= current[0]:
                best[override.key] = (rank, override.value)
        return {key: value for key, (_, value) in best.items()}

    def explain(self, key: str, **scope: Any) -> dict[str, Any]:
        """يوضح مصدر القيمة الفعالة — لتفسير قرارات المحرك (§32)."""
        spec = SETTING_INDEX[key]
        source = "افتراضي (المملكة)"
        value = spec.default
        rank = -1
        mapping = {
            "REGION": scope.get("region_id"),
            "CITY": scope.get("city_id"),
            "HUB": scope.get("hub_id"),
        }
        for override in self.overrides:
            if override.key != key:
                continue
            if override.scope_type == "KINGDOM" and override.scope_id is None:
                pass
            elif mapping.get(override.scope_type) != override.scope_id:
                continue
            r = SCOPE_PRECEDENCE[override.scope_type]
            if r >= rank:
                rank, value, source = r, override.value, (
                    f"{override.scope_type}:{override.scope_id or '-'}"
                )
        return {
            "key": key,
            "name_ar": spec.name_ar,
            "value": value,
            "unit_ar": spec.unit_ar,
            "source": source,
            "default": spec.default,
        }


#: قيم موصى بها لنطاقات معروفة، تُزرع كتجاوزات وليست مكتوبة في الكود (§13)
SEED_SCOPE_OVERRIDES: dict[str, dict[str, Any]] = {
    "عرعر": {"min_event_gap_minutes": 20, "use_time_dependent_travel": False},
    "الرياض": {
        "min_event_gap_minutes": 0,
        "use_time_dependent_travel": True,
        "peak_periods": ["06:30-09:00", "15:30-18:30"],
    },
    "_governorate_default": {"min_event_gap_minutes": 10},
}
