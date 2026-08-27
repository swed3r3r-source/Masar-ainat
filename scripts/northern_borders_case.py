"""حالة الحدود الشمالية: خط أساس محرك الاستدلال مقابل الحل المضبوط.

الغرض مزدوج:
* **الجزء ٣** — بديل أمين عن مقارنة OR-Tools المتعذّرة: نقارن الاستدلال
  بـ**الأمثل المُثبَت** لا بحلّال آخر. هذا سقف أعلى لا أدنى.
* **الجزء ٥** — يطبع جدول السيقان (نقطة البداية، النهاية، المسافة، زمن
  القيادة، الخدمة، الانتظار، الوصول، مصدر البيانات) الذي تُملأ منه لاحقًا
  أعمدة OSRM والخدمة المستقلة.

البيانات مجهولة الهوية بالكامل: منشآت مرجعية عامة وشحنات تركيبية، بلا أي
بيانات مريض أو تشغيل حقيقي.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from masar_opt.evaluate import evaluate_route
from masar_opt.exact import solve_single_vehicle_exact
from masar_opt.model import NodeKind
from masar_opt.objective import objective_vector
from masar_opt.solver import RouteOptimizer, SolveOptions
from tests.support import PLACES, at, hub, problem, shipment, vehicle

# نقاط مرجعية عامة إضافية في منطقة الحدود الشمالية (إحداثيات علنية، بلا أي
# بيانات مريض أو تشغيل حقيقي). العويقيلة تقع على الطريق بين رفحاء وعرعر،
# فتجعل السلسلة واقعية: التقاط بعيد ← التقاط وسيط ← تسليم.
PLACES.setdefault("PHC_UWQ", ("مركز صحي العويقيلة", "HEALTH_CENTER", 30.3333, 42.2500))
PLACES.setdefault("PHC_RFH2", ("مركز صحي رفحاء الشمالي", "HEALTH_CENTER", 29.6350, 43.5060))

PROVIDER = sys.argv[1] if len(sys.argv) > 1 else "haversine"


def build():
    """عرعر مركزًا، رفحاء وطريف التقاطًا بعيدًا، والمختبر تسليمًا."""
    hubs = [hub("HUB_ARR", hub_id="hub_arr", opens=6, closes=23)]
    shipments = [
        # التقاط بعيد: رفحاء (≈٢٧٠ كم من عرعر)
        shipment(1, pickup="PHC_RFH", dropoff="LAB_ARR", hub_id="hub_arr",
                 window=(at(8, 0), at(13, 0)), sla=at(20, 0),
                 pickup_service=15.0, dropoff_service=10.0),
        # التقاط ثانٍ من البلدة نفسها — منشأة مختلفة، نفس صنف الخلط
        shipment(2, pickup="PHC_RFH2", dropoff="LAB_ARR", hub_id="hub_arr",
                 window=(at(8, 0), at(13, 30)), sla=at(20, 0),
                 pickup_service=10.0, dropoff_service=10.0,
                 service_type="URGENT"),
        # التقاط وسيط على الطريق: العويقيلة
        shipment(3, pickup="PHC_UWQ", dropoff="LAB_ARR", hub_id="hub_arr",
                 window=(at(9, 0), at(16, 0)), sla=at(21, 0),
                 pickup_service=15.0, dropoff_service=10.0),
    ]
    vehicles = [vehicle(1, hub_id="hub_arr", start=at(6), end=at(23),
                        shift_minutes=840.0)]
    return problem(shipments, vehicles, hubs=hubs, provider=PROVIDER)


TZ = dt.timezone(dt.timedelta(hours=3))  # توقيت الرياض


def clock(minutes: float) -> str:
    """الدقائق في النموذج دقائق حقبة يونكس — تُحوَّل إلى توقيت الرياض."""
    from masar_opt.model import to_datetime
    return to_datetime(minutes).astimezone(TZ).strftime("%H:%M")


def legs(prob, veh, sequence, evaluation):
    rows = []
    names = [prob.nodes[veh.start_node].label if veh.start_node is not None
             else "بداية المركبة"]
    prev = names[0]
    for timing in evaluation.timings:
        node = prob.nodes[timing.node_index]
        kind = {NodeKind.PICKUP: "التقاط", NodeKind.DELIVERY: "تسليم"}.get(
            node.kind, str(node.kind))
        rows.append({
            "من": prev,
            "إلى": f"{node.label} ({kind})",
            "مسافة_كم": round(timing.leg_km, 1),
            "قيادة_د": round(timing.leg_minutes, 1),
            "خدمة_د": round(timing.service_end - timing.service_start, 1),
            "انتظار_د": round(timing.wait_minutes, 1),
            # قاعدة تشغيلية معلنة: لا حركة قبل منتصف نافذة الالتقاط +
            # post_pickup_departure_minutes. تظهر كعمود مستقل لئلا تبدو فجوة
            # غير مفسَّرة في الجدول.
            "احتجاز_د": round(timing.departure - timing.service_end, 1),
            "وصول": clock(timing.arrival),
            "مغادرة": clock(timing.departure),
            "المصدر": "تقديري (Haversine ×1.35)" if prob.travel.is_estimated
                      else "طرق حقيقية (OSRM/OpenStreetMap — مصفوفة مثبَّتة)",
        })
        prev = node.label
    return rows


def main() -> int:
    prob = build()
    veh = prob.vehicles[0]
    print("=" * 78)
    print(f"حالة الحدود الشمالية — مزوّد الطرق: {PROVIDER} "
          f"· تقديري={prob.travel.is_estimated}")
    print("=" * 78)

    t0 = time.perf_counter()
    optimizer = RouteOptimizer(prob, SolveOptions(seed=7, time_limit_seconds=20.0))
    heuristic = optimizer.solve()
    t_heur = time.perf_counter() - t0

    plan = next(p for p in heuristic.routes if p.sequence)
    heur_eval = evaluate_route(prob, prob.vehicles[plan.vehicle_index],
                               plan.sequence, stop_on_first_violation=False)

    t1 = time.perf_counter()
    exact = solve_single_vehicle_exact(prob, veh, list(range(len(prob.shipments))))
    t_exact = time.perf_counter() - t1

    print(f"\n— الاستدلال (Regret-k + ALNS) — {t_heur*1000:.0f} مل.ث، "
          f"{optimizer.iterations} تكرار")
    print(f"  مسافة {heur_eval.distance_km:.1f} كم · قيادة "
          f"{heur_eval.drive_minutes:.0f} د · انتظار {heur_eval.wait_minutes:.0f} د "
          f"· عدد الرحلات {sum(1 for p in heuristic.routes if p.sequence)} "
          f"· سائقون {sum(1 for p in heuristic.routes if p.sequence)}")
    print(f"  المدقّق المستقل evaluate_route → صالح={heur_eval.feasible} "
          f"· خروقات={len(heur_eval.violations)}")

    if exact is None:
        print("\n— الحل المضبوط: تعذّر (تجاوز حدّ العقد)")
        return 1
    print(f"\n— الحل المضبوط (تفريع وتحديد) — {t_exact*1000:.0f} مل.ث، "
          f"{exact.nodes_explored} عقدة مستكشَفة · أمثل مُثبَت={exact.proven_optimal}")
    print(f"  مسافة {exact.evaluation.distance_km:.1f} كم · قيادة "
          f"{exact.evaluation.drive_minutes:.0f} د · انتظار "
          f"{exact.evaluation.wait_minutes:.0f} د")
    ex_eval = evaluate_route(prob, veh, exact.sequence, stop_on_first_violation=False)
    print(f"  المدقّق المستقل evaluate_route → صالح={ex_eval.feasible} "
          f"· خروقات={len(ex_eval.violations)}")

    same = plan.sequence == exact.sequence
    gap = (heur_eval.distance_km - exact.evaluation.distance_km)
    print(f"\n— المقارنة: تسلسل مطابق={same} · فارق المسافة={gap:+.2f} كم "
          f"({(gap / exact.evaluation.distance_km * 100) if exact.evaluation.distance_km else 0:+.2f}٪)")

    print("\n— جدول السيقان (الحل المعتمد)")
    rows = legs(prob, prob.vehicles[plan.vehicle_index], plan.sequence, heur_eval)
    head = ["من", "إلى", "مسافة_كم", "قيادة_د", "خدمة_د", "انتظار_د", "احتجاز_د",
            "وصول", "مغادرة", "المصدر"]
    print(" | ".join(head))
    for row in rows:
        print(" | ".join(str(row[key]) for key in head))
    print(f"\nالإجمالي: {heur_eval.distance_km:.1f} كم · قيادة "
          f"{heur_eval.drive_minutes:.0f} د · انتهاء الرحلة {clock(heur_eval.end_at)} "
          f"(آخر تسليم — بلا عودة إجبارية)")

    out = ROOT / "var" / "reports" / f"northern-borders-{PROVIDER}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "provider": PROVIDER,
        "is_estimated": prob.travel.is_estimated,
        "heuristic": {"distance_km": round(heur_eval.distance_km, 2),
                      "drive_minutes": round(heur_eval.drive_minutes, 1),
                      "wait_minutes": round(heur_eval.wait_minutes, 1),
                      "routes": sum(1 for p in heuristic.routes if p.sequence),
                      "solve_ms": round(t_heur * 1000),
                      "feasible": heur_eval.feasible},
        "exact": {"distance_km": round(exact.evaluation.distance_km, 2),
                  "drive_minutes": round(exact.evaluation.drive_minutes, 1),
                  "wait_minutes": round(exact.evaluation.wait_minutes, 1),
                  "proven_optimal": exact.proven_optimal,
                  "nodes_explored": exact.nodes_explored,
                  "solve_ms": round(t_exact * 1000),
                  "feasible": ex_eval.feasible},
        "identical_sequence": same,
        "legs": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nمخرج JSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
