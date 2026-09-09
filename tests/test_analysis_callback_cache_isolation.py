"""Tool closures must never reuse another callback's final result."""
import asyncio
from orchestrator.graph import analysis_service as svc


def test_callback_results_are_not_shared(tmp_path):
    async def scenario():
        for label in ("quality", "dsm", "testgap", "quality-changed-mode"):
            def work_fn(**kwargs):
                return {"status": "ok", "tool_result": label}
            result = await svc.run_analysis_view(str(tmp_path), included_files=[], work_fn=work_fn)
            assert result["tool_result"] == label
            assert result["cache_hit"] is False
        assert svc.active_worker_count() == 0
    asyncio.run(scenario())


def test_callback_ignores_existing_view_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_cache_get", lambda *args: {"status": "ok", "wrong": True})
    async def scenario():
        result = await svc.run_analysis_view(
            str(tmp_path), included_files=[],
            work_fn=lambda **kwargs: {"status": "ok", "right": True})
        assert result.get("right") is True
        assert "wrong" not in result
    asyncio.run(scenario())
