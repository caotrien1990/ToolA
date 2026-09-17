"""A32-1.1: ocr_rule compact -- the checklist may stand in for the rule body only
where that substitution is honest.

`delegate rule` returns a 7598-byte body for one .py file (measured live). A CA that
only needs "what should I look for here" pays ~1900 tokens for prose it already
knows, so compact=True answers with the 7-group checklist instead. That is a
SUBSTITUTION, not a truncation, which is exactly why it needs pinning: a rule the
repo authored, or a system rule for another language, must keep its byte count, and
a condensed payload must always declare verbatim_ocr_text=False. A summary of
someone else's text that you did not read is a guess, not a summary.

Same fake-subprocess harness as test_ocr_integration_p5.py, and for the same reason:
the binary lives under gitignored venv_ocr/, and the behaviour worth pinning is in
ToolA's own filtering rather than in OCR.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.tools import ocr_review as mod

# The live payload this was designed against -- ocr_rule on code_tools.py, daemon PID
# 48264 / generation eb032da77532:
#   groups[0] = {"group_id": 1, "source": "system",
#                "pattern": "**/*.{py,pyi,ipynb}", "files": ["code_tools.py"],
#                "rule": {"omitted": true, "bytes": 7598, "head": "> Favor precision..."}}
LIVE_PATTERN = "**/*.{py,pyi,ipynb}"
BIG_RULE = "# Review rule\n" + ("guidance line that costs real tokens\n" * 210)


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeSubprocess:
    """Stands in for the subprocess module inside ocr_review's globals."""

    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.calls = []

    def run(self, command, **kwargs):
        self.calls.append({"command": list(command), "kwargs": kwargs})
        return _FakeCompleted(self.returncode, self.stdout, self.stderr)


def _wire(monkeypatch, tmp_path, stdout=""):
    """Install a fake OCR binary and a fake subprocess. Returns (repo, fake)."""
    home = tmp_path / "a_home"
    bin_dir = home / "venv_ocr" / "node_modules" / "@alibaba-group" / "ocr-win32-x64" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "opencodereview.exe").write_bytes(b"MZ not a real binary")
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "mod.py").write_text("x = 1\n", encoding="utf-8")
    fake = _FakeSubprocess(stdout=stdout)
    monkeypatch.setattr(mod, "_a_repo_root", lambda: str(home))
    monkeypatch.setattr(mod, "subprocess", fake)
    return repo, fake


def _call(handler, **kwargs) -> dict:
    return json.loads(asyncio.run(handler(**kwargs)))


def _sys_py(rule=BIG_RULE, gid=1):
    return {"group_id": gid, "source": "system", "pattern": LIVE_PATTERN,
            "files": ["code_tools.py"], "rule": rule}


def _body(*groups) -> str:
    return json.dumps({"groups": list(groups), "total_groups": len(groups)})


# ------------------------------------------------------------------ substitution

def test_compact_replaces_the_system_python_rule_with_the_checklist(monkeypatch, tmp_path):
    repo, _fake = _wire(monkeypatch, tmp_path, stdout=_body(_sys_py()))

    payload = _call(mod.ocr_rule, project_path=str(repo), paths=["mod.py"], compact=True)

    assert payload["ok"] is True
    assert payload["compact"] is True
    assert payload["groups_condensed"] == 1
    assert payload["groups_not_condensed"] == []
    rule = payload["groups"][0]["rule"]
    assert rule["condensed"] is True
    assert rule["verbatim_ocr_text"] is False, "a condensation must never pass as OCR's words"
    assert rule["checklist"] == list(mod._OCR_PY_CHECKLIST)
    assert rule["body_bytes"] and rule["body_bytes"] > 5000, "the cost avoided stays on record"
    assert "head" not in rule, "no prose from the body may leak through the substitution"
    assert "guidance line that costs real tokens" not in json.dumps(payload, ensure_ascii=False)


def test_compact_keeps_a_repo_authored_rule_at_its_byte_count(monkeypatch, tmp_path):
    """The checklist condenses OCR's SYSTEM python rule. A repo wrote its own text."""
    group = _sys_py()
    group["source"] = "repo"
    repo, _fake = _wire(monkeypatch, tmp_path, stdout=_body(group))

    payload = _call(mod.ocr_rule, project_path=str(repo), paths=["mod.py"], compact=True)

    assert payload["groups_condensed"] == 0
    assert payload["groups_not_condensed"] == [1]
    assert payload["groups"][0]["rule"]["omitted"] is True
    assert payload["groups"][0]["rule"]["bytes"] > 5000
    assert "include_rule_text=true" in payload["ca_hint_rule_text"]


def test_compact_keeps_a_system_rule_for_another_language(monkeypatch, tmp_path):
    group = _sys_py()
    group["pattern"] = "**/*.{ts,tsx}"
    repo, _fake = _wire(monkeypatch, tmp_path, stdout=_body(group))

    payload = _call(mod.ocr_rule, project_path=str(repo), paths=["mod.py"], compact=True)

    assert payload["groups_condensed"] == 0
    assert payload["groups"][0]["rule"]["omitted"] is True


