"""campaign-1608 (A51): regression guard for ruff ANSI-escape parse blindness.

Bug: validate_file runs ruff and parses its text with _py_error_ruff_hint, whose
rule detector matches case-insensitively behind a (?<![A-Z0-9]) lookbehind. When
the caller carries FORCE_COLOR, ruff wraps output in ANSI SGR escapes; every SGR
ends in a letter (\x1b[31m), and that trailing 'm' satisfies the lookbehind so NO
rule matches. Result slid silently toward "clean": ruff_rules_matched=[] and
ruff_fix_applicable=false while lint_warning still listed every finding.

Two layers fixed, each guarded here:
  L1 -- validate_file's ruff argv carries "--color never" (never emit ANSI).
  L2 -- _py_error_ruff_hint strips ANSI before matching (parser robust anyway).
"""
import asyncio
import re
from unittest import mock

import code_tools as ct


# Realistic ruff --output-format concise lines, one per rule under test. Each
# carries ruff's "[*]" safe-fix mark: only a marked finding promises ruff_fix.
_PLAIN_LINES = {
    "F401": "code.py:1:8: F401 [*] `os` imported but unused",
    "E401": "code.py:3:1: E401 [*] Multiple imports on one line",
    "F541": "code.py:7:7: F541 [*] f-string without any placeholders",
    "I001": "code.py:1:1: I001 [*] Import block is un-sorted or un-formatted",
}


def _ansi_wrap(line: str) -> str:
    """Mimic how ruff paints a concise line when FORCE_COLOR is set: bold+red on
    the rule code, reset after. Every SGR ends in a letter -- the exact trigger."""
    return re.sub(
        r"\b(F\d{3}|E\d{3}|I\d{3}|RUF\d{3})\b",
        lambda m: f"\x1b[1m\x1b[31m{m.group(1)}\x1b[0m",
        line,
    )


def test_plain_lines_match_expected_rule():
    for rule, line in _PLAIN_LINES.items():
        applicable, matched, hint = ct._py_error_ruff_hint(line)
        expected = "I" if rule.startswith("I") else rule
        assert applicable is True, f"{rule}: plain line should be applicable"
        assert expected in matched, f"{rule}: expected {expected} in {matched}"
        assert hint, f"{rule}: hint should be non-empty"


def test_ansi_lines_still_match():
    """The core regression: ANSI-wrapped lines MUST match (before fix -> []).
    Covers more than F401: F401, E401, F541 and the I\\d{3} isort branch."""
    for rule, line in _PLAIN_LINES.items():
        ansi = _ansi_wrap(line)
        assert "\x1b[" in ansi, "test scaffold must actually inject ANSI"
        applicable, matched, hint = ct._py_error_ruff_hint(ansi)
        expected = "I" if rule.startswith("I") else rule
        assert applicable is True, f"{rule}: ANSI line should be applicable, got {matched}"
        assert expected in matched, f"{rule}: expected {expected} in {matched} (ANSI)"


def test_plain_and_ansi_are_identical():
    """Plain and ANSI inputs must produce byte-identical (applicable, matched, hint)."""
    for rule, line in _PLAIN_LINES.items():
        plain_result = ct._py_error_ruff_hint(line)
        ansi_result = ct._py_error_ruff_hint(_ansi_wrap(line))
        assert plain_result == ansi_result, (
            f"{rule}: plain {plain_result!r} != ansi {ansi_result!r}")


def test_multiline_ansi_block_matches_all_rules():
    """A full multi-finding ruff dump, ANSI-wrapped, recovers every rule."""
    plain_block = "\n".join(_PLAIN_LINES.values())
    ansi_block = _ansi_wrap(plain_block)
    plain_result = ct._py_error_ruff_hint(plain_block)
    ansi_result = ct._py_error_ruff_hint(ansi_block)
    assert plain_result == ansi_result
    _, matched, _ = ansi_result
    for expected in ("F401", "E401", "F541", "I"):
        assert expected in matched, f"{expected} missing from {matched}"


def test_validate_file_argv_carries_no_color():
    """Layer 1: the ruff subprocess argv must carry '--color never'."""
    captured = {}

    class _FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(argv, *args, **kwargs):
        # Only capture the ruff invocation; ignore py_compile etc. (none here).
        if any("ruff" == a for a in argv):
            captured["argv"] = argv
        return _FakeCompleted()

    with mock.patch.object(ct.subprocess, "run", side_effect=_fake_run):
        asyncio.run(ct.validate_file("code_tools.py"))

    argv = captured.get("argv")
    assert argv is not None, "ruff was never invoked"
    assert "--color" in argv, f"--color flag missing from argv: {argv}"
    assert argv[argv.index("--color") + 1] == "never", (
        f"--color must be 'never', got argv: {argv}")


def test_findings_without_the_safe_fix_mark_promise_nothing():
    """F841/E402 print without "[*]": ruff has no safe fix for them, so the safe
    pass ruff_fix runs would change nothing. Plain or ANSI, no promise."""
    for line in ("code.py:5:5: F841 Local variable `x` is assigned to but never used",
                 "code.py:9:1: E402 Module level import not at top of file"):
        assert ct._py_error_ruff_hint(line) == (False, [], "")
        assert ct._py_error_ruff_hint(_ansi_wrap(line)) == (False, [], "")
