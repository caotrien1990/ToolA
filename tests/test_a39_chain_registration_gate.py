"""A39 -- the registration gate for the whole ADVISORY surface, and the truth table for every
trigger this campaign shipped.

Why this file exists. ``write_fix -> rule_drift_check`` was DESIGNED, approved, and never
built, and the way that stayed invisible for a whole campaign is the point. A31 already had a
registration gate, but it matched by SHAPE (``ca_hint*`` / ``ca_chain*``), so
``post_commit_validation`` shipped UNREGISTERED without failing anything -- and a chain that
was never written failed nothing by construction. A shape gate can only ever catch keys that
look like the keys it already knows.

So the gate here is a CLASSIFICATION, never a prefix ban, and that is a measured decision
rather than a preference. Probed 2026-09-20 with an ast walk over the production tree,
``^(ca_|post_)`` matched 27 distinct payload KEYS (keys, not sites -- one key is written at
many): 15 registered, 12 not. A gate that simply demanded registration would have been RED on
arrival for those 12, and the cheapest way to green it would have been to register DATA as
chain fields -- which makes AUTO_CHAIN_RULES lie about what a chain field is, and makes the
advisory guard in orchestrator_server (built FROM AUTO_CHAIN_FIELDS) start answering
UNKNOWN_TOOL for names that were never advisory. So every ``ca_*``/``post_*`` payload key must
be exactly one of three things, and saying WHICH is the whole job:

  1. registered in ``code_tools.AUTO_CHAIN_RULES`` -- plus free-form ``ca_next_action``, whose
     reason for staying out is written at the registry itself; or
  2. a live TOOL_MAP name used as a dict key. A description/metrics map keyed BY tool is not
     an advisory field at all (measured: ``post_write_batch_verify``, ``ca_policy_metrics``),
     and ``test_a_live_tool_name_is_never_also_a_chain_field`` is what makes that exemption
     safe instead of a hole; or
  3. named in ``_DATA_KEYS`` below, together with the reason it is data and not a next step.

A 4th, unclassified key fails here. That is the cost this file imposes on purpose: a new
advisory field is registered or explained, never merely shipped.

The second half is the truth table. Every trigger shipped in A39 is exercised on BOTH sides --
the state it must speak on and the state it must stay silent on -- because a trigger that
never fires is indistinguishable from a condition that never held, and that exact silence is
how ``write_fix -> rule_drift_check`` read as "built" for a whole campaign.
"""

import ast
import os
from pathlib import Path

import pytest

import code_tools as ct
import orchestrator_server as srv  # noqa: F401 -- late git/cron registrations join TOOL_MAP
from orchestrator.core.passive_chain import PASSIVE_CHAINS

REPO = Path(__file__).resolve().parents[1]

# The PRODUCTION tree only. tests/ and docs/ are excluded on purpose: a fixture or a doc
# example may legitimately spell a key no tool emits, and a gate that counted those would be
# measuring itself. scripts/ and examples/ are excluded for the same reason.
_SKIP_DIRS = frozenset({
    ".git", "venv312", "venv_ocr", "node_modules", ".orchestrator_cache", "__pycache__",
    "_iso_campaign", ".council", "tests", "docs", "examples", "scripts",
})

_ADVISORY_PREFIX = ("ca_", "post_")

#: The registry plus the one field deliberately left out of it. ca_next_action is free-form
#: prose written at hundreds of literals; AUTO_CHAIN_RULES is a table of structured chains.
#: Registering it would make the registry a list of every string in the tree.
REGISTERED = frozenset(ct.AUTO_CHAIN_FIELDS) | {"ca_next_action"}

