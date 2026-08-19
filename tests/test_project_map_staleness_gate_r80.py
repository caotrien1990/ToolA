"""R80: staleness gate for generate_project_map / refresh_project_map.

The full rebuild is O(repo) and serialises on surgical_mapper._pmap_lock, so N
concurrent CAs opening sessions used to herd into the same rescan. These tests
pin the gate contract: skip when the index matches disk AND every artifact
exists, rebuild otherwise, and always rebuild under force=True.
"""

import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import code_tools


def _run(coro):
    return json.loads(asyncio.run(coro))


class TestProjectMapStalenessGate(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="r80_pmap_gate_"))
        (self.tempdir / "sample.py").write_text("def sample():\n    return 1\n", encoding="utf-8")
        first = _run(code_tools.generate_project_map(str(self.tempdir)))
        self.assertTrue(first.get("ok"))
        self.assertFalse(first.get("skipped"), "first build must not be skipped")

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_fresh_cache_is_skipped_without_rebuild(self):
        with mock.patch.object(code_tools.surgical_mapper, "generate_project_map") as builder:
            result = _run(code_tools.generate_project_map(str(self.tempdir)))
        builder.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertFalse(result["rebuilt"])
        self.assertEqual(result["reason"], "cache_fresh")
        self.assertEqual(result["total_changed"], 0)
        # A skip must stay a drop-in for a rebuild: the index summary is present.
        self.assertEqual(result.get("files_indexed"), 1)
        self.assertTrue(result.get("index_path"))

    def test_refresh_is_gated_too(self):
        with mock.patch.object(code_tools.surgical_mapper, "refresh_project_map") as builder:
            result = _run(code_tools.refresh_project_map(str(self.tempdir)))
        builder.assert_not_called()
        self.assertTrue(result["skipped"])

    def test_force_bypasses_the_gate(self):
        result = _run(code_tools.generate_project_map(str(self.tempdir), force=True))
        self.assertTrue(result["ok"])
        self.assertFalse(result["skipped"])
        self.assertTrue(result["rebuilt"])

    def test_modified_file_rebuilds(self):
        (self.tempdir / "sample.py").write_text("def sample():\n    return 2\n", encoding="utf-8")
        freshness = asyncio.run(code_tools._project_map_freshness(str(self.tempdir)))
        self.assertFalse(freshness["fresh"])
        self.assertEqual(freshness["reason"], "drift_detected")
        self.assertEqual(freshness["modified"], 1)
        result = _run(code_tools.generate_project_map(str(self.tempdir)))
        self.assertFalse(result["skipped"])
        self.assertTrue(result["rebuilt"])

    def test_added_file_rebuilds(self):
        (self.tempdir / "extra.py").write_text("value = 2\n", encoding="utf-8")
        result = _run(code_tools.generate_project_map(str(self.tempdir)))
        self.assertFalse(result["skipped"])
        self.assertEqual(result.get("files_indexed"), 2)

    def test_missing_artifact_rebuilds_even_at_zero_drift(self):
        # Zero drift but a missing artifact: skipping here would leave the
        # repair prepare_ca_session performs today permanently undone.
        (self.tempdir / "PROJECT_CONTEXT.md").unlink()
        self.assertEqual(
            code_tools._project_map_artifacts_missing(str(self.tempdir)),
            ["PROJECT_CONTEXT.md"],
        )
        result = _run(code_tools.generate_project_map(str(self.tempdir)))
        self.assertFalse(result["skipped"])
        self.assertTrue((self.tempdir / "PROJECT_CONTEXT.md").exists())

    def test_probe_failure_falls_back_to_rebuild(self):
        # The probe exists to avoid a rebuild; it must never block one.
        with mock.patch.object(code_tools, "cache_delta", side_effect=RuntimeError("probe boom")):
            freshness = asyncio.run(code_tools._project_map_freshness(str(self.tempdir)))
            self.assertFalse(freshness["fresh"])
            self.assertEqual(freshness["reason"], "delta_probe_failed")
            result = _run(code_tools.generate_project_map(str(self.tempdir)))
        self.assertTrue(result["ok"])
        self.assertFalse(result["skipped"])

    def test_dispatcher_gates_and_forwards_force(self):
        gated = _run(code_tools.project_map(str(self.tempdir), mode="generate"))
        self.assertTrue(gated["ok"])
        self.assertEqual(gated["mode"], "generate")
        self.assertTrue(gated["skipped"])
        forced = _run(code_tools.project_map(str(self.tempdir), mode="generate", force=True))
        self.assertFalse(forced["skipped"], "project_map(force=true) must reach the builder")

    def test_gate_is_cheaper_than_rebuild(self):
        calls = {"walks": 0}
        real_reindex = code_tools.surgical_mapper._reindex_from_disk

        def counting_reindex(*args, **kwargs):
            calls["walks"] += 1
            return real_reindex(*args, **kwargs)

        with mock.patch.object(code_tools.surgical_mapper, "_reindex_from_disk", counting_reindex):
            for _ in range(5):
                _run(code_tools.generate_project_map(str(self.tempdir)))
        self.assertEqual(calls["walks"], 0, "5 session-start calls must trigger 0 full rescans")


    def test_orphan_cache_sidecar_forces_rebuild(self):
        # cache_delta walks SOURCE files, so a sidecar left behind by a file
        # deleted outside the write pipeline reports zero drift. A rebuild wipes
        # the cache tree, so the gate must not skip while one is present.
        stale = self.tempdir / ".orchestrator_cache" / "deleted.py.json"
        stale.write_text("{}", encoding="utf-8")
        self.assertEqual(code_tools._orphan_cache_sidecars(str(self.tempdir)), ["deleted.py.json"])
        freshness = asyncio.run(code_tools._project_map_freshness(str(self.tempdir)))
        self.assertFalse(freshness["fresh"])
        self.assertEqual(freshness["reason"], "orphan_cache_sidecars")
        result = _run(code_tools.generate_project_map(str(self.tempdir)))
        self.assertFalse(result["skipped"])
        self.assertFalse(stale.exists())

    def test_index_json_is_not_mistaken_for_an_orphan_sidecar(self):
        # INDEX.json and the snapshot store live in the same tree; only
        # "<source>.<ext>.json" entries are per-file sidecars.
        self.assertEqual(code_tools._orphan_cache_sidecars(str(self.tempdir)), [])

if __name__ == "__main__":
    unittest.main()
