#!/usr/bin/env bash
# الحزمة الكاملة لاختبارات §30: قاعدة نظيفة ← بيانات موسومة ← خادم ← ٤٦ سيناريو.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env.dev; set +a
mkdir -p var/logs

# كلمة مرور حسابات الاختبار تُولَّد لكل تشغيل ولا تُكتب في أي ملف — القاعدة
# تُزرع بها والاختبارات تستقبلها عبر البيئة. لا قيمة افتراضية في المستودع.
export MASAR_SEED_PASSWORD="${MASAR_SEED_PASSWORD:-$(python3 -c "
import secrets, string
alphabet = string.ascii_letters + string.digits + '!@#\$%^&*-_=+'
print(''.join(secrets.choice(alphabet) for _ in range(20)))")}"
export MASAR_TEST_PASSWORD="$MASAR_SEED_PASSWORD"

./scripts/pg.sh start
echo "── إعادة بناء قاعدة البيانات وزرع بيانات الاختبار الموسومة ──"
PYTHONPATH=packages python3 scripts/seed.py --reset > var/logs/seed-tests.log 2>&1
tail -2 var/logs/seed-tests.log

# تدوير مقطع WAL قبل الاختبارات: المؤرشف غير متزامن، وبلا هذا
# قد يبدأ اختبار الأرشفة قبل أرشفة أول مقطع فيفشل بلا سبب حقيقي.
./scripts/pg.sh switch-wal > /dev/null 2>&1 || true

./scripts/serve.sh restart > /dev/null
echo "── تشغيل الاختبارات ──"
PYTHONPATH=packages python3 tests/run_tests.py "$@"
