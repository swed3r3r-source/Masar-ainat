"""سيناريوهات محرك المسارات (§30: ١–١٨، ٤٠، ٤٣، ٤٤، ٤٥).

هذه الاختبارات لا تمس قاعدة البيانات ولا الشبكة: تبني مسائل تركيبية بمواقع
حقيقية تقريبية وتستدعي **نفس** دوال التقييم والحل التي يستدعيها التطبيق.
المرجع الوحيد للشرعية هو ``evaluate_route``؛ فما يثبت هنا يثبت في الإنتاج.
"""

from __future__ import annotations

import datetime as dt
import time
import unittest

from masar_core.errors import DependencyUnavailable, OptimizationFailed
from masar_opt.engine import run_engine
from masar_opt.evaluate import evaluate_route
from masar_opt.exact import solve_exact
from masar_opt.model import NodeKind, to_datetime
from masar_opt.objective import objective_vector
from masar_opt.routing import FailingProvider, build_travel_matrix
from masar_opt.solver import SolveOptions

from .support import (
    MasarTestCase,
    at,
    hub,
    pair_nodes,
    problem,
    scenario,
    sequence_of,
    settings,
    shipment,
    vehicle,
)

FAST = SolveOptions(time_limit_seconds=2.0, seed=11)


def plan(prob, **kwargs):
    return run_engine(prob, options=kwargs.pop("options", FAST),
                      compute_reference_plan=False, **kwargs)


# =================================================== ١–٤: الأحجام الأساسية ==

class TestBasicSizes(MasarTestCase):

    @scenario(1)
    def test_single_shipment_single_driver(self) -> None:
        prob = problem([shipment(1, pickup="PHC1")], [vehicle(1)])
        result = plan(prob)

        routes = result.solution.used_routes()
        self.assertEqual(len(routes), 1, "شحنة واحدة يجب أن تُنتج رحلة واحدة")
        self.assertEqual(len(routes[0].sequence), 2, "التقاط واحد وتسليم واحد")
        self.assertEqual(result.metrics["planned_shipment_count"], 1)
        self.assertEqual(result.metrics["unplannable_count"], 0)
        self.assertTrue(routes[0].evaluation.feasible)

        # التحقق المستقل: الحل المضبوط يعطي نفس عدد السائقين
        exact = solve_exact(prob)
        self.assertIsNotNone(exact, "المسألة صغيرة بما يكفي للحل المضبوط")
        self.assertEqual(
            objective_vector(exact, prob)[1], objective_vector(result.solution, prob)[1],
            "الاستدلال يطابق الحل المضبوط في عدد السائقين")

    @scenario(2)
    def test_many_shipments_single_driver(self) -> None:
        shipments = [
            shipment(i, pickup=code,
                     window=(at(7, 15) + dt.timedelta(hours=i - 1),
                             at(7, 45) + dt.timedelta(hours=i - 1)),
                     sla=at(16, 0))
            # الترتيب الجغرافي متدرج (شمال شرق ← وسط ← جنوب) كي تكون النوافذ
            # المتتابعة قابلة للتنفيذ فعليًا بسائق واحد
            for i, code in enumerate(["PHC1", "PHC4", "PHC2", "PHC3"], start=1)
        ]
        prob = problem(shipments, [vehicle(1)])
        result = plan(prob)

        routes = result.solution.used_routes()
        self.assertEqual(len(routes), 1, "سائق واحد متاح ⇒ رحلة واحدة")
        self.assertEqual(result.metrics["planned_shipment_count"], 4,
                         "كل الشحنات مخططة على السائق الوحيد")
        self.assertLessEqual(routes[0].evaluation.working_minutes, 600.0)

    @scenario(3)
    def test_many_shipments_many_drivers(self) -> None:
        # نوافذ متزامنة ضيقة تفرض أكثر من سائق
        shipments = [
            shipment(i, pickup=code, window=(at(8, 0), at(8, 20)), sla=at(12, 0))
            for i, code in enumerate(["PHC1", "PHC2", "PHC3", "PHC5"], start=1)
        ]
        prob = problem(shipments, [vehicle(i) for i in range(1, 5)])
        result = plan(prob)

        routes = result.solution.used_routes()
        self.assertGreater(len(routes), 1,
                           "نوافذ متزامنة في مواقع متباعدة تستلزم أكثر من سائق")
        self.assertEqual(result.metrics["planned_shipment_count"], 4)
        assigned = [s for route in routes for s in route.sequence]
        self.assertEqual(len(assigned), len(set(assigned)),
                         "لا محطة مكررة بين رحلتين")

    @scenario(4)
    def test_multi_depot(self) -> None:
        hubs = [hub("HUB_RYD", hub_id="hub_ryd"), hub("HUB_ARR", hub_id="hub_arr")]
        shipments = [
            shipment(1, pickup="PHC1", dropoff="LAB", hub_id="hub_ryd"),
            shipment(2, pickup="PHC2", dropoff="LAB", hub_id="hub_ryd"),
            shipment(3, pickup="PHC_RFH", dropoff="LAB_ARR", hub_id="hub_arr",
                     window=(at(10, 0), at(13, 0)), sla=at(20, 0)),
        ]
        vehicles = [vehicle(1, hub_id="hub_ryd"), vehicle(2, hub_id="hub_ryd"),
                    vehicle(3, hub_id="hub_arr", end=at(23), shift_minutes=720)]
        prob = problem(shipments, vehicles, hubs=hubs)
        result = plan(prob)

        self.assertEqual(result.metrics["unplannable_count"], 0,
                         f"غير مخطط: {result.unplannable}")
        for route in result.solution.used_routes():
            vehicle_spec = prob.vehicles[route.vehicle_index]
            hub_ids = {prob.nodes[i].hub_id for i in route.sequence}
            self.assertEqual(
                hub_ids, {vehicle_spec.hub_id},
                "كل رحلة تخدم شحنات مركز انطلاقها فقط — لا خلط بين المراكز")


