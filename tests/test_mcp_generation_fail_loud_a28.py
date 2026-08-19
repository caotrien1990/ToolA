"""A28-P3: the MCP staleness gate must not answer its own question.

``_mcp_current_server_generation`` asks the HTTP daemon for its generation stamp
so ``_mcp_generation_gate`` can tell the caller "the daemon restarted, reload the
MCP client". Measured 2026-08-16: MCP and HTTP are two separate [A] processes,
each with its own ``SERVER_GENERATION = uuid4().hex[:12]``. When the ``/health``
probe raised, the helper fell back to ``srv.SERVER_GENERATION`` -- the MCP
process's OWN stamp -- so the gate compared MCP against MCP and was structurally
incapable of ever firing. Live evidence:

    GET /health            -> 5005a3596702   (daemon, pythonw.exe PID 13956)
    any tool through MCP   -> 82bd592df238   (MCP host, python.exe --mcp PID 36132)

That is worse than no gate: a broken detector that reports "all clear" is what
CA reads as proof it is talking to fresh code (this is A01 in a shape nobody
looks at).

P3 does not fix the two-process architecture (that is P1/P2, still open). It
fixes the lie: when the daemon cannot be reached, say so in the response and do
NOT feed a locally-invented stamp into the gate.

Deliberately NOT done: raising an error on probe failure. MCP must stay usable
when the daemon is down; the requirement is disclosure, not self-DoS.
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


class _FakeServer:
    """Stand-in for the orchestrator_server module inside the MCP process."""

    PORT = 8083
    SERVER_GENERATION = "mcplocal1234"


def _reachable(generation: str):
    """Patch urlopen so /health answers with the daemon's stamp."""

    class _Resp:
        def read(self):
            return json.dumps({"ok": True, "generation": generation}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return mock.patch.object(mcp_stdio.urllib.request, "urlopen", return_value=_Resp())


def _unreachable(exc: Exception):
    return mock.patch.object(mcp_stdio.urllib.request, "urlopen", side_effect=exc)


class ProbeDisclosureTests(unittest.TestCase):
    def setUp(self):
        mcp_stdio._MCP_SERVER_GENERATION_SEEN = None

    tearDown = setUp

    def test_probe_reports_daemon_stamp_and_says_it_came_from_the_daemon(self):
        with _reachable("daemongen001"):
            probe = mcp_stdio._mcp_probe_generation(_FakeServer())

        self.assertEqual(probe["generation"], "daemongen001")
        self.assertEqual(probe["source"], "daemon")
        self.assertFalse(probe.get("probe_error"))

    def test_probe_failure_does_not_pass_off_the_local_stamp_as_the_daemons(self):
        with _unreachable(urllib.error.URLError("connection refused")):
            probe = mcp_stdio._mcp_probe_generation(_FakeServer())

        self.assertNotEqual(
            probe["generation"],
            _FakeServer.SERVER_GENERATION,
            "the MCP process's own generation was reported as if it were the daemon's",
        )
        self.assertIsNone(probe["generation"])
        self.assertEqual(probe["source"], "unreachable")

    def test_probe_failure_names_the_error_instead_of_swallowing_it(self):
        with _unreachable(urllib.error.URLError("connection refused")):
            probe = mcp_stdio._mcp_probe_generation(_FakeServer())

        self.assertIn("URLError", probe["probe_error"])
        self.assertIn("connection refused", probe["probe_error"])

    def test_local_generation_is_still_reported_but_under_its_own_name(self):
        # Losing the local stamp entirely would be a regression -- it is real
        # information, just not an answer to "which daemon am I talking to".
        with _unreachable(OSError("boom")):
            probe = mcp_stdio._mcp_probe_generation(_FakeServer())

        self.assertEqual(probe["local_generation"], _FakeServer.SERVER_GENERATION)


class GateTests(unittest.TestCase):
    def setUp(self):
        mcp_stdio._MCP_SERVER_GENERATION_SEEN = None

    tearDown = setUp

    def test_a_real_generation_change_is_detected_and_re_baselined(self):
        # A28 pinned that the gate can SEE a genuine daemon restart -- the point
        # of P3 was that the probe must not answer its own question. TA-5
        # (2026-08-19) changed only what the gate DOES with that knowledge:
        # latching MCP_STALE_RELOAD_REQUIRED for the rest of the client's life
        # cost a whole session of MCP every time [A] restarted. The detection
        # invariant A28 cares about is unchanged and still asserted here --
        # the new baseline is the new daemon, which is only reachable if the
        # change was actually observed. The fail-closed answer is now opt-in and
        # is pinned in test_strict_mode_still_reports_the_restart below.
        server = _FakeServer()
        with _reachable("daemongen001"):
            first = mcp_stdio._mcp_probe_generation(server)
        self.assertIsNone(mcp_stdio._mcp_generation_gate(first["generation"]))
        self.assertEqual(mcp_stdio._MCP_SERVER_GENERATION_SEEN, "daemongen001")

        with _reachable("daemongen002"):
            second = mcp_stdio._mcp_probe_generation(server)
        self.assertIsNone(mcp_stdio._mcp_generation_gate(second["generation"]))
        self.assertEqual(
            mcp_stdio._MCP_SERVER_GENERATION_SEEN,
            "daemongen002",
            "the restart was not observed -- the baseline never moved",
        )

    def test_strict_mode_still_reports_the_restart(self):
        # CLM_MCP_STRICT_GENERATION=1 restores the pre-TA-5 fail-closed answer.
        # This is the assertion A28 originally made; it is kept verbatim behind
        # the flag so the detector itself stays under test.
        server = _FakeServer()
        with _reachable("daemongen001"):
            first = mcp_stdio._mcp_probe_generation(server)
        self.assertIsNone(mcp_stdio._mcp_generation_gate(first["generation"]))

        with _reachable("daemongen002"):
            second = mcp_stdio._mcp_probe_generation(server)
        with mock.patch.dict(os.environ, {"CLM_MCP_STRICT_GENERATION": "1"}):
            stale = mcp_stdio._mcp_generation_gate(second["generation"])

        self.assertIsNotNone(stale)
        self.assertEqual(stale["error_name"], "MCP_STALE_RELOAD_REQUIRED")
        self.assertEqual(stale["expected_generation"], "daemongen001")
        self.assertEqual(stale["generation"], "daemongen002")
        self.assertEqual(
            mcp_stdio._MCP_SERVER_GENERATION_SEEN,
            "daemongen001",
            "strict mode must not re-baseline; it refuses instead",
        )

    def test_an_unreachable_probe_never_becomes_the_remembered_baseline(self):
        # The bug's real teeth: if a fallback stamp is remembered first, the next
        # successful probe looks like a restart and the gate cries wolf; if it is
        # remembered second, a real restart is masked. Neither may happen.
        server = _FakeServer()
        with _unreachable(OSError("down")):
            probe = mcp_stdio._mcp_probe_generation(server)
        mcp_stdio._mcp_generation_gate(probe["generation"])

        self.assertIsNone(
            mcp_stdio._MCP_SERVER_GENERATION_SEEN,
            "a fallback/unknown stamp was recorded as the baseline",
        )

        with _reachable("daemongen001"):
            probe = mcp_stdio._mcp_probe_generation(server)
        self.assertIsNone(
            mcp_stdio._mcp_generation_gate(probe["generation"]),
            "first real daemon stamp was mistaken for a restart",
        )

    def test_a_probe_failure_after_a_good_baseline_does_not_cry_wolf(self):
        # Found by sabotage: dropping the ``is None`` guard left the earlier
        # tests green, because None-as-baseline is indistinguishable from
        # no-baseline. This is the branch that actually needs it -- once a real
        # stamp is remembered, an unreachable probe compares None against it and
        # reports MCP_STALE_RELOAD_REQUIRED for a daemon that never restarted.
        server = _FakeServer()
        with _reachable("daemongen001"):
            mcp_stdio._mcp_generation_gate(
                mcp_stdio._mcp_probe_generation(server)["generation"]
            )

        with _unreachable(OSError("down")):
            probe = mcp_stdio._mcp_probe_generation(server)
        stale = mcp_stdio._mcp_generation_gate(probe["generation"])

        self.assertIsNone(
            stale, f"unreachable daemon reported as a restart: {stale}"
        )
        self.assertEqual(
            mcp_stdio._MCP_SERVER_GENERATION_SEEN,
            "daemongen001",
            "a failed probe overwrote the known-good baseline",
        )


class ResponseStampTests(unittest.TestCase):
    def test_response_discloses_the_probe_source(self):
        stamped = mcp_stdio._mcp_add_generation(
            '{"ok": true}',
            {"generation": "daemongen001", "source": "daemon",
             "probe_error": "", "local_generation": "mcplocal1234"},
        )

        payload = json.loads(stamped)
        self.assertEqual(payload["generation"], "daemongen001")
        self.assertEqual(payload["generation_source"], "daemon")

    def test_response_says_the_gate_could_not_run(self):
        stamped = mcp_stdio._mcp_add_generation(
            '{"ok": true}',
            {"generation": None, "source": "unreachable",
             "probe_error": "URLError: down", "local_generation": "mcplocal1234"},
        )

        payload = json.loads(stamped)
        self.assertEqual(payload["generation_source"], "unreachable")
        self.assertIn("URLError", payload["generation_probe_error"])
        self.assertEqual(payload["mcp_generation"], "mcplocal1234")
        self.assertIsNone(payload["generation"], "unknown must read as unknown, not as a stamp")

    def test_plain_string_generation_still_works(self):
        # Back-compat: callers that already pass a bare string must not break.
        payload = json.loads(mcp_stdio._mcp_add_generation('{"ok": true}', "gen-a"))

        self.assertEqual(payload["generation"], "gen-a")

    def test_existing_generation_field_is_not_overwritten(self):
        payload = json.loads(mcp_stdio._mcp_add_generation(
            '{"ok": true, "generation": "inner"}',
            {"generation": "outer", "source": "daemon", "probe_error": "",
             "local_generation": "mcplocal1234"},
        ))

        self.assertEqual(payload["generation"], "inner")

    def test_non_json_passthrough(self):
        self.assertEqual(mcp_stdio._mcp_add_generation("not json", "gen-a"), "not json")


class BackCompatTests(unittest.TestCase):
    def test_thin_wrapper_still_returns_a_string_or_none(self):
        with _reachable("daemongen001"):
            self.assertEqual(
                mcp_stdio._mcp_current_server_generation(_FakeServer()), "daemongen001"
            )
        with _unreachable(OSError("down")):
            self.assertIsNone(mcp_stdio._mcp_current_server_generation(_FakeServer()))


if __name__ == "__main__":
    unittest.main()
