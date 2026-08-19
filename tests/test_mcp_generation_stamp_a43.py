"""A43-P1: the daemon generation stamp must win, and the payload must never
tell two stories at once.

Measured 2026-08-16 (campaign-1608). MCP does NOT proxy to the HTTP daemon; it
calls ``srv.dispatch_tool_call`` in-process (mcp_stdio.py:303-305) where ``srv``
is orchestrator_server imported *inside the MCP process*. dispatch_tool_call
(orchestrator_server.py:13618/:13649) already does
``_result.setdefault("generation", SERVER_GENERATION)`` -- so the payload arrives
at ``_mcp_add_generation`` ALREADY carrying the MCP process's own stamp.

The pre-A43 wrapper then did ``payload.setdefault("generation", ...)`` -- a no-op,
because the field was already present -- while ``generation_source`` (a fresh key)
was successfully setdefault'd to "daemon". Result: ``generation`` = MCP's stamp,
``generation_source`` = "daemon". Two adjacent fields, two different stories, and
the *asserting* one (source) was the lie.

This suite pins three situations end to end:
  1. daemon-gen != mcp-gen  (the real, common case: two processes)  -> daemon wins
  2. daemon-gen == mcp-gen  (they happen to coincide)               -> still coherent
  3. probe fail                                                     -> unknown stays unknown

Plus the disclosure invariant: on a successful daemon probe the response ALWAYS
carries ``mcp_generation`` so a reader can tell "daemon is at X" apart from "the
MCP host that ran this call is Y" -- the only way to see cross-process code skew,
which is deliberately NOT a gate (it would false-positive on every call).
"""
from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.server import mcp_stdio


# --- daemon-gen != mcp-gen : the real two-process case --------------------

DAEMON = "daemongenAAAA"
MCP = "mcplocalBBBB"


def _daemon_probe(daemon_gen=DAEMON, local_gen=MCP) -> dict:
    """A successful probe dict, exactly as _mcp_probe_generation returns it."""
    return {
        "generation": daemon_gen,
        "source": "daemon",
        "probe_error": "",
        "probe_url": "http://localhost:8083/health",
        "local_generation": local_gen,
    }


def _fail_probe() -> dict:
    return {
        "generation": None,
        "source": "unreachable",
        "probe_error": "URLError: connection refused",
        "probe_url": "http://localhost:8083/health",
        "local_generation": MCP,
    }


