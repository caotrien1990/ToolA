"""A34 W2 - pin the tool-name / hint separation that `ca_next_tool` introduces.

The problem, measured 2026-09-18 with an ast walk over the production tree (same scanner
shape tests/test_a31_autochain_registry.py uses, and with orchestrator_server imported so
the LATE-registered git/cron tools are in TOOL_MAP -- 258 tools, not the 235 code_tools
registers on its own):

  259 distinct ca_next_action literals over 332 sites
    8  are a bare tool name                 -> "refresh_project_map", "list_sessions"
   53  resolve to a live tool, 38 distinct  -> "use_write_fix_or_choose_new_path"
   28  are the SAME imperative shape and name NO tool at all
                                            -> "use_partial_then_narrow_regex_scope"

Those 28 are the whole bug. `use_write_fix_or_choose_new_path` and
`use_partial_then_narrow_regex_scope` are indistinguishable by shape, so a CA reading
ca_next_action -- the field it reads FIRST on an error -- cannot tell what it may call from
what it must merely read, and a wrong guess costs a round trip against a tool that does not
exist.

The fix keeps ca_next_action's documented contract (free-form advisory prose, NOT an enum;
see the comment above AUTO_CHAIN_RULES in code_tools.py and the AUTO-CHAIN section of
examples/CORE_RULE.txt) and moves the callable half to its own field:

    ca_next_tool PRESENT  => that name exists in the live registry; it is callable.
    ca_next_tool ABSENT   => ca_next_action is advice to read, not a tool to call.

This file pins that in four directions, because any one alone can pass while the feature is
broken:

  1. resolution   - the resolver's rules (imperative prefix, leading run only, longest-first,
                    self-reference) on a synthetic registry, so a table here cannot rot with
                    the real one.
  2. population   - re-derived over the LIVE literals every run: not one resolution may name
                    something outside TOOL_MAP, and the imperative-shaped phantoms must stay
                    unresolved. Both halves carry a positive control, because "0 findings"
                    and "the scan resolved nothing at all" look identical otherwise.
  3. injection    - the annotate step's own rules (never overwrite, respect a fields=
                    projection), plus an ast check that the dispatch funnel calls it for
                    EVERY tool rather than from inside a `tool_name == "..."` branch, which
                    is how the two pre-existing git hints are written.
  4. registration - the field is a registered AUTO_CHAIN_RULES row and the rule file states
                    the ABSENT-means-advice half, or a CA is never told the rule.
"""

import ast
import os
import re
from pathlib import Path

import pytest

import code_tools as ct
import orchestrator_server as srv  # noqa: F401 -- triggers the late git/cron registrations
from orchestrator.core.next_tool import annotate_next_tool, resolve_next_tool

REPO = Path(__file__).resolve().parents[1]

# Not [A]'s production surface: virtualenvs, caches, vendored + runtime trees, and the
# test/doc/example trees that legitimately quote these literals (this file included).
_SKIP_DIRS = {
    ".git", "venv312", "venv_ocr", "node_modules", ".orchestrator_cache",
    "__pycache__", "_iso_campaign", ".council", "tests", "docs", "examples",
}

# The three verbs the measured population uses, with either separator. Kept independent of
# the resolver's own regex on purpose: if the resolver narrows its verb set, the phantom scan
# below must still classify the same literals and start failing, not silently agree.
_IMPERATIVE = re.compile(r"^(?:call|use|run)[\s_]")

# The two measured self-referential sites, pinned by their literal (identity), never by
# file:line -- a line number rots on the next edit above it. They DISAGREE about what should
# happen, which is why "an auto-chain may never point at itself" would have been wrong:
#
#   tools_v15.normalize_ca_registration emits _SELF_CHAIN -- a real self-chain, the dry_run
#   preview telling the CA to re-call the SAME tool with dry_run=false. Suppressing it would
#   delete correct advice.
#
#   code_tools.workflow_reset emits _SELF_ECHO -- a STATUS sentence ("reset complete, do not
#   submit the previous terminal step") whose first identifier run merely happens to be the
#   tool's own name. Emitting ca_next_tool here would advise resetting the workflow again.
#
# The imperative verb is the difference, and it is the only thing separating them.
_SELF_CHAIN = "call_normalize_ca_registration_with_dry_run_false_to_apply"
_SELF_ECHO = "workflow_reset_complete_no_submit_previous_terminal_step"


def _prod_py_files():
    for base, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in names:
            if name.endswith(".py"):
                yield Path(base) / name


