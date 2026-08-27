#!/usr/bin/env bash
# التحقق من البيئة التجريبية بعد تشغيلها — بأدلة مقيسة لا بانطباع.
#
#   ./scripts/staging-test.sh            # كل الفحوص
#   ./scripts/staging-test.sh --quick    # فحوص سريعة بلا الحزمة الكاملة
#
# كل فحص هنا يجيب على سؤال يمكن أن يكون جوابه «لا» ويظل النظام يبدو سليمًا.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
STAGING="$ROOT/deploy/staging"
ENV_FILE="$STAGING/.env.staging"

PASSED=0; FAILED=0
pass() { PASSED=$((PASSED+1)); printf '\033[32m✅ %s\033[0m\n' "$1"; }
fail() { FAILED=$((FAILED+1)); printf '\033[31m❌ %s\033[0m\n' "$1"; }
head_() { printf '\n\033[1m── %s ──\033[0m\n' "$1"; }

[ -f "$ENV_FILE" ] || { echo "❌ $ENV_FILE غير موجود — شغّل ./scripts/staging-up.sh"; exit 1; }

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose --env-file "$ENV_FILE" -f "$STAGING/docker-compose.staging.yml" "$@"
  else
    docker-compose --env-file "$ENV_FILE" -f "$STAGING/docker-compose.staging.yml" "$@"
  fi
}

HTTPS_PORT="$(grep -E '^MASAR_HTTPS_PORT=' "$ENV_FILE" | cut -d= -f2)"
HTTPS_PORT="${HTTPS_PORT:-443}"
BASE="https://localhost:${HTTPS_PORT}"

# ═════════════════════════════════════════════════ ١) صحة الخدمات ═══════
head_ "صحة الخدمات"
for service in db osrm app nginx; do
  state="$(compose ps --format '{{.Service}} {{.Health}}' 2>/dev/null \
           | awk -v s="$service" '$1==s{print $2}')"
  if [ "$state" = "healthy" ]; then pass "$service — healthy"
  else fail "$service — الحالة «${state:-غير معروفة}» (المطلوب healthy)"; fi
done

# ═══════════════════════════════════ ٢) العزل: ما يجب ألا يكون مكشوفًا ══
head_ "عزل الشبكة"
for entry in "5432:قاعدة البيانات" "5000:OSRM" "8080:التطبيق"; do
  port="${entry%%:*}"; label="${entry#*:}"
  if (exec 3<>/dev/tcp/127.0.0.1/"$port") 2>/dev/null; then
    fail "$label مكشوف على المضيف عبر المنفذ $port — يجب ألا يكون"
    exec 3<&- 2>/dev/null
  else
    pass "$label غير مكشوف على المنفذ $port"
  fi
done

# ═════════════════════════════════════════════════════ ٣) HTTPS ════════
head_ "HTTPS والرؤوس الأمنية"
if curl -sk --max-time 15 "$BASE/api/health" | grep -q '"ok":true'; then
  pass "الخدمة تستجيب على HTTPS"
else
  fail "لا استجابة على $BASE/api/health"
fi

redirect="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
            "http://localhost:${MASAR_HTTP_PORT:-80}/" 2>/dev/null)"
[ "$redirect" = "301" ] && pass "HTTP يحوّل إلى HTTPS (301)" \
                        || fail "HTTP لا يحوّل — الرمز $redirect"

headers="$(curl -skI --max-time 10 "$BASE/api/health" 2>/dev/null)"
for header in "strict-transport-security" "x-content-type-options" \
              "x-frame-options" "content-security-policy"; do
  echo "$headers" | grep -qi "^$header" \
    && pass "الرأس $header موجود" || fail "الرأس $header مفقود"
done
echo "$headers" | grep -qi "^server: nginx/" \
  && fail "nginx يعلن إصداره" || pass "لا إعلان عن إصدار الخادم"

# ══════════════════════════════════════ ٤) التشفير ومزوّد الطرق ════════
head_ "التشفير ومزوّد الطرق"
crypto="$(compose exec -T app python3 -c "
from masar_core import crypto
import json; print(json.dumps(crypto.status(), ensure_ascii=False))" 2>/dev/null)"
echo "$crypto" | grep -q '"production_grade": true' \
  && pass "التشفير AES-256-GCM عبر cryptography (معتمد)" \
  || fail "التشفير غير معتمد: $crypto"

routing="$(curl -sk --max-time 20 "$BASE/api/routing/status" 2>/dev/null)"
echo "$routing" | grep -q '"provider": *"osrm"' \
  && pass "مزوّد الطرق osrm لا haversine" || fail "المزوّد ليس osrm: $routing"
echo "$routing" | grep -q '"map_verified": *true' \
  && pass "الخريطة متحقَّقة مقابل سيقان سعودية مقيسة" \
  || fail "الخريطة غير متحقَّقة: $routing"
