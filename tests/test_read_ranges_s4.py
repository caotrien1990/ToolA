"""S4 ranges[] tests for the ToolA read path (read-limits campaign).

SOT: PLAN.md (toola-read-limits-20261106) section 3.2 (ranges[] B3-B7:
ordered whole-call gates, NORMALIZE, SINGLE-BLOCK delivery + metadata,
seed-once B5, interactions B7), section 3.4 (client guardrail recorded
in the registry description) and section 4 step S4.

Like the S1 golden / S3 budget files, these run the handler in-process
through the real tool-call surface (code_tools.read_file_range, which
is the fs shim over orchestrator.tools.read.context) on tmp fixtures --
never against the live daemon. The MCP parity tests pin the named
wrapper in orchestrator/server/mcp_stdio.py by AST (the A33b pattern)
plus a behavioural call: the extracted wrapper body runs against a
proxy stub that forwards its payload into code_tools.read_file_range.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import hashlib
import inspect
import json
import math
import textwrap
from pathlib import Path

import pytest

import code_tools
import surgical_mapper
from orchestrator.server import mcp_stdio
from orchestrator.tools.read import adaptive

LINES = [f"line {i:04d}" for i in range(1, 101)]
TEXT = "\n".join(LINES) + "\n"
LONG_LINES = ["x" * 100 for _ in range(20)]
LONG_TEXT = "\n".join(LONG_LINES) + "\n"


def _clear_read_cache() -> None:
    with adaptive._CACHE_COND:
        adaptive._CACHE_BY_PATH.clear()
        adaptive._CACHE_BY_HASH.clear()
        adaptive._INFLIGHT.clear()
        adaptive._CACHE_BYTES = 0


@pytest.fixture(autouse=True)
def _seed_stub(monkeypatch):
    """Deterministic seed seam (the S1/S3 pattern) with a call counter,
    so seed-once is asserted, not assumed."""
    calls = {"n": 0}

    def _upsert(*args, **kwargs):
        calls["n"] += 1
        return True

    monkeypatch.setattr(
        code_tools, "_read_token_upsert", _upsert, raising=False)
    _clear_read_cache()
    yield calls
    _clear_read_cache()


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return path


def _read(tmp_path: Path, name: str, **kwargs) -> dict:
    raw = asyncio.run(code_tools.read_file_range(
        name, project_path=str(tmp_path), **kwargs))
    return json.loads(raw)


def _estimated(lines: list[str], start: int) -> int:
    return sum(
        math.ceil(len(f"{start + i}: {line}\n".encode("utf-8")) / 4.0)
        for i, line in enumerate(lines))


def _rng(start, end):
    return {"start_line": start, "end_line": end}


# ---------------------------------------------------------------------------
# NORMALIZE: merge / dedupe / clamp / per-range errors
# ---------------------------------------------------------------------------

def test_merge_overlapping(tmp_path):
    _write(tmp_path, "f.txt", TEXT)
    body = _read(tmp_path, "f.txt", ranges=[_rng(10, 20), _rng(15, 25)])
    assert body["ok"] is True
    assert body["ranges"] == [{
        "start": 10, "end": 25, "shown_lines": 16,
        "estimated_tokens": _estimated(LINES[9:25], 10)}]
    assert body["result"].startswith("--- RANGE 1/1: L10-L25 ---\n10: line 0010")
    assert body["shown"] == "10-25"
    assert body["truncated"] is False
    assert body["range_errors"] == []
    assert body["skipped_ranges"] == []


def test_merge_adjacent(tmp_path):
    # First proof of the adjacency branch: next.start == prev.end + 1 merges.
    _write(tmp_path, "f.txt", TEXT)
    body = _read(tmp_path, "f.txt", ranges=[_rng(10, 20), _rng(21, 30)])
    assert [ (r["start"], r["end"]) for r in body["ranges"]] == [(10, 30)]
    assert body["result"].startswith("--- RANGE 1/1: L10-L30 ---")


def test_dedupe_and_sort_with_normalized_markers(tmp_path):
    _write(tmp_path, "f.txt", TEXT)
    body = _read(tmp_path, "f.txt",
                 ranges=[_rng(50, 60), _rng(10, 20), _rng(15, 25), _rng(10, 20)])
    assert [(r["start"], r["end"]) for r in body["ranges"]] == [(10, 25), (50, 60)]
    blocks = body["result"].split("\n--- RANGE ")
    assert blocks[0].startswith("--- RANGE 1/2: L10-L25 ---")
    assert "2/2: L50-L60 ---" in body["result"]
    assert body["shown"] == "10-60"
    assert body["selection"] == {
        "ok": True, "scope": "range", "name": None,
        "start_line": 10, "end_line": 60, "source": "line_range"}
    digest = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
    assert body["content_hash"] == digest
    assert body["range_ref"] == f"f.txt@{digest[:12]}:L10-L60"


def test_clamp_end_past_eof(tmp_path):
    _write(tmp_path, "f.txt", TEXT)
    body = _read(tmp_path, "f.txt", ranges=[_rng(95, 150)])
    assert body["ranges"] == [{
        "start": 95, "end": 100, "shown_lines": 6, "clamped": True,
        "estimated_tokens": _estimated(LINES[94:100], 95)}]
    assert "100: line 0100" in body["result"]


def test_past_eof_is_distinct_and_does_not_fail_call(tmp_path):
    _write(tmp_path, "f.txt", TEXT)
    body = _read(tmp_path, "f.txt", ranges=[_rng(5, 6), _rng(101, 110)])
    assert body["ok"] is True
    assert [(r["start"], r["end"]) for r in body["ranges"]] == [(5, 6)]
    assert body["range_errors"] == [
        {"index": 1, "start": 101, "end": 110, "error": "PAST_EOF"}]
    assert body["truncated"] is True


def test_invalid_and_empty_range_errors(tmp_path):
    _write(tmp_path, "f.txt", TEXT)
    body = _read(tmp_path, "f.txt", ranges=[
        _rng(0, 5), _rng(20, 10), {"start_line": 7}, {"end_line": 9},
        {"start_line": True, "end_line": 9}, 42, None, [1, 2], _rng(1, 2)])
    assert body["ok"] is True
    assert [(r["start"], r["end"]) for r in body["ranges"]] == [(1, 2)]
    errors = body["range_errors"]
    assert [e["error"] for e in errors] == [
        "INVALID_RANGE", "EMPTY_RANGE", "INVALID_RANGE", "INVALID_RANGE",
        "INVALID_RANGE", "INVALID_RANGE", "INVALID_RANGE", "INVALID_RANGE"]
    by_index = {e["index"]: e for e in errors}
    assert by_index[0] == {"index": 0, "start": 0, "end": 5, "error": "INVALID_RANGE"}
    assert by_index[1] == {"index": 1, "start": 20, "end": 10, "error": "EMPTY_RANGE"}
    assert by_index[2] == {"index": 2, "start": 7, "end": None, "error": "INVALID_RANGE"}
    assert by_index[3] == {"index": 3, "start": None, "end": 9, "error": "INVALID_RANGE"}
    assert by_index[4]["error"] == "INVALID_RANGE" and by_index[4]["start"] is True
    assert by_index[5] == {"index": 5, "start": None, "end": None, "error": "INVALID_RANGE"}
    assert by_index[6] == {"index": 6, "start": None, "end": None, "error": "INVALID_RANGE"}
    assert by_index[7] == {"index": 7, "start": None, "end": None, "error": "INVALID_RANGE"}
    assert body["truncated"] is True


def test_single_document_load_for_many_ranges(tmp_path, monkeypatch):
    _write(tmp_path, "f.txt", TEXT)
    real = adaptive.get_document
    loads = {"n": 0}

    def counting(fp, project_path=None, **kwargs):
        loads["n"] += 1
        return real(fp, project_path, **kwargs)

    monkeypatch.setattr(adaptive, "get_document", counting)
    body = _read(tmp_path, "f.txt",
                 ranges=[_rng(1, 5), _rng(20, 25), _rng(90, 95)])
    assert body["ok"] is True and len(body["ranges"]) == 3
    assert loads["n"] == 1


# ---------------------------------------------------------------------------
# Whole-call gates + pinned order
# ---------------------------------------------------------------------------

def test_too_many_raw_elements(tmp_path):
    _write(tmp_path, "f.txt", TEXT)
    body = _read(tmp_path, "f.txt", ranges=[_rng(1, 1)] * 17)
    assert body["ok"] is False
    assert body["error_name"] == "RANGES_TOO_MANY"
    assert "16" in body["error"]


def test_empty_array(tmp_path):
    _write(tmp_path, "f.txt", TEXT)
    body = _read(tmp_path, "f.txt", ranges=[])
    assert body["ok"] is False
    assert body["error_name"] == "RANGES_EMPTY"


@pytest.mark.parametrize("bad", [5, "x", {"start_line": 1, "end_line": 2}])
def test_ranges_not_an_array(tmp_path, bad):
    _write(tmp_path, "f.txt", TEXT)
    body = _read(tmp_path, "f.txt", ranges=bad)
    assert body["ok"] is False
    assert body["error_name"] == "RANGES_INVALID_TYPE"


def test_ranges_null_is_legacy(tmp_path):
    _write(tmp_path, "f.txt", TEXT)
    body = _read(tmp_path, "f.txt", ranges=None)
    assert body["ok"] is True
    assert body["shown"] == "1-100"
    assert "ranges" not in body and "range_errors" not in body


def test_legacy_conflicts_judged_on_coerced_values(tmp_path):
    _write(tmp_path, "f.txt", TEXT)
    base = [_rng(1, 5)]
    conflict = _read(tmp_path, "f.txt", ranges=base, max_lines=500)
    assert conflict["error_name"] == "RANGES_WITH_LEGACY_PARAMS"
    conflict = _read(tmp_path, "f.txt", ranges=base, end_line=50)
    assert conflict["error_name"] == "RANGES_WITH_LEGACY_PARAMS"
    conflict = _read(tmp_path, "f.txt", ranges=base, start_line=2)
    assert conflict["error_name"] == "RANGES_WITH_LEGACY_PARAMS"
    for kwargs in ({"max_lines": 0}, {"max_lines": 200},
                   {"start_line": 1}, {"start_line": 0}, {"start_line": None}):
        body = _read(tmp_path, "f.txt", ranges=base, **kwargs)
        assert body["ok"] is True, kwargs
        assert [(r["start"], r["end"]) for r in body["ranges"]] == [(1, 5)]


def test_scope_gate_on_resolved_value(tmp_path):
    _write(tmp_path, "f.txt", TEXT)
    base = [_rng(1, 5)]
    assert _read(tmp_path, "f.txt", ranges=base, scope="RANGE")["ok"] is True
    assert _read(tmp_path, "f.txt", ranges=base, scope="range ")["error_name"] == "RANGES_SCOPE_UNSUPPORTED_V1"
    assert _read(tmp_path, "f.txt", ranges=base, scope="file_summary")["error_name"] == "RANGES_SCOPE_UNSUPPORTED_V1"
    assert _read(tmp_path, "f.txt", ranges=base, symbol="")["ok"] is True
    assert _read(tmp_path, "f.txt", ranges=base, symbol=None)["ok"] is True
    assert _read(tmp_path, "f.txt", ranges=base, section=None)["ok"] is True
    assert _read(tmp_path, "f.txt", ranges=base, symbol="alpha")["error_name"] == "RANGES_SCOPE_UNSUPPORTED_V1"
    assert _read(tmp_path, "f.txt", ranges=base, section="## X")["error_name"] == "RANGES_SCOPE_UNSUPPORTED_V1"


def test_pipeline_with_ranges_rejected_by_name(tmp_path):
    _write(tmp_path, "f.txt", TEXT)
    body = _read(tmp_path, "f.txt", ranges=[_rng(1, 5)], pipeline=["next_range"])
    assert body["ok"] is False
    assert body["error_name"] == "RANGES_PIPELINE_UNSUPPORTED_V1"


def test_whole_call_gate_order(tmp_path, monkeypatch):
    _write(tmp_path, "f.txt", TEXT)
    # (1) disabled beats even a garbage ranges value.
    monkeypatch.setenv("ORCHESTRATOR_READ_RANGES_ENABLED", "0")
    body = _read(tmp_path, "f.txt", ranges=5)
    assert body["error_name"] == "RANGES_DISABLED"
    monkeypatch.delenv("ORCHESTRATOR_READ_RANGES_ENABLED")
    # (4) scope beats (5) pipeline.
    body = _read(tmp_path, "f.txt", ranges=[_rng(1, 2)],
                 scope="file_summary", pipeline=["next_range"])
    assert body["error_name"] == "RANGES_SCOPE_UNSUPPORTED_V1"
    # (4) scope beats (7) too-many.
    body = _read(tmp_path, "f.txt", ranges=[_rng(1, 1)] * 17,
                 scope="file_summary")
    assert body["error_name"] == "RANGES_SCOPE_UNSUPPORTED_V1"


def test_kill_switch_value_semantics(tmp_path, monkeypatch):
    _write(tmp_path, "f.txt", TEXT)
    for value in ("0", "false", " FALSE ", "False"):
        monkeypatch.setenv("ORCHESTRATOR_READ_RANGES_ENABLED", value)
        body = _read(tmp_path, "f.txt", ranges=[_rng(1, 2)])
        assert body["error_name"] == "RANGES_DISABLED", value
    for value in ("off", "no", "00", "1", ""):
        monkeypatch.setenv("ORCHESTRATOR_READ_RANGES_ENABLED", value)
        body = _read(tmp_path, "f.txt", ranges=[_rng(1, 2)])
        assert body["ok"] is True, value
    monkeypatch.delenv("ORCHESTRATOR_READ_RANGES_ENABLED")
    assert _read(tmp_path, "f.txt", ranges=[_rng(1, 2)])["ok"] is True


# ---------------------------------------------------------------------------
# Zero-delivered shape
# ---------------------------------------------------------------------------

def test_zero_delivered_shape_on_empty_file(tmp_path):
    _write(tmp_path, "empty.txt", "")
    body = _read(tmp_path, "empty.txt", ranges=[_rng(1, 1)])
    assert body["ok"] is True
    assert body["result"] == ""
    assert body["ranges"] == []
    assert body["shown"] is None
    assert body["selection"] is None
    assert body["range_ref"] is None
    assert body["estimated_tokens"] == 0
    assert body["truncated"] is True
    assert body["read_token_seeded"] is False
    assert body["range_errors"] == [
        {"index": 0, "start": 1, "end": 1, "error": "PAST_EOF"}]
    assert body["skipped_ranges"] == []
    assert "line_cut" not in body


def test_zero_delivered_shape_with_budget(tmp_path):
    _write(tmp_path, "empty.txt", "")
    body = _read(tmp_path, "empty.txt", ranges=[_rng(1, 1)], token_budget=5000)
    assert body["ok"] is True
    assert body["skipped_ranges"] == []
    assert body["budget_mode"] is True
    assert body["token_budget_effective"] == 5000
    assert "line_cut" not in body


def test_all_ranges_invalid_without_force_is_zero_shape(tmp_path):
    _write(tmp_path, "f.txt", TEXT)
    body = _read(tmp_path, "f.txt", ranges=[_rng(0, 5)])
    assert body["ok"] is True
    assert body["ranges"] == [] and body["shown"] is None
    assert body["read_token_seeded"] is False


# ---------------------------------------------------------------------------
# Budget interaction
# ---------------------------------------------------------------------------

def test_no_budget_keys_when_budget_not_declared(tmp_path):
    _write(tmp_path, "f.txt", TEXT)
    body = _read(tmp_path, "f.txt", ranges=[_rng(1, 5), _rng(10, 12)])
    assert body["ok"] is True and len(body["ranges"]) == 2
    assert "budget_mode" not in body
    assert "token_budget_effective" not in body


def test_shared_budget_skips_later_range(tmp_path):
    _write(tmp_path, "long.txt", LONG_TEXT)
    # Each numbered line costs ceil((3 + 100 + 1) / 4) = 26 tokens.
    body = _read(tmp_path, "long.txt",
                 ranges=[_rng(1, 2), _rng(10, 12)], token_budget=26)
    assert body["budget_mode"] is True
    assert body["token_budget_effective"] == 26
    assert body["ranges"] == [{
        "start": 1, "end": 2, "shown_lines": 1,
        "estimated_tokens": 26, "truncated": True}]
    assert body["skipped_ranges"] == [
        {"start": 10, "end": 12, "reason": "token_budget"}]
    assert body["truncated"] is True


def test_line_cut_inside_ranges(tmp_path):
    _write(tmp_path, "long.txt", LONG_TEXT)
    body = _read(tmp_path, "long.txt", ranges=[_rng(1, 3)], token_budget=10)
    assert body["line_cut"] is True
    assert body["ranges"][0]["truncated"] is True
    assert body["ranges"][0]["estimated_tokens"] == 10
    assert body["result"].splitlines()[1] == "1: " + "x" * 36


# ---------------------------------------------------------------------------
# Seed (B5)
# ---------------------------------------------------------------------------

def test_seed_once_when_ranges_delivered(tmp_path, _seed_stub):
    _write(tmp_path, "f.txt", TEXT)
    body = _read(tmp_path, "f.txt", ranges=[_rng(1, 5), _rng(50, 60)])
    assert body["read_token_seeded"] is True
    assert _seed_stub["n"] == 1


def test_seed_when_delivery_is_partial(tmp_path, _seed_stub):
    _write(tmp_path, "long.txt", LONG_TEXT)
    body = _read(tmp_path, "long.txt", ranges=[_rng(1, 5)], token_budget=26)
    assert body["ranges"][0]["shown_lines"] == 1
    assert body["read_token_seeded"] is True
    assert _seed_stub["n"] == 1


def test_no_seed_when_document_unstable(tmp_path, monkeypatch, _seed_stub):
    _write(tmp_path, "f.txt", TEXT)
    real = adaptive.get_document

    def unstable(fp, project_path=None, **kwargs):
        doc, meta = real(fp, project_path, **kwargs)
        return dataclasses.replace(doc, stable=False), meta

    monkeypatch.setattr(adaptive, "get_document", unstable)
    body = _read(tmp_path, "f.txt", ranges=[_rng(1, 5)])
    assert body["ok"] is True
    assert body["read_token_seeded"] is False
    assert _seed_stub["n"] == 0


def test_reseed_failed_dominates_zero_shape(tmp_path, monkeypatch):
    _write(tmp_path, "f.txt", TEXT)
    monkeypatch.setattr(
        code_tools, "_read_token_upsert", lambda *a, **k: False, raising=False)
    body = _read(tmp_path, "f.txt", ranges=[_rng(0, 5)], force_reseed=True)
    assert body["ok"] is False
    assert body["error_name"] == "RESEED_FAILED"


def test_write_fix_after_ranges_on_disjoint_ranges(tmp_path, monkeypatch):
    # Real token store + real seed (the contention-test fixture pattern):
    # the ranges read must seed the WHOLE-file token, so a write_fix in
    # the second, disjoint range passes the freshness guard. The target
    # is .txt on purpose: write_fix on .py spawns a ruff subprocess for
    # validation, and long pytest sessions on this machine intermittently
    # fail that spawn with WinError 50/6 (the S3-documented artifact);
    # the token guard under test is extension-agnostic.
    monkeypatch.undo()
    db_path = tmp_path / "sessions_s4.db"
    monkeypatch.setattr(code_tools, "_SESSIONS_DB", db_path)
    code_tools._sync_session_db_alias()
    code_tools._init_session_db()
    try:
        root = tmp_path / "proj"
        root.mkdir()
        (root / ".git").mkdir()
        target = root / "mod.txt"
        target.write_bytes(
            ("\n".join(f"value = {i}" for i in range(1, 101)) + "\n").encode())
        surgical_mapper.ensure_minimal_index(str(root))
        body = json.loads(asyncio.run(code_tools.read_file_range(
            "mod.txt", project_path=str(root),
            ranges=[_rng(10, 20), _rng(80, 90)])))
        assert body["read_token_seeded"] is True, body
        out = json.loads(asyncio.run(code_tools.write_fix(
            "mod.txt", 85, 85, "value = 999", project_path=str(root))))
        assert out["ok"] is True, out
        assert target.read_text(encoding="utf-8").splitlines()[84] == "value = 999"
    finally:
        monkeypatch.undo()
        code_tools._sync_session_db_alias()


# ---------------------------------------------------------------------------
# Registry declaration + MCP parity (R-05)
# ---------------------------------------------------------------------------

def test_registry_declares_ranges_with_client_guardrail():
    schema = next(x["function"] for x in code_tools.TOOL_SCHEMAS
                  if x["function"]["name"] == "read_file_range")
    props = schema["parameters"]["properties"]
    assert props["ranges"]["type"] == "array"
    description = props["ranges"]["description"]
    for needle in ("result_ref", "next_offset", "content_sha256",
                   "64,000", "20,000", "estimator"):
        assert needle in description, needle


def _mcp_wrapper_node():
    source = inspect.getsource(mcp_stdio.run_mcp_stdio)
    tree = ast.parse(textwrap.dedent(source))
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "read_file_range")


def test_mcp_wrapper_declares_and_forwards_ranges():
    node = _mcp_wrapper_node()
    params = {p.arg for p in list(node.args.args) + list(node.args.kwonlyargs)}
    assert "ranges" in params
    proxy_calls = [sub for sub in ast.walk(node)
                   if isinstance(sub, ast.Call)
                   and isinstance(sub.func, ast.Name)
                   and sub.func.id == "_mcp_proxy_call"]
    assert proxy_calls
    payload = proxy_calls[0].args[1]
    keys = {k.value for k in payload.keys if isinstance(k, ast.Constant)}
    assert "ranges" in keys


def test_mcp_wrapper_call_reaches_handler(tmp_path):
    _write(tmp_path, "f.txt", TEXT)
    node = _mcp_wrapper_node()
    node.decorator_list = []
    captured = {}

    def fake_proxy(tool_name, payload):
        captured["tool"] = tool_name
        captured["payload"] = payload
        return asyncio.run(code_tools.read_file_range(**payload))

    namespace = {"_mcp_proxy_call": fake_proxy}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<mcp_wrapper>", "exec"),
         namespace)
    raw = namespace["read_file_range"](
        "f.txt", project_path=str(tmp_path), ranges=[_rng(3, 4)])
    body = json.loads(raw)
    assert captured["tool"] == "read_file_range"
    assert captured["payload"]["ranges"] == [_rng(3, 4)]
    assert body["ok"] is True
    assert [(r["start"], r["end"]) for r in body["ranges"]] == [(3, 4)]
