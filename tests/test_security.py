"""فحوص أمنية آلية — تقييم ذاتي هجومي.

**تحذير مهم:** هذه الحزمة **لا تُغني عن اختبار اختراق مستقل**. ما تفعله هو
إغلاق الباب أمام الأخطاء الواضحة والمتكررة، وتحويلها إلى انحدارات مكشوفة:
تجاوز المصادقة، الوصول لكائنات الغير (IDOR)، حقن SQL، رفع الصلاحيات، ضعف
الجلسات، ترويسات ناقصة، ومسارات ملفات.

كل فحص هنا **يحاول الاختراق فعلًا** عبر HTTP، ثم يتأكد أن النظام رفض.
"""

from __future__ import annotations

import datetime as dt
import unittest
import uuid

import httpx

from .support import (
    BASE_URL,
    ROOT,
    require_test_password,
    ApiClient,
    MasarTestCase,
    db_connection,
    server_is_up,
)

STATE: dict[str, object] = {}


def login_raw(email: str, session: httpx.Client | None = None):
    """تسجيل دخول مباشر يحترم مهلة محدِّد المعدّل بدل تعطيله.

    محدِّد المعدّل نفسه مُختبَر في ``test_zz_login_is_rate_limited``؛ بقية
    الفحوص تحتاج جلسة صالحة لا أن تصطدم بأثر فحص سابق.
    """
    import time as _time

    poster = session.post if session else (
        lambda url, **kw: httpx.post(f"{BASE_URL}{url}", timeout=30.0, **kw))
    for _attempt in range(5):
        response = poster("/api/auth/login",
                          json={"email": email, "password": require_test_password()})
        if response.status_code != 429:
            return response
        try:
            wait = float(response.json()["error"]["details"]["retry_after"])
        except Exception:
            wait = 5.0
        _time.sleep(min(wait, 65.0) + 1.0)
    return response


def setUpModule() -> None:  # noqa: N802
    if not server_is_up():
        raise unittest.SkipTest("الخادم غير مشغّل — شغّل ./scripts/run_tests.sh")
    STATE["admin"] = ApiClient("admin@masar.test")
    STATE["planner"] = ApiClient("planner@masar.test")
    STATE["supervisor"] = ApiClient("sup.ryd@masar.test")
    STATE["arar"] = ApiClient("sup.arr@masar.test")
    STATE["requester"] = ApiClient("req.phc01@masar.test")


# ================================================== المصادقة والجلسات =======

class TestAuthentication(MasarTestCase):

    def test_protected_endpoints_reject_anonymous(self) -> None:
        """لا نقطة تشغيلية مفتوحة بلا مصادقة."""
        from masar_api.routes import API_ROUTES, PUBLIC_PATHS

        leaked: list[str] = []
        for route in API_ROUTES:
            if route.path in PUBLIC_PATHS or "{" in route.path:
                continue
            if "GET" not in route.methods:
                continue
            response = httpx.get(f"{BASE_URL}{route.path}", timeout=30.0)
            if response.status_code not in (401, 403, 405):
                leaked.append(f"{route.path} → {response.status_code}")
        self.assertEqual(leaked, [], f"نقاط مفتوحة بلا مصادقة: {leaked}")

    def test_forged_token_is_rejected(self) -> None:
        forged = httpx.get(
            f"{BASE_URL}/api/shipments",
            headers={"authorization": "Bearer eyJhbGciOiJub25lIn0.e30."},
            timeout=30.0)
        self.assertEqual(forged.status_code, 401)

    def test_token_from_another_secret_is_rejected(self) -> None:
        from masar_core.security import encode_jwt

        import time as _time

        # يُبنى وقت التشغيل: لا قيمة تشبه سرًّا مكتوبة في المستودع
        wrong_key = "-".join(("attacker", "signing", "material"))
        token = encode_jwt({"sub": str(uuid.uuid4()), "role": "ADMIN",
                            "exp": _time.time() + 600}, secret=wrong_key)
        response = httpx.get(f"{BASE_URL}/api/audit",
                             headers={"authorization": f"Bearer {token}"},
                             timeout=30.0)
        self.assertEqual(response.status_code, 401,
                         "قُبل رمز موقّع بمفتاح غير مفتاح الخادم")

    def test_expired_token_is_rejected(self) -> None:
        import time as _time

        from masar_core.security import encode_jwt

        admin = STATE["admin"]
        token = encode_jwt({"sub": admin.user["id"], "role": "ADMIN",
                            "exp": _time.time() - 60})
        response = httpx.get(f"{BASE_URL}/api/audit",
                             headers={"authorization": f"Bearer {token}"},
                             timeout=30.0)
        self.assertEqual(response.status_code, 401)

    def test_logout_invalidates_the_refresh_token(self) -> None:
        session = httpx.Client(base_url=BASE_URL, timeout=60.0)
        login = login_raw("auditor@masar.test", session)
        self.assertEqual(login.status_code, 200, login.text[:200])
        refresh_cookie = session.cookies.get("masar_refresh")
        self.assertTrue(refresh_cookie, "لم تُضبط كوكي التحديث")

        session.headers["authorization"] = (
            f"Bearer {login.json()['data']['access_token']}")
        session.post("/api/auth/logout", json={})

        replay = httpx.post(f"{BASE_URL}/api/auth/refresh",
                            json={"refresh_token": refresh_cookie}, timeout=30.0)
        self.assertGreaterEqual(replay.status_code, 400,
                                "رمز تحديث مُبطَل ما زال يعمل بعد الخروج")

    def test_refresh_token_reuse_is_detected(self) -> None:
        """إعادة استخدام رمز تحديث مستهلك = تسريب ⇒ تُبطَل العائلة كلها."""
        session = httpx.Client(base_url=BASE_URL, timeout=60.0)
        login = login_raw("tower@masar.test", session)
        self.assertEqual(login.status_code, 200, login.text[:200])
        first = session.cookies.get("masar_refresh")

        rotated = session.post("/api/auth/refresh", json={"refresh_token": first})
        self.assertEqual(rotated.status_code, 200, rotated.text[:200])
        second = session.cookies.get("masar_refresh")
        self.assertNotEqual(first, second, "رمز التحديث لم يُدوَّر")

        replay = httpx.post(f"{BASE_URL}/api/auth/refresh",
                            json={"refresh_token": first}, timeout=30.0)
        self.assertGreaterEqual(replay.status_code, 400,
                                "قُبلت إعادة استخدام رمز تحديث مستهلك")

        after = httpx.post(f"{BASE_URL}/api/auth/refresh",
                           json={"refresh_token": second}, timeout=30.0)
        self.assertGreaterEqual(
            after.status_code, 400,
            "بعد كشف إعادة الاستخدام يجب إبطال العائلة كاملةً لا الرمز المُعاد فقط")

    def test_cookies_are_httponly_and_samesite(self) -> None:
        response = login_raw("auditor@masar.test")
        self.assertEqual(response.status_code, 200)
        raw = "; ".join(
            value for key, value in response.headers.multi_items()
            if key.lower() == "set-cookie")
        self.assertIn("HttpOnly", raw, "كوكي الجلسة يمكن قراءته من JavaScript")
        self.assertIn("SameSite=strict", raw.replace("Strict", "strict"),
                      "كوكي الجلسة بلا حماية SameSite ⇒ عرضة لـ CSRF")

    def test_zz_login_is_rate_limited(self) -> None:
        email = "admin@masar.test"
        statuses = [
            httpx.post(f"{BASE_URL}/api/auth/login",
                       json={"email": email, "password": f"wrong-{index}"},
                       timeout=30.0).status_code
            for index in range(12)
        ]
        self.assertIn(429, statuses,
                      f"لا حد لمحاولات الدخول — تخمين كلمات المرور مفتوح: {statuses}")

    def test_error_message_does_not_reveal_account_existence(self) -> None:
        missing = httpx.post(f"{BASE_URL}/api/auth/login",
                             json={"email": f"{uuid.uuid4().hex}@masar.test",
                                   "password": "whatever"}, timeout=30.0)
        self.assertEqual(missing.status_code, 401)
        body = missing.json()["error"]["message"]
        self.assertNotIn("غير موجود", body,
                         "رسالة الخطأ تكشف وجود الحساب من عدمه")


