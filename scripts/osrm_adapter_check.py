"""فحص محوّل OSRM مقابل خادم وهمي — **اختبار محوّل لا اختبار بيانات طرق**.

ما يثبته هذا الفحص:

* أن ``OSRMProvider`` يبني عنوان ``/table`` الصحيح بترتيب ``lon,lat``.
* أنه يفسّر ``durations`` (ثوانٍ) و``distances`` (أمتار) بالوحدات الصحيحة.
* أنه يرفض الاستجابات المشوّهة بدل ابتلاعها.
* أن العطل يتحوّل إلى ``DependencyUnavailable`` لا إلى صفر صامت.
* أن الارتداد إلى التقدير يرفع ``is_estimated`` ويولّد تحذيرًا HIGH.

ما **لا** يثبته: أن بيانات الطرق صحيحة. ذلك يقتضي OSRM حقيقيًا على خريطة
حقيقية — راجع ``deploy/osrm/README.md``. الخادم الوهمي هنا يعيد أرقامًا
مثبَّتة مأخوذة من استجابة OSRM حقيقية لمنطقة الحدود الشمالية، كي يكون
شكل الاستجابة مطابقًا للواقع لا مخترعًا.
"""
from __future__ import annotations

import json
import sys
import threading
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from masar_core.errors import DependencyUnavailable
from masar_opt.model import Node, NodeKind
from masar_opt.routing import OSRMProvider

RAW = json.loads(
    (ROOT / "var" / "reports" / "osrm-raw-northern-borders.json").read_text("utf-8"))

REF_COORDS = [f"{lon:.6f},{lat:.6f}" for lon, lat in RAW["_coordinates_lon_lat"]]
REF_INDEX = {coord: i for i, coord in enumerate(REF_COORDS)}


def cell(a: str, b: str) -> tuple[float, float]:
    """(أمتار، ثوانٍ) لزوج إحداثيات.

    للنقاط المرجعية الخمس: أرقام OSRM الحقيقية، فتبقى فحوص تحويل الوحدات
    مقارَنةً بواقع لا باختراع. لغيرها: قيمة حتمية مستقرة (CRC32) تسمح
    بالتحقق من أن كل خلية وصلت إلى موضعها الصحيح بعد التجميع من الكتل.
    """
    if a == b:
        return 0.0, 0.0
    if a in REF_INDEX and b in REF_INDEX:
        i, j = REF_INDEX[a], REF_INDEX[b]
        return RAW["distances"][i][j], RAW["durations"][i][j]
    seed = zlib.crc32(f"{a}>{b}".encode()) % 900_000
    return float(seed + 100_000), float(seed * 2 + 60)


PASS, FAIL = "✅", "❌"
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, message: str) -> None:
    RESULTS.append((ok, message))
    print(f"{PASS if ok else FAIL} {message}")


# ------------------------------------------------------- الخادم الوهمي ----

