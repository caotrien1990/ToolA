"""A35: three CA-facing gaps from the 2026-09-19 order, one group of tests each.

(a) run_project_test_script(runtime="python") set only PYTHONPATH on the child,
    while the powershell branch got a UTF-8 BOM plus a chcp 65001 prelude. Measured
    live on generation d2190b5ed454: the child ran with sys.stdout.encoding='cp1252'
    and utf8_mode=0, so a Vietnamese literal did not arrive as mojibake -- it raised
    UnicodeEncodeError INSIDE the script and killed it. The same function's sibling
    in orchestrator_server._council_run_agent had set both vars all along.
    Gap #3 (the ~180-195s MCP stdio ceiling) is a TRANSPORT limit and stays by
    design; what was missing was any disclosure of it, so a 600s request now says so.
    Gap #2 (destructive refuse) needed nothing: the refusal already names the exact
    confirm_destructive value, and the footgun branch already says it cannot be
    confirmed at all -- measured in _destructive_script_refusal, not assumed.

(b) The graph resolves a call edge only when the receiver is an imported module or
    alias. self.fn() / obj.fn() / cfg.helper() stay ext:<name> and create NO inbound
    'calls' edge, so `callers` answering count=0 -- and cbm_dead_code listing a
    symbol -- proves "no RESOLVED reference", never "unused". The resolver is
    deliberately narrow and is NOT changed here; only the disclosure is added.

(c) git_status.staged_hash digests the WHOLE index (git ls-files --stage), not the
    staged diff vs HEAD. A commit leaves index CONTENT byte-identical and only moves
    HEAD, so the hash is the same before and after -- which is why a commit retry
    after a client timeout passes the optimistic lock and then dies at NOTHING_STAGED
    looking like state confusion. test_a35_staged_hash_is_identical_across_a_commit
    measures that equality rather than trusting the new prose.

Run:  ./venv312/Scripts/python.exe -m pytest tests/test_a35_ca_hint_gaps.py -v
"""
import asyncio
import json
import os
import subprocess
import sys

import pytest

import code_tools
from orchestrator import git_tools
from orchestrator.graph import query as gquery
from orchestrator.graph import tools as gtools

_ROOT = os.path.dirname(os.path.abspath(code_tools.__file__))

# U+1EBF, the exact character that raised UnicodeEncodeError under cp1252.
_VI = "ế"
_PROBE = (
    "import sys\n"
    "sys.stdout.write('ENC=%s UTF8MODE=%s CHAR=\\u1ebf\\n'"
    " % (sys.stdout.encoding, sys.flags.utf8_mode))\n"
)


def _run_probe(**kw):
    return json.loads(asyncio.run(code_tools.run_project_test_script(
        _ROOT, _PROBE, runtime="python", script_label="a35_probe", **kw)))


def _restore_env(name, prior):
    """Restore an env var, never pop it: popping an INHERITED value silently
    changes what every later test in this process sees, which is how an env
    assertion turns order-dependent. Measured 2026-09-20."""
    if prior is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = prior

# ── (a) the python child's stdout codec ──────────────────────────────────────

def test_a35_python_child_encodes_vietnamese_instead_of_dying():
    """The measurement that proved the gap, now the regression test.

    Asserting on stdout content, not on the env dict: the old failure mode was a
    non-zero exit with UnicodeEncodeError in stderr, which an env-only assertion
    would have happily passed.
    """
    out = _run_probe(timeout=60)
    assert out["ok"] is True, out
    assert out["exit_code"] == 0, out.get("stderr")
    assert "UnicodeEncodeError" not in (out.get("stderr") or "")
    assert _VI in out["stdout"], out["stdout"]
    assert "enc=utf-8" in out["stdout"].lower(), out["stdout"]
    assert "utf8mode=1" in out["stdout"].lower(), out["stdout"]


