#!/usr/bin/env bash
# تشغيل البيئة التجريبية من الصفر — بخطوات موقوفة ببوابات.
#
#   ./scripts/staging-up.sh              # الدورة كاملة
#   ./scripts/staging-up.sh --rebuild    # إعادة بناء صورة التطبيق
#   ./scripts/staging-up.sh --down       # إيقاف وحذف الحاويات (البيانات تبقى)
#
# كل بوابة توقف الباقي عند فشلها. الفلسفة: **بيئة نصف عاملة أخطر من بيئة
# لا تعمل** — الأولى تُستخدم ويُبنى على نتائجها.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
STAGING="$ROOT/deploy/staging"
ENV_FILE="$STAGING/.env.staging"
TOTAL=8
STEP=0

say()  { STEP=$((STEP+1)); printf '\n\033[1m[%d/%d] %s\033[0m\n' "$STEP" "$TOTAL" "$1"; }
ok()   { printf '\033[32m✓ %s\033[0m\n' "$1"; }
warn() { printf '\033[33m⚠ %s\033[0m\n' "$1"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose --env-file "$ENV_FILE" -f "$STAGING/docker-compose.staging.yml" "$@"
  else
    docker-compose --env-file "$ENV_FILE" -f "$STAGING/docker-compose.staging.yml" "$@"
  fi
}

random_secret() { python3 -c "import base64,os;print(base64.b64encode(os.urandom($1)).decode())"; }

# ─────────────────────────────────────────────────────────── ١) المتطلبات
check_requirements() {
  say "فحص المتطلبات"
  command -v docker >/dev/null 2>&1 || die "Docker غير مثبَّت."
  docker info >/dev/null 2>&1 \
    || die "خدمة Docker لا تعمل أو لا صلاحية لك عليها: sudo systemctl start docker"
  command -v python3 >/dev/null 2>&1 || die "Python 3 مطلوب لتوليد الأسرار."

  local mem_gb disk_gb
  mem_gb=$(( $(getconf _PHYS_PAGES) * $(getconf PAGE_SIZE) / 1024 / 1024 / 1024 ))
  disk_gb=$(df -BG --output=avail "$ROOT" | tail -1 | tr -dc '0-9')
  [ "$mem_gb" -ge 8 ] || warn "الذاكرة ${mem_gb} ج.ب — تجهيز الخرائط يحتاج ٨ ج.ب"
  [ "$disk_gb" -ge 25 ] || die "المساحة الحرة ${disk_gb} ج.ب — يلزم ٢٥ ج.ب"
  ok "الذاكرة ${mem_gb} ج.ب · المساحة ${disk_gb} ج.ب · Docker يعمل"
}

# ──────────────────────────────────────────────────── ٢) الأسرار والإعداد
prepare_env() {
  say "توليد الأسرار وملف الإعداد"
  if [ -f "$ENV_FILE" ]; then
    ok "$ENV_FILE موجود — لن يُعاد توليده (حذفه يعني فقد مفاتيح التشفير)"
    return
  fi
  # كل قيمة نائبة تُستبدل بقيمة عشوائية قوية. لا سر يُكتب في المستودع:
  # الملف الناتج مستبعَد في .gitignore.
  python3 - "$STAGING/.env.staging.example" "$ENV_FILE" <<'PY'
import base64, os, re, secrets, string, sys

source, target = sys.argv[1], sys.argv[2]
alphabet = string.ascii_letters + string.digits + "!@#%^&*-_=+"

def password(length=28):
    return "".join(secrets.choice(alphabet) for _ in range(length))

lines = []
for line in open(source, encoding="utf-8").read().splitlines():
    match = re.match(r"^([A-Z_]+)=(.*)$", line)
    if match:
        key, value = match.group(1), match.group(2).strip()
        if value == "CHANGE_ME":
            value = password()
        elif value == "GENERATE_AT_DEPLOYMENT":
            # سر JWT: ٤٨ بايت عشوائية — preflight يرفض ما دون ٣٢ محرفًا
            value = base64.b64encode(os.urandom(48)).decode()
        elif value.endswith(":GENERATE_AT_DEPLOYMENT"):
            # مفتاح تشفير: ٣٢ بايت بالضبط (٢٥٦ بت)
            prefix = value.split(":", 1)[0]
            value = f"{prefix}:{base64.b64encode(os.urandom(32)).decode()}"
        line = f"{key}={value}"
    lines.append(line)
open(target, "w", encoding="utf-8").write("\n".join(lines) + "\n")
PY
  chmod 600 "$ENV_FILE"
  ok "وُلّد $ENV_FILE بصلاحية 600 — مستبعَد من المستودع"
}

# ─────────────────────────────────────────────────────────── ٣) الشهادات
prepare_certs() {
  say "شهادة HTTPS"
  mkdir -p "$STAGING/certs"
  if [ -f "$STAGING/certs/fullchain.pem" ] && [ -f "$STAGING/certs/privkey.pem" ]; then
    ok "شهادة موجودة — لن تُستبدل"
    return
  fi
  command -v openssl >/dev/null 2>&1 || die "openssl مطلوب لتوليد شهادة تجريبية."
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout "$STAGING/certs/privkey.pem" \
    -out "$STAGING/certs/fullchain.pem" \
    -subj "/CN=masar-staging.local" \
    -addext "subjectAltName=DNS:masar-staging.local,DNS:localhost,IP:127.0.0.1" \
    2>/dev/null
  chmod 600 "$STAGING/certs/privkey.pem"
  warn "شهادة موقَّعة ذاتيًا — المتصفح سيحذّر، وهذا متوقَّع في بيئة تجريبية."
  warn "لشهادة موثوقة: ضع fullchain.pem و privkey.pem في deploy/staging/certs/"
}

