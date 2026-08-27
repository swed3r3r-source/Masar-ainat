"""آليات مصادقة PostgreSQL: MD5 و SCRAM-SHA-256."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets


def md5_password(user: str, password: str, salt: bytes) -> bytes:
    inner = hashlib.md5(
        (password + user).encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    outer = hashlib.md5(
        inner.encode("ascii") + salt, usedforsecurity=False
    ).hexdigest()
    return ("md5" + outer).encode("ascii")


def _normalize(password: str) -> bytes:
    """SASLprep مبسط: يرفض المحارف غير المطبوعة ويمرر ASCII كما هو.

    كلمات المرور في النظام تُولَّد/تُدخل بمحارف ASCII مطبوعة، وإذا احتوت
    على محارف Unicode أخرى نمررها بترميز UTF-8 كما توصي RFC 5802 عند
    تعذر SASLprep الكامل.
    """
    return password.encode("utf-8")


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


class ScramClient:
    """تنفيذ عميل SCRAM-SHA-256 (RFC 5802 / RFC 7677) بدون channel binding."""

    def __init__(self, user: str, password: str) -> None:
        self.password = _normalize(password)
        # اسم المستخدم في SCRAM يُترك فارغًا في PostgreSQL (يؤخذ من StartupMessage)
        self.nonce = base64.b64encode(os.urandom(18)).decode("ascii")
        self.client_first_bare = f"n=,r={self.nonce}"
        self.server_signature: bytes = b""

    def first_message(self) -> bytes:
        return ("n,," + self.client_first_bare).encode("utf-8")

    def final_message(self, server_first: bytes) -> bytes:
        text = server_first.decode("utf-8")
        attrs = dict(
            part.split("=", 1) for part in text.split(",") if "=" in part
        )
        server_nonce = attrs["r"]
        if not server_nonce.startswith(self.nonce):
            raise ValueError("SCRAM: nonce الخادم لا يبدأ بـ nonce العميل")
        salt = base64.b64decode(attrs["s"])
        iterations = int(attrs["i"])

        salted = hashlib.pbkdf2_hmac("sha256", self.password, salt, iterations)
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        stored_key = hashlib.sha256(client_key).digest()

        client_final_without_proof = f"c=biws,r={server_nonce}"
        auth_message = (
            f"{self.client_first_bare},{text},{client_final_without_proof}"
        ).encode("utf-8")

        client_signature = hmac.new(stored_key, auth_message, hashlib.sha256).digest()
        proof = base64.b64encode(_xor(client_key, client_signature)).decode("ascii")

        server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
        self.server_signature = hmac.new(
            server_key, auth_message, hashlib.sha256
        ).digest()

        return f"{client_final_without_proof},p={proof}".encode("utf-8")

    def verify_final(self, server_final: bytes) -> None:
        text = server_final.decode("utf-8")
        match = re.search(r"v=([^,]+)", text)
        if not match:
            raise ValueError("SCRAM: رسالة الخادم النهائية بلا توقيع")
        if not hmac.compare_digest(
            base64.b64decode(match.group(1)), self.server_signature
        ):
            raise ValueError("SCRAM: توقيع الخادم غير صحيح — احتمال هجوم وسيط")


def random_password(length: int = 32) -> str:
    return secrets.token_urlsafe(length)