#: Keys that are ca_*/post_* by NAME and data by BEHAVIOUR. The value is the reason and it is
#: load-bearing: test_every_data_exemption_still_names_a_key_the_tree_really_has deletes the
#: excuse for a key the tree no longer emits, so this map cannot rot into a blanket amnesty.
_DATA_KEYS = {
    "ca_id": "the calling agent's own id echoed back for correlation -- not a next step",
    "ca_identity": "the resolved agent identity block written into a generated MCP pack",
    "ca_liveness": "whether the CA's session still looks alive: an observation, not advice",
    "ca_activity": "the last observed CA activity -- a measurement",
    "ca_activity_observed": "when that measurement was taken",
    "ca_activity_source": "which ledger the measurement was read from",
    "ca_guidance": (
        "a nested guidance BLOCK built by _ca_guidance(), which carries its own "
        "ca_next_action INSIDE it -- registering the container would double-count one chain"
    ),
    "ca_fields_hint": (
        "R101's size/projection helper: it explains fields= on an oversized answer, which is "
        "a transport fact about THIS response and not a next tool. Already pinned by "
        "tests/test_a29_size_hint_and_ruff_preview_states.py"
    ),
    "post_attempted": "whether the restart POST was actually sent -- an outcome flag",
    "post_check": "a post-condition result value, not an instruction to act on",
}


