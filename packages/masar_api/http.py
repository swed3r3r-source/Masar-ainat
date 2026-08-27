"""أدوات HTTP مشتركة: الاستجابات، الأخطاء، التحقق من المدخلات."""

from __future__ import annotations

import datetime as dt
import decimal
import json
import uuid
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from masar_core.errors import MasarError, ValidationError


class ArabicJSONResponse(JSONResponse):
    """استجابة JSON تحفظ الحروف العربية كما هي (بلا هروب \\uXXXX)."""

    media_type = "application/json; charset=utf-8"

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content, ensure_ascii=False, allow_nan=False, default=_default,
            separators=(",", ":"),
        ).encode("utf-8")


def _default(obj: Any) -> Any:
    if isinstance(obj, (dt.datetime,)):
        return obj.astimezone(dt.timezone.utc).isoformat()
    if isinstance(obj, (dt.date, dt.time)):
        return obj.isoformat()
    if isinstance(obj, dt.timedelta):
        return obj.total_seconds()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=str)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    if hasattr(obj, "__slots__"):
        return {slot: getattr(obj, slot, None) for slot in obj.__slots__}
    raise TypeError(f"غير قابل للتحويل إلى JSON: {type(obj).__name__}")


def ok(data: Any = None, *, status: int = 200, **extra: Any) -> Response:
    payload: dict[str, Any] = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return ArabicJSONResponse(payload, status_code=status)


def created(data: Any = None, **extra: Any) -> Response:
    return ok(data, status=201, **extra)


def error(message: str, *, code: str = "ERROR", status: int = 400, **details: Any) -> Response:
    payload: dict[str, Any] = {"ok": False, "error": {"code": code, "message": message}}
    if details:
        payload["error"]["details"] = details
    return ArabicJSONResponse(payload, status_code=status)


def error_from(exc: MasarError) -> Response:
    return ArabicJSONResponse(
        {"ok": False, "error": exc.to_dict()}, status_code=exc.http_status
    )


def paginated(items: list[Any], *, total: int, page: int, page_size: int, **extra: Any) -> Response:
    return ArabicJSONResponse({
        "ok": True,
        "data": items,
        "pagination": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, -(-total // page_size)) if page_size else 1,
        },
        **extra,
    })


# ---------------------------------------------------- قراءة المدخلات ------

async def read_json(request: Request) -> dict[str, Any]:
    try:
        body = await request.body()
    except Exception:
        raise ValidationError("تعذر قراءة جسم الطلب") from None
    if not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValidationError("جسم الطلب ليس JSON صالحًا") from None
    if not isinstance(payload, dict):
        raise ValidationError("جسم الطلب يجب أن يكون كائن JSON")
    return payload


def require_fields(payload: dict[str, Any], *fields: str) -> None:
    missing = [field for field in fields if payload.get(field) in (None, "")]
    if missing:
        raise ValidationError(
            "حقول مطلوبة ناقصة: " + "، ".join(missing), missing_fields=missing
        )


def get_uuid(payload: dict[str, Any], field: str, *, required: bool = True) -> str | None:
    value = payload.get(field)
    if value in (None, ""):
        if required:
            raise ValidationError(f"الحقل {field} مطلوب")
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise ValidationError(f"الحقل {field} ليس معرّفًا صالحًا") from None


def get_int(payload: dict[str, Any], field: str, default: int | None = None,
            *, minimum: int | None = None, maximum: int | None = None) -> int | None:
    value = payload.get(field, default)
    if value is None:
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"الحقل {field} يجب أن يكون عددًا صحيحًا") from None
    if minimum is not None and result < minimum:
        raise ValidationError(f"الحقل {field} أقل من {minimum}")
    if maximum is not None and result > maximum:
        raise ValidationError(f"الحقل {field} أكبر من {maximum}")
    return result


def get_float(payload: dict[str, Any], field: str, default: float | None = None) -> float | None:
    value = payload.get(field, default)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"الحقل {field} يجب أن يكون رقمًا") from None


def get_date(payload: dict[str, Any], field: str, *, required: bool = True) -> dt.date | None:
    value = payload.get(field)
    if value in (None, ""):
        if required:
            raise ValidationError(f"الحقل {field} مطلوب")
        return None
    from masar_core.timeutil import parse_date

    return parse_date(value, field=field)


def get_datetime(payload: dict[str, Any], field: str, *, required: bool = True) -> dt.datetime | None:
    value = payload.get(field)
    if value in (None, ""):
        if required:
            raise ValidationError(f"الحقل {field} مطلوب")
        return None
    from masar_core.timeutil import parse_datetime

    return parse_datetime(value, field=field)


def get_reason(payload: dict[str, Any], *, minimum_length: int = 3) -> str:
    """يقرأ سبب العملية ويفرض وجوده — تُستخدم مع كل عملية تتطلب سببًا (§27)."""
    from masar_core.errors import ReasonRequired

    reason = str(payload.get("reason") or "").strip()
    if len(reason) < minimum_length:
        raise ReasonRequired(
            f"هذه العملية تتطلب سببًا مكتوبًا لا يقل عن {minimum_length} أحرف"
        )
    return reason


def query_int(request: Request, key: str, default: int, *, minimum: int = 0,
              maximum: int = 10_000) -> int:
    raw = request.query_params.get(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def query_list(request: Request, key: str) -> list[str]:
    raw = request.query_params.get(key)
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
