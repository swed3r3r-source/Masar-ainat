-- =====================================================================
-- الترحيل 0002: الاستيراد، الشحنات، الخطط، الرحلات، المحطات، التحذيرات
-- =====================================================================

-- ---------------------------------------------------------------------
-- رفع الجدول الأسبوعي
-- ---------------------------------------------------------------------
CREATE TABLE schedule_imports (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reference         text NOT NULL UNIQUE,
    original_filename text NOT NULL,
    storage_key       text NOT NULL,
    content_type      text,
    byte_size         bigint,
    sha256            text,
    status            text NOT NULL DEFAULT 'UPLOADED',
    period_start      date,
    period_end        date,
    column_mapping    jsonb NOT NULL DEFAULT '{}'::jsonb,
    total_rows        integer NOT NULL DEFAULT 0,
    valid_rows        integer NOT NULL DEFAULT 0,
    invalid_rows      integer NOT NULL DEFAULT 0,
    duplicate_rows    integer NOT NULL DEFAULT 0,
    summary           jsonb NOT NULL DEFAULT '{}'::jsonb,
    uploaded_by       uuid REFERENCES users(id) ON DELETE SET NULL,
    committed_by      uuid REFERENCES users(id) ON DELETE SET NULL,
    committed_at      timestamptz,
    is_test_data      boolean NOT NULL DEFAULT false,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT schedule_imports_status_valid CHECK (status IN
        ('UPLOADED','MAPPING','VALIDATING','VALIDATED','PARTIALLY_VALID','REJECTED','COMMITTED')),
    CONSTRAINT schedule_imports_period_order CHECK (
        period_end IS NULL OR period_start IS NULL OR period_end >= period_start)
);

