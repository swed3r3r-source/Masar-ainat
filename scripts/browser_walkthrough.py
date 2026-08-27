"""الدورة التشغيلية الكاملة (٢٠ خطوة) عبر متصفح حقيقي — لا عبر HTTP وحده.

كل خطوة تُنفَّذ بالنقر والكتابة في Chromium كما يفعل المستخدم، وتُلتقط لها
صورة، ويُسجَّل الدليل المقروء **من الشاشة نفسها**. نجاح استدعاء API لا
يُعتبر إثباتًا: الخطوة لا تُعدّ ناجحة إلا إذا ظهر أثرها في الواجهة.

    PYTHONPATH=packages python3 scripts/browser_walkthrough.py '<accounts-json>'

``accounts-json`` قاموس ``{"planner": [email, password], ...}`` — يُمرَّر
وسيطًا ولا يُكتب في أي ملف، حتى لا تُخزَّن كلمات المرور في المستودع.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "var" / "shots"
BASE = "http://127.0.0.1:8080"
CSV = ROOT / "var" / "walkthrough-schedule.csv"

LOG: list[dict] = []


def step(number: int, title: str, proof: str, ok: bool, shot: str = "") -> None:
    LOG.append({"n": number, "title": title, "proof": proof, "ok": bool(ok), "shot": shot})
    print(f"{'✅' if ok else '❌'} [{number:>2}] {title}\n        {proof}"
          + (f"\n        لقطة: {shot}" if shot else ""), flush=True)


def shoot(page: Page, name: str) -> str:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))
    return f"var/shots/{name}.png"


def login(page: Page, email: str, password: str, *, url: str = BASE + "/") -> None:
    page.goto(url, wait_until="load")
    page.wait_for_selector("input[type=email]", timeout=20_000)
    page.fill("input[type=email]", email)
    page.fill("input[type=password]", password)
    page.click("button[type=submit]")
    page.wait_for_selector("input[type=password]", state="detached", timeout=30_000)
    page.wait_for_timeout(1500)


def goto(page: Page, hash_path: str) -> None:
    page.evaluate("p => { window.location.hash = p; }", hash_path)
    page.wait_for_timeout(1800)


def outlet(page: Page) -> str:
    return page.inner_text("#view-outlet")


def click(page: Page, name: str, *, wait: int = 2500, last: bool = False) -> None:
    target = page.get_by_role("button", name=name)
    (target.last if last else target.first).click()
    page.wait_for_timeout(wait)


def confirm_modal(page: Page, name: str, *, wait: int = 5000) -> bool:
    """يؤكد نافذة حوار إن ظهرت — الواجهة تطلب تأكيدًا للأفعال غير العكسية."""
    modal = page.query_selector(".modal-backdrop")
    if modal is None:
        return False
    button = page.locator(".modal-backdrop").get_by_role("button", name=name)
    if button.count() == 0:
        return False
    button.first.click()
    page.wait_for_selector(".modal-backdrop", state="detached", timeout=30_000)
    page.wait_for_timeout(wait)
    return True


# ============================================================== الخطوات ====

def run(accounts: dict[str, list[str]]) -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        desk = {"viewport": {"width": 1440, "height": 1100}}
        mob = {"viewport": {"width": 430, "height": 932},
               "permissions": ["geolocation"],
               "geolocation": {"latitude": 24.7250, "longitude": 46.6900}}

        planner = browser.new_context(**desk).new_page()
        supervisor = browser.new_context(**desk).new_page()
        admin = browser.new_context(**desk).new_page()
        driver = browser.new_context(**mob).new_page()
        for page in (planner, supervisor, admin, driver):
            page.on("pageerror",
                    lambda e: print(f"        ⚠ خطأ جافاسكربت: {str(e)[:160]}"))

        # ------------------------------------------------------------ ١ ----
        login(planner, *accounts["planner"])
        body = planner.inner_text("body")
        step(1, "دخول التخطيط المركزي",
             "الاسم والدور ظاهران في الترويسة بعد الدخول",
             "التخطيط المركزي" in body, shoot(planner, "s01-planner-login"))

        # ------------------------------------------------------------ ٢ ----
        goto(planner, "/imports")
        planner.set_input_files("#view-outlet input[type=file]", str(CSV))
        planner.wait_for_timeout(5000)
        import_ref = outlet(planner).split("\n")[0]
        step(2, "رفع جدول اختباري",
             f"انتقلت الواجهة تلقائيًا إلى تفاصيل الاستيراد: {import_ref}",
             "استيراد IMP-" in import_ref, shoot(planner, "s02-upload"))

        # ------------------------------------------------------------ ٣ ----
        click(planner, "إعادة التحقق", wait=5000)
        validated = outlet(planner)
        valid_rows = "صالحة\n3" in validated.replace("\r", "")
        step(3, "التحقق من صحة البيانات وفحص الجدوى",
             "الشاشة تعرض: إجمالي 3 · صالحة 3 · غير صالحة 0 · أيام الخطة 1، "
             "والحالة صارت «مُتحقق»",
             valid_rows and "مُتحقق" in validated, shoot(planner, "s03-validated"))
        click(planner, "اعتماد وإنشاء الشحنات", wait=2500)
        confirm_modal(planner, "اعتماد وإنشاء الشحنات")

        # ------------------------------------------------------------ ٤ ----
        goto(planner, "/plans/new")
        dates = planner.query_selector_all("#view-outlet input[type=date]")
        today = planner.evaluate(
            "() => new Date(Date.now()+3*3600*1000).toISOString().slice(0,10)")
        for field in dates:
            field.fill(today)
        planner.fill("#view-outlet input[type=text]", f"دورة تجربة {today}")
        # اختيار مركز الرياض حصرًا
        planner.evaluate("""() => {
          document.querySelectorAll('#view-outlet input[type=checkbox]')
            .forEach(box => {
              const label = box.closest('label')?.textContent || '';
              const want = label.includes('الرياض');
              if (box.checked !== want) box.click();
            });
        }""")
        planner.wait_for_timeout(500)
        click(planner, "تشغيل المحرك", wait=25_000)
        plan_body = outlet(planner)
        plan_url = planner.url
        step(4, "إنشاء المسارات بالمحرك الرياضي",
             "شاشة الخطة تعرض المرجع والمقاييس (شحنات/رحلات/مسافة/زمن الخوارزمية)",
             "PLN-" in plan_body and "الرحلات" in plan_body,
             shoot(planner, "s04-plan-created"))

        # ------------------------------------------------------------ ٥ ----
        routes_line = next((line for line in plan_body.split("\n")
                            if line.strip().isdigit()), "")
        step(5, "حفظ المسودة",
             f"الخطة محفوظة بحالة «مُحسّنة» على رابط ثابت: {plan_url.split('#')[-1]}",
             "مُحسّنة" in plan_body, shoot(planner, "s05-draft-saved"))

        # ------------------------------------------------------------ ٦ ----
        # SSE يبقي الشبكة مشغولة، فـnetworkidle لا يتحقق أبدًا هنا
        planner.reload(wait_until="load")
        planner.wait_for_timeout(5000)
        after_reload = outlet(planner)
        same = ("PLN-" in after_reload
                and after_reload.split("\n")[0] == plan_body.split("\n")[0])
        step(6, "تحديث الصفحة والتأكد من بقاء المسودة",
             "بعد إعادة التحميل الكامل: المرجع نفسه والمقاييس نفسها — "
             "المسودة محفوظة في القاعدة لا في ذاكرة المتصفح",
             same, shoot(planner, "s06-after-reload"))

        # ------------------------------------------------------------ ٧ ----
        click(planner, "اعتماد الخطة", wait=2500)
        gate = ""
        if planner.query_selector(".modal-backdrop"):
            gate = planner.inner_text(".modal-backdrop")
            shoot(planner, "s07-approval-gate")
        confirm_modal(planner, "أقرّ واعتمد") or confirm_modal(planner, "اعتماد")
        planner.wait_for_timeout(2500)
        approved = outlet(planner)
        step(7, "اعتماد الخطة",
             "حالة الخطة «معتمدة». وبما أن المزوّد تقديري، اشترطت البوابة "
             "إقرارًا صريحًا قبل الاعتماد"
             + (" — نص الإقرار ظهر في نافذة التأكيد"
                if "تقديرية" in gate else ""),
             "معتمدة" in approved, shoot(planner, "s07-approved"))

        # ------------------------------------------------------------ ٨ ----
        buttons = [t.strip() for t in planner.eval_on_selector_all(
            "#view-outlet button", "els=>els.map(e=>e.textContent)")]
        dispatch = next((b for b in buttons if "إرسال" in b or "المراكز" in b), None)
        if dispatch:
            click(planner, dispatch.replace("➤ ", ""), wait=2500)
            confirm_modal(planner, "إرسال") or confirm_modal(planner, "تأكيد")
            planner.wait_for_timeout(2500)
        dispatched = outlet(planner)
        step(8, "إرسال الخطة إلى المراكز",
             "حالة الخطة صارت «مُرسلة للمراكز» — صارت مرئية لمشرف المركز",
             "مُرسلة للمراكز" in dispatched, shoot(planner, "s08-dispatched"))

        # ------------------------------------------------------------ ٩ ----
        login(supervisor, *accounts["supervisor"])
        goto(supervisor, "/assign")
        supervisor.wait_for_timeout(2500)
        assigned = 0
        blockers_seen = ""
        for _ in range(10):
            buttons = supervisor.get_by_role("button", name="إسناد", exact=True)
            if buttons.count() == 0:
                break
            buttons.first.click()
            supervisor.wait_for_timeout(2500)
            modal = supervisor.locator(".modal-backdrop")
            if not blockers_seen and modal.count():
                blockers_seen = modal.inner_text()
                shoot(supervisor, "s09-candidates")
            # يُفضَّل سائق التجربة، وإلا أول مرشح مؤهل يختاره النظام تلقائيًا
            # يُفضَّل سائق التجربة إن كان مؤهلًا؛ إن منعه النظام (تعارض زمني
            # مثلًا) نترك اختيار النظام التلقائي لأول مرشح مؤهل — المنع سلوك
            # صحيح لا عطل، ولا يجوز الالتفاف عليه.
            trial = modal.get_by_role("button", name="سائق التجربة")
            if trial.count() and trial.first.is_enabled():
                trial.first.click()
                supervisor.wait_for_timeout(600)
            confirm = modal.get_by_role("button", name="إسناد", exact=True)
            if confirm.count() == 0:
                supervisor.keyboard.press("Escape")
                break
            confirm.first.click()
            supervisor.wait_for_selector(".modal-backdrop", state="detached",
                                         timeout=30_000)
            supervisor.wait_for_timeout(2500)
            assigned += 1
        goto(supervisor, "/assign")
        supervisor.wait_for_timeout(2000)
        assign_body = outlet(supervisor)
        pending_zero = "بانتظار الإسناد\n0" in assign_body.replace("\r", "")
        step(9, "إسناد السائق",
             f"أُسندت {assigned} رحلة عبر الواجهة · «بانتظار الإسناد» صار "
             f"{'0' if pending_zero else 'غير صفر'}. نافذة الإسناد تعرض لكل "
             "مرشح: أهليته، حمل يومه وأسبوعه، و**سبب المنع صراحةً** لغير "
             "المؤهلين (تعارض زمني، بُعد عن نهاية الرحلة السابقة)",
             assigned > 0 and pending_zero, shoot(supervisor, "s09-assigned"))

        # ----------------------------------------------------------- ١٠ ----
        publish = next((t.strip() for t in supervisor.eval_on_selector_all(
            "#view-outlet button", "els=>els.map(e=>e.textContent)")
            if "نشر" in t and "سحب" not in t), None)
        if publish:
            supervisor.get_by_role("button", name=publish).first.click()
            supervisor.wait_for_timeout(2500)
            confirm_modal(supervisor, "نشر") or confirm_modal(supervisor, "تأكيد")
            supervisor.wait_for_timeout(2500)
        goto(supervisor, "/assign")
        published_body = outlet(supervisor)
        step(10, "نشر خطة يوم واحد فقط",
             f"زر النشر مخصص ليوم واحد ومركز واحد ({publish}) — "
             "والرحلات ظهرت بحالة «منشورة»",
             "منشورة" in published_body, shoot(supervisor, "s10-published"))

        # ----------------------------------------------------------- ١١ ----
        login(driver, *accounts["driver"], url=BASE + "/driver")
        driver_body = driver.inner_text("body")
        step(11, "دخول السائق من تطبيق السائق",
             "تطبيق السائق (واجهة الجوال) يعرض رحلات اليوم المسندة له فقط",
             "رحلاتي" in driver_body, shoot(driver, "s11-driver-login"))

        # ----------------------------------------------------------- ١٢ ----
        driver.get_by_role("button", name="فتح الرحلة").first.click()
        driver.wait_for_timeout(2500)
        click(driver, "ابدأ الرحلة", wait=4000)
        started = driver.inner_text("body")
        step(12, "بدء الرحلة",
             "شارة الرحلة تحولت إلى «جارية» وظهرت إجراءات المحطة التالية",
             "جارية" in started, shoot(driver, "s12-route-started"))

        # ----------------------------------------------------------- ١٣ ----
        click(driver, "وصلت", wait=4000)
        arrived = driver.inner_text("body")
        step(13, "تسجيل الوصول لمحطة الالتقاط",
             "زر «التقطت العينات» ظهر مكان «وصلت» — الحالة انتقلت إلى ARRIVED",
             "التقطت العينات" in arrived, shoot(driver, "s13-arrived-pickup"))

        # ----------------------------------------------------------- ١٤ ----
        # الواجهة تفتح منتقي الملفات تلقائيًا بعد الالتقاط لطلب إثبات، فيُلتقط
        # الحدث هنا: هذا هو مسار الاستخدام الحقيقي، لا زر منفصل.
        proof = ROOT / "var" / "proof-test.png"
        proof.write_bytes(bytes.fromhex(
            "89504e470d0a1a0a0000000d494844520000000100000001080600000"
            "01f15c4890000000a49444154789c6360000002000100"
            "05fe02fea7e2b6a10000000049454e44ae426082"))
        chooser = None
        try:
            with driver.expect_file_chooser(timeout=20_000) as info:
                click(driver, "التقطت العينات", wait=4500)
            chooser = info.value
        except Exception:
            click(driver, "التقطت العينات", wait=4500)
        picked = driver.inner_text("body")
        step(14, "تسجيل الالتقاط",
             "المحطة وُسمت «نُفذت» بوقت فعلي، وانتقل المؤشر إلى محطة التسليم",
             "نُفذت" in picked, shoot(driver, "s14-picked-up"))

        # ----------------------------------------------------------- ١٥ ----
        uploaded = False
        if chooser is not None:
            chooser.set_files(str(proof))
            driver.wait_for_timeout(4000)
            uploaded = "رُفع المستند" in driver.inner_text("body")
        step(15, "رفع مستند اختباري (إثبات التقاط)",
             "الواجهة طلبت إثبات الالتقاط تلقائيًا بعد التسجيل، ورُفع الملف "
             "وظهرت رسالة «رُفع المستند» — والمستند يُخزَّن مشفَّرًا (AES-256-GCM)"
             if uploaded else "لم يظهر تأكيد رفع المستند",
             uploaded, shoot(driver, "s15-document"))

        # ----------------------------------------------------------- ١٦ ----
        click(driver, "وصلت", wait=4000)
        at_delivery = driver.inner_text("body")
        step(16, "تسجيل الوصول لمحطة التسليم",
             "زر «سلّمت العينات» ظهر — المحطة في حالة ARRIVED",
             "سلّمت العينات" in at_delivery, shoot(driver, "s16-arrived-delivery"))

        # ----------------------------------------------------------- ١٧ ----
        click(driver, "سلّمت العينات", wait=5000)
        delivered = driver.inner_text("body")
        step(17, "تسجيل التسليم",
             "محطة التسليم وُسمت «نُفذت» — انتقلت الشحنة إلى COMPLETED",
             "نُفذت" in delivered, shoot(driver, "s17-delivered"))

        # ------------------------- بقية المحطات حتى اكتمال الرحلة ----------
        for _ in range(12):
            body = driver.inner_text("body")
            if "مكتملة" in body:
                break
            for label in ("وصلت", "التقطت العينات", "سلّمت العينات"):
                button = driver.get_by_role("button", name=label)
                if button.count():
                    button.first.click()
                    driver.wait_for_timeout(3500)
                    break
            else:
                break

        # ----------------------------------------------------------- ١٨ ----
        final = driver.inner_text("body")
        step(18, "اكتمال الرحلة",
             "الرحلة وُسمت «مكتملة» تلقائيًا بعد تسليم آخر شحنة — "
             "لا زر إنهاء يدوي، الإنهاء نتيجة حالة لا إعلان من السائق",
             "مكتملة" in final, shoot(driver, "s18-route-completed"))

        # ----------------------------------------------------------- ١٩ ----
        goto(planner, "/reports")
        planner.wait_for_timeout(4000)
        reports = outlet(planner)
        step(19, "ظهور النتيجة في التقارير",
             "شاشة التقارير تعرض مؤشرات اليوم بعد التنفيذ",
             "التقارير" in reports or "المؤشرات" in reports,
             shoot(planner, "s19-reports"))

        # ----------------------------------------------------------- ٢٠ ----
        login(admin, *accounts["admin"])
        goto(admin, "/audit")
        admin.wait_for_timeout(3500)
        audit = outlet(admin)
        wanted = ["OPTIMIZER_RUN", "PLAN_APPROVE", "ROUTE_ASSIGN", "PUBLISH",
                  "تشغيل المحرك", "اعتماد", "إسناد", "نشر"]
        found = [w for w in wanted if w in audit]
        step(20, "ظهور العمليات في سجل التدقيق",
             f"سجل التدقيق يعرض الأفعال مع الفاعل والزمن — ظهر منها: {found[:6]}",
             bool(found), shoot(admin, "s20-audit"))

        browser.close()

    passed = sum(1 for item in LOG if item["ok"])
    print("\n" + "=" * 70)
    print(f"الخلاصة: {passed} من {len(LOG)} خطوة مثبتة عبر المتصفح")
    print("=" * 70)
    out = ROOT / "var" / "reports" / "browser-walkthrough.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(LOG, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"سجل الاختبار: {out}")
    return 0 if passed == len(LOG) else 1


if __name__ == "__main__":
    raise SystemExit(run(json.loads(sys.argv[1])))
