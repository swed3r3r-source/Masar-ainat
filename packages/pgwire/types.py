"""ترميز وفك ترميز القيم بين بايثون و PostgreSQL (تنسيق نصي)."""

from __future__ import annotations

import datetime as _dt
import json
import re
import uuid
from decimal import Decimal
from typing import Any


class Json:
    """غلاف يجبر الترميز إلى نوع ``json``."""

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value


class Jsonb(Json):
    """غلاف يجبر الترميز إلى نوع ``jsonb``."""


class SqlLiteral:
    """نص SQL يُدرج كما هو (لأسماء الأعمدة/الاتجاهات فقط، بعد قائمة بيضاء)."""

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*(\s+(ASC|DESC|asc|desc))?", text):
            raise ValueError(f"SqlLiteral مرفوض: {text!r}")
        self.text = text

    def __str__(self) -> str:  # pragma: no cover - تمثيل فقط
        return self.text


# ---------------------------------------------------------------- الترميز ---

def _quote_array_element(item: Any) -> str:
    if item is None:
        return "NULL"
    text = encode_param(item)
    if text is None:
        return "NULL"
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def encode_param(value: Any) -> str | None:
    """يحول قيمة بايثون إلى تمثيلها النصي في البروتوكول (أو ``None`` لـ NULL)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Json):
        return json.dumps(value.value, ensure_ascii=False, default=_json_default)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "\\x" + bytes(value).hex()
    if isinstance(value, _dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, _dt.time):
        return value.isoformat()
    if isinstance(value, _dt.timedelta):
        total = value.total_seconds()
        return f"{total} seconds"
    if isinstance(value, (list, tuple)):
        return "{" + ",".join(_quote_array_element(v) for v in value) + "}"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=_json_default)
    if isinstance(value, set):
        return "{" + ",".join(_quote_array_element(v) for v in sorted(value, key=str)) + "}"
    raise TypeError(f"نوع غير مدعوم كمعامل استعلام: {type(value).__name__}")


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, set):
        return sorted(obj, key=str)
    raise TypeError(f"غير قابل للتحويل إلى JSON: {type(obj).__name__}")


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=_json_default)


# ------------------------------------------------------------- فك الترميز ---

BOOL = 16
BYTEA = 17
CHAR = 18
NAME = 19
INT8 = 20
INT2 = 21
INT4 = 23
TEXT = 25
OID = 26
JSON = 114
XML = 142
FLOAT4 = 700
FLOAT8 = 701
UNKNOWN = 705
BPCHAR = 1042
VARCHAR = 1043
DATE = 1082
TIME = 1083
TIMESTAMP = 1114
TIMESTAMPTZ = 1184
INTERVAL = 1186
NUMERIC = 1700
UUID = 2950
JSONB = 3802

_ARRAY_ELEMENT: dict[int, int] = {
    1000: BOOL, 1001: BYTEA, 1005: INT2, 1007: INT4, 1016: INT8,
    1009: TEXT, 1015: VARCHAR, 1014: BPCHAR, 1021: FLOAT4, 1022: FLOAT8,
    1182: DATE, 1183: TIME, 1115: TIMESTAMP, 1185: TIMESTAMPTZ,
    1231: NUMERIC, 2951: UUID, 199: JSON, 3807: JSONB, 1028: OID,
}

_TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?"
    r"(?:([+-])(\d{2})(?::?(\d{2}))?(?::?(\d{2}))?)?$"
)


def _parse_timestamp(text: str, *, tz_aware: bool) -> _dt.datetime:
    m = _TS_RE.match(text.strip())
    if not m:
        # قيم خاصة: infinity / -infinity
        if text.strip() == "infinity":
            return _dt.datetime.max.replace(tzinfo=_dt.timezone.utc if tz_aware else None)
        if text.strip() == "-infinity":
            return _dt.datetime.min.replace(tzinfo=_dt.timezone.utc if tz_aware else None)
        raise ValueError(f"طابع زمني غير مفهوم: {text!r}")
    y, mo, d, h, mi, s = (int(m.group(i)) for i in range(1, 7))
    micro = int((m.group(7) or "0").ljust(6, "0"))
    tzinfo: _dt.tzinfo | None = None
    if m.group(8):
        sign = 1 if m.group(8) == "+" else -1
        offset = _dt.timedelta(
            hours=int(m.group(9)),
            minutes=int(m.group(10) or 0),
            seconds=int(m.group(11) or 0),
        )
        tzinfo = _dt.timezone(sign * offset)
    elif tz_aware:
        tzinfo = _dt.timezone.utc
    return _dt.datetime(y, mo, d, h, mi, s, micro, tzinfo)


def _parse_time(text: str) -> _dt.time:
    parts = text.strip().split(":")
    hh = int(parts[0]); mm = int(parts[1]) if len(parts) > 1 else 0
    sec_part = parts[2] if len(parts) > 2 else "0"
    if "." in sec_part:
        sec, frac = sec_part.split(".", 1)
        return _dt.time(hh, mm, int(sec), int(frac.ljust(6, "0")[:6]))
    return _dt.time(hh, mm, int(float(sec_part)))


_INTERVAL_RE = re.compile(
    r"(?:(?P<years>[-+]?\d+) years?\s*)?"
    r"(?:(?P<mons>[-+]?\d+) mons?\s*)?"
    r"(?:(?P<days>[-+]?\d+) days?\s*)?"
    r"(?:(?P<sign>[-+])?(?P<h>\d+):(?P<m>\d{2}):(?P<s>\d{2}(?:\.\d+)?))?"
)


def _parse_interval(text: str) -> _dt.timedelta:
    m = _INTERVAL_RE.match(text.strip())
    if not m:
        return _dt.timedelta(0)
    days = int(m.group("days") or 0)
    days += int(m.group("mons") or 0) * 30
    days += int(m.group("years") or 0) * 365
    seconds = 0.0
    if m.group("h") is not None:
        seconds = int(m.group("h")) * 3600 + int(m.group("m")) * 60 + float(m.group("s"))
        if m.group("sign") == "-":
            seconds = -seconds
    return _dt.timedelta(days=days, seconds=seconds)


def _split_array(text: str) -> list[str | None]:
    """يفصل عناصر مصفوفة PostgreSQL بصيغتها النصية ``{a,b,"c,d"}``."""
    assert text.startswith("{") and text.endswith("}")
    body = text[1:-1]
    out: list[str | None] = []
    if not body:
        return out
    buf: list[str] = []
    in_quotes = False
    escape = False
    depth = 0
    for ch in body:
        if escape:
            buf.append(ch)
            escape = False
        elif ch == "\\":
            escape = True
        elif ch == '"':
            in_quotes = not in_quotes
        elif not in_quotes and ch == "{":
            depth += 1
            buf.append(ch)
        elif not in_quotes and ch == "}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and not in_quotes and depth == 0:
            token = "".join(buf)
            out.append(None if token == "NULL" else token)
            buf = []
        else:
            buf.append(ch)
    token = "".join(buf)
    out.append(None if token == "NULL" else token)
    return out


def decode_value(raw: bytes, oid: int) -> Any:
    """يفك قيمة عمود واحدة حسب OID نوعها."""
    if oid in _ARRAY_ELEMENT:
        element_oid = _ARRAY_ELEMENT[oid]
        text = raw.decode("utf-8")
        return [
            None if item is None else decode_value(item.encode("utf-8"), element_oid)
            for item in _split_array(text)
        ]
    if oid == BOOL:
        return raw == b"t"
    if oid in (INT2, INT4, INT8, OID):
        return int(raw)
    if oid in (FLOAT4, FLOAT8):
        return float(raw)
    if oid == NUMERIC:
        return Decimal(raw.decode("ascii"))
    if oid == BYTEA:
        text = raw.decode("ascii")
        return bytes.fromhex(text[2:]) if text.startswith("\\x") else raw
    if oid in (JSON, JSONB):
        return json.loads(raw.decode("utf-8"))
    if oid == UUID:
        return uuid.UUID(raw.decode("ascii"))
    if oid == DATE:
        return _dt.date.fromisoformat(raw.decode("ascii"))
    if oid == TIME:
        return _parse_time(raw.decode("ascii"))
    if oid == TIMESTAMP:
        return _parse_timestamp(raw.decode("ascii"), tz_aware=False)
    if oid == TIMESTAMPTZ:
        return _parse_timestamp(raw.decode("ascii"), tz_aware=True)
    if oid == INTERVAL:
        return _parse_interval(raw.decode("ascii"))
    return raw.decode("utf-8")
