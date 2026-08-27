"""مشغّل الاختبارات: ينفّذ الحزم الثلاث ويبني **مصفوفة تغطية** السيناريوهات.

المخرج ليس «نجح/فشل» فقط: لكل سيناريو من §30 يُظهر أي اختبار غطاه وبأي نتيجة،
ويُظهر السيناريوهات غير المُغطاة وغير المنفَّذة صراحةً. سيناريو بلا اختبار لا
يمر بصمت.

التشغيل::

    ./scripts/run_tests.sh              # الحزمة الكاملة (يُهيّئ القاعدة والخادم)
    python3 tests/run_tests.py --unit   # الوحدة والمحرك فقط (بلا قاعدة بيانات)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from tests.support import MANDATORY_SCENARIOS, TEST_SCENARIOS  # noqa: E402

UNIT_MODULES = ("tests.test_unit_rules", "tests.test_engine_scenarios",
                "tests.test_export_scan")
INTEGRATION_MODULES = ("tests.test_api_scenarios", "tests.test_security")

PASS, FAIL, SKIP = "✅", "❌", "⏭️"


class RecordingResult(unittest.TextTestResult):
    """يسجّل نتيجة كل اختبار بالاسم كي تُبنى مصفوفة التغطية."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.outcomes: dict[str, dict] = {}
        self._started: dict[str, float] = {}

    def startTest(self, test) -> None:  # noqa: N802
        self._started[test.id()] = time.monotonic()
        super().startTest(test)

    def _record(self, test, status: str, detail: str = "") -> None:
        self.outcomes[test.id()] = {
            "status": status,
            "detail": detail,
            "seconds": round(time.monotonic() - self._started.get(test.id(), 0.0), 3),
            "doc": (test.shortDescription() or ""),
        }

    def addSuccess(self, test) -> None:  # noqa: N802
        super().addSuccess(test)
        self._record(test, "PASS")

    def addFailure(self, test, err) -> None:  # noqa: N802
        super().addFailure(test, err)
        self._record(test, "FAIL", self._exc_message(err))

    def addError(self, test, err) -> None:  # noqa: N802
        super().addError(test, err)
        self._record(test, "ERROR", self._exc_message(err))

    def addSkip(self, test, reason) -> None:  # noqa: N802
        super().addSkip(test, reason)
        self._record(test, "SKIP", reason)

    @staticmethod
    def _exc_message(err) -> str:
        _kind, value, _tb = err
        return str(value).strip().splitlines()[0][:400] if str(value).strip() else repr(value)


def run_modules(modules: tuple[str, ...], *, verbosity: int) -> RecordingResult:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in modules:
        try:
            suite.addTests(loader.loadTestsFromName(name))
        except Exception as exc:  # pragma: no cover
            print(f"{FAIL} تعذّر تحميل {name}: {exc}")
            raise
    runner = unittest.TextTestRunner(
        verbosity=verbosity, resultclass=RecordingResult, stream=sys.stdout)
    return runner.run(suite)


def build_matrix(outcomes: dict[str, dict]) -> dict[int, dict]:
    """يربط كل سيناريو إلزامي باختباراته ونتيجتها."""
    matrix: dict[int, dict] = {
        number: {"title": title, "tests": [], "status": "UNCOVERED"}
        for number, title in MANDATORY_SCENARIOS.items()
    }
    for test_id, numbers in TEST_SCENARIOS.items():
        outcome = outcomes.get(test_id)
        if outcome is None:
            continue
        for number in numbers:
            entry = matrix[number]
            entry["tests"].append({"test": test_id, **outcome})

    for entry in matrix.values():
        statuses = {item["status"] for item in entry["tests"]}
        if not statuses:
            entry["status"] = "UNCOVERED"
        elif statuses & {"FAIL", "ERROR"}:
            entry["status"] = "FAIL"
        elif statuses == {"SKIP"}:
            entry["status"] = "SKIP"
        else:
            entry["status"] = "PASS"
    return matrix


def print_matrix(matrix: dict[int, dict]) -> None:
    print("\n" + "═" * 78)
    print("مصفوفة تغطية السيناريوهات الإلزامية (§30)")
    print("═" * 78)
    marks = {"PASS": PASS, "FAIL": FAIL, "SKIP": SKIP, "UNCOVERED": "⚠️ "}
    for number in sorted(matrix):
        entry = matrix[number]
        mark = marks[entry["status"]]
        count = len(entry["tests"])
        line = f"{mark} {number:>2}. {entry['title']}"
        if entry["status"] == "UNCOVERED":
            line += "  ← لا يوجد اختبار يغطيه"
        else:
            line += f"  ({count} اختبار)"
        print(line)
        for item in entry["tests"]:
            if item["status"] in ("FAIL", "ERROR"):
                print(f"      {FAIL} {item['test'].rsplit('.', 1)[-1]}: {item['detail']}")
            elif item["status"] == "SKIP":
                print(f"      {SKIP} {item['test'].rsplit('.', 1)[-1]}: {item['detail']}")


