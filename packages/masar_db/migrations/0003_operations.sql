-- =====================================================================
-- الترحيل 0003: التنفيذ، المستندات، الاستثناءات، التنبيهات، التتبع، الحرارة
-- =====================================================================

-- ---------------------------------------------------------------------
-- أحداث الشحنة — المصدر الوحيد للحقيقة في التنفيذ
-- ---------------------------------------------------------------------
CREATE TABLE shipment_events (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    shipment_id      uuid NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    route_id         uuid REFERENCES routes(id) ON DELETE SET NULL,
    route_stop_id    uuid REFERENCES route_stops(id) ON DELETE SET NULL,
    event_type       text NOT NULL,
    -- الوقت الفعلي كما سجله الجهاز (قد يكون قديمًا عند المزامنة بعد انقطاع)
    occurred_at      timestamptz NOT NULL,
    -- وقت وصول الحدث للخادم
    received_at      timestamptz NOT NULL DEFAULT now(),
    lat              double precision,
    lon              double precision,
    accuracy_m       numeric(8,2),
    driver_id        uuid REFERENCES drivers(id) ON DELETE SET NULL,
    actor_user_id    uuid REFERENCES users(id) ON DELETE SET NULL,
    -- معرّف يولّده العميل لضمان عدم التكرار عند إعادة المزامنة (§18)
    client_event_id  text,
    was_offline      boolean NOT NULL DEFAULT false,
    payload          jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_test_data     boolean NOT NULL DEFAULT false,
    CONSTRAINT shipment_events_type_valid CHECK (event_type IN (
        'ROUTE_STARTED','ARRIVED_PICKUP','PICKED_UP','ARRIVED_DELIVERY','DELIVERED',
        'EXCEPTION_RECORDED','CANCELLED','DOCUMENT_UPLOADED','REASSIGNED',
        'STATUS_CORRECTED','ROUTE_COMPLETED'))
);
CREATE UNIQUE INDEX shipment_events_client_uniq
    ON shipment_events(driver_id, client_event_id)
    WHERE client_event_id IS NOT NULL;
CREATE INDEX shipment_events_shipment_idx ON shipment_events(shipment_id, occurred_at);
CREATE INDEX shipment_events_route_idx    ON shipment_events(route_id, occurred_at);

COMMENT ON COLUMN shipment_events.client_event_id IS
    'مفتاح تكرار للعمل دون اتصال — إعادة إرسال نفس الحدث لا تُنشئ سجلًا جديدًا';

