"""Verifica backup recuperável e preservação de configurações sem dados reais."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ops.deploy_importacao import backup_sqlite, checksum, set_revision
from ops.verify_integrity import compare


class DeploySafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database = self.root / "source.sqlite3"
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executescript(
                "CREATE TABLE django_migrations(id INTEGER PRIMARY KEY);"
                "CREATE TABLE questoes_questao(id INTEGER PRIMARY KEY, texto TEXT);"
                "INSERT INTO questoes_questao VALUES(1, 'original');"
                "CREATE TABLE pre_auditoria_decisao(id INTEGER PRIMARY KEY, status TEXT);"
                "INSERT INTO pre_auditoria_decisao VALUES(1, 'APROVADA');"
            )

    def test_backup_can_be_opened_and_source_is_unchanged(self):
        before = checksum(self.database)
        target = self.root / "backup.sqlite3"
        digest = backup_sqlite(self.database, target)
        self.assertEqual(digest, checksum(target))
        self.assertEqual(before, checksum(self.database))
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertTrue(compare(target, self.database)["questoes_questao"]["existing_preserved"])

    def test_missing_database_does_not_create_empty_source(self):
        absent = self.root / "missing.sqlite3"
        with self.assertRaises(RuntimeError):
            backup_sqlite(absent, self.root / "backup.sqlite3")
        self.assertFalse(absent.exists())

    def test_existing_backup_is_never_overwritten(self):
        target = self.root / "backup.sqlite3"
        target.write_bytes(b"preservar")
        with self.assertRaises(FileExistsError):
            backup_sqlite(self.database, target)
        self.assertEqual(target.read_bytes(), b"preservar")

    def test_wrong_database_and_broken_foreign_keys_abort(self):
        other = self.root / "other.sqlite3"
        with closing(sqlite3.connect(other)) as connection, connection:
            connection.execute("CREATE TABLE another(id INTEGER PRIMARY KEY)")
        with self.assertRaises(RuntimeError):
            backup_sqlite(other, self.root / "wrong-backup.sqlite3")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executescript(
                "CREATE TABLE child(id INTEGER REFERENCES questoes_questao(id));INSERT INTO child VALUES(99);"
            )
        with self.assertRaises(RuntimeError):
            backup_sqlite(self.database, self.root / "broken-backup.sqlite3")

    def test_audit_histories_must_also_remain_unchanged(self):
        target = self.root / "backup.sqlite3"
        backup_sqlite(self.database, target)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE pre_auditoria_decisao SET status='REPROVADA'")
        with self.assertRaisesRegex(AssertionError, "pre_auditoria_decisao"):
            compare(target, self.database)

    def test_release_revision_preserves_other_private_settings(self):
        env = self.root / ".env"
        env.write_text("# configuração\nDJANGO_SECRET_KEY=teste-isolado\nexport RELEASE_REVISION =old\n")
        set_revision(env, "a" * 40)
        self.assertEqual(
            env.read_text(),
            "# configuração\nDJANGO_SECRET_KEY=teste-isolado\nRELEASE_REVISION=" + "a" * 40 + "\n",
        )
        self.assertEqual(env.stat().st_mode & 0o777, 0o600)

    def test_missing_revision_is_added_and_ambiguous_revision_aborts(self):
        env = self.root / ".env"
        env.write_text("SETTING=value")
        set_revision(env, "b" * 40)
        self.assertEqual(env.read_text(), "SETTING=value\nRELEASE_REVISION=" + "b" * 40 + "\n")
        original = "RELEASE_REVISION=one\nRELEASE_REVISION=two\n"
        env.write_text(original)
        with self.assertRaises(RuntimeError):
            set_revision(env, "b" * 40)
        self.assertEqual(env.read_text(), original)


if __name__ == "__main__":
    unittest.main()
