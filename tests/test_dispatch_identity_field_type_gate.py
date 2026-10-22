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


def _request(tool_name: str, raw: bytes, headers=None):
    return make_mocked_request(
        "POST", f"/api/call/{tool_name}", headers=headers or {},
        payload=_raw_body(raw), match_info={"tool_name": tool_name})


def _post(tool_name: str, body, headers=None):
    request = _request(tool_name, json.dumps(body).encode("utf-8"),
                       headers=headers)
    response = asyncio.run(orchestrator_server._dispatch_tool_call_request(request))
    return response, json.loads(response.text)


def _post_as(tool_name: str, body, agent_id=None, session_id=None):
    """Dispatch with an explicit caller identity (X-Agent-Id) and, optionally,
    a task session id (X-Session-Id): the header-carrying fixtures the
    agent_id-preservation tests (plan section 5) are built on."""
    headers = {}
    if agent_id is not None:
        headers["X-Agent-Id"] = agent_id
    if session_id is not None:
        headers["X-Session-Id"] = session_id
    return _post(tool_name, body, headers=headers)


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


# =====================================================================
# Lever 2 rehearsal suite: fix-plan section 5 (T1-T12 incl. T4b, T-PART)
# for the dispatcher agent_id overwrite -- the sibling defect of the
# identity-field-type gate above. Written against the UNFIXED tree:
# behavior proofs (T1-T7, T12(i), T11 rename/retarget legs) are expected
# RED, each for the exact reason section 5 names; locks (T8, T9, T10,
# T-PART, T12(ii)/(iii)) are expected GREEN.
#
# Fixture isolation (plan section 5, mandatory): every test that can
# touch the agent registry runs with code_tools._AGENTS_REGISTRY_PATH
# redirected to a throwaway seeded file under tmp_path; session-scoped
# tools run against a throwaway sessions DB (code_tools._SESSIONS_DB
# redirected + _sync_session_db_alias, the established test pattern).
# No test reads or writes the worktree's real agents_registry.json.
# =====================================================================

import inspect
import sqlite3

FIXTURE_PROJECT = "D:/lever2/proj"

_OBJECT_TOOLS = {"manage_agent", "audit_reconciliation_report",
                 "session_audit_summary", "ca_policy_metrics",
                 "mcp_pack_generate"}
_IDENTITY_TOOLS = {"open_session", "close_session", "rename_session",
                   "log_native_event", "create_pending_audit",
                   "sync_pending_audit", "get_session_events", "begin_task",
                   "owner_heartbeat", "bootstrap_session",
                   "prepare_ca_session", "get_rule_core_text"}
_NO_EFFECT_TOOLS = {"hermes_host_gate_check", "classify_ca_action"}


@pytest.fixture(autouse=True)
def _lever2_clean_dedupe_state():
    """The duplicate-retry guard is process-global with no TTL; isolate it
    per test so deliberate repeats (T7/T11/T12) start from a clean slate."""
    orchestrator_server._DUPLICATE_FAILURE_STATE.clear()
    orchestrator_server._DUPLICATE_FIRST_SEEN.clear()
    yield
    orchestrator_server._DUPLICATE_FAILURE_STATE.clear()
    orchestrator_server._DUPLICATE_FIRST_SEEN.clear()


@pytest.fixture()
def agent_registry(tmp_path):
    """Throwaway seeded registry. Marker fields (persona/skill_dir) make
    retargeting observable; rule_file points into tmp_path so set_mode's
    rule sync writes land in the fixture, never in the repo. The handler's
    legacy .bak_ma sweep is neutralized: it scans fixed user-profile roots
    unrelated to the behavior under test."""
    reg_path = tmp_path / "agents_registry.json"
    reg = {"agents": {
        "muse1": {"id": "muse1", "mode": "A", "persona": "FIXTURE-MUSE1",
                  "rule_file": str(tmp_path / "muse1_RULE.md"),
                  "skill_dir": "SKILLDIR-MUSE1"},
        "muse2": {"id": "muse2", "mode": "A", "persona": "FIXTURE-MUSE2",
                  "rule_file": str(tmp_path / "muse2_RULE.md"),
                  "skill_dir": "SKILLDIR-MUSE2"},
    }}
    reg_path.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    with mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", reg_path), \
            mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma",
                              lambda *a, **k: []):
        yield reg_path


@pytest.fixture()
def sessions_db(tmp_path):
    """Throwaway sessions DB with the real schema (_init_session_db),
    wired through the same alias sync the v15 handlers perform."""
    db = tmp_path / "sessions.db"
    old = code_tools._SESSIONS_DB
    code_tools._SESSIONS_DB = db
    code_tools._init_session_db()
    code_tools._sync_session_db_alias()
    try:
        yield db
    finally:
        code_tools._SESSIONS_DB = old
        code_tools._sync_session_db_alias()


def _seed_session(db, session_id, agent_id, started_at):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO sessions (session_id, session_title, project_path,"
            " status, started_at, agent_id) VALUES (?,?,?,?,?,?)",
            (session_id, "fixture-" + session_id,
             code_tools._session_project_key(FIXTURE_PROJECT),
             "active", started_at, agent_id))
        conn.commit()
    finally:
        conn.close()


def _two_agent_sessions(db):
    _seed_session(db, "sess-a-muse1", "muse1", "2026-10-21T00:00:00")
    _seed_session(db, "sess-b-muse2", "muse2", "2026-10-21T01:00:00")


# --- T1: cross-identity read returns the NAMED agent's record ---------

