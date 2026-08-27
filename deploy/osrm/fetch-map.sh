#!/usr/bin/env bash
# تنزيل خريطة السعودية من Geofabrik والتحقق من سلامتها.
#
# **ثبّت التاريخ ولا تعتمد على latest دائمًا**: تغيّر الخريطة يغيّر المسافات،
# ومقارنة خطة اليوم بخطة الأمس تصبح بلا معنى إن تغيّر الأساس صامتًا.
# سجّل md5 وتاريخ الملف مع كل إصدار خطة.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data && cd data

BASE="https://download.geofabrik.de/asia"
FILE="saudi-arabia-latest.osm.pbf"

curl -fL# -o "$FILE"     "$BASE/$FILE"
curl -fL# -o "$FILE.md5" "$BASE/$FILE.md5"
md5sum -c "$FILE.md5"

echo "✅ الخريطة جاهزة: $(du -h "$FILE" | cut -f1) · $(date -r "$FILE" +%F)"
echo "سجّل هذه البصمة مع خططك: $(md5sum "$FILE" | cut -d' ' -f1)"
