"""S1 golden byte-shape pins for the ToolA read path (read-limits campaign).

SOT: PLAN.md (toola-read-limits-20261106) section 4 step S1 + section 1 (A10)
+ section 2 (B10). These tests capture the CURRENT handler behaviour
in-process on tmp fixtures -- never from the live daemon -- so that later
sprints (S2 envelope/B8, S3 budget mode, S4 ranges[]) turn a red test into a
deliberate, reviewable shape change instead of a silent regression.

Pinned here:
  * legacy success shape (bare call, explicit call) -- exact key set, exact
    ``result`` string, exact metadata;
  * selection-error shape (INVALID_RANGE) and RESEED_FAILED shape -- these
    error shapes are frozen by B10 together with the success shape;
  * ``file_summary`` with and without ``token_budget`` (the summary branch
    has its own budget semantics today; S3 must not disturb it);
  * shown=200 by default on a large file and shown=800 at the hard cap
    (section 1 A10: no test pinned either before S1);
  * ENVELOPE BASELINE (tests named ``envelope_baseline``): the pre-B8
    excerpt/chunk behaviour of ``orchestrator_server``. These expectations
    are EXPECTED TO CHANGE in S2, and the S2 diff must match exactly the
    B8 carve-out list in PLAN section 2 (B10): byte-bounded excerpt,
    UTF-8-boundary chunking, ``content_sha256`` on chunk responses,
    ref-store size/eviction. Anything else changing there is a regression.

No production behaviour is changed by this file (S1 is test-only).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

import code_tools
import orchestrator_server as srv
from orchestrator.tools.read import adaptive

# --------------------------------------------------------------------------
# Fixtures (byte-exact; written with write_bytes so no newline translation
# can move a hash).
# --------------------------------------------------------------------------

BIG_LINES = [f"line {i:04d} alpha" for i in range(1, 1001)]
BIG_TEXT = "\n".join(BIG_LINES) + "\n"
NOTE_TEXT = "one\ntwo\nthree\nfour\n"
SUMMARY_TEXT = (
    "import os\nimport sys\n\n\n"
    "def alpha():\n    return 1\n\n\n"
    "def beta(x):\n    return x + 1\n\n\n"
    "class Gamma:\n    def method(self):\n        return 3\n"
)

# Handler success shape for scope=range, captured in-process at the SOT pin
# (orchestrator/tools/read/context.py range branch). The dispatcher adds
# ca_hint/ca_next_action/ca_next_tool/generation on the wire; those are NOT
# part of the handler shape pinned here.
RANGE_KEYS = {
    "ok", "result", "file", "project_path", "file_group", "lang",
    "total_lines", "shown", "range_id", "bounded", "max_lines",
    "max_lines_cap", "max_lines_default", "scope_requested", "scope_resolved",
    "selection", "content_hash", "range_ref", "cache_hit", "cache_waited",
    "estimated_tokens", "token_budget", "token_estimator", "auto_full_file",
    "small_file_threshold", "truncated", "next_ranges", "read_token_seeded",
    "derived_state_touched",
}

# file_summary branch: same shape minus small_file_threshold, plus summary.
SUMMARY_KEYS = (RANGE_KEYS - {"small_file_threshold"}) | {"summary"}

SELECTION_ERROR_KEYS = {
    "ok", "error_name", "error", "requested_start_line", "requested_end_line",
    "read_token_seeded", "derived_state_touched", "action_required",
    "ca_next_action", "hint", "file", "project_path", "content_hash",
    "cache_hit", "total_lines",
}

RESEED_FAILED_KEYS = {
    "ok", "error", "error_name", "reseed_failure_reason", "reseed_error",
    "file", "project_path", "read_token_seeded", "content_hash",
    "total_lines", "hint", "ca_next_action", "retryable",
    "derived_state_touched",
}

EXPECTED_SUMMARY = {
    "file": "summary.py",
    "line_count": 15,
    "size_bytes": len(SUMMARY_TEXT.encode("utf-8")),
    "content_hash": hashlib.sha256(SUMMARY_TEXT.encode("utf-8")).hexdigest(),
    "parser": "python_ast",
    "parse_error": None,
    "imports": ["os", "sys"],
    "symbols": [
        {"name": "alpha", "qualified_name": "alpha", "kind": "function",
         "start_line": 5, "end_line": 6},
        {"name": "beta", "qualified_name": "beta", "kind": "function",
         "start_line": 9, "end_line": 10},
        {"name": "Gamma", "qualified_name": "Gamma", "kind": "class",
         "start_line": 13, "end_line": 15},
        {"name": "method", "qualified_name": "Gamma.method", "kind": "method",
         "start_line": 14, "end_line": 15},
    ],
    "sections": [],
    "symbols_total": 4,
    "sections_total": 0,
}


def _clear_read_cache() -> None:
    with adaptive._CACHE_COND:
        adaptive._CACHE_BY_PATH.clear()
        adaptive._CACHE_BY_HASH.clear()
        adaptive._INFLIGHT.clear()
        adaptive._CACHE_BYTES = 0


@pytest.fixture(autouse=True)
def _deterministic_seed(monkeypatch):
    """Seed must be deterministic in-process: the real token store writes to
    a sessions DB keyed by the tmp project root, which is not what this file
    pins. Force the upsert seam to succeed; the RESEED_FAILED test re-points
    it at failure."""
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
    # Independent re-derivation of the estimator contract
    # (adaptive.estimate_tokens over numbered lines incl. the "N: " prefix
    # and trailing newline): ceil(utf8_bytes / 4) per line.
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


# --------------------------------------------------------------------------
# Legacy success shapes
# --------------------------------------------------------------------------

def test_bare_call_large_file_golden_shape(tmp_path):
    _write(tmp_path, "big.txt", BIG_TEXT)
    body = _read(tmp_path, "big.txt")

    assert set(body) == RANGE_KEYS
    assert body["ok"] is True
    assert body["result"] == _numbered(BIG_LINES[:200], 1)
    assert body["estimated_tokens"] == _estimated(BIG_LINES[:200], 1) == 1101
    assert body["file"] == str((tmp_path / "big.txt").resolve())
    assert body["project_path"] == str(tmp_path.resolve())
    assert body["file_group"] is None
    assert body["lang"] == "text"
    assert body["total_lines"] == 1000
    assert body["shown"] == "1-200"
    assert body["range_id"] == "big.txt:1p200"
    assert body["bounded"] is True
    assert body["max_lines"] == 200
    assert body["max_lines_cap"] == 800
    assert body["max_lines_default"] == 200
    assert body["scope_requested"] == "range"
    assert body["scope_resolved"] == "range"
    assert body["selection"] == {
        "ok": True, "scope": "range", "name": None,
        "start_line": 1, "end_line": 1000, "source": "line_range",
    }
    digest = hashlib.sha256(BIG_TEXT.encode("utf-8")).hexdigest()
    assert body["content_hash"] == digest
    assert body["range_ref"] == f"big.txt@{digest[:12]}:L1-L200"
    assert body["cache_hit"] is False
    assert body["cache_waited"] is False
    assert body["token_budget"] is None
    assert body["token_estimator"] == "utf8_bytes/4"
    assert body["auto_full_file"] is False
    assert body["small_file_threshold"] == 240
    assert body["truncated"] is True
    assert body["next_ranges"] == _continuation(tmp_path, "big.txt", 201, 1000)
    assert body["read_token_seeded"] is True
    assert body["derived_state_touched"] is False


def test_explicit_range_golden_shape(tmp_path):
    _write(tmp_path, "note.txt", NOTE_TEXT)
    body = _read(tmp_path, "note.txt", start_line=2, end_line=3)

    assert set(body) == RANGE_KEYS
    assert body["ok"] is True
    assert body["result"] == "2: two\n3: three"
    assert body["estimated_tokens"] == 5
    assert body["shown"] == "2-3"
    assert body["range_id"] == "note.txt:2p2"
    assert body["total_lines"] == 4
    assert body["selection"] == {
        "ok": True, "scope": "range", "name": None,
        "start_line": 2, "end_line": 3, "source": "line_range",
    }
    digest = hashlib.sha256(NOTE_TEXT.encode("utf-8")).hexdigest()
    assert body["content_hash"] == digest
    assert body["range_ref"] == f"note.txt@{digest[:12]}:L2-L3"
    assert body["auto_full_file"] is False
    assert body["truncated"] is False
    assert body["next_ranges"] == []
    assert body["read_token_seeded"] is True


def test_shown_defaults_to_200_on_large_file(tmp_path):
    # PLAN section 1 A10: shown=200 by default was pinned by no test before S1.
    _write(tmp_path, "big.txt", BIG_TEXT)
    body = _read(tmp_path, "big.txt")
    assert body["shown"] == "1-200"
    assert len(body["result"].split("\n")) == 200
    assert body["truncated"] is True


def test_shown_caps_at_800(tmp_path):
    # PLAN section 1 A10: shown=800 at the hard cap was pinned by no test
    # before S1. max_lines=1000 must be clamped to the 800 hard cap.
    _write(tmp_path, "big.txt", BIG_TEXT)
    body = _read(tmp_path, "big.txt", max_lines=1000)

    assert set(body) == RANGE_KEYS
    assert body["shown"] == "1-800"
    assert body["max_lines"] == 800
    assert body["max_lines_cap"] == 800
    assert body["result"] == _numbered(BIG_LINES[:800], 1)
    assert body["estimated_tokens"] == _estimated(BIG_LINES[:800], 1) == 4701
    assert body["range_id"] == "big.txt:1p800"
    digest = hashlib.sha256(BIG_TEXT.encode("utf-8")).hexdigest()
    assert body["range_ref"] == f"big.txt@{digest[:12]}:L1-L800"
    assert body["truncated"] is True
    assert body["next_ranges"] == _continuation(tmp_path, "big.txt", 801, 1000)


# --------------------------------------------------------------------------
# Error shapes (frozen by B10 together with the success shape)
# --------------------------------------------------------------------------

def test_selection_error_golden_shape(tmp_path):
    _write(tmp_path, "big.txt", BIG_TEXT)
    body = _read(tmp_path, "big.txt", start_line=5, end_line=2)

    assert set(body) == SELECTION_ERROR_KEYS
    assert body["ok"] is False
    assert body["error_name"] == "INVALID_RANGE"
    assert body["error"] == (
        "Range requires 1 <= start_line <= end_line when end_line is supplied.")
    assert body["requested_start_line"] == 5
    assert body["requested_end_line"] == 2
    assert body["action_required"] == "correct_range"
    assert body["ca_next_action"] == "correct_range"
    assert body["hint"] == (
        "Choose the intended range with start_line >= 1 and end_line >= "
        "start_line, or omit end_line. Bounds are inclusive; "
        "start_line=end_line reads one line. For up to N lines, use "
        "start_line + max_lines=N and omit end_line (subject to EOF and "
        "backend/token caps). Do not blindly swap bounds. force_reseed does "
        "not repair an invalid range.")
    assert body["file"] == str((tmp_path / "big.txt").resolve())
    assert body["project_path"] == str(tmp_path.resolve())
    assert body["content_hash"] == hashlib.sha256(
        BIG_TEXT.encode("utf-8")).hexdigest()
    assert body["cache_hit"] is False
    assert body["total_lines"] == 1000
    assert body["read_token_seeded"] is False
    assert body["derived_state_touched"] is False


def test_reseed_failed_golden_shape(tmp_path, monkeypatch):
    # Force the token store to decline: _seed_read_token_detailed reports
    # reason "upsert_failed" and the handler fails loud with RESEED_FAILED
    # instead of returning ok:true without a token (P11 fail-loud branch).
    monkeypatch.setattr(
        code_tools, "_read_token_upsert", lambda *a, **k: False, raising=False)
    _write(tmp_path, "big.txt", BIG_TEXT)
    body = _read(tmp_path, "big.txt", force_reseed=True)

    assert set(body) == RESEED_FAILED_KEYS
    assert body["ok"] is False
    assert body["error_name"] == "RESEED_FAILED"
    assert body["reseed_failure_reason"] == "upsert_failed"
    assert body["reseed_error"] == "_read_token_upsert returned falsy"
    assert body["error"] == (
        "force_reseed resolved the project root but the read-token store "
        "refused the write: _read_token_upsert returned falsy.")
    assert body["hint"] == (
        "This is NOT a missing project_path — the root resolved fine. "
        "Retrying the same call will fail the same way. Check the session "
        "store (sessions.db locked / disk full / schema mismatch) before "
        "retrying.")
    assert body["ca_next_action"] == "inspect_session_store"
    assert body["retryable"] is False
    assert body["file"] == str((tmp_path / "big.txt").resolve())
    assert body["project_path"] == str(tmp_path.resolve())
    assert body["content_hash"] == hashlib.sha256(
        BIG_TEXT.encode("utf-8")).hexdigest()
    assert body["total_lines"] == 1000
    assert body["read_token_seeded"] is False
    assert body["derived_state_touched"] is False


# --------------------------------------------------------------------------
# file_summary branch (own budget semantics today -- PLAN section 1 X4;
# the S3 budget mode must NOT apply to this branch)
# --------------------------------------------------------------------------

def _assert_summary_body(body: dict, tmp_path: Path, token_budget: int):
    assert set(body) == SUMMARY_KEYS
    assert body["ok"] is True
    assert body["summary"] == EXPECTED_SUMMARY
    assert body["result"] == json.dumps(
        EXPECTED_SUMMARY, ensure_ascii=False, indent=2)
    assert body["estimated_tokens"] == 221
    assert body["token_budget"] == token_budget
    assert body["shown"] == "file_summary"
    assert body["range_id"] == "summary.py:summary"
    digest = EXPECTED_SUMMARY["content_hash"]
    assert body["content_hash"] == digest
    assert body["range_ref"] == f"summary.py@{digest[:12]}:summary"
    assert body["selection"] == {
        "ok": True, "scope": "file_summary", "name": "summary.py",
        "start_line": 1, "end_line": 15, "source": "python_ast",
    }
    assert body["scope_requested"] == "file_summary"
    assert body["scope_resolved"] == "file_summary"
    assert body["lang"] == "python"
    assert body["total_lines"] == 15
    assert body["max_lines"] == 200
    assert body["max_lines_cap"] == 800
    assert body["auto_full_file"] is False
    assert body["truncated"] is False
    assert body["next_ranges"] == []
    assert body["read_token_seeded"] is True
    assert body["derived_state_touched"] is False


def test_file_summary_without_token_budget_golden(tmp_path):
    _write(tmp_path, "summary.py", SUMMARY_TEXT)
    body = _read(tmp_path, "summary.py", scope="file_summary")
    # No caller budget: the summary branch falls back to its own 4000.
    _assert_summary_body(body, tmp_path, token_budget=4000)


def test_file_summary_with_token_budget_golden(tmp_path):
    _write(tmp_path, "summary.py", SUMMARY_TEXT)
    body = _read(tmp_path, "summary.py", scope="file_summary", token_budget=2000)
    # The caller's budget is echoed by this branch (and drives its item cap);
    # it is NOT the S3 budget mode -- there is no budget_mode key today.
    _assert_summary_body(body, tmp_path, token_budget=2000)
    assert "budget_mode" not in body
    assert "token_budget_effective" not in body


# --------------------------------------------------------------------------
# ENVELOPE BASELINE (pre-B8) -- expected to change in S2, only along the
# B8 carve-out list (PLAN section 2 B10 / section 3.3).
# --------------------------------------------------------------------------

def _envelope_raw() -> str:
    return json.dumps(
        {"ok": True,
         "result": "Đọc tiếng Việt — dữ liệu thử nghiệm cho envelope. " * 200},
        ensure_ascii=False)


def test_envelope_baseline_excerpt_shape(tmp_path):
    raw = _envelope_raw()
    shaped = json.loads(srv._shape_tool_response_text(
        "read_file_range", raw, max_bytes=4096))

    assert set(shaped) == {
        "ok", "truncated", "original_bytes", "result_ref", "result_ref_url",
        "read_hint", "excerpt", "ca_next_action",
    }
    assert shaped["ok"] is True
    assert shaped["truncated"] is True
    assert shaped["original_bytes"] == len(raw.encode("utf-8")) == 13426
    ref = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()
    assert shaped["result_ref"] == ref
    assert shaped["result_ref_url"] == f"/api/result/{ref}"
    # S2 (B8): the read_hint now carries all three section 3.3 rules --
    # follow the server-returned next_offset (never stride
    # offset+=max_bytes), verify content_sha256 before using the
    # assembled bytes, and the moved-back start rule (returned offset
    # Y < requested X: place the chunk at Y, skip its first X-Y bytes
    # if bytes up to X are already held, to avoid duplicating Y..X).
    assert shaped["read_hint"] == (
        f"GET /api/result/{ref}?offset=<byte>&max_bytes=<n> returns the "
        "full untruncated response in ordered chunks; follow the "
        "server-returned next_offset until null, never stride "
        "offset+=max_bytes; verify content_sha256 of the assembled "
        "bytes before use; if the returned offset Y is smaller than "
        "the offset X you requested (start moved back to a UTF-8 "
        "character boundary), place the chunk at Y and skip its first "
        "X-Y bytes if you already hold bytes up to X, to avoid "
        "duplicating Y..X.")
    assert shaped["ca_next_action"] == (
        "read_bounded_result_by_ref_if_more_context_is_required")
    # S2 (B8 carve-out): the excerpt is bounded on the WIRE -- it is cut
    # at a UTF-8 character boundary so that its JSON-escaped byte
    # length (json.dumps with ensure_ascii=False, the shaping encoder)
    # is at most budget - 700 = 3396. The pre-B8 excerpt was cut by
    # characters (raw[:3396] == 4546 bytes) and overran the budget.
    assert shaped["excerpt"] == raw[:2536]
    assert len(shaped["excerpt"]) == 2536
    assert len(shaped["excerpt"].encode("utf-8")) == 3391
    assert (len(json.dumps(shaped["excerpt"], ensure_ascii=False)
                .encode("utf-8")) - 2) == 3396
    assert shaped["excerpt"].endswith("Đọc tiếng Vi")
    # The full payload is stored under the ref, byte-identical.
    assert srv._RESULT_REF_CACHE[ref] == raw
    # S2 store constants (B8 carve-out): 256 entries (was 64) with
    # access-LRU eviction, plus an 8 MB total-byte cap on the store.
    assert srv._RESULT_BUDGET_BYTES == 50000
    assert srv._RESULT_REF_MAX_ENTRIES == 256
    assert srv._RESULT_REF_MAX_BYTES == 8 * 1024 * 1024


def test_envelope_baseline_chunk_payload(tmp_path):
    raw = _envelope_raw()
    shaped = json.loads(srv._shape_tool_response_text(
        "read_file_range", raw, max_bytes=4096))
    ref = shaped["result_ref"]
    data = raw.encode("utf-8", errors="replace")

    def _chunk(offset: int, max_bytes: int) -> dict:
        request = make_mocked_request(
            "GET", f"/api/result/{ref}?offset={offset}&max_bytes={max_bytes}",
            match_info={"ref": ref})
        response = asyncio.run(srv.handle_result_ref(request))
        assert response.status == 200
        return json.loads(response.text)

    first = _chunk(0, 1030)
    assert set(first) == {
        "ok", "result_ref", "content_sha256", "total_bytes", "offset",
        "returned_bytes", "next_offset", "truncated", "content",
        "ca_next_action", "generation",
    }
    assert first["ok"] is True
    assert first["result_ref"] == ref
    assert first["total_bytes"] == len(data) == 13426
    assert first["offset"] == 0
    # S2 (B8 carve-out): chunking is aligned to UTF-8 character
    # boundaries, so the chunk end moves back from 1030 to 1029 -- the
    # last complete character at or before offset + max_bytes -- and
    # next_offset is that aligned offset. The pre-B8 byte-offset chunk
    # split a character here and produced U+FFFD in both neighbours.
    assert first["returned_bytes"] == 1029
    assert first["next_offset"] == 1029
    assert first["truncated"] is True
    assert first["ca_next_action"] == "read_next_offset"
    assert first["generation"] == srv.SERVER_GENERATION
    assert first["content"] == data[0:1029].decode("utf-8")
    assert len(first["content"]) == 774
    assert not first["content"].endswith("\ufffd")
    # S2 integrity (B8 carve-out addition): content_sha256 is the hex
    # sha256 of the full stored payload bytes -- the exact bytes being
    # chunked, which for a shaped response is also the ref value.
    assert first["content_sha256"] == hashlib.sha256(data).hexdigest()
    assert first["content_sha256"] == ref

    # Asking for offset 1030 lands inside the character that starts at
    # 1029: the server moves the start back to 1029 and reports the
    # offset actually used in the response.
    second = _chunk(1030, 1030)
    assert second["offset"] == 1029
    assert second["returned_bytes"] == 1030
    assert second["next_offset"] == 2059
    assert second["truncated"] is True
    assert second["content"] == data[1029:2059].decode("utf-8")
    assert second["content"].startswith("Đọc")
    assert not second["content"].startswith("\ufffd")
    assert second["content_sha256"] == hashlib.sha256(data).hexdigest()
