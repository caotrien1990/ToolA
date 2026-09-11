"""CA Tool Policy Matrix runtime helpers (CSLt A-first governance).

North star CSLt: Correctness > Save token > Less context > Time fee.

SOT file: .council/CA_TOOL_POLICY_MATRIX.json (status=active_sot).
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
    "Read": "read_project_file",
    "Bash": "project_command",
    "Grep": "search_project_code",
    "Glob": "search_project_code",
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
# native_policy is forbid, so a caller can never self-declare its way into
# upgrade_orchestrator / a_unreachable_mid_task and mint native permission it
# has not earned. Those still have to be derived from the target path.
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

_OUTSIDE_PROJECT_MARKERS = (
    r"\\windows\\", r"/windows/", r"\\system32\\", r"/system32/",
    r"\\users\\", r"/users/", r"\\appdata\\", r"/appdata/",
    r"c:\\program", r"c:/program", r"%temp%", r"%localappdata%",
)


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
    target_l = (target or "").strip().lower()
    blob = f"{tool} {action} {scope_l} {target_l}"
    # W7 (F021, 2026-08-19): the keyword heuristics below used plain `k in blob`.
    # `blob` ends with a FILE PATH, so an ordinary filename could flip policy:
    #   "my_code_tools_notes.txt" -> upgrade_orchestrator (hybrid_upgrade_only)
    #   "data download.csv"       -> a_unreachable_mid_task (allow_with_log_native_event)
    # Both LOOSEN native_policy away from `forbid` for files that have nothing
    # to do with [A]. Match on token boundaries instead, so "upgrade" no longer
    # fires on "downgrade"/"upgrade_notes.md" and "code_tools" no longer fires
    # on "my_code_tools_notes". Underscore is a word char, so \b already
    # handles the _notes/_helper suffixes; the dot in ".txt" is a boundary.
    def _kw_hit(text: str, keywords: tuple[str, ...]) -> bool:
        return any(
            re.search(r"\b" + re.escape(k) + r"\b", text)
            for k in keywords
        )

    # "[A] is unreachable" describes the SESSION, never a path. Keeping the
    # target out of this one blob is what kills the "data download.csv" ->
    # allow_with_log_native_event false positive at the root.
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

    # Scope heuristics
    outside = False
    if scope_l in ("os", "outside_project", "env", "host"):
        outside = True
    elif any(x in target_l.replace("/", "\\") for x in (
        "\\windows\\", "\\system32\\", "\\users\\public",
    )):
        outside = True
    elif project_path and target:
        try:
            pp = Path(project_path).resolve()
            tp = Path(target).resolve()
            if pp.exists() and tp.exists():
                try:
                    tp.relative_to(pp)
                except ValueError:
                    outside = True
        except Exception:
            pass

    # Outside project: prefer outside_project_os_read so allow_readonly applies.
    # Tool maps like Read→read_project_file must not force project-forbid off-root.
    if outside:
        if action in ("write", "execute", "delete", "command") or task_class in (
            "write_existing_file", "create_project_file", "batch_project_write",
            "delete_or_copy_project_file", "project_command", "project_test",
        ):
            # mutating-ish outside still classified; host_gate enforces deny unless approved
            if not task_class:
                task_class = "outside_project_os_read"
        else:
            task_class = "outside_project_os_read"
    if _kw_hit(blob, ("web",)) or "http" in action:
        if not task_class:
            task_class = "general_web_research"
    if _kw_hit(blob, ("upgrade", "hybrid", "edit orchestrator", "code_tools")):
        if not task_class:
            task_class = "upgrade_orchestrator"
    # [A] self-edit: a NATIVE mutation whose target is inside [A]'s own source
    # tree is upgrade_orchestrator (hybrid_upgrade_only), not a plain project
    # write. Without this the tool/action map pinned it to write_existing_file
    # -> native_policy=forbid, which contradicted the standing rule "editing [A]
    # itself: native edit + log_native_event" and made log_native_event answer
    # ok:false / is_forbidden_native:true for the very edits the rule mandates.
    # Only NATIVE transport is reclassified: via_a stays governed as before.
    if (
        tr == "native"
        and is_orchestrator_self_target(target)
        and (
            task_class in _MUTATING_CLASSES_FOR_SELF_EDIT
            or (task_class in _COMMAND_CLASSES_FOR_SELF_EDIT
                and _is_mutating_self_edit_action(action))
            or (not task_class and _is_mutating_self_edit_action(action))
        )
    ):
        task_class = "upgrade_orchestrator"
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
            "allow_with_log_native_event",
            "hybrid_upgrade_only",
        ),
    )
    # hybrid_upgrade_only still requires log; treat as allowed-with-condition
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
        "matrix_version": m.get("version"),
        "matrix_status": m.get("status"),
        "north_star": north_star(m),
        "preferred_via_a": preferred,
        "preflight_hard_stop": preflight_hard_stop,
        "error_name": err_name,
        "ca_next_action": next_act,
    }


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
    """
    aid = (agent_id or "").strip().lower()
    is_hermes = aid in ("hermes", "hermc", "herm")
    scope_l = (scope or "").strip().lower()
    user_approved = scope_l in (
        "user_approved_os_mutate", "anh_approved", "user_go_ahead",
    )

    outside = task_class == "outside_project_os_read" or scope_l in (
        "os", "outside_project", "env", "host",
    )
    if not outside and project_path and target:
        try:
            pp = Path(project_path).resolve()
            tp = Path(target)
            if not tp.is_absolute():
                outside = False
            else:
                tp = tp.resolve()
                try:
                    tp.relative_to(pp)
                    outside = False
                except ValueError:
                    outside = True
        except Exception:
            pass

    if outside:
        allowed = (
            native_policy == "allow_readonly"
            or user_approved
            or transport == "via_a"
        )
        decision = "allow_readonly" if allowed and not user_approved else (
            "allow_user_approved" if user_approved else "deny_outside_project_mutate"
        )
        return {
            "agent_id": aid or None,
            "is_hermes": is_hermes,
            "scope": "outside_project",
            "decision": decision,
            "allowed": decision.startswith("allow"),
            "reason": (
                "Outside project: readonly OS probe OK; mutating host ops need Anh go-ahead"
            ),
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
            elif origin_kind == "native" or transport == "native":
                # Recompute forbid/allow from matrix if tag missing flags inconsistently
                live = classify_action(
                    tool_name=str(tool_name or ""),
                    action_type=str(action_type or ""),
                    transport="native",
                    target=str(target or ""),
                    project_path=project_path,
                    agent_id=agent_id,
                )
                # Prefer live matrix truth for native events; keep tag task_class if better
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
            "version": m.get("version"),
            "status": m.get("status"),
            "task_class_count": len(m.get("rows") or []),
        },
        "a_first": True,
        "host_gate": hermes_host_gate(
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
