-- =====================================================================
-- الترحيل 0005: أمن مستوى الصف (RLS) والصلاحيات
-- الضمانة الحقيقية للعزل: حتى لو أخطأ استعلام في طبقة الخدمة، لا يعيد
-- المحرك صفًا خارج نطاق المستخدم.
-- =====================================================================

-- ---------------------------------------------------------------------
-- الصلاحيات على مستوى الكائنات
-- ---------------------------------------------------------------------
GRANT USAGE ON SCHEMA public, app TO masar_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA app TO masar_app;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO masar_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO masar_app;

-- الحذف ممنوع افتراضيًا؛ يُمنح فقط حيث الحذف جزء من التشغيل الطبيعي
REVOKE DELETE ON ALL TABLES IN SCHEMA public FROM masar_app;
GRANT DELETE ON user_sessions, password_resets, import_rows,
                 facility_contacts, user_scopes, availability_exceptions,
                 route_stops, plan_warnings TO masar_app;

-- سجل التدقيق: إدراج وقراءة فقط
REVOKE UPDATE, DELETE ON audit_log FROM masar_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO masar_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO masar_app;

-- ---------------------------------------------------------------------
-- تفعيل RLS
-- ---------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'users','user_scopes','user_sessions','custom_roles',
        'regions','cities','hubs','facilities','facility_contacts',
        'drivers','vehicles','boxes','availability_exceptions',
        'operational_settings','audit_log','api_clients',
        'schedule_imports','import_rows','shipments','plans','plan_days','routes',
        'route_stops','plan_warnings','driver_estimations','route_revisions',
        'shipment_events','shipment_status_history','documents','shipment_exceptions',
        'alerts','driver_positions','driver_last_position','sensors',
        'temperature_readings','temperature_breaches','custody_transfers','system_events'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- دوال مساعدة للنطاق
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.is_driver_role()
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT app.current_role_key() = 'DRIVER'
$$;

CREATE OR REPLACE FUNCTION app.is_external_role()
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT app.current_role_key() IN ('EXTERNAL_REQUESTER','INTEGRATION')
$$;

CREATE OR REPLACE FUNCTION app.is_admin()
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT app.current_role_key() = 'ADMIN'
$$;

CREATE OR REPLACE FUNCTION app.is_authenticated()
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT app.current_user_id() IS NOT NULL
$$;

-- المستخدم يرى مركز الانطلاق إذا كان نطاقه وطنيًا أو المركز ضمن مراكزه
CREATE OR REPLACE FUNCTION app.hub_in_scope(p_hub_id uuid)
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT app.is_global_scope() OR p_hub_id = ANY (app.current_hub_ids())
$$;

-- ---------------------------------------------------------------------
-- البيانات المرجعية العامة: قراءة لكل مستخدم مصادَق، كتابة للمدير
-- ---------------------------------------------------------------------
CREATE POLICY regions_read ON regions FOR SELECT
    USING (app.is_authenticated());
CREATE POLICY regions_write ON regions FOR ALL
    USING (app.is_admin()) WITH CHECK (app.is_admin());

CREATE POLICY cities_read ON cities FOR SELECT
    USING (app.is_authenticated());
CREATE POLICY cities_write ON cities FOR ALL
    USING (app.is_admin()) WITH CHECK (app.is_admin());

-- مراكز الانطلاق: المشرف يرى مركزه فقط (§5)
CREATE POLICY hubs_read ON hubs FOR SELECT
    USING (
        app.is_global_scope()
        OR id = ANY (app.current_hub_ids())
        OR (app.is_driver_role() AND EXISTS (
                SELECT 1 FROM drivers d
                WHERE d.id = app.current_driver_id() AND d.hub_id = hubs.id))
    );
CREATE POLICY hubs_write ON hubs FOR ALL
    USING (app.is_admin()) WITH CHECK (app.is_admin());

