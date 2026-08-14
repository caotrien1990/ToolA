"""Phase B regression: open_session must reuse a TTL-cached matrix-audit summary.

Why: live-server measurement (2026-08-11) showed `_audit_tool_matrix_sync`
costs ~22ms serial / ~420ms p50 at 20 concurrent (filesystem + regex scan of
~50 SKILL.md files + bootstrap + harness). With 20 concurrent open_session
calls, this was the dominant single contributor to a p50 of ~6.3 seconds.
The skill tree is static within a session, so a short TTL absorbs the burst
without changing externally visible behavior. This test guards the invariant:
"two open_session-triggered audit calls within the TTL run the underlying
scan at most once."

B-01 (2026-08-11): the cache is keyed by NORMALIZED project_path
(os.path.normcase(os.path.abspath(...))) and the single-flight in-flight
event is also per-key. Before B-01, the cache was a single process-global
{"at", "payload"} slot, so within the 30s TTL open_session for Project B
returned Project A's matrix_audit/drift/progressive_gap payload. These tests
now pin both invariants: (a) same-project calls within TTL share one scan,
and (b) cross-project calls within TTL each run their own scan and each get
their own project's payload.
"""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

import code_tools


def _reset_matrix_audit_open_session_cache() -> None:
    """Clear every per-key cache entry and in-flight event (hermetic tests)."""
    with code_tools._MATRIX_AUDIT_OPEN_SESSION_CACHE_LOCK:
        code_tools._MATRIX_AUDIT_OPEN_SESSION_CACHE.clear()
        code_tools._MATRIX_AUDIT_OPEN_SESSION_INFLIGHT.clear()