def _payload_str_literals(tree, key):
    """Yield (value, lineno) for every string literal written under `key`.

    Same three shapes as the A31 scanner: a dict entry, an `_ok(key=...)` keyword, and an
    `out["key"] = ...` subscript assignment.
    """
    for node in ast.walk(tree):
        pairs = []
        if isinstance(node, ast.Dict):
            pairs = [(k.value, v) for k, v in zip(node.keys, node.values)
                     if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        elif isinstance(node, ast.Call):
            pairs = [(kw.arg, kw.value) for kw in node.keywords if kw.arg]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            pairs = [(t.slice.value, node.value) for t in targets
                     if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                     and isinstance(t.slice.value, str)]
        for found, val in pairs:
            if found == key and isinstance(val, ast.Constant) and isinstance(val.value, str):
                yield val.value, val.lineno


@pytest.fixture(scope="module")
def population():
    """{literal: [relpath:lineno]} for every ca_next_action literal production code writes."""
    lits = {}
    for path in _prod_py_files():
        rel = path.relative_to(REPO).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for value, lineno in _payload_str_literals(tree, "ca_next_action"):
            lits.setdefault(value, []).append("%s:%d" % (rel, lineno))
    return lits


# ------------------------------------------------------------------ 1. resolution rules

# A frozen registry: the resolver's rules must be provable without depending on which real
# tools happen to exist today.
_FAKE = {"write_fix", "git_status", "git", "search_code", "workflow_reset", "workflow"}


@pytest.mark.parametrize("literal,expected", [
    ("write_fix", "write_fix"),                       # a bare registered name
    ("  write_fix  ", "write_fix"),                   # surrounding whitespace
    ("call_write_fix", "write_fix"),                  # imperative, underscore separator
    ("use_write_fix", "write_fix"),
    ("run write_fix", "write_fix"),                   # imperative, space separator
    ("use_write_fix_or_choose_new_path", "write_fix"),  # tool + trailing advice
    ("git_status then git_commit", "git_status"),      # first thing named is what to do next
    ("workflow_get_current_state", "workflow"),        # longest available leading run
])
def test_the_resolver_answers_each_shape_the_population_really_uses(literal, expected):
    assert resolve_next_tool(literal, _FAKE) == expected


@pytest.mark.parametrize("literal", [
    "use_partial_then_narrow_regex_scope",   # imperative shape, names nothing registered
    "narrow_the_scope_then_write_fix",       # names a tool, but NOT in the leading run
    "retry with a smaller range",            # ordinary advice
    "write_fixture",                         # a longer identifier that merely starts alike
])
def test_the_resolver_refuses_everything_that_does_not_lead_with_a_tool(literal):
    assert resolve_next_tool(literal, _FAKE) == ""


def test_longest_leading_run_wins_so_the_specific_tool_is_chosen():
    # Both "git" and "git_status" are registered in _FAKE. Shrinking from the longest run
    # picks the specific one; growing from the shortest would always answer "git".
    assert resolve_next_tool("git_status_after_the_write", _FAKE) == "git_status"
    assert resolve_next_tool("git_only", _FAKE) == "git"


@pytest.mark.parametrize("literal", [None, 5, b"write_fix", "", "   ", "\n"])
def test_the_resolver_is_safe_on_every_non_literal_input(literal):
    assert resolve_next_tool(literal, _FAKE) == ""


@pytest.mark.parametrize("registry", [None, set(), {}, ()])
def test_an_empty_registry_resolves_nothing_rather_than_guessing(registry):
    assert resolve_next_tool("write_fix", registry) == ""


def test_the_two_self_referential_literals_disagree_and_both_are_honoured():
    # THE measured case for the current_tool rule. If either half flips, the rule has been
    # replaced by a blanket one and one of these two sites is now wrong.
    assert resolve_next_tool(_SELF_ECHO, ct.TOOL_MAP, "workflow_reset") == "", \
        "a status sentence opening with its own tool name is an echo, not an instruction"
    assert resolve_next_tool(_SELF_CHAIN, ct.TOOL_MAP, "normalize_ca_registration") == \
        "normalize_ca_registration", \
        "a dry_run preview telling the CA to re-call with dry_run=false is a real self-chain"
    # Positive control on the guard itself: without the current_tool context the echo DOES
    # resolve, so the assertion above is testing the guard and not a broken resolver.
    assert resolve_next_tool(_SELF_ECHO, ct.TOOL_MAP) == "workflow_reset"
    # And the guard is narrow: it only ever fires on the tool's own name.
    assert resolve_next_tool(_SELF_ECHO, ct.TOOL_MAP, "write_fix") == "workflow_reset"


def test_both_self_referential_literals_are_still_emitted_by_production_code(population):
    # An allowlist-shaped constant is only honest while its subject exists. If a site is
    # deleted or reworded, this fails and the rule above has to be re-justified against
    # whatever the population looks like then.
    for literal in (_SELF_CHAIN, _SELF_ECHO):
        assert literal in population, \
            "%r is no longer emitted anywhere; re-derive the current_tool rule" % literal


# --------------------------------------------------------------- 2. the live population

def test_no_resolution_over_the_live_population_names_a_nonexistent_tool(population):
    """The claim ca_next_tool makes: if it is present, that tool exists. Derived, not listed."""
    resolved = {lit: resolve_next_tool(lit, ct.TOOL_MAP) for lit in population}
    resolved = {lit: name for lit, name in resolved.items() if name}
    bogus = {lit: name for lit, name in resolved.items() if name not in ct.TOOL_MAP}
    assert not bogus, "ca_next_tool would point at a tool that does not exist: %r" % bogus
    # Positive controls: an empty registry, a broken walker, or a resolver that returns ""
    # unconditionally would satisfy the assertion above vacuously.
    assert len(ct.TOOL_MAP) > 100, "TOOL_MAP looks unpopulated: %d" % len(ct.TOOL_MAP)
    assert len(population) > 150, "the ca_next_action scan found almost nothing"
    assert len(resolved) >= 20, "the resolver answered on almost nothing: %d" % len(resolved)
    assert len({*resolved.values()}) >= 10, "every resolution collapsed to a handful of tools"


def test_imperative_shaped_literals_that_name_no_tool_stay_unresolved(population):
    """The 28. These are what a CA would misread as callable if ca_next_action were alone."""
    phantoms = {
        lit for lit in population
        if _IMPERATIVE.match(lit) and not resolve_next_tool(lit, ct.TOOL_MAP)
    }
    # Positive control: the whole point is that this class is large. If the count collapses,
    # either the scan broke or the resolver started answering on prose -- both are failures,
    # and the second is exactly the false positive ca_next_tool must never produce.
    assert len(phantoms) >= 15, \
        "expected a substantial imperative-but-not-a-tool class, found %d" % len(phantoms)
    assert "use_partial_then_narrow_regex_scope" in phantoms, \
        "the documented example resolved to something; the resolver got looser"
    # Every one of them is an emitted literal, so a CA really does see this shape.
    for lit in phantoms:
        assert population[lit], lit


def test_a_bare_tool_name_is_a_real_and_recognised_subpopulation(population):
    """The easy 8: ca_next_action IS just a tool name. These must always resolve."""
    bare = {lit for lit in population if lit in ct.TOOL_MAP}
    assert len(bare) >= 5, "no bare-tool-name literals found; the scan is suspect"
    for lit in bare:
        assert resolve_next_tool(lit, ct.TOOL_MAP) == lit


# ------------------------------------------------------------------- 3. the injection

def test_annotate_writes_the_key_and_returns_what_it_wrote():
    payload = {"ok": False, "ca_next_action": "use_write_fix_or_choose_new_path"}
    assert annotate_next_tool(payload, ct.TOOL_MAP) == "write_fix"
    assert payload["ca_next_tool"] == "write_fix"


def test_annotate_adds_nothing_when_the_action_is_only_advice():
    payload = {"ok": False, "ca_next_action": "use_partial_then_narrow_regex_scope"}
    assert annotate_next_tool(payload, ct.TOOL_MAP) == ""
    assert "ca_next_tool" not in payload, \
        "the ABSENCE of the key is the signal; an empty string is not absence"


def test_annotate_never_overwrites_a_value_a_tool_computed_itself():
    payload = {"ca_next_action": "use_write_fix_or_choose_new_path",
               "ca_next_tool": "read_file_range"}
    assert annotate_next_tool(payload, ct.TOOL_MAP) == ""
    assert payload["ca_next_tool"] == "read_file_range"


@pytest.mark.parametrize("payload", [None, [], "text", 5])
def test_annotate_ignores_a_payload_that_is_not_a_dict(payload):
    assert annotate_next_tool(payload, ct.TOOL_MAP) == ""


@pytest.mark.parametrize("fields,expected", [
    (None, "write_fix"),                            # no projection -> emit
    (set(), "write_fix"),                           # empty projection -> no fields= was sent
    ({"ca_next_tool"}, "write_fix"),                # explicitly requested
    ({"ok", "ca_next_tool", "error"}, "write_fix"),
    ({"ca_next_tool.anything"}, "write_fix"),       # dotted R101 spec on the same key
    ({"ok", "ca_next_action"}, ""),                 # projected payload -> must NOT sneak in
    ({"ok", "error_name"}, ""),
])
def test_annotate_respects_an_explicit_fields_projection(fields, expected):
    """R101: the payload reaching the funnel is already projected, so a key added afterwards
    would bypass the caller's token budget entirely."""
    payload = {"ok": False, "ca_next_action": "use_write_fix_or_choose_new_path"}
    assert annotate_next_tool(payload, ct.TOOL_MAP, "", fields) == expected
    assert ("ca_next_tool" in payload) is bool(expected)


def _funnel_call_site():
    """(annotate lineno, enclosing FunctionDef, parent map) for the dispatch funnel call."""
    src = (REPO / "orchestrator_server.py").read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse(src)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "annotate_next_tool"]
    assert len(calls) == 1, \
        "expected exactly one funnel injection, found %d" % len(calls)
    return calls[0], parents


