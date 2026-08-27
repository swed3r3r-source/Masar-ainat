# ١١) دليل النشر والتشغيل

> موجَّه لمن سيُشغّل النظام، لا لمن كتبه. كل خطوة قابلة للتنفيذ حرفيًا،
> وكل عطل مذكور معه ما يُفعل عنده.

---

## الجزء الأول: النشر لأول مرة

### ١. المتطلبات

| المكوّن | الحد الأدنى | ملاحظة |
|---|---|---|
| PostgreSQL | 16 | مع `pgcrypto` و`btree_gist` |
| Python | 3.11 | |
| المعالج | ٤ أنوية | التخطيط متوازٍ — كل نواة إضافية تقصّر زمن الخطة |
| الذاكرة | ٨ ج.ب | |
| القرص | ١٠٠ ج.ب + مساحة النسخ | مواقع السائقين تنمو بسرعة (جدول مقسّم) |
| nginx | أي إصدار حديث | لإنهاء TLS |

### ٢. المستخدم والمجلدات

```bash
useradd --system --home /opt/masar --shell /usr/sbin/nologin masar
mkdir -p /opt/masar /etc/masar /var/lib/masar /var/log/masar /var/backups/masar
chown -R masar:masar /opt/masar /var/lib/masar /var/log/masar /var/backups/masar
```

### ٣. قاعدة البيانات

```sql
CREATE ROLE masar_migrate LOGIN PASSWORD '…' CREATEDB;
CREATE ROLE masar_app     LOGIN PASSWORD '…';
CREATE DATABASE masar OWNER masar_migrate;
\c masar
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS btree_gist;
```

`CREATEDB` على دور الترحيل ليست ترفًا: `backup.sh verify` ينشئ قاعدة مؤقتة
كل أسبوع ليثبت أن النسخة قابلة للاستعادة فعلًا.

### ٤. الإعداد

```bash
cp deploy/env.production.example /etc/masar/masar.env
# املأ القيم — المُعلَّمة «ولّد» تُولَّد عشوائيًا:
openssl rand -base64 48                                    # MASAR_JWT_SECRET
python3 -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"
chown root:masar /etc/masar/masar.env && chmod 640 /etc/masar/masar.env
```

### ٥. الترحيلات وفحص الجاهزية

```bash
cd /opt/masar
set -a; . /etc/masar/masar.env; set +a
PYTHONPATH=packages python3 -m masar_db.migrate up
PYTHONPATH=packages python3 scripts/preflight.py
```

`preflight` يفشل بقائمة موانع محددة عند أي إعداد ناقص أو ضعيف — **لا تتجاوزه**.
وهو نفسه `ExecStartPre` في وحدة systemd، فالخدمة لن تبدأ بإعداد يرفضه.

### ٦. أرشفة WAL (شرط RPO ≤ ٥ دقائق)

```bash
/opt/masar/scripts/backup.sh archive-setup /var/backups/masar/wal
# اتبع المخرجات: عدّل postgresql.conf ثم أعد تشغيل PostgreSQL
```

النسخة اليومية وحدها تعني RPO = يوم كامل. الأرشفة المستمرة هي ما يجعله دقائق.

### ٧. تشغيل الخدمات

```bash
cp deploy/systemd/* /etc/systemd/system/
cp deploy/nginx/masar.conf /etc/nginx/sites-available/masar
ln -s /etc/nginx/sites-available/masar /etc/nginx/sites-enabled/
systemctl daemon-reload
systemctl enable --now masar-api masar-backup.timer masar-restore-check.timer
nginx -t && systemctl reload nginx
```

### ٨. التحقق بعد النشر

```bash
curl -fsS https://masar.example.sa/api/health | python3 -m json.tool
systemctl status masar-api
/opt/masar/scripts/backup.sh verify      # نسخة ← استعادة ← مطابقة
```

---

## الجزء الثاني: التشغيل اليومي

### الدورة التشغيلية

