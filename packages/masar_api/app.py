"""تجميع تطبيق ASGI: الوسائط، المسارات، المهام الخلفية، الملفات الثابتة."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from pathlib import Path

import pgwire
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from masar_core.config import get_config
from masar_core.errors import MasarError, RateLimited, Unauthorized
from masar_core.security import rate_limiter
from masar_db.driver import close_pool, get_pool

from .deps import authenticate_request
from .http import client_ip, error
from .routes import API_ROUTES, PUBLIC_PATHS
from .services import alerts as alerts_service
from .services import events as events_service
from .services import temperature as temperature_service

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"

logger = logging.getLogger("masar")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """يعطي كل طلب معرّفًا ويسجّل زمنه ويحوّل أخطاء المجال إلى JSON."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.monotonic()
        try:
            response = await call_next(request)
        except MasarError as exc:
            response = error(exc.message, code=exc.code, status=exc.http_status,
                             **exc.details)
        except pgwire.InvalidTextRepresentation:
            # مُدخَل مستخدم لا يُحوَّل إلى نوع العمود — أشهر حالاته معرّف في
            # المسار ليس UUID. كان يصل إلى معالج الخطأ العام فيُنتج ٥٠٠، وهو
            # تصنيف خاطئ: الطلب غير صالح لا الخادم معطوب. الفرق ليس تجميليًا —
            # ٥٠٠ يُخفي خطأ المستخدم ويشوّش مراقبة الأعطال الحقيقية.
            logger.info("مُدخَل غير صالح [%s] %s %s",
                        request_id, request.method, request.url.path)
            response = error(
                "أحد المعرّفات أو القيم المُرسلة غير صالح",
                code="VALIDATION_ERROR", status=422,
            )
        except Exception:
            logger.exception("خطأ غير معالج [%s] %s %s",
                             request_id, request.method, request.url.path)
            response = error(
                "حدث خطأ داخلي في الخادم. رقم الطلب للمتابعة: " + request_id,
                code="INTERNAL_ERROR", status=500, request_id=request_id,
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        response.headers["x-request-id"] = request_id
        response.headers["x-response-time-ms"] = str(duration_ms)
        if request.url.path.startswith("/api/") and duration_ms > 1500:
            logger.warning("طلب بطيء [%s] %s %s — %d مللي ثانية",
                           request_id, request.method, request.url.path, duration_ms)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("x-content-type-options", "nosniff")
        response.headers.setdefault("x-frame-options", "DENY")
        response.headers.setdefault("referrer-policy", "same-origin")
        response.headers.setdefault(
            "content-security-policy",
            "default-src 'self'; img-src 'self' data: blob: https:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
            "form-action 'self'",
        )
        response.headers.setdefault(
            "permissions-policy", "geolocation=(self), camera=(self), microphone=()")
        if get_config().is_production:
            response.headers.setdefault(
                "strict-transport-security", "max-age=31536000; includeSubDomains")
        # لا نعلن اسم المكدّس وإصداره: معرفة الخادم بالضبط تختصر على المهاجم
        # خطوة الاستطلاع وتوجّهه إلى ثغرات إصدار بعينه. الترويسة يضيفها خادم
        # ASGI بعد خروج الاستجابة من الوسائط، فيُطفأ توليدها عند التشغيل
        # (``--no-server-header``) وتُضبط هنا قيمة محايدة للحالات الأخرى.
        del response.headers["server"]
        response.headers["server"] = "masar"
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    """يصادق كل طلب API ويطبّق حدًا عامًا للمعدل."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        request.state.user = None

        if path.startswith("/api/"):
            cfg = get_config().security
            identity = client_ip(request) or "anonymous"
            if not rate_limiter.allow(f"global:{identity}", cfg.rate_limit_per_minute):
                return error(
                    "تجاوزت الحد المسموح من الطلبات",
                    code="RATE_LIMITED", status=429,
                    retry_after=rate_limiter.retry_after(f"global:{identity}"),
                )
            if path not in PUBLIC_PATHS:
                try:
                    user = await authenticate_request(request)
                except Unauthorized as exc:
                    return error(exc.message, code=exc.code, status=401)
                if user is None:
                    return error("يلزم تسجيل الدخول", code="UNAUTHORIZED", status=401)
                request.state.user = user
            else:
                with contextlib.suppress(Exception):
                    request.state.user = await authenticate_request(request)

        return await call_next(request)


# -------------------------------------------------------- صفحات الواجهة ----

def _page(filename: str):
    async def handler(request: Request) -> Response:
        path = WEB_ROOT / filename
        if not path.exists():
            return PlainTextResponse("الصفحة غير موجودة", status_code=404)
        return FileResponse(path, media_type="text/html; charset=utf-8")

    return handler


async def service_worker(request: Request) -> Response:
    path = WEB_ROOT / "sw.js"
    if not path.exists():
        return PlainTextResponse("", status_code=404)
    return FileResponse(
        path, media_type="application/javascript; charset=utf-8",
        headers={"service-worker-allowed": "/", "cache-control": "no-cache"},
    )


async def manifest(request: Request) -> Response:
    path = WEB_ROOT / "manifest.webmanifest"
    return FileResponse(path, media_type="application/manifest+json")


# ------------------------------------------------------- المهام الخلفية ----

async def _alert_scanner() -> None:
    """يفحص التنبيهات التشغيلية دوريًا (§24)."""
    await asyncio.sleep(10)
    while True:
        try:
            from starlette.concurrency import run_in_threadpool

            await run_in_threadpool(alerts_service.scan_operational_alerts)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("فشل فحص التنبيهات الدوري")
        await asyncio.sleep(60)


async def _notification_worker() -> None:
    """عامل صندوق الصادر: يسحب المعلّق ويحاول الإرسال بإعادة محاولة متصاعدة.

    منفصل عن مسار الطلب عمدًا: تعطّل مزوّد الرسائل يؤخّر الإشعار ولا يُسقط
    أي عملية تشغيلية.
    """
    from .services import notifications as notifications_service

    if get_config().notifications.provider == "none":
        logger.info("لا مزوّد إشعارات مُعدّ — عامل الإرسال لم يبدأ")
        return
    await asyncio.sleep(20)
    while True:
        try:
            from starlette.concurrency import run_in_threadpool

            result = await run_in_threadpool(notifications_service.deliver_pending)
            if result.get("failed"):
                logger.warning("فشل إرسال %d إشعارًا", result["failed"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("فشل عامل الإشعارات")
        await asyncio.sleep(30)


async def _temperature_poller() -> None:
    cfg = get_config().temperature
    if cfg.provider == "none":
        return
    await asyncio.sleep(15)
    while True:
        try:
            from starlette.concurrency import run_in_threadpool

            await run_in_threadpool(temperature_service.poll_provider)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("فشل سحب قراءات الحرارة")
        await asyncio.sleep(60)


def create_app(*, start_background: bool = True) -> Starlette:
    cfg = get_config()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )

    problems = cfg.validate()
    if problems:
        message = "مشاكل في إعداد البيئة:\n  - " + "\n  - ".join(problems)
        if cfg.is_production:
            raise SystemExit(message)
        logger.warning(message)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        tasks: list[asyncio.Task] = []
        get_pool()
        events_service.bus.start()
        if start_background:
            tasks.append(asyncio.create_task(_alert_scanner()))
            tasks.append(asyncio.create_task(_temperature_poller()))
            tasks.append(asyncio.create_task(_notification_worker()))
        logger.info("بدأ خادم مسار عينات — البيئة %s", cfg.environment)
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            events_service.bus.stop()
            close_pool()
            logger.info("أُغلق خادم مسار عينات")

    routes = [
        *API_ROUTES,
        Route("/sw.js", service_worker, methods=["GET"]),
        Route("/manifest.webmanifest", manifest, methods=["GET"]),
        Route("/", _page("index.html"), methods=["GET"]),
        Route("/login", _page("index.html"), methods=["GET"]),
        Route("/driver", _page("driver.html"), methods=["GET"]),
        Route("/request", _page("requester.html"), methods=["GET"]),
        # اسم الملف نفسه: بدونه يبتلعه الالتقاط الشامل أدناه فيعيد واجهة
        # المكتب بصمت لمن كتب /driver.html — شاشة فارغة بلا رسالة خطأ.
        Route("/driver.html", _page("driver.html"), methods=["GET"]),
        Route("/requester.html", _page("requester.html"), methods=["GET"]),
        Mount("/static", app=StaticFiles(directory=str(WEB_ROOT / "static")),
              name="static"),
        Route("/{path:path}", _page("index.html"), methods=["GET"]),
    ]

    return Starlette(
        debug=not cfg.is_production,
        routes=routes,
        middleware=[
            Middleware(RequestContextMiddleware),
            Middleware(SecurityHeadersMiddleware),
            Middleware(AuthMiddleware),
        ],
        lifespan=lifespan,
    )


app = create_app()
