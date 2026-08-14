import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import COUNCIL_TRACKER
import orchestrator_server
from orchestrator.core.ca_policy_matrix import agent_transport_policy
from orchestrator.council.cron_jobs import CouncilCronService
from orchestrator.council.identity import canonical_id


class TestCouncilCanonicalIdentity(unittest.TestCase):
    def test_normalizer_is_shared_by_runtime_policy_and_cron(self):
        self.assertEqual(canonical_id("  CoDoC  "), "codoc")
        self.assertEqual(orchestrator_server._council_runtime_key(" OPE ", " MiniMax-M3 "),
                         "ope::minimax-m3")
        self.assertEqual(agent_transport_policy("  CodoC  "), "http_only")
        with tempfile.TemporaryDirectory() as tmp:
            service = CouncilCronService(Path(tmp) / "cron.db", lambda: ("codo", "ope"))
            job = service.create_job({
                "name": "canonical", "workers": [" CODO ", "Ope"], "prompt": "x",
                "schedule_type": "interval", "schedule_value": "1m",
            })
            self.assertEqual(job["workers"], ["codo", "ope"])

    def test_duplicate_runtime_key_check_fails_loud(self):
        with mock.patch.object(orchestrator_server, "_COUNCIL_AGENTS", ("Ope", "ope")), \
             mock.patch.object(orchestrator_server, "_council_effective_model",
                               return_value={"model": "MiniMax-M3"}):
            with self.assertRaisesRegex(AssertionError, "duplicate council agent id"):
                orchestrator_server._assert_council_runtime_key_uniqueness()


class TestCouncilStallAlerts(unittest.TestCase):
    def setUp(self):
        self.old_here = orchestrator_server._HERE
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".council").mkdir()
        orchestrator_server._HERE = self.root

    def tearDown(self):
        orchestrator_server._HERE = self.old_here
        self.tmp.cleanup()

    def test_written_alert_surfaces_in_tracker_summary(self):
        orchestrator_server._council_stall_alert(
            "codo::gpt", {"agent": "codo", "request_id": "req-visible", "silent_for_sec": 901},
            "cancelled_after_silence",
        )
        lines = []
        COUNCIL_TRACKER.append_full_jsonl_section(
            lines, "STALL ALERTS", str(self.root / ".council" / "stall_alerts.jsonl"), 8
        )
        rendered = "\n".join(lines)
        self.assertIn("STALL ALERTS", rendered)
        self.assertIn("req-visible", rendered)

    def test_alert_file_keeps_last_200_entries(self):
        with mock.patch.object(orchestrator_server, "_COUNCIL_STALL_ALERT_MAX_ENTRIES", 200):
            for index in range(225):
                orchestrator_server._council_stall_alert(
                    "codo::gpt", {"agent": "codo", "request_id": f"req-{index}"}, "test"
                )
        path = self.root / ".council" / "stall_alerts.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 200)
        self.assertEqual(rows[0]["request_id"], "req-25")
        self.assertEqual(rows[-1]["request_id"], "req-224")


if __name__ == "__main__":
    unittest.main()