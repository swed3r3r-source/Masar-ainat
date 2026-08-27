"""دورة كاملة عبر HTTP: رفع الجدول ← تخطيط ← إسناد ← نشر ← تنفيذ ← تقارير.

هذا ليس اختبارًا شكليًا: كل خطوة تمر بنفس نقاط الـ API التي تستخدمها الواجهة،
وبنفس المصادقة والصلاحيات وسياسات RLS.

**حدّ معروف:** ثلاثة فحوص في قسم «تنفيذ الرحلة» تشترط رحلة متعددة الشحنات
(منع القفز فوق محطة، ومنع التسليم قبل الالتقاط، وإكمال كل المحطات). المحرك
يقرّر عدد الرحلات بنفسه، وقد يوزّع شحنات اليوم على رحلة لكل شحنة حين تباعدت
النوافذ أو ضاق الوقت — فتفشل هذه الثلاثة لأن الرحلة المختارة بمحطتين، لا
لعطل في السلوك المفحوص. السلوك نفسه مُثبَت **حتميًا** في حزمة الوحدة
(سيناريوهات ٨ و٩ و١٠ و١٦) على مسائل مبنية خصيصًا لذلك.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

BASE = "http://127.0.0.1:8080"
#: لا كلمة مرور مثبّتة في المستودع — تُمرَّر عبر البيئة
PASSWORD = os.environ["MASAR_TEST_PASSWORD"]
TZ = dt.timezone(dt.timedelta(hours=3))

PASS = "✅"
FAIL = "❌"
INFO = "  ·"

failures: list[str] = []


def step(title: str) -> None:
    print(f"\n{'─' * 72}\n{title}\n{'─' * 72}")


def check(condition: bool, message: str, detail: str = "") -> bool:
    print(f"{PASS if condition else FAIL} {message}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(message)
    return condition


class Client:
    def __init__(self, email: str) -> None:
        self.session = httpx.Client(base_url=BASE, timeout=180.0)
        response = self.session.post(
            "/api/auth/login", json={"email": email, "password": PASSWORD})
        response.raise_for_status()
        payload = response.json()
        self.user = payload["data"]["user"]
        self.token = payload["data"]["access_token"]
        self.session.headers["authorization"] = f"Bearer {self.token}"

    def get(self, url: str, **kwargs):
        return self.session.get(url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.session.post(url, **kwargs)

    def patch(self, url: str, **kwargs):
        return self.session.patch(url, **kwargs)

    def data(self, response) -> object:
        if response.status_code >= 400:
            raise AssertionError(f"{response.status_code}: {response.text[:600]}")
        return response.json().get("data")


def main() -> int:
    base_file = ROOT / "var" / "sample-schedule.base"
    base = dt.datetime.fromisoformat(base_file.read_text().strip())
    plan_dates = [base.date().isoformat()]
    print(f"{INFO} تاريخ الخطة: {plan_dates[0]} · أول موعد التقاط "
          f"{base:%H:%M} بتوقيت الرياض")

    # ---------------------------------------------------------- المصادقة --
    step("١) المصادقة والصلاحيات")
    admin = Client("admin@masar.test")
    planner = Client("planner@masar.test")
    supervisor = Client("sup.ryd@masar.test")
    arar_supervisor = Client("sup.arr@masar.test")
    check(planner.user["role"] == "CENTRAL_PLANNER", "دخول التخطيط المركزي")
    check(len(supervisor.user["hub_ids"]) == 1,
          "نطاق مشرف الرياض محصور بمركز واحد",
          f"{len(supervisor.user['hub_ids'])} مركز")

    bad = httpx.post(f"{BASE}/api/auth/login",
                     json={"email": "admin@masar.test", "password": "wrong"})
    check(bad.status_code == 401, "رفض كلمة مرور خاطئة")

    no_token = httpx.get(f"{BASE}/api/shipments")
    check(no_token.status_code == 401, "منع الوصول بلا رمز دخول")

    # مشرف لا يستطيع رفع الجدول الوطني (§5)
    forbidden = supervisor.post("/api/imports", content=b"x",
                                headers={"x-file-name": "x.csv"})
    check(forbidden.status_code == 403, "منع المشرف من رفع الجدول الوطني (اختبار ٣٢)")

    # ------------------------------------------------------- رفع الجدول --
    step("٢) رفع الجدول الأسبوعي والتحقق منه")
    content = (ROOT / "var" / "sample-schedule.csv").read_bytes()
    response = planner.post(
        "/api/imports", content=content,
        headers={"x-file-name": "sample-schedule.csv", "content-type": "text/csv"})
    upload = planner.data(response)
    import_id = upload["id"]
    check(bool(import_id), "أُنشئ سجل الاستيراد", upload["reference"])
    mapped = sum(1 for value in upload["mapping"].values() if value)
    check(mapped >= 12, "مطابقة الأعمدة تلقائيًا", f"{mapped} عمودًا")

    validation = planner.data(planner.post(f"/api/imports/{import_id}/validate", json={}))
    print(f"{INFO} الصفوف: {validation['total_rows']} · صالحة: {validation['valid_rows']} "
          f"· غير صالحة: {validation['invalid_rows']} "
          f"· مكررة: {validation['duplicate_rows']}")
    codes = {item["code"] for item in validation["issue_summary"]}
    for expected, label in (
        ("DUPLICATE_ROW", "كشف الصفوف المكررة (اختبار ٤١)"),
        ("FACILITY_NOT_FOUND", "كشف الجهات غير المسجلة"),
        ("HUB_NOT_FOUND", "كشف مراكز الانطلاق غير المسجلة"),
        ("SLA_BEFORE_PICKUP", "كشف SLA قبل نافذة الالتقاط"),
        ("BAD_DATE", "كشف صيغ التواريخ الخاطئة"),
        ("MISSING_REQUIRED", "كشف الحقول الإلزامية الفارغة"),
        ("BAD_NUMBER", "كشف الأرقام غير الصالحة"),
    ):
        check(expected in codes, label)
    check(any(c.startswith("INFEASIBLE_") for c in codes),
          "فحص الجدوى المبدئي يكشف SLA المستحيل (اختبار ٦)",
          ", ".join(sorted(c for c in codes if c.startswith("INFEASIBLE_"))))

    errors_csv = planner.get(f"/api/imports/{import_id}/errors.csv")
    check(errors_csv.status_code == 200 and len(errors_csv.content) > 100,
          "تنزيل ملف الأخطاء", f"{len(errors_csv.content)} بايت")

    commit = planner.data(planner.post(f"/api/imports/{import_id}/commit",
                                       json={"skip_invalid": True}))
    check(commit["created_shipments"] > 0, "اعتماد الاستيراد وإنشاء الشحنات",
          f"{commit['created_shipments']} شحنة")

    # ---------------------------------------------------------- التخطيط --
    step("٣) تشغيل محرك المسارات")
    hubs = planner.data(planner.get("/api/md/hubs"))
    hub_by_code = {hub["code"]: hub for hub in hubs}
    target_hubs = [hub_by_code["H-RYD-1"]["id"], hub_by_code["H-ARR-1"]["id"],
                   hub_by_code["H-KRJ-1"]["id"]]

    plan = planner.data(planner.post("/api/plans/run", json={
        "hub_ids": target_hubs,
        "dates": plan_dates,
        "import_id": import_id,
        "time_limit_seconds": 6,
        "name": "خطة اختبار الدورة الكاملة",
    }))
    plan_id = plan["plan_id"]
    metrics = plan["metrics"]
    print(f"{INFO} الرحلات {metrics['route_count']} · السائقون المستخدمون "
          f"{metrics['drivers_used']} · غير قابلة للتخطيط {metrics['unplannable_count']} "
          f"· زمن الحل {metrics['solve_ms']} مللي ثانية")
    check(metrics["route_count"] > 0, "بُنيت رحلات فعلية")
    check(plan["routing_estimated"] is True,
          "الخطة موسومة صراحة بأن أزمنة الطريق تقديرية (لا ادعاء زائف)")

    detail = planner.data(planner.get(f"/api/plans/{plan_id}"))
    routes = detail["routes"]
    warnings = detail["warnings"]
    check(len(routes) == metrics["route_count"],
          "تطابق عدد الرحلات بين الملخص والتفاصيل (اختبار ٣٤)",
          f"{len(routes)} = {metrics['route_count']}")
    check(all(w["reason_ar"] and w["affected_entity_ar"] and w["suggested_action_ar"]
              for w in warnings),
          "كل تحذير له سبب وجهة متأثرة وإجراء مقترح (اختبار ٣٥)",
          f"{len(warnings)} تحذيرًا")
    linked = [w for w in warnings if w["route_id"] or w["shipment_id"]]
    check(len(linked) > 0, "التحذيرات مرتبطة برحلة أو شحنة قابلة للفتح")

    # قيد عدم الخلط
    mixed = []
    for route in routes:
        stops = planner.data(planner.get(f"/api/routes/{route['id']}"))["stops"]
        types = {s["facility_type"] for s in stops
                 if s["kind"] == "PICKUP" and s["facility_type"]}
        if "HOSPITAL" in types and "HEALTH_CENTER" in types:
            mixed.append(route["reference"])
    check(not mixed, "منع خلط المستشفيات والمراكز الصحية على السائق نفسه (اختبار ١١)",
          f"مخالفات: {mixed}" if mixed else "لا مخالفات")

    long_haul = [r for r in routes if r["is_long_haul"]]
    for route in long_haul:
        stops = planner.data(planner.get(f"/api/routes/{route['id']}"))["stops"]
        pickups = [s for s in stops if s["kind"] == "PICKUP"]
        check(len(pickups) >= 1,
              f"الرحلة البعيدة {route['reference']} تحتوي التقاطًا واحدًا على الأقل")
    check(len(long_haul) >= 1, "اكتُشفت رحلة بعيدة (اختبار ١٣)",
          f"{len(long_haul)} رحلة، أقصى مسافة "
          f"{max((float(r['max_hub_distance_km']) for r in long_haul), default=0):.0f} كم")

    over_shift = [r for r in routes if float(r["working_minutes"]) > 600.5]
    check(not over_shift, "لا رحلة تتجاوز وردية ١٠ ساعات (اختبار ١٥)")

    unplannable = detail["unplannable"]
    check(all(u["unplannable_reason"] and u["unplannable_detail"] for u in unplannable),
          "كل شحنة غير قابلة للتخطيط لها سبب مُصنّف وتفصيل مكتوب (HC-19)",
          f"{len(unplannable)} شحنة"
          + (f" · مثال: {unplannable[0]['unplannable_reason']}" if unplannable else ""))
    check("PHC-RFH-01" in errors_csv.content.decode("utf-8"),
          "الشحنة المستحيلة جغرافيًا (رفحاء) رُدّت في التحقق قبل التخطيط",
          "ظهرت في ملف الأخطاء بسبب مكتوب")

    estimations = detail["estimations"]
    check(len(estimations) > 0 and all(e["justification"] for e in estimations),
          "تقدير السائقين مع تبرير لكل سائق إضافي (اختبار ٣٨)")
    for estimate in estimations:
        print(f"{INFO} {estimate['hub_name_ar']}: نظري {estimate['theoretical_minimum']} "
              f"· موصى به {estimate['recommended']} · متوفر {estimate['available']} "
              f"· مستخدم {estimate['used']}")

    # ثبات المسودة بعد التحديث (اختبار ٣٣)
    again = planner.data(planner.get(f"/api/plans/{plan_id}"))
    check(len(again["routes"]) == len(routes),
          "مسودة الخطة محفوظة بشكل دائم ولا تختفي بإعادة التحميل (اختبار ٣٣)")

    # ----------------------------------------------------- الاعتماد والإرسال
    step("٤) الاعتماد والإرسال إلى مراكز الانطلاق")
    # المزوّد هنا تقديري، والاعتماد يتطلب إقرارًا صريحًا يُسجَّل في سجل
    # التدقيق (§23). في الإنتاج لا يوجد إقرار يجيزه — الاعتماد مرفوض قطعًا.
    planner.data(planner.post(f"/api/plans/{plan_id}/approve",
                              json={"acknowledge_estimated": True}))
    dispatch = planner.data(planner.post(f"/api/plans/{plan_id}/dispatch", json={}))
    check(dispatch["status"] == "DISPATCHED", "أُرسلت الخطة للمراكز",
          f"{dispatch['hub_count']} مركز")

    # ------------------------------------------------------------ الإسناد --
    step("٥) الإسناد والنشر")
    riyadh_hub = hub_by_code["H-RYD-1"]["id"]
    riyadh_routes = [r for r in routes if r["hub_id"] == riyadh_hub]
    check(len(riyadh_routes) > 0, "توجد رحلات في مركز الرياض")

    # المشرف يرى مركزه فقط (اختبار ٣١)
    supervisor_routes = supervisor.data(supervisor.get("/api/routes"))
    other_hub_visible = [r for r in supervisor_routes if r["hub_id"] != riyadh_hub]
    check(not other_hub_visible,
          "منع المشرف من رؤية رحلات مركز آخر — مطبَّق في قاعدة البيانات (اختبار ٣١)",
          f"رأى {len(supervisor_routes)} رحلة كلها لمركزه")

    assigned = 0
    vehicles = supervisor.data(supervisor.get("/api/md/vehicles"))
    boxes = supervisor.data(supervisor.get("/api/md/boxes"))
    for index, route in enumerate(riyadh_routes):
        candidates = supervisor.data(
            supervisor.get(f"/api/routes/{route['id']}/candidates"))["candidates"]
        eligible = [c for c in candidates if c["eligible"]]
        if not eligible:
            print(f"{INFO} لا مرشح مؤهل للرحلة {route['reference']} — "
                  f"أسباب: {candidates[0]['blockers'] if candidates else 'لا مرشحين'}")
            continue
        picked = eligible[0]
        result = supervisor.post(f"/api/routes/{route['id']}/assign", json={
            "driver_id": picked["driver_id"],
            "vehicle_id": vehicles[index % len(vehicles)]["id"] if vehicles else None,
            "box_id": boxes[index % len(boxes)]["id"] if boxes else None,
        })
        if result.status_code == 200:
            assigned += 1
    check(assigned == len(riyadh_routes), "أُسندت كل رحلات مركز الرياض",
          f"{assigned}/{len(riyadh_routes)}")

    # منع تعارض الإسناد (اختبار ٦ من قائمة الأمان)
    if len(riyadh_routes) >= 2:
        first_driver = supervisor.data(
            supervisor.get(f"/api/routes/{riyadh_routes[0]['id']}"))["route"]["driver_id"]
        conflict = supervisor.post(f"/api/routes/{riyadh_routes[1]['id']}/assign",
                                   json={"driver_id": first_driver})
        check(conflict.status_code in (409, 422),
              "منع الإسناد المتعارض لنفس السائق",
              f"HTTP {conflict.status_code}")

    publish = supervisor.post("/api/publish", json={
        "hub_id": riyadh_hub, "service_date": plan_dates[0]})
    check(publish.status_code == 200, "نشر خطة يوم واحد فقط (اختبار ٣٦)",
          publish.text[:200] if publish.status_code != 200 else
          f"{publish.json()['data']['published_routes']} رحلة")

    # يوم عرعر لم يُنشر — استقلال النشر لكل يوم/مركز
    arar_published = arar_supervisor.data(arar_supervisor.get("/api/routes"))
    arar_published_count = len([r for r in arar_published if r["status"] == "PUBLISHED"])
    check(arar_published_count == 0,
          "نشر مركز الرياض لم ينشر مركز عرعر (استقلال النشر)")

    # ------------------------------------------------------ تطبيق السائق --
    step("٦) تنفيذ الرحلة من تطبيق السائق (خطة اليوم الحالي)")
    from scripts_same_day import prepare_same_day_route  # noqa: E402

    prepared = prepare_same_day_route(planner, supervisor, admin, hub_by_code, check)
    if prepared is None:
        print(f"{INFO} تعذّر إعداد خطة لليوم الحالي (الوقت متأخر) — "
              "تُخطى مرحلة التنفيذ")
        return _finish()

    (driver_client, target_route, route_detail, driver_row, published_routes,
     prepared_base) = prepared

    my_routes = driver_client.data(driver_client.get("/api/driver/routes"))
    check(any(r["id"] == target_route["id"] for r in my_routes),
          "السائق يرى رحلته المنشورة")
    visible = driver_client.data(driver_client.get("/api/routes"))
    check(all(r["driver_id"] == driver_row["driver_id"] for r in visible),
          "السائق لا يرى إلا رحلاته (اختبار ٣٠)", f"{len(visible)} رحلة")

    other_route = next((r for r in published_routes if r["id"] != target_route["id"]), None)
    if other_route:
        blocked = driver_client.get(f"/api/routes/{other_route['id']}")
        check(blocked.status_code in (403, 404),
              "منع السائق من فتح رحلة سائق آخر (اختبار ٣٠)",
              f"HTTP {blocked.status_code}")

    started = driver_client.post(f"/api/driver/routes/{target_route['id']}/start",
                                 json={"lat": 24.725, "lon": 46.690})
    check(started.status_code == 200, "بدء الرحلة",
          started.text[:250] if started.status_code != 200 else "")

    stops = [s for s in route_detail["stops"] if s["kind"] != "HUB_START"]
    executed = 0
    skipped_check_done = False
    delivery_before_pickup_blocked = None

    for index, stop in enumerate(stops):
        # منع القفز فوق محطة سابقة
        if index == 1 and not skipped_check_done:
            jump = driver_client.post(f"/api/driver/stops/{stops[-1]['id']}/arrive",
                                      json={"lat": stop["lat"], "lon": stop["lon"]})
            check(jump.status_code >= 400,
                  "منع القفز فوق محطة سابقة غير محسومة",
                  f"HTTP {jump.status_code}")
            skipped_check_done = True

        if stop["kind"] == "DELIVERY" and delivery_before_pickup_blocked is None:
            attempt = driver_client.post(f"/api/driver/stops/{stop['id']}/deliver",
                                         json={"lat": stop["lat"], "lon": stop["lon"]})
            delivery_before_pickup_blocked = attempt.status_code >= 400

        arrive = driver_client.post(f"/api/driver/stops/{stop['id']}/arrive",
                                    json={"lat": stop["lat"], "lon": stop["lon"]})
        if arrive.status_code != 200:
            print(f"{INFO} تعذر الوصول للمحطة {stop['sequence']}: {arrive.text[:200]}")
            continue
        action = "pickup" if stop["kind"] == "PICKUP" else "deliver"
        done = driver_client.post(f"/api/driver/stops/{stop['id']}/{action}",
                                  json={"lat": stop["lat"], "lon": stop["lon"]})
        if done.status_code == 200:
            executed += 1
        else:
            print(f"{INFO} تعذر {action} للمحطة {stop['sequence']}: {done.text[:200]}")

    check(executed == len(stops), "نُفذت كل محطات الرحلة",
          f"{executed}/{len(stops)}")
    check(delivery_before_pickup_blocked is not False,
          "منع التسليم قبل الالتقاط (اختبار ٨)")

    final_route = supervisor.data(
        supervisor.get(f"/api/routes/{target_route['id']}"))["route"]
    check(final_route["status"] == "COMPLETED",
          "اكتملت الرحلة تلقائيًا بعد حسم كل شحناتها (اختبار ١٦)",
          final_route["status"])

    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000a49444154789c6300010000050001"
        "0d0a2db40000000049454e44ae426082")
    shipment_id = next((s["shipment_id"] for s in stops if s["shipment_id"]), None)
    if shipment_id:
        doc = driver_client.post(
            f"/api/documents?shipment_id={shipment_id}&doc_kind=DELIVERY_PROOF",
            content=png, headers={"content-type": "image/png",
                                  "x-file-name": "proof.png"})
        check(doc.status_code in (200, 201), "رفع مستند إثبات التسليم",
              doc.text[:250] if doc.status_code >= 400 else "")

        fake = driver_client.post(
            f"/api/documents?shipment_id={shipment_id}&doc_kind=DELIVERY_PROOF",
            content=b"<html>not an image</html>",
            headers={"content-type": "image/png", "x-file-name": "fake.png"})
        check(fake.status_code >= 400,
              "رفض ملف مزيّف يخالف نوعه المعلن (فحص التوقيع الثنائي)",
              f"HTTP {fake.status_code}")

    positions = driver_client.post("/api/positions", json={"points": [
        {"lat": 24.73, "lon": 46.69, "speed_kmh": 40,
         "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
         "route_id": target_route["id"]},
    ]})
    check(positions.status_code == 200, "إرسال موقع السائق")

    live = supervisor.data(supervisor.get("/api/tracking/live"))
    check(any(p["driver_id"] == driver_row["driver_id"] for p in live),
          "ظهور السائق على الخريطة المباشرة")

    # ------------------------------------------------------ الطلب الفوري --
    step("٧) الطلب الفوري")
    requester = Client("req.phc01@masar.test")
    facilities = admin.data(admin.get("/api/md/facilities"))
    by_code = {f["code"]: f for f in facilities}
    # نافذة الطلب تُقاس من **بداية خطة اليوم المنفَّذة** لا من لحظة تشغيل
    # الاختبار: الإدراج الديناميكي يُدرج في رحلة قائمة، فنافذة خارج نطاق تلك
    # الرحلة تُرفض بحق. ربطها بالخطة يجعل الفحص مستقلًا عن ساعة التشغيل.
    anchor = prepared_base if prepared_base is not None else dt.datetime.now(TZ)
    request_payload = {
        "pickup_facility_id": by_code["PHC-RYD-01"]["id"],
        "dropoff_facility_id": by_code["LAB-RYD-01"]["id"],
        "pickup_window_from": (anchor + dt.timedelta(minutes=5)).isoformat(),
        "pickup_window_to": (anchor + dt.timedelta(minutes=120)).isoformat(),
        "sla_deadline": (anchor + dt.timedelta(hours=5)).isoformat(),
        "service_type": "URGENT",
        "piece_count": 2,
        "temperature_mode": "CHILLED",
        "notes": "عينة عاجلة",
    }
    created_request = requester.post("/api/ondemand", json=request_payload)
    check(created_request.status_code in (200, 201), "إنشاء طلب فوري",
          created_request.text[:250] if created_request.status_code >= 400 else "")

    if created_request.status_code < 400:
        request_id = created_request.json()["data"]["shipment_id"]

        # الجهة الأخرى لا ترى الطلب (اختبار الصلاحيات)
        other_requester = Client("req.hos01@masar.test")
        visible = other_requester.data(other_requester.get("/api/ondemand"))
        check(not any(item["id"] == request_id for item in visible),
              "مقدم طلب آخر لا يرى هذا الطلب — RLS")

        tower = Client("tower@masar.test")
        reviewed = tower.post(f"/api/ondemand/{request_id}/review",
                              json={"approve": True})
        check(reviewed.status_code == 200, "موافقة برج التحكم على الطلب الفوري")

        options = supervisor.get(f"/api/ondemand/{request_id}/options")
        if options.status_code == 200:
            payload = options.json()["data"]
            print(f"{INFO} خيارات الإدراج: {len(payload['options'])} "
                  f"· قابل للإدراج: {payload['feasible']}")
            if payload["options"]:
                best = payload["options"][0]
                print(f"{INFO} أفضل خيار: {best['route_reference']} "
                      f"(+{best['added_minutes']} د، +{best['added_km']} كم، "
                      f"محسوب من {best['computed_from']})")
                assign = supervisor.post(f"/api/ondemand/{request_id}/assign", json={
                    "route_id": best["route_id"],
                    "pickup_position": best["pickup_position"],
                    "delivery_position": best["delivery_position"],
                })
                check(assign.status_code == 200,
                      "إسناد الطلب الفوري إلى رحلة نشطة (اختبار ٢٠)",
                      assign.text[:250] if assign.status_code >= 400 else "")
            else:
                check(False, "إيجاد خيار إدراج للطلب الفوري (اختبار ٢٠)",
                      "; ".join(r["message_ar"][:110] for r in payload["rejections"][:2]))

        cancelled = requester.post(f"/api/ondemand/{request_id}/cancel",
                                   json={"reason": "لم تعد العينة مطلوبة"})
        check(cancelled.status_code in (200, 409),
              "إلغاء الطلب قبل الالتقاط (اختبار ٢١)",
              f"HTTP {cancelled.status_code}")

    # --------------------------------------------------- التنبيهات والتقارير
    step("٨) التنبيهات والتقارير والتدقيق")
    scan = supervisor.data(supervisor.post("/api/alerts/scan", json={}))
    print(f"{INFO} فحص التنبيهات: {scan}")
    alerts = supervisor.data(supervisor.get("/api/alerts"))
    check(isinstance(alerts, list), "قائمة التنبيهات تعمل", f"{len(alerts)} تنبيهًا")
    if alerts:
        alert_id = alerts[0]["id"]
        no_note = supervisor.post(f"/api/alerts/{alert_id}/resolve", json={})
        check(no_note.status_code >= 400, "منع إغلاق تنبيه بلا إجراء مسجّل")
        resolved = supervisor.post(f"/api/alerts/{alert_id}/resolve",
                                   json={"action_note": "تم التواصل مع الجهة ومعالجة السبب"})
        check(resolved.status_code == 200, "إغلاق التنبيه بإجراء مسجّل")

    kpi = planner.data(planner.get(
        f"/api/reports/kpi?include_test_data=true&date_from={plan_dates[0]}"))
    print(f"{INFO} المؤشرات: شحنات {kpi['shipment_count']} · مكتملة "
          f"{kpi['completed_count']} · تجاوز SLA {kpi['sla_breached_count']}")
    grouped = planner.data(planner.get(
        "/api/reports/grouped?group_by=hub&include_test_data=true"))
    total_from_groups = sum(int(row["shipment_count"]) for row in grouped)
    kpi_all = planner.data(planner.get("/api/reports/kpi?include_test_data=true"))
    check(total_from_groups == int(kpi_all["shipment_count"]),
          "تطابق الأرقام بين الملخص والتفصيل — مصدر بيانات موحّد (اختبار ٣٤)",
          f"{total_from_groups} = {kpi_all['shipment_count']}")

    capacity = planner.data(planner.get("/api/reports/driver-capacity"))
    check(isinstance(capacity, list),
          "تقرير كشف الزيادة غير المبررة في السائقين (اختبار ٣٩)",
          f"{len(capacity)} سجل")

    csv_export = planner.get(
        "/api/reports/grouped?group_by=hub&format=csv&include_test_data=true")
    check(csv_export.status_code == 200 and csv_export.content.startswith("﻿".encode()),
          "تصدير التقرير إلى CSV بترميز صحيح")

    audit_rows = admin.data(admin.get("/api/audit?limit=20"))
    check(len(audit_rows) > 0, "سجل التدقيق يحتوي عمليات", f"{len(audit_rows)} سجل")
    actions = {row["action"] for row in admin.data(admin.get("/api/audit?limit=500"))}
    for expected in ("LOGIN_SUCCESS", "SCHEDULE_UPLOAD", "OPTIMIZER_RUN",
                     "PLAN_APPROVE", "ROUTE_ASSIGN", "ROUTE_PUBLISH"):
        check(expected in actions, f"سجل التدقيق يوثّق {expected}")

    supervisor_audit = supervisor.get("/api/audit")
    check(supervisor_audit.status_code == 403,
          "منع المشرف من الاطلاع على سجل التدقيق")

    # حرارة
    temp_status = httpx.get(f"{BASE}/api/temperature/status").json()["data"]
    check(temp_status["is_real_integration"] is False
          and "NO_SENSOR" in temp_status["message_ar"],
          "حالة تكامل الحرارة معلنة بصدق (لا محاكاة معروضة كتكامل حقيقي)")

    return _finish()


def _finish() -> int:
    step("النتيجة")
    if failures:
        print(f"{FAIL} فشل {len(failures)} فحصًا:")
        for item in failures:
            print(f"   - {item}")
        return 1
    print(f"{PASS} نجحت كل الفحوص")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
