"""2026-09-29: identity fields must be JSON strings, and /api/call always answers JSON.

Incident (measured): a ToolQ CA ran `$r = Call 'run_project_test_script' ...` in
PowerShell, where variable names are case-insensitive, so `$R` -- its repo path --
became the response object. The next calls sent `project_path` as a JSON OBJECT:

  * git_status answered 400 WRONG_ARGUMENTS carrying a raw TypeError, and its
    durable audit row failed (CRITICAL) on the same Path(dict).
  * git_commit crashed in _lookup_caller_session -> _session_project_key ->
    Path(dict); the exception escaped the dispatcher and aiohttp answered a bare
    text/plain 500. bin/a.ps1 parsed that to $null, so the CA read an EMPTY
    staged_hash and committed nothing.

The published contract was already right (every schema says `string`); nothing
enforced it. Fix: ONE gate in _dispatch_tool_call_request
(_identity_type_violation over _STRING_IDENTITY_KEYS), a JSON catch-all in
handle_call, and a NON_JSON_RESPONSE answer in bin/a.ps1
(tests/test_bin_a_ps1_client.py).
"""

from __future__ import annotations

import asyncio
import json
from unittest import mock

import pytest
from aiohttp import StreamReader, web
from aiohttp.test_utils import make_mocked_request

import code_tools
import orchestrator_server

# The real clobbered shape: a previous [A] answer standing where the path was.
CLOBBERED = {"ok": True, "result": "5108 passed", "error_name": None}


def _raw_body(raw: bytes) -> StreamReader:
    loop = asyncio.new_event_loop()
    try:
        reader = StreamReader(protocol=mock.Mock(), limit=2 ** 16, loop=loop)
        reader.feed_data(raw)
        reader.feed_eof()
        return reader
    finally:
        loop.close()


def _request(tool_name: str, raw: bytes):
    return make_mocked_request(
        "POST", f"/api/call/{tool_name}", headers={},
        payload=_raw_body(raw), match_info={"tool_name": tool_name})


def _post(tool_name: str, body):
    request = _request(tool_name, json.dumps(body).encode("utf-8"))
    response = asyncio.run(orchestrator_server._dispatch_tool_call_request(request))
    return response, json.loads(response.text)


def _spy():
    calls = []

    async def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return json.dumps({"ok": True})

    return spy, calls


# ---- the incident, end to end through the real dispatcher ---------------------

def test_git_commit_with_an_object_project_path_is_400_not_a_bare_500():
    # Before the gate this raised out of _dispatch_tool_call_request (the bare 500).
    response, result = _post("git_commit", {
        "project_path": CLOBBERED, "message": "x", "expected_staged_hash": "abc"})

    assert response.status == 400
    assert response.content_type == "application/json"
    assert result["error_name"] == "WRONG_ARGUMENTS"
    assert result["field"] == "project_path"
    assert result["received_type"] == "object"
    assert result["expected_type"] == "string"
    assert result["write_performed"] is False
    assert result["ca_next_action"] == "resend_field_as_json_string"


def test_git_status_is_refused_before_the_tool_runs():
    # git_status used to answer WRONG_ARGUMENTS too -- from a raw TypeError INSIDE
    # the tool. The discriminator is that the tool is never reached.
    spy, calls = _spy()
    with mock.patch.dict(code_tools.TOOL_MAP, {"git_status": spy}):
        response, result = _post("git_status", {"project_path": CLOBBERED})

    assert response.status == 400
    assert result["field"] == "project_path"
    assert result["received_type"] == "object"
    assert calls == []


@pytest.mark.parametrize("field", ["project_path", "file_path"])
@pytest.mark.parametrize("value,received", [
    ({"a": 1}, "object"), (["D:/repo"], "array"), (7, "number"),
    (1.5, "number"), (True, "boolean"),
])
def test_every_non_string_json_type_is_named(field, value, received):
    spy, calls = _spy()
    with mock.patch.dict(code_tools.TOOL_MAP, {"git_status": spy}):
        response, result = _post("git_status", {field: value})

    assert response.status == 400
    assert (result["error_name"], result["field"], result["received_type"]) == (
        "WRONG_ARGUMENTS", field, received)
    assert calls == []


