"""A30-P5: open-code-review integration must hold its contract without the binary.

Every test here fakes the subprocess, so the suite passes on a machine that has
never run `npm install --prefix venv_ocr`. That is deliberate: the things most
likely to rot are the ones a real-binary test would hide -- an install path that
silently falls back to an unmanaged copy on PATH, a missing `--color never` on
the second call site, and an empty capture being read as "no findings".

The fake replaces the `subprocess` reference in ocr_review's OWN globals rather
than patching the real module, so nothing else in the suite can see it.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.tools import ocr_review as mod

REPO_ROOT = Path(__file__).resolve().parents[1]


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeSubprocess:
    """Stands in for the subprocess module inside ocr_review's globals."""

    def __init__(self, stdout="", returncode=0, stderr="", raises=None):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.raises = raises
        self.calls = []

    def run(self, command, **kwargs):
        self.calls.append({"command": list(command), "kwargs": kwargs})
        if self.raises is not None:
            raise self.raises
        return _FakeCompleted(self.returncode, self.stdout, self.stderr)

    @property
    def last_command(self):
        return self.calls[-1]["command"]


def _fake_install(root: Path) -> Path:
    """Build the exact layout `npm install --prefix venv_ocr` produced (measured)."""
    bin_dir = root / "venv_ocr" / "node_modules" / "@alibaba-group" / "ocr-win32-x64" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "opencodereview.exe"
    exe.write_bytes(b"MZ not a real binary")
    return exe


def _wire(monkeypatch, tmp_path, **fake_kwargs):
    """Install a fake OCR binary and a fake subprocess. Returns (repo, fake, exe)."""
    home = tmp_path / "a_home"
    home.mkdir(exist_ok=True)
    exe = _fake_install(home)
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    fake = _FakeSubprocess(**fake_kwargs)
    monkeypatch.setattr(mod, "_a_repo_root", lambda: str(home))
    monkeypatch.setattr(mod, "subprocess", fake)
    return repo, fake, exe


def _call(handler, **kwargs) -> dict:
    return json.loads(asyncio.run(handler(**kwargs)))


# ---------------------------------------------------------------- binary resolve

def test_owned_install_wins_and_is_the_path_that_runs(monkeypatch, tmp_path):
    repo, fake, exe = _wire(monkeypatch, tmp_path, stdout='{"total_files": 4}')

    payload = _call(mod.ocr_review, project_path=str(repo))

    assert payload["ok"] is True, payload
    assert payload["binary_source"] == "venv_ocr"
    assert payload["binary"] == str(exe)
    assert fake.last_command[0] == str(exe)


def test_env_override_is_used_only_when_the_owned_install_is_absent(monkeypatch, tmp_path):
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    override = tmp_path / "elsewhere.exe"
    override.write_bytes(b"MZ")
    monkeypatch.setattr(mod, "_a_repo_root", lambda: str(empty_home))
    monkeypatch.setenv("OCR_BINARY", str(override))

    resolved = mod._ocr_binary()

    assert resolved["ok"] is True
    assert resolved["source"] == "env_OCR_BINARY"
    assert resolved["pinned_version"] is None, (
        "an override is not a pin: [A] cannot promise the version of a binary it did not install"
    )


def test_a_copy_on_path_is_reported_but_never_executed(monkeypatch, tmp_path):
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    fake = _FakeSubprocess(stdout="{}")
    monkeypatch.setattr(mod, "_a_repo_root", lambda: str(empty_home))
    monkeypatch.setattr(mod, "subprocess", fake)
    monkeypatch.delenv("OCR_BINARY", raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda _name: r"C:\unmanaged\ocr.ps1")

    payload = _call(mod.ocr_review, project_path=str(repo))

    assert payload["ok"] is False
    assert payload["error_name"] == "OCR_BINARY_NOT_FOUND"
    assert payload["unmanaged_on_path"] == r"C:\unmanaged\ocr.ps1"
    assert payload["install_command"].startswith("npm install --prefix")
    assert "venv_ocr" in payload["install_dir"]
    assert fake.calls == [], (
        "an install [A] does not own must never be executed: a .ps1 shim is exactly "
        "the shell layer that swallowed ruff's stdout under pythonw.exe"
    )


