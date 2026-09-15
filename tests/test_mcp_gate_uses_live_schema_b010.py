"""B010: the MCP pre-gate must not refuse a field the live daemon accepts.

Measured on live [A] on 2026-08-20, right after a restart to generation
4c69c7c10f4b, calling the same tool two ways:

    HTTP  POST /api/call/git_status {"project_path": ..., "max_records": 0}
        -> ok:true

    MCP   call_tool("git_status", {"project_path": ..., "max_records": 0})
        -> ok:false  UNSUPPORTED_FIELD  accepted_fields:[include_ignored, project_path]

`max_records` had been in the registry since commit ba88645 (21:18) and
`/api/tools/git_status/schema` listed it. The MCP client processes had started at
09:30, 17:56 and 18:37 -- all before that commit.

The give-away was in the MCP response for a payload the gate DID let through: it
came back carrying `records_total` and `records_truncated`, which only the new
code emits. So the handler was the new one all along; only the GATE was old.

Why: dispatch_tool_call(transport="mcp") screened the payload with
`_schema_for_tool()`, which reads `ct.TOOL_SCHEMAS` in whichever process runs the
gate. For an MCP call that is the stdio bridge -- a separate long-lived process
that imported orchestrator_server at launch. TA-5 taught the bridge to
re-baseline its generation stamp after a daemon restart so calls keep flowing;
that is correct, and it is also what makes this mismatch reachable rather than
theoretical, because the bridge now happily proxies to a daemon whose schema it
no longer shares.

The same staleness applies to a whole tool, not just a field: for a tool added
after the bridge started, `_schema_for_tool` returns {}, so every field looks
unknown and the caller got UNKNOWN_TOOL from the bridge for a tool the daemon
serves.

The local screen looked justified by a comment saying it shares the
duplicate-retry ledger with the HTTP dispatcher, so a genuinely bad payload gets
one correction instead of an infinite loop. But the proxy path already runs
`_bridge_dedup`, which applies `_duplicate_retry_guard` to every error in
`_DETERMINISTIC_TOOLBODY_ERRORS` -- UNSUPPORTED_FIELD among them. The ledger was
never the local branch's to protect. So the screen is removed and the daemon
decides: a field the daemon rejects still comes back as UNSUPPORTED_FIELD, from
the daemon, with the daemon's `accepted_fields` -- the only version a caller can
act on.
"""
import asyncio
import json

import code_tools as ct
import orchestrator_server as srv


def _stale_schema_dropping(tool_name, drop_field):
    """A copy of TOOL_SCHEMAS with one parameter removed from one tool.

    This is the exact shape of the real defect: not a corrupt schema, just an
    older one that predates a field.
    """
    import copy
    out = []
    for schema in ct.TOOL_SCHEMAS:
        if (schema.get("function") or {}).get("name") != tool_name:
            out.append(schema)
            continue
        clone = copy.deepcopy(schema)
        props = ((clone.get("function") or {}).get("parameters") or {}).get("properties") or {}
        props.pop(drop_field, None)
        out.append(clone)
    return out


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload


def _isolate(monkeypatch):
    """Fresh duplicate-retry ledger, and no stateful-T4 short circuit.

    The fingerprint deliberately excludes project_path, so a shared ledger would
    let an unrelated test turn UNSUPPORTED_FIELD into DUPLICATE_RETRY_BLOCKED
    here. Give each test its own.
    """
    monkeypatch.setattr(srv, "_DUPLICATE_FAILURE_STATE", {})
    monkeypatch.setattr(srv, "_DUPLICATE_FIRST_SEEN", {})
    monkeypatch.setattr(srv, "_mcp_is_stateful_t4_tool", lambda name: False)


def test_stale_local_schema_does_not_refuse_a_live_field(monkeypatch, tmp_path):
    """The gate's schema is missing a field; the daemon knows it. Daemon answers.

    Asserting on the outcome the caller sees, plus the one structural fact that
    matters (the payload reached the daemon intact), not on which branch ran.
    """
    project = tmp_path / "repo"
    project.mkdir()
    _isolate(monkeypatch)

    monkeypatch.setattr(ct, "TOOL_SCHEMAS",
                        _stale_schema_dropping("git_status", "max_records"))
    # Prove the premise. If the stale copy ever stops looking stale, the test
    # below is vacuous and must be revisited rather than trusted.
    props = ((srv._schema_for_tool("git_status") or {})
             .get("parameters") or {}).get("properties") or {}
    assert "max_records" not in props, \
        "premise gone: the stale schema still lists max_records"

    local = {}

    async def refuse_locally(request, transport="mcp"):
        local["transport"] = transport
        from aiohttp import web
        return web.json_response({"ok": False, "_from": "local"})

    monkeypatch.setattr(srv, "_dispatch_tool_call_request", refuse_locally)

    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"ok": True, "records_total": 0, "_from": "daemon"})

    import urllib.request as ur
    monkeypatch.setattr(ur, "urlopen", fake_urlopen)

    resp = asyncio.run(srv.dispatch_tool_call(
        "git_status",
        {"project_path": str(project), "max_records": 0},
        transport="mcp",
    ))
    payload = json.loads(resp.body.decode("utf-8"))

    assert "transport" not in local, (
        "the gate answered from its own stale schema instead of letting the "
        "daemon decide; payload=%r" % (payload,))
    assert payload.get("ok") is True, payload
    assert "/api/call/git_status" in seen.get("url", ""), seen
    assert seen.get("body", {}).get("max_records") == 0, seen