def test_t1_get_returns_named_agent_not_caller(agent_registry):
    _, result = _post_as("manage_agent", {"action": "get", "agent_id": "muse1"},
                         agent_id="muse2")
    assert result["ok"] is True
    assert result["agent"]["id"] == "muse1"
    assert result["agent"]["persona"] == "FIXTURE-MUSE1"
    # 'muse' is not a registered id: naming it must be AGENT_NOT_REGISTERED,
    # never the caller's own record.
    _, result = _post_as("manage_agent", {"action": "get", "agent_id": "muse"},
                         agent_id="muse2")
    assert result["ok"] is False
    assert result["error_name"] == "AGENT_NOT_REGISTERED"


# --- T2: every object tool consumes the body agent_id, not the header -

def test_t2_object_tools_consume_body_agent_id(agent_registry, sessions_db):
    _two_agent_sessions(sessions_db)
    # manage_agent: header muse1, body muse2 -> muse2's record.
    _, result = _post_as("manage_agent", {"action": "get", "agent_id": "muse2"},
                         agent_id="muse1")
    assert result["ok"] is True and result["agent"]["id"] == "muse2"
    # audit_reconciliation_report: explicit id narrows to muse2's session.
    _, result = _post_as("audit_reconciliation_report",
                         {"project_path": FIXTURE_PROJECT, "agent_id": "muse2"},
                         agent_id="muse1")
    assert result["ok"] is True and result["session_id"] == "sess-b-muse2"
    # session_audit_summary: same narrowing.
    _, result = _post_as("session_audit_summary",
                         {"project_path": FIXTURE_PROJECT, "agent_id": "muse2"},
                         agent_id="muse1")
    assert result["ok"] is True and result["session_id"] == "sess-b-muse2"
    # ca_policy_metrics: the consumed filter echoes the body id.
    _, result = _post_as("ca_policy_metrics",
                         {"project_path": FIXTURE_PROJECT, "agent_id": "muse2"},
                         agent_id="muse1")
    assert result["ok"] is True and result["filters"]["agent_id"] == "muse2"


# --- T2 (Amendment 1): mcp_pack_generate -- agent_id is the documented
# --- alias of ca_id, so preservation must restore the alias path --------

def test_t2_mcp_pack_generate_agent_id_only_targets_named_ca(tmp_path):
    # Header identity muse1; the body names the target CA via agent_id
    # ONLY. The pack must be generated for the TARGET, not the caller.
    # dry_run=True writes nothing anywhere; out_root is the fixture tmp
    # dir regardless, so even path resolution stays inside the fixture.
    _, result = _post_as("mcp_pack_generate",
                         {"action": "generate", "pack_id": "toolq",
                          "agent_id": "lever3-target-ca",
                          "out_root": str(tmp_path), "dry_run": True},
                         agent_id="muse1")
    assert result["ok"] is True
    assert result["pack"]["ca_identity"]["ca_id"] == "lever3-target-ca"
    assert result["out_dir"].replace("\\", "/").endswith(
        "toolq/lever3-target-ca")


def test_t2_mcp_pack_generate_ca_id_precedence_lock(tmp_path):
    # ca_id is never dispatcher-managed and wins over agent_id at the
    # generator's resolution (cid = ca_id or agent_id): a regression lock,
    # green before and after the fix.
    _, result = _post_as("mcp_pack_generate",
                         {"action": "generate", "pack_id": "toolq",
                          "ca_id": "lever3-ca", "agent_id": "lever3-other",
                          "out_root": str(tmp_path), "dry_run": True},
                         agent_id="muse1")
    assert result["ok"] is True
    assert result["pack"]["ca_identity"]["ca_id"] == "lever3-ca"



# --- T3: cross-identity write refused, before argument validation ----

def test_t3_set_mode_cross_identity_refused_before_validation(agent_registry):
    before = agent_registry.read_bytes()
    _, result = _post_as("manage_agent",
                         {"action": "set_mode", "agent_id": "muse2",
                          "mode": "native"}, agent_id="muse1")
    assert result["ok"] is False
    assert result["error_name"] == "AGENT_IDENTITY_MISMATCH"
    # The mismatch error names caller and target.
    assert "muse1" in json.dumps(result) and "muse2" in json.dumps(result)
    assert agent_registry.read_bytes() == before
    # Confinement precedes argument validation: an invalid mode from a
    # cross-identity caller is still AGENT_IDENTITY_MISMATCH.
    _, result = _post_as("manage_agent",
                         {"action": "set_mode", "agent_id": "muse2",
                          "mode": "bogus"}, agent_id="muse1")
    assert result["ok"] is False
    assert result["error_name"] == "AGENT_IDENTITY_MISMATCH"
    assert agent_registry.read_bytes() == before


# --- T4: lifecycle, identity asserted per leg -------------------------

def test_t4_register_lifecycle_per_leg_identity(agent_registry, tmp_path):
    new_id = "lever2-throwaway"
    # Leg 1 (creation): the new id registers itself; creation is allowed.
    _, result = _post_as("manage_agent",
                         {"action": "register", "agent_id": new_id,
                          "rule_file": str(tmp_path / "throwaway_RULE.md"),
                          "persona": "FIXTURE-THROWAWAY"}, agent_id=new_id)
    assert result["ok"] is True and result["registered"] == new_id
    # Leg 2 (read): another caller naming the throwaway gets ITS record,
    # not the caller's.
    _, result = _post_as("manage_agent", {"action": "get", "agent_id": new_id},
                         agent_id="muse2")
    assert result["ok"] is True
    assert result["agent"]["id"] == new_id
    assert result["agent"]["persona"] == "FIXTURE-THROWAWAY"
    # Leg 3 (write on an existing record): self set_mode succeeds.
    _, result = _post_as("manage_agent",
                         {"action": "set_mode", "agent_id": new_id,
                          "mode": "native"}, agent_id=new_id)
    assert result["ok"] is True and result["mode"] == "native"
    # Leg 4: self unregister succeeds.
    _, result = _post_as("manage_agent",
                         {"action": "unregister", "agent_id": new_id},
                         agent_id=new_id)
    assert result["ok"] is True
    _, result = _post_as("manage_agent", {"action": "list"}, agent_id="muse2")
    assert new_id not in result["agents"]


