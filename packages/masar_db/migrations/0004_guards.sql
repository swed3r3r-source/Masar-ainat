-- =====================================================================
-- الترحيل 0004: حراس قواعد العمل داخل قاعدة البيانات
-- كل قاعدة هنا مطبَّقة أيضًا في طبقة الخدمة؛ وجودها هنا يضمن أن أي كتابة
-- مباشرة على القاعدة — من أي أداة — لا تستطيع خرقها.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS btree_gist;

-- ---------------------------------------------------------------------
-- جداول الانتقالات المسموحة (تُزامَن من masar_core.state_machine)
-- ---------------------------------------------------------------------
CREATE TABLE allowed_transitions (
    entity        text NOT NULL,
    from_status   text NOT NULL,
    to_status     text NOT NULL,
    permission    text NOT NULL,
    requires_reason boolean NOT NULL DEFAULT false,
    label_ar      text,
    PRIMARY KEY (entity, from_status, to_status)
);
COMMENT ON TABLE allowed_transitions IS
    'مصدرها الوحيد masar_core/state_machine.py — تُزامن آليًا عند كل ترحيل';

CREATE OR REPLACE FUNCTION app.guard_shipment_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT EXISTS (
            SELECT 1 FROM allowed_transitions
            WHERE entity = 'SHIPMENT'
              AND from_status = OLD.status
              AND to_status = NEW.status
        ) THEN
            RAISE EXCEPTION
                'انتقال حالة غير مسموح للشحنة %: % ← %',
                NEW.reference, OLD.status, NEW.status
                USING ERRCODE = 'P0001';
        END IF;

        -- §21: لا يمكن التسليم قبل الالتقاط
        IF NEW.status IN ('DELIVERED','COMPLETED')
           AND NEW.actual_pickup_at IS NULL
           AND OLD.status <> 'EXCEPTION' THEN
            RAISE EXCEPTION
                'لا يمكن تسجيل التسليم للشحنة % قبل تسجيل الالتقاط', NEW.reference
                USING ERRCODE = 'P0001';
        END IF;

        -- §21: الإلغاء قبل الالتقاط ممنوع بعد تسجيل الالتقاط
        IF NEW.status = 'CANCELLED_BEFORE_PICKUP' AND NEW.actual_pickup_at IS NOT NULL THEN
            RAISE EXCEPTION
                'لا يمكن تسجيل «إلغاء قبل الالتقاط» للشحنة % بعد تسجيل الالتقاط',
                NEW.reference
                USING ERRCODE = 'P0001';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER shipments_guard_transition BEFORE UPDATE ON shipments
    FOR EACH ROW EXECUTE FUNCTION app.guard_shipment_transition();

CREATE OR REPLACE FUNCTION app.guard_route_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    unresolved integer;
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT EXISTS (
            SELECT 1 FROM allowed_transitions
            WHERE entity = 'ROUTE' AND from_status = OLD.status AND to_status = NEW.status
        ) THEN
            RAISE EXCEPTION 'انتقال حالة غير مسموح للرحلة %: % ← %',
                NEW.reference, OLD.status, NEW.status
                USING ERRCODE = 'P0001';
        END IF;

        -- §21: لا يمكن إكمال الرحلة مع وجود شحنة غير محسومة
        IF NEW.status = 'COMPLETED' THEN
            SELECT count(*) INTO unresolved
            FROM shipments s
            WHERE s.route_id = NEW.id
              AND s.status NOT IN ('COMPLETED','DELIVERED','CANCELLED_BEFORE_PICKUP',
                                   'FAILED','REJECTED');
            IF unresolved > 0 THEN
                RAISE EXCEPTION
                    'لا يمكن إكمال الرحلة %: توجد % شحنة غير محسومة',
                    NEW.reference, unresolved
                    USING ERRCODE = 'P0001';
            END IF;
        END IF;

        -- §21: لا يمكن بدء رحلة غير منشورة
        IF NEW.status = 'IN_PROGRESS' AND OLD.status <> 'PUBLISHED' THEN
            RAISE EXCEPTION 'لا يمكن بدء الرحلة % من الحالة %', NEW.reference, OLD.status
                USING ERRCODE = 'P0001';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER routes_guard_transition BEFORE UPDATE ON routes
    FOR EACH ROW EXECUTE FUNCTION app.guard_route_transition();

CREATE OR REPLACE FUNCTION app.guard_plan_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT EXISTS (
        SELECT 1 FROM allowed_transitions
        WHERE entity = 'PLAN' AND from_status = OLD.status AND to_status = NEW.status
    ) THEN
        RAISE EXCEPTION 'انتقال حالة غير مسموح للخطة %: % ← %',
            NEW.reference, OLD.status, NEW.status
            USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER plans_guard_transition BEFORE UPDATE ON plans
    FOR EACH ROW EXECUTE FUNCTION app.guard_plan_transition();

