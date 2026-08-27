"""طبقة الوصول لقاعدة البيانات: التجميعة، سياق الجلسة الأمني، المعاملات.

**نقطة الاستبدال الوحيدة** بين ``pgwire`` (بيئة بلا وصول لمستودعات الحزم)
و``psycopg 3`` في الإنتاج. باقي التطبيق يتعامل مع ``get_pool()`` و
``session()`` فقط.
"""

from __future__ import annotations

import contextlib
import threading
from typing import Any, Iterator, Sequence

import pgwire
from masar_core.config import get_config
from masar_core.errors import Forbidden

Row = pgwire.connection.Row
Connection = pgwire.Connection

_pool: pgwire.Pool | None = None
_pool_lock = threading.Lock()

#: متغيرات سياق الجلسة التي تقرأها سياسات RLS
CONTEXT_KEYS = (
    "masar.user_id",
    "masar.role",
    "masar.hub_ids",
    "masar.region_ids",
    "masar.facility_id",
    "masar.driver_id",
    "masar.environment",
    "masar.change_reason",
    "masar.change_source",
    "masar.allow_hard_delete",
)


def get_pool(*, as_migrate: bool = False, reset: bool = False) -> pgwire.Pool:
    """يعيد تجميعة الاتصالات المشتركة."""
    global _pool
    cfg = get_config()
    if as_migrate:
        return pgwire.Pool(
            min_size=1, max_size=2,
            host=cfg.database.host, port=cfg.database.port,
            user=cfg.database.migrate_user, password=cfg.database.migrate_password,
            database=cfg.database.name, sslmode=cfg.database.sslmode,
            statement_timeout_ms=0,
            application_name="masar-migrate",
        )
    with _pool_lock:
        if _pool is None or reset:
            if _pool is not None:
                _pool.close()
            _pool = pgwire.Pool(
                min_size=cfg.database.pool_min,
                max_size=cfg.database.pool_max,
                host=cfg.database.host, port=cfg.database.port,
                user=cfg.database.user, password=cfg.database.password,
                database=cfg.database.name, sslmode=cfg.database.sslmode,
                statement_timeout_ms=cfg.database.statement_timeout_ms,
                application_name="masar-api",
            )
        return _pool


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


class SecurityContext:
    """سياق المستخدم الذي تُطبَّق عليه سياسات RLS.

    ``system`` يُستخدم للمهام الخلفية (المراقب، مستهلك الحرارة) ويعمل بنطاق
    وطني قراءةً وكتابةً — ولا يُشتق أبدًا من طلب مستخدم.
    """

    __slots__ = (
        "user_id", "role", "hub_ids", "region_ids", "facility_id", "driver_id",
        "allow_hard_delete", "change_reason", "change_source",
    )

    def __init__(
        self,
        *,
        user_id: str | None = None,
        role: str = "ANONYMOUS",
        hub_ids: Sequence[str] | None = None,
        region_ids: Sequence[str] | None = None,
        facility_id: str | None = None,
        driver_id: str | None = None,
        allow_hard_delete: bool = False,
        change_reason: str | None = None,
        change_source: str = "API",
    ) -> None:
        self.user_id = str(user_id) if user_id else None
        self.role = role
        self.hub_ids = [str(h) for h in (hub_ids or [])]
        self.region_ids = [str(r) for r in (region_ids or [])]
        self.facility_id = str(facility_id) if facility_id else None
        self.driver_id = str(driver_id) if driver_id else None
        self.allow_hard_delete = allow_hard_delete
        self.change_reason = change_reason
        self.change_source = change_source

    @classmethod
    def system(cls, source: str = "SYSTEM") -> "SecurityContext":
        return cls(
            user_id="00000000-0000-0000-0000-000000000001",
            role="ADMIN",
            change_source=source,
        )

    @classmethod
    def anonymous(cls) -> "SecurityContext":
        return cls()

    def as_settings(self) -> dict[str, str]:
        cfg = get_config()
        return {
            "masar.user_id": self.user_id or "",
            "masar.role": self.role,
            "masar.hub_ids": ",".join(self.hub_ids),
            "masar.region_ids": ",".join(self.region_ids),
            "masar.facility_id": self.facility_id or "",
            "masar.driver_id": self.driver_id or "",
            "masar.environment": cfg.environment,
            "masar.change_reason": self.change_reason or "",
            "masar.change_source": self.change_source,
            "masar.allow_hard_delete": "on" if self.allow_hard_delete else "off",
        }

    def require_hub(self, hub_id: str | None) -> None:
        """فحص نطاق في طبقة الخدمة — الطبقة الثانية بعد RLS."""
        if self.role in ("ADMIN", "CENTRAL_PLANNER", "CONTROL_TOWER", "AUDITOR"):
            return
        if hub_id is None or str(hub_id) not in self.hub_ids:
            raise Forbidden(
                "هذا المركز خارج نطاق صلاحياتك",
                hub_id=str(hub_id) if hub_id else None,
            )

    def __repr__(self) -> str:  # pragma: no cover
        return f"SecurityContext(role={self.role}, user={self.user_id}, hubs={len(self.hub_ids)})"


def apply_context(conn: Connection, context: SecurityContext) -> None:
    settings = context.as_settings()
    for key, value in settings.items():
        conn.execute("SELECT set_config($1, $2, false)", [key, value])


def clear_context(conn: Connection) -> None:
    for key in CONTEXT_KEYS:
        with contextlib.suppress(Exception):
            conn.execute("SELECT set_config($1, '', false)", [key])


@contextlib.contextmanager
def session(context: SecurityContext, *, readonly: bool = False) -> Iterator[Connection]:
    """اتصال بسياق أمني مطبَّق — بلا معاملة صريحة."""
    pool = get_pool()
    conn = pool.acquire()
    try:
        apply_context(conn, context)
        yield conn
    finally:
        with contextlib.suppress(Exception):
            clear_context(conn)
        pool.release(conn)


@contextlib.contextmanager
def transaction(
    context: SecurityContext, *, isolation: str | None = None, readonly: bool = False
) -> Iterator[Connection]:
    """معاملة ذرية بسياق أمني — تُستخدم لكل عملية حساسة (§28)."""
    pool = get_pool()
    conn = pool.acquire()
    try:
        apply_context(conn, context)
        with conn.transaction(isolation=isolation, read_only=readonly):
            yield conn
    finally:
        with contextlib.suppress(Exception):
            clear_context(conn)
        pool.release(conn)


# ------------------------------------------------------------ اختصارات ----

def fetch_all(context: SecurityContext, sql: str, params: Sequence[Any] | None = None) -> list[Row]:
    with session(context) as conn:
        return conn.fetch_all(sql, params)


def fetch_one(context: SecurityContext, sql: str, params: Sequence[Any] | None = None) -> Row | None:
    with session(context) as conn:
        return conn.fetch_one(sql, params)


def fetch_value(context: SecurityContext, sql: str, params: Sequence[Any] | None = None) -> Any:
    with session(context) as conn:
        return conn.fetch_value(sql, params)
