"""الإشعارات الخارجية — صندوق صادر ومحوّلات مزوّدين.

المبدأ (§20/§34): **لا يُعرض ما لم يُرسَل على أنه أُرسل.** حين لا يوجد مزوّد
مُعدّ، الإشعار يُسجَّل بحالة ``NO_PROVIDER`` صريحة، ولا يُوسم «مُرسَل» ولا
يُخفى. المشرف يرى في الشاشة أن التنبيه لم يغادر النظام، ولماذا.

البنية:

* ``enqueue`` — يكتب الإشعار في **نفس معاملة الحدث**. إن تراجعت المعاملة لم
  يُرسل شيء، وإن ثبتت وقع الإشعار حتمًا. هذا هو مكسب نمط الصندوق.
* ``deliver_pending`` — عامل مستقل يسحب المستحق ويحاول الإرسال بإعادة محاولة
  متصاعدة. تعطّل المزوّد يؤخّر الإشعار ولا يُسقط العملية التشغيلية.
* المحوّلات: ``LogProvider`` (تطوير)، ``SmtpProvider``، ``HttpSmsProvider``،
  و``NoProvider`` الافتراضي الذي يُصرّح بعدم توفره بدل ادّعاء الإرسال.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

import pgwire
from masar_core.config import get_config
from masar_core.constants import Severity
from masar_core.errors import DependencyUnavailable, ValidationError
from masar_db.driver import SecurityContext, transaction

logger = logging.getLogger("masar.notifications")

#: القنوات المدعومة في المخطط
CHANNELS = ("SMS", "EMAIL", "PUSH", "WEBHOOK", "LOG")

#: خطورة التنبيه ← أولوية الإشعار. التنبيه المنخفض لا يوقظ أحدًا ليلًا.
SEVERITY_PRIORITY = {
    Severity.CRITICAL: "CRITICAL",
    Severity.HIGH: "HIGH",
    Severity.MEDIUM: "NORMAL",
    Severity.LOW: "LOW",
}

#: تصاعد إعادة المحاولة بالدقائق — لا نُغرق مزوّدًا متعثرًا بمحاولات متلاحقة
RETRY_BACKOFF_MINUTES = (1, 5, 15, 60, 240)


# ================================================== محوّلات المزوّدين ========

class NoProvider:
    """لا مزوّد مُعدّ — الحالة الافتراضية والصادقة."""

    name = "none"
    is_real = False
    channels: tuple[str, ...] = ()

    def send(self, channel: str, recipient: str, subject: str | None,
             body: str) -> str:
        raise DependencyUnavailable(
            "لا يوجد مزوّد إشعارات مُعدّ. التنبيهات تعمل داخل النظام، لكن لا "
            "تُرسل رسائل خارجية. للتفعيل اضبط MASAR_NOTIFY_PROVIDER."
        )


class LogProvider:
    """يكتب الإشعار في السجل بدل إرساله — لبيئات التطوير والاختبار.

    **ليس مزوّدًا حقيقيًا** (``is_real = False``): ما يُرسل به يظهر في الشاشة
    موسومًا بأنه لم يغادر الخادم، كي لا يُقرأ سجل ناجح على أنه رسالة وصلت.
    """

    name = "log"
    is_real = False
    channels = CHANNELS

    def send(self, channel: str, recipient: str, subject: str | None,
             body: str) -> str:
        logger.info("إشعار [%s] إلى %s: %s — %s",
                    channel, recipient, subject or "", body[:200])
        return f"log:{dt.datetime.now(dt.timezone.utc).isoformat()}"


class SmtpProvider:
    """إرسال بريد عبر SMTP.

    ⚠️ **لم يُختبر في هذه البيئة** لعدم توفر خادم بريد. البنية مكتوبة كاملة،
    والتفعيل يحتاج بيانات خادم بريد ثم اختبار تكامل حقيقي قبل الاعتماد.
    """

    name = "smtp"
    is_real = True
    channels = ("EMAIL",)

    def __init__(self) -> None:
        cfg = get_config().notifications
        if not cfg.smtp_host:
            raise DependencyUnavailable("MASAR_SMTP_HOST غير محدد")
        self.host = cfg.smtp_host
        self.port = cfg.smtp_port
        self.username = cfg.smtp_username
        self.password = cfg.smtp_password
        self.sender = cfg.sender_email or "no-reply@masar.local"
        self.timeout = cfg.request_timeout_seconds
        self.use_tls = cfg.smtp_use_tls

    def send(self, channel: str, recipient: str, subject: str | None,
             body: str) -> str:
        if channel != "EMAIL":
            raise ValidationError(f"مزوّد البريد لا يدعم القناة {channel}")

        import smtplib
        import uuid
        from email.message import EmailMessage

        message = EmailMessage()
        message["Subject"] = subject or "إشعار من مسار عينات"
        message["From"] = self.sender
        message["To"] = recipient
        message["Message-ID"] = f"<{uuid.uuid4()}@masar>"
        message.set_content(body)

        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as client:
                if self.use_tls:
                    client.starttls()
                if self.username:
                    client.login(self.username, self.password or "")
                client.send_message(message)
        except Exception as exc:
            raise DependencyUnavailable(
                f"تعذر إرسال البريد عبر {self.host}: {exc}", provider="smtp"
            ) from exc
        return message["Message-ID"]


class HttpSmsProvider:
    """إرسال رسائل نصية عبر واجهة HTTP لمزوّد اتصالات.

    ⚠️ **لم يُختبر في هذه البيئة** لعدم توفر مزوّد. الشكل العام (مفتاح في
    الترويسة، جسم JSON، مهلة) قياسي، وقد يحتاج تعديلًا طفيفًا حسب المزوّد
    المختار. **المفتاح لا يُخزَّن في الشيفرة ولا يصل إلى المتصفح** (§29).
    """

    name = "http_sms"
    is_real = True
    channels = ("SMS",)

    def __init__(self) -> None:
        cfg = get_config().notifications
        if not cfg.sms_url:
            raise DependencyUnavailable("MASAR_SMS_URL غير محدد")
        self.url = cfg.sms_url
        self.api_key = cfg.sms_api_key
        self.sender = cfg.sender_name or "MASAR"
        self.timeout = cfg.request_timeout_seconds

    def send(self, channel: str, recipient: str, subject: str | None,
             body: str) -> str:
        if channel != "SMS":
            raise ValidationError(f"مزوّد الرسائل لا يدعم القناة {channel}")

        import urllib.error
        import urllib.request

        payload = json.dumps({
            "to": recipient, "sender": self.sender, "body": body,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=payload, method="POST",
            headers={
                "content-type": "application/json",
                **({"authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DependencyUnavailable(
                f"تعذر الوصول إلى مزوّد الرسائل: {exc}", provider="http_sms"
            ) from exc
        try:
            return str(json.loads(raw).get("id") or raw[:80])
        except json.JSONDecodeError:
            return raw[:80]


_PROVIDERS = {
    "none": NoProvider,
    "log": LogProvider,
    "smtp": SmtpProvider,
    "http_sms": HttpSmsProvider,
}


def build_provider(name: str | None = None):
    key = (name or get_config().notifications.provider).lower()
    factory = _PROVIDERS.get(key)
    if factory is None:
        raise DependencyUnavailable(f"مزوّد إشعارات غير معروف: {key}")
    return factory()


def provider_status() -> dict[str, Any]:
    """يعلن حالة التكامل بصراحة — تُعرض في شاشة التكاملات (§34)."""
    cfg = get_config().notifications
    try:
        provider = build_provider()
        return {
            "provider": provider.name,
            "available": not isinstance(provider, NoProvider),
            "is_real_integration": provider.is_real,
            "channels": list(provider.channels),
            "message_ar": (
                "لا يوجد مزوّد إشعارات — التنبيهات داخل النظام فقط"
                if isinstance(provider, NoProvider) else
                "مزوّد تسجيل: الرسائل تُكتب في سجل الخادم ولا تُرسل فعليًا"
                if not provider.is_real else
                f"مزوّد فعّال: {provider.name}"
            ),
        }
    except DependencyUnavailable as exc:
        return {
            "provider": cfg.provider,
            "available": False,
            "is_real_integration": False,
            "channels": [],
            "message_ar": exc.message,
        }


# ==================================================== صندوق الصادر ==========

def enqueue(
    conn: pgwire.Connection,
    *,
    channel: str,
    recipient: str,
    body_ar: str,
    subject_ar: str | None = None,
    priority: str = "NORMAL",
    alert_id: str | None = None,
    shipment_id: str | None = None,
    route_id: str | None = None,
    hub_id: str | None = None,
    user_id: str | None = None,
    dedupe_key: str | None = None,
    is_test_data: bool = False,
) -> str | None:
    """يضع إشعارًا في الصندوق **داخل معاملة الحدث نفسها**.

    يعيد ``None`` إن كان مكررًا (نفس ``dedupe_key``) — وهي حالة طبيعية لا خطأ:
    تنبيه متكرر لا يعني رسالتين.
    """
    if channel not in CHANNELS:
        raise ValidationError(f"قناة إشعار غير معروفة: {channel}")
    if not (recipient or "").strip():
        raise ValidationError("لا يمكن إنشاء إشعار بلا مستلم")
    if not (body_ar or "").strip():
        raise ValidationError("لا يمكن إنشاء إشعار بلا نص")

    # الحالة الابتدائية تعكس الواقع: بلا مزوّد لا يُوسم الإشعار «معلّقًا»
    # كأنه في طريقه، بل NO_PROVIDER كي يقرأ المشغّل الحقيقة من الشاشة.
    try:
        provider = build_provider()
        status = "PENDING" if not isinstance(provider, NoProvider) else "NO_PROVIDER"
    except DependencyUnavailable:
        status = "NO_PROVIDER"

    return conn.fetch_value(
        """
        INSERT INTO notifications (
            channel, recipient, subject_ar, body_ar, status, priority,
            alert_id, shipment_id, route_id, hub_id, user_id, dedupe_key,
            is_test_data
        ) VALUES ($1,$2,$3,$4,$5,$6,$7::uuid,$8::uuid,$9::uuid,$10::uuid,
                  $11::uuid,$12,$13)
        ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
        RETURNING id::text
        """,
        [channel, recipient.strip(), subject_ar, body_ar.strip(), status, priority,
         alert_id, shipment_id, route_id, hub_id, user_id, dedupe_key, is_test_data],
    )


def enqueue_for_alert(
    conn: pgwire.Connection,
    alert: dict[str, Any],
    *,
    recipients: list[tuple[str, str]],
) -> int:
    """يحوّل تنبيهًا إلى إشعارات لمستلميه. ``recipients`` = [(قناة، عنوان)]."""
    priority = SEVERITY_PRIORITY.get(alert.get("severity", ""), "NORMAL")
    created = 0
    for channel, recipient in recipients:
        notification_id = enqueue(
            conn, channel=channel, recipient=recipient,
            subject_ar=alert.get("title_ar"),
            body_ar=alert.get("body_ar", ""),
            priority=priority,
            alert_id=alert.get("id"),
            shipment_id=alert.get("shipment_id"),
            route_id=alert.get("route_id"),
            hub_id=alert.get("hub_id"),
            dedupe_key=f"ALERT:{alert.get('id')}:{channel}:{recipient}",
            is_test_data=bool(alert.get("is_test_data")),
        )
        created += 1 if notification_id else 0
    return created


# ====================================================== عامل الإرسال ========

def deliver_pending(limit: int = 50, *, now: dt.datetime | None = None) -> dict[str, int]:
    """يحاول إرسال الإشعارات المستحقة. يُشغَّل دوريًا من مهمة خلفية."""
    now = now or dt.datetime.now(dt.timezone.utc)
    counters = {"sent": 0, "failed": 0, "retried": 0, "skipped": 0}

    try:
        provider = build_provider()
    except DependencyUnavailable:
        provider = NoProvider()
    if isinstance(provider, NoProvider):
        counters["skipped"] = -1  # -1 يعني: لا مزوّد أصلًا، لم تُفحص الطوابير
        return counters

    context = SecurityContext.system("NOTIFIER")
    with transaction(context) as conn:
        rows = conn.fetch_all(
            """
            SELECT id::text AS id, channel, recipient, subject_ar, body_ar,
                   attempts, max_attempts
            FROM notifications
            WHERE status IN ('PENDING', 'NO_PROVIDER')
              AND next_attempt_at <= $1::timestamptz
            ORDER BY CASE priority WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1
                                   WHEN 'NORMAL' THEN 2 ELSE 3 END,
                     created_at
            LIMIT $2
            FOR UPDATE SKIP LOCKED
            """,
            [now, limit],
        )

    for row in rows:
        if row["channel"] not in provider.channels:
            with transaction(context) as conn:
                conn.execute(
                    "UPDATE notifications SET status = 'FAILED', "
                    "last_error = $1, provider = $2 WHERE id = $3::uuid",
                    [f"المزوّد {provider.name} لا يدعم القناة {row['channel']}",
                     provider.name, row["id"]],
                )
            counters["failed"] += 1
            continue

        try:
            reference = provider.send(
                row["channel"], row["recipient"], row["subject_ar"], row["body_ar"])
        except Exception as exc:
            attempts = int(row["attempts"]) + 1
            exhausted = attempts >= int(row["max_attempts"])
            delay = RETRY_BACKOFF_MINUTES[
                min(attempts - 1, len(RETRY_BACKOFF_MINUTES) - 1)]
            with transaction(context) as conn:
                conn.execute(
                    "UPDATE notifications SET attempts = $1, "
                    "status = CASE WHEN $2 THEN 'FAILED' ELSE 'PENDING' END, "
                    "last_error = $3, provider = $4, "
                    "next_attempt_at = $5::timestamptz WHERE id = $6::uuid",
                    [attempts, exhausted, str(exc)[:1000], provider.name,
                     now + dt.timedelta(minutes=delay), row["id"]],
                )
            counters["failed" if exhausted else "retried"] += 1
            continue

        with transaction(context) as conn:
            conn.execute(
                "UPDATE notifications SET status = 'SENT', sent_at = now(), "
                "attempts = attempts + 1, provider = $1, provider_ref = $2, "
                "last_error = NULL WHERE id = $3::uuid",
                [provider.name, reference, row["id"]],
            )
        counters["sent"] += 1

    return counters


def list_notifications(
    context: SecurityContext, *, status: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    from masar_db.driver import session

    clauses: list[str] = []
    params: list[Any] = []
    if status:
        params.append(status)
        clauses.append(f"n.status = ${len(params)}")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with session(context) as conn:
        rows = conn.fetch_all(
            f"""
            SELECT n.id::text AS id, n.channel, n.recipient, n.subject_ar,
                   n.body_ar, n.status, n.priority, n.attempts, n.last_error,
                   n.provider, n.created_at, n.sent_at,
                   h.name_ar AS hub_name_ar
            FROM notifications n
            LEFT JOIN hubs h ON h.id = n.hub_id
            {where}
            ORDER BY n.created_at DESC
            LIMIT {int(limit)}
            """,
            params,
        )
    return [dict(row) for row in rows]
