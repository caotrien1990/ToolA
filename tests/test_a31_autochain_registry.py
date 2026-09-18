"""A31 Q5 - bind the auto-chain surface in three directions so none can drift alone.

Three campaigns argued whether [A] has "12 vs 13 vs 15 auto-chain rules" and the argument
was unsettleable, because there was no registry: the rules existed only as comments saying
"rule 1" and "rule 4" plus a prose list in examples/CORE_RULE.txt. code_tools.AUTO_CHAIN_RULES
is now that registry, and this file pins it from both sides:

  code emits it  ->  the registry names it  ->  the rule file mentions it

Deliberately no count is asserted anywhere. The A31 doctrine is that a number written in
prose beside the code that produces it always rots, so the fix is to delete the digit and
name the mechanism. len(AUTO_CHAIN_RULES) is therefore derived, never pinned.

Why the scanner is an ast walk and not a regex - measured 2026-09-17, both ways:

  * A line regex for '"ca_next_action": "..."' found 127 distinct literals. The ast walk
    finds 258 / 331 occurrences. The regex misses implicit concatenation across lines
    (orchestrator/tools/write/draft.py:314 splits one value over two source lines, so the
    first fragment even looks unstripped) and it misses the keyword-argument form.
  * A dict-literal-only ast walk found 2 of the 10 chain fields. Eight of them are written
    as 'out["ca_hint_commit"] = ...' - an assignment to a Subscript target, not a Dict key.
    A scanner that only reads dict literals reports a clean tree because it cannot see
    where the fields are.

So _payload_keys() covers all three shapes a payload key can take, and one test pins the
scanner itself against a synthetic module using all three. Without that test the other
tests could pass vacuously - the exact failure mode this file exists to prevent.

ca_next_action is intentionally NOT a chain field and NOT an enum: there are hundreds of
distinct literals, most of them advisory sentences rather than tool names, and the scan below
re-derives that population on every run rather than trusting a number written here. It is
pinned by SHAPE only (non-empty, stripped, single line), never by membership in a table.
"""

import ast
import os
import re
from pathlib import Path

import pytest

import code_tools as ct

REPO = Path(__file__).resolve().parents[1]

# Directories that are not [A]'s production surface: virtualenvs, caches, vendored trees,
# runtime artifacts, and the doc/test/example trees that legitimately quote these keys.
_SKIP_DIRS = {
    ".git", "venv312", "venv_ocr", "node_modules", ".orchestrator_cache",
    "__pycache__", "_iso_campaign", ".council", "tests", "docs", "examples",
}

_HINTISH = re.compile(r"^(ca_hint|ca_chain)")


