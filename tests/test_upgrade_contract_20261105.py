"""Regressions for upgrades 4/5/6/8/11/12/13/18 at served boundaries."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
import code_tools as ct
import orchestrator_server as srv
import tools_v15 as hints
from orchestrator.core import response_contract as wire
from orchestrator.core.definition_contract import definition_version, decorate_schema
from orchestrator.core.tool_retrieval import rank_tools


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(wire, "_recent", wire.OrderedDict())
    monkeypatch.setattr(wire, "_counts", wire.Counter())
    wire._repeat.set(False)
    srv._R101_FIELDS.set(None)


def schema(name="probe", description="Inspect a source file.", **extra):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {}}, **extra}}


def test_version_deterministic_and_changes_with_contract_or_purpose():
    original = schema()
    shuffled = {k: v for k, v in reversed(list(original.items()))}
    assert definition_version(original, "read") == definition_version(shuffled, "read")
    assert definition_version(original, "read") != definition_version(original, "write")
    changed = copy.deepcopy(original)
    changed["function"]["parameters"]["properties"]["mode"] = {"type": "string"}
    assert definition_version(original) != definition_version(changed)
    served = decorate_schema(original, original)
    assert "definition_version" not in original["function"]
    assert definition_version(served) == definition_version(original)


def test_schema_paths_agree_and_keep_returns_private(monkeypatch):
    monkeypatch.setitem(ct.TOOL_MAP, "probe", lambda: None)
    monkeypatch.setattr(ct, "TOOL_SCHEMAS", [schema(returns={"type": "object"})])
    direct = srv._enriched_tool_schema("probe")
    payload, status = srv._tool_schema_payload("probe")
    request = SimpleNamespace(query={}, headers={})
    catalog = json.loads(asyncio.run(srv.handle_tools(request)).body)
    assert status == 200 and direct == payload["tool"]
    assert catalog[0]["function"]["definition_version"] == direct["function"]["definition_version"]
    assert "returns" not in direct["function"]
    assert "definition_version" in direct["function"]["parameters"]["properties"]


@pytest.mark.parametrize("mode", ["", "dual", "2"])
def test_preserves_every_business_field_in_each_view(mode):
    payload = {"ok": True, "summary": {"count": 2}, "result": "text", "hash": "abc",
               "hint": "old", "ca_next_tool": "read_file_range", "ca_next_actions": ["one", "two"]}
    rendered = wire.render("probe", payload, mode, None, ct.AUTO_CHAIN_FIELDS)
    if mode == "2":
        assert rendered["result"] == payload and isinstance(rendered["summary"], str)
        assert rendered["hint"]["chain"]["ca_next_actions"] == ["one", "two"]
    else:
        assert all(rendered[k] == v for k, v in payload.items())
        if mode == "dual":
            assert rendered["response_v2"]["result"] == payload


def test_soft_repeat_is_scoped_consecutive_and_poll_safe():
    identity = ("session", "agent", "20")
    for number in range(3):
        wire.observe(identity, "read_file_range", {"a": 1, "b": 2})
        result = wire.render("read_file_range", {"ok": True}, "", {"ok"}, ())
        assert bool(result.get("hint", {}).get("ask")) == (number == 2)
        assert result["ok"]
    wire.observe(identity, "read_file_range", {"a": 2})
    assert not wire._repeat.get()
    wire.observe(("other", "agent", "20"), "read_file_range", {"a": 1, "b": 2})
    assert not wire._repeat.get()
    for _ in range(4):
        wire.observe(identity, "write_status", {"operation_id": "fixed"})
        assert not wire._repeat.get()
    wire.observe(("", "", ""), "probe", {})
    assert not wire._repeat.get()


def test_confirmation_and_failure_keep_existing_meaning():
    payload = {"ok": False, "error_name": "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED",
               "confirm_destructive_required": 2, "executed": False}
    result = wire.render("run_project_command", payload, "dual", None, ())
    assert result["response_v2"]["hint"]["ask"]
    assert result["executed"] is False and result["ok"] is False
    denied = wire.render("x", {"ok": False, "error_name": "DUPLICATE_RETRY_BLOCKED"}, "2", None, ())
    assert denied["ok"] is False and not denied["hint"]["ask"]


def test_bm25_uses_examples_filters_deprecated_and_refreshes():
    documents = [schema("alpha", "Unrelated.", trigger_examples=["inspect zebra stripes"]),
                 schema("beta", "Inspect zebra stripes.", deprecated=True), schema("gamma", "Other.")]
    assert rank_tools("zebra", documents, {})[0]["tool"] == "alpha"
    documents[0]["function"]["trigger_examples"] = ["other"]
    documents[2]["function"]["trigger_examples"] = ["inspect zebra stripes"]
    assert rank_tools("zebra", documents, {})[0]["tool"] == "gamma"
    assert rank_tools("zebra", documents, {}, eligible={"alpha"}) == []
    assert rank_tools("other", documents, {}, boosts=["gamma"])[0]["tool"] == "gamma"


def test_bootstrap_task_hint_keeps_eligible_core_and_once_guard(monkeypatch):
    from orchestrator.core.registry_data import CURATED_CORE_TOOLS
    monkeypatch.setattr(ct, "_RULE_CORE_FETCH_CACHE", {})
    monkeypatch.setattr(hints, "_wt_q_z2_refs", lambda _: pytest.fail("bootstrap must be local"))
    async def run():
        first = json.loads(await ct.get_rule_core_text("upgrade-test", task_hint="query sqlite"))
        second = json.loads(await ct.get_rule_core_text("upgrade-test", task_hint="find callers"))
        return first, second
    first, second = asyncio.run(run())
    eligible = ct.bootstrap_tool_purposes()
    assert set(CURATED_CORE_TOOLS).intersection(eligible) <= first["tool_purposes"].keys()
    assert len(first["tool_purposes"]) <= 8 + len(CURATED_CORE_TOOLS)
    assert second["already_fetched"] and "text" not in second
    assert first["bootstrap_epoch"] == second["bootstrap_epoch"]


def test_hint_exposes_bounded_explained_candidates(monkeypatch):
    monkeypatch.setattr(hints, "_wt_q_z2_refs", lambda _: [])
    result = json.loads(asyncio.run(hints.tool_decision_hint("query sqlite database", verbosity="terse")))
    assert 1 <= len(result["ranked_tools"]) <= 8
    assert all(row["score"] > 0 and row["why"] for row in result["ranked_tools"])
    assert len({row["tool"] for row in result["ranked_tools"]}) == len(result["ranked_tools"])


@pytest.mark.parametrize("version,status", [("old", 409), (None, 200), ("current", 200)])
def test_dispatch_stale_precondition_prevents_handler(monkeypatch, version, status):
    calls = []
    async def probe():
        calls.append(True)
        return json.dumps({"ok": True, "count": 1})
    monkeypatch.setitem(ct.TOOL_MAP, "upgrade_probe", probe)
    monkeypatch.setattr(ct, "TOOL_SCHEMAS", [schema("upgrade_probe")])
    monkeypatch.setattr(srv, "_auth_preamble_sync", lambda *args: ({}, {}, ""))
    monkeypatch.setattr(srv, "_is_local", lambda _: True)
    monkeypatch.setattr(srv, "_POST_CALL_ACCOUNTING", SimpleNamespace(enqueue=lambda record: True))
    current = srv._enriched_tool_schema("upgrade_probe")["function"]["definition_version"]
    body = {"fields": "ok"}
    if version is not None:
        body["definition_version"] = current if version == "current" else version
    request = srv._InProcessToolRequest("upgrade_probe", body, headers={})
    response = asyncio.run(srv.handle_call(request))
    result = json.loads(response.body)
    assert response.status == status, result
    assert len(calls) == int(status == 200)
    if status == 409:
        assert result["error_name"] == "STALE_TOOL_DEFINITION"
        assert result["pushed_schema"]["function"]["definition_version"] == current


def test_live_wire_boundary_handles_errors_projection_and_dual(monkeypatch):
    from aiohttp import web
    async def endpoint(request, transport="api"):
        srv._R101_FIELDS.set({"ok", "ca_next_tool"})
        return web.json_response({"ok": False, "error_name": "EXAMPLE_ERROR",
                                  "ca_next_tool": "read_file_range"}, status=409)
    monkeypatch.setattr(srv, "_dispatch_tool_call_request", endpoint)
    for mode in ("dual", "2"):
        request = srv._InProcessToolRequest("probe", {}, headers={"X-ToolA-Response": mode})
        response = asyncio.run(srv.handle_call(request))
        body = json.loads(response.body)
        view = body["response_v2"] if mode == "dual" else body
        assert response.status == 409
        assert view["ok"] is False
        assert view["result"]["error_name"] == "EXAMPLE_ERROR"
        assert view["hint"]["next"] == "read_file_range"
    assert wire._repeat.get() is False


def test_batch_rejects_all_writes_before_any_read():
    from experiments.upgrade_20261105.batch_pilot import run_batch
    calls = []
    async def dispatch(name, payload):
        calls.append(name)
    with pytest.raises(ValueError):
        asyncio.run(run_batch([{"tool": "git_status", "payload": {}},
                               {"tool": "write_fix", "payload": {}}], dispatch))
    assert not calls


def test_recall_gate_rejects_tiny_perfect_sample_and_empty_data():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "bin/evaluate_tool_retrieval.py"
    spec = importlib.util.spec_from_file_location("retrieval_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    row = {"task": "query sqlite", "expected_tools": ["query_sqlite"], "audit_id": "test",
           "label_source": "fixture"}
    result = asyncio.run(module.evaluate([row]))
    assert not result["release_pass"] and result["embedding_condition"] is None
    empty = asyncio.run(module.evaluate([]))
    assert empty["candidate_recall_at_8"] is None and not empty["release_pass"]


@pytest.mark.parametrize("payload", [None, [1, 2], "text"])
def test_v2_wraps_json_scalars_and_arrays(payload):
    result = wire.render("probe", payload, "2", None, ())
    assert result["result"] == payload and result["ok"]
    assert isinstance(result["summary"], str)


def test_v2_large_response_retains_envelope_and_lossless_reference(monkeypatch):
    from aiohttp import web
    from hashlib import sha256
    stored = {}
    async def endpoint(request, transport="api"):
        srv._R101_FIELDS.set(None)
        return web.json_response({"ok": True, "data": "x" * 10000})
    monkeypatch.setattr(srv, "_dispatch_tool_call_request", endpoint)
    monkeypatch.setattr(srv, "_RESULT_BUDGET_BYTES", 2048)
    monkeypatch.setattr(srv, "_result_ref_store", lambda key, text: stored.update({key: text}))
    request = srv._InProcessToolRequest("probe", {}, headers={"X-ToolA-Response": "2"})
    response = asyncio.run(srv.handle_call(request))
    result = json.loads(response.body)
    assert set(result) == {"ok", "hint", "result", "summary"}
    ref = result["result"]["result_ref"]
    assert sha256(stored[ref].encode()).hexdigest() == ref
    assert json.loads(stored[ref])["result"]["data"] == "x" * 10000
    assert len(response.body) < 2048


def test_definition_error_phase_and_browser_negotiation():
    assert ct._phase_for_error_name("STALE_TOOL_DEFINITION") == "schema"
    assert "X-ToolA-Response" in srv._cors(None)["Access-Control-Allow-Headers"]
