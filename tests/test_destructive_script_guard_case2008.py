"""A script that deletes files must not be able to do so unannounced (CASE-2008).

On 2026-08-20 an agent ran this through ``run_ps1_script`` and lost files it never
meant to touch::

    Remove-Item -LiteralPath <dir> -Include *.tmp -Recurse -Force -ErrorAction SilentlyContinue

Four separate things had to be true for that to end badly, and all four were:

* **PowerShell silently ignores -Include/-Exclude when -LiteralPath is given.** The
  filter had no effect, so the whole directory went -- including ``.gitignore`` and
  ``README.md``. No error, no warning, exit code 0. This, not regex over-matching,
  is the actual mechanism.
* **No dry run existed**, so the blast radius was never previewable.
* **No backup was taken.** The dispatcher's auto-backup gate
  (``_backup_gated_tools``) is a hardcoded set of five FILE tools, and a script
  that deletes files is not one of them. (The post-incident report blames
  ``mutation_scope``; that is wrong -- it is read only by /api/tools, the catalog
  and the drift check, and gates nothing. A fix aimed there would have changed
  nothing, which is why this test does not assert anything about it.)
* **stdout was truncated at 4000 chars** while 68,978 were produced, so the lines
  naming the two files fell off the end and the agent believed only temp files had
  gone.

The guard lives in ``run_project_test_script`` rather than in ``run_ps1_script``,
because the ps1/node tools are thin wrappers over it -- one gate therefore covers
every caller and every runtime. These tests drive the PUBLIC wrappers and assert on
the filesystem where they can, so they keep passing if the guard is later moved.
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
    """A project root holding one disposable file and two that must survive."""
    (tmp_path / ".orchestrator_cache").mkdir()
    (tmp_path / "keep.txt").write_text("PRECIOUS", encoding="utf-8")
    junk = tmp_path / "junk"
    junk.mkdir()
    (junk / "a.tmp").write_text("disposable", encoding="utf-8")
    # The two real casualties of the incident, in the directory being cleaned.
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


def _incident_script(root: Path) -> str:
    return (
        'Remove-Item -LiteralPath "%s" -Include *.tmp -Recurse -Force '
        "-ErrorAction SilentlyContinue" % (root / "junk")
    )


def test_the_exact_incident_script_is_refused_and_deletes_nothing(sandbox) -> None:
    """The regression that matters: this body must never execute again.

    Asserted on the FILESYSTEM as well as the error, because an error response
    with the files already gone would be the incident repeating with better
    paperwork.
    """
    before = _survivors(sandbox)
    payload = _run(ct.run_ps1_script(str(sandbox), _incident_script(sandbox)))
    assert payload["ok"] is False
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_FOOTGUN"
    assert _survivors(sandbox) == before, "the refused script still deleted something"
    assert "junk/README.md" in _survivors(sandbox)
    assert "junk/.gitignore" in _survivors(sandbox)


def test_the_footgun_is_not_confirmable(sandbox) -> None:
    """-Include with -LiteralPath is refused OUTRIGHT, not merely gated.

    Acknowledging a count here would confirm an intent the script does not
    implement: the caller means "delete the .tmp files" and the script means
    "delete the directory". No number expresses that, so there is nothing to
    confirm -- only a script to rewrite. This is the one case where a correct
    ``confirm_destructive`` must still fail.
    """
    payload = _run(
        ct.run_ps1_script(str(sandbox), _incident_script(sandbox), confirm_destructive=1)
    )
    assert payload["ok"] is False
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_FOOTGUN"
    payload = _run(
        ct.run_ps1_script(str(sandbox), _incident_script(sandbox), confirm_destructive=99)
    )
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_FOOTGUN"
    assert "junk/README.md" in _survivors(sandbox)


def test_the_refusal_explains_the_silent_ignore(sandbox) -> None:
    """The caller has to learn WHY, or they will retry the same shape.

    An agent that only reads "refused" edits the path and tries again. The
    response has to name the PowerShell behaviour, because that behaviour is
    invisible from the script text.
    """
    payload = _run(ct.run_ps1_script(str(sandbox), _incident_script(sandbox)))
    footguns = payload.get("footguns") or []
    assert footguns, "the refusal carries no footgun detail"
    detail = json.dumps(footguns).lower()
    assert "literalpath" in detail
    assert "ignore" in detail, "the response never says the filter is ignored"
    assert footguns[0].get("fix"), "no rewrite is suggested"


def test_a_line_split_footgun_is_still_caught(sandbox) -> None:
    """A backtick continuation must not smuggle the shape past a line-based check.

    This is the form a CA naturally writes for a long command, so a guard that
    only looked at physical lines would miss the common case rather than an
    exotic one.
    """
    script = (
        'Remove-Item -LiteralPath "%s" `\n'
        "  -Include *.tmp -Recurse -Force\n" % (sandbox / "junk")
    )
    payload = _run(ct.run_ps1_script(str(sandbox), script))
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_FOOTGUN"
    assert "junk/README.md" in _survivors(sandbox)


def test_a_correct_destructive_script_still_needs_acknowledgement(sandbox) -> None:
    """The properly-written form runs, but not on the first call.

    ``-Path <dir>\\*`` with ``-Include`` is the correct shape and is NOT a footgun,
    so it is gated rather than refused -- and the gate must hold nothing back
    about what it found, or the acknowledgement is uninformed.
    """
    script = 'Remove-Item -Path "%s\\*" -Include *.tmp -Recurse -Force' % (sandbox / "junk")
    before = _survivors(sandbox)
    payload = _run(ct.run_ps1_script(str(sandbox), script))
    assert payload["ok"] is False
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED"
    assert payload["destructive_count"] >= 1
    assert payload["confirm_destructive_required"] == payload["destructive_count"]
    assert payload["destructive_statements"], "the caller is asked to confirm a count with no list"
    assert _survivors(sandbox) == before, "the unconfirmed script executed anyway"


def test_a_stale_or_wrong_count_is_refused(sandbox) -> None:
    """A count, not a boolean, so editing the script invalidates the acknowledgement.

    ``confirm_destructive=true`` would carry over to a body the caller never
    previewed. Pinning the exact number is what stops "I confirmed the last
    version" from authorising this one.
    """
    script = 'Remove-Item -Path "%s\\*" -Include *.tmp -Recurse -Force' % (sandbox / "junk")
    actual = _run(ct.run_ps1_script(str(sandbox), script))["destructive_count"]
    for wrong in (actual + 1, 0, 99):
        payload = _run(ct.run_ps1_script(str(sandbox), script, confirm_destructive=wrong))
        assert payload["ok"] is False, "confirm_destructive=%r was accepted" % wrong
        assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED"
    assert "junk/a.tmp" in _survivors(sandbox)


def test_the_count_is_one_per_statement_not_one_per_pattern(sandbox) -> None:
    """The number the caller must reproduce has to mean something to a reader.

    Found while writing these tests: a single ``Remove-Item`` scored 2, because it
    matches both the powershell pattern and the embedded-shell one. Two bad
    consequences. A caller reading "confirm 2 deletions" for one command cannot
    reconcile the number with the script, so the acknowledgement stops being a
    review and becomes copying a magic number. And the count moved whenever the
    pattern list grew -- silently invalidating acknowledgements that were correct
    when written.
    """
    one = 'Remove-Item -Path "%s\\*" -Include *.tmp -Recurse -Force' % (sandbox / "junk")
    payload = _run(ct.run_ps1_script(str(sandbox), one))
    assert payload["destructive_count"] == 1, (
        "one command counted as %r" % payload["destructive_count"]
    )

    two = one + '\nRemove-Item -Path "%s\\keep.txt" -Force' % sandbox
    payload = _run(ct.run_ps1_script(str(sandbox), two))
    assert payload["destructive_count"] == 2, "two commands did not count as 2"
    assert len(payload["destructive_statements"]) == 2
    assert {s["line"] for s in payload["destructive_statements"]} == {1, 2}


def test_an_acknowledged_script_runs_and_deletes_only_its_target(sandbox) -> None:
    """The guard must not become a wall: the correct script, correctly confirmed, works.

    And it deletes only ``a.tmp`` -- which is the whole difference between the
    correct shape and the incident shape, demonstrated on real files rather than
    argued from the flags.
    """
    script = 'Remove-Item -Path "%s\\*" -Include *.tmp -Recurse -Force' % (sandbox / "junk")
    count = _run(ct.run_ps1_script(str(sandbox), script))["destructive_count"]
    payload = _run(
        ct.run_ps1_script(str(sandbox), script, confirm_destructive=count, skip_backup=True)
    )
    assert payload["ok"] is True, payload
    assert payload["exit_code"] == 0
    assert payload["destructive"] is True
    assert payload["confirm_destructive_acknowledged"] == count
    survivors = _survivors(sandbox)
    assert "junk/a.tmp" not in survivors, "the confirmed deletion did not happen"
    assert {"junk/README.md", "junk/.gitignore", "keep.txt"} <= survivors


def test_a_destructive_run_never_truncates_its_output(sandbox) -> None:
    """Truncation is what hid the damage, so a destructive run must ignore the cap.

    Asserted by asking for an absurdly small cap on a chatty script: the old
    behaviour would honour it, which is precisely how 68,978 chars became 4,000
    and two filenames vanished from the report.
    """
    script = (
        '1..80 | ForEach-Object { Write-Host "line $_ ................................" }\n'
        'Remove-Item -Path "%s\\*" -Include *.tmp -Force\n' % (sandbox / "junk")
    )
    count = _run(ct.run_ps1_script(str(sandbox), script))["destructive_count"]
    payload = _run(
        ct.run_ps1_script(
            str(sandbox), script, confirm_destructive=count, skip_backup=True,
            stdout_max_chars=50,
        )
    )
    assert payload["ok"] is True, payload
    metadata = payload.get("stdout_metadata") or {}
    assert metadata.get("truncated") is False, (
        "a destructive run truncated its own output: %r" % metadata
    )
    assert "line 80" in payload["stdout"], "the tail of a destructive run was dropped"


def test_dry_run_reports_without_executing(sandbox) -> None:
    """A preview must be free of side effects AND legible as a non-run.

    ``ok:true`` here means "the preview worked", so ``executed:false`` is what
    stops a caller reading a clean preview as a clean run.
    """
    script = 'Remove-Item -Path "%s\\*" -Include *.tmp -Recurse -Force' % (sandbox / "junk")
    before = _survivors(sandbox)
    payload = _run(ct.run_ps1_script(str(sandbox), script, dry_run=True))
    assert payload["ok"] is True
    assert payload["executed"] is False
    assert payload["destructive"] is True
    assert payload["destructive_count"] >= 1
    assert _survivors(sandbox) == before, "dry_run executed the script"
    # An honest preview says what it cannot see. It is a source scan: it does not
    # expand variables or resolve globs, so calling it a blast radius would lie.
    assert payload.get("preview_limits"), "dry_run does not state its own limits"


def test_dry_run_is_safe_on_the_footgun_shape(sandbox) -> None:
    """Previewing the incident script must not be the thing that runs it.

    A caller told "rewrite this" will often reach for dry_run to inspect it, and
    that path has to be inert even though execution is refused for other reasons.
    """
    before = _survivors(sandbox)
    payload = _run(ct.run_ps1_script(str(sandbox), _incident_script(sandbox), dry_run=True))
    assert _survivors(sandbox) == before
    # Either answer is defensible (refuse, or preview and report the footgun) as
    # long as nothing executed and the footgun is visible either way. Asserted on
    # the STRUCTURED fields, not on a substring of the whole payload: the script
    # body is echoed back, so searching the JSON for "literalpath" passes even
    # with the guard removed entirely -- which a mutation check caught it doing.
    assert (
        payload.get("error_name") == "DESTRUCTIVE_SCRIPT_FOOTGUN"
        or payload.get("footguns")
    ), "the footgun is invisible in a dry_run: %r" % payload


def test_a_benign_script_is_completely_unaffected(sandbox) -> None:
    """The guard must be invisible on the common path.

    Every non-destructive caller -- which is nearly all of them -- has to keep the
    old behaviour exactly, including the truncation cap and the absence of any
    destructive bookkeeping in the response.
    """
    payload = _run(ct.run_ps1_script(str(sandbox), 'Write-Host "hello benign"'))
    assert payload["ok"] is True
    assert payload["exit_code"] == 0
    assert "hello benign" in payload["stdout"]
    assert "destructive" not in payload, (
        "a benign script grew destructive bookkeeping: %r" % payload
    )
    assert "confirm_destructive_required" not in payload


def test_a_benign_script_keeps_its_truncation_cap(sandbox) -> None:
    """Only DESTRUCTIVE runs get the untruncated treatment.

    Removing truncation everywhere would be a token-cost regression on every
    chatty benign script, so the exemption has to stay scoped.
    """
    script = '1..200 | ForEach-Object { Write-Host "row $_ .........................." }'
    payload = _run(ct.run_ps1_script(str(sandbox), script, stdout_max_chars=100))
    assert payload["ok"] is True
    assert (payload.get("stdout_metadata") or {}).get("truncated") is True, (
        "a benign script stopped honouring stdout_max_chars"
    )


def test_the_node_runtime_is_covered_by_the_same_gate(sandbox) -> None:
    """One gate, every runtime -- including the receiver-agnostic call shape.

    ``require("fs").rmSync(...)`` is a MEASURED false negative of an fs-anchored
    pattern: the ``)`` before the dot defeats it, and in a probe the call executed.
    """
    payload = _run(
        ct.run_node_script(str(sandbox), 'require("fs").rmSync("nope",{force:true});')
    )
    assert payload["ok"] is False
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED"

    payload = _run(
        ct.run_node_script(str(sandbox), 'const {unlinkSync} = require("fs"); unlinkSync(p);')
    )
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED", (
        "a destructured fs import bypassed the gate"
    )

    payload = _run(ct.run_node_script(str(sandbox), 'console.log("ok-node");'))
    assert payload["ok"] is True
    assert "ok-node" in payload["stdout"]


def test_a_destructive_command_hidden_in_a_shell_string_is_caught(sandbox) -> None:
    """Shelling out must not launder the intent.

    A Python body whose only destructive act is a string handed to PowerShell
    looks completely benign to a scan scoped to the host language -- and it
    deletes just as much. Same for ``child_process.execSync("rm -rf ...")``.
    """
    laundered_py = (
        "import subprocess\n"
        'subprocess.run(["powershell", "-c", "Remove-Item -Recurse -Force D:/nope"])\n'
    )
    payload = _run(
        ct.run_project_test_script(str(sandbox), laundered_py, runtime="python")
    )
    assert payload["ok"] is False
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED"

    laundered_js = 'require("child_process").execSync("rm -rf " + dir);'
    payload = _run(ct.run_node_script(str(sandbox), laundered_js))
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED"

    # Shelling out for non-destructive work must stay unflagged, or the guard
    # trips constantly and gets acknowledged reflexively -- worse than no guard.
    benign_py = "import subprocess\nsubprocess.run(['git', 'status'])\n"
    payload = _run(ct.run_project_test_script(str(sandbox), benign_py, runtime="python"))
    assert "destructive" not in payload or payload.get("destructive") is not True


def test_a_base64_body_is_scanned_after_decoding(sandbox) -> None:
    """The scan must see the script, not its envelope.

    ``content_encoding="base64"`` is the documented way to send a multiline body,
    so it is a completely ordinary call -- and the guard only works on it because
    it sits AFTER the decode. Nothing about the code says so, which makes this an
    invisible ordering dependency: hoisting the gate a few lines up for tidiness
    would leave every base64 caller unguarded and every test but this one green.
    """
    import base64

    encoded = base64.b64encode(_incident_script(sandbox).encode("utf-8")).decode("ascii")
    before = _survivors(sandbox)
    payload = _run(
        ct.run_project_test_script(
            str(sandbox), encoded, runtime="powershell", content_encoding="base64"
        )
    )
    assert payload["ok"] is False
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_FOOTGUN", (
        "a base64 body was scanned as opaque text: %r" % payload.get("error_name")
    )
    assert _survivors(sandbox) == before


def test_silentlycontinue_beside_a_deletion_is_called_out(sandbox) -> None:
    """The flag that made the incident quiet has to be named in the response.

    ``-ErrorAction SilentlyContinue`` is why a partial or wrong deletion reported
    success, and it is legitimate on non-destructive code -- so it is a warning
    attached to a destructive body, not an error on its own.
    """
    script = (
        'Remove-Item -Path "%s\\*" -Include *.tmp -Recurse -Force '
        "-ErrorAction SilentlyContinue" % (sandbox / "junk")
    )
    payload = _run(ct.run_ps1_script(str(sandbox), script))
    warnings = " ".join(payload.get("warnings") or [])
    assert "SilentlyContinue" in warnings, (
        "the silencing flag is not reported: %r" % payload.get("warnings")
    )

    benign = 'Get-Item "nope" -ErrorAction SilentlyContinue | Out-Null'
    payload = _run(ct.run_ps1_script(str(sandbox), benign))
    assert payload["ok"] is True, "SilentlyContinue was treated as destructive on its own"


def test_a_destructive_run_takes_a_backup_first(sandbox) -> None:
    """Unrecoverability was the real cost, so the backup is part of the fix.

    Asserted through the reported ``backup_info`` rather than by inspecting the
    archive, so the test survives a change of backup implementation. ``skip_backup``
    exists for disposable targets and must be visible in the result when used, or
    the audit trail cannot tell a backed-up run from a skipped one.
    """
    script = 'Remove-Item -Path "%s\\*" -Include *.tmp -Recurse -Force' % (sandbox / "junk")
    count = _run(ct.run_ps1_script(str(sandbox), script))["destructive_count"]

    payload = _run(
        ct.run_ps1_script(str(sandbox), script, confirm_destructive=count, skip_backup=True)
    )
    assert payload["ok"] is True
    assert (payload.get("backup_info") or {}).get("reason") == "skip_backup_requested", (
        "a skipped backup is not recorded in the result: %r" % payload.get("backup_info")
    )

    (sandbox / "junk" / "b.tmp").write_text("again", encoding="utf-8")
    payload = _run(
        ct.run_ps1_script(str(sandbox), script, confirm_destructive=count)
    )
    assert payload["ok"] is True, payload
    assert payload.get("backup_info") is not None, (
        "a destructive run without skip_backup reported no backup at all"
    )


@pytest.mark.parametrize("mode", ["returns_not_ok", "raises"])
def test_a_failed_backup_refuses_the_run(sandbox, monkeypatch, mode) -> None:
    """If the safety net cannot be hung, the trapeze does not swing.

    Proceeding after a failed backup would be the worst of both designs: the
    caller sees backup machinery in the response and assumes recoverability that
    does not exist. Unrecoverability, not the deletion itself, is what made
    CASE-2008 expensive -- so both failure shapes (a not-ok result and a raising
    call) must refuse, and the files must still be there afterwards.
    """
    script = 'Remove-Item -Path "%s\\*" -Include *.tmp -Recurse -Force' % (sandbox / "junk")
    count = _run(ct.run_ps1_script(str(sandbox), script))["destructive_count"]

    async def broken_backup(*_args, **_kwargs):
        if mode == "raises":
            raise RuntimeError("disk full")
        return json.dumps({"ok": False, "error": "disk full"})

    monkeypatch.setattr(ct, "backup_project", broken_backup)
    monkeypatch.setattr(ct, "_backup_manifest_status", lambda *_a, **_k: "missing")

    before = _survivors(sandbox)
    payload = _run(ct.run_ps1_script(str(sandbox), script, confirm_destructive=count))
    assert payload["ok"] is False
    assert payload["error_name"] == "BACKUP_REQUIRED"
    assert _survivors(sandbox) == before, "the script ran with no backup behind it"
    # skip_backup remains the deliberate escape hatch, so a caller who genuinely
    # has nothing to lose is not blocked by a broken backup subsystem.
    payload = _run(
        ct.run_ps1_script(
            str(sandbox), script, confirm_destructive=count, skip_backup=True
        )
    )
    assert payload["ok"] is True, payload
    assert "junk/a.tmp" not in _survivors(sandbox)


# --- An overwrite is destructive too (2026-09-24) --------------------------------
# 26 deployed SKILL.md were replaced by ``Copy-Item $src $dst -Force`` through
# run_ps1_script and the scan answered destructive_count=0: no count reviewed, no
# backup taken. Each shape names the flag whose only job is to switch an overwrite
# guard off, or to delete what the source lacks, so a future narrowing of the
# pattern table names the shape it dropped.
_OVERWRITE_SHAPES = [
    ("powershell", "Copy-Item $src $dst -Force", "copy_item_force"),
    ("powershell", "    cp $src $dst -Fo", "copy_item_force"),
    ("powershell", "Get-ChildItem $d | Copy-Item -Destination $t -Force:$true", "copy_item_force"),
    ("any", "xcopy src dst /E /Y", "copy_overwrite_y"),
    ("any", "copy /y a.txt b.txt", "copy_overwrite_y"),
    ("any", "robocopy src dst /MIR", "robocopy_purge"),
    ("any", "robocopy src dst /MOV", "robocopy_purge"),
    ("sh", "rsync -a --delete src/ dst/", "rsync_delete"),
    ("sh", "cp -rf src dst", "cp_force"),
    ("python", "shutil.copytree(src, dst, dirs_exist_ok=True)", "copytree_merge"),
    ("python", 'subprocess.run("xcopy a b /y", shell=True)', "copy_overwrite_y"),
]


@pytest.mark.parametrize("runtime,line,kind", _OVERWRITE_SHAPES)
def test_an_overwrite_that_switches_its_guard_off_is_counted(runtime, line, kind) -> None:
    """One statement, one count -- and the kind says which flag made it destructive."""
    scan = ct._scan_destructive_script(line, runtime)
    assert scan["count"] == 1, "%s scanned as %r" % (line, scan["findings"])
    assert kind in scan["findings"][0]["kinds"]


# An ordinary write must stay invisible, or the guard trips on every deploy script
# and gets acknowledged reflexively. The ``-Format`` row pins that -Force is matched
# as a parameter prefix, not as any word that starts with "-Fo".
_ORDINARY_WRITES = [
    ("powershell", "Copy-Item $src $dst"),
    ("powershell", "Copy-Item $src $dst -Recurse"),
    ("powershell", "Copy-Item $src $dst; Get-Date -Format o"),
    ("powershell", "Set-Content -Path $p -Value $v -Force"),
    ("powershell", "Out-File -FilePath $p -Force"),
    ("any", "copy a.txt b.txt"),
    ("any", "xcopy src dst /E /I"),
    ("any", "robocopy src dst /E"),
    ("sh", "cp -r src dst"),
    ("python", "shutil.copytree(src, dst)"),
    ("python", "shutil.copy2(src, dst)"),
]


@pytest.mark.parametrize("runtime,line", _ORDINARY_WRITES)
def test_an_ordinary_write_is_not_counted(runtime, line) -> None:
    """No overwrite flag, no count: the common path keeps the old behaviour exactly."""
    scan = ct._scan_destructive_script(line, runtime)
    assert scan["count"] == 0, "%s scanned as %r" % (line, scan["findings"])


def test_a_forced_copy_is_refused_until_acknowledged_and_clobbers_nothing(sandbox) -> None:
    """The measured shape, end to end: the file it would replace is still there.

    And once acknowledged it runs -- the guard is a review, not a wall.
    """
    script = 'Copy-Item -LiteralPath "%s" -Destination "%s" -Force' % (
        sandbox / "junk" / "a.tmp", sandbox / "keep.txt")
    payload = _run(ct.run_ps1_script(str(sandbox), script))
    assert payload["error_name"] == "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED", payload
    assert (sandbox / "keep.txt").read_text(encoding="utf-8") == "PRECIOUS"

    payload = _run(ct.run_ps1_script(
        str(sandbox), script, confirm_destructive=payload["destructive_count"], skip_backup=True))
    assert payload["ok"] is True, payload
    assert (sandbox / "keep.txt").read_text(encoding="utf-8") == "disposable"


# --- 2026-09-24 (#26 / #30): a python body is judged as CODE ---------------------
#
# The python rows were a regex over the raw text, and it was wrong in BOTH
# directions. Measured on the old scan: a string literal ``x = "path.unlink()"``,
# a comment, ``sys.path.remove(...)`` and ``items.remove(1)`` each scored 1 -- a
# CA running a harmless probe was asked to confirm a deletion -- while
# ``from os import remove as rm; rm(p)`` and ``map(os.unlink, paths)`` scored 0
# and would have run unannounced. A false positive trains the reflexive confirm;
# a false negative costs files.

_PY_DELETES = [
    ("import os\nos.remove(p)\n", "os_remove"),
    ("import os as o\no.remove(p)\n", "os_remove"),
    ("from os import remove as rm\nrm(p)\n", "os_remove"),
    ("from shutil import rmtree as nuke\nnuke(p)\n", "rmtree"),
    ("import shutil\n(shutil.rmtree\n (p))\n", "rmtree"),
    ("import os\nlist(map(os.unlink, paths))\n", "os_remove"),
    ("import atexit\nfrom shutil import rmtree\natexit.register(rmtree, d)\n", "rmtree"),
    ("p.unlink()\n", "os_remove"),
    # A receiver the scan cannot PROVE lives in memory still counts: sftp.remove,
    # fs.remove and a parameter's .remove may all delete a file.
    ("def f(xs):\n    xs.remove(1)\n", "os_remove"),
    ("lst = []\nlst = get()\nlst.remove(1)\n", "os_remove"),
    ("class C:\n    def f(self, p):\n        self.fs.remove(p)\n", "os_remove"),
    ("from somewhere import *\nitems.remove(p)\n", "os_remove"),
    ('import os\nx = f"{os.remove(p)}"\n', "os_remove"),
    ('open(p, "r+").truncate()\n', "os_remove"),
]


@pytest.mark.parametrize("body, kind", _PY_DELETES)
def test_a_python_delete_is_counted_however_it_is_spelled(body, kind) -> None:
    scan = ct._scan_destructive_script(body, "python")
    assert scan["count"] == 1, scan
    assert scan["findings"][0]["kind"] == kind


_PY_NOT_A_DELETE = [
    'print(1)\nimport pathlib\nx = "path.unlink()"\n',  # the #26 repro, as measured
    'x = """\nshutil.rmtree(d)\n"""\n',
    "# os.remove(p)\nprint(1)\n",
    'import os, sys\nsys.path.remove(os.path.join(ROOT, "bin"))\n',  # #30
    "items = [1, 2]\nitems.remove(1)\n",
    "s = set()\ns.remove(1)\n",
    "[1, 2].remove(1)\n",
]


@pytest.mark.parametrize("body", _PY_NOT_A_DELETE)
def test_python_data_and_an_in_memory_remove_are_reported_not_counted(body) -> None:
    scan = ct._scan_destructive_script(body, "python")
    assert scan["count"] == 0, scan
    assert scan["prose_matches"], "a mention is reported, never silently dropped"
    assert all(match["why_not_counted"] for match in scan["prose_matches"])


_PY_RUNS_A_STRING = [
    'exec("os.remove(p)")\n',
    'import subprocess, sys\nsubprocess.run([sys.executable, "-c", "import os; os.remove(p)"])\n',
    'src = "os.remove(p)"\nopen("h.py", "w").write(src)\n',
    'import runpy\ncode = "shutil.rmtree(d)"\n',
]


@pytest.mark.parametrize("body", _PY_RUNS_A_STRING)
def test_a_string_counts_again_once_the_script_can_run_one(body) -> None:
    assert ct._scan_destructive_script(body, "python")["count"] == 1


def test_an_unparseable_python_body_keeps_the_text_scan() -> None:
    assert ct._scan_destructive_script("os.remove(p) (((\n", "python")["count"] == 1


def test_the_python_text_rows_come_from_the_one_callee_table() -> None:
    """run_project_command reads ``python -c "..."`` as TEXT (runtime "any"), so
    the regex rows must know every name the AST knows: one table, two readers."""
    for name, kind in ct._PY_DESTRUCTIVE_CALLEES.items():
        scan = ct._scan_destructive_script(f'python -c "import x; x.{name}(p)"\n', "any")
        assert scan["count"] == 1, (name, scan)
        assert kind in scan["findings"][0]["kinds"], (name, scan)


def test_a_harmless_probe_that_names_a_delete_in_a_string_just_runs(sandbox) -> None:
    """#26 end to end: this is the shape of the body a CA sent, and it was refused."""
    body = 'print("probe-ran")\nimport pathlib\nx = "path.unlink()"\n'
    payload = _run(ct.run_project_test_script(str(sandbox), body, runtime="python"))
    assert payload["ok"] is True, payload
    assert "probe-ran" in payload["stdout"]