# ------------------------------------------------------------------ color / env

def test_every_tool_passes_color_never(monkeypatch, tmp_path):
    repo, fake, _exe = _wire(monkeypatch, tmp_path, stdout="{}")

    _call(mod.ocr_review, project_path=str(repo))
    _call(mod.ocr_rule, project_path=str(repo), paths=["."])
    _call(mod.ocr_scan, project_path=str(repo))

    assert len(fake.calls) == 3
    for call in fake.calls:
        command = call["command"]
        assert "--color" in command and command[command.index("--color") + 1] == "never", (
            "FORCE_COLOR=3 lives in the daemon env; a call site without --color never "
            "returns escape codes that json.loads cannot parse: %s" % command
        )


def test_force_color_is_scrubbed_from_the_child_env(monkeypatch, tmp_path):
    repo, fake, _exe = _wire(monkeypatch, tmp_path, stdout="{}")
    monkeypatch.setenv("FORCE_COLOR", "3")

    payload = _call(mod.ocr_review, project_path=str(repo))

    assert payload["force_color_scrubbed"] is True
    assert payload["color_flag"] == "never"
    assert "FORCE_COLOR" not in fake.calls[-1]["kwargs"]["env"]


def test_the_scrub_disclosure_is_derived_from_the_env_actually_passed(monkeypatch, tmp_path):
    # Found by this file: the first version of _base_meta dropped both keys, so a
    # successful call disclosed nothing about colour at all -- only _fail() did,
    # by copying the whole run dict. Same class as A24's config="isolated", which
    # stayed green after --isolated was deleted because it was a restated literal.
    repo, fake, _exe = _wire(monkeypatch, tmp_path, stdout="{}")
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    payload = _call(mod.ocr_review, project_path=str(repo))

    assert payload["force_color_scrubbed"] is False, (
        "nothing was there to scrub; claiming a scrub happened is the same lie as "
        "hiding that one did"
    )
    assert "FORCE_COLOR" not in fake.calls[-1]["kwargs"]["env"]
    assert payload["color_flag"] == "never", "--color never does not depend on the env"


# ------------------------------------------------------------- empty / bad output

def test_empty_stdout_on_success_is_an_anomaly_not_a_clean_result(monkeypatch, tmp_path):
    repo, _fake, _exe = _wire(monkeypatch, tmp_path, stdout="", returncode=0)

    payload = _call(mod.ocr_review, project_path=str(repo))

    assert payload["ok"] is False
    assert payload["error_name"] == "OCR_EMPTY_OUTPUT"
    assert payload["stdout_empty"] is True
    assert payload["stdout_bytes"] == 0


def test_nonzero_exit_is_reported_with_its_stderr(monkeypatch, tmp_path):
    repo, _fake, _exe = _wire(
        monkeypatch, tmp_path, stdout="", returncode=2, stderr="fatal: not a git repository")

    payload = _call(mod.ocr_review, project_path=str(repo))

    assert payload["ok"] is False
    assert payload["error_name"] == "OCR_EXEC_FAILED"
    assert payload["exit_code"] == 2
    assert "not a git repository" in payload["stderr"]
    assert "stdout" not in payload, "the failure payload must not re-inline raw stdout"


def test_exec_exception_becomes_ocr_exec_failed(monkeypatch, tmp_path):
    repo, _fake, _exe = _wire(
        monkeypatch, tmp_path, raises=OSError("[WinError 5] Access is denied"))

    payload = _call(mod.ocr_review, project_path=str(repo))

    assert payload["ok"] is False
    assert payload["error_name"] == "OCR_EXEC_FAILED"
    assert "OSError" in payload["error"]


def test_non_json_stdout_is_named_not_swallowed(monkeypatch, tmp_path):
    repo, _fake, _exe = _wire(monkeypatch, tmp_path, stdout="Reviewing 4 files...\n")

    payload = _call(mod.ocr_review, project_path=str(repo))

    assert payload["ok"] is False
    assert payload["error_name"] == "OCR_OUTPUT_NOT_JSON"
    assert "Reviewing 4 files" in payload["stdout_head"]


