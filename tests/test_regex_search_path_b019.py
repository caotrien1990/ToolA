"""B019: regex_search's own error told the caller to narrow the path, and refused `path`.

Two halves of one defect, both measured on the live daemon at generation
528d657f9449 against [A]'s own tree:

  regex_search(project_path=<[A]>, pattern="def classify_action")
    -> ok:False  REGEX_INPUT_BUDGET_EXCEEDED  "regex input exceeds 16777216 bytes"
       ca_next_action: "narrow_path_or_reduce_regex_input"
       payload_example: {"project_path": "D:/path/to/project"}

  regex_search(..., path=<[A]>\orchestrator\core)   -> ok:False UNSUPPORTED_FIELD
  search_code(..., mode="regex", path=<same>)         -> ok:True  count=1

So the tool advised narrowing by path, rejected `path`, and its payload_example
echoed back the very call that had just failed. `regex_search`'s docstring calls
itself a "convenience alias of search_code(mode='regex')" -- and the alias was
strictly weaker than its target on the one axis its own error message names.

The budget ceiling is not the bug: a 16 MB cap on regex input is a real, named
limit and refusing loudly is correct. The bug is that the refusal was
unactionable through this tool.

Pinned here:
  1. regex_search accepts `path` and forwards it (the alias is not weaker).
  2. The BUDGET error's guidance is actionable -- it names a field this tool
     really accepts, and its payload_example is not the failing call echoed back.
Both are bounded by a third claim: the budget ceiling still fires.
"""
import inspect
import json
import tempfile
import unittest
from pathlib import Path

import tools_v15
from orchestrator.tools import search as search_module


def _run(coro):
    import asyncio
    return asyncio.run(coro)


class RegexSearchAcceptsPath(unittest.TestCase):
    def test_the_signature_accepts_path(self):
        """The alias must not be weaker than search_code on the axis its error names."""
        params = inspect.signature(tools_v15.regex_search).parameters
        self.assertIn("path", params,
                      "regex_search advises narrowing by path but does not accept `path`")

    def test_path_actually_narrows_the_search(self):
        """Forwarding, not just accepting: a path outside the hit must find nothing."""
        root = Path(tempfile.mkdtemp(prefix="b019_")).resolve()
        try:
            (root / "inside").mkdir()
            (root / "outside").mkdir()
            (root / "inside" / "hit.py").write_text(
                "def target_symbol_b019():\n    return 1\n", encoding="utf-8")
            (root / "outside" / "miss.py").write_text(
                "def unrelated():\n    return 2\n", encoding="utf-8")

            got = json.loads(_run(tools_v15.regex_search(
                pattern=r"def target_symbol_b019", project_path=str(root),
                path=str(root / "inside"), max_results=5)))
            self.assertTrue(got.get("ok"), got)
            self.assertEqual(got.get("count"), 1, got)

            got = json.loads(_run(tools_v15.regex_search(
                pattern=r"def target_symbol_b019", project_path=str(root),
                path=str(root / "outside"), max_results=5)))
            self.assertTrue(got.get("ok"), got)
            self.assertEqual(got.get("count"), 0,
                             "path was accepted but not forwarded -- it found a hit "
                             "outside the directory it was scoped to")
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)