# --- T4b: confinement lanes, both polarities --------------------------

def test_t4b_confinement_both_polarities_through_dispatch(agent_registry):
    # Positive polarity through the real dispatch path: header identity ==
    # target succeeds (the handler's internal lane crossings keep the
    # validated identity).
    _, result = _post_as("manage_agent",
                         {"action": "set_mode", "agent_id": "muse2",
                          "mode": "native"}, agent_id="muse2")
    assert result["ok"] is True and result["mode"] == "native"
    # Negative polarity: a mismatched header is refused.
    _, result = _post_as("manage_agent",
                         {"action": "set_mode", "agent_id": "muse2",
                          "mode": "A"}, agent_id="muse1")
    assert result["ok"] is False
    assert result["error_name"] == "AGENT_IDENTITY_MISMATCH"


def test_t4b_empty_validated_identity_fails_closed(agent_registry):
    # Unit level (plan section 5): with the caller-identity context var
    # empty, a write on an existing record must fail closed. (On the
    # no-header dispatch path the resolved identity falls back to the body
    # value, so the empty case is exercised here, at the handler.)
    token = orchestrator_server._A37_AGENT_CTX.set("")
    try:
        raw = asyncio.run(code_tools.manage_agent(
            action="set_mode", agent_id="muse2", mode="native"))
    finally:
        orchestrator_server._A37_AGENT_CTX.reset(token)
    result = json.loads(raw)
    assert result["ok"] is False
    assert result["error_name"] == "AGENT_IDENTITY_MISMATCH"


# --- T5: audit_reconciliation_report absence shapes -------------------

def test_t5_arr_single_candidate_row(sessions_db):
    _seed_session(sessions_db, "sess-muse2-only", "muse2", "2026-10-21T00:00:00")
    _, result = _post_as("audit_reconciliation_report",
                         {"project_path": FIXTURE_PROJECT}, agent_id="muse1")
    assert result["ok"] is True
    assert result["session_id"] == "sess-muse2-only"


def test_t5_arr_two_agents_ambiguous(sessions_db):
    _two_agent_sessions(sessions_db)
    _, result = _post_as("audit_reconciliation_report",
                         {"project_path": FIXTURE_PROJECT}, agent_id="muse1")
    assert result["ok"] is False
    assert result["error_name"] == "SESSION_SCOPE_AMBIGUOUS"


def test_t5_arr_explicit_id_narrows(sessions_db):
    _two_agent_sessions(sessions_db)
    _, result = _post_as("audit_reconciliation_report",
                         {"project_path": FIXTURE_PROJECT, "agent_id": "muse2"},
                         agent_id="muse2")
    assert result["ok"] is True
    assert result["session_id"] == "sess-b-muse2"


# --- T6: agent_id absence reaches every object tool's handler ---------

def test_t6_agent_id_absence_reaches_handler(agent_registry, sessions_db):
    _two_agent_sessions(sessions_db)
    # manage_agent: omitting agent_id surfaces the handler's
    # MISSING_REQUIRED_FIELD, never the caller's identity.
    _, result = _post_as("manage_agent", {"action": "get"}, agent_id="muse1")
    assert result["ok"] is False
    assert result["error_name"] == "MISSING_REQUIRED_FIELD"
    assert result["accepted_fields"] == ["agent_id"]
    # session_audit_summary: absence across two agents is ambiguous.
    _, result = _post_as("session_audit_summary",
                         {"project_path": FIXTURE_PROJECT}, agent_id="muse1")
    assert result["ok"] is False
    assert result["error_name"] == "SESSION_SCOPE_AMBIGUOUS"
    # ca_policy_metrics: the filter value reaching the handler is the
    # as-sent absence (never the caller's identity); the response echo
    # renders that absence as null -- the aggregation layer's
    # pre-existing representation (agent_id or None), per Amendment 2 /
    # F-L3-3. (Pre-fix the dispatcher injects the caller's id, so the
    # echo is "muse1" and this leg is red.)
    _, result = _post_as("ca_policy_metrics",
                         {"project_path": FIXTURE_PROJECT}, agent_id="muse1")
    assert result["ok"] is True and result["filters"]["agent_id"] is None


# --- T7: dedupe fingerprint independence (premise for the section 4.1
# --- DEFER; preservation is what makes the fingerprints distinct) -----

def test_t7_dedupe_fingerprint_independence(agent_registry):
    caller = "lever2-ghost-caller"
    _, r1 = _post_as("manage_agent",
                     {"action": "get", "agent_id": "lever2-ghost-a"},
                     agent_id=caller)
    assert r1["ok"] is False and r1["error_name"] == "AGENT_NOT_REGISTERED"
    # A different unregistered target is an INDEPENDENT attempt: the guard
    # must not block it as a duplicate of the first.
    _, r2 = _post_as("manage_agent",
                     {"action": "get", "agent_id": "lever2-ghost-b"},
                     agent_id=caller)
    assert r2["ok"] is False and r2["error_name"] == "AGENT_NOT_REGISTERED"
    # The SAME failing call repeated does hit the guard.
    _, r3 = _post_as("manage_agent",
                     {"action": "get", "agent_id": "lever2-ghost-a"},
                     agent_id=caller)
    assert r3["error_name"] == "DUPLICATE_RETRY_BLOCKED"
    assert r3["original_error_name"] == "AGENT_NOT_REGISTERED"