# ------------------------------------------------------------------ token gating

def test_background_is_omitted_by_default_and_returned_on_request(monkeypatch, tmp_path):
    body = json.dumps({"total_files": 4, "reviewable_count": 4,
                       "background": "commit message " + "x" * 2000})
    repo, _fake, _exe = _wire(monkeypatch, tmp_path, stdout=body)

    default = _call(mod.ocr_review, project_path=str(repo))
    assert default["background"]["omitted"] is True
    assert default["background"]["bytes"] > 2000
    assert default["total_files"] == 4

    asked = _call(mod.ocr_review, project_path=str(repo), include_background=True)
    assert asked["background"].startswith("commit message")


def test_rule_text_is_measured_by_default_and_returned_on_request(monkeypatch, tmp_path):
    rule_text = "# Review rule\n" + ("guidance line\n" * 600)
    body = json.dumps({"group_id": "python", "source": "builtin",
                       "pattern": "**/*.py", "rule": rule_text})
    repo, _fake, _exe = _wire(monkeypatch, tmp_path, stdout=body)
    (repo / "mod.py").write_text("x = 1\n", encoding="utf-8")

    default = _call(mod.ocr_rule, project_path=str(repo), paths=["mod.py"])
    assert default["rule_text_included"] is False
    assert default["rule"]["omitted"] is True
    assert default["rule"]["bytes"] > 512
    assert default["rule_bytes"] > 512
    assert default["group_id"] == "python", "small fields must survive the shrink"

    asked = _call(mod.ocr_rule, project_path=str(repo), paths=["mod.py"],
                  include_rule_text=True)
    assert asked["rule"] == rule_text


def test_delegate_mode_reports_zero_llm_and_zero_tokens(monkeypatch, tmp_path):
    repo, _fake, _exe = _wire(monkeypatch, tmp_path, stdout="{}")

    payload = _call(mod.ocr_review, project_path=str(repo))

    assert payload["llm_used"] is False
    assert payload["token_cost"] == 0


# ------------------------------------------------------------------- LLM refusal

def test_scan_without_preview_refuses_when_no_provider_is_configured(monkeypatch, tmp_path):
    repo, fake, _exe = _wire(monkeypatch, tmp_path, stdout="{}")
    empty_config = tmp_path / "cfg"
    empty_config.mkdir()
    monkeypatch.setenv("OCR_CONFIG_DIR", str(empty_config))

    payload = _call(mod.ocr_scan, project_path=str(repo), preview_only=False)

    assert payload["ok"] is False
    assert payload["error_name"] == "OCR_LLM_NOT_CONFIGURED"
    assert payload["ca_next_action"] == "use_ocr_review_delegate_mode"
    assert payload["configured"] is False
    assert payload["token_cost"] == 0
    assert fake.calls == [], "the refusal must land BEFORE the provider call is spent"


def test_scan_without_preview_proceeds_once_a_provider_config_exists(monkeypatch, tmp_path):
    repo, fake, _exe = _wire(monkeypatch, tmp_path, stdout="{}")
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "config.json").write_text('{"provider": "anthropic"}', encoding="utf-8")
    monkeypatch.setenv("OCR_CONFIG_DIR", str(config_dir))

    payload = _call(mod.ocr_scan, project_path=str(repo), preview_only=False)

    assert payload["ok"] is True, payload
    assert payload["llm_used"] is True
    assert "token_cost" not in payload, "an LLM call is not free; do not report 0"
    assert "--preview" not in fake.last_command


def test_preview_only_needs_no_config_and_passes_the_preview_flag(monkeypatch, tmp_path):
    repo, fake, _exe = _wire(monkeypatch, tmp_path, stdout="{}")
    empty_config = tmp_path / "cfg"
    empty_config.mkdir()
    monkeypatch.setenv("OCR_CONFIG_DIR", str(empty_config))

    payload = _call(mod.ocr_scan, project_path=str(repo))

    assert payload["ok"] is True, payload
    assert payload["mode"] == "scan_preview"
    assert payload["llm_used"] is False
    assert "--preview" in fake.last_command


# ------------------------------------------------------------------ input guards