# --- 2026-09-25 (#37): a command can start inside a shell introducer -------------
#
# Every statement-anchored row had its own copy of the "command starts here" test,
# and every copy missed the argument of a shell introducer: ``powershell -c "ri x"``
# and ``cmd /c del x`` delete, yet scored 0. One matcher (``_CMD_START``) now owns
# the question. Each row is pinned by its own kind, so a row that drops the shared
# matcher names itself.

_INTRODUCED_SHAPES = [
    ("powershell", 'powershell -NoProfile -c "ri x -Recurse"', "remove_item"),
    ("powershell", 'pwsh -Command "clc log.txt"', "clear_content"),
    ("powershell", 'powershell -c "mi a b"', "move_item"),
    ("powershell", 'powershell -c "cp a b -Force"', "copy_item_force"),
    ("sh", "bash -c 'rm x'", "rm"),
    ("sh", "sh -c 'rmdir d'", "rmdir"),
    ("sh", "bash -c 'mv a b'", "sh_destructive"),
    ("any", 'cmd /c del "x"', "remove_item"),
    ("any", "cmd.exe /k erase x", "remove_item"),
]


@pytest.mark.parametrize("runtime,line,kind", _INTRODUCED_SHAPES)
def test_a_shell_introducer_argument_is_a_command_start(runtime, line, kind) -> None:
    scan = ct._scan_destructive_script(line, runtime)
    assert scan["count"] == 1, "%s scanned as %r" % (line, scan["findings"])
    assert kind in scan["findings"][0]["kinds"], scan["findings"]