# --- T12: register-wipe guard (isolated fixture registry) -------------

def test_t12_register_refused_when_registry_unloadable(tmp_path):
    # File PRESENT but unloadable: register is refused with
    # AGENT_REGISTRY_UNAVAILABLE and the fixture file's bytes are
    # untouched. (Unfixed: register succeeds and the whole-registry save
    # wipes every existing record.)
    bad = tmp_path / "agents_registry.json"
    bad.write_bytes(b"{not valid json")
    body = {"action": "register", "agent_id": "lever2-newbie",
            "rule_file": str(tmp_path / "nb.md")}
    with mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", bad), \
            mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma",
                              lambda *a, **k: []):
        _, result = _post_as("manage_agent", body, agent_id="muse1")
        assert result["ok"] is False
        assert result["error_name"] == "AGENT_REGISTRY_UNAVAILABLE"
        assert bad.read_bytes() == b"{not valid json"
        # The refusal is deterministic-set classified: an identical repeat
        # is duplicate-blocked with the original error named.
        _, result = _post_as("manage_agent", body, agent_id="muse1")
        assert result["error_name"] == "DUPLICATE_RETRY_BLOCKED"
        assert result["original_error_name"] == "AGENT_REGISTRY_UNAVAILABLE"


def test_t12_register_proceeds_when_registry_absent(tmp_path):
    # File ABSENT: register proceeds (legitimate creation).
    absent = tmp_path / "absent_registry.json"
    with mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", absent), \
            mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma",
                              lambda *a, **k: []):
        _, result = _post_as("manage_agent",
                             {"action": "register", "agent_id": "lever2-newbie",
                              "rule_file": str(tmp_path / "nb.md")},
                             agent_id="muse1")
        assert result["ok"] is True
        assert absent.exists()


def test_t12_register_proceeds_when_registry_healthy(agent_registry):
    # File HEALTHY: register proceeds; the wipe guard must not over-fire.
    _, result = _post_as("manage_agent",
                         {"action": "register", "agent_id": "lever2-newbie"},
                         agent_id="muse1")
    assert result["ok"] is True
    assert result.get("error_name") != "AGENT_REGISTRY_UNAVAILABLE"


# --- Group-3 G1: registry state matrix, deletion branches, observability
# (plan sections 3.9 / 3.12 / 3.7). Same isolation discipline as T12:
# code_tools._AGENTS_REGISTRY_PATH is redirected to a throwaway file and
# the legacy .bak_ma sweep is neutralized; the real registry is never
# touched.

import logging
from pathlib import Path

import orchestrator.core.ca_policy_matrix as _cpm


def _g1_registry(tmp_path, raw: bytes):
    reg_path = tmp_path / "agents_registry.json"
    reg_path.write_bytes(raw)
    return reg_path


@pytest.mark.parametrize("raw,expected_state", [
    (b"{}", "empty_present"),
    (b'{"agents": {}}', "empty_present"),
    (b'{"agents": null}', "off_shape"),
    (b"[]", "off_shape"),
    (b"42", "off_shape"),
])
def test_t12x_register_refuses_with_registry_state(tmp_path, raw, expected_state):
    # Section 3.9: every FAILED shape refuses register with
    # AGENT_REGISTRY_UNAVAILABLE and names its class in registry_state;
    # the file on disk is untouched.
    reg_path = _g1_registry(tmp_path, raw)
    with mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", reg_path), \
            mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma",
                              lambda *a, **k: []):
        _, result = _post_as("manage_agent",
                             {"action": "register", "agent_id": "lever2-newbie"},
                             agent_id="muse1")
        assert result["ok"] is False
        assert result["error_name"] == "AGENT_REGISTRY_UNAVAILABLE"
        assert result["registry_state"] == expected_state
        assert reg_path.read_bytes() == raw


@pytest.mark.parametrize("raw", [
    b"{}", b'{"agents": {}}', b'{"agents": null}', b"[]", b"42",
])
def test_t12x_no_action_crashes_on_failed_states(tmp_path, raw):
    # Section 3.9: on a FAILED load state the substitute keeps every
    # other action crash-free (the pre-fix {"agents": null} shape made
    # reg.setdefault return None and the handler raise).
    reg_path = _g1_registry(tmp_path, raw)
    with mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", reg_path), \
            mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma",
                              lambda *a, **k: []):
        for body in ({"action": "list"},
                     {"action": "get", "agent_id": "ghost"},
                     {"action": "set_mode", "agent_id": "ghost", "mode": "A"},
                     {"action": "unregister", "agent_id": "ghost"}):
            _, result = _post_as("manage_agent", body, agent_id="muse1")
            assert isinstance(result, dict) and "ok" in result
            assert result.get("error_name") != "INTERNAL_ERROR"


def test_t12x_unregister_last_agent_deletes_file_with_backup(tmp_path):
    # Section 3.9: unregistering the LAST agent deletes the registry
    # file (missing stays the only empty representation), after backing
    # it up to the fixed sibling name; register then recreates it.
    reg_path = tmp_path / "agents_registry.json"
    solo = {"agents": {"solo": {"id": "solo", "mode": "A"}}}
    reg_path.write_text(json.dumps(solo), encoding="utf-8")
    with mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", reg_path), \
            mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma",
                              lambda *a, **k: []):
        _, result = _post_as("manage_agent",
                             {"action": "unregister", "agent_id": "solo"},
                             agent_id="solo")
        assert result["ok"] is True
        assert not reg_path.exists()
        bak = tmp_path / "agents_registry.json.bak"
        assert json.loads(bak.read_text(encoding="utf-8")) == solo
        _, result = _post_as("manage_agent",
                             {"action": "register", "agent_id": "fresh"},
                             agent_id="solo")
        assert result["ok"] is True
        assert reg_path.exists()


