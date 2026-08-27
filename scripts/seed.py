"""بيانات تجريبية موسومة (§31) — لا تُخلط ببيانات الإنتاج.

كل صف يُنشأ هنا يحمل ``is_test_data = true``، ومحفّز قاعدة البيانات
``guard_no_test_data_in_production`` يرفض إدخالها إذا كانت البيئة إنتاجًا.

التشغيل::

    python3 scripts/seed.py            # يزرع فوق المخطط الحالي
    python3 scripts/seed.py --reset    # يعيد بناء المخطط ثم يزرع
"""

from __future__ import annotations

import datetime as dt
import os
import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

import pgwire  # noqa: E402
from masar_core.config import get_config  # noqa: E402
from masar_core.security import password_hasher  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=3))


def _demo_password() -> str:
    """كلمة مرور حسابات بيانات الاختبار — **لا قيمة مثبّتة في المستودع**.

    تُؤخذ من ``MASAR_SEED_PASSWORD``؛ وإن لم تُضبط تُولَّد عشوائيًا وتُطبع
    مرة واحدة على المخرج القياسي. كلمة مرور مكتوبة في الكود تنتقل مع كل نسخة
    من المستودع وتبقى صالحة إلى الأبد — وهذا كيف تتسرب البيئات.
    """
    supplied = os.environ.get("MASAR_SEED_PASSWORD")
    if supplied:
        return supplied
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(16))
        if (any(c.islower() for c in candidate) and any(c.isupper() for c in candidate)
                and any(c.isdigit() for c in candidate)
                and any(c in "!@#$%^&*-_=+" for c in candidate)):
            return candidate


DEMO_PASSWORD = _demo_password()

# ---------------------------------------------------------------- بيانات ---

REGIONS = [
    ("R-RYD", "منطقة الرياض"),
    ("R-NBR", "منطقة الحدود الشمالية"),
    ("R-EST", "المنطقة الشرقية"),
]

CITIES = [
    ("C-RYD", "الرياض", "R-RYD", False, 24.7136, 46.6753),
    ("C-KRJ", "الخرج", "R-RYD", True, 24.1554, 47.3346),
    ("C-ARR", "عرعر", "R-NBR", False, 30.9753, 41.0381),
    ("C-RFH", "رفحاء", "R-NBR", True, 29.6202, 43.4980),
    ("C-HDT", "الحديثة", "R-NBR", True, 31.4667, 40.0000),
    ("C-DMM", "الدمام", "R-EST", False, 26.4207, 50.0888),
]

HUBS = [
    ("H-RYD-1", "مركز انطلاق الرياض الرئيسي", "C-RYD", 24.7250, 46.6900),
    ("H-KRJ-1", "مركز انطلاق الخرج", "C-KRJ", 24.1600, 47.3300),
    ("H-ARR-1", "مركز انطلاق عرعر", "C-ARR", 30.9800, 41.0400),
    ("H-DMM-1", "مركز انطلاق الدمام", "C-DMM", 26.4300, 50.1000),
]

