"""``run_project_command`` deletes trees too, and was the unguarded half (CASE-2008).

The incident was fixed in ``run_project_test_script``, which is the right place for
scripts: ``run_ps1_script`` and ``run_node_script`` are thin wrappers over it, so one
gate covers every script caller. But ``run_project_command`` is a SEPARATE entry
point to the same filesystem, and ``rd /s /q junk`` through it returned ok with exit
code 0 and really removed the directory.

That gap is the same shape as the incident's own root cause: a safety property that
lived on one tool and not on its sibling. The dispatcher's auto-backup gate is a
hardcoded set of five FILE tools, which is why a deleting script was never covered;
leaving the command surface uncovered would have set up the identical failure with a
different tool name. Being token-gated as "arbitrary shell exec" does not close it --
CASE-2008 was a mistaken call by an authorised caller, so authorisation was never the
missing part.

Two properties of this surface, both load-bearing and both asserted below:

* **It is not one interpreter.** The same tool runs cmd, PowerShell, ``node -e`` and
  ``python -c``. Scanning with a single runtime's pattern set was measured to miss
  ``fs.rmSync``, ``fs.unlinkSync``, ``shutil.rmtree`` and ``os.remove`` -- four
  working ways to delete a tree through the surface that already had an incident. The
  gate therefore scans the union (``runtime="any"``).
* **Zero does not mean unlimited here.** ``stdout_max_chars`` is passed on to
  ``run_project_test_script`` as ``stdout_max_chars or 12000`` on the auto-route
  paths, so "clear the cap to disable truncation" silently re-imposes 12000 -- the
  same class of accident as the truncation that hid the damage. The no-truncate
  intent is carried as a flag that each site reads.

The gate itself is shared with the script surface (``_destructive_refusal`` /
``_destructive_backup_or_refusal``) rather than copied, so the two cannot drift.
These tests drive the public tool and assert on the filesystem, so they keep working
if the gate moves again.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import code_tools as ct  # noqa: E402


@pytest.fixture()
def sandbox(tmp_path):
    """A project root with one disposable file and three that must survive."""
    (tmp_path / ".orchestrator_cache").mkdir()
    (tmp_path / "keep.txt").write_text("PRECIOUS", encoding="utf-8")
    junk = tmp_path / "junk"
    junk.mkdir()
    (junk / "a.tmp").write_text("disposable", encoding="utf-8")
    (junk / "README.md").write_text("DO NOT DELETE", encoding="utf-8")
    (junk / ".gitignore").write_text("*.log", encoding="utf-8")
    return tmp_path


def _run(coro):
    return json.loads(asyncio.run(coro))


def _survivors(root: Path) -> set[str]:
    return {
        str(p.relative_to(root)).replace("\\", "/")
        for p in root.rglob("*")
        if p.is_file() and ".orchestrator_cache" not in str(p)
    }


def test_rd_slash_s_is_refused_and_deletes_nothing(sandbox) -> None:
    """The gap, stated as the regression: this really did delete the tree.

    Asserted on the filesystem rather than on the refusal text, because the point
    is not that a message was produced -- it is that the directory is still there.
    """
    before = _survivors(sandbox)
    payload = _run(ct.run_project_command(str(sandbox), 'rd /s /q "%s"' % (sandbox / "junk")))

    assert payload["ok"] is False, payload
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED"
    assert payload["destructive_count"] >= 1
    assert _survivors(sandbox) == before, "the refused command deleted files anyway"


def test_the_scan_covers_every_runtime_this_one_tool_can_reach(sandbox) -> None:
    """One tool, four interpreters. Naming one runtime unguards the other three.

    This is the parametrised form of a measurement taken before the gate was
    written: with the PowerShell pattern set selected, every ``node``/``python``
    row here scanned clean and executed. They are separated by interpreter rather
    than merged into one case so a future narrowing of the pattern set names which
    runtime it broke.
    """
    target = str(sandbox / "junk").replace("\\", "/")
    commands = {
        "cmd_rd": 'rd /s /q "%s"' % (sandbox / "junk"),
        "cmd_del": 'del /s /q "%s\\*"' % (sandbox / "junk"),
        "powershell": 'Remove-Item -Recurse -Force "%s"' % (sandbox / "junk"),
        "node_rmsync": 'node -e "fs.rmSync(\'%s\',{recursive:true})"' % target,
        "node_unlink": 'node -e "fs.unlinkSync(\'%s/a.tmp\')"' % target,
        "python_rmtree": 'python -c "import shutil;shutil.rmtree(\'%s\')"' % target,
        "python_remove": 'python -c "import os;os.remove(\'%s/a.tmp\')"' % target,
        "sh_rm_rf": "bash -c 'rm -rf %s'" % target,
    }
    before = _survivors(sandbox)
    unguarded = []
    for label, command in commands.items():
        payload = _run(ct.run_project_command(str(sandbox), command))
        if payload.get("error_name") != "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED":
            unguarded.append((label, payload.get("error_name"), payload.get("ok")))
    assert not unguarded, "these delete shapes passed the gate: %r" % unguarded
    assert _survivors(sandbox) == before, "a refused command still deleted something"


def test_an_acknowledged_command_runs_and_deletes_only_its_target(sandbox) -> None:
    """The gate must not become a wall: confirmed, it works, and only on its target.

    ``del`` on ``junk\\*.tmp`` rather than ``rd`` on the directory, so the surviving
    siblings are evidence that the confirmation authorises the command as written and
    nothing wider.
    """
    command = 'del /q "%s\\*.tmp"' % (sandbox / "junk")
    count = _run(ct.run_project_command(str(sandbox), command))["destructive_count"]
    payload = _run(ct.run_project_command(
        str(sandbox), command, confirm_destructive=count, skip_backup=True))

    assert payload["ok"] is True, payload
    survivors = _survivors(sandbox)
    assert "junk/a.tmp" not in survivors, "the confirmed deletion did not happen"
    assert {"junk/README.md", "junk/.gitignore", "keep.txt"} <= survivors


def test_a_stale_count_is_refused(sandbox) -> None:
    """A count, not a boolean, so editing the command invalidates the acknowledgement.

    Nine is not a number any of these commands produces; the guard must compare
    rather than merely check for presence.
    """
    before = _survivors(sandbox)
    payload = _run(ct.run_project_command(
        str(sandbox), 'rd /s /q "%s"' % (sandbox / "junk"), confirm_destructive=9))

    assert payload["ok"] is False, payload
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED"
    assert _survivors(sandbox) == before


def test_dry_run_previews_without_executing(sandbox) -> None:
    """A preview must be side-effect free AND legible as a non-run.

    ``ok:true`` means "the preview worked", so ``executed:false`` is what stops a
    caller reading a clean preview as a clean run.
    """
    before = _survivors(sandbox)
    payload = _run(ct.run_project_command(
        str(sandbox), 'rd /s /q "%s"' % (sandbox / "junk"), dry_run=True))

    assert payload["ok"] is True, payload
    assert payload["executed"] is False
    assert payload["dry_run"] is True
    assert payload["destructive"] is True
    assert payload["destructive_count"] >= 1
    assert payload["confirm_destructive_required"] == payload["destructive_count"]
    assert _survivors(sandbox) == before, "a dry run deleted files"


def test_the_literalpath_footgun_is_refused_not_confirmable(sandbox) -> None:
    """The incident shape reaches this surface too, and is still not confirmable.

    PowerShell discards ``-Include`` when ``-LiteralPath`` is given, so there is no
    count worth acknowledging -- only a command to rewrite. Asserted here because
    ``run_project_command`` can invoke powershell, so dropping the footgun analysis
    for the union runtime would reopen the exact incident through a different tool.
    """
    before = _survivors(sandbox)
    command = ('powershell -Command "Remove-Item -LiteralPath \'%s\' -Include *.tmp '
               '-Recurse -Force"' % (sandbox / "junk"))
    payload = _run(ct.run_project_command(str(sandbox), command))

    assert payload["ok"] is False, payload
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_FOOTGUN"
    assert payload["retryable"] is False
    assert payload["footguns"], "the footgun list is empty on the footgun shape"
    assert _survivors(sandbox) == before

    # And it stays refused even when a caller tries to confirm its way past it.
    payload = _run(ct.run_project_command(str(sandbox), command, confirm_destructive=1))
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_FOOTGUN", payload
    assert _survivors(sandbox) == before


def test_a_list_form_command_is_scanned_as_the_string_it_becomes(sandbox) -> None:
    """``command`` accepts a list, and a list must not be a way around the gate.

    The scan runs on the joined text for exactly this reason. A guard that only saw
    the string shape would be bypassable by passing the same words as a list.
    """
    before = _survivors(sandbox)
    payload = _run(ct.run_project_command(
        str(sandbox), ["rd", "/s", "/q", str(sandbox / "junk")]))

    assert payload["ok"] is False, payload
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED"
    assert _survivors(sandbox) == before


def test_a_base64_command_is_scanned_after_decoding(sandbox) -> None:
    """Encoding is a transport detail, not a privilege level.

    The gate sits after the decode, so an encoded body is scanned as the command it
    becomes rather than as its envelope.
    """
    import base64

    raw = 'rd /s /q "%s"' % (sandbox / "junk")
    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    before = _survivors(sandbox)
    payload = _run(ct.run_project_command(
        str(sandbox), encoded, command_encoding="base64"))

    assert payload["ok"] is False, payload
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED"
    assert _survivors(sandbox) == before


def test_a_destructive_command_never_truncates_its_output(sandbox) -> None:
    """Truncation hid the damage, so the cap must not apply to a destructive run.

    Asserted by asking for an absurdly small cap: 68,978 chars became 4,000 in the
    incident and the two filenames fell off the end. Note the fix could not be
    "set the cap to 0" -- on the auto-route paths that value is passed on as
    ``stdout_max_chars or 12000`` and would restore a cap, so this asserts the
    OUTCOME (nothing truncated) rather than the mechanism.
    """
    # 60 files, so ``del /s /q`` reports ~2900 chars of "Deleted file - ..." lines.
    # No shell loop and no ``&&``: run_project_command escapes ``%`` and ``&`` on
    # purpose (transport policy C2), so a loop would never execute and the test would
    # be measuring a failed command rather than a truncation rule.
    junk = sandbox / "junk"
    for index in range(60):
        (junk / ("f%02d.tmp" % index)).write_text("x" * 40, encoding="utf-8")
    command = 'del /s /q "%s"' % (junk / "*.tmp")
    count = _run(ct.run_project_command(str(sandbox), command))["destructive_count"]
    payload = _run(ct.run_project_command(
        str(sandbox), command, confirm_destructive=count, skip_backup=True,
        stdout_max_chars=50))

    assert payload["ok"] is True, payload
    stdout = payload.get("stdout") or ""
    assert "truncated" not in stdout, (
        "a destructive command truncated its own output: %r" % stdout[-200:])
    assert len(stdout) > 2000, (
        "expected the full per-file delete log, got %d chars" % len(stdout))
    assert "f59.tmp" in stdout, "the tail of a destructive run was dropped"
    assert not list(junk.glob("*.tmp")), "the confirmed deletion did not happen"


def test_a_benign_command_keeps_its_truncation_cap(sandbox) -> None:
    """The no-truncate rule is scoped to destructive runs, not a global change.

    The companion to the test above, and the one that catches a flag wired the wrong
    way round: an unconditional no-truncate would pass that test and quietly return
    unbounded output for every ordinary call.
    """
    junk = sandbox / "junk"
    for index in range(60):
        (junk / ("f%02d.keep" % index)).write_text("x" * 40, encoding="utf-8")
    payload = _run(ct.run_project_command(
        str(sandbox), 'dir /s /b "%s"' % junk, stdout_max_chars=120))

    assert payload["ok"] is True, payload
    assert "truncated" in (payload.get("stdout") or ""), (
        "a benign command ignored its stdout_max_chars cap")


def test_a_benign_command_is_completely_unaffected(sandbox) -> None:
    """34 real call sites in this repo were measured against the gate: 0 flagged.

    So the common path must be untouched -- no refusal, no backup, no destructive
    keys appearing in an ordinary response.
    """
    payload = _run(ct.run_project_command(str(sandbox), "echo hello"))

    assert payload["ok"] is True, payload
    assert "hello" in (payload.get("stdout") or "")
    assert payload.get("destructive") is not True
    assert "destructive_count" not in payload
    assert payload.get("backup_info") is None, (
        "a benign command triggered a project backup: %r" % payload.get("backup_info"))
    assert _survivors(sandbox) >= {"keep.txt", "junk/a.tmp"}


def test_a_destructive_command_takes_a_backup_first(sandbox) -> None:
    """Unrecoverability was the real cost, so the backup is part of the fix here too.

    Asserted through the reported ``backup_info`` rather than by inspecting the
    archive, so the test survives a change of backup implementation.
    """
    command = 'del /q "%s\\*.tmp"' % (sandbox / "junk")
    count = _run(ct.run_project_command(str(sandbox), command))["destructive_count"]

    payload = _run(ct.run_project_command(
        str(sandbox), command, confirm_destructive=count, skip_backup=True))
    assert payload["ok"] is True, payload
    assert (payload.get("backup_info") or {}).get("reason") == "skip_backup_requested", (
        "a skipped backup is not recorded: %r" % payload.get("backup_info"))

    (sandbox / "junk" / "b.tmp").write_text("again", encoding="utf-8")
    payload = _run(ct.run_project_command(
        str(sandbox), command, confirm_destructive=count))
    assert payload["ok"] is True, payload
    assert payload.get("backup_info") is not None, (
        "a destructive command without skip_backup reported no backup at all")


@pytest.mark.parametrize("mode", ["returns_not_ok", "raises"])
def test_a_failed_backup_refuses_the_run(sandbox, monkeypatch, mode) -> None:
    """If the safety net cannot be hung, the trapeze does not swing.

    Proceeding after a failed backup is the worst of both designs: the caller sees
    backup machinery in the response and assumes a recoverability that does not
    exist. Both failure shapes must refuse, and the files must still be there.
    """
    async def _not_ok(*args, **kwargs):
        return json.dumps({"ok": False, "error": "disk full"})

    async def _raises(*args, **kwargs):
        raise RuntimeError("backup subsystem down")

    monkeypatch.setattr(ct, "backup_project", _not_ok if mode == "returns_not_ok" else _raises)

    command = 'del /q "%s\\*.tmp"' % (sandbox / "junk")
    before = _survivors(sandbox)
    payload = _run(ct.run_project_command(str(sandbox), command, confirm_destructive=1))

    assert payload["ok"] is False, payload
    assert payload["error_name"] == "BACKUP_REQUIRED", payload
    assert _survivors(sandbox) == before, "the run proceeded despite a failed backup"


def test_the_gate_is_shared_with_the_script_surface_not_copied() -> None:
    """The drift that made CASE-2008 possible must not be recreated by the fix.

    A safety property on one tool and not its sibling is the root cause, so two
    copies of this gate -- with a future fix applied to one -- would be the same bug
    again. Asserted structurally: both entry points must resolve the refusal through
    the shared helper, and the helper must be the only place the refusal text lives.
    """
    assert callable(ct._destructive_refusal)
    assert callable(ct._destructive_backup_or_refusal)

    source = Path(ct.__file__).with_suffix(".py").read_text(encoding="utf-8")
    for error_name in ("DESTRUCTIVE_SCRIPT_FOOTGUN", "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED"):
        produced = source.count('error_name="%s"' % error_name)
        assert produced == 1, (
            "%s is raised from %d places; it must come from the shared helper only"
            % (error_name, produced))

    import inspect
    for tool in (ct.run_project_command, ct.run_project_test_script):
        body = inspect.getsource(tool)
        assert "_destructive_refusal(" in body, "%s does not use the shared gate" % tool.__name__
        assert "_destructive_backup_or_refusal(" in body, (
            "%s does not use the shared backup gate" % tool.__name__)


def test_the_schema_accepts_the_three_new_fields() -> None:
    """A guard the dispatcher rejects as UNSUPPORTED_FIELD is a guard nobody can pass.

    ``allowed_params`` is built from the registry's properties, so a parameter that
    exists only in the Python signature is unreachable over HTTP -- the caller would
    be told to confirm and then refused for confirming. Both halves are asserted,
    plus the error's own ``accepted_fields`` list, which is what teaches a caller
    the field set.
    """
    import inspect

    schema = None
    for entry in ct.TOOL_SCHEMAS:
        if entry.get("function", {}).get("name") == "run_project_command":
            schema = entry["function"]["parameters"]
    assert schema is not None, "run_project_command is not registered"

    properties = schema["properties"]
    signature = inspect.signature(ct.run_project_command).parameters
    for field in ("dry_run", "confirm_destructive", "skip_backup"):
        assert field in properties, "%s missing from the registry schema" % field
        assert field in signature, "%s missing from the signature" % field

    source = inspect.getsource(ct.run_project_command)
    for field in ("dry_run", "confirm_destructive", "skip_backup"):
        assert '"%s"' % field in source, (
            "%s is not listed in accepted_fields, so UNSUPPORTED_FIELD would teach a "
            "caller the wrong field set" % field)
