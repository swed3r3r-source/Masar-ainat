"""المصادقة وإدارة الجلسات والنطاقات."""

from __future__ import annotations

import contextlib
import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

import pgwire
from masar_core.config import get_config
from masar_core.constants import AuditAction, Role
from masar_core.errors import Forbidden, NotFound, Unauthorized, ValidationError
from masar_core.permissions import has_permission, permissions_for
from masar_core.security import (
    hash_token,
    issue_tokens,
    new_token,
    password_hasher,
    verify_password_constant_time,
)
from masar_db.driver import SecurityContext, get_pool, session, transaction

from . import audit


@dataclass(slots=True)
class AuthenticatedUser:
    user_id: str
    email: str
    full_name: str
    role: str
    custom_role_id: str | None
    permissions: frozenset[str]
    hub_ids: list[str]
    region_ids: list[str]
    facility_id: str | None
    driver_id: str | None
    must_change_password: bool
    session_id: str

    def to_context(self, **kwargs: Any) -> SecurityContext:
        return SecurityContext(
            user_id=self.user_id, role=self.role,
            hub_ids=self.hub_ids, region_ids=self.region_ids,
            facility_id=self.facility_id, driver_id=self.driver_id,
            **kwargs,
        )

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.user_id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "permissions": sorted(self.permissions),
            "hub_ids": self.hub_ids,
            "region_ids": self.region_ids,
            "facility_id": self.facility_id,
            "driver_id": self.driver_id,
            "must_change_password": self.must_change_password,
        }


ADMIN_CONTEXT = SecurityContext.system("AUTH")


def load_user_scopes(conn: pgwire.Connection, user_id: str) -> dict[str, Any]:
    rows = conn.fetch_all(
        "SELECT scope_type, scope_id::text AS scope_id FROM user_scopes WHERE user_id = $1::uuid",
        [user_id],
    )
    hub_ids = [row["scope_id"] for row in rows if row["scope_type"] == "HUB"]
    region_ids = [row["scope_id"] for row in rows if row["scope_type"] == "REGION"]
    facility_ids = [row["scope_id"] for row in rows if row["scope_type"] == "FACILITY"]

    # نطاق المنطقة يوسّع تلقائيًا إلى مراكز الانطلاق التابعة لها
    if region_ids:
        extra = conn.fetch_all(
            "SELECT id::text AS id FROM hubs WHERE region_id = ANY($1::uuid[])",
            [region_ids],
        )
        hub_ids = sorted(set(hub_ids) | {row["id"] for row in extra})

    driver_row = conn.fetch_one(
        "SELECT id::text AS id, hub_id::text AS hub_id FROM drivers WHERE user_id = $1::uuid",
        [user_id],
    )
    return {
        "hub_ids": hub_ids,
        "region_ids": region_ids,
        "facility_id": facility_ids[0] if facility_ids else None,
        "driver_id": driver_row["id"] if driver_row else None,
        "driver_hub_id": driver_row["hub_id"] if driver_row else None,
    }


def effective_permissions(conn: pgwire.Connection, role: str, custom_role_id: str | None) -> frozenset[str]:
    base = set(permissions_for(role))
    if custom_role_id:
        row = conn.fetch_one(
            "SELECT permissions, base_role FROM custom_roles "
            "WHERE id = $1::uuid AND is_active",
            [custom_role_id],
        )
        if row:
            base = set(permissions_for(row["base_role"])) | set(row["permissions"] or [])
    return frozenset(base)