-- ---------------------------------------------------------------------
-- منع تعارض إسناد السائق والمركبة (§16 / HC-04 / HC-06)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.route_time_range(p_start timestamptz, p_end timestamptz)
RETURNS tstzrange LANGUAGE sql IMMUTABLE AS $$
    SELECT tstzrange(
        coalesce(p_start, '-infinity'::timestamptz),
        coalesce(p_end, p_start + interval '1 minute', 'infinity'::timestamptz),
        '[)')
$$;

ALTER TABLE routes ADD COLUMN active_window tstzrange
    GENERATED ALWAYS AS (app.route_time_range(planned_start_at, planned_end_at)) STORED;

-- سائق واحد لا يمكن أن يكون في رحلتين متداخلتين زمنيًا
ALTER TABLE routes ADD CONSTRAINT routes_driver_no_overlap
    EXCLUDE USING gist (
        driver_id WITH =,
        active_window WITH &&
    ) WHERE (driver_id IS NOT NULL AND status IN ('ASSIGNED','PUBLISHED','IN_PROGRESS'));

-- مركبة واحدة لا يمكن أن تكون في رحلتين متداخلتين زمنيًا
ALTER TABLE routes ADD CONSTRAINT routes_vehicle_no_overlap
    EXCLUDE USING gist (
        vehicle_id WITH =,
        active_window WITH &&
    ) WHERE (vehicle_id IS NOT NULL AND status IN ('ASSIGNED','PUBLISHED','IN_PROGRESS'));

-- ---------------------------------------------------------------------
-- HC-11: التقاطان من نفس الجهة في اليوم — تسليم الأول قبل تنفيذ الثاني
-- يُفرض في المحرك؛ وهنا فحص تحقق يُستدعى قبل النشر
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.verify_route_feasibility(p_route_id uuid)
RETURNS TABLE (rule_code text, detail_ar text) LANGUAGE plpgsql STABLE AS $$
BEGIN
    -- HC-01: الالتقاط يسبق التسليم لنفس الشحنة
    RETURN QUERY
    SELECT 'HC-01',
           format('الشحنة %s: محطة التسليم (%s) قبل محطة الالتقاط (%s)',
                  s.reference, d.sequence, p.sequence)
    FROM route_stops p
    JOIN route_stops d ON d.route_id = p.route_id AND d.shipment_id = p.shipment_id
                      AND d.kind = 'DELIVERY'
    JOIN shipments s ON s.id = p.shipment_id
    WHERE p.route_id = p_route_id AND p.kind = 'PICKUP' AND d.sequence < p.sequence;

    -- HC-02: الالتزام بنافذة الالتقاط
    RETURN QUERY
    SELECT 'HC-02',
           format('الشحنة %s: الوصول المخطط %s خارج نافذة الالتقاط [%s .. %s]',
                  s.reference,
                  to_char(st.planned_arrival_at, 'YYYY-MM-DD HH24:MI'),
                  to_char(st.window_from, 'HH24:MI'), to_char(st.window_to, 'HH24:MI'))
    FROM route_stops st
    JOIN shipments s ON s.id = st.shipment_id
    WHERE st.route_id = p_route_id AND st.kind = 'PICKUP'
      AND st.window_to IS NOT NULL
      AND st.planned_service_start IS NOT NULL
      AND st.planned_service_start > st.window_to;

    -- HC-03: الالتزام بـ SLA التسليم
    RETURN QUERY
    SELECT 'HC-03',
           format('الشحنة %s: التسليم المخطط %s بعد الموعد النهائي %s',
                  s.reference,
                  to_char(st.planned_departure_at, 'YYYY-MM-DD HH24:MI'),
                  to_char(s.sla_deadline, 'YYYY-MM-DD HH24:MI'))
    FROM route_stops st
    JOIN shipments s ON s.id = st.shipment_id
    WHERE st.route_id = p_route_id AND st.kind = 'DELIVERY'
      AND st.planned_departure_at IS NOT NULL
      AND st.planned_departure_at > s.sla_deadline;

    -- HC-11: تسليم الالتقاط الأول قبل الالتقاط الثاني لنفس الجهة
    RETURN QUERY
    SELECT 'HC-11',
           format('الجهة %s: الالتقاط الثاني (محطة %s) قبل تسليم الالتقاط الأول (محطة %s)',
                  f.name_ar, p2.sequence, d1.sequence)
    FROM route_stops p1
    JOIN route_stops p2 ON p2.route_id = p1.route_id AND p2.facility_id = p1.facility_id
                       AND p2.kind = 'PICKUP' AND p2.sequence > p1.sequence
    JOIN route_stops d1 ON d1.route_id = p1.route_id AND d1.shipment_id = p1.shipment_id
                       AND d1.kind = 'DELIVERY'
    JOIN facilities f ON f.id = p1.facility_id
    WHERE p1.route_id = p_route_id AND p1.kind = 'PICKUP'
      AND d1.sequence > p2.sequence;
