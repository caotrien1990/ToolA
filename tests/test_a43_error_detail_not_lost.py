"""A43: an audit row for a failed tool call must name the failure.

Two defects, both measured on 2026-09-22 against the live stores, both in a PRODUCER
that a consumer was already waiting on:

1. `durable_audit.db`, 44 days: 863 rows reached EMPTY_ERROR_DETAIL. Of the 441 that
   were `run_project_test_script`, ALL 441 carried `stdout` and 425 had `stderr`
   EMPTY -- pytest writes its diagnosis to stdout, and the extraction chain stopped at
   stderr. The chain's last hop was `returncode`, a key `run_project_test_script` does
   not even have (its schema answers `exit_code`).

2. `sessions.db`, 9317 rows of which 635 are status='error': `events.error_name` was
   NOT NULL on **0** of them. `store.py:_log_session_event` reads `error_name` off the
   compacted result, `_error_response` always sets it on the answer, and
   `_record_error_resolution` inherits the source row's value -- only
   `_compact_accounting_result` in between dropped it, so every error in the audit
   degraded to an ungroupable free-text `reason`.

The fix for #1 also moved the decision out of `dispatch_tool_call`'s `finally` into
`_error_detail_from_result`. That is not cosmetic: inline, the only thing a test could
assert about it was its own source text.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import orchestrator_server as srv  # noqa: E402

_SERVER_SRC = Path(srv.__file__).read_text(encoding="utf-8", errors="ignore")


def _old_chain(result):
    """The pre-fix extraction, verbatim, so its blind spot stays reproducible."""
    if not isinstance(result, dict):
        return ""
    return str(result.get("error") or
               result.get("stderr") or
               result.get("warning") or
               result.get("returncode") or "")[:300]


def _failing_pytest_answer(tail="=== 3 failed, 739 passed in 278.99s ==="):
    """The measured shape of a failed run_project_test_script answer.

    Long stdout, EMPTY stderr, `exit_code` rather than `returncode` -- 425 of the 441
    empty rows looked exactly like this.
    """
    banner = "platform win32 -- Python 3.12.0, pytest-8.0.0\ncollected 742 items\n"
    return {
        "ok": False,
        "stdout": banner + ("test_something.py .....\n" * 400) + tail,
        "stderr": "",
        "exit_code": 1,
        "error_name": "TOOL_ERROR",
    }


class TestTheProducerNamesTheFailure:
    def test_error_is_canonical_and_wins(self):
        detail = srv._error_detail_from_result(
            {"ok": False, "error": "INDEX not found", "stderr": "noise",
             "stdout": "more noise"})
        assert detail == "INDEX not found"

    def test_stderr_is_used_when_there_is_no_error_key(self):
        detail = srv._error_detail_from_result(
            {"ok": False, "stderr": "ModuleNotFoundError: No module named pytest"})
        assert "No module named pytest" in detail

    def test_negative_control_the_old_chain_still_loses_a_pytest_failure(self):
        """This is the measured bug: 441 rows, stdout present, stderr empty."""
        answer = _failing_pytest_answer()
        assert _old_chain(answer) == "", "pre-fix chain must reproduce the empty row"
        detail = srv._error_detail_from_result(answer)
        assert "3 failed" in detail
        assert not detail.startswith("EMPTY_ERROR_DETAIL")

    def test_stdout_is_read_from_the_tail_not_the_head(self):
        """The banner is printed first; the line that names the failure is printed last."""
        detail = srv._error_detail_from_result(
            _failing_pytest_answer(tail="E   assert 0 == 1  LAST_LINE_MARKER"))
        assert "LAST_LINE_MARKER" in detail
        assert "collected 742 items" not in detail
        assert len(detail) <= 300

    def test_exit_code_is_reached_when_both_streams_are_empty(self):
        detail = srv._error_detail_from_result(
            {"ok": False, "stdout": "", "stderr": "", "exit_code": 1})
        assert detail == "exit_code=1"

    def test_returncode_still_works_and_outranks_exit_code(self):
        """Tools that answer `returncode` must not regress when `exit_code` was added."""
        detail = srv._error_detail_from_result(
            {"ok": False, "returncode": 9, "exit_code": 1})
        assert detail == "returncode=9"

    def test_a_zero_exit_status_is_still_reported_not_swallowed(self):
        """`or` chaining read exit_code 0 as absent; a 0 with ok=false is a real signal."""
        detail = srv._error_detail_from_result({"ok": False, "exit_code": 0})
        assert detail == "exit_code=0"

    def test_a_timeout_is_distinguishable_from_a_crash(self):
        detail = srv._error_detail_from_result(
            {"ok": False, "exit_code": 1, "timed_out": True})
        assert detail == "exit_code=1 timed_out=true"

    def test_no_result_body_says_so(self):
        assert srv._error_detail_from_result(None) == (
            "EMPTY_ERROR_DETAIL: tool failed with no result body")

    def test_a_truly_silent_failure_keeps_the_error_name_that_was_served(self):
        detail = srv._error_detail_from_result(
            {"ok": False, "error_name": "SYNTAX_ERROR_PRE_WRITE"})
        assert detail.startswith("EMPTY_ERROR_DETAIL")
        assert "SYNTAX_ERROR_PRE_WRITE" in detail

    def test_a_silent_failure_with_no_error_name_says_unknown_not_empty(self):
        detail = srv._error_detail_from_result({"ok": False})
        assert "error_name=unknown" in detail


class TestTheCompactKeepsTheOneGroupableField:
    def test_error_name_survives_the_compact(self):
        compact = srv._compact_accounting_result(
            {"ok": False, "error": "boom", "error_name": "SYNTAX_ERROR_PRE_WRITE"}, {})
        assert compact.get("error_name") == "SYNTAX_ERROR_PRE_WRITE"

    def test_the_message_itself_is_not_stored_twice(self):
        """`error` already lands in events.reason; a second copy per row buys nothing."""
        compact = srv._compact_accounting_result(
            {"ok": False, "error": "boom", "error_name": "X"}, {})
        assert "error" not in compact

    def test_error_name_is_capped(self):
        compact = srv._compact_accounting_result(
            {"ok": False, "error_name": "E" * 500}, {})
        assert len(compact["error_name"]) == 80

    def test_a_successful_answer_adds_no_error_key(self):
        compact = srv._compact_accounting_result({"ok": True, "stdout": "fine"}, {})
        assert "error_name" not in compact

    @pytest.mark.parametrize("field", [
        "error_owner", "correlation_type", "correlation_key", "evidence",
    ])
    def test_documented_non_patch_the_other_four_columns_have_no_dispatch_producer(
            self, field):
        """Measured 0-of-9317 as well, but NOT the same defect -- do not "fix" these.

        `_log_session_event` reads all four off the compacted result, and no [A] tool
        puts any of them on an answer. Their real writer is `record_error_resolution`,
        which INSERTs its own row with its own SQL (store.py:_record_error_resolution),
        so the dispatch-side read is a dead fallback, not a dropped field. Adding them
        to the whitelist would ship four more always-null columns; this test pins the
        reasoning so a later sweep does not read the zeros as the same bug.
        """
        compact = srv._compact_accounting_result(
            {"ok": False, field: "something", "error_name": "X"}, {})
        assert field not in compact


class TestOneProducerOnly:
    def test_the_decision_has_exactly_one_definition_and_one_caller(self):
        assert _SERVER_SRC.count("_error_detail_from_result(") == 2

    def test_the_inline_chain_is_gone_for_good(self):
        """Its return was the whole bug; a copy left behind would silently win again."""
        assert '_result.get("stderr") or' not in _SERVER_SRC
        assert '_result.get("returncode") or' not in _SERVER_SRC

    def test_the_consumer_that_needed_error_name_still_reads_it(self):
        """Pin the producer to the consumer across files: if store.py stops reading
        `error_name` off the event metadata, keeping it in the compact is pointless."""
        from orchestrator.session import store as session_store
        src = Path(session_store.__file__).read_text(encoding="utf-8", errors="ignore")
        assert 'error_name = metadata.get("error_name")' in src