def test_a35_child_encoding_is_disclosed_not_silent():
    """A CA must be able to tell a forced codec from a lucky one.

    BOTH vars are set with setdefault (code_tools.py:8720-8721), so the PARENT
    wins whenever it already exports one -- that is the documented contract,
    and the two tests below pin both of its halves. This test therefore reads
    the parent env instead of hardcoding "utf-8": measured 2026-09-20, a shell
    exporting PYTHONIOENCODING=utf-8:surrogateescape made the hardcoded form
    fail while the child was obeying the contract exactly -- a false defect
    report costing a full suite round to disprove.
    """
    out = _run_probe(timeout=60)
    note = out.get("child_encoding")
    assert isinstance(note, dict), out.keys()
    want_ioenc = os.environ.get("PYTHONIOENCODING") or "utf-8"
    want_utf8 = os.environ.get("PYTHONUTF8") or "1"
    assert note["PYTHONIOENCODING"].lower() == want_ioenc.lower(), (want_ioenc, note)
    assert note["PYTHONUTF8"] == want_utf8, (want_utf8, note)
    assert "UnicodeEncodeError" in note["why"]
    assert "PYTHONIOENCODING" in note["override"]


def test_a35_forced_codec_uses_setdefault_so_a_caller_can_still_win():
    """setdefault, not assignment: an [A]-level env var must keep priority."""
    prior = os.environ.get("PYTHONIOENCODING")
    os.environ["PYTHONIOENCODING"] = "latin-1"
    try:
        out = _run_probe(timeout=60)
        assert out["child_encoding"]["PYTHONIOENCODING"] == "latin-1", out["child_encoding"]
    finally:
        _restore_env("PYTHONIOENCODING", prior)


def test_a35_forced_codec_defaults_to_utf8_when_the_parent_exports_nothing():
    """The other half of the same contract, pinned independently of the shell.

    The test above pins "an exported codec wins"; this one pins "parent silent
    -> utf-8" by clearing the var first. With only one of the two, a run whose
    shell happened to export a codec could not tell a working default from a
    missing one.
    """
    prior = os.environ.get("PYTHONIOENCODING")
    os.environ.pop("PYTHONIOENCODING", None)
    try:
        out = _run_probe(timeout=60)
        assert out["child_encoding"]["PYTHONIOENCODING"].lower() == "utf-8", out["child_encoding"]
    finally:
        _restore_env("PYTHONIOENCODING", prior)


# ── (a) gap #3: the transport ceiling is disclosed, not enforced ─────────────

def test_a35_transport_ceiling_note_fires_only_above_the_wall():
    """A pair, so the test cannot pass by emitting the note unconditionally."""
    quiet = _run_probe(timeout=120)
    assert "transport_ceiling_note" not in quiet, quiet.get("transport_ceiling_note")

    loud = _run_probe(timeout=600)
    note = loud.get("transport_ceiling_note") or ""
    assert "180-195s" in note, note
    assert "/api/call/run_project_test_script" in note, note
    # The child still ran: the ceiling is disclosed, never enforced by refusing.
    assert loud["exit_code"] == 0, loud.get("stderr")


def test_a35_destructive_refusal_already_named_its_own_workaround():
    """Gap #2 needed no change -- pinned so a later 'improvement' cannot claim it did.

    A footgun is NOT confirmable (there is no count worth acknowledging), while a
    plain destructive body is, and says with which exact value.
    """
    scan = {"destructive": True, "count": 2, "findings": ["rm -rf a", "del b"],
            "footguns": [], "warnings": [], "prose_matches": []}
    ref = code_tools._destructive_refusal(
        scan, dry_run=False, confirm_destructive=-1, surface="script")
    assert ref["error_name"] == "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED"
    assert ref["confirm_destructive_required"] == 2
    assert "confirm_destructive=2" in ref["confirm_hint"]

    scan_fg = dict(scan, footguns=["Remove-Item -Include"])
    ref_fg = code_tools._destructive_refusal(
        scan_fg, dry_run=False, confirm_destructive=1, surface="script")
    assert ref_fg["error_name"] == "DESTRUCTIVE_SCRIPT_FOOTGUN"
    assert ref_fg["retryable"] is False
    assert "cannot be confirmed" in ref_fg["error"]


# ── (b) the graph's unresolved-receiver blind spot ───────────────────────────

class _FakeCur:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else (0,)


class _FakeConn:
    """Answers every SELECT with nothing and every COUNT(*) with 0.

    That is the exact shape of the bug being disclosed: a method called only via
    self./instance receivers has no inbound 'calls' edge, so the real database
    answers zero rows too.
    """

    def execute(self, sql, params=()):
        return _FakeCur([(0,)] if "COUNT(*)" in sql else [])

    def close(self):
        pass


