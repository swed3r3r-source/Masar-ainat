# ٦-أ) أمن الصفوف (RLS) — الطبقة التي لا يمكن تجاوزها

> **وثيقة مُولَّدة آليًا** من الشيفرة وقاعدة البيانات في 2026-08-26 21:32 UTC.
> لا تُحرَّر يدويًا: أعد توليدها بـ `PYTHONPATH=packages python3 scripts/gen_docs.py`.


الصلاحية تُطبَّق في **ثلاث طبقات**: إخفاء في الواجهة (راحة استخدام)،
و`require(permission)` في الخادم، و**سياسات صفوف في قاعدة البيانات**.
الطبقة الثالثة هي الضمانة: حتى لو أخطأ استعلام في الخادم، لا يعيد
المحرك صفًا خارج نطاق المستخدم.

**39 جدولًا** مفعّل عليها RLS، بـ **74 سياسة**.

## دوال السياق (`app`)

تقرأ متغيرات الجلسة التي يضبطها الخادم عند كل اتصال:

| الدالة | تعيد | الغرض |
|---|---|---|
| `app.audit_log_is_append_only()` | `trigger` | — |
| `app.can_see_hub()` | `boolean` | — |
| `app.current_driver_id()` | `uuid` | — |
| `app.current_facility_id()` | `uuid` | — |
| `app.current_hub_ids()` | `uuid[]` | — |
| `app.current_region_ids()` | `uuid[]` | — |
| `app.current_role_key()` | `text` | — |
| `app.current_user_id()` | `uuid` | — |
| `app.ensure_position_partition()` | `void` | — |
| `app.guard_no_test_data_in_production()` | `trigger` | — |
| `app.guard_operational_delete()` | `trigger` | — |
| `app.guard_plan_transition()` | `trigger` | — |
| `app.guard_route_publish()` | `trigger` | — |
| `app.guard_route_transition()` | `trigger` | — |
| `app.guard_shipment_transition()` | `trigger` | — |
| `app.hub_in_scope()` | `boolean` | — |
| `app.is_admin()` | `boolean` | — |
| `app.is_authenticated()` | `boolean` | — |
| `app.is_driver_role()` | `boolean` | — |
| `app.is_external_role()` | `boolean` | — |
| `app.is_global_scope()` | `boolean` | — |
| `app.publish_system_event()` | `trigger` | — |
| `app.record_shipment_status_change()` | `trigger` | — |
| `app.route_time_range()` | `tstzrange` | — |
| `app.setting_text()` | `text` | — |
| `app.touch_updated_at()` | `trigger` | — |
| `app.upsert_driver_last_position()` | `trigger` | — |
| `app.verify_route_feasibility()` | `TABLE(rule_code text, detail_ar text)` | فحص جدوى مستقل عن المحرك — يُشغَّل قبل النشر ولا يُسمح بالنشر مع وجود نتائج |

## السياسات

### `alerts`

- **alerts_read** (`SELECT`)
  ```sql
  (app.is_global_scope() OR ((NOT app.is_driver_role()) AND (hub_id IS NOT NULL) AND (hub_id = ANY (app.current_hub_ids()))) OR (app.is_driver_role() AND (driver_id = app.current_driver_id())) OR (responsible_user_id = app.current_user_id()) OR (EXISTS ( SELECT 1 FROM shipments s WHERE (s.id = alerts.shipment_id))))
  ```
- **alerts_write** (`ALL`)
  ```sql
  (app.is_global_scope() OR ((hub_id IS NOT NULL) AND (hub_id = ANY (app.current_hub_ids()))))
  ```
### `api_clients`

- **api_clients_admin** (`ALL`)
  ```sql
  app.is_admin()
  ```
### `audit_log`

- **audit_insert** (`INSERT`)
  ```sql
  (app.is_authenticated() OR (app.current_role_key() = 'ANONYMOUS'::text))
  ```
- **audit_read** (`SELECT`)
  ```sql
  (app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text, 'AUDITOR'::text]))
  ```
### `availability_exceptions`

- **availability_read** (`SELECT`)
  ```sql
  app.is_authenticated()
  ```
- **availability_write** (`ALL`)
  ```sql
  (app.is_admin() OR (app.current_role_key() = 'HUB_SUPERVISOR'::text))
  ```
