"""A33 - open_session must not publish a foreign process's tool surface as drift.

Measured cause pinned here: orchestrator/server/mcp_stdio.py RAN open_session
inside the MCP host process (asyncio.run(srv.ct.open_session(...))) instead of
proxying over HTTP, so a host whose registry was imported before a new tool
existed compared its own in-memory registry against the daemon-refreshed
on-disk inventory and reported a phantom drift under the daemon's generation.

A33-B (2026-09-17) removed that transport, so the cause itself is gone. This gate
STAYS anyway: it sits at the producer, which any non-daemon caller can still
reach -- pytest does exactly that, which is how every test below drives it. The
transport is pinned separately by tests/test_a33b_mcp_session_shortcut_transport.py.

Every test drives the gate with SYNTHETIC provenance, both directions: a gate
that has only ever seen a green tree is not yet a gate.
"""

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
code_tools = importlib.import_module("code_tools")
_v15 = importlib.import_module("tools_v15")


def _payload(tools=("ocr_map",)):
    """Shape _matrix_audit_open_session_summary builds before the gate runs."""
    drift = [
        {"type": "inventory_outdated", "tool": t, "severity": "medium"}
        for t in tools
    ]
    return {
        "summary": {
            "code_tools_count": 257,
            "inventory_count": 258,
            "drift_count": len(drift),
            "alias_drift_count": 0,
            "alias_drift_severity": None,
            "progressive_gap_count": 0,
        },
        "drift": drift,
        "generated_at": "2026-09-17T00:00:00Z",
        "ok": True,
    }


class _GateCase(unittest.TestCase):
    def setUp(self):
        self._orig_pid_fn = code_tools._daemon_published_pid
        self._clear()

    def tearDown(self):
        code_tools._daemon_published_pid = self._orig_pid_fn
        self._clear()

    def _clear(self):
        code_tools._MATRIX_AUDIT_DAEMON_PID_CACHE.clear()
        with code_tools._MATRIX_AUDIT_OPEN_SESSION_CACHE_LOCK:
            code_tools._MATRIX_AUDIT_OPEN_SESSION_CACHE.clear()
            code_tools._MATRIX_AUDIT_OPEN_SESSION_INFLIGHT.clear()

    def _pid(self, value):
        code_tools._daemon_published_pid = lambda: value


class TheGateSeparatesDaemonTruthFromLocalRegistry(_GateCase):
    def test_daemon_runtime_keeps_its_drift(self):
        # Positive control: the same code path must NOT swallow real drift.
        self._pid(os.getpid())
        out = code_tools._matrix_audit_gate_foreign_runtime(_payload())
        self.assertEqual(len(out["drift"]), 1)
        self.assertEqual(out["summary"]["drift_count"], 1)
        self.assertEqual(out["summary"]["audit_scope"], "daemon_runtime")
        self.assertNotIn("local_registry_counts", out["summary"])

    def test_foreign_runtime_never_publishes_drift(self):
        self._pid(os.getpid() + 1)
        out = code_tools._matrix_audit_gate_foreign_runtime(_payload())
        self.assertEqual(out["drift"], [])
        self.assertEqual(out["summary"]["drift_count"], 0)
        self.assertEqual(out["summary"]["audit_scope"], "local_process_registry")
        self.assertFalse(out["summary"]["audit_is_daemon_runtime"])
        self.assertEqual(out["summary"]["audit_pid"], os.getpid())

    def test_foreign_numbers_are_kept_under_a_name_that_says_what_they_measure(self):
        self._pid(os.getpid() + 1)
        out = code_tools._matrix_audit_gate_foreign_runtime(_payload())
        local = out["summary"]["local_registry_counts"]
        self.assertEqual(local["drift_count"], 1)
        self.assertEqual(local["drift_tools"], ["ocr_map"])
        self.assertIn("audit_tool_matrix", out["summary"]["ca_next_action"])

    def test_unknown_daemon_pid_fails_closed(self):
        # No published stamp is not permission to speak for the project.
        self._pid(None)
        out = code_tools._matrix_audit_gate_foreign_runtime(_payload())
        self.assertEqual(out["drift"], [])
        self.assertEqual(out["summary"]["audit_scope"], "local_process_registry")
        self.assertIsNone(out["summary"]["daemon_pid"])

    def test_gate_is_idempotent_and_never_recaptures_zeroed_counts(self):
        self._pid(os.getpid() + 1)
        once = code_tools._matrix_audit_gate_foreign_runtime(_payload())
        twice = code_tools._matrix_audit_gate_foreign_runtime(once)
        self.assertEqual(twice["summary"]["local_registry_counts"]["drift_count"], 1)

    def test_drift_tools_capped_at_five(self):
        self._pid(os.getpid() + 1)
        tools = tuple("tool_%d" % i for i in range(8))
        out = code_tools._matrix_audit_gate_foreign_runtime(_payload(tools))
        local = out["summary"]["local_registry_counts"]
        self.assertEqual(local["drift_count"], 8)
        self.assertEqual(len(local["drift_tools"]), 5)

    def test_a_payload_without_a_summary_dict_is_still_gated(self):
        self._pid(os.getpid() + 1)
        out = code_tools._matrix_audit_gate_foreign_runtime(
            {"summary": None, "drift": [{"tool": "ocr_map"}]}
        )
        self.assertEqual(out["drift"], [])
        self.assertEqual(out["summary"]["audit_scope"], "local_process_registry")

    def test_a_non_dict_payload_passes_through(self):
        self._pid(os.getpid() + 1)
        self.assertIsNone(code_tools._matrix_audit_gate_foreign_runtime(None))


