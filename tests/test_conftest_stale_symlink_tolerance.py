"""A dead symlink in pytest's own temp root must not take the session down.

Measured 2026-08-20: every ``pytest tests/`` on this box aborted during COLLECTION
with ``PermissionError: [WinError 5]`` on
``%LOCALAPPDATA%\\Temp\\pytest-of-ADMIN\\pytest-current`` -- a symlink left behind by
a killed run three days earlier. The raise comes from pytest's own temp-dir garbage
collector (``_pytest.pathlib.cleanup_dead_symlinks``), which calls ``path.exists()``
in a bare loop; on Windows an orphaned symlink can be readable-but-not-stat-able,
and ``Path.exists()`` propagates EACCES instead of returning False.

Worth pinning rather than leaving as a one-line shim, for two reasons:

* The failure is in COLLECTION, so it takes out the whole suite, not one test.
  There is no smaller signal -- nothing else in the run gets far enough to report.
* It was misdiagnosed once already, expensively. The path lives under
  ``%LOCALAPPDATA%\\Temp``, which was read as "OneDrive is syncing the temp dir", so
  every pytest invocation grew ``--basetemp=D:\\w7tmp``. That moved all test I/O onto
  a HDD and saturated it hard enough that unrelated folders looked locked from
  Explorer. ``%LOCALAPPDATA%`` is never OneDrive-redirected; the symlink was always
  the whole cause. Deleting it is a one-off the next killed run undoes, so the fix
  is in the loop -- and this file is what keeps it there.

The last test therefore also asserts the corollary: no test in this repo may pass
``--basetemp``, because doing so is what hid the real bug.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _patched():
    from _pytest import pathlib as pytest_pathlib
    return pytest_pathlib.cleanup_dead_symlinks


def test_the_shim_is_installed() -> None:
    """conftest replaces the upstream function; if not, everything below is moot."""
    assert _patched().__name__ == "_cleanup_dead_symlinks_tolerantly", (
        "conftest's cleanup_dead_symlinks shim is not installed; a stale "
        "pytest-current symlink will abort collection again")


def test_an_unstatable_symlink_does_not_raise(tmp_path, monkeypatch) -> None:
    """The exact production shape: one entry whose exists() raises EACCES.

    Simulated by monkeypatching ``Path.exists`` rather than by manufacturing a real
    unreadable symlink, because the ACL needed to reproduce it genuinely requires
    elevation -- and the property under test is about how the loop REACTS to the
    error, not about how Windows produces it.
    """
    victim = tmp_path / "pytest-current"
    victim.mkdir()

    real_exists = Path.exists

    def _exploding_exists(self, *args, **kwargs):
        if self.name == "pytest-current":
            raise PermissionError(13, "Access is denied")
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", _exploding_exists)
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "pytest-current")

    # No exception is the assertion. Before the fix this raised PermissionError and
    # the enclosing pytest session died.
    _patched()(tmp_path)


def test_a_live_directory_is_left_alone(tmp_path) -> None:
    """The shim must not become a way to delete real temp dirs.

    ``cleanup_dead_symlinks`` only ever removes symlinks; a fix that fell back to
    unlinking whatever it could not stat would delete live per-run directories and
    turn a collection error into data loss inside other agents' test runs.
    """
    keeper = tmp_path / "pytest-133"
    keeper.mkdir()
    (keeper / "artifact.txt").write_text("live run", encoding="utf-8")

    _patched()(tmp_path)

    assert keeper.is_dir(), "the shim deleted a live numbered temp dir"
    assert (keeper / "artifact.txt").read_text(encoding="utf-8") == "live run"


def test_garbage_collection_still_happens(tmp_path) -> None:
    """Swallowing the error must not mean skipping the work.

    The naive fix -- one try/except around the original call -- stops collecting
    garbage at the FIRST unreadable entry, so a single bad symlink would leak every
    dead link behind it forever. The fallback loop is per-entry for that reason, so
    a dead symlink sitting AFTER the poisoned one still gets removed.
    """
    (tmp_path / "target").mkdir()
    dead = tmp_path / "dead-link"
    try:
        dead.symlink_to(tmp_path / "never-existed", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation needs privileges not held here")

    _patched()(tmp_path)

    assert not dead.exists() and not dead.is_symlink(), (
        "a dead symlink survived the cleanup, so garbage collection stopped working")


def test_no_basetemp_in_this_repo_points_at_a_fixed_drive_path() -> None:
    """The corollary, stated as the property that actually matters.

    ``--basetemp`` itself is fine and used deliberately in four places, all of which
    resolve through ``tempfile.gettempdir()`` or ``%TEMP%`` -- i.e. SSD, per-run,
    cleaned by the OS. What caused the damage was a HARDCODED path on ``D:``: that
    drive is a HDD, and routing a 2400-test suite's temp I/O onto it saturated the
    spindle badly enough that unrelated folders stopped opening from Explorer.
    (Measured: ``du -sh`` on a sibling directory hung past 120s during the run and
    completed in 1s once pytest stopped.)

    So the assertion is about the TARGET, not the flag. A fixed absolute path is also
    shared mutable state between concurrent runs, which is the same reasoning
    test_graph_enabled_live_flag_a13 already recorded when it stopped using an
    in-repo child basetemp.
    """
    suspicious = []
    for root, dirs, files in os.walk(_REPO):
        dirs[:] = [d for d in dirs
                   if d not in {".git", "venv312", "node_modules", "__pycache__",
                                ".orchestrator_cache", "research", ".council"}]
        for name in files:
            if not name.endswith((".py", ".bat", ".ps1", ".cfg", ".ini", ".toml")):
                continue
            path = Path(root) / name
            if path.resolve() == Path(__file__).resolve():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in re.finditer(r"--basetemp[=\s]+[\"']?([^\s\"',\]]+)", text):
                target = match.group(1)
                # A drive-letter literal is the failure mode; %TEMP%, $env:TEMP,
                # {child_tmp} and f-string placeholders all resolve at runtime.
                if re.match(r"^[A-Za-z]:", target):
                    suspicious.append("%s -> %s" % (path.relative_to(_REPO), target))
    assert not suspicious, (
        "these pass a hardcoded drive path to --basetemp; it must resolve through "
        "the system TEMP (D: is a HDD and a fixed path is shared between runs): %r"
        % suspicious)
