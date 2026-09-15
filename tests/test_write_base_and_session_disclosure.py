import asyncio
import json
from unittest import mock

import code_tools as ct
import surgical_mapper as sm
from orchestrator.tools.write import core


def test_parallel_same_file_never_reports_two_successful_lost_updates(tmp_path):
    fp = tmp_path / "sample.txt"
    fp.write_text("first\nsecond\n")
    sm.generate_project_map(str(tmp_path))

    async def scenario():
        await ct.read_file_range(str(fp), 1, 2, project_path=str(tmp_path))
        count = 0
        ready = asyncio.Event()

        async def barrier(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                ready.set()
            await ready.wait()
            return {"ok": True}

        with mock.patch.object(core, "_pre_write_syntax_check", side_effect=barrier):
            results = await asyncio.gather(
                ct.write_fix("sample.txt", 1, 1, "FIRST", project_path=str(tmp_path)),
                ct.write_fix("sample.txt", 2, 2, "SECOND", project_path=str(tmp_path)))
        return [json.loads(r) for r in results]

    results = asyncio.run(scenario())
    assert sum(bool(r.get("ok")) for r in results) == 1
    failure = next(r for r in results if not r.get("ok"))
    assert failure["error_name"] == "WRITE_BASE_CHANGED"
    assert failure["write_performed"] is False
    assert fp.read_text() in ("FIRST\nsecond\n", "first\nSECOND\n")


def test_resume_title_difference_is_disclosed(tmp_path):
    row = {"session_id": "original", "session_title": "old-title", "started_at": "2026-09-14"}
    with mock.patch.object(ct, "_init_session_db"), \
         mock.patch.object(ct, "_open_or_resume_session", return_value=(row, False)), \
         mock.patch.object(ct, "_matrix_audit_open_session_summary", return_value={}), \
         mock.patch.object(ct, "_r43_project_switch_context", return_value={}):
        result = json.loads(asyncio.run(ct.open_session(
            "new-title", str(tmp_path), agent_id="Ope")))
    assert result["session_id"] == "original"
    assert result["session_title"] == "old-title"
    assert result["requested_session_title"] == "new-title"
    assert result["session_title_mismatch"] is True
    assert result["rename_session_payload"]["new_title"] == "new-title"


def test_created_artifact_cache_failure_does_not_invite_rewrite(tmp_path):
    with mock.patch.object(ct, "_update_cache_after_write", return_value=False):
        result = json.loads(asyncio.run(ct.write_new_file(
            "artifact.txt", new_content="written", project_path=str(tmp_path))))
    assert result["ok"] and result["write_performed"]
    assert result["cache_synced"] is False
    assert (tmp_path / "artifact.txt").read_text() == "written"
    assert "Do not repeat" in result["cache_sync_note"]

def test_unknown_open_session_mode_is_rejected(tmp_path):
    with mock.patch.object(ct, "_init_session_db"), \
         mock.patch.object(ct, "_open_or_resume_session") as opener:
        result = json.loads(asyncio.run(ct.open_session(
            "probe", str(tmp_path), mode="execute", agent_id="Clau")))
    opener.assert_not_called()
    assert result["ok"] is False
    assert result["error_name"] == "INVALID_MODE"
    assert result["requested_mode"] == "execute"
    assert "T1" in result["accepted_modes"]
    assert result["ca_next_action"] == "use_T1_or_T2_or_set_workflow_mode"


def test_resume_discloses_mode_and_does_not_apply_requested(tmp_path):
    row = {
        "session_id": "original",
        "session_title": "old-title",
        "started_at": "2026-09-14",
        "mode": "T2",
    }
    with mock.patch.object(ct, "_init_session_db"), \
         mock.patch.object(ct, "_open_or_resume_session", return_value=(row, False)), \
         mock.patch.object(ct, "_matrix_audit_open_session_summary", return_value={}), \
         mock.patch.object(ct, "_r43_project_switch_context", return_value={}):
        result = json.loads(asyncio.run(ct.open_session(
            "old-title", str(tmp_path), mode="T1", agent_id="Ope")))
    assert result["ok"] is True
    assert result["resumed"] is True
    assert result["requested_mode"] == "T1"
    assert result["effective_mode"] == "T2"
    assert result["mode_applied"] is False
    assert result["mode_ignored_on_resume"] is True
    assert "set_workflow_mode" in result["mode_change_hint"]