def authenticate(
    email: str,
    password: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[AuthenticatedUser, Any]:
    """يتحقق من بيانات الدخول وينشئ جلسة. يرفع :class:`Unauthorized` عند الفشل."""
    cfg = get_config().security
    pool = get_pool()
    conn = pool.acquire()
    try:
        from masar_db.driver import apply_context, clear_context

        apply_context(conn, ADMIN_CONTEXT)
        row = conn.fetch_one(
            "SELECT id::text AS id, email, full_name, password_hash, role, "
            "custom_role_id::text AS custom_role_id, is_active, must_change_password, "
            "failed_attempts, locked_until "
            "FROM users WHERE lower(email) = lower($1)",
            [email],
        )

        failure_code: str | None = None
        if row is None:
            failure_code = "NO_USER"
        elif not row["is_active"]:
            failure_code = "DISABLED"
        elif row["locked_until"] and row["locked_until"] > dt.datetime.now(dt.timezone.utc):
            failure_code = "LOCKED"

        stored_hash = row["password_hash"] if row else None
        password_ok = verify_password_constant_time(password, stored_hash)
        if failure_code is None and not password_ok:
            failure_code = "BAD_PASSWORD"

        with conn.transaction():
            audit.record_login(
                conn, email, failure_code is None,
                ip_address=ip_address, user_agent=user_agent, failure_code=failure_code,
            )
            if failure_code is not None:
                if row is not None and failure_code == "BAD_PASSWORD":
                    attempts = int(row["failed_attempts"]) + 1
                    locked_until = None
                    if attempts >= cfg.max_login_attempts:
                        locked_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
                            minutes=cfg.lockout_minutes)
                    conn.execute(
                        "UPDATE users SET failed_attempts = $1, locked_until = $2 "
                        "WHERE id = $3::uuid",
                        [attempts if not locked_until else 0, locked_until, row["id"]],
                    )
                audit.record(
                    conn, SecurityContext.anonymous(), AuditAction.LOGIN_FAILURE,
                    entity_type="user", entity_label=email,
                    new_value={"failure_code": failure_code},
                    ip_address=ip_address, user_agent=user_agent,
                )

        if failure_code == "LOCKED":
            raise Unauthorized(
                f"الحساب موقوف مؤقتًا لتكرار محاولات الدخول الخاطئة. "
                f"أعد المحاولة بعد {cfg.lockout_minutes} دقيقة."
            )
        if failure_code is not None:
            # رسالة موحدة لا تكشف وجود الحساب من عدمه
            raise Unauthorized("البريد الإلكتروني أو كلمة المرور غير صحيحة")

        assert row is not None
        scopes = load_user_scopes(conn, row["id"])
        # ملاحظة أمنية: **لا يُضاف مركز السائق إلى نطاق المراكز**، وإلا رأى
        # كل رحلات المركز. السائق يرى رحلاته المسندة إليه فقط (سياسة routes_read).

        session_id = str(uuid.uuid4())
        refresh_token = new_token()
        expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            days=cfg.refresh_token_days)

        permissions = effective_permissions(conn, row["role"], row["custom_role_id"])
        user = AuthenticatedUser(
            user_id=row["id"], email=row["email"], full_name=row["full_name"],
            role=row["role"], custom_role_id=row["custom_role_id"],
            permissions=permissions,
            hub_ids=scopes["hub_ids"], region_ids=scopes["region_ids"],
            facility_id=scopes["facility_id"], driver_id=scopes["driver_id"],
            must_change_password=bool(row["must_change_password"]),
            session_id=session_id,
        )

        with conn.transaction():
            conn.execute(
                "INSERT INTO user_sessions (id, user_id, refresh_token_hash, expires_at, "
                "ip_address, user_agent) VALUES ($1::uuid,$2::uuid,$3,$4,$5::inet,$6)",
                [session_id, row["id"], hash_token(refresh_token), expires_at,
                 ip_address, user_agent],
            )
            conn.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = NULL, "
                "last_login_at = now() WHERE id = $1::uuid",
                [row["id"]],
            )
            # ترقية تجزئة كلمة المرور إن تغيّرت معاملات الأمان
            if stored_hash and password_hasher.needs_rehash(stored_hash):
                conn.execute(
                    "UPDATE users SET password_hash = $1 WHERE id = $2::uuid",
                    [password_hasher.hash(password), row["id"]],
                )
            audit.record(
                conn, user.to_context(), AuditAction.LOGIN_SUCCESS,
                entity_type="user", entity_id=row["id"], entity_label=row["email"],
                ip_address=ip_address, user_agent=user_agent, actor_name=row["full_name"],
            )

        tokens = issue_tokens(
            user_id=row["id"], role=row["role"], session_id=session_id,
            extra_claims={"name": row["full_name"]},
        )
        tokens.refresh_token = refresh_token
        return user, tokens
    finally:
        with contextlib.suppress(Exception):
            from masar_db.driver import clear_context

            clear_context(conn)
        pool.release(conn)


