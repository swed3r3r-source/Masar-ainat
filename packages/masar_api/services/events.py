"""ناقل الأحداث الفورية — من قاعدة البيانات إلى المتصفح عبر SSE.

المسار: عملية تكتب في ``system_events`` ⇒ محفّز ``pg_notify`` ⇒ مستمع واحد
داخل الخادم على قناة ``masar_events`` ⇒ توزيع على المشتركين المتصلين حسب
نطاق كل مشترك.

اختير **SSE** على WebSocket لأن التدفق أحادي الاتجاه (خادم ⟵ عميل) ويعمل
عبر HTTP/2 وأي وكيل عكسي بلا ترقية بروتوكول، ويعيد الاتصال تلقائيًا في
المتصفح. الأوامر تُرسل بطلبات HTTP عادية.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import pgwire
from masar_core.config import get_config
from masar_db.driver import SecurityContext

NOTIFY_CHANNEL = "masar_events"

#: مواضيع الأحداث
TOPIC_ROUTE = "route"
TOPIC_SHIPMENT = "shipment"
TOPIC_ALERT = "alert"
TOPIC_POSITION = "position"
TOPIC_TEMPERATURE = "temperature"
TOPIC_PLAN = "plan"
TOPIC_ON_DEMAND = "on_demand"


def publish(
    conn: pgwire.Connection,
    topic: str,
    payload: dict[str, Any],
    *,
    hub_id: str | None = None,
    region_id: str | None = None,
    driver_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """ينشر حدثًا داخل نفس معاملة العملية — لا يُرسل إن فشلت المعاملة."""
    conn.execute(
        "INSERT INTO system_events (topic, payload, hub_id, region_id, driver_id, user_id) "
        "VALUES ($1, $2::jsonb, $3::uuid, $4::uuid, $5::uuid, $6::uuid)",
        [topic, pgwire.Jsonb(payload), hub_id, region_id, driver_id, user_id],
    )


# eq=False يحافظ على __hash__ الافتراضي بالهوية — لازم لتخزين المشتركين في set
@dataclass(slots=True, eq=False)
class Subscriber:
    queue: "asyncio.Queue[str]"
    context: SecurityContext
    topics: set[str] = field(default_factory=set)
    loop: Any = None

    def wants(self, event: dict[str, Any]) -> bool:
        if self.topics and event.get("topic") not in self.topics:
            return False
        role = self.context.role
        if role in ("ADMIN", "CENTRAL_PLANNER", "CONTROL_TOWER", "AUDITOR"):
            return True
        hub_id = event.get("hub_id")
        if hub_id and hub_id in self.context.hub_ids:
            return True
        if role == "DRIVER" and event.get("driver_id") == self.context.driver_id:
            return True
        if event.get("user_id") and event["user_id"] == self.context.user_id:
            return True
        return False


class EventBus:
    """مستمع واحد على قاعدة البيانات + توزيع داخلي على المشتركين."""

    def __init__(self) -> None:
        self._subscribers: set[Subscriber] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_event_id = 0
        self.connected = False
        self.last_error: str | None = None

    # ------------------------------------------------------------ اشتراك --
    def subscribe(self, context: SecurityContext, topics: set[str] | None = None) -> Subscriber:
        subscriber = Subscriber(
            queue=asyncio.Queue(maxsize=500),
            context=context,
            topics=topics or set(),
            loop=asyncio.get_running_loop(),
        )
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    # ------------------------------------------------------------ التوزيع --
    def _dispatch(self, event: dict[str, Any]) -> None:
        message = json.dumps(event, ensure_ascii=False, default=str)
        with self._lock:
            targets = [s for s in self._subscribers if s.wants(event)]
        for subscriber in targets:
            loop = subscriber.loop
            if loop is None or loop.is_closed():
                continue
            try:
                loop.call_soon_threadsafe(subscriber.queue.put_nowait, message)
            except (asyncio.QueueFull, RuntimeError):
                # المشترك بطيء أو أُغلقت حلقته — يُهمل هذا الحدث لديه
                continue

    # ------------------------------------------------------------ المستمع --
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._listen_loop, name="masar-event-listener", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def _listen_loop(self) -> None:
        cfg = get_config().database
        while not self._stop.is_set():
            conn = None
            try:
                conn = pgwire.connect(
                    host=cfg.host, port=cfg.port, user=cfg.user, password=cfg.password,
                    database=cfg.name, sslmode=cfg.sslmode,
                    statement_timeout_ms=0, application_name="masar-events",
                )
                # سياق نظامي للقراءة فقط من جدول الأحداث
                for key, value in SecurityContext.system("EVENT_BUS").as_settings().items():
                    conn.execute("SELECT set_config($1, $2, false)", [key, value])
                conn.listen(NOTIFY_CHANNEL)
                self.connected = True
                self.last_error = None

                while not self._stop.is_set():
                    notifications = conn.poll_notifications(timeout=1.0)
                    if not notifications:
                        continue
                    ids = []
                    for notification in notifications:
                        try:
                            ids.append(int(json.loads(notification.payload)["id"]))
                        except (ValueError, KeyError, json.JSONDecodeError):
                            continue
                    if not ids:
                        continue
                    rows = conn.fetch_all(
                        "SELECT id, topic, payload, hub_id::text, region_id::text, "
                        "driver_id::text, user_id::text, created_at "
                        "FROM system_events WHERE id = ANY($1::bigint[]) ORDER BY id",
                        [ids],
                    )
                    for row in rows:
                        self._last_event_id = max(self._last_event_id, row["id"])
                        self._dispatch({
                            "id": row["id"],
                            "topic": row["topic"],
                            "payload": row["payload"],
                            "hub_id": row["hub_id"],
                            "region_id": row["region_id"],
                            "driver_id": row["driver_id"],
                            "user_id": row["user_id"],
                            "at": row["created_at"].isoformat(),
                        })
            except Exception as exc:  # pragma: no cover - إعادة اتصال
                self.connected = False
                self.last_error = str(exc)
                time.sleep(2.0)
            finally:
                if conn is not None:
                    with contextlib.suppress(Exception):
                        conn.close()


bus = EventBus()