-- ---------------------------------------------------------------------
-- سجل تغييرات حالة الشحنة
-- ---------------------------------------------------------------------
CREATE TABLE shipment_status_history (
    id             bigserial PRIMARY KEY,
    shipment_id    uuid NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    from_status    text,
    to_status      text NOT NULL,
    changed_at     timestamptz NOT NULL DEFAULT now(),
    changed_by     uuid REFERENCES users(id) ON DELETE SET NULL,
    actor_role     text,
    reason         text,
    source         text NOT NULL DEFAULT 'API',
    context        jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX shipment_status_history_idx
    ON shipment_status_history(shipment_id, changed_at DESC);

-- ---------------------------------------------------------------------
-- المستندات والصور
-- ---------------------------------------------------------------------
CREATE TABLE documents (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    shipment_id    uuid REFERENCES shipments(id) ON DELETE CASCADE,
    route_id       uuid REFERENCES routes(id) ON DELETE CASCADE,
    route_stop_id  uuid REFERENCES route_stops(id) ON DELETE SET NULL,
    exception_id   uuid,
    doc_kind       text NOT NULL,
    storage_key    text NOT NULL UNIQUE,
    original_name  text,
    content_type   text NOT NULL,
    byte_size      bigint NOT NULL,
    sha256         text NOT NULL,
    captured_at    timestamptz,
    lat            double precision,
    lon            double precision,
    uploaded_by    uuid REFERENCES users(id) ON DELETE SET NULL,
    uploaded_at    timestamptz NOT NULL DEFAULT now(),
    is_test_data   boolean NOT NULL DEFAULT false,
    CONSTRAINT documents_kind_valid CHECK (doc_kind IN
        ('PICKUP_PROOF','DELIVERY_PROOF','EXCEPTION_PROOF','TEMPERATURE_LOG','OTHER')),
    CONSTRAINT documents_size_positive CHECK (byte_size > 0),
    CONSTRAINT documents_type_allowed CHECK (content_type IN
        ('image/jpeg','image/png','image/webp','application/pdf'))
);
CREATE INDEX documents_shipment_idx ON documents(shipment_id);
CREATE INDEX documents_stop_idx     ON documents(route_stop_id);

-- ---------------------------------------------------------------------
-- الحالات الاستثنائية
-- ---------------------------------------------------------------------
CREATE TABLE shipment_exceptions (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    shipment_id        uuid NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    route_id           uuid REFERENCES routes(id) ON DELETE SET NULL,
    route_stop_id      uuid REFERENCES route_stops(id) ON DELETE SET NULL,
    hub_id             uuid NOT NULL REFERENCES hubs(id) ON DELETE RESTRICT,
    reason             text NOT NULL,
    note               text,
    occurred_at        timestamptz NOT NULL DEFAULT now(),
    lat                double precision,
    lon                double precision,
    reported_by        uuid REFERENCES users(id) ON DELETE SET NULL,
    reported_by_driver uuid REFERENCES drivers(id) ON DELETE SET NULL,
    status             text NOT NULL DEFAULT 'OPEN',
    -- §19: التزام التسليم يبقى مفتوحًا حتى يقرر المشرف
    keeps_obligation   boolean NOT NULL DEFAULT false,
    action_taken       text,
    resolution         text,
    resolved_by        uuid REFERENCES users(id) ON DELETE SET NULL,
    resolved_at        timestamptz,
    is_test_data       boolean NOT NULL DEFAULT false,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT shipment_exceptions_reason_valid CHECK (reason IN (
        'NO_SAMPLES','SAMPLES_NOT_READY','FACILITY_CLOSED','NO_STAFF',
        'CANCELLED_BEFORE_PICKUP','PICKUP_DELAYED','DELIVERY_DELAYED',
        'TEMPERATURE_BREACH','BOX_DAMAGED','LOCATION_UNREACHABLE',
        'VEHICLE_BREAKDOWN','OTHER')),
    CONSTRAINT shipment_exceptions_status_valid CHECK (status IN
        ('OPEN','ACKNOWLEDGED','RESOLVED')),
    -- §19: لا حسم بلا إجراء مسجّل
    CONSTRAINT shipment_exceptions_resolution CHECK (
        status <> 'RESOLVED'
        OR (action_taken IS NOT NULL AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL))
);
CREATE INDEX shipment_exceptions_shipment_idx ON shipment_exceptions(shipment_id);
CREATE INDEX shipment_exceptions_hub_open_idx ON shipment_exceptions(hub_id, occurred_at DESC)
    WHERE status <> 'RESOLVED';

ALTER TABLE documents
    ADD CONSTRAINT documents_exception_fk
    FOREIGN KEY (exception_id) REFERENCES shipment_exceptions(id) ON DELETE CASCADE;

-- ---------------------------------------------------------------------
-- التنبيهات
-- ---------------------------------------------------------------------
CREATE TABLE alerts (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_type        text NOT NULL,
    severity          text NOT NULL,
    title_ar          text NOT NULL,
    body_ar           text NOT NULL,
    shipment_id       uuid REFERENCES shipments(id) ON DELETE CASCADE,
    route_id          uuid REFERENCES routes(id) ON DELETE CASCADE,
    hub_id            uuid REFERENCES hubs(id) ON DELETE CASCADE,
    region_id         uuid REFERENCES regions(id) ON DELETE CASCADE,
    driver_id         uuid REFERENCES drivers(id) ON DELETE SET NULL,
    -- المستخدم المسؤول عن المعالجة
    responsible_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    context           jsonb NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key        text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    acknowledged_by   uuid REFERENCES users(id) ON DELETE SET NULL,
    acknowledged_at   timestamptz,
    resolved_at       timestamptz,
    action_note       text,
    is_test_data      boolean NOT NULL DEFAULT false,
    CONSTRAINT alerts_severity_valid CHECK (severity IN
        ('INFO','LOW','MEDIUM','HIGH','CRITICAL')),
    CONSTRAINT alerts_type_valid CHECK (alert_type IN (
        'PICKUP_WINDOW_APPROACHING','PICKUP_LATE','DELIVERY_LATE','SLA_AT_RISK',
        'SLA_BREACHED','REQUEST_CANCELLED','SAMPLES_NOT_READY','PICKUP_FAILED',
        'DELIVERY_FAILED','TEMPERATURE_BREACH','TRACKING_STALE',
        'PUBLISHED_ROUTE_MODIFIED','NEW_ON_DEMAND_REQUEST','ROUTE_WITHOUT_DRIVER',
        'DRIVER_SHORTAGE','ASSIGNMENT_CONFLICT')),
    -- §24: كل تنبيه مرتبط بشحنة أو رحلة أو مركز
    CONSTRAINT alerts_has_target CHECK (
        shipment_id IS NOT NULL OR route_id IS NOT NULL OR hub_id IS NOT NULL),
    CONSTRAINT alerts_resolution_note CHECK (
        resolved_at IS NULL OR action_note IS NOT NULL)
);
CREATE UNIQUE INDEX alerts_dedupe_uniq ON alerts(dedupe_key)
    WHERE dedupe_key IS NOT NULL AND resolved_at IS NULL;
CREATE INDEX alerts_hub_open_idx ON alerts(hub_id, created_at DESC)
    WHERE resolved_at IS NULL;
CREATE INDEX alerts_shipment_idx ON alerts(shipment_id);
CREATE INDEX alerts_severity_idx ON alerts(severity, created_at DESC)
    WHERE resolved_at IS NULL;

-- ---------------------------------------------------------------------
-- تتبع مواقع السائقين — مقسّم شهريًا
-- ---------------------------------------------------------------------
CREATE TABLE driver_positions (
    id            bigserial,
    driver_id     uuid NOT NULL,
    route_id      uuid,
    lat           double precision NOT NULL,
    lon           double precision NOT NULL,
    speed_kmh     numeric(6,2),
    heading_deg   numeric(6,2),
    accuracy_m    numeric(8,2),
    battery_pct   numeric(5,2),
    recorded_at   timestamptz NOT NULL,
    received_at   timestamptz NOT NULL DEFAULT now(),
    is_test_data  boolean NOT NULL DEFAULT false,
    PRIMARY KEY (id, recorded_at),
    CONSTRAINT driver_positions_lat CHECK (lat BETWEEN -90 AND 90),
    CONSTRAINT driver_positions_lon CHECK (lon BETWEEN -180 AND 180)
) PARTITION BY RANGE (recorded_at);

CREATE INDEX driver_positions_driver_time_idx
    ON driver_positions(driver_id, recorded_at DESC);
CREATE INDEX driver_positions_route_idx ON driver_positions(route_id, recorded_at);

-- دالة إنشاء أقسام شهرية مسبقًا
CREATE OR REPLACE FUNCTION app.ensure_position_partition(p_month date)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    start_date date := date_trunc('month', p_month)::date;
    end_date   date := (date_trunc('month', p_month) + interval '1 month')::date;
    part_name  text := format('driver_positions_%s', to_char(start_date, 'YYYY_MM'));
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = part_name) THEN
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF driver_positions FOR VALUES FROM (%L) TO (%L)',
            part_name, start_date, end_date);
    END IF;
