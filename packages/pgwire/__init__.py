"""pgwire — عميل بروتوكول PostgreSQL v3 نقي بلغة بايثون.

سبب الوجود
----------
بيئة التطوير الحالية لا تملك وصولًا إلى مستودعات الحزم (PyPI/npm محجوبة)،
لذلك لا يتوفر ``psycopg``. هذه الوحدة تنفذ بروتوكول الاتصال الرسمي
(PostgreSQL Frontend/Backend Protocol v3.0) مباشرةً حتى يعمل النظام على
قاعدة بيانات PostgreSQL حقيقية بدل اللجوء إلى SQLite أو بيانات وهمية.

في الإنتاج يمكن استبدالها بـ ``psycopg3`` عبر نفس الواجهة
(:class:`Connection.execute` / :class:`Pool`) دون تغيير في طبقة التطبيق —
انظر ``docs/02-architecture.md`` قسم «قابلية الاستبدال».

المدعوم
-------
* المصادقة: trust، cleartext، MD5، SCRAM-SHA-256 (channel binding غير مطلوب).
* الاستعلام البسيط (Simple Query) والاستعلام الممتد (Extended Query) مع
  معاملات مربوطة (Bound Parameters) — أي حماية كاملة من حقن SQL.
* المعاملات (Transactions) والنقاط المحفوظة (Savepoints).
* LISTEN/NOTIFY للتحديثات الفورية.
* تجميع الاتصالات (Connection Pool) آمن للخيوط.
* SSL/TLS عبر SSLRequest.
"""

from .exceptions import (
    PgError,
    PgIntegrityError,
    PgOperationalError,
    PgProgrammingError,
    UniqueViolation,
    ForeignKeyViolation,
    CheckViolation,
    InsufficientPrivilege,
    InvalidTextRepresentation,
    RaisedException,
)
from .connection import Connection, connect
from .pool import Pool
from .types import Json, Jsonb, SqlLiteral

__all__ = [
    "Connection",
    "connect",
    "Pool",
    "Json",
    "Jsonb",
    "SqlLiteral",
    "PgError",
    "PgIntegrityError",
    "PgOperationalError",
    "PgProgrammingError",
    "UniqueViolation",
    "ForeignKeyViolation",
    "CheckViolation",
    "InsufficientPrivilege",
    "RaisedException",
]
