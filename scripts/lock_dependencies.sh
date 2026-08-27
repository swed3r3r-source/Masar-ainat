#!/usr/bin/env bash
# يولّد requirements.lock من requirements.in — بتجزئات (hashes) لكل حزمة.
#
#   ./scripts/lock_dependencies.sh            # توليد
#   ./scripts/lock_dependencies.sh --verify   # تحقق فقط: هل القفل موجود وسليم؟
#
# **يتطلب اتصالًا بـPyPI.** لا يمكن تلفيق ملف قفل: التجزئة بصمة للملف الفعلي
# المنشور، ولا تُشتق من رقم الإصدار. أي «قفل» مكتوب يدويًا إما يفشل عند
# التثبيت أو — وهو الأسوأ — يمرّ بتجزئات خاطئة فيُبطل الحماية كلها.
#
# ما يشتريه القفل: تثبيت لا يتغيّر بين البيئات، وحماية من اختطاف حزمة أو
# إعادة نشر إصدار بمحتوى مختلف. بدونه، `pip install` اليوم قد يجلب شيئًا
# غير ما جلبه أمس بالرقم نفسه.
set -euo pipefail
cd "$(dirname "$0")/.."

IN="requirements.in"
LOCK="requirements.lock"
PYTHON="${PYTHON:-python3}"

# ── الإصدار المستهدف: مثبَّت لا مفتوح ──────────────────────────────────────
# القفل صالح لإصدار Python الذي وُلّد عليه فقط: العجلات تختلف بين 3.11 و3.12،
# والاعتماديات المشروطة بالإصدار تختلف كذلك.
REQUIRED_PY="3.11"

need_python() {
  local actual
  actual="$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  if [ "$actual" != "$REQUIRED_PY" ]; then
    echo "❌ إصدار Python هو $actual والمطلوب $REQUIRED_PY بالضبط." >&2
    echo "   القفل صالح لإصدار واحد. استخدم: PYTHON=python$REQUIRED_PY $0" >&2
    exit 1
  fi
}

verify() {
  [ -f "$LOCK" ] || {
    echo "❌ $LOCK غير موجود."
    echo "   وهذا متوقَّع في نسخة بُنيت بلا نفاذ إلى PyPI — راجع STATUS.md."
    echo "   على خادم متصل: $0"
    exit 1
  }
  grep -q -- "--hash=sha256:" "$LOCK" || {
    echo "❌ $LOCK بلا تجزئات — ملف قفل بلا hashes لا يحمي من شيء." >&2
    exit 1
  }
  local packages hashes
  packages="$(grep -cE '^[A-Za-z0-9]' "$LOCK" || true)"
  hashes="$(grep -c -- '--hash=sha256:' "$LOCK" || true)"
  echo "✅ $LOCK موجود — نحو $packages حزمة و$hashes تجزئة"
  echo "   التثبيت: pip install --require-hashes -r $LOCK"
}

generate() {
  need_python
  [ -f "$IN" ] || { echo "❌ $IN غير موجود" >&2; exit 1; }

  echo "── فحص الاتصال بـPyPI ──"
  if ! $PYTHON -c "
import urllib.request, sys
try:
    urllib.request.urlopen('https://pypi.org/simple/', timeout=10)
except Exception as exc:
    print(exc); sys.exit(1)
" 2>/dev/null; then
    cat >&2 <<'MSG'
❌ لا يمكن الوصول إلى PyPI من هذه البيئة.

   لا يُولَّد ملف قفل بلا اتصال، ولن يُختلق: التجزئة بصمة للملف المنشور
   ولا تُشتق من رقم الإصدار. ملف قفل ملفَّق يمنح شعورًا بالأمان بلا أساس.

   شغّل هذا السكربت على خادم متصل بـPyPI، ثم انقل requirements.lock.
MSG
    exit 2
  fi

  echo "── تثبيت pip-tools ──"
  $PYTHON -m pip install --quiet --upgrade "pip-tools>=7.4"

  echo "── توليد $LOCK (قد يستغرق دقائق: يُنزّل كل عجلة ليحسب تجزئتها) ──"
  $PYTHON -m piptools compile \
    --generate-hashes \
    --allow-unsafe \
    --strip-extras \
    --output-file "$LOCK" \
    "$IN"

  echo
  verify
  cat <<'MSG'

── خطوة إلزامية بعد التوليد ──
شغّل الحزمة كاملة على البيئة المقفولة قبل اعتماد القفل:

    python3 -m venv .venv && . .venv/bin/activate
    pip install --require-hashes -r requirements.lock
    ./scripts/run_tests.sh

قفل لم تُشغَّل عليه الاختبارات ليس مقفولًا — هو مجرد ملف.
MSG
}

case "${1:-generate}" in
  --verify|verify) verify ;;
  generate|"")     generate ;;
  *) echo "الاستخدام: $0 [generate|--verify]" >&2; exit 2 ;;
esac