### `boxes`

- **boxes_read** (`SELECT`)
  ```sql
  app.hub_in_scope(hub_id)
  ```
- **boxes_write** (`ALL`)
  ```sql
  app.is_admin()
  ```
### `cities`

- **cities_read** (`SELECT`)
  ```sql
  app.is_authenticated()
  ```
- **cities_write** (`ALL`)
  ```sql
  app.is_admin()
  ```
### `custody_transfers`

- **custody_all** (`ALL`)
  ```sql
  (EXISTS ( SELECT 1 FROM shipments s WHERE (s.id = custody_transfers.shipment_id)))
  ```
### `custom_roles`

- **custom_roles_read** (`SELECT`)
  ```sql
  app.is_authenticated()
  ```
- **custom_roles_write** (`ALL`)
  ```sql
  app.is_admin()
  ```
### `documents`

- **documents_all** (`ALL`)
  ```sql
  ((shipment_id IS NULL) OR (EXISTS ( SELECT 1 FROM shipments s WHERE (s.id = documents.shipment_id))))
  ```
### `driver_estimations`

- **driver_estimations_read** (`SELECT`)
  ```sql
  app.hub_in_scope(hub_id)
  ```
- **driver_estimations_write** (`ALL`)
  ```sql
  (app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text]))
  ```
### `driver_last_position`

- **last_position_read** (`SELECT`)
  ```sql
  (app.is_global_scope() OR (app.is_driver_role() AND (driver_id = app.current_driver_id())) OR ((NOT app.is_driver_role()) AND (EXISTS ( SELECT 1 FROM drivers d WHERE ((d.id = driver_last_position.driver_id) AND (d.hub_id = ANY (app.current_hub_ids())))))))
  ```
- **last_position_write** (`ALL`)
  ```sql
  true
  ```
### `driver_positions`

- **positions_insert** (`INSERT`)
  ```sql
  ((app.is_driver_role() AND (driver_id = app.current_driver_id())) OR app.is_global_scope())
  ```
- **positions_read** (`SELECT`)
  ```sql
  (app.is_global_scope() OR (app.is_driver_role() AND (driver_id = app.current_driver_id())) OR ((NOT app.is_driver_role()) AND (EXISTS ( SELECT 1 FROM drivers d WHERE ((d.id = driver_positions.driver_id) AND (d.hub_id = ANY (app.current_hub_ids())))))))
  ```
### `drivers`

- **drivers_read** (`SELECT`)
  ```sql
  (((NOT app.is_driver_role()) AND app.hub_in_scope(hub_id)) OR (app.is_driver_role() AND (id = app.current_driver_id())))
  ```
- **drivers_write** (`ALL`)
  ```sql
  app.is_admin()
  ```
### `facilities`

- **facilities_read** (`SELECT`)
  ```sql
  CASE WHEN app.is_external_role() THEN ((id = app.current_facility_id()) OR (facility_type = ANY (ARRAY['LABORATORY'::text, 'BLOOD_BANK'::text]))) ELSE app.is_authenticated() END
  ```
- **facilities_write** (`ALL`)
  ```sql
  app.is_admin()
  ```
### `facility_contacts`

- **facility_contacts_read** (`SELECT`)
  ```sql
  (EXISTS ( SELECT 1 FROM facilities f WHERE (f.id = facility_contacts.facility_id)))
  ```
- **facility_contacts_write** (`ALL`)
  ```sql
  app.is_admin()
  ```
### `hubs`

- **hubs_read** (`SELECT`)
  ```sql
  (app.is_global_scope() OR (id = ANY (app.current_hub_ids())) OR (app.is_driver_role() AND (EXISTS ( SELECT 1 FROM drivers d WHERE ((d.id = app.current_driver_id()) AND (d.hub_id = hubs.id))))))
  ```
- **hubs_write** (`ALL`)
  ```sql
  app.is_admin()
  ```
### `import_rows`

- **import_rows_read** (`SELECT`)
  ```sql
  (app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text, 'AUDITOR'::text]))
  ```
- **import_rows_write** (`ALL`)
  ```sql
  (app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text]))
  ```
### `notifications`

- **notifications_insert** (`INSERT`)
  ```sql
  app.is_authenticated()
  ```
