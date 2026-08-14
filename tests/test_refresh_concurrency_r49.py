"""R49 (2026-08-12): concurrent full cache rebuilds (generate/refresh_project_map) of
the SAME project must serialize via a per-project reentrant lock, not race on the shared
.orchestrator_cache tree and raise FILE_ACCESS_DENIED. Surfaced under stress (many agents
calling refresh_project_map at once -> unlink/rmdir collisions).
"""
import concurrent.futures as cf
import shutil
import tempfile
import unittest
from pathlib import Path

import surgical_mapper as sm


class TestRefreshConcurrencyR49(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r49_"))
        for i in range(6):
            (self.tmp / f"m{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_concurrent_refresh_no_collision(self):
        errs = []

        def _refresh():
            try:
                sm.refresh_project_map(str(self.tmp))
            except Exception as e:
                errs.append(f"{type(e).__name__}: {e}")

        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(_refresh) for _ in range(8)]
            for f in futs:
                f.result()
        self.assertEqual(errs, [], f"concurrent refresh raced: {errs}")

    def test_lock_per_project_and_reentrant(self):
        lk1 = sm._pmap_lock(self.tmp)
        lk2 = sm._pmap_lock(self.tmp)
        self.assertIs(lk1, lk2)          # same project -> same lock object
        self.assertTrue(lk1.acquire())
        self.assertTrue(lk1.acquire())   # reentrant (refresh -> generate on one thread)
        lk1.release()
        lk1.release()


if __name__ == "__main__":
    unittest.main()
