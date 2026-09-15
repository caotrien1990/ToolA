import asyncio
import json
import subprocess

import pytest
from aiohttp.test_utils import make_mocked_request

import orchestrator_server as srv
from orchestrator import git_tools


def test_unknown_query_hint_uses_current_registry_count():
    response = asyncio.run(srv.handle_tools_names(make_mocked_request(
        "GET", "/api/tools/names?curated=0")))
    payload = json.loads(response.body)
    assert f"limit={len(srv.ct.TOOL_MAP)}" in payload["ignored_query_params_hint"]


@pytest.mark.parametrize("provider,model,expected", [
    ("openai-compatible", "gpt-6-astra", "openai-compatible/gpt-6-astra"),
    ("openai-compatible", "openai-compatible/gpt-6-astra", "openai-compatible/gpt-6-astra"),
    ("opencode-zen", "model-id", "opencode/model-id"),
])
def test_ope_cli_qualifies_bare_model(monkeypatch, tmp_path, provider, model, expected):
    monkeypatch.setattr(srv, "_council_worker_model", lambda *a, **k: (provider, model))
    argv = srv._council_agent_command("ope", "smoke", cwd=tmp_path)
    assert argv[argv.index("--model") + 1] == expected


def test_compact_git_keeps_exact_hashes_and_counts(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    folder = tmp_path / "%TEMP%toolq_tests"
    folder.mkdir()
    for i in range(30):
        (folder / f"test{i}.txt").write_text("fixture")
    (tmp_path / "user.py").write_text("value = 1\n")
    full = git_tools.status(str(tmp_path))
    compact = git_tools.status(str(tmp_path), compact_untracked=True)
    assert compact["state_hash"] == full["state_hash"]
    assert compact["staged_hash"] == full["staged_hash"]
    assert compact["counts"] == full["counts"]
    assert compact["records_total"] == 31
    assert len(compact["records"]) == 2
    group = next(x for x in compact["records"] if x.get("aggregated"))
    assert group["untracked_count"] == 30
    assert group["path"] == "%TEMP%toolq_tests/"