# (رمز، اسم، نوع، مدينة، خط عرض، خط طول، مدة الخدمة، مركز الانطلاق)
FACILITIES = [
    # الرياض — مراكز صحية
    ("PHC-RYD-01", "مركز صحي النسيم", "HEALTH_CENTER", "C-RYD", 24.7743, 46.8172, 10, "H-RYD-1"),
    ("PHC-RYD-02", "مركز صحي العزيزية", "HEALTH_CENTER", "C-RYD", 24.5701, 46.7386, 10, "H-RYD-1"),
    ("PHC-RYD-03", "مركز صحي الشفا", "HEALTH_CENTER", "C-RYD", 24.5620, 46.6800, 10, "H-RYD-1"),
    ("PHC-RYD-04", "مركز صحي الملز", "HEALTH_CENTER", "C-RYD", 24.6690, 46.7300, 12, "H-RYD-1"),
    ("PHC-RYD-05", "مركز صحي السويدي", "HEALTH_CENTER", "C-RYD", 24.6060, 46.6320, 10, "H-RYD-1"),
    ("PHC-RYD-06", "مركز صحي الروضة", "HEALTH_CENTER", "C-RYD", 24.7460, 46.7910, 10, "H-RYD-1"),
    ("PHC-RYD-07", "مركز صحي العريجاء", "HEALTH_CENTER", "C-RYD", 24.6180, 46.6010, 10, "H-RYD-1"),
    ("PHC-RYD-08", "مركز صحي الياسمين", "HEALTH_CENTER", "C-RYD", 24.8440, 46.6360, 10, "H-RYD-1"),
    # الرياض — مستشفيات
    ("HOS-RYD-01", "مستشفى الملك سعود", "HOSPITAL", "C-RYD", 24.6520, 46.7130, 20, "H-RYD-1"),
    ("HOS-RYD-02", "مستشفى الإيمان العام", "HOSPITAL", "C-RYD", 24.7900, 46.7550, 20, "H-RYD-1"),
    ("HOS-RYD-03", "مستشفى اليمامة", "HOSPITAL", "C-RYD", 24.6420, 46.7300, 18, "H-RYD-1"),
    # الرياض — مختبرات وبنك دم
    ("LAB-RYD-01", "المختبر الإقليمي بالرياض", "LABORATORY", "C-RYD", 24.6877, 46.7219, 15, "H-RYD-1"),
    ("LAB-RYD-02", "مختبر الصحة العامة", "LABORATORY", "C-RYD", 24.7010, 46.6600, 15, "H-RYD-1"),
    ("BLD-RYD-01", "بنك الدم المركزي بالرياض", "BLOOD_BANK", "C-RYD", 24.6800, 46.7100, 15, "H-RYD-1"),
    # الخرج
    ("PHC-KRJ-01", "مركز صحي الخرج الأول", "HEALTH_CENTER", "C-KRJ", 24.1480, 47.3200, 12, "H-KRJ-1"),
    ("PHC-KRJ-02", "مركز صحي اليمامة بالخرج", "HEALTH_CENTER", "C-KRJ", 24.1720, 47.3520, 12, "H-KRJ-1"),
    ("HOS-KRJ-01", "مستشفى الخرج العام", "HOSPITAL", "C-KRJ", 24.1560, 47.3400, 20, "H-KRJ-1"),
    ("LAB-KRJ-01", "مختبر الخرج المرجعي", "LABORATORY", "C-KRJ", 24.1590, 47.3310, 15, "H-KRJ-1"),
    # عرعر
    ("PHC-ARR-01", "مركز صحي عرعر الشمالي", "HEALTH_CENTER", "C-ARR", 30.9900, 41.0500, 12, "H-ARR-1"),
    ("PHC-ARR-02", "مركز صحي المساعدية", "HEALTH_CENTER", "C-ARR", 30.9600, 41.0100, 12, "H-ARR-1"),
    ("PHC-ARR-03", "مركز صحي البادية", "HEALTH_CENTER", "C-ARR", 30.9100, 41.1200, 12, "H-ARR-1"),
    ("HOS-ARR-01", "مستشفى عرعر المركزي", "HOSPITAL", "C-ARR", 30.9750, 41.0350, 20, "H-ARR-1"),
    ("LAB-ARR-01", "مختبر عرعر المرجعي", "LABORATORY", "C-ARR", 30.9790, 41.0420, 15, "H-ARR-1"),
    ("BLD-ARR-01", "بنك الدم بعرعر", "BLOOD_BANK", "C-ARR", 30.9770, 41.0390, 15, "H-ARR-1"),
    # الحديثة — بعيدة عن عرعر (~١٥٠ كم طريقًا): رحلة بعيدة ممكنة (HC-15/HC-16)
    ("PHC-HDT-01", "مركز صحي الحديثة", "HEALTH_CENTER", "C-HDT", 31.4667, 40.0000, 12, "H-ARR-1"),
    # رفحاء — أبعد من مدى الوردية: حالة غير قابلة للتخطيط مقصودة (HC-19)
    ("PHC-RFH-01", "مركز صحي رفحاء", "HEALTH_CENTER", "C-RFH", 29.6250, 43.5050, 12, "H-ARR-1"),
    ("HOS-RFH-01", "مستشفى رفحاء العام", "HOSPITAL", "C-RFH", 29.6180, 43.4900, 20, "H-ARR-1"),
    # الدمام
    ("PHC-DMM-01", "مركز صحي الفيصلية", "HEALTH_CENTER", "C-DMM", 26.4100, 50.0700, 10, "H-DMM-1"),
    ("PHC-DMM-02", "مركز صحي الجلوية", "HEALTH_CENTER", "C-DMM", 26.4350, 50.1050, 10, "H-DMM-1"),
    ("HOS-DMM-01", "مستشفى الدمام المركزي", "HOSPITAL", "C-DMM", 26.4250, 50.0950, 20, "H-DMM-1"),
    ("LAB-DMM-01", "مختبر الدمام المرجعي", "LABORATORY", "C-DMM", 26.4220, 50.0900, 15, "H-DMM-1"),
]

