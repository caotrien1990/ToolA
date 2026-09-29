"""CA Tool Policy Matrix runtime helpers (CSLt A-first governance).

North star CSLt: Correctness > Save token > Less context > Time fee.

SOT file: .council/CA_TOOL_POLICY_MATRIX.json.
This module is the request-time classifier + metrics aggregator. It does not
duplicate Matrix content — it loads SOT and derives:
  - task_class from tool_name / action_type / scope / target
  - native_policy for that class
  - bypass counters (forbidden_native / allowed_native / via_a / unclassified)
  - Hermes host gate by project vs outside-project scope
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from orchestrator.council.identity import canonical_id

_ROOT = Path(__file__).resolve().parents[2]  # project-orchestrator/
_MATRIX_PATH = _ROOT / ".council" / "CA_TOOL_POLICY_MATRIX.json"
_AGENT_REGISTRY_PATH = _ROOT / "agents_registry.json"

# AUDIT-KEEP(roster), Anh 2026-09-29: the retired worker ids in the two sets below
# (hermes/hermc/herm_c, kilo/kiloc/kilo_c, comc/comc_c/com_c) are inert -- the suffix
# rules already decide those agents. Kept, no action.
# CLI / worker agents that MUST stay HTTP-only (C-suffix workers).
# NOTE: desktop ids like "comc"/"kilo"/"zcode" must NOT be listed here — they collide
# with lowercased worker names (ComC→comc). Prefer legacy_worker_name.endswith("c")
# + explicit C-suffix forms only.
_HTTP_ONLY_AGENT_IDS = frozenset({
    "clauc", "codoc", "opec", "comc_c", "hermc", "kiloc", "cozoc",
    "clau_c", "codo_c", "ope_c", "com_c", "herm_c", "kilo_c", "zcode_c",
    "cozo_c",
})
# Desktop agents may use MCP for T4 page-stateful only; still mode=A / a_first.
_DESKTOP_AGENT_IDS = frozenset({
    "clau", "codo", "opencode", "ope", "comc", "hermes", "zcode", "kilo", "cozo",
})

# Heuristic tool → task_class mapping (primary; SOT rows keep semantic match text).
_TOOL_TO_CLASS: dict[str, str] = {
    "get_rule_core_text": "bootstrap_rule",
    "bootstrap_session": "bootstrap_rule",
    "prepare_ca_session": "bootstrap_rule",
    "resolve_project_path": "project_path_resolution",
    "read_file_range": "read_project_file",
    "get_file_symbols": "read_project_file",
    "list_structure": "read_project_file",
    "search_code": "search_project_code",
    "find_symbol": "search_project_code",
    "regex_search": "search_project_code",
    "find_callers": "search_project_code",
    "find_definition_and_usage": "search_project_code",
    "write_fix": "write_existing_file",
    "write_draft_patch": "write_existing_file",
    "write_file": "write_existing_file",
    "write_text": "write_existing_file",
    "Edit": "write_existing_file",
    "Write": "write_existing_file",
    # The host's other two file-mutation tools. Unmapped, they fell through to
    # `unknown` -- the same forbid by accident, and no audit class to log under.
    "MultiEdit": "write_existing_file",
    "NotebookEdit": "write_existing_file",
    "Read": "read_project_file",
    "Bash": "project_command",
    "Grep": "search_project_code",
    "Glob": "search_project_code",
    # The host's web tools. Unmapped, they reached general_web_research only
    # through a keyword -- "http" in the action, or "web" somewhere in the URL --
    # and the keyword heuristics no longer read the target.
    "WebFetch": "general_web_research",
    "WebSearch": "general_web_research",
    "write_fix_html": "write_existing_file",
    "write_new_file": "create_project_file",
    "write_transaction": "batch_project_write",
    "delete_file": "delete_or_copy_project_file",
    "copy_file": "delete_or_copy_project_file",
    "run_project_command": "project_command",
    "run_ps1_script": "project_command",
    "run_node_script": "project_command",
    "run_project_test_script": "project_test",
    "run_test_profile": "project_test",
    "backup_project": "session_backup_audit",
    "backup_status": "session_backup_audit",
    "open_session": "session_backup_audit",
    "close_session": "session_backup_audit",
    "log_native_event": "session_backup_audit",
    "log_native": "session_backup_audit",
    "create_pending_audit": "session_backup_audit",
    "sync_pending_audit": "session_backup_audit",
    "session_audit_summary": "session_backup_audit",
    "council_message": "council_dispatch",
    "convene_council": "council_dispatch",
    "council_tracker": "council_dispatch",
    "mission_create": "mission_workflow",
    "mission_next": "mission_workflow",
    "mission_dispatch": "mission_workflow",
    "mission_report": "mission_workflow",
    "mission_status": "mission_workflow",
    "mission_gate": "mission_workflow",
    "workflow_start": "mission_workflow",
    "workflow_submit_step": "mission_workflow",
    "manage_agent": "agent_rule_sync",
    "council_config_sync": "agent_rule_sync",
    "desktop_screenshot": "t4_snapshot",
    "describe_page": "t4_snapshot",
    "read_page": "t4_snapshot",
    "screenshot_page": "t4_snapshot",
    "query_selector": "t4_snapshot",
    "click": "t4_action",
    "fill": "t4_action",
    "click_selector": "t4_action",
    "fill_value": "t4_action",
    "desktop_click": "t4_action",
    "desktop_send_text": "t4_action",
    "restart_orchestrator_via_bat": "orchestrator_lifecycle",
    "restart_orchestrator_via_task": "orchestrator_lifecycle",
    "server_lifecycle_status": "orchestrator_lifecycle",
    "port_owner_inspect": "orchestrator_lifecycle",
}

_ACTION_TO_CLASS: dict[str, str] = {
    "write": "write_existing_file",
    "read": "read_project_file",
    "search": "search_project_code",
    "delete": "delete_or_copy_project_file",
    "command": "project_command",
    "test": "project_test",
    "audit": "session_backup_audit",
    "session": "session_backup_audit",
    "council": "council_dispatch",
    "mission": "mission_workflow",
    "workflow": "mission_workflow",
    "ui": "t4_action",
    "snapshot": "t4_snapshot",
    "restart": "orchestrator_lifecycle",
    "upgrade": "upgrade_orchestrator",
    # F021: the log_native_event schema documents these action_type values, but
    # they had no row here and fell through to task_class="unknown" -> a
    # native_policy=forbid HOST_GATE_DENY on perfectly ordinary actions.
    "edit": "write_existing_file",
    "create": "create_project_file",
    "patch": "write_existing_file",
    "execute": "project_command",
    "validate": "project_test",
    "cache": "session_backup_audit",
    "backup": "session_backup_audit",
}

# F021: callers routinely pass the task_class itself as action_type (the two
# vocabularies overlap and the schema description does not spell out which one
# it wants). Accept that as an identity mapping -- but ONLY for classes whose
# native_policy is forbid. upgrade_orchestrator comes from the target path;
# a_unreachable_mid_task comes from the caller's own words and is forbid too,
# so neither door mints native permission.
_SELF_DECLARABLE_CLASSES = frozenset({
    "write_existing_file",
    "create_project_file",
    "batch_project_write",
    "delete_or_copy_project_file",
    "read_project_file",
    "search_project_code",
    "project_command",
    "project_test",
    "session_backup_audit",
    "council_dispatch",
    "mission_workflow",
    "t4_action",
    "t4_snapshot",
    "orchestrator_lifecycle",
    "agent_rule_sync",
    "bootstrap_rule",
    "project_path_resolution",
})

# ONE scope matcher (2026-09-23). classify_action and hermes_host_gate each
# derived "inside the project?" on their own and disagreed: one required both
# paths to exist, the other did not, and an empty project_path made EVERY target
# inside -- so %TEMP%\x.txt answered scope=project / deny, and a native write to
# the agent's own memory was refused through both entry points.
#
# A project is recognised by find_project_root's markers MINUS [A]'s own
# `.orchestrator_cache`. [A] writes that directory wherever it operates --
# measured in %TEMP%, in the agent memory dir, in the agent home and in the
# parent folder of every app -- so it proves [A] touched a directory, not that
# the directory is a project. (The _OUTSIDE_PROJECT_MARKERS tuple that sat here
# before had no reader anywhere in the repo.)
_PROJECT_ROOT_MARKERS = (".git", "package.json", "requirements.txt")
_OS_PATH_MARKERS = ("\\windows\\", "\\system32\\", "\\users\\public")
_OS_SCOPES = ("os", "outside_project", "env", "host")


def governed_project_root(target: str) -> Path | None:
    """Nearest existing ancestor of `target` that carries a project marker.

    HOME, HOME's ancestors and a drive root never count, for the reason
    find_project_root skips them: a stray marker there would turn every path
    under HOME (%TEMP% included) into a project.
    """
    t = (target or "").strip()
    if not t or t.startswith(("\\\\", "//")):
        return None
    try:
        tp = Path(t)
        if not tp.is_absolute():
            return None
        tp = tp.resolve()
        home = Path.home().resolve()
        for parent in (tp, *tp.parents):
            if parent == parent.parent or not parent.is_dir():
                continue
            if home.is_relative_to(parent):
                continue
            if any((parent / mk).exists() for mk in _PROJECT_ROOT_MARKERS):
                return parent
    except (OSError, RuntimeError, ValueError):
        return None
    return None


def target_scope(*, target: str = "", project_path: str = "", scope: str = "") -> str:
    """"project" or "outside_project" for one target -- the only place that decides.

    Governance is the default: no target, a relative one, or a path that cannot
    be resolved is "project". A target under the declared project_path is
    "project" whether or not it exists yet (a new file). Otherwise the target's
    OWN root decides, so a write into a different project than the declared one
    is still governed, and a path under no project is outside.
    """
    if (scope or "").strip().lower() in _OS_SCOPES:
        return "outside_project"
    t = (target or "").strip()
    if any(x in t.lower().replace("/", "\\") for x in _OS_PATH_MARKERS):
        return "outside_project"
    if not t:
        return "project"
    try:
        tp = Path(t)
        if not tp.is_absolute():
            return "project"
        if project_path and tp.resolve().is_relative_to(Path(project_path).resolve()):
            return "project"
    except (OSError, RuntimeError, ValueError):
        return "project"
    return "project" if governed_project_root(t) is not None else "outside_project"


def is_agent_memory_target(target: str) -> bool:
    """True when `target` sits in a registered agent's own harness memory tree.

    Shape: <dirname(rule_file)>/projects/<slug>/memory/... -- the tree the host
    harness tells its CA to write with native file tools. The agent home comes
    from agents_registry.json (the SOT), never from a path written here.
    """
    t = (target or "").strip()
    if not t:
        return False
    try:
        tp = Path(t)
        if not tp.is_absolute():
            return False
        tp = tp.resolve()
        home = Path.home().resolve()
        agents = load_agents_registry_file(_AGENT_REGISTRY_PATH).get("agents") or {}
    except Exception:
        return False
    for rec in agents.values():
        rule_file = str(rec.get("rule_file") or "").strip() if isinstance(rec, dict) else ""
        if not rule_file:
            continue
        try:
            agent_home = Path(rule_file).resolve().parent
            # A rule file directly in HOME must not make HOME\projects "memory".
            if home.is_relative_to(agent_home):
                continue
            parts = tp.relative_to(agent_home / "projects").parts
        except (OSError, RuntimeError, ValueError):
            continue
        if len(parts) >= 3 and parts[1].lower() == "memory":
            return True
    return False


@lru_cache(maxsize=4)
def load_matrix(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else _MATRIX_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("matrix root must be object")
    return data


def matrix_path() -> str:
    return str(_MATRIX_PATH)


def north_star(matrix: dict[str, Any] | None = None) -> dict[str, Any]:
    m = matrix or load_matrix()
    order = m.get("north_star") or [
        "Correctness", "Save token", "Less context", "Time fee",
    ]
    return {
        "acronym": m.get("acronym") or "CSLt",
        "order": list(order),
        "text": m.get("north_star_text") or "Correctness > Save token > Less context > Time fee",
        "note": "CSLt not CLSt; Save token before Less context",
    }




def row_why_cslt(row: dict | None) -> str:
    """CSLt rationale field; accept legacy why_cls key once during migration."""
    if not isinstance(row, dict):
        return ""
    return str(row.get("why_cslt") or row.get("why_cls") or "")

def _rows_by_class(matrix: dict[str, Any] | None = None) -> dict[str, dict]:
    m = matrix or load_matrix()
    out: dict[str, dict] = {}
    for row in m.get("rows") or []:
        tc = row.get("task_class")
        if tc:
            out[str(tc)] = row
    return out


# Mutating task_classes that must yield to upgrade_orchestrator when the target
# is [A]'s own source tree. Reads are NOT listed: reading [A] via an [A] tool is
# correct and never conflicted with the self-edit rule.
_MUTATING_CLASSES_FOR_SELF_EDIT = frozenset({
    "write_existing_file",
    "create_project_file",
    "batch_project_write",
    "delete_or_copy_project_file",
})

_MUTATING_ACTIONS_FOR_SELF_EDIT = frozenset({"write", "edit", "create", "delete", "patch"})

# A shell tool maps to project_command by TOOL NAME (`Bash`, `run_ps1_script`),
# and that mapping used to win outright — so the IDENTICAL self-edit answered
# ok:true through `Edit` and HOST_GATE_DENY through `Bash`, decided purely by
# which tool spelled the write. Measured on
# orchestrator/graph/store.py: Edit/write -> upgrade_orchestrator, Bash/write ->
# project_command / forbid. A command-runner class yields ONLY when the declared
# action is itself a file mutation; running a real command inside [A] (action
# "command"/"execute"/"test") stays project_command, and privileged classes
# (orchestrator_lifecycle, council_dispatch, …) never yield at all.
_COMMAND_CLASSES_FOR_SELF_EDIT = frozenset({"project_command", "project_test"})


def _is_mutating_self_edit_action(action: str) -> bool:
    """True when `action_type` names a file mutation in EITHER vocabulary.

    Callers pass the action verb ("write") or the task_class ("write_existing_
    file") interchangeably — the same overlap `_SELF_DECLARABLE_CLASSES` already
    concedes — so both spellings must reach the self-edit branch.
    """
    return (action in _MUTATING_ACTIONS_FOR_SELF_EDIT
            or action in _MUTATING_CLASSES_FOR_SELF_EDIT)


def _is_native_mutation(task_class: str | None, action: str) -> bool:
    """ONE answer to "does this op change its target file?".

    Asked by the [A] self-edit branch and by the outside-project memory branch,
    which used to spell it a second way. A mutating class is a mutation, a
    command-runner only when its declared action is a file mutation, and a bare
    verb only when no class resolved -- so a Read that says action="write"
    stays a read.
    """
    return bool(
        task_class in _MUTATING_CLASSES_FOR_SELF_EDIT
        or (task_class in _COMMAND_CLASSES_FOR_SELF_EDIT
            and _is_mutating_self_edit_action(action))
        or (not task_class and _is_mutating_self_edit_action(action))
    )


# The classes that only LOOK at their target. Off-root they become the readonly
# OS probe; every other class keeps its own row there (#18).
_OS_READ_CLASSES = frozenset({"read_project_file", "search_project_code"})


def is_orchestrator_self_target(target: str) -> bool:
    """True when `target` lives inside [A]'s own source tree (_ROOT).

    Path-exact, unlike the old keyword blob heuristic which only fired on the
    words upgrade/hybrid/code_tools and therefore missed every real self-edit
    (e.g. orchestrator/session/store.py).
    """
    t = (target or "").strip()
    if not t:
        return False
    try:
        tp = Path(t)
        if not tp.is_absolute():
            return False
        tp = tp.resolve()
        try:
            tp.relative_to(_ROOT.resolve())
        except ValueError:
            return False
        return True
    except Exception:
        return False


def classify_action(
    *,
    tool_name: str = "",
    action_type: str = "",
    transport: str = "native",
    scope: str = "",
    target: str = "",
    project_path: str = "",
    agent_id: str = "",
    matrix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify one governed action against Matrix SOT.

    Returns machine-checkable fields for ledger/metrics:
      task_class, native_policy, classification, is_forbidden_native,
      is_allowed_native, is_via_a, is_unclassified, host_gate
    """
    m = matrix or load_matrix()
    by = _rows_by_class(m)
    tool = (tool_name or "").strip()
    action = (action_type or "").strip().lower()
    tr = (transport or "native").strip().lower()
    if tr not in ("native", "via_a"):
        tr = "native"

    scope_l = (scope or "").strip().lower()
    # W7 (F021, 2026-08-19): the keyword heuristics below used plain `k in blob`,
    # and that blob ended with the TARGET PATH, so an ordinary filename flipped
    # policy:
    #   "my_code_tools_notes.txt" -> upgrade_orchestrator (then hybrid_upgrade_only)
    #   "data download.csv"       -> a_unreachable_mid_task (then allow_with_log_native_event)
    # Match on token boundaries, so "upgrade" no longer fires on
    # "downgrade"/"upgrade_notes.md". Underscore is a word char, so \b already
    # handles the _notes/_helper suffixes; the dot in ".txt" is a boundary.
    def _kw_hit(text: str, keywords: tuple[str, ...]) -> bool:
        return any(
            re.search(r"\b" + re.escape(k) + r"\b", text)
            for k in keywords
        )

    # Every keyword heuristic reads the CALLER'S words only, never the path. The
    # boundary match still let one path segment decide (#18, 2026-09-24): a
    # native PowerShell move of <project>\web\index.html matched "web" and was
    # ALLOWED as general_web_research. What a path means has its own matchers --
    # target_scope, is_orchestrator_self_target, is_agent_memory_target.
    signal_blob = f"{tool} {action} {scope_l}".lower()

    task_class = _TOOL_TO_CLASS.get(tool) or _ACTION_TO_CLASS.get(action)
    # F021: accept an action_type that already names a (non-privileged) class.
    if not task_class and action in _SELF_DECLARABLE_CLASSES:
        task_class = action
    # B017: and accept the `<verb>_file` / `<verb>_files` spelling of a verb that
    # already has a row. `create_file` fell through to task_class="unknown" ->
    # native_policy="forbid", so a native CREATE inside [A]'s own tree was
    # refused while a native EDIT of the same file was allowed -- a distinction
    # no rule makes, and it made the audit trail for a new file unwritable.
    # This resolves ONLY through the table above: an unknown verb stays unknown,
    # so "frobnicate_file" is still forbid, and a suffix can never mint
    # upgrade_orchestrator (not in _ACTION_TO_CLASS; derived from the path).
    if not task_class:
        stem = re.sub(r"_files?$", "", action)
        if stem != action:
            task_class = _ACTION_TO_CLASS.get(stem)

    # Scope: target_scope is the ONE matcher, shared with hermes_host_gate.
    outside = target_scope(
        target=target, project_path=project_path, scope=scope,
    ) == "outside_project"

    # Outside every project the class still names the OPERATION (#18). A write
    # keeps write_existing_file because [A] governs it off-root too: write_fix /
    # write_new_file take an absolute outside target (xroot), so the row's
    # preferred tool is a working route, and host_gate.scope carries the scope.
    # Only a read becomes the readonly OS probe. Anything else keeps what it
    # resolved to -- a privileged class keeps its row, a verb no table knows stays
    # unknown. Relabelling those a read recorded PowerShell move/rm/"" and a
    # restart as ALLOWED reads, where the Matrix says never guess.
    if outside:
        if is_agent_memory_target(target) and _is_native_mutation(task_class, action):
            # The agent's OWN harness memory is written natively by mandate and
            # governed by no [A] tool, so it gets its own class.
            task_class = "agent_memory_write"
        elif task_class in _OS_READ_CLASSES:
            task_class = "outside_project_os_read"
    if _kw_hit(signal_blob, ("web",)) or "http" in action:
        if not task_class:
            task_class = "general_web_research"
    if _kw_hit(signal_blob, ("upgrade", "hybrid", "edit orchestrator", "code_tools")):
        if not task_class:
            task_class = "upgrade_orchestrator"
    # [A] self-edit: a NATIVE mutation whose target is inside [A]'s own source
    # tree is upgrade_orchestrator, not a plain project write, so the ledger and
    # the deny name it for what it is and preferred_via_a carries the self-upgrade
    # steps (restart_orchestrator_via_bat, manage_agent). Its native_policy is
    # forbid like every project write: [A] edits itself through read_file_range
    # -> write_fix, and a restart loads the new code. The hybrid_upgrade_only
    # carve-out that let this through natively was the widest native channel in
    # use; a broken [A] is Anh's break-glass on the host, not a Matrix row.
    # Only NATIVE transport is reclassified: via_a stays governed as before.
    if (
        tr == "native"
        and is_orchestrator_self_target(target)
        and _is_native_mutation(task_class, action)
    ):
        task_class = "upgrade_orchestrator"
    # The caller's own words. Since 2026-09-24 this row is forbid: a classifier
    # that answers proves [A] is up, so the claim names itself and licenses nothing.
    if _kw_hit(signal_blob, ("a down", "unreachable", "conn-refused", "connection refused")):
        task_class = "a_unreachable_mid_task"

    if not task_class:
        task_class = "unknown"

    row = by.get(task_class) or by.get("unknown") or {}
    native_policy = row.get("native_policy") or "forbid"
    classified = task_class != "unknown" and bool(row)

    is_via_a = tr == "via_a"
    is_native = tr == "native"
    is_forbidden_native = bool(is_native and native_policy == "forbid")
    is_allowed_native = bool(
        is_native
        and native_policy in (
            "allow_readonly",
            "allow_native",
            "allow_with_log_native_event",
        ),
    )
    is_unclassified = not classified

    host = hermes_host_gate(
        agent_id=agent_id,
        project_path=project_path,
        target=target,
        scope=scope,
        task_class=task_class,
        transport=tr,
        native_policy=native_policy,
    )

    preferred = list(row.get("toolset_primary") or row.get("via_a_tools") or [])[:12]
    # Preflight contract: forbidden native / host deny must surface hard_stop even
    # when this function is only consulted (before any native write executes).
    host_denied = isinstance(host, dict) and not bool(host.get("allowed", True))
    preflight_hard_stop = bool(is_forbidden_native or host_denied)
    if preflight_hard_stop:
        if host_denied and str((host or {}).get("decision") or "").startswith("deny"):
            err_name = "HOST_GATE_DENY"
            next_act = "stop_native_use_a_tool"
        else:
            err_name = "FORBIDDEN_NATIVE"
            next_act = "stop_native_use_a_tool"
    elif is_allowed_native and native_policy == "allow_with_log_native_event":
        err_name = None
        next_act = "log_native_event_required"
    else:
        err_name = None
        next_act = "continue"

    return {
        "ok": True,
        "task_class": task_class,
        "native_policy": native_policy,
        "classified": classified,
        "transport": tr,
        "is_via_a": is_via_a,
        "is_native": is_native,
        "is_forbidden_native": is_forbidden_native,
        "is_allowed_native": is_allowed_native,
        "is_unclassified": is_unclassified,
        "policy_row": {
            "task_class": row.get("task_class"),
            "tier": row.get("tier"),
            "toolset_primary": row.get("toolset_primary"),
            "native_policy": native_policy,
            "session_required": row.get("session_required"),
            "token_required": row.get("token_required"),
        } if row else {},
        "host_gate": host,
        "north_star": north_star(m),
        "preferred_via_a": preferred,
        "preflight_hard_stop": preflight_hard_stop,
        "error_name": err_name,
        "ca_next_action": next_act,
    }


# AUDIT-KEEP(hermes), Anh 2026-09-29: the name is historical. This is the host gate for
# EVERY CA (classify_action and open_session_policy_payload call it). The agent id only
# feeds the echo keys agent_id / is_hermes; no decision reads it and nothing consumes
# is_hermes. The Hermes host is retired and not installed; its agents_registry.json row
# is kept, and every hermes label here, in tools_v15.hermes_host_gate_check, in
# registry_data (schema default) and in mcp_packs/generator.py is harmless -- no action.
def hermes_host_gate(
    *,
    agent_id: str = "",
    project_path: str = "",
    target: str = "",
    scope: str = "",
    task_class: str = "",
    transport: str = "native",
    native_policy: str = "forbid",
) -> dict[str, Any]:
    """Hermes (and peers) host gate by project scope.

    Inside project root: [A]-first; native only when Matrix allows + audit.
    Outside project: allow_readonly OS probe only; mutating host ops forbidden
    unless explicit Anh go-ahead (signaled via scope=user_approved_os_mutate).
    The one outside mutation that needs no go-ahead is the agent's own harness
    memory (native_policy=allow_native): the harness mandates it and no [A] tool
    governs that tree.
    """
    aid = (agent_id or "").strip().lower()
    is_hermes = aid in ("hermes", "hermc", "herm")
    scope_l = (scope or "").strip().lower()
    user_approved = scope_l in (
        "user_approved_os_mutate", "anh_approved", "user_go_ahead",
    )

    # Scope comes from target_scope, the matcher classify_action uses too. This
    # gate used to derive it a second way (no exists() check; an empty
    # project_path made every target inside), so the two could disagree on the
    # very same target.
    outside = task_class in ("outside_project_os_read", "agent_memory_write") or (
        target_scope(target=target, project_path=project_path, scope=scope)
        == "outside_project"
    )

    if outside:
        reason = "Outside project: readonly OS probe OK; mutating host ops need Anh go-ahead"
        if user_approved:
            decision = "allow_user_approved"
        elif native_policy == "allow_native":
            decision = "allow_native"
            reason = "Outside project: the agent's own harness memory, written natively by mandate"
        elif native_policy == "allow_readonly" or transport == "via_a":
            decision = "allow_readonly"
        else:
            decision = "deny_outside_project_mutate"
        return {
            "agent_id": aid or None,
            "is_hermes": is_hermes,
            "scope": "outside_project",
            "decision": decision,
            "allowed": decision.startswith("allow"),
            "reason": reason,
            "native_policy": native_policy,
        }

    # inside project
    if transport == "via_a":
        return {
            "agent_id": aid or None,
            "is_hermes": is_hermes,
            "scope": "project",
            "decision": "allow_via_a",
            "allowed": True,
            "reason": "Inside project via [A]",
            "native_policy": native_policy,
        }
    if native_policy == "forbid":
        return {
            "agent_id": aid or None,
            "is_hermes": is_hermes,
            "scope": "project",
            "decision": "deny_native_forbid",
            "allowed": False,
            "reason": "Inside project: native_policy=forbid — use [A] tool",
            "native_policy": native_policy,
        }
    return {
        "agent_id": aid or None,
        "is_hermes": is_hermes,
        "scope": "project",
        "decision": "allow_native_with_policy",
        "allowed": True,
        "reason": f"Inside project: native_policy={native_policy}",
        "native_policy": native_policy,
    }


def agent_transport_policy(agent_id: str, record: dict | None = None) -> str:
    """Return the agent runtime transport policy (not shell/script transport)."""
    rec = record or {}
    aid = canonical_id(agent_id or rec.get("id"))
    existing = (
        rec.get("agent_transport_policy") or rec.get("transport_policy") or ""
    ).strip()
    if (rec.get("mode") or "A") == "native" or existing == "native_opt_out":
        return "native_opt_out"
    # An explicit registry runtime policy overrides desktop/default inference.
    if existing in ("http_only", "a_first_mcp_t4_ok"):
        return existing
    persona = (rec.get("persona") or "").lower()
    # Worker C-suffix: registry id is *C form, or aid in HTTP-only set.
    # Do NOT treat desktop legacy_worker_name="ComC" as making desktop comc http_only.
    is_worker_id = (
        aid in _HTTP_ONLY_AGENT_IDS
        or (aid.endswith("c") and aid not in _DESKTOP_AGENT_IDS and len(aid) > 1)
        or aid.endswith("_c")
    )
    if is_worker_id or ("do not use mcp" in persona and "http" in persona):
        return "http_only"
    return "a_first_mcp_t4_ok"


def default_transport_policy(agent_id: str, record: dict | None = None) -> str:
    """Backward-compatible alias for the agent runtime transport policy."""
    return agent_transport_policy(agent_id, record)


def load_agents_registry_file(path) -> dict:
    """A31: single BOM-safe decode point for agents_registry.json.

    Three code paths read this file (code_tools._load_agents_registry,
    open_session_policy_payload, tools_v15.normalize_ca_registration). Only this
    one used utf-8-sig before; the other two used utf-8, so a UTF-8 BOM made
    json.loads raise "Unexpected UTF-8 BOM". Decoding with utf-8-sig strips a BOM
    if present and is a no-op otherwise.

    This helper RAISES on a missing/corrupt file. Callers keep their own
    existence check and error policy (fail-safe vs fail-loud) on top of it — the
    decode is unified, the caller error contracts are deliberately NOT.
    """
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def normalize_agent_record(agent_id: str, record: dict) -> dict:
    """Ensure mode=A and machine-checkable agent runtime transport fields."""
    rec = dict(record or {})
    rec.setdefault("id", agent_id)
    mode = (rec.get("mode") or "A").strip()
    rec["mode"] = "native" if mode.lower() == "native" else "A"
    runtime_policy = agent_transport_policy(agent_id, rec)
    rec["agent_transport_policy"] = runtime_policy
    rec["transport_policy"] = runtime_policy
    rec["governance"] = {
        "north_star": "CSLt",
        "acronym": "CSLt",
        "order": ["Correctness", "Save token", "Less context", "Time fee"],
        "a_first": rec["mode"] == "A",
        "http_only": runtime_policy == "http_only",
    }
    return rec


def normalize_registry(reg: dict) -> tuple[dict, list[dict]]:
    """Return (updated_registry, changes[])."""
    agents = dict(reg.get("agents") or {})
    changes: list[dict] = []
    for aid, rec in list(agents.items()):
        before = {
            "mode": rec.get("mode"),
            "transport_policy": rec.get("transport_policy"),
        }
        new = normalize_agent_record(aid, rec)
        after = {
            "mode": new.get("mode"),
            "transport_policy": new.get("transport_policy"),
        }
        if before != after:
            changes.append({"agent_id": aid, "before": before, "after": after})
        agents[aid] = new
    out = dict(reg)
    out["agents"] = agents
    out["governance_normalized_at"] = datetime.now().isoformat(timespec="seconds")
    out["governance_north_star"] = "CSLt"
    return out, changes


def encode_classification_reason(base_reason: str, classification: dict) -> str:
    """Embed compact machine tag in reason (events.reason is varchar-ish)."""
    tag = (
        f"[CA_POL task={classification.get('task_class')}"
        f" np={classification.get('native_policy')}"
        f" tr={classification.get('transport')}"
        f" forbid={int(bool(classification.get('is_forbidden_native')))}"
        f" allow={int(bool(classification.get('is_allowed_native')))}"
        f" via={int(bool(classification.get('is_via_a')))}"
        f" uncls={int(bool(classification.get('is_unclassified')))}]"
    )
    base = (base_reason or "").strip()
    # strip prior tag if re-logging
    base = re.sub(r"\[CA_POL[^\]]*\]\s*", "", base).strip()
    merged = f"{tag} {base}".strip()
    return merged[:300]


_CA_POL_RE = re.compile(
    r"\[CA_POL\s+task=(?P<task>\S+)\s+np=(?P<np>\S+)\s+tr=(?P<tr>\S+)\s+"
    r"forbid=(?P<forbid>[01])\s+allow=(?P<allow>[01])\s+via=(?P<via>[01])\s+"
    r"uncls=(?P<uncls>[01])\]",
)


def parse_classification_reason(reason: str | None) -> dict | None:
    if not reason:
        return None
    m = _CA_POL_RE.search(reason)
    if not m:
        return None
    return {
        "task_class": m.group("task"),
        "native_policy": m.group("np"),
        "transport": m.group("tr"),
        "is_forbidden_native": m.group("forbid") == "1",
        "is_allowed_native": m.group("allow") == "1",
        "is_via_a": m.group("via") == "1",
        "is_unclassified": m.group("uncls") == "1",
    }


# sessions.db stores [A] HTTP tool calls as tool_origin="A" (legacy), not "via_a".
# Without this map, metrics treated every /api/call as native→forbidden (bypass_rate≈1.0).
_VIA_A_ORIGINS = {
    "a", "via_a", "via-a", "viaa", "orchestrator", "[a]", "http", "api",
}
_NATIVE_ORIGINS = {
    "native", "n", "local", "fallback", "hermes_native", "cli_native",
}


def normalize_tool_origin(tool_origin: str | None) -> str:
    """Return 'via_a' | 'native' | 'unknown' from events.tool_origin."""
    o = (tool_origin or "").strip().lower()
    if not o:
        return "unknown"
    if o in _VIA_A_ORIGINS or o.startswith("via_"):
        return "via_a"
    if o in _NATIVE_ORIGINS or o.startswith("native"):
        return "native"
    # bare agent ids that logged through [A] still count via_a when origin is literally "A"
    if o == "a":
        return "via_a"
    return "unknown"


def aggregate_metrics_from_events(
    rows: list[tuple],
    *,
    group_by: list[str] | None = None,
    agent_id: str = "",
    project_path: str = "",
    time_window_hours: float | None = None,
) -> dict[str, Any]:
    """Aggregate bypass counters from events rows.

    Expected tuple layout (flexible):
      (tool_name, tool_origin, action_type, reason, created_at, target?)

    CSLt Correctness rule for origin:
      tool_origin in {A, via_a, ...} → via_a (never forbidden_native)
      tool_origin native → classify with transport=native
      empty origin → fall back to CA_POL tag then classify native-default
    """
    now = datetime.now()
    cutoff = None
    if time_window_hours is not None:
        cutoff = now - timedelta(hours=float(time_window_hours))

    forbidden = allowed = via_a = unclassified = 0
    by_class: dict[str, dict[str, int]] = defaultdict(
        lambda: {"forbidden_native_count": 0, "allowed_native_count": 0,
                 "via_a_count": 0, "unclassified_count": 0, "total": 0},
    )
    by_origin: dict[str, int] = defaultdict(int)
    samples: list[dict] = []

    for row in rows:
        tool_name = row[0] if len(row) > 0 else ""
        tool_origin = (row[1] if len(row) > 1 else "") or ""
        action_type = row[2] if len(row) > 2 else ""
        reason = row[3] if len(row) > 3 else ""
        created_at = row[4] if len(row) > 4 else ""
        target = row[5] if len(row) > 5 else ""

        if cutoff and created_at:
            try:
                ts = datetime.fromisoformat(str(created_at)[:19])
                if ts < cutoff:
                    continue
            except Exception:
                pass

        origin_kind = normalize_tool_origin(tool_origin)
        by_origin[str(tool_origin or "") or "(empty)"] += 1

        # Transport resolution: origin beats untagged default; CA_POL tag still used for
        # native policy flags, but cannot flip an [A]/A-origin event into forbidden_native.
        parsed_tag = parse_classification_reason(reason)
        if origin_kind == "via_a":
            transport = "via_a"
        elif origin_kind == "native":
            transport = "native"
        elif parsed_tag and parsed_tag.get("transport") in ("via_a", "native"):
            transport = parsed_tag["transport"]
        else:
            transport = "native"

        if parsed_tag:
            parsed = {
                **parsed_tag,
                "task_class": parsed_tag.get("task_class") or "unknown",
                "transport": transport,
            }
            # Origin wins for via_a/is_forbidden: never count [A] HTTP as forbidden native
            if origin_kind == "via_a" or transport == "via_a":
                parsed["is_via_a"] = True
                parsed["is_forbidden_native"] = False
                parsed["is_allowed_native"] = False
                parsed["transport"] = "via_a"
            elif parsed_tag.get("transport") != "native":
                # The tag's flags were computed for ANOTHER transport, so they answer
                # a different question; only then is the row re-judged as native.
                # A native tag is kept as written: log_native_event classified the
                # row at WRITE time, with the scope and the Matrix live then. Re-judging
                # it here used the CURRENT Matrix and no scope, so every policy change
                # (upgrade_orchestrator -> forbid, 2026-09-24) rewrote history.
                live = classify_action(
                    tool_name=str(tool_name or ""),
                    action_type=str(action_type or ""),
                    transport="native",
                    target=str(target or ""),
                    project_path=project_path,
                    agent_id=agent_id,
                )
                if parsed.get("task_class") in (None, "", "unknown") and live.get("task_class"):
                    parsed["task_class"] = live["task_class"]
                parsed["is_via_a"] = False
                parsed["is_forbidden_native"] = bool(live.get("is_forbidden_native"))
                parsed["is_allowed_native"] = bool(live.get("is_allowed_native"))
                parsed["is_unclassified"] = bool(live.get("is_unclassified"))
                parsed["native_policy"] = live.get("native_policy") or parsed.get("native_policy")
                parsed["transport"] = "native"
        else:
            parsed = classify_action(
                tool_name=str(tool_name or ""),
                action_type=str(action_type or ""),
                transport=transport,
                target=str(target or ""),
                project_path=project_path,
                agent_id=agent_id,
            )
            if origin_kind == "via_a":
                parsed["is_via_a"] = True
                parsed["is_forbidden_native"] = False
                parsed["is_allowed_native"] = False
                parsed["transport"] = "via_a"

        tc = parsed.get("task_class") or "unknown"
        bucket = by_class[tc]
        bucket["total"] += 1

        if parsed.get("is_via_a") or origin_kind == "via_a" or transport == "via_a":
            via_a += 1
            bucket["via_a_count"] += 1
            classification_label = "via_a"
        elif parsed.get("is_forbidden_native"):
            forbidden += 1
            bucket["forbidden_native_count"] += 1
            classification_label = "forbidden_native"
        elif parsed.get("is_allowed_native"):
            allowed += 1
            bucket["allowed_native_count"] += 1
            classification_label = "allowed_native"
        elif parsed.get("is_unclassified") or tc == "unknown":
            unclassified += 1
            bucket["unclassified_count"] += 1
            classification_label = "unclassified"
        else:
            # native but classified non-forbid non-allow → allowed_native soft
            allowed += 1
            bucket["allowed_native_count"] += 1
            classification_label = "allowed_native"

        if len(samples) < 20:
            samples.append({
                "tool_name": tool_name,
                "tool_origin": tool_origin,
                "origin_kind": origin_kind,
                "transport": parsed.get("transport") or transport,
                "task_class": tc,
                "label": classification_label,
                "created_at": created_at,
            })

    # governed classified = all labeled; unclassified stays in denom per SOT
    denom = forbidden + allowed + via_a + unclassified
    bypass_rate = (forbidden / denom) if denom else None

    return {
        "ok": True,
        "north_star": north_star(),
        "metrics_contract": {
            "bypass_rate_formula": "forbidden_native_actions / all_classified_governed_actions",
            "required_counters": [
                "forbidden_native_count",
                "allowed_native_count",
                "via_a_count",
                "unclassified_count",
                "bypass_rate",
            ],
            "origin_map_note": "tool_origin 'A' = via_a (HTTP /api/call); 'native' = true bypass",
        },
        "filters": {
            "agent_id": agent_id or None,
            "project_path": project_path or None,
            "time_window_hours": time_window_hours,
            "group_by": group_by or ["task_class"],
        },
        "counters": {
            "forbidden_native_count": forbidden,
            "allowed_native_count": allowed,
            "via_a_count": via_a,
            "unclassified_count": unclassified,
            "total_governed": denom,
            "bypass_rate": bypass_rate,
        },
        "by_task_class": dict(by_class),
        "by_tool_origin_raw": dict(by_origin),
        "samples": samples,
        "matrix_path": matrix_path(),
    }


def query_session_metrics(
    sessions_db: str | Path,
    *,
    session_id: str = "",
    project_path: str = "",
    agent_id: str = "",
    time_window_hours: float | None = 24.0,
) -> dict[str, Any]:
    """Pull events from sessions.db and aggregate Matrix metrics."""
    db = Path(sessions_db)
    if not db.exists():
        return {
            "ok": False,
            "error": f"sessions db not found: {db}",
            "error_name": "SESSION_NOT_FOUND",
            "counters": {
                "forbidden_native_count": 0,
                "allowed_native_count": 0,
                "via_a_count": 0,
                "unclassified_count": 0,
                "bypass_rate": None,
            },
        }

    conn = sqlite3.connect(str(db))
    try:
        if session_id:
            rows = conn.execute(
                """SELECT tool_name, tool_origin, action_type, reason, created_at, target
                   FROM events WHERE session_id=? ORDER BY id DESC LIMIT 5000""",
                (session_id,),
            ).fetchall()
        elif project_path:
            # match any session for project_path (loose)
            rows = conn.execute(
                """SELECT e.tool_name, e.tool_origin, e.action_type, e.reason, e.created_at, e.target
                   FROM events e
                   JOIN sessions s ON s.session_id = e.session_id
                   WHERE s.project_path LIKE ?
                   ORDER BY e.id DESC LIMIT 5000""",
                (f"%{project_path.replace('/', '%').replace(chr(92), '%')}%",),
            ).fetchall()
            if not rows:
                # fallback: all recent events
                rows = conn.execute(
                    """SELECT tool_name, tool_origin, action_type, reason, created_at, target
                       FROM events ORDER BY id DESC LIMIT 2000""",
                ).fetchall()
        else:
            rows = conn.execute(
                """SELECT tool_name, tool_origin, action_type, reason, created_at, target
                   FROM events ORDER BY id DESC LIMIT 2000""",
            ).fetchall()
    finally:
        conn.close()

    return aggregate_metrics_from_events(
        rows,
        agent_id=agent_id,
        project_path=project_path,
        time_window_hours=time_window_hours,
    )


def open_session_policy_payload(agent_id: str = "", project_path: str = "") -> dict[str, Any]:
    """Compact payload attached to open_session (token-cheap)."""
    m = load_matrix()
    registry_record: dict[str, Any] = {}
    if agent_id and _AGENT_REGISTRY_PATH.is_file():
        registry = load_agents_registry_file(_AGENT_REGISTRY_PATH)
        registry_record = (registry.get("agents") or {}).get(agent_id.lower()) or {}
    runtime_policy = agent_transport_policy(agent_id, registry_record)
    return {
        "north_star": north_star(m),
        "agent_transport_policy": runtime_policy,
        "matrix": {
            "path": matrix_path(),
            "task_class_count": len(m.get("rows") or []),
        },
        "a_first": True,
        "host_gate": hermes_host_gate(
            # AUDIT-KEEP(hermes): inert echo label -- see hermes_host_gate.
            agent_id=agent_id or "hermes",
            project_path=project_path,
            task_class="read_project_file",
            transport="via_a",
            native_policy="forbid",
        ),
        "metrics_contract": m.get("metrics_contract") or {},
        "rules": [
            "Prefer [A] HTTP /api/call/* for project ops",
            "Native only when Matrix native_policy allows + log_native_event",
            "CLI C-suffix workers: transport_policy=http_only",
            "Outside project: allow_readonly only",
        ],
    }