class TestMatrixAuditOpenSessionCache(unittest.TestCase):
    def setUp(self) -> None:
        # Reset the cache so each test is hermetic.
        _reset_matrix_audit_open_session_cache()

    def tearDown(self) -> None:
        _reset_matrix_audit_open_session_cache()

    def test_second_call_within_ttl_uses_cache(self) -> None:
        """Two calls within TTL must invoke the underlying audit at most once."""
        calls = []

        def fake_audit(project_path: str = "", persist: bool = True):
            calls.append((project_path, persist))
            return {
                "ok": True,
                "generated_at": "2026-08-11T00:00:00",
                "summary": {"drift_count": 0, "alias_drift_count": 0,
                            "progressive_gap_count": 0, "alias_drift_severity": "none"},
                "drift": [],
            }

        # Patch the module attribute the cache helper dereferences.
        with patch.object(code_tools, "_audit_tool_matrix_sync", fake_audit):
            a = code_tools._matrix_audit_open_session_summary("D:/x")
            b = code_tools._matrix_audit_open_session_summary("D:/x")
        self.assertEqual(len(calls), 1, "TTL cache miss should re-scan only once")
        self.assertEqual(a["summary"]["drift_count"], 0)
        self.assertEqual(b["summary"]["drift_count"], 0)
        # Returned payloads are independent dicts (handler may mutate).
        self.assertIsNot(a, b)

    def test_cache_expires_after_ttl(self) -> None:
        """After TTL, a fresh call must re-invoke the underlying audit."""
        calls = []

        def fake_audit(project_path: str = "", persist: bool = True):
            calls.append(time.monotonic())
            return {
                "ok": True,
                "generated_at": "2026-08-11T00:00:00",
                "summary": {"drift_count": 1, "alias_drift_count": 0,
                            "progressive_gap_count": 0, "alias_drift_severity": "none"},
                "drift": [{"type": "x"}],
            }

        with patch.object(code_tools, "_audit_tool_matrix_sync", fake_audit):
            code_tools._matrix_audit_open_session_summary("D:/x")
            # Force expiry by pushing the per-key cache timestamp into the past.
            key = code_tools._matrix_audit_cache_key("D:/x")
            with code_tools._MATRIX_AUDIT_OPEN_SESSION_CACHE_LOCK:
                code_tools._MATRIX_AUDIT_OPEN_SESSION_CACHE[key]["at"] = (
                    time.monotonic()
                    - code_tools._MATRIX_AUDIT_OPEN_SESSION_CACHE_TTL_SECONDS
                    - 0.01
                )
            code_tools._matrix_audit_open_session_summary("D:/x")
        self.assertEqual(len(calls), 2, "Expired TTL must trigger a fresh scan")

    def test_concurrent_calls_within_ttl_single_scan(self) -> None:
        """20 concurrent calls within TTL must not amplify the underlying scan.

        This is the exact invariant the optimization was designed to satisfy
        (measured live: 20 concurrent open_session → p50 ~6.3s). Without the
        cache, every thread would re-run the scan; with it, the burst collapses
        to a single scan + dict copies.
        """
        calls = []
        calls_lock = threading.Lock()

        def fake_audit(project_path: str = "", persist: bool = True):
            # Simulate the measured ~22ms serial cost
            time.sleep(0.022)
            with calls_lock:
                calls.append(time.monotonic())
            return {
                "ok": True,
                "generated_at": "2026-08-11T00:00:00",
                "summary": {"drift_count": 0, "alias_drift_count": 0,
                            "progressive_gap_count": 0, "alias_drift_severity": "none"},
                "drift": [],
            }

        with patch.object(code_tools, "_audit_tool_matrix_sync", fake_audit):
            def worker(_):
                return code_tools._matrix_audit_open_session_summary("D:/x")

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

        # Either one thread wins the miss and the rest hit the warm cache, or
        # (worst case) a few threads slip through before the first commit lands.
        # We assert the burst does NOT trigger 20 scans — that was the whole bug.
        self.assertLessEqual(
            len(calls), 5,
            f"20 concurrent calls triggered {len(calls)} scans; cache failed to absorb burst"
        )

    def test_payload_shape_matches_open_session_consumers(self) -> None:
        """open_session reads `_mx.summary` and `_mx.drift`. The cache must
        preserve both keys with the same types as the underlying audit."""
        def fake_audit(project_path: str = "", persist: bool = True):
            return {
                "ok": True,
                "generated_at": "2026-08-11T00:00:00",
                "summary": {"drift_count": 3, "alias_drift_count": 2,
                            "progressive_gap_count": 1, "alias_drift_severity": "medium"},
                "drift": [{"type": "missing_schema", "tool": "x"}],
                # Extra keys the helper returns but open_session ignores
                "coverage": {"x": {}},
                "matrix_log_path": "D:/x/.orchestrator_cache/matrix_log.jsonl",
                "persisted": True,
            }

        with patch.object(code_tools, "_audit_tool_matrix_sync", fake_audit):
            payload = code_tools._matrix_audit_open_session_summary("D:/x")

        # Handler consumed keys must be present with right types.
        self.assertIsInstance(payload.get("summary"), dict)
        self.assertEqual(payload["summary"]["drift_count"], 3)
        self.assertEqual(payload["summary"]["progressive_gap_count"], 1)
        self.assertIsInstance(payload.get("drift"), list)
        # Truthy check `bool(_mx.get('drift'))` must still work for drift presence
        self.assertTrue(bool(payload.get("drift")))


    def test_cross_project_calls_within_ttl_each_get_own_payload(self) -> None:
        """B-01 regression: A then B within TTL must NOT share one cached payload.

        Why: before B-01 the cache was a single process-global {"at","payload"}
        slot. open_session for Project A warmed it; open_session for Project B
        within 30s then returned A's matrix_audit/drift/progressive_gap summary
        (wrong project's drift surfaced in B's session bootstrap). The fix keys
        the cache (and the single-flight election) by normalized project_path,
        so each project runs its own scan and caches its own payload.
        """
        calls = []

        def fake_audit(project_path: str = "", persist: bool = True):
            calls.append(project_path)
            # Marker encodes WHICH project was scanned, so a cross-project
            # cache leak is directly observable in the returned payload.
            return {
                "ok": True,
                "generated_at": "2026-08-11T00:00:00",
                "summary": {"drift_count": 0, "alias_drift_count": 0,
                            "progressive_gap_count": 0,
                            "alias_drift_severity": "none",
                            "project_marker": project_path},
                "drift": [],
            }

        with patch.object(code_tools, "_audit_tool_matrix_sync", fake_audit):
            a = code_tools._matrix_audit_open_session_summary("D:/project_a")
            b = code_tools._matrix_audit_open_session_summary("D:/project_b")

        # Two distinct project paths must each trigger exactly one scan.
        self.assertEqual(len(calls), 2,
                         "A then B within TTL must scan twice (once per project), "
                         "not reuse A's cached payload for B")
        self.assertEqual(calls[0], "D:/project_a")
        self.assertEqual(calls[1], "D:/project_b")
        # And each caller must receive ITS OWN project's marker.
        self.assertEqual(a["summary"]["project_marker"], "D:/project_a")
        self.assertEqual(b["summary"]["project_marker"], "D:/project_b")

    def test_same_project_path_variants_share_one_scan(self) -> None:
        """Path normalization: slash-style and backslash-style spellings (and
        case variants on Windows) of the same project path must collapse to a
        single cache key — otherwise B-01's fix would silently disable the
        burst-absorption the cache exists for."""
        calls = []

        def fake_audit(project_path: str = "", persist: bool = True):
            calls.append(project_path)
            return {
                "ok": True,
                "generated_at": "2026-08-11T00:00:00",
                "summary": {"drift_count": 0, "alias_drift_count": 0,
                            "progressive_gap_count": 0,
                            "alias_drift_severity": "none"},
                "drift": [],
            }

        with patch.object(code_tools, "_audit_tool_matrix_sync", fake_audit):
            first = code_tools._matrix_audit_open_session_summary("D:/x")
            second = code_tools._matrix_audit_open_session_summary("D:" + chr(92) + "x")
        self.assertEqual(len(calls), 1,
                         "Normalized path variants of the same project must "
                         "share the per-key cache entry (one scan)")
        self.assertEqual(first["summary"], second["summary"])

    def test_per_key_single_flight_election_is_independent(self) -> None:
        """Distinct projects elect leaders independently: a slow scan for A
        must not serialize or starve B's scan (they hold different keys)."""
        calls = []
        calls_lock = threading.Lock()

        def fake_audit(project_path: str = "", persist: bool = True):
            # Small delay so both threads overlap inside the "scan".
            time.sleep(0.05)
            with calls_lock:
                calls.append(project_path)
            return {
                "ok": True,
                "generated_at": "2026-08-11T00:00:00",
                "summary": {"drift_count": 0, "alias_drift_count": 0,
                            "progressive_gap_count": 0,
                            "alias_drift_severity": "none",
                            "project_marker": project_path},
                "drift": [],
            }

        results = {}
        with patch.object(code_tools, "_audit_tool_matrix_sync", fake_audit):
            def worker(path):
                results[path] = code_tools._matrix_audit_open_session_summary(path)

            t_a = threading.Thread(target=worker, args=("D:/proj_a",))
            t_b = threading.Thread(target=worker, args=("D:/proj_b",))
            started = time.monotonic()
            t_a.start(); t_b.start()
            t_a.join(timeout=5.0); t_b.join(timeout=5.0)
            elapsed = time.monotonic() - started

        self.assertEqual(sorted(calls), ["D:/proj_a", "D:/proj_b"])
        self.assertEqual(results["D:/proj_a"]["summary"]["project_marker"], "D:/proj_a")
        self.assertEqual(results["D:/proj_b"]["summary"]["project_marker"], "D:/proj_b")
        # Both scans ran concurrently (~50ms each, parallel) — well under the
        # ~100ms serial cost if B had wrongly waited on A's in-flight event.
        self.assertLess(elapsed, 0.095,
                        f"per-key single-flight serialized distinct projects ({elapsed:.3f}s)")


if __name__ == "__main__":
    unittest.main()
