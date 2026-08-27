"""بنية الاختبارات المشتركة: سجل السيناريوهات، بناء مسائل تركيبية، عميل HTTP.

**سجل السيناريوهات** هو ما يجعل التغطية قابلة للإثبات لا للادعاء: كل اختبار
يُوسَم برقم السيناريو الإلزامي من §30، والمشغّل يبني في النهاية مصفوفة تُظهر
أي سيناريو غُطّي، وبأي اختبار، وهل نجح أم فشل. سيناريو بلا اختبار يظهر
صراحةً كـ «غير مُغطى» بدل أن يمر بصمت.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import time
import unittest
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "packages") not in sys.path:
    sys.path.insert(0, str(ROOT / "packages"))

TZ = dt.timezone(dt.timedelta(hours=3))

#: قائمة السيناريوهات الإلزامية كما وردت في §30 — المرجع الوحيد للتغطية
MANDATORY_SCENARIOS: dict[int, str] = {
    1: "شحنة واحدة وسائق واحد",
    2: "عدة شحنات وسائق واحد",
    3: "عدة شحنات وعدة سائقين",
    4: "عدة مراكز انطلاق",
    5: "نافذة التقاط مستحيلة",
    6: "SLA تسليم مستحيل",
    7: "التقاط قبل التسليم",
    8: "منع التسليم قبل الالتقاط",
    9: "عمليتا التقاط من المركز نفسه مع تسليم الأولى قبل الثانية",
    10: "عدة التقاطات قبل التسليم",
    11: "منع خلط مستشفى ومركز صحي",
    12: "تطبيق استثناء بنك الدم",
    13: "رحلة بعيدة",
    14: "منع رحلتين بعيدتين للسائق نفسه",
    15: "منع تجاوز وردية ١٠ ساعات",
    16: "انتهاء الرحلة عند آخر تسليم",
    17: "عدم إضافة عودة إجبارية إلى مركز الانطلاق",
    18: "رحلة ثانية تبدأ من موقع آخر تسليم",
    19: "طلب فوري قبل بدء السائق",
    20: "طلب فوري بعد بدء السائق",
    21: "إلغاء طلب فوري قبل الالتقاط",
    22: "إعادة الطلب إلى الانتظار بعد إزالة السائق",
    23: "تأخر الالتقاط",
    24: "تأخر التسليم",
    25: "عدم جاهزية العينات",
    26: "تعذر التسليم",
    27: "مخالفة درجة الحرارة",
    28: "توقف تحديث الموقع",
    29: "انقطاع الإنترنت عن السائق ثم المزامنة",
    30: "منع السائق من رؤية رحلات غيره",
    31: "منع المشرف من رؤية مركز آخر",
    32: "منع المشرف من رفع الجدول الوطني",
    33: "حفظ مسودة المسارات بعد تحديث الصفحة",
    34: "تطابق عدد الرحلات في جميع الشاشات",
    35: "فتح بطاقة التحذيرات ومعرفة السبب",
    36: "نشر خطة يوم واحد فقط",
    37: "تعديل رحلة منشورة وإرسال التحديث",
    38: "تقدير عدد السائقين",
    39: "اكتشاف الزيادة غير المبررة في السائقين",
    40: "تشغيل الخطة على مدينة الرياض بأزمنة ازدحام متغيرة",
    41: "ملف يحتوي على بيانات مكررة",
    42: "ملف بإحداثيات غير صحيحة",
    43: "الضغط بعدد كبير من الشحنات",
    44: "فشل خدمة الخرائط",
    45: "فشل محرك التحسين",
    46: "النسخ الاحتياطي والاستعادة",
}


# =========================================================== سجل التغطية ====

#: (اسم الاختبار الكامل) → أرقام السيناريوهات التي يغطيها
TEST_SCENARIOS: dict[str, tuple[int, ...]] = {}


def scenario(*numbers: int) -> Callable[[Callable], Callable]:
    """يوسم دالة اختبار بأرقام السيناريوهات الإلزامية التي تثبتها."""
    for number in numbers:
        if number not in MANDATORY_SCENARIOS:
            raise KeyError(f"رقم سيناريو غير موجود في §30: {number}")

    def decorator(func: Callable) -> Callable:
        existing = getattr(func, "masar_scenarios", ())
        func.masar_scenarios = tuple(sorted(set(existing) | set(numbers)))  # type: ignore[attr-defined]
        return func

    return decorator


def register_case(case: unittest.TestCase) -> None:
    """يسجّل تغطية اختبار يعمل الآن (يستدعيه ``MasarTestCase.setUp``)."""
    method = getattr(case, case._testMethodName, None)
    numbers = tuple(getattr(method, "masar_scenarios", ()))
    if numbers:
        TEST_SCENARIOS[case.id()] = numbers


class MasarTestCase(unittest.TestCase):
    """قاعدة كل اختبار — تسجّل التغطية تلقائيًا."""

    def setUp(self) -> None:  # noqa: N802
        register_case(self)
        super().setUp()

    # مساعدات تأكيد بلغة المجال ------------------------------------------
    def assertViolation(self, evaluation, rule: str, msg: str = "") -> None:  # noqa: N802
        rules = [v.rule for v in evaluation.violations]
        self.assertFalse(evaluation.feasible, msg or f"توقعنا رفض الرحلة بالقاعدة {rule}")
        self.assertIn(rule, rules, f"القواعد المخروقة: {rules} — توقعنا {rule}")

    def assertReasoned(self, item: dict, *keys: str) -> None:  # noqa: N802
        """لا تحذير ولا رفض بلا سبب مكتوب (§2)."""
        for key in keys:
            value = item.get(key)
            self.assertTrue(
                isinstance(value, str) and value.strip(),
                f"الحقل «{key}» فارغ — كل تحذير/رفض يجب أن يحمل سببًا مكتوبًا",
            )


# ====================================================== بناء مسألة تركيبية ==

from masar_core.operational_settings import SettingsResolver  # noqa: E402
from masar_opt.engine import (  # noqa: E402
    HubInput,
    ShipmentInput,
    VehicleInput,
    build_problem,
)

#: يوم مرجعي ثابت — الاختبارات الهندسية لا تعتمد على «الآن» كي تكون قابلة للإعادة
BASE_DAY = dt.date(2026, 9, 6)


def at(hour: int, minute: int = 0, *, day: dt.date | None = None) -> dt.datetime:
    day = day or BASE_DAY
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


#: مواقع حقيقية تقريبية — الرياض ومراكز بعيدة (لاختبار الرحلات البعيدة)
PLACES: dict[str, tuple[str, str, float, float]] = {
    # رمز: (الاسم، النوع، خط العرض، خط الطول)
    "HUB_RYD": ("مركز انطلاق الرياض", "HUB", 24.7250, 46.6900),
    "HUB_ARR": ("مركز انطلاق عرعر", "HUB", 30.9800, 41.0400),
    "PHC1": ("مركز صحي النسيم", "HEALTH_CENTER", 24.7743, 46.8172),
    "PHC2": ("مركز صحي العزيزية", "HEALTH_CENTER", 24.5701, 46.7386),
    "PHC3": ("مركز صحي الشفا", "HEALTH_CENTER", 24.5620, 46.6800),
    "PHC4": ("مركز صحي الملز", "HEALTH_CENTER", 24.6690, 46.7300),
    "PHC5": ("مركز صحي السويدي", "HEALTH_CENTER", 24.6060, 46.6320),
    "HOSP1": ("مستشفى الملك سعود", "HOSPITAL", 24.6840, 46.7220),
    "HOSP2": ("مستشفى الشميسي", "HOSPITAL", 24.6350, 46.7050),
    "BLOOD1": ("بنك الدم المركزي", "BLOOD_BANK", 24.6900, 46.7100),
    "LAB": ("المختبر الإقليمي بالرياض", "LABORATORY", 24.6877, 46.7219),
    "LAB_ARR": ("مختبر عرعر الإقليمي", "LABORATORY", 30.9770, 41.0300),
    "PHC_RFH": ("مركز صحي رفحاء", "HEALTH_CENTER", 29.6202, 43.4980),
    "PHC_HDT": ("مركز صحي الحديثة", "HEALTH_CENTER", 31.4667, 40.0000),
    "PHC_TRF": ("مركز صحي طريف", "HEALTH_CENTER", 31.6775, 38.6531),
}


def hub(code: str = "HUB_RYD", *, hub_id: str | None = None,
        opens: int = 6, closes: int = 22) -> HubInput:
    name, _kind, lat, lon = PLACES[code]
    return HubInput(
        hub_id=hub_id or code.lower(), code=code, name_ar=name,
        lat=lat, lon=lon, opens_at=at(opens), closes_at=at(closes),
    )


def shipment(
    index: int,
    *,
    pickup: str,
    dropoff: str = "LAB",
    hub_id: str = "hub_ryd",
    window: tuple[dt.datetime, dt.datetime] | None = None,
    sla: dt.datetime | None = None,
    pickup_service: float = 10.0,
    dropoff_service: float = 10.0,
    service_type: str = "ROUTINE",
    temperature_mode: str = "AMBIENT",
    piece_count: int = 1,
    facility_suffix: str = "",
) -> ShipmentInput:
    """يبني شحنة تركيبية من فهرس مكان معروف."""
    p_name, p_type, p_lat, p_lon = PLACES[pickup]
    d_name, d_type, d_lat, d_lon = PLACES[dropoff]
    window = window or (at(8, 0), at(8, 30))
    sla = sla or (window[1] + dt.timedelta(hours=4))
    return ShipmentInput(
        shipment_id=f"shp-{index}",
        reference=f"TST-{index:04d}",
        hub_id=hub_id,
        pickup_facility_id=f"fac-{pickup}{facility_suffix}",
        pickup_facility_type=p_type,
        pickup_name=p_name,
        pickup_lat=p_lat, pickup_lon=p_lon,
        pickup_window_from=window[0], pickup_window_to=window[1],
        pickup_service_minutes=pickup_service,
        dropoff_facility_id=f"fac-{dropoff}",
        dropoff_facility_type=d_type,
        dropoff_name=d_name,
        dropoff_lat=d_lat, dropoff_lon=d_lon,
        dropoff_service_minutes=dropoff_service,
        sla_deadline=sla,
        service_type=service_type,
        temperature_mode=temperature_mode,
        piece_count=piece_count,
    )


def vehicle(
    index: int = 1,
    *,
    hub_id: str = "hub_ryd",
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    shift_minutes: float = 600.0,
    start_at_place: str | None = None,
) -> VehicleInput:
    lat = lon = None
    if start_at_place:
        _n, _k, lat, lon = PLACES[start_at_place]
    return VehicleInput(
        hub_id=hub_id, label=f"خانة سائق {index}",
        earliest_start=start or at(6), latest_end=end or at(22),
        max_shift_minutes=shift_minutes,
        start_lat=lat, start_lon=lon,
    )


def settings(**overrides: Any) -> dict[str, Any]:
    """القيم التشغيلية الفعالة الافتراضية مع تجاوزات صريحة للاختبار."""
    effective = SettingsResolver().effective()
    effective.update(overrides)
    return effective


def problem(
    shipments: Sequence[ShipmentInput],
    vehicles: Sequence[VehicleInput],
    *,
    hubs: Sequence[HubInput] | None = None,
    effective: dict[str, Any] | None = None,
    provider: str = "haversine",
    day: dt.date | None = None,
    fallback_to_estimate: bool = False,
):
    return build_problem(
        service_date=day or BASE_DAY,
        hubs=list(hubs or [hub()]),
        shipments=list(shipments),
        vehicles=list(vehicles),
        effective_settings=effective or settings(),
        routing_provider_name=provider,
        fallback_to_estimate=fallback_to_estimate,
    )


def sequence_of(prob, *codes_kinds: tuple[str, str]) -> list[int]:
    """يترجم ``[("PHC1","PICKUP"), ("LAB","DELIVERY")]`` إلى فهارس عقد."""
    from masar_opt.model import NodeKind

    wanted = list(codes_kinds)
    out: list[int] = []
    used: set[int] = set()
    for code, kind in wanted:
        name = PLACES[code][0]
        for node in prob.nodes:
            if node.index in used:
                continue
            if node.kind is NodeKind(kind) and name in node.label:
                out.append(node.index)
                used.add(node.index)
                break
        else:
            raise KeyError(f"لا توجد عقدة {kind} للمكان {code}")
    return out


def pair_nodes(prob, shipment_index: int) -> tuple[int, int]:
    spec = prob.shipments[shipment_index]
    return spec.pickup_node, spec.delivery_node


# ============================================================ عميل HTTP =====

BASE_URL = os.environ.get("MASAR_TEST_BASE_URL", "http://127.0.0.1:8080")
#: كلمة مرور حسابات الاختبار — من البيئة حصرًا، بلا قيمة احتياطية في الكود.
#: ``run_tests.sh`` يزرع القاعدة ويصدّرها؛ التشغيل المباشر بلا ضبطها يفشل
#: برسالة واضحة بدل أن يفشل الدخول لسبب غامض.
DEMO_PASSWORD = os.environ.get("MASAR_TEST_PASSWORD") or ""


def require_test_password() -> str:
    """يفشل برسالة مفهومة إن لم تُضبط كلمة مرور الاختبار."""
    if not DEMO_PASSWORD:
        raise RuntimeError(
            "MASAR_TEST_PASSWORD غير مضبوطة. شغّل ./scripts/run_tests.sh — "
            "فهو يولّد كلمة مرور، يزرع بها القاعدة، ويصدّرها للاختبارات. "
            "لا توجد كلمة مرور افتراضية في الكود عمدًا.")
    return DEMO_PASSWORD


class ServerUnavailable(RuntimeError):
    """الخادم غير مشغّل — الاختبارات التكاملية لا تُخفي هذا، بل تفشل بوضوح."""


def server_is_up() -> bool:
    import httpx

    try:
        response = httpx.get(f"{BASE_URL}/api/health", timeout=5.0)
        return response.status_code == 200
    except Exception:
        return False


class ApiClient:
    """عميل مُصادَق يمر بنفس نقاط API التي تستخدمها الواجهة."""

    def __init__(self, email: str, password: str | None = None) -> None:
        import httpx

        password = password or require_test_password()
        self.email = email
        self.session = httpx.Client(base_url=BASE_URL, timeout=180.0)
        # محدِّد معدّل تسجيل الدخول ميزة أمنية مقصودة (§29)؛ الحزمة تسجّل دخول
        # ثمانية أدوار متتابعة فتلامسه أحيانًا. ننتظر المدة التي يطلبها الخادم
        # نفسه بدل تعطيله في بيئة الاختبار — فتبقى الميزة مُختبَرة كما هي.
        for attempt in range(4):
            response = self.session.post(
                "/api/auth/login", json={"email": email, "password": password})
            if response.status_code != 429:
                break
            try:
                wait = float(response.json()["error"]["details"]["retry_after"])
            except Exception:
                wait = 5.0
            time.sleep(min(wait, 30.0) + 1.0)
        if response.status_code != 200:
            raise ServerUnavailable(
                f"تعذّر تسجيل الدخول بـ {email}: {response.status_code} {response.text[:200]}")
        payload = response.json()
        self.user = payload["data"]["user"]
        self.session.headers["authorization"] = f"Bearer {payload['data']['access_token']}"

    # طبقة رقيقة فوق httpx ------------------------------------------------
    def get(self, url: str, **kwargs):
        return self.session.get(url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.session.post(url, **kwargs)

    def patch(self, url: str, **kwargs):
        return self.session.patch(url, **kwargs)

    def data(self, response) -> Any:
        if response.status_code >= 400:
            raise AssertionError(
                f"{response.request.method} {response.request.url.path} "
                f"→ {response.status_code}: {response.text[:500]}")
        return response.json().get("data")

    def code_of(self, response) -> str:
        try:
            return str(response.json().get("error", {}).get("code", ""))
        except Exception:
            return ""


def db_connection(*, as_app: bool = False):
    """اتصال مباشر بصلاحية الترحيل — لتهيئة حالات لا يمكن بلوغها عبر API.

    يُستخدم فقط لـ**تهيئة** الحالة (مثل تقديم عقارب الساعة على محطة، أو زرع
    جهة بإحداثيات خاطئة)، ولا يُستخدم أبدًا للتحقق بدل API: التحقق يمر دائمًا
    بنفس الطبقات التي يمر بها المستخدم الحقيقي.
    """
    import pgwire
    from masar_core.config import get_config

    cfg = get_config().database
    user, password = (
        (cfg.user, cfg.password) if as_app else (cfg.migrate_user, cfg.migrate_password)
    )
    return pgwire.connect(
        host=cfg.host, port=cfg.port, user=user, password=password,
        database=cfg.name, sslmode=cfg.sslmode,
        statement_timeout_ms=0, application_name="masar-tests",
    )


def load_suite(modules: Iterable[str]) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in modules:
        suite.addTests(loader.loadTestsFromName(name))
    return suite
