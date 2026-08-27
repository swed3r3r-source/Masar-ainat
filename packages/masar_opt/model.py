"""نموذج مسألة التخطيط — مستقل تمامًا عن قاعدة البيانات وعن الواجهة.

الزمن يُمثَّل بـ **دقائق مطلقة** (عدد الدقائق منذ حقبة يونكس) كأعداد عشرية،
لتفادي أي التباس في المناطق الزمنية داخل المحرك. التحويل من/إلى
``datetime`` يتم في الحدود فقط (``from_datetime`` / ``to_datetime``).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


def from_datetime(value: dt.datetime) -> float:
    return value.timestamp() / 60.0


def to_datetime(minutes: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(minutes * 60.0, tz=dt.timezone.utc)


class NodeKind(StrEnum):
    START = "START"
    PICKUP = "PICKUP"
    DELIVERY = "DELIVERY"


@dataclass(slots=True)
class Node:
    """عقدة في الشبكة: نقطة بداية أو التقاط أو تسليم."""

    index: int
    kind: NodeKind
    lat: float
    lon: float
    label: str
    service_minutes: float = 0.0
    #: نافذة زمنية مطلقة بالدقائق؛ ``None`` = بلا حد
    window_from: float | None = None
    window_to: float | None = None
    shipment_index: int = -1
    facility_id: str | None = None
    facility_type: str | None = None
    mixing_class: str | None = None
    hub_id: str | None = None
    external_id: str | None = None

    @property
    def is_pickup(self) -> bool:
        return self.kind is NodeKind.PICKUP

    @property
    def is_delivery(self) -> bool:
        return self.kind is NodeKind.DELIVERY


@dataclass(slots=True)
class ShipmentSpec:
    """شحنة = زوج التقاط/تسليم مع SLA."""

    index: int
    shipment_id: str
    reference: str
    pickup_node: int
    delivery_node: int
    sla_deadline: float
    piece_count: int = 1
    service_type: str = "ROUTINE"
    temperature_mode: str = "AMBIENT"
    hub_id: str | None = None
    #: أولوية مسبقة (للطلبات الفورية العاجلة)
    priority: int = 0
    #: هل هي طلب فوري أُدرج ديناميكيًا؟
    is_on_demand: bool = False
    #: قيود ترتيب مُولَّدة: هذه الشحنة يجب أن تُسلَّم قبل التقاط الشحنات التالية (HC-11)
    delivery_before_pickups: tuple[int, ...] = ()


@dataclass(slots=True)
class VehicleSpec:
    """خانة سائق/مركبة متاحة للتخطيط في يوم محدد."""

    index: int
    hub_id: str
    start_node: int
    earliest_start: float
    latest_end: float
    max_shift_minutes: float
    max_long_haul: int = 1
    driver_id: str | None = None
    vehicle_id: str | None = None
    label: str = ""
    #: للرحلة المتسلسلة: هذه المركبة تبدأ من نهاية رحلة سابقة (HC-10)
    chained_from_route: str | None = None
    #: محطات منفَّذة مسبقًا لا يجوز إعادة ترتيبها (الإدراج الديناميكي)
    locked_prefix: tuple[int, ...] = ()


@dataclass(slots=True)
class ProblemSettings:
    """القيم التشغيلية الفعالة للنطاق — كلها قادمة من الإعدادات لا من الكود."""

    max_shift_minutes: float = 600.0
    min_event_gap_minutes: float = 0.0
    post_pickup_departure_minutes: float = 15.0
    allow_early_arrival: bool = False
    long_haul_km: float = 150.0
    max_long_haul_per_driver_per_day: int = 1
    post_long_haul_policy: str = "NO_CITY_HOPPING"
    post_long_haul_min_stop_km: float = 25.0
    enforce_facility_mixing_rule: bool = True
    mixing_exempt_classes: frozenset[str] = frozenset({"BLOOD"})
    require_return_to_hub: bool = False
    count_return_leg_in_shift: bool = False
    max_routes_per_driver_per_day: int = 3
    max_wait_minutes_per_stop: float = 45.0
    cost_per_km: float = 1.10
    cost_per_driver_day: float = 420.0
    cost_per_hour: float = 0.0
    fairness_weight: float = 1.0
    hub_load_minutes: float = 15.0
    #: هامش تسامح عددي بالدقائق لتفادي أخطاء الفاصلة العائمة
    epsilon_minutes: float = 1e-6

    @classmethod
    def from_effective(cls, effective: dict[str, Any]) -> "ProblemSettings":
        """يبني الإعدادات من ناتج ``SettingsResolver.effective()``."""
        exempt_types = effective.get("mixing_exempt_facility_types", ["BLOOD_BANK"])
        from masar_core.constants import FACILITY_MIXING_CLASS

        exempt_classes = frozenset(
            FACILITY_MIXING_CLASS.get(str(t), str(t)) for t in exempt_types
        )
        return cls(
            max_shift_minutes=float(effective["max_shift_hours"]) * 60.0,
            min_event_gap_minutes=float(effective["min_event_gap_minutes"]),
            post_pickup_departure_minutes=float(effective["post_pickup_departure_minutes"]),
            allow_early_arrival=bool(effective["allow_early_arrival"]),
            long_haul_km=float(effective["long_haul_km"]),
            max_long_haul_per_driver_per_day=int(
                effective["max_long_haul_per_driver_per_day"]),
            post_long_haul_policy=str(effective["post_long_haul_policy"]),
            post_long_haul_min_stop_km=float(effective["post_long_haul_min_stop_km"]),
            enforce_facility_mixing_rule=bool(effective["enforce_facility_mixing_rule"]),
            mixing_exempt_classes=exempt_classes,
            require_return_to_hub=bool(effective["require_return_to_hub"]),
            count_return_leg_in_shift=bool(effective["count_return_leg_in_shift"]),
            max_routes_per_driver_per_day=int(effective["max_routes_per_driver_per_day"]),
            max_wait_minutes_per_stop=float(effective["max_wait_minutes_per_stop"]),
            cost_per_km=float(effective["cost_per_km"]),
            cost_per_driver_day=float(effective["cost_per_driver_day"]),
            cost_per_hour=float(effective["cost_per_hour"]),
            fairness_weight=float(effective["fairness_weight"]),
            hub_load_minutes=float(effective["hub_load_minutes"]),
        )


@dataclass(slots=True)
class Problem:
    """المسألة الكاملة ليوم تشغيلي ومركز انطلاق واحد أو أكثر."""

    nodes: list[Node]
    shipments: list[ShipmentSpec]
    vehicles: list[VehicleSpec]
    settings: ProblemSettings
    service_date: dt.date
    #: مصفوفة الأزمنة والمسافات
    travel: "TravelMatrix" = None  # type: ignore[assignment]
    #: بيانات وصفية للتفسير
    meta: dict[str, Any] = field(default_factory=dict)

    def shipment_of_node(self, node_index: int) -> ShipmentSpec | None:
        node = self.nodes[node_index]
        if node.shipment_index < 0:
            return None
        return self.shipments[node.shipment_index]

    def hub_start_node(self, hub_id: str) -> int:
        for node in self.nodes:
            if node.kind is NodeKind.START and node.hub_id == hub_id:
                return node.index
        raise KeyError(f"لا توجد عقدة بداية لمركز الانطلاق {hub_id}")


class TravelMatrix:
    """مصفوفة أزمنة ومسافات بين العقد، مع دعم تباين أزمنة الذروة."""

    __slots__ = ("_minutes", "_km", "size", "is_estimated", "provider", "_peak_minutes",
                 "_peak_periods", "_timezone")

    def __init__(
        self,
        minutes: list[list[float]],
        km: list[list[float]],
        *,
        is_estimated: bool,
        provider: str,
        peak_minutes: list[list[float]] | None = None,
        peak_periods: list[str] | None = None,
        timezone_name: str = "Asia/Riyadh",
    ) -> None:
        self._minutes = minutes
        self._km = km
        self.size = len(minutes)
        self.is_estimated = is_estimated
        self.provider = provider
        self._peak_minutes = peak_minutes
        self._peak_periods = peak_periods or []
        self._timezone = timezone_name

    def minutes(self, i: int, j: int, depart_at: float | None = None) -> float:
        """زمن القيادة من ``i`` إلى ``j``؛ يراعي الذروة إن توفرت مصفوفة ذروة."""
        if self._peak_minutes is not None and depart_at is not None and self._in_peak(depart_at):
            return self._peak_minutes[i][j]
        return self._minutes[i][j]

    def km(self, i: int, j: int) -> float:
        return self._km[i][j]

    def _in_peak(self, depart_at: float) -> bool:
        if not self._peak_periods:
            return False
        from masar_core.timeutil import in_periods

        return in_periods(to_datetime(depart_at), self._peak_periods, self._timezone)

    @property
    def has_peak_profile(self) -> bool:
        return self._peak_minutes is not None
