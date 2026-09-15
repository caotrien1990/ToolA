"""UX discovery/lifecycle contracts; process execution is mocked at its boundary."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import make_mocked_request

import code_tools as ct
import orchestrator_server as srv
import tools_upgrade_p5 as apps
import tools_v15 as v


@pytest.mark.parametrize("name,expected", [
    ("inspect_process", "process_tree_inspect"),
    ("strat_app", "start_app"),
    ("inspect", "port_owner_inspect"),
    ("PROCESS", "process_tree_inspect"),
])
def test_unknown_suggestions_shared_across_get_post_and_mcp(name, expected, monkeypatch):
    schema, status = srv._tool_schema_payload(name)
    request = make_mocked_request("GET", "/api/tools/"+name+"/schema", match_info={"tool_name": name})
    get_response = asyncio.run(srv.handle_tool_describe(request))
    monkeypatch.setattr(srv, "_is_local", lambda r: True)
    monkeypatch.setattr(srv, "_origin_ok", lambda r: True)
    request = make_mocked_request("POST", "/api/call/"+name, match_info={"tool_name": name})
    post_response = asyncio.run(srv.handle_call(request))
    get = json.loads(get_response.text)
    post = json.loads(post_response.text)
    assert status == get_response.status == post_response.status == 404
    assert schema["suggestions"] == get["suggestions"] == post["suggestions"]
    assert expected in schema["suggestions"]
    assert len(schema["suggestions"]) <= 8
    assert set(schema["suggestions"]) <= set(ct.TOOL_MAP)
    assert "available" not in post and "available" not in get
    assert "/api/tools/"+name+"/schema" not in json.dumps(post)
    assert post["ca_guidance"]["call_pattern"].startswith("GET /api/tools/names")


def test_unknown_noise_does_not_invent_matches_and_is_bounded():
    for name in ("", "zzzzqqqqxxxxvvvv", "z"*10000):
        result = srv._unknown_tool_payload(name)
        assert result["suggestions"] == []
        assert len(result["discovery_url"]) < 250


def test_substring_matches_rank_ahead_of_fuzzy(monkeypatch):
    monkeypatch.setattr(ct, "TOOL_MAP", {"inspect_project_usage": None, "inspekt": None})
    assert srv._unknown_tool_payload("inspect")["suggestions"][0] == "inspect_project_usage"


@pytest.mark.parametrize("verbosity", ["full", "terse", "ref"])
@pytest.mark.parametrize("query", ["recycle process", "restart application", "relaunch server"])
def test_recycle_recipe_is_inspect_first_and_advisory(query, verbosity, monkeypatch):
    launch = AsyncMock(side_effect=AssertionError("must not execute"))
    monkeypatch.setattr(ct, "run_project_command", launch)
    result = json.loads(asyncio.run(v.tool_decision_hint(query, verbosity=verbosity)))
    assert result["recommended_tool"] == "server_lifecycle_status"
    assert result["recipe"]["advisory_only"] is True
    steps = [item["tool"] for item in result["recipe"]["steps"]]
    assert steps == ["server_lifecycle_status", "stop_app", "start_app", "server_lifecycle_status"]
    assert set(steps) <= set(ct.TOOL_MAP)
    launch.assert_not_awaited()


def test_orchestrator_recipe_uses_dedicated_restart():
    result = json.loads(asyncio.run(v.tool_decision_hint("restart orchestrator")))
    steps = [item["tool"] for item in result["recipe"]["steps"]]
    assert "restart_orchestrator_via_bat" in steps
    assert "stop_app" not in steps and "kill_process" not in steps


def test_recycle_missing_tool_fails_loud(monkeypatch):
    monkeypatch.setattr(ct, "TOOL_MAP", {"server_lifecycle_status": None})
    result = json.loads(asyncio.run(v.tool_decision_hint("recycle process")))
    assert result["error_name"] == "RECIPE_TOOL_UNAVAILABLE"
    assert "start_app" in result["missing_tools"]


def test_non_lifecycle_recycle_does_not_get_process_recipe(monkeypatch):
    monkeypatch.setattr(v, "_wt_q_z2_refs", lambda *a: [])
    result = json.loads(asyncio.run(v.tool_decision_hint("recycle bin file recovery")))
    assert "recipe" not in result


@pytest.mark.parametrize("app,command,source,ignored", [
    ("app.exe", "", "app", False),
    ("", "python server.py", "command", False),
    ("app.exe", "python server.py", "command", True),
    ("app.exe", "app.exe", "command", True),
])
@pytest.mark.parametrize("success", [True, False])
def test_start_app_discloses_effective_input_without_changing_execution(app, command, source, ignored, success, monkeypatch):
    run = AsyncMock(return_value=json.dumps({"ok": success, "spawned_pid": 42, "readiness": "evidence"}))
    monkeypatch.setattr(ct, "run_project_command", run)
    result = json.loads(asyncio.run(apps.start_app(app=app, command=command, project_path="project", expected_port=4321)))
    assert run.await_args.kwargs["command"] == (command or app)
    assert run.await_args.kwargs["expected_port"] == 4321
    assert result["command_source"] == source
    assert result["app_ignored"] is ignored
    assert result["command"] == (command or app)
    assert result["app"] == app and result["ok"] is success
    assert result["spawned_pid"] == 42 and result["readiness"] == "evidence"


def test_start_app_missing_input_never_launches(monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(ct, "run_project_command", run)
    result = json.loads(asyncio.run(apps.start_app(project_path="project")))
    assert result["error_name"] == "MISSING_REQUIRED_FIELD"
    run.assert_not_awaited()


@pytest.mark.parametrize("listening,pid,name", [(True, 42, "python"), (False, None, None), (None, None, None)])
def test_compact_port_preserves_unknown_and_nullable_owner(listening, pid, name, monkeypatch):
    probe = AsyncMock(return_value=json.dumps({"ok": True, "listening": listening, "pid": pid, "process_name": name, "cmdline": "private", "requires_admin": True}))
    monkeypatch.setattr(v, "server_lifecycle_status", probe)
    result = json.loads(asyncio.run(v.port_owner_inspect(8083)))
    probe.assert_awaited_once_with(8083)
    assert result["listening"] is listening and result["pid"] == pid
    assert result["process_name"] == name and result["port"] == 8083
    assert "cmdline" not in result


def test_compact_port_preserves_failure(monkeypatch):
    error = json.dumps({"ok": False, "error_name": "LIFECYCLE_TIMEOUT"})
    monkeypatch.setattr(v, "server_lifecycle_status", AsyncMock(return_value=error))
    assert asyncio.run(v.port_owner_inspect(8083)) == error


def test_public_schema_describes_real_contracts():
    port = srv._tool_schema_payload("port_owner_inspect")[0]["tool"]["function"]
    assert "null" in port["description"] and "scan/cache" in port["description"]
    lifecycle = srv._tool_schema_payload("server_lifecycle_status")[0]["tool"]["function"]
    assert "estimates" in lifecycle["description"]
    start = srv._tool_schema_payload("start_app")[0]["tool"]["function"]
    assert "command_source" in start["description"] and "app_ignored" in start["description"]