def _payload_keys_in_tree():
    """Every ca_*/post_* string used as a payload KEY, mapped to the sites that write it.

    Four shapes, every one of them real in this tree, which is why a grep is not enough:
    a dict-literal key, a keyword argument to ``_ok()``/``_err()``, a subscript assignment
    ``result["ca_hint"] = ...``, and ``out.setdefault("ca_next_action", ...)``. The last is
    the shape three of THIS campaign's triggers use -- a scan that skipped it would call
    itself complete while the newest sites were invisible to it.
    """
    found: dict[str, list[str]] = {}
    for base, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in names:
            if not name.endswith(".py"):
                continue
            path = Path(base) / name
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except (SyntaxError, OSError):
                continue
            rel = path.relative_to(REPO).as_posix()
            for node in ast.walk(tree):
                hits: list[tuple] = []
                if isinstance(node, ast.Dict):
                    hits = [(k.value, k.lineno) for k in node.keys
                            if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                elif isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if (isinstance(tgt, ast.Subscript)
                                and isinstance(tgt.slice, ast.Constant)
                                and isinstance(tgt.slice.value, str)):
                            hits.append((tgt.slice.value, tgt.lineno))
                elif isinstance(node, ast.Call):
                    hits = [(kw.arg, getattr(kw.value, "lineno", node.lineno))
                            for kw in node.keywords if kw.arg]
                    fn = node.func
                    if (isinstance(fn, ast.Attribute) and fn.attr == "setdefault"
                            and node.args and isinstance(node.args[0], ast.Constant)
                            and isinstance(node.args[0].value, str)):
                        hits.append((node.args[0].value, node.lineno))
                for key, lineno in hits:
                    if isinstance(key, str) and key.startswith(_ADVISORY_PREFIX):
                        found.setdefault(key, []).append("%s:%d" % (rel, lineno))
    return found


_KEYS = _payload_keys_in_tree()
_KEY_IDS = sorted(_KEYS)


def _src(rel):
    return (REPO / rel).read_text(encoding="utf-8", errors="replace")


def _func(rel, name):
    """The ast node of one top-level-or-nested function, by name, or fail loudly."""
    for node in ast.walk(ast.parse(_src(rel))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    pytest.fail("%s no longer defines %s -- this guard now pins nothing" % (rel, name))


def _strings(node):
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


# --------------------------------------------------------- 0. the scan's own positive control

def test_the_scan_really_found_all_four_write_shapes_it_claims_to_read():
    """Without this, an empty or broken scan would make every gate below pass by collecting
    nothing -- which is the same failure mode the whole file exists to close."""
    assert len(_KEYS) >= 20, "scan collapsed to %d keys" % len(_KEYS)
    # subscript assign, dict literal / kwarg, and setdefault -- each proven by a key that can
    # only reach the scan through that shape.
    assert "ca_hint" in _KEYS            # result["ca_hint"] = ...
    assert "ca_next_action" in _KEYS     # both a dict key and a setdefault first-arg
    assert "post_commit_validation" in _KEYS
    assert "ca_chain_hint" in _KEYS
    for key, sites in _KEYS.items():
        assert sites, "%s recorded with no site" % key


# ------------------------------------------------------------- 1. the classification gate

@pytest.mark.parametrize("key", _KEY_IDS)
def test_every_advisory_shaped_payload_key_is_registered_or_declared(key):
    """The gate. Registered, a live tool name, or declared data with a reason -- pick one.

    This is what A31's shape gate could not do: post_commit_validation is not ca_hint*, so it
    shipped unregistered, and the advisory guard in orchestrator_server (which is built from
    AUTO_CHAIN_FIELDS) therefore did not protect it from being called as a tool.
    """
    if key in REGISTERED:
        return
    if key in ct.TOOL_MAP:
        return
    assert key in _DATA_KEYS, (
        "%r is written into a payload at %s but is neither registered in AUTO_CHAIN_RULES, "
        "nor a live tool name, nor declared in _DATA_KEYS with a reason. Register it if it "
        "advises a next step; declare it if it is data. Do not leave it unclassified -- that "
        "is exactly how post_commit_validation and the rule_drift chain stayed invisible."
        % (key, _KEYS[key][:3]))
    assert _DATA_KEYS[key].strip(), "%s is exempted with an empty reason" % key


@pytest.mark.parametrize("key", sorted(_DATA_KEYS))
def test_every_data_exemption_still_names_a_key_the_tree_really_has(key):
    """Anti-rot, in the direction the gate itself cannot see. An exemption for a key nobody
    emits any more is a standing licence for whoever next picks that name."""
    assert key in _KEYS, (
        "%r is exempted as data but no production site writes it any more -- delete the "
        "exemption rather than leaving the name pre-approved" % key)


def test_a_live_tool_name_is_never_also_a_chain_field():
    """The exemption in rule 2 is only safe while the two namespaces are disjoint. If a chain
    field were ever named after a tool, `key in TOOL_MAP` would wave a real advisory field
    through unregistered, and the gate would have a silent hole in it."""
    overlap = sorted(set(ct.TOOL_MAP) & REGISTERED)
    assert not overlap, (
        "%r is both a live tool and a registered chain field; the TOOL_MAP exemption above "
        "now hides real advisory fields" % overlap)


def test_auto_chain_fields_is_derived_from_the_rules_and_is_not_a_second_hand_list():
    assert ct.AUTO_CHAIN_FIELDS == tuple(r[0] for r in ct.AUTO_CHAIN_RULES)


def test_the_advisory_guard_is_derived_from_the_registry_so_registration_is_the_whole_fix():
    """The claim the registry rests on: adding a row EXTENDS the response_field guard for
    free. If `advisory_fields` were ever spelled out as a literal, a registered field would
    still be callable as a tool and a CA would get UNKNOWN_TOOL with no surface= hint.
    """
    tree = ast.parse(_src("orchestrator_server.py"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "advisory_fields" not in names:
            continue
        found.append(node)
        refs = [n.attr for n in ast.walk(node.value) if isinstance(n, ast.Attribute)]
        assert "AUTO_CHAIN_FIELDS" in refs, (
            "advisory_fields at orchestrator_server.py:%d no longer derives from "
            "AUTO_CHAIN_FIELDS; registering a row would stop extending the guard"
            % node.lineno)
    assert found, "advisory_fields is gone from orchestrator_server.py"


def test_the_two_fields_this_campaign_registered_are_in_the_registry():
    """post_commit_validation was built and returned while declared nowhere; ca_next_actions
    (PLURAL) was the same, and is NOT a stray duplicate of the singular -- ISS-08 created it
    so a resume that rejects BOTH the title and the mode can name both owed actions."""
    for field in ("post_commit_validation", "ca_next_actions"):
        assert field in ct.AUTO_CHAIN_FIELDS, "%s fell out of the registry" % field


def test_the_plural_next_actions_is_emitted_beside_resume_rejected_and_not_alone():
    """The reason it is a row and the free-form singular is not: it is structured, written at
    exactly ONE site under one condition, next to the list of what was rejected."""
    src = _src("code_tools.py")
    assert 'resp["ca_next_actions"]' in src
    idx = src.index('resp["ca_next_actions"]')
    window = src[idx:idx + 400]
    assert "resume_rejected" in window, (
        "ca_next_actions no longer ships beside resume_rejected; one of the two divergences "
        "is invisible again (ISS-08)")


# ------------------------------------------- 2. truth table: write_fix -> rule_drift_check

@pytest.mark.parametrize("rule_file", ct.RULE_DRIFT_SOURCES)
def test_a_rule_file_fires_on_its_path_alone_with_no_text_at_all(rule_file):
    """A rule file matches by PATH, deliberately: a CA can rewrite any part of CORE_RULE.txt
    and the doc may now disagree with the registry regardless of which lines moved."""
    reason = ct.rule_drift_trigger_reason("D:/repo/" + rule_file, "")
    assert reason, "%s no longer fires the drift trigger" % rule_file
    assert rule_file in reason


@pytest.mark.parametrize("sym", ct.RULE_DRIFT_SYMBOLS)
@pytest.mark.parametrize("reg", ct.RULE_DRIFT_REGISTRY_FILES)
def test_a_registry_file_fires_only_when_the_new_text_touches_a_registry_symbol(reg, sym):
    """And a registry file matches by path AND symbol. Path alone would fire on every
    code_tools.py edit -- the single biggest source of noise this predicate exists to avoid."""
    assert ct.rule_drift_trigger_reason("D:/repo/" + reg, "x = %s" % sym), (
        "%s + %s stopped firing" % (reg, sym))
    assert sym in ct.rule_drift_trigger_reason("D:/repo/" + reg, "x = %s" % sym)


@pytest.mark.parametrize("reg", ct.RULE_DRIFT_REGISTRY_FILES)
def test_a_registry_file_edit_that_touches_no_registry_symbol_is_silent(reg):
    assert ct.rule_drift_trigger_reason("D:/repo/" + reg, "x = 1  # unrelated edit") == ""


def test_an_unrelated_file_is_silent_even_when_it_names_every_registry_symbol():
    """The other half of the same decision: a content-only test would fire on every file that
    merely MENTIONS TOOL_MAP, which is most of this tree."""
    text = "\n".join(ct.RULE_DRIFT_SYMBOLS)
    assert ct.rule_drift_trigger_reason("D:/repo/orchestrator/tools/write/core.py", text) == ""


def test_a_windows_backslash_path_is_normalised_before_matching():
    """Every real caller passes a Windows path. A predicate that only matched forward slashes
    would be silent in production and green in every test written with posix paths."""
    assert ct.rule_drift_trigger_reason(r"D:\repo\examples\CORE_RULE.txt", "")


def test_the_reason_says_which_side_moved_instead_of_one_generic_sentence():
    doc = ct.rule_drift_trigger_reason("x/examples/CORE_RULE.txt", "")
    code = ct.rule_drift_trigger_reason("x/code_tools.py", "TOOL_MAP = {}")
    assert doc and code and doc != code
    assert "rule files" in doc
    assert "TOOL_MAP" in code


def test_the_predicate_reads_the_same_tuples_rule_drift_check_itself_reads():
    """One tuple, two readers. Two literals would drift apart the day a fourth rule file
    appears, leaving the trigger silent on exactly the edit it exists to catch."""
    fn = _func("code_tools.py", "rule_drift_check")
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "RULE_DRIFT_SOURCES" in names, (
        "rule_drift_check stopped reading RULE_DRIFT_SOURCES; the write-side trigger and the "
        "check can now disagree about what a rule file is")


@pytest.mark.parametrize("entry", ["write_fix", "write_new_file"])
def test_both_write_entry_points_call_the_predicate(entry):
    """Measured 2026-09-20: write_fix:1886 and write_new_file:2242. write_fix_html and
    replace_between_anchors need no site of their own -- they delegate to write_fix and inherit
    its hints verbatim, which is why this names two entry points and not four."""
    fn = _func("orchestrator/tools/write/core.py", entry)
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "rule_drift_trigger_reason"]
    assert calls, "%s no longer calls rule_drift_trigger_reason" % entry


@pytest.mark.parametrize("entry", ["write_fix", "write_new_file"])
def test_the_drift_branch_setdefaults_ca_next_action_and_never_assigns_it(entry):
    """A setdefault, never an assignment: the cache_delta hint above outranks this one -- a
    desynced cache breaks your NEXT read, drift only the next campaign -- and two authors
    writing one key produce advice neither of them meant."""
    fn = _func("orchestrator/tools/write/core.py", entry)
    drift_vars = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
                  for t in n.targets if isinstance(t, ast.Name)
                  if isinstance(n.value, ast.Call)
                  and isinstance(n.value.func, ast.Attribute)
                  and n.value.func.attr == "rule_drift_trigger_reason"}
    assert drift_vars, "%s no longer binds the drift reason to a name" % entry
    branches = [n for n in ast.walk(fn) if isinstance(n, ast.If)
                and isinstance(n.test, ast.Name) and n.test.id in drift_vars]
    assert branches, "%s: the drift reason is computed but never branched on" % entry
    for branch in branches:
        assigns = [n for n in ast.walk(branch) if isinstance(n, ast.Assign)
                   for t in n.targets
                   if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                   and t.slice.value == "ca_next_action"]
        assert not assigns, (
            "%s stamps ca_next_action with = instead of setdefault; it now outranks the "
            "cache_delta hint, which is the wrong way round" % entry)
        setdefaults = [n for n in ast.walk(branch) if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Attribute) and n.func.attr == "setdefault"
                       and n.args and isinstance(n.args[0], ast.Constant)
                       and n.args[0].value == "ca_next_action"]
        assert setdefaults, "%s: the drift branch names no next action at all" % entry
        assert any("rule_drift_check" in s for s in _strings(branch)), (
            "%s: the drift branch no longer names rule_drift_check" % entry)


def test_rule_drift_check_closes_the_loop_but_only_when_it_found_drift():
    """The other end of the same chain. Gated on drift_count>0 by design: a clean rule set
    carries no hint key at all, so the common path pays nothing for it."""
    fn = _func("code_tools.py", "rule_drift_check")
    gated = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        writes = [t.slice.value for n in ast.walk(node) if isinstance(n, ast.Assign)
                  for t in n.targets
                  if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                  and t.slice.value in ("ca_hint", "ca_next_action")]
        if writes:
            gated.append((node, writes))
    assert gated, "rule_drift_check emits no ca_hint/ca_next_action at all any more"
    assert any({"ca_hint", "ca_next_action"} <= set(w) for _, w in gated)
    for node, _ in gated:
        assert isinstance(node.test, ast.Name) and node.test.id == "drifts", (
            "the hint at code_tools.py:%d is no longer gated on `drifts`; a clean rule set "
            "now pays for a hint that has nothing to say" % node.lineno)
    body = [s for node, _ in gated for s in _strings(node)]
    assert any("code_value" in s for s in body), (
        "the hint stopped naming code_value -- it now reads as 'the doc is wrong', and the "
        "CODE is sometimes the side that moved")


# ---------------------------------------- 3. truth table: the two matrix tools

def test_the_matrix_hint_is_stamped_after_the_verbosity_filter():
    """The regression guard for a bug that was nearly shipped. A key written BEFORE
    _apply_verbosity is pruned out of the very response it was written for, at every verbosity
    but "full" -- so its own absence would read as "no drift".
    """
    fn = _func("code_tools.py", "audit_tool_matrix")
    verb = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "_apply_verbosity"]
    hints = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign)
             for t in n.targets
             if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
             and t.slice.value == "ca_hint"]
    assert verb, "audit_tool_matrix no longer calls _apply_verbosity; re-decide this guard"
    assert hints, "audit_tool_matrix stopped emitting ca_hint"
    assert min(hints) > max(verb), (
        "ca_hint at line %d is stamped BEFORE _apply_verbosity at line %d -- the verbosity "
        "filter will prune it and the missing key will read as a clean matrix"
        % (min(hints), max(verb)))


