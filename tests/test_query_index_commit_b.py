"""Regression tests for Commit B: P2-SRCH-01 + P2-IDX-02.

P2-SRCH-01 (S2): search_code(mode="index")/query_index previously had no
deadline around index readiness (pin), auto_refresh (full project-map rebuild),
or the drift check. A cold/contended index produced a client-observed 15s
zero-response timeout, and an unhealthy index state could surface as a
misleading successful matched:0. These tests pin the new contract: any
readiness failure returns a structured degraded state (INDEX_QUERY_TIMEOUT /
index_not_ready) and never a false matched:0.

P2-IDX-02 (S2): query_index(query="search_code") returned matched:0 even though
orchestrator/tools/search.py contains that identifier, because the query filter
only substring-matched file PATHS. The INDEX functions/classes symbol maps and
the pinned snapshot content blobs were never consulted. These tests prove that
filename and file content (via indexed symbols and bounded blob scan) are
independently queryable, and that a genuine zero now carries query_scope
metadata identifying exactly which scopes were searched.
"""
import asyncio
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import code_tools
from orchestrator import search_snapshots


class TestQueryIndexCommitB(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="query_index_commit_b_"))
        # Fixture: a file whose NAME does not contain the query identifier but
        # whose CONTENT defines it — mirrors search_code living in
        # orchestrator/tools/search.py (P2-IDX-02).
        (self.root / "tools").mkdir()
        self.src = self.root / "tools" / "search.py"
        self.src.write_text(
            "async def search_code(query: str = '') -> str:\n"
            "    return query\n"
            "\n"
            "class QueryEngine:\n"
            "    pass\n",
            encoding="utf-8",
        )
        self.other = self.root / "notes.md"
        self.other.write_text("# nothing relevant here\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _generate(self):
        return json.loads(asyncio.run(code_tools.generate_project_map(str(self.root))))

    def _query(self, **kwargs):
        raw = asyncio.run(code_tools.query_index(str(self.root), **kwargs))
        return raw, json.loads(raw)

    # ── P2-IDX-02: symbol scope ──────────────────────────────────────────

    def test_symbol_query_hits_indexed_function_not_path(self):
        """Known-symbol query must hit via the INDEX symbol map even though no
        file path contains the identifier. Pre-fix this returned matched:0."""
        self._generate()
        _raw, payload = self._query(query="search_code", auto_refresh=False)

        self.assertTrue(payload["ok"], msg=payload)
        self.assertGreaterEqual(payload["matched"], 1, msg=payload)
        hit = next(
            (row for row in payload["files"] if row["path"].endswith("search.py")),
            None,
        )
        self.assertIsNotNone(hit, msg=payload)
        self.assertIn("symbol", hit.get("matched_via", []), msg=hit)
        self.assertIn("search_code", hit.get("matched_symbols", []), msg=hit)
        self.assertIn("symbol", payload.get("query_scope", []), msg=payload)

    def test_content_query_hits_blob_when_symbol_map_misses(self):
        """A content token that is not an indexed symbol name and not in any
        path must still be found via the bounded snapshot blob scan."""
        self.src.write_text(
            self.src.read_text(encoding="utf-8")
            + "\nUNIQUE_CONTENT_TOKEN_ZQXWV = 42\n",
            encoding="utf-8",
        )
        self._generate()
        _raw, payload = self._query(query="UNIQUE_CONTENT_TOKEN_ZQXWV", auto_refresh=False)

        self.assertTrue(payload["ok"], msg=payload)
        self.assertGreaterEqual(payload["matched"], 1, msg=payload)
        hit = next(
            (row for row in payload["files"] if row["path"].endswith("search.py")),
            None,
        )
        self.assertIsNotNone(hit, msg=payload)
        self.assertIn("content", hit.get("matched_via", []), msg=hit)
        self.assertIn("content", payload.get("query_scope", []), msg=payload)

    def test_path_scope_still_matches_filename(self):
        """Filename queries must keep working (pre-fix behavior preserved)."""
        self._generate()
        _raw, payload = self._query(query="search.py", auto_refresh=False)

        self.assertTrue(payload["ok"], msg=payload)
        self.assertGreaterEqual(payload["matched"], 1, msg=payload)
        hit = payload["files"][0]
        self.assertIn("path", hit.get("matched_via", []), msg=hit)

    def test_genuine_zero_is_unambiguous_with_query_scope(self):
        """A truly absent query returns matched:0 PLUS query_scope metadata so
        the caller can distinguish 'searched everywhere, not present' from the
        pre-fix false zero."""
        self._generate()
        _raw, payload = self._query(query="definitely_absent_identifier_zzz", auto_refresh=False)

        self.assertTrue(payload["ok"], msg=payload)
        self.assertEqual(payload["matched"], 0, msg=payload)
        self.assertIn("query_scope", payload, msg=payload)
        self.assertIn("path", payload["query_scope"], msg=payload)
        self.assertIn("symbol", payload["query_scope"], msg=payload)
        self.assertIn("content", payload["query_scope"], msg=payload)

    # ── P2-SRCH-01: deadline / degraded state ────────────────────────────

    def test_pin_deadline_returns_structured_timeout_not_matched_zero(self):
        """When the snapshot state lease cannot be acquired inside the budget,
        query_index must return INDEX_QUERY_TIMEOUT (degraded), never hang past
        the client timeout and never return matched:0."""
        self._generate()

        real_pin = search_snapshots.pin_current_snapshot

        def slow_pin(project_root, expected_id=None, *, deadline_at=None):
            # Simulate a refresh-in-progress holding the state lease: burn the
            # caller's deadline, then raise exactly what the real lease raises.
            if deadline_at is not None:
                remaining = float(deadline_at) - time.monotonic()
                if remaining > 0:
                    time.sleep(min(remaining + 0.05, 3.0))
            raise search_snapshots.SnapshotDeadlineExceeded(
                "snapshot state lease deadline exceeded"
            )

        with mock.patch.object(
            code_tools.search_snapshots,
            "pin_current_snapshot",
            side_effect=slow_pin,
        ):
            started = time.monotonic()
            _raw, payload = self._query(auto_refresh=False)
            elapsed = time.monotonic() - started

        self.assertFalse(payload["ok"], msg=payload)
        self.assertEqual(payload["error_name"], "INDEX_QUERY_TIMEOUT", msg=payload)
        self.assertEqual(payload["category"], "index_not_ready", msg=payload)
        self.assertNotIn("matched", payload, msg=payload)
        self.assertNotIn("files", payload, msg=payload)
        self.assertLess(elapsed, 14.0, msg=f"exceeded client-observed timeout: {elapsed:.1f}s")
        # Real pin restored — sanity that the mock did not leak.
        self.assertIs(code_tools.search_snapshots.pin_current_snapshot, real_pin)

    def test_refresh_deadline_returns_structured_timeout_on_cold_index(self):
        """Cold index + auto_refresh: the full project-map rebuild is bounded;
        on expiry the response is a structured degraded state (not a bare
        client timeout, not matched:0)."""
        # No generate_project_map: snapshot missing -> refresh path.
        def slow_refresh(_root):
            time.sleep(3.0)
            return json.dumps({"ok": True, "snapshot_id": "late"})

        with mock.patch.object(code_tools, "_query_index_deadline_seconds", return_value=1.0):
            with mock.patch.object(
                code_tools,
                "refresh_project_map",
                mock.AsyncMock(side_effect=slow_refresh),
            ):
                started = time.monotonic()
                _raw, payload = self._query(auto_refresh=True)
                elapsed = time.monotonic() - started

        self.assertFalse(payload["ok"], msg=payload)
        self.assertEqual(payload["error_name"], "INDEX_QUERY_TIMEOUT", msg=payload)
        self.assertEqual(payload["category"], "index_not_ready", msg=payload)
        self.assertNotIn("matched", payload, msg=payload)
        self.assertLess(elapsed, 14.0, msg=f"exceeded client-observed timeout: {elapsed:.1f}s")

    def test_healthy_warm_index_query_is_fast_and_hit(self):
        """Warm index: a known-symbol query returns a real hit quickly — the
        live post-fix expectation (hit or clear degraded state, never a
        timeout-as-matched:0)."""
        self._generate()
        started = time.monotonic()
        _raw, payload = self._query(query="search_code", auto_refresh=False)
        elapsed = time.monotonic() - started

        self.assertTrue(payload["ok"], msg=payload)
        self.assertGreaterEqual(payload["matched"], 1, msg=payload)
        self.assertLess(elapsed, 5.0, msg=f"warm query too slow: {elapsed:.1f}s")

    def test_refresh_in_progress_then_retry_returns_hit_or_degraded(self):
        """Cold -> refresh -> retry sequence: the retry after a completed
        refresh must return a genuine hit for the known symbol (the original
        P2-SRCH-01 observation was matched:0 on retry)."""
        _raw, first = self._query(query="search_code", auto_refresh=True)
        self.assertTrue(first["ok"], msg=first)
        self.assertTrue(first.get("auto_refreshed"), msg=first)

        _raw, retry = self._query(query="search_code", auto_refresh=False)
        self.assertTrue(retry["ok"], msg=retry)
        self.assertGreaterEqual(retry["matched"], 1, msg=retry)


if __name__ == "__main__":
    unittest.main()
