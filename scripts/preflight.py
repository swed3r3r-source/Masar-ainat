"""فحص جاهزية الإقلاع — يمنع تشغيل إنتاج بإعداد ناقص أو ضعيف.

يُشغَّل قبل بدء الخدمة (``ExecStartPre`` في وحدة systemd). الفلسفة: **الفشل
عند الإقلاع أرخص من الفشل أثناء التشغيل**. إعداد ضعيف يمر بصمت يتحوّل بعد
أسابيع إلى حادثة أمنية أو خطة مبنية على أزمنة تقديرية.

    PYTHONPATH=packages python3 scripts/preflight.py [--strict]

يعيد ٠ إن كان كل شيء سليمًا، و١ عند وجود مانع.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

PASS, FAIL, WARN = "✅", "❌", "⚠️ "


def main() -> int:
    from masar_core.config import _env_bool, get_config

    cfg = get_config()
    blockers: list[str] = []
    warnings: list[str] = []

    print("=" * 72)
    print(f"فحص جاهزية الإقلاع — البيئة: {cfg.environment}")
    print("=" * 72)

    # ------------------------------------------------ إعداد أساسي (§29) ----
    blockers.extend(cfg.validate())

    # ------------------------------------------------ الاتصال بقاعدة البيانات
    try:
        import pgwire

        conn = pgwire.connect(
            host=cfg.database.host, port=cfg.database.port,
            user=cfg.database.user, password=cfg.database.password,
            database=cfg.database.name, sslmode=cfg.database.sslmode,
            application_name="masar-preflight",
        )
        try:
            version = conn.fetch_value("SELECT version()")
            print(f"{PASS} الاتصال بقاعدة البيانات — {str(version)[:40]}")

            applied = conn.fetch_value(
                "SELECT count(*) FROM schema_migrations") or 0
            files = len(list(
                (ROOT / "packages" / "masar_db" / "migrations").glob("*.sql")))
            if int(applied) < files:
                blockers.append(
                    f"ترحيلات معلّقة: مطبَّق {applied} من {files} — "
                    "شغّل python3 -m masar_db.migrate up")
            else:
                print(f"{PASS} الترحيلات مكتملة ({applied}/{files})")

            policies = int(conn.fetch_value(
                "SELECT count(*) FROM pg_policies WHERE schemaname = 'public'") or 0)
            if policies < 50:
                blockers.append(
                    f"سياسات RLS ناقصة ({policies}) — الوصول غير محمي على مستوى الصف")
            else:
                print(f"{PASS} سياسات أمن الصفوف مفعّلة ({policies} سياسة)")

            if cfg.is_production:
                test_rows = int(conn.fetch_value(
                    "SELECT count(*) FROM shipments WHERE is_test_data") or 0)
                if test_rows:
                    blockers.append(
                        f"{test_rows} شحنة اختبار في قاعدة الإنتاج (§31)")
        finally:
            conn.close()
    except Exception as exc:
        blockers.append(f"تعذر الاتصال بقاعدة البيانات: {exc}")

    # ----------------------------------------------------- التشفير والتخزين
    try:
        from masar_api.services import storage

        status = storage.storage_status()
        encryption = status["encryption"]

        # البوابة الأولى: هل التنفيذ نفسه معتمد في هذه البيئة؟ يسبق سؤال
        # «هل التشفير مفعّل»، لأن تشفيرًا بتنفيذ غير معتمد ليس تشفيرًا مقبولًا.
        if encryption["blocked"]:
            blockers.append(encryption["message_ar"])
        elif not encryption["production_grade"]:
            warnings.append(
                f"التنفيذ الاحتياطي (ChaCha20-Poly1305) مستخدم في بيئة "
                f"«{encryption['environment']}» — مقبول هنا فقط. "
                + (encryption.get("review_note_ar") or ""))

        # البوابة الثانية: هل التشفير مفعّل فعلًا على المخزن؟
        if status["encrypted_at_rest"]:
            print(f"{PASS} التشفير عند التخزين مفعّل — "
                  f"{encryption['algorithm']} عبر {encryption['implementation']}")
        elif cfg.is_production:
            blockers.append(
                "التشفير عند التخزين غير مفعّل — بيانات صحية حساسة تُحفظ كما هي. "
                "اضبط MASAR_ENCRYPTION_KEYS")
        else:
            warnings.append("التشفير عند التخزين غير مفعّل (مقبول خارج الإنتاج)")

        # البوابة الثالثة: مخرج الترحيل الاستثنائي يجب ألا يبقى مفتوحًا
        if (not cfg.environment in ("development", "test")
                and _env_bool("MASAR_ALLOW_LEGACY_FALLBACK_DECRYPT")):
            blockers.append(
                "MASAR_ALLOW_LEGACY_FALLBACK_DECRYPT مفعّل — هذا مخرج ترحيل "
                "لمرة واحدة، وتركه مفتوحًا يعني قبول محتوى مشفَّر بتنفيذ غير "
                "معتمد. أعد تشفير المحتوى ثم أزل الضبط.")
    except Exception as exc:
        blockers.append(f"فحص التخزين فشل: {exc}")

    # ------------------------------------------------------- مزوّد الطرق ---
    # «مضبوط» ليس «يعمل»: نستدعي الخدمة على ساق مرجعية مقيسة بدل الاكتفاء
    # بقراءة اسم المزوّد من الإعداد.
    try:
        from masar_api.services.routing_status import provider_status as routing_status

        status = routing_status()
        if status["provider"] == "haversine":
            (blockers if cfg.is_production else warnings).append(status["message_ar"])
        elif not status.get("reachable"):
            blockers.append(f"مزوّد الطرق {status['provider']}: {status['message_ar']}")
        elif not status.get("map_verified"):
            # يستجيب لكن بأرقام بعيدة عن المرجع — الأرجح خريطة خاطئة.
            blockers.append(f"مزوّد الطرق {status['provider']}: {status['message_ar']}")
        else:
            reference = status["reference"]
            print(f"{PASS} مزوّد الطرق: {status['provider']} — "
                  f"الساق المرجعية {reference['actual_km']} كم "
                  f"(فارق {reference['drift_percent']}٪ عن المقيس) · "
                  f"{status.get('latency_ms')} مل.ث")
    except Exception as exc:  # noqa: BLE001
        blockers.append(f"تعذر فحص مزوّد الطرق: {exc}")

    # -------------------------------------------------- النسخ الاحتياطي ----
    try:
        import pgwire

        conn = pgwire.connect(
            host=cfg.database.host, port=cfg.database.port,
            user=cfg.database.migrate_user, password=cfg.database.migrate_password,
            database=cfg.database.name, sslmode=cfg.database.sslmode,
            application_name="masar-preflight",
        )
        try:
            archive_mode = conn.fetch_value(
                "SELECT current_setting('archive_mode', true)")
            failed = conn.fetch_value(
                "SELECT failed_count FROM pg_stat_archiver")
            if archive_mode == "on" and int(failed or 0) == 0:
                print(f"{PASS} أرشفة WAL تعمل — الاستعادة إلى لحظة محددة ممكنة")
            elif archive_mode == "on":
                blockers.append(f"فشلت أرشفة {failed} مقطع WAL — هدف RPO غير مضمون")
            else:
                message = ("أرشفة WAL غير مفعّلة — RPO = فترة النسخة الكاملة "
                           "لا خمس دقائق")
                (blockers if cfg.is_production else warnings).append(message)
        finally:
            conn.close()
    except Exception as exc:
        warnings.append(f"تعذر فحص أرشفة WAL: {exc}")

    # ------------------------------------------------------- التكاملات -----
    for label, module, key in (
        ("الحرارة", "masar_api.services.temperature", "provider_status"),
        ("الإشعارات", "masar_api.services.notifications", "provider_status"),
    ):
        try:
            module_object = __import__(module, fromlist=[key])
            status = getattr(module_object, key)()
            mark = PASS if status.get("is_real_integration") else WARN
            print(f"{mark} {label}: {status.get('message_ar', '')}")
            if not status.get("is_real_integration"):
                warnings.append(f"{label}: {status.get('message_ar', '')}")
        except Exception as exc:
            warnings.append(f"تعذر فحص {label}: {exc}")

    # ------------------------------------------------------------ الخلاصة --
    print("\n" + "=" * 72)
    if warnings:
        print(f"{WARN} تنبيهات ({len(warnings)}) — لا تمنع الإقلاع:")
        for item in warnings:
            print(f"    · {item}")
    if blockers:
        print(f"\n{FAIL} موانع الإقلاع ({len(blockers)}):")
        for item in blockers:
            print(f"    · {item}")
        print("\nالخدمة لن تبدأ حتى تُعالَج الموانع.")
        return 1
    print(f"\n{PASS} الإعداد جاهز للتشغيل")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