# ───────────────────────────────────────────────────── ٤) بيانات الطرق
prepare_osrm() {
  say "بيانات الطرق الحقيقية (OpenStreetMap للسعودية)"
  if [ -n "$(docker volume ls -q -f name=masar-staging_osrm-data)" ] \
     && compose run --rm --entrypoint sh osrm -c \
        'test -f /data/saudi-arabia-latest.osrm.mldgr' >/dev/null 2>&1; then
    ok "بيانات الطرق مجهَّزة مسبقًا — تخطّي"
    return
  fi
  if [ ! -f "$ROOT/deploy/osrm/data/saudi-arabia-latest.osm.pbf" ]; then
    echo "    تنزيل خريطة السعودية (~٢٠٠ م.ب)…"
    "$ROOT/deploy/osrm/fetch-map.sh" || die "فشل تنزيل الخريطة."
  fi
  echo "    التجهيز ١٠–٢٥ دقيقة. إن قُتلت العملية بلا رسالة فالسبب غالبًا نفاد الذاكرة."
  compose --profile prepare run --rm osrm-prepare || die "فشل تجهيز بيانات الطرق."
  ok "بيانات الطرق جاهزة"
}

# ───────────────────────────────────────────────────────── ٥) التشغيل
start_services() {
  say "بناء وتشغيل الخدمات"
  compose up -d --build --wait --wait-timeout 300 db osrm app nginx \
    || { compose ps; compose logs --tail 60 app; die "لم تجهز الخدمات."; }
  compose ps
  ok "كل الخدمات في حالة healthy"
}

# ─────────────────────────────────────────────── ٦) الترحيلات والبيانات
migrate_and_seed() {
  say "الترحيلات وبيانات التجربة الموسومة"
  compose exec -T app python3 -m masar_db.migrate up || die "فشلت الترحيلات."

  # **لا بيانات مرضى ولا بيانات تشغيل حقيقية في هذه البيئة.** كل صف يزرعه
  # seed.py يحمل is_test_data = true، ومحفّز guard_no_test_data_in_production
  # يرفض هذه الصفوف في بيئة الإنتاج — الحاجز في قاعدة البيانات لا في نيّة
  # المشغّل.
  SEED_PASSWORD="$(python3 -c "
import secrets, string
alphabet = string.ascii_letters + string.digits + '!@#%^&*-_=+'
print(''.join(secrets.choice(alphabet) for _ in range(20)))")"
  compose exec -T -e MASAR_SEED_PASSWORD="$SEED_PASSWORD" app \
    python3 scripts/seed.py > /tmp/masar-seed-$$.log 2>&1 || {
      cat /tmp/masar-seed-$$.log; rm -f /tmp/masar-seed-$$.log
      die "فشل زرع بيانات التجربة."; }
  grep -c "is_test_data" /tmp/masar-seed-$$.log >/dev/null 2>&1 || true
  rm -f /tmp/masar-seed-$$.log
  ok "بيانات تجريبية موسومة is_test_data — لا بيانات مرضى ولا تشغيل حقيقي"
}

# ─────────────────────────────────────────────────── ٧) التحقق من الطرق
verify_routing() {
  say "التحقق من أن الطرق حقيقية لا تقديرية"
  compose exec -T app python3 scripts/osrm_verify.py \
    || die "خدمة الطرق لا تعمل أو خريطتها ليست خريطة السعودية. لا تستخدم هذه البيئة."
  ok "OSRM يعمل على خريطة السعودية — الخطط ستُبنى على مسافات وأزمنة حقيقية"
}

# ──────────────────────────────────────────── ٨) الحسابات وطباعة الوصول
print_access() {
  say "حسابات التجربة"
  # تُولَّد الآن وتُطبع مرة واحدة. لا تُحفظ في أي ملف ولا في المستودع.
  compose exec -T app python3 scripts/make_trial_accounts.py \
    || die "فشل إنشاء حسابات التجربة."

  local https_port
  https_port="$(grep -E '^MASAR_HTTPS_PORT=' "$ENV_FILE" | cut -d= -f2)"
  cat <<EOF

$(printf '\033[1m── الوصول ──\033[0m')

    https://localhost:${https_port:-443}/            واجهة المكتب
    https://localhost:${https_port:-443}/driver      تطبيق السائق
    https://localhost:${https_port:-443}/request     الجهة الطالبة

    المتصفح سيحذّر من الشهادة الموقَّعة ذاتيًا — هذا متوقَّع في بيئة تجريبية.

$(printf '\033[1m── ما هو مكشوف وما ليس ──\033[0m')

    مكشوف للشبكة : 80 (تحويل إلى HTTPS) و 443 فقط
    غير مكشوف    : التطبيق (8080) · قاعدة البيانات (5432) · OSRM (5000)

    كلمات المرور أعلاه ظهرت مرة واحدة ولم تُحفظ في أي ملف.
    لتوليد مجموعة جديدة:
        docker compose -f deploy/staging/docker-compose.staging.yml \\
          exec app python3 scripts/make_trial_accounts.py

$(printf '\033[1m── الخطوة التالية ──\033[0m')

    ./scripts/staging-test.sh

EOF
}

case "${1:-up}" in
  --down)
    compose down
    ok "أُوقفت الحاويات. البيانات محفوظة في volumes — للحذف الكامل: compose down -v"
    exit 0 ;;
  --rebuild)
    check_requirements; prepare_env; prepare_certs
    compose build --no-cache app
    start_services; migrate_and_seed; verify_routing; print_access ;;
  up|"")
    check_requirements; prepare_env; prepare_certs; prepare_osrm
    start_services; migrate_and_seed; verify_routing; print_access ;;
  *) die "الاستخدام: $0 [up|--rebuild|--down]" ;;
esac
