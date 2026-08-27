"""تشفير المحتوى عند التخزين، وإدارة المفاتيح.

**لماذا هذا موجود:** صور إثبات التسليم ومستندات السلسلة الباردة بيانات صحية
تشغيلية حساسة (§29). تشفير القرص وحده لا يكفي حين يكون التخزين خدمة خارجية أو
نسخة احتياطية منقولة: الملف يخرج من نطاق القرص وهو مقروء.

**الخوارزمية:** AES-256-GCM. اختيرت لأنها تعطي السرية والسلامة معًا: أي تعديل
بايت واحد في الملف المشفَّر يجعل فك التشفير يفشل بدل أن يُخرج بيانات فاسدة.
التنفيذ من المكتبة القياسية عبر ``hashlib``/``hmac`` غير ممكن لـ GCM، لذلك:

* إن توفّرت ``cryptography`` تُستخدم AES-256-GCM (المسار المفضّل للإنتاج).
* وإلا يُستخدم مسار احتياطي **معلن**: ChaCha20-Poly1305 منفَّذ من الصفر
  بالمكتبة القياسية. الخوارزمية قياسية (RFC 8439) ومُختبرة هنا بمتجهات
  الاختبار الرسمية، لكن **التنفيذ اليدوي لأي بدائية تعمية يجب أن يُراجَع
  خارجيًا قبل الاعتماد في الإنتاج** — وهذا مذكور في تقرير التقدم لا مخفيًا.

**إدارة المفاتيح:** المفتاح يُقرأ من البيئة (تُملأ من خزنة أسرار في الإنتاج)
ولا يُكتب في الشيفرة ولا في قاعدة البيانات ولا يصل إلى المتصفح. يدعم التدوير:
كل ملف مشفَّر يحمل **معرّف المفتاح** الذي شُفّر به، فتبقى الملفات القديمة
قابلة للقراءة بعد إدخال مفتاح جديد.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct
from typing import Any

from .errors import DependencyUnavailable, ValidationError

#: بادئة تُوسم بها كل حمولة مشفَّرة — تجعل التمييز بين المشفَّر والخام قاطعًا
MAGIC = b"MSR1"
NONCE_BYTES = 12
TAG_BYTES = 16


# ============================================ ChaCha20-Poly1305 (RFC 8439) ==

def _rotate(value: int, count: int) -> int:
    return ((value << count) | (value >> (32 - count))) & 0xFFFFFFFF


def _chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    constants = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)
    state = list(constants)
    state += list(struct.unpack("<8I", key))
    state.append(counter)
    state += list(struct.unpack("<3I", nonce))
    working = list(state)

    def quarter(a: int, b: int, c: int, d: int) -> None:
        working[a] = (working[a] + working[b]) & 0xFFFFFFFF
        working[d] = _rotate(working[d] ^ working[a], 16)
        working[c] = (working[c] + working[d]) & 0xFFFFFFFF
        working[b] = _rotate(working[b] ^ working[c], 12)
        working[a] = (working[a] + working[b]) & 0xFFFFFFFF
        working[d] = _rotate(working[d] ^ working[a], 8)
        working[c] = (working[c] + working[d]) & 0xFFFFFFFF
        working[b] = _rotate(working[b] ^ working[c], 7)

    for _round in range(10):
        quarter(0, 4, 8, 12); quarter(1, 5, 9, 13)
        quarter(2, 6, 10, 14); quarter(3, 7, 11, 15)
        quarter(0, 5, 10, 15); quarter(1, 6, 11, 12)
        quarter(2, 7, 8, 13); quarter(3, 4, 9, 14)

    return struct.pack(
        "<16I", *[(working[i] + state[i]) & 0xFFFFFFFF for i in range(16)])


def _chacha20(key: bytes, counter: int, nonce: bytes, data: bytes) -> bytes:
    output = bytearray()
    for offset in range(0, len(data), 64):
        block = _chacha20_block(key, counter + offset // 64, nonce)
        chunk = data[offset:offset + 64]
        output.extend(byte ^ block[index] for index, byte in enumerate(chunk))
    return bytes(output)


def _poly1305(key: bytes, message: bytes) -> bytes:
    prime = (1 << 130) - 5
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:32], "little")
    accumulator = 0
    for offset in range(0, len(message), 16):
        chunk = message[offset:offset + 16]
        accumulator += int.from_bytes(chunk + b"\x01", "little")
        accumulator = (accumulator * r) % prime
    return ((accumulator + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _pad16(data: bytes) -> bytes:
    remainder = len(data) % 16
    return b"" if remainder == 0 else b"\x00" * (16 - remainder)


def _chacha_seal(key: bytes, nonce: bytes, plaintext: bytes,
                 associated: bytes) -> tuple[bytes, bytes]:
    poly_key = _chacha20_block(key, 0, nonce)[:32]
    ciphertext = _chacha20(key, 1, nonce, plaintext)
    mac_data = (associated + _pad16(associated) + ciphertext + _pad16(ciphertext)
                + struct.pack("<Q", len(associated))
                + struct.pack("<Q", len(ciphertext)))
    return ciphertext, _poly1305(poly_key, mac_data)


def _chacha_open(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes,
                 associated: bytes) -> bytes:
    poly_key = _chacha20_block(key, 0, nonce)[:32]
    mac_data = (associated + _pad16(associated) + ciphertext + _pad16(ciphertext)
                + struct.pack("<Q", len(associated))
                + struct.pack("<Q", len(ciphertext)))
    expected = _poly1305(poly_key, mac_data)
    # المقارنة بزمن ثابت: مقارنة عادية تسرّب معلومات عن التاق بالتوقيت
    if not hmac.compare_digest(expected, tag):
        raise ValidationError(
            "فشل التحقق من سلامة الملف المشفَّر — المحتوى مُعدَّل أو المفتاح خاطئ")
    return _chacha20(key, 1, nonce, ciphertext)


# ================================================== إدارة المفاتيح ==========

class KeyRing:
    """حلقة مفاتيح تدعم التدوير.

    الصيغة في البيئة: متغيّر ``MASAR_ENCRYPTION_KEYS`` يحمل أزواجًا مفصولة
    بفواصل، كل زوج «معرّف» ثم نقطتان ثم مفتاح base64 بطول ٣٢ بايت؛
    و``MASAR_ENCRYPTION_ACTIVE_KEY`` يسمّي المعرّف الفعّال. الملفات القديمة تبقى مقروءة بمفتاحها
    القديم بينما يُشفَّر الجديد بالمفتاح الفعّال — وهذا ما يجعل التدوير عملية
    تشغيلية بلا توقف، لا هجرة كبرى.
    """

    def __init__(self, keys: dict[str, bytes] | None = None,
                 active: str | None = None) -> None:
        self._keys = keys if keys is not None else self._load_from_env()
        self._active = active or os.environ.get("MASAR_ENCRYPTION_ACTIVE_KEY") or ""
        if self._keys and not self._active:
            self._active = sorted(self._keys)[-1]
        if self._active and self._active not in self._keys:
            raise ValidationError(
                f"المفتاح الفعّال «{self._active}» غير موجود في حلقة المفاتيح")

    @staticmethod
    def _load_from_env() -> dict[str, bytes]:
        import base64

        raw = os.environ.get("MASAR_ENCRYPTION_KEYS", "").strip()
        keys: dict[str, bytes] = {}
        for entry in filter(None, (part.strip() for part in raw.split(","))):
            if ":" not in entry:
                raise ValidationError(
                    "صيغة MASAR_ENCRYPTION_KEYS يجب أن تكون «معرّف:مفتاح_base64»")
            key_id, encoded = entry.split(":", 1)
            try:
                material = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise ValidationError(
                    f"مفتاح التشفير «{key_id}» ليس base64 صالحًا") from exc
            if len(material) != 32:
                raise ValidationError(
                    f"مفتاح التشفير «{key_id}» طوله {len(material)} بايت — "
                    "المطلوب ٣٢ بايت (٢٥٦ بت)")
            keys[key_id.strip()] = material
        return keys

    @property
    def enabled(self) -> bool:
        return bool(self._keys)

    @property
    def active_key_id(self) -> str:
        if not self._keys:
            raise DependencyUnavailable(
                "التشفير عند التخزين غير مفعّل: MASAR_ENCRYPTION_KEYS غير محدد")
        return self._active

    def material(self, key_id: str) -> bytes:
        try:
            return self._keys[key_id]
        except KeyError:
            raise DependencyUnavailable(
                f"مفتاح التشفير «{key_id}» غير متاح — لا يمكن فك تشفير هذا الملف. "
                "لا تحذف مفتاحًا قديمًا قبل إعادة تشفير ما شُفّر به."
            ) from None

    def key_ids(self) -> list[str]:
        return sorted(self._keys)


_keyring: KeyRing | None = None


def get_keyring(reload: bool = False) -> KeyRing:
    global _keyring
    if _keyring is None or reload:
        _keyring = KeyRing()
    return _keyring


def generate_key() -> str:
    """يولّد مفتاحًا جديدًا صالحًا للاستخدام مباشرة (base64)."""
    import base64

    return base64.b64encode(os.urandom(32)).decode("ascii")


# ================================================== الواجهة العامة ==========

#: البيئات الوحيدة التي يُسمح فيها بالتنفيذ الاحتياطي (ChaCha20-Poly1305
#: المكتوب هنا بالمكتبة القياسية). خارجها يُمنع منعًا باتًا.
FALLBACK_ALLOWED_ENVIRONMENTS = frozenset({"development", "test"})


def _use_library() -> Any | None:
    # مفتاح اختبار وحيد الغرض: محاكاة غياب المكتبة في **عملية فرعية**، حيث
    # لا يمكن ترقيع الدالة. بدونه لا سبيل لاختبار أن preflight يرفض الإنتاج
    # عند غياب cryptography — وهو أهم ما يجب إثباته هنا. المفتاح يُقرأ في
    # كل نداء، فلا يمكن أن يُنسى مضبوطًا من تشغيل سابق داخل العملية نفسها.
    if os.environ.get("MASAR_TEST_FORCE_NO_CRYPTOGRAPHY") == "1":
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM
    except Exception:
        return None


def library_available() -> bool:
    return _use_library() is not None


def _environment() -> str:
    from .config import get_config

    return get_config().environment


def fallback_permitted(environment: str | None = None) -> bool:
    """هل يُسمح بالتنفيذ الاحتياطي في هذه البيئة؟"""
    return (environment or _environment()) in FALLBACK_ALLOWED_ENVIRONMENTS


def assert_production_grade(environment: str | None = None) -> None:
    """يمنع أي مسار تشفير غير AES-256-GCM عبر ``cryptography`` خارج التطوير.

    **لماذا الرفض بدل الرجوع الصامت:** التنفيذ الاحتياطي هنا مكتوب يدويًا
    ومُتحقَّق من صحته مقابل متجهات RFC 8439 — لكن «صحيح رياضيًا» ليس
    «صالح للإنتاج». ينقصه ما لا يُثبَت باختبار وظيفي: مقاومة هجمات التوقيت
    والقنوات الجانبية، والمراجعة الخارجية، والتسريع العتادي. والأخطر أن
    الرجوع إليه **صامت**: يستمر النظام في العمل، وتُشفَّر بيانات صحية بتنفيذ
    لم يُراجَع، ولا يعلم أحد إلا عند التحقيق في حادثة.

    لذلك: في الإنتاج (وأي بيئة غير development/test) يفشل الإقلاع بصوت عالٍ.
    """
    environment = environment or _environment()
    if fallback_permitted(environment):
        return
    if not library_available():
        raise DependencyUnavailable(
            f"التشفير عند التخزين في بيئة «{environment}» يتطلب مكتبة "
            "cryptography (AES-256-GCM). المكتبة غير متاحة، والتنفيذ "
            "الاحتياطي (ChaCha20-Poly1305 محلي) مسموح في development و"
            "test فقط — لا يُسمح بالرجوع إليه هنا. "
            "ثبّتها: pip install 'cryptography>=42.0'",
            component="crypto", environment=environment,
        )


def encrypt(content: bytes, *, associated: bytes = b"") -> bytes:
    """يشفّر محتوى ويعيد حمولة موسومة تحمل معرّف المفتاح والخوارزمية.

    البنية: ``MAGIC | طول_المعرّف | المعرّف | الخوارزمية | nonce | tag | نص``
    """
    keyring = get_keyring()
    key_id = keyring.active_key_id
    key = keyring.material(key_id)
    nonce = os.urandom(NONCE_BYTES)

    # البوابة قبل اختيار الخوارزمية، لا بعده: الرجوع إلى الاحتياطي يجب أن
    # يكون مستحيلًا خارج development/test، لا مجرد غير مفضَّل.
    assert_production_grade()

    aesgcm = _use_library()
    if aesgcm is not None:
        algorithm = b"A"  # AES-256-GCM
        sealed = aesgcm(key).encrypt(nonce, content, associated)
        ciphertext, tag = sealed[:-TAG_BYTES], sealed[-TAG_BYTES:]
    else:
        algorithm = b"C"  # ChaCha20-Poly1305 — development/test حصرًا
        ciphertext, tag = _chacha_seal(key, nonce, content, associated)

    identifier = key_id.encode("utf-8")
    return (MAGIC + bytes([len(identifier)]) + identifier + algorithm
            + nonce + tag + ciphertext)


def decrypt(payload: bytes, *, associated: bytes = b"") -> bytes:
    """يفك تشفير حمولة موسومة. يرفع خطأً واضحًا إن عُدِّل المحتوى."""
    if not is_encrypted(payload):
        raise ValidationError("المحتوى ليس حمولة مشفَّرة بصيغة مسار")

    cursor = len(MAGIC)
    id_length = payload[cursor]
    cursor += 1
    key_id = payload[cursor:cursor + id_length].decode("utf-8")
    cursor += id_length
    algorithm = payload[cursor:cursor + 1]
    cursor += 1
    nonce = payload[cursor:cursor + NONCE_BYTES]
    cursor += NONCE_BYTES
    tag = payload[cursor:cursor + TAG_BYTES]
    cursor += TAG_BYTES
    ciphertext = payload[cursor:]

    key = get_keyring().material(key_id)
    if algorithm == b"A":
        aesgcm = _use_library()
        if aesgcm is None:
            raise DependencyUnavailable(
                "هذا الملف مشفَّر بـ AES-GCM ومكتبة cryptography غير مثبّتة")
        try:
            return aesgcm(key).decrypt(nonce, ciphertext + tag, associated)
        except Exception as exc:
            raise ValidationError(
                "فشل التحقق من سلامة الملف المشفَّر") from exc
    if algorithm == b"C":
        # حمولة مشفَّرة بالتنفيذ الاحتياطي. خارج development/test وجودها
        # شذوذ يستحق التوقف: إما أن بيانات تطوير سُرّبت إلى الإنتاج، أو أن
        # الإنتاج شُغّل يومًا بلا المكتبة. لا نمنع الاسترجاع منعًا نهائيًا —
        # ذلك يعني فقد بيانات — لكن نشترط قرارًا صريحًا مُسجَّلًا بدل
        # استرجاع صامت يُخفي أن المشكلة وقعت أصلًا.
        environment = _environment()
        if not fallback_permitted(environment):
            from .config import _env_bool

            if not _env_bool("MASAR_ALLOW_LEGACY_FALLBACK_DECRYPT", False):
                raise DependencyUnavailable(
                    f"هذا الملف مشفَّر بالتنفيذ الاحتياطي (ChaCha20-Poly1305) "
                    f"وبيئة التشغيل «{environment}». وجوده هنا يعني تسرّب "
                    "بيانات من بيئة تطوير أو تشغيل إنتاج بلا مكتبة معتمدة. "
                    "للاسترجاع ولمرة واحدة أثناء الترحيل اضبط "
                    "MASAR_ALLOW_LEGACY_FALLBACK_DECRYPT=true، ثم أعد تشفير "
                    "المحتوى بـ AES-256-GCM وأزل الضبط.",
                    component="crypto", environment=environment,
                )
        return _chacha_open(key, nonce, ciphertext, tag, associated)
    raise ValidationError(f"خوارزمية تشفير غير معروفة: {algorithm!r}")


def is_encrypted(payload: bytes) -> bool:
    return payload[:len(MAGIC)] == MAGIC


def status() -> dict[str, Any]:
    """حالة التشفير — تُعرض في شاشة التكاملات بصدق."""
    keyring = get_keyring()
    library = library_available()
    environment = _environment()
    fallback_ok = fallback_permitted(environment)
    # «معتمد للإنتاج» = AES-256-GCM عبر cryptography. لا شيء آخر يُحتسب.
    production_grade = library
    blocked = not production_grade and not fallback_ok

    if blocked:
        message = (
            f"التشفير عند التخزين **معطَّل في بيئة «{environment}»**: مكتبة "
            "cryptography غير متاحة، والتنفيذ الاحتياطي مسموح في development "
            "و test فقط. الخدمة لن تعمل حتى تُثبَّت المكتبة."
        )
    elif not keyring.enabled:
        message = ("التشفير عند التخزين **غير مفعّل** — المستندات تُحفظ كما هي. "
                   "اضبط MASAR_ENCRYPTION_KEYS لتفعيله.")
    elif production_grade:
        message = "التشفير عند التخزين مفعّل — AES-256-GCM عبر cryptography"
    else:
        message = (f"التشفير عند التخزين مفعّل بالتنفيذ الاحتياطي "
                   f"(ChaCha20-Poly1305) — مقبول في «{environment}» فقط")

    return {
        "enabled": keyring.enabled,
        "algorithm": "AES-256-GCM" if library else "ChaCha20-Poly1305",
        "implementation": "cryptography" if library else "stdlib-native",
        "environment": environment,
        "library_available": library,
        "fallback_permitted": fallback_ok,
        #: الحقل الحاسم: هل هذا التشفير صالح للاعتماد في الإنتاج؟
        "production_grade": production_grade,
        "blocked": blocked,
        "key_count": len(keyring.key_ids()),
        "active_key_id": keyring.active_key_id if keyring.enabled else None,
        "message_ar": message,
        "review_note_ar": (
            None if library else
            "تنفيذ ChaCha20-Poly1305 محلي بالمكتبة القياسية — صحيح مقابل "
            "متجهات RFC 8439، لكنه غير مُراجَع خارجيًا وغير مقاوم مُثبَتًا "
            "لهجمات التوقيت. لا يُعتمد خارج development/test."
        ),
    }


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
