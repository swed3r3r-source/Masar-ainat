"""الإعدادات والأسرار — تُقرأ من البيئة فقط، ولا يوجد سر واحد داخل الكود.

قاعدة أمنية (§29): أي مفتاح API أو كلمة مرور تُقرأ هنا ولا تُمرَّر أبدًا إلى
طبقة الواجهة. ``public_config()`` هي الدالة الوحيدة المسموح بإرسال ناتجها
إلى المتصفح، وهي تُبنى بقائمة بيضاء صريحة.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    return value if value not in (None, "") else default


#: البيئات المعروفة. أي قيمة أخرى تُعامل معاملة الإنتاج احتياطًا — الخطأ في
#: اتجاه التشدد يُكلّف رسالة رفض، والخطأ في الاتجاه الآخر يُكلّف تسريبًا.
KNOWN_ENVIRONMENTS = ("development", "test", "staging", "production")


def _resolve_environment() -> str:
    """يحسم البيئة من ``MASAR_ENV`` و``APP_ENV`` معًا.

    القاعدة: إن ذكر أيّ منهما ``production`` فالبيئة إنتاج. تجاهل أحدهما
    يعني خادمًا يظن نفسه في التطوير وهو في الإنتاج، فتُلغى كل البوابات بصمت.
    """
    values = [(_env("MASAR_ENV") or "").strip().lower(),
              (_env("APP_ENV") or "").strip().lower()]
    named = [value for value in values if value]
    if not named:
        return "development"
    if "production" in named:
        return "production"
    if "staging" in named:
        return "staging"
    return named[0]


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    try:
        return float(raw) if raw is not None else default
    except ValueError:
        return default


@dataclass(slots=True)
class DatabaseConfig:
    host: str = field(default_factory=lambda: _env("MASAR_DB_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("MASAR_DB_PORT", 5432))
    name: str = field(default_factory=lambda: _env("MASAR_DB_NAME", "masar_dev"))
    user: str = field(default_factory=lambda: _env("MASAR_DB_USER", "masar_app"))
    password: str | None = field(default_factory=lambda: _env("MASAR_DB_PASSWORD"))
    sslmode: str = field(default_factory=lambda: _env("MASAR_DB_SSLMODE", "prefer"))
    pool_min: int = field(default_factory=lambda: _env_int("MASAR_DB_POOL_MIN", 1))
    pool_max: int = field(default_factory=lambda: _env_int("MASAR_DB_POOL_MAX", 10))
    statement_timeout_ms: int = field(
        default_factory=lambda: _env_int("MASAR_DB_STATEMENT_TIMEOUT_MS", 30_000)
    )
    #: دور الترحيلات — يملك الجداول ويتجاوز RLS بحكم الملكية
    migrate_user: str = field(
        default_factory=lambda: _env("MASAR_DB_MIGRATE_USER", "masar_migrate")
    )
    migrate_password: str | None = field(
        default_factory=lambda: _env("MASAR_DB_MIGRATE_PASSWORD")
    )


@dataclass(slots=True)
class SecurityConfig:
    #: سر توقيع JWT — إلزامي في الإنتاج
    jwt_secret: str = field(
        default_factory=lambda: _env("MASAR_JWT_SECRET") or secrets.token_urlsafe(48)
    )
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = field(
        default_factory=lambda: _env_int("MASAR_ACCESS_TOKEN_MINUTES", 30)
    )
    refresh_token_days: int = field(
        default_factory=lambda: _env_int("MASAR_REFRESH_TOKEN_DAYS", 14)
    )
    #: مدة الخمول قبل انتهاء الجلسة
    idle_timeout_minutes: int = field(
        default_factory=lambda: _env_int("MASAR_IDLE_TIMEOUT_MINUTES", 120)
    )
    max_login_attempts: int = field(
        default_factory=lambda: _env_int("MASAR_MAX_LOGIN_ATTEMPTS", 5)
    )
    lockout_minutes: int = field(
        default_factory=lambda: _env_int("MASAR_LOCKOUT_MINUTES", 15)
    )
    scrypt_n: int = field(default_factory=lambda: _env_int("MASAR_SCRYPT_N", 16384))
    scrypt_r: int = field(default_factory=lambda: _env_int("MASAR_SCRYPT_R", 8))
    scrypt_p: int = field(default_factory=lambda: _env_int("MASAR_SCRYPT_P", 1))
    rate_limit_per_minute: int = field(
        default_factory=lambda: _env_int("MASAR_RATE_LIMIT_PER_MINUTE", 240)
    )
    login_rate_limit_per_minute: int = field(
        default_factory=lambda: _env_int("MASAR_LOGIN_RATE_LIMIT_PER_MINUTE", 10)
    )
    max_upload_bytes: int = field(
        default_factory=lambda: _env_int("MASAR_MAX_UPLOAD_BYTES", 25 * 1024 * 1024)
    )
    allowed_document_types: tuple[str, ...] = (
        "image/jpeg", "image/png", "image/webp", "application/pdf",
    )
    allowed_import_types: tuple[str, ...] = (
        "text/csv", "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@dataclass(slots=True)
class RoutingConfig:
    #: haversine | osrm | matrix_file
    provider: str = field(
        default_factory=lambda: _env("MASAR_ROUTING_PROVIDER", "haversine")
    )
    osrm_base_url: str | None = field(default_factory=lambda: _env("MASAR_OSRM_URL"))
    #: عدد العقد في كتلة مصفوفة واحدة. إحداثيات OSRM تُرسل في مسار الرابط،
    #: فطلب غير مقسَّم لخطة وطنية ينتج رابطًا يتجاوز حد الخادم. ارفعه إن رفعت
    #: حدود الرابط عندك، واخفضه إن ظهرت أخطاء 414 أو TooBig.
    osrm_block_size: int = field(
        default_factory=lambda: int(_env_float("MASAR_OSRM_BLOCK_SIZE", 100))
    )
    matrix_file: str | None = field(default_factory=lambda: _env("MASAR_MATRIX_FILE"))
    request_timeout_seconds: float = field(
        default_factory=lambda: _env_float("MASAR_ROUTING_TIMEOUT", 20.0)
    )
    #: معامل التفافية الطريق مقابل الخط المستقيم (يُستخدم في المزوّد التقديري فقط)
    detour_factor: float = field(
        default_factory=lambda: _env_float("MASAR_DETOUR_FACTOR", 1.35)
    )
    #: سرعات افتراضية بالكيلومتر/ساعة للمزوّد التقديري
    urban_speed_kmh: float = field(
        default_factory=lambda: _env_float("MASAR_URBAN_SPEED_KMH", 32.0)
    )
    intercity_speed_kmh: float = field(
        default_factory=lambda: _env_float("MASAR_INTERCITY_SPEED_KMH", 88.0)
    )
    #: عتبة المسافة التي يُعتبر ما فوقها سفرًا بين المدن (كم)
    intercity_threshold_km: float = field(
        default_factory=lambda: _env_float("MASAR_INTERCITY_THRESHOLD_KM", 40.0)
    )
    #: عنوان بلاطات الخريطة في الواجهة (فارغ ⇒ شبكة إحداثيات بلا بلاطات)
    tile_url: str = field(
        default_factory=lambda: _env("MASAR_TILE_URL", "")
    )
    tile_attribution: str = field(
        default_factory=lambda: _env("MASAR_TILE_ATTRIBUTION", "")
    )


@dataclass(slots=True)
class StorageConfig:
    #: local | s3
    backend: str = field(default_factory=lambda: _env("MASAR_STORAGE_BACKEND", "local"))
    local_path: str = field(
        default_factory=lambda: _env("MASAR_STORAGE_PATH", str(REPO_ROOT / "var" / "storage"))
    )
    s3_endpoint: str | None = field(default_factory=lambda: _env("MASAR_S3_ENDPOINT"))
    s3_bucket: str | None = field(default_factory=lambda: _env("MASAR_S3_BUCKET"))
    s3_access_key: str | None = field(default_factory=lambda: _env("MASAR_S3_ACCESS_KEY"))
    s3_secret_key: str | None = field(default_factory=lambda: _env("MASAR_S3_SECRET_KEY"))
    s3_region: str = field(default_factory=lambda: _env("MASAR_S3_REGION", "me-central-1"))


@dataclass(slots=True)
class OptimizerConfig:
    #: inprocess | http
    mode: str = field(default_factory=lambda: _env("MASAR_OPTIMIZER_MODE", "inprocess"))
    service_url: str | None = field(default_factory=lambda: _env("MASAR_OPTIMIZER_URL"))
    #: native_alns | ortools
    backend: str = field(
        default_factory=lambda: _env("MASAR_OPTIMIZER_BACKEND", "native_alns")
    )
    time_limit_seconds: float = field(
        default_factory=lambda: _env_float("MASAR_SOLVE_TIME_LIMIT", 25.0)
    )
    #: حد عدد العقد الذي يُستخدم تحته الحل المضبوط للتحقق
    exact_node_limit: int = field(
        default_factory=lambda: _env_int("MASAR_EXACT_NODE_LIMIT", 9)
    )
    random_seed: int = field(default_factory=lambda: _env_int("MASAR_SOLVE_SEED", 20260826))
    workers: int = field(default_factory=lambda: _env_int("MASAR_SOLVE_WORKERS", 1))


@dataclass(slots=True)
class NotificationsConfig:
    """إعداد الإشعارات الخارجية. الافتراضي ``none``: لا إرسال ولا ادّعاء إرسال."""

    #: none | log | smtp | http_sms
    provider: str = field(
        default_factory=lambda: _env("MASAR_NOTIFY_PROVIDER", "none")
    )
    request_timeout_seconds: float = field(
        default_factory=lambda: _env_float("MASAR_NOTIFY_TIMEOUT", 10.0)
    )
    sender_name: str | None = field(
        default_factory=lambda: _env("MASAR_NOTIFY_SENDER_NAME", "MASAR")
    )
    sender_email: str | None = field(
        default_factory=lambda: _env("MASAR_NOTIFY_SENDER_EMAIL")
    )
    smtp_host: str | None = field(default_factory=lambda: _env("MASAR_SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: _env_int("MASAR_SMTP_PORT", 587))
    smtp_username: str | None = field(
        default_factory=lambda: _env("MASAR_SMTP_USERNAME"))
    smtp_password: str | None = field(
        default_factory=lambda: _env("MASAR_SMTP_PASSWORD"))
    smtp_use_tls: bool = field(
        default_factory=lambda: _env_bool("MASAR_SMTP_TLS", True))
    sms_url: str | None = field(default_factory=lambda: _env("MASAR_SMS_URL"))
    sms_api_key: str | None = field(default_factory=lambda: _env("MASAR_SMS_API_KEY"))


@dataclass(slots=True)
class TemperatureConfig:
    #: none | simulation | http
    provider: str = field(
        default_factory=lambda: _env("MASAR_TEMPERATURE_PROVIDER", "none")
    )
    simulation_enabled: bool = field(
        default_factory=lambda: _env_bool("MASAR_TEMPERATURE_SIMULATION", False)
    )
    ingest_url: str | None = field(default_factory=lambda: _env("MASAR_TEMPERATURE_URL"))
    ingest_api_key: str | None = field(
        default_factory=lambda: _env("MASAR_TEMPERATURE_API_KEY")
    )
    #: بعد كم ثانية تُعد القراءة قديمة
    stale_after_seconds: int = field(
        default_factory=lambda: _env_int("MASAR_TEMPERATURE_STALE_SECONDS", 900)
    )


@dataclass(slots=True)
class Config:
    # ``APP_ENV`` مرادف مقبول لـ``MASAR_ENV``: أدوات النشر والحاويات تضبط
    # الأول عادةً، وقراءة أحدهما دون الآخر تجعل خادمًا يظن نفسه في التطوير
    # وهو في الإنتاج — وهي أخطر حالة إعداد ممكنة، لأن كل البوابات الإنتاجية
    # تُلغى بصمت. عند تعارضهما نُغلّب **الأشد** (production) ولا نخمّن.
    environment: str = field(
        default_factory=lambda: _resolve_environment()
    )
    timezone: str = field(default_factory=lambda: _env("MASAR_TZ", "Asia/Riyadh"))
    default_locale: str = "ar"
    host: str = field(default_factory=lambda: _env("MASAR_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("MASAR_PORT", 8080))
    base_url: str = field(default_factory=lambda: _env("MASAR_BASE_URL", ""))
    #: مسار رفع الملفات المؤقت
    upload_dir: str = field(
        default_factory=lambda: _env("MASAR_UPLOAD_DIR", str(REPO_ROOT / "var" / "uploads"))
    )
    log_dir: str = field(
        default_factory=lambda: _env("MASAR_LOG_DIR", str(REPO_ROOT / "var" / "logs"))
    )
    log_level: str = field(default_factory=lambda: _env("MASAR_LOG_LEVEL", "INFO"))
    #: يسمح بوجود بيانات موسومة كتجريبية — يُمنع في الإنتاج
    allow_test_data: bool = field(
        default_factory=lambda: _env_bool("MASAR_ALLOW_TEST_DATA", True)
    )
    tracking_stale_seconds: int = field(
        default_factory=lambda: _env_int("MASAR_TRACKING_STALE_SECONDS", 180)
    )

    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    optimizer: OptimizerConfig = field(default_factory=OptimizerConfig)
    temperature: TemperatureConfig = field(default_factory=TemperatureConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_test(self) -> bool:
        return self.environment == "test"

    def validate(self) -> list[str]:
        """يعيد قائمة مشاكل الإعداد. في الإنتاج يجب أن تكون فارغة."""
        problems: list[str] = []
        if self.is_production:
            # وجود المفتاح لا يكفي: أخطر تسريب هو نشر الإنتاج بمفتاح التطوير
            # نفسه، وهو مكتوب في المستودع ومعروف لكل من قرأه. مفتاح مسرَّب
            # يعني أن أي شخص يصنع رمز دخول بدور مدير النظام.
            secret = _env("MASAR_JWT_SECRET") or ""
            weak_markers = ("dev", "test", "change", "example", "secret",
                            "default", "sample", "demo")
            if not secret:
                problems.append("MASAR_JWT_SECRET غير محدد — يمنع تشغيل الإنتاج")
            elif len(secret) < 32:
                problems.append(
                    f"MASAR_JWT_SECRET قصير ({len(secret)} حرفًا) — "
                    "الحد الأدنى ٣٢ حرفًا عشوائيًا"
                )
            elif any(marker in secret.lower() for marker in weak_markers):
                problems.append(
                    "MASAR_JWT_SECRET يبدو مفتاح تطوير أو قيمة نموذجية — "
                    "ولّد مفتاحًا عشوائيًا: openssl rand -base64 48"
                )
            if len(set(secret)) < 8 and secret:
                problems.append("MASAR_JWT_SECRET منخفض العشوائية")

            if not self.database.password:
                problems.append("MASAR_DB_PASSWORD غير محدد")
            elif len(self.database.password) < 16:
                problems.append("MASAR_DB_PASSWORD أقصر من ١٦ حرفًا")
            if self.database.sslmode not in ("require", "verify-full"):
                problems.append("يجب أن يكون MASAR_DB_SSLMODE=require أو verify-full")
            if self.storage.backend == "local":
                problems.append("تخزين محلي غير مسموح في الإنتاج — استخدم S3 متوافق")
            if self.allow_test_data:
                problems.append("MASAR_ALLOW_TEST_DATA يجب أن يكون false في الإنتاج")
            if self.routing.provider == "haversine":
                problems.append(
                    "مزوّد الطرق التقديري (haversine) غير مسموح في الإنتاج — "
                    "استخدم osrm أو مزوّدًا تجاريًا"
                )
            if self.temperature.simulation_enabled:
                problems.append("محاكاة الحرارة يجب أن تكون معطلة في الإنتاج")
        return problems

    def public_config(self) -> dict[str, object]:
        """القائمة البيضاء الوحيدة المسموح إرسالها للمتصفح. لا أسرار هنا."""
        return {
            "environment": self.environment,
            "timezone": self.timezone,
            "locale": self.default_locale,
            "tile_url": self.routing.tile_url,
            "tile_attribution": self.routing.tile_attribution,
            "routing_provider": self.routing.provider,
            "routing_is_estimated": self.routing.provider == "haversine",
            "temperature_provider": self.temperature.provider,
            "temperature_is_simulated": self.temperature.simulation_enabled,
            "tracking_stale_seconds": self.tracking_stale_seconds,
            "max_upload_bytes": self.security.max_upload_bytes,
            "idle_timeout_minutes": self.security.idle_timeout_minutes,
        }


_config: Config | None = None


def get_config(reload: bool = False) -> Config:
    global _config
    if _config is None or reload:
        _config = Config()
    return _config
