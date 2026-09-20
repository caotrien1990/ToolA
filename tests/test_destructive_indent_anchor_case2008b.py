"""CASE-2008 one indent level deeper: a bare ``^`` anchor demanded column 0.

``_scan_destructive_script`` applies its pattern table per LINE
(``re.search(pattern, line)``), so a statement-start anchor written
``(?:^|[;|&{(]\\s*)`` meant "column 0, or immediately after a separator". Real
scripts indent -- every statement inside ``if`` / ``foreach`` / ``try`` carries
leading whitespace::

    if (Test-Path $dst) {
        Move-Item $src $dst -Force
    }

That scored ``count=0``. ``destructive`` came back False, ``confirm_destructive``
was never demanded, there was no number for the caller to reproduce, and the
pre-run backup that rides on the same verdict was never taken. That is the
CASE-2008 chain exactly -- the guard was written, then walked past by an indent.

Two aliases were rescued by accident: ``Remove-Item`` and ``Clear-Content`` each
carry a bare-word companion pattern with no anchor at all, so an indented
``Remove-Item`` was always counted. ``clc``, ``Move-Item``/``mi``/``move``/``mv``
and the plain (unflagged) half of the ``sh`` list had no companion, so for those
the hole was real and silent. ``rm -rf`` was covered from a different direction
by ``_DESTRUCTIVE_EMBEDDED_SHELL``'s bare-word ``\\brm\\s+-[a-zA-Z]*[rf]``, which
is why the positive controls below use ``rm "$f"`` with no flags: that is the
shape nothing caught.

The fix is the anchor itself -- ``(?:^\\s*|[;|&{(]\\s*)`` at all seven sites --
not a per-alias patch, because the next alias copied from an old row would
inherit the same hole. ``test_no_pattern_site_keeps_the_bare_caret_anchor`` is
the assertion that actually holds that line; the parametrized cases only show
what it costs when it slips.

On the negative controls: a ``#`` comment is NOT globally exempt here, and that
is deliberate. The bare-word companions match prose by design -- the table's own
comment weighs it as "a false positive costs one extra call carrying
confirm_destructive, a false negative costs files" -- so ``# Remove-Item ...``
counts today and ``# Move-Item ...`` counts now that Move-Item has the companion
its siblings had. Only here-strings are exempt, via ``prose_matches``. So the
negative controls below use the aliases that have no companion, where ``^\\s*``
is the only thing that could have promoted a comment to a command: it must not,
because ``#`` is not whitespace.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import code_tools as ct  # noqa: E402

# The shape being outlawed, spelled once. A bare ``^`` followed by the
# separator class is a column-0 demand on a per-line scan.
_BARE_CARET = "(?:^|"


def _scan(body: str, runtime: str) -> dict:
    return ct._scan_destructive_script(body, runtime)


@pytest.mark.parametrize(
    ("body", "runtime", "kind"),
    [
        ("if ($ok) {\n    Move-Item $src $dst -Force\n}\n", "powershell", "move_item"),
        ("foreach ($f in $all) {\n    mi $f $dst\n}\n", "powershell", "move_item"),
        ("try {\n    mv $a $b\n} catch {}\n", "powershell", "move_item"),
        ("if ($ok) {\n    clc .\\log.txt\n}\n", "powershell", "clear_content"),
        ("if ($ok) {\n    del .\\junk.tmp\n}\n", "powershell", "remove_item"),
        ("if ($ok) {\n    ri -Recurse -Force $dir\n}\n", "powershell", "remove_item"),
        ('if [ -f "$f" ]; then\n    rm "$f"\nfi\n', "sh", "rm"),
        ('if [ -d "$d" ]; then\n    rmdir "$d"\nfi\n', "sh", "rmdir"),
        ('if [ -f "$f" ]; then\n    mv "$f" /tmp/\nfi\n', "sh", "sh_destructive"),
    ],
)
def test_indented_statement_is_counted(body, runtime, kind):
    """Every one of these scored count=0 before the anchor was widened."""
    scan = _scan(body, runtime)
    assert scan["destructive"] is True, f"indented {kind} scored 0: {scan}"
    kinds = {k for finding in scan["findings"] for k in finding["kinds"]}
    assert kind in kinds, f"expected kind {kind!r}, got {sorted(kinds)}"


@pytest.mark.parametrize(
    ("body", "runtime"),
    [
        ("Move-Item $src $dst -Force\n", "powershell"),
        ("clc .\\log.txt\n", "powershell"),
        ("Remove-Item -Recurse -Force $dir\n", "powershell"),
        ('rm "$f"\n', "sh"),
        ("$x = 1; Move-Item $a $b\n", "powershell"),
        ("Get-ChildItem $d | ForEach-Object { del $_ }\n", "powershell"),
    ],
)
def test_already_working_shapes_still_counted(body, runtime):
    """Widening an anchor must not move what column 0 and the separators caught."""
    assert _scan(body, runtime)["count"] >= 1


@pytest.mark.parametrize(
    ("body", "runtime"),
    [
        ("    # clc .\\log.txt\n", "powershell"),
        ("    # mi $src $dst\n", "powershell"),
        ("    # del .\\junk.tmp\n", "powershell"),
        ('    # rmdir "$d"\n', "sh"),
    ],
)
def test_commented_alias_without_a_companion_is_not_counted(body, runtime):
    """``^\\s*`` allows whitespace, not ``#`` -- a comment is not a command."""
    scan = _scan(body, runtime)
    assert scan["count"] == 0, f"a comment was counted as a command: {scan}"
    assert scan["destructive"] is False


def test_an_inert_indented_line_is_still_zero():
    body = "if ($ok) {\n    $count = $count + 1\n}\n"
    assert _scan(body, "powershell")["count"] == 0


def test_no_pattern_site_keeps_the_bare_caret_anchor():
    """The root assertion, and the only one that survives a new alias.

    Per-alias cases go stale the moment the table grows. This reads the live
    tables and the live function body, so a pattern copied from an old row fails
    here instead of in the next incident report.
    """
    offenders: list[str] = []
    tables: dict[str, list[str]] = {
        "_DESTRUCTIVE_PATTERNS": [
            pattern
            for group in ct._DESTRUCTIVE_PATTERNS.values()
            for pattern, _kind in group
        ],
    }
    for name in ("_DESTRUCTIVE_COMMON", "_DESTRUCTIVE_EMBEDDED_SHELL"):
        group = getattr(ct, name, None)
        if group:
            tables[name] = [pattern for pattern, _kind in group]
    for name, patterns in tables.items():
        offenders += [f"{name}: {p}" for p in patterns if _BARE_CARET in p]

    # The footgun detector carries its own inline copy of the remove_item
    # pattern, which is a seventh site no table exposes.
    source = inspect.getsource(ct._scan_destructive_script)
    for lineno, line in enumerate(source.splitlines(), 1):
        if _BARE_CARET in line:
            offenders.append(f"_scan_destructive_script+{lineno}: {line.strip()}")

    assert not offenders, (
        "a bare ^ anchor demands column 0 on a per-line scan, so an indented "
        "statement scores 0:\n  " + "\n  ".join(offenders)
    )


def test_move_item_has_the_bare_word_companion_its_siblings_had():
    """Remove-Item and Clear-Content had one; Move-Item now does too."""
    powershell = [pattern for pattern, _kind in ct._DESTRUCTIVE_PATTERNS["powershell"]]
    assert r"(?i)\bMove-Item\b" in powershell
