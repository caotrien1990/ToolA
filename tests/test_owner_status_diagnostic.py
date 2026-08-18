"""Regression tests for the non-secret owner-status and heartbeat diagnostic."""
import time
from datetime import datetime, timedelta

from code_tools import _owner_status_from_checkpoint


def test_committed_owner_does_not_expose_capability():
    result = _owner_status_from_checkpoint(
        {
            "agent_id": "codo",
            "owner_capability_sha256": "hidden",
            "owner_claim_status": "committed",
        },
        "",
    )
    assert result["state"] == "committed"
    assert result["owner_liveness"] == "unknown_without_heartbeat"
    assert result["stuck_suspected"] == "unknown"
    assert result["orphan_suspected"] == "unknown"
    assert "owner_capability_sha256" not in result
    assert "owner_identity_id" not in result


def test_pending_owner_is_not_called_live():
    result = _owner_status_from_checkpoint(
        {
            "agent_id": "codo",
            "owner_capability_sha256": "hidden",
            "owner_claim_status": "pending",
            "owner_claim_expires_at": time.time() + 120,
        },
        datetime.now().isoformat(timespec="seconds"),
    )
    assert result["state"] == "pending"
    assert result["owner_liveness"] == "pending_not_committed"
    assert result["stuck_suspected"] == "unknown"
    assert result["orphan_suspected"] is False
    assert result["claim_expires_in_seconds"] > 0


def test_expired_pending_owner_is_orphan_suspected():
    result = _owner_status_from_checkpoint(
        {
            "agent_id": "codo",
            "owner_capability_sha256": "hidden",
            "owner_claim_status": "pending",
            "owner_claim_expires_at": time.time() - 1,
        },
        datetime.now().isoformat(timespec="seconds"),
    )
    assert result["state"] == "expired"
    assert result["owner_liveness"] == "not_confirmed"
    assert result["stuck_suspected"] is True
    assert result["orphan_suspected"] is True


def test_old_checkpoint_is_marked_stale_without_claiming_orphan():
    old = (datetime.now() - timedelta(seconds=1000)).isoformat(timespec="seconds")
    result = _owner_status_from_checkpoint(
        {
            "agent_id": "codo",
            "owner_capability_sha256": "hidden",
            "owner_claim_status": "committed",
        },
        old,
    )
    assert result["checkpoint_freshness"] == "stale"
    assert result["stale_checkpoint_suspected"] is True
    assert result["heartbeat_freshness"] == "stale"
    assert result["owner_liveness"] == "stale_suspected"
    assert result["stuck_suspected"] is True
    assert result["orphan_suspected"] == "unknown"


def test_fresh_owner_heartbeat_is_active():
    now = time.time()
    result = _owner_status_from_checkpoint(
        {
            "agent_id": "codo",
            "owner_capability_sha256": "hidden",
            "owner_claim_status": "committed",
            "owner_last_heartbeat_at": now - 5,
            "owner_last_heartbeat_at_iso": datetime.fromtimestamp(now - 5).isoformat(timespec="seconds"),
            "owner_heartbeat_count": 3,
        },
        "",
        now=now,
    )
    assert result["heartbeat_seen"] is True
    assert result["heartbeat_freshness"] == "fresh"
    assert result["activity_source"] == "owner_heartbeat"
    assert result["owner_liveness"] == "active"
    assert result["stuck_suspected"] is False


def test_stale_owner_heartbeat_is_stuck_suspected():
    now = time.time()
    result = _owner_status_from_checkpoint(
        {
            "agent_id": "codo",
            "owner_capability_sha256": "hidden",
            "owner_claim_status": "committed",
            "owner_last_heartbeat_at": now - 300,
            "owner_last_heartbeat_at_iso": datetime.fromtimestamp(now - 300).isoformat(timespec="seconds"),
            "owner_heartbeat_count": 3,
        },
        "",
        now=now,
    )
    assert result["heartbeat_seen"] is True
    assert result["heartbeat_freshness"] == "stale"
    assert result["owner_liveness"] == "stale_suspected"
    assert result["stuck_suspected"] is True


def test_unowned_checkpoint_is_explicit():
    result = _owner_status_from_checkpoint({}, "")
    assert result["state"] == "unowned"
    assert result["owner_liveness"] == "not_claimed"
    assert result["capability_commitment_present"] is False


def test_authenticated_task_activity_updates_committed_root(monkeypatch):
    import json

    import code_tools

    captured = {}
    previous = {
        "agent_id": "codo",
        "owner_capability_sha256": "hidden",
        "owner_claim_status": "committed",
        "task_epoch": 7,
        "prompt_hash": "prompt-7",
        "owner_heartbeat_count": 2,
    }

    def fake_update(session_key, update_fn):
        replacement, response = update_fn(dict(previous))
        captured["session_key"] = session_key
        captured["replacement"] = replacement
        return replacement, response, replacement is not None

    monkeypatch.setattr(code_tools, "_task_checkpoint_update", fake_update)
    result = code_tools._record_authenticated_owner_activity(
        "session-1", "codo", 7, "prompt-7", "get_rule_core_text"
    )

    assert result["recorded"] is True
    assert result["owner_liveness"] == "active"
    assert result["tool_name"] == "get_rule_core_text"
    assert result["heartbeat_count"] == 3
    assert result["heartbeat_freshness"] == "fresh"
    assert captured["session_key"] == "session-1"
    assert captured["replacement"]["owner_heartbeat_source"] == "authenticated_task_activity"


def test_authenticated_task_activity_rejects_stale_envelope_without_write(monkeypatch):
    import code_tools

    captured = {}

    def fake_update(session_key, update_fn):
        replacement, response = update_fn({
            "agent_id": "codo",
            "owner_capability_sha256": "hidden",
            "owner_claim_status": "committed",
            "task_epoch": 7,
            "prompt_hash": "prompt-7",
        })
        captured["replacement"] = replacement
        return replacement, response, replacement is not None

    monkeypatch.setattr(code_tools, "_task_checkpoint_update", fake_update)
    result = code_tools._record_authenticated_owner_activity(
        "session-1", "codo", 6, "prompt-7", "get_rule_core_text"
    )

    assert result["recorded"] is False
    assert result["reason"] == "task_epoch_mismatch"
    assert captured["replacement"] is None
