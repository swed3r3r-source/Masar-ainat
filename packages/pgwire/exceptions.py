"""أخطاء طبقة قاعدة البيانات، مربوطة بأكواد SQLSTATE."""

from __future__ import annotations


class PgError(Exception):
    """خطأ عام قادم من الخادم أو من طبقة النقل."""

    def __init__(self, message: str, *, fields: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.fields: dict[str, str] = fields or {}

    @property
    def sqlstate(self) -> str | None:
        return self.fields.get("C")

    @property
    def detail(self) -> str | None:
        return self.fields.get("D")

    @property
    def constraint(self) -> str | None:
        return self.fields.get("n")

    @property
    def table(self) -> str | None:
        return self.fields.get("t")

    @property
    def message_primary(self) -> str:
        return self.fields.get("M", str(self))


class PgOperationalError(PgError):
    """مشكلة في الاتصال أو في تشغيل الخادم (خارجة عن نص الاستعلام)."""


class PgProgrammingError(PgError):
    """خطأ في نص الاستعلام أو في عدد المعاملات."""


class PgIntegrityError(PgError):
    """خرق لقيود التكامل."""


class UniqueViolation(PgIntegrityError):
    """23505 — خرق قيد التفرد (يستخدم لكشف الشحنات المكررة)."""


class ForeignKeyViolation(PgIntegrityError):
    """23503 — مرجع غير موجود."""


class CheckViolation(PgIntegrityError):
    """23514 — خرق قيد CHECK (مثل انتقال حالة غير مسموح)."""


class NotNullViolation(PgIntegrityError):
    """23502."""


class InsufficientPrivilege(PgError):
    """42501 — منع من سياسة RLS أو من GRANT."""


class InvalidTextRepresentation(PgProgrammingError):
    """22P02 / 22021 — قيمة لا تُحوَّل إلى نوع العمود، أو بايت غير صالح
    في الترميز (بايت صفري مدسوس في المسار مثلًا).

    مصدرها الغالب مُدخَل مستخدم لا خلل برمجي، فتُترجم في طبقة الـ API إلى
    خطأ تحقق ٤٠٠ لا إلى خطأ خادم ٥٠٠.
    """


class RaisedException(PgError):
    """P0001 — استثناء صريح من دالة PL/pgSQL (قواعد العمل في قاعدة البيانات)."""


class SerializationFailure(PgOperationalError):
    """40001 — تعارض تسلسل، قابل لإعادة المحاولة."""


class DeadlockDetected(PgOperationalError):
    """40P01 — قابل لإعادة المحاولة."""


_SQLSTATE_MAP: dict[str, type[PgError]] = {
    "23505": UniqueViolation,
    "23503": ForeignKeyViolation,
    "23514": CheckViolation,
    "23502": NotNullViolation,
    "42501": InsufficientPrivilege,
    "22P02": InvalidTextRepresentation,
    "22021": InvalidTextRepresentation,
    "P0001": RaisedException,
    "40001": SerializationFailure,
    "40P01": DeadlockDetected,
}

_CLASS_MAP: dict[str, type[PgError]] = {
    "23": PgIntegrityError,
    "42": PgProgrammingError,
    "08": PgOperationalError,
    "53": PgOperationalError,
    "57": PgOperationalError,
    "58": PgOperationalError,
}

RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})


def error_from_fields(fields: dict[str, str]) -> PgError:
    """يبني الاستثناء المناسب من حقول رسالة ErrorResponse."""
    code = fields.get("C", "")
    cls = _SQLSTATE_MAP.get(code) or _CLASS_MAP.get(code[:2], PgError)
    severity = fields.get("S") or fields.get("V") or "ERROR"
    message = fields.get("M", "unknown database error")
    detail = fields.get("D")
    text = f"[{code or '-----'}] {severity}: {message}"
    if detail:
        text += f" — {detail}"
    return cls(text, fields=fields)