# ============================================ الوصول لكائنات الغير (IDOR) ===

class TestObjectAccess(MasarTestCase):

    def test_supervisor_cannot_read_another_hubs_route(self) -> None:
        supervisor, arar = STATE["supervisor"], STATE["arar"]
        arar_routes = arar.data(arar.get("/api/routes"))
        if not arar_routes:
            self.skipTest("لا توجد رحلة في مركز عرعر لهذا الفحص")
        response = supervisor.get(f"/api/routes/{arar_routes[0]['id']}")
        self.assertIn(response.status_code, (403, 404),
                      f"تسرّبت رحلة مركز آخر: HTTP {response.status_code}")

    def test_supervisor_cannot_mutate_another_hubs_route(self) -> None:
        supervisor, arar = STATE["supervisor"], STATE["arar"]
        arar_routes = arar.data(arar.get("/api/routes"))
        if not arar_routes:
            self.skipTest("لا توجد رحلة في مركز عرعر لهذا الفحص")
        route_id = arar_routes[0]["id"]
        for path, payload in (
            (f"/api/routes/{route_id}/assign", {"driver_id": str(uuid.uuid4())}),
            (f"/api/routes/{route_id}/unassign", {"reason": "محاولة غير مصرّح بها"}),
            (f"/api/routes/{route_id}/modify",
             {"change_kind": "OTHER", "reason": "محاولة غير مصرّح بها"}),
        ):
            response = supervisor.post(path, json=payload)
            self.assertGreaterEqual(
                response.status_code, 400,
                f"{path} سمح بالتعديل على مركز آخر: HTTP {response.status_code}")

    def test_requester_cannot_read_arbitrary_shipment(self) -> None:
        requester, planner = STATE["requester"], STATE["planner"]
        shipments = planner.data(planner.get("/api/shipments", params={"limit": 50}))
        foreign = [
            row for row in shipments
            if row.get("requester_facility_id") != requester.user.get("facility_id")
        ]
        if not foreign:
            self.skipTest("لا توجد شحنة تخص جهة أخرى")
        response = requester.get(f"/api/shipments/{foreign[0]['id']}")
        self.assertIn(response.status_code, (403, 404),
                      "مقدم الطلب قرأ شحنة ليست لجهته")

    def test_unknown_uuid_does_not_leak_existence(self) -> None:
        supervisor = STATE["supervisor"]
        response = supervisor.get(f"/api/routes/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)

    def test_malformed_id_is_rejected_cleanly(self) -> None:
        """معرّف غير صالح يجب أن يُرفض برسالة، لا أن يُسقط الخادم بخطأ داخلي."""
        supervisor = STATE["supervisor"]
        for bad in ("not-a-uuid", "1 OR 1=1", "%00", "0x1", "null", "-1"):
            response = supervisor.get(f"/api/routes/{bad}")
            self.assertLess(response.status_code, 500,
                            f"المعرّف «{bad}» أنتج خطأ خادم {response.status_code}")
            self.assertGreaterEqual(response.status_code, 400,
                                    f"المعرّف «{bad}» قُبل كأنه صالح")


# ==================================================== الحقن والمدخلات =======

class TestInjection(MasarTestCase):

    SQL_PAYLOADS = (
        "' OR '1'='1",
        "'; DROP TABLE shipments; --",
        "1; DELETE FROM audit_log WHERE 1=1; --",
        "' UNION SELECT NULL, current_user, NULL --",
        "\\'; SELECT pg_sleep(5); --",
    )

    def test_sql_injection_in_query_parameters(self) -> None:
        planner = STATE["planner"]
        for payload in self.SQL_PAYLOADS:
            for path, params in (
                ("/api/shipments", {"status": payload}),
                ("/api/routes", {"service_date": payload}),
                ("/api/md/facilities", {"search": payload}),
                ("/api/reports/grouped", {"group_by": payload}),
            ):
                response = planner.get(path, params=params)
                self.assertLess(
                    response.status_code, 500,
                    f"{path} مع «{payload}» أنتج {response.status_code}: "
                    f"{response.text[:200]}")

    def test_tables_survive_injection_attempts(self) -> None:
        """الإثبات النهائي: الجداول ما زالت موجودة بعد كل المحاولات أعلاه."""
        connection = db_connection()
        try:
            for table in ("shipments", "audit_log", "routes"):
                count = connection.fetch_value(f"SELECT count(*) FROM {table}")
                self.assertIsNotNone(count, f"الجدول {table} لم يعد موجودًا")
        finally:
            connection.close()

    def test_stored_script_is_not_executed_as_html(self) -> None:
        """المحتوى المُخزَّن يعود كبيانات JSON لا كـ HTML قابل للتنفيذ."""
        admin = STATE["admin"]
        payload = "<script>alert('xss')</script>"
        facilities = admin.data(admin.get("/api/md/facilities", params={"limit": 1}))
        if not facilities:
            self.skipTest("لا توجد جهة لاختبارها")
        target = facilities[0]["id"]
        updated = admin.patch(f"/api/md/facilities/{target}",
                              json={"notes": payload})
        self.assertLess(updated.status_code, 500, updated.text[:200])

        response = admin.get(f"/api/md/facilities/{target}")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.headers["content-type"].startswith("application/json"),
            "الاستجابة ليست JSON — احتمال تنفيذ المحتوى في المتصفح")
        self.assertIn("nosniff", response.headers.get("x-content-type-options", ""))

    def test_oversized_payload_is_rejected(self) -> None:
        planner = STATE["planner"]
        response = planner.post("/api/imports", content=b"x" * (30 * 1024 * 1024),
                                headers={"x-file-name": "huge.csv"})
        self.assertGreaterEqual(response.status_code, 400,
                                "قُبل ملف يتجاوز الحد المسموح")
        self.assertLess(response.status_code, 500)

    def test_content_type_spoofing_is_rejected(self) -> None:
        planner = STATE["planner"]
        shipments = planner.data(planner.get("/api/shipments", params={"limit": 1}))
        if not shipments:
            self.skipTest("لا توجد شحنة لرفع مستند عليها")
        response = planner.post(
            f"/api/documents?shipment_id={shipments[0]['id']}&doc_kind=DELIVERY_PROOF",
            content=b"<html><script>alert(1)</script></html>",
            headers={"content-type": "image/png", "x-file-name": "fake.png"})
        self.assertGreaterEqual(response.status_code, 400,
                                "قُبل ملف HTML مُعلَن أنه صورة")