# ================================================ ٥–٦: الاستحالة المعلنة ====

class TestInfeasibility(MasarTestCase):

    @scenario(5)
    def test_impossible_pickup_window(self) -> None:
        """نافذة التقاط تنتهي قبل أن يصل أسرع سائق ⇒ رفض بسبب مكتوب."""
        far = shipment(1, pickup="PHC_RFH", dropoff="LAB",
                       window=(at(6, 0), at(6, 10)), sla=at(20, 0))
        prob = problem([far], [vehicle(1, start=at(6, 0))])
        result = plan(prob)

        self.assertEqual(result.metrics["planned_shipment_count"], 0)
        self.assertEqual(len(result.unplannable), 1)
        rejection = result.unplannable[0]
        self.assertReasoned(rejection, "message_ar", "reason", "rule")
        self.assertIn(rejection["rule"], ("PRE-02", "HC-02"),
                      f"توقعنا خرق نافذة الالتقاط، وجدنا {rejection}")
        self.assertEqual(rejection["reason"], "IMPOSSIBLE_PICKUP_WINDOW")

    @scenario(6)
    def test_impossible_sla(self) -> None:
        """موعد تسليم قبل أن يكتمل الالتقاط ⇒ رفض بسبب SLA لا بسبب غامض."""
        impossible = shipment(1, pickup="PHC1", dropoff="LAB",
                              window=(at(8, 0), at(8, 30)), sla=at(8, 15))
        prob = problem([impossible], [vehicle(1)])
        result = plan(prob)

        self.assertEqual(len(result.unplannable), 1)
        rejection = result.unplannable[0]
        self.assertReasoned(rejection, "message_ar")
        self.assertIn(rejection["rule"], ("PRE-03", "HC-03"))
        self.assertIn("IMPOSSIBLE_SLA", rejection["reason"])

    @scenario(5, 6)
    def test_rejection_never_silent(self) -> None:
        """HC-19: لا شحنة تُترك بلا سبب — ولو كان السبب مركّبًا."""
        shipments = [
            shipment(1, pickup="PHC_RFH", window=(at(6, 0), at(6, 5)), sla=at(7, 0)),
            shipment(2, pickup="PHC1", window=(at(8, 0), at(8, 30)), sla=at(8, 5)),
        ]
        prob = problem(shipments, [vehicle(1)])
        result = plan(prob)
        self.assertEqual(len(result.unplannable), 2)
        for rejection in result.unplannable:
            self.assertReasoned(rejection, "message_ar", "reason", "rule")
            self.assertGreater(len(rejection["message_ar"]), 15,
                               "السبب يجب أن يكون مفهومًا لا رمزًا")


# =========================================== ٧–١٠: الأسبقية وترتيب المحطات ==

