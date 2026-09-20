"""A39 close-out: UNSUPPORTED_FIELD offered a retry step that could never succeed.

WHY this test exists (measured 2026-09-20 against the live daemon, pid 41712):

  POST /api/call/manage_agent {"project_path": ..., "action": "sync_rule", ...}
    -> 400 UNSUPPORTED_FIELD, unsupported_fields=["project_path"]
       corrected_payload  = {"action": "sync_rule", "target": "all", "dry_run": true}   <- right
       retry_sequence[-1] = {"tool": "manage_agent", "payload": {... "project_path": ...},
                             "note": "retry once with corrected payload"}               <- WRONG

The step called itself a correction while carrying the uncorrected request. A CA that
follows retry_sequence -- which the rule tells it to trust -- resent the identical body,
and the duplicate-failure guard answered DUPLICATE_RETRY_BLOCKED on the retry. So the
remediation path published by the error was guaranteed to burn two calls and fix nothing.

Two fields claiming to be the retry body is the actual defect, not the stale copy: the
schema gate already computes the one true body once (alias mapping first, then strip) and
publishes it as corrected_payload. _retry_sequence now forwards THAT. The sibling branch
UNSUPPORTED_DRY_RUN had always stripped its offending field before echoing, which is what
makes the old UNSUPPORTED_FIELD branch a defect rather than a design choice.

Fallback is deliberate: call sites with no corrected_payload to hand (the raise path, the
generic dispatcher exception path) keep replaying `body`, so their behaviour is unchanged.
"""
import json
import unittest

import orchestrator_server as srv


REJECTED = "project_path"


class UnsupportedFieldRetryPayload(unittest.TestCase):

    def test_retry_step_carries_the_corrected_payload_not_the_rejected_body(self):
        body = {REJECTED: "D:/x", "action": "sync_rule", "target": "all", "dry_run": True}
        corrected = {"action": "sync_rule", "target": "all", "dry_run": True}
        steps = srv._retry_sequence("manage_agent", "UNSUPPORTED_FIELD", body,
                                    corrected_payload=corrected)
        retry = [s for s in steps if s.get("tool")]
        self.assertTrue(retry, "UNSUPPORTED_FIELD offered no tool step to retry")
        payload = retry[-1].get("payload") or {}
        self.assertNotIn(
            REJECTED, payload,
            "the retry step still replays the field the daemon just rejected -- "
            "following it earns DUPLICATE_RETRY_BLOCKED")
        self.assertEqual(payload, corrected)

    def test_without_a_corrected_payload_the_old_replay_is_preserved(self):
        # The raise path and the dispatcher exception path have no corrected_payload;
        # they must keep working exactly as before rather than lose their retry step.
        body = {REJECTED: "D:/x", "action": "list"}
        steps = srv._retry_sequence("manage_agent", "UNSUPPORTED_FIELD", body)
        retry = [s for s in steps if s.get("tool")]
        self.assertTrue(retry)
        self.assertEqual(retry[-1].get("payload"), body)

    def test_error_response_never_publishes_two_disagreeing_retry_bodies(self):
        # Asserted through the real response, not the helper: the in-process helper is
        # exactly the layer that looked correct while the HTTP answer was wrong.
        body = {REJECTED: "D:/x", "action": "sync_rule", "target": "all"}
        corrected = {"action": "sync_rule", "target": "all"}
        resp = srv._error_response(
            "manage_agent", "Unsupported field(s) for 'manage_agent': project_path",
            400, {}, body,
            error_name="UNSUPPORTED_FIELD",
            unsupported_fields=[REJECTED],
            corrected_payload=corrected,
            corrected_payload_source="stripped_unsupported_fields",
        )
        payload = json.loads(resp.body)
        self.assertEqual(payload.get("error_name"), "UNSUPPORTED_FIELD")
        self.assertEqual(payload.get("corrected_payload"), corrected)
        steps = [s for s in (payload.get("retry_sequence") or []) if s.get("tool")]
        self.assertTrue(steps, "the HTTP answer carried no retry step")
        self.assertEqual(
            steps[-1].get("payload"), payload.get("corrected_payload"),
            "corrected_payload and retry_sequence disagree about the retry body -- "
            "two sources of truth, and the CA cannot tell which one to trust")

    def test_the_note_does_not_promise_a_correction_it_is_not_carrying(self):
        body = {REJECTED: "D:/x", "action": "list"}
        steps = srv._retry_sequence("manage_agent", "UNSUPPORTED_FIELD", body,
                                    corrected_payload={"action": "list"})
        note = " ".join(str(s.get("note") or "") for s in steps)
        self.assertIn("DUPLICATE_RETRY_BLOCKED", note,
                      "the note never warns that replaying the original body is blocked")


if __name__ == "__main__":
    unittest.main()