def test_the_matrix_hint_points_at_rule_drift_check_not_straight_at_a_write():
    """It says which side moved. Pointing a CA straight at write_fix would invite an edit to
    the doc before anyone had established the doc was the wrong side."""
    fn = _func("code_tools.py", "audit_tool_matrix")
    assert any("rule_drift_check" in s for s in _strings(fn))
    assert "rule_drift_check" in ct.TOOL_MAP


def test_the_progressive_gap_hint_derives_its_next_tool_instead_of_hardcoding_one():
    """A schema gap and a ui gap do not have the same next step, so the name is DERIVED from
    gaps[].area. A constant here would send every ui gap to audit_tool_matrix."""
    fn = _func("tools_v15.py", "progressive_matrix_check")
    setdefaults = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute) and n.func.attr == "setdefault"
                   and n.args and isinstance(n.args[0], ast.Constant)
                   and n.args[0].value == "ca_next_action"]
    assert setdefaults, "progressive_matrix_check names no next action any more"
    for call in setdefaults:
        assert len(call.args) >= 2 and isinstance(call.args[1], ast.Name), (
            "the next action at tools_v15.py:%d is a constant; it can no longer tell a schema "
            "gap from a ui gap" % call.lineno)
    derived = {s for s in _strings(fn) if s in ct.TOOL_MAP}
    assert {"audit_tool_matrix", "read_file_range"} <= derived, (
        "the two derived targets are no longer both live tools: %r" % sorted(derived))