def test_tool_missing_from_the_local_registry_still_reaches_the_daemon(
        monkeypatch, tmp_path):
    """A tool added after the bridge started must not answer UNKNOWN_TOOL.

    Same staleness, one level up: with no local entry at all every field looks
    unknown, which is how a brand-new tool became invisible over MCP.
    """
    project = tmp_path / "repo"
    project.mkdir()
    _isolate(monkeypatch)

    monkeypatch.setattr(ct, "TOOL_SCHEMAS", [
        s for s in ct.TOOL_SCHEMAS
        if (s.get("function") or {}).get("name") != "git_status"
    ])
    assert srv._schema_for_tool("git_status") == {}, "premise gone"

    local = {}

    async def refuse_locally(request, transport="mcp"):
        local["hit"] = True
        from aiohttp import web
        return web.json_response({"ok": False, "error_name": "UNKNOWN_TOOL"})

    monkeypatch.setattr(srv, "_dispatch_tool_call_request", refuse_locally)

    def fake_urlopen(req, timeout=None):
        return _FakeResponse({"ok": True, "_from": "daemon"})

    import urllib.request as ur
    monkeypatch.setattr(ur, "urlopen", fake_urlopen)

    resp = asyncio.run(srv.dispatch_tool_call(
        "git_status", {"project_path": str(project)}, transport="mcp"))
    payload = json.loads(resp.body.decode("utf-8"))
    assert "hit" not in local, payload
    assert payload.get("ok") is True, payload


def test_progress_index_proxy_has_no_fixed_wall_clock(monkeypatch, tmp_path):
    _isolate(monkeypatch)
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _FakeResponse({"ok": True})

    import urllib.request as ur
    monkeypatch.setattr(ur, "urlopen", fake_urlopen)
    response = asyncio.run(srv.dispatch_tool_call(
        "cbm_incremental_index", {"project_path": str(tmp_path)}, transport="mcp"))
    assert json.loads(response.body)["ok"]
    assert seen["timeout"] is None


def test_a_field_no_layer_knows_still_fails_loud(monkeypatch, tmp_path):
    """Deferring must not become "accept anything".

    The change is about WHO answers, not about whether a bad payload is caught. A
    field neither side declares has to come back as a refusal -- otherwise the
    fix trades a false refusal for a silent acceptance, which is the worse half
    of the A17/A09/A23-P2 family this repo has already paid for three times.
    """
    project = tmp_path / "repo"
    project.mkdir()
    _isolate(monkeypatch)

    class _FakeHTTPError(Exception):
        code = 400
        fp = True

        def read(self):
            return json.dumps({
                "ok": False,
                "error_name": "UNSUPPORTED_FIELD",
                "unsupported_fields": ["totally_bogus_field"],
                "accepted_fields": ["include_ignored", "max_records",
                                    "project_path"],
            }).encode("utf-8")

    import urllib.error as ue
    monkeypatch.setattr(ue, "HTTPError", _FakeHTTPError, raising=False)

    def fake_urlopen(req, timeout=None):
        raise _FakeHTTPError()

    import urllib.request as ur
    monkeypatch.setattr(ur, "urlopen", fake_urlopen)

    resp = asyncio.run(srv.dispatch_tool_call(
        "git_status",
        {"project_path": str(project), "totally_bogus_field": 1},
        transport="mcp",
    ))
    payload = json.loads(resp.body.decode("utf-8"))
    assert payload.get("ok") is False, payload
    assert payload.get("error_name") == "UNSUPPORTED_FIELD", payload
    # The daemon's accepted_fields is the useful half: it is the CURRENT one.
    assert "max_records" in (payload.get("accepted_fields") or []), payload


def test_repeated_bad_payload_is_still_blocked_by_the_ledger(monkeypatch,
                                                            tmp_path):
    """The ledger survives the removal of the local branch.

    This is the guarantee the old comment claimed the local screen was there to
    provide. Pinning it here means a future reader does not have to re-derive
    that `_bridge_dedup` already covers it.
    """
    project = tmp_path / "repo"
    project.mkdir()
    _isolate(monkeypatch)

    class _FakeHTTPError(Exception):
        code = 400
        fp = True

        def read(self):
            return json.dumps({
                "ok": False,
                "error_name": "UNSUPPORTED_FIELD",
                "accepted_fields": ["project_path"],
            }).encode("utf-8")

    import urllib.error as ue
    monkeypatch.setattr(ue, "HTTPError", _FakeHTTPError, raising=False)
    import urllib.request as ur
    monkeypatch.setattr(ur, "urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(
                            _FakeHTTPError()))

    def call_once():
        resp = asyncio.run(srv.dispatch_tool_call(
            "git_status",
            {"project_path": str(project), "totally_bogus_field": 2},
            transport="mcp",
        ))
        return json.loads(resp.body.decode("utf-8"))

    first = call_once()
    assert first.get("error_name") == "UNSUPPORTED_FIELD", first
    second = call_once()
    assert second.get("error_name") == "DUPLICATE_RETRY_BLOCKED", second
    assert second.get("original_error_name") == "UNSUPPORTED_FIELD", second
