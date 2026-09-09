"""RC1/RC2 isolated: campaign work honors checkpoint; ignore-stop hangs."""
from __future__ import annotations

import asyncio
import time

import pytest

from orchestrator.graph import analysis_service as svc
from orchestrator.graph import analysis_view as aview
from orchestrator.graph import tools as gtools

from test_graph_analysis_service import _run, _seed, _wait_idle


class TestCampaignCooperativeStop:
    def setup_method(self):
        svc.reset_runtime_state()

    def teardown_method(self):
        svc.reset_runtime_state()

    def test_ignore_stop_work_latches_quarantine(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path,
            monkeypatch,
            "ignore",
            {"a.py": "x=1\n"},
            [],
        )

        def work_fn(*, checkpoint, stop, deadline_mono, **_kw):
            del checkpoint, stop, deadline_mono
            time.sleep(1.2)
            return {"status": "ok", "score": 1.0, "pass": True}

        # asyncio.run waits leftover to_thread; a live HTTP loop does not.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            t0 = time.monotonic()
            payload = loop.run_until_complete(
                svc.run_analysis_view(
                    project_path=root,
                    included_files=["a.py"],
                    work_fn=work_fn,
                    deadline_ms=40,
                )
            )
            elapsed = time.monotonic() - t0
            assert payload["status"] == "unavailable"
            assert "worker_stop_unconfirmed" in str(payload.get("reason") or "")
            assert elapsed < 0.8
            assert svc.is_quarantined() is True
            deadline = time.monotonic() + 2.0
            while svc.active_worker_count() > 0 and time.monotonic() < deadline:
                loop.run_until_complete(asyncio.sleep(0.01))
            assert svc.active_worker_count() == 0
            assert svc.is_quarantined() is False
        finally:
            loop.close()
            asyncio.set_event_loop(None)
    def test_campaign_style_work_exits_without_quarantine(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path,
            monkeypatch,
            "honor",
            {"a.py": "import b\n", "b.py": "x=1\n"},
            [("a.py", "b.py", 1.0)],
        )

        def work_fn(
            *,
            checkpoint,
            stop,
            deadline_mono,
            project_path,
            included_files,
            test_files,
            finalize_scope,
        ):
            del finalize_scope
            gtools._raise_if_stopped(checkpoint, stop, deadline_mono)
            return aview.build_analysis_view(
                project_path,
                included_files=included_files,
                test_files=test_files,
                finalize_scope=False,
                checkpoint=checkpoint,
            )

        payload = _run(
            project_path=root,
            included_files=["a.py", "b.py"],
            work_fn=work_fn,
            deadline_ms=80,
        )
        reason = str(payload.get("reason") or "")
        assert "worker_stop_unconfirmed" not in reason
        assert payload["status"] in {"ok", "partial", "unavailable"}
        if payload["status"] == "unavailable":
            assert reason.startswith("unavailable:")
        assert _wait_idle(timeout_s=1.0) == 0
        assert svc.is_quarantined() is False

    def test_walk_checkpoint_raises_interrupted(self, tmp_path):
        root = tmp_path / "walkroot"
        (root / "orchestrator").mkdir(parents=True)
        (root / "orchestrator" / "a.py").write_text("x=1\n", encoding="utf-8")
        (root / "orchestrator" / "b.py").write_text("y=1\n", encoding="utf-8")
        hits = {"n": 0}

        def checkpoint(_label):
            hits["n"] += 1
            return hits["n"] >= 1

        with pytest.raises(InterruptedError, match="analysis_stop"):
            aview._candidate_python_files(str(root), checkpoint=checkpoint)
        assert hits["n"] >= 1

    def test_default_view_honors_checkpoint_via_service(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path,
            monkeypatch,
            "defview",
            {"a.py": "x=1\n"},
            [],
        )
        payload = _run(
            project_path=root,
            included_files=["a.py"],
            deadline_ms=40,
        )
        reason = str(payload.get("reason") or "")
        assert "worker_stop_unconfirmed" not in reason
        assert payload["status"] in {"ok", "partial", "unavailable"}
        assert _wait_idle(timeout_s=1.0) == 0
        assert svc.is_quarantined() is False

    def test_snapshot_checkpoint_stops(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path,
            monkeypatch,
            "snap",
            {"a.py": "x=1\n", "b.py": "y=1\n"},
            [],
        )
        hits = {"n": 0}

        def checkpoint(_label):
            hits["n"] += 1
            return True

        with pytest.raises(InterruptedError, match="analysis_stop"):
            svc._file_snapshot(root, ["a.py", "b.py"], checkpoint=checkpoint)
        assert hits["n"] >= 1
