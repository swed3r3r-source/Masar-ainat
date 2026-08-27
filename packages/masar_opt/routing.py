"""مزوّدو المسافات وأزمنة القيادة — خلف واجهة واحدة قابلة للاستبدال.

* :class:`OSRMProvider` — مسافات وأزمنة **طريق حقيقية** (خدمة مفتوحة، ذاتية الاستضافة).
* :class:`MatrixFileProvider` — مصفوفة محفوظة (للاختبارات ولإعادة إنتاج خطة قديمة).
* :class:`HaversineProvider` — **تقديري**: خط مستقيم × معامل التفافية، بسرعات
  حسب نوع المسار. يُوسم ناتجه دائمًا ``is_estimated=True`` ويمنع الإعداد
  استخدامه في الإنتاج (§23 «إلا كحل تقديري معلن»).
"""

from __future__ import annotations

import json
import math
from typing import Protocol, Sequence

from masar_core.config import get_config
from masar_core.errors import DependencyUnavailable
from masar_core.timeutil import haversine_km

from .model import Node, NodeKind, TravelMatrix

#: ساق مرجعية مقيسة على OSRM/OpenStreetMap لمنطقة الحدود الشمالية.
#: تُستعمل لفحص أن الخدمة تعمل **وأنها محمّلة على خريطة السعودية** — خدمة
#: ترد ``Ok`` على خريطة منطقة أخرى تبدو سليمة وهي عديمة القيمة. التسامح واسع
#: عمدًا (±١٠٪) ليستوعب تغيّر خريطة OSM بين الإصدارات دون أن يتساهل مع خطأ جسيم.
REFERENCE_LEG: dict = {
    "from": (30.9800, 41.0400), "from_label": "مركز انطلاق عرعر",
    "to": (29.6202, 43.4980), "to_label": "مركز صحي رفحاء",
    "expected_km": 290.4, "expected_minutes": 187.2, "tolerance": 0.10,
    "measured_at": "2026-08-26", "source": "OSRM/OpenStreetMap",
}


class RoutingProvider(Protocol):
    name: str
    is_estimated: bool

    def matrix(self, nodes: Sequence[Node]) -> tuple[list[list[float]], list[list[float]]]:
        """يعيد (مصفوفة الدقائق، مصفوفة الكيلومترات)."""
        ...


class HaversineProvider:
    """تقدير معلن — ليس زمن طريق حقيقيًا."""

    name = "haversine"
    is_estimated = True

    def __init__(
        self,
        *,
        detour_factor: float | None = None,
        urban_speed_kmh: float | None = None,
        intercity_speed_kmh: float | None = None,
        intercity_threshold_km: float | None = None,
    ) -> None:
        cfg = get_config().routing
        self.detour_factor = detour_factor if detour_factor is not None else cfg.detour_factor
        self.urban_speed = urban_speed_kmh or cfg.urban_speed_kmh
        self.intercity_speed = intercity_speed_kmh or cfg.intercity_speed_kmh
        self.threshold_km = (
            intercity_threshold_km if intercity_threshold_km is not None
            else cfg.intercity_threshold_km
        )

    def leg(self, a: Node, b: Node) -> tuple[float, float]:
        straight = haversine_km(a.lat, a.lon, b.lat, b.lon)
        road_km = straight * self.detour_factor
        if road_km <= 0:
            return 0.0, 0.0
        if road_km <= self.threshold_km:
            speed = self.urban_speed
        else:
            # مزيج: الجزء الحضري بسرعة المدينة والباقي بسرعة الطريق السريع
            urban_part = self.threshold_km
            highway_part = road_km - self.threshold_km
            minutes = (urban_part / self.urban_speed + highway_part / self.intercity_speed) * 60.0
            return minutes, road_km
        return (road_km / speed) * 60.0, road_km

    def matrix(self, nodes: Sequence[Node]) -> tuple[list[list[float]], list[list[float]]]:
        size = len(nodes)
        minutes = [[0.0] * size for _ in range(size)]
        km = [[0.0] * size for _ in range(size)]
        for i in range(size):
            for j in range(size):
                if i == j:
                    continue
                m, d = self.leg(nodes[i], nodes[j])
                minutes[i][j] = m
                km[i][j] = d
        return minutes, km


