"""GUIDANCE_V11 acceptance fixtures (claim GUIDANCE_V11_CLAIM_OPE_20261121).

Phase 1 acceptance, per item, all against the WORKING-TREE code (the daemon
keeps serving the old code until a restart, which the claim forbids):

- 1a read_file_range: the served payload_example is a SINGLE-range call that
  actually reads lines 10-12 of a 30-line fixture; the multi-range shape
  (ranges alone) works as its own call; mixing both groups STILL answers
  RANGES_WITH_LEGACY_PARAMS (negative preserved).
- 1b run_project_test_script: the served payload_example pairs a Python
  marker script with runtime=python and the script actually runs; the schema
  DEFAULT runtime stays "node" (backward compat).
- 1c git_diff: cached=true is remapped to scope='staged' in the corrected
  payload (intent preserved, source=alias_mapped_intent_preserved) and the
  corrected call returns the STAGED diff on a staged!=unstaged fixture;
  scope='unstaged' still fails INVALID_GIT_DIFF_SCOPE (negative preserved).
- 1c cbm_template_query: from_symbol is remapped to name and the corrected
  call returns the real callers on a graph fixture.
- 2a guidance: one SOT constant rides every served schema via
  _prepare_tool_schema (usage_guidance); SLIM/CORE untouched by this test.
- 2b table: examples/COMMON_ERRORS.md carries G1..G15.

WHY these assertions: the v10 verify report showed the generated examples
taught calls the server itself rejects (1a/1b), and the UNSUPPORTED_FIELD
corrector silently dropped the caller's intent (1c ok-but-wrong-scope).
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import orchestrator_server as osrv  # noqa: E402
from orchestrator import git_tools  # noqa: E402
from orchestrator.graph import tools as gtools  # noqa: E402
from orchestrator.tools.read import context as rctx  # noqa: E402


def _example(tool):
    return osrv._payload_example_for_tool(tool, osrv._schema_for_tool(tool))


def _run(coro):
    return json.loads(asyncio.run(coro))


# ─── 1a: read_file_range example ────────────────────────────────────────────

@pytest.fixture()
def file30(tmp_path):
    f = tmp_path / "fixture30.txt"
    f.write_text("".join(f"line{i}\n" for i in range(1, 31)), encoding="utf-8")
    return tmp_path, f


def test_1a_example_is_single_range_and_executes(file30):
    root, _ = file30
    ex = _example("read_file_range")
    assert "ranges" not in ex, ex
    assert ex["start_line"] == 10 and ex["end_line"] == 12, ex
    payload = dict(ex, project_path=str(root), file_path="fixture30.txt")
    out = _run(rctx.read_file_range(**payload))
    assert out["ok"] is True, out
    text = out["result"]
    assert "line10" in text and "line12" in text, text
    assert "line13" not in text and "line9" not in text, text


def test_1a_multi_range_shape_works_alone(file30):
    root, _ = file30
    out = _run(rctx.read_file_range(
        project_path=str(root), file_path="fixture30.txt",
        ranges=[{"start_line": 10, "end_line": 12},
                {"start_line": 20, "end_line": 25}]))
    assert out["ok"] is True, out
    text = out["result"]
    assert "line10" in text and "line20" in text and "line25" in text, text
    assert "line15" not in text, text


def test_1a_mixed_still_rejected(file30):
    root, _ = file30
    out = _run(rctx.read_file_range(
        project_path=str(root), file_path="fixture30.txt",
        start_line=1, end_line=10,
        ranges=[{"start_line": 20, "end_line": 25}]))
    assert out["ok"] is False, out
    assert out["error_name"] == "RANGES_WITH_LEGACY_PARAMS", out


def test_1a_ranges_description_teaches_call_shape():
    fn = osrv._schema_for_tool("read_file_range")
    desc = fn["parameters"]["properties"]["ranges"]["description"]
    assert "v11 1a CALL SHAPE" in desc
    assert "RANGES_WITH_LEGACY_PARAMS" in desc


# ─── 1b: run_project_test_script example ────────────────────────────────────

def test_1b_example_is_python_pair_and_executes(tmp_path):
    ex = _example("run_project_test_script")
    assert ex["runtime"] == "python", ex
    assert "print(" in ex["script_content"], ex
    r = subprocess.run([sys.executable, "-c", ex["script_content"]],
                       capture_output=True, text=True, timeout=60,
                       cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "GV11_EXAMPLE_OK" in r.stdout, r.stdout


def test_1b_schema_default_runtime_stays_node():
    fn = osrv._schema_for_tool("run_project_test_script")
    assert fn["parameters"]["properties"]["runtime"]["default"] == "node"


# ─── 1c: git_diff cached=true intent preservation ───────────────────────────

@pytest.fixture()
def git_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    def git(*args):
        r = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-c", "user.email=t@t",
             "-c", "user.name=t", *args],
            cwd=str(root), capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return r.stdout
    git("init", "-q")
    f = root / "a.txt"
    f.write_text("base\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-q", "-m", "base")
    f.write_text("base\nSTAGED_MARK\n", encoding="utf-8")
    git("add", "a.txt")
    f.write_text("base\nSTAGED_MARK\nUNSTAGED_MARK\n", encoding="utf-8")
    return root


def _correction(tool, body, unsupported):
    return osrv.build_correction_material(
        tool, "UNSUPPORTED_FIELD", body,
        sorted(body.keys()), unsupported, sorted(body.keys()),
        _example(tool), None)


def test_1c_git_cached_remaps_to_staged(git_repo):
    body = {"project_path": str(git_repo), "cached": True}
    m = _correction("git_diff", body, ["cached"])
    cp = m["corrected_payload"]
    assert cp.get("scope") == "staged", m
    assert m["corrected_payload_source"] == "alias_mapped_intent_preserved", m
    assert "cached->scope" in m["alias_remediable"], m
    out = git_tools.diff(**cp)
    assert out["ok"] is True and out["scope"] == "staged", out
    assert "STAGED_MARK" in out["diff"], out["diff"]
    assert "UNSTAGED_MARK" not in out["diff"], out["diff"]


def test_1c_git_unstaged_scope_still_rejected(git_repo):
    with pytest.raises(git_tools.GitToolError) as ei:
        git_tools.diff(str(git_repo), scope="unstaged")
    assert ei.value.error_name == "INVALID_GIT_DIFF_SCOPE", ei.value


def test_1c_git_hint_names_the_intent():
    hint = osrv._default_hint("git_diff", "UNSUPPORTED_FIELD")
    assert "scope=staged" in hint
    generic = osrv._default_hint("search_code", "UNSUPPORTED_FIELD")
    assert "scope=staged" not in generic


# ─── 1c: cbm_template_query from_symbol intent preservation ─────────────────

B_PY = "def helper():\n    return 1\n\n\ndef worker():\n    return helper()\n"
A_PY = "import b\n\n\ndef run():\n    return b.worker()\n"
C_PY = "import b\n\n\ndef main():\n    return b.helper()\n"


@pytest.fixture(scope="module")
def graph_project(tmp_path_factory):
    root = tmp_path_factory.mktemp("gv11graph")
    (root / "b.py").write_text(B_PY, encoding="utf-8")
    (root / "a.py").write_text(A_PY, encoding="utf-8")
    (root / "c.py").write_text(C_PY, encoding="utf-8")
    out = _run(gtools.cbm_index(project_path=str(root), scope="full"))
    assert out["ok"] is True, out
    return str(root)


def test_1c_from_symbol_remaps_to_name_and_executes(graph_project):
    body = {"project_path": graph_project, "template": "callers",
            "from_symbol": "helper"}
    m = _correction("cbm_template_query", body, ["from_symbol"])
    cp = m["corrected_payload"]
    assert cp.get("name") == "helper", m
    assert m["corrected_payload_source"] == "alias_mapped_intent_preserved", m
    out = _run(gtools.cbm_template_query(**cp))
    assert out["ok"] is True, out
    blob = json.dumps(out["result"])
    assert "worker" in blob or "main" in blob, blob


def test_1c_unknown_field_still_stripped(graph_project):
    body = {"project_path": graph_project, "template": "callers",
            "name": "helper", "totally_bogus": 1}
    m = _correction("cbm_template_query", body, ["totally_bogus"])
    assert "totally_bogus" not in m["corrected_payload"], m
    assert m["corrected_payload_source"] == "stripped_unsupported_fields", m


# ─── 2a: guidance SOT rides the served schema ───────────────────────────────

def test_2a_guidance_on_served_schema():
    for tool in ("read_file_range", "git_diff", "run_project_test_script"):
        fn = osrv._schema_for_tool(tool)
        served = osrv._prepare_tool_schema(tool, {"type": "function",
                                                  "function": fn})
        g = served["function"]["usage_guidance"]
        assert g == osrv._V11_GUIDANCE
        assert "lost intent" in g and "write_status" in g


# ─── 2b: G1..G15 table exists ───────────────────────────────────────────────

def test_2b_common_errors_table():
    f = Path(osrv.__file__).resolve().parent / "examples" / "COMMON_ERRORS.md"
    assert f.is_file(), f
    text = f.read_text(encoding="utf-8")
    for i in range(1, 16):
        assert f"G{i}" in text, f"G{i} missing"
