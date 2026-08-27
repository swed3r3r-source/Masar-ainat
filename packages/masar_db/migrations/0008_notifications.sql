-- ============================================================================
-- 0008) الإشعارات الخارجية — نمط «صندوق الصادر» (Transactional Outbox)
--
-- لماذا صندوق صادر لا إرسال مباشر؟ لأن الإرسال المباشر داخل المعاملة يربط
-- سلامة البيانات بتوفر مزوّد خارجي:
--
--   * لو أُرسلت الرسالة ثم تراجعت المعاملة ⇒ أُبلغ المشرف بحدث لم يقع.
--   * لو تعطّل المزوّد ⇒ فشلت العملية التشغيلية كلها بسبب رسالة نصية.
--
-- الصندوق يفصلهما: العملية تكتب صفًا في نفس معاملتها (فتصل الرسالة إن وقع
-- الحدث فقط، ولا تصل إن تراجع)، ثم يسحبها عامل مستقل ويحاول الإرسال بإعادة
-- محاولة متصاعدة. تعطّل المزوّد يؤخّر الإشعار ولا يُسقط التشغيل.
-- ============================================================================

CREATE TABLE notifications (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    channel         text NOT NULL,
    recipient       text NOT NULL,
    subject_ar      text,
    body_ar         text NOT NULL,
    status          text NOT NULL DEFAULT 'PENDING',
    priority        text NOT NULL DEFAULT 'NORMAL',

    -- ربط الإشعار بمصدره: بلا هذا يصبح سجلًا معزولًا لا يُفسَّر لاحقًا
    alert_id        uuid REFERENCES alerts(id) ON DELETE SET NULL,
    shipment_id     uuid REFERENCES shipments(id) ON DELETE SET NULL,
    route_id        uuid REFERENCES routes(id) ON DELETE SET NULL,
    hub_id          uuid REFERENCES hubs(id) ON DELETE SET NULL,
    user_id         uuid REFERENCES users(id) ON DELETE SET NULL,

    attempts        integer NOT NULL DEFAULT 0,
    max_attempts    integer NOT NULL DEFAULT 5,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_error      text,
    provider        text,
    provider_ref    text,

    -- منع التكرار: نفس الحدث لا يُرسل مرتين مهما أُعيد تشغيل العامل
    dedupe_key      text,

    created_at      timestamptz NOT NULL DEFAULT now(),
    sent_at         timestamptz,
    is_test_data    boolean NOT NULL DEFAULT false,

    CONSTRAINT notifications_channel_valid CHECK (
        channel IN ('SMS', 'EMAIL', 'PUSH', 'WEBHOOK', 'LOG')),
    CONSTRAINT notifications_status_valid CHECK (
        status IN ('PENDING', 'SENDING', 'SENT', 'FAILED', 'CANCELLED', 'NO_PROVIDER')),
    CONSTRAINT notifications_priority_valid CHECK (
        priority IN ('LOW', 'NORMAL', 'HIGH', 'CRITICAL')),
    -- الفشل النهائي يستوجب سببًا مكتوبًا، كما كل رفض في هذا النظام
    CONSTRAINT notifications_failure_reason CHECK (
        status <> 'FAILED' OR last_error IS NOT NULL),
    CONSTRAINT notifications_sent_at CHECK (
        status <> 'SENT' OR sent_at IS NOT NULL)
);

COMMENT ON TABLE notifications IS
    'صندوق صادر للإشعارات الخارجية — يُكتب في معاملة الحدث ويُرسَل بعامل مستقل';

CREATE UNIQUE INDEX notifications_dedupe_idx
    ON notifications (dedupe_key)
    WHERE dedupe_key IS NOT NULL;

-- فهرس السحب: العامل يقرأ المعلّقة المستحقة بترتيب الأولوية ثم الأقدمية
CREATE INDEX notifications_pending_idx
    ON notifications (next_attempt_at, priority)
    WHERE status IN ('PENDING', 'SENDING');

CREATE INDEX notifications_hub_idx ON notifications (hub_id, created_at DESC);

-- ------------------------------------------------------------ الصلاحيات ----
GRANT SELECT, INSERT, UPDATE ON notifications TO masar_app;

ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

-- الإشعار يخص مركزًا أو مستخدمًا: النطاق نفسه المطبَّق على بقية البيانات
CREATE POLICY notifications_read ON notifications FOR SELECT
    USING (
        app.is_global_scope()
        OR (hub_id IS NOT NULL AND app.hub_in_scope(hub_id))
        OR user_id = app.current_user_id()
    );

CREATE POLICY notifications_insert ON notifications FOR INSERT
    WITH CHECK (app.is_authenticated());

CREATE POLICY notifications_update ON notifications FOR UPDATE
    USING (app.is_global_scope())
    WITH CHECK (app.is_global_scope());

COMMENT ON POLICY notifications_read ON notifications IS
    'المشرف يرى إشعارات مركزه، والمستخدم إشعاراته، والنطاق الوطني يرى الكل';
