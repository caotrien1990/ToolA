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

    def _finalize(self, raw, returncode=None):
        base = orchestrator_server.CouncilModelResolution(
            requested_provider="claudeltnai", requested_model="claude-opus-5",
            resolved_provider="claudeltnai", resolved_model="claude-opus-5",
            resolution_status="unverified",
        )
        return orchestrator_server._council_finalize_model_resolution(base, raw, returncode=returncode)

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



# A harness that emits NO stream-json at all: GroC/grok returns plain Vietnamese
# prose on stdout, so no envelope key is ever seen and _council_harness_started()
# is False for a perfectly successful run. Line 6 quotes the launcher phrase
# because the dispatched task asked the worker to quote that very line.
GROC_PLAIN_TEXT_REPLY = NL.join([
    "Da doc xong file orchestrator_server.py va dispatch_ledger.jsonl.",
    "Ket luan: detector _council_launcher_argv_too_long van con false positive.",
    "Ly do: harness cua GroC khong xuat stream-json, chi xuat text thuan.",
    "Do do _council_harness_started() luon tra ve False.",
    "Scope thu hai cung pass vi dong text nam ngoai JSON.",
    "Dong log goc: The " + PHRASE + ".",
])


class TestCouncilArgvFalsePositiveR3(unittest.TestCase):
    """TOOLA-ARGVFALSE-R3: returncode is the harness-AGNOSTIC scope. A launcher
    refusal means the child never spawned, so returncode 0 proves argv was accepted
    even for harnesses that emit no JSON envelope at all (run-979141e5, agent gro)."""

    def _finalize(self, raw, returncode=None):
        base = orchestrator_server.CouncilModelResolution(
            requested_provider="ltnai", requested_model="grok-4.6-build",
            resolved_provider="ltnai", resolved_model="grok-4.6-build",
            resolution_status="unverified",
        )
        return orchestrator_server._council_finalize_model_resolution(
            base, raw, returncode=returncode)

    # ---- (a) the R3 regression: rc=0 + zero JSON + phrase quoted --------------
    def test_rc0_plain_text_harness_quoting_phrase_is_not_a_launcher_failure(self):
        raw = GROC_PLAIN_TEXT_REPLY
        # Preconditions that made BOTH R2 scopes pass on a successful run.
        self.assertIn(PHRASE, raw.lower())
        self.assertFalse(orchestrator_server._council_harness_started(raw))
        # returncode 0 must veto the detector regardless of the other scopes.
        self.assertFalse(
            orchestrator_server._council_launcher_argv_too_long(raw, 0))
        _final, error_name = self._finalize(raw, returncode=0)
        self.assertNotEqual(error_name, "COUNCIL_HARNESS_ARGV_TOO_LONG")

    # ---- (b) real failure, real non-zero exit --------------------------------
    def test_rc_nonzero_bare_refusal_still_reported(self):
        raw = LAUNCHER_STDERR
        self.assertTrue(
            orchestrator_server._council_launcher_argv_too_long(raw, 1))
        final, error_name = self._finalize(raw, returncode=1)
        self.assertEqual(error_name, "COUNCIL_HARNESS_ARGV_TOO_LONG")
        self.assertEqual(final.resolution_status, "unavailable")
        self.assertIn({"warning_name": "HARNESS_ARGV_TOO_LONG"}, list(final.warnings))

    # ---- (c) timeout path: no returncode exists, behaviour unchanged ---------
    def test_rc_none_bare_refusal_still_reported(self):
        raw = LAUNCHER_STDERR
        self.assertTrue(
            orchestrator_server._council_launcher_argv_too_long(raw, None))
        _final, error_name = self._finalize(raw, returncode=None)
        self.assertEqual(error_name, "COUNCIL_HARNESS_ARGV_TOO_LONG")

    # ---- (d) third scope: rc!=0 but the blob carries real prose --------------
    def test_rc_nonzero_long_plain_text_quoting_phrase_is_not_a_launcher_failure(self):
        raw = GROC_PLAIN_TEXT_REPLY
        non_empty = len([ln for ln in raw.splitlines() if ln.strip()])
        self.assertGreater(non_empty, orchestrator_server._COUNCIL_ARGV_REFUSAL_MAX_LINES)
        self.assertFalse(orchestrator_server._council_harness_started(raw))
        self.assertFalse(
            orchestrator_server._council_launcher_argv_too_long(raw, 1))
        _final, error_name = self._finalize(raw, returncode=1)
        self.assertNotEqual(error_name, "COUNCIL_HARNESS_ARGV_TOO_LONG")

    def test_third_scope_boundary_allows_wrapped_single_refusal(self):
        # <= 3 non-empty lines is still treated as a bare launcher refusal.
        raw = NL.join(["cmd.exe:", LAUNCHER_STDERR, "aborting."])
        self.assertTrue(
            orchestrator_server._council_launcher_argv_too_long(raw, 1))


if __name__ == "__main__":
    unittest.main()
