"""البيانات الرئيسية (§6): المناطق، المدن، المراكز، الجهات، السائقون، المركبات، الصناديق.

CRUD موحّد مدفوع بالبيانات: كل كيان يُعرَّف مرة واحدة بحقوله وقواعد تحققه،
فتُشتق منه عمليات الإنشاء والتعديل والإبطال وسجل التدقيق. هذا يمنع اختلاف
سلوك التحقق بين شاشة وأخرى.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pgwire
from masar_core.constants import AuditAction, FacilityType
from masar_core.errors import Conflict, NotFound, ValidationError
from masar_core.timeutil import validate_coordinates
from masar_db.driver import SecurityContext, session, transaction

from . import audit


@dataclass(slots=True)
class FieldSpec:
    name: str
    label_ar: str
    kind: str = "text"        # text | int | float | bool | uuid | date | time | json | text[]
    required: bool = False
    updatable: bool = True
    choices: tuple[str, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None
    cast: str | None = None   # نوع SQL للتحويل الصريح


@dataclass(slots=True)
class EntitySpec:
    key: str
    table: str
    label_ar: str
    fields: list[FieldSpec]
    order_by: str = "created_at DESC"
    searchable: tuple[str, ...] = ()
    scope_column: str | None = None      # عمود نطاق مركز الانطلاق
    soft_delete: bool = False
    extra_validate: Callable[[dict[str, Any]], None] | None = None

    @property
    def field_index(self) -> dict[str, FieldSpec]:
        return {f.name: f for f in self.fields}


def _validate_facility(values: dict[str, Any]) -> None:
    validate_coordinates(values.get("lat"), values.get("lon"), label="إحداثيات الجهة")
    if values.get("facility_type") not in set(FacilityType):
        raise ValidationError(
            "نوع الجهة غير صالح", allowed=sorted(set(FacilityType))
        )


def _validate_hub(values: dict[str, Any]) -> None:
    validate_coordinates(values.get("lat"), values.get("lon"), label="إحداثيات مركز الانطلاق")


ENTITIES: dict[str, EntitySpec] = {
    "regions": EntitySpec(
        key="regions", table="regions", label_ar="المناطق",
        order_by="name_ar", searchable=("code", "name_ar"),
        fields=[
            FieldSpec("code", "الرمز", required=True, updatable=False),
            FieldSpec("name_ar", "الاسم", required=True),
            FieldSpec("name_en", "الاسم بالإنجليزية"),
            FieldSpec("timezone", "المنطقة الزمنية"),
            FieldSpec("is_active", "مفعّل", kind="bool"),
            FieldSpec("is_test_data", "بيانات اختبار", kind="bool", updatable=False),
        ],
    ),
    "cities": EntitySpec(
        key="cities", table="cities", label_ar="المدن والمحافظات",
        order_by="name_ar", searchable=("code", "name_ar"),
        fields=[
            FieldSpec("region_id", "المنطقة", kind="uuid", required=True, cast="uuid"),
            FieldSpec("code", "الرمز", required=True, updatable=False),
            FieldSpec("name_ar", "الاسم", required=True),
            FieldSpec("name_en", "الاسم بالإنجليزية"),
            FieldSpec("is_governorate", "محافظة", kind="bool"),
            FieldSpec("timezone", "المنطقة الزمنية"),
            FieldSpec("center_lat", "خط العرض", kind="float"),
            FieldSpec("center_lon", "خط الطول", kind="float"),
            FieldSpec("is_active", "مفعّل", kind="bool"),
            FieldSpec("is_test_data", "بيانات اختبار", kind="bool", updatable=False),
        ],
    ),
    "hubs": EntitySpec(
        key="hubs", table="hubs", label_ar="مراكز الانطلاق",
        order_by="name_ar", searchable=("code", "name_ar"),
        extra_validate=_validate_hub,
        fields=[
            FieldSpec("region_id", "المنطقة", kind="uuid", required=True, cast="uuid"),
            FieldSpec("city_id", "المدينة", kind="uuid", required=True, cast="uuid"),
            FieldSpec("code", "الرمز", required=True, updatable=False),
            FieldSpec("name_ar", "الاسم", required=True),
            FieldSpec("lat", "خط العرض", kind="float", required=True),
            FieldSpec("lon", "خط الطول", kind="float", required=True),
            FieldSpec("address", "العنوان"),
            FieldSpec("contact_name", "مسؤول التواصل"),
            FieldSpec("contact_phone", "رقم التواصل"),
            FieldSpec("working_hours", "أوقات العمل", kind="json", cast="jsonb"),
            FieldSpec("is_active", "مفعّل", kind="bool"),
            FieldSpec("is_test_data", "بيانات اختبار", kind="bool", updatable=False),
        ],
    ),
    "facilities": EntitySpec(
        key="facilities", table="facilities", label_ar="الجهات",
        order_by="name_ar", searchable=("code", "name_ar", "address"),
        scope_column="default_hub_id", soft_delete=True,
        extra_validate=_validate_facility,
        fields=[
            FieldSpec("region_id", "المنطقة", kind="uuid", required=True, cast="uuid"),
            FieldSpec("city_id", "المدينة", kind="uuid", required=True, cast="uuid"),
            FieldSpec("default_hub_id", "مركز الانطلاق", kind="uuid", cast="uuid"),
            FieldSpec("code", "الرمز", required=True, updatable=False),
            FieldSpec("name_ar", "الاسم", required=True),
            FieldSpec("name_en", "الاسم بالإنجليزية"),
            FieldSpec("facility_type", "النوع", required=True,
                      choices=tuple(sorted(set(FacilityType)))),
            FieldSpec("lat", "خط العرض", kind="float", required=True),
            FieldSpec("lon", "خط الطول", kind="float", required=True),
            FieldSpec("address", "العنوان"),
            FieldSpec("contact_name", "مسؤول التواصل"),
            FieldSpec("contact_phone", "رقم التواصل"),
            FieldSpec("contact_email", "البريد الإلكتروني"),
            FieldSpec("service_minutes", "مدة الخدمة (دقيقة)", kind="int",
                      minimum=1, maximum=480),
            FieldSpec("working_hours", "أوقات العمل", kind="json", cast="jsonb"),
            FieldSpec("notes", "ملاحظات"),
            FieldSpec("is_active", "مفعّل", kind="bool"),
            FieldSpec("is_test_data", "بيانات اختبار", kind="bool", updatable=False),
        ],
    ),
    "drivers": EntitySpec(
        key="drivers", table="drivers", label_ar="السائقون",
        order_by="full_name", searchable=("code", "full_name", "phone"),
        scope_column="hub_id",
        fields=[
            FieldSpec("hub_id", "مركز الانطلاق", kind="uuid", required=True, cast="uuid"),
            FieldSpec("user_id", "حساب المستخدم", kind="uuid", cast="uuid"),
            FieldSpec("code", "الرمز الوظيفي", required=True, updatable=False),
            FieldSpec("full_name", "الاسم", required=True),
            FieldSpec("phone", "الجوال"),
            FieldSpec("national_id", "الهوية"),
            FieldSpec("license_number", "رقم الرخصة"),
            FieldSpec("license_expiry", "انتهاء الرخصة", kind="date", cast="date"),
            FieldSpec("employment_status", "حالة التوظيف",
                      choices=("ACTIVE", "ON_LEAVE", "SUSPENDED", "TERMINATED")),
            FieldSpec("shift_start", "بداية الوردية", kind="time", cast="time"),
            FieldSpec("shift_end", "نهاية الوردية", kind="time", cast="time"),
            FieldSpec("qualifications", "المؤهلات", kind="text[]", cast="text[]"),
            FieldSpec("is_active", "مفعّل", kind="bool"),
            FieldSpec("is_test_data", "بيانات اختبار", kind="bool", updatable=False),
        ],
    ),
    "vehicles": EntitySpec(
        key="vehicles", table="vehicles", label_ar="المركبات",
        order_by="plate_number", searchable=("plate_number", "model"),
        scope_column="hub_id",
        fields=[
            FieldSpec("hub_id", "مركز الانطلاق", kind="uuid", required=True, cast="uuid"),
            FieldSpec("plate_number", "اللوحة", required=True, updatable=False),
            FieldSpec("model", "الطراز"),
            FieldSpec("make_year", "سنة الصنع", kind="int", minimum=1980, maximum=2100),
            FieldSpec("vehicle_type", "النوع",
                      choices=("CAR", "VAN", "TRUCK", "MOTORCYCLE")),
            FieldSpec("has_cooling", "مزوّدة بتبريد", kind="bool"),
            FieldSpec("status", "الحالة",
                      choices=("AVAILABLE", "IN_USE", "MAINTENANCE", "OUT_OF_SERVICE")),
            FieldSpec("is_active", "مفعّل", kind="bool"),
            FieldSpec("is_test_data", "بيانات اختبار", kind="bool", updatable=False),
        ],
    ),
    "boxes": EntitySpec(
        key="boxes", table="boxes", label_ar="الصناديق",
        order_by="code", searchable=("code", "name_ar"),
        scope_column="hub_id",
        fields=[
            FieldSpec("hub_id", "مركز الانطلاق", kind="uuid", required=True, cast="uuid"),
            FieldSpec("code", "الرمز", required=True, updatable=False),
            FieldSpec("name_ar", "الاسم"),
            FieldSpec("temperature_mode", "نطاق الحرارة",
                      choices=("AMBIENT", "CHILLED", "FROZEN", "DEEP_FROZEN", "CONTROLLED")),
            FieldSpec("capacity_units", "السعة", kind="int", minimum=1),
            FieldSpec("status", "الحالة",
                      choices=("AVAILABLE", "IN_USE", "MAINTENANCE", "DAMAGED", "RETIRED")),
            FieldSpec("is_active", "مفعّل", kind="bool"),
            FieldSpec("is_test_data", "بيانات اختبار", kind="bool", updatable=False),
        ],
    ),
    "temperature_ranges": EntitySpec(
        key="temperature_ranges", table="temperature_ranges", label_ar="نطاقات الحرارة",
        order_by="mode", searchable=("mode", "name_ar"),
        fields=[
            FieldSpec("mode", "النطاق", required=True, updatable=False),
            FieldSpec("name_ar", "الاسم", required=True),
            FieldSpec("min_celsius", "أدنى حرارة", kind="float", required=True),
            FieldSpec("max_celsius", "أعلى حرارة", kind="float", required=True),
            FieldSpec("is_active", "مفعّل", kind="bool"),
        ],
    ),
}


def _coerce(spec: FieldSpec, raw: Any) -> Any:
    if raw is None:
        return None
    try:
        if spec.kind == "int":
            value: Any = int(raw)
        elif spec.kind == "float":
            value = float(raw)
        elif spec.kind == "bool":
            value = raw if isinstance(raw, bool) else str(raw).lower() in (
                "1", "true", "yes", "on", "نعم")
        elif spec.kind == "json":
            value = pgwire.Jsonb(raw) if not isinstance(raw, pgwire.Json) else raw
        elif spec.kind == "text[]":
            value = list(raw) if isinstance(raw, (list, tuple)) else [
                item.strip() for item in str(raw).split(",") if item.strip()]
        elif spec.kind == "date":
            from masar_core.timeutil import parse_date
            value = parse_date(raw, field=spec.label_ar)
        elif spec.kind == "time":
            from masar_core.timeutil import parse_time
            value = parse_time(raw, field=spec.label_ar)
        elif spec.kind == "uuid":
            value = str(raw)
        else:
            value = str(raw).strip()
    except ValidationError:
        raise
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"قيمة غير صالحة لحقل «{spec.label_ar}»: {raw!r}") from exc

    if spec.choices and value not in spec.choices:
        raise ValidationError(
            f"قيمة «{spec.label_ar}» يجب أن تكون إحدى: {'، '.join(spec.choices)}"
        )
    if spec.kind in ("int", "float"):
        if spec.minimum is not None and value < spec.minimum:
            raise ValidationError(f"«{spec.label_ar}» أقل من الحد الأدنى {spec.minimum}")
        if spec.maximum is not None and value > spec.maximum:
            raise ValidationError(f"«{spec.label_ar}» أعلى من الحد الأقصى {spec.maximum}")
    return value


def _prepare(spec: EntitySpec, payload: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field_spec in spec.fields:
        if not creating and not field_spec.updatable:
            continue
        if field_spec.name not in payload:
            if creating and field_spec.required:
                raise ValidationError(f"الحقل «{field_spec.label_ar}» مطلوب")
            continue
        values[field_spec.name] = _coerce(field_spec, payload[field_spec.name])

    if creating:
        for field_spec in spec.fields:
            if field_spec.required and values.get(field_spec.name) in (None, ""):
                raise ValidationError(f"الحقل «{field_spec.label_ar}» مطلوب")
        if spec.extra_validate:
            spec.extra_validate(values)
    elif spec.extra_validate and {"lat", "lon"} & set(values):
        pass  # التحقق الكامل يتم بعد الدمج في `update`
    return values


def _select_columns(spec: EntitySpec) -> str:
    columns = ["id::text AS id"]
    for field_spec in spec.fields:
        if field_spec.kind == "uuid":
            columns.append(f"{field_spec.name}::text AS {field_spec.name}")
        else:
            columns.append(field_spec.name)
    columns.append("created_at")
    if spec.soft_delete:
        columns.append("voided_at")
    return ", ".join(columns)


def list_entities(
    context: SecurityContext,
    entity: str,
    *,
    search: str | None = None,
    filters: dict[str, Any] | None = None,
    include_inactive: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[Any], int]:
    spec = ENTITIES.get(entity)
    if spec is None:
        raise NotFound(f"كيان غير معروف: {entity}")

    clauses: list[str] = ["1=1"]
    params: list[Any] = []

    def add(clause: str, value: Any) -> None:
        params.append(value)
        clauses.append(clause.replace("?", f"${len(params)}"))

    if spec.soft_delete:
        clauses.append("voided_at IS NULL")
    if not include_inactive and any(f.name == "is_active" for f in spec.fields):
        clauses.append("is_active")
    if search and spec.searchable:
        pattern = f"%{search.strip()}%"
        params.append(pattern)
        placeholder = f"${len(params)}"
        ors = " OR ".join(f"{column} ILIKE {placeholder}" for column in spec.searchable)
        clauses.append(f"({ors})")
    for key, value in (filters or {}).items():
        field_spec = spec.field_index.get(key)
        if field_spec is None or value in (None, ""):
            continue
        cast = f"::{field_spec.cast}" if field_spec.cast else ""
        add(f"{key} = ?{cast}", _coerce(field_spec, value))

    where = " AND ".join(clauses)
    with session(context) as conn:
        total = int(conn.fetch_value(
            f"SELECT count(*) FROM {spec.table} WHERE {where}", params) or 0)
        rows = conn.fetch_all(
            f"SELECT {_select_columns(spec)} FROM {spec.table} WHERE {where} "
            f"ORDER BY {spec.order_by} LIMIT {int(limit)} OFFSET {int(offset)}",
            params,
        )
    return rows, total


def get_entity(context: SecurityContext, entity: str, entity_id: str) -> Any:
    spec = ENTITIES[entity]
    with session(context) as conn:
        row = conn.fetch_one(
            f"SELECT {_select_columns(spec)} FROM {spec.table} WHERE id = $1::uuid",
            [entity_id],
        )
    if row is None:
        raise NotFound(f"لم يُعثر على السجل في {spec.label_ar}")
    return row


def create_entity(
    context: SecurityContext,
    entity: str,
    payload: dict[str, Any],
    *,
    ip_address: str | None = None,
    actor_name: str | None = None,
) -> Any:
    spec = ENTITIES[entity]
    values = _prepare(spec, payload, creating=True)

    columns = list(values)
    casts = [
        f"${i + 1}::{spec.field_index[name].cast}" if spec.field_index[name].cast
        else f"${i + 1}"
        for i, name in enumerate(columns)
    ]
    sql = (
        f"INSERT INTO {spec.table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(casts)}) RETURNING {_select_columns(spec)}"
    )
    try:
        with transaction(context) as conn:
            row = conn.fetch_one(sql, [values[name] for name in columns])
            audit.record(
                conn, context, AuditAction.MASTER_DATA_CREATE,
                entity_type=entity, entity_id=row["id"],
                entity_label=str(row.get("name_ar") or row.get("code") or row["id"]),
                new_value={k: _jsonable(v) for k, v in values.items()},
                ip_address=ip_address, actor_name=actor_name,
                is_test_data=bool(values.get("is_test_data")),
            )
    except pgwire.UniqueViolation as exc:
        raise Conflict(
            f"سجل مكرر في {spec.label_ar} — القيد {exc.constraint}",
            constraint=exc.constraint,
        ) from exc
    except pgwire.ForeignKeyViolation as exc:
        raise ValidationError(
            f"مرجع غير موجود في {spec.label_ar} — القيد {exc.constraint}"
        ) from exc
    return row


def update_entity(
    context: SecurityContext,
    entity: str,
    entity_id: str,
    payload: dict[str, Any],
    *,
    reason: str | None = None,
    ip_address: str | None = None,
    actor_name: str | None = None,
) -> Any:
    spec = ENTITIES[entity]
    values = _prepare(spec, payload, creating=False)
    if not values:
        raise ValidationError("لا توجد حقول قابلة للتعديل في الطلب")

    with transaction(context) as conn:
        before = conn.fetch_one(
            f"SELECT {_select_columns(spec)} FROM {spec.table} WHERE id = $1::uuid",
            [entity_id],
        )
        if before is None:
            raise NotFound(f"لم يُعثر على السجل في {spec.label_ar}")

        if spec.extra_validate:
            merged = {**dict(before), **values}
            spec.extra_validate(merged)

        assignments = []
        params: list[Any] = []
        for name, value in values.items():
            params.append(value)
            cast = spec.field_index[name].cast
            assignments.append(f"{name} = ${len(params)}" + (f"::{cast}" if cast else ""))
        params.append(entity_id)
        try:
            after = conn.fetch_one(
                f"UPDATE {spec.table} SET {', '.join(assignments)} "
                f"WHERE id = ${len(params)}::uuid RETURNING {_select_columns(spec)}",
                params,
            )
        except pgwire.UniqueViolation as exc:
            raise Conflict(f"قيمة مكررة — القيد {exc.constraint}") from exc

        audit.record_change(
            conn, context, AuditAction.MASTER_DATA_UPDATE,
            entity_type=entity, entity_id=entity_id,
            before={k: _jsonable(v) for k, v in dict(before).items() if k in values},
            after={k: _jsonable(v) for k, v in values.items()},
            reason=reason, ip_address=ip_address, actor_name=actor_name,
        )
    return after


def void_entity(
    context: SecurityContext,
    entity: str,
    entity_id: str,
    reason: str,
    *,
    ip_address: str | None = None,
    actor_name: str | None = None,
) -> None:
    """إبطال ناعم (§28: لا حذف نهائي دون صلاحية خاصة)."""
    spec = ENTITIES[entity]
    with transaction(context) as conn:
        before = conn.fetch_one(
            f"SELECT {_select_columns(spec)} FROM {spec.table} WHERE id = $1::uuid",
            [entity_id],
        )
        if before is None:
            raise NotFound(f"لم يُعثر على السجل في {spec.label_ar}")

        if spec.soft_delete:
            conn.execute(
                f"UPDATE {spec.table} SET voided_at = now(), voided_by = $1::uuid, "
                f"void_reason = $2, is_active = false WHERE id = $3::uuid",
                [context.user_id, reason, entity_id],
            )
        else:
            conn.execute(
                f"UPDATE {spec.table} SET is_active = false WHERE id = $1::uuid",
                [entity_id],
            )
        audit.record(
            conn, context, AuditAction.MASTER_DATA_VOID,
            entity_type=entity, entity_id=entity_id,
            entity_label=str(before.get("name_ar") or before.get("code") or entity_id),
            old_value={"is_active": True}, new_value={"is_active": False},
            reason=reason, ip_address=ip_address, actor_name=actor_name,
        )


def _jsonable(value: Any) -> Any:
    import datetime as dt
    from decimal import Decimal

    if isinstance(value, (dt.date, dt.time, dt.datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, pgwire.Json):
        return value.value
    return value


def describe(entity: str) -> dict[str, Any]:
    """يصف الكيان للواجهة — تُبنى النماذج آليًا فلا تختلف عن قواعد الخادم."""
    spec = ENTITIES[entity]
    return {
        "key": spec.key,
        "label_ar": spec.label_ar,
        "soft_delete": spec.soft_delete,
        "fields": [
            {
                "name": f.name, "label_ar": f.label_ar, "kind": f.kind,
                "required": f.required, "updatable": f.updatable,
                "choices": list(f.choices) if f.choices else None,
                "minimum": f.minimum, "maximum": f.maximum,
            }
            for f in spec.fields
        ],
    }