def load_user_by_id(user_id: str, session_id: str | None = None) -> AuthenticatedUser:
    """يعيد بناء المستخدم من قاعدة البيانات — تُستدعى مع كل طلب مصادَق."""
    with session(ADMIN_CONTEXT) as conn:
        row = conn.fetch_one(
            "SELECT id::text AS id, email, full_name, role, "
            "custom_role_id::text AS custom_role_id, is_active, must_change_password "
            "FROM users WHERE id = $1::uuid",
            [user_id],
        )
        if row is None or not row["is_active"]:
            raise Unauthorized("الحساب غير موجود أو معطّل")

        if session_id:
            active = conn.fetch_one(
                "SELECT id, expires_at, revoked_at, last_seen_at FROM user_sessions "
                "WHERE id = $1::uuid AND user_id = $2::uuid",
                [session_id, user_id],
            )
            if active is None or active["revoked_at"] is not None:
                raise Unauthorized("انتهت الجلسة أو أُلغيت")
            now = dt.datetime.now(dt.timezone.utc)
            if active["expires_at"] < now:
                raise Unauthorized("انتهت صلاحية الجلسة")
            idle_limit = get_config().security.idle_timeout_minutes
            if active["last_seen_at"] < now - dt.timedelta(minutes=idle_limit):
                conn.execute(
                    "UPDATE user_sessions SET revoked_at = now(), "
                    "revoke_reason = 'IDLE_TIMEOUT' WHERE id = $1::uuid",
                    [session_id],
                )
                raise Unauthorized("انتهت الجلسة بسبب الخمول")
            conn.execute(
                "UPDATE user_sessions SET last_seen_at = now() WHERE id = $1::uuid",
                [session_id],
            )

        scopes = load_user_scopes(conn, row["id"])
        permissions = effective_permissions(conn, row["role"], row["custom_role_id"])

    return AuthenticatedUser(
        user_id=row["id"], email=row["email"], full_name=row["full_name"],
        role=row["role"], custom_role_id=row["custom_role_id"],
        permissions=permissions,
        hub_ids=scopes["hub_ids"], region_ids=scopes["region_ids"],
        facility_id=scopes["facility_id"], driver_id=scopes["driver_id"],
        must_change_password=bool(row["must_change_password"]),
        session_id=session_id or "",
    )


def _revoke_family(conn: Any, user_id: str, reason: str) -> None:
    """يُبطل كل جلسات المستخدم الحية.

    الإبطال الجماعي مقصود لا مبالغة: عند رصد رمز مسرَّب لا نعرف أي الجلستين
    للمهاجم وأيها للمستخدم، فإبطال واحدة فقط قد يُبقي جلسة المهاجم. إخراج
    الطرفين وإجبار الدخول من جديد هو الخيار الوحيد الذي لا يترك المهاجم داخلًا.
    """
    conn.execute(
        "UPDATE user_sessions SET revoked_at = now(), revoke_reason = $2 "
        "WHERE user_id = $1::uuid AND revoked_at IS NULL",
        [user_id, reason],
    )


def refresh_session(refresh_token: str) -> tuple[AuthenticatedUser, Any]:
    """يدوّر رمز التحديث: الرمز القديم يُلغى فورًا (Refresh Token Rotation)."""
    token_hash = hash_token(refresh_token)

    # الإبطال يجب أن **يُثبَّت** قبل رفع الخطأ. رفع استثناء داخل كتلة المعاملة
    # يُرجِع كل ما كُتب فيها — وهو ما كان يجعل «إبطال العائلة» بلا أثر فعلي:
    # يُنفَّذ التحديث ثم يُلغى بالتراجع، فتبقى جلسة المهاجم حية.
    compromised_user: str | None = None
    revoke_reason = "REUSE_DETECTED"
    failure: str | None = None

    with transaction(ADMIN_CONTEXT) as conn:
        row = conn.fetch_one(
            "SELECT id::text AS id, user_id::text AS user_id, expires_at, revoked_at "
            "FROM user_sessions WHERE refresh_token_hash = $1",
            [token_hash],
        )

        if row is None:
            # الرمز ليس الحالي لأي جلسة. قبل الرفض العام نسأل: هل هو رمز
            # **سبق تدويره**؟ إن كان كذلك فهذه إعادة استخدام، ومعناها أن نسخة
            # منه بيد طرف آخر — لأن صاحب الجلسة الشرعي يحمل الرمز الجديد.
            superseded = conn.fetch_one(
                "SELECT id::text AS id, user_id::text AS user_id "
                "FROM user_sessions WHERE previous_token_hash = $1",
                [token_hash],
            )
            if superseded is not None:
                compromised_user = superseded["user_id"]
                failure = (
                    "أُلغيت كل الجلسات: رُصد استخدام رمز تحديث سبق تدويره. "
                    "سجّل الدخول من جديد."
                )
            else:
                failure = "رمز التحديث غير صالح"

        elif row["revoked_at"] is not None:
            # رمز جلسة مُلغاة (خروج أو إبطال إداري) — يُعامل معاملة التسريب
            compromised_user = row["user_id"]
            failure = "أُلغيت كل الجلسات: رُصد استخدام رمز تحديث ملغى"
        elif row["expires_at"] < dt.datetime.now(dt.timezone.utc):
            failure = "انتهت صلاحية رمز التحديث"
        else:
            new_refresh = new_token()
            conn.execute(
                "UPDATE user_sessions SET refresh_token_hash = $1, "
                "previous_token_hash = $2, rotation_count = rotation_count + 1, "
                "last_seen_at = now() WHERE id = $3::uuid",
                [hash_token(new_refresh), token_hash, row["id"]],
            )

    if compromised_user is not None:
        # معاملة مستقلة تُثبَّت فعلًا قبل رفع الخطأ
        with transaction(ADMIN_CONTEXT) as conn:
            _revoke_family(conn, compromised_user, revoke_reason)
    if failure is not None:
        raise Unauthorized(failure)

    user = load_user_by_id(row["user_id"], row["id"])
    tokens = issue_tokens(
        user_id=user.user_id, role=user.role, session_id=user.session_id,
        extra_claims={"name": user.full_name},
    )
    tokens.refresh_token = new_refresh
    return user, tokens


