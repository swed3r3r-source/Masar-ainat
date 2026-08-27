"""يبني أرشيف تصدير كامل للمشروع — **بعد فحص آلي يمنع تسريب أي سر**.

الفحص ليس تجميلًا: أرشيف يُرسل خارج البيئة لا يُراجَع بالعين. لذلك يمر كل ملف
مرشَّح على قائمة أنماط، وأي مطابقة **توقف البناء** ولا تُحذف بصمت — الحذف
الصامت يخفي أن السر كان هناك أصلًا.

    PYTHONPATH=packages python3 scripts/make_export.py
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "masar-ainat"

#: ما يدخل الأرشيف — قائمة سماح لا قائمة منع. قائمة المنع تنسى دائمًا شيئًا.
INCLUDE_DIRS = ["packages", "web", "tests", "scripts", "deploy", "docs", "db"]
INCLUDE_FILES = [
    "README.md", "STATUS.md", "requirements.txt", "requirements.in",
    ".env.example", ".gitignore",
]

#: ما لا يدخل مهما كان موقعه
EXCLUDE_NAMES = {"__pycache__", ".git", ".venv", "venv", "node_modules", "data"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".log", ".pid", ".dump", ".pbf", ".osrm"}
EXCLUDE_EXACT = {".env", ".env.dev", ".env.local", ".env.production",
                 ".env.staging", "fullchain.pem", "privkey.pem"}

#: النوائب المسموح بها — **القائمة الوحيدة للاستثناء، وهي على مستوى القيمة
#: لا على مستوى الملف**. الاستثناء بالملف («تجاهل مجلد docs») هو ما يجعل
#: التسريبات تمر: يكفي أن يُكتب سر في ملف مستثنى مرة واحدة. هنا لا ملف
#: مستثنى إطلاقًا؛ تُقبل القيمة فقط إن كانت نائبًا معلنًا.
PLACEHOLDERS = ("CHANGE_ME", "EXAMPLE_ONLY", "GENERATE_AT_DEPLOYMENT")

#: النائب وحده، أو النائب متبوعًا بلاحقة توضيحية بأحرف كبيرة
#: (``CHANGE_ME_DB``)، أو مسبوقًا بمعرّف مفتاح (``dev1:CHANGE_ME``).
PLACEHOLDER_VALUE = re.compile(
    r"^(?:[A-Za-z0-9_-]{1,16}:)?(?:" + "|".join(PLACEHOLDERS) + r")(?:[_A-Z0-9]*)$")

#: أنماط الأسرار. لكل نمط رقم مجموعة القيمة التي تُفحص مقابل قائمة النوائب؛
#: ``None`` يعني أن مجرد المطابقة تسريب مهما كانت القيمة (مفتاح خاص مثلًا).
SECRET_PATTERNS: list[tuple[str, str, int | None]] = [
    (r"(?i)\b(MASAR_JWT_SECRET|MASAR_ENCRYPTION_KEYS|MASAR_DB_PASSWORD"
     r"|MASAR_DB_MIGRATE_PASSWORD|MASAR_SEED_PASSWORD|POSTGRES_PASSWORD)"
     r"\s*=\s*[\"']?([^\s\"'#]+)",
     "سر بيئة له قيمة", 2),
    (r"(?i)\b(password|passwd|secret|api[_-]?key|apikey|access[_-]?token"
     r"|auth[_-]?token|private[_-]?key)\s*[:=]\s*[\"']([^\"'\n]{6,})[\"']",
     "قيمة سرّية مثبّتة في الكود", 2),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "مفتاح خاص", None),
    (r"\b(AKIA|ASIA)[0-9A-Z]{16}\b", "مفتاح AWS", None),
    (r"\bgh[pousr]_[A-Za-z0-9]{36,}", "رمز GitHub", None),
    (r"\bsk-[A-Za-z0-9]{32,}", "مفتاح خدمة", None),
    (r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
     "رمز JWT كامل", None),
    (r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://"
     r"[^\s:@\"'/]+:([^\s@\"'/]+)@",
     "رابط اتصال يحوي بيانات اعتماد", 1),
]

#: امتدادات نصية تُفحص. أي ملف نصي يدخل الأرشيف يُفحص — بلا استثناء لمجلد.
TEXT_SUFFIX = {".py", ".sh", ".sql", ".md", ".js", ".html", ".css", ".json",
               ".yml", ".yaml", ".conf", ".service", ".timer", ".txt", ".ini",
               ".cfg", ".toml", ".in", ".lock", ".env", ".example",
               ".webmanifest", ".gitignore"}


#: توسيعة متغيّر (``$VAR`` أو ``${VAR:-…}`` أو ``$(cmd)``) ليست قيمة حرفية:
#: السر — إن وُجد — يعيش في البيئة لا في الملف. هذا ليس استثناءً لملف، بل
#: تمييز لشكل القيمة نفسه، ويسري على كل الملفات بالتساوي.
EXPANSION = re.compile(r"^\$[({A-Za-z_]")


def is_placeholder(value: str) -> bool:
    """هل القيمة نائب معلن أو توسيعة متغيّر؟ التطابق كامل لا جزئي."""
    value = value.strip()
    return bool(PLACEHOLDER_VALUE.match(value) or EXPANSION.match(value))


def collect() -> list[Path]:
    out: list[Path] = []
    for name in INCLUDE_FILES:
        path = ROOT / name
        if path.is_file():
            out.append(path)
    for directory in INCLUDE_DIRS:
        base = ROOT / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if any(part in EXCLUDE_NAMES for part in path.parts):
                continue
            if path.suffix in EXCLUDE_SUFFIX or path.name in EXCLUDE_EXACT:
                continue
            out.append(path)
    return out


def scan_text(relative: str, text: str) -> list[str]:
    """يفحص نصًّا واحدًا ويعيد قائمة التسريبات. لا استثناء لأي ملف."""
    findings: list[str] = []
    for pattern, label, value_group in SECRET_PATTERNS:
        for match in re.finditer(pattern, text):
            if value_group is not None:
                value = match.group(value_group) or ""
                if is_placeholder(value):
                    continue
            line = text[:match.start()].count("\n") + 1
            snippet = match.group(0)[:80].replace("\n", " ")
            findings.append(f"{relative}:{line} — {label}: {snippet}")
    return findings


def scan(files: list[Path]) -> list[str]:
    findings: list[str] = []
    scanned = 0
    for path in files:
        relative = str(path.relative_to(ROOT))
        if path.suffix not in TEXT_SUFFIX and path.name not in INCLUDE_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        findings.extend(scan_text(relative, text))
    print(f"    فُحص {scanned} ملفًا نصيًا — بلا استثناء لأي ملف أو مجلد")
    print(f"    النوائب المقبولة: {'، '.join(PLACEHOLDERS)}")
    return findings


def main() -> int:
    print("=" * 74)
    print("بناء أرشيف التصدير")
    print("=" * 74)

    files = collect()
    print(f"\nالملفات المرشَّحة: {len(files)}")

    print("\n── فحص الأسرار ──")
    findings = scan(files)
    if findings:
        print(f"\n❌ توقف البناء — {len(findings)} تطابقًا محتملًا:")
        for item in findings:
            print(f"    · {item}")
        print("\nعالج كل بند أعلاه (أو أضف استثناءً مبرَّرًا) ثم أعد التشغيل.")
        return 1
    print("✅ لا أسرار في الملفات المرشَّحة")

    # تأكيد صريح أن الملفات الحساسة خارج الأرشيف
    print("\n── تأكيد الاستبعاد ──")
    included = {str(p.relative_to(ROOT)) for p in files}
    for must_not in (".env.dev", "var/", "deploy/osrm/data/",
                     "deploy/staging/.env.staging",
                     "deploy/staging/certs/privkey.pem"):
        # المسار المنتهي بـ/ مجلد فيُطابَق بالبادئة؛ وغيره ملف فيُطابَق
        # تطابقًا تامًا — وإلا استُبعد «.env.staging.example» بحجة أنه
        # يبدأ بـ«.env.staging»، وهو القالب الذي يجب أن يُصدَّر.
        leaked = [p for p in included
                  if (p.startswith(must_not) if must_not.endswith("/") else p == must_not)]
        mark = "❌" if leaked else "✅"
        print(f"{mark} {must_not:26s} {'مسرَّب: ' + str(leaked[:3]) if leaked else 'مستبعَد'}")
        if leaked:
            return 1

    staging = ROOT / "var" / "export" / NAME
    if staging.parent.exists():
        shutil.rmtree(staging.parent)
    staging.mkdir(parents=True)

    for path in files:
        target = staging / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    # مجلدات التشغيل الفارغة التي يتوقعها النظام
    for folder in ("var/logs", "var/run", "var/storage", "var/uploads", "var/backups"):
        keep = staging / folder / ".gitkeep"
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.write_text("", encoding="utf-8")

    archive = ROOT / "var" / "export" / f"{NAME}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(staging.parent))

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    size_mb = archive.stat().st_size / 1024 / 1024

    print(f"\n{'=' * 74}")
    print(f"✅ {archive}")
    print(f"   {len(files)} ملفًا · {size_mb:.2f} م.ب · SHA-256 {digest[:32]}…")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