class TheProducerActuallyCallsTheGate(_GateCase):
    """Wiring, not just the helper: removing the one call site must fail here."""

    def setUp(self):
        super().setUp()
        self._orig_sync = code_tools._audit_tool_matrix_sync
        code_tools._audit_tool_matrix_sync = lambda project_path: _payload()

    def tearDown(self):
        code_tools._audit_tool_matrix_sync = self._orig_sync
        super().tearDown()

    def test_a_foreign_process_summary_arrives_empty_of_drift(self):
        self._pid(os.getpid() + 1)
        out = code_tools._matrix_audit_open_session_summary("D:/a33_synthetic_foreign")
        self.assertEqual(out["drift"], [])
        self.assertEqual(out["summary"]["audit_scope"], "local_process_registry")

    def test_the_daemon_summary_still_carries_its_drift(self):
        self._pid(os.getpid())
        out = code_tools._matrix_audit_open_session_summary("D:/a33_synthetic_daemon")
        self.assertEqual(len(out["drift"]), 1)
        self.assertEqual(out["summary"]["audit_scope"], "daemon_runtime")


class TheDaemonPidComesFromThePublishedStamp(_GateCase):
    def _write_stamp(self, tmpdir, pid):
        stamp = Path(tmpdir) / "server_generation.json"
        stamp.write_text(
            json.dumps({"generation": "a33test0000", "pid": pid, "port": 8083}),
            encoding="utf-8",
        )
        return stamp

    def test_pid_is_read_from_the_env_redirected_generation_file(self):
        prev = os.environ.get("ORCHESTRATOR_GENERATION_FILE")
        with tempfile.TemporaryDirectory() as tmpdir:
            stamp = self._write_stamp(tmpdir, 4242)
            os.environ["ORCHESTRATOR_GENERATION_FILE"] = str(stamp)
            try:
                code_tools._MATRIX_AUDIT_DAEMON_PID_CACHE.clear()
                self.assertEqual(code_tools._daemon_published_pid(), 4242)
            finally:
                if prev is None:
                    os.environ.pop("ORCHESTRATOR_GENERATION_FILE", None)
                else:
                    os.environ["ORCHESTRATOR_GENERATION_FILE"] = prev
                code_tools._MATRIX_AUDIT_DAEMON_PID_CACHE.clear()

    def test_a_missing_stamp_yields_none_not_a_guess(self):
        prev = os.environ.get("ORCHESTRATOR_GENERATION_FILE")
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["ORCHESTRATOR_GENERATION_FILE"] = str(
                Path(tmpdir) / "no_such_stamp.json"
            )
            try:
                code_tools._MATRIX_AUDIT_DAEMON_PID_CACHE.clear()
                self.assertIsNone(code_tools._daemon_published_pid())
            finally:
                if prev is None:
                    os.environ.pop("ORCHESTRATOR_GENERATION_FILE", None)
                else:
                    os.environ["ORCHESTRATOR_GENERATION_FILE"] = prev
                code_tools._MATRIX_AUDIT_DAEMON_PID_CACHE.clear()


class TerseModeMustNotLaunderTheProvenance(_GateCase):
    def _terse(self, summary):
        return _v15._apply_verbosity(
            {"matrix_audit": summary}, "terse", "session_open"
        )["matrix_audit"]

    def test_gated_foreign_audit_survives_terse(self):
        self._pid(os.getpid() + 1)
        gated = code_tools._matrix_audit_gate_foreign_runtime(_payload())["summary"]
        out = self._terse(gated)
        self.assertEqual(out["audit_scope"], "local_process_registry")
        self.assertEqual(out["local_registry_counts"]["drift_count"], 1)
        self.assertEqual(out["drift_count"], 0)

    def test_daemon_audit_adds_exactly_one_key_in_terse(self):
        self._pid(os.getpid())
        gated = code_tools._matrix_audit_gate_foreign_runtime(_payload())["summary"]
        out = self._terse(gated)
        self.assertEqual(
            set(out),
            {
                "drift_count",
                "alias_drift_count",
                "progressive_gap_count",
                "alias_drift_severity",
                "audit_scope",
            },
        )

    def test_a_legacy_matrix_audit_gets_no_none_valued_keys(self):
        # Negative control: an ungated shape must not grow empty A33 keys.
        out = self._terse({"drift_count": 2, "alias_drift_count": 0})
        self.assertEqual(
            set(out),
            {
                "drift_count",
                "alias_drift_count",
                "progressive_gap_count",
                "alias_drift_severity",
            },
        )
        self.assertEqual(out["drift_count"], 2)


if __name__ == "__main__":
    unittest.main()