def test_t12x_unregister_last_backup_failure_keeps_present_empty(tmp_path, caplog):
    # Section 3.12, branch 1: the backup write fails -> the file is NOT
    # deleted; the degraded fallback (empty-dict save) lands instead,
    # the degraded state is logged, and register refuses into the
    # present-empty file.
    reg_path = tmp_path / "agents_registry.json"
    solo = {"agents": {"solo": {"id": "solo", "mode": "A"}}}
    reg_path.write_text(json.dumps(solo), encoding="utf-8")
    real_write_bytes = code_tools._atomic_write_bytes

    def _bak_blocked(path, data):
        if str(path).endswith(".bak"):
            raise OSError("backup target locked")
        return real_write_bytes(path, data)

    with mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", reg_path), \
            mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma",
                              lambda *a, **k: []), \
            mock.patch.object(code_tools, "_atomic_write_bytes", _bak_blocked):
        with caplog.at_level(logging.WARNING):
            _, result = _post_as("manage_agent",
                                 {"action": "unregister", "agent_id": "solo"},
                                 agent_id="solo")
        assert result["ok"] is True
        assert reg_path.exists()
        assert json.loads(reg_path.read_text(encoding="utf-8")) == {"agents": {}}
        assert "present-empty" in caplog.text
        _, result = _post_as("manage_agent",
                             {"action": "register", "agent_id": "fresh"},
                             agent_id="solo")
        assert result["ok"] is False
        assert result["error_name"] == "AGENT_REGISTRY_UNAVAILABLE"
        assert result["registry_state"] == "empty_present"


def test_t12x_unregister_last_delete_failure_falls_back(tmp_path, caplog):
    # Section 3.12, branch 2: the delete itself fails (locked file) ->
    # the backup DID land, the empty-dict fallback is saved, the
    # degraded state is logged, and register refuses into the
    # present-empty file until it is manually removed.
    reg_path = tmp_path / "agents_registry.json"
    solo = {"agents": {"solo": {"id": "solo", "mode": "A"}}}
    reg_path.write_text(json.dumps(solo), encoding="utf-8")
    real_unlink = Path.unlink

    def _locked_unlink(self, *args, **kwargs):
        if self.name == "agents_registry.json":
            raise PermissionError("registry file locked")
        return real_unlink(self, *args, **kwargs)

    with mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", reg_path), \
            mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma",
                              lambda *a, **k: []), \
            mock.patch.object(Path, "unlink", _locked_unlink):
        with caplog.at_level(logging.WARNING):
            _, result = _post_as("manage_agent",
                                 {"action": "unregister", "agent_id": "solo"},
                                 agent_id="solo")
        assert result["ok"] is True
        assert "present-empty" in caplog.text
    assert reg_path.exists()
    assert json.loads(reg_path.read_text(encoding="utf-8")) == {"agents": {}}
    assert (tmp_path / "agents_registry.json.bak").exists()
    with mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", reg_path), \
            mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma",
                              lambda *a, **k: []):
        _, result = _post_as("manage_agent",
                             {"action": "register", "agent_id": "fresh"},
                             agent_id="solo")
        assert result["ok"] is False
        assert result["registry_state"] == "empty_present"


def _load_failed_lines(caplog):
    return [r.getMessage() for r in caplog.records
            if "agents_registry load failed" in r.getMessage()]


def test_obs_corrupt_registry_logs_one_unparseable_line(tmp_path, caplog):
    # Section 3.7 (consumer: loader via companion, manage_agent get):
    # exactly ONE warning naming path + failure_class + exception class.
    # (The pre-fix companion re-read would have logged twice.)
    reg_path = _g1_registry(tmp_path, b"{not valid json")
    with mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", reg_path), \
            mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma",
                              lambda *a, **k: []):
        with caplog.at_level(logging.WARNING):
            _, result = _post_as("manage_agent",
                                 {"action": "get", "agent_id": "ghost"},
                                 agent_id="muse1")
    assert result["error_name"] == "AGENT_NOT_REGISTERED"
    lines = _load_failed_lines(caplog)
    assert len(lines) == 1
    assert "failure_class=unparseable" in lines[0]
    assert "exc=JSONDecodeError" in lines[0]
    assert str(reg_path) in lines[0]


def test_obs_off_shape_registry_logs_one_off_shape_line(tmp_path, caplog):
    # Section 3.7: the off_shape class is decided at the loader, once.
    reg_path = _g1_registry(tmp_path, b'{"agents": null}')
    with mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", reg_path), \
            mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma",
                              lambda *a, **k: []):
        with caplog.at_level(logging.WARNING):
            _post_as("manage_agent", {"action": "get", "agent_id": "ghost"},
                     agent_id="muse1")
    lines = _load_failed_lines(caplog)
    assert len(lines) == 1
    assert "failure_class=off_shape" in lines[0]


