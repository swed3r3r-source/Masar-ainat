# صورة تطبيق «مسار عينات» — بيئة تجريبية (staging).
#
# الإصدار مثبَّت بدقة: python:3.11.9-slim-bookworm. لا `latest` ولا `3.11`
# ولا `slim` وحدها — وسم متحرك يعني أن إعادة البناء بعد شهر تنتج صورة
# مختلفة، فتضيع القدرة على إعادة إنتاج بيئة كانت تعمل.
FROM python:3.11.9-slim-bookworm

# متطلبات نظام قليلة: عميل PostgreSQL مكتوب داخل المشروع فلا حاجة لـlibpq،
# وcryptography تأتي بعجلة مبنية فلا حاجة لمترجم.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# مستخدم غير جذر: الخدمة لا تحتاج صلاحيات، ومنحها إياها بلا سبب توسيع
# لأثر أي ثغرة مستقبلية.
RUN useradd --system --create-home --uid 10001 masar

WORKDIR /app

# ── الاعتماديات أولًا: طبقة تُخزَّن مؤقتًا ولا تُعاد إلا عند تغيّرها ────────
COPY requirements.in requirements.txt ./
# ملف القفل لا يدخل هذه الحزمة حاليًا؛ لذلك نثبت من requirements.txt
# ونُبقي التحذير صريحًا إلى أن يُضاف قفل مولَّد ومراجع بالتجزئات.
RUN echo "⚠️  requirements.lock غير موجود — تثبيت بلا تجزئات." && \
    echo "⚠️  ولّده على خادم متصل: ./scripts/lock_dependencies.sh" && \
    pip install --no-cache-dir -r requirements.txt

COPY packages/ ./packages/
COPY web/ ./web/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY README.md STATUS.md ./

RUN mkdir -p /var/lib/masar/storage /var/lib/masar/uploads /var/log/masar \
 && chown -R masar:masar /var/lib/masar /var/log/masar /app

USER masar
ENV PYTHONPATH=/app/packages \
    PYTHONUNBUFFERED=1 \
    MASAR_PORT=8080

EXPOSE 8080

# الفحص الصحي جزء من الصورة لا من التنسيق وحده: خدمة لا تعرف كيف تُفحص
# تُعلن «تعمل» وهي ساقطة.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1

# preflight بوابة إقلاع: إعداد ناقص أو تشفير غير معتمد يوقف الخدمة هنا،
# لا بعد ساعة من التشغيل.
CMD ["sh", "-c", "python3 scripts/preflight.py && exec python3 -m uvicorn masar_api.app:app --host 0.0.0.0 --port 8080 --no-server-header --no-date-header --log-level warning"]
