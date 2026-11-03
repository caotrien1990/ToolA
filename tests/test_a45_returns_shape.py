"""A45: Point 16 (Q13) returns-shape mechanism -- Sprint C1 mechanism tests.

Pins the warn-only output-shape leaf (orchestrator/core/returns_shape.py), its
accounting read-back (channel 2), the served-schema strip at all three
enrichment paths, the dispatch-window identity (the checked object IS the
recorded full answer), and the window-journal mechanics.

Grounding notes (read from the live leaf before writing, per brief):
- rs.check_returns(tool_name, answer, raw_len) takes NO schemas= parameter in
  the landed leaf; it resolves the returns block through
  rs.resolve_returns(tool_name), which reads the ASSEMBLED
  code_tools.TOOL_SCHEMAS list.  The synthetic-schema tests below therefore
  inject schemas by monkeypatching code_tools.TOOL_SCHEMAS -- the real
  resolution path, not a stub of it.
- handle_tools IS exercised behaviourally below (fake request, one-entry
  TOOL_SCHEMAS/TOOL_MAP), so all three served-strip paths are behavioural.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from aiohttp import StreamReader
from aiohttp.test_utils import make_mocked_request

import code_tools as ct
import orchestrator_server as srv
from orchestrator.core import call_cache
from orchestrator.core import returns_shape as rs

_SERVER_SRC = Path(srv.__file__).read_text(encoding="utf-8", errors="ignore")
_REPO_ROOT = Path(srv.__file__).resolve().parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _audited_block(body: dict, depth_D: int) -> dict:
    """A returns block whose x-audit proves it was audited (canonical sha)."""
    block = dict(body)
    block["x-audit"] = {
        "status": "audited",
        "schema_sha256": rs.canonical_schema_sha(block),
        "depth_D": depth_D,
    }
    return block


def _schemas_for(tool: str, block) -> list:
    """One assembled-schema entry in the shape resolve_returns scans."""
    fn = {"name": tool, "parameters": {"type": "object", "properties": {}}}
    if block is not None:
        fn["returns"] = block
    return [{"type": "function", "function": fn}]


def _nest(depth: int) -> dict:
    """A dict whose rs.depth_of() is exactly `depth`."""
    value = {"leaf": 1}
    while rs.depth_of(value) < depth:
        value = {"child": value}
    return value


def _rows(tool: str, verdict: str, n: int) -> list:
    return [{"tool": tool, "returns_shape_verdict": verdict} for _ in range(n)]


@pytest.fixture
def install_schemas(monkeypatch):
    def _install(tool: str, block):
        monkeypatch.setattr(ct, "TOOL_SCHEMAS", _schemas_for(tool, block))
    return _install


# ---------------------------------------------------------------------------
# 1. Leaf verdict logic (pure; synthetic schemas via the assembled list)
# ---------------------------------------------------------------------------

class TestLeafVerdictLogic:
    def test_disabled_tool_is_skipped_disabled(self, monkeypatch):
        monkeypatch.setattr(rs, "DISABLED_TOOLS", frozenset({"tool_x"}))
        assert rs.check_returns("tool_x", {"ok": True}, 10) == (
            "skipped", "disabled", [])

    def test_disabled_check_precedes_non_dict(self, monkeypatch):
        monkeypatch.setattr(rs, "DISABLED_TOOLS", frozenset({"tool_x"}))
        assert rs.check_returns("tool_x", "not a dict", 10) == (
            "skipped", "disabled", [])

    def test_non_dict_answer_is_skipped_non_dict(self):
        assert rs.check_returns("tool_x", [1, 2, 3], 10) == (
            "skipped", "non_dict", [])

    def test_budget_error_name_is_dispatcher_payload(self):
        answer = {"ok": False, "error_name": "SERVER_BUDGET_EXCEEDED"}
        assert rs.check_returns("tool_x", answer, 10) == (
            "skipped", "dispatcher_payload", [])

    def test_partial_plus_stopped_by_is_dispatcher_payload(self):
        answer = {"ok": False, "partial": True, "stopped_by": "budget"}
        assert rs.check_returns("tool_x", answer, 10) == (
            "skipped", "dispatcher_payload", [])

    def test_partial_without_stopped_by_falls_through(self, install_schemas):
        install_schemas("tool_x", None)  # entry exists, no returns block
        answer = {"ok": False, "partial": True}
        assert rs.check_returns("tool_x", answer, 10) == (
            "skipped", "no_schema", [])

    def test_tool_with_no_returns_is_skipped_no_schema(self, install_schemas):
        install_schemas("tool_x", None)
        assert rs.check_returns("tool_x", {"ok": True}, 10) == (
            "skipped", "no_schema", [])

    def test_sha_mismatched_block_is_skipped_schema_invalid(self, install_schemas):
        block = _audited_block({"type": "object"}, 4)
        block["x-audit"]["schema_sha256"] = "0" * 64
        install_schemas("tool_x", block)
        assert rs.check_returns("tool_x", {"ok": True}, 10) == (
            "skipped", "schema_invalid", [])

    def test_raw_len_over_cap_is_skipped_too_large(self, install_schemas):
        install_schemas("tool_x", _audited_block({"type": "object"}, 4))
        assert rs.check_returns("tool_x", {"ok": True}, 300_000) == (
            "skipped", "too_large", [])

    def test_node_budget_overflow_is_skipped_node_budget(self, install_schemas):
        install_schemas("tool_x", _audited_block({"type": "object"}, 4))
        answer = {"items": list(range(2500))}  # 2502 nodes, depth 2
        assert rs.check_returns("tool_x", answer, 10) == (
            "skipped", "node_budget", [])

    def test_deep_answer_vs_small_depth_d_is_skipped_depth(self, install_schemas):
        # SIM-1a shape: depth_D=1 -> cap = max(8, 1+4) = 8; answer depth 10.
        install_schemas("tool_x", _audited_block({"type": "object"}, 1))
        assert rs.check_returns("tool_x", _nest(10), 10) == (
            "skipped", "depth", [])

    def test_same_answer_vs_deepest_variant_depth_d_passes(self, install_schemas):
        # SIM-1b shape: depth_D=12 -> cap = min(32, max(8, 16)) = 16 >= 10.
        install_schemas("tool_x", _audited_block({"type": "object"}, 12))
        assert rs.check_returns("tool_x", _nest(10), 10) == ("pass", None, [])

    def test_sim2_malformed_answer_warns_with_exact_mismatches(self, install_schemas):
        body = {
            "type": "object",
            "required": ["ok", "result", "total_lines"],
            "properties": {
                "ok": {"type": "boolean"},
                "result": {"type": "string"},
                "total_lines": {"type": "integer"},
            },
        }
        install_schemas("tool_x", _audited_block(body, 4))
        verdict, reason, mismatches = rs.check_returns(
            "tool_x", {"ok": "yes", "result": 42}, 10)
        assert verdict == "warn" and reason is None
        assert mismatches == [
            "missing:total_lines",
            "type:ok=string!=boolean",
            "type:result=integer!=string",
        ]

    def test_mismatch_cap_is_five(self, install_schemas):
        body = {
            "type": "object",
            "required": ["a", "b", "c", "d", "e", "f", "g"],
            "properties": {},
        }
        install_schemas("tool_x", _audited_block(body, 4))
        verdict, _, mismatches = rs.check_returns("tool_x", {}, 10)
        assert verdict == "warn"
        assert len(mismatches) == 5
        assert all(m.startswith("missing:") for m in mismatches)

    def test_bool_where_integer_expected_warns(self, install_schemas):
        body = {"type": "object", "properties": {"n": {"type": "integer"}}}
        install_schemas("tool_x", _audited_block(body, 4))
        verdict, _, mismatches = rs.check_returns("tool_x", {"n": True}, 10)
        assert verdict == "warn"
        assert mismatches == ["type:n=boolean!=integer"]

    def test_integer_where_number_expected_passes(self, install_schemas):
        body = {"type": "object", "properties": {"n": {"type": "number"}}}
        install_schemas("tool_x", _audited_block(body, 4))
        assert rs.check_returns("tool_x", {"n": 3}, 10) == ("pass", None, [])

    def test_additional_properties_false_flags_extra_key(self, install_schemas):
        body = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "additionalProperties": False,
        }
        install_schemas("tool_x", _audited_block(body, 4))
        verdict, _, mismatches = rs.check_returns(
            "tool_x", {"ok": True, "surprise": 1}, 10)
        assert verdict == "warn"
        assert mismatches == ["additional:surprise"]

    def test_anyof_with_a_matching_branch_passes(self, install_schemas):
        body = {
            "type": "object",
            "properties": {
                "v": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            },
        }
        install_schemas("tool_x", _audited_block(body, 4))
        assert rs.check_returns("tool_x", {"v": 7}, 10) == ("pass", None, [])

    def test_anyof_with_no_matching_branch_is_exactly_one_mismatch(
            self, install_schemas):
        body = {
            "type": "object",
            "properties": {
                "v": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            },
        }
        install_schemas("tool_x", _audited_block(body, 4))
        verdict, _, mismatches = rs.check_returns("tool_x", {"v": True}, 10)
        assert verdict == "warn"
        assert mismatches == ["type:v=boolean!=string"]


# ---------------------------------------------------------------------------
# 2. schema_state / canonical sha
# ---------------------------------------------------------------------------

class TestSchemaStateAndCanonicalSha:
    def test_correctly_built_block_is_audited(self):
        block = _audited_block(
            {"type": "object", "properties": {"ok": {"type": "boolean"}}}, 4)
        assert rs.schema_state(block) == "audited"

    def test_status_not_audited_is_invalid(self):
        block = _audited_block({"type": "object"}, 4)
        block["x-audit"]["status"] = "draft"
        assert rs.schema_state(block) == "invalid"

    def test_missing_depth_d_is_invalid(self):
        block = {"type": "object"}
        block["x-audit"] = {
            "status": "audited",
            "schema_sha256": rs.canonical_schema_sha(block),
        }
        assert rs.schema_state(block) == "invalid"

    def test_disallowed_keyword_is_invalid(self):
        body = {
            "type": "object",
            "properties": {"v": {"type": "string", "enum": ["a", "b"]}},
        }
        assert rs.schema_state(_audited_block(body, 4)) == "invalid"

    def test_anyof_with_three_branches_is_invalid(self):
        body = {
            "type": "object",
            "properties": {
                "v": {"anyOf": [
                    {"type": "string"}, {"type": "integer"},
                    {"type": "boolean"}]},
            },
        }
        assert rs.schema_state(_audited_block(body, 4)) == "invalid"

    def test_canonical_sha_is_stable_across_key_ordering(self):
        block_a = {
            "type": "object",
            "properties": {
                "a": {"type": "string"}, "b": {"type": "integer"}},
            "required": ["a"],
        }
        block_b = {
            "required": ["a"],
            "properties": {
                "b": {"type": "integer"}, "a": {"type": "string"}},
            "type": "object",
        }
        assert rs.canonical_schema_sha(block_a) == rs.canonical_schema_sha(block_b)

    def test_canonical_sha_changes_when_a_property_type_changes(self):
        block_a = {"type": "object", "properties": {"a": {"type": "string"}}}
        block_b = {"type": "object", "properties": {"a": {"type": "integer"}}}
        assert rs.canonical_schema_sha(block_a) != rs.canonical_schema_sha(block_b)


# ---------------------------------------------------------------------------
# 3. Depth-skip gate (synthetic rows)
# ---------------------------------------------------------------------------

class TestDepthSkipGate:
    TOOL = "gate_tool"

    def test_nine_depth_of_120_fires(self):
        rows = _rows(self.TOOL, "skipped/depth", 9) + _rows(self.TOOL, "pass", 111)
        assert rs.depth_skip_gate(rows, self.TOOL) is True

    def test_one_depth_of_120_does_not_fire(self):
        rows = _rows(self.TOOL, "skipped/depth", 1) + _rows(self.TOOL, "pass", 119)
        assert rs.depth_skip_gate(rows, self.TOOL) is False

    def test_99_total_rows_with_50_depth_does_not_fire(self):
        rows = _rows(self.TOOL, "skipped/depth", 50) + _rows(self.TOOL, "pass", 49)
        assert rs.depth_skip_gate(rows, self.TOOL) is False

    def test_rows_without_verdict_are_not_counted(self):
        rows = _rows(self.TOOL, "skipped/depth", 50)
        rows += [{"tool": self.TOOL} for _ in range(60)]  # no verdict key
        assert rs.depth_skip_gate(rows, self.TOOL) is False

    def test_other_tools_rows_are_not_counted(self):
        rows = _rows("other_tool", "skipped/depth", 60)
        rows += _rows("other_tool", "pass", 60)
        assert rs.depth_skip_gate(rows, self.TOOL) is False
        assert rs.depth_skip_gate(rows, "other_tool") is True


# ---------------------------------------------------------------------------
# 4. Census (synthetic schema lists + one real-assembly invariant)
# ---------------------------------------------------------------------------

class TestCensus:
    def _schemas(self, blocks_by_tool: dict) -> list:
        return [
            {
                "type": "function",
                "function": {"name": name, "returns": block},
            }
            for name, block in blocks_by_tool.items()
        ]

    def test_ten_covered_of_38_eligible(self):
        tools = list(rs.RETURNS_SET_38)
        blocks = {t: _audited_block({"type": "object"}, 4) for t in tools[:10]}
        out = rs.compute_returns_coverage(
            schemas=self._schemas(blocks), usage_rows=[])
        assert out["eligible"] == 38
        assert out["covered"] == 10
        assert out["depth_suspect"] == 0
        assert out["disabled"] == 0

    def test_disabled_counts_once_even_when_depth_gate_trips(self, monkeypatch):
        tools = list(rs.RETURNS_SET_38)
        disabled_tool, other_a, other_b = tools[0], tools[1], tools[2]
        monkeypatch.setattr(rs, "DISABLED_TOOLS", frozenset({disabled_tool}))
        blocks = {
            t: _audited_block({"type": "object"}, 4)
            for t in (disabled_tool, other_a, other_b)
        }
        rows = _rows(disabled_tool, "skipped/depth", 9)
        rows += _rows(disabled_tool, "pass", 111)
        out = rs.compute_returns_coverage(
            schemas=self._schemas(blocks), usage_rows=rows)
        assert out["disabled"] == 1
        assert out["depth_suspect"] == 0, (
            "partition precedence: a disabled tool is counted ONCE, as disabled")
        assert out["covered"] == 2
        assert out["covered"] + out["depth_suspect"] + out["disabled"] <= out["eligible"]

    def test_depth_gate_tripped_audited_tool_is_depth_suspect(self):
        tools = list(rs.RETURNS_SET_38)
        suspect = tools[3]
        blocks = {suspect: _audited_block({"type": "object"}, 4)}
        rows = _rows(suspect, "skipped/depth", 9) + _rows(suspect, "pass", 111)
        out = rs.compute_returns_coverage(
            schemas=self._schemas(blocks), usage_rows=rows)
        assert out["depth_suspect"] == 1
        assert out["covered"] == 0
        assert out["covered"] + out["depth_suspect"] + out["disabled"] <= out["eligible"]

    def test_real_assembly_eligible_is_38_and_partition_holds(self):
        out = rs.compute_returns_coverage()
        assert out["eligible"] == 38
        assert out["covered"] + out["depth_suspect"] + out["disabled"] <= out["eligible"]


# ---------------------------------------------------------------------------
# 5. Accounting read-back (tmp USAGE_LOG via env redirection)
# ---------------------------------------------------------------------------

class TestAccountingReadBack:
    @pytest.fixture(autouse=True)
    def _tmp_usage_log(self, monkeypatch, tmp_path):
        self.log_path = tmp_path / "USAGE_LOG.jsonl"
        monkeypatch.setenv("ORCHESTRATOR_USAGE_LOG_FILE", str(self.log_path))

    def _rows(self) -> list:
        if not self.log_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_log_usage_carries_all_four_values_verbatim(self):
        assert ct._log_usage(
            "tool_a", {}, True, 1,
            returns_shape_verdict="warn",
            returns_shape_errors=["missing:x"],
            accounting_id="op-1", accounting_sequence=7,
        )
        [row] = self._rows()
        assert row["tool"] == "tool_a"
        assert row["returns_shape_verdict"] == "warn"
        assert row["returns_shape_errors"] == ["missing:x"]
        assert row["accounting_id"] == "op-1"
        assert row["accounting_sequence"] == 7

    def test_log_usage_without_new_kwargs_has_none_of_the_four_keys(self):
        assert ct._log_usage("tool_b", {}, True, 1)
        [row] = self._rows()
        for key in ("returns_shape_verdict", "returns_shape_errors",
                    "accounting_id", "accounting_sequence"):
            assert key not in row, key

    def _record(self, **overrides) -> dict:
        record = {
            "tool_name": "tool_c",
            "body": {},
            "status": "ok",
            "duration_ms": 3,
            "error_msg": "",
            "delivered": True,
            "client_gone": False,
            "error_name": "",
            "agent_id": "",
        }
        record.update(overrides)
        return record

    def test_persist_carries_verdict_id_and_sequence(self):
        srv._persist_post_call_accounting(self._record(
            returns_shape_verdict="skipped/depth",
            returns_shape_errors=[],
            accounting_id="op-2", accounting_sequence=11,
        ))
        [row] = self._rows()
        assert row["returns_shape_verdict"] == "skipped/depth"
        assert row["accounting_id"] == "op-2"
        assert row["accounting_sequence"] == 11

    def test_persist_without_verdict_keeps_id_and_sequence_only(self):
        srv._persist_post_call_accounting(self._record(
            returns_shape_verdict=None,
            returns_shape_errors=None,
            accounting_id="op-3", accounting_sequence=12,
        ))
        [row] = self._rows()
        assert row["accounting_id"] == "op-3"
        assert row["accounting_sequence"] == 12
        assert "returns_shape_verdict" not in row
        assert "returns_shape_errors" not in row


# ---------------------------------------------------------------------------
# 6. Served strip -- three paths: handle_tools, _enriched_tool_schema,
#    _tool_schema_payload (all exercised behaviourally)
# ---------------------------------------------------------------------------

class TestServedStrip:
    TOOL = "__a45_strip_probe__"

    @pytest.fixture(autouse=True)
    def _probe_schema(self, monkeypatch):
        self.entry = {
            "type": "function",
            "function": {
                "name": self.TOOL,
                "description": "a45 strip probe",
                "parameters": {"type": "object", "properties": {}},
                "returns": {"type": "object"},
            },
        }
        monkeypatch.setattr(ct, "TOOL_SCHEMAS", [self.entry])
        monkeypatch.setitem(ct.TOOL_MAP, self.TOOL, lambda **_kw: "{}")

    def test_the_three_paths_exist_by_name(self):
        for name in ("handle_tools", "_enriched_tool_schema",
                     "_tool_schema_payload"):
            assert callable(getattr(srv, name)), name

    def test_enriched_tool_schema_strips_and_source_keeps(self):
        out = srv._enriched_tool_schema(self.TOOL)
        assert out is not None
        assert "returns" not in out["function"]
        assert "returns" in self.entry["function"], (
            "the strip must hit the served copy only, never the registry entry")

    def test_tool_schema_payload_strips_and_source_keeps(self):
        payload, status = srv._tool_schema_payload(self.TOOL)
        assert status == 200
        assert "returns" not in payload["tool"]["function"]
        assert "returns" in self.entry["function"]

    def test_handle_tools_strips_and_source_keeps(self):
        async def scenario():
            request = make_mocked_request("GET", "/api/tools")
            return await srv.handle_tools(request)

        response = asyncio.run(scenario())
        assert response.status == 200
        served = json.loads(response.text)
        [entry] = [e for e in served
                   if e.get("function", {}).get("name") == self.TOOL]
        assert "returns" not in entry["function"]
        assert "returns" in self.entry["function"]


# ---------------------------------------------------------------------------
# 7. Window / identity integration (test_a43 dispatch machinery)
# ---------------------------------------------------------------------------

class TestWindowIdentityIntegration:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch, tmp_path):
        self.captured = []

        def _enqueue(record):
            self.captured.append(dict(record))
            return True

        monkeypatch.setattr(
            srv, "_POST_CALL_ACCOUNTING", SimpleNamespace(enqueue=_enqueue))
        self.tmp_path = tmp_path
        monkeypatch.setenv(
            "ORCHESTRATOR_USAGE_LOG_FILE", str(tmp_path / "USAGE_LOG.jsonl"))
        srv._R101_FIELDS.set(None)
        srv._R101_DISPATCH_FULL.set(None)
        call_cache._ENTRIES.clear()
        old_db = ct._SESSIONS_DB
        ct._SESSIONS_DB = tmp_path / "sessions.db"
        ct._sync_session_db_alias()
        ct._init_session_db()
        yield
        ct._SESSIONS_DB = old_db
        ct._sync_session_db_alias()
        srv._R101_FIELDS.set(None)
        srv._R101_DISPATCH_FULL.set(None)
        call_cache._ENTRIES.clear()

    async def _dispatch(self, tool: str, body: dict, op_id: str):
        reader = StreamReader(
            protocol=mock.Mock(), limit=2 ** 16,
            loop=asyncio.get_running_loop())
        reader.feed_data(json.dumps(body).encode("utf-8"))
        reader.feed_eof()
        request = make_mocked_request(
            "POST", f"/api/call/{tool}", payload=reader,
            match_info={"tool_name": tool},
            headers={"X-Operation-ID": op_id})
        return await srv._dispatch_tool_call_request(request)

    def _record_for(self, op_id: str) -> dict:
        matches = [r for r in self.captured
                   if r.get("accounting_id") == op_id]
        assert len(matches) == 1, (
            f"expected exactly one accounting record for {op_id}, "
            f"got {len(matches)}")
        return matches[0]

    def _journal_rows(self) -> list:
        path = self.tmp_path / "returns_shape_journal.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_read_file_range_verdict_identity_and_journal(self):
        op_id = "a45-op-window-1"
        body = {
            "project_path": str(_REPO_ROOT),
            "file_path": "orchestrator/core/returns_shape.py",
            "start_line": 1,
            "end_line": 5,
            "max_lines": 5,
            "include_next_ranges": False,
        }

        async def scenario():
            response = await self._dispatch("read_file_range", body, op_id)
            # Read the ContextVar INSIDE the task: a set() in a task context
            # does not propagate back to the caller after asyncio.run().
            return response, srv._R101_DISPATCH_FULL.get()

        response, full = asyncio.run(scenario())
        assert response.status == 200
        assert json.loads(response.text).get("ok") is True

        verdict, reason, _ = rs.check_returns("read_file_range", full, 0)
        expected = f"skipped/{reason}" if verdict == "skipped" else verdict
        record = self._record_for(op_id)
        assert record.get("returns_shape_verdict") == expected, (
            "identity: the verdict in the accounting record must be the "
            "verdict the leaf computes over the recorded full answer object")

        journal = self._journal_rows()
        assert len(journal) == 1
        assert journal[0]["accounting_id"] == op_id
        assert journal[0]["tool"] == "read_file_range"

    def test_non_json_tool_answer_is_422_dispatcher_payload_no_journal(
            self, monkeypatch):
        tool = "__a45_nonjson__"
        op_id = "a45-op-window-2"

        async def probe(**_kwargs) -> str:
            return "not json"

        monkeypatch.setitem(ct.TOOL_MAP, tool, probe)

        async def scenario():
            return await self._dispatch(tool, {}, op_id)

        response = asyncio.run(scenario())
        assert response.status == 422
        record = self._record_for(op_id)
        assert record.get("returns_shape_verdict") == "skipped/dispatcher_payload"
        assert [r for r in self._journal_rows()
                if r.get("accounting_id") == op_id] == [], (
            "the 422 early return is inside the verdict window's guard: "
            "no journal line may be written for it")

    def test_list_answer_is_skipped_non_dict_with_journal_line(
            self, monkeypatch):
        tool = "__a45_list__"
        op_id = "a45-op-window-3"

        async def probe(**_kwargs) -> str:
            return json.dumps([1, 2, 3])

        monkeypatch.setitem(ct.TOOL_MAP, tool, probe)

        async def scenario():
            return await self._dispatch(tool, {}, op_id)

        asyncio.run(scenario())
        record = self._record_for(op_id)
        assert record.get("returns_shape_verdict") == "skipped/non_dict"
        mine = [r for r in self._journal_rows()
                if r.get("accounting_id") == op_id]
        assert len(mine) == 1
        assert mine[0]["tool"] == tool


# ---------------------------------------------------------------------------
# 8. Journal mechanics
# ---------------------------------------------------------------------------

class TestJournalMechanics:
    def test_append_three_checkpoint_two(self, monkeypatch, tmp_path):
        monkeypatch.setenv(
            "ORCHESTRATOR_USAGE_LOG_FILE", str(tmp_path / "USAGE_LOG.jsonl"))
        assert rs.append_window_journal("a", "tool_a") is True
        assert rs.append_window_journal("b", "tool_b") is True
        assert rs.append_window_journal("c", "tool_c") is True

        moved = rs.checkpoint_journal({"a", "c"})
        assert moved == 2

        journal = tmp_path / "returns_shape_journal.jsonl"
        kept = [
            json.loads(line)
            for line in journal.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert [row["accounting_id"] for row in kept] == ["b"]

        archive = tmp_path / "returns_shape_journal.archive.jsonl"
        archived = [
            json.loads(line)
            for line in archive.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert [row["accounting_id"] for row in archived] == ["a", "c"]

    def test_checkpoint_with_no_covered_ids_moves_nothing(
            self, monkeypatch, tmp_path):
        monkeypatch.setenv(
            "ORCHESTRATOR_USAGE_LOG_FILE", str(tmp_path / "USAGE_LOG.jsonl"))
        assert rs.append_window_journal("a", "tool_a") is True
        assert rs.checkpoint_journal(set()) == 0
        journal = tmp_path / "returns_shape_journal.jsonl"
        lines = [line for line in
                 journal.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        assert len(lines) == 1

# ---------------------------------------------------------------------------
# PART 2 -- Sprint C2: merge gate over the REAL artifacts (no monkeypatching
# of the schema assembly) + the duties inherited from the G3b-C1 audit:
#   N1 -- re-pin the window/identity test content-sensitively now that
#         read_file_range carries an audited returns block (batch 1 landed);
#   N3 -- exact depth-gate boundaries (total == 100, ratio == 2%) and an
#         independent, definition-written depth function cross-checked
#         against rs.depth_of (C1's _nest built fixtures WITH rs.depth_of).
# Already pinned by C1 and deliberately NOT duplicated here:
#   - schema_state == "invalid" for a block missing depth_D
#     (TestSchemaStateAndCanonicalSha.test_missing_depth_d_is_invalid) --
#     section 10 below pins only the missing check_returns leg;
#   - depth_skip_gate traces 9/120 fires, 1/120 does not, and the <100
#     carry-forward at 99 rows (TestDepthSkipGate) -- section 12 pins only
#     the total == 100 boundary pair the audit found untested.
# ---------------------------------------------------------------------------

_AUTHORED_10 = (
    "read_file_range",
    "read_context_batch",
    "architecture_context_pack",
    "search_code",
    "find_symbol",
    "cbm_get_architecture",
    "ocr_map",
    "list_structure",
    "query_index",
    "get_rule_core_text",
)

_FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures" / "returns_shape"
_BASELINE_FILE = _REPO_ROOT / "orchestrator" / "core" / "returns_baseline.json"


def _fixtures_for(tool: str) -> list:
    """The tool's committed fixture files, sorted by name."""
    return sorted(_FIXTURES_DIR.glob(f"{tool}__*.json"))