| الوقت | الإجراء | الدور |
|---|---|---|
| قبل الأسبوع | رفع الجدول ← تحقق ← اعتماد | التخطيط المركزي |
| قبل الأسبوع | تشغيل المحرك ← مراجعة التحذيرات ← اعتماد ← إرسال | التخطيط المركزي |
| قبل اليوم | إسناد السائقين ← نشر خطة اليوم | مشرف المركز |
| أثناء اليوم | متابعة التنبيهات والتتبع، معالجة الطلبات الفورية | برج التحكم |
| نهاية اليوم | مراجعة الاستثناءات وإغلاقها بإجراء مسجّل | مشرف المركز |

### الأوامر المتكررة

```bash
systemctl status masar-api
journalctl -u masar-api -f
journalctl -u masar-api --since "1 hour ago" | grep "طلب بطيء"
/opt/masar/scripts/backup.sh dump
/opt/masar/scripts/backup.sh measure     # قياس RTO الفعلي على حجم اليوم
```

---

## الجزء الثالث: الأعطال ومعالجتها

### «تعذر الاتصال بقاعدة البيانات»

```bash
systemctl status postgresql
psql -h $MASAR_DB_HOST -U masar_app -d masar -c "SELECT 1"
psql -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state"
```

عند امتلاء التجميعة: ارفع `MASAR_DB_POOL_MAX` أو ابحث عن استعلام عالق
(`state = 'idle in transaction'` مع `query_start` قديم).

### الخطة تستغرق وقتًا طويلًا

1. تحقق من `MASAR_SOLVE_WORKERS` — يجب أن يقارب عدد الأنوية.
2. اخفض `MASAR_SOLVE_TIME_LIMIT` — الحارس يكمل البناء بأول-ما-يصلح، الخطة
   تصبح أقل تحسينًا لكنها **تصل في وقتها**، ولا تخرق قيدًا أبدًا.
3. قسّم الخطة على مراكز أقل في التشغيل الواحد.

### كل الشحنات «غير قابلة للتخطيط»

افتح شاشة الخطة واقرأ **السبب المصنّف** لكل شحنة — لا تخمّن. الأسباب الشائعة:

| السبب | المعنى | الإجراء |
|---|---|---|
| `OUTSIDE_WORKING_HOURS` | الجدول خارج أوقات عمل المركز | صحّح أوقات العمل أو مواعيد الجدول |
| `IMPOSSIBLE_PICKUP_WINDOW` | لا يمكن بلوغ النافذة من أي مركز | راجع إحداثيات الجهة أو وسّع النافذة |
| `IMPOSSIBLE_SLA` | الموعد النهائي غير قابل للتحقيق | راجع SLA في الجدول |
| `SHIFT_LIMIT_EXCEEDED` | تنفيذها منفردة يتجاوز الوردية | تحتاج مركزًا أقرب أو ورديتين |
| `MIXING_CONSTRAINT` | قيد عدم الخلط | سائق إضافي، أو مراجعة القيد إداريًا |

### «لا يمكن نشر اليوم: توجد رحلات بلا سائق»

الرسالة تذكر مراجع الرحلات. أسندها ثم أعد النشر. إن لم يوجد مرشح مؤهل، شاشة
الإسناد تعرض **سبب المنع لكل سائق** (رخصة منتهية، تعارض زمني، تجاوز وردية،
قيد HC-15).

### تعطّل مزوّد الطرق

الخطط الجديدة تفشل بـ `DEPENDENCY_UNAVAILABLE` (٥٠٣) — وهذا مقصود: خطة على
أزمنة مختلقة أسوأ من لا خطة. الخطط المنشورة تبقى تعمل.

```bash
curl -fsS "$MASAR_OSRM_URL/table/v1/driving/46.6,24.7;46.7,24.8"
```

للطوارئ فقط: `MASAR_ROUTING_PROVIDER=haversine` — كل خطة ستحمل تحذيرًا
`ESTIMATED_TRAVEL_TIME`، ويجب اعتمادها بعلم صريح أنها تقديرية.

### الإشعارات لا تصل

```bash
curl -fsS https://masar.example.sa/api/notifications/status
```

