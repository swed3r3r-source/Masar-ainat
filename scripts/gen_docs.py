"""يولّد وثائق المرحلة الثانية **من الشيفرة وقاعدة البيانات الحيّة**.

السبب في التوليد بدل الكتابة اليدوية مبدئي لا كسلًا: وثيقة مكتوبة بيد تتقادم
بصمت وتصبح ادّعاءً عن نظام لم يعد موجودًا. ما يُولَّد هنا (المخطط، آلات
الحالة، مرجع الـ API، مصفوفة الصلاحيات، فهرس الإعدادات) يُقرأ من المصدر
الحقيقي في كل مرة، فإن تغيّر النظام تغيّرت الوثيقة أو انكشف الفارق.

    PYTHONPATH=packages python3 scripts/gen_docs.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT))

import pgwire  # noqa: E402
from masar_core.config import get_config  # noqa: E402
from masar_core.operational_settings import SETTING_SPECS  # noqa: E402
from masar_core.permissions import PERMISSIONS, matrix_rows  # noqa: E402
from masar_core.constants import Role  # noqa: E402
from masar_core.state_machine import (  # noqa: E402
    export_diagram, plan_sm, route_sm, shipment_sm,
)

DOCS = ROOT / "docs"
STAMP = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

ROLE_LABELS = {
    Role.ADMIN: "مدير", Role.CENTRAL_PLANNER: "تخطيط", Role.HUB_SUPERVISOR: "مشرف",
    Role.CONTROL_TOWER: "برج", Role.DRIVER: "سائق",
    Role.EXTERNAL_REQUESTER: "مقدم طلب", Role.AUDITOR: "مدقق",
    Role.INTEGRATION: "تكامل",
}

HEADER = (
    "> **وثيقة مُولَّدة آليًا** من الشيفرة وقاعدة البيانات في {stamp}.\n"
    "> لا تُحرَّر يدويًا: أعد توليدها بـ `PYTHONPATH=packages python3 "
    "scripts/gen_docs.py`.\n"
)


def connect() -> pgwire.Connection:
    cfg = get_config().database
    return pgwire.connect(
        host=cfg.host, port=cfg.port, user=cfg.migrate_user,
        password=cfg.migrate_password, database=cfg.name, sslmode=cfg.sslmode,
        statement_timeout_ms=0, application_name="masar-docs",
    )


# ============================================ ٠٣: نموذج البيانات ============

TABLE_GROUPS: dict[str, tuple[str, ...]] = {
    "الهيكل التنظيمي والبيانات الرئيسية": (
        "regions", "cities", "hubs", "facilities", "drivers", "vehicles", "boxes",
        "sensors", "availability_exceptions",
    ),
    "المستخدمون والأمان": (
        "users", "user_scopes", "user_sessions", "api_clients", "audit_log",
    ),
    "الإعدادات": ("operational_settings",),
    "الاستيراد والتخطيط": (
        "schedule_imports", "import_rows", "shipments", "plans", "plan_days",
        "routes", "route_stops", "plan_warnings", "driver_estimations",
        "route_revisions",
    ),
    "التشغيل والتتبع": (
        "shipment_events", "shipment_status_history", "documents",
        "shipment_exceptions", "alerts", "driver_positions", "driver_last_position",
        "temperature_readings", "temperature_breaches", "custody_transfers",
        "system_events",
    ),
    "قواعد الانتقال": ("allowed_transitions",),
}


def generate_data_model(conn: pgwire.Connection) -> str:
    columns = conn.fetch_all(
        """
        SELECT c.table_name, c.column_name, c.data_type, c.is_nullable,
               c.column_default
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_name = c.table_name AND t.table_schema = c.table_schema
        WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
        ORDER BY c.table_name, c.ordinal_position
        """
    )
    foreign_keys = conn.fetch_all(
        """
        SELECT tc.table_name AS source, kcu.column_name AS source_column,
               ccu.table_name AS target
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_name = tc.constraint_name
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
        ORDER BY tc.table_name, kcu.column_name
        """
    )
    checks = conn.fetch_all(
        """
        SELECT rel.relname AS table_name, con.conname AS name,
               pg_get_constraintdef(con.oid) AS definition
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = 'public' AND con.contype IN ('c', 'x')
        ORDER BY rel.relname, con.conname
        """
    )
    triggers = conn.fetch_all(
        """
        SELECT rel.relname AS table_name, tg.tgname AS name,
               pg_get_triggerdef(tg.oid) AS definition
        FROM pg_trigger tg
        JOIN pg_class rel ON rel.oid = tg.tgrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE NOT tg.tgisinternal AND ns.nspname = 'public'
        ORDER BY rel.relname, tg.tgname
        """
    )
    partitioned = {
        row["table_name"] for row in conn.fetch_all(
            "SELECT c.relname AS table_name FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'p' AND n.nspname = 'public'")
    }

    by_table: dict[str, list] = {}
    for row in columns:
        by_table.setdefault(row["table_name"], []).append(row)

    grouped = {name: [t for t in tables if t in by_table]
               for name, tables in TABLE_GROUPS.items()}
    listed = {t for tables in grouped.values() for t in tables}
    others = sorted(set(by_table) - listed)
    if others:
        grouped["جداول أخرى"] = others

    lines = ["# ٣) نموذج البيانات — الجداول والعلاقات والقيود", "",
             HEADER.format(stamp=STAMP), ""]
    lines += [
        "## المبدأ",
        "",
        "القيود التشغيلية لا تُترك للتطبيق وحده. ما يمكن التعبير عنه كقيد في",
        "قاعدة البيانات يُكتب قيدًا: `CHECK` للثوابت المنطقية، `EXCLUDE` لمنع",
        "تعارض إسناد السائق أو المركبة، ومحفّزات (triggers) لحراسة الانتقالات",
        "والحذف. هذا يعني أن أي مسار برمجي — حتى لو أخطأ — لا يستطيع كتابة حالة",
        "غير مشروعة.",
        "",
        f"**الإجمالي:** {len(by_table)} جدولًا · {len(foreign_keys)} مفتاحًا خارجيًا · "
        f"{len(checks)} قيد تحقق/استبعاد · {len(triggers)} محفّزًا.",
        "",
    ]

    # ---- مخطط العلاقات (Mermaid)
    lines += ["## مخطط العلاقات (ERD)", "",
              "العلاقات المفتاحية فقط — المخطط الكامل في جداول الأعمدة أدناه.", "",
              "```mermaid", "erDiagram"]
    core = {
        "regions", "cities", "hubs", "facilities", "users", "drivers", "vehicles",
        "boxes", "shipments", "plans", "plan_days", "routes", "route_stops",
        "schedule_imports", "import_rows", "shipment_events", "alerts",
        "shipment_exceptions", "documents", "temperature_readings", "audit_log",
        "driver_positions", "plan_warnings",
    }
    seen_pairs: set[tuple[str, str]] = set()
    for fk in foreign_keys:
        source, target = fk["source"], fk["target"]
        if source not in core or target not in core or source == target:
            continue
        pair = (target, source)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        lines.append(f'    {target} ||--o{{ {source} : "{fk["source_column"]}"')
    lines += ["```", ""]

    # ---- الجداول
    for group, tables in grouped.items():
        lines += [f"## {group}", ""]
        for table in tables:
            table_fks = {fk["source_column"]: fk["target"]
                         for fk in foreign_keys if fk["source"] == table}
            suffix = " · **مقسّم بالمدى (partitioned)**" if table in partitioned else ""
            lines += [f"### `{table}`{suffix}", "",
                      "| العمود | النوع | إلزامي | يشير إلى | الافتراضي |",
                      "|---|---|---|---|---|"]
            for column in by_table[table]:
                default = (column["column_default"] or "")
                default = default.split("::")[0][:34]
                lines.append(
                    f"| `{column['column_name']}` | {column['data_type']} | "
                    f"{'نعم' if column['is_nullable'] == 'NO' else 'لا'} | "
                    f"{'`' + table_fks[column['column_name']] + '`' if column['column_name'] in table_fks else '—'} | "
                    f"{'`' + default + '`' if default else '—'} |"
                )
            table_checks = [c for c in checks if c["table_name"] == table]
            if table_checks:
                lines += ["", "**القيود:**", ""]
                for check in table_checks:
                    lines.append(f"- `{check['name']}` — `{check['definition'][:210]}`")
            table_triggers = [t for t in triggers if t["table_name"] == table]
            if table_triggers:
                lines += ["", "**المحفّزات:**", ""]
                for trigger in table_triggers:
                    action = trigger["definition"].split(" EXECUTE FUNCTION ")[-1]
                    when = trigger["definition"].split(" ON ")[0].split("TRIGGER ")[-1]
                    lines.append(f"- `{trigger['name']}` — {when} ⇒ `{action}`")
            lines.append("")
    return "\n".join(lines)


def generate_rls(conn: pgwire.Connection) -> str:
    policies = conn.fetch_all(
        "SELECT tablename, policyname, cmd, qual, with_check, "
        "obj_description(('public.' || quote_ident(tablename))::regclass) AS table_comment "
        "FROM pg_policies WHERE schemaname = 'public' "
        "ORDER BY tablename, policyname"
    )
    helpers = conn.fetch_all(
        "SELECT p.proname AS name, pg_get_function_result(p.oid) AS returns, "
        "obj_description(p.oid) AS comment "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'app' ORDER BY p.proname"
    )
    rls_tables = conn.fetch_all(
        "SELECT c.relname AS table_name FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relrowsecurity ORDER BY c.relname"
    )

    lines = ["# ٦-أ) أمن الصفوف (RLS) — الطبقة التي لا يمكن تجاوزها", "",
             HEADER.format(stamp=STAMP), "",
             "الصلاحية تُطبَّق في **ثلاث طبقات**: إخفاء في الواجهة (راحة استخدام)،",
             "و`require(permission)` في الخادم، و**سياسات صفوف في قاعدة البيانات**.",
             "الطبقة الثالثة هي الضمانة: حتى لو أخطأ استعلام في الخادم، لا يعيد",
             "المحرك صفًا خارج نطاق المستخدم.", "",
             f"**{len(rls_tables)} جدولًا** مفعّل عليها RLS، بـ **{len(policies)} سياسة**.",
             "",
             "## دوال السياق (`app`)", "",
             "تقرأ متغيرات الجلسة التي يضبطها الخادم عند كل اتصال:", "",
             "| الدالة | تعيد | الغرض |", "|---|---|---|"]
    for helper in helpers:
        comment = (helper["comment"] or "").replace("\n", " ")[:90]
        lines.append(f"| `app.{helper['name']}()` | `{helper['returns']}` | {comment or '—'} |")

    lines += ["", "## السياسات", ""]
    current = None
    for policy in policies:
        if policy["tablename"] != current:
            current = policy["tablename"]
            lines += [f"### `{current}`", ""]
        expression = (policy["qual"] or policy["with_check"] or "true")
        expression = " ".join(expression.split())
        lines += [f"- **{policy['policyname']}** (`{policy['cmd']}`)",
                  f"  ```sql\n  {expression[:520]}\n  ```"]
    lines.append("")
    return "\n".join(lines)


# ============================================ ٠٤: آلات الحالة ===============

def generate_state_machines() -> str:
    lines = ["# ٤) آلات الحالة — الشحنة والرحلة والخطة", "",
             HEADER.format(stamp=STAMP), "",
             "الانتقالات ليست شرطًا في الكود فحسب: الجدول نفسه مُزامَن إلى",
             "`allowed_transitions` في قاعدة البيانات، ومحفّزات `guard_*_transition`",
             "ترفض أي تحديث حالة خارج الجدول. مصدر الحقيقة واحد",
             "(`masar_core/state_machine.py`) والقاعدة تُلزم به.", ""]
    for machine, title, note in (
        (shipment_sm, "الشحنة", "١٨ حالة. الخروج من `EXCEPTION` يتطلب سببًا مكتوبًا "
                                "دائمًا، ولا تُحذف شحنة أبدًا (§19)."),
        (route_sm, "الرحلة", "سحب النشر وإزالة السائق والإلغاء كلها تتطلب سببًا."),
        (plan_sm, "الخطة", "`FAILED` ليست نهاية الطريق — يمكن إعادة المحاولة."),
    ):
        transitions = machine.table
        needs_reason = sum(1 for t in transitions.values() if t.requires_reason)
        lines += [f"## {title}", "", note, "",
                  f"**{len(transitions)} انتقالًا**، منها **{needs_reason}** يتطلب سببًا مكتوبًا.",
                  "", "```mermaid", export_diagram(machine), "```", "",
                  "| من | إلى | الصلاحية | يتطلب سببًا | الوصف |", "|---|---|---|---|---|"]
        for (source, target), transition in sorted(transitions.items()):
            lines.append(
                f"| `{source}` | `{target}` | `{transition.permission}` | "
                f"{'نعم' if transition.requires_reason else 'لا'} | "
                f"{transition.label_ar} |")
        lines.append("")

    lines += ["## قواعد عبر-كيانية", "",
              "قواعد لا تعبّر عنها آلة حالة واحدة، وتُفحص في الخادم وفي القاعدة:", "",
              "- **`assert_delivery_after_pickup`** — لا تسليم قبل التقاط، ولا زمن",
              "  تسليم أسبق من زمن الالتقاط (قيد `CHECK` مرافق على `shipments`).",
              "- **`assert_route_completable`** — لا تكتمل رحلة وفيها شحنة غير محسومة.",
              "- **`assert_can_cancel_before_pickup`** — بعد الالتقاط لا يوجد «إلغاء",
              "  قبل الالتقاط»، بل مسار استثناء.",
              "- **`assert_route_startable`** — لا يبدأ السائق رحلة غير منشورة ولا",
              "  قبل تاريخها.", ""]
    return "\n".join(lines)


# ============================================ ٠٥: مرجع الـ API ==============

def generate_api_reference() -> str:
    from masar_api.routes import API_ROUTES, PUBLIC_PATHS

    lines = ["# ٥) مرجع واجهة البرمجة (API)", "",
             HEADER.format(stamp=STAMP), "",
             "## الاتفاقيات", "",
             "- كل استجابة ناجحة: `{\"ok\": true, \"data\": ...}`.",
             "- كل خطأ: `{\"ok\": false, \"error\": {\"code\", \"message\", \"details\"}}`",
             "  برسالة عربية صالحة للعرض المباشر.",
             "- المصادقة: `Authorization: Bearer <access_token>` أو ملف تعريف ارتباط",
             "  `masar_access` (HttpOnly). التجديد عبر `/api/auth/refresh` مع تدوير",
             "  رمز التحديث وكشف إعادة الاستخدام.",
             "- الإجراءات الحساسة تتطلب حقل `reason` غير فارغ، وإلا `REASON_REQUIRED`.",
             "- التوقيت: كل الطوابع `timestamptz` بصيغة ISO-8601 (UTC)، والعرض",
             "  بتوقيت `Asia/Riyadh`.", "",
             f"**{len(API_ROUTES)} مسارًا.**", "",
             "| المسار | الطرق | الصلاحية المطلوبة |", "|---|---|---|"]

    for route in API_ROUTES:
        methods = ",".join(m for m in route.methods if m not in ("HEAD", "OPTIONS"))
        endpoint = route.endpoint
        permissions = list(getattr(endpoint, "__masar_permissions__", ()) or ())
        if route.path in PUBLIC_PATHS:
            required = "عام (بلا مصادقة)"
        elif permissions:
            required = " + ".join(f"`{key}`" for key in permissions)
        else:
            required = "مصادقة فقط"
        lines.append(f"| `{route.path}` | {methods} | {required} |")

    lines += ["", "## رموز الأخطاء", "",
              "| الرمز | HTTP | متى يظهر |", "|---|---|---|"]
    from masar_core import errors as error_module

    for name in dir(error_module):
        candidate = getattr(error_module, name)
        if (isinstance(candidate, type) and issubclass(candidate, error_module.MasarError)
                and candidate is not error_module.MasarError):
            doc = (candidate.__doc__ or "").strip().split("\n")[0]
            lines.append(f"| `{candidate.code}` | {candidate.http_status} | {doc or name} |")
    lines.append("")
    return "\n".join(lines)


# ============================== ٠٦: الصلاحيات والإعدادات ====================

def generate_permissions_and_settings() -> str:
    rows = matrix_rows()
    roles = list(Role)
    lines = ["# ٦) مصفوفة الصلاحيات وفهرس الإعدادات التشغيلية", "",
             HEADER.format(stamp=STAMP), "",
             "## مصفوفة الصلاحيات", "",
             f"{len(PERMISSIONS)} صلاحية موزّعة على {len(roles)} أدوار.",
             "العمود «سبب» يعني أن تنفيذ الإجراء يستلزم سببًا مكتوبًا يُحفظ في",
             "سجل التدقيق.", "",
             "| الصلاحية | المجموعة | سبب | " +
             " | ".join(ROLE_LABELS[r] for r in roles) + " |",
             "|---|---|---|" + "---|" * len(roles)]
    for row in rows:
        marks = " | ".join("✅" if row[r.value] else "—" for r in roles)
        lines.append(
            f"| `{row['key']}` — {row['name_ar']} | {row['group']} | "
            f"{'✓' if row['requires_reason'] else '—'} | {marks} |")

    lines += ["", "### حدود النطاق لكل دور", "",
              "الصلاحية تفتح الشاشة، و**النطاق** يحدد الصفوف. النطاق مطبَّق في",
              "سياسات RLS لا في الاستعلامات:", "",
              "| الدور | النطاق |", "|---|---|",
              "| مدير النظام / التخطيط المركزي / برج التحكم / المدقق | المملكة كاملة |",
              "| مشرف مركز الانطلاق | مراكزه المسندة فقط |",
              "| السائق | رحلاته المسندة والمنشورة فقط |",
              "| مقدم الطلب الخارجي | طلبات جهته فقط + جهات التسليم الممكنة |",
              "| التكامل | ما تسمح به بيانات اعتماد العميل |", ""]

    lines += ["## فهرس الإعدادات التشغيلية", "",
              "لا قيمة تشغيلية مكتوبة في الشيفرة (§2). كل قيمة أدناه لها نوع ومدى",
              "ووحدة ووصف، وتُحلّ هرميًا بمبدأ **الأخص يفوز**:",
              "`المملكة ← المنطقة ← المدينة ← مركز الانطلاق`.", ""]
    groups: dict[str, list] = {}
    for spec in SETTING_SPECS:
        groups.setdefault(spec.group_ar, []).append(spec)
    for group, specs in groups.items():
        lines += [f"### {group}", "",
                  "| المفتاح | الاسم | النوع | الافتراضي | المدى | الوصف |",
                  "|---|---|---|---|---|---|"]
        for spec in specs:
            bounds = "—"
            if spec.minimum is not None or spec.maximum is not None:
                bounds = f"{spec.minimum} … {spec.maximum}"
            elif spec.choices:
                bounds = " / ".join(spec.choices)
            unit = f" {spec.unit_ar}" if spec.unit_ar else ""
            lines.append(
                f"| `{spec.key}` | {spec.name_ar} | {spec.kind} | "
                f"`{spec.default}`{unit} | {bounds} | {spec.description_ar} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    written: list[tuple[str, int]] = []

    def write(name: str, content: str) -> None:
        path = DOCS / name
        path.write_text(content, encoding="utf-8")
        written.append((name, len(content.encode("utf-8"))))

    conn = connect()
    try:
        write("03-data-model.md", generate_data_model(conn))
        write("06a-row-level-security.md", generate_rls(conn))
    finally:
        conn.close()

    write("04-state-machines.md", generate_state_machines())
    write("05-api-reference.md", generate_api_reference())
    write("06-permissions-and-settings.md", generate_permissions_and_settings())

    print("الوثائق المُولَّدة:")
    for name, size in written:
        print(f"  ✅ docs/{name} ({size:,} بايت)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