- **notifications_read** (`SELECT`)
  ```sql
  (app.is_global_scope() OR ((hub_id IS NOT NULL) AND app.hub_in_scope(hub_id)) OR (user_id = app.current_user_id()))
  ```
- **notifications_update** (`UPDATE`)
  ```sql
  app.is_global_scope()
  ```
### `operational_settings`

- **settings_read** (`SELECT`)
  ```sql
  app.is_authenticated()
  ```
- **settings_write** (`ALL`)
  ```sql
  app.is_admin()
  ```
### `plan_days`

- **plan_days_read** (`SELECT`)
  ```sql
  app.hub_in_scope(hub_id)
  ```
- **plan_days_write** (`ALL`)
  ```sql
  app.hub_in_scope(hub_id)
  ```
### `plan_warnings`

- **plan_warnings_read** (`SELECT`)
  ```sql
  (app.is_global_scope() OR ((hub_id IS NOT NULL) AND (hub_id = ANY (app.current_hub_ids()))) OR (EXISTS ( SELECT 1 FROM routes r WHERE (r.id = plan_warnings.route_id))))
  ```
- **plan_warnings_write** (`ALL`)
  ```sql
  (app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text]))
  ```
### `plans`

- **plans_read** (`SELECT`)
  ```sql
  (app.is_global_scope() OR (EXISTS ( SELECT 1 FROM plan_days pd WHERE ((pd.plan_id = plans.id) AND (pd.hub_id = ANY (app.current_hub_ids()))))))
  ```
- **plans_write** (`ALL`)
  ```sql
  (app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text]))
  ```
### `regions`

- **regions_read** (`SELECT`)
  ```sql
  app.is_authenticated()
  ```
- **regions_write** (`ALL`)
  ```sql
  app.is_admin()
  ```
### `route_revisions`

- **route_revisions_insert** (`INSERT`)
  ```sql
  (EXISTS ( SELECT 1 FROM routes r WHERE (r.id = route_revisions.route_id)))
  ```
- **route_revisions_read** (`SELECT`)
  ```sql
  (EXISTS ( SELECT 1 FROM routes r WHERE (r.id = route_revisions.route_id)))
  ```
### `route_stops`

- **route_stops_all** (`ALL`)
  ```sql
  (EXISTS ( SELECT 1 FROM routes r WHERE (r.id = route_stops.route_id)))
  ```
### `routes`

- **routes_read** (`SELECT`)
  ```sql
  (app.is_global_scope() OR ((NOT app.is_driver_role()) AND (hub_id = ANY (app.current_hub_ids()))) OR (app.is_driver_role() AND (driver_id = app.current_driver_id()) AND (status = ANY (ARRAY['PUBLISHED'::text, 'IN_PROGRESS'::text, 'COMPLETED'::text]))))
  ```
- **routes_write** (`ALL`)
  ```sql
  ((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text])) OR ((NOT app.is_driver_role()) AND (hub_id = ANY (app.current_hub_ids()))) OR (app.is_driver_role() AND (driver_id = app.current_driver_id())))
  ```
### `schedule_imports`

- **imports_read** (`SELECT`)
  ```sql
  (app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text, 'AUDITOR'::text]))
  ```
- **imports_write** (`ALL`)
  ```sql
  (app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text]))
  ```
### `sensors`

- **sensors_read** (`SELECT`)
  ```sql
  app.is_authenticated()
  ```
- **sensors_write** (`ALL`)
  ```sql
  app.is_admin()
  ```
### `shipment_events`

- **shipment_events_all** (`ALL`)
  ```sql
  (EXISTS ( SELECT 1 FROM shipments s WHERE (s.id = shipment_events.shipment_id)))
  ```
### `shipment_exceptions`

- **exceptions_all** (`ALL`)
  ```sql
  (app.hub_in_scope(hub_id) OR (EXISTS ( SELECT 1 FROM shipments s WHERE (s.id = shipment_exceptions.shipment_id))))
  ```
### `shipment_status_history`

- **status_history_insert** (`INSERT`)
  ```sql
  true
  ```
- **status_history_read** (`SELECT`)
  ```sql
  (EXISTS ( SELECT 1 FROM shipments s WHERE (s.id = shipment_status_history.shipment_id)))
  ```
### `shipments`