`NO_PROVIDER` = لا مزوّد مُعدّ (التنبيهات داخل النظام فقط). `FAILED` مع
`last_error` = عطل مزوّد؛ بعد إصلاحه:

```bash
curl -X POST https://masar.example.sa/api/notifications/flush -H "authorization: Bearer …"
```

### فقد مفتاح تشفير

**لا يمكن استرجاع المستندات المشفَّرة بمفتاح مفقود** — هذا سلوك مقصود لا عطل.
لذلك: احفظ حلقة المفاتيح في خزنة أسرار مع نسخة احتياطية مستقلة عن نسخة قاعدة
البيانات، ولا تحذف مفتاحًا قديمًا قبل إعادة تشفير كل ما شُفّر به.

---

## الجزء الرابع: الاستعادة من كارثة

### استعادة كاملة (فقد قاعدة البيانات)

```bash
systemctl stop masar-api
ls -lt /var/backups/masar/masar-*.dump | head
/opt/masar/scripts/backup.sh restore /var/backups/masar/masar-<التاريخ>.dump masar
PYTHONPATH=packages python3 scripts/preflight.py
systemctl start masar-api
```

### استعادة إلى لحظة محددة (خطأ بشري: حذف أو تعديل خاطئ)

هذا ما تشتريه أرشفة WAL:

1. استعد نسخة الأساس إلى مجلد بيانات جديد.
2. أنشئ `recovery.signal` فيه.
3. في `postgresql.conf`:
   ```
   restore_command = 'cp /var/backups/masar/wal/%f %p'
   recovery_target_time = '2026-08-26 14:30:00+03'
   ```
4. شغّل الخادم — يتوقف عند اللحظة المطلوبة، ثم `pg_wal_replay_resume()`.

**قِس زمن الاستعادة على حجم إنتاجك** بـ `backup.sh measure` ولا تفترض أن
هدف RTO محقَّق قبل قياسه.

---

## الجزء الخامس: الترقية

```bash
systemctl stop masar-api
cp -a /opt/masar /opt/masar.backup-$(date +%F)     # للتراجع
/opt/masar/scripts/backup.sh dump                   # نسخة قبل الترحيل
# انشر الإصدار الجديد في /opt/masar
PYTHONPATH=packages python3 -m masar_db.migrate status
PYTHONPATH=packages python3 -m masar_db.migrate up
PYTHONPATH=packages python3 scripts/preflight.py
systemctl start masar-api && curl -fsS localhost:8080/api/health
```

**التراجع:** أعد المجلد القديم واستعد النسخة. الترحيلات غير عكسية، لذا النسخة
قبل الترحيل ليست احتياطًا زائدًا بل شرط التراجع.

---

## الجزء السادس: المراقبة — ماذا تُنذر عليه

| المؤشر | العتبة | لماذا |
|---|---|---|
| `/api/health` | فشل مرتين متتاليتين | الخدمة أو قاعدة البيانات ساقطة |
| `failed_count` في `pg_stat_archiver` | > 0 | هدف RPO لم يعد مضمونًا |
| عمر آخر نسخة احتياطية | > ٢٦ ساعة | النسخة اليومية لم تعمل |
| «طلب بطيء» في السجل | تكرار | تدهور أداء قبل أن يصبح انقطاعًا |
| إشعارات `FAILED` | > ٠ | التنبيهات لا تغادر النظام |
| رحلات `PUBLISHED` بلا موقع سائق | أثناء الدوام | التتبع متوقف |
| مساحة القرص | > ٨٠٪ | جداول المواقع والحرارة تنمو باطراد |

### تشغيل حزم التحقق على بيئة مماثلة للإنتاج

```bash
./scripts/run_tests.sh              # ٤٦ سيناريو + مصفوفة التغطية
python3 tests/run_tests.py --security   # الفحوص الأمنية وحدها
./scripts/backup.sh verify
```

**لا تُشغَّل حزم الاختبار على قاعدة الإنتاج**: تنشئ بيانات موسومة
`is_test_data`، ومحفّز `guard_no_test_data_in_production` يرفضها في بيئة
الإنتاج — وهو خط الدفاع الأخير لا خطة العمل.
