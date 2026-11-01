# -*- coding: utf-8 -*-
"""CF-16 invariant-guard tests: attempt-tracked stage/spool epilogue (Phase 1, Sprint 2).

Placement note (recorded, NOT pre-decided): these tests live in this new
dedicated file, following the Sprint 1 precedent
(tests/test_cf09_append_only.py). No existing test file is modified; the
CF-16 guards are a distinct suite. CF-16 Gate 1 names/judges the final
placement (mini-CF-16 §12).

All tests are code-level tests in the test runner. They exercise
_stage_durable_session_event with a fake audit writer and the pure
epilogue decision helpers; they never touch the live daemon, live DBs,
or live repos. The flag is monkeypatched per test via mock.patch
contexts; the module default (PHASE1_CF16_FLAG = False) is itself
asserted.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import orchestrator_server as srv  # noqa: E402
from orchestrator.server.audit_writer import (  # noqa: E402
    AuditIdConflict,
    AuditSchemaMismatch,
    AuditStorageBusy,
)

OLD_JOINT_MESSAGE = (
    "Durable session-event stage AND spool failed for tool=%s id=%s: %s"
)


class _FakeWriter:
    """Minimal stand-in for the durable audit writer."""

    def __init__(self, submit_exc=None, spool_result=False, spool_exc=None):
        self.submit_exc = submit_exc
        self.spool_result = spool_result
        self.spool_exc = spool_exc
        self.spool_calls = []

    def submit(self, staged_id, kind, payload):
        if self.submit_exc is not None:
            raise self.submit_exc
        return "staged"

    def spool_failed_row(self, staged_id, kind, payload, reason):
        self.spool_calls.append((staged_id, kind, reason))
        if self.spool_exc is not None:
            raise self.spool_exc
        return self.spool_result


class _CaptureLog:
    """Logger stand-in capturing critical() calls."""

    def __init__(self):
        self.calls = []

    def critical(self, msg, *args, **kwargs):
        self.calls.append((msg, args, kwargs))


def _call_stage(writer):
    """Call the stage helper with the writer monkeypatched in."""
    with mock.patch.object(
        srv, "_get_durable_audit_writer", return_value=writer
    ), mock.patch.object(srv, "_SKIP_SESSION_LOG", set()):
        return srv._stage_durable_session_event(
            "op-1",
            "git_status",
            {"project_path": "D:/x"},
            {"ok": True},
            1,
            "ok",
            "",
            {"agent_id": "t"},
            "sess-1",
        )


def _formatted(template, exc):
    return template % ("git_status", "op-1", exc)


class Cf16AttemptTrackingTests(unittest.TestCase):
    def test_cf16_joint_failure_only_when_both_attempted(self):
        # True stage-failed + spool-failed: submit raises a spoolable
        # error and the spool fallback also fails (returns False).
        writer = _FakeWriter(
            submit_exc=AuditStorageBusy("db busy"), spool_result=False
        )
        with self.assertRaises(AuditStorageBusy):
            _call_stage(writer)
        # The spool WAS attempted exactly once.
        self.assertEqual(len(writer.spool_calls), 1)
        exc = writer.submit_exc
        self.assertEqual(srv._cf16_attempts(exc), (True, True))
        outcome, template = srv._cf16_epilogue_outcome(exc)
        self.assertEqual(outcome, "joint_failure")
        self.assertEqual(template, OLD_JOINT_MESSAGE)
        self.assertIn("stage AND spool failed", _formatted(template, exc))

    def test_cf16_joint_failure_when_spool_raises(self):
        # Spool attempted and itself raising is still a both-attempted
        # failure, not a distinct-message case.
        writer = _FakeWriter(
            submit_exc=AuditStorageBusy("db busy"),
            spool_exc=RuntimeError("spool append failed"),
        )
        with self.assertRaises(RuntimeError):
            _call_stage(writer)
        self.assertEqual(len(writer.spool_calls), 1)

    def test_cf16_no_false_joint_claim_on_construction_failure(self):
        # (a) Writer unavailable: the stage submit is never attempted and
        # the spool is never reached.
        with mock.patch.object(
            srv, "_get_durable_audit_writer", return_value=None
        ), mock.patch.object(srv, "_SKIP_SESSION_LOG", set()):
            with self.assertRaises(RuntimeError) as ctx:
                srv._stage_durable_session_event(
                    "op-1",
                    "git_status",
                    {"project_path": "D:/x"},
                    {"ok": True},
                    1,
                    "ok",
                    "",
                    {"agent_id": "t"},
                    "sess-1",
                )
        self.assertEqual(srv._cf16_attempts(ctx.exception), (False, False))
        outcome, template = srv._cf16_epilogue_outcome(ctx.exception)
        self.assertEqual(outcome, "construction_failed_spool_unreached")
        # (b) A failure while constructing the staged id, before any
        # submit attempt: unannotated, so the epilogue must read it as
        # construction-failed, never joint.
        with mock.patch.object(
            srv, "_get_durable_audit_writer",
            return_value=_FakeWriter(),
        ), mock.patch.object(srv, "_SKIP_SESSION_LOG", set()), mock.patch.object(
            srv, "_durable_audit_id", side_effect=ValueError("boom-construction")
        ):
            with self.assertRaises(ValueError):
                srv._stage_durable_session_event(
                    "op-1",
                    "git_status",
                    {"project_path": "D:/x"},
                    {"ok": True},
                    1,
                    "ok",
                    "",
                    {"agent_id": "t"},
                    "sess-1",
                )
        # (c) Directly: a bare unannotated exception is construction.
        exc = ValueError("boom-construction")
        self.assertEqual(srv._cf16_attempts(exc), (False, False))
        outcome, template = srv._cf16_epilogue_outcome(exc)
        self.assertEqual(outcome, "construction_failed_spool_unreached")
        message = _formatted(template, exc)
        self.assertNotIn("stage AND spool failed", message)
        self.assertIn("spool never reached", message)
        self.assertIn("boom-construction", message)

    def test_cf16_distinct_message_spool_unattempted(self):
        # AuditIdConflict / schema-mismatch paths: the submit was attempted
        # and failed, but the spool is deliberately NEVER attempted for
        # these (they cannot succeed on replay), so the message must be
        # the distinct stage-failed/spool-unattempted one, with the cause
        # attached -- not the joint claim.
        for submit_exc, cause in (
            (AuditIdConflict("id reused for a different payload"), "id reused"),
            (AuditSchemaMismatch("db contract broken"), "contract broken"),
        ):
            with self.subTest(cause=cause):
                writer = _FakeWriter(submit_exc=submit_exc)
                with self.assertRaises(type(submit_exc)):
                    _call_stage(writer)
                self.assertEqual(writer.spool_calls, [])
                self.assertEqual(srv._cf16_attempts(submit_exc), (True, False))
                outcome, template = srv._cf16_epilogue_outcome(submit_exc)
                self.assertEqual(
                    outcome, "stage_failed_spool_unattempted"
                )
                message = _formatted(template, submit_exc)
                self.assertNotIn("stage AND spool failed", message)
                self.assertIn("spool unattempted", message)
                self.assertIn(cause, message)

    def test_cf16_flag_default_off(self):
        self.assertIs(srv.PHASE1_CF16_FLAG, False)

    def test_cf16_flag_off_reproduces_old_message(self):
        # OFF state: even a true both-attempted failure logs the OLD
        # message verbatim, with no structured fields -- byte-identical
        # behaviour to before the fix.
        exc = AuditStorageBusy("db busy")
        srv._cf16_annotate_attempts(exc, True, True)
        log = _CaptureLog()
        with mock.patch.object(srv, "PHASE1_CF16_FLAG", False):
            srv._cf16_log_epilogue(log, "git_status", "op-1", exc)
        self.assertEqual(len(log.calls), 1)
        msg, args, kwargs = log.calls[0]
        self.assertEqual(msg, OLD_JOINT_MESSAGE)
        self.assertEqual(args, ("git_status", "op-1", exc))
        self.assertEqual(kwargs, {})

    def test_cf16_flag_on_gives_attempt_tracked_messages(self):
        cases = (
            # (annotation, expected outcome, expected message fragment)
            ((True, True), "joint_failure", "stage AND spool failed"),
            (
                (True, False),
                "stage_failed_spool_unattempted",
                "spool unattempted",
            ),
            (
                (False, False),
                "construction_failed_spool_unreached",
                "spool never reached",
            ),
        )
        for (stage, spool), outcome, fragment in cases:
            with self.subTest(outcome=outcome):
                exc = RuntimeError("cause-%s" % outcome)
                srv._cf16_annotate_attempts(exc, stage, spool)
                log = _CaptureLog()
                with mock.patch.object(srv, "PHASE1_CF16_FLAG", True):
                    srv._cf16_log_epilogue(log, "git_status", "op-1", exc)
                self.assertEqual(len(log.calls), 1)
                msg, args, kwargs = log.calls[0]
                self.assertIn(fragment, msg % args)
                self.assertEqual(
                    kwargs.get("extra", {}).get("cf16_outcome"), outcome
                )
                self.assertEqual(
                    kwargs.get("extra", {}).get("cf16_stage_attempted"), stage
                )
                self.assertEqual(
                    kwargs.get("extra", {}).get("cf16_spool_attempted"), spool
                )


if __name__ == "__main__":
    unittest.main()
