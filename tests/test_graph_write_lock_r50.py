"""R50 (2026-08-12): graph WRITES (cbm_index / cbm_incremental_index) are serialized per
project by a lazy asyncio.Lock, so concurrent rebuilds no longer contend on the single
graph.db writer and exceed the SQLite busy-timeout (OperationalError 'database is locked',
surfaced only at ~100 concurrent workers). Guards the per-project lock identity + reuse.
"""
import asyncio
import unittest

from orchestrator.graph import tools as gt


class TestGraphWriteLockR50(unittest.TestCase):
    def test_same_project_same_lock_diff_project_diff_lock(self):
        async def go():
            a1 = gt._graph_write_lock("D:/proj/a")
            a2 = gt._graph_write_lock("D:/proj/a")
            b1 = gt._graph_write_lock("D:/proj/b")
            return a1 is a2, a1 is b1, isinstance(a1, asyncio.Lock)
        same, cross, is_lock = asyncio.run(go())
        self.assertTrue(same)          # same project -> same lock (serialized)
        self.assertFalse(cross)        # different project -> different lock (parallel)
        self.assertTrue(is_lock)

    def test_lock_actually_serializes(self):
        # Two coroutines contending on the same project's lock must not overlap.
        order = []

        async def go():
            lk = gt._graph_write_lock("D:/proj/serial")

            async def worker(n):
                async with lk:
                    order.append(("enter", n))
                    await asyncio.sleep(0.02)
                    order.append(("exit", n))

            await asyncio.gather(worker(1), worker(2))

        asyncio.run(go())
        # enter/exit must be strictly nested (no interleave) => serialized
        self.assertEqual(len(order), 4)
        self.assertEqual(order[0][0], "enter")
        self.assertEqual(order[1][0], "exit")
        self.assertEqual(order[0][1], order[1][1])  # same worker entered+exited before the other


if __name__ == "__main__":
    unittest.main()