class OSRMProvider:
    """مزوّد أزمنة ومسافات طريق حقيقية عبر خدمة OSRM (``/table`` service).

    **لماذا التقسيم إلى كتل؟** خدمة ``/table`` تستقبل الإحداثيات في *مسار*
    الرابط. عقدة واحدة ≈ ٢١ محرفًا، فخطة بـ٩٩٠ عقدة تنتج رابطًا بـ١٩ ك.ب —
    وأغلب خوادم HTTP ترفض ما يتجاوز ٨ ك.ب (``large_client_header_buffers``
    في nginx افتراضيًا ٨ ك.ب). أي أن الطلب الواحد يعمل في الاختبارات الصغيرة
    ثم **ينهار عند أول خطة وطنية**. لذلك تُطلب المصفوفة على كتل، وكل كتلة
    ترسل إحداثيات صفوفها وأعمدتها فقط مع ``sources``/``destinations``.

    كتلة ١٠٠×١٠٠ تعني رابطًا ≈ ٤ ك.ب مهما بلغ حجم الخطة، وعدد الطلبات
    ``ceil(n/B)²`` — ١٠٠ طلب لـ٩٩٠ عقدة، وهي أجزاء من الثانية على خدمة محلية.
    """

    name = "osrm"
    is_estimated = False

    #: حجم الكتلة الافتراضي — يوازن بين طول الرابط وعدد الطلبات
    DEFAULT_BLOCK = 100

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        *,
        block_size: int | None = None,
        max_attempts: int = 3,
        retry_pause_seconds: float = 0.5,
    ) -> None:
        cfg = get_config().routing
        self.base_url = (base_url or cfg.osrm_base_url or "").rstrip("/")
        self.timeout = timeout or cfg.request_timeout_seconds
        self.block_size = max(2, int(
            block_size if block_size is not None
            else getattr(cfg, "osrm_block_size", None) or self.DEFAULT_BLOCK))
        self.max_attempts = max(1, int(max_attempts))
        self.retry_pause = max(0.0, float(retry_pause_seconds))
        #: عدد الطلبات في آخر بناء مصفوفة — للتشخيص لا للمنطق
        self.last_request_count = 0
        if not self.base_url:
            raise DependencyUnavailable(
                "عنوان خدمة OSRM غير محدد — عيّن MASAR_OSRM_URL"
            )

    # ------------------------------------------------------------ الشبكة --
    def _get(self, url: str) -> dict:
        """طلب واحد مع إعادة محاولة للأعطال العابرة فقط.

        العطل العابر (تعذّر اتصال، مهلة) يُعاد؛ أما رد OSRM بـ``code`` خطأ
        فهو حكم نهائي (طلب أكبر من الحد مثلًا) وإعادته عبث يضاعف الزمن.
        """
        import time
        import urllib.error
        import urllib.request

        last: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                self.last_request_count += 1
                with urllib.request.urlopen(url, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise DependencyUnavailable(
                    "استجابة غير صالحة من خدمة الطرق", provider="osrm"
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
                if attempt + 1 < self.max_attempts and self.retry_pause:
                    time.sleep(self.retry_pause * (attempt + 1))
        raise DependencyUnavailable(
            f"تعذر الوصول إلى خدمة الطرق (OSRM) بعد {self.max_attempts} محاولات: {last}",
            provider="osrm", url=self.base_url,
        ) from last

    def _block(
        self, nodes: Sequence[Node], rows: Sequence[int], cols: Sequence[int]
    ) -> tuple[list[list[float]], list[list[float]]]:
        """يجلب كتلة (صفوف × أعمدة) بإرسال إحداثيات هذه العقد وحدها."""
        wanted = sorted(set(rows) | set(cols))
        position = {index: order for order, index in enumerate(wanted)}
        coords = ";".join(
            f"{nodes[index].lon:.6f},{nodes[index].lat:.6f}" for index in wanted)
        sources = ";".join(str(position[index]) for index in rows)
        destinations = ";".join(str(position[index]) for index in cols)
        url = (
            f"{self.base_url}/table/v1/driving/{coords}"
            f"?annotations=duration,distance"
            f"&sources={sources}&destinations={destinations}"
        )
        payload = self._get(url)

        if payload.get("code") != "Ok":
            raise DependencyUnavailable(
                f"خدمة الطرق أعادت خطأ: {payload.get('code')} — {payload.get('message')}",
                provider="osrm",
            )
        durations = payload.get("durations") or []
        distances = payload.get("distances") or []
        if (len(durations) != len(rows) or len(distances) != len(rows)
                or any(len(row) != len(cols) for row in durations)
                or any(len(row) != len(cols) for row in distances)):
            raise DependencyUnavailable(
                "أبعاد مصفوفة خدمة الطرق لا تطابق عدد العقد", provider="osrm"
            )
        return durations, distances

    # ------------------------------------------------------------ المصفوفة
    def matrix(self, nodes: Sequence[Node]) -> tuple[list[list[float]], list[list[float]]]:
        size = len(nodes)
        self.last_request_count = 0
        minutes = [[0.0] * size for _ in range(size)]
        km = [[0.0] * size for _ in range(size)]
        if size == 0:
            return minutes, km

        step = self.block_size
        for row_start in range(0, size, step):
            rows = list(range(row_start, min(row_start + step, size)))
            for col_start in range(0, size, step):
                cols = list(range(col_start, min(col_start + step, size)))
                durations, distances = self._block(nodes, rows, cols)
                for r, row_index in enumerate(rows):
                    duration_row, distance_row = durations[r], distances[r]
                    for c, col_index in enumerate(cols):
                        if row_index == col_index:
                            continue
                        value = duration_row[c]
                        minutes[row_index][col_index] = (
                            0.0 if value is None else float(value) / 60.0)
                        value = distance_row[c]
                        km[row_index][col_index] = (
                            0.0 if value is None else float(value) / 1000.0)
        return minutes, km

    # -------------------------------------------------------- حالة الخدمة
    def probe(self) -> dict[str, object]:
        """يستدعي الخدمة فعليًا على مسار معروف ويعيد ما ردّت به.

        الغرض أن يكون «المزوّد مضبوط» و«المزوّد يعمل» شيئين مختلفين: الأول
        قراءة إعداد، والثاني نتيجة استدعاء.
        """
        import time

        started = time.monotonic()
        probe_nodes = [
            Node(index=0, kind=NodeKind.PICKUP, lat=REFERENCE_LEG["from"][0],
                 lon=REFERENCE_LEG["from"][1], label=REFERENCE_LEG["from_label"]),
            Node(index=1, kind=NodeKind.PICKUP, lat=REFERENCE_LEG["to"][0],
                 lon=REFERENCE_LEG["to"][1], label=REFERENCE_LEG["to_label"]),
        ]
        minutes, km = self.matrix(probe_nodes)
        return {
            "reachable": True,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "reference_km": round(km[0][1], 1),
            "reference_minutes": round(minutes[0][1], 1),
            "block_size": self.block_size,
        }


class MatrixFileProvider:
    """يقرأ مصفوفة محفوظة — يُستخدم للاختبارات ولإعادة إنتاج خطة سابقة بالضبط."""

    name = "matrix_file"
    is_estimated = False

    def __init__(self, path: str | None = None, data: dict | None = None) -> None:
        if data is None:
            path = path or get_config().routing.matrix_file
            if not path:
                raise DependencyUnavailable("مسار ملف المصفوفة غير محدد")
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        self._keys: list[str] = data["keys"]
        self._minutes: list[list[float]] = data["minutes"]
        self._km: list[list[float]] = data["km"]
        self._index = {key: i for i, key in enumerate(self._keys)}

    def matrix(self, nodes: Sequence[Node]) -> tuple[list[list[float]], list[list[float]]]:
        size = len(nodes)
        minutes = [[0.0] * size for _ in range(size)]
        km = [[0.0] * size for _ in range(size)]
        for i, a in enumerate(nodes):
            for j, b in enumerate(nodes):
                if i == j:
                    continue
                key_a, key_b = self._node_key(a), self._node_key(b)
                if key_a not in self._index or key_b not in self._index:
                    raise DependencyUnavailable(
                        f"مصفوفة الملف لا تغطي المسار {key_a} ← {key_b}"
                    )
                minutes[i][j] = self._minutes[self._index[key_a]][self._index[key_b]]
                km[i][j] = self._km[self._index[key_a]][self._index[key_b]]
        return minutes, km

    @staticmethod
    def _node_key(node: Node) -> str:
        return node.facility_id or node.hub_id or f"{node.lat:.5f},{node.lon:.5f}"


class FailingProvider:
    """مزوّد يفشل دائمًا — للاختبار رقم 44 (فشل خدمة الخرائط)."""

    name = "failing"
    is_estimated = False

    def matrix(self, nodes: Sequence[Node]) -> tuple[list[list[float]], list[list[float]]]:
        raise DependencyUnavailable(
            "خدمة الطرق غير متاحة (مزوّد اختبار الفشل)", provider="failing"
        )


def build_provider(name: str | None = None, **kwargs) -> RoutingProvider:
    cfg = get_config().routing
    name = (name or cfg.provider).lower()
    if name == "osrm":
        return OSRMProvider(**kwargs)
    if name == "matrix_file":
        return MatrixFileProvider(**kwargs)
    if name == "failing":
        return FailingProvider()
    if name == "haversine":
        return HaversineProvider(**kwargs)
    raise DependencyUnavailable(f"مزوّد طرق غير معروف: {name}")


def build_travel_matrix(
    nodes: Sequence[Node],
    provider: RoutingProvider | None = None,
    *,
    peak_periods: list[str] | None = None,
    peak_multiplier: float | None = None,
    timezone_name: str = "Asia/Riyadh",
    fallback_to_estimate: bool = False,
) -> TravelMatrix:
    """يبني مصفوفة السفر، مع خيار الرجوع للتقدير عند فشل المزوّد الحقيقي.

    الرجوع للتقدير **ليس صامتًا**: النتيجة تحمل ``is_estimated=True`` وتُترجم
    إلى تحذير ``ESTIMATED_TRAVEL_TIME`` على مستوى الخطة يمنع اعتمادها بصمت.
    """
    provider = provider or build_provider()
    used = provider
    try:
        minutes, km = provider.matrix(nodes)
    except DependencyUnavailable:
        if not fallback_to_estimate:
            raise
        used = HaversineProvider()
        minutes, km = used.matrix(nodes)

    peak = None
    if peak_periods and peak_multiplier and peak_multiplier > 1.0:
        peak = [[value * peak_multiplier for value in row] for row in minutes]

    return TravelMatrix(
        minutes, km,
        is_estimated=used.is_estimated,
        provider=used.name,
        peak_minutes=peak,
        peak_periods=peak_periods,
        timezone_name=timezone_name,
    )