-- الجهات: مقدم الطلب الخارجي يرى جهته فقط
CREATE POLICY facilities_read ON facilities FOR SELECT
    USING (
        CASE
            WHEN app.is_external_role() THEN id = app.current_facility_id()
            ELSE app.is_authenticated()
        END
    );
CREATE POLICY facilities_write ON facilities FOR ALL
    USING (app.is_admin()) WITH CHECK (app.is_admin());

CREATE POLICY facility_contacts_read ON facility_contacts FOR SELECT
    USING (EXISTS (SELECT 1 FROM facilities f WHERE f.id = facility_id));
CREATE POLICY facility_contacts_write ON facility_contacts FOR ALL
    USING (app.is_admin()) WITH CHECK (app.is_admin());

-- ---------------------------------------------------------------------
-- المستخدمون
-- ---------------------------------------------------------------------
CREATE POLICY users_self_read ON users FOR SELECT
    USING (
        app.is_admin()
        OR id = app.current_user_id()
        OR app.current_role_key() IN ('CENTRAL_PLANNER','AUDITOR','CONTROL_TOWER')
        OR (app.current_role_key() = 'HUB_SUPERVISOR' AND EXISTS (
                SELECT 1 FROM user_scopes us
                WHERE us.user_id = users.id AND us.scope_type = 'HUB'
                  AND us.scope_id = ANY (app.current_hub_ids())))
    );
CREATE POLICY users_admin_write ON users FOR ALL
    USING (app.is_admin()) WITH CHECK (app.is_admin());
CREATE POLICY users_self_update ON users FOR UPDATE
    USING (id = app.current_user_id()) WITH CHECK (id = app.current_user_id());

CREATE POLICY user_scopes_read ON user_scopes FOR SELECT
    USING (app.is_admin() OR user_id = app.current_user_id()
           OR app.current_role_key() IN ('CENTRAL_PLANNER','AUDITOR'));
CREATE POLICY user_scopes_write ON user_scopes FOR ALL
    USING (app.is_admin()) WITH CHECK (app.is_admin());

CREATE POLICY user_sessions_own ON user_sessions FOR ALL
    USING (user_id = app.current_user_id() OR app.is_admin())
    WITH CHECK (user_id = app.current_user_id() OR app.is_admin());

CREATE POLICY custom_roles_read ON custom_roles FOR SELECT
    USING (app.is_authenticated());
CREATE POLICY custom_roles_write ON custom_roles FOR ALL
    USING (app.is_admin()) WITH CHECK (app.is_admin());

-- ---------------------------------------------------------------------
-- السائقون والمركبات والصناديق: نطاق مركز الانطلاق
-- ---------------------------------------------------------------------
CREATE POLICY drivers_read ON drivers FOR SELECT
    USING (
        app.hub_in_scope(hub_id)
        OR (app.is_driver_role() AND id = app.current_driver_id())
    );
CREATE POLICY drivers_write ON drivers FOR ALL
    USING (app.is_admin()) WITH CHECK (app.is_admin());

CREATE POLICY vehicles_read ON vehicles FOR SELECT
    USING (app.hub_in_scope(hub_id));
CREATE POLICY vehicles_write ON vehicles FOR ALL
    USING (app.is_admin()) WITH CHECK (app.is_admin());

CREATE POLICY boxes_read ON boxes FOR SELECT
    USING (app.hub_in_scope(hub_id));
CREATE POLICY boxes_write ON boxes FOR ALL
    USING (app.is_admin()) WITH CHECK (app.is_admin());

CREATE POLICY availability_read ON availability_exceptions FOR SELECT
    USING (app.is_authenticated());
CREATE POLICY availability_write ON availability_exceptions FOR ALL
    USING (app.is_admin() OR app.current_role_key() = 'HUB_SUPERVISOR')
    WITH CHECK (app.is_admin() OR app.current_role_key() = 'HUB_SUPERVISOR');

CREATE POLICY sensors_read ON sensors FOR SELECT USING (app.is_authenticated());
CREATE POLICY sensors_write ON sensors FOR ALL
    USING (app.is_admin()) WITH CHECK (app.is_admin());