# ================================================== رفع الصلاحيات ===========

class TestPrivilegeEscalation(MasarTestCase):

    def test_user_cannot_promote_themselves(self) -> None:
        supervisor = STATE["supervisor"]
        response = supervisor.patch(f"/api/users/{supervisor.user['id']}",
                                    json={"role": "ADMIN"})
        self.assertGreaterEqual(response.status_code, 400,
                                "المستخدم رفّع دوره بنفسه")

        again = ApiClient("sup.ryd@masar.test")
        self.assertEqual(again.user["role"], "HUB_SUPERVISOR",
                         "تغيّر الدور فعليًا رغم رفض الطلب")

    def test_user_cannot_widen_own_scope(self) -> None:
        supervisor = STATE["supervisor"]
        arar_hub = None
        admin = STATE["admin"]
        for hub in admin.data(admin.get("/api/md/hubs")):
            if hub["code"] == "H-ARR-1":
                arar_hub = hub["id"]
        response = supervisor.patch(
            f"/api/users/{supervisor.user['id']}",
            json={"scopes": [{"scope_type": "HUB", "scope_id": arar_hub}]})
        self.assertGreaterEqual(response.status_code, 400,
                                "المستخدم وسّع نطاقه بنفسه")

    def test_driver_cannot_ingest_temperature(self) -> None:
        """§18: السائق لا يُدخل حرارة — حتى بطلب مباشر متجاوزًا الواجهة."""
        admin = STATE["admin"]
        drivers = admin.data(admin.get("/api/md/drivers"))
        if not drivers:
            self.skipTest("لا يوجد سائق")
        driver = ApiClient(f"{drivers[0]['code'].lower()}@masar.test")
        response = driver.post("/api/temperature/ingest", json={
            "readings": [{"box_id": str(uuid.uuid4()), "celsius": 5.0}],
            "source": "SENSOR"})
        self.assertEqual(response.status_code, 403)

    def test_non_admin_cannot_change_operational_settings(self) -> None:
        supervisor = STATE["supervisor"]
        response = supervisor.post("/api/settings", json={
            "key": "max_shift_hours", "value": 24, "scope_type": "KINGDOM",
            "reason": "محاولة غير مصرّح بها"})
        self.assertEqual(response.status_code, 403,
                         "المشرف عدّل قيدًا تشغيليًا وطنيًا")

    def test_hard_delete_is_denied_even_to_admin(self) -> None:
        """§28: الحذف النهائي غير ممنوح لأي دور افتراضيًا."""
        admin = STATE["admin"]
        matrix = admin.data(admin.get("/api/meta/permissions"))
        row = next(r for r in matrix["rows"] if r["key"] == "data.hard_delete")
        granted = [role for role in matrix["roles"] if row.get(role)]
        self.assertEqual(granted, [],
                         f"الحذف النهائي ممنوح لأدوار: {granted}")

    def test_operational_delete_is_blocked_at_database_level(self) -> None:
        connection = db_connection(as_app=True)
        try:
            with self.assertRaises(Exception) as caught:
                connection.execute("DELETE FROM shipments WHERE true")
            self.assertTrue(str(caught.exception).strip())
        finally:
            connection.close()


