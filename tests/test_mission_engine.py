"""Tests for orchestrator.mission.engine (P3b-1).

Covers: attempt token make/parse/stale; build_wrapper 7 mandatory lines with
correct slash direction per line; artifact_complete missing/empty/marker;
next_ready_task dependency gating; classify_for_reaper 8 rule branches.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.mission.engine import (
    artifact_complete,
    build_wrapper,
    classify_for_reaper,
    is_stale_attempt,
    make_attempt_token,
    next_ready_task,
    parse_attempt_token,
)


# --- attempt token -----------------------------------------------------------

def test_make_attempt_token_format():
    assert make_attempt_token(7, 2) == "mission_attempt=7:2"
    assert make_attempt_token(0, 0) == "mission_attempt=0:0"


def test_parse_attempt_token_valid():
    text = "some wrapper\nmission_attempt=7:2\nEND"
    assert parse_attempt_token(text) == (7, 2)


def test_parse_attempt_token_none():
    assert parse_attempt_token("no token here") is None
    assert parse_attempt_token("") is None


def test_is_stale_attempt_true():
    assert is_stale_attempt(1, 3) is True


def test_is_stale_attempt_false():
    assert is_stale_attempt(3, 3) is False
    assert is_stale_attempt(4, 3) is False


# --- build_wrapper -----------------------------------------------------------

def test_build_wrapper_contains_all_mandatory_parts_and_slash_direction():
    w = build_wrapper(
        task_id=5,
        attempt=1,
        title="Do thing",
        prompt_artifact_path=r"D:\proj\t5_prompt.md",
        output_artifact_path="D:/proj/t5_out.md",
        agent_label="HermC",
    )
    # 1 header
    assert "[MISSION TASK 5 ATTEMPT 1] Bạn là HermC, worker one-shot, không có context cũ." in w
    # 2 prompt path forward-slash
    assert "Đọc prompt đầy đủ tại: D:/proj/t5_prompt.md" in w
    assert r"D:\proj\t5_prompt.md" not in w.split("\n")[1]
    # 3 output path backslash
    assert "Ghi kết quả vào: D:\\proj\\t5_out.md" in w
    # 4 partial continue
    assert "Artifact có thể đã partial từ attempt trước — CONTINUE, đừng làm lại từ đầu." in w
    # 5 token
    assert "mission_attempt=5:1" in w
    # 6 bootstrap hint
    assert "If this task must write outside cwd, bootstrap [A] and use [A] write tools." in w
    # 7 end marker default
    assert "Kết thúc file + reply bằng dòng: END_TASK_5_A1" in w


def test_build_wrapper_custom_end_marker():
    w = build_wrapper(
        task_id=5,
        attempt=1,
        title="x",
        prompt_artifact_path="a.md",
        output_artifact_path="b.md",
        agent_label="OpeC",
        end_marker="CUSTOM_END",
    )
    assert "Kết thúc file + reply bằng dòng: CUSTOM_END" in w


def test_build_wrapper_injects_budget_and_partial_checkpoint():
    w = build_wrapper(
        task_id=9, attempt=2, title="x", prompt_artifact_path="a.md",
        output_artifact_path="D:/work/result.md", agent_label="CodoC",
        tool_call_budget=10,
    )
    assert "Budget: ~10 tool calls" in w
    assert "at ~70% you MUST write current progress" in w
    assert r"D:\work\result.md.partial" in w


# --- artifact_complete -------------------------------------------------------

def test_artifact_complete_missing_file(tmp_path):
    assert artifact_complete(tmp_path / "nope.md", "END") is False


def test_artifact_complete_missing_marker(tmp_path):
    f = tmp_path / "out.md"
    f.write_text("some content\nno marker here\n", encoding="utf-8")
    assert artifact_complete(f, "END_TASK_1_A1") is False


def test_artifact_complete_correct_marker(tmp_path):
    f = tmp_path / "out.md"
    f.write_text("result line\nEND_TASK_1_A1\n", encoding="utf-8")
    assert artifact_complete(f, "END_TASK_1_A1") is True


def test_artifact_complete_empty_file(tmp_path):
    f = tmp_path / "empty.md"
    f.write_text("", encoding="utf-8")
    assert artifact_complete(f, "END") is False


def test_artifact_complete_marker_with_trailing_whitespace(tmp_path):
    f = tmp_path / "out.md"
    f.write_text("data\nEND_TASK_1_A1   \n", encoding="utf-8")
    assert artifact_complete(f, "END_TASK_1_A1") is True


# --- next_ready_task ---------------------------------------------------------

def test_next_ready_task_no_deps_picks_lowest_seq():
    tasks = [
        {"id": 3, "state": "pending", "depends_on_json": "[]", "seq": 2},
        {"id": 1, "state": "pending", "depends_on_json": "[]", "seq": 1},
        {"id": 2, "state": "pending", "depends_on_json": "[]", "seq": 1},
    ]
    # seq=1 tie → lower id wins
    got = next_ready_task(tasks)
    assert got is not None
    assert got["id"] == 1


def test_next_ready_task_deps_not_done_skipped():
    tasks = [
        {"id": 1, "state": "done", "depends_on_json": "[]", "seq": 0},
        {"id": 2, "state": "pending", "depends_on_json": "[1,3]", "seq": 1},
        {"id": 3, "state": "pending", "depends_on_json": "[]", "seq": 2},
    ]
    # id=2 depends on 3 (not done) → skipped; id=3 ready (seq=2)
    got = next_ready_task(tasks)
    assert got is not None
    assert got["id"] == 3


def test_next_ready_task_deps_done_selected():
    tasks = [
        {"id": 1, "state": "done", "depends_on_json": "[]", "seq": 0},
        {"id": 2, "state": "done", "depends_on_json": "[]", "seq": 0},
        {"id": 3, "state": "pending", "depends_on_json": "[1,2]", "seq": 1},
    ]
    got = next_ready_task(tasks)
    assert got is not None
    assert got["id"] == 3


def test_next_ready_task_none_when_all_done():
    tasks = [
        {"id": 1, "state": "done", "depends_on_json": "[]", "seq": 0},
        {"id": 2, "state": "done", "depends_on_json": "[1]", "seq": 1},
    ]
    assert next_ready_task(tasks) is None


def test_next_ready_task_none_when_pending_blocked():
    tasks = [
        {"id": 1, "state": "pending", "depends_on_json": "[2]", "seq": 1},
        {"id": 2, "state": "pending", "depends_on_json": "[]", "seq": 2},
    ]
    # id=1 depends on 2 (pending, not done); id=2 ready
    got = next_ready_task(tasks)
    assert got is not None
    assert got["id"] == 2


# --- classify_for_reaper (8 branches) ----------------------------------------

def test_reaper_dispatching_no_pid_requeue():
    assert classify_for_reaper(
        state="dispatching", side_effect_class="pure_artifact",
        dispatch_started=False, grace_expired=True, lease_expired=False,
        artifact_stale=False, heartbeat_recent=False, child_pid_alive=None,
    ) == "requeue"


def test_reaper_dispatching_pid_false_requeue():
    assert classify_for_reaper(
        state="dispatching", side_effect_class="mutating",
        dispatch_started=False, grace_expired=True, lease_expired=False,
        artifact_stale=False, heartbeat_recent=False, child_pid_alive=False,
    ) == "requeue"


def test_reaper_dispatching_pid_true_pure_kill():
    assert classify_for_reaper(
        state="dispatching", side_effect_class="pure_artifact",
        dispatch_started=False, grace_expired=True, lease_expired=False,
        artifact_stale=False, heartbeat_recent=False, child_pid_alive=True,
    ) == "kill_then_requeue"


def test_reaper_dispatching_pid_true_mutating_block():
    assert classify_for_reaper(
        state="dispatching", side_effect_class="mutating",
        dispatch_started=False, grace_expired=True, lease_expired=False,
        artifact_stale=False, heartbeat_recent=False, child_pid_alive=True,
    ) == "blocked_gate"


def test_reaper_started_pid_true_pure_kill():
    assert classify_for_reaper(
        state="started", side_effect_class="pure_artifact",
        dispatch_started=True, grace_expired=False, lease_expired=True,
        artifact_stale=True, heartbeat_recent=False, child_pid_alive=True,
    ) == "kill_then_requeue"


def test_reaper_started_pid_true_destructive_block():
    assert classify_for_reaper(
        state="started", side_effect_class="destructive",
        dispatch_started=True, grace_expired=False, lease_expired=True,
        artifact_stale=True, heartbeat_recent=False, child_pid_alive=True,
    ) == "blocked_gate"


def test_reaper_started_pid_false_requeue():
    assert classify_for_reaper(
        state="started", side_effect_class="mutating",
        dispatch_started=True, grace_expired=False, lease_expired=True,
        artifact_stale=True, heartbeat_recent=False, child_pid_alive=False,
    ) == "requeue"


def test_reaper_verifying_lease_expired_requeue_verify():
    assert classify_for_reaper(
        state="verifying", side_effect_class="pure_artifact",
        dispatch_started=True, grace_expired=False, lease_expired=True,
        artifact_stale=False, heartbeat_recent=True, child_pid_alive=True,
    ) == "requeue_verify"


def test_reaper_none_default():
    assert classify_for_reaper(
        state="started", side_effect_class="pure_artifact",
        dispatch_started=True, grace_expired=False, lease_expired=False,
        artifact_stale=False, heartbeat_recent=True, child_pid_alive=True,
    ) == "none"
