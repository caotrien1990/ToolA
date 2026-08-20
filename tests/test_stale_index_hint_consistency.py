"""A stale-index response must never tell CA to do the one thing CORE_RULE forbids.

CORE_RULE, `CA_SESSION_BOOTSTRAP.md` and every `STALE_INDEX` /
`FILE_CHANGED_OUTSIDE_ORCHESTRATOR` error in the tree say the same thing: recover
with `cache_delta` or `read_file_range(force_reseed=true)`, and never
`refresh_project_map` on a large root -- `[A:P]` is ~450MB, so a full refresh is
minutes of repo walk to repair the index for a single written file.

Four `cache_sync_warning` sites in `write/core.py` said the opposite. A CA that
wrote a file, got `cache_synced: false`, and read `action_required:
"refresh_project_map"` was stuck between the rule and the error hint with no
correct move -- reported from the field on 2026-08-20. `cache_delta` is not just
the permitted answer, it is the RIGHT one: it compares the filesystem against
INDEX for the delta, which is exactly the scope of what one write invalidated.

These tests assert the property over the source of the write tools rather than by
provoking a failed cache sync: `_update_cache_after_write` returns False only on a
persistent mtime mismatch or an internal exception, neither of which can be forced
from a test without patching the very code under test into something that no
longer resembles production.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_WRITE_CORE = _ROOT / "orchestrator" / "tools" / "write" / "core.py"

# The recovery verbs CA is allowed to be sent to for a stale index.
_PERMITTED = {"cache_delta", "read_file_range"}


def _source() -> str:
    return _WRITE_CORE.read_text(encoding="utf-8")


def test_no_write_response_directs_ca_to_refresh_a_large_root() -> None:
    """Every `action_required` in the write path must be a permitted recovery verb."""
    values = re.findall(r'"action_required"\s*\]\s*=\s*"([a-z_]+)"', _source())
    assert values, "no action_required assignments found -- has the file moved?"
    forbidden = sorted({v for v in values if v not in _PERMITTED})
    assert not forbidden, (
        "write responses set action_required=%s; CORE_RULE forbids refreshing a "
        "large root, so CA would have no legal recovery" % forbidden
    )


def test_every_cache_sync_warning_names_a_permitted_recovery() -> None:
    """The prose CA reads must agree with the structured field.

    A warning that says "call refresh_project_map" is a double-bind even when
    `action_required` is correct, because the two contradict each other and CA has
    no way to tell which one is authoritative.
    """
    source = _source()
    for match in re.finditer(r'"cache_sync_warning"\s*\]\s*=', source):
        # The assigned value is either one string literal or a parenthesised run of
        # them; take everything up to the next assignment or dedent-level `result [`.
        tail = source[match.end():match.end() + 600]
        literals = "".join(re.findall(r'"([^"]*)"', tail.split("result [")[0]))
        assert literals, "could not read the warning text at offset %d" % match.start()
        assert any(verb in literals for verb in _PERMITTED), (
            "a cache_sync_warning offers no permitted recovery: %r" % literals[:200]
        )
        # It may MENTION refresh_project_map, but only to forbid it.
        if "refresh_project_map" in literals:
            assert re.search(r"(KHONG|Never|never|Do not|do not)[^.]*refresh_project_map", literals), (
                "a cache_sync_warning tells CA to call refresh_project_map: %r"
                % literals[:200]
            )


def test_cache_delta_is_a_real_tool_so_the_hint_is_actionable() -> None:
    """Guard against fixing the double-bind by naming a verb that does not exist."""
    import ast

    tree = ast.parse((_ROOT / "code_tools.py").read_text(encoding="utf-8"))
    names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "cache_delta" in names, (
        "the stale-index hint routes CA to cache_delta; it must exist in code_tools"
    )