class TestPrecedence(MasarTestCase):

    def _single(self):
        prob = problem([shipment(1, pickup="PHC1")], [vehicle(1)])
        return prob, pair_nodes(prob, 0)

    @scenario(7)
    def test_pickup_then_delivery_is_feasible(self) -> None:
        prob, (pickup, delivery) = self._single()
        evaluation = evaluate_route(prob, prob.vehicles[0], [pickup, delivery])
        self.assertTrue(evaluation.feasible,
                        [v.message_ar for v in evaluation.violations])
        self.assertEqual(prob.nodes[evaluation.timings[0].node_index].kind,
                         NodeKind.PICKUP)
        self.assertLess(evaluation.timings[0].service_end,
                        evaluation.timings[1].arrival + 1e-6,
                        "التسليم بعد اكتمال الالتقاط زمنيًا")

    @scenario(8)
    def test_delivery_before_pickup_rejected(self) -> None:
        prob, (pickup, delivery) = self._single()
        evaluation = evaluate_route(prob, prob.vehicles[0], [delivery, pickup])
        self.assertViolation(evaluation, "HC-01")

    @scenario(8)
    def test_delivery_without_pickup_rejected(self) -> None:
        prob, (_pickup, delivery) = self._single()
        evaluation = evaluate_route(prob, prob.vehicles[0], [delivery])
        self.assertViolation(evaluation, "HC-01")

    @scenario(9)
    def test_two_pickups_same_facility_must_deliver_first(self) -> None:
        """HC-11: التقاطان من الجهة نفسها — الأول يُسلَّم قبل تنفيذ الثاني."""
        shipments = [
            shipment(1, pickup="PHC1", window=(at(8, 0), at(8, 30)), sla=at(13, 0)),
            shipment(2, pickup="PHC1", window=(at(10, 0), at(10, 30)), sla=at(15, 0)),
        ]
        prob = problem(shipments, [vehicle(1)])

        self.assertEqual(prob.shipments[0].delivery_before_pickups, (1,),
                         "القيد يُولَّد آليًا من البيانات لا يُكتب يدويًا")

        p1, d1 = pair_nodes(prob, 0)
        p2, d2 = pair_nodes(prob, 1)

        bad = evaluate_route(prob, prob.vehicles[0], [p1, p2, d1, d2])
        self.assertViolation(bad, "HC-11")

        good = evaluate_route(prob, prob.vehicles[0], [p1, d1, p2, d2])
        self.assertTrue(good.feasible, [v.message_ar for v in good.violations])

    @scenario(10)
    def test_multiple_pickups_before_one_delivery_round(self) -> None:
        """جهات مختلفة: يجوز تجميع عدة التقاطات قبل التسليم."""
        shipments = [
            shipment(i, pickup=code,
                     window=(at(7, 45) + dt.timedelta(minutes=30 * (i - 1)),
                             at(8, 15) + dt.timedelta(minutes=30 * (i - 1))),
                     sla=at(14, 0))
            for i, code in enumerate(["PHC3", "PHC2", "PHC4"], start=1)
        ]
        prob = problem(shipments, [vehicle(1)])
        pickups = [pair_nodes(prob, i)[0] for i in range(3)]
        deliveries = [pair_nodes(prob, i)[1] for i in range(3)]

        evaluation = evaluate_route(prob, prob.vehicles[0], pickups + deliveries)
        self.assertTrue(evaluation.feasible,
                        [v.message_ar for v in evaluation.violations])

        result = plan(prob)
        route = result.solution.used_routes()[0]
        first_delivery = next(i for i, n in enumerate(route.sequence)
                              if prob.nodes[n].kind is NodeKind.DELIVERY)
        self.assertGreaterEqual(
            first_delivery, 2,
            "المحرك يجمع أكثر من التقاط قبل أول تسليم حين يكون ذلك أقصر")


# ==================================================== ١١–١٢: قيد عدم الخلط ==