END;
$$;

COMMENT ON FUNCTION app.verify_route_feasibility IS
    'فحص جدوى مستقل عن المحرك — يُشغَّل قبل النشر ولا يُسمح بالنشر مع وجود نتائج';

-- ---------------------------------------------------------------------
-- HC-18: بوابة النشر — لا نشر لرحلة تخرق قيدًا صلبًا
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.guard_route_publish()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    violation record;
    violations text := '';
BEGIN
    IF NEW.status = 'PUBLISHED' AND OLD.status IS DISTINCT FROM 'PUBLISHED' THEN
        FOR violation IN SELECT * FROM app.verify_route_feasibility(NEW.id) LOOP
            violations := violations || violation.rule_code || ': ' || violation.detail_ar || E'\n';
        END LOOP;
        IF violations <> '' THEN
            RAISE EXCEPTION
                'لا يمكن نشر الرحلة % لخرقها قيودًا صلبة:%s%',
                NEW.reference, E'\n', violations
                USING ERRCODE = 'P0001';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER routes_guard_publish BEFORE UPDATE ON routes
    FOR EACH ROW EXECUTE FUNCTION app.guard_route_publish();

-- ---------------------------------------------------------------------
-- §31: منع اختلاط بيانات الاختبار بالإنتاج
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.guard_no_test_data_in_production()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.is_test_data
       AND coalesce(current_setting('masar.environment', true), 'development') = 'production' THEN
        RAISE EXCEPTION
            'محاولة إدخال بيانات موسومة كتجريبية في بيئة الإنتاج (جدول %)', TG_TABLE_NAME
            USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'regions','cities','hubs','facilities','users','drivers','vehicles','boxes',
        'shipments','plans','routes','schedule_imports','alerts','documents',
        'shipment_exceptions','temperature_readings','driver_positions','sensors'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER %I_no_test_data BEFORE INSERT OR UPDATE ON %I
             FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production()', t, t);
    END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- §27: تسجيل تلقائي لتغيّر حالة الشحنة في السجل التاريخي
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.record_shipment_status_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO shipment_status_history (
            shipment_id, from_status, to_status, changed_by, actor_role, reason, source
        ) VALUES (
            NEW.id, OLD.status, NEW.status,
            app.current_user_id(), app.current_role_key(),
            coalesce(nullif(current_setting('masar.change_reason', true), ''), NULL),
            coalesce(nullif(current_setting('masar.change_source', true), ''), 'API')
        );
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER shipments_record_status AFTER UPDATE ON shipments
    FOR EACH ROW EXECUTE FUNCTION app.record_shipment_status_change();

-- ---------------------------------------------------------------------
-- §28: منع الحذف النهائي للبيانات التشغيلية دون صلاحية خاصة
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.guard_operational_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF coalesce(current_setting('masar.allow_hard_delete', true), 'off') <> 'on' THEN
        RAISE EXCEPTION
            'الحذف النهائي من % ممنوع — يتطلب صلاحية data.hard_delete', TG_TABLE_NAME
            USING ERRCODE = '42501';
    END IF;
    RETURN OLD;
END;
$$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'shipments','routes','plans','shipment_events','documents',
        'shipment_exceptions','temperature_readings','custody_transfers'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER %I_guard_delete BEFORE DELETE ON %I
             FOR EACH ROW EXECUTE FUNCTION app.guard_operational_delete()', t, t);
    END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- تحديث آخر موقع للسائق تلقائيًا
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.upsert_driver_last_position()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO driver_last_position (
        driver_id, route_id, lat, lon, speed_kmh, heading_deg, recorded_at, received_at)
    VALUES (NEW.driver_id, NEW.route_id, NEW.lat, NEW.lon,
            NEW.speed_kmh, NEW.heading_deg, NEW.recorded_at, NEW.received_at)
    ON CONFLICT (driver_id) DO UPDATE SET
        route_id = EXCLUDED.route_id,
        lat = EXCLUDED.lat, lon = EXCLUDED.lon,
        speed_kmh = EXCLUDED.speed_kmh, heading_deg = EXCLUDED.heading_deg,
        recorded_at = EXCLUDED.recorded_at, received_at = EXCLUDED.received_at
    WHERE driver_last_position.recorded_at < EXCLUDED.recorded_at;
    RETURN NEW;
END;
$$;

CREATE TRIGGER driver_positions_last AFTER INSERT ON driver_positions
    FOR EACH ROW EXECUTE FUNCTION app.upsert_driver_last_position();
