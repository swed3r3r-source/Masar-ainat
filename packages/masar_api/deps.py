"""التبعيات المشتركة للموجّهات: المصادقة، الصلاحيات، النطاق، تحديد المعدل."""

from __future__ import annotations

import functools
from typing import Any, Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from masar_core.config import get_config
from masar_core.errors import Forbidden, MasarError, RateLimited, Unauthorized
from masar_core.permissions import requires_reason as permission_requires_reason
from masar_core.security import decode_jwt, rate_limiter
from masar_db.driver import SecurityContext

from .http import client_ip, error_from
from .services.auth import AuthenticatedUser, load_user_by_id

Handler = Callable[[Request], Awaitable[Response]]


def current_user(request: Request) -> AuthenticatedUser:
    user = getattr(request.state, "user", None)
    if user is None:
        raise Unauthorized("يلزم تسجيل الدخول")
    return user


def optional_user(request: Request) -> AuthenticatedUser | None:
    return getattr(request.state, "user", None)


def context_of(request: Request, **kwargs: Any) -> SecurityContext:
    """يبني سياق RLS من المستخدم الحالي — لا يُبنى أبدًا من مدخلات الطلب."""
    return current_user(request).to_context(**kwargs)


async def authenticate_request(request: Request) -> AuthenticatedUser | None:
    """يقرأ رمز الوصول من الترويسة أو من كوكي آمنة."""
    header = request.headers.get("authorization", "")
    token: str | None = None
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
    if not token:
        token = request.cookies.get("masar_access")
    if not token:
        return None

    payload = decode_jwt(token)
    if payload.get("typ") != "access":
        raise Unauthorized("نوع الرمز غير صالح لهذه العملية")
    user = load_user_by_id(payload["sub"], payload.get("sid"))
    return user


def require(*permissions: str, any_of: bool = False) -> Callable[[Handler], Handler]:
    """يفرض صلاحية في **الخادم** — الطبقة الثانية بعد RLS.

    كذلك يفرض وجود سبب مكتوب إن كانت الصلاحية تقتضيه.
    """

    def decorator(handler: Handler) -> Handler:
        @functools.wraps(handler)
        async def wrapper(request: Request) -> Response:
            user = current_user(request)
            granted = (
                any(user.can(p) for p in permissions) if any_of
                else all(user.can(p) for p in permissions)
            )
            if not granted:
                missing = [p for p in permissions if not user.can(p)]
                raise Forbidden(
                    "لا تملك الصلاحية اللازمة لهذه العملية",
                    required=list(permissions), missing=missing,
                )
            request.state.required_permissions = list(permissions)
            request.state.requires_reason = any(
                permission_requires_reason(p) for p in permissions)
            return await handler(request)

        wrapper.__masar_permissions__ = list(permissions)  # type: ignore[attr-defined]
        return wrapper

    return decorator


def rate_limit(limit: int | None = None, *, key: str = "default") -> Callable[[Handler], Handler]:
    def decorator(handler: Handler) -> Handler:
        @functools.wraps(handler)
        async def wrapper(request: Request) -> Response:
            cfg = get_config().security
            effective = limit or cfg.rate_limit_per_minute
            user = optional_user(request)
            identity = user.user_id if user else (client_ip(request) or "anonymous")
            bucket = f"{key}:{identity}"
            if not rate_limiter.allow(bucket, effective):
                raise RateLimited(
                    "تجاوزت الحد المسموح من الطلبات. أعد المحاولة بعد قليل.",
                    retry_after=rate_limiter.retry_after(bucket),
                )
            return await handler(request)

        return wrapper

    return decorator


def handle_errors(handler: Handler) -> Handler:
    """يحوّل أخطاء المجال إلى استجابات JSON دون إخفاء السبب (§34)."""

    @functools.wraps(handler)
    async def wrapper(request: Request) -> Response:
        try:
            return await handler(request)
        except MasarError as exc:
            return error_from(exc)

    return wrapper