- **shipments_insert** (`INSERT`)
  ```sql
  ((app.current_role_key() = ANY (ARRAY['ADMIN'::text, 'CENTRAL_PLANNER'::text])) OR (app.is_external_role() AND (requester_facility_id = app.current_facility_id()) AND (request_kind = 'ON_DEMAND'::text)) OR ((app.current_role_key() = ANY (ARRAY['HUB_SUPERVISOR'::text, 'CONTROL_TOWER'::text])) AND (hub_id = ANY (app.current_hub_ids()))))
  ```
- **shipments_read** (`SELECT`)
  ```sql
  (app.is_global_scope() OR ((NOT app.is_driver_role()) AND (NOT app.is_external_role()) AND (hub_id = ANY (app.current_hub_ids()))) OR (app.is_driver_role() AND (driver_id = app.current_driver_id()) AND (status <> ALL (ARRAY['DRAFT'::text, 'VALIDATED'::text, 'PLANNED'::text]))) OR (app.is_external_role() AND (requester_facility_id = app.current_facility_id())))
  ```
- **shipments_update** (`UPDATE`)
  ```sql
  (app.is_global_scope() OR ((NOT app.is_driver_role()) AND (NOT app.is_external_role()) AND (hub_id = ANY (app.current_hub_ids()))) OR (app.is_driver_role() AND (driver_id = app.current_driver_id())) OR (app.is_external_role() AND (requester_facility_id = app.current_facility_id())))
  ```
### `system_events`

- **system_events_insert** (`INSERT`)
  ```sql
  true
  ```
- **system_events_read** (`SELECT`)
  ```sql
  (app.is_global_scope() OR ((hub_id IS NOT NULL) AND (hub_id = ANY (app.current_hub_ids()))) OR (app.is_driver_role() AND (driver_id = app.current_driver_id())) OR (user_id = app.current_user_id()))
  ```
### `temperature_breaches`

- **breaches_read** (`SELECT`)
  ```sql
  (app.is_global_scope() OR (shipment_id IS NULL) OR (EXISTS ( SELECT 1 FROM shipments s WHERE (s.id = temperature_breaches.shipment_id))))
  ```
- **breaches_write** (`ALL`)
  ```sql
  (app.is_global_scope() OR (EXISTS ( SELECT 1 FROM shipments s WHERE (s.id = temperature_breaches.shipment_id))))
  ```
### `temperature_readings`

- **temperature_insert** (`INSERT`)
  ```sql
  app.is_authenticated()
  ```
- **temperature_read** (`SELECT`)
  ```sql
  (app.is_global_scope() OR (shipment_id IS NULL) OR (EXISTS ( SELECT 1 FROM shipments s WHERE (s.id = temperature_readings.shipment_id))))
  ```
### `user_scopes`

- **user_scopes_read** (`SELECT`)
  ```sql
  (app.is_admin() OR (user_id = app.current_user_id()) OR (app.current_role_key() = ANY (ARRAY['CENTRAL_PLANNER'::text, 'AUDITOR'::text])))
  ```
- **user_scopes_write** (`ALL`)
  ```sql
  app.is_admin()
  ```
### `user_sessions`

- **user_sessions_own** (`ALL`)
  ```sql
  ((user_id = app.current_user_id()) OR app.is_admin())
  ```
### `users`

- **users_admin_write** (`ALL`)
  ```sql
  app.is_admin()
  ```
- **users_self_read** (`SELECT`)
  ```sql
  (app.is_admin() OR (id = app.current_user_id()) OR (app.current_role_key() = ANY (ARRAY['CENTRAL_PLANNER'::text, 'AUDITOR'::text, 'CONTROL_TOWER'::text])) OR ((app.current_role_key() = 'HUB_SUPERVISOR'::text) AND (EXISTS ( SELECT 1 FROM user_scopes us WHERE ((us.user_id = users.id) AND (us.scope_type = 'HUB'::text) AND (us.scope_id = ANY (app.current_hub_ids())))))))
  ```
- **users_self_update** (`UPDATE`)
  ```sql
  (id = app.current_user_id())
  ```
### `vehicles`

- **vehicles_read** (`SELECT`)
  ```sql
  app.hub_in_scope(hub_id)
  ```
- **vehicles_write** (`ALL`)
  ```sql
  app.is_admin()
  ```