class TestMixing(MasarTestCase):

    @scenario(11)
    def test_hospital_and_health_center_cannot_mix(self) -> None:
        shipments = [
            shipment(1, pickup="HOSP1", window=(at(8, 0), at(9, 0)), sla=at(13, 0)),
            shipment(2, pickup="PHC4", window=(at(8, 0), at(9, 0)), sla=at(13, 0)),
        ]
        prob = problem(shipments, [vehicle(1), vehicle(2)])

        p1, d1 = pair_nodes(prob, 0)
        p2, d2 = pair_nodes(prob, 1)
        mixed = evaluate_route(prob, prob.vehicles[0], [p1, p2, d1, d2])
        self.assertViolation(mixed, "HC-13")

        result = plan(prob)
        for route in result.solution.used_routes():
            classes = {prob.nodes[i].mixing_class for i in route.sequence
                       if prob.nodes[i].kind is NodeKind.PICKUP}
            self.assertLessEqual(
                len(classes & {"PRIMARY_CARE", "SECONDARY_CARE"}), 1,
                "المحرك لا ينتج رحلة تخلط مستشفى ومركزًا صحيًا")
        self.assertEqual(result.metrics["planned_shipment_count"], 2,
                         "القيد يفرّق الرحلتين ولا يُسقط شحنة")

    @scenario(11)
    def test_mixing_rule_is_configurable_not_hardcoded(self) -> None:
        """§2: القيد إعداد، لا سطر في الكود — إطفاؤه يغيّر النتيجة فعلًا."""
        shipments = [
            shipment(1, pickup="HOSP1", window=(at(8, 0), at(9, 0)), sla=at(13, 0)),
            shipment(2, pickup="PHC4", window=(at(8, 0), at(9, 0)), sla=at(13, 0)),
        ]
        prob = problem(shipments, [vehicle(1)],
                       effective=settings(enforce_facility_mixing_rule=False))
        p1, d1 = pair_nodes(prob, 0)
        p2, d2 = pair_nodes(prob, 1)
        evaluation = evaluate_route(prob, prob.vehicles[0], [p1, p2, d1, d2])
        self.assertTrue(evaluation.feasible,
                        "بإطفاء الإعداد يصبح الخلط مسموحًا — إذًا القيد ليس مكتوبًا في الكود")

    @scenario(12)
    def test_blood_bank_exemption(self) -> None:
        """بنك الدم صنف معفى: يجوز خلطه مع صنف مقيّد واحد."""
        shipments = [
            shipment(1, pickup="BLOOD1", window=(at(8, 0), at(9, 0)), sla=at(13, 0)),
            shipment(2, pickup="PHC4", window=(at(8, 0), at(9, 0)), sla=at(13, 0)),
        ]
        prob = problem(shipments, [vehicle(1)])
        p1, d1 = pair_nodes(prob, 0)
        p2, d2 = pair_nodes(prob, 1)
        evaluation = evaluate_route(prob, prob.vehicles[0], [p1, p2, d1, d2])

        self.assertTrue(evaluation.feasible,
                        [v.message_ar for v in evaluation.violations])
        self.assertTrue(evaluation.mixing_exemption_used,
                        "التقييم يوثّق استخدام الاستثناء صراحةً")
        self.assertIn("BLOOD", evaluation.mixing_classes)

    @scenario(12)
    def test_exemption_does_not_open_the_gate(self) -> None:
        """الاستثناء لبنك الدم فقط — لا يبيح خلط مستشفى ومركز صحي معه."""
        shipments = [
            shipment(1, pickup="BLOOD1", window=(at(8, 0), at(9, 30)), sla=at(14, 0)),
            shipment(2, pickup="PHC4", window=(at(8, 0), at(9, 30)), sla=at(14, 0)),
            shipment(3, pickup="HOSP1", window=(at(8, 0), at(9, 30)), sla=at(14, 0)),
        ]
        prob = problem(shipments, [vehicle(1)])
        nodes = [n for i in range(3) for n in pair_nodes(prob, i)]
        pickups = nodes[0::2]
        deliveries = nodes[1::2]
        evaluation = evaluate_route(prob, prob.vehicles[0], pickups + deliveries)
        self.assertViolation(evaluation, "HC-13")


# ================================================== ١٣–١٤: الرحلات البعيدة ==

