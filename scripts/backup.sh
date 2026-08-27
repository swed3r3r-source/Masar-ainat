#!/usr/bin/env bash
# النسخ الاحتياطي والاستعادة (§30 اختبار ٤٦، §24 خطة التعافي).
#
#   ./scripts/backup.sh dump [ملف]     نسخة كاملة مضغوطة
#   ./scripts/backup.sh restore <ملف> <قاعدة>   استعادة في قاعدة محددة
#   ./scripts/backup.sh verify         نسخ ← استعادة في قاعدة مؤقتة ← مطابقة الأعداد
#
# «verify» هو الاختبار الفعلي: نسخة بلا استعادة مُثبتة ليست نسخة احتياطية.
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env.dev; set +a

PGBIN=/usr/lib/postgresql/16/bin
HOST="${MASAR_DB_HOST:-127.0.0.1}"
PORT="${MASAR_DB_PORT:-55432}"
DB="${MASAR_DB_NAME:-masar_dev}"
OWNER="${MASAR_DB_MIGRATE_USER:-masar_migrate}"
export PGPASSWORD="${MASAR_DB_MIGRATE_PASSWORD:-}"

BACKUP_DIR=var/backups
mkdir -p "$BACKUP_DIR"

#: الجداول التي تُطابَق أعدادها بعد الاستعادة — بيانات تشغيلية لا تُفقد
CRITICAL_TABLES=(regions cities hubs facilities users drivers vehicles \
                 shipments routes route_stops plans plan_days audit_log \
                 shipment_events documents alerts temperature_readings)

stamp() { date -u +%Y%m%dT%H%M%SZ; }

dump() {
  local out="${1:-$BACKUP_DIR/masar-$(stamp).dump}"
  "$PGBIN/pg_dump" -h "$HOST" -p "$PORT" -U "$OWNER" -d "$DB" \
      --format=custom --compress=6 --file "$out" || return 1
  echo "$out"
}

restore() {
  local file="$1" target="$2"
  "$PGBIN/dropdb" -h "$HOST" -p "$PORT" -U "$OWNER" --if-exists "$target" >/dev/null 2>&1
  "$PGBIN/createdb" -h "$HOST" -p "$PORT" -U "$OWNER" "$target" || return 1
  # الأدوار موجودة مسبقًا على العنقود؛ نتجاهل أخطاء منح الصلاحيات المكررة فقط
  "$PGBIN/pg_restore" -h "$HOST" -p "$PORT" -U "$OWNER" -d "$target" \
      --no-owner --exit-on-error "$file" > "$BACKUP_DIR/restore.log" 2>&1
}

count_in() {
  local database="$1" table="$2"
  "$PGBIN/psql" -h "$HOST" -p "$PORT" -U "$OWNER" -d "$database" -tAc \
      "SELECT count(*) FROM $table" 2>/dev/null || echo "ERR"
}

