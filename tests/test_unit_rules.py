"""اختبارات وحدة للقواعد الحاكمة: آلة الحالة، الصلاحيات، الإعدادات، الأمان.

هذه الطبقة تثبت المبادئ الحاكمة في §2 حيث تُكتب فعليًا — قبل أي قاعدة بيانات
أو شبكة: الانتقالات المسموحة، مصفوفة الأدوار، أن القيود إعدادات لا شيفرة،
وأن أدوات المصادقة ترفض ما يجب رفضه.
"""

from __future__ import annotations

import datetime as dt
import time
import unittest

from masar_core.constants import (
    EXCEPTION_KEEPS_OBLIGATION_OPEN,
    EXCEPTION_REQUIRES_PROOF,
    ExceptionReason,
    PlanStatus,
    Role,
    RouteStatus,
    ShipmentStatus,
)
from masar_core.errors import InvalidTransition
from masar_core.errors import Unauthorized
from masar_core.operational_settings import (
    SEED_SCOPE_OVERRIDES,
    SETTING_INDEX,
    SettingOverride,
    SettingsResolver,
    coerce,
)
from masar_core.permissions import (
    PERMISSION_INDEX,
    ROLE_PERMISSIONS,
    matrix_rows,
    permissions_for,
    requires_reason,
)
from masar_core.security import (
    decode_jwt,
    encode_jwt,
    password_hasher,
    verify_password_constant_time,
)
from masar_core.state_machine import (
    assert_can_cancel_before_pickup,
    assert_delivery_after_pickup,
    assert_route_completable,
    assert_route_startable,
    export_diagram,
    plan_sm,
    route_sm,
    shipment_sm,
)

from .support import MasarTestCase, scenario


# ================================================ آلة الحالة (§21) ==========