# ------------------------------------------------- 4. truth table: the draft family

def test_the_still_failing_draft_names_both_ways_out_of_the_loop():
    """A draft is a HELD write: it ships NOTHING when its TTL expires. So the hint on the
    failure payload names the patch AND the deliberate abort -- abort is an exit, never the
    default, and a CA that only heard about patching can sit in the loop until the TTL wins.
    """
    src = _src("orchestrator/tools/write/draft.py")
    assert "DRAFT_STILL_FAILING" in src
    idx = src.index("DRAFT_STILL_FAILING")
    window = src[idx:idx + 1600]
    assert '"ca_hint"' in window, "the DRAFT_STILL_FAILING payload lost its hint"
    for tool in ("write_draft_patch", "write_draft_abort"):
        assert tool in window, "the still-failing hint no longer names %s" % tool
        assert tool in ct.TOOL_MAP


def test_the_draft_status_gate_reads_the_record_and_not_the_projection():
    """Measured decision: read last_errors from `rec`, NOT from whatever keys _view() happens
    to project. A trigger that depends on a projection goes silent the day that projection
    drops a field -- and a silent trigger here reads as "this draft is fine"."""
    tree = ast.parse(_src("orchestrator/tools/write/draft.py"))
    gates = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        writes = [t.slice.value for n in ast.walk(node) if isinstance(n, ast.Assign)
                  for t in n.targets
                  if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                  and t.slice.value == "ca_hint"]
        if not writes:
            continue
        test = node.test
        if (isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute)
                and test.func.attr == "get"
                and isinstance(test.func.value, ast.Name) and test.func.value.id == "rec"
                and test.args and isinstance(test.args[0], ast.Constant)
                and test.args[0].value == "last_errors"):
            gates.append(node.lineno)
    assert gates, (
        "no ca_hint in draft.py is gated on rec.get('last_errors') any more; the single-draft "
        "status hint now depends on _view()'s projection instead of the record")


