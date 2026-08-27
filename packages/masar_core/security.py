"""المصادقة: تجزئة كلمات المرور، رموز JWT، الرموز العشوائية، تحديد المعدل."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from .config import get_config
from .errors import Unauthorized


# ================================================ تجزئة كلمات المرور ========

class PasswordHasher:
    """واجهة تجزئة كلمات المرور.

    التطبيق الحالي ``scrypt`` من المكتبة القياسية (RFC 7914) لأن Argon2 غير
    متوفر في هذه البيئة. الترقية إلى Argon2id تتم بإضافة صنف يحقق نفس الواجهة
    وتسجيله في ``HASHERS``؛ التجزئات القديمة تبقى صالحة للتحقق وتُرقّى تلقائيًا
    عند أول تسجيل دخول ناجح (``needs_rehash``).
    """

    prefix = "scrypt"

    def hash(self, password: str) -> str:
        cfg = get_config().security
        salt = secrets.token_bytes(16)
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=cfg.scrypt_n,
            r=cfg.scrypt_r,
            p=cfg.scrypt_p,
            dklen=32,
            maxmem=cfg.scrypt_n * cfg.scrypt_r * 256,
        )
        return "$".join([
            self.prefix,
            str(cfg.scrypt_n), str(cfg.scrypt_r), str(cfg.scrypt_p),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(derived).decode("ascii"),
        ])

    def verify(self, password: str, stored: str) -> bool:
        try:
            prefix, n, r, p, salt_b64, hash_b64 = stored.split("$")
            if prefix != self.prefix:
                return False
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(hash_b64)
            n_i, r_i, p_i = int(n), int(r), int(p)
            derived = hashlib.scrypt(
                password.encode("utf-8"), salt=salt, n=n_i, r=r_i, p=p_i,
                dklen=len(expected), maxmem=n_i * r_i * 256,
            )
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(derived, expected)

    def needs_rehash(self, stored: str) -> bool:
        cfg = get_config().security
        try:
            prefix, n, r, p, _, _ = stored.split("$")
        except ValueError:
            return True
        return (
            prefix != self.prefix
            or int(n) != cfg.scrypt_n
            or int(r) != cfg.scrypt_r
            or int(p) != cfg.scrypt_p
        )


password_hasher = PasswordHasher()

#: تجزئة وهمية تُستخدم لمساواة زمن الاستجابة عند عدم وجود المستخدم
#: (منع كشف وجود الحسابات عبر تحليل التوقيت)
_DUMMY_HASH = password_hasher.hash("masar-timing-equalizer")


def verify_password_constant_time(password: str, stored: str | None) -> bool:
    if stored is None:
        password_hasher.verify(password, _DUMMY_HASH)
        return False
    return password_hasher.verify(password, stored)


# =========================================================== رموز JWT =======

def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def encode_jwt(payload: dict[str, Any], *, secret: str | None = None) -> str:
    cfg = get_config().security
    secret = secret or cfg.jwt_secret
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"


def decode_jwt(token: str, *, secret: str | None = None, verify_exp: bool = True) -> dict[str, Any]:
    cfg = get_config().security
    secret = secret or cfg.jwt_secret
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError:
        raise Unauthorized("رمز الدخول غير صالح") from None

    try:
        header = json.loads(_b64url_decode(header_b64))
    except Exception:
        raise Unauthorized("رمز الدخول غير صالح") from None
    if header.get("alg") != "HS256":
        # منع هجوم alg=none / تبديل الخوارزمية
        raise Unauthorized("خوارزمية توقيع غير مقبولة")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        provided = _b64url_decode(signature_b64)
    except Exception:
        raise Unauthorized("رمز الدخول غير صالح") from None
    if not hmac.compare_digest(expected, provided):
        raise Unauthorized("توقيع رمز الدخول غير صحيح")

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        raise Unauthorized("محتوى رمز الدخول غير صالح") from None

    if verify_exp:
        exp = payload.get("exp")
        if exp is not None and time.time() > float(exp):
            raise Unauthorized("انتهت صلاحية الجلسة")
    return payload


@dataclass(slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int


def issue_tokens(
    *,
    user_id: str,
    role: str,
    session_id: str,
    extra_claims: dict[str, Any] | None = None,
) -> TokenPair:
    cfg = get_config().security
    now = int(time.time())
    access_ttl = cfg.access_token_minutes * 60
    refresh_ttl = cfg.refresh_token_days * 86400
    base = {
        "sub": user_id,
        "role": role,
        "sid": session_id,
        "iat": now,
        "iss": "masar-ainat",
    }
    if extra_claims:
        base.update(extra_claims)
    access = encode_jwt({**base, "typ": "access", "exp": now + access_ttl})
    refresh = encode_jwt(
        {"sub": user_id, "sid": session_id, "iat": now, "iss": "masar-ainat",
         "typ": "refresh", "exp": now + refresh_ttl}
    )
    return TokenPair(access, refresh, access_ttl, refresh_ttl)


# ================================================ رموز عشوائية وتجزئة =======

def new_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def hash_token(token: str) -> str:
    """تجزئة رموز التحديث/الاستعادة قبل تخزينها (لا تُخزَّن نصًا صريحًا)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ========================================================= تحديد المعدل =====

class RateLimiter:
    """محدد معدل بنافذة منزلقة داخل العملية.

    كافٍ لنشر بعملية واحدة. للنشر متعدد العمليات يُستبدل بمحدد مشترك
    (Redis/PostgreSQL) عبر نفس الواجهة ``allow()``.
    """

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        now = time.monotonic()
        bucket = self._hits.setdefault(key, [])
        cutoff = now - window_seconds
        # تنظيف كسول
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)

    def retry_after(self, key: str, window_seconds: float = 60.0) -> int:
        bucket = self._hits.get(key)
        if not bucket:
            return 0
        return max(0, int(window_seconds - (time.monotonic() - bucket[0])) + 1)


rate_limiter = RateLimiter()
