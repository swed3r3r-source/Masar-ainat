#!/usr/bin/env bash
# تفعيل OSRM ذاتي الاستضافة من البداية إلى التحقق — بخطوات موقوفة ببوابات.
#
#   ./deploy/osrm/activate.sh              # الدورة كاملة
#   ./deploy/osrm/activate.sh verify       # التحقق وحده (خدمة تعمل مسبقًا)
#   ./deploy/osrm/activate.sh prepare      # تجهيز البيانات وحده
#
# كل خطوة تتوقف عند فشلها ولا تكمل. الفلسفة: **خدمة تعمل على خريطة خاطئة
# أسوأ من خدمة لا تعمل** — لأن الأولى تنتج خططًا تبدو سليمة.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd ../.. && pwd)"

PORT="${OSRM_PORT:-5000}"
URL="http://127.0.0.1:${PORT}"
STEP=0

say()  { STEP=$((STEP+1)); printf '\n\033[1m[%d/%d] %s\033[0m\n' "$STEP" "$TOTAL" "$1"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }
ok()   { printf '\033[32m✓ %s\033[0m\n' "$1"; }

need() {
  command -v "$1" >/dev/null 2>&1 || die "$1 غير مثبَّت. $2"
}

compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@"
  else docker-compose "$@"; fi
}

# --------------------------------------------------------- المتطلبات ------
check_requirements() {
  say "فحص المتطلبات"
  need docker "ثبّت Docker Engine ثم أعد المحاولة."
  docker info >/dev/null 2>&1 \
    || die "خدمة Docker لا تعمل أو لا صلاحية لك عليها. جرّب: sudo systemctl start docker"
  need python3 "يلزم Python 3.11."

  local mem_gb disk_gb
  mem_gb=$(( $(getconf _PHYS_PAGES) * $(getconf PAGE_SIZE) / 1024 / 1024 / 1024 ))
  disk_gb=$(df -BG --output=avail . | tail -1 | tr -dc '0-9')
  [ "$mem_gb" -ge 8 ] || printf '\033[33m⚠ الذاكرة %s ج.ب — التجهيز يحتاج ٨ ج.ب وقد يُقتل بـOOM\033[0m\n' "$mem_gb"
  [ "$disk_gb" -ge 20 ] || die "المساحة الحرة ${disk_gb} ج.ب — يلزم ٢٠ ج.ب على الأقل."
  ok "الذاكرة ${mem_gb} ج.ب · المساحة الحرة ${disk_gb} ج.ب"
}

# ------------------------------------------------------------ الخريطة ----
fetch_map() {
  say "تنزيل خريطة السعودية والتحقق من بصمتها"
  if [ -f data/saudi-arabia-latest.osm.pbf ]; then
    ok "الخريطة موجودة — $(du -h data/saudi-arabia-latest.osm.pbf | cut -f1)، تخطّي التنزيل"
    ok "بصمتها: $(md5sum data/saudi-arabia-latest.osm.pbf | cut -d' ' -f1)"
  else
    ./fetch-map.sh
  fi
}

prepare() {
  say "تجهيز بيانات التوجيه (extract → partition → customize)"
  echo "    قد يستغرق ١٠–٢٥ دقيقة. إن قُتلت العملية بلا رسالة فالسبب غالبًا نفاد الذاكرة."
  compose --profile prepare up --abort-on-container-exit
  [ -f data/saudi-arabia-latest.osrm.mldgr ] \
    || die "التجهيز لم ينتج ملفات MLD. راجع مخرجات osrm-extract أعلاه."
  ok "التجهيز اكتمل"
}

serve() {
  say "تشغيل خدمة OSRM"
  compose up -d osrm
  printf '    بانتظار جاهزية الخدمة'
  for _ in $(seq 1 60); do
    if curl -fsS "${URL}/route/v1/driving/46.6,24.7;46.7,24.8?overview=false" \
        2>/dev/null | grep -q '"code":"Ok"'; then
      printf '\n'; ok "الخدمة تستجيب على ${URL}"; return 0
    fi
    printf '.'; sleep 2
  done
  printf '\n'
  compose logs --tail 40 osrm
  die "الخدمة لم تجهز خلال دقيقتين — راجع السجل أعلاه."
}

# ------------------------------------------------------------ التحقق -----
verify() {
  say "التحقق: هل النتائج من خريطة السعودية فعلًا؟"
  echo "    خدمة على خريطة خاطئة ترد Ok بأرقام سليمة الشكل — لذلك نقارن بمسافات مقيسة."
  ( cd "$ROOT" && MASAR_OSRM_URL="$URL" MASAR_ROUTING_PROVIDER=osrm \
      PYTHONPATH=packages python3 scripts/osrm_verify.py ) \
    || die "التحقق فشل. لا تفعّل المزوّد قبل معالجة الموانع المذكورة."
}

finish() {
  say "ما تبقى — خطوة واحدة بيدك"
  cat <<EOF
    أضف إلى /etc/masar/masar.env :

        MASAR_ROUTING_PROVIDER=osrm
        MASAR_OSRM_URL=${URL}

    ثم:

        systemctl restart masar-api
        PYTHONPATH=packages python3 scripts/preflight.py

    لم يعدّل هذا السكربت إعدادك — تغيير مزوّد الطرق قرار تشغيلي يُتخذ
    بعلم، لا أثرًا جانبيًا لسكربت تركيب.

    بعد التفعيل: أعد تشغيل محرك التخطيط. الخطط الجديدة ستفقد وسم «تقديرية»
    ويُرفع عنها حظر الاعتماد في الإنتاج. الخطط القديمة تبقى موسومة كما هي —
    وهذا مقصود: بُنيت فعلًا على أزمنة تقديرية.
EOF
}

case "${1:-all}" in
  all)     TOTAL=6; check_requirements; fetch_map; prepare; serve; verify; finish ;;
  prepare) TOTAL=3; check_requirements; fetch_map; prepare ;;
  serve)   TOTAL=2; serve; verify ;;
  verify)  TOTAL=1; verify ;;
  *)       die "استخدام: $0 [all|prepare|serve|verify]" ;;
esac

printf '\n\033[32m✓ انتهى\033[0m\n'
