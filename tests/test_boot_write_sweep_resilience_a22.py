# pyright: reportOptionalMemberAccess=false, reportArgumentType=false
"""A22 regression: _boot_write_sweep must survive ONE unreadable project root.

Defect: a stale/junk project root in sessions.db whose
'.orchestrator_cache/INDEX.json' is unreadable (broken ACLs -> PermissionError)
propagated straight through _boot_sweep_index_files into _boot_sweep_scan and
collapsed the ENTIRE boot write sweep. Other, healthy roots were never swept and
no .bak restore happened.

Campaign theme = fail loud. The fix must NOT silently swallow the bad root: the
sweep continues over the good roots AND the failure is reported in the result
(root_errors / root_error_count).
"""
import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import code_tools
from orchestrator.tools.write import core as write_core
from orchestrator.tools.write.core import _boot_write_sweep


class TestBootWriteSweepResilienceA22(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="a22_sweep_"))
        # Healthy root with a restorable .bak (mirrors the AC-SYS06 fixture).
        self.good_root = self.tempdir / "good"
        self.good_root.mkdir()
        self.target = self.good_root / "broken.py"
        self.target.write_text("def ok():\n    return 1\n", encoding="utf-8")
        asyncio.run(code_tools.generate_project_map(str(self.good_root)))
        self.bak = self.good_root / "broken.py.bak"
        self.bak.write_text("def ok():\n    return 1\n", encoding="utf-8")
        self.target.write_text("def broken(:\n    pass\n", encoding="utf-8")
        now = code_tools.time.time()
        code_tools.os.utime(self.target, (now - 10, now - 10))
        code_tools.os.utime(self.bak, (now, now))
        # Second healthy root, so a mid-list failure cannot mask later roots.
        self.other_root = self.tempdir / "other"
        self.other_root.mkdir()
        (self.other_root / "plain.py").write_text("x = 1\n", encoding="utf-8")
        asyncio.run(code_tools.generate_project_map(str(self.other_root)))
        # The unreadable/broken-ACL root that used to kill the whole sweep.
        self.bad_root = self.tempdir / "bad"
        self.bad_root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_permission_denied_root_does_not_collapse_sweep(self):
        """One PermissionError root must not abort the sweep; good roots still swept."""
        real_index_files = write_core._boot_sweep_index_files
        bad_key = str(self.bad_root)

        def flaky_index_files(root):
            # Emulate the broken-ACL junk temp dir: os.stat on INDEX.json denied.
            if str(root) == bad_key:
                raise PermissionError(
                    "[WinError 5] Access is denied: "
                    f"'{self.bad_root / '.orchestrator_cache' / 'INDEX.json'}'"
                )
            return real_index_files(root)

        roots = [str(self.good_root), str(self.bad_root), str(self.other_root)]
        with mock.patch.object(
            write_core, "_boot_sweep_index_files", side_effect=flaky_index_files
        ):
            result = asyncio.run(_boot_write_sweep(project_roots=roots))

        # 1) The sweep survived the bad root and restored the good root's file.
        self.assertEqual(result.get("restored_count"), 1, msg=result)
        self.assertEqual(
            self.target.read_text(encoding="utf-8"), "def ok():\n    return 1\n"
        )
        self.assertGreaterEqual(result.get("candidate_count", 0), 1, msg=result)
        # 2) Fail loud: the bad root is REPORTED, not silently skipped.
        self.assertGreaterEqual(result.get("root_error_count", 0), 1, msg=result)
        errors = result.get("root_errors") or []
        self.assertTrue(
            any(bad_key in str(e.get("root", "")) for e in errors),
            msg=f"bad root not reported in root_errors: {result}",
        )
        self.assertTrue(
            any("PermissionError" in str(e.get("error", "")) for e in errors),
            msg=f"error class not reported: {result}",
        )


if __name__ == "__main__":
    unittest.main()
