"""A38-B: the lint contract for code_tools.py -- and, just as much, what it is NOT.

READ THIS BEFORE "fixing ruff findings" in this repo.

The gate this repo enforces is `ruff.toml` -> [lint] select = ['E9', 'F63', 'F7']. Under that gate
`ruff check code_tools.py` answers "All checks passed!", and that is the only ruff verdict anything
here depends on. Every assertion below that names F401 / F811 / F841 / I001 / E402 runs ruff with
its OWN `--isolated --select`, DELIBERATELY OUTSIDE the gate: those rules are measurements, not
enforced checks. A count in this file therefore never means "the gate wants this fixed".

Why the file exists: the A38-B survey spent a full measurement round establishing that the
"~50 lint findings in code_tools.py" story was a gate-scope artifact -- the findings came from an
over-selected run, not from debt anyone had skipped. The baseline block in `ruff.toml` records the
outcome so no CA pays for that round again, and this test is what keeps the block honest: when the
real counts move, the note fails here instead of quietly going stale.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TARGET = "code_tools.py"
RUFF_TOML = REPO / "ruff.toml"

#: The baseline block's own format: "#   E402  17  ..." -- one rule code, one count, then prose.
_NOTE_LINE = re.compile(r"^#\s+([A-Z]\d{3})\s+(\d+)\s")

#: Error-class rules a wider select would add. All zero, asserted so "nothing fatal hides behind
#: the narrow gate" stays a measured claim instead of a remembered one.
_ERROR_CLASS = ("F821", "F822", "E711", "E712", "E722", "F632")


def _ruff(*args: str):
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", *args],
        cwd=str(REPO), capture_output=True, text=True,
    )
    if "No module named" in (proc.stderr or ""):
        pytest.skip("ruff is not installed in this interpreter")
    return proc


def _count(rule: str) -> int:
    """Findings for ONE rule, `--isolated` so the repo gate cannot narrow the answer away."""
    proc = _ruff(TARGET, "--isolated", "--select", rule, "--statistics")
    if proc.returncode == 0:
        return 0
    total = 0
    for line in proc.stdout.splitlines():
        head = line.split("\t", 1)[0].strip()
        if head.isdigit():
            total += int(head)
    return total


def _note_baseline() -> dict:
    out = {}
    for line in RUFF_TOML.read_text(encoding="utf-8").splitlines():
        match = _NOTE_LINE.match(line.strip())
        if match:
            out[match.group(1)] = int(match.group(2))
    return out


def test_the_gate_is_the_narrow_error_set_and_nothing_wider():
    text = RUFF_TOML.read_text(encoding="utf-8")
    match = re.search(r"^select = \[([^\]]*)\]", text, re.M)
    assert match, "ruff.toml no longer declares a [lint] select -- the gate is undefined"
    selected = {part.strip().strip("'\"") for part in match.group(1).split(",") if part.strip()}
    assert selected == {"E9", "F63", "F7"}, (
        "the gate is now %r; the baseline note under it was measured against E9/F63/F7, so it has "
        "to be re-measured rather than edited" % sorted(selected))


def test_code_tools_passes_the_gate_the_repo_actually_enforces():
    proc = _ruff(TARGET)
    assert proc.returncode == 0, proc.stdout


def test_the_rules_a38b_closed_stay_closed():
    """OUTSIDE the gate, on purpose. These three were the entire actionable set in code_tools.py,
    and ONE deletion closed F401 and F811 together: the session-log import whose five names the
    wrapper defs further down already shadowed. F841 was the unused see_also_macros dict."""
    live = {rule: _count(rule) for rule in ("F401", "F811", "F841")}
    assert live == {"F401": 0, "F811": 0, "F841": 0}, live


def test_no_comment_sits_inside_an_import_block():
    """I001, measured on this very file during A38-B: a comment placed INSIDE an import block makes
    isort report the block un-sorted. The note that explains the deleted import therefore lives at
    the wrapper defs, not between the imports."""
    assert _count("I001") == 0


def test_nothing_fatal_hides_behind_the_narrow_gate():
    live = {rule: _count(rule) for rule in _ERROR_CLASS}
    assert set(live.values()) == {0}, live


def test_the_ruff_toml_baseline_block_still_matches_the_live_counts():
    """The block exists so nobody re-measures this. Anchored here so it cannot go stale silently:
    add a module-level import and E402 moves, this fails, and the note gets the real number --
    which is the opposite of a digit sitting in a comment with no test behind it."""
    baseline = _note_baseline()
    assert len(baseline) == 8, (
        "expected 8 rule lines in the ruff.toml baseline block (5 from A38-B round 1 plus the E7 "
        "one-liner family E701/E702/E741 from round 2), parsed %r -- the block was edited into a "
        "shape this test can no longer read" % baseline)
    live = {rule: _count(rule) for rule in baseline}
    assert live == baseline, "ruff.toml baseline is stale: live=%r note=%r" % (live, baseline)


def test_the_note_says_out_loud_that_a_wider_run_is_not_the_gate():
    assert "OUT OF GATE BY DESIGN" in RUFF_TOML.read_text(encoding="utf-8")


def test_the_e7_one_liner_family_is_silenced_by_scope_not_by_line_noise():
    """A38-B round 2. Of 61 E701/E702 sites across code_tools.py and tools_v15.py, 59 were READ and
    kept as deliberate compact style (try/except one-liners in the T4 shims, aligned assignment
    tables, aligned dispatch returns) and 2 were real defects that got fixed. The root fix for the
    59 is the `[lint.per-file-ignores]` table in ruff.toml, NOT 59 per-line suppressions: a comment
    cannot stop the next CA from running a wider --select, but ruff answering zero can.

    Note the missing `--isolated` here -- that is the whole point. This run reads ruff.toml, so it
    measures the table. The baseline test above keeps its `--isolated`, so the raw counts stay
    visible and honest instead of being laundered away by the same table.
    """
    proc = _ruff(TARGET, "tools_v15.py", "--select", "E701,E702,E741")
    assert proc.returncode == 0, (
        "ruff still reports the E7 family on the two ignored files -- the per-file-ignores table in "
        "ruff.toml was removed, renamed or narrowed:\n%s" % proc.stdout)


def test_that_e7_ignore_is_scoped_and_hides_nothing_else():
    """Positive control for the test above. Widen the table to the whole repo and the E7 family
    disappears everywhere while that test still passes -- a suppression quietly hiding findings
    nobody measured. Measured 2026-09-20: 441 sites live outside the two named files.
    """
    proc = _ruff(".", "--select", "E701,E702,E741", "--statistics")
    assert proc.returncode != 0, (
        "the whole repo now reports zero E701/E702/E741 -- the per-file-ignores entry was widened "
        "past code_tools.py and tools_v15.py, which turns a scoped note into a blanket suppression")


# ---------------------------------------------------------------------------
# D5, 2026-09-22. Same two-test shape applied to orchestrator_server.py: one test
# that the ignore entry actually silences the rule, and one positive control that
# it is scoped. Without the second, widening the entry to the whole repo would
# leave the first one green while hiding findings nobody measured.
#
# `_count` above is hardcoded to code_tools.py on purpose -- the baseline block it
# guards is that file's -- so these use their own target rather than widening it.
# ---------------------------------------------------------------------------

SERVER = "orchestrator_server.py"


def _count_in(target: str, rule: str) -> int:
    """`_count`, for a file other than the baseline TARGET. Same `--isolated` reason."""
    proc = _ruff(target, "--isolated", "--select", rule, "--statistics")
    if proc.returncode == 0:
        return 0
    total = 0
    for line in proc.stdout.splitlines():
        head = line.split("\t", 1)[0].strip()
        if head.isdigit():
            total += int(head)
    return total


def test_the_d5_real_findings_in_the_server_stay_closed():
    """The 4 of 13 that were defects, not style. `--isolated`, so the E402 entry below
    cannot launder any of them away:

      F401 x2 -- `functools`, and the `orchestrator.core.errors as _legacy_errors`
                 alias from the strangler migration. Audited before deleting: that
                 module is pure class definitions with no import-time side effect,
                 nothing in the repo calls `__subclasses__`, and its only other
                 importer is its own test.
      F541    -- an f-string with no placeholder in the broker context block.
      F841    -- `except ... as e` where `e` was never read, so an invalid-JSON log
                 never said WHY the parse failed. Closed by USING `e`, which is why
                 this assert would also catch someone "fixing" it back to a bare
                 `except` and dropping the diagnosis again.
    """
    live = {rule: _count_in(SERVER, rule) for rule in ("F401", "F541", "F841")}
    assert live == {"F401": 0, "F541": 0, "F841": 0}, live


def test_the_server_e402_is_silenced_by_scope_not_by_line_noise():
    """9 structural late imports, same shape as code_tools.py's 17: the module registers
    tools in stages and imports each stage where it is used. No `--isolated` here, on
    purpose -- this run reads ruff.toml, so it measures the table."""
    proc = _ruff(SERVER, "--select", "E402")
    assert proc.returncode == 0, (
        "ruff still reports E402 on orchestrator_server.py -- the per-file-ignores entry "
        "in ruff.toml was removed, renamed or narrowed:\n%s" % proc.stdout)


def test_that_e402_ignore_is_scoped_and_hides_nothing_else():
    """Positive control: code_tools.py's 17 E402 are NOT covered by that entry and must
    still be reported, so the entry cannot have been widened into a blanket one."""
    proc = _ruff(".", "--select", "E402", "--statistics")
    assert proc.returncode != 0, (
        "the whole repo now reports zero E402 -- the per-file-ignores entry was widened "
        "past orchestrator_server.py, which turns a scoped note into a blanket suppression")
    assert _count_in(TARGET, "E402") > 0, (
        "code_tools.py reports zero E402 under --isolated, so the count in the ruff.toml "
        "baseline block is stale and the control above proves nothing")