def summarize(matrix: dict[int, dict], outcomes: dict[str, dict],
              seconds: float) -> dict:
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0, "UNCOVERED": 0}
    for entry in matrix.values():
        counts[entry["status"]] += 1
    test_counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "SKIP": 0}
    for outcome in outcomes.values():
        test_counts[outcome["status"]] = test_counts.get(outcome["status"], 0) + 1

    print("\n" + "═" * 78)
    print("الخلاصة")
    print("═" * 78)
    print(f"الاختبارات : {sum(test_counts.values())} "
          f"· ناجحة {test_counts['PASS']} "
          f"· فاشلة {test_counts['FAIL'] + test_counts['ERROR']} "
          f"· متخطّاة {test_counts['SKIP']}")
    print(f"السيناريوهات: {len(matrix)} "
          f"· مثبتة {counts['PASS']} "
          f"· فاشلة {counts['FAIL']} "
          f"· غير منفَّذة {counts['SKIP']} "
          f"· غير مُغطاة {counts['UNCOVERED']}")
    print(f"الزمن الكلي : {seconds:.1f} ثانية")

    ok = counts["FAIL"] == 0 and counts["UNCOVERED"] == 0 and test_counts["FAIL"] == 0 \
        and test_counts["ERROR"] == 0
    print(("\n" + PASS + " كل السيناريوهات الإلزامية مُغطاة وناجحة")
          if ok and counts["SKIP"] == 0 else
          ("\n" + PASS + " لا فشل — مع سيناريوهات غير منفَّذة (انظر أعلاه)") if ok else
          ("\n" + FAIL + " توجد سيناريوهات فاشلة أو غير مُغطاة"))
    return {"scenarios": counts, "tests": test_counts, "ok": ok, "seconds": seconds}


def print_exceptions(outcomes: dict[str, dict]) -> None:
    """يسمّي كل اختبار لم ينجح — بالاسم والسبب.

    الاختبارات الأمنية بلا وسم ``@scenario`` فلا تظهر في المصفوفة؛ تخطّيها
    كان يمر بلا اسم. هذه الكتلة تمنع ذلك: أي تخطٍّ أو فشل يُذكر صراحةً.
    """
    odd = {test_id: outcome for test_id, outcome in outcomes.items()
           if outcome["status"] != "PASS"}
    if not odd:
        return
    print("\n" + "═" * 78)
    print("اختبارات لم تنجح — بالاسم والسبب")
    print("═" * 78)
    marks = {"SKIP": SKIP, "FAIL": FAIL, "ERROR": FAIL}
    for test_id, outcome in sorted(odd.items()):
        tagged = TEST_SCENARIOS.get(test_id)
        origin = f"سيناريو {', '.join(str(n) for n in tagged)}" if tagged \
            else "غير موسوم بسيناريو (فحص أمني/بنية تحتية)"
        print(f"{marks.get(outcome['status'], '?')} {test_id}")
        print(f"      التصنيف: {origin}")
        print(f"      السبب  : {outcome['detail'] or '—'}")


def write_report(matrix: dict[int, dict], summary: dict, path: Path,
                 outcomes_ref: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "summary": summary,
        "not_passed": {
            test_id: {
                **outcome,
                "scenarios": TEST_SCENARIOS.get(test_id, []),
            }
            for test_id, outcome in sorted(outcomes_ref.items())
            if outcome["status"] != "PASS"
        },
        "scenarios": {
            str(number): {
                "title": entry["title"],
                "status": entry["status"],
                "tests": entry["tests"],
            }
            for number, entry in sorted(matrix.items())
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nتقرير مفصّل: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="مشغّل اختبارات مسار عينات")
    parser.add_argument("--unit", action="store_true",
                        help="الوحدة والمحرك فقط — بلا قاعدة بيانات أو خادم")
    parser.add_argument("--integration", action="store_true",
                        help="الاختبارات التكاملية فقط")
    parser.add_argument("--security", action="store_true",
                        help="الفحوص الأمنية فقط")
    parser.add_argument("--verbosity", type=int, default=2)
    parser.add_argument("--report", default="var/test-report.json")
    args = parser.parse_args()

    modules: tuple[str, ...] = ()
    if args.unit:
        modules = UNIT_MODULES
    elif args.security:
        modules = ("tests.test_security",)
    elif args.integration:
        modules = INTEGRATION_MODULES
    else:
        modules = UNIT_MODULES + INTEGRATION_MODULES

    started = time.monotonic()
    result = run_modules(modules, verbosity=args.verbosity)
    seconds = time.monotonic() - started

    matrix = build_matrix(result.outcomes)
    if not args.unit and not args.integration and not args.security:
        print_matrix(matrix)
        print_exceptions(result.outcomes)
        summary = summarize(matrix, result.outcomes, seconds)
        write_report(matrix, summary, ROOT / args.report, result.outcomes)
        return 0 if summary["ok"] else 1

    print_exceptions(result.outcomes)

    # تشغيل جزئي: لا تُدّعى التغطية الكاملة
    print(f"\nتشغيل جزئي ({', '.join(modules)}) — مصفوفة التغطية تُبنى في "
          "التشغيل الكامل فقط.")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
