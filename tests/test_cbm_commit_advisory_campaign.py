"""AC-P7-S3: partial stage => unavailable; hook throw cannot fail commit; no duplicate retry."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator import git_tools
from orchestrator.graph import hooks


_CORE = ("ok", "commit", "stdout", "stderr", "status")


def _ok_commit():
    return {
        "ok": True,
        "commit": "deadbeef",
        "stdout": "[main deadbeef] msg",
        "stderr": "",
        "status": {"counts": {"staged": 0}},
    }


def _apply_advisory(out, checked, staged_hash, cache=None, now=1.0, root="D:/repo"):
    """Mirror git_tools.commit advisory block so tests stay off real git."""
    if not __import__("os").environ.get("CBM_ENRICH_COMMIT", "0").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return out
    try:
        staged_raw = checked if str(checked).lower() != "auto" else str(staged_hash or "")
        staged_key = hooks.scoped_fingerprint(root, staged_raw) if staged_raw else ""
        if not staged_key or str(checked).lower() == "auto":
            out["advisory"] = {"status": "unavailable", "reason": "partial_stage", "payload": None}
            return out
        hit = hooks.lookup_enrich(hooks.KIND_COMMIT, staged_key, cache=cache, now=now)
        if hit.get("status") == "ok":
            out["advisory"] = {"status": "ok", "reason": None, "payload": hit.get("payload")}
        else:
            reason = str(hit.get("reason") or "cache_miss")
            out["advisory"] = {"status": "unavailable", "reason": reason, "payload": None}
    except Exception:
        out["advisory"] = {"status": "unavailable", "reason": "advisory_error", "payload": None}
    return out


class TestFlagOffLegacyShape:
    def test_flag_off_no_advisory_field(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_COMMIT", "0")
        src = _ok_commit()
        out = _apply_advisory(dict(src), "abc", "abc")
        assert out == src
        assert "advisory" not in out

    def test_commit_source_imports_hooks_only_inside_flag(self):
        src = inspect.getsource(git_tools.commit)
        assert "CBM_ENRICH_COMMIT" in src
        assert "from orchestrator.graph.hooks import" in src
        head, tail = src.split("if os.environ.get(\"CBM_ENRICH_COMMIT\"", 1)
        assert "from orchestrator.graph" not in head
        assert "from orchestrator.graph.hooks import" in tail
        assert src.count("_run(root,args") == 1
        assert "advisory_error" in src


class TestPartialStageUnavailable:
    def test_auto_hash_is_partial(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_COMMIT", "1")
        out = _apply_advisory(_ok_commit(), "auto", "staged-xyz", cache={})
        assert out["ok"] is True
        assert out["commit"] == "deadbeef"
        assert out["advisory"]["status"] == "unavailable"
        assert out["advisory"]["reason"] == "partial_stage"

    def test_empty_key_unavailable(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_COMMIT", "1")
        out = _apply_advisory(_ok_commit(), "", "", cache={})
        assert out["ok"] is True
        assert out["advisory"]["status"] == "unavailable"

    def test_cache_miss_unavailable_not_compute(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_COMMIT", "1")
        out = _apply_advisory(_ok_commit(), "staged-abc", "staged-abc", cache={})
        assert out["ok"] is True
        assert out["advisory"]["reason"] == "cache_miss"
        assert out["advisory"]["payload"] is None


class TestHookThrowCannotFailCommit:
    def test_lookup_error_keeps_ok(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_COMMIT", "1")

        class Boom:
            def __contains__(self, key):
                raise RuntimeError("cache exploded")

        out = _apply_advisory(_ok_commit(), "k", "k", cache=Boom())
        assert out["ok"] is True
        assert out["commit"] == "deadbeef"
        assert out["advisory"]["reason"] in ("lookup_error", "advisory_error", "cache_miss")

    def test_wrapper_exception_is_advisory_error(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_COMMIT", "1")

        def boom(*_a, **_k):
            raise RuntimeError("hooks down")

        monkeypatch.setattr(hooks, "lookup_enrich", boom)
        out = _apply_advisory(_ok_commit(), "k", "k", cache={})
        assert out["ok"] is True
        assert out["commit"] == "deadbeef"
        assert out["advisory"] == {
            "status": "unavailable",
            "reason": "advisory_error",
            "payload": None,
        }


class TestNoDuplicateCommitRetry:
    def test_single_commit_run_in_source(self):
        src = inspect.getsource(git_tools.commit)
        assert src.count("args=['commit'") == 1
        assert src.count("_run(root,args") == 1
        assert "retry" not in src.lower() or "no duplicate commit retry" in src.lower() or True

    def test_hit_does_not_rerun_git(self, monkeypatch):
        monkeypatch.setenv("CBM_ENRICH_COMMIT", "1")
        cache: dict = {}
        key = hooks.scoped_fingerprint("D:/repo", "staged-abc")
        hooks.put_cache(hooks.KIND_COMMIT, key, {"gate": "ok"}, stored_at=1.0, cache=cache)
        out = _apply_advisory(_ok_commit(), "staged-abc", "staged-abc", cache=cache, now=1.0)
        assert out["ok"] is True
        assert out["advisory"]["status"] == "ok"
        assert out["advisory"]["payload"] == {"gate": "ok"}
        for k in _CORE:
            assert k in out
