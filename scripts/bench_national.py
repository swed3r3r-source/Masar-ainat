"""قياس أداء التخطيط على حجم وطني — متتابعًا ومتوازيًا، على نفس المدخلات.

الغرض ليس رقمًا للعرض بل إجابة سؤالين:

1. هل التفكيك على المراكز يعطي **نفس النتيجة** التي يعطيها التشغيل المتتابع؟
   (لو اختلفت، فالتوازي غيّر الخطة لا سرّعها فقط — وذلك خلل لا تحسين.)
2. كم يكسب فعليًا على هذا العتاد؟

التشغيل::

    PYTHONPATH=packages python3 scripts/bench_national.py [عدد الشحنات لكل مركز-يوم]
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "scripts"))

import pgwire  # noqa: E402
from masar_core.config import get_config  # noqa: E402
from masar_db.driver import SecurityContext, get_pool  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=3))
INFO = "  ·"


def connect() -> pgwire.Connection:
    cfg = get_config().database
    return pgwire.connect(
        host=cfg.host, port=cfg.port, user=cfg.migrate_user,
        password=cfg.migrate_password, database=cfg.name, sslmode=cfg.sslmode,
        statement_timeout_ms=0, application_name="masar-bench",
    )


def build_schedule(per_hub_day: int, days: int) -> tuple[bytes, dt.datetime, list[str]]:
    """جدول موزّع على كل المراكز والأيام — الحجم الوطني الذي نقيس عليه."""
    from make_sample_schedule import HEADERS, _row, compute_base

    base = compute_base()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)

    conn = connect()
    try:
        rows = conn.fetch_all(
            "SELECT h.code AS hub_code, f.code AS facility_code, f.facility_type "
            "FROM facilities f JOIN hubs h ON h.id = f.default_hub_id "
            "WHERE f.is_active AND f.facility_type IN "
            "('HEALTH_CENTER','HOSPITAL','BLOOD_BANK') ORDER BY h.code, f.code")
        labs = conn.fetch_all(
            "SELECT h.code AS hub_code, f.code AS facility_code FROM facilities f "
            "JOIN hubs h ON h.id = f.default_hub_id "
            "WHERE f.is_active AND f.facility_type = 'LABORATORY' ORDER BY h.code")
    finally:
        conn.close()

    by_hub: dict[str, list] = {}
    for row in rows:
        by_hub.setdefault(row["hub_code"], []).append(row)
    lab_by_hub: dict[str, str] = {}
    for row in labs:
        lab_by_hub.setdefault(row["hub_code"], row["facility_code"])

    hubs = [hub for hub in by_hub if hub in lab_by_hub]
    counter = 0
    for day in range(days):
        for hub in hubs:
            pickups = by_hub[hub]
            for index in range(per_hub_day):
                source = pickups[index % len(pickups)]
                counter += 1
                # نوافذ متدرجة كل ٥ دقائق داخل نافذة عمل معقولة
                offset = (index % 96) * 5
                writer.writerow(_row(
                    f"BCH-{counter:06d}", base, day,
                    source["facility_code"], lab_by_hub[hub], hub, offset, 4))
    return ("﻿" + buffer.getvalue()).encode("utf-8"), base, hubs


def comparable(per_day: list[dict]) -> list[tuple]:
    """توقيع النتيجة الذي يجب أن يتطابق بين الوضعين."""
    return sorted(
        (row["hub_code"], row["service_date"], row["shipment_count"],
         row["route_count"], row["unplannable_count"], row["drivers_used"],
         round(float(row["total_distance_km"]), 3))
        for row in per_day
    )


def run(workers: int, hub_ids: list[str], dates: list[dt.date],
        import_id: str, context: SecurityContext) -> tuple[float, dict]:
    os.environ["MASAR_SOLVE_WORKERS"] = str(workers)
    get_config(reload=True)
    get_pool(reset=True)

    from masar_api.services import planning

    started = time.monotonic()
    result = planning.run_planning(
        context, hub_ids=hub_ids, dates=dates, import_id=import_id,
        plan_name=f"قياس أداء — {workers} عملية",
        time_limit_seconds=5.0, seed=7,
    )
    return time.monotonic() - started, result


def main() -> int:
    per_hub_day = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    from masar_api.services import imports as imports_service

    conn = connect()
    try:
        admin = conn.fetch_one(
            "SELECT id::text AS id FROM users WHERE email = 'admin@masar.test'")
        hub_rows = conn.fetch_all("SELECT id::text AS id, code FROM hubs ORDER BY code")
    finally:
        conn.close()
    if admin is None:
        print("❌ لا توجد بيانات تجريبية — شغّل scripts/seed.py أولًا")
        return 1

    context = SecurityContext(user_id=admin["id"], role="ADMIN")
    content, base, hub_codes = build_schedule(per_hub_day, days)
    hub_ids = [row["id"] for row in hub_rows if row["code"] in hub_codes]
    dates = [(base + dt.timedelta(days=offset)).date() for offset in range(days)]
    tasks = len(hub_ids) * len(dates)

    print("=" * 74)
    print("قياس أداء التخطيط على حجم وطني")
    print("=" * 74)
    print(f"{INFO} المراكز: {len(hub_ids)} · الأيام: {len(dates)} · "
          f"مسائل (مركز×يوم): {tasks}")
    print(f"{INFO} الشحنات لكل مسألة: {per_hub_day} · الإجمالي: "
          f"{per_hub_day * tasks}")
    print(f"{INFO} أنوية المعالج المتاحة: {os.cpu_count()}")

    from masar_api.services import storage

    key = storage.build_key("imports", "bench.csv", "text/csv").rsplit(".", 1)[0] + ".csv"
    storage.get_store().put(key, content, "text/csv")
    upload = imports_service.create_import(
        context, filename="bench.csv", content=content,
        content_type="text/csv", storage_key=key)
    import_id = upload["id"]
    validation = imports_service.validate_import(context, import_id)
    print(f"{INFO} صفوف صالحة: {validation['valid_rows']} من "
          f"{validation['total_rows']}")
    commit = imports_service.commit_import(context, import_id, skip_invalid=True)
    print(f"{INFO} شحنات مُنشأة: {commit['created_shipments']}")

    print("\n── تشغيل متتابع (عملية واحدة) ──")
    sequential_seconds, sequential = run(1, hub_ids, dates, import_id, context)
    print(f"  الزمن: {sequential_seconds:.1f} ث · الرحلات: "
          f"{sequential['metrics']['route_count']} · غير مخطط: "
          f"{sequential['metrics']['unplannable_count']}")

    print("\n── تشغيل متوازٍ ──")
    workers = min(os.cpu_count() or 1, tasks)
    parallel_seconds, parallel = run(workers, hub_ids, dates, import_id, context)
    print(f"  العمليات: {workers} · الزمن: {parallel_seconds:.1f} ث · الرحلات: "
          f"{parallel['metrics']['route_count']} · غير مخطط: "
          f"{parallel['metrics']['unplannable_count']}")

    print("\n" + "=" * 74)
    identical = comparable(sequential["per_day"]) == comparable(parallel["per_day"])
    speedup = sequential_seconds / max(parallel_seconds, 1e-9)
    print(f"تطابق النتيجة بين الوضعين: {'✅ نعم' if identical else '❌ لا'}")
    print(f"التسريع: ×{speedup:.2f} على {workers} عملية "
          f"({sequential_seconds:.1f} ث ← {parallel_seconds:.1f} ث)")
    if not identical:
        print("  الفروق:")
        for left, right in zip(comparable(sequential["per_day"]),
                               comparable(parallel["per_day"])):
            if left != right:
                print(f"    متتابع {left}\n    متوازٍ  {right}")
    print("=" * 74)
    return 0 if identical else 1


if __name__ == "__main__":
    raise SystemExit(main())