# ==================================================== الترويسات والنقل ======

class TestTransportHardening(MasarTestCase):

    REQUIRED_HEADERS = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "content-security-policy": None,
        "referrer-policy": None,
        "permissions-policy": None,
    }

    def test_security_headers_present(self) -> None:
        response = httpx.get(f"{BASE_URL}/api/health", timeout=30.0)
        for header, expected in self.REQUIRED_HEADERS.items():
            self.assertIn(header, response.headers, f"ترويسة ناقصة: {header}")
            if expected:
                self.assertEqual(response.headers[header], expected)

    def test_csp_forbids_inline_and_eval_scripts(self) -> None:
        response = httpx.get(f"{BASE_URL}/", timeout=30.0)
        policy = response.headers.get("content-security-policy", "")
        self.assertIn("script-src 'self'", policy)
        self.assertNotIn("unsafe-eval", policy)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", policy,
                         "سياسة المحتوى تسمح بسكربت مضمّن")
        self.assertIn("frame-ancestors 'none'", policy)

    def test_public_config_carries_no_secrets(self) -> None:
        meta = httpx.get(f"{BASE_URL}/api/meta", timeout=30.0).json()["data"]
        flattened = str(meta).lower()
        for secret in ("secret", "password", "api_key", "apikey",
                       "private", "jwt", "token"):
            self.assertNotIn(secret, flattened,
                             f"إعداد عام يحوي «{secret}»: {meta}")

    def test_server_does_not_advertise_its_stack(self) -> None:
        response = httpx.get(f"{BASE_URL}/api/health", timeout=30.0)
        server = response.headers.get("server", "")
        self.assertNotIn("uvicorn", server.lower(),
                         "الخادم يعلن عن نفسه وإصداره في الترويسات")

    def test_production_config_refuses_weak_defaults(self) -> None:
        """§29: الإعداد الضعيف يجب أن يمنع الإقلاع في الإنتاج لا أن يُحذِّر."""
        import os

        from masar_core.config import Config

        previous = dict(os.environ)
        try:
            os.environ.update({
                "MASAR_ENV": "production",
                # قيمة اصطناعية طويلة بما يكفي لتجاوز فحص الطول، وتحمل علامة
                # «example» فيلتقطها فحص القيم النموذجية. ليست سرًّا
                # استُخدم في أي بيئة.
                "MASAR_JWT_SECRET": "example-placeholder-value-" + "x" * 24,
                "MASAR_DB_SSLMODE": "disable",
                "MASAR_ROUTING_PROVIDER": "haversine",
                "MASAR_ALLOW_TEST_DATA": "true",
            })
            problems = Config().validate()
        finally:
            os.environ.clear()
            os.environ.update(previous)

        self.assertTrue(problems, "الإعداد الإنتاجي الضعيف مرّ بلا اعتراض")
        joined = " ".join(problems)
        for expected in ("JWT", "SSL"):
            self.assertIn(expected, joined.upper(),
                          f"التحقق لم يذكر {expected}: {problems}")


# ================================================== سلامة سجل التدقيق =======

