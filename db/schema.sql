--
-- «مسار عينات» — لقطة مخطط قاعدة البيانات (بنية فقط، بلا أي بيانات)
--
-- ⚠ هذا الملف **مرجعي للاطلاع والمراجعة**، وليس مصدر الحقيقة.
--   مصدر الحقيقة هو ترحيلات packages/masar_db/migrations/*.sql بالترتيب.
--   لبناء القاعدة استخدم:  python3 -m masar_db.migrate up
--   تطبيق هذا الملف مباشرة يتجاوز جدول schema_migrations فتفقد تتبّع الإصدار.
--
-- ما يحويه: 51 جدولًا · 74 سياسة أمن صفوف (RLS) ·
--           28 دالة · 54 محفّز.
-- لا يحوي: أي صف بيانات، ولا كلمات مرور، ولا مفاتيح، ولا أسماء أدوار محلية.
--
-- وُلّد في 2026-08-27 من قاعدة تطوير مبنية بالترحيلات نفسها.
--

--
-- PostgreSQL database dump
--

\restrict KROpmAeDoxA7uSjmrdw1dQvf4dm1x9Z9LRaeMexfrn9OkJWbsu3ndCorm6Zq1Po




SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: app; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA app;


--
-- Name: SCHEMA app; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA app IS 'دوال مساعدة لسياسات RLS وقواعد العمل';


--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

-- *not* creating schema, since initdb creates it


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS '';


--
-- Name: btree_gist; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;


--
-- Name: EXTENSION btree_gist; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION btree_gist IS 'support for indexing common datatypes in GiST';


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: audit_log_is_append_only(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.audit_log_is_append_only() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'سجل التدقيق إلحاقي فقط: لا يُسمح بـ % عليه', TG_OP
        USING ERRCODE = 'P0001';
END;
$$;


--
-- Name: can_see_hub(uuid); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.can_see_hub(p_hub_id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT app.is_global_scope()
        OR (p_hub_id IS NOT NULL AND p_hub_id = ANY (app.current_hub_ids()))
$$;


--
-- Name: current_driver_id(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.current_driver_id() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$
    SELECT nullif(current_setting('masar.driver_id', true), '')::uuid
$$;


--
-- Name: current_facility_id(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.current_facility_id() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$
    SELECT nullif(current_setting('masar.facility_id', true), '')::uuid
$$;


--
-- Name: current_hub_ids(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.current_hub_ids() RETURNS uuid[]
    LANGUAGE sql STABLE
    AS $$
    SELECT CASE
        WHEN coalesce(current_setting('masar.hub_ids', true), '') = '' THEN ARRAY[]::uuid[]
        ELSE string_to_array(current_setting('masar.hub_ids', true), ',')::uuid[]
    END
$$;


--
-- Name: current_region_ids(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.current_region_ids() RETURNS uuid[]
    LANGUAGE sql STABLE
    AS $$
    SELECT CASE
        WHEN coalesce(current_setting('masar.region_ids', true), '') = '' THEN ARRAY[]::uuid[]
        ELSE string_to_array(current_setting('masar.region_ids', true), ',')::uuid[]
    END
$$;


--
-- Name: current_role_key(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.current_role_key() RETURNS text
    LANGUAGE sql STABLE
    AS $$
    SELECT coalesce(nullif(current_setting('masar.role', true), ''), 'ANONYMOUS')
$$;


--
-- Name: current_user_id(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.current_user_id() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$
    SELECT nullif(current_setting('masar.user_id', true), '')::uuid
$$;


--
-- Name: ensure_position_partition(date); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.ensure_position_partition(p_month date) RETURNS void
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: guard_no_test_data_in_production(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.guard_no_test_data_in_production() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: guard_operational_delete(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.guard_operational_delete() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF coalesce(current_setting('masar.allow_hard_delete', true), 'off') <> 'on' THEN
        RAISE EXCEPTION
            'الحذف النهائي من % ممنوع — يتطلب صلاحية data.hard_delete', TG_TABLE_NAME
            USING ERRCODE = '42501';
    END IF;
    RETURN OLD;
END;
$$;


--
-- Name: guard_plan_transition(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.guard_plan_transition() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: guard_route_publish(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.guard_route_publish() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: guard_route_transition(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.guard_route_transition() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: guard_shipment_transition(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.guard_shipment_transition() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: hub_in_scope(uuid); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.hub_in_scope(p_hub_id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT app.is_global_scope() OR p_hub_id = ANY (app.current_hub_ids())
$$;


--
-- Name: is_admin(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.is_admin() RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT app.current_role_key() = 'ADMIN'
$$;


--
-- Name: is_authenticated(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.is_authenticated() RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT app.current_user_id() IS NOT NULL
$$;


--
-- Name: is_driver_role(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.is_driver_role() RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT app.current_role_key() = 'DRIVER'
$$;


--
-- Name: is_external_role(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.is_external_role() RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT app.current_role_key() IN ('EXTERNAL_REQUESTER','INTEGRATION')
$$;


--
-- Name: is_global_scope(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.is_global_scope() RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT app.current_role_key() IN
        ('ADMIN', 'CENTRAL_PLANNER', 'CONTROL_TOWER', 'AUDITOR')
$$;


--
-- Name: publish_system_event(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.publish_system_event() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: record_shipment_status_change(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.record_shipment_status_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: route_time_range(timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.route_time_range(p_start timestamp with time zone, p_end timestamp with time zone) RETURNS tstzrange
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT tstzrange(
        coalesce(p_start, '-infinity'::timestamptz),
        coalesce(p_end, p_start + interval '1 minute', 'infinity'::timestamptz),
        '[)')
$$;


--
-- Name: setting_text(text); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.setting_text(p_key text) RETURNS text
    LANGUAGE sql STABLE
    AS $$
    SELECT nullif(current_setting(p_key, true), '')
$$;


--
-- Name: touch_updated_at(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;


--
-- Name: upsert_driver_last_position(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.upsert_driver_last_position() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: verify_route_feasibility(uuid); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.verify_route_feasibility(p_route_id uuid) RETURNS TABLE(rule_code text, detail_ar text)
    LANGUAGE plpgsql STABLE
    AS $$
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


--
-- Name: FUNCTION verify_route_feasibility(p_route_id uuid); Type: COMMENT; Schema: app; Owner: -
--

COMMENT ON FUNCTION app.verify_route_feasibility(p_route_id uuid) IS 'فحص جدوى مستقل عن المحرك — يُشغَّل قبل النشر ولا يُسمح بالنشر مع وجود نتائج';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alerts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    alert_type text NOT NULL,
    severity text NOT NULL,
    title_ar text NOT NULL,
    body_ar text NOT NULL,
    shipment_id uuid,
    route_id uuid,
    hub_id uuid,
    region_id uuid,
    driver_id uuid,
    responsible_user_id uuid,
    context jsonb DEFAULT '{}'::jsonb NOT NULL,
    dedupe_key text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    acknowledged_by uuid,
    acknowledged_at timestamp with time zone,
    resolved_at timestamp with time zone,
    action_note text,
    is_test_data boolean DEFAULT false NOT NULL,
    CONSTRAINT alerts_has_target CHECK (((shipment_id IS NOT NULL) OR (route_id IS NOT NULL) OR (hub_id IS NOT NULL))),
    CONSTRAINT alerts_resolution_note CHECK (((resolved_at IS NULL) OR (action_note IS NOT NULL))),
    CONSTRAINT alerts_severity_valid CHECK ((severity = ANY (ARRAY['INFO'::text, 'LOW'::text, 'MEDIUM'::text, 'HIGH'::text, 'CRITICAL'::text]))),
    CONSTRAINT alerts_type_valid CHECK ((alert_type = ANY (ARRAY['PICKUP_WINDOW_APPROACHING'::text, 'PICKUP_LATE'::text, 'DELIVERY_LATE'::text, 'SLA_AT_RISK'::text, 'SLA_BREACHED'::text, 'REQUEST_CANCELLED'::text, 'SAMPLES_NOT_READY'::text, 'PICKUP_FAILED'::text, 'DELIVERY_FAILED'::text, 'TEMPERATURE_BREACH'::text, 'TRACKING_STALE'::text, 'PUBLISHED_ROUTE_MODIFIED'::text, 'NEW_ON_DEMAND_REQUEST'::text, 'ROUTE_WITHOUT_DRIVER'::text, 'DRIVER_SHORTAGE'::text, 'ASSIGNMENT_CONFLICT'::text])))
);


--
-- Name: allowed_transitions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.allowed_transitions (
    entity text NOT NULL,
    from_status text NOT NULL,
    to_status text NOT NULL,
    permission text NOT NULL,
    requires_reason boolean DEFAULT false NOT NULL,
    label_ar text
);


--
-- Name: TABLE allowed_transitions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.allowed_transitions IS 'مصدرها الوحيد masar_core/state_machine.py — تُزامن آليًا عند كل ترحيل';


--
-- Name: api_clients; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_clients (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    key_prefix text NOT NULL,
    key_hash text NOT NULL,
    facility_id uuid,
    scopes text[] DEFAULT '{}'::text[] NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    last_used_at timestamp with time zone,
    expires_at timestamp with time zone,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    id bigint NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    actor_user_id uuid,
    actor_role text,
    actor_name text,
    action text NOT NULL,
    entity_type text,
    entity_id uuid,
    entity_label text,
    old_value jsonb,
    new_value jsonb,
    reason text,
    ip_address inet,
    user_agent text,
    request_id text,
    is_test_data boolean DEFAULT false NOT NULL
);


--
-- Name: TABLE audit_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.audit_log IS 'سجل تدقيق إلحاقي — التعديل والحذف ممنوعان بمحفّز';


--
-- Name: audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_log_id_seq OWNED BY public.audit_log.id;


--
-- Name: availability_exceptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.availability_exceptions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    entity_type text NOT NULL,
    entity_id uuid NOT NULL,
    from_date date NOT NULL,
    to_date date NOT NULL,
    is_available boolean DEFAULT false NOT NULL,
    reason_ar text NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT availability_date_order CHECK ((to_date >= from_date)),
    CONSTRAINT availability_entity_valid CHECK ((entity_type = ANY (ARRAY['DRIVER'::text, 'VEHICLE'::text, 'BOX'::text, 'FACILITY'::text, 'HUB'::text])))
);


--
-- Name: boxes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.boxes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    hub_id uuid NOT NULL,
    code text NOT NULL,
    name_ar text,
    temperature_mode text DEFAULT 'AMBIENT'::text NOT NULL,
    capacity_units integer,
    status text DEFAULT 'AVAILABLE'::text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT boxes_mode_valid CHECK ((temperature_mode = ANY (ARRAY['AMBIENT'::text, 'CHILLED'::text, 'FROZEN'::text, 'DEEP_FROZEN'::text, 'CONTROLLED'::text]))),
    CONSTRAINT boxes_status_valid CHECK ((status = ANY (ARRAY['AVAILABLE'::text, 'IN_USE'::text, 'MAINTENANCE'::text, 'DAMAGED'::text, 'RETIRED'::text])))
);


--
-- Name: cities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cities (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    region_id uuid NOT NULL,
    code text NOT NULL,
    name_ar text NOT NULL,
    name_en text,
    is_governorate boolean DEFAULT false NOT NULL,
    timezone text DEFAULT 'Asia/Riyadh'::text NOT NULL,
    center_lat double precision,
    center_lon double precision,
    is_active boolean DEFAULT true NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT cities_lat_range CHECK (((center_lat IS NULL) OR ((center_lat >= ('-90'::integer)::double precision) AND (center_lat <= (90)::double precision)))),
    CONSTRAINT cities_lon_range CHECK (((center_lon IS NULL) OR ((center_lon >= ('-180'::integer)::double precision) AND (center_lon <= (180)::double precision))))
);


--
-- Name: custody_transfers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.custody_transfers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    shipment_id uuid NOT NULL,
    from_party text NOT NULL,
    to_party text NOT NULL,
    from_entity_id uuid,
    to_entity_id uuid,
    box_id uuid,
    occurred_at timestamp with time zone NOT NULL,
    lat double precision,
    lon double precision,
    document_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT custody_party_valid CHECK (((from_party = ANY (ARRAY['FACILITY'::text, 'DRIVER'::text, 'HUB'::text, 'LAB'::text])) AND (to_party = ANY (ARRAY['FACILITY'::text, 'DRIVER'::text, 'HUB'::text, 'LAB'::text]))))
);


--
-- Name: custom_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.custom_roles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    key text NOT NULL,
    name_ar text NOT NULL,
    base_role text NOT NULL,
    permissions text[] DEFAULT '{}'::text[] NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT custom_roles_base_valid CHECK ((base_role = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text, 'HUB_SUPERVISOR'::text, 'DRIVER'::text, 'EXTERNAL_REQUESTER'::text, 'CONTROL_TOWER'::text, 'AUDITOR'::text, 'INTEGRATION'::text])))
);


--
-- Name: documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    shipment_id uuid,
    route_id uuid,
    route_stop_id uuid,
    exception_id uuid,
    doc_kind text NOT NULL,
    storage_key text NOT NULL,
    original_name text,
    content_type text NOT NULL,
    byte_size bigint NOT NULL,
    sha256 text NOT NULL,
    captured_at timestamp with time zone,
    lat double precision,
    lon double precision,
    uploaded_by uuid,
    uploaded_at timestamp with time zone DEFAULT now() NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    CONSTRAINT documents_kind_valid CHECK ((doc_kind = ANY (ARRAY['PICKUP_PROOF'::text, 'DELIVERY_PROOF'::text, 'EXCEPTION_PROOF'::text, 'TEMPERATURE_LOG'::text, 'OTHER'::text]))),
    CONSTRAINT documents_size_positive CHECK ((byte_size > 0)),
    CONSTRAINT documents_type_allowed CHECK ((content_type = ANY (ARRAY['image/jpeg'::text, 'image/png'::text, 'image/webp'::text, 'application/pdf'::text])))
);


--
-- Name: driver_estimations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.driver_estimations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    plan_id uuid NOT NULL,
    hub_id uuid NOT NULL,
    service_date date NOT NULL,
    theoretical_minimum integer NOT NULL,
    recommended integer NOT NULL,
    available integer NOT NULL,
    used integer NOT NULL,
    gap integer NOT NULL,
    workload_minutes numeric(12,2) DEFAULT 0 NOT NULL,
    justification jsonb DEFAULT '[]'::jsonb NOT NULL,
    sla_impact jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: driver_last_position; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.driver_last_position (
    driver_id uuid NOT NULL,
    route_id uuid,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    speed_kmh numeric(6,2),
    heading_deg numeric(6,2),
    recorded_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: driver_positions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.driver_positions (
    id bigint NOT NULL,
    driver_id uuid NOT NULL,
    route_id uuid,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    speed_kmh numeric(6,2),
    heading_deg numeric(6,2),
    accuracy_m numeric(8,2),
    battery_pct numeric(5,2),
    recorded_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    CONSTRAINT driver_positions_lat CHECK (((lat >= ('-90'::integer)::double precision) AND (lat <= (90)::double precision))),
    CONSTRAINT driver_positions_lon CHECK (((lon >= ('-180'::integer)::double precision) AND (lon <= (180)::double precision)))
)
PARTITION BY RANGE (recorded_at);


--
-- Name: driver_positions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.driver_positions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: driver_positions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.driver_positions_id_seq OWNED BY public.driver_positions.id;


--
-- Name: driver_positions_2026_07; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.driver_positions_2026_07 (
    id bigint DEFAULT nextval('public.driver_positions_id_seq'::regclass) NOT NULL,
    driver_id uuid NOT NULL,
    route_id uuid,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    speed_kmh numeric(6,2),
    heading_deg numeric(6,2),
    accuracy_m numeric(8,2),
    battery_pct numeric(5,2),
    recorded_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    CONSTRAINT driver_positions_lat CHECK (((lat >= ('-90'::integer)::double precision) AND (lat <= (90)::double precision))),
    CONSTRAINT driver_positions_lon CHECK (((lon >= ('-180'::integer)::double precision) AND (lon <= (180)::double precision)))
);


--
-- Name: driver_positions_2026_08; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.driver_positions_2026_08 (
    id bigint DEFAULT nextval('public.driver_positions_id_seq'::regclass) NOT NULL,
    driver_id uuid NOT NULL,
    route_id uuid,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    speed_kmh numeric(6,2),
    heading_deg numeric(6,2),
    accuracy_m numeric(8,2),
    battery_pct numeric(5,2),
    recorded_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    CONSTRAINT driver_positions_lat CHECK (((lat >= ('-90'::integer)::double precision) AND (lat <= (90)::double precision))),
    CONSTRAINT driver_positions_lon CHECK (((lon >= ('-180'::integer)::double precision) AND (lon <= (180)::double precision)))
);


--
-- Name: driver_positions_2026_09; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.driver_positions_2026_09 (
    id bigint DEFAULT nextval('public.driver_positions_id_seq'::regclass) NOT NULL,
    driver_id uuid NOT NULL,
    route_id uuid,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    speed_kmh numeric(6,2),
    heading_deg numeric(6,2),
    accuracy_m numeric(8,2),
    battery_pct numeric(5,2),
    recorded_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    CONSTRAINT driver_positions_lat CHECK (((lat >= ('-90'::integer)::double precision) AND (lat <= (90)::double precision))),
    CONSTRAINT driver_positions_lon CHECK (((lon >= ('-180'::integer)::double precision) AND (lon <= (180)::double precision)))
);


--
-- Name: driver_positions_2026_10; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.driver_positions_2026_10 (
    id bigint DEFAULT nextval('public.driver_positions_id_seq'::regclass) NOT NULL,
    driver_id uuid NOT NULL,
    route_id uuid,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    speed_kmh numeric(6,2),
    heading_deg numeric(6,2),
    accuracy_m numeric(8,2),
    battery_pct numeric(5,2),
    recorded_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    CONSTRAINT driver_positions_lat CHECK (((lat >= ('-90'::integer)::double precision) AND (lat <= (90)::double precision))),
    CONSTRAINT driver_positions_lon CHECK (((lon >= ('-180'::integer)::double precision) AND (lon <= (180)::double precision)))
);


--
-- Name: driver_positions_2026_11; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.driver_positions_2026_11 (
    id bigint DEFAULT nextval('public.driver_positions_id_seq'::regclass) NOT NULL,
    driver_id uuid NOT NULL,
    route_id uuid,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    speed_kmh numeric(6,2),
    heading_deg numeric(6,2),
    accuracy_m numeric(8,2),
    battery_pct numeric(5,2),
    recorded_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    CONSTRAINT driver_positions_lat CHECK (((lat >= ('-90'::integer)::double precision) AND (lat <= (90)::double precision))),
    CONSTRAINT driver_positions_lon CHECK (((lon >= ('-180'::integer)::double precision) AND (lon <= (180)::double precision)))
);


--
-- Name: drivers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.drivers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    hub_id uuid NOT NULL,
    code text NOT NULL,
    full_name text NOT NULL,
    phone text,
    national_id text,
    license_number text,
    license_expiry date,
    employment_status text DEFAULT 'ACTIVE'::text NOT NULL,
    shift_start time without time zone,
    shift_end time without time zone,
    qualifications text[] DEFAULT '{}'::text[] NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT drivers_status_valid CHECK ((employment_status = ANY (ARRAY['ACTIVE'::text, 'ON_LEAVE'::text, 'SUSPENDED'::text, 'TERMINATED'::text])))
);


--
-- Name: facilities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.facilities (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    region_id uuid NOT NULL,
    city_id uuid NOT NULL,
    default_hub_id uuid,
    code text NOT NULL,
    name_ar text NOT NULL,
    name_en text,
    facility_type text NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    address text,
    contact_name text,
    contact_phone text,
    contact_email text,
    service_minutes integer DEFAULT 10 NOT NULL,
    working_hours jsonb,
    notes text,
    is_active boolean DEFAULT true NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    voided_at timestamp with time zone,
    voided_by uuid,
    void_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT facilities_coords_not_null_island CHECK ((NOT ((lat = (0)::double precision) AND (lon = (0)::double precision)))),
    CONSTRAINT facilities_lat_range CHECK (((lat >= ('-90'::integer)::double precision) AND (lat <= (90)::double precision))),
    CONSTRAINT facilities_lon_range CHECK (((lon >= ('-180'::integer)::double precision) AND (lon <= (180)::double precision))),
    CONSTRAINT facilities_service_minutes_range CHECK (((service_minutes >= 1) AND (service_minutes <= 480))),
    CONSTRAINT facilities_type_valid CHECK ((facility_type = ANY (ARRAY['HEALTH_CENTER'::text, 'HOSPITAL'::text, 'LABORATORY'::text, 'BLOOD_BANK'::text, 'WAREHOUSE'::text, 'CLINIC'::text, 'OTHER'::text]))),
    CONSTRAINT facilities_void_reason CHECK (((voided_at IS NULL) OR (void_reason IS NOT NULL)))
);


--
-- Name: TABLE facilities; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.facilities IS 'الجهات: مراكز صحية، مستشفيات، مختبرات، بنوك دم، وغيرها';


--
-- Name: facility_contacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.facility_contacts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    facility_id uuid NOT NULL,
    name text NOT NULL,
    phone text,
    email text,
    role_ar text,
    is_primary boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: holidays; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.holidays (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    scope_type text DEFAULT 'KINGDOM'::text NOT NULL,
    scope_id uuid,
    holiday_date date NOT NULL,
    name_ar text NOT NULL,
    is_working boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT holidays_scope_valid CHECK ((scope_type = ANY (ARRAY['KINGDOM'::text, 'REGION'::text, 'CITY'::text, 'HUB'::text])))
);


--
-- Name: hubs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hubs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    region_id uuid NOT NULL,
    city_id uuid NOT NULL,
    code text NOT NULL,
    name_ar text NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    address text,
    contact_name text,
    contact_phone text,
    working_hours jsonb,
    is_active boolean DEFAULT true NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT hubs_coords_not_null_island CHECK ((NOT ((lat = (0)::double precision) AND (lon = (0)::double precision)))),
    CONSTRAINT hubs_lat_range CHECK (((lat >= ('-90'::integer)::double precision) AND (lat <= (90)::double precision))),
    CONSTRAINT hubs_lon_range CHECK (((lon >= ('-180'::integer)::double precision) AND (lon <= (180)::double precision)))
);


--
-- Name: TABLE hubs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.hubs IS 'مراكز الانطلاق — النطاق التشغيلي الأساسي للمشرف';


--
-- Name: import_rows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.import_rows (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    import_id uuid NOT NULL,
    row_number integer NOT NULL,
    raw jsonb NOT NULL,
    normalized jsonb,
    is_valid boolean DEFAULT false NOT NULL,
    is_excluded boolean DEFAULT false NOT NULL,
    errors jsonb DEFAULT '[]'::jsonb NOT NULL,
    warnings jsonb DEFAULT '[]'::jsonb NOT NULL,
    dedupe_key text,
    shipment_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: login_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.login_attempts (
    id bigint NOT NULL,
    email text NOT NULL,
    succeeded boolean NOT NULL,
    ip_address inet,
    user_agent text,
    failure_code text,
    attempted_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: login_attempts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.login_attempts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: login_attempts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.login_attempts_id_seq OWNED BY public.login_attempts.id;


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    channel text NOT NULL,
    recipient text NOT NULL,
    subject_ar text,
    body_ar text NOT NULL,
    status text DEFAULT 'PENDING'::text NOT NULL,
    priority text DEFAULT 'NORMAL'::text NOT NULL,
    alert_id uuid,
    shipment_id uuid,
    route_id uuid,
    hub_id uuid,
    user_id uuid,
    attempts integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 5 NOT NULL,
    next_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    last_error text,
    provider text,
    provider_ref text,
    dedupe_key text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    sent_at timestamp with time zone,
    is_test_data boolean DEFAULT false NOT NULL,
    CONSTRAINT notifications_channel_valid CHECK ((channel = ANY (ARRAY['SMS'::text, 'EMAIL'::text, 'PUSH'::text, 'WEBHOOK'::text, 'LOG'::text]))),
    CONSTRAINT notifications_failure_reason CHECK (((status <> 'FAILED'::text) OR (last_error IS NOT NULL))),
    CONSTRAINT notifications_priority_valid CHECK ((priority = ANY (ARRAY['LOW'::text, 'NORMAL'::text, 'HIGH'::text, 'CRITICAL'::text]))),
    CONSTRAINT notifications_sent_at CHECK (((status <> 'SENT'::text) OR (sent_at IS NOT NULL))),
    CONSTRAINT notifications_status_valid CHECK ((status = ANY (ARRAY['PENDING'::text, 'SENDING'::text, 'SENT'::text, 'FAILED'::text, 'CANCELLED'::text, 'NO_PROVIDER'::text])))
);


--
-- Name: TABLE notifications; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.notifications IS 'صندوق صادر للإشعارات الخارجية — يُكتب في معاملة الحدث ويُرسَل بعامل مستقل';


--
-- Name: operational_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operational_settings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    setting_key text NOT NULL,
    scope_type text NOT NULL,
    scope_id uuid,
    value jsonb NOT NULL,
    reason text,
    updated_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT operational_settings_kingdom_null CHECK ((((scope_type = 'KINGDOM'::text) AND (scope_id IS NULL)) OR ((scope_type <> 'KINGDOM'::text) AND (scope_id IS NOT NULL)))),
    CONSTRAINT operational_settings_scope_valid CHECK ((scope_type = ANY (ARRAY['KINGDOM'::text, 'REGION'::text, 'CITY'::text, 'HUB'::text])))
);


--
-- Name: TABLE operational_settings; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.operational_settings IS 'القيم التشغيلية القابلة للإعداد — لا قيمة منها مكتوبة في الكود';


--
-- Name: password_resets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.password_resets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token_hash text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: plan_days; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.plan_days (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    plan_id uuid NOT NULL,
    hub_id uuid NOT NULL,
    service_date date NOT NULL,
    is_published boolean DEFAULT false NOT NULL,
    published_at timestamp with time zone,
    published_by uuid,
    metrics jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT plan_days_publish_meta CHECK (((is_published AND (published_at IS NOT NULL)) OR (NOT is_published)))
);


--
-- Name: plan_warnings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.plan_warnings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    plan_id uuid NOT NULL,
    route_id uuid,
    shipment_id uuid,
    hub_id uuid,
    warning_type text NOT NULL,
    severity text DEFAULT 'MEDIUM'::text NOT NULL,
    reason_ar text NOT NULL,
    affected_entity_ar text NOT NULL,
    suggested_action_ar text NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    context jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT plan_warnings_has_detail CHECK (((length(btrim(reason_ar)) > 0) AND (length(btrim(affected_entity_ar)) > 0) AND (length(btrim(suggested_action_ar)) > 0))),
    CONSTRAINT plan_warnings_has_target CHECK (((route_id IS NOT NULL) OR (shipment_id IS NOT NULL) OR (hub_id IS NOT NULL))),
    CONSTRAINT plan_warnings_severity_valid CHECK ((severity = ANY (ARRAY['INFO'::text, 'LOW'::text, 'MEDIUM'::text, 'HIGH'::text, 'CRITICAL'::text])))
);


--
-- Name: plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.plans (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reference text NOT NULL,
    name_ar text NOT NULL,
    status text DEFAULT 'DRAFT'::text NOT NULL,
    scope_type text DEFAULT 'KINGDOM'::text NOT NULL,
    scope_id uuid,
    period_start date NOT NULL,
    period_end date NOT NULL,
    import_id uuid,
    baseline_plan_id uuid,
    parameters jsonb DEFAULT '{}'::jsonb NOT NULL,
    settings_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    metrics jsonb DEFAULT '{}'::jsonb NOT NULL,
    engine_name text,
    engine_version text,
    routing_provider text,
    routing_estimated boolean DEFAULT false NOT NULL,
    solve_ms integer,
    objective_trace jsonb DEFAULT '[]'::jsonb NOT NULL,
    failure_reason text,
    created_by uuid,
    approved_by uuid,
    approved_at timestamp with time zone,
    dispatched_at timestamp with time zone,
    is_test_data boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT plans_period_order CHECK ((period_end >= period_start)),
    CONSTRAINT plans_scope_valid CHECK ((scope_type = ANY (ARRAY['KINGDOM'::text, 'REGION'::text, 'CITY'::text, 'HUB'::text]))),
    CONSTRAINT plans_status_valid CHECK ((status = ANY (ARRAY['DRAFT'::text, 'OPTIMIZING'::text, 'OPTIMIZED'::text, 'APPROVED'::text, 'DISPATCHED'::text, 'SUPERSEDED'::text, 'FAILED'::text])))
);


--
-- Name: COLUMN plans.routing_estimated; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.plans.routing_estimated IS 'true إذا استُخدم مزوّد مسافات تقديري — يُعرض كتحذير ويمنع الاعتماد الصامت';


--
-- Name: regions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.regions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code text NOT NULL,
    name_ar text NOT NULL,
    name_en text,
    timezone text DEFAULT 'Asia/Riyadh'::text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT regions_code_format CHECK ((code ~ '^[A-Z0-9_-]{2,20}$'::text))
);


--
-- Name: TABLE regions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.regions IS 'مناطق المملكة';


--
-- Name: route_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.route_revisions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    route_id uuid NOT NULL,
    revision_number integer NOT NULL,
    changed_by uuid,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    reason text NOT NULL,
    change_kind text NOT NULL,
    before_snapshot jsonb NOT NULL,
    after_snapshot jsonb NOT NULL,
    diff_summary jsonb DEFAULT '{}'::jsonb NOT NULL,
    notified_driver boolean DEFAULT false NOT NULL,
    CONSTRAINT route_revisions_kind_valid CHECK ((change_kind = ANY (ARRAY['ADD_STOP'::text, 'REMOVE_STOP'::text, 'REORDER'::text, 'REASSIGN_DRIVER'::text, 'REASSIGN_VEHICLE'::text, 'RESCHEDULE'::text, 'CANCEL'::text, 'OTHER'::text]))),
    CONSTRAINT route_revisions_reason_present CHECK ((length(btrim(reason)) >= 3))
);


--
-- Name: route_stops; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.route_stops (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    route_id uuid NOT NULL,
    sequence integer NOT NULL,
    kind text NOT NULL,
    facility_id uuid,
    hub_id uuid,
    shipment_id uuid,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    label_ar text NOT NULL,
    planned_arrival_at timestamp with time zone,
    planned_service_start timestamp with time zone,
    planned_departure_at timestamp with time zone,
    window_from timestamp with time zone,
    window_to timestamp with time zone,
    service_minutes numeric(8,2) DEFAULT 0 NOT NULL,
    wait_minutes numeric(8,2) DEFAULT 0 NOT NULL,
    leg_distance_km numeric(10,3) DEFAULT 0 NOT NULL,
    leg_minutes numeric(10,2) DEFAULT 0 NOT NULL,
    leg_is_estimated boolean DEFAULT false NOT NULL,
    status text DEFAULT 'PENDING'::text NOT NULL,
    actual_arrival_at timestamp with time zone,
    actual_completed_at timestamp with time zone,
    actual_lat double precision,
    actual_lon double precision,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT route_stops_actual_order CHECK (((actual_completed_at IS NULL) OR (actual_arrival_at IS NULL) OR (actual_completed_at >= actual_arrival_at))),
    CONSTRAINT route_stops_kind_valid CHECK ((kind = ANY (ARRAY['HUB_START'::text, 'PICKUP'::text, 'DELIVERY'::text, 'HUB_END'::text]))),
    CONSTRAINT route_stops_shipment_required CHECK ((((kind = ANY (ARRAY['PICKUP'::text, 'DELIVERY'::text])) AND (shipment_id IS NOT NULL)) OR (kind = ANY (ARRAY['HUB_START'::text, 'HUB_END'::text])))),
    CONSTRAINT route_stops_status_valid CHECK ((status = ANY (ARRAY['PENDING'::text, 'ARRIVED'::text, 'DONE'::text, 'SKIPPED'::text, 'FAILED'::text])))
);


--
-- Name: routes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.routes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reference text NOT NULL,
    plan_id uuid,
    plan_day_id uuid,
    hub_id uuid NOT NULL,
    region_id uuid NOT NULL,
    service_date date NOT NULL,
    status text DEFAULT 'DRAFT'::text NOT NULL,
    sequence_in_day integer DEFAULT 1 NOT NULL,
    driver_id uuid,
    vehicle_id uuid,
    box_id uuid,
    start_lat double precision NOT NULL,
    start_lon double precision NOT NULL,
    start_node_kind text DEFAULT 'HUB'::text NOT NULL,
    previous_route_id uuid,
    planned_start_at timestamp with time zone,
    planned_end_at timestamp with time zone,
    actual_start_at timestamp with time zone,
    actual_end_at timestamp with time zone,
    end_lat double precision,
    end_lon double precision,
    distance_km numeric(10,3) DEFAULT 0 NOT NULL,
    drive_minutes numeric(10,2) DEFAULT 0 NOT NULL,
    service_minutes numeric(10,2) DEFAULT 0 NOT NULL,
    wait_minutes numeric(10,2) DEFAULT 0 NOT NULL,
    working_minutes numeric(10,2) DEFAULT 0 NOT NULL,
    estimated_cost numeric(12,2) DEFAULT 0 NOT NULL,
    shipment_count integer DEFAULT 0 NOT NULL,
    pickup_count integer DEFAULT 0 NOT NULL,
    delivery_count integer DEFAULT 0 NOT NULL,
    is_long_haul boolean DEFAULT false NOT NULL,
    max_hub_distance_km numeric(10,3) DEFAULT 0 NOT NULL,
    facility_classes text[] DEFAULT '{}'::text[] NOT NULL,
    mixing_exemption_used boolean DEFAULT false NOT NULL,
    assigned_by uuid,
    assigned_at timestamp with time zone,
    published_by uuid,
    published_at timestamp with time zone,
    cancel_reason text,
    is_test_data boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    active_window tstzrange GENERATED ALWAYS AS (app.route_time_range(planned_start_at, planned_end_at)) STORED,
    CONSTRAINT routes_actual_time_order CHECK (((actual_end_at IS NULL) OR (actual_start_at IS NULL) OR (actual_end_at >= actual_start_at))),
    CONSTRAINT routes_assigned_meta CHECK (((driver_id IS NULL) OR (assigned_at IS NOT NULL))),
    CONSTRAINT routes_published_needs_driver CHECK (((status <> ALL (ARRAY['PUBLISHED'::text, 'IN_PROGRESS'::text, 'COMPLETED'::text])) OR (driver_id IS NOT NULL))),
    CONSTRAINT routes_start_node_valid CHECK ((start_node_kind = ANY (ARRAY['HUB'::text, 'PREVIOUS_ROUTE_END'::text, 'DRIVER_CURRENT_POSITION'::text]))),
    CONSTRAINT routes_status_valid CHECK ((status = ANY (ARRAY['DRAFT'::text, 'PLANNED'::text, 'ASSIGNED'::text, 'PUBLISHED'::text, 'IN_PROGRESS'::text, 'COMPLETED'::text, 'CANCELLED'::text]))),
    CONSTRAINT routes_time_order CHECK (((planned_end_at IS NULL) OR (planned_start_at IS NULL) OR (planned_end_at >= planned_start_at)))
);


--
-- Name: schedule_imports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedule_imports (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reference text NOT NULL,
    original_filename text NOT NULL,
    storage_key text NOT NULL,
    content_type text,
    byte_size bigint,
    sha256 text,
    status text DEFAULT 'UPLOADED'::text NOT NULL,
    period_start date,
    period_end date,
    column_mapping jsonb DEFAULT '{}'::jsonb NOT NULL,
    total_rows integer DEFAULT 0 NOT NULL,
    valid_rows integer DEFAULT 0 NOT NULL,
    invalid_rows integer DEFAULT 0 NOT NULL,
    duplicate_rows integer DEFAULT 0 NOT NULL,
    summary jsonb DEFAULT '{}'::jsonb NOT NULL,
    uploaded_by uuid,
    committed_by uuid,
    committed_at timestamp with time zone,
    is_test_data boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT schedule_imports_period_order CHECK (((period_end IS NULL) OR (period_start IS NULL) OR (period_end >= period_start))),
    CONSTRAINT schedule_imports_status_valid CHECK ((status = ANY (ARRAY['UPLOADED'::text, 'MAPPING'::text, 'VALIDATING'::text, 'VALIDATED'::text, 'PARTIALLY_VALID'::text, 'REJECTED'::text, 'COMMITTED'::text])))
);


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version text NOT NULL,
    checksum text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    duration_ms integer DEFAULT 0 NOT NULL
);


--
-- Name: sensors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sensors (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code text NOT NULL,
    provider text DEFAULT 'NONE'::text NOT NULL,
    box_id uuid,
    vehicle_id uuid,
    is_active boolean DEFAULT true NOT NULL,
    last_seen_at timestamp with time zone,
    is_test_data boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT sensors_binding CHECK (((box_id IS NOT NULL) OR (vehicle_id IS NOT NULL)))
);


--
-- Name: shipment_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.shipment_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    shipment_id uuid NOT NULL,
    route_id uuid,
    route_stop_id uuid,
    event_type text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    lat double precision,
    lon double precision,
    accuracy_m numeric(8,2),
    driver_id uuid,
    actor_user_id uuid,
    client_event_id text,
    was_offline boolean DEFAULT false NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    CONSTRAINT shipment_events_type_valid CHECK ((event_type = ANY (ARRAY['ROUTE_STARTED'::text, 'ARRIVED_PICKUP'::text, 'PICKED_UP'::text, 'ARRIVED_DELIVERY'::text, 'DELIVERED'::text, 'EXCEPTION_RECORDED'::text, 'CANCELLED'::text, 'DOCUMENT_UPLOADED'::text, 'REASSIGNED'::text, 'STATUS_CORRECTED'::text, 'ROUTE_COMPLETED'::text])))
);


--
-- Name: COLUMN shipment_events.client_event_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.shipment_events.client_event_id IS 'مفتاح تكرار للعمل دون اتصال — إعادة إرسال نفس الحدث لا تُنشئ سجلًا جديدًا';


--
-- Name: shipment_exceptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.shipment_exceptions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    shipment_id uuid NOT NULL,
    route_id uuid,
    route_stop_id uuid,
    hub_id uuid NOT NULL,
    reason text NOT NULL,
    note text,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    lat double precision,
    lon double precision,
    reported_by uuid,
    reported_by_driver uuid,
    status text DEFAULT 'OPEN'::text NOT NULL,
    keeps_obligation boolean DEFAULT false NOT NULL,
    action_taken text,
    resolution text,
    resolved_by uuid,
    resolved_at timestamp with time zone,
    is_test_data boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT shipment_exceptions_reason_valid CHECK ((reason = ANY (ARRAY['NO_SAMPLES'::text, 'SAMPLES_NOT_READY'::text, 'FACILITY_CLOSED'::text, 'NO_STAFF'::text, 'CANCELLED_BEFORE_PICKUP'::text, 'PICKUP_DELAYED'::text, 'DELIVERY_DELAYED'::text, 'TEMPERATURE_BREACH'::text, 'BOX_DAMAGED'::text, 'LOCATION_UNREACHABLE'::text, 'VEHICLE_BREAKDOWN'::text, 'OTHER'::text]))),
    CONSTRAINT shipment_exceptions_resolution CHECK (((status <> 'RESOLVED'::text) OR ((action_taken IS NOT NULL) AND (resolved_by IS NOT NULL) AND (resolved_at IS NOT NULL)))),
    CONSTRAINT shipment_exceptions_status_valid CHECK ((status = ANY (ARRAY['OPEN'::text, 'ACKNOWLEDGED'::text, 'RESOLVED'::text])))
);


--
-- Name: shipment_status_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.shipment_status_history (
    id bigint NOT NULL,
    shipment_id uuid NOT NULL,
    from_status text,
    to_status text NOT NULL,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    changed_by uuid,
    actor_role text,
    reason text,
    source text DEFAULT 'API'::text NOT NULL,
    context jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: shipment_status_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.shipment_status_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: shipment_status_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.shipment_status_history_id_seq OWNED BY public.shipment_status_history.id;


--
-- Name: shipments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.shipments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    reference text NOT NULL,
    external_reference text,
    request_kind text DEFAULT 'SCHEDULED'::text NOT NULL,
    service_type text DEFAULT 'ROUTINE'::text NOT NULL,
    status text DEFAULT 'DRAFT'::text NOT NULL,
    region_id uuid NOT NULL,
    city_id uuid NOT NULL,
    hub_id uuid NOT NULL,
    pickup_facility_id uuid NOT NULL,
    pickup_facility_type text NOT NULL,
    pickup_contact_name text,
    pickup_contact_phone text,
    pickup_address text,
    pickup_lat double precision NOT NULL,
    pickup_lon double precision NOT NULL,
    pickup_window_from timestamp with time zone NOT NULL,
    pickup_window_to timestamp with time zone NOT NULL,
    pickup_service_minutes integer DEFAULT 10 NOT NULL,
    dropoff_facility_id uuid NOT NULL,
    dropoff_facility_type text NOT NULL,
    dropoff_contact_name text,
    dropoff_contact_phone text,
    dropoff_address text,
    dropoff_lat double precision NOT NULL,
    dropoff_lon double precision NOT NULL,
    sla_deadline timestamp with time zone NOT NULL,
    dropoff_service_minutes integer DEFAULT 10 NOT NULL,
    piece_count integer DEFAULT 1 NOT NULL,
    sample_types text[] DEFAULT '{}'::text[] NOT NULL,
    temperature_mode text DEFAULT 'AMBIENT'::text NOT NULL,
    temperature_min_c numeric(5,2),
    temperature_max_c numeric(5,2),
    service_date date NOT NULL,
    route_id uuid,
    driver_id uuid,
    vehicle_id uuid,
    box_id uuid,
    planned_pickup_arrival timestamp with time zone,
    planned_pickup_at timestamp with time zone,
    planned_dropoff_arrival timestamp with time zone,
    planned_dropoff_at timestamp with time zone,
    actual_pickup_arrival timestamp with time zone,
    actual_pickup_at timestamp with time zone,
    actual_dropoff_arrival timestamp with time zone,
    actual_dropoff_at timestamp with time zone,
    sla_breached boolean DEFAULT false NOT NULL,
    pickup_window_breached boolean DEFAULT false NOT NULL,
    delay_minutes integer,
    failure_reason text,
    cancel_reason text,
    unplannable_reason text,
    unplannable_detail text,
    delivery_obligation_open boolean DEFAULT false NOT NULL,
    import_id uuid,
    import_row_number integer,
    requested_by uuid,
    requester_facility_id uuid,
    approved_by uuid,
    approved_at timestamp with time zone,
    rejection_reason text,
    notes text,
    dedupe_key text,
    is_test_data boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT shipments_actual_order CHECK (((actual_dropoff_at IS NULL) OR (actual_pickup_at IS NULL) OR (actual_dropoff_at >= actual_pickup_at))),
    CONSTRAINT shipments_cancel_reason CHECK (((status <> 'CANCELLED_BEFORE_PICKUP'::text) OR (cancel_reason IS NOT NULL))),
    CONSTRAINT shipments_distinct_endpoints CHECK ((pickup_facility_id <> dropoff_facility_id)),
    CONSTRAINT shipments_dropoff_lat CHECK (((dropoff_lat >= ('-90'::integer)::double precision) AND (dropoff_lat <= (90)::double precision))),
    CONSTRAINT shipments_dropoff_lon CHECK (((dropoff_lon >= ('-180'::integer)::double precision) AND (dropoff_lon <= (180)::double precision))),
    CONSTRAINT shipments_kind_valid CHECK ((request_kind = ANY (ARRAY['SCHEDULED'::text, 'ON_DEMAND'::text]))),
    CONSTRAINT shipments_pickup_lat CHECK (((pickup_lat >= ('-90'::integer)::double precision) AND (pickup_lat <= (90)::double precision))),
    CONSTRAINT shipments_pickup_lon CHECK (((pickup_lon >= ('-180'::integer)::double precision) AND (pickup_lon <= (180)::double precision))),
    CONSTRAINT shipments_pieces_positive CHECK ((piece_count > 0)),
    CONSTRAINT shipments_reject_reason CHECK (((status <> 'REJECTED'::text) OR (rejection_reason IS NOT NULL))),
    CONSTRAINT shipments_service_valid CHECK ((service_type = ANY (ARRAY['ROUTINE'::text, 'URGENT'::text, 'STAT'::text, 'RETURN'::text]))),
    CONSTRAINT shipments_sla_after_window CHECK ((sla_deadline > pickup_window_from)),
    CONSTRAINT shipments_status_valid CHECK ((status = ANY (ARRAY['DRAFT'::text, 'VALIDATED'::text, 'PENDING_APPROVAL'::text, 'REJECTED'::text, 'PENDING_ASSIGNMENT'::text, 'PLANNED'::text, 'ASSIGNED'::text, 'PUBLISHED'::text, 'IN_PROGRESS'::text, 'ARRIVED_PICKUP'::text, 'PICKED_UP'::text, 'ARRIVED_DELIVERY'::text, 'DELIVERED'::text, 'COMPLETED'::text, 'CANCELLED_BEFORE_PICKUP'::text, 'EXCEPTION'::text, 'FAILED'::text, 'UNPLANNABLE'::text]))),
    CONSTRAINT shipments_temp_mode_valid CHECK ((temperature_mode = ANY (ARRAY['AMBIENT'::text, 'CHILLED'::text, 'FROZEN'::text, 'DEEP_FROZEN'::text, 'CONTROLLED'::text]))),
    CONSTRAINT shipments_unplannable_reason CHECK (((status <> 'UNPLANNABLE'::text) OR (unplannable_reason IS NOT NULL))),
    CONSTRAINT shipments_window_order CHECK ((pickup_window_to >= pickup_window_from))
);


--
-- Name: COLUMN shipments.dedupe_key; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.shipments.dedupe_key IS 'مفتاح منع التكرار: جهة الالتقاط + جهة التسليم + التاريخ + نافذة الالتقاط + المرجع الخارجي';


--
-- Name: system_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_events (
    id bigint NOT NULL,
    topic text NOT NULL,
    payload jsonb NOT NULL,
    hub_id uuid,
    region_id uuid,
    driver_id uuid,
    user_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: system_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.system_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: system_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.system_events_id_seq OWNED BY public.system_events.id;


--
-- Name: temperature_breaches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.temperature_breaches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    shipment_id uuid,
    box_id uuid,
    route_id uuid,
    sensor_id uuid,
    started_at timestamp with time zone NOT NULL,
    ended_at timestamp with time zone,
    duration_minutes numeric(10,2),
    min_celsius numeric(6,2),
    max_celsius numeric(6,2),
    required_min_c numeric(6,2),
    required_max_c numeric(6,2),
    breach_kind text NOT NULL,
    action_taken text,
    resolved_by uuid,
    resolved_at timestamp with time zone,
    is_test_data boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT temperature_breach_kind_valid CHECK ((breach_kind = ANY (ARRAY['HIGH'::text, 'LOW'::text]))),
    CONSTRAINT temperature_breach_order CHECK (((ended_at IS NULL) OR (ended_at >= started_at)))
);


--
-- Name: temperature_ranges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.temperature_ranges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    mode text NOT NULL,
    name_ar text NOT NULL,
    min_celsius numeric(5,2) NOT NULL,
    max_celsius numeric(5,2) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT temperature_ranges_order CHECK ((min_celsius < max_celsius))
);


--
-- Name: temperature_readings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.temperature_readings (
    id bigint NOT NULL,
    sensor_id uuid,
    box_id uuid,
    shipment_id uuid,
    route_id uuid,
    celsius numeric(6,2) NOT NULL,
    humidity_pct numeric(5,2),
    recorded_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    source text NOT NULL,
    status text DEFAULT 'IN_RANGE'::text NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    CONSTRAINT temperature_source_valid CHECK ((source = ANY (ARRAY['SENSOR'::text, 'GATEWAY'::text, 'SIMULATION'::text, 'MANUAL_ADMIN'::text]))),
    CONSTRAINT temperature_status_valid CHECK ((status = ANY (ARRAY['IN_RANGE'::text, 'BREACH_HIGH'::text, 'BREACH_LOW'::text, 'NO_SENSOR'::text, 'STALE'::text])))
);


--
-- Name: COLUMN temperature_readings.source; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.temperature_readings.source IS 'SIMULATION تعني بيانات محاكاة اختبارية — تُعرض دائمًا موسومة ولا تُقدَّم كتكامل حقيقي';


--
-- Name: temperature_readings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.temperature_readings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: temperature_readings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.temperature_readings_id_seq OWNED BY public.temperature_readings.id;


--
-- Name: user_scopes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_scopes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    scope_type text NOT NULL,
    scope_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT user_scopes_type_valid CHECK ((scope_type = ANY (ARRAY['REGION'::text, 'HUB'::text, 'FACILITY'::text])))
);


--
-- Name: user_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    refresh_token_hash text NOT NULL,
    issued_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    revoke_reason text,
    ip_address inet,
    user_agent text,
    previous_token_hash text,
    rotation_count integer DEFAULT 0 NOT NULL,
    CONSTRAINT user_sessions_expiry CHECK ((expires_at > issued_at))
);


--
-- Name: COLUMN user_sessions.previous_token_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_sessions.previous_token_hash IS 'تجزئة الرمز السابق مباشرةً — وجودها هو ما يجعل إعادة الاستخدام قابلة للكشف';


--
-- Name: COLUMN user_sessions.rotation_count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_sessions.rotation_count IS 'عدد مرات تدوير رمز التحديث — مؤشر تشغيلي على عمر الجلسة ونشاطها';


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email text NOT NULL,
    phone text,
    full_name text NOT NULL,
    password_hash text,
    role text NOT NULL,
    custom_role_id uuid,
    is_active boolean DEFAULT true NOT NULL,
    must_change_password boolean DEFAULT true NOT NULL,
    failed_attempts integer DEFAULT 0 NOT NULL,
    locked_until timestamp with time zone,
    last_login_at timestamp with time zone,
    preferred_locale text DEFAULT 'ar'::text NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT users_email_format CHECK ((email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'::text)),
    CONSTRAINT users_role_valid CHECK ((role = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text, 'HUB_SUPERVISOR'::text, 'DRIVER'::text, 'EXTERNAL_REQUESTER'::text, 'CONTROL_TOWER'::text, 'AUDITOR'::text, 'INTEGRATION'::text])))
);


--
-- Name: vehicles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vehicles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    hub_id uuid NOT NULL,
    plate_number text NOT NULL,
    model text,
    make_year integer,
    vehicle_type text DEFAULT 'CAR'::text NOT NULL,
    has_cooling boolean DEFAULT false NOT NULL,
    status text DEFAULT 'AVAILABLE'::text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_test_data boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT vehicles_status_valid CHECK ((status = ANY (ARRAY['AVAILABLE'::text, 'IN_USE'::text, 'MAINTENANCE'::text, 'OUT_OF_SERVICE'::text]))),
    CONSTRAINT vehicles_type_valid CHECK ((vehicle_type = ANY (ARRAY['CAR'::text, 'VAN'::text, 'TRUCK'::text, 'MOTORCYCLE'::text])))
);


--
-- Name: driver_positions_2026_07; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_positions ATTACH PARTITION public.driver_positions_2026_07 FOR VALUES FROM ('2026-07-01 00:00:00+00') TO ('2026-08-01 00:00:00+00');


--
-- Name: driver_positions_2026_08; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_positions ATTACH PARTITION public.driver_positions_2026_08 FOR VALUES FROM ('2026-08-01 00:00:00+00') TO ('2026-09-01 00:00:00+00');


--
-- Name: driver_positions_2026_09; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_positions ATTACH PARTITION public.driver_positions_2026_09 FOR VALUES FROM ('2026-09-01 00:00:00+00') TO ('2026-10-01 00:00:00+00');


--
-- Name: driver_positions_2026_10; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_positions ATTACH PARTITION public.driver_positions_2026_10 FOR VALUES FROM ('2026-10-01 00:00:00+00') TO ('2026-11-01 00:00:00+00');


--
-- Name: driver_positions_2026_11; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_positions ATTACH PARTITION public.driver_positions_2026_11 FOR VALUES FROM ('2026-11-01 00:00:00+00') TO ('2026-12-01 00:00:00+00');


--
-- Name: audit_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log ALTER COLUMN id SET DEFAULT nextval('public.audit_log_id_seq'::regclass);


--
-- Name: driver_positions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_positions ALTER COLUMN id SET DEFAULT nextval('public.driver_positions_id_seq'::regclass);


--
-- Name: login_attempts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.login_attempts ALTER COLUMN id SET DEFAULT nextval('public.login_attempts_id_seq'::regclass);


--
-- Name: shipment_status_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_status_history ALTER COLUMN id SET DEFAULT nextval('public.shipment_status_history_id_seq'::regclass);


--
-- Name: system_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_events ALTER COLUMN id SET DEFAULT nextval('public.system_events_id_seq'::regclass);


--
-- Name: temperature_readings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temperature_readings ALTER COLUMN id SET DEFAULT nextval('public.temperature_readings_id_seq'::regclass);


--
-- Name: alerts alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_pkey PRIMARY KEY (id);


--
-- Name: allowed_transitions allowed_transitions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.allowed_transitions
    ADD CONSTRAINT allowed_transitions_pkey PRIMARY KEY (entity, from_status, to_status);


--
-- Name: api_clients api_clients_key_prefix_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_clients
    ADD CONSTRAINT api_clients_key_prefix_key UNIQUE (key_prefix);


--
-- Name: api_clients api_clients_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_clients
    ADD CONSTRAINT api_clients_pkey PRIMARY KEY (id);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: availability_exceptions availability_exceptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.availability_exceptions
    ADD CONSTRAINT availability_exceptions_pkey PRIMARY KEY (id);


--
-- Name: boxes boxes_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boxes
    ADD CONSTRAINT boxes_code_key UNIQUE (code);


--
-- Name: boxes boxes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boxes
    ADD CONSTRAINT boxes_pkey PRIMARY KEY (id);


--
-- Name: cities cities_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cities
    ADD CONSTRAINT cities_code_key UNIQUE (code);


--
-- Name: cities cities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cities
    ADD CONSTRAINT cities_pkey PRIMARY KEY (id);


--
-- Name: custody_transfers custody_transfers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.custody_transfers
    ADD CONSTRAINT custody_transfers_pkey PRIMARY KEY (id);


--
-- Name: custom_roles custom_roles_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.custom_roles
    ADD CONSTRAINT custom_roles_key_key UNIQUE (key);


--
-- Name: custom_roles custom_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.custom_roles
    ADD CONSTRAINT custom_roles_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: documents documents_storage_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_storage_key_key UNIQUE (storage_key);


--
-- Name: driver_estimations driver_estimations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_estimations
    ADD CONSTRAINT driver_estimations_pkey PRIMARY KEY (id);


--
-- Name: driver_estimations driver_estimations_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_estimations
    ADD CONSTRAINT driver_estimations_uniq UNIQUE (plan_id, hub_id, service_date);


--
-- Name: driver_last_position driver_last_position_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_last_position
    ADD CONSTRAINT driver_last_position_pkey PRIMARY KEY (driver_id);


--
-- Name: driver_positions driver_positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_positions
    ADD CONSTRAINT driver_positions_pkey PRIMARY KEY (id, recorded_at);


--
-- Name: driver_positions_2026_07 driver_positions_2026_07_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_positions_2026_07
    ADD CONSTRAINT driver_positions_2026_07_pkey PRIMARY KEY (id, recorded_at);


--
-- Name: driver_positions_2026_08 driver_positions_2026_08_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_positions_2026_08
    ADD CONSTRAINT driver_positions_2026_08_pkey PRIMARY KEY (id, recorded_at);


--
-- Name: driver_positions_2026_09 driver_positions_2026_09_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_positions_2026_09
    ADD CONSTRAINT driver_positions_2026_09_pkey PRIMARY KEY (id, recorded_at);


--
-- Name: driver_positions_2026_10 driver_positions_2026_10_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_positions_2026_10
    ADD CONSTRAINT driver_positions_2026_10_pkey PRIMARY KEY (id, recorded_at);


--
-- Name: driver_positions_2026_11 driver_positions_2026_11_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_positions_2026_11
    ADD CONSTRAINT driver_positions_2026_11_pkey PRIMARY KEY (id, recorded_at);


--
-- Name: drivers drivers_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drivers
    ADD CONSTRAINT drivers_code_key UNIQUE (code);


--
-- Name: drivers drivers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drivers
    ADD CONSTRAINT drivers_pkey PRIMARY KEY (id);


--
-- Name: drivers drivers_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drivers
    ADD CONSTRAINT drivers_user_id_key UNIQUE (user_id);


--
-- Name: facilities facilities_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facilities
    ADD CONSTRAINT facilities_code_key UNIQUE (code);


--
-- Name: facilities facilities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facilities
    ADD CONSTRAINT facilities_pkey PRIMARY KEY (id);


--
-- Name: facility_contacts facility_contacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facility_contacts
    ADD CONSTRAINT facility_contacts_pkey PRIMARY KEY (id);


--
-- Name: holidays holidays_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.holidays
    ADD CONSTRAINT holidays_pkey PRIMARY KEY (id);


--
-- Name: holidays holidays_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.holidays
    ADD CONSTRAINT holidays_uniq UNIQUE (scope_type, scope_id, holiday_date);


--
-- Name: hubs hubs_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hubs
    ADD CONSTRAINT hubs_code_key UNIQUE (code);


--
-- Name: hubs hubs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hubs
    ADD CONSTRAINT hubs_pkey PRIMARY KEY (id);


--
-- Name: import_rows import_rows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.import_rows
    ADD CONSTRAINT import_rows_pkey PRIMARY KEY (id);


--
-- Name: import_rows import_rows_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.import_rows
    ADD CONSTRAINT import_rows_uniq UNIQUE (import_id, row_number);


--
-- Name: login_attempts login_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.login_attempts
    ADD CONSTRAINT login_attempts_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: operational_settings operational_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operational_settings
    ADD CONSTRAINT operational_settings_pkey PRIMARY KEY (id);


--
-- Name: password_resets password_resets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_pkey PRIMARY KEY (id);


--
-- Name: password_resets password_resets_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_token_hash_key UNIQUE (token_hash);


--
-- Name: plan_days plan_days_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plan_days
    ADD CONSTRAINT plan_days_pkey PRIMARY KEY (id);


--
-- Name: plan_days plan_days_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plan_days
    ADD CONSTRAINT plan_days_uniq UNIQUE (plan_id, hub_id, service_date);


--
-- Name: plan_warnings plan_warnings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plan_warnings
    ADD CONSTRAINT plan_warnings_pkey PRIMARY KEY (id);


--
-- Name: plans plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plans
    ADD CONSTRAINT plans_pkey PRIMARY KEY (id);


--
-- Name: plans plans_reference_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plans
    ADD CONSTRAINT plans_reference_key UNIQUE (reference);


--
-- Name: regions regions_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regions
    ADD CONSTRAINT regions_code_key UNIQUE (code);


--
-- Name: regions regions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regions
    ADD CONSTRAINT regions_pkey PRIMARY KEY (id);


--
-- Name: route_revisions route_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.route_revisions
    ADD CONSTRAINT route_revisions_pkey PRIMARY KEY (id);


--
-- Name: route_revisions route_revisions_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.route_revisions
    ADD CONSTRAINT route_revisions_uniq UNIQUE (route_id, revision_number);


--
-- Name: route_stops route_stops_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.route_stops
    ADD CONSTRAINT route_stops_pkey PRIMARY KEY (id);


--
-- Name: route_stops route_stops_seq_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.route_stops
    ADD CONSTRAINT route_stops_seq_uniq UNIQUE (route_id, sequence);


--
-- Name: routes routes_driver_no_overlap; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_driver_no_overlap EXCLUDE USING gist (driver_id WITH =, active_window WITH &&) WHERE (((driver_id IS NOT NULL) AND (status = ANY (ARRAY['ASSIGNED'::text, 'PUBLISHED'::text, 'IN_PROGRESS'::text]))));


--
-- Name: routes routes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_pkey PRIMARY KEY (id);


--
-- Name: routes routes_reference_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_reference_key UNIQUE (reference);


--
-- Name: routes routes_vehicle_no_overlap; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_vehicle_no_overlap EXCLUDE USING gist (vehicle_id WITH =, active_window WITH &&) WHERE (((vehicle_id IS NOT NULL) AND (status = ANY (ARRAY['ASSIGNED'::text, 'PUBLISHED'::text, 'IN_PROGRESS'::text]))));


--
-- Name: schedule_imports schedule_imports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_imports
    ADD CONSTRAINT schedule_imports_pkey PRIMARY KEY (id);


--
-- Name: schedule_imports schedule_imports_reference_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_imports
    ADD CONSTRAINT schedule_imports_reference_key UNIQUE (reference);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: sensors sensors_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensors
    ADD CONSTRAINT sensors_code_key UNIQUE (code);


--
-- Name: sensors sensors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensors
    ADD CONSTRAINT sensors_pkey PRIMARY KEY (id);


--
-- Name: shipment_events shipment_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_events
    ADD CONSTRAINT shipment_events_pkey PRIMARY KEY (id);


--
-- Name: shipment_exceptions shipment_exceptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_exceptions
    ADD CONSTRAINT shipment_exceptions_pkey PRIMARY KEY (id);


--
-- Name: shipment_status_history shipment_status_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_status_history
    ADD CONSTRAINT shipment_status_history_pkey PRIMARY KEY (id);


--
-- Name: shipments shipments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_pkey PRIMARY KEY (id);


--
-- Name: shipments shipments_reference_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_reference_key UNIQUE (reference);


--
-- Name: system_events system_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_events
    ADD CONSTRAINT system_events_pkey PRIMARY KEY (id);


--
-- Name: temperature_breaches temperature_breaches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temperature_breaches
    ADD CONSTRAINT temperature_breaches_pkey PRIMARY KEY (id);


--
-- Name: temperature_ranges temperature_ranges_mode_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temperature_ranges
    ADD CONSTRAINT temperature_ranges_mode_key UNIQUE (mode);


--
-- Name: temperature_ranges temperature_ranges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temperature_ranges
    ADD CONSTRAINT temperature_ranges_pkey PRIMARY KEY (id);


--
-- Name: temperature_readings temperature_readings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temperature_readings
    ADD CONSTRAINT temperature_readings_pkey PRIMARY KEY (id);


--
-- Name: user_scopes user_scopes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_scopes
    ADD CONSTRAINT user_scopes_pkey PRIMARY KEY (id);


--
-- Name: user_scopes user_scopes_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_scopes
    ADD CONSTRAINT user_scopes_uniq UNIQUE (user_id, scope_type, scope_id);


--
-- Name: user_sessions user_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_pkey PRIMARY KEY (id);


--
-- Name: user_sessions user_sessions_refresh_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_refresh_token_hash_key UNIQUE (refresh_token_hash);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: vehicles vehicles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_pkey PRIMARY KEY (id);


--
-- Name: vehicles vehicles_plate_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_plate_number_key UNIQUE (plate_number);


--
-- Name: alerts_dedupe_uniq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX alerts_dedupe_uniq ON public.alerts USING btree (dedupe_key) WHERE ((dedupe_key IS NOT NULL) AND (resolved_at IS NULL));


--
-- Name: alerts_hub_open_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX alerts_hub_open_idx ON public.alerts USING btree (hub_id, created_at DESC) WHERE (resolved_at IS NULL);


--
-- Name: alerts_severity_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX alerts_severity_idx ON public.alerts USING btree (severity, created_at DESC) WHERE (resolved_at IS NULL);


--
-- Name: alerts_shipment_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX alerts_shipment_idx ON public.alerts USING btree (shipment_id);


--
-- Name: audit_log_action_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX audit_log_action_idx ON public.audit_log USING btree (action, occurred_at DESC);


--
-- Name: audit_log_actor_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX audit_log_actor_idx ON public.audit_log USING btree (actor_user_id, occurred_at DESC);


--
-- Name: audit_log_entity_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX audit_log_entity_idx ON public.audit_log USING btree (entity_type, entity_id, occurred_at DESC);


--
-- Name: audit_log_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX audit_log_time_idx ON public.audit_log USING btree (occurred_at DESC);


--
-- Name: availability_entity_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX availability_entity_idx ON public.availability_exceptions USING btree (entity_type, entity_id, from_date, to_date);


--
-- Name: boxes_hub_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX boxes_hub_idx ON public.boxes USING btree (hub_id) WHERE is_active;


--
-- Name: cities_region_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX cities_region_idx ON public.cities USING btree (region_id) WHERE is_active;


--
-- Name: custody_transfers_shipment_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX custody_transfers_shipment_idx ON public.custody_transfers USING btree (shipment_id, occurred_at);


--
-- Name: documents_shipment_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX documents_shipment_idx ON public.documents USING btree (shipment_id);


--
-- Name: documents_stop_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX documents_stop_idx ON public.documents USING btree (route_stop_id);


--
-- Name: driver_positions_driver_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX driver_positions_driver_time_idx ON ONLY public.driver_positions USING btree (driver_id, recorded_at DESC);


--
-- Name: driver_positions_2026_07_driver_id_recorded_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX driver_positions_2026_07_driver_id_recorded_at_idx ON public.driver_positions_2026_07 USING btree (driver_id, recorded_at DESC);


--
-- Name: driver_positions_route_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX driver_positions_route_idx ON ONLY public.driver_positions USING btree (route_id, recorded_at);


--
-- Name: driver_positions_2026_07_route_id_recorded_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX driver_positions_2026_07_route_id_recorded_at_idx ON public.driver_positions_2026_07 USING btree (route_id, recorded_at);


--
-- Name: driver_positions_2026_08_driver_id_recorded_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX driver_positions_2026_08_driver_id_recorded_at_idx ON public.driver_positions_2026_08 USING btree (driver_id, recorded_at DESC);


--
-- Name: driver_positions_2026_08_route_id_recorded_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX driver_positions_2026_08_route_id_recorded_at_idx ON public.driver_positions_2026_08 USING btree (route_id, recorded_at);


--
-- Name: driver_positions_2026_09_driver_id_recorded_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX driver_positions_2026_09_driver_id_recorded_at_idx ON public.driver_positions_2026_09 USING btree (driver_id, recorded_at DESC);


--
-- Name: driver_positions_2026_09_route_id_recorded_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX driver_positions_2026_09_route_id_recorded_at_idx ON public.driver_positions_2026_09 USING btree (route_id, recorded_at);


--
-- Name: driver_positions_2026_10_driver_id_recorded_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX driver_positions_2026_10_driver_id_recorded_at_idx ON public.driver_positions_2026_10 USING btree (driver_id, recorded_at DESC);


--
-- Name: driver_positions_2026_10_route_id_recorded_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX driver_positions_2026_10_route_id_recorded_at_idx ON public.driver_positions_2026_10 USING btree (route_id, recorded_at);


--
-- Name: driver_positions_2026_11_driver_id_recorded_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX driver_positions_2026_11_driver_id_recorded_at_idx ON public.driver_positions_2026_11 USING btree (driver_id, recorded_at DESC);


--
-- Name: driver_positions_2026_11_route_id_recorded_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX driver_positions_2026_11_route_id_recorded_at_idx ON public.driver_positions_2026_11 USING btree (route_id, recorded_at);


--
-- Name: drivers_hub_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX drivers_hub_idx ON public.drivers USING btree (hub_id) WHERE is_active;


--
-- Name: facilities_city_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX facilities_city_idx ON public.facilities USING btree (city_id) WHERE is_active;


--
-- Name: facilities_hub_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX facilities_hub_idx ON public.facilities USING btree (default_hub_id) WHERE is_active;


--
-- Name: facilities_name_city_uniq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX facilities_name_city_uniq ON public.facilities USING btree (city_id, lower(name_ar)) WHERE (voided_at IS NULL);


--
-- Name: facilities_type_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX facilities_type_idx ON public.facilities USING btree (facility_type);


--
-- Name: facility_contacts_facility_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX facility_contacts_facility_idx ON public.facility_contacts USING btree (facility_id);


--
-- Name: hubs_city_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX hubs_city_idx ON public.hubs USING btree (city_id);


--
-- Name: hubs_region_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX hubs_region_idx ON public.hubs USING btree (region_id);


--
-- Name: import_rows_dedupe_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX import_rows_dedupe_idx ON public.import_rows USING btree (import_id, dedupe_key) WHERE (dedupe_key IS NOT NULL);


--
-- Name: import_rows_import_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX import_rows_import_idx ON public.import_rows USING btree (import_id, is_valid);


--
-- Name: login_attempts_email_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX login_attempts_email_time_idx ON public.login_attempts USING btree (lower(email), attempted_at DESC);


--
-- Name: notifications_dedupe_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX notifications_dedupe_idx ON public.notifications USING btree (dedupe_key) WHERE (dedupe_key IS NOT NULL);


--
-- Name: notifications_hub_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX notifications_hub_idx ON public.notifications USING btree (hub_id, created_at DESC);


--
-- Name: notifications_pending_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX notifications_pending_idx ON public.notifications USING btree (next_attempt_at, priority) WHERE (status = ANY (ARRAY['PENDING'::text, 'SENDING'::text]));


--
-- Name: operational_settings_uniq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX operational_settings_uniq ON public.operational_settings USING btree (setting_key, scope_type, COALESCE(scope_id, '00000000-0000-0000-0000-000000000000'::uuid));


--
-- Name: plan_days_hub_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX plan_days_hub_date_idx ON public.plan_days USING btree (hub_id, service_date);


--
-- Name: plan_warnings_plan_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX plan_warnings_plan_idx ON public.plan_warnings USING btree (plan_id, severity);


--
-- Name: plan_warnings_route_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX plan_warnings_route_idx ON public.plan_warnings USING btree (route_id);


--
-- Name: plan_warnings_shipment_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX plan_warnings_shipment_idx ON public.plan_warnings USING btree (shipment_id);


--
-- Name: plans_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX plans_status_idx ON public.plans USING btree (status, period_start DESC);


--
-- Name: route_revisions_route_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX route_revisions_route_idx ON public.route_revisions USING btree (route_id, revision_number DESC);


--
-- Name: route_stops_route_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX route_stops_route_idx ON public.route_stops USING btree (route_id, sequence);


--
-- Name: route_stops_shipment_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX route_stops_shipment_idx ON public.route_stops USING btree (shipment_id) WHERE (shipment_id IS NOT NULL);


--
-- Name: routes_driver_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX routes_driver_date_idx ON public.routes USING btree (driver_id, service_date) WHERE (driver_id IS NOT NULL);


--
-- Name: routes_hub_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX routes_hub_date_idx ON public.routes USING btree (hub_id, service_date, status);


--
-- Name: routes_plan_day_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX routes_plan_day_idx ON public.routes USING btree (plan_day_id);


--
-- Name: routes_plan_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX routes_plan_idx ON public.routes USING btree (plan_id);


--
-- Name: shipment_events_client_uniq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX shipment_events_client_uniq ON public.shipment_events USING btree (driver_id, client_event_id) WHERE (client_event_id IS NOT NULL);


--
-- Name: shipment_events_route_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shipment_events_route_idx ON public.shipment_events USING btree (route_id, occurred_at);


--
-- Name: shipment_events_shipment_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shipment_events_shipment_idx ON public.shipment_events USING btree (shipment_id, occurred_at);


--
-- Name: shipment_exceptions_hub_open_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shipment_exceptions_hub_open_idx ON public.shipment_exceptions USING btree (hub_id, occurred_at DESC) WHERE (status <> 'RESOLVED'::text);


--
-- Name: shipment_exceptions_shipment_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shipment_exceptions_shipment_idx ON public.shipment_exceptions USING btree (shipment_id);


--
-- Name: shipment_status_history_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shipment_status_history_idx ON public.shipment_status_history USING btree (shipment_id, changed_at DESC);


--
-- Name: shipments_dedupe_uniq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX shipments_dedupe_uniq ON public.shipments USING btree (dedupe_key) WHERE (dedupe_key IS NOT NULL);


--
-- Name: shipments_driver_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shipments_driver_idx ON public.shipments USING btree (driver_id, service_date) WHERE (driver_id IS NOT NULL);


--
-- Name: shipments_hub_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shipments_hub_date_idx ON public.shipments USING btree (hub_id, service_date, status);


--
-- Name: shipments_import_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shipments_import_idx ON public.shipments USING btree (import_id) WHERE (import_id IS NOT NULL);


--
-- Name: shipments_open_obligation_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shipments_open_obligation_idx ON public.shipments USING btree (hub_id, service_date) WHERE delivery_obligation_open;


--
-- Name: shipments_region_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shipments_region_date_idx ON public.shipments USING btree (region_id, service_date);


--
-- Name: shipments_requester_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shipments_requester_idx ON public.shipments USING btree (requester_facility_id) WHERE (requester_facility_id IS NOT NULL);


--
-- Name: shipments_route_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shipments_route_idx ON public.shipments USING btree (route_id) WHERE (route_id IS NOT NULL);


--
-- Name: shipments_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX shipments_status_idx ON public.shipments USING btree (status, service_date);


--
-- Name: system_events_hub_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX system_events_hub_idx ON public.system_events USING btree (hub_id, id DESC);


--
-- Name: system_events_topic_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX system_events_topic_time_idx ON public.system_events USING btree (topic, id DESC);


--
-- Name: temperature_breaches_shipment_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX temperature_breaches_shipment_idx ON public.temperature_breaches USING btree (shipment_id);


--
-- Name: temperature_readings_box_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX temperature_readings_box_idx ON public.temperature_readings USING btree (box_id, recorded_at DESC);


--
-- Name: temperature_readings_shipment_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX temperature_readings_shipment_idx ON public.temperature_readings USING btree (shipment_id, recorded_at DESC);


--
-- Name: user_scopes_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_scopes_user_idx ON public.user_scopes USING btree (user_id);


--
-- Name: user_sessions_previous_token_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_sessions_previous_token_idx ON public.user_sessions USING btree (previous_token_hash) WHERE (previous_token_hash IS NOT NULL);


--
-- Name: user_sessions_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_sessions_user_idx ON public.user_sessions USING btree (user_id) WHERE (revoked_at IS NULL);


--
-- Name: users_email_uniq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX users_email_uniq ON public.users USING btree (lower(email));


--
-- Name: users_role_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_role_idx ON public.users USING btree (role) WHERE is_active;


--
-- Name: vehicles_hub_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX vehicles_hub_idx ON public.vehicles USING btree (hub_id) WHERE is_active;


--
-- Name: driver_positions_2026_07_driver_id_recorded_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.driver_positions_driver_time_idx ATTACH PARTITION public.driver_positions_2026_07_driver_id_recorded_at_idx;


--
-- Name: driver_positions_2026_07_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.driver_positions_pkey ATTACH PARTITION public.driver_positions_2026_07_pkey;


--
-- Name: driver_positions_2026_07_route_id_recorded_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.driver_positions_route_idx ATTACH PARTITION public.driver_positions_2026_07_route_id_recorded_at_idx;


--
-- Name: driver_positions_2026_08_driver_id_recorded_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.driver_positions_driver_time_idx ATTACH PARTITION public.driver_positions_2026_08_driver_id_recorded_at_idx;


--
-- Name: driver_positions_2026_08_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.driver_positions_pkey ATTACH PARTITION public.driver_positions_2026_08_pkey;


--
-- Name: driver_positions_2026_08_route_id_recorded_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.driver_positions_route_idx ATTACH PARTITION public.driver_positions_2026_08_route_id_recorded_at_idx;


--
-- Name: driver_positions_2026_09_driver_id_recorded_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.driver_positions_driver_time_idx ATTACH PARTITION public.driver_positions_2026_09_driver_id_recorded_at_idx;


--
-- Name: driver_positions_2026_09_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.driver_positions_pkey ATTACH PARTITION public.driver_positions_2026_09_pkey;


--
-- Name: driver_positions_2026_09_route_id_recorded_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.driver_positions_route_idx ATTACH PARTITION public.driver_positions_2026_09_route_id_recorded_at_idx;


--
-- Name: driver_positions_2026_10_driver_id_recorded_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.driver_positions_driver_time_idx ATTACH PARTITION public.driver_positions_2026_10_driver_id_recorded_at_idx;


--
-- Name: driver_positions_2026_10_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.driver_positions_pkey ATTACH PARTITION public.driver_positions_2026_10_pkey;


--
-- Name: driver_positions_2026_10_route_id_recorded_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.driver_positions_route_idx ATTACH PARTITION public.driver_positions_2026_10_route_id_recorded_at_idx;


--
-- Name: driver_positions_2026_11_driver_id_recorded_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.driver_positions_driver_time_idx ATTACH PARTITION public.driver_positions_2026_11_driver_id_recorded_at_idx;


--
-- Name: driver_positions_2026_11_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.driver_positions_pkey ATTACH PARTITION public.driver_positions_2026_11_pkey;


--
-- Name: driver_positions_2026_11_route_id_recorded_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.driver_positions_route_idx ATTACH PARTITION public.driver_positions_2026_11_route_id_recorded_at_idx;


--
-- Name: alerts alerts_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER alerts_no_test_data BEFORE INSERT OR UPDATE ON public.alerts FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: api_clients api_clients_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER api_clients_touch BEFORE UPDATE ON public.api_clients FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: audit_log audit_log_no_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON public.audit_log FOR EACH ROW EXECUTE FUNCTION app.audit_log_is_append_only();


--
-- Name: audit_log audit_log_no_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON public.audit_log FOR EACH ROW EXECUTE FUNCTION app.audit_log_is_append_only();


--
-- Name: boxes boxes_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER boxes_no_test_data BEFORE INSERT OR UPDATE ON public.boxes FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: boxes boxes_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER boxes_touch BEFORE UPDATE ON public.boxes FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: cities cities_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER cities_no_test_data BEFORE INSERT OR UPDATE ON public.cities FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: cities cities_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER cities_touch BEFORE UPDATE ON public.cities FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: custody_transfers custody_transfers_guard_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER custody_transfers_guard_delete BEFORE DELETE ON public.custody_transfers FOR EACH ROW EXECUTE FUNCTION app.guard_operational_delete();


--
-- Name: custom_roles custom_roles_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER custom_roles_touch BEFORE UPDATE ON public.custom_roles FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: documents documents_guard_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER documents_guard_delete BEFORE DELETE ON public.documents FOR EACH ROW EXECUTE FUNCTION app.guard_operational_delete();


--
-- Name: documents documents_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER documents_no_test_data BEFORE INSERT OR UPDATE ON public.documents FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: driver_positions driver_positions_last; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER driver_positions_last AFTER INSERT ON public.driver_positions FOR EACH ROW EXECUTE FUNCTION app.upsert_driver_last_position();


--
-- Name: driver_positions driver_positions_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER driver_positions_no_test_data BEFORE INSERT OR UPDATE ON public.driver_positions FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: drivers drivers_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER drivers_no_test_data BEFORE INSERT OR UPDATE ON public.drivers FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: drivers drivers_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER drivers_touch BEFORE UPDATE ON public.drivers FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: facilities facilities_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER facilities_no_test_data BEFORE INSERT OR UPDATE ON public.facilities FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: facilities facilities_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER facilities_touch BEFORE UPDATE ON public.facilities FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: hubs hubs_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER hubs_no_test_data BEFORE INSERT OR UPDATE ON public.hubs FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: hubs hubs_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER hubs_touch BEFORE UPDATE ON public.hubs FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: operational_settings operational_settings_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER operational_settings_touch BEFORE UPDATE ON public.operational_settings FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: plan_days plan_days_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER plan_days_touch BEFORE UPDATE ON public.plan_days FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: plans plans_guard_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER plans_guard_delete BEFORE DELETE ON public.plans FOR EACH ROW EXECUTE FUNCTION app.guard_operational_delete();


--
-- Name: plans plans_guard_transition; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER plans_guard_transition BEFORE UPDATE ON public.plans FOR EACH ROW EXECUTE FUNCTION app.guard_plan_transition();


--
-- Name: plans plans_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER plans_no_test_data BEFORE INSERT OR UPDATE ON public.plans FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: plans plans_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER plans_touch BEFORE UPDATE ON public.plans FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: regions regions_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER regions_no_test_data BEFORE INSERT OR UPDATE ON public.regions FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: regions regions_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER regions_touch BEFORE UPDATE ON public.regions FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: route_stops route_stops_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER route_stops_touch BEFORE UPDATE ON public.route_stops FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: routes routes_guard_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER routes_guard_delete BEFORE DELETE ON public.routes FOR EACH ROW EXECUTE FUNCTION app.guard_operational_delete();


--
-- Name: routes routes_guard_publish; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER routes_guard_publish BEFORE UPDATE ON public.routes FOR EACH ROW EXECUTE FUNCTION app.guard_route_publish();


--
-- Name: routes routes_guard_transition; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER routes_guard_transition BEFORE UPDATE ON public.routes FOR EACH ROW EXECUTE FUNCTION app.guard_route_transition();


--
-- Name: routes routes_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER routes_no_test_data BEFORE INSERT OR UPDATE ON public.routes FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: routes routes_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER routes_touch BEFORE UPDATE ON public.routes FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: schedule_imports schedule_imports_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER schedule_imports_no_test_data BEFORE INSERT OR UPDATE ON public.schedule_imports FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: schedule_imports schedule_imports_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER schedule_imports_touch BEFORE UPDATE ON public.schedule_imports FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: sensors sensors_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER sensors_no_test_data BEFORE INSERT OR UPDATE ON public.sensors FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: sensors sensors_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER sensors_touch BEFORE UPDATE ON public.sensors FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: shipment_events shipment_events_guard_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER shipment_events_guard_delete BEFORE DELETE ON public.shipment_events FOR EACH ROW EXECUTE FUNCTION app.guard_operational_delete();


--
-- Name: shipment_exceptions shipment_exceptions_guard_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER shipment_exceptions_guard_delete BEFORE DELETE ON public.shipment_exceptions FOR EACH ROW EXECUTE FUNCTION app.guard_operational_delete();


--
-- Name: shipment_exceptions shipment_exceptions_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER shipment_exceptions_no_test_data BEFORE INSERT OR UPDATE ON public.shipment_exceptions FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: shipments shipments_guard_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER shipments_guard_delete BEFORE DELETE ON public.shipments FOR EACH ROW EXECUTE FUNCTION app.guard_operational_delete();


--
-- Name: shipments shipments_guard_transition; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER shipments_guard_transition BEFORE UPDATE ON public.shipments FOR EACH ROW EXECUTE FUNCTION app.guard_shipment_transition();


--
-- Name: shipments shipments_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER shipments_no_test_data BEFORE INSERT OR UPDATE ON public.shipments FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: shipments shipments_record_status; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER shipments_record_status AFTER UPDATE ON public.shipments FOR EACH ROW EXECUTE FUNCTION app.record_shipment_status_change();


--
-- Name: shipments shipments_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER shipments_touch BEFORE UPDATE ON public.shipments FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: system_events system_events_notify; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER system_events_notify AFTER INSERT ON public.system_events FOR EACH ROW EXECUTE FUNCTION app.publish_system_event();


--
-- Name: temperature_ranges temperature_ranges_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER temperature_ranges_touch BEFORE UPDATE ON public.temperature_ranges FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: temperature_readings temperature_readings_guard_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER temperature_readings_guard_delete BEFORE DELETE ON public.temperature_readings FOR EACH ROW EXECUTE FUNCTION app.guard_operational_delete();


--
-- Name: temperature_readings temperature_readings_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER temperature_readings_no_test_data BEFORE INSERT OR UPDATE ON public.temperature_readings FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: users users_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER users_no_test_data BEFORE INSERT OR UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: users users_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER users_touch BEFORE UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: vehicles vehicles_no_test_data; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vehicles_no_test_data BEFORE INSERT OR UPDATE ON public.vehicles FOR EACH ROW EXECUTE FUNCTION app.guard_no_test_data_in_production();


--
-- Name: vehicles vehicles_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vehicles_touch BEFORE UPDATE ON public.vehicles FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();


--
-- Name: alerts alerts_acknowledged_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_acknowledged_by_fkey FOREIGN KEY (acknowledged_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: alerts alerts_driver_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_driver_id_fkey FOREIGN KEY (driver_id) REFERENCES public.drivers(id) ON DELETE SET NULL;


--
-- Name: alerts alerts_hub_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_hub_id_fkey FOREIGN KEY (hub_id) REFERENCES public.hubs(id) ON DELETE CASCADE;


--
-- Name: alerts alerts_region_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_region_id_fkey FOREIGN KEY (region_id) REFERENCES public.regions(id) ON DELETE CASCADE;


--
-- Name: alerts alerts_responsible_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_responsible_user_id_fkey FOREIGN KEY (responsible_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: alerts alerts_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.routes(id) ON DELETE CASCADE;


--
-- Name: alerts alerts_shipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES public.shipments(id) ON DELETE CASCADE;


--
-- Name: api_clients api_clients_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_clients
    ADD CONSTRAINT api_clients_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: api_clients api_clients_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_clients
    ADD CONSTRAINT api_clients_facility_id_fkey FOREIGN KEY (facility_id) REFERENCES public.facilities(id) ON DELETE CASCADE;


--
-- Name: availability_exceptions availability_exceptions_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.availability_exceptions
    ADD CONSTRAINT availability_exceptions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: boxes boxes_hub_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.boxes
    ADD CONSTRAINT boxes_hub_id_fkey FOREIGN KEY (hub_id) REFERENCES public.hubs(id) ON DELETE RESTRICT;


--
-- Name: cities cities_region_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cities
    ADD CONSTRAINT cities_region_id_fkey FOREIGN KEY (region_id) REFERENCES public.regions(id) ON DELETE RESTRICT;


--
-- Name: custody_transfers custody_transfers_box_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.custody_transfers
    ADD CONSTRAINT custody_transfers_box_id_fkey FOREIGN KEY (box_id) REFERENCES public.boxes(id) ON DELETE SET NULL;


--
-- Name: custody_transfers custody_transfers_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.custody_transfers
    ADD CONSTRAINT custody_transfers_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE SET NULL;


--
-- Name: custody_transfers custody_transfers_shipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.custody_transfers
    ADD CONSTRAINT custody_transfers_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES public.shipments(id) ON DELETE CASCADE;


--
-- Name: documents documents_exception_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_exception_fk FOREIGN KEY (exception_id) REFERENCES public.shipment_exceptions(id) ON DELETE CASCADE;


--
-- Name: documents documents_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.routes(id) ON DELETE CASCADE;


--
-- Name: documents documents_route_stop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES public.route_stops(id) ON DELETE SET NULL;


--
-- Name: documents documents_shipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES public.shipments(id) ON DELETE CASCADE;


--
-- Name: documents documents_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: driver_estimations driver_estimations_hub_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_estimations
    ADD CONSTRAINT driver_estimations_hub_id_fkey FOREIGN KEY (hub_id) REFERENCES public.hubs(id) ON DELETE CASCADE;


--
-- Name: driver_estimations driver_estimations_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_estimations
    ADD CONSTRAINT driver_estimations_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.plans(id) ON DELETE CASCADE;


--
-- Name: driver_last_position driver_last_position_driver_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_last_position
    ADD CONSTRAINT driver_last_position_driver_id_fkey FOREIGN KEY (driver_id) REFERENCES public.drivers(id) ON DELETE CASCADE;


--
-- Name: driver_last_position driver_last_position_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_last_position
    ADD CONSTRAINT driver_last_position_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.routes(id) ON DELETE SET NULL;


--
-- Name: drivers drivers_hub_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drivers
    ADD CONSTRAINT drivers_hub_id_fkey FOREIGN KEY (hub_id) REFERENCES public.hubs(id) ON DELETE RESTRICT;


--
-- Name: drivers drivers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drivers
    ADD CONSTRAINT drivers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: facilities facilities_city_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facilities
    ADD CONSTRAINT facilities_city_id_fkey FOREIGN KEY (city_id) REFERENCES public.cities(id) ON DELETE RESTRICT;


--
-- Name: facilities facilities_default_hub_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facilities
    ADD CONSTRAINT facilities_default_hub_id_fkey FOREIGN KEY (default_hub_id) REFERENCES public.hubs(id) ON DELETE SET NULL;


--
-- Name: facilities facilities_region_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facilities
    ADD CONSTRAINT facilities_region_id_fkey FOREIGN KEY (region_id) REFERENCES public.regions(id) ON DELETE RESTRICT;


--
-- Name: facility_contacts facility_contacts_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.facility_contacts
    ADD CONSTRAINT facility_contacts_facility_id_fkey FOREIGN KEY (facility_id) REFERENCES public.facilities(id) ON DELETE CASCADE;


--
-- Name: hubs hubs_city_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hubs
    ADD CONSTRAINT hubs_city_id_fkey FOREIGN KEY (city_id) REFERENCES public.cities(id) ON DELETE RESTRICT;


--
-- Name: hubs hubs_region_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hubs
    ADD CONSTRAINT hubs_region_id_fkey FOREIGN KEY (region_id) REFERENCES public.regions(id) ON DELETE RESTRICT;


--
-- Name: import_rows import_rows_import_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.import_rows
    ADD CONSTRAINT import_rows_import_id_fkey FOREIGN KEY (import_id) REFERENCES public.schedule_imports(id) ON DELETE CASCADE;


--
-- Name: notifications notifications_alert_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_alert_id_fkey FOREIGN KEY (alert_id) REFERENCES public.alerts(id) ON DELETE SET NULL;


--
-- Name: notifications notifications_hub_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_hub_id_fkey FOREIGN KEY (hub_id) REFERENCES public.hubs(id) ON DELETE SET NULL;


--
-- Name: notifications notifications_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.routes(id) ON DELETE SET NULL;


--
-- Name: notifications notifications_shipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES public.shipments(id) ON DELETE SET NULL;


--
-- Name: notifications notifications_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: operational_settings operational_settings_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operational_settings
    ADD CONSTRAINT operational_settings_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: password_resets password_resets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: plan_days plan_days_hub_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plan_days
    ADD CONSTRAINT plan_days_hub_id_fkey FOREIGN KEY (hub_id) REFERENCES public.hubs(id) ON DELETE CASCADE;


--
-- Name: plan_days plan_days_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plan_days
    ADD CONSTRAINT plan_days_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.plans(id) ON DELETE CASCADE;


--
-- Name: plan_days plan_days_published_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plan_days
    ADD CONSTRAINT plan_days_published_by_fkey FOREIGN KEY (published_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: plan_warnings plan_warnings_hub_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plan_warnings
    ADD CONSTRAINT plan_warnings_hub_id_fkey FOREIGN KEY (hub_id) REFERENCES public.hubs(id) ON DELETE CASCADE;


--
-- Name: plan_warnings plan_warnings_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plan_warnings
    ADD CONSTRAINT plan_warnings_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.plans(id) ON DELETE CASCADE;


--
-- Name: plan_warnings plan_warnings_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plan_warnings
    ADD CONSTRAINT plan_warnings_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.routes(id) ON DELETE CASCADE;


--
-- Name: plan_warnings plan_warnings_shipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plan_warnings
    ADD CONSTRAINT plan_warnings_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES public.shipments(id) ON DELETE CASCADE;


--
-- Name: plans plans_approved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plans
    ADD CONSTRAINT plans_approved_by_fkey FOREIGN KEY (approved_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: plans plans_baseline_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plans
    ADD CONSTRAINT plans_baseline_plan_id_fkey FOREIGN KEY (baseline_plan_id) REFERENCES public.plans(id) ON DELETE SET NULL;


--
-- Name: plans plans_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plans
    ADD CONSTRAINT plans_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: plans plans_import_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plans
    ADD CONSTRAINT plans_import_id_fkey FOREIGN KEY (import_id) REFERENCES public.schedule_imports(id) ON DELETE SET NULL;


--
-- Name: route_revisions route_revisions_changed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.route_revisions
    ADD CONSTRAINT route_revisions_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: route_revisions route_revisions_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.route_revisions
    ADD CONSTRAINT route_revisions_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.routes(id) ON DELETE CASCADE;


--
-- Name: route_stops route_stops_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.route_stops
    ADD CONSTRAINT route_stops_facility_id_fkey FOREIGN KEY (facility_id) REFERENCES public.facilities(id) ON DELETE RESTRICT;


--
-- Name: route_stops route_stops_hub_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.route_stops
    ADD CONSTRAINT route_stops_hub_id_fkey FOREIGN KEY (hub_id) REFERENCES public.hubs(id) ON DELETE RESTRICT;


--
-- Name: route_stops route_stops_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.route_stops
    ADD CONSTRAINT route_stops_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.routes(id) ON DELETE CASCADE;


--
-- Name: route_stops route_stops_shipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.route_stops
    ADD CONSTRAINT route_stops_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES public.shipments(id) ON DELETE CASCADE;


--
-- Name: routes routes_assigned_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_assigned_by_fkey FOREIGN KEY (assigned_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: routes routes_box_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_box_id_fkey FOREIGN KEY (box_id) REFERENCES public.boxes(id) ON DELETE SET NULL;


--
-- Name: routes routes_driver_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_driver_id_fkey FOREIGN KEY (driver_id) REFERENCES public.drivers(id) ON DELETE SET NULL;


--
-- Name: routes routes_hub_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_hub_id_fkey FOREIGN KEY (hub_id) REFERENCES public.hubs(id) ON DELETE RESTRICT;


--
-- Name: routes routes_plan_day_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_plan_day_id_fkey FOREIGN KEY (plan_day_id) REFERENCES public.plan_days(id) ON DELETE CASCADE;


--
-- Name: routes routes_plan_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES public.plans(id) ON DELETE CASCADE;


--
-- Name: routes routes_previous_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_previous_route_id_fkey FOREIGN KEY (previous_route_id) REFERENCES public.routes(id) ON DELETE SET NULL;


--
-- Name: routes routes_published_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_published_by_fkey FOREIGN KEY (published_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: routes routes_region_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_region_id_fkey FOREIGN KEY (region_id) REFERENCES public.regions(id) ON DELETE RESTRICT;


--
-- Name: routes routes_vehicle_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_vehicle_id_fkey FOREIGN KEY (vehicle_id) REFERENCES public.vehicles(id) ON DELETE SET NULL;


--
-- Name: schedule_imports schedule_imports_committed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_imports
    ADD CONSTRAINT schedule_imports_committed_by_fkey FOREIGN KEY (committed_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: schedule_imports schedule_imports_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedule_imports
    ADD CONSTRAINT schedule_imports_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: sensors sensors_box_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensors
    ADD CONSTRAINT sensors_box_id_fkey FOREIGN KEY (box_id) REFERENCES public.boxes(id) ON DELETE SET NULL;


--
-- Name: sensors sensors_vehicle_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensors
    ADD CONSTRAINT sensors_vehicle_id_fkey FOREIGN KEY (vehicle_id) REFERENCES public.vehicles(id) ON DELETE SET NULL;


--
-- Name: shipment_events shipment_events_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_events
    ADD CONSTRAINT shipment_events_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: shipment_events shipment_events_driver_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_events
    ADD CONSTRAINT shipment_events_driver_id_fkey FOREIGN KEY (driver_id) REFERENCES public.drivers(id) ON DELETE SET NULL;


--
-- Name: shipment_events shipment_events_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_events
    ADD CONSTRAINT shipment_events_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.routes(id) ON DELETE SET NULL;


--
-- Name: shipment_events shipment_events_route_stop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_events
    ADD CONSTRAINT shipment_events_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES public.route_stops(id) ON DELETE SET NULL;


--
-- Name: shipment_events shipment_events_shipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_events
    ADD CONSTRAINT shipment_events_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES public.shipments(id) ON DELETE CASCADE;


--
-- Name: shipment_exceptions shipment_exceptions_hub_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_exceptions
    ADD CONSTRAINT shipment_exceptions_hub_id_fkey FOREIGN KEY (hub_id) REFERENCES public.hubs(id) ON DELETE RESTRICT;


--
-- Name: shipment_exceptions shipment_exceptions_reported_by_driver_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_exceptions
    ADD CONSTRAINT shipment_exceptions_reported_by_driver_fkey FOREIGN KEY (reported_by_driver) REFERENCES public.drivers(id) ON DELETE SET NULL;


--
-- Name: shipment_exceptions shipment_exceptions_reported_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_exceptions
    ADD CONSTRAINT shipment_exceptions_reported_by_fkey FOREIGN KEY (reported_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: shipment_exceptions shipment_exceptions_resolved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_exceptions
    ADD CONSTRAINT shipment_exceptions_resolved_by_fkey FOREIGN KEY (resolved_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: shipment_exceptions shipment_exceptions_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_exceptions
    ADD CONSTRAINT shipment_exceptions_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.routes(id) ON DELETE SET NULL;


--
-- Name: shipment_exceptions shipment_exceptions_route_stop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_exceptions
    ADD CONSTRAINT shipment_exceptions_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES public.route_stops(id) ON DELETE SET NULL;


--
-- Name: shipment_exceptions shipment_exceptions_shipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_exceptions
    ADD CONSTRAINT shipment_exceptions_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES public.shipments(id) ON DELETE CASCADE;


--
-- Name: shipment_status_history shipment_status_history_changed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_status_history
    ADD CONSTRAINT shipment_status_history_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: shipment_status_history shipment_status_history_shipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipment_status_history
    ADD CONSTRAINT shipment_status_history_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES public.shipments(id) ON DELETE CASCADE;


--
-- Name: shipments shipments_approved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_approved_by_fkey FOREIGN KEY (approved_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: shipments shipments_box_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_box_id_fkey FOREIGN KEY (box_id) REFERENCES public.boxes(id) ON DELETE SET NULL;


--
-- Name: shipments shipments_city_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_city_id_fkey FOREIGN KEY (city_id) REFERENCES public.cities(id) ON DELETE RESTRICT;


--
-- Name: shipments shipments_driver_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_driver_id_fkey FOREIGN KEY (driver_id) REFERENCES public.drivers(id) ON DELETE SET NULL;


--
-- Name: shipments shipments_dropoff_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_dropoff_facility_id_fkey FOREIGN KEY (dropoff_facility_id) REFERENCES public.facilities(id) ON DELETE RESTRICT;


--
-- Name: shipments shipments_hub_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_hub_id_fkey FOREIGN KEY (hub_id) REFERENCES public.hubs(id) ON DELETE RESTRICT;


--
-- Name: shipments shipments_import_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_import_id_fkey FOREIGN KEY (import_id) REFERENCES public.schedule_imports(id) ON DELETE SET NULL;


--
-- Name: shipments shipments_pickup_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_pickup_facility_id_fkey FOREIGN KEY (pickup_facility_id) REFERENCES public.facilities(id) ON DELETE RESTRICT;


--
-- Name: shipments shipments_region_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_region_id_fkey FOREIGN KEY (region_id) REFERENCES public.regions(id) ON DELETE RESTRICT;


--
-- Name: shipments shipments_requested_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_requested_by_fkey FOREIGN KEY (requested_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: shipments shipments_requester_facility_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_requester_facility_id_fkey FOREIGN KEY (requester_facility_id) REFERENCES public.facilities(id) ON DELETE SET NULL;


--
-- Name: shipments shipments_route_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_route_fk FOREIGN KEY (route_id) REFERENCES public.routes(id) ON DELETE SET NULL;


--
-- Name: shipments shipments_vehicle_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shipments
    ADD CONSTRAINT shipments_vehicle_id_fkey FOREIGN KEY (vehicle_id) REFERENCES public.vehicles(id) ON DELETE SET NULL;


--
-- Name: temperature_breaches temperature_breaches_box_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temperature_breaches
    ADD CONSTRAINT temperature_breaches_box_id_fkey FOREIGN KEY (box_id) REFERENCES public.boxes(id) ON DELETE SET NULL;


--
-- Name: temperature_breaches temperature_breaches_resolved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temperature_breaches
    ADD CONSTRAINT temperature_breaches_resolved_by_fkey FOREIGN KEY (resolved_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: temperature_breaches temperature_breaches_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temperature_breaches
    ADD CONSTRAINT temperature_breaches_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.routes(id) ON DELETE SET NULL;


--
-- Name: temperature_breaches temperature_breaches_sensor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temperature_breaches
    ADD CONSTRAINT temperature_breaches_sensor_id_fkey FOREIGN KEY (sensor_id) REFERENCES public.sensors(id) ON DELETE SET NULL;


--
-- Name: temperature_breaches temperature_breaches_shipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temperature_breaches
    ADD CONSTRAINT temperature_breaches_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES public.shipments(id) ON DELETE CASCADE;


--
-- Name: temperature_readings temperature_readings_box_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temperature_readings
    ADD CONSTRAINT temperature_readings_box_id_fkey FOREIGN KEY (box_id) REFERENCES public.boxes(id) ON DELETE SET NULL;


--
-- Name: temperature_readings temperature_readings_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temperature_readings
    ADD CONSTRAINT temperature_readings_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.routes(id) ON DELETE SET NULL;


--
-- Name: temperature_readings temperature_readings_sensor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temperature_readings
    ADD CONSTRAINT temperature_readings_sensor_id_fkey FOREIGN KEY (sensor_id) REFERENCES public.sensors(id) ON DELETE SET NULL;


--
-- Name: temperature_readings temperature_readings_shipment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.temperature_readings
    ADD CONSTRAINT temperature_readings_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES public.shipments(id) ON DELETE CASCADE;


--
-- Name: user_scopes user_scopes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_scopes
    ADD CONSTRAINT user_scopes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_sessions user_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: users users_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: users users_custom_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_custom_role_id_fkey FOREIGN KEY (custom_role_id) REFERENCES public.custom_roles(id) ON DELETE SET NULL;


--
-- Name: vehicles vehicles_hub_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_hub_id_fkey FOREIGN KEY (hub_id) REFERENCES public.hubs(id) ON DELETE RESTRICT;


--
-- Name: alerts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.alerts ENABLE ROW LEVEL SECURITY;

--
-- Name: alerts alerts_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY alerts_read ON public.alerts FOR SELECT USING ((app.is_global_scope() OR ((NOT app.is_driver_role()) AND (hub_id IS NOT NULL) AND (hub_id = ANY (app.current_hub_ids()))) OR (app.is_driver_role() AND (driver_id = app.current_driver_id())) OR (responsible_user_id = app.current_user_id()) OR (EXISTS ( SELECT 1
   FROM public.shipments s
  WHERE (s.id = alerts.shipment_id)))));


--
-- Name: alerts alerts_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY alerts_write ON public.alerts USING ((app.is_global_scope() OR ((hub_id IS NOT NULL) AND (hub_id = ANY (app.current_hub_ids()))))) WITH CHECK (true);


--
-- Name: api_clients; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.api_clients ENABLE ROW LEVEL SECURITY;

--
-- Name: api_clients api_clients_admin; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY api_clients_admin ON public.api_clients USING (app.is_admin()) WITH CHECK (app.is_admin());


--
-- Name: audit_log audit_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY audit_insert ON public.audit_log FOR INSERT WITH CHECK ((app.is_authenticated() OR (app.current_role_key() = 'ANONYMOUS'::text)));


--
-- Name: audit_log; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;

--
-- Name: audit_log audit_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY audit_read ON public.audit_log FOR SELECT USING ((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text, 'AUDITOR'::text])));


--
-- Name: availability_exceptions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.availability_exceptions ENABLE ROW LEVEL SECURITY;

--
-- Name: availability_exceptions availability_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY availability_read ON public.availability_exceptions FOR SELECT USING (app.is_authenticated());


--
-- Name: availability_exceptions availability_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY availability_write ON public.availability_exceptions USING ((app.is_admin() OR (app.current_role_key() = 'HUB_SUPERVISOR'::text))) WITH CHECK ((app.is_admin() OR (app.current_role_key() = 'HUB_SUPERVISOR'::text)));


--
-- Name: boxes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.boxes ENABLE ROW LEVEL SECURITY;

--
-- Name: boxes boxes_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY boxes_read ON public.boxes FOR SELECT USING (app.hub_in_scope(hub_id));


--
-- Name: boxes boxes_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY boxes_write ON public.boxes USING (app.is_admin()) WITH CHECK (app.is_admin());


--
-- Name: temperature_breaches breaches_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY breaches_read ON public.temperature_breaches FOR SELECT USING ((app.is_global_scope() OR (shipment_id IS NULL) OR (EXISTS ( SELECT 1
   FROM public.shipments s
  WHERE (s.id = temperature_breaches.shipment_id)))));


--
-- Name: temperature_breaches breaches_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY breaches_write ON public.temperature_breaches USING ((app.is_global_scope() OR (EXISTS ( SELECT 1
   FROM public.shipments s
  WHERE (s.id = temperature_breaches.shipment_id))))) WITH CHECK (true);


--
-- Name: cities; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cities ENABLE ROW LEVEL SECURITY;

--
-- Name: cities cities_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cities_read ON public.cities FOR SELECT USING (app.is_authenticated());


--
-- Name: cities cities_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY cities_write ON public.cities USING (app.is_admin()) WITH CHECK (app.is_admin());


--
-- Name: custody_transfers custody_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY custody_all ON public.custody_transfers USING ((EXISTS ( SELECT 1
   FROM public.shipments s
  WHERE (s.id = custody_transfers.shipment_id)))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.shipments s
  WHERE (s.id = custody_transfers.shipment_id))));


--
-- Name: custody_transfers; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.custody_transfers ENABLE ROW LEVEL SECURITY;

--
-- Name: custom_roles; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.custom_roles ENABLE ROW LEVEL SECURITY;

--
-- Name: custom_roles custom_roles_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY custom_roles_read ON public.custom_roles FOR SELECT USING (app.is_authenticated());


--
-- Name: custom_roles custom_roles_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY custom_roles_write ON public.custom_roles USING (app.is_admin()) WITH CHECK (app.is_admin());


--
-- Name: documents; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;

--
-- Name: documents documents_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY documents_all ON public.documents USING (((shipment_id IS NULL) OR (EXISTS ( SELECT 1
   FROM public.shipments s
  WHERE (s.id = documents.shipment_id))))) WITH CHECK (((shipment_id IS NULL) OR (EXISTS ( SELECT 1
   FROM public.shipments s
  WHERE (s.id = documents.shipment_id)))));


--
-- Name: driver_estimations; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.driver_estimations ENABLE ROW LEVEL SECURITY;

--
-- Name: driver_estimations driver_estimations_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY driver_estimations_read ON public.driver_estimations FOR SELECT USING (app.hub_in_scope(hub_id));


--
-- Name: driver_estimations driver_estimations_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY driver_estimations_write ON public.driver_estimations USING ((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text]))) WITH CHECK ((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text])));


--
-- Name: driver_last_position; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.driver_last_position ENABLE ROW LEVEL SECURITY;

--
-- Name: driver_positions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.driver_positions ENABLE ROW LEVEL SECURITY;

--
-- Name: drivers; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.drivers ENABLE ROW LEVEL SECURITY;

--
-- Name: drivers drivers_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY drivers_read ON public.drivers FOR SELECT USING ((((NOT app.is_driver_role()) AND app.hub_in_scope(hub_id)) OR (app.is_driver_role() AND (id = app.current_driver_id()))));


--
-- Name: drivers drivers_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY drivers_write ON public.drivers USING (app.is_admin()) WITH CHECK (app.is_admin());


--
-- Name: shipment_exceptions exceptions_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY exceptions_all ON public.shipment_exceptions USING ((app.hub_in_scope(hub_id) OR (EXISTS ( SELECT 1
   FROM public.shipments s
  WHERE (s.id = shipment_exceptions.shipment_id))))) WITH CHECK ((app.hub_in_scope(hub_id) OR (EXISTS ( SELECT 1
   FROM public.shipments s
  WHERE (s.id = shipment_exceptions.shipment_id)))));


--
-- Name: facilities; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.facilities ENABLE ROW LEVEL SECURITY;

--
-- Name: facilities facilities_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY facilities_read ON public.facilities FOR SELECT USING (
CASE
    WHEN app.is_external_role() THEN ((id = app.current_facility_id()) OR (facility_type = ANY (ARRAY['LABORATORY'::text, 'BLOOD_BANK'::text])))
    ELSE app.is_authenticated()
END);


--
-- Name: POLICY facilities_read ON facilities; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON POLICY facilities_read ON public.facilities IS 'مقدم الطلب الخارجي يرى جهته + جهات التسليم الممكنة فقط (مختبرات وبنوك دم)';


--
-- Name: facilities facilities_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY facilities_write ON public.facilities USING (app.is_admin()) WITH CHECK (app.is_admin());


--
-- Name: facility_contacts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.facility_contacts ENABLE ROW LEVEL SECURITY;

--
-- Name: facility_contacts facility_contacts_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY facility_contacts_read ON public.facility_contacts FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.facilities f
  WHERE (f.id = facility_contacts.facility_id))));


--
-- Name: facility_contacts facility_contacts_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY facility_contacts_write ON public.facility_contacts USING (app.is_admin()) WITH CHECK (app.is_admin());


--
-- Name: hubs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.hubs ENABLE ROW LEVEL SECURITY;

--
-- Name: hubs hubs_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY hubs_read ON public.hubs FOR SELECT USING ((app.is_global_scope() OR (id = ANY (app.current_hub_ids())) OR (app.is_driver_role() AND (EXISTS ( SELECT 1
   FROM public.drivers d
  WHERE ((d.id = app.current_driver_id()) AND (d.hub_id = hubs.id)))))));


--
-- Name: hubs hubs_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY hubs_write ON public.hubs USING (app.is_admin()) WITH CHECK (app.is_admin());


--
-- Name: import_rows; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.import_rows ENABLE ROW LEVEL SECURITY;

--
-- Name: import_rows import_rows_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY import_rows_read ON public.import_rows FOR SELECT USING ((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text, 'AUDITOR'::text])));


--
-- Name: import_rows import_rows_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY import_rows_write ON public.import_rows USING ((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text]))) WITH CHECK ((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text])));


--
-- Name: schedule_imports imports_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY imports_read ON public.schedule_imports FOR SELECT USING ((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text, 'AUDITOR'::text])));


--
-- Name: schedule_imports imports_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY imports_write ON public.schedule_imports USING ((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text]))) WITH CHECK ((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text])));


--
-- Name: driver_last_position last_position_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY last_position_read ON public.driver_last_position FOR SELECT USING ((app.is_global_scope() OR (app.is_driver_role() AND (driver_id = app.current_driver_id())) OR ((NOT app.is_driver_role()) AND (EXISTS ( SELECT 1
   FROM public.drivers d
  WHERE ((d.id = driver_last_position.driver_id) AND (d.hub_id = ANY (app.current_hub_ids()))))))));


--
-- Name: driver_last_position last_position_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY last_position_write ON public.driver_last_position USING (true) WITH CHECK (true);


--
-- Name: notifications; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;

--
-- Name: notifications notifications_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY notifications_insert ON public.notifications FOR INSERT WITH CHECK (app.is_authenticated());


--
-- Name: notifications notifications_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY notifications_read ON public.notifications FOR SELECT USING ((app.is_global_scope() OR ((hub_id IS NOT NULL) AND app.hub_in_scope(hub_id)) OR (user_id = app.current_user_id())));


--
-- Name: POLICY notifications_read ON notifications; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON POLICY notifications_read ON public.notifications IS 'المشرف يرى إشعارات مركزه، والمستخدم إشعاراته، والنطاق الوطني يرى الكل';


--
-- Name: notifications notifications_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY notifications_update ON public.notifications FOR UPDATE USING (app.is_global_scope()) WITH CHECK (app.is_global_scope());


--
-- Name: operational_settings; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.operational_settings ENABLE ROW LEVEL SECURITY;

--
-- Name: plan_days; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.plan_days ENABLE ROW LEVEL SECURITY;

--
-- Name: plan_days plan_days_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plan_days_read ON public.plan_days FOR SELECT USING (app.hub_in_scope(hub_id));


--
-- Name: plan_days plan_days_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plan_days_write ON public.plan_days USING (app.hub_in_scope(hub_id)) WITH CHECK (app.hub_in_scope(hub_id));


--
-- Name: plan_warnings; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.plan_warnings ENABLE ROW LEVEL SECURITY;

--
-- Name: plan_warnings plan_warnings_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plan_warnings_read ON public.plan_warnings FOR SELECT USING ((app.is_global_scope() OR ((hub_id IS NOT NULL) AND (hub_id = ANY (app.current_hub_ids()))) OR (EXISTS ( SELECT 1
   FROM public.routes r
  WHERE (r.id = plan_warnings.route_id)))));


--
-- Name: plan_warnings plan_warnings_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plan_warnings_write ON public.plan_warnings USING ((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text]))) WITH CHECK ((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text])));


--
-- Name: plans; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.plans ENABLE ROW LEVEL SECURITY;

--
-- Name: plans plans_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plans_read ON public.plans FOR SELECT USING ((app.is_global_scope() OR (EXISTS ( SELECT 1
   FROM public.plan_days pd
  WHERE ((pd.plan_id = plans.id) AND (pd.hub_id = ANY (app.current_hub_ids())))))));


--
-- Name: plans plans_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plans_write ON public.plans USING ((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text]))) WITH CHECK ((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text])));


--
-- Name: driver_positions positions_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY positions_insert ON public.driver_positions FOR INSERT WITH CHECK (((app.is_driver_role() AND (driver_id = app.current_driver_id())) OR app.is_global_scope()));


--
-- Name: driver_positions positions_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY positions_read ON public.driver_positions FOR SELECT USING ((app.is_global_scope() OR (app.is_driver_role() AND (driver_id = app.current_driver_id())) OR ((NOT app.is_driver_role()) AND (EXISTS ( SELECT 1
   FROM public.drivers d
  WHERE ((d.id = driver_positions.driver_id) AND (d.hub_id = ANY (app.current_hub_ids()))))))));


--
-- Name: regions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.regions ENABLE ROW LEVEL SECURITY;

--
-- Name: regions regions_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY regions_read ON public.regions FOR SELECT USING (app.is_authenticated());


--
-- Name: regions regions_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY regions_write ON public.regions USING (app.is_admin()) WITH CHECK (app.is_admin());


--
-- Name: route_revisions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.route_revisions ENABLE ROW LEVEL SECURITY;

--
-- Name: route_revisions route_revisions_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY route_revisions_insert ON public.route_revisions FOR INSERT WITH CHECK ((EXISTS ( SELECT 1
   FROM public.routes r
  WHERE (r.id = route_revisions.route_id))));


--
-- Name: route_revisions route_revisions_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY route_revisions_read ON public.route_revisions FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.routes r
  WHERE (r.id = route_revisions.route_id))));


--
-- Name: route_stops; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.route_stops ENABLE ROW LEVEL SECURITY;

--
-- Name: route_stops route_stops_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY route_stops_all ON public.route_stops USING ((EXISTS ( SELECT 1
   FROM public.routes r
  WHERE (r.id = route_stops.route_id)))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.routes r
  WHERE (r.id = route_stops.route_id))));


--
-- Name: routes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.routes ENABLE ROW LEVEL SECURITY;

--
-- Name: routes routes_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY routes_read ON public.routes FOR SELECT USING ((app.is_global_scope() OR ((NOT app.is_driver_role()) AND (hub_id = ANY (app.current_hub_ids()))) OR (app.is_driver_role() AND (driver_id = app.current_driver_id()) AND (status = ANY (ARRAY['PUBLISHED'::text, 'IN_PROGRESS'::text, 'COMPLETED'::text])))));


--
-- Name: routes routes_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY routes_write ON public.routes USING (((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text])) OR ((NOT app.is_driver_role()) AND (hub_id = ANY (app.current_hub_ids()))) OR (app.is_driver_role() AND (driver_id = app.current_driver_id())))) WITH CHECK (((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text])) OR ((NOT app.is_driver_role()) AND (hub_id = ANY (app.current_hub_ids()))) OR (app.is_driver_role() AND (driver_id = app.current_driver_id()))));


--
-- Name: schedule_imports; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.schedule_imports ENABLE ROW LEVEL SECURITY;

--
-- Name: sensors; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.sensors ENABLE ROW LEVEL SECURITY;

--
-- Name: sensors sensors_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sensors_read ON public.sensors FOR SELECT USING (app.is_authenticated());


--
-- Name: sensors sensors_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY sensors_write ON public.sensors USING (app.is_admin()) WITH CHECK (app.is_admin());


--
-- Name: operational_settings settings_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY settings_read ON public.operational_settings FOR SELECT USING (app.is_authenticated());


--
-- Name: operational_settings settings_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY settings_write ON public.operational_settings USING (app.is_admin()) WITH CHECK (app.is_admin());


--
-- Name: shipment_events; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.shipment_events ENABLE ROW LEVEL SECURITY;

--
-- Name: shipment_events shipment_events_all; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY shipment_events_all ON public.shipment_events USING ((EXISTS ( SELECT 1
   FROM public.shipments s
  WHERE (s.id = shipment_events.shipment_id)))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.shipments s
  WHERE (s.id = shipment_events.shipment_id))));


--
-- Name: shipment_exceptions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.shipment_exceptions ENABLE ROW LEVEL SECURITY;

--
-- Name: shipment_status_history; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.shipment_status_history ENABLE ROW LEVEL SECURITY;

--
-- Name: shipments; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.shipments ENABLE ROW LEVEL SECURITY;

--
-- Name: shipments shipments_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY shipments_insert ON public.shipments FOR INSERT WITH CHECK (((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text])) OR (app.is_external_role() AND (requester_facility_id = app.current_facility_id()) AND (request_kind = 'ON_DEMAND'::text)) OR ((app.current_role_key() = ANY (ARRAY['HUB_SUPERVISOR'::text, 'CONTROL_TOWER'::text])) AND (hub_id = ANY (app.current_hub_ids())))));


--
-- Name: shipments shipments_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY shipments_read ON public.shipments FOR SELECT USING ((app.is_global_scope() OR ((NOT app.is_driver_role()) AND (NOT app.is_external_role()) AND (hub_id = ANY (app.current_hub_ids()))) OR (app.is_driver_role() AND (driver_id = app.current_driver_id()) AND (status <> ALL (ARRAY['DRAFT'::text, 'VALIDATED'::text, 'PLANNED'::text]))) OR (app.is_external_role() AND (requester_facility_id = app.current_facility_id()))));


--
-- Name: shipments shipments_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY shipments_update ON public.shipments FOR UPDATE USING ((app.is_global_scope() OR ((NOT app.is_driver_role()) AND (NOT app.is_external_role()) AND (hub_id = ANY (app.current_hub_ids()))) OR (app.is_driver_role() AND (driver_id = app.current_driver_id())) OR (app.is_external_role() AND (requester_facility_id = app.current_facility_id())))) WITH CHECK ((app.is_global_scope() OR ((NOT app.is_driver_role()) AND (NOT app.is_external_role()) AND (hub_id = ANY (app.current_hub_ids()))) OR (app.is_driver_role() AND (driver_id = app.current_driver_id())) OR (app.is_external_role() AND (requester_facility_id = app.current_facility_id()))));


--
-- Name: shipment_status_history status_history_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY status_history_insert ON public.shipment_status_history FOR INSERT WITH CHECK (true);


--
-- Name: shipment_status_history status_history_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY status_history_read ON public.shipment_status_history FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.shipments s
  WHERE (s.id = shipment_status_history.shipment_id))));


--
-- Name: system_events; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.system_events ENABLE ROW LEVEL SECURITY;

--
-- Name: system_events system_events_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY system_events_insert ON public.system_events FOR INSERT WITH CHECK (true);


--
-- Name: system_events system_events_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY system_events_read ON public.system_events FOR SELECT USING ((app.is_global_scope() OR ((hub_id IS NOT NULL) AND (hub_id = ANY (app.current_hub_ids()))) OR (app.is_driver_role() AND (driver_id = app.current_driver_id())) OR (user_id = app.current_user_id())));


--
-- Name: temperature_breaches; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.temperature_breaches ENABLE ROW LEVEL SECURITY;

--
-- Name: temperature_readings temperature_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY temperature_insert ON public.temperature_readings FOR INSERT WITH CHECK (app.is_authenticated());


--
-- Name: temperature_readings temperature_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY temperature_read ON public.temperature_readings FOR SELECT USING ((app.is_global_scope() OR (shipment_id IS NULL) OR (EXISTS ( SELECT 1
   FROM public.shipments s
  WHERE (s.id = temperature_readings.shipment_id)))));


--
-- Name: temperature_readings; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.temperature_readings ENABLE ROW LEVEL SECURITY;

--
-- Name: user_scopes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_scopes ENABLE ROW LEVEL SECURITY;

--
-- Name: user_scopes user_scopes_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_scopes_read ON public.user_scopes FOR SELECT USING ((app.is_admin() OR (user_id = app.current_user_id()) OR (app.current_role_key() = ANY (ARRAY['CENTRAL_PLANNER'::text, 'AUDITOR'::text]))));


--
-- Name: user_scopes user_scopes_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_scopes_write ON public.user_scopes USING (app.is_admin()) WITH CHECK (app.is_admin());


--
-- Name: user_sessions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.user_sessions ENABLE ROW LEVEL SECURITY;

--
-- Name: user_sessions user_sessions_own; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY user_sessions_own ON public.user_sessions USING (((user_id = app.current_user_id()) OR app.is_admin())) WITH CHECK (((user_id = app.current_user_id()) OR app.is_admin()));


--
-- Name: users; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

--
-- Name: users users_admin_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY users_admin_write ON public.users USING (app.is_admin()) WITH CHECK (app.is_admin());


--
-- Name: users users_self_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY users_self_read ON public.users FOR SELECT USING ((app.is_admin() OR (id = app.current_user_id()) OR (app.current_role_key() = ANY (ARRAY['CENTRAL_PLANNER'::text, 'AUDITOR'::text, 'CONTROL_TOWER'::text])) OR ((app.current_role_key() = 'HUB_SUPERVISOR'::text) AND (EXISTS ( SELECT 1
   FROM public.user_scopes us
  WHERE ((us.user_id = users.id) AND (us.scope_type = 'HUB'::text) AND (us.scope_id = ANY (app.current_hub_ids()))))))));


--
-- Name: users users_self_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY users_self_update ON public.users FOR UPDATE USING ((id = app.current_user_id())) WITH CHECK ((id = app.current_user_id()));


--
-- Name: vehicles; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.vehicles ENABLE ROW LEVEL SECURITY;

--
-- Name: vehicles vehicles_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY vehicles_read ON public.vehicles FOR SELECT USING (app.hub_in_scope(hub_id));


--
-- Name: vehicles vehicles_write; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY vehicles_write ON public.vehicles USING (app.is_admin()) WITH CHECK (app.is_admin());


--
-- PostgreSQL database dump complete
--

\unrestrict KROpmAeDoxA7uSjmrdw1dQvf4dm1x9Z9LRaeMexfrn9OkJWbsu3ndCorm6Zq1Po

