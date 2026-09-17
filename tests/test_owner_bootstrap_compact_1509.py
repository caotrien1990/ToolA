"""Lost/truncated bootstrap and reconnect must never replace durable ownership."""
import ast
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import code_tools as ct


@pytest.fixture
def owner_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "_SESSIONS_DB", tmp_path / "sessions.db")
    return tmp_path


def run(coro):
    return asyncio.run(coro)


def bootstrap(**kwargs):
    return json.loads(run(ct.bootstrap_session(
        session_id="compact-owner", agent_id="ope",
        owner_claim_id="persisted-claim-0123456789abcdef0123456789abcdef", **kwargs)))


def test_default_is_truncation_resistant_and_full_is_opt_in(owner_db):
    compact = bootstrap()
    wire = json.dumps(compact, ensure_ascii=False)
    assert compact["owner_capability"] in wire[:2048]
    assert compact["owner_claim_id"] in wire[:2048]
    assert "key_tools_compact" not in compact
    assert "active_skills_index" not in compact
    full = bootstrap(verbosity="full")
    assert full["key_tools_compact"]
    assert full["owner_capability"] == compact["owner_capability"]
    assert json.dumps(full).index('"owner_capability"') < json.dumps(full).index('"key_tools_compact"')
    task = json.loads(run(ct.begin_task(
        "compact-owner", "new real task", "ope",
        owner_capability=compact["owner_capability"], owner_claim_id=compact["owner_claim_id"])))
    assert task["ok"] and task["scope"] == "root" and task["task_epoch"] == 1


@pytest.mark.parametrize("committed", [False, True])
def test_empty_cache_after_reconnect_never_mints_over_durable_owner(owner_db, committed):
    first = bootstrap()
    if committed:
        run(ct.begin_task("compact-owner", "task", "ope",
                          owner_capability=first["owner_capability"],
                          owner_claim_id=first["owner_claim_id"]))
    before = ct._task_checkpoint_load("compact-owner")
    with patch.object(ct.secrets, "token_urlsafe", side_effect=AssertionError("must not mint")):
        result = json.loads(run(ct._bootstrap_with_cached_owner_claim(
            "", "compact-owner", "ope", claim_cache={})))
    assert result["error_name"] == "TASK_OWNER_MISMATCH"
    assert result["ca_next_action"] == "retry_with_the_original_owner_claim_id"
    assert ct._task_checkpoint_load("compact-owner") == before


def test_matching_cache_recovers_and_wrong_cache_cannot_replace(owner_db):
    first = bootstrap()
    cache = {("compact-owner", "ope"): first["owner_claim_id"]}
    recovered = json.loads(run(ct._bootstrap_with_cached_owner_claim(
        "", "compact-owner", "ope", claim_cache=cache)))
    assert recovered["owner_capability"] == first["owner_capability"]
    before = ct._task_checkpoint_load("compact-owner")
    cache[("compact-owner", "ope")] = "wrong-claim-0123456789abcdef0123456789abcdef"
    denied = json.loads(run(ct._bootstrap_with_cached_owner_claim(
        "", "compact-owner", "ope", claim_cache=cache)))
    assert denied["error_name"] == "TASK_OWNER_MISMATCH"
    assert ct._task_checkpoint_load("compact-owner") == before


def test_missing_capability_error_is_copy_ready_and_does_not_leak(owner_db):
    first = bootstrap()
    denied = json.loads(run(ct.begin_task("compact-owner", "task", "ope",
                                         owner_claim_id=first["owner_claim_id"])))
    assert denied["ca_next_action"] == "use_server_issued_owner_capability"
    assert {"owner_capability", "owner_claim_id"} <= denied["payload_example"].keys()
    assert first["owner_capability"] not in json.dumps(denied)
    public = json.loads(run(ct.get_task_checkpoint("compact-owner")))
    assert first["owner_capability"] not in json.dumps(public)
    assert first["owner_claim_id"] not in json.dumps(public)
    assert "owner_capability_sha256" not in json.dumps(public)


def test_prepare_checks_claim_before_project_artifacts(owner_db):
    first = bootstrap()
    before = set(owner_db.iterdir())
    with patch.object(ct, "_PREPARE_OWNER_CLAIMS", {}):
        result = json.loads(run(ct.prepare_ca_session(
            str(owner_db), agent_id="ope", host_session_id="compact-owner")))
    assert result["error_name"] == "TASK_OWNER_MISMATCH"
    assert set(owner_db.iterdir()) == before
    helper = AsyncMock(return_value=json.dumps({"ok": False, "error_name": "TEST_STOP"}))
    with patch.object(ct, "_bootstrap_with_cached_owner_claim", helper):
        run(ct.prepare_ca_session(str(owner_db), agent_id="ope",
                                  host_session_id="compact-owner", owner_claim_id=first["owner_claim_id"]))
    assert helper.call_args.args[3] == first["owner_claim_id"]


def test_mcp_prepare_forwards_claim_and_bootstrap_uses_durable_helper():
    source = Path(ct.__file__).parent / "orchestrator/server/mcp_stdio.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    prepare = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "prepare_ca_session")
    assert "owner_claim_id" in {a.arg for a in prepare.args.args}
    # A33-B (2026-09-17): the shortcut now PROXIES to the daemon, so the claim rides
    # in the _mcp_call_text payload dict instead of a call keyword. Assert the
    # FORWARDING, not the transport -- dropping the field still fails here, and a
    # stub literal does not pass because the value must be the parameter itself.
    forwarded_as_keyword = any(
        isinstance(n, ast.keyword) and n.arg == "owner_claim_id" for n in ast.walk(prepare)
    )
    forwarded_in_payload = any(
        isinstance(k, ast.Constant) and k.value == "owner_claim_id"
        and isinstance(v, ast.Name) and v.id == "owner_claim_id"
        for n in ast.walk(prepare) if isinstance(n, ast.Dict)
        for k, v in zip(n.keys, n.values)
    )
    assert forwarded_as_keyword or forwarded_in_payload
    boot = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "bootstrap_session")
    assert any(isinstance(n, ast.Attribute) and n.attr == "_bootstrap_with_cached_owner_claim" for n in ast.walk(boot))
    assert not any(isinstance(n, ast.Attribute) and n.attr == "token_urlsafe" for n in ast.walk(boot))