class TestLongHaul(MasarTestCase):

    def _arar_problem(self, places, *, vehicles_count=1, **kwargs):
        hubs = [hub("HUB_ARR", hub_id="hub_arr", opens=5, closes=23)]
        shipments = [
            shipment(i, pickup=code, dropoff="LAB_ARR", hub_id="hub_arr",
                     window=(at(6, 0), at(12, 0)), sla=at(22, 0))
            for i, code in enumerate(places, start=1)
        ]
        vehicles = [
            vehicle(i, hub_id="hub_arr", start=at(5), end=at(23), shift_minutes=720)
            for i in range(1, vehicles_count + 1)
        ]
        return problem(shipments, vehicles, hubs=hubs, **kwargs)

    @scenario(13)
    def test_long_haul_detected_and_flagged(self) -> None:
        prob = self._arar_problem(["PHC_RFH"])
        result = plan(prob)
        routes = result.solution.used_routes()
        self.assertEqual(len(routes), 1, f"غير مخطط: {result.unplannable}")
        evaluation = routes[0].evaluation
        self.assertTrue(evaluation.is_long_haul,
                        f"أقصى مسافة عن المركز {evaluation.max_hub_distance_km:.0f} كم")
        self.assertGreaterEqual(evaluation.max_hub_distance_km,
                                prob.settings.long_haul_km)

    @scenario(13)
    def test_long_haul_threshold_is_a_setting(self) -> None:
        prob = self._arar_problem(["PHC_RFH"], effective=settings(long_haul_km=900))
        result = plan(prob)
        self.assertFalse(result.solution.used_routes()[0].evaluation.is_long_haul,
                         "برفع العتبة تتوقف نفس الرحلة عن كونها بعيدة — القيمة إعداد")

    @scenario(14)
    def test_no_city_hopping_after_long_haul(self) -> None:
        """HC-16: بعد قطع مسافة بعيدة لا يُضاف التقاط قريب من المركز."""
        hubs = [hub("HUB_ARR", hub_id="hub_arr", opens=5, closes=23)]
        shipments = [
            shipment(1, pickup="PHC_RFH", dropoff="LAB_ARR", hub_id="hub_arr",
                     window=(at(6, 0), at(12, 0)), sla=at(22, 0)),
            shipment(2, pickup="LAB_ARR", dropoff="LAB_ARR", hub_id="hub_arr",
                     window=(at(6, 0), at(20, 0)), sla=at(22, 0),
                     facility_suffix="-near"),
        ]
        prob = problem(
            shipments,
            [vehicle(1, hub_id="hub_arr", start=at(5), end=at(23), shift_minutes=900)],
            hubs=hubs)
        p_far, d_far = pair_nodes(prob, 0)
        p_near, d_near = pair_nodes(prob, 1)

        evaluation = evaluate_route(
            prob, prob.vehicles[0], [p_far, p_near, d_far, d_near])
        self.assertViolation(evaluation, "HC-16")

    @scenario(14)
    def test_two_long_hauls_need_two_drivers(self) -> None:
        """رحلتان بعيدتان في اتجاهين متعاكسين لا تجتمعان على سائق واحد."""
        single = self._arar_problem(["PHC_RFH", "PHC_TRF"], vehicles_count=1)
        result_single = plan(single)
        self.assertGreater(
            len(result_single.unplannable), 0,
            "سائق واحد لا يستطيع تنفيذ رحلتين بعيدتين متعاكستين في يوم واحد")
        for rejection in result_single.unplannable:
            self.assertReasoned(rejection, "message_ar")

        both = self._arar_problem(["PHC_RFH", "PHC_TRF"], vehicles_count=2)
        result_both = plan(both)
        self.assertEqual(result_both.metrics["unplannable_count"], 0,
                         f"بسائقين تُخطط الاثنتان: {result_both.unplannable}")
        self.assertEqual(len(result_both.solution.used_routes()), 2)

    @scenario(14)
    def test_estimation_explains_long_haul_driver_cost(self) -> None:
        """تقدير السائقين يفصح عن السائق الإضافي الناتج عن قيد الرحلات البعيدة."""
        prob = self._arar_problem(["PHC_RFH", "PHC_TRF"], vehicles_count=2)
        result = run_engine(prob, options=FAST, compute_reference_plan=False,
                            available_drivers_by_hub={"hub_arr": 2})
        estimate = result.estimations[0]
        self.assertTrue(estimate.justification, "كل سائق مقدَّر له تبرير")
        for item in estimate.justification:
            self.assertReasoned(item, "label_ar", "detail_ar")


# =============================================== ١٥–١٧: الوردية ونهاية الرحلة =

