import json
import unittest

import orchestrator_server

# Built at runtime so this test file itself never carries the literal launcher
# phrase -- otherwise reading THIS file would re-arm the very false positive
# under test (the original bug self-triggered in exactly that way).
PHRASE = "command line " + "is too " + "long"
LAUNCHER_STDERR = "The " + PHRASE + "."
NL = chr(10)

INIT_ENVELOPE = json.dumps({
    "type": "system",
    "subtype": "init",
    "cwd": "C:/repo/project-orchestrator",
    "session_id": "4d445be3-0f41-4ba1-8563-9f6cd51e25b2",
    "tools": ["Bash", "Read"],
    "mcp_servers": [{"name": "project-orchestrator", "status": "connected"}],
    "model": "claude-opus-5",
})


def _tool_result_quoting_phrase():
    """A stream-json record whose CONTENT merely quotes the launcher phrase."""
    return json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{
            "type": "tool_result",
            "content": "orchestrator_server.py: every real broker prompt died with "
                       "`The " + PHRASE + ".`",
        }]},
    })


class TestCouncilArgvFalsePositiveR1(unittest.TestCase):
    """TOOLA-ARGVFALSE-R1: a SUCCESSFUL worker run must never be relabelled a
    launcher failure just because the phrase appears somewhere in its stdout."""

    def _finalize(self, raw):
        base = orchestrator_server.CouncilModelResolution(
            requested_provider="claudeltnai", requested_model="claude-opus-5",
            resolved_provider="claudeltnai", resolved_model="claude-opus-5",
            resolution_status="unverified",
        )
        return orchestrator_server._council_finalize_model_resolution(base, raw)

    # ---- the false positive -------------------------------------------------
    def test_init_envelope_plus_quoted_phrase_is_not_a_launcher_failure(self):
        raw = NL.join([INIT_ENVELOPE, _tool_result_quoting_phrase()])
        self.assertIn(PHRASE, raw.lower())
        self.assertFalse(orchestrator_server._council_launcher_argv_too_long(raw))
        _final, error_name = self._finalize(raw)
        self.assertNotEqual(error_name, "COUNCIL_HARNESS_ARGV_TOO_LONG")

    def test_worker_child_process_stderr_does_not_fail_a_started_harness(self):
        # Source #2: the worker itself ran a cmd.exe with a long argv. The harness
        # still started, so this is worker noise -- not OUR launcher failing.
        raw = NL.join([INIT_ENVELOPE, LAUNCHER_STDERR, '{"type":"result","session_id":"x"}'])
        self.assertFalse(orchestrator_server._council_launcher_argv_too_long(raw))
        _final, error_name = self._finalize(raw)
        self.assertNotEqual(error_name, "COUNCIL_HARNESS_ARGV_TOO_LONG")

    def test_non_claude_harness_envelope_also_counts_as_started(self):
        # codex / opencode / kilo never emit a claude "system/init" record. Keying
        # only on init would leave those harnesses falsely failing.
        codex_envelope = json.dumps({"type": "item.completed", "thread_id": "t-1"})
        raw = NL.join([codex_envelope, LAUNCHER_STDERR])
        self.assertTrue(orchestrator_server._council_harness_started(raw))
        self.assertFalse(orchestrator_server._council_launcher_argv_too_long(raw))
        _final, error_name = self._finalize(raw)
        self.assertNotEqual(error_name, "COUNCIL_HARNESS_ARGV_TOO_LONG")

        opencode_envelope = json.dumps({"sessionID": "ses_1", "part": {"type": "text"}})
        self.assertTrue(orchestrator_server._council_harness_started(opencode_envelope))

    def test_harness_started_detects_init_envelope(self):
        self.assertTrue(orchestrator_server._council_harness_started(INIT_ENVELOPE))
        self.assertFalse(orchestrator_server._council_harness_started(LAUNCHER_STDERR))
        self.assertFalse(orchestrator_server._council_harness_started(""))
        self.assertFalse(orchestrator_server._council_harness_started("{not json"))

    # ---- the real failure must still be caught ------------------------------
    def test_real_launcher_failure_still_reported(self):
        raw = LAUNCHER_STDERR
        self.assertTrue(orchestrator_server._council_launcher_argv_too_long(raw))
        final, error_name = self._finalize(raw)
        self.assertEqual(error_name, "COUNCIL_HARNESS_ARGV_TOO_LONG")
        self.assertEqual(final.resolution_status, "unavailable")
        self.assertIn({"warning_name": "HARNESS_ARGV_TOO_LONG"}, list(final.warnings))

    def test_real_launcher_failure_with_surrounding_noise_still_reported(self):
        raw = NL.join(["", LAUNCHER_STDERR, ""])
        self.assertTrue(orchestrator_server._council_launcher_argv_too_long(raw))
        _final, error_name = self._finalize(raw)
        self.assertEqual(error_name, "COUNCIL_HARNESS_ARGV_TOO_LONG")

    def test_clean_run_is_untouched(self):
        self.assertFalse(orchestrator_server._council_launcher_argv_too_long(""))
        self.assertFalse(orchestrator_server._council_launcher_argv_too_long(INIT_ENVELOPE))


if __name__ == "__main__":
    unittest.main()
