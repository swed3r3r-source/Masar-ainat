"""إعداد خطة لليوم الحالي حتى تُختبر دورة التنفيذ الفعلية عبر HTTP.

مفصولة عن ``e2e_smoke`` لأنها منطق تحضير لا فحصًا: ترفع جدولًا صغيرًا لليوم،
تخطط، تُسند، تنشر، وتعيد رحلة جاهزة للتنفيذ مع حساب سائقها.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "scripts"))

TZ = dt.timezone(dt.timedelta(hours=3))


def prepare_same_day_route(
    planner: Any,
    supervisor: Any,
    admin: Any,
    hub_by_code: dict[str, Any],
    check: Callable[..., bool],
) -> tuple[Any, Any, Any, Any, list[Any], Any] | None:
    """يبني ويُسند ويَنشر خطة اليوم، ويعيد رحلة قابلة للتنفيذ.

    يعيد ``None`` إذا كان الوقت متأخرًا بحيث لا تتسع بقية اليوم للتنفيذ.
    """
    from e2e_smoke import Client  # noqa: E402
    from make_sample_schedule import build_same_day  # noqa: E402

    generated = build_same_day()
    if generated is None:
        # أوقات عمل المركز بيانات رئيسية قابلة للتعديل (§13). توسيعها عبر API
        # الإدارة نفسه **تغيير إعداد لا تجاوز قيد**: المحرك يبقى يفحص كل رحلة
        # مقابل النافذة المُعلنة. البديل — تخطّي دورة التنفيذ كلما شُغّل
        # الاختبار خارج الدوام — يترك أهم ما في النظام بلا إثبات.
        riyadh = hub_by_code["H-RYD-1"]
        widened = {day: ["00:00", "23:59"]
                   for day in ("sat", "sun", "mon", "tue", "wed", "thu", "fri")}
        response = admin.patch(f"/api/md/hubs/{riyadh['id']}",
                               json={"working_hours": widened})
        if response.status_code != 200:
            check(False, "توسيع أوقات عمل المركز لتمكين دورة التنفيذ",
                  response.text[:200])
            return None
        check(True, "وُسّعت أوقات عمل مركز الرياض في بيئة الاختبار",
              "تغيير إعداد عبر API الإدارة — القيود تبقى مفحوصة")
        generated = build_same_day(open_hour=0, close_hour=24)
        if generated is None:
            return None
    content, base = generated
    today = base.date().isoformat()

    upload = planner.data(planner.post(
        "/api/imports", content=content,
        headers={"x-file-name": "same-day.csv", "content-type": "text/csv"}))
    import_id = upload["id"]
    validation = planner.data(planner.post(f"/api/imports/{import_id}/validate", json={}))
    if validation["valid_rows"] == 0:
        check(False, "تحقق جدول اليوم الحالي",
              f"لا صفوف صالحة: {validation['issue_summary'][:2]}")
        return None
    planner.data(planner.post(f"/api/imports/{import_id}/commit",
                              json={"skip_invalid": True}))

    riyadh_hub = hub_by_code["H-RYD-1"]["id"]
    plan = planner.data(planner.post("/api/plans/run", json={
        "hub_ids": [riyadh_hub],
        "dates": [today],
        "import_id": import_id,
        "time_limit_seconds": 4,
        "name": f"خطة تنفيذ اليوم {today}",
    }))
    plan_id = plan["plan_id"]
    if plan["metrics"]["route_count"] == 0:
        check(False, "بناء رحلة لليوم الحالي",
              f"غير قابلة للتخطيط: {plan['metrics']['unplannable_count']}")
        return None

    planner.data(planner.post(f"/api/plans/{plan_id}/approve",
                              json={"acknowledge_estimated": True}))
    planner.data(planner.post(f"/api/plans/{plan_id}/dispatch", json={}))

    day_routes = [
        route for route in supervisor.data(
            supervisor.get(f"/api/routes?service_date={today}"))
        if route["status"] in ("PLANNED", "ASSIGNED")
    ]
    vehicles = supervisor.data(supervisor.get("/api/md/vehicles"))
    boxes = supervisor.data(supervisor.get("/api/md/boxes"))

    assigned = 0
    for index, route in enumerate(day_routes):
        candidates = supervisor.data(
            supervisor.get(f"/api/routes/{route['id']}/candidates"))["candidates"]
        eligible = [c for c in candidates if c["eligible"]]
        if not eligible:
            continue
        result = supervisor.post(f"/api/routes/{route['id']}/assign", json={
            "driver_id": eligible[0]["driver_id"],
            "vehicle_id": vehicles[index % len(vehicles)]["id"] if vehicles else None,
            "box_id": boxes[index % len(boxes)]["id"] if boxes else None,
        })
        if result.status_code == 200:
            assigned += 1
    check(assigned > 0, "إسناد رحلات اليوم الحالي", f"{assigned}/{len(day_routes)}")

    published = supervisor.post("/api/publish",
                                json={"hub_id": riyadh_hub, "service_date": today})
    check(published.status_code == 200, "نشر خطة اليوم الحالي",
          published.text[:200] if published.status_code != 200 else "")
    if published.status_code != 200:
        return None

    published_routes = [
        route for route in supervisor.data(
            supervisor.get(f"/api/routes?service_date={today}"))
        if route["status"] == "PUBLISHED"
    ]
    if not published_routes:
        check(False, "توجد رحلة منشورة لليوم الحالي")
        return None

    # نختار **أطول رحلة** لا أول رحلة: فحوص «منع القفز فوق محطة غير محسومة»
    # و«منع التسليم قبل تسجيل الوصول» لا معنى لها على رحلة من محطتين، إذ تكون
    # المحطة التالية هي نفسها آخر محطة. طول الرحلة قرار للمحرك لا نفرضه،
    # فنختار من مخرجاته ما يصلح للفحص.
    details = {}
    for candidate in published_routes:
        details[candidate["id"]] = supervisor.data(
            supervisor.get(f"/api/routes/{candidate['id']}"))
    target_route = max(
        published_routes,
        key=lambda r: len([s for s in details[r["id"]]["stops"]
                           if s["kind"] != "HUB_START"]))
    route_detail = details[target_route["id"]]
    driver_row = route_detail["route"]

    driver_email = None
    for driver in admin.data(admin.get("/api/md/drivers")):
        if driver["id"] == driver_row["driver_id"]:
            driver_email = f"{driver['code'].lower()}@masar.test"
    if not driver_email:
        check(False, "تحديد حساب السائق المسند")
        return None
    check(True, "تحديد حساب السائق المسند", driver_email)

    return (Client(driver_email), target_route, route_detail, driver_row,
            published_routes, base)
