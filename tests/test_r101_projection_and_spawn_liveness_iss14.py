"""ISS-14: two ways a response reported success it had not established.

ISS-14a -- start_app / run_project_command(keep_alive=True). With no readiness
target the keep_alive branch returned a STATIC stub: no settle wait, no liveness
check, `launch_verified: null`, ok:true, and a `spawned_pid` the OS may already
have reclaimed. A launcher that died on a bad path was indistinguishable from a
serving app, and `verify_after_launch_ms` was ignored outright. stop_app was then
aimed at a PID that no longer existed and the caller never learned the launch had
failed. `_spawn_liveness_evidence` now settles, polls, and separates the three
real outcomes -- alive-but-unproven, wrapper-exited-0 (normal for a detaching
.bat), and non-zero exit (a genuine failure, so ok flips to False).

ISS-14b -- the R101 `fields=` projection. file_sha256 answers with a RAW-BYTES
digest, which is a different hash domain from `read_file_range.content_hash` and
seeds no edit authority; the two are indistinguishable by shape (64 hex chars).
`hash_domain` and `read_token_seeded` are the only markers saying so, and
`fields=ok,sha256` dropped both -- handing back a bare digest a caller could paste
into `write_fix.expected_hash`. They are now force-kept AND the rescue is
announced, so a caller cannot silently receive the ambiguous half.
"""
from __future__ import annotations

import asyncio
import json
import sys
from unittest import mock

import pytest

import code_tools
import orchestrator_server as srv


# --------------------------------------------------------------------------
# ISS-14b -- R101 projection safety
# --------------------------------------------------------------------------

def _sha_payload() -> dict:
    return {"ok": True, "sha256": "a" * 64, "hash_domain": "raw_file_bytes",
            "read_token_seeded": False, "path": "x.py", "size_bytes": 10}


def test_no_fields_returns_the_full_envelope_untouched():
    payload = _sha_payload()
    assert srv._r101_filter(payload, set()) is payload
    assert srv._r101_filter(payload, None) is payload


def test_non_dict_payload_is_passed_through():
    assert srv._r101_filter("raw text", {"ok"}) == "raw text"


def test_hash_domain_markers_survive_a_projection_that_dropped_them():
    out = srv._r101_filter(_sha_payload(), {"ok", "sha256"})
    # The digest is useless -- and dangerous -- without its domain.
    assert out["sha256"] == "a" * 64
    assert out["hash_domain"] == "raw_file_bytes"
    assert out["read_token_seeded"] is False
    assert out["response_incomplete"] == "RESPONSE_INCOMPLETE"
    assert out["fields_force_kept"] == ["hash_domain", "read_token_seeded"]
    assert "write_fix.expected_hash" in out["fields_force_kept_hint"]


def test_projection_discloses_that_it_is_a_projection():
    out = srv._r101_filter(_sha_payload(), {"ok", "sha256"})
    # 6 keys in, 4 kept (2 of them force-kept) -> 2 dropped.
    assert out["response_truncated"] is True
    assert out["fields_dropped_count"] == 2


def test_asking_for_the_markers_explicitly_raises_no_rescue_notice():
    out = srv._r101_filter(
        _sha_payload(), {"ok", "sha256", "hash_domain", "read_token_seeded"})
    assert out["hash_domain"] == "raw_file_bytes"
    # Nothing was rescued, so nothing is announced: the notice means "your
    # projection was unsafe", not "a projection happened".
    assert "response_incomplete" not in out
    assert "fields_force_kept" not in out


def test_a_payload_without_markers_gets_no_safety_keys():
    # A clean subset stays clean: nothing was rescued and every key asked for
    # existed, so the projection adds NOTHING -- not even the truncation
    # disclosure. A23-P2's CorrectFilterStaysClean owns that invariant, and a
    # marker present on every filtered response carries no information.
    out = srv._r101_filter({"ok": True, "results": [1, 2], "count": 2}, {"results"})
    assert out == {"ok": True, "results": [1, 2]}