echo "$routing" | grep -q '"is_estimated": *false' \
  && pass "الأزمنة حقيقية لا تقديرية" || fail "الأزمنة تقديرية"

# ═══════════════════════════════ ٥) مسارات سعودية معروفة ══════════════
head_ "مسارات سعودية معروفة — مقارنة الزمن والمسافة"
compose exec -T app python3 scripts/osrm_verify.py 2>&1 | sed 's/^/    /'
[ "${PIPESTATUS[0]:-1}" -eq 0 ] && pass "كل السيقان المرجعية ضمن التسامح" \
                               || fail "انحراف في السيقان المرجعية"

# ══════════════════════════════════ ٦) فحص الإقلاع في وضع staging ═════
head_ "preflight في وضع staging"
compose exec -T app python3 scripts/preflight.py 2>&1 | tail -12 | sed 's/^/    /'
[ "${PIPESTATUS[0]:-1}" -eq 0 ] && pass "preflight يقبل إعداد staging" \
                               || fail "preflight يرفض الإعداد الحالي"

# ═════════════════════════ ٧) لا بيانات إنتاج في البيئة التجريبية ═════
head_ "بيانات التجربة"
untagged="$(compose exec -T app python3 -c "
import sys; sys.path.insert(0, 'tests')
from support import db_connection
c = db_connection()
print(c.fetch_value('SELECT count(*) FROM shipments WHERE NOT is_test_data') or 0)
c.close()" 2>/dev/null | tr -dc '0-9')"
[ "${untagged:-1}" = "0" ] \
  && pass "كل الشحنات موسومة is_test_data — لا بيانات تشغيل حقيقية" \
  || fail "توجد ${untagged} شحنة غير موسومة — بيئة تجريبية يجب ألا تحوي بيانات حقيقية"

# ═══════════════════════════════════ ٨) النسخ والاستعادة ══════════════
head_ "النسخ الاحتياطي وأرشفة WAL"
archived="$(compose exec -T db psql -U "$(grep -E '^MASAR_DB_MIGRATE_USER=' "$ENV_FILE" | cut -d= -f2)" \
            -d "$(grep -E '^MASAR_DB_NAME=' "$ENV_FILE" | cut -d= -f2)" \
            -tAc "SELECT archived_count FROM pg_stat_archiver" 2>/dev/null | tr -dc '0-9')"
failed="$(compose exec -T db psql -U "$(grep -E '^MASAR_DB_MIGRATE_USER=' "$ENV_FILE" | cut -d= -f2)" \
          -d "$(grep -E '^MASAR_DB_NAME=' "$ENV_FILE" | cut -d= -f2)" \
          -tAc "SELECT failed_count FROM pg_stat_archiver" 2>/dev/null | tr -dc '0-9')"
[ "${failed:-1}" = "0" ] && pass "لا فشل في أرشفة WAL" || fail "فشل أرشفة ${failed} مقطعًا"
[ "${archived:-0}" -gt 0 ] 2>/dev/null \
  && pass "أُرشف ${archived} مقطع WAL — الاستعادة إلى لحظة محددة ممكنة" \
  || printf '\033[33m⚠ لم يُؤرشف مقطع بعد (archive_timeout ٥ دقائق) — أعد الفحص لاحقًا\033[0m\n'

# ══════════════════════════════════ ٩) الحزمة الكاملة (اختيارية) ══════
if [ "${1:-}" != "--quick" ]; then
  head_ "الحزمة الآلية الكاملة داخل الحاوية"
  echo "    تستغرق ~٣ دقائق. للتخطي: $0 --quick"
  compose exec -T app python3 tests/run_tests.py --unit 2>&1 | tail -8 | sed 's/^/    /'
  [ "${PIPESTATUS[0]:-1}" -eq 0 ] && pass "اختبارات الوحدة والمحرك" \
                                  || fail "فشل في اختبارات الوحدة"
fi

# ══════════════════════════════════════════════════════ الخلاصة ══════
printf '\n%s\n' "══════════════════════════════════════════════════════════════"
printf 'نجح %d · فشل %d\n' "$PASSED" "$FAILED"
printf '%s\n' "══════════════════════════════════════════════════════════════"
if [ "$FAILED" -gt 0 ]; then
  echo "❌ البيئة التجريبية ليست جاهزة. عالج ما فشل أعلاه."
  exit 1
fi
echo "✅ البيئة التجريبية جاهزة للاستخدام."
echo
echo "تذكير: OR-Tools **غير مثبَّتة** وليست قيد الاستخدام — محرّك التخطيط"
echo "الفعّال هو native_alns (إدراج بالندم + ALNS). راجع STATUS.md."