def test_the_draft_list_hint_only_speaks_when_a_listed_draft_is_really_failing():
    src = _src("orchestrator/tools/write/draft.py")
    assert '_list_out["ca_hint"]' in src, "the draft-list hint is gone"
    idx = src.index('_list_out["ca_hint"]')
    before = src[max(0, idx - 400):idx]
    assert "if _bad" in before, (
        "the draft-list hint is no longer gated on a failing draft; a clean list now pays for "
        "advice it cannot act on")


# --------------------------------------------- 5. truth table: git_stage, and M-8's real fix

def test_git_stage_names_the_field_it_already_returns_instead_of_chaining_to_git_status():
    """M-8 was DESIGNED as `git_stage -> git_status` and the design was wrong: `after` is a
    full status(), so after.staged_hash IS the hash git_commit wants. A chain to git_status
    would have shipped a round trip [A] never needed. Measured 2026-09-20: a CA probed
    fields=staged_hash on git_stage, got fields_unknown, concluded the field did not exist and
    paid for a git_status anyway -- the value was one level down the whole time.
    """
    fn = _func("orchestrator/git_tools.py", "stage")
    strings = _strings(fn)
    hint = [s for s in strings if "staged_hash" in s and "after." in s]
    assert hint, "git_stage no longer names after.staged_hash in its hint"
    setdefaults = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute) and n.func.attr == "setdefault"
                   and n.args and isinstance(n.args[0], ast.Constant)
                   and n.args[0].value == "ca_next_action"]
    assert setdefaults, "git_stage names no next action"
    nxt = [n.args[1].value for n in setdefaults
           if len(n.args) >= 2 and isinstance(n.args[1], ast.Constant)]
    assert nxt == ["git_commit"], (
        "git_stage's next action is %r; chaining anywhere but git_commit reintroduces the "
        "round trip M-8's hint exists to remove" % nxt)


