# ٥) مرجع واجهة البرمجة (API)

> **وثيقة مُولَّدة آليًا** من الشيفرة وقاعدة البيانات في 2026-08-26 21:32 UTC.
> لا تُحرَّر يدويًا: أعد توليدها بـ `PYTHONPATH=packages python3 scripts/gen_docs.py`.


## الاتفاقيات

- كل استجابة ناجحة: `{"ok": true, "data": ...}`.
- كل خطأ: `{"ok": false, "error": {"code", "message", "details"}}`
  برسالة عربية صالحة للعرض المباشر.
- المصادقة: `Authorization: Bearer <access_token>` أو ملف تعريف ارتباط
  `masar_access` (HttpOnly). التجديد عبر `/api/auth/refresh` مع تدوير
  رمز التحديث وكشف إعادة الاستخدام.
- الإجراءات الحساسة تتطلب حقل `reason` غير فارغ، وإلا `REASON_REQUIRED`.
- التوقيت: كل الطوابع `timestamptz` بصيغة ISO-8601 (UTC)، والعرض
  بتوقيت `Asia/Riyadh`.

**93 مسارًا.**

| المسار | الطرق | الصلاحية المطلوبة |
|---|---|---|
| `/api/health` | GET | عام (بلا مصادقة) |
| `/api/meta` | GET | عام (بلا مصادقة) |
| `/api/meta/permissions` | GET | مصادقة فقط |
| `/api/meta/state-machines` | GET | مصادقة فقط |
| `/api/auth/login` | POST | عام (بلا مصادقة) |
| `/api/auth/refresh` | POST | عام (بلا مصادقة) |
| `/api/auth/logout` | POST | مصادقة فقط |
| `/api/auth/me` | GET | مصادقة فقط |
| `/api/auth/session` | GET | عام (بلا مصادقة) |
| `/api/auth/password` | POST | مصادقة فقط |
| `/api/auth/password/reset` | POST | عام (بلا مصادقة) |
| `/api/auth/password/reset/complete` | POST | عام (بلا مصادقة) |
| `/api/md/{entity}/schema` | GET | مصادقة فقط |
| `/api/md/{entity}` | GET | مصادقة فقط |
| `/api/md/{entity}` | POST | مصادقة فقط |
| `/api/md/{entity}/{id}` | GET | مصادقة فقط |
| `/api/md/{entity}/{id}` | PATCH | مصادقة فقط |
| `/api/md/{entity}/{id}/void` | POST | مصادقة فقط |
| `/api/users` | GET | `users.read` |
| `/api/users` | POST | `users.write` |
| `/api/users/{id}` | PATCH | `users.write` |
| `/api/settings` | GET | `settings.read` |
| `/api/settings` | POST | `settings.write` |
| `/api/settings/{id}` | DELETE | `settings.write` |
| `/api/imports/template` | GET | مصادقة فقط |
| `/api/imports` | GET | `schedule.read` |
| `/api/imports` | POST | `schedule.upload` |
| `/api/imports/{id}` | GET | `schedule.read` |
| `/api/imports/{id}/validate` | POST | `schedule.upload` |
| `/api/imports/{id}/errors.csv` | GET | `schedule.read` |
| `/api/imports/{id}/exclude` | POST | `schedule.commit` |
| `/api/imports/{id}/commit` | POST | `schedule.commit` |
| `/api/plans` | GET | `plan.read` |
| `/api/plans/run` | POST | `plan.optimize` |
| `/api/plans/compare` | GET | `plan.compare` |
| `/api/plans/{id}` | GET | `plan.read` |
| `/api/plans/{id}/approve` | POST | `plan.approve` |
| `/api/plans/{id}/dispatch` | POST | `plan.dispatch` |
| `/api/routes` | GET | `routes.read` |
| `/api/routes/{id}` | GET | `routes.read` |
| `/api/routes/{id}/candidates` | GET | `routes.assign` |
| `/api/routes/{id}/assign` | POST | `routes.assign` |
| `/api/routes/{id}/unassign` | POST | `routes.unassign` |
| `/api/routes/{id}/modify` | POST | `routes.modify_published` |
| `/api/publish` | POST | `routes.publish` |
| `/api/unpublish` | POST | `routes.publish` |
| `/api/driver/routes` | GET | `routes.read` |
| `/api/driver/routes/{id}/start` | POST | `routes.execute` |
| `/api/driver/stops/{id}/arrive` | POST | `routes.execute` |
| `/api/driver/stops/{id}/pickup` | POST | `routes.execute` |
| `/api/driver/stops/{id}/deliver` | POST | `routes.execute` |
| `/api/driver/sync` | POST | `routes.execute` |
| `/api/positions` | POST | `tracking.publish` |
| `/api/documents` | POST | `documents.upload` |
| `/api/documents/{id}` | GET | `documents.read` |
| `/api/shipments/{id}/documents` | GET | `documents.read` |
| `/api/exceptions` | POST | `exceptions.record` |
| `/api/exceptions` | GET | `exceptions.resolve` + `shipments.read` |
| `/api/exceptions/{id}/resolve` | POST | `exceptions.resolve` |
| `/api/ondemand` | POST | `ondemand.create` |
| `/api/ondemand` | GET | `shipments.read` |
| `/api/ondemand/{id}/review` | POST | `ondemand.review` |
| `/api/ondemand/{id}/options` | GET | `routes.assign` |
| `/api/ondemand/{id}/assign` | POST | `routes.assign` |
| `/api/ondemand/{id}/cancel` | POST | `ondemand.cancel_own` + `shipments.cancel` |
| `/api/alerts` | GET | `alerts.read` |
| `/api/alerts/summary` | GET | `alerts.read` |
| `/api/alerts/scan` | POST | `alerts.read` |
| `/api/alerts/{id}/ack` | POST | `alerts.act` |
| `/api/alerts/{id}/resolve` | POST | `alerts.act` |
| `/api/notifications` | GET | `alerts.read` |
| `/api/notifications/status` | GET | عام (بلا مصادقة) |
| `/api/storage/status` | GET | `integrations.read` |
| `/api/notifications/flush` | POST | `alerts.act` |
| `/api/tracking/live` | GET | `tracking.read` |
| `/api/tracking/routes/{id}` | GET | `tracking.read` |
| `/api/temperature/status` | GET | عام (بلا مصادقة) |
| `/api/temperature/ingest` | POST | `temperature.ingest` |
| `/api/temperature/poll` | POST | `temperature.read` |
| `/api/temperature/shipments/{id}` | GET | `temperature.read` |
| `/api/temperature/breaches/{id}/resolve` | POST | `alerts.act` |
| `/api/shipments` | GET | `shipments.read` |
| `/api/shipments/{id}` | GET | `shipments.read` |
| `/api/reports/kpi` | GET | `reports.read` |
| `/api/reports/grouped` | GET | `reports.read` |
| `/api/reports/routes` | GET | `reports.read` |
| `/api/reports/exceptions` | GET | `reports.read` |
| `/api/reports/temperature` | GET | `temperature.read` |
| `/api/reports/plan-vs-execution` | GET | `reports.read` |
| `/api/reports/hub-modifications` | GET | `hub_changes.monitor` |
| `/api/reports/driver-capacity` | GET | `driver_estimation.read` |
| `/api/audit` | GET | `audit.read` |
| `/api/events` | GET | مصادقة فقط |

## رموز الأخطاء

| الرمز | HTTP | متى يظهر |
|---|---|---|
| `CONFLICT` | 409 | Conflict |
| `DEPENDENCY_UNAVAILABLE` | 503 | خدمة خارجية (طرق/تخزين/محرك) غير متاحة — لا يُخفى الخطأ. |
| `FEASIBILITY_VIOLATION` | 409 | خرق قيد صلب اكتُشف في فحص ما بعد الحل — يمنع النشر. |
| `FORBIDDEN` | 403 | Forbidden |
| `INVALID_TRANSITION` | 409 | InvalidTransition |
| `NOT_FOUND` | 404 | NotFound |
| `OPTIMIZATION_FAILED` | 500 | OptimizationFailed |
| `OUT_OF_SCOPE` | 403 | المستخدم مصرّح بالعملية لكن الكائن خارج نطاقه (مركز/جهة/سائق آخر). |
| `RATE_LIMITED` | 429 | RateLimited |
| `REASON_REQUIRED` | 422 | ReasonRequired |
| `UNAUTHORIZED` | 401 | Unauthorized |
| `VALIDATION_ERROR` | 422 | ValidationError |
