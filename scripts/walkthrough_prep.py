"""تهيئة بيئة نظيفة قبل الدورة عبر المتصفح.

يوسّع أوقات عمل مركز الرياض فقط — وهي **بيانات رئيسية قابلة للتعديل** لا
قيمة مثبّتة في الكود (§13). التوسيع ضروري لأن التجربة تُشغَّل خارج ساعات
العمل الافتراضية؛ المحرك يظل يفحص كل رحلة مقابل النافذة المُعلنة.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from tests.support import db_connection

WIDE = {day: ["00:00", "23:59"]
        for day in ("sat", "sun", "mon", "tue", "wed", "thu", "fri")}


def main() -> int:
    conn = db_connection()
    try:
        with conn.transaction():
            conn.execute(
                "UPDATE hubs SET working_hours = $1::jsonb WHERE code = 'H-RYD-1'",
                [json.dumps(WIDE)])
        print("✓ وُسِّعت أوقات عمل مركز الرياض للتجربة (بيانات رئيسية، لا كود)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