def test_a_misspelled_field_is_named_instead_of_looking_empty():
    # `fields=result` on a tool that answers `results` used to return a bare
    # envelope, which reads exactly like "the tool found nothing".
    out = srv._r101_filter({"ok": True, "results": [1]}, {"result"})
    assert out["fields_unknown"] == ["result"]
    assert "NOT empty" in out["fields_unknown_hint"]


def test_always_keep_names_are_not_reported_as_unknown():
    # `error_name` is absent from a success payload by design; calling that a
    # caller mistake would fire the unknown-field warning on every ok:true call.
    out = srv._r101_filter({"ok": True, "results": []}, {"ok", "error_name"})
    assert "fields_unknown" not in out


# --------------------------------------------------------------------------
# ISS-14a -- spawn liveness (real subprocess; no readiness target)
# --------------------------------------------------------------------------

def _keep_alive(tmp_path, code: str, **kwargs) -> dict:
    return json.loads(asyncio.run(code_tools.run_project_command(
        str(tmp_path), [sys.executable, "-c", code],
        shell_mode="exec", keep_alive=True, background=True, **kwargs)))


def _reap(pid) -> None:
    if not pid:
        return
    try:
        import psutil
        proc = psutil.Process(int(pid))
        for child in proc.children(recursive=True):
            child.kill()
        proc.kill()
    except Exception:
        pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows spawn contract")
def test_a_process_that_dies_after_spawn_is_reported_as_a_failure(tmp_path):
    # 1500ms is well above interpreter startup on an HDD, so the exit is
    # observed rather than raced -- and it pins that verify_after_launch_ms is
    # honoured on this branch at all, which it previously was not.
    result = _keep_alive(tmp_path, "raise SystemExit(3)", verify_after_launch_ms=1500)
    assert result["ok"] is False
    assert result["error_name"] == "PROCESS_EXITED_AFTER_SPAWN"
    assert result["root_causes"] == ["PROCESS_EXITED_AFTER_SPAWN"]
    assert result["launch_verified"] is False
    assert result["pid_alive"] is False
    assert result["wrapper_exit_code"] == 3
    assert result["spawn_settle_ms"] == 1500
    assert result["ca_next_action"] == "rerun_with_keep_alive_false_to_capture_stderr"
    # keep_alive discards the streams, so the response must say where the reason is.
    assert "keep_alive=false" in result["hint"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows spawn contract")
def test_a_live_pid_is_ok_but_never_claims_readiness(tmp_path):
    result = _keep_alive(tmp_path, "import time; time.sleep(30)",
                         verify_after_launch_ms=600)
    try:
        assert result["ok"] is True
        assert result["pid_alive"] is True
        assert result["wrapper_exit_code"] is None
        # The whole point: alive is NOT serving, and the response says so
        # instead of implying a verified launch.
        assert result["readiness_established"] is False
        assert result["launch_verified"] is None
        assert "SERVING" in result["readiness_hint"]
        assert "error_name" not in result
    finally:
        _reap(result.get("spawned_pid"))


def test_settle_wait_is_capped_so_a_huge_value_cannot_hang_the_call(tmp_path):
    # No real spawn: a mocked Popen that reports exit 0 lets the cap be pinned
    # without a 10s test. 10_000ms is the ceiling; 900_000 must not be honoured.
    proc = mock.MagicMock()
    proc.pid = 424242
    proc.poll.return_value = 0
    with mock.patch.object(code_tools.subprocess, "Popen", return_value=proc), \
         mock.patch.object(code_tools.time, "sleep"):
        result = _keep_alive(tmp_path, "pass", verify_after_launch_ms=900_000)
    assert result["spawn_settle_ms"] == 10_000
    # Wrapper exit 0 is NOT a failure on Windows -- a .bat that detaches its own
    # server exits 0 while the server lives -- so ok stays True, unproven.
    assert result["ok"] is True
    assert result["readiness_established"] is False
    assert "NOT evidence the app is up" in result["readiness_hint"]