def logout(user: AuthenticatedUser, *, ip_address: str | None = None) -> None:
    with transaction(user.to_context()) as conn:
        conn.execute(
            "UPDATE user_sessions SET revoked_at = now(), revoke_reason = 'LOGOUT' "
            "WHERE id = $1::uuid",
            [user.session_id],
        )
        audit.record(
            conn, user.to_context(), AuditAction.LOGOUT,
            entity_type="user", entity_id=user.user_id, ip_address=ip_address,
        )


def change_password(user: AuthenticatedUser, current: str, new_password: str) -> None:
    validate_password_strength(new_password)
    with transaction(user.to_context()) as conn:
        row = conn.fetch_one(
            "SELECT password_hash FROM users WHERE id = $1::uuid", [user.user_id])
        if row is None or not verify_password_constant_time(current, row["password_hash"]):
            raise Unauthorized("كلمة المرور الحالية غير صحيحة")
        conn.execute(
            "UPDATE users SET password_hash = $1, must_change_password = false "
            "WHERE id = $2::uuid",
            [password_hasher.hash(new_password), user.user_id],
        )
        # إلغاء الجلسات الأخرى بعد تغيير كلمة المرور
        conn.execute(
            "UPDATE user_sessions SET revoked_at = now(), revoke_reason = 'PASSWORD_CHANGED' "
            "WHERE user_id = $1::uuid AND id <> $2::uuid AND revoked_at IS NULL",
            [user.user_id, user.session_id],
        )
        audit.record(
            conn, user.to_context(), AuditAction.USER_UPDATE,
            entity_type="user", entity_id=user.user_id,
            new_value={"password_changed": True},
        )


def validate_password_strength(password: str) -> None:
    problems: list[str] = []
    if len(password) < 12:
        problems.append("لا تقل عن ١٢ محرفًا")
    if not any(c.isdigit() for c in password):
        problems.append("تحتوي رقمًا واحدًا على الأقل")
    if not any(c.isalpha() for c in password):
        problems.append("تحتوي حرفًا واحدًا على الأقل")
    if not any(not c.isalnum() for c in password):
        problems.append("تحتوي رمزًا خاصًا واحدًا على الأقل")
    if password.lower() in ("password", "12345678", "قلقلقلقلقل"):
        problems.append("ليست كلمة مرور شائعة")
    if problems:
        raise ValidationError(
            "كلمة المرور يجب أن: " + "، و".join(problems), requirements=problems
        )


def request_password_reset(email: str) -> str | None:
    """ينشئ رمز استعادة. يعيد الرمز للمرسل البريدي فقط، ولا يكشف وجود الحساب."""
    with transaction(ADMIN_CONTEXT) as conn:
        row = conn.fetch_one(
            "SELECT id::text AS id FROM users WHERE lower(email) = lower($1) AND is_active",
            [email],
        )
        if row is None:
            return None
        token = new_token()
        conn.execute(
            "INSERT INTO password_resets (user_id, token_hash, expires_at) "
            "VALUES ($1::uuid, $2, now() + interval '1 hour')",
            [row["id"], hash_token(token)],
        )
        return token


def complete_password_reset(token: str, new_password: str) -> None:
    validate_password_strength(new_password)
    with transaction(ADMIN_CONTEXT) as conn:
        row = conn.fetch_one(
            "SELECT id::text AS id, user_id::text AS user_id, expires_at, used_at "
            "FROM password_resets WHERE token_hash = $1",
            [hash_token(token)],
        )
        if row is None or row["used_at"] is not None:
            raise Unauthorized("رمز الاستعادة غير صالح أو مستخدم")
        if row["expires_at"] < dt.datetime.now(dt.timezone.utc):
            raise Unauthorized("انتهت صلاحية رمز الاستعادة")
        conn.execute(
            "UPDATE users SET password_hash = $1, must_change_password = false, "
            "failed_attempts = 0, locked_until = NULL WHERE id = $2::uuid",
            [password_hasher.hash(new_password), row["user_id"]],
        )
        conn.execute(
            "UPDATE password_resets SET used_at = now() WHERE id = $1::uuid", [row["id"]])
        conn.execute(
            "UPDATE user_sessions SET revoked_at = now(), revoke_reason = 'PASSWORD_RESET' "
            "WHERE user_id = $1::uuid AND revoked_at IS NULL",
            [row["user_id"]],
        )