def test_a_mixed_answer_condenses_only_the_group_it_may(monkeypatch, tmp_path):
    other = _sys_py(gid=2)
    other["source"] = "repo"
    repo, _fake = _wire(monkeypatch, tmp_path, stdout=_body(_sys_py(gid=1), other))

    payload = _call(mod.ocr_rule, project_path=str(repo), paths=["mod.py"], compact=True)

    assert payload["groups_condensed"] == 1
    assert payload["groups_not_condensed"] == [2]
    assert payload["groups"][0]["rule"]["condensed"] is True
    assert payload["groups"][1]["rule"]["omitted"] is True


# ----------------------------------------------------------------- contradiction

def test_compact_and_include_rule_text_are_refused_before_spawning(monkeypatch, tmp_path):
    repo, fake = _wire(monkeypatch, tmp_path, stdout=_body(_sys_py()))

    payload = _call(mod.ocr_rule, project_path=str(repo), paths=["mod.py"],
                    compact=True, include_rule_text=True)

    assert payload["ok"] is False
    assert payload["error_name"] == "SCHEMA_VIOLATION"
    assert "mutually" in payload["error"]
    assert fake.calls == [], "a contradiction is answered without spawning OCR"


# ------------------------------------------------------------- the old contract

def test_compact_false_leaves_the_payload_on_its_old_contract(monkeypatch, tmp_path):
    repo, _fake = _wire(monkeypatch, tmp_path, stdout=_body(_sys_py()))

    payload = _call(mod.ocr_rule, project_path=str(repo), paths=["mod.py"])

    assert payload["rule_text_included"] is False
    assert "compact" not in payload
    assert "groups_condensed" not in payload
    assert "groups_not_condensed" not in payload
    rule = payload["groups"][0]["rule"]
    assert rule["omitted"] is True
    assert "condensed" not in rule
    assert "large strings are reported as byte counts" in payload["ca_hint_rule_text"]


def test_include_rule_text_alone_still_returns_the_whole_body(monkeypatch, tmp_path):
    repo, _fake = _wire(monkeypatch, tmp_path, stdout=_body(_sys_py()))

    payload = _call(mod.ocr_rule, project_path=str(repo), paths=["mod.py"],
                    include_rule_text=True)

    assert payload["groups"][0]["rule"] == BIG_RULE
    assert "compact" not in payload


# ------------------------------------------------------------------- the gate
#
# _ocr_checklist_applies is module-level so these can drive it with synthetic groups.
# A gate that has only ever seen the happy path is not a gate.

def test_the_gate_admits_the_shape_measured_live():
    assert mod._ocr_checklist_applies(_sys_py()) is True
    assert mod._ocr_checklist_applies(
        {"source": "SYSTEM", "pattern": "**/*.py"}) is True, "case and spacing are noise"
    assert mod._ocr_checklist_applies({"source": " system ", "pattern": "src/**/*.py"}) is True


def test_the_gate_refuses_every_shape_it_cannot_vouch_for():
    assert mod._ocr_checklist_applies({"source": "repo", "pattern": LIVE_PATTERN}) is False
    assert mod._ocr_checklist_applies({"source": "system", "pattern": "**/*.ts"}) is False
    assert mod._ocr_checklist_applies({"source": "system", "pattern": "**/*.pyi"}) is False, (
        "a stub-only rule is not the rule the checklist condenses")
    assert mod._ocr_checklist_applies({"source": "system"}) is False, "no pattern, no claim"
    assert mod._ocr_checklist_applies({"pattern": LIVE_PATTERN}) is False, "no source, no claim"
    assert mod._ocr_checklist_applies({}) is False
    assert mod._ocr_checklist_applies(None) is False
    assert mod._ocr_checklist_applies("system") is False


# ------------------------------------------------------------------ the payoff

def test_the_checklist_still_names_all_seven_groups():
    """Pinned by the ASCII anchors, so the Vietnamese wording can be edited freely
    while a silent drop of a whole group cannot pass."""
    lines = list(mod._OCR_PY_CHECKLIST)
    assert len(lines) == 8, "one header plus seven groups"
    for i, line in enumerate(lines[1:], start=1):
        assert line.startswith(f"{i}. "), f"group {i} must be numbered in order"
    joined = "\n".join(lines)
    for anchor in ("Boundary", "Mutable", "Errors", "Resources",
                   "Performance", "Concurrency", "Security"):
        assert anchor in joined, f"{anchor} group went missing from the checklist"
    for probe in ("off-by-one", "def f(x=[])", "bare except", "with",
                  "list vs set", "async", "shell=True"):
        assert probe in joined, f"{probe} is what makes that group actionable"


def test_the_condensation_is_measurably_cheaper_than_the_body(monkeypatch, tmp_path):
    repo, _fake = _wire(monkeypatch, tmp_path, stdout=_body(_sys_py()))

    full = _call(mod.ocr_rule, project_path=str(repo), paths=["mod.py"],
                 include_rule_text=True)
    compact = _call(mod.ocr_rule, project_path=str(repo), paths=["mod.py"], compact=True)

    full_bytes = len(json.dumps(full, ensure_ascii=False).encode("utf-8"))
    compact_bytes = len(json.dumps(compact, ensure_ascii=False).encode("utf-8"))
    assert full_bytes > 7000, "the fake body must be the size the live one measured"
    assert compact_bytes < 2000
    assert full_bytes > compact_bytes * 4, (
        f"compact bought too little: {full_bytes} -> {compact_bytes}")