def _patch_empty_graph(monkeypatch):
    monkeypatch.setattr(gquery, "graph_enabled", lambda *a, **k: True)
    monkeypatch.setattr(gquery._store, "get_db", lambda *a, **k: _FakeConn())
    monkeypatch.setattr(gquery, "_resolve_node",
                        lambda *a, **k: {"id": "n1", "name": "fn"})


@pytest.mark.parametrize("template", ["callers", "callees"])
def test_a35_empty_result_states_it_is_not_proof_of_unused(monkeypatch, template):
    _patch_empty_graph(monkeypatch)
    p = gquery.template_query(_ROOT, template, name="fn")
    assert p["error"] is None, p["error"]
    assert p["count"] == 0
    lim = p["resolution_limits"]
    assert "self.fn()" in lim["not_resolved"]
    assert "never 'unused'" in lim["means"]
    # The cross-check must name a tool AND its real parameters: search_code takes
    # query=, not symbol=, and a hint that guesses is the trap being closed.
    assert "search_code(query=" in lim["cross_check"]
    assert "mode='callers'" in lim["cross_check"]
    assert lim["applies_to_template"] == template
    assert p["ca_next_action"] == (
        "cross_check_with_search_code_mode_callers_before_calling_it_unused")


def test_a35_no_disclosure_is_invented_when_the_query_failed(monkeypatch):
    """An error payload must not carry a limits note about results it never read."""
    monkeypatch.setattr(gquery, "graph_enabled", lambda *a, **k: True)

    def _boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(gquery._store, "get_db", _boom)
    p = gquery.template_query(_ROOT, "callers", name="fn")
    assert p["error"] and "db gone" in p["error"]
    assert "resolution_limits" not in p
    assert "ca_next_action" not in p


def _call_dead_code(monkeypatch, dead):
    monkeypatch.setattr(gtools, "_ensure_graph_fresh",
                        lambda *a, **k: asyncio.sleep(0, result={"reason": "fresh"}))
    monkeypatch.setattr(gtools, "_freshness_refusal", lambda *a, **k: None)
    monkeypatch.setattr(gtools, "_cbm_dead_code_dispatch",
                        lambda *a, **k: {"ok": True, "dead": list(dead)})
    return json.loads(asyncio.run(gtools.cbm_dead_code(project_path=_ROOT)))


def test_a35_dead_code_no_longer_points_straight_at_write_fix(monkeypatch):
    """The old hint was a bare 'write_fix available to remove dead code entries'."""
    out = _call_dead_code(monkeypatch, [{"name": "fn", "file_path": "a.py"}])
    res = out.get("result", out)
    assert "advisory" in res["ca_chain_hint"].lower()
    assert "search_code(mode='callers')" in res["ca_chain_hint"]
    # CORE_RULE documents cbm_dead_code -> ca_chain_next write_fix; the chain is
    # kept and the PRECONDITION is what gets stated.
    assert res["ca_chain_next"] == "write_fix"
    assert "search_code(mode='callers')" in res["ca_chain_precondition"]
    assert "NOT proof of unused" in res["resolution_limits"]["means"]


def test_a35_dead_code_discloses_limits_even_with_nothing_to_delete(monkeypatch):
    out = _call_dead_code(monkeypatch, [])
    res = out.get("result", out)
    assert "resolution_limits" in res
    assert "ca_chain_precondition" not in res
    assert "ca_chain_next" not in res


# ── (c) the staged_hash domain ───────────────────────────────────────────────

def test_a35_git_status_names_the_hash_domain():
    s = git_tools.status(_ROOT)
    sem = s["staged_hash_semantics"]
    assert "WHOLE index" in sem
    assert "does NOT prove" in sem
    assert "counts.staged" in sem


