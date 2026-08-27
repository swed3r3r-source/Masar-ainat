"""اختبار الواجهة في متصفح حقيقي (Playwright + Chromium).

يفحص ما لا يكشفه اختبار API: هل الشاشات تُبنى فعلًا؟ هل الاتجاه RTL صحيح؟
هل توجد أخطاء في Console؟ هل تعمل على مقاس الجوال؟ وهل تُخفي الواجهة ما
لا يملك المستخدم صلاحيته؟ (§32 من معايير القبول)
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:8080"
#: لا كلمة مرور مثبّتة في المستودع — تُمرَّر عبر البيئة
PASSWORD = os.environ["MASAR_TEST_PASSWORD"]
SHOT_DIR = Path(__file__).resolve().parents[1] / "var" / "screenshots"

PASS, FAIL, INFO = "✅", "❌", "  ·"
failures: list[str] = []


def check(condition: bool, message: str, detail: str = "") -> bool:
    print(f"{PASS if condition else FAIL} {message}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(message)
    return condition


def step(title: str) -> None:
    print(f"\n{'─' * 72}\n{title}\n{'─' * 72}")


class Session:
    """جلسة متصفح تجمع أخطاء Console وفشل الشبكة تلقائيًا."""

    def __init__(self, browser, viewport=None, label=""):
        self.label = label
        self.context = browser.new_context(
            viewport=viewport or {"width": 1440, "height": 960},
            locale="ar-SA",
            timezone_id="Asia/Riyadh",
            permissions=["geolocation"],
            geolocation={"latitude": 24.7136, "longitude": 46.6753},
        )
        self.console_errors: list[str] = []
        self.failed_requests: list[str] = []
        self.page = self.context.new_page()
        self.page.on("console", self._on_console)
        self.page.on("pageerror", lambda exc: self.console_errors.append(f"pageerror: {exc}"))
        self.page.on("requestfailed", self._on_failed)

    def _on_console(self, message):
        if message.type in ("error",):
            text = message.text
            # تجاهل أخطاء تحميل بلاطات الخريطة عند غياب الإنترنت
            if "tile" in text.lower() or "ERR_" in text:
                return
            # ٤٢٩ أثناء الدخول ليس خطأ واجهة بل محدِّد المعدّل يعمل كما صُمّم،
            # وطبقة الدخول تعيد المحاولة بعد المهلة التي يطلبها الخادم.
            if "429" in text or "الحد المسموح" in text:
                return
            self.console_errors.append(text)

    #: /api/events تدفق مفتوح يُلغى عند مغادرة الصفحة — إلغاؤه ليس فشلًا
    IGNORED_REQUESTS = ("/api/events",)

    def _on_failed(self, request):
        if request.url.startswith(BASE) and "/api/" in request.url:
            if any(path in request.url for path in self.IGNORED_REQUESTS):
                return
            self.failed_requests.append(f"{request.method} {request.url}")

    def login(self, email: str, path: str = "/") -> None:
        # ملاحظة: بعد الدخول يبقى اتصال SSE مفتوحًا، فحالة networkidle
        # لا تتحقق أبدًا — ننتظر عناصر الشاشة بدلًا منها.
        for attempt in range(4):
            self.page.goto(f"{BASE}{path}", wait_until="load")
            self.page.wait_for_selector("input[type=email]", timeout=15000)
            self.page.fill('input[type=email]', email)
            self.page.fill('input[type=password]', PASSWORD)
            self.page.click('button[type=submit]')
            self.page.wait_for_timeout(2500)

            # محدِّد معدّل تسجيل الدخول ميزة أمنية مقصودة (§29). تشغيل هذا
            # الاختبار مباشرةً بعد حزمة أخرى قد يلامسه، فننتظر ونعيد المحاولة
            # بدل تعطيله — كي تبقى الميزة مُختبَرة كما هي في الإنتاج.
            error = self.page.locator(".error")
            text = error.inner_text() if error.count() else ""
            if "الحد المسموح" not in text:
                break
            self.page.wait_for_timeout(20000)

        if path in ("/", ""):
            self.page.wait_for_selector(".nav-item", timeout=15000)

    def shot(self, name: str) -> Path:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOT_DIR / f"{name}.png"
        self.page.screenshot(path=str(path), full_page=False)
        return path

    def close(self):
        self.context.close()


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            return run(browser)
        finally:
            browser.close()


def run(browser) -> int:
    # ------------------------------------------------ صفحة الدخول ----
    step("١) صفحة الدخول والاتجاه")
    guest = Session(browser, label="guest")
    guest.page.goto(BASE, wait_until="load")
    check(guest.page.locator("html").get_attribute("dir") == "rtl",
          "اتجاه الصفحة RTL")
    check(guest.page.locator("html").get_attribute("lang") == "ar", "لغة الصفحة عربية")
    check(guest.page.locator(".auth-card").count() == 1, "ظهرت بطاقة تسجيل الدخول")
    guest.shot("01-login")

    guest.page.fill('input[type=email]', "admin@masar.test")
    guest.page.fill('input[type=password]', "wrong-password")
    guest.page.click('button[type=submit]')
    guest.page.wait_for_timeout(1200)
    error_text = guest.page.locator(".error").inner_text()
    check("غير صحيحة" in error_text, "رسالة خطأ عربية عند كلمة مرور خاطئة",
          error_text.strip()[:60])
    guest.close()

    # ------------------------------------------- التخطيط المركزي -----
    step("٢) واجهة التخطيط المركزي")
    planner = Session(browser, label="planner")
    planner.login("planner@masar.test")
    check(planner.page.locator(".app-header").count() == 1, "ظهرت قشرة التطبيق")
    check("مسار عينات" in planner.page.title(), "عنوان الصفحة صحيح")
    planner.shot("02-dashboard-planner")

    kpi_count = planner.page.locator(".kpi").count()
    check(kpi_count >= 6, "لوحة المعلومات تعرض بطاقات مؤشرات", f"{kpi_count} بطاقة")

    nav_items = planner.page.locator(".nav-item").all_inner_texts()
    check(any("رفع الجدول" in item for item in nav_items),
          "قائمة التنقل تعرض رفع الجدول للتخطيط المركزي")
    check(not any("إدارة" in item and "المستخدمون" in item for item in nav_items),
          "التخطيط المركزي لا يرى إدارة المستخدمين")

    # ---- شاشة الخطط
    planner.page.click('.nav-item:has-text("الخطط")')
    planner.page.wait_for_timeout(1500)
    check(planner.page.locator("table.data").count() >= 1, "جدول الخطط ظهر")
    planner.shot("03-plans")

    # ---- فتح أحدث خطة
    rows = planner.page.locator("table.data tbody tr")
    if rows.count():
        rows.first.click()
        planner.page.wait_for_timeout(3000)
        check(planner.page.locator(".kpi").count() >= 12,
              "شاشة معاينة المسارات تعرض المقاييس الكاملة",
              f"{planner.page.locator('.kpi').count()} بطاقة")
        warning_cards = planner.page.locator(".warning-card")
        check(warning_cards.count() >= 1, "بطاقات التحذيرات معروضة",
              f"{warning_cards.count()} بطاقة")
        if warning_cards.count():
            text = warning_cards.first.inner_text()
            check("السبب" in text and "الإجراء المقترح" in text,
                  "بطاقة التحذير تحمل السبب والجهة المتأثرة والإجراء المقترح")
        planner.shot("04-plan-preview")

        # الضغط على تحذير مرتبط برحلة ينقل إلى الرحلة
        route_warning = planner.page.locator(".warning-card:has(.badge.brand)")
        if route_warning.count():
            route_warning.first.click()
            planner.page.wait_for_timeout(2500)
            check("/routes/" in planner.page.url,
                  "الضغط على بطاقة التحذير ينقل إلى الرحلة المتأثرة",
                  planner.page.url.split("#")[-1])
            check(planner.page.locator("canvas.map-canvas").count() == 1,
                  "خريطة المسار ظهرت في شاشة الرحلة")
            check(planner.page.locator(".stop-row").count() >= 2,
                  "تسلسل المحطات معروض",
                  f"{planner.page.locator('.stop-row').count()} محطة")
            planner.shot("05-route-detail")

            # تحديث الصفحة لا يفقد البيانات (§32)
            planner.page.reload(wait_until="load")
            planner.page.wait_for_timeout(2500)
            check(planner.page.locator(".stop-row").count() >= 2,
                  "تحديث الصفحة لا يفقد البيانات (اختبار ٣٣)")

    # ---- تقدير السائقين
    planner.page.goto(f"{BASE}/#/estimation", wait_until="load")
    planner.page.wait_for_timeout(2500)
    check(planner.page.locator("table.data").count() >= 1, "شاشة تقدير السائقين تعمل")
    planner.shot("06-driver-estimation")

    # ---- التقارير
    planner.page.goto(f"{BASE}/#/reports", wait_until="load")
    planner.page.wait_for_timeout(2500)
    check(planner.page.locator(".kpi").count() >= 10, "شاشة التقارير تعرض المؤشرات")
    planner.page.click('.tab:has-text("تقرير مُجمَّع")')
    planner.page.wait_for_timeout(2000)
    check(planner.page.locator("table.data").count() >= 1, "التقرير المُجمَّع يعمل")
    planner.shot("07-reports")

    check(not planner.console_errors, "لا أخطاء في Console لواجهة التخطيط",
          "; ".join(planner.console_errors[:3]))
    check(not planner.failed_requests, "لا طلبات API فاشلة",
          "; ".join(planner.failed_requests[:3]))
    planner.close()

    # ------------------------------------------------- المشرف --------
    step("٣) واجهة مشرف مركز الانطلاق")
    supervisor = Session(browser, label="supervisor")
    supervisor.login("sup.ryd@masar.test")
    nav_items = supervisor.page.locator(".nav-item").all_inner_texts()
    check(not any("رفع الجدول" in item for item in nav_items),
          "المشرف لا يرى شاشة رفع الجدول الوطني (اختبار ٣٢)")
    check(any("الإسناد والنشر" in item for item in nav_items),
          "المشرف يرى شاشة الإسناد والنشر")
    check(not any("سجل التدقيق" in item for item in nav_items),
          "المشرف لا يرى سجل التدقيق")

    supervisor.page.goto(f"{BASE}/#/assign", wait_until="load")
    supervisor.page.wait_for_timeout(2500)
    check(supervisor.page.locator(".kpi").count() >= 4, "شاشة الإسناد تعرض الملخص")
    supervisor.shot("08-assign")

    # فتح حوار الإسناد إن وُجدت رحلة
    # يُقصر البحث على منطقة المحتوى حتى لا يلتقط عنصر التنقل «الإسناد والنشر»
    assign_button = supervisor.page.locator(
        '.app-main table.data button:has-text("تغيير السائق"), '
        '.app-main table.data button:text-is("إسناد")').first
    if assign_button.count():
        assign_button.click()
        supervisor.page.wait_for_timeout(2500)
        check(supervisor.page.locator(".modal").count() == 1, "حوار الإسناد فُتح")
        candidates = supervisor.page.locator(".modal .warning-card")
        check(candidates.count() >= 1, "قائمة المرشحين معروضة",
              f"{candidates.count()} مرشح")
        if candidates.count():
            text = candidates.first.inner_text()
            check("الأسبوع" in text or "اليوم" in text,
                  "بطاقة المرشح تعرض حمله اليومي والأسبوعي (عدالة التوزيع)")
        supervisor.shot("09-assign-dialog")
        supervisor.page.keyboard.press("Escape")

    # منع الوصول المباشر لشاشة خارج الصلاحية
    supervisor.page.goto(f"{BASE}/#/audit", wait_until="load")
    supervisor.page.wait_for_timeout(1500)
    body_text = supervisor.page.locator(".app-main").inner_text()
    check("لا تملك صلاحية" in body_text,
          "الوصول المباشر لشاشة سجل التدقيق مرفوض للمشرف")

    supervisor.page.goto(f"{BASE}/#/live", wait_until="load")
    supervisor.page.wait_for_timeout(2500)
    check(supervisor.page.locator("canvas.map-canvas").count() == 1,
          "الخريطة المباشرة تعرض لوحة الرسم")
    supervisor.shot("10-live-map")

    check(not supervisor.console_errors, "لا أخطاء في Console لواجهة المشرف",
          "; ".join(supervisor.console_errors[:3]))
    supervisor.close()

    # ------------------------------------------------- المدير --------
    step("٤) واجهة مدير النظام")
    admin = Session(browser, label="admin")
    admin.login("admin@masar.test")
    admin.page.goto(f"{BASE}/#/settings", wait_until="load")
    admin.page.wait_for_timeout(2500)
    check(admin.page.locator("table.data").count() >= 3,
          "شاشة الإعدادات تعرض المجموعات")
    settings_text = admin.page.locator(".app-main").inner_text()
    check("الأخص يفوز" in settings_text, "شرح آلية حل القيم الهرمية معروض")
    check("مركز الانطلاق" in settings_text, "مصدر القيمة معروض")
    admin.shot("11-settings")

    admin.page.goto(f"{BASE}/#/md/facilities", wait_until="load")
    admin.page.wait_for_timeout(2500)
    check(admin.page.locator("table.data tbody tr").count() >= 10,
          "شاشة الجهات الصحية تعرض السجلات",
          f"{admin.page.locator('table.data tbody tr').count()} سجل")
    admin.shot("12-facilities")

    admin.page.goto(f"{BASE}/#/audit", wait_until="load")
    admin.page.wait_for_timeout(2500)
    check(admin.page.locator("table.data tbody tr").count() >= 5,
          "سجل التدقيق يعرض العمليات")
    admin.shot("13-audit")

    admin.page.goto(f"{BASE}/#/users", wait_until="load")
    admin.page.wait_for_timeout(2000)
    admin.page.click('button:has-text("مصفوفة الصلاحيات")')
    admin.page.wait_for_timeout(1500)
    check(admin.page.locator(".modal table.data").count() == 1,
          "مصفوفة الصلاحيات الكاملة تُعرض")
    admin.shot("14-permission-matrix")

    check(not admin.console_errors, "لا أخطاء في Console لواجهة المدير",
          "; ".join(admin.console_errors[:3]))
    admin.close()

    # ------------------------------------------- تطبيق السائق --------
    step("٥) تطبيق السائق (مقاس الجوال)")
    driver = Session(browser, viewport={"width": 390, "height": 844}, label="driver")
    driver.login("drv-ryd-01@masar.test", "/driver")
    driver.page.wait_for_timeout(2500)
    check(driver.page.locator(".driver-app").count() == 1, "تطبيق السائق يعمل على الجوال")
    driver.shot("15-driver-routes")

    body_text = driver.page.locator(".driver-body").inner_text()
    check("درجة الحرارة" not in body_text and "الحرارة" not in body_text.split("يتطلب")[0],
          "لا يوجد حقل إدخال حرارة في تطبيق السائق (§18)")

    open_route = driver.page.locator('button:has-text("فتح الرحلة"), '
                                     'button:has-text("عرض التفاصيل")').first
    if open_route.count():
        open_route.click()
        driver.page.wait_for_timeout(2500)
        stops = driver.page.locator(".driver-stop")
        check(stops.count() >= 1, "تفاصيل الرحلة تعرض المحطات", f"{stops.count()} محطة")
        page_text = driver.page.locator(".driver-body").inner_text()
        # الرحلة قد تكون قيد التنفيذ (أزرار) أو منتهية (حالة نهائية) حسب ما
        # نفّذته الفحوص السابقة على نفس البيانات. الشرط الحقيقي هو أن تعرض
        # الشاشة **حالة تنفيذ مفهومة** لا شاشة صامتة.
        markers = ("ابدأ الرحلة", "الملاحة", "وصلت", "نُفذت", "اكتملت", "مكتملة",
                   "تم التسليم", "التقطت", "مسلَّمة", "منتهية")
        check(any(marker in page_text for marker in markers),
              "أزرار التنفيذ أو حالة التنفيذ ظاهرة",
              f"نص الشاشة: {page_text[:180]!r}")
        temperature_inputs = driver.page.locator(
            'input[placeholder*="حرارة"], input[name*="temperature"]').count()
        check(temperature_inputs == 0,
              "لا يوجد أي حقل إدخال لدرجة الحرارة في شاشة المحطة")
        driver.shot("16-driver-route")

    check(not driver.console_errors, "لا أخطاء في Console لتطبيق السائق",
          "; ".join(driver.console_errors[:3]))
    driver.close()

    # ------------------------------------------ مقدم الطلب الخارجي ---
    step("٦) بوابة مقدم الطلب الخارجي")
    requester = Session(browser, viewport={"width": 900, "height": 900}, label="requester")
    requester.login("req.phc01@masar.test", "/request")
    requester.page.wait_for_timeout(2500)
    page_text = requester.page.locator(".app-main").inner_text()
    check("طلب نقل عينات" in page_text, "بوابة الطلب تعمل")
    check("مركز صحي النسيم" in page_text, "جهة مقدم الطلب معروضة")
    check("لا يمكنك إنشاء طلب لجهة أخرى" in page_text,
          "قيد الجهة الواحدة معلن في الواجهة")

    options = requester.page.locator("select").first.locator("option").all_inner_texts()
    check(all("مركز صحي" not in option for option in options),
          "قائمة جهات التسليم تعرض المختبرات وبنوك الدم فقط (RLS)",
          f"{len(options)} خيار")
    requester.shot("17-requester")

    check(not requester.console_errors, "لا أخطاء في Console لبوابة الطلب",
          "; ".join(requester.console_errors[:3]))
    requester.close()

    # ------------------------------------------------- الاستجابة -----
    step("٧) الاستجابة على مقاسات مختلفة")
    for width, height, name in ((360, 740, "mobile"), (768, 1024, "tablet"),
                                (1440, 900, "desktop")):
        session = Session(browser, viewport={"width": width, "height": height},
                          label=name)
        session.login("sup.ryd@masar.test")
        session.page.wait_for_timeout(2000)
        overflow = session.page.evaluate(
            "() => document.documentElement.scrollWidth > "
            "document.documentElement.clientWidth + 2")
        check(not overflow, f"لا تمرير أفقي على مقاس {width}px ({name})")
        if width <= 780:
            toggle_visible = session.page.locator(".nav-toggle").is_visible()
            check(toggle_visible, f"زر القائمة يظهر على مقاس {width}px")
        session.shot(f"18-responsive-{name}")
        session.close()

    return finish()


def finish() -> int:
    step("النتيجة")
    print(f"لقطات الشاشة في: {SHOT_DIR}")
    if failures:
        print(f"{FAIL} فشل {len(failures)} فحصًا:")
        for item in failures:
            print(f"   - {item}")
        return 1
    print(f"{PASS} نجحت كل فحوص الواجهة")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
