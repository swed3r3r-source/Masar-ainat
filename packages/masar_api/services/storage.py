"""تخزين المستندات والصور خلف محوّل قابل للاستبدال (محلي / S3 متوافق)."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Any, Protocol

from masar_core import crypto
from masar_core.config import get_config
from masar_core.errors import DependencyUnavailable, ValidationError

#: تواقيع الملفات المسموحة — يُتحقق من المحتوى لا من الامتداد وحده (§29)
MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"%PDF-", "application/pdf"),
)


def detect_content_type(content: bytes, declared: str | None = None) -> str:
    """يكشف نوع الملف من محتواه. يرفض التعارض مع النوع المعلن."""
    detected: str | None = None
    for signature, mime in MAGIC_SIGNATURES:
        if content.startswith(signature):
            detected = mime
            break
    if detected is None and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        detected = "image/webp"

    if detected is None:
        raise ValidationError(
            "نوع الملف غير مدعوم — المسموح: صور JPEG/PNG/WebP أو مستند PDF"
        )
    if declared and declared.split(";")[0].strip() not in (detected, "application/octet-stream"):
        raise ValidationError(
            f"محتوى الملف ({detected}) لا يطابق النوع المعلن ({declared}) — رُفض الرفع"
        )
    return detected


def validate_upload(content: bytes, declared_type: str | None, *, kind: str = "document") -> str:
    cfg = get_config().security
    if not content:
        raise ValidationError("الملف فارغ")
    if len(content) > cfg.max_upload_bytes:
        raise ValidationError(
            f"حجم الملف {len(content) / 1048576:.1f} ميغابايت يتجاوز الحد "
            f"{cfg.max_upload_bytes / 1048576:.0f} ميغابايت"
        )
    content_type = detect_content_type(content, declared_type)
    if content_type not in cfg.allowed_document_types:
        raise ValidationError(f"نوع الملف {content_type} غير مسموح")
    return content_type


_SAFE_KEY = re.compile(r"^[A-Za-z0-9/_.-]{1,300}$")


class ObjectStore(Protocol):
    name: str

    def put(self, key: str, content: bytes, content_type: str) -> str: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class LocalFileStore:
    """تخزين على نظام الملفات — للتطوير والاختبار فقط (يُمنع في الإنتاج)."""

    name = "local"

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or get_config().storage.local_path)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not _SAFE_KEY.match(key) or ".." in key:
            raise ValidationError("مفتاح تخزين غير صالح")
        path = (self.root / key).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValidationError("مسار تخزين خارج المجلد المسموح")
        return path

    def put(self, key: str, content: bytes, content_type: str) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(content)
        os.replace(temporary, path)
        return key

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise DependencyUnavailable("الملف غير موجود في التخزين")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3CompatibleStore:
    """تخزين متوافق مع S3 (MinIO أو مزوّد داخل المملكة).

    ⚠️ **غير مفعّل في هذه البيئة**: تنفيذ توقيع AWS SigV4 يتطلب مكتبة عميل
    (``boto3``) لم يكن ممكنًا تثبيتها هنا. الواجهة جاهزة والتبديل يتم
    بضبط ``MASAR_STORAGE_BACKEND=s3`` بعد تثبيت المكتبة وتنفيذ الجسم.
    """

    name = "s3"

    def __init__(self) -> None:
        cfg = get_config().storage
        if not all((cfg.s3_endpoint, cfg.s3_bucket, cfg.s3_access_key, cfg.s3_secret_key)):
            raise DependencyUnavailable(
                "إعدادات التخزين المتوافق مع S3 غير مكتملة"
            )
        raise DependencyUnavailable(
            "محوّل التخزين S3 غير منفَّذ في هذه البيئة (تعذر تثبيت boto3). "
            "المحوّل المفعّل والمختبَر هو local. راجع docs/08-security.md."
        )

    def put(self, key: str, content: bytes, content_type: str) -> str: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class EncryptedStore:
    """غلاف يشفّر المحتوى قبل كتابته ويفك تشفيره بعد قراءته.

    التغليف بدل التعديل داخل كل محوّل مقصود: التشفير مسؤولية واحدة تُطبَّق على
    أي تخزين خلفي (محلي أو S3) بلا تكرار، ويمكن إطفاؤها بإعداد واحد.

    **التوافق مع الملفات القديمة:** ما كُتب قبل تفعيل التشفير يُقرأ كما هو —
    ``decrypt`` لا يُستدعى إلا على حمولة تحمل الوسم. هذا يجعل التفعيل عملية
    تدريجية بلا هجرة إجبارية، وإن كان إعادة تشفير القديم يبقى موصى به.
    """

    def __init__(self, inner: "ObjectStore") -> None:
        self.inner = inner
        self.name = f"{inner.name}+encrypted"

    def put(self, key: str, content: bytes, content_type: str) -> str:
        # مفتاح التخزين يدخل في البيانات المُصادَق عليها: نقل ملف مشفَّر إلى
        # مفتاح آخر يجعل فك تشفيره يفشل بدل أن يُقرأ في سياق خاطئ.
        return self.inner.put(
            key, crypto.encrypt(content, associated=key.encode("utf-8")),
            content_type)

    def get(self, key: str) -> bytes:
        payload = self.inner.get(key)
        if not crypto.is_encrypted(payload):
            return payload
        return crypto.decrypt(payload, associated=key.encode("utf-8"))

    def delete(self, key: str) -> None:
        self.inner.delete(key)

    def exists(self, key: str) -> bool:
        return self.inner.exists(key)


_store: ObjectStore | None = None


def get_store(reload: bool = False) -> ObjectStore:
    global _store
    if _store is None or reload:
        backend = get_config().storage.backend.lower()
        base: ObjectStore = S3CompatibleStore() if backend == "s3" else LocalFileStore()
        _store = EncryptedStore(base) if crypto.get_keyring(reload).enabled else base
    return _store


def storage_status() -> dict[str, Any]:
    """حالة التخزين والتشفير — تُعلن بصدق ما إذا كانت الملفات مشفَّرة."""
    store = get_store()
    encryption = crypto.status()
    return {
        "backend": store.name,
        "encrypted_at_rest": isinstance(store, EncryptedStore),
        "encryption": encryption,
    }


def build_key(prefix: str, original_name: str, content_type: str) -> str:
    extension = {
        "image/jpeg": "jpg", "image/png": "png",
        "image/webp": "webp", "application/pdf": "pdf",
    }.get(content_type, "bin")
    import datetime as dt

    today = dt.datetime.now(dt.timezone.utc)
    return f"{prefix}/{today:%Y/%m/%d}/{uuid.uuid4().hex}.{extension}"


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
