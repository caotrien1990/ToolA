"""D-06 regression test: memory_query must not surface MEMORY_UNAVAILABLE on a
slow-but-alive ToolQ /pre_search endpoint, and must distinguish timeout from
other failure modes so callers can decide whether to retry.

Why: pre-fix, memory_query used a 20s timeout on _toolq_post while ToolQ's
/pre_search routinely takes ~10s and can spike past 20s under load — the
handler then collapsed any exception into MEMORY_UNAVAILABLE, which looked
identical to ToolQ being down. memory_recall_cluster (timeout=30 on a fast
endpoint) kept working, which made memory_query look broken by comparison.

Fix under test:
- timeout aligned to 30s (matches memory_recall_cluster),
- timeout exceptions map to error_name=MEMORY_TIMEOUT; other errors keep
  MEMORY_UNAVAILABLE,
- response includes the endpoint for diagnostics.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class MemoryQueryTimeoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = importlib.import_module("orchestrator_server")

    def test_timeout_maps_to_memory_timeout(self):
        """A timeout from _toolq_post must return MEMORY_TIMEOUT (not
        MEMORY_UNAVAILABLE) so callers can tell a slow backend from a dead one."""
        with mock.patch.object(
            self.srv, "_toolq_post", side_effect=TimeoutError("timed out")
        ) as post_mock:
            result = asyncio.run(self.srv.memory_query(query="q", top_k=2))
        payload = json.loads(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_name"], "MEMORY_TIMEOUT", payload)
        self.assertEqual(payload["endpoint"], "/pre_search")
        # Must have been called with the raised 30s timeout.
        self.assertEqual(post_mock.call_args.kwargs.get("timeout"), 30)

    def test_urlopen_style_timeout_also_maps_to_memory_timeout(self):
        """urllib surfaces socket timeouts as URLError('timed out') in some
        paths; the substring fallback must still classify as MEMORY_TIMEOUT."""
        import urllib.error

        with mock.patch.object(
            self.srv,
            "_toolq_post",
            side_effect=urllib.error.URLError("timed out"),
        ):
            result = asyncio.run(self.srv.memory_query(query="q", top_k=2))
        payload = json.loads(result)
        self.assertEqual(payload["error_name"], "MEMORY_TIMEOUT", payload)

    def test_other_errors_keep_memory_unavailable(self):
        """Non-timeout network failures must keep MEMORY_UNAVAILABLE (they
        indicate a dead/unreachable backend, not a slow one)."""
        with mock.patch.object(
            self.srv, "_toolq_post", side_effect=ConnectionRefusedError("refused")
        ):
            result = asyncio.run(self.srv.memory_query(query="q", top_k=2))
        payload = json.loads(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_name"], "MEMORY_UNAVAILABLE", payload)

    def test_success_still_works(self):
        """Happy path: ToolQ responds; result is passed through with endpoint."""
        fake = {"ok": True, "hits": [{"id": 1}]}
        with mock.patch.object(self.srv, "_toolq_post", return_value=fake) as post_mock:
            result = asyncio.run(self.srv.memory_query(query="q", top_k=2))
        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["endpoint"], "/pre_search")
        self.assertEqual(payload["result"], fake)
        self.assertEqual(post_mock.call_args.kwargs.get("timeout"), 30)

    def test_query_mode_uses_query_endpoint(self):
        """mode='query' must route to /query (not /pre_search) with the same
        30s timeout."""
        with mock.patch.object(
            self.srv, "_toolq_post", return_value={"ok": True}
        ) as post_mock:
            asyncio.run(self.srv.memory_query(query="q", top_k=2, mode="query"))
        args, kwargs = post_mock.call_args
        self.assertEqual(args[0], "/query")
        self.assertEqual(kwargs.get("timeout"), 30)


if __name__ == "__main__":
    unittest.main()