# The switch opens a command only when a SHELL owns it; anywhere else a quote is a
# quoted word and ``-c`` is some other program's flag. Counting these would trip the
# guard on ordinary commits and greps, and a guard that trips on everything gets
# acknowledged reflexively.
_NOT_A_COMMAND_START = [
    ("any", 'git commit -m "move the rm call"'),
    ("any", "grep -c rm notes.txt"),
    ("sh", "gcc -c rm.c"),
    ("powershell", 'Write-Host "ri is an alias"'),
]


@pytest.mark.parametrize("runtime,line", _NOT_A_COMMAND_START)
def test_a_quoted_word_or_a_non_shell_switch_is_not_a_command(runtime, line) -> None:
    scan = ct._scan_destructive_script(line, runtime)
    assert scan["count"] == 0, "%s scanned as %r" % (line, scan["findings"])


# A python call that hands a COMMAND to a process carries it as literal strings. The
# AST judged such a call by its NAME (``system`` / ``run`` delete nothing) and the text
# rows saw only quoted words, so both scored 0. The literal argv is rejoined and
# judged by the same shell matcher; a command built at run time stays unjudged.
_PY_PROCESS_DELETES = [
    'import os\nos.system("del x")\n',
    'import subprocess\nsubprocess.run(["cmd", "/c", "rd", "/s", "/q", d])\n',
    'import subprocess\nsubprocess.Popen(["powershell", "-c", "ri x"])\n',
    'import subprocess\nsubprocess.run(args=["rm", "-f", p])\n',
    'import os\nos.execvp("rm", ["rm", "x"])\n',
]


