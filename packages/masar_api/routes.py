"""تعريف نقاط الـ API. كل نقطة تمر بثلاث بوابات: مصادقة ← صلاحية ← RLS."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

from masar_core.config import get_config
from masar_core.constants import (
    ExceptionReason,
    FacilityType,
    Role,
    ServiceType,
    ShipmentStatus,
    TemperatureMode,
)
from masar_core.errors import Forbidden, NotFound, Unauthorized, ValidationError
from masar_core.operational_settings import SETTING_SPECS
from masar_core.permissions import PERMISSIONS, matrix_rows
from masar_core.state_machine import export_diagram, plan_sm, route_sm, shipment_sm
from masar_db.driver import SecurityContext, session

from .deps import context_of, current_user, optional_user, rate_limit, require
from .http import (
    ArabicJSONResponse,
    client_ip,
    created,
    error,
    get_date,
    get_datetime,
    get_float,
    get_int,
    get_reason,
    get_uuid,
    ok,
    paginated,
    query_int,
    read_json,
    require_fields,
)
from .services import (
    alerts as alerts_service,
    audit as audit_service,
    auth as auth_service,
    events as events_service,
    execution,
    imports as imports_service,
    masterdata,
    ondemand,
    operations,
    planning,
    reports,
    settings as settings_service,
    temperature as temperature_service,
    tracking,
)


def _cookie_kwargs() -> dict[str, Any]:
    cfg = get_config()
    return {
        "httponly": True,
        "samesite": "strict",
        "secure": cfg.is_production,
        "path": "/",
    }


# ============================================================ عام ===========

async def health(request: Request) -> Response:
    from masar_db.driver import get_pool

    checks: dict[str, Any] = {}
    try:
        pool = get_pool()
        with pool.connection() as conn:
            checks["database"] = {"ok": conn.fetch_value("SELECT 1") == 1,
                                  "pool_size": pool.size}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)}
    checks["event_bus"] = {
        "ok": events_service.bus.connected,
        "subscribers": events_service.bus.subscriber_count,
        "last_error": events_service.bus.last_error,
    }
    checks["temperature_provider"] = temperature_service.provider_status()
    healthy = all(
        item.get("ok", True) for item in checks.values() if isinstance(item, dict))
    return ArabicJSONResponse(
        {"ok": healthy, "checks": checks,
         "environment": get_config().environment,
         "time": dt.datetime.now(dt.timezone.utc).isoformat()},
        status_code=200 if healthy else 503,
    )


async def meta(request: Request) -> Response:
    cfg = get_config()
    return ok({
        "config": cfg.public_config(),
        "roles": [
            {"key": role.value, "label_ar": ROLE_LABELS[role.value]} for role in Role
        ],
        "enums": {
            "facility_types": [
                {"key": item.value, "label_ar": FACILITY_TYPE_LABELS[item.value]}
                for item in FacilityType
            ],
            "service_types": [
                {"key": item.value, "label_ar": SERVICE_TYPE_LABELS[item.value]}
                for item in ServiceType
            ],
            "temperature_modes": [
                {"key": item.value, "label_ar": TEMPERATURE_LABELS[item.value]}
                for item in TemperatureMode
            ],
            "exception_reasons": [
                {"key": item.value, "label_ar": EXCEPTION_LABELS[item.value]}
                for item in ExceptionReason
            ],
            "shipment_statuses": [
                {"key": item.value, "label_ar": STATUS_LABELS.get(item.value, item.value)}
                for item in ShipmentStatus
            ],
        },
        "permissions": [
            {"key": p.key, "name_ar": p.name_ar, "group": p.group,
             "requires_reason": p.requires_reason}
            for p in PERMISSIONS
        ],
        "setting_specs": [
            {"key": s.key, "name_ar": s.name_ar, "kind": s.kind, "unit_ar": s.unit_ar,
             "group_ar": s.group_ar, "default": s.default, "minimum": s.minimum,
             "maximum": s.maximum, "choices": list(s.choices) if s.choices else None,
             "description_ar": s.description_ar}
            for s in SETTING_SPECS
        ],
    })


async def permission_matrix(request: Request) -> Response:
    return ok({"rows": matrix_rows(), "roles": [role.value for role in Role]})


async def state_diagrams(request: Request) -> Response:
    return ok({
        "shipment": export_diagram(shipment_sm),
        "route": export_diagram(route_sm),
        "plan": export_diagram(plan_sm),
    })


ROLE_LABELS = {
    "ADMIN": "مدير النظام",
    "CENTRAL_PLANNER": "التخطيط المركزي",
    "HUB_SUPERVISOR": "مشرف مركز انطلاق",
    "DRIVER": "سائق",
    "EXTERNAL_REQUESTER": "مقدم طلب خارجي",
    "CONTROL_TOWER": "برج التحكم",
    "AUDITOR": "مدقق",
    "INTEGRATION": "حساب تكامل",
}
FACILITY_TYPE_LABELS = {
    "HEALTH_CENTER": "مركز صحي", "HOSPITAL": "مستشفى", "LABORATORY": "مختبر",
    "BLOOD_BANK": "بنك دم", "WAREHOUSE": "مستودع", "CLINIC": "عيادة", "OTHER": "أخرى",
}
SERVICE_TYPE_LABELS = {
    "ROUTINE": "روتيني", "URGENT": "عاجل", "STAT": "فوري حرج", "RETURN": "إرجاع",
}
TEMPERATURE_LABELS = {
    "AMBIENT": "حرارة الغرفة", "CHILLED": "مبرّد", "FROZEN": "مجمّد",
    "DEEP_FROZEN": "تجميد عميق", "CONTROLLED": "نطاق مخصص",
}
EXCEPTION_LABELS = {
    "NO_SAMPLES": "لا توجد عينات",
    "SAMPLES_NOT_READY": "العينات غير جاهزة",
    "FACILITY_CLOSED": "المركز مغلق",
    "NO_STAFF": "لا يوجد موظف لتسليم العينات",
    "CANCELLED_BEFORE_PICKUP": "إلغاء قبل الالتقاط",
    "PICKUP_DELAYED": "تأخر الالتقاط",
    "DELIVERY_DELAYED": "تأخر التسليم",
    "TEMPERATURE_BREACH": "مخالفة درجة الحرارة",
    "BOX_DAMAGED": "تلف أو مشكلة في الصندوق",
    "LOCATION_UNREACHABLE": "تعذر الوصول إلى الموقع",
    "VEHICLE_BREAKDOWN": "عطل في المركبة",
    "OTHER": "سبب آخر",
}
STATUS_LABELS = {
    "DRAFT": "مسودة", "VALIDATED": "مُتحقق منها", "PENDING_APPROVAL": "بانتظار الموافقة",
    "REJECTED": "مرفوضة", "PENDING_ASSIGNMENT": "بانتظار الإسناد", "PLANNED": "مخططة",
    "ASSIGNED": "مُسندة", "PUBLISHED": "منشورة", "IN_PROGRESS": "قيد التنفيذ",
    "ARRIVED_PICKUP": "وصل للالتقاط", "PICKED_UP": "تم الالتقاط",
    "ARRIVED_DELIVERY": "وصل للتسليم", "DELIVERED": "تم التسليم", "COMPLETED": "مكتملة",
    "CANCELLED_BEFORE_PICKUP": "ملغاة قبل الالتقاط", "EXCEPTION": "حالة استثنائية",
    "FAILED": "فاشلة", "UNPLANNABLE": "غير قابلة للتخطيط",
}


# =========================================================== المصادقة =======

@rate_limit(10, key="login")
async def login(request: Request) -> Response:
    payload = await read_json(request)
    require_fields(payload, "email", "password")
    user, tokens = await run_in_threadpool(
        auth_service.authenticate,
        str(payload["email"]).strip(), str(payload["password"]),
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    response = ok({
        "user": user.to_public(),
        "access_token": tokens.access_token,
        "expires_in": tokens.expires_in,
    })
    response.set_cookie("masar_access", tokens.access_token,
                        max_age=tokens.expires_in, **_cookie_kwargs())
    response.set_cookie("masar_refresh", tokens.refresh_token,
                        max_age=tokens.refresh_expires_in, **_cookie_kwargs())
    return response


@rate_limit(30, key="refresh")
async def refresh(request: Request) -> Response:
    payload = await read_json(request)
    token = payload.get("refresh_token") or request.cookies.get("masar_refresh")
    if not token:
        # غياب الرمز حالة مصادقة طبيعية عند أول زيارة، وليست خطأ في المدخلات
        raise Unauthorized("لا توجد جلسة سابقة")
    user, tokens = await run_in_threadpool(auth_service.refresh_session, token)
    response = ok({"user": user.to_public(), "access_token": tokens.access_token,
                   "expires_in": tokens.expires_in})
    response.set_cookie("masar_access", tokens.access_token,
                        max_age=tokens.expires_in, **_cookie_kwargs())
    response.set_cookie("masar_refresh", tokens.refresh_token,
                        max_age=tokens.refresh_expires_in, **_cookie_kwargs())
    return response


async def logout(request: Request) -> Response:
    user = optional_user(request)
    if user:
        await run_in_threadpool(auth_service.logout, user, ip_address=client_ip(request))
    response = ok({"logged_out": True})
    response.delete_cookie("masar_access", path="/")
    response.delete_cookie("masar_refresh", path="/")
    return response


async def me(request: Request) -> Response:
    return ok({"user": current_user(request).to_public()})


@rate_limit(60, key="session")
async def session_probe(request: Request) -> Response:
    """فحص الجلسة عند فتح التطبيق — يعيد 200 دائمًا مع المستخدم أو ``null``.

    وجودها يمنع ظهور أخطاء 401 في سجل المتصفح عند أول زيارة، وهي حالة
    طبيعية لا خطأ. الرفض الحقيقي يبقى 401 على بقية النقاط.
    """
    user = optional_user(request)
    if user is not None:
        return ok({"user": user.to_public(), "renewed": False})

    token = request.cookies.get("masar_refresh")
    if not token:
        return ok({"user": None})

    try:
        refreshed, tokens = await run_in_threadpool(auth_service.refresh_session, token)
    except Exception:
        response = ok({"user": None})
        response.delete_cookie("masar_access", path="/")
        response.delete_cookie("masar_refresh", path="/")
        return response

    response = ok({
        "user": refreshed.to_public(),
        "renewed": True,
        "access_token": tokens.access_token,
        "expires_in": tokens.expires_in,
    })
    response.set_cookie("masar_access", tokens.access_token,
                        max_age=tokens.expires_in, **_cookie_kwargs())
    response.set_cookie("masar_refresh", tokens.refresh_token,
                        max_age=tokens.refresh_expires_in, **_cookie_kwargs())
    return response


async def change_password(request: Request) -> Response:
    payload = await read_json(request)
    require_fields(payload, "current_password", "new_password")
    await run_in_threadpool(
        auth_service.change_password, current_user(request),
        str(payload["current_password"]), str(payload["new_password"]),
    )
    return ok({"changed": True})


@rate_limit(5, key="reset")
async def request_password_reset(request: Request) -> Response:
    payload = await read_json(request)
    require_fields(payload, "email")
    token = await run_in_threadpool(
        auth_service.request_password_reset, str(payload["email"]))
    # لا يُكشف وجود الحساب من عدمه
    data: dict[str, Any] = {"sent": True}
    if token and get_config().environment != "production":
        data["dev_token"] = token
    return ok(data)


async def complete_password_reset(request: Request) -> Response:
    payload = await read_json(request)
    require_fields(payload, "token", "new_password")
    await run_in_threadpool(
        auth_service.complete_password_reset,
        str(payload["token"]), str(payload["new_password"]))
    return ok({"reset": True})


# ==================================================== البيانات الرئيسية =====

_ENTITY_PERMISSIONS = {
    "regions": ("geo.read", "geo.write"),
    "cities": ("geo.read", "geo.write"),
    "hubs": ("hubs.read", "hubs.write"),
    "facilities": ("facilities.read", "facilities.write"),
    "drivers": ("drivers.read", "drivers.write"),
    "vehicles": ("vehicles.read", "vehicles.write"),
    "boxes": ("vehicles.read", "vehicles.write"),
    "temperature_ranges": ("settings.read", "settings.write"),
}


def _entity_permission(entity: str, write: bool) -> str:
    if entity not in _ENTITY_PERMISSIONS:
        raise NotFound(f"كيان غير معروف: {entity}")
    return _ENTITY_PERMISSIONS[entity][1 if write else 0]


async def md_schema(request: Request) -> Response:
    entity = request.path_params["entity"]
    user = current_user(request)
    if not user.can(_entity_permission(entity, False)):
        raise Forbidden("لا تملك صلاحية عرض هذا الكيان")
    return ok(masterdata.describe(entity))


async def md_list(request: Request) -> Response:
    entity = request.path_params["entity"]
    user = current_user(request)
    if not user.can(_entity_permission(entity, False)):
        raise Forbidden("لا تملك صلاحية عرض هذا الكيان")

    filters = {
        key: value for key, value in request.query_params.items()
        if key not in ("search", "limit", "offset", "include_inactive")
    }
    rows, total = await run_in_threadpool(
        masterdata.list_entities, context_of(request), entity,
        search=request.query_params.get("search"),
        filters=filters,
        include_inactive=request.query_params.get("include_inactive") == "true",
        limit=query_int(request, "limit", 200, maximum=1000),
        offset=query_int(request, "offset", 0),
    )
    return paginated([dict(row) for row in rows], total=total,
                     page=1, page_size=len(rows) or 1)


async def md_get(request: Request) -> Response:
    entity = request.path_params["entity"]
    user = current_user(request)
    if not user.can(_entity_permission(entity, False)):
        raise Forbidden("لا تملك صلاحية عرض هذا الكيان")
    row = await run_in_threadpool(
        masterdata.get_entity, context_of(request), entity, request.path_params["id"])
    return ok(dict(row))


async def md_create(request: Request) -> Response:
    entity = request.path_params["entity"]
    user = current_user(request)
    if not user.can(_entity_permission(entity, True)):
        raise Forbidden("لا تملك صلاحية إنشاء سجل في هذا الكيان")
    payload = await read_json(request)
    row = await run_in_threadpool(
        masterdata.create_entity, context_of(request), entity, payload,
        ip_address=client_ip(request), actor_name=user.full_name,
    )
    return created(dict(row))


async def md_update(request: Request) -> Response:
    entity = request.path_params["entity"]
    user = current_user(request)
    if not user.can(_entity_permission(entity, True)):
        raise Forbidden("لا تملك صلاحية تعديل هذا الكيان")
    payload = await read_json(request)
    row = await run_in_threadpool(
        masterdata.update_entity, context_of(request), entity,
        request.path_params["id"], payload,
        reason=payload.get("reason"), ip_address=client_ip(request),
        actor_name=user.full_name,
    )
    return ok(dict(row))


async def md_void(request: Request) -> Response:
    entity = request.path_params["entity"]
    user = current_user(request)
    if not user.can(_entity_permission(entity, True)):
        raise Forbidden("لا تملك صلاحية إبطال سجل في هذا الكيان")
    payload = await read_json(request)
    reason = get_reason(payload)
    await run_in_threadpool(
        masterdata.void_entity, context_of(request), entity,
        request.path_params["id"], reason,
        ip_address=client_ip(request), actor_name=user.full_name,
    )
    return ok({"voided": True})


# ====================================================== المستخدمون ==========

@require("users.read")
async def users_list(request: Request) -> Response:
    with session(context_of(request)) as conn:
        rows = conn.fetch_all(
            "SELECT u.id::text AS id, u.email, u.full_name, u.phone, u.role, "
            "u.is_active, u.must_change_password, u.last_login_at, u.created_at, "
            "coalesce(array_agg(DISTINCT s.scope_type || ':' || s.scope_id::text) "
            "  FILTER (WHERE s.id IS NOT NULL), '{}') AS scopes "
            "FROM users u LEFT JOIN user_scopes s ON s.user_id = u.id "
            "GROUP BY u.id ORDER BY u.full_name LIMIT 500"
        )
    return ok([dict(row) for row in rows])


@require("users.write")
async def users_create(request: Request) -> Response:
    payload = await read_json(request)
    require_fields(payload, "email", "full_name", "role", "password")
    role = str(payload["role"])
    if role not in set(Role):
        raise ValidationError("دور غير معروف")
    auth_service.validate_password_strength(str(payload["password"]))

    from masar_core.constants import AuditAction
    from masar_core.security import password_hasher
    from masar_db.driver import transaction

    context = context_of(request)
    scopes = payload.get("scopes") or []

    def work() -> dict[str, Any]:
        with transaction(context) as conn:
            user_id = conn.fetch_value(
                "INSERT INTO users (email, full_name, phone, password_hash, role, "
                "must_change_password, created_by, is_test_data) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7::uuid,$8) RETURNING id::text",
                [str(payload["email"]).strip().lower(), str(payload["full_name"]),
                 payload.get("phone"), password_hasher.hash(str(payload["password"])),
                 role, bool(payload.get("must_change_password", True)),
                 context.user_id, bool(payload.get("is_test_data", False))],
            )
            for scope in scopes:
                conn.execute(
                    "INSERT INTO user_scopes (user_id, scope_type, scope_id) "
                    "VALUES ($1::uuid,$2,$3::uuid) ON CONFLICT DO NOTHING",
                    [user_id, scope["scope_type"], scope["scope_id"]],
                )
            audit_service.record(
                conn, context, AuditAction.USER_CREATE,
                entity_type="user", entity_id=user_id,
                entity_label=str(payload["email"]),
                new_value={"role": role, "scopes": scopes},
                ip_address=client_ip(request),
            )
            return {"id": user_id}

    return created(await run_in_threadpool(work))


@require("users.write")
async def users_update(request: Request) -> Response:
    payload = await read_json(request)
    user_id = request.path_params["id"]
    context = context_of(request)

    from masar_core.constants import AuditAction
    from masar_db.driver import transaction

    def work() -> dict[str, Any]:
        with transaction(context) as conn:
            before = conn.fetch_one(
                "SELECT role, is_active, full_name, phone FROM users WHERE id = $1::uuid",
                [user_id])
            if before is None:
                raise NotFound("المستخدم غير موجود")
            assignments, params = [], []
            for field in ("full_name", "phone", "is_active", "role"):
                if field in payload:
                    params.append(payload[field])
                    assignments.append(f"{field} = ${len(params)}")
            if assignments:
                params.append(user_id)
                conn.execute(
                    f"UPDATE users SET {', '.join(assignments)} "
                    f"WHERE id = ${len(params)}::uuid",
                    params,
                )
            if "scopes" in payload:
                conn.execute("DELETE FROM user_scopes WHERE user_id = $1::uuid", [user_id])
                for scope in payload["scopes"]:
                    conn.execute(
                        "INSERT INTO user_scopes (user_id, scope_type, scope_id) "
                        "VALUES ($1::uuid,$2,$3::uuid)",
                        [user_id, scope["scope_type"], scope["scope_id"]],
                    )
            action = (
                AuditAction.ROLE_CHANGE
                if "role" in payload and payload["role"] != before["role"]
                else AuditAction.USER_UPDATE
            )
            audit_service.record(
                conn, context, action, entity_type="user", entity_id=user_id,
                old_value=dict(before), new_value=dict(payload),
                reason=payload.get("reason"), ip_address=client_ip(request),
            )
            return {"updated": True}

    return ok(await run_in_threadpool(work))


# ======================================================== الإعدادات =========

@require("settings.read")
async def settings_list(request: Request) -> Response:
    data = await run_in_threadpool(
        settings_service.explain_all, context_of(request),
        region_id=request.query_params.get("region_id"),
        city_id=request.query_params.get("city_id"),
        hub_id=request.query_params.get("hub_id"),
    )
    overrides = await run_in_threadpool(settings_service.list_overrides, context_of(request))
    return ok({"effective": data, "overrides": [dict(row) for row in overrides]})


@require("settings.write")
async def settings_set(request: Request) -> Response:
    payload = await read_json(request)
    require_fields(payload, "key", "value", "scope_type")
    reason = get_reason(payload)
    result = await run_in_threadpool(
        settings_service.set_override, context_of(request),
        key=str(payload["key"]), value=payload["value"],
        scope_type=str(payload["scope_type"]), scope_id=payload.get("scope_id"),
        reason=reason, ip_address=client_ip(request),
        actor_name=current_user(request).full_name,
    )
    return ok(result)


@require("settings.write")
async def settings_delete(request: Request) -> Response:
    payload = await read_json(request)
    reason = get_reason(payload)
    await run_in_threadpool(
        settings_service.delete_override, context_of(request),
        request.path_params["id"], reason, ip_address=client_ip(request))
    return ok({"deleted": True})


# ===================================================== رفع الجدول ==========

async def import_template(request: Request) -> Response:
    fmt = request.query_params.get("format", "csv")
    if fmt == "xlsx":
        content = await run_in_threadpool(imports_service.build_template_xlsx)
        if content is None:
            raise ValidationError("توليد ملفات Excel غير متاح — استخدم صيغة CSV")
        return Response(
            content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"content-disposition":
                     'attachment; filename="masar-schedule-template.xlsx"'},
        )
    content = await run_in_threadpool(imports_service.build_template_csv)
    return Response(content, media_type="text/csv; charset=utf-8",
                    headers={"content-disposition":
                             'attachment; filename="masar-schedule-template.csv"'})


@require("schedule.upload")
async def import_upload(request: Request) -> Response:
    """رفع الملف كجسم خام — اسم الملف في ترويسة ``X-File-Name``."""
    content = await request.body()
    if not content:
        raise ValidationError("لم يُرفع أي ملف")
    cfg = get_config().security
    if len(content) > cfg.max_upload_bytes:
        raise ValidationError("حجم الملف يتجاوز الحد المسموح")

    filename = request.headers.get("x-file-name", "schedule.csv")
    content_type = request.headers.get("content-type", "text/csv")

    from .services import storage

    key = storage.build_key("imports", filename, "application/pdf")
    key = key.rsplit(".", 1)[0] + (".xlsx" if filename.lower().endswith("x") else ".csv")
    await run_in_threadpool(storage.get_store().put, key, content, content_type)

    result = await run_in_threadpool(
        imports_service.create_import, context_of(request),
        filename=filename, content=content, content_type=content_type,
        storage_key=key,
        is_test_data=request.query_params.get("test_data") == "true",
        ip_address=client_ip(request),
    )
    return created(result)


@require("schedule.upload")
async def import_validate(request: Request) -> Response:
    payload = await read_json(request)
    result = await run_in_threadpool(
        imports_service.validate_import, context_of(request),
        request.path_params["id"], payload.get("mapping"))
    return ok(result)


@require("schedule.read")
async def import_get(request: Request) -> Response:
    return ok(await run_in_threadpool(
        imports_service.get_import, context_of(request), request.path_params["id"]))


@require("schedule.read")
async def import_list(request: Request) -> Response:
    rows = await run_in_threadpool(imports_service.list_imports, context_of(request))
    return ok([dict(row) for row in rows])


@require("schedule.read")
async def import_errors_csv(request: Request) -> Response:
    content = await run_in_threadpool(
        imports_service.build_error_report_csv, context_of(request),
        request.path_params["id"])
    return Response(content, media_type="text/csv; charset=utf-8",
                    headers={"content-disposition":
                             'attachment; filename="import-errors.csv"'})


@require("schedule.commit")
async def import_exclude(request: Request) -> Response:
    payload = await read_json(request)
    rows = [int(n) for n in (payload.get("row_numbers") or [])]
    count = await run_in_threadpool(
        imports_service.exclude_rows, context_of(request),
        request.path_params["id"], rows)
    return ok({"excluded": count})


@require("schedule.commit")
async def import_commit(request: Request) -> Response:
    payload = await read_json(request)
    result = await run_in_threadpool(
        imports_service.commit_import, context_of(request), request.path_params["id"],
        skip_invalid=bool(payload.get("skip_invalid", True)),
        ip_address=client_ip(request))
    return ok(result)


# ========================================================== التخطيط =========

@require("plan.optimize")
async def plan_run(request: Request) -> Response:
    payload = await read_json(request)
    require_fields(payload, "hub_ids", "dates")
    from masar_core.timeutil import parse_date

    dates = [parse_date(item, field="تاريخ") for item in payload["dates"]]
    result = await run_in_threadpool(
        planning.run_planning, context_of(request),
        hub_ids=[str(h) for h in payload["hub_ids"]],
        dates=dates,
        plan_name=payload.get("name"),
        import_id=payload.get("import_id"),
        time_limit_seconds=payload.get("time_limit_seconds"),
        routing_provider=payload.get("routing_provider"),
        fallback_to_estimate=bool(payload.get("fallback_to_estimate", True)),
        baseline_plan_id=payload.get("baseline_plan_id"),
        seed=payload.get("seed"),
        ip_address=client_ip(request),
    )
    return created(result)


@require("plan.read")
async def plan_list(request: Request) -> Response:
    rows = await run_in_threadpool(planning.list_plans, context_of(request))
    return ok([dict(row) for row in rows])


@require("plan.read")
async def plan_get(request: Request) -> Response:
    return ok(await run_in_threadpool(
        planning.get_plan, context_of(request), request.path_params["id"]))


@require("plan.approve")
async def plan_approve(request: Request) -> Response:
    payload = await read_json(request)
    return ok(await run_in_threadpool(
        planning.approve_plan, context_of(request), request.path_params["id"],
        acknowledge_estimated=bool(payload.get("acknowledge_estimated")),
        ip_address=client_ip(request)))


@require("plan.dispatch")
async def plan_dispatch(request: Request) -> Response:
    return ok(await run_in_threadpool(
        planning.dispatch_plan, context_of(request), request.path_params["id"],
        ip_address=client_ip(request)))


@require("plan.compare")
async def plan_compare(request: Request) -> Response:
    plan_a = request.query_params.get("a")
    plan_b = request.query_params.get("b")
    if not plan_a or not plan_b:
        raise ValidationError("حدّد الخطتين a و b")
    return ok(await run_in_threadpool(
        planning.compare_plans, context_of(request), plan_a, plan_b))


# =========================================================== الرحلات ========

@require("routes.read")
async def routes_list(request: Request) -> Response:
    from masar_core.timeutil import parse_date

    service_date = request.query_params.get("service_date")
    rows = await run_in_threadpool(
        operations.list_routes, context_of(request),
        hub_id=request.query_params.get("hub_id"),
        service_date=parse_date(service_date) if service_date else None,
        status=request.query_params.get("status"),
        driver_id=request.query_params.get("driver_id"),
        limit=query_int(request, "limit", 200, maximum=1000),
    )
    return ok([dict(row) for row in rows])


@require("routes.read")
async def route_get(request: Request) -> Response:
    return ok(await run_in_threadpool(
        planning.get_route_detail, context_of(request), request.path_params["id"]))


@require("routes.assign")
async def route_candidates(request: Request) -> Response:
    return ok(await run_in_threadpool(
        operations.suggest_drivers, context_of(request), request.path_params["id"]))


@require("routes.assign")
async def route_assign(request: Request) -> Response:
    payload = await read_json(request)
    require_fields(payload, "driver_id")
    return ok(await run_in_threadpool(
        operations.assign_route, context_of(request), request.path_params["id"],
        driver_id=str(payload["driver_id"]),
        vehicle_id=payload.get("vehicle_id"), box_id=payload.get("box_id"),
        reason=payload.get("reason"), force=bool(payload.get("force", False)),
        ip_address=client_ip(request)))


@require("routes.unassign")
async def route_unassign(request: Request) -> Response:
    payload = await read_json(request)
    reason = get_reason(payload)
    return ok(await run_in_threadpool(
        operations.unassign_route, context_of(request), request.path_params["id"],
        reason, ip_address=client_ip(request)))


@require("routes.publish")
async def publish_day(request: Request) -> Response:
    payload = await read_json(request)
    require_fields(payload, "hub_id", "service_date")
    service_date = get_date(payload, "service_date")
    return ok(await run_in_threadpool(
        operations.publish_day, context_of(request),
        hub_id=str(payload["hub_id"]), service_date=service_date,
        plan_id=payload.get("plan_id"), ip_address=client_ip(request)))


@require("routes.publish")
async def unpublish_day(request: Request) -> Response:
    payload = await read_json(request)
    require_fields(payload, "hub_id", "service_date")
    reason = get_reason(payload)
    return ok(await run_in_threadpool(
        operations.unpublish_day, context_of(request),
        hub_id=str(payload["hub_id"]), service_date=get_date(payload, "service_date"),
        reason=reason, ip_address=client_ip(request)))


@require("routes.modify_published")
async def route_modify(request: Request) -> Response:
    payload = await read_json(request)
    reason = get_reason(payload)
    return ok(await run_in_threadpool(
        operations.modify_published_route, context_of(request),
        request.path_params["id"],
        change_kind=str(payload.get("change_kind", "OTHER")),
        reason=reason,
        add_shipment_ids=payload.get("add_shipment_ids"),
        remove_shipment_ids=payload.get("remove_shipment_ids"),
        new_order=payload.get("new_order"),
        ip_address=client_ip(request)))


# ==================================================== تطبيق السائق ==========

@require("routes.read")
async def driver_routes(request: Request) -> Response:
    from masar_core.timeutil import parse_date

    service_date = request.query_params.get("service_date")
    rows = await run_in_threadpool(
        execution.my_routes, context_of(request),
        service_date=parse_date(service_date) if service_date else None,
        days=query_int(request, "days", 7, maximum=60))
    return ok([dict(row) for row in rows])


@require("routes.execute")
async def driver_start_route(request: Request) -> Response:
    payload = await read_json(request)
    return ok(await run_in_threadpool(
        execution.start_route, context_of(request), request.path_params["id"],
        lat=get_float(payload, "lat"), lon=get_float(payload, "lon"),
        occurred_at=get_datetime(payload, "occurred_at", required=False),
        client_event_id=payload.get("client_event_id"),
        was_offline=bool(payload.get("was_offline", False)),
        ip_address=client_ip(request)))


@require("routes.execute")
async def driver_arrive(request: Request) -> Response:
    payload = await read_json(request)
    return ok(await run_in_threadpool(
        execution.mark_arrived, context_of(request), request.path_params["id"],
        lat=get_float(payload, "lat"), lon=get_float(payload, "lon"),
        accuracy_m=get_float(payload, "accuracy_m"),
        occurred_at=get_datetime(payload, "occurred_at", required=False),
        client_event_id=payload.get("client_event_id"),
        was_offline=bool(payload.get("was_offline", False)),
        ip_address=client_ip(request)))


@require("routes.execute")
async def driver_pickup(request: Request) -> Response:
    payload = await read_json(request)
    return ok(await run_in_threadpool(
        execution.mark_picked_up, context_of(request), request.path_params["id"],
        lat=get_float(payload, "lat"), lon=get_float(payload, "lon"),
        piece_count=get_int(payload, "piece_count", None, minimum=1),
        occurred_at=get_datetime(payload, "occurred_at", required=False),
        client_event_id=payload.get("client_event_id"),
        was_offline=bool(payload.get("was_offline", False)),
        ip_address=client_ip(request)))


@require("routes.execute")
async def driver_deliver(request: Request) -> Response:
    payload = await read_json(request)
    return ok(await run_in_threadpool(
        execution.mark_delivered, context_of(request), request.path_params["id"],
        lat=get_float(payload, "lat"), lon=get_float(payload, "lon"),
        receiver_name=payload.get("receiver_name"),
        occurred_at=get_datetime(payload, "occurred_at", required=False),
        client_event_id=payload.get("client_event_id"),
        was_offline=bool(payload.get("was_offline", False)),
        ip_address=client_ip(request)))


@require("exceptions.record")
async def record_exception(request: Request) -> Response:
    payload = await read_json(request)
    require_fields(payload, "shipment_id", "reason")
    return created(await run_in_threadpool(
        execution.record_exception, context_of(request),
        shipment_id=str(payload["shipment_id"]), reason=str(payload["reason"]),
        note=payload.get("note"), route_stop_id=payload.get("stop_id"),
        lat=get_float(payload, "lat"), lon=get_float(payload, "lon"),
        occurred_at=get_datetime(payload, "occurred_at", required=False),
        client_event_id=payload.get("client_event_id"),
        was_offline=bool(payload.get("was_offline", False)),
        has_proof=bool(payload.get("has_proof", False)),
        ip_address=client_ip(request)))


@require("exceptions.resolve")
async def resolve_exception(request: Request) -> Response:
    payload = await read_json(request)
    require_fields(payload, "action_taken", "new_shipment_status")
    return ok(await run_in_threadpool(
        execution.resolve_exception, context_of(request), request.path_params["id"],
        action_taken=str(payload["action_taken"]),
        new_shipment_status=str(payload["new_shipment_status"]),
        resolution=payload.get("resolution"), ip_address=client_ip(request)))


@require("routes.execute")
async def driver_sync(request: Request) -> Response:
    payload = await read_json(request)
    return ok(await run_in_threadpool(
        execution.sync_offline_events, context_of(request),
        payload.get("events") or []))


@require("tracking.publish")
async def positions_publish(request: Request) -> Response:
    payload = await read_json(request)
    return ok(await run_in_threadpool(
        tracking.publish_positions, context_of(request), payload.get("points") or []))


@require("alerts.read")
async def notifications_list(request: Request) -> Response:
    from .services import notifications as notifications_service

    return ok(await run_in_threadpool(
        notifications_service.list_notifications, context_of(request),
        status=request.query_params.get("status"),
        limit=query_int(request, "limit", 100, maximum=500)))


@require("integrations.read")
async def storage_status(request: Request) -> Response:
    """حالة التخزين والتشفير — هل المستندات مشفَّرة فعلًا عند التخزين؟"""
    from .services import storage as storage_service

    return ok(await run_in_threadpool(storage_service.storage_status))


async def routing_status_endpoint(request: Request) -> Response:
    """حالة مزوّد الطرق — يستدعي الخدمة فعليًا لا يقرأ الإعداد فقط."""
    from .services import routing_status as routing_status_service

    return ok(await run_in_threadpool(routing_status_service.provider_status))


async def notifications_status(request: Request) -> Response:
    """حالة تكامل الإشعارات — تُعلن بصدق أن لا مزوّد عند غيابه (§34)."""
    from .services import notifications as notifications_service

    return ok(await run_in_threadpool(notifications_service.provider_status))


@require("alerts.act")
async def notifications_flush(request: Request) -> Response:
    """محاولة إرسال المعلّق الآن — للتشخيص اليدوي بعد إصلاح عطل مزوّد."""
    from .services import notifications as notifications_service

    return ok(await run_in_threadpool(notifications_service.deliver_pending))


# ======================================================== المستندات =========

@require("documents.upload")
async def document_upload(request: Request) -> Response:
    content = await request.body()
    shipment_id = request.query_params.get("shipment_id")
    doc_kind = request.query_params.get("doc_kind", "OTHER")
    if not shipment_id:
        raise ValidationError("shipment_id مطلوب")
    return created(await run_in_threadpool(
        execution.upload_document, context_of(request),
        shipment_id=shipment_id, doc_kind=doc_kind, content=content,
        declared_type=request.headers.get("content-type"),
        original_name=request.headers.get("x-file-name"),
        route_stop_id=request.query_params.get("stop_id"),
        exception_id=request.query_params.get("exception_id"),
        lat=float(request.query_params["lat"]) if request.query_params.get("lat") else None,
        lon=float(request.query_params["lon"]) if request.query_params.get("lon") else None,
        ip_address=client_ip(request)))


@require("documents.read")
async def document_get(request: Request) -> Response:
    content, content_type, name = await run_in_threadpool(
        execution.read_document, context_of(request), request.path_params["id"])
    return Response(content, media_type=content_type,
                    headers={"content-disposition": f'inline; filename="{name}"',
                             "cache-control": "private, max-age=300"})


@require("documents.read")
async def documents_for_shipment(request: Request) -> Response:
    with session(context_of(request)) as conn:
        rows = conn.fetch_all(
            "SELECT id::text AS id, doc_kind, content_type, byte_size, sha256, "
            "captured_at, uploaded_at, lat, lon FROM documents "
            "WHERE shipment_id = $1::uuid ORDER BY uploaded_at DESC",
            [request.path_params["id"]],
        )
    return ok([dict(row) for row in rows])


# ==================================================== الطلبات الفورية =======

@require("ondemand.create")
async def ondemand_create(request: Request) -> Response:
    payload = await read_json(request)
    require_fields(payload, "pickup_facility_id", "dropoff_facility_id",
                   "pickup_window_from", "pickup_window_to", "sla_deadline")
    return created(await run_in_threadpool(
        ondemand.create_request, context_of(request),
        pickup_facility_id=str(payload["pickup_facility_id"]),
        dropoff_facility_id=str(payload["dropoff_facility_id"]),
        pickup_window_from=get_datetime(payload, "pickup_window_from"),
        pickup_window_to=get_datetime(payload, "pickup_window_to"),
        sla_deadline=get_datetime(payload, "sla_deadline"),
        service_type=str(payload.get("service_type", "URGENT")),
        piece_count=get_int(payload, "piece_count", 1, minimum=1) or 1,
        temperature_mode=str(payload.get("temperature_mode", "AMBIENT")),
        sample_types=payload.get("sample_types"),
        notes=payload.get("notes"),
        contact_name=payload.get("contact_name"),
        contact_phone=payload.get("contact_phone"),
        ip_address=client_ip(request)))


@require("shipments.read")
async def ondemand_list(request: Request) -> Response:
    rows = await run_in_threadpool(
        ondemand.list_requests, context_of(request),
        status=request.query_params.get("status"),
        hub_id=request.query_params.get("hub_id"),
        limit=query_int(request, "limit", 100, maximum=500))
    return ok([dict(row) for row in rows])


@require("ondemand.review")
async def ondemand_review(request: Request) -> Response:
    payload = await read_json(request)
    approve = bool(payload.get("approve", True))
    return ok(await run_in_threadpool(
        ondemand.review_request, context_of(request), request.path_params["id"],
        approve=approve, reason=payload.get("reason"), ip_address=client_ip(request)))


@require("routes.assign")
async def ondemand_options(request: Request) -> Response:
    return ok(await run_in_threadpool(
        ondemand.insertion_options, context_of(request), request.path_params["id"]))


@require("routes.assign")
async def ondemand_assign(request: Request) -> Response:
    payload = await read_json(request)
    require_fields(payload, "route_id", "pickup_position", "delivery_position")
    return ok(await run_in_threadpool(
        ondemand.assign_on_demand, context_of(request), request.path_params["id"],
        route_id=str(payload["route_id"]),
        pickup_position=int(payload["pickup_position"]),
        delivery_position=int(payload["delivery_position"]),
        reason=payload.get("reason"), ip_address=client_ip(request)))


@require("ondemand.cancel_own", "shipments.cancel", any_of=True)
async def ondemand_cancel(request: Request) -> Response:
    payload = await read_json(request)
    reason = get_reason(payload)
    return ok(await run_in_threadpool(
        ondemand.cancel_request, context_of(request), request.path_params["id"],
        reason, ip_address=client_ip(request)))


# ========================================================== التنبيهات =======

@require("alerts.read")
async def alerts_list(request: Request) -> Response:
    rows, total = await run_in_threadpool(
        alerts_service.list_alerts, context_of(request),
        hub_id=request.query_params.get("hub_id"),
        severity=request.query_params.get("severity"),
        alert_type=request.query_params.get("type"),
        only_open=request.query_params.get("only_open", "true") == "true",
        limit=query_int(request, "limit", 100, maximum=500),
        offset=query_int(request, "offset", 0))
    return paginated([dict(row) for row in rows], total=total, page=1,
                     page_size=len(rows) or 1)


@require("alerts.read")
async def alerts_summary(request: Request) -> Response:
    rows = await run_in_threadpool(alerts_service.summary_by_hub, context_of(request))
    return ok([dict(row) for row in rows])


@require("alerts.act")
async def alert_ack(request: Request) -> Response:
    return ok(await run_in_threadpool(
        alerts_service.acknowledge, context_of(request), request.path_params["id"],
        ip_address=client_ip(request)))


@require("alerts.act")
async def alert_resolve(request: Request) -> Response:
    payload = await read_json(request)
    note = str(payload.get("action_note") or "").strip()
    if len(note) < 3:
        raise ValidationError("إغلاق التنبيه يتطلب وصف الإجراء المتخذ")
    return ok(await run_in_threadpool(
        alerts_service.resolve, context_of(request), request.path_params["id"], note,
        ip_address=client_ip(request)))


@require("alerts.read")
async def alerts_scan(request: Request) -> Response:
    """تشغيل يدوي لفحص التنبيهات (يعمل دوريًا كذلك في الخلفية)."""
    return ok(await run_in_threadpool(alerts_service.scan_operational_alerts))


# ========================================================== التتبع ==========

@require("tracking.read")
async def tracking_live(request: Request) -> Response:
    return ok(await run_in_threadpool(
        tracking.live_positions, context_of(request),
        hub_id=request.query_params.get("hub_id")))


@require("tracking.read")
async def tracking_route(request: Request) -> Response:
    return ok(await run_in_threadpool(
        tracking.route_track, context_of(request), request.path_params["id"]))


# ========================================================== الحرارة =========

async def temperature_status(request: Request) -> Response:
    return ok(temperature_service.provider_status())


@require("temperature.read")
async def temperature_shipment(request: Request) -> Response:
    return ok(await run_in_threadpool(
        temperature_service.shipment_temperature, context_of(request),
        request.path_params["id"]))


@require("temperature.ingest")
async def temperature_ingest(request: Request) -> Response:
    payload = await read_json(request)
    return ok(await run_in_threadpool(
        temperature_service.ingest_readings, context_of(request),
        payload.get("readings") or [],
        source=str(payload.get("source", "SENSOR")),
        ip_address=client_ip(request)))


@require("temperature.read")
async def temperature_poll(request: Request) -> Response:
    return ok(await run_in_threadpool(temperature_service.poll_provider))


@require("alerts.act")
async def temperature_resolve(request: Request) -> Response:
    payload = await read_json(request)
    note = str(payload.get("action_taken") or "").strip()
    if len(note) < 3:
        raise ValidationError("حسم مخالفة الحرارة يتطلب وصف الإجراء المتخذ")
    return ok(await run_in_threadpool(
        temperature_service.resolve_breach, context_of(request),
        request.path_params["id"], note, ip_address=client_ip(request)))


# ========================================================= الشحنات =========

@require("shipments.read")
async def shipments_list(request: Request) -> Response:
    clauses = ["1=1"]
    params: list[Any] = []

    def add(clause: str, value: Any) -> None:
        params.append(value)
        clauses.append(clause.replace("?", f"${len(params)}"))

    for key, expression in (
        ("hub_id", "s.hub_id = ?::uuid"),
        ("status", "s.status = ?"),
        ("driver_id", "s.driver_id = ?::uuid"),
        ("route_id", "s.route_id = ?::uuid"),
        ("request_kind", "s.request_kind = ?"),
    ):
        value = request.query_params.get(key)
        if value:
            add(expression, value)
    service_date = request.query_params.get("service_date")
    if service_date:
        add("s.service_date = ?::date", service_date)
    search = request.query_params.get("search")
    if search:
        add("(s.reference ILIKE ? OR s.external_reference ILIKE ?)", f"%{search}%")
        clauses[-1] = clauses[-1].replace("ILIKE ?", f"ILIKE ${len(params)}")

    limit = query_int(request, "limit", 100, maximum=1000)
    with session(context_of(request)) as conn:
        total = int(conn.fetch_value(
            f"SELECT count(*) FROM shipments s WHERE {' AND '.join(clauses)}", params) or 0)
        rows = conn.fetch_all(
            "SELECT s.id::text AS id, s.reference, s.external_reference, s.status, "
            "s.request_kind, s.service_type, s.service_date, s.piece_count, "
            "s.temperature_mode, s.pickup_window_from, s.pickup_window_to, "
            "s.sla_deadline, s.planned_pickup_at, s.planned_dropoff_at, "
            "s.actual_pickup_at, s.actual_dropoff_at, s.sla_breached, s.delay_minutes, "
            "s.unplannable_reason, s.unplannable_detail, s.route_id::text AS route_id, "
            "s.driver_id::text AS driver_id, s.hub_id::text AS hub_id, "
            "pf.name_ar AS pickup_name, df.name_ar AS dropoff_name, "
            "h.name_ar AS hub_name_ar, d.full_name AS driver_name, r.reference AS route_reference "
            "FROM shipments s "
            "JOIN facilities pf ON pf.id = s.pickup_facility_id "
            "JOIN facilities df ON df.id = s.dropoff_facility_id "
            "JOIN hubs h ON h.id = s.hub_id "
            "LEFT JOIN drivers d ON d.id = s.driver_id "
            "LEFT JOIN routes r ON r.id = s.route_id "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY s.service_date DESC, s.reference LIMIT {limit}",
            params,
        )
    return paginated([dict(row) for row in rows], total=total, page=1, page_size=limit)


@require("shipments.read")
async def shipment_get(request: Request) -> Response:
    shipment_id = request.path_params["id"]
    with session(context_of(request)) as conn:
        shipment = conn.fetch_one(
            "SELECT s.*, s.id::text AS id, s.route_id::text AS route_id, "
            "s.driver_id::text AS driver_id, s.hub_id::text AS hub_id, "
            "pf.name_ar AS pickup_name, pf.facility_type AS pickup_type, "
            "df.name_ar AS dropoff_name, df.facility_type AS dropoff_type, "
            "h.name_ar AS hub_name_ar, d.full_name AS driver_name, "
            "r.reference AS route_reference "
            "FROM shipments s "
            "JOIN facilities pf ON pf.id = s.pickup_facility_id "
            "JOIN facilities df ON df.id = s.dropoff_facility_id "
            "JOIN hubs h ON h.id = s.hub_id "
            "LEFT JOIN drivers d ON d.id = s.driver_id "
            "LEFT JOIN routes r ON r.id = s.route_id "
            "WHERE s.id = $1::uuid",
            [shipment_id],
        )
        if shipment is None:
            raise NotFound("الشحنة غير موجودة أو خارج نطاقك")
        history = conn.fetch_all(
            "SELECT from_status, to_status, changed_at, actor_role, reason, source "
            "FROM shipment_status_history WHERE shipment_id = $1::uuid "
            "ORDER BY changed_at",
            [shipment_id])
        events_rows = conn.fetch_all(
            "SELECT event_type, occurred_at, received_at, lat, lon, was_offline, payload "
            "FROM shipment_events WHERE shipment_id = $1::uuid ORDER BY occurred_at",
            [shipment_id])
        exceptions = conn.fetch_all(
            "SELECT id::text AS id, reason, note, occurred_at, status, action_taken, "
            "resolution, resolved_at, keeps_obligation FROM shipment_exceptions "
            "WHERE shipment_id = $1::uuid ORDER BY occurred_at DESC",
            [shipment_id])
        documents = conn.fetch_all(
            "SELECT id::text AS id, doc_kind, content_type, byte_size, uploaded_at "
            "FROM documents WHERE shipment_id = $1::uuid ORDER BY uploaded_at",
            [shipment_id])
        alert_rows = conn.fetch_all(
            "SELECT id::text AS id, alert_type, severity, title_ar, body_ar, created_at, "
            "resolved_at FROM alerts WHERE shipment_id = $1::uuid ORDER BY created_at DESC",
            [shipment_id])
    return ok({
        "shipment": dict(shipment),
        "status_history": [dict(r) for r in history],
        "events": [dict(r) for r in events_rows],
        "exceptions": [dict(r) for r in exceptions],
        "documents": [dict(r) for r in documents],
        "alerts": [dict(r) for r in alert_rows],
    })


@require("exceptions.resolve", "shipments.read", any_of=True)
async def exceptions_list(request: Request) -> Response:
    clauses = ["1=1"]
    params: list[Any] = []
    if request.query_params.get("hub_id"):
        params.append(request.query_params["hub_id"])
        clauses.append(f"e.hub_id = ${len(params)}::uuid")
    if request.query_params.get("status"):
        params.append(request.query_params["status"])
        clauses.append(f"e.status = ${len(params)}")
    with session(context_of(request)) as conn:
        rows = conn.fetch_all(
            "SELECT e.id::text AS id, e.reason, e.note, e.occurred_at, e.status, "
            "e.keeps_obligation, e.action_taken, e.resolution, e.resolved_at, "
            "e.lat, e.lon, e.shipment_id::text AS shipment_id, "
            "e.route_id::text AS route_id, s.reference AS shipment_reference, "
            "s.status AS shipment_status, h.name_ar AS hub_name_ar, "
            "d.full_name AS driver_name, r.reference AS route_reference "
            "FROM shipment_exceptions e "
            "JOIN shipments s ON s.id = e.shipment_id "
            "JOIN hubs h ON h.id = e.hub_id "
            "LEFT JOIN drivers d ON d.id = e.reported_by_driver "
            "LEFT JOIN routes r ON r.id = e.route_id "
            f"WHERE {' AND '.join(clauses)} ORDER BY e.occurred_at DESC LIMIT 300",
            params)
    return ok([dict(row) for row in rows])


# ========================================================== التقارير ========

def _report_filters(request: Request) -> dict[str, Any]:
    from masar_core.timeutil import parse_date

    params = request.query_params
    return {
        "date_from": parse_date(params["date_from"]) if params.get("date_from") else None,
        "date_to": parse_date(params["date_to"]) if params.get("date_to") else None,
        "region_id": params.get("region_id"),
        "city_id": params.get("city_id"),
        "hub_id": params.get("hub_id"),
        "driver_id": params.get("driver_id"),
        "facility_type": params.get("facility_type"),
        "service_type": params.get("service_type"),
        "status": params.get("status"),
        "request_kind": params.get("request_kind"),
        "include_test_data": params.get("include_test_data") == "true",
    }


@require("reports.read")
async def report_kpi(request: Request) -> Response:
    return ok(await run_in_threadpool(
        reports.kpi_summary, context_of(request), **_report_filters(request)))


@require("reports.read")
async def report_grouped(request: Request) -> Response:
    rows = await run_in_threadpool(
        reports.grouped_report, context_of(request),
        group_by=request.query_params.get("group_by", "hub"),
        **_report_filters(request))
    if request.query_params.get("format") == "csv":
        content = reports.export_csv(rows, [
            ("group_label", "المجموعة"), ("shipment_count", "عدد الشحنات"),
            ("completed_count", "المكتملة"), ("sla_breached_count", "تجاوز SLA"),
            ("pickup_breached_count", "تجاوز نافذة الالتقاط"),
            ("failed_count", "الفاشلة"), ("cancelled_count", "الملغاة"),
            ("unplannable_count", "غير القابلة للتخطيط"),
            ("on_demand_count", "الطلبات الفورية"),
            ("avg_delivery_delay", "متوسط تأخر التسليم"),
            ("sla_compliance_pct", "نسبة الالتزام بـ SLA"),
        ])
        return Response(content, media_type="text/csv; charset=utf-8",
                        headers={"content-disposition":
                                 'attachment; filename="masar-report.csv"'})
    return ok(rows)


@require("reports.read")
async def report_routes(request: Request) -> Response:
    return ok(await run_in_threadpool(
        reports.route_metrics, context_of(request), **_report_filters(request)))


@require("reports.read")
async def report_exceptions(request: Request) -> Response:
    return ok(await run_in_threadpool(
        reports.exception_report, context_of(request), **_report_filters(request)))


@require("temperature.read")
async def report_temperature(request: Request) -> Response:
    return ok(await run_in_threadpool(
        reports.temperature_report, context_of(request), **_report_filters(request)))


@require("reports.read")
async def report_plan_vs_execution(request: Request) -> Response:
    return ok(await run_in_threadpool(
        reports.plan_vs_execution, context_of(request), **_report_filters(request)))


@require("hub_changes.monitor")
async def report_hub_modifications(request: Request) -> Response:
    return ok(await run_in_threadpool(
        reports.hub_modification_monitor, context_of(request), **_report_filters(request)))


@require("driver_estimation.read")
async def report_driver_capacity(request: Request) -> Response:
    return ok(await run_in_threadpool(
        reports.driver_capacity_monitor, context_of(request), **_report_filters(request)))


# =========================================================== التدقيق ========

@require("audit.read")
async def audit_query(request: Request) -> Response:
    from masar_core.timeutil import parse_date

    params = request.query_params
    limit = query_int(request, "limit", 100, maximum=1000)
    offset = query_int(request, "offset", 0)

    def work() -> tuple[list[Any], int]:
        with session(context_of(request)) as conn:
            return audit_service.query(
                conn,
                actor_user_id=params.get("actor_user_id"),
                action=params.get("action"),
                entity_type=params.get("entity_type"),
                entity_id=params.get("entity_id"),
                date_from=parse_date(params["date_from"]) if params.get("date_from") else None,
                date_to=parse_date(params["date_to"]) if params.get("date_to") else None,
                limit=limit, offset=offset,
            )

    rows, total = await run_in_threadpool(work)
    return paginated([dict(row) for row in rows], total=total, page=1, page_size=limit)


# =========================================== التحديثات الفورية (SSE) ========

async def event_stream(request: Request) -> Response:
    user = current_user(request)
    topics = set(request.query_params.get("topics", "").split(",")) - {""}
    subscriber = events_service.bus.subscribe(user.to_context(), topics)

    async def generator():
        try:
            yield (
                "event: ready\ndata: "
                + json.dumps({"connected": True, "role": user.role}, ensure_ascii=False)
                + "\n\n"
            )
            while True:
                try:
                    message = await asyncio.wait_for(subscriber.queue.get(), timeout=20.0)
                    yield f"event: masar\ndata: {message}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            events_service.bus.unsubscribe(subscriber)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache, no-transform",
            "x-accel-buffering": "no",
            "connection": "keep-alive",
        },
    )


# ============================================================ التسجيل =======

API_ROUTES: list[Route] = [
    Route("/api/health", health, methods=["GET"]),
    Route("/api/meta", meta, methods=["GET"]),
    Route("/api/meta/permissions", permission_matrix, methods=["GET"]),
    Route("/api/meta/state-machines", state_diagrams, methods=["GET"]),

    Route("/api/auth/login", login, methods=["POST"]),
    Route("/api/auth/refresh", refresh, methods=["POST"]),
    Route("/api/auth/logout", logout, methods=["POST"]),
    Route("/api/auth/me", me, methods=["GET"]),
    Route("/api/auth/session", session_probe, methods=["GET"]),
    Route("/api/auth/password", change_password, methods=["POST"]),
    Route("/api/auth/password/reset", request_password_reset, methods=["POST"]),
    Route("/api/auth/password/reset/complete", complete_password_reset, methods=["POST"]),

    Route("/api/md/{entity}/schema", md_schema, methods=["GET"]),
    Route("/api/md/{entity}", md_list, methods=["GET"]),
    Route("/api/md/{entity}", md_create, methods=["POST"]),
    Route("/api/md/{entity}/{id}", md_get, methods=["GET"]),
    Route("/api/md/{entity}/{id}", md_update, methods=["PATCH"]),
    Route("/api/md/{entity}/{id}/void", md_void, methods=["POST"]),

    Route("/api/users", users_list, methods=["GET"]),
    Route("/api/users", users_create, methods=["POST"]),
    Route("/api/users/{id}", users_update, methods=["PATCH"]),

    Route("/api/settings", settings_list, methods=["GET"]),
    Route("/api/settings", settings_set, methods=["POST"]),
    Route("/api/settings/{id}", settings_delete, methods=["DELETE"]),

    Route("/api/imports/template", import_template, methods=["GET"]),
    Route("/api/imports", import_list, methods=["GET"]),
    Route("/api/imports", import_upload, methods=["POST"]),
    Route("/api/imports/{id}", import_get, methods=["GET"]),
    Route("/api/imports/{id}/validate", import_validate, methods=["POST"]),
    Route("/api/imports/{id}/errors.csv", import_errors_csv, methods=["GET"]),
    Route("/api/imports/{id}/exclude", import_exclude, methods=["POST"]),
    Route("/api/imports/{id}/commit", import_commit, methods=["POST"]),

    Route("/api/plans", plan_list, methods=["GET"]),
    Route("/api/plans/run", plan_run, methods=["POST"]),
    Route("/api/plans/compare", plan_compare, methods=["GET"]),
    Route("/api/plans/{id}", plan_get, methods=["GET"]),
    Route("/api/plans/{id}/approve", plan_approve, methods=["POST"]),
    Route("/api/plans/{id}/dispatch", plan_dispatch, methods=["POST"]),

    Route("/api/routes", routes_list, methods=["GET"]),
    Route("/api/routes/{id}", route_get, methods=["GET"]),
    Route("/api/routes/{id}/candidates", route_candidates, methods=["GET"]),
    Route("/api/routes/{id}/assign", route_assign, methods=["POST"]),
    Route("/api/routes/{id}/unassign", route_unassign, methods=["POST"]),
    Route("/api/routes/{id}/modify", route_modify, methods=["POST"]),
    Route("/api/publish", publish_day, methods=["POST"]),
    Route("/api/unpublish", unpublish_day, methods=["POST"]),

    Route("/api/driver/routes", driver_routes, methods=["GET"]),
    Route("/api/driver/routes/{id}/start", driver_start_route, methods=["POST"]),
    Route("/api/driver/stops/{id}/arrive", driver_arrive, methods=["POST"]),
    Route("/api/driver/stops/{id}/pickup", driver_pickup, methods=["POST"]),
    Route("/api/driver/stops/{id}/deliver", driver_deliver, methods=["POST"]),
    Route("/api/driver/sync", driver_sync, methods=["POST"]),
    Route("/api/positions", positions_publish, methods=["POST"]),

    Route("/api/documents", document_upload, methods=["POST"]),
    Route("/api/documents/{id}", document_get, methods=["GET"]),
    Route("/api/shipments/{id}/documents", documents_for_shipment, methods=["GET"]),

    Route("/api/exceptions", record_exception, methods=["POST"]),
    Route("/api/exceptions", exceptions_list, methods=["GET"]),
    Route("/api/exceptions/{id}/resolve", resolve_exception, methods=["POST"]),

    Route("/api/ondemand", ondemand_create, methods=["POST"]),
    Route("/api/ondemand", ondemand_list, methods=["GET"]),
    Route("/api/ondemand/{id}/review", ondemand_review, methods=["POST"]),
    Route("/api/ondemand/{id}/options", ondemand_options, methods=["GET"]),
    Route("/api/ondemand/{id}/assign", ondemand_assign, methods=["POST"]),
    Route("/api/ondemand/{id}/cancel", ondemand_cancel, methods=["POST"]),

    Route("/api/alerts", alerts_list, methods=["GET"]),
    Route("/api/alerts/summary", alerts_summary, methods=["GET"]),
    Route("/api/alerts/scan", alerts_scan, methods=["POST"]),
    Route("/api/alerts/{id}/ack", alert_ack, methods=["POST"]),
    Route("/api/alerts/{id}/resolve", alert_resolve, methods=["POST"]),

    Route("/api/notifications", notifications_list, methods=["GET"]),
    Route("/api/notifications/status", notifications_status, methods=["GET"]),
    Route("/api/routing/status", routing_status_endpoint, methods=["GET"]),
    Route("/api/storage/status", storage_status, methods=["GET"]),
    Route("/api/notifications/flush", notifications_flush, methods=["POST"]),

    Route("/api/tracking/live", tracking_live, methods=["GET"]),
    Route("/api/tracking/routes/{id}", tracking_route, methods=["GET"]),

    Route("/api/temperature/status", temperature_status, methods=["GET"]),
    Route("/api/temperature/ingest", temperature_ingest, methods=["POST"]),
    Route("/api/temperature/poll", temperature_poll, methods=["POST"]),
    Route("/api/temperature/shipments/{id}", temperature_shipment, methods=["GET"]),
    Route("/api/temperature/breaches/{id}/resolve", temperature_resolve, methods=["POST"]),

    Route("/api/shipments", shipments_list, methods=["GET"]),
    Route("/api/shipments/{id}", shipment_get, methods=["GET"]),

    Route("/api/reports/kpi", report_kpi, methods=["GET"]),
    Route("/api/reports/grouped", report_grouped, methods=["GET"]),
    Route("/api/reports/routes", report_routes, methods=["GET"]),
    Route("/api/reports/exceptions", report_exceptions, methods=["GET"]),
    Route("/api/reports/temperature", report_temperature, methods=["GET"]),
    Route("/api/reports/plan-vs-execution", report_plan_vs_execution, methods=["GET"]),
    Route("/api/reports/hub-modifications", report_hub_modifications, methods=["GET"]),
    Route("/api/reports/driver-capacity", report_driver_capacity, methods=["GET"]),

    Route("/api/audit", audit_query, methods=["GET"]),
    Route("/api/events", event_stream, methods=["GET"]),
]

#: نقاط لا تتطلب مصادقة
PUBLIC_PATHS = frozenset({
    "/api/health", "/api/meta", "/api/auth/login", "/api/auth/refresh",
    "/api/auth/session",
    "/api/auth/password/reset", "/api/auth/password/reset/complete",
    "/api/temperature/status", "/api/notifications/status", "/api/routing/status",
})
