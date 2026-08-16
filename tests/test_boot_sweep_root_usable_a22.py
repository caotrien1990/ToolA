# pyright: reportOptionalMemberAccess=false, reportArgumentType=false
"""A22 layer 2: _boot_sweep_root_usable must reject UNREADABLE roots, not just
missing ones.

Defect: the check only tested exists()/is_dir()/skip-name. A root that exists but
cannot be read (permission denied, dead network share, damaged ACL) passed the
usability gate and then exploded later in the sweep when INDEX.json under it was
stat'd. On Windows exists()/is_dir() return True for a read-denied directory, and
os.access(R_OK) is unreliable (also returns True), so the only honest probe is an
actual directory read.

These tests pin the BEHAVIOUR ("can this root be swept without exploding?") in
BOTH directions: a readable root is still accepted, an unreadable root is now
rejected. They must not depend on the implementation shape of the probe.
"""
import contextlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orchestrator.tools.write.core import (
    _BOOT_SWEEP_SKIP_DIR_NAMES,
    _boot_sweep_project_roots,
    _boot_sweep_root_usable,
)


def _make_unreadable(path: Path) -> bool:
    """Deny list-directory to the current user via icacls. Return True only if
    the denial actually makes os.scandir raise (running as admin bypasses ACLs)."""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if not user:
        return False
    try:
        subprocess.run(
            ["icacls", str(path), "/deny", f"{user}:(RD)"],
            capture_output=True, text=True, check=False,
        )
    except Exception:
        return False
    try:
        with os.scandir(path) as it:
            next(it, None)
        return False  # denial did not take effect (likely admin) -> not usable as a probe
    except PermissionError:
        return True
    except Exception:
        return False


def _restore(path: Path) -> None:
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    try:
        subprocess.run(
            ["icacls", str(path), "/remove:d", user],
            capture_output=True, text=True, check=False,
        )
    except Exception:
        pass


class TestBootSweepRootUsableA22(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="a22_usable_"))

    def tearDown(self):
        _restore(self.tempdir / "denied")
        shutil.rmtree(self.tempdir, ignore_errors=True)

    # --- direction 1: healthy roots are STILL accepted ---
    def test_readable_directory_is_accepted(self):
        good = self.tempdir / "good"
        good.mkdir()
        (good / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.assertTrue(_boot_sweep_root_usable(good))

    def test_empty_readable_directory_is_accepted(self):
        # An empty (but readable) root must not be rejected just for being empty.
        empty = self.tempdir / "empty"
        empty.mkdir()
        self.assertTrue(_boot_sweep_root_usable(empty))

    # --- direction 2: unhealthy roots are rejected ---
    def test_missing_directory_is_rejected(self):
        self.assertFalse(_boot_sweep_root_usable(self.tempdir / "does_not_exist"))

    def test_file_is_rejected(self):
        f = self.tempdir / "file.py"
        f.write_text("x = 1\n", encoding="utf-8")
        self.assertFalse(_boot_sweep_root_usable(f))

    def test_skip_named_directory_is_rejected(self):
        skip_name = next(iter(_BOOT_SWEEP_SKIP_DIR_NAMES))
        d = self.tempdir / skip_name
        d.mkdir()
        self.assertFalse(_boot_sweep_root_usable(d))

    def test_unreadable_directory_is_rejected(self):
        """The core layer-2 property: exists()+is_dir() True but cannot be read."""
        denied = self.tempdir / "denied"
        denied.mkdir()
        (denied / "keep.py").write_text("x = 1\n", encoding="utf-8")
        if not _make_unreadable(denied):
            self.skipTest("could not create a genuinely read-denied dir on this host")
        # Sanity: it really does look present-but-unreadable (the trap condition).
        self.assertTrue(denied.exists())
        self.assertTrue(denied.is_dir())
        self.assertFalse(_boot_sweep_root_usable(denied))

    # --- end-to-end: an unreadable SESSION root never reaches the sweep ---
    def test_unreadable_session_root_is_filtered_out_of_project_roots(self):
        """Roots discovered from sessions.db go through the usability gate. A
        read-denied one must be dropped before the sweep can stat under it.

        (Note: explicit extra_roots passed by a caller intentionally bypass the
        gate; the session-history path is the one that carries junk temp dirs, so
        that is the branch under test here.)"""
        denied = self.tempdir / "denied"
        denied.mkdir()
        (denied / "keep.py").write_text("x = 1\n", encoding="utf-8")
        good = self.tempdir / "good"
        good.mkdir()
        (good / "a.py").write_text("x = 1\n", encoding="utf-8")
        if not _make_unreadable(denied):
            self.skipTest("could not create a genuinely read-denied dir on this host")

        # Fake sessions.db returning both roots as session history rows.
        import code_tools as ct

        class _FakeConn:
            def execute(self, *_a, **_k):
                class _Cur:
                    def fetchall(_self):
                        return [(str(good),), (str(denied),)]
                return _Cur()

        @contextlib.contextmanager
        def _fake_db():
            yield _FakeConn()

        with mock.patch.object(ct, "_with_sessions_db", _fake_db):
            roots = _boot_sweep_project_roots()
        keys = {os.path.normcase(str(r)) for r in roots}
        self.assertIn(os.path.normcase(str(good)), keys)
        self.assertNotIn(os.path.normcase(str(denied)), keys)


# ---------------------------------------------------------------------------
# Added during independent verification (ClauLTN). The tests above provoke the
# unreadable case with an ACL denial, which raises PermissionError -- so
# narrowing the probe's `except Exception` to `except PermissionError` left the
# whole boot-sweep suite green. That narrowing is exactly the wrong direction:
# the docstring names "dead network share" as a target case, and a dead share
# raises OSError/TimeoutError (WinError 53/59/1231), never PermissionError. An
# unreachable share would then pass the gate and the sweep would hang or blow up
# on it later -- the very failure layer 2 exists to prevent. Pin the whole
# exception class, not just the one the local ACL test happens to raise.
# ---------------------------------------------------------------------------
class TestBootSweepRootUsableNonPermissionErrors(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="a22_nonperm_"))
        self.root = self.tempdir / "root"
        self.root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _rejects_when_scandir_raises(self, exc):
        # sanity: without the failure this root IS usable, so the assertion
        # below can only be about the raised error.
        self.assertTrue(_boot_sweep_root_usable(self.root))
        with mock.patch("os.scandir", side_effect=exc):
            self.assertFalse(_boot_sweep_root_usable(self.root),
                             f"root accepted despite {type(exc).__name__}")

    def test_dead_network_share_oserror_is_rejected(self):
        # WinError 53 -- the network path was not found.
        self._rejects_when_scandir_raises(OSError(53, "network path not found"))

    def test_timeout_is_rejected(self):
        self._rejects_when_scandir_raises(TimeoutError("share did not respond"))

    def test_permission_error_is_still_rejected(self):
        # the direction the ACL test already covers, restated without ACLs so
        # it holds on hosts where the icacls denial cannot be created.
        self._rejects_when_scandir_raises(PermissionError(5, "access denied"))


if __name__ == "__main__":
    unittest.main()