def _tmp_repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    for args in (['init', '-q'], ['config', 'user.name', 'a35'],
                 ['config', 'user.email', 'a35@local'],
                 ['config', 'commit.gpgsign', 'false']):
        subprocess.run(['git', '-C', str(r), *args], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (r / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(['git', '-C', str(r), 'add', 'seed.txt'], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(['git', '-C', str(r), 'commit', '-q', '-m', 'seed'], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return r


def test_a35_staged_hash_is_identical_across_a_commit(tmp_path):
    """The anomaly, measured: same staged_hash at staged=1 and staged=0.

    This is the fact the new prose claims, so it is asserted rather than trusted.
    A commit rewrites no index entry -- same blobs, same modes -- it only moves
    HEAD, so the digest of `git ls-files --stage` cannot change.
    """
    r = _tmp_repo(tmp_path)
    (r / "seed.txt").write_text("seed\nmore\n", encoding="utf-8")
    subprocess.run(['git', '-C', str(r), 'add', 'seed.txt'], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    before = git_tools.status(str(r))
    assert before["counts"]["staged"] == 1
    subprocess.run(['git', '-C', str(r), 'commit', '-q', '-m', 'second'], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    after = git_tools.status(str(r))

    assert after["counts"]["staged"] == 0
    assert after["staged_hash"] == before["staged_hash"]   # the anomaly, by design
    assert after["head"] != before["head"]                 # what actually moved


def test_a35_nothing_staged_explains_the_already_committed_case(tmp_path):
    r = _tmp_repo(tmp_path)
    with pytest.raises(git_tools.GitToolError) as ei:
        git_tools.commit(str(r), "seed", "auto")
    err = ei.value
    assert err.error_name == "NOTHING_STAGED"
    x = err.extra
    assert x["staged_count"] == 0
    assert len(x["head"]) == 40, x["head"]
    assert x["head_subject"] == "seed"
    assert "already landed" in x["why"]
    assert "head_subject" in x["ca_next_action"]
    # state_hash is offered because it is the field that DOES discriminate.
    assert x["state_hash"]


# ── #31: a python child's environment must not leak into its descendants ─────
#
# The child used to be `python <script>` with PYTHONPATH=<root> EXPORTED, so the
# export reached every grandchild, and a PYTHONHOME the child's own launcher set
# (the ToolQ uv launcher does) killed a grandchild of another version with
# `SRE module mismatch`. code_tools._PY_CHILD_BOOT now starts the script instead.
# Tests below read what the SCRIPT sees, never the env dict [A] builds.

_BOOT_PROBE = (
    "from __future__ import annotations\n"
    "import json, os, sys\n"
    "import code_tools\n"
    "def _f(x: NotDefinedAnywhere) -> None: ...\n"
    "print('A31=' + json.dumps({'name': __name__, 'file': __file__, 'argv': sys.argv,\n"
    "    'path': sys.path[:2], 'pp': os.environ.get('PYTHONPATH'),\n"
    "    'ann': _f.__annotations__}))\n"
)


def _a31_payload(stdout):
    line = next(ln for ln in stdout.splitlines() if ln.startswith("A31="))
    return json.loads(line[len("A31="):])


def _norm(p):
    return os.path.normcase(os.path.abspath(p))


def test_a31_script_sees_what_python_script_gives_it():
    """The bootstrap must be invisible to the script: __main__, __file__, argv,
    sys.path[0] = its own dir, <root> importable, and a __future__ import still
    honoured (a bare exec of the text would have NameError'd on the annotation).
    `import code_tools` is the <root> check: no env var carries <root> any more.
    """
    out = json.loads(asyncio.run(code_tools.run_project_test_script(
        _ROOT, _BOOT_PROBE, runtime="python", script_label="a31_fidelity", timeout=60)))
    assert out["ok"] is True and out["exit_code"] == 0, out.get("stderr")
    got = _a31_payload(out["stdout"])
    script = _norm(out["script_path"])
    assert got["name"] == "__main__", got
    assert _norm(got["file"]) == script, got
    assert [_norm(a) for a in got["argv"]] == [script], got
    assert _norm(got["path"][0]) == _norm(os.path.dirname(script)), got
    assert _norm(got["path"][1]) == _norm(_ROOT), got
    assert got["ann"] == {"x": "NotDefinedAnywhere", "return": "None"}, got


def test_a31_root_is_no_longer_exported_through_the_environment(tmp_path):
    """The caller's PYTHONPATH reaches the script unchanged -- [A] prepends
    nothing to it, so nothing of [A]'s reaches a grandchild through it."""
    prior = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = str(tmp_path)
    try:
        out = json.loads(asyncio.run(code_tools.run_project_test_script(
            _ROOT, _BOOT_PROBE, runtime="python", script_label="a31_pythonpath", timeout=60)))
    finally:
        _restore_env("PYTHONPATH", prior)
    assert out["ok"] is True and out["exit_code"] == 0, out.get("stderr")
    assert _a31_payload(out["stdout"])["pp"] == str(tmp_path)


_HOME_PROBE = (
    "import json, os, subprocess, sys\n"
    "g = subprocess.run([sys.executable, '-c',\n"
    "    'import os; print(os.environ.get(\"PYTHONHOME\"))'],\n"
    "    capture_output=True, text=True, timeout=60)\n"
    "print('A31=' + json.dumps({'home': os.environ.get('PYTHONHOME'),\n"
    "    'g_rc': g.returncode, 'g_home': g.stdout.strip(), 'path': sys.path[:1],\n"
    "    'safe': bool(getattr(sys.flags, 'safe_path', False))}))\n"
)


def _run_home_probe(tmp_path, *, boot, **env):
    script = tmp_path / "a31_home_probe.py"
    script.write_text(_HOME_PROBE, encoding="utf-8")
    argv = ([sys.executable, "-c", code_tools._PY_CHILD_BOOT, _ROOT, str(script)]
            if boot else [sys.executable, str(script)])
    p = subprocess.run(argv, capture_output=True, text=True, timeout=120,
                       env={**os.environ, **env})
    assert p.returncode == 0, p.stderr
    return _a31_payload(p.stdout), script


def test_a31_bootstrap_drops_pythonhome_for_the_script_and_its_children(tmp_path):
    """Run the bootstrap directly with PYTHONHOME in the child's startup env --
    the state a uv launcher leaves behind. Same-version sys.base_prefix so the
    child itself can start; what matters is that nothing BELOW it inherits it.

    Positive control first: without the bootstrap the same env DOES reach the
    script, so a green result below is not an env that never arrived.
    """
    home = sys.base_prefix
    ctrl, _ = _run_home_probe(tmp_path, boot=False, PYTHONHOME=home)
    assert _norm(ctrl["home"]) == _norm(home), ctrl
    got, _ = _run_home_probe(tmp_path, boot=True, PYTHONHOME=home)
    assert got["home"] is None, got
    assert got["g_rc"] == 0 and got["g_home"] == "None", got


def test_a31_bootstrap_honours_safe_path(tmp_path):
    """-P / PYTHONSAFEPATH: `python <script>` would NOT put the script's dir on
    sys.path, so the bootstrap must not either -- and must not overwrite the
    first real entry, which is what an unconditional sys.path[0:1] would do."""
    got, _ = _run_home_probe(tmp_path, boot=True, PYTHONSAFEPATH="1")
    assert got["safe"] is True, got
    assert _norm(got["path"][0]) == _norm(_ROOT), got


def test_f3_bootstrap_runs_the_text_that_was_scanned_not_a_coding_cookie(tmp_path):
    """F3, review of #32: the bootstrap compiled the script's BYTES, which honours a
    PEP 263 cookie. Under `# -*- coding: utf-7 -*-` the line `+AHM-hutil.+AHI-mtree(p)`
    runs as shutil.rmtree(p), while _scan_destructive_script reads the utf-8 text [A]
    wrote and counts 0. The child must run that same text, so the cookie is inert.

    Positive control first: plain `python <script>` DOES decode the cookie, so a
    green result below is not a body that could never have run."""
    script = tmp_path / "f3_utf7.py"
    script.write_text("# -*- coding: utf-7 -*-\nimport +AHM-ys\nprint('F3-UTF7-RAN')\n",
                      encoding="utf-8")
    assert code_tools._scan_destructive_script(script.read_text(encoding="utf-8"),
                                               "python")["count"] == 0
    env = {**os.environ}
    env.pop("PYTHONHOME", None)
    ctrl = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          timeout=120, env=env)
    assert "F3-UTF7-RAN" in ctrl.stdout, ctrl.stderr
    got = subprocess.run([sys.executable, "-c", code_tools._PY_CHILD_BOOT, _ROOT, str(script)],
                         capture_output=True, text=True, timeout=120, env=env)
    assert "F3-UTF7-RAN" not in got.stdout and got.returncode != 0, (got.stdout, got.stderr)
    assert "SyntaxError" in got.stderr, got.stderr
