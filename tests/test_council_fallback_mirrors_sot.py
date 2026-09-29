"""Drift guard: every council FALLBACK table must mirror what the SOT
.council/council_config.json yields, and served prose must not resurrect
retired workers.

WHY this file exists: a fallback table is used exactly when the SOT is
unreadable, so it must reproduce what the SOT yields. Measured before this
campaign, the fallbacks resurrected three retired workers (kilo/pic/hermes,
enabled:false rows kept for re-enable), a hardcoded `canonical_labels` table
OVERRODE config labels, and the hermes default-model row disagreed with the
SOT. Each test below pins one of those invariants against the real config
file, cross-source (the expectation is re-derived from the JSON here, never
copied from the code under test).
"""
import asyncio
import copy
import json
import os
import re
import time
from pathlib import Path

import COUNCIL_TRACKER
import code_tools
import orchestrator_server


def _cfg_path() -> Path:
    path = (Path(orchestrator_server.__file__).resolve().parent
            / ".council" / "council_config.json")
    assert path.exists(), f"council config SOT not found at {path}"
    return path


def _real_cfg() -> dict:
    return json.loads(_cfg_path().read_text(encoding="utf-8-sig"))


def _rows_in_order(cfg: dict):
    """All agent rows (enabled or not) in roster_order then dict order."""
    agents = cfg["agents"]
    order = list(cfg.get("roster_order") or []) + list(agents.keys())
    seen, out = set(), []
    for aid in order:
        meta = agents.get(aid)
        if meta is not None and aid not in seen:
            out.append((aid, meta))
            seen.add(aid)
    return out


def _enabled(cfg: dict):
    return [(aid, m) for aid, m in _rows_in_order(cfg) if m.get("enabled", True)]


def test_fallback_roster_mirrors_enabled_config_rows(monkeypatch):
    """WHY: with the SOT unreadable, _council_active_from_config must yield the
    SAME agents/order/labels the SOT yields. A fallback that resurrected a
    retired row (or reordered roster_order) would send a leader at a worker
    that refuses dispatch."""
    monkeypatch.setattr(orchestrator_server.ct, "_load_council_config",
                        lambda force=False: None)
    agents, labels = orchestrator_server._council_active_from_config()
    enabled = _enabled(_real_cfg())
    assert list(agents) == [aid for aid, _ in enabled]
    for aid, meta in enabled:
        assert labels[aid] == meta.get("label")


def test_config_labels_win_over_hardcoded_table(monkeypatch):
    """WHY: the old code applied a hardcoded canonical_labels dict AFTER the
    config labels, so the table overrode the SOT. Now every config row's own
    `label` must win, including for retired rows (they still resolve a label)."""
    agents, labels = orchestrator_server._council_active_from_config()
    cfg = _real_cfg()
    for aid, meta in _rows_in_order(cfg):
        if meta.get("label"):
            assert labels[aid] == meta["label"], aid
    # The override case, built not remembered: a config row whose label differs
    # from any hardcoded one keeps the config label.
    cfg2 = copy.deepcopy(cfg)
    cfg2["agents"]["gro"]["label"] = "GroX"
    monkeypatch.setattr(orchestrator_server.ct, "_load_council_config",
                        lambda force=False: cfg2)
    _agents2, labels2 = orchestrator_server._council_active_from_config()
    assert labels2["gro"] == "GroX"


def test_caps_fallback_enabled_flags_match_config():
    """WHY: _council_agent_caps defaults an UNKNOWN agent to enabled True, so a
    deleted fallback row would be WORSE than a wrong one. Rows must stay, with
    `enabled` mirroring the config row, and the key sets must be equal."""
    cfg = _real_cfg()
    fb = orchestrator_server._COUNCIL_AGENT_CAPS_FALLBACK
    assert set(fb) == {aid for aid, _ in _rows_in_order(cfg)}
    for aid, meta in _rows_in_order(cfg):
        assert fb[aid]["enabled"] == meta.get("enabled", True), aid


def test_default_models_fallback_matches_config():
    """WHY: the fallback is only used when the SOT is unreadable, so it must
    reproduce the SOT rows exactly (measured defect: hermes fallback said
    opencode-zen/deepseek-v4-flash-free while the SOT row is
    ltnai/deepseek-v4-pro)."""
    cfg = _real_cfg()
    fb = orchestrator_server._COUNCIL_DEFAULT_MODELS_FALLBACK
    for aid, meta in _rows_in_order(cfg):
        assert fb[aid] == {"provider": meta["provider"], "model": meta["model"]}, aid