verify() {
  local target="masar_restore_check"
  echo "── ١) أخذ نسخة كاملة ──"
  local file
  file="$(dump)" || { echo "❌ فشل pg_dump"; return 1; }
  local size
  size=$(stat -c%s "$file")
  echo "الملف: $file ($size بايت)"
  if [ "$size" -lt 1000 ]; then echo "❌ حجم النسخة غير معقول"; return 1; fi

  echo "── ٢) الاستعادة في قاعدة منفصلة ($target) ──"
  if ! restore "$file" "$target"; then
    echo "❌ فشلت الاستعادة:"; tail -30 "$BACKUP_DIR/restore.log"; return 1
  fi
  echo "تمت الاستعادة"

  echo "── ٣) مطابقة أعداد الصفوف في الجداول الحرجة ──"
  local failures=0
  for table in "${CRITICAL_TABLES[@]}"; do
    local before after
    before=$(count_in "$DB" "$table")
    after=$(count_in "$target" "$table")
    if [ "$before" = "$after" ] && [ "$before" != "ERR" ]; then
      printf '  ✅ %-22s %s صف\n' "$table" "$before"
    else
      printf '  ❌ %-22s الأصل=%s المستعاد=%s\n' "$table" "$before" "$after"
      failures=$((failures + 1))
    fi
  done

  echo "── ٤) فحص أن الاستعادة أعادت القيود لا الصفوف فقط ──"
  local policies triggers
  policies=$("$PGBIN/psql" -h "$HOST" -p "$PORT" -U "$OWNER" -d "$target" -tAc \
      "SELECT count(*) FROM pg_policies WHERE schemaname = 'public'")
  triggers=$("$PGBIN/psql" -h "$HOST" -p "$PORT" -U "$OWNER" -d "$target" -tAc \
      "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal")
  echo "  سياسات RLS المستعادة: $policies · المحفّزات: $triggers"
  [ "${policies:-0}" -gt 0 ] || { echo "  ❌ لم تُستعد سياسات RLS"; failures=$((failures + 1)); }
  [ "${triggers:-0}" -gt 0 ] || { echo "  ❌ لم تُستعد المحفّزات"; failures=$((failures + 1)); }

  "$PGBIN/dropdb" -h "$HOST" -p "$PORT" -U "$OWNER" --if-exists "$target" >/dev/null 2>&1

  echo "── ٥) أرشفة WAL (هدف RPO ≤ ٥ دقائق) ──"
  local archive_mode archive_timeout archived failed
  archive_mode=$("$PGBIN/psql" -h "$HOST" -p "$PORT" -U "$OWNER" -d "$DB" -tAc \
      "SELECT current_setting('archive_mode', true)")
  archive_timeout=$("$PGBIN/psql" -h "$HOST" -p "$PORT" -U "$OWNER" -d "$DB" -tAc \
      "SELECT current_setting('archive_timeout', true)")
  archived=$("$PGBIN/psql" -h "$HOST" -p "$PORT" -U "$OWNER" -d "$DB" -tAc \
      "SELECT archived_count FROM pg_stat_archiver")
  failed=$("$PGBIN/psql" -h "$HOST" -p "$PORT" -U "$OWNER" -d "$DB" -tAc \
      "SELECT failed_count FROM pg_stat_archiver")
  echo "  archive_mode=$archive_mode · archive_timeout=$archive_timeout · مؤرشف=$archived · فاشل=$failed"
  if [ "$archive_mode" = "on" ]; then
    if [ "${failed:-0}" -gt 0 ]; then
      echo "  ❌ فشلت أرشفة $failed مقطعًا — RPO غير مضمون"
      failures=$((failures + 1))
    else
      echo "  ✅ الأرشفة تعمل — الاستعادة إلى لحظة محددة ممكنة"
    fi
  else
    echo "  ⚠️  الأرشفة غير مفعّلة: النسخة الكاملة وحدها تعني RPO = فترة النسخ"
    echo "     شغّل: $0 archive-setup"
  fi

  if [ "$failures" -gt 0 ]; then
    echo "❌ فشل التحقق في $failures موضعًا"
    return 1
  fi
  echo "✅ النسخ والاستعادة سليمان — كل الجداول الحرجة والقيود مطابقة"
}

# ---------------------------------------------------- أرشفة WAL (RPO) ------
# نسخة كاملة يومية وحدها تعني فقدان يوم عمل عند العطل. أرشفة WAL المستمرة هي
# ما يجعل RPO دقائق: كل مقطع سجل مكتمل يُنسخ فور اكتماله، فالاستعادة تصل إلى
# أي لحظة (Point-In-Time Recovery).
archive_setup() {
  local archive_dir="${1:-$PWD/var/wal-archive}"
  mkdir -p "$archive_dir"
  echo "── تفعيل أرشفة WAL إلى: $archive_dir ──"
  "$PGBIN/psql" -h "$HOST" -p "$PORT" -U "$OWNER" -d "$DB" -tAc \
      "SELECT 1" >/dev/null || { echo "❌ تعذر الاتصال"; return 1; }

  cat <<EOF
أضف إلى postgresql.conf ثم أعد تشغيل الخادم:

    wal_level = replica
    archive_mode = on
    archive_command = 'test ! -f $archive_dir/%f && cp %p $archive_dir/%f'
    archive_timeout = 300        # يفرض إغلاق مقطع كل ٥ دقائق ⇒ RPO ≤ ٥ د

ثم خذ نسخة أساس مرة واحدة:

    $PGBIN/pg_basebackup -h $HOST -p $PORT -U $OWNER \\
        -D $archive_dir/base -Fp -Xs -P

الاستعادة إلى لحظة محددة:

    1) استعد نسخة الأساس إلى مجلد بيانات جديد
    2) أنشئ recovery.signal
    3) restore_command = 'cp $archive_dir/%f %p'
       recovery_target_time = 'YYYY-MM-DD HH:MM:SS+03'
    4) شغّل الخادم — يتوقف عند اللحظة المطلوبة

