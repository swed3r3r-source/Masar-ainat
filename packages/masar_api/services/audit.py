"""سجل التدقيق (§27) — كل عملية حساسة تُسجَّل بقيمها القديمة والجديدة."""

from __future__ import annotations

from typing import Any

import pgwire
from masar_core.constants import ACTIONS_REQUIRING_REASON, AuditAction
from masar_core.errors import ReasonRequired
from masar_db.driver import SecurityContext


def _diff(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """يبني فرقًا مختصرًا بين حالتين — لا تُخزَّن الحقول غير المتغيرة."""
    if old is None or new is None:
        return {}
    changed: dict[str, Any] = {}
    for key in set(old) | set(new):
        before, after = old.get(key), new.get(key)
        if before != after:
            changed[key] = {"before": before, "after": after}
    return changed


def record(
    conn: pgwire.Connection,
    context: SecurityContext,
    action: str,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    entity_label: str | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    reason: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
    actor_name: str | None = None,
    is_test_data: bool = False,
) -> None:
    """يكتب سطرًا في سجل التدقيق. يُستدعى **داخل** معاملة العملية نفسها."""
    if action in ACTIONS_REQUIRING_REASON and not (reason or "").strip():
        raise ReasonRequired(
            f"العملية {action} تتطلب سببًا مكتوبًا قبل التنفيذ"
        )

    conn.execute(
        """
        INSERT INTO audit_log (
            actor_user_id, actor_role, actor_name, action, entity_type, entity_id,
            entity_label, old_value, new_value, reason, ip_address, user_agent,
            request_id, is_test_data
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11::inet,$12,$13,$14)
        """,
        [
            context.user_id, context.role, actor_name, action, entity_type, entity_id,
            entity_label,
            pgwire.Jsonb(old_value) if old_value is not None else None,
            pgwire.Jsonb(new_value) if new_value is not None else None,
            reason, ip_address, user_agent, request_id, is_test_data,
        ],
    )


def record_change(
    conn: pgwire.Connection,
    context: SecurityContext,
    action: str,
    *,
    entity_type: str,
    entity_id: str,
    before: dict[str, Any],
    after: dict[str, Any],
    reason: str | None = None,
    **kwargs: Any,
) -> None:
    """يسجّل تعديلًا مع الفرق فقط."""
    changes = _diff(before, after)
    if not changes:
        return
    record(
        conn, context, action,
        entity_type=entity_type, entity_id=entity_id,
        old_value={key: value["before"] for key, value in changes.items()},
        new_value={key: value["after"] for key, value in changes.items()},
        reason=reason, **kwargs,
    )


def record_login(
    conn: pgwire.Connection,
    email: str,
    succeeded: bool,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
    failure_code: str | None = None,
) -> None:
    """سجل محاولات الدخول (§29) — منفصل عن سجل التدقيق ليُحتفظ به أطول."""
    conn.execute(
        "INSERT INTO login_attempts (email, succeeded, ip_address, user_agent, failure_code) "
        "VALUES ($1,$2,$3::inet,$4,$5)",
        [email, succeeded, ip_address, user_agent, failure_code],
    )


def recent_failures(conn: pgwire.Connection, email: str, minutes: int = 15) -> int:
    return int(conn.fetch_value(
        "SELECT count(*) FROM login_attempts "
        "WHERE lower(email) = lower($1) AND NOT succeeded "
        f"AND attempted_at > now() - interval '{int(minutes)} minutes'",
        [email],
    ) or 0)


def query(
    conn: pgwire.Connection,
    *,
    actor_user_id: str | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    date_from: Any = None,
    date_to: Any = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Any], int]:
    clauses: list[str] = ["1=1"]
    params: list[Any] = []

    def add(clause: str, value: Any) -> None:
        params.append(value)
        clauses.append(clause.replace("?", f"${len(params)}"))

    if actor_user_id:
        add("actor_user_id = ?::uuid", actor_user_id)
    if action:
        add("action = ?", action)
    if entity_type:
        add("entity_type = ?", entity_type)
    if entity_id:
        add("entity_id = ?::uuid", entity_id)
    if date_from:
        add("occurred_at >= ?", date_from)
    if date_to:
        add("occurred_at <= ?", date_to)

    where = " AND ".join(clauses)
    total = int(conn.fetch_value(f"SELECT count(*) FROM audit_log WHERE {where}", params) or 0)
    rows = conn.fetch_all(
        f"SELECT id, occurred_at, actor_user_id, actor_role, actor_name, action, "
        f"entity_type, entity_id, entity_label, old_value, new_value, reason, "
        f"host(ip_address) AS ip_address, request_id "
        f"FROM audit_log WHERE {where} ORDER BY occurred_at DESC, id DESC "
        f"LIMIT {int(limit)} OFFSET {int(offset)}",
        params,
    )
    return rows, total