def test_the_funnel_injects_for_every_tool_not_from_inside_a_tool_name_branch():
    """The two pre-existing git hints live inside `if tool_name == "git_status"` blocks. This
    one must NOT: `_result` is the parsed payload for every tool, ok or error, and that is the
    only reason one insertion covers the whole surface."""
    call, parents = _funnel_call_site()
    node = call
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.If):
            names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
            assert "tool_name" not in names, (
                "the ca_next_tool injection is nested inside a tool_name test at line %d, so "
                "it no longer covers every tool" % node.lineno)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            break
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    # Ordering: the ledger records the payload, so the key must be attached before it is
    # written or the ledger and the response disagree about what the CA was told.
    ledger = [n.lineno for n in ast.walk(node)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "_write_ledger_finish_async"]
    assert ledger, "the funnel no longer writes the ledger; re-locate the injection point"
    assert call.lineno < min(ledger), \
        "ca_next_tool is attached after the ledger write, so the ledger loses it"


def test_the_funnel_passes_the_live_registry_and_the_current_tool():
    """A funnel that passed a stale or hand-built list would make every derivation claim in
    this file untrue, and a funnel that dropped current_tool would re-emit the status echo."""
    call, _parents = _funnel_call_site()
    args = [ast.dump(a) for a in call.args]
    assert any("TOOL_MAP" in a for a in args), \
        "the funnel must resolve against the live TOOL_MAP, not a private list"
    assert any("tool_name" in a for a in args), \
        "the funnel must pass tool_name so a self-referential echo can be suppressed"


