"""يحوّل استجابة OSRM الخام إلى ملف مصفوفة يقرأه ``MatrixFileProvider``.

المصفوفة **بيانات طرق حقيقية** من OSRM على بيانات OpenStreetMap، جُلبت خارج
نطاق الخادم (حاوية التطبيق بلا نفاذ شبكي) وثُبِّتت في ملف. هذا ليس تكاملًا
حيًّا: التكامل الحيّ يقتضي تشغيل OSRM ذاتي الاستضافة كما في deploy/osrm/.
تثبيت المصفوفة يجعل الخطة **قابلة لإعادة الإنتاج بالضبط**، وهو استخدام
مشروع ومعلن لا محاكاة لنجاح غير متحقق.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from masar_opt.routing import MatrixFileProvider
from scripts.northern_borders_case import build

RAW = ROOT / "var" / "reports" / "osrm-raw-northern-borders.json"
OUT = ROOT / "var" / "reports" / "osrm-matrix-northern-borders.json"


def main() -> int:
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    points = [(lat, lon) for lon, lat in raw["_coordinates_lon_lat"]]

    prob = build()  # المسألة نفسها ببناء haversine — نأخذ منها العقد فقط
    keys: list[str] = []
    mapping: list[int] = []          # فهرس نقطة OSRM لكل مفتاح
    for node in prob.nodes:
        key = MatrixFileProvider._node_key(node)
        if key in keys:
            continue
        best = min(range(len(points)),
                   key=lambda i: abs(points[i][0] - node.lat) + abs(points[i][1] - node.lon))
        delta = abs(points[best][0] - node.lat) + abs(points[best][1] - node.lon)
        if delta > 1e-4:
            raise SystemExit(f"عقدة بلا نقطة OSRM مطابقة: {node.label} ({node.lat},{node.lon})")
        keys.append(key)
        mapping.append(best)

    size = len(keys)
    minutes = [[0.0] * size for _ in range(size)]
    km = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            if i == j:
                continue
            minutes[i][j] = round(raw["durations"][mapping[i]][mapping[j]] / 60.0, 3)
            km[i][j] = round(raw["distances"][mapping[i]][mapping[j]] / 1000.0, 3)

    OUT.write_text(json.dumps({
        "_source": raw["_source"],
        "_note_ar": "بيانات طرق حقيقية (OSM/OSRM) مثبَّتة في ملف — ليست تكاملًا حيًّا",
        "keys": keys, "minutes": minutes, "km": km,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"مفاتيح: {keys}")
    print(f"كُتب: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