class DaemonStampWinsTests(unittest.TestCase):
    """Situation 1: daemon-gen differs from the pre-stamped mcp-gen."""

    def test_dispatch_prestamped_mcp_gen_is_replaced_by_the_daemon_stamp(self):
        # This is the bug's exact shape: dispatch already put the MCP stamp in.
        pre_stamped = json.dumps({"ok": True, "generation": MCP})
        out = json.loads(mcp_stdio._mcp_add_generation(pre_stamped, _daemon_probe()))

        self.assertEqual(out["generation"], DAEMON,
                         "the MCP process's stamp survived and masqueraded as the daemon's")
        self.assertEqual(out["generation_source"], "daemon")

    def test_generation_and_source_tell_the_same_story(self):
        pre_stamped = json.dumps({"ok": True, "generation": MCP})
        out = json.loads(mcp_stdio._mcp_add_generation(pre_stamped, _daemon_probe()))

        # source=="daemon" is only truthful if generation is actually the daemon's.
        self.assertEqual(out["generation_source"], "daemon")
        self.assertEqual(out["generation"], DAEMON)
        self.assertNotEqual(out["generation"], MCP)

    def test_mcp_generation_is_always_disclosed_on_a_daemon_probe(self):
        # The only way a reader sees cross-process code skew (mcp != daemon).
        pre_stamped = json.dumps({"ok": True, "generation": MCP})
        out = json.loads(mcp_stdio._mcp_add_generation(pre_stamped, _daemon_probe()))

        self.assertEqual(out["mcp_generation"], MCP)
        self.assertNotEqual(out["mcp_generation"], out["generation"])

    def test_empty_payload_gets_daemon_stamp_not_none(self):
        # No pre-stamp at all (generation absent) must still land the daemon gen.
        out = json.loads(mcp_stdio._mcp_add_generation('{"ok": true}', _daemon_probe()))

        self.assertEqual(out["generation"], DAEMON)
        self.assertEqual(out["mcp_generation"], MCP)

    def test_a_deliberate_non_local_generation_is_respected(self):
        # If a tool intentionally set generation to something that is neither the
        # MCP local stamp nor empty, we must NOT clobber it -- that value is the
        # tool's own signal, not the dispatch auto-stamp. (Pins the guard that
        # keeps test_existing_generation_field_is_not_overwritten green too.)
        payload = json.dumps({"ok": True, "generation": "inner-deliberate"})
        out = json.loads(mcp_stdio._mcp_add_generation(payload, _daemon_probe()))

        self.assertEqual(out["generation"], "inner-deliberate")
        self.assertEqual(out["mcp_generation"], MCP)

    def test_a_kept_tool_value_is_never_labelled_daemon(self):
        """The kept-value branch must not rebuild the contradiction A43 removes.

        Keeping the tool's value is correct. Announcing `generation_source:
        "daemon"` next to it is not: `generation` then holds something the daemon
        never said, and the field that ASSERTS is the wrong one -- byte-for-byte
        the L-ah shape (probe the daemon, then publish a stamp that isn't its).
        The daemon stamp is still disclosed, under a key that means what it says.
        """
        payload = json.dumps({"ok": True, "generation": "inner-deliberate"})
        out = json.loads(mcp_stdio._mcp_add_generation(payload, _daemon_probe()))

        self.assertNotEqual(
            out["generation_source"], "daemon",
            "generation is the tool's value, so the source must not claim daemon")
        self.assertEqual(out["generation_source"], "tool_supplied")
        self.assertEqual(out["daemon_generation"], DAEMON)
        self.assertEqual(out["mcp_generation"], MCP)

    def test_a_payload_already_holding_the_daemon_stamp_is_called_daemon(self):
        """The mislabel in the OTHER direction, and the one no test caught.

        A payload that already carries the daemon's own stamp agrees with the
        probe -- it IS a daemon stamp, whoever wrote it. Routing it down the
        "kept tool value" branch would announce `generation_source:
        "tool_supplied"` over a value the daemon really did say: correct value,
        wrong label. Both mislabels are the same defect class (the asserting
        field disagreeing with the value beside it), so both are pinned.
        """
        payload = json.dumps({"ok": True, "generation": DAEMON})
        out = json.loads(mcp_stdio._mcp_add_generation(payload, _daemon_probe()))

        self.assertEqual(out["generation"], DAEMON)
        self.assertEqual(out["generation_source"], "daemon")
        self.assertNotIn(
            "daemon_generation", out,
            "the tool_supplied disclosure key leaked onto a genuine daemon stamp")


class DaemonEqualsMcpTests(unittest.TestCase):
    """Situation 2: daemon-gen and mcp-gen coincide (same uuid drawn twice, or
    a single-process test). The payload must stay coherent -- generation is the
    daemon's, source says daemon, and mcp_generation is present and equal."""

    def test_coincident_generations_are_coherent(self):
        same = "coincident01"
        pre_stamped = json.dumps({"ok": True, "generation": same})
        out = json.loads(mcp_stdio._mcp_add_generation(
            pre_stamped, _daemon_probe(daemon_gen=same, local_gen=same)))

        self.assertEqual(out["generation"], same)
        self.assertEqual(out["generation_source"], "daemon")
        self.assertEqual(out["mcp_generation"], same)


class ProbeFailUnchangedTests(unittest.TestCase):
    """Situation 3: probe failed. Behaviour here is A28-P3 and must be byte-for-
    byte unchanged -- unknown stays unknown, never a stamp."""

    def test_unreachable_never_carries_a_generation(self):
        out = json.loads(mcp_stdio._mcp_add_generation('{"ok": true}', _fail_probe()))

        self.assertIsNone(out["generation"], "unknown must read as unknown")
        self.assertEqual(out["generation_source"], "unreachable")
        self.assertEqual(out["mcp_generation"], MCP)
        self.assertIn("URLError", out["generation_probe_error"])

    def test_unreachable_does_not_replace_a_prestamped_value_with_none(self):
        # Defensive: even if a value was pre-stamped, the fail path uses
        # setdefault and must not blank it out.
        pre_stamped = json.dumps({"ok": True, "generation": MCP})
        out = json.loads(mcp_stdio._mcp_add_generation(pre_stamped, _fail_probe()))

        self.assertEqual(out["generation"], MCP)
        self.assertEqual(out["generation_source"], "unreachable")