def test_the_late_registered_git_tools_are_in_the_registry_the_funnel_uses():
    """code_tools alone does not register the git tools -- orchestrator_server does, at import.
    Without that, `ca_next_action="git_commit"` would resolve to nothing and the oldest
    auto-chain in the codebase would carry no ca_next_tool."""
    for tool in ("git_status", "git_stage", "git_commit", "git_diff"):
        assert tool in ct.TOOL_MAP, "%s missing: the late registrations did not run" % tool


@pytest.mark.parametrize("action,expected", [
    ("git_commit", "git_commit"),                     # git_status staged>0 branch
    ("git_stage", "git_stage"),                       # git_diff worktree branch
    ("git_status then git_commit", "git_status"),      # git_diff staged branch
])
def test_the_hints_the_funnel_itself_already_sets_all_carry_a_callable_tool(action, expected):
    """The three ca_next_action values orchestrator_server sets for git_status / git_diff are
    the ones a CA meets most often, so they are exactly where a silent "" would hurt."""
    payload = {"ca_next_action": action}
    assert annotate_next_tool(payload, ct.TOOL_MAP) == expected
    assert payload["ca_next_tool"] == expected


# ----------------------------------------------------------------- 4. the contract side

def test_ca_next_tool_is_a_registered_chain_field_pointing_at_its_real_module():
    row = [r for r in ct.AUTO_CHAIN_RULES if r[0] == "ca_next_tool"]
    assert len(row) == 1, "ca_next_tool must be registered exactly once"
    _field, trigger, module = row[0]
    assert module == "orchestrator/core/next_tool.py", \
        "the row must name the module that really writes the key, not the one that calls it"
    assert (REPO / module).exists()
    assert trigger.strip()


def test_ca_next_action_is_still_deliberately_not_a_chain_field():
    """The split only works while the prose field stays prose. 'Completing' the registry by
    adding ca_next_action would tell a CA to treat 259 advisory sentences as chain values."""
    assert "ca_next_action" not in ct.AUTO_CHAIN_FIELDS


def test_the_rule_file_states_the_absence_half_of_the_contract():
    """A CA reads the rule file. Naming the field is not enough: the actionable half is that
    an ABSENT ca_next_tool means ca_next_action is advice, so it must not be lifted and called."""
    text = (REPO / "examples" / "CORE_RULE.txt").read_text(encoding="utf-8", errors="ignore")
    section = re.search(r"^## AUTO-CHAIN.*?(?=^## |\Z)", text, re.M | re.S)
    assert section, "CORE_RULE.txt has no '## AUTO-CHAIN' section"
    body = section.group(0)
    assert "ca_next_tool" in body
    assert "ABSENT" in body, "the rule file never states what an absent ca_next_tool means"
    assert "ca_next_action" in body
