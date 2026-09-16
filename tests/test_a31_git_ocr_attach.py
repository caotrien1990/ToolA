"""A31-1: `git_stage`/`git_commit` now RUN the zero-LLM review, not just name it.

A30-B attached a STRING (`ca_hint_review`) telling the caller a review was possible.
The follow-up question was the obvious one: if delegate mode needs no LLM, why is it
only a hint? So the review is now actually executed and attached as `ocr_review`.

The interesting part is not that it runs -- it is everything that must stay true when
it does NOT. A stage or a commit is the last step before work leaves the machine; it
must never fail, and must never be held open indefinitely, because an advisory review
errored, hung, or was never installed. Those are the properties pinned here.

The wiring lives in the ASYNC wrapper layer (`orchestrator_server.git_stage` /
`git_commit`) and NOT in `orchestrator/git_tools.py`, because `ocr_review` is a
coroutine while `git_tools.stage`/`commit` are sync functions executed on an executor
thread -- there is no loop there to await into. That placement is asserted too, so a
later refactor cannot quietly move it somewhere it cannot work.
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import orchestrator_server as osrv  # noqa: E402
from orchestrator.tools import ocr_review as ocr_mod  # noqa: E402

_REPO = str(_ROOT)


def _lane_returning(payload):
    """Stand in for `run_in_executor_lane` so no real git process is involved."""

    async def lane(*_args, **_kwargs):
        return dict(payload)

    return lane


def _fake_ocr(files=(), ok=True, raises=None, sleep=0.0, seen=None):
    async def review(**kwargs):
        if seen is not None:
            seen.append(kwargs)
        if sleep:
            await asyncio.sleep(sleep)
        if raises is not None:
            raise raises
        if not ok:
            return json.dumps({"ok": False, "error_name": "OCR_EXEC_FAILED"})
        return json.dumps({
            "ok": True,
            "mode": "workspace",
            "total_files": len(files),
            "reviewable_count": len(files),
            "excluded_count": 0,
            "total_insertions": 7,
            "total_deletions": 2,
            "reviewable_files": [{"path": f, "status": "modified"} for f in files],
        })

    return review


class GitOcrAttachTest(unittest.TestCase):
    """Behaviour of the attach step, with git and OCR both replaced by fakes."""

    def setUp(self):
        self._lane = osrv.run_in_executor_lane
        self._ocr = ocr_mod.ocr_review
        self._timeout = osrv._OCR_ATTACH_TIMEOUT_S

    def tearDown(self):
        osrv.run_in_executor_lane = self._lane
        ocr_mod.ocr_review = self._ocr
        osrv._OCR_ATTACH_TIMEOUT_S = self._timeout

    def _stage(self, **kwargs):
        return json.loads(asyncio.run(osrv.git_stage(project_path=_REPO, paths=["a.py"], **kwargs)))

    def _commit(self, **kwargs):
        return json.loads(asyncio.run(osrv.git_commit(project_path=_REPO, message="m", **kwargs)))

    def test_stage_carries_a_real_review_and_not_only_a_hint(self):
        osrv.run_in_executor_lane = _lane_returning({"ok": True, "paths": ["a.py"]})
        ocr_mod.ocr_review = _fake_ocr(files=["a.py", "b.py"])
        out = self._stage()
        self.assertEqual(out["ocr_review"]["state"], "ok")
        self.assertEqual(out["ocr_review"]["mode"], "workspace")
        self.assertEqual(out["ocr_review"]["reviewable_count"], 2)
        self.assertEqual(out["ocr_review"]["total_insertions"], 7)
        self.assertEqual(len(out["ocr_review"]["reviewable_files"]), 2)

    def test_stage_reviews_the_uncommitted_tree_so_it_passes_no_ref(self):
        # Workspace mode is the whole point at stage time: the content that matters is
        # staged but not yet committed, so a commit range would review the wrong thing.
        seen = []
        osrv.run_in_executor_lane = _lane_returning({"ok": True})
        ocr_mod.ocr_review = _fake_ocr(files=["a.py"], seen=seen)
        self._stage()
        self.assertEqual(seen[0], {"project_path": _REPO})

    def test_commit_review_is_scoped_to_the_commit_that_just_landed(self):
        seen = []
        osrv.run_in_executor_lane = _lane_returning({"ok": True, "commit": "deadbeef"})
        ocr_mod.ocr_review = _fake_ocr(files=["a.py"], seen=seen)
        out = self._commit()
        self.assertEqual(seen[0].get("commit"), "deadbeef")
        self.assertEqual(out["ocr_review"]["state"], "ok")

    def test_ocr_false_skips_the_subprocess_entirely(self):
        called = []

        async def must_not_run(**kwargs):
            called.append(kwargs)
            return "{}"

        osrv.run_in_executor_lane = _lane_returning({"ok": True})
        ocr_mod.ocr_review = must_not_run
        out = self._stage(ocr=False)
        self.assertEqual(called, [])
        self.assertNotIn("ocr_review", out)

    def test_a_review_that_raises_cannot_fail_the_commit(self):
        osrv.run_in_executor_lane = _lane_returning({"ok": True, "commit": "c0ffee"})
        ocr_mod.ocr_review = _fake_ocr(raises=RuntimeError("binary missing"))
        out = self._commit()
        self.assertTrue(out["ok"])
        self.assertEqual(out["commit"], "c0ffee")
        self.assertEqual(out["ocr_review"]["state"], "unavailable")
        self.assertIn("RuntimeError", out["ocr_review"]["reason"])

    def test_a_review_that_answers_not_ok_is_declared_not_swallowed(self):
        osrv.run_in_executor_lane = _lane_returning({"ok": True})
        ocr_mod.ocr_review = _fake_ocr(ok=False)
        out = self._stage()
        self.assertEqual(out["ocr_review"]["state"], "unavailable")
        self.assertEqual(out["ocr_review"]["reason"], "OCR_EXEC_FAILED")

    def test_a_hanging_review_is_bounded_and_says_so(self):
        osrv._OCR_ATTACH_TIMEOUT_S = 0.05
        osrv.run_in_executor_lane = _lane_returning({"ok": True})
        ocr_mod.ocr_review = _fake_ocr(files=["a.py"], sleep=5.0)
        out = self._stage()
        self.assertEqual(out["ocr_review"]["state"], "timeout")
        self.assertEqual(out["ocr_review"]["timeout_s"], 0.05)

    def test_a_long_file_list_is_capped_and_the_drop_is_declared(self):
        many = ["f%03d.py" % i for i in range(osrv._OCR_ATTACH_FILE_CAP + 10)]
        osrv.run_in_executor_lane = _lane_returning({"ok": True})
        ocr_mod.ocr_review = _fake_ocr(files=many)
        block = self._stage()["ocr_review"]
        self.assertEqual(len(block["reviewable_files"]), osrv._OCR_ATTACH_FILE_CAP)
        self.assertEqual(block["files_omitted"], 10)
        self.assertEqual(block["max_files"], osrv._OCR_ATTACH_FILE_CAP)
        # The count is of the WHOLE diff, not of the truncated view.
        self.assertEqual(block["reviewable_count"], len(many))

    def test_a_short_file_list_claims_no_truncation(self):
        # A cap that announces itself on a clean run teaches the caller to ignore the
        # announcement on the run where it matters.
        osrv.run_in_executor_lane = _lane_returning({"ok": True})
        ocr_mod.ocr_review = _fake_ocr(files=["a.py", "b.py"])
        block = self._stage()["ocr_review"]
        self.assertNotIn("files_omitted", block)
        self.assertNotIn("max_files", block)

    def test_a_failed_git_call_is_returned_untouched(self):
        called = []

        async def must_not_run(**kwargs):
            called.append(kwargs)
            return "{}"

        osrv.run_in_executor_lane = _lane_returning({"ok": False, "error_name": "GIT_STATE_STALE"})
        ocr_mod.ocr_review = must_not_run
        out = self._stage()
        self.assertEqual(called, [])
        self.assertNotIn("ocr_review", out)
        self.assertEqual(out["error_name"], "GIT_STATE_STALE")


class GitOcrSurfaceTest(unittest.TestCase):
    """The switch must be discoverable, and the wiring must stay where it can work."""

    def test_both_git_tools_declare_the_ocr_switch_with_the_same_text(self):
        props = {row[0]: row[7] for row in osrv._GIT_TOOL_SPECS if row[0] in ("git_stage", "git_commit")}
        self.assertEqual(sorted(props), ["git_commit", "git_stage"])
        for name, schema in props.items():
            self.assertIn("ocr", schema, name)
            self.assertEqual(schema["ocr"]["type"], "boolean", name)
            self.assertIs(schema["ocr"]["default"], True, name)
        self.assertEqual(props["git_stage"]["ocr"]["description"],
                         props["git_commit"]["ocr"]["description"])

    def test_git_unstage_was_not_given_a_review_it_has_no_use_for(self):
        rows = {row[0]: row[7] for row in osrv._GIT_TOOL_SPECS}
        self.assertNotIn("ocr", rows["git_unstage"])

    def test_the_attach_stays_in_the_async_layer(self):
        # `git_tools.stage`/`commit` run on an executor thread with no event loop, so an
        # await there is impossible. Pin the split rather than trusting a comment.
        git_tools_src = (_ROOT / "orchestrator" / "git_tools.py").read_text(encoding="utf-8")
        self.assertNotIn("_attach_ocr_review", git_tools_src)
        self.assertNotIn("await", git_tools_src)
        server_src = (_ROOT / "orchestrator_server.py").read_text(encoding="utf-8")
        self.assertIn("await _attach_ocr_review(out, project_path)", server_src)
        self.assertIn("await _attach_ocr_review(out, project_path, commit=", server_src)

    def test_the_a30b_hint_survives_as_the_fallback(self):
        # The hint costs nothing and still helps on the paths where the review could not
        # run, so wiring the review in must not delete it.
        git_tools_src = (_ROOT / "orchestrator" / "git_tools.py").read_text(encoding="utf-8")
        self.assertEqual(git_tools_src.count("ca_hint_review"), 2)


if __name__ == "__main__":
    unittest.main()
