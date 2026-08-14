"""C3 DURABLE regression tests: restart-control secret path stability + delete_file denylist.

WHY these tests exist:
  The restart-control secret was deleted from .orchestrator_cache/ by the write-pipeline
  delete_file tool, permanently breaking restart_orchestrator_via_bat with
  RESTART_CONTROL_SECRET_UNAVAILABLE. The durable fix moves the secret to
  .orchestrator_lifecycle/ and denies deletion of control artifacts.

These tests encode the invariants that prevent recurrence:
  1. The canonical secret path is OUTSIDE .orchestrator_cache/.
  2. delete_file refuses to delete the secret (CONTROL_FILE_DELETE_DENIED).
  3. Normal unrelated .orchestrator_cache/ deletions still work.
  4. load_control_secret create-once semantics are preserved.
  5. Legacy migration moves the secret to the canonical path.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from orchestrator.lifecycle.restart_protocol import (
    RESTART_LIFECYCLE_DIR_NAME,
    canonical_secret_path,
    is_protected_control_path,
    load_control_secret,
    migrate_legacy_secret,
)


class CanonicalPathTests(unittest.TestCase):
    """The secret must live outside .orchestrator_cache/."""

    def test_canonical_path_is_in_lifecycle_dir(self):
        root = Path("/fake/project")
        result = canonical_secret_path(root)
        self.assertIn(RESTART_LIFECYCLE_DIR_NAME, str(result))
        self.assertEqual(result.name, "restart_control.secret")
        self.assertNotIn(".orchestrator_cache", str(result))

    def test_canonical_path_not_in_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = canonical_secret_path(tmp)
            self.assertNotIn(".orchestrator_cache", str(result))
            self.assertIn(RESTART_LIFECYCLE_DIR_NAME, str(result))


class ProtectedPathTests(unittest.TestCase):
    """is_protected_control_path must identify all control artifacts."""

    def test_canonical_secret_is_protected(self):
        self.assertTrue(is_protected_control_path("/project/.orchestrator_lifecycle/restart_control.secret"))

    def test_canonical_secret_windows_path_is_protected(self):
        self.assertTrue(is_protected_control_path(r"D:\project\.orchestrator_lifecycle\restart_control.secret"))

    def test_legacy_secret_is_protected(self):
        self.assertTrue(is_protected_control_path("/project/.orchestrator_cache/restart_control.secret"))

    def test_legacy_secret_windows_path_is_protected(self):
        self.assertTrue(is_protected_control_path(r"D:\project\.orchestrator_cache\restart_control.secret"))

    def test_normal_cache_file_is_not_protected(self):
        self.assertFalse(is_protected_control_path("/project/.orchestrator_cache/INDEX.json"))
        self.assertFalse(is_protected_control_path("/project/.orchestrator_cache/restart_busy_snapshot.json"))

    def test_normal_file_is_not_protected(self):
        self.assertFalse(is_protected_control_path("/project/some_file.py"))
        self.assertFalse(is_protected_control_path("/project/.orchestrator_cache/helper.cmd"))

    def test_empty_path_is_not_protected(self):
        self.assertFalse(is_protected_control_path(""))


class MigrationTests(unittest.TestCase):
    """migrate_legacy_secret must move the secret from cache to lifecycle dir."""

    def test_migration_moves_secret_to_canonical_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_dir = root / ".orchestrator_cache"
            legacy_dir.mkdir(parents=True)
            legacy_secret = legacy_dir / "restart_control.secret"
            original_bytes = b"A" * 32
            legacy_secret.write_bytes(original_bytes)

            result = migrate_legacy_secret(root)
            self.assertIsNotNone(result)
            self.assertEqual(result, canonical_secret_path(root))
            self.assertTrue(result.exists())
            self.assertEqual(result.read_bytes(), original_bytes)
            self.assertFalse(legacy_secret.exists())

    def test_migration_noop_when_canonical_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = canonical_secret_path(root)
            canonical.parent.mkdir(parents=True)
            canonical.write_bytes(b"B" * 32)

            result = migrate_legacy_secret(root)
            self.assertEqual(result, canonical)
            self.assertEqual(canonical.read_bytes(), b"B" * 32)

    def test_migration_creates_fresh_when_nothing_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = migrate_legacy_secret(root)
            self.assertIsNotNone(result)
            self.assertEqual(result, canonical_secret_path(root))
            # File not yet created (load_control_secret will create it)
            self.assertFalse(result.exists())


class DeleteFileDenylistTests(unittest.TestCase):
    """delete_file must refuse to delete control artifacts."""

    def _run_delete(self, file_path, project_path=""):
        from orchestrator.tools.write.core import delete_file
        return json.loads(asyncio.run(delete_file(file_path, project_path)))

    def test_delete_canonical_secret_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret_path = canonical_secret_path(root)
            secret_path.parent.mkdir(parents=True)
            secret_path.write_bytes(b"X" * 32)

            result = self._run_delete(str(secret_path))
            self.assertFalse(result.get("ok", False))
            self.assertEqual(result.get("error_name"), "CONTROL_FILE_DELETE_DENIED")
            # File must still exist
            self.assertTrue(secret_path.exists())

    def test_delete_legacy_secret_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_path = root / ".orchestrator_cache" / "restart_control.secret"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_bytes(b"Y" * 32)

            result = self._run_delete(str(legacy_path))
            self.assertFalse(result.get("ok", False))
            self.assertEqual(result.get("error_name"), "CONTROL_FILE_DELETE_DENIED")
            self.assertTrue(legacy_path.exists())

    def _make_project_root(self, tmp: str) -> Path:
        # Place a package.json marker so code_tools.find_project_root resolves to
        # THIS small temp dir instead of walking up to the (huge) user profile and
        # triggering a pathological 30s index build. Keeps the success path fast
        # while exercising the real resolution chain.
        root = Path(tmp)
        (root / "package.json").write_text("{}", encoding="utf-8")
        return root

    def test_delete_normal_cache_file_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_project_root(tmp)
            cache_file = root / ".orchestrator_cache" / "some_temp_file.json"
            cache_file.parent.mkdir(parents=True)
            cache_file.write_text("{}", encoding="utf-8")

            result = self._run_delete(str(cache_file))
            self.assertTrue(result.get("ok"))
            self.assertFalse(cache_file.exists())

    def test_delete_normal_file_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_project_root(tmp)
            normal_file = root / "some_module.py"
            normal_file.write_text("# hello", encoding="utf-8")

            result = self._run_delete(str(normal_file))
            self.assertTrue(result.get("ok"))
            self.assertFalse(normal_file.exists())


class LoadControlSecretCreateOnceTests(unittest.TestCase):
    """load_control_secret must preserve create-once semantics at the new path."""

    def test_create_once_and_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = canonical_secret_path(tmp)
            with mock.patch(
                "orchestrator.lifecycle.restart_protocol._restrict_secret",
                return_value=(True, None),
            ):
                first = load_control_secret(path)
                second = load_control_secret(path)
            self.assertTrue(first.usable)
            self.assertTrue(second.usable)
            self.assertEqual(first.secret, second.secret)
            self.assertEqual(len(first.secret), 32)

    def test_short_secret_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = canonical_secret_path(tmp)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"short")
            with mock.patch(
                "orchestrator.lifecycle.restart_protocol._restrict_secret",
                return_value=(True, None),
            ):
                status = load_control_secret(path)
            self.assertFalse(status.usable)
            self.assertIsNone(status.secret)


if __name__ == "__main__":
    unittest.main()