class TestShift(MasarTestCase):

    @scenario(15)
    def test_shift_limit_enforced(self) -> None:
        prob = problem(
            [shipment(1, pickup="PHC1", window=(at(7, 0), at(7, 30)), sla=at(20, 0)),
             shipment(2, pickup="PHC2", window=(at(18, 0), at(18, 30)), sla=at(21, 0))],
            [vehicle(1, start=at(6), end=at(23), shift_minutes=600)])
        p1, d1 = pair_nodes(prob, 0)
        p2, d2 = pair_nodes(prob, 1)

        evaluation = evaluate_route(prob, prob.vehicles[0], [p1, d1, p2, d2])
        self.assertViolation(evaluation, "HC-05")
        self.assertGreater(evaluation.violations[0].slack_minutes * -1, 0,
                           "الرسالة تحمل مقدار التجاوز بالدقائق")

    @scenario(15)
    def test_shift_limit_is_a_setting(self) -> None:
        prob = problem(
            [shipment(1, pickup="PHC1", window=(at(7, 0), at(7, 30)), sla=at(20, 0)),
             shipment(2, pickup="PHC2", window=(at(18, 0), at(18, 30)), sla=at(21, 0))],
            [vehicle(1, start=at(6), end=at(23), shift_minutes=900)])
        p1, d1 = pair_nodes(prob, 0)
        p2, d2 = pair_nodes(prob, 1)
        evaluation = evaluate_route(prob, prob.vehicles[0], [p1, d1, p2, d2])
        self.assertTrue(evaluation.feasible,
                        [v.message_ar for v in evaluation.violations])

    @scenario(15)
    def test_engine_never_publishes_an_over_shift_route(self) -> None:
        shipments = [
            shipment(i, pickup=code,
                     window=(at(7, 0) + dt.timedelta(hours=3 * (i - 1)),
                             at(7, 30) + dt.timedelta(hours=3 * (i - 1))),
                     sla=at(23, 0))
            for i, code in enumerate(["PHC1", "PHC2", "PHC3", "PHC5"], start=1)
        ]
        prob = problem(shipments, [vehicle(i, end=at(23)) for i in range(1, 4)])
        result = plan(prob)
        for route in result.solution.used_routes():
            self.assertLessEqual(route.evaluation.working_minutes, 600.0 + 1e-6)

    @scenario(16)
    def test_route_ends_at_last_delivery(self) -> None:
        shipments = [shipment(i, pickup=code,
                              window=(at(7, 45) + dt.timedelta(minutes=45 * (i - 1)),
                                      at(8, 15) + dt.timedelta(minutes=45 * (i - 1))),
                              sla=at(14, 0))
                     for i, code in enumerate(["PHC4", "PHC1"], start=1)]
        prob = problem(shipments, [vehicle(1)])
        result = plan(prob)
        route = result.solution.used_routes()[0]
        evaluation = route.evaluation

        last_node = prob.nodes[route.sequence[-1]]
        self.assertIs(last_node.kind, NodeKind.DELIVERY,
                      "آخر محطة في الرحلة تسليم — لا محطة بعده")
        self.assertAlmostEqual(evaluation.end_at, evaluation.timings[-1].departure,
                               delta=1e-6,
                               msg="نهاية الرحلة = لحظة انتهاء آخر تسليم")

    @scenario(17)
    def test_no_forced_return_to_hub(self) -> None:
        prob = problem([shipment(1, pickup="PHC1")], [vehicle(1)])
        result = plan(prob)
        route = result.solution.used_routes()[0]

        self.assertNotIn(NodeKind.START,
                         [prob.nodes[i].kind for i in route.sequence],
                         "لا تُضاف عقدة عودة إلى مركز الانطلاق")
        without_return = route.evaluation.distance_km

        # الإعداد موجود لمن يريده — والفرق يظهر في المسافة، فالسلوك حقيقي
        prob_return = problem([shipment(1, pickup="PHC1")], [vehicle(1)],
                              effective=settings(require_return_to_hub=True))
        result_return = plan(prob_return)
        with_return = result_return.solution.used_routes()[0].evaluation.distance_km
        self.assertGreater(with_return, without_return,
                           "تفعيل الإعداد يضيف مقطع العودة فعليًا إلى المسافة")

    @scenario(17)
    def test_return_leg_excluded_from_shift_by_default(self) -> None:
        prob = problem([shipment(1, pickup="PHC1")], [vehicle(1)])
        base = plan(prob).solution.used_routes()[0].evaluation.working_minutes

        counted = problem([shipment(1, pickup="PHC1")], [vehicle(1)],
                          effective=settings(count_return_leg_in_shift=True))
        counted_minutes = plan(counted).solution.used_routes()[0].evaluation.working_minutes
        self.assertGreater(counted_minutes, base,
                           "زمن العودة لا يُحتسب افتراضيًا (حلّ التعارض T-3)")


# ================================================ ١٨: الرحلة الثانية المتسلسلة

class TestChainedRoute(MasarTestCase):

    @scenario(18)
    def test_second_route_starts_from_last_delivery(self) -> None:
        """الرحلة الثانية تنطلق من موقع آخر تسليم، لا من مركز الانطلاق."""
        second = shipment(1, pickup="PHC1", window=(at(13, 0), at(14, 0)),
                          sla=at(18, 0))

        from_hub = problem([second], [vehicle(1, start=at(12, 30))])
        from_lab = problem([second], [vehicle(1, start=at(12, 30),
                                              start_at_place="LAB")])

        start_hub = from_hub.vehicles[0].start_node
        start_lab = from_lab.vehicles[0].start_node
        self.assertIs(from_lab.nodes[start_lab].kind, NodeKind.START)
        self.assertAlmostEqual(from_lab.nodes[start_lab].lat, 24.6877, places=3,
                               msg="نقطة البداية هي موقع آخر تسليم فعليًا")

        pickup_hub = pair_nodes(from_hub, 0)[0]
        pickup_lab = pair_nodes(from_lab, 0)[0]
        leg_from_hub = from_hub.travel.km(start_hub, pickup_hub)
        leg_from_lab = from_lab.travel.km(start_lab, pickup_lab)
        self.assertNotAlmostEqual(
            leg_from_hub, leg_from_lab, places=2,
            msg="المقطع الأول يُحتسب من نقطة البداية الحقيقية لا من المركز")

        result = plan(from_lab)
        route = result.solution.used_routes()[0]
        self.assertEqual(route.evaluation.timings[0].leg_km, leg_from_lab)


# ================================================= ٤٠: أزمنة الذروة بالرياض ==

