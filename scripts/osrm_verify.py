"""تحقق ما بعد التركيب: هل خدمة OSRM عندك تعمل — وعلى الخريطة الصحيحة؟

يُشغَّل **على خادمك بعد** ``docker compose up -d osrm``. لا يفترض شيئًا، بل
يقيس ويقارن بأرقام مرجعية قيست على خريطة السعودية:

    MASAR_OSRM_URL=http://127.0.0.1:5000 \\
    PYTHONPATH=packages python3 scripts/osrm_verify.py

لماذا لا يكفي ``curl`` وحده: خادم OSRM محمّل على خريطة قارة أخرى يرد
``{"code":"Ok"}`` بأرقام سليمة الشكل. لا شيء في الرد يكشف أنها لمنطقة
خاطئة — إلا مقارنتها بمسافة تعرفها سلفًا. لذلك كل فحص هنا له **رقم متوقَّع**.

يعيد ٠ إن كانت الخدمة صالحة للاعتماد، و١ عند أي مانع.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from masar_core.config import get_config
from masar_core.errors import DependencyUnavailable
from masar_opt.model import Node, NodeKind
from masar_opt.routing import OSRMProvider

PASS, FAIL, WARN = "✅", "❌", "⚠️ "

#: سيقان مرجعية مقيسة على OSRM/OpenStreetMap في ٢٦ أغسطس ٢٠٢٦.
#: التسامح ±١٠٪ يستوعب تحديث الخريطة بين الإصدارات، ويكشف الخريطة الخاطئة.
LEGS = [
    ("مركز انطلاق عرعر", (30.9800, 41.0400), "مركز صحي رفحاء", (29.6202, 43.4980), 290.4, 187.2),
    ("مركز صحي رفحاء", (29.6202, 43.4980), "مركز صحي العويقيلة", (30.3333, 42.2500), 157.3, 112.1),
    ("مركز صحي العويقيلة", (30.3333, 42.2500), "مختبر عرعر الإقليمي", (30.9770, 41.0300), 143.2, 100.8),
]
TOLERANCE = 0.10

blockers: list[str] = []
notes: list[str] = []


def node(index: int, lat: float, lon: float, label: str) -> Node:
    return Node(index=index, kind=NodeKind.PICKUP, lat=lat, lon=lon, label=label)


def main() -> int:
    cfg = get_config().routing
    url = cfg.osrm_base_url
    print("=" * 74)
    print(f"تحقق خدمة OSRM — {url or '(غير محدد)'}")
    print("=" * 74)

    if not url:
        print(f"{FAIL} MASAR_OSRM_URL غير محدد. مثال:")
        print("      MASAR_OSRM_URL=http://127.0.0.1:5000 "
              "PYTHONPATH=packages python3 scripts/osrm_verify.py")
        return 1

    provider = OSRMProvider(base_url=url, timeout=cfg.request_timeout_seconds)

    # ------------------------------------------------ ١) هل تستجيب أصلًا --
    try:
        started = time.monotonic()
        provider.probe()
        latency = round((time.monotonic() - started) * 1000)
        print(f"{PASS} الخدمة تستجيب — {latency} مل.ث")
    except DependencyUnavailable as exc:
        print(f"{FAIL} الخدمة لا تستجيب: {exc}")
        print("\n      افحص: docker compose ps · docker compose logs osrm")
        return 1

    # -------------------------------------- ٢) هل هي على خريطة السعودية؟ --
    print("\nالسيقان المرجعية (مقيسة على خريطة السعودية):")
    print(f"{'الساق':<40} {'المقيس':>19} {'المتوقع':>19} {'الفارق':>8}")
    all_nodes = []
    for i, (a_label, a, b_label, b, _km, _min) in enumerate(LEGS):
        all_nodes.append(node(len(all_nodes), a[0], a[1], a_label))
        all_nodes.append(node(len(all_nodes), b[0], b[1], b_label))

    try:
        minutes, km = provider.matrix(all_nodes)
    except DependencyUnavailable as exc:
        print(f"{FAIL} فشل بناء مصفوفة السيقان: {exc}")
        return 1

    for i, (a_label, _a, b_label, _b, expected_km, expected_min) in enumerate(LEGS):
        actual = km[i * 2][i * 2 + 1]
        drift = abs(actual - expected_km) / expected_km
        mark = PASS if drift <= TOLERANCE else FAIL
        leg = f"{a_label} ← {b_label}"
        actual_min = minutes[i * 2][i * 2 + 1]
        print(f"{mark} {leg:<38} {actual:>7.1f} كم {actual_min:>6.0f} د "
              f"{expected_km:>7.1f} كم {expected_min:>6.0f} د "
              f"{drift * 100:>+6.1f}٪")
        if drift > TOLERANCE:
            blockers.append(
                f"الساق «{leg}» تبعد {drift * 100:.0f}٪ عن المقيس — "
                "الأرجح أن الخريطة المحمَّلة ليست خريطة السعودية أو أن الملف تالف")
        elif drift > 0.03:
            notes.append(
                f"الساق «{leg}» تختلف {drift * 100:.1f}٪ — مقبول، وغالبًا "
                "بسبب تحديث خريطة OSM منذ القياس")

    # -------------------------------------------- ٣) عدم التماثل الواقعي --
    forward, backward = km[0][1], km[1][0]
    if abs(forward - backward) < 0.01 and forward > 50:
        notes.append(
            "المصفوفة متماثلة تمامًا على مسافة طويلة — غير معتاد على شبكة طرق "
            "حقيقية؛ تأكد أن الخدمة ليست في وضع خط مستقيم")
    else:
        print(f"{PASS} المصفوفة غير متماثلة كما يليق بشبكة طرق حقيقية "
              f"({forward:.1f} ≠ {backward:.1f} كم)")

    # ------------------------------ ٤) حجم الخطة الوطنية وحدود الرابط -----
    size = 240
    grid = [node(i, 24.0 + (i % 40) * 0.11, 42.0 + (i // 40) * 0.13, f"عقدة {i}")
            for i in range(size)]
    try:
        started = time.monotonic()
        provider.matrix(grid)
        seconds = time.monotonic() - started
        print(f"{PASS} مصفوفة {size}×{size} بُنيت في {seconds:.1f} ث عبر "
              f"{provider.last_request_count} طلبًا "
              f"(حجم الكتلة {provider.block_size})")
    except DependencyUnavailable as exc:
        message = str(exc)
        print(f"{FAIL} فشلت مصفوفة بحجم خطة وطنية: {message[:140]}")
        if "414" in message or "TooBig" in message:
            blockers.append(
                "الطلب تجاوز حدود خادمك. اخفض MASAR_OSRM_BLOCK_SIZE (جرّب ٥٠)، "
                "أو ارفع max-table-size في osrm-routed و"
                "large_client_header_buffers في nginx")
        else:
            blockers.append(f"فشل بناء مصفوفة كبيرة: {message[:160]}")

    # ------------------------------------------- ٥) الحكم عبر طبقة النظام -
    try:
        from masar_api.services.routing_status import provider_status

        get_config(reload=True)
        status = provider_status()
        if status.get("ok_for_production"):
            print(f"{PASS} حالة النظام: {status['message_ar']}")
        else:
            blockers.append(f"حالة النظام: {status['message_ar']}")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"تعذر فحص حالة النظام: {exc}")

    # ------------------------------------------------------------ الخلاصة -
    print("\n" + "=" * 74)
    for item in notes:
        print(f"{WARN} {item}")
    if blockers:
        print(f"\n{FAIL} موانع الاعتماد ({len(blockers)}):")
        for item in blockers:
            print(f"    · {item}")
        print("\nلا تفعّل MASAR_ROUTING_PROVIDER=osrm قبل معالجتها.")
        return 1

    print(f"{PASS} الخدمة صالحة للاعتماد. الخطوة التالية:")
    print("      1) في /etc/masar/masar.env :  MASAR_ROUTING_PROVIDER=osrm")
    print(f"                                    MASAR_OSRM_URL={url}")
    print("      2) systemctl restart masar-api")
    print("      3) PYTHONPATH=packages python3 scripts/preflight.py")
    print("      4) أعد تشغيل محرك التخطيط — الخطط الجديدة ستفقد وسم «تقديرية»")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