DRIVERS = [
    ("DRV-RYD-01", "فهد بن عبدالله القحطاني", "H-RYD-1", "0501110001"),
    ("DRV-RYD-02", "سعود بن ناصر العتيبي", "H-RYD-1", "0501110002"),
    ("DRV-RYD-03", "ماجد بن سالم الدوسري", "H-RYD-1", "0501110003"),
    ("DRV-RYD-04", "تركي بن محمد الشمري", "H-RYD-1", "0501110004"),
    ("DRV-RYD-05", "بندر بن خالد الحربي", "H-RYD-1", "0501110005"),
    ("DRV-KRJ-01", "عبدالرحمن بن علي الزهراني", "H-KRJ-1", "0502220001"),
    ("DRV-KRJ-02", "ياسر بن سعد المطيري", "H-KRJ-1", "0502220002"),
    ("DRV-ARR-01", "نايف بن مطلق العنزي", "H-ARR-1", "0503330001"),
    ("DRV-ARR-02", "مشعل بن فهد الرشيدي", "H-ARR-1", "0503330002"),
    ("DRV-ARR-03", "سلطان بن عايد الشراري", "H-ARR-1", "0503330003"),
    ("DRV-DMM-01", "أحمد بن حسن الغامدي", "H-DMM-1", "0504440001"),
    ("DRV-DMM-02", "وليد بن يوسف البقمي", "H-DMM-1", "0504440002"),
]

USERS = [
    ("admin@masar.test", "مدير النظام", "ADMIN", []),
    ("planner@masar.test", "منسق التخطيط المركزي", "CENTRAL_PLANNER", []),
    ("tower@masar.test", "مناوب برج التحكم", "CONTROL_TOWER", []),
    ("auditor@masar.test", "المدقق الداخلي", "AUDITOR", []),
    ("sup.ryd@masar.test", "مشرف مركز الرياض", "HUB_SUPERVISOR", [("HUB", "H-RYD-1")]),
    ("sup.krj@masar.test", "مشرف مركز الخرج", "HUB_SUPERVISOR", [("HUB", "H-KRJ-1")]),
    ("sup.arr@masar.test", "مشرف مركز عرعر", "HUB_SUPERVISOR", [("HUB", "H-ARR-1")]),
    ("sup.dmm@masar.test", "مشرف مركز الدمام", "HUB_SUPERVISOR", [("HUB", "H-DMM-1")]),
    ("req.phc01@masar.test", "منسقة مركز صحي النسيم", "EXTERNAL_REQUESTER",
     [("FACILITY", "PHC-RYD-01")]),
    ("req.hos01@masar.test", "منسق مستشفى الملك سعود", "EXTERNAL_REQUESTER",
     [("FACILITY", "HOS-RYD-01")]),
]

TEMPERATURE_RANGES = [
    ("AMBIENT", "حرارة الغرفة", 15.0, 30.0),
    ("CHILLED", "مبرّد ٢–٨", 2.0, 8.0),
    ("FROZEN", "مجمّد", -25.0, -15.0),
    ("DEEP_FROZEN", "تجميد عميق", -80.0, -60.0),
    ("CONTROLLED", "نطاق مخصص", 2.0, 25.0),
]


def connect() -> pgwire.Connection:
    cfg = get_config().database
    return pgwire.connect(
        host=cfg.host, port=cfg.port, user=cfg.migrate_user,
        password=cfg.migrate_password, database=cfg.name, sslmode=cfg.sslmode,
        statement_timeout_ms=0, application_name="masar-seed",
    )