class TestPeakTraffic(MasarTestCase):

    def _riyadh(self, **overrides):
        return settings(
            use_time_dependent_travel=True,
            peak_periods=["06:30-09:00", "15:30-18:30"],
            peak_travel_multiplier=1.45,
            min_event_gap_minutes=0,
            **overrides,
        )

    @scenario(40)
    def test_peak_profile_changes_travel_time(self) -> None:
        prob = problem(
            [shipment(1, pickup="PHC1", window=(at(8, 0), at(8, 30)), sla=at(13, 0))],
            [vehicle(1)], effective=self._riyadh())

        self.assertTrue(prob.travel.has_peak_profile,
                        "الرياض تعمل بمصفوفة ذروة، لا بفاصل ثابت")
        pickup = pair_nodes(prob, 0)[0]
        start = prob.vehicles[0].start_node
        peak = prob.travel.minutes(start, pickup, at(8, 0).timestamp() / 60.0)
        off_peak = prob.travel.minutes(start, pickup, at(12, 0).timestamp() / 60.0)
        self.assertGreater(peak, off_peak,
                           "زمن الذروة أطول فعليًا — القيمة ليست ثابتة")
        self.assertAlmostEqual(peak / off_peak, 1.45, places=2)

    @scenario(40)
    def test_riyadh_uses_road_time_not_fixed_gap(self) -> None:
        """§13: الرياض لا تستخدم فاصلًا ثابتًا — الفاصل صفر والزمن من المصفوفة."""
        prob = problem(
            [shipment(1, pickup="PHC1", window=(at(8, 0), at(9, 0)), sla=at(13, 0)),
             shipment(2, pickup="PHC2", window=(at(8, 0), at(9, 0)), sla=at(13, 0))],
            [vehicle(1)], effective=self._riyadh())
        self.assertEqual(prob.settings.min_event_gap_minutes, 0)

        result = plan(prob)
        route = result.solution.used_routes()[0]
        legs = [t.leg_minutes for t in route.evaluation.timings]
        self.assertGreater(len(set(round(v, 3) for v in legs)), 1,
                           "أزمنة المقاطع متباينة حسب المسافة لا ثابتة")

    @scenario(40)
    def test_peak_plan_is_still_feasible_and_slower(self) -> None:
        shipments = [
            shipment(i, pickup=code,
                     window=(at(7, 0) + dt.timedelta(minutes=40 * (i - 1)),
                             at(7, 30) + dt.timedelta(minutes=40 * (i - 1))),
                     sla=at(14, 0))
            for i, code in enumerate(["PHC1", "PHC2", "PHC4"], start=1)
        ]
        calm = plan(problem(shipments, [vehicle(1), vehicle(2)],
                            effective=settings(min_event_gap_minutes=0)))
        busy = plan(problem(shipments, [vehicle(1), vehicle(2)],
                            effective=self._riyadh()))

        self.assertEqual(busy.metrics["unplannable_count"], 0,
                         f"الخطة تبقى منفَّذة في الذروة: {busy.unplannable}")
        self.assertGreater(float(busy.metrics["total_drive_minutes"]),
                           float(calm.metrics["total_drive_minutes"]),
                           "خطة الذروة أبطأ فعليًا — الفرق محسوب لا مُدّعى")


# ============================================================ ٤٣: الضغط =====

class TestLoad(MasarTestCase):

    @scenario(43)
    def test_large_instance_completes_within_time_budget(self) -> None:
        codes = ["PHC1", "PHC2", "PHC3", "PHC4", "PHC5"]
        shipments = [
            shipment(
                i, pickup=codes[i % len(codes)],
                window=(at(6, 45) + dt.timedelta(minutes=6 * (i % 60)),
                        at(7, 15) + dt.timedelta(minutes=6 * (i % 60))),
                sla=at(16, 0),
                facility_suffix=f"-{i}",   # جهات مختلفة كي لا يقيّد HC-11 المسألة
            )
            for i in range(300)
        ]
        vehicles = [vehicle(i, end=at(23)) for i in range(1, 61)]

        started = time.monotonic()
        prob = problem(shipments, vehicles)
        build_seconds = time.monotonic() - started

        started = time.monotonic()
        result = run_engine(prob, options=SolveOptions(time_limit_seconds=20.0, seed=5),
                            compute_reference_plan=False)
        solve_seconds = time.monotonic() - started

        self.assertLess(build_seconds, 60.0,
                        f"بناء مصفوفة {len(prob.nodes)} عقدة استغرق {build_seconds:.1f} ث")
        self.assertLess(solve_seconds, 60.0,
                        f"الحل استغرق {solve_seconds:.1f} ث")
        self.assertGreater(result.metrics["planned_shipment_count"], 250,
                           f"مخطط {result.metrics['planned_shipment_count']} من 300")
        for rejection in result.unplannable:
            self.assertReasoned(rejection, "message_ar")
        # فحص ما بعد الحل يمر على كل رحلة — لو أنتج المحرك رحلة غير شرعية لرُفعت
        for route in result.solution.used_routes():
            self.assertTrue(route.evaluation.feasible)