class BudgetGuidanceIsActionable(unittest.TestCase):
    """The BUDGET branch must hand back something the caller can actually send."""

    def _budget_payload(self, detail="regex input exceeds 16777216 bytes"):
        return search_module._regex_budget_payload(
            query="def classify_action", path="", project_path="D:/repo",
            file_pattern="*", detail=detail)

    def test_it_names_a_field_this_tool_accepts(self):
        p = self._budget_payload()
        self.assertEqual(p.get("error_name"), "REGEX_INPUT_BUDGET_EXCEEDED")
        blob = json.dumps(p)
        self.assertTrue("file_pattern" in blob or "path" in blob,
                        "the budget error advises narrowing but names no narrowing field")

    def test_the_payload_example_is_not_the_failing_call(self):
        p = self._budget_payload()
        example = p.get("payload_example") or {}
        self.assertTrue(example, "no payload_example on the budget error")
        self.assertNotEqual(
            set(example.keys()), {"project_path"},
            "payload_example echoed back the same unscoped call that just failed")

    def test_it_carries_the_workers_own_message_not_a_restatement(self):
        """REGEX_INPUT_BUDGET_EXCEEDED fires for MAX_RECORDS *and* MAX_INPUT_BYTES.

        A builder that hardcodes one ceiling would name the wrong limit for the
        other cause -- the same dishonesty B015 was written for, one layer down.
        """
        p = self._budget_payload(detail="regex input exceeds 250000 records")
        self.assertIn("records", p.get("error", ""),
                      "the builder restated a byte ceiling for a record-count refusal")
        from orchestrator import search_regex_worker as rw
        self.assertEqual(p.get("input_budget_bytes"), rw.MAX_INPUT_BYTES)
        self.assertEqual(p.get("input_budget_records"), rw.MAX_RECORDS)

    def test_it_offers_a_retry_sequence(self):
        p = self._budget_payload()
        seq = p.get("retry_sequence") or []
        self.assertTrue(seq, "budget error offered an empty retry_sequence")
        for step in seq:
            self.assertIn("tool", step)
            self.assertIn("payload", step)


class TheBudgetBranchActuallyUsesTheBuilder(unittest.TestCase):
    """Routing, not just the builder.

    The first cut of this file only called _regex_budget_payload directly, and a
    mutant that unrouted the branch (`if False:`) passed all 8 tests. A builder
    nothing reaches is decoration -- the exact failure mode B011-B016 were
    written about, reproduced inside their own campaign's tests. This class
    drives the real search_code error path.
    """

    def test_a_budget_refusal_from_the_worker_carries_the_guidance(self):
        """Drive the real ceiling: patch the worker function search.py calls.

        `validate_next_record_budget` is the site that raises
        REGEX_INPUT_BUDGET_EXCEEDED for both MAX_RECORDS and MAX_INPUT_BYTES
        (orchestrator/tools/search.py:946,953), so failing there exercises the
        branch a 17 GB repo would reach -- not a name invented for the mock.
        """
        from unittest import mock
        from orchestrator import search_regex_worker as rw

        root = Path(tempfile.mkdtemp(prefix="b019_route_")).resolve()
        try:
            body = "def anything():\n    return 1\n"
            (root / "f.py").write_text(body, encoding="utf-8")

            def _boom(*a, **k):
                raise rw.RegexSearchBudgetExceeded(
                    "regex input exceeds 16777216 bytes",
                    "REGEX_INPUT_BUDGET_EXCEEDED")

            with mock.patch.object(rw, "validate_next_record_budget", _boom):
                got = json.loads(_run(search_module.search_code(
                    query="def", project_path=str(root), mode="regex",
                    max_results=5)))

            self.assertEqual(got.get("error_name"), "REGEX_INPUT_BUDGET_EXCEEDED", got)
            self.assertTrue(got.get("retry_sequence"),
                            "the live budget branch answered without a retry_sequence -- "
                            "the builder exists but nothing routes to it")
            self.assertIn("accepted_narrowing_fields", got)
            self.assertEqual(got.get("input_budget_bytes"), rw.MAX_INPUT_BYTES)
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)


class TheCeilingStillFires(unittest.TestCase):
    """Bounding: making the guidance useful must not raise or remove the cap."""

    def test_budget_exceeded_is_still_not_ok(self):
        p = search_module._regex_budget_payload(
            query="x", path="", project_path="D:/repo", file_pattern="*")
        self.assertIs(p.get("ok"), False)
        self.assertIs(p.get("retryable"), True)

    def test_the_worker_still_enforces_a_byte_ceiling(self):
        from orchestrator import search_regex_worker as rw
        src = Path(rw.__file__).read_text(encoding="utf-8")
        self.assertIn("REGEX_INPUT_BUDGET_EXCEEDED", src,
                      "the byte ceiling was removed instead of made actionable")


if __name__ == "__main__":
    unittest.main()
