-- =====================================================================
-- الترحيل 0006: تصحيحات نطاق مكتشَفة في اختبار الدورة الكاملة
-- =====================================================================

-- (1) مقدم الطلب الخارجي يحتاج رؤية **جهات التسليم الممكنة** (المختبرات
--     وبنوك الدم) لإنشاء طلب فوري، دون أن يرى بقية الجهات ولا بياناتها.
--     نطاقه على الشحنات يبقى محصورًا بطلباته هو (سياسة shipments_read).
DROP POLICY IF EXISTS facilities_read ON facilities;
CREATE POLICY facilities_read ON facilities FOR SELECT
    USING (
        CASE
            WHEN app.is_external_role() THEN
                id = app.current_facility_id()
                OR facility_type IN ('LABORATORY', 'BLOOD_BANK')
            ELSE app.is_authenticated()
        END
    );

COMMENT ON POLICY facilities_read ON facilities IS
    'مقدم الطلب الخارجي يرى جهته + جهات التسليم الممكنة فقط (مختبرات وبنوك دم)';

-- (2) السائق كان يرى كل رحلات مركزه لأن مركزه كان يُضاف إلى قائمة نطاق
--     المراكز. الصحيح: السائق يرى **رحلاته المسندة إليه فقط**، ويُقرأ
--     مركزه من جدول السائقين لا من نطاق المستخدم.
DROP POLICY IF EXISTS routes_read ON routes;
CREATE POLICY routes_read ON routes FOR SELECT
    USING (
        app.is_global_scope()
        OR (NOT app.is_driver_role() AND hub_id = ANY (app.current_hub_ids()))
        OR (app.is_driver_role() AND driver_id = app.current_driver_id()
            AND status IN ('PUBLISHED','IN_PROGRESS','COMPLETED'))
    );

DROP POLICY IF EXISTS routes_write ON routes;
CREATE POLICY routes_write ON routes FOR ALL
    USING (
        app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER')
        OR (NOT app.is_driver_role() AND hub_id = ANY (app.current_hub_ids()))
        OR (app.is_driver_role() AND driver_id = app.current_driver_id())
    )
    WITH CHECK (
        app.current_role_key() IN ('ADMIN','CENTRAL_PLANNER')
        OR (NOT app.is_driver_role() AND hub_id = ANY (app.current_hub_ids()))
        OR (app.is_driver_role() AND driver_id = app.current_driver_id())
    );

DROP POLICY IF EXISTS shipments_read ON shipments;
CREATE POLICY shipments_read ON shipments FOR SELECT
    USING (
        app.is_global_scope()
        OR (NOT app.is_driver_role() AND NOT app.is_external_role()
            AND hub_id = ANY (app.current_hub_ids()))
        OR (app.is_driver_role() AND driver_id = app.current_driver_id()
            AND status NOT IN ('DRAFT','VALIDATED','PLANNED'))
        OR (app.is_external_role() AND requester_facility_id = app.current_facility_id())
    );

DROP POLICY IF EXISTS shipments_update ON shipments;
CREATE POLICY shipments_update ON shipments FOR UPDATE
    USING (
        app.is_global_scope()
        OR (NOT app.is_driver_role() AND NOT app.is_external_role()
            AND hub_id = ANY (app.current_hub_ids()))
        OR (app.is_driver_role() AND driver_id = app.current_driver_id())
        OR (app.is_external_role() AND requester_facility_id = app.current_facility_id())
    )
    WITH CHECK (
        app.is_global_scope()
        OR (NOT app.is_driver_role() AND NOT app.is_external_role()
            AND hub_id = ANY (app.current_hub_ids()))
        OR (app.is_driver_role() AND driver_id = app.current_driver_id())
        OR (app.is_external_role() AND requester_facility_id = app.current_facility_id())
    );

-- (3) نفس المبدأ على المواقع والتنبيهات: السائق يرى ما يخصه فقط
DROP POLICY IF EXISTS positions_read ON driver_positions;
CREATE POLICY positions_read ON driver_positions FOR SELECT
    USING (
        app.is_global_scope()
        OR (app.is_driver_role() AND driver_id = app.current_driver_id())
        OR (NOT app.is_driver_role() AND EXISTS (
                SELECT 1 FROM drivers d
                WHERE d.id = driver_positions.driver_id
                  AND d.hub_id = ANY (app.current_hub_ids())))
    );

DROP POLICY IF EXISTS last_position_read ON driver_last_position;
CREATE POLICY last_position_read ON driver_last_position FOR SELECT
    USING (
        app.is_global_scope()
        OR (app.is_driver_role() AND driver_id = app.current_driver_id())
        OR (NOT app.is_driver_role() AND EXISTS (
                SELECT 1 FROM drivers d
                WHERE d.id = driver_last_position.driver_id
                  AND d.hub_id = ANY (app.current_hub_ids())))
    );

DROP POLICY IF EXISTS alerts_read ON alerts;
CREATE POLICY alerts_read ON alerts FOR SELECT
    USING (
        app.is_global_scope()
        OR (NOT app.is_driver_role() AND hub_id IS NOT NULL
            AND hub_id = ANY (app.current_hub_ids()))
        OR (app.is_driver_role() AND driver_id = app.current_driver_id())
        OR responsible_user_id = app.current_user_id()
        OR EXISTS (SELECT 1 FROM shipments s WHERE s.id = alerts.shipment_id)
    );

-- (4) السائق يرى مركز انطلاقه (لعرض نقطة البداية) من جدول السائقين
DROP POLICY IF EXISTS drivers_read ON drivers;
CREATE POLICY drivers_read ON drivers FOR SELECT
    USING (
        (NOT app.is_driver_role() AND app.hub_in_scope(hub_id))
        OR (app.is_driver_role() AND id = app.current_driver_id())
    );
