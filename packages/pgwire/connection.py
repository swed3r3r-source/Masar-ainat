"""اتصال PostgreSQL: مصادقة، استعلامات، معاملات، LISTEN/NOTIFY."""

from __future__ import annotations

import socket
import ssl as ssl_module
import struct
import threading
from typing import Any, Iterable, Sequence

from . import auth as _auth
from . import protocol as _p
from .exceptions import PgError, PgOperationalError, error_from_fields
from .types import decode_value, encode_param

_I32 = struct.Struct("!i")


class Row(dict):
    """صف نتيجة: يدعم الوصول بالاسم ``row["id"]`` وبالنقطة ``row.id``."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover
            raise AttributeError(name) from exc


class Notification:
    __slots__ = ("pid", "channel", "payload")

    def __init__(self, pid: int, channel: str, payload: str) -> None:
        self.pid = pid
        self.channel = channel
        self.payload = payload

    def __repr__(self) -> str:  # pragma: no cover
        return f"Notification({self.channel!r}, {self.payload!r})"


class Connection:
    """اتصال متزامن واحد. آمن للاستخدام من خيط واحد في كل لحظة (قفل داخلي)."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 5432,
        user: str = "postgres",
        password: str | None = None,
        database: str | None = None,
        unix_socket: str | None = None,
        connect_timeout: float = 10.0,
        statement_timeout_ms: int | None = 30_000,
        application_name: str = "masar-ainat",
        sslmode: str = "prefer",
        options: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.database = database or user
        self._password = password
        self._unix_socket = unix_socket
        self._sslmode = sslmode
        self._lock = threading.RLock()
        self.closed = False
        self.backend_pid: int | None = None
        self._backend_secret: int | None = None
        self.parameters: dict[str, str] = {}
        self.transaction_status = "I"
        self.notifications: list[Notification] = []
        self._prepared: dict[str, str] = {}
        self._stmt_counter = 0
        self._buffer = bytearray()
        self._in_transaction_depth = 0

        self._sock = self._open_socket(connect_timeout)
        try:
            self._startup(application_name, options)
            if statement_timeout_ms:
                self.execute(f"SET statement_timeout = {int(statement_timeout_ms)}")
            self.execute("SET TIME ZONE 'UTC'")
        except Exception:
            try:
                self._sock.close()
            finally:
                self.closed = True
            raise

    # ------------------------------------------------------------- الاتصال ---
    def _open_socket(self, timeout: float) -> socket.socket:
        if self._unix_socket:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(self._unix_socket)
        else:
            sock = socket.create_connection((self.host, self.port), timeout=timeout)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if self._sslmode in ("require", "prefer", "verify-full"):
                sock = self._try_ssl(sock)
        sock.settimeout(None)
        return sock

    def _try_ssl(self, sock: socket.socket) -> socket.socket:
        sock.sendall(_p.ssl_request())
        reply = sock.recv(1)
        if reply == b"S":
            context = ssl_module.create_default_context()
            if self._sslmode != "verify-full":
                context.check_hostname = False
                context.verify_mode = ssl_module.CERT_NONE
            return context.wrap_socket(sock, server_hostname=self.host)
        if self._sslmode in ("require", "verify-full"):
            sock.close()
            raise PgOperationalError("الخادم رفض TLS بينما sslmode يفرضه")
        return sock

    def _send(self, data: bytes) -> None:
        try:
            self._sock.sendall(data)
        except OSError as exc:
            self.closed = True
            raise PgOperationalError(f"فشل الإرسال إلى قاعدة البيانات: {exc}") from exc

    def _recv_exact(self, size: int) -> bytes:
        while len(self._buffer) < size:
            try:
                chunk = self._sock.recv(65536)
            except (socket.timeout, TimeoutError):
                # المهلة تُدار من طبقة أعلى (poll_notifications) ولا تُفسد الاتصال
                # ما دامت عند حد رسالة، أي لا توجد بايتات جزئية في المخزن.
                raise
            except OSError as exc:
                self.closed = True
                raise PgOperationalError(f"فشل الاستقبال: {exc}") from exc
            if not chunk:
                self.closed = True
                raise PgOperationalError("أُغلق الاتصال بقاعدة البيانات من الطرف الآخر")
            self._buffer.extend(chunk)
        out = bytes(self._buffer[:size])
        del self._buffer[:size]
        return out

    def _read_message(self) -> tuple[bytes, bytes]:
        tag = self._recv_exact(1)
        length = _I32.unpack(self._recv_exact(4))[0]
        body = self._recv_exact(length - 4) if length > 4 else b""
        return tag, body

    # ------------------------------------------------------------- البدء ---
    def _startup(self, application_name: str, options: str | None) -> None:
        params = {
            "user": self.user,
            "database": self.database,
            "application_name": application_name,
            "client_encoding": "UTF8",
            "DateStyle": "ISO, YMD",
        }
        if options:
            params["options"] = options
        self._send(_p.startup_message(params))

        scram: _auth.ScramClient | None = None
        while True:
            tag, body = self._read_message()
            if tag == b"R":
                code = _I32.unpack_from(body, 0)[0]
                if code == 0:
                    continue
                if code == 3:  # cleartext
                    self._require_password()
                    self._send(_p.password_message(self._password.encode("utf-8")))
                elif code == 5:  # md5
                    self._require_password()
                    salt = body[4:8]
                    self._send(
                        _p.password_message(
                            _auth.md5_password(self.user, self._password, salt)
                        )
                    )
                elif code == 10:  # SASL
                    self._require_password()
                    mechanisms = body[4:].split(b"\x00")
                    if b"SCRAM-SHA-256" not in mechanisms:
                        raise PgOperationalError(
                            "الخادم لا يعرض SCRAM-SHA-256؛ الآليات الأخرى غير مدعومة"
                        )
                    scram = _auth.ScramClient(self.user, self._password)
                    self._send(
                        _p.sasl_initial_response("SCRAM-SHA-256", scram.first_message())
                    )
                elif code == 11:  # SASLContinue
                    assert scram is not None
                    self._send(_p.sasl_response(scram.final_message(body[4:])))
                elif code == 12:  # SASLFinal
                    assert scram is not None
                    scram.verify_final(body[4:])
                else:
                    raise PgOperationalError(f"آلية مصادقة غير مدعومة: {code}")
            elif tag == b"S":
                key, offset = _p.read_cstring(body, 0)
                value, _ = _p.read_cstring(body, offset)
                self.parameters[key] = value
            elif tag == b"K":
                self.backend_pid = _I32.unpack_from(body, 0)[0]
                self._backend_secret = _I32.unpack_from(body, 4)[0]
            elif tag == b"Z":
                self.transaction_status = body[:1].decode("ascii")
                return
            elif tag == b"E":
                raise error_from_fields(_p.parse_error_fields(body))
            elif tag == b"N":
                pass
            else:
                raise PgOperationalError(f"رسالة غير متوقعة أثناء البدء: {tag!r}")

    def _require_password(self) -> None:
        if self._password is None:
            raise PgOperationalError("الخادم يطلب كلمة مرور ولم تُزود")

    # -------------------------------------------------------- الاستعلامات ---
    def execute(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
        *,
        prepare: bool = True,
    ) -> "Result":
        """ينفذ استعلامًا واحدًا مع معاملات مربوطة ويعيد :class:`Result`."""
        with self._lock:
            if self.closed:
                raise PgOperationalError("الاتصال مغلق")
            if not params:
                return self._simple_or_extended(sql, [], prepare)
            encoded = [encode_param(value) for value in params]
            return self._simple_or_extended(sql, encoded, prepare)

    def _simple_or_extended(
        self, sql: str, encoded: list[str | None], prepare: bool
    ) -> "Result":
        statement = ""
        if prepare and encoded:
            statement = self._prepared.get(sql, "")
            if not statement:
                self._stmt_counter += 1
                statement = f"masar_s{self._stmt_counter}"
                self._send(_p.parse_message(statement, sql))
                self._prepared[sql] = statement
        else:
            self._send(_p.parse_message("", sql))

        self._send(_p.bind_message("", statement, encoded))
        self._send(_p.describe_message(b"P", ""))
        self._send(_p.execute_message("", 0))
        self._send(_p.sync_message())
        return self._collect()

    def _collect(self) -> "Result":
        columns: list[tuple[str, int]] = []
        rows: list[Row] = []
        command_tag = ""
        error: PgError | None = None
        notices: list[dict[str, str]] = []

        while True:
            tag, body = self._read_message()
            if tag == b"T":
                columns = _p.parse_row_description(body)
            elif tag == b"D":
                values = list(_p.parse_data_row(body))
                row = Row()
                for (name, oid), raw in zip(columns, values):
                    row[name] = None if raw is None else decode_value(raw, oid)
                rows.append(row)
            elif tag == b"C":
                command_tag, _ = _p.read_cstring(body, 0)
            elif tag == b"E":
                error = error_from_fields(_p.parse_error_fields(body))
            elif tag == b"N":
                notices.append(_p.parse_error_fields(body))
            elif tag == b"A":
                pid, channel, payload = _p.parse_notification(body)
                self.notifications.append(Notification(pid, channel, payload))
            elif tag == b"S":
                key, offset = _p.read_cstring(body, 0)
                value, _ = _p.read_cstring(body, offset)
                self.parameters[key] = value
            elif tag in (b"1", b"2", b"3", b"n", b"s", b"t", b"I"):
                continue
            elif tag == b"Z":
                self.transaction_status = body[:1].decode("ascii")
                break
            elif tag == b"G" or tag == b"H" or tag == b"W":  # pragma: no cover
                raise PgOperationalError("COPY غير مدعوم في هذا العميل")
            else:  # pragma: no cover
                raise PgOperationalError(f"رسالة غير متوقعة: {tag!r}")

        if error is not None:
            raise error
        return Result(columns=[name for name, _ in columns], rows=rows,
                      command_tag=command_tag, notices=notices)

    # -------------------------------------------------------- اختصارات ---
    def fetch_all(self, sql: str, params: Sequence[Any] | None = None) -> list[Row]:
        return self.execute(sql, params).rows

    def fetch_one(self, sql: str, params: Sequence[Any] | None = None) -> Row | None:
        rows = self.execute(sql, params).rows
        return rows[0] if rows else None

    def fetch_value(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        row = self.fetch_one(sql, params)
        if row is None:
            return None
        return next(iter(row.values()))

    def execute_script(self, sql: str) -> None:
        """ينفذ عدة عبارات مفصولة بفاصلة منقوطة (للترحيلات فقط، بلا معاملات)."""
        with self._lock:
            self._send(_p.query_message(sql))
            self._collect()

    # -------------------------------------------------------- المعاملات ---
    def begin(self, *, isolation: str | None = None, read_only: bool = False) -> None:
        if self._in_transaction_depth == 0:
            statement = "BEGIN"
            if isolation:
                statement += f" ISOLATION LEVEL {isolation}"
            if read_only:
                statement += " READ ONLY"
            self.execute(statement)
        else:
            self.execute(f"SAVEPOINT masar_sp{self._in_transaction_depth}")
        self._in_transaction_depth += 1

    def commit(self) -> None:
        if self._in_transaction_depth == 0:
            return
        self._in_transaction_depth -= 1
        if self._in_transaction_depth == 0:
            self.execute("COMMIT")
        else:
            self.execute(f"RELEASE SAVEPOINT masar_sp{self._in_transaction_depth}")

    def rollback(self) -> None:
        if self._in_transaction_depth == 0:
            return
        self._in_transaction_depth -= 1
        if self._in_transaction_depth == 0:
            self.execute("ROLLBACK")
        else:
            self.execute(f"ROLLBACK TO SAVEPOINT masar_sp{self._in_transaction_depth}")

    class _Tx:
        def __init__(self, conn: "Connection", **kwargs: Any) -> None:
            self.conn = conn
            self.kwargs = kwargs

        def __enter__(self) -> "Connection":
            self.conn.begin(**self.kwargs)
            return self.conn

        def __exit__(self, exc_type, exc, tb) -> bool:
            if exc_type is None:
                self.conn.commit()
            else:
                try:
                    self.conn.rollback()
                except Exception:  # pragma: no cover
                    pass
            return False

    def transaction(self, **kwargs: Any) -> "Connection._Tx":
        """مدير سياق للمعاملة، يدعم التداخل عبر SAVEPOINT."""
        return Connection._Tx(self, **kwargs)

    @property
    def in_transaction(self) -> bool:
        return self._in_transaction_depth > 0

    # ------------------------------------------------------ LISTEN/NOTIFY ---
    def listen(self, channel: str) -> None:
        if not channel.replace("_", "").isalnum():
            raise ValueError("اسم القناة يجب أن يكون أبجديًا رقميًا")
        self.execute(f'LISTEN "{channel}"')

    def notify(self, channel: str, payload: str = "") -> None:
        self.execute("SELECT pg_notify($1, $2)", [channel, payload])

    def poll_notifications(self, timeout: float = 1.0) -> list[Notification]:
        """ينتظر إشعارات واردة حتى المهلة ويعيد ما تجمع."""
        with self._lock:
            self._sock.settimeout(timeout)
            try:
                while True:
                    tag, body = self._read_message()
                    if tag == b"A":
                        pid, channel, payload = _p.parse_notification(body)
                        self.notifications.append(Notification(pid, channel, payload))
                    elif tag == b"Z":
                        self.transaction_status = body[:1].decode("ascii")
                    elif tag == b"E":
                        raise error_from_fields(_p.parse_error_fields(body))
            except (socket.timeout, TimeoutError):
                pass
            finally:
                self._sock.settimeout(None)
            out, self.notifications = self.notifications, []
            return out

    # ---------------------------------------------------------- الإغلاق ---
    def reset(self) -> None:
        """يعيد الاتصال إلى حالة نظيفة قبل إعادته للتجميعة."""
        if self.closed:
            return
        try:
            if self.transaction_status != "I":
                self.execute("ROLLBACK")
            self._in_transaction_depth = 0
            self.execute("RESET ALL")
            self.execute("SET TIME ZONE 'UTC'")
        except PgError:
            self.close()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._send(_p.terminate_message())
        except Exception:  # pragma: no cover
            pass
        try:
            self._sock.close()
        except Exception:  # pragma: no cover
            pass

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class Result:
    __slots__ = ("columns", "rows", "command_tag", "notices")

    def __init__(
        self,
        columns: list[str],
        rows: list[Row],
        command_tag: str,
        notices: list[dict[str, str]],
    ) -> None:
        self.columns = columns
        self.rows = rows
        self.command_tag = command_tag
        self.notices = notices

    @property
    def rowcount(self) -> int:
        parts = self.command_tag.split()
        if not parts:
            return len(self.rows)
        if parts[0] == "INSERT" and len(parts) >= 3:
            return int(parts[2])
        if parts[0] in ("UPDATE", "DELETE", "SELECT", "MOVE", "FETCH", "COPY") and len(parts) >= 2:
            return int(parts[1])
        return len(self.rows)

    def __iter__(self) -> Iterable[Row]:
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)


def connect(**kwargs: Any) -> Connection:
    return Connection(**kwargs)
