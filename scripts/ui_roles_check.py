"""فحص واجهات كل الأدوار في متصفح حقيقي.

الغرض ليس تكرار دورة التشغيل (تلك في ``browser_walkthrough.py``)، بل التأكد
أن **كل دور** يجد واجهته تعمل، ولا يجد ما ليس له. ثلاثة أسئلة لكل دور:

1. هل تُرسم شاشاته بلا خطأ جافاسكربت؟ خطأ واحد يوقف الرسم ويترك شاشة فارغة
   بلا رسالة — وهو ما لا يكشفه أي اختبار HTTP.
2. هل يرى ما له؟
3. هل **لا** يرى ما ليس له؟ إخفاء الأزرار ليس أمانًا، لكن ظهورها عيب أيضًا.

    PYTHONPATH=packages python3 scripts/ui_roles_check.py '<accounts-json>'
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "var" / "shots" / "roles"
BASE = "http://127.0.0.1:8080"

PASS, FAIL = "✅", "❌"
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, message: str) -> None:
    RESULTS.append((ok, message))
    print(f"{PASS if ok else FAIL} {message}", flush=True)


#: (المفتاح، العنوان، شاشات مسموحة، شاشات ممنوعة، نص يجب أن يظهر)
ROLES = [
    ("admin", "/", ["/users", "/settings", "/audit", "/md/hubs"], [],
     "المستخدمون والأدوار"),
    # ملاحظة: settings.read ممنوحة للمخطط والمدقق بالتصميم — الإعدادات
    # التشغيلية بيانات يجب أن يراها من يخطط ومن يدقق. الممنوع هو التعديل
    # (settings.write)، وهو ما يُفحص أدناه بغياب أزرار الحفظ.
    ("planner", "/", ["/imports", "/plans", "/plans/new", "/reports", "/settings"],
     ["/users"], "رفع الجدول الأسبوعي"),
    ("supervisor", "/", ["/assign", "/routes"],
     ["/imports", "/plans/new", "/users", "/audit"], "الإسناد والنشر"),
    ("tower", "/", ["/live", "/alerts", "/ondemand"],
     ["/users", "/settings"], ""),
    ("auditor", "/", ["/audit", "/reports", "/settings"],
     ["/users", "/assign"], "سجل التدقيق"),
]


def login(page: Page, email: str, password: str, *, url: str = BASE + "/") -> None:
    page.goto(url, wait_until="load")
    page.wait_for_selector("input[type=email]", timeout=20_000)
    page.fill("input[type=email]", email)
    page.fill("input[type=password]", password)
    page.click("button[type=submit]")
    page.wait_for_selector("input[type=password]", state="detached", timeout=30_000)
    page.wait_for_timeout(1500)


def goto(page: Page, path: str) -> None:
    page.evaluate("p => { window.location.hash = p; }", path)
    page.wait_for_timeout(1400)


def run(accounts: dict[str, list[str]]) -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        for key, home, allowed, denied, expect in ROLES:
            if key not in accounts:
                check(False, f"{key}: لا حساب مُمرَّر")
                continue
            errors: list[str] = []
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.on("pageerror", lambda e, bucket=errors: bucket.append(str(e)[:160]))
            try:
                login(page, *accounts[key])
                goto(page, home)
                body = page.inner_text("body")
                check(bool(body.strip()), f"{key}: الشاشة الرئيسية تُرسم")
                if expect:
                    seen = expect in body
                    for path in allowed:
                        if seen:
                            break
                        goto(page, path)
                        seen = expect in page.inner_text("body")
                    check(seen, f"{key}: يجد «{expect}»")

                for path in allowed:
                    goto(page, path)
                    text = page.inner_text("body")
                    denied_marker = "لا تملك صلاحية" in text or "غير مصرّح" in text
                    check(not denied_marker and len(text.strip()) > 40,
                          f"{key}: {path} متاح ويُرسم")

                for path in denied:
                    goto(page, path)
                    text = page.inner_text("body")
                    blocked = ("لا تملك صلاحية" in text or "غير مصرّح" in text
                               or "غير موجودة" in text or len(text.strip()) < 400)
                    check(blocked, f"{key}: {path} ممنوع كما يجب")

                if "/settings" in allowed:
                    goto(page, "/settings")
                    editable = page.eval_on_selector_all(
                        "#view-outlet button",
                        "els => els.filter(e => /حفظ|تعديل|تطبيق/.test(e.textContent)).length")
                    check(editable == 0,
                          f"{key}: يرى الإعدادات ولا يملك تعديلها "
                          f"({editable} زر تعديل)")

                page.screenshot(path=str(SHOTS / f"{key}.png"))
                check(not errors, f"{key}: بلا أخطاء جافاسكربت"
                                  + (f" — {errors[:2]}" if errors else ""))
            finally:
                context.close()

        # ── السائق: تطبيق منفصل على مقاس الجوال ────────────────────────────
        if "driver" in accounts:
            errors = []
            context = browser.new_context(
                viewport={"width": 430, "height": 932}, permissions=["geolocation"],
                geolocation={"latitude": 24.7250, "longitude": 46.6900})
            page = context.new_page()
            page.on("pageerror", lambda e, bucket=errors: bucket.append(str(e)[:160]))
            try:
                login(page, *accounts["driver"], url=BASE + "/driver")
                body = page.inner_text("body")
                check("رحلاتي" in body, "driver: تطبيق السائق يُرسم")
                check(page.title().startswith("مسار عينات — تطبيق السائق"),
                      f"driver: الصفحة الصحيحة ({page.title()})")
                # لا يرى أي شاشة تخطيط أو إدارة
                check("سجل التدقيق" not in body and "المستخدمون" not in body,
                      "driver: لا يرى شاشات التخطيط أو الإدارة")
                page.screenshot(path=str(SHOTS / "driver.png"))
                check(not errors, "driver: بلا أخطاء جافاسكربت"
                                  + (f" — {errors[:2]}" if errors else ""))
            finally:
                context.close()

        # ── الجهة الطالبة الخارجية ─────────────────────────────────────────
        if "requester" in accounts:
            errors = []
            context = browser.new_context(viewport={"width": 430, "height": 932})
            page = context.new_page()
            page.on("pageerror", lambda e, bucket=errors: bucket.append(str(e)[:160]))
            try:
                login(page, *accounts["requester"], url=BASE + "/request")
                body = page.inner_text("body")
                check("إرسال الطلب" in body, "requester: نموذج الطلب يُرسم")
                check("طلباتي" in body, "requester: يرى طلباته هو")
                # الفحص على عناصر التنقل لا على ورود الكلمة: صفحة الطلب
                # تشرح أن «الطلب يمر بمراجعة برج التحكم قبل الإسناد» — نص
                # توضيحي لا شاشة تشغيل.
                nav = page.eval_on_selector_all(
                    "nav a, nav button", "els => els.map(e => e.textContent).join(' ')")
                check(all(word not in nav for word in
                          ("سجل التدقيق", "الإسناد", "المستخدمون", "الخطط")),
                      f"requester: لا شاشات تشغيل في التنقل ({nav[:60] or 'لا تنقل'})")
                page.screenshot(path=str(SHOTS / "requester.png"))
                check(not errors, "requester: بلا أخطاء جافاسكربت"
                                  + (f" — {errors[:2]}" if errors else ""))
            finally:
                context.close()

        browser.close()

    passed = sum(1 for ok, _ in RESULTS if ok)
    print("\n" + "=" * 70)
    print(f"{passed} من {len(RESULTS)} فحصًا ناجحًا · اللقطات في var/shots/roles/")
    print("=" * 70)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(run(json.loads(sys.argv[1])))