CREATE TABLE import_rows (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    import_id     uuid NOT NULL REFERENCES schedule_imports(id) ON DELETE CASCADE,
    row_number    integer NOT NULL,
    raw           jsonb NOT NULL,
    normalized    jsonb,
    is_valid      boolean NOT NULL DEFAULT false,
    is_excluded   boolean NOT NULL DEFAULT false,
    errors        jsonb NOT NULL DEFAULT '[]'::jsonb,
    warnings      jsonb NOT NULL DEFAULT '[]'::jsonb,
    dedupe_key    text,
    shipment_id   uuid,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT import_rows_uniq UNIQUE (import_id, row_number)
);
CREATE INDEX import_rows_import_idx ON import_rows(import_id, is_valid);
CREATE INDEX import_rows_dedupe_idx ON import_rows(import_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL;

-- ---------------------------------------------------------------------
-- الشحنات — الكيان المحوري
-- ---------------------------------------------------------------------
CREATE TABLE shipments (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reference               text NOT NULL UNIQUE,
    external_reference      text,
    request_kind            text NOT NULL DEFAULT 'SCHEDULED',
    service_type            text NOT NULL DEFAULT 'ROUTINE',
    status                  text NOT NULL DEFAULT 'DRAFT',

    -- النطاق التنظيمي
    region_id               uuid NOT NULL REFERENCES regions(id) ON DELETE RESTRICT,
    city_id                 uuid NOT NULL REFERENCES cities(id) ON DELETE RESTRICT,
    hub_id                  uuid NOT NULL REFERENCES hubs(id) ON DELETE RESTRICT,

    -- الالتقاط
    pickup_facility_id      uuid NOT NULL REFERENCES facilities(id) ON DELETE RESTRICT,
    pickup_facility_type    text NOT NULL,
    pickup_contact_name     text,
    pickup_contact_phone    text,
    pickup_address          text,
    pickup_lat              double precision NOT NULL,
    pickup_lon              double precision NOT NULL,
    pickup_window_from      timestamptz NOT NULL,
    pickup_window_to        timestamptz NOT NULL,
    pickup_service_minutes  integer NOT NULL DEFAULT 10,

    -- التسليم
    dropoff_facility_id     uuid NOT NULL REFERENCES facilities(id) ON DELETE RESTRICT,
    dropoff_facility_type   text NOT NULL,
    dropoff_contact_name    text,
    dropoff_contact_phone   text,
    dropoff_address         text,
    dropoff_lat             double precision NOT NULL,
    dropoff_lon             double precision NOT NULL,
    sla_deadline            timestamptz NOT NULL,
    dropoff_service_minutes integer NOT NULL DEFAULT 10,

    -- المحتوى
    piece_count             integer NOT NULL DEFAULT 1,
    sample_types            text[] NOT NULL DEFAULT '{}',
    temperature_mode        text NOT NULL DEFAULT 'AMBIENT',
    temperature_min_c       numeric(5,2),
    temperature_max_c       numeric(5,2),

    -- التاريخ التشغيلي
    service_date            date NOT NULL,

    -- الإسناد
    route_id                uuid,
    driver_id               uuid REFERENCES drivers(id) ON DELETE SET NULL,
    vehicle_id              uuid REFERENCES vehicles(id) ON DELETE SET NULL,
    box_id                  uuid REFERENCES boxes(id) ON DELETE SET NULL,

    -- الأوقات المخططة (منفصلة تمامًا عن الفعلية — §28)
    planned_pickup_arrival    timestamptz,
    planned_pickup_at         timestamptz,
    planned_dropoff_arrival   timestamptz,
    planned_dropoff_at        timestamptz,

    -- الأوقات الفعلية
    actual_pickup_arrival     timestamptz,
    actual_pickup_at          timestamptz,
    actual_dropoff_arrival    timestamptz,
    actual_dropoff_at         timestamptz,

    -- النتيجة
    sla_breached            boolean NOT NULL DEFAULT false,
    pickup_window_breached  boolean NOT NULL DEFAULT false,
    delay_minutes           integer,
    failure_reason          text,
    cancel_reason           text,
    unplannable_reason      text,
    unplannable_detail      text,
    delivery_obligation_open boolean NOT NULL DEFAULT false,

    -- المصدر
    import_id               uuid REFERENCES schedule_imports(id) ON DELETE SET NULL,
    import_row_number       integer,
    requested_by            uuid REFERENCES users(id) ON DELETE SET NULL,
    requester_facility_id   uuid REFERENCES facilities(id) ON DELETE SET NULL,
    approved_by             uuid REFERENCES users(id) ON DELETE SET NULL,
    approved_at             timestamptz,
    rejection_reason        text,

    notes                   text,
    dedupe_key              text,
    is_test_data            boolean NOT NULL DEFAULT false,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT shipments_kind_valid CHECK (request_kind IN ('SCHEDULED','ON_DEMAND')),
    CONSTRAINT shipments_service_valid CHECK (service_type IN
        ('ROUTINE','URGENT','STAT','RETURN')),
    CONSTRAINT shipments_status_valid CHECK (status IN (
        'DRAFT','VALIDATED','PENDING_APPROVAL','REJECTED','PENDING_ASSIGNMENT',
        'PLANNED','ASSIGNED','PUBLISHED','IN_PROGRESS','ARRIVED_PICKUP','PICKED_UP',
        'ARRIVED_DELIVERY','DELIVERED','COMPLETED','CANCELLED_BEFORE_PICKUP',
        'EXCEPTION','FAILED','UNPLANNABLE')),
    CONSTRAINT shipments_temp_mode_valid CHECK (temperature_mode IN
        ('AMBIENT','CHILLED','FROZEN','DEEP_FROZEN','CONTROLLED')),
    CONSTRAINT shipments_window_order CHECK (pickup_window_to >= pickup_window_from),
    CONSTRAINT shipments_sla_after_window CHECK (sla_deadline > pickup_window_from),
    CONSTRAINT shipments_pieces_positive CHECK (piece_count > 0),
    CONSTRAINT shipments_pickup_lat CHECK (pickup_lat BETWEEN -90 AND 90),
    CONSTRAINT shipments_pickup_lon CHECK (pickup_lon BETWEEN -180 AND 180),
    CONSTRAINT shipments_dropoff_lat CHECK (dropoff_lat BETWEEN -90 AND 90),
    CONSTRAINT shipments_dropoff_lon CHECK (dropoff_lon BETWEEN -180 AND 180),
    CONSTRAINT shipments_distinct_endpoints CHECK (pickup_facility_id <> dropoff_facility_id),
    -- §28: الوقت الفعلي للتسليم لا يسبق الالتقاط أبدًا
    CONSTRAINT shipments_actual_order CHECK (
        actual_dropoff_at IS NULL OR actual_pickup_at IS NULL
        OR actual_dropoff_at >= actual_pickup_at),
    -- §12/HC-19: تعذر التخطيط يستوجب سببًا مسجلًا
    CONSTRAINT shipments_unplannable_reason CHECK (
        status <> 'UNPLANNABLE' OR unplannable_reason IS NOT NULL),
    CONSTRAINT shipments_cancel_reason CHECK (
        status <> 'CANCELLED_BEFORE_PICKUP' OR cancel_reason IS NOT NULL),
    CONSTRAINT shipments_reject_reason CHECK (
        status <> 'REJECTED' OR rejection_reason IS NOT NULL)
);

CREATE UNIQUE INDEX shipments_dedupe_uniq
    ON shipments(dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX shipments_hub_date_idx    ON shipments(hub_id, service_date, status);
CREATE INDEX shipments_route_idx       ON shipments(route_id) WHERE route_id IS NOT NULL;
CREATE INDEX shipments_driver_idx      ON shipments(driver_id, service_date)
    WHERE driver_id IS NOT NULL;
CREATE INDEX shipments_status_idx      ON shipments(status, service_date);
CREATE INDEX shipments_requester_idx   ON shipments(requester_facility_id)
    WHERE requester_facility_id IS NOT NULL;
CREATE INDEX shipments_region_date_idx ON shipments(region_id, service_date);
CREATE INDEX shipments_import_idx      ON shipments(import_id) WHERE import_id IS NOT NULL;
CREATE INDEX shipments_open_obligation_idx ON shipments(hub_id, service_date)
    WHERE delivery_obligation_open;

COMMENT ON COLUMN shipments.dedupe_key IS
    'مفتاح منع التكرار: جهة الالتقاط + جهة التسليم + التاريخ + نافذة الالتقاط + المرجع الخارجي';

-- ---------------------------------------------------------------------
-- الخطط
-- ---------------------------------------------------------------------
CREATE TABLE plans (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reference         text NOT NULL UNIQUE,
    name_ar           text NOT NULL,
    status            text NOT NULL DEFAULT 'DRAFT',
    scope_type        text NOT NULL DEFAULT 'KINGDOM',
    scope_id          uuid,
    period_start      date NOT NULL,
    period_end        date NOT NULL,
    import_id         uuid REFERENCES schedule_imports(id) ON DELETE SET NULL,
    baseline_plan_id  uuid REFERENCES plans(id) ON DELETE SET NULL,
    parameters        jsonb NOT NULL DEFAULT '{}'::jsonb,
    settings_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    metrics           jsonb NOT NULL DEFAULT '{}'::jsonb,
    engine_name       text,
    engine_version    text,
    routing_provider  text,
    routing_estimated boolean NOT NULL DEFAULT false,
    solve_ms          integer,
    objective_trace   jsonb NOT NULL DEFAULT '[]'::jsonb,
    failure_reason    text,
    created_by        uuid REFERENCES users(id) ON DELETE SET NULL,
    approved_by       uuid REFERENCES users(id) ON DELETE SET NULL,
    approved_at       timestamptz,
    dispatched_at     timestamptz,
    is_test_data      boolean NOT NULL DEFAULT false,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plans_status_valid CHECK (status IN
        ('DRAFT','OPTIMIZING','OPTIMIZED','APPROVED','DISPATCHED','SUPERSEDED','FAILED')),
    CONSTRAINT plans_scope_valid CHECK (scope_type IN ('KINGDOM','REGION','CITY','HUB')),
    CONSTRAINT plans_period_order CHECK (period_end >= period_start)
);
CREATE INDEX plans_status_idx ON plans(status, period_start DESC);

COMMENT ON COLUMN plans.routing_estimated IS
    'true إذا استُخدم مزوّد مسافات تقديري — يُعرض كتحذير ويمنع الاعتماد الصامت';

-- يوم واحد داخل الخطة — النشر يتم لكل يوم على حدة (§17)
CREATE TABLE plan_days (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id        uuid NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    hub_id         uuid NOT NULL REFERENCES hubs(id) ON DELETE CASCADE,
    service_date   date NOT NULL,
    is_published   boolean NOT NULL DEFAULT false,
    published_at   timestamptz,
    published_by   uuid REFERENCES users(id) ON DELETE SET NULL,
    metrics        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_days_uniq UNIQUE (plan_id, hub_id, service_date),
    CONSTRAINT plan_days_publish_meta CHECK (
        (is_published AND published_at IS NOT NULL) OR NOT is_published)
);
CREATE INDEX plan_days_hub_date_idx ON plan_days(hub_id, service_date);

-- ---------------------------------------------------------------------
-- الرحلات
-- ---------------------------------------------------------------------
CREATE TABLE routes (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reference           text NOT NULL UNIQUE,
    plan_id             uuid REFERENCES plans(id) ON DELETE CASCADE,
    plan_day_id         uuid REFERENCES plan_days(id) ON DELETE CASCADE,
    hub_id              uuid NOT NULL REFERENCES hubs(id) ON DELETE RESTRICT,
    region_id           uuid NOT NULL REFERENCES regions(id) ON DELETE RESTRICT,
    service_date        date NOT NULL,
    status              text NOT NULL DEFAULT 'DRAFT',
    sequence_in_day     integer NOT NULL DEFAULT 1,

    driver_id           uuid REFERENCES drivers(id) ON DELETE SET NULL,
    vehicle_id          uuid REFERENCES vehicles(id) ON DELETE SET NULL,
    box_id              uuid REFERENCES boxes(id) ON DELETE SET NULL,

    -- نقطة البداية: المركز أو موقع نهاية الرحلة السابقة (HC-10)
    start_lat           double precision NOT NULL,
    start_lon           double precision NOT NULL,
    start_node_kind     text NOT NULL DEFAULT 'HUB',
    previous_route_id   uuid REFERENCES routes(id) ON DELETE SET NULL,

    planned_start_at    timestamptz,
    planned_end_at      timestamptz,
    actual_start_at     timestamptz,
    actual_end_at       timestamptz,
    end_lat             double precision,
    end_lon             double precision,

    distance_km         numeric(10,3) NOT NULL DEFAULT 0,
    drive_minutes       numeric(10,2) NOT NULL DEFAULT 0,
    service_minutes     numeric(10,2) NOT NULL DEFAULT 0,
    wait_minutes        numeric(10,2) NOT NULL DEFAULT 0,
    working_minutes     numeric(10,2) NOT NULL DEFAULT 0,
    estimated_cost      numeric(12,2) NOT NULL DEFAULT 0,

    shipment_count      integer NOT NULL DEFAULT 0,
    pickup_count        integer NOT NULL DEFAULT 0,
    delivery_count      integer NOT NULL DEFAULT 0,

    is_long_haul        boolean NOT NULL DEFAULT false,
    max_hub_distance_km numeric(10,3) NOT NULL DEFAULT 0,
    facility_classes    text[] NOT NULL DEFAULT '{}',
    mixing_exemption_used boolean NOT NULL DEFAULT false,

    assigned_by         uuid REFERENCES users(id) ON DELETE SET NULL,
    assigned_at         timestamptz,
    published_by        uuid REFERENCES users(id) ON DELETE SET NULL,
    published_at        timestamptz,
    cancel_reason       text,
    is_test_data        boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT routes_status_valid CHECK (status IN
        ('DRAFT','PLANNED','ASSIGNED','PUBLISHED','IN_PROGRESS','COMPLETED','CANCELLED')),
    CONSTRAINT routes_start_node_valid CHECK (start_node_kind IN
        ('HUB','PREVIOUS_ROUTE_END','DRIVER_CURRENT_POSITION')),
    CONSTRAINT routes_time_order CHECK (
        planned_end_at IS NULL OR planned_start_at IS NULL
        OR planned_end_at >= planned_start_at),
    CONSTRAINT routes_actual_time_order CHECK (
        actual_end_at IS NULL OR actual_start_at IS NULL
        OR actual_end_at >= actual_start_at),
    -- HC-20 / §16: لا نشر بلا سائق
    CONSTRAINT routes_published_needs_driver CHECK (
        status NOT IN ('PUBLISHED','IN_PROGRESS','COMPLETED') OR driver_id IS NOT NULL),
    CONSTRAINT routes_assigned_meta CHECK (
        driver_id IS NULL OR assigned_at IS NOT NULL)
);
CREATE INDEX routes_hub_date_idx    ON routes(hub_id, service_date, status);
CREATE INDEX routes_driver_date_idx ON routes(driver_id, service_date)
    WHERE driver_id IS NOT NULL;
CREATE INDEX routes_plan_idx        ON routes(plan_id);
CREATE INDEX routes_plan_day_idx    ON routes(plan_day_id);

ALTER TABLE shipments
    ADD CONSTRAINT shipments_route_fk
    FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------
-- محطات الرحلة
-- ---------------------------------------------------------------------
CREATE TABLE route_stops (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    route_id              uuid NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
    sequence              integer NOT NULL,
    kind                  text NOT NULL,
    facility_id           uuid REFERENCES facilities(id) ON DELETE RESTRICT,
    hub_id                uuid REFERENCES hubs(id) ON DELETE RESTRICT,
    shipment_id           uuid REFERENCES shipments(id) ON DELETE CASCADE,
    lat                   double precision NOT NULL,
    lon                   double precision NOT NULL,
    label_ar              text NOT NULL,

    planned_arrival_at    timestamptz,
    planned_service_start timestamptz,
    planned_departure_at  timestamptz,
    window_from           timestamptz,
    window_to             timestamptz,
    service_minutes       numeric(8,2) NOT NULL DEFAULT 0,
    wait_minutes          numeric(8,2) NOT NULL DEFAULT 0,
    leg_distance_km       numeric(10,3) NOT NULL DEFAULT 0,
    leg_minutes           numeric(10,2) NOT NULL DEFAULT 0,
    leg_is_estimated      boolean NOT NULL DEFAULT false,

    status                text NOT NULL DEFAULT 'PENDING',
    actual_arrival_at     timestamptz,
    actual_completed_at   timestamptz,
    actual_lat            double precision,
    actual_lon            double precision,

    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT route_stops_kind_valid CHECK (kind IN
        ('HUB_START','PICKUP','DELIVERY','HUB_END')),
    CONSTRAINT route_stops_status_valid CHECK (status IN
        ('PENDING','ARRIVED','DONE','SKIPPED','FAILED')),
    CONSTRAINT route_stops_seq_uniq UNIQUE (route_id, sequence),
    CONSTRAINT route_stops_shipment_required CHECK (
        (kind IN ('PICKUP','DELIVERY') AND shipment_id IS NOT NULL)
        OR kind IN ('HUB_START','HUB_END')),
    CONSTRAINT route_stops_actual_order CHECK (
        actual_completed_at IS NULL OR actual_arrival_at IS NULL
        OR actual_completed_at >= actual_arrival_at)
);
CREATE INDEX route_stops_route_idx    ON route_stops(route_id, sequence);
CREATE INDEX route_stops_shipment_idx ON route_stops(shipment_id)
    WHERE shipment_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- تحذيرات الخطة — لا تحذير بلا سبب وجهة متأثرة وإجراء مقترح (§22)
-- ---------------------------------------------------------------------
CREATE TABLE plan_warnings (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id            uuid NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    route_id           uuid REFERENCES routes(id) ON DELETE CASCADE,
    shipment_id        uuid REFERENCES shipments(id) ON DELETE CASCADE,
    hub_id             uuid REFERENCES hubs(id) ON DELETE CASCADE,
    warning_type       text NOT NULL,
    severity           text NOT NULL DEFAULT 'MEDIUM',
    reason_ar          text NOT NULL,
    affected_entity_ar text NOT NULL,
    suggested_action_ar text NOT NULL,
    occurred_at        timestamptz NOT NULL DEFAULT now(),
    context            jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT plan_warnings_severity_valid CHECK (severity IN
        ('INFO','LOW','MEDIUM','HIGH','CRITICAL')),
    -- §34: لا تحذير دون تفاصيل
    CONSTRAINT plan_warnings_has_detail CHECK (
        length(btrim(reason_ar)) > 0
        AND length(btrim(affected_entity_ar)) > 0
        AND length(btrim(suggested_action_ar)) > 0),
    -- التحذير يجب أن يرتبط برحلة أو شحنة (§2)
    CONSTRAINT plan_warnings_has_target CHECK (
        route_id IS NOT NULL OR shipment_id IS NOT NULL OR hub_id IS NOT NULL)
);
CREATE INDEX plan_warnings_plan_idx ON plan_warnings(plan_id, severity);
CREATE INDEX plan_warnings_route_idx ON plan_warnings(route_id);
CREATE INDEX plan_warnings_shipment_idx ON plan_warnings(shipment_id);

-- ---------------------------------------------------------------------
-- تقدير السائقين (§15)
-- ---------------------------------------------------------------------
CREATE TABLE driver_estimations (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id               uuid NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    hub_id                uuid NOT NULL REFERENCES hubs(id) ON DELETE CASCADE,
    service_date          date NOT NULL,
    theoretical_minimum   integer NOT NULL,
    recommended           integer NOT NULL,
    available             integer NOT NULL,
    used                  integer NOT NULL,
    gap                   integer NOT NULL,
    workload_minutes      numeric(12,2) NOT NULL DEFAULT 0,
    justification         jsonb NOT NULL DEFAULT '[]'::jsonb,
    sla_impact            jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT driver_estimations_uniq UNIQUE (plan_id, hub_id, service_date)
);

-- ---------------------------------------------------------------------
-- سجل تعديلات الرحلات المنشورة (§17)
-- ---------------------------------------------------------------------
CREATE TABLE route_revisions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    route_id        uuid NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
    revision_number integer NOT NULL,
    changed_by      uuid REFERENCES users(id) ON DELETE SET NULL,
    changed_at      timestamptz NOT NULL DEFAULT now(),
    reason          text NOT NULL,
    change_kind     text NOT NULL,
    before_snapshot jsonb NOT NULL,
    after_snapshot  jsonb NOT NULL,
    diff_summary    jsonb NOT NULL DEFAULT '{}'::jsonb,
    notified_driver boolean NOT NULL DEFAULT false,
    CONSTRAINT route_revisions_uniq UNIQUE (route_id, revision_number),
    CONSTRAINT route_revisions_reason_present CHECK (length(btrim(reason)) >= 3),
    CONSTRAINT route_revisions_kind_valid CHECK (change_kind IN
        ('ADD_STOP','REMOVE_STOP','REORDER','REASSIGN_DRIVER','REASSIGN_VEHICLE',
         'RESCHEDULE','CANCEL','OTHER'))
);
CREATE INDEX route_revisions_route_idx ON route_revisions(route_id, revision_number DESC);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'schedule_imports','shipments','plans','plan_days','routes','route_stops'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER %I_touch BEFORE UPDATE ON %I
             FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at()', t, t);
    END LOOP;
END $$;