def test_code_tools_fallback_tables_mirror_enabled_config():
    """WHY: convene_council serves its fallback tables when the SOT is
    unreadable; they must mirror the ENABLED config rows (bridge ids, labels,
    messaging enum), never the retired ones."""
    cfg = _real_cfg()
    enabled = _enabled(cfg)
    bridges = {row["bridge_agent"] for row in code_tools._COUNCIL_FALLBACK_ROSTER}
    assert bridges == {meta.get("bridge", aid) for aid, meta in enabled}
    cap_keys = set(code_tools._COUNCIL_FALLBACK_CAPABILITIES) - {"selection_rule"}
    assert cap_keys == {row["agent"] for row in code_tools._COUNCIL_FALLBACK_ROSTER}
    assert (code_tools._COUNCIL_FALLBACK_MESSAGING_TO_ENUM
            == cfg["messaging_to_enum"])


def test_served_convene_payload_names_no_retired_worker():
    """WHY: cli_identity_rule and broker_only_rule are served to EVERY leader on
    every convene_council call (config-loaded or not). A retired label there
    reads as a routable worker, but the agent cannot be dispatched at all."""
    cfg = _real_cfg()
    retired_labels = [meta["label"] for aid, meta in _rows_in_order(cfg)
                      if not meta.get("enabled", True)]
    assert retired_labels, "test assumes the SOT keeps retired enabled:false rows"
    payload = json.loads(asyncio.run(code_tools.convene_council()))
    for key in ("cli_identity_rule", "broker_only_rule"):
        for label in retired_labels:
            assert not re.search(rf"\b{re.escape(label)}\b", payload[key]), (key, label)


def test_tracker_picks_newest_non_empty_live_raw_per_enabled_agent(monkeypatch, tmp_path):
    """WHY: every unsuffixed .council/live_raw_<agent>.log measured 0 bytes
    (created 2026-08-14); real logs are live_raw_<agent>_<model>.log. The
    tracker helper must pick the NEWEST NON-EMPTY candidate per enabled agent,
    with an exact `live_raw_<agent>_` prefix (live_raw_codo-smoke.log must NOT
    match codo), and must skip retired agents entirely."""
    council = tmp_path
    cfg = {
        "roster_order": ["a1", "d1", "a2"],
        "agents": {
            "a1": {"label": "A1C", "enabled": True},
            "a2": {"label": "A2C", "enabled": True},
            "d1": {"label": "D1C", "enabled": False},
        },
    }
    (council / "council_config.json").write_text(json.dumps(cfg), encoding="utf-8")

    def _mk(name, text, age_sec=0):
        p = council / name
        p.write_text(text, encoding="utf-8")
        when = time.time() - age_sec
        os.utime(p, (when, when))
        return p

    _mk("live_raw_a1.log", "", age_sec=10)                      # empty: skipped
    m1 = _mk("live_raw_a1_m1.log", "older line\n", age_sec=200)  # older non-empty
    m2 = _mk("live_raw_a1_m2.log", "newer line\n", age_sec=100)  # newest non-empty match
    _mk("live_raw_a1-smoke.log", "smoke line\n", age_sec=1)      # hyphen: must NOT match a1
    plain = _mk("live_raw_a2.log", "a2 line\n", age_sec=50)      # unsuffixed non-empty works
    _mk("live_raw_d1.log", "retired line\n", age_sec=10)         # disabled agent: skipped

    monkeypatch.setattr(COUNCIL_TRACKER, "COUNCIL_DIR", str(council))
    picks = COUNCIL_TRACKER.newest_live_raw_picks()

    assert set(picks) == {"a1", "a2"}
    assert Path(picks["a1"]) == m2
    assert Path(picks["a1"]).name != m1.name
    assert Path(picks["a2"]) == plain


def test_tracker_fallback_mirrors_enabled_config_rows(monkeypatch, tmp_path):
    """WHY: COUNCIL_TRACKER is standalone and keeps its own fallback roster for an
    unreadable config; like every fallback table it must reproduce what the SOT
    yields. A malformed config (top-level list, non-dict agent row) raises
    AttributeError on .get(), which must fall back instead of crashing the tracker."""
    expected = tuple(aid for aid, _ in _enabled(_real_cfg()))
    monkeypatch.setattr(COUNCIL_TRACKER, "COUNCIL_DIR", str(tmp_path))
    cfg_file = tmp_path / "council_config.json"
    for bad in ("[]", '{"agents": {"x": "not-a-dict"}}', "{not json"):
        cfg_file.write_text(bad, encoding="utf-8")
        assert COUNCIL_TRACKER.enabled_agents_from_config() == expected, bad
    cfg_file.unlink()
    assert COUNCIL_TRACKER.enabled_agents_from_config() == expected