def seed(conn: pgwire.Connection, *, quiet: bool = False) -> dict[str, str]:
    def say(text: str) -> None:
        if not quiet:
            print(text)

    ids: dict[str, str] = {}

    with conn.transaction():
        for code, name in REGIONS:
            ids[code] = conn.fetch_value(
                "INSERT INTO regions (code, name_ar, is_test_data) VALUES ($1,$2,true) "
                "ON CONFLICT (code) DO UPDATE SET name_ar = EXCLUDED.name_ar "
                "RETURNING id::text",
                [code, name],
            )
        say(f"  ✓ {len(REGIONS)} مناطق")

        for code, name, region_code, is_gov, lat, lon in CITIES:
            ids[code] = conn.fetch_value(
                "INSERT INTO cities (region_id, code, name_ar, is_governorate, "
                "center_lat, center_lon, is_test_data) "
                "VALUES ($1::uuid,$2,$3,$4,$5,$6,true) "
                "ON CONFLICT (code) DO UPDATE SET name_ar = EXCLUDED.name_ar "
                "RETURNING id::text",
                [ids[region_code], code, name, is_gov, lat, lon],
            )
        say(f"  ✓ {len(CITIES)} مدن ومحافظات")

        city_region = {city[0]: city[2] for city in CITIES}
        for code, name, city_code, lat, lon in HUBS:
            ids[code] = conn.fetch_value(
                "INSERT INTO hubs (region_id, city_id, code, name_ar, lat, lon, "
                "working_hours, is_test_data) "
                "VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6,$7::jsonb,true) "
                "ON CONFLICT (code) DO UPDATE SET name_ar = EXCLUDED.name_ar "
                "RETURNING id::text",
                [ids[city_region[city_code]], ids[city_code], code, name, lat, lon,
                 pgwire.Jsonb({
                     "sun": ["06:00", "20:00"], "mon": ["06:00", "20:00"],
                     "tue": ["06:00", "20:00"], "wed": ["06:00", "20:00"],
                     "thu": ["06:00", "20:00"], "sat": ["07:00", "16:00"],
                 })],
            )
        say(f"  ✓ {len(HUBS)} مراكز انطلاق")

        for code, name, ftype, city_code, lat, lon, minutes, hub_code in FACILITIES:
            ids[code] = conn.fetch_value(
                "INSERT INTO facilities (region_id, city_id, default_hub_id, code, "
                "name_ar, facility_type, lat, lon, service_minutes, contact_name, "
                "contact_phone, address, is_test_data) "
                "VALUES ($1::uuid,$2::uuid,$3::uuid,$4,$5,$6,$7,$8,$9,$10,$11,$12,true) "
                "ON CONFLICT (code) DO UPDATE SET name_ar = EXCLUDED.name_ar, "
                "lat = EXCLUDED.lat, lon = EXCLUDED.lon RETURNING id::text",
                [ids[city_region[city_code]], ids[city_code], ids[hub_code], code, name,
                 ftype, lat, lon, minutes, f"منسق {name}", "0555000000",
                 f"{name}، {dict((c[0], c[1]) for c in CITIES)[city_code]}"],
            )
        say(f"  ✓ {len(FACILITIES)} جهة صحية")

        for code, name, hub_code, phone in DRIVERS:
            ids[code] = conn.fetch_value(
                "INSERT INTO drivers (hub_id, code, full_name, phone, license_number, "
                "license_expiry, shift_start, shift_end, is_test_data) "
                "VALUES ($1::uuid,$2,$3,$4,$5,$6::date,$7::time,$8::time,true) "
                "ON CONFLICT (code) DO UPDATE SET full_name = EXCLUDED.full_name "
                "RETURNING id::text",
                [ids[hub_code], code, name, phone, f"LIC-{code}",
                 (dt.date.today() + dt.timedelta(days=540)).isoformat(), "06:00", "18:00"],
            )
        say(f"  ✓ {len(DRIVERS)} سائقًا")

        vehicle_count = box_count = 0
        for index, (code, _name, hub_code, _phone) in enumerate(DRIVERS, start=1):
            plate = f"م ع ن {1000 + index}"
            conn.execute(
                "INSERT INTO vehicles (hub_id, plate_number, model, make_year, "
                "vehicle_type, has_cooling, is_test_data) "
                "VALUES ($1::uuid,$2,$3,$4,'VAN',true,true) "
                "ON CONFLICT (plate_number) DO NOTHING",
                [ids[hub_code], plate, "تويوتا هايس", 2024],
            )
            vehicle_count += 1
            for suffix in ("A", "B"):
                conn.execute(
                    "INSERT INTO boxes (hub_id, code, name_ar, temperature_mode, "
                    "capacity_units, is_test_data) "
                    "VALUES ($1::uuid,$2,$3,$4,$5,true) ON CONFLICT (code) DO NOTHING",
                    [ids[hub_code], f"BOX-{code[-6:]}-{suffix}",
                     f"صندوق {code[-6:]}-{suffix}",
                     "CHILLED" if suffix == "A" else "AMBIENT", 40],
                )
                box_count += 1
        say(f"  ✓ {vehicle_count} مركبة و{box_count} صندوقًا")

        for mode, name, minimum, maximum in TEMPERATURE_RANGES:
            conn.execute(
                "INSERT INTO temperature_ranges (mode, name_ar, min_celsius, max_celsius) "
                "VALUES ($1,$2,$3,$4) ON CONFLICT (mode) DO UPDATE SET "
                "min_celsius = EXCLUDED.min_celsius, max_celsius = EXCLUDED.max_celsius",
                [mode, name, minimum, maximum],
            )
        say(f"  ✓ {len(TEMPERATURE_RANGES)} نطاقات حرارة")

        password_hash = password_hasher.hash(DEMO_PASSWORD)
        for email, full_name, role, scopes in USERS:
            user_id = conn.fetch_value(
                "INSERT INTO users (email, full_name, password_hash, role, "
                "must_change_password, is_test_data) VALUES ($1,$2,$3,$4,false,true) "
                "ON CONFLICT (lower(email)) DO UPDATE SET full_name = EXCLUDED.full_name, "
                "password_hash = EXCLUDED.password_hash, role = EXCLUDED.role, "
                "is_active = true RETURNING id::text",
                [email, full_name, password_hash, role],
            )
            ids[email] = user_id
            conn.execute("DELETE FROM user_scopes WHERE user_id = $1::uuid", [user_id])
            for scope_type, scope_code in scopes:
                conn.execute(
                    "INSERT INTO user_scopes (user_id, scope_type, scope_id) "
                    "VALUES ($1::uuid,$2,$3::uuid)",
                    [user_id, scope_type, ids[scope_code]],
                )

        # حسابات السائقين
        for code, name, hub_code, phone in DRIVERS:
            email = f"{code.lower()}@masar.test"
            user_id = conn.fetch_value(
                "INSERT INTO users (email, full_name, phone, password_hash, role, "
                "must_change_password, is_test_data) "
                "VALUES ($1,$2,$3,$4,'DRIVER',false,true) "
                "ON CONFLICT (lower(email)) DO UPDATE SET full_name = EXCLUDED.full_name, "
                "password_hash = EXCLUDED.password_hash RETURNING id::text",
                [email, name, phone, password_hash],
            )
            conn.execute(
                "UPDATE drivers SET user_id = $1::uuid WHERE code = $2", [user_id, code])
            ids[email] = user_id
        say(f"  ✓ {len(USERS) + len(DRIVERS)} حساب مستخدم")

        # الحساسات مربوطة بالصناديق (تظل بلا مزوّد حتى يُفعَّل التكامل)
        sensors = 0
        for row in conn.fetch_all("SELECT id::text AS id, code FROM boxes"):
            conn.execute(
                "INSERT INTO sensors (code, provider, box_id, is_test_data) "
                "VALUES ($1,'NONE',$2::uuid,true) ON CONFLICT (code) DO NOTHING",
                [f"SNS-{row['code']}", row["id"]],
            )
            sensors += 1
        say(f"  ✓ {sensors} حساس مربوط بالصناديق")

    # تجاوزات الإعدادات لكل نطاق (§13) — بيانات لا كود
    from masar_api.services.settings import seed_defaults

    with conn.transaction():
        seed_defaults(conn)
    say("  ✓ تجاوزات الإعدادات التشغيلية (عرعر ٢٠ د · المحافظات ١٠ د · الرياض زمن طريق)")

    return ids


def main() -> int:
    cfg = get_config()
    if cfg.is_production:
        print("⛔ زرع بيانات الاختبار ممنوع في بيئة الإنتاج")
        return 2

    if "--reset" in sys.argv:
        from masar_db.migrate import reset

        reset(verbose=True)

    print(f"\nزرع بيانات الاختبار في {cfg.database.name} …")
    conn = connect()
    try:
        seed(conn)
    finally:
        conn.close()

    source = ("من MASAR_SEED_PASSWORD" if os.environ.get("MASAR_SEED_PASSWORD")
              else "مولَّدة عشوائيًا — تُعرض هنا مرة واحدة ولا تُحفظ في أي ملف")
    print("\nالحسابات التجريبية (كلمة المرور واحدة للجميع):")
    print(f"  كلمة المرور: {DEMO_PASSWORD}   ({source})\n")
    for email, name, role, _ in USERS:
        print(f"  {email:28s}  {role:20s}  {name}")
    print(f"  {'drv-ryd-01@masar.test':28s}  {'DRIVER':20s}  فهد بن عبدالله القحطاني")
    print("\n✅ اكتمل الزرع. كل الصفوف موسومة is_test_data = true.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
