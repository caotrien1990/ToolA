"""A34 W2 - resolve the ONE [A] tool name that a ca_next_action really names.

Why this exists (measured 2026-09-18 with an ast walk over the production tree, the
same scanner shape tests/test_a31_autochain_registry.py uses):

  259 distinct ca_next_action literals
    8  are a bare tool name            -> "refresh_project_map", "list_sessions"
   25  name a real tool inside advice   -> "use_write_fix_or_choose_new_path"
   26  look identical to those 25 but name NO existing tool
                                        -> "use_partial_then_narrow_regex_scope"
  ~200 are advice sentences with no tool in them

The 26 are the whole problem. ``use_write_fix_or_choose_new_path`` and
``use_partial_then_narrow_regex_scope`` are the same shape, so a CA cannot tell
from ca_next_action alone whether the snake_case run is something it may CALL or
prose it must READ. Guessing wrong costs a round trip against a tool that does
not exist -- and ca_next_action is the field a CA reads first on an error.

So ca_next_action keeps its documented contract (free-form advisory prose, not an
enum -- see the comment above AUTO_CHAIN_RULES in code_tools.py) and the callable
half moves to its own field: ``ca_next_tool`` is emitted ONLY when the value is a
name that exists in the live tool registry. Its ABSENCE is now the signal that
ca_next_action is advice. That is the tool-name / hint separation, expressed as
two fields instead of one overloaded one.

Derivation, not a hand table: a 33-row mapping written beside 259 literals is the
exact rot A31 forbids. The resolver below re-derives from the literal on every
call, so a new ca_next_action is covered the moment it is written, and a tool
that is REMOVED stops being suggested with no edit anywhere.

Longest-first is deliberate: `workflow_get_current` and `workflow_get` can both be
live tools, and shrinking from the longest leading run picks the specific one.
Only a LEADING run counts -- "use_partial_then_narrow_regex_scope" must not resolve
to some tool named deep inside a sentence, because the first thing named is the
thing to do next.

`current_tool` exists because of two measured sites, and they disagree, so a blanket
"an auto-chain may not point at itself" rule would have been wrong:

  tools_v15.py:9513   normalize_ca_registration
      -> "call_normalize_ca_registration_with_dry_run_false_to_apply"
      A real self-chain: the dry_run preview tells the CA to re-call the SAME tool
      with dry_run=false. Suppressing it would delete correct advice.

  code_tools.py:11971 workflow_reset
      -> "workflow_reset_complete_no_submit_previous_terminal_step"
      A STATUS sentence ("reset complete, do not submit the previous terminal
      step") whose first token-run merely happens to be the tool's own name.
      Emitting ca_next_tool here would advise resetting the workflow again.

The difference is the imperative verb. So a self-referential resolution is kept only
when the literal actually says call/use/run; without one, a leading match on your own
name is an echo of what just happened, not an instruction. Every other resolution is
unaffected -- the guard only fires when the resolved name equals `current_tool`.
"""

from __future__ import annotations

import re

# The three verbs the measured population actually uses, with either separator:
# "call_bootstrap_session_then_begin_task", "run audit_tool_matrix",
# "use_run_project_test_script_wrapper".
_VERB = re.compile(r"^(?:call|use|run)[\s_]+")

# A tool name is a python identifier run; anything else ends the candidate.
_HEAD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*")


def resolve_next_tool(action, known_tools, current_tool: str = "") -> str:
    """Return the tool name `action` names, or "" when it names none.

    `known_tools` is the live registry (any container supporting `in` -- TOOL_MAP
    itself is passed, so the answer follows tool add/remove with no edit here).
    `current_tool` is the tool whose payload this is; see the module docstring for
    why a self-referential answer needs an imperative verb to survive.
    """
    if not isinstance(action, str) or not known_tools:
        return ""
    text = action.strip()
    if not text:
        return ""
    imperative = bool(_VERB.match(text))
    if text in known_tools:
        candidate = text
    else:
        head = _HEAD.match(_VERB.sub("", text, count=1))
        if not head:
            return ""
        parts = head.group(0).split("_")
        candidate = ""
        for size in range(len(parts), 0, -1):
            probe = "_".join(parts[:size])
            if probe in known_tools:
                candidate = probe
                break
        if not candidate:
            return ""
    if candidate == current_tool and not imperative:
        return ""
    return candidate


def annotate_next_tool(result, known_tools, current_tool: str = "", fields=None) -> str:
    """Write ``result["ca_next_tool"]`` when ca_next_action names a live tool.

    Returns the name written, or "" when nothing was. The decision lives here rather than
    inline in the orchestrator_server dispatch funnel so that all of it -- including the
    R101 projection rule -- is directly testable: the funnel is a few lines inside a long
    async HTTP handler that cannot be exercised without a live request.

    An existing ca_next_tool is never overwritten: a tool that computed its own answer knows
    more than this resolver does.
    """
    if not isinstance(result, dict) or result.get("ca_next_tool"):
        return ""
    # R101: the payload reaching the funnel has ALREADY been projected by `fields=`, so
    # adding a key here would smuggle an advisory field past the caller's token budget.
    # Only emit when no projection is active, or when ca_next_tool was itself requested.
    if fields and not any(
            str(f).split(".", 1)[0] == "ca_next_tool" for f in fields):
        return ""
    name = resolve_next_tool(result.get("ca_next_action"), known_tools, current_tool)
    if name:
        result["ca_next_tool"] = name
    return name