def _payload_keys(tree):
    """Yield (key, value_node) for every payload key a module writes.

    Three shapes, all of them live in this codebase:
      {"ca_hint": ...}          -> ast.Dict
      _ok(ca_next_action=...)   -> ast.Call keyword
      out["ca_hint_commit"] = ...  -> ast.Assign to an ast.Subscript
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, val in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    yield key.value, val
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg:
                    yield kw.arg, kw.value
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for tgt in targets:
                if (isinstance(tgt, ast.Subscript)
                        and isinstance(tgt.slice, ast.Constant)
                        and isinstance(tgt.slice.value, str)):
                    yield tgt.slice.value, node.value


@pytest.fixture(scope="module")
def emitted():
    """What production code really emits: {field: {relpath}} and ca_next_action literals."""
    fields = {}
    next_action = {}
    for base, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in names:
            if not name.endswith(".py"):
                continue
            path = Path(base) / name
            rel = str(path.relative_to(REPO))
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            for key, val in _payload_keys(tree):
                # A34 W2: registry-driven, not a hand list. The two non-ca_hint*/ca_chain*
                # rows used to be spelled out here, so the third one (ca_next_tool) would
                # have been registered and named in the rule file yet never scanned -- the
                # row would pass vacuously. Asking AUTO_CHAIN_FIELDS keeps this scan honest
                # for every future row. The reverse direction is still independent: the
                # _HINTISH branch below catches a ca_hint*/ca_chain* key with NO row.
                if _HINTISH.match(key) or key in ct.AUTO_CHAIN_FIELDS:
                    fields.setdefault(key, set()).add(rel)
                if key == "ca_next_action" and isinstance(val, ast.Constant) \
                        and isinstance(val.value, str):
                    next_action.setdefault(val.value, []).append("%s:%d" % (rel, val.lineno))
    return {"fields": fields, "next_action": next_action}


def _auto_chain_section():
    """The AUTO-CHAIN block of the rule file - the prose side of the contract."""
    text = (REPO / "examples" / "CORE_RULE.txt").read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"^## AUTO-CHAIN.*?(?=^## |\Z)", text, re.M | re.S)
    assert match, "CORE_RULE.txt has no '## AUTO-CHAIN' section for the registry to bind to"
    return match.group(0)


def test_the_scanner_sees_all_three_shapes_a_payload_key_can_take():
    """Pin the instrument. A dict-only walk found 2 of 10 fields and looked clean."""
    tree = ast.parse(
        "def f(out):\n"
        "    a = {'ca_hint': 'x'}\n"
        "    out['ca_hint_commit'] = 'y'\n"
        "    return g(ca_next_action='z')\n"
    )
    found = dict(_payload_keys(tree))
    assert {"ca_hint", "ca_hint_commit", "ca_next_action"} <= set(found)


def test_the_registry_is_not_empty_and_names_each_field_once():
    fields = list(ct.AUTO_CHAIN_FIELDS)
    assert fields, "AUTO_CHAIN_RULES is empty: the registry cannot answer anything"
    assert len(fields) == len(set(fields)), \
        "a field is registered twice, so 'ask the registry' has two answers: %s" % fields
    for row in ct.AUTO_CHAIN_RULES:
        assert len(row) == 3, "each row is (field, trigger, module): %r" % (row,)
        field, trigger, module = row
        assert field and trigger and module, "no row may have a blank column: %r" % (row,)


def test_no_hint_field_is_emitted_without_a_registry_row(emitted):
    """Adding a new ca_hint*/ca_chain* key without registering it must fail here."""
    unregistered = {
        f: sorted(where) for f, where in emitted["fields"].items()
        if _HINTISH.match(f) and f not in ct.AUTO_CHAIN_FIELDS
    }
    assert not unregistered, \
        "emitted but not in AUTO_CHAIN_RULES: %s" % unregistered


@pytest.mark.parametrize("row", ct.AUTO_CHAIN_RULES, ids=lambda r: r[0])
def test_every_registry_row_is_really_emitted_by_its_declared_module(row, emitted):
    """A row that survives the removal of its field is worse than no row at all."""
    field, _trigger, module = row
    where = emitted["fields"].get(field)
    assert where, "%s is registered but no production module emits it" % field
    declared = module.replace("/", os.sep)
    assert declared in where, \
        "%s says it comes from %s, ast found it in %s" % (field, declared, sorted(where))


@pytest.mark.parametrize("field", ct.AUTO_CHAIN_FIELDS)
def test_every_registry_field_is_named_in_the_rule_file(field):
    """The doc side. A CA reads the rule file, so a field missing there is invisible."""
    assert field in _auto_chain_section(), \
        "%s is emitted and registered but the CORE_RULE.txt AUTO-CHAIN section never " \
        "names it, so no CA is told to read it" % field


def test_ca_next_action_is_deliberately_not_a_chain_field():
    """Guard the exclusion so nobody 'completes' the registry by adding it."""
    assert "ca_next_action" not in ct.AUTO_CHAIN_FIELDS
    section = _auto_chain_section()
    assert "NOT a chain field" in section and "NOT an enum" in section, \
        "the rule file must keep saying why ca_next_action is excluded"


def test_every_ca_next_action_literal_is_one_stripped_line(emitted):
    """Shape, never membership: the population is large enough that a value table would rot.

    A leading/trailing space or an embedded newline breaks callers that compare or log the
    value, and it is invisible in review. This is the only property worth pinning.
    """
    literals = emitted["next_action"]
    assert literals, "scanner found no ca_next_action literal at all - it is broken"
    malformed = {
        val: locs[0] for val, locs in literals.items()
        if (not val) or val != val.strip() or "\n" in val
    }
    assert not malformed, "malformed ca_next_action literals: %r" % malformed


# --- the git_diff hint, added in A31 Q5 -------------------------------------------------
#
# git_status and git_diff are one-line `async def` wrappers, so their hints are NOT part of
# the tool function: they are attached post-dispatch, on `_result`, inside the HTTP dispatch
# path in orchestrator_server.py. A per-function scan cannot see them at all. So these tests
# lift the real `if` statement out of that file with ast and EXECUTE it against synthetic
# payloads: that pins BEHAVIOUR, where a source-grep would still pass if the branch were
# inverted or the two scopes swapped.
#
# Map ast line numbers with source.split("\n"), NEVER splitlines(): orchestrator_server.py
# contains a form feed (\x0c) that splitlines() treats as a line break and the Python
# tokenizer does not, so every ast lineno after it is off by one against a splitlines() list.

_GIT_DIFF_TEST = 'tool_name == "git_diff"'


def _dispatch_branch(needle):
    """Compile the one post-dispatch `if` statement whose test line contains `needle`."""
    import textwrap

    src = (REPO / "orchestrator_server.py").read_text(encoding="utf-8", errors="replace")
    lines = src.split("\n")
    blocks = [
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.If) and needle in lines[n.lineno - 1]
    ]
    assert len(blocks) == 1, \
        "expected exactly one dispatch branch for %r, found %d" % (needle, len(blocks))
    node = blocks[0]
    seg = textwrap.dedent("\n".join(lines[node.lineno - 1:node.end_lineno]))
    return compile(seg, "<dispatch:%s>" % needle, "exec")


def _run_branch(code, tool_name, status, payload):
    scope = {"tool_name": tool_name, "_status": status, "_result": dict(payload)}
    exec(code, scope)
    return scope["_result"]


@pytest.mark.parametrize(
    "scope_value,expected_next_action",
    [
        ("staged", "git_status then git_commit"),
        ("worktree", "git_stage"),
        ("", "git_stage"),
        (None, "git_stage"),
    ],
    ids=["staged", "worktree", "empty-scope", "no-scope"],
)
def test_the_git_diff_hint_is_derived_from_the_payloads_own_scope(scope_value, expected_next_action):
    """git_tools.diff() returns `scope`, so the next step is measured, not guessed."""
    code = _dispatch_branch(_GIT_DIFF_TEST)
    payload = {"ok": True, "diff": "diff --git a/x b/x"}
    if scope_value is not None:
        payload["scope"] = scope_value
    result = _run_branch(code, "git_diff", "ok", payload)
    assert result["ca_next_action"] == expected_next_action
    hint = result["ca_hint"]
    assert hint and hint == hint.strip() and "\n" not in hint
    if scope_value == "staged":
        assert "expected_staged_hash" in hint, \
            "a staged diff must say where git_commit's hash comes from"
    else:
        assert "allow_all" in hint, \
            "a worktree diff must repeat the whitelist-only staging rule"


@pytest.mark.parametrize("diff_value", ["", "   ", "\n\n"], ids=["empty", "spaces", "newlines"])
def test_git_diff_with_nothing_to_show_carries_no_hint_at_all(diff_value):
    """Same discipline as the A30-B cap: a hint on a clean run teaches callers to ignore it."""
    code = _dispatch_branch(_GIT_DIFF_TEST)
    result = _run_branch(
        code, "git_diff", "ok", {"ok": True, "scope": "worktree", "diff": diff_value}
    )
    assert "ca_hint" not in result and "ca_next_action" not in result


def test_the_git_diff_branch_ignores_other_tools_and_error_statuses():
    code = _dispatch_branch(_GIT_DIFF_TEST)
    busy = {"ok": True, "scope": "staged", "diff": "diff --git a/x b/x"}
    assert "ca_hint" not in _run_branch(code, "git_status", "ok", busy)
    assert "ca_hint" not in _run_branch(code, "git_diff", "error", busy)


def test_the_git_diff_hint_introduces_no_new_vocabulary():
    """Whatever hint key the branch writes must already have a row in the registry."""
    code = _dispatch_branch(_GIT_DIFF_TEST)
    result = _run_branch(
        code, "git_diff", "ok", {"ok": True, "scope": "staged", "diff": "d"}
    )
    hintish = {k for k in result if _HINTISH.match(k)}
    assert hintish, "the branch must attach a hint"
    assert hintish <= set(ct.AUTO_CHAIN_FIELDS), \
        "unregistered hint field(s): %r" % sorted(hintish - set(ct.AUTO_CHAIN_FIELDS))


def test_the_rule_file_names_the_next_actions_the_git_diff_branch_really_emits():
    code = _dispatch_branch(_GIT_DIFF_TEST)
    section = _auto_chain_section()
    assert "git_diff" in section, "the AUTO-CHAIN section must mention git_diff at all"
    for scope_value in ("staged", "worktree"):
        result = _run_branch(
            code, "git_diff", "ok", {"ok": True, "scope": scope_value, "diff": "d"}
        )
        assert result["ca_next_action"] in section, \
            "the rule file must name what the code emits for scope=%s" % scope_value
