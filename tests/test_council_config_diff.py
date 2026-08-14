import asyncio
import json
from pathlib import Path

import code_tools


def _cfg():
    return {
        "roster_order": ["clau", "ope", "codo", "kilo", "pic", "hermes"],
        "agents": {
            "clau": {"label": "ClauC", "enabled": True},
            "codo": {"label": "CodoC", "enabled": True},
            "pic": {"label": "PiC", "enabled": True},
            "ope": {"label": "OpeC", "enabled": True},
            "hermes": {"label": "HermC", "enabled": True},
            "kilo": {"label": "KiloC", "enabled": True},
        },
    }


def _routing_doc(labels: str) -> str:
    return (
        f"{labels}\n"
        "Route by task fit and benchmark evidence. Do not use a cost chain.\n"
        "CodoC is a high-end judgement worker, not default leader.\n"
        "ClauC is outside the normal routing order.\n"
    )


def test_core_rule_v4_pointer_is_in_sync_without_inline_roster(monkeypatch, tmp_path):
    core = tmp_path / "CORE_RULE.txt"
    leader = tmp_path / "council_rule_leader.md"
    worker = tmp_path / "global_rule_worker.md"
    labels = "ClauC CodoC PiC OpeC HermC KiloC"

    core.write_text(
        "## COUNCIL (pointer)\n"
        "SOT: .council/council_config.json+council_rule_leader.md; routing/model live THERE\n",
        encoding="utf-8",
    )
    leader.write_text(_routing_doc(labels), encoding="utf-8")
    worker.write_text(_routing_doc(labels), encoding="utf-8")

    monkeypatch.setattr(code_tools, "_load_council_config", lambda force=True: _cfg())
    monkeypatch.setattr(code_tools, "_council_config_path", lambda: Path(".council/council_config.json"))
    monkeypatch.setattr(code_tools, "_council_doc_targets", lambda: [core, leader, worker])

    payload = json.loads(asyncio.run(code_tools.council_config_diff()))

    assert payload["summary"] == "0 drift / 3 doc targets"
    assert payload["fixed_cost_chain"] is False
    assert payload["normal_routing_excludes"] == ["ClauC"]
    assert {item["target"]: item["status"] for item in payload["drift"]} == {
        "CORE_RULE.txt": "in_sync",
        "council_rule_leader.md": "in_sync",
        "global_rule_worker.md": "in_sync",
    }
    assert payload["drift"][0]["detail"] == "SOT pointer present"


def test_non_core_docs_still_require_roster_and_cost_chain(monkeypatch, tmp_path):
    core = tmp_path / "CORE_RULE.txt"
    leader = tmp_path / "council_rule_leader.md"
    worker = tmp_path / "global_rule_worker.md"
    labels = "ClauC CodoC PiC OpeC HermC KiloC"

    core.write_text("SOT: .council/council_config.json; use convene_council for routing.\n", encoding="utf-8")
    leader.write_text("Council doc intentionally stale.\n", encoding="utf-8")
    worker.write_text(_routing_doc(labels), encoding="utf-8")

    monkeypatch.setattr(code_tools, "_load_council_config", lambda force=True: _cfg())
    monkeypatch.setattr(code_tools, "_council_config_path", lambda: Path(".council/council_config.json"))
    monkeypatch.setattr(code_tools, "_council_doc_targets", lambda: [core, leader, worker])

    payload = json.loads(asyncio.run(code_tools.council_config_diff()))
    statuses = {item["target"]: item for item in payload["drift"]}

    assert statuses["CORE_RULE.txt"]["status"] == "in_sync"
    assert statuses["council_rule_leader.md"]["status"] == "drift"
    assert "missing labels" in statuses["council_rule_leader.md"]["detail"]
    assert "missing no-cost-chain marker" in statuses["council_rule_leader.md"]["detail"]
    assert "missing CodoC-not-default-leader marker" in statuses["council_rule_leader.md"]["detail"]
    assert "missing ClauC-exception marker" in statuses["council_rule_leader.md"]["detail"]
    assert statuses["global_rule_worker.md"]["status"] == "in_sync"
