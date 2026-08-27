"""ينشئ حسابات تجربة جديدة للأدوار الخمسة بكلمات مرور مولَّدة عشوائيًا.

**لا تُكتب كلمات المرور في أي ملف ولا في المستودع** — تُطبع على المخرج
القياسي مرة واحدة فقط، ويُخزَّن في القاعدة تجزئتها (scrypt) لا نصّها.
كل الحسابات موسومة ``is_test_data = true`` فلا تختلط ببيانات إنتاج، ومحفّز
``guard_no_test_data_in_production`` يرفضها في بيئة الإنتاج.
"""
from __future__ import annotations

import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from masar_core.security import password_hasher
from tests.support import db_connection

ALPHABET = string.ascii_letters + string.digits
SYMBOLS = "!@#$%^&*-_=+"


def new_password() -> str:
    """١٦ محرفًا من مولّد آمن، مع ضمان تنوّع الأصناف."""
    while True:
        raw = "".join(secrets.choice(ALPHABET + SYMBOLS) for _ in range(16))
        if (any(c.islower() for c in raw) and any(c.isupper() for c in raw)
                and any(c.isdigit() for c in raw) and any(c in SYMBOLS for c in raw)):
            return raw


ACCOUNTS = [
    ("trial.admin@masar.test",      "مدير التجربة",            "ADMIN",              None),
    ("trial.planner@masar.test",    "مخطط مركزي — تجربة",      "CENTRAL_PLANNER",    None),
    ("trial.super.ryd@masar.test",  "مشرف مركز الرياض — تجربة", "HUB_SUPERVISOR",    "H-RYD-1"),
    ("trial.driver@masar.test",     "سائق التجربة",            "DRIVER",             "H-RYD-1"),
    ("trial.requester@masar.test",  "جهة طالبة — تجربة",       "EXTERNAL_REQUESTER", None),
]

DRIVER_CODE = "DRV-TRIAL-01"
REQUESTER_FACILITY = "PHC-RYD-01"


def main() -> int:
    conn = db_connection()
    issued: list[tuple[str, str, str]] = []
    try:
        with conn.transaction():
            for email, full_name, role, hub_code in ACCOUNTS:
                password = new_password()
                user_id = conn.fetch_value(
                    "INSERT INTO users (email, full_name, password_hash, role, "
                    "must_change_password, is_test_data) "
                    "VALUES ($1,$2,$3,$4,false,true) "
                    "ON CONFLICT (lower(email)) DO UPDATE SET "
                    "full_name = EXCLUDED.full_name, "
                    "password_hash = EXCLUDED.password_hash, "
                    "role = EXCLUDED.role, is_active = true, "
                    "failed_attempts = 0, locked_until = NULL "
                    "RETURNING id::text",
                    [email, full_name, password_hasher.hash(password), role],
                )
                conn.execute("DELETE FROM user_scopes WHERE user_id = $1::uuid", [user_id])

                if role == "HUB_SUPERVISOR" and hub_code:
                    hub_id = conn.fetch_value(
                        "SELECT id::text FROM hubs WHERE code = $1", [hub_code])
                    conn.execute(
                        "INSERT INTO user_scopes (user_id, scope_type, scope_id) "
                        "VALUES ($1::uuid,'HUB',$2::uuid)", [user_id, hub_id])

                if role == "EXTERNAL_REQUESTER":
                    facility_id = conn.fetch_value(
                        "SELECT id::text FROM facilities WHERE code = $1",
                        [REQUESTER_FACILITY])
                    if facility_id is None:
                        raise SystemExit(f"جهة الطلب غير موجودة: {REQUESTER_FACILITY}")
                    conn.execute(
                        "INSERT INTO user_scopes (user_id, scope_type, scope_id) "
                        "VALUES ($1::uuid,'FACILITY',$2::uuid)", [user_id, facility_id])

                if role == "DRIVER":
                    hub_id = conn.fetch_value(
                        "SELECT id::text FROM hubs WHERE code = $1", [hub_code])
                    conn.execute(
                        "INSERT INTO drivers (hub_id, user_id, code, full_name, phone, "
                        "license_number, license_expiry, shift_start, shift_end, "
                        "is_test_data) VALUES "
                        "($1::uuid,$2::uuid,$3,$4,'0555000199',$5,"
                        "(CURRENT_DATE + 540)::date,'06:00','20:00',true) "
                        "ON CONFLICT (code) DO UPDATE SET user_id = EXCLUDED.user_id, "
                        "is_active = true, license_expiry = EXCLUDED.license_expiry",
                        [hub_id, user_id, DRIVER_CODE, full_name, f"LIC-{DRIVER_CODE}"])

                issued.append((email, password, role))
    finally:
        conn.close()

    print("=" * 74)
    print("حسابات التجربة — تُعرض مرة واحدة، ولا تُحفظ في أي ملف")
    print("=" * 74)
    for email, password, role in issued:
        print(f"{role:20s} {email:30s} {password}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
