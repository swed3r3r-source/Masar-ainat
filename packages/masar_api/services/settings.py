"""خدمة الإعدادات التشغيلية الهرمية (§13)."""

from __future__ import annotations

from typing import Any

import pgwire
from masar_core.constants import AuditAction
from masar_core.errors import NotFound, ValidationError
from masar_core.operational_settings import (
    DEFAULTS,
    SETTING_INDEX,
    SETTING_SPECS,
    SettingOverride,
    SettingsResolver,
    coerce,
)
from masar_db.driver import SecurityContext, session, transaction

from . import audit


def load_resolver(conn: pgwire.Connection) -> SettingsResolver:
    rows = conn.fetch_all(
        "SELECT setting_key, scope_type, scope_id::text AS scope_id, value "
        "FROM operational_settings"
    )
    overrides = [
        SettingOverride(
            key=row["setting_key"],
            value=row["value"],
            scope_type=row["scope_type"],
            scope_id=row["scope_id"],
        )
        for row in rows
        if row["setting_key"] in SETTING_INDEX
    ]
    return SettingsResolver(overrides)


def effective_for_hub(conn: pgwire.Connection, hub_id: str) -> dict[str, Any]:
    """يحل القيم الفعالة لمركز انطلاق محدد (المملكة ← المنطقة ← المدينة ← المركز)."""
    row = conn.fetch_one(
        "SELECT region_id::text AS region_id, city_id::text AS city_id FROM hubs "
        "WHERE id = $1::uuid",
        [hub_id],
    )
    if row is None:
        raise NotFound("مركز الانطلاق غير موجود")
    resolver = load_resolver(conn)
    return resolver.effective(
        region_id=row["region_id"], city_id=row["city_id"], hub_id=hub_id
    )


def effective_scope(
    context: SecurityContext,
    *,
    region_id: str | None = None,
    city_id: str | None = None,
    hub_id: str | None = None,
) -> dict[str, Any]:
    with session(context) as conn:
        if hub_id:
            return effective_for_hub(conn, hub_id)
        resolver = load_resolver(conn)
        return resolver.effective(region_id=region_id, city_id=city_id)


def explain_all(
    context: SecurityContext,
    *,
    region_id: str | None = None,
    city_id: str | None = None,
    hub_id: str | None = None,
) -> list[dict[str, Any]]:
    """يفسّر كل قيمة فعالة ومصدرها — لجعل قرارات المحرك قابلة للتفسير (§32)."""
    with session(context) as conn:
        resolver = load_resolver(conn)
        if hub_id:
            row = conn.fetch_one(
                "SELECT region_id::text AS region_id, city_id::text AS city_id "
                "FROM hubs WHERE id = $1::uuid",
                [hub_id],
            )
            if row:
                region_id = region_id or row["region_id"]
                city_id = city_id or row["city_id"]
    result = []
    for spec in SETTING_SPECS:
        explanation = resolver.explain(
            spec.key, region_id=region_id, city_id=city_id, hub_id=hub_id)
        explanation.update({
            "group_ar": spec.group_ar,
            "kind": spec.kind,
            "description_ar": spec.description_ar,
            "minimum": spec.minimum,
            "maximum": spec.maximum,
            "choices": list(spec.choices) if spec.choices else None,
        })
        result.append(explanation)
    return result


