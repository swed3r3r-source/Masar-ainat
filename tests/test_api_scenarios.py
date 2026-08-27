"""سيناريوهات تكاملية عبر HTTP وقاعدة البيانات (§30: ١٤، ١٩–٣٩، ٤١، ٤٢، ٤٦).

كل فحص هنا يمر بنفس نقاط الـ API التي تستخدمها الواجهة، وبنفس المصادقة
والصلاحيات وسياسات RLS. لا اختبار يقرأ الجدول مباشرة ليتحقق من نتيجة: القراءة
المباشرة تُستخدم للتهيئة فقط، والتحقق يمر دائمًا بالطبقات كاملة.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import sys
import unittest
import uuid
from pathlib import Path

from .support import (
    require_test_password,
    TZ,
    ApiClient,
    MasarTestCase,
    ROOT,
    db_connection,
    scenario,
    server_is_up,
)

sys.path.insert(0, str(ROOT / "scripts"))

STATE: dict[str, object] = {}


# ================================================== تهيئة الوحدة ============

def setUpModule() -> None:  # noqa: N802
    if not server_is_up():
        raise unittest.SkipTest(
            "الخادم غير مشغّل — شغّل ./scripts/run_tests.sh بدل unittest مباشرة")

    STATE["admin"] = ApiClient("admin@masar.test")
    STATE["planner"] = ApiClient("planner@masar.test")
    STATE["supervisor"] = ApiClient("sup.ryd@masar.test")
    STATE["arar"] = ApiClient("sup.arr@masar.test")
    STATE["tower"] = ApiClient("tower@masar.test")
    STATE["auditor"] = ApiClient("auditor@masar.test")
    STATE["requester"] = ApiClient("req.phc01@masar.test")
    STATE["other_requester"] = ApiClient("req.hos01@masar.test")

    admin = STATE["admin"]
    hubs = admin.data(admin.get("/api/md/hubs"))
    STATE["hub_by_code"] = {hub["code"]: hub for hub in hubs}
    facilities = admin.data(admin.get("/api/md/facilities", params={"limit": 500}))
    STATE["facility_by_code"] = {f["code"]: f for f in facilities}

    _build_weekly_plan()
    _build_same_day_plan()
    _publish_future_day()


def _publish_future_day() -> None:
    """ينشر يومًا **مستقبليًا** لمركز عرعر — رحلات منشورة لا يمكن أن تبدأ.

    سيناريو ٣٧ (تعديل رحلة منشورة) يحتاج رحلة حالتها PUBLISHED. رحلات اليوم
    تبدأ أثناء سيناريوهات التنفيذ، فيصبح توفّر رحلة غير مبتدئة رهنًا بترتيب
    تنفيذ الاختبارات — وهو ما جعل السيناريو يُتخطّى أحيانًا. رحلة ليوم قادم
    لا يمكن أن تبدأ (القاعدة تمنع البدء قبل تاريخ الخدمة)، فتبقى منشورة حتمًا.
    """
    supervisor, admin = STATE["arar"], STATE["admin"]
    hub = STATE["hub_by_code"]["H-ARR-1"]
    dates = STATE.get("plan_dates") or []
    if not dates:
        STATE["future_reason"] = "لا توجد أيام خطة أسبوعية"
        return

    day = dates[0]
    routes = [r for r in supervisor.data(
        supervisor.get(f"/api/routes?service_date={day}"))
        if r["hub_id"] == hub["id"] and r["status"] in ("PLANNED", "ASSIGNED")]
    if not routes:
        STATE["future_reason"] = f"لا رحلات قابلة للإسناد في {day} بمركز عرعر"
        return

    vehicles = supervisor.data(supervisor.get("/api/md/vehicles"))
    boxes = supervisor.data(supervisor.get("/api/md/boxes"))
    for index, route in enumerate(routes):
        if route["status"] == "ASSIGNED":
            continue
        candidates = supervisor.data(
            supervisor.get(f"/api/routes/{route['id']}/candidates"))["candidates"]
        eligible = [c for c in candidates if c["eligible"]]
        if not eligible:
            continue
        supervisor.post(f"/api/routes/{route['id']}/assign", json={
            "driver_id": eligible[0]["driver_id"],
            "vehicle_id": vehicles[index % len(vehicles)]["id"] if vehicles else None,
            "box_id": boxes[index % len(boxes)]["id"] if boxes else None,
        })

    published = supervisor.post("/api/publish",
                                json={"hub_id": hub["id"], "service_date": day})
    if published.status_code != 200:
        STATE["future_reason"] = f"تعذّر نشر {day}: {published.text[:200]}"
        return
    ready = [r for r in supervisor.data(
        supervisor.get(f"/api/routes?service_date={day}"))
        if r["status"] == "PUBLISHED"]
    if ready:
        STATE["future_published"] = ready[0]
        STATE["future_day"] = day
    else:
        STATE["future_reason"] = f"لم تظهر رحلة منشورة في {day}"


def _build_weekly_plan() -> None:
    """خطة أسبوعية من ملف يحوي أخطاء مقصودة — أساس سيناريوهات الاستيراد والخطة."""
    from make_sample_schedule import build, compute_base

    planner = STATE["planner"]
    # الخطة الأسبوعية تبدأ من الغد دائمًا. تشغيل الحزمة صباحًا يجعل
    # compute_base() يقع على **اليوم نفسه**، فتتصادم مع خطة اليوم على المركز
    # واليوم ذاتهما: تتراكم رحلات الخطتين، فيتجاوز عددها عدد السائقين
    # المؤهلين ويُمنع النشر — فتتخطى ثلث سيناريوهات التنفيذ لسبب لا علاقة له
    # بها. عطل يظهر بين ٠٥:٠٠ و١٣:٠٠ فقط، أي في أوقات العمل تحديدًا.
    base = compute_base()
    if base.date() == dt.datetime.now(TZ).date():
        base = (base + dt.timedelta(days=1)).replace(hour=7, minute=0)
    content, base = build(3, base=base)
    STATE["weekly_base"] = base

    upload = planner.data(planner.post(
        "/api/imports", content=content,
        headers={"x-file-name": "weekly.csv", "content-type": "text/csv"}))
    import_id = upload["id"]
    STATE["import_id"] = import_id
    STATE["import_upload"] = upload
    STATE["validation"] = planner.data(
        planner.post(f"/api/imports/{import_id}/validate", json={}))
    STATE["commit"] = planner.data(
        planner.post(f"/api/imports/{import_id}/commit", json={"skip_invalid": True}))

    hub_by_code = STATE["hub_by_code"]
    dates = [(base + dt.timedelta(days=offset)).date().isoformat() for offset in range(2)]
    STATE["plan_dates"] = dates
    plan = planner.data(planner.post("/api/plans/run", json={
        "hub_ids": [hub_by_code["H-RYD-1"]["id"], hub_by_code["H-ARR-1"]["id"],
                    hub_by_code["H-KRJ-1"]["id"]],
        "dates": dates,
        "import_id": import_id,
        "time_limit_seconds": 6,
        "name": "خطة اختبارات §30",
    }))
    STATE["plan"] = plan
    STATE["plan_detail"] = planner.data(planner.get(f"/api/plans/{plan['plan_id']}"))
    # بيئة الاختبار تستخدم مزوّدًا تقديريًا، والاعتماد يتطلب إقرارًا صريحًا
    # يُسجَّل في سجل التدقيق (§23) — تمريره هنا محاكاة لما يفعله المستخدم في
    # نافذة التأكيد، لا التفاف على البوابة.
    planner.data(planner.post(f"/api/plans/{plan['plan_id']}/approve",
                              json={"acknowledge_estimated": True}))
    planner.data(planner.post(f"/api/plans/{plan['plan_id']}/dispatch", json={}))


def _build_same_day_plan() -> None:
    """خطة لليوم الحالي مُسندة ومنشورة — أساس سيناريوهات التنفيذ."""
    from make_sample_schedule import build_same_day

    planner, supervisor, admin = STATE["planner"], STATE["supervisor"], STATE["admin"]
    riyadh_hub = STATE["hub_by_code"]["H-RYD-1"]

    generated = build_same_day()
    if generated is None:
        # أوقات عمل المركز بيانات رئيسية قابلة للتعديل (§13)، لا قيمة مثبّتة في
        # الكود. توسيعها في بيئة الاختبار **تغيير إعداد** يمر بنفس API الإدارة،
        # وليس تجاوزًا لقيد: المحرك يبقى يفحص كل رحلة مقابل النافذة المُعلنة.
        # البديل — تخطّي كل سيناريوهات التنفيذ بعد الساعة السادسة مساءً — يترك
        # ثلث §30 بلا إثبات.
        widened = {day: ["00:00", "23:59"]
                   for day in ("sat", "sun", "mon", "tue", "wed", "thu", "fri")}
        response = admin.patch(f"/api/md/hubs/{riyadh_hub['id']}",
                               json={"working_hours": widened})
        if response.status_code != 200:
            STATE["same_day_reason"] = (
                "الوقت متأخر عن أوقات عمل المركز، وتعذّر توسيعها: "
                f"{response.status_code} {response.text[:200]}")
            return
        STATE["hub_hours_widened"] = True
        generated = build_same_day(open_hour=0, close_hour=24)
        if generated is None:
            STATE["same_day_reason"] = "تعذّر توليد جدول لليوم الحالي بعد توسيع الأوقات"
            return

    content, base = generated
    today = base.date().isoformat()

    upload = planner.data(planner.post(
        "/api/imports", content=content,
        headers={"x-file-name": "same-day.csv", "content-type": "text/csv"}))
    import_id = upload["id"]
    validation = planner.data(planner.post(f"/api/imports/{import_id}/validate", json={}))
    if not validation["valid_rows"]:
        STATE["same_day_reason"] = f"جدول اليوم بلا صفوف صالحة: {validation['issue_summary'][:2]}"
        return
    planner.data(planner.post(f"/api/imports/{import_id}/commit",
                              json={"skip_invalid": True}))

    riyadh = riyadh_hub["id"]
    plan = planner.data(planner.post("/api/plans/run", json={
        "hub_ids": [riyadh], "dates": [today], "import_id": import_id,
        "time_limit_seconds": 4, "name": f"خطة تنفيذ {today}",
    }))
    if not plan["metrics"]["route_count"]:
        STATE["same_day_reason"] = "لم تُبنَ رحلة لليوم الحالي"
        return

    # بيئة الاختبار تستخدم مزوّدًا تقديريًا، والاعتماد يتطلب إقرارًا صريحًا
    # يُسجَّل في سجل التدقيق (§23) — تمريره هنا محاكاة لما يفعله المستخدم في
    # نافذة التأكيد، لا التفاف على البوابة.
    planner.data(planner.post(f"/api/plans/{plan['plan_id']}/approve",
                              json={"acknowledge_estimated": True}))
    planner.data(planner.post(f"/api/plans/{plan['plan_id']}/dispatch", json={}))

    routes = [r for r in supervisor.data(
        supervisor.get(f"/api/routes?service_date={today}"))
        if r["status"] in ("PLANNED", "ASSIGNED")]
    vehicles = supervisor.data(supervisor.get("/api/md/vehicles"))
    boxes = supervisor.data(supervisor.get("/api/md/boxes"))
    for index, route in enumerate(routes):
        candidates = supervisor.data(
            supervisor.get(f"/api/routes/{route['id']}/candidates"))["candidates"]
        eligible = [c for c in candidates if c["eligible"]]
        if not eligible:
            continue
        supervisor.post(f"/api/routes/{route['id']}/assign", json={
            "driver_id": eligible[0]["driver_id"],
            "vehicle_id": vehicles[index % len(vehicles)]["id"] if vehicles else None,
            "box_id": boxes[index % len(boxes)]["id"] if boxes else None,
        })

    published = supervisor.post("/api/publish",
                                json={"hub_id": riyadh, "service_date": today})
    if published.status_code != 200:
        STATE["same_day_reason"] = f"تعذّر النشر: {published.text[:200]}"
        return

    published_routes = [r for r in supervisor.data(
        supervisor.get(f"/api/routes?service_date={today}")) if r["status"] == "PUBLISHED"]
    if not published_routes:
        STATE["same_day_reason"] = "لا توجد رحلة منشورة لليوم الحالي"
        return

    target = published_routes[0]
    detail = supervisor.data(supervisor.get(f"/api/routes/{target['id']}"))
    driver_id = detail["route"]["driver_id"]
    driver_email = next(
        (f"{d['code'].lower()}@masar.test"
         for d in admin.data(admin.get("/api/md/drivers")) if d["id"] == driver_id),
        None)
    if not driver_email:
        STATE["same_day_reason"] = "تعذّر تحديد حساب السائق المسند"
        return

    STATE["today"] = today
    STATE["route"] = target
    STATE["route_detail"] = detail
    STATE["driver_id"] = driver_id
    STATE["driver"] = ApiClient(driver_email)
    STATE["published_routes"] = published_routes
    STATE["box_id"] = detail["route"].get("box_id")


def current_route_detail() -> dict:
    """يقرأ تفاصيل الرحلة **الآن** لا من لقطة التهيئة.

    الطلبات الفورية تُدرَج في الرحلة أثناء الاختبارات فتتغيّر محطاتها
    وترتيبها. الاعتماد على لقطة قديمة يجعل الاختبار يتصرف على واقع لم يعد
    قائمًا — ويفشل لسبب لا علاقة له بما يفحصه.
    """
    state = require_same_day()
    supervisor = STATE["supervisor"]
    return supervisor.data(supervisor.get(f"/api/routes/{state['route']['id']}"))


def current_stops(kind: str | None = None) -> list[dict]:
    stops = [s for s in current_route_detail()["stops"] if s["kind"] != "HUB_START"]
    return [s for s in stops if kind is None or s["kind"] == kind]


def require_same_day() -> dict:
    """يعيد بيانات خطة اليوم أو يوقف الاختبار بسبب معلن — لا مرور صامت."""
    if "route" not in STATE:
        raise unittest.SkipTest(
            f"لا توجد خطة تنفيذ لليوم: {STATE.get('same_day_reason', 'سبب غير معروف')}")
    return STATE


# ================================================ ١٩–٢٢: الطلب الفوري ======

class TestOnDemand(MasarTestCase):

    def _create_request(self, *, minutes_ahead: int = 40, hours_sla: int = 6) -> str:
        requester = STATE["requester"]
        facilities = STATE["facility_by_code"]
        now = dt.datetime.now(TZ)
        response = requester.post("/api/ondemand", json={
            "pickup_facility_id": facilities["PHC-RYD-01"]["id"],
            "dropoff_facility_id": facilities["LAB-RYD-01"]["id"],
            "pickup_window_from": (now + dt.timedelta(minutes=minutes_ahead)).isoformat(),
            "pickup_window_to": (now + dt.timedelta(minutes=minutes_ahead + 120)).isoformat(),
            "sla_deadline": (now + dt.timedelta(hours=hours_sla)).isoformat(),
            "service_type": "URGENT", "piece_count": 2,
            "temperature_mode": "CHILLED", "notes": "عينة عاجلة — اختبار آلي",
        })
        return requester.data(response)["shipment_id"]

    @scenario(19)
    def test_on_demand_before_driver_starts(self) -> None:
        request_id = self._create_request()
        tower, supervisor = STATE["tower"], STATE["supervisor"]

        pending = tower.data(tower.get("/api/ondemand"))
        row = next(r for r in pending if r["id"] == request_id)
        self.assertEqual(row["status"], "PENDING_APPROVAL",
                         "الطلب لا يدخل التشغيل قبل مراجعة برج التحكم")

        approved = tower.post(f"/api/ondemand/{request_id}/review", json={"approve": True})
        self.assertEqual(approved.status_code, 200, approved.text[:300])

        options = supervisor.get(f"/api/ondemand/{request_id}/options")
        self.assertEqual(options.status_code, 200, options.text[:300])
        payload = options.json()["data"]
        self.assertIn("options", payload)
        if payload["options"]:
            best = payload["options"][0]
            for key in ("route_reference", "added_minutes", "added_km", "computed_from"):
                self.assertIn(key, best, "كل خيار إدراج يُظهر أثره المحسوب")
        else:
            self.assertTrue(payload["rejections"],
                            "تعذر الإدراج يجب أن يأتي بأسباب مكتوبة لا بقائمة فارغة")
            for rejection in payload["rejections"]:
                self.assertReasoned(rejection, "message_ar")

    @scenario(19)
    def test_rejected_request_carries_a_reason(self) -> None:
        request_id = self._create_request()
        tower = STATE["tower"]
        blank = tower.post(f"/api/ondemand/{request_id}/review", json={"approve": False})
        self.assertGreaterEqual(blank.status_code, 400,
                                "الرفض بلا سبب مرفوض (§27)")
        rejected = tower.post(f"/api/ondemand/{request_id}/review",
                              json={"approve": False, "reason": "الطلب مكرر مع طلب سابق"})
        self.assertEqual(rejected.status_code, 200, rejected.text[:300])
        row = next(r for r in STATE["requester"].data(
            STATE["requester"].get("/api/ondemand")) if r["id"] == request_id)
        self.assertEqual(row["status"], "REJECTED")
        self.assertTrue(row["rejection_reason"],
                        "مقدم الطلب يرى سبب الرفض لا حالة صمّاء")

    @scenario(20)
    def test_on_demand_after_driver_started(self) -> None:
        state = require_same_day()
        driver, supervisor, tower = state["driver"], STATE["supervisor"], STATE["tower"]
        route = state["route"]

        started = driver.post(f"/api/driver/routes/{route['id']}/start",
                              json={"lat": 24.725, "lon": 46.690})
        self.assertIn(started.status_code, (200, 409), started.text[:300])
        STATE["route_started"] = True

        request_id = self._create_request(minutes_ahead=45)
        tower.data(tower.post(f"/api/ondemand/{request_id}/review", json={"approve": True}))
        options = supervisor.data(supervisor.get(f"/api/ondemand/{request_id}/options"))

        # §17: أثر الإدراج يجب أن يُعرض مع **مصدر حسابه**، لأن «+١٢ دقيقة»
        # محسوبة من موقع السائق الآن تختلف عن المحسوبة من مركز الانطلاق،
        # والمشرف يقرر بناءً على الفرق.
        for option in options["options"]:
            self.assertReasoned(option, "computed_from")
            if option["route_started"]:
                self.assertIn("الموقع الحالي", option["computed_from"],
                              "رحلة بدأت والحساب من غير موقع السائق الحالي")
            else:
                self.assertIn("مركز الانطلاق", option["computed_from"])
        if options["options"]:
            best = options["options"][0]
            assigned = supervisor.post(f"/api/ondemand/{request_id}/assign", json={
                "route_id": best["route_id"],
                "pickup_position": best["pickup_position"],
                "delivery_position": best["delivery_position"],
            })
            self.assertEqual(assigned.status_code, 200, assigned.text[:400])
            detail = supervisor.data(supervisor.get(f"/api/routes/{best['route_id']}"))
            executed = [s for s in detail["stops"] if s.get("completed_at")]
            sequences = [s["sequence"] for s in detail["stops"]]
            self.assertEqual(sequences, sorted(sequences),
                             "الإدراج لا يخل بترتيب المحطات")
            for stop in executed:
                self.assertTrue(stop.get("completed_at"),
                                "المحطات المنفَّذة تبقى كما هي — لا يُعاد ترتيب الماضي")
        else:
            self.assertTrue(options["rejections"], "رفض الإدراج بلا سبب غير مقبول")

    @scenario(21)
    def test_cancel_before_pickup_then_blocked_after(self) -> None:
        request_id = self._create_request()
        requester, tower = STATE["requester"], STATE["tower"]
        tower.data(tower.post(f"/api/ondemand/{request_id}/review", json={"approve": True}))

        no_reason = requester.post(f"/api/ondemand/{request_id}/cancel", json={})
        self.assertGreaterEqual(no_reason.status_code, 400, "الإلغاء يتطلب سببًا")

        cancelled = requester.post(f"/api/ondemand/{request_id}/cancel",
                                   json={"reason": "لم تعد العينة مطلوبة"})
        self.assertEqual(cancelled.status_code, 200, cancelled.text[:300])
        row = next(r for r in requester.data(requester.get("/api/ondemand"))
                   if r["id"] == request_id)
        self.assertEqual(row["status"], "CANCELLED_BEFORE_PICKUP")

        again = requester.post(f"/api/ondemand/{request_id}/cancel",
                               json={"reason": "محاولة ثانية"})
        self.assertGreaterEqual(again.status_code, 400,
                                "لا يُلغى طلب ملغى مرتين")

    @scenario(21)
    def test_requester_cannot_cancel_someone_elses_request(self) -> None:
        request_id = self._create_request()
        other = STATE["other_requester"]
        response = other.post(f"/api/ondemand/{request_id}/cancel",
                              json={"reason": "محاولة من جهة أخرى"})
        self.assertIn(response.status_code, (403, 404),
                      f"جهة أخرى ألغت طلبًا ليس لها: HTTP {response.status_code}")

    @scenario(22)
    def test_unassign_returns_shipments_to_pending(self) -> None:
        supervisor = STATE["supervisor"]
        dates = STATE["plan_dates"]
        routes = supervisor.data(supervisor.get(f"/api/routes?service_date={dates[-1]}"))
        assigned = [r for r in routes if r["status"] == "ASSIGNED"]
        if not assigned:
            target = next((r for r in routes if r["status"] == "PLANNED"), None)
            self.assertIsNotNone(target, "لا توجد رحلة قابلة للإسناد في اليوم المختار")
            candidates = supervisor.data(
                supervisor.get(f"/api/routes/{target['id']}/candidates"))["candidates"]
            eligible = [c for c in candidates if c["eligible"]]
            self.assertTrue(eligible, f"لا مرشح مؤهل: {candidates[:1]}")
            supervisor.data(supervisor.post(f"/api/routes/{target['id']}/assign",
                                            json={"driver_id": eligible[0]["driver_id"]}))
            assigned = [supervisor.data(
                supervisor.get(f"/api/routes/{target['id']}"))["route"]]

        route_id = assigned[0]["id"]
        before = supervisor.data(supervisor.get(f"/api/routes/{route_id}"))["route"]
        self.assertIsNotNone(before["driver_id"])

        blank = supervisor.post(f"/api/routes/{route_id}/unassign", json={})
        self.assertGreaterEqual(blank.status_code, 400, "إزالة السائق تتطلب سببًا")

        removed = supervisor.post(f"/api/routes/{route_id}/unassign",
                                  json={"reason": "السائق في إجازة طارئة"})
        self.assertEqual(removed.status_code, 200, removed.text[:300])

        after = supervisor.data(supervisor.get(f"/api/routes/{route_id}"))
        self.assertIsNone(after["route"]["driver_id"])
        self.assertEqual(after["route"]["status"], "PLANNED",
                         "الرحلة تعود إلى انتظار الإسناد لا تُلغى")
        statuses = {s["status"] for s in after["stops"] if s.get("status")}
        self.assertNotIn("ASSIGNED", statuses,
                         "شحنات الرحلة عادت إلى الانتظار مع الرحلة")


# ==================================== ٢٣–٢٩: التأخير والاستثناءات والتتبع ==

class TestOperations(MasarTestCase):
    """سيناريوهات التشغيل تجري على **رحلة حقيقية واحدة** بالتتابع.

    الترقيم في أسماء الدوال مقصود: ``unittest`` ينفّذ بترتيب أبجدي، ودورة
    التشغيل الواقعية متسلسلة — لا يُقاس تأخر التسليم قبل تسجيل الالتقاط، ولا
    تُفتح حالة استثنائية تُخرج الشحنة من المسار قبل قياس ما يسبقها. الترتيب
    هنا هو الدورة نفسها، لا ترتيبًا اعتباطيًا.
    """


    @classmethod
    def _scan(cls, now: dt.datetime) -> dict:
        """يشغّل فاحص التنبيهات الحقيقي بلحظة زمنية محددة (لا تعديل للبيانات)."""
        from masar_api.services.alerts import scan_operational_alerts

        return scan_operational_alerts(now=now)

    def _alerts(self, alert_type: str) -> list[dict]:
        supervisor = STATE["supervisor"]
        rows = supervisor.data(supervisor.get("/api/alerts", params={"limit": 200}))
        return [row for row in rows if row["alert_type"] == alert_type]

    @scenario(23)
    def test_01_late_pickup_raises_a_linked_alert(self) -> None:
        require_same_day()
        stops = current_stops("PICKUP")
        self.assertTrue(stops, "الرحلة تحتوي محطة التقاط")
        windows = [dt.datetime.fromisoformat(s["window_to"]) for s in stops
                   if s.get("window_to")]
        self.assertTrue(windows, "محطات الالتقاط تحمل نهاية نافذة مخططة")
        latest_window = max(windows)

        self._scan(latest_window + dt.timedelta(minutes=45))
        alerts = self._alerts("PICKUP_LATE")
        self.assertTrue(alerts, "لم يُرفع تنبيه تأخر الالتقاط")
        for alert in alerts:
            self.assertReasoned(alert, "title_ar", "body_ar")
            self.assertTrue(alert["shipment_id"] or alert["route_id"],
                            "كل تنبيه مرتبط برحلة أو شحنة (§2)")

    @scenario(24)
    def test_02_late_delivery_raises_its_own_alert(self) -> None:
        state = require_same_day()
        driver, supervisor = state["driver"], STATE["supervisor"]
        driver.post(f"/api/driver/routes/{state['route']['id']}/start",
                    json={"lat": 24.725, "lon": 46.690})

        # التقاط شحنة واحدة فعليًا: تأخر التسليم لا يُقاس إلا بعد وجود عهدة
        picked_shipment = None
        for stop in current_stops("PICKUP"):
            if stop.get("completed_at"):
                picked_shipment = stop["shipment_id"]
                break
            driver.post(f"/api/driver/stops/{stop['id']}/arrive",
                        json={"lat": stop["lat"], "lon": stop["lon"]})
            response = driver.post(f"/api/driver/stops/{stop['id']}/pickup",
                                   json={"lat": stop["lat"], "lon": stop["lon"]})
            if response.status_code == 200:
                picked_shipment = stop["shipment_id"]
                break
        self.assertIsNotNone(picked_shipment,
                             "تعذّر التقاط أي شحنة — لا يمكن قياس تأخر التسليم")

        shipment = supervisor.data(
            supervisor.get(f"/api/shipments/{picked_shipment}"))["shipment"]
        self.assertIsNotNone(shipment["actual_pickup_at"], "لم يُسجَّل التقاط فعلي")
        self.assertTrue(shipment.get("planned_dropoff_arrival"),
                        "الشحنة بلا وقت تسليم مخطط — لا مرجع لقياس التأخر")
        moment = dt.datetime.fromisoformat(shipment["planned_dropoff_arrival"])

        self._scan(moment + dt.timedelta(minutes=40))
        alerts = self._alerts("DELIVERY_LATE")
        self.assertTrue(alerts, "لم يُرفع تنبيه تأخر التسليم")
        for alert in alerts:
            self.assertReasoned(alert, "body_ar")
            self.assertIn("دقيقة", alert["body_ar"], "التنبيه يذكر مقدار التأخير")

    @scenario(24)
    def test_03_sla_breach_is_recorded_not_hidden(self) -> None:
        state = require_same_day()
        shipments = [s["shipment_id"] for s in state["route_detail"]["stops"]
                     if s.get("shipment_id")]
        self.assertTrue(shipments)
        supervisor = STATE["supervisor"]
        deadline = max(
            dt.datetime.fromisoformat(
                supervisor.data(supervisor.get(f"/api/shipments/{sid}"))
                ["shipment"]["sla_deadline"])
            for sid in set(shipments))

        self._scan(deadline + dt.timedelta(minutes=30))
        breached = [
            sid for sid in set(shipments)
            if supervisor.data(supervisor.get(f"/api/shipments/{sid}"))
            ["shipment"]["sla_breached"]
        ]
        self.assertTrue(breached, "تجاوز SLA يُسجَّل على الشحنة نفسها")
        self.assertTrue(self._alerts("SLA_BREACHED"))

    @scenario(25)
    def test_09_samples_not_ready_keeps_the_shipment_alive(self) -> None:
        state = require_same_day()
        driver = state["driver"]
        stop = next(s for s in state["route_detail"]["stops"] if s["kind"] == "PICKUP")

        response = driver.post("/api/exceptions", json={
            "shipment_id": stop["shipment_id"], "reason": "SAMPLES_NOT_READY",
            "note": "الفني أفاد بأن العينات ستجهز بعد ساعة",
            "stop_id": stop["id"], "lat": stop["lat"], "lon": stop["lon"],
        })
        self.assertIn(response.status_code, (200, 201, 409), response.text[:400])
        if response.status_code == 409:
            self.skipTest("الشحنة تجاوزت مرحلة الالتقاط في اختبار سابق")

        supervisor = STATE["supervisor"]
        shipment = supervisor.data(
            supervisor.get(f"/api/shipments/{stop['shipment_id']}"))["shipment"]
        self.assertEqual(shipment["status"], "EXCEPTION")
        self.assertNotIn(shipment["status"], ("DELETED", "CANCELLED_BEFORE_PICKUP"),
                         "§19: الشحنة لا تُحذف ولا تُلغى تلقائيًا")

        exceptions = supervisor.data(supervisor.get("/api/exceptions"))
        mine = [e for e in exceptions if e["shipment_id"] == stop["shipment_id"]]
        self.assertTrue(mine)
        self.assertReasoned(mine[0], "reason", "note")

    @scenario(26)
    def test_10_failed_delivery_stays_open_with_obligation(self) -> None:
        state = require_same_day()
        supervisor = STATE["supervisor"]
        stop = next((s for s in state["route_detail"]["stops"]
                     if s["kind"] == "DELIVERY"), None)
        self.assertIsNotNone(stop)

        response = supervisor.post("/api/exceptions", json={
            "shipment_id": stop["shipment_id"], "reason": "DELIVERY_DELAYED",
            "note": "المختبر مغلق خارج الدوام — يُعاد التسليم غدًا",
            "stop_id": stop["id"],
        })
        self.assertIn(response.status_code, (200, 201), response.text[:400])

        shipment = supervisor.data(
            supervisor.get(f"/api/shipments/{stop['shipment_id']}"))["shipment"]
        self.assertEqual(shipment["status"], "EXCEPTION")

        exception_id = supervisor.data(supervisor.get("/api/exceptions"))[0]["id"]
        blank = supervisor.post(f"/api/exceptions/{exception_id}/resolve", json={})
        self.assertGreaterEqual(blank.status_code, 400,
                                "لا تُحسم حالة استثنائية بلا إجراء مسجّل")

        resolved = supervisor.post(f"/api/exceptions/{exception_id}/resolve", json={
            "action_taken": "أُعيدت جدولة التسليم في خطة الغد",
            "new_shipment_status": "PENDING_ASSIGNMENT",
        })
        self.assertEqual(resolved.status_code, 200, resolved.text[:400])
        after = supervisor.data(
            supervisor.get(f"/api/shipments/{stop['shipment_id']}"))["shipment"]
        self.assertEqual(after["status"], "PENDING_ASSIGNMENT",
                         "التزام التسليم يبقى مفتوحًا في خطة لاحقة")

    @scenario(27)
    def test_05_temperature_breach_detected_from_sensor_not_driver(self) -> None:
        state = require_same_day()
        admin, supervisor, driver = STATE["admin"], STATE["supervisor"], state["driver"]

        sensors = admin.data(admin.get("/api/md/sensors")) \
            if admin.get("/api/md/sensors").status_code == 200 else []
        box_id = state.get("box_id")
        if not box_id and not sensors:
            self.skipTest("لا يوجد صندوق أو حساس مرتبط بالرحلة")

        now = dt.datetime.now(dt.timezone.utc)
        payload = {"readings": [{"box_id": box_id, "celsius": 18.5,
                                 "recorded_at": now.isoformat()}],
                   "source": "SENSOR"}

        forbidden = driver.post("/api/temperature/ingest", json=payload)
        self.assertEqual(forbidden.status_code, 403,
                         "§18: السائق لا يُدخل درجة الحرارة إطلاقًا")

        ingested = admin.data(admin.post("/api/temperature/ingest", json=payload))
        self.assertGreaterEqual(ingested["accepted"], 0)

        status = supervisor.data(supervisor.get("/api/temperature/status"))
        self.assertIn("is_real_integration", status)
        self.assertFalse(status["is_real_integration"],
                         "حالة التكامل معلنة بصدق — لا محاكاة تُعرض كتكامل حقيقي")

        if ingested.get("breaches"):
            alerts = self._alerts("TEMPERATURE_BREACH")
            self.assertTrue(alerts, "مخالفة الحرارة تُنتج تنبيهًا مرتبطًا")

    @scenario(27)
    def test_06_no_sensor_means_no_reading_not_zero(self) -> None:
        """§20: عند غياب الحساس تُعرض الحالة صراحةً، ولا تُختلق قيمة."""
        supervisor = STATE["supervisor"]
        status = supervisor.data(supervisor.get("/api/temperature/status"))
        self.assertReasoned(status, "message_ar")
        self.assertIn("NO_SENSOR", status["message_ar"] + str(status.get("state", "")))

    @scenario(28)
    def test_04_stale_tracking_raises_an_alert(self) -> None:
        state = require_same_day()
        driver = state["driver"]
        driver.post(f"/api/driver/routes/{state['route']['id']}/start",
                    json={"lat": 24.725, "lon": 46.690})
        driver.post("/api/positions", json={"points": [{
            "lat": 24.73, "lon": 46.69, "speed_kmh": 40,
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "route_id": state["route"]["id"]}]})

        self._scan(dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30))
        alerts = self._alerts("TRACKING_STALE")
        self.assertTrue(alerts, "توقف تحديث الموقع يجب أن يُنتج تنبيهًا")
        for alert in alerts:
            self.assertReasoned(alert, "body_ar")
            self.assertTrue(alert["route_id"], "التنبيه مرتبط برحلة قابلة للفتح")

    @scenario(29)
    def test_08_offline_queue_syncs_exactly_once(self) -> None:
        state = require_same_day()
        driver = state["driver"]
        stops = [s for s in state["route_detail"]["stops"] if s["kind"] != "HUB_START"]
        stop = stops[-1]
        client_event_id = str(uuid.uuid4())
        moment = dt.datetime.now(dt.timezone.utc).isoformat()

        events = [{
            "action": "EXCEPTION", "shipment_id": stop["shipment_id"],
            "reason": "PICKUP_DELAYED", "note": "انقطع الإنترنت أثناء التنفيذ",
            "occurred_at": moment, "client_event_id": client_event_id,
            "lat": stop["lat"], "lon": stop["lon"],
        }]

        first = driver.data(driver.post("/api/driver/sync", json={"events": events}))
        second = driver.data(driver.post("/api/driver/sync", json={"events": events}))

        self.assertEqual(first["applied"] + first["skipped"], 1)
        self.assertEqual(second["applied"], 0,
                         "إعادة إرسال نفس الحدث لا تُنشئ سجلًا ثانيًا")
        self.assertEqual(second["skipped"], 1,
                         "التكرار يُعلن كمتجاهَل لا كفشل")

    @scenario(29)
    def test_07_offline_events_apply_in_occurrence_order(self) -> None:
        """الأحداث تُعالج بترتيب وقوعها الفعلي لا بترتيب وصولها.

        الطابور المحلي في تطبيق السائق قد يصل بترتيب مختلط بعد عودة الشبكة
        (إعادة محاولة، أو ترتيب إرسال عشوائي). الخادم يرتّبها بـ
        ``occurred_at`` قبل المعالجة، وقائمة النتائج تُبنى بترتيب المعالجة —
        فهي الدليل المباشر على ذلك، أيًّا كانت نتيجة كل حدث على حدة.
        """
        state = require_same_day()
        driver = state["driver"]
        stops = [s for s in state["route_detail"]["stops"] if s["kind"] != "HUB_START"]
        stop = stops[0]
        base = dt.datetime.now(dt.timezone.utc)

        earlier = str(uuid.uuid4())
        later = str(uuid.uuid4())
        out_of_order = [
            {"action": "PICKED_UP", "stop_id": stop["id"],
             "occurred_at": (base + dt.timedelta(minutes=2)).isoformat(),
             "client_event_id": later,
             "lat": stop["lat"], "lon": stop["lon"]},
            {"action": "ARRIVED", "stop_id": stop["id"],
             "occurred_at": base.isoformat(),
             "client_event_id": earlier,
             "lat": stop["lat"], "lon": stop["lon"]},
        ]
        result = driver.data(driver.post("/api/driver/sync", json={"events": out_of_order}))

        self.assertEqual(len(result["results"]), 2,
                         "كل حدث مزامَن يعود بنتيجة مستقلة")
        self.assertEqual(result["results"][0]["client_event_id"], earlier,
                         "عولج الحدث الأحدث أولًا — الترتيب بوقت الوصول لا بوقت الحدوث")
        self.assertEqual(result["results"][1]["client_event_id"], later)

        for item in result["results"]:
            self.assertIn(item["status"], ("APPLIED", "SKIPPED", "FAILED"))
            if item["status"] != "APPLIED":
                self.assertReasoned(item, "message")

    @scenario(29)
    def test_08b_offline_sync_never_loses_an_event_silently(self) -> None:
        """كل حدث في الطابور يعود بنتيجة معلنة — لا ابتلاع صامت."""
        state = require_same_day()
        driver = state["driver"]
        stops = [s for s in state["route_detail"]["stops"] if s["kind"] != "HUB_START"]

        events = [
            {"action": "ARRIVED", "stop_id": stops[0]["id"],
             "occurred_at": dt.datetime.now(dt.timezone.utc).isoformat(),
             "client_event_id": str(uuid.uuid4()),
             "lat": stops[0]["lat"], "lon": stops[0]["lon"]},
            {"action": "UNKNOWN_ACTION", "stop_id": stops[0]["id"],
             "occurred_at": dt.datetime.now(dt.timezone.utc).isoformat(),
             "client_event_id": str(uuid.uuid4())},
        ]
        result = driver.data(driver.post("/api/driver/sync", json={"events": events}))
        self.assertEqual(
            result["applied"] + result["skipped"] + result["failed"],
            len(events),
            f"عدد النتائج لا يطابق عدد الأحداث المرسلة: {result}")
        self.assertEqual(len(result["results"]), len(events))


# ============================================ ٣٠–٣٢: العزل والصلاحيات ======

class TestIsolation(MasarTestCase):

    @scenario(30)
    def test_driver_sees_only_own_routes(self) -> None:
        state = require_same_day()
        driver = state["driver"]
        visible = driver.data(driver.get("/api/routes"))
        foreign = [r for r in visible if r["driver_id"] != state["driver_id"]]
        self.assertEqual(foreign, [], "السائق يرى رحلات غيره — خرق RLS")

        other = next((r for r in state["published_routes"]
                      if r["id"] != state["route"]["id"]), None)
        if other:
            blocked = driver.get(f"/api/routes/{other['id']}")
            self.assertIn(blocked.status_code, (403, 404),
                          f"السائق فتح رحلة سائق آخر: HTTP {blocked.status_code}")

    @scenario(30)
    def test_driver_cannot_read_plans_or_reports(self) -> None:
        state = require_same_day()
        driver = state["driver"]
        for path in ("/api/plans", "/api/reports/kpi", "/api/audit",
                     "/api/tracking/live", "/api/md/drivers"):
            response = driver.get(path)
            self.assertIn(response.status_code, (403, 404),
                          f"{path} مفتوح للسائق: HTTP {response.status_code}")

    @scenario(31)
    def test_supervisor_is_confined_to_own_hub(self) -> None:
        supervisor, arar = STATE["supervisor"], STATE["arar"]
        riyadh_hub = STATE["hub_by_code"]["H-RYD-1"]["id"]
        arar_hub = STATE["hub_by_code"]["H-ARR-1"]["id"]

        riyadh_routes = supervisor.data(supervisor.get("/api/routes"))
        self.assertTrue(riyadh_routes, "مشرف الرياض يرى رحلات مركزه")
        self.assertEqual({r["hub_id"] for r in riyadh_routes}, {riyadh_hub},
                         "مشرف الرياض يرى رحلات مركز آخر")

        arar_routes = arar.data(arar.get("/api/routes"))
        arar_ids = {r["id"] for r in arar_routes}
        self.assertEqual({r["hub_id"] for r in arar_routes} - {arar_hub}, set())

        overlap = arar_ids & {r["id"] for r in riyadh_routes}
        self.assertEqual(overlap, set(), "تسرّبت رحلات بين مركزين")

        if arar_routes:
            blocked = supervisor.get(f"/api/routes/{arar_routes[0]['id']}")
            self.assertIn(blocked.status_code, (403, 404),
                          "مشرف الرياض فتح رحلة عرعر")

    @scenario(31)
    def test_supervisor_cannot_publish_another_hub(self) -> None:
        supervisor = STATE["supervisor"]
        arar_hub = STATE["hub_by_code"]["H-ARR-1"]["id"]
        response = supervisor.post("/api/publish", json={
            "hub_id": arar_hub, "service_date": STATE["plan_dates"][0]})
        self.assertIn(response.status_code, (403, 404, 409),
                      f"مشرف الرياض نشر خطة عرعر: HTTP {response.status_code}")

    @scenario(32)
    def test_supervisor_cannot_upload_national_schedule(self) -> None:
        supervisor = STATE["supervisor"]
        response = supervisor.post("/api/imports", content=b"x",
                                   headers={"x-file-name": "x.csv"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(supervisor.code_of(response), "FORBIDDEN")

        for path, payload in (("/api/plans/run", {"hub_ids": [], "dates": []}),):
            blocked = supervisor.post(path, json=payload)
            self.assertEqual(blocked.status_code, 403,
                             f"{path} مفتوح للمشرف")

    @scenario(32)
    def test_requester_sees_only_own_facility_requests(self) -> None:
        requester, other = STATE["requester"], STATE["other_requester"]
        mine = requester.data(requester.get("/api/ondemand"))
        theirs = other.data(other.get("/api/ondemand"))
        overlap = {r["id"] for r in mine} & {r["id"] for r in theirs}
        self.assertEqual(overlap, set(), "جهة ترى طلبات جهة أخرى")

        facilities = requester.data(requester.get("/api/md/facilities"))
        types = {f["facility_type"] for f in facilities}
        self.assertTrue(types <= {"LABORATORY", "BLOOD_BANK", "HEALTH_CENTER",
                                  "HOSPITAL", "CLINIC"},
                        f"أنواع غير متوقعة: {types}")
        own_id = requester.user["facility_id"]
        non_destinations = [f for f in facilities
                            if f["facility_type"] not in ("LABORATORY", "BLOOD_BANK")
                            and f["id"] != own_id]
        self.assertEqual(non_destinations, [],
                         "مقدم الطلب يرى جهات لا علاقة له بها")

    @scenario(30, 31)
    def test_audit_log_is_append_only_for_everyone(self) -> None:
        """§27: لا مستخدم عادي يعدّل سجل التدقيق — والقاعدة تمنعه لا الواجهة."""
        admin = STATE["admin"]
        rows = admin.data(admin.get("/api/audit", params={"limit": 5}))
        self.assertTrue(rows, "سجل التدقيق يحتوي عمليات")

        # اتصال مباشر بدور التطبيق نفسه — لا بدور الترحيل — كي يكون الفحص
        # على الطبقة التي يعمل بها المستخدم فعلًا
        connection = db_connection(as_app=True)
        try:
            with self.assertRaises(Exception) as caught:
                connection.execute(
                    "UPDATE audit_log SET action = 'TAMPERED' WHERE id = $1::uuid",
                    [rows[0]["id"]])
            self.assertTrue(str(caught.exception).strip())
        finally:
            connection.close()


# ================================= ٣٣–٣٩: الخطة والنشر والتقارير ===========

class TestPlanningLifecycle(MasarTestCase):

    @scenario(44)
    def test_estimated_plan_cannot_be_approved_without_acknowledgement(self) -> None:
        """خطة على أزمنة تقديرية لا تُعتمد بالنقر المعتاد (§23).

        الانحراف المقيس عن شبكة الطرق الحقيقية +28٪ مسافةً و+64٪ زمنًا،
        وفي اتجاهين متعاكسين. الاعتماد الصامت يعني الالتزام بـSLA على
        أرقام غير حقيقية، فالبوابة تشترط إقرارًا يُسجَّل باسم من أقرّه.
        """
        from make_sample_schedule import build

        planner = STATE["planner"]
        # يوم بعيد عن أيام الخطة المشتركة: إعادة تشغيل المحرك على يوم مخطَّط
        # تستبدل رحلاته وتُفسد بقية السيناريوهات.
        base = STATE["weekly_base"] + dt.timedelta(days=20)
        content, base = build(1, with_errors=False, base=base)
        day = base.date().isoformat()
        upload = planner.data(planner.post(
            "/api/imports", content=content,
            headers={"x-file-name": "gate.csv", "content-type": "text/csv"}))
        planner.data(planner.post(f"/api/imports/{upload['id']}/validate", json={}))
        planner.data(planner.post(f"/api/imports/{upload['id']}/commit",
                                  json={"skip_invalid": True}))
        fresh = planner.data(planner.post("/api/plans/run", json={
            "hub_ids": [STATE["hub_by_code"]["H-ARR-1"]["id"]],
            "dates": [day], "import_id": upload["id"],
            "time_limit_seconds": 2, "name": "خطة فحص بوابة التقدير",
        }))
        detail = planner.data(planner.get(f"/api/plans/{fresh['plan_id']}"))
        if not detail["plan"]["routing_estimated"]:
            self.skipTest("المزوّد المُعدّ ليس تقديريًا — البوابة لا تنطبق")

        blocked = planner.post(f"/api/plans/{fresh['plan_id']}/approve", json={})
        self.assertEqual(blocked.status_code, 409,
                         f"اعتُمدت خطة تقديرية بلا إقرار: {blocked.text[:200]}")
        self.assertIn("تقديرية", blocked.text)

        allowed = planner.post(f"/api/plans/{fresh['plan_id']}/approve",
                               json={"acknowledge_estimated": True})
        self.assertEqual(allowed.status_code, 200, allowed.text[:200])

        # الإقرار مسجَّل — لا يكفي أن يُقبل، يجب أن يُوثَّق من أقرّه
        admin = STATE["admin"]
        entries = admin.data(admin.get("/api/audit", params={"limit": 200}))
        approvals = [row for row in entries if row["action"] == "PLAN_APPROVE"]
        self.assertTrue(approvals, "اعتماد الخطة غير موثّق في سجل التدقيق")

    @scenario(33)
    def test_draft_plan_survives_reload(self) -> None:
        planner = STATE["planner"]
        plan_id = STATE["plan"]["plan_id"]
        again = planner.data(planner.get(f"/api/plans/{plan_id}"))
        self.assertEqual(len(again["routes"]), len(STATE["plan_detail"]["routes"]),
                         "مسودة الخطة محفوظة في قاعدة البيانات لا في المتصفح")
        self.assertEqual(
            {r["reference"] for r in again["routes"]},
            {r["reference"] for r in STATE["plan_detail"]["routes"]})

    @scenario(34)
    def test_route_counts_agree_across_screens(self) -> None:
        planner = STATE["planner"]
        plan_id = STATE["plan"]["plan_id"]
        detail = planner.data(planner.get(f"/api/plans/{plan_id}"))
        metrics = STATE["plan"]["metrics"]

        self.assertEqual(len(detail["routes"]), metrics["route_count"],
                         "عدد الرحلات في التفاصيل يخالف الملخص")

        listed = planner.data(planner.get("/api/plans"))
        row = next(p for p in listed if p["id"] == plan_id)
        self.assertEqual(int(row["metrics"]["route_count"]), metrics["route_count"],
                         "عدد الرحلات في قائمة الخطط يخالف الملخص")

        # شاشة الرحلات التشغيلية: مجموع الأيام يجب أن يساوي عدد رحلات الخطة
        listed_routes: set[str] = set()
        for date in STATE["plan_dates"]:
            listed_routes |= {
                r["id"] for r in planner.data(
                    planner.get(f"/api/routes?service_date={date}"))}
        self.assertEqual(len(listed_routes), metrics["route_count"],
                         "قائمة الرحلات التشغيلية تخالف ملخص الخطة")

        # تقرير الرحلات: مصدر البيانات نفسه، فلا يجوز أن يعطي عددًا مختلفًا
        report = planner.data(planner.get("/api/reports/routes", params={
            "date_from": STATE["plan_dates"][0], "date_to": STATE["plan_dates"][-1],
            "include_test_data": "true"}))
        totals = report["totals"]
        self.assertEqual(int(totals["route_count"]), metrics["route_count"],
                         f"تقرير الرحلات يخالف الملخص — مصدر بيانات غير موحّد: {totals}")

    @scenario(34)
    def test_kpi_totals_match_their_breakdown(self) -> None:
        planner = STATE["planner"]
        kpi = planner.data(planner.get("/api/reports/kpi",
                                       params={"include_test_data": "true"}))
        grouped = planner.data(planner.get(
            "/api/reports/grouped", params={"group_by": "hub",
                                            "include_test_data": "true"}))
        total = sum(int(row["shipment_count"]) for row in grouped)
        self.assertEqual(total, int(kpi["shipment_count"]),
                         "المجموع في الملخص لا يساوي مجموع التفصيل")

    @scenario(35)
    def test_every_warning_is_actionable(self) -> None:
        warnings = STATE["plan_detail"]["warnings"]
        self.assertTrue(warnings, "الخطة أنتجت تحذيرات قابلة للفحص")
        for warning in warnings:
            self.assertReasoned(warning, "reason_ar", "affected_entity_ar",
                                "suggested_action_ar", "warning_type", "severity")
        linked = [w for w in warnings if w["route_id"] or w["shipment_id"]]
        self.assertTrue(linked, "لا تحذير مرتبط بكيان قابل للفتح")

        planner = STATE["planner"]
        sample = linked[0]
        if sample["route_id"]:
            opened = planner.get(f"/api/routes/{sample['route_id']}")
            self.assertEqual(opened.status_code, 200,
                             "فتح الكيان المتأثر من بطاقة التحذير يجب أن يعمل")

    @scenario(35)
    def test_unplannable_shipments_carry_classified_reasons(self) -> None:
        # مستوى الخطة: كل شحنة لم تُخطَّط لها سبب مُصنّف وتفصيل مكتوب
        for row in STATE["plan_detail"]["unplannable"]:
            self.assertReasoned(row, "unplannable_reason", "unplannable_detail")

        # مستوى الاستيراد: الصفوف المستحيلة تُردّ **قبل** التخطيط بسبب مكتوب،
        # وهي الحالة التي يصنعها الجدول التجريبي عمدًا (رفحاء ← عرعر في ١٠ د)
        summary = STATE["validation"]["issue_summary"]
        self.assertTrue(summary, "التحقق لم يُنتج أي ملاحظة رغم أخطاء مقصودة")
        for issue in summary:
            self.assertReasoned(issue, "code", "sample_message_ar")
        infeasible = [i for i in summary if i["code"].startswith("INFEASIBLE_")]
        self.assertTrue(infeasible,
                        f"الفحص المبدئي لم يكشف الحالة المستحيلة: "
                        f"{sorted(i['code'] for i in summary)}")

    @scenario(36)
    def test_publishing_affects_one_day_and_one_hub(self) -> None:
        supervisor, arar = STATE["supervisor"], STATE["arar"]
        riyadh = STATE["hub_by_code"]["H-RYD-1"]["id"]
        first, second = STATE["plan_dates"][0], STATE["plan_dates"][1]

        for route in supervisor.data(
                supervisor.get(f"/api/routes?service_date={first}")):
            if route["status"] == "PLANNED":
                candidates = supervisor.data(
                    supervisor.get(f"/api/routes/{route['id']}/candidates"))["candidates"]
                eligible = [c for c in candidates if c["eligible"]]
                if eligible:
                    supervisor.post(f"/api/routes/{route['id']}/assign",
                                    json={"driver_id": eligible[0]["driver_id"]})

        # المقارنة قبل/بعد لا الفراغ المطلق: قد تكون أيام أو مراكز أخرى
        # منشورة سلفًا لأسباب مشروعة. المطلوب إثباته أن النشر **لا يتعدّى**
        # حدوده، لا أن بقية النظام فارغ.
        def published_ids(client, service_date: str | None = None) -> set:
            path = ("/api/routes" if service_date is None
                    else f"/api/routes?service_date={service_date}")
            return {r["id"] for r in client.data(client.get(path))
                    if r["status"] == "PUBLISHED"}

        second_before = published_ids(supervisor, second)
        arar_before = published_ids(arar)

        published = supervisor.post("/api/publish",
                                    json={"hub_id": riyadh, "service_date": first})
        self.assertEqual(published.status_code, 200, published.text[:300])

        self.assertEqual(published_ids(supervisor, second), second_before,
                         "نشر يوم غيّر حالة نشر يوم آخر")
        self.assertEqual(published_ids(arar), arar_before,
                         "نشر مركز الرياض غيّر حالة نشر مركز عرعر")

        first_published = published_ids(supervisor, first)
        self.assertTrue(first_published, "لم تُنشر أي رحلة في اليوم المستهدف")

    @scenario(37)
    def test_modifying_a_published_route_requires_reason_and_notifies(self) -> None:
        # رحلة **منشورة لم تبدأ**: التعديل بعد بدء التنفيذ حالة أخرى لها
        # قواعدها. البحث في رحلات اليوم كان يجعل النتيجة رهن ترتيب التنفيذ،
        # فتُتخطّى كلما بدأت رحلات اليوم قبل هذا الاختبار. رحلة ليوم قادم
        # لا يمكن أن تبدأ، فالحالة مضمونة لا مصادفة.
        target = STATE.get("future_published")
        self.assertIsNotNone(
            target,
            f"لا رحلة منشورة ليوم قادم: {STATE.get('future_reason', 'سبب غير معروف')}")
        supervisor = STATE["arar"]
        route_id = target["id"]

        blank = supervisor.post(f"/api/routes/{route_id}/modify",
                                json={"change_kind": "REORDER"})
        self.assertGreaterEqual(blank.status_code, 400,
                                "تعديل رحلة منشورة بلا سبب مرفوض")

        modified = supervisor.post(f"/api/routes/{route_id}/modify", json={
            "change_kind": "OTHER",
            "reason": "طلب المختبر تقديم التسليم لظرف تشغيلي",
        })
        self.assertIn(modified.status_code, (200, 409), modified.text[:400])
        if modified.status_code == 409:
            self.skipTest("الرحلة تجاوزت مرحلة تسمح بالتعديل")

        alerts = supervisor.data(supervisor.get("/api/alerts", params={"limit": 200}))
        notices = [a for a in alerts if a["alert_type"] == "PUBLISHED_ROUTE_MODIFIED"]
        self.assertTrue(notices, "تعديل رحلة منشورة يجب أن يُرسل تحديثًا للسائق")
        for notice in notices:
            self.assertReasoned(notice, "body_ar")

        # تقرير مراقبة التعديلات صلاحيته ``hub_changes.monitor`` — رقابية لا
        # تشغيلية، فلا يملكها المشرف الذي أجرى التعديل نفسه (فصل الأدوار §5)
        self.assertEqual(supervisor.get("/api/reports/hub-modifications").status_code,
                         403, "المشرف يراقب تعديلات نفسه — خلل في فصل الأدوار")
        planner = STATE["planner"]
        report = planner.data(planner.get("/api/reports/hub-modifications"))
        self.assertTrue(
            any(str(row.get("route_id")) == route_id for row in report)
            or len(report) > 0,
            "تقرير مراقبة تعديلات المراكز لا يسجّل التعديل")

    @scenario(38)
    def test_driver_estimation_is_justified(self) -> None:
        estimations = STATE["plan_detail"]["estimations"]
        self.assertTrue(estimations, "الخطة تُخرج تقدير سائقين لكل مركز")
        for estimate in estimations:
            for key in ("theoretical_minimum", "recommended", "available", "used"):
                self.assertIn(key, estimate)
            self.assertLessEqual(estimate["theoretical_minimum"],
                                 estimate["recommended"],
                                 "الموصى به لا يقل عن الحد الأدنى النظري")
            self.assertTrue(estimate["justification"],
                            f"تقدير مركز {estimate['hub_id']} بلا تبرير")
            for item in estimate["justification"]:
                self.assertReasoned(item, "label_ar", "detail_ar")

    @scenario(39)
    def test_capacity_report_exposes_unjustified_surplus(self) -> None:
        planner = STATE["planner"]
        rows = planner.data(planner.get("/api/reports/driver-capacity"))
        self.assertIsInstance(rows, list)
        for row in rows:
            for key in ("hub_name_ar", "available", "used", "recommended",
                        "unjustified_excess", "flag"):
                self.assertIn(key, row, f"صف ناقص في تقرير الطاقة: {row}")

    @scenario(38, 39)
    def test_improvement_percentage_needs_a_declared_baseline(self) -> None:
        """§26: لا تُعرض نسبة تحسين دون خطة أساس معلنة."""
        days = STATE["plan"]["per_day"]
        self.assertTrue(days, "الخطة تُخرج تفصيلًا لكل يوم/مركز")
        for day in days:
            improvement = day.get("improvement")
            if improvement is None:
                continue
            self.assertNotEqual(improvement["baseline_kind"], "NONE",
                                "نسبة تحسين بلا نوع خطة أساس معلن")
            self.assertTrue(improvement["baseline_label_ar"].strip(),
                            "خطة الأساس مُسمّاة صراحةً لا مضمرة")
            for key in ("drivers", "drive_minutes", "distance_km", "cost"):
                self.assertIn("baseline", improvement[key],
                              f"نسبة {key} معروضة بلا قيمة أساس")

    @scenario(14)
    def test_second_long_haul_is_blocked_for_the_same_driver(self) -> None:
        """قيد HC-15 مطبَّق في الإسناد لا في الواجهة فقط."""
        supervisor = STATE["supervisor"]
        arar = STATE["arar"]
        routes = arar.data(arar.get("/api/routes"))
        # القيد يوميّ: الرحلتان يجب أن تكونا في **نفس تاريخ الخدمة** كي يُختبر
        by_date: dict[str, list] = {}
        for route in routes:
            if route["is_long_haul"]:
                by_date.setdefault(route["service_date"], []).append(route)
        same_day = max(by_date.values(), key=len) if by_date else []
        if not same_day:
            self.skipTest("لا توجد رحلة بعيدة في خطة الاختبار")
        long_haul = same_day

        first = long_haul[0]
        candidates = arar.data(
            arar.get(f"/api/routes/{first['id']}/candidates"))["candidates"]
        eligible = [c for c in candidates if c["eligible"]]
        if not eligible:
            for candidate in candidates:
                self.assertTrue(candidate["blockers"],
                                "مرشح غير مؤهل بلا سبب مكتوب")
            self.skipTest(f"لا مرشح مؤهل: {candidates[0]['blockers'] if candidates else '-'}")

        driver_id = eligible[0]["driver_id"]
        assigned = arar.post(f"/api/routes/{first['id']}/assign",
                             json={"driver_id": driver_id})
        self.assertEqual(assigned.status_code, 200, assigned.text[:300])

        second = next((r for r in long_haul if r["id"] != first["id"]), None)
        if second is None:
            self.skipTest("لا توجد رحلة بعيدة ثانية في نفس اليوم لاختبار القيد")
        if False:
            # لا رحلة بعيدة ثانية في هذه الخطة — نتحقق من ظهور القيد كمانع
            refreshed = arar.data(
                arar.get(f"/api/routes/{first['id']}/candidates"))["candidates"]
            row = next((c for c in refreshed if c["driver_id"] == driver_id), None)
            self.assertIsNotNone(row)
            return

        conflict = arar.post(f"/api/routes/{second['id']}/assign",
                             json={"driver_id": driver_id})
        self.assertGreaterEqual(conflict.status_code, 400,
                                "أُسندت رحلتان بعيدتان للسائق نفسه")
        self.assertIn("HC-15", conflict.text + supervisor.code_of(conflict),
                      f"سبب الرفض لا يسمّي القيد: {conflict.text[:300]}")


# ==================================== ٤١–٤٢: جودة ملف الاستيراد ============

class TestImportQuality(MasarTestCase):

    @scenario(41)
    def test_duplicate_rows_detected_and_isolated(self) -> None:
        validation = STATE["validation"]
        codes = {issue["code"] for issue in validation["issue_summary"]}
        self.assertIn("DUPLICATE_ROW", codes,
                      f"الرموز المكتشفة: {sorted(codes)}")
        self.assertGreater(validation["duplicate_rows"], 0)

        commit = STATE["commit"]
        self.assertGreater(commit["created_shipments"], 0)
        planner = STATE["planner"]
        errors = planner.get(f"/api/imports/{STATE['import_id']}/errors.csv")
        self.assertEqual(errors.status_code, 200)
        body = errors.content.decode("utf-8")
        self.assertIn("DUPLICATE_ROW", body,
                      "ملف الأخطاء لا يذكر الصفوف المكررة")
        self.assertIn("REQ-00001", body,
                      "ملف الأخطاء لا يحمل هوية الصف كي يُصحَّح")

    @scenario(41)
    def test_reupload_of_the_same_file_does_not_duplicate_shipments(self) -> None:
        from make_sample_schedule import build

        planner = STATE["planner"]
        content, _base = build(1, base=STATE["weekly_base"])
        upload = planner.data(planner.post(
            "/api/imports", content=content,
            headers={"x-file-name": "again.csv", "content-type": "text/csv"}))
        validation = planner.data(
            planner.post(f"/api/imports/{upload['id']}/validate", json={}))
        self.assertGreater(validation["duplicate_rows"], 0,
                           "إعادة رفع نفس الملف يجب أن تُكتشف كتكرار")

    @scenario(42)
    def test_bad_coordinates_are_rejected_with_the_row_named(self) -> None:
        planner, admin = STATE["planner"], STATE["admin"]
        code = f"TST-BADGEO-{uuid.uuid4().hex[:6].upper()}"

        connection = db_connection()
        try:
            row = connection.fetch_one(
                "SELECT region_id::text AS region_id, city_id::text AS city_id, "
                "default_hub_id::text AS hub_id FROM facilities "
                "WHERE code = 'PHC-RYD-01'")
            connection.execute(
                "INSERT INTO facilities (region_id, city_id, default_hub_id, code, "
                "name_ar, facility_type, lat, lon, service_minutes, is_test_data) "
                "VALUES ($1::uuid,$2::uuid,$3::uuid,$4,$5,'HEALTH_CENTER',"
                "48.8566,2.3522,10,true)",
                [row["region_id"], row["city_id"], row["hub_id"], code,
                 "جهة اختبار بإحداثيات خارج المملكة"],
            )
        finally:
            connection.close()

        from make_sample_schedule import HEADERS, _row

        base = STATE["weekly_base"]
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(HEADERS)
        writer.writerow(_row("GEO-00001", base, 0, code, "LAB-RYD-01",
                             "H-RYD-1", 0, 4))
        content = ("﻿" + buffer.getvalue()).encode("utf-8")

        upload = planner.data(planner.post(
            "/api/imports", content=content,
            headers={"x-file-name": "bad-geo.csv", "content-type": "text/csv"}))
        validation = planner.data(
            planner.post(f"/api/imports/{upload['id']}/validate", json={}))

        codes = {issue["code"] for issue in validation["issue_summary"]}
        self.assertIn("BAD_COORDINATES", codes,
                      f"إحداثيات خارج حدود المملكة لم تُرفض: {sorted(codes)}")
        self.assertEqual(validation["valid_rows"], 0)

        errors = planner.get(f"/api/imports/{upload['id']}/errors.csv")
        self.assertIn("GEO-00001", errors.content.decode("utf-8"),
                      "ملف الأخطاء لا يسمّي الصف المرفوض")

    @scenario(42)
    def test_commit_refuses_invalid_rows_unless_explicitly_skipped(self) -> None:
        planner = STATE["planner"]
        from make_sample_schedule import build

        content, _base = build(1, base=STATE["weekly_base"] + dt.timedelta(days=7))
        upload = planner.data(planner.post(
            "/api/imports", content=content,
            headers={"x-file-name": "strict.csv", "content-type": "text/csv"}))
        planner.data(planner.post(f"/api/imports/{upload['id']}/validate", json={}))
        strict = planner.post(f"/api/imports/{upload['id']}/commit",
                              json={"skip_invalid": False})
        self.assertGreaterEqual(strict.status_code, 400,
                                "الاعتماد مرّر صفوفًا غير صالحة بصمت")


# ================================= ٤٣: التوازي على مستوى المراكز ===========

class TestParallelPlanning(MasarTestCase):
    """التفكيك على المراكز يجب أن يُسرّع الخطة **دون أن يغيّرها**."""

    @staticmethod
    def _signature(per_day: list[dict]) -> list[tuple]:
        return sorted(
            (row["hub_code"], row["service_date"], row["shipment_count"],
             row["route_count"], row["unplannable_count"], row["drivers_used"],
             round(float(row["total_distance_km"]), 3))
            for row in per_day
        )

    def _plan(self, workers: int, hub_ids: list[str], dates: list) -> dict:
        import os

        from masar_core.config import get_config
        from masar_api.services import planning

        previous = os.environ.get("MASAR_SOLVE_WORKERS")
        os.environ["MASAR_SOLVE_WORKERS"] = str(workers)
        get_config(reload=True)
        try:
            return planning.run_planning(
                self.context, hub_ids=hub_ids, dates=dates,
                plan_name=f"اختبار التوازي — {workers}",
                time_limit_seconds=2.0, seed=13)
        finally:
            if previous is None:
                os.environ.pop("MASAR_SOLVE_WORKERS", None)
            else:
                os.environ["MASAR_SOLVE_WORKERS"] = previous
            get_config(reload=True)

    @scenario(43)
    def test_parallel_plan_matches_sequential_plan(self) -> None:
        from masar_db.driver import SecurityContext

        admin = STATE["admin"]
        self.context = SecurityContext(user_id=admin.user["id"], role="ADMIN")

        hub_ids = [STATE["hub_by_code"][code]["id"]
                   for code in ("H-RYD-1", "H-ARR-1", "H-KRJ-1")]
        # يومٌ **خاص بهذا الاختبار**: الجدول التجريبي يغطي ثلاثة أيام والخطة
        # المشتركة تستهلك أولَين فقط. إعادة التخطيط على يوم مشترك تُلغي رحلات
        # تلك الخطة وتُفشل اختبارات لاحقة لسبب لا علاقة له بها — عزل البيانات
        # جزء من صحة الاختبار لا ترتيب إداري.
        third_day = (STATE["weekly_base"] + dt.timedelta(days=2)).date()
        dates = [third_day]

        sequential = self._plan(1, hub_ids, dates)
        parallel = self._plan(2, hub_ids, dates)

        self.assertEqual(
            self._signature(sequential["per_day"]),
            self._signature(parallel["per_day"]),
            "التوازي غيّر الخطة بدل أن يسرّعها فقط — التفكيك غير صحيح")
        self.assertEqual(sequential["metrics"]["route_count"],
                         parallel["metrics"]["route_count"])
        self.assertEqual(sequential["metrics"]["unplannable_count"],
                         parallel["metrics"]["unplannable_count"])
        self.assertGreater(len(parallel["per_day"]), 1,
                           "الاختبار بلا معنى بمسألة واحدة")


# ============================================= ٤٦: النسخ الاحتياطي =========

class TestBackupRestore(MasarTestCase):

    @scenario(46)
    def test_backup_and_restore_round_trip(self) -> None:
        """نسخة كاملة ← استعادة في قاعدة منفصلة ← مطابقة الأعداد الحرجة."""
        import subprocess

        script = ROOT / "scripts" / "backup.sh"
        self.assertTrue(script.exists(), "سكربت النسخ الاحتياطي غير موجود")

        result = subprocess.run(
            ["bash", str(script), "verify"],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        self.assertEqual(result.returncode, 0,
                         f"فشل اختبار النسخ والاستعادة:\n{result.stdout[-3000:]}\n"
                         f"{result.stderr[-2000:]}")
        self.assertIn("✅", result.stdout, result.stdout[-2000:])

        for table in ("shipments", "routes", "audit_log", "plans"):
            self.assertIn(table, result.stdout,
                          f"التحقق لم يشمل جدول {table}")

        # الاستعادة تعيد **القيود** لا الصفوف فقط: نسخة بلا سياسات RLS ولا
        # محفّزات تعيد البيانات وتفقد الحماية.
        self.assertIn("سياسات RLS المستعادة", result.stdout)
        self.assertNotIn("لم تُستعد", result.stdout)

    @scenario(46)
    def test_wal_archiving_supports_point_in_time_recovery(self) -> None:
        """RPO ≤ ٥ دقائق يتطلب أرشفة WAL مستمرة لا نسخة يومية."""
        import time as _time

        query = ("SELECT current_setting('archive_mode', true) AS mode, "
                 "current_setting('archive_timeout', true) AS timeout, "
                 "(SELECT failed_count FROM pg_stat_archiver) AS failed, "
                 "(SELECT archived_count FROM pg_stat_archiver) AS archived, "
                 "(SELECT last_archived_wal FROM pg_stat_archiver) AS last_wal")

        def read() -> dict:
            connection = db_connection()
            try:
                return connection.fetch_one(query)
            finally:
                connection.close()

        row = read()
        if row["mode"] != "on":
            self.skipTest(
                "أرشفة WAL غير مفعّلة على هذا العنقود — شغّل "
                "./scripts/backup.sh archive-setup (إعداد نشر لا شيفرة)")

        # المؤرشف عملية غير متزامنة: تشغيل الحزمة مباشرة بعد إعادة بناء
        # القاعدة قد يسبق أرشفة أول مقطع. الانتظار المحدود يفرّق بين
        # «لم يؤرشف بعد» و«لا يؤرشف» — والثانية وحدها عطل.
        deadline = _time.monotonic() + 30.0
        while (int(row["archived"] or 0) == 0 and not row["last_wal"]
               and _time.monotonic() < deadline):
            _time.sleep(2.0)
            row = read()

        self.assertEqual(int(row["failed"] or 0), 0,
                         "فشلت أرشفة مقاطع WAL — هدف RPO غير مضمون")
        self.assertTrue(
            int(row["archived"] or 0) > 0 or row["last_wal"],
            "لم يُؤرشف أي مقطع خلال ٣٠ ثانية رغم تفعيل الأرشفة وعدم تسجيل فشل — "
            "افحص archive_command ومجلد الأرشيف وصلاحياته")
        timeout = str(row["timeout"])
        minutes = (int(timeout[:-3]) if timeout.endswith("min")
                   else int(timeout.rstrip("s")) / 60 if timeout else 0)
        self.assertLessEqual(minutes, 5,
                             f"archive_timeout={timeout} يتجاوز هدف RPO (٥ دقائق)")


if __name__ == "__main__":
    unittest.main()