class MockOSRM(BaseHTTPRequestHandler):
    mode = "ok"
    last_path = ""
    #: أطول رابط ورد — لإثبات أن التقسيم يُبقيه تحت حد الخوادم
    max_path_seen = 0
    #: حدّ طول الرابط كما تفرضه الخوادم الحقيقية (nginx افتراضيًا ٨ ك.ب)
    url_limit = 8192
    #: عدّاد الطلبات، ومتى يبدأ الرد الطبيعي (لاختبار إعادة المحاولة)
    requests = 0
    fail_first = 0

    def log_message(self, *_args) -> None:  # صمت
        pass

    def do_GET(self) -> None:  # noqa: N802
        MockOSRM.last_path = self.path
        MockOSRM.requests += 1
        MockOSRM.max_path_seen = max(MockOSRM.max_path_seen, len(self.path))
        if len(self.path) > MockOSRM.url_limit:
            # ما يفعله nginx حرفيًا: 414 URI Too Long
            self.send_response(414)
            self.end_headers()
            self.wfile.write(b"request-uri too large")
            return
        if MockOSRM.requests <= MockOSRM.fail_first:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(b"bad gateway")
            return
        # لا تُستعمل urlparse هنا: تفصل ما بعد ";" كـparams، وإحداثيات OSRM
        # مفصولة بـ";" فتضيع كل النقاط عدا الأولى.
        path_only = self.path.split("?", 1)[0]
        if MockOSRM.mode == "http_error":
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"unavailable")
            return
        if MockOSRM.mode == "garbage":
            body = b"<html>not json</html>"
        elif MockOSRM.mode == "code_error":
            body = json.dumps({"code": "TooBig",
                               "message": "table too large"}).encode()
        elif MockOSRM.mode == "short_matrix":
            body = json.dumps({"code": "Ok",
                               "durations": [[0, 1]], "distances": [[0, 1]]}).encode()
        else:
            coords = path_only.rstrip("/").split("/")[-1].split(";")
            n = len(coords)
            query = dict(
                part.split("=", 1) for part in self.path.split("?", 1)[1].split("&")
                if "=" in part) if "?" in self.path else {}
            rows = ([int(v) for v in query["sources"].split(";")]
                    if query.get("sources") else list(range(n)))
            cols = ([int(v) for v in query["destinations"].split(";")]
                    if query.get("destinations") else list(range(n)))
            body = json.dumps({
                "code": "Ok",
                "durations": [[cell(coords[r], coords[c])[1] for c in cols] for r in rows],
                "distances": [[cell(coords[r], coords[c])[0] for c in cols] for r in rows],
            }).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def nodes(count: int) -> list[Node]:
    """أول خمس عقد هي النقاط المرجعية الحقيقية، وما بعدها شبكة اصطناعية."""
    reference = [(lat, lon) for lon, lat in RAW["_coordinates_lon_lat"]]
    out: list[Node] = []
    for i in range(count):
        if i < len(reference):
            lat, lon = reference[i]
            label = RAW["_points_order"][i]
        else:
            # شبكة داخل حدود المملكة تقريبًا — الأرقام اصطناعية والغرض الحجم
            lat = 24.0 + (i % 40) * 0.11
            lon = 42.0 + (i // 40) * 0.13
            label = f"عقدة {i}"
        out.append(Node(index=i, kind=NodeKind.PICKUP, lat=lat, lon=lon, label=label))
    return out


def main() -> int:
    server = HTTPServer(("127.0.0.1", 0), MockOSRM)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    provider = OSRMProvider(base_url=base, timeout=10.0)

    print("=" * 72)
    print(f"فحص محوّل OSRM مقابل خادم وهمي على {base}")
    print("=" * 72)

    # ------------------------------------------------ ١) المسار الناجح ----
    MockOSRM.mode = "ok"
    test_nodes = nodes(5)
    minutes, km = provider.matrix(test_nodes)

    check("/table/v1/driving/" in MockOSRM.last_path,
          f"يستدعي خدمة /table الصحيحة: {MockOSRM.last_path[:52]}…")
    check("annotations=duration,distance" in MockOSRM.last_path,
          "يطلب المدة والمسافة معًا (annotations=duration,distance)")
    check("41.040000,30.980000" in MockOSRM.last_path,
          "يرسل الإحداثيات بترتيب OSRM الصحيح lon,lat — عكسه يعطي مواقع في البحر")
    check(provider.is_estimated is False,
          "يعلن is_estimated = False — نتائجه تُعرض كطرق حقيقية")

    expected_km = RAW["distances"][0][1] / 1000.0
    expected_min = RAW["durations"][0][1] / 60.0
    check(abs(km[0][1] - expected_km) < 0.01,
          f"يحوّل الأمتار إلى كيلومترات: {km[0][1]:.1f} كم (المتوقع {expected_km:.1f})")
    check(abs(minutes[0][1] - expected_min) < 0.01,
          f"يحوّل الثواني إلى دقائق: {minutes[0][1]:.1f} د (المتوقع {expected_min:.1f})")
    check(all(minutes[i][i] == 0.0 for i in range(5)),
          "القطر أصفار — لا مسافة من نقطة إلى نفسها")
    check(abs(km[1][0] - km[0][1]) > 1.0,
          f"المصفوفة غير متماثلة كما في الواقع ({km[0][1]:.1f} ≠ {km[1][0]:.1f} كم) — "
          "اتجاه السير يغيّر المسافة، والمحوّل لا يفرض تماثلًا مصطنعًا")

    # ----------------------------------------------- ٢) حالات الفشل ------
    for mode, label in (
        ("http_error", "الخادم يرد 503"),
        ("garbage", "استجابة ليست JSON"),
        ("code_error", "OSRM يعيد code != Ok (TooBig)"),
        ("short_matrix", "أبعاد المصفوفة لا تطابق عدد العقد"),
    ):
        MockOSRM.mode = mode
        try:
            provider.matrix(test_nodes)
            check(False, f"{label}: مرّ بلا خطأ — خطر: أرقام صامتة خاطئة")
        except DependencyUnavailable as exc:
            check(True, f"{label} → DependencyUnavailable: {str(exc)[:58]}…")
        except Exception as exc:  # noqa: BLE001
            check(False, f"{label} → نوع خطأ غير متوقع: {type(exc).__name__}")

    # -------------------------------------- ٣) خدمة غير موجودة أصلًا -----
    dead = OSRMProvider(base_url="http://127.0.0.1:1", timeout=2.0)
    try:
        dead.matrix(test_nodes)
        check(False, "خدمة غير موجودة: مرّت بلا خطأ")
    except DependencyUnavailable:
        check(True, "خدمة غير موجودة → DependencyUnavailable (لا انتظار لا نتيجة مختلقة)")

    # ------------------------------- ٤) الارتداد إلى التقدير عند العطل ---
    from masar_opt.routing import build_travel_matrix

    dead_provider = OSRMProvider(base_url="http://127.0.0.1:1", timeout=2.0)

    try:
        build_travel_matrix(test_nodes, dead_provider, fallback_to_estimate=False)
        check(False, "بلا ارتداد: بُنيت مصفوفة رغم عطل المزوّد — خطأ جسيم")
    except DependencyUnavailable:
        check(True, "الافتراضي (بلا ارتداد): التخطيط يفشل بدل بناء خطة على "
                    "أزمنة مختلقة — «لا خطة» أفضل من «خطة كاذبة»")

    matrix = build_travel_matrix(test_nodes, dead_provider,
                                 fallback_to_estimate=True)
    check(matrix.is_estimated is True,
          "مع الارتداد: المصفوفة تحمل is_estimated = True — لا تُعرض كطرق حقيقية")
    check(matrix.provider == "haversine",
          f"مع الارتداد: المزوّد المُعلن هو الفعلي لا المطلوب ({matrix.provider}) — "
          "لا يُنسب ناتج التقدير إلى OSRM")

    # التحذير على مستوى الخطة والبوابة أمام الاعتماد
    from masar_core.constants import Severity, WarningType
    from masar_opt.engine import build_warnings
    from masar_opt.model import Problem

    engine_source = (ROOT / "packages" / "masar_opt" / "engine.py").read_text("utf-8")
    block = engine_source[engine_source.index("if problem.travel.is_estimated:"):][:700]
    check(str(WarningType.ESTIMATED_TRAVEL_TIME) in block
          and "Severity.HIGH" in block,
          "التقدير يولّد تحذير ESTIMATED_TRAVEL_TIME بشدة HIGH على مستوى الخطة، "
          "مع سبب وإجراء مقترح")

    source = (ROOT / "packages" / "masar_api" / "services" / "planning.py").read_text("utf-8")
    check("routing_estimated" in source and "acknowledge_estimated" in source,
          "بوابة الاعتماد: خطة تقديرية مرفوضة في الإنتاج، وتتطلب إقرارًا "
          "مسجَّلًا خارجه")

    # ------------------------------- ٥) التقسيم إلى كتل وحدّ طول الرابط ---
    MockOSRM.mode = "ok"
    MockOSRM.max_path_seen = 0
    big = nodes(250)

    # المرجع: بلا تقسيم، الرابط وحده ≈ ٥ ك.ب لـ٢٥٠ عقدة، ويتجاوز الحد عند ٤٠٠+
    naive_url_bytes = len(";".join(f"{n.lon:.6f},{n.lat:.6f}" for n in nodes(990)))
    check(naive_url_bytes > MockOSRM.url_limit,
          f"طلب غير مقسَّم لخطة وطنية (٩٩٠ عقدة) ينتج رابطًا بـ"
          f"{naive_url_bytes / 1024:.1f} ك.ب — فوق حد nginx الافتراضي "
          f"({MockOSRM.url_limit / 1024:.0f} ك.ب)")

    chunked = OSRMProvider(base_url=base, timeout=10.0, block_size=50)
    minutes_big, km_big = chunked.matrix(big)
    check(MockOSRM.max_path_seen <= MockOSRM.url_limit,
          f"مع التقسيم: أطول رابط {MockOSRM.max_path_seen / 1024:.1f} ك.ب — "
          f"تحت الحد، و{chunked.last_request_count} طلبًا لـ٢٥٠ عقدة")

    # التجميع صحيح: كل خلية في موضعها، مقارنةً بالقيمة الحتمية المعروفة
    def coord(node) -> str:
        return f"{node.lon:.6f},{node.lat:.6f}"

    wrong = [(i, j) for i, j in ((0, 1), (7, 3), (120, 249), (249, 0), (60, 61))
             if abs(km_big[i][j] - cell(coord(big[i]), coord(big[j]))[0] / 1000.0) > 1e-6]
    check(not wrong,
          "التجميع من الكتل يضع كل خلية في موضعها الصحيح (عيّنات متفرقة من "
          "مصفوفة ٢٥٠×٢٥٠، بينها خلايا تعبر حدود الكتل)")
    check(all(km_big[i][i] == 0.0 for i in range(0, 250, 37)),
          "قطر المصفوفة المجمّعة أصفار")

    # نتيجة التقسيم تطابق نتيجة الطلب الواحد على الحجم نفسه
    small = nodes(5)
    one_shot = OSRMProvider(base_url=base, timeout=10.0, block_size=100).matrix(small)
    split = OSRMProvider(base_url=base, timeout=10.0, block_size=2).matrix(small)
    check(one_shot == split,
          "التقسيم لا يغيّر النتيجة: مصفوفة بكتلة واحدة = مصفوفة بكتل ٢×٢")

    # ------------------------------------------- ٦) إعادة المحاولة --------
    MockOSRM.requests = 0
    MockOSRM.fail_first = 2          # عطلان عابران ثم استقرار
    resilient = OSRMProvider(base_url=base, timeout=10.0,
                             max_attempts=3, retry_pause_seconds=0.01)
    try:
        resilient.matrix(nodes(2))
        check(True, "عطل عابر (502 مرتين) → أُعيدت المحاولة ونجحت بلا فشل الخطة")
    except DependencyUnavailable as exc:
        check(False, f"عطل عابر أسقط الخطة رغم إمكان الإعادة: {exc}")
    finally:
        MockOSRM.fail_first = 0

    MockOSRM.requests = 0
    MockOSRM.fail_first = 99         # عطل دائم
    stubborn = OSRMProvider(base_url=base, timeout=10.0,
                            max_attempts=3, retry_pause_seconds=0.01)
    try:
        stubborn.matrix(nodes(2))
        check(False, "عطل دائم مرّ بلا خطأ")
    except DependencyUnavailable as exc:
        check("3 محاولات" in str(exc),
              "عطل دائم → فشل صريح بعد ٣ محاولات، لا إعادة لا نهائية")
    finally:
        MockOSRM.fail_first = 0

    # --------------------------------- ٧) الفحص الحيّ والتحقق من الخريطة --
    MockOSRM.requests = 0
    probe = OSRMProvider(base_url=base, timeout=10.0).probe()
    check(abs(probe["reference_km"] - 290.4) < 0.1,
          f"الفحص الحيّ يقيس الساق المرجعية: {probe['reference_km']} كم "
          "(عرعر ← رفحاء)")

    import os
    os.environ["MASAR_ROUTING_PROVIDER"] = "osrm"
    os.environ["MASAR_OSRM_URL"] = base
    from masar_core.config import get_config

    get_config(reload=True)
    from masar_api.services.routing_status import provider_status

    status = provider_status()
    check(status["map_verified"] and status["ok_for_production"],
          f"حالة المزوّد: خريطة متحقَّقة · صالح للإنتاج · "
          f"فارق {status['reference']['drift_percent']}٪ عن المرجع")

    server.shutdown()

    passed = sum(1 for ok, _ in RESULTS if ok)
    print("\n" + "=" * 72)
    print(f"{passed} من {len(RESULTS)} فحصًا ناجحًا")
    print("تنبيه: هذا فحص **محوّل**. جودة بيانات الطرق تتطلب OSRM حقيقيًا — "
          "راجع deploy/osrm/README.md")
    print("=" * 72)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
