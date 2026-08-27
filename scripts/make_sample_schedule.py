"""يولّد ملف جدول أسبوعي تجريبي — سليم ومع أخطاء مقصودة للاختبار (§31).

المواعيد **نسبية للحظة التوليد** (تبدأ بعد ساعة من الآن) حتى تعمل الدورة
الكاملة — بما فيها تنفيذ السائق — في أي وقت يُشغَّل فيه الاختبار.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from masar_api.services.imports import TEMPLATE_COLUMNS  # noqa: E402

HEADERS = [column.label_ar for column in TEMPLATE_COLUMNS]
TZ = dt.timezone(dt.timedelta(hours=3))

# (جهة الالتقاط، جهة التسليم، مركز الانطلاق، إزاحة الالتقاط بالدقائق، مهلة SLA بالساعات)
RIYADH_ROUTINE = [
    ("PHC-RYD-01", "LAB-RYD-01", "H-RYD-1", 0, 4),
    ("PHC-RYD-02", "LAB-RYD-01", "H-RYD-1", 30, 4),
    ("PHC-RYD-03", "LAB-RYD-01", "H-RYD-1", 60, 4),
    ("PHC-RYD-04", "LAB-RYD-01", "H-RYD-1", 90, 4),
    ("PHC-RYD-05", "LAB-RYD-02", "H-RYD-1", 120, 4),
    ("PHC-RYD-06", "LAB-RYD-02", "H-RYD-1", 150, 4),
    ("PHC-RYD-07", "LAB-RYD-02", "H-RYD-1", 180, 4),
    ("PHC-RYD-08", "LAB-RYD-01", "H-RYD-1", 210, 4),
    # مستشفيات — لا يجوز خلطها مع المراكز الصحية على السائق نفسه (HC-13)
    ("HOS-RYD-01", "LAB-RYD-01", "H-RYD-1", 15, 4),
    ("HOS-RYD-02", "LAB-RYD-01", "H-RYD-1", 75, 4),
    ("HOS-RYD-03", "LAB-RYD-02", "H-RYD-1", 135, 4),
    # بنك دم — مستثنى من قيد الخلط (HC-14)
    ("BLD-RYD-01", "LAB-RYD-01", "H-RYD-1", 240, 3),
]

ARAR_ROUTINE = [
    ("PHC-ARR-01", "LAB-ARR-01", "H-ARR-1", 0, 4),
    ("PHC-ARR-02", "LAB-ARR-01", "H-ARR-1", 60, 4),
    ("PHC-ARR-03", "LAB-ARR-01", "H-ARR-1", 120, 5),
    ("HOS-ARR-01", "LAB-ARR-01", "H-ARR-1", 30, 4),
]

#: الحديثة تبعد ~١٥٠ كم طريقًا عن عرعر — رحلة بعيدة **ممكنة** (HC-15/HC-16)
ARAR_LONG_HAUL = [
    ("PHC-HDT-01", "LAB-ARR-01", "H-ARR-1", 180, 9),
]

#: رفحاء تبعد ~٣٨٠ كم طريقًا — ذهابًا وإيابًا تتجاوز وردية ١٠ ساعات.
#: حالة **غير قابلة للتخطيط مقصودة** لإثبات تسجيل السبب (HC-19).
ARAR_UNPLANNABLE = [
    ("PHC-RFH-01", "LAB-ARR-01", "H-ARR-1", 90, 9),
]

KHARJ_ROUTINE = [
    ("PHC-KRJ-01", "LAB-KRJ-01", "H-KRJ-1", 30, 4),
    ("PHC-KRJ-02", "LAB-KRJ-01", "H-KRJ-1", 90, 4),
    ("HOS-KRJ-01", "LAB-KRJ-01", "H-KRJ-1", 120, 4),
]

#: التقاطان من نفس الجهة في اليوم نفسه (HC-11)
DOUBLE_PICKUP = [
    ("PHC-RYD-01", "LAB-RYD-01", "H-RYD-1", 330, 3),
]

ALL_ROUTINE = (
    RIYADH_ROUTINE + ARAR_ROUTINE + ARAR_LONG_HAUL + ARAR_UNPLANNABLE + KHARJ_ROUTINE
)


def compute_base(now: dt.datetime | None = None) -> dt.datetime:
    """أول موعد التقاط: بعد ساعة من الآن، مقرّبًا لأقرب ربع ساعة.

    إن كانت الساعة متأخرة بحيث لا تتسع الخطة لليوم، يُنقل الأساس إلى ٠٧:٠٠
    من اليوم التالي.
    """
    now = (now or dt.datetime.now(TZ)).astimezone(TZ)
    base = (now + dt.timedelta(hours=1)).replace(second=0, microsecond=0)
    base += dt.timedelta(minutes=(15 - base.minute % 15) % 15)
    # آخر موعد التقاط في اليوم = الأساس + ٣٣٠ دقيقة، ويجب أن ينتهي قبل ١٩:٠٠
    # **من اليوم نفسه**. مقارنة الساعة وحدها تفشل عند تجاوز منتصف الليل
    # (١٩:٠٠ + ٣٣٠ د = ٠٠:٣٠، وساعتها ٠ فتمر الشرط) فيُبنى جدول كامل خارج
    # أوقات عمل المراكز.
    last_pickup = base + dt.timedelta(minutes=330)
    if base.hour < 6 or last_pickup.date() != base.date() or last_pickup.hour >= 19:
        base = (base + dt.timedelta(days=1)).replace(hour=7, minute=0)
    return base


def _row(
    reference: str, base: dt.datetime, day_offset: int,
    pickup: str, dropoff: str, hub: str, minute_offset: int, sla_hours: float,
    *, temperature: str = "CHILLED", pieces: int = 3, service_type: str = "ROUTINE",
    sla_override: str | None = None, date_override: str | None = None,
) -> list[str]:
    moment = base + dt.timedelta(days=day_offset, minutes=minute_offset)
    sla = sla_override or (moment + dt.timedelta(hours=sla_hours)).strftime("%H:%M")
    values = {
        "external_reference": reference,
        "service_date": date_override or moment.date().isoformat(),
        "service_type": service_type,
        "hub_code": hub,
        "pickup_facility_code": pickup,
        "pickup_time": moment.strftime("%H:%M"),
        "pickup_window_from": "",
        "pickup_window_to": "",
        "dropoff_facility_code": dropoff,
        "sla_time": sla,
        "piece_count": str(pieces),
        "sample_types": "دم، بول",
        "temperature_mode": temperature,
        "pickup_contact_name": "",
        "pickup_contact_phone": "",
        "dropoff_contact_name": "",
        "dropoff_contact_phone": "",
        "notes": "",
    }
    return [values[column.key] for column in TEMPLATE_COLUMNS]


def build(
    days: int = 5, *, with_errors: bool = True, base: dt.datetime | None = None
) -> tuple[bytes, dt.datetime]:
    base = base or compute_base()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)

    counter = 0
    for offset in range(days):
        for pickup, dropoff, hub, minute, sla in ALL_ROUTINE:
            counter += 1
            writer.writerow(_row(f"REQ-{counter:05d}", base, offset,
                                 pickup, dropoff, hub, minute, sla))
        for pickup, dropoff, hub, minute, sla in DOUBLE_PICKUP:
            counter += 1
            writer.writerow(_row(f"REQ-{counter:05d}", base, offset,
                                 pickup, dropoff, hub, minute, sla))

    if with_errors:
        # ١) صف مكرر تمامًا لأول صف
        counter += 1
        writer.writerow(_row("REQ-00001", base, 0, "PHC-RYD-01", "LAB-RYD-01",
                             "H-RYD-1", 0, 4))
        # ٢) جهة غير مسجلة
        counter += 1
        writer.writerow(_row(f"REQ-{counter:05d}", base, 0, "PHC-UNKNOWN-99",
                             "LAB-RYD-01", "H-RYD-1", 0, 4))
        # ٣) مركز انطلاق غير مسجل
        counter += 1
        writer.writerow(_row(f"REQ-{counter:05d}", base, 0, "PHC-RYD-02",
                             "LAB-RYD-01", "H-XXX-9", 0, 4))
        # ٤) SLA قبل نافذة الالتقاط (التقاط صباحي وتسليم فجرًا في نفس اليوم)
        counter += 1
        morning = base.replace(hour=10, minute=0)
        writer.writerow(_row(f"REQ-{counter:05d}", base, 0, "PHC-RYD-03",
                             "LAB-RYD-01", "H-RYD-1",
                             int((morning - base).total_seconds() // 60), 0,
                             sla_override="06:00"))
        # ٥) SLA مستحيل: رفحاء ← عرعر خلال ١٠ دقائق
        counter += 1
        impossible = (base + dt.timedelta(minutes=10)).strftime("%H:%M")
        writer.writerow(_row(f"REQ-{counter:05d}", base, 0, "PHC-RFH-01",
                             "LAB-ARR-01", "H-ARR-1", 0, 0,
                             sla_override=impossible))
        # ٦) تاريخ بصيغة غير مفهومة
        counter += 1
        writer.writerow(_row(f"REQ-{counter:05d}", base, 0, "PHC-RYD-04",
                             "LAB-RYD-01", "H-RYD-1", 0, 4,
                             date_override="٣٢/١٣/٢٠٢٦"))
        # ٧) حقل إلزامي فارغ
        counter += 1
        row = _row(f"REQ-{counter:05d}", base, 0, "PHC-RYD-05", "LAB-RYD-01",
                   "H-RYD-1", 0, 4)
        row[HEADERS.index("رمز جهة الالتقاط")] = ""
        writer.writerow(row)
        # ٨) عدد قطع غير رقمي
        counter += 1
        row = _row(f"REQ-{counter:05d}", base, 0, "PHC-RYD-06", "LAB-RYD-01",
                   "H-RYD-1", 0, 4)
        row[HEADERS.index("عدد القطع")] = "ثلاثة"
        writer.writerow(row)

    return ("﻿" + buffer.getvalue()).encode("utf-8"), base


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("var/sample-schedule.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    # الجدول الأسبوعي يبدأ من الغد دائمًا. لو بدأ اليوم لتزاحمت رحلاته مع
    # خطة اليوم على السائقين أنفسهم، فتبقى رحلات اليوم بلا سائق ويُمنع
    # النشر — عطل يظهر في ساعات العمل فقط ويبدو خللًا في النظام وهو تصادم
    # بين بياناتَي اختبار.
    base = compute_base()
    if base.date() == dt.datetime.now(TZ).date():
        base = (base + dt.timedelta(days=1)).replace(hour=7, minute=0)
    content, base = build(days, base=base)
    output.write_bytes(content)
    (output.parent / "sample-schedule.base").write_text(base.isoformat(), encoding="utf-8")
    print(f"كُتب الملف: {output} ({output.stat().st_size} بايت)")
    print(f"أول موعد التقاط: {base:%Y-%m-%d %H:%M} (بتوقيت الرياض)")
    print(f"أيام الخطة: {[(base + dt.timedelta(days=d)).date().isoformat() for d in range(days)]}")


# --------------------------------------------------- خطة اليوم الحالي ------

#: مجموعة مضغوطة عمدًا: مركزان متقاربان ومهلة SLA ساعة واحدة، كي تبقى دورة
#: التنفيذ الكاملة ممكنة حتى في ساعة متأخرة نسبيًا من يوم العمل. توسيعها يجعل
#: اختبارات التنفيذ تُتخطّى في كل تشغيل بعد العصر.
#: ثلاث جهات متقاربة جغرافيًا بمواعيد متدرجة ومهلة واسعة، كي يجمعها المحرك
#: في **رحلة واحدة متعددة المحطات**. رحلة بمحطتين فقط لا تصلح لإثبات منع
#: القفز فوق محطة غير محسومة ولا منع التسليم قبل تسجيل الوصول.
#: ثلاث جهات قريبة من المختبر بمواعيد متقاربة (٠/١٥/٣٠ د) ومهلة ٥ ساعات.
#: التباعد الزمني الواسع كان يدفع المحرك إلى تقسيمها على رحلتين: قاعدة
#: «لا حركة قبل منتصف نافذة الالتقاط + ١٥ د» تجعل الفارق بين موعدين بعيدين
#: احتجازًا طويلًا، فيصير سائقان أرخص من الانتظار. التقارب يعيدها رحلة
#: واحدة متعددة المحطات — وهي الشكل الذي تحتاجه فحوص التسلسل.
SAME_DAY_SET = [
    ("PHC-RYD-04", "LAB-RYD-01", "H-RYD-1", 0, 5),
    ("PHC-RYD-06", "LAB-RYD-01", "H-RYD-1", 15, 5),
    ("PHC-RYD-01", "LAB-RYD-01", "H-RYD-1", 30, 5),
]


def build_same_day(
    now: dt.datetime | None = None, *, lead_minutes: int = 20,
    open_hour: int = 6, close_hour: int = 20,
) -> tuple[bytes, dt.datetime] | None:
    """جدول صغير لليوم الحالي لاختبار دورة التنفيذ الفعلية.

    يعيد ``None`` إذا كان الوقت متأخرًا بحيث لا تتسع بقية اليوم للتنفيذ —
    وهي حالة تشغيلية صحيحة لا خطأ.

    ``open_hour``/``close_hour`` هما أوقات عمل مركز الانطلاق المستخدمة في
    الفحص. تمريرهما أوسع من الافتراضي مشروط بأن تكون أوقات عمل المركز نفسها
    قد وُسِّعت فعلًا في قاعدة البيانات، وإلا سيرفض المحرك الخطة بقيد أوقات العمل.
    """
    now = (now or dt.datetime.now(TZ)).astimezone(TZ)
    last_offset = max(item[3] for item in SAME_DAY_SET)
    last_sla = max(item[4] for item in SAME_DAY_SET)
    span = dt.timedelta(minutes=last_offset, hours=last_sla)

    # **اليوم مرجع ثابت هنا، لا «الآن + مهلة».** اشتقاق اليوم من لحظة مستقبلية
    # يقفز إلى الغد قرب منتصف الليل، فتُبنى خطة لتاريخ الغد بينما يشترط بدء
    # الرحلة أن يكون تاريخها اليوم — فتفشل دورة التنفيذ لسبب لا علاقة له بها.
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    close_at = day_start + dt.timedelta(hours=close_hour)

    forward = (now + dt.timedelta(minutes=lead_minutes)).replace(
        second=0, microsecond=0)
    forward += dt.timedelta(minutes=(5 - forward.minute % 5) % 5)

    # آخر لحظة يمكن أن يبدأ عندها الجدول ويظل ينتهي قبل إغلاق المركز **اليوم**.
    latest = close_at - span - dt.timedelta(minutes=5)
    latest -= dt.timedelta(minutes=latest.minute % 5)

    # حين يتأخر الوقت نُرجِع الأساس إلى الوراء بدل إلغاء الاختبار: التخطيط يجري
    # على اليوم كوحدة زمنية، ونافذة التقاط مضت لا تمنع بناء خطة اليوم ولا
    # تنفيذها متأخرة — وهي حالة تشغيلية واقعية لا حيلة اختبار.
    base = min(forward, latest)
    last_delivery = base + span
    if (base < day_start + dt.timedelta(hours=open_hour)
            or last_delivery.date() != day_start.date()
            or last_delivery >= close_at):
        return None

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    for index, (pickup, dropoff, hub, minute, sla) in enumerate(SAME_DAY_SET, start=1):
        writer.writerow(_row(f"SDY-{index:05d}", base, 0, pickup, dropoff, hub, minute, sla))
    return ("\ufeff" + buffer.getvalue()).encode("utf-8"), base