# ================================================ ٤٤–٤٥: فشل الخدمات ========

class TestFailurePaths(MasarTestCase):

    @scenario(44)
    def test_routing_failure_is_not_hidden(self) -> None:
        prob_nodes = problem([shipment(1, pickup="PHC1")], [vehicle(1)]).nodes
        with self.assertRaises(DependencyUnavailable) as caught:
            build_travel_matrix(prob_nodes, FailingProvider(),
                                fallback_to_estimate=False)
        self.assertIn("خدمة الطرق", caught.exception.message)
        self.assertEqual(caught.exception.http_status, 503,
                         "فشل تبعية خارجية يُترجم إلى 503 لا إلى خطة صامتة")

    @scenario(44)
    def test_routing_fallback_is_declared_estimated(self) -> None:
        prob_nodes = problem([shipment(1, pickup="PHC1")], [vehicle(1)]).nodes
        matrix = build_travel_matrix(prob_nodes, FailingProvider(),
                                     fallback_to_estimate=True)
        self.assertTrue(matrix.is_estimated,
                        "الرجوع للتقدير ليس صامتًا: النتيجة موسومة تقديرية")
        self.assertEqual(matrix.provider, "haversine",
                         "المزوّد المستخدم فعليًا معلن باسمه لا باسم المزوّد الفاشل")

    @scenario(44)
    def test_plan_built_on_estimate_carries_a_warning(self) -> None:
        prob = problem([shipment(1, pickup="PHC1")], [vehicle(1)])
        result = plan(prob)
        self.assertTrue(result.routing_estimated)
        self.assertTrue(
            any(w.warning_type == "ESTIMATED_TRAVEL_TIME" for w in result.warnings),
            f"أنواع التحذيرات: {[w.warning_type for w in result.warnings]}")

    @scenario(45)
    def test_optimizer_backend_failure_surfaces(self) -> None:
        prob = problem([shipment(1, pickup="PHC1")], [vehicle(1)])
        with self.assertRaises(DependencyUnavailable) as caught:
            run_engine(prob, options=FAST, backend_name="ortools")
        self.assertIn("OR-Tools", caught.exception.message,
                      "الخلفية غير المتاحة تُصرّح بذلك بدل أن تدّعي عملًا")

    @scenario(45)
    def test_optimizer_crash_becomes_typed_failure(self) -> None:
        from masar_opt import backends

        class ExplodingBackend:
            name = "exploding"
            version = "test"

            def solve(self, problem, options, baseline=None):
                raise RuntimeError("انهيار مُفتعل داخل الحلّال")

        backends.BACKENDS["exploding"] = ExplodingBackend()
        try:
            prob = problem([shipment(1, pickup="PHC1")], [vehicle(1)])
            with self.assertRaises(OptimizationFailed) as caught:
                run_engine(prob, options=FAST, backend_name="exploding")
            self.assertIn("فشل محرك التحسين", caught.exception.message)
            self.assertEqual(caught.exception.details.get("backend"), "exploding")
        finally:
            backends.BACKENDS.pop("exploding", None)

    @scenario(45)
    def test_illegal_solver_output_is_rejected_post_solve(self) -> None:
        """لو أنتجت خلفية رحلة تخرق قيدًا صلبًا، يوقفها فحص ما بعد الحل."""
        from masar_opt import backends
        from masar_opt.evaluate import RouteEvaluation
        from masar_opt.objective import RoutePlan, Solution

        class CheatingBackend:
            name = "cheating"
            version = "test"

            def solve(self, prob, options, baseline=None):
                pickup, delivery = prob.shipments[0].pickup_node, prob.shipments[0].delivery_node
                solution = Solution(
                    # ترتيب مقلوب: التسليم قبل الالتقاط
                    routes=[RoutePlan(0, [delivery, pickup],
                                      RouteEvaluation(feasible=True))],
                    assignment={0: 0})
                return solution, {}

        backends.BACKENDS["cheating"] = CheatingBackend()
        try:
            prob = problem([shipment(1, pickup="PHC1")], [vehicle(1)])
            with self.assertRaises(OptimizationFailed) as caught:
                run_engine(prob, options=FAST, backend_name="cheating")
            self.assertIn("فحص ما بعد الحل", caught.exception.message)
        finally:
            backends.BACKENDS.pop("cheating", None)


if __name__ == "__main__":
    unittest.main()
