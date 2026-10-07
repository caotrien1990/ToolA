"""TOOLA-AHEALTHHUNG-R1: write_new_file must not pin the asyncio loop, and
write_fix must seed a missing INDEX instead of hard-failing after a restart.

Also a real (in-process) proof: while write_new_file runs a slow cache update,
an on-loop /health handler must still answer within 1s.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import threading
import time
import unittest
from pathlib import Path

import code_tools
import surgical_mapper as sm
from orchestrator.tools.write import core as write_core


def _run(coro):
    return json.loads(asyncio.run(coro))


class TestWriteNewFileOffloadsCache(unittest.TestCase):
    def test_write_new_file_calls_update_cache_via_to_thread(self):
        src = inspect.getsource(write_core.write_new_file)
        compact = "".join(src.split())
        self.assertIn("to_thread(ct._update_cache_after_write", compact)
        self.assertNotIn(
            "synced=ct._update_cache_after_write(str(fp),project_path)",
            compact,
        )


class TestWriteFixSeedsIndex(unittest.TestCase):
    def test_ensure_minimal_index_creates_index_without_source_walk(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="a_idxseed_") as td:
            root = Path(td)
            (root / "note.md").write_text("hello\n", encoding="utf-8")
            before = sm.ensure_minimal_index(str(root))
            self.assertTrue(before["ok"])
            self.assertTrue(before["seeded"])
            idx = root / ".orchestrator_cache" / "INDEX.json"
            self.assertTrue(idx.exists())
            again = sm.ensure_minimal_index(str(root))
            self.assertTrue(again["ok"])
            self.assertFalse(again["seeded"])

    def test_write_fix_auto_seeds_and_writes(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="a_wfixseed_") as td:
            root = Path(td)
            target = root / "mod.py"
            target.write_text("A = 1\nB = 2\nC = 3\n", encoding="utf-8")
            self.assertFalse((root / ".orchestrator_cache" / "INDEX.json").exists())
            real = _run(code_tools.write_fix(
                str(target), 2, 2, "B = 20\n", project_path=str(root), dry_run=False))
            self.assertTrue(real.get("ok"), real)
            self.assertTrue(real.get("index_auto_seeded"), real)
            self.assertTrue((root / ".orchestrator_cache" / "INDEX.json").exists())
            self.assertIn("B = 20", target.read_text(encoding="utf-8"))


class TestHealthStaysUpDuringWriteNewFile(unittest.TestCase):
    """Real test: /health on the SAME asyncio loop must answer during write_new_file.

    Auto-heal probes /health on :8083 (the event loop). A ThreadingHTTPServer
    would stay up even if the loop were blocked, so that is not the proof.
    Health here is an asyncio protocol; the poller is a side thread (like the
    real auto-heal monitor). If write_new_file still called cache update
    synchronously, time.sleep(2.5) on the loop would make every probe miss.
    """

    def test_health_responds_while_cache_update_is_slow(self):
        import tempfile
        import urllib.request

        hits = {"n": 0, "errors": [], "delays": []}

        async def _write_and_poll(root: Path):
            ready = asyncio.Event()
            stop = threading.Event()

            async def _health_handler(reader, writer):
                try:
                    await reader.read(256)
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n{\"ok\":true}")
                    await writer.drain()
                finally:
                    writer.close()

            server = await asyncio.start_server(_health_handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            ready.set()

            def _poll_from_thread():
                deadline = time.monotonic() + 6.0
                while not stop.is_set() and time.monotonic() < deadline:
                    t0 = time.monotonic()
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/health", timeout=1.0
                        ) as resp:
                            resp.read()
                        hits["n"] += 1
                        hits["delays"].append(time.monotonic() - t0)
                    except Exception as exc:
                        if stop.is_set():
                            break
                        hits["errors"].append(f"{type(exc).__name__}:{exc}")
                        hits["delays"].append(time.monotonic() - t0)
                    time.sleep(0.15)

            orig = code_tools._update_cache_after_write

            def _wrapped(file_path, project_root=None, diagnostics=None):
                time.sleep(2.5)
                return True

            code_tools._update_cache_after_write = _wrapped
            poller = threading.Thread(target=_poll_from_thread, daemon=True)
            try:
                await ready.wait()
                poller.start()
                await asyncio.sleep(0.25)
                result = json.loads(await code_tools.write_new_file(
                    str(root / "ROUTE_RULE.md"),
                    new_content="# rule\n" + ("x" * 4000) + "\n",
                    project_path=str(root),
                ))
                await asyncio.sleep(0.4)
                return result
            finally:
                code_tools._update_cache_after_write = orig
                stop.set()
                poller.join(timeout=2.0)
                server.close()
                await server.wait_closed()

        with tempfile.TemporaryDirectory(prefix="a_healthwrite_") as td:
            root = Path(td)
            result = asyncio.run(_write_and_poll(root))
            wrote = (root / "ROUTE_RULE.md").exists()

        self.assertTrue(result.get("ok"), result)
        self.assertTrue(wrote, result)
        self.assertGreaterEqual(hits["n"], 4, hits)
        self.assertEqual(hits["errors"], [], hits)
        self.assertTrue(hits["delays"], "poller recorded no samples")
        self.assertLess(max(hits["delays"]), 1.5, hits)


class TestSnapshotSkipOnMissingIndex(unittest.TestCase):
    def test_first_write_skips_full_walk_when_index_missing(self):
        calls = {"full": 0, "delta": 0}

        def _spy(project_root, **kw):
            if kw.get("changed_paths") is None:
                calls["full"] += 1
            else:
                calls["delta"] += 1
            return "snap-test"

        import tempfile
        real = __import__("orchestrator.search_snapshots", fromlist=["publish_snapshot"]).publish_snapshot
        ss = __import__("orchestrator.search_snapshots", fromlist=["publish_snapshot"])
        ss.publish_snapshot = _spy
        try:
            with tempfile.TemporaryDirectory(prefix="a_snapskip_") as td:
                root = Path(td)
                fp = root / "a.py"
                fp.write_text("x = 1\n", encoding="utf-8")
                self.assertFalse((root / ".orchestrator_cache" / "INDEX.json").exists())
                sm.update_file_cache(str(fp), str(root))
                self.assertTrue((root / ".orchestrator_cache" / "INDEX.json").exists())
        finally:
            ss.publish_snapshot = real
        self.assertEqual(calls["full"], 0, calls)
        self.assertGreaterEqual(calls["delta"], 1, calls)


if __name__ == "__main__":
    unittest.main()
