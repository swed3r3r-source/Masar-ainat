"""أدوات الوقت والمنطقة الزمنية والجغرافيا.

قاعدة (§28): يُخزَّن كل وقت مطلق بـ UTC (`timestamptz`)، والعرض والإدخال
بالتوقيت التشغيلي للنطاق. أوقات العمل والنوافذ اليومية تُخزَّن كـ `time`
محلي + التاريخ التشغيلي، ولا تُخلط بالأوقات المطلقة.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from zoneinfo import ZoneInfo

from .errors import ValidationError

UTC = dt.timezone.utc
DEFAULT_TZ = "Asia/Riyadh"

#: حدود المملكة العربية السعودية التقريبية للتحقق من الإحداثيات
KSA_BOUNDS = {"lat_min": 15.5, "lat_max": 32.4, "lon_min": 34.3, "lon_max": 56.0}


def tz(name: str = DEFAULT_TZ) -> ZoneInfo:
    return ZoneInfo(name)


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def to_utc(value: dt.datetime, timezone_name: str = DEFAULT_TZ) -> dt.datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz(timezone_name))
    return value.astimezone(UTC)


def to_local(value: dt.datetime, timezone_name: str = DEFAULT_TZ) -> dt.datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(tz(timezone_name))


def combine_local(
    day: dt.date, clock: dt.time, timezone_name: str = DEFAULT_TZ
) -> dt.datetime:
    """يجمع تاريخًا تشغيليًا ووقتًا محليًا إلى لحظة UTC صحيحة."""
    return dt.datetime.combine(day, clock, tzinfo=tz(timezone_name)).astimezone(UTC)


def operational_date(value: dt.datetime, timezone_name: str = DEFAULT_TZ) -> dt.date:
    return to_local(value, timezone_name).date()


def minutes_between(start: dt.datetime, end: dt.datetime) -> float:
    return (end - start).total_seconds() / 60.0


def add_minutes(value: dt.datetime, minutes: float) -> dt.datetime:
    return value + dt.timedelta(minutes=minutes)


# ------------------------------------------------------------- التحليل -----

_DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%m/%d/%Y", "%d.%m.%Y",
)
_TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p")

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")


def normalize_digits(text: str) -> str:
    """يحوّل الأرقام العربية الهندية إلى لاتينية (شائع في ملفات Excel العربية)."""
    return text.translate(_ARABIC_DIGITS)


def parse_date(raw: object, *, field: str = "التاريخ") -> dt.date:
    if isinstance(raw, dt.datetime):
        return raw.date()
    if isinstance(raw, dt.date):
        return raw
    text = normalize_digits(str(raw).strip())
    if not text:
        raise ValidationError(f"{field} مفقود")
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValidationError(
        f"{field} بصيغة غير مفهومة: {raw!r}. الصيغ المقبولة: "
        "YYYY-MM-DD أو DD/MM/YYYY"
    )


def parse_time(raw: object, *, field: str = "الوقت") -> dt.time:
    if isinstance(raw, dt.datetime):
        return raw.time()
    if isinstance(raw, dt.time):
        return raw
    text = normalize_digits(str(raw).strip()).upper().replace("ص", "AM").replace("م", "PM")
    if not text:
        raise ValidationError(f"{field} مفقود")
    # قيم Excel الكسرية (0.5 = 12:00)
    if re.fullmatch(r"0?\.\d+", text):
        total = round(float(text) * 24 * 60)
        return dt.time(total // 60 % 24, total % 60)
    for fmt in _TIME_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise ValidationError(
        f"{field} بصيغة غير مفهومة: {raw!r}. الصيغ المقبولة: HH:MM أو HH:MM:SS"
    )


def parse_datetime(raw: object, *, field: str = "الوقت", timezone_name: str = DEFAULT_TZ) -> dt.datetime:
    if isinstance(raw, dt.datetime):
        return to_utc(raw, timezone_name)
    text = normalize_digits(str(raw).strip())
    if not text:
        raise ValidationError(f"{field} مفقود")
    try:
        return to_utc(dt.datetime.fromisoformat(text), timezone_name)
    except ValueError:
        pass
    for date_fmt in _DATE_FORMATS:
        for time_fmt in _TIME_FORMATS:
            try:
                parsed = dt.datetime.strptime(text, f"{date_fmt} {time_fmt}")
                return to_utc(parsed, timezone_name)
            except ValueError:
                continue
    raise ValidationError(f"{field} بصيغة تاريخ ووقت غير مفهومة: {raw!r}")


def parse_window_spec(spec: str) -> tuple[dt.time, dt.time]:
    """يحلل ``"06:30-09:00"`` إلى وقتين."""
    try:
        start_text, end_text = spec.split("-")
    except ValueError:
        raise ValidationError(f"صيغة فترة غير صحيحة: {spec!r}") from None
    return parse_time(start_text), parse_time(end_text)


def in_periods(moment: dt.datetime, periods: list[str], timezone_name: str = DEFAULT_TZ) -> bool:
    local_time = to_local(moment, timezone_name).time()
    for spec in periods:
        start, end = parse_window_spec(spec)
        if start <= end:
            if start <= local_time <= end:
                return True
        elif local_time >= start or local_time <= end:
            return True
    return False


# ----------------------------------------------------------- الجغرافيا -----

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """المسافة الجوية بين نقطتين بالكيلومتر."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat = p2 - p1
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def validate_coordinates(
    lat: object, lon: object, *, enforce_ksa_bounds: bool = True, label: str = "الإحداثيات"
) -> tuple[float, float]:
    """يتحقق من الإحداثيات ويعيدها كأرقام عشرية. يرفع :class:`ValidationError`."""
    try:
        lat_f = float(normalize_digits(str(lat).strip()))
        lon_f = float(normalize_digits(str(lon).strip()))
    except (TypeError, ValueError):
        raise ValidationError(f"{label} ليست أرقامًا صالحة: ({lat!r}, {lon!r})") from None

    if not (-90.0 <= lat_f <= 90.0):
        raise ValidationError(f"{label}: خط العرض خارج المدى (-90..90): {lat_f}")
    if not (-180.0 <= lon_f <= 180.0):
        raise ValidationError(f"{label}: خط الطول خارج المدى (-180..180): {lon_f}")
    if lat_f == 0.0 and lon_f == 0.0:
        raise ValidationError(f"{label}: إحداثيات (0,0) غير مقبولة")
    if enforce_ksa_bounds and not (
        KSA_BOUNDS["lat_min"] <= lat_f <= KSA_BOUNDS["lat_max"]
        and KSA_BOUNDS["lon_min"] <= lon_f <= KSA_BOUNDS["lon_max"]
    ):
        raise ValidationError(
            f"{label}: النقطة ({lat_f}, {lon_f}) خارج حدود المملكة التقريبية. "
            "تحقق من تبديل خط الطول والعرض."
        )
    return lat_f, lon_f


def format_duration_ar(minutes: float) -> str:
    total = int(round(minutes))
    hours, mins = divmod(max(0, total), 60)
    if hours and mins:
        return f"{hours} س {mins} د"
    if hours:
        return f"{hours} س"
    return f"{mins} د"