class TestAuditIntegrity(MasarTestCase):

    def test_audit_log_rejects_update_and_delete(self) -> None:
        """العبث بسجل التدقيق مرفوض على مستوى القاعدة — لا على مستوى الواجهة.

        ملاحظتان أُصلحتا بعد أن ظل هذا الاختبار يُتخطّى دائمًا:

        1. ``audit_log`` محميّ بـRLS، وسياسة ``audit_read`` تشترط دورًا رقابيًا.
           اتصال بلا سياق جلسة دوره ``ANONYMOUS``، فيرى **صفر** صفوف مهما بلغ
           عدد السجلات — فيتخطّى الاختبار بحجة «السجل فارغ» وهي غير صحيحة.
           لذا نضبط سياق الجلسة أولًا كما يفعل الخادم.
        2. ``id`` من نوع ``bigint`` لا ``uuid``. المقارنة بـ``$1::uuid`` كانت
           ترمي خطأ نوع، و``assertRaises(Exception)`` كان يبتلعه ويمرّ لسبب
           خاطئ. لذلك نتحقق من رمز الحالة ``42501`` تحديدًا.
        """
        connection = db_connection(as_app=True)
        try:
            connection.execute("SELECT set_config('masar.role', 'ADMIN', false)")
            connection.execute(
                "SELECT set_config('masar.user_id',"
                " (SELECT id::text FROM users LIMIT 1), false)")
            row = connection.fetch_one("SELECT id FROM audit_log LIMIT 1")
            self.assertIsNotNone(
                row, "سجل التدقيق فارغ فعلًا — لا توجد أفعال موثّقة للتحقق منها")
            for statement in (
                "UPDATE audit_log SET action = 'TAMPERED' WHERE id = $1",
                "DELETE FROM audit_log WHERE id = $1",
            ):
                with self.assertRaises(Exception, msg=f"نجح: {statement}") as caught:
                    connection.execute(statement, [row["id"]])
                self.assertIn(
                    "42501", str(caught.exception),
                    f"رُفض لسبب غير المنع: {statement} → {caught.exception}")
        finally:
            connection.close()

    def test_sensitive_actions_are_recorded(self) -> None:
        admin = STATE["admin"]
        rows = admin.data(admin.get("/api/audit", params={"limit": 500}))
        actions = {row["action"] for row in rows}
        self.assertIn("LOGIN_SUCCESS", actions, "الدخول نفسه غير موثّق")

        # بقية الأفعال تتطلب أن تكون عمليات تشغيلية قد جرت على هذه القاعدة.
        # في التشغيل الكامل تسبقها حزمة السيناريوهات؛ منفردةً قد لا تكون جرت،
        # فنُعلن ذلك بدل ادّعاء تغطية لم تحدث.
        operational = {"OPTIMIZER_RUN", "ROUTE_ASSIGN", "PLAN_APPROVE"}
        present = operational & actions
        if not present:
            self.skipTest(
                "لم تجرِ عمليات تشغيلية على هذه القاعدة بعد — "
                "شغّل ./scripts/run_tests.sh للتحقق الكامل")
        for expected in present:
            sample = next(row for row in rows if row["action"] == expected)
            self.assertTrue(sample.get("entity_type"),
                            f"سجل {expected} بلا نوع كيان")

    def test_audit_entries_identify_the_actor(self) -> None:
        admin = STATE["admin"]
        rows = admin.data(admin.get("/api/audit", params={"limit": 50}))
        self.assertTrue(rows)
        for row in rows:
            # محاولة دخول فاشلة ليس لها فاعل مُصادق — وهذا صحيح لا نقص. لكنها
            # يجب أن تبقى **منسوبة** إلى شيء: البريد المُحاوَل وعنوان الشبكة،
            # وإلا صار السجل بلا قيمة تحقيقية.
            attributable = (
                row.get("actor_user_id") or row.get("actor_name")
                or row.get("entity_label") or row.get("ip_address")
            )
            self.assertTrue(attributable, f"سجل تدقيق غير منسوب لأي طرف: {row}")
            self.assertTrue(row.get("action"), "سجل بلا فعل")
            self.assertTrue(row.get("occurred_at"), "سجل بلا زمن")

    def test_auditor_can_read_but_not_write(self) -> None:
        auditor = ApiClient("auditor@masar.test")
        self.assertEqual(auditor.get("/api/audit").status_code, 200)
        blocked = auditor.post("/api/plans/run",
                               json={"hub_ids": [], "dates": []})
        self.assertEqual(blocked.status_code, 403,
                         "المدقق يملك صلاحية تعديل")


# ============================================ الإشعارات الخارجية ===========

class TestNotifications(MasarTestCase):
    """صندوق الصادر: لا يُعرض ما لم يُرسَل على أنه أُرسل (§20/§34)."""

    def test_status_is_declared_honestly(self) -> None:
        status = httpx.get(f"{BASE_URL}/api/notifications/status",
                           timeout=30.0).json()["data"]
        for key in ("provider", "available", "is_real_integration", "message_ar"):
            self.assertIn(key, status)
        self.assertTrue(status["message_ar"].strip())
        if status["provider"] in ("none", "log"):
            self.assertFalse(
                status["is_real_integration"],
                "مزوّد غير حقيقي معروض على أنه تكامل حقيقي")

    def test_outbox_records_instead_of_pretending(self) -> None:
        """بلا مزوّد: الإشعار يُسجَّل NO_PROVIDER لا SENT."""
        import os

        from masar_core.config import get_config
        from masar_api.services import notifications
        from masar_db.driver import SecurityContext, transaction

        admin = STATE["admin"]
        context = SecurityContext(user_id=admin.user["id"], role="ADMIN")

        previous = os.environ.get("MASAR_NOTIFY_PROVIDER")
        os.environ["MASAR_NOTIFY_PROVIDER"] = "none"
        get_config(reload=True)
        try:
            with transaction(context) as conn:
                notification_id = notifications.enqueue(
                    conn, channel="SMS", recipient="+966500000000",
                    body_ar="اختبار: لا مزوّد مُعدّ",
                    dedupe_key=f"test-none-{uuid.uuid4()}", is_test_data=True)
            self.assertIsNotNone(notification_id)

            rows = notifications.list_notifications(context, limit=50)
            row = next(r for r in rows if r["id"] == notification_id)
            self.assertEqual(row["status"], "NO_PROVIDER",
                             "إشعار بلا مزوّد وُسم بحالة توحي بأنه في طريقه")
            self.assertIsNone(row["sent_at"], "وقت إرسال لإشعار لم يُرسل")
        finally:
            if previous is None:
                os.environ.pop("MASAR_NOTIFY_PROVIDER", None)
            else:
                os.environ["MASAR_NOTIFY_PROVIDER"] = previous
            get_config(reload=True)

    def test_delivery_marks_sent_only_after_provider_accepts(self) -> None:
        import os

        from masar_core.config import get_config
        from masar_api.services import notifications
        from masar_db.driver import SecurityContext, transaction

        admin = STATE["admin"]
        context = SecurityContext(user_id=admin.user["id"], role="ADMIN")
        previous = os.environ.get("MASAR_NOTIFY_PROVIDER")
        os.environ["MASAR_NOTIFY_PROVIDER"] = "log"
        get_config(reload=True)
        try:
            with transaction(context) as conn:
                notification_id = notifications.enqueue(
                    conn, channel="EMAIL", recipient="ops@masar.test",
                    subject_ar="اختبار الإرسال", body_ar="نص الاختبار",
                    dedupe_key=f"test-log-{uuid.uuid4()}", is_test_data=True)

            rows = notifications.list_notifications(context, limit=50)
            before = next(r for r in rows if r["id"] == notification_id)
            self.assertEqual(before["status"], "PENDING")

            counters = notifications.deliver_pending(limit=100)
            self.assertGreaterEqual(counters["sent"], 1, counters)

            rows = notifications.list_notifications(context, limit=50)
            after = next(r for r in rows if r["id"] == notification_id)
            self.assertEqual(after["status"], "SENT")
            self.assertIsNotNone(after["sent_at"])
            self.assertEqual(after["provider"], "log")
        finally:
            if previous is None:
                os.environ.pop("MASAR_NOTIFY_PROVIDER", None)
            else:
                os.environ["MASAR_NOTIFY_PROVIDER"] = previous
            get_config(reload=True)

    def test_duplicate_alert_does_not_send_twice(self) -> None:
        from masar_api.services import notifications
        from masar_db.driver import SecurityContext, transaction

        admin = STATE["admin"]
        context = SecurityContext(user_id=admin.user["id"], role="ADMIN")
        key = f"test-dedupe-{uuid.uuid4()}"
        with transaction(context) as conn:
            first = notifications.enqueue(
                conn, channel="SMS", recipient="+966500000001",
                body_ar="تنبيه مكرر", dedupe_key=key, is_test_data=True)
        with transaction(context) as conn:
            second = notifications.enqueue(
                conn, channel="SMS", recipient="+966500000001",
                body_ar="تنبيه مكرر", dedupe_key=key, is_test_data=True)
        self.assertIsNotNone(first)
        self.assertIsNone(second, "تنبيه متكرر أنتج رسالتين")

    def test_notification_requires_recipient_and_body(self) -> None:
        from masar_api.services import notifications
        from masar_core.errors import ValidationError
        from masar_db.driver import SecurityContext, transaction

        admin = STATE["admin"]
        context = SecurityContext(user_id=admin.user["id"], role="ADMIN")
        with transaction(context) as conn:
            for kwargs in (
                {"channel": "SMS", "recipient": "  ", "body_ar": "نص"},
                {"channel": "SMS", "recipient": "+966500000002", "body_ar": " "},
                {"channel": "CARRIER_PIGEON", "recipient": "x", "body_ar": "y"},
            ):
                with self.assertRaises(ValidationError):
                    notifications.enqueue(conn, **kwargs)

    def test_supervisor_sees_only_own_hub_notifications(self) -> None:
        supervisor, arar = STATE["supervisor"], STATE["arar"]
        mine = supervisor.data(supervisor.get("/api/notifications"))
        theirs = arar.data(arar.get("/api/notifications"))
        overlap = {row["id"] for row in mine} & {row["id"] for row in theirs}
        self.assertEqual(overlap, set(),
                         "إشعارات مركز تسرّبت إلى مشرف مركز آخر")


