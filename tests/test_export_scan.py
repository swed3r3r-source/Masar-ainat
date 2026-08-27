"""فحص أسرار التصدير — يُختبر بوضع أسرار حقيقية فيه، لا بقراءته.

ماسح أسرار غير مُختبَر هو أسوأ من لا ماسح: يعطي طمأنينة بلا أساس. لذلك كل
اختبار هنا **يزرع سرًّا فعليًا** في ملف يدخل الأرشيف، ويتحقق أن البناء يتوقف.

المبدأ الحاكم: **لا استثناء على مستوى الملف.** الاستثناء بالملف («تجاهل
مجلد docs») هو الثغرة نفسها — يكفي أن يُكتب سر هناك مرة. الاستثناء الوحيد
المسموح على مستوى **القيمة**: نائب معلن من قائمة محددة.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import make_export  # noqa: E402


class TestSecretPatterns(unittest.TestCase):
    """فحص وحدة سريع لكل نمط — يسبق الاختبار البطيء الكامل."""

    def assert_blocked(self, text: str, *, name: str = "example.md") -> None:
        findings = make_export.scan_text(name, text)
        self.assertTrue(findings, f"مرّ بلا اعتراض: {text[:70]}")

    def assert_clean(self, text: str, *, name: str = "example.md") -> None:
        findings = make_export.scan_text(name, text)
        self.assertEqual(findings, [], f"اعتراض في غير محله: {findings}")

    # ملاحظة بنيوية: **لا سر حرفي في هذا الملف.** الماسح لا يستثني ملفًا،
    # وهذا الملف يدخل الأرشيف كغيره — فلو كُتبت العيّنات حرفيًا لأوقفت
    # البناء بنفسها. لذلك تُركَّب من أجزاء وقت التشغيل: الاختبار يبقى
    # حقيقيًا (سر مُركَّب كامل يمر على الماسح)، والملف يبقى نظيفًا.
    @staticmethod
    def assign(key: str, value: str) -> str:
        return f"{key}={value}"

    def test_real_credentials_are_blocked(self) -> None:
        # أسماء محايدة عمدًا: متغيّر اسمه ``password`` بقيمة حرفية
        # يطابق النمط نفسه الذي نختبره.
        db_value = "hunter" + "2Winter#2026"
        jwt_value = "Xq7pLm2vRt9w" + "Zs4yKn6bHd1gFj8cAe3u"
        key_material = "k1:" + "c2VjcmV0a2V5bWF0ZXJpYWxoZXJl"
        cases = {
            "كلمة مرور قاعدة بيانات": self.assign("MASAR_DB_" + "PASSWORD", db_value),
            "سر JWT": self.assign("MASAR_JWT_" + "SECRET", jwt_value),
            "مفتاح تشفير": self.assign("MASAR_ENCRYPTION_" + "KEYS", key_material),
            "كلمة مرور في كود": "pass" + 'word: "Sup3rS3cret!"',
            "مفتاح API": "api_" + "key = 'ak_live_9f8e7d6c5b4a3210'",
            "مفتاح خاص": "-----BEGIN RSA " + "PRIVATE KEY-----",
            "مفتاح AWS": "AKIA" + "IOSFODNN7EXAMPLE",
            "رمز GitHub": "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
            "رمز JWT كامل": ("eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
                             + ".dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
            "رابط اتصال بكلمة مرور":
                "postgresql://masar_app:" + "realpassword123" + "@db:5432/masar",
        }
        for label, text in cases.items():
            with self.subTest(label):
                self.assert_blocked(text)

    def test_declared_placeholders_pass(self) -> None:
        for text in (
            self.assign("MASAR_DB_" + "PASSWORD", "CHANGE_ME"),
            self.assign("MASAR_JWT_" + "SECRET", "GENERATE_AT_DEPLOYMENT"),
            self.assign("MASAR_ENCRYPTION_" + "KEYS", "dev1:GENERATE_AT_DEPLOYMENT"),
            self.assign("POSTGRES_" + "PASSWORD", "CHANGE_ME_DB"),
            "api_" + 'key: "EXAMPLE_ONLY"',
            # توسيعة بيئة: السر يعيش في البيئة لا في الملف
            self.assign("MASAR_DB_" + "PASSWORD", '"${POSTGRES_PASSWORD}"'),
            self.assign("MASAR_SEED_" + "PASSWORD", "$(generate_password)"),
        ):
            with self.subTest(text):
                self.assert_clean(text)

    def test_placeholder_must_match_whole_value(self) -> None:
        """احتواء النائب لا يكفي — وإلا صار «CHANGE_ME» تعويذة تمرير."""
        for text in (
            self.assign("MASAR_DB_" + "PASSWORD",
                        "CHANGE_ME" + "_but_here_is_the_real_one_x9"),
            "pass" + 'word: "prefix' + 'CHANGE_ME"',
        ):
            with self.subTest(text):
                self.assert_blocked(text)

    def test_no_file_is_exempt_from_scanning(self) -> None:
        """المسارات التي كانت مستثناة سابقًا تُفحص الآن كغيرها تمامًا."""
        planted = self.assign("MASAR_JWT_" + "SECRET",
                              "Zx9Wv8Ut7Sr6" + "Qp5On4Ml3Kj2Ih1Gf0Ed")
        for name in ("docs/01-requirements-analysis.md", "README.md",
                     "STATUS.md", ".env.example",
                     "deploy/env.production.example",
                     "tests/test_security.py", "db/schema.sql"):
            with self.subTest(name):
                self.assert_blocked(planted, name=name)

    def test_this_test_file_itself_is_clean(self) -> None:
        """الملف الذي يفحص الماسح يجب أن يمر على الماسح — بلا استثناء."""
        text = Path(__file__).read_text(encoding="utf-8")
        self.assertEqual(
            make_export.scan_text("tests/test_export_scan.py", text), [],
            "ملف اختبار الماسح يحوي سرًّا حرفيًا — ركّبه من أجزاء وقت التشغيل")


class TestExportBuildBlocksSecrets(unittest.TestCase):
    """الاختبار الكامل: يزرع سرًّا في نسخة من المشروع ويشغّل الباني فعلًا."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="masar-export-test-"))
        cls.project = cls.tmp / "project"
        # نسخة خفيفة تكفي الباني: الملفات التي يجمعها فقط
        cls.project.mkdir()
        for name in make_export.INCLUDE_FILES:
            source = ROOT / name
            if source.is_file():
                shutil.copy2(source, cls.project / name)
        for directory in make_export.INCLUDE_DIRS:
            source = ROOT / directory
            if source.is_dir():
                shutil.copytree(
                    source, cls.project / directory,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "data"))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def build(self) -> subprocess.CompletedProcess:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(self.project / "packages")
        return subprocess.run(
            [sys.executable, str(self.project / "scripts" / "make_export.py")],
            capture_output=True, text=True, env=environment, timeout=300)

    def test_01_clean_project_exports_successfully(self) -> None:
        result = self.build()
        self.assertEqual(result.returncode, 0,
                         f"فشل تصدير مشروع نظيف:\n{result.stdout[-1500:]}")
        self.assertIn("SHA-256", result.stdout)
        self.assertTrue((self.project / "var" / "export" / "masar-ainat.zip").is_file())

    def test_02_secret_planted_in_docs_blocks_the_build(self) -> None:
        target = self.project / "docs" / "01-requirements-analysis.md"
        original = target.read_text(encoding="utf-8")
        target.write_text(
            original + "\n\nمثال إعداد:\n\n    "
            + "MASAR_JWT_" + "SECRET=" + "Qw8Er7Ty6Ui5" + "Op4As3Df2Gh1Jk0Lz9Xc" + "\n",
            encoding="utf-8")
        try:
            result = self.build()
            self.assertEqual(result.returncode, 1,
                             f"سر في docs مرّ بلا اعتراض:\n{result.stdout[-1500:]}")
            self.assertIn("01-requirements-analysis.md", result.stdout)
        finally:
            target.write_text(original, encoding="utf-8")

    def test_03_secret_planted_in_env_example_blocks_the_build(self) -> None:
        target = self.project / ".env.example"
        original = target.read_text(encoding="utf-8")
        target.write_text(
            original.replace("MASAR_DB_" + "PASSWORD=CHANGE_ME",
                             "MASAR_DB_" + "PASSWORD=" + "Pr0duct10n#Pass!"),
            encoding="utf-8")
        try:
            result = self.build()
            self.assertEqual(result.returncode, 1,
                             f"سر في .env.example مرّ بلا اعتراض:\n{result.stdout[-1500:]}")
            self.assertIn(".env.example", result.stdout)
        finally:
            target.write_text(original, encoding="utf-8")

    def test_04_connection_string_with_credentials_blocks_the_build(self) -> None:
        target = self.project / "README.md"
        original = target.read_text(encoding="utf-8")
        target.write_text(
            original + "\n\n    postgresql://masar_app:"
            + "LiveDbPass2026" + "@db:5432/masar\n",
            encoding="utf-8")
        try:
            result = self.build()
            self.assertEqual(result.returncode, 1,
                             f"رابط اتصال ببيانات اعتماد مرّ:\n{result.stdout[-1500:]}")
            self.assertIn("README.md", result.stdout)
        finally:
            target.write_text(original, encoding="utf-8")

    def test_05_project_is_clean_again_after_removals(self) -> None:
        """يثبت أن الفشل أعلاه سببه السر المزروع لا خلل دائم في النسخة."""
        result = self.build()
        self.assertEqual(result.returncode, 0,
                         f"النسخة لم تعد نظيفة:\n{result.stdout[-1500:]}")


if __name__ == "__main__":
    unittest.main()