@pytest.mark.parametrize("body", _PY_PROCESS_DELETES)
def test_a_process_call_is_judged_by_the_command_it_carries(body) -> None:
    scan = ct._scan_destructive_script(body, "python")
    assert scan["count"] == 1, scan


_PY_PROCESS_NOT_A_DELETE = [
    "import subprocess\nsubprocess.run(cmd)\n",
    'import subprocess\nsubprocess.run(["git", "status"])\n',
    'import os\nos.system("echo hi")\n',
    'import subprocess\nsubprocess.run(["git", "commit", "-m", "move x"])\n',
]


@pytest.mark.parametrize("body", _PY_PROCESS_NOT_A_DELETE)
def test_a_process_call_that_carries_no_delete_is_not_counted(body) -> None:
    assert ct._scan_destructive_script(body, "python")["count"] == 0


# --- 2026-09-25 (#37): a command can ride inside a carrier the per-line scan reads
#     as opaque -------------------------------------------------------------------
#
# A -EncodedCommand base64 payload, the string arguments of a node child_process
# call, and the trailing command of an ssh line each HIDE a shell command from a
# per-line regex. Each is decoded back to the shell text it becomes and judged by
# the SAME matcher (_scan_destructive_script recursed with _scan_carriers=False).
# Each row is pinned by the kind its decoded command yields, so a row that drops
# the decode for that carrier names itself.


