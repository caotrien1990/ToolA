"""W5: `ignore_cleanup_errors=True` must actually ignore cleanup errors.

Not a hypothetical. `test_task_epoch_cross_process` passed every assertion and
then died in tearDown on `durable_audit.db-shm` (WinError 5) during a full-suite
run, while passing 3/3 in isolation -- Windows had not yet released the WAL
sidecar of the server child the test had just terminated. The stdlib flag that
was supposed to cover exactly this does not: in CPython 3.12 the `onexc` handler
guards `_resetperms` with `except FileNotFoundError` only, so a `PermissionError`
raised by `os.chmod` itself escapes `cleanup()`.

These tests pin the two halves of the conftest fix: a transient block is retried
until it clears, and a permanent one is swallowed only when the caller asked for
that. A test that did NOT pass the flag must still raise.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest


def _blocking(target_name: str, fail_times: list[int]):
    """os.chmod/os.unlink replacements that deny `target_name` N times.

    fail_times is a one-element list so the closure can decrement it.
    """
    real_chmod, real_unlink = os.chmod, os.unlink

    def _hit(path) -> bool:
        if not str(path).endswith(target_name):
            return False
        if fail_times[0] <= 0:
            return False
        fail_times[0] -= 1
        return True

    def chmod(path, *a, **k):
        if _hit(path):
            raise PermissionError(5, "Access is denied")
        return real_chmod(path, *a, **k)

    def unlink(path, *a, **k):
        if _hit(path):
            raise PermissionError(5, "Access is denied")
        return real_unlink(path, *a, **k)

    return chmod, unlink


def _tmpdir_with_locked_file(flag: bool, name: str = "durable_audit.db-shm"):
    t = tempfile.TemporaryDirectory(ignore_cleanup_errors=flag)
    (Path(t.name) / name).write_text("x", encoding="utf-8")
    return t, name


def test_the_stdlib_flag_alone_does_not_cover_a_chmod_permissionerror():
    """The gap this fix exists for, stated as an executable fact.

    If a future CPython closes it, this test starts failing and the conftest
    patch can be deleted -- that is the point of pinning it.
    """
    t, name = _tmpdir_with_locked_file(True)
    chmod, unlink = _blocking(name, [999])  # permanently blocked
    with mock.patch("os.chmod", chmod), mock.patch("os.unlink", unlink):
        # The UNPATCHED stdlib implementation, reached through the saved
        # original the conftest patch keeps.
        from conftest import _orig_tmpdir_cleanup
        with pytest.raises(PermissionError):
            _orig_tmpdir_cleanup(t)


def test_a_transient_block_is_retried_and_the_directory_really_goes_away():
    """The real Windows case: the handle IS released, just late."""
    t, name = _tmpdir_with_locked_file(True)
    path = t.name
    chmod, unlink = _blocking(name, [2])  # clears on the third attempt
    with mock.patch("os.chmod", chmod), mock.patch("os.unlink", unlink):
        t.cleanup()
    assert not os.path.exists(path), "retry must delete, not merely swallow"


def test_a_permanent_block_is_swallowed_when_the_caller_asked_for_that():
    t, name = _tmpdir_with_locked_file(True)
    chmod, unlink = _blocking(name, [999])
    with mock.patch("os.chmod", chmod), mock.patch("os.unlink", unlink):
        t.cleanup()  # must not raise


def test_without_the_flag_a_permanent_block_still_raises():
    """The patch closes the gap between the flag and its behaviour; it must not
    silence a teardown failure the caller never opted out of."""
    t, name = _tmpdir_with_locked_file(False)
    chmod, unlink = _blocking(name, [999])
    with mock.patch("os.chmod", chmod), mock.patch("os.unlink", unlink):
        with pytest.raises(OSError):
            t.cleanup()


def test_context_manager_exit_goes_through_the_same_path():
    """`with TemporaryDirectory(...)` calls cleanup(), so it inherits the fix."""
    chmod = unlink = None
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        (Path(d) / "durable_audit.db-shm").write_text("x", encoding="utf-8")
        chmod, unlink = _blocking("durable_audit.db-shm", [999])
        cm = mock.patch.multiple("os", chmod=chmod, unlink=unlink)
        cm.start()
    cm.stop()  # exiting the with-block must not have raised


def test_a_clean_directory_is_removed_on_the_first_attempt(monkeypatch):
    """No behaviour change and no added latency on the happy path."""
    calls = []
    import conftest

    real = conftest._orig_tmpdir_cleanup
    monkeypatch.setattr(conftest, "_orig_tmpdir_cleanup",
                        lambda self: (calls.append(1), real(self))[1])
    slept = []
    monkeypatch.setattr(conftest.time, "sleep", lambda s: slept.append(s))

    t = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    path = t.name
    t.cleanup()
    assert not os.path.exists(path)
    assert calls == [1], "one attempt, no retries"
    assert slept == [], "no sleep on the happy path"
