"""A24: ruff_fix must not inherit the linted project's own ruff config.

``ruff_fix`` runs ``python -m ruff check <target> --fix``. Ruff discovers its
configuration by walking up from the TARGET (falling back to cwd), so linting a
file that lives under a project with a narrow ``select`` silently adopts that
narrow ``select``. This repository's own ``ruff.toml`` says::

    [lint]
    select = ['E9', 'F63', 'F7']   # no F401

so ``ruff_fix`` on any file inside this repo returned ``exit_code: 0`` and
``diff: ""`` -- indistinguishable from "the file is clean" -- for a file with a
genuinely unused import. Measured, same file, four combinations::

    target IN repo,  cwd=repo -> rc 0, no diff   <- the lie
    target IN repo,  cwd=out  -> rc 0, no diff   <- cwd is NOT the variable
    target OUT repo, cwd=repo -> rc 0, no diff
    target OUT repo, cwd=out  -> rc 1, diff

That matters because ``ruff_fix`` is the tool [A] hands a worker to audit [A]
itself, and because ``validate_file``'s ``ruff_fix_hint`` actively promises
``ruff_fix`` can repair F401/F841/E711/E712/E731/E401/E402/F541/RUF100 -- a
promise the narrow select silently breaks. Two tools disagreeing about the same
file is worse than either being wrong alone.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.tools import ruff_fix

# The exact narrow config that caused the incident.
NARROW_RUFF_TOML = "target-version = 'py312'\n\n[lint]\nselect = ['E9', 'F63', 'F7']\n"
UNUSED_IMPORT = "import os\nprint('ok')\n"


def _run(**kwargs) -> dict:
    return json.loads(asyncio.run(ruff_fix(**kwargs)))


def test_preview_sees_unused_import_despite_narrow_project_config(tmp_path):
    (tmp_path / "ruff.toml").write_text(NARROW_RUFF_TOML, encoding="utf-8")
    source = tmp_path / "mod.py"
    source.write_text(UNUSED_IMPORT, encoding="utf-8")

    payload = _run(project_path=str(tmp_path), path="mod.py")

    assert payload["ok"] is True, payload
    result = payload["result"]
    assert "-import os" in result["diff"], (
        "narrow project ruff.toml silenced F401; ruff_fix reported a dirty file as clean"
    )
    assert result["changed"] is True
    assert source.read_text(encoding="utf-8") == UNUSED_IMPORT, "preview must not mutate"


def test_apply_fixes_unused_import_despite_narrow_project_config(tmp_path):
    (tmp_path / "ruff.toml").write_text(NARROW_RUFF_TOML, encoding="utf-8")
    source = tmp_path / "mod.py"
    source.write_text(UNUSED_IMPORT, encoding="utf-8")

    payload = _run(project_path=str(tmp_path), path="mod.py", apply=True)

    assert payload["ok"] is True, payload
    assert payload["result"]["applied"] is True
    assert source.read_text(encoding="utf-8") == "print('ok')\n"


def test_pyproject_ruff_section_is_also_ignored(tmp_path):
    # ruff.toml is not the only discovery path -- [tool.ruff] in pyproject.toml
    # gets picked up identically, and most projects use that form.
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff.lint]\nselect = ['E9']\n", encoding="utf-8"
    )
    source = tmp_path / "mod.py"
    source.write_text(UNUSED_IMPORT, encoding="utf-8")

    payload = _run(project_path=str(tmp_path), path="mod.py")

    assert payload["ok"] is True, payload
    assert "-import os" in payload["result"]["diff"], payload["result"]


def test_response_states_which_rules_were_used(tmp_path):
    # Silently substituting a rule set is how A24 stayed hidden. If ruff_fix
    # overrides the project's config it has to say so in the response.
    source = tmp_path / "mod.py"
    source.write_text(UNUSED_IMPORT, encoding="utf-8")

    result = _run(project_path=str(tmp_path), path="mod.py")["result"]

    assert result.get("config"), f"response does not disclose its lint config: {result}"
    assert result.get("select"), f"response does not disclose its rule set: {result}"


def test_disclosure_tracks_the_command_actually_run(tmp_path, monkeypatch):
    # Found by sabotaging the fix: with --isolated removed the response still
    # said config="isolated" because the string was restated, not derived. A
    # disclosure that cannot go stale is the only kind worth printing.
    from orchestrator.graph import tools as graph_tools

    seen: list[list[str]] = []
    real_run = graph_tools.subprocess.run

    def spy(command, *args, **kwargs):
        seen.append(list(command))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(graph_tools.subprocess, "run", spy)

    source = tmp_path / "mod.py"
    source.write_text(UNUSED_IMPORT, encoding="utf-8")
    result = _run(project_path=str(tmp_path), path="mod.py")["result"]

    assert seen, "ruff was never invoked"
    command = seen[-1]
    assert (result["config"] == "isolated") == ("--isolated" in command), (
        f"config disclosure disagrees with the command: {result['config']} vs {command}"
    )
    assert result["select"] == command[command.index("--select") + 1]


def test_rules_cover_what_validate_file_promises(tmp_path):
    # validate_file's ruff_fix_hint tells the caller these rules are fixable by
    # ruff_fix. If ruff_fix's own select does not contain them, the hint lies.
    # (#7: the policy rules are selected only where the project enables them --
    # pinned by the #7 tests at the bottom of this file.)
    import code_tools

    source = tmp_path / "mod.py"
    source.write_text(UNUSED_IMPORT, encoding="utf-8")
    select = _run(project_path=str(tmp_path), path="mod.py")["result"]["select"]
    selected = {token.strip() for token in str(select).split(",")}

    promised = code_tools._RUFF_DEFECT_FIX_RULES
    for rule in promised:
        # A prefix like "F" or "E" legitimately covers "F401"/"E711".
        covered = any(rule.startswith(token) for token in selected if token)
        assert covered, f"validate_file promises {rule} but ruff_fix does not select it"
    assert hasattr(code_tools, "_py_error_ruff_hint"), "hint helper moved; update this test"


FIRST_PARTY_RUFF_TOML = (
    "[lint]\nselect = ['E9', 'I']\n\n[lint.isort]\nknown-first-party = ['acme_shared']\n"
)
# Already sorted the way that config wants: third-party block, blank line, first-party.
ALREADY_SORTED = "import requests\n\nimport acme_shared\n\nprint(acme_shared, requests)\n"


def test_apply_does_not_rewrite_imports_that_the_project_config_calls_correct(tmp_path):
    # Why ruff_fix does NOT use --isolated, even though --isolated is the stronger
    # override. --isolated discards [lint.isort] as well, so `acme_shared` -- an
    # installed internal package ruff cannot infer from the filesystem -- gets
    # classified third-party and merged into the first block. Measured:
    #   after --isolated apply : import acme_shared / import requests   <- WRONG
    #   after plain --select   : unchanged                              <- correct
    # ruff_fix WRITES that. Missing a finding is a missed hint; rewriting a correct
    # file into a wrong one is damage.
    (tmp_path / "ruff.toml").write_text(FIRST_PARTY_RUFF_TOML, encoding="utf-8")
    source = tmp_path / "mod.py"
    source.write_text(ALREADY_SORTED, encoding="utf-8")

    result = _run(project_path=str(tmp_path), path="mod.py", apply=True)["result"]

    # #7: isort only runs where the project enables it, and this config does --
    # without that, the assertion below would pass with isort never having run.
    assert any(t.startswith("I0") for t in str(result["select"]).split(",")), result

    assert source.read_text(encoding="utf-8") == ALREADY_SORTED, (
        "ruff_fix reordered imports the project's own isort config calls correct; "
        "this is what --isolated does"
    )


def test_explicit_select_alone_beats_every_hostile_config_shape(tmp_path):
    # The narrow `select` was only one way a project config can silence a rule.
    # Each of these was measured to defeat a bare `ruff check --fix`, and each is
    # beaten by the explicit --select -- which is why --isolated is not needed.
    shapes = {
        "narrow_select": "[lint]\nselect = ['E9']\n",
        "unfixable": "[lint]\nselect = ['E9']\nunfixable = ['F401']\n",
        "ignore": "[lint]\nselect = ['E9']\nignore = ['F401']\n",
        "exclude": "exclude = ['mod.py']\n[lint]\nselect = ['E9']\n",
        "per_file_ignores": (
            "[lint]\nselect = ['E9']\n[lint.per-file-ignores]\n'mod.py' = ['F401']\n"
        ),
    }
    for name, config in shapes.items():
        project = tmp_path / name
        project.mkdir()
        (project / "ruff.toml").write_text(config, encoding="utf-8")
        (project / "mod.py").write_text(UNUSED_IMPORT, encoding="utf-8")

        result = _run(project_path=str(project), path="mod.py")["result"]

        assert "-import os" in result["diff"], f"{name} config silenced F401: {result}"


def test_uses_a_console_python_so_output_survives_under_pythonw(tmp_path, monkeypatch):
    # A24b, found by the live real test after restart: over HTTP the daemon runs
    # pythonw.exe (GUI subsystem, no stdout handle), so a console child like
    # ruff.exe has nothing to inherit. Live result was exit_code 1 with BOTH
    # stdout and stderr empty -- ruff found the error and the answer vanished.
    # Same class as B8. scan_ruff already solved this with _console_python();
    # ruff_fix passed a bare sys.executable.
    from orchestrator.graph import tools as graph_tools
    from orchestrator.graph.extractors import vulture_ruff

    seen: list[list[str]] = []
    real_run = graph_tools.subprocess.run

    def spy(command, *args, **kwargs):
        seen.append(list(command))
        return real_run(command, *args, **kwargs)

    # pytest itself runs under python.exe, so sys.executable must be forced to
    # pythonw.exe -- otherwise this test is green for the wrong reason.
    # Patch vulture_ruff.sys: that is the module _console_python() reads. (It is
    # the same singleton `sys` object either way, but naming the real reader is
    # what keeps this test honest if tools.py stops importing sys.)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        import pytest

        pytest.skip("no pythonw.exe next to this interpreter")
    monkeypatch.setattr(vulture_ruff.sys, "executable", str(pythonw))
    monkeypatch.setattr(graph_tools.subprocess, "run", spy)
    source = tmp_path / "mod.py"
    source.write_text(UNUSED_IMPORT, encoding="utf-8")

    result = _run(project_path=str(tmp_path), path="mod.py")["result"]

    assert seen, "ruff was never invoked"
    interpreter = Path(seen[-1][0]).name.lower()
    assert interpreter != "pythonw.exe", (
        "ruff_fix spawns ruff from pythonw.exe; the child cannot inherit stdout "
        "and every finding is silently lost"
    )
    assert "-import os" in result["diff"], (
        f"output vanished under pythonw.exe: {result}"
    )


def test_live_daemon_reports_a_finding_it_can_actually_show(tmp_path):
    # Guards the exact shape the live test caught: a non-zero exit code paired
    # with empty output is a lie either way -- either report the finding or say
    # the lint result is unknown. Never both "something is wrong" and "".
    source = tmp_path / "mod.py"
    source.write_text(UNUSED_IMPORT, encoding="utf-8")

    result = _run(project_path=str(tmp_path), path="mod.py")["result"]

    if result["exit_code"] not in (0,):
        assert result["diff"] or result["stdout"] or result["stderr"], (
            f"exit_code={result['exit_code']} but no output at all: {result}"
        )


def test_clean_file_is_still_reported_clean(tmp_path):
    # Widening the rule set must not turn every file into a finding.
    source = tmp_path / "clean.py"
    source.write_text("print('ok')\n", encoding="utf-8")

    result = _run(project_path=str(tmp_path), path="clean.py", apply=True)["result"]

    assert result["changed"] is False
    assert source.read_text(encoding="utf-8") == "print('ok')\n"


# --- A30-P4: ruff 0.16.7 -----------------------------------------------------
# 0.16.7 carries a known upstream PANIC in `ruff format --check` on a file that
# combines a UTF-8 BOM with mixed line endings ("Annotation range 0..429 is
# beyond the end of buffer 427"). The upgrade was judged safe because [A] never
# runs that subcommand -- true when said, and worth nothing unless something
# fails the day it stops being true. These two are that something.


def test_ruff_fix_only_ever_runs_the_check_subcommand(tmp_path, monkeypatch):
    from orchestrator.graph import tools as graph_tools

    seen: list[list[str]] = []
    real_run = graph_tools.subprocess.run

    def spy(command, *args, **kwargs):
        seen.append(list(command))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(graph_tools.subprocess, "run", spy)
    (tmp_path / "mod.py").write_text(UNUSED_IMPORT, encoding="utf-8")

    _run(project_path=str(tmp_path), path="mod.py", apply=True)

    assert seen, "ruff was never invoked"
    for command in seen:
        assert "check" in command, f"not a check invocation: {command}"
        assert "format" not in command, (
            f"ruff format reached a subprocess: {command} -- 0.16.7 panics on "
            "BOM + mixed EOL, so this is not a style preference"
        )


def test_no_validate_or_scan_call_site_spells_ruff_format():
    # The behavioural test above only covers ruff_fix. validate_file and
    # scan_ruff build their own commands, and B029a is the precedent: the A24
    # survey enumerated one call site and the second one went unfixed for a
    # whole round. So pin the source text of all three. Whitespace is stripped
    # first because this tree's formatting puts spaces in odd places.
    root = Path(__file__).resolve().parents[1]
    targets = (
        root / "code_tools.py",
        root / "orchestrator" / "graph" / "tools.py",
        root / "orchestrator" / "graph" / "extractors" / "vulture_ruff.py",
    )
    forbidden = ('"ruff","format"', "'ruff','format'", "-mruffformat")
    for target in targets:
        assert target.is_file(), f"moved or renamed: {target}"
        compact = "".join(target.read_text(encoding="utf-8").split())
        for spelling in forbidden:
            assert spelling not in compact, (
                f"{target.name} now builds a ruff format command: {spelling!r}"
            )



# --- #7: the POLICY rules follow the project ---------------------------------
# RUF100 and I used to sit in the --select, and --select REPLACES the project's
# rule set, so every preview imposed them: on this repo, whose config enables
# neither, a preview of orchestrator/graph/tools.py with 0 defect findings
# proposed 15 hunks of import and noqa rewrites (measured 2026-09-25).
UNSORTED_WITH_STALE_NOQA = "import sys\nimport os  # noqa: E501\n\nprint(os, sys)\n"
POLICY_FIXED = "import os\nimport sys\n\nprint(os, sys)\n"


def _selected(result: dict) -> list[str]:
    return [token.strip() for token in str(result["select"]).split(",") if token.strip()]


def test_a_project_without_the_policy_rules_gets_no_policy_rewrite(tmp_path):
    (tmp_path / "ruff.toml").write_text("[lint]\nselect = ['E9']\n", encoding="utf-8")
    (tmp_path / "mod.py").write_text(UNSORTED_WITH_STALE_NOQA, encoding="utf-8")

    result = _run(project_path=str(tmp_path), path="mod.py")["result"]

    assert result["diff"] == "" and result["changed"] is False, result
    selected = _selected(result)
    assert "RUF100" not in selected and not any(t.startswith("I") for t in selected), (
        selected
    )
    assert "policy_rules_unresolved" not in result, result


def test_a_project_that_enables_the_policy_rules_still_gets_them_fixed(tmp_path):
    # Positive control for the test above, in both config shapes: the rewrite is
    # real, and ruff_fix still performs it wherever the project asks for it.
    shapes = {
        "ruff_toml": ("ruff.toml", "[lint]\nselect = ['E9', 'I', 'RUF100']\n"),
        "pyproject_extend": (
            "pyproject.toml", "[tool.ruff.lint]\nextend-select = ['I', 'RUF100']\n"
        ),
    }
    for name, (config_name, config) in shapes.items():
        project = tmp_path / name
        project.mkdir()
        (project / config_name).write_text(config, encoding="utf-8")
        source = project / "mod.py"
        source.write_text(UNSORTED_WITH_STALE_NOQA, encoding="utf-8")

        result = _run(project_path=str(project), path="mod.py", apply=True)["result"]

        assert source.read_text(encoding="utf-8") == POLICY_FIXED, (name, result)
        selected = _selected(result)
        assert "RUF100" in selected and any(t.startswith("I0") for t in selected), (
            name, selected
        )
        assert "policy_rules_unresolved" not in result, (name, result)


def _policy_from(monkeypatch, stdout: str, returncode: int = 0):
    import subprocess

    from orchestrator.graph import tools as graph_tools

    def fake(command, *args, **kwargs):
        return subprocess.CompletedProcess(command, returncode, stdout, "")

    monkeypatch.setattr(graph_tools.subprocess, "run", fake)
    return graph_tools._project_policy_rules("mod.py", ".")


def test_policy_parser_reads_the_enabled_block_and_nothing_else(monkeypatch):
    enabled = (
        "linter.rules.enabled = [\n\tunsorted-imports (I001),\n\tunused-noqa (RUF100),\n"
        "\tmissing-required-import (I002),\n\tunused-import (F401),\n]\n"
    )
    assert _policy_from(monkeypatch, "linter.x = 1\n" + enabled) == (
        ["I001", "RUF100", "I002"], ""
    )
    # A code that merely STARTS with I belongs to another plugin.
    lookalikes = (
        "linter.rules.enabled = [\n\tf-string-in-get-text-func-call (INT001),\n"
        "\tsingle-line-implicit-string-concatenation (ISC001),\n]\n"
    )
    assert _policy_from(monkeypatch, lookalikes) == ([], "")
    # An empty set is an answer; a LATER block listing I001 is not the enabled set.
    empty_then_other = (
        "linter.rules.enabled = []\nlinter.rules.should_fix = [\n"
        "\tunsorted-imports (I001),\n]\n"
    )
    assert _policy_from(monkeypatch, empty_then_other) == ([], "")


def test_policy_parser_gives_a_reason_whenever_it_cannot_answer(monkeypatch):
    # ([], "") means "the project enables no policy rule" -- a claim none of these
    # inputs can back, so each must come back with a reason instead.
    cases = {
        "no_block": ("linter.rules.other = []\n", 0),
        "never_closed": ("linter.rules.enabled = [\n\tunsorted-imports (I001),\n", 0),
        "ruff_failed": ("ruff failed\n  Cause: bad toml\n", 2),
    }
    for name, (stdout, returncode) in cases.items():
        codes, reason = _policy_from(monkeypatch, stdout, returncode)
        assert codes == [] and reason, (name, codes, reason)

    from orchestrator.graph import tools as graph_tools

    def boom(command, *args, **kwargs):
        raise graph_tools.subprocess.TimeoutExpired(command, 30)

    monkeypatch.setattr(graph_tools.subprocess, "run", boom)
    codes, reason = graph_tools._project_policy_rules("mod.py", ".")
    assert codes == [] and "TimeoutExpired" in reason, (codes, reason)


def test_unreadable_policy_falls_back_to_the_defect_rules_and_says_so(tmp_path, monkeypatch):
    from orchestrator.graph import tools as graph_tools

    real_run = graph_tools.subprocess.run

    def broken_show_settings(command, *args, **kwargs):
        if "--show-settings" in command:
            return graph_tools.subprocess.CompletedProcess(command, 2, "", "ruff failed")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(graph_tools.subprocess, "run", broken_show_settings)
    (tmp_path / "ruff.toml").write_text("[lint]\nselect = ['E9', 'I']\n", encoding="utf-8")
    (tmp_path / "mod.py").write_text(UNUSED_IMPORT, encoding="utf-8")

    result = _run(project_path=str(tmp_path), path="mod.py")["result"]

    assert result["select"] == graph_tools._RUFF_FIX_SELECT, result
    assert "-import os" in result["diff"], f"the defect rules must still run: {result}"
    assert "exited 2" in result.get("policy_rules_unresolved", ""), result