# ======================================== التشفير عند التخزين (§29) ========

class TestEncryptionAtRest(MasarTestCase):

    def test_algorithm_matches_rfc_test_vectors(self) -> None:
        """التنفيذ المحلي يُثبَت بمتجهات RFC 8439 الرسمية لا بالثقة."""
        import binascii

        from masar_core import crypto

        key = bytes(range(0x80, 0xA0))
        nonce = binascii.unhexlify("070000004041424344454647")
        aad = binascii.unhexlify("50515253c0c1c2c3c4c5c6c7")
        plaintext = (
            b"Ladies and Gentlemen of the class of '99: If I could offer you "
            b"only one tip for the future, sunscreen would be it.")
        expected_tag = binascii.unhexlify("1ae10b594f09e26a7e902ecbd0600691")

        ciphertext, tag = crypto._chacha_seal(key, nonce, plaintext, aad)
        self.assertEqual(tag, expected_tag, "التاق يخالف متجه RFC 8439")
        self.assertEqual(
            crypto._chacha_open(key, nonce, ciphertext, tag, aad), plaintext)

    def test_tampered_ciphertext_is_rejected(self) -> None:
        from masar_core import crypto
        from masar_core.errors import ValidationError

        if not crypto.get_keyring().enabled:
            self.skipTest("التشفير غير مفعّل في هذه البيئة")

        payload = bytearray(crypto.encrypt(b"delivery proof", associated=b"k"))
        payload[-1] ^= 0x01
        with self.assertRaises(ValidationError):
            crypto.decrypt(bytes(payload), associated=b"k")

    def test_payload_bound_to_its_storage_key(self) -> None:
        """نقل ملف مشفَّر إلى مفتاح تخزين آخر يجب أن يفشل لا أن يُقرأ."""
        from masar_core import crypto
        from masar_core.errors import ValidationError

        if not crypto.get_keyring().enabled:
            self.skipTest("التشفير غير مفعّل في هذه البيئة")

        payload = crypto.encrypt(b"proof", associated=b"documents/a.png")
        self.assertEqual(crypto.decrypt(payload, associated=b"documents/a.png"),
                         b"proof")
        with self.assertRaises(ValidationError):
            crypto.decrypt(payload, associated=b"documents/b.png")

    def test_stored_document_is_unreadable_on_disk(self) -> None:
        from masar_core import crypto
        from masar_api.services import storage

        if not crypto.get_keyring().enabled:
            self.skipTest("التشفير غير مفعّل في هذه البيئة")

        store = storage.get_store()
        self.assertTrue(isinstance(store, storage.EncryptedStore),
                        "التخزين غير مغلَّف بالتشفير رغم وجود مفاتيح")

        plaintext = "نتيجة تحليل مريض".encode("utf-8")
        key = f"documents/tests/{uuid.uuid4().hex}.bin"
        store.put(key, plaintext, "application/octet-stream")
        try:
            raw = store.inner.get(key)
            self.assertTrue(crypto.is_encrypted(raw), "الملف غير مشفَّر على القرص")
            self.assertNotIn(plaintext, raw, "النص الأصلي ظاهر في الملف المخزَّن")
            self.assertEqual(store.get(key), plaintext, "القراءة لا تعيد الأصل")
        finally:
            store.delete(key)

    def test_key_rotation_keeps_old_files_readable(self) -> None:
        """التدوير عملية تشغيلية: القديم يبقى مقروءًا والجديد يُشفَّر بالجديد."""
        from masar_core import crypto

        first, second = crypto.generate_key(), crypto.generate_key()
        old_ring = crypto.KeyRing({"k1": __import__("base64").b64decode(first)}, "k1")
        payload = None

        import base64

        keys = {"k1": base64.b64decode(first), "k2": base64.b64decode(second)}
        original = crypto._keyring
        try:
            crypto._keyring = old_ring
            payload = crypto.encrypt(b"old file")

            crypto._keyring = crypto.KeyRing(keys, "k2")
            self.assertEqual(crypto.decrypt(payload), b"old file",
                             "ملف قديم صار غير مقروء بعد تدوير المفتاح")
            fresh = crypto.encrypt(b"new file")
            self.assertIn(b"k2", fresh[:16], "الملف الجديد لم يُشفَّر بالمفتاح الفعّال")
        finally:
            crypto._keyring = original

    def test_missing_key_fails_loudly(self) -> None:
        """مفتاح محذوف: خطأ صريح يقول ما المفقود، لا بيانات فاسدة."""
        import base64

        from masar_core import crypto
        from masar_core.errors import DependencyUnavailable

        ring = crypto.KeyRing({"k1": base64.b64decode(crypto.generate_key())}, "k1")
        original = crypto._keyring
        try:
            crypto._keyring = ring
            payload = crypto.encrypt(b"x")
            crypto._keyring = crypto.KeyRing(
                {"k9": base64.b64decode(crypto.generate_key())}, "k9")
            with self.assertRaises(DependencyUnavailable) as caught:
                crypto.decrypt(payload)
            self.assertIn("k1", caught.exception.message)
        finally:
            crypto._keyring = original

    def test_short_key_is_rejected(self) -> None:
        import base64
        import os

        from masar_core import crypto
        from masar_core.errors import ValidationError

        previous = os.environ.get("MASAR_ENCRYPTION_KEYS")
        os.environ["MASAR_ENCRYPTION_KEYS"] = (
            "weak:" + base64.b64encode(b"short").decode())
        try:
            with self.assertRaises(ValidationError):
                crypto.KeyRing()
        finally:
            if previous is None:
                os.environ.pop("MASAR_ENCRYPTION_KEYS", None)
            else:
                os.environ["MASAR_ENCRYPTION_KEYS"] = previous

    def test_status_declares_reality(self) -> None:
        admin = STATE["admin"]
        status = admin.data(admin.get("/api/storage/status"))
        self.assertIn("encrypted_at_rest", status)
        self.assertTrue(status["encryption"]["message_ar"].strip())
        if status["encrypted_at_rest"]:
            self.assertTrue(status["encryption"]["enabled"])
            self.assertIn(status["encryption"]["algorithm"],
                          ("AES-256-GCM", "ChaCha20-Poly1305"))


