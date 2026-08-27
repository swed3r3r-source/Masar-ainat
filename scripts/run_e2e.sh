#!/usr/bin/env bash
# دورة اختبار كاملة من الصفر: قاعدة نظيفة ← بيانات ← ملف جدول ← خادم ← اختبار
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env.dev; set +a
mkdir -p var/logs

# كلمة مرور الحسابات تُولَّد لكل تشغيل ولا تُكتب في ملف — القاعدة تُزرع بها
# وسكربت الدورة يستقبلها عبر البيئة.
export MASAR_SEED_PASSWORD="${MASAR_SEED_PASSWORD:-$(python3 -c "
import secrets, string
alphabet = string.ascii_letters + string.digits + '!@#\$%^&*-_=+'
print(''.join(secrets.choice(alphabet) for _ in range(20)))")}"
export MASAR_TEST_PASSWORD="$MASAR_SEED_PASSWORD"

./scripts/pg.sh start
echo "── إعادة بناء قاعدة البيانات وزرع البيانات التجريبية ──"
PYTHONPATH=packages python3 scripts/seed.py --reset > var/logs/seed.log 2>&1
tail -3 var/logs/seed.log
echo "── توليد ملف الجدول الأسبوعي ──"
PYTHONPATH=packages python3 scripts/make_sample_schedule.py 5 var/sample-schedule.csv
./scripts/serve.sh restart > /dev/null
echo "── تشغيل اختبار الدورة الكاملة ──"
PYTHONPATH=packages python3 scripts/e2e_smoke.py