class TestStateMachine(MasarTestCase):

    @scenario(8)
    def test_delivery_requires_pickup(self) -> None:
        with self.assertRaises(InvalidTransition) as caught:
            assert_delivery_after_pickup(None, dt.datetime.now(dt.timezone.utc))
        self.assertIn("قبل تسجيل الالتقاط", caught.exception.message)

    @scenario(8)
    def test_delivery_timestamp_cannot_precede_pickup(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        with self.assertRaises(InvalidTransition):
            assert_delivery_after_pickup(now, now - dt.timedelta(minutes=5))

    @scenario(21)
    def test_cancel_before_pickup_blocked_after_custody(self) -> None:
        assert_can_cancel_before_pickup(ShipmentStatus.PUBLISHED)  # مسموح
        with self.assertRaises(InvalidTransition) as caught:
            assert_can_cancel_before_pickup(ShipmentStatus.PICKED_UP)
        self.assertIn("مسار الاستثناء", caught.exception.message,
                      "الرسالة تقترح البديل الصحيح لا ترفض فقط")

    @scenario(22)
    def test_unassign_returns_route_to_planned_and_needs_reason(self) -> None:
        transition = route_sm.transition(RouteStatus.ASSIGNED, RouteStatus.PLANNED)
        self.assertTrue(transition.requires_reason,
                        "إزالة السائق إجراء يتطلب سببًا مكتوبًا")
        with self.assertRaises(InvalidTransition):
            route_sm.check(RouteStatus.ASSIGNED, RouteStatus.PLANNED, reason="  ")
        route_sm.check(RouteStatus.ASSIGNED, RouteStatus.PLANNED,
                       reason="السائق في إجازة طارئة")

    @scenario(22)
    def test_shipment_returns_to_pending_assignment(self) -> None:
        self.assertTrue(
            shipment_sm.can(ShipmentStatus.ASSIGNED, ShipmentStatus.PENDING_ASSIGNMENT)
            or shipment_sm.can(ShipmentStatus.PLANNED, ShipmentStatus.PENDING_ASSIGNMENT),
            f"من ASSIGNED متاح: {shipment_sm.allowed_targets(ShipmentStatus.ASSIGNED)}")

    @scenario(16)
    def test_route_cannot_complete_with_open_shipment(self) -> None:
        with self.assertRaises(InvalidTransition) as caught:
            assert_route_completable([ShipmentStatus.DELIVERED,
                                      ShipmentStatus.PICKED_UP])
        self.assertIn("PICKED_UP", caught.exception.message,
                      "الرسالة تسمّي الحالة غير المحسومة")
        assert_route_completable([ShipmentStatus.DELIVERED, ShipmentStatus.COMPLETED])

    @scenario(36)
    def test_driver_cannot_start_unpublished_route(self) -> None:
        today = dt.date(2026, 9, 6)
        with self.assertRaises(InvalidTransition):
            assert_route_startable(RouteStatus.ASSIGNED, today, today)
        with self.assertRaises(InvalidTransition):
            assert_route_startable(RouteStatus.PUBLISHED,
                                   today + dt.timedelta(days=1), today)
        assert_route_startable(RouteStatus.PUBLISHED, today, today)

    @scenario(26)
    def test_exception_is_not_a_terminal_deletion(self) -> None:
        """§19: تعذر التسليم لا يحذف الشحنة — يبقيها قابلة لإعادة الجدولة."""
        targets = shipment_sm.allowed_targets(ShipmentStatus.EXCEPTION)
        self.assertIn(ShipmentStatus.PENDING_ASSIGNMENT, targets,
                      f"من EXCEPTION متاح: {targets}")
        self.assertIn(ShipmentStatus.DELIVERED, targets)
        for target in targets:
            self.assertTrue(
                shipment_sm.transition(ShipmentStatus.EXCEPTION, target).requires_reason,
                f"الخروج من EXCEPTION إلى {target} يجب أن يوثَّق بسبب")

    @scenario(25, 26)
    def test_exception_reason_classification_is_explicit(self) -> None:
        self.assertIn(ExceptionReason.NO_STAFF, EXCEPTION_REQUIRES_PROOF)
        self.assertIn(ExceptionReason.DELIVERY_DELAYED,
                      EXCEPTION_KEEPS_OBLIGATION_OPEN)
        self.assertNotIn(ExceptionReason.NO_SAMPLES, EXCEPTION_KEEPS_OBLIGATION_OPEN,
                         "عدم وجود عينات أصلًا لا يُبقي التزام تسليم مفتوحًا")

    def test_no_transition_lacks_a_label(self) -> None:
        for machine in (shipment_sm, route_sm, plan_sm):
            for (source, target), transition in machine.table.items():
                self.assertTrue(
                    transition.label_ar.strip(),
                    f"{machine.name}: {source} ← {target} بلا وصف عربي")

    def test_diagrams_export_for_documentation(self) -> None:
        for machine in (shipment_sm, route_sm, plan_sm):
            diagram = export_diagram(machine)
            self.assertTrue(diagram.startswith("stateDiagram-v2"))
            self.assertGreater(len(diagram.splitlines()), len(machine.table))

    def test_plan_failure_is_recoverable(self) -> None:
        self.assertTrue(plan_sm.can(PlanStatus.OPTIMIZING, PlanStatus.FAILED))
        self.assertTrue(plan_sm.can(PlanStatus.FAILED, PlanStatus.OPTIMIZING),
                        "فشل المحرك حالة قابلة لإعادة المحاولة لا طريق مسدود")


# ================================================== الصلاحيات (§5) ==========

class TestPermissions(MasarTestCase):

    @scenario(32)
    def test_supervisor_cannot_upload_national_schedule(self) -> None:
        supervisor = permissions_for(Role.HUB_SUPERVISOR)
        for forbidden in ("schedule.upload", "schedule.commit", "plan.optimize",
                          "plan.approve", "plan.dispatch"):
            self.assertNotIn(forbidden, supervisor,
                             f"المشرف لا يملك {forbidden}")
        self.assertIn("schedule.upload", permissions_for(Role.CENTRAL_PLANNER))

    @scenario(30)
    def test_driver_permissions_are_execution_only(self) -> None:
        driver = permissions_for(Role.DRIVER)
        self.assertIn("routes.execute", driver)
        for forbidden in ("routes.assign", "routes.publish", "plan.read",
                          "reports.read", "audit.read", "temperature.ingest",
                          "shipments.cancel", "tracking.read"):
            self.assertNotIn(forbidden, driver,
                             f"السائق لا يملك {forbidden}")

    @scenario(30)
    def test_driver_cannot_enter_temperature(self) -> None:
        """§18: لا حقل حرارة في تطبيق السائق ولا صلاحية إدخالها."""
        self.assertNotIn("temperature.ingest", permissions_for(Role.DRIVER))
        self.assertNotIn("temperature.read", permissions_for(Role.DRIVER))

    @scenario(31)
    def test_requester_sees_only_own_requests(self) -> None:
        requester = permissions_for(Role.EXTERNAL_REQUESTER)
        self.assertIn("ondemand.create", requester)
        self.assertIn("ondemand.cancel_own", requester)
        for forbidden in ("ondemand.review", "routes.read", "plan.read",
                          "drivers.read", "reports.read"):
            self.assertNotIn(forbidden, requester)

    def test_auditor_is_read_only(self) -> None:
        auditor = permissions_for(Role.AUDITOR)
        self.assertIn("audit.read", auditor)
        writes = [key for key in auditor
                  if key.endswith((".write", ".assign", ".publish", ".execute",
                                   ".approve", ".dispatch", ".commit", ".upload"))]
        self.assertEqual(writes, [], f"المدقق يملك صلاحيات تعديل: {writes}")

    def test_hard_delete_granted_to_nobody_by_default(self) -> None:
        """§28: منع الحذف النهائي دون صلاحية خاصة — وهي غير ممنوحة لأي دور."""
        for role, permissions in ROLE_PERMISSIONS.items():
            self.assertNotIn("data.hard_delete", permissions,
                             f"الدور {role} يملك الحذف النهائي")

    def test_audit_log_is_not_writable_by_any_role(self) -> None:
        """§27: لا يستطيع المستخدم العادي تعديل سجل التدقيق."""
        writable = [key for key in PERMISSION_INDEX if key.startswith("audit.")
                    and not key.endswith(".read")]
        self.assertEqual(writable, [],
                         f"توجد صلاحية كتابة على سجل التدقيق: {writable}")

    def test_dangerous_actions_require_a_written_reason(self) -> None:
        for key in ("routes.unassign", "routes.modify_published", "shipments.cancel",
                    "settings.write", "users.disable", "alerts.act",
                    "exceptions.resolve", "data.hard_delete", "data.archive"):
            self.assertTrue(requires_reason(key), f"{key} يجب أن يتطلب سببًا")

    def test_matrix_covers_every_permission_and_role(self) -> None:
        rows = matrix_rows()
        self.assertEqual(len(rows), len(PERMISSION_INDEX))
        for row in rows:
            self.assertTrue(str(row["name_ar"]).strip(),
                            f"الصلاحية {row['key']} بلا اسم عربي")
            for role in Role:
                self.assertIn(role.value, row)


# =============================================== الإعدادات (§2، §13) ========

class TestOperationalSettings(MasarTestCase):

    def test_every_setting_is_documented_and_bounded(self) -> None:
        for key, spec in SETTING_INDEX.items():
            self.assertTrue(spec.name_ar.strip(), f"{key} بلا اسم عربي")
            self.assertTrue(spec.description_ar.strip(), f"{key} بلا شرح")
            if spec.kind in ("int", "float"):
                self.assertIsNotNone(spec.minimum, f"{key} بلا حد أدنى")
                self.assertIsNotNone(spec.maximum, f"{key} بلا حد أعلى")

    def test_scope_precedence_most_specific_wins(self) -> None:
        resolver = SettingsResolver([
            SettingOverride("max_shift_hours", 10, "KINGDOM", None),
            SettingOverride("max_shift_hours", 9, "REGION", "r1"),
            SettingOverride("max_shift_hours", 8, "CITY", "c1"),
            SettingOverride("max_shift_hours", 7, "HUB", "h1"),
        ])
        self.assertEqual(resolver.effective()["max_shift_hours"], 10)
        self.assertEqual(resolver.effective(region_id="r1")["max_shift_hours"], 9)
        self.assertEqual(
            resolver.effective(region_id="r1", city_id="c1")["max_shift_hours"], 8)
        self.assertEqual(
            resolver.effective(region_id="r1", city_id="c1",
                               hub_id="h1")["max_shift_hours"], 7)

    def test_explain_names_the_source_of_a_value(self) -> None:
        resolver = SettingsResolver([
            SettingOverride("max_shift_hours", 7, "HUB", "h1"),
        ])
        explained = resolver.explain("max_shift_hours", hub_id="h1")
        self.assertEqual(explained["value"], 7)
        self.assertIn("HUB", str(explained["source"]),
                      "الشاشة يجب أن تُظهر من أين جاءت القيمة")

    @scenario(40)
    def test_city_specific_values_live_in_data_not_code(self) -> None:
        """§13: قيم عرعر والرياض تُزرع كتجاوزات نطاق، لا تُكتب في الشيفرة."""
        self.assertEqual(SEED_SCOPE_OVERRIDES["عرعر"]["min_event_gap_minutes"], 20)
        self.assertEqual(SEED_SCOPE_OVERRIDES["الرياض"]["min_event_gap_minutes"], 0)
        self.assertEqual(
            SEED_SCOPE_OVERRIDES["_governorate_default"]["min_event_gap_minutes"], 10)
        self.assertTrue(SEED_SCOPE_OVERRIDES["الرياض"]["use_time_dependent_travel"],
                        "الرياض تعتمد زمن طريق متغيّر لا فاصلًا ثابتًا")

    def test_out_of_range_value_is_rejected(self) -> None:
        from masar_core.errors import ValidationError

        with self.assertRaises(ValidationError):
            coerce("max_shift_hours", 99)


# ==================================================== الأمان (§29) ==========

class TestSecurity(MasarTestCase):

    def test_password_hash_is_salted_and_verifiable(self) -> None:
        first = password_hasher.hash("عيّنة-اختبار-التجزئة-A1!")
        second = password_hasher.hash("عيّنة-اختبار-التجزئة-A1!")
        self.assertNotEqual(first, second, "ملح مختلف لكل تجزئة")
        self.assertNotIn("عيّنة-اختبار-التجزئة-A1!", first, "لا تظهر كلمة المرور في التجزئة")
        self.assertTrue(verify_password_constant_time("عيّنة-اختبار-التجزئة-A1!", first))
        self.assertFalse(verify_password_constant_time("wrong", first))

    def test_missing_hash_still_costs_time(self) -> None:
        """مستخدم غير موجود يجب ألا يُكشف بفارق زمن الاستجابة."""
        self.assertFalse(verify_password_constant_time("anything", None))

    def test_token_rejects_algorithm_swap(self) -> None:
        import base64
        import json

        token = encode_jwt({"sub": "user-1", "exp": time.time() + 60})
        self.assertEqual(decode_jwt(token)["sub"], "user-1")

        header, payload, _signature = token.split(".")
        forged_header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        with self.assertRaises(Unauthorized):
            decode_jwt(f"{forged_header}.{payload}.")

    def test_expired_token_is_rejected(self) -> None:
        token = encode_jwt({"sub": "user-1", "exp": time.time() - 10})
        with self.assertRaises(Unauthorized):
            decode_jwt(token)

    def test_tampered_payload_is_rejected(self) -> None:
        token = encode_jwt({"sub": "user-1", "role": "DRIVER", "exp": time.time() + 60})
        header, _payload, signature = token.split(".")
        import base64
        import json

        forged = base64.urlsafe_b64encode(
            json.dumps({"sub": "user-1", "role": "ADMIN"}).encode()
        ).rstrip(b"=").decode()
        with self.assertRaises(Unauthorized):
            decode_jwt(f"{header}.{forged}.{signature}")

    def test_public_config_never_leaks_secrets(self) -> None:
        """§29: لا تُخزَّن مفاتيح ولا أسرار في ما يصل إلى المتصفح."""
        from masar_core.config import get_config

        public = get_config().public_config()
        flattened = str(public).lower()
        for secret in ("secret", "password", "api_key", "apikey", "token"):
            self.assertNotIn(secret, flattened,
                             f"إعداد عام يحتوي «{secret}»: {public}")


class TestOsrmMatrixChunking(unittest.TestCase):
    """تقسيم مصفوفة OSRM — انحدار يظهر فقط على حجم خطة حقيقي.

    إحداثيات ``/table`` تُرسل في **مسار** الرابط. الطلب غير المقسَّم يعمل في
    كل اختبار صغير ثم ينهار عند أول خطة وطنية بـ414. هذه الاختبارات تثبّت
    السلوك الصحيح بلا حاجة إلى قاعدة بيانات ولا إلى OSRM حقيقي.
    """

    @staticmethod
    def _serve():
        import json
        import threading
        import zlib
        from http.server import BaseHTTPRequestHandler, HTTPServer

        state = {"max_path": 0, "requests": 0, "limit": 8192}

        def value(a: str, b: str) -> tuple[float, float]:
            if a == b:
                return 0.0, 0.0
            seed = zlib.crc32(f"{a}>{b}".encode()) % 900_000
            return float(seed + 100_000), float(seed * 2 + 60)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                state["requests"] += 1
                state["max_path"] = max(state["max_path"], len(self.path))
                if len(self.path) > state["limit"]:
                    self.send_response(414); self.end_headers(); return
                path, _, query_text = self.path.partition("?")
                coords = path.rstrip("/").split("/")[-1].split(";")
                query = dict(p.split("=", 1) for p in query_text.split("&") if "=" in p)
                rows = ([int(v) for v in query["sources"].split(";")]
                        if query.get("sources") else list(range(len(coords))))
                cols = ([int(v) for v in query["destinations"].split(";")]
                        if query.get("destinations") else list(range(len(coords))))
                body = json.dumps({
                    "code": "Ok",
                    "durations": [[value(coords[r], coords[c])[1] for c in cols] for r in rows],
                    "distances": [[value(coords[r], coords[c])[0] for c in cols] for r in rows],
                }).encode()
                self.send_response(200)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, state, value

    @staticmethod
    def _nodes(count: int):
        from masar_opt.model import Node, NodeKind

        return [Node(index=i, kind=NodeKind.PICKUP,
                     lat=24.0 + (i % 40) * 0.11, lon=42.0 + (i // 40) * 0.13,
                     label=f"عقدة {i}")
                for i in range(count)]

    def test_national_sized_matrix_stays_under_url_limits(self) -> None:
        from masar_opt.routing import OSRMProvider

        server, state, _value = self._serve()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            nodes = self._nodes(260)

            unsplit = len(";".join(f"{n.lon:.6f},{n.lat:.6f}" for n in self._nodes(990)))
            self.assertGreater(
                unsplit, state["limit"],
                "الافتراض نفسه سقط: رابط ٩٩٠ عقدة لم يعد يتجاوز الحد")

            provider = OSRMProvider(base_url=base, timeout=10.0, block_size=64)
            provider.matrix(nodes)
            self.assertLessEqual(
                state["max_path"], state["limit"],
                f"أطول رابط {state['max_path']} بايت — فوق حد الخادم")
            self.assertEqual(provider.last_request_count, 25,
                             "عدد الكتل لا يطابق ceil(260/64)²")
        finally:
            server.shutdown()
            server.server_close()

    def test_chunked_result_matches_single_request(self) -> None:
        from masar_opt.routing import OSRMProvider

        server, _state, value = self._serve()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            nodes = self._nodes(9)
            whole = OSRMProvider(base_url=base, timeout=10.0, block_size=100).matrix(nodes)
            split = OSRMProvider(base_url=base, timeout=10.0, block_size=2).matrix(nodes)
            self.assertEqual(whole, split, "التقسيم غيّر النتيجة")

            # وكل خلية في موضعها — بما فيها ما يعبر حدود الكتل
            _minutes, km = split
            for i, j in ((0, 8), (8, 0), (3, 4), (5, 2)):
                expected = value(f"{nodes[i].lon:.6f},{nodes[i].lat:.6f}",
                                 f"{nodes[j].lon:.6f},{nodes[j].lat:.6f}")[0] / 1000.0
                self.assertAlmostEqual(km[i][j], expected, places=6,
                                       msg=f"الخلية ({i},{j}) في الموضع الخطأ")
            self.assertTrue(all(km[i][i] == 0.0 for i in range(9)))
        finally:
            server.shutdown()
            server.server_close()

    def test_permanent_failure_stops_after_declared_attempts(self) -> None:
        from masar_core.errors import DependencyUnavailable
        from masar_opt.routing import OSRMProvider

        provider = OSRMProvider(base_url="http://127.0.0.1:1", timeout=1.0,
                                max_attempts=2, retry_pause_seconds=0.0)
        with self.assertRaises(DependencyUnavailable) as caught:
            provider.matrix(self._nodes(2))
        self.assertIn("2 محاولات", str(caught.exception))
        self.assertEqual(provider.last_request_count, 2,
                         "عدد المحاولات لا يطابق المُعلن — إعادة لا نهائية أو ناقصة")


if __name__ == "__main__":
    unittest.main()