def test_obs_present_empty_logs_empty_present_at_register(tmp_path, caplog):
    # Section 3.7: a present-empty file logs failure_class=empty_present
    # once, at the load behind the register refusal.
    reg_path = _g1_registry(tmp_path, b'{"agents": {}}')
    with mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", reg_path), \
            mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma",
                              lambda *a, **k: []):
        with caplog.at_level(logging.WARNING):
            _, result = _post_as("manage_agent",
                                 {"action": "register", "agent_id": "lever2-newbie"},
                                 agent_id="muse1")
    assert result["registry_state"] == "empty_present"
    lines = _load_failed_lines(caplog)
    assert len(lines) == 1
    assert "failure_class=empty_present" in lines[0]


def test_obs_missing_registry_logs_nothing(tmp_path, caplog):
    # Section 3.7 / Rule O: a missing file is a legitimate state and
    # produces NO warning line at all.
    absent = tmp_path / "agents_registry.json"
    with mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", absent), \
            mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma",
                              lambda *a, **k: []):
        with caplog.at_level(logging.WARNING):
            _, result = _post_as("manage_agent",
                                 {"action": "register", "agent_id": "lever2-newbie"},
                                 agent_id="muse1")
    assert result["ok"] is True
    assert _load_failed_lines(caplog) == []
    assert not any("agents_registry" in r.getMessage()
                   for r in caplog.records if r.levelno >= logging.WARNING)


def test_obs_save_fallback_mode_logs_shared_inplace(tmp_path, caplog):
    # Section 3.7: a save forced down the shared-inplace fallback logs
    # the save-mode warning; the mode itself now propagates out of
    # _atomic_write_json (Area 2c).
    reg_path = tmp_path / "agents_registry.json"
    with mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", reg_path), \
            mock.patch.object(code_tools, "_atomic_write_text",
                              return_value="shared_inplace"):
        with caplog.at_level(logging.WARNING):
            code_tools._save_agents_registry({"agents": {}})
    assert any("shared_inplace" in r.getMessage() for r in caplog.records)
    target = tmp_path / "plain.json"
    assert code_tools._atomic_write_json(target, {"a": 1}) == "atomic_replace"


def test_obs_failsafe_consumer_missing_and_corrupt(tmp_path, caplog):
    # Section 3.7 (consumer: ca_policy_matrix.is_agent_memory_target,
    # the :271 bare reader call with no existence guard): a missing file
    # yields NO log line (FileNotFoundError exemption) and the unchanged
    # False return; a corrupt file yields exactly one unparseable line.
    target = str(tmp_path / "home" / "projects" / "slug" / "memory" / "note.md")
    missing = tmp_path / "missing_registry.json"
    with mock.patch.object(_cpm, "_AGENT_REGISTRY_PATH", missing):
        with caplog.at_level(logging.WARNING):
            assert _cpm.is_agent_memory_target(target) is False
    assert _load_failed_lines(caplog) == []
    caplog.clear()
    corrupt = _g1_registry(tmp_path, b"{bad json")
    with mock.patch.object(_cpm, "_AGENT_REGISTRY_PATH", corrupt):
        with caplog.at_level(logging.WARNING):
            assert _cpm.is_agent_memory_target(target) is False
    lines = _load_failed_lines(caplog)
    assert len(lines) == 1
    assert "failure_class=unparseable" in lines[0]


def test_obs_failloud_consumer_raises_with_log_line(tmp_path, caplog):
    # Section 3.7 (consumer: open_session_policy_payload, the :1110
    # fail-loud path): still raises on a corrupt registry -- now with
    # exactly one unparseable log line naming the failure.
    corrupt = _g1_registry(tmp_path, b"{bad json")
    with mock.patch.object(_cpm, "_AGENT_REGISTRY_PATH", corrupt):
        with caplog.at_level(logging.WARNING):
            with pytest.raises(json.JSONDecodeError):
                _cpm.open_session_policy_payload(agent_id="muse1")
    lines = _load_failed_lines(caplog)
    assert len(lines) == 1
    assert "failure_class=unparseable" in lines[0]

# --- Group-3 G2: register dry_run parity (plan section 3.11, Area 5 /
# Rule D). A dry_run register runs every gate a real call runs, returns
# the unregister-shaped preview, and performs ZERO filesystem writes:
# the registry file's bytes are hash-identical before/after and nothing
# from the preview is visible to a later call.

import hashlib


def _sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_g2_register_dry_run_new_agent_preview_writes_nothing(agent_registry):
    before = _sha256_of(agent_registry)
    _, result = _post_as("manage_agent",
                         {"action": "register", "agent_id": "lever2-newbie",
                          "persona": "PREVIEW-PERSONA", "dry_run": True},
                         agent_id="muse1")
    assert result["ok"] is True
    assert result["action"] == "register"
    assert result["agent_id"] == "lever2-newbie"
    assert result["dry_run"] is True
    assert result["existed"] is False
    # The would-be record is fully built, exactly as a real call builds it.
    assert result["agent"]["id"] == "lever2-newbie"
    assert result["agent"]["persona"] == "PREVIEW-PERSONA"
    assert result["agent"]["mode"] == "A"
    assert _sha256_of(agent_registry) == before
    # Nothing persisted: the previewed id is still unknown afterwards.
    _, result = _post_as("manage_agent",
                         {"action": "get", "agent_id": "lever2-newbie"},
                         agent_id="muse1")
    assert result["ok"] is False
    assert result["error_name"] == "AGENT_NOT_REGISTERED"
    # The same call WITHOUT dry_run does persist (preview != real).
    _, result = _post_as("manage_agent",
                         {"action": "register", "agent_id": "lever2-newbie",
                          "persona": "PREVIEW-PERSONA"},
                         agent_id="muse1")
    assert result["ok"] is True
    assert _sha256_of(agent_registry) != before