def _enc(command: str) -> str:
    """A PowerShell -EncodedCommand payload: base64 of UTF-16LE, as pwsh reads it."""
    import base64
    return base64.b64encode(command.encode("utf-16-le")).decode("ascii")


_ENCODED_CARRIERS = [
    ("powershell", "powershell -EncodedCommand " + _enc("Remove-Item x"), "remove_item"),
    ("any", "pwsh -enc " + _enc("ri x"), "remove_item"),
    ("any", "powershell -NoProfile -ec " + _enc("del x"), "remove_item"),
    ("any", "powershell -e " + _enc("rm x -Recurse"), "rm"),
]


@pytest.mark.parametrize("runtime,line,kind", _ENCODED_CARRIERS)
def test_an_encodedcommand_payload_is_decoded_and_judged(runtime, line, kind) -> None:
    scan = ct._scan_destructive_script(line, runtime)
    assert scan["count"] == 1, "%s scanned as %r" % (line, scan["findings"])
    assert kind in scan["findings"][0]["kinds"], scan["findings"]


_NODE_SHELL_CARRIERS = [
    ("node", 'require("child_process").execSync("del x");', "remove_item"),
    ("node", "execSync('rm x');", "rm"),
    ("node", 'spawn("cmd", ["/c", "del", "x"]);', "remove_item"),
    ("node", 'spawnSync("rm", ["x"]);', "rm"),
    ("node", 'cp.exec(\n  "del x");', "remove_item"),
    ("any", "node -e \"require('child_process').execSync('del x')\"", "remove_item"),
]


