"""Real SQLite/WAL and filesystem tests; no app imports or production data."""

from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/migration_bundle.py"
SPEC = importlib.util.spec_from_file_location("migration_bundle", SCRIPT)
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


class MigrationBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.destination = self.root / "bundle"
        self.data = self.source / "data"
        self.data.mkdir()
        self.database = self.data / "stock_analysis.db"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE notes (body TEXT)")
            connection.execute("INSERT INTO notes VALUES ('before migration')")
            connection.commit()
        for relative, content in {
            "data/attachments/report.pdf": b"private attachment",
            "data/transcripts/call.txt": b"full transcript",
            "data/tasks/task.json": b'{"state":"completed"}',
            "reports/daily.md": b"report",
            "strategies/custom.yaml": b"name: retained",
            "cloud-config/.env": b"API_KEY=fake-cloud-test-secret",
            "cloud-config/profile.json": b'{"token":"fake-profile-test-secret"}',
            ".env": b"API_KEY=fake-root-test-secret",
            "tyc-config/auth.json": b'{"token":"fake-tyc-test-secret"}',
            "longbridge_tokens/token": b"fake-longbridge-test-secret",
            "logs/excluded.log": b"do not ship",
            "backups/old.db": b"do not ship",
            "data/backups/old.txt": b"do not ship",
            "node_modules/pkg/file.js": b"do not ship",
            ".git/config": b"do not ship",
            "data/custom/.env": b"API_KEY=fake-nested-test-secret",
            "data/.session_secret": b"fake-session-test-secret",
            "data/.admin_password_hash": b"fake-admin-test-password-hash",
            "data/.user_auth_secret": b"fake-user-auth-test-secret",
            "data/.llm_usage_hmac_secret": b"fake-hmac-test-secret",
        }.items():
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        with closing(sqlite3.connect(self.data / "jobs.sqlite3")) as connection:
            connection.execute("CREATE TABLE jobs (id INTEGER)")
            connection.execute("INSERT INTO jobs VALUES (42)")
            connection.commit()

    def tearDown(self):
        # Only test-created paths; restore permissions so TemporaryDirectory can clean up.
        for path in self.source.rglob("*"):
            if not path.is_symlink():
                path.chmod(0o700 if path.is_dir() else 0o600)
        self.temporary.cleanup()

    def export(self, **kwargs):
        return bundle.export(self.source, self.destination, writers_stopped=True, **kwargs)

    def manifest(self):
        return json.loads((self.destination / bundle.MANIFEST_PATH).read_text())

    def rewrite_manifest(self, manifest):
        (self.destination / bundle.MANIFEST_PATH).write_text(json.dumps(manifest))

    def source_state(self):
        return {path.relative_to(self.source).as_posix():
                (path.stat().st_mode, path.stat().st_size, path.stat().st_mtime_ns,
                 hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
                for path in self.source.rglob("*")}

    def test_plan_is_stat_only_and_does_not_create_destination(self):
        before = self.source_state()
        with mock.patch.object(Path, "open", side_effect=AssertionError("plan must not read files")):
            result = bundle.plan(self.source, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertFalse(result["writes_performed"])
        self.assertFalse(result["production_ready"])
        self.assertGreater(result["estimated_required_bytes"], result["selected_bytes"])
        self.assertIn("External storage coverage is unverified", " ".join(result["warnings"]))
        self.assertEqual(before, self.source_state())

    def test_export_preserves_entire_data_and_excludes_secrets_by_default(self):
        before = self.source_state()
        result = self.export()
        self.assertTrue(result["integrity_ok"])
        self.assertEqual(2, result["sqlite_count"])
        self.assertEqual(before, self.source_state())
        for relative in ("attachments/report.pdf", "transcripts/call.txt", "tasks/task.json"):
            self.assertEqual((self.data / relative).read_bytes(),
                             (self.destination / "runtime/data" / relative).read_bytes())
        with closing(sqlite3.connect(self.destination / "runtime/data/jobs.sqlite3")) as connection:
            self.assertEqual([(42,)], connection.execute("SELECT id FROM jobs").fetchall())
        manifest = self.manifest()
        self.assertFalse(manifest["production_ready"])
        for record in manifest["files"]:
            self.assertNotIn("fake-", json.dumps(record))
            self.assertFalse(record["path"].endswith(bundle.SIDECAR_SUFFIXES))
        self.assertFalse((self.destination / "runtime/.env").exists())
        self.assertFalse((self.destination / "runtime/config/.env").exists())
        self.assertFalse((self.destination / "runtime/data/custom/.env").exists())
        for name in bundle.AUTH_SECRET_NAMES:
            self.assertFalse((self.destination / "runtime/data" / name).exists())
        self.assertFalse((self.destination / "runtime/data/backups").exists())
        self.assertEqual([], list((self.destination / "runtime/logs").iterdir()))
        for path in (self.destination, *self.destination.rglob("*")):
            self.assertEqual(0o700 if path.is_dir() else 0o600, stat.S_IMODE(path.stat().st_mode))

    def test_include_secrets_requires_opt_in_and_manifest_never_contains_values(self):
        self.export(include_secrets=True)
        for original, target in ((".env", "runtime/.env"),
                                 ("cloud-config/.env", "runtime/config/.env"),
                                 ("tyc-config/auth.json", "runtime/tyc/auth.json"),
                                 ("longbridge_tokens/token", "runtime/longbridge_tokens/token")):
            self.assertEqual((self.source / original).read_bytes(),
                             (self.destination / target).read_bytes())
        self.assertNotIn("fake-", (self.destination / bundle.MANIFEST_PATH).read_text())
        self.assertFalse(bundle.verify(self.destination)["production_ready"])
        for name in bundle.AUTH_SECRET_NAMES:
            self.assertEqual((self.data / name).read_bytes(),
                             (self.destination / "runtime/data" / name).read_bytes())

    def test_missing_persistent_env_is_explicit_even_with_secrets_enabled(self):
        (self.source / "cloud-config/.env").unlink()
        self.export(include_secrets=True)
        statuses = {item["source"]: item["status"] for item in self.manifest()["secrets"]}
        self.assertEqual("missing", statuses["cloud-config/.env"])
        self.assertIn("not sufficient to go live", " ".join(self.manifest()["warnings"]))

    def test_export_requires_stopped_writers_and_new_nonoverlapping_destination(self):
        with self.assertRaisesRegex(bundle.BundleError, "--writers-stopped"):
            bundle.export(self.source, self.destination)
        self.assertFalse(self.destination.exists())
        for destination in (self.source, self.source / "nested", self.root):
            with self.subTest(destination=destination):
                with self.assertRaisesRegex(bundle.BundleError, "overlap"):
                    bundle.export(self.source, destination, writers_stopped=True)
        self.destination.mkdir()
        (self.destination / "keep").write_text("untouched")
        with self.assertRaisesRegex(bundle.BundleError, "entirely new"):
            self.export()
        self.assertEqual("untouched", (self.destination / "keep").read_text())

    def test_source_symlink_is_rejected_without_following_it(self):
        (self.data / "outside").symlink_to(self.root)
        with self.assertRaisesRegex(bundle.BundleError, "Symlinks"):
            self.export()
        self.assertFalse(self.destination.exists())

    def test_source_hardlink_is_copied_into_an_independent_private_file(self):
        source_file = self.data / "attachments/report.pdf"
        os.link(source_file, self.data / "linked-report.pdf")
        before = self.source_state()
        self.export()
        snapshot_file = self.destination / "runtime/data/linked-report.pdf"
        self.assertFalse(os.path.samefile(source_file, snapshot_file))
        self.assertEqual(1, snapshot_file.stat().st_nlink)
        self.assertEqual(before, self.source_state())
        snapshot_file.write_bytes(b"output-only change")
        self.assertEqual(b"private attachment", source_file.read_bytes())

    def test_destination_symlink_ancestor_is_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(bundle.BundleError, "Symlink"):
            bundle.export(self.source, alias / "new", writers_stopped=True)

    def test_writable_wal_is_refused_without_modifying_source(self):
        with closing(sqlite3.connect(self.database)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO notes VALUES ('WAL-only committed row')")
            writer.commit()
            before = self.source_state()
            self.assertGreater(Path(str(self.database) + "-wal").stat().st_size, 0)
            with self.assertRaisesRegex(bundle.BundleError, "read-only source snapshot"):
                self.export()
            self.assertEqual(before, self.source_state())
            self.assertFalse((self.destination / bundle.MANIFEST_PATH).exists())

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root bypasses the temporary readonly fixture permissions")
    def test_real_readonly_wal_backup_includes_uncheckpointed_commit(self):
        protected_paths = [self.database, Path(str(self.database) + "-wal"),
                           Path(str(self.database) + "-shm")]
        try:
            # A crashed/terminated process leaves committed WAL frames without
            # keeping a writable SQLite SHM mapping alive in the test process.
            subprocess.run([sys.executable, "-c", """
import os, sqlite3, sys
writer = sqlite3.connect(sys.argv[1])
writer.execute('PRAGMA journal_mode=WAL')
writer.execute('PRAGMA wal_autocheckpoint=0')
writer.execute("INSERT INTO notes VALUES ('WAL-only committed row')")
writer.commit()
os._exit(0)
""", str(self.database)], check=True, timeout=10)
            with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro&immutable=1", uri=True)) as raw:
                self.assertNotIn(("WAL-only committed row",), raw.execute("SELECT body FROM notes").fetchall())
            for path in protected_paths:
                path.chmod(0o400)
            self.data.chmod(0o500)
            before = self.source_state()
            self.export()
            self.assertEqual(before, self.source_state())
            snapshot = self.destination / "runtime/data/stock_analysis.db"
            with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True)) as result:
                self.assertIn(("WAL-only committed row",), result.execute("SELECT body FROM notes").fetchall())
                self.assertEqual(("delete",), result.execute("PRAGMA journal_mode").fetchone())
            self.assertFalse(Path(str(snapshot) + "-wal").exists())
            self.assertFalse(Path(str(snapshot) + "-shm").exists())
        finally:
            self.data.chmod(0o700)
            for path in protected_paths:
                if path.exists():
                    path.chmod(0o600)

    def test_cleanly_closed_wal_database_does_not_create_source_sidecars(self):
        with closing(sqlite3.connect(self.database)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("INSERT INTO notes VALUES ('cleanly stopped')")
            writer.commit()
        self.assertFalse(Path(str(self.database) + "-wal").exists())
        before = self.source_state()
        self.export()
        self.assertEqual(before, self.source_state())

    def test_sqlite_header_identifies_nonstandard_and_extensionless_databases(self):
        for name in ("archive.cache", "without_extension", "other.DB"):
            with closing(sqlite3.connect(self.data / name)) as connection:
                connection.execute("CREATE TABLE marker (value INTEGER)")
                connection.execute("INSERT INTO marker VALUES (7)")
                connection.commit()
        before = self.source_state()
        result = self.export()
        self.assertEqual(5, result["sqlite_count"])
        declarations = {item["path"]: item["sqlite"] for item in self.manifest()["files"]}
        for name in ("archive.cache", "without_extension", "other.DB"):
            self.assertTrue(declarations[f"runtime/data/{name}"])
            output = self.destination / "runtime/data" / name
            with closing(sqlite3.connect(output.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
                self.assertEqual([(7,)], connection.execute("SELECT value FROM marker").fetchall())
        self.assertEqual(before, self.source_state())

    def test_verify_is_readonly_and_rejects_unlisted_symlink(self):
        self.export()

        def state():
            return {path.relative_to(self.destination).as_posix():
                    (path.stat().st_mode, path.stat().st_mtime_ns,
                     hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
                    for path in self.destination.rglob("*")}

        before = state()
        bundle.verify(self.destination)
        self.assertEqual(before, state())
        (self.destination / "runtime/unknown-link").symlink_to(self.source)
        with self.assertRaisesRegex(bundle.BundleError, "Symlinks"):
            bundle.verify(self.destination)

    def test_manifest_rejects_traversal_duplicate_paths_and_absolute_paths(self):
        self.export()
        original = self.manifest()
        for field in ("path", "source"):
            for value in ("../escape", "/tmp/escape", "runtime/../escape", "C:/escape", "runtime\\escape"):
                with self.subTest(field=field, path=value):
                    manifest = json.loads(json.dumps(original))
                    manifest["files"][0][field] = value
                    self.rewrite_manifest(manifest)
                    with self.assertRaisesRegex(bundle.BundleError, "path"):
                        bundle.verify(self.destination)
        original["files"].append(original["files"][0])
        self.rewrite_manifest(original)
        with self.assertRaisesRegex(bundle.BundleError, "Duplicate"):
            bundle.verify(self.destination)

    def test_verify_rejects_tampering_and_unlisted_files(self):
        self.export()
        attachment = self.destination / "runtime/data/attachments/report.pdf"
        attachment.write_bytes(b"tampered")
        with self.assertRaisesRegex(bundle.BundleError, "SHA256 mismatch"):
            bundle.verify(self.destination)
        attachment.write_bytes(b"private attachment")
        unlisted = self.destination / "runtime/unlisted"
        unlisted.write_bytes(b"extra")
        unlisted.chmod(0o600)
        with self.assertRaisesRegex(bundle.BundleError, "Undeclared"):
            bundle.verify(self.destination)

    def test_verify_rejects_symlink_and_hardlink(self):
        self.export()
        attachment = self.destination / "runtime/data/attachments/report.pdf"
        attachment.unlink()
        attachment.symlink_to(self.data / "attachments/report.pdf")
        with self.assertRaisesRegex(bundle.BundleError, "Symlinks"):
            bundle.verify(self.destination)
        attachment.unlink()
        os.link(self.data / "attachments/report.pdf", attachment)
        with self.assertRaisesRegex(bundle.BundleError, "hardlinks"):
            bundle.verify(self.destination)

    def test_corrupt_sqlite_fails_even_when_manifest_hash_is_updated(self):
        self.export()
        database = self.destination / "runtime/data/stock_analysis.db"
        database.write_bytes(bundle.SQLITE_HEADER + b"broken database")
        manifest = self.manifest()
        record = next(item for item in manifest["files"] if item["path"] == "runtime/data/stock_analysis.db")
        record["size"] = database.stat().st_size
        record["sha256"] = hashlib.sha256(database.read_bytes()).hexdigest()
        self.rewrite_manifest(manifest)
        with self.assertRaises((bundle.BundleError, sqlite3.DatabaseError)):
            bundle.verify(self.destination)

    def test_low_space_fails_before_destination_creation(self):
        usage = shutil.disk_usage(self.root)
        with mock.patch.object(shutil, "disk_usage", return_value=type(usage)(usage.total, usage.used, 0)):
            with self.assertRaisesRegex(bundle.BundleError, "Insufficient"):
                self.export()
        self.assertFalse(self.destination.exists())

    def test_cli_plan_export_and_verify(self):
        for arguments in (
            ["plan", "--source", str(self.source), "--destination", str(self.destination)],
            ["export", "--source", str(self.source), "--destination", str(self.destination), "--writers-stopped"],
            ["verify", "--bundle", str(self.destination)],
        ):
            result = subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True,
                                    text=True, timeout=15, check=False)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(json.loads(result.stdout)["production_ready"])


if __name__ == "__main__":
    unittest.main()