def test_g2_register_dry_run_existing_agent_preview_writes_nothing(agent_registry):
    before = _sha256_of(agent_registry)
    _, result = _post_as("manage_agent",
                         {"action": "register", "agent_id": "muse1",
                          "persona": "CHANGED-IN-PREVIEW", "dry_run": True},
                         agent_id="muse1")
    assert result["ok"] is True
    assert result["existed"] is True
    assert result["agent"]["persona"] == "CHANGED-IN-PREVIEW"
    assert _sha256_of(agent_registry) == before
    # The stored record still carries the fixture persona.
    _, result = _post_as("manage_agent",
                         {"action": "get", "agent_id": "muse1"},
                         agent_id="muse1")
    assert result["agent"]["persona"] == "FIXTURE-MUSE1"


def test_g2_register_dry_run_missing_agent_id_still_refused(agent_registry):
    before = _sha256_of(agent_registry)
    _, result = _post_as("manage_agent",
                         {"action": "register", "dry_run": True},
                         agent_id="muse1")
    assert result["ok"] is False
    assert result["error_name"] == "MISSING_REQUIRED_FIELD"
    assert _sha256_of(agent_registry) == before


def test_g2_register_dry_run_confinement_still_refused(agent_registry):
    # Registering over an EXISTING id is confined (caller == target);
    # the refusal must fire under dry_run exactly as for a real call.
    before = _sha256_of(agent_registry)
    _, result = _post_as("manage_agent",
                         {"action": "register", "agent_id": "muse2",
                          "persona": "SPOOF", "dry_run": True},
                         agent_id="muse1")
    assert result["ok"] is False
    assert result["error_name"] == "AGENT_IDENTITY_MISMATCH"
    assert _sha256_of(agent_registry) == before


@pytest.mark.parametrize("raw,expected_state", [
    (b"{}", "empty_present"),
    (b'{"agents": null}', "off_shape"),
])
def test_g2_register_dry_run_registry_state_still_refused(tmp_path, raw,
                                                           expected_state):
    # Section 3.11: the Group-3 registry-state refusal (Gate G1) fires
    # under dry_run too, naming the state; the file is untouched.
    reg_path = _g1_registry(tmp_path, raw)
    with mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", reg_path), \
            mock.patch.object(code_tools, "_ma_archive_legacy_bak_ma",
                              lambda *a, **k: []):
        _, result = _post_as("manage_agent",
                             {"action": "register", "agent_id": "lever2-newbie",
                              "dry_run": True},
                             agent_id="muse1")
        assert result["ok"] is False
        assert result["error_name"] == "AGENT_REGISTRY_UNAVAILABLE"
        assert result["registry_state"] == expected_state
        assert reg_path.read_bytes() == raw
# --- T8 (lock): identity tools still canonicalize to the caller -------
# Spoof shape per tool: body agent_id names a DIFFERENT registered id;
# the identity delivered to the handler must remain the caller's. The
# probe replaces the handler at this harness's own TOOL_MAP seam and
# mirrors a declaring signature: the dispatcher canonicalizes into the
# body before fn(**body), so the delivered value is exactly what the
# real handler would execute with. (A handler-direct call cannot express
# this test -- it bypasses the dispatcher, and for these tools returns
# identical outcomes pre/post fix.)

_T8_BODIES = {
    "open_session": {},
    "close_session": {"session_id": "lever2-t8-session"},
    "rename_session": {"project_path": FIXTURE_PROJECT, "new_title": "x"},
    "log_native_event": {"action_type": "read", "tool_name": "read_file_range",
                         "project_path": FIXTURE_PROJECT},
    "create_pending_audit": {"project_path": FIXTURE_PROJECT,
                             "action_type": "read",
                             "tool_name": "read_file_range"},
    "sync_pending_audit": {"project_path": FIXTURE_PROJECT},
    "get_session_events": {},
    "begin_task": {"session_id": "lever2-t8-session", "prompt": "x"},
    "owner_heartbeat": {"session_id": "lever2-t8-session",
                        "owner_capability": "x"},
    "bootstrap_session": {"session_id": "lever2-t8-session",
                          "owner_claim_id": "x"},
    "prepare_ca_session": {"project_path": FIXTURE_PROJECT},
    "get_rule_core_text": {},
}


@pytest.mark.parametrize("tool", sorted(_T8_BODIES))
def test_t8_identity_tools_canonicalize_to_caller(tool, sessions_db):
    _seed_session(sessions_db, "lever2-t8-session", "muse1", "2026-10-21T00:00:00")
    delivered = []

    async def probe(agent_id="", session_id="", target_session_id="", **kwargs):
        delivered.append(agent_id)
        return json.dumps({"ok": True, "probe": True})

    body = {"agent_id": "muse2", **_T8_BODIES[tool]}
    with mock.patch.dict(code_tools.TOOL_MAP, {tool: probe}):
        _, result = _post_as(tool, body, agent_id="muse1",
                             session_id="lever2-t8-session")
    assert result["ok"] is True
    assert delivered == ["muse1"]


# --- T9 (lock): sync_* outcomes do not depend on the agent_id field ---

def test_t9_sync_outcomes_independent_of_agent_id(agent_registry, tmp_path):
    reg = json.loads(agent_registry.read_text(encoding="utf-8"))
    mcp_path = tmp_path / "muse1_mcp.json"
    reg["agents"]["muse1"]["mcp_file"] = str(mcp_path)
    agent_registry.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    rule_path = tmp_path / "muse1_RULE.md"

    def run_sync(agent_id_value):
        for p in (rule_path, mcp_path):
            if p.exists():
                p.unlink()
        _, rr = _post_as("manage_agent",
                         {"action": "sync_rule", "target": "muse1",
                          "agent_id": agent_id_value}, agent_id="muse1")
        _, rm = _post_as("manage_agent",
                         {"action": "sync_mcp", "target": "muse1",
                          "agent_id": agent_id_value}, agent_id="muse1")
        return (rr.get("ok"), rm.get("ok"),
                rule_path.read_bytes() if rule_path.exists() else None,
                mcp_path.read_bytes() if mcp_path.exists() else None)

    first = run_sync("muse1")
    assert first[0] is True and first[1] is True
    assert run_sync("muse2") == first


