"""Exercise server-only return builders without dispatching a worker or restarting."""
import asyncio
import json
from unittest.mock import AsyncMock

import orchestrator_server as srv


def test_sync_reply_fixture_matches_real_response_builder(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(srv, "_COUNCIL_AGENTS", ("probe",))
    monkeypatch.setattr(srv, "_council_agent_caps", lambda _: {"enabled": True})
    monkeypatch.setattr(srv, "_artifact_already_complete", lambda *a: None)
    monkeypatch.setattr(srv, "_council_worker_model_configured", lambda *a: True)
    monkeypatch.setattr(srv, "_council_runtime_status", lambda *a: {"state": "idle"})
    monkeypatch.setenv("CBM_ENRICH_COUNCIL", "0")
    queue = AsyncMock(return_value={"ok": True, "reply_text": "fixture reply", "error": "",
                                    "provider": "fixture-provider", "model": "fixture-model"})
    monkeypatch.setattr(srv, "_council_enqueue_or_run", queue)
    result = json.loads(asyncio.run(srv.council_message(
        sender="user", to="probe", text="fixture request")))
    fixture = Path(__file__).parent / "fixtures/returns_shape/council_message__dispatched_reply.json"
    assert result == json.loads(fixture.read_text(encoding="utf-8"))
    queue.assert_awaited_once()


def test_metadata_changes_only_returns_and_examples():
    import copy
    import code_tools as ct
    from orchestrator.core.server_schema_metadata import enrich, METADATA
    for entry in ct.TOOL_SCHEMAS:
        fn = entry["function"]
        name = fn["name"]
        if name not in METADATA:
            continue
        plain = copy.deepcopy(fn)
        plain.pop("returns")
        for prop in plain["parameters"]["properties"].values():
            prop.pop("example", None)
        enriched = enrich(name, plain)
        assert enriched["returns"] == fn["returns"]
        for key, value in METADATA[name]["parameter_examples"].items():
            assert enriched["parameters"]["properties"][key]["example"] == value
        restored = copy.deepcopy(enriched)
        restored.pop("returns")
        for prop in restored["parameters"]["properties"].values():
            prop.pop("example", None)
        assert restored == plain
    untouched = {"parameters": {"properties": {}}}
    assert enrich("other_tool", untouched) is untouched
