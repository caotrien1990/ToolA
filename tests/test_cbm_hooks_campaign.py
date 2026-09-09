"""AC-P7-S1: disabled no access; stale/miss => skipped+reason; helper p95<=20ms; TTL 60s + fingerprint."""
from __future__ import annotations

import inspect
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import config as cfg
from orchestrator.graph import hooks


class _BoomCache(dict):
    def __contains__(self, key):  # type: ignore[override]
        raise AssertionError("cache accessed while disabled")

    def __getitem__(self, key):  # type: ignore[override]
        raise AssertionError("cache accessed while disabled")

    def get(self, key, default=None):  # type: ignore[override]
        raise AssertionError("cache accessed while disabled")


class TestFlagDefaultsOff:
    def test_defaults_are_off(self, monkeypatch):
        monkeypatch.delenv("CBM_ENRICH_TEST", raising=False)
        monkeypatch.delenv("CBM_ENRICH_COMMIT", raising=False)
        monkeypatch.delenv("CBM_ENRICH_COUNCIL", raising=False)
        assert cfg.enrich_test_enabled() is False
        assert cfg.enrich_commit_enabled() is False
        assert cfg.enrich_council_enabled() is False

    def test_live_reread_not_frozen(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_TEST", "0")
        assert cfg.enrich_test_enabled() is False
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")
        assert cfg.enrich_test_enabled() is True
        monkeypatch.setenv("CBM_ENRICH_TEST", "0")
        assert cfg.enrich_test_enabled() is False


class TestDisabledNoAccess:
    def test_disabled_skips_without_cache_read(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_TEST", "0")
        out = hooks.lookup_enrich(hooks.KIND_TEST, "abc", cache=_BoomCache())
        assert out["status"] == "skipped"
        assert out["reason"] == "disabled"
        assert out["payload"] is None

    def test_disabled_all_kinds(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_TEST", "0")
        monkeypatch.setenv("CBM_ENRICH_COMMIT", "0")
        monkeypatch.setenv("CBM_ENRICH_COUNCIL", "0")
        for kind in (hooks.KIND_TEST, hooks.KIND_COMMIT, hooks.KIND_COUNCIL):
            out = hooks.lookup_enrich(kind, "fp", cache=_BoomCache())
            assert out["reason"] == "disabled", kind

    def test_module_is_cache_read_only(self):
        src = inspect.getsource(hooks)
        assert "import subprocess" not in src
        assert "from subprocess" not in src
        assert "from . import status" not in src
        assert "from . import indexer" not in src
        assert "git_tools" not in src
        assert "compute_evolution" not in src
        assert "compute_test_gap" not in src
        assert "from .config import" in src
        assert "os.system" not in src
        assert "Popen" not in src


class TestCacheLookup:
    def test_miss_is_skipped(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")
        cache: dict = {}
        out = hooks.lookup_enrich(hooks.KIND_TEST, "missing", cache=cache, now=100.0)
        assert out["status"] == "skipped"
        assert out["reason"] == "cache_miss"
        assert out["payload"] is None

    def test_empty_fingerprint_is_miss(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")
        out = hooks.lookup_enrich(hooks.KIND_TEST, "  ", cache={}, now=100.0)
        assert out["reason"] == "cache_miss"

    def test_stale_ttl(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_COMMIT", "1")
        cache: dict = {}
        hooks.put_cache(
            hooks.KIND_COMMIT, "staged-abc", {"ok": True}, stored_at=10.0, cache=cache
        )
        stale = hooks.lookup_enrich(
            hooks.KIND_COMMIT, "staged-abc", cache=cache, now=10.0 + hooks.CACHE_TTL_S + 0.001
        )
        assert stale["status"] == "skipped"
        assert stale["reason"] == "cache_stale"
        fresh = hooks.lookup_enrich(
            hooks.KIND_COMMIT, "staged-abc", cache=cache, now=10.0 + hooks.CACHE_TTL_S
        )
        assert fresh["status"] == "ok"
        assert fresh["payload"] == {"ok": True}

    def test_fingerprint_mismatch(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")
        cache = {
            (hooks.KIND_TEST, "want"): {
                "fingerprint": "other",
                "stored_at": 1.0,
                "payload": {"x": 1},
            }
        }
        out = hooks.lookup_enrich(hooks.KIND_TEST, "want", cache=cache, now=1.0)
        assert out["status"] == "skipped"
        assert out["reason"] == "fingerprint_mismatch"

    def test_hit_returns_payload(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_COUNCIL", "1")
        cache: dict = {}
        hooks.put_cache(
            hooks.KIND_COUNCIL, "pack-1", {"files": ["a.py"]}, stored_at=5.0, cache=cache
        )
        out = hooks.lookup_enrich(hooks.KIND_COUNCIL, "pack-1", cache=cache, now=5.5)
        assert out["status"] == "ok"
        assert out["reason"] is None
        assert out["payload"] == {"files": ["a.py"]}
        assert out["fingerprint"] == "pack-1"
        assert out["age_s"] == 0.5

    def test_lookup_error_is_skipped(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")

        class Bad:
            def __contains__(self, key):
                raise RuntimeError("boom")

        out = hooks.lookup_enrich(hooks.KIND_TEST, "x", cache=Bad())  # type: ignore[arg-type]
        assert out["status"] == "skipped"
        assert out["reason"] == "lookup_error"


class TestHelperLatency:
    def test_p95_lookup_under_20ms(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")
        cache: dict = {}
        hooks.put_cache(hooks.KIND_TEST, "hot", {"n": 1}, stored_at=0.0, cache=cache)
        samples: list[float] = []
        for _ in range(40):
            t0 = time.perf_counter()
            out = hooks.lookup_enrich(hooks.KIND_TEST, "hot", cache=cache, now=1.0)
            samples.append((time.perf_counter() - t0) * 1000.0)
            assert out["status"] == "ok"
        samples.sort()
        p95 = samples[int(len(samples) * 0.95) - 1]
        assert p95 <= 20.0, p95


_CORE = ("ok", "exit_code", "stdout", "stderr", "duration_seconds",
         "script_path", "script_hash", "script_label", "runtime_used",
         "timed_out", "ca_next_action", "stdout_metadata", "stderr_metadata")


def _runner(ok=True, exit_code=0, timed_out=False, stdout="ok", stderr="",
            executed=None, script_hash="abc123"):
    row = {
        "ok": ok,
        "exit_code": exit_code,
        "stdout": stdout,
        "stdout_metadata": {"truncated": False},
        "stderr": stderr,
        "stderr_metadata": {"truncated": False},
        "duration_seconds": 0.1,
        "script_path": "x.py",
        "script_hash": script_hash,
        "script_label": "t",
        "runtime_used": "python",
        "timed_out": timed_out,
        "ca_next_action": "evaluate_stdout_assertions",
    }
    if executed is not None:
        row["executed_test_files"] = executed
    return row


class TestH2PreserveOriginalFields:
    def _assert_core(self, original, enriched):
        for k in _CORE:
            assert enriched[k] == original[k], k

    def test_flag_off_identical(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_TEST", "0")
        for kind, kwargs in (
            ("success", {}),
            ("assertion", {"ok": False, "exit_code": 1, "stdout": "FAILED"}),
            ("runtime", {"ok": False, "exit_code": 2, "stderr": "Traceback"}),
            ("timeout", {"ok": False, "timed_out": True, "exit_code": None}),
        ):
            src = _runner(**kwargs)
            out = hooks.enrich_test_result(src)
            assert "test_gap" not in out, kind
            self._assert_core(src, out)

    def test_unknown_selection_not_from_stdout(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")
        src = _runner(stdout="tests/test_foo.py::test_a PASSED\n")
        out = hooks.enrich_test_result(src, cache={})
        self._assert_core(src, out)
        assert out["test_gap"]["reason"] == "test_selection_unknown"
        assert out["test_gap"]["payload"] is None

    def test_explicit_files_plus_cache_hit(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")
        cache: dict = {}
        hooks.put_cache(hooks.KIND_TEST, "abc123", {"coverage_ratio": 0.5}, stored_at=1.0, cache=cache)
        src = _runner(executed=["tests/test_a.py"])
        out = hooks.enrich_test_result(src, cache=cache, now=1.0)
        self._assert_core(src, out)
        assert out["test_gap"]["status"] == "ok"
        assert out["test_gap"]["payload"]["executed_test_files"] == ["tests/test_a.py"]

    def test_does_not_parse_stdout_even_when_named(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_TEST", "1")
        src = _runner(stdout="===== 1 passed =====\ntests/invented.py")
        out = hooks.enrich_test_result(src, cache={})
        assert out["test_gap"]["reason"] == "test_selection_unknown"


class TestH5PackCaps:
    def test_caps_10_files_50_symbols(self):
        files = [f"f{i}.py" for i in range(15)]
        symbols = [f"s{i}" for i in range(60)]
        out = hooks.cap_council_pack(files, symbols)
        assert out["budget"] == {"max_files": 10, "max_symbols": 50}
        assert len(out["files"]) == 10
        assert len(out["symbols"]) == 50
        assert out["truncated"] is True
        assert out["truncation_reason"] == "max_files+max_symbols"
        assert out["files"] == files[:10]
        assert out["symbols"] == symbols[:50]

    def test_under_cap_untouched(self):
        out = hooks.cap_council_pack(["a.py"], ["s"])
        assert out["truncated"] is False
        assert out["truncation_reason"] is None
        assert out["files"] == ["a.py"]
        assert out["symbols"] == ["s"]
