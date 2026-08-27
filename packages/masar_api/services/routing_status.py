"""حالة مزوّد الطرق — «مضبوط» و«يعمل» شيئان مختلفان.

قراءة الإعداد تقول أي مزوّد اختير. هذه الوحدة تذهب أبعد: تستدعي الخدمة
فعليًا على **ساق مرجعية مقيسة**، وتقارن الناتج بالرقم المعروف. الغرض ليس
التأكد من أن الخدمة ترد — بل أن ترد **من خريطة السعودية**. خادم OSRM محمّل
على خريطة منطقة أخرى يرد ``Ok`` بأرقام سليمة الشكل وعديمة القيمة، ولا شيء
في الرد نفسه يكشف ذلك.
"""

from __future__ import annotations

from typing import Any

from masar_core.config import get_config
from masar_core.errors import DependencyUnavailable
from masar_opt.routing import REFERENCE_LEG, build_provider


def provider_status() -> dict[str, Any]:
    """يعيد حالة المزوّد المُعدّ، بعد استدعائه فعليًا إن كان حقيقيًا."""
    cfg = get_config().routing
    name = cfg.provider

    if name == "haversine":
        return {
            "provider": name,
            "configured": True,
            "reachable": None,          # لا خدمة تُستدعى أصلًا
            "is_estimated": True,
            "map_verified": False,
            "ok_for_production": False,
            "message_ar": (
                "مزوّد تقديري (خط مستقيم × معامل التفافية) — ليست أزمنة طريق "
                "حقيقية. الانحراف المقيس عن شبكة الطرق: +28٪ مسافةً و+64٪ زمنًا. "
                "اعتماد الخطط مرفوض في بيئة الإنتاج."
            ),
        }

    try:
        provider = build_provider(name)
    except DependencyUnavailable as exc:
        return {
            "provider": name, "configured": False, "reachable": False,
            "is_estimated": None, "map_verified": False, "ok_for_production": False,
            "message_ar": exc.message,
        }

    base: dict[str, Any] = {
        "provider": provider.name,
        "configured": True,
        "is_estimated": provider.is_estimated,
    }

    probe = getattr(provider, "probe", None)
    if probe is None:
        # مزوّد بلا فحص حيّ (مصفوفة ملف مثلًا): لا ندّعي تحققًا لم يجرِ.
        base.update({
            "reachable": None, "map_verified": False,
            "ok_for_production": not provider.is_estimated,
            "message_ar": "مزوّد بلا فحص حيّ — تحقق من مصدر بياناته يدويًا",
        })
        return base

    try:
        result = probe()
    except DependencyUnavailable as exc:
        base.update({
            "reachable": False, "map_verified": False, "ok_for_production": False,
            "message_ar": f"المزوّد مُعدّ لكنه لا يستجيب: {exc.message}",
        })
        return base

    expected_km = float(REFERENCE_LEG["expected_km"])
    tolerance = float(REFERENCE_LEG["tolerance"])
    actual_km = float(result["reference_km"])
    drift = abs(actual_km - expected_km) / expected_km if expected_km else 1.0
    verified = drift <= tolerance

    base.update({
        "reachable": True,
        "latency_ms": result.get("latency_ms"),
        "map_verified": verified,
        "ok_for_production": verified and not provider.is_estimated,
        "reference": {
            "leg_ar": f"{REFERENCE_LEG['from_label']} ← {REFERENCE_LEG['to_label']}",
            "expected_km": expected_km,
            "actual_km": actual_km,
            "drift_percent": round(drift * 100, 1),
            "tolerance_percent": round(tolerance * 100),
            "measured_at": REFERENCE_LEG["measured_at"],
            "source": REFERENCE_LEG["source"],
        },
        "message_ar": (
            f"خدمة طرق حقيقية تعمل — الساق المرجعية {actual_km:.1f} كم مقابل "
            f"{expected_km:.1f} كم متوقعة (فارق {drift * 100:.1f}٪)"
            if verified else
            f"الخدمة تستجيب لكن نتيجتها بعيدة عن المرجع: {actual_km:.1f} كم "
            f"مقابل {expected_km:.1f} كم (فارق {drift * 100:.1f}٪). الأرجح أن "
            "الخريطة المحمَّلة ليست خريطة السعودية أو أن الملف تالف — "
            "لا تعتمد خططًا مبنية عليها."
        ),
    })
    return base
