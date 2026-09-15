"""A09: GET /api/tools/names must not silently swallow unknown query params.

Recorded defect: ``?curated=0``/``?all=1``/``?bounded=0`` (and any bogus key)
produced results indistinguishable from the unfiltered baseline, while the
equivalent POST returns ``UNSUPPORTED_FIELD``. Two sides of the same server had
opposite contracts. This is the A17 defect on the GET side: [A] threw the input
away without telling the caller.

The fix follows the A17 precedent -- surface what was dropped rather than
swallowing it -- by emitting ``ignored_query_params`` (and a hint) ONLY when
unknown params are present. These tests pin BEHAVIOUR in both directions:

  * an unknown param IS reported (and does not change the result), and
  * a valid param (tier/name_contains/category/limit) still works normally and
    carries NO ignored-params signal (a signal that is always present is
    meaningless -- the A17 negative-case discipline).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp.test_utils import make_mocked_request  # noqa: E402

import orchestrator_server  # noqa: E402


async def _call(path: str) -> dict:
    resp = await orchestrator_server.handle_tools_names(
        make_mocked_request("GET", path))
    return json.loads(resp.text)


class ToolsNamesUnknownParamReported(unittest.IsolatedAsyncioTestCase):
    async def test_bogus_param_is_reported(self):
        payload = await _call("/api/tools/names?totally_bogus_xyz=1")
        self.assertIn("ignored_query_params", payload)
        self.assertEqual(payload["ignored_query_params"], ["totally_bogus_xyz"])

    async def test_curated_param_is_reported(self):
        # `curated` reads like a real switch but the endpoint never honoured it;
        # it must be reported, not silently swallowed.
        payload = await _call("/api/tools/names?curated=0")
        self.assertIn("curated", payload.get("ignored_query_params", []))

    async def test_all_and_bounded_reported(self):
        payload = await _call("/api/tools/names?all=1&bounded=0")
        self.assertEqual(
            payload.get("ignored_query_params"), ["all", "bounded"])

    async def test_reported_param_does_not_change_result(self):
        # The whole point of A09: `?curated=0` and baseline were
        # indistinguishable in the RESULT. That part stays true (we don't newly
        # implement curated=0); what changes is the caller can now SEE it was
        # ignored. Pin: same count/curated as baseline AND the signal present.
        base = await _call("/api/tools/names")
        ignored = await _call("/api/tools/names?curated=0")
        self.assertEqual(base["count"], ignored["count"])
        self.assertEqual(base["curated"], ignored["curated"])
        self.assertNotIn("ignored_query_params", base)
        self.assertIn("ignored_query_params", ignored)

    async def test_hint_lists_supported_params(self):
        payload = await _call("/api/tools/names?nope=1")
        hint = payload.get("ignored_query_params_hint", "")
        for supported in ("tier", "name_contains", "category", "limit"):
            self.assertIn(supported, hint)


class ToolsNamesValidParamStillWorks(unittest.IsolatedAsyncioTestCase):
    async def test_clean_call_has_no_signal(self):
        payload = await _call("/api/tools/names")
        self.assertNotIn("ignored_query_params", payload)
        self.assertNotIn("ignored_query_params_hint", payload)

    async def test_tier_filter_works_and_has_no_signal(self):
        payload = await _call("/api/tools/names?tier=T1")
        self.assertTrue(payload["filtered"])
        self.assertNotIn("ignored_query_params", payload)
        # tier actually narrows: fewer-than-total, and distinct from the
        # curated baseline.
        base = await _call("/api/tools/names")
        self.assertNotEqual(payload["count"], base["count"])

    async def test_phase_alias_works_and_has_no_signal(self):
        payload = await _call("/api/tools/names?phase=T1")
        self.assertTrue(payload["filtered"])
        self.assertNotIn("ignored_query_params", payload)

    async def test_name_contains_works_and_has_no_signal(self):
        payload = await _call("/api/tools/names?name_contains=council")
        self.assertTrue(payload["filtered"])
        self.assertNotIn("ignored_query_params", payload)

    async def test_valid_and_unknown_mixed_reports_only_unknown(self):
        # A valid param present alongside an unknown one: the valid one takes
        # effect, only the unknown one is reported.
        payload = await _call("/api/tools/names?tier=T1&curated=0")
        self.assertTrue(payload["filtered"])
        self.assertEqual(payload.get("ignored_query_params"), ["curated"])

    async def test_suppress_hints_is_known_and_default_still_emits(self):
        payload = await _call("/api/tools/names")
        self.assertNotIn("ignored_query_params", payload)
        self.assertGreaterEqual(len(payload.get("must_know_hints") or []), 6)
        self.assertTrue(payload.get("ca_hint"))

    async def test_suppress_hints_true_clears_repeat_hints(self):
        payload = await _call("/api/tools/names?suppress_hints=true")
        self.assertNotIn("ignored_query_params", payload)
        self.assertEqual(payload.get("must_know_hints"), [])
        self.assertEqual(payload.get("ca_hint"), "")
        self.assertTrue(payload.get("hints_suppressed"))
        self.assertIn("tools", payload)
        self.assertGreater(payload.get("count") or 0, 0)

    async def test_suppress_hints_false_keeps_default_hints(self):
        payload = await _call("/api/tools/names?suppress_hints=false")
        self.assertNotIn("ignored_query_params", payload)
        self.assertGreaterEqual(len(payload.get("must_know_hints") or []), 6)
        self.assertFalse(payload.get("hints_suppressed"))


# ---------------------------------------------------------------------------
# Added during independent verification (ClauLTN). The tests above all feed a
# single occurrence of each key and never look at what the mutation does to the
# shared payload, so two branches were unpinned:
#
#   * request.query is a MULTIDICT -- `?curated=0&curated=1` listed `curated`
#     twice in the report. Cosmetic, but the report is the whole deliverable.
#   * `payload = dict(payload)` could be deleted with the whole suite green.
#     _tool_names_payload is shared with the MCP discovery path
#     (orchestrator/server/mcp_stdio.py:311) and scripts/mcp_api_parity_check.py;
#     it happens to return a fresh dict today, so nothing breaks -- but that is
#     an implementation detail. Pin the no-mutation contract directly so the
#     copy cannot be removed silently if that ever changes.
# ---------------------------------------------------------------------------
class ToolsNamesReportHygiene(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_unknown_key_reported_once(self):
        payload = await _call("/api/tools/names?curated=0&curated=1")
        self.assertEqual(payload.get("ignored_query_params"), ["curated"])

    async def test_handler_does_not_mutate_the_shared_payload(self):
        from unittest import mock

        shared = orchestrator_server._tool_names_payload()
        before = set(shared)
        with mock.patch.object(orchestrator_server, "_tool_names_payload",
                               return_value=shared):
            reported = await _call("/api/tools/names?bogus=1")
        self.assertIn("ignored_query_params", reported)
        self.assertEqual(set(shared), before,
                         "handler wrote an HTTP-only field into a payload "
                         "object shared with the MCP discovery path")


if __name__ == "__main__":
    unittest.main()