@pytest.mark.parametrize("runtime,line,kind", _NODE_SHELL_CARRIERS)
def test_a_node_child_process_call_is_judged_by_its_literal_argv(runtime, line, kind) -> None:
    scan = ct._scan_destructive_script(line, runtime)
    assert scan["count"] == 1, "%s scanned as %r" % (line, scan["findings"])
    assert kind in scan["findings"][0]["kinds"], scan["findings"]


_SSH_CARRIERS = [
    ("sh", 'ssh host "rm x"', "rm"),
    ("any", "ssh -p 22 user@host rm x", "rm"),
    ("any", "ssh -i key.pem host del x", "remove_item"),
]


@pytest.mark.parametrize("runtime,line,kind", _SSH_CARRIERS)
def test_an_ssh_line_is_judged_by_its_remote_command(runtime, line, kind) -> None:
    scan = ct._scan_destructive_script(line, runtime)
    assert scan["count"] == 1, "%s scanned as %r" % (line, scan["findings"])
    assert kind in scan["findings"][0]["kinds"], scan["findings"]


# Controls: a carrier that runs something harmless, a switch that is not the carrier
# it looks like, and a command built at run time (a variable, not a literal) must all
# stay 0 -- a guard that trips on `git status` or an -File launch gets acknowledged
# reflexively, and a static scan that claims to judge a runtime-built string lies.
_CARRIER_CONTROLS = [
    ("any", "powershell -enc " + _enc("Get-ChildItem")),        # decodes to a listing
    ("any", "powershell -ExecutionPolicy Bypass -File x.ps1"),  # -File, not -enc
    ("any", "powershell -enc $payload"),                        # variable payload
    ("node", 'execSync("git status");'),
    ("node", 'spawn("git", ["commit", "-m", "move x"]);'),
    ("node", "execSync(cmd);"),                                 # runtime-built argv
    ("any", "ssh host ls"),
    ("any", "ssh-keygen -f rm"),                                # not ssh; -f takes a file
    ("any", "ssh -V"),
]