# ---- the helper: the ONE matcher ----------------------------------------------

@pytest.mark.parametrize("body", [
    {"project_path": "D:/repo"}, {"project_path": None}, {"file_path": ""},
    {"page_id": "p1", "session_id": "s1"}, {"unrelated": {"a": 1}}, {},
])
def test_strings_null_and_non_identity_keys_pass(body):
    # null stays allowed: several tools read a missing identity as "use the session".
    assert orchestrator_server._identity_type_violation(body) is None


@pytest.mark.parametrize("key", ["page_id", "session_id", "cwd", "dest"])
def test_non_path_identity_keys_are_gated_too(key):
    assert orchestrator_server._identity_type_violation({key: {"x": 1}}) == (key, "object")


def test_the_first_violation_is_deterministic():
    body = {"project_path": 1, "file_path": [], "cwd": {}}
    assert orchestrator_server._identity_type_violation(body) == ("cwd", "object")


def test_every_path_key_is_gated():
    # Derived, not copied: a key added to _PATH_KEYS is gated with no second edit.
    assert set(orchestrator_server._PATH_KEYS) <= orchestrator_server._STRING_IDENTITY_KEYS


def test_every_published_schema_declares_the_gated_keys_as_string():
    # The gate may only refuse what the contract already forbids. Measured
    # 2026-09-29: 258 tools, 13 gated keys present, 0 non-string declarations.
    offenders = []
    for name in sorted(code_tools.TOOL_MAP):
        schema = orchestrator_server._enriched_tool_schema(name) or {}
        props = ((schema.get("function") or {}).get("parameters") or {}).get("properties") or {}
        for key in orchestrator_server._STRING_IDENTITY_KEYS.intersection(props):
            if props[key].get("type") != "string":
                offenders.append((name, key, props[key].get("type")))
    assert offenders == []


# ---- a non-object body has ONE check and a name ---------------------------------

@pytest.mark.parametrize("raw,got", [(b"[1, 2]", "array"), (b'"D:/repo"', "string"),
                                     (b"42", "number"), (b"true", "boolean")])
def test_a_non_object_body_is_invalid_json_body(raw, got):
    response = asyncio.run(orchestrator_server._dispatch_tool_call_request(
        _request("read_file_range", raw)))
    result = json.loads(response.text)

    assert response.status == 400
    assert result["error_name"] == "INVALID_JSON_BODY"
    assert result["ca_next_action"] == "send_a_json_object_body"
    assert result["error"].endswith(f"got {got}")


# ---- handle_call: every answer on /api/call is JSON ------------------------------

def _crash(exc):
    async def crashed(request, transport="api"):
        raise exc
    return crashed


@pytest.mark.parametrize("tool_name,next_action,safe", [
    ("git_commit", "check_write_status_before_retry", False),
    ("read_file_range", "retry_same_call", True),
])
def test_a_dispatcher_crash_answers_json_500(tool_name, next_action, safe):
    request = _request(tool_name, b"{}")
    with mock.patch.object(orchestrator_server, "_dispatch_tool_call_request",
                           _crash(RuntimeError("boom"))):
        response = asyncio.run(orchestrator_server.handle_call(request))
    result = json.loads(response.text)

    assert response.status == 500
    assert response.content_type == "application/json"
    assert result["error_name"] == "INTERNAL_ERROR"
    assert "boom" in result["error"]
    assert result["ca_next_action"] == next_action
    assert result["safe_to_retry"] is safe
    assert result["retryable"] is safe


def test_http_exceptions_still_propagate():
    # aiohttp control flow (redirects, 404s raised on purpose) is not a crash.
    request = _request("read_file_range", b"{}")
    with mock.patch.object(orchestrator_server, "_dispatch_tool_call_request",
                           _crash(web.HTTPNotFound())):
        with pytest.raises(web.HTTPNotFound):
            asyncio.run(orchestrator_server.handle_call(request))