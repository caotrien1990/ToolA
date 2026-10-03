"""A03: search_code must not serve a stale snapshot silently.

Root defect: search_code reads an immutable published snapshot and never reconciles
it against disk. A file edited OUTSIDE [A] (native edit / git checkout / IDE) never
enters the snapshot, so a real, committed symbol can be reported as count:0 =
"not found" -- a phantom. This is the exact failure that made a previous subagent
declare `cbm_cycle_point` (a real tool) to be non-existent.

Layer 1 (fail-loud): on count==0 search_code must consult the existing drift signal
(_index_drift / cache_delta) and, when the index is stale, report the result as
INCONCLUSIVE ("khong ket luan duoc"), not "not found":
    stale_index: True
    stale_files_count: N (>=1)
    ca_next_action: "refresh_or_narrow"
    inconclusive: True
If the drift probe itself FAILS, search_code must say so (stale_index_error) and
must NOT claim a clean index.

Layer 2 (root fix): an incremental snapshot publish must re-stat files changed
outside [A] so the served snapshot stops lying.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

import code_tools
import surgical_mapper
from orchestrator.tools import search as search_module


def _build_project(root: Path) -> None:
    """Build a real INDEX + published search snapshot for `root` (like [A] does)."""
    surgical_mapper.generate_project_map(str(root))


def _clear_drift_caches() -> None:
    # cache_delta coalesces per-project futures; the recent-refresh window suppresses
    # drift. Clear both so each test observes a fresh, unsuppressed drift computation.
    try:
        code_tools._RECENT_REFRESH_TS.clear()
    except Exception:
        pass
    try:
        with code_tools._CACHE_DELTA_FUTURES_LOCK:
            code_tools._CACHE_DELTA_FUTURES.clear()
    except Exception:
        pass


class TestSearchStaleIndexA03(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # basetemp lives OUTSIDE the repo so fixtures never inherit the repo ruff.toml.
        self.root = Path(tempfile.mkdtemp(prefix="a03_stale_")).resolve()
        (self.root / "keep.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
        (self.root / "target.py").write_text(
            "def old_symbol():\n    return 'ORIGINAL'\n", encoding="utf-8"
        )
        _build_project(self.root)
        _clear_drift_caches()

    def tearDown(self) -> None:
        _clear_drift_caches()
        shutil.rmtree(self.root, ignore_errors=True)

    async def _search(self, query: str, **kw) -> dict:
        raw = await search_module.search_code(
            query=query,
            path=str(self.root),
            project_path=str(self.root),
            mode=kw.pop("mode", "text"),
            max_results=kw.pop("max_results", 20),
            **kw,
        )
        return json.loads(raw)

    def _edit_outside_orchestrator(self, rel: str, content: str) -> None:
        """Simulate a native/IDE/git edit that bypasses [A]'s write pipeline."""
        (self.root / rel).write_text(content, encoding="utf-8")

    # ---- Layer 1 ----------------------------------------------------------

    async def test_phantom_not_found_is_fixed_by_bounded_reconcile(self) -> None:
        """A committed symbol added OUTSIDE [A] must be FOUND, not just flagged.

        P15 root fix: the 0-hit verdict block used to stop at the honest
        INCONCLUSIVE report (the old Layer-1 shape this test pinned). Small
        in-scope drift (<= SEARCH_RECONCILE_MAX_FILES) is now reconciled
        incrementally and the search re-runs ONCE, so the phantom is answered
        from a fresh snapshot. The ABOVE-bound shape keeps the old report --
        pinned in test_drift_above_bound_keeps_honest_inconclusive below."""
        self._edit_outside_orchestrator(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW'\n"
        )
        _clear_drift_caches()
        payload = await self._search("brand_new_symbol_xyz")

        self.assertGreaterEqual(payload["count"], 1, msg=payload)
        self.assertIs(payload.get("stale_index"), False, msg=payload)
        self.assertIsNot(payload.get("inconclusive"), True, msg=payload)
        self.assertNotIn("ca_next_action", payload, msg=payload)
        rec = payload.get("stale_reconcile")
        self.assertIsInstance(rec, dict, msg=payload)
        self.assertIs(rec.get("applied"), True, msg=payload)
        self.assertEqual(int(rec.get("drift_files") or 0), 1, msg=payload)

    async def test_clean_index_zero_hit_is_a_real_not_found(self) -> None:
        """When the index IS fresh, a genuine 0-hit must stay a plain not-found."""
        payload = await self._search("definitely_absent_token_qqq")
        self.assertEqual(payload["count"], 0)
        self.assertFalse(
            payload.get("stale_index") is True,
            "fresh index must not raise a false stale warning",
        )
        self.assertIsNot(payload.get("inconclusive"), True)
        self.assertNotIn("stale_index_error", payload)

    async def test_drift_probe_failure_is_reported_not_swallowed(self) -> None:
        """If the drift probe itself fails, do NOT claim a clean index -- say so."""
        # A03b moved the seam from _index_drift to cache_delta: _index_drift folds
        # five distinct situations (including the 30s post-refresh noise-suppression
        # window) into a bare None, and reading that None as "clean" reintroduced the
        # very phantom A03 exists to prevent. Mocking the old seam here would leave a
        # test that can no longer fail, so it follows the seam.
        self._edit_outside_orchestrator(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW'\n"
        )
        _clear_drift_caches()
        with mock.patch.object(
            code_tools, "cache_delta", side_effect=RuntimeError("drift boom")
        ):
            payload = await self._search("brand_new_symbol_xyz")

        self.assertEqual(payload["count"], 0)
        self.assertIn("stale_index_error", payload)
        self.assertNotEqual(
            payload.get("stale_index"), False,
            "a failed drift probe must not be reported as a clean (False) index",
        )
        self.assertTrue(
            payload.get("inconclusive") is True,
            "cannot conclude 'not found' when the staleness probe failed",
        )

    async def test_hits_present_keeps_fast_path_no_stale_probe(self) -> None:
        """count>0 must not pay the drift cost and must not be flagged inconclusive."""
        with mock.patch.object(
            code_tools, "cache_delta", side_effect=AssertionError("drift must not run on hits")
        ):
            payload = await self._search("old_symbol")
        self.assertGreaterEqual(payload["count"], 1)
        self.assertIsNot(payload.get("inconclusive"), True)

    # ---- Layer 2 (documented NOT-DONE; see report) ------------------------

    async def test_incremental_publish_reconciles_external_edit(self) -> None:
        """After ANY incremental publish, a file edited outside [A] must stop being a
        phantom: its new content must appear in the served snapshot."""
        # 1) external edit to target.py that [A] never saw
        self._edit_outside_orchestrator(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW'\n"
        )
        # 2) an unrelated write THROUGH [A] triggers an incremental publish
        surgical_mapper.update_file_cache(str(self.root / "keep.py"), str(self.root))
        _clear_drift_caches()
        # 3) the snapshot must now reflect the external edit
        payload = await self._search("brand_new_symbol_xyz")
        self.assertGreaterEqual(
            payload["count"], 1,
            "incremental publish must re-stat externally-changed files into snapshot",
        )

    # ---- P10: the drift probe must be scoped to the searched subtree ----------
    #
    # Root defect (regression of the 2026-08-10 stat-first fix): the A03 probe
    # hashed EVERY indexed file in the project on every 0-hit, ignoring `path`.
    # On a large root (measured on 49. CUA, ~3900 indexed files) one 0-hit cost
    # 14.6s in-process and was re-paid in full on the very next call. The probe
    # exists to certify the hit set the search could have served; drift OUTSIDE
    # `path` cannot change a 0-hit answer produced under `path`.

    async def test_scoped_probe_ignores_drift_outside_path(self) -> None:
        """An edit outside `path` must not make an in-scope 0-hit inconclusive."""
        sub = self.root / "sub"
        sub.mkdir()
        (sub / "inner.py").write_text("def inner_marker():\n    return 1\n", encoding="utf-8")
        surgical_mapper.generate_project_map(str(self.root))
        _clear_drift_caches()
        # Drift OUTSIDE the searched subtree.
        self._edit_outside_orchestrator(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW'\n"
        )
        raw = await search_module.search_code(
            query="definitely_absent_token_qqq",
            path=str(sub),
            project_path=str(self.root),
            mode="text",
            max_results=20,
        )
        payload = json.loads(raw)

        self.assertEqual(payload["count"], 0, msg=payload)
        self.assertIs(
            payload.get("stale_index"), False,
            f"drift outside `path` was charged to an in-scope 0-hit: {payload}",
        )
        self.assertEqual(
            payload.get("stale_index_scope"), "sub",
            f"a scoped measurement must say what it measured: {payload}",
        )

    async def test_scoped_probe_detects_drift_inside_path(self) -> None:
        """Drift INSIDE the searched subtree is reconciled (P15), then measured clean."""
        sub = self.root / "sub"
        sub.mkdir()
        (sub / "inner.py").write_text("def inner_marker():\n    return 1\n", encoding="utf-8")
        surgical_mapper.generate_project_map(str(self.root))
        _clear_drift_caches()
        self._edit_outside_orchestrator(
            "sub/inner.py", "def inner_marker():\n    return 2  # edited\n"
        )
        raw = await search_module.search_code(
            query="definitely_absent_token_qqq",
            path=str(sub),
            project_path=str(self.root),
            mode="text",
            max_results=20,
        )
        payload = json.loads(raw)

        self.assertEqual(payload["count"], 0, msg=payload)
        self.assertIs(payload.get("stale_index"), False, msg=payload)
        self.assertIsNot(payload.get("inconclusive"), True, msg=payload)
        rec = payload.get("stale_reconcile")
        self.assertIsInstance(rec, dict, msg=payload)
        self.assertIs(rec.get("applied"), True, msg=payload)

    async def test_scoped_probe_second_call_hashes_nothing(self) -> None:
        """Regression guard: the SECOND 0-hit probe must be O(stat), not O(read).

        HEAD re-hashes every indexed file on every probe; the fix memoizes
        (stat signature -> content hash) so an unchanged file is never re-read.
        """
        sub = self.root / "sub"
        sub.mkdir()
        (sub / "inner.py").write_text("def inner_marker():\n    return 1\n", encoding="utf-8")
        surgical_mapper.generate_project_map(str(self.root))
        _clear_drift_caches()

        hashes = 0

        def counting_hash(path):
            nonlocal hashes
            hashes += 1
            return real_hash(path)

        real_hash = surgical_mapper.get_file_hash

        async def probe():
            raw = await search_module.search_code(
                query="definitely_absent_token_qqq",
                path=str(sub),
                project_path=str(self.root),
                mode="text",
                max_results=20,
            )
            return json.loads(raw)

        with mock.patch.object(surgical_mapper, "get_file_hash", side_effect=counting_hash):
            first = await probe()
            self.assertEqual(first["count"], 0, msg=first)
            first_hashes = hashes
            self.assertGreaterEqual(first_hashes, 1, "cold probe must measure, not guess")
            _clear_drift_caches()
            hashes = 0
            second = await probe()
            self.assertEqual(second["count"], 0, msg=second)
            self.assertEqual(
                hashes, 0,
                f"second identical 0-hit probe re-hashed {hashes} file(s); "
                "an unchanged file must never be re-read",
            )
    # ---- P13: snapshot-vs-INDEX divergence must void a clean 0-hit ------------
    #
    # Root defect: the A03 probe (cache_delta) compares INDEX against DISK. When the
    # write path advanced INDEX but the served snapshot was never republished (the
    # fresh-project publish failure, or any publish that failed after the INDEX
    # write), INDEX == disk and the probe certifies "clean" -- while the snapshot
    # search actually served PREDATES the file. The verdict must therefore also
    # compare the SERVED SNAPSHOT against INDEX: a 0-hit over a snapshot that lacks
    # an indexed file is inconclusive, never stale_index False.

    async def test_zero_hit_when_served_snapshot_predates_index_is_fixed_by_reconcile(self) -> None:
        """INDEX lists a file the served snapshot lacks -> P15 reconciles and FINDS it."""
        # Advance INDEX past the snapshot WITHOUT republishing (the state the P13
        # publish failure leaves behind): cache the new file and patch INDEX only.
        new_file = self.root / "p13_late_file.py"
        new_file.write_text("def p13_late_symbol_zz():\n    return 1\n", encoding="utf-8")
        metadata = surgical_mapper.write_file_cache(str(new_file), str(self.root))
        surgical_mapper.update_index_incremental(self.root, metadata, removed=False)
        _clear_drift_caches()

        # Positive control: INDEX really does list the file now.
        idx = json.loads((self.root / ".orchestrator_cache" / "INDEX.json").read_text(encoding="utf-8"))
        self.assertIn("p13_late_file.py", idx.get("files", {}))

        payload = await self._search("p13_late_symbol_zz")
        # P15: the snapshot_behind_index branch reconciles INDEX-minus-served
        # (bounded), republishes, and re-searches ONCE -- the symbol is found.
        self.assertGreaterEqual(payload["count"], 1, msg=payload)
        self.assertIs(payload.get("stale_index"), False, msg=payload)
        self.assertIsNot(payload.get("inconclusive"), True, msg=payload)
        rec = payload.get("stale_reconcile")
        self.assertIsInstance(rec, dict, msg=payload)
        self.assertIs(rec.get("applied"), True, msg=payload)

    async def test_snapshot_behind_index_above_bound_keeps_honest_inconclusive(self) -> None:
        """Same state, but reconcile bound=0 -> the P13 honest report stands."""
        new_file = self.root / "p13_late_file.py"
        new_file.write_text("def p13_late_symbol_zz():\n    return 1\n", encoding="utf-8")
        metadata = surgical_mapper.write_file_cache(str(new_file), str(self.root))
        surgical_mapper.update_index_incremental(self.root, metadata, removed=False)
        _clear_drift_caches()
        idx = json.loads((self.root / ".orchestrator_cache" / "INDEX.json").read_text(encoding="utf-8"))
        self.assertIn("p13_late_file.py", idx.get("files", {}))

        with mock.patch.object(surgical_mapper, "SEARCH_RECONCILE_MAX_FILES", 0):
            payload = await self._search("p13_late_symbol_zz")
        self.assertEqual(payload["count"], 0, "the served snapshot predates the file")
        self.assertNotEqual(
            payload.get("stale_index"), False,
            f"snapshot lacks an INDEX-listed file; a clean verdict is the P13 lie: {payload}",
        )
        self.assertTrue(payload.get("inconclusive") is True, payload)
        self.assertEqual(payload.get("stale_index_reason"), "snapshot_behind_index", payload)
        self.assertGreaterEqual(int(payload.get("snapshot_missing_indexed_files") or 0), 1, payload)
        rec = payload.get("stale_reconcile")
        self.assertIsInstance(rec, dict, msg=payload)
        self.assertIs(rec.get("applied"), False, msg=payload)
        self.assertEqual(rec.get("reason"), "drift_above_bound", msg=payload)

    async def test_zero_hit_with_snapshot_matching_index_stays_clean(self) -> None:
        """Guard against a false positive: snapshot==INDEX + clean disk stays clean."""
        payload = await self._search("definitely_absent_token_qqq")
        self.assertEqual(payload["count"], 0)
        self.assertIs(payload.get("stale_index"), False, payload)
        self.assertIsNot(payload.get("inconclusive"), True, payload)


    # ---- P15: bounded stale-drift reconcile + re-search (root fix) ----------
    #
    # A03 Layer 1 (above) reports a 0-hit over measured in-scope drift as
    # INCONCLUSIVE -- honest, but the tool still cannot answer the question it
    # was asked, and refresh_project_map on a large root is forbidden by rule.
    # P15 closes that: when the scoped drift is small enough
    # (surgical_mapper.SEARCH_RECONCILE_MAX_FILES), the 0-hit verdict block
    # reconciles exactly those files (existing per-file machinery), republishes
    # the snapshot incrementally and re-runs the search ONCE against the fresh
    # snapshot. Above the bound the honest inconclusive answer stands.

    async def test_native_add_in_scope_is_found_after_reconcile(self) -> None:
        """A natively-CREATED file in scope must be FOUND by the same search."""
        (self.root / "p15_added.py").write_text(
            "P15_ADDED_TOKEN = 'zz_p15_added'\n", encoding="utf-8")
        _clear_drift_caches()
        payload = await self._search("zz_p15_added")

        self.assertGreaterEqual(payload["count"], 1, msg=payload)
        self.assertEqual(
            payload["results"][0]["file"], "p15_added.py", msg=payload)
        self.assertIs(payload.get("stale_index"), False, msg=payload)
        self.assertIsNot(payload.get("inconclusive"), True, msg=payload)
        self.assertNotIn("ca_next_action", payload, msg=payload)
        rec = payload.get("stale_reconcile")
        self.assertIsInstance(rec, dict, msg=payload)
        self.assertIs(rec.get("applied"), True, msg=payload)
        self.assertEqual(int(rec.get("drift_files") or 0), 1, msg=payload)
        self.assertTrue(rec.get("snapshot_id"), msg=payload)

    async def test_native_modify_in_scope_finds_new_text_after_reconcile(self) -> None:
        """A natively-MODIFIED file must serve its NEW text, not the old bytes."""
        self._edit_outside_orchestrator(
            "target.py", "def old_symbol():\n    return 'zz_p15_modified'\n")
        _clear_drift_caches()
        payload = await self._search("zz_p15_modified")

        self.assertGreaterEqual(payload["count"], 1, msg=payload)
        self.assertEqual(payload["results"][0]["file"], "target.py", msg=payload)
        self.assertIs(payload.get("stale_index"), False, msg=payload)
        rec = payload.get("stale_reconcile")
        self.assertIsInstance(rec, dict, msg=payload)
        self.assertIs(rec.get("applied"), True, msg=payload)

    async def test_native_delete_is_an_honest_clean_zero_hit(self) -> None:
        """A natively-DELETED file must read as a real not-found, not inconclusive."""
        victim = self.root / "p15_victim.py"
        victim.write_text("P15_VICTIM_TOKEN = 'zz_p15_victim'\n", encoding="utf-8")
        _clear_drift_caches()
        found = await self._search("zz_p15_victim")
        self.assertGreaterEqual(found["count"], 1,
                                msg=f"positive control failed: {found}")

        victim.unlink()
        _clear_drift_caches()
        payload = await self._search("zz_p15_victim")

        self.assertEqual(payload["count"], 0, msg=payload)
        self.assertIs(payload.get("stale_index"), False, msg=payload)
        self.assertIsNot(payload.get("inconclusive"), True, msg=payload)
        rec = payload.get("stale_reconcile")
        self.assertIsInstance(rec, dict, msg=payload)
        self.assertIs(rec.get("applied"), True, msg=payload)
        self.assertEqual(int(rec.get("drift_files") or 0), 1, msg=payload)

    async def test_drift_above_bound_keeps_honest_inconclusive(self) -> None:
        """Above the reconcile bound the A03 Layer-1 answer stands unchanged."""
        self._edit_outside_orchestrator(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW'\n")
        _clear_drift_caches()
        with mock.patch.object(surgical_mapper, "SEARCH_RECONCILE_MAX_FILES", 0, create=True):
            payload = await self._search("brand_new_symbol_xyz")

        self.assertEqual(payload["count"], 0, msg=payload)
        self.assertIs(payload.get("stale_index"), True, msg=payload)
        self.assertGreaterEqual(int(payload.get("stale_files_count") or 0), 1)
        self.assertEqual(payload.get("ca_next_action"), "refresh_or_narrow")
        self.assertIs(payload.get("inconclusive"), True, msg=payload)
        rec = payload.get("stale_reconcile")
        self.assertIsInstance(rec, dict, msg=payload)
        self.assertIs(rec.get("applied"), False, msg=payload)
        self.assertEqual(rec.get("reason"), "drift_above_bound", msg=payload)

    async def test_out_of_scope_drift_triggers_no_reconcile(self) -> None:
        """Drift OUTSIDE `path` must neither flag the 0-hit nor reconcile."""
        sub = self.root / "sub"
        sub.mkdir()
        (sub / "inner.py").write_text(
            "def inner_marker():\n    return 1\n", encoding="utf-8")
        surgical_mapper.generate_project_map(str(self.root))
        _clear_drift_caches()
        # Drift at the ROOT -- outside the searched subtree.
        self._edit_outside_orchestrator(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW'\n")
        with mock.patch.object(
            surgical_mapper, "reconcile_stale_scope_drift",
            side_effect=AssertionError("reconcile ran for out-of-scope drift"),
        ):
            raw = await search_module.search_code(
                query="definitely_absent_token_qqq",
                path=str(sub),
                project_path=str(self.root),
                mode="text",
                max_results=20,
            )
        payload = json.loads(raw)

        self.assertEqual(payload["count"], 0, msg=payload)
        self.assertIs(payload.get("stale_index"), False, msg=payload)
        self.assertEqual(payload.get("stale_index_scope"), "sub", msg=payload)
        self.assertNotIn("stale_reconcile", payload, msg=payload)

    async def test_second_identical_search_does_no_reconcile_work(self) -> None:
        """The reconcile is one-shot: a clean re-probe must not redo it."""
        (self.root / "p15_added.py").write_text(
            "P15_ADDED_TOKEN = 'zz_p15_added'\n", encoding="utf-8")
        _clear_drift_caches()
        real = surgical_mapper.reconcile_stale_scope_drift
        calls = []

        def counting(*a, **kw):
            calls.append(1)
            return real(*a, **kw)

        with mock.patch.object(
            surgical_mapper, "reconcile_stale_scope_drift",
            side_effect=counting,
        ):
            first = await self._search("zz_p15_added")
            self.assertGreaterEqual(first["count"], 1, msg=first)
            self.assertEqual(len(calls), 1, msg=first)
            second = await self._search("zz_p15_added")
            self.assertGreaterEqual(second["count"], 1, msg=second)
            self.assertEqual(
                len(calls), 1,
                f"second identical search re-ran the reconcile: {second}")

    async def test_find_symbol_finds_natively_added_definition(self) -> None:
        """find_symbol must find a natively-added def.

        NOT RED on HEAD: find_symbol's fallback walks the LIVE filesystem
        (an os.walk over the project, not the published snapshot), so it never
        served the stale snapshot. The measured live 0-hit (daemon gen
        6287f8eecec9, 49. CUA) named a token that does not exist as a
        definition anywhere on disk -- verified by grep. This test is a
        regression guard, and pins WHY no routing change was made (P15 scope
        decision)."""
        (self.root / "p15_sym.py").write_text(
            "def p15_native_symbol_zz():\n    return 1\n", encoding="utf-8")
        _clear_drift_caches()
        raw = await search_module.find_symbol(
            name="p15_native_symbol_zz", project_path=str(self.root))
        payload = json.loads(raw)

        self.assertGreaterEqual(payload["count"], 1, msg=payload)
        self.assertEqual(
            payload["results"][0]["file"].replace("\\", "/"), "p15_sym.py",
            msg=payload)

    async def test_reconcile_failure_keeps_honest_inconclusive(self) -> None:
        """A reconcile that raises must leave the A03 Layer-1 answer in place."""
        self._edit_outside_orchestrator(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW'\n")
        _clear_drift_caches()
        with mock.patch.object(
            surgical_mapper, "reconcile_stale_scope_drift",
            side_effect=RuntimeError("reconcile boom"),
        ):
            payload = await self._search("brand_new_symbol_xyz")

        self.assertEqual(payload["count"], 0, msg=payload)
        self.assertIs(payload.get("stale_index"), True, msg=payload)
        self.assertIs(payload.get("inconclusive"), True, msg=payload)
        self.assertEqual(payload.get("ca_next_action"), "refresh_or_narrow")
        rec = payload.get("stale_reconcile")
        self.assertIsInstance(rec, dict, msg=payload)
        self.assertIs(rec.get("applied"), False, msg=payload)
        self.assertIn("reconcile boom", rec.get("reason", ""), msg=payload)

    def test_reconcile_on_project_with_no_index_is_refused(self) -> None:
        """A search-triggered reconcile must never BUILD an index from scratch.

        Leader audit (2026-10-03): on a project with no INDEX at all,
        update_index_incremental falls back to a full build_index -- an O(repo)
        walk the file-count bound does not bound (the bound counts drifted
        files, not repo size). Building an index is generate_project_map's job;
        the helper must refuse and the refusal must create NOTHING on disk.
        """
        bare = Path(tempfile.mkdtemp(prefix="a03_noindex_")).resolve()
        try:
            (bare / "orphan.py").write_text("def orphan():\n    return 1\n", encoding="utf-8")
            idx_path = bare / ".orchestrator_cache" / "INDEX.json"
            self.assertFalse(idx_path.exists(), "precondition: no INDEX at all")

            report = surgical_mapper.reconcile_stale_scope_drift(
                str(bare), ["orphan.py"], [], [])

            self.assertIs(report.get("applied"), False, msg=report)
            self.assertEqual(report.get("reason"), "no_index", msg=report)
            self.assertFalse(idx_path.exists(),
                             f"the no_index refusal must not build an INDEX: {report}")
        finally:
            shutil.rmtree(bare, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