END;
$$;

-- آخر موقع معروف لكل سائق (جدول مادي سريع للخريطة المباشرة)
CREATE TABLE driver_last_position (
    driver_id     uuid PRIMARY KEY REFERENCES drivers(id) ON DELETE CASCADE,
    route_id      uuid REFERENCES routes(id) ON DELETE SET NULL,
    lat           double precision NOT NULL,
    lon           double precision NOT NULL,
    speed_kmh     numeric(6,2),
    heading_deg   numeric(6,2),
    recorded_at   timestamptz NOT NULL,
    received_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- الحساسات والحرارة
-- ---------------------------------------------------------------------
CREATE TABLE sensors (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code           text NOT NULL UNIQUE,
    provider       text NOT NULL DEFAULT 'NONE',
    box_id         uuid REFERENCES boxes(id) ON DELETE SET NULL,
    vehicle_id     uuid REFERENCES vehicles(id) ON DELETE SET NULL,
    is_active      boolean NOT NULL DEFAULT true,
    last_seen_at   timestamptz,
    is_test_data   boolean NOT NULL DEFAULT false,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sensors_binding CHECK (box_id IS NOT NULL OR vehicle_id IS NOT NULL)
);

CREATE TABLE temperature_readings (
    id            bigserial PRIMARY KEY,
    sensor_id     uuid REFERENCES sensors(id) ON DELETE SET NULL,
    box_id        uuid REFERENCES boxes(id) ON DELETE SET NULL,
    shipment_id   uuid REFERENCES shipments(id) ON DELETE CASCADE,
    route_id      uuid REFERENCES routes(id) ON DELETE SET NULL,
    celsius       numeric(6,2) NOT NULL,
    humidity_pct  numeric(5,2),
    recorded_at   timestamptz NOT NULL,
    received_at   timestamptz NOT NULL DEFAULT now(),
    source        text NOT NULL,
    status        text NOT NULL DEFAULT 'IN_RANGE',
    is_test_data  boolean NOT NULL DEFAULT false,
    CONSTRAINT temperature_source_valid CHECK (source IN
        ('SENSOR','GATEWAY','SIMULATION','MANUAL_ADMIN')),
    CONSTRAINT temperature_status_valid CHECK (status IN
        ('IN_RANGE','BREACH_HIGH','BREACH_LOW','NO_SENSOR','STALE'))
);
CREATE INDEX temperature_readings_shipment_idx
    ON temperature_readings(shipment_id, recorded_at DESC);
CREATE INDEX temperature_readings_box_idx ON temperature_readings(box_id, recorded_at DESC);

COMMENT ON COLUMN temperature_readings.source IS
    'SIMULATION تعني بيانات محاكاة اختبارية — تُعرض دائمًا موسومة ولا تُقدَّم كتكامل حقيقي';

CREATE TABLE temperature_breaches (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    shipment_id      uuid REFERENCES shipments(id) ON DELETE CASCADE,
    box_id           uuid REFERENCES boxes(id) ON DELETE SET NULL,
    route_id         uuid REFERENCES routes(id) ON DELETE SET NULL,
    sensor_id        uuid REFERENCES sensors(id) ON DELETE SET NULL,
    started_at       timestamptz NOT NULL,
    ended_at         timestamptz,
    duration_minutes numeric(10,2),
    min_celsius      numeric(6,2),
    max_celsius      numeric(6,2),
    required_min_c   numeric(6,2),
    required_max_c   numeric(6,2),
    breach_kind      text NOT NULL,
    action_taken     text,
    resolved_by      uuid REFERENCES users(id) ON DELETE SET NULL,
    resolved_at      timestamptz,
    is_test_data     boolean NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT temperature_breach_kind_valid CHECK (breach_kind IN ('HIGH','LOW')),
    CONSTRAINT temperature_breach_order CHECK (ended_at IS NULL OR ended_at >= started_at)
);
CREATE INDEX temperature_breaches_shipment_idx ON temperature_breaches(shipment_id);

-- سلسلة الحيازة (§20)
CREATE TABLE custody_transfers (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    shipment_id    uuid NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    from_party     text NOT NULL,
    to_party       text NOT NULL,
    from_entity_id uuid,
    to_entity_id   uuid,
    box_id         uuid REFERENCES boxes(id) ON DELETE SET NULL,
    occurred_at    timestamptz NOT NULL,
    lat            double precision,
    lon            double precision,
    document_id    uuid REFERENCES documents(id) ON DELETE SET NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT custody_party_valid CHECK (
        from_party IN ('FACILITY','DRIVER','HUB','LAB') AND
        to_party   IN ('FACILITY','DRIVER','HUB','LAB'))
);
CREATE INDEX custody_transfers_shipment_idx ON custody_transfers(shipment_id, occurred_at);

-- ---------------------------------------------------------------------
-- ناقل الأحداث الفورية — LISTEN/NOTIFY
-- ---------------------------------------------------------------------
CREATE TABLE system_events (
    id           bigserial PRIMARY KEY,
    topic        text NOT NULL,
    payload      jsonb NOT NULL,
    hub_id       uuid,
    region_id    uuid,
    driver_id    uuid,
    user_id      uuid,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX system_events_topic_time_idx ON system_events(topic, id DESC);
CREATE INDEX system_events_hub_idx ON system_events(hub_id, id DESC);

CREATE OR REPLACE FUNCTION app.publish_system_event()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    message text;
BEGIN
    message := json_build_object(
        'id', NEW.id,
        'topic', NEW.topic,
        'hub_id', NEW.hub_id,
        'region_id', NEW.region_id,
        'driver_id', NEW.driver_id,
        'user_id', NEW.user_id
    )::text;
    -- حد pg_notify هو 8000 بايت؛ نرسل المفاتيح فقط والمستهلك يقرأ الصف
    PERFORM pg_notify('masar_events', message);
    RETURN NEW;
END;
$$;

CREATE TRIGGER system_events_notify AFTER INSERT ON system_events
    FOR EACH ROW EXECUTE FUNCTION app.publish_system_event();

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['sensors'] LOOP
        EXECUTE format(
            'CREATE TRIGGER %I_touch BEFORE UPDATE ON %I
             FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at()', t, t);
    END LOOP;
END $$;
