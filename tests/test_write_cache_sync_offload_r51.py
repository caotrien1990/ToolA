"""R51 (2026-08-12): the write_fix / write_new_file ASYNC handlers must offload the
post-write cache sync (_update_cache_after_write / _restore_backup_and_reseed, which run
a full build_index) via asyncio.to_thread. Running it on the event loop blocked the loop
for minutes on large repos (29k files) -> /health no-response 150s -> R45 auto-heal killed
[A] mid-write (looked like a crash: Connection Refused on both /health and /livez).

Source-inspection guard: every call to those helpers must be wrapped in to_thread EXCEPT
the single one inside the sync _write() nested fn (already dispatched via to_thread).
"""
import re
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "orchestrator" / "tools" / "write" / "core.py"
_HELPERS = r"(?:ct\._update_cache_after_write|_restore_backup_and_reseed)"


class TestWriteCacheSyncOffloadR51(unittest.TestCase):
    def setUp(self):
        self.src = CORE.read_text(encoding="utf-8")

    def test_cache_sync_calls_are_offloaded(self):
        offloaded = len(re.findall(r"to_thread\(\s*" + _HELPERS, self.src))
        self.assertGreaterEqual(
            offloaded, 5,
            "async-handler post-write cache sync must be offloaded via asyncio.to_thread")

    def test_at_most_one_on_loop_call(self):
        # A "bare" call = assigned directly, not through to_thread. Only one is allowed:
        # the call inside the sync _write() nested fn, which itself runs in a thread.
        bare = [m for m in re.findall(r"=\s*" + _HELPERS + r"\s*\(", self.src)]
        self.assertLessEqual(
            len(bare), 1,
            f"found {len(bare)} on-loop cache-sync call(s); only the one inside the "
            f"sync _write() is permitted — new bare calls re-introduce the loop-block crash")


if __name__ == "__main__":
    unittest.main()
