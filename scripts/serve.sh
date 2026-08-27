#!/usr/bin/env bash
# تشغيل الخادم في الخلفية بشكل منفصل عن جلسة الطرفية
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env.dev; set +a
mkdir -p var/logs var/run

PORT="${MASAR_PORT:-8080}"
PIDFILE="var/run/masar.pid"

case "${1:-start}" in
  start)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "الخادم يعمل مسبقًا (pid $(cat "$PIDFILE"))"
      exit 0
    fi
    PYTHONPATH=packages setsid nohup python3 -m uvicorn masar_api.app:app \
      --host 127.0.0.1 --port "$PORT" --log-level warning \
      --no-server-header --no-date-header \
      >> var/logs/server.log 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
    for _ in $(seq 1 40); do
      sleep 0.5
      # فحص العملية **قبل** فحص المنفذ: خادم آخر يشغل المنفذ نفسه يجعل
      # /api/health يرد بنجاح بينما عمليتنا ماتت — فيُعلن نجاح كاذب
      # وتُشغَّل الاختبارات على خادم غير الذي قصدناه.
      if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "❌ ماتت عملية الخادم أثناء الإقلاع — آخر سطور السجل:"
        tail -20 var/logs/server.log
        rm -f "$PIDFILE"
        exit 1
      fi
      if curl -fsS "http://127.0.0.1:${PORT}/api/health" > /dev/null 2>&1; then
        echo "✅ الخادم يعمل على http://127.0.0.1:${PORT} (pid $(cat "$PIDFILE"))"
        exit 0
      fi
    done
    echo "❌ تعذر بدء الخادم — آخر سطور السجل:"; tail -20 var/logs/server.log; exit 1
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      kill "$(cat "$PIDFILE")" 2>/dev/null || true
      rm -f "$PIDFILE"
      echo "أُوقف الخادم"
    fi
    ;;
  restart)
    "$0" stop; sleep 1; "$0" start
    ;;
  status)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      curl -fsS "http://127.0.0.1:${PORT}/api/health" && echo
    else
      echo "الخادم متوقف"
    fi
    ;;
esac
