"""Tests for orchestrator/core/call_cache.py -- the A41 result cache + cost observatory.

The module shipped in 5ad06c13 with a 29-probe ad-hoc script and NO committed test, so
nothing in the suite pinned it. These tests pin the two things a later pass can silently
break: WHICH cost buckets are allowed to exist, and the abandoned-answer advisory that is
the whole point of tier 1b (a CA that walked away on timeout must be told once that the
answer is already in cache, and exactly once).

Every bound read off the module (`_COST_MIN_SAMPLES`, `CALL_CACHE_MIN_MS`) instead of
restated as a literal: all of them are env-overridable, so a literal here would fail in a
shell that exports the override and pass in one that does not.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.core import call_cache as cc  # noqa: E402


class _CostPathIsolated(unittest.TestCase):
    """Aim the observatory at a throwaway file; the live CALL_COST.json must not move."""

    ENV = "ORCHESTRATOR_CALL_COST_PATH"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cost_path = Path(self._tmp.name) / "CALL_COST.json"
        self._old_env = os.environ.get(self.ENV)
        os.environ[self.ENV] = str(self.cost_path)
        cc.reset_for_tests()

    def tearDown(self):
        cc.reset_for_tests()
        if self._old_env is None:
            os.environ.pop(self.ENV, None)
        else:
            os.environ[self.ENV] = self._old_env
        self._tmp.cleanup()

    def _force_reload(self):
        """reset_for_tests() marks the store LOADED-and-empty; undo that one flag."""
        with cc._COST_LOCK:
            cc._COST.clear()
            cc._COST_LOADED = False


class TestCostBucketJunkGuard(_CostPathIsolated):
    """MEASURED 2026-09-21: 3 of the 4 live buckets could never match a future call.

    `observe()` recorded whatever it was handed and nothing bounded the NUMBER of buckets
    (_COST_MAX_SAMPLES bounds samples WITHIN one), so CALL_COST.json could only grow and
    each campaign ended with a hand purge. The guard refuses at the producer and drops the
    same shapes on load, so old garbage self-heals.
    """

    def test_junk_guard_control_batch(self):
        tmp_proj = os.path.normcase(os.path.join(tempfile.gettempdir(), "tmpabc", "proj_a"))
        real = os.path.normcase(str(Path(__file__).resolve().parents[1]))
        cases = [
            ("__fast_accounting_probe__|", True, "internal probe: no CA can call it"),
            ("backup_project|" + tmp_proj, True, "project_path inside the OS temp dir"),
            ("__init__|" + real, True, "dunder tool name on a real path"),
            # NEGATIVE controls. A guard that answered True here would silently switch
            # off a working floor, which is worse than the leak it was written for.
            ("search_code|", False, "a real call may legitimately carry no project_path"),
            ("search_code|" + real, False, "a real project"),
            ("write_fix|c:\\temporary_but_not_tmp\\proj", False, "not the OS temp dir"),
        ]
        for key, want, why in cases:
            with self.subTest(key=key, why=why):
                self.assertIs(cc._cost_key_is_junk(key), want, why)

    def test_load_prunes_junk_written_before_the_guard_existed(self):
        live_shape = {
            "__fast_accounting_probe__|": {"samples": [0.1] * 25},
            "backup_project|" + os.path.normcase(
                os.path.join(tempfile.gettempdir(), "tmp3v57d339", "proj_a")): {"samples": [1.2]},
            "backup_project|" + os.path.normcase(
                os.path.join(tempfile.gettempdir(), "tmpernf82vo", "proj_a")): {"samples": [1.3]},
            "search_code|": {"samples": [0.4, 0.5, 0.6]},
        }
        self.cost_path.write_text(json.dumps(live_shape), encoding="utf-8")
        self._force_reload()
        with cc._COST_LOCK:
            cc._cost_load_locked()
            kept = sorted(cc._COST)
        self.assertEqual(kept, ["search_code|"])

    def test_observe_refuses_junk_and_still_records_a_real_bucket(self):
        cc.observe("__fast_accounting_probe__", "", 1.5)
        cc.observe("backup_project", os.path.join(tempfile.gettempdir(), "tmpzzz", "proj_a"), 1.5)
        cc.observe("search_code", "", 1.5)
        cc.flush_cost_observatory()
        on_disk = json.loads(self.cost_path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(on_disk), ["search_code|"])


class TestMeasuredFloor(_CostPathIsolated):

    def test_no_floor_until_min_samples(self):
        need = cc._COST_MIN_SAMPLES
        for i in range(need - 1):
            cc.observe("cbm_dsm", "p", 1.0 + i)
            self.assertIsNone(
                cc.measured_cost("cbm_dsm", "p"),
                "a floor computed from fewer than _COST_MIN_SAMPLES samples would gate "
                "real calls on one slow outlier")
        cc.observe("cbm_dsm", "p", 1.0 + need)
        floor = cc.measured_cost("cbm_dsm", "p")
        self.assertIsNotNone(floor)
        self.assertEqual(floor["samples"], need)
        self.assertLessEqual(floor["min_s"], floor["p95_s"])
        self.assertLessEqual(floor["p95_s"], floor["max_s"])

    def test_a_floor_survives_a_restart(self):
        for i in range(cc._COST_MIN_SAMPLES):
            cc.observe("cbm_dsm", "p", 2.0 + i)
        cc.flush_cost_observatory()
        self._force_reload()  # a fresh process reading the same file
        self.assertIsNotNone(
            cc.measured_cost("cbm_dsm", "p"),
            "a gate that appears and vanishes across restarts is worse than no gate")


class TestAbandonedAnswerAdvisory(unittest.TestCase):
    """Tier 1b: the CA gave up on timeout, the server finished anyway.

    The next call gets ONE short note that the answer is already cached. Advised once, not
    forever -- an advisory that repeats costs tokens on every call after a single orphan.
    """

    def setUp(self):
        cc.reset_for_tests()

    def tearDown(self):
        cc.reset_for_tests()

    def test_transport_only_keys_do_not_split_one_question(self):
        body = {"project_path": "p", "mode": "text"}
        noisy = dict(body, fields="ok,result", agent_id="claultnai", operation_id="abc")
        self.assertEqual(cc.call_signature("search_code", body),
                         cc.call_signature("search_code", noisy))

    def test_cheap_or_failed_answers_are_not_stored(self):
        body = {"project_path": "p"}
        self.assertEqual(cc.store("search_code", body, {"ok": True}, 1), "")
        self.assertEqual(
            cc.store("search_code", body, {"ok": False}, cc.CALL_CACHE_MIN_MS + 1), "",
            "a failure may have a cause that is already gone; never serve it twice")
        self.assertEqual(
            cc.store("write_fix", body, {"ok": True}, cc.CALL_CACHE_MIN_MS + 1), "",
            "only read-only tools are cacheable")

    def test_abandoned_answer_is_advertised_once_then_consumed(self):
        body = {"project_path": "p", "mode": "text"}
        key = cc.store("search_code", body, {"ok": True, "hits": 3},
                       cc.CALL_CACHE_MIN_MS + 1, project_path="p", agent_id="claultnai")
        self.assertTrue(key)
        self.assertIsNone(cc.pending_advisory(agent_id="claultnai", project_path="p"),
                          "a delivered answer owes the next call nothing")
        self.assertTrue(cc.mark_undelivered(key))
        first = cc.pending_advisory(agent_id="claultnai", project_path="p")
        self.assertIsInstance(first, dict)
        self.assertIn("matched_by", first)
        self.assertIsNone(cc.pending_advisory(agent_id="claultnai", project_path="p"),
                          "consumed once: an advisory that repeats taxes every later call")

    def test_serving_the_cache_discharges_the_advisory(self):
        body = {"project_path": "p", "mode": "text"}
        key = cc.store("search_code", body, {"ok": True, "hits": 3},
                       cc.CALL_CACHE_MIN_MS + 1, project_path="p", agent_id="claultnai")
        cc.mark_undelivered(key)
        hit = cc.lookup("search_code", body)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["result"]["hits"], 3)
        self.assertIsNone(
            cc.pending_advisory(agent_id="claultnai", project_path="p"),
            "the CA is holding the answer now; telling it to retry the cache is noise")


if __name__ == "__main__":
    unittest.main()