# ------------------------------------- 6. truth table: the M-2 passive row (stale graph)

def _row(tool, trigger):
    for r in PASSIVE_CHAINS:
        if r.tool == tool and r.trigger == trigger:
            return r
    pytest.fail("PASSIVE_CHAINS lost the %s:%s row" % (tool, trigger))


def test_the_stale_graph_row_is_second_so_a_missing_graph_still_wins():
    """Precedence is table order and first match wins. db_present must stay ahead: a graph
    that does not exist cannot be 'behind', and telling a CA to re-index one it never built
    would be advice for the wrong problem."""
    order = [r.trigger for r in PASSIVE_CHAINS if r.tool == "cbm_graph_status"]
    assert order.index("db_present") < order.index("stale_files"), order


def test_the_stale_graph_row_fires_on_a_positive_count_and_on_nothing_else():
    row = _row("cbm_graph_status", "stale_files")
    present = {"db_present": True}
    assert row.when({"ok": True, "result": dict(present, stale_files=9)})
    assert not row.when({"ok": True, "result": dict(present, stale_files=0)})
    # The junk table, and the one coercion that is DELIBERATE. _positive_count's own docstring
    # settles it: a bool is rejected because a count arriving as a flag is a wrong shape and
    # not the number 1, while a numeric string IS accepted -- `str` is in its isinstance tuple
    # on purpose. Measured here rather than assumed, because a first draft of this test asserted
    # "9" must be silent, and hardening a SHARED helper for an input [A] cannot produce would
    # have changed every other row that uses it to fix nothing real.
    for bad in (True, "nine", "", None, [], {}, object()):
        assert not row.when({"ok": True, "result": dict(present, stale_files=bad)}), bad
    assert row.when({"ok": True, "result": dict(present, stale_files="9")}), (
        "a numeric string stopped coercing; _positive_count changed shape and the rows that "
        "share it changed with it")
    assert not row.when({"ok": True, "result": present})
    assert not row.when({})


def test_the_stale_graph_row_points_at_a_live_indexer():
    row = _row("cbm_graph_status", "stale_files")
    assert row.next_action in ct.TOOL_MAP, (
        "%r is not a live tool, so this row emits advice with no callable half"
        % row.next_action)


# ------------------------------------------------------- 7. the deliberate non-actions

def test_auto_chain_rules_is_not_itself_a_rule_drift_symbol():
    """Measured, and deliberately NOT changed: editing AUTO_CHAIN_RULES does not fire the
    drift trigger, because rule_drift_check cannot see that kind of drift -- its checks_run are
    behavior_sets / agent_list / tool_names, none of which compare chain fields to the doc.
    Adding AUTO_CHAIN_RULES to RULE_DRIFT_SYMBOLS would point a CA at a check guaranteed to
    answer 'no drift'. THIS suite and test_a31_autochain_registry are the gate for that
    direction. If rule_drift_check ever gains a chain-field check, delete this test.
    """
    assert "AUTO_CHAIN_RULES" not in ct.RULE_DRIFT_SYMBOLS
    fn = _func("code_tools.py", "rule_drift_check")
    assert not any("AUTO_CHAIN" in s for s in _strings(fn)), (
        "rule_drift_check now inspects the chain registry -- AUTO_CHAIN_RULES can be added to "
        "RULE_DRIFT_SYMBOLS and this note re-decided")