@pytest.mark.parametrize("runtime,line", _CARRIER_CONTROLS)
def test_a_carrier_that_hides_nothing_destructive_is_not_counted(runtime, line) -> None:
    scan = ct._scan_destructive_script(line, runtime)
    assert scan["count"] == 0, "%s scanned as %r" % (line, scan["findings"])


# Declared lower bound: a shell string nested one level further down inside an
# interpreter's -c argument, or an -ArgumentList wrapper, decodes to nothing here.
# The scan says so in its own comment; this pins that these SPECIFIC shapes are the
# boundary, so a future reader does not mistake 0 for "covered".
_DECLARED_LOWER_BOUND = [
    ("any", "python -c \"import os; os.system('del x')\""),
    ("powershell", 'Start-Process cmd -ArgumentList "/c del x"'),
]


@pytest.mark.parametrize("runtime,line", _DECLARED_LOWER_BOUND)
def test_a_carrier_nested_below_the_first_level_is_a_declared_lower_bound(runtime, line) -> None:
    assert ct._scan_destructive_script(line, runtime)["count"] == 0


def test_a_decoded_carrier_that_matches_two_kinds_is_still_one_count() -> None:
    """Remove-Item matches both the powershell and the embedded-shell pattern; the
    decoded finding must merge onto ONE line so confirm_destructive stays 1."""
    scan = ct._scan_destructive_script(
        "powershell -EncodedCommand " + _enc("Remove-Item x"), "powershell")
    assert scan["count"] == 1, scan
    assert len(scan["findings"]) == 1, scan
    assert set(scan["findings"][0]["kinds"]) >= {"remove_item", "embedded_remove_item"}, scan


def test_an_unreadable_encoded_payload_decodes_to_nothing() -> None:
    """A payload that is not clean base64, or decodes to mostly non-printable text, is
    treated as unreadable -- a lower bound, never a false positive. Random bytes are
    NOT asserted empty here: utf-16-le lands random bytes in printable CJK ranges often
    enough that the 0.8-printable gate lets some through, and that is fine -- the
    scanner, not this lenient decoder, then finds no destructive command in the noise."""
    import base64
    assert ct._decode_encoded_command(_enc("Remove-Item x")) == "Remove-Item x"
    assert ct._decode_encoded_command("not!base64!") == ""
    # utf-16-le code units U+0001..U+0008: all control chars, deterministically under
    # the printable gate, so the payload is rejected as text (was a flaky os.urandom).
    control = base64.b64encode(
        bytes([1, 0, 2, 0, 3, 0, 4, 0, 5, 0, 6, 0, 7, 0, 8, 0])).decode("ascii")
    assert ct._decode_encoded_command(control) == ""