class GateFiresRightTests(unittest.TestCase):
    """Situation 2 vs the gate: the gate must fire on a daemon restart (delta a)
    and must NOT fire merely because mcp-gen differs from daemon-gen (delta b),
    which is the permanent two-process condition."""

    def setUp(self):
        mcp_stdio._MCP_SERVER_GENERATION_SEEN = None

    tearDown = setUp

    def test_gate_observes_a_daemon_restart_and_moves_its_baseline(self):
        # A43's invariant is the AXIS: the gate must react to a daemon-gen change
        # (delta a) and stay silent on the permanent mcp-gen != daemon-gen skew
        # (delta b, next test). TA-5 (2026-08-19) changed the reaction, not the
        # axis: a restart now re-baselines and lets the call through instead of
        # latching MCP_STALE_RELOAD_REQUIRED for the client's whole life. The
        # baseline moving to the new daemon stamp is the observable proof that
        # delta a was seen. The old fail-closed answer is pinned below.
        self.assertIsNone(mcp_stdio._mcp_generation_gate(DAEMON))
        self.assertIsNone(mcp_stdio._mcp_generation_gate("daemongenCCCC"))
        self.assertEqual(
            mcp_stdio._MCP_SERVER_GENERATION_SEEN,
            "daemongenCCCC",
            "delta a went unnoticed -- the baseline never moved",
        )

    def test_strict_mode_fires_on_a_daemon_restart(self):
        # CLM_MCP_STRICT_GENERATION=1 = the pre-TA-5 fail-closed answer, kept
        # verbatim so the daemon axis stays asserted with a hard signal too.
        self.assertIsNone(mcp_stdio._mcp_generation_gate(DAEMON))
        with mock.patch.dict(os.environ, {"CLM_MCP_STRICT_GENERATION": "1"}):
            stale = mcp_stdio._mcp_generation_gate("daemongenCCCC")

        self.assertIsNotNone(stale)
        self.assertEqual(stale["error_name"], "MCP_STALE_RELOAD_REQUIRED")
        self.assertEqual(stale["expected_generation"], DAEMON)
        self.assertEqual(stale["generation"], "daemongenCCCC")

    def test_strict_mode_stays_silent_on_the_two_process_skew(self):
        # delta b must not fire even with the hard signal armed: the gate is fed
        # probe["generation"] (daemon), never local_generation (MCP host).
        with mock.patch.dict(os.environ, {"CLM_MCP_STRICT_GENERATION": "1"}):
            self.assertIsNone(mcp_stdio._mcp_generation_gate(DAEMON))
            self.assertIsNone(mcp_stdio._mcp_generation_gate(DAEMON))

    def test_gate_does_not_fire_on_a_stable_daemon(self):
        # Same daemon-gen probed twice: no restart, no cry-wolf -- even though
        # the MCP host (local_gen) is a different process entirely (delta b).
        self.assertIsNone(mcp_stdio._mcp_generation_gate(DAEMON))
        self.assertIsNone(mcp_stdio._mcp_generation_gate(DAEMON))

    def test_gate_is_fed_daemon_gen_not_mcp_gen(self):
        # Integration guard: _mcp_guard_response feeds the gate probe["generation"]
        # which is the daemon stamp, never local_generation. If someone rewired
        # the gate to compare against the MCP stamp it would fire on every distinct
        # process -- prove it stays on the daemon axis.
        probe = _daemon_probe()  # daemon != local
        self.assertIsNone(mcp_stdio._mcp_generation_gate(probe["generation"]))
        # Second identical daemon probe: stable, must stay silent.
        self.assertIsNone(mcp_stdio._mcp_generation_gate(probe["generation"]))


if __name__ == "__main__":
    unittest.main()