def _load_json_file(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_raw_len(answer) -> int:
    """Byte length of the answer's canonical UTF-8 dump (fixture raw_len)."""
    return len(json.dumps(
        answer, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8"))


def _baseline_entries() -> dict:
    return _load_json_file(_BASELINE_FILE)


def _depth_from_definition(value) -> int:
    """Answer depth written directly from the addendum E-P16-3 clause 1
    definition (root = 1 for dict/list, scalars add 0).  Independent of the
    leaf: this function never calls rs.depth_of."""
    if isinstance(value, dict):
        if not value:
            return 1
        return 1 + max(_depth_from_definition(v) for v in value.values())
    if isinstance(value, list):
        if not value:
            return 1
        return 1 + max(_depth_from_definition(v) for v in value)
    return 0


# ---------------------------------------------------------------------------
# 9. Merge gate over the real artifacts (assembled TOOL_SCHEMAS + real files)
# ---------------------------------------------------------------------------

class TestMergeGateRealArtifacts:
    def test_each_authored_tool_resolves_audited(self):
        for tool in _AUTHORED_10:
            block = rs.resolve_returns(tool)
            assert block is not None, f"{tool}: no returns block resolved"
            assert rs.schema_state(block) == "audited", (
                f"{tool}: schema_state is {rs.schema_state(block)!r}, "
                "the merge gate requires 'audited'")

    def test_baseline_has_exactly_the_ten_authored_tools(self):
        baseline = _baseline_entries()
        assert sorted(baseline.keys()) == sorted(_AUTHORED_10)
        assert len(baseline) == 10

    def test_baseline_entries_match_the_live_blocks(self):
        baseline = _baseline_entries()
        for tool in _AUTHORED_10:
            block = rs.resolve_returns(tool)
            assert baseline[tool]["sha256"] == rs.canonical_schema_sha(block), (
                f"{tool}: baseline sha256 does not match the live block's "
                "canonical schema sha")
            assert baseline[tool]["depth_D"] == block["x-audit"]["depth_D"], (
                f"{tool}: baseline depth_D does not match the live block's "
                "x-audit depth_D")

    def test_each_authored_tool_has_at_least_two_fixtures(self):
        total = 0
        for tool in _AUTHORED_10:
            files = _fixtures_for(tool)
            assert len(files) >= 2, (
                f"{tool}: only {len(files)} fixture file(s), the merge gate "
                "requires the fixture pair")
            total += len(files)
        assert total == 25, f"expected the 25 committed fixtures, got {total}"

    def test_fixtures_pass_except_the_one_acp_node_budget_skip(self):
        node_budget_files = []
        for tool in _AUTHORED_10:
            for path in _fixtures_for(tool):
                fixture = _load_json_file(path)
                assert isinstance(fixture, dict), (
                    f"{path.name}: fixture does not parse as a JSON dict")
                exact_nodes = rs.count_nodes(fixture, 1_000_000)
                if tool == "architecture_context_pack" and exact_nodes > 2000:
                    node_budget_files.append(path.name)
                    # The pin explains itself: this fixture's exact node
                    # count is over the 2,000 NODE_BUDGET, so the designed
                    # fail-open skip -- not a pass -- is the verdict.
                    assert exact_nodes == 2935, path.name
                    assert rs.count_nodes(
                        fixture, rs.NODE_BUDGET) > rs.NODE_BUDGET
                    assert rs.check_returns(
                        tool, fixture, _canonical_raw_len(fixture),
                    ) == ("skipped", "node_budget", []), path.name
                else:
                    assert rs.check_returns(
                        tool, fixture, _canonical_raw_len(fixture),
                    ) == ("pass", None, []), path.name
        assert node_budget_files == [
            "architecture_context_pack__largest_by_bytes.json"], (
            "exactly one fixture in the whole set may be node-budget-skipped")

    def test_fixture_top_level_keys_stay_within_declared_properties(self):
        for tool in _AUTHORED_10:
            block = rs.resolve_returns(tool)
            declared = set((block.get("properties") or {}).keys())
            assert declared, f"{tool}: live block declares no properties"
            for path in _fixtures_for(tool):
                fixture = _load_json_file(path)
                extra = set(fixture.keys()) - declared
                assert not extra, (
                    f"{path.name}: top-level keys {sorted(extra)} are not in "
                    f"{tool}'s declared properties")

    def test_fixture_max_depth_equals_baseline_depth_d(self):
        baseline = _baseline_entries()
        for tool in _AUTHORED_10:
            depths = [
                rs.depth_of(_load_json_file(path))
                for path in _fixtures_for(tool)
            ]
            assert depths, f"{tool}: no fixtures to measure"
            assert max(depths) == baseline[tool]["depth_D"], (
                f"{tool}: deepest fixture is depth {max(depths)} but "
                f"baseline depth_D is {baseline[tool]['depth_D']}")

    def test_the_other_28_set38_tools_are_not_audited(self):
        others = [t for t in rs.RETURNS_SET_38 if t not in _AUTHORED_10]
        assert len(others) == 28
        for tool in others:
            assert rs.schema_state(rs.resolve_returns(tool)) != "audited", (
                f"{tool}: not in batch 1, must not resolve as audited")

    def test_census_on_empty_rows_is_the_exact_landed_partition(self):
        # The G3b-C1 N2 repair: C1's real-assembly census test asserted only
        # eligible == 38 plus the partition inequality, which the leaf's
        # fail-open fallback dict would also satisfy.  With batch 1 landed
        # the real census on empty rows is exactly this dict.
        assert rs.compute_returns_coverage(usage_rows=[]) == {
            "covered": 10,
            "depth_suspect": 0,
            "disabled": 0,
            "eligible": 38,
        }


# ---------------------------------------------------------------------------
# 10. Missing depth_D at check time (the check_returns leg; the schema_state
#     leg is C1's test_missing_depth_d_is_invalid, cited in the banner)
# ---------------------------------------------------------------------------

class TestMissingDepthDCheckReturns:
    def test_missing_depth_d_block_is_skipped_schema_invalid(
            self, install_schemas):
        block = {"type": "object"}
        block["x-audit"] = {
            "status": "audited",
            "schema_sha256": rs.canonical_schema_sha(block),
        }
        install_schemas("tool_x", block)
        assert rs.check_returns("tool_x", {"ok": True}, 10) == (
            "skipped", "schema_invalid", [])


# ---------------------------------------------------------------------------
# 11. N1 -- content-sensitive identity re-pin (C1 section 7's dispatch
#     pattern, now against the authored read_file_range block)
# ---------------------------------------------------------------------------

class TestContentSensitiveIdentity:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch, tmp_path):
        self.captured = []

        def _enqueue(record):
            self.captured.append(dict(record))
            return True

        monkeypatch.setattr(
            srv, "_POST_CALL_ACCOUNTING", SimpleNamespace(enqueue=_enqueue))
        self.tmp_path = tmp_path
        monkeypatch.setenv(
            "ORCHESTRATOR_USAGE_LOG_FILE", str(tmp_path / "USAGE_LOG.jsonl"))
        srv._R101_FIELDS.set(None)
        srv._R101_DISPATCH_FULL.set(None)
        call_cache._ENTRIES.clear()
        old_db = ct._SESSIONS_DB
        ct._SESSIONS_DB = tmp_path / "sessions.db"
        ct._sync_session_db_alias()
        ct._init_session_db()
        yield
        ct._SESSIONS_DB = old_db
        ct._sync_session_db_alias()
        srv._R101_FIELDS.set(None)
        srv._R101_DISPATCH_FULL.set(None)
        call_cache._ENTRIES.clear()

    async def _dispatch(self, tool: str, body: dict, op_id: str):
        reader = StreamReader(
            protocol=mock.Mock(), limit=2 ** 16,
            loop=asyncio.get_running_loop())
        reader.feed_data(json.dumps(body).encode("utf-8"))
        reader.feed_eof()
        request = make_mocked_request(
            "POST", f"/api/call/{tool}", payload=reader,
            match_info={"tool_name": tool},
            headers={"X-Operation-ID": op_id})
        return await srv._dispatch_tool_call_request(request)

    def _record_for(self, op_id: str) -> dict:
        matches = [r for r in self.captured
                   if r.get("accounting_id") == op_id]
        assert len(matches) == 1, (
            f"expected exactly one accounting record for {op_id}, "
            f"got {len(matches)}")
        return matches[0]

    def _dispatch_read_file_range(self, op_id: str):
        body = {
            "project_path": str(_REPO_ROOT),
            "file_path": "orchestrator/core/returns_shape.py",
            "start_line": 1,
            "end_line": 5,
            "max_lines": 5,
            "include_next_ranges": False,
        }

        async def scenario():
            response = await self._dispatch("read_file_range", body, op_id)
            # Read the ContextVar INSIDE the task (C1 section 7's lesson).
            return response, srv._R101_DISPATCH_FULL.get()

        return asyncio.run(scenario())

    def test_stored_verdict_is_pass_and_matches_recomputation(self):
        op_id = "a45-op-c2-identity-1"
        response, full = self._dispatch_read_file_range(op_id)
        assert response.status == 200
        assert json.loads(response.text).get("ok") is True
        assert isinstance(full, dict)

        verdict, reason, mismatches = rs.check_returns(
            "read_file_range", full, _canonical_raw_len(full))
        expected = f"skipped/{reason}" if verdict == "skipped" else verdict
        record = self._record_for(op_id)
        assert record.get("returns_shape_verdict") == expected, (
            "identity: the stored verdict must be the verdict the leaf "
            "computes over the captured DISPATCH_FULL object")
        # Content-sensitive now: with the audited block landed, the real
        # answer does not merely reach SOME verdict -- it passes.
        assert verdict == "pass" and mismatches == []
        assert record.get("returns_shape_verdict") == "pass"

    def test_deleting_required_ok_flips_recomputation_to_warn(self):
        op_id = "a45-op-c2-identity-2"
        response, full = self._dispatch_read_file_range(op_id)
        assert response.status == 200
        record = self._record_for(op_id)
        assert record.get("returns_shape_verdict") == "pass"

        mutated = json.loads(json.dumps(full))  # deep copy; `full` untouched
        assert "ok" in mutated, (
            "the authored read_file_range schema requires 'ok' and the real "
            "answer carries it -- deleting it is the content change")
        del mutated["ok"]
        verdict, reason, mismatches = rs.check_returns(
            "read_file_range", mutated, _canonical_raw_len(mutated))
        assert verdict == "warn" and reason is None
        assert "missing:ok" in mismatches, (
            "the recomputed verdict must be sensitive to the full answer's "
            "content: removing the required 'ok' key warns with missing:ok, "
            "so the stored 'pass' cannot be a plumbing echo")
        # The unmutated captured object still passes: the flip came from
        # the content change, not from the recomputation itself.
        assert rs.check_returns(
            "read_file_range", full, _canonical_raw_len(full),
        ) == ("pass", None, [])


# ---------------------------------------------------------------------------
# 12. N3 -- gate boundaries at total == 100, the real-assembly census move,
#     verdict_counts, and the independent depth cross-check
# ---------------------------------------------------------------------------

class TestGateBoundariesAndIndependentDepth:
    TOOL = "gate_tool"

    def test_gate_at_exactly_100_rows_two_depth_does_not_fire(self):
        # 2/100 is exactly 0.02 and the gate is strict (> 0.02).
        rows = _rows(self.TOOL, "skipped/depth", 2)
        rows += _rows(self.TOOL, "pass", 98)
        assert len(rows) == 100
        assert rs.depth_skip_gate(rows, self.TOOL) is False

    def test_gate_at_exactly_100_rows_three_depth_fires(self):
        rows = _rows(self.TOOL, "skipped/depth", 3)
        rows += _rows(self.TOOL, "pass", 97)
        assert len(rows) == 100
        assert rs.depth_skip_gate(rows, self.TOOL) is True

    def test_census_moves_the_one_depth_tripped_authored_tool(self):
        # Real assembly (no schemas= override): 120 verdict rows for
        # read_file_range, 9 of them skipped/depth -- the section 4 worked
        # trace -- moves exactly that one authored tool covered ->
        # depth_suspect under the partition precedence.
        rows = _rows("read_file_range", "skipped/depth", 9)
        rows += _rows("read_file_range", "pass", 111)
        assert rs.compute_returns_coverage(usage_rows=rows) == {
            "covered": 9,
            "depth_suspect": 1,
            "disabled": 0,
            "eligible": 38,
        }

    def test_verdict_counts_are_exact_per_verdict(self):
        rows = _rows(self.TOOL, "pass", 3)
        rows += _rows(self.TOOL, "warn", 2)
        rows += _rows(self.TOOL, "skipped/depth", 4)
        rows += _rows(self.TOOL, "skipped/node_budget", 1)
        rows += _rows("other_tool", "pass", 5)
        rows += [{"tool": self.TOOL}]  # no verdict key: not counted
        assert rs.verdict_counts(rows, self.TOOL) == {
            "pass": 3,
            "warn": 2,
            "skipped/depth": 4,
            "skipped/node_budget": 1,
        }
        assert rs.verdict_counts(rows, "other_tool") == {"pass": 5}
        assert rs.verdict_counts([], self.TOOL) == {}

    def test_independent_depth_matches_leaf_on_fixtures_and_edges(self):
        checked = 0
        for tool in _AUTHORED_10:
            for path in _fixtures_for(tool):
                fixture = _load_json_file(path)
                assert _depth_from_definition(fixture) == rs.depth_of(
                    fixture), path.name
                checked += 1
        assert checked == 25
        for edge in ({}, [], 0, "x", None, True):
            assert _depth_from_definition(edge) == rs.depth_of(edge), edge

    def test_independent_depth_known_values(self):
        known = [
            ({}, 1),
            ([], 1),
            (0, 0),
            ("x", 0),
            (None, 0),
            ({"a": 1}, 1),
            ([1, 2], 1),
            ({"a": {"b": 1}}, 2),
            ([[[]]], 3),
            ({"a": [{"b": 1}]}, 3),
            ({"a": {"b": {"c": []}}}, 4),
        ]
        for value, expected in known:
            assert _depth_from_definition(value) == expected, value
            assert rs.depth_of(value) == expected, value


# ---------------------------------------------------------------------------
# 13. PART 3 -- F-BL-1 / F-BL-2 regressions (Big Loop cycle 1 mini-plan)
# ---------------------------------------------------------------------------
# F-BL-1: depth_of was recursive; a parseable answer deeper than the Python
# recursion limit (~1,000) died as RecursionError inside check_returns and
# was mislabelled ("check_error", ...) instead of ("skipped", "depth", []).
# Equivalence for ordinary depths is already pinned by C2's independent-depth
# tests (TestGateBoundariesAndIndependentDepth:
# test_independent_depth_matches_leaf_on_fixtures_and_edges and
# test_independent_depth_known_values) -- cited here, not duplicated; the
# new equivalence obligations are only the >1,000 case and one mid-depth
# hand-built structure with a literal expected value.
# F-BL-2: _log_usage converted accounting_sequence with a bare int(...);
# a non-int value raised out of _log_usage and destroyed the entire
# accounting row (verdict keys included).  The conversion is now guarded
# by try/except Exception at the same site, omitting only the key.
# These tests live in test_a45 (not test_post_call_accounting) because
# PART 3 is the F-BL-1+F-BL-2 regression home named by the mini-plan and
# the C1 accounting read-back pattern (ct._log_usage + the
# ORCHESTRATOR_USAGE_LOG_FILE env redirection) already lives in this file
# (TestAccountingReadBack).

def _deep_chain(depth: int) -> dict:
    """A nested-dict chain whose depth is exactly `depth` (one key/level).

    Built iteratively, independently of rs.depth_of (the existing _nest
    helper constructs via rs.depth_of and must not be reused here).
    """
    value: dict = {}
    for _ in range(depth - 1):
        value = {"k": value}
    return value


class TestFBL1DeepDepth:
    def test_deep_chain_1201_check_returns_skipped_depth(self, install_schemas):
        install_schemas("tool_x", _audited_block({"type": "object"}, 1))
        chain = _deep_chain(1201)
        assert rs.count_nodes(chain, rs.NODE_BUDGET) == 1201
        assert rs.count_nodes(chain, rs.NODE_BUDGET) <= 2000
        assert rs.check_returns("tool_x", chain, 10) == (
            "skipped", "depth", [])

    def test_depth_of_deep_chain_1201_without_exception(self):
        assert rs.depth_of(_deep_chain(1201)) == 1201

    def test_depth_of_mid_depth_hand_built_literal_50(self):
        # Base structure depth 3: {"a": [{"b": 1}]}; 47 single-key wraps
        # add 47 -> literal expected depth 50.
        value = {"a": [{"b": 1}]}
        for _ in range(47):
            value = {"k": value}
        assert rs.depth_of(value) == 50


class TestFBL2AccountingSequenceGuard:
    @pytest.fixture(autouse=True)
    def _tmp_usage_log(self, monkeypatch, tmp_path):
        self.log_path = tmp_path / "USAGE_LOG.jsonl"
        monkeypatch.setenv("ORCHESTRATOR_USAGE_LOG_FILE", str(self.log_path))

    def _rows(self) -> list:
        if not self.log_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _call(self, sequence):
        return ct._log_usage(
            "tool_fbl", {}, True, 1,
            returns_shape_verdict="warn",
            returns_shape_errors=["missing:x"],
            accounting_id="op-fbl", accounting_sequence=sequence,
        )

    def _assert_guarded_row(self):
        [row] = self._rows()
        assert row["tool"] == "tool_fbl"
        assert row["returns_shape_verdict"] == "warn"
        assert row["returns_shape_errors"] == ["missing:x"]
        assert row["accounting_id"] == "op-fbl"
        assert "accounting_sequence" not in row

    def test_bogus_string_sequence_row_written_without_key(self):
        assert self._call("bogus")
        self._assert_guarded_row()

    def test_dict_sequence_row_written_without_key(self):
        assert self._call({})
        self._assert_guarded_row()

    def test_inf_sequence_row_written_without_key(self):
        assert self._call(float("inf"))
        self._assert_guarded_row()

    def test_coercible_string_7_sequence_is_int_7(self):
        assert self._call("7")
        [row] = self._rows()
        assert row["accounting_sequence"] == 7
        assert isinstance(row["accounting_sequence"], int)

    def test_valid_int_sequence_present(self):
        assert self._call(42)
        [row] = self._rows()
        assert row["accounting_sequence"] == 42

    def test_none_sequence_absent(self):
        assert self._call(None)
        [row] = self._rows()
        assert row["returns_shape_verdict"] == "warn"
        assert "accounting_sequence" not in row
