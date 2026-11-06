"""S5 kill-switch tests for the ToolA read path (read-limits campaign).

SOT: PLAN.md (toola-read-limits-20261106) section 3.2 kill-switch bullet
(B9) and section 4 step S5. The flag ORCHESTRATOR_READ_RANGES_ENABLED is
read from the process environment on EVERY call (predicate
``_ranges_enabled`` in orchestrator/tools/read/context.py): it is OFF iff
the trimmed, lower-cased value is exactly "0" or "false"; unset, empty or
any other value (off / no / 00 / 1 / ...) means ON.

S4 already wired the flag into the ranges[] whole-call gate (1)
RANGES_DISABLED (pinned by tests/test_read_ranges_s4.py). S5 completes
B9:

  * a declared token_budget falls back to EXACT legacy semantics while
    the flag is OFF -- the fit stops early inside the 800-line hard cap,
    auto-full follows legacy, and no budget_mode /
    token_budget_effective / line_cut keys appear;
  * with the flag ON (including odd values such as "off"), budget mode
    works as S3 built it and shown can pass 800 lines;
  * the registry description of read_file_range states the value
    semantics and the restart caveat (the flag is read per call from the
    process env, so flipping it on a running daemon needs a restart).

Like the S1 golden / S3 budget / S4 ranges files, these run the handler
in-process through the real tool-call surface on tmp fixtures -- never
against the live daemon. monkeypatch flips the env per test; the
predicate reads it per call, so no process restart is needed here (that
is exactly the test/harness value of read-per-call noted in the plan).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from pathlib import Path

import pytest

import code_tools
from orchestrator.tools.read import adaptive
from orchestrator.tools.read import context as read_context

ENV_FLAG = "ORCHESTRATOR_READ_RANGES_ENABLED"

# Same fixture as the S1 golden file, so the flag-OFF legacy expectations
# below are compared against the very expectations S1 pinned.
BIG_LINES = [f"line {i:04d} alpha" for i in range(1, 1001)]
BIG_TEXT = "\n".join(BIG_LINES) + "\n"
NOTE_TEXT = "one\ntwo\nthree\nfour\n"
# 2,000 short lines: the numbered cost of the whole file (~5,901 tokens by
# the estimator) fits the 12,000 CAP, so budget mode delivers all 2,000
# lines while legacy stops at the 800-line hard cap.
SHORT_LINES = ["x=1"] * 2000
SHORT_TEXT = "\n".join(SHORT_LINES) + "\n"
SMALL_LINES = [f"line {i:04d}" for i in range(1, 101)]
SMALL_TEXT = "\n".join(SMALL_LINES) + "\n"

# Handler success shape for scope=range, pinned by the S1 golden file.
RANGE_KEYS = {
    "ok", "result", "file", "project_path", "file_group", "lang",
    "total_lines", "shown", "range_id", "bounded", "max_lines",
    "max_lines_cap", "max_lines_default", "scope_requested", "scope_resolved",
    "selection", "content_hash", "range_ref", "cache_hit", "cache_waited",
    "estimated_tokens", "token_budget", "token_estimator", "auto_full_file",
    "small_file_threshold", "truncated", "next_ranges", "read_token_seeded",
    "derived_state_touched",
}


def _clear_read_cache() -> None:
    with adaptive._CACHE_COND:
        adaptive._CACHE_BY_PATH.clear()
        adaptive._CACHE_BY_HASH.clear()
        adaptive._INFLIGHT.clear()
        adaptive._CACHE_BYTES = 0


@pytest.fixture(autouse=True)
def _seed_stub_and_flag_unset(monkeypatch):
    """Deterministic seed seam (the S1/S3/S4 pattern) and a known flag
    state: every test starts with the flag UNSET (ON) and flips it
    explicitly when it wants another state."""
    monkeypatch.delenv(ENV_FLAG, raising=False)
    monkeypatch.setattr(
        code_tools, "_read_token_upsert", lambda *a, **k: True, raising=False)
    _clear_read_cache()
    yield
    _clear_read_cache()


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return path


def _read(tmp_path: Path, name: str, **kwargs) -> dict:
    raw = asyncio.run(code_tools.read_file_range(
        name, project_path=str(tmp_path), **kwargs))
    return json.loads(raw)


def _numbered(lines: list[str], start: int) -> str:
    return "\n".join(f"{start + i}: {line}" for i, line in enumerate(lines))


def _estimated(lines: list[str], start: int) -> int:
    # Independent re-derivation of the estimator contract (the S1/S3 one).
    return sum(
        math.ceil(len(f"{start + i}: {line}\n".encode("utf-8")) / 4.0)
        for i, line in enumerate(lines))


def _continuation(tmp_path: Path, name: str, start: int, end: int) -> list:
    return [{
        "file_path": str((tmp_path / name).resolve()),
        "scope": "range", "start_line": start, "end_line": end,
        "name": None, "kind": "continuation", "relevance_score": 1.0,
        "reason": "selected_scope_continuation", "source": "line_range",
    }]


def _budget_call(tmp_path: Path) -> dict:
    """One call shape exercised in both flag states: an explicit
    1-2000 selection, max_lines=3000 and a budget at the CAP."""
    _write(tmp_path, "short.txt", SHORT_TEXT)
    return _read(tmp_path, "short.txt", start_line=1, end_line=2000,
                 max_lines=3000, token_budget=12000)


# ---------------------------------------------------------------------------
# Predicate semantics (the four env states, at the unit level)
# ---------------------------------------------------------------------------

def test_predicate_value_semantics(monkeypatch):
    assert read_context._ranges_enabled() is True  # unset = ON
    for value, expected in [
            ("0", False), ("false", False), (" FALSE ", False),
            ("False", False),
            ("", True), ("off", True), ("no", True), ("00", True),
            ("1", True), ("true", True)]:
        monkeypatch.setenv(ENV_FLAG, value)
        assert read_context._ranges_enabled() is expected, value


# ---------------------------------------------------------------------------
# Budget path across the flag states
# ---------------------------------------------------------------------------

def test_flag_unset_budget_mode_active(tmp_path):
    body = _budget_call(tmp_path)
    assert body["ok"] is True
    assert body["budget_mode"] is True
    assert body["token_budget_effective"] == 12000
    assert body["shown"] == "1-2000"
    assert body["result"] == _numbered(SHORT_LINES, 1)
    assert body["estimated_tokens"] == _estimated(SHORT_LINES, 1)
    assert body["estimated_tokens"] > _estimated(SHORT_LINES[:800], 1)
    assert body["selection"]["start_line"] == 1
    assert body["selection"]["end_line"] == 2000
    assert body["truncated"] is False
    assert "line_cut" not in body


def test_flag_zero_budget_falls_back_to_legacy(tmp_path, monkeypatch):
    # S5 task case (b): flag OFF + a large declared budget on a file over
    # 800 lines -> shown is capped at 800 exactly as legacy, and NONE of
    # the budget-mode keys appear; the whole shape is the golden one.
    monkeypatch.setenv(ENV_FLAG, "0")
    body = _budget_call(tmp_path)
    assert set(body) == RANGE_KEYS
    assert "budget_mode" not in body
    assert "token_budget_effective" not in body
    assert "line_cut" not in body
    assert body["shown"] == "1-800"
    assert body["max_lines"] == 800
    assert body["max_lines_cap"] == 800
    assert body["result"] == _numbered(SHORT_LINES[:800], 1)
    assert body["estimated_tokens"] == _estimated(SHORT_LINES[:800], 1)
    # The declared budget is still echoed under its legacy key.
    assert body["token_budget"] == 12000
    assert body["selection"]["start_line"] == 1
    assert body["selection"]["end_line"] == 2000
    assert body["truncated"] is True
    assert body["next_ranges"] == _continuation(tmp_path, "short.txt", 801, 2000)


def test_flag_false_budget_falls_back_to_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "false")
    body = _budget_call(tmp_path)
    assert body["shown"] == "1-800"
    assert "budget_mode" not in body
    assert "token_budget_effective" not in body
    assert "line_cut" not in body


def test_flag_odd_values_keep_budget_mode(tmp_path, monkeypatch):
    # "off" reads as OFF in shell lore but is NOT an off value here: only
    # 0 / false (trimmed, lower-cased) disable the flag (PLAN section 3.2).
    for value in ("off", "00"):
        monkeypatch.setenv(ENV_FLAG, value)
        body = _budget_call(tmp_path)
        assert body["budget_mode"] is True, value
        assert body["token_budget_effective"] == 12000, value
        assert body["shown"] == "1-2000", value


# ---------------------------------------------------------------------------
# ranges[] under the flag (S5 representative case; S4 pins the full matrix)
# ---------------------------------------------------------------------------

def test_flag_off_ranges_rejected(tmp_path, monkeypatch):
    _write(tmp_path, "f.txt", SMALL_TEXT)
    monkeypatch.setenv(ENV_FLAG, "0")
    body = _read(tmp_path, "f.txt",
                 ranges=[{"start_line": 1, "end_line": 2}])
    assert body["ok"] is False
    assert body["error_name"] == "RANGES_DISABLED"
    # Gate order: DISABLED (1) still beats a garbage ranges value (2).
    monkeypatch.setenv(ENV_FLAG, "false")
    body = _read(tmp_path, "f.txt", ranges=5)
    assert body["error_name"] == "RANGES_DISABLED"
    # An odd value does NOT disable: the same call is served.
    monkeypatch.setenv(ENV_FLAG, "off")
    body = _read(tmp_path, "f.txt",
                 ranges=[{"start_line": 1, "end_line": 2}])
    assert body["ok"] is True


# ---------------------------------------------------------------------------
# Flag OFF: a plain legacy call is byte-identical to the S1 golden shape
# ---------------------------------------------------------------------------

def test_flag_off_bare_call_matches_golden_shape(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "0")
    _write(tmp_path, "big.txt", BIG_TEXT)
    body = _read(tmp_path, "big.txt")

    assert set(body) == RANGE_KEYS
    assert body["ok"] is True
    assert body["result"] == _numbered(BIG_LINES[:200], 1)
    assert body["estimated_tokens"] == _estimated(BIG_LINES[:200], 1) == 1101
    assert body["shown"] == "1-200"
    assert body["range_id"] == "big.txt:1p200"
    assert body["max_lines"] == 200
    assert body["selection"] == {
        "ok": True, "scope": "range", "name": None,
        "start_line": 1, "end_line": 1000, "source": "line_range",
    }
    digest = hashlib.sha256(BIG_TEXT.encode("utf-8")).hexdigest()
    assert body["content_hash"] == digest
    assert body["range_ref"] == f"big.txt@{digest[:12]}:L1-L200"
    assert body["token_budget"] is None
    assert body["auto_full_file"] is False
    assert body["truncated"] is True
    assert body["next_ranges"] == _continuation(tmp_path, "big.txt", 201, 1000)
    assert body["read_token_seeded"] is True


def test_flag_off_explicit_call_matches_golden_shape(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "false")
    _write(tmp_path, "note.txt", NOTE_TEXT)
    body = _read(tmp_path, "note.txt", start_line=2, end_line=3)

    assert set(body) == RANGE_KEYS
    assert body["result"] == "2: two\n3: three"
    assert body["estimated_tokens"] == 5
    assert body["shown"] == "2-3"
    assert body["range_id"] == "note.txt:2p2"
    assert body["truncated"] is False
    assert body["next_ranges"] == []


# ---------------------------------------------------------------------------
# Flag OFF: the single-line cut declaration is a budget-mode signal only
# ---------------------------------------------------------------------------

def test_flag_off_single_line_cut_is_silent_legacy(tmp_path, monkeypatch):
    _write(tmp_path, "huge.txt", "a" * 200000 + "\n")
    # Budget 10: the cut length is budget*4 - prefix(3) - newline(1) = 36
    # bytes under the S3 prefix-aware formula, in BOTH flag states; only
    # the DECLARATION differs.
    monkeypatch.setenv(ENV_FLAG, "0")
    body = _read(tmp_path, "huge.txt", token_budget=10)
    assert set(body) == RANGE_KEYS
    assert body["result"] == "1: " + "a" * 36
    assert body["estimated_tokens"] == 10
    assert "line_cut" not in body
    assert "budget_mode" not in body
    # Legacy truncated is line-based only: the cut was silent pre-S3.
    assert body["truncated"] is False

    monkeypatch.delenv(ENV_FLAG)
    body = _read(tmp_path, "huge.txt", token_budget=10)
    assert set(body) == RANGE_KEYS | {
        "budget_mode", "token_budget_effective", "line_cut"}
    assert body["result"] == "1: " + "a" * 36
    assert body["line_cut"] is True
    assert body["truncated"] is True
    assert body["budget_mode"] is True
    assert body["token_budget_effective"] == 10


# ---------------------------------------------------------------------------
# Registry description documents the flag honestly (PLAN section 4 S5)
# ---------------------------------------------------------------------------

def test_registry_description_documents_kill_switch():
    schema = next(x["function"] for x in code_tools.TOOL_SCHEMAS
                  if x["function"]["name"] == "read_file_range")
    props = schema["parameters"]["properties"]
    description = props["ranges"]["description"]
    for needle in ("ORCHESTRATOR_READ_RANGES_ENABLED",
                   "off, no or 00 do NOT turn it off",
                   "an unset or empty value means ON",
                   "RANGES_DISABLED",
                   "read from the process environment on every call",
                   "only after that process restarts"):
        assert needle in description, needle