if __name__ == "__main__":
    unittest.main()


# ======================= بوابة التشفير المعتمد في الإنتاج ====================

class TestProductionEncryptionGate(unittest.TestCase):
    """AES-256-GCM عبر ``cryptography`` شرط لا خيار خارج development/test.

    **الخطر الذي تغلقه هذه البوابة** ليس أن التنفيذ الاحتياطي خاطئ — هو
    مُتحقَّق من صحته مقابل متجهات RFC 8439. الخطر أن الرجوع إليه كان
    **صامتًا**: يستمر النظام في العمل، وتُشفَّر بيانات صحية بتنفيذ لم
    يُراجَع خارجيًا ولم تُثبَت مقاومته لهجمات التوقيت، ولا يعلم أحد إلا عند
    التحقيق في حادثة.
    """

    def setUp(self) -> None:
        import os

        self._environ = dict(os.environ)

    def tearDown(self) -> None:
        import os

        from masar_core.config import get_config

        os.environ.clear()
        os.environ.update(self._environ)
        get_config(reload=True)

    @staticmethod
    def _set_environment(name: str) -> None:
        import os

        from masar_core.config import get_config

        os.environ["MASAR_ENV"] = name
        os.environ.pop("APP_ENV", None)
        get_config(reload=True)

    # ------------------------------------------------------ حالة النجاح ----
    def test_production_accepts_aes_gcm_from_cryptography(self) -> None:
        from masar_core import crypto

        if not crypto.library_available():
            self.skipTest("مكتبة cryptography غير متاحة — حالة النجاح غير قابلة للفحص")

        self._set_environment("production")
        crypto.assert_production_grade()          # لا يرفع

        payload = crypto.encrypt("نتيجة تحليل".encode(), associated=b"doc/1")
        self.assertEqual(crypto.decrypt(payload, associated=b"doc/1"),
                         "نتيجة تحليل".encode())
        # وسم الخوارزمية داخل الحمولة نفسها: A = AES-GCM
        marker = payload[len(crypto.MAGIC) + 1 + payload[len(crypto.MAGIC)]]
        self.assertEqual(bytes([marker]), b"A",
                         "الحمولة لم تُشفَّر بـ AES-GCM رغم توفر المكتبة")

        status = crypto.status()
        self.assertTrue(status["production_grade"])
        self.assertFalse(status["blocked"])
        self.assertEqual(status["algorithm"], "AES-256-GCM")
        self.assertEqual(status["implementation"], "cryptography")

    # ------------------------------------------------------ حالات الرفض ----
    def test_production_refuses_when_library_missing(self) -> None:
        """غياب المكتبة في الإنتاج = فشل صريح، لا رجوع صامت."""
        from masar_core import crypto
        from masar_core.errors import DependencyUnavailable

        self._set_environment("production")
        original = crypto._use_library
        crypto._use_library = lambda: None        # محاكاة غياب المكتبة
        try:
            with self.assertRaises(DependencyUnavailable) as caught:
                crypto.assert_production_grade()
            self.assertIn("cryptography", str(caught.exception))

            with self.assertRaises(DependencyUnavailable):
                crypto.encrypt("لن تُشفَّر".encode())     # المسار الفعلي لا الفحص وحده

            status = crypto.status()
            self.assertTrue(status["blocked"])
            self.assertFalse(status["production_grade"])
        finally:
            crypto._use_library = original

    def test_staging_and_unknown_environments_are_gated_too(self) -> None:
        """البوابة ليست خاصة بكلمة production — كل ما عدا dev/test مشمول."""
        from masar_core import crypto
        from masar_core.errors import DependencyUnavailable

        original = crypto._use_library
        crypto._use_library = lambda: None
        try:
            for environment in ("staging", "production", "uat"):
                self._set_environment(environment)
                with self.assertRaises(DependencyUnavailable,
                                       msg=f"بيئة {environment} مرّت بلا بوابة"):
                    crypto.encrypt("محتوى".encode())
        finally:
            crypto._use_library = original

    def test_fallback_allowed_only_in_development_and_test(self) -> None:
        from masar_core import crypto

        original = crypto._use_library
        crypto._use_library = lambda: None
        try:
            for environment in ("development", "test"):
                self._set_environment(environment)
                payload = crypto.encrypt("محتوى تطوير".encode())
                marker = payload[len(crypto.MAGIC) + 1
                                 + payload[len(crypto.MAGIC)]]
                self.assertEqual(bytes([marker]), b"C",
                                 "التنفيذ الاحتياطي لم يُستخدم رغم غياب المكتبة")
                self.assertEqual(crypto.decrypt(payload), "محتوى تطوير".encode())
                self.assertFalse(crypto.status()["blocked"])
        finally:
            crypto._use_library = original

    def test_app_env_alias_cannot_be_used_to_bypass_the_gate(self) -> None:
        """``APP_ENV=production`` وحده يكفي لتفعيل البوابة.

        أدوات النشر تضبط ``APP_ENV`` عادةً. قراءة ``MASAR_ENV`` وحده تجعل
        خادمًا يظن نفسه في التطوير وهو في الإنتاج — وكل البوابات تُلغى بصمت.
        """
        import os

        from masar_core import crypto
        from masar_core.config import get_config
        from masar_core.errors import DependencyUnavailable

        original = crypto._use_library
        crypto._use_library = lambda: None
        try:
            os.environ.pop("MASAR_ENV", None)
            os.environ["APP_ENV"] = "production"
            get_config(reload=True)
            self.assertEqual(get_config().environment, "production")
            with self.assertRaises(DependencyUnavailable):
                crypto.encrypt("محتوى".encode())

            # وعند التعارض تُغلَّب الأشد، لا الأولى ولا الأخيرة
            os.environ["MASAR_ENV"] = "development"
            os.environ["APP_ENV"] = "production"
            get_config(reload=True)
            self.assertEqual(get_config().environment, "production",
                             "تعارض البيئتين لم يُحسم لصالح الأشد")
            with self.assertRaises(DependencyUnavailable):
                crypto.encrypt("محتوى".encode())
        finally:
            crypto._use_library = original

    def test_legacy_fallback_payload_needs_explicit_opt_in(self) -> None:
        """فك تشفير حمولة احتياطية في الإنتاج يتطلب قرارًا صريحًا."""
        import os

        from masar_core import crypto
        from masar_core.errors import DependencyUnavailable

        original = crypto._use_library
        crypto._use_library = lambda: None
        try:
            self._set_environment("development")
            legacy = crypto.encrypt("محتوى قديم".encode())
        finally:
            crypto._use_library = original

        self._set_environment("production")
        with self.assertRaises(DependencyUnavailable) as caught:
            crypto.decrypt(legacy)
        self.assertIn("MASAR_ALLOW_LEGACY_FALLBACK_DECRYPT", str(caught.exception))

        os.environ["MASAR_ALLOW_LEGACY_FALLBACK_DECRYPT"] = "true"
        self.assertEqual(crypto.decrypt(legacy), "محتوى قديم".encode(),
                         "مخرج الترحيل الصريح لا يعمل — الاسترجاع مستحيل")

    def test_preflight_blocks_production_without_approved_crypto(self) -> None:
        """البوابة نفسها مطبَّقة في فحص الإقلاع لا في المكتبة وحدها."""
        import os
        import subprocess
        import sys

        environment = dict(os.environ)
        environment.update({
            "MASAR_ENV": "production",
            "PYTHONPATH": str(ROOT / "packages"),
            # إعداد إنتاج سليم فيما عدا التشفير، كي يكون سبب الرفض واحدًا
            "MASAR_JWT_SECRET": crypto_secret(),
            "MASAR_DB_SSLMODE": "require",
            "MASAR_ALLOW_TEST_DATA": "false",
            # يجبر مسار الاستيراد على الفشل داخل العملية الفرعية وحدها
            "MASAR_TEST_FORCE_NO_CRYPTOGRAPHY": "1",
        })
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "preflight.py")],
            capture_output=True, text=True, env=environment, timeout=180)

        self.assertEqual(result.returncode, 1,
                         f"preflight قبل إنتاجًا بلا تشفير معتمد:\n{result.stdout[-1200:]}")
        self.assertIn("cryptography", result.stdout,
                      f"سبب الرفض لم يُذكر بوضوح:\n{result.stdout[-1200:]}")


def crypto_secret() -> str:
    """سر JWT قوي مولَّد للحظة الاختبار — لا قيمة مثبّتة في المستودع."""
    import base64
    import os as _os

    return base64.b64encode(_os.urandom(48)).decode()