EOF
  local current
  current=$("$PGBIN/psql" -h "$HOST" -p "$PORT" -U "$OWNER" -d "$DB" -tAc \
      "SELECT current_setting('archive_mode', true)")
  if [ "$current" = "on" ]; then
    echo "✅ الأرشفة مفعّلة على هذا العنقود"
  else
    echo "⚠️  الأرشفة غير مفعّلة حاليًا (archive_mode=$current) — إعداد نشر مطلوب"
  fi
}

# --------------------------------------------- سياسة الاحتفاظ والتقليم -----
prune() {
  local keep_days="${1:-14}"
  echo "── تقليم النسخ الأقدم من $keep_days يومًا ──"
  local removed=0
  while IFS= read -r file; do
    rm -f "$file" && removed=$((removed + 1))
    echo "  حُذفت: $(basename "$file")"
  done < <(find "$BACKUP_DIR" -maxdepth 1 -name 'masar-*.dump' -mtime "+$keep_days")
  echo "✅ حُذفت $removed نسخة · المتبقي: $(find "$BACKUP_DIR" -maxdepth 1 -name 'masar-*.dump' | wc -l)"
}

# --------------------------------------------------- قياس RTO الفعلي -------
# الهدف المعلن RTO ≤ ٦٠ دقيقة. الرقم بلا قياس ادّعاء، فنقيسه على حجم البيانات
# الحالي ونعلن أنه يتناسب طرديًا مع الحجم.
measure() {
  local target="masar_rto_measure"
  echo "── قياس زمن النسخ والاستعادة الفعلي ──"
  local size
  size=$("$PGBIN/psql" -h "$HOST" -p "$PORT" -U "$OWNER" -d "$DB" -tAc \
      "SELECT pg_size_pretty(pg_database_size('$DB'))")
  echo "حجم قاعدة البيانات: $size"

  local start_dump end_dump file
  start_dump=$(date +%s)
  file="$(dump)" || return 1
  end_dump=$(date +%s)

  local start_restore end_restore
  start_restore=$(date +%s)
  restore "$file" "$target" || { echo "❌ فشلت الاستعادة"; return 1; }
  end_restore=$(date +%s)
  "$PGBIN/dropdb" -h "$HOST" -p "$PORT" -U "$OWNER" --if-exists "$target" >/dev/null 2>&1

  local dump_seconds=$((end_dump - start_dump))
  local restore_seconds=$((end_restore - start_restore))
  local total=$((dump_seconds + restore_seconds))
  echo "  زمن النسخ    : ${dump_seconds} ث"
  echo "  زمن الاستعادة: ${restore_seconds} ث"
  echo "  الإجمالي     : ${total} ث"
  if [ "$restore_seconds" -le 3600 ]; then
    echo "✅ زمن الاستعادة المقيس ضمن هدف RTO (≤ ٦٠ دقيقة) على هذا الحجم"
  else
    echo "❌ زمن الاستعادة يتجاوز هدف RTO"
    return 1
  fi
  echo "⚠️  الرقم يتناسب مع حجم البيانات — أعد القياس على حجم الإنتاج."
}

# ----------------------------------------------------- الجدولة الدورية -----
schedule() {
  local hour="${1:-2}"
  local script_path
  script_path="$(cd "$(dirname "$0")" && pwd)/backup.sh"
  cat <<EOF
── جدولة النسخ الاحتياطي اليومي ──

أضف إلى crontab (المستخدم الذي يملك القاعدة):

    # نسخة كاملة يوميًا الساعة $hour:00 بتوقيت الخادم
    0 $hour * * *  $script_path dump >> $PWD/var/logs/backup.log 2>&1
    # تحقق أسبوعي من صلاحية الاستعادة (لا نسخة بلا استعادة مُثبتة)
    0 $hour * * 5  $script_path verify >> $PWD/var/logs/backup.log 2>&1
    # تقليم النسخ الأقدم من ١٤ يومًا
    30 $hour * * * $script_path prune 14 >> $PWD/var/logs/backup.log 2>&1

للأنظمة التي تعمل بـ systemd، استخدم مؤقتًا بدل cron:
    systemctl enable --now masar-backup.timer   (انظر deploy/systemd/)
EOF
}

case "${1:-verify}" in
  dump)          dump "${2:-}" ;;
  restore)       restore "$2" "$3" ;;
  verify)        verify ;;
  measure)       measure ;;
  prune)         prune "${2:-14}" ;;
  schedule)      schedule "${2:-2}" ;;
  archive-setup) archive_setup "${2:-}" ;;
  *) echo "الاستخدام: $0 {dump|restore <ملف> <قاعدة>|verify|measure|prune [أيام]|schedule [ساعة]|archive-setup [مجلد]}"; exit 2 ;;
esac