# --- T10 (lock): no-effect tools echo the caller, outcomes unchanged --

def test_t10_no_effect_tools_echo_caller_identity():
    _, result = _post_as("classify_ca_action",
                         {"tool_name": "read_file_range", "action_type": "read",
                          "agent_id": "muse2"}, agent_id="muse1")
    assert result["ok"] is True
    assert result["host_gate"]["agent_id"] == "muse1"
    _, result = _post_as("hermes_host_gate_check",
                         {"tool_name": "read_file_range", "action_type": "read",
                          "agent_id": "muse2"}, agent_id="muse1")
    assert result["host_gate"]["agent_id"] == "muse1"


# --- T11: error-name semantics (section 4.2) ---------------------------
# Issued as caller muse1 per section 5. The rename legs are RED on the
# unfixed tree by retarget (the call succeeds against the caller's own
# record); the preserved-shape legs pin what the fix must produce.

def test_t11_unregistered_id_error_name_and_boundary(agent_registry):
    for action, extra in (("get", {}), ("unregister", {}),
                          ("set_mode", {"mode": "native"})):
        _, result = _post_as("manage_agent",
                             {"action": action, "agent_id": "lever2-ghost",
                              **extra}, agent_id="muse1")
        assert result["ok"] is False, action
        assert result["error_name"] == "AGENT_NOT_REGISTERED", action
        assert result["accepted_fields"] == ["muse1", "muse2"], action
        # Boundary guidance (section 4.2 as corrected by Amendment 2 /
        # F-L3-1): the handler layer stamps ca_next_action on every error
        # dict it builds and the boundary preserves the stamp, so the
        # observed value is the handler's, not the boundary map's
        # "read_error" default. No hint, no retry sequence, phase
        # "runtime" (the maps' defaults) hold as pinned.
        assert result["ca_next_action"] == \
            "inspect_error_context_or_report_backend_issue", action
        assert not result.get("hint"), action
        assert result["retry_sequence"] == [], action
        assert result["phase"] == "runtime", action


def test_t11_empty_id_shapes(agent_registry):
    for action, extra in (("get", {}), ("unregister", {}),
                          ("set_mode", {"mode": "native"})):
        _, result = _post_as("manage_agent",
                             {"action": action, "agent_id": "", **extra},
                             agent_id="muse1")
        assert result["ok"] is False, action
        assert result["error_name"] == "MISSING_REQUIRED_FIELD", action
        assert result["accepted_fields"] == ["agent_id"], action
    # register with an empty id: MISSING_REQUIRED_FIELD after the section
    # 4.2 site-2 rename -- schema phase.
    _, result = _post_as("manage_agent", {"action": "register", "agent_id": ""},
                         agent_id="muse1")
    assert result["ok"] is False
    assert result["error_name"] == "MISSING_REQUIRED_FIELD"
    assert result["accepted_fields"] == ["agent_id"]
    assert result["phase"] == "schema"
    # The boundary's missing-required enrichment names the field only
    # when it is ABSENT from the body (Amendment 2 / F-L3-2): the
    # sent-empty shape above must NOT name it; the omitted shape must.
    assert "agent_id" not in result.get("missing_fields", [])
    _, result = _post_as("manage_agent", {"action": "register"},
                         agent_id="muse1")
    assert result["ok"] is False
    assert result["error_name"] == "MISSING_REQUIRED_FIELD"
    assert "agent_id" in result.get("missing_fields", [])


def test_t11_agent_not_registered_is_duplicate_blockable(agent_registry):
    body = {"action": "get", "agent_id": "lever2-ghost"}
    _, r1 = _post_as("manage_agent", body, agent_id="muse1")
    assert r1["error_name"] == "AGENT_NOT_REGISTERED"
    _, r2 = _post_as("manage_agent", body, agent_id="muse1")
    assert r2["error_name"] == "DUPLICATE_RETRY_BLOCKED"
    assert r2["original_error_name"] == "AGENT_NOT_REGISTERED"


# --- T-PART (lock): the agent_id partition over the runtime tool map --

def test_tpart_universe_size_matches_step0():
    # Step-0 audit_tool_matrix count, re-asserted on the runtime map
    # (import-time enumeration undercounts: registration completes only
    # after orchestrator_server is imported).
    assert len(code_tools.TOOL_MAP) == 258


def test_tpart_agent_id_partition():
    declaring = set()
    for name, fn in code_tools.TOOL_MAP.items():
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            continue
        if "agent_id" in params:
            declaring.add(name)
    expected = _OBJECT_TOOLS | _IDENTITY_TOOLS | _NO_EFFECT_TOOLS
    assert declaring == expected, (
        f"extra={sorted(declaring - expected)} "
        f"missing={sorted(expected - declaring)}")
    # The fix's classification constant is asserted only when it exists:
    # it is introduced BY the fix, so it is absent on the unfixed tree and
    # this leg stays inert until then.
    from orchestrator.core import registry_data
    constant = getattr(registry_data, "AGENT_ID_OBJECT_TOOLS", None)
    if constant is not None:
        assert set(constant) == _OBJECT_TOOLS
