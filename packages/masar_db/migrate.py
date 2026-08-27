"""مُشغّل الترحيلات — يطبّق ملفات SQL بالترتيب ويزامن جداول القواعد.

الاستخدام::

    python3 -m masar_db.migrate up          # تطبيق الترحيلات الناقصة
    python3 -m masar_db.migrate status      # عرض الحالة
    python3 -m masar_db.migrate reset       # حذف المخطط وإعادة بنائه (تطوير/اختبار فقط)
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

import pgwire
from masar_core.config import get_config
from masar_core.state_machine import (
    PLAN_TRANSITIONS,
    ROUTE_TRANSITIONS,
    SHIPMENT_TRANSITIONS,
)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version      text PRIMARY KEY,
    checksum     text NOT NULL,
    applied_at   timestamptz NOT NULL DEFAULT now(),
    duration_ms  integer NOT NULL DEFAULT 0
);
"""


def _connect_migrate() -> pgwire.Connection:
    cfg = get_config().database
    return pgwire.connect(
        host=cfg.host, port=cfg.port,
        user=cfg.migrate_user, password=cfg.migrate_password,
        database=cfg.name, sslmode=cfg.sslmode,
        statement_timeout_ms=0,
        application_name="masar-migrate",
    )


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def sync_transition_tables(conn: pgwire.Connection) -> int:
    """يزامن ``allowed_transitions`` من مصدرها الوحيد في بايثون."""
    rows: list[tuple[str, str, str, str, bool, str]] = []
    for entity, table in (
        ("SHIPMENT", SHIPMENT_TRANSITIONS),
        ("ROUTE", ROUTE_TRANSITIONS),
        ("PLAN", PLAN_TRANSITIONS),
    ):
        for (source, target), transition in table.items():
            rows.append((
                entity, str(source), str(target), transition.permission,
                transition.requires_reason, transition.label_ar,
            ))

    conn.execute("DELETE FROM allowed_transitions")
    for row in rows:
        conn.execute(
            "INSERT INTO allowed_transitions "
            "(entity, from_status, to_status, permission, requires_reason, label_ar) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            list(row),
        )
    return len(rows)


def ensure_partitions(conn: pgwire.Connection, months_ahead: int = 3) -> None:
    """ينشئ أقسام جدول المواقع للشهر الحالي والأشهر القادمة."""
    conn.execute(
        """
        DO $$
        DECLARE i integer;
        BEGIN
            FOR i IN -1..%s LOOP
                PERFORM app.ensure_position_partition(
                    (date_trunc('month', now()) + (i || ' month')::interval)::date);
            END LOOP;
        END $$;
        """ % months_ahead
    )


def applied_versions(conn: pgwire.Connection) -> dict[str, str]:
    conn.execute_script(_BOOTSTRAP)
    return {
        row["version"]: row["checksum"]
        for row in conn.fetch_all("SELECT version, checksum FROM schema_migrations")
    }


def up(verbose: bool = True) -> list[str]:
    conn = _connect_migrate()
    applied: list[str] = []
    try:
        existing = applied_versions(conn)
        for path in _migration_files():
            version = path.stem
            checksum = _checksum(path)
            if version in existing:
                if existing[version] != checksum:
                    raise SystemExit(
                        f"⚠️  الترحيل {version} تغيّر بعد تطبيقه "
                        f"(البصمة {existing[version]} ← {checksum}). "
                        "أنشئ ترحيلًا جديدًا بدل تعديل ترحيل مطبَّق."
                    )
                continue
            started = time.monotonic()
            if verbose:
                print(f"▶ تطبيق {version} …")
            sql = path.read_text(encoding="utf-8")
            with conn.transaction():
                conn.execute_script(sql)
                elapsed = int((time.monotonic() - started) * 1000)
                conn.execute(
                    "INSERT INTO schema_migrations (version, checksum, duration_ms) "
                    "VALUES ($1, $2, $3)",
                    [version, checksum, elapsed],
                )
            applied.append(version)
            if verbose:
                print(f"  ✓ {version} خلال {elapsed} مللي ثانية")

        count = sync_transition_tables(conn)
        ensure_partitions(conn)
        if verbose:
            print(f"  ✓ زُوملت {count} قاعدة انتقال حالة إلى قاعدة البيانات")
            print(f"  ✓ أقسام جدول المواقع جاهزة")
        # منح الصلاحيات على أي كائنات أُنشئت بعد الترحيل 0005
        conn.execute_script(
            "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO masar_app;"
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO masar_app;"
            "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA app TO masar_app;"
            "REVOKE UPDATE, DELETE ON audit_log FROM masar_app;"
            "GRANT SELECT ON allowed_transitions TO masar_app;"
        )
    finally:
        conn.close()
    return applied


def status() -> None:
    conn = _connect_migrate()
    try:
        existing = applied_versions(conn)
        print(f"قاعدة البيانات: {get_config().database.name}")
        for path in _migration_files():
            mark = "✓" if path.stem in existing else "·"
            print(f"  {mark} {path.stem}")
        pending = [p.stem for p in _migration_files() if p.stem not in existing]
        print(f"\nالمطبَّق: {len(existing)} · المعلَّق: {len(pending)}")
    finally:
        conn.close()


def reset(verbose: bool = True) -> None:
    cfg = get_config()
    if cfg.is_production:
        raise SystemExit("reset ممنوع في بيئة الإنتاج")
    conn = _connect_migrate()
    try:
        if verbose:
            print(f"⟲ إعادة بناء المخطط في {cfg.database.name} …")
        conn.execute_script(
            "DROP SCHEMA IF EXISTS public CASCADE;"
            "DROP SCHEMA IF EXISTS app CASCADE;"
            "CREATE SCHEMA public;"
            f"GRANT ALL ON SCHEMA public TO {cfg.database.migrate_user};"
            "GRANT USAGE ON SCHEMA public TO PUBLIC;"
        )
    finally:
        conn.close()
    up(verbose=verbose)


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "up"
    if command == "up":
        applied = up()
        print(f"\nتم تطبيق {len(applied)} ترحيلًا جديدًا." if applied
              else "\nلا توجد ترحيلات معلّقة.")
    elif command == "status":
        status()
    elif command == "reset":
        reset()
        print("\nأُعيد بناء المخطط بالكامل.")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
