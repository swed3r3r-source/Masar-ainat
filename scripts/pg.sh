#!/usr/bin/env bash
# إدارة عنقود PostgreSQL محلي للتطوير — معزول عن أي عنقود آخر على الجهاز.
#
#   ./scripts/pg.sh init      # إنشاء العنقود والأدوار والقاعدة (مرة واحدة)
#   ./scripts/pg.sh start|stop|status
#   ./scripts/pg.sh switch-wal   # تدوير مقطع WAL (لاختبارات الأرشفة)
#   ./scripts/pg.sh psql      # جلسة تفاعلية بصلاحية المدير
#
# كل المسارات قابلة للتجاوز بالبيئة، فلا شيء مربوط بجهاز بعينه:
#   PGBIN    مجلد ثنائيات PostgreSQL 16
#   PGDATA   مجلد بيانات العنقود   (افتراضيًا ~/.masar-pgdata)
#   PGPORT   المنفذ                (افتراضيًا 55432 — بعيد عن 5432 المعتاد)
#   PGRUNAS  حساب مالك PGDATA إن اختلف عن المستخدم الحالي
set -uo pipefail
cd "$(dirname "$0")/.."

PGPORT="${PGPORT:-55432}"
PGDATA="${PGDATA:-$HOME/.masar-pgdata}"
PGRUN="${PGRUN:-$HOME/.masar-pgrun}"
SUPERUSER="${PGSUPERUSER:-$(id -un)}"
# PostgreSQL يرفض العمل بصلاحية root، ويشترط أن يملك مالكُ PGDATA العمليةَ.
# PGRUNAS يسمح بتشغيل الأوامر بحساب المالك عند اختلافه عن المستخدم الحالي.
RUNAS="${PGRUNAS:-}"
as_owner() {
  if [ -n "$RUNAS" ] && [ "$RUNAS" != "$(id -un)" ]; then
    su "$RUNAS" -c "$1"
  else
    eval "$1"
  fi
}

find_bin() {
  if [ -n "${PGBIN:-}" ] && [ -x "$PGBIN/pg_ctl" ]; then echo "$PGBIN"; return; fi
  for candidate in /usr/lib/postgresql/16/bin /usr/pgsql-16/bin \
                   /opt/homebrew/opt/postgresql@16/bin /usr/local/opt/postgresql@16/bin; do
    [ -x "$candidate/pg_ctl" ] && { echo "$candidate"; return; }
  done
  if command -v pg_ctl >/dev/null 2>&1; then dirname "$(command -v pg_ctl)"; return; fi
  echo ""
}

PGBIN="$(find_bin)"
if [ -z "$PGBIN" ]; then
  echo "❌ لم يُعثر على ثنائيات PostgreSQL 16." >&2
  echo "   ثبّتها، أو عيّن PGBIN=/مسار/إلى/postgresql/16/bin" >&2
  exit 1
fi

running() { "$PGBIN/pg_isready" -h 127.0.0.1 -p "$PGPORT" -q 2>/dev/null; }

init() {
  if [ -f "$PGDATA/PG_VERSION" ]; then
    echo "العنقود موجود مسبقًا في $PGDATA"
  else
    echo "── إنشاء العنقود في $PGDATA ──"
    mkdir -p "$PGDATA" "$PGRUN"
    as_owner "'$PGBIN/initdb' -D '$PGDATA' -U '$SUPERUSER' --auth-local=trust \
      --auth-host=scram-sha-256 -E UTF8 --locale=C" > /dev/null || return 1
  fi
  start || return 1

  : "${MASAR_DB_NAME:?عيّن متغيرات البيئة أولًا: set -a; . ./.env.dev; set +a}"
  : "${MASAR_DB_USER:?}" ; : "${MASAR_DB_PASSWORD:?}"
  : "${MASAR_DB_MIGRATE_USER:?}" ; : "${MASAR_DB_MIGRATE_PASSWORD:?}"

  echo "── إنشاء الأدوار والقاعدة ──"
  psql_super -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${MASAR_DB_MIGRATE_USER}') THEN
    CREATE ROLE ${MASAR_DB_MIGRATE_USER} LOGIN PASSWORD '${MASAR_DB_MIGRATE_PASSWORD}' CREATEDB;
  ELSE
    ALTER ROLE ${MASAR_DB_MIGRATE_USER} PASSWORD '${MASAR_DB_MIGRATE_PASSWORD}';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${MASAR_DB_USER}') THEN
    CREATE ROLE ${MASAR_DB_USER} LOGIN PASSWORD '${MASAR_DB_PASSWORD}';
  ELSE
    ALTER ROLE ${MASAR_DB_USER} PASSWORD '${MASAR_DB_PASSWORD}';
  END IF;
END \$\$;
SQL
  if ! psql_super -tAc "SELECT 1 FROM pg_database WHERE datname='${MASAR_DB_NAME}'" | grep -q 1; then
    psql_super -c "CREATE DATABASE ${MASAR_DB_NAME} OWNER ${MASAR_DB_MIGRATE_USER}"
  fi
  psql_super -d "${MASAR_DB_NAME}" -v ON_ERROR_STOP=1 \
    -c "CREATE EXTENSION IF NOT EXISTS pgcrypto" \
    -c "CREATE EXTENSION IF NOT EXISTS btree_gist" > /dev/null
  echo "✅ القاعدة ${MASAR_DB_NAME} جاهزة على 127.0.0.1:${PGPORT}"
}

psql_super() { "$PGBIN/psql" -w -h 127.0.0.1 -p "$PGPORT" -U "$SUPERUSER" -d postgres "$@"; }

start() {
  if running; then echo "PostgreSQL يعمل مسبقًا على المنفذ $PGPORT"; return 0; fi
  [ -f "$PGDATA/PG_VERSION" ] || { echo "❌ لا يوجد عنقود — شغّل: $0 init" >&2; return 1; }
  mkdir -p "$PGRUN"
  rm -f "$PGDATA/postmaster.pid" 2>/dev/null
  as_owner "'$PGBIN/pg_ctl' -D '$PGDATA' \
    -o '-p $PGPORT -k $PGRUN -c listen_addresses=127.0.0.1' \
    -l '$PGDATA/server.log' -w start" > /dev/null 2>&1
  for _ in $(seq 1 40); do
    sleep 0.5
    running && { echo "✅ PostgreSQL يعمل على 127.0.0.1:$PGPORT"; return 0; }
  done
  echo "❌ تعذر بدء PostgreSQL:"; tail -20 "$PGDATA/server.log"; return 1
}

case "${1:-start}" in
  init)   init ;;
  start)  start ;;
  stop)   as_owner "'$PGBIN/pg_ctl' -D '$PGDATA' stop -m fast" ;;
  status) "$PGBIN/pg_isready" -h 127.0.0.1 -p "$PGPORT" ;;
  psql)   shift; psql_super "$@" ;;
  # تدوير مقطع WAL: يحتاج صلاحية مدير، ويجعل اختبار الأرشفة حتميًا بدل
  # أن ينتظر انقضاء archive_timeout.
  switch-wal) psql_super -tAc "SELECT pg_switch_wal()" > /dev/null 2>&1 \
              && echo "✓ دُوِّر مقطع WAL" || echo "· تعذر تدوير WAL (غير حرج)" ;;
  *) echo "الاستخدام: $0 {init|start|stop|status|psql|switch-wal}"; exit 2 ;;
esac