def set_override(
    context: SecurityContext,
    *,
    key: str,
    value: Any,
    scope_type: str,
    scope_id: str | None,
    reason: str,
    ip_address: str | None = None,
    actor_name: str | None = None,
) -> dict[str, Any]:
    if key not in SETTING_INDEX:
        raise ValidationError(f"مفتاح إعداد غير معروف: {key}")
    if scope_type not in ("KINGDOM", "REGION", "CITY", "HUB"):
        raise ValidationError("نطاق غير صالح")
    if scope_type == "KINGDOM":
        scope_id = None
    elif not scope_id:
        raise ValidationError(f"النطاق {scope_type} يتطلب تحديد الكيان")

    typed = coerce(key, value)

    with transaction(context) as conn:
        existing = conn.fetch_one(
            "SELECT id::text AS id, value FROM operational_settings "
            "WHERE setting_key = $1 AND scope_type = $2 "
            "AND coalesce(scope_id, '00000000-0000-0000-0000-000000000000'::uuid) "
            "    = coalesce($3::uuid, '00000000-0000-0000-0000-000000000000'::uuid)",
            [key, scope_type, scope_id],
        )
        old_value = existing["value"] if existing else DEFAULTS[key]

        if existing:
            conn.execute(
                "UPDATE operational_settings SET value = $1::jsonb, reason = $2, "
                "updated_by = $3::uuid WHERE id = $4::uuid",
                [pgwire.Jsonb(typed), reason, context.user_id, existing["id"]],
            )
            setting_id = existing["id"]
        else:
            setting_id = conn.fetch_value(
                "INSERT INTO operational_settings "
                "(setting_key, scope_type, scope_id, value, reason, updated_by) "
                "VALUES ($1,$2,$3::uuid,$4::jsonb,$5,$6::uuid) RETURNING id::text",
                [key, scope_type, scope_id, pgwire.Jsonb(typed), reason, context.user_id],
            )

        audit.record(
            conn, context, AuditAction.SETTING_CHANGE,
            entity_type="operational_setting", entity_id=setting_id,
            entity_label=f"{key}@{scope_type}:{scope_id or '-'}",
            old_value={"value": old_value}, new_value={"value": typed},
            reason=reason, ip_address=ip_address, actor_name=actor_name,
        )

    return {
        "id": setting_id, "key": key, "value": typed,
        "scope_type": scope_type, "scope_id": scope_id,
    }


def delete_override(
    context: SecurityContext,
    setting_id: str,
    reason: str,
    *,
    ip_address: str | None = None,
) -> None:
    with transaction(context) as conn:
        row = conn.fetch_one(
            "SELECT setting_key, scope_type, scope_id::text AS scope_id, value "
            "FROM operational_settings WHERE id = $1::uuid",
            [setting_id],
        )
        if row is None:
            raise NotFound("التجاوز غير موجود")
        conn.execute("DELETE FROM operational_settings WHERE id = $1::uuid", [setting_id])
        audit.record(
            conn, context, AuditAction.SETTING_CHANGE,
            entity_type="operational_setting", entity_id=setting_id,
            entity_label=f"{row['setting_key']}@{row['scope_type']}",
            old_value={"value": row["value"]},
            new_value={"value": DEFAULTS.get(row["setting_key"])},
            reason=reason, ip_address=ip_address,
        )


def list_overrides(context: SecurityContext) -> list[Any]:
    with session(context) as conn:
        return conn.fetch_all(
            "SELECT s.id::text AS id, s.setting_key, s.scope_type, "
            "s.scope_id::text AS scope_id, s.value, s.reason, s.updated_at, "
            "u.full_name AS updated_by_name "
            "FROM operational_settings s LEFT JOIN users u ON u.id = s.updated_by "
            "ORDER BY s.setting_key, s.scope_type"
        )


def seed_defaults(conn: pgwire.Connection) -> None:
    """يزرع تجاوزات النطاقات المعروفة (عرعر/الرياض/المحافظات) كبيانات لا ككود."""
    from masar_core.operational_settings import SEED_SCOPE_OVERRIDES

    city_rows = conn.fetch_all("SELECT id::text AS id, name_ar, is_governorate FROM cities")
    by_name = {row["name_ar"]: row for row in city_rows}

    for city_name, overrides in SEED_SCOPE_OVERRIDES.items():
        if city_name.startswith("_"):
            continue
        city = by_name.get(city_name)
        if city is None:
            continue
        for key, value in overrides.items():
            conn.execute(
                "INSERT INTO operational_settings "
                "(setting_key, scope_type, scope_id, value, reason) "
                "VALUES ($1,'CITY',$2::uuid,$3::jsonb,$4) "
                "ON CONFLICT DO NOTHING",
                [key, city["id"], pgwire.Jsonb(coerce(key, value)),
                 f"قيمة تشغيلية معتمدة لمدينة {city_name}"],
            )

    governorate_defaults = SEED_SCOPE_OVERRIDES.get("_governorate_default", {})
    for city in city_rows:
        if not city["is_governorate"] or city["name_ar"] in SEED_SCOPE_OVERRIDES:
            continue
        for key, value in governorate_defaults.items():
            conn.execute(
                "INSERT INTO operational_settings "
                "(setting_key, scope_type, scope_id, value, reason) "
                "VALUES ($1,'CITY',$2::uuid,$3::jsonb,$4) "
                "ON CONFLICT DO NOTHING",
                [key, city["id"], pgwire.Jsonb(coerce(key, value)),
                 "قيمة افتراضية للمحافظات"],
            )
