"""أخطاء مجال العمل — كل خطأ يحمل رمزًا ثابتًا ورسالة عربية وحالة HTTP."""

from __future__ import annotations

from typing import Any


class MasarError(Exception):
    code = "MASAR_ERROR"
    http_status = 400

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(MasarError):
    code = "VALIDATION_ERROR"
    http_status = 422


class NotFound(MasarError):
    code = "NOT_FOUND"
    http_status = 404


class Conflict(MasarError):
    code = "CONFLICT"
    http_status = 409


class InvalidTransition(Conflict):
    code = "INVALID_TRANSITION"
    http_status = 409


class Unauthorized(MasarError):
    code = "UNAUTHORIZED"
    http_status = 401


class Forbidden(MasarError):
    code = "FORBIDDEN"
    http_status = 403


class OutOfScope(Forbidden):
    """المستخدم مصرّح بالعملية لكن الكائن خارج نطاقه (مركز/جهة/سائق آخر)."""

    code = "OUT_OF_SCOPE"


class ReasonRequired(MasarError):
    code = "REASON_REQUIRED"
    http_status = 422


class RateLimited(MasarError):
    code = "RATE_LIMITED"
    http_status = 429


class DependencyUnavailable(MasarError):
    """خدمة خارجية (طرق/تخزين/محرك) غير متاحة — لا يُخفى الخطأ."""

    code = "DEPENDENCY_UNAVAILABLE"
    http_status = 503


class OptimizationFailed(MasarError):
    code = "OPTIMIZATION_FAILED"
    http_status = 500


class FeasibilityViolation(MasarError):
    """خرق قيد صلب اكتُشف في فحص ما بعد الحل — يمنع النشر."""

    code = "FEASIBILITY_VIOLATION"
    http_status = 409
