"""WHY: rule files must not enumerate roster ids -- the id list lives only in
.council/council_config.json (agents_registry.json is the identity SOT; the
council config is the roster SOT). The old agent_list check ALSO reported every
config agent no rule file names as `info` drift, which measured 2026-09-29 was
5 permanent rows of noise (codo, gro, hermes, kilo, pic) for the DESIGNED
state. This test pins rule_drift_check(checks="agent_list") as ONE-WAY: only a
rule naming an agent the config lacks is drift.
"""
import asyncio
import json
import re
from pathlib import Path

import code_tools


def test_unreferenced_config_agent_is_not_drift():
    """WHY: the precondition is cross-source (real config + the very
    RULE_DRIFT_SOURCES the check reads), so if someone later teaches a rule file
    to enumerate the roster the test says so instead of silently passing -- and
    doing that would itself break the no-enumeration rule."""
    here = Path(code_tools.__file__).resolve().parent
    cfg = json.loads((here / ".council" / "council_config.json")
                     .read_text(encoding="utf-8-sig"))
    joined = ""
    for src in code_tools.RULE_DRIFT_SOURCES:
        p = here / src
        try:
            joined += p.read_text(encoding="utf-8") if p.exists() else ""
        except OSError:
            joined += ""
    unref = [a for a in cfg["agents"]
             if not re.search(rf"\b{re.escape(a)}\b", joined)]
    assert unref, (
        "no unreferenced config agent: this test cannot discriminate the "
        "one-way behaviour any more (and a rule file naming every roster id "
        "would itself break the no-enumeration rule)")

    out = json.loads(asyncio.run(code_tools.rule_drift_check(checks="agent_list")))
    drifts = out.get("drifts") or []
    agent_drifts = [d for d in drifts if d.get("check") == "agent_list"]
    assert not [d for d in agent_drifts
                if "not referenced in rule files" in str(d.get("detail", ""))], \
        f"one-way agent_list still reports unreferenced config agents: {agent_drifts}"
    noisy = [d for d in agent_drifts
             if d.get("severity") == "info"
             and any(a in str(d.get("detail", "")) for a in unref)]
    assert not noisy, f"info drift names an unreferenced roster id: {noisy}"