-- ---------------------------------------------------------------------
-- الإعدادات وسجل التدقيق
-- ---------------------------------------------------------------------
CREATE POLICY settings_read ON operational_settings FOR SELECT
    USING (app.is_authenticated());
CREATE POLICY settings_write ON operational_settings FOR ALL
    USING (app.is_admin()) WITH CHECK (app.is_admin());

-- سجل التدقيق: قراءة للأدوار الرقابية فقط، وإدراج للجميع
CREATE POLICY audit_read ON audit_log FOR SELECT
    USING (app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER','AUDITOR'));
CREATE POLICY audit_insert ON audit_log FOR INSERT
    WITH CHECK (app.is_authenticated() OR app.current_role_key() = 'ANONYMOUS');

CREATE POLICY api_clients_admin ON api_clients FOR ALL
    USING (app.is_admin()) WITH CHECK (app.is_admin());

-- ---------------------------------------------------------------------
-- الاستيراد والتخطيط
-- ---------------------------------------------------------------------
-- §5: المشرف لا يرفع الجدول الوطني ولا يراه
CREATE POLICY imports_read ON schedule_imports FOR SELECT
    USING (app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER','AUDITOR'));
CREATE POLICY imports_write ON schedule_imports FOR ALL
    USING (app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER'))
    WITH CHECK (app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER'));

CREATE POLICY import_rows_read ON import_rows FOR SELECT
    USING (app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER','AUDITOR'));
CREATE POLICY import_rows_write ON import_rows FOR ALL
    USING (app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER'))
    WITH CHECK (app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER'));

-- الخطط: نطاق وطني، والمشرف يرى الخطط التي تخص مركزه
CREATE POLICY plans_read ON plans FOR SELECT
    USING (
        app.is_global_scope()
        OR EXISTS (SELECT 1 FROM plan_days pd
                   WHERE pd.plan_id = plans.id
                     AND pd.hub_id = ANY (app.current_hub_ids()))
    );
CREATE POLICY plans_write ON plans FOR ALL
    USING (app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER'))
    WITH CHECK (app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER'));

CREATE POLICY plan_days_read ON plan_days FOR SELECT
    USING (app.hub_in_scope(hub_id));
CREATE POLICY plan_days_write ON plan_days FOR ALL
    USING (app.hub_in_scope(hub_id)) WITH CHECK (app.hub_in_scope(hub_id));

CREATE POLICY driver_estimations_read ON driver_estimations FOR SELECT
    USING (app.hub_in_scope(hub_id));
CREATE POLICY driver_estimations_write ON driver_estimations FOR ALL
    USING (app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER'))
    WITH CHECK (app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER'));

-- ---------------------------------------------------------------------
-- الشحنات — أهم سياسة في النظام
-- ---------------------------------------------------------------------
CREATE POLICY shipments_read ON shipments FOR SELECT
    USING (
        app.is_global_scope()
        OR hub_id = ANY (app.current_hub_ids())
        -- السائق يرى شحنات رحلاته المنشورة فقط
        OR (app.is_driver_role() AND driver_id = app.current_driver_id()
            AND status NOT IN ('DRAFT','VALIDATED','PLANNED'))
        -- مقدم الطلب يرى طلباته هو فقط
        OR (app.is_external_role() AND requester_facility_id = app.current_facility_id())
    );

CREATE POLICY shipments_insert ON shipments FOR INSERT
    WITH CHECK (
        app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER')
        OR (app.is_external_role()
            AND requester_facility_id = app.current_facility_id()
            AND request_kind = 'ON_DEMAND')
        OR (app.current_role_key() IN ('HUB_SUPERVISOR','CONTROL_TOWER')
            AND hub_id = ANY (app.current_hub_ids()))
    );

CREATE POLICY shipments_update ON shipments FOR UPDATE
    USING (
        app.is_global_scope()
        OR hub_id = ANY (app.current_hub_ids())
        OR (app.is_driver_role() AND driver_id = app.current_driver_id())
        OR (app.is_external_role() AND requester_facility_id = app.current_facility_id())
    )
    WITH CHECK (
        app.is_global_scope()
        OR hub_id = ANY (app.current_hub_ids())
        OR (app.is_driver_role() AND driver_id = app.current_driver_id())
        OR (app.is_external_role() AND requester_facility_id = app.current_facility_id())
    );

-- ---------------------------------------------------------------------
-- الرحلات والمحطات
-- ---------------------------------------------------------------------
CREATE POLICY routes_read ON routes FOR SELECT
    USING (
        app.is_global_scope()
        OR hub_id = ANY (app.current_hub_ids())
        -- §5/§18: السائق يرى رحلاته المسندة والمنشورة فقط
        OR (app.is_driver_role() AND driver_id = app.current_driver_id()
            AND status IN ('PUBLISHED','IN_PROGRESS','COMPLETED'))
    );
CREATE POLICY routes_write ON routes FOR ALL
    USING (
        app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER')
        OR hub_id = ANY (app.current_hub_ids())
        OR (app.is_driver_role() AND driver_id = app.current_driver_id())
    )
    WITH CHECK (
        app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER')
        OR hub_id = ANY (app.current_hub_ids())
        OR (app.is_driver_role() AND driver_id = app.current_driver_id())
    );

CREATE POLICY route_stops_all ON route_stops FOR ALL
    USING (EXISTS (SELECT 1 FROM routes r WHERE r.id = route_stops.route_id))
    WITH CHECK (EXISTS (SELECT 1 FROM routes r WHERE r.id = route_stops.route_id));

CREATE POLICY plan_warnings_read ON plan_warnings FOR SELECT
    USING (
        app.is_global_scope()
        OR (hub_id IS NOT NULL AND hub_id = ANY (app.current_hub_ids()))
        OR EXISTS (SELECT 1 FROM routes r WHERE r.id = plan_warnings.route_id)
    );
CREATE POLICY plan_warnings_write ON plan_warnings FOR ALL
    USING (app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER'))
    WITH CHECK (app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER'));

CREATE POLICY route_revisions_read ON route_revisions FOR SELECT
    USING (EXISTS (SELECT 1 FROM routes r WHERE r.id = route_revisions.route_id));
CREATE POLICY route_revisions_insert ON route_revisions FOR INSERT
    WITH CHECK (EXISTS (SELECT 1 FROM routes r WHERE r.id = route_revisions.route_id));

-- ---------------------------------------------------------------------
-- التنفيذ
-- ---------------------------------------------------------------------
CREATE POLICY shipment_events_all ON shipment_events FOR ALL
    USING (EXISTS (SELECT 1 FROM shipments s WHERE s.id = shipment_events.shipment_id))
    WITH CHECK (EXISTS (SELECT 1 FROM shipments s WHERE s.id = shipment_events.shipment_id));

CREATE POLICY status_history_read ON shipment_status_history FOR SELECT
    USING (EXISTS (SELECT 1 FROM shipments s WHERE s.id = shipment_status_history.shipment_id));
CREATE POLICY status_history_insert ON shipment_status_history FOR INSERT
    WITH CHECK (true);

-- §29: منع الوصول غير المصرح للصور — المستند يتبع نطاق شحنته
CREATE POLICY documents_all ON documents FOR ALL
    USING (
        shipment_id IS NULL
        OR EXISTS (SELECT 1 FROM shipments s WHERE s.id = documents.shipment_id)
    )
    WITH CHECK (
        shipment_id IS NULL
        OR EXISTS (SELECT 1 FROM shipments s WHERE s.id = documents.shipment_id)
    );

CREATE POLICY exceptions_all ON shipment_exceptions FOR ALL
    USING (app.hub_in_scope(hub_id)
           OR EXISTS (SELECT 1 FROM shipments s WHERE s.id = shipment_exceptions.shipment_id))
    WITH CHECK (app.hub_in_scope(hub_id)
           OR EXISTS (SELECT 1 FROM shipments s WHERE s.id = shipment_exceptions.shipment_id));

CREATE POLICY alerts_read ON alerts FOR SELECT
    USING (
        app.is_global_scope()
        OR (hub_id IS NOT NULL AND hub_id = ANY (app.current_hub_ids()))
        OR (app.is_driver_role() AND driver_id = app.current_driver_id())
        OR responsible_user_id = app.current_user_id()
        OR EXISTS (SELECT 1 FROM shipments s WHERE s.id = alerts.shipment_id)
    );
CREATE POLICY alerts_write ON alerts FOR ALL
    USING (app.is_global_scope() OR (hub_id IS NOT NULL AND hub_id = ANY (app.current_hub_ids())))
    WITH CHECK (true);

-- ---------------------------------------------------------------------
-- التتبع
-- ---------------------------------------------------------------------
CREATE POLICY positions_read ON driver_positions FOR SELECT
    USING (
        app.is_global_scope()
        OR (app.is_driver_role() AND driver_id = app.current_driver_id())
        OR EXISTS (SELECT 1 FROM drivers d
                   WHERE d.id = driver_positions.driver_id
                     AND d.hub_id = ANY (app.current_hub_ids()))
    );
CREATE POLICY positions_insert ON driver_positions FOR INSERT
    WITH CHECK (
        (app.is_driver_role() AND driver_id = app.current_driver_id())
        OR app.is_global_scope()
    );

CREATE POLICY last_position_read ON driver_last_position FOR SELECT
    USING (
        app.is_global_scope()
        OR (app.is_driver_role() AND driver_id = app.current_driver_id())
        OR EXISTS (SELECT 1 FROM drivers d
                   WHERE d.id = driver_last_position.driver_id
                     AND d.hub_id = ANY (app.current_hub_ids()))
    );
CREATE POLICY last_position_write ON driver_last_position FOR ALL
    USING (true) WITH CHECK (true);

-- ---------------------------------------------------------------------
-- الحرارة وسلسلة الحيازة
-- ---------------------------------------------------------------------
CREATE POLICY temperature_read ON temperature_readings FOR SELECT
    USING (
        app.is_global_scope()
        OR shipment_id IS NULL
        OR EXISTS (SELECT 1 FROM shipments s WHERE s.id = temperature_readings.shipment_id)
    );
CREATE POLICY temperature_insert ON temperature_readings FOR INSERT
    WITH CHECK (app.is_authenticated());

CREATE POLICY breaches_read ON temperature_breaches FOR SELECT
    USING (
        app.is_global_scope()
        OR shipment_id IS NULL
        OR EXISTS (SELECT 1 FROM shipments s WHERE s.id = temperature_breaches.shipment_id)
    );
CREATE POLICY breaches_write ON temperature_breaches FOR ALL
    USING (app.is_global_scope()
           OR EXISTS (SELECT 1 FROM shipments s WHERE s.id = temperature_breaches.shipment_id))
    WITH CHECK (true);

CREATE POLICY custody_all ON custody_transfers FOR ALL
    USING (EXISTS (SELECT 1 FROM shipments s WHERE s.id = custody_transfers.shipment_id))
    WITH CHECK (EXISTS (SELECT 1 FROM shipments s WHERE s.id = custody_transfers.shipment_id));

CREATE POLICY system_events_read ON system_events FOR SELECT
    USING (
        app.is_global_scope()
        OR (hub_id IS NOT NULL AND hub_id = ANY (app.current_hub_ids()))
        OR (app.is_driver_role() AND driver_id = app.current_driver_id())
        OR user_id = app.current_user_id()
    );
CREATE POLICY system_events_insert ON system_events FOR INSERT
    WITH CHECK (true);
