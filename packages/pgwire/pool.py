"""تجميعة اتصالات آمنة للخيوط."""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from typing import Any, Iterator

from .connection import Connection
from .exceptions import PgError, PgOperationalError


class Pool:
    """تجميعة اتصالات بسيطة بحد أدنى وأقصى، مع فحص صحة الاتصال قبل الإعارة."""

    def __init__(
        self,
        *,
        min_size: int = 1,
        max_size: int = 10,
        max_idle_seconds: float = 300.0,
        **connect_kwargs: Any,
    ) -> None:
        self._connect_kwargs = connect_kwargs
        self._max_size = max_size
        self._max_idle = max_idle_seconds
        self._idle: queue.LifoQueue[tuple[Connection, float]] = queue.LifoQueue()
        self._lock = threading.Lock()
        self._created = 0
        self._closed = False
        for _ in range(max(0, min_size)):
            self._idle.put((self._new_connection(), time.monotonic()))

    def _new_connection(self) -> Connection:
        with self._lock:
            if self._created >= self._max_size:
                raise PgOperationalError("تجاوز الحد الأقصى لعدد الاتصالات")
            self._created += 1
        try:
            return Connection(**self._connect_kwargs)
        except Exception:
            with self._lock:
                self._created -= 1
            raise

    def acquire(self, timeout: float = 10.0) -> Connection:
        if self._closed:
            raise PgOperationalError("التجميعة مغلقة")
        deadline = time.monotonic() + timeout
        while True:
            try:
                conn, idled_at = self._idle.get_nowait()
            except queue.Empty:
                with self._lock:
                    can_grow = self._created < self._max_size
                if can_grow:
                    return self._new_connection()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PgOperationalError("انتهت مهلة انتظار اتصال من التجميعة")
                try:
                    conn, idled_at = self._idle.get(timeout=min(remaining, 0.5))
                except queue.Empty:
                    continue
            if conn.closed or (time.monotonic() - idled_at) > self._max_idle:
                self._discard(conn)
                continue
            return conn

    def release(self, conn: Connection) -> None:
        if conn.closed or self._closed:
            self._discard(conn)
            return
        try:
            conn.reset()
        except PgError:
            self._discard(conn)
            return
        if conn.closed:
            self._discard(conn)
            return
        self._idle.put((conn, time.monotonic()))

    def _discard(self, conn: Connection) -> None:
        with contextlib.suppress(Exception):
            conn.close()
        with self._lock:
            self._created = max(0, self._created - 1)

    @contextlib.contextmanager
    def connection(self, timeout: float = 10.0) -> Iterator[Connection]:
        conn = self.acquire(timeout)
        try:
            yield conn
        finally:
            self.release(conn)

    @contextlib.contextmanager
    def transaction(self, timeout: float = 10.0, **tx_kwargs: Any) -> Iterator[Connection]:
        conn = self.acquire(timeout)
        try:
            with conn.transaction(**tx_kwargs):
                yield conn
        finally:
            self.release(conn)

    def close(self) -> None:
        self._closed = True
        while True:
            try:
                conn, _ = self._idle.get_nowait()
            except queue.Empty:
                break
            self._discard(conn)

    @property
    def size(self) -> int:
        return self._created
