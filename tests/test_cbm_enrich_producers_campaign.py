"""GAP-01: the producer side of the P7 Heavy enrich cache.

hooks.py is cache-READ only ("never cold-computes"), so before this module the
KIND_TEST / KIND_COMMIT / KIND_COUNCIL entries were wired-but-empty: consumers
reported skipped:cache_miss forever. enrich_producers cold-computes a real
payload and put_cache()s it, keyed by the SAME fingerprint the consumer reads.

These tests pin the producer->consumer contract deterministically. The compute
(testgap.build_test_gap / query.council_graph_context) is monkeypatched so the
assertions test the producer logic -- gating, fingerprinting, honesty (never
cache status="unavailable"), deadline bounding, prod/test split -- not a real
graph index. Each test passes an isolated cache=dict() so the global _STORE is
untouched.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import enrich_producers as ep
from orchestrator.graph import hooks


def _ok_payload(**extra):
    """A minimal compute_test_gap-style 'ok' payload (cacheable)."""
    base = {
        "status": "ok",
        "reason": None,
        "coverage_ratio": 0.5,
        "source_files": ["a.py", "b.py"],
        "test_files": ["test_a.py"],
        "uncovered_files": ["b.py"],
        "counts": {"source_files": 2, "test_files": 1, "covered_files": 1, "uncovered_files": 1},
    }
    base.update(extra)
    return base


def _unavailable_payload(reason="no_graph"):
    return {"status": "unavailable", "reason": reason, "payload": None}


# ── H2 produce_test_advisory ────────────────────────────────────────────────


class TestProduceTestAdvisory:
    def test_disabled_flag_does_not_warm(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CBM_ENRICH_TEST", raising=False)
        r = ep.produce_test_advisory(str(tmp_path), "abcd1234", ["test_x.py"], cache={})
        assert r == {"warmed": False, "reason": "disabled"}

    def test_no_fingerprint(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")
        r = ep.produce_test_advisory(str(tmp_path), "   ", ["test_x.py"], cache={})
        assert r["warmed"] is False and r["reason"] == "no_fingerprint"

    def test_no_test_files(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")
        r = ep.produce_test_advisory(str(tmp_path), "abcd1234", [], cache={})
        assert r["warmed"] is False and r["reason"] == "no_test_files"

    def test_bad_root(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")
        r = ep.produce_test_advisory(str(Path("/no/such/dir/xyz")), "abcd1234", ["test_x.py"], cache={})
        assert r["warmed"] is False and r["reason"] == "no_root"

    def test_happy_path_warms_and_consumer_hits(self, monkeypatch, tmp_path):
        """The whole GAP-01 fix: producer warms KIND_TEST, enrich_test_result hits."""
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")
        seen = {}

        def _fake_build(root, **kw):
            seen["root"] = root
            seen["test_files"] = list(kw.get("test_files") or [])
            seen["checkpoint_callable"] = callable(kw.get("checkpoint"))
            return _ok_payload()

        from orchestrator.graph import testgap
        monkeypatch.setattr(testgap, "build_test_gap", _fake_build)

        cache: dict = {}
        fp = "script_hash_deadbeef"
        r = ep.produce_test_advisory(str(tmp_path), fp, ["tests/test_a.py"], cache=cache)
        assert r["warmed"] is True and r["reason"] is None
        assert seen["test_files"] == ["tests/test_a.py"]
        assert seen["checkpoint_callable"] is True

        # producer stamped its own metadata onto the payload
        scoped = hooks.scoped_fingerprint(str(tmp_path), fp)
        entry = cache[(hooks.KIND_TEST, scoped)]
        assert entry["payload"]["producer"] == ep.PRODUCER_VERSION
        assert entry["payload"]["included_scope"] in ("declared", "git_changed", "declared_tests_only")

        # consumer side now returns a real advisory, not skipped:cache_miss
        enriched = hooks.enrich_test_result(
            {"script_hash": fp, "enrich_fingerprint": scoped,
             "executed_test_files": ["tests/test_a.py"]}, cache=cache
        )
        assert enriched["test_gap"]["status"] == "ok"
        assert enriched["test_gap"]["payload"]["selection"] == "explicit"
        assert enriched["test_gap"]["payload"]["coverage_ratio"] == 0.5

    def test_second_call_already_cached(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")
        calls = {"n": 0}

        def _fake_build(root, **kw):
            calls["n"] += 1
            return _ok_payload()

        from orchestrator.graph import testgap
        monkeypatch.setattr(testgap, "build_test_gap", _fake_build)
        cache: dict = {}
        ep.produce_test_advisory(str(tmp_path), "fp2", ["test_a.py"], cache=cache)
        r2 = ep.produce_test_advisory(str(tmp_path), "fp2", ["test_a.py"], cache=cache)
        assert r2["warmed"] is True and r2["reason"] == "already_cached"
        assert calls["n"] == 1  # no recompute on a fresh hit

    def test_unavailable_payload_is_never_cached(self, monkeypatch, tmp_path):
        """Honesty: a status='unavailable' compute must NOT fabricate a cache hit."""
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")

        def _fake_build(root, **kw):
            return _unavailable_payload("no_graph")

        from orchestrator.graph import testgap
        monkeypatch.setattr(testgap, "build_test_gap", _fake_build)
        cache: dict = {}
        r = ep.produce_test_advisory(str(tmp_path), "fp3", ["test_a.py"], cache=cache)
        assert r["warmed"] is False and r["reason"] == "unavailable_payload"
        scoped = hooks.scoped_fingerprint(str(tmp_path), "fp3")
        assert hooks.lookup_enrich(hooks.KIND_TEST, scoped, cache=cache)["reason"] == "cache_miss"

    def test_deadline_overrun_fails_open(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")

        def _slow_build(root, **kw):
            time.sleep(0.4)
            return _ok_payload()

        from orchestrator.graph import testgap
        monkeypatch.setattr(testgap, "build_test_gap", _slow_build)
        cache: dict = {}
        r = ep.produce_test_advisory(str(tmp_path), "fp4", ["test_a.py"], deadline_s=0.05, cache=cache)
        assert r["warmed"] is False and r["reason"] == "deadline"
        # an overrunning thread must not have written to the cache
        scoped = hooks.scoped_fingerprint(str(tmp_path), "fp4")
        assert hooks.lookup_enrich(hooks.KIND_TEST, scoped, cache=cache)["reason"] == "cache_miss"


# ── H3 produce_commit_advisory ──────────────────────────────────────────────


class TestProduceCommitAdvisory:
    def test_disabled_flag(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CBM_ENRICH_COMMIT", raising=False)
        r = ep.produce_commit_advisory(str(tmp_path), "stagedhash", ["a.py"], cache={})
        assert r == {"warmed": False, "reason": "disabled"}

    def test_no_staged_files(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CBM_ENRICH_COMMIT", "1")
        r = ep.produce_commit_advisory(str(tmp_path), "stagedhash", [], cache={})
        assert r["warmed"] is False and r["reason"] == "no_staged_files"

    def test_prod_test_split_from_path_names(self, monkeypatch, tmp_path):
        """H3 derives prod vs test from PATH NAME only -- never stdout."""
        monkeypatch.setenv("CBM_ENRICH_COMMIT", "1")
        seen = {}

        def _fake_build(root, **kw):
            seen["included"] = sorted(kw.get("included_files") or [])
            seen["tests"] = sorted(kw.get("test_files") or [])
            return _ok_payload()

        from orchestrator.graph import testgap
        monkeypatch.setattr(testgap, "build_test_gap", _fake_build)
        cache: dict = {}
        staged = ["pkg/mod.py", "tests/test_mod.py", "pkg/util.py", "test_helper.py"]
        r = ep.produce_commit_advisory(str(tmp_path), "sh1", staged, cache=cache)
        assert r["warmed"] is True
        assert seen["tests"] == ["test_helper.py", "tests/test_mod.py"]
        assert seen["included"] == ["pkg/mod.py", "pkg/util.py"]
        # consumer side: commit advisory lookup now hits
        hit = hooks.lookup_enrich(
            hooks.KIND_COMMIT, hooks.scoped_fingerprint(str(tmp_path), "sh1"), cache=cache)
        assert hit["status"] == "ok"
        assert hit["payload"]["staged_files"] == sorted(staged)
        assert hit["payload"]["producer"] == ep.PRODUCER_VERSION

    def test_unavailable_not_cached(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CBM_ENRICH_COMMIT", "1")

        def _fake_build(root, **kw):
            return _unavailable_payload()

        from orchestrator.graph import testgap
        monkeypatch.setattr(testgap, "build_test_gap", _fake_build)
        cache: dict = {}
        r = ep.produce_commit_advisory(str(tmp_path), "sh2", ["a.py"], cache=cache)
        assert r["warmed"] is False and r["reason"] == "unavailable_payload"
        assert cache == {}

    def test_same_hash_is_isolated_by_project(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CBM_ENRICH_COMMIT", "1")
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir(); b.mkdir()
        seen = []

        def _fake_build(root, **kw):
            seen.append(root)
            return _ok_payload()

        from orchestrator.graph import testgap
        monkeypatch.setattr(testgap, "build_test_gap", _fake_build)
        cache = {}
        ep.produce_commit_advisory(str(a), "same", ["x.py"], cache=cache)
        ep.produce_commit_advisory(str(b), "same", ["x.py"], cache=cache)
        assert len(seen) == 2
        assert len(cache) == 2


# ── H5 produce_council_pack ─────────────────────────────────────────────────


class TestProduceCouncilPack:
    def test_disabled_flag(self, monkeypatch):
        monkeypatch.delenv("CBM_ENRICH_COUNCIL", raising=False)
        r = ep.produce_council_pack(r"D:\some\proj", cache={})
        assert r["warmed"] is False and r["reason"] == "disabled" and r["payload"] is None

    def test_no_project(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_COUNCIL", "1")
        r = ep.produce_council_pack("   ", cache={})
        assert r["warmed"] is False and r["reason"] == "no_project"

    def test_happy_path_caps_and_attaches(self, monkeypatch):
        """H5 down-caps via hooks.cap_council_pack and returns payload for ATTACHMENT."""
        monkeypatch.setenv("CBM_ENRICH_COUNCIL", "1")
        # 40 files / 120 symbols -> must be capped to 10 / 50
        files = [{"path": f"f{i}.py"} for i in range(40)]
        symbols = [{"name": f"s{i}"} for i in range(120)]

        def _fake_ctx(root, *, name, depth, file_path):
            assert name == ""  # changes-mode
            return {"graph_status": "ready", "source": "graph",
                    "files": files, "symbols": symbols,
                    "graph_degraded_reason": None}

        from orchestrator.graph import query
        monkeypatch.setattr(query, "council_graph_context", _fake_ctx)
        cache: dict = {}
        r = ep.produce_council_pack(r"D:\Some\Proj", generation=7, cache=cache)
        assert r["warmed"] is True and r["reason"] is None
        pack = r["payload"]
        assert len(pack["files"]) == hooks.COUNCIL_CONTEXT_MAX_FILES
        assert len(pack["symbols"]) == hooks.COUNCIL_CONTEXT_MAX_SYMBOLS
        assert pack["truncated"] is True
        assert pack["graph_status"] == "ready"
        assert pack["producer"] == ep.PRODUCER_VERSION
        # fingerprint stable for (path, generation); a bumped generation misses
        fp = r["fingerprint"]
        assert hooks.lookup_enrich(hooks.KIND_COUNCIL, fp, cache=cache)["status"] == "ok"
        r_gen8 = ep.produce_council_pack(r"D:\Some\Proj", generation=8, cache=cache)
        assert r_gen8["fingerprint"] != fp

    def test_generation_invalidation_and_already_cached(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_COUNCIL", "1")
        calls = {"n": 0}

        def _fake_ctx(root, *, name, depth, file_path):
            calls["n"] += 1
            return {"graph_status": "ready", "source": "graph",
                    "files": [{"path": "f.py"}], "symbols": [],
                    "graph_degraded_reason": None}

        from orchestrator.graph import query
        monkeypatch.setattr(query, "council_graph_context", _fake_ctx)
        cache: dict = {}
        ep.produce_council_pack(r"D:\P", generation=1, cache=cache)
        r2 = ep.produce_council_pack(r"D:\P", generation=1, cache=cache)
        assert r2["reason"] == "already_cached" and calls["n"] == 1


# ── helpers ─────────────────────────────────────────────────────────────────


class TestHelpers:
    def test_looks_like_test(self):
        assert ep._looks_like_test("tests/test_a.py")
        assert ep._looks_like_test("test_a.py")
        assert ep._looks_like_test("a_test.go")
        assert ep._looks_like_test("a.test.js")
        assert ep._looks_like_test("a.spec.ts")
        assert ep._looks_like_test("pkg/tests/x.py")
        assert not ep._looks_like_test("pkg/mod.py")
        assert not ep._looks_like_test("main.py")

    def test_budget_prefers_explicit_then_env_then_default(self, monkeypatch):
        assert ep._budget_s(3.0) == 3.0
        monkeypatch.setenv(ep.BUDGET_ENV, "9")
        assert ep._budget_s(None) == 9.0
        monkeypatch.delenv(ep.BUDGET_ENV, raising=False)
        assert ep._budget_s(None) == ep.DEFAULT_BUDGET_S

    def test_checkpoint_flips_at_deadline(self):
        cp = ep._checkpoint(time.monotonic() - 1)
        assert cp("tag") is True
        cp2 = ep._checkpoint(time.monotonic() + 10)
        assert cp2("tag") is False

    def test_timed_out_thread_remains_accounted_until_exit(self):
        deadline = time.time() + 1
        while ep.active_producer_count() and time.time() < deadline:
            time.sleep(0.01)
        before = ep.active_producer_count()
        done, _ = ep._run_bounded(lambda: time.sleep(0.15), 0.01)
        assert done is False
        assert ep.active_producer_count() == before + 1
        time.sleep(0.2)
        assert ep.active_producer_count() == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
