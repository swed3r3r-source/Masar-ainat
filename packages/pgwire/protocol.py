"""بناء وقراءة رسائل بروتوكول PostgreSQL v3."""

from __future__ import annotations

import struct
from typing import Iterator

PROTOCOL_VERSION = 196608  # 3.0
SSL_REQUEST_CODE = 80877103
CANCEL_REQUEST_CODE = 80877102

_I32 = struct.Struct("!i")
_I16 = struct.Struct("!h")


def _cstring(text: str) -> bytes:
    return text.encode("utf-8") + b"\x00"


def startup_message(params: dict[str, str]) -> bytes:
    body = _I32.pack(PROTOCOL_VERSION)
    for key, value in params.items():
        body += _cstring(key) + _cstring(value)
    body += b"\x00"
    return _I32.pack(len(body) + 4) + body


def ssl_request() -> bytes:
    return _I32.pack(8) + _I32.pack(SSL_REQUEST_CODE)


def cancel_request(pid: int, secret: int) -> bytes:
    return _I32.pack(16) + _I32.pack(CANCEL_REQUEST_CODE) + _I32.pack(pid) + _I32.pack(secret)


def _msg(tag: bytes, body: bytes) -> bytes:
    return tag + _I32.pack(len(body) + 4) + body


def password_message(secret: bytes) -> bytes:
    return _msg(b"p", secret + b"\x00")


def sasl_initial_response(mechanism: str, initial: bytes) -> bytes:
    body = _cstring(mechanism) + _I32.pack(len(initial)) + initial
    return _msg(b"p", body)


def sasl_response(data: bytes) -> bytes:
    return _msg(b"p", data)


def query_message(sql: str) -> bytes:
    return _msg(b"Q", _cstring(sql))


def parse_message(statement: str, sql: str, param_oids: list[int] | None = None) -> bytes:
    oids = param_oids or []
    body = _cstring(statement) + _cstring(sql) + _I16.pack(len(oids))
    for oid in oids:
        body += _I32.pack(oid)
    return _msg(b"P", body)


def bind_message(
    portal: str,
    statement: str,
    params: list[str | None],
) -> bytes:
    """يربط المعاملات بتنسيق نصي (format code 0) لكل المعاملات والنتائج."""
    body = _cstring(portal) + _cstring(statement)
    body += _I16.pack(0)  # لا أكواد تنسيق ⇒ الكل نصي
    body += _I16.pack(len(params))
    for value in params:
        if value is None:
            body += _I32.pack(-1)
        else:
            encoded = value.encode("utf-8")
            body += _I32.pack(len(encoded)) + encoded
    body += _I16.pack(0)  # نتائج نصية
    return _msg(b"B", body)


def describe_message(kind: bytes, name: str = "") -> bytes:
    return _msg(b"D", kind + _cstring(name))


def execute_message(portal: str = "", max_rows: int = 0) -> bytes:
    return _msg(b"E", _cstring(portal) + _I32.pack(max_rows))


def close_message(kind: bytes, name: str) -> bytes:
    return _msg(b"C", kind + _cstring(name))


def sync_message() -> bytes:
    return _msg(b"S", b"")


def flush_message() -> bytes:
    return _msg(b"H", b"")


def terminate_message() -> bytes:
    return _msg(b"X", b"")


# ------------------------------------------------------------------ قراءة ---

def read_cstring(buf: bytes, offset: int) -> tuple[str, int]:
    end = buf.index(b"\x00", offset)
    return buf[offset:end].decode("utf-8", errors="replace"), end + 1


def parse_error_fields(body: bytes) -> dict[str, str]:
    """يفك حقول ErrorResponse/NoticeResponse إلى قاموس code→value."""
    fields: dict[str, str] = {}
    offset = 0
    while offset < len(body):
        code = body[offset : offset + 1]
        if code == b"\x00":
            break
        offset += 1
        value, offset = read_cstring(body, offset)
        fields[code.decode("ascii")] = value
    return fields


def parse_row_description(body: bytes) -> list[tuple[str, int]]:
    """يعيد قائمة (اسم العمود، OID النوع)."""
    count = _I16.unpack_from(body, 0)[0]
    offset = 2
    columns: list[tuple[str, int]] = []
    for _ in range(count):
        name, offset = read_cstring(body, offset)
        # table_oid(4) column_attr(2) type_oid(4) type_size(2) type_mod(4) format(2)
        type_oid = _I32.unpack_from(body, offset + 6)[0]
        offset += 18
        columns.append((name, type_oid))
    return columns


def parse_data_row(body: bytes) -> Iterator[bytes | None]:
    count = _I16.unpack_from(body, 0)[0]
    offset = 2
    for _ in range(count):
        length = _I32.unpack_from(body, offset)[0]
        offset += 4
        if length == -1:
            yield None
        else:
            yield body[offset : offset + length]
            offset += length


def parse_notification(body: bytes) -> tuple[int, str, str]:
    pid = _I32.unpack_from(body, 0)[0]
    channel, offset = read_cstring(body, 4)
    payload, _ = read_cstring(body, offset)
    return pid, channel, payload