def test_commit_and_ref_range_together_are_refused_before_spawning(monkeypatch, tmp_path):
    repo, fake, _exe = _wire(monkeypatch, tmp_path, stdout="{}")

    payload = _call(mod.ocr_review, project_path=str(repo), commit="abc123", from_ref="HEAD~1")

    assert payload["ok"] is False
    assert payload["error_name"] == "SCHEMA_VIOLATION"
    assert fake.calls == []


def test_paths_outside_the_project_are_refused(monkeypatch, tmp_path):
    repo, fake, _exe = _wire(monkeypatch, tmp_path, stdout="{}")

    payload = _call(mod.ocr_rule, project_path=str(repo), paths=["../escape.py"])

    assert payload["ok"] is False
    assert payload["error_name"] == "PATH_OUTSIDE_PROJECT"
    assert fake.calls == []


def test_missing_project_path_is_a_schema_violation(monkeypatch, tmp_path):
    _repo, fake, _exe = _wire(monkeypatch, tmp_path, stdout="{}")

    payload = _call(mod.ocr_review, project_path="")

    assert payload["ok"] is False
    assert payload["error_name"] == "SCHEMA_VIOLATION"
    assert fake.calls == []


def test_repo_flag_is_passed_so_the_daemon_never_changes_directory(monkeypatch, tmp_path):
    repo, fake, _exe = _wire(monkeypatch, tmp_path, stdout="{}")

    _call(mod.ocr_review, project_path=str(repo), commit="9216cb3", exclude=["docs", "*.md"])

    command = fake.last_command
    assert command[1:3] == ["delegate", "preview"]
    assert "--repo" in command
    assert "--format" in command and command[command.index("--format") + 1] == "json"
    assert command[command.index("--commit") + 1] == "9216cb3"
    assert command[command.index("--exclude") + 1] == "docs,*.md"


# ------------------------------------------------------------- finding normalize

def test_findings_normalize_onto_the_shape_ruff_findings_already_use():
    common = mod._ocr_findings_to_common([
        {"file": "a.py", "line": 12, "severity": "high", "rule": "SEC001", "message": "unsafe"},
        {"path": "b.go", "start_line": "7", "level": "low", "rule_id": "STY9",
         "title": "naming"},
        {"location": {"file": "c.ts", "line": 3}, "description": "no severity given"},
        "not a dict",
    ])

    assert [f["file"] for f in common] == ["a.py", "b.go", "c.ts"]
    assert [f["line"] for f in common] == [12, 7, 3]
    assert common[1]["rule"] == "STY9"
    assert common[1]["message"] == "naming"
    assert common[2]["severity"] == "unknown", "a missing severity must be named, not dropped"


# -------------------------------------------------------- registration / aliases

def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_ocr_registration_is_its_own_island_after_the_cbm_block():
    source = (REPO_ROOT / "code_tools.py").read_text(encoding="utf-8", errors="replace")
    compact = _compact(source)

    assert "_register_ocr_p5" in source
    cbm_except = source.index("except Exception as _cbm_p1_exc")
    ocr_try = source.index("from orchestrator.tools.ocr_review import")
    assert ocr_try > cbm_except, (
        "the OCR import must start a NEW try island; inside the CBM block a missing "
        "npm install would take every CBM graph tool offline with it"
    )
    assert "except Exception as _ocr_p5_exc" in source
    for name in ("ocr_review", "ocr_rule", "ocr_scan"):
        assert 'name="%s", handler=' % name in compact, name
    assert compact.count('_register_ocr_p5( name=') == 3


def test_ocr_arg_aliases_remap_the_cli_flag_names():
    source = (REPO_ROOT / "orchestrator_server.py").read_text(encoding="utf-8", errors="replace")

    for tool in ("ocr_review", "ocr_rule", "ocr_scan"):
        assert "_ARG_ALIASES.setdefault('%s', {})" % tool in source, tool
    line = next(ln for ln in source.splitlines()
                if "_ARG_ALIASES.setdefault('ocr_review'" in ln)
    assert "'from': 'from_ref'" in line, "`from` is a Python keyword; the handler cannot take it"
    assert "'to': 'to_ref'" in line
    assert "'repo': 'project_path'" in line
