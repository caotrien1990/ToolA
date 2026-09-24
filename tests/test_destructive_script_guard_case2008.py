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