def test_an_ssh_remote_command_strips_value_options_and_the_host() -> None:
    """The host and ssh's own value-taking options are not the command; -p 22 is
    consumed as a pair, user@host is the host, and the remainder is the command."""
    assert ct._ssh_remote_command('host "rm x"') == "rm x"
    assert ct._ssh_remote_command("-p 22 user@host rm x") == "rm x"
    assert ct._ssh_remote_command("host") == ""       # no command
    assert ct._ssh_remote_command("-V") == ""         # no host, no command


def test_a_primary_finding_and_a_carrier_on_one_line_count_once() -> None:
    """When the visible text of a line is itself destructive AND it carries a second
    destructive command in a payload, the two findings merge onto the one line, so
    confirm_destructive stays 1 -- a line is the unit the caller reproduces."""
    import base64
    payload = base64.b64encode("del b".encode("utf-16-le")).decode("ascii")
    scan = ct._scan_destructive_script("Remove-Item a; powershell -enc " + payload, "any")
    assert scan["count"] == 1, scan
    assert len(scan["findings"]) == 1, scan


# ───── Fix E (2026-09-25): scan the lines the INTERPRETER runs ─────
# Each body below really deleted a scratch dir when its interpreter ran it, while
# the scanner answered count=0 (measured before the fix).

def test_python_ends_a_line_at_a_lone_cr() -> None:
    """python reads `import shutil<CR>shutil.rmtree(d)` as two lines and runs the
    rmtree; the text scan saw one line, the AST's line 2 matched none of it, and the
    call was filed as a string literal."""
    scan = ct._scan_destructive_script("import shutil\rshutil.rmtree(d)\n", "python")
    assert scan["count"] == 1, scan
    assert scan["findings"][0]["line"] == 2, scan


def test_powershell_ends_a_line_at_a_lone_cr() -> None:
    """`ri` is only an alias at the start of a command, and after a lone CR it IS at
    the start as far as PowerShell is concerned."""
    scan = ct._scan_destructive_script("Write-Output a\rri -r -fo d\n", "powershell")
    assert scan["count"] == 1, scan


def test_a_here_string_closed_by_a_cr_line_ends_there() -> None:
    """`x<CR>'@` puts the terminator at the start of a line for PowerShell, so the
    Remove-Item after it runs -- it is not here-string prose, on either surface."""
    body = "$a = @'\nx\r'@\nRemove-Item -Recurse -Force d\n$b = @'\n'@\n"
    for runtime in ("powershell", "any"):
        scan = ct._scan_destructive_script(body, runtime)
        assert scan["count"] == 1, (runtime, scan)
        assert not scan["prose_matches"], (runtime, scan)


def test_any_still_reads_a_cr_line_the_way_cmd_does() -> None:
    """cmd DELETES a CR, so `rd<CR>/s /q d` runs as `rd/s /q d`. Splitting at the CR
    for PowerShell must not lose that reading on run_project_command's surface."""
    scan = ct._scan_destructive_script('rd\r/s /q "d"', "any")
    assert scan["count"] == 1, scan
    assert scan["findings"][0]["line"] == 1, scan


def test_a_form_feed_does_not_end_a_powershell_line() -> None:
    """PowerShell reads a form feed as whitespace (measured: both arguments bound to
    ONE Set-Content), so run.ps1 is still this cmdlet's target and the body can run
    what it writes -- its here-string is not prose. str.splitlines() broke the line
    at the form feed and read the write as a data write."""
    body = ("$code = @'\nRemove-Item -Recurse -Force d\n'@\n"
            "Set-Content -Value 'notes.txt'\x0c-Path run.ps1\n")
    scan = ct._scan_destructive_script(body, "powershell")
    assert 2 in [record["line"] for record in scan["findings"]], scan
    assert 2 not in [record["line"] for record in scan["prose_matches"]], scan


def test_any_rejoins_a_cr_split_word_on_the_line_the_caller_sees() -> None:
    """cmd deletes a CR inside a word too, so `r<CR>d /s /q d` runs `rd /s /q d` --
    the CR is dropped, not read as a space. The finding lands on the LF-view line
    (3 here: the lone CR on line 1 already split it in two), because that is the
    view every other finding in this body is numbered in."""
    scan = ct._scan_destructive_script('echo a\rb\nr\rd /s /q "d"', "any")
    assert scan["count"] == 1, scan
    assert scan["findings"][0]["line"] == 3, scan


def test_bash_keeps_a_lone_cr_inside_the_line() -> None:
    """F1: bash reads a CR as part of a word, so its lines are the LF view
    (text_lines.split_lf), the view _line_of_offset numbers every carrier in.
    Splitting the body at the CR moved the rm to line 3 and quoted line `b` for the
    line-2 carrier."""
    import base64

    scan = ct._scan_destructive_script("echo a\rb\nrm -rf zz\n", "bash")
    assert [record["line"] for record in scan["findings"]] == [2], scan
    enc = base64.b64encode("Remove-Item zzzzzz -Recurse".encode("utf-16-le")).decode()
    scan = ct._scan_destructive_script("echo a\rb\npowershell -EncodedCommand " + enc + "\n", "bash")
    assert [(record["line"], record["text"][:10]) for record in scan["findings"]] == [(2, "powershell")], scan
