# pyright: reportArgumentType=false, reportPossiblyUnboundVariable=false, reportAssignmentType=false, reportIndexIssue=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportUnboundVariable=false
"""
Code Intelligence Tools - the async tool functions shared by the HTTP API and
MCP stdio mode. How many there are is whatever build_tool_map() registered at
import time; ask the daemon (GET /api/tools/names), never this docstring.
All functions return JSON strings for easy transport over HTTP or MCP stdio.
"""
import asyncio
import base64
import difflib
import fnmatch
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil

# C2-followup (2026-08-10): commit 4e4e8bf's message claims "signal/sys imports
# added" but its actual diff never landed them. Both _kill_process_tree
# (signal.SIGKILL) and run_project_command / run_project_test_script
# (sys.platform) raised NameError at runtime — breaking the command/test
# transport runner that 4e4e8bf was supposed to fix. Land them for real.
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import path_runtime
import surgical_mapper
from orchestrator import search_snapshots
from orchestrator.core.text_cap import cap_head_tail
from orchestrator.mission import (
    tools as _mission_tools,  # noqa: F401 - eval'd by BASE_TOOL_MAP_SPECS as '_mission_tools.mission_*'
)
from orchestrator.server.executor_lanes import (
    ExecutorLaneError,
    run_in_executor_lane,
)
from orchestrator.skip_rules import (
    effective_skip_dir_names,
    ignored_directory_name,
    is_write_backup,
    name_matches_ignore,
)
from orchestrator.text_lines import split_lf, split_lines
from rule_core import get_rule_core, get_rule_core_metadata

# ─── Shared constants ─────────────────────────────────────────────────────────

# CAN-042: single canonical SKIP_DIRS source = surgical_mapper.SKIP_DIRS. code_tools
# and orchestrator_server alias it so all three modules share one value (previously
# orchestrator_server held a divergent 11-entry subset).
SKIP_DIRS = surgical_mapper.SKIP_DIRS
MAX_SEARCH_RESULTS = 50
MAX_SEARCH_CONTEXT_LINES = 1
MAX_READ_LINES = 200            # default span when max_lines not given (token economy)
MAX_READ_LINES_HARD_CAP = 800   # R81: explicit max_lines may reach this for long spec/code reads
MAX_COMMAND_OUTPUT = 12000
WORKFLOW_STATE_FILENAME = "WORKFLOW_STATE.json"
SESSION_BOOTSTRAP_FILENAME = "CA_SESSION_BOOTSTRAP.md"

_BACKGROUND_PROCESSES: dict[int, subprocess.Popen] = {}
_BACKGROUND_PROCESSES_LOCK = threading.Lock()

# Full-tree INDEX drift scans are blocking filesystem work. Keep them off the
# asyncio loop and out of the default executor used by unrelated tools.
_INDEX_IO_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="orchestrator-index-io")
_CACHE_DELTA_FUTURES_LOCK = threading.Lock()
_CACHE_DELTA_FUTURES: dict[object, Future[str]] = {}

def _close_process_streams(proc: subprocess.Popen) -> None:
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(proc, stream_name, None)
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass


def _prune_background_processes() -> None:
    completed = []
    with _BACKGROUND_PROCESSES_LOCK:
        for pid, proc in list(_BACKGROUND_PROCESSES.items()):
            try:
                is_complete = proc.poll() is not None
            except Exception:
                is_complete = True
            if is_complete:
                completed.append(_BACKGROUND_PROCESSES.pop(pid))
    for proc in completed:
        _close_process_streams(proc)


def _register_background_process(proc: subprocess.Popen) -> None:
    _prune_background_processes()
    with _BACKGROUND_PROCESSES_LOCK:
        _BACKGROUND_PROCESSES[proc.pid] = proc


def _reap_background_process(
    pid: int,
    timeout: float = 5,
    kill_if_running: bool = True,
) -> None:
    _prune_background_processes()
    process_pid = int(pid)
    with _BACKGROUND_PROCESSES_LOCK:
        proc = _BACKGROUND_PROCESSES.pop(process_pid, None)
    if proc is None:
        return
    keep_registered = False
    try:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if not kill_if_running:
                keep_registered = True
                with _BACKGROUND_PROCESSES_LOCK:
                    current = _BACKGROUND_PROCESSES.get(process_pid)
                    if current is None or current is proc:
                        _BACKGROUND_PROCESSES[process_pid] = proc
                return
            proc.kill()
            proc.wait()
    finally:
        if not keep_registered:
            _close_process_streams(proc)


def _shared_inplace_write_bytes(path: Path, tmp: Path) -> str:
    last_exc: Exception | None = None
    for _ in range(5):
        try:
            mode = "r+b" if path.exists() else "wb"
            with tmp.open("rb") as src, path.open(mode) as dst:
                dst.seek(0)
                shutil.copyfileobj(src, dst)
                dst.truncate()
                dst.flush()
                os.fsync(dst.fileno())
            return "shared_inplace"
        except PermissionError as exc:
            last_exc = exc
            time.sleep(0.05)
    if last_exc:
        raise last_exc
    return "shared_inplace"


def _atomic_write_bytes(path: Path, data: bytes) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    keep_tmp = False
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.replace(str(tmp), str(path))
            return "atomic_replace"
        except PermissionError:
            keep_tmp = True
            return _shared_inplace_write_bytes(path, tmp)
    except Exception:
        try:
            if not keep_tmp:
                tmp.unlink()
        except Exception:
            pass
        raise
    finally:
        if keep_tmp:
            try:
                tmp.unlink()
            except Exception:
                pass


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> str:
    return _atomic_write_bytes(Path(path), text.encode(encoding))


def _atomic_write_json(path: Path, data, *, indent: int | None = None) -> None:
    _atomic_write_text(Path(path), json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")

# === E402 ANCHOR NOTE -- READ THIS BEFORE ANY LINT SWEEP ON code_tools.py ===================
# E402 CLUSTER: this line plus the three `from orchestrator.council.route_metrics import (...)`
# blocks below (4 sites). All 17 E402 in this file are OUT OF GATE BY DESIGN -- ruff.toml selects
# only E9/F63/F7 and its baseline block records the count, which tests/test_a38b_lint_contract.py
# parses, so the number is anchored by a test. This module is a 15.6k-line aggregator: every
# section keeps its imports under its own banner, hence module-level imports after the helpers.
# MEASURED 2026-09-20: orchestrator/core/registry_data.py and orchestrator/council/route_metrics.py
# do NOT import code_tools back. This is NOT a circular-import guard -- do not document it as one,
# and do not hoist these imports to the top: churn with no behaviour change.
# THE REAL HAZARD HERE IS F401, NOT E402: registry_data.BASE_TOOL_MAP_SPECS names its handlers as
# STRING expressions that build_tool_map() eval()s against code_tools globals() at the bottom of
# this file, so ruff sees no reference to them. `ruff --fix` deleting such an import makes the
# daemon die at import with NameError -- that is why those lines carry a noqa F401 suppression
# (spelled without the hash here on purpose: ruff parses a literal noqa token inside a comment and
# would warn about an invalid directive). Never run a
# blanket `ruff --fix` or auto-organise-imports on this file. The other three E402 clusters
# (~L6432 logging helpers, ~L7765 T4 runtime shims, ~L11503 workflow dispenser) point back here.
from orchestrator.core.registry_data import (
    BASE_TOOL_MAP_SPECS,
    T1_TOOLS,  # noqa: F401 - re-exported: tool_smoke_runner.py reads ct.T1_TOOLS
    T2_TOOLS,  # noqa: F401 - re-exported: tool_smoke_runner.py reads ct.T2_TOOLS
    T3_TOOLS,  # noqa: F401 - re-exported: tool_smoke_runner.py reads ct.T3_TOOLS
    V15_NEW_TOOL_SPECS,
    V15_R8_NEW_SCHEMA_SPECS,
    V15_TIERS,
    build_tool_map,
    build_tool_phases,
    build_tool_schemas,
)
from orchestrator.council.route_metrics import (
    build_scorecard as _route_build_scorecard,
)
from orchestrator.council.route_metrics import (
    load_measurements as _route_load_measurements,
)
from orchestrator.council.route_metrics import (
    write_scorecard_files as _route_write_scorecard_files,
)

PROTECTED_TOOL_NAMES = {
    "write_fix", "write_fix_html", "write_new_file", "delete_file", "ruff_fix",
    "replace_between_anchors",  # R43 P1 #3-B anchor-based patch (write op)
    "run_project_test_script",  # R43 P1 #5 artifact-script runner (executes code)
    "run_project_command", "kill_process", "backup_project", "refresh_project_map",
    "generate_project_map", "prepare_ca_session",
    "set_workflow_mode",
    # rule_drift_check cleanup: update_file_cache / create_session_bootstrap /
    # update_workflow_state are INTERNAL functions (not in TOOL_MAP, not callable via
    # /api/call), so PROTECTED/mutation_scope metadata for them was dead. Removed.
    "create_build_context", "create_benchmark_request", "close_session",
    "rename_session", "log_native", "log_native_event", "apply_after_dry_run",
    "create_pending_audit", "sync_pending_audit",
    "record_error_resolution",
    "clear_closed_sessions",  # PC1-CLEANUP: session-lifecycle cleanup (delete closed rows)
    # V15 R28 — runtime-mutating tools (UI/DOM/process).
    # These were missing from protected set → metadata mismatch surfaced in audit.
    "fill_folder_dialog", "cancel_native_dialog", "invoke_app_function",
    "click_and_handle_folder_dialog",  # V15 R31: atomic click+dialog macro (mutates UI)
    "click", "fill", "click_selector", "fill_value", "click_and_checkpoint_loop",
    "run_script", "launch_electron_with_cdp", "ensure_single_electron_instance",
    "cleanup_test_session", "desktop_click", "desktop_send_text",
    "assert_button_click_changes_state", "restart_orchestrator_via_task",
    "restart_orchestrator_via_bat",
    "save_log_snapshot",
    # R81 CEP deploy tooling — file-mutating, require token + session
    "copy_file", "cep_deploy", "create_folder", "create_folders",
    # Council SOT sync — rewrites derived rule docs
    "council_config_sync",
    # CAN-011/R29 acceptance: process-scope + TOKEN_REQUIRED runners must also be
    # origin/session-protected (scope_process_but_not_protected was false drift).
    "run_ps1_script", "run_node_script",
    # B5: P5 app-lifecycle wrappers. start_app -> run_project_command (arbitrary
    # shell), stop_app -> kill_process, browser_smoke_test -> open_browser_page
    # (spawns Chromium). In-process calls bypass the HTTP token gate, so these
    # wrappers must be origin/session-protected like what they wrap.
    "start_app", "stop_app", "browser_smoke_test",
    # CBM derived-state writers. They never edit source, but they do write
    # graph.db and must not be callable as ungated T1 reads.
    "cbm_index", "cbm_incremental_index", "cbm_get_architecture",
    "cbm_dead_code", "cbm_test_gap", "cbm_what_if", "cbm_cycle_point",
}

# Token gate (X-Orchestrator-Token) is DECOUPLED from mutating/session semantics.
# PROTECTED_TOOL_NAMES above still drives mutating + requires_session (via
# orchestrator_server._MUTATING_TOOLS). But ONLY these genuinely destructive /
# irreversible tools require the token; every other mutating tool keeps its
# mutating+session semantics yet runs token-free. (User decision 2026-06-11: narrow
# the token to the destructive set to cut friction; intent is still gated by the
# human 'tiến hành' trigger + localhost-only daemon.)
TOKEN_REQUIRED_TOOLS = {
    "delete_file",                    # irreversible file delete
    "clear_closed_sessions",          # irreversible delete of closed-session audit rows (PC1-CLEANUP: matches existing REST handler which already requires X-Orchestrator-Token)
    "kill_process",                   # kills processes
    "run_project_command",            # arbitrary shell exec
    "run_project_test_script",        # runs artifact code
    "run_ps1_script",                 # arbitrary PowerShell
    "run_node_script",                # arbitrary Node
    "write_transaction",              # atomic multi-file overwrite
    "cep_deploy",                     # mirrors to LIVE
    "restart_orchestrator_via_task",  # restarts [A]
    "restart_orchestrator_via_bat",   # restarts [A] via canonical [A:S] script
    # B5: start_app wraps run_project_command (arbitrary shell) and stop_app wraps
    # kill_process, both in-process (HTTP token gate bypassed) -> inherit the gate.
    "start_app", "stop_app",
}

# V15 R28 — Risk-scope metadata (per-tool). Surfaces in /api/tools so CA can
# distinguish mutation kind (filesystem vs runtime UI vs process vs audit-only)
# instead of binary protected. Bridge to R29 split-sets refactor.
_MUTATION_SCOPE_MAP = {
    # file_artifact — writes to disk (project files, artifacts, cache, index)
    "write_fix": "file_artifact", "write_fix_html": "file_artifact",
    "write_new_file": "file_artifact", "delete_file": "file_artifact",
    "ruff_fix": "file_artifact",
    "replace_between_anchors": "file_artifact",  # R43 P1 #3-B
    "run_project_test_script": "process",  # R43 P1 #5 — executes user code
    "save_log_snapshot": "file_artifact", "backup_project": "file_artifact",
    "refresh_project_map": "file_artifact", "generate_project_map": "file_artifact",
    "apply_after_dry_run": "file_artifact",
    # runtime_ui — mutates DOM / native UI controls
    "click": "runtime_ui", "fill": "runtime_ui", "click_selector": "runtime_ui",
    "fill_value": "runtime_ui", "click_and_checkpoint_loop": "runtime_ui",
    "fill_folder_dialog": "runtime_ui", "cancel_native_dialog": "runtime_ui",
    "invoke_app_function": "runtime_ui", "run_script": "runtime_ui",
    "click_and_handle_folder_dialog": "runtime_ui",  # R31
    "desktop_click": "runtime_ui", "desktop_send_text": "runtime_ui", "assert_button_click_changes_state": "runtime_ui",
    # process — spawns / kills processes
    "kill_process": "process", "launch_electron_with_cdp": "process",
    "ensure_single_electron_instance": "process", "cleanup_test_session": "process",
    "run_project_command": "process", "restart_orchestrator_via_task": "process",
    # campaign-1608 (A11-b): restart_orchestrator_via_bat was the ONLY entry in the
    # gate set (PROTECTED | TOKEN_REQUIRED) with no scope, so /api/tools
    # reported mutation_scope='none' -- "read-only" -- for the tool that kills and
    # relaunches the whole daemon. Its sibling restart_orchestrator_via_task was
    # already 'process'; the two differ only in launcher (bat vs schtasks).
    # It slipped through because it is registered from orchestrator_server.py while
    # this map lives here, so a plain `import code_tools` cannot see it in TOOL_MAP
    # -- which is also why rule_drift_check's coverage loop (`if name not in
    # tool_keys: continue`) skipped it instead of flagging it.
    "restart_orchestrator_via_bat": "process",
    # CAN-011: arbitrary-script runners were missing a scope despite being mutating +
    # token-required (run shell/Node code) — surfaced "none" in /api/tools.
    "run_ps1_script": "process", "run_node_script": "process",
    # audit — session/event log mutations (low risk)
    "open_session": "audit", "close_session": "audit", "rename_session": "audit",
    "clear_closed_sessions": "audit",
    "log_native": "audit", "log_native_event": "audit",
    "create_pending_audit": "audit", "sync_pending_audit": "audit",
    "record_error_resolution": "audit",
    "set_workflow_mode": "audit",
    "prepare_ca_session": "audit",
    "create_build_context": "audit", "create_benchmark_request": "audit",
    # V15 R29: fill scope for protected tools previously missing
    "write_transaction": "file_artifact",  # atomic multi-file write
    "evaluate_js": "runtime_ui",            # executes JS in page
    "open_browser_page": "process",         # spawns Chromium
    # B5: P5 process/shell wrappers mirror run_project_command / kill_process /
    # open_browser_page. http_check is a stdlib HTTP probe -> no mutation scope.
    "start_app": "process", "stop_app": "process",
    "browser_smoke_test": "process",
    "desktop_app_focus": "runtime_ui",      # focuses window via CDP/Win32
    "desktop_window_state": "runtime_ui",   # Win32 ShowWindow state mutation
    "session_audit_summary": "audit",       # session aggregation read+mark
    # R81 CEP deploy tooling
    "copy_file": "file_artifact", "cep_deploy": "file_artifact",
    "create_folder": "file_artifact", "create_folders": "file_artifact",
    # Council SOT
    "council_config_sync": "file_artifact",
    # CAN-051: derived-state writers — tools that stay non-source-mutating and
    # token-free but write derived artifacts (index/cache/audit logs). Declared
    # so catalog/schema say WHAT they touch instead of claiming 'none'.
    "list_structure": "cache",                 # structure_output.txt when save_output=true
    "audit_tool_matrix": "audit_log",          # appends .orchestrator_cache/matrix_log.jsonl
    "query_index": "derived_index",            # auto_refresh=true rebuilds INDEX inline
    "cbm_index": "derived_index",
    "cbm_incremental_index": "derived_index",
    "cbm_get_architecture": "derived_index",   # hybrid auto-reindex when stale
    "cbm_dead_code": "derived_index",
    "cbm_test_gap": "derived_index",
    "cbm_what_if": "derived_index",
    "cbm_cycle_point": "derived_index",
    # E-03 (2026-08-10): stateful workflow / mission tools. These do not edit
    # source files, but they DO mutate durable run-state (workflow_state/*.json,
    # missions.db) or trigger side effects (dispatch). Previously they were
    # reported as mutating:false with mutation_scope='none', which mis-led
    # read-caller tooling into assuming idempotence. The 'stateful' scope is
    # metadata-only: it does NOT gate origin/session/token (those still belong
    # to PROTECTED_TOOL_NAMES/TOKEN_REQUIRED_TOOLS), it purely surfaces the
    # side-effect contract so callers stop treating them as pure reads.
    # Workflow writers
    "workflow_activate": "stateful",
    "workflow_deactivate": "stateful",
    "workflow_create_from_ui": "stateful",
    "workflow_draft_create": "stateful",
    "workflow_goto_step": "stateful",
    "workflow_reset": "stateful",
    "workflow_retry_step": "stateful",
    "workflow_save_md": "stateful",
    "workflow_skip_step": "stateful",
    "workflow_start": "stateful",
    "workflow_submit_step": "stateful",
    # Mission writers / dispatch triggers
    "mission_create": "stateful",
    "mission_dispatch": "stateful",
    "mission_gate": "stateful",
    "mission_report": "stateful",
    # Pure reads stay at default scope='none' (workflow_active_get,
    # workflow_get_current, workflow_list, workflow_read_md, workflow_status,
    # workflow_status_registry, workflow_draft_get, workflow_checkpoint,
    # mission_next, mission_status) — metadata honesty cuts BOTH ways.
}

# A37: the CAN-051 group, named ONCE. Every scope in here is a writer that the map
# above calls non-source-mutating and token-free: it touches [A]'s own index, cache or
# audit log, never the caller's files. That distinction is the only thing a CA is
# really asking about ("can this change MY source?"), and it was previously re-derived
# by hand in orchestrator_server._r102_tool_categories -- where guessing it from the
# tool-name prefix had already filed the entire cbm family as write and hidden it from
# category="read". One SOT, so a new scope cannot be added here and silently rot there.
NON_SOURCE_MUTATION_SCOPES = frozenset({"none", "cache", "audit_log", "derived_index"})


def tool_mutation_scope(tool_name: str) -> str:
    """V15 R28 — return mutation_scope for a tool. Default 'none' = read-only."""
    return _MUTATION_SCOPE_MAP.get(tool_name, "none")
TOOL_PHASES = build_tool_phases()
PHASE_LABELS = {
    "T1": "Discover / Analyze / Read",
    "T2": "Mutate / Verify / Refresh",
    "T3": "Session / Audit / Auxiliary",
}


def tool_phase(tool_name: str) -> str:
    return TOOL_PHASES.get(tool_name, "T3")


def _is_unc_path_like(value) -> bool:
    # V15 — UNC timeout guard. A dead/unreachable SMB/UNC share makes
    # Path.resolve()/exists/is_dir/stat block for 30-60s. Detect UNC-like
    # strings cheaply here (NO Path/resolve/exists calls) so auto-root and
    # session inference can skip the probe entirely. This does NOT forbid
    # intentional UNC access — that still routes through check_server_path
    # (timeout-aware) or an explicit absolute file_path without project_path.
    try:
        s = str(value).strip()
    except Exception:
        return False
    return s.startswith("\\\\") or s.startswith("//")


def find_project_root(file_path: str) -> str | None:
    # V15 R56 — empty/None file_path silently resolved to Path("").resolve() =
    # current-working-directory of the orchestrator process. That makes the
    # session guard falsely use ToolA (where orchestrator runs) as the project
    # for tools called WITHOUT any path/file_path hint — masking session
    # mismatches for page-bound T4 tools targeting other projects. Explicit
    # None on empty input forces the caller's resolution chain to fall through
    # to the next strategy (e.g. _resolve_project_from_page_id).
    if not file_path or not str(file_path).strip():
        return None
    path_runtime.reject_c0_control_characters(file_path, field="file_path")
    # UNC timeout guard: never auto-resolve/probe a UNC path during root inference.
    if _is_unc_path_like(file_path):
        return None
    path = Path(file_path).resolve()
    # V15 R56+ (campaign-1608): never treat the user HOME dir, any ancestor of
    # HOME, or a filesystem/drive root as a project root. A stray marker there
    # (e.g. a junk .orchestrator_cache / package.json sitting in C:\Users\<user>)
    # would otherwise hijack root inference for tempfile-based paths — which live
    # under HOME via %TEMP% — pointing writes at a mislocated, unbounded snapshot
    # store and hanging the write pipeline. Returning None lets the caller's
    # resolve chain fall through to its next strategy (the documented R56 intent).
    try:
        _home = Path.home().resolve()
    except Exception:
        _home = None
    for parent in [path] + list(path.parents):
        if not parent.exists():
            continue
        if parent == parent.parent:  # filesystem/drive root (e.g. C:\ or D:\)
            continue
        if _home is not None:
            if parent == _home:
                continue
            try:
                _home.relative_to(parent)  # parent is an ancestor of HOME
                continue
            except ValueError:
                pass
        if (parent / ".orchestrator_cache").exists() or (parent / "package.json").exists() or (parent / "requirements.txt").exists() or (parent / ".git").exists():
            return str(parent)
    return None


def _looks_like_project_root(path: Path) -> bool:
    return (
        (path / ".orchestrator_cache").exists()
        or (path / "package.json").exists()
        or (path / "requirements.txt").exists()
        or (path / ".git").exists()
    )


def _quote_ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _relative_to_or_none(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return ""


def _resolve_project_path_payload(input_path: str, preferred_root: str = "", mode: str = "auto") -> dict:
    raw_input = str(input_path or "").strip()
    if not raw_input:
        return {
            "ok": False,
            "input_path": input_path,
            "normalized_path": "",
            "exists": False,
            "is_file": False,
            "is_dir": False,
            "project_path": "",
            "file_path": "",
            "folder_path": "",
            "suggested_session_title": "",
            "path_notes": ["input_path is required"],
            "needs_user_confirmation": True,
            "ca_next_action": "ask_user_for_path",
        }

    # UNC timeout guard: do not expanduser/resolve/exists-probe a UNC path or
    # UNC preferred_root during project-root inference (dead SMB = 30-60s hang).
    if _is_unc_path_like(raw_input) or _is_unc_path_like(preferred_root):
        return {
            "ok": False,
            "input_path": input_path,
            "normalized_path": raw_input,
            "exists": False,
            "is_file": False,
            "is_dir": False,
            "project_path": "",
            "file_path": "",
            "folder_path": "",
            "suggested_session_title": "",
            "path_notes": [
                "UNC path auto-inference unsupported; use check_server_path for UNC "
                "or choose a local project root"
            ],
            "needs_user_confirmation": True,
            "error_name": "UNC_PROJECT_ROOT_UNSUPPORTED",
            "ca_next_action": "use_check_server_path_for_unc_or_choose_local_project_root",
        }

    preferred = Path(preferred_root).expanduser().resolve() if preferred_root else None
    path = Path(raw_input).expanduser()
    if not path.is_absolute() and preferred:
        path = preferred / path
    path = path.resolve()
    exists = path.exists()
    is_file = path.is_file()
    is_dir = path.is_dir()
    notes: list[str] = []

    project_root: Path | None = None
    if preferred and preferred.exists():
        try:
            path.relative_to(preferred)
            project_root = preferred
            notes.append("preferred_root contains input_path")
        except ValueError:
            notes.append("preferred_root ignored because input_path is outside it")

    if project_root is None:
        if exists and is_dir and (mode == "folder" or _looks_like_project_root(path)):
            project_root = path
            notes.append("input_path is a project root")
        elif exists and is_dir:
            found = find_project_root(str(path))
            if found:
                project_root = Path(found).resolve()
                notes.append("project root found from parent markers")
            else:
                project_root = path
                notes.append("existing folder accepted as explicit project_path (R81: no ancestor climb)")
        else:
            found = find_project_root(str(path))
            if found:
                project_root = Path(found).resolve()
                notes.append("project root found from parent markers")

    needs_confirmation = False
    if project_root is None:
        needs_confirmation = True
        ca_next_action = "ask_user_to_confirm_project_root"
    else:
        ca_next_action = "use_project_path_for_session"

    if mode == "file" and exists and not is_file:
        needs_confirmation = True
        notes.append("mode=file but input_path is not a file")
        ca_next_action = "ask_user_for_file_path"
    if mode == "folder" and exists and not is_dir:
        notes.append("mode=folder received a file path; resolved project root when available")

    file_path = ""
    folder_path = ""
    if project_root:
        if is_file:
            file_path = _relative_to_or_none(path, project_root) or str(path)
        elif is_dir:
            folder_path = _relative_to_or_none(path, project_root) or str(path)

    if exists and is_file:
        notes.append("input_path is a file; do not pass it directly as project_path")
    if not exists:
        notes.append("input_path does not exist")
        if project_root is None:
            needs_confirmation = True

    return {
        "ok": project_root is not None and not needs_confirmation,
        "input_path": input_path,
        "normalized_path": str(path),
        "exists": exists,
        "is_file": is_file,
        "is_dir": is_dir,
        "project_path": str(project_root) if project_root else "",
        "file_path": file_path,
        "folder_path": folder_path,
        "suggested_session_title": project_root.name if project_root else "",
        "path_notes": notes,
        "needs_user_confirmation": needs_confirmation,
        "ca_next_action": ca_next_action,
    }


def _path_quote_helper_payload(project_path: str = "", subfolder: str = "", command: str = "", shell: str = "powershell") -> dict:
    root = Path(project_path).resolve() if project_path else Path.cwd().resolve()
    sub = str(subfolder or "").strip()
    cmd = str(command or "").strip()
    shell_name = (shell or "powershell").lower()
    warnings: list[str] = []

    blocked = bool(re.search(r'(^|\s)(curl|wget|bash|cat|grep|sed|awk)(\s|$)', cmd, re.I))
    destructive = bool(re.search(r'(^|\s)(del|erase|rd|rmdir|rm|remove-item)(\s|$)', cmd, re.I))
    if blocked:
        warnings.append("Windows-first sessions block curl/wget/bash/cat/grep/sed/awk.")
    if destructive:
        warnings.append("Destructive command templates require explicit confirmation.")

    cwd = root / sub if sub else root
    uses_set_location = bool(sub)
    # R16.9: support cmd alongside powershell — Windows ENV uses ["cmd","/c",...]
    if shell_name in {"powershell", "pwsh"}:
        shell_label = "powershell"
    elif shell_name in {"cmd", "cmd.exe", "batch"}:
        shell_label = "cmd"
    else:
        warnings.append(f"Unknown shell '{shell_name}'; defaulting to powershell. Accepted: powershell|pwsh|cmd.")
        shell_label = "powershell"

    if shell_label == "powershell":
        if uses_set_location:
            safe_command = f"powershell -NoProfile -Command \"Set-Location -LiteralPath {_quote_ps_literal(sub)}; {cmd}\""
            quoting_strategy = "powershell_set_location_literal_subfolder"
        else:
            safe_command = cmd
            quoting_strategy = "project_path_as_cwd"
    else:  # cmd
        # CMD: use cmd /c with double-quote escaping; pushd/popd for cwd-with-spaces
        # Double up internal quotes for cmd /c per Windows rule: cmd /c ""quoted path""
        if uses_set_location:
            cwd_str = str(root / sub)
            # pushd handles paths with spaces by quoting; popd restores
            safe_command = f'cmd /c "pushd "{cwd_str}" && {cmd} && popd"'
            quoting_strategy = "cmd_pushd_quoted_subfolder"
        else:
            # If command has a .bat with embedded spaces, wrap whole thing in cmd /c ""...""
            if re.search(r'\.bat\b', cmd, re.I) and ' ' in cmd:
                safe_command = f'cmd /c ""{cmd}""'
                quoting_strategy = "cmd_double_quoted_bat"
            else:
                safe_command = f'cmd /c "{cmd}"'
                quoting_strategy = "cmd_single_quoted"
    if re.search(r'(?i)(^|\s)[A-Z]:\\[^"]*\s[^"]*\.bat(\s|$)', cmd) or re.search(r'(?i)(^|\s)\.?\\[^"]*\s[^"]*\.bat(\s|$)', cmd):
        warnings.append('Wrap .bat paths containing spaces in double quotes when using cmd /c, e.g. cmd /c ""D:\\path with spaces\\start.bat""')

    return {
        "ok": bool(cmd) and not blocked and not destructive,
        "project_path": str(root),
        "cwd": str(cwd),
        "safe_command": safe_command,
        "command_executed": safe_command,
        "uses_set_location": uses_set_location,
        "quoting_strategy": quoting_strategy,
        "shell": shell_label,
        "warnings": warnings,
        "bat_path_with_spaces_hint": 'Use cmd /c ""D:\\path with spaces\\script.bat"" or PowerShell Start-Process -FilePath with a quoted LiteralPath.',
        "blocked": blocked or destructive or not bool(cmd),
        "ca_next_action": "use_safe_command" if bool(cmd) and not (blocked or destructive) else "stop_and_confirm",
    }


def _update_cache_after_write(file_path: str, project_root: str | None = None) -> bool:
    root = str(Path(project_root).resolve()) if project_root else find_project_root(file_path)
    if not root:
        return False
    try:
        surgical_mapper.update_file_cache(file_path, root)
        # Verify: re-read INDEX and confirm mtime matches disk within threshold
        fp = Path(file_path).resolve()
        disk_mtime = fp.stat().st_mtime
        idx_path = _project_cache_dir(root) / "INDEX.json"
        if idx_path.exists():
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            rel = str(fp.relative_to(Path(root).resolve())).replace("\\", "/")
            entry = (idx.get("files", {}).get(rel)
                     or idx.get("files", {}).get(rel.replace("/", "\\")))
            if entry:
                indexed_mtime = float(entry.get("mtime") or 0)
                if abs(disk_mtime - indexed_mtime) > 0.5:
                    # F7: Mtime mismatch — retry once, then verify result
                    surgical_mapper.update_file_cache(file_path, root)
                    try:
                        idx2 = json.loads(idx_path.read_text(encoding="utf-8"))
                        entry2 = (idx2.get("files", {}).get(rel)
                                  or idx2.get("files", {}).get(rel.replace("/", "\\")))
                        if entry2:
                            mtime2 = float(entry2.get("mtime") or 0)
                            if abs(disk_mtime - mtime2) > 0.5:
                                import logging as _log_caw
                                _log_caw.warning(
                                    f"_update_cache_after_write: mtime still mismatched after retry for {file_path}"
                                )
                                return False
                    except Exception:
                        pass
        return True
    except search_snapshots.SnapshotDeadlineExceeded:
        # Fail loud (7-RULE #7): a snapshot deadline means the write path hit an
        # unhealthy/oversized snapshot store (the exact hang this budget guards).
        # Never mask it as a mere cache_synced=False — surface it to the caller.
        raise
    except Exception:
        return False


def _text_file_extensions() -> set[str]:
    text_exts = set(getattr(surgical_mapper, "SUPPORTED_EXTENSIONS", set()))
    text_exts.update({".txt", ".md", ".json", ".html", ".css", ".csv", ".xml", ".yaml", ".yml", ".log"})
    return text_exts


def _is_binary_file(file_path: Path, sample_size: int = 4096, allow_utf16_text: bool = False) -> bool:
    """Conservative binary guard for text-diff mutation tools."""
    try:
        sample = file_path.read_bytes()[:sample_size]
    except Exception:
        return False
    text_exts = _text_file_extensions()
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return not (allow_utf16_text and file_path.suffix.lower() in text_exts)
    if b"\x00" in sample:
        return True
    if file_path.suffix.lower() in text_exts:
        return False
    try:
        sample.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def _resolve_project_file(project_path: str | None, file_path: str) -> Path:
    # UNC timeout guard.
    if project_path and _is_unc_path_like(project_path):
        raise ValueError(
            "UNC project_path không dùng cho project guard (auto-resolve sẽ treo "
            "trên SMB 30-60s). Dùng check_server_path cho UNC, hoặc bỏ project_path "
            "nếu file_path là absolute UNC path có chủ đích."
        )
    if _is_unc_path_like(file_path):
        if project_path:
            raise ValueError(
                "UNC file_path kèm project_path không thể relative_to root an toàn. "
                "Bỏ project_path nếu đây là absolute UNC path có chủ đích, hoặc dùng "
                "check_server_path."
            )
        # Intentional absolute UNC file access: giữ capability, KHÔNG resolve()
        # (resolve() sẽ treo trên dead SMB share).
        return Path(file_path)
    fp = Path(file_path)
    if fp.is_absolute():
        resolved = fp.resolve()
        if project_path:
            root = Path(project_path).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                raise ValueError(
                    f"File path '{file_path}' is outside project root '{project_path}'. "
                    f"Dùng đúng project_path hoặc bỏ project_path nếu không cần guardrail."
                )
        return resolved
    if not project_path:
        return fp.resolve()
    root = Path(project_path).resolve()
    resolved = (root / fp).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(f"File path escapes project root: {file_path}")
    return resolved


def _project_cache_dir(project_path: str) -> Path:
    # UNC timeout guard: project cache auto-infer trên UNC không supported.
    if _is_unc_path_like(project_path):
        raise ValueError(
            "UNC project_path không hỗ trợ project cache auto-infer "
            "(.orchestrator_cache trên SMB sẽ treo). Dùng local project root, "
            "hoặc check_server_path cho UNC."
        )
    return Path(project_path).resolve() / ".orchestrator_cache"


def _backup_manifest_status(project_path: str) -> str:
    manifest_path = _project_cache_dir(project_path) / "BACKUP_MANIFEST.json"
    if not manifest_path.exists():
        return "missing"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        backups = manifest.get("backups", [])
        if not backups and manifest.get("backup_path"):
            backups = [{"backup_path": manifest["backup_path"]}]
        return "available" if any(Path(b.get("backup_path", "")).exists() for b in backups) else "missing"
    except Exception:
        return "unknown"


def _session_project_key(path: str) -> str:
    # UNC timeout guard: KHÔNG resolve()/find_orchestrator_dir trên UNC (treo
    # SMB). Dùng raw stripped string làm session key ổn định.
    if _is_unc_path_like(path):
        return str(path).strip()
    p = Path(path).resolve()
    embedded = path_runtime.find_orchestrator_dir(p)
    if embedded and embedded == p and (p.parent / ".orchestrator_cache").exists():
        return str(p.parent)
    return str(p)


def _load_index(project_path: str) -> dict:
    index_path = _project_cache_dir(project_path) / "INDEX.json"
    if not index_path.exists():
        return {}
    return json.loads(index_path.read_text(encoding="utf-8"))


def _pin_snapshot_index(project_root: str | Path):
    pin = search_snapshots.pin_current_snapshot(project_root)
    try:
        return pin, pin.read_index()
    except Exception:
        pin.release()
        raise


class _QueryIndexDeadline(Exception):
    """Internal: query_index hard wall-clock budget spent (P2-SRCH-01)."""

    def __init__(self, operation: str):
        super().__init__(operation)
        self.operation = operation


def _pin_snapshot_index_with_deadline(project_root: str | Path, deadline_at: float):
    """Deadline-aware variant of _pin_snapshot_index (P2-SRCH-01).

    Threads deadline_at into pin_current_snapshot so a contended state lease
    raises SnapshotDeadlineExceeded instead of blocking through the client's
    HTTP timeout; the async caller converts that into a structured degraded
    response (INDEX_QUERY_TIMEOUT, category index_not_ready — a lease is
    contended, not a stale index) rather than a misleading matched:0.
    """
    pin = search_snapshots.pin_current_snapshot(project_root, deadline_at=deadline_at)
    try:
        return pin, pin.read_index()
    except Exception:
        pin.release()
        raise


def _scoped_session_artifact_path(project_path: str, filename: str,
                                  agent_id: str = "", host_session_id: str = "") -> Path:
    """Return a collision-resistant per-caller artifact path when scope is present."""
    if not (agent_id.strip() or host_session_id.strip()):
        return _project_cache_dir(project_path) / filename
    identity = json.dumps(
        {"agent_id": agent_id.strip(), "host_session_id": host_session_id.strip()},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    label_source = agent_id.strip() or host_session_id.strip()
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", label_source).strip("._-")[:48] or "caller"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return _project_cache_dir(project_path) / "sessions" / f"{label}-{digest}" / filename


def _workflow_state_path(project_path: str, agent_id: str = "",
                         host_session_id: str = "") -> Path:
    return _scoped_session_artifact_path(
        project_path, WORKFLOW_STATE_FILENAME, agent_id, host_session_id,
    )


def _bootstrap_path(project_path: str, agent_id: str = "",
                    host_session_id: str = "") -> Path:
    return _scoped_session_artifact_path(
        project_path, SESSION_BOOTSTRAP_FILENAME, agent_id, host_session_id,
    )


def _truncate_output(text: str | None, limit: int = MAX_COMMAND_OUTPUT) -> str:
    return cap_head_tail(text, limit)[0]


# V15 R49 — Error phase taxonomy (machine-readable). Every _json_error
# response carries a `phase` field so CA can route by category instead of
# parsing free-form messages. Six phases:
#   - policy:              user-rule violation (transport ban, missing token, backup gate)
#   - schema:              shape/type/required-field violations
#   - session:             session/workflow lifecycle (not started/active/found)
#   - environment:         filesystem/process/native dependency missing
#   - retryable_transport: stale cache, file mutated outside, CDP transient
#   - runtime:             generic catch-all (logic/data failure inside tool)
_R49_PHASE_MAP = {
    # policy
    "POWERSHELL_INLINE_FORBIDDEN": "policy",
    "NODE_INLINE_FORBIDDEN": "policy",
    "TRANSPORT_POLICY_VIOLATION": "policy",
    "MUTATION_TOKEN_REQUIRED": "policy",
    "BACKUP_REQUIRED": "policy",
    "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED": "policy",  # CASE-2008
    "DESTRUCTIVE_SCRIPT_FOOTGUN": "policy",  # CASE-2008
    # schema
    "UNSUPPORTED_FIELD": "schema",
    "UNSUPPORTED_DRY_RUN": "schema",
    "UNSUPPORTED_PROTOCOL": "schema",
    "MISSING_REQUIRED_FIELD": "schema",
    "WRONG_ARGUMENTS": "schema",
    "INVALID_BASE64": "schema",
    "INVALID_STATUS": "schema",
    "INVALID_STEP_RANGE": "schema",
    "INVALID_RANGE_STATUS": "schema",
    "STEP_MISMATCH": "schema",
    "SCHEMA_VIOLATION": "schema",
    # F13 2026-09-27: a corrupt request body is the CALLER's payload to repair, so it
    # routes with the other schema violations. Unregistered it fell to the "runtime"
    # default in _phase_for_error_name, which tells a CA the daemon misbehaved and
    # sends it looking for a server-side cause that does not exist.
    "INVALID_JSON_BODY": "schema",
    # session
    "ACTIVE_SESSION_REQUIRED": "session",
    "SESSION_NOT_FOUND": "session",
    "SESSION_CLOSED": "session",
    "WORKFLOW_NOT_STARTED": "session",
    "WORKFLOW_NOT_FOUND": "session",
    "WORKFLOW_ALREADY_COMPLETE": "session",
    "PROJECT_PATH_AMBIGUOUS": "session",
    # environment
    "PROJECT_PATH_NOT_FOUND": "environment",
    "PROJECT_PATH_REQUIRED": "environment",
    "PROJECT_PATH_INVALID": "environment",
    "FILE_ACCESS_DENIED": "environment",
    "PATH_NOT_FOUND": "environment",
    "NOT_A_DIRECTORY": "environment",
    "PYWIN32_MISSING": "environment",
    "ELECTRON_NOT_INSTALLED": "environment",
    "LAUNCHER_NOT_FOUND": "environment",
    "ELECTRON_LAUNCHER_NOT_FOUND": "environment",
    "ARTIFACT_WRITE_FAILED": "environment",
    "TARGET_FOLDER_INVALID": "environment",
    # R86 — [A] subprocess/probe can't access a UNC share the interactive user can
    # (per-session logon-token auth limitation, NOT server-down / not a code bug)
    "UNC_ACCESS_FROM_ORCHESTRATOR_PROCESS": "environment",
    "UNC_AUTH_CONTEXT_MISMATCH": "environment",
    # retryable_transport
    "SESSION_STORE_CONTENTION": "retryable_transport",
    "STALE_INDEX": "retryable_transport",
    "FILE_CHANGED_OUTSIDE_ORCHESTRATOR": "retryable_transport",
    "CDP_UNREACHABLE_AFTER_LAUNCH": "retryable_transport",
    "CDP_ATTACH_EXPIRED": "retryable_transport",
    "CDP_NAVIGATE_FAILED": "retryable_transport",
    "WEBFETCH_URL_ERROR": "retryable_transport",
    "WEBFETCH_HTTP_ERROR": "retryable_transport",
    # runtime — T4 / tool-body explicit (so map wins over heuristic)
    "PAGE_NOT_FOUND": "runtime",
    "SELECTOR_NOT_FOUND": "runtime",
    "DOM_READ_FAILED": "runtime",
    "FILL_FAILED": "runtime",
    "CLICK_FAILED": "runtime",
    "EVALUATE_JS_FAILED": "runtime",
    "CDP_ATTACH_FAILED": "runtime",
    "BROWSER_LAUNCH_FAILED": "runtime",
    "WEBFETCH_FAILED": "runtime",
    "WEBFETCH_JSON_PARSE_FAILED": "runtime",
    "TOOL_RESPONSE_NOT_JSON": "runtime",
    "TOOL_ERROR": "runtime",
    "INTERNAL_ERROR": "runtime",
    "DIALOG_EDIT_SETTEXT_FAILED": "runtime",
    "MODERN_PICKER_KEYBOARD_FAILED": "runtime",
    "NEW_DIALOG_NOT_FOUND": "runtime",
    "PATH_QUOTING_ERROR": "schema",
}


def _phase_for_error_name(name: str) -> str:
    """R49 — Map error_name → high-level phase taxonomy."""
    if not name:
        return "runtime"
    return _R49_PHASE_MAP.get(name, "runtime")


def _session_contention_fields(exc: BaseException):
    """A40: single source of truth for translating a transient session-store
    write contention into a retryable, machine-actionable error contract.

    ``SessionStoreContentionError`` is raised by ``SessionWriter.submit`` for
    EVERY durable write once the write-queue watchdog declares the writer
    STALLED or WEDGED (or the queue budget is exceeded). It is transient: a
    plain retry once the queue drains actually commits. But it reaches most
    tool handlers through a generic ``except Exception`` that funnels into
    ``_json_error(str(e))`` -- which drops the exception type and defaults to
    ``retryable=False`` / ``INTERNAL_ERROR``. That tells CA a retryable
    condition is a permanent internal error, so it gives up and the write is
    silently lost. Centralizing the translation here (instead of copy-pasting
    an ``except SessionStoreContentionError`` into each tool) is what keeps a
    fourth caller from being missed the way A40 itself was created.

    Returns the contract fields for a contention exception, or ``None`` for
    anything else so callers keep their default (non-retryable) verdict -- the
    translation must never widen to swallow a genuine defect.
    """
    if not isinstance(exc, _session_store_module.SessionStoreContentionError):
        return None
    return {
        "error_name": "SESSION_STORE_CONTENTION",
        "retryable": True,
        # Generic-but-accurate: the write did not commit and a short backoff +
        # retry is the correct move for ANY session-store-touching tool, not a
        # close_session-specific instruction.
        "ca_next_action": "retry_after_short_backoff",
        "hint": ("The session write queue was temporarily busy; the write did "
                 "not commit. Retry after a short backoff."),
    }


# W7 (2026-08-19): the manage_agent action enum, named once. It also appears in
# the tool schema (registry_data); keeping a literal list at the error site is how
# the two drifted apart in the first place.
_MANAGE_AGENT_ACTIONS = (
    "register", "unregister", "list", "get",
    "set_mode", "sync_rule", "sync_mcp", "sync_skill",
)


def _json_error(message, **extra) -> str:
    """Serialised form. The dict form below is the implementation; this is the
    wrapper every existing caller already uses, byte-for-byte unchanged."""
    return json.dumps(_json_error_dict(message, **extra), ensure_ascii=False)


def _json_error_dict(message, **extra) -> dict:
    # A40: when handed the original exception (``_json_error(e)`` rather than
    # ``_json_error(str(e))``), recognize a transient session-store contention
    # at the source and stamp the retryable contract before it collapses into
    # the generic INTERNAL_ERROR default below. Passing a string keeps the old
    # behavior verbatim, so this is a no-op for every existing non-exception
    # caller. Explicit fields supplied by the caller always win (setdefault).
    if isinstance(message, BaseException):
        _contention = _session_contention_fields(message)
        if _contention is not None:
            for _k, _v in _contention.items():
                extra.setdefault(_k, _v)
        message = str(message)
    msg = str(message or "unexpected internal error")
    data = {"ok": False, "error": msg}
    data.update(extra)
    data.setdefault("error_name", "INTERNAL_ERROR")
    data.setdefault("type", "INTERNAL_ERROR")
    data.setdefault("severity", "error")
    data.setdefault("retryable", False)
    data.setdefault("reason", msg)
    data.setdefault("ca_next_action", "inspect_error_context_or_report_backend_issue")
    # R49 — surface machine-readable phase taxonomy on every error response.
    data.setdefault("phase", _phase_for_error_name(data.get("error_name", "")))
    return data

_SECRET_FIELD_RE = re.compile(
    r"(token|secret|password|api[_-]?key|authorization|capability|owner_claim_id)",
    re.IGNORECASE,
)
_MOJIBAKE_MARKERS = ("Ã", "Ä", "áº", "á»", "â€™", "â€œ", "â€", "Â")
_VIETNAMESE_DIACRITIC_RE = re.compile(
    r"[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]",
    re.IGNORECASE,
)
_LATIN1_UTF8_MOJIBAKE_HINTS = ("Ã", "Â", "Æ", "Ä", "â", "áº", "á»")
_DRY_RUN_TTL_SECONDS = 30 * 60


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_content_encoding(content_encoding: str | None) -> str:
    value = (content_encoding or "plain").strip().lower()
    if value == "none":
        return "plain"
    return value


def _sanitize_audit_value(value, max_len: int = 500):
    """Remove likely secrets and trim audit values before persistence."""
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if _SECRET_FIELD_RE.search(str(key)):
                clean[str(key)] = "<redacted>"
            else:
                clean[str(key)] = _sanitize_audit_value(item, max_len=max_len)
        return clean
    if isinstance(value, list):
        return [_sanitize_audit_value(item, max_len=max_len) for item in value[:20]]
    text = "" if value is None else str(value)
    if _SECRET_FIELD_RE.search(text):
        return "<redacted>"
    return text[:max_len]


def _decode_text_payload(raw_content: str, content_encoding: str | None) -> tuple[str | None, dict, dict | None]:
    encoding = _normalize_content_encoding(content_encoding)
    accepted_payload_fields = [
        "project_path", "file_path", "path", "new_content", "content_encoding",
        "start_line", "end_line", "dry_run", "overwrite",
    ]
    if encoding not in {"plain", "base64"}:
        return None, {}, {
            "ok": False,
            "error": f"Unsupported content_encoding: {content_encoding}",
            "error_name": "INVALID_CONTENT_ENCODING",
            "type": "SCHEMA_VIOLATION",
            "severity": "error",
            "retryable": False,
            "reason": "unsupported content_encoding",
            "accepted_fields": accepted_payload_fields,
            "ca_next_action": "use_plain_or_base64_content_encoding",
            "write_performed": False,
        }
    try:
        if encoding == "base64":
            raw_bytes = base64.b64decode(raw_content, validate=True)
            text = raw_bytes.decode("utf-8", errors="strict")
        else:
            text = raw_content
            raw_bytes = text.encode("utf-8", errors="strict")
    except Exception as exc:
        if encoding == "base64":
            return None, {}, {
                "ok": False,
                "error": f"base64 decode failed: {exc}",
                "error_name": "INVALID_CONTENT_ENCODING",
                "type": "SCHEMA_VIOLATION",
                "severity": "error",
                "retryable": False,
                "reason": "new_content is not valid base64 UTF-8",
                "encoding_error_stage": "utf8_decode" if isinstance(exc, UnicodeDecodeError) else "base64_decode",
                "hint": (
                    "Generate base64 programmatically from the intended UTF-8 bytes; "
                    "do not compose or repair base64 by hand. Decode locally with strict UTF-8 "
                    "and compare to the intended text before retrying once. This error identifies "
                    "invalid received content, not which transport or caller changed it."
                ),
                "accepted_fields": accepted_payload_fields,
                "payload_example": {
                    "content_encoding": "base64",
                    "new_content": "aGVsbG8=",
                },
                "ca_next_action": "re_encode_utf8_content_to_base64_and_retry_once",
                "write_performed": False,
            }
        return None, {}, {
            "ok": False,
            "error": f"{encoding} decode failed: {exc}",
            "error_name": "INVALID_CONTENT_ENCODING",
            "type": "INVALID_REQUEST",
            "severity": "error",
            "retryable": False,
            "reason": "unsupported content payload encoding",
            "accepted_fields": accepted_payload_fields,
            "ca_next_action": "use_plain_or_base64_content_encoding",
            "write_performed": False,
        }
    if "\x00" in text:
        return None, {}, {
            "ok": False,
            "error": "Binary content unsupported by text write tool",
            "error_name": "BINARY_FILE_UNSUPPORTED",
            "type": "UNSUPPORTED_MEDIA_TYPE",
            "severity": "CRITICAL",
            "retryable": False,
            "ca_next_action": "stop_and_report",
            "write_performed": False,
        }
    check = _encoding_check_from_text(text, encoding)
    check["bytes_written"] = len(raw_bytes)
    return text, check, None

def _mojibake_confirmed(text: str) -> bool:
    """Confirm a marker hit is REAL mojibake, not text that merely mentions one.

    _MOJIBAKE_MARKERS is a substring probe, so any file that *discusses* mojibake
    matches itself — code_tools.py did, which made validate_encoding fail on this
    very module and taught CAs to ignore the gate. Real mojibake is UTF-8 bytes
    that were decoded as latin-1/CP-1252, so re-encoding to latin-1 and decoding
    as UTF-8 recovers clean text; prose that merely contains the marker glyphs
    either fails to round-trip or comes back unchanged. Verified against both:
    a corrupted Vietnamese string recovers exactly, all four marker-mentioning
    lines in this file do not.
    """
    if not text:
        return False
    try:
        recovered = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return False
    return recovered != text


def _encoding_check_from_text(text: str, encoding_used: str = "plain") -> dict:
    suspicious = [marker for marker in _MOJIBAKE_MARKERS if marker in text]
    # R81: markers alone are a hint, not a verdict — see _mojibake_confirmed.
    confirmed = bool(suspicious) and _mojibake_confirmed(text)
    has_vietnamese = bool(_VIETNAMESE_DIACRITIC_RE.search(text))
    return {
        "encoding_used": encoding_used,
        "utf8_valid": True,
        "mojibake_risk": confirmed,
        "mojibake_markers_present": bool(suspicious),
        "mojibake_confirmed": confirmed,
        "vietnamese_diacritic_check": {
            "has_vietnamese_diacritics": has_vietnamese,
            "status": "ok" if has_vietnamese or not text.strip() else "not_detected",
        },
        "suspicious_sequences": suspicious,
    }


def _encoding_check_from_file(path: Path, encoding_used: str = "utf-8") -> dict:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="strict")
        check = _encoding_check_from_text(text, encoding_used)
        check.update({
            "encoding_detected": "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8",
            "bom": raw.startswith(b"\xef\xbb\xbf"),
            "bytes": len(raw),
        })
        return check
    except UnicodeDecodeError as exc:
        return {
            "encoding_detected": "unknown",
            "utf8_valid": False,
            "bom": False,
            "mojibake_risk": True,
            "vietnamese_diacritic_check": {"has_vietnamese_diacritics": False, "status": "unknown"},
            "suspicious_sequences": [],
            "error": str(exc),
        }


def _unified_diff_preview(before: str, after: str, fromfile: str, tofile: str, limit: int = 120) -> dict:
    diff_lines = list(difflib.unified_diff(
        split_lines(before, keepends=True),
        split_lines(after, keepends=True),
        fromfile=fromfile,
        tofile=tofile,
        n=3,
    ))
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    truncated = len(diff_lines) > limit
    before_lines = split_lines(before)
    after_lines = split_lines(after)
    return {
        "changed_lines": added + removed,
        "added": added,
        "removed": removed,
        "preview_before": "\n".join(before_lines[:40]),
        "preview_after": "\n".join(after_lines[:40]),
        "diff_unified": "".join(diff_lines[:limit]),
        "truncated": truncated,
        "preview_hash": _sha256_text(before + "\n---after---\n" + after),
    }


def _dry_run_store_path(project_path: str, dry_run_id: str) -> Path:
    return _project_cache_dir(project_path) / "dry_runs" / f"{dry_run_id}.json"


def _store_dry_run(project_path: str, payload: dict) -> dict:
    dry_run_id = f"dry_{uuid.uuid4().hex}"
    expires_at = time.time() + _DRY_RUN_TTL_SECONDS
    payload = dict(payload)
    payload.update({
        "dry_run_id": dry_run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "expires_at": datetime.fromtimestamp(expires_at).isoformat(timespec="seconds"),
        "expires_at_epoch": expires_at,
    })
    path = _dry_run_store_path(project_path, dry_run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, payload, indent=2)
    return {
        "dry_run_id": dry_run_id,
        "payload_hash": payload["payload_hash"],
        "target_file_hash": payload.get("target_file_hash"),
        "expires_at": payload["expires_at"],
    }

SUPPORTED_TEST_PROFILES = ["unittest", "pytest", "py_compile_core", "py_compile", "npm_test",
                            # V15 R34: Rawcut-specific workflow profiles (each maps to a .workflow.json file)
                            "rawcut_test4_manual", "rawcut_testgf_sheet"]

# ─── OpenAI-format tool schemas (used by /api/tools endpoint) ─────────────────

# Registry schema literals live in orchestrator.core.registry_data.
TOOL_SCHEMAS = build_tool_schemas()

for schema in TOOL_SCHEMAS:
    fn = schema.get("function", {})
    params = fn.setdefault("parameters", {})
    props = params.setdefault("properties", {})
    name = fn.get("name")
    if name:
        fn["x_phase"] = tool_phase(name)
        fn["x_phase_label"] = PHASE_LABELS.get(tool_phase(name), PHASE_LABELS["T3"])
    if name == "find_symbol":
        props["project_path"] = {
            "type": "string",
            "default": "",
            "description": "Project root (preferred); path remains a legacy alias",
        }
        params["required"] = ["name"]
    if name == "query_index":
        props["query"] = {
            "type": "string",
            "default": "",
            "description": "Optional text filter against indexed relative paths",
        }
    # E-01: include_global_usage / include_http_log are intentionally NOT
    # injected into the get_usage_stats schema anymore (tombstoned — explicit
    # use fails loud with GLOBAL_SCOPE_NOT_ENABLED).
    if fn.get("name") == "read_file_range":
        props["project_path"] = {
            "type": "string",
            "description": "Project root for relative file_path",
        }
        props["max_lines"] = {
            "type": "integer",
            "default": MAX_READ_LINES,
            "description": "Maximum lines to return, capped by the backend.",
        }
        props["force_reseed"] = {
            "type": "boolean",
            "default": False,
            "description": "Force read-token reseed even when the file changes during read; audited.",
        }
        # A36: registry_data.BASE_TOOL_SCHEMAS owns the read_file_range description.
        # It is the FIRST tool a CA reaches for on an unfamiliar repo, so its text has to
        # carry the CSLt order (ask the cbm INDEX before a bulk read) and the read_token /
        # content_hash caveat BEFORE the first call. The one-line label that used to be
        # assigned here erased all of it. See _REGISTRY_DESCRIPTION_WINS below.
    if name == "search_code":
        from orchestrator import search_regex_worker as _search_regex_worker

        props["regex_deadline_seconds"] = {
            "type": "number",
            "default": _search_regex_worker.DEFAULT_DEADLINE_SECONDS,
            "minimum": _search_regex_worker.MIN_DEADLINE_SECONDS,
            "maximum": _search_regex_worker.MAX_DEADLINE_SECONDS,
            "description": "Hard end-to-end deadline for isolated regex preflight, snapshot staging, and matching.",
        }
        props["max_context_lines"] = {
            "type": "integer",
            "default": MAX_SEARCH_CONTEXT_LINES,
            "description": "Context lines before and after each match, capped by the backend.",
        }
        props["max_results"] = {
            "type": "integer",
            "default": MAX_SEARCH_RESULTS,
            "description": "Maximum matches to return, capped by the backend.",
        }
        # A36: description deliberately NOT set here. registry_data owns the search_code
        # text because it carries the mode enum, the cbm routing map and the measured
        # traps (*.txt/*.md = 0 hits is a measurement trap; mode=callers DOES see
        # instance receivers). The old one-liner said only "bounded max_results", which a
        # CA can already read off these very properties.
    if name == "write_fix_html":
        fn["description"] = "Edit a range inside an .html file with base64, encoding_check, and diff_preview; performs delimiter safety checks before writing."
    if name == "write_new_file":
        fn["description"] = "Create a new file. Supports dry_run preview, base64 UTF-8 strict, diff_preview, and apply_after_dry_run."
    if name == "run_project_command":
        fn["description"] = "Run a bounded project command. V15 R14: shell_mode=auto|cmd|powershell|exec|posix; command_encoding=plain|base64; keep_alive=true detaches GUI/long-running processes; optional expected_port/expected_process_name verify GUI/debug launch and return spawned_pid, launch_verified, port_probe, process_probe. JSON-encode backslashes before placing command in an outer JSON string. On Windows prefer direct search/read tools or script-file transport for fragile shell operators and findstr/dir reads. Auto routes only a standalone script.ps1 to PowerShell -File; findstr text script.ps1 remains a normal command. Mutating commands require X-Orchestrator-Token."
    if name == "run_test_profile":
        props["profile"]["enum"] = SUPPORTED_TEST_PROFILES
        fn["description"] = "Run a known test profile. If invalid, the error returns available profiles."
    if name == "create_benchmark_request":
        fn["description"] = "Create BENCHMARK_REQUEST.md from idea only. Does not accept description."
    if name == "create_build_context":
        fn["description"] = "Create BUILD_CONTEXT.md from idea and approved_inputs only. Does not accept name."
    if name == "get_folder_size":
        fn["description"] = "Count files and bytes under folder_path without blocking the API loop. Safe defaults exclude generated/cache trees; pass include_skipped=true for a full scan."
    if name == "log_native":
        fn["description"] = "End-of-session native fallback log. Use native=[], not entries."
    if name == "get_session_events":
        fn["description"] = "Read session events. Prefer session_id; project_path only resolves the active session."

_ENGLISH_TOOL_DESCRIPTIONS = {
    "list_structure": "Return a project folder tree without reading file contents. Prefer project_path; path remains a legacy alias. R43.1: compact=true (or verbosity='terse') hard-caps depth=2 and summarizes folders with >30 files.",
    "find_symbol": "Find function, class, or variable definitions and return file:line targets for follow-up reads.",
    "read_file_range": "Adaptive pure read for a range or structural scope with bounded output and ranked next_ranges.",
    "read_context_batch": "Read multiple file ranges or structural scopes under one deterministic token budget.",
    "architecture_context_pack": "Build a graph-first readonly context pack without auto-reindexing.",
    "search_code": "Search the codebase with bounded max_results and max_context_lines. Prefer project_path; path remains a legacy alias.",
    "get_file_symbols": "List all functions, classes, and variables defined in one file.",
    "write_fix": "Replace a line range in one file, create a .bak backup, and sync the cache after writing.",
    "write_new_file": "Create a new file. Supports dry_run preview, base64 UTF-8 strict, diff_preview, and apply_after_dry_run.",
    "generate_project_map": "Generate the project cache, INDEX.json, and PROJECT_MAP.md while skipping junk, secret, and generated folders.",
    "refresh_project_map": "Delete the old cache and rescan the project to rebuild INDEX.json. Large repos can take 60-180s+ -- set call timeout >= 300s; the rescan completes server-side even if the client times out.",
    "update_file_cache": "Update cached metadata for one changed file and sync INDEX.json.",
    "cache_status": "Check project cache status and the latest INDEX.json update time.",
    "delete_file": "Delete one file at the requested path. This is permanent.",
    "get_rule_core_text": "Return rendered CORE_RULE text + runtime metadata. HTTP bootstrap; pass stable agent_id.",
    "audit_tool_matrix": "V15 R13 — Cross-check TOOL_MAP/schemas/skills/router/tool surface reference; reports drift + writes matrix_log.jsonl. Auto-invoked on session open/prepare.",
    "prepare_ca_session": "Create a one-paste CA bootstrap prompt for T1/T2 workflows. V15 R13: minimal dedup-safe session prompt.",
    "set_workflow_mode": ("Set workflow mode in WORKFLOW_STATE.json AND on the active session row "
                          "(sessions.mode) — read session_mode.applied in the response."),
    "workflow_status_registry": "R43.2 C2: T1 registry inspector — cache/drift/mode/backup state from .orchestrator_cache. Renamed to disambiguate from MD workflow runner.",
    "update_workflow_state": "Update WORKFLOW_STATE.json.",
    "next_recommended_action": "Return the next deterministic workflow action.",
    "capability_status": "Report parser/indexing capabilities and missing optional libraries.",
    "suggest_missing_capabilities": "Suggest optional parser libraries without installing them.",
    "backup_status": "Read Orchestrator backup manifest status.",
    "resolve_project_path": "Resolve any Windows file/folder path into project_path plus file_path/folder_path metadata.",
    "protected_tools_status": "Return mutation-token protection status without exposing the raw token.",
    "path_quote_helper": "Generate a Windows-safe run_project_command wrapper for subfolders or paths with spaces. Does not execute.",
    "optional_tool_probe": "Check optional helper tools or endpoints without mutating state or fetching raw tokens.",
    "backup_project": "Create a timestamped zip backup and backup manifest.",
    "cache_delta": "Compare the filesystem against INDEX for added, modified, and deleted files.",
    "run_project_command": "Run a bounded project command. JSON-encode backslashes before transport; on Windows prefer direct search/read tools for file reads. Only a standalone .ps1 path uses PowerShell -File; a command such as findstr text script.ps1 remains a normal command. Supports keep_alive for GUI/long-running processes. Mutating commands require X-Orchestrator-Token.",
    "run_test_profile": "Run a known bounded test profile. Invalid profiles return the available profile list.",
    "create_benchmark_request": "Create BENCHMARK_REQUEST.md from idea only. Does not accept description.",
    "create_build_context": "Create BUILD_CONTEXT.md from idea and approved_inputs only. Does not accept name.",
    "get_project_summary": "Return a quick INDEX overview: groups, hotspots, and entry points.",
    "query_index": "Filter indexed files by group, complexity, LOC, fnmatch pattern, and optional text query.",
    "get_usage_stats": "Read APP_SESSION_LOG.jsonl, SQLite session events, and optional HTTP log stats.",
    "get_folder_size": "Count files and bytes under folder_path without blocking the API loop. Safe defaults exclude generated/cache trees; pass include_skipped=true for a full scan.",
    "validate_file": "Check syntax/validity after a write. Python uses py_compile; JS/TS use node --check.",
    "validate_symbols": "Post-write integration check: syntax plus function/class drift against INDEX.",
    "validate_encoding": "Validate UTF-8, BOM, mojibake risk, and Vietnamese diacritic integrity after writes.",
    "post_write_batch_verify": "Run the standard non-mutating post-write verification bundle for changed files.",
    "write_fix_html": "Edit a range inside an .html file with base64, encoding_check, and diff_preview; performs delimiter safety checks before writing.",
    "apply_after_dry_run": "Apply an exact previously previewed dry-run payload by dry_run_id; rejects stale target files and expired previews.",
    "open_session": "Open or resume one active session per project and agent. Resume preserves title and mode; use rename_session for a new title. Opt-in apply_requested_title renames in place; force_new_session closes then creates atomically and refuses pending writes, drafts or audits. Both default false, require agent_id, and are mutually exclusive.",
    "close_session": "Close the active CA session after log_native(native=[...]) has been recorded.",
    "rename_session": "Rename the active session. Closed sessions cannot be renamed.",
    "clear_closed_sessions": "Delete all closed sessions and their events for a project; active session is preserved.",
    "log_native": "End-of-session native fallback log. Use native=[], not entries.",
    "log_native_event": "Log one native fallback action immediately after using a native tool.",
    "create_pending_audit": "Store a pending native fallback audit entry for later sync.",
    "sync_pending_audit": "Sync pending native fallback audit entries into the active session log.",
    "list_sessions": "List recent sessions for a project without events.",
    "get_session_events": "Read paginated session events. Prefer session_id; project_path resolves only the active session.",
}

_ENGLISH_PROPERTY_DESCRIPTIONS = {
    "path": "Path or legacy project_path alias.",
    "project_path": "Absolute project root path.",
    "depth": "Maximum folder-tree depth.",
    "save_output": "Write structure_output.txt when true.",
    "name": "Function, class, variable, or symbol name.",
    "context_lines": "Number of context lines around each match.",
    "file_path": "File path, absolute or relative to project_path.",
    "start_line": "1-based start line.",
    "end_line": "Inclusive end line.",
    "query": "Regex or text query.",
    "file_pattern": "fnmatch pattern for basename or relative path.",
    "new_content": "Replacement content.",
    "dry_run": "Preview/validate without writing when supported.",
    "content_encoding": "'none' or 'base64'.",
    "content": "File content.",
    "script_path": "Python script path.",
    "timeout": "Timeout in seconds.",
    "force": "Force the operation when supported.",
    "keep_last_n": "Maximum number of backup zip files to keep.",
    "backup_dir": "Optional backup directory.",
    "input_path": "User-provided file or folder path.",
    "preferred_root": "Optional project root to prefer when it contains input_path.",
    "subfolder": "Relative subfolder under project_path.",
    "shell": "Wrapper shell, default powershell.",
    "command": "Command to run.",
    "keep_alive": "Spawn GUI or long-running process in background and do not auto-kill it.",
    "profile": "Known test profile.",
    "idea": "Short task description.",
    "approved_inputs": "User-approved build inputs.",
    "folder_path": "Absolute folder path.",
    "lang": ("auto | python | js | ts | esbuild | html | vue | json | css. "
             "auto picks by extension and passes unknown extensions with "
             "checked:false. Common aliases (javascript, node, typescript, py, "
             "jsonc, htm, scss) are normalised; anything else is refused with "
             "UNSUPPORTED_LANG rather than silently unchecked."),
    "session_title": "Optional session title.",
    "mode": "Workflow mode such as T1 or T2.",
    "new_title": "New session title.",
    "native": "Array of native fallback entries; pass [] when none were used.",
    "action_type": "read | write | execute | validate | cache | backup | workflow.",
    "tool_name": "Tool or native command name.",
    "target": "File, command, symbol, or scope affected.",
    "duration_ms": "Elapsed time in milliseconds.",
    "status": "ok | error | warn.",
    "reason": "Optional explanation.",
    "instead_of": "Orchestrator tool that the native fallback replaced.",
    "failed_payload": "Payload that failed before native fallback.",
    "failed_error": "Error that caused native fallback.",
    "native_command": "Native command or action used.",
    "scope": "Scope of the native fallback.",
    "risk": "Risk notes for the native fallback.",
    "tools": "Tool names or endpoint paths to probe.",
    "changed_files": "Changed files to verify, relative to project_path or absolute inside it.",
    "run_validate_symbols": "Run validate_symbols when supported.",
    "run_cache_delta": "Run cache_delta once for the whole batch.",
    "run_validate_encoding": "Run validate_encoding for text files.",
    "pending_audit_id": "Pending audit identifier returned by create_pending_audit.",
    "expected_encoding": "Expected encoding label, usually utf-8.",
    "language_hint": "Optional language hint for diagnostics.",
    "overwrite": "Allow replacing an existing file when true.",
    "confirm_hash": "Optional payload_hash from the dry-run response.",
    "confirm_warnings": "Set true only after reviewing dry-run warnings.",
    "dry_run_id": "Identifier returned by a successful dry-run response.",
    "session_id": "Session identifier from list_sessions.",
    "limit": "Maximum number of rows/events.",
    "offset": "Pagination offset.",
    "group": "source | doc | test | config | script.",
    "min_complexity": "Minimum complexity filter.",
    "min_loc": "Minimum line-count filter.",
    "max_results": "Maximum number of results.",
    "max_lines": "Maximum lines to return, capped by the backend.",
    "max_context_lines": "Context lines before and after each search match.",
    "last_n": "Maximum recent entries to read.",
    # E-01: include_http_log / include_global_usage descriptions removed —
    # both params are tombstoned and no longer advertised in the schema.
}

# A36: the four tools a CA reaches for FIRST on an unfamiliar repo keep their
# registry_data description. Measured 2026-09-19: the daemon served a 94-char
# read_file_range description while registry_data already held the long one, so every
# word of CA-facing routing guidance written there was unreachable text. The generic
# one-line English label erases exactly the part that has to arrive BEFORE the first
# call - ask the cbm INDEX before a bulk read, write_fix is FLAT and BOTTOM-UP,
# mode=callers DOES see instance receivers - while adding nothing a CA cannot already
# read off the parameters. Same reasoning as the read-range property carve-out below.
_REGISTRY_DESCRIPTION_WINS = frozenset({
    "read_file_range",
    "read_context_batch",
    "search_code",
    "write_fix",
})

for schema in TOOL_SCHEMAS:
    fn = schema.get("function", {})
    name = fn.get("name", "")
    if name in _ENGLISH_TOOL_DESCRIPTIONS and name not in _REGISTRY_DESCRIPTION_WINS:
        fn["description"] = _ENGLISH_TOOL_DESCRIPTIONS[name]
    props = fn.get("parameters", {}).get("properties", {})
    for prop_name, prop_schema in props.items():
        # A37 root fix: FILL-ONLY, never overwrite. This map holds generic English LABELS
        # keyed by BARE PARAM NAME -- and a bare name cannot describe a param whose meaning
        # is per-tool: `mode` is T1/T2 for open_session but text/regex/symbol/callers for
        # search_code, and ONE label was being published for both. Measured before this fix:
        # the overwrite erased real per-tool text on 85 properties, including write_fix's
        # 309-char content_encoding='base64' guidance (down to 19), which is exactly why a CA
        # kept hitting SYNTAX_ERROR_PRE_WRITE on new_content containing quotes. registry_data
        # is the SOT for what a param MEANS; this map only fills a param that carries no
        # description at all. That also retires the read_file_range start_line/end_line
        # special case: under fill-only every tool's own contract survives, so no tool needs
        # an exception. Guard kept: nothing here may introduce non-English copy, and the 85
        # recovered descriptions were measured diacritic-free.
        if not isinstance(prop_schema, dict):
            continue
        if prop_name in _ENGLISH_PROPERTY_DESCRIPTIONS and not str(
                prop_schema.get("description") or "").strip():
            prop_schema["description"] = _ENGLISH_PROPERTY_DESCRIPTIONS[prop_name]

# Strip orphan schemas declared for internal helpers but not exposed in TOOL_MAP.
# capability_status + suggest_missing_capabilities are public T1 tools because
# TOOL_REFERENCE.md / AGENT_BOOTSTRAP_PROMPT.md document them as callable.
# R43.2 C3: _HIDDEN_ORPHAN_SCHEMAS filter REMOVED. The 4 orphan schemas
# (execute_script, create_session_bootstrap, update_workflow_state, update_file_cache)
# are no longer declared upstream → no filter needed. Those functions remain internal-only.

# ─── V15 R8 schemas (Round A merged tools + Round B new tools) ─────────────
# V15 R8 merged schemas and phase entries live in orchestrator.core.registry_data.

# ─── Tool dispatch map (populated at bottom of file) ─────────────────────────

TOOL_MAP: dict = {}  # filled after function definitions

# ─── Tool 1-5 shims ───────────────────────────────────────────────────────────
# F401 FALSE POSITIVE on this whole block: registry_data.BASE_TOOL_MAP_SPECS names these
# handlers as STRING exprs that build_tool_map() eval()s against code_tools globals() at
# the bottom of this file, so ruff sees no reference. `ruff --fix` deleting them makes the
# daemon die at import with NameError -- that is why every line carries a noqa.
from orchestrator.tools.read import (
    architecture_context_pack,  # noqa: F401
    get_file_symbols,  # noqa: F401
    list_structure,  # noqa: F401
    read_context_batch,  # noqa: F401
    read_file_range,  # noqa: F401
)
from orchestrator.tools.search import find_symbol, search_code  # noqa: F401

# ─── Tool write_fix_html ──────────────────────────────────────────────────────

async def write_fix_html(file_path: str, start_line: int, end_line: int, new_content: str,
                          project_path: str = "", content_encoding: str = "none",
                          force: bool = False) -> str:
    """[DEPRECATED V15 R8] Use write_fix for ALL extensions. write_fix_html remains as
    backward-compatible alias that:
    1. For .html → keeps the historical delimiter safety check (</script>/</style>/</body>/</html>).
    2. For non-.html → silently routes to write_fix (no more UNSUPPORTED_FILE_TYPE error).

    Prefer write_fix going forward; the delimiter check fires automatically when the
    suffix is .html.
    """
    if isinstance(force, str):
        force = force.strip().lower() in {"1", "true", "yes", "y", "on"}
    fp = _resolve_project_file(project_path, file_path)
    if not fp.exists():
        return json.dumps({"ok": False, "error": f"File not found: {file_path}"})
    # V15 R8 A.7: route non-html to write_fix instead of rejecting — write_fix already
    # handles css/js/ts/json/md/txt/py uniformly (no ext gate).
    if fp.suffix.lower() != ".html":
        return await write_fix(
            file_path, start_line, end_line, new_content,
            project_path=project_path, dry_run=False, content_encoding=content_encoding,
        )

    # Step 1: internal dry_run
    dry = json.loads(await write_fix(
        file_path, start_line, end_line, new_content,
        project_path, dry_run=True, content_encoding=content_encoding
    ))
    if not dry.get("ok"):
        return json.dumps(dry)

    # Step 2: closing delimiter safety check
    _DELIMITERS = ["</script>", "</style>", "</body>", "</html>"]
    removed_lines = [ln.strip() for ln in dry.get("preview", {}).get("lines_removed", [])]
    added_lines   = [ln.strip() for ln in dry.get("preview", {}).get("lines_added", [])]
    removed_text  = "\n".join(removed_lines)
    added_text    = "\n".join(added_lines)

    missing = []
    for d in _DELIMITERS:
        if d.lower() in removed_text.lower() and d.lower() not in added_text.lower():
            missing.append(d)

    # Brace balance check
    brace_lost = (removed_text.count("{") - removed_text.count("}")) - \
                 (added_text.count("{") - added_text.count("}"))
    if brace_lost > 0:
        missing.append(f"{{brace}} net lost: {brace_lost}")

    if missing and not force:
        return json.dumps({
            "ok": False,
            "delimiter_warning": f"Closing delimiter(s) sẽ bị mất: {missing}",
            "action": "Kiểm tra lại new_content hoặc truyền force=True nếu cố ý xóa",
            "dry_run_preview": dry.get("preview"),
            "diff": dry.get("diff", ""),
        }, ensure_ascii=False)

    # Step 3: apply real write
    result = json.loads(await write_fix(
        file_path, start_line, end_line, new_content,
        project_path, dry_run=False, content_encoding=content_encoding
    ))
    result["html_tool"] = True
    result["delimiter_check"] = "passed" if not missing else "force_bypassed"
    if dry.get("diff"):
        result.setdefault("diff", dry["diff"])
    if dry.get("diff_preview"):
        result.setdefault("diff_preview", dry["diff_preview"])
        result.setdefault("changed_lines", dry["diff_preview"].get("changed_lines"))
    if dry.get("encoding_check"):
        result.setdefault("encoding_check", dry["encoding_check"])
    return json.dumps(result, ensure_ascii=False)


# F401 FALSE POSITIVE on every name here except write_fix: BASE_TOOL_MAP_SPECS names
# them as string exprs that build_tool_map() eval()s against code_tools globals().
from orchestrator.tools.write import (
    apply_after_dry_run,  # noqa: F401
    delete_file,  # noqa: F401
    write_draft_abort,  # noqa: F401
    write_draft_patch,  # noqa: F401
    write_draft_status,  # noqa: F401
    write_fix,
    write_new_file,  # noqa: F401
)

# ─── V15 R58: Python executable resolver ──────────────────────────────────────
# Decouples script Python from orchestrator's own venv. Orchestrator may run on
# venv312 (Py 3.12) while user scripts need Py 3.10 with specific packages.
# Resolution order: explicit param > ORCHESTRATOR_PYTHON_EXE env > sys.executable.
# D-ORPHAN (2026-08-11): the legacy _resolve_python_exe helper was removed
# alongside its only caller execute_script; use _python_interpreter_candidates
# / _select_python_interpreter instead.


def _python_interpreter_candidates(project_root: Path | None = None,
                                   explicit: str = "") -> list[tuple[str, str]]:
    import os as _os
    import shutil as _shutil
    import sys as _sys
    candidates = [("explicit", explicit), ("env", _os.environ.get("ORCHESTRATOR_PYTHON_EXE", ""))]
    if project_root is not None:
        suffix = "Scripts/python.exe" if _os.name == "nt" else "bin/python"
        candidates.extend((name, str(project_root / name / suffix)) for name in ("venv312", ".venv", "venv"))
    candidates.extend((("sys", _sys.executable),
                       ("path", _shutil.which("python") or _shutil.which("python3") or "")))
    seen, result = set(), []
    for source, candidate in candidates:
        if candidate:
            key = _os.path.normcase(_os.path.abspath(candidate))
            if key not in seen:
                seen.add(key)
                result.append((source, candidate))
    return result


def _select_python_interpreter(project_root: Path | None = None, explicit: str = "",
                               required_modules: list[str] | None = None) -> dict:
    import subprocess as _subprocess
    modules = sorted({str(item).strip() for item in (required_modules or []) if str(item).strip()})
    probe = ("import importlib.util,sys;" + f"m={modules!r};" +
             "sys.exit(3 if any(importlib.util.find_spec(x) is None for x in m) else 0)")
    attempted = []
    for source, candidate in _python_interpreter_candidates(project_root, explicit):
        try:
            capable = _subprocess.run([candidate, "-c", probe], capture_output=True,
                                      timeout=8, check=False).returncode == 0
        except (OSError, _subprocess.SubprocessError):
            capable = False
        attempted.append({"source": source, "executable": candidate, "capable": capable})
        if capable:
            return {"ok": True, "executable": candidate, "source": source,
                    "required_modules": modules, "attempted": attempted}
    return {"ok": False, "error_name": "INTERPRETER_CAPABILITY_MISSING",
            "error": "No available Python interpreter provides the required capabilities.",
            "required_python_modules": modules, "attempted": attempted, "retryable": False,
            "ca_next_action": "provide_a_capable_python_exe_or_adjust_required_python_modules"}


def _truncate_stream(value: str, max_chars: int = 0) -> tuple[str, dict]:
    import hashlib as _hashlib
    original = value or ""
    returned, omitted = cap_head_tail(original, max_chars)
    return returned, {"original_chars": len(original), "returned_chars": len(returned),
                      "truncated": bool(omitted),
                      "sha256": _hashlib.sha256(original.encode("utf-8", errors="replace")).hexdigest()}


# ─── Tool 9: execute_script (RETIRED 2026-08-11) ─────────────────────────────
# Orphaned helper: never registered in TOOL_MAP / TOOL_SCHEMAS, no callers
# outside its own definition. Removed — callers needing "run one Python script"
# should use run_project_command (T2, governed) or run_project_test_script.
# ─── Tool 10: generate_project_map ───────────────────────────────────────────────────

# V15 R8 C.4: track recent INDEX refreshes per project to suppress noisy drift block
# in find_symbol/search_code/query_index responses when index was just refreshed.
_RECENT_REFRESH_TS: dict[str, float] = {}
_DRIFT_SUPPRESS_WINDOW_SEC = 30.0  # within N seconds of refresh, drift hint omitted


def _mark_index_refreshed(project_path: str) -> None:
    try:
        key = str(Path(project_path).resolve()) if project_path else ""
    except Exception:
        key = project_path or ""
    if key:
        _RECENT_REFRESH_TS[key] = time.time()


def _is_index_recently_refreshed(project_path: str) -> bool:
    try:
        key = str(Path(project_path).resolve()) if project_path else ""
    except Exception:
        key = project_path or ""
    if not key:
        return False
    last = _RECENT_REFRESH_TS.get(key)
    return bool(last and (time.time() - last) < _DRIFT_SUPPRESS_WINDOW_SEC)


# ─── V15 R80: project-map staleness gate ─────────────────────────────────────
# generate/refresh_project_map is O(repo) (60-180s+, up to ~10 min on very large
# roots) and serialises on surgical_mapper._pmap_lock. With N CAs opening sessions
# at once the SAME full rebuild is requested over and over — a thundering herd
# that has already produced FILE_ACCESS_DENIED under stress (see the _pmap_lock
# docstring). Knowing whether a rebuild is NEEDED costs ~2s (cache_delta is
# hash-based and singleflight-coalesced per project); rebuilding blindly costs
# 70-350x that. So: probe first, rebuild only on real drift. force=True always
# rebuilds, which is the pre-R80 behaviour and the corrupt-cache escape hatch.
_PROJECT_MAP_ARTIFACTS_NOTE = "INDEX.json + PROJECT_MAP.md + PROJECT_CONTEXT.md + search snapshot"


def _project_map_artifacts_missing(project_path: str) -> list[str]:
    """Stat-only check that every generate_project_map output is present.

    A zero-drift INDEX alone is NOT enough to skip: generate_project_map also
    writes PROJECT_MAP.md and PROJECT_CONTEXT.md and publishes a search
    snapshot. Skipping while one of those is missing would leave the repair
    prepare_ca_session performs today permanently undone.
    """
    missing: list[str] = []
    try:
        root = Path(project_path).resolve()
        cache_dir = _project_cache_dir(str(root))
    except Exception as exc:
        return [f"unresolvable_project_path:{exc}"]
    if not (cache_dir / "INDEX.json").exists():
        missing.append("INDEX.json")
    if not (root / "PROJECT_MAP.md").exists():
        missing.append("PROJECT_MAP.md")
    if not (root / "PROJECT_CONTEXT.md").exists():
        missing.append("PROJECT_CONTEXT.md")
    try:
        if not search_snapshots.current_snapshot_id(root):
            missing.append("search_snapshot")
    except Exception:
        missing.append("search_snapshot")
    return missing


def _orphan_cache_sidecars(project_path: str) -> list[str]:
    """Per-file cache sidecars in .orchestrator_cache with no source file left.

    A rebuild wipes the non-preserved cache tree, so these vanish on any real
    generate_project_map. The staleness gate has to look for them explicitly:
    cache_delta walks SOURCE files and reports zero drift while stale sidecars
    still shadow modules deleted outside the write pipeline. Stat-only.
    """
    try:
        root = Path(project_path).resolve()
        cache_dir = _project_cache_dir(str(root))
    except Exception:
        return []
    if not cache_dir.is_dir():
        return []
    orphans: list[str] = []
    try:
        for sidecar in cache_dir.rglob("*.json"):
            rel = sidecar.relative_to(cache_dir).as_posix()
            if surgical_mapper._cache_path_is_preserved(rel):
                continue
            source_rel = rel[: -len(".json")]
            # _cache_path names sidecars "<source-rel-with-extension>.json", so a
            # stripped name with no extension left is cache infrastructure
            # (INDEX.json and friends), not a per-file sidecar.
            if "." not in source_rel.rsplit("/", 1)[-1]:
                continue
            if not (root / source_rel).exists():
                orphans.append(rel)
    except Exception:
        return orphans
    return orphans


async def _project_map_freshness(project_path: str) -> dict:
    """Decide whether the full rebuild can be skipped. Never raises."""
    missing = _project_map_artifacts_missing(project_path)
    if missing:
        return {"fresh": False, "reason": "artifacts_missing",
                "missing_artifacts": missing, "total_changed": None}
    try:
        delta = json.loads(await cache_delta(project_path))
    except Exception as exc:
        # A probe failure must never block the rebuild it exists to avoid.
        return {"fresh": False, "reason": "delta_probe_failed",
                "probe_error": str(exc), "total_changed": None}
    if not delta.get("ok"):
        return {"fresh": False, "reason": "delta_probe_failed",
                "probe_error": delta.get("error") or "cache_delta ok=false",
                "total_changed": None}
    total = int(delta.get("total_changed") or 0)
    if total == 0:
        # cache_delta only compares SOURCE files against the index; it is blind to
        # junk INSIDE .orchestrator_cache. A full rebuild also wipes orphan
        # sidecars (a *.json left behind by a file deleted outside the write
        # pipeline), so zero source-drift alone must not authorise a skip.
        orphans = _orphan_cache_sidecars(project_path)
        if orphans:
            return {"fresh": False, "reason": "orphan_cache_sidecars",
                    "orphan_sidecars": orphans[:20],
                    "orphan_sidecars_total": len(orphans),
                    "total_changed": 0}
    return {
        "fresh": total == 0,
        "reason": "cache_fresh" if total == 0 else "drift_detected",
        "total_changed": total,
        "added": len(delta.get("added") or []),
        "modified": len(delta.get("modified") or []),
        "deleted": len(delta.get("deleted") or []),
        "cache_updated_at": delta.get("cache_updated_at") or "",
    }


def _project_map_skipped_payload(project_path: str, freshness: dict) -> dict:
    """Same shape as summarize_index, so a skip is a drop-in for a rebuild."""
    try:
        summary = surgical_mapper.summarize_index(_load_index(project_path))
    except Exception:
        summary = {}
    summary.update({
        "ok": True,
        "skipped": True,
        "rebuilt": False,
        "reason": "cache_fresh",
        "total_changed": 0,
        "cache_updated_at": freshness.get("cache_updated_at", ""),
        "checked": _PROJECT_MAP_ARTIFACTS_NOTE,
        "hint": "Index matches disk (hash-compared) and every artifact exists; "
                "no rebuild needed. Pass force=true to rebuild anyway.",
    })
    return summary


async def _project_map_build(project_path: str, force: bool, builder,
                             _operation_id: str = "", _cancel_event=None) -> str:
    if not force:
        freshness = await _project_map_freshness(project_path)
        if freshness.get("fresh"):
            _mark_index_refreshed(project_path)
            return json.dumps(_project_map_skipped_payload(project_path, freshness),
                              ensure_ascii=False)
    try:
        from orchestrator.progress_watchdog import current_watchdog
        watchdog = current_watchdog()
        result = await asyncio.to_thread(builder, project_path, watchdog=watchdog)
        _mark_index_refreshed(project_path)
        if isinstance(result, dict):
            result = {"ok": True, "skipped": False, "rebuilt": True, **result}
        return json.dumps(result, ensure_ascii=False)
    except search_snapshots.SnapshotError as exc:
        return json.dumps({
            "ok": False,
            "error": str(exc),
            "error_name": "SNAPSHOT_PUBLISH_FAILED",
            "ca_next_action": "inspect_snapshot_integrity",
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


async def generate_project_map(project_path: str, force: bool = False,
                               _operation_id: str = "", _cancel_event=None) -> str:
    return await _project_map_build(project_path, force, surgical_mapper.generate_project_map,
                                    _operation_id, _cancel_event)


# ─── Tool 11: refresh_project_map ───────────────────────────────────────────────────

async def refresh_project_map(project_path: str, force: bool = False,
                              _operation_id: str = "", _cancel_event=None) -> str:
    return await _project_map_build(project_path, force, surgical_mapper.refresh_project_map,
                                    _operation_id, _cancel_event)


# ─── Tool 12: update_file_cache ───────────────────────────────────────────────────

async def update_file_cache(file_path: str, project_path: str) -> str:
    try:
        result = await asyncio.to_thread(surgical_mapper.update_file_cache, file_path, project_path)
        # Compact output — agent chỉ cần confirmation, không cần full symbol dump (~500 dòng)
        compact = {
            "ok": True,
            "file": result.get("file", file_path),
            "loc": result.get("loc"),
            "symbols_count": result.get("symbols_count"),
            "complexity_score": result.get("complexity_score"),
            "hash": result.get("hash"),
            "snapshot_id": result.get("snapshot_id"),
            "skipped": result.get("skipped", False),
        }
        return json.dumps(compact, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

# ─── Tool 13: cache_status ───────────────────────────────────────────────────────

async def cache_status(project_path: str) -> str:
    try:
        result = surgical_mapper.get_cache_status(project_path)
        last_updated = result.get("last_updated_iso") or result.get("generated_at")
        if last_updated:
            try:
                age_hours = (datetime.now() - datetime.fromisoformat(str(last_updated))).total_seconds() / 3600
                result["cache_age_hours"] = round(age_hours, 2)
                if age_hours > 6:
                    result["stale_warning"] = True
                    result["ca_next_action"] = "refresh_project_map"
            except Exception:
                pass
        # Authoritative drift check via cache_delta (full hash compare).
        # Skip if no index exists yet — nothing to compare against.
        if result.get("index_exists"):
            try:
                delta_raw = await cache_delta(project_path)
                delta = json.loads(delta_raw)
                added    = delta.get("added", []) or []
                modified = delta.get("modified", []) or []
                deleted  = delta.get("deleted", []) or []
                if added or modified or deleted:
                    result["status"] = "stale"
                    result["drift_warning"] = True
                    result["drift_action"] = "refresh_project_map"
                    result["drift"] = {
                        "added":    added[:20],
                        "modified": modified[:20],
                        "deleted":  deleted[:20],
                        "added_total":    len(added),
                        "modified_total": len(modified),
                        "deleted_total":  len(deleted),
                    }
            except Exception as exc:
                result["drift_check_error"] = str(exc)
        # B024: answer `ok` on BOTH paths. B018 fixed this same defect in
        # search_code and recorded that every other tool answered `ok`.
        # B031 (2026-09-21): that survey was WRONG, twice over -- cache_status
        # (this site) was missing from it, and so were find_symbol's live-scanner
        # success path and its path-not-found branch, both still ok-less a year
        # later. A one-off survey cannot hold this class shut, so B031 replaces the
        # claim with an enforced gate: tests/test_ok_envelope_gate_b031.py.
        # `ok` means "the check ran",
        # NOT "the cache is fresh": staleness stays in status/stale_warning/
        # drift_warning, or a stale cache would be indistinguishable from a
        # broken check to `if not resp.get("ok")`.
        # NOT "the cache is fresh": staleness stays in status/stale_warning/
        # drift_warning, or a stale cache would be indistinguishable from a
        # broken check to `if not resp.get("ok")`.
        result.setdefault("ok", True)
        return json.dumps(result)
    except Exception as exc:
        # B024: a failure must say it failed and name itself; this branch
        # used to answer a bare {"error": ...} with no ok and no error_name,
        # which no documented caller guard can read.
        return json.dumps({
            "ok": False,
            "error": str(exc),
            "error_name": "CACHE_STATUS_FAILED",
            "ca_next_action": "refresh_project_map",
        })


def _dedup_session_vs_core(header_text: str, core_text: str) -> str:
    """V15 R14.1 — Line-level dedup: strip lines from `core_text` that already
    appear (normalized) in `header_text`.

    Use case (TH1): User pastes session prompt + CORE_RULE.txt into a fresh CA
    that has no global rule. Both contain ENV / IDENTIFIERS / BOOTSTRAP /
    "North star" overlap. This helper keeps the session_prompt version
    (User decision: session header is authoritative) and removes the
    duplicate lines from CORE_RULE so the merged paste has no repeated text.

    Normalization:
    - Strip leading/trailing whitespace per line
    - Collapse internal whitespace (multi-space → single)
    - Empty lines and pure dividers (═════ ... ═════) are kept in CORE_RULE
      unless their identical normalized form also appears in header
    - Result post-processed: 3+ consecutive blank lines → 1
    """
    import re as _re

    def _norm(line: str) -> str:
        s = _re.sub(r"\s+", " ", line).strip()
        # Strip markdown heading markers (## ENV ≡ ENV)
        s = _re.sub(r"^#+\s+", "", s)
        # Strip list bullets (- foo ≡ foo)
        s = _re.sub(r"^[-*•]\s+", "", s)
        # Strip enumerated bullets (1. foo, 1) foo)
        s = _re.sub(r"^\d+[.)]\s+", "", s)
        # Strip trailing punctuation that doesn't change semantics
        s = s.rstrip(".")
        return s

    header_seen = set()
    for ln in header_text.splitlines():
        n = _norm(ln)
        if n:
            header_seen.add(n)

    kept = []
    prev_blank = False
    for ln in core_text.splitlines():
        n = _norm(ln)
        if n and n in header_seen:
            # Duplicate of a meaningful line in session header → drop
            continue
        if not n:
            if prev_blank:
                continue  # collapse runs of blank lines
            prev_blank = True
        else:
            prev_blank = False
        kept.append(ln)
    return "\n".join(kept).strip()


def _session_prompt(project_path: str, mode: str, idea: str = "", structure: str = "",
                    agent_id: str = "", host_session_id: str = "") -> str:
    """V15 R14.1 — Generate session prompt for paste-into-fresh-CA scenario.

    Use cases:
    - TH1: CA1 has NO global rule → User pastes this output (header + CORE_RULE)
      directly at session start. CA1 learns workflow from CORE_RULE and the
      project anchor from the session header.
    - TH2: CA2 ALREADY has CORE_RULE as global rule → User typically does NOT
      use this output; just prompts "đăng ký [Ab:P] = ..." and CA2 calls
      open_session. (This path skips _session_prompt entirely.)

    Output structure:
        <full session header: ENV + ORCH + IDENTIFIERS + BOOTSTRAP + SESSION>
        --- CORE RULE ---
        <CORE_RULE.txt with header-overlapping lines stripped line-by-line>

    Dedup strategy:
    - Session header is AUTHORITATIVE (User decision, V15 R14.1)
    - Overlapping lines (ENV, "North star", IDENTIFIERS rows, BOOTSTRAP block,
      health_cmd) appear ONLY in header.
    - CORE_RULE-only sections (MODE, SAFETY, SKILL ROUTING, ERROR/RETRY,
      RESPONSE CONTRACT, Round/Transport rules) are preserved.

    `structure` parameter kept for backward compat but not embedded.
    """
    mode = mode.upper()
    project_path_fwd = project_path.replace("\\", "/")
    idea_block = f"\n**Task:** {idea}" if idea else ""
    identity_block = ""
    if host_session_id:
        identity_block += f"\n- host_session_id = `{host_session_id}`"
    if agent_id:
        identity_block += f"\n- agent_id = `{agent_id}`"
    header = (
        "## ENV\n"
        '- Windows 11. CMD: ["cmd","/c","<cmd>"]\n'
        "- PowerShell only via -File. No WMIC.\n"
        "- [CA] = Code Agent = 'You'\n"
        "\n"
        "═════ PROJECT ORCHESTRATOR ═════\n"
        "- North star: Correctness > Save token > Less context > Time fee\n"
        "\n"
        "IDENTIFIERS\n"
        "- [A] = Orchestrator | [A:P] = its parent folder (auto) | [A:S] = [A:P]\\project-orchestrator\\start_orchestrator.bat | [A:API] = env ORCHESTRATOR_BASE_URL, fallback http://localhost:8083\n"
        "- [Q] = Query Machine / clau-mem librarian memory layer (NOT an orchestrator) | [Q:A] = http://127.0.0.1:8082 | [Q:P] = D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ | [Q:S] = [Q:P]\\Q_S.bat | [Q:S:install] = [Q:P]\\bin\\Q_S_install_task.ps1\n"
        "- [T1]=Read | [T2]=Write/Verify | [T3]=Session/Audit | [T4]=Runtime/DOM/Browser\n"
        "\n"
        "═════ BOOTSTRAP ═════\n"
        "- Active bootstrap rules may be loaded from the prompt. Rule loading is not native fallback.\n"
        "- Do not run a separate health-check or polling loop.\n"
        "- First [A] action: HTTP POST [A:API]/api/call/get_rule_core_text once. Do not call open_session except for explicit Clau/ClauC stateful T4 MCP work.\n"
        "- If an [A] call is connection-refused / ORCHESTRATOR_NOT_RUNNING, stop and report immediately.\n"
        "\n"
        "## SESSION\n"
        "- [Ab] = Absolute Project (CA's target) | [Ab:P] absolute path | [Ab:Rel] inside [Ab]\n"
        f"- [Ab:P] = `{project_path_fwd}`\n"
        "- [Ac]..[Az] = Project # [A] # [Ab]\n"
        f"{identity_block}{idea_block}\n"
        "\n"
        "═════ SESSION KICKSTART (V15 R16) ═════\n"
        "- Pre-flight: `inspect_project_runtime(project_path=[Ab:P])` → detect Electron/Node/Python launcher + remote-debugging port.\n"
        "- Skill router @ [A:P]\\.ToolA_Skill\\SKILLS.md. Quick recipes in 18_runtime_dom_T4 (T4 patterns A-E) and 22_electron_automation.\n"
        "- New tools R16: inspect_project_runtime, launch_electron_with_cdp, dom_state_diff, extract_log_section, fill_value(encoding=base64). See skill 18 Recipe A-E.\n"
        "- Failure triage in skill 18: button state does not change → check CDP attach. lost backslashes in fill → fill_value(encoding=base64). CSP unsafe-eval = warning (ignore default).\n"
        "- ALWAYS open_session(project_path=[Ab:P]) before any T2/T3 mutating call (auto-fires matrix_audit; check matrix_drift_present).\n"
        "\n"
        "Transport contract (V15 R12, unified across tools):\n"
        "- Use only the Orchestrator API; do not bypass [A] for project work.\n"
        "- Phase labels: T1 = Discover / Analyze / Read; T2 = Mutate / Verify / Refresh; T3 = Session / Audit / Auxiliary.\n"
        "- API base placeholder: {ORCHESTRATOR_BASE_URL}.\n"
        "- Encodings: content_encoding, query_encoding, name_encoding, command_encoding = plain|base64.\n"
        "- Windows shell: prefer script-file transport for non-trivial commands; shell_mode must be explicit when command parsing matters.\n"
        "- NEVER use `curl`, `wget`, `bash`, `cat`, `grep`, `sed`, or `awk`; use PowerShell/Node/Python through [A] tools.\n"
        "- For path errors, check for wrong duplicated path before retrying.\n"
    )

    core_text = get_rule_core()
    deduped_core = _dedup_session_vs_core(header, core_text)

    return header + "\n--- CORE RULE ---\n\n" + deduped_core + "\n"


# Canonicalize mode names — accept both short and long forms but persist short.
_MODE_ALIASES = {
    "T1_EXISTING_APP": "T1",
    "T1_FIX": "T1",
    "T1": "T1",
    "T2_NEW_APP": "T2",
    "T2_BUILD": "T2",
    "T2": "T2",
}
_VALID_WORKFLOW_MODES = frozenset({"T1", "T2"})

def _canon_mode(mode: str) -> str:
    if not mode:
        return "T1"
    return _MODE_ALIASES.get(mode.upper(), mode.upper())

def _invalid_mode_error(requested_mode: str, canon: str) -> str:
    return _json_error(
        f"Unknown open_session mode {requested_mode!r} (canonicalized to {canon!r}). "
        "mode is workflow T1/T2, not an execution verb. execute is not valid.",
        error_name="INVALID_MODE",
        requested_mode=requested_mode,
        canonical_mode=canon,
        accepted_modes=["T1", "T2"],
        accepted_aliases=sorted(_MODE_ALIASES.keys()),
        ca_next_action="use_T1_or_T2_or_set_workflow_mode",
        hint="Pass mode=T1 or mode=T2 (aliases T1_FIX/T1_EXISTING_APP/T2_BUILD/T2_NEW_APP). "
             "To change an already-open session, call set_workflow_mode.",
    )

def _open_session_mode_fields(requested_mode: str, canon_mode: str,
                              session_row: dict, created: bool,
                              project_path: str = "", agent_id: str = "") -> dict:
    """Disclose requested-vs-persisted workflow mode on open/resume.

    ISS-08: this used to raise `mode_ignored_on_resume: true` on EVERY resume,
    including the overwhelmingly common case where the requested mode already
    equalled the persisted one -- nothing was ignored, but the caller was told
    something was. A flag that fires when nothing is wrong is a flag nobody
    reads, so the one resume that DID silently drop a T1->T2 request looked
    identical to the hundreds that did not. The flag now fires only on real
    divergence, and when it does it carries the same shape the title-mismatch
    branch already provides: what was asked, what is in force, and the exact
    payloads for the two ways out (re-mode the session, or start a new one).
    """
    persisted = (session_row or {}).get("mode") or (canon_mode if created else "")
    effective = persisted or canon_mode
    fields = {
        "requested_mode": requested_mode or "T1",
        "effective_mode": effective,
        "mode_applied": bool(created),
    }
    if created:
        return fields
    diverged = bool(canon_mode) and bool(effective) and canon_mode != effective
    fields["mode_matches_persisted"] = not diverged
    fields["mode_ignored_on_resume"] = diverged
    if not diverged:
        return fields
    fields["mode_conflict"] = {
        "requested_mode": canon_mode,
        "persisted_mode": effective,
        "in_force": effective,
        "note": ("The PERSISTED mode governs the T1/T2 gate. The requested mode was "
                 "NOT applied, so nothing silently ran at the requested tier."),
    }
    fields["set_workflow_mode_payload"] = {
        "project_path": project_path, "mode": canon_mode,
    }
    fields["force_new_session_payload"] = {
        "project_path": project_path, "mode": canon_mode,
        "agent_id": agent_id, "force_new_session": True,
    }
    fields["ca_next_action"] = "set_workflow_mode_if_requested_mode_is_intended"
    fields["mode_change_hint"] = (
        f"Resume kept the persisted mode {effective!r} and did not apply the requested "
        f"{canon_mode!r}. Call set_workflow_mode (it writes sessions.mode as well as "
        "WORKFLOW_STATE.json — check session_mode.applied in its response), or "
        "open_session with force_new_session=true for a clean session at the new mode."
    )
    return fields


def _default_workflow_state(project_path: str, mode: str, idea: str = "",
                            agent_id: str = "", host_session_id: str = "") -> dict:
    return {
        "project_path": str(Path(project_path).resolve()),
        "mode": _canon_mode(mode),
        "idea": idea,
        "agent_id": agent_id,
        "host_session_id": host_session_id,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "cache_status": surgical_mapper.get_cache_status(project_path).get("status"),
        "backup_status": _backup_manifest_status(project_path),
        "context_ready": (Path(project_path) / "PROJECT_CONTEXT.md").exists(),
        "benchmark_request_created": (Path(project_path) / "BENCHMARK_REQUEST.md").exists(),
        "build_context_created": (Path(project_path) / "BUILD_CONTEXT.md").exists(),
        "last_test_status": None,
        "no_internal_ai": True,
        "no_crawl": True,
    }


async def create_session_bootstrap(project_path: str, mode: str = "T1", idea: str = "") -> str:
    """RETIRED 2026-08-11 (D-ORPHAN): orphaned helper — never registered in
    TOOL_MAP / TOOL_SCHEMAS, no callers outside its own definition. Kept as a
    stub that returns an explicit error so any latent external caller fails
    loud instead of AttributeError. New code should call open_session (which
    auto-materializes the bootstrap prompt) or prepare_ca_session.
    """
    return _json_error(
        "create_session_bootstrap was retired 2026-08-11; it was an unregistered internal helper.",
        error_name="TOOL_RETIRED",
        ca_next_action="use_open_session_or_prepare_ca_session",
    )


async def update_workflow_state(project_path: str, mode: str = None, idea: str = "", **updates) -> str:
    root = Path(project_path).resolve()
    if not root.exists():
        return _json_error(f"Project path not found: {project_path}")

    def _write():
        # CAN-037 (D4): WORKFLOW_STATE.json uses an atomic temp-file replace
        # (_atomic_write_json), so a concurrent write can never leave torn/invalid
        # JSON. Concurrency semantics are LAST-WRITER-WINS on the single state dict
        # (read -> modify -> atomic replace); callers that need a field preserved must
        # not race blind overwrites of it. Runtime_state (per-agent keys) uses
        # merge-on-write instead; the workflow state is a single coherent document so
        # last-writer is the documented, intended behaviour.
        path = _workflow_state_path(str(root))
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
        else:
            state = _default_workflow_state(str(root), mode or "T1", idea)
        if mode:
            state["mode"] = mode.upper()
        if idea:
            state["idea"] = idea
        state.update({k: v for k, v in updates.items() if v is not None})
        state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _atomic_write_json(path, state, indent=2)
        return {"ok": True, "workflow_state_path": str(path), "state": state}

    return json.dumps(await asyncio.to_thread(_write), ensure_ascii=False)


_PREPARE_OWNER_CLAIMS: dict[tuple[str, str], str] = {}


async def prepare_ca_session(project_path: str, mode: str = "T1", idea: str = "",
                             agent_id: str = "", host_session_id: str = "",
                             owner_claim_id: str = "") -> str:
    root = Path(project_path).resolve()
    if not root.exists():
        return _json_error(f"Project path not found: {project_path}")

    # Resolve ownership before project preparation can write any artifacts.
    canonical = None
    if host_session_id and agent_id:
        claim_key = (str(host_session_id).strip(), str(agent_id).strip())
        if not owner_claim_id and not _PREPARE_OWNER_CLAIMS.get(claim_key):
            try:
                previous = await asyncio.to_thread(_task_checkpoint_load, claim_key[0]) or {}
            except TaskCheckpointStorageError as exc:
                return _json_error(str(exc), error_name=exc.error_name,
                                   ca_next_action="repair_task_checkpoint_storage_then_retry")
            if previous.get("owner_capability_sha256") or previous.get("owner_claim_sha256"):
                return _json_error("Persisted ownership requires the original owner_claim_id",
                                   error_name="TASK_OWNER_MISMATCH",
                                   ca_next_action="retry_with_the_original_owner_claim_id")

    def _prepare():
        if not (_project_cache_dir(str(root)) / "INDEX.json").exists():
            surgical_mapper.generate_project_map(str(root))
        elif not (root / "PROJECT_CONTEXT.md").exists():
            surgical_mapper.update_file_cache(str(root / "PROJECT_MAP.md"), str(root))
            surgical_mapper.generate_project_map(str(root))

        cache_dir_s = _project_cache_dir(str(root))
        structure_file = cache_dir_s / "structure_output.txt"
        if not structure_file.exists():
            # Generate inline depth=5 tree and cache it
            def _build_tree(current, cur_depth, prefix="", max_depth=5):
                if cur_depth > max_depth:
                    return []
                try:
                    entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
                except PermissionError:
                    return []
                result_lines = []
                entries = [e for e in entries if e.name not in {".git", "__pycache__", "node_modules", ".orchestrator_cache"}]
                for i, entry in enumerate(entries):
                    connector = "└── " if i == len(entries) - 1 else "├── "
                    result_lines.append(f"{prefix}{connector}{entry.name}")
                    if entry.is_dir():
                        extension = "    " if i == len(entries) - 1 else "│   "
                        result_lines.extend(_build_tree(entry, cur_depth + 1, prefix + extension, max_depth))
                return result_lines
            tree_lines = [f"{root.name}/"] + _build_tree(root, 1)
            structure = "\n".join(tree_lines)
            try:
                cache_dir_s.mkdir(parents=True, exist_ok=True)
                _atomic_write_text(structure_file, structure, encoding="utf-8")
            except Exception:
                pass
        else:
            structure = structure_file.read_text(encoding="utf-8")
        bootstrap = _session_prompt(
            str(root), mode, idea, structure=structure,
            agent_id=agent_id, host_session_id=host_session_id,
        )

        cache_dir = _project_cache_dir(str(root))
        cache_dir.mkdir(parents=True, exist_ok=True)
        bootstrap_path = _bootstrap_path(str(root), agent_id, host_session_id)
        bootstrap_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(bootstrap_path, bootstrap, encoding="utf-8")
        state = _default_workflow_state(
            str(root), mode, idea, agent_id=agent_id,
            host_session_id=host_session_id,
        )
        state_path = _workflow_state_path(str(root), agent_id, host_session_id)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(state_path, state, indent=2)
        # Cache status snapshot (NR1: embed in result so CA doesn't need separate call)
        try:
            _cache_info = surgical_mapper.get_cache_status(str(root))
            cache_status_val = _cache_info.get("status", "unknown")
            cache_suggested  = _cache_info.get("suggested_action", "")
        except Exception:
            cache_status_val = "unknown"
            cache_suggested  = ""

        # V15 R13: auto-invoke matrix audit (silent unless drift)
        matrix_audit_summary = None
        matrix_drift_present = False
        try:
            _m = _audit_tool_matrix_sync(str(root))
            matrix_audit_summary = _m.get("summary")
            matrix_drift_present = bool(_m.get("drift"))
        except Exception:
            pass

        result = {
            "ok": True,
            "mode": mode.upper(),
            "bootstrap_path": str(bootstrap_path),
            "workflow_state_path": str(state_path),
            "prompt": bootstrap,
            "cache": cache_status_val,
            "cache_suggested_action": cache_suggested,
            "matrix_audit": matrix_audit_summary,
            "matrix_drift_present": matrix_drift_present,
            "next_action": "Paste the prompt into CA, then start working.",
        }
        if matrix_drift_present:
            result["matrix_hint"] = "Drift detected — call audit_tool_matrix(include_coverage=true) or open Matrix Log panel."
        return result

    r = await asyncio.to_thread(_prepare)

    # Canonical startup is explicit and idempotent: open_session resumes the
    # matching active agent session, and bootstrap_session returns a stable ref.
    title = idea[:60] if idea else f"{mode.upper()}: {datetime.now().strftime('%m/%d %H:%M')}"
    try:
        opened = json.loads(await open_session(title, str(root), mode, agent_id=agent_id))
    except Exception as exc:
        opened = {"ok": False, "error": str(exc), "error_name": "OPEN_SESSION_FAILED"}
    if opened.get("ok"):
        r["session"] = {
            "status": "resumed" if opened.get("resumed") else "opened",
            "session_id": opened.get("session_id"),
            "agent_id": opened.get("agent_id", agent_id),
            "resumed": bool(opened.get("resumed")),
        }
        r["session_id"] = opened.get("session_id")
    else:
        r["ok"] = False
        r["session"] = {"status": "failed", "error": opened.get("error"),
                        "error_name": opened.get("error_name")}
        r["error"] = opened.get("error") or "open_session failed"
        r["error_name"] = opened.get("error_name") or "OPEN_SESSION_FAILED"
        r["bootstrap"] = {"status": "not_started", "bootstrap_ref": None}
        return json.dumps(r, ensure_ascii=False)

    if host_session_id and agent_id:
        try:
            canonical = json.loads(await _bootstrap_with_cached_owner_claim(
                str(root), host_session_id, agent_id, owner_claim_id,
                claim_cache=_PREPARE_OWNER_CLAIMS,
            ))
        except Exception as exc:
            canonical = {"ok": False, "error": str(exc), "error_name": "BOOTSTRAP_FAILED"}
        claim_id = canonical.get("owner_claim_id", owner_claim_id)
        if not canonical.get("ok"):
            r["ok"] = False
            r["bootstrap"] = {
                "status": "failed", "bootstrap_ref": None,
                "error": canonical.get("error"),
                "error_name": canonical.get("error_name"),
            }
            r["error"] = canonical.get("error") or "bootstrap_session failed"
            r["error_name"] = canonical.get("error_name") or "BOOTSTRAP_FAILED"
            for field in ("ca_next_action", "accepted_fields", "payload_example"):
                if field in canonical:
                    r[field] = canonical[field]
            return json.dumps(r, ensure_ascii=False)
        r["bootstrap"] = {
            "status": "ready", "bootstrap_ref": canonical.get("bootstrap_ref"),
            "session_id": host_session_id, "agent_id": agent_id,
            "owner_claim_id": claim_id,
        }
        if canonical.get("owner_capability"):
            r["bootstrap"]["owner_capability"] = canonical["owner_capability"]
        r["bootstrap_ref"] = canonical.get("bootstrap_ref")
        # Keep credentials ahead of the potentially large generated prompt.
        r = {**{key: canonical[key] for key in (
            "ok", "session_scope", "owner_capability", "owner_claim_id",
            "owner_claim_status", "owner_claim_recovered",
            "owner_claim_expires_in_seconds", "ca_next_action", "bootstrap_ref",
        ) if key in canonical}, **r}
    else:
        r["bootstrap"] = {
            "status": "skipped", "bootstrap_ref": None,
            "reason": "host_session_id and agent_id are required for canonical bootstrap",
        }

    return json.dumps(r, ensure_ascii=False)


_RULE_CORE_FETCH_TTL_SECONDS = int(os.environ.get("ORCHESTRATOR_RULE_CORE_TTL_SECONDS", "21600"))
_RULE_CORE_FETCH_CACHE: dict = {}


def _rule_core_agent_key(agent_id: str = "") -> str:
    return (agent_id or os.environ.get("ORCHESTRATOR_AGENT_ID") or "default").strip() or "default"


# ---------------------------------------------------------------------------
# A37 #0 Tang 0 -- the bootstrap epoch + the read-tool purpose map.
#
# WHY here and nowhere else: get_rule_core_text is the one fixed point in a CA's
# life. It runs exactly once before any project work, so it is the only place where
# "first use since bootstrap" can be a MEASURED fact instead of a guess. Everything
# below is derived from the LIVE registry -- no tool name, no count and no purpose
# sentence is copied into CORE_RULE, because soft data pasted into a rule file is
# exactly the rot this campaign exists to stop.
#
# Measured problem this closes: a CA fetched ~18 schemas in one session, read_file_range
# alone 6 times, because nothing ever told it which read tools exist or what they are
# FOR -- so the cbm graph and the OCR family went undiscovered while the curated read
# tools were re-learned over and over.
# ---------------------------------------------------------------------------

_BOOTSTRAP_EPOCHS: dict = {}
_BOOTSTRAP_EPOCH_LOCK = threading.Lock()
# Bounded so a long-lived daemon serving many agent_ids cannot grow this forever.
# Eviction drops the oldest ctx, which at worst costs that CA one extra schema push --
# the failure mode is a few wasted tokens, never a wrong answer.
_BOOTSTRAP_EPOCH_MAX = 64
# A purpose is ONE sentence, capped. The cap is what keeps Tang 0 a net saving:
# ~40 read tools x ~120 chars lands once, against 1-3KB per schema x ~18 avoided.
_BOOTSTRAP_PURPOSE_MAX_CHARS = 140


def bootstrap_ctx_id(agent_id: str = "") -> str:
    """Context identity for the epoch -- deliberately the SAME key as the rule-core
    cache.

    A33-C measured that the fetch-once guard is per-process BY DESIGN, so agent_id
    already separates two CAs. Adding a session_id parameter would have to be declared
    in the registry schema or the dispatcher refuses it as UNSUPPORTED_FIELD, so the
    epoch reuses the key that already exists rather than inventing a second identity.
    """
    return _rule_core_agent_key(agent_id)


def open_bootstrap_epoch(ctx_id: str) -> int:
    """Start a new epoch for ctx_id and forget every first-use mark from the old one.

    Called ONLY from the full branch of get_rule_core_text. An already_fetched stub
    must never re-arm the pushes: if it did, "first read since bootstrap" would mean
    "first read since the last time I asked", which is not a bootstrap at all.
    """
    with _BOOTSTRAP_EPOCH_LOCK:
        row = _BOOTSTRAP_EPOCHS.get(ctx_id)
        epoch = (int(row["epoch"]) + 1) if row else 1
        _BOOTSTRAP_EPOCHS.pop(ctx_id, None)
        _BOOTSTRAP_EPOCHS[ctx_id] = {"epoch": epoch, "used": set()}
        while len(_BOOTSTRAP_EPOCHS) > _BOOTSTRAP_EPOCH_MAX:
            _BOOTSTRAP_EPOCHS.pop(next(iter(_BOOTSTRAP_EPOCHS)), None)
        return epoch


def bootstrap_epoch_of(ctx_id: str) -> int:
    """Live epoch for ctx_id, 0 when none is open. Read-only on purpose: the stub
    branch discloses the epoch without arming anything."""
    with _BOOTSTRAP_EPOCH_LOCK:
        row = _BOOTSTRAP_EPOCHS.get(ctx_id)
        return int(row["epoch"]) if row else 0


def bootstrap_first_use(ctx_id: str, tool_name: str) -> bool:
    """True exactly ONCE per (ctx_id, epoch, tool_name); False after that.

    False is also the answer when no epoch is open, and that is the safe default: no
    epoch means this caller never bootstrapped through [A], so pushing a schema it
    never asked for would be pure noise.
    """
    if not tool_name:
        return False
    with _BOOTSTRAP_EPOCH_LOCK:
        row = _BOOTSTRAP_EPOCHS.get(ctx_id)
        if not row:
            return False
        used = row["used"]
        if tool_name in used:
            return False
        used.add(tool_name)
        return True


def bootstrap_forget(ctx_id: str, tool_name: str) -> bool:
    """Re-arm one tool's push. Called when that tool ERRORS.

    An error IS the evidence that the CA does not actually hold the schema -- a wrong
    param name or a missing field is what forgetting looks like from outside -- so the
    next call pushes it again instead of letting the CA guess twice.
    """
    if not tool_name:
        return False
    with _BOOTSTRAP_EPOCH_LOCK:
        row = _BOOTSTRAP_EPOCHS.get(ctx_id)
        if not row or tool_name not in row["used"]:
            return False
        row["used"].discard(tool_name)
        return True


def _purpose_sentence(text: str) -> str:
    """First sentence of a tool description, capped.

    Anh's constraint, verbatim: "Hint phai la cong dung, khong phai huong dan su dung,
    schema moi lo huong dan". So this keeps only the opening claim -- what the tool is
    FOR -- and drops the payload talk that follows it in most descriptions. The schema
    owns the how; duplicating it here would pay for the same bytes twice.
    """
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    cut = len(raw)
    for marker in (". ", "! ", "? "):
        idx = raw.find(marker)
        if idx != -1:
            cut = min(cut, idx + 1)
    out = raw[:cut].rstrip()
    if len(out) > _BOOTSTRAP_PURPOSE_MAX_CHARS:
        out = out[:_BOOTSTRAP_PURPOSE_MAX_CHARS - 1].rstrip() + "…"
    return out


def bootstrap_tool_purposes() -> dict:
    """Purpose map for the READ surface, derived from the live registry.

    Membership = (cbm_* | ocr_* | CURATED_CORE_TOOLS) & the non-source mutation scopes.
    The cbm and ocr families are named explicitly because they are exactly the two
    surfaces measured never to be discovered. The scope intersection is what keeps a
    write tool out, and it answers the question a CA is actually asking -- "can this
    change MY files?" -- instead of trusting a name prefix: cbm_get_architecture
    answers mutation_scope="derived_index", not "none", so a none-only filter would
    have dropped the whole cbm family right back out of view.

    Write tools are excluded BY DESIGN. They keep fetching their schema on every call;
    that costs S and t and we accept it, because a wrong write costs Correctness, which
    outranks both.
    """
    try:
        # Lazy: one name, imported at call time, rather than renumbering ~2300 lines
        # of this module to extend the top-level registry_data import block.
        from orchestrator.core.registry_data import CURATED_CORE_TOOLS as _curated
    except Exception:
        _curated = ()
    # globals() and not a direct reference: TOOL_SCHEMAS is rebound as later batches
    # append to it, and this must read whatever the module holds when the CA calls.
    schemas = globals().get("TOOL_SCHEMAS") or []
    out: dict = {}
    for entry in schemas:
        fn = entry.get("function") if isinstance(entry, dict) else None
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "")
        if not name:
            continue
        if not (name.startswith("cbm_") or name.startswith("ocr_") or name in _curated):
            continue
        try:
            if tool_mutation_scope(name) not in NON_SOURCE_MUTATION_SCOPES:
                continue
        except Exception:
            continue
        purpose = _purpose_sentence(fn.get("description"))
        if purpose:
            out[name] = purpose
    return out


_BOOTSTRAP_PUSH_TOOLS_CACHE: dict = {}


def bootstrap_push_tools() -> frozenset:
    """The tools eligible for the #0 first-use schema push = exactly the keys of the
    purpose map, cached.

    ONE membership answer, derived from ONE place: if a tool is worth a purpose sentence
    at bootstrap it is worth its schema on first use, and a second hand-maintained list
    in the dispatcher would be free to drift away from this one. Cached because the
    dispatcher asks on every single tool call; keyed on the live schema count so the set
    rebuilds by itself when a later batch appends to TOOL_SCHEMAS.
    """
    try:
        stamp = len(globals().get("TOOL_SCHEMAS") or ())
    except Exception:
        stamp = -1
    cached = _BOOTSTRAP_PUSH_TOOLS_CACHE.get(stamp)
    if cached is None:
        cached = frozenset(bootstrap_tool_purposes())
        _BOOTSTRAP_PUSH_TOOLS_CACHE.clear()
        _BOOTSTRAP_PUSH_TOOLS_CACHE[stamp] = cached
    return cached

async def get_rule_core_text(agent_id: str = "", force: bool = False) -> str:
    """R102-B once-guard: full CORE_RULE on first fetch per agent/TTL; repeated calls return a
    tiny already_fetched stub (saves ~9KB/repeat). force=true or a distinct agent_id re-fetches.
    Cache is per-process; each agent's MCP server process keeps its own (= per-agent guard)."""
    try:
        rendered = get_rule_core()
        sha = _sha256_text(rendered)
        key = _rule_core_agent_key(agent_id)
        now = time.time()
        seen_at, seen_sha = _RULE_CORE_FETCH_CACHE.get(key, (0.0, ""))
        if not force and seen_sha == sha and (now - seen_at) < _RULE_CORE_FETCH_TTL_SECONDS:
            return json.dumps({
                "ok": True,
                "already_fetched": True,
                "sha256": sha,
                "agent_id": key,
                # A37 #1a: `text` is absent here BY DESIGN, and saying so out loud ends a
                # contradiction a CA hit twice this campaign. The rule says the first fetch
                # must include `text`; this answer has none; so a CA reasonably read the
                # missing key as staleness and re-fetched with force=true, paying ~9KB to be
                # told the same thing. Name the omission AND name the only conditions that
                # justify force, so the two readings can no longer both look correct.
                "text_omitted": True,
                "text_omitted_reason": (
                    "already_fetched: an earlier call in this process returned the full "
                    "CORE_RULE at this exact sha256. A missing `text` is the expected stub "
                    "shape, NOT evidence of staleness."
                ),
                "ca_next_action": (
                    "Reuse the CORE_RULE text already in your context; do not re-fetch. "
                    "force=true is correct ONLY if you genuinely do not hold it: you were "
                    "asked to verify, this sha256 disagrees with the copy you hold, or [A] "
                    "restarted at a new generation and you never held the text. A context "
                    "compaction is not a new session and does not by itself justify force."
                ),
                "bootstrap_epoch": bootstrap_epoch_of(key),
                "hint": "CORE_RULE already returned for this agent within TTL; reuse the cached rule text in context. Pass force=true (or a distinct agent_id) to re-fetch.",
            }, ensure_ascii=False)
        metadata = get_rule_core_metadata()
        _RULE_CORE_FETCH_CACHE[key] = (now, sha)
        # A37 #0 Tang 0: THIS is the bootstrap moment -- the one call every CA makes
        # before any project work, so it is the only place a purpose map can arrive ONCE
        # instead of being re-derived per tool call. The epoch is opened here and NOT in
        # the already_fetched branch above: that is what makes "first read since
        # bootstrap" a measurable fact instead of a guess.
        payload = {
            "ok": True,
            "already_fetched": False,
            "text": rendered,
            **metadata,
        }
        try:
            ctx = bootstrap_ctx_id(agent_id)
            payload["ctx_id"] = ctx
            payload["bootstrap_epoch"] = open_bootstrap_epoch(ctx)
            purposes = bootstrap_tool_purposes()
            if purposes:
                payload["tool_purposes"] = purposes
                payload["tool_purposes_note"] = (
                    "What each read tool is FOR, not how to call it -- the schema owns the "
                    "how. The FIRST call of one of these after this bootstrap returns its "
                    "schema next to the result (response key pushed_schema), so do not fetch "
                    "it: call the tool. A later call does not repeat the push, and an ERROR "
                    "re-pushes it. Write tools are excluded on purpose -- keep fetching their "
                    "schema every call, because a wrong write costs Correctness."
                )
        except Exception as exc:
            payload["tool_purposes_error"] = str(exc)
        return json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        return _json_error(str(exc))


# Phase B (2026-08-11): open_session latency — short-TTL cache for the matrix
# audit summary consumed by open_session. The full _audit_tool_matrix_sync scans
# ~50 SKILL.md files + AGENT_BOOTSTRAP_PROMPT.md + harness/*.md with regex and
# costs ~22ms serial / ~420ms p50 at 20 concurrent (measured). The skill tree
# is static within a session, so a 30s TTL absorbs concurrent open_session
# bursts without changing externally visible behavior. Cache stores only the
# summary+drift-presence open_session reads; the raw audit (coverage, drift
# detail, harness findings) is NOT cached — audit_tool_matrix always re-runs
# the full scan when called directly. Persist (matrix_log.jsonl append) is
# skipped on cache hits; the first call within the TTL still persists.
#
# Single-flight: a compute-in-progress Event forces concurrent callers to wait
# for the leader's result instead of all 20 threads stampeding the filesystem
# scan (measured live: 20 concurrent open_session → p50 ~6.3s, dominated by
# this audit). The waiters fall back to the cache after the leader finishes.
_MATRIX_AUDIT_OPEN_SESSION_CACHE_TTL_SECONDS = 30.0
_MATRIX_AUDIT_OPEN_SESSION_CACHE_LOCK = threading.Lock()
# B-01 (2026-08-11): per-project cache. Keys are normalized project paths via
# _matrix_audit_cache_key (os.path.normcase(os.path.abspath(...))). Values are
# {"at": float, "payload": dict|None}. Before B-01 this was a single global
# {"at": 0.0, "payload": None}, so open_session for Project B within the TTL
# returned Project A's matrix_audit/drift/progressive_gap payload.
_MATRIX_AUDIT_OPEN_SESSION_CACHE: dict = {}
# Per-key single-flight events: key -> threading.Event. One leader scans per
# key; waiters for the SAME key share the leader's result. Distinct projects
# elect leaders independently.
_MATRIX_AUDIT_OPEN_SESSION_INFLIGHT: dict = {}


def _matrix_audit_cache_key(project_path: str) -> str:
    """Normalize project_path for cache/single-flight keying.

    Uses normcase(abspath(...)) so slash-style, backslash-style, and case
    variants of the same path resolve to one key on Windows, matching how
    Path(project_path).resolve() consumers downstream treat the path. Falls
    back to the raw string when normalization itself fails (never raises —
    cache must stay best-effort).
    """
    try:
        return os.path.normcase(os.path.abspath(project_path or ""))
    except Exception:
        return str(project_path or "")


def _drift_force_full_severities() -> set:
    """Live-read the severity set that forces verbosity='full' on drift.

    A13 pattern (see orchestrator/graph/config.py::graph_enabled): read the env
    LIVE on every call, never freeze at import — freezing breaks test isolation.
    Default {high, critical}. Override via ORCHESTRATOR_DRIFT_FORCE_SEVERITIES
    (comma-separated, case-insensitive), e.g. "high,critical,medium".
    """
    raw = os.environ.get("ORCHESTRATOR_DRIFT_FORCE_SEVERITIES", "high,critical")
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def _drift_force_full_count() -> int:
    """Live-read the drift-count threshold that forces 'full' regardless of
    severity. Default 3. Override via ORCHESTRATOR_DRIFT_FORCE_FULL_COUNT."""
    try:
        return int(os.environ.get("ORCHESTRATOR_DRIFT_FORCE_FULL_COUNT", "3"))
    except (TypeError, ValueError):
        return 3


def _drift_note_max_entries() -> int:
    """Live-read the max entries kept in a compact matrix_drift_note. Default 5.
    Override via ORCHESTRATOR_DRIFT_NOTE_MAX_ENTRIES."""
    try:
        return int(os.environ.get("ORCHESTRATOR_DRIFT_NOTE_MAX_ENTRIES", "5"))
    except (TypeError, ValueError):
        return 5


def _open_session_drift_decision(resp: dict, mx, verbosity: str) -> str:
    """V15 F4 (D4) — severity-aware drift verbosity policy for open_session.

    Single source of truth shared by every open_session drift branch (replaces
    the duplicated force-full snippets). Mutates ``resp`` in place and returns
    the effective verbosity string.

    Policy:
      - high/critical severity (see ORCHESTRATOR_DRIFT_FORCE_SEVERITIES) OR
        drift_count>=threshold (ORCHESTRATOR_DRIFT_FORCE_FULL_COUNT, default 3)
        OR any progressive gap  -> legacy force-full: set resp['_drift_override']
        and return 'full'. No matrix_drift_note (full payload already carries
        drift detail).
      - low/medium with count<threshold and no gap -> keep the worker-requested
        verbosity, attach a compact resp['matrix_drift_note'] (<= max entries;
        each {tool, severity, ca_next_action='run audit_tool_matrix'}). If the
        worker already asked for 'full', add the note but no override text.
      - drift_count==0 and no gap -> add nothing, return requested verbosity.

    All thresholds are read LIVE (A13 pattern) so tests / env can retune them.

    ``mx`` is the matrix-audit payload ({'drift': [...], ...}) or None; ``resp``
    is the open_session response dict (carries progressive_gap_count).
    """
    drift = ((mx or {}).get("drift")) or []
    gap = resp.get("progressive_gap_count", 0) or 0

    severities = {str(d.get("severity", "")).strip().lower() for d in drift}
    force_set = _drift_force_full_severities()
    force_count = _drift_force_full_count()

    critical_severity = bool(severities & force_set)
    force_full = bool(critical_severity or len(drift) >= force_count or gap > 0)

    if force_full:
        if verbosity != "full":
            resp["_drift_override"] = (
                f"verbosity forced 'full': drift_count={len(drift)}, "
                f"severities={sorted(severities) if severities else []}, "
                f"progressive_gap_count={gap}"
            )
            return "full"
        return verbosity

    # Low/medium, count below threshold, no gap: keep requested verbosity and
    # attach a compact note (only when there is drift to report).
    if drift:
        max_entries = _drift_note_max_entries()
        resp["matrix_drift_note"] = [
            {
                "tool": d.get("tool"),
                "severity": str(d.get("severity", "")).strip().lower(),
                "ca_next_action": "run audit_tool_matrix",
            }
            for d in drift[:max_entries]
        ]
    return verbosity

# A33 (2026-09-17) - provenance gate for the open_session matrix audit.
#
# Measured defect: open_session reported code_tools 257 / inventory 258 with a
# phantom `ocr_map` drift while audit_tool_matrix on the SAME daemon generation
# reported 258/258/258, drift_count 0. The cache is NOT the cause (in-memory,
# 30s TTL, same producer). orchestrator/server/mcp_stdio.py RAN the open_session
# shortcut IN THE MCP HOST PROCESS - asyncio.run(srv.ct.open_session(...)) -
# instead of proxying over HTTP the way call_tool does. A host whose registry
# was imported before ocr_map existed therefore compared its own 257-tool
# in-memory registry against the daemon-refreshed 258-entry on-disk inventory,
# while the envelope still carried the daemon's generation. matrix_log.jsonl
# proves the skew ran BOTH ways: rows from pid 41876 carry ct=258/inv=257
# (runtime ahead of disk), rows from pid 57644 carry ct=257/inv=258, and 57644
# wrote rows on both sides of the 45504 -> 36100 daemon restart so it cannot be
# the daemon. Only the daemon's own runtime may publish a tool surface as the
# project's truth. Out of scope because unmeasured: progressive_gap_count from a
# foreign process is left untouched - no phantom gap has been observed.
#
# A33-B (2026-09-17) closed that CAUSE: both MCP session shortcuts now proxy to
# the daemon, pinned by tests/test_a33b_mcp_session_shortcut_transport.py. This
# gate still earns its place and must NOT be deleted because the bridge was
# fixed: it guards the PRODUCER, which any non-daemon caller still reaches
# directly (pytest does exactly that), so correctness here never depended on one
# transport staying correct.
_MATRIX_AUDIT_DAEMON_PID_CACHE: dict = {}
_MATRIX_AUDIT_DAEMON_PID_TTL_SECONDS = 5.0


def _daemon_published_pid():
    """PID the daemon published in server_generation.json, or None if unknown.

    Same stamp orchestrator_server.py:120-124 writes and
    orchestrator/server/mcp_stdio.py:262 already reads, so nothing new is
    invented here. conftest.py:156 redirects ORCHESTRATOR_GENERATION_FILE per
    test run, which keeps tests off the live daemon's stamp for free.
    """
    now = time.monotonic()
    cached = _MATRIX_AUDIT_DAEMON_PID_CACHE.get("v")
    if cached and (now - cached[0]) < _MATRIX_AUDIT_DAEMON_PID_TTL_SECONDS:
        return cached[1]
    pid = None
    try:
        configured = os.environ.get("ORCHESTRATOR_GENERATION_FILE")
        gen_file = (
            Path(configured)
            if configured
            else path_runtime.ORCHESTRATOR_DIR
            / ".orchestrator_cache"
            / "server_generation.json"
        )
        published = json.loads(gen_file.read_text(encoding="utf-8"))
        raw = published.get("pid")
        pid = int(raw) if raw is not None else None
    except Exception:
        pid = None
    # Deliberately LOCK-FREE: a race costs one extra tiny file read, while a
    # lock around a callback-reachable path is exactly the A32 deadlock family.
    _MATRIX_AUDIT_DAEMON_PID_CACHE["v"] = (now, pid)
    return pid


def _matrix_audit_runtime_provenance() -> dict:
    """Who computed this audit, and is that the daemon."""
    own = os.getpid()
    daemon_pid = _daemon_published_pid()
    return {
        "audit_pid": own,
        "daemon_pid": daemon_pid,
        "audit_is_daemon_runtime": bool(
            daemon_pid is not None and daemon_pid == own
        ),
    }


def _matrix_audit_gate_foreign_runtime(payload: dict) -> dict:
    """Refuse to publish a non-daemon process's tool surface as project drift.

    Daemon runtime: pass through, plus one `audit_scope` key so the caller can
    see the audit is authoritative. Any other process: keep the numbers but
    under names that say what they measure (`local_registry_counts`), empty the
    drift list, and hand CA the one call that reaches the daemon.
    """
    if not isinstance(payload, dict):
        return payload
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        summary = {}
        payload["summary"] = summary
    if summary.get("audit_scope"):
        return payload  # already gated; never re-capture zeroed counts
    prov = _matrix_audit_runtime_provenance()
    if prov["audit_is_daemon_runtime"]:
        summary["audit_scope"] = "daemon_runtime"
        return payload
    drift = payload.get("drift") or []
    local_counts = {
        k: summary.get(k)
        for k in ("drift_count", "alias_drift_count", "alias_drift_severity")
        if k in summary
    }
    local_counts["drift_tools"] = [
        d.get("tool") for d in drift if isinstance(d, dict)
    ][:5]
    summary.update(prov)
    summary["audit_scope"] = "local_process_registry"
    summary["local_registry_counts"] = local_counts
    summary["drift_count"] = 0
    summary["alias_drift_count"] = 0
    summary["alias_drift_severity"] = None
    summary["audit_note"] = (
        "this process is not the daemon runtime; its in-memory tool registry is"
        " not the project's tool surface, so its drift is reported under"
        " local_registry_counts instead of as project drift"
    )
    summary["ca_next_action"] = (
        "call audit_tool_matrix over HTTP for the daemon's own tool surface"
    )
    # Empty the drift list so matrix_drift_present / matrix_drift_note / the
    # force-full escalation never fire on a phantom. _open_session_drift_decision
    # is deliberately left untouched.
    payload["drift"] = []
    return payload

def _matrix_audit_open_session_summary(project_path: str):
    """Return the matrix-audit fields open_session needs, with TTL + single-flight.

    Cache hit returns the same dict the handler consumed before (summary +
    drift-presence + generated_at) FOR THE SAME normalized project_path. On
    miss, exactly one thread per project key runs the full
    _audit_tool_matrix_sync (which also persists matrix_log.jsonl); other
    threads for that same key wait on the in-flight event and then read the
    now-warm cache entry. If the leader raises, waiters fall through and one
    becomes the next leader. Returned dicts are independent copies so callers
    may mutate safely.
    """
    # Loop bound: at most a handful of leader elections per call (one per
    # failing leader). Each iteration either returns or blocks on an event,
    # so the loop cannot spin.
    key = _matrix_audit_cache_key(project_path)
    for _attempt in range(8):
        now = time.monotonic()
        with _MATRIX_AUDIT_OPEN_SESSION_CACHE_LOCK:
            entry = _MATRIX_AUDIT_OPEN_SESSION_CACHE.get(key) or {}
            cached = entry.get("payload")
            at = entry.get("at", 0.0)
            if cached is not None and (now - at) < _MATRIX_AUDIT_OPEN_SESSION_CACHE_TTL_SECONDS:
                return dict(cached)
            inflight = _MATRIX_AUDIT_OPEN_SESSION_INFLIGHT.get(key)
            if inflight is None:
                inflight = threading.Event()
                _MATRIX_AUDIT_OPEN_SESSION_INFLIGHT[key] = inflight
                is_leader = True
            else:
                is_leader = False

        if not is_leader:
            # Wait for the leader to populate the cache (bounded by the underlying
            # audit cost; the leader's own failure path also releases the event).
            inflight.wait(timeout=30.0)
            continue  # re-check cache; if leader failed, become next leader

        try:
            try:
                full = _audit_tool_matrix_sync(project_path)
            except Exception:
                full = None
            if not isinstance(full, dict):
                return None
            payload = {
                "summary": full.get("summary") or {},
                "drift": full.get("drift"),
                "generated_at": full.get("generated_at"),
                "ok": full.get("ok"),
            }
            # A33 (2026-09-17): gate BEFORE the cache stores it, so a foreign
            # process can never publish its own tool surface as project drift.
            payload = _matrix_audit_gate_foreign_runtime(payload)
            with _MATRIX_AUDIT_OPEN_SESSION_CACHE_LOCK:
                _MATRIX_AUDIT_OPEN_SESSION_CACHE[key] = {
                    "at": time.monotonic(),
                    "payload": dict(payload),
                }
            return payload
        finally:
            with _MATRIX_AUDIT_OPEN_SESSION_CACHE_LOCK:
                _MATRIX_AUDIT_OPEN_SESSION_INFLIGHT.pop(key, None)
            inflight.set()
    return None


# A31 Q4 (2026-09-17, re-sourced A37 2026-09-19): the curated core tool surface.
# SOT MOVED: it was examples/CORE_RULE.txt "TOOL SURFACE (minimal)", but A37 deleted that
# named list ON PURPOSE -- CORE_RULE now points at the bootstrap tool_purposes map, built
# from the live registry at answer time, because a list inside a rule file is exactly the
# soft data CBM-FIRST forbids. The named list survives in examples/SLIM_RULE.txt on the
# "Curated core surface" line, and tests/test_a31_skill_surface_gates.py parses THAT line
# and asserts it equals this tuple, so the two still cannot drift apart silently.
# Deliberately a NAMED LIST and not a count: the same A31 doctrine that says never write a
# tool total in prose next to the code that computes it. Measured trap for whoever edits
# this next: council_message and restart_orchestrator_via_bat are NOT in TOOL_MAP or
# TOOL_SCHEMAS (they register at the server layer), so a liveness check on this tuple must
# union the schema names with registry_data.CURATED_CORE_TOOLS or it reports false ghosts.
CORE_TEN_TOOLS = (
    "get_rule_core_text",
    "open_session",
    "read_file_range",
    "search_code",
    "write_fix",
    "validate_file",
    "council_message",
    "manage_agent",
    "desktop_screenshot",
    "restart_orchestrator_via_bat",
)

# A31 Q5 (2026-09-17): the auto-chain surface, as a NAMED LIST of the fields actually
# emitted -- never as a count. Three campaigns argued "12 vs 13 vs 15 auto-chain rules"
# with no registry to settle it, which is the same rot as writing a tool total in prose:
# ask this tuple, and if a row is missing the field is not registered, whatever a doc says.
# SOT for the prose side is examples/CORE_RULE.txt section "AUTO-CHAIN (pointer)";
# tests/test_a31_autochain_registry.py binds the three directions -- code emits it, the
# registry names it, the rule file mentions it -- so none can drift alone.
# ca_next_action is deliberately NOT a row here: it is free-form advisory prose carried on
# both ok and error payloads -- hundreds of distinct literals, only a handful of which are
# tool names -- so it is a message, not a chain field. Do not add it, and do not pin its
# values in a table: tests/test_a31_autochain_registry.py checks its SHAPE (non-empty,
# stripped, single line) and re-derives the population with an ast walk on every run, which
# is the only figure that cannot go stale. Ask the scanner, not a comment.
# A34 W2 (2026-09-18): the CALLABLE half of that prose IS a row -- `ca_next_tool`. Measured
# over the same population: 25 literals name a real tool inside the advice
# ("use_write_fix_or_choose_new_path") and 26 more are the identical snake_case shape while
# naming no tool at all ("use_partial_then_narrow_regex_scope"), so a CA reading
# ca_next_action alone cannot tell "call this" from "read this" and pays a round trip
# against a tool that does not exist. ca_next_tool is emitted ONLY when the literal resolves
# against the LIVE TOOL_MAP: its presence means callable, its absence means advice. It is
# derived per response by orchestrator.core.next_tool.resolve_next_tool -- never a
# hand-written mapping table, so a tool added or removed changes the answer with no edit.
AUTO_CHAIN_RULES = (
    # (field, trigger, module that emits it)
    ("ruff_preview", "write_fix / write_new_file on a .py file", "orchestrator/tools/write/core.py"),
    ("ca_hint_commit", "write_fix / write_new_file success", "orchestrator/tools/write/core.py"),
    ("ca_hint_review", "git_stage / git_commit", "orchestrator/git_tools.py"),
    ("ca_hint_map", "ocr_map", "orchestrator/tools/ocr_review.py"),
    ("ca_hint_rule", "ocr_rule", "orchestrator/tools/ocr_review.py"),
    ("ca_hint_rule_text", "ocr_rule with the rule body omitted", "orchestrator/tools/ocr_review.py"),
    ("ca_hint_scope", "ocr_scan when a list was really capped", "orchestrator/tools/ocr_review.py"),
    ("ca_hint", "search_code / validate_file / git_status staged>0 / git_diff non-empty / write_fix or write_new_file on a rule or registry file / any matching PASSIVE_CHAINS row applied at the dispatch funnel", "orchestrator_server.py"),
    ("post_ruff_validation", "ruff_fix apply=true", "orchestrator/graph/tools.py"),
    ("ca_chain_hint", "search_code regex on root, cbm_dead_code", "orchestrator/tools/search.py"),
    ("ca_chain_next", "search_code regex on root, cbm coverage", "orchestrator/graph/tools.py"),
    # A35 (b): cbm_dead_code keeps ca_chain_next=write_fix (CORE_RULE documents that
    # chain) but the chain is only safe once the entry is cross-checked, because the
    # graph resolves no call edge through an instance receiver. The precondition is a
    # chain field of its own so it is registered, doc-bound and ast-verified like the
    # rest -- a bare softened sentence inside ca_chain_hint would bind to nothing.
    ("ca_chain_precondition", "cbm_dead_code with findings -- the cross-check owed before the ca_chain_next write_fix", "orchestrator/graph/tools.py"),
    ("ca_next_tool", "any tool whose ca_next_action resolves to a live TOOL_MAP name; injected once by the orchestrator_server dispatch funnel", "orchestrator/core/next_tool.py"),
    # A39: post_commit_validation was BUILT and returned -- the git_commit post-hook in
    # orchestrator_server.py has computed it since Task 3 -- but it was declared NOWHERE,
    # so `advisory_fields` (derived from AUTO_CHAIN_FIELDS) did not cover it and a CA that
    # called the field name as a tool got an empty suggestions list instead of the
    # surface=response_field answer every other chain field gives. Nothing caught it for
    # the same reason: the only registration gate in the A31 suite matches ca_hint*/
    # ca_chain* by shape, and this name is neither, so a non-hint-shaped chain field could
    # ship invisible. Registering it is the whole fix -- the guard extends itself from the
    # registry -- and tests/test_a39_chain_registration_gate.py now closes the class by
    # scanning EVERY declared-chain-field emitter instead of one shape.
    ("post_commit_validation", "git_commit success with committed_files", "orchestrator_server.py"),
    # A39 (same sweep): `ca_next_actions` -- PLURAL -- is the OTHER field that was built and
    # returned while declared nowhere, and it is NOT a stray duplicate of the deliberately
    # unregistered singular above. ISS-08 created it on purpose: a resume can reject the
    # requested title AND the mode, `ca_next_action` holds one string, so overwriting it hid
    # one of the two divergences and the caller fixed the title never learning the mode had
    # not applied. A LIST, emitted at exactly one site under one condition, is a chain field
    # by this registry's own test -- structured, not prose -- so the fix is to register it,
    # never to delete it: deleting it reopens ISS-08. Measured 2026-09-20: one site, the
    # write itself, and ZERO consumers across .py/.js/.html/.md/.txt/.json -- which is
    # precisely how an undeclared field stays invisible long enough to be mistaken for rot.
    ("ca_next_actions", "open_session resume that rejects BOTH the requested title and the mode", "code_tools.py"),
)

#: The chain fields only, for callers that just need the vocabulary.
AUTO_CHAIN_FIELDS = tuple(r[0] for r in AUTO_CHAIN_RULES)

# --- rule-drift trigger (A39) ---------------------------------------------------------
# `write_fix -> rule_drift_check` was a designed chain that was never built: measured
# 2026-09-20, `rule_drift_check` appeared in ZERO chain/hint contexts anywhere in the
# production tree. The consequence is silent and expensive: a CA edits examples/CORE_RULE.txt
# or the tool registry, nothing tells it the doc and the code may now disagree, and the
# drift is found by the next campaign instead of the next call.
#
# The trigger data lives HERE rather than inside rule_drift_check because there are two
# readers -- the check itself and the write path -- and two literals would drift apart the
# day a fourth rule file appears, leaving the trigger silent on exactly the edit it exists
# to catch. One tuple, two readers.

#: The DOC side: the rule files rule_drift_check reads. Also rule_drift_check's own list.
RULE_DRIFT_SOURCES = (
    "examples/CORE_RULE.txt",
    "examples/SLIM_RULE.txt",
    "examples/SLIM_RULE_INLINE.txt",
)

#: The CODE side of the same comparison. Drift has TWO producers and the trigger needs
#: both: editing a rule file can make the doc disagree with the registry, and editing the
#: registry can make the registry disagree with the doc.
RULE_DRIFT_SYMBOLS = (
    "TOOL_MAP",
    "PROTECTED_TOOL_NAMES",
    "TOKEN_REQUIRED_TOOLS",
    "register_tool",
)

#: The files that hold that registry.
RULE_DRIFT_REGISTRY_FILES = (
    "code_tools.py",
    "orchestrator/core/registry_data.py",
)


def rule_drift_trigger_reason(file_path: str, written_text: str = "") -> str:
    """Why THIS write could have drifted the rule files -- or "" when it could not.

    Deliberately NOT "any write". A hint costs tokens on every response that carries it,
    forever, multiplied by every call: a path-only test would fire on every code_tools.py
    edit, and a content-only test would fire on every file that merely mentions TOOL_MAP.
    So the two are combined -- a rule file by PATH alone, a registry file by path AND
    symbol -- and the result is silent on writes that cannot desync examples/*RULE*.txt
    from the live registry.

    Returns the REASON, not a bool, so the caller can say which side moved instead of
    emitting one generic sentence. An encoded body (content_encoding=base64) reaches the
    caller as ciphertext, so a registry edit shipped that way is a MISSED hint, never a
    wrong one -- the path branch above still covers the rule files themselves.
    """
    norm = str(file_path or "").replace("\\", "/").lower()
    for src in RULE_DRIFT_SOURCES:
        if norm.endswith(src.lower()):
            return "you edited %s, one of the rule files rule_drift_check reads" % src
    text = str(written_text or "")
    for reg in RULE_DRIFT_REGISTRY_FILES:
        if norm.endswith(reg.lower()):
            hit = [sym for sym in RULE_DRIFT_SYMBOLS if sym in text]
            if hit:
                return "this %s edit touches %s, which the rule files describe" % (
                    reg.rsplit("/", 1)[-1], ", ".join(hit))
            break
    return ""

def _audit_tool_matrix_sync(project_path: str = "", persist: bool = True) -> dict:
    """V15 R13 — Cross-reference TOOL_MAP / TOOL_SCHEMAS / TOOL_PHASES against
    skill SKILL.md trigger.tool, SKILLS.md router, harness *.md test cases,
    and .ToolA_Skill/references/tool_surface_current.md to detect drift after tool add/remove.

    E-04 (2026-08-10): `persist` controls whether the audit record is appended
    to .orchestrator_cache/matrix_log.jsonl. Default True preserves legacy
    behavior for auto-invocations from prepare_ca_session/open_session; the
    user-facing audit_tool_matrix tool now passes persist=False by default so
    a read-only audit call no longer silently mutates derived state.

    Output structure:
        {
          ok, generated_at, summary: {code_tools, skill_tools, drift_count, ok_count},
          drift: [{type, tool|skill|file, detail, severity}],
          coverage: {tool_name: {in_code, in_skills, in_router, in_inventory, tier}},
          matrix_log_path, persisted
        }
    """
    import re as _re
    skill_root = path_runtime.ORCHESTRATOR_DIR / ".ToolA_Skill"
    if not skill_root.exists():
        return {"ok": False, "error": "skill_root_missing", "path": str(skill_root)}

    # D1 (2026-08-11): when invoked standalone (scripts/sync_tool_matrix.py --ci),
    # code_tools is imported but orchestrator_server is not — so the late
    # _register_tool callsites in orchestrator_server (governed git, council cron,
    # restart helpers, …) never run and the audit sees a 213-tool TOOL_MAP instead
    # of the full 235-tool runtime surface. Self-lift by importing
    # orchestrator_server once when it is not already in sys.modules. This is
    # safe at runtime: orchestrator_server is already imported there, so the
    # branch is a no-op for the prepare_ca_session / open_session auto-audit.
    import sys as _sys_audit
    if "orchestrator_server" not in _sys_audit.modules:
        try:
            import orchestrator_server as _os_lift  # noqa: F401  (side-effect: late registrations)
        except Exception:
            # Standalone audit remains best-effort: if the server module fails to
            # import (e.g. missing runtime deps), fall back to the code_tools-only
            # 213-tool surface so the audit still produces a useful report.
            pass

    code_tools_set = set(TOOL_MAP.keys())
    schema_names = set()
    for s in TOOL_SCHEMAS:
        fn = s.get("function", {}) if isinstance(s, dict) else {}
        name = fn.get("name")
        if name:
            schema_names.add(name)

    # ---- Skill router (SKILLS.md) ----
    # A31 Q4 (2026-09-17): SKILLS.md is a markdown table ("| task | `folder/` | conf |").
    # The previous regex expected "- topic -> NN_skill" rows and matched 0 of the file's
    # lines, so router_topics was silently always []. Two things changed here: parse the
    # table, and do NOT require an NN_ prefix -- some skill folders are named rather
    # than numbered, so a \\d{2}_ pattern could never have reached them even with the
    # separator fixed. routed_folders is what makes the reachability gates below possible.
    skills_md = skill_root / "SKILLS.md"
    router_topics = []
    routed_folders = set()
    if skills_md.exists():
        _row_re = _re.compile(r"^\|(?P<topic>[^|]+)\|\s*`(?P<skill>[A-Za-z0-9_.\-]+)/`\s*\|")
        for line in skills_md.read_text(encoding="utf-8", errors="ignore").splitlines():
            # The header row and the |---|---| separator have no backticked cell, so
            # requiring the backticks is what excludes them -- no line counting.
            m = _row_re.match(line)
            if not m:
                continue
            folder = m.group("skill").strip()
            router_topics.append({"topic": m.group("topic").strip(), "skill": folder})
            routed_folders.add(folder)

    # ---- Per-skill SKILL.md trigger.tool / trigger.task_pattern ----
    skill_tools = {}  # tool_name -> [skill_folder]
    skill_folders = []
    skill_bodies = {}  # folder -> SKILL.md text, reused by the A31 gates below
    skills_without_frontmatter = []
    skills_dir = skill_root / "skills"
    if skills_dir.exists():
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            skill_folders.append(skill_dir.name)
            txt = skill_md.read_text(encoding="utf-8", errors="ignore")
            skill_bodies[skill_dir.name] = txt
            # A31 Q4: harvest `tool:` ONLY from the YAML frontmatter block. Measured on
            # 2026-09-17: frontmatter-only and whole-file both yield the same 94 tools
            # (0 lost), so this is not a coverage change -- it stops body prose from
            # inventing coverage the router would never actually honour.
            fm = _re.match(r"^﻿?---\r?\n(.*?)\r?\n---\r?\n", txt, _re.S)
            if not fm:
                skills_without_frontmatter.append(skill_dir.name)
                continue
            front = fm.group(1)
            # YAML scan: look for `tool: "<list>"` lines inside trigger block.
            # Filter: only treat tokens that look like real tool names (in TOOL_MAP or schema).
            # Avoids false positives from descriptive strings like "any protected T2/T3 tool".
            _known = code_tools_set | schema_names
            for m in _re.finditer(r"tool:\s*['\"]?([^'\"\n]+)['\"]?", front):
                tools_str = m.group(1)
                for tn in _re.split(r"[|,\s]+", tools_str):
                    tn = tn.strip().strip('"').strip("'")
                    if not tn or not _re.match(r"^[a-z_][a-z_0-9]+$", tn):
                        continue
                    # Only register tokens that are actual tool names; skip description noise
                    if tn in _known:
                        skill_tools.setdefault(tn, []).append(skill_dir.name)

    # ---- Tool inventory ----
    inventory_md = path_runtime.ORCHESTRATOR_DIR / ".ToolA_Skill" / "references" / "tool_surface_current.md"
    inventory_tools = set()
    if inventory_md.exists():
        for m in _re.finditer(r"`([a-z_][a-z_0-9]+)`", inventory_md.read_text(encoding="utf-8", errors="ignore")):
            inventory_tools.add(m.group(1))

    # ---- Drift detection ----
    drift = []
    coverage = {}
    for tool in sorted(code_tools_set | schema_names | set(skill_tools.keys())):
        tier = TOOL_PHASES.get(tool, "?")
        cov = {
            "in_code": tool in code_tools_set,
            "in_schema": tool in schema_names,
            "in_skills": tool in skill_tools,
            "in_inventory": tool in inventory_tools,
            "tier": tier,
            "skill_refs": skill_tools.get(tool, []),
        }
        coverage[tool] = cov
        if cov["in_code"] and not cov["in_schema"]:
            drift.append({"type": "missing_schema", "tool": tool, "severity": "high",
                          "detail": "Tool in TOOL_MAP but no TOOL_SCHEMAS entry"})
        if cov["in_schema"] and not cov["in_code"]:
            drift.append({"type": "ghost_schema", "tool": tool, "severity": "high",
                          "detail": "Schema present but no TOOL_MAP dispatch"})
        if cov["in_skills"] and not cov["in_code"]:
            drift.append({"type": "skill_ref_missing_tool", "tool": tool, "skill_refs": cov["skill_refs"],
                          "severity": "medium",
                          "detail": "Skill references a tool that is not registered in TOOL_MAP"})
        if cov["in_code"] and not cov["in_inventory"] and tier in {"T1", "T2", "T3", "T4"}:
            drift.append({"type": "inventory_outdated", "tool": tool, "severity": "low",
                          "detail": "Code tool not listed in .ToolA_Skill/references/tool_surface_current.md"})

    # ---- D1 (2026-08-11): bidirectional check — inventory ghosts ----
    # The audit used to be one-way (in_code && !in_inventory) so hand-edited
    # ghost entries in tool_surface_current.md (e.g. retired tools like
    # `copy_range`, `move_file`) went undetected. Now flag any inventory entry
    # that has no matching TOOL_MAP registration so `audit_tool_matrix` and
    # scripts/sync_tool_matrix.py --ci catch the drift in CI.
    for ghost in sorted(inventory_tools - code_tools_set):
        drift.append({
            "type": "inventory_ghost",
            "tool": ghost,
            "severity": "medium",
            "detail": ("Inventory lists a tool that is not registered in TOOL_MAP. "
                       "Regenerate .ToolA_Skill/references/tool_surface_current.md "
                       "via scripts/sync_tool_matrix.py --generate."),
        })

    # ---- A31 Q4 (2026-09-17): skill-surface gates ----
    # Five checks the audit structurally could not make before, because the SKILLS.md
    # router half was dead code (its regex expected "- topic -> NN_skill" rows and
    # matched 0 lines of the markdown table the file actually is). With routed_folders
    # now populated, a folder nobody routes to, a route to nothing, a SKILL.md with no
    # frontmatter, a Core-10 tool with no trigger.tool, and a dangling references/ link
    # all become reportable instead of invisible.
    for folder in sorted(set(skill_folders) - routed_folders):
        drift.append({
            "type": "skill_folder_unreachable",
            "skill": folder,
            "severity": "medium",
            "detail": (f"skills/{folder}/SKILL.md exists but no row of "
                       ".ToolA_Skill/SKILLS.md routes to it, so the router can never "
                       "reach it. Add a route row naming the folder in backticks."),
        })
    for folder in sorted(routed_folders - set(skill_folders)):
        drift.append({
            "type": "route_to_missing_folder",
            "skill": folder,
            "severity": "high",
            "detail": (f".ToolA_Skill/SKILLS.md routes to `{folder}/` but "
                       f"skills/{folder}/SKILL.md does not exist."),
        })
    for folder in sorted(skills_without_frontmatter):
        drift.append({
            "type": "skill_missing_frontmatter",
            "skill": folder,
            "severity": "medium",
            "detail": (f"skills/{folder}/SKILL.md has no YAML frontmatter block, so any "
                       "trigger.tool it declares is invisible to the router."),
        })
    for tool in CORE_TEN_TOOLS:
        if tool not in skill_tools:
            drift.append({
                "type": "core10_tool_undocumented",
                "tool": tool,
                "severity": "medium",
                "detail": ("Curated-core tool (examples/SLIM_RULE.txt, the 'Curated core "
                           "surface' line) has no trigger.tool route in any SKILL.md "
                           "frontmatter. Body prose does not count: the router only reads "
                           "frontmatter."),
            })
    _shared_refs = skill_root / "references"
    _seen_refs = set()
    for folder in sorted(skill_bodies):
        for m in _re.finditer(r"references/([A-Za-z0-9_.\-]+\.md)", skill_bodies[folder]):
            ref = m.group(1)
            if (folder, ref) in _seen_refs:
                continue
            _seen_refs.add((folder, ref))
            # A link resolves if the file sits beside the skill OR in the shared dir.
            if (skills_dir / folder / "references" / ref).exists():
                continue
            if (_shared_refs / ref).exists():
                continue
            drift.append({
                "type": "skill_reference_missing",
                "skill": folder,
                "reference": ref,
                "severity": "low",
                "detail": (f"skills/{folder}/SKILL.md links references/{ref} but that file "
                           "exists neither beside the skill nor in "
                           ".ToolA_Skill/references/."),
            })
    # ---- Harness scan for obsolete payloads ----
    harness_dir = skill_root / "harness"
    obsolete_patterns = {
        r"\bmode\s*[:=]\s*['\"]base64['\"]": "search_code mode=base64 (REMOVED V15 R12)",
        r"search_text_base64": "search_text_base64 (never shipped — use query_encoding=base64)",
    }
    if harness_dir.exists():
        for f in harness_dir.glob("*.md"):
            txt = f.read_text(encoding="utf-8", errors="ignore")
            for pat, label in obsolete_patterns.items():
                for m in _re.finditer(pat, txt):
                    line_no = txt[:m.start()].count("\n") + 1
                    # Skip negative-test contexts where this is intentional
                    line_text = (split_lines(txt)[line_no - 1:line_no] or [""])[0]
                    if "REMOVED" in line_text or "negative" in line_text.lower() or "MUST FAIL" in line_text:
                        continue
                    drift.append({
                        "type": "obsolete_payload_in_harness",
                        "file": str(f.relative_to(skill_root)),
                        "line": line_no,
                        "pattern": label,
                        "severity": "medium",
                    })

    # ---- R19+R20: Phase F — Alias drift scan ----
    # Scan skill docs + bootstrap for legacy field mentions that have R19 aliases.
    # R20: severity escalates to "medium" when the file is a CA-frequently-read skill
    # (00, 07, 18, 20, 17, 22). pattern is broader: includes JSON-style `{"legacy": ...}`.
    alias_drift = []
    try:
        import sys as _sys
        srv = _sys.modules.get("orchestrator_server") or _sys.modules.get("__main__")
        arg_aliases = getattr(srv, "_ARG_ALIASES", {}) if srv else {}
        scan_roots = []
        try:
            scan_roots.append(path_runtime.ORCHESTRATOR_DIR / ".ToolA_Skill" / "skills")
        except Exception:
            pass
        try:
            bootstrap = path_runtime.ORCHESTRATOR_DIR / "AGENT_BOOTSTRAP_PROMPT.md"
            if bootstrap.exists():
                scan_roots.append(bootstrap)
        except Exception:
            pass
        # R20: skills whose vocabulary must not rot -> escalate severity if a retired
        # term is mentioned there. Hand-curated, not derivable: the first group is by CA
        # read frequency, the trailing group is the newest skill surfaces, where a retired
        # term would be a fresh mistake rather than inherited debt. Every prefix must
        # resolve to a real folder under `.ToolA_Skill/skills` or the entry can never
        # match and silently escalates nothing -- pinned by
        # tests/test_a31_skill_surface_gates.py. That gate is why "20_" is not here:
        # measured 2026-09-17, no folder with that prefix exists.
        _HOT_SKILL_PREFIXES = ("00_", "07_", "17_", "18_", "22_", "26_", "27_", "28_")
        retired_terms = {"tool_inspector", "tool_inventory", "dom_screenshot_summary"}
        def _severity_for(file_path: str) -> str:
            fn = str(file_path).replace("\\", "/")
            if any(f"/skills/{p}" in fn for p in _HOT_SKILL_PREFIXES):
                return "medium"
            if fn.endswith("AGENT_BOOTSTRAP_PROMPT.md"):
                return "medium"
            return "low"
        # R21: skip findings under headings that are explicitly about alias contract
        # / legacy / migration / retired — those sections are MEANT to teach legacy mappings.
        _BYPASS_HEADING_RE = _re.compile(
            r"^\s*#{1,6}\s+.*(alias\s*contract|alias\s*migration|legacy|retired|deprecated|R1[789]\b|R20\b|R21\b)",
            _re.IGNORECASE,
        )
        _ANY_HEADING_RE = _re.compile(r"^\s*#{1,6}\s+")
        def _scan_file(p, tool_alias_pairs, retired):
            findings = []
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return findings
            lines = split_lines(content)
            sev = _severity_for(p)
            # R21: precompute which line ranges are in alias-migration sections (to skip)
            # R25: also skip code-block fenced regions (```...```) — most field-name
            # mentions are inside JSON/code examples, intentional teaching.
            bypass_lines = set()
            in_bypass = False
            in_code_block = False
            for i, ln in enumerate(lines, 1):
                stripped = ln.lstrip()
                if stripped.startswith("```"):
                    in_code_block = not in_code_block
                    bypass_lines.add(i)  # the fence line itself
                    continue
                if in_code_block:
                    bypass_lines.add(i)
                    continue
                if _ANY_HEADING_RE.match(ln):
                    in_bypass = bool(_BYPASS_HEADING_RE.match(ln))
                if in_bypass:
                    bypass_lines.add(i)
            # R28: line-level keyword bypass — when same line has explicit
            # legacy/retired/deprecated/alias annotation, it's intentional teaching.
            _LINE_BYPASS_KEYWORDS = ("legacy", "retired", "deprecated", "alias",
                                       "synonym", "compat", "migration",
                                       "(not ", "never existed", "previously")
            for tname, alias_map in tool_alias_pairs.items():
                for old, canonical in alias_map.items():
                    # V15 R29: word-boundary needles. Drop standalone backtick needle.
                    # V15 R32: REMOVED bare `({old}` needles — they false-matched
                    # `(project_path=` in other tools' calls when tname appeared
                    # anywhere in file. Keep ONLY explicit `tname({old}` prefixed.
                    needles = (
                        f"{tname}({old},", f"{tname}({old})", f"{tname}({old}=", f"{tname}({old} ",
                    )
                    for i, ln in enumerate(lines, 1):
                        if i in bypass_lines:
                            continue  # R21 in alias-migration section
                        ln_lower = ln.lower()
                        if any(kw in ln_lower for kw in _LINE_BYPASS_KEYWORDS):
                            continue  # R28 explicit annotation present
                        if any(n in ln for n in needles) and tname.lower() in content.lower():
                            findings.append({
                                "file": str(p),
                                "line": i,
                                "tool": tname,
                                "legacy_field": old,
                                "canonical": canonical,
                                "severity": sev,
                                "kind": "legacy_alias_in_doc",
                            })
                            break
            for term in retired:
                for i, ln in enumerate(lines, 1):
                    if i in bypass_lines:
                        continue  # R21
                    ln_lower = ln.lower()
                    # R28: expand bypass keywords
                    if any(kw in ln_lower for kw in _LINE_BYPASS_KEYWORDS):
                        continue
                    if term in ln:
                        # R28: skip file-path references like `tool_surface_current.md`
                        if f"{term}_v" in ln or f"{term}.md" in ln:
                            continue  # file path, not retired tool name
                        findings.append({
                            "file": str(p),
                            "line": i,
                            "legacy_term": term,
                            "severity": sev,
                            "kind": "retired_term_referenced",
                        })
            return findings
        for root in scan_roots:
            if root.is_file():
                alias_drift.extend(_scan_file(root, arg_aliases, retired_terms))
            elif root.is_dir():
                for skill_md in root.rglob("SKILL.md"):
                    alias_drift.extend(_scan_file(skill_md, arg_aliases, retired_terms))
    except Exception:
        pass

    # ---- R20/R21: progressive_matrix_check gap count surface ----
    # R21 fix: use path_runtime.resolve_ui_html() instead of guessing
    # <project_path>/UI/... — UI lives at <orchestrator>.parent/UI/, not project root.
    progressive_gap_count = 0
    progressive_gaps = []
    try:
        from path_runtime import resolve_ui_html as _resolve_ui_html
        ui_file = _resolve_ui_html(path_runtime.ORCHESTRATOR_DIR)
        ui_text = ui_file.read_text(encoding="utf-8", errors="replace") if ui_file.exists() else ""
        # V15 R37: align needles with progressive_matrix_check (tools_v15.py:993-1000).
        # Old needles (legacy tab markers) never landed in the actual UI.
        ui_gates = {
            "t4_tab": ('data-mode="T4"' in ui_text or "data-mode='T4'" in ui_text),
        }
        for k, v in ui_gates.items():
            if not v:
                progressive_gap_count += 1
                progressive_gaps.append({"gate": k, "status": "missing", "kind": "ui_marker",
                                          "ui_file_probed": str(ui_file)})
    except Exception as _ui_exc:
        progressive_gaps.append({"gate": "ui_probe_error", "error": str(_ui_exc)})

    # ---- Append to matrix log ----
    matrix_log_path = path_runtime.ORCHESTRATOR_DIR / ".orchestrator_cache" / "matrix_log.jsonl"
    # R20: severity rollup — highest of all alias_drift entries
    _ad_sev = "none"
    if alias_drift:
        _ad_sev = "medium" if any(d.get("severity") == "medium" for d in alias_drift) else "low"
    # R20/R21: inventory classification — split active vs retired/stub vs internal vs endpoint
    _retired_terms = {"tool_inspector", "tool_inventory", "dom_screenshot_summary"}
    inventory_active = sorted(t for t in inventory_tools if t in code_tools_set)
    inventory_retired = sorted(t for t in inventory_tools if t in _retired_terms)
    inventory_extra_raw = sorted(t for t in inventory_tools if t not in code_tools_set and t not in _retired_terms)
    # R21: finer split — endpoint (http path), internal helper (_prefix or known utility),
    # planned/stub (matches "_stub" or in known-planned list), doc_only (rest).
    _ENDPOINT_HINTS = ("api_", "endpoint_", "http_")
    _INTERNAL_HINTS = ("_helper", "_internal", "_v15", "_apply", "_log_", "_session_",
                         "_normalize", "_alias", "_get_", "_set_", "_resolve_", "_compute_",
                         "_ok", "_err", "_init", "_close_", "_inject_", "_payload_")
    _KNOWN_PLANNED_STUBS = {"record_ui_flow"}
    # V15 R29: extend doc-only hints to absorb R28 inventory bloat
    _R29_DOC_ONLY_HINTS = (
        "_payload", "_doc", "_table", "_summary", "_breakdown",
        "_per_tool", "_examples", "_baseline", "_ref", "_v1",
    )
    _R29_COMMON_DOC_NAMES = {
        # Backtick-captured prose: JSON keys, fragments, references
        "name", "tool", "task", "path", "value", "filter", "tier",
        "mode", "fields", "data", "items", "count", "url", "type",
        "ok", "args", "error", "category", "phase", "kind", "scope",
        "schema_version", "round_id", "session_id", "auto_estimate",
        "mutation_scope", "stale_decision", "diagnostic_hint",
        "ca_next_action", "event_bindings", "ipc_handlers",
        "runtime_functions", "scanned_js_files", "skill_tools",
        "drift_count", "alias_drift_count", "weak_schema_count",
        "tool_inventory_v15", "see", "via", "kw", "old",
    }
    inventory_extra_endpoints = []
    inventory_extra_internal = []
    inventory_extra_planned = []
    inventory_extra_doc_only = []
    inventory_extra_unknown = []
    for t in inventory_extra_raw:
        tl = t.lower()
        if t in _KNOWN_PLANNED_STUBS or "_stub" in tl or "planned" in tl:
            inventory_extra_planned.append(t)
        elif tl.startswith(_ENDPOINT_HINTS) or tl in {"api_tools", "api_token", "api_call", "api_health"}:
            inventory_extra_endpoints.append(t)
        elif t.startswith("_") or any(h in tl for h in _INTERNAL_HINTS):
            inventory_extra_internal.append(t)
        elif tl in {"true", "false", "none", "null"} or len(t) <= 2:
            # Backtick-captured noise from inventory prose (e.g. `true`, `id` in JSON example)
            inventory_extra_doc_only.append(t)
        elif tl in _R29_COMMON_DOC_NAMES:
            # V15 R29: common JSON keys / prose fragments mis-captured by scanner
            inventory_extra_doc_only.append(t)
        elif any(h in tl for h in _R29_DOC_ONLY_HINTS):
            # V15 R29: suffix-marked doc artifacts (_payload_example, _v1, etc.)
            inventory_extra_doc_only.append(t)
        elif "_" in tl and len(tl.split("_")) >= 3 and tl not in code_tools_set:
            # V15 R29: 3+ underscore segments unlikely to be real tool names
            inventory_extra_doc_only.append(t)
        else:
            inventory_extra_unknown.append(t)
    inventory_extra = inventory_extra_raw  # legacy compat
    summary = {
        "code_tools": len(code_tools_set),
        "schema_tools": len(schema_names),
        "skill_tools": len(skill_tools),
        "skill_folders": len(skill_folders),
        "inventory_tools": len(inventory_tools),
        # R20: classify inventory split
        "inventory_active_count": len(inventory_active),
        "inventory_retired_count": len(inventory_retired),
        "inventory_extra_count": len(inventory_extra),
        # R21: finer extras split
        "inventory_extra_endpoints_count": len(inventory_extra_endpoints),
        "inventory_extra_internal_count": len(inventory_extra_internal),
        "inventory_extra_planned_count": len(inventory_extra_planned),
        "inventory_extra_doc_only_count": len(inventory_extra_doc_only),
        "inventory_extra_unknown_count": len(inventory_extra_unknown),
        "drift_count": len(drift),
        "ok_count": len(code_tools_set) - len(drift),
        "alias_drift_count": len(alias_drift),
        "alias_drift_severity": _ad_sev,
        "progressive_gap_count": progressive_gap_count,
    }
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "project_path": project_path,
        # A06: the persisted record is read back by /api/matrix_log (history[])
        # and rendered by the Matrix Log panel, i.e. by a reader who never sees
        # the live return value. Carry the same scope declaration, or a stored
        # `drift_count: 0` reads as a disk-vs-runtime guarantee the audit was
        # never able to make. `pid` disambiguates which runtime produced the
        # line: two rows can disagree simply because a restart happened between
        # them, and without it that looks like drift appearing from nowhere.
        "scope": "runtime_only",
        "pid": os.getpid(),
        "summary": summary,
        "drift": drift,
    }
    persisted = False
    if persist:
        try:
            matrix_log_path.parent.mkdir(parents=True, exist_ok=True)
            with matrix_log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            persisted = True
        except Exception:
            pass

    # R20: ca_next_action escalates when alias_drift contains medium-severity items
    # (skill 00/07/17/18/20/22 that CA reads frequently)
    if drift:
        _next = "review_drift_and_fix"
    elif _ad_sev == "medium":
        _next = "review_alias_drift_for_S_L"
    elif progressive_gap_count > 0:
        _next = "review_progressive_gaps"
    elif alias_drift:
        _next = "alias_drift_informational"
    else:
        _next = "no_action_needed"
    return {
        "ok": True,
        "generated_at": record["ts"],
        # A06: declare the audit's blind spot. This function cross-references the
        # in-memory TOOL_MAP / TOOL_SCHEMAS / TOOL_PHASES against .md files only;
        # it never parses .py source on disk. So drift_count=0 means the running
        # runtime is internally consistent, NOT that the code on disk matches the
        # runtime. If a committed tool has not been loaded, this audit still
        # reports drift 0. Proving disk == runtime requires a restart.
        "scope": "runtime_only",
        "scope_note": (
            "Compares in-memory TOOL_MAP/TOOL_SCHEMAS against .md docs only; does "
            "not parse .py source on disk. drift_count=0 means runtime is "
            "internally consistent, not that disk matches the running runtime. To "
            "prove disk == runtime, restart the orchestrator."
        ),
        "summary": summary,
        "drift": drift,
        "alias_drift": alias_drift,  # R19: informational, not failing
        "progressive_gaps": progressive_gaps,  # R20: surface UI progressive gates
        "inventory_classification": {
            "active_in_code": inventory_active[:50],
            "retired_referenced": inventory_retired,
            "extras_or_endpoints": inventory_extra[:50],
            # R21: per-category extras lists
            "extras_endpoints": inventory_extra_endpoints,
            "extras_internal": inventory_extra_internal[:30],
            "extras_planned": inventory_extra_planned,
            "extras_doc_only": inventory_extra_doc_only[:30],
            "extras_unknown": inventory_extra_unknown[:30],
        },
        "coverage": coverage,
        "router_topics_count": len(router_topics),
        "matrix_log_path": str(matrix_log_path),
        "persisted": persisted,
        "ca_next_action": _next,
    }


async def audit_tool_matrix(project_path: str = "", include_coverage: bool = False,
                             verbosity: str = "full", persist: bool = False) -> str:
    """V15 R13 — Built-in matrix audit: cross-checks TOOL_MAP / TOOL_SCHEMAS /
    TOOL_PHASES against skill SKILL.md, SKILLS.md router, harness, inventory.

    Use after adding/removing a tool, or after large skill/workflow edits.
    Auto-invoked by `prepare_ca_session` (silent unless drift). UI exposes
    drift report via /api/matrix_log endpoint.

    V15 R22: `verbosity` ∈ {full|terse|ref} — terse caps drift[] + alias_drift[]
    at 3 entries each, drops coverage + inventory_classification. ref drops all
    arrays (counts only). Saves ~1500-1850 tokens/call. Drift safety: auto-force
    `full` when drift_count>0 OR progressive_gap_count>0.

    E-04 (2026-08-10): `persist` defaults to False so a read call does NOT
    append to .orchestrator_cache/matrix_log.jsonl. Pass persist=True to
    record the audit (matches the legacy auto-invoke behavior used by
    prepare_ca_session/open_session). The schema previously claimed
    non-mutating while the handler always wrote — this restores metadata
    honesty: read calls are pure reads; persistence is an explicit opt-in.
    """
    def _run():
        result = _audit_tool_matrix_sync(project_path, persist=persist)
        if not include_coverage:
            result.pop("coverage", None)
        return result
    result = await run_in_executor_lane("read_io", _run)
    # R22 Phase C — drift safety net
    _summary = result.get("summary", {}) or {}
    _eff_verbosity = verbosity
    if (_summary.get("drift_count", 0) > 0 or _summary.get("progressive_gap_count", 0) > 0) and verbosity != "full":
        result["_drift_override"] = (
            f"verbosity forced 'full': drift_count={_summary.get('drift_count', 0)}, "
            f"progressive_gap_count={_summary.get('progressive_gap_count', 0)}"
        )
        _eff_verbosity = "full"
    try:
        import tools_v15 as _v15
        result = _v15._apply_verbosity(result, _eff_verbosity, "matrix_audit")
    except Exception:
        pass
    # A39: stamped AFTER _apply_verbosity on purpose. A key written before a verbosity /
    # R101 filter is a key that filter can prune out of the very response it was written
    # for (measured in A37), and an advisory that survives only at verbosity="full" is
    # worse than none: its absence would read as "no drift".
    #
    # The condition is the SAME one _drift_override already computed above, so the trigger
    # costs no extra work and is silent on a clean matrix. It names rule_drift_check, not
    # write_fix: matrix drift is a doc-vs-registry disagreement and WHICH SIDE MOVED is
    # precisely what rule_drift_check answers -- pointing straight at a write would invite
    # editing the doc to match a registry that is itself the bug.
    _drift_n = int(_summary.get("drift_count", 0) or 0)
    _gap_n = int(_summary.get("progressive_gap_count", 0) or 0)
    if _drift_n or _gap_n:
        result["ca_hint"] = (
            "rule_drift_check(project_path=...) says which side moved for drift_count=%d / "
            "progressive_gap_count=%d -- read that before editing either side"
            % (_drift_n, _gap_n))
        result.setdefault("ca_next_action", "rule_drift_check")
    return json.dumps(result, ensure_ascii=False)


async def set_workflow_mode(project_path: str, mode: str, idea: str = "") -> str:
    """Change the workflow mode in BOTH records that claim to hold it.

    ISS-13: this used to write only WORKFLOW_STATE.json. `sessions.mode` was
    write-once at open time, and open_session on resume answers
    `mode_ignored_on_resume: true` with the hint "call set_workflow_mode" -- a
    hint this tool could not honour. The T1/T2 gate reads the SESSION row, so a
    caller that followed the hint saw ok:true and stayed on the old mode
    forever, with the JSON file and the session row disagreeing permanently.
    The response now reports the session-row outcome explicitly, including the
    case where there is no active session to re-mode.
    """
    raw = await update_workflow_state(project_path, mode=mode, idea=idea)
    try:
        result = json.loads(raw)
    except Exception:
        return raw
    if not isinstance(result, dict) or not result.get("ok"):
        return raw

    canon = _canon_mode(mode)
    try:
        root = str(Path(project_path).resolve())
        session_id = _get_active_session_id(_session_project_key(root))
        if not session_id:
            result["session_mode"] = {
                "applied": False,
                "reason": "no_active_session",
                "hint": ("WORKFLOW_STATE.json now says "
                         f"{canon}, but no active session carries a mode. The next "
                         "open_session will persist this mode on creation."),
            }
        else:
            applied = _run_session_write(
                "set session workflow mode",
                lambda write_conn: _session_store_module._set_session_mode(
                    write_conn, session_id, canon),
            )
            if applied is None:
                result["session_mode"] = {
                    "applied": False, "session_id": session_id,
                    "reason": "session_not_active",
                    "hint": "The session closed between lookup and write; call open_session.",
                }
            else:
                result["session_mode"] = {"applied": True, "session_id": session_id, **applied}
    except Exception as exc:
        # The JSON file is already updated; report the split instead of
        # pretending the whole change landed.
        result["session_mode"] = {"applied": False,
                                  "reason": f"session_write_failed: {type(exc).__name__}",
                                  "error": str(exc)[:300]}
    result["mode"] = canon
    return json.dumps(result, ensure_ascii=False)


async def workflow_status_registry(project_path: str) -> str:
    """V15 R43.2 C2 fix: renamed from workflow_status to avoid name collision with
    the R35 MD workflow version (line ~8112). Previously the second def silently
    shadowed this T1 registry inspector → it became dead code.
    Now distinctly callable as `workflow_status_registry` for cache/drift/mode/backup
    state of project-registry (.orchestrator_cache state file), separate from
    R35 MD-driven workflow runner (`workflow_status`).
    """
    try:
        root = Path(project_path).resolve()
        path = _workflow_state_path(str(root))
        if not path.exists():
            state = _default_workflow_state(str(root), "T1")
            path.parent.mkdir(parents=True, exist_ok=True)
            created = True
        else:
            state = json.loads(path.read_text(encoding="utf-8"))
            created = False
        live_cache = surgical_mapper.get_cache_status(str(root))
        if live_cache.get("index_exists"):
            try:
                delta = json.loads(await cache_delta(str(root)))
                drift = {
                    "added_total": len(delta.get("added", []) or []),
                    "modified_total": len(delta.get("modified", []) or []),
                    "deleted_total": len(delta.get("deleted", []) or []),
                }
                if any(drift.values()):
                    live_cache["status"] = "stale"
                    live_cache["drift_warning"] = True
                    live_cache["drift"] = drift
            except Exception as exc:
                live_cache["drift_check_error"] = str(exc)
        state["cache_status"] = live_cache.get("status")
        state["drift_warning"] = bool(live_cache.get("drift_warning"))
        if live_cache.get("drift"):
            state["drift"] = live_cache.get("drift")
        # V15 R28: stale_decision field — explicit routing for T1/T2/T4 tasks
        if state.get("cache_status") == "stale" and state.get("drift_warning"):
            state["stale_decision"] = {
                "T1_read_only": "warning_only_proceed",
                "T2_write_mutate": "BLOCKER_call_refresh_project_map_first",
                "T4_runtime": "warning_check_probe_evidence_before_restart",
                "_decision_table_v": "R28",
                "_skill_ref": "skill 16 server_lifecycle R28 decision table",
            }
        state["backup_status"] = _backup_manifest_status(str(root))
        state["active_session_id"] = _get_active_session_id(_session_project_key(str(root)))
        state["backup_required_for_large_changes"] = state["backup_status"] in {"missing", "unknown"}
        state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _atomic_write_json(path, state, indent=2)
        result = {"ok": True, "state": state, "workflow_state_path": str(path)}
        # V15 R29 A.1: surface stale_decision at top-level for CA shape stability
        if state.get("stale_decision"):
            result["stale_decision"] = state["stale_decision"]
        if created:
            result["created"] = True
        return json.dumps(result, ensure_ascii=False)
    except PermissionError as exc:
        return _json_error(
            str(exc),
            error_name="FILE_ACCESS_DENIED",
            type="PERMISSION_ERROR",
            severity="error",
            retryable=False,
            reason="permission denied while reading workflow state file",
            ca_next_action="check_file_permissions_or_run_with_access_to_project",
            suggested_fallback="none",
        )


async def next_recommended_action(project_path: str) -> str:
    root = Path(project_path).resolve()
    state_path = _workflow_state_path(str(root))
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else _default_workflow_state(str(root), "T1")
    mode = state.get("mode", "T1").upper()
    if surgical_mapper.get_cache_status(str(root)).get("status") != "ok":
        action = "Run generate_project_map or refresh_project_map."
    elif _backup_manifest_status(str(root)) in {"missing", "unknown"}:
        action = "Run backup_project before large edits, deletes, cross-module changes, or contract changes."
    elif not _bootstrap_path(str(root)).exists():
        action = "Run prepare_ca_session."
    elif mode.startswith("T2") and not (root / "BENCHMARK_REQUEST.md").exists():
        action = "Create BENCHMARK_REQUEST.md from the user idea."
    elif mode.startswith("T2") and not (root / "BUILD_CONTEXT.md").exists():
        action = "Create BUILD_CONTEXT.md from user-approved inputs."
    else:
        action = "Use INDEX refs to read exact ranges, then edit/test/cache-delta."
    return json.dumps({
        "ok": True,
        "mode": mode,
        "next_action": action,
        "next_recommended_action": action,
        "context": {
            "cache_status": surgical_mapper.get_cache_status(str(root)).get("status"),
            "backup_status": _backup_manifest_status(str(root)),
            "has_bootstrap": _bootstrap_path(str(root)).exists(),
        },
    }, ensure_ascii=False)


async def capability_status(project_path: str = "", include: list = None) -> str:
    """V15 R8 A.4: extended capability_status.

    include: list of "capabilities" (default), "protected", "optional", "missing".
    With empty include or default, returns just the capability list (legacy shape).
    With include set, aggregates additional fields from protected_tools_status /
    optional_tool_probe / suggest_missing_capabilities.
    """
    capabilities = [
        {"name": "python_ast", "available": True, "install": None, "benefit": "Python functions/classes/imports without full file reads."},
        {"name": "tree_sitter_languages", "available": bool(getattr(surgical_mapper, "TREE_SITTER_AVAILABLE", False)), "install": "pip install tree-sitter-languages", "benefit": "Better JS/TS symbols and imports."},
        {"name": "yaml_parser", "available": _module_available("yaml"), "install": "pip install pyyaml", "benefit": "Structured YAML key-path cache."},
        {"name": "markdown", "available": True, "install": None, "benefit": "Markdown headings and docs can be routed with regex fallback."},
        {"name": "powershell_shell", "available": True, "install": None, "benefit": "PowerShell/Bat files can be grouped and searched."},
        {"name": "sql_regex", "available": True, "install": None, "benefit": "SQL files can be grouped and routed with bounded reads."},
        # V15 R8 added capability probes
        {"name": "playwright", "available": _module_available("playwright"), "install": "pip install playwright && playwright install chromium", "benefit": "T4 browser DOM tools."},
        {"name": "psutil", "available": _module_available("psutil"), "install": "pip install psutil", "benefit": "T4c server_lifecycle_status, port_owner_inspect."},
        {"name": "pillow", "available": _module_available("PIL"), "install": "pip install pillow", "benefit": "T4d desktop_screenshot."},
    ]
    out: dict = {"ok": True, "capabilities": capabilities}
    if include:
        if isinstance(include, str):
            include = [include]
        if "protected" in include:
            out["protected"] = json.loads(await protected_tools_status())
        if "optional" in include:
            try:
                out["optional"] = json.loads(await optional_tool_probe([]))
            except Exception as exc:
                out["optional"] = {"ok": False, "error": str(exc)}
        if "missing" in include:
            out["missing"] = json.loads(await suggest_missing_capabilities(project_path))
    return json.dumps(out, ensure_ascii=False)


def _module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


async def suggest_missing_capabilities(project_path: str = "") -> str:
    status = json.loads(await capability_status(project_path))
    missing = [item for item in status["capabilities"] if not item.get("available") and item.get("install")]
    return json.dumps({"ok": True, "missing": missing, "auto_install": False}, ensure_ascii=False)


async def backup_status(project_path: str) -> str:
    root = Path(project_path).resolve()
    manifest_path = _project_cache_dir(str(root)) / "BACKUP_MANIFEST.json"
    if not manifest_path.exists():
        return json.dumps({
            "ok": True,
            "status": "missing",
            "message": "No Orchestrator backup manifest found.",
            "next_action": "Run backup_project(project_path, force=True) before large edits.",
        }, ensure_ascii=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    backups = manifest.get("backups", [])
    # Backward-compat: manifest cũ chỉ có backup_path đơn
    if not backups and manifest.get("backup_path"):
        backups = [{"created_at": manifest.get("created_at", ""), "backup_path": manifest["backup_path"]}]
    # Chỉ giữ những file còn tồn tại
    existing = [b for b in backups if Path(b["backup_path"]).exists()]
    for item in existing:
        try:
            bp = Path(item["backup_path"])
            item["size_mb"] = round(bp.stat().st_size / (1024 * 1024), 2)
        except Exception:
            item["size_mb"] = None
        try:
            created = datetime.fromisoformat(str(item.get("created_at", "")))
            item["backup_age_hours"] = round((datetime.now() - created).total_seconds() / 3600, 2)
        except Exception:
            item["backup_age_hours"] = None
    return json.dumps({
        "ok": True, "status": "available",
        "total_backups": len(existing),
        "latest": existing[0] if existing else None,
        "all_backups": existing,
    }, ensure_ascii=False)


# ── Per-project backup locks ─────────────────────────────────────────────
# Serialize backup_project calls per canonical project root: a second call
# while one is in-flight returns a structured BACKUP_IN_PROGRESS error
# instead of queueing another full os.walk on the single-worker backup
# lane. Locks are keyed by the resolved project path so different projects
# can back up concurrently. Guarded by a threading lock so concurrent
# first-time lookups from different event loops return the SAME
# asyncio.Lock instance per project.
_BACKUP_PROJECT_LOCKS: dict[str, asyncio.Lock] = {}
_BACKUP_PROJECT_LOCKS_GUARD = threading.Lock()


def _backup_lock_for(project_lock_key: str) -> asyncio.Lock:
    """Return the stable per-project asyncio.Lock for ``project_lock_key``.

    The key is the canonical (resolved) project root path string. The same
    key always returns the same lock instance so concurrent callers contend
    on a single lock per project. Lock instances are NOT reaped; the
    registry is intentionally small (one entry per project touched).
    """
    with _BACKUP_PROJECT_LOCKS_GUARD:
        lock = _BACKUP_PROJECT_LOCKS.get(project_lock_key)
        if lock is None:
            lock = asyncio.Lock()
            _BACKUP_PROJECT_LOCKS[project_lock_key] = lock
        return lock


async def backup_project(
    project_path: str,
    backup_dir: str = None,
    timeout: int = 600,
    force: bool = False,
    keep_last_n: int = 2,
    target_paths: list[str] | None = None,
) -> str:
    root = Path(project_path).resolve()
    if not root.exists():
        return _json_error(f"Project path not found: {project_path}")
    if target_paths is None and (root == Path.home().resolve() or root.parent == root):
        return _json_error(
            f"Full backup denied for broad root: {root}. Pass target_paths for a targeted backup."
        )
    if not isinstance(keep_last_n, int) or isinstance(keep_last_n, bool) or keep_last_n < 1:
        return _json_error("keep_last_n must be an integer greater than or equal to 1")

    normalized_targets: list[Path] | None = None
    normalized_target_labels: list[str] = []
    if target_paths is not None:
        normalized_targets = []
        for value in dict.fromkeys(
            item for item in target_paths if isinstance(item, str) and item.strip()
        ):
            raw_candidate = Path(value)
            raw_candidate = raw_candidate if raw_candidate.is_absolute() else (root / raw_candidate)
            if raw_candidate.is_symlink():
                return _json_error(f"Backup target cannot be a symlink: {value}")
            candidate = raw_candidate.resolve()
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                return _json_error(f"Backup target is outside project root: {value}")
            normalized_targets.append(candidate)
            normalized_target_labels.append(str(relative).replace("\\", "/"))
        normalized_target_labels.sort()

    cache_dir = _project_cache_dir(str(root))
    manifest_path = cache_dir / "BACKUP_MANIFEST.json"

    # --- Skip nếu đã backup hôm nay và không force ---
    if not force and manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            backups = existing.get("backups", [])
            # Backward-compat: manifest cũ chỉ có backup_path đơn (không có backups list)
            if not backups and existing.get("backup_path"):
                backups = [{"created_at": existing.get("created_at", ""), "backup_path": existing["backup_path"]}]
            if backups:
                latest = backups[0]
                today = datetime.now().strftime("%Y-%m-%d")
                requested_scope = "targeted" if normalized_targets is not None else "full"
                latest_targets = sorted(latest.get("target_paths", []))
                if (latest.get("created_at", "").startswith(today)
                        and Path(latest["backup_path"]).exists()
                        and latest.get("backup_scope", "full") == requested_scope
                        and latest_targets == normalized_target_labels):
                    return json.dumps({
                        "ok": True,
                        "skipped": True,
                        "reason": f"Backup already exists from today ({today}). Use force=True to override.",
                        "backup_path": latest["backup_path"],
                        "created_at": latest["created_at"],
                    }, ensure_ascii=False)
        except Exception:
            pass  # manifest corrupt → tạo mới

    # Serialize backups per project: a second call while one is running returns
    # a structured backup_in_progress error instead of queueing another full
    # os.walk on the single-worker backup lane. Targeted backups still run
    # concurrently across DIFFERENT projects.
    project_lock_key = str(root)
    backup_lock = _backup_lock_for(project_lock_key)
    if backup_lock.locked():
        return _json_error(
            "backup_project is already running for this project",
            error_name="BACKUP_IN_PROGRESS",
            reason="backup_in_progress",
            project_path=str(root),
            backup_scope="targeted" if normalized_targets is not None else "full",
            retryable=True,
            retry_after_seconds=1,
            ca_next_action="wait_for_current_backup_then_retry",
        )

    async with backup_lock:
        return await _backup_project_locked(
            root=root,
            backup_dir=backup_dir,
            timeout=timeout,
            keep_last_n=keep_last_n,
            normalized_targets=normalized_targets,
            normalized_target_labels=normalized_target_labels,
            cache_dir=cache_dir,
            manifest_path=manifest_path,
        )


async def _backup_project_locked(
    *,
    root: Path,
    backup_dir: str | None,
    timeout: int,
    keep_last_n: int,
    normalized_targets: list[Path] | None,
    normalized_target_labels: list[str],
    cache_dir: Path,
    manifest_path: Path,
) -> str:
    import threading
    stop_backup = threading.Event()

    def _backup():
        target_root = Path(backup_dir).resolve() if backup_dir else root.parent / "_orchestrator_backups"
        target_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        archive_base = target_root / f"{root.name}_backup_{timestamp}"
        # CSLt Correctness: shutil.make_archive fails hard on locked files
        # (Windows orchestrator.lock → PermissionError). Walker skips unreadable.
        import zipfile
        archive_path = str(archive_base) + ".zip"
        skipped: list[str] = []
        written = 0
        targets = normalized_targets
        # B027j 2026-09-22: was `{d.lower() for d in surgical_mapper.SKIP_DIRS}`, i.e.
        # the global EXACT set only -- no `.orchestrator_ignore` overlay, no global
        # globs. `effective_skip_dir_names` carries all three layers plus the `!unskip`
        # lines, so a project that unskips `archive` now gets `archive` into its backup.
        # The 11 names below were EVERY ONE already in SKIP_DIRS when measured; they
        # stay as a floor so a future edit to the global set cannot make a backup start
        # zipping `node_modules`. frozenset so the `_glob_patterns` lru_cache inside
        # `name_matches_ignore` hits instead of rebuilding the subset per directory.
        skip_dir_names = set(effective_skip_dir_names(root))
        skip_dir_names.update({
            ".git", "node_modules", "__pycache__", "venv", "venv312", ".venv",
            ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
        })
        skip_dir_names = frozenset(skip_dir_names)
        skip_file_names = {
            "orchestrator.lock", "sessions.db-journal", "sessions.db-wal",
            "sessions.db-shm", "Thumbs.db", "desktop.ini",
        }
        try:
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                # B027j 2026-09-22: this used to be a LIST built with
                # `walk_entries.extend(os.walk(target))`, and that made the whole
                # directory filter below dead code. `os.walk` is a generator that
                # honours an in-place edit of its `dirnames` list only while it is
                # still being iterated; `extend` drains it to the last leaf FIRST, so
                # by the time the loop pruned `dirs[:]` the descent had already
                # happened and every pruned tree was already in `walk_entries`.
                # Measured 2026-09-22: a full backup archived `.git/`,
                # `node_modules/` and every other name in the skip set, under
                # ok:true -- only the per-FILE skips had any effect. Yielding lazily
                # is what connects the filter to the walk.
                def _walk_targets():
                    for target in (targets if targets is not None else [root]):
                        if target.is_file():
                            yield (str(target.parent), [], [target.name])
                        elif target.is_dir():
                            yield from os.walk(target)
                        else:
                            skipped.append(
                                str(target.relative_to(root)).replace("\\", "/"))

                for walk_root, dirs, files in _walk_targets():
                    if stop_backup.is_set():
                        raise InterruptedError("backup cancelled")
                    # B027j 2026-09-22: a raw `not in` here was blind to the overlay
                    # AND to the global glob layer, so a backup zipped the junk trees
                    # the index prunes. Deliberately NOT `should_skip_dir`: that gate
                    # also drops every leading-dot directory, and a BACKUP silently
                    # losing `.github` / `.claude` / `.vscode` is the CASE-2008 shape
                    # aimed at the one tool whose entire job is to lose nothing.
                    #
                    # `.git` is matched EXACTLY for that same reason. The old clause
                    # was `not d.startswith(".git")`, which ALSO dropped `.github` and
                    # `.gitlab` -- a backup quietly losing the CI workflows it was
                    # taken to preserve, under ok:true. `.git` is already in SKIP_DIRS,
                    # so this clause is only a floor; making it exact costs nothing.
                    dirs[:] = [
                        d for d in dirs
                        if not name_matches_ignore(d.lower(), skip_dir_names)
                        and d.lower() != ".git"
                    ]
                    for fn in files:
                        if stop_backup.is_set():
                            raise InterruptedError("backup cancelled")
                        if fn in skip_file_names or fn.endswith((".lock", ".tmp", ".pyc")):
                            skipped.append(str(Path(walk_root, fn).relative_to(root)))
                            continue
                        fp = Path(walk_root) / fn
                        try:
                            if not fp.is_file():
                                continue
                            resolved_file = fp.resolve()
                            try:
                                resolved_file.relative_to(root)
                            except ValueError:
                                skipped.append(str(fp.relative_to(root)).replace("\\", "/"))
                                continue
                            if fp.is_symlink():
                                skipped.append(str(fp.relative_to(root)).replace("\\", "/"))
                                continue
                            arcname = str(fp.relative_to(root)).replace("\\", "/")
                            with open(fp, "rb") as source, zf.open(arcname, "w") as dest:
                                while chunk := source.read(1024 * 1024):
                                    if stop_backup.is_set():
                                        raise InterruptedError("backup cancelled")
                                    dest.write(chunk)
                            written += 1
                        except InterruptedError:
                            raise
                        except Exception:
                            try:
                                skipped.append(str(fp.relative_to(root)).replace("\\", "/"))
                            except Exception:
                                skipped.append(str(fp))
        except InterruptedError:
            try:
                Path(archive_path).unlink(missing_ok=True)
            except OSError:
                pass
            raise
        if written == 0:
            raise PermissionError(
                f"backup_project wrote 0 files (skipped={len(skipped)}); "
                f"check permissions under {root}"
            )

        created_at = datetime.now().isoformat(timespec="seconds")
        new_entry = {
            "created_at": created_at,
            "backup_path": archive_path,
            "files_written": written,
            "files_skipped": len(skipped),
            "backup_scope": "targeted" if targets is not None else "full",
            "target_paths": [str(path.relative_to(root)).replace("\\", "/") for path in (targets or [])],
            "skipped_sample": skipped[:20],
        }

        # Load existing manifest (danh sách)
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
            all_backups = old.get("backups", [])
        except Exception:
            all_backups = []

        # Thêm entry mới vào đầu (newest first)
        all_backups.insert(0, new_entry)

        # Keep the manifest bounded first.
        while len(all_backups) > keep_last_n:
            old_entry = all_backups.pop()
            try:
                old_path = Path(old_entry["backup_path"])
                if old_path.exists():
                    old_path.unlink()
            except Exception:
                pass

        # Older versions could leave archives outside the manifest. Bound the
        # actual backup directory too, otherwise those orphan ZIPs accumulate.
        archive_pattern = f"{root.name}_backup_*.zip"
        actual_archives = sorted(
            target_root.glob(archive_pattern),
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
        for stale_archive in actual_archives[keep_last_n:]:
            try:
                stale_archive.unlink()
            except OSError:
                pass
        all_backups = [
            entry for entry in all_backups
            if Path(entry.get("backup_path", "")).exists()
        ]

        manifest = {
            "project_path": str(root),
            "created_at": created_at,       # latest
            "backup_path": archive_path,    # latest (backward compat)
            "backups": all_backups,
        }
        _atomic_write_json(manifest_path, manifest, indent=2)
        return {
            "ok": True,
            "backup_path": archive_path,
            "created_at": created_at,
            "total_backups": len(all_backups),
            "files_written": written,
            "files_skipped": len(skipped),
            "skipped_sample": skipped[:10],
            "backup_scope": new_entry["backup_scope"],
            "target_paths": new_entry["target_paths"],
        }
    # Lane choice: targeted backups walk only their target list, so they are
    # small and short-lived. Routing them through the `backup` lane
    # (max_workers=1, max_queue=2) would stall them behind any in-flight FULL
    # repo walk. Send targeted backups to `read_io` (12 workers, larger queue)
    # so a targeted call returns in milliseconds even while a full backup is
    # running. Full backups stay on the dedicated `backup` lane.
    lane_name = "read_io" if normalized_targets is not None else "backup"
    worker_task = asyncio.create_task(run_in_executor_lane(lane_name, _backup))

    async def _wait_for_cleanup():
        try:
            await asyncio.wait_for(asyncio.shield(worker_task), timeout=15)
            return True
        except asyncio.TimeoutError:
            return False
        except asyncio.CancelledError:
            return worker_task.done()
        except (InterruptedError, Exception):
            return worker_task.done()

    try:
        return json.dumps(await asyncio.wait_for(asyncio.shield(worker_task), timeout=timeout), ensure_ascii=False)
    except asyncio.TimeoutError:
        # Hard-deadline path: signal the worker via the stop_backup Event.
        # The Event is checked between 1 MB chunks inside `_backup`, so a
        # cooperative worker raises InterruptedError promptly, exits the
        # `with zipfile.ZipFile(...)` block, and the except-InterruptedError
        # branch unlinks the partial archive before `_wait_for_cleanup`
        # returns. We deliberately do NOT call worker_task.cancel() here:
        # cancelling the wrapper task marks it done immediately (asyncio
        # wraps the Future with CancelledError) while the underlying worker
        # THREAD keeps running and would finish writing the archive in the
        # background, re-creating the very partial-file bug we are trying
        # to prevent. Event-driven cancellation + bounded cleanup wait is
        # the correct mechanism for a running thread; Future.cancel() only
        # helps when the work has not started yet.
        stop_backup.set()
        cleanup_complete = await _wait_for_cleanup()
        if not cleanup_complete:
            # Worker ignored the Event and is still unwinding (e.g. stuck in
            # a slow filesystem call). Best-effort sweep of any partial
            # archive it may have started so we never leave a corrupt .zip
            # behind. Only archives created within the timeout window are
            # removed -- never an older good backup.
            try:
                target_root = Path(backup_dir).resolve() if backup_dir else root.parent / "_orchestrator_backups"
                partial_pattern = f"{root.name}_backup_*.zip"
                partials = sorted(
                    target_root.glob(partial_pattern),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                if partials:
                    newest = partials[0]
                    age_seconds = time.time() - newest.stat().st_mtime
                    if age_seconds <= max(timeout, 1) + 30:
                        newest.unlink(missing_ok=True)
            except OSError:
                pass
        return _json_error(
            f"backup_project timed out after {timeout}s; partial archive cleanup "
            f"{'completed' if cleanup_complete else 'still pending'}",
            error_name="BACKUP_TIMEOUT",
            cleanup_complete=cleanup_complete,
            cleanup_pending=not cleanup_complete,
            timeout_seconds=timeout,
            ca_next_action="retry_with_larger_timeout_or_narrower_targets",
        )
    except asyncio.CancelledError:
        stop_backup.set()
        await _wait_for_cleanup()
        raise


def _cache_delta_sync(project_path: str, index_override: dict | None = None) -> str:
    root = Path(project_path).resolve()
    index = index_override if isinstance(index_override, dict) else _load_index(str(root))
    files = index.get("files", {})
    added = []
    modified = []
    deleted = []
    for rel, info in files.items():
        fp = root / rel
        if not fp.exists():
            deleted.append(rel)
            continue
        try:
            # Always hash: a restore/copy can preserve both mtime and size.
            current_hash = surgical_mapper.get_file_hash(str(fp))
            if current_hash != info.get("hash"):
                modified.append(rel)
        except Exception:
            modified.append(rel)
    indexed = set(files)
    for walk_root, dirs, walk_files in os.walk(root):
        dirs[:] = [d for d in dirs if not surgical_mapper.should_skip_dir(d, root)]
        for fn in walk_files:
            path = Path(walk_root) / fn
            # B022: pass `root`. `_skip_reason` needs it for `_depth_below_root`,
            # and the indexer already passes it (surgical_mapper.py:1134). Without
            # it the depth is None and R82's conservative default KEEPS the file --
            # correct for the indexer, inverted here, where keeping it means "new
            # file". Every deep data-JSON the indexer refuses was reported `added`
            # forever, so no refresh could ever clear it.
            if path.is_file() and surgical_mapper._should_scan_file(path, root):
                rel = str(path.relative_to(root)).replace("\\", "/")
                if rel not in indexed:
                    added.append(rel)
    return json.dumps({
        "ok": True,
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "total_changed": len(added) + len(modified) + len(deleted),
        "cache_updated_at": index.get("generated_at") or index.get("updated_at") or "",
    }, ensure_ascii=False)


def _forget_cache_delta_future(project_key: object, future: Future[str]) -> None:
    with _CACHE_DELTA_FUTURES_LOCK:
        if _CACHE_DELTA_FUTURES.get(project_key) is future:
            _CACHE_DELTA_FUTURES.pop(project_key, None)


async def cache_delta(project_path: str) -> str:
    """Compute live-INDEX drift off-loop and coalesce callers per project."""
    project_key = str(Path(project_path).resolve())
    with _CACHE_DELTA_FUTURES_LOCK:
        future = _CACHE_DELTA_FUTURES.get(project_key)
        created = future is None
        if created:
            future = _INDEX_IO_EXECUTOR.submit(_cache_delta_sync, project_key)
            _CACHE_DELTA_FUTURES[project_key] = future
    # Recorded 2026-09-17: registering the done-callback while still holding the lock wedges
    # the calling thread forever. concurrent.futures invokes add_done_callback INLINE when
    # the future has already finished, and _forget_cache_delta_future acquires this same
    # non-reentrant Lock -- a full-suite run froze here under load, on the daemon's own
    # write path via post_write_batch_verify. The dict entry is still published before the
    # lock is released, so the coalescing invariant is unchanged.
    if created:
        future.add_done_callback(
            lambda done, key=project_key: _forget_cache_delta_future(key, done)
        )
    return await asyncio.shield(asyncio.wrap_future(future))


async def _cache_delta_for_snapshot(
    project_path: str,
    snapshot_id: str,
    index: dict,
) -> str:
    """Compute drift against one pinned snapshot INDEX with snapshot-scoped singleflight."""
    project_key = str(Path(project_path).resolve())
    future_key = (project_key, snapshot_id)
    with _CACHE_DELTA_FUTURES_LOCK:
        future = _CACHE_DELTA_FUTURES.get(future_key)
        created = future is None
        if created:
            future = _INDEX_IO_EXECUTOR.submit(
                _cache_delta_sync,
                project_key,
                index,
            )
            _CACHE_DELTA_FUTURES[future_key] = future
    # Same self-deadlock as cache_delta: add_done_callback runs the callback INLINE on the
    # calling thread when the future is already done, and the callback acquires this very
    # Lock, which is not reentrant. Register it only after the lock is released.
    if created:
        future.add_done_callback(
            lambda done, key=future_key: _forget_cache_delta_future(key, done)
        )
    return await asyncio.shield(asyncio.wrap_future(future))


async def _index_drift(project_path: str) -> dict | None:
    """Return cache drift details, or None when the index is fresh/unavailable.

    V15 R8 C.4: when index was refreshed within _DRIFT_SUPPRESS_WINDOW_SEC, omit the
    drift block from caller responses to save ~0.3 KB / call and reduce noise.
    """
    if not project_path:
        return None
    if _is_index_recently_refreshed(project_path):
        return None
    try:
        delta = json.loads(await cache_delta(project_path))
    except Exception:
        return None
    if not isinstance(delta, dict) or not delta.get("ok"):
        return None
    if delta.get("added") or delta.get("modified") or delta.get("deleted"):
        return delta
    return None


async def resolve_project_path(input_path: str, preferred_root: str = "", mode: str = "auto") -> str:
    """Resolve user-provided file/folder path into stable project/file metadata."""
    return json.dumps(_resolve_project_path_payload(input_path, preferred_root, mode), ensure_ascii=False)


async def protected_tools_status() -> str:
    """Return token protection status without exposing token material."""
    protected = sorted(name for name in TOOL_MAP if name in PROTECTED_TOOL_NAMES)
    unprotected = sorted(name for name in TOOL_MAP if name not in PROTECTED_TOOL_NAMES)
    protected_by_phase: dict[str, list[str]] = {}
    for name in protected:
        protected_by_phase.setdefault(tool_phase(name), []).append(name)
    mutating_t4 = sorted(
        name for name in protected
        if tool_phase(name) == "T4" and name in {"open_browser_page", "click", "fill", "evaluate_js", "desktop_click", "desktop_app_focus"}
    )
    # CAN-016: report the TRUE origin/session-gated ("mutating") set = PROTECTED ∪
    # TOKEN_REQUIRED (mirrors orchestrator_server._MUTATING_TOOLS) + per-tool
    # mutation_scope risk labels. The legacy mutating_t4_tools_require_token field
    # below is kept for back-compat with existing UI/tests.
    mutating_tools = sorted(name for name in TOOL_MAP
                            if name in PROTECTED_TOOL_NAMES or name in TOKEN_REQUIRED_TOOLS)
    mutation_scopes = {name: tool_mutation_scope(name) for name in sorted(TOOL_MAP)
                       if tool_mutation_scope(name) != "none"}
    return json.dumps({
        "ok": True,
        "protected_tools": protected,
        "unprotected_tools": unprotected,
        "mutating_tools": mutating_tools,
        "mutation_scopes": mutation_scopes,
        "mutation_token_required": {name: name in TOKEN_REQUIRED_TOOLS for name in sorted(TOOL_MAP)},
        "token_required_tools": sorted(TOKEN_REQUIRED_TOOLS),
        "protected_by_phase": protected_by_phase,
        "protected_tiers": sorted(protected_by_phase),
        "mutating_t4_tools_require_token": mutating_t4,
        "enforcement_model": ("DECOUPLED: origin+session gate = mutating_tools "
                              "(PROTECTED_TOOL_NAMES + TOKEN_REQUIRED_TOOLS); X-Orchestrator-Token "
                              "gate = token_required_tools (destructive subset); mutation_scopes = "
                              "informational risk label (a tool may carry a scope without being in "
                              "the gate set, by design for some T4 tools); requires_session is "
                              "reported per-tool in GET /api/tools."),
        "token_reuse_policy": "fetch_once_reuse_for_session",
        "tier_label": {
            "T1": PHASE_LABELS["T1"],
            "T2": PHASE_LABELS["T2"],
            "T3": PHASE_LABELS["T3"],
            "T4": PHASE_LABELS.get("T4", "Runtime / DOM / Browser / Server lifecycle"),
        },
        "protection_source": "token=code_tools.TOKEN_REQUIRED_TOOLS; mutating/session=code_tools.PROTECTED_TOOL_NAMES (decoupled)",
        "last_updated": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False)


async def rule_drift_check(project_path: str = "", checks: str = "") -> str:
    """T1 — audit DRIFT between the RULE files (examples/CORE_RULE.txt, SLIM_RULE.txt,
    SLIM_RULE_INLINE.txt) and the live code (TOOL_MAP + enforcement sets + council
    config). Catches the "updated the rule but forgot the related code" (and vice
    versa) class of drift so a CA can fix the missing parts. Read-only, no mutation.

    `checks` = optional comma list to limit: tool_names,agent_list,behavior_sets
    (default = all). Output: {drift_count, by_severity, drifts:[{check,severity,detail,
    rule_loc?,code_value?}]}. tool_names = best-effort (backtick tokens); behavior_sets
    = deterministic; agent_list = council_config vs rule."""
    import re as _re

    from orchestrator.core.params import csv_param
    here = Path(__file__).resolve().parent
    # A34: `checks` arrives as a csv STRING from the schema, but a CA that sends a
    # JSON list (or the in-process caller that passes a real list) used to crash on
    # `.split(",")`. csv_param normalises str / json-string / list / tuple / set.
    sel = set(csv_param(checks, lower=True)) or {
        "tool_names", "agent_list", "behavior_sets", "constants"}
    drifts: list[dict] = []

    def _add(check, severity, detail, **kw):
        d = {"check": check, "severity": severity, "detail": detail}
        d.update(kw)
        drifts.append(d)

    # A39: this list used to be a literal HERE, which meant the write-side drift trigger
    # would have needed a second copy -- and a second copy goes stale the day a fourth
    # rule file appears, leaving the trigger silent on the very edit it exists to catch.
    rule_files = list(RULE_DRIFT_SOURCES)
    rule_text = {}
    for rf in rule_files:
        p = here / rf
        try:
            rule_text[rf] = p.read_text(encoding="utf-8") if p.exists() else ""
        except Exception:
            rule_text[rf] = ""
    tool_keys = set(TOOL_MAP.keys())
    mutating = set(PROTECTED_TOOL_NAMES) | set(TOKEN_REQUIRED_TOOLS)

    if "tool_names" in sel:
        # A tool reference is recognised by POSITION, never by exclusion. The old
        # contract flagged every backticked snake_case token and subtracted a
        # hand-written _NON_TOOL list, so every response field (`fields_unknown`),
        # param (`script_content`) and error name (`write_existing_file`) the rule
        # documents read as "missing tool": measured 2026-09-20 against
        # SLIM_RULE.txt -- 16 findings, 16 of them false, 0 true. That list could
        # only ever be grown by hand AFTER each false report. A rule file writes a
        # TOOL as a call `tool(...)` or as an /api/call/<tool> path and never
        # writes a response field that way, so position alone separates the two
        # classes with no name list left to go stale.
        registered = set(tool_keys)
        for _s in TOOL_SCHEMAS:
            _n = ((_s.get("function") or {}) if isinstance(_s, dict) else {}).get("name")
            if _n:
                registered.add(_n)
        # TOOL_MAP is NOT the whole surface: git_status, council_message and
        # restart_orchestrator_via_bat are live and in none of it, so comparing
        # against one registry reported REAL tools as missing. Union every live
        # registry instead of naming their members here.
        registered |= set(_MUTATION_SCOPE_MAP) | set(PROTECTED_TOOL_NAMES)
        try:
            from orchestrator.core.ca_policy_matrix import _TOOL_TO_CLASS as _T2C
            registered |= set(_T2C)
        except Exception:
            pass
        # The MCP host's own dispatcher/discovery tools have no daemon registry
        # row by construction -- they run in the bridge, not in the daemon.
        registered |= {"call_tool", "tool_names", "tool_schema"}
        for rf, txt in rule_text.items():
            seen = set()
            cands = [m.group(1) for m in _re.finditer(r"`([a-z][a-z0-9_]{3,})\(", txt)]
            cands += [m.group(1) for m in _re.finditer(r"/api/call/([a-z][a-z0-9_]{3,})", txt)]
            for tok in cands:
                if tok in seen or tok in registered or "_" not in tok:
                    continue
                seen.add(tok)
                _add("tool_names", "review",
                     f"rule {rf} calls `{tok}(...)` but no live registry knows that tool -- stale/renamed tool ref",
                     rule_loc=rf, code_value="absent")
            # A BARE mention is prose, with one exception: a name the registry
            # itself marks deprecated is a real stale ref the rule keeps teaching.
            for tok in sorted(_DEPRECATED_TOOLS):
                if tok in seen or not _re.search(r"`" + _re.escape(tok) + r"`", txt):
                    continue
                seen.add(tok)
                _add("tool_names", "review",
                     f"rule {rf} still teaches `{tok}`, which the registry marks DEPRECATED",
                     rule_loc=rf, code_value="deprecated")

    if "agent_list" in sel:
        cfg_agents = set()
        try:
            cfg = json.loads((here / ".council" / "council_config.json").read_text(encoding="utf-8-sig"))
            cfg_agents = set((cfg.get("agents") or {}).keys())
        except Exception as _e:
            _add("agent_list", "info", f"could not read council_config.json: {_e}")
        rule_agents = set()
        for txt in rule_text.values():
            for m in _re.finditer(r"\b(clau|codo|gro|pic|ope|hermes|kilo|cozo)\b", txt):
                rule_agents.add(m.group(1))
        if cfg_agents:
            for a in sorted(rule_agents - cfg_agents):
                _add("agent_list", "high", f"rule mentions agent '{a}' but council_config has no such agent",
                     code_value=sorted(cfg_agents))
            for a in sorted(cfg_agents - rule_agents):
                _add("agent_list", "info", f"council_config has agent '{a}' not referenced in rule files")

    if "behavior_sets" in sel:
        for name, sc in _MUTATION_SCOPE_MAP.items():
            if sc != "none" and name not in mutating:
                _add("behavior_sets", "info",
                     f"`{name}` has mutation_scope='{sc}' but is NOT in the origin/session gate set (PROTECTED+TOKEN_REQUIRED) — decoupled-by-design (CAN-012) or drift; verify intent",
                     code_value=sc)
            if name not in tool_keys:
                _add("behavior_sets", "medium", f"`{name}` has mutation_scope but is not a registered tool (phantom)")
        for name in TOKEN_REQUIRED_TOOLS:
            if name not in tool_keys:
                _add("behavior_sets", "high", f"`{name}` is TOKEN_REQUIRED but not a registered tool (phantom)")
        for name in PROTECTED_TOOL_NAMES:
            if name not in tool_keys:
                _add("behavior_sets", "high", f"`{name}` is PROTECTED but not a registered tool (phantom)")
        for name in sorted(mutating):
            if name not in tool_keys:
                continue
            if tool_mutation_scope(name) == "none":
                _add("behavior_sets", "medium", f"`{name}` is in the mutating gate set but has NO mutation_scope (CAN-011-class coverage gap)")
            if tool_phase(name) == "T1":
                _add("behavior_sets", "high", f"`{name}` is phase T1 (read-only) but in the mutating gate set — contradiction")

    if "constants" in sel:
        import re as _re2
        code_vals = {
            "MAX_READ_LINES": str(MAX_READ_LINES),
            "MAX_READ_LINES_HARD_CAP": str(MAX_READ_LINES_HARD_CAP),
            "MAX_SEARCH_RESULTS": str(MAX_SEARCH_RESULTS),
        }
        try:
            orch = (here / "orchestrator_server.py").read_text(encoding="utf-8")
            mp = _re2.search(r"\bPORT\s*=\s*int\([^)]*?[\"'](\d{3,5})[\"']", orch)
            if mp:
                code_vals["PORT"] = mp.group(1)
        except Exception:
            pass
        all_rule = "\n".join(rule_text.values())
        if "PORT" in code_vals:
            ports_in_rule = set(_re2.findall(r"localhost:(\d{3,5})", all_rule))
            # 8082 = documented backward-compat port for older launchers/tabs.
            stale_ports = ports_in_rule - {code_vals["PORT"], "8082"}
            if stale_ports:
                _add("constants", "high",
                     f"rule references localhost port(s) {sorted(stale_ports)} but code PORT={code_vals['PORT']} — stale rule value",
                     code_value=code_vals["PORT"])
        for label, val in code_vals.items():
            if label == "PORT":
                continue
            for m in _re2.finditer(_re2.escape(label) + r"[^0-9]{0,12}(\d{2,5})", all_rule):
                if m.group(1) != val:
                    _add("constants", "medium",
                         f"rule mentions {label}={m.group(1)} but code = {val}", code_value=val)

    by_sev: dict = {}
    for d in drifts:
        by_sev[d["severity"]] = by_sev.get(d["severity"], 0) + 1
    _drift_payload = {
        "ok": True,
        "checks_run": sorted(sel),
        "drift_count": len(drifts),
        "by_severity": by_sev,
        "drifts": drifts,
        "note": "T1 read-only. behavior_sets=deterministic; agent_list=config-vs-rule; tool_names(review)=best-effort backtick heuristic (verify each).",
    }
    # A39: the tool that FINDS the drift owes the fix. write_fix now sends a CA here
    # (rule_drift_trigger_reason), so the loop has to close on this side too -- otherwise
    # the CA arrives, reads drift_count=3, and is on its own again, which is the same
    # dead end the write-side trigger was built to remove.
    #
    # Gated on drift_count>0 by design: a clean rule set carries no hint key at all, so
    # the common path pays nothing. And the hint says RECONCILE, never "edit the doc":
    # each row already carries code_value, because the code is sometimes the side that
    # is wrong and a hint that always blames the doc would launder a real code bug.
    if drifts:
        _drift_payload["ca_hint"] = (
            "write_fix reconciles the %d drift row(s): each row carries code_value, so read "
            "it first -- the doc is not automatically the wrong side" % len(drifts))
        _drift_payload["ca_next_action"] = "write_fix"
    return json.dumps(_drift_payload, ensure_ascii=False)


async def path_quote_helper(project_path: str = "", subfolder: str = "", command: str = "", shell: str = "powershell") -> str:
    """Generate a safe Windows wrapper for run_project_command; never executes it."""
    return json.dumps(_path_quote_helper_payload(project_path, subfolder, command, shell), ensure_ascii=False)


async def optional_tool_probe(tools: list[str]) -> str:
    """Probe optional helpers/endpoints without executing them or reading token values."""
    if not isinstance(tools, list) or not tools:
        return _json_error(
            "tools must be a non-empty list",
            error_name="EMPTY_TOOL_LIST",
            ca_next_action="provide_tools_to_probe",
        )
    optional_endpoints = {
        # R16.9: tool_inspector is RETIRED — surface as deprecated synonym, not "available"
        "/api/tool_inspector": {
            "available": False,
            "deprecated": True,
            "replacement": "GET /api/tools (filterable: ?tier=T4, ?phase=T4, ?name_contains=)",
            "ca_next_action": "use_api_tools_filtered_instead",
        },
        "/api/tool_inspector/{tool}": {
            "available": False,
            "deprecated": True,
            "replacement": "GET /api/tools?name_contains=<tool>",
            "ca_next_action": "use_api_tools_filtered_instead",
        },
        "/api/command_helper": {
            "available": True,
            "replacement": "path_quote_helper",
            "ca_next_action": "use_command_helper_if_available_else_path_quote_helper",
        },
        "/api/token_panel": {
            "available": True,
            "replacement": "GET /api/token + protected_tools_status",
            "ca_next_action": "use_token_panel_if_available_else_token_endpoint",
        },
    }
    replacements = {
        "tool_inspector": "GET /api/tools?tier=T4&name_contains=...  (R16.9 retired this synonym)",
        "command_helper": "path_quote_helper",
        "token_panel": "GET /api/token + protected_tools_status",
        # R16.9: surface real names for recurring CA mistakes
        "click_selector": "click_selector EXISTS (R16.9) — or use canonical click",
        "read_skill": "read_skill EXISTS (R16.9, by short name)",
        "checkpoint_loop_dom_state": "checkpoint_loop_dom_state EXISTS (R16.9)",
        "ensure_single_electron_instance": "ensure_single_electron_instance EXISTS (R16.9)",
        "dom_screenshot_summary": "Compose screenshot_page(save_path=...) + query_selector(bbox) — base64 in JSON inflates token cost 99%",
    }
    available = []
    unavailable = []
    unsupported = []
    requires_token = []
    for raw in tools:
        name = str(raw or "").strip()
        if not name:
            unsupported.append({"name": name, "reason": "empty"})
            continue
        endpoint_key = name
        if name.startswith("/api/tool_inspector/"):
            endpoint_key = "/api/tool_inspector/{tool}"
        if endpoint_key in optional_endpoints:
            item = {"name": name, **optional_endpoints[endpoint_key]}
            available.append(item)
            continue
        tool_name = name.strip("/").split("/")[-1]
        if tool_name in TOOL_MAP:
            item = {
                "name": name,
                "tool_name": tool_name,
                "tier": tool_phase(tool_name),
                "replacement": "",
                "ca_next_action": "call_tool_with_schema_from_api_tools",
            }
            available.append(item)
            if tool_name in TOKEN_REQUIRED_TOOLS:
                requires_token.append(tool_name)
            continue
        if tool_name in replacements:
            unavailable.append({
                "name": name,
                "replacement": replacements[tool_name],
                "ca_next_action": "use_replacement_and_report_optional_unavailable",
            })
        else:
            unsupported.append({
                "name": name,
                "replacement": "GET /api/tools",
                "ca_next_action": "verify_tool_name_before_calling",
            })
    return json.dumps({
        "ok": True,
        "available": available,
        "unavailable": unavailable,
        "unsupported": unsupported,
        "requires_token": sorted(set(requires_token)),
        "replacement": {
            item["name"]: item.get("replacement", "")
            for item in [*available, *unavailable, *unsupported]
            if item.get("replacement")
        },
        "ca_next_action": "use_available_helpers_skip_optional_unavailable",
    }, ensure_ascii=False)

def _command_targets_git(command: list[str] | str) -> bool:
    """Return True when the launched command resolves to git, including shell wrappers."""
    import re
    if isinstance(command, list):
        tokens = [str(item).strip().lower() for item in command]
        while len(tokens) >= 2 and tokens[0] in {"cmd", "cmd.exe"} and tokens[1] in {"/c", "/s"}:
            tokens = tokens[2:]
        return bool(tokens and tokens[0] in {"git", "git.exe"})
    text = str(command or "").strip()
    return bool(re.match(r"(?i)^(?:cmd(?:\.exe)?\s+/(?:s\s+)?c\s+)?[\"']?git(?:\.exe)?[\"']?(?:\s|$)", text))


def _runner_environment(*, git_command: bool = False) -> dict[str, str] | None:
    if not git_command:
        return None
    child_env = os.environ.copy()
    child_env["GIT_PAGER"] = "cat"
    return child_env


def _kill_process_tree(proc, *, is_windows: bool) -> dict:
    """Terminate a subprocess and every descendant, returning auditable evidence."""
    evidence = {"attempted": True, "pid": proc.pid, "method": "taskkill" if is_windows else "killpg"}
    try:
        if is_windows:
            result = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, text=True, timeout=10)
            evidence.update({"succeeded": result.returncode == 0, "returncode": result.returncode, "stdout": result.stdout or "", "stderr": result.stderr or ""})
            if result.returncode != 0 and proc.poll() is None:
                proc.kill()
                evidence["fallback"] = "proc.kill"
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            evidence["succeeded"] = True
    except Exception as exc:
        evidence.update({"succeeded": False, "error": str(exc)})
        try:
            if proc.poll() is None:
                proc.kill()
                evidence["fallback"] = "proc.kill"
        except Exception as fallback_exc:
            evidence["fallback_error"] = str(fallback_exc)
    return evidence


# #29: how long stdout/stderr may stay open once the process itself is gone. A
# descendant that inherited the pipes (Start-Process -Redirect*, a server the
# script launched) holds EOF for as long as it lives, so waiting on EOF -- what
# communicate() does -- blocked a 10 s script for its whole timeout and then
# without a bound, and taskkill /T on the already-dead child missed the holder.
_PIPE_DRAIN_GRACE_S = 2.0


def _pipe_reader(stream, sink: list) -> None:
    """Append chunks as they arrive, so what was read so far is never lost."""
    raw = getattr(stream, "buffer", stream)
    read = getattr(raw, "read1", None) or raw.read
    try:
        while True:
            chunk = read(65536)
            if not chunk:
                break
            sink.append(chunk)
    except (OSError, ValueError):
        pass
    finally:
        # Only this thread may close it: close() takes the lock a blocked read holds.
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _pipe_text(stream, sink: list):
    data = b"".join(list(sink))
    if not hasattr(stream, "encoding"):
        return data
    # The decode Popen(text=True) does: the stream's codec, then universal newlines.
    text = data.decode(stream.encoding or "utf-8", stream.errors or "strict")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _communicate_with_tree_timeout(proc, timeout: int, *, is_windows: bool,
                                   drain_grace: float = _PIPE_DRAIN_GRACE_S):
    """Wait on the PROCESS, not on pipe EOF: (stdout, stderr, timed_out, evidence).

    evidence is None on a clean run; it carries the tree kill of a timeout, and
    pipe_held_after_exit when a descendant still held the pipes at return. A
    descendant is never killed on a normal exit -- it may be a server on purpose.
    """
    pipes = [(s, []) for s in (proc.stdout, proc.stderr)]
    readers = []
    for stream, sink in pipes:
        if stream is not None:
            reader = threading.Thread(target=_pipe_reader, args=(stream, sink), daemon=True)
            reader.start()
            readers.append(reader)
    evidence = {}
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        # The child is still alive here, so /T still reaches every descendant.
        evidence["tree_kill"] = _kill_process_tree(proc, is_windows=is_windows)
        try:
            proc.wait(timeout=drain_grace)
        except subprocess.TimeoutExpired:
            evidence["still_running_after_kill"] = True
    deadline = time.monotonic() + drain_grace
    for reader in readers:
        reader.join(max(0.0, deadline - time.monotonic()))
    if any(r.is_alive() for r in readers) and "still_running_after_kill" not in evidence:
        evidence.update({
            "pipe_held_after_exit": True,
            "drain_grace_s": drain_grace,
            "note": ("a process the script started still holds stdout/stderr; "
                     "anything it writes after this answer is not in it"),
        })
    out, err = (_pipe_text(s, sink) if s is not None else "" for s, sink in pipes)
    return out or "", err or "", timed_out, evidence or None


async def run_project_command(
    project_path: str,
    command: list[str] | str,
    timeout: int = 120,
    timeout_ms: int = 0,
    shell_mode: str = "auto",
    command_encoding: str = "plain",
    expected_port: int = 0,
    expected_process_name: str = "",
    verify_after_launch_ms: int = 0,
    readiness_protocol: str = "",
    health_url: str = "",
    expect_status: int = 200,
    keep_alive: bool = False,
    background: bool = False,
    interactive: bool = False,
    elevated: bool = False,
    include_command: bool = True,
    stdout_max_chars: int = 0,
    stderr_max_chars: int = 0,
    dry_run: bool = False,
    confirm_destructive: int = -1,
    skip_backup: bool = False,
    _executor_lane: str = "command",
) -> str:
    """V15 R12: shell_mode={auto|cmd|powershell|exec|posix}, command_encoding={plain|base64}.

    - auto (default): Windows → cmd; non-Windows → exec (POSIX argv via shlex).
    - cmd: wraps command as `cmd /c "<raw>"` with auto-escape (% → %%, plus ^,&,|,(,)
      outside quotes). .bat/.cmd targets also routed here.
    - powershell: `powershell -NoProfile -Command "<raw>"` (or `-File` for *.ps1).
    - exec: shlex.split(posix=False) on Windows, posix=True elsewhere — DEPRECATED legacy.
    - posix: shlex.split(posix=True) — explicit Unix mode.

    R43 P0 #4 (token-cost reduction):
    - include_command (default True): when False, suppress full echo of
      command_received / command / command_executed; replaced with command_hash_sha256
      + command_preview (first 50 chars). Saves ~500-2000 bytes/call for long
      `node -e` / base64 scripts. ROI: ~150KB saved per 50-call round.
    - stdout_max_chars / stderr_max_chars (default 0 → use existing 12000 cap):
      explicit override to truncate stdout/stderr more aggressively.
    """
    # V15 R66: accept timeout_ms (CA habit from T4 tools) and convert to seconds.
    # Honors CA intent instead of returning UNSUPPORTED_FIELD; takes precedence over timeout.
    if timeout_ms and isinstance(timeout_ms, (int, float)) and timeout_ms > 0:
        timeout = max(1, int(round(timeout_ms / 1000.0)))
    # V15 R34: defensive `/` → `\` for direct Python callers (API boundary already normalizes)
    if isinstance(project_path, str) and "/" in project_path and len(project_path) >= 2 and project_path[1] == ":":
        project_path = project_path.replace("/", "\\")
    protocol = str(readiness_protocol or "").lower().strip()
    valid_protocols = {"", "process", "http", "tcp", "cdp"}
    if protocol not in valid_protocols:
        return _json_error(
            f"Unsupported readiness protocol: {readiness_protocol}",
            error_name="INVALID_READINESS_PROTOCOL",
            ca_next_action="use_process_http_tcp_or_cdp",
        )
    if protocol == "http" and not health_url:
        return _json_error(
            "readiness_protocol='http' requires health_url",
            error_name="READINESS_INPUT_REQUIRED",
            ca_next_action="provide_health_url",
        )
    if protocol in {"tcp", "cdp"} and not expected_port:
        return _json_error(
            f"readiness_protocol='{protocol}' requires expected_port",
            error_name="READINESS_INPUT_REQUIRED",
            ca_next_action="provide_expected_port",
        )
    root = Path(project_path).resolve()
    if not root.exists():
        return _json_error(
            f"Project path not found: {project_path}",
            project_path=str(root),
            error_name="PROJECT_PATH_NOT_FOUND",
            ca_next_action="call_resolve_project_path",
        )
    # Decode command_encoding (only applies to string form)
    if isinstance(command, str) and command_encoding and command_encoding != "plain":
        _decoded_text, _info, _err = _decode_text_payload(command, command_encoding)
        if _err is not None:
            return json.dumps(_err, ensure_ascii=False)
        command = _decoded_text or ""
    import re as _re_rpc_temp
    import tempfile as _tempfile_rpc
    _temp_root = str(Path(_tempfile_rpc.gettempdir()).resolve())
    _temp_alias = _re_rpc_temp.compile(r"(?i)(%TEMP%|%TMP%|\$env:TEMP|\$\{TEMP\})")
    def _normalize_basetemp_text(text: str) -> str:
        pattern = _re_rpc_temp.compile(
            r"(?i)(--basetemp(?:=|\s+))(?P<quote>[\"']?)(?P<value>(?:%TEMP%|%TMP%|\$env:TEMP|\$\{TEMP\})(?:[/\\][^\s\"']*)?)(?P=quote)"
        )
        def replace(match):
            value = _temp_alias.sub(lambda _match: _temp_root, match.group("value"), count=1)
            return match.group(1) + match.group("quote") + value + match.group("quote")
        return pattern.sub(replace, text)
    if isinstance(command, str):
        command = _normalize_basetemp_text(command)
    elif isinstance(command, list):
        normalized_command = []
        previous_basetemp = False
        for item in command:
            text = str(item)
            if previous_basetemp:
                text = _temp_alias.sub(lambda _match: _temp_root, text, count=1)
            text = _normalize_basetemp_text(text)
            normalized_command.append(text)
            previous_basetemp = text == "--basetemp"
        command = normalized_command
    import shlex as _shlex_rpc
    import sys as _sys_rpc
    _is_win = _sys_rpc.platform == "win32"
    if background:
        keep_alive = True
    sm = (shell_mode or "auto").lower().strip()
    if sm not in {"auto", "cmd", "powershell", "exec", "posix"}:
        return _json_error(
            f"Unsupported shell_mode={sm!r}",
            error_name="UNSUPPORTED_FIELD",
            unsupported_fields=["shell_mode"],
            accepted_fields=["project_path", "command", "timeout", "shell_mode", "command_encoding", "expected_port", "expected_process_name", "verify_after_launch_ms", "keep_alive", "background", "interactive", "elevated", "include_command", "stdout_max_chars", "stderr_max_chars", "dry_run",
                            "confirm_destructive", "skip_backup"],
            accepted_shell_modes=["auto", "cmd", "powershell", "exec", "posix"],
        )
    raw_str = command if isinstance(command, str) else " ".join(str(x) for x in command)

    # ── CASE-2008 gate ──────────────────────────────────────────────────────
    # run_project_command is the OTHER way to delete a tree, and it was not
    # covered: `rd /s /q junk` returns ok with exit code 0 and really removes
    # the directory. Being token-gated as "arbitrary shell exec" bounds WHO can
    # call it, not what a mistaken call does -- and CASE-2008 was a mistaken
    # call by an authorised caller, so authorisation was never the missing part.
    #
    # Scanned on raw_str because it is the single text form of both accepted
    # shapes (a list is joined here), and placed AFTER the base64 decode above
    # so an encoded body is scanned as the command it becomes, not as its
    # envelope. Measured over all 34 real call sites in this repo: 0 flagged, so
    # the common path is unchanged.
    #
    # runtime="any" (union of every pattern set), NOT the resolved shell_mode.
    # shell_mode describes how the string is handed to the OS; it does not say
    # which interpreter the string invokes. `run_project_command` is a single
    # surface for cmd, PowerShell, `node -e` and `python -c` alike, so selecting
    # one runtime's patterns leaves the others unguarded. Measured before this
    # was written: naming "powershell" here missed fs.rmSync, fs.unlinkSync,
    # shutil.rmtree and os.remove -- four working ways to delete a tree through
    # the very tool that already had an incident.
    _cmd_scan = _scan_destructive_script(raw_str, "any")
    _cmd_refusal = _destructive_refusal(
        _cmd_scan, confirm_destructive=confirm_destructive, dry_run=dry_run,
        surface="Command",
    )
    if _cmd_refusal is not None:
        _cmd_refusal["project_path"] = str(root)
        return json.dumps(_cmd_refusal, ensure_ascii=False)

    _cmd_backup_info, _cmd_backup_refusal = await _destructive_backup_or_refusal(
        root, _cmd_scan, dry_run=dry_run, skip_backup=skip_backup,
    )
    if _cmd_backup_refusal is not None:
        return json.dumps(_cmd_backup_refusal, ensure_ascii=False)

    if dry_run:
        # Preview only, whatever the scan says. #37 (2026-09-25): this branch used
        # to be ``destructive and dry_run``, so a command the scan scored 0 fell
        # through and RAN under dry_run=true -- measured live, ``echo`` executed with
        # returncode 0 and no ``executed`` field. The scan is a lower bound (a
        # command built at run time, or a shape no row knows, scores 0), so letting
        # its verdict decide whether anything runs made every scan miss a real
        # write. The contract already said "Nothing runs"; run_project_test_script
        # has always returned here unconditionally. Static: a shell-expanded target
        # (a variable, a wildcard) is reported as written rather than resolved.
        return json.dumps({
            "ok": True,
            "executed": False,
            "dry_run": True,
            "project_path": str(root),
            "destructive": _cmd_scan["destructive"],
            "destructive_count": _cmd_scan["count"],
            "destructive_statements": _cmd_scan["findings"],
            # B028: what the here-string rule EXCLUDED from the count, so a caller
            # can see it was classified rather than silently dropped. The warning
            # already names this field; a warning pointing at an absent key is
            # worse than no warning.
            **({"prose_matches": _cmd_scan["prose_matches"]}
               if _cmd_scan.get("prose_matches") else {}),
            "warnings": _cmd_scan["warnings"],
            "preview_kind": "static_source_scan",
            "preview_limits": ("Static scan of the command text. Targets are NOT "
                               "expanded, so the real file set may differ, and a "
                               "command built at run time is not judged: treat the "
                               "count as a lower bound."),
            "confirm_destructive_required": _cmd_scan["count"],
            "ca_next_action": (
                "review_then_retry_with_confirm_destructive"
                if _cmd_scan["destructive"] else "rerun_without_dry_run"
            ),
        }, ensure_ascii=False)

    # A destructive command must never have its output truncated: the cap is what
    # hid the damage in CASE-2008 (68,978 chars produced; the lines naming the
    # files that were lost scrolled off the end).
    #
    # Carried as a FLAG, deliberately, rather than by clearing stdout_max_chars:
    # 0 does not mean "unlimited" everywhere in this function. The two auto-route
    # branches pass ``stdout_max_chars or 12000`` on to run_project_test_script,
    # so zeroing the field would silently RE-IMPOSE a 12000 cap -- the same class
    # of accident as the truncation being fixed. Only the final filter treats 0 as
    # off. Each site therefore reads the flag; nothing assigns to the caps. A
    # dry_run has already returned above, so this is reached only by a real run.
    _cmd_no_truncate = bool(_cmd_scan["destructive"])

    def _standalone_ps1_path(value: object) -> str:
        """Return a script path only when the whole command is that path.

        A command such as ``findstr pattern runner.ps1`` is a normal shell
        command, not a PowerShell ``-File`` invocation. The old suffix-only
        heuristic misrouted that valid read command whenever its final
        argument happened to be a ``.ps1`` file.
        """
        if not isinstance(value, str):
            return ""
        text = value.strip()
        if not text:
            return ""
        if text.startswith('"') and text.endswith('"') and text.count('"') == 2:
            text = text[1:-1].strip()
        elif any(ch.isspace() for ch in text) or any(ch in text for ch in "\r\n&|<>;"):
            return ""
        return text if text.lower().endswith(".ps1") else ""

    standalone_ps1_path = _standalone_ps1_path(command)
    # Resolve auto-mode
    resolved_mode = sm
    if sm == "auto":
        if _is_win:
            if isinstance(command, list):
                resolved_mode = "exec"
            # A standalone .ps1 target → PowerShell; a command whose final
            # argument is .ps1 remains cmd (safer default for Windows builtins).
            elif standalone_ps1_path or str(command or "").strip().lower() in {"powershell", "pwsh"}:
                resolved_mode = "powershell"
            else:
                resolved_mode = "cmd"
        else:
            resolved_mode = "exec"

    def _cmd_escape(s: str) -> str:
        """Auto-escape special cmd.exe characters outside double-quoted regions.
        Always doubles % (cmd variable expansion). Outside quotes, escapes ^,&,|,(,) with ^.
        Stream suppression (2>nul / >nul / 2>&1) is left unescaped so cmd actually
        suppresses the stream. Other > / < still get ^ so file redirects stay
        gated as REDIRECTION_NOT_EFFECTIVE instead of silently writing files.
        """
        import re as _re_esc
        _suppress = _re_esc.compile(r'(?:\d*>{1,2}\s*nul\b|\d*>&\d+)', _re_esc.IGNORECASE)
        out = []
        in_q = False
        index = 0
        while index < len(s):
            ch = s[index]
            if ch == '"':
                in_q = not in_q
                out.append(ch)
                index += 1
                continue
            if ch == '%':
                if index + 1 < len(s) and s[index + 1] == '%':
                    out.append('%%')
                    index += 2
                    continue
                out.append('%%')
                index += 1
                continue
            if not in_q:
                m = _suppress.match(s, index)
                if m:
                    out.append(m.group(0))
                    index = m.end()
                    continue
            if not in_q and ch in '^&|()<>':
                out.append('^' + ch)
            else:
                out.append(ch)
            index += 1
        return ''.join(out)

    # V15 R46 / R47 / R48 — PowerShell inline transport hard guard.
    # CA drift: long PS one-liners with continuation `>>`, here-strings,
    # multiline concatenation, heavy quote/backslash density, regex+JSON+UNC
    # combos, or non-ASCII (Vietnamese diacritic) content fail silently in
    # `powershell.exe -Command "<inline>"` because the console code page
    # re-encodes the argv string. Refuse with a structured error that
    # points CA to the script-file wrapper (run_project_test_script) which
    # writes the payload to disk in UTF-8 (BOM for PS 5.1) and invokes
    # `powershell -File <temp>` — Unicode-safe, no quote escaping needed.
    #
    # R47 — also detect inline PS when command arrived as a LIST. CA's
    # habit of passing ['powershell','-Command','<ugly body>'] previously
    # bypassed the str-only check and let bad payloads run silently.
    #
    # R48 — MODE-AGNOSTIC: previously gated on `resolved_mode == "powershell"`,
    # but Windows + list command + shell_mode='auto' (or 'exec') routes to
    # resolved_mode='exec' even when the list IS a `['powershell','-Command',
    # '<body>']` invocation. CA could pass shell_mode='exec' to silently
    # bypass the guard. Detect the PowerShell wrapper from `command` itself
    # — regardless of resolved_mode — so the guard catches every list-form
    # variant: shell_mode in {powershell, auto, exec, posix}.
    def _detect_ps_invocation(_cmd):
        """R48 — return (is_ps_invocation, inline_body, was_list).
        Detects: powershell.exe / pwsh.exe wrapper as first token of list,
        OR string command in resolved PS mode, OR cmd-wrapped
        `['cmd','/c','powershell','-Command',...]` double-wrapper drift."""
        if isinstance(_cmd, str):
            _s = _cmd.strip()
            _sl = _s.lower()
            # Either resolved_mode said PS, OR string explicitly invokes PS.
            if resolved_mode == "powershell" or _sl.startswith(("powershell", "pwsh")):
                return True, _s, False
            return False, "", False
        if isinstance(_cmd, list) and _cmd:
            _tokens = [str(x) for x in _cmd]
            _stripped = list(_tokens)
            # Peel off optional `cmd /c` double-wrapper first.
            if (len(_stripped) >= 2
                    and _stripped[0].lower() in ("cmd", "cmd.exe")
                    and _stripped[1].lower() == "/c"):
                _stripped = _stripped[2:]
            if not _stripped:
                return False, "", True
            # Now must start with powershell/pwsh.
            if _stripped[0].lower() not in (
                "powershell", "powershell.exe", "pwsh", "pwsh.exe"
            ):
                # Not a PS invocation at all (e.g. ['node','...']).
                # If resolved_mode happens to be 'powershell' for some other
                # reason, fall back to joined view for trigger detection.
                if resolved_mode == "powershell":
                    return True, " ".join(_tokens), True
                return False, "", True
            _stripped = _stripped[1:]
            # Strip well-known flags.
            while _stripped and _stripped[0].lower() in (
                "-noprofile", "-noni", "-noninteractive",
                "-executionpolicy", "bypass", "unrestricted",
                "remotesigned", "-nologo",
            ):
                _stripped = _stripped[1:]
            if _stripped and _stripped[0].lower() in ("-command", "-c"):
                return True, " ".join(_stripped[1:]), True
            # No -Command flag → assume remaining tokens ARE the inline body
            # (CA habit: ['powershell','Write-Host hi']).
            return True, " ".join(_stripped), True
        return False, "", False

    _ps_is_inv, _ps_inline_body, _ps_was_list = _detect_ps_invocation(command)
    if _is_win and _ps_is_inv:

        _ps_inline_triggers: list = []
        _ps_cmd_lc = _ps_inline_body.strip().lower().lstrip(".\\").lstrip("./")
        _ps_is_file_invocation = (
            _ps_cmd_lc.endswith(".ps1")
            or _ps_cmd_lc.startswith(".\\")
            or _ps_cmd_lc.startswith("./")
            or "-file" in _ps_cmd_lc
            or _ps_cmd_lc == ""  # empty after wrapper strip → file/exec, not inline body
        )
        if _ps_inline_body and not _ps_is_file_invocation:
            _ps_length = len(_ps_inline_body)
            if _ps_length > 800:
                _ps_inline_triggers.append(f"length={_ps_length}_exceeds_800")
            if ">>" in _ps_inline_body:
                _ps_inline_triggers.append("continuation_>>_present")
            if "@'" in _ps_inline_body or '@"' in _ps_inline_body:
                _ps_inline_triggers.append("here_string_present")
            if "\n" in _ps_inline_body or "\r" in _ps_inline_body:
                _ps_inline_triggers.append("multiline_present")
            _ps_quote_count = _ps_inline_body.count('"') + _ps_inline_body.count("'")
            if _ps_quote_count >= 10:
                _ps_inline_triggers.append(f"quote_density={_ps_quote_count}_geq_10")
            if any(ord(c) > 127 for c in _ps_inline_body):
                _ps_inline_triggers.append("non_ascii_console_codepage_lossy")
            _ps_has_regex = any(s in _ps_inline_body for s in (r"\b", r"\d", r"\w", r"\s"))
            _ps_has_json = "{" in _ps_inline_body and "}" in _ps_inline_body and ":" in _ps_inline_body
            _ps_has_unc = "\\\\" in _ps_inline_body
            if sum([_ps_has_regex, _ps_has_json, _ps_has_unc]) >= 2:
                _ps_inline_triggers.append("regex_json_unc_combo_present")
            # R47 — list-form invocation itself is a transport smell: even a
            # short list payload with PS-specific operators tends to mean CA
            # tried to bypass quote escaping. Add a soft signal only when
            # body uses risky chars.
            if _ps_was_list and any(c in _ps_inline_body for c in ('"', "'", ">", "<", "|", "&", "$", "@")):
                _ps_inline_triggers.append("list_form_with_shell_metachars")
            if _ps_inline_triggers:
                safe_body = ""
                if _ps_was_list and isinstance(command, list):
                    tokens = [str(item) for item in command]
                    flags = [i for i, item in enumerate(tokens) if item.lower() in ("-command", "-c")]
                    if len(flags) == 1 and len(tokens) == flags[0] + 2:
                        safe_body = tokens[flags[0] + 1]
                if safe_body:
                    routed = json.loads(await run_project_test_script(
                        str(root), safe_body, runtime="powershell", timeout=timeout,
                        script_label="auto_routed_powershell",
                        return_full_stdout=_cmd_no_truncate,
                        stdout_max_chars=(0 if _cmd_no_truncate
                                          else (stdout_max_chars or 12000)),
                        stderr_max_chars=(0 if _cmd_no_truncate
                                          else (stderr_max_chars or 12000)),
                        # The CASE-2008 gate above already ran, on the full
                        # command text, and already took the backup. Forward the
                        # acknowledgement so the routed call does not refuse an
                        # already-confirmed command, and skip the second backup
                        # rather than paying for the same snapshot twice.
                        #
                        # The inner gate still runs -- on safe_body, which is the
                        # narrower text. If its count differs from the count the
                        # caller acknowledged, it refuses. That is the correct
                        # direction: fail closed on a body that is not the one
                        # that was reviewed.
                        confirm_destructive=confirm_destructive,
                        skip_backup=True,
                    ))
                    routed.update({"auto_routed": True, "routed_from": "powershell_-Command",
                                   "transport": "script_file", "triggers": _ps_inline_triggers})
                    return json.dumps(routed, ensure_ascii=False)
                return _json_error(
                    f"PowerShell inline command blocked by transport policy "
                    f"(triggers: {', '.join(_ps_inline_triggers)})",
                    error_name="COMMAND_TRANSPORT_UNSAFE",
                    type="TRANSPORT_POLICY_VIOLATION",
                    severity="ERROR",
                    triggers=_ps_inline_triggers,
                    command_length=_ps_length,
                    command_was_list=_ps_was_list,  # R47 audit — list-form bypass detected
                    transport_policy={
                        "windows": {
                            "powershell_inline": "forbidden",
                            "preferred_transport": "script_file",
                            "rationale": (
                                "powershell.exe -Command re-encodes through console "
                                "code page (CP-1252 default), strips Vietnamese "
                                "diacritics, and breaks on >> / here-string / "
                                "heavy-quote patterns. Script-file route writes "
                                "UTF-8 BOM artifact and invokes via -File so the "
                                "interpreter reads source bytes directly."
                            ),
                        }
                    },
                    ca_next_action="use_run_project_test_script_wrapper",
                    preferred_tool="run_project_test_script",
                    one_shot_recipe={
                        "tool": "run_project_test_script",
                        "args": {
                            "project_path": str(root),
                            "runtime": "powershell",
                            "script_content": "<full multiline PS body here — UTF-8 safe>",
                            "script_label": "<short_identifier_for_audit>",
                            "timeout": timeout,
                        },
                        "notes": (
                            "[A] writes to .orchestrator_cache/test_scripts/<hash>.ps1 "
                            "(UTF-8 BOM for PS 5.1 compat), runs `powershell -NoProfile "
                            "-ExecutionPolicy Bypass -File <temp.ps1>`, captures stdout/"
                            "stderr, returns exit_code. Unicode preserved end-to-end. "
                            "No inline quote/backslash escaping needed."
                        ),
                    },
                    hint=(
                        "Inline PowerShell rejected. Move the body into "
                        "run_project_test_script(runtime='powershell', "
                        "script_content=...). For SIMPLE single-line PS without "
                        "non-ASCII / quote nesting, fewer than 800 chars, you may "
                        "retry — but prefer script_file for anything non-trivial."
                    ),
                )

    # V15 R49 — Node inline transport guard (mirror PS guard).
    # CA habit: `node -e "<long JS body with backslash + JSON.stringify + UNC>"`
    # fails silently on Windows because cmd /c reinterprets backslash + quote
    # nesting BEFORE node parses -e, losing escapes and breaking payloads.
    # Same fix: write JS to a .js artifact and invoke `node <temp.js>` via
    # run_project_test_script(runtime='node') — string literal stays intact.
    def _detect_node_invocation(_cmd):
        """R49 — return (is_node_inline, inline_body, was_list, flag)."""
        if isinstance(_cmd, str):
            s = _cmd.strip()
            sl = s.lower()
            for prefix in ("node -e ", "node --eval ", "node -p ", "node --print "):
                if sl.startswith(prefix):
                    flag = prefix.strip()
                    return True, s[len(prefix):], False, flag
            return False, "", False, ""
        if isinstance(_cmd, list) and _cmd:
            tokens = [str(x) for x in _cmd]
            stripped = list(tokens)
            if (len(stripped) >= 2
                    and stripped[0].lower() in ("cmd", "cmd.exe")
                    and stripped[1].lower() == "/c"):
                stripped = stripped[2:]
            if not stripped or stripped[0].lower() not in ("node", "node.exe"):
                return False, "", False, ""
            for i in range(1, len(stripped)):
                if stripped[i].lower() in ("-e", "--eval", "-p", "--print"):
                    if i + 1 < len(stripped):
                        return True, " ".join(stripped[i + 1:]), True, stripped[i].lower()
            return False, "", False, ""
        return False, "", False, ""

    _node_is_inv, _node_inline_body, _node_was_list, _node_flag = _detect_node_invocation(command)
    if _is_win and _node_is_inv:
        _node_triggers: list = []
        if _node_inline_body:
            _node_length = len(_node_inline_body)
            if _node_length > 800:
                _node_triggers.append(f"length={_node_length}_exceeds_800")
            if "\n" in _node_inline_body or "\r" in _node_inline_body:
                _node_triggers.append("multiline_present")
            _node_quote_count = (_node_inline_body.count('"')
                                  + _node_inline_body.count("'")
                                  + _node_inline_body.count('`'))
            if _node_quote_count >= 10:
                _node_triggers.append(f"quote_density={_node_quote_count}_geq_10")
            if any(ord(c) > 127 for c in _node_inline_body):
                _node_triggers.append("non_ascii_console_codepage_lossy")
            _node_has_regex = any(s in _node_inline_body for s in (r"\b", r"\d", r"\w", r"\s"))
            _node_has_json = "{" in _node_inline_body and "}" in _node_inline_body and ":" in _node_inline_body
            _node_has_unc = "\\\\" in _node_inline_body
            if sum([_node_has_regex, _node_has_json, _node_has_unc]) >= 2:
                _node_triggers.append("regex_json_unc_combo_present")
            if _node_was_list and any(c in _node_inline_body for c in ('"', "'", "`", ">", "<", "|", "&", "$")):
                _node_triggers.append("list_form_with_shell_metachars")
            if _node_triggers:
                safe_body = ""
                if _node_was_list and isinstance(command, list):
                    tokens = [str(item) for item in command]
                    flags = [i for i, item in enumerate(tokens) if item.lower() in ("-e", "--eval")]
                    if len(flags) == 1 and len(tokens) == flags[0] + 2:
                        safe_body = tokens[flags[0] + 1]
                if safe_body:
                    routed = json.loads(await run_project_test_script(
                        str(root), safe_body, runtime="node", timeout=timeout,
                        script_label="auto_routed_node",
                        return_full_stdout=_cmd_no_truncate,
                        stdout_max_chars=(0 if _cmd_no_truncate
                                          else (stdout_max_chars or 12000)),
                        stderr_max_chars=(0 if _cmd_no_truncate
                                          else (stderr_max_chars or 12000)),
                        # The CASE-2008 gate above already ran, on the full
                        # command text, and already took the backup. Forward the
                        # acknowledgement so the routed call does not refuse an
                        # already-confirmed command, and skip the second backup
                        # rather than paying for the same snapshot twice.
                        #
                        # The inner gate still runs -- on safe_body, which is the
                        # narrower text. If its count differs from the count the
                        # caller acknowledged, it refuses. That is the correct
                        # direction: fail closed on a body that is not the one
                        # that was reviewed.
                        confirm_destructive=confirm_destructive,
                        skip_backup=True,
                    ))
                    routed.update({"auto_routed": True, "routed_from": "node_-e",
                                   "transport": "script_file", "triggers": _node_triggers})
                    return json.dumps(routed, ensure_ascii=False)
                return _json_error(
                    f"Node inline command blocked by transport policy "
                    f"(triggers: {', '.join(_node_triggers)})",
                    error_name="SCRIPT_RUNTIME_REQUIRED",
                    type="TRANSPORT_POLICY_VIOLATION",
                    severity="ERROR",
                    triggers=_node_triggers,
                    command_length=_node_length,
                    command_was_list=_node_was_list,
                    node_flag=_node_flag,
                    transport_policy={
                        "windows": {
                            "node_inline": "forbidden",
                            "preferred_transport": "script_file",
                            "rationale": (
                                "cmd /c reinterprets backslash + quote nesting "
                                "BEFORE node parses -e, losing escapes and "
                                "breaking JSON.stringify / regex literals / UNC "
                                "path strings. Script-file route writes the "
                                "payload to a .js artifact and runs `node "
                                "<temp.js>` — JS source bytes pass through "
                                "unchanged."
                            ),
                        }
                    },
                    ca_next_action="use_run_project_test_script_wrapper",
                    preferred_tool="run_project_test_script",
                    one_shot_recipe={
                        "tool": "run_project_test_script",
                        "args": {
                            "project_path": str(root),
                            "runtime": "node",
                            "script_content": "<full multiline JS body — no escape headaches>",
                            "script_label": "<short_identifier>",
                            "timeout": timeout,
                        },
                        "notes": (
                            "[A] writes .orchestrator_cache/test_scripts/<hash>.js "
                            "(UTF-8) and runs `node <temp.js>`. CommonJS by default; "
                            "JSON.stringify / UNC paths / regex literals all safe."
                        ),
                    },
                    hint=(
                        "Inline `node -e` rejected. Move body into "
                        "run_project_test_script(runtime='node', script_content=...). "
                        "For SIMPLE one-liners (<800 chars, no newlines, no non-ASCII, "
                        "no regex+JSON+UNC combo) inline still works."
                    ),
                )

    # Build argv per resolved_mode
    use_shell = False
    if resolved_mode == "cmd":
        if isinstance(command, list):
            raw_str = " ".join(command)
        escaped = _cmd_escape(raw_str)
        args = ["cmd", "/c", escaped]
        shell_name = "cmd"
    elif resolved_mode == "powershell":
        if isinstance(command, str):
            if standalone_ps1_path:
                args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", standalone_ps1_path]
            else:
                args = ["powershell", "-NoProfile", "-Command", command]
        else:
            args = ["powershell", "-NoProfile", "-Command", " ".join(str(x) for x in command)]
        shell_name = "powershell"
    elif resolved_mode == "posix":
        if isinstance(command, str):
            args = _shlex_rpc.split(command, posix=True)
        else:
            args = list(command)
        shell_name = "exec"
    else:  # exec
        if isinstance(command, str):
            args = _shlex_rpc.split(command, posix=(not _is_win))
        else:
            args = list(command)
        shell_name = "exec"
    if not args:
        return _json_error("Missing command")
    # R57: cmd mode must launch as a single CreateProcess command STRING so
    # embedded quotes survive. The list form ["cmd","/c", escaped] goes through
    # subprocess.list2cmdline, which escapes the inner `"` of a quoted path as
    # `\"` — cmd then reads literal backslash-quotes and `"D:\dir with spaces\x.bat"`
    # fails ("is not recognized"). Passing `cmd /s /c "<escaped>"` as a raw string
    # bypasses list2cmdline (Windows hands it to CreateProcess verbatim); `/s`
    # makes cmd strip exactly the outer quote pair and run the rest unchanged.
    # args (the list) is preserved for the elevated branch and for reporting.
    launch_args = args
    if resolved_mode == "cmd" and isinstance(args, list):
        launch_args = 'cmd /s /c "' + escaped + '"'
    command_received = command if isinstance(command, list) else str(command)
    path_resolution_note = "project_path is used as cwd; command is not prepended with project_path"

    def _redirection_evidence() -> dict:
        import re as _re_rpc
        if not isinstance(raw_str, str) or ">" not in raw_str:
            return {"redirection_requested": False}
        # 2>nul / >nul / 2>&1 are stream suppression, not a log-file redirect.
        _nul_re = _re_rpc.compile(r"(?:\d*>{1,2}\s*nul\b|\d*>&\d+)", _re_rpc.IGNORECASE)
        stripped = _nul_re.sub(" ", raw_str)
        if ">" not in stripped:
            return {"redirection_requested": False, "nul_suppression": True}
        m = _re_rpc.search(r">+\s*(?:\"([^\"]+)\"|'([^']+)'|([^&|<>]+))", stripped)
        if not m:
            return {"redirection_requested": True, "redirection_effective": None, "log_path": "", "log_size": 0}
        log_raw = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if log_raw.lower() == "nul":
            return {"redirection_requested": False, "nul_suppression": True}
        log_path = log_raw if os.path.isabs(log_raw) else os.path.join(str(root), log_raw)
        exists = os.path.exists(log_path)
        size = os.path.getsize(log_path) if exists else 0
        return {
            "redirection_requested": True,
            "redirection_effective": bool(exists and size > 0),
            "log_path": log_path,
            "log_size": size,
        }

    def _apply_redirection_gate(payload: dict) -> dict:
        redir = _redirection_evidence()
        payload.update(redir)
        if redir.get("redirection_requested") and redir.get("redirection_effective") is False:
            payload["ok"] = False
            payload["error_name"] = "REDIRECTION_NOT_EFFECTIVE"
            payload["error"] = "Command requested shell redirection but the target log was not written. Use run_project_test_script or a script file wrapper."
            payload["ca_next_action"] = "use_run_project_test_script_or_script_file_wrapper"
        return payload

    # R43 P0 #4: helper to apply include_command + stdout/stderr cap to a payload dict
    def _r43_apply_command_filter(p: dict) -> dict:
        if not include_command:
            try:
                _src = command_received if isinstance(command_received, str) else " ".join(str(x) for x in command_received)
            except Exception:
                _src = ""
            import hashlib as _hl_rpc
            _h = _hl_rpc.sha256(_src.encode("utf-8", errors="replace")).hexdigest()[:16]
            _preview = _src[:50] + ("..." if len(_src) > 50 else "")
            p["command_hash_sha256"] = _h
            p["command_preview"] = _preview
            p["command_length"] = len(_src)
            p["command_suppressed"] = True
            for _k in ("command_received", "command", "command_executed"):
                p.pop(_k, None)
        # _cmd_no_truncate wins over any cap: a destructive run's output is the
        # only record of what it did. READ the flag here, never assign to
        # stdout_max_chars -- this is a nested function, so an assignment would
        # rebind the name as a local and every non-destructive call would raise
        # UnboundLocalError before reaching the cap it was meant to apply.
        if not _cmd_no_truncate and stdout_max_chars and isinstance(p.get("stdout"), str):
            p["stdout"] = cap_head_tail(p["stdout"], stdout_max_chars)[0]
        if not _cmd_no_truncate and stderr_max_chars and isinstance(p.get("stderr"), str):
            p["stderr"] = cap_head_tail(p["stderr"], stderr_max_chars)[0]
        # CASE-2008 — a destructive run says so in its OWN result, so the audit
        # trail does not depend on anyone having kept the refusal that preceded it,
        # and a backed-up run stays distinguishable from a skipped one afterwards.
        #
        # Stamped HERE, in the one filter every success path already funnels through
        # (four call sites), rather than at each return: the auto-route branches hand
        # back a payload built by run_project_test_script, and a per-return version
        # would have to be repeated and would be missed by whichever return is added
        # next.
        if _cmd_scan["destructive"]:
            p["destructive"] = True
            p["destructive_count"] = _cmd_scan["count"]
            p["destructive_statements"] = _cmd_scan["findings"]
            if _cmd_scan.get("prose_matches"):        # B028
                p["prose_matches"] = _cmd_scan["prose_matches"]
            p["confirm_destructive_acknowledged"] = confirm_destructive
            if _cmd_scan["warnings"]:
                p["warnings"] = _cmd_scan["warnings"]
            if _cmd_backup_info is not None:
                p["backup_info"] = _cmd_backup_info
            elif skip_backup:
                p["backup_info"] = {"ran": False, "ok": None,
                                    "reason": "skip_backup_requested"}
        return p

    def _readiness_proto() -> str:
        """CAN-053 readiness DSL: explicit protocol, else infer (http > cdp > tcp)."""
        if protocol:
            return protocol
        if health_url:
            return "http"
        if expected_port in (9222, 9223):
            return "cdp"
        if expected_port:
            return "tcp"
        return "process"

    def _verify_launch(spawned_pid: int) -> dict:
        import socket
        import time as _time
        deadline = _time.time() + (max(verify_after_launch_ms, 0) / 1000.0)
        if keep_alive and (expected_port or health_url):
            deadline = max(deadline, _time.time() + min(max(timeout, 1), 30))
        elif verify_after_launch_ms and verify_after_launch_ms > 0:
            _time.sleep(min(verify_after_launch_ms, 10000) / 1000.0)
        port_probe = None
        process_probe = None
        health_probe = None
        # CAN-053: HTTP health probe — an http-ready service must verify via
        # health_url + expect_status, not via GUI/port heuristics (Q_S.bat's
        # 232s false negative came from exactly that mismatch).
        if health_url:
            import urllib.request as _urlq
            _h_ok = False
            _h_status = None
            _h_err = ""
            while True:
                try:
                    with _urlq.urlopen(health_url, timeout=2) as _hr:
                        _h_status = int(getattr(_hr, "status", 200))
                        _h_ok = (_h_status == int(expect_status or 200))
                except Exception as _hx:
                    _h_err = str(_hx)[:160]
                if _h_ok or _time.time() >= deadline:
                    break
                _time.sleep(0.5)
            health_probe = {"ok": _h_ok, "url": health_url, "status": _h_status,
                            "expect_status": int(expect_status or 200), "error": _h_err}
        if expected_port:
            reachable = False
            owner_pid = 0
            owner_name = ""
            last_error = ""
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", int(expected_port)), timeout=1.0):
                        reachable = True
                except Exception as exc:
                    last_error = str(exc)
                    if _time.time() >= deadline:
                        break
                    _time.sleep(0.2)
                    continue
                break
            if reachable:
                port_probe = {"expected_port": expected_port, "reachable": True}
            else:
                port_probe = {"expected_port": expected_port, "reachable": False, "error": last_error}
            try:
                import psutil
                for conn in psutil.net_connections(kind="inet"):
                    if conn.laddr and conn.laddr.port == int(expected_port) and conn.status == psutil.CONN_LISTEN:
                        owner_pid = conn.pid or 0
                        if owner_pid:
                            try:
                                owner_name = psutil.Process(owner_pid).name()
                            except Exception:
                                owner_name = ""
                        break
                if port_probe is not None:
                    port_probe.update({"owner_pid": owner_pid, "owner_name": owner_name})
            except Exception as exc:
                if port_probe is not None:
                    port_probe["owner_lookup_error"] = str(exc)
        if expected_process_name:
            matches = []
            try:
                import psutil
                needle = expected_process_name.lower()
                for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                    name = proc.info.get("name") or ""
                    cmdline = " ".join(proc.info.get("cmdline") or [])
                    if needle in name.lower() or needle in cmdline.lower():
                        matches.append({"pid": proc.info.get("pid"), "name": name, "cmdline": cmdline[:300]})
                        if len(matches) >= 10:
                            break
                process_probe = {"expected_process_name": expected_process_name, "matches": matches, "match_count": len(matches)}
            except Exception as exc:
                process_probe = {"expected_process_name": expected_process_name, "matches": [], "match_count": 0, "error": str(exc)}
        selected_protocol = _readiness_proto()
        root_causes = []
        if selected_protocol == "http":
            launch_verified = bool((health_probe or {}).get("ok"))
            if not launch_verified:
                root_causes.append("HEALTH_PROBE_FAILED")
        elif selected_protocol in {"tcp", "cdp"}:
            launch_verified = bool((port_probe or {}).get("reachable"))
            if not launch_verified:
                root_causes.append("CDP_UNREACHABLE" if selected_protocol == "cdp" else "PORT_UNREACHABLE")
        else:
            if expected_process_name:
                launch_verified = bool((process_probe or {}).get("match_count"))
                if not launch_verified:
                    root_causes.append("PROCESS_NOT_FOUND")
            else:
                launch_verified = spawned_pid > 0
        return {
            "spawned_pid": spawned_pid,
            "launch_verified": launch_verified,
            "expected_port": expected_port or None,
            "expected_process_name": expected_process_name or "",
            "readiness_protocol": _readiness_proto(),
            "port_probe": port_probe,
            "process_probe": process_probe,
            "health_probe": health_probe,
            "root_causes": root_causes,
        }

    def _spawn_liveness_evidence(proc) -> dict:
        """ISS-14: prove the spawned process is still there, or say we cannot.

        The keep_alive branch used to skip verification ENTIRELY whenever no
        readiness target was given: it answered ok:true with launch_verified:null
        and a spawned_pid the OS may already have reclaimed, and it did not even
        honour verify_after_launch_ms. That is how start_app reported success for
        an app that died milliseconds after spawn -- the caller then aimed
        stop_app at a PID that no longer existed and never learned the launch had
        failed at all.

        A dead wrapper is NOT automatically a dead app on Windows: a .bat that
        detaches its own server exits 0 while the server lives. So only a
        NON-ZERO exit is reported as a failure. Exit 0 with no readiness target
        stays ok:true but says readiness_established:false and names the fields
        that would make it provable, instead of implying a verified launch.
        """
        import time as _time
        settle_ms = max(int(verify_after_launch_ms or 0), 0)
        if settle_ms <= 0:
            # A launch that fails on a bad path / missing interpreter dies
            # immediately; 300ms is far below process spawn cost and turns the
            # single most common failure from a false ok:true into evidence.
            settle_ms = 300
        settle_ms = min(settle_ms, 10000)
        _time.sleep(settle_ms / 1000.0)
        exit_code = proc.poll()
        evidence = {
            "spawned_pid": proc.pid,
            "launch_verified": None,
            "expected_port": None,
            "expected_process_name": "",
            "readiness_protocol": "process",
            "port_probe": None,
            "process_probe": None,
            "health_probe": None,
            "root_causes": [],
            "spawn_settle_ms": settle_ms,
            "pid_alive": exit_code is None,
            "wrapper_exit_code": exit_code,
            "readiness_established": False,
        }
        if exit_code is None:
            try:
                import psutil
                evidence["child_count"] = len(
                    psutil.Process(proc.pid).children(recursive=True))
            except Exception:
                pass
            evidence["readiness_hint"] = (
                "PID is alive but nothing proves the app is SERVING. Pass health_url "
                "(+expect_status), expected_port or expected_process_name for a real "
                "readiness verdict; stop_app needs include_children for this PID.")
            return evidence
        if exit_code != 0:
            evidence.update({
                "launch_verified": False,
                "root_causes": ["PROCESS_EXITED_AFTER_SPAWN"],
                "error_name": "PROCESS_EXITED_AFTER_SPAWN",
                "error": (f"The launched process exited with code {exit_code} within "
                          f"{settle_ms}ms of spawn; nothing is running."),
                "ca_next_action": "rerun_with_keep_alive_false_to_capture_stderr",
                "hint": ("stdout/stderr are discarded for keep_alive launches, so the "
                         "reason is not in this response. Rerun the same command with "
                         "keep_alive=false to capture it."),
            })
            return evidence
        evidence["readiness_hint"] = (
            f"The launcher wrapper exited 0 within {settle_ms}ms. That is normal for a "
            ".bat/script that detaches its own server, but it is NOT evidence the app is "
            "up, and this PID is gone so stop_app cannot reach the app through it. Pass "
            "health_url, expected_port or expected_process_name for a verified launch.")
        return evidence

    def _path_quoting_error_payload(stderr: str, stdout: str, returncode: int) -> dict | None:
        combined = f"{stderr or ''}\n{stdout or ''}".lower()
        if returncode == 0 or "is not recognized" not in combined:
            return None
        if not isinstance(command_received, str) or " " not in command_received or '"' in command_received:
            return None
        return {
            "error_name": "PATH_QUOTING_ERROR",
            "error": "Command appears to contain an unquoted path with spaces.",
            "suggestion": "wrap path in double quotes",
            "ca_next_action": "retry_with_quoted_path_or_path_quote_helper",
            "retry_example": f'cmd /c "{command_received}"',
            "suggested_helper": "path_quote_helper",
            "root_causes": ["PATH_WITH_SPACES_UNQUOTED"],
        }

    def _unquoted_absolute_script_path() -> str:
        """Return an unquoted absolute executable/script prefix, or empty.

        Deliberately narrow: ordinary commands with spaced arguments are valid;
        only a command that STARTS like an absolute Windows/UNC path and reaches a
        known executable/script extension after whitespace is rejected.
        """
        if not isinstance(command_received, str):
            return ""
        text = command_received.strip()
        if not text or text[0] in {'"', "'"}:
            return ""
        lower = text.lower()
        if not (re.match(r"^[a-z]:[\\/]", lower) or lower.startswith("\\\\")):
            return ""
        for ext in (".exe", ".bat", ".cmd", ".ps1"):
            end = lower.find(ext)
            if end >= 0:
                prefix = text[:end + len(ext)]
                return prefix if any(ch.isspace() for ch in prefix) else ""
        return ""

    def _run():
        start = time.time()
        try:
            unquoted_path = _unquoted_absolute_script_path()
            if unquoted_path:
                return _r43_apply_command_filter({
                    "ok": False, "project_path": str(root),
                    "command_received": command_received,
                    "command": args, "command_executed": [], "shell": shell_name,
                    "shell_mode_requested": sm, "shell_mode_resolved": resolved_mode,
                    "cwd": str(root), "returncode": None, "exit_code": None,
                    "duration_seconds": round(time.time() - start, 3),
                    "stdout": "", "stderr": "", "timed_out": False,
                    "keep_alive": bool(keep_alive), "background": bool(background),
                    "interactive": bool(interactive), "elevated": bool(elevated),
                    "spawn_attempted": False, "write_performed": False,
                    "error_name": "PATH_QUOTING_ERROR",
                    "error": "Command starts with an unquoted absolute executable/script path containing spaces.",
                    "unquoted_path": unquoted_path,
                    "suggestion": "wrap path in double quotes",
                    "ca_next_action": "retry_with_quoted_path_or_path_quote_helper",
                    "suggested_helper": "path_quote_helper",
                    "root_causes": ["PATH_WITH_SPACES_UNQUOTED"],
                })
            if elevated:
                if not _is_win:
                    return {
                        "ok": False,
                        "project_path": str(root),
                        "command_received": command_received,
                        "command": args,
                        "command_executed": args,
                        "shell": shell_name,
                        "error": "elevated launch is only supported on Windows",
                        "error_name": "UNSUPPORTED_FIELD",
                        "interactive": bool(interactive),
                        "elevated": True,
                    }
                import ctypes
                params = ""
                executable = args[0]
                if len(args) > 1:
                    params = " ".join(str(x) for x in args[1:])
                rc = ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", executable, params, str(root), 1
                )
                ok = int(rc) > 32
                return _r43_apply_command_filter({
                    "ok": ok,
                    "project_path": str(root),
                    "command_received": command_received,
                    "command": args,
                    "command_executed": args,
                    "shell": shell_name,
                    "shell_mode_requested": sm,
                    "shell_mode_resolved": resolved_mode,
                    "cwd": str(root),
                    "returncode": None,
                    "exit_code": None,
                    "duration_seconds": round(time.time() - start, 3),
                    "stdout": "",
                    "stderr": "",
                    "timed_out": False,
                    "interactive": True,
                    "elevated": True,
                    "launch_requested": ok,
                    "shell_execute_result": int(rc),
                    "launch_verified": None,
                    "path_resolution_note": "elevated GUI launch uses ShellExecuteW runas; no stdout/stderr capture or PID is available.",
                    **({} if ok else {"error": "ShellExecuteW elevation launch failed", "error_name": "ELEVATED_LAUNCH_FAILED"}),
                })
            creationflags = 0
            stdout_target = subprocess.PIPE
            stderr_target = subprocess.PIPE
            stdin_target = None
            close_fds = False
            # C2-followup (2026-08-10): commit 4e4e8bf added
            # `start_new_session=start_new_session` to this Popen call but
            # never introduced the local variable, so every run_project_command
            # invocation raised NameError at this line and the command runner
            # was unusable. It also claimed CREATE_NEW_PROCESS_GROUP on
            # Windows (so taskkill /T can reach the tree on timeout) but the
            # flag was never set. Define both here:
            #   - POSIX: start_new_session=True (setsid) so
            #     _kill_process_tree's os.killpg(SIGKILL) reaches the group.
            #   - Windows: start_new_session stays False (not meaningful);
            #     the process group boundary is CREATE_NEW_PROCESS_GROUP in
            #     creationflags below.
            start_new_session = (os.name != "nt")
            if _is_win:
                creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if keep_alive:
                stdin_target = subprocess.DEVNULL
                stdout_target = subprocess.DEVNULL
                stderr_target = subprocess.DEVNULL
                close_fds = True
                if _is_win:
                    if interactive:
                        creationflags |= getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                    else:
                        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
            child_env = _runner_environment(git_command=_command_targets_git(command))
            proc = subprocess.Popen(
                launch_args, cwd=str(root), stdin=stdin_target,
                stdout=stdout_target, stderr=stderr_target, text=not keep_alive,
                shell=use_shell, creationflags=creationflags, close_fds=close_fds,
                start_new_session=start_new_session, env=child_env,
            )
            if keep_alive:
                _register_background_process(proc)
                stdout, stderr = "", ""
                timed_out = False
                # ISS-14: with no readiness target this used to return a static
                # stub -- no wait, no liveness check, ok:true. See
                # _spawn_liveness_evidence for why that made a dead app
                # indistinguishable from a running one.
                launch_evidence = (_verify_launch(proc.pid)
                                   if (protocol or expected_port or expected_process_name
                                       or health_url)
                                   else _spawn_liveness_evidence(proc))
                ok = True
                if (expected_port or expected_process_name or health_url) and not launch_evidence["launch_verified"]:
                    ok = False
                elif launch_evidence.get("error_name") == "PROCESS_EXITED_AFTER_SPAWN":
                    ok = False
                payload = {
                    "ok": ok,
                    "project_path": str(root),
                    "command_received": command_received,
                    "command": args,
                    "command_executed": args,
                    "shell": shell_name,
                    "shell_mode_requested": sm,
                    "shell_mode_resolved": resolved_mode,
                    "cwd": str(root),
                    "returncode": None,
                    "exit_code": None,
                    "duration_seconds": round(time.time() - start, 3),
                    "stdout": "",
                    "stderr": "",
                    "timed_out": False,
                    "keep_alive": True,
                    "background": bool(background),
                    "interactive": bool(interactive),
                    "elevated": False,
                    "detached": bool(_is_win and not interactive),
                    "path_resolution_note": path_resolution_note,
                }
                payload = _apply_redirection_gate(payload)
                payload.update(launch_evidence)
                if (expected_port or expected_process_name or health_url) and not launch_evidence["launch_verified"]:
                    _proto = _readiness_proto()
                    payload["error_name"] = ("CDP_UNREACHABLE" if _proto == "cdp"
                                              else "HEALTH_PROBE_FAILED" if _proto == "http"
                                              else "PORT_UNREACHABLE" if expected_port
                                              else "LAUNCH_VERIFICATION_FAILED")
                    payload["error"] = "Background process spawned but expected readiness target was not verified."
                    payload["readiness_protocol"] = _proto
                return _r43_apply_command_filter(payload)
            stdout, stderr, timed_out, proc_evidence = _communicate_with_tree_timeout(
                proc, timeout, is_windows=_is_win,
            )
            launch_evidence = _verify_launch(proc.pid) if (expected_port or expected_process_name or health_url) else {
                "spawned_pid": proc.pid,
                "launch_verified": None,
                "expected_port": None,
                "expected_process_name": "",
                "port_probe": None,
                "process_probe": None,
                "root_causes": [],
            }
            ok = proc.returncode == 0 and not timed_out
            if (expected_port or expected_process_name) and not launch_evidence["launch_verified"]:
                ok = False
            payload = {
                "ok": ok,
                "project_path": str(root),
                "command_received": command_received,
                "command": args,
                "command_executed": args,
                "shell": shell_name,
                "shell_mode_requested": sm,
                "shell_mode_resolved": resolved_mode,
                "cwd": str(root),
                "returncode": proc.returncode,
                "exit_code": proc.returncode,
                "duration_seconds": round(time.time() - start, 3),
                "stdout": _truncate_output(stdout),
                "stderr": _truncate_output(stderr),
                "timed_out": timed_out,
                **({"process_evidence": proc_evidence} if proc_evidence else {}),
                "background": False,
                "interactive": bool(interactive),
                "elevated": False,
                "path_resolution_note": path_resolution_note,
            }
            payload = _apply_redirection_gate(payload)
            payload.update(launch_evidence)
            path_quote_error = _path_quoting_error_payload(stderr, stdout, proc.returncode)
            if path_quote_error:
                payload.update(path_quote_error)
                payload["ok"] = False
            if (expected_port or expected_process_name or health_url) and not launch_evidence["launch_verified"]:
                _proto = _readiness_proto()
                payload["error_name"] = ("CDP_UNREACHABLE" if _proto == "cdp"
                                          else "HEALTH_PROBE_FAILED" if _proto == "http"
                                          else "PORT_UNREACHABLE" if expected_port
                                          else "LAUNCH_VERIFICATION_FAILED")
                payload["error"] = "Command wrapper exited but expected readiness target was not verified."
                payload["readiness_protocol"] = _proto
            elif (health_url and (launch_evidence.get("health_probe") or {}).get("ok")
                    and payload.get("ok") is False and not timed_out):
                # CAN-053 ok_with_warning: launcher wrapper exited rc!=0 but the
                # health probe confirms the service is actually up.
                payload["ok"] = True
                payload["ok_with_warning"] = True
                payload["warning"] = "Launcher exited rc!=0 but health probe passed - service is up."
            return _r43_apply_command_filter(payload)
        except Exception as exc:
            return _r43_apply_command_filter({
                "ok": False,
                "project_path": str(root),
                "command_received": command_received,
                "command": args,
                "command_executed": args,
                "shell": shell_name,
                "cwd": str(root),
                "error": str(exc),
                "hint": "If the failure is caused by a path with spaces, retry via: powershell -NoProfile -Command \"Set-Location '2. Rawcut'; node health-check.js\"",
                "ca_next_action": "retry_with_powershell_set_location_wrapper",
                "retry_example": "powershell -NoProfile -Command \"Set-Location -LiteralPath '2. Rawcut'; node health-check.js\"",
                "suggested_helper": "path_quote_helper",
                "spawned_pid": 0,
                "launch_verified": False if (expected_port or expected_process_name) else None,
                "expected_port": expected_port or None,
                "expected_process_name": expected_process_name or "",
                "root_causes": ["COMMAND_RUNNER_ERROR"],
                "exit_code": None,
                "timed_out": "timed out" in str(exc).lower(),
                "interactive": bool(interactive),
                "elevated": bool(elevated),
                "path_resolution_note": path_resolution_note,
            })

    result = await run_in_executor_lane(_executor_lane, _run)
    # CSLt 2026-07-16 (upgrade #3): one bounded retry for TRANSIENT SPAWN failures only.
    # COMMAND_RUNNER_ERROR + exit_code None + WinError 32/33 means the OS blocked the
    # launch (file lock / sharing violation) and the command never actually ran, so a
    # retry is safe even for mutating commands. Failures where the command DID run
    # (non-zero exit, timeout) are never retried here.
    if isinstance(result, dict) and not result.get("ok") and result.get("exit_code") is None \
            and "COMMAND_RUNNER_ERROR" in (result.get("root_causes") or []):
        _err_txt = str(result.get("error", ""))
        if ("WinError 32" in _err_txt or "WinError 33" in _err_txt
                or "being used by another process" in _err_txt):
            await asyncio.sleep(0.5)
            retry_result = await run_in_executor_lane(_executor_lane, _run)
            if isinstance(retry_result, dict):
                retry_result["retried"] = True
                retry_result["retry_reason"] = "transient_spawn_lock"
            result = retry_result
    return json.dumps(result, ensure_ascii=False)


# F401 FALSE POSITIVE: 'kill_process' is a TOOL handler that BASE_TOOL_MAP_SPECS
# resolves as a string expr eval'd against these globals -- deleting it kills the
# import of this module, not just the tool.
from orchestrator.session.process import kill_process  # noqa: F401


def _fresh_basetemp() -> str:
    """F4: create a fresh per-run temp dir under the system TEMP for pytest --basetemp.

    Avoids PermissionError from stale/locked __pycache__ or temp artifacts
    inside the project tree. Each invocation gets its own subdir; OS cleans
    %TEMP% on reboot.
    """
    import tempfile as _tf
    import uuid as _uuid
    d = Path(_tf.gettempdir()) / f"orch_pytest_{_uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


async def run_test_profile(project_path: str, profile: str = "unittest", timeout: int = 120) -> str:
    # V15 R34: defensive path normalize
    if isinstance(project_path, str) and "/" in project_path and len(project_path) >= 2 and project_path[1] == ":":
        project_path = project_path.replace("/", "\\")
    root = Path(project_path).resolve()

    # Resolve core source dir from the live Orchestrator layout when the caller points at the app root.
    core_root = root
    if profile in {"unittest", "pytest", "py_compile_core"} and not (core_root / "code_tools.py").exists():
        embedded_root = path_runtime.find_orchestrator_dir(root)
        if embedded_root is not None:
            core_root = embedded_root
        else:
            core_root = path_runtime.ORCHESTRATOR_DIR

    selected_python = await run_in_executor_lane(
        "test", _select_python_interpreter, core_root, "", ["pytest"] if profile == "pytest" else [],
    )
    if not selected_python.get("ok"):
        selected_python["profile"] = profile
        return json.dumps(selected_python, ensure_ascii=False)
    _PY = selected_python["executable"]

    # unittest: auto-discover test directory (fixes "Ran 0 tests" from bare `python -m unittest`)
    if profile == "unittest":
        def _detect_unittest_cmd():
            for candidate in ["tests", "test"]:
                d = core_root / candidate
                if d.is_dir() and list(d.glob("test_*.py")):
                    return [_PY, "-m", "unittest", "discover",
                            "-s", candidate, "-p", "test_*.py", "-v"]
            return [_PY, "-m", "unittest", "discover", "-v"]
        import asyncio as _aio
        command = await _aio.to_thread(_detect_unittest_cmd)
        return await run_project_command(str(core_root), command, timeout=timeout)

    # npm_test: validate environment before attempting
    if profile == "npm_test":
        if not (core_root / "package.json").exists():
            return _json_error(
                "npm_test unavailable: no package.json in project root",
                error_name="NO_TESTS_FOUND",
                type="TEST_WARNING",
                severity="warning",
                retryable=False,
                reason="no package.json found for npm_test",
                ca_next_action="inspect_project_test_config_or_skip_with_reason",
                accepted_fields=["project_path", "profile", "timeout"],
                payload_example={
                    "project_path": "D:\\path\\to\\project",
                    "profile": "npm_test",
                    "timeout": 120,
                },
                available=SUPPORTED_TEST_PROFILES,
                profile=profile,
            )
        if shutil.which("npm") is None:
            return _json_error(
                "npm is not installed or not in PATH",
                error_name="RUN_TEST_PROFILE_INVALID",
                type="VALIDATION_ERROR",
                severity="error",
                retryable=False,
                reason="unsupported test profile environment: npm is not installed",
                ca_next_action="choose_supported_profile_from_schema",
                accepted_fields=["project_path", "profile", "timeout"],
                payload_example={
                    "project_path": "D:\\path\\to\\project",
                    "profile": "npm_test",
                    "timeout": 120,
                },
                available=SUPPORTED_TEST_PROFILES,
                profile=profile,
            )

    core_py_candidates = [
        "code_tools.py", "surgical_mapper.py", "orchestrator_server.py",
        "orchestrator_ui.py", "mcp_client_manager.py", "magika_scan.py",
        "verify_fix.py",
    ]
    core_py = [name for name in core_py_candidates if (path_runtime.ORCHESTRATOR_DIR / name).exists()]
    if profile == "py_compile":
        embedded_root = path_runtime.find_orchestrator_dir(root)
        embedded_prefix = ""
        if embedded_root and embedded_root != root:
            try:
                embedded_prefix = embedded_root.relative_to(root).as_posix() + "/"
            except ValueError:
                embedded_prefix = ""

        def _project_python_files():
            py_files: list[str] = []
            index = _load_index(str(root))
            for rel, info in (index.get("files", {}) or {}).items():
                rel_norm = rel.replace("\\", "/")
                if embedded_prefix and rel_norm.startswith(embedded_prefix):
                    continue
                if str(rel).lower().endswith(".py") and info.get("group") in {"source", "test", "script", "entrypoint", None}:
                    py_files.append(str(rel).replace("/", os.sep))
            if not py_files:
                for fp in root.rglob("*.py"):
                    # B027j 2026-09-22: was `any(part in SKIP_DIRS for part in fp.parts)`,
                    # wrong in two independent ways. It ignored `.orchestrator_ignore`
                    # and the global glob layer, and -- because `fp.parts` is ABSOLUTE --
                    # a project living under a parent folder named `build`, `archive` or
                    # `logs` matched on that PARENT and compiled NOTHING, reporting a
                    # clean py_compile over zero files. Relative parts fix the second.
                    # The file gate is the right gate: the walk is already finished and
                    # we are filtering paths, not pruning a descent. It also must NOT be
                    # `should_skip_dir`, which drops every leading-dot directory --
                    # `.github/scripts/*.py` has to stay compilable.
                    rel_parts = fp.relative_to(root).parts  # rglob only yields under root
                    if ignored_directory_name(rel_parts, root) is not None:
                        continue
                    if embedded_root and embedded_root != root:
                        try:
                            fp.relative_to(embedded_root)
                            continue
                        except ValueError:
                            pass
                    try:
                        py_files.append(str(fp.relative_to(root)))
                    except ValueError:
                        pass
            return sorted(dict.fromkeys(py_files))

        project_py = await run_in_executor_lane("test", _project_python_files)
        if not project_py:
            return json.dumps({
                "ok": True,
                "warning": "No Python files found for py_compile in this project.",
                "error_name": "NO_TESTS_FOUND",
                "type": "TEST_WARNING",
                "severity": "warning",
                "retryable": False,
                "reason": "no Python files found for py_compile",
                "test_discovered": False,
                "profile": profile,
                "suggested_profiles": ["npm_test", "run_project_command"],
                "ca_next_action": "inspect_project_test_config_or_skip_with_reason",
            }, ensure_ascii=False)
        return await run_project_command(str(root), [_PY, "-m", "py_compile", *project_py], timeout=timeout, _executor_lane="test")

    # V15 R35: workflow-based test profiles — delegate to workflow_start (MD dispenser).
    # Returns first step body; CA continues with workflow_submit_step on each step done.
    if profile in ("rawcut_test4_manual", "rawcut_testgf_sheet"):
        try:
            wf_raw = await workflow_start(name=profile, project_path=str(root))
            wf = json.loads(wf_raw)
            return json.dumps({
                "ok": wf.get("ok", False),
                "profile": profile,
                "delegated_to": "workflow_start",
                "workflow_response": wf,
                "ca_next_action": (
                    "do_first_step_then_workflow_submit_step_done_repeat_until_workflow_complete"
                ),
                "hint": (
                    f"Profile '{profile}' is a workflow-based test. First step body is in "
                    f"`workflow_response.step`. Execute it, then call "
                    f"`workflow_submit_step(name='{profile}', step=N, status='done', evidence=...)` "
                    f"to advance. On fail → workflow_paused; STOP and report User."
                ),
            }, ensure_ascii=False)
        except Exception as exc:
            return _json_error(
                f"workflow profile load failed: {exc}",
                error_name="WORKFLOW_PROFILE_LOAD_FAILED",
                profile=profile,
                ca_next_action="create_workflow_md_in_workflows_dir_or_global_or_via_workflow_create_from_ui",
                hint=f"Place {profile}.md in <project>/.orchestrator_cache/workflows/ or <[A]>/.ToolA_Skill/workflows/",
            )

    pytest_basetemp = _fresh_basetemp() if profile == "pytest" else None
    profiles = {
        # F4: auto-inject --basetemp into a fresh %TEMP% subdir so pytest
        # doesn't hit PermissionError on stale __pycache__ / locked temp dirs
        # inside the project tree. Server-side inject — CA never needs to
        # remember the flag.
        "pytest":          [_PY, "-m", "pytest", "-v", "--basetemp", pytest_basetemp],
        "py_compile_core": [_PY, "-m", "py_compile", *core_py],
        "npm_test":        ["npm", "test"],
    }
    command = profiles.get(profile)
    if not command:
        return _json_error(
            f"Invalid test profile: {profile}",
            error_name="RUN_TEST_PROFILE_INVALID",
            type="VALIDATION_ERROR",
            severity="error",
            retryable=False,
            reason="unsupported test profile",
            ca_next_action="choose_supported_profile_from_schema",
            accepted_fields=["project_path", "profile", "timeout"],
            payload_example={
                "project_path": "D:\\path\\to\\project",
                "profile": "npm_test",
                "timeout": 120,
            },
            available_profiles=SUPPORTED_TEST_PROFILES,
            available=SUPPORTED_TEST_PROFILES,
            profile=profile,
        )

    try:
        raw_result = await run_project_command(str(core_root), command, timeout=timeout, _executor_lane="test")
    finally:
        if pytest_basetemp:
            await run_in_executor_lane("test", shutil.rmtree, pytest_basetemp, ignore_errors=True)
    try:
        parsed = json.loads(raw_result)
    except Exception:
        return raw_result
    if parsed.get("ok") is False:
        out = "\n".join([
            str(parsed.get("stderr", "")),
            str(parsed.get("stdout", "")),
            str(parsed.get("error", "")),
        ]).lower()
        error_name = "TEST_PROFILE_FAILED"
        error_type = "TEST_ERROR"
        severity = "error"
        if any(token in out for token in ["no tests", "ran 0 tests", "no test files", "missing script: test"]):
            error_name = "NO_TESTS_FOUND"
            error_type = "TEST_WARNING"
            severity = "warning"
        parsed.setdefault("error_name", error_name)
        parsed.setdefault("type", error_type)
        parsed.setdefault("severity", severity)
        parsed.setdefault("retryable", False)
        parsed.setdefault("reason", str(parsed.get("error") or parsed.get("stderr") or "test profile execution failed")[:500])
        parsed.setdefault("ca_next_action", "inspect_project_test_config_or_skip_with_reason")
        parsed.setdefault("accepted_fields", ["project_path", "profile", "timeout"])
        parsed.setdefault("payload_example", {
            "project_path": "D:\\path\\to\\project",
            "profile": "npm_test",
            "timeout": 120,
        })
        parsed["failure_evidence"] = {
            "exit_code": parsed.get("exit_code"),
            "timed_out": bool(parsed.get("timed_out")),
            "stdout": str(parsed.get("stdout") or ""),
            "stderr": str(parsed.get("stderr") or ""),
            "error": str(parsed.get("error") or ""),
        }
        parsed["profile"] = profile
        return json.dumps(parsed, ensure_ascii=False)
    if parsed.get("warning") and not parsed.get("ca_next_action"):
        parsed["ca_next_action"] = "inspect_project_test_config_or_skip_with_reason"
        parsed["profile"] = profile
        return json.dumps(parsed, ensure_ascii=False)
    return raw_result


async def create_benchmark_request(project_path: str, idea: str = "") -> str:
    root = Path(project_path).resolve()
    if not root.exists():
        return _json_error(f"Project path not found: {project_path}")
    content = f"""# Benchmark Request

User idea:
{idea or "(not provided)"}

Please provide user-approved inputs before CA builds:

- 3-5 benchmark links or app references
- `UI.md` or UI/UX library path if available
- data samples or schemas
- assets or brand constraints
- stack/deploy preference
- must-have features
- explicit out-of-scope items

Rules:

- CA must not crawl the internet.
- CA must not use internal AI/model/proxy tools.
- CA creates `BUILD_CONTEXT.md` only from user-approved inputs.
"""
    path = root / "BENCHMARK_REQUEST.md"
    _atomic_write_text(path, content, encoding="utf-8")
    _update_cache_after_write(str(path), str(root))
    return json.dumps({"ok": True, "file": str(path), "content": content}, ensure_ascii=False)


async def create_build_context(project_path: str, idea: str = "", approved_inputs: str = "") -> str:
    root = Path(project_path).resolve()
    if not root.exists():
        return _json_error(f"Project path not found: {project_path}")
    content = f"""# Build Context

This file is created from user-approved inputs only.

## User Idea

{idea or "(not provided)"}

## Approved Inputs

{approved_inputs or "(none provided yet)"}

## CA Rules

- Build only from this context and user-approved files/paths.
- Do not crawl the internet.
- Do not use internal AI/model/proxy tools.
- Ask before installing dependencies.
- Verify with Orchestrator tests and cache status after writes.
"""
    path = root / "BUILD_CONTEXT.md"
    _atomic_write_text(path, content, encoding="utf-8")
    _update_cache_after_write(str(path), str(root))
    return json.dumps({"ok": True, "file": str(path), "content": content}, ensure_ascii=False)

# ─── Logging helpers ────────────────────────────────────────────────────────

_ORCHESTRATOR_ROOT = Path(__file__).parent.parent  # project root của Orchestrator

# E402 CLUSTER -- this line plus the 2 imports below (3 sites). OUT OF GATE BY DESIGN: ruff.toml
# selects only E9/F63/F7 and its baseline block records all 17 E402 in this file. They sit under
# the "Logging helpers" banner beside the wrapper defs that consume them, per this file's section
# layout. MEASURED 2026-09-20: orchestrator/session/logs.py and orchestrator/session/store.py do
# NOT import code_tools, so this is NOT a circular-import guard -- hoisting is churn with no
# behaviour change. The wrappers below dereference these module aliases at call time; see the
# note above _log_to_file, and the F401/eval() hazard note near L192 before any `ruff --fix`.
import orchestrator.session.logs as _session_logs_module
import orchestrator.session.store as _session_store_module
from orchestrator.session.store import (
    TOOL_ACTION_MAP,
    _session_project_key,
)

_SESSIONS_DB = _session_store_module._SESSIONS_DB


def _sync_session_db_alias():
    _session_store_module._SESSIONS_DB = _SESSIONS_DB
    _session_logs_module._SESSIONS_DB = _SESSIONS_DB


def _with_sessions_db():
    _sync_session_db_alias()
    return _session_store_module._with_sessions_db()


# ─── Commit E (S3-CONTRACT-01) — shared project-scope resolver ────────────────
# list_sessions / get_usage_stats used to live in _PROJECT_PATH_DEFAULTABLE, so
# a zero-payload call had project_path='.' injected by the dispatcher, silently
# scoping the query to the orchestrator's CWD and returning cross-project data.
# Both handlers now share this single scope resolver so they cannot diverge.
# Contract (Commit E, purpose-approved Option c — strict project-only):
#   scope == "project" (default):
#     project_path REQUIRED, non-empty, canonicalized via _session_project_key
#     (the same normalization used when rows were persisted).
#   scope == "all" (explicit cross-project global):
#     DEFERRED — global ops need orchestrator-token auth + audit. Until that
#     plumbing lands, scope="all" returns GLOBAL_SCOPE_NOT_ENABLED; there is
#     NO silent fallback to project scope and NO accidental global leak.
# Returns (project_key, error_json). Exactly one of the two is meaningful:
#   - on success: (canonical_project_key, "")
#   - on failure: ("", json_error_string) — caller must `return error_json`
def _resolve_session_query_scope(tool_name: str, project_path, scope) -> tuple:
    scope_str = str(scope or "project").strip().lower()
    if scope_str not in ("project", "all"):
        return "", _json_error(
            f"Invalid scope '{scope}' for '{tool_name}'. Expected 'project' or 'all'.",
            error_name="INVALID_SCOPE",
            type="SCHEMA_VIOLATION",
            severity="error",
            retryable=False,
            reason=f"scope must be 'project' or 'all', got: {scope!r}",
            missing_fields=[],
            accepted_fields=["project_path", "scope"],
            write_performed=False,
            ca_next_action="set_scope_to_project_or_all",
        )
    if scope_str == "all":
        # Explicit deferral (purpose allows): global cross-project queries
        # require an orchestrator-token gate + audit event. Until that wiring
        # lands we fail loud instead of falling back to project scope.
        return "", _json_error(
            f"Global scope '{scope_str}' is not enabled for '{tool_name}'. "
            "Cross-project queries require orchestrator-token auth + audit (deferred). "
            "Use scope='project' with an explicit project_path.",
            error_name="GLOBAL_SCOPE_NOT_ENABLED",
            type="AUTHORIZATION_ERROR",
            severity="error",
            retryable=False,
            reason="scope='all' deferred — no auth/audit wiring yet; no silent fallback",
            missing_fields=[],
            accepted_fields=["project_path", "scope"],
            write_performed=False,
            ca_next_action="use_scope_project_with_explicit_project_path",
        )
    # scope == "project"
    pp = str(project_path or "").strip()
    if not pp:
        return "", _json_error(
            f"Missing required field(s) for '{tool_name}': project_path",
            error_name="MISSING_REQUIRED_FIELD",
            type="SCHEMA_VIOLATION",
            severity="error",
            retryable=True,
            reason="missing required field(s): project_path",
            missing_fields=["project_path"],
            required_fields=["project_path"],
            accepted_fields=["project_path", "scope"],
            write_performed=False,
            ca_next_action="rebuild_payload_using_required_and_accepted_fields",
        )
    try:
        key = _session_project_key(pp)
    except Exception as exc:
        return "", _json_error(
            f"Invalid project_path for '{tool_name}': {exc}",
            error_name="INVALID_PROJECT_PATH",
            type="SCHEMA_VIOLATION",
            severity="error",
            retryable=False,
            reason=str(exc),
            missing_fields=[],
            accepted_fields=["project_path", "scope"],
            write_performed=False,
            ca_next_action="pass_absolute_project_root",
        )
    if not key:
        return "", _json_error(
            f"Invalid project_path for '{tool_name}': canonical form is empty",
            error_name="INVALID_PROJECT_PATH",
            type="SCHEMA_VIOLATION",
            severity="error",
            retryable=False,
            reason="canonical project key is empty",
            missing_fields=[],
            accepted_fields=["project_path", "scope"],
            write_performed=False,
            ca_next_action="pass_absolute_project_root",
        )
    return key, ""


def _run_session_write(operation, fn, *, busy_timeout_ms=5000):
    _sync_session_db_alias()
    return _session_store_module._run_session_write(
        operation, fn, busy_timeout_ms=busy_timeout_ms)


def _init_session_db():
    _sync_session_db_alias()
    return _session_store_module._init_session_db()


def _close_stale_active_sessions(project_path: str) -> int:
    _sync_session_db_alias()
    return _session_store_module._close_stale_active_sessions(project_path)


def _get_active_session_id(project_path: str, agent_id=None) -> str | None:
    _sync_session_db_alias()
    return _session_store_module._get_active_session_id(project_path, agent_id=agent_id)


def _auto_open_session(project_path: str, body: dict,
                       agent_id=None) -> str | None:
    _sync_session_db_alias()
    return _session_store_module._auto_open_session(
        project_path, body, agent_id=agent_id)


def _open_or_resume_session(project_path: str, session_title: str, mode: str,
                            agent_id=None, **options):
    _sync_session_db_alias()
    return _session_store_module._open_or_resume_session(
        project_path, session_title, mode, agent_id=agent_id, **options)


def _log_session_event(session_id: str, tool_name: str, body: dict, result: dict,
                       duration_ms: int, status: str, error_msg: str = "",
                       audit_id: str = ""):
    _sync_session_db_alias()
    return _session_store_module._log_session_event(
        session_id, tool_name, body, result, duration_ms, status, error_msg,
        audit_id=audit_id,
    )


def _get_session_events_since(session_id: str, last_id: int) -> list[dict]:
    _sync_session_db_alias()
    return _session_store_module._get_session_events_since(session_id, last_id)


def _read_token_upsert(project_key: str, rel_path: str, read_hash: str,
                       read_mtime: float, read_size_bytes: int) -> bool:
    _sync_session_db_alias()
    return _session_store_module._read_token_upsert(
        project_key, rel_path, read_hash, read_mtime, read_size_bytes)


def _read_token_get(project_key: str, rel_path: str):
    _sync_session_db_alias()
    return _session_store_module._read_token_get(project_key, rel_path)


def _task_checkpoint_save(session_key: str, checkpoint: dict) -> bool:
    _sync_session_db_alias()
    return _session_store_module._task_checkpoint_save(session_key, checkpoint)


def _task_checkpoint_load(session_key: str):
    _sync_session_db_alias()
    return _session_store_module._task_checkpoint_load(session_key)


def _task_checkpoint_load_with_meta(session_key: str):
    _sync_session_db_alias()
    return _session_store_module._task_checkpoint_load_with_meta(session_key)


def _task_checkpoint_update(session_key: str, update_fn, busy_timeout_ms: int = 5000):
    _sync_session_db_alias()
    return _session_store_module._task_checkpoint_update(
        session_key, update_fn, busy_timeout_ms=busy_timeout_ms)


TaskCheckpointStorageError = _session_store_module.TaskCheckpointStorageError


# These five wrappers ARE the live definitions of the session-log helpers -- they delegate to
# _session_logs_module. Importing the same five names at the top of this file is therefore dead by
# construction: these defs shadow them as soon as the module finishes loading. A38-B removed that
# import (F401); do not re-add it to "restore a missing name".
#
# They forward (*args, **kwargs) BLIND, on purpose. MEASURED 2026-09-21: A41 widened
# _session_logs_module._log_usage with delivered/client_gone/error_name/agent_id and the dispatcher
# started passing them, but this wrapper still restated the OLD signature -- so EVERY accounting row
# died with `TypeError: _log_usage() got an unexpected keyword argument 'delivered'` (9 of 9 calls
# failed, /health went accounting_ok=False degraded=True, USAGE_LOG.jsonl stopped growing entirely).
# A shadowing wrapper that restates a signature is a SECOND contract nothing keeps in lockstep, and
# tests/test_post_call_accounting.py monkeypatches this very name, so the suite cannot see the drift.
# Forwarding blind deletes the second contract instead of documenting it: the real signatures, their
# defaults and their docstrings live in orchestrator/session/logs.py and are the only copy. Do NOT
# re-add parameter names or defaults here -- every default below was verified identical to the
# producer's before this change, so there is nothing for a signature here to preserve.
def _log_to_file(*args, **kwargs):
    return _session_logs_module._log_to_file(*args, **kwargs)


def _log_usage(*args, **kwargs):
    return _session_logs_module._log_usage(*args, **kwargs)


def _log_app_session(*args, **kwargs):
    return _session_logs_module._log_app_session(*args, **kwargs)


def _auto_trim_log(*args, **kwargs):
    return _session_logs_module._auto_trim_log(*args, **kwargs)


def _check_incomplete_sessions(*args, **kwargs):
    return _session_logs_module._check_incomplete_sessions(*args, **kwargs)


async def log_native(native: list = None) -> str:
    _sync_session_db_alias()
    return await _session_logs_module.log_native(native=native)


# --- Large-repo hardening: offloaded + cached folder scan --------------------
# get_project_summary previously ran an unbounded root.rglob("*") + stat() over
# the whole repo SYNCHRONOUSLY on the single aiohttp event loop to compute
# folder_size_mb (plus a second rglob over ../UI for external_assets). On a big
# repo -- e.g. a project carrying a multi-hundred-MB ledger with tens of
# thousands of files -- that blocked the loop for seconds to minutes, so /health
# stopped answering and the auto-heal watchdog spawned restart orphans that then
# contended on sessions.db. This helper runs the same scan but (1) via os.walk
# with in-place dir pruning so it never descends into skipped dirs (rglob
# descended first, then filtered), (2) bounded by a file cap, and (3) TTL-cached;
# get_project_summary invokes it through asyncio.to_thread so the loop stays free.
_PROJECT_SIZE_TTL_SEC = 300.0
_PROJECT_SIZE_MAX_FILES = 200_000
_project_size_cache: dict[str, tuple[float, dict]] = {}
_project_size_lock = threading.Lock()


def _scan_folder_size_and_assets(root: Path) -> dict:
    """Blocking folder-size + sibling-UI scan. Runs in a worker thread via
    asyncio.to_thread and is TTL-cached so repeat summaries are effectively free.
    """
    key = str(root)
    now = time.time()
    with _project_size_lock:
        hit = _project_size_cache.get(key)
        if hit and (now - hit[0]) < _PROJECT_SIZE_TTL_SEC:
            return hit[1]

    # B027j 2026-09-22: was `SKIP_DIRS | {".orchestrator_cache"}` compared with a raw
    # `in`, so this walk was blind to `.orchestrator_ignore` (B027) AND to the global
    # glob layer (B027i/D2) -- the size it reported counted the very junk trees the
    # index skips, and being case-sensitive it counted `Venv312` too. The union was
    # redundant: `.orchestrator_cache` has been in SKIP_DIRS since CAN-042.
    #
    # Resolved ONCE per scan rather than once per directory. The overlay parse is
    # cached on (root, mtime, size), but rebuilding a frozenset per directory is not,
    # and this walk is the one that used to block the event loop for minutes.
    skip_names = effective_skip_dir_names(root)
    folder_size_mb = None
    truncated = False
    try:
        total_bytes = 0
        seen = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames if not name_matches_ignore(d.lower(), skip_names)
            ]
            for fn in filenames:
                try:
                    total_bytes += os.stat(os.path.join(dirpath, fn)).st_size
                except OSError:
                    continue
                seen += 1
            if seen > _PROJECT_SIZE_MAX_FILES:
                truncated = True
                break
        folder_size_mb = round(total_bytes / (1024 * 1024), 2)
    except Exception:
        folder_size_mb = None

    external_assets: list[dict] = []
    try:
        ui_dir = root.parent / "UI"
        if ui_dir.is_dir():
            for dirpath, dirnames, filenames in os.walk(ui_dir):
                for fn in filenames:
                    if Path(fn).suffix.lower() in {".html", ".js", ".css"}:
                        fp = os.path.join(dirpath, fn)
                        try:
                            sz = os.stat(fp).st_size
                        except OSError:
                            sz = None
                        external_assets.append({
                            "path": fp.replace("\\", "/"),
                            "kind": "ui",
                            "size_bytes": sz,
                        })
    except Exception:
        external_assets = []

    result = {
        "folder_size_mb": folder_size_mb,
        "folder_size_truncated": truncated,
        "external_assets": external_assets,
    }
    with _project_size_lock:
        _project_size_cache[key] = (now, result)
    return result

# ─── Tool 29: get_project_summary ────────────────────────────────────────────

async def get_project_summary(project_path: str) -> str:
    """Trả về overview nhanh từ INDEX — thay thế đọc INDEX.json thủ công."""
    root = Path(project_path).resolve()
    index = _load_index(str(root))
    if not index:
        return _json_error("INDEX not found. Run generate_project_map first.")
    cache_dir = _project_cache_dir(str(root))
    files = index.get("files", {})

    groups: dict = {}
    for rel, info in files.items():
        g = info.get("group", "other")
        if g not in groups:
            groups[g] = {"files": 0, "loc": 0}
        groups[g]["files"] += 1
        groups[g]["loc"] += info.get("loc", 0)

    hotspots = sorted(
        [{"file": rel, "loc": info.get("loc", 0), "complexity": info.get("complexity_score", 0), "group": info.get("group")}
         for rel, info in files.items()],
        key=lambda x: x["complexity"], reverse=True
    )[:5]

    entrypoints = [rel for rel, info in files.items() if info.get("group") == "entrypoint"]

    # folder_size_mb + external UI assets come from a single blocking filesystem
    # scan that is OFFLOADED off the event loop and TTL-cached
    # (_scan_folder_size_and_assets). The previous inline root.rglob("*")+stat()
    # ran synchronously on the single aiohttp loop; on a large repo it blocked the
    # loop for seconds to minutes, tripping the auto-heal watchdog into spawning
    # restart orphans that then contended on sessions.db (2026-08-13 incident).
    _size_scan = await asyncio.to_thread(_scan_folder_size_and_assets, root)
    folder_size_mb = _size_scan["folder_size_mb"]
    external_assets = _size_scan["external_assets"]

    has_session_log = (cache_dir / "APP_SESSION_LOG.jsonl").exists()
    if not has_session_log:
        try:
            db = _SESSIONS_DB
            has_session_log = db.exists() and db.stat().st_size > 0
        except Exception:
            has_session_log = False

    last_mtime = max((info.get("mtime", 0) for info in files.values()), default=0)
    try:
        last_write_at = datetime.fromtimestamp(last_mtime).isoformat(timespec="seconds") if last_mtime else ""
    except Exception:
        last_write_at = ""
    return json.dumps({
        "ok": True,
        "project": root.name,
        "project_name": root.name,
        "generated_at": index.get("generated_at", ""),
        "last_write_at": last_write_at,
        "folder_size_mb": folder_size_mb,
        "stats": {
            "files": len(files),
            "loc": sum(i.get("loc", 0) for i in files.values()),
            "functions": len(index.get("functions", [])),
            "classes": len(index.get("classes", [])),
        },
        "groups": groups,
        "entrypoints": entrypoints,
        "hotspots": hotspots,
        "external_assets": external_assets,
        "context": {
            "has_session_log": has_session_log,
            "has_workflow_state": (cache_dir / "WORKFLOW_STATE.json").exists(),
            "has_project_context": (root / "PROJECT_CONTEXT.md").exists(),
            "has_external_ui": (root.parent / "UI").is_dir(),
        },
    }, ensure_ascii=False)


# ─── Tool 30: query_index ─────────────────────────────────────────────────────

def _query_index_deadline_seconds() -> float:
    """Hard wall-clock budget for one query_index call (P2-SRCH-01).

    Must stay below the agent-side HTTP timeout (~15s) so an unhealthy or
    cold index surfaces as a structured degraded response instead of a
    client-observed zero-response timeout. Env-tunable; default 12s.
    """
    try:
        return max(1.0, float(os.environ.get("ORCHESTRATOR_QUERY_INDEX_DEADLINE_SEC", "12") or 12))
    except Exception:
        return 12.0


def _query_index_symbol_refs(index: dict, needle: str) -> dict[str, list[dict]]:
    """Match a content/symbol query against the INDEX functions/classes maps.

    P2-IDX-02: the pre-fix query filter only substring-matched file paths, so a
    symbol query like ``search_code`` (defined in orchestrator/tools/search.py)
    returned matched:0 - a false zero. The INDEX already carries symbol maps
    keyed by identifier name; underscored identifiers are indexed verbatim, so
    exact case-insensitive name matching finds them without tokenisation drift.
    Returns {symbol_name: [ref, ...]} for names containing the needle.
    """
    hits: dict[str, list[dict]] = {}
    if not needle:
        return hits
    low = needle.lower()
    for bucket in ("functions", "classes"):
        table = index.get(bucket)
        if not isinstance(table, dict):
            continue
        for name, refs in table.items():
            if not isinstance(name, str) or low not in name.lower():
                continue
            if not isinstance(refs, list):
                continue
            for ref in refs:
                if isinstance(ref, dict) and ref.get("file"):
                    hits.setdefault(name, []).append(ref)
    return hits


async def query_index(project_path: str, query: str = "", group: str = None, min_complexity: int = None,
                      min_loc: int = None, file_pattern: str = None, max_results: int = 50,
                      auto_refresh: bool = True) -> str:
    """Filter the immutable CURRENT snapshot INDEX by metadata, path, symbol, and content.

    The selected snapshot stays pinned through drift detection and filtering. When
    refresh is allowed, a missing, non-authoritative, or stale snapshot is replaced
    and CURRENT is pinned again before query evaluation.

    P2-SRCH-01: the whole readiness+query pipeline runs under a strict deadline
    (ORCHESTRATOR_QUERY_INDEX_DEADLINE_SEC, default 12s < the ~15s client HTTP
    timeout). A timeout/unhealthy index returns a structured degraded response,
    never a misleading matched:0. ISS-06: which response depends on what expired
    — INDEX_QUERY_TIMEOUT for a contended snapshot lease (retry clears it) or an
    over-broad query (narrow it), INDEX_REFRESH_REQUIRED for anything that needs
    an O(repo) rebuild the caller must launch and poll itself.

    P2-IDX-02: ``query`` matches three independent scopes - file path/name,
    INDEX symbol maps (functions/classes), and bounded file content read from
    the pinned snapshot blobs. The response carries ``query_scope`` listing the
    scopes that were searched so a zero match is unambiguous.
    """
    root = Path(project_path).resolve()
    loop = asyncio.get_running_loop()
    deadline_at = time.monotonic() + _query_index_deadline_seconds()
    pin = None
    index: dict = {}
    auto_refreshed = False

    def error_response(
        message: str,
        error_name: str,
        category: str,
        snapshot_id: str | None,
        *,
        drift: dict | None = None,
        hint: str = "",
    ) -> str:
        payload = {
            "ok": False,
            "error": message,
            "error_name": error_name,
            "category": category,
            "snapshot_id": snapshot_id,
        }
        if drift is not None:
            payload["drift"] = drift
        if hint:
            payload["hint"] = hint
        return json.dumps(payload, ensure_ascii=False)

    def deadline_error(operation: str, snapshot_id: str | None) -> str:
        # P2-SRCH-01: surface a structured degraded state; never let the caller
        # observe a bare client timeout or mistake a not-ready index for a
        # legitimate matched:0.
        #
        # ISS-06: the error_name is classified by WHAT ran out of time, because a
        # caller retries on each one differently. Lease/pin contention clears by
        # itself, so INDEX_QUERY_TIMEOUT ("retry shortly") is honest there. An
        # O(repo) drift check does NOT clear by itself -- the only way forward is
        # an explicit long refresh, and telling that caller "retry shortly" sent
        # it into a retry loop against a snapshot that stayed stale. A query-side
        # scan budget is neither: the index is pinned and authoritative, so
        # category index_not_ready was simply false there.
        if operation in {"post_refresh_drift_check", "drift_check"}:
            return error_response(
                f"query_index could not complete {operation} within the interactive "
                f"budget ({_query_index_deadline_seconds():.1f}s); no refresh was started",
                "INDEX_REFRESH_REQUIRED",
                "stale_index",
                snapshot_id,
                hint=("Run generate_project_map/refresh_project_map explicitly, poll "
                      "write_status progress until committed, then retry query_index."),
            )
        if operation in {"pin_current_snapshot", "repin_after_refresh"}:
            return error_response(
                f"query_index deadline exceeded during {operation} "
                f"(budget {_query_index_deadline_seconds():.1f}s)",
                "INDEX_QUERY_TIMEOUT",
                "index_not_ready",
                snapshot_id,
                hint=("Snapshot lease is contended or a refresh holds it. Retry shortly; "
                      "if it persists, run generate_project_map explicitly."),
            )
        return error_response(
            f"query_index deadline exceeded during {operation} "
            f"(budget {_query_index_deadline_seconds():.1f}s)",
            "INDEX_QUERY_TIMEOUT",
            "query_budget_exceeded",
            snapshot_id,
            hint=("The index is pinned and authoritative — this query was too broad for "
                  "the interactive budget. Narrow it with file_pattern/group, or raise "
                  "ORCHESTRATOR_QUERY_INDEX_DEADLINE_SEC."),
        )

    def seconds_left() -> float:
        return deadline_at - time.monotonic()

    def check_deadline(operation: str) -> None:
        if seconds_left() <= 0:
            raise _QueryIndexDeadline(operation)

    async def acquire_current():
        # P2-SRCH-01: pin with a hard deadline so a contended STATE.lock or a
        # refresh-in-progress cannot block past the client timeout.
        remaining = seconds_left()
        if remaining <= 0:
            raise _QueryIndexDeadline("pin_current_snapshot")
        return await loop.run_in_executor(
            _INDEX_IO_EXECUTOR,
            _pin_snapshot_index_with_deadline,
            str(root),
            deadline_at,
        )

    async def refresh_current(category: str, previous_snapshot_id: str | None) -> str | None:
        # Interactive queries never own an O(repo) rebuild. A caller can launch
        # generate/refresh explicitly and poll its operation watchdog; this read
        # remains bounded and honest about the stale/missing snapshot.
        return error_response(
            "query_index requires an explicit project-map refresh; no inline long rebuild was started",
            "INDEX_REFRESH_REQUIRED",
            category,
            previous_snapshot_id,
            hint=("Run generate_project_map/refresh_project_map explicitly, poll write_status "
                  "progress until committed, then retry query_index."),
        )

    async def repin_after_refresh(category: str, previous_snapshot_id: str | None) -> str | None:
        nonlocal pin, index, auto_refreshed
        refresh_error = await refresh_current(category, previous_snapshot_id)
        if refresh_error is not None:
            return refresh_error
        auto_refreshed = True
        try:
            pin, index = await acquire_current()
        except (_QueryIndexDeadline, search_snapshots.SnapshotDeadlineExceeded):
            return deadline_error("repin_after_refresh", previous_snapshot_id)
        except Exception as exc:
            return error_response(
                f"CURRENT snapshot unavailable after auto-refresh: {exc}",
                "INDEX_MISSING_AFTER_REFRESH",
                category,
                None,
            )
        if not pin.manifest.get("index_authoritative", False):
            return error_response(
                "CURRENT snapshot INDEX is still non-authoritative after auto-refresh",
                "SNAPSHOT_INDEX_NOT_AUTHORITATIVE_AFTER_REFRESH",
                category,
                pin.snapshot_id,
            )
        return None

    try:
        try:
            pin, index = await acquire_current()
        except (_QueryIndexDeadline, search_snapshots.SnapshotDeadlineExceeded):
            return deadline_error("pin_current_snapshot", None)
        except Exception as exc:
            if not auto_refresh:
                return error_response(
                    f"CURRENT snapshot unavailable: {exc}",
                    "SNAPSHOT_UNAVAILABLE",
                    "missing_index",
                    None,
                    hint="Run generate_project_map or re-run with auto_refresh=true.",
                )
            refresh_error = await repin_after_refresh("missing_index", None)
            if refresh_error is not None:
                return refresh_error

        if not pin.manifest.get("index_authoritative", False):
            snapshot_id = pin.snapshot_id
            if not auto_refresh:
                return error_response(
                    "CURRENT snapshot INDEX is non-authoritative; refresh_project_map required before query_index",
                    "SNAPSHOT_INDEX_NOT_AUTHORITATIVE",
                    "non_authoritative_index",
                    snapshot_id,
                    hint="Re-run with auto_refresh=true to publish and pin an authoritative INDEX.",
                )
            pin.release()
            pin = None
            refresh_error = await repin_after_refresh("non_authoritative_index", snapshot_id)
            if refresh_error is not None:
                return refresh_error

        try:
            drift_payload = json.loads(await asyncio.wait_for(
                asyncio.shield(_cache_delta_for_snapshot(
                    str(root),
                    pin.snapshot_id,
                    index,
                )),
                timeout=max(0.001, seconds_left()),
            ))
        except asyncio.TimeoutError:
            return error_response(
                "snapshot drift check exceeded the interactive response budget; no refresh was started",
                "INDEX_REFRESH_REQUIRED",
                "stale_index",
                pin.snapshot_id,
                hint=("Run refresh_project_map explicitly, poll write_status progress until "
                      "committed, then retry query_index."),
            )
        except Exception as exc:
            return error_response(
                f"snapshot drift check failed: {exc}",
                "SNAPSHOT_DRIFT_CHECK_FAILED",
                "stale_index",
                pin.snapshot_id,
            )
        if not isinstance(drift_payload, dict) or not drift_payload.get("ok"):
            return error_response(
                "snapshot drift check returned an invalid response",
                "SNAPSHOT_DRIFT_CHECK_FAILED",
                "stale_index",
                pin.snapshot_id,
            )
        drift = drift_payload if (
            drift_payload.get("added")
            or drift_payload.get("modified")
            or drift_payload.get("deleted")
        ) else None

        if drift:
            snapshot_id = pin.snapshot_id
            if not auto_refresh:
                return error_response(
                    "INDEX stale; refresh_project_map required before query_index",
                    "STALE_INDEX",
                    "stale_index",
                    snapshot_id,
                    drift=drift,
                    hint="Re-run with auto_refresh=true to refresh inline.",
                )
            pin.release()
            pin = None
            refresh_error = await repin_after_refresh("stale_index", snapshot_id)
            if refresh_error is not None:
                return refresh_error
            assert pin is not None
            try:
                drift_payload = json.loads(await asyncio.wait_for(
                    asyncio.shield(_cache_delta_for_snapshot(
                        str(root),
                        pin.snapshot_id,
                        index,
                    )),
                    timeout=max(0.001, seconds_left()),
                ))
            except asyncio.TimeoutError:
                return deadline_error("post_refresh_drift_check", pin.snapshot_id)
            except Exception as exc:
                return error_response(
                    f"post-refresh snapshot drift check failed: {exc}",
                    "SNAPSHOT_DRIFT_CHECK_FAILED",
                    "stale_index",
                    pin.snapshot_id,
                )
            drift = drift_payload if (
                isinstance(drift_payload, dict)
                and drift_payload.get("ok")
                and (
                    drift_payload.get("added")
                    or drift_payload.get("modified")
                    or drift_payload.get("deleted")
                )
            ) else None
            if drift:
                return error_response(
                    "INDEX still stale after auto-refresh",
                    "STALE_INDEX_AFTER_REFRESH",
                    "stale_index",
                    pin.snapshot_id,
                    drift=drift,
                )
            if not isinstance(drift_payload, dict) or not drift_payload.get("ok"):
                return error_response(
                    "post-refresh snapshot drift check returned an invalid response",
                    "SNAPSHOT_DRIFT_CHECK_FAILED",
                    "stale_index",
                    pin.snapshot_id,
                )

        files = index.get("files", {})
        if not isinstance(files, dict):
            return error_response(
                "snapshot INDEX files field is invalid",
                "SNAPSHOT_INDEX_INVALID",
                "invalid_index",
                pin.snapshot_id,
            )

        # P2-IDX-02: query matching spans three independent scopes so a text
        # query that lives in file CONTENT (or names an indexed symbol) no
        # longer returns a false matched:0 when no file PATH contains it.
        #   - path:    substring on relative path / file name (pre-fix behavior)
        #   - symbol:  substring on INDEX functions/classes symbol names
        #              (underscored identifiers are indexed verbatim, so
        #              "search_code" hits the def in orchestrator/tools/search.py)
        #   - content: bounded literal substring scan of the pinned snapshot
        #              blobs for files whose path+symbol scopes missed
        symbol_refs = _query_index_symbol_refs(index, query) if query else {}
        symbol_files: dict[str, list[str]] = {}
        for sym_name, refs in symbol_refs.items():
            for ref in refs:
                symbol_files.setdefault(str(ref["file"]), []).append(sym_name)

        # Content scope is bounded: per-file byte cap + global wall-clock via
        # check_deadline so a huge tree cannot eat the query budget.
        try:
            content_file_cap = int(os.environ.get("ORCHESTRATOR_QUERY_INDEX_CONTENT_CAP_BYTES", "262144") or 262144)
        except Exception:
            content_file_cap = 262144
        needle_bytes = query.encode("utf-8", errors="ignore").lower() if query else b""
        content_scope_enabled = bool(needle_bytes)

        def _evaluate_rows() -> tuple[list[dict], list[str], int]:
            content_scanned = 0

            def _content_hit(rel_path: str) -> bool:
                nonlocal content_scanned
                try:
                    payload = pin.read_bytes(rel_path)
                except Exception:
                    return False
                if len(payload) > content_file_cap:
                    payload = payload[:content_file_cap]
                content_scanned += 1
                return needle_bytes in payload.lower()

            rows: list[dict] = []
            scopes: list[str] = []
            if query:
                scopes.append("path")
                scopes.append("symbol")
            for rel, info in files.items():
                if group and info.get("group") != group:
                    continue
                matched_via: list[str] = []
                if query:
                    if query.lower() in rel.lower() or query.lower() in Path(rel).name.lower():
                        matched_via.append("path")
                    elif rel in symbol_files:
                        matched_via.append("symbol")
                    elif content_scope_enabled:
                        # P2-IDX-02: content fallback for path-oriented index -
                        # scan the pinned snapshot blob instead of returning a
                        # false zero.
                        if "content" not in scopes:
                            scopes.append("content")
                        check_deadline("content_scan")
                        if _content_hit(rel):
                            matched_via.append("content")
                    if not matched_via:
                        continue
                if min_complexity is not None and info.get("complexity_score", 0) < min_complexity:
                    continue
                if min_loc is not None and info.get("loc", 0) < min_loc:
                    continue
                if file_pattern:
                    rel_norm = rel.replace("\\", "/")
                    name_match = fnmatch.fnmatch(Path(rel).name, file_pattern)
                    path_match = (
                        fnmatch.fnmatch(rel_norm, file_pattern)
                        or fnmatch.fnmatch(rel_norm, f"*/{file_pattern}")
                    )
                    if not (name_match or path_match):
                        continue
                row = {
                    "path": rel,
                    "group": info.get("group"),
                    "loc": info.get("loc", 0),
                    "complexity": info.get("complexity_score", 0),
                }
                if matched_via:
                    row["matched_via"] = matched_via
                    if rel in symbol_files:
                        row["matched_symbols"] = sorted(set(symbol_files[rel]))[:10]
                rows.append(row)
            return rows, scopes, content_scanned

        # Run the (potentially blob-reading) evaluation off the event loop; the
        # inner check_deadline self-terminates the worker on budget exhaustion
        # and _QueryIndexDeadline propagates to the degraded-response handler.
        results, query_scopes_searched, content_scanned = await loop.run_in_executor(
            _INDEX_IO_EXECUTOR,
            _evaluate_rows,
        )

        results.sort(key=lambda item: item["complexity"], reverse=True)
        total_matched = len(results)
        results = results[:max_results]
        response = {
            "ok": True,
            "matched": total_matched,
            "returned": len(results),
            "truncated": total_matched > max_results,
            "total_files": len(files),
            "files": results,
            "snapshot_id": pin.snapshot_id,
        }
        if query:
            # P2-IDX-02: make a zero match unambiguous - the caller can see
            # exactly which scopes were searched before concluding "absent".
            response["query_scope"] = query_scopes_searched
            if content_scanned:
                response["content_files_scanned"] = content_scanned
        if auto_refreshed:
            response["auto_refreshed"] = True
            response["note"] = "Snapshot INDEX was refreshed and repinned before query."
        return json.dumps(response, ensure_ascii=False)
    except _QueryIndexDeadline as exc:
        # P2-SRCH-01: any stage that blew the hard budget surfaces as a
        # structured degraded state - never a bare hang and never matched:0.
        return deadline_error(exc.operation, pin.snapshot_id if pin is not None else None)
    finally:
        if pin is not None:
            pin.release()


# ─── Tool 31: get_usage_stats ─────────────────────────────────────────────────

async def get_usage_stats(project_path: str = "", last_n: int = 200,
                            include_http_log: bool = False,
                            include_global_usage: bool = False,
                            scope: str = "project") -> str:
    """Đọc APP_SESSION_LOG.jsonl HOẶC SQLite events cho MỘT project (project-scoped only).

    E-01: include_global_usage + include_http_log are TOMBSTONES. They used to
    append process-global root USAGE_LOG.jsonl, ui_log.jsonl and an HTTP tail
    of orchestrator.log to a project-scoped result — a cross-project/root/UI/
    HTTP leak with no scope='all' gate. Both now fail loud with
    GLOBAL_SCOPE_NOT_ENABLED on explicit use; the global/UI/HTTP aggregation
    branches are removed from project-scoped execution. Re-enable only via a
    future token-gated + audited global op."""
    # Commit E (S3-CONTRACT-01): scope resolved via shared helper so a
    # zero-payload call can never silently downgrade to CWD/global data.
    project_key, _scope_err = _resolve_session_query_scope(
        "get_usage_stats", project_path, scope)
    if _scope_err:
        return _scope_err
    # E-01 tombstone gates: explicit legacy request -> fail loud, no silent
    # ignore and no generic schema error.
    if include_global_usage:
        return _json_error(
            "include_global_usage is not enabled for 'get_usage_stats'. "
            "Root/global usage + UI logs are cross-project data and require an "
            "orchestrator-token-gated, audited global op (deferred).",
            error_name="GLOBAL_SCOPE_NOT_ENABLED",
            type="AUTHORIZATION_ERROR",
            severity="error",
            retryable=False,
            reason="include_global_usage tombstoned (E-01): process-global escape hatch removed from project scope",
            missing_fields=[],
            accepted_fields=["project_path", "scope", "last_n"],
            write_performed=False,
            ca_next_action="drop_include_global_usage_or_wait_for_token_gated_global_op",
        )
    if include_http_log:
        return _json_error(
            "include_http_log is not enabled for 'get_usage_stats'. "
            "The orchestrator.log HTTP tail is process-wide (all projects/roots) "
            "and requires an orchestrator-token-gated, audited global op (deferred).",
            error_name="GLOBAL_SCOPE_NOT_ENABLED",
            type="AUTHORIZATION_ERROR",
            severity="error",
            retryable=False,
            reason="include_http_log tombstoned (E-01): process-wide HTTP log escape hatch removed from project scope",
            missing_fields=[],
            accepted_fields=["project_path", "scope", "last_n"],
            write_performed=False,
            ca_next_action="drop_include_http_log_or_wait_for_token_gated_global_op",
        )
    root = Path(project_key).resolve()
    cache_dir = _project_cache_dir(str(root))
    log_path = cache_dir / "APP_SESSION_LOG.jsonl"

    entries: list = []
    source = ""
    if log_path.exists():
        try:
            # One row per "\n": ensure_ascii=False keeps a raw U+2028/U+0085 INSIDE a
            # row, and a str.splitlines() fragment failed json.loads -- and the tool.
            raw_lines = split_lines(log_path.read_text(encoding="utf-8"))
            entries = [json.loads(line) for line in raw_lines[-last_n:] if line.strip()]
            source = "APP_SESSION_LOG.jsonl"
        except Exception as e:
            return _json_error(f"Failed to read session log: {e}")

    # Fallback: aggregate from SQLite session events for THIS project.
    # Commit E: filter by the canonical project_key resolved at the entry gate,
    # exact-equality (no prefix/substring), so cross-project rows can never leak.
    if not entries:
        try:
            norm = project_key
            conn = sqlite3.connect(str(_SESSIONS_DB))
            rows = conn.execute(
                """SELECT e.tool_name, e.status, e.duration_ms, e.target,
                          e.reason, e.created_at
                   FROM events e
                   JOIN sessions s ON e.session_id = s.session_id
                   WHERE s.project_path = ?
                   ORDER BY e.id DESC LIMIT ?""",
                (norm, last_n)
            ).fetchall()
            conn.close()
            for r in rows:
                entries.append({
                    "tool": r[0],
                    "ok": (r[1] == "ok"),
                    "ms": r[2] or 0,
                    "file": r[3] or "",
                    "error": r[4] or "",
                    "ts": r[5] or "",
                })
            entries.reverse()
            if entries:
                source = "sqlite:events"
        except Exception:
            pass

    # E-01: the include_global_usage (root USAGE_LOG.jsonl + ui_log.jsonl) and
    # include_http_log (orchestrator.log HTTP tail) branches are REMOVED.
    # Both read process-global files outside the resolved project scope and
    # leaked cross-project/root/UI/HTTP data into a project-scoped result.
    # Explicit use of either flag is rejected at the entry gate above with
    # GLOBAL_SCOPE_NOT_ENABLED; there is no silent ignore.

    if not entries:
        return json.dumps({
            "ok": True, "status": "no_project_log",
            "scope": "project",
            "project_path": project_key,
            "message": "No usage data for this project.",
        }, ensure_ascii=False)

    from collections import defaultdict
    stats: dict = defaultdict(lambda: {"calls": 0, "errors": 0, "total_ms": 0})
    errors = []
    errors_by_tool: dict = defaultdict(list)
    recent_writes: list = []

    for entry in entries:
        tool = entry.get("tool", "?")
        stats[tool]["calls"] += 1
        stats[tool]["total_ms"] += entry.get("ms", 0)
        if not entry.get("ok", True):
            stats[tool]["errors"] += 1
            err = {"tool": tool, "ts": entry.get("ts", ""), "error": entry.get("error", "")}
            errors.append(err)
            errors_by_tool[tool].append(err)
        if tool in ("write_fix", "write_new_file") and entry.get("file"):
            recent_writes.append(entry["file"])

    top_tools = sorted([{
        "tool": t, "calls": s["calls"],
        "error_rate": round(s["errors"] / s["calls"], 2) if s["calls"] else 0,
        "avg_ms": int(s["total_ms"] / s["calls"]) if s["calls"] else 0,
    } for t, s in stats.items()], key=lambda x: x["calls"], reverse=True)

    return json.dumps({
        "ok": True,
        "scope": "project",
        "project_path": project_key,
        "source": source,
        "entries_analyzed": len(entries),
        "top_tools": top_tools[:10],
        "recent_errors": errors[-20:],
        "errors_by_tool": {tool: items[-10:] for tool, items in errors_by_tool.items()},
        "recent_writes": list(dict.fromkeys(recent_writes))[-10:],
    }, ensure_ascii=False)


# ─── Tool 32: get_folder_size ─────────────────────────────────────────────────

async def get_folder_size(folder_path: str, include_skipped: bool = False) -> str:
    """Count a directory tree; safe-default exclusions are opt-out and pruned early."""
    skip_names = {
        "venv", "venv312", ".venv", "node_modules", ".git",
        "__pycache__", ".orchestrator_cache",
    }
    root = Path(folder_path).resolve()
    if not root.exists():
        return _json_error(f"Path not found: {folder_path}")
    if not root.is_dir():
        return _json_error(
            f"Directory required: {folder_path}",
            error_name="PATH_IS_NOT_DIRECTORY",
            ca_next_action="pass_folder_path",
        )

    def _scan() -> tuple[int, int, int, int]:
        total_bytes = 0
        total_files = 0
        skipped_dirs = 0
        unreadable_files = 0
        for current, dirnames, filenames in os.walk(root):
            if not include_skipped:
                kept = []
                for dirname in dirnames:
                    if dirname.lower() in skip_names:
                        skipped_dirs += 1
                    else:
                        kept.append(dirname)
                dirnames[:] = kept
            current_path = Path(current)
            for filename in filenames:
                try:
                    total_bytes += (current_path / filename).stat().st_size
                    total_files += 1
                except OSError:
                    unreadable_files += 1
        return total_bytes, total_files, skipped_dirs, unreadable_files

    try:
        total_bytes, total_files, skipped_dirs, unreadable_files = await run_in_executor_lane("read_io", _scan)
    except Exception as exc:
        return _json_error(str(exc))
    return json.dumps({
        "ok": True,
        "folder": str(root),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / 1_048_576, 2),
        "scan_scope": "full" if include_skipped else "safe_default",
        "include_skipped": bool(include_skipped),
        "excluded_names": [] if include_skipped else sorted(skip_names),
        "skipped_dir_count": skipped_dirs,
        "unreadable_file_count": unreadable_files,
    }, ensure_ascii=False)


# ─── V15 R27 — Folder interaction suite (testgf workflow) ─────────────────────

async def folder_exists(path: str) -> str:
    """V15 R27 T1: minimal folder existence + type probe."""
    try:
        p = Path(path).resolve()
        if not p.exists():
            return json.dumps({"ok": True, "path": str(p), "exists": False})
        return json.dumps({
            "ok": True,
            "path": str(p),
            "exists": True,
            "is_dir": p.is_dir(),
            "is_file": p.is_file(),
            "is_symlink": p.is_symlink(),
        })
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc), "error_name": "FOLDER_EXISTS_FAILED"})


async def create_folder(path: str, recursive: bool = True, project_path: str = "") -> str:
    """V15 R65 T2 file-artifact mutation: create a directory.

    Idempotent mkdir — an already-existing directory is SUCCESS (created=False),
    not an error. recursive=True (default) creates the parent tree like
    `mkdir -p`. Lightweight replacement for run_project_test_script(fs.mkdirSync)
    per the CA folder-creation pain report (was 9 round-trips + Windows quoting
    hell for a basic syscall). Path travels as a JSON string so spaces/non-ASCII
    survive without any shell quoting.

    Returns {ok, path, created, exists, is_dir}.
    """
    try:
        raw = str(path or "").strip()
        if not raw:
            return json.dumps({"ok": False, "error": "path is required",
                               "error_name": "WRONG_ARGUMENTS"})
        p = Path(raw)
        root = str(project_path or "").strip()
        if root and not p.is_absolute():
            p = Path(root) / raw
        p = p.resolve()
        if p.exists():
            if p.is_dir():
                return json.dumps({"ok": True, "path": str(p), "created": False,
                                   "exists": True, "is_dir": True, "note": "already exists",
                                   "project_path": root})
            return json.dumps({"ok": False, "path": str(p),
                               "error": "Path exists but is not a directory",
                               "error_name": "NOT_A_DIRECTORY"})
        p.mkdir(parents=recursive, exist_ok=True)
        return json.dumps({"ok": True, "path": str(p), "created": True,
                           "exists": p.exists(), "is_dir": p.is_dir(), "recursive": recursive,
                           "project_path": root})
    except FileNotFoundError as exc:
        return json.dumps({"ok": False,
                           "error": f"Parent folder missing — set recursive=true: {exc}",
                           "error_name": "PARENT_NOT_FOUND", "path": path, "recursive": recursive})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc),
                           "error_name": "CREATE_FOLDER_FAILED", "path": path})


async def create_folders(paths: list | None = None, recursive: bool = True, project_path: str = "") -> str:
    """V15 R65 T2 file-artifact mutation: batch create_folder.

    Pass paths=[...]; returns per-path results + created count. ok=True only when
    every path succeeded. One bad path does not abort the others.
    """
    if not isinstance(paths, list) or not paths:
        return json.dumps({"ok": False,
                           "error": "paths must be a non-empty list of strings",
                           "error_name": "WRONG_ARGUMENTS"})
    results = []
    all_ok = True
    for raw in paths:
        if not isinstance(raw, str):
            all_ok = False
            results.append({
                "ok": False,
                "path": raw,
                "error": "each item in paths must be a string; nested arrays are not supported",
                "error_name": "WRONG_ARGUMENTS",
                "accepted_shape": {"paths": ["C:/absolute/folder"]},
            })
            continue
        try:
            one = json.loads(await create_folder(raw, recursive, project_path=project_path))
        except Exception as exc:
            one = {"ok": False, "path": str(raw), "error": str(exc),
                   "error_name": "CREATE_FOLDER_FAILED"}
        if not one.get("ok"):
            all_ok = False
        results.append(one)
    return json.dumps({"ok": all_ok, "count": len(results),
                       "created": sum(1 for r in results if r.get("created")),
                       "results": results})


async def folder_stats(path: str, recursive: bool = False,
                        ignore_patterns: list | None = None,
                        hint_when_subdirs_present: bool = True) -> str:
    """V15 R27 T1: folder stats (file count, dir count, bytes, mtime, top extensions).

    Non-recursive by default (matches `dir` behavior). Set recursive=true for deep stats.
    `ignore_patterns` = list of substring patterns; entries whose name contains any pattern skipped.
    """
    DEFAULT_SKIP = ("venv", ".venv", "node_modules", ".git", "__pycache__", ".orchestrator_cache")
    skip_patterns = tuple(ignore_patterns or []) + DEFAULT_SKIP
    try:
        root = Path(path).resolve()
        if not root.exists():
            return json.dumps({"ok": False, "exists": False,
                                "error": f"Path not found: {path}",
                                "error_name": "PATH_NOT_FOUND"})
        if not root.is_dir():
            return json.dumps({"ok": False, "exists": True, "is_dir": False,
                                "error": "Path is not a directory",
                                "error_name": "NOT_A_DIRECTORY"})
        def _scan():
            file_count = 0
            dir_count = 0
            total_bytes = 0
            latest_mtime = 0.0
            ext_counter: dict = {}
            # Large-repo hardening: recursive rglob("*") can enumerate a whole
            # tree, so this scan runs in an executor lane (like get_folder_size)
            # rather than blocking the single aiohttp event loop.
            iterator = root.rglob("*") if recursive else root.iterdir()
            for p in iterator:
                try:
                    if any(s.lower() in str(p).lower() for s in skip_patterns):
                        continue
                    if p.is_file():
                        file_count += 1
                        st = p.stat()
                        total_bytes += st.st_size
                        if st.st_mtime > latest_mtime:
                            latest_mtime = st.st_mtime
                        ext = (p.suffix or "<no_ext>").lower()
                        ext_counter[ext] = ext_counter.get(ext, 0) + 1
                    elif p.is_dir():
                        dir_count += 1
                except (PermissionError, OSError):
                    continue
            return file_count, dir_count, total_bytes, latest_mtime, ext_counter

        file_count, dir_count, total_bytes, latest_mtime, ext_counter = await run_in_executor_lane("read_io", _scan)
        from datetime import datetime as _dt
        latest_mtime_iso = (_dt.fromtimestamp(latest_mtime).isoformat(timespec="seconds")
                              if latest_mtime > 0 else None)
        top_extensions = sorted(ext_counter.items(), key=lambda kv: kv[1], reverse=True)[:8]
        # V15 R64-C: surface "0 direct files but N subdirs" hint so CA doesn't
        # mistakenly conclude "empty folder" when subdirs hold the actual files.
        # Pattern hit live during rawcut_getfootage_sheet_v0 step 25: workflow
        # called folder_stats(getlink_output) → file_count=0 → confusing because
        # getlink_map/HOOK 1/sheet_rows.json existed deeper. Hint guides CA to
        # recursive=True OR per-subdir folder_stats.
        _resp = {
            "ok": True,
            "path": str(root),
            "exists": True,
            "is_dir": True,
            "recursive": recursive,
            "file_count": file_count,
            "dir_count": dir_count,
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / 1_048_576, 2),
            "latest_mtime": latest_mtime,
            "latest_mtime_iso": latest_mtime_iso,
            "top_extensions": [{"ext": e, "count": c} for e, c in top_extensions],
            "ignore_patterns_used": list(skip_patterns),
        }
        if hint_when_subdirs_present and not recursive and file_count == 0 and dir_count > 0:
            _resp["subdirs_present_hint"] = (
                f"file_count=0 but dir_count={dir_count} (non-recursive). "
                f"Files likely live in subfolders — re-call with recursive=true for full count, "
                f"OR call folder_stats per subdirectory."
            )
        return json.dumps(_resp, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc), "error_name": "FOLDER_STATS_FAILED"})


async def folder_diff(path_a: str, path_b: str,
                       ignore_patterns: list | None = None,
                       compare_by: str = "name") -> str:
    """V15 R27 T1: compare contents of two folders (non-recursive, top-level entries).

    compare_by ∈ {name, name+size, name+sha}.
      - name: only entry name
      - name+size: name + file size
      - name+sha: name + sha256 (slow, opt-in)
    Returns: {only_in_a, only_in_b, in_both, total_a, total_b, matched_count, mismatched: [...]}.
    """
    DEFAULT_SKIP = ("venv", ".venv", "node_modules", ".git", "__pycache__", ".orchestrator_cache")
    skip = tuple(ignore_patterns or []) + DEFAULT_SKIP
    mode = (compare_by or "name").lower()
    if mode not in {"name", "name+size", "name+sha"}:
        return json.dumps({"ok": False,
                            "error_name": "UNSUPPORTED_FIELD",
                            "error": f"compare_by must be one of name|name+size|name+sha; got {compare_by}"})
    try:
        ra = Path(path_a).resolve()
        rb = Path(path_b).resolve()
        if not ra.exists() or not ra.is_dir():
            return json.dumps({"ok": False, "error_name": "PATH_A_INVALID", "path_a": str(ra)})
        if not rb.exists() or not rb.is_dir():
            return json.dumps({"ok": False, "error_name": "PATH_B_INVALID", "path_b": str(rb)})

        def _entry_key(p: Path) -> tuple:
            """Build comparison key based on mode."""
            name = p.name
            if mode == "name":
                return (name,)
            if mode == "name+size":
                size = p.stat().st_size if p.is_file() else -1
                return (name, size)
            # name+sha
            if p.is_file():
                import hashlib as _hl
                h = _hl.sha256()
                with open(p, "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        h.update(chunk)
                return (name, h.hexdigest()[:16])
            return (name, "<dir>")

        def _scan(root: Path) -> dict:
            entries = {}
            for p in root.iterdir():
                try:
                    if any(s.lower() in str(p).lower() for s in skip):
                        continue
                    entries[p.name] = _entry_key(p)
                except (PermissionError, OSError):
                    continue
            return entries

        a_entries = _scan(ra)
        b_entries = _scan(rb)
        names_a = set(a_entries.keys())
        names_b = set(b_entries.keys())
        only_in_a = sorted(names_a - names_b)
        only_in_b = sorted(names_b - names_a)
        in_both = sorted(names_a & names_b)
        mismatched = []
        if mode != "name":
            for n in in_both:
                if a_entries[n] != b_entries[n]:
                    mismatched.append({"name": n, "a_key": a_entries[n], "b_key": b_entries[n]})
        matched_count = len(in_both) - len(mismatched)
        return json.dumps({
            "ok": True,
            "path_a": str(ra),
            "path_b": str(rb),
            "compare_by": mode,
            "total_a": len(names_a),
            "total_b": len(names_b),
            "only_in_a": only_in_a,
            "only_in_b": only_in_b,
            "in_both": in_both,
            "matched_count": matched_count,
            "mismatched": mismatched,
            "identical_top_level": (not only_in_a and not only_in_b and not mismatched),
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc), "error_name": "FOLDER_DIFF_FAILED"})


# -- T4 desktop dialog runtime shims -----------------------------------------
# E402 CLUSTER -- 4 sites: this line plus the three `from orchestrator.runtime import (...)` blocks
# below. OUT OF GATE BY DESIGN: ruff.toml selects only E9/F63/F7 and its baseline block records all
# 17 E402 in this file. They live beside the T4 shims that consume them because this module is a
# 15.6k-line aggregator sectioned by banner. MEASURED 2026-09-20: orchestrator/runtime/__init__.py
# does NOT import code_tools, so this is NOT a circular-import guard -- hoisting is pure churn.
# Every alias below is used by the T4 wrapper defs that follow; do not delete an "unused" import.
from orchestrator.runtime import (
    cancel_native_dialog as _runtime_cancel_native_dialog,
)
from orchestrator.runtime import (
    click_and_handle_folder_dialog as _runtime_click_and_handle_folder_dialog,
)
from orchestrator.runtime import (
    fill_folder_dialog as _runtime_fill_folder_dialog,
)
from orchestrator.runtime import (
    folder_dialog_snapshot as _runtime_folder_dialog_snapshot,
)


async def fill_folder_dialog(title_pattern: str = "", folder_path: str = "",
                             timeout_ms: int = 5000,
                             wait_for_dialog_ms: int = 3000,
                             auto_try_common_titles: bool = True,
                             electron_fallback_hint: dict | None = None,
                             owner_pid: int = 0,
                             parent_hwnd: int = 0,
                             class_name_pattern: str = "",
                             exclude_titles: list | None = None,
                             launched_after_ts: str = "",
                             dry_run: bool = False,
                             try_modern_picker: bool = True) -> str:
    return await _runtime_fill_folder_dialog(
        title_pattern=title_pattern,
        folder_path=folder_path,
        timeout_ms=timeout_ms,
        wait_for_dialog_ms=wait_for_dialog_ms,
        auto_try_common_titles=auto_try_common_titles,
        electron_fallback_hint=electron_fallback_hint,
        owner_pid=owner_pid,
        parent_hwnd=parent_hwnd,
        class_name_pattern=class_name_pattern,
        exclude_titles=exclude_titles,
        launched_after_ts=launched_after_ts,
        dry_run=dry_run,
        try_modern_picker=try_modern_picker,
    )


async def folder_dialog_snapshot(owner_pid: int = 0,
                                 parent_hwnd: int = 0,
                                 title_pattern: str = "",
                                 launched_after_ts: str = "",
                                 include_child_controls: bool = True) -> str:
    return await _runtime_folder_dialog_snapshot(
        owner_pid=owner_pid,
        parent_hwnd=parent_hwnd,
        title_pattern=title_pattern,
        launched_after_ts=launched_after_ts,
        include_child_controls=include_child_controls,
    )


async def cancel_native_dialog(title_pattern: str = "",
                               button_pattern: str = "Cancel|Huy|Close|Dong",
                               owner_pid: int = 0,
                               parent_hwnd: int = 0,
                               exclude_titles: list | None = None) -> str:
    return await _runtime_cancel_native_dialog(
        title_pattern=title_pattern,
        button_pattern=button_pattern,
        owner_pid=owner_pid,
        parent_hwnd=parent_hwnd,
        exclude_titles=exclude_titles,
    )


async def click_and_handle_folder_dialog(page_id: str,
                                         click_selector: str,
                                         folder_path: str,
                                         owner_pid: int = 0,
                                         owner_title: str = "",
                                         wait_for_dialog_ms: int = 5000,
                                         timeout_ms: int = 8000,
                                         try_modern_picker: bool = True,
                                         verify_target_exists: bool = True) -> str:
    return await _runtime_click_and_handle_folder_dialog(
        page_id=page_id,
        click_selector=click_selector,
        folder_path=folder_path,
        owner_pid=owner_pid,
        owner_title=owner_title,
        wait_for_dialog_ms=wait_for_dialog_ms,
        timeout_ms=timeout_ms,
        try_modern_picker=try_modern_picker,
        verify_target_exists=verify_target_exists,
    )


# ─── V15 R41: Active-source detector for Electron (verify file actually loaded) ─────

async def verify_active_electron_source(page_id: str, expected_path: str = "",
                                          cdp_port: int = 9223) -> str:
    """V15 R41 T1: verify which file Electron CDP target is ACTUALLY loaded from.

    Use case: CA hay sửa nhầm `Source/ui/index.html` trong khi Electron đã load
    mirror `dist/index.html` hoặc `public/index.html`. Đọc `document.URL` /
    `window.location.href` qua CDP để confirm active source.

    Args:
      page_id: CDP target ID (from list_cdp_targets)
      expected_path: optional absolute path CA expects (e.g. ".../Source/ui/index.html")
      cdp_port: CDP debug port (default 9223)

    Returns:
      {ok, document_url, window_location, file_path_decoded, matches_expected,
       all_loaded_resources (top 10), ca_next_action, hint}
    """
    import tools_v15 as _v15_r41
    try:
        # evaluate document.URL + window.location.href + a list of <script src=> + <link href=>
        expr = (
            "(()=>{const refs=[];try{document.querySelectorAll('script[src],link[href]').forEach"
            "(el=>refs.push(el.src||el.href));}catch(e){}return JSON.stringify({"
            "documentURL:document.URL,locationHref:window.location.href,"
            "baseURI:document.baseURI,resources:refs.slice(0,10)})})()"
        )
        raw = await _v15_r41.evaluate_js(page_id=page_id, expression=expr)
        try:
            ev_resp = json.loads(raw)
        except Exception:
            ev_resp = {"result": raw}
        # evaluate_js returns nested result; try common shapes
        payload = (ev_resp.get("value") or ev_resp.get("result")
                    or ev_resp.get("data") or "")
        if isinstance(payload, dict):
            data = payload
        else:
            try:
                data = json.loads(payload) if isinstance(payload, str) else {}
            except Exception:
                data = {"raw": str(payload)[:300]}
    except Exception as exc:
        return _json_error(
            f"evaluate_js failed: {exc}",
            error_name="EVALUATE_JS_FAILED",
            ca_next_action="verify_page_id_via_list_cdp_targets",
        )

    doc_url = data.get("documentURL") or data.get("locationHref") or ""
    # decode file:/// URLs to local path
    file_path_decoded = ""
    if doc_url.startswith("file:///"):
        from urllib.parse import unquote
        # file:///D:/path/to/file.html  → strip prefix, unquote
        raw_path = doc_url[len("file:///"):]
        file_path_decoded = unquote(raw_path).replace("/", "\\")
    matches_expected = None
    if expected_path:
        exp_norm = expected_path.replace("/", "\\").lower()
        actual_norm = file_path_decoded.lower()
        # accept partial suffix match (in case expected_path is relative)
        matches_expected = exp_norm in actual_norm or actual_norm.endswith(exp_norm)
    return json.dumps({
        "ok": True,
        "document_url": doc_url,
        "window_location": data.get("locationHref", ""),
        "base_uri": data.get("baseURI", ""),
        "file_path_decoded": file_path_decoded,
        "matches_expected": matches_expected,
        "expected_path": expected_path,
        "all_loaded_resources": data.get("resources", [])[:10],
        "ca_next_action": (
            "matches_expected_safe_to_edit_source" if matches_expected else
            ("source_mismatch_check_dist_or_public_mirror" if expected_path else
             "active_source_confirmed_proceed")
        ),
        "hint": (
            "If matches_expected=false → Electron loaded a DIFFERENT file than the one "
            "you're about to edit. Common cause: app loads dist/ or public/ mirror, "
            "not Source/. Check all_loaded_resources for the real path. Edit the loaded "
            "file OR rebuild dist from Source/."
        ) if not matches_expected else (
            "Active source confirmed. Safe to edit." if expected_path else
            "Active source URL captured."
        ),
    }, ensure_ascii=False)


# ─── V15 R43 NEW TOOL: run_project_test_script ────────────────────────────────

# --- CASE-2008 - destructive-script guard -----------------------------------
# On 2026-08-20 an agent deleted the wrong files through ``run_ps1_script``.
# Three independent holes lined up, and all three belong HERE rather than in the
# wrapper, because ``run_ps1_script`` and ``run_node_script`` are thin wrappers
# over ``run_project_test_script`` -- one gate therefore covers every caller and
# every runtime:
#
#   1. No dry run existed at all, so the blast radius was never previewable.
#   2. No backup was taken. The auto-backup gate in ``orchestrator_server.py``
#      (``_backup_gated_tools``) is a hardcoded set of five FILE tools --
#      delete_file / write_fix / write_fix_html / write_new_file /
#      apply_after_dry_run -- and a script that deletes files is not one of them.
#      NOTE for anyone reading the post-incident report: it blames
#      ``mutation_scope`` ("process" escaping the "file_artifact" backup). That
#      explanation is WRONG -- ``tool_mutation_scope`` is read only by
#      /api/tools, the catalog and the drift check; it gates nothing anywhere,
#      so editing the scope map would have prevented nothing.
#   3. The 4000-char stdout cap hid the damage while it happened: the run
#      produced 68,978 chars, so the lines naming .gitignore and README.md were
#      truncated away and the agent believed only temp files had gone.
#
# The over-deletion itself had a narrower and nastier cause than any of those:
# PowerShell SILENTLY IGNORES -Include / -Exclude when -LiteralPath is used, so
# ``Remove-Item -LiteralPath <dir> -Include *.tmp -Recurse`` removes the WHOLE
# directory. No error, no warning, exit code 0. The same silent-ignore applies
# to -Path without -Recurse and without a trailing wildcard. Those two shapes
# are refused outright rather than merely confirmed: the script does not do what
# it says it does, so acknowledging a count would only confirm a wrong intent.
#
# A script with no destructive match takes exactly the old path, so benign
# callers see no behaviour change at all.

# #37 (2026-09-25): where a shell COMMAND can start, in ONE place. Every
# statement-anchored row below, and the CASE-2008 footgun check, kept its own copy
# of ``(?:^\s*|[;|&{(]\s*)`` -- and every copy missed the same start: the argument
# of a shell introducer. ``powershell -c "ri x"``, ``cmd /c del x`` and ``bash -c
# "rm x"`` each delete, yet scored 0, because ``ri`` / ``del`` / ``rm`` sit after
# ``-c "`` or ``/c ``. Only an introducer's argument opens a command: the switch
# counts when a SHELL owns it (``grep -c rm f`` counts matches, it runs nothing),
# and a quote anywhere else is a quoted word (``git commit -m "move X"``).
_CMD_START = (r"(?:^\s*|[;|&{(]\s*|\b(?:powershell|pwsh|cmd|bash|sh|zsh)(?:\.exe)?\b"
              r"[^;|&\n]*?\s(?:-c|-Command|/c|/k)\s+[\"']?)")
# rm / del / ri / rd / rmdir / erase are all Remove-Item; the footgun check asks
# the same question as the remove_item row, so both read this one pattern.
_PS_REMOVE_AT_START = _CMD_START + r"(?:Remove-Item|ri|rm|rmdir|rd|del|erase)\b"

_DESTRUCTIVE_COMMON = [
    (r"(?i)\bgit\s+clean\b", "git_clean"),
    (r"(?i)\bgit\s+reset\s+--hard\b", "git_reset_hard"),
    (r"(?i)\bgit\s+checkout\s+--\s", "git_checkout_discard"),
    (r"(?i)\bgit\s+rm\b", "git_rm"),
]

# Destructive commands written as a STRING and handed to a shell. Any runtime can
# do this -- ``subprocess.run(["powershell","-c","Remove-Item -Recurse $d"])``,
# ``child_process.execSync("rm -rf " + d)``, ``& cmd /c rd /s /q ...`` -- so a
# scan scoped to the host language would see only harmless Python or JS and wave
# the call through. Flagging ``subprocess.run`` / ``exec`` itself was considered
# and rejected: it fires on the many benign scripts that shell out for
# non-destructive work, and a guard that trips on everything gets acknowledged
# reflexively, which is worse than no guard. Matching the destructive COMMAND
# inside whatever string carries it keeps the signal where it belongs.
_DESTRUCTIVE_EMBEDDED_SHELL = [
    (r"(?i)\bRemove-Item\b", "embedded_remove_item"),
    (r"(?i)\bClear-Content\b", "embedded_clear_content"),
    (r"(?i)\brm\s+-[a-zA-Z]*[rf]", "embedded_rm_rf"),
    (r"(?i)\brmdir\s+/s\b", "embedded_rmdir_s"),
    (r"(?i)\brd\s+/s\b", "embedded_rd_s"),
    (r"(?i)\bdel\s+/[sq]\b", "embedded_del_s"),
    (r"(?i)\bshred\b", "embedded_shred"),
]

# An OVERWRITE is destructive too -- Hard Rules name "mass overwrite" beside delete.
# Measured 2026-09-24: 26 deployed SKILL.md were replaced through run_ps1_script by
# ``Copy-Item $src $dst -Force`` and the scan answered destructive_count=0, so no
# count was reviewed and no pre-run backup was taken. A static read cannot tell
# whether a destination already exists, so flagging every copy would trip on the
# many copies into a fresh directory, and a guard that trips on everything gets
# acknowledged reflexively. The objective signal is the FLAG whose only job is to
# switch an overwrite guard off -- -Force past a read-only target, /y past cmd's
# "Overwrite?" prompt, dirs_exist_ok past copytree's FileExistsError, cp -f -- or
# to delete what the source lacks (robocopy /MIR /PURGE /MOV /MOVE, rsync --delete
# / --remove-source-files). A plain copy, Set-Content or Out-File stays uncounted:
# that is an ordinary write -- and so is a truncating ``>`` redirect, the same
# write in shell spelling (#37 left it uncounted on purpose, for the same reason).
# Known limit, stated rather than hidden: a copy that overwrites WITHOUT saying so
# (``Copy-Item -Recurse`` into an existing tree) is still invisible to a source
# scan. Runtime-independent, like the embedded list: every one of these can arrive
# as a string handed to a shell.
_DESTRUCTIVE_OVERWRITE = [
    (r"(?i)\bCopy-Item\b[^\n]*\s-Fo(?:r(?:ce?)?)?\b", "copy_item_force"),
    (r"(?i)" + _CMD_START + r"(?:copy|cp|cpi)\s[^\n]*\s-Fo(?:r(?:ce?)?)?\b", "copy_item_force"),
    (r"(?i)\bx?copy\b[^\n]*\s/y\b", "copy_overwrite_y"),
    (r"(?i)\brobocopy\b[^\n]*\s/(?:MIR|PURGE|MOVE?)\b", "robocopy_purge"),
    (r"\brsync\b[^\n]*\s--(?:delete|remove-source-files)", "rsync_delete"),
    (r"\bcp\s+(?:-[a-zA-Z]*f|--force\b|--remove-destination\b)", "cp_force"),
    (r"\bcopytree\s*\([^\n]*\bdirs_exist_ok\s*=\s*True\b", "copytree_merge"),
]

# #26 / #30 (2026-09-24): the Python-level destructive calls, in ONE table. The
# python regex rows below are DERIVED from it -- runtime "any" still reads
# ``python -c "..."`` as shell text -- and `_py_destructive_lines` judges a
# python body against the same names as code.
_PY_DESTRUCTIVE_CALLEES = {
    "rmtree": "rmtree",
    "remove": "os_remove",
    "unlink": "os_remove",
    "rmdir": "os_remove",
    "removedirs": "os_remove",
    "truncate": "os_remove",
}

# PowerShell aliases matter as much as the cmdlet names: rm / del / ri / rd /
# rmdir / erase are all Remove-Item, and a guard that only knew "Remove-Item"
# would wave through ``rm -Recurse -Force $dir``.
#
# CASE-2008 follow-up, measured 2026-09-20 (Anh/ClauLTN): the statement-start
# anchor must allow LEADING WHITESPACE. The scan below is per-LINE
# (``re.search(pattern, line)``), so a bare ``^`` demanded column 0 -- and every
# real script indents its statements inside ``if`` / ``foreach`` / ``try``. An
# indented ``Move-Item`` or ``clc`` therefore scored destructive_count=0, ran
# with ``confirm_destructive`` unspent and took no pre-run backup: CASE-2008
# again, one indent level deeper. ``Remove-Item`` and ``Clear-Content`` were
# rescued by their bare-word companions below; ``clc``, ``Move-Item``/``mi``/
# ``move``/``mv`` and the whole ``sh`` list had none, so ``Move-Item`` gets one
# too. ``^\s*`` is strictly widening: a leading ``#`` still fails to match, so
# a commented-out line stays uncounted.
_DESTRUCTIVE_PATTERNS = {
    "powershell": [
        (r"(?i)" + _PS_REMOVE_AT_START, "remove_item"),
        (r"(?i)\bRemove-Item(?:Property)?\b", "remove_item"),
        (r"(?i)\bClear-Content\b", "clear_content"),
        (r"(?i)" + _CMD_START + r"clc\b", "clear_content"),
        (r"(?i)" + _CMD_START + r"(?:Move-Item|mi|move|mv)\b", "move_item"),
        (r"(?i)\bMove-Item\b", "move_item"),
        (r"(?i)\[(?:System\.)?IO\.(?:File|Directory)\]::Delete", "dotnet_delete"),
        (r"(?i)\.Delete\s*\(", "dotnet_instance_delete"),
        (r"(?i)\b(?:Format-Volume|Clear-Disk|Remove-Partition|Initialize-Disk)\b", "disk_destructive"),
    ],
    # Receiver-agnostic on purpose. Anchoring these on ``fs.`` was a MEASURED
    # false negative: ``require("fs").rmSync(dir)`` has a ``)`` before the dot,
    # so an fs-anchored pattern let the call straight through and it EXECUTED in
    # a probe. The same hole covers ``const {rmSync} = require("fs")`` and any
    # alias. Matching the method name alone over-matches (a custom
    # ``cache.unlink()`` counts), which is the correct direction here: a false
    # positive costs one extra call carrying confirm_destructive, a false
    # negative costs files.
    "node": [
        (r"\brm(?:Sync)?\s*\(", "fs_rm"),
        (r"\bunlink(?:Sync)?\s*\(", "fs_unlink"),
        (r"\brmdir(?:Sync)?\s*\(", "fs_rmdir"),
        (r"\btruncate(?:Sync)?\s*\(", "fs_truncate"),
        (r"\brimraf\b", "rimraf"),
    ],
    # Derived from _PY_DESTRUCTIVE_CALLEES, one row per kind.
    "python": [
        (r"\b(?:%s)\s*\(" % "|".join(
            name for name, of_kind in _PY_DESTRUCTIVE_CALLEES.items() if of_kind == kind),
         kind)
        for kind in dict.fromkeys(_PY_DESTRUCTIVE_CALLEES.values())
    ],
    "sh": [
        (_CMD_START + r"rm\b", "rm"),
        (_CMD_START + r"rmdir\b", "rmdir"),
        (_CMD_START + r"(?:mv|dd|shred|mkfs\S*)\b", "sh_destructive"),
        (r"-delete\b", "find_delete"),
    ],
}


# B028 2026-08-22 (Anh/Clau): PowerShell here-string PROSE is data, not commands.
#
# Reported and reproduced: a commit message inside @'...'@ had two of its
# SENTENCES classified as destructive commands ("khong Remove-Item -Recurse tren
# thu muc" -> remove_item; "git rm --cached -> 580/583 VAN NGUYEN" -> git_rm).
# `confirm_destructive` is a number the caller has to reproduce and it is supposed
# to mean "destructive statements I reviewed"; prose inflated it, so documenting a
# deletion honestly RAISED the count, and editing a word moved it and invalidated
# an acknowledgement that was correct when written. That is a correctness defect
# in what the number MEANS, not a cosmetic false positive.
#
# The report asked to skip here-string contents outright. That is NOT what this
# does, because it would reopen the hole _DESTRUCTIVE_EMBEDDED_SHELL exists to
# close -- `powershell -Command @'<nl>Remove-Item -Recurse $t<nl>'@` IS a
# deletion, written as a string. So:
#
#   * A here-string counts as DATA only when the body contains no way to evaluate
#     a string as code. Any eval route present (iex, -Command, cmd /c, node -e,
#     python -c, subprocess, &, Start-Process, [scriptblock]::Create, a redirect
#     into a .ps1) and here-strings are scanned exactly as before B028.
#   * Findings inside a data here-string are REPORTED as `prose_matches`, never
#     dropped silently. They only leave `count`, which is the number with meaning.
#   * Malformed input fails SAFE: an unterminated here-string, an opener that is
#     not at end of line, or an indented terminator all leave the region
#     executable. Guessing "this is prose" is the direction that costs files.
#
# Footguns get the same exclusion for a sharper reason than the count: a footgun is
# refused with retryable=False and NO confirm path at all, so one sentence
# mentioning -Include and -LiteralPath could make a script permanently unrunnable.

# Each of these evaluates a string. Deliberately broad -- a false positive here
# costs one confirm_destructive round-trip; a false negative costs files.
_STRING_EVAL_ROUTES = (
    r"(?i)\b(?:iex|Invoke-Expression)\b",
    r"(?i)\bInvoke-Command\b",
    r"(?i)\[\s*scriptblock\s*\]\s*::\s*Create",
    r"(?i)\bStart-Process\b",
    r"(?i)\b(?:powershell|pwsh)(?:\.exe)?\b[^\r\n]*?-(?:c|Command|EncodedCommand)\b",
    r"(?i)\bcmd(?:\.exe)?\b[^\r\n]*?/c\b",
    r"(?i)\bnode(?:\.exe)?\b[^\r\n]*?-e\b",
    r"(?i)\b(?:python|python3|py)(?:\.exe)?\b[^\r\n]*?-c\b",
    r"(?i)\bsubprocess\b",
    r"(?i)\bexecSync\b|\bspawnSync\b|\bchild_process\b",
    r"(?i)\bbash\b[^\r\n]*?-c\b",
    # Writing the string out to a script file and running it later is an eval
    # route too, just a slower one. B028c: the redirect form checks WHERE the
    # string lands; `Set-Content` / `Out-File` are checked for the same thing by
    # `_writes_string_to_executable`, because they used to match on the cmdlet
    # name alone and so counted `Out-File msg.txt` -- a data write -- as a way to
    # execute the here-string.
    r"(?i)>>?\s*['\"]?[^\r\n]*\.(?:ps1|cmd|bat|sh|js|py)\b",
    # A bare call operator on its own token: `& $s`. Not `&&`, which is a chain.
    r"(?<!&)&(?!&)\s*[\$\(]",
)


_SCRIPT_TARGET_EXTS = ("ps1", "cmd", "bat", "sh", "js", "py")

# A quoted string, or a bare token, ending in a short extension. This is what "a
# target this parser can read" means; anything else is unreadable by design.
_TARGET_LITERAL_RE = re.compile(
    r"'([^']*\.[A-Za-z0-9]{1,10})'"
    r'|"([^"]*\.[A-Za-z0-9]{1,10})"'
    r"|(?<![\w'\"])([^\s'\"();|]*\.[A-Za-z0-9]{1,10})(?![\w.])"
)
_STRING_WRITE_CMDLET_RE = re.compile(r"(?i)\b(?:Set-Content|Out-File|Add-Content)\b")


def _writes_string_to_executable(script: str) -> bool:
    """True when the body writes a string to something that can later be RUN.

    B028c 2026-08-22. `Set-Content` / `Out-File` used to be eval routes on the
    cmdlet name alone, while the `>` redirect right beside them in
    `_STRING_EVAL_ROUTES` checked the target extension. Same alternation, two
    halves that disagreed -- so a prose here-string written to `msg.txt` came back
    as destructive, and `git commit -F msg.txt` written the obvious way was
    unrunnable.

    Three answers, because two are not enough:
      * a literal target with a script extension -> True (the slow eval route);
      * a literal target with any other extension -> False (a data write);
      * NO readable literal target -- a bare `$dest`, a `(Join-Path ...)`, a
        cmdlet with no target at all -> True. FAIL SAFE: guessing "that is
        probably a .txt" is the direction that costs files.

    Judged per line, so one data write does not excuse a script write elsewhere in
    the same body.
    """
    for line in split_lf(script):
        match = _STRING_WRITE_CMDLET_RE.search(line)
        if not match:
            continue
        rest = line[match.end():]
        targets = [group.lower() for found in _TARGET_LITERAL_RE.findall(rest)
                   for group in found if group]
        if not targets:
            return True  # cannot read the target -> assume it is runnable
        for target in targets:
            if target.rsplit(".", 1)[-1] in _SCRIPT_TARGET_EXTS:
                return True
    return False


def _ps_here_string_line_span(script: str) -> set:
    """1-based line numbers that sit INSIDE a PowerShell here-string body.

    The opener (@' / @") and terminator ('@ / "@) lines are NOT included: the
    opener line can legally carry a command before it, and the terminator line can
    carry one after it, so both stay scannable.

    PowerShell 5.1 syntax, followed exactly -- loose parsing here is what turns a
    guard into a bypass:
      * an opener is only an opener when @' or @" ENDS the line (trailing
        whitespace tolerated);
      * a terminator must be at the START of a line. An indented terminator is a
        parse error in 5.1, so it does not close anything;
      * an unterminated here-string yields an EMPTY span, leaving every line
        executable. Fail safe.
    """
    lines = split_lf(script)
    inside = set()
    index = 0
    while index < len(lines):
        opener = re.search(r"@(['\"])[ \t]*$", lines[index])
        if not opener:
            index += 1
            continue
        quote = opener.group(1)
        terminator = re.compile(r"^" + re.escape(quote) + r"@")
        body_start = index + 1
        close_at = None
        for probe in range(body_start, len(lines)):
            if terminator.match(lines[probe]):
                close_at = probe
                break
        if close_at is None:
            break
        for line_no in range(body_start, close_at):
            inside.add(line_no + 1)
        index = close_at + 1
    return inside


def _body_runs_strings(script: str) -> bool:
    """True when the body's TEXT shows any way to evaluate a string as code.

    The one answer B028's here-string prose and the python AST scan both ask for,
    so the two cannot drift apart on what an eval route is.
    """
    if any(re.search(pattern, script) for pattern in _STRING_EVAL_ROUTES):
        return True
    # B028c: the cmdlet write forms, which need a target check the pattern list
    # cannot express (see `_writes_string_to_executable`).
    return _writes_string_to_executable(script)


def _ps_prose_line_span(script: str, runtime: str) -> set:
    """Here-string lines that may be treated as data for THIS body.

    Empty when the runtime is not PowerShell-reachable (here-strings are
    PowerShell syntax; `@'` in a JS or Python body is not a data delimiter), and
    empty when the body contains any way to evaluate a string as code.
    """
    if runtime not in ("powershell", "any"):
        return set()
    span = _ps_here_string_line_span(script)
    if not span or _body_runs_strings(script):
        return set()
    return span


def _ps_join_continuations(script: str) -> str:
    """Fold PowerShell backtick line continuations so a split command is seen whole.

    ``Remove-Item -LiteralPath $d `` + newline + ``-Include *.tmp`` is ONE
    command written over two lines, and a purely line-based footgun check would
    miss it -- which is exactly the shape a CA writing a long command produces.
    """
    return re.sub(r"`[ \t]*\r?\n[ \t]*", " ", script)


# A python body can turn a string into code only through one of these, on top of
# the text routes every runtime shares (`_body_runs_strings`). Present, and every
# textual mention counts again -- fail safe, the old answer. Broad on purpose: a
# write counts, because a string written to a .py and imported is the slow route
# to the same exec.
_PY_STRING_RUN_MODULES = frozenset({
    "subprocess", "runpy", "importlib", "code", "codeop", "pty", "multiprocessing",
    "pickle", "marshal", "ctypes",
})
_PY_STRING_RUN_CALLEES = frozenset({
    "exec", "eval", "compile", "execfile", "__import__", "system", "popen",
    "startfile", "loads", "import_module", "run_path", "run_module", "write",
    "write_text", "write_bytes", "writelines", "create_subprocess_exec",
    "create_subprocess_shell",
})
# #37: the calls that hand their arguments to an OS process as a command line (plus
# every exec*/spawn* name, matched by prefix at the call). Their literal arguments
# are rescanned as shell text by ``shell_literal_kinds``.
_PY_PROCESS_CALLEES = frozenset({
    "system", "popen", "Popen", "run", "call", "check_call", "check_output",
    "getoutput", "getstatusoutput", "startfile", "create_subprocess_exec",
    "create_subprocess_shell",
})
# ``.remove`` is the one destructive name that ordinary containers share. Only a
# receiver PROVEN to live in memory -- a list/set display, a name bound to nothing
# else, an attribute of ``sys`` -- is left uncounted: sftp.remove and fs.remove
# delete files, so a receiver the scan cannot prove never passes.
_PY_CONTAINER_BUILDERS = frozenset({"list", "set", "sorted", "deque"})
_PY_CONTAINER_NODES = ("List", "Set", "ListComp", "SetComp")
# A destructive function handed on as a VALUE deletes just the same:
# map(os.unlink, paths), atexit.register(rmtree, d), handlers = [shutil.rmtree].
_PY_VALUE_PARENTS = ("Call", "keyword", "Assign", "AnnAssign", "NamedExpr", "Return",
                     "Lambda", "List", "Tuple", "Set", "Dict")


def _py_destructive_lines(body: str) -> dict | None:
    """Judge a python body's destructive calls on its AST; None when it does not parse.

    #26 / #30 (2026-09-24). The python regex rows read the raw text and were wrong
    both ways: ``x = "path.unlink()"``, a comment and ``sys.path.remove(...)`` each
    counted, while ``from os import remove as rm; rm(p)`` and
    ``map(os.unlink, paths)`` did not. Answers::

        counted       {line: [kind, ...]}  a destructive call, or one passed on as a value
        in_memory     {line, ...}          ``.remove`` on a receiver proven in memory
        runs_strings  bool                 the body can run a string as python code
    """
    import ast

    try:
        tree = ast.parse(body)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    parents: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    # Pass 1: what every name is bound to, and what each from-import brings in.
    # Collected module-wide, so a name is "in memory" only if EVERY binding says so.
    imported: dict[str, str] = {}
    bindings: dict[str, list] = {}
    runs_strings = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                runs_strings = runs_strings or root in _PY_STRING_RUN_MODULES
                bindings.setdefault(alias.asname or root, []).append(
                    ("import", alias.name if alias.asname else root))
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in _PY_STRING_RUN_MODULES:
                runs_strings = True
            for alias in node.names:
                imported[alias.asname or alias.name] = alias.name
                bindings.setdefault(alias.asname or alias.name, []).append(("other", None))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            parent = parents.get(node)
            value = getattr(parent, "value", None)
            if isinstance(parent, (ast.Assign, ast.AnnAssign)) and value is not None:
                bindings.setdefault(node.id, []).append(("value", value))
            else:  # for / with / unpacking / augmented / walrus: unknown
                bindings.setdefault(node.id, []).append(("other", None))
        elif not isinstance(node, ast.alias):
            # def / class / except-as / match capture / a parameter
            name = node.arg if isinstance(node, ast.arg) else getattr(node, "name", None)
            if isinstance(name, str):
                bindings.setdefault(name, []).append(("other", None))

    def callee_names(func) -> set:
        if isinstance(func, ast.Attribute):
            return {func.attr}
        if isinstance(func, ast.Name):
            return {func.id, imported.get(func.id, func.id)}
        return set()

    def container(expr) -> bool:
        if type(expr).__name__ in _PY_CONTAINER_NODES:
            return True
        return isinstance(expr, ast.Call) and bool(
            callee_names(expr.func) & _PY_CONTAINER_BUILDERS)

    def proven_in_memory(receiver) -> bool:
        if container(receiver):
            return True
        if isinstance(receiver, ast.Name):
            hows = bindings.get(receiver.id, [])
            return bool(hows) and all(how == "value" and container(v) for how, v in hows)
        root = receiver
        while isinstance(root, ast.Attribute):
            root = root.value
        if root is receiver or not isinstance(root, ast.Name):
            return False
        hows = bindings.get(root.id, [])
        return bool(hows) and all(how == ("import", "sys") for how in hows)

    def shell_literal_kinds(call) -> list:
        # #37 (2026-09-25): a call that hands a COMMAND to a process carries it as
        # literal strings -- ``os.system("del x")``, ``subprocess.run(["cmd", "/c",
        # "rd", "/s", "/q", d])``. The text rows read neither: shell rows are not
        # python rows, and a list argv puts quotes and commas between the words.
        # Rejoined as the command line it becomes, it is judged by the SAME matcher
        # a shell command gets. A word that is not a literal becomes "_", so a
        # command built at run time stays unjudged (the preview calls the count a
        # lower bound).
        words: list[str] = []
        for arg in [*call.args, *(k.value for k in call.keywords
                                   if k.arg in ("args", "cmd", "command"))]:
            items = arg.elts if isinstance(arg, (ast.List, ast.Tuple)) else [arg]
            words += [item.value if isinstance(item, ast.Constant)
                      and isinstance(item.value, str) else "_" for item in items]
        if all(word == "_" for word in words):
            return []
        return [kind for finding in _scan_destructive_script(" ".join(words), "any")["findings"]
                for kind in finding["kinds"]]

    # Pass 2: every call, and every destructive function handed on as a value.
    funcs = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    counted: dict[int, list[str]] = {}
    in_memory: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            names = callee_names(node.func)
            if (names & _PY_STRING_RUN_CALLEES
                    or any(n.startswith(("exec", "spawn")) for n in names)
                    or ("print" in names and any(k.arg == "file" for k in node.keywords))):
                runs_strings = True
            if (names & _PY_PROCESS_CALLEES
                    or any(n.startswith(("exec", "spawn")) for n in names)):
                shell_kinds = shell_literal_kinds(node)
                if shell_kinds:
                    counted.setdefault(node.lineno, []).extend(shell_kinds)
            target = node.func
        elif (isinstance(node, (ast.Attribute, ast.Name)) and isinstance(node.ctx, ast.Load)
              and id(node) not in funcs
              and type(parents.get(node)).__name__ in _PY_VALUE_PARENTS):
            target = node
            if isinstance(node, ast.Attribute):
                names = {node.attr}
            else:  # a bare name is a destructive function only if imported as one
                names = {imported[node.id]} if node.id in imported else set()
        else:
            continue
        hits = [name for name in sorted(names) if name in _PY_DESTRUCTIVE_CALLEES]
        if not hits:
            continue
        line = target.end_lineno if isinstance(target, ast.Attribute) else target.lineno
        if (hits == ["remove"] and isinstance(target, ast.Attribute)
                and proven_in_memory(target.value)):
            in_memory.add(line)
            continue
        counted.setdefault(line, []).append(_PY_DESTRUCTIVE_CALLEES[hits[0]])
    return {"counted": counted, "in_memory": in_memory, "runs_strings": runs_strings}


# #37 (2026-09-25): decode the carriers that hide a shell command from a per-line
# scan, so the ONE matcher can judge what they actually run.
#
#   * a PowerShell -EncodedCommand / -enc / -ec / -e payload is base64 of UTF-16LE
#     text -- decoded and rescanned;
#   * a node child_process call (exec/execSync/spawn/.../fork) carries its command
#     as string literals, one per argv word -- rejoined and rescanned, the JS twin
#     of the python AST's shell_literal_kinds (node has no parser here);
#   * an ssh line runs its trailing argument on the far host -- options and the host
#     stripped, the remainder rescanned.
#
# What CANNOT be read -- a payload held in a variable, spawn(cmd), an ssh command
# built at run time, a shell string nested one level further down inside an
# interpreter's argument (python -c "os.system('del x')" -- the outer -c holds
# python, whose os.system in turn holds the shell), or an -ArgumentList wrapper
# (Start-Process cmd -ArgumentList "/c del x") -- decodes to nothing here and is
# left uncounted, the same lower bound the preview already declares. Runtime-built
# exec/eval is that case by construction and is NOT patched here: a static scan
# cannot judge a string it never sees. Each decoded carrier is rescanned with
# _scan_carriers=False, so the recursion is one level deep.
_ENCODED_CMD_RE = re.compile(
    r"(?i)\b(?:powershell|pwsh)(?:\.exe)?\b[^\r\n]*?\s-(?:encodedcommand|enc|ec|e)\s+"
    r"([A-Za-z0-9+/]{8,}={0,2})\b")
_NODE_SHELL_CALL_RE = re.compile(
    r"(?i)\b(?:child_process\.)?(?:exec|execSync|spawn|spawnSync|execFile|"
    r"execFileSync|fork)\s*\(")
_STRING_LITERAL_RE = re.compile(
    r'"((?:[^"\\]|\\.)*)"' r"|'((?:[^'\\]|\\.)*)'" r"|`([^`]*)`")
_SSH_LINE_RE = re.compile(r"(?im)\bssh(?:\.exe)?[ \t]+([^\r\n]+)$")
_SSH_TOKEN_RE = re.compile(r'"[^"]*"|\'[^\']*\'|\S+')
_SSH_VALUE_OPTS = frozenset("bcDEeFIiJLlmOopQRSWw")


def _line_of_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _balanced_paren(text: str, open_idx: int) -> str:
    """The text between ``(`` at open_idx and its matching ``)``, quotes respected.

    An unbalanced open takes the rest of the body: a truncated call still gets its
    literals scanned rather than silently skipped.
    """
    depth = 0
    quote = None
    i = open_idx
    while i < len(text):
        ch = text[i]
        if quote is not None:
            if ch == quote and text[i - 1] != "\\":
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
        i += 1
    return text[open_idx + 1:]


def _decode_encoded_command(b64: str) -> str:
    """A -EncodedCommand payload decoded to its command text, or "" if unreadable.

    UTF-16LE per PowerShell's contract; a payload that is not clean base64 or does
    not decode to mostly printable text is treated as unreadable, so a variable or
    a random-looking token is a lower bound, not a false positive.
    """
    import base64
    try:
        raw = base64.b64decode(b64 + "=" * (-len(b64) % 4), validate=True)
    except Exception:
        return ""
    try:
        text = raw.decode("utf-16-le")
    except Exception:
        return ""
    if not text:
        return ""
    printable = sum(ch.isprintable() or ch.isspace() for ch in text)
    return text if printable >= 0.8 * len(text) else ""


def _ssh_remote_command(rest: str) -> str:
    """The remote command an ssh line runs: options and the one host token stripped.

    ssh's own value-taking short options consume their value so the host is found
    correctly; the first bare token is the host; everything after it is the command
    (a surrounding quote unwrapped). No command -> "" and nothing is flagged.
    """
    tokens = _SSH_TOKEN_RE.findall(rest)
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("-"):
            break  # the first non-option token is the host
        if len(token) == 2 and token[1] in _SSH_VALUE_OPTS:
            i += 2  # a short option that takes a value
        else:
            i += 1
    command = " ".join(tokens[i + 1:]).strip()
    if len(command) >= 2 and command[0] in "\"'" and command[-1] == command[0]:
        command = command[1:-1]
    return command


def _shell_command_carriers(body: str, runtime: str):
    """Yield (source_line, decoded_command_text) for each carrier in the body."""
    for match in _ENCODED_CMD_RE.finditer(body):
        decoded = _decode_encoded_command(match.group(1))
        if decoded:
            yield _line_of_offset(body, match.start()), decoded
    if runtime in ("node", "any"):
        for match in _NODE_SHELL_CALL_RE.finditer(body):
            region = _balanced_paren(body, match.end() - 1)
            words = [group for found in _STRING_LITERAL_RE.findall(region)
                     for group in found if group]
            if words:
                yield _line_of_offset(body, match.start()), " ".join(words)
    if runtime != "python":
        for match in _SSH_LINE_RE.finditer(body):
            command = _ssh_remote_command(match.group(1))
            if command:
                yield _line_of_offset(body, match.start()), command


# Fix E (2026-09-25): scan the lines the INTERPRETER runs. python and PowerShell
# also end a line at a lone CR, so a body split on LF only hid what they run
# (measured: each deleted a scratch dir at count=0 -- python
# `import shutil<CR>shutil.rmtree(d)`, PowerShell `Write-Output a<CR>ri -r -fo d`,
# and a here-string closed by `x<CR>'@`). bash reads a CR as part of a word and
# node's rows are not line-anchored, so both keep the body as written.
_CR_ENDS_A_LINE = ("python", "powershell", "any")


def _interpreter_newlines(script: str, runtime: str) -> str:
    """The body with every line end its interpreter honours spelled as LF."""
    if runtime not in _CR_ENDS_A_LINE:
        return script
    return script.replace("\r\n", "\n").replace("\r", "\n")


def _cmd_cr_joined_lines(raw: str) -> list:
    """(line in the LF view, text cmd runs) for each line holding a lone CR.

    cmd DELETES a CR, so `rd<CR>/s /q d` runs as `rd/s /q d` (measured), the
    opposite of the split the LF view makes; run_project_command reaches both
    interpreters, so "any" reads each such line both ways.
    """
    joined = []
    line_no = 1
    for line in split_lf(raw):
        parts = line.split("\r")
        if len(parts) > 1:
            joined.append((line_no, "".join(parts)))
        line_no += len(parts)
    return joined


def _scan_destructive_script(script_content: str, runtime: str, _scan_carriers: bool = True) -> dict:
    """Classify a script body: destructive matches, silent-ignore footguns, warnings.

    Deliberately matches over the RAW body without stripping comments or string
    literals. That over-matches (a comment mentioning Remove-Item counts), and
    that is the safe direction: a false positive costs one extra call carrying
    ``confirm_destructive``, whereas a false negative costs files. Parsing
    PowerShell well enough to know a token sits inside a string is not something
    a guard should be betting the filesystem on.

    A python body is the exception, because Python ships its own parser: its
    Python-level rows are judged on the AST (``_py_destructive_lines``), which is
    stricter than the text in BOTH directions. A body that does not parse keeps
    the text scan.
    """
    raw = script_content or ""
    body = _interpreter_newlines(raw, runtime)
    # runtime="any" scans the union of every runtime's patterns. It exists for
    # run_project_command, which is not one interpreter: the SAME tool runs
    # `rd /s /q`, `node -e "fs.rmSync(...)"` and `python -c "shutil.rmtree(...)"`,
    # and its shell_mode says nothing about which interpreter the command text
    # invokes. Measured: naming a single runtime there missed fs.rmSync,
    # fs.unlinkSync, shutil.rmtree and os.remove -- four ways to delete a tree,
    # unguarded, on the surface that already had one incident. Over-matching in
    # the other direction costs one extra confirm_destructive round-trip.
    if runtime == "any":
        selected: list = []
        for runtime_patterns in _DESTRUCTIVE_PATTERNS.values():
            selected.extend(runtime_patterns)
    else:
        selected = list(_DESTRUCTIVE_PATTERNS.get(runtime, []))
    patterns = (selected
                + list(_DESTRUCTIVE_COMMON)
                + list(_DESTRUCTIVE_EMBEDDED_SHELL)
                + list(_DESTRUCTIVE_OVERWRITE))
    # #26/#30: on a python body that parses, the AST owns the Python rows. Their
    # regex stays only to REPORT a mention the AST did not count -- or to count it
    # after all, once the body has a way to run a string as code. The other lists
    # stay text on purpose: they are strings handed to a shell.
    py = _py_destructive_lines(body) if runtime == "python" else None
    py_rows = _DESTRUCTIVE_PATTERNS["python"]
    runs_strings = False
    if py is not None:
        patterns = [row for row in patterns if row not in py_rows]
        runs_strings = py["runs_strings"] or _body_runs_strings(body)
    # One finding per LINE, not per (line, kind). ``confirm_destructive`` is a
    # number the caller has to reproduce, so it has to mean "destructive
    # statements I reviewed" -- and a line is the unit a reader counts. Keying on
    # (line, kind) made a single ``Remove-Item`` score 2, because it matches both
    # the powershell pattern and the embedded-shell one; the caller was then asked
    # to confirm 2 deletions for 1 command. Worse, the number moved whenever the
    # pattern list grew, silently invalidating acknowledgements that were correct
    # when they were written. All matching kinds are kept in ``kinds`` so nothing
    # is lost from the report.
    # B028: lines inside a here-string that this body has no way to EXECUTE are
    # prose. They are still reported (`prose_matches`) so nothing is hidden, but
    # they leave `count` -- the number the caller has to reproduce, which must mean
    # "destructive statements", not "sentences mentioning one".
    prose_lines = _ps_prose_line_span(body, runtime)
    findings: list[dict] = []
    prose_matches: list[dict] = []
    for line_no, line in enumerate(split_lf(body), 1):
        kinds = [kind for pattern, kind in patterns if re.search(pattern, line)]
        text_kinds: list[str] = []
        why_not = ""
        if py is not None:
            kinds += py["counted"].get(line_no, [])
            text_kinds = [kind for pattern, kind in py_rows if re.search(pattern, line)]
            if text_kinds and not kinds:
                if line_no in py["in_memory"]:
                    why_not = ("`.remove` on a list, a set or an attribute of sys: it "
                               "edits memory, not a file")
                elif runs_strings:
                    kinds = text_kinds
                else:
                    why_not = ("inside a string literal or a comment, and this script "
                               "has no way to run a string as Python code, so the "
                               "match is data, not a call")
        if not kinds and not why_not:
            continue
        deduped: list[str] = []
        for kind in kinds or text_kinds:
            if kind not in deduped:
                deduped.append(kind)
        record = {
            "line": line_no,
            "kind": deduped[0],
            "kinds": deduped,
            "text": line.strip()[:200],
        }
        if why_not:
            record["why_not_counted"] = why_not
            prose_matches.append(record)
            continue
        if line_no in prose_lines:
            record["why_not_counted"] = (
                "inside a PowerShell here-string and this script contains no way to "
                "evaluate a string as code, so the line is data (e.g. a commit "
                "message), not a command"
            )
            prose_matches.append(record)
            continue
        findings.append(record)

    # #37 (2026-09-25): a destructive command can ride INSIDE a carrier the primary
    # per-line scan reads as opaque -- a base64 -EncodedCommand payload, the string
    # arguments of a node child_process call, the remote command of an ssh line. Each
    # is decoded back to the shell text it becomes and judged by the SAME matcher via
    # a bounded re-scan (_scan_carriers=False stops it recursing). A carrier built at
    # run time (a variable payload, spawn(cmd)) decodes to nothing and stays a lower
    # bound, exactly like a runtime-built argv. Skipped for a parsed python body: its
    # AST (shell_literal_kinds) already rejoins process-call literals and would
    # double-count. Findings merge by line so one command is still one count.
    if _scan_carriers and (runtime != "python" or py is None):
        by_line = {record["line"]: record for record in findings}
        body_lines = split_lf(body)
        carriers = list(_shell_command_carriers(body, runtime))
        if runtime == "any":
            carriers += _cmd_cr_joined_lines(raw)
        for src_line, decoded in carriers:
            if src_line in prose_lines:
                continue
            sub = _scan_destructive_script(decoded, "any", _scan_carriers=False)
            sub_kinds: list[str] = []
            for finding in sub["findings"]:
                for kind in finding["kinds"]:
                    if kind not in sub_kinds:
                        sub_kinds.append(kind)
            if not sub_kinds:
                continue
            existing = by_line.get(src_line)
            if existing is not None:
                for kind in sub_kinds:
                    if kind not in existing["kinds"]:
                        existing["kinds"].append(kind)
                continue
            text = (body_lines[src_line - 1].strip()[:200]
                    if 0 < src_line <= len(body_lines) else "")
            record = {"line": src_line, "kind": sub_kinds[0], "kinds": sub_kinds, "text": text}
            findings.append(record)
            by_line[src_line] = record
        findings.sort(key=lambda record: record["line"])

    footguns: list[dict] = []
    warnings: list[str] = []
    # "any" keeps the PowerShell footgun analysis: the -Include/-LiteralPath
    # shape IS CASE-2008, and run_project_command reaches powershell.
    if runtime in ("powershell", "any"):
        folded = _ps_join_continuations(body)
        # B028: folding continuations shifts nothing here (a here-string body has no
        # backtick continuations to fold), but recompute the span against the FOLDED
        # text so the line numbers line up with the loop below.
        folded_prose = _ps_prose_line_span(folded, runtime)
        for line_no, line in enumerate(split_lf(folded), 1):
            # A footgun is refused with retryable=False and NO confirm path, so a
            # sentence merely DESCRIBING the CASE-2008 shape would make a script
            # permanently unrunnable. Prose cannot delete anything.
            if line_no in folded_prose:
                continue
            is_remove = re.search(
                r"(?i)" + _PS_REMOVE_AT_START, line
            ) or re.search(r"(?i)\bRemove-Item\b", line)
            if not is_remove:
                continue
            if not re.search(r"(?i)-(?:include|exclude)\b", line):
                continue
            snippet = line.strip()[:300]
            if re.search(r"(?i)-literalpath\b", line):
                footguns.append({
                    "line": line_no,
                    "kind": "include_ignored_with_literalpath",
                    "text": snippet,
                    "why": ("PowerShell ignores -Include/-Exclude when -LiteralPath is "
                            "given. The filter has NO effect and the whole target is "
                            "removed, silently, with exit code 0."),
                    "fix": ("Use -Path with a trailing wildcard (e.g. -Path 'C:\\dir\\*') "
                            "plus -Include, or enumerate with Get-ChildItem -File -Filter "
                            "and pipe explicit items into Remove-Item."),
                })
            elif not re.search(r"(?i)-recurse\b", line) and not re.search(
                r"\*\s*[\'\"]?(?:\s|$|[;|)])", line
            ):
                footguns.append({
                    "line": line_no,
                    "kind": "include_ignored_without_recurse_or_wildcard",
                    "text": snippet,
                    "why": ("-Include is only honoured when -Path ends in a wildcard or "
                            "-Recurse is present; otherwise it is ignored and the path "
                            "itself is removed."),
                    "fix": "Append \\* to -Path, or add -Recurse, or pipe explicit items.",
                })
        if findings and re.search(r"(?i)-ErrorAction\s+SilentlyContinue", body):
            warnings.append(
                "-ErrorAction SilentlyContinue is used alongside a destructive command: "
                "failures and partial deletions will not be reported."
            )
        if re.search(r"(?i)\.Delete\s*\(|\[(?:System\.)?IO\.", body):
            warnings.append(
                "Direct .NET calls ([IO.File]::Delete, .Delete()) do NOT honour -WhatIf, "
                "so a dry_run preview cannot cover them."
            )

    if prose_matches and py is not None:
        warnings.append(
            "%d line(s) mention a destructive Python call but were NOT counted: the "
            "parsed script makes no such call there (see prose_matches for each "
            "reason). Once a script can run a string as code -- exec, eval, "
            "subprocess, a write to a .py -- its strings count again." % len(prose_matches)
        )
    elif prose_matches:
        warnings.append(
            "%d line(s) inside a here-string mention a destructive command but were "
            "NOT counted: this script has no way to execute a string, so they are "
            "data (see prose_matches). If the here-string IS meant to be executed, "
            "add the eval route and the count will include them." % len(prose_matches)
        )
    return {
        "destructive": bool(findings),
        "count": len(findings),
        "findings": findings,
        # B028: always present, even when empty. A key that appears only sometimes
        # is how a KeyError reaches production.
        "prose_matches": prose_matches,
        "footguns": footguns,
        "warnings": warnings,
    }


def _destructive_refusal(scan: dict, *, confirm_destructive: int, dry_run: bool,
                         surface: str) -> dict | None:
    """Return the CASE-2008 refusal for a scanned body, or None to let it proceed.

    Extracted so ``run_project_test_script`` and ``run_project_command`` cannot
    drift apart. The whole reason CASE-2008 was possible is that a safety property
    lived on one tool and not on its sibling -- the dispatcher's backup gate is a
    hardcoded set of five FILE tools, so a script that deleted files was never
    covered. Duplicating ninety lines of gate onto a second entry point would set
    up the identical failure a year from now, with a fix applied to one copy.

    Returns a plain dict (not a JSON string) so each caller can add its own
    surface-specific fields before serialising. ``surface`` only ever appears in
    prose the caller reads; it never changes what is refused.
    """
    if scan["footguns"]:
        # Refused, NOT confirmable. These shapes do not do what they say: the
        # -Include filter is silently discarded and the whole target goes. There
        # is no count worth acknowledging, only a command to rewrite.
        return _json_error_dict(
            "%s contains a PowerShell filter that will be SILENTLY IGNORED, so it "
            "would delete far more than the filter implies. Rewrite it; this shape "
            "cannot be confirmed." % surface,
            error_name="DESTRUCTIVE_SCRIPT_FOOTGUN",
            phase="policy",
            footguns=scan["footguns"],
            destructive_statements=scan["findings"],
            prose_matches=scan.get("prose_matches") or [],   # B028
            incident="CASE-2008 (2026-08-20): this exact shape deleted .gitignore and "
                     "README.md while reporting exit code 0.",
            ca_next_action="rewrite_script_then_retry",
            retryable=False,
        )

    if scan["destructive"] and not dry_run and confirm_destructive != scan["count"]:
        # The preview is returned in the SAME response as the refusal, so the
        # caller does not need a separate dry_run round-trip to learn the count.
        return _json_error_dict(
            "%s contains %d destructive statement(s) and was not executed. Review "
            "the list, then retry with confirm_destructive=%d to acknowledge exactly "
            "that many." % (surface, scan["count"], scan["count"]),
            error_name="DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED",
            phase="policy",
            destructive_count=scan["count"],
            destructive_statements=scan["findings"],
            prose_matches=scan.get("prose_matches") or [],   # B028
            warnings=scan["warnings"],
            confirm_destructive_required=scan["count"],
            confirm_hint=("Pass confirm_destructive=%d. If you edit the command the "
                          "count may change, and a stale value is refused on purpose."
                          % scan["count"]),
            dry_run_hint="Pass dry_run=true for a static, no-execute preview of the same list.",
            backup_note="A project backup runs automatically before execution unless skip_backup=true.",
            incident="CASE-2008 (2026-08-20): run_ps1_script had no dry_run and no backup; "
                     "an agent deleted the wrong files.",
            ca_next_action="review_then_retry_with_confirm_destructive",
            retryable=False,
        )
    return None


async def _destructive_backup_or_refusal(root: Path, scan: dict, *, dry_run: bool,
                                         skip_backup: bool) -> tuple[dict | None, dict | None]:
    """Take the pre-run backup for a destructive body. Returns (backup_info, refusal).

    Shared with ``_destructive_refusal`` for the same reason: unrecoverability, not
    the deletion itself, is what made CASE-2008 expensive, and a second copy of
    this logic is a second place for the refusal-on-failure to be dropped.
    """
    if not (scan["destructive"] and not dry_run and not skip_backup):
        return None, None
    if _backup_manifest_status(str(root)) not in {"missing", "unknown"}:
        return {"ran": False, "ok": True, "reason": "backup_already_available"}, None
    try:
        parsed = json.loads(await backup_project(str(root)))
    except Exception as exc:
        return None, _json_error_dict(
            "Pre-run backup raised, so the destructive command was not executed: %s" % exc,
            error_name="BACKUP_REQUIRED",
            phase="policy",
            destructive_count=scan["count"],
            ca_next_action="fix_backup_or_pass_skip_backup_if_target_is_disposable",
            retryable=False,
        )
    info = {
        "ran": True,
        "ok": bool(parsed.get("ok")),
        "backup_path": parsed.get("backup_path", ""),
    }
    if not parsed.get("ok"):
        # Refuse rather than proceed unprotected. The whole point of CASE-2008 is
        # that the deletion was unrecoverable.
        return None, _json_error_dict(
            "Pre-run backup FAILED, so the destructive command was not executed.",
            error_name="BACKUP_REQUIRED",
            phase="policy",
            backup_error=parsed.get("error", ""),
            destructive_count=scan["count"],
            ca_next_action="fix_backup_or_pass_skip_backup_if_target_is_disposable",
            retryable=False,
        )
    return info, None


# #31 -- how a python script child is started. The child used to be
# `python <script>` with PYTHONPATH=<root> exported through the environment, so
# the export reached EVERY descendant, not just the interpreter it was meant for.
# PYTHONHOME is not set by [A] (the daemon env carries no PYTHON* var): measured
# 2026-09-25, the ToolQ uv venv launcher (python.exe 0.11.8) sets it for its base
# interpreter, and a grandchild of a DIFFERENT version inheriting it loads the
# wrong stdlib and dies `AssertionError: SRE module mismatch` -- an exit a
# mutant runner reads as a kill. This bootstrap runs after the child's own
# startup has consumed PYTHONHOME, drops it for descendants, puts <root> on
# sys.path instead of in the env, and keeps what `python <script>` gives the
# script: __name__ == "__main__", __file__, sys.argv, sys.path[0] = its dir
# (safe_path honoured), and __future__ imports (compile() reads them from the
# source). An inherited PYTHONPATH is left untouched: it is the caller's.
# F3 (review of #32, 2026-09-25): the script is read as the utf-8 text [A] wrote
# and scanned, never compiled from its bytes. compile(bytes) honours a PEP 263
# cookie, so `# -*- coding: utf-7 -*-` made the child run `+AHM-hutil.+AHI-mtree(p)`
# as shutil.rmtree(p) while the destructive scan read that line as noise and
# counted 0. compile(str) ignores the cookie: what runs is what was scanned.
# utf-8-sig drops a leading BOM, which compile(bytes) accepted as well.
_PY_CHILD_BOOT = (
    "import os, sys\n"
    "os.environ.pop('PYTHONHOME', None)\n"
    "__file__ = sys.argv[2]\n"
    "if getattr(sys.flags, 'safe_path', False):\n"
    "    sys.path.insert(0, sys.argv[1])\n"
    "else:\n"
    "    sys.path[0:1] = [os.path.dirname(__file__), sys.argv[1]]\n"
    "sys.argv = sys.argv[2:]\n"
    "with open(__file__, encoding='utf-8-sig') as _a_src:\n"
    "    _a_code = compile(_a_src.read(), __file__, 'exec')\n"
    "del os, sys, _a_src\n"
    "exec(_a_code)\n"
)


async def run_project_test_script(
    project_path: str,
    script_content: str,
    runtime: str = "node",
    timeout: int = 120,
    script_label: str = "",
    return_full_stdout: bool = False,
    stdout_max_chars: int = 4000,
    stderr_max_chars: int = 2000,
    content_encoding: str = "plain",
    python_exe: str = "",
    required_python_modules: list[str] | None = None,
    executed_test_files: list[str] | None = None,
    dry_run: bool = False,
    confirm_destructive: int = -1,
    skip_backup: bool = False,
) -> str:
    """V15 R43 P1 #5 T2: save script as artifact in .orchestrator_cache, execute,
    return result summary. Avoids embedding long node -e scripts in request body
    (which inflates L token cost ~500-2000 bytes/call).

    Saved to: .orchestrator_cache/test_scripts/<hash>.{js,py,ps1}
    Runtimes: node | python | powershell | sh.

    Args:
      script_content: source code of the script (or base64 if content_encoding='base64')
      runtime: 'node' (.js) | 'python' (.py) | 'powershell' (.ps1) | 'sh' (.sh)
      script_label: human-readable label (e.g. 'rawcut_fixture_setup')
      return_full_stdout: when True, return entire stdout (overrides stdout_max_chars)
      stdout_max_chars/stderr_max_chars: truncation caps for output
      content_encoding: 'plain' or 'base64'
      dry_run: preview only -- scan the body, report the destructive statements
        found, and do NOT execute anything. This is a STATIC read of the source,
        not a -WhatIf run: it cannot enumerate the files that would actually be
        hit, and it deliberately does not try, because reaching a real -WhatIf
        means running the script, and .NET calls ([IO.File]::Delete) ignore
        -WhatIf entirely. Free to call and safe on any script.
      confirm_destructive: acknowledged destructive-statement count (CASE-2008).
        A body containing a destructive command refuses to run until this equals
        the scanner's count exactly; -1 means "not acknowledged". The count, not
        a boolean, so that editing the script after a preview invalidates the
        acknowledgement instead of silently carrying over.
      skip_backup: skip the pre-run project backup for a destructive script.
        Requires confirm_destructive to already match; use only when the target
        is genuinely disposable (e.g. inside .orchestrator_cache).

    Returns: {ok, exit_code, stdout, stderr, duration_seconds, script_path, script_hash, runtime_used, timed_out}
      A35: runtime="python" also returns `child_encoding`. The child's stdout/stderr
        are forced to UTF-8 (PYTHONIOENCODING/PYTHONUTF8 via setdefault), closing the
        asymmetry with the powershell branch's UTF-8 BOM + chcp 65001 prelude. Without
        it a non-cp1252 character raised UnicodeEncodeError INSIDE the script and
        killed it -- it was never merely mojibake.
      A35: timeout>170 also returns `transport_ceiling_note`. The ~180-195s MCP stdio
        proxy ceiling is a TRANSPORT limit, not a tool limit: call this over HTTP
        (POST /api/call/run_project_test_script) for anything longer.
      On a destructive body without acknowledgement: DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED.
      On a PowerShell -Include/-LiteralPath footgun: DESTRUCTIVE_SCRIPT_FOOTGUN (refused outright).
    """
    import hashlib as _hash_rpts
    import subprocess as _sp_rpts

    # Decode content if needed
    if content_encoding and content_encoding != "plain":
        decoded_text, _info, decode_err = _decode_text_payload(script_content, content_encoding)
        if decode_err is not None:
            return json.dumps(decode_err, ensure_ascii=False)
        script_content = decoded_text or ""

    # Validate runtime + ext mapping
    runtime_clean = (runtime or "node").lower().strip()
    ext_map = {"node": ".js", "python": ".py", "powershell": ".ps1", "sh": ".sh"}
    if runtime_clean not in ext_map:
        return _json_error(
            f"Unsupported runtime: {runtime}",
            error_name="UNSUPPORTED_FIELD",
            accepted_runtimes=list(ext_map.keys()),
        )

    # Resolve project + cache dir
    try:
        root = Path(project_path).resolve()
    except Exception as exc:
        return _json_error(f"project_path resolve failed: {exc}",
                            error_name="PROJECT_PATH_INVALID")
    if not root.exists():
        return _json_error(f"Project path not found: {project_path}",
                            error_name="PROJECT_PATH_NOT_FOUND")

    selected_python = None
    if runtime_clean == "python":
        selected_python = await run_in_executor_lane(
            "test", _select_python_interpreter, root, python_exe, required_python_modules,
        )
        if not selected_python.get("ok"):
            return json.dumps(selected_python, ensure_ascii=False)

    # ── CASE-2008 gate ──────────────────────────────────────────────────────
    # Placed after project resolution (so the response can name a real root) and
    # before the artifact is written, because a refused script should not leave a
    # .ps1 behind for someone to run by hand.
    _scan = _scan_destructive_script(script_content, runtime_clean)

    _refusal = _destructive_refusal(
        _scan, confirm_destructive=confirm_destructive, dry_run=dry_run,
        surface="Script",
    )
    if _refusal is not None:
        return json.dumps(_refusal, ensure_ascii=False)

    # A destructive run gets a backup FIRST. backup_project itself no-ops when
    # today's backup already exists, so this is cheap on the common path -- and
    # unlike the dispatcher's gate it fires for a SCRIPT, which is the hole that
    # let CASE-2008 through.
    _backup_info, _backup_refusal = await _destructive_backup_or_refusal(
        root, _scan, dry_run=dry_run, skip_backup=skip_backup,
    )
    if _backup_refusal is not None:
        return json.dumps(_backup_refusal, ensure_ascii=False)

    # A destructive run must never have its output truncated: the 4000-char cap
    # is what hid the damage in CASE-2008 (68,978 chars produced, and the lines
    # naming .gitignore/README.md fell off the end).
    if _scan["destructive"] and not dry_run:
        return_full_stdout = True

    if dry_run:
        # Nothing is written and nothing is run. Reported as ok:true because the
        # preview succeeded -- `executed:false` is the field that says no work
        # happened, so a caller cannot mistake a clean preview for a clean run.
        return json.dumps({
            "ok": True,
            "executed": False,
            "dry_run": True,
            "runtime_used": runtime_clean,
            "destructive": _scan["destructive"],
            "destructive_count": _scan["count"],
            "destructive_statements": _scan["findings"],
            **({"prose_matches": _scan["prose_matches"]}     # B028
               if _scan.get("prose_matches") else {}),
            "footguns": _scan["footguns"],
            "warnings": _scan["warnings"],
            "preview_kind": "static_source_scan",
            "preview_limits": ("Source-level scan only: it does NOT expand variables, "
                              "resolve globs, or list the files that would be deleted. "
                              "Treat the count as a lower bound on intent, not as a "
                              "blast radius."),
            "confirm_destructive_required": _scan["count"] if _scan["destructive"] else 0,
            "ca_next_action": (
                "retry_with_confirm_destructive_%d" % _scan["count"]
                if _scan["destructive"] else "rerun_without_dry_run"
            ),
        }, ensure_ascii=False)

    cache = root / ".orchestrator_cache" / "test_scripts"
    cache.mkdir(parents=True, exist_ok=True)

    # Hash + save
    script_hash = _hash_rpts.sha256(script_content.encode("utf-8", errors="replace")).hexdigest()[:16]
    safe_label = ""
    if script_label:
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in script_label)[:48] + "_"
    script_name = f"{safe_label}{script_hash}{ext_map[runtime_clean]}"
    script_path = cache / script_name
    # V15 R46 — write UTF-8 with BOM for PowerShell 5.1, plain UTF-8 elsewhere.
    # PS 5.1 (powershell.exe) defaults to ANSI/Windows-1252 when reading a
    # BOM-less .ps1, which silently strips Vietnamese diacritics from
    # literal strings. utf-8-sig writes the BOM so PS 5.1 reads UTF-8 reliably.
    _write_encoding = "utf-8-sig" if runtime_clean == "powershell" else "utf-8"
    # V15 R46 — also prepend an OutputEncoding prelude for PowerShell so
    # Write-Host / pipeline output goes to stdout as UTF-8 instead of OEM
    # (CP-437/850), which subprocess.run would otherwise corrupt when
    # decoding the captured stream. Source-side BOM alone fixes literal
    # strings; this fixes the OUTPUT path.
    if runtime_clean == "powershell":
        ps_prelude = (
            "# R46 auto-prelude: force UTF-8 console + pipeline encoding\r\n"
            "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}\r\n"
            "try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}\r\n"
            "try { chcp 65001 > $null } catch {}\r\n"
            "\r\n"
        )
        script_content_to_write = ps_prelude + script_content
    else:
        script_content_to_write = script_content
    try:
        _atomic_write_text(script_path, script_content_to_write, encoding=_write_encoding)
    except Exception as exc:
        return _json_error(f"script_write_failed: {exc}",
                            error_name="ARTIFACT_WRITE_FAILED")

    # Build runtime command
    if runtime_clean == "node":
        cmd = ["node", str(script_path)]
    elif runtime_clean == "python":
        cmd = [selected_python["executable"], "-c", _PY_CHILD_BOOT, str(root), str(script_path)]
    elif runtime_clean == "powershell":
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)]
    else:  # sh
        cmd = ["sh", str(script_path)]
    child_env = os.environ.copy()
    child_encoding_note: dict = {}
    if runtime_clean == "python":
        # A35 -- close the encoding asymmetry with the powershell branch above.
        # The parent decodes this child as UTF-8 (Popen encoding="utf-8" below),
        # but a python child on Windows defaults stdout to the ANSI code page.
        # Measured live on generation d2190b5ed454: PYTHONIOENCODING unset,
        # PYTHONUTF8 unset, sys.stdout.encoding='cp1252', sys.flags.utf8_mode=0
        # -- start_orchestrator.bat sets neither, so the child inherited nothing.
        # A Vietnamese literal therefore did not merely arrive as mojibake: it
        # raised UnicodeEncodeError INSIDE the script and killed it, while .ps1
        # got a BOM plus a chcp 65001 prelude and the council dispatcher already
        # sets both vars for its own children (_council_run_agent). setdefault,
        # not assignment, so a caller who wants another codec still wins.
        child_env.setdefault("PYTHONIOENCODING", "utf-8")
        child_env.setdefault("PYTHONUTF8", "1")
        child_encoding_note = {
            "PYTHONIOENCODING": child_env["PYTHONIOENCODING"],
            "PYTHONUTF8": child_env["PYTHONUTF8"],
            "why": ("python child stdout/stderr forced to UTF-8 to match this side's "
                    "utf-8 decode; without it a non-cp1252 character raises "
                    "UnicodeEncodeError in the script itself, not just mojibake"),
            "override": "set PYTHONIOENCODING in the [A] process env to beat this default",
        }

    start_t = time.time()
    timed_out = False
    proc_evidence = None
    try:
        creationflags = getattr(_sp_rpts, "CREATE_NEW_PROCESS_GROUP", 0) if sys.platform == "win32" else 0
        proc = await run_in_executor_lane(
            "test", _sp_rpts.Popen, cmd, cwd=str(root), stdout=_sp_rpts.PIPE,
            stderr=_sp_rpts.PIPE, text=True, encoding="utf-8", errors="replace",
            env=child_env, creationflags=creationflags,
            start_new_session=(sys.platform != "win32"),
        )
        stdout, stderr, timed_out, proc_evidence = await run_in_executor_lane(
            "test", _communicate_with_tree_timeout, proc, timeout,
            is_windows=(sys.platform == "win32"),
        )
        rc = proc.returncode
    except ExecutorLaneError:
        raise
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc), "error_name": "SCRIPT_EXEC_FAILED", "script_path": str(script_path), "script_hash": script_hash, "runtime_used": runtime_clean})

    stdout, stdout_metadata = _truncate_stream(
        stdout, 0 if return_full_stdout else stdout_max_chars,
    )
    stderr, stderr_metadata = _truncate_stream(stderr, stderr_max_chars)

    # R86 — surface UNC-auth-context failures so CA does NOT misread a Windows
    # per-session logon limitation as a script/code bug. The [A] subprocess
    # inherits [A]'s logon token, which may lack the UNC share auth/mapping the
    # interactive user's own session has (e.g. WinError 1326). run_node_script
    # failing on a UNC path that native node (user session) reads = this case.
    _unc_auth_ctx = None
    if (rc not in (0, None)) and stderr:
        _stderr_low = stderr.lower()
        _unc_present = ("\\\\" in stderr) or ("unc" in _stderr_low) or ("network path" in _stderr_low)
        _auth_hit = any(c in stderr for c in (
            "WinError 1326", "WinError 1219", "WinError 1385",
            "WinError 86", "WinError 5", "WinError 53", "WinError 67",
            "1326", "logon failure",
        ))
        if _unc_present and _auth_hit:
            _unc_auth_ctx = {
                "error_name": "UNC_ACCESS_FROM_ORCHESTRATOR_PROCESS",
                "phase": "environment",
                "cause": "[A] subprocess logon session lacks the UNC share credentials/mapping the interactive user has — NOT server-down, NOT a script bug.",
                "ca_next_action": "do_not_retry_same_way__verify_path_from_user_session_or_localize",
                "recovery_options": [
                    "A) Ask the user to confirm/read the UNC path in their OWN terminal (native read usually works).",
                    "B) User maps/authenticates the share for [A]'s logon session (net use \\\\server\\share /persistent:yes), then retry.",
                    "C) Copy needed files to a LOCAL path [A] can read, then operate on the copy.",
                ],
                "do_not": "Do not retry the same script unchanged and do not report it as code/server failure.",
                "alias_see_also": "sample_unc_paths_with_root_check verdict='unc_auth_context_mismatch'",
            }
    result = {
        "ok": (rc == 0 and not timed_out),
        "exit_code": rc,
        "stdout": stdout,
        "stdout_metadata": stdout_metadata,
        "stderr": stderr,
        "stderr_metadata": stderr_metadata,
        "duration_seconds": round(time.time() - start_t, 3),
        "script_path": str(script_path),
        "script_hash": script_hash,
        "script_label": script_label,
        "runtime_used": runtime_clean,
        "timed_out": timed_out,
        # #29: the tree kill of a timeout, or a descendant that still held the
        # pipes after the script exited -- evidence; ok stays rc/timed_out.
        **({"process_evidence": proc_evidence} if proc_evidence else {}),
        # A35: say so when [A] forced the child's stdout codec, so a CA never has
        # to guess whether a clean non-ASCII line was luck or policy.
        **({"child_encoding": child_encoding_note} if child_encoding_note else {}),
        # A35 gap#3 -- the ~180-195s MCP stdio ceiling is a TRANSPORT limit, not a
        # tool limit, and nothing in the surface said so: a CA that asked for a 600s
        # run lost the whole answer with no way to know why. Emitted only when the
        # requested timeout is near that wall, so an ordinary call pays nothing.
        **({"transport_ceiling_note": (
            "timeout=%ds exceeds the ~180-195s MCP stdio proxy ceiling. Over MCP the proxy "
            "dies first and this result is lost even though the child keeps running. Call "
            "POST http://localhost:8083/api/call/run_project_test_script over HTTP instead, "
            "or use the detached-supervisor pattern (a 0.1s launcher that Popens the real "
            "work and writes a timestamped status file to poll)."
            % int(timeout or 0))} if int(timeout or 0) > 170 else {}),
        "ca_next_action": (
            "inspect_stderr_if_failed" if rc != 0 or timed_out else "evaluate_stdout_assertions"
        ),
    }
    # CASE-2008 — a destructive run says so in its OWN result, so the audit trail
    # does not depend on anyone having kept the refusal that preceded it.
    if _scan["destructive"]:
        result["destructive"] = True
        result["destructive_count"] = _scan["count"]
        result["destructive_statements"] = _scan["findings"]
        if _scan.get("prose_matches"):                       # B028
            result["prose_matches"] = _scan["prose_matches"]
        result["confirm_destructive_acknowledged"] = confirm_destructive
        if _scan["warnings"]:
            result["warnings"] = _scan["warnings"]
        if _backup_info is not None:
            result["backup_info"] = _backup_info
        elif skip_backup:
            result["backup_info"] = {"ran": False, "ok": None, "reason": "skip_backup_requested"}
    if _unc_auth_ctx:
        result["unc_auth_context"] = _unc_auth_ctx
        result["ca_next_action"] = _unc_auth_ctx["ca_next_action"]
    # P7-S2 H2: opt-in cached enrich. Flag-OFF = no hooks/graph import.
    if os.environ.get("CBM_ENRICH_TEST", "0").strip().lower() in ("1", "true", "yes", "on"):
        # GAP-01/H2: the caller-DECLARED test list is the only honest source --
        # the daemon never scrapes stdout. Surface it on the result (so
        # enrich_test_result marks selection="explicit") and warm the KIND_TEST
        # cache keyed by script_hash BEFORE the read-side lookup runs, so a
        # declared run yields a real advisory instead of skipped:cache_miss.
        _declared = [str(f).strip().replace("\\", "/")
                     for f in (executed_test_files or []) if str(f).strip()]
        if _declared:
            result["executed_test_files"] = _declared
            try:
                from orchestrator.graph.enrich_producers import (
                    produce_test_advisory as _cbm_warm_test,
                )
                from orchestrator.graph.enrich_producers import (
                    run_producer_async as _cbm_run_async,
                )
                from orchestrator.graph.hooks import (
                    scoped_fingerprint as _cbm_scoped_fp,
                )
                result["enrich_fingerprint"] = _cbm_scoped_fp(str(root), script_hash)
                _warm = await _cbm_run_async(_cbm_warm_test, str(root), script_hash, _declared)
                if isinstance(_warm, dict):
                    result["test_gap_warm"] = {
                        "warmed": bool(_warm.get("warmed")),
                        "reason": _warm.get("reason"),
                    }
            except Exception:
                pass  # fail-open: warm miss leaves the honest skipped path below
        try:
            from orchestrator.graph.hooks import enrich_test_result as _cbm_enrich_test
            result = _cbm_enrich_test(result)
        except Exception:
            result["test_gap"] = {
                "status": "skipped", "reason": "advisory_error", "payload": None,
            }
    return json.dumps(result, ensure_ascii=False)


# ─── Active task epoch contract ──────────────────────────────────────────────

_TASK_EPOCH_LOCKS: dict[str, threading.Lock] = {}
_TASK_EPOCH_LOCKS_GUARD = threading.Lock()
_OWNER_CLAIM_TTL_SECONDS = max(30, int(os.environ.get("ORCHESTRATOR_OWNER_CLAIM_TTL_SECONDS", "300")))
_OWNER_HEARTBEAT_TTL_SECONDS = max(15, int(os.environ.get("ORCHESTRATOR_OWNER_HEARTBEAT_TTL_SECONDS", "120")))


def _task_epoch_lock(session_key: str) -> threading.Lock:
    with _TASK_EPOCH_LOCKS_GUARD:
        return _TASK_EPOCH_LOCKS.setdefault(session_key, threading.Lock())


def _task_prompt_hash(prompt: str) -> str:
    return hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()


def _owner_claim_hash(owner_claim_id: str) -> str:
    return hashlib.sha256(str(owner_claim_id or "").encode("utf-8")).hexdigest()


def _owner_capability_from_claim(session_id: str, agent_id: str,
                                 owner_claim_id: str) -> str:
    context = f"owner-claim-v1\0{session_id}\0{agent_id}".encode("utf-8")
    digest = hmac.new(str(owner_claim_id).encode("utf-8"), context, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _valid_owner_claim_id(owner_claim_id: str) -> bool:
    value = str(owner_claim_id or "")
    return len(value) >= 32 and len(value.encode("utf-8")) >= 32


def _repair_latin1_utf8_prompt(prompt: str) -> tuple[str, bool]:
    """Repair the common UTF-8-as-latin1 transport corruption losslessly.

    The repair is accepted only when latin-1 round-tripping succeeds and the
    candidate removes more mojibake markers than the original. Legitimate
    Unicode that cannot round-trip through latin-1 is returned unchanged.
    """
    text = prompt or ""
    before = sum(text.count(marker) for marker in _LATIN1_UTF8_MOJIBAKE_HINTS)
    if not before:
        return text, False
    try:
        candidate = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text, False
    after = sum(candidate.count(marker) for marker in _LATIN1_UTF8_MOJIBAKE_HINTS)
    if after >= before:
        return text, False
    # Guard against false positives on literal Latin-1 that is incidentally
    # valid UTF-8 (a stray copyright/accented symbol, not transport corruption):
    # accept only when the rewrite clears >=2 markers OR recovers a genuinely
    # non-Latin-1 character (real Vietnamese/CJK). Single Latin-1 symbols stay.
    if (before - after) < 2 and not any(ord(ch) > 0xFF for ch in candidate):
        return text, False
    return candidate, True


async def begin_task(session_id: str, prompt: str, agent_id: str = "", max_chars: int = 2000,
                     owner_capability: str = "", delegation_capability: str = "",
                     delegate_agent_id: str = "", owner_claim_id: str = "") -> str:
    session_key = (session_id or "default").strip() or "default"
    normalized, repaired = _repair_latin1_utf8_prompt(prompt or "")
    bounded = normalized[:max(256, min(int(max_chars or 2000), 8000))]
    incoming = (agent_id or "").strip()
    def cap_hash(value):
        return hashlib.sha256(str(value or "").encode()).hexdigest()
    def reject(message, name, **extra):
        if name in {"TASK_OWNER_REQUIRED", "TASK_OWNER_MISMATCH", "OWNER_CLAIM_EXPIRED"}:
            extra.setdefault("accepted_fields", [
                "session_id", "prompt", "agent_id", "owner_capability",
                "owner_claim_id", "delegation_capability", "delegate_agent_id",
            ])
            extra.setdefault("payload_example", {
                "session_id": session_key, "prompt": "<newest real user prompt>",
                "agent_id": incoming, "owner_capability": "<from bootstrap_session>",
                "owner_claim_id": "<original persisted owner_claim_id>",
            })
        return None, _json_error(message, error_name=name, **extra)
    def update(previous):
        root = str(previous.get("agent_id") or "")
        if not previous:
            return reject("bootstrap_session must establish the server-owned task root first",
                          "TASK_OWNER_REQUIRED", ca_next_action="call_bootstrap_session_then_retry_with_owner_capability")
        status = str(previous.get("owner_claim_status") or "")
        if status == "pending":
            if time.time() > float(previous.get("owner_claim_expires_at") or 0):
                return reject("pending owner claim expired before confirmation", "OWNER_CLAIM_EXPIRED",
                              ca_next_action="bootstrap_session_with_a_new_owner_claim_id")
            expected = str(previous.get("owner_claim_sha256") or "")
            if expected and (not owner_claim_id or not secrets.compare_digest(_owner_claim_hash(owner_claim_id), expected)):
                return reject("owner_claim_id does not match the pending owner claim", "TASK_OWNER_MISMATCH",
                              ca_next_action="retry_with_the_original_owner_claim_id")
        if incoming != root:
            subtasks = dict(previous.get("subtasks") or {}); old = subtasks.get(incoming) or {}
            if not old or not delegation_capability or not secrets.compare_digest(cap_hash(delegation_capability), str(old.get("delegation_capability_sha256") or "")):
                return reject("agent does not own this task root and has no valid delegation", "TASK_OWNER_MISMATCH",
                              owner_agent_id=root, ca_next_action="ask_root_owner_to_issue_explicit_delegation")
            response = {"ok": True, "session_id": session_key, "agent_id": incoming, "scope": "subtask",
                        "task_epoch": int(old.get("task_epoch", 0)) + 1, "root_task_epoch": int(previous.get("task_epoch", 0)),
                        "prompt_hash": _task_prompt_hash(normalized), "active_task": bounded,
                        "prompt_encoding_repaired": repaired, "supersedes_prior_tasks": False,
                        "ca_next_action": "proceed_with_active_task"}
            subtasks[incoming] = {"agent_id": incoming, "task_epoch": response["task_epoch"],
                                  "prompt_hash": response["prompt_hash"], "active_task": bounded,
                                  "prompt_encoding_repaired": repaired,
                                  "delegation_capability_sha256": old["delegation_capability_sha256"]}
            replacement = dict(previous); replacement["subtasks"] = subtasks
            return replacement, json.dumps(response, ensure_ascii=False)
        if not owner_capability or not secrets.compare_digest(cap_hash(owner_capability), str(previous.get("owner_capability_sha256") or "")):
            return reject("valid root owner capability is required", "TASK_OWNER_MISMATCH", owner_agent_id=root,
                          ca_next_action="use_server_issued_owner_capability")
        replacement = dict(previous)
        if status == "pending":
            # Both statements are inside this if. They used to share one line via `;`, where
            # the pop() reads as unconditional -- splitting only the first one off would have
            # silently changed behaviour. Keep them as an indented block.
            replacement["owner_claim_status"] = "committed"
            replacement.pop("owner_claim_expires_at", None)
        delegate = str(delegate_agent_id or "").strip()
        if delegate:
            if delegate == incoming: return reject("delegate_agent_id must differ from root owner", "INVALID_DELEGATION")
            issued = secrets.token_urlsafe(32); subtasks = dict(replacement.get("subtasks") or {})
            sub = {"agent_id": delegate, "task_epoch": int((subtasks.get(delegate) or {}).get("task_epoch", 0)) + 1,
                   "root_task_epoch": int(replacement.get("task_epoch", 0)), "prompt_hash": _task_prompt_hash(normalized),
                   "active_task": bounded, "prompt_encoding_repaired": repaired,
                   "delegation_capability_sha256": cap_hash(issued)}
            subtasks[delegate] = sub; replacement["subtasks"] = subtasks
            response = {k: sub[k] for k in ("agent_id", "task_epoch", "root_task_epoch", "prompt_hash", "active_task")}
            response.update({"ok": True, "session_id": session_key, "scope": "subtask", "delegation_capability": issued,
                             "supersedes_prior_tasks": False, "ca_next_action": "deliver_delegation_capability_to_named_agent"})
            return replacement, json.dumps(response, ensure_ascii=False)
        checkpoint = {"ok": True, "session_id": session_key, "agent_id": incoming or root, "scope": "root",
                       "task_epoch": int(replacement.get("task_epoch", 0)) + 1, "prompt_hash": _task_prompt_hash(normalized),
                       "active_task": bounded, "prompt_encoding_repaired": repaired, "supersedes_prior_tasks": True,
                       "ca_next_action": "proceed_with_active_task", "owner_capability_sha256": replacement.get("owner_capability_sha256"),
                       "owner_identity_id": replacement.get("owner_identity_id"), "owner_claim_sha256": replacement.get("owner_claim_sha256"),
                       "owner_claim_status": replacement.get("owner_claim_status") or "committed"}
        for heartbeat_key in ("owner_last_heartbeat_at", "owner_last_heartbeat_at_iso",
                              "owner_heartbeat_count", "owner_heartbeat_source"):
            if heartbeat_key in replacement:
                checkpoint[heartbeat_key] = replacement[heartbeat_key]
        if replacement.get("subtasks"): checkpoint["subtasks"] = replacement["subtasks"]
        hidden = {"owner_capability_sha256", "owner_identity_id", "owner_claim_sha256", "owner_claim_status", "owner_claim_expires_at",
                 "owner_last_heartbeat_at", "owner_last_heartbeat_at_iso", "owner_heartbeat_count", "owner_heartbeat_source"}
        return checkpoint, json.dumps({k: v for k, v in checkpoint.items() if k not in hidden}, ensure_ascii=False)
    # Anh 2026-08-11 (Phase A on-loop audit): run the durable task checkpoint update
    # OFF the loop. _task_checkpoint_update goes through the SessionWriter and blocks
    # on a durable-commit Future; calling it synchronously pinned the event loop for
    # the write — a hang vector for begin_task under contention.
    try: checkpoint, response, wrote = await asyncio.to_thread(_task_checkpoint_update, session_key, update)
    except TaskCheckpointStorageError as exc:
        return _json_error(str(exc), error_name=exc.error_name, retryable=False, ca_next_action="repair_task_checkpoint_storage_then_retry")
    return response


async def owner_heartbeat(session_id: str, agent_id: str, owner_capability: str,
                          summary: str = "") -> str:
    """Record authenticated owner liveness without exposing capability material."""
    session_key = (session_id or "default").strip() or "default"
    incoming = (agent_id or "").strip()

    def reject(message, name, **extra):
        return None, _json_error(message, error_name=name, retryable=False, **extra)

    def update(previous):
        if not previous:
            return reject("bootstrap_session must establish the server-owned task root first",
                          "TASK_OWNER_REQUIRED",
                          ca_next_action="call_bootstrap_session_then_begin_task")
        root = str(previous.get("agent_id") or "")
        if incoming != root:
            return reject("only the committed root owner may send this heartbeat",
                          "TASK_OWNER_MISMATCH", owner_agent_id=root,
                          ca_next_action="use_the_root_owner_agent_id_and_capability")
        status = str(previous.get("owner_claim_status") or "")
        if status != "committed":
            return reject("owner capability is not committed; confirm the owner claim first",
                          "OWNER_CLAIM_NOT_COMMITTED",
                          ca_next_action="call_begin_task_with_the_server_issued_owner_capability")
        expected = str(previous.get("owner_capability_sha256") or "")
        if not owner_capability or not expected or not secrets.compare_digest(
                hashlib.sha256(str(owner_capability).encode("utf-8")).hexdigest(), expected):
            return reject("valid root owner capability is required",
                          "TASK_OWNER_MISMATCH", owner_agent_id=root,
                          ca_next_action="use_server_issued_owner_capability")
        now_ts = time.time()
        heartbeat_at = datetime.fromtimestamp(now_ts).isoformat(timespec="seconds")
        replacement = dict(previous)
        replacement["owner_last_heartbeat_at"] = now_ts
        replacement["owner_last_heartbeat_at_iso"] = heartbeat_at
        replacement["owner_heartbeat_count"] = int(replacement.get("owner_heartbeat_count") or 0) + 1
        replacement["owner_heartbeat_source"] = "owner_heartbeat"
        response = {
            "ok": True,
            "session_id": session_key,
            "agent_id": incoming,
            "scope": "root",
            "owner_liveness": "active",
            "heartbeat_freshness": "fresh",
            "heartbeat_at": heartbeat_at,
            "heartbeat_ttl_seconds": _OWNER_HEARTBEAT_TTL_SECONDS,
            "heartbeat_count": replacement["owner_heartbeat_count"],
            "ca_next_action": "continue_and_heartbeat_before_ttl",
        }
        return replacement, json.dumps(response, ensure_ascii=False)

    try:
        _checkpoint, response, _wrote = await asyncio.to_thread(
            _task_checkpoint_update, session_key, update
        )
    except TaskCheckpointStorageError as exc:
        return _json_error(str(exc), error_name=exc.error_name, retryable=False,
                           ca_next_action="repair_task_checkpoint_storage_then_retry")
    return response


def _record_authenticated_owner_activity(session_id: str, agent_id: str,
                                          task_epoch, prompt_hash: str,
                                          tool_name: str = "") -> dict:
    """Record activity after the dispatcher validates a task envelope.

    The dispatcher has already checked the epoch and prompt hash before calling
    this helper. The checkpoint owner and committed claim are checked again
    inside the atomic update so stale or delegated identities cannot touch the
    root owner's liveness timestamp.
    """
    session_key = (session_id or "default").strip() or "default"
    incoming = (agent_id or "").strip()

    def update(previous):
        if not previous:
            return None, json.dumps({"recorded": False, "reason": "checkpoint_missing"})
        if str(previous.get("agent_id") or "") != incoming:
            return None, json.dumps({"recorded": False, "reason": "owner_identity_mismatch"})
        if (str(previous.get("owner_claim_status") or "") != "committed"
                or not previous.get("owner_capability_sha256")):
            return None, json.dumps({"recorded": False, "reason": "owner_not_committed"})
        try:
            expected_epoch = int(previous.get("task_epoch", 0))
            incoming_epoch = int(task_epoch)
        except (TypeError, ValueError):
            return None, json.dumps({"recorded": False, "reason": "task_epoch_invalid"})
        if incoming_epoch != expected_epoch:
            return None, json.dumps({"recorded": False, "reason": "task_epoch_mismatch"})
        if str(prompt_hash or "") != str(previous.get("prompt_hash") or ""):
            return None, json.dumps({"recorded": False, "reason": "prompt_hash_mismatch"})

        now_ts = time.time()
        heartbeat_at = datetime.fromtimestamp(now_ts).isoformat(timespec="seconds")
        replacement = dict(previous)
        replacement["owner_last_heartbeat_at"] = now_ts
        replacement["owner_last_heartbeat_at_iso"] = heartbeat_at
        replacement["owner_heartbeat_count"] = int(
            replacement.get("owner_heartbeat_count") or 0
        ) + 1
        replacement["owner_heartbeat_source"] = "authenticated_task_activity"
        response = {
            "recorded": True,
            "session_id": session_key,
            "agent_id": incoming,
            "tool_name": str(tool_name or ""),
            "owner_liveness": "active",
            "heartbeat_freshness": "fresh",
            "heartbeat_at": heartbeat_at,
            "heartbeat_ttl_seconds": _OWNER_HEARTBEAT_TTL_SECONDS,
            "heartbeat_count": replacement["owner_heartbeat_count"],
        }
        return replacement, json.dumps(response, ensure_ascii=False)

    _checkpoint, response, _wrote = _task_checkpoint_update(session_key, update)
    try:
        return json.loads(response)
    except (TypeError, json.JSONDecodeError):
        return {"recorded": False, "reason": "activity_result_invalid"}


def _owner_status_from_checkpoint(checkpoint: dict, updated_at: str = "", now=None):
    """Return safe owner liveness diagnostics without exposing opaque capabilities."""
    point = dict(checkpoint or {})
    now_ts = float(time.time() if now is None else now)
    claim_status = str(point.get("owner_claim_status") or "")
    owner_agent = str(point.get("agent_id") or "")
    commitment_present = bool(point.get("owner_capability_sha256"))
    expiry = None
    try:
        if point.get("owner_claim_expires_at") is not None:
            expiry = float(point.get("owner_claim_expires_at"))
    except (TypeError, ValueError):
        expiry = None

    checkpoint_age = None
    if updated_at:
        try:
            stamp = datetime.fromisoformat(str(updated_at))
            if stamp.tzinfo is not None:
                stamp = stamp.replace(tzinfo=None)
            checkpoint_age = max(0.0, (datetime.now() - stamp).total_seconds())
        except (TypeError, ValueError, OverflowError):
            checkpoint_age = None
    heartbeat_at = None
    try:
        if point.get("owner_last_heartbeat_at") is not None:
            heartbeat_at = float(point.get("owner_last_heartbeat_at"))
    except (TypeError, ValueError):
        heartbeat_at = None
    heartbeat_seen = heartbeat_at is not None
    activity_at = heartbeat_at
    activity_iso = str(point.get("owner_last_heartbeat_at_iso") or "")
    activity_source = (
        str(point.get("owner_heartbeat_source") or "owner_heartbeat")
        if heartbeat_seen else ""
    )
    if activity_at is None and updated_at:
        try:
            stamp = datetime.fromisoformat(str(updated_at))
            if stamp.tzinfo is not None:
                stamp = stamp.replace(tzinfo=None)
            activity_at = stamp.timestamp()
            activity_iso = str(updated_at)
            activity_source = "authenticated_checkpoint_update"
        except (TypeError, ValueError, OverflowError, OSError):
            activity_at = None
    activity_age = max(0.0, now_ts - activity_at) if activity_at is not None else None
    heartbeat_ttl = float(_OWNER_HEARTBEAT_TTL_SECONDS)
    if activity_age is None:
        heartbeat_freshness = "unknown"
    elif activity_age <= heartbeat_ttl:
        heartbeat_freshness = "fresh"
    else:
        heartbeat_freshness = "stale"

    if claim_status == "pending":
        expired = expiry is not None and now_ts >= expiry
        state = "expired" if expired else "pending"
        orphan_suspected = True if expired else False
        owner_liveness = "not_confirmed" if expired else "pending_not_committed"
        stuck_suspected = True if expired else "unknown"
        interpretation = (
            "pending claim expired; no live owner was confirmed"
            if expired else
            "owner claim is pending confirmation; liveness is not proven"
        )
    elif commitment_present:
        state = "committed"
        orphan_suspected = "unknown"
        if heartbeat_freshness == "fresh":
            owner_liveness = "active"
            stuck_suspected = False
            interpretation = (
                "owner is active: a fresh owner heartbeat or authenticated task "
                "checkpoint was recorded within the heartbeat TTL"
            )
        elif heartbeat_freshness == "stale":
            owner_liveness = "stale_suspected"
            stuck_suspected = True
            interpretation = (
                "owner may be stuck, stopped, or disconnected: no authenticated "
                "activity was recorded within the heartbeat TTL; do not reclaim automatically"
            )
        else:
            owner_liveness = "unknown_without_heartbeat"
            stuck_suspected = "unknown"
            interpretation = (
                "server-owned capability commitment exists, but no heartbeat or "
                "authenticated activity timestamp is available"
            )
    elif owner_agent:
        state = "unowned"
        orphan_suspected = False
        owner_liveness = "not_claimed"
        stuck_suspected = "unknown"
        interpretation = "owner label exists without a server capability commitment"
    else:
        state = "unowned"
        orphan_suspected = False
        owner_liveness = "not_claimed"
        stuck_suspected = "unknown"
        interpretation = "no server-owned owner claim is present"

    result = {
        "state": state,
        "claim_status": claim_status or "none",
        "owner_agent_id": owner_agent,
        "capability_commitment_present": commitment_present,
        "heartbeat_supported": True,
        "heartbeat_seen": heartbeat_seen,
        "heartbeat_ttl_seconds": _OWNER_HEARTBEAT_TTL_SECONDS,
        "heartbeat_freshness": heartbeat_freshness,
        "heartbeat_age_seconds": round(activity_age, 3) if activity_age is not None else None,
        "last_heartbeat_at": activity_iso if heartbeat_seen else "",
        "last_authenticated_activity_at": activity_iso,
        "activity_source": activity_source or "none",
        "owner_liveness": owner_liveness,
        "stuck_suspected": stuck_suspected,
        "checkpoint_freshness": (
            "unknown" if checkpoint_age is None else
            "fresh" if checkpoint_age <= _OWNER_CLAIM_TTL_SECONDS else
            "aging" if checkpoint_age <= _OWNER_CLAIM_TTL_SECONDS * 2 else "stale"
        ),
        "stale_checkpoint_suspected": (
            checkpoint_age is not None and checkpoint_age > _OWNER_CLAIM_TTL_SECONDS * 2
        ),
        "orphan_suspected": orphan_suspected,
        "last_checkpoint_at": str(updated_at or ""),
        "checkpoint_age_seconds": round(checkpoint_age, 3) if checkpoint_age is not None else None,
        "interpretation": interpretation,
    }
    if state == "pending" and expiry is not None:
        result["claim_expires_in_seconds"] = round(expiry - now_ts, 3)
    return result

def _session_activity_snapshot(session_key: str, now=None) -> dict:
    """Return safe CA activity evidence from the durable session ledger."""
    result = {
        "observed": False,
        "ca_liveness": "unknown",
        "activity_freshness": "unknown",
        "activity_source": "session_ledger",
        "session_status": "missing",
        "event_count": 0,
        "last_activity_at": "",
        "last_tool": "",
        "activity_age_seconds": None,
    }
    try:
        conn = sqlite3.connect(str(_SESSIONS_DB))
        try:
            session_row = conn.execute(
                "SELECT status FROM sessions WHERE session_id=?",
                (str(session_key),),
            ).fetchone()
            if not session_row:
                return result
            event_count = int(conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id=?",
                (str(session_key),),
            ).fetchone()[0] or 0)
            latest = conn.execute(
                "SELECT tool_name, created_at FROM events "
                "WHERE session_id=? ORDER BY id DESC LIMIT 1",
                (str(session_key),),
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return result

    result["session_status"] = str(session_row[0] or "unknown")
    result["event_count"] = event_count
    if not latest:
        result["ca_liveness"] = (
            "active_without_events"
            if result["session_status"] == "active" else result["session_status"]
        )
        return result

    last_tool, last_activity_at = latest
    result["observed"] = True
    result["last_tool"] = str(last_tool or "")
    result["last_activity_at"] = str(last_activity_at or "")
    try:
        stamp = datetime.fromisoformat(str(last_activity_at))
        if stamp.tzinfo is not None:
            stamp = stamp.replace(tzinfo=None)
        now_dt = datetime.fromtimestamp(float(time.time() if now is None else now))
        age = max(0.0, (now_dt - stamp).total_seconds())
        result["activity_age_seconds"] = round(age, 3)
        ttl = float(_OWNER_HEARTBEAT_TTL_SECONDS)
        result["activity_freshness"] = "fresh" if age <= ttl else "stale"
        if result["session_status"] == "active":
            result["ca_liveness"] = "active" if age <= ttl else "stale_suspected"
        else:
            result["ca_liveness"] = result["session_status"]
    except (TypeError, ValueError, OverflowError, OSError):
        result["ca_liveness"] = "unknown"
    return result


async def get_task_checkpoint(session_id: str, include_owner_status: bool = False) -> str:
    session_key = (session_id or "default").strip() or "default"
    try:
        checkpoint, updated_at = _task_checkpoint_load_with_meta(session_key)
    except TaskCheckpointStorageError as exc:
        return _json_error(str(exc), error_name=exc.error_name, retryable=False,
                           ca_next_action="repair_task_checkpoint_storage_then_retry")
    activity = None
    if include_owner_status:
        activity = await asyncio.to_thread(_session_activity_snapshot, session_key)
    if not checkpoint:
        if not include_owner_status:
            return _json_error("No active task checkpoint", error_name="TASK_CHECKPOINT_MISSING",
                               ca_next_action="call_begin_task")
        missing = json.loads(_json_error(
            "No active task checkpoint; CA activity may still be present in the session ledger",
            error_name="TASK_CHECKPOINT_MISSING",
            ca_next_action="call_bootstrap_session_then_begin_task",
        ))
        ca_live = str(activity.get("ca_liveness") or "unknown")
        missing["session_activity"] = activity
        missing["owner_status"] = {
            "state": "unowned",
            "owner_liveness": "not_claimed",
            "stuck_suspected": "unknown",
            "orphan_suspected": False,
            "capability_commitment_present": False,
            "ca_activity": ca_live,
            "ca_activity_observed": bool(activity.get("observed")),
            "ca_activity_source": "session_ledger",
            "interpretation": (
                "CA activity is observed, but no server-owned owner claim exists; "
                "owner liveness and stuck state cannot be attributed safely"
                if activity.get("observed") else
                "no owner claim or recent CA activity was observed for this session"
            ),
        }
        return json.dumps(missing, ensure_ascii=False)
    public = {k: v for k, v in checkpoint.items()
              if k not in {"owner_capability_sha256", "owner_identity_id", "owner_claim_sha256",
                           "owner_claim_status", "owner_claim_expires_at",
                           "owner_last_heartbeat_at", "owner_last_heartbeat_at_iso",
                           "owner_heartbeat_count", "owner_heartbeat_source"}}
    if isinstance(public.get("subtasks"), dict):
        public["subtasks"] = {agent: {k: v for k, v in subtask.items()
                                      if k != "delegation_capability_sha256"}
                              for agent, subtask in public["subtasks"].items()}
    response = {**public, "ok": True}
    if include_owner_status:
        owner_status = _owner_status_from_checkpoint(checkpoint, updated_at)
        owner_status["ca_activity"] = str(activity.get("ca_liveness") or "unknown")
        owner_status["ca_activity_observed"] = bool(activity.get("observed"))
        owner_status["ca_activity_source"] = "session_ledger"
        response["owner_status"] = owner_status
        response["session_activity"] = activity
    return json.dumps(response, ensure_ascii=False)


# ─── V15 R42 NEW TOOLS: bootstrap_session, check_server_path, wait_for_idle, parse_log_section, workflow_goto_step ─────

async def _bootstrap_with_cached_owner_claim(
        project_path: str, session_id: str, agent_id: str, owner_claim_id: str = "",
        *, claim_cache: dict | None = None, verbosity: str = "terse",
        include_discovery: bool = False) -> str:
    """Shortcut compatibility: never mint over a durable owner after reconnect."""
    cache = _PREPARE_OWNER_CLAIMS if claim_cache is None else claim_cache
    key = (str(session_id).strip(), str(agent_id).strip())
    claim = owner_claim_id or cache.get(key, "")
    if not claim:
        try:
            checkpoint = await asyncio.to_thread(_task_checkpoint_load, key[0]) or {}
        except TaskCheckpointStorageError as exc:
            return _json_error(str(exc), error_name=exc.error_name, retryable=False,
                               ca_next_action="repair_task_checkpoint_storage_then_retry")
        if checkpoint.get("owner_capability_sha256") or checkpoint.get("owner_claim_sha256"):
            return _json_error(
                "Persisted ownership requires the original owner_claim_id; no replacement was minted",
                error_name="TASK_OWNER_MISMATCH",
                ca_next_action="retry_with_the_original_owner_claim_id",
                accepted_fields=["session_id", "agent_id", "owner_claim_id"],
                payload_example={"session_id": key[0], "agent_id": key[1],
                                 "owner_claim_id": "<original persisted owner_claim_id>"},
            )
        claim = secrets.token_urlsafe(32)
    result = await bootstrap_session(project_path, key[0], key[1], claim,
                                     verbosity=verbosity, include_discovery=include_discovery)
    if json.loads(result).get("ok"):
        cache[key] = claim
    return result


async def bootstrap_session(project_path: str = "", session_id: str = "",
                            agent_id: str = "", owner_claim_id: str = "",
                            verbosity: str = "terse", include_discovery: bool = False) -> str:
    """V15 R42 T1: compact discovery entry — first-call CA does to avoid 17% schema driff.

    Returns:
      - orchestrator_health (port, version)
      - active_workflow (via workflow_active_get)
      - key_tools_compact (~30 frequently-used tools with required + alias hints)
      - common_pitfalls (R41 lessons distilled)
      - active_skills_index (skill names)
    """
    import json as _json_bs
    if verbosity not in {"terse", "compact", "full", "ref"}:
        return _json_error("Use terse, compact, ref or full", error_name="INVALID_VERBOSITY")
    session_key = (session_id or "").strip()
    agent_key = (agent_id or "").strip()
    if not session_key or not agent_key:
        return _json_error(
            "bootstrap_session requires explicit session_id and agent_id",
            error_name="BOOTSTRAP_SCOPE_REQUIRED",
            reason="bootstrap identity must be scoped to one host session and agent",
            accepted_fields=["session_id", "agent_id"],
            ca_next_action="retry_with_session_id_and_agent_id",
        )
    issued_owner_capability = ""
    claim_recovered = False
    if owner_claim_id and not _valid_owner_claim_id(owner_claim_id):
        return _json_error(
            "owner_claim_id must be caller-generated high entropy (at least 32 bytes)",
            error_name="OWNER_CLAIM_ID_WEAK",
            ca_next_action="generate_a_cryptographically_random_owner_claim_id",
        )
    now = time.time()
    def update_claim(checkpoint):
        nonlocal issued_owner_capability, claim_recovered
        claim_status = str(checkpoint.get("owner_claim_status") or "")
        committed_existing = bool(checkpoint.get("owner_capability_sha256") and claim_status != "pending")
        if not owner_claim_id and not committed_existing:
            return None, _json_error("owner_claim_id is required before creating new bootstrap ownership",
                                     error_name="OWNER_CLAIM_ID_REQUIRED",
                                     ca_next_action="generate_and_reuse_a_high_entropy_owner_claim_id")
        if claim_status == "pending" and now > float(checkpoint.get("owner_claim_expires_at") or 0):
            checkpoint = {k: v for k, v in checkpoint.items()
                          if k not in {"agent_id", "scope", "owner_identity_id", "owner_capability_sha256",
                                       "owner_claim_sha256", "owner_claim_status", "owner_claim_expires_at"}}
            claim_status = ""
        owner_agent = str(checkpoint.get("agent_id") or "")
        if owner_agent and owner_agent != agent_key:
            return None, _json_error("host session already has a different immutable task owner",
                                     error_name="TASK_OWNER_MISMATCH", owner_agent_id=owner_agent,
                                     ca_next_action="use_the_owned_host_session_or_explicit_delegation")
        existing_claim_hash = str(checkpoint.get("owner_claim_sha256") or "")
        incoming_claim_hash = _owner_claim_hash(owner_claim_id) if owner_claim_id else ""
        if existing_claim_hash and not secrets.compare_digest(existing_claim_hash, incoming_claim_hash):
            return None, _json_error("a different owner claim cannot replace the active owner",
                                     error_name="TASK_OWNER_MISMATCH", owner_agent_id=owner_agent,
                                     ca_next_action="retry_with_the_original_owner_claim_id")
        if existing_claim_hash:
            issued_owner_capability = _owner_capability_from_claim(session_key, agent_key, owner_claim_id)
            derived = hashlib.sha256(issued_owner_capability.encode()).hexdigest()
            persisted = str(checkpoint.get("owner_capability_sha256") or "")
            if not persisted or not secrets.compare_digest(derived, persisted):
                return None, _json_error("owner capability commitment does not match the recovery claim",
                                         error_name="TASK_OWNER_STATE_INTEGRITY",
                                         ca_next_action="restore_or_migrate_the_task_checkpoint")
            claim_recovered = True
            return None, None
        if checkpoint.get("owner_capability_sha256"):
            return None, None
        issued_owner_capability = _owner_capability_from_claim(session_key, agent_key, owner_claim_id)
        replacement = dict(checkpoint)
        replacement.update({"agent_id": owner_agent or agent_key, "scope": "root",
                            "owner_identity_id": replacement.get("owner_identity_id") or secrets.token_hex(16),
                            "owner_capability_sha256": hashlib.sha256(issued_owner_capability.encode()).hexdigest(),
                            "owner_claim_sha256": incoming_claim_hash or None, "owner_claim_status": "pending",
                            "owner_claim_expires_at": now + _OWNER_CLAIM_TTL_SECONDS})
        return replacement, None
    try:
        # Anh 2026-08-11: run the durable owner-claim checkpoint update OFF the loop.
        # _task_checkpoint_update goes through the SessionWriter and blocks on a
        # commit Future; calling it synchronously pinned the event loop for the
        # write — a hang vector for bootstrap under contention/large sessions.
        checkpoint, claim_error, wrote_claim = await asyncio.to_thread(
            _task_checkpoint_update, session_key, update_claim
        )
    except TaskCheckpointStorageError as exc:
        return _json_error(str(exc), error_name=exc.error_name, retryable=False,
                           ca_next_action="repair_task_checkpoint_storage_then_retry")
    if claim_error: return claim_error
    checkpoint = checkpoint or {}
    # Health (cheap)
    health = {
        "service": "project-orchestrator",
        "port": 8083,
        "base_url": "http://localhost:8083",
        "version": "v15.r42",
    }
    # CAN-051 transparency: surface live process identity so CAs know
    # generation/restart facts WITHOUT probing /health separately.
    # NOTE: sys.modules lookup, NOT `import orchestrator_server` — when the
    # server runs as a script the module lives under __main__, and a fresh
    # import would re-execute the whole server module (token rewrite, etc.).
    try:
        import sys as _sys_bs
        _osrv_bs = _sys_bs.modules.get("orchestrator_server") or _sys_bs.modules.get("__main__")
        health["generation"] = getattr(_osrv_bs, "SERVER_GENERATION", "") or ""
        health["pid"] = os.getpid()
        health["queue_depth_semantics"] = "requests_in_flight"
        health["restart_path"] = "restart_orchestrator_via_bat (verify /health generation changed)"
    except Exception:
        pass
    # Active workflow (best-effort)
    active_wf = {}
    try:
        active_wf = _json_bs.loads(await workflow_active_get(project_path=project_path))
    except Exception:
        active_wf = {"active": False, "error": "workflow_active_get_unavailable"}

    # Compact key tools — minimal field info to cut token cost
    key_tools_compact = [
        # T1 read
        {"name": "open_session", "tier": "T3", "required": ["project_path", "session_title"],
         "aliases": ["intent->session_title", "title->session_title"],
         "notes": "optional agent_id isolates concurrent active sessions on one project"},
        {"name": "read_file_range", "tier": "T1", "required": ["file_path"],
         "aliases": ["path->file_path", "file->file_path", "start->start_line", "end->end_line",
                     "line_start->start_line", "line_end->end_line"],
         "notes": "scope ∈ {range, function, class, section, file_summary}; supports token_budget and next_ranges"},
        {"name": "read_context_batch", "tier": "T1", "required": ["project_path", "requests"],
         "notes": "Pure bounded batch read under one shared token_budget"},
        {"name": "architecture_context_pack", "tier": "T1", "required": ["project_path"],
         "notes": "Readonly graph-first context; pass symbol/query or file_path fallback; never auto-reindexes"},
        {"name": "search_code", "tier": "T1", "required": ["query"],
         "aliases": ["pattern->query", "regex->query", "q->query"],
         "notes": "mode ∈ {smart, literal, regex}; aliases: plain/text/raw→literal, re/regexp→regex"},
        {"name": "find_symbol", "tier": "T1", "required": ["name", "project_path"]},
        {"name": "find_callers", "tier": "T1", "required": ["symbol", "project_path"]},
        {"name": "query_index", "tier": "T1", "required": ["project_path"]},
        {"name": "folder_stats", "tier": "T1", "required": ["path"],
         "notes": "project_path NOT accepted (harmless silent ignore)"},
        {"name": "folder_dialog_snapshot", "tier": "T1", "required": [],
         "notes": "project_path NOT accepted; pass owner_pid/title_pattern"},
        {"name": "extract_log_section", "tier": "T4", "required": ["page_id", "section_title"],
         "notes": "Use parse_log_section instead if you need decoded base64 + structured JSON"},
        {"name": "parse_log_section", "tier": "T1", "required": ["page_id", "section_title"],
         "notes": "R42: extract_log_section + base64 decode + nested JSON parse in one call"},
        {"name": "audit_tool_matrix", "tier": "T1", "required": ["project_path"]},
        {"name": "acceptance_dashboard", "tier": "T1", "required": ["project_path"]},
        # T2 write
        {"name": "write_fix", "tier": "T2", "required": ["file_path", "start_line", "end_line", "new_content"],
         "aliases": ["path->file_path", "content->new_content", "start->start_line", "end->end_line"],
         "notes": "REQUIRES token. For multi-hunk same file → use write_transaction (bottom-up sort)"},
        {"name": "write_transaction", "tier": "T2", "required": ["operations"],
         "notes": "Atomic batch. R41 BUG FIX: same-file ops refresh cache between writes"},
        {"name": "write_new_file", "tier": "T2", "required": ["path", "new_content"]},
        # T4 runtime
        {"name": "open_browser_page", "tier": "T4", "required": ["url"],
         "aliases": ["wait_until->wait_for", "timeout->timeout_ms"],
         "notes": "wait_for: load|domcontentloaded|networkidle (default load); timeout_ms default 20000; polling apps -> domcontentloaded"},
        {"name": "launch_electron_with_cdp", "tier": "T4", "required": ["executable_path"]},
        {"name": "list_cdp_targets", "tier": "T4", "required": [],
         "notes": "project_path harmless ignore. Use cdp_port (default 9223)"},
        {"name": "verify_active_electron_source", "tier": "T1", "required": ["page_id"],
         "notes": "R41: check which file Electron actually loaded (avoid editing wrong mirror)"},
        {"name": "fill_value", "tier": "T4", "required": ["page_id", "selector", "value"],
         "notes": "Token-protected. encoding='base64' for Windows paths / Unicode"},
        {"name": "click_selector", "tier": "T4", "required": ["page_id", "selector"]},
        {"name": "fill_folder_dialog", "tier": "T4", "required": ["folder_path"],
         "aliases": ["path->folder_path"],
         "notes": "Token-protected. Use folder_dialog_snapshot first. R42: auto_click_button_by_hwnd default True"},
        {"name": "click_and_handle_folder_dialog", "tier": "T4", "required": ["page_id", "click_selector", "folder_path"]},
        {"name": "wait_for_idle", "tier": "T1", "required": ["signals"],
         "notes": "R42: built-in polling — saves 60+ assert_target_idle calls"},
        {"name": "assert_target_idle", "tier": "T1", "required": ["signals"],
         "notes": "R38: check outcome (NOT ok); outcome ∈ {idle, copy_in_progress, busy, unknown}. "
                   "SCHEMA: signals is an ARRAY of objects — NOT flat fields path/stable_seconds. "
                   "Example: signals=[{kind:'folder_growth_stable', folder:'D:/out', stable_seconds:3}, "
                   "{kind:'process_cpu_idle', process_name:'Rawcut.exe', idle_threshold_pct:5, stable_seconds:3}]. "
                   "If unsure → GET /api/tools?name_contains=assert_target_idle FIRST."},
        # Workflow
        {"name": "workflow_list", "tier": "T1", "required": []},
        {"name": "workflow_start", "tier": "T3", "required": ["name"]},
        {"name": "workflow_get_current", "tier": "T1", "required": ["name"]},
        {"name": "workflow_submit_step", "tier": "T3", "required": ["name", "step"],
         "notes": "R42: also accepts step_range=[start, end] for batch advance"},
        {"name": "workflow_goto_step", "tier": "T3", "required": ["name", "target_step"],
         "notes": "R42: forward + backward jump (no cycle limit)"},
        {"name": "workflow_status", "tier": "T1", "required": ["name"],
         "notes": "R38: returns current_step_body + step_manifest (no need to read MD)"},
        {"name": "check_server_path", "tier": "T1", "required": ["path"],
         "notes": "R42: probe UNC/HTTP/local accessibility"},
        {"name": "bootstrap_session", "tier": "T1", "required": ["session_id", "agent_id", "owner_claim_id"],
         "notes": "Canonical discovery entrypoint; persist and reuse the same owner_claim_id until begin_task confirms ownership"},
        {"name": "begin_task", "tier": "T3", "required": ["session_id", "prompt", "agent_id"],
         "notes": "Call once per real user prompt; root owner requires server-issued owner_capability, and a pending claim also requires the original owner_claim_id"},
        {"name": "get_task_checkpoint", "tier": "T1", "required": ["session_id"],
         "notes": "Compact active-task anchor for tool batches and post-compaction restore"},
    ]

    common_pitfalls = [
        "Bootstrap recipe: bootstrap_session(session_id, agent_id, owner_claim_id) → keep the original claim → begin_task(..., owner_capability[, owner_claim_id]).",
        "JSON transport recipe: command is inside an outer JSON string; JSON-encode backslashes first (prefer Invoke-RestMethod + ConvertTo-Json).",
        "Windows shell recipe: shell operators (|, &&, >) and findstr/dir for file reading are fragile; use the direct search/read tool or a script-file route instead of retrying the command.",
        "PowerShell recipe: only a standalone script.ps1 is a -File invocation; findstr text script.ps1 is a normal command and must not be auto-routed to -File.",
        "STALE_INDEX recipe: switch once to search_code(mode=literal) or read_file_range; do not retry the unchanged complex regex.",
        "Do not repeat deterministic failures.",
        "Use bounded, filtered discovery.",
        "Use script-file transport on Windows for multiline, regex, JSON, Unicode, or heavy quoting.",
    ]

    # Active skills via skill_router file scan (cheap)
    active_skills_index = []
    try:
        from pathlib import Path as _PathBs
        skill_root = _PathBs(__file__).parent / ".ToolA_Skill" / "skills"
        if skill_root.exists():
            for d in sorted(skill_root.iterdir()):
                if d.is_dir() and (d / "SKILL.md").exists():
                    active_skills_index.append({"name": d.name})
    except Exception:
        pass

    # Stable contract ref plus explicit session scope. The ref changes whenever
    # canonical rule content, server generation, or compact discovery changes.
    import hashlib as _hl
    rule_meta_full = get_rule_core_metadata()
    rule_meta = {
        "sha256": rule_meta_full["sha256"],
        "bytes": rule_meta_full["bytes"],
        "core_rule_path": rule_meta_full["core_rule_path"],
    }
    scope = {
        "session_id": session_key,
        "agent_id": agent_key,
    }
    if project_path:
        scope["project_path"] = str(Path(project_path).resolve())
    ref_material = _json_bs.dumps({
        "generation": health.get("generation", ""),
        "rule_sha256": rule_meta["sha256"],
        "tools": key_tools_compact,
        "skills": [item["name"] for item in active_skills_index],
    }, ensure_ascii=False, sort_keys=True)
    ruleset_sha256 = _hl.sha256(ref_material.encode("utf-8")).hexdigest()
    bootstrap_ref = ruleset_sha256[:16]
    try:
        checkpoint = _task_checkpoint_load(session_key) or {}
    except TaskCheckpointStorageError as exc:
        return _json_error(str(exc), error_name=exc.error_name, retryable=False,
                           ca_next_action="repair_task_checkpoint_storage_then_retry")
    if agent_key:
        checkpoint = ((checkpoint.get("subtasks") or {}).get(agent_key) or checkpoint)
    checkpoint_payload = {
        key: checkpoint[key]
        for key in ("task_epoch", "prompt_hash", "active_task") if key in checkpoint
    }
    checkpoint_material = _json_bs.dumps({
        "session_id": session_key,
        "agent_id": agent_key,
        **checkpoint_payload,
    }, ensure_ascii=False, sort_keys=True)
    checkpoint_sha256 = _hl.sha256(checkpoint_material.encode("utf-8")).hexdigest()

    task_contract = {
        "begin_task_required": True,
        "session_id_source": "open_session or active session",
        "prompt_hash": "sha256(canonical repaired UTF-8 full prompt); server-issued value is authoritative and opaque; copy it unchanged to X-Prompt-Hash and reload it via get_task_checkpoint after recovery",
        "retry_policy": "correct once; stop on second deterministic failure",
    }

    response = {
        "ok": True,
        "session_scope": scope,
    }
    if issued_owner_capability:
        response.update({
            "owner_capability": issued_owner_capability,
            "owner_claim_id": owner_claim_id,
            "owner_claim_status": str(checkpoint.get("owner_claim_status") or ""),
            "owner_claim_recovered": claim_recovered,
        })
        if response["owner_claim_status"] == "pending":
            response["owner_claim_expires_in_seconds"] = max(
                0, int(float(checkpoint.get("owner_claim_expires_at") or now) - time.time()))
    response.update({
        "ca_next_action": "call_begin_task_with_owner_capability",
        "bootstrap_ref": bootstrap_ref,
        "ruleset_sha256": ruleset_sha256,
        "rule_source_sha256": rule_meta["sha256"],
        "session_scope": scope,
        "task_checkpoint": checkpoint_payload,
        "task_checkpoint_sha256": checkpoint_sha256,
        "rule": rule_meta,
        "discovery": {
            "names": "GET /api/tools/names",
            "schema": "GET /api/tools/<tool>/schema",
            "call": "HTTP /api/call/<tool>",
        },
        "orchestrator_health": health,
        "task_contract": task_contract,
        "hint": "First-call lifecycle macro; reuse bootstrap_ref for this generation.",
    })
    if verbosity == "full" or include_discovery:
        response.update({"active_workflow": active_wf, "key_tools_compact": key_tools_compact,
                         "common_pitfalls": common_pitfalls, "active_skills_index": active_skills_index})
    return json.dumps(response, ensure_ascii=False)


async def project_context_pack(project_path: str, refresh: bool = False) -> str:
    """CAN-053 (codo P1): one-call compact project profile for first touch.

    Component roots, languages, groups, hotspots, entrypoints, graph freshness
    + recommended next tools. Cached in .orchestrator_cache/context_pack.json
    keyed by INDEX fingerprint — repeat calls are free (no rescan)."""
    root = Path(project_path).resolve()
    if not root.exists():
        return _json_error(f"project_path not found: {project_path}",
                           error_name="PATH_NOT_FOUND")
    cache_dir = _project_cache_dir(str(root))
    idx_path = cache_dir / "INDEX.json"
    pack_path = cache_dir / "context_pack.json"
    idx_stat = idx_path.stat() if idx_path.exists() else None
    fp_now = f"{int(idx_stat.st_mtime)}-{idx_stat.st_size}" if idx_stat else "no-index"
    if not refresh and pack_path.exists():
        try:
            cached = json.loads(pack_path.read_text(encoding="utf-8"))
            if cached.get("fingerprint") == fp_now:
                cached["cache_hit"] = True
                return json.dumps(cached, ensure_ascii=False)
        except Exception:
            pass
    index = _load_index(str(root))
    if not index:
        return _json_error("INDEX not found. Run generate_project_map first.",
                           error_name="INDEX_MISSING",
                           ca_next_action="generate_project_map")
    files = index.get("files", {}) or {}
    groups: dict = {}
    langs: dict = {}
    comp_roots: dict = {}
    for rel, info in files.items():
        g = (info or {}).get("group", "other")
        groups[g] = groups.get(g, 0) + 1
        ext = str(Path(str(rel)).suffix or "").lower()
        if ext:
            langs[ext] = langs.get(ext, 0) + 1
        top = str(rel).replace("\\\\", "/").split("/")[0]
        comp_roots[top] = comp_roots.get(top, 0) + 1
    hotspots = sorted(
        ({"file": rel, "loc": (info or {}).get("loc", 0),
          "complexity": (info or {}).get("complexity_score", 0)}
         for rel, info in files.items()),
        key=lambda x: x["complexity"], reverse=True)[:5]
    entrypoints = [rel for rel, info in files.items()
                   if (info or {}).get("group") == "entrypoint"][:10]
    graph = {}
    try:
        from orchestrator.graph.status import graph_status as _gs
        graph = {k: v for k, v in (_gs(str(root)) or {}).items()
                 if k in ("enabled", "nodes", "edges", "files", "freshness",
                          "stale_files", "extract_js_enabled", "coverage_note")}
    except Exception:
        graph = {"enabled": False}
    pack = {
        "ok": True,
        "fingerprint": fp_now,
        "cache_hit": False,
        "project_path": str(root),
        "files_indexed": len(files),
        "component_roots": dict(sorted(comp_roots.items(), key=lambda x: -x[1])[:12]),
        "languages": dict(sorted(langs.items(), key=lambda x: -x[1])[:12]),
        "groups": groups,
        "hotspots": hotspots,
        "entrypoints": entrypoints,
        "graph": graph,
        "recommended_next_tools": [
            "query_index(file_pattern=...) - find files without blind probes",
            "cbm_get_architecture - bounded module/dependency overview",
            "find_symbol / search_code(mode=literal) - targeted code lookup",
            "read_file_range - only after the path is confirmed",
        ],
    }
    try:
        pack_path.write_text(json.dumps(pack, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    except Exception:
        pass
    return json.dumps(pack, ensure_ascii=False)


async def resolve_candidate_paths(project_path: str, paths: list = None) -> str:
    """CAN-053: batch existence check — one call instead of N blind read probes."""
    root = Path(project_path).resolve()
    import difflib
    import os as _os
    index = _load_index(str(root)) or {}
    names = {}
    for k in (index.get("files") or {}):
        names.setdefault(_os.path.basename(str(k)).lower(), str(k))
    out = []
    for p in (paths or [])[:50]:
        fp = (root / str(p)) if not Path(str(p)).is_absolute() else Path(str(p))
        item = {"path": str(p), "exists": fp.exists()}
        if not item["exists"] and names:
            m = difflib.get_close_matches(_os.path.basename(str(p)).lower(),
                                          list(names), n=3, cutoff=0.6)
            if m:
                item["fuzzy_suggestions"] = [names[x] for x in m]
        out.append(item)
    return json.dumps({"ok": True, "checked": len(out), "results": out},
                      ensure_ascii=False)


async def check_server_path(path: str, mode: str = "auto", timeout_sec: int = 5, verify_tls: bool = True) -> str:
    """V15 R42 T1: probe UNC/HTTP/local path accessibility.

    Modes:
      - auto: detect by prefix (\\\\server → unc; http(s):// → http; else local)
      - unc: SMB share — uses os.path.exists with timeout via threading
      - http/https: HEAD request, return status_code
      - local: file or folder existence + read permission
    """
    import time as _t_csp
    if not path:
        return _json_error("path required", error_name="MISSING_REQUIRED_FIELD")
    detected = mode
    if mode == "auto":
        if path.startswith("\\\\") or path.startswith("//"):
            detected = "unc"
        elif path.startswith("http://"):
            detected = "http"
        elif path.startswith("https://"):
            detected = "https"
        else:
            detected = "local"
    t0 = _t_csp.time()
    result = {
        "ok": True,
        "path": path,
        "mode": mode,
        "mode_detected": detected,
        "reachable": False,
    }
    try:
        if detected in ("http", "https"):
            import ssl as _ssl
            import urllib.request as _req
            if verify_tls:
                ctx = _ssl.create_default_context()
            else:
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
                if detected == "https":
                    result["tls_verified"] = False
                    result["security_warning"] = (
                        "TLS verification DISABLED (verify_tls=false). "
                        "Response is NOT authenticated — an on-path attacker or hostile proxy "
                        "can impersonate the target. Use only on isolated or corporate networks "
                        "with out-of-band trust."
                    )
            req = _req.Request(path, method="HEAD")
            try:
                with _req.urlopen(req, timeout=timeout_sec, context=ctx) as resp:
                    result["reachable"] = True
                    result["status_code"] = resp.status
                    result["headers_excerpt"] = dict(list(resp.headers.items())[:5])
                    if verify_tls and detected == "https":
                        result["tls_verified"] = True
            except Exception as exc:
                result["reachable"] = False
                result["error"] = str(exc)
        elif detected == "unc":
            # SMB existence check via Path; can hang if server unreachable — use threaded timeout
            import queue as _q_csp
            import threading as _th_csp
            q = _q_csp.Queue()
            def _probe():
                try:
                    p = Path(path)
                    q.put(("ok", p.exists(), p.is_file() if p.exists() else None, p.is_dir() if p.exists() else None))
                except Exception as e:
                    q.put(("err", str(e), None, None))
            th = _th_csp.Thread(target=_probe, daemon=True)
            th.start()
            try:
                tag, a, b, c = q.get(timeout=timeout_sec)
                if tag == "ok":
                    result["reachable"] = bool(a)
                    result["is_file"] = b
                    result["is_dir"] = c
                else:
                    result["reachable"] = False
                    result["error"] = a
            except Exception:
                result["reachable"] = False
                result["error"] = f"UNC probe timeout after {timeout_sec}s"
        else:  # local
            p = Path(path)
            result["reachable"] = p.exists()
            if p.exists():
                result["is_file"] = p.is_file()
                result["is_dir"] = p.is_dir()
                try:
                    result["size_bytes"] = p.stat().st_size if p.is_file() else None
                except Exception:
                    pass
                try:
                    result["readable"] = os.access(str(p), os.R_OK)
                    result["writable"] = os.access(str(p), os.W_OK)
                except Exception:
                    pass
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)
    result["latency_ms"] = int((_t_csp.time() - t0) * 1000)
    # V15 R45 — flag this tool as the Unicode-safe path probe. UNC mode uses
    # Python's Path.exists() which preserves Vietnamese diacritics and other
    # non-ASCII characters end-to-end (vs. a PowerShell Test-Path round-trip
    # that re-encodes through the console code page and silently drops
    # diacritics → false-negative on Vietnamese network folders).
    result["unicode_safe"] = True
    result["unicode_safe_note"] = (
        "Python Path.exists() preserves Vietnamese diacritics. Do NOT wrap this "
        "tool's path argument in PowerShell Test-Path / Get-ChildItem — those "
        "lose non-ASCII via console code page conversion."
    )
    result["ca_next_action"] = (
        "path_reachable_proceed" if result.get("reachable") else
        "path_not_reachable_check_credentials_or_url_or_typo"
    )
    return json.dumps(result, ensure_ascii=False)


async def webfetch(url: str, max_chars: int = 200000, format: str = "text",
                    timeout_seconds: int = 15, verify_tls: bool = True) -> str:
    """V15 R44 T1: fetch URL content (HTTP/HTTPS GET).

    Re-introduces the historic `webfetch` tool used by getfootage workflow v3
    to pull Google Sheets CSV exports. Returns raw text under `content`.

    format:
      - text (default): return raw decoded UTF-8 text
      - json: parse content as JSON and return under `parsed`
    Truncates content to max_chars (default 200KB) and reports `truncated`.
    """
    import ssl as _ssl_wf
    import urllib.error as _uerr_wf
    import urllib.request as _ur_wf
    if not url:
        return _json_error("url required", error_name="MISSING_REQUIRED_FIELD")
    if not (url.startswith("http://") or url.startswith("https://")):
        return _json_error(
            "url must start with http:// or https://",
            error_name="UNSUPPORTED_PROTOCOL", url=url,
            ca_next_action="prefix_with_http_or_https",
        )
    try:
        if verify_tls:
            ctx = _ssl_wf.create_default_context()
        else:
            ctx = _ssl_wf.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl_wf.CERT_NONE
        req = _ur_wf.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (ToolA Orchestrator R44 webfetch)",
                "Accept": "text/csv, text/plain, application/json, text/html;q=0.8, */*;q=0.5",
            },
        )
        with _ur_wf.urlopen(req, timeout=max(2, int(timeout_seconds or 15)), context=ctx) as resp:
            status_code = getattr(resp, "status", None) or resp.getcode()
            content_type = resp.headers.get("Content-Type", "")
            raw_bytes = resp.read(max(1024, int(max_chars or 200000)) + 1)
        truncated = len(raw_bytes) > max_chars
        decoded = raw_bytes[:max_chars].decode("utf-8", errors="replace")
        if format == "json":
            try:
                parsed = json.loads(decoded)
                resp = {
                    "ok": True, "url": url, "status_code": status_code,
                    "content_type": content_type, "format": "json",
                    "parsed": parsed, "truncated": truncated,
                    "length": len(decoded),
                }
                if url.startswith("https://"):
                    resp["tls_verified"] = verify_tls
                    if not verify_tls:
                        resp["security_warning"] = (
                            "TLS verification DISABLED (verify_tls=false). "
                            "Response is NOT authenticated — an on-path attacker or hostile proxy "
                            "can impersonate the target. Use only on isolated or corporate networks "
                            "with out-of-band trust."
                        )
                return json.dumps(resp, ensure_ascii=False)
            except Exception as exc:
                return _json_error(
                    f"JSON parse failed: {exc}",
                    error_name="WEBFETCH_JSON_PARSE_FAILED",
                    url=url, content_excerpt=decoded[:200],
                )
        # text mode: return raw content + line/row counts as a convenience.
        lines = decoded.splitlines() if decoded else []
        resp = {
            "ok": True, "url": url, "status_code": status_code,
            "content_type": content_type, "format": "text",
            "content": decoded, "length": len(decoded),
            "line_count": len(lines), "truncated": truncated,
        }
        if url.startswith("https://"):
            resp["tls_verified"] = verify_tls
            if not verify_tls:
                resp["security_warning"] = (
                    "TLS verification DISABLED (verify_tls=false). "
                    "Response is NOT authenticated — an on-path attacker or hostile proxy "
                    "can impersonate the target. Use only on isolated or corporate networks "
                    "with out-of-band trust."
                )
        return json.dumps(resp, ensure_ascii=False)
    except _uerr_wf.HTTPError as exc:
        return _json_error(
            f"HTTP {exc.code}: {exc.reason}",
            error_name="WEBFETCH_HTTP_ERROR",
            url=url, status_code=exc.code,
        )
    except _uerr_wf.URLError as exc:
        return _json_error(
            f"URL error: {exc.reason}",
            error_name="WEBFETCH_URL_ERROR", url=url,
        )
    except Exception as exc:
        return _json_error(
            f"webfetch failed: {exc}",
            error_name="WEBFETCH_FAILED", url=url,
        )


# ─── V15 R49 — High-level workflow primitives ────────────────────────────────
# These primitives encapsulate multi-step intents that workflows (esp. v3) used
# to fan out across 10+ micro-calls. CA passes ONE intent + URL/paths and the
# primitive runs the full chain (fetch → parse → classify → sample → root-check)
# and returns a structured verdict + evidence trail. Reduces CA orchestration
# burden — Report #4: "Workflow đóng gói ở mức intent, không bắt CA tự ráp vi mô".


def _r49_parse_csv_row(line: str) -> list:
    """Minimal RFC-4180-ish CSV parser: handles quoted fields with embedded commas."""
    cells: list = []
    cur = []
    in_q = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            if in_q and i + 1 < len(line) and line[i + 1] == '"':
                cur.append('"')
                i += 2
                continue
            in_q = not in_q
            i += 1
            continue
        if ch == ',' and not in_q:
            cells.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    cells.append("".join(cur))
    return cells


# V15 R53 — Header token set used to detect "polluted header row" (sheet
# author concatenated column labels into the same cell as a path) AND to
# trim trailing header noise from extracted UNC paths. Pattern observed
# live on v1 sheet row 12: a UNC path cell ending in
#   `\H. 599 STT,FOOTAGE,TIME,VOICE,...,REF,"",""`
# Detection rules:
#   - row excluded when 2+ DISTINCT header tokens appear in ANY cell
#   - UNC path truncated at FIRST header token match (rest is header noise)
_R53_HEADER_TOKEN_RE = re.compile(
    r"\b(STT|FOOTAGE|TIME|VOICE\s*OFF|VOICE|NOTE|REF|EFFECTS?|HIGHLIGHT(?:\s*TEXT)?|"
    r"C[ÂA]U\s*H[ỎO]I|GHI\s*CH[ÚU]|TARGET|SOURCE\s*PATH)\b",
    re.IGNORECASE,
)


def _r53_count_header_tokens(cell: str) -> int:
    """Return count of DISTINCT header-label tokens found in a cell."""
    if not cell:
        return 0
    matches = _R53_HEADER_TOKEN_RE.findall(cell)
    distinct = set(m.upper().replace(" ", "") for m in matches)
    return len(distinct)


def _r53_trim_header_noise(unc_path: str) -> tuple[str, bool]:
    """Trim header-noise tail off a UNC path. Returns (clean_path, trimmed)."""
    if not unc_path:
        return unc_path, False
    m = _R53_HEADER_TOKEN_RE.search(unc_path)
    if not m:
        return unc_path, False
    # Cut at the segment boundary BEFORE the header token. Walk backwards
    # from match start to last `\\` or `/` separator so we keep complete path.
    cut = m.start()
    # Strip trailing whitespace/separators left over after cut.
    trimmed = unc_path[:cut].rstrip()
    while trimmed.endswith(("\\", "/", " ", ",", '"')):
        trimmed = trimmed[:-1]
    return trimmed, True


def _r53_ascii_safe(s: str) -> str:
    """Return ASCII-folded version of `s` (Vietnamese diacritics stripped).

    Use for SAFE TERMINAL DISPLAY where Windows console code page (CP-1252 /
    OEM 850) would otherwise show mojibake for Vietnamese characters. The
    original `path` field still carries true UTF-8 bytes for filesystem ops;
    `path_ascii_safe` is a side-channel for log/terminal scenarios.
    """
    import unicodedata as _ud
    if not s:
        return s
    try:
        nfkd = _ud.normalize("NFKD", s)
        ascii_bytes = nfkd.encode("ascii", "ignore")
        return ascii_bytes.decode("ascii")
    except Exception:
        return s


def _r53_path_has_non_ascii(s: str) -> bool:
    return bool(s) and any(ord(c) > 127 for c in s)


# V15 R54 — Suspected input mojibake detector. Reuses module-level
# _MOJIBAKE_MARKERS tuple. Use case: when CA forwards a UTF-8 Vietnamese
# path through a non-UTF-8 transport (PowerShell inline, terminal CP-1252,
# OEM 850), the bytes that arrive at [A] still look "non-ASCII" by ord()
# check but encode garbage (e.g. UTF-8 'ư' read as CP-1252 = `Æ°`, 'ổ' =
# `á»•`). non_ascii_present alone can't distinguish "real Vietnamese" vs
# "mojibake from upstream CP corruption". This helper flags the suspicion
# so CA reroutes via base64 / script-file before treating the path as real.
def _r54_detect_mojibake(s: str) -> tuple[bool, list]:
    """Return (mojibake_suspected, found_markers[:5])."""
    if not s:
        return False, []
    found: list = []
    for marker in _MOJIBAKE_MARKERS:
        if marker in s:
            found.append(marker)
            if len(found) >= 5:
                break
    return bool(found), found


async def fetch_sheet_csv_summary(url: str, max_rows: int = 5000,
                                     timeout_seconds: int = 15,
                                     unc_pattern: str = r"\\\\[^\\]+\\[^\\]+",
                                     sample_row_count: int = 5,
                                     sample_max_chars_per_row: int = 400,
                                     verify_tls: bool = True) -> str:
    """V15 R49 T1: high-level v3 primitive — fetch Google Sheets CSV export
    via webfetch, parse rows, classify cells into path types, surface a
    structured summary.

    Args:
      url: CSV export URL (e.g. .../gviz/tq?tqx=out:csv&gid=0)
      max_rows: cap parsed rows for safety (default 5000)
      timeout_seconds: webfetch HTTP timeout
      unc_pattern: regex for UNC paths (default `\\\\server\\share` prefix)

    Returns: {
      ok, url, status_code, fetched_at,
      row_count_total, row_count_data, row_count_header_excluded, row_count_empty,
      unc_path_count, unc_path_unique_parent_count, unc_parent_folders (top 10),
      http_url_count, local_path_count,
      header_paths_excluded (max 10),
      first_3_data_rows, last_3_data_rows,
      truncated_rows, ca_next_action
    }
    Replaces v3 steps 7-10 (fetch CSV + scan UNC + exclude headers + count types).
    """
    import re as _re_fs
    from datetime import datetime as _dt_fs
    raw = await webfetch(url=url, max_chars=2_000_000, format="text",
                           timeout_seconds=timeout_seconds, verify_tls=verify_tls)
    try:
        wf = json.loads(raw)
    except Exception as exc:
        return _json_error(f"webfetch returned non-JSON: {exc}",
                            error_name="WEBFETCH_FAILED", url=url)
    if not wf.get("ok"):
        # Propagate webfetch failure with its phase/error_name.
        return raw
    content = wf.get("content") or ""
    lines = content.splitlines()
    parsed_rows: list = []
    for idx, ln in enumerate(lines):
        if idx >= max_rows:
            break
        if not ln.strip():
            continue
        cells = _r49_parse_csv_row(ln)
        parsed_rows.append({"row_index": idx, "cells": cells, "raw": ln})
    truncated_rows = len(lines) > max_rows

    unc_re = _re_fs.compile(unc_pattern)
    http_re = _re_fs.compile(r"https?://", _re_fs.IGNORECASE)
    drive_re = _re_fs.compile(r"^[A-Za-z]:[\\/]")
    # Header detection: a path cell that ends with " STT", just "STT", or whose
    # ALL data rows in that column are empty is treated as a column label.
    header_suffix_re = _re_fs.compile(r"\bSTT\b\"?$", _re_fs.IGNORECASE)

    unc_paths_all: list = []
    unc_parents: dict = {}
    http_count = 0
    local_count = 0
    header_paths_excluded: list = []
    data_rows_count = 0
    empty_rows_count = 0
    # R53 — header-row exclusion: when 2+ DISTINCT column-label tokens
    # appear in ANY cell, treat whole row as header concatenation drift.
    header_rows_excluded: list = []

    for row in parsed_rows:
        cells = row["cells"]
        if not any((c or "").strip() for c in cells):
            empty_rows_count += 1
            continue
        # R53 — row-level header detector (whole row excluded when polluted)
        row_max_tokens = max(
            (_r53_count_header_tokens((c or "")) for c in cells),
            default=0,
        )
        if row_max_tokens >= 2:
            if len(header_rows_excluded) < 10:
                # Find the cell that triggered exclusion for evidence
                triggering = next(
                    (c for c in cells if _r53_count_header_tokens(c or "") >= 2),
                    "",
                )
                header_rows_excluded.append({
                    "row_index": row["row_index"],
                    "tokens_in_row": row_max_tokens,
                    "triggering_cell": (triggering or "")[:200],
                })
            continue
        data_rows_count += 1
        for cell in cells:
            c = (cell or "").strip().strip('"')
            if not c:
                continue
            # Header suffix detector first — short-circuit header_paths_excluded
            if header_suffix_re.search(c):
                if len(header_paths_excluded) < 10:
                    header_paths_excluded.append({
                        "row_index": row["row_index"], "value": c[:200],
                    })
                continue
            if unc_re.match(c):
                # R53 — trim header-noise tail (e.g. `\path\file STT,FOOTAGE,...`)
                clean_path, trimmed = _r53_trim_header_noise(c)
                # If after trimming the path no longer looks UNC, skip it.
                if not clean_path or not unc_re.match(clean_path):
                    continue
                entry = {
                    "row_index": row["row_index"],
                    "path": clean_path[:300],
                    "path_ascii_safe": _r53_ascii_safe(clean_path[:300]),
                    "non_ascii_present": _r53_path_has_non_ascii(clean_path),
                }
                if trimmed:
                    entry["header_noise_trimmed"] = True
                unc_paths_all.append(entry)
                # parent = first 3 segments after share, i.e. \\server\share\seg1
                parts = clean_path.replace("/", "\\").split("\\")
                # parts: ['','','server','share','seg1',...]
                parent_segs = parts[:5] if len(parts) >= 5 else parts
                parent_key = "\\".join(parent_segs)
                unc_parents[parent_key] = unc_parents.get(parent_key, 0) + 1
                continue
            if http_re.match(c):
                http_count += 1
                continue
            if drive_re.match(c):
                local_count += 1
                continue

    parent_folders_top = sorted(unc_parents.items(), key=lambda kv: -kv[1])[:10]
    parent_folders_out = [{"parent": k, "row_count": v} for k, v in parent_folders_top]

    # R55 — bump sample count + dedicate one pool to rows that actually
    # contain UNC/HTTP/local paths (CA audit needs CONTENT, not just count).
    _n = max(1, int(sample_row_count or 5))
    _cap = max(80, int(sample_max_chars_per_row or 400))
    _non_empty_rows = [r for r in parsed_rows if any((c or "").strip() for c in r["cells"])]
    first_n = _non_empty_rows[:_n]
    last_n = _non_empty_rows[-_n:] if len(_non_empty_rows) >= _n else _non_empty_rows
    # R55 — rows that contain at least one UNC/HTTP/local path AFTER header-row exclusion
    rows_with_path_pool: list = []
    for row in _non_empty_rows:
        _has_path = False
        for cell in row["cells"]:
            c = (cell or "").strip().strip('"')
            if not c:
                continue
            if unc_re.match(c) or http_re.match(c) or drive_re.match(c):
                # Skip header-polluted rows (already excluded from data analysis)
                if _r53_count_header_tokens(c) >= 2:
                    continue
                _has_path = True
                break
        if _has_path:
            rows_with_path_pool.append(row)
        if len(rows_with_path_pool) >= _n * 3:
            break  # bounded scan
    rows_with_path_sample = rows_with_path_pool[:_n]

    return json.dumps({
        "ok": True,
        "url": url,
        "status_code": wf.get("status_code"),
        "fetched_at": _dt_fs.now().isoformat(timespec="seconds"),
        "tls_verified": wf.get("tls_verified"),
        "security_warning": wf.get("security_warning"),
        "row_count_total": len(lines),
        "row_count_parsed": len(parsed_rows),
        "row_count_data": data_rows_count,
        "row_count_empty": empty_rows_count,
        "row_count_header_excluded": len(header_paths_excluded),
        # R53 — distinct from path-suffix STT match; counts WHOLE rows
        # excluded because cells contained ≥2 column-label tokens
        # (header concatenation drift).
        "row_count_header_row_excluded": len(header_rows_excluded),
        "unc_path_count": len(unc_paths_all),
        "unc_path_unique_parent_count": len(unc_parents),
        "unc_parent_folders": parent_folders_out,
        "unc_paths_sample": unc_paths_all[:20],
        "http_url_count": http_count,
        "local_path_count": local_count,
        "header_paths_excluded": header_paths_excluded,
        "header_rows_excluded": header_rows_excluded,
        # R55 — sample now configurable via sample_row_count (default 5);
        # field names changed from first_3_data_rows / last_3_data_rows to
        # first_n_data_rows / last_n_data_rows. CA audit gets CONTENT, not just count.
        "sample_row_count_applied": _n,
        "sample_max_chars_applied": _cap,
        "first_n_data_rows": [
            {"row_index": r["row_index"], "raw": r["raw"][:_cap]} for r in first_n
        ],
        "last_n_data_rows": [
            {"row_index": r["row_index"], "raw": r["raw"][:_cap]} for r in last_n
        ],
        # R55 NEW — rows containing actual UNC/HTTP/local paths (skip prose / TOC rows)
        "rows_with_path_sample": [
            {"row_index": r["row_index"], "raw": r["raw"][:_cap]}
            for r in rows_with_path_sample
        ],
        "rows_with_path_total_scanned": len(rows_with_path_pool),
        "truncated_rows": truncated_rows,
        "max_rows_applied": max_rows,
        "ca_next_action": (
            "sample_unc_paths_with_root_check_then_compare_log"
            if unc_paths_all else "no_unc_paths_skip_server_audit"
        ),
        "primitive": "R49/R53/R55 fetch_sheet_csv_summary — replaces v3 steps 7-10 (R53: row-level header drift + header-noise trim + ASCII-safe; R55: configurable sample_row_count + dedicated rows_with_path_sample for content audit)",
    }, ensure_ascii=False)


async def sample_unc_paths_with_root_check(paths: list,
                                             sample_count: int = 3,
                                             root_check: bool = True,
                                             timeout_sec: int = 5,
                                             unc_pattern: str = r"\\\\[^\\]+\\[^\\]+") -> str:
    """V15 R49 T1: high-level v3 primitive — sample N UNC paths from input,
    test each via Path.exists() (Unicode-safe), and if any fails AND root_check
    is on, also probe the root share `\\\\<server>\\<share>` to distinguish
    "path stale" from "server down".

    Args:
      paths: list of candidate UNC paths (from fetch_sheet_csv_summary or other)
      sample_count: how many to test (default 3 per v3 step 12 rule)
      root_check: probe `\\\\server\\share` for any failed sample (v3 step 14)
      timeout_sec: per-probe timeout

    Returns: {
      ok, samples_tested, samples_reachable, samples_failed,
      per_sample[]: {path, reachable, file_count, dir_count, latency_ms, error},
      root_share_status[]: {root, reachable, error},
      verdict: all_reachable | partial_reachable_paths_stale | all_failed_server_down,
      ca_next_action
    }
    Replaces v3 steps 12-14 (sample UNC + folder_stats per sample + root probe).
    """
    import queue as _q_su
    import re as _re_su
    import threading as _th_su
    import time as _t_su
    if not isinstance(paths, list):
        return _json_error("paths must be a list of strings",
                            error_name="WRONG_ARGUMENTS",
                            expected_type="array",
                            field="paths",
                            received_type=type(paths).__name__,
                            accepted_shape={"paths": ["\\\\server\\share\\folder"]})
    bad_items = [
        {"index": i, "received_type": type(p).__name__}
        for i, p in enumerate(paths)
        if not isinstance(p, str)
    ]
    if bad_items:
        return _json_error("each item in paths must be a string",
                            error_name="WRONG_ARGUMENTS",
                            field="paths",
                            invalid_items=bad_items,
                            accepted_shape={"paths": ["\\\\server\\share\\folder"]})
    unc_re = _re_su.compile(unc_pattern)
    # Dedup + filter to valid UNC.
    seen = set()
    valid: list = []
    for p in paths:
        s = str(p).strip().strip('"')
        if not s or s in seen:
            continue
        seen.add(s)
        if unc_re.match(s):
            valid.append(s)
    # Picking strategy: respect input order but try to pick from at least 2
    # distinct parent folders (v3 step 12 rule).
    chosen: list = []
    parents_seen: set = set()
    for p in valid:
        parts = p.replace("/", "\\").split("\\")
        parent_key = "\\".join(parts[:5]) if len(parts) >= 5 else "\\".join(parts)
        if parent_key not in parents_seen:
            parents_seen.add(parent_key)
            chosen.append(p)
            if len(chosen) >= sample_count:
                break
    # If still under sample_count, pad with remaining valid paths.
    for p in valid:
        if len(chosen) >= sample_count:
            break
        if p not in chosen:
            chosen.append(p)

    def _probe(target: str) -> dict:
        # R53 — surface path_ascii_safe + non_ascii_present so CA reading
        # raw `path` field in mojibake-prone terminals (Windows console CP-1252)
        # has a safe fallback. `path` itself stays true UTF-8 for filesystem ops.
        # R54 — additionally detect SUSPECTED mojibake (UTF-8 bytes that were
        # already re-encoded through CP-1252/OEM upstream). Distinguishes
        # "real Vietnamese path" (non_ascii=true, mojibake=false) from
        # "corrupted upstream bytes" (non_ascii=true, mojibake=true).
        moji_suspected, moji_markers = _r54_detect_mojibake(target)
        out = {
            "path": target,
            "path_ascii_safe": _r53_ascii_safe(target),
            "non_ascii_present": _r53_path_has_non_ascii(target),
            "mojibake_suspected": moji_suspected,
            "mojibake_markers": moji_markers,
            "reachable": False, "file_count": None,
            "dir_count": None, "latency_ms": 0, "error": "", "winerror": None,
        }
        t0 = _t_su.time()
        q = _q_su.Queue()

        def _run():
            try:
                p = Path(target)
                # R85: pathlib.exists() swallows OSError and returns False, which
                # masks UNC auth/session failures (WinError 1326 logon-failure)
                # as "path stale / server down". Re-probe via os.stat to surface
                # the real winerror so the verdict can distinguish
                # auth-context-mismatch from a genuinely down server.
                try:
                    exists = p.exists()
                except OSError as _oe0:
                    q.put(("err", False, None, None, f"WinError {getattr(_oe0,'winerror','')}: {_oe0}"))
                    return
                if not exists:
                    try:
                        os.stat(str(p))
                    except FileNotFoundError:
                        q.put(("ok", False, None, None, ""))
                        return
                    except OSError as _oe1:
                        q.put(("err", False, None, None, f"WinError {getattr(_oe1,'winerror','')}: {_oe1}"))
                        return
                    q.put(("ok", False, None, None, ""))
                    return
                if p.is_dir():
                    fc = 0
                    dc = 0
                    try:
                        for child in p.iterdir():
                            try:
                                if child.is_file():
                                    fc += 1
                                elif child.is_dir():
                                    dc += 1
                            except Exception:
                                pass
                    except Exception:
                        pass
                    q.put(("ok", True, fc, dc, ""))
                else:
                    q.put(("ok", True, None, None, ""))
            except Exception as e:
                q.put(("err", False, None, None, str(e)))

        th = _th_su.Thread(target=_run, daemon=True)
        th.start()
        try:
            tag, reach, fc, dc, err = q.get(timeout=timeout_sec)
            out["reachable"] = bool(reach)
            out["file_count"] = fc
            out["dir_count"] = dc
            out["error"] = err
            if err and "WinError" in err:
                _mwe = _re_su.search(r"WinError (\d+)", err)
                if _mwe:
                    out["winerror"] = int(_mwe.group(1))
        except Exception:
            out["error"] = f"probe_timeout_after_{timeout_sec}s"
        out["latency_ms"] = int((_t_su.time() - t0) * 1000)
        return out

    per_sample = [_probe(p) for p in chosen]
    samples_failed = [s for s in per_sample if not s["reachable"]]
    samples_reachable = [s for s in per_sample if s["reachable"]]

    root_share_status: list = []
    if root_check and samples_failed:
        roots_seen = set()
        for s in samples_failed:
            parts = s["path"].replace("/", "\\").split("\\")
            if len(parts) >= 4:
                root = "\\\\" + parts[2] + "\\" + parts[3]
            else:
                root = s["path"]
            if root in roots_seen:
                continue
            roots_seen.add(root)
            root_share_status.append(_probe(root))

    # Verdict
    if not chosen:
        verdict = "no_unc_samples_available"
        ca_next = "skip_server_audit_no_unc_in_sheet"
    elif len(samples_reachable) == len(chosen):
        verdict = "all_reachable"
        ca_next = "compare_against_log_evidence"
    elif samples_reachable:
        # Partial — check if any root is alive
        live_root = any(r.get("reachable") for r in root_share_status)
        if live_root:
            verdict = "partial_reachable_paths_stale"
            ca_next = "mark_failed_paths_as_stale_root_share_alive"
        else:
            verdict = "partial_reachable_root_unknown"
            ca_next = "investigate_failed_paths_individually"
    else:
        # All failed
        # R85: distinguish UNC auth/session-context failure from server-down.
        # Codes {1326 logon-failure, 1219 multi-conn, 1385 logon-type, 86 bad-pw,
        # 5 access-denied} mean the [A] process logon session lacks the share's
        # credentials the interactive user has — the path may be perfectly
        # reachable from the user's own session. Do NOT report server_down.
        _AUTH_WINERRORS = {1326, 1219, 1385, 86, 5}
        _auth_hit = any(
            (s.get("winerror") in _AUTH_WINERRORS)
            for s in (per_sample + root_share_status)
        )
        if _auth_hit:
            verdict = "unc_auth_context_mismatch"
            ca_next = "verify_from_user_logon_session__A_process_credentials_differ__not_server_down"
        elif root_share_status and any(r.get("reachable") for r in root_share_status):
            verdict = "all_failed_but_root_alive_paths_stale"
            ca_next = "all_sampled_paths_stale_root_alive"
        elif root_share_status:
            verdict = "all_failed_server_down"
            ca_next = "server_down_escalate_to_user"
        else:
            verdict = "all_failed_no_root_probe"
            ca_next = "rerun_with_root_check_true"

    # R54 — scan ALL input paths (not just chosen samples) for upstream
    # mojibake. Even if a path isn't sampled this round, its presence in
    # the input list signals a transport drift that CA should address.
    input_mojibake_samples: list = []
    input_mojibake_count = 0
    for idx, raw_p in enumerate(paths or []):
        sp = str(raw_p) if raw_p is not None else ""
        suspect, markers = _r54_detect_mojibake(sp)
        if suspect:
            input_mojibake_count += 1
            if len(input_mojibake_samples) < 5:
                input_mojibake_samples.append({
                    "path_index": idx,
                    "path_excerpt": sp[:200],
                    "markers": markers,
                })
    input_mojibake_detected = input_mojibake_count > 0
    # Warnings list — R54 introduces, kept compatible with prior responses
    warnings: list = []
    if input_mojibake_detected:
        warnings.append({
            "kind": "INPUT_MOJIBAKE_SUSPECTED",
            "severity": "warn",
            "detail": (
                f"{input_mojibake_count}/{len(paths or [])} input path(s) contain "
                f"suspected mojibake byte sequences "
                f"({_MOJIBAKE_MARKERS!r} subset). These bytes were likely "
                "re-encoded through CP-1252 / OEM 850 upstream (e.g. PowerShell "
                "inline transport). Filesystem probes will likely return "
                "exists=false even when the real share is alive. Reroute the "
                "upstream step via run_node_script / run_ps1_script / "
                "fetch_sheet_csv_summary so paths stay UTF-8 end-to-end."
            ),
            "samples": input_mojibake_samples,
        })

    return json.dumps({
        "ok": True,
        "paths_total": len(paths),
        "unc_paths_valid": len(valid),
        "sample_count_requested": sample_count,
        "samples_tested": len(chosen),
        "samples_reachable": len(samples_reachable),
        "samples_failed": len(samples_failed),
        "per_sample": per_sample,
        "root_check_applied": bool(root_check and samples_failed),
        "root_share_status": root_share_status,
        # R54 — input-side mojibake forensics
        "input_mojibake_detected": input_mojibake_detected,
        "input_mojibake_count": input_mojibake_count,
        "input_mojibake_samples": input_mojibake_samples,
        "warnings": warnings,
        "verdict": verdict,
        "verdict_enum": [
            "all_reachable", "partial_reachable_paths_stale",
            "partial_reachable_root_unknown",
            "all_failed_but_root_alive_paths_stale", "all_failed_server_down",
            "all_failed_no_root_probe", "no_unc_samples_available",
        ],
        "ca_next_action": ca_next,
        # R54 — primitive label reflects cumulative work (R49 base + R53 ascii-safe + R54 mojibake forensics)
        "primitive": "R49/R53/R54 sample_unc_paths_with_root_check — replaces v3 steps 12-14 (R53: per-sample path_ascii_safe + non_ascii_present; R54: input-side mojibake detection + warnings[])",
    }, ensure_ascii=False)


# ─── V15 R50 — Causality verifier (Item 5 from 5-axis report) ────────────────
# Walks evidence ACROSS layers (folder filesystem ↔ Rawcut log) and returns a
# single structured verdict so CA doesn't have to manually correlate
# folder_stats output with FILE_COPIED log lines and decide whether the copy
# truly happened. Specific to Rawcut getfootage workflow but generic in shape:
# {target_folder, baseline, log_path, since_iso} → causality verdict.

_R50_GETFOOTAGE_LOG_RE = re.compile(
    r"(FILE_COPIED|FILE_COPIED_REUSED|FILE_PATH_SKIPPED|GET_FOOTAGE_START|GET_FOOTAGE_COMPLETE|ERR_[A-Z0-9_]+|WARN_[A-Z0-9_]+)"
)
_R50_LOG_TS_RE = re.compile(r"^\[?(\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2})")


async def verify_get_footage_copy_completion(
    target_folder: str,
    baseline_file_count: int = 0,
    baseline_total_bytes: int = 0,
    log_path: str = "",
    since_iso: str = "",
    expected_min_files: int = 1,
    recursive_folder_scan: bool = True,
) -> str:
    """V15 R50 T1 — Rawcut copy completion causality verifier.

    Replaces v1/v2 steps 22-29 (folder_stats after + read log + parse FILE_COPIED +
    cross-check folder vs log + classify outcome). Walks 2 evidence layers
    (filesystem and disk log) and returns a single structured verdict.

    Args:
      target_folder: where Rawcut copied files (mandatory).
      baseline_file_count: file count from pre-copy folder_stats (default 0).
      baseline_total_bytes: total_bytes from pre-copy folder_stats (default 0).
      log_path: absolute path to tas-footage-err.log. Empty → autodetect
                `<target_folder>/../Source/tas-footage-err.log` and similar
                Rawcut conventions are NOT attempted; caller must pass.
      since_iso: only count log entries with ts >= this ISO-8601 timestamp.
                  Empty → count entries in the last 200KB tail (no filter).
      expected_min_files: success threshold for "files actually copied".
      recursive_folder_scan: deep recurse target_folder (default True).

    Returns: {
      ok, verdict, verdict_enum, target_folder, log_path,
      folder_now: {file_count, total_bytes, dir_count, latest_mtime_iso},
      folder_delta: {file_count, total_bytes},
      log_summary: {copied, reused, skipped, get_footage_start, get_footage_complete,
                    errors[], warnings[], first_3_evidence_lines, last_3_evidence_lines},
      cross_check: {folder_delta_files, log_files_total, matched, mismatch_delta},
      evidence_trail: [{layer, source, snippet}],
      ca_next_action
    }
    """
    from datetime import datetime as _dt_v
    if not target_folder:
        return _json_error("target_folder required",
                            error_name="MISSING_REQUIRED_FIELD")
    # Layer 1 — folder filesystem state.
    try:
        fs_raw = await folder_stats(path=target_folder, recursive=recursive_folder_scan)
        fs = json.loads(fs_raw)
    except Exception as exc:
        return _json_error(f"folder_stats failed: {exc}",
                            error_name="FOLDER_STATS_FAILED",
                            target_folder=target_folder)
    if not fs.get("ok"):
        return fs_raw
    folder_now = {
        "file_count": int(fs.get("file_count", 0) or 0),
        "total_bytes": int(fs.get("total_bytes", 0) or 0),
        "dir_count": int(fs.get("dir_count", 0) or 0),
        "latest_mtime_iso": fs.get("latest_mtime_iso"),
    }
    folder_delta = {
        "file_count": folder_now["file_count"] - int(baseline_file_count or 0),
        "total_bytes": folder_now["total_bytes"] - int(baseline_total_bytes or 0),
    }
    # Layer 2 — disk log file (tas-footage-err.log).
    log_summary: dict = {
        "log_path_used": log_path,
        "log_exists": False,
        "log_read_bytes": 0,
        "copied": 0,
        "reused": 0,
        "skipped": 0,
        "get_footage_start": 0,
        "get_footage_complete": 0,
        "errors": [],
        "warnings": [],
        "first_3_evidence_lines": [],
        "last_3_evidence_lines": [],
        "since_iso_applied": since_iso or "",
    }
    if log_path:
        try:
            lp = Path(log_path)
            if lp.exists() and lp.is_file():
                log_summary["log_exists"] = True
                # Tail last 200KB to keep parse cheap on large logs.
                with open(lp, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(0, 2)
                    end = f.tell()
                    start = max(0, end - 200_000)
                    f.seek(start)
                    text = f.read()
                log_summary["log_read_bytes"] = len(text.encode("utf-8", errors="replace"))
                # Parse line-by-line, optionally filter by since_iso.
                since_dt = None
                if since_iso:
                    try:
                        since_dt = _dt_v.fromisoformat(since_iso.replace("Z", "+00:00"))
                    except Exception:
                        since_dt = None
                event_lines: list = []
                for ln in text.splitlines():
                    if not ln.strip():
                        continue
                    # since_iso filter
                    if since_dt is not None:
                        m_ts = _R50_LOG_TS_RE.search(ln)
                        if m_ts:
                            try:
                                ts = _dt_v.fromisoformat(
                                    m_ts.group(1).replace("/", "-").replace(" ", "T")
                                )
                                if ts.replace(tzinfo=None) < since_dt.replace(tzinfo=None):
                                    continue
                            except Exception:
                                pass
                    m = _R50_GETFOOTAGE_LOG_RE.search(ln)
                    if not m:
                        continue
                    code = m.group(1)
                    event_lines.append(ln[:250])
                    if code == "FILE_COPIED":
                        log_summary["copied"] += 1
                    elif code == "FILE_COPIED_REUSED":
                        log_summary["reused"] += 1
                    elif code == "FILE_PATH_SKIPPED":
                        log_summary["skipped"] += 1
                    elif code == "GET_FOOTAGE_START":
                        log_summary["get_footage_start"] += 1
                    elif code == "GET_FOOTAGE_COMPLETE":
                        log_summary["get_footage_complete"] += 1
                    elif code.startswith("ERR_"):
                        if len(log_summary["errors"]) < 10:
                            log_summary["errors"].append({"code": code, "line": ln[:250]})
                    elif code.startswith("WARN_"):
                        if len(log_summary["warnings"]) < 10:
                            log_summary["warnings"].append({"code": code, "line": ln[:250]})
                log_summary["first_3_evidence_lines"] = event_lines[:3]
                log_summary["last_3_evidence_lines"] = event_lines[-3:] if len(event_lines) >= 3 else event_lines
        except Exception as exc:
            log_summary["log_read_error"] = str(exc)
    # Cross-check layer.
    log_files_total = (log_summary["copied"] + log_summary["reused"])
    cross_check = {
        "folder_delta_files": folder_delta["file_count"],
        "log_files_total": log_files_total,
        "log_skipped": log_summary["skipped"],
        "matched": (folder_delta["file_count"] == log_files_total),
        "mismatch_delta": folder_delta["file_count"] - log_files_total,
    }
    # V15 R51 — drilldown enumeration when folder ↔ log mismatch.
    # Log alone is misleading (only counts recent FILE_COPIED), folder alone
    # can include accumulation from prior runs. When they don't agree, walk
    # both sides and surface set difference so CA sees the actual gap rather
    # than guessing from counts.
    mismatch_drilldown: dict | None = None
    if not cross_check["matched"] and log_summary["log_exists"]:
        try:
            # Folder side: enumerate filenames (recursive when scan was recursive)
            folder_files = set()
            try:
                root_p = Path(target_folder)
                if root_p.exists():
                    iterator = root_p.rglob("*") if recursive_folder_scan else root_p.iterdir()
                    for p in iterator:
                        try:
                            if p.is_file():
                                folder_files.add(p.name)
                        except Exception:
                            pass
            except Exception:
                pass
            # Log side: extract filenames from FILE_COPIED / FILE_COPIED_REUSED lines.
            # Rawcut log format example:
            #   2026-05-25T12:34:56 FILE_COPIED stt=5 src=... dst=...\foo.mp4
            # We grep for the last path-like token on each matching line.
            log_files = set()
            for ln in log_summary["first_3_evidence_lines"] + log_summary["last_3_evidence_lines"]:
                pass  # placeholder — we use the deeper scan below for completeness
            try:
                if log_path and Path(log_path).exists():
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(0, 2)
                        end = f.tell()
                        start = max(0, end - 400_000)
                        f.seek(start)
                        text_full = f.read()
                    # R51 — Rawcut log format observed live:
                    #   [INFO] FILE_COPIED {"stt":"13","file":"<basename>",
                    #                        "source":"\\\\srv\\share\\src.ext",
                    #                        "destination":"D:\\target\\new_name.ext"}
                    # Parse the JSON body and prefer `destination` basename
                    # (has extension), fall back to `file` (sometimes ext-less).
                    fc_block_re = re.compile(
                        r"FILE_COPIED(?:_REUSED)?\s*(\{[^\r\n]+\})",
                        re.IGNORECASE,
                    )
                    for m in fc_block_re.finditer(text_full):
                        raw_json = m.group(1)
                        try:
                            obj = json.loads(raw_json)
                        except Exception:
                            continue
                        dest = (obj.get("destination") or "").strip()
                        if dest:
                            base = dest
                            for sep in ("\\", "/"):
                                if sep in base:
                                    base = base.rsplit(sep, 1)[-1]
                            if base:
                                log_files.add(base)
                                continue
                        file_val = (obj.get("file") or "").strip()
                        if file_val:
                            log_files.add(file_val)
            except Exception:
                pass
            # Set differences
            only_in_folder = sorted(folder_files - log_files)[:25]
            only_in_log = sorted(log_files - folder_files)[:25]
            in_both = sorted(folder_files & log_files)[:25]
            mismatch_drilldown = {
                "folder_files_total": len(folder_files),
                "log_referenced_files_total": len(log_files),
                "matched_in_both": len(folder_files & log_files),
                "only_in_folder_count": len(folder_files - log_files),
                "only_in_log_count": len(log_files - folder_files),
                "only_in_folder_sample": only_in_folder,
                "only_in_log_sample": only_in_log,
                "matched_in_both_sample": in_both,
                "explainer": (
                    "files_only_in_folder = accumulated from prior runs OR copied "
                    "outside log scope (since_iso filter cut earlier entries). "
                    "files_only_in_log = log claims copy but file missing from folder "
                    "(bug — investigate Rawcut copy logic). matched_in_both = OK."
                ),
            }
        except Exception as exc:
            mismatch_drilldown = {"drilldown_error": str(exc)}
    # Verdict.
    if not log_summary["log_exists"] and log_path:
        verdict = "log_path_not_found"
        ca_next = "verify_log_path_or_run_workflow_first"
    elif folder_delta["file_count"] == 0 and log_files_total == 0 and log_summary["get_footage_start"] == 0:
        verdict = "copy_did_not_start"
        ca_next = "investigate_picker_or_app_handler"
    elif folder_delta["file_count"] == 0 and log_summary["get_footage_start"] > 0 and log_summary["get_footage_complete"] == 0:
        verdict = "copy_started_but_no_files_yet"
        ca_next = "wait_and_recheck_with_assert_target_idle"
    elif log_summary["get_footage_complete"] > 0 and cross_check["matched"] and folder_delta["file_count"] >= expected_min_files and not log_summary["errors"] and not log_summary["warnings"]:
        verdict = "copy_complete_clean"
        ca_next = "advance_workflow_step_done"
    elif log_summary["get_footage_complete"] > 0 and cross_check["matched"] and (log_summary["warnings"] or log_summary["skipped"]):
        verdict = "copy_complete_with_warnings"
        ca_next = "advance_workflow_step_done_note_warnings"
    elif log_summary["get_footage_complete"] > 0 and not cross_check["matched"]:
        verdict = "mismatch_folder_vs_log"
        ca_next = "list_files_in_folder_vs_log_to_find_gap"
    elif folder_delta["file_count"] > 0 and log_summary["get_footage_complete"] == 0:
        verdict = "copy_in_progress_no_complete_marker"
        ca_next = "wait_and_recheck_or_assert_target_idle"
    elif log_summary["errors"]:
        verdict = "copy_failed_with_errors"
        ca_next = "inspect_errors_in_log_summary"
    else:
        verdict = "indeterminate"
        ca_next = "manually_inspect_evidence_trail"
    evidence_trail = [
        {"layer": "filesystem", "source": "folder_stats(target_folder, recursive=True)",
         "snippet": f"file_count={folder_now['file_count']}, total_bytes={folder_now['total_bytes']}, delta={folder_delta['file_count']} files"},
        {"layer": "disk_log", "source": log_path or "(no log_path passed)",
         "snippet": f"copied={log_summary['copied']}, reused={log_summary['reused']}, skipped={log_summary['skipped']}, complete_markers={log_summary['get_footage_complete']}"},
        {"layer": "cross_check", "source": "delta_files vs (copied + reused)",
         "snippet": f"matched={cross_check['matched']}, mismatch_delta={cross_check['mismatch_delta']}"},
    ]
    return json.dumps({
        "ok": True,
        "verdict": verdict,
        "verdict_enum": [
            "copy_complete_clean", "copy_complete_with_warnings",
            "copy_in_progress_no_complete_marker",
            "copy_started_but_no_files_yet", "copy_did_not_start",
            "mismatch_folder_vs_log", "copy_failed_with_errors",
            "log_path_not_found", "indeterminate",
        ],
        "target_folder": target_folder,
        "log_path": log_path,
        "folder_now": folder_now,
        "folder_baseline": {
            "file_count": int(baseline_file_count or 0),
            "total_bytes": int(baseline_total_bytes or 0),
        },
        "folder_delta": folder_delta,
        "log_summary": log_summary,
        "cross_check": cross_check,
        "mismatch_drilldown": mismatch_drilldown,  # R51 — only populated when cross_check.matched=false
        "evidence_trail": evidence_trail,
        "ca_next_action": ca_next,
        "primitive": "R50/R51 verify_get_footage_copy_completion — replaces v1/v2 steps 22-29 (folder_stats after + log parse + cross-check + verdict + drilldown)",
    }, ensure_ascii=False)


# ─── V15 R50 — Narrow transport wrappers (Item 2 from 5-axis report) ─────────
# Thin aliases over run_project_test_script that hide the `runtime` parameter
# decision. CA passes script_content + project_path only — intent is explicit
# in the tool name itself (run_ps1_script / run_node_script). Reduces drift
# vs picking generic run_project_test_script with a runtime arg.


async def run_ps1_script(project_path: str, script_content: str,
                            timeout: int = 120, script_label: str = "",
                            return_full_stdout: bool = False,
                            stdout_max_chars: int = 4000,
                            stderr_max_chars: int = 2000,
                            content_encoding: str = "plain",
                            dry_run: bool = False,
                            confirm_destructive: int = -1,
                            skip_backup: bool = False) -> str:
    """V15 R50 T2 — narrow wrapper: write .ps1 artifact + run via
    `powershell -NoProfile -ExecutionPolicy Bypass -File <temp>`.

    UTF-8 BOM + OutputEncoding prelude pre-applied (R46), so Vietnamese
    diacritics and other non-ASCII content survive end-to-end. Use this
    instead of inline `node -e` / `powershell -Command` (both blocked by
    R46/R49 guards).

    CASE-2008: a body containing Remove-Item (or any alias: rm/del/ri/rd/rmdir/
    erase), Clear-Content, Move-Item, Copy-Item -Force or a .NET delete refuses
    to run until ``confirm_destructive`` matches the scanner count, takes a
    project backup first, and returns untruncated output. ``dry_run=true``
    previews the scan without executing. See ``_scan_destructive_script``.
    """
    return await run_project_test_script(
        project_path=project_path,
        script_content=script_content,
        runtime="powershell",
        timeout=timeout,
        script_label=script_label or "ps1_script",
        return_full_stdout=return_full_stdout,
        stdout_max_chars=stdout_max_chars,
        stderr_max_chars=stderr_max_chars,
        content_encoding=content_encoding,
        dry_run=dry_run,
        confirm_destructive=confirm_destructive,
        skip_backup=skip_backup,
    )


async def run_node_script(project_path: str, script_content: str,
                            timeout: int = 120, script_label: str = "",
                            return_full_stdout: bool = False,
                            stdout_max_chars: int = 4000,
                            stderr_max_chars: int = 2000,
                            content_encoding: str = "plain",
                            dry_run: bool = False,
                            confirm_destructive: int = -1,
                            skip_backup: bool = False) -> str:
    """V15 R50 T2 — narrow wrapper: write .js artifact + run via `node <temp.js>`.

    JS source bytes pass through unchanged — JSON.stringify / regex literals /
    UNC paths all safe. Use this instead of inline `node -e` (blocked by R49
    guard NODE_INLINE_FORBIDDEN).

    CASE-2008: a body calling fs.rm/unlink/rmdir/truncate or rimraf refuses to run
    until ``confirm_destructive`` matches the scanner count, takes a project backup
    first, and returns untruncated output. ``dry_run=true`` previews without
    executing. See ``_scan_destructive_script``.
    """
    return await run_project_test_script(
        project_path=project_path,
        script_content=script_content,
        runtime="node",
        timeout=timeout,
        script_label=script_label or "node_script",
        return_full_stdout=return_full_stdout,
        stdout_max_chars=stdout_max_chars,
        stderr_max_chars=stderr_max_chars,
        content_encoding=content_encoding,
        dry_run=dry_run,
        confirm_destructive=confirm_destructive,
        skip_backup=skip_backup,
    )


# ─── V15 R51 — Workflow mid-flow drift checkpoint ────────────────────────────
# Item 5 from the 5-axis report (Observability / completion gate): instead of
# only checking drift at the end, expose a primitive CA can call mid-flow
# to ASK whether the path so far is clean. Surfaces:
#   - round accounting / marker health
#   - Session health (active session present, matrix_drift)
#   - Recent forbidden-transport policy hits (drift evidence)
#   - Workflow state (paused/complete/current step) when workflow_name given
# Returns drift_detected + warnings[] + ca_next_action so CA reroutes early.


async def workflow_checkpoint(
    project_path: str = "",
    label: str = "",
    expected_state: str = "",
    evidence: str = "",
    workflow_name: str = "",
) -> str:
    """V15 R51 T3 — Mid-flow drift checkpoint.

    Call between mutating steps to ASK [A]: is the current execution path
    still on rails? Returns a structured drift_detected + warnings[] verdict
    consolidating round accounting, session health, recent policy violations,
    and (optional) workflow state into a single snapshot.

    Args:
      project_path: project root (also used to resolve active session)
      label: human-readable checkpoint name (e.g. "after_step_10_validate")
      expected_state: optional free-form note for audit trail
      evidence: optional 200-char evidence note
      workflow_name: optional — when set, include workflow_get_current data

    Returns: {ok, checkpoint_id, label, recorded_at, drift_detected,
              warnings[{kind,severity,detail}], session_health,
              workflow_health (if workflow_name), ca_next_action,
              warning_kinds_enum}
    """
    from datetime import datetime as _dt_ck
    warnings: list = []
    session_health: dict = {}
    workflow_health: dict | None = None
    # V15 R52 — internal probe-failure accounting. If ANY layer probe inside
    # the checkpoint raises, mark the checkpoint itself as drift-detected
    # rather than letting ok=true mask a broken inner branch. Each failure
    # gets a CHECKPOINT_INTERNAL_PROBE_FAILED warning so the tool's own
    # health is visible alongside the layer warnings it's supposed to surface.
    internal_probe_failures: list = []

    # — Session health —
    if project_path:
        try:
            norm = _session_project_key(project_path)
            sid = _get_active_session_id(norm)
            session_health["active_session_id"] = sid
            session_health["session_present"] = bool(sid)
            session_health["project_path_normalized"] = norm
            if not sid:
                warnings.append({
                    "kind": "SESSION_NOT_ACTIVE",
                    "severity": "error",
                    "detail": (
                        f"No active session for project_path={project_path!r}. "
                        "T2/T3/T4-mutating tools will fail with ACTIVE_SESSION_REQUIRED. "
                        "Call open_session before continuing."
                    ),
                })
        except Exception as exc:
            session_health["probe_error"] = str(exc)
            internal_probe_failures.append({"layer": "session_health", "error": str(exc)})
            warnings.append({
                "kind": "CHECKPOINT_INTERNAL_PROBE_FAILED",
                "severity": "error",
                "detail": f"session_health layer probe failed: {exc}",
                "layer": "session_health",
            })

    # — Recent policy / drift evidence from session events —
    # R52 — get_session_events also lives in code_tools.py, direct call.
    forbidden_recent: list = []
    if project_path and session_health.get("active_session_id"):
        try:
            evt_raw = await get_session_events(
                session_id=session_health["active_session_id"],
                limit=30,
            )
            evt = json.loads(evt_raw) if evt_raw else {}
            for e in (evt.get("events") or []):
                err_name = (e.get("error_name") or "") or ""
                # Sometimes error_name is nested under result_payload
                if not err_name:
                    rp = e.get("result_payload") or e.get("result") or {}
                    if isinstance(rp, dict):
                        err_name = rp.get("error_name") or ""
                if err_name in {
                    "POWERSHELL_INLINE_FORBIDDEN",
                    "NODE_INLINE_FORBIDDEN",
                    "TRANSPORT_POLICY_VIOLATION",
                }:
                    forbidden_recent.append({
                        "tool": e.get("tool_name") or e.get("tool"),
                        "error_name": err_name,
                        "at": e.get("created_at") or e.get("ts"),
                    })
                    if len(forbidden_recent) >= 5:
                        break
            if forbidden_recent:
                warnings.append({
                    "kind": "RECENT_TRANSPORT_POLICY_DRIFT",
                    "severity": "warn",
                    "detail": (
                        f"{len(forbidden_recent)} recent call(s) blocked by transport "
                        "policy guard — CA may still be trying inline shells. "
                        "Switch to run_ps1_script / run_node_script / "
                        "run_project_test_script."
                    ),
                    "samples": forbidden_recent,
                })
        except Exception as exc:
            internal_probe_failures.append({"layer": "session_events_scan", "error": str(exc)})
            warnings.append({
                "kind": "CHECKPOINT_INTERNAL_PROBE_FAILED",
                "severity": "warn",
                "detail": f"session_events probe failed: {exc} — recent policy drift not scanned",
                "layer": "session_events_scan",
            })

    # — Workflow state (when given) —
    if workflow_name:
        try:
            wf_raw = await workflow_get_current(name=workflow_name, project_path=project_path)
            wf = json.loads(wf_raw)
            workflow_health = {
                "name": workflow_name,
                "current_step": wf.get("current_step"),
                "total_steps": wf.get("total_steps"),
                "complete": wf.get("complete"),
                "paused": (wf.get("summary") or {}).get("paused"),
                "pause_reason": (wf.get("summary") or {}).get("pause_reason"),
                "outcome": wf.get("outcome"),
            }
            if workflow_health.get("paused"):
                warnings.append({
                    "kind": "WORKFLOW_PAUSED",
                    "severity": "warn",
                    "detail": (
                        f"Workflow {workflow_name} paused: "
                        f"{workflow_health.get('pause_reason') or 'unknown reason'}. "
                        "Resume via workflow_retry_step / workflow_skip_step before continuing."
                    ),
                })
        except Exception as exc:
            workflow_health = {"name": workflow_name, "probe_error": str(exc)}
            internal_probe_failures.append({"layer": "workflow_get_current", "error": str(exc)})
            warnings.append({
                "kind": "CHECKPOINT_INTERNAL_PROBE_FAILED",
                "severity": "warn",
                "detail": f"workflow_get_current probe failed: {exc}",
                "layer": "workflow_get_current",
            })

    # — Drift detection —
    # R52 — internal_probe_failures count as drift too (was previously
    # invisible behind ok=true / drift=false on an internal probe bug).
    drift_detected = (
        any(w.get("severity") in ("warn", "error") for w in warnings)
        or bool(internal_probe_failures)
    )
    has_error = any(w.get("severity") == "error" for w in warnings)
    has_internal_failure = bool(internal_probe_failures)

    if has_error or has_internal_failure:
        ca_next = "address_error_warnings_before_continuing"
    elif drift_detected:
        ca_next = "review_warnings_and_decide_if_continue_or_reroute"
    else:
        ca_next = "checkpoint_clean_proceed_to_next_step"

    checkpoint_id = f"ck-{int(time.time() * 1000)}"
    return json.dumps({
        "ok": True,
        "checkpoint_id": checkpoint_id,
        "label": label or "unlabeled_checkpoint",
        "recorded_at": _dt_ck.now().isoformat(timespec="seconds"),
        "expected_state": expected_state,
        "evidence": evidence[:200] if evidence else "",
        "drift_detected": drift_detected,
        # R52 — explicit honesty bit: any internal layer that errored.
        "checkpoint_self_healthy": not has_internal_failure,
        "internal_probe_failures": internal_probe_failures,
        "warnings": warnings,
        "warning_kinds_enum": [
            "SESSION_NOT_ACTIVE",
            "RECENT_TRANSPORT_POLICY_DRIFT",
            "WORKFLOW_PAUSED",
            "CHECKPOINT_INTERNAL_PROBE_FAILED",  # R52
        ],
        "session_health": session_health,
        "workflow_health": workflow_health,
        "ca_next_action": ca_next,
        "primitive": "R51/R52 workflow_checkpoint — mid-flow drift gate (session + policy + workflow + self-health consolidated)",
    }, ensure_ascii=False)


async def wait_for_idle(signals: list, stable_seconds: int = 20,
                          timeout_seconds: int = 900,
                          check_interval_sec: int = 30,
                          project_path: str = "") -> str:
    """V15 R42 T1: built-in polling loop wrapping assert_target_idle.

    Returns when target is idle for `stable_seconds` consecutive OR `timeout_seconds`
    exceeded. Saves CA from manual 60-iteration loops (~180 API calls → 1 call).
    """
    import time as _t_wfi
    if not isinstance(signals, list) or not signals:
        return _json_error("signals required (non-empty list)",
                           error_name="MISSING_REQUIRED_FIELD")
    t0 = _t_wfi.time()
    deadline = t0 + max(1, int(timeout_seconds or 900))
    interval = max(2, int(check_interval_sec or 30))
    target_stable = max(1, int(stable_seconds or 20))
    consecutive_idle_seconds = 0.0
    checks = []
    last_evidence = None
    timeout_hit = False
    final_outcome = "unknown"
    while _t_wfi.time() < deadline:
        check_t0 = _t_wfi.time()
        try:
            raw = await assert_target_idle(signals=signals, window_seconds=min(3, interval),
                                              project_path=project_path)
            j = json.loads(raw)
        except Exception as exc:
            j = {"ok": False, "outcome": "unknown", "error": str(exc)}
        outcome = j.get("outcome", "unknown")
        check_dur = _t_wfi.time() - check_t0
        checks.append({"ts": _t_wfi.time() - t0, "outcome": outcome,
                         "duration_sec": round(check_dur, 2)})
        last_evidence = j.get("evidence") or j.get("busy_signals")
        if outcome == "idle":
            consecutive_idle_seconds += check_dur + interval  # cumulative
            if consecutive_idle_seconds >= target_stable:
                final_outcome = "idle"
                break
        else:
            consecutive_idle_seconds = 0  # reset on non-idle
        # Sleep for next check (subtract probe duration)
        _sleep_amt = max(0, interval - check_dur)
        if _t_wfi.time() + _sleep_amt >= deadline:
            timeout_hit = True
            final_outcome = outcome
            break
        if _sleep_amt > 0:
            import asyncio as _aio_wfi
            await _aio_wfi.sleep(_sleep_amt)
    else:
        timeout_hit = True
        final_outcome = checks[-1]["outcome"] if checks else "unknown"

    return json.dumps({
        "ok": True,
        "final_outcome": final_outcome,
        "idle_confirmed": final_outcome == "idle",
        "total_checks": len(checks),
        "elapsed_seconds": round(_t_wfi.time() - t0, 1),
        "stable_duration_achieved": round(consecutive_idle_seconds, 1),
        "stable_seconds_target": target_stable,
        "timeout_hit": timeout_hit,
        "timeout_seconds": timeout_seconds,
        "check_interval_sec": interval,
        "checks_summary": checks[-5:],  # last 5 only
        "evidence_last": last_evidence,
        "ca_next_action": (
            "advance_workflow_step" if final_outcome == "idle"
            else "report_timeout_to_user_or_extend_timeout"
        ),
    }, ensure_ascii=False)


async def parse_log_section(page_id: str, section_title: str,
                              max_chars: int = 50000, tail: bool = True,
                              decode_base64: bool = True,
                              parse_nested_json: bool = True) -> str:
    """V15 R42 T1: extract_log_section + base64 decode + nested JSON parse.

    Wraps `extract_log_section` then for each line tries to decode message_b64 /
    context_b64 / raw_issue_b64 and parse double-encoded JSON. Returns structured
    entries so CA doesn't manually decode.
    """
    import base64 as _b64

    import tools_v15 as _v15_pl
    try:
        raw = await _v15_pl.extract_log_section(page_id=page_id, section_title=section_title,
                                                   max_chars=max_chars, tail=tail)
    except AttributeError:
        # Fallback: extract_log_section might be exposed via another module
        return _json_error("extract_log_section helper not available",
                           error_name="DEPENDENCY_MISSING",
                           ca_next_action="check_tools_v15_extract_log_section_exists")
    except Exception as exc:
        return _json_error(f"extract_log_section failed: {exc}",
                           error_name="EXTRACT_FAILED")
    try:
        log_obj = json.loads(raw)
    except Exception:
        log_obj = {"raw": str(raw)[:5000]}

    log_text = log_obj.get("content", "") or log_obj.get("text", "") or log_obj.get("raw", "")
    if isinstance(log_text, dict):
        log_text = json.dumps(log_text, ensure_ascii=False)

    entries = []
    counts = {"total_entries": 0, "errors": 0, "warnings": 0, "info": 0, "decoded": 0}
    top_categories = {}

    # Try to parse log_text as line-delimited JSON
    for ln in split_lines(log_text or ""):
        ln = ln.strip()
        if not ln:
            continue
        try:
            entry = json.loads(ln)
            if not isinstance(entry, dict):
                continue
        except Exception:
            # not JSON, store as raw line
            entries.append({"raw_line": ln[:500]})
            counts["total_entries"] += 1
            continue
        # Decode base64 fields if requested
        out_entry = {}
        for k, v in entry.items():
            if isinstance(v, str) and k.endswith("_b64") and decode_base64:
                try:
                    decoded = _b64.b64decode(v).decode("utf-8", errors="replace")
                    base_key = k[:-4]
                    out_entry[base_key + "_decoded"] = decoded
                    counts["decoded"] += 1
                    # Try nested JSON parse
                    if parse_nested_json:
                        try:
                            nested = json.loads(decoded)
                            out_entry[base_key + "_parsed"] = nested
                        except Exception:
                            pass
                except Exception:
                    out_entry[k] = v
            else:
                out_entry[k] = v
        level = (out_entry.get("level") or out_entry.get("severity") or "").lower()
        if "error" in level:
            counts["errors"] += 1
        elif "warn" in level:
            counts["warnings"] += 1
        elif "info" in level:
            counts["info"] += 1
        cat = out_entry.get("category") or out_entry.get("type") or out_entry.get("event")
        if cat:
            top_categories[cat] = top_categories.get(cat, 0) + 1
        entries.append(out_entry)
        counts["total_entries"] += 1

    return json.dumps({
        "ok": True,
        "section_title": section_title,
        "entry_count": counts["total_entries"],
        "summary": counts,
        "top_categories": dict(sorted(top_categories.items(), key=lambda x: -x[1])[:5]),
        "entries": entries[-50:] if tail else entries[:50],  # cap to 50 to keep payload reasonable
        "ca_next_action": "inspect_entries_or_filter_by_top_categories",
    }, ensure_ascii=False)


async def workflow_goto_step(name: str, target_step: int, reason: str = "",
                                project_path: str = "") -> str:
    """V15 R42 T3: jump to arbitrary step (forward + backward, no cycle limit).

    User-chosen pattern: low-level CA tested less likely to loop if MD is tight.
    Logs jump_history for audit. Workflow state must already be started.
    """
    if not isinstance(target_step, int) or target_step < 1:
        return _json_error("target_step must be int >= 1",
                           error_name="INVALID_TARGET_STEP",
                           field="target_step",
                           expected_type="integer",
                           received_type=type(target_step).__name__)
    fp, source = _wf_find_md(project_path, name)
    if not fp:
        return _json_error(f"workflow MD not found: {name}",
                           error_name="WORKFLOW_NOT_FOUND")
    slug = fp.stem
    state = _wf_load_state(project_path, slug)
    if not state:
        return _json_error("workflow not started — call workflow_start first",
                           error_name="WORKFLOW_NOT_STARTED")
    with open(fp, "r", encoding="utf-8") as f:
        parsed = _wf_parse_md(f.read())
    steps_list = parsed.get("steps") or []
    valid_numbers = [s.get("number") for s in steps_list if s.get("number")]
    if target_step not in valid_numbers:
        return _json_error(
            f"target_step {target_step} not in workflow (valid: {valid_numbers})",
            error_name="STEP_NOT_FOUND",
            valid_step_numbers=valid_numbers,
        )
    from_step = state.get("current_step", 0)
    direction = "forward" if target_step > from_step else ("backward" if target_step < from_step else "noop")
    state.setdefault("jump_history", []).append({
        "from": from_step,
        "to": target_step,
        "direction": direction,
        "ts": _wf_now_iso(),
        "reason": reason,
    })
    state["current_step"] = target_step
    state.setdefault("steps", {})[str(target_step)] = {
        "status": "active",
        "started_at": _wf_now_iso(),
        "via_jump": True,
    }
    # Clear paused flag if jumping resets the failed-step pause
    state.pop("paused", None)
    state.pop("pause_reason", None)
    _wf_save_state(project_path, slug, state)
    target_body = next((s for s in steps_list if s.get("number") == target_step), None)
    return json.dumps({
        "ok": True,
        "name": slug,
        "jumped": True,
        "from_step": from_step,
        "target_step": target_step,
        "direction": direction,
        "reason": reason,
        "current_step_body": target_body,
        "jump_history_size": len(state["jump_history"]),
        "ca_next_action": "execute_target_step_then_workflow_submit_step",
    }, ensure_ascii=False)


# ─── V15 R35: MD workflow stateful dispenser + universal completion-signal gate ─────

# E402 (module-level import not at top of file) -- OUT OF GATE BY DESIGN: ruff.toml selects only
# E9/F63/F7, and its baseline block records the 17 E402 sites in this file. This is a 15.6k-line
# aggregator where each section carries its own imports under its own banner. MEASURED 2026-09-20:
# this is NOT a circular-import guard. Do not hoist it to the top and do not run `ruff --fix` on
# this file -- see the F401/eval() hazard in the tool-map import note near L192.
import re as _re_wf

_WORKFLOW_VALID_STATUS = {"active", "done", "fail", "skipped", "pending"}

def _wf_slugify(name: str) -> str:
    """R35 / R55: convert workflow display name to filesystem-safe slug.
    R55 — pre-strip common markdown file extensions before slugify so that
    CA habit `workflow_get_current(name='foo.md')` doesn't slug to `foomd`
    and falsely report WORKFLOW_NOT_FOUND.
    """
    s = (name or "").strip().lower()
    # R55 — strip file extension if CA passed a filename instead of a slug
    for ext in (".md", ".markdown", ".mdx"):
        if s.endswith(ext):
            s = s[: -len(ext)]
            break
    s = _re_wf.sub(r"\s+", "-", s)
    s = _re_wf.sub(r"[^a-z0-9_\-]", "", s)
    s = _re_wf.sub(r"-+", "-", s).strip("-_")
    return s or "unnamed"

def _wf_dirs(project_path: str = "") -> list[tuple[Path, str]]:
    """R37 Y1: workflow files are GLOBAL only — tránh phân mảnh, mọi project share workflow.
    project_path arg kept for backward-compat (silently ignored)."""
    dirs = []
    try:
        global_dir = Path(__file__).parent / ".ToolA_Skill" / "workflows"
        dirs.append((global_dir, "global"))
    except Exception:
        pass
    return dirs

def _wf_project_state_dir(project_path: str = "") -> Path:
    """Return workflow run-state directory scoped to project_path.

    Workflow definitions stay global, but mutable run state must not be shared
    across projects. Empty project_path preserves the previous global behavior
    for UI calls that do not carry a project context.
    """
    base = Path(__file__).parent / ".ToolA_Skill" / "workflow_state"
    if project_path:
        try:
            resolved = str(Path(project_path).resolve()).lower()
        except Exception:
            resolved = str(project_path).strip().lower()
        if resolved:
            project_key = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
            base = base / "by_project" / project_key
    base.mkdir(parents=True, exist_ok=True)
    return base

def _wf_state_path(project_path: str, slug: str) -> Path:
    """Workflow run state is isolated per project_path; MD definitions remain global."""
    base = _wf_project_state_dir(project_path)
    return base / f"{slug}.state.json"

def _wf_find_md(project_path: str, name: str) -> tuple[Path | None, str]:
    """R35 / R55: locate workflow MD by name (slug or raw).

    R55 — normalize BOTH directions (dashes ↔ underscores) when comparing
    against on-disk filenames. CA habit variants
    `rawcut-getfootage-sheet-v1` vs `rawcut_getfootage_sheet_v1` now resolve
    to the same file instead of falsely returning WORKFLOW_NOT_FOUND.
    Returns (path, source) or (None, '').
    """
    slug = _wf_slugify(name)
    # R55 — generate dash and underscore variants of slug for filename lookup
    slug_underscored = slug.replace("-", "_")
    slug_dashed = slug.replace("_", "-")
    slug_norm = slug.replace("-", "_")  # canonical compare form
    for d, source in _wf_dirs(project_path):
        if not d.exists():
            continue
        candidates = [d / f"{slug}.md"]
        if slug_underscored != slug:
            candidates.append(d / f"{slug_underscored}.md")
        if slug_dashed != slug:
            candidates.append(d / f"{slug_dashed}.md")
        try:
            for fp in d.glob("*.md"):
                stem_lc = fp.stem.lower()
                stem_norm = stem_lc.replace("-", "_")
                if stem_lc == slug.lower() or stem_norm == slug_norm:
                    candidates.append(fp)
        except Exception:
            pass
        for c in candidates:
            if c.exists():
                return c, source
    return None, ""

_WF_STEP_RE = _re_wf.compile(r"^##\s+Step\s+(\d+)\s*$", _re_wf.IGNORECASE | _re_wf.MULTILINE)
_WF_TITLE_RE = _re_wf.compile(r"^###\s+(.+?)\s*$", _re_wf.MULTILINE)
_WF_COMPLETION_SIGNAL_RE = _re_wf.compile(r"\[completion_signal:\s*(.+?)\]", _re_wf.IGNORECASE)
_WF_REQUIRE_ARTIFACT_RE = _re_wf.compile(r"\[require_artifact:\s*(.+?)\]", _re_wf.IGNORECASE)  # R77 hard anti-fabrication gate

def _wf_parse_md(md_text: str) -> dict:
    """R35: parse MD into {workflow_title, steps:[{number, body, title_blocks:[{title, context}], completion_signals:[...]}]}.

    Convention: `# <title>` = workflow title, `## Step N` = step boundary,
    `### <subtitle>` inside step = title/context block, `[completion_signal: ...]` = idle gate hint.
    R43 P2 #2: also parses `## Known domain rules` (or `## Known rules` / `## Domain rules`)
    section. Each bullet line becomes a string in known_rules[]. CA reads via
    workflow_status to skip inferring rules from code.

    [A] does NOT transform user content — only splits.
    """
    # R43 P2 #2: extract Known domain rules section BEFORE step parsing
    known_rules: list = []
    try:
        _kr_match = _re_wf.search(
            r"^##\s+Known\s+(?:domain\s+)?rules\s*$(.*?)(?=^##\s+|\Z)",
            md_text,
            flags=_re_wf.MULTILINE | _re_wf.DOTALL | _re_wf.IGNORECASE,
        )
        if _kr_match:
            _section = _kr_match.group(1)
            for _ln in _section.split("\n"):
                _ln_stripped = _ln.strip()
                if not _ln_stripped:
                    continue
                _bullet = _re_wf.match(r"^[-*+]\s+(.+?)\s*$", _ln_stripped)
                if _bullet:
                    known_rules.append(_bullet.group(1).strip())
    except Exception:
        pass
    lines = md_text.split("\n")
    wf_title = ""
    # find first `# ` line
    for ln in lines:
        m = _re_wf.match(r"^#\s+(.+?)\s*$", ln)
        if m:
            wf_title = m.group(1).strip()
            break
    # split by step
    step_matches = list(_WF_STEP_RE.finditer(md_text))
    steps = []
    for i, m in enumerate(step_matches):
        step_num = int(m.group(1))
        body_start = m.end()
        body_end = step_matches[i + 1].start() if (i + 1) < len(step_matches) else len(md_text)
        body = md_text[body_start:body_end].strip()
        # parse title/context blocks within step
        title_blocks = []
        sub_matches = list(_WF_TITLE_RE.finditer(body))
        for j, sm in enumerate(sub_matches):
            t = sm.group(1).strip()
            ctx_start = sm.end()
            ctx_end = sub_matches[j + 1].start() if (j + 1) < len(sub_matches) else len(body)
            ctx = body[ctx_start:ctx_end].strip()
            # strip completion_signal + require_artifact tags from context (kept separately)
            ctx_clean = _WF_COMPLETION_SIGNAL_RE.sub("", ctx)
            ctx_clean = _WF_REQUIRE_ARTIFACT_RE.sub("", ctx_clean).strip()
            title_blocks.append({"title": t, "context": ctx_clean})
        # extract completion_signals from full step body
        completion_signals = []
        for cs in _WF_COMPLETION_SIGNAL_RE.finditer(body):
            completion_signals.append(cs.group(1).strip())
        # R77: extract require_artifact specs (hard anti-fabrication gate)
        require_artifacts = []
        for ra in _WF_REQUIRE_ARTIFACT_RE.finditer(body):
            require_artifacts.append(ra.group(1).strip())
        steps.append({
            "number": step_num,
            "body": body,
            "title_blocks": title_blocks,
            "completion_signals": completion_signals,
            "require_artifacts": _wf_parse_artifact_tags(require_artifacts),
        })
    return {"workflow_title": wf_title, "steps": steps, "known_rules": known_rules}

def _wf_load_state(project_path: str, slug: str) -> dict:
    sp = _wf_state_path(project_path, slug)
    if sp.exists():
        try:
            with open(sp, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _wf_resolve_current_step(state: dict | None, parsed_steps: list | None) -> tuple[int, dict | None]:
    """E-01 (2026-08-10): canonical current-step resolver shared by
    workflow_get_current / workflow_status / _wf_step_response.

    Prior drift: `_wf_step_response` defaulted missing current_step to 1 while
    `workflow_status` defaulted to 0, and `workflow_get_current` errored on
    unstarted workflows while `workflow_status` returned started:False. Two
    endpoints therefore disagreed on the "current step" for the same
    workflow state.

    Contract:
      - Unstarted workflow (state falsy): current_step == 0 and step_obj is
        None. Both endpoints report this identically.
      - Started workflow: current_step == int(state['current_step']) — no
        silent fallback to 1. If the state file is missing current_step, we
        treat that as unstarted (0), not step 1, so callers do not
        mis-advance.
      - step_obj is the matching entry from parsed_steps by 'number', else
        None.
    """
    if not state:
        return 0, None
    try:
        cur = int(state.get("current_step", 0))
    except Exception:
        cur = 0
    steps = parsed_steps or []
    step_obj = next((s for s in steps if s.get("number") == cur), None)
    return cur, step_obj

def _wf_save_state(project_path: str, slug: str, state: dict) -> None:
    sp = _wf_state_path(project_path, slug)
    try:
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _wf_now_iso() -> str:
    from datetime import datetime as _dt
    return _dt.now().isoformat(timespec="seconds")

async def workflow_list(project_path: str = "") -> str:
    """V15 R35 T1: list `.md` workflows from project + global sources.

    Returns name + title + step_count + status + source + path.
    """
    found: dict = {}
    for d, source in _wf_dirs(project_path):
        if not d.exists():
            continue
        try:
            for fp in d.glob("*.md"):
                slug = fp.stem
                if slug in found and source == "global":
                    continue
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        md_text = f.read()
                except Exception:
                    md_text = ""
                parsed = _wf_parse_md(md_text)
                state = _wf_load_state(project_path, slug) if project_path else {}
                status = "idle"
                if state:
                    if state.get("complete"):
                        status = "complete"
                    elif state.get("current_step", 0) > 0:
                        status = "active"
                found[slug] = {
                    "name": slug,
                    "title": parsed.get("workflow_title") or slug,
                    "step_count": len(parsed.get("steps") or []),
                    "status": status,
                    "current_step": (state or {}).get("current_step", 0),
                    "source": source,
                    "path": str(fp),
                }
        except Exception:
            pass
    return json.dumps({
        "ok": True,
        "workflows": list(found.values()),
        "count": len(found),
        "sources_scanned": [str(d) for d, _ in _wf_dirs(project_path)],
        "ca_next_action": "call_workflow_start_or_workflow_get_current_with_name",
    }, ensure_ascii=False)

def _wf_step_response(slug: str, parsed: dict, state: dict, source: str, path: Path,
                       extra: dict | None = None) -> dict:
    """R35: build CA-facing step response from parsed MD + state."""
    # E-01: delegate current-step resolution to the shared canonical resolver
    # so this builder, workflow_get_current, and workflow_status never drift.
    cur, step_obj = _wf_resolve_current_step(state, parsed.get("steps"))
    steps = parsed.get("steps") or []
    total = len(steps)
    out = {
        "ok": True,
        "name": slug,
        "workflow_title": parsed.get("workflow_title"),
        "current_step": cur,
        "total_steps": total,
        "source": source,
        "path": str(path),
        "step": step_obj,
        "complete": bool(state.get("complete")),
        "ca_next_action": (
            "do_step_then_workflow_submit_step_with_status_done"
            if step_obj else (
                "workflow_complete_close_session" if state.get("complete") else
                # E-01 v2: distinguish "no state file" (unstarted) from
                # "state file exists but current_step resolves to no step"
                # (drifted / out-of-range). Unstarted matches workflow_status's
                # hint so both endpoints tell the CA the same next action.
                "workflow_not_started_call_workflow_start" if not state else
                "step_not_found_inspect_workflow_md_or_call_workflow_reset"
            )
        ),
    }
    if extra:
        out.update(extra)
    return out

async def workflow_start(name: str, project_path: str = "") -> str:
    """V15 R35 / R37 Y1 T3: start workflow by name. Initializes state, returns step 1 body.
    R37: project_path now optional (state is global).
    """
    fp, source = _wf_find_md(project_path, name)
    if not fp:
        return _json_error(f"workflow MD not found: {name}",
                           error_name="WORKFLOW_NOT_FOUND",
                           name=name,
                           searched=[str(d) for d, _ in _wf_dirs(project_path)],
                           ca_next_action="call_workflow_list_to_see_available")
    slug = fp.stem
    try:
        with open(fp, "r", encoding="utf-8") as f:
            md_text = f.read()
    except Exception as exc:
        return _json_error(f"workflow MD read failed: {exc}",
                           error_name="WORKFLOW_READ_ERROR")
    parsed = _wf_parse_md(md_text)
    state = {
        "workflow": slug,
        "title": parsed.get("workflow_title"),
        "started_at": _wf_now_iso(),
        "project_path": str(Path(project_path).resolve()) if project_path else "",
        "current_step": parsed["steps"][0]["number"] if parsed.get("steps") else 0,
        "steps": {},
        "complete": False,
        "source": source,
        "md_path": str(fp),
    }
    if parsed.get("steps"):
        state["steps"][str(parsed["steps"][0]["number"])] = {"status": "active",
                                                                 "started_at": _wf_now_iso()}
    _wf_save_state(project_path, slug, state)
    return json.dumps(_wf_step_response(slug, parsed, state, source, fp,
                                          extra={"started": True}), ensure_ascii=False)

def _wf_extract_summary(state: dict) -> dict:
    """V15 R44 — Best-effort summary aggregator for workflow_get_current.

    Scans completed step evidence text for known patterns and surfaces
    outcome/file_count/total_bytes/error_summary at the top level. Used by
    workflows that load prior runs (e.g. getfootage_sheet_v3 reading v1/v2).
    Returns empty/None fields when evidence doesn't contain matches — never
    raises. Caller treats missing fields as "data not available".
    """
    import re as _re_wfes
    steps = (state or {}).get("steps") or {}
    summary = {
        "outcome": None,
        "file_count": None,
        "total_bytes": None,
        "error_summary": None,
        "complete": bool((state or {}).get("complete")),
        "paused": bool((state or {}).get("paused")),
        "pause_reason": (state or {}).get("pause_reason") or "",
    }
    # Concatenate all evidence text in step order for scanning.
    ordered = sorted(
        ((int(k), v) for k, v in steps.items() if str(k).isdigit()),
        key=lambda kv: kv[0],
    )
    all_evidence = " | ".join(
        f"step{n}: {(v.get('evidence') or '')[:1000]}" for n, v in ordered
    )
    # outcome: scan for classify_get_footage_outcome-style verdicts.
    m_outcome = _re_wfes.search(
        r"\b(copy_complete_with_warnings|copy_complete|copy_in_progress_timeout|"
        r"copy_in_progress|idle|busy|no_signals|unknown|process_busy)\b",
        all_evidence, _re_wfes.IGNORECASE,
    )
    if m_outcome:
        summary["outcome"] = m_outcome.group(1).lower()
    # file_count: prefer "after" folder_stats numbers, fall back to any "file_count: N".
    m_fc_after = _re_wfes.search(
        r"after[^|]{0,80}?file[_ ]count[:= ]+(\d+)", all_evidence, _re_wfes.IGNORECASE
    )
    if m_fc_after:
        summary["file_count"] = int(m_fc_after.group(1))
    else:
        m_fc = _re_wfes.search(r"file[_ ]count[:= ]+(\d+)", all_evidence, _re_wfes.IGNORECASE)
        if m_fc:
            summary["file_count"] = int(m_fc.group(1))
    # total_bytes: prefer integer bytes; if only "X.YYGB" found, convert.
    m_tb = _re_wfes.search(r"total[_ ]bytes[:= ]+(\d+)", all_evidence, _re_wfes.IGNORECASE)
    if m_tb:
        summary["total_bytes"] = int(m_tb.group(1))
    else:
        m_gb = _re_wfes.search(
            r"(\d+(?:\.\d+)?)\s*GB\b", all_evidence, _re_wfes.IGNORECASE
        )
        if m_gb:
            summary["total_bytes"] = int(float(m_gb.group(1)) * (1024 ** 3))
    # error_summary: surface fail steps or evidence containing ERR_/WARN_.
    fail_steps = [n for n, v in ordered if (v.get("status") == "fail")]
    err_hits = _re_wfes.findall(
        r"(ERR_[A-Z0-9_]+|WARN_[A-Z0-9_]+)", all_evidence
    )
    if fail_steps or err_hits:
        summary["error_summary"] = {
            "fail_steps": fail_steps,
            "error_codes": sorted(set(err_hits))[:20],
        }
    return summary


async def workflow_get_current(name: str, project_path: str = "") -> str:
    """V15 R35 / R37 Y1 / R44 T1: read current step body (idempotent).

    R44: response also carries a top-level `summary` block with best-effort
    outcome/file_count/total_bytes/error_summary extracted from completed
    step evidence — consumed by workflows (e.g. getfootage_sheet_v3) that
    load prior runs to chain downstream decisions.
    """
    fp, source = _wf_find_md(project_path, name)
    if not fp:
        return _json_error(f"workflow MD not found: {name}",
                           error_name="WORKFLOW_NOT_FOUND")
    slug = fp.stem
    state = _wf_load_state(project_path, slug)
    with open(fp, "r", encoding="utf-8") as f:
        parsed = _wf_parse_md(f.read())
    # E-01 v2 (2026-08-10): unify with workflow_status — do NOT early-exit with
    # WORKFLOW_NOT_STARTED. Both public endpoints must resolve identically for
    # an unstarted workflow: current_step=0, step/current_step_body=None,
    # started=False. The shared resolver _wf_resolve_current_step already
    # encodes this contract; _wf_step_response is safe on an empty state dict.
    body = _wf_step_response(slug, parsed, state or {}, source, fp,
                             extra={"started": bool(state)})
    # R44 — inject top-level summary so v3 markdown can populate
    # outcome/file_count/total_bytes/error_summary without bespoke parsing.
    try:
        summary = _wf_extract_summary(state)
        if isinstance(body, dict):
            body["summary"] = summary
            # Flatten common fields to top-level so callers using either shape
            # (nested under summary OR flat) read consistent data.
            for k in ("outcome", "file_count", "total_bytes", "error_summary"):
                body.setdefault(k, summary.get(k))
    except Exception:
        pass
    return json.dumps(body, ensure_ascii=False)

async def workflow_submit_step(name: str, step: int = 0, status: str = "done",
                                   evidence: str = "", project_path: str = "",
                                   step_range: list | None = None,
                                   artifacts: dict | None = None,
                                   next_step: int = 0) -> str:
    """V15 R35 / R37 Y1 / R42 T3: submit current step. done→advance; fail→pause.

    R42: optional `step_range=[start, end]` to batch advance multiple steps in one
    call (e.g. steps 19..32 done without 14 sequential calls). Reject if range[0]
    doesn't match current_step. Evidence applies to last step only.
    """
    if status not in ("done", "fail"):
        return _json_error(f"invalid status: {status}",
                           error_name="INVALID_STATUS",
                           accepted=["done", "fail"])
    fp, source = _wf_find_md(project_path, name)
    if not fp:
        return _json_error(f"workflow MD not found: {name}",
                           error_name="WORKFLOW_NOT_FOUND")
    slug = fp.stem
    state = _wf_load_state(project_path, slug)
    if not state:
        return _json_error("workflow not started",
                           error_name="WORKFLOW_NOT_STARTED",
                           ca_next_action="call_workflow_start_first")
    if state.get("complete"):
        return _json_error("workflow already complete",
                           error_name="WORKFLOW_ALREADY_COMPLETE",
                           ca_next_action="call_workflow_reset_or_choose_another")
    try:
        cur = int(state.get("current_step", 0))
    except Exception:
        return _json_error("workflow state current_step is not an integer",
                           error_name="INVALID_WORKFLOW_STATE",
                           current_step=state.get("current_step"))
    try:
        step = int(step)
    except Exception:
        return _json_error("step must be an integer",
                           error_name="INVALID_STEP",
                            passed_step=step,
                            ca_next_action="call_workflow_get_current_to_resync")
    try:
        requested_next_step = int(next_step or 0)
    except Exception:
        return _json_error("next_step must be an integer",
                           error_name="INVALID_NEXT_STEP",
                           passed_next_step=next_step,
                           ca_next_action="omit_next_step_or_pass_declared_branch_step")
    # V15 R42: step_range path — batch advance
    if step_range and isinstance(step_range, list) and len(step_range) == 2:
        try:
            r_start = int(step_range[0])
            r_end = int(step_range[1])
        except Exception:
            return _json_error("step_range must be [int, int]",
                               error_name="INVALID_STEP_RANGE")
        if r_start != cur:
            return _json_error(
                f"step_range start mismatch: passed [{r_start}, {r_end}] but current_step={cur}",
                error_name="STEP_MISMATCH",
                current_step_authoritative=cur,
                passed_range=step_range,
                delta=r_start - cur,
                suggestion=f"Use step_range=[{cur}, {r_end}] OR workflow_goto_step(target_step={r_start}) first",
                alternative_tool="workflow_goto_step",
                ca_next_action="call_workflow_get_current_to_resync_or_use_goto_step",
            )
        if r_end < r_start:
            return _json_error("step_range end < start",
                               error_name="INVALID_STEP_RANGE")
        with open(fp, "r", encoding="utf-8") as f:
            parsed = _wf_parse_md(f.read())
        steps_list = parsed.get("steps") or []
        valid_nums = sorted({s.get("number") for s in steps_list if s.get("number")})
        nums_in_range = [n for n in valid_nums if r_start <= n <= r_end]
        # Apply done to each step in range (only on status=done; fail path uses single step)
        if status == "fail":
            return _json_error("step_range only supports status='done' (use single step for fail)",
                               error_name="INVALID_RANGE_STATUS",
                               ca_next_action="use_single_step_for_fail_status")
        # R77: steps with required artifacts CANNOT be batch-advanced (closes the
        # batch-fabrication hole). They must be submitted one-by-one with artifacts={}.
        gated = [n for n in nums_in_range
                 if next((s for s in steps_list if s.get("number") == n), {}).get("require_artifacts")]
        if gated:
            return _json_error(
                f"steps {gated} declare required artifacts and cannot be batch-advanced",
                error_name="ARTIFACT_GATE_REQUIRES_SEQUENTIAL",
                gated_steps=gated,
                ca_next_action="submit_each_gated_step_individually_with_artifacts_param",
                hint=("Submit each gated step alone: workflow_submit_step(step=N, status='done', "
                      "artifacts={'<key>':'<absolute path>'}). The engine verifies the file exists on disk "
                      "before accepting — you cannot batch past it."))
        now_iso = _wf_now_iso()
        for n in nums_in_range:
            state.setdefault("steps", {})[str(n)] = {
                "status": "done",
                "submitted_at": now_iso,
                "evidence": evidence[:2000] if n == nums_in_range[-1] else "(batch advance)",
                "batch_range": [r_start, r_end],
            }
        # Find next step after r_end
        next_step = next((s for s in steps_list if s.get("number") > r_end), None)
        if next_step is None:
            state["complete"] = True
            state["completed_at"] = now_iso
            _wf_save_state(project_path, slug, state)
            return json.dumps({
                "ok": True,
                "workflow_complete": True,
                "name": slug,
                "batch_advanced_steps": nums_in_range,
                "total_steps_executed": len(state.get("steps", {})),
                "ca_next_action": "report_completion_to_user",
            }, ensure_ascii=False)
        state["current_step"] = next_step["number"]
        state["steps"][str(next_step["number"])] = {"status": "active", "started_at": now_iso}
        _wf_save_state(project_path, slug, state)
        return json.dumps(_wf_step_response(slug, parsed, state, source, fp,
                                              extra={"batch_advanced_steps": nums_in_range}),
                           ensure_ascii=False)
    # Single-step path (existing semantics)
    if step != cur:
        return _json_error(f"step mismatch: passed step={step} but current_step={cur}",
                           error_name="STEP_MISMATCH",
                           current_step_authoritative=cur,
                           passed_step=step,
                           delta=step - cur,  # R42: friendlier
                           suggestion=(
                               f"Use workflow_submit_step(step_range=[{cur}, {step}]) to batch advance, "
                               f"OR submit sequentially starting step={cur}, "
                               f"OR call workflow_goto_step(target_step={step}) if you want to skip ahead"
                           ),
                           alternative_tool="workflow_goto_step",
                           ca_next_action="submit_sequential_or_use_step_range_or_goto_step")
    with open(fp, "r", encoding="utf-8") as f:
        parsed = _wf_parse_md(f.read())
    cur_step_obj = next((s for s in (parsed.get("steps") or []) if s.get("number") == cur), None)
    # R77: HARD artifact gate — a step with [require_artifact: ...] is NOT accepted as
    # done unless its declared output really exists on disk. Defeats fabricated reports
    # (CA narrating "done" without doing the work). Only applies on status=done.
    if status == "done" and cur_step_obj and cur_step_obj.get("require_artifacts"):
        gate = _wf_eval_artifact_gate(cur_step_obj["require_artifacts"], artifacts, state)
        if not gate["ok"]:
            return _json_error(
                f"artifact gate failed for step {cur}: required output(s) missing/invalid on disk",
                error_name="ARTIFACT_GATE_FAILED",
                step=cur,
                required=cur_step_obj["require_artifacts"],
                failures=gate["failures"],
                artifacts_received=list((artifacts or {}).keys()),
                ca_next_action="actually_produce_the_artifact_then_resubmit_with_artifacts_param",
                hint=("This step declares required outputs. Do the real work, then resubmit: "
                      f"workflow_submit_step(step={cur}, status='done', artifacts={{'<key>':'<absolute path>'}}). "
                      "The engine re-checks disk; you cannot mark it done by narrating."))
    # record step status
    state.setdefault("steps", {})[str(cur)] = {
        "status": status,
        "submitted_at": _wf_now_iso(),
        "evidence": evidence[:2000],
    }
    # On fail → PAUSE (no advance). User decides retry/skip/edit.
    if status == "fail":
        state["paused"] = True
        state["pause_reason"] = "step_failed"
        _wf_save_state(project_path, slug, state)
        return json.dumps({
            "ok": True,
            "workflow_paused": True,
            "pause_reason": "step_failed",
            "name": slug,
            "failed_step": cur,
            "failed_step_body": cur_step_obj,
            "evidence_from_ca": evidence,
            "hint": (
                "Workflow paused. STOP and report to User with evidence above. "
                "User decides: workflow_retry_step | workflow_skip_step | edit MD then workflow_get_current."
            ),
            "ca_next_action": "stop_and_report_to_user_wait_for_decision",
        }, ensure_ascii=False)
    # On done → advance
    # Soft completion-signal evaluation (if step had [completion_signal: ...] tags)
    signal_warning = None
    if cur_step_obj and cur_step_obj.get("completion_signals"):
        try:
            parsed_signals = _wf_parse_signal_tags(cur_step_obj["completion_signals"])
            idle_raw = await assert_target_idle(signals=parsed_signals, project_path=project_path)
            idle_j = json.loads(idle_raw)
            if not idle_j.get("idle", True):
                signal_warning = {
                    "target_not_idle_warning": True,
                    "busy_signals": idle_j.get("busy_signals", []),
                    "evidence": idle_j.get("evidence", []),
                    "hint": "Target appears not idle. SOFT warning — CA may retry submit after a few seconds or proceed if confident.",
                }
        except Exception:
            pass
    # advance to next step, optionally honoring a declared/manual branch target.
    steps_list = parsed.get("steps") or []
    next_step = None
    if requested_next_step:
        valid_nums = sorted({s.get("number") for s in steps_list if s.get("number")})
        if requested_next_step not in valid_nums:
            return _json_error(
                f"next_step={requested_next_step} is not in workflow",
                error_name="INVALID_NEXT_STEP",
                requested_next_step=requested_next_step,
                valid_steps=valid_nums,
                ca_next_action="omit_next_step_or_pass_declared_branch_step",
            )
        if requested_next_step == cur:
            return _json_error(
                "next_step must differ from current step",
                error_name="INVALID_NEXT_STEP",
                requested_next_step=requested_next_step,
                current_step=cur,
                ca_next_action="omit_next_step_or_pass_declared_branch_step",
            )
        next_step = next((s for s in steps_list if s.get("number") == requested_next_step), None)
    for s in steps_list:
        if next_step is None and s.get("number") > cur:
            next_step = s
            break
    if next_step is None:
        state["complete"] = True
        state["completed_at"] = _wf_now_iso()
        _wf_save_state(project_path, slug, state)
        out = {
            "ok": True,
            "workflow_complete": True,
            "name": slug,
            "total_steps_executed": len(state.get("steps", {})),
            "ca_next_action": "report_completion_to_user_then_optionally_close_session",
        }
        if signal_warning:
            out.update(signal_warning)
        return json.dumps(out, ensure_ascii=False)
    state["current_step"] = next_step["number"]
    state["steps"][str(next_step["number"])] = {"status": "active",
                                                   "started_at": _wf_now_iso()}
    _wf_save_state(project_path, slug, state)
    resp = _wf_step_response(slug, parsed, state, source, fp,
                              extra={"prev_step_status": "done"})
    if signal_warning:
        resp.update(signal_warning)
    return json.dumps(resp, ensure_ascii=False)

async def workflow_retry_step(name: str, project_path: str = "") -> str:
    """V15 R35 / R37 Y1 T3: retry current step after fail/pause."""
    fp, source = _wf_find_md(project_path, name)
    if not fp:
        return _json_error(f"workflow MD not found: {name}",
                           error_name="WORKFLOW_NOT_FOUND")
    slug = fp.stem
    state = _wf_load_state(project_path, slug)
    if not state:
        return _json_error("workflow not started",
                           error_name="WORKFLOW_NOT_STARTED")
    cur = state.get("current_step", 0)
    state["steps"][str(cur)] = {"status": "active",
                                  "restarted_at": _wf_now_iso()}
    state.pop("paused", None)
    state.pop("pause_reason", None)
    _wf_save_state(project_path, slug, state)
    with open(fp, "r", encoding="utf-8") as f:
        parsed = _wf_parse_md(f.read())
    return json.dumps(_wf_step_response(slug, parsed, state, source, fp,
                                          extra={"retried": True}), ensure_ascii=False)

async def workflow_skip_step(name: str, reason: str = "", project_path: str = "") -> str:
    """V15 R35 / R37 Y1 T3: skip current step, advance to next."""
    fp, source = _wf_find_md(project_path, name)
    if not fp:
        return _json_error(f"workflow MD not found: {name}",
                           error_name="WORKFLOW_NOT_FOUND")
    slug = fp.stem
    state = _wf_load_state(project_path, slug)
    if not state:
        return _json_error("workflow not started",
                           error_name="WORKFLOW_NOT_STARTED")
    cur = state.get("current_step", 0)
    state["steps"][str(cur)] = {"status": "skipped",
                                  "skipped_at": _wf_now_iso(),
                                  "reason": reason}
    state.pop("paused", None)
    state.pop("pause_reason", None)
    with open(fp, "r", encoding="utf-8") as f:
        parsed = _wf_parse_md(f.read())
    steps_list = parsed.get("steps") or []
    next_step = None
    for s in steps_list:
        if s.get("number") > cur:
            next_step = s
            break
    if next_step is None:
        state["complete"] = True
        state["completed_at"] = _wf_now_iso()
        _wf_save_state(project_path, slug, state)
        return json.dumps({
            "ok": True,
            "workflow_complete": True,
            "name": slug,
            "ca_next_action": "report_completion_to_user",
        }, ensure_ascii=False)
    state["current_step"] = next_step["number"]
    state["steps"][str(next_step["number"])] = {"status": "active",
                                                   "started_at": _wf_now_iso()}
    _wf_save_state(project_path, slug, state)
    return json.dumps(_wf_step_response(slug, parsed, state, source, fp,
                                          extra={"prev_step_status": "skipped"}),
                       ensure_ascii=False)

async def workflow_reset(name: str, project_path: str = "") -> str:
    """V15 R35 / R37 Y1 T3: reset workflow state, archive old state file."""
    fp, source = _wf_find_md(project_path, name)
    if not fp:
        return _json_error(f"workflow MD not found: {name}",
                           error_name="WORKFLOW_NOT_FOUND")
    slug = fp.stem
    sp = _wf_state_path(project_path, slug)
    archived = ""
    reset_id = _wf_now_iso().replace(":", "").replace("-", "").replace(".", "")
    if sp.exists():
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        bak = sp.with_suffix(f".json.bak.{ts}")
        try:
            sp.rename(bak)
            archived = str(bak)
        except Exception:
            pass
        # R43.3 M12: rotation — keep newest 3 .bak files per slug
        try:
            _bak_glob = sorted(sp.parent.glob(f"{slug}.state.json.bak.*"))
            _too_old = _bak_glob[:-3]  # keep last 3
            for _old in _too_old:
                try:
                    _old.unlink()
                except Exception:
                    pass
        except Exception:
            pass
    # re-start
    started = json.loads(await workflow_start(name, project_path))
    started["reset_id"] = reset_id
    started["archived_state"] = archived
    started["reset_complete"] = True
    started["ca_next_action"] = "workflow_reset_complete_no_submit_previous_terminal_step"
    return json.dumps(started, ensure_ascii=False)

async def workflow_status(name: str, project_path: str = "") -> str:
    """V15 R35 / R37 Y1 / R38 T1: full workflow state INCLUDING current step body.

    R38: expose `current_step_body` (title_blocks + completion_signals) so CA không
    phải đọc raw MD bằng read_file_range — saves 1 tool call per status query.
    """
    fp, source = _wf_find_md(project_path, name)
    if not fp:
        return _json_error(f"workflow MD not found: {name}",
                           error_name="WORKFLOW_NOT_FOUND")
    slug = fp.stem
    state = _wf_load_state(project_path, slug)
    with open(fp, "r", encoding="utf-8") as f:
        parsed = _wf_parse_md(f.read())
    steps_list = parsed.get("steps") or []
    # E-01: use the shared canonical resolver so workflow_status and
    # workflow_get_current always agree on the current step — including
    # the unstarted case (current_step=0, step_obj=None).
    cur_num, current_step_body = _wf_resolve_current_step(state, steps_list)
    # also build a compact step manifest so CA sees all titles at once (no full MD read needed)
    step_manifest = [
        {
            "number": s.get("number"),
            "title_blocks_count": len(s.get("title_blocks") or []),
            "first_title": (s.get("title_blocks") or [{}])[0].get("title", ""),
            "has_completion_signal": bool(s.get("completion_signals")),
            "has_require_artifact": bool(s.get("require_artifacts")),  # R77
            "require_artifact_keys": [a.get("key") for a in (s.get("require_artifacts") or [])],  # R77
        }
        for s in steps_list
    ]
    return json.dumps({
        "ok": True,
        "name": slug,
        "source": source,
        "md_path": str(fp),
        "state_path": str(_wf_state_path(project_path, slug)),
        "workflow_title": parsed.get("workflow_title"),
        "total_steps": len(steps_list),
        "started": bool(state),
        "complete": (state or {}).get("complete", False),
        "current_step": cur_num,
        "current_step_body": current_step_body,  # R38: full step object (title_blocks, context, completion_signals)
        "step_manifest": step_manifest,           # R38: compact overview all steps (titles only)
        "step_statuses": (state or {}).get("steps", {}),
        "paused": (state or {}).get("paused", False),
        "pause_reason": (state or {}).get("pause_reason", ""),
        "known_rules": parsed.get("known_rules") or [],  # R43 P2 #2: domain-rule checklist
        "known_rules_count": len(parsed.get("known_rules") or []),
        "ca_next_action": (
            "use_current_step_body_directly_no_need_to_read_md_file"
            if current_step_body else "workflow_not_started_call_workflow_start"
        ),
    }, ensure_ascii=False)

# R36: in-memory draft store (agent push → UI pull). TTL-managed.
_WORKFLOW_DRAFTS: dict = {}  # token → {body_md, name, scope, created_at, expires_at}

def _wf_purge_expired_drafts() -> None:
    from datetime import datetime as _dt
    now = _dt.now()
    for tok in list(_WORKFLOW_DRAFTS.keys()):
        try:
            if _WORKFLOW_DRAFTS[tok].get("expires_at_dt") and _WORKFLOW_DRAFTS[tok]["expires_at_dt"] < now:
                _WORKFLOW_DRAFTS.pop(tok, None)
        except Exception:
            pass

async def workflow_read_md(name: str, project_path: str = "") -> str:
    """V15 R36 T1: read raw MD body for a workflow. Project-first, global fallback.

    Used by: agent (read-then-modify-then-save), UI editor `?load=<slug>` query.
    """
    fp, source = _wf_find_md(project_path, name)
    if not fp:
        return _json_error(f"workflow MD not found: {name}",
                           error_name="WORKFLOW_NOT_FOUND",
                           ca_next_action="call_workflow_list_to_see_available")
    try:
        with open(fp, "r", encoding="utf-8") as f:
            body = f.read()
    except Exception as exc:
        return _json_error(f"workflow read failed: {exc}",
                           error_name="WORKFLOW_READ_ERROR")
    parsed = _wf_parse_md(body)
    return json.dumps({
        "ok": True,
        "name": fp.stem,
        "source": source,
        "path": str(fp),
        "body_md": body,
        "workflow_title": parsed.get("workflow_title"),
        "step_count": len(parsed.get("steps") or []),
    }, ensure_ascii=False)

async def workflow_draft_create(name: str, body_md: str, ttl_sec: int = 3600) -> str:
    """V15 R36 T3: push agent-authored MD draft into in-memory store, return token.

    UI editor opens `/ui/workflow_editor?draft=<token>` to populate form. User reviews
    and clicks Save → invokes workflow_create_from_ui to persist. Drafts auto-expire.
    """
    _wf_purge_expired_drafts()
    if not name or not body_md:
        return _json_error("name and body_md required",
                           error_name="MISSING_REQUIRED_FIELD")
    import secrets as _sec
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    token = _sec.token_urlsafe(16)
    now = _dt.now()
    ttl = max(60, min(int(ttl_sec or 3600), 86400))  # clamp 60s..24h
    expires = now + _td(seconds=ttl)
    _WORKFLOW_DRAFTS[token] = {
        "name": name,
        "body_md": body_md,
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": expires.isoformat(timespec="seconds"),
        "expires_at_dt": expires,
        "ttl_sec": ttl,
    }
    return json.dumps({
        "ok": True,
        "draft_token": token,
        "name": name,
        "expires_at": expires.isoformat(timespec="seconds"),
        "ttl_sec": ttl,
        "editor_url": f"/ui/workflow_editor?draft={token}",
        "hint": "Open editor_url in browser; User reviews then Save to persist.",
    }, ensure_ascii=False)

async def workflow_draft_get(token: str) -> str:
    """V15 R36 T1: UI editor pulls draft by token. Used by `?draft=<token>` query."""
    _wf_purge_expired_drafts()
    d = _WORKFLOW_DRAFTS.get(token)
    if not d:
        return _json_error("draft not found or expired",
                           error_name="DRAFT_NOT_FOUND",
                           token=token,
                           ca_next_action="call_workflow_draft_create_again")
    return json.dumps({
        "ok": True,
        "draft_token": token,
        "name": d.get("name"),
        "body_md": d.get("body_md"),
        "created_at": d.get("created_at"),
        "expires_at": d.get("expires_at"),
    }, ensure_ascii=False)

def _wf_active_path(project_path: str = "") -> Path:
    """Active workflow marker follows the same project-scoped state directory."""
    base = _wf_project_state_dir(project_path)
    return base / "_active.json"

async def workflow_activate(name: str, project_path: str = "") -> str:
    """V15 R36 / R37 Y1 T3: activate workflow (GLOBAL — applies regardless of project).
    Auto-starts state if not started. project_path optional (kept for compat).

    R37: removed project_path requirement so User can activate via UI without
    setting [Ab:P] first — workflows are universal pipelines.
    """
    fp, source = _wf_find_md(project_path, name)
    if not fp:
        return _json_error(f"workflow MD not found: {name}",
                           error_name="WORKFLOW_NOT_FOUND")
    slug = fp.stem
    # auto-start if no state yet
    state = _wf_load_state(project_path, slug)
    if not state:
        start_raw = await workflow_start(name=slug, project_path=project_path)
        _ = json.loads(start_raw)  # init state
        state = _wf_load_state(project_path, slug)
    active = {
        "name": slug,
        "activated_at": _wf_now_iso(),
        "source": source,
        "md_path": str(fp),
    }
    try:
        with open(_wf_active_path(project_path), "w", encoding="utf-8") as f:
            json.dump(active, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        return _json_error(f"write active state failed: {exc}",
                           error_name="WRITE_FAILED")
    return json.dumps({
        "ok": True,
        "activated": True,
        "name": slug,
        "current_step": state.get("current_step", 1),
        "total_steps": len(_wf_parse_md(open(fp, encoding='utf-8').read()).get("steps") or []),
        "hint": "[A] will now inject workflow hints into responses. CA should follow steps via workflow_get_current + workflow_submit_step.",
    }, ensure_ascii=False)

async def workflow_deactivate(project_path: str = "") -> str:
    """V15 R36 / R37 Y1 T3: stop workflow mode. project_path optional (state is global)."""
    ap = _wf_active_path(project_path)
    was_active = None
    if ap.exists():
        try:
            with open(ap, "r", encoding="utf-8") as f:
                was_active = json.load(f)
        except Exception:
            pass
        try:
            ap.unlink()
        except Exception:
            pass
    return json.dumps({
        "ok": True,
        "deactivated": True,
        "was_active": was_active,
        "hint": "Workflow mode off. [A] no longer injects workflow hints.",
    }, ensure_ascii=False)

async def workflow_active_get(project_path: str = "") -> str:
    """V15 R36 / R37 Y1 T1: read active workflow state (GLOBAL). project_path ignored."""
    ap = _wf_active_path(project_path)
    if not ap.exists():
        return json.dumps({"ok": True, "active": False}, ensure_ascii=False)
    try:
        with open(ap, "r", encoding="utf-8") as f:
            active = json.load(f)
    except Exception:
        return json.dumps({"ok": True, "active": False, "reason": "state read failed"},
                           ensure_ascii=False)
    name = active.get("name", "")
    state = _wf_load_state(project_path, name) if name else {}
    fp, source = _wf_find_md(project_path, name)
    cur_step = None
    total_steps = 0
    if fp and fp.exists():
        try:
            parsed = _wf_parse_md(fp.read_text(encoding="utf-8"))
            steps_list = parsed.get("steps") or []
            total_steps = len(steps_list)
            cur_num = state.get("current_step", 0)
            cur_step = next((s for s in steps_list if s.get("number") == cur_num), None)
        except Exception:
            pass
    return json.dumps({
        "ok": True,
        "active": True,
        "name": name,
        "activated_at": active.get("activated_at"),
        "current_step": state.get("current_step", 0),
        "total_steps": total_steps,
        "step": cur_step,
        "complete": state.get("complete", False),
        "paused": state.get("paused", False),
        "step_statuses": state.get("steps", {}),
    }, ensure_ascii=False)


async def workflow_create_from_ui(name: str, body_md: str, scope: str = "global",
                                     project_path: str = "", overwrite: bool = False) -> str:
    """V15 R35 / R37 Y1 T3: save MD body received from UI popup. [A] does NOT transform.
    Alias `workflow_save_md` registered for agent-side use (same function).

    R37: scope param kept for backward-compat but forced to 'global' — workflows
    are global-only to avoid file fragmentation across projects. project_path ignored.
    """
    if not name:
        return _json_error("name required", error_name="MISSING_REQUIRED_FIELD")
    if not body_md:
        return _json_error("body_md required", error_name="MISSING_REQUIRED_FIELD")
    slug = _wf_slugify(name)
    # R37: always write to global directory
    target_dir = Path(__file__).parent / ".ToolA_Skill" / "workflows"
    _scope_used = "global"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return _json_error(f"mkdir failed: {exc}",
                           error_name="MKDIR_FAILED",
                           path=str(target_dir))
    target = target_dir / f"{slug}.md"
    if target.exists() and not overwrite:
        return _json_error(f"workflow exists: {target}",
                           error_name="WORKFLOW_ALREADY_EXISTS",
                           ca_next_action="pass_overwrite_true_to_replace",
                           path=str(target))
    try:
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(body_md)
    except Exception as exc:
        return _json_error(f"write failed: {exc}",
                           error_name="WRITE_FAILED",
                           path=str(target))
    # parse + return summary so UI can confirm
    parsed = _wf_parse_md(body_md)
    return json.dumps({
        "ok": True,
        "saved": True,
        "name": slug,
        "path": str(target),
        "scope": _scope_used,
        "workflow_title": parsed.get("workflow_title"),
        "step_count": len(parsed.get("steps") or []),
        "bytes_written": len(body_md.encode("utf-8")),
    }, ensure_ascii=False)

# ─── R35: Universal completion-signal gate ──────────────────────────────────

def _wf_parse_signal_tags(raw_tags: list) -> list:
    """R35: parse `[completion_signal: kind=folder_growth_stable folder=... stable_seconds=5]` strings.

    Tag format inside `[]`: `kind=<x> key1=val1 key2=val2 ...` (space-separated kv).
    Returns list of dicts compatible with assert_target_idle.signals.
    """
    out = []
    for raw in raw_tags or []:
        parts = raw.strip().split()
        spec = {}
        for p in parts:
            if "=" in p:
                k, v = p.split("=", 1)
                # cast numerics
                if v.isdigit():
                    v = int(v)
                elif v.replace(".", "").isdigit():
                    try:
                        v = float(v)
                    except Exception:
                        pass
                spec[k] = v
            else:
                # bare kind shortcut (first token without `=` treated as kind)
                if "kind" not in spec:
                    spec["kind"] = p
        if spec.get("kind"):
            out.append(spec)
    return out

def _wf_parse_artifact_tags(raw_tags: list) -> list:
    """R77: parse `[require_artifact: key=cep_trim kind=json_items_gt0 min_size=1 fresh=true]` strings.

    kv format (space-separated). kinds: file (default) | folder_nonempty | json_items_gt0 | png.
    `key` = a name the CA maps to a resolved absolute path at submit time (artifacts={key: path}).
    `fresh=true` = artifact mtime must be >= workflow start (defeats stale prior-run reuse).
    Returns list of spec dicts.
    """
    out = []
    for raw in raw_tags or []:
        spec = {}
        for p in raw.strip().split():
            if "=" in p:
                k, v = p.split("=", 1)
                if v.lower() in ("true", "false"):
                    v = (v.lower() == "true")
                elif v.isdigit():
                    v = int(v)
                spec[k] = v
        if spec.get("key"):
            spec.setdefault("kind", "file")
            out.append(spec)
    return out

def _wf_workflow_start_ts(state: dict):
    """R77: earliest recorded step timestamp = freshness floor for artifact checks."""
    cands = []
    for st in (state.get("steps") or {}).values():
        for k in ("started_at", "submitted_at"):
            v = st.get(k)
            if v:
                try:
                    cands.append(datetime.fromisoformat(v))
                except Exception:
                    pass
    return min(cands) if cands else None

def _wf_check_artifact(spec: dict, path: str, start_ts) -> dict:
    """R77: verify ONE required artifact on disk. Returns {ok, key, path, reason?}."""
    key = spec.get("key")
    kind = spec.get("kind", "file")
    if not path:
        return {"ok": False, "key": key, "reason": "no path provided by CA for this required artifact key (pass artifacts={'%s': '<abs path>'})" % key}
    p = Path(path)
    if not p.exists():
        return {"ok": False, "key": key, "path": path, "reason": "path does not exist on disk"}
    min_size = int(spec.get("min_size", 1) or 0)
    if kind == "folder_nonempty":
        if not p.is_dir():
            return {"ok": False, "key": key, "path": path, "reason": "expected a non-empty folder, got a file"}
        try:
            has_file = any(c.is_file() for c in p.iterdir())
        except Exception:
            has_file = False
        if not has_file:
            return {"ok": False, "key": key, "path": path, "reason": "folder is empty (no files)"}
    elif kind == "json_items_gt0":
        if not p.is_file():
            return {"ok": False, "key": key, "path": path, "reason": "expected a JSON file"}
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return {"ok": False, "key": key, "path": path, "reason": "not valid JSON: %s" % e}
        items = data.get("items") if isinstance(data, dict) else (data if isinstance(data, list) else None)
        if not isinstance(items, list) or len(items) == 0:
            return {"ok": False, "key": key, "path": path, "reason": "JSON has no non-empty items[] array"}
    else:  # file / png (default)
        if not p.is_file():
            return {"ok": False, "key": key, "path": path, "reason": "expected a file"}
        try:
            sz = p.stat().st_size
        except Exception:
            sz = 0
        if sz < min_size:
            return {"ok": False, "key": key, "path": path, "reason": "file size %d < min_size %d" % (sz, min_size)}
        if kind == "png" and p.suffix.lower() != ".png":
            return {"ok": False, "key": key, "path": path, "reason": "expected a .png file"}
    # freshness: artifact must be produced by THIS run (mtime >= workflow start)
    if spec.get("fresh") and start_ts is not None:
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            if mtime < (start_ts - timedelta(seconds=10)):
                return {"ok": False, "key": key, "path": path,
                        "reason": "STALE: mtime %s predates workflow start %s — not produced by this run" % (
                            mtime.isoformat(timespec="seconds"), start_ts.isoformat(timespec="seconds"))}
        except Exception:
            pass
    return {"ok": True, "key": key, "path": path}

def _wf_eval_artifact_gate(specs: list, artifacts: dict, state: dict) -> dict:
    """R77: enforce all require_artifact specs for a step. Returns {ok, failures:[...]}."""
    artifacts = artifacts or {}
    start_ts = _wf_workflow_start_ts(state)
    failures = []
    for spec in specs or []:
        res = _wf_check_artifact(spec, str(artifacts.get(spec.get("key"), "")).strip(), start_ts)
        if not res.get("ok"):
            failures.append(res)
    return {"ok": len(failures) == 0, "failures": failures}

async def assert_target_idle(signals: list = None, window_seconds: int = 3,
                                 project_path: str = "",
                                 # V15 R44 — backward-compat aliases for legacy
                                 # workflow markdown payloads that called
                                 # assert_target_idle(path=..., stable_seconds=...,
                                 # timeout_seconds=...). New schema uses signals[].
                                 path: str = "", folder: str = "",
                                 stable_seconds: int = 0,
                                 timeout_seconds: int = 0) -> str:
    """V15 R35 T1: universal completion-signal gate (read-only soft warning).

    Evaluates each signal independently. Returns idle=true if ALL signals report idle.
    Supported signal kinds:
      - folder_growth_stable: {folder, stable_seconds, max_wait?}
      - process_cpu_idle:     {process_name|pid, idle_threshold_pct=10, stable_seconds=3}
      - process_io_idle:      {process_name|pid, stable_seconds=3}
      - cdp_global_flag:      {page_id, global_var, expected_value=true}
      - log_pattern_match:    {log_path, regex, since_ts?}
      - ui_state_stable:      {page_id, stable_seconds=3}  (best-effort dom_state_diff)
      - window_title_match:   {hwnd|title_pattern, regex}

    V15 R44 backward-compat: legacy callers can pass
    `path`/`folder` + `stable_seconds` + `timeout_seconds`; this auto-synthesizes
    a single folder_growth_stable signal so old workflow markdown keeps working.
    """
    import time as _t
    signals = signals or []
    # V15 R44 — synthesize folder_growth_stable from legacy aliases when no
    # signals[] was supplied. timeout_seconds is recorded as evidence only;
    # this tool is a one-shot gate (no internal polling loop).
    _legacy_folder = folder or path
    if _legacy_folder and not signals:
        _legacy_sig = {"kind": "folder_growth_stable", "folder": _legacy_folder}
        if stable_seconds:
            _legacy_sig["stable_seconds"] = int(stable_seconds)
        signals = [_legacy_sig]
        if stable_seconds and (window_seconds is None or window_seconds == 3):
            window_seconds = int(stable_seconds)
    evaluated = []
    busy = []
    evidence = []
    overall_idle = True
    for sig in signals:
        if not isinstance(sig, dict):
            evaluated.append({"signal": sig, "ok": False, "reason": "not_a_dict"})
            overall_idle = False
            continue
        kind = sig.get("kind") or ""
        result = {"signal": sig, "kind": kind, "idle": None, "detail": ""}
        try:
            if kind == "folder_growth_stable":
                folder = sig.get("folder", "")
                stable_s = int(sig.get("stable_seconds", window_seconds))
                if not folder:
                    result.update({"idle": None, "detail": "folder param missing"})
                else:
                    def _snap():
                        import os as _os
                        total = 0
                        count = 0
                        try:
                            for root, dirs, files in _os.walk(folder):
                                count += len(files)
                                for fn in files:
                                    try:
                                        total += _os.path.getsize(_os.path.join(root, fn))
                                    except Exception:
                                        pass
                        except Exception:
                            return None
                        return (count, total)
                    s1 = _snap()
                    _t.sleep(stable_s)
                    s2 = _snap()
                    if s1 is None or s2 is None:
                        result.update({"idle": None, "detail": f"folder not accessible: {folder}"})
                    else:
                        idle = (s1 == s2)
                        result.update({"idle": idle, "detail": f"before={s1} after={s2} window={stable_s}s"})
            elif kind in ("process_cpu_idle", "process_io_idle"):
                try:
                    import psutil as _ps_idle
                except Exception:
                    result.update({"idle": None, "detail": "psutil missing"})
                else:
                    target_pids = []
                    if sig.get("pid"):
                        target_pids.append(int(sig["pid"]))
                    elif sig.get("process_name"):
                        pname = sig["process_name"].lower()
                        for p in _ps_idle.process_iter(["pid", "name"]):
                            try:
                                if (p.info["name"] or "").lower() == pname:
                                    target_pids.append(p.info["pid"])
                            except Exception:
                                pass
                    if not target_pids:
                        result.update({"idle": True, "detail": "no matching process — treated as idle"})
                    elif kind == "process_cpu_idle":
                        thresh = float(sig.get("idle_threshold_pct", 10))
                        stable_s = int(sig.get("stable_seconds", window_seconds))
                        samples = []
                        for _ in range(2):
                            inst = 0.0
                            for pid in target_pids:
                                try:
                                    p = _ps_idle.Process(pid)
                                    inst += p.cpu_percent(interval=stable_s / 2)
                                except Exception:
                                    pass
                            samples.append(inst)
                        max_obs = max(samples) if samples else 0.0
                        idle = max_obs < thresh
                        result.update({"idle": idle, "detail": f"cpu_pct samples={samples} max={max_obs:.1f} thresh={thresh}"})
                    else:  # process_io_idle
                        stable_s = int(sig.get("stable_seconds", window_seconds))
                        def _io_total():
                            total = 0
                            for pid in target_pids:
                                try:
                                    p = _ps_idle.Process(pid)
                                    io = p.io_counters()
                                    total += (io.read_bytes + io.write_bytes)
                                except Exception:
                                    pass
                            return total
                        i1 = _io_total()
                        _t.sleep(stable_s)
                        i2 = _io_total()
                        idle = (i1 == i2)
                        result.update({"idle": idle, "detail": f"io_bytes before={i1} after={i2} delta={i2-i1}"})
            elif kind == "log_pattern_match":
                log_path = sig.get("log_path", "")
                regex = sig.get("regex", "")
                if not log_path or not regex:
                    result.update({"idle": None, "detail": "log_path or regex missing"})
                else:
                    try:
                        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(0, 2)
                            end = f.tell()
                            f.seek(max(0, end - 50000))
                            text = f.read()
                        m = _re_wf.search(regex, text, _re_wf.IGNORECASE)
                        idle = bool(m)
                        result.update({"idle": idle, "detail": f"pattern_found={idle}"})
                    except Exception as exc:
                        result.update({"idle": None, "detail": f"log read failed: {exc}"})
            elif kind in ("cdp_global_flag", "ui_state_stable", "window_title_match"):
                # best-effort stubs: rely on tools_v15 helpers; mark as not_evaluated if missing
                result.update({"idle": None, "detail": f"{kind} requires runtime helper — eval later or use alternative signal"})
            else:
                result.update({"idle": None, "detail": f"unknown signal kind: {kind}"})
        except Exception as exc:
            result.update({"idle": None, "detail": f"signal eval exception: {exc}"})
        evaluated.append(result)
        evidence.append({"kind": kind, "detail": result.get("detail", "")})
        if result.get("idle") is False:
            busy.append(result)
            overall_idle = False
        elif result.get("idle") is None and signals:
            # treat None as indeterminate (don't force busy, but don't claim idle either)
            pass
    # V15 R38: explicit outcome classifier so CA không nhầm `ok=true` thành "idle confirmed".
    # `ok` chỉ nghĩa "tool ran successfully"; `idle` / `outcome` mới là verdict thực tế.
    any_indeterminate = any(r.get("idle") is None for r in evaluated) if evaluated else False
    if not signals:
        outcome = "no_signals"
    elif overall_idle and not any_indeterminate:
        outcome = "idle"
    elif busy:
        # heuristic naming when first busy signal is folder/copy related
        first_kind = (busy[0].get("kind") or "") if busy else ""
        if first_kind == "folder_growth_stable":
            outcome = "copy_in_progress"
        elif first_kind in ("process_cpu_idle", "process_io_idle"):
            outcome = "process_busy"
        else:
            outcome = "busy"
    elif any_indeterminate:
        outcome = "unknown"
    else:
        outcome = "idle"
    ok_vs_idle_warning = None
    if outcome != "idle":
        ok_vs_idle_warning = (
            "⚠️ DO NOT use `ok=true` as completion proof. `ok` only means the gate "
            "tool ran successfully. The real verdict is `outcome` or `idle`. "
            f"Current outcome=`{outcome}`. Wait + retry, do NOT submit step done."
        )
    return json.dumps({
        "ok": True,
        "idle": overall_idle,
        "outcome": outcome,  # R38: idle | copy_in_progress | process_busy | busy | unknown | no_signals
        "outcome_enum": ["idle", "copy_in_progress", "process_busy", "busy", "unknown", "no_signals"],
        "ok_vs_idle_warning": ok_vs_idle_warning,
        "signals_evaluated": evaluated,
        "busy_signals": busy,
        "evidence": evidence,
        "signal_count": len(signals),
        "hint": (
            "Soft gate — CA decides whether to proceed. CHECK `outcome` field (NOT `ok`). "
            "outcome='idle' → safe to advance. outcome='copy_in_progress'/'process_busy'/'busy' → wait + retry. "
            "outcome='unknown' → at least one signal indeterminate (e.g. missing psutil); read evidence."
        ),
        "ca_next_action": (
            "advance_to_next_step" if outcome == "idle"
            else ("wait_then_recheck_assert_target_idle" if outcome in ("copy_in_progress", "process_busy", "busy")
                  else "inspect_evidence_or_use_alternative_signal")
        ),
    }, ensure_ascii=False)

# Post-action classifier: get-footage outcome
_GET_FOOTAGE_PATTERNS = {
    "folder_picker_fail": [
        r"folder name is not valid", r"invalid path",
        r"đường dẫn không hợp lệ", r"tên thư mục không hợp lệ",
    ],
    "source_unc_fail": [
        r"UNC .* (cannot find|access denied|not accessible)",
        r"\\\\\\\\[^\\\\]+\\\\[^\\\\]+ .* (cannot find|access denied)",
    ],
    "copy_runtime_fail": [
        r"ENOENT", r"EPERM", r"EACCES", r"copy failed", r"copy error",
    ],
}

async def classify_get_footage_outcome(project_path: str = "", since_ts: str = "",
                                          output_folder: str = "",
                                          log_excerpt: str = "") -> str:
    """V15 R34 T1: post-get-footage classifier — reads log + folder_stats, classifies outcome.

    Args:
      project_path: project root for log lookup
      since_ts: ISO timestamp to slice log from
      output_folder: where files should have been copied to (for delta check)
      log_excerpt: optional direct log text (skip log read if provided)

    Returns:
      outcome ∈ {success, folder_picker_fail, source_unc_fail, copy_runtime_fail, unknown}
      evidence: {log_excerpt (head), files_created (count), errors_found[]}
    """
    import re as _re_cl
    log_text = log_excerpt or ""
    if not log_text and project_path:
        try:
            # best-effort: read orchestrator.log tail (proxy for app log if app doesn't have one)
            log_path = Path(project_path) / "orchestrator.log"
            if log_path.exists():
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(0, 2)
                    end = f.tell()
                    f.seek(max(0, end - 50000))
                    log_text = f.read()
        except Exception:
            pass
    files_created = 0
    folder_stats_evidence = {}
    if output_folder:
        try:
            fs_raw = await folder_stats(path=output_folder, recursive=True)
            fs = json.loads(fs_raw)
            files_created = fs.get("file_count", 0) or 0
            folder_stats_evidence = {"file_count": files_created,
                                       "total_bytes": fs.get("total_bytes", 0)}
        except Exception:
            pass
    errors_found: list = []
    outcome = "unknown"
    for category, patterns in _GET_FOOTAGE_PATTERNS.items():
        for pat in patterns:
            try:
                m = _re_cl.search(pat, log_text, _re_cl.IGNORECASE)
                if m:
                    errors_found.append({"category": category, "pattern": pat,
                                           "excerpt": log_text[max(0, m.start()-40):m.end()+40]})
                    if outcome == "unknown":
                        outcome = category
            except Exception:
                pass
    # success heuristic: files created AND no fatal errors in log
    if outcome == "unknown" and files_created > 0:
        if not _re_cl.search(r"ERROR|EXCEPTION|TRACEBACK", log_text, _re_cl.IGNORECASE):
            outcome = "success"
    ca_next = {
        "success": "report_success_to_user",
        "folder_picker_fail": "verify_folder_path_normalization_and_existence_then_retry",
        "source_unc_fail": "verify_source_unc_access_or_use_local_copy_fallback",
        "copy_runtime_fail": "inspect_files_permissions_or_disk_space",
        "unknown": "read_full_logs_with_read_all_logs_for_deeper_investigation",
    }.get(outcome, "inspect_logs")
    return json.dumps({
        "ok": True,
        "outcome": outcome,
        "evidence": {
            "log_excerpt": log_text[:600] if log_text else "",
            "files_created": files_created,
            "folder_stats": folder_stats_evidence,
            "errors_found": errors_found[:5],
        },
        "ca_next_action": ca_next,
    }, ensure_ascii=False)


# ─── Tool dispatch map ──────────────────────────────────────────────────────

# ─── Tool 33: validate_file ───────────────────────────────────────────────────

async def validate_encoding(file_path: str, project_path: str = "",
                            expected_encoding: str = "utf-8",
                            language_hint: str = "") -> str:
    try:
        fp = _resolve_project_file(project_path, file_path)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc), "ca_next_action": "fix_project_path_or_file_path"})
    if not fp.exists():
        return json.dumps({"ok": False, "error": f"File not found: {file_path}", "ca_next_action": "check_file_path"})
    if _is_binary_file(fp):
        return _json_error(
            f"Binary file unsupported by validate_encoding: {file_path}",
            error_name="BINARY_FILE_UNSUPPORTED",
            type="UNSUPPORTED_MEDIA_TYPE",
            severity="CRITICAL",
            retryable=False,
            ca_next_action="stop_and_report",
        )
    check = _encoding_check_from_file(fp, expected_encoding or "utf-8")
    ok = bool(check.get("utf8_valid")) and not bool(check.get("mojibake_risk"))
    return json.dumps({
        "ok": ok,
        "file": str(fp),
        "expected_encoding": expected_encoding or "utf-8",
        "language_hint": language_hint,
        **check,
        "ca_next_action": "proceed_to_validate_file" if ok else "inspect_suspicious_sequences_before_apply",
    }, ensure_ascii=False)

class _HTMLTagStackParser(HTMLParser):
    """Track HTML element nesting so cut-off documents cannot pass validation."""

    VOID_ELEMENTS = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.tag_errors: list[dict] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag not in self.VOID_ELEMENTS:
            self.stack.append((tag, self.getpos()[0]))

    def handle_startendtag(self, tag: str, attrs) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        line = self.getpos()[0]
        if tag in self.VOID_ELEMENTS:
            return
        if not self.stack:
            self.tag_errors.append({"kind": "unexpected_closing", "tag": tag, "line": line, "expected": None})
            return
        expected = self.stack[-1][0]
        if expected == tag:
            self.stack.pop()
            return
        self.tag_errors.append({"kind": "mismatched_closing", "tag": tag, "line": line, "expected": expected})
        for index in range(len(self.stack) - 2, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return

    @property
    def unclosed_tags(self) -> list[dict]:
        return [{"tag": tag, "opened_line": line} for tag, line in self.stack]


def _console_python() -> str:
    """Resolve python.exe (CONSOLE subsystem) from sys.executable.

    campaign-1608 (A19/B8 layer 2): [A] runs as a service via pythonw.exe, a GUI
    subsystem binary with NO console and NO stdout handle. A console child such
    as ruff cannot pipe output back to a GUI parent: the run returns rc=1
    (findings DO exist) but stdout AND stderr both come back EMPTY. A caller
    that only inspects the text then concludes "no findings" and reports the
    file clean. Resolving python.exe from the same Scripts dir restores capture.

    Mirrors orchestrator/graph/extractors/vulture_ruff.py:_console_python, which
    already solved this locally; keep the two in sync until one is promoted to a
    shared helper.
    """
    console = Path(sys.executable).with_name("python.exe")
    return str(console) if console.is_file() else sys.executable
# campaign-1608 (A51 / ANSI): shared CSI-escape stripper. Single call site for
# every validate_file language branch AND _py_error_ruff_hint, so the strip path
# is exercised by one code path, not copy-pasted per branch (a copy-pasted regex
# let a mutant survive earlier in this campaign; one helper makes every caller
# die together under the same test).
#
# The pattern deliberately matches the FULL CSI grammar, not just the SGR colour
# subset that live node/esbuild output happens to emit today (surveyed with
# FORCE_COLOR=1: only \x1b[90m / \x1b[39m / \x1b[4m; no OSC \x1b]...\x07). Two
# reasons, both measured rather than assumed:
#   1. It is identical to _COUNCIL_ANSI_RE (orchestrator_server.py), which has
#      been stripping council logs for far longer. Two strippers in one repo
#      disagreeing on what an escape IS is exactly the two-sources-of-truth shape
#      this campaign keeps finding -- so they agree by construction.
#   2. Widening costs nothing here: the private-mode/erase forms it adds
#      (\x1b[?25l, \x1b[?1049h, \x1b[2K) carry no text, and the broad pattern was
#      verified NOT to touch any diagnostic content -- filenames, line:col,
#      esbuild's UTF-8 box-drawing carets (U+2502 / U+2575), ruff rule codes.
# The narrow SGR-only form leaks \x1b[?25l and \x1b[?1049h straight into `error`.
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _strip_ansi(text) -> str:
    """Remove ANSI CSI escapes (colour, cursor, erase); leave all text intact."""
    return _ANSI_CSI_RE.sub("", str(text or ""))


# ONE tuple, three uses (campaign-1608 A29-P4; split in A32-1.2, and again in
# #7): the codes validate_file LINTS for and the codes it advertises as
# ruff-fixable. They were two literals ~150 lines apart, and the day the fixable
# half drifts from what ruff can really apply, `_py_error_ruff_hint` starts
# promising an edit ruff refuses to make -- worse than not reporting the rule at
# all. orchestrator/graph/tools.py's _RUFF_FIX_SELECT is the third copy (the one
# ruff_fix hands to --fix) and it tracks the DEFECT tuple, never the lint
# select; the guard test asserts that relationship instead of importing across
# the boundary at runtime, which would add a module-level dependency the daemon
# does not need at boot.
#
# #7 (measured 2026-09-25): the fixable rules are two kinds. A DEFECT rule finds
# the same thing in every project. A POLICY rule's finding is the project's own
# choice: I001 depends on the import order the project picked, and RUF100
# ("unused noqa") is relative to the rule set that is enabled -- code_tools.py
# has 19 findings under `--select I,RUF100` and 0 under ruff_fix's own set.
# Adding them on [A]'s side reported 216 I001 + 169 RUF100 across this repo,
# whose own config reports 0, and a ruff_fix preview of graph/tools.py (0 defect
# findings) proposed 15 hunks of import and noqa rewrites. So [A] never selects
# a policy rule: the lint pass sees one only when the project's config enables
# it (--extend-select keeps the project's rules), and ruff_fix asks the project
# which ones it enables.
_RUFF_DEFECT_FIX_RULES = ("F401", "F841", "E711", "E712", "E731", "E401", "E402",
                          "F541")
_RUFF_POLICY_FIX_RULES = ("RUF100", "I")
# What the hint may name. A policy finding only reaches it from a project that
# enables the rule, and ruff_fix then selects that rule as well.
_RUFF_SAFE_FIX_RULES = (*_RUFF_DEFECT_FIX_RULES, *_RUFF_POLICY_FIX_RULES)
# A32-1.2: lint-only, deliberately NOT in the tuple above. Measured on ruff
# 0.16.7 (positive control: a probe with `def f(x=[])` + a bare `except:` fired
# B006 at 1:9 and E722 at 4:5; negative control: `--select ZZ999` is rejected as
# an unknown selector, so a zero count means clean rather than ignored):
# B006's fix applicability is `unsafe` and E722 has NO fix, and `--diff` over
# the whole repo wrote 0 bytes. Advertising them as safe-fixable would make
# `_py_error_ruff_hint` promise a fix that cannot happen. They are still worth
# SEEING -- a mutable default argument and a bare except are exactly the two
# defects in OCR's checklist that a linter can catch mechanically -- so they are
# selected for the lint pass and reported, and `ruff_preview` answers
# `violations_not_auto_fixable`, which is why that state is no longer
# unreachable (see the docstring of RuffRuleSetsMustNotDrift).
# F821 (undefined name) joined 2026-09-24 (#7): report-only -- a missing name
# has no mechanical repair, and a write that introduces one is exactly what the
# write-time lint exists to catch.
_RUFF_REPORT_ONLY_RULES = ("B006", "E722", "F821")
_RUFF_LINT_SELECT = ",".join((*_RUFF_DEFECT_FIX_RULES, *_RUFF_REPORT_ONLY_RULES))
_RUFF_FIX_CA_HINT = "ruff_fix available to auto-fix lint issues"
# One concise-format finding: "path:line:col: CODE [*] message". The optional
# "[*] " is ruff's own "has a safe fix" mark.
_RUFF_FINDING_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+): (?P<code>[A-Z]+[0-9]+) (?P<fix>\[\*\] )?(?P<msg>[^\r\n]*)$",
    re.MULTILINE)
_RUFF_SUMMARY_RE = re.compile(r"^(?:Found \d+ error|\[\*\] \d+ fixable|No fixes available|\d+ fixable)")


def _ruff_lint_argv(fp, stdin: bool = False) -> list[str]:
    """The one ruff argv for the write-time lint pass and its baseline.

    --extend-select, not --select (#7, measured 2026-09-24): --select REPLACES
    the project's own rule set, so a project whose ruff.toml selects "F" had
    ``x = pytest.mark`` (F821) answer validation.ok with no finding. Extending
    keeps the project's rules and adds [A]'s advisory set on top. The fixable
    override makes ``[*]`` mean "ruff has a safe fix", not "this project allows
    fixing it" -- the same override ruff_fix itself runs with.
    """
    target = ["--stdin-filename", str(fp), "-"] if stdin else [str(fp)]
    return [_console_python(), "-m", "ruff", "check", *target,
            "--extend-select", _RUFF_LINT_SELECT,
            "--config", 'lint.fixable = ["ALL"]',
            "--output-format", "concise", "--color", "never"]


def _py_error_ruff_hint(error_msg: str) -> tuple[bool, list[str], str]:
    """Map Ruff diagnostics to safe-fix preview guidance without applying fixes.

    A finding counts only when ruff itself marks it ``[*]`` (it has a safe fix)
    AND its rule is one ruff_fix's safe pass selects. Measured 2026-09-24: E711
    and F841 print WITHOUT ``[*]`` -- their fixes are unsafe -- yet a match on
    the rule name alone promised ruff_fix, and the ruff_fix that followed
    changed nothing.
    """
    # campaign-1608 (A51): strip ANSI SGR escapes before matching (layer 2 /
    # defence in depth). An escape inside a line breaks the finding pattern and
    # suppresses every match. Layer 1 passes "--color never" so the ruff we
    # spawn never colours; this guards any future/foreign call site that forgets.
    text = _strip_ansi(error_msg)
    found = set()
    for m in _RUFF_FINDING_RE.finditer(text):
        if not m.group("fix"):
            continue
        code = m.group("code")
        if re.fullmatch(r"I\d{3}", code):
            found.add("I")
        elif code in _RUFF_SAFE_FIX_RULES:
            found.add(code)
    matched = [rule for rule in _RUFF_SAFE_FIX_RULES if rule in found]
    if not matched:
        return False, [], ""
    rules = ", ".join(matched)
    return True, matched, f"ruff_fix's safe pass repairs these findings (rules: {rules})."


def _lint_only_introduced(val: dict, fp, baseline) -> dict:
    """Reduce a post-write lint result to the findings THIS write introduced.

    Measured 2026-09-24: a pre-existing I001 at orchestrator_server.py:15914 was
    reported -- with a ruff_fix hint -- on every write to that file, whatever
    the write touched. A write answers for its own change; the file's full lint
    state stays one validate_file call away. The baseline is the pre-write text,
    linted through stdin with the same argv, and findings are matched as a
    multiset on (code, message) so a line shift does not re-report them.
    Anything this cannot measure returns ``val`` unchanged: an unfiltered
    report is noisy, a wrongly filtered one would hide a real finding.
    """
    after = str(val.get("lint_warning") or "") if isinstance(val, dict) else ""
    if not after or not isinstance(baseline, str):
        return val
    try:
        # BYTES, not text: text-mode stdin turns a CRLF file's "\r\n" into
        # "\r\r\n" on Windows, so ruff lints a different file and the baseline
        # misses findings the real file has (measured: its I001 vanished, and the
        # write re-reported it as introduced).
        r = subprocess.run(_ruff_lint_argv(fp, stdin=True),
                           input=baseline.removeprefix("\ufeff").encode("utf-8"),
                           capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return val
    if r.returncode not in (0, 1):
        return val
    before = {}
    for m in _RUFF_FINDING_RE.finditer(_strip_ansi((r.stdout or b"").decode("utf-8", "replace"))):
        key = (m.group("code"), m.group("msg"))
        before[key] = before.get(key, 0) + 1
    if not before:
        return val
    kept, introduced, preexisting = [], 0, 0
    for line in _strip_ansi(after).splitlines():
        m = _RUFF_FINDING_RE.fullmatch(line)
        if m is None:
            if line.strip() and not _RUFF_SUMMARY_RE.match(line):
                kept.append(line)
            continue
        key = (m.group("code"), m.group("msg"))
        if before.get(key):
            before[key] -= 1
            preexisting += 1
        else:
            kept.append(line)
            introduced += 1
    if not preexisting:
        return val
    out = {k: v for k, v in val.items() if k != "lint_warning"}
    if out.get("ca_hint") == _RUFF_FIX_CA_HINT:
        out.pop("ca_hint")
        if out.get("ca_next_action") == "ruff_fix":
            out.pop("ca_next_action")
    if kept:
        kept.append(f"Found {introduced} error(s) introduced by this write ({preexisting} pre-existing not shown).")
        out["lint_warning"] = "\n".join(kept)
    applicable, matched, hint = _py_error_ruff_hint(out.get("lint_warning", ""))
    out.update(ruff_fix_applicable=applicable, ruff_rules_matched=matched, ruff_fix_hint=hint)
    if applicable:
        out["ca_hint"] = _RUFF_FIX_CA_HINT
        out.setdefault("ca_next_action", "ruff_fix")
    return out


# Every lang `validate_file` actually implements a checker for. The registered
# schema used to document four of these ("python | js | ts | auto") while the
# code branched on nine, so a caller had no way to learn the real vocabulary
# except by reading the source -- and an unlisted-but-real value looked identical
# to a typo. This set is the single source of truth for both the refusal message
# and the schema text.
_VALIDATE_LANGS = frozenset({
    "auto", "python", "js", "ts", "esbuild", "html", "vue", "json", "css", "toml",
})

# Spellings that mean one of the above. These are the values a caller naturally
# sends; answering ok:true to them (the old behaviour) validated nothing.
_VALIDATE_LANG_ALIASES = {
    "py": "python", "python3": "python",
    "javascript": "js", "node": "js", "nodejs": "js", "mjs": "js", "cjs": "js",
    "typescript": "ts",
    "jsx": "esbuild", "tsx": "esbuild",
    "htm": "html",
    "jsonc": "json", "json5": "json",
    "scss": "css", "sass": "css",
    "": "auto", "detect": "auto",
}


async def validate_file(file_path: str, lang: str = "auto", project_path: str = "") -> str:
    """Check syntax/validity sau write. Python: py_compile. JS/TS: node --check.
    HTML: UTF-8 + extract <script> blocks → node --check.
    project_path: optional, được dùng để resolve relative file_path."""
    if project_path and not Path(file_path).is_absolute():
        file_path = str(Path(project_path) / file_path)
    try:
        fp = _resolve_project_file(project_path, file_path)
    except Exception as exc:
        return _json_error(
            str(exc),
            error_name="VALIDATION_FAILED",
            type="VALIDATION_ERROR",
            severity="error",
            retryable=False,
            reason="syntax validation failed",
            ca_next_action="fix_syntax_before_retry",
            write_performed=False,
        )
    if not fp.exists():
        return _json_error(
            f"File not found: {file_path}",
            error_name="VALIDATION_FAILED",
            type="VALIDATION_ERROR",
            severity="error",
            retryable=False,
            reason="syntax validation failed",
            ca_next_action="fix_syntax_before_retry",
            write_performed=False,
        )

    # A lang the dispatcher did not recognise used to fall through to the
    # terminal `else` and be answered ok:true ("no syntax checker for this file
    # type"), so `lang="JSON"` / `"javascript"` / `"jsonc"` / `"node"` -- all of
    # them different spellings of a language this tool DOES implement -- silently
    # validated nothing. Normalise the spellings, then refuse anything still
    # unknown instead of passing it.
    lang = _VALIDATE_LANG_ALIASES.get(str(lang).strip().lower(), str(lang).strip())
    if lang not in _VALIDATE_LANGS:
        return _json_error(
            f"Unsupported lang {lang!r}. Supported: "
            + ", ".join(sorted(_VALIDATE_LANGS)),
            error_name="UNSUPPORTED_LANG",
            type="VALIDATION_ERROR",
            severity="error",
            retryable=False,
            reason="lang is not one this validator can check",
            ca_next_action="retry_with_supported_lang",
            write_performed=False,
            lang=lang,
            file=str(fp),
            supported_langs=sorted(_VALIDATE_LANGS),
        )
    if lang == "auto":
        ext = fp.suffix.lower()
        if ext == ".py":
            lang = "python"
        elif ext in (".js", ".mjs", ".cjs"):
            lang = "js"
        elif ext in (".ts", ".tsx"):
            lang = "esbuild" if ext == ".tsx" else "ts"
        elif ext == ".jsx":
            lang = "esbuild"
        elif ext == ".vue":
            lang = "vue"
        elif ext == ".html":
            lang = "html"
        elif ext == ".json":
            lang = "json"
        elif ext == ".css":
            lang = "css"
        elif ext == ".toml":
            lang = "toml"
        else:
            lang = "none"

    def _check():
        if lang == "python":
            import py_compile
            out_fd, out_path = tempfile.mkstemp(suffix=".pyc")
            os.close(out_fd)
            try:
                py_compile.compile(str(fp), cfile=out_path, doraise=True)
            except py_compile.PyCompileError as exc:
                return {"ok": False, "lang": "python", "file": str(fp), "error": str(exc)}
            finally:
                try:
                    os.unlink(out_path)
                except Exception:
                    pass
            try:
                # campaign-1608 (A19/B8): invoke ruff as a MODULE of a CONSOLE
                # interpreter. Two separate defects live here, both of which made
                # validate_file report every python file as clean:
                #   1) a bare "ruff" name resolves off PATH; the [A] service does
                #      not necessarily have the venv Scripts dir on its PATH, so it
                #      raised FileNotFoundError -> swallowed by the except below.
                #   2) sys.executable is pythonw.exe when [A] runs as a service. A
                #      GUI-subsystem parent has no stdout handle, so the console
                #      child returns rc=1 (findings exist) with stdout AND stderr
                #      EMPTY. Measured live, not inferred. pytest never reproduces
                #      this because pytest runs under python.exe.
                # _console_python() fixes (2); "-m ruff" fixes (1).
                # campaign-1608 (A51): "--color never". A caller carrying
                # FORCE_COLOR (the harness sets =3; so does most CI) makes ruff
                # wrap output in ANSI SGR escapes even into a pipe. Every SGR ends
                # in a letter (\x1b[31m), and _py_error_ruff_hint matches rules
                # case-insensitively, so that trailing 'm' satisfies the negative
                # lookbehind and NO rule ever matches -- ruff_rules_matched goes []
                # and ruff_fix_applicable goes false while lint_warning still lists
                # every finding: a silent slide toward "clean". --color never wins
                # over FORCE_COLOR; NO_COLOR does not (proven in graph/tools.py's
                # _RUFF_FIX_OVERRIDES), so the flag -- not an env scrub -- is the
                # authoritative fix and matches the house idiom. Parser also strips
                # ANSI now (layer 2) in case a future call site forgets this flag.
                # argv lives in _ruff_lint_argv: the write-time baseline in
                # _lint_only_introduced must lint with exactly these rules.
                r = subprocess.run(_ruff_lint_argv(fp), capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=10)
                if r.returncode == 1:
                    err = (r.stdout or "").strip() or (r.stderr or "").strip()
                    if not err:
                        # Fail loud (7-RULE #7). rc=1 means ruff DID find something;
                        # empty text means we lost it in transit. Saying nothing here
                        # is what let defect (2) hide behind a green "clean" result.
                        return {"ok": True, "lang": "python", "file": str(fp), "lint_skipped_reason": "ruff exited 1 (findings exist) but produced no output on stdout/stderr - lint result is UNKNOWN, not clean", "ruff_fix_applicable": False, "ruff_rules_matched": [], "ruff_fix_hint": ""}
                    applicable, matched_rules, hint_text = _py_error_ruff_hint(err)
                    return {"ok": True, "lang": "python", "file": str(fp), "lint_warning": err, "ruff_fix_applicable": applicable, "ruff_rules_matched": matched_rules, "ruff_fix_hint": hint_text}
                if r.returncode != 0:
                    # Fail loud too: rc 2 is ruff itself refusing (an unparseable
                    # project ruff.toml, an unknown selector). It linted nothing,
                    # and the bare return below read exactly like a clean file.
                    detail = ((r.stderr or "").strip() or (r.stdout or "").strip())[:300]
                    return {"ok": True, "lang": "python", "file": str(fp), "lint_skipped_reason": f"ruff exited {r.returncode} ({detail}) - lint result is UNKNOWN, not clean", "ruff_fix_applicable": False, "ruff_rules_matched": [], "ruff_fix_hint": ""}
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                # Also fail loud: a missing or hung linter is not a clean file.
                return {"ok": True, "lang": "python", "file": str(fp), "lint_skipped_reason": f"ruff could not run ({type(exc).__name__}) - lint result is UNKNOWN, not clean", "ruff_fix_applicable": False, "ruff_rules_matched": [], "ruff_fix_hint": ""}
            return {"ok": True, "lang": "python", "file": str(fp)}
        elif lang == "js":
            try:
                r = subprocess.run(
                    ["node", "--check", str(fp)],
                    capture_output=True, text=True, timeout=10
                )
                if r.returncode == 0:
                    return {"ok": True, "lang": "js", "file": str(fp)}
                return {"ok": False, "lang": "js", "file": str(fp), "error": _strip_ansi(r.stderr.strip())}
            except FileNotFoundError:
                return {"ok": False, "lang": "js", "file": str(fp), "error": "node not found in PATH"}
            except Exception as exc:
                return {"ok": False, "lang": "js", "file": str(fp), "error": str(exc)}
        elif lang == "ts":
            try:
                r = subprocess.run(
                    ["npx", "tsc", "--noEmit", "--pretty", "false", str(fp)],
                    capture_output=True, text=True, timeout=30
                )
                if r.returncode == 0:
                    return {"ok": True, "lang": "ts", "file": str(fp)}
                err = (r.stderr or "").strip() or (r.stdout or "").strip() or "TypeScript compile failed"
                return {"ok": False, "lang": "ts", "file": str(fp), "error": err}
            except FileNotFoundError:
                return {"ok": False, "lang": "ts", "file": str(fp), "error": "npx/tsc not found in PATH"}
            except Exception as exc:
                return {"ok": False, "lang": "ts", "file": str(fp), "error": str(exc)}
        elif lang == "esbuild":
            try:
                out_fd, out_path = tempfile.mkstemp(suffix=".js")
                os.close(out_fd)
                cmd = ["esbuild", str(fp), "--bundle=false", f"--outfile={out_path}"]
                resolved = shutil.which(cmd[0])
                if not resolved:
                    appdata = os.environ.get("APPDATA", "")
                    candidate = Path(appdata) / "npm" / "esbuild.cmd" if appdata else None
                    if candidate and candidate.exists():
                        resolved = str(candidate)
                if resolved:
                    cmd[0] = resolved
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                try:
                    os.unlink(out_path)
                except Exception:
                    pass
                if r.returncode == 0:
                    return {"ok": True, "lang": "esbuild", "file": str(fp)}
                err = _strip_ansi((r.stderr or "").strip() or (r.stdout or "").strip()) or "esbuild parse failed"
                return {"ok": False, "lang": "esbuild", "file": str(fp), "error": err}
            except FileNotFoundError:
                return {"ok": False, "lang": "esbuild", "file": str(fp), "error": "esbuild not found in PATH"}
            except Exception as exc:
                try:
                    os.unlink(out_path)
                except Exception:
                    pass
                return {"ok": False, "lang": "esbuild", "file": str(fp), "error": str(exc)}
        elif lang == "html":
            try:
                content = fp.read_text(encoding="utf-8")
            except Exception as exc:
                return {"ok": False, "lang": "html", "file": str(fp), "error": str(exc)}
            parser = _HTMLTagStackParser()
            try:
                parser.feed(content)
                parser.close()
            except Exception as exc:
                return {"ok": False, "lang": "html", "file": str(fp), "error": f"HTML parse failed: {exc}", "unclosed_tags": parser.unclosed_tags, "tag_errors": parser.tag_errors, "truncated": True, "ruff_fix_applicable": False}
            unclosed_tags = parser.unclosed_tags
            tag_errors = parser.tag_errors
            if unclosed_tags or tag_errors:
                return {"ok": False, "lang": "html", "file": str(fp), "error": "HTML document has unclosed or mismatched tags", "unclosed_tags": unclosed_tags, "tag_errors": tag_errors, "truncated": bool(unclosed_tags), "ruff_fix_applicable": False}
            scripts = re.findall(r'<script(?:[^>]*)>(.*?)</script>', content, re.IGNORECASE | re.DOTALL)
            for i, body in enumerate(scripts):
                body = body.strip()
                if not body:
                    continue
                tmp_path = ""
                try:
                    with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w", encoding="utf-8") as tmp:
                        tmp.write(body)
                        tmp_path = tmp.name
                    r = subprocess.run(["node", "--check", tmp_path], capture_output=True, text=True, timeout=10)
                    os.unlink(tmp_path)
                    if r.returncode != 0:
                        return {"ok": False, "lang": "html", "file": str(fp), "error": f"script[{i}] syntax error: {_strip_ansi(r.stderr.strip())}"}
                except FileNotFoundError:
                    return {"ok": False, "lang": "html", "file": str(fp), "error": "node not found in PATH - cannot validate <script> blocks"}
                except Exception as exc:
                    try:
                        if tmp_path:
                            os.unlink(tmp_path)
                    except Exception:
                        pass
                    return {"ok": False, "lang": "html", "file": str(fp), "error": str(exc)}
            return {"ok": True, "lang": "html", "file": str(fp), "note": f"HTML OK: {len(scripts)} script block(s) checked, full document balanced", "unclosed_tags": [], "tag_errors": [], "truncated": False, "ruff_fix_applicable": False}
        elif lang == "vue":
            try:
                content = fp.read_text(encoding="utf-8")
                match = re.search(r"<script\b[^>]*>(.*?)</script>", content, re.IGNORECASE | re.DOTALL)
                if not match:
                    return {"ok": True, "lang": "vue", "file": str(fp), "note": "No <script> block found"}
                suffix = ".ts" if re.search(r"<script\b[^>]*\blang=[\"']ts[\"']", content, re.IGNORECASE) else ".js"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="w", encoding="utf-8") as tmp:
                    tmp.write(match.group(1))
                    tmp_path = tmp.name
                out_fd, out_path = tempfile.mkstemp(suffix=".js")
                os.close(out_fd)
                cmd = ["esbuild", tmp_path, "--bundle=false", f"--outfile={out_path}"]
                resolved = shutil.which(cmd[0])
                if not resolved:
                    appdata = os.environ.get("APPDATA", "")
                    candidate = Path(appdata) / "npm" / "esbuild.cmd" if appdata else None
                    if candidate and candidate.exists():
                        resolved = str(candidate)
                if resolved:
                    cmd[0] = resolved
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                for path in (tmp_path, out_path):
                    try:
                        os.unlink(path)
                    except Exception:
                        pass
                if r.returncode == 0:
                    return {"ok": True, "lang": "vue", "file": str(fp)}
                err = _strip_ansi((r.stderr or "").strip() or (r.stdout or "").strip()) or "Vue script parse failed"
                return {"ok": False, "lang": "vue", "file": str(fp), "error": err}
            except FileNotFoundError:
                return {"ok": False, "lang": "vue", "file": str(fp), "error": "esbuild not found in PATH"}
            except Exception as exc:
                for path in (locals().get("tmp_path", ""), locals().get("out_path", "")):
                    if path:
                        try:
                            os.unlink(path)
                        except Exception:
                            pass
                return {"ok": False, "lang": "vue", "file": str(fp), "error": str(exc)}
        elif lang == "json":
            try:
                # json.loads accepts NaN/Infinity as an extension and silently
                # last-wins duplicate keys. Both produce a file that json.parse,
                # jq and serde_json all reject or read differently -- calling it
                # valid is the validator reporting the opposite of the truth.
                def _reject_constant(name):
                    raise ValueError(
                        f"{name} is not valid JSON (RFC 8259 has no such literal; "
                        f"json.parse / jq / serde_json all reject it)")

                def _reject_duplicates(pairs):
                    seen = {}
                    for key, value in pairs:
                        if key in seen:
                            raise ValueError(
                                f"duplicate key {key!r} — JSON parsers silently "
                                f"keep the last value, discarding the first")
                        seen[key] = value
                    return seen

                json.loads(fp.read_text(encoding="utf-8"),
                           parse_constant=_reject_constant,
                           object_pairs_hook=_reject_duplicates)
                return {"ok": True, "lang": "json", "file": str(fp), "checked": True}
            except json.JSONDecodeError as exc:
                return {"ok": False, "lang": "json", "file": str(fp), "error": str(exc)}
            except Exception as exc:
                return {"ok": False, "lang": "json", "file": str(fp), "error": str(exc)}
        elif lang == "css":
            try:
                content = fp.read_text(encoding="utf-8")
                opens = content.count("{")
                closes = content.count("}")
                if opens != closes:
                    return {"ok": False, "lang": "css", "file": str(fp),
                            "error": f"Unbalanced braces: {opens} open vs {closes} close"}
                return {"ok": True, "lang": "css", "file": str(fp)}
            except Exception as exc:
                return {"ok": False, "lang": "css", "file": str(fp), "error": str(exc)}
        elif lang == "toml":
            # #5, measured 2026-09-24: a .toml write answered ok with no check at
            # all while the file on disk no longer parsed. tomllib is the stdlib
            # reader Python's own consumers load TOML with; its message carries
            # "line N", which the line/col parse below reads. utf-8, not
            # utf-8-sig: tomllib refuses a BOM, so a BOM'd file is reported as
            # the consumer will see it, the same stance as the json branch.
            try:
                import tomllib
            except ImportError:
                return {"ok": True, "lang": "toml", "file": str(fp), "checked": False,
                        "note": "tomllib unavailable (Python < 3.11) - TOML NOT validated"}
            try:
                tomllib.loads(fp.read_text(encoding="utf-8"))
                return {"ok": True, "lang": "toml", "file": str(fp)}
            except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
                return {"ok": False, "lang": "toml", "file": str(fp), "error": str(exc)}
        else:
            # Reachable only via lang="auto" on an extension with no checker --
            # the caller asked us to guess and there was nothing to run. That
            # stays a pass (write_fix validates every write and must not fail a
            # .txt), but `checked: False` marks it so a reader cannot mistake
            # "nothing ran" for "validated". An explicitly-named lang can no
            # longer land here; it is refused as UNSUPPORTED_LANG above.
            try:
                fp.read_text(encoding="utf-8")
                return {"ok": True, "lang": "none", "file": str(fp), "checked": False,
                        "note": "No syntax checker for this file type - UTF-8 readability OK"}
            except Exception as exc:
                return {"ok": False, "lang": "none", "file": str(fp), "checked": False,
                        "error": str(exc)}

    result = await asyncio.to_thread(_check)
    # Every branch except the no-checker fallback really ran a checker; say so
    # explicitly so `ok:true` is never ambiguous about whether anything was
    # verified. The fallback sets checked=False itself.
    result.setdefault("checked", True)
    if not result.get("ok", False):
        err_text = str(result.get("error", "syntax validation failed"))
        result.setdefault("error_name", "VALIDATION_FAILED")
        result.setdefault("type", "VALIDATION_ERROR")
        result.setdefault("severity", "error")
        result.setdefault("retryable", False)
        result.setdefault("reason", "syntax validation failed")
        result.setdefault("ca_next_action", "fix_syntax_before_retry")
        result.setdefault("write_performed", False)
        if result.get("lang") == "python":
            applicable, matched_rules, hint_text = _py_error_ruff_hint(err_text)
            result.update(ruff_fix_applicable=applicable, ruff_rules_matched=matched_rules, ruff_fix_hint=hint_text)
        else:
            result.setdefault("ruff_fix_applicable", False)
            result.setdefault("ruff_rules_matched", [])
            result.setdefault("ruff_fix_hint", "")
        line_col = re.search(r"(?:line\s+|:)(\d+)(?::(\d+))?", err_text, re.IGNORECASE)
        if line_col:
            result.setdefault("line", int(line_col.group(1)))
            if line_col.group(2):
                result.setdefault("column", int(line_col.group(2)))
    else:
        result.setdefault("ruff_fix_applicable", False)
        result.setdefault("ruff_rules_matched", [])
        result.setdefault("ruff_fix_hint", "")
    # Task 4 auto-chain: hint ruff_fix when applicable
    if result.get("ruff_fix_applicable"):
        result["ca_hint"] = _RUFF_FIX_CA_HINT
        result.setdefault("ca_next_action", "ruff_fix")
    return json.dumps(result, ensure_ascii=False)


async def validate_symbols(file_path: str, project_path: str = "") -> str:
    """Post-write integration check: syntax + AST symbol comparison vs INDEX.
    Phát hiện hàm/class bị xóa ngoài ý muốn khi write_fix. Dùng sau write_fix thay vì chỉ validate_file."""
    try:
        fp = _resolve_project_file(project_path, file_path)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    project_root = project_path or find_project_root(str(fp))
    drift = await _index_drift(project_root) if project_root else None
    target_rel = None
    if project_root:
        try:
            target_rel = str(fp.relative_to(Path(project_root).resolve())).replace("\\", "/")
        except ValueError:
            target_rel = str(fp).replace("\\", "/")

    if drift and target_rel:
        deleted = set(drift.get("deleted", []))
        if target_rel in deleted:
            return _json_error(
                "Target file missing from filesystem",
                category="stale_index",
                drift=drift,
            )

    if not fp.exists():
        payload = {"ok": False, "error": f"File not found: {file_path}"}
        if target_rel:
            payload["category"] = "stale_index"
            if drift:
                payload["drift"] = drift
        return json.dumps(payload, ensure_ascii=False)

    # Step 1: syntax check
    val = json.loads(await validate_file(str(fp), project_path=""))
    if not val.get("ok"):
        return json.dumps({"ok": False, "file": str(fp),
                           "syntax_error": val.get("error"), "syntax_ok": False})

    def _run():
        ext = fp.suffix.lower()
        if ext == ".py":
            lang = "python"
        elif ext in (".js", ".mjs", ".cjs"):
            lang = "js"
        elif ext in (".ts", ".tsx"):
            lang = "ts"
        else:
            lang = "none"

        content = fp.read_text(encoding="utf-8")
        if content.startswith('﻿'):
            content = content.lstrip('﻿')
        lines = split_lines(content)

        # Step 2: extract symbols from file — only top-level + class methods
        # (matches index granularity, excludes nested helpers like _run/_write
        # that AST walk would otherwise pollute symbols_added with).
        symbols_found = []
        extract_error = None
        if lang == "python":
            import ast as _ast
            try:
                tree = _ast.parse(content)
                for node in tree.body:
                    if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                        symbols_found.append(node.name)
                    elif isinstance(node, _ast.ClassDef):
                        symbols_found.append(node.name)
                        # include direct class methods (one level deep)
                        for sub in node.body:
                            if isinstance(sub, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                                symbols_found.append(sub.name)
            except SyntaxError as exc:
                extract_error = f"AST parse failed: {exc}"
            except Exception as exc:
                extract_error = f"Extract failed: {exc}"
        elif lang in ("js", "ts"):
            matches = re.findall(
                r'(?:function\s+(\w+)|class\s+(\w+)|'
                r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:function|\())',
                content
            )
            symbols_found = [s for group in matches for s in group if s]

        # Step 3: INDEX comparison — robust path normalization.
        # INDEX stores symbols at top level: {"functions": {name: [{file, line_start, line_end}]}}
        # File entry has only `symbols_count` (int), not the symbol list itself.
        symbols_indexed = []
        indexed_line_count = None
        index_match_method = None
        matched_file_key = None
        root = project_path or find_project_root(str(fp))
        if root:
            try:
                idx_path = _project_cache_dir(root) / "INDEX.json"
                if idx_path.exists():
                    idx = json.loads(idx_path.read_text(encoding="utf-8"))
                    files_idx = idx.get("files", {})
                    root_resolved = Path(root).resolve()
                    entry = None

                    # Find the file's INDEX key (multiple match strategies)
                    candidates = []
                    try:
                        rel = str(fp.relative_to(root_resolved)).replace("\\", "/")
                        candidates.append(("relative", rel))
                    except ValueError:
                        pass
                    candidates.append(("absolute", str(fp).replace("\\", "/")))

                    for method, cand in candidates:
                        if cand in files_idx:
                            entry = files_idx[cand]
                            matched_file_key = cand
                            index_match_method = method
                            break
                        # case-insensitive fallback
                        cand_lower = cand.lower()
                        for k, v in files_idx.items():
                            if k.replace("\\", "/").lower() == cand_lower:
                                entry = v
                                matched_file_key = k
                                index_match_method = method + "-ci"
                                break
                        if entry:
                            break

                    # Tail match last resort (e.g. tests/test_x.py)
                    if not entry:
                        target_tail = (fp.parent.name + "/" + fp.name).lower()
                        for k, v in files_idx.items():
                            if k.replace("\\", "/").lower().endswith(target_tail):
                                entry = v
                                matched_file_key = k
                                index_match_method = "tail"
                                break
                    # Basename fallback for UI/HTML assets whose parent path can differ.
                    if not entry:
                        target_name = fp.name.lower()
                        for k, v in files_idx.items():
                            if Path(k).name.lower() == target_name:
                                entry = v
                                matched_file_key = k
                                index_match_method = "basename"
                                break

                    if entry:
                        indexed_line_count = entry.get("line_count")
                        # Walk top-level functions/classes dicts to find symbols for this file
                        for sym_dict_key in ("functions", "classes"):
                            sym_dict = idx.get(sym_dict_key, {}) or {}
                            for sym_name, occurrences in sym_dict.items():
                                if not isinstance(occurrences, list):
                                    continue
                                for occ in occurrences:
                                    if not isinstance(occ, dict):
                                        continue
                                    occ_file = (occ.get("file") or "").replace("\\", "/")
                                    if occ_file == matched_file_key.replace("\\", "/"):
                                        symbols_indexed.append(sym_name)
                                        break
            except Exception as _exc:
                index_match_method = f"error: {_exc}"

        intact  = [s for s in symbols_found if s in symbols_indexed]
        added   = [s for s in symbols_found if s not in symbols_indexed]
        removed = [s for s in symbols_indexed if s not in symbols_found]
        line_delta = len(lines) - indexed_line_count if indexed_line_count else 0

        # If INDEX is older than the target file, report target-specific stale
        # state even when the top-level symbol names are unchanged.
        stale_index = False
        target_file_newer_than_index = False
        try:
            file_mtime = fp.stat().st_mtime
            idx_mtime = 0.0
            if root:
                _idx_path2 = _project_cache_dir(root) / "INDEX.json"
                if _idx_path2.exists():
                    idx_mtime = _idx_path2.stat().st_mtime
            if idx_mtime > 0 and idx_mtime < file_mtime:
                target_file_newer_than_index = True
            if removed and idx_mtime > 0 and idx_mtime < file_mtime:
                stale_index = True
        except Exception:
            pass

        result = {
            "ok": (len(removed) == 0 or stale_index) and not extract_error,
            "file": str(fp),
            "lang": lang,
            "symbols_supported": lang in ("python", "js", "ts"),
            "syntax_ok": True,
            "line_count": len(lines),
            "line_delta": line_delta,
            "stale_index": stale_index,
            "symbols_found": symbols_found,
        }
        if (drift and target_rel in set(drift.get("modified", []))) or target_file_newer_than_index:
            stale_index = True
            result["ok"] = not extract_error
            result["stale_index"] = True
            result["category"] = "stale_index_live_fallback"
            result["warning"] = "INDEX stale; returned live AST symbols for target file."
            result["drift"] = drift
        elif stale_index:
            result["category"] = "stale_index"
            result["warning"] = "INDEX stale; run refresh_project_map before treating removed symbols as real."
        if extract_error:
            result["extract_error"] = extract_error
        if symbols_indexed:
            result["symbols_intact"]  = intact
            result["symbols_added"]   = added
            result["symbols_removed"] = [] if stale_index else removed
            result["index_match"]     = index_match_method
            if removed and not stale_index:
                if stale_index:
                    result["warning"] = (
                        f"INDEX stale (older than file); {len(removed)} symbol(s) appear "
                        f"removed but may be refactored. Run refresh_project_map to reset. "
                        f"removed={removed}"
                    )
                else:
                    result["warning"] = f"{len(removed)} symbol(s) removed vs INDEX: {removed}"
        else:
            result["warning"] = (
                f"No INDEX entry for {fp.name}; symbol comparison skipped. "
                f"Match attempt: {index_match_method or 'no project root'}. "
                f"Fix: refresh_project_map(project_path) or check INDEX coverage."
            )
            if lang == "none":
                result["category"] = "symbols_unsupported"
                result["ca_next_action"] = "skip_validate_symbols_for_non_code_or_use_validate_file"
        return result

    result = await asyncio.to_thread(_run)
    return json.dumps(result, ensure_ascii=False)


async def post_write_batch_verify(project_path: str, changed_files: list[str],
                                  run_validate_symbols: bool = True,
                                  run_cache_delta: bool = True,
                                  run_validate_encoding: bool = True) -> str:
    """Run the standard non-mutating verification bundle after a write batch."""
    if not isinstance(changed_files, list) or not changed_files:
        return _json_error(
            "changed_files must be a non-empty list",
            error_name="EMPTY_CHANGED_FILES",
            retryable=True,
            ca_next_action="provide_changed_files",
        )
    root = Path(project_path).resolve()
    if not root.exists():
        return _json_error(
            f"Project path not found: {project_path}",
            error_name="PROJECT_PATH_NOT_FOUND",
            ca_next_action="call_resolve_project_path",
        )

    validate_file_results = []
    validate_symbols_results = []
    encoding_results = []
    unsupported_tools = []
    files_checked = []
    symbol_exts = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx"}

    for raw_file in changed_files:
        file_label = str(raw_file or "").strip()
        if not file_label:
            validate_file_results.append({
                "ok": False,
                "file_path": file_label,
                "error": "empty file path",
                "ca_next_action": "remove_empty_changed_file",
            })
            continue
        try:
            fp = _resolve_project_file(str(root), file_label)
            rel = str(fp.relative_to(root)).replace("\\", "/")
        except Exception as exc:
            validate_file_results.append({
                "ok": False,
                "file_path": file_label,
                "error": str(exc),
                "ca_next_action": "fix_project_path_or_file_path",
            })
            continue
        files_checked.append(rel)
        file_result = json.loads(await validate_file(rel, project_path=str(root)))
        validate_file_results.append(file_result)

        if run_validate_encoding:
            if fp.exists() and not _is_binary_file(fp):
                encoding_results.append(json.loads(await validate_encoding(rel, project_path=str(root))))
            else:
                unsupported_tools.append({
                    "tool": "validate_encoding",
                    "file_path": rel,
                    "reason": "binary_or_missing_file",
                })

        if run_validate_symbols:
            if fp.suffix.lower() in symbol_exts:
                symbol_result = json.loads(await validate_symbols(rel, project_path=str(root)))
                validate_symbols_results.append(symbol_result)
                if symbol_result.get("category") == "symbols_unsupported":
                    unsupported_tools.append({
                        "tool": "validate_symbols",
                        "file_path": rel,
                        "reason": symbol_result.get("warning", "unsupported"),
                    })
            else:
                unsupported_tools.append({
                    "tool": "validate_symbols",
                    "file_path": rel,
                    "reason": "unsupported_file_type",
                })

    cache_delta_result = None
    if run_cache_delta:
        cache_delta_result = json.loads(await cache_delta(str(root)))

    failed = []
    for group_name, results in (
        ("validate_file", validate_file_results),
        ("validate_symbols", validate_symbols_results),
        ("validate_encoding", encoding_results),
    ):
        for item in results:
            if item.get("ok") is False and item.get("category") != "symbols_unsupported":
                failed.append({"tool": group_name, "file": item.get("file") or item.get("file_path"), "error": item.get("error") or item.get("warning")})
    overall_status = "pass" if not failed else "fail"
    return json.dumps({
        "ok": overall_status == "pass",
        "overall_status": overall_status,
        "files_checked": files_checked,
        "validate_file_results": validate_file_results,
        "validate_symbols_results": validate_symbols_results,
        "encoding_results": encoding_results,
        "cache_delta_result": cache_delta_result,
        "unsupported_tools": unsupported_tools,
        "failed_checks": failed,
        "cache_delta_runs": 1 if run_cache_delta else 0,
        "ca_next_action": "review_failed_checks" if failed else "proceed_or_refresh_project_map_if_needed",
    }, ensure_ascii=False)


# ─── Session Management Tools (Phase 2) ──────────────────────────────────────

def _r43_project_switch_context(project_path_norm: str) -> dict:
    """V15 R43 P1 #1 — Compact multi-project context summary surfaced in open_session.

    Goal: when CA fixes multiple projects ([Ab], [Ac]) sequentially, give them an
    anchor showing what was touched recently across projects, what's active here,
    so context drift is reinforced by [A] not memory.

    Returns:
      - recent_projects_today: [{project_path, last_session_id, started_at, ended_at}]
      - current_project_recent_files_5: [str] from events table (write/read events)
      - current_project_active_workflow_hint: str (from workflow state file)
    """
    ctx = {
        "recent_projects_today": [],
        "current_project_recent_files_5": [],
        "current_project_recent_queries_5": [],  # R43.1 Gap#2: tách search artifact riêng
        "current_project_active_workflow_hint": "",
    }
    # R43.1 Gap#2: whitelist tools — only true file-touch tools populate recent_files.
    # `action_type='read'` was too broad (included search_code/find_symbol → polluted with query strings).
    _R43_FILE_TOUCH_TOOLS = {
        "write_fix", "write_fix_html", "write_new_file",
        "read_file_range", "validate_file", "delete_file",
        "apply_after_dry_run", "replace_between_anchors",
        "write_transaction", "get_file_symbols",
    }
    _R43_QUERY_TOOLS = {
        "search_code", "find_symbol", "find_callers",
        "find_definition_and_usage", "query_index", "regex_search",
        "ast_query",
    }
    def _looks_like_path(s: str) -> bool:
        if not s or len(s) > 300:
            return False
        if "/" in s or "\\" in s:
            return True
        # Has file extension (.py, .js, .md, .json, .html, .css, .txt, .yml, .toml, .bat, .ps1, .ipynb, .cjs, .mjs, .ts, .tsx, .jsx)
        if "." in s:
            ext = s.rsplit(".", 1)[-1].lower()
            if ext in {"py","js","md","json","html","css","txt","yml","yaml","toml","bat","ps1","ipynb","cjs","mjs","ts","tsx","jsx","sh","go","rs","java","c","cpp","h"}:
                return True
        return False
    try:
        from datetime import datetime as _dt_psc
        today_iso = _dt_psc.now().strftime("%Y-%m-%d")
        # Phase B (2026-08-11): reuse the process-wide thread-local read connection
        # (query_only=ON, busy_timeout set, mmap/cache tuned) instead of opening a
        # fresh sqlite3.connect per open_session call. Under a 20-concurrent burst
        # this avoids 20 separate connection establishments + page-cache warmup.
        # Connection is owned by the thread; do NOT close here.
        conn = _session_store_module._session_read_connection()
        try:
            # Recent sessions today across ALL projects
            rows = conn.execute(
                "SELECT project_path, session_id, started_at, ended_at, status "
                "FROM sessions WHERE started_at LIKE ? "
                "ORDER BY started_at DESC LIMIT 8",
                (f"{today_iso}%",)
            ).fetchall()
            seen_proj = set()
            for r_proj, r_sid, r_started, r_ended, r_status in rows:
                if r_proj in seen_proj:
                    continue
                seen_proj.add(r_proj)
                ctx["recent_projects_today"].append({
                    "project_path": r_proj,
                    "last_session_id": r_sid,
                    "started_at": r_started,
                    "ended_at": r_ended,
                    "status": r_status,
                    "is_current": r_proj == project_path_norm,
                })
                if len(ctx["recent_projects_today"]) >= 5:
                    break
            # Recent files touched in CURRENT project
            current_sids = conn.execute(
                "SELECT session_id FROM sessions WHERE project_path=? "
                "ORDER BY started_at DESC LIMIT 2",
                (project_path_norm,)
            ).fetchall()
            sid_list = [s[0] for s in current_sids]
            if sid_list:
                placeholders = ",".join(["?"] * len(sid_list))
                # R43.1 Gap#2: select tool_name too so we can whitelist-filter
                evt_rows = conn.execute(
                    f"SELECT target, tool_name FROM events WHERE session_id IN ({placeholders}) "
                    "AND target IS NOT NULL AND target != '' "
                    "ORDER BY id DESC LIMIT 80",
                    sid_list
                ).fetchall()
                seen_f = set()
                seen_q = set()
                for (tgt, tname) in evt_rows:
                    if not tgt:
                        continue
                    tname_s = (tname or "")
                    if tname_s in _R43_FILE_TOUCH_TOOLS and _looks_like_path(tgt) and tgt not in seen_f:
                        seen_f.add(tgt)
                        if len(ctx["current_project_recent_files_5"]) < 5:
                            ctx["current_project_recent_files_5"].append(tgt)
                    elif tname_s in _R43_QUERY_TOOLS and tgt not in seen_q:
                        seen_q.add(tgt)
                        if len(ctx["current_project_recent_queries_5"]) < 5:
                            ctx["current_project_recent_queries_5"].append({"tool": tname_s, "query": tgt})
                    if (len(ctx["current_project_recent_files_5"]) >= 5
                            and len(ctx["current_project_recent_queries_5"]) >= 5):
                        break
        finally:
            # Phase B: connection is the thread-local read conn; do not close.
            # Roll back any accidental open transaction so the next read on this
            # thread sees a clean snapshot.
            try:
                if conn.in_transaction:
                    conn.rollback()
            except Exception:
                pass
    except Exception as _exc_psc:
        ctx["error_sessions_db"] = str(_exc_psc)
    # Active workflow hint (best-effort)
    try:
        _wf_state_dir = Path(project_path_norm) / ".orchestrator_cache" / "workflows"
        if _wf_state_dir.exists():
            for f in sorted(_wf_state_dir.glob("*.state.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                    if d.get("status") in {"active", "in_progress"}:
                        ctx["current_project_active_workflow_hint"] = (
                            f"{f.stem.replace('.state', '')}: step {d.get('current_step', '?')}"
                        )
                        break
                except Exception:
                    continue
    except Exception:
        pass
    return ctx


def _transport_policy_payload() -> dict:
    """V15 R46 — Machine-readable transport policy surfaced in open_session.

    Hard policy that lives in the open_session response (not just docs) so
    CA can read it at session start without needing prompt memory. The
    matching enforcement lives in run_project_command (POWERSHELL_INLINE_FORBIDDEN
    structured error) and the canonical wrapper is run_project_test_script.
    """
    import sys as _sys_tp
    return {
        "platform": "windows" if _sys_tp.platform == "win32" else "posix",
        "windows": {
            "powershell_inline": "forbidden",
            "node_inline": "forbidden",
            "preferred_transport": "script_file",
            "preferred_tool": "run_project_test_script",
            "enforcement": {
                "tool": "run_project_command",
                "error_names": [
                    "POWERSHELL_INLINE_FORBIDDEN",
                    "NODE_INLINE_FORBIDDEN",  # R49
                ],
                "triggers_common": [
                    "length>800", "multiline", "quote_density>=10",
                    "non_ascii", "regex_json_unc_combo",
                    "list_form_with_shell_metachars",
                ],
                "triggers_ps_only": ["continuation_>>", "here_string"],
            },
            "rationale": (
                "Inline shells on Windows mangle non-ASCII (Vietnamese diacritics) "
                "via console code page, lose backslash escapes through cmd /c "
                "re-parsing, and break on continuation/here-string/heavy-quote "
                "payloads. The script-file path writes the body as a temp "
                "artifact (UTF-8 BOM for .ps1 / plain UTF-8 for .js) and invokes "
                "via -File / direct path so the interpreter reads source bytes "
                "unchanged."
            ),
            "path_as_argv_warning": (
                "R55 ADDITIONAL RULE: NEVER pass non-ASCII paths (Vietnamese "
                "diacritics, UNC shares with national characters) as DIRECT "
                "shell argv even with permitted runtimes. The Windows console "
                "argv re-encoding still applies BEFORE the interpreter parses "
                "argv. Symptom: downstream tools receive mojibake bytes and "
                "filesystem probes return exists=false. Safe routes: "
                "(a) write path into the script file body (run_ps1_script / "
                "run_node_script — body is read as UTF-8/UTF-8-BOM, no argv "
                "transit); (b) pass path as a JSON request body field (HTTP "
                "transport stays UTF-8 end-to-end). The R54 input mojibake "
                "detector on sample_unc_paths_with_root_check will surface "
                "INPUT_MOJIBAKE_SUSPECTED if upstream argv corruption occurred."
            ),
            "one_shot_recipe_powershell": {
                "tool": "run_project_test_script",
                "args": {
                    "project_path": "<project_root>",
                    "runtime": "powershell",
                    "script_content": "<multiline PS body, UTF-8 safe>",
                    "script_label": "<short_audit_identifier>",
                },
                "behaviour": (
                    "[A] writes .orchestrator_cache/test_scripts/<hash>.ps1 "
                    "with UTF-8 BOM (PS 5.1 compat) and runs `powershell "
                    "-NoProfile -ExecutionPolicy Bypass -File <temp>`."
                ),
            },
            "one_shot_recipe_node": {
                "tool": "run_project_test_script",
                "args": {
                    "project_path": "<project_root>",
                    "runtime": "node",
                    "script_content": "<multiline JS body — JSON.stringify/UNC/regex safe>",
                    "script_label": "<short_audit_identifier>",
                },
                "behaviour": (
                    "[A] writes .orchestrator_cache/test_scripts/<hash>.js and "
                    "runs `node <temp.js>` — JS string literals/regex/UNC bytes "
                    "pass through unchanged."
                ),
            },
        },
        "posix": {
            "powershell_inline": "n/a",
            "preferred_transport": "exec",
            "preferred_tool": "run_project_command",
            "shell_mode": "exec_or_posix",
        },
        "ca_next_action": "use_script_file_when_payload_is_non_trivial",
        "schema_version": "v15.r56",
    }


async def open_session(session_title: str = "", project_path: str = ".", mode: str = "T1",
                        verbosity: str = "full", agent_id: str = "",
                        force_new_session: bool = False,
                        apply_requested_title: bool = False) -> str:
    """Open or resume one active session per project and agent.

    Resume keeps its title and mode by default; use rename_session to rename.
    apply_requested_title explicitly renames in place; force_new_session closes
    the old row and creates a new one atomically, refusing pending work.
    Both flags default false and cannot be combined in a single call.

    V15 R22: `verbosity` ∈ {full|terse|ref} — terse compresses matrix_audit to
    {drift_count, alias_drift_count, progressive_gap_count} and drops
    path_resolution_note, progressive_gaps_hint, resolved_from, project_path_confirmed,
    root_exists. Saves ~400 tokens/call. Drift safety: auto-force `full` when
    matrix_drift_present=True OR progressive_gap_count>0.

    V15 R46: response includes `transport_policy` — machine-readable hard
    policy (windows.powershell_inline=forbidden, preferred_transport=script_file)
    so CA reads enforced policy at session start instead of relying on prompt
    memory. Enforcement lives in run_project_command (POWERSHELL_INLINE_FORBIDDEN).
    """
    try:
        if type(force_new_session) is not bool or type(apply_requested_title) is not bool:
            return _json_error("Session options must be booleans.", error_name="INVALID_SESSION_OPTIONS")
        if force_new_session and apply_requested_title:
            return _json_error("Choose force_new_session or apply_requested_title per call.",
                               error_name="CONFLICTING_SESSION_OPTIONS")
        if (force_new_session or apply_requested_title) and not agent_id.strip():
            return _json_error("Opt-in session changes require agent_id.", error_name="AGENT_ID_REQUIRED")
        if apply_requested_title and not session_title.strip():
            return _json_error("apply_requested_title requires a non-empty title.", error_name="SESSION_TITLE_REQUIRED")
        await asyncio.to_thread(_init_session_db)
        requested_session_title = session_title
        if not session_title:
            session_title = f"Untitled Session {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        resolution = _resolve_project_path_payload(project_path, mode="folder")
        original_project_path = project_path
        if resolution.get("is_file") and resolution.get("project_path"):
            project_path = resolution["project_path"]
            resolution_note = "file path received; session opened on resolved project root"
            project_path_confirmed = False
            resolved_from = original_project_path
        elif resolution.get("needs_user_confirmation"):
            return json.dumps({
                "ok": False,
                "error": "Unable to confirm project root from project_path",
                "error_name": "PROJECT_PATH_AMBIGUOUS",
                "project_path_confirmed": False,
                "root_exists": False,
                "path_resolution_note": "Call resolve_project_path before open_session.",
                "resolution": resolution,
                "ca_next_action": "call_resolve_project_path",
            }, ensure_ascii=False)
        else:
            project_path = resolution.get("project_path") or project_path
            resolution_note = "project_path confirmed"
            project_path_confirmed = True
            resolved_from = ""
        norm = _session_project_key(project_path)
        requested_mode = mode or "T1"
        canon_mode = _canon_mode(requested_mode)
        if canon_mode not in _VALID_WORKFLOW_MODES:
            return _invalid_mode_error(requested_mode, canon_mode)
        # Anh 2026-08-11: run the session upsert OFF the event loop. _open_or_resume_session
        # goes through the SessionWriter and blocks on a durable-commit Future; calling it
        # synchronously here pinned the loop for the write (up to the 5s stall budget under
        # contention) — a hang vector for large/concurrent open_session bootstraps.
        options = {}
        if force_new_session or apply_requested_title:
            options = {"force_new_session": force_new_session,
                       "apply_requested_title": apply_requested_title}
        session_row, created = await asyncio.to_thread(
            _open_or_resume_session, norm, session_title, canon_mode, agent_id or None,
            **options,
        )
        if session_row.get("ok") is False:
            return json.dumps(session_row, ensure_ascii=False)
        existing = None if created else (
            session_row["session_id"],
            session_row["session_title"],
            session_row["started_at"],
        )
        sid_to_insert = session_row["session_id"]
        if existing:
            # V15 R13: auto-invoke matrix audit (silent, summary only)
            # Phase B (2026-08-11): use TTL-cached summary to avoid re-running
            # the full filesystem scan per call. First call within the TTL still
            # runs _audit_tool_matrix_sync (and persists matrix_log.jsonl).
            _mx = None
            try:
                _mx = await asyncio.to_thread(
                    _matrix_audit_open_session_summary, project_path
                )
            except Exception:
                pass
            # R20: explicit requested vs effective so CA never confuses them
            _eff = str(Path(project_path).resolve()).replace("\\", "/")
            _req = str(Path(original_project_path).resolve()).replace("\\", "/") if original_project_path else _eff
            _mx_summary = (_mx or {}).get("summary") or {}
            resp = {"ok": True, "session_id": existing[0], "session_title": existing[1],
                    "started_at": existing[2], "resumed": True,
                    "agent_id": agent_id,
                    "project_name": Path(project_path).resolve().name,
                    "project_path": norm,
                    "requested_project_path": _req,
                    "effective_project_path": _eff,
                    "relative_base_hint": _eff,
                    "project_path_confirmed": project_path_confirmed,
                    "root_exists": Path(project_path).exists(),
                    "path_resolution_note": resolution_note,
                    "resolved_from": resolved_from,
                    "matrix_audit": _mx_summary,
                    "matrix_drift_present": bool((_mx or {}).get("drift")),
                    "progressive_gap_count": _mx_summary.get("progressive_gap_count", 0),
                    "progressive_gaps_hint": (
                        "Call progressive_matrix_check for gap detail"
                        if _mx_summary.get("progressive_gap_count") else
                        "no progressive gaps"
                    ),
                    # R43 P1 #1: project_switch_context — multi-project anchor
                    "project_switch_context": _r43_project_switch_context(norm),
                    # V15 R46 — machine-readable transport policy (resumed branch)
                    "transport_policy": _transport_policy_payload(),
                    "cslt_policy": __import__("orchestrator.core.ca_policy_matrix", fromlist=["open_session_policy_payload"]).open_session_policy_payload(agent_id=agent_id, project_path=project_path),
                    }
            resp.update(_open_session_mode_fields(
                requested_mode, canon_mode, session_row, created=False,
                project_path=project_path, agent_id=agent_id or ""))
            if apply_requested_title:
                resp.update({key: session_row[key] for key in ("old_title", "new_title", "renamed")})
            if requested_session_title and requested_session_title != existing[1]:
                # ISS-08: a mode conflict may already own ca_next_action. Blindly
                # overwriting it hid one of the two divergences whenever both the
                # title AND the mode were rejected on the same resume, so the
                # caller fixed the title and never learned the mode had not
                # applied. Keep both, and say there are two.
                _prior_action = resp.get("ca_next_action")
                resp.update({
                    "requested_session_title": requested_session_title,
                    "effective_session_title": existing[1],
                    "session_title_mismatch": True,
                    "resume_policy": "active_session_by_project_and_agent",
                    "ca_next_action": "rename_session_if_requested_title_is_intended",
                    "rename_session_payload": {
                        "project_path": project_path, "new_title": requested_session_title,
                        "agent_id": agent_id,
                    },
                })
                if _prior_action:
                    resp["ca_next_actions"] = [
                        "rename_session_if_requested_title_is_intended", _prior_action]
                    resp["resume_rejected"] = ["session_title", "mode"]
            # V15 F4 (D4) - severity-aware drift verbosity (shared decision fn)
            _eff_verbosity = _open_session_drift_decision(resp, _mx, verbosity)
            try:
                import tools_v15 as _v15
                resp = _v15._apply_verbosity(resp, _eff_verbosity, "session_open")
            except Exception:
                pass
            return json.dumps(resp, ensure_ascii=False)
        # Atomic helper created the row and returned its persisted values.
        now = session_row["started_at"]
        sid = sid_to_insert
        # V15 R13: auto-invoke matrix audit on new session (silent, summary only)
        # Phase B (2026-08-11): TTL-cached summary; see _matrix_audit_open_session_summary.
        _mx = None
        try:
            _mx = await asyncio.to_thread(
                _matrix_audit_open_session_summary, project_path
            )
        except Exception:
            pass
        # R20: explicit requested vs effective so CA never confuses them
        _eff = str(Path(project_path).resolve()).replace("\\", "/")
        _req = str(Path(original_project_path).resolve()).replace("\\", "/") if original_project_path else _eff
        _mx_summary = (_mx or {}).get("summary") or {}
        resp = {"ok": True, "session_id": sid, "session_title": session_title,
                "started_at": now, "resumed": False,
                "agent_id": agent_id,
                "project_name": Path(project_path).resolve().name,
                "project_path": norm,
                "requested_project_path": _req,
                "effective_project_path": _eff,
                "relative_base_hint": _eff,
                "project_path_confirmed": project_path_confirmed,
                "root_exists": Path(project_path).exists(),
                "path_resolution_note": resolution_note,
                "resolved_from": resolved_from,
                "matrix_audit": _mx_summary,
                "matrix_drift_present": bool((_mx or {}).get("drift")),
                "progressive_gap_count": _mx_summary.get("progressive_gap_count", 0),
                "progressive_gaps_hint": (
                    "Call progressive_matrix_check for gap detail"
                    if _mx_summary.get("progressive_gap_count") else
                    "no progressive gaps"
                ),
                # R43 P1 #1: project_switch_context — multi-project anchor
                "project_switch_context": _r43_project_switch_context(norm),
                # V15 R46 — machine-readable transport policy (new session branch)
                "transport_policy": _transport_policy_payload(),
                "cslt_policy": __import__("orchestrator.core.ca_policy_matrix", fromlist=["open_session_policy_payload"]).open_session_policy_payload(agent_id=agent_id, project_path=project_path),
                }
        resp.update(_open_session_mode_fields(requested_mode, canon_mode, session_row, created=True))
        if "closed_session_id" in session_row:
            resp["closed_session_id"] = session_row["closed_session_id"]
        # V15 F4 (D4) — severity-aware drift verbosity (shared decision fn)
        _eff_verbosity = _open_session_drift_decision(resp, _mx, verbosity)
        try:
            import tools_v15 as _v15
            resp = _v15._apply_verbosity(resp, _eff_verbosity, "session_open")
        except Exception:
            pass
        return json.dumps(resp, ensure_ascii=False)
    except Exception as e:
        return _json_error(e)


async def close_session(project_path: str = "", session_id: str = "",
                        agent_id: str = "") -> str:
    """Close the active session and summarize counts by action_type.

    V15 R64-D: session_id now accepted as an alternative to project_path. When
    `session_id` is provided, look it up directly in the sessions table —
    saves CA from having to remember which project_path opened a sub-session
    (e.g. v4 had Rawcut/[Ac] + GetLink/[Ad] sessions plus the main [Ab] one,
    and close_session(session_id=...) previously returned ok:false because the
    signature required project_path).
    """
    try:
        # R64-D: session_id direct lookup path
        if session_id and not project_path:
            # Anh 2026-08-11 (Phase A on-loop audit): the whole session_close block
            # below interleaves _log_session_event (durable write via SessionWriter,
            # blocks on a commit Future) + _run_session_write + 10+ _with_sessions_db
            # reads. Calling it on the event loop pins the loop for the entire
            # critical section. Run it OFF the loop in one to_thread so the
            # durability/ordering semantics of the read→write→read sequence are
            # preserved (single thread, single connection scope) without stalling
            # the asyncio loop.
            def _close_session_by_id():
                with _with_sessions_db() as conn:
                    if agent_id:
                        row = conn.execute(
                            "SELECT session_id, started_at, project_path FROM sessions "
                            "WHERE session_id=? AND status='active' AND agent_id=? LIMIT 1",
                            (session_id, agent_id),
                        ).fetchone()
                    else:
                        row = conn.execute(
                            "SELECT session_id, started_at, project_path FROM sessions WHERE session_id=? AND status='active' LIMIT 1",
                            (session_id,),
                        ).fetchone()
                    if not row:
                        return None
                    sid = row[0]
                    started_at = row[1]
                    norm = row[2]
                    now = datetime.now().isoformat(timespec="seconds")
                    _log_session_event(sid, "close_session", {"session_id": session_id}, {"ok": True}, 0, "ok", "")
                    _run_session_write(
                        "close session",
                        lambda write_conn: write_conn.execute(
                            "UPDATE sessions SET status='closed', ended_at=? WHERE session_id=?",
                            (now, sid),
                        ),
                    )
                    counts = dict(conn.execute(
                        "SELECT action_type, COUNT(*) FROM events WHERE session_id=? GROUP BY action_type", (sid,)
                    ).fetchall())
                    ok_cnt  = conn.execute("SELECT COUNT(*) FROM events WHERE session_id=? AND status='ok'", (sid,)).fetchone()[0]
                    err_cnt = conn.execute("SELECT COUNT(*) FROM events WHERE session_id=? AND status='error'", (sid,)).fetchone()[0]
                    total   = conn.execute("SELECT COUNT(*) FROM events WHERE session_id=?", (sid,)).fetchone()[0]
                    native_fallback_count = conn.execute(
                        "SELECT COUNT(*) FROM events WHERE session_id=? AND tool_origin='native'", (sid,)
                    ).fetchone()[0]
                    if agent_id:
                        pending_audit_count = conn.execute(
                            "SELECT COUNT(*) FROM pending_audits WHERE project_path=? "
                            "AND sync_status='pending' AND agent_id=?",
                            (norm, agent_id),
                        ).fetchone()[0]
                    else:
                        pending_audit_count = conn.execute(
                            "SELECT COUNT(*) FROM pending_audits WHERE project_path=? AND sync_status='pending'",
                            (norm,),
                        ).fetchone()[0]
                    try:
                        duration_minutes = round((datetime.fromisoformat(now) - datetime.fromisoformat(started_at)).total_seconds() / 60, 2)
                    except Exception:
                        duration_minutes = None
                return {"ok": True, "session_id": sid, "ended_at": now,
                        "duration_minutes": duration_minutes,
                        "native_fallback_count": native_fallback_count,
                        "pending_audit_count": pending_audit_count,
                        "resolved_via": "session_id_direct",
                        "summary": {**counts, "success": ok_cnt, "error": err_cnt, "total": total,
                                    "native_fallback_count": native_fallback_count,
                                    "pending_audit_count": pending_audit_count}}
            _closed = await asyncio.to_thread(_close_session_by_id)
            if _closed is None:
                return _json_error(
                    f"No active session with session_id={session_id}",
                    error_name="SESSION_NOT_FOUND",
                    hint="Use list_sessions to see active sessions and their session_id values.",
                    ca_next_action="list_sessions",
                )
            return json.dumps(_closed, ensure_ascii=False)
        # Default path: resolve by project_path
        if not project_path:
            return _json_error(
                "Either project_path or session_id required",
                error_name="MISSING_REQUIRED_FIELD",
                hint="Pass project_path (preferred for primary [Ab] session) or session_id (use list_sessions to find).",
                ca_next_action="provide_project_path_or_session_id",
            )
        # Q5 fix: resolve same way as open_session so subdir / variant paths
        # map back to the registered project root.
        resolution = _resolve_project_path_payload(project_path, mode="folder")
        resolved_path = resolution.get("project_path") or project_path
        norm = _session_project_key(resolved_path)
        # R43.3 C6: ctx manager — previously 8+ conn.execute() calls between
        # connect (was line 9591) and close (was line 9636); any failure mid-stream leaked conn.
        # Anh 2026-08-11 (Phase A on-loop audit): same wrap as the session_id branch
        # above — durable _log_session_event + _run_session_write + many
        # _with_sessions_db reads all live inside this critical section; run it
        # OFF the loop in one to_thread so ordering/durability are preserved.
        def _close_session_by_project():
            nonlocal norm
            with _with_sessions_db() as conn:
                if agent_id:
                    row = conn.execute(
                        "SELECT session_id, started_at FROM sessions WHERE project_path=? "
                        "AND status='active' AND agent_id=? ORDER BY started_at DESC LIMIT 1",
                        (norm, agent_id),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT session_id, started_at FROM sessions WHERE project_path=? AND status='active' ORDER BY started_at DESC LIMIT 1",
                        (norm,),
                    ).fetchone()
                if not row:
                    # Fallback: try the raw input as a session key (no resolution) for older sessions
                    raw_norm = _session_project_key(project_path)
                    if raw_norm != norm:
                        if agent_id:
                            row = conn.execute(
                                "SELECT session_id, started_at FROM sessions WHERE project_path=? "
                                "AND status='active' AND agent_id=? ORDER BY started_at DESC LIMIT 1",
                                (raw_norm, agent_id),
                            ).fetchone()
                        else:
                            row = conn.execute(
                                "SELECT session_id, started_at FROM sessions WHERE project_path=? AND status='active' ORDER BY started_at DESC LIMIT 1",
                                (raw_norm,),
                            ).fetchone()
                        if row:
                            norm = raw_norm
                if not row:
                    return None
                sid = row[0]
                started_at = row[1]
                now = datetime.now().isoformat(timespec="seconds")
                _log_session_event(sid, "close_session", {"project_path": project_path}, {"ok": True}, 0, "ok", "")
                _run_session_write(
                    "close session",
                    lambda write_conn: write_conn.execute(
                        "UPDATE sessions SET status='closed', ended_at=? WHERE session_id=?",
                        (now, sid),
                    ),
                )
                counts = dict(conn.execute(
                    "SELECT action_type, COUNT(*) FROM events WHERE session_id=? GROUP BY action_type", (sid,)
                ).fetchall())
                ok_cnt  = conn.execute("SELECT COUNT(*) FROM events WHERE session_id=? AND status='ok'", (sid,)).fetchone()[0]
                err_cnt = conn.execute("SELECT COUNT(*) FROM events WHERE session_id=? AND status='error'", (sid,)).fetchone()[0]
                total   = conn.execute("SELECT COUNT(*) FROM events WHERE session_id=?", (sid,)).fetchone()[0]
                native_fallback_count = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE session_id=? AND tool_origin='native'", (sid,)
                ).fetchone()[0]
                pending_audit_count = conn.execute(
                    "SELECT COUNT(*) FROM pending_audits WHERE project_path=? AND sync_status='pending'", (norm,)
                ).fetchone()[0]
                try:
                    duration_minutes = round((datetime.fromisoformat(now) - datetime.fromisoformat(started_at)).total_seconds() / 60, 2)
                except Exception:
                    duration_minutes = None
            return {"ok": True, "session_id": sid, "ended_at": now,
                    "duration_minutes": duration_minutes,
                    "native_fallback_count": native_fallback_count,
                    "pending_audit_count": pending_audit_count,
                    "summary": {**counts, "success": ok_cnt, "error": err_cnt, "total": total,
                                "native_fallback_count": native_fallback_count,
                                "pending_audit_count": pending_audit_count}}
        _closed = await asyncio.to_thread(_close_session_by_project)
        if _closed is None:
            return _json_error(
                "No active session found for this project.",
                error_name="SESSION_NOT_FOUND",
                hint="If you opened the session under a different path, pass that path. Use list_sessions to see active sessions.",
                ca_next_action="list_sessions",
            )
        return json.dumps(_closed, ensure_ascii=False)
    except _session_store_module.SessionStoreContentionError as e:
        # A33: _log_session_event now propagates contention instead of returning
        # None, which is right -- but it reaches CA through this generic handler,
        # and _json_error defaults to retryable=False. Telling CA "not retryable"
        # about a transient write-queue stall makes it give up, leaving the
        # session stuck 'active' forever (the UPDATE never ran). Contention is
        # the one error here that a plain retry actually fixes.
        return _json_error(
            str(e),
            error_name="SESSION_STORE_CONTENTION",
            retryable=True,
            ca_next_action="retry_close_session_after_short_backoff",
            hint="The session write queue was busy; the session is still active. Retry.",
        )
    except Exception as e:
        return _json_error(e)


async def rename_session(project_path: str, new_title: str,
                         agent_id: str = "") -> str:
    """Rename this agent's active session. Empty agent_id preserves legacy lookup."""
    try:
        norm = _session_project_key(project_path)
        # Anh 2026-08-11 (Phase A on-loop audit): the read (find active session) and
        # the durable _run_session_write both block the event loop when called
        # synchronously. Run the read→write sequence OFF the loop in one to_thread
        # so ordering (read-then-write) is preserved without stalling the loop.
        def _do_rename():
            with _with_sessions_db() as conn:
                if agent_id:
                    row = conn.execute(
                        "SELECT session_id FROM sessions WHERE project_path=? AND status='active' "
                        "AND agent_id=? ORDER BY started_at DESC LIMIT 1",
                        (norm, agent_id),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT session_id FROM sessions WHERE project_path=? AND status='active' ORDER BY started_at DESC LIMIT 1",
                        (norm,),
                    ).fetchone()
            if not row:
                return None
            renamed = _run_session_write(
                "rename session",
                lambda write_conn: _session_store_module._rename_session_title(
                    write_conn, row[0], new_title),
            )
            return row[0] if renamed is not None else None
        _sid = await asyncio.to_thread(_do_rename)
        if _sid is None:
            # PC1-SESSION (2026-08-11): contract is "Closed sessions cannot be
            # renamed". Return a fail-loud, machine-actionable error so callers
            # know to open_session instead of silently renaming an implicit
            # auto-opened "untitled-session-...". The server gate normally
            # short-circuits this with ACTIVE_SESSION_REQUIRED; this branch is
            # the direct-call safety net.
            return _json_error(
                "No active session found; closed/absent sessions cannot be renamed.",
                error_name="SESSION_CLOSED",
                hint="Open a session for this project first (open_session), then retry rename_session.",
                ca_next_action="open_session",
            )
        return json.dumps({"ok": True, "session_id": _sid, "new_title": new_title}, ensure_ascii=False)
    except Exception as e:
        return _json_error(e)


async def log_native_event(action_type: str = "", tool_name: str = "", target: str = "",
                            duration_ms: int = 0, status: str = "ok",
                            reason: str = "", project_path: str = "",
                            instead_of: str = "", failed_payload: dict = None,
                            failed_error: str = "", native_command: str = "",
                            scope: str = "", risk: str = "",
                            transport: str = "native",
                            native: list = None,
                            agent_id: str = "") -> str:
    """Write one or many native fallback events to the active session.

    V15 R8 A.6: when `native` is a non-empty list of event dicts, insert all of them
    in a batch (each dict accepts the same keys as the single-event signature).
    Single-event mode (legacy) when `native` is None/empty — keeps backward compat.

    R81: `transport` distinguishes a TRUE native fallback ("native", default — an op
    that bypassed [A], e.g. raw fs/spawn when [A] was unavailable) from a "via_a"
    transport (the work WAS done through an [A] tool such as write_new_file /
    run_project_test_script — logged only for traceability, NOT an audit-boundary
    breach). via_a events are tagged "via_A" and are NOT counted as native fallbacks.
    """
    # V15 R8 A.6: batch mode
    if native:
        if not isinstance(native, list):
            return _json_error("native must be a list of event dicts",
                               error_name="UNSUPPORTED_FIELD")
        results: list = []
        for ev in native:
            if not isinstance(ev, dict):
                results.append({"ok": False, "error": "non-dict event skipped"})
                continue
            sub_args = dict(ev)
            if agent_id and not sub_args.get("agent_id"):
                sub_args["agent_id"] = agent_id
            sub = await log_native_event(**sub_args)
            try:
                results.append(json.loads(sub))
            except Exception:
                results.append({"ok": False, "raw": sub})
        ok_count = sum(1 for r in results if r.get("ok"))
        return json.dumps({
            "ok": ok_count == len(results),
            "batch": True,
            "count": len(results),
            "ok_count": ok_count,
            "results": results,
        }, ensure_ascii=False)
    try:
        if not project_path:
            return _json_error("project_path required for log_native_event",
                               error_name="MISSING_REQUIRED_FIELD")
        audit_parts = []
        for key, value in [
            ("instead_of", instead_of),
            ("failed_payload", failed_payload or {}),
            ("failed_error", failed_error),
            ("native_command", native_command),
            ("scope", scope),
            ("risk", risk),
        ]:
            if value not in ("", {}, None):
                audit_parts.append(f"{key}={json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value}")
        if audit_parts:
            reason = "; ".join([part for part in [reason, *audit_parts] if part])
        norm = _session_project_key(project_path)
        # Anh 2026-08-11 (Phase A on-loop audit): _get_active_session_id is a
        # SQLite read and _auto_open_session goes through the SessionWriter
        # (durable write, blocks on a commit Future). Both pin the event loop
        # when called synchronously. Resolve the session OFF the loop in one
        # to_thread so the read-then-open ordering is preserved.
        def _resolve_or_open_session():
            _sid_inner = _get_active_session_id(norm, agent_id or None)
            if not _sid_inner:
                _sid_inner = _auto_open_session(
                    norm, {"agent_id": agent_id} if agent_id else {},
                    agent_id=agent_id or None,
                )
            return _sid_inner
        sid = await asyncio.to_thread(_resolve_or_open_session)
        if not sid:
            return _json_error("Could not find or create session.")
        tool_icon = TOOL_ACTION_MAP.get(tool_name, ("🔗", "workflow", "🔧"))[0]
        # R81: transport tag — "via_A" when the work went through an [A] tool (not a
        # native bypass). tool_origin stays distinguishable so audit/fallback counts
        # don't conflate transport-logging with real native fallback.
        _transport = (transport or "native").strip().lower()
        if _transport not in ("native", "via_a"):
            _transport = "native"
        _tag = "via_A" if _transport == "via_a" else "native"
        _origin = "via_a" if _transport == "via_a" else "native"
        # CSLt axis 4/5: encode Matrix classification into reason so metrics
        # can aggregate forbidden_native / allowed_native / via_a / unclassified.
        _cls = None
        try:
            from orchestrator.core.ca_policy_matrix import (
                classify_action as _cslt_classify,
            )
            from orchestrator.core.ca_policy_matrix import (
                encode_classification_reason as _cslt_encode,
            )
            _cls = _cslt_classify(
                tool_name=tool_name,
                action_type=action_type,
                transport=_transport,
                scope=scope,
                target=target,
                project_path=project_path,
            )
            reason = _cslt_encode(reason, _cls)
        except Exception:
            _cls = None
        if target:
            natural = f"{tool_icon} {tool_name} ({_tag}) | 🔧 {target} | ⏱️ {duration_ms}ms"
        else:
            natural = f"{tool_icon} {tool_name} ({_tag}) | ⏱️ {duration_ms}ms"
        now = datetime.now().isoformat(timespec="seconds")
        def _write_native_event(write_conn):
            write_conn.execute(
                """INSERT INTO events
                   (session_id, tool_name, tool_origin, action_type, status,
                    target, duration_ms, reason, natural_log_line, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (sid, tool_name, _origin, action_type, status,
                 target, duration_ms, reason[:300], natural, now)
            )
            return write_conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id=? AND tool_origin='native'",
                (sid,),
            ).fetchone()[0]

        # Anh 2026-08-11 (Phase A on-loop audit): _run_session_write goes through
        # the SessionWriter and blocks on a durable-commit Future; calling it
        # synchronously here pinned the event loop. Run the write OFF the loop.
        fallback_count_so_far = await asyncio.to_thread(
            _run_session_write, "log native event", _write_native_event)
        out = {"ok": True, "session_id": sid, "logged": natural,
               "fallback_count_so_far": fallback_count_so_far,
               "transport": _transport, "audit_status": "logged"}
        if _cls:
            out["cslt_classification"] = {
                "task_class": _cls.get("task_class"),
                "native_policy": _cls.get("native_policy"),
                "is_forbidden_native": _cls.get("is_forbidden_native"),
                "is_allowed_native": _cls.get("is_allowed_native"),
                "is_via_a": _cls.get("is_via_a"),
                "is_unclassified": _cls.get("is_unclassified"),
                "host_gate": (_cls.get("host_gate") or {}).get("decision"),
                "ca_next_action": _cls.get("ca_next_action"),
            }
            # CSLt Axis 4 — Correctness: FORBID native is HARD reject after ledger write.
            # Event is still recorded so metrics/bypass_rate remain truthful.
            if _cls.get("is_forbidden_native"):
                preferred = (_cls.get("preferred_via_a") or [])[:5]
                next_act = _cls.get("ca_next_action") or "use_preferred_via_a_tools"
                warn = (
                    f"FORBIDDEN native for task_class={_cls.get('task_class')}; "
                    f"preferred_via_a={preferred}; next={next_act}"
                )
                out["policy_warning"] = warn
                out["ok"] = False
                out["error"] = warn
                out["error_name"] = "FORBIDDEN_NATIVE"
                out["type"] = "POLICY_REJECT"
                out["severity"] = "error"
                out["retryable"] = False
                out["audit_status"] = "logged_and_rejected"
                out["ca_next_action"] = next_act
                out["preferred_via_a"] = preferred
                # host-gate deny is also a hard reject surface (Hermes axis 3)
                hg = (_cls.get("host_gate") or {})
                if isinstance(hg, dict) and str(hg.get("decision") or "").startswith("deny"):
                    out["error_name"] = "HOST_GATE_DENY"
                    out["type"] = "POLICY_REJECT"
            else:
                hg = (_cls.get("host_gate") or {})
                if isinstance(hg, dict) and str(hg.get("decision") or "").startswith("deny"):
                    preferred = (_cls.get("preferred_via_a") or [])[:5]
                    next_act = _cls.get("ca_next_action") or "stay_inside_project_or_use_a"
                    warn = (
                        f"HOST GATE DENY decision={hg.get('decision')}; "
                        f"task_class={_cls.get('task_class')}; next={next_act}"
                    )
                    out["policy_warning"] = warn
                    out["ok"] = False
                    out["error"] = warn
                    out["error_name"] = "HOST_GATE_DENY"
                    out["type"] = "POLICY_REJECT"
                    out["severity"] = "error"
                    out["retryable"] = False
                    out["audit_status"] = "logged_and_rejected"
                    out["ca_next_action"] = next_act
                    out["preferred_via_a"] = preferred
        # R83: if the logged tool_name is an actual [A] tool but transport defaulted to
        # 'native', the audit line reads "<tool> (native)" which wrongly implies a native
        # fallback/bypass. Nudge CA to classify it correctly. (Most [A]-tool work needs NO
        # log_native_event at all — it is already audited by the tool itself.)
        if _transport == "native" and tool_name in TOOL_MAP:
            out["transport_warning"] = (
                f"'{tool_name}' is an [A] tool, not a native bypass. If the work was done THROUGH [A], "
                f"either omit log_native_event (the tool self-audits) or pass transport='via_a' so the "
                f"audit reads 'via_A' instead of 'native'. Only log transport='native' for a TRUE bypass "
                f"(raw fs/spawn used because [A] was unavailable).")
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        return _json_error(e)


async def create_pending_audit(project_path: str, action_type: str, tool_name: str,
                               instead_of: str = "", failed_error: str = "",
                               target: str = "", reason: str = "",
                               agent_id: str = "") -> str:
    """Persist a pending native fallback audit entry without raw secrets or file contents."""
    try:
        # Anh 2026-08-11 (Phase A on-loop audit): _init_session_db (SQLite schema
        # init) and _run_session_write (SessionWriter durable write, blocks on a
        # commit Future) both pin the event loop when called synchronously. Run
        # the init→sanitize→write sequence OFF the loop in one to_thread so
        # ordering is preserved.
        def _init_and_insert():
            _init_session_db()
            norm_local = _session_project_key(project_path)
            pending_id_local = "pa-" + uuid.uuid4().hex[:16]
            now_local = datetime.now().isoformat(timespec="seconds")
            clean_local = {
                "action_type": _sanitize_audit_value(action_type, 80),
                "tool_name": _sanitize_audit_value(tool_name, 120),
                "instead_of": _sanitize_audit_value(instead_of, 120),
                "failed_error": _sanitize_audit_value(failed_error, 500),
                "target": _sanitize_audit_value(target, 240),
                "reason": _sanitize_audit_value(reason, 300),
            }
            _run_session_write(
                "create pending audit",
                lambda write_conn: write_conn.execute(
                    """INSERT INTO pending_audits
                       (pending_audit_id, project_path, action_type, tool_name, instead_of,
                        failed_error, target, reason, sync_status, created_at, agent_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (pending_id_local, norm_local, clean_local["action_type"], clean_local["tool_name"], clean_local["instead_of"],
                     clean_local["failed_error"], clean_local["target"], clean_local["reason"], "pending", now_local,
                     agent_id or None),
                ),
            )
            return pending_id_local, norm_local, now_local
        pending_id, norm, now = await asyncio.to_thread(_init_and_insert)
        return json.dumps({
            "ok": True,
            "pending_audit_id": pending_id,
            "sync_status": "pending",
            "project_path": norm,
            "created_at": now,
            "ca_next_action": "sync_pending_audit_when_api_available",
        }, ensure_ascii=False)
    except Exception as e:
        return _json_error(e)


async def sync_pending_audit(project_path: str, pending_audit_id: str = "", limit: int = 50,
                             agent_id: str = "", target_session_id: str = "",
                             include_legacy: bool = False) -> str:
    """Sync scoped pending audit rows; legacy rows require explicit opt-in and target."""
    try:
        # Anh 2026-08-11 (Phase A on-loop audit): _init_session_db pins the loop
        # when called synchronously. Run it OFF the loop.
        await asyncio.to_thread(_init_session_db)
        norm = _session_project_key(project_path)
        sid = ""
        session_agent = ""
        if target_session_id:
            # Anh 2026-08-11 (Phase A on-loop audit): _with_sessions_db read below
            # pins the loop when called synchronously. Run it OFF the loop.
            def _lookup_session_row():
                with _with_sessions_db() as conn:
                    return conn.execute(
                        "SELECT project_path, agent_id FROM sessions WHERE session_id=?",
                        (target_session_id,),
                    ).fetchone()
            session_row = await asyncio.to_thread(_lookup_session_row)
            if not session_row:
                return _json_error(
                    f"Session not found: {target_session_id}",
                    error_name="SESSION_NOT_FOUND",
                    ca_next_action="list_sessions",
                )
            if _session_project_key(session_row[0] or "") != norm:
                return _json_error(
                    "target_session_id belongs to a different project",
                    error_name="SESSION_PROJECT_MISMATCH",
                    ca_next_action="pass_matching_target_session_id",
                )
            sid = target_session_id
            session_agent = str(session_row[1] or "")
            if agent_id and agent_id != session_agent:
                return _json_error(
                    "agent_id does not match target_session_id owner",
                    error_name="SESSION_AGENT_MISMATCH",
                    target_session_id=target_session_id,
                    target_agent_id=session_agent or None,
                    ca_next_action="pass_matching_agent_id_or_target_session_id",
                )
        else:
            # Anh 2026-08-11 (Phase A on-loop audit): _get_active_session_id is a
            # SQLite read; run it OFF the loop.
            sid = await asyncio.to_thread(_get_active_session_id, norm, agent_id or None)
            session_agent = agent_id or ""
        if not sid:
            # Anh 2026-08-11 (Phase A on-loop audit): _auto_open_session goes
            # through the SessionWriter and blocks on a durable-commit Future;
            # run it OFF the loop.
            sid = await asyncio.to_thread(
                _auto_open_session,
                norm, {"agent_id": agent_id} if agent_id else {},
                agent_id=agent_id or None,
            )
            session_agent = agent_id or ""
        if not sid:
            return _json_error(
                "Could not find or create session.",
                error_name="SESSION_UNAVAILABLE",
                ca_next_action="open_session_then_retry",
            )
        effective_agent = agent_id or session_agent
        if include_legacy and not target_session_id:
            return _json_error(
                "include_legacy=true requires target_session_id",
                error_name="LEGACY_AUDIT_TARGET_REQUIRED",
                ca_next_action="create_dedicated_session_then_retry",
            )
        if not effective_agent and not include_legacy:
            return _json_error(
                "Unscoped pending audits require target_session_id and include_legacy=true",
                error_name="AUDIT_AGENT_SCOPE_REQUIRED",
                target_session_id=target_session_id or None,
                ca_next_action="pass_agent_id_or_explicitly_opt_in_to_legacy",
            )
        # R43.3 C5: ctx manager guarantees conn.close() even if SELECT/INSERT raises
        synced = []
        failed = []
        now = datetime.now().isoformat(timespec="seconds")
        def _sync_pending(write_conn):
            if include_legacy and effective_agent:
                _agent_clause = " AND (agent_id=? OR agent_id IS NULL)"
                _agent_params = (effective_agent,)
            elif include_legacy:
                _agent_clause = " AND agent_id IS NULL"
                _agent_params = ()
            elif effective_agent:
                _agent_clause = " AND agent_id=?"
                _agent_params = (effective_agent,)
            else:
                # Reached only through the explicit legacy opt-in above.
                _agent_clause = " AND agent_id IS NULL"
                _agent_params = ()
            if pending_audit_id:
                rows = write_conn.execute(
                    f"""SELECT pending_audit_id, action_type, tool_name, instead_of,
                               failed_error, target, reason
                        FROM pending_audits
                        WHERE project_path=? AND pending_audit_id=? AND sync_status='pending'
                        {_agent_clause}
                        ORDER BY created_at ASC LIMIT ?""",
                    (norm, pending_audit_id, *_agent_params, limit),
                ).fetchall()
            else:
                rows = write_conn.execute(
                    f"""SELECT pending_audit_id, action_type, tool_name, instead_of,
                               failed_error, target, reason
                        FROM pending_audits
                        WHERE project_path=? AND sync_status='pending'
                        {_agent_clause}
                        ORDER BY created_at ASC LIMIT ?""",
                    (norm, *_agent_params, limit),
                ).fetchall()
            for row in rows:
                pid, action_type, tool_name, instead_of, failed_error, target, reason = row
                try:
                    audit_reason = "; ".join(part for part in [
                        reason or "pending native fallback audit",
                        f"instead_of={instead_of}" if instead_of else "",
                        f"failed_error={failed_error}" if failed_error else "",
                        f"pending_audit_id={pid}",
                    ] if part)
                    natural = f"🔗 {tool_name} (native pending sync) | 🔧 {target or ''} | ⏱️ 0ms"
                    write_conn.execute(
                        """INSERT INTO events
                           (session_id, tool_name, tool_origin, action_type, status,
                             target, duration_ms, reason, natural_log_line, created_at, agent_id)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (sid, tool_name, "native", action_type, "ok",
                         target or "", 0, audit_reason[:300], natural, now,
                         effective_agent or None),
                    )
                    write_conn.execute(
                        "UPDATE pending_audits SET sync_status='synced', session_id=?, synced_at=? WHERE pending_audit_id=?",
                        (sid, now, pid),
                    )
                    synced.append(pid)
                except Exception as exc:
                    failed.append({"pending_audit_id": pid, "error": str(exc)})
                    write_conn.execute(
                        "UPDATE pending_audits SET sync_status='failed', session_id=?, synced_at=? WHERE pending_audit_id=?",
                        (sid, now, pid),
                    )
            if effective_agent:
                remaining = write_conn.execute(
                    "SELECT COUNT(*) FROM pending_audits WHERE project_path=? "
                    "AND sync_status='pending' AND agent_id=?",
                    (norm, effective_agent),
                ).fetchone()[0]
            else:
                remaining = write_conn.execute(
                    "SELECT COUNT(*) FROM pending_audits WHERE project_path=? "
                    "AND sync_status='pending' AND agent_id IS NULL",
                    (norm,),
                ).fetchone()[0]
            legacy_remaining = write_conn.execute(
                "SELECT COUNT(*) FROM pending_audits WHERE project_path=? "
                "AND sync_status='pending' AND agent_id IS NULL",
                (norm,),
            ).fetchone()[0]
            project_remaining = write_conn.execute(
                "SELECT COUNT(*) FROM pending_audits WHERE project_path=? AND sync_status='pending'",
                (norm,),
            ).fetchone()[0]
            native_count = write_conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id=? AND tool_origin='native'", (sid,)
            ).fetchone()[0]
            return (remaining, legacy_remaining, project_remaining, native_count)

        # Anh 2026-08-11 (Phase A on-loop audit): the durable sync transaction
        # blocks on a commit Future; run it OFF the loop.
        remaining, legacy_remaining, project_remaining, native_count = await asyncio.to_thread(
            _run_session_write, "sync pending audit", _sync_pending)
        return json.dumps({
            "ok": len(failed) == 0,
            "session_id": sid,
            "synced": synced,
            "failed": failed,
            "pending_remaining": remaining,
            "legacy_pending_remaining": legacy_remaining,
            "project_pending_remaining": project_remaining,
            "include_legacy": bool(include_legacy),
            "fallback_count_so_far": native_count,
            "audit_status": "synced" if synced and not failed else ("failed" if failed else "nothing_to_sync"),
            "ca_next_action": "continue_session_audit" if not failed else "review_failed_pending_audit",
        }, ensure_ascii=False)
    except Exception as e:
        return _json_error(e)


async def list_sessions(project_path: str = "", limit: int = 10,
                        scope: str = "project") -> str:
    """Return recent sessions without events, plus per-session summaries.

    Commit E (S3-CONTRACT-01): rows are filtered by the canonical project key
    resolved at the entry gate, BEFORE ordering/pagination/limit, so a
    cross-project session row can never leak through the response. Sessions
    whose stored project_path has no trustworthy canonical identity are
    excluded (exact-equality predicate, no prefix/substring)."""
    try:
        # Commit E: scope resolved via shared helper so a zero-payload call
        # can never silently downgrade to CWD/global data.
        norm, _scope_err = _resolve_session_query_scope(
            "list_sessions", project_path, scope)
        if _scope_err:
            return _scope_err
        # Anh 2026-08-11 (Phase A on-loop audit): the SQLite read + per-session
        # aggregation loop pins the event loop when called synchronously. Run the
        # whole read OFF the loop in one to_thread (read-only; no durability or
        # ordering change).
        def _list_sessions():
            conn = sqlite3.connect(str(_SESSIONS_DB))
            try:
                rows = conn.execute(
                    "SELECT session_id, session_title, mode, status, coverage, started_at, ended_at, agent_id "
                    "FROM sessions WHERE project_path=? ORDER BY started_at DESC LIMIT ?",
                    (norm, limit)
                ).fetchall()
                result = []
                for sid, title, mode, status, coverage, started_at, ended_at, session_agent in rows:
                    counts = dict(conn.execute(
                        "SELECT action_type, COUNT(*) FROM events WHERE session_id=? GROUP BY action_type", (sid,)
                    ).fetchall())
                    total = conn.execute("SELECT COUNT(*) FROM events WHERE session_id=?", (sid,)).fetchone()[0]
                    result.append({"session_id": sid, "session_title": title, "mode": mode,
                                   "status": status, "coverage": coverage,
                                   "agent_id": session_agent,
                                   "started_at": started_at, "ended_at": ended_at,
                                   "counts": counts, "total_events": total})
                return result
            finally:
                conn.close()
        result = await asyncio.to_thread(_list_sessions)
        return json.dumps({
            "ok": True,
            "scope": "project",
            "project_path": norm,
            "sessions": result,
            "total": len(result),
        }, ensure_ascii=False)
    except Exception as e:
        return _json_error(e)


async def clear_closed_sessions(project_path: str) -> str:
    """Delete closed sessions and their events for one project."""
    try:
        if not project_path:
            return _json_error("project_path required")
        norm = _session_project_key(project_path)
        def _clear_closed(write_conn):
            rows = write_conn.execute(
                "SELECT session_id FROM sessions WHERE project_path=? AND status='closed'",
                (norm,),
            ).fetchall()
            session_ids = [row[0] for row in rows]
            deleted_events = 0
            for sid in session_ids:
                cur = write_conn.execute("DELETE FROM events WHERE session_id=?", (sid,))
                deleted_events += cur.rowcount if cur.rowcount is not None else 0
            write_conn.execute(
                "DELETE FROM sessions WHERE project_path=? AND status='closed'", (norm,))
            return session_ids, deleted_events

        # Anh 2026-08-11 (Phase A on-loop audit): the DELETE loop blocks on a
        # durable commit Future; run it OFF the loop.
        session_ids, deleted_events = await asyncio.to_thread(
            _run_session_write, "clear closed sessions", _clear_closed)
        return json.dumps({
            "ok": True,
            "project_path": norm,
            "deleted_sessions": len(session_ids),
            "deleted_events": deleted_events,
        }, ensure_ascii=False)
    except Exception as e:
        return _json_error(e)


async def get_session_events(session_id: str = "", limit: int = 50, offset: int = 0,
                              project_path: str = "", target_session_id: str = "",
                              agent_id: str = "") -> str:
    # CAN-054: target_session_id is the unambiguous domain parameter — it wins
    # over session_id (which doubles as host transport metadata).
    if target_session_id:
        session_id = target_session_id
    # Convenience: caller may know only project_path → resolve active session
    if not session_id and project_path:
        try:
            # Anh 2026-08-11 (Phase A on-loop audit): _get_active_session_id is a
            # SQLite read; run it OFF the loop.
            sid = await asyncio.to_thread(
                _get_active_session_id, _session_project_key(project_path), agent_id or None)
            if sid:
                session_id = sid
        except Exception:
            pass
    if not session_id:
        return json.dumps({"ok": False,
                           "error": "session_id required (or project_path with active session)",
                           "error_name": "SESSION_NOT_FOUND",
                           "hint": "Pass session_id from list_sessions, or project_path when an active session exists.",
                           "ca_next_action": "list_sessions"},
                          ensure_ascii=False)
    """Return paginated events for one session."""
    try:
        # Anh 2026-08-11 (Phase A on-loop audit): the two SELECTs + COUNT pin the
        # event loop when called synchronously. Run the whole read OFF the loop
        # in one to_thread (read-only; no durability or ordering change).
        def _read_session_events():
            conn = sqlite3.connect(str(_SESSIONS_DB))
            try:
                sess = conn.execute(
                    "SELECT session_title, project_path, status, started_at, ended_at, agent_id "
                    "FROM sessions WHERE session_id=?",
                    (session_id,)
                ).fetchone()
                if not sess:
                    return None, None, None
                rows = conn.execute(
                    """SELECT id, tool_name, tool_origin, action_type, status, target,
                               range_label, duration_ms, reason, natural_log_line, created_at, outcome,
                               queue_wait_ms, host_session_id, task_epoch, prompt_hash, agent_id,
                               event_kind, resolves_event_id, resolution_kind, error_owner,
                               error_name, correlation_type, correlation_key, evidence_json
                       FROM events WHERE session_id=? ORDER BY id ASC LIMIT ? OFFSET ?""",
                    (session_id, limit, offset)
                ).fetchall()
                total = conn.execute("SELECT COUNT(*) FROM events WHERE session_id=?", (session_id,)).fetchone()[0]
                return sess, rows, total
            finally:
                conn.close()
        sess, rows, total = await asyncio.to_thread(_read_session_events)
        if sess is None:
            return json.dumps({"ok": False,
                               "error": f"Session not found: {session_id}",
                               "error_name": "SESSION_NOT_FOUND",
                               "hint": "Call list_sessions(project_path) and retry with a valid session_id.",
                               "ca_next_action": "list_sessions"},
                              ensure_ascii=False)
        keys = ["id", "tool_name", "tool_origin", "action_type", "status", "target",
                "range_label", "duration_ms", "reason", "natural_log_line", "created_at", "outcome",
                "queue_wait_ms", "host_session_id", "task_epoch", "prompt_hash", "agent_id",
                "event_kind", "resolves_event_id", "resolution_kind", "error_owner",
                "error_name", "correlation_type", "correlation_key", "evidence_json"]
        events = [dict(zip(keys, row)) for row in rows]
        # Build summary badges
        summary: dict = {}
        for ev in events:
            at = ev.get("action_type") or "other"
            summary[at] = summary.get(at, 0) + 1
        return json.dumps({"ok": True, "session_id": session_id,
                           "session_title": sess[0], "project_path": sess[1],
                           "status": sess[2], "started_at": sess[3], "ended_at": sess[4],
                           "agent_id": sess[5],
                           "summary": summary, "events": events,
                           "total": total, "offset": offset, "limit": limit
                           }, ensure_ascii=False)
    except Exception as e:
        return _json_error(e)


# ─────────────────────────────────────────────────────────────────────────────
# V15 R8 — Unified tool wrappers (merges per Round A)
# ─────────────────────────────────────────────────────────────────────────────


async def validate(file_path: str = "", project_path: str = "",
                   checks: list = None,
                   lang: str = "auto",
                   expected_encoding: str = "utf-8",
                   language_hint: str = "") -> str:
    """V15 R8 A.2: unified validate dispatcher.

    checks: list of "file" | "symbols" | "encoding". Default ["file"].
    Aggregates results into one response; ok = all sub-checks ok.

    R81: a failure names the sub-check that caused it. Previously the caller got
    a bare ok=false and the ledger recorded error_name=NULL with an empty reason
    ("Tool returned ok=false"), so neither the CA nor the audit row could tell
    which of three checks had objected.
    """
    if checks is None or not checks:
        checks = ["file"]
    if isinstance(checks, str):
        checks = [checks]
    out: dict = {"ok": True, "checks": list(checks)}
    failed: list[str] = []

    def _record(name: str, sub: dict) -> None:
        out[name] = sub
        if not sub.get("ok", True):
            out["ok"] = False
            failed.append(name)

    if "file" in checks:
        _record("file", json.loads(await validate_file(file_path, lang=lang, project_path=project_path)))
    if "symbols" in checks:
        _record("symbols", json.loads(await validate_symbols(file_path, project_path=project_path)))
    if "encoding" in checks:
        _record("encoding", json.loads(await validate_encoding(
            file_path, project_path=project_path,
            expected_encoding=expected_encoding, language_hint=language_hint)))
    if failed:
        details = []
        for name in failed:
            sub = out.get(name) or {}
            detail = sub.get("error") or sub.get("detail") or sub.get("reason") or ""
            details.append(f"{name}: {detail}" if detail else name)
        out["failed_checks"] = failed
        out["error_name"] = "VALIDATION_FAILED"
        out["error"] = "; ".join(details)
        out["ca_next_action"] = f"inspect_{failed[0]}_check_result"
    return json.dumps(out, ensure_ascii=False)


async def project_map(project_path: str, mode: str = "status",
                      backup_override: bool = False, force: bool = False) -> str:
    """V15 R8 A.3 (R9 fix): unified project_map dispatcher with consistent ok wrapper.

    mode: "status" | "generate" | "refresh" | "delta"
    All responses wrapped {ok, mode, ...inner} for stable shape.
    """
    m = (mode or "status").lower().strip()
    dispatch = {
        "status": cache_status,
        "generate": generate_project_map,
        "refresh": refresh_project_map,
        "delta": cache_delta,
    }
    fn = dispatch.get(m)
    if not fn:
        return json.dumps({
            "ok": False, "error": f"UNSUPPORTED_MODE: {mode}",
            "error_name": "UNSUPPORTED_FIELD",
            "accepted_fields": ["status", "generate", "refresh", "delta"],
        })
    try:
        # R80: generate/refresh accept force to bypass the staleness gate; status
        # and delta do not take it. Previously `force` was accepted by the schema
        # here and then silently dropped — a CA asking for a forced rebuild
        # through this dispatcher never got one.
        if m in ("generate", "refresh"):
            raw = await fn(project_path, force=bool(force))
        else:
            raw = await fn(project_path)
        inner = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        return json.dumps({"ok": False, "mode": m, "error": str(exc),
                            "error_name": "PROJECT_MAP_FAILED"})
    if isinstance(inner, dict):
        is_ok = bool(inner.get("ok", True)) and not inner.get("error")
        # Avoid key collision on "ok"
        merged = {k: v for k, v in inner.items() if k != "ok"}
        return json.dumps({"ok": is_ok, "mode": m, **merged}, ensure_ascii=False)
    return json.dumps({"ok": True, "mode": m, "result": inner}, ensure_ascii=False)


async def state(project_path: str, aspect: str = "all") -> str:
    """V15 R8 A.5 (R11 A.3 extend): unified state dispatcher.

    aspect: "cache" | "workflow" | "recommendation" | "session" | "all"
    R11 A.3: added "session" to dispatch — returns latest session events summary.
    """
    a = (aspect or "all").lower().strip()
    accepted = ("cache", "workflow", "recommendation", "session", "all")
    if a not in accepted:
        return json.dumps({
            "ok": False,
            "error": f"UNSUPPORTED_ASPECT: {aspect}",
            "error_name": "UNSUPPORTED_FIELD",
            "accepted_fields": list(accepted),
        })
    out: dict = {"ok": True, "aspect": a}
    if a in ("cache", "all"):
        try:
            out["cache"] = json.loads(await cache_status(project_path))
        except Exception as exc:
            out["cache"] = {"ok": False, "error": str(exc)}
    if a in ("workflow", "all"):
        try:
            # R43.2 C2: was `workflow_status(project_path)` — that name now binds to
            # R35 MD workflow runner (signature: name, project_path). Use renamed registry version.
            out["workflow"] = json.loads(await workflow_status_registry(project_path))
        except Exception as exc:
            out["workflow"] = {"ok": False, "error": str(exc)}
    if a in ("recommendation", "all"):
        try:
            out["recommendation"] = json.loads(await next_recommended_action(project_path))
        except Exception as exc:
            out["recommendation"] = {"ok": False, "error": str(exc)}
    if a in ("session", "all"):
        try:
            # Limit small to keep state response compact; CA can call get_session_events
            # directly when full event log is needed.
            out["session"] = json.loads(await get_session_events(project_path=project_path, limit=10))
        except Exception as exc:
            out["session"] = {"ok": False, "error": str(exc)}
    return json.dumps(out, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# R81 — CEP deploy tooling suite (copy_file / cep_manifest_validate /
# cep_extension_status / cep_deploy). Adobe CEP extensions must be MIRRORED from
# the repo into the LIVE folder %APPDATA%/Adobe/CEP/extensions/<name> for Premiere
# to load them. Previously CA hand-rolled that with run_project_test_script (logged
# as native, no audit of source/dest/hash). These tools make it first-class.
# ─────────────────────────────────────────────────────────────────────────────
def _cep_live_root() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
    return os.path.join(base, "Adobe", "CEP", "extensions")


def _sha256_file(p) -> str | None:
    # single module-level definition — a duplicate def at former L756 shadowed
    # this one (last-def-wins) and was removed in the 2026-07-11 fix round.
    import hashlib
    if not os.path.exists(p):
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _cep_default_deploy_files(repo: "Path") -> list:
    """Curated deployable set for a CEP panel: manifest + html/js/jsx/css at the
    extension root and under CSXS/. Excludes node_modules/.git/.bak/cache."""
    out = []
    skip_dirs = {"node_modules", ".git", ".orchestrator_cache", "__pycache__", "logs"}
    keep_ext = {".html", ".js", ".jsx", ".css", ".xml", ".json", ".png", ".svg", ".jsxbin"}
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            if is_write_backup(fn) or fn.endswith(".backup"):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in keep_ext:
                continue
            full = Path(dirpath) / fn
            rel = full.relative_to(repo).as_posix()
            out.append(rel)
    return sorted(out)


def _cep_parse_manifest(mp: "Path") -> dict:
    """Parse a CSXS manifest.xml → structured fields + validation findings."""
    import xml.etree.ElementTree as ET
    errors = []
    try:
        tree = ET.parse(str(mp))
        root = tree.getroot()
    except Exception as e:
        return {"valid": False, "errors": [f"XML parse failed: {e}"], "extensions": [], "hosts": [], "bundle_id": None, "duplicate_ids": []}
    bundle_id = root.attrib.get("ExtensionBundleId")
    bundle_name = root.attrib.get("ExtensionBundleName")
    if not bundle_id:
        errors.append("missing ExtensionBundleId on root")
    # Extension declarations: ONLY the direct <Extension> children of <ExtensionList>.
    # (A CEP Id legitimately appears again as a <DispatchInfoList> body — counting the
    # whole tree would false-positive every extension as a duplicate. R82 fix.)
    ext_list_el = next((el for el in root.iter("ExtensionList")), None)
    listed_ids = [e.attrib.get("Id") for e in (ext_list_el.findall("Extension") if ext_list_el is not None else [])
                  if e.attrib.get("Id") is not None]
    # de-dup detection: an Id should appear in list + once as a body; collect bodies w/ Resources
    ext_bodies = {}
    for e in root.iter("Extension"):
        eid = e.attrib.get("Id")
        if eid is None:
            continue
        res = e.find(".//MainPath")
        spath = e.find(".//ScriptPath")
        if res is not None or spath is not None:
            ext_bodies[eid] = {
                "id": eid,
                "main_path": (res.text or "").strip() if res is not None else None,
                "script_path": (spath.text or "").strip() if spath is not None else None,
            }
    ext_dir = mp.parent.parent  # CSXS/manifest.xml → extension root
    extensions = []
    for eid, body in ext_bodies.items():
        rec = {"id": eid, "main_path": body["main_path"], "script_path": body["script_path"]}
        if body["main_path"]:
            rec["main_exists"] = (ext_dir / body["main_path"].lstrip("./")).exists()
            if not rec["main_exists"]:
                errors.append(f"{eid}: MainPath '{body['main_path']}' not found on disk")
        if body["script_path"]:
            rec["script_exists"] = (ext_dir / body["script_path"].lstrip("./")).exists()
            if not rec["script_exists"]:
                errors.append(f"{eid}: ScriptPath '{body['script_path']}' not found on disk")
        extensions.append(rec)
    # duplicate Extension Ids in <ExtensionList>
    seen = {}
    duplicate_ids = []
    for i in listed_ids:
        seen[i] = seen.get(i, 0) + 1
    duplicate_ids = sorted([i for i, c in seen.items() if c > 1])
    if duplicate_ids:
        errors.append(f"duplicate Extension Id(s) in ExtensionList: {duplicate_ids}")
    hosts = [h.attrib.get("Name") for h in root.iter("Host") if h.attrib.get("Name")]
    # R82 warnings (non-blocking but high-value CEP gotchas):
    #  - a DispatchInfo body whose Id is NOT in <ExtensionList> → panel won't appear in Premiere
    #  - an <ExtensionList> Id with NO body → declared but nothing to load
    warnings = []
    listed_set = set(listed_ids)
    body_ids = set(ext_bodies.keys())
    for bid in sorted(body_ids - listed_set):
        warnings.append(f"Extension '{bid}' has a DispatchInfo body but is NOT in <ExtensionList> — Premiere will NOT show this panel")
    for lid in sorted(listed_set - body_ids):
        warnings.append(f"Extension '{lid}' is in <ExtensionList> but has no DispatchInfo body/Resources")
    return {
        "valid": len(errors) == 0,
        "bundle_id": bundle_id,
        "bundle_name": bundle_name,
        "listed_extension_ids": listed_ids,
        "extensions": extensions,
        "hosts": sorted(set(hosts)),
        "duplicate_ids": duplicate_ids,
        "errors": errors,
        "warnings": warnings,
    }


async def cep_manifest_validate(project_path: str = "", manifest_path: str = "") -> str:
    """R81 (T1) — validate a CEP CSXS/manifest.xml: XML well-formed + ExtensionBundleId,
    per-Extension MainPath/ScriptPath exist on disk, declared Hosts, duplicate Extension Ids."""
    mp = Path(manifest_path) if manifest_path else (Path(project_path) / "CSXS" / "manifest.xml" if project_path else None)
    if mp is None:
        return _json_error("Provide project_path (extension root) or manifest_path", error_name="MISSING_REQUIRED_FIELD")
    if not mp.exists():
        return _json_error(f"manifest not found: {mp}", error_name="MANIFEST_NOT_FOUND",
                           ca_next_action="check project_path points at the extension root containing CSXS/manifest.xml")
    res = _cep_parse_manifest(mp)
    res["ok"] = True
    res["manifest_path"] = str(mp)
    return json.dumps(res, ensure_ascii=False)


async def cep_extension_status(project_path: str = "", live_name: str = "") -> str:
    """R81 (T1) — readiness of a CEP extension: repo↔LIVE file hash diff, PlayerDebugMode
    registry flag, manifest validity. Answers 'will Premiere see my latest panel?'."""
    repo = Path(project_path) if project_path else None
    if repo is None or not repo.exists():
        return _json_error("project_path (extension repo root) required and must exist", error_name="MISSING_REQUIRED_FIELD")
    name = live_name or repo.name
    live_dir = Path(_cep_live_root()) / name
    mp = repo / "CSXS" / "manifest.xml"
    manifest = _cep_parse_manifest(mp) if mp.exists() else {"valid": False, "errors": ["no CSXS/manifest.xml in repo"]}
    same, diff, missing_in_live = [], [], []
    files = _cep_default_deploy_files(repo)
    for rel in files:
        rp = repo / rel
        lp = live_dir / rel
        try:
            if not lp.exists():
                missing_in_live.append(rel)
            elif _sha256_file(rp) == _sha256_file(lp):
                same.append(rel)
            else:
                diff.append(rel)
        except Exception:
            diff.append(rel)
    # PlayerDebugMode registry (HKCU\Software\Adobe\CSXS.<n>)
    debug_keys = []
    try:
        import winreg
        for v in range(6, 15):
            try:
                k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\\Adobe\\CSXS.{v}")
                try:
                    val, _ = winreg.QueryValueEx(k, "PlayerDebugMode")
                    debug_keys.append({"csxs": v, "PlayerDebugMode": str(val)})
                except FileNotFoundError:
                    debug_keys.append({"csxs": v, "PlayerDebugMode": None})
                winreg.CloseKey(k)
            except FileNotFoundError:
                continue
    except Exception as e:
        debug_keys = [{"error": str(e)}]
    debug_enabled = any(str(d.get("PlayerDebugMode")) == "1" for d in debug_keys if isinstance(d, dict))
    in_sync = (len(diff) == 0 and len(missing_in_live) == 0 and live_dir.exists())
    return json.dumps({
        "ok": True,
        "repo": str(repo),
        "live_dir": str(live_dir),
        "live_exists": live_dir.exists(),
        "manifest_valid": manifest.get("valid"),
        "manifest_errors": manifest.get("errors", []),
        "files": {"total": len(files), "same": len(same), "diff": diff, "missing_in_live": missing_in_live},
        "in_sync": in_sync,
        "debug_mode_enabled": debug_enabled,
        "debug_keys": debug_keys,
        "ready_hint": ("Premiere should load the latest panel"
                       if (in_sync and manifest.get("valid") and debug_enabled)
                       else "NOT ready: " + "; ".join(filter(None, [
                           None if live_dir.exists() else "LIVE folder missing — run cep_deploy",
                           None if in_sync else f"{len(diff)} changed + {len(missing_in_live)} missing in LIVE — run cep_deploy",
                           None if manifest.get("valid") else "manifest invalid — fix CSXS/manifest.xml",
                           None if debug_enabled else "PlayerDebugMode not set — unsigned extension won't load (set HKCU\\Software\\Adobe\\CSXS.N\\PlayerDebugMode=1)",
                       ]))),
    }, ensure_ascii=False)


# F401 FALSE POSITIVE: registry_data.BASE_TOOL_MAP_SPECS names 'copy_file' as a STRING
# expr that build_tool_map() eval()s against code_tools globals(), so ruff sees no
# reference -- `ruff --fix` would delete it and the daemon would then die at import
# with NameError. Keep the noqa.
from orchestrator.tools.write import copy_file  # noqa: F401


async def cep_deploy(project_path: str = "", live_name: str = "", files: list = None,
                     validate_first: bool = True) -> str:
    """R81 (T2) — mirror a CEP extension repo→LIVE (%APPDATA%/Adobe/CEP/extensions/<name>), per-file backup + hash verify + audit. files=[] deploys the curated
    set (manifest + html/js/jsx/css/json/png); pass explicit relative paths to deploy a subset."""
    import shutil
    import time as _t
    repo = Path(project_path) if project_path else None
    if repo is None or not repo.exists():
        return _json_error("project_path (extension repo root) required and must exist", error_name="MISSING_REQUIRED_FIELD")
    name = live_name or repo.name
    live_dir = Path(_cep_live_root()) / name
    mp = repo / "CSXS" / "manifest.xml"
    if validate_first:
        if not mp.exists():
            return _json_error(f"no CSXS/manifest.xml in repo: {mp}", error_name="MANIFEST_NOT_FOUND")
        man = _cep_parse_manifest(mp)
        if not man.get("valid"):
            return _json_error("manifest invalid — fix before deploy", error_name="MANIFEST_INVALID",
                               manifest_errors=man.get("errors", []), ca_next_action="fix CSXS/manifest.xml then retry")
    rels = list(files) if files else _cep_default_deploy_files(repo)
    deployed, errors = [], []
    for rel in rels:
        rp = repo / rel
        lp = live_dir / rel
        if not rp.exists():
            errors.append({"rel": rel, "error": "source missing"})
            continue
        try:
            lp.parent.mkdir(parents=True, exist_ok=True)
            existed = lp.exists()
            bak = None
            if existed:
                bak = str(lp) + ".bak_" + _t.strftime("%Y%m%d_%H%M%S")
                shutil.copy2(str(lp), bak)
            shutil.copy2(str(rp), str(lp))
            ok_hash = _sha256_file(rp) == _sha256_file(lp)
            deployed.append({"rel": rel, "sha256": _sha256_file(lp), "dst_existed": existed, "hash_verified": ok_hash, "backup": bak})
            if not ok_hash:
                errors.append({"rel": rel, "error": "hash mismatch after copy"})
        except Exception as e:
            errors.append({"rel": rel, "error": str(e)})
    return json.dumps({
        "ok": len(errors) == 0,
        "repo": str(repo), "live_dir": str(live_dir),
        "validated": bool(validate_first),
        "deployed_count": len(deployed), "deployed": deployed,
        "errors": errors,
        "reload_hint": "In Premiere: close & reopen the panel (Window>Extensions) to load the redeployed files.",
    }, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# TOOL_MAP — public T1/T2/T3 tool exposure.
#
# T1: Discover / Analyze / Read
# T2: Mutate / Verify / Refresh
# T3: Session / Audit / Auxiliary
# T4: Runtime / DOM / Browser / Server lifecycle (V15)
# Internal-only helpers are intentionally not registered here:
# execute_script, create_session_bootstrap, update_workflow_state, update_file_cache.
# ─────────────────────────────────────────────────────────────────────────────

# ─── R86 — multi-agent config manager (manage_agent) ─────────────────────────
# Central tool to register agents and write their global-rule files + MCP config
# in bulk after an [A] self-upgrade changes the rule/MCP contract. Preserves each
# agent's persona line; rewrites only the managed body. Auto-backup + validate.
_AGENTS_REGISTRY_PATH = Path(__file__).parent / "agents_registry.json"

_PERSONA_RE = re.compile(r'(?im)always refer to yourself as .+?address the user as [^.\n]*\.')


def _ma_rule_file_text(filename: str) -> str:
    """Rule template SOT = file under examples/ (editable without code change /
    [A] restart — manage_agent reads at call time). There is no in-code copy:
    a missing or empty template raises, naming the path, so sync_rule renders
    nothing instead of a stale body into every agent's rule file."""
    p = Path(__file__).parent / "examples" / filename
    text = p.read_text(encoding="utf-8").strip() if p.is_file() else ""
    if not text:
        raise FileNotFoundError(
            f"rule template missing or empty: {p} -- examples/ is the only rule SOT; "
            "restore it from git, nothing was rendered")
    return text


def _ma_core_rule_body() -> str:
    return _ma_rule_file_text("CORE_RULE.txt")


def _ma_slim_rule_body() -> str:
    return _ma_rule_file_text("SLIM_RULE.txt")


def _ma_rule_body(template: str) -> str:
    t = (template or "slim").strip().lower()
    if t in ("full", "core"):
        return _ma_core_rule_body()
    return _ma_slim_rule_body()


def _ma_rule_source_metadata(template: str) -> dict:
    t = (template or "slim").strip().lower()
    if t in ("full", "core"):
        metadata = get_rule_core_metadata()
        return {
            "rule_source": "CORE_RULE.txt",
            **metadata,
            "rule_source_sha256": metadata["sha256"],
        }
    text = _ma_slim_rule_body()
    path = Path(__file__).parent / "examples" / "SLIM_RULE.txt"
    return {
        "rule_source": "SLIM_RULE.txt",
        "rule_source_path": str(path),
        "rule_source_sha256": _sha256_text(text),
    }


def _ma_mode_stamp(mode: str, template: str, source_sha256: str) -> str:
    """Stable managed footer identifying the canonical source used to render it."""
    return (f"<!-- [A] managed rule | mode={mode} | template={template} | "
            f"rule_source_sha256={source_sha256} | "
            f"owned by manage_agent sync_rule/set_mode - do not hand-edit -->")


_MA_RULE_DIFF_MAX_CHARS = 2000


def _ma_rule_diff_summary(current: str, expected: str) -> str:
    diff = "\n".join(difflib.unified_diff(
        current.splitlines(), expected.splitlines(),
        fromfile="current", tofile="expected", lineterm=""))
    if len(diff) <= _MA_RULE_DIFF_MAX_CHARS:
        return diff
    suffix = "\n... diff truncated ..."
    return diff[:_MA_RULE_DIFF_MAX_CHARS - len(suffix)] + suffix


_MA_NATIVE_RULE_INLINE = (
    "[A]-NATIVE MODE (Anh-approved opt-out): native project ops allowed for this CA; "
    "[A] at http://localhost:8083 stays available but is not mandatory. Destructive "
    "actions still need Anh's explicit go-ahead. Default for every CA is [A]-first; "
    "return with manage_agent set_mode mode=A.")


def _ma_native_rule_body(ag: dict) -> str:
    aid = str(ag.get("id") or "ca")
    return f"""# Project-Orchestrator [A] — NATIVE MODE (Anh-approved opt-out)

Anh has explicitly allowed agent `{aid}` to work natively without routing project ops through [A].

- Native file/shell tools are permitted in this mode; you do NOT need to call [A] for project reads/writes.
- [A] stays available at http://localhost:8083 (HTTP `POST /api/call/<tool>`) — use it when clearly better (council messaging, audits, cross-agent work).
- Destructive/irreversible actions still require Anh's explicit go-ahead ("tiến hành").
- DEFAULT for every CA is [A]-first; this opt-out is temporary. Return with: manage_agent(action='set_mode', agent_id='{aid}', mode='A') — [A] regenerates this file.
"""


def _ma_rule_inline(template: str) -> str:
    t = (template or "slim").strip().lower()
    if t in ("full", "core"):
        return re.sub(r"\s+", " ", _ma_core_rule_body()).strip()
    return _ma_rule_file_text("SLIM_RULE_INLINE.txt")


def _load_agents_registry() -> dict:
    # A31: decode via the shared BOM-safe helper (utf-8-sig). The fail-safe
    # try/except is kept HERE deliberately — this reader must never raise into
    # manage_agent; callers rely on the empty-but-safe {"agents": {}} shape.
    try:
        if _AGENTS_REGISTRY_PATH.exists():
            from orchestrator.core.ca_policy_matrix import load_agents_registry_file
            return load_agents_registry_file(_AGENTS_REGISTRY_PATH)
    except Exception:
        pass
    return {"agents": {}}


def _save_agents_registry(reg: dict) -> None:
    _atomic_write_json(_AGENTS_REGISTRY_PATH, reg, indent=2)


def _ma_extract_persona(text: str) -> str:
    m = _PERSONA_RE.search(text or "")
    return m.group(0).strip() if m else ""


_MA_LEGACY_SKILL_ROOTS = (
    Path("C:/Users/ADMIN/.claude/skills"),
    Path("C:/Users/ADMIN/.agents/skills"),
)


def _ma_archive_dir(parent: Path, stamp: str | None = None) -> Path:
    return parent / "_ma_archive" / (stamp or datetime.now().strftime("%Y%m%d_%H%M%S"))


def _ma_unique_archive_path(parent: Path, name: str, stamp: str | None = None) -> Path:
    base = _ma_archive_dir(parent, stamp) / name
    if not base.exists():
        return base
    for i in range(1, 1000):
        candidate = base.with_name(f"{base.name}.{i}")
        if not candidate.exists():
            return candidate
    return base.with_name(f"{base.name}.{uuid.uuid4().hex[:8]}")


def _ma_backup(p: Path) -> str:
    try:
        if p.exists():
            bak = _ma_unique_archive_path(p.parent, p.name + ".bak_ma")
            bak.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_bytes(bak, p.read_bytes())
            return str(bak)
    except Exception:
        pass
    return ""


def _ma_mcp_block(reg: dict) -> tuple:
    cmd = reg.get("mcp_command", "")
    server = reg.get("mcp_args_server", "")
    return cmd, [server, "--mcp"]


def _ma_deny_refusal(aid, key: str, p: Path) -> dict | None:
    """#35: a path the registry names is a write target too. sync_rule / sync_mcp /
    sync_skill write it, and the dispatcher's gate only sees the call's own body --
    register / update store any path -- so the deny list reads it HERE, where it is
    written. None = allowed; a dry run is refused the same way, so it previews true.
    """
    from orchestrator.fileops.xroot import xroot_check

    verdict = xroot_check(p, "manage_agent")
    if verdict.get("allowed"):
        return None
    return {"ok": False, "agent": aid, "status": "error", key: str(p), "error_name": "XROOT_DENIED",
            "group": verdict.get("group", ""), "error": verdict.get("reason", "")}


def _ma_write_rule(ag: dict, reg: dict, dry_run: bool) -> dict:
    rule_file = (ag.get("rule_file") or "").strip()
    if not rule_file:
        return {"ok": False, "agent": ag.get("id"), "error": "rule_file missing; cannot sync_rule"}
    p = Path(rule_file)
    if p == Path(".") or p.is_dir():
        return {"ok": False, "agent": ag.get("id"), "rule_file": str(p), "error": "rule_file must be a file path"}
    refused = _ma_deny_refusal(ag.get("id"), "rule_file", p)
    if refused:
        return refused
    fmt = ag.get("rule_format", "markdown")
    template = ag.get("rule_template", "slim")
    # Per-CA transport mode: "A" = [A]-first rule body (the default for every
    # CA), "native" = Anh-approved opt-out body. Missing field = "A".
    mode = "native" if str(ag.get("mode") or "A").strip().lower() == "native" else "A"
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    persona = ag.get("persona", "") or _ma_extract_persona(existing)
    if fmt == "markdown":
        source_meta = _ma_rule_source_metadata(template)
        source_sha256 = source_meta["rule_source_sha256"]
        body = _ma_native_rule_body(ag) if mode == "native" else _ma_rule_body(template)
        new = (((persona + "\n\n") if persona else "") + body.rstrip()
               + "\n\n" + _ma_mode_stamp(mode, template, source_sha256) + "\n")
        expected_hash = _sha256_text(new)
        current_hash = _sha256_text(existing)
        changed = existing != new
        sync_meta = {
            "expected_hash": expected_hash,
            "current_hash": current_hash,
            "changed": changed,
            "diff_summary": _ma_rule_diff_summary(existing, new) if changed else "",
        }
        if dry_run:
            return {"ok": True, "agent": ag.get("id"), "rule_file": str(p), "dry_run": True, "rule_template": template, "mode": mode, "would_write_chars": len(new), "persona_preserved": bool(persona), **sync_meta, **source_meta}
        if not changed:
            return {"ok": True, "agent": ag.get("id"), "rule_file": str(p), "backup": "", "rule_template": template, "mode": mode, "persona_preserved": bool(persona), "bytes": len(new), **sync_meta, **source_meta}
        existed = p.exists()
        bak = _ma_backup(p)
        if existed and not bak:
            return {"ok": False, "agent": ag.get("id"), "rule_file": str(p), "backup": "", "error": "rule backup failed; write not attempted", **sync_meta, **source_meta}
        p.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(p, new, encoding="utf-8")
        try:
            readback_matches = p.read_text(encoding="utf-8") == new
        except Exception:
            readback_matches = False
        if not readback_matches:
            if bak:
                _ma_restore(p, bak)
            elif not existed:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
            return {"ok": False, "agent": ag.get("id"), "rule_file": str(p), "backup": bak, "error": "post-write readback mismatch; rolled back", **sync_meta, **source_meta}
        return {"ok": True, "agent": ag.get("id"), "rule_file": str(p), "backup": bak, "rule_template": template, "mode": mode, "persona_preserved": bool(persona), "bytes": len(new), **sync_meta, **source_meta}
    if fmt == "json_field":
        try:
            data = json.loads(existing) if existing else {}
        except Exception as e:
            return {"ok": False, "agent": ag.get("id"), "error": f"settings.json parse failed: {e}"}
        # persona from existing field text if present
        field = "github.copilot.chat.codeGeneration.instructions"
        cur = ""
        try:
            cur = (data.get(field) or [{}])[0].get("text", "")
        except Exception:
            cur = ""
        persona2 = persona or _ma_extract_persona(cur)
        inline_core = _MA_NATIVE_RULE_INLINE if mode == "native" else _ma_rule_inline(template)
        inline = ((persona2 + " ") if persona2 else "") + inline_core
        if dry_run:
            return {"ok": True, "agent": ag.get("id"), "rule_file": str(p), "dry_run": True, "rule_template": template, "would_set_field": field, "persona_preserved": bool(persona2)}
        data[field] = [{"text": inline}]
        bak = _ma_backup(p)
        _atomic_write_json(p, data, indent=4)
        ok, err = _ma_validate_json(p)
        if not ok:
            _ma_restore(p, bak)
            return {"ok": False, "agent": ag.get("id"), "error": f"post-write JSON invalid, rolled back: {err}"}
        return {"ok": True, "agent": ag.get("id"), "rule_file": str(p), "backup": bak, "rule_template": template, "persona_preserved": bool(persona2)}
    return {"ok": False, "agent": ag.get("id"), "error": f"unknown rule_format {fmt}"}


def _ma_validate_json(p: Path) -> tuple:
    try:
        json.loads(p.read_text(encoding="utf-8"))
        return True, ""
    except Exception as e:
        return False, str(e)


def _ma_validate_toml(p: Path) -> tuple:
    try:
        import tomllib
        tomllib.loads(p.read_text(encoding="utf-8"))
        return True, ""
    except Exception as e:
        return False, str(e)


def _ma_restore(p: Path, bak: str) -> None:
    try:
        if bak and Path(bak).exists():
            _atomic_write_bytes(p, Path(bak).read_bytes())
    except Exception:
        pass


def _ma_restore_tree(target: Path, bak: str) -> None:
    try:
        bp = Path(bak) if bak else None
        if not bp or not bp.exists():
            return
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink()
        shutil.move(str(bp), str(target))
    except Exception:
        pass


def _ma_archive_legacy_bak_ma(roots: tuple[Path, ...] | None = None) -> list[dict]:
    moved = []
    for root in roots or _MA_LEGACY_SKILL_ROOTS:
        try:
            if not root.exists() or not root.is_dir():
                continue
            for item in root.glob("*.bak_ma"):
                if item.parent.name == "_ma_archive":
                    continue
                dest = _ma_unique_archive_path(root, item.name, "legacy_20260709")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(item), str(dest))
                moved.append({"from": str(item), "to": str(dest)})
        except Exception as exc:
            moved.append({"root": str(root), "error": str(exc)})
    return moved


def _ma_write_mcp(ag: dict, reg: dict, dry_run: bool) -> dict:
    mcp_file = (ag.get("mcp_file") or "").strip()
    if not mcp_file:
        return {"ok": True, "agent": ag.get("id"), "skipped": True, "reason": "no mcp_file configured; broker-only or non-MCP agent"}
    p = Path(mcp_file)
    if p == Path(".") or p.is_dir():
        return {"ok": False, "agent": ag.get("id"), "mcp_file": str(p), "error": "mcp_file must be a file path"}
    refused = _ma_deny_refusal(ag.get("id"), "mcp_file", p)
    if refused:
        return refused
    fmt = ag.get("mcp_format", "json")
    key = ag.get("mcp_key", "project-orchestrator")
    cmd, args = _ma_mcp_block(reg)
    # A34 W2 item 4: the MCP bridge reads its HTTP wait from the ENVIRONMENT at import time --
    # mcp_stdio._MCP_PROXY_DEFAULT_TIMEOUT <- MCP_PROXY_HTTP_TIMEOUT (default 180s), then
    # max(that, tool_declared_timeout + 30s). Only wrappers that DECLARE a `timeout` param can
    # push past 180s, so a future 3-5 minute tool without one was capped with no knob anywhere:
    # the env var existed but nothing wired it into the CA configs this function writes.
    # Registry-level `mcp_env` applies to every agent, per-agent `mcp_env` overrides key by key.
    # Absent on both = nothing emitted, so a config that never opts in stays byte-identical.
    env = {str(k): str(v) for k, v in
           {**(reg.get("mcp_env") or {}), **(ag.get("mcp_env") or {})}.items()}
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    if dry_run:
        return {"ok": True, "agent": ag.get("id"), "mcp_file": str(p), "mcp_format": fmt, "dry_run": True}
    if fmt in ("json", "vscode"):
        try:
            data = json.loads(existing) if existing else {}
        except Exception as e:
            return {"ok": False, "agent": ag.get("id"), "error": f"{p.name} parse failed: {e}"}
        entry = {"type": "stdio", "command": cmd, "args": args}
        if env:
            entry["env"] = dict(env)
        if fmt == "json" and (ag.get("id") == "opencode" or p.name.lower() == "opencode.json"):
            local = {"type": "local", "command": [cmd, *args], "enabled": True}
            if env:
                # opencode spells this key `environment`, NOT `env` -- measured 2026-09-18 in the
                # live C:/Users/ADMIN/.config/opencode/opencode.json, where every other local
                # server carries `environment`. Emitting `env` here would be silently ignored.
                local["environment"] = dict(env)
            data.setdefault("mcp", {})[key] = local
            if isinstance(data.get("mcpServers"), dict):
                data["mcpServers"].pop(key, None)
                if not data["mcpServers"]:
                    data.pop("mcpServers", None)
        elif fmt == "json":
            plain = {"command": cmd, "args": args}
            if env:
                plain["env"] = dict(env)
            data.setdefault("mcpServers", {})[key] = plain
        else:
            data.setdefault("servers", {})[key] = entry
        bak = _ma_backup(p)
        _atomic_write_json(p, data, indent=4)
        ok, err = _ma_validate_json(p)
        if not ok:
            _ma_restore(p, bak)
            return {"ok": False, "agent": ag.get("id"), "error": f"post-write JSON invalid, rolled back: {err}"}
        return {"ok": True, "agent": ag.get("id"), "mcp_file": str(p), "backup": bak, "mcp_format": fmt}
    if fmt == "toml":
        block = f"[mcp_servers.{key}]\ncommand = '{cmd}'\nargs = ['{args[0]}', '--mcp']\n"
        if env:
            # An INLINE table stays on ONE line, so the region regex below (which ends at the
            # next `^[`) still sees exactly one block. An `[mcp_servers.KEY.env]` sub-table would
            # terminate that match early and leave a stale duplicate behind on the next sync.
            parts = []
            for ek in sorted(env):
                bare = ek if re.match(r"^[A-Za-z0-9_-]+$", ek) else json.dumps(ek)
                parts.append(bare + " = " + json.dumps(env[ek]))
            block += "env = { " + ", ".join(parts) + " }\n"
        pat = re.compile(r"(?ms)^\[mcp_servers\." + re.escape(key) + r"\].*?(?=^\[|\Z)")
        if pat.search(existing):
            newtext = pat.sub(block, existing)
        else:
            newtext = existing.rstrip() + "\n\n" + block
        bak = _ma_backup(p)
        _atomic_write_text(p, newtext, encoding="utf-8")
        ok, err = _ma_validate_toml(p)
        if not ok:
            _ma_restore(p, bak)
            return {"ok": False, "agent": ag.get("id"), "error": f"post-write TOML invalid, rolled back: {err}"}
        return {"ok": True, "agent": ag.get("id"), "mcp_file": str(p), "backup": bak, "mcp_format": fmt}
    return {"ok": False, "agent": ag.get("id"), "error": f"unknown mcp_format {fmt}"}


def _ma_skip_write_backups(_dirpath, names) -> list:
    """copytree ignore: a write_fix `.bak` is the source's rollback copy, not part of the skill."""
    return [n for n in names if is_write_backup(n)]


def _ma_skill_dir_summary(root: Path) -> dict:
    h = hashlib.sha256()
    n = 0
    for p in sorted(root.rglob("*"), key=lambda x: x.as_posix()):
        if p.is_file() and not is_write_backup(p.name):
            rel = p.relative_to(root).as_posix()
            h.update(rel.encode("utf-8"))
            h.update(hashlib.sha256(p.read_bytes()).hexdigest().encode("utf-8"))
            n += 1
    return {"files": n, "sha256": h.hexdigest()}


_SKILL_ADAPTER_FORMATS = ("agents_md_ref", "rule_file_append")


def _ma_write_skill_adapter(aid: str, rule_file: str, skill_name: str, src: str,
                            sha: str, body: str, skill_format: str,
                            dry_run: bool, explicit_target: bool) -> dict:
    """Adapter sync for agents whose canonical mechanism is an AGENTS.md / rule
    file (no native SKILL.md folder load). Writes a compact, idempotent managed
    skill-reference block into the agent's rule_file. Never claims native support."""
    if not rule_file:
        return {"ok": True, "agent": aid, "status": "adapter_needed", "skill_format": skill_format,
                "reason": "skill_format requires rule_file but none configured; set rule_file or a verified skill_dir"}
    refused = _ma_deny_refusal(aid, "target", Path(rule_file))
    if refused:
        return refused
    if dry_run or not explicit_target:
        return {"ok": True, "agent": aid, "status": "adapter_needed", "skill_format": skill_format,
                "target": rule_file, "sha256": sha,
                "reason": "rule_file adapter available (not native); rerun with explicit target=<id> + dry_run=false to write the skill reference"}
    start = f"<!-- A:SKILL:{skill_name} START -->"
    end = f"<!-- A:SKILL:{skill_name} END -->"
    inner = (f"{start}\n"
             f"<!-- managed by [A] manage_agent sync_skill ({skill_format}); canonical source: {src} sha256={sha}; do not edit by hand -->\n"
             f"{body.strip()}\n"
             f"{end}")
    p = Path(rule_file)
    backup = ""
    try:
        existing = p.read_text(encoding="utf-8") if p.exists() else ""
        pat = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
        if pat.search(existing):
            newtext = pat.sub(lambda _m: inner, existing)
        elif existing.strip():
            newtext = existing.rstrip() + "\n\n" + inner + "\n"
        else:
            newtext = inner + "\n"
        backup = _ma_backup(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(p, newtext, encoding="utf-8")
    except Exception as e:
        return {"ok": False, "agent": aid, "status": "error", "target": str(p),
                "backup": backup, "reason": f"adapter write failed: {e}"}
    return {"ok": True, "agent": aid, "status": "ok_adapter", "skill_format": skill_format,
            "target": str(p), "sha256": sha, "backup": backup,
            "reason": "skill reference written into rule_file (adapter, not native SKILL.md folder)"}


def _ma_write_skill(ag: dict, reg: dict, skill_name: str, dry_run: bool,
                    explicit_target: bool = False) -> dict:
    aid = ag.get("id")
    skill_dir = (ag.get("skill_dir") or "").strip()
    skill_format = (ag.get("skill_format") or "folder").strip().lower()
    rule_file = (ag.get("rule_file") or "").strip()
    if not skill_name:
        return {"ok": False, "agent": aid, "status": "error", "reason": "skill_name required"}
    src_root = (Path(__file__).parent / ".ToolA_Skill" / "skills").resolve()
    src = (src_root / skill_name).resolve()
    if src.parent != src_root:
        return {"ok": False, "agent": aid, "status": "error",
                "reason": "skill must be canonical .ToolA_Skill/skills/<name> (no shim/other path)"}
    if not src.is_dir():
        return {"ok": False, "agent": aid, "status": "error", "source": str(src), "reason": "source skill folder missing"}
    skill_md = src / "SKILL.md"
    if not skill_md.is_file():
        return {"ok": False, "agent": aid, "status": "error", "source": str(src), "reason": "SKILL.md missing in source"}
    src_sum = _ma_skill_dir_summary(src)
    # 1) Native SKILL.md folder copy (verified mechanism: clau/codo/pic/hermes).
    if skill_dir and skill_format == "folder":
        target = Path(skill_dir) / skill_name
        refused = _ma_deny_refusal(aid, "target", target)
        if refused:
            return refused
        if dry_run:
            return {"ok": True, "agent": aid, "status": "dry_run", "source": str(src), "target": str(target),
                    "sha256": src_sum["sha256"], "files": src_sum["files"], "reason": "would copy (native folder)"}
        backup = ""
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                bak = _ma_unique_archive_path(target.parent, target.name + ".bak_ma")
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(bak))
                backup = str(bak)
            shutil.copytree(src, target, ignore=_ma_skip_write_backups)
        except Exception as e:
            _ma_restore_tree(target, backup)
            return {"ok": False, "agent": aid, "status": "error", "source": str(src), "target": str(target),
                    "backup": backup, "reason": f"copy failed: {e}"}
        tgt_sum = _ma_skill_dir_summary(target)
        return {"ok": True, "agent": aid, "status": "ok", "source": str(src), "target": str(target),
                "sha256": tgt_sum["sha256"], "files": tgt_sum["files"],
                "match": tgt_sum["sha256"] == src_sum["sha256"], "backup": backup, "reason": "copied (native folder)"}
    # 2) Adapter mechanism (AGENTS.md / rule-file agents, e.g. opencode/clin).
    if skill_format in _SKILL_ADAPTER_FORMATS:
        body = skill_md.read_text(encoding="utf-8")
        return _ma_write_skill_adapter(aid, rule_file, skill_name, str(src),
                                       src_sum["sha256"], body, skill_format, dry_run, explicit_target)
    # 3) No verified native folder and no adapter configured — report honestly.
    if rule_file:
        return {"ok": True, "agent": aid, "status": "adapter_needed", "rule_file": rule_file,
                "reason": "no skill_dir; rule_file present but native SKILL.md folder load not verified for this agent — set skill_format='agents_md_ref' or 'rule_file_append' to sync a reference"}
    return {"ok": True, "agent": aid, "status": "not_verified",
            "reason": "no skill_dir and no rule_file; native SKILL.md folder support not verified for this agent (HermC: unknown) — do not assume native load"}


async def manage_agent(action: str, agent_id: str = "", target: str = "all",
                       persona: str = "", rule_file: str = "", rule_format: str = "markdown",
                       rule_template: str = "slim", mcp_file: str = "", mcp_format: str = "json",
                       mcp_key: str = "project-orchestrator", dry_run: bool = False,
                       skill_name: str = "", skill_dir: str = "", skill_format: str = "folder",
                       mode: str = "", cleanup: bool = True) -> str:
    """R86 T3 — central multi-agent config manager (CA lifecycle).

    actions:
      register   — upsert one agent {agent_id, persona, rule_file, rule_format,
                   mcp_file, mcp_format, mcp_key, mode} into agents_registry.json
                   (mode defaults to "A" = [A]-first)
      unregister — remove agent from registry; cleanup=True (default) also strips
                   the managed rule body (persona kept) and removes the [A] MCP
                   block from json-format mcp_file
      list/get   — inspect the registry (get needs agent_id)
      set_mode   — flip one agent between mode="A" ([A]-first, the default for
                   every CA) and mode="native" (Anh-approved opt-out), then
                   regenerate its rule file immediately
      sync_rule  — rewrite global-rule body for target (id or 'all'), persona
                   preserved; body rendered per the agent's mode
      sync_mcp   — rewrite project-orchestrator MCP block for target (id or 'all')
    All writes auto-backup under _ma_archive + validate (json/toml) + rollback on failure.
    Use dry_run=True to preview.
    """
    try:
        migration = _ma_archive_legacy_bak_ma()
        reg = _load_agents_registry()
        agents = reg.setdefault("agents", {})
        act = (action or "").strip().lower()

        def _ma_require_agent_id(_act: str, _aid: str, _target: str, _agents: dict):
            """A40 — get/set_mode/unregister read ``agent_id``; only sync_* read
            ``target``. An empty agent_id is a MISSING field, not an unsupported
            one: naming it UNSUPPORTED_FIELD handed the CA a corrected_payload
            that still carried ``agent_id=""``, so the prescribed retry could only
            fail identically and the attempt after it earned
            DUPLICATE_RETRY_BLOCKED. Returns an error string, or None to proceed."""
            if not _aid:
                _moved = _target if _target and _target != "all" and _target in _agents else ""
                _msg = f"agent_id required for {_act}"
                if _moved:
                    _msg += (f"; you passed target='{_moved}' -- {_act} ignores target, "
                             f"resend with agent_id='{_moved}'")
                return _json_error(_msg, error_name="MISSING_REQUIRED_FIELD",
                                   accepted_fields=["agent_id"])
            if _aid not in _agents:
                return _json_error(f"agent not registered: {_aid}",
                                   error_name="UNSUPPORTED_FIELD",
                                   accepted_fields=sorted(_agents.keys()))
            return None
        if act == "list":
            return json.dumps({"ok": True, "agents": list(agents.keys()), "registry_path": str(_AGENTS_REGISTRY_PATH)}, ensure_ascii=False)
        if act == "get":
            _bad = _ma_require_agent_id(act, agent_id, target, agents)
            if _bad:
                return _bad
            return json.dumps({"ok": True, "agent": {"id": agent_id, **agents[agent_id]}}, ensure_ascii=False)
        if act == "register":
            if not agent_id:
                return _json_error("agent_id required for register", error_name="UNSUPPORTED_FIELD")
            rec = agents.get(agent_id, {})
            if persona: rec["persona"] = persona
            if rule_file: rec["rule_file"] = rule_file
            if rule_format: rec["rule_format"] = rule_format
            if rule_template: rec["rule_template"] = rule_template
            if mcp_file: rec["mcp_file"] = mcp_file
            if mcp_format: rec["mcp_format"] = mcp_format
            if mcp_key: rec["mcp_key"] = mcp_key
            if skill_dir: rec["skill_dir"] = skill_dir
            if skill_format: rec["skill_format"] = skill_format
            if mode:
                rec["mode"] = "native" if mode.strip().lower() == "native" else "A"
            rec.setdefault("mode", "A")
            rec["id"] = agent_id
            agents[agent_id] = rec
            _save_agents_registry(reg)
            return json.dumps({"ok": True, "registered": agent_id, "agent": rec}, ensure_ascii=False)
        if act == "set_mode":
            _bad = _ma_require_agent_id(act, agent_id, target, agents)
            if _bad:
                return _bad
            m = (mode or "").strip().lower()
            if m not in ("a", "native"):
                return _json_error("mode must be 'A' or 'native'", error_name="UNSUPPORTED_FIELD",
                                   accepted_fields=["A", "native"])
            new_mode = "native" if m == "native" else "A"
            agents[agent_id]["mode"] = new_mode
            if not dry_run:
                _save_agents_registry(reg)
            rule_sync = _ma_write_rule({"id": agent_id, **agents[agent_id]}, reg, dry_run)
            return json.dumps({"ok": bool(rule_sync.get("ok")), "action": "set_mode",
                               "agent": agent_id, "mode": new_mode, "dry_run": dry_run,
                               "rule_sync": rule_sync,
                               "note": ("native = Anh-approved opt-out; default for every CA is mode=A"
                                        if new_mode == "native" else "[A]-first restored")},
                              ensure_ascii=False)
        if act == "unregister":
            _bad = _ma_require_agent_id(act, agent_id, target, agents)
            if _bad:
                return _bad
            rec = dict(agents[agent_id])
            cleaned: dict = {}
            if dry_run:
                return json.dumps({"ok": True, "action": "unregister", "agent": agent_id,
                                   "dry_run": True, "would_cleanup": bool(cleanup),
                                   "rule_file": rec.get("rule_file", ""),
                                   "mcp_file": rec.get("mcp_file", "")}, ensure_ascii=False)
            agents.pop(agent_id, None)
            _save_agents_registry(reg)
            if cleanup:
                try:
                    rf = Path(rec.get("rule_file") or "")
                    if rec.get("rule_file") and rf.is_file():
                        old_text = rf.read_text(encoding="utf-8")
                        persona_line = rec.get("persona") or _ma_extract_persona(old_text)
                        bak = _ma_backup(rf)
                        note = (f"# [A] managed rule removed (unregister "
                                f"{datetime.now().strftime('%Y-%m-%d %H:%M')}) — this CA is no "
                                f"longer [A]-managed; re-register via manage_agent(action='register').\n")
                        _atomic_write_text(rf, (((persona_line + "\n\n") if persona_line else "") + note),
                                           encoding="utf-8")
                        cleaned["rule_file"] = {"path": str(rf), "backup": bak, "persona_kept": bool(persona_line)}
                except Exception as exc:
                    cleaned["rule_file"] = {"error": str(exc)}
                try:
                    mf = Path(rec.get("mcp_file") or "")
                    mkey = rec.get("mcp_key") or "project-orchestrator"
                    if rec.get("mcp_file") and mf.is_file() and (rec.get("mcp_format") or "json") in ("json", "vscode"):
                        data = json.loads(mf.read_text(encoding="utf-8"))
                        holder = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else data
                        if isinstance(holder, dict) and mkey in holder:
                            bak = _ma_backup(mf)
                            holder.pop(mkey, None)
                            _atomic_write_json(mf, data, indent=2)
                            okj, errj = _ma_validate_json(mf)
                            if not okj:
                                _ma_restore(mf, bak)
                                cleaned["mcp_file"] = {"error": f"post-write invalid, rolled back: {errj}"}
                            else:
                                cleaned["mcp_file"] = {"path": str(mf), "backup": bak, "removed_key": mkey}
                        else:
                            cleaned["mcp_file"] = {"note": f"key '{mkey}' not present; nothing removed"}
                    elif rec.get("mcp_file"):
                        cleaned["mcp_file"] = {"note": f"manual cleanup needed for mcp_format={rec.get('mcp_format')}"}
                except Exception as exc:
                    cleaned["mcp_file"] = {"error": str(exc)}
            return json.dumps({"ok": True, "action": "unregister", "agent": agent_id,
                               "cleanup": cleaned, "remaining": list(agents.keys())}, ensure_ascii=False)
        if act in ("sync_rule", "sync_mcp"):
            ids = list(agents.keys()) if (target or "all") == "all" else [target]
            results = []
            for aid in ids:
                if aid not in agents:
                    results.append({"ok": False, "agent": aid, "error": "not registered"})
                    continue
                ag = {"id": aid, **agents[aid]}
                if act == "sync_rule" and (rule_template or "").strip().lower() in ("full", "core"):
                    ag["rule_template"] = rule_template
                if act == "sync_rule":
                    results.append(_ma_write_rule(ag, reg, dry_run))
                else:
                    results.append(_ma_write_mcp(ag, reg, dry_run))
            all_ok = all(r.get("ok") for r in results)
            return json.dumps({"ok": all_ok, "action": act, "target": target, "dry_run": dry_run, "results": results, "legacy_bak_ma_migration": migration}, ensure_ascii=False)
        if act == "sync_skill":
            if not skill_name:
                return _json_error("skill_name required for sync_skill", error_name="UNSUPPORTED_FIELD")
            explicit_target = (target or "all") != "all"
            ids = list(agents.keys()) if not explicit_target else [target]
            results = []
            for aid in ids:
                if aid not in agents:
                    results.append({"ok": False, "agent": aid, "status": "error", "reason": "not registered"})
                    continue
                results.append(_ma_write_skill({"id": aid, **agents[aid]}, reg, skill_name, dry_run, explicit_target))
            all_ok = all(r.get("ok") for r in results)
            return json.dumps({"ok": all_ok, "action": act, "target": target, "skill_name": skill_name, "dry_run": dry_run, "results": results, "legacy_bak_ma_migration": migration}, ensure_ascii=False)
        # W7 (F020 sweep, 2026-08-19): this used error_name=UNSUPPORTED_FIELD with
        # accepted_fields=[...enum values...]. Both halves were wrong and they
        # compounded: nothing is wrong with the FIELD (action is declared and
        # required), the VALUE is; and accepted_fields is contractually a list of
        # parameter NAMES, so a CA reading it tried to resend with a field literally
        # called "register". Use the same shape as the other enum guards
        # (orchestrator_server source_type, adaptive.py INVALID_SCOPE): name the
        # field, list accepted_values. The enum is already in the schema, so this
        # just makes the runtime error agree with it.
        return _json_error(
            f"action must be one of: {', '.join(_MANAGE_AGENT_ACTIONS)} (got: {action!r})",
            error_name="SCHEMA_VIOLATION",
            field="action",
            accepted_values=list(_MANAGE_AGENT_ACTIONS),
            ca_next_action="retry_with_valid_action",
        )
    except Exception as exc:
        return _json_error(str(exc))


_COUNCIL_CONFIG_CACHE = {"mtime": 0.0, "data": None}


def _council_config_path() -> Path:
    return Path(__file__).parent / ".council" / "council_config.json"


def _council_leader_skill_path() -> Path:
    # D4 (2026-08-11): the historical `.ToolA_Skill/24_council_tr.md` shim was
    # retired (folder deleted 2026-06-19, manifest records replacement by
    # 00_skill_router / 04_tool_tiers / 07_error_schema_hints). The actual
    # leader-rule source of truth is `.council/council_rule_leader.md`, which
    # convene_council already exposes as `leadership_rule` text. Resolve here
    # so the `council_leader_skill` payload carries the real contract instead
    # of a pointer to a missing canonical path.
    return Path(__file__).parent / ".council" / "council_rule_leader.md"


def _load_council_leader_skill() -> dict:
    """Return the council leader skill contract, or an explicit error marker."""
    path = _council_leader_skill_path()
    info = {
        "name": "council_rule_leader",
        "path": str(path),
    }
    try:
        if not path.exists():
            info.update({"text": "", "error": "missing", "missing": True})
            return info
        text = path.read_text(encoding="utf-8")
        info.update({
            "text": text,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "missing": False,
        })
        return info
    except Exception as exc:
        info.update({"text": "", "error": f"read_failed: {exc}", "missing": False})
        return info


def _load_council_config(force: bool = False):
    """Load .council/council_config.json — the SINGLE SOURCE OF TRUTH for council
    config. Returns a dict, or None on any failure so callers fall back to the
    hardcoded defaults (this function never raises)."""
    try:
        path = _council_config_path()
        if not path.exists():
            return None
        mt = path.stat().st_mtime
        cache = _COUNCIL_CONFIG_CACHE
        if not force and cache["data"] is not None and cache["mtime"] == mt:
            return cache["data"]
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or not isinstance(data.get("agents"), dict):
            return None
        cache["mtime"] = mt
        cache["data"] = data
        return data
    except Exception:
        return None


def _council_enabled_agents(cfg: dict):
    """Return [(agent_id, meta)] for enabled agents, in roster_order then dict order."""
    agents = cfg.get("agents", {})
    order = cfg.get("roster_order") or list(agents.keys())
    seen = set()
    out = []
    for aid in order:
        m = agents.get(aid)
        if m and aid not in seen and m.get("enabled", True):
            out.append((aid, m)); seen.add(aid)
    for aid, m in agents.items():
        if aid not in seen and m.get("enabled", True):
            out.append((aid, m)); seen.add(aid)
    return out


def _council_build_from_config(cfg: dict):
    """Build (roster, route_capabilities, coordination_rule, parallelism_rule, mission,
    messaging_to_enum) from the SOT config."""
    roster = []
    for aid, m in _council_enabled_agents(cfg):
        roster.append({
            "agent": m.get("label", aid),
            "bridge_agent": m.get("bridge", aid),
            "role": m.get("role", ""),
            "cli_channel": m.get("cli_channel", ""),
        })
    route_capabilities = {"selection_rule": "ROUTE RULE SOT = Anh chỉ đạo. Do not use a cost chain. Benchmark 2026-07-25 table SUSPENDED."}
    for aid, m in _council_enabled_agents(cfg):
        if aid == "clau":
            line = "ONLY agent allowed MCP: use MCP (call_tool) for T4 (runtime/DOM/CDP/browser) + high-stakes stateful; HTTP /api/call otherwise"
        else:
            line = "HTTP /api/call ONLY; never MCP — route any T4 to clauC"
        if m.get("free"):
            line += ". FREE worker — prefer for token-heavy scan/test/search"
        if m.get("can_write_A"):
            line += ". Allowed to WRITE [A] itself"
        route_capabilities[m.get("label", aid)] = line
    return (
        roster,
        route_capabilities,
        cfg.get("coordination_rule", ""),
        cfg.get("parallelism_rule", ""),
        cfg.get("mission", ""),
        cfg.get("messaging_to_enum", []),
    )


# Fallback hardcoded council spec, used only when council_config.json is missing/invalid.
_COUNCIL_FALLBACK_ROSTER = [
    {"agent": "clauC", "bridge_agent": "clau", "role": "Judgement: final verification, limited patching, T4/MCP only when explicitly delegated", "cli_channel": 'claudeltnai --print --model claude-opus-5 -p "<prompt>"  [diagnostic only; dispatch via council_message; log .council\\log_clau.txt]'},
    {"agent": "opeC", "bridge_agent": "ope", "role": "OpenCode worker; live pin grok-4.6-build; no suspended Qwen default", "cli_channel": 'opencode run "<prompt>" --model openai-compatible/grok-4.6-build --dangerously-skip-permissions  [diagnostic only; dispatch via council_message; log .council\\log_ope.txt]'},
    {"agent": "codoC", "bridge_agent": "codo", "role": "High-end grok-4.6-build worker via CodexLTNAI for judgement, independent audit, high-difficulty development; never the default leader", "cli_channel": 'codexltnai exec "<prompt>" --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check  [diagnostic only; dispatch via council_message; log .council\\log_codo.txt]'},
    {"agent": "kiloC", "bridge_agent": "kilo", "role": "Kilo harness; no Anh-directed default route cell; do not apply suspended MiniMax default", "cli_channel": 'kilo run "<prompt>" --model ltnai/glm-5.2 --format json --dangerously-skip-permissions  [diagnostic only; dispatch via council_message; log .council\\log_kilo.txt; cwd set by subprocess not --dir]'},
    {"agent": "PiC", "bridge_agent": "com", "role": "Pi harness; no Anh-directed default route cell; do not apply suspended Qwen/DeepSeek defaults", "cli_channel": 'pi -p "<prompt>" --yolo --skip-onboarding  [diagnostic only; dispatch via council_message; log .council\\log_pic.txt]'},
    {"agent": "HermC", "bridge_agent": "hermes", "role": "general worker (Hermes); BROKER-ONLY one-shot; web/file by default, browser only when explicitly required", "cli_channel": 'hermes chat -q "<prompt>" --yolo -t web,file [-m <model>] [--provider <prov>]  [diagnostic only; dispatch via council_message; log .council\\log_hermes.txt]'},
]
_COUNCIL_FALLBACK_CAPABILITIES = {
    "selection_rule": "ROUTE RULE SOT = Anh chỉ đạo. Do not use a cost chain. Benchmark 2026-07-25 table SUSPENDED. ClauC is outside normal routing order.",
    "clauC": "Judgement/final verification; ONLY worker allowed MCP for T4; HTTP /api/call otherwise",
    "codoC": "HTTP /api/call ONLY; never MCP. Optional high-context worker when explicitly routed",
    "clinC": "DISABLED fallback entry; do not route unless explicitly re-enabled in council_config.json",
    "PiC": "HTTP /api/call ONLY; never MCP. Single sequential worker; model routed via HTTP council_worker_model before dispatch",
    "opeC": "HTTP /api/call ONLY; never MCP. FREE worker — prefer for token-heavy scan/test/search",
    "HermC": "HTTP /api/call ONLY; never MCP. FREE worker — prefer for token-heavy scan/test/search",
}
_COUNCIL_FALLBACK_COORDINATION = "The user-triggered desktop CA is temporary leader. ROUTE RULE SOT = Anh chỉ đạo. Never a cost chain. Benchmark 2026-07-25 table SUSPENDED. CodoC is a high-end worker, not default leader. ClauC is outside normal routing order and explicit exceptional T4/scoped work only. Always set+get; no silent substitution."
_COUNCIL_FALLBACK_PARALLELISM = "Leader MAY dispatch independent worker tasks in PARALLEL (workers run over HTTP /api/call — parallel-safe). Serialize ONLY tasks that WRITE the SAME project concurrently. Writes to [A] itself restricted to clauC or codoC ONLY."


async def convene_council(project_path: str = "") -> str:
    """Return the CLI-only council protocol for trigger receivers.

    Roster, cost routing, coordination/parallelism rules, mission and the
    messaging enum are read LIVE from the SOT .council/council_config.json. If
    that file is missing or invalid, hardcoded fallbacks are used instead."""
    bridge_dir = Path(__file__).parent / ".council"
    cfg = _load_council_config()
    if cfg:
        (roster, route_capabilities, coordination_rule, parallelism_rule,
         mission, messaging_to_enum) = _council_build_from_config(cfg)
        guardrail_harness_rule = cfg.get("guardrail_harness_rule", "")
        task_routing = cfg.get("task_routing", {})
        config_source = str(_council_config_path())
    else:
        roster = list(_COUNCIL_FALLBACK_ROSTER)
        route_capabilities = dict(_COUNCIL_FALLBACK_CAPABILITIES)
        coordination_rule = _COUNCIL_FALLBACK_COORDINATION
        parallelism_rule = _COUNCIL_FALLBACK_PARALLELISM
        mission = ""
        messaging_to_enum = ["ope", "codo", "pic", "hermes", "clau", "kilo", "user"]
        guardrail_harness_rule = ""
        task_routing = {}
        config_source = "(fallback: council_config.json missing/invalid)"
    payload = {
        "ok": True,
        "roster": roster,
        "config_source": config_source,
        "bridge_dir": str(bridge_dir),
        "message_format": {
            "request": "<<MSG id=N to=X status=NEW>>...<<END N>>",
            "reply": "<<RES id=N from=X status=DONE>>...<<END N>>",
            "files": ["PROTOCOL.md", "inbox_<bridge_agent>.md", "outbox_<bridge_agent>.md", "STATE.md", "metrics_<bridge_agent>.json"],
        },
        "cli_identity_rule": "Desktop/current CA identities use names without C (Clau, Codo, Ope...); CLI council workers use leading-cap C-suffixed names (ClauC, CodoC, OpeC, PiC, HermC, KiloC). Desktop CAs must refer to themselves without C. Bridge file stems stay legacy lowercase without C.",
        "addressing_rule": "Route messages by worker id. Do not require salutations; identity comes from the worker label and persona, not from a greeting.",
        "verify_outbox_rule": "VERIFY ONLY / no-edits prohibits source/runtime changes, but appending the required RES block to the worker's own outbox is mandatory and is not considered a forbidden edit.",
        "prompt_style_rule": "Worker prompts should be short Vietnamese with correct diacritics; avoid ambiguous 'Chao Anh' headers in agent-to-agent prompts.",
        "leadership_rule": "The desktop/current CA that receives Anh's exact user-only trigger `bat hoi dong tr` / `bật hội đồng tr` is the temporary council LEADER unless Anh explicitly delegates leadership. Continuation gate: before coordinating or dispatching any council work (`convene_council`, `council_message`, `council_worker_model`, worker routing, brainstorm rounds, multi-worker artifacts), load `leader-council-skill` first even without the exact trigger; never apply this inside broker/worker prompts.",
        "leader_frugality_rule": "Temporary leader = reason + direct ONLY: scope, plan, route workers only from Anh chỉ đạo or live pin after SET+GET, then delegate anything more expensive than directing. ClauC is Judgement, not the default coordinator.",
        "worker_prompt_route_rule": "BROKER-ONLY: every worker runs one-shot with NO context/session retention. Send each task as a SELF-CONTAINED Council Message; embed any required prior context directly in the prompt — workers do not remember previous turns.",
        "broker_only_rule": "Context mode has been REMOVED from [A]. All workers (ClauC/OpeC/CodoC/PiC/HermC/KiloC) run BROKER one-shot only: fresh process each dispatch, no --resume/--id/session continuity. Provider+model is set through HTTP council_worker_model (writes .council/worker_models.json, applied on next dispatch, no restart); runtime supports clau|codo|pic|ope|hermes|kilo though schema text may lag. Verify HermC slots with action=get before relying on a switch. For PiC, route exactly one model per task and dispatch sequentially.",
        "messaging_rule": "CA/worker messaging uses the [A] tool council_message via POST /api/call/council_message with {sender,to,text,timeout_sec?}. The dashboard may use /api/council/message internally. Each message is an independent one-shot task. Route by target worker id and read cross-agent replies from .council\\broker_filtered.jsonl.",
        "broker_read_rule": "Workers read cross-agent responses from .council\\broker_filtered.jsonl. It mirrors the broker UI transcript, excludes raw/reasoning fields, and exposes only filtered log filenames rather than absolute paths.",
        "parallelism_rule": parallelism_rule,
        "io_language": "en",
        "coordination_rule": coordination_rule,
        "task_routing": task_routing,
        "guardrail_harness_rule": guardrail_harness_rule,
        "mission": mission,
        "messaging_to_enum": messaging_to_enum,
        "dispatch_rule": "[A] dispatches workers internally with subprocess argv (and stdin for codoC) to avoid shell quoting/Unicode corruption. CA/leader code must not hand-build shell commands, curl, or utf8_runner calls; use council_message instead. The cli_channel strings are descriptive only for diagnostics/dashboard display.",
        "liveness_rule": "The /council dashboard detects working/idle by live OS process scan (psutil cmdline signature), NOT by any metrics flag. Do NOT write a status field; dispatch through council_message.",
        "route_capabilities": route_capabilities,
        "council_leader_skill": _load_council_leader_skill(),
    }
    if project_path:
        payload["project_path"] = project_path
    return json.dumps(payload, ensure_ascii=False)


def _council_roster_labels(cfg: dict):
    """Enabled agent labels in roster_order (display order)."""
    return [str(m.get("label", aid)).casefold() for aid, m in _council_enabled_agents(cfg)]


def _council_retired_labels(cfg: dict):
    """Labels of rows kept with enabled:false -- retired, but preserved for re-enable.

    A derived doc that still names one of these is stale, not merely verbose: the agent
    cannot be dispatched at all, so a route row or a greeting line for it can only send a
    leader at a worker that will refuse. Kept separate from _council_roster_labels because
    the two are checked in OPPOSITE directions -- enabled labels must be present, retired
    labels must be absent.
    """
    live = {aid for aid, _ in _council_enabled_agents(cfg)}
    return [str(m.get("label", aid)).casefold()
            for aid, m in (cfg.get("agents") or {}).items() if aid not in live]


def _council_doc_targets() -> list:
    """Derived rule-doc files that should stay consistent with the SOT config."""
    root = Path(__file__).parent
    return [
        root / "examples" / "CORE_RULE.txt",
        root / ".council" / "council_rule_leader.md",
        root / ".council" / "global_rule_worker.md",
    ]


_COUNCIL_BACKTICK_LIST_RE = re.compile(r"`clauC`(?:\s*,\s*`[A-Za-z]+`)+", re.IGNORECASE)
_COUNCIL_CORE_RULE_TARGET = "CORE_RULE.txt"
_COUNCIL_CORE_POINTER_CONFIG = ".council/council_config.json"
_COUNCIL_CORE_POINTER_DOCS = ("council_rule_leader.md", "convene_council")


def _council_core_rule_pointer_ok(text: str) -> bool:
    """CORE_RULE v4 points to council SOT instead of duplicating routing data."""
    return _COUNCIL_CORE_POINTER_CONFIG in text and any(marker in text for marker in _COUNCIL_CORE_POINTER_DOCS)


async def council_config_diff(project_path: str = "") -> str:
    """READ-ONLY. Verify task-fit/no-cost-chain council routing markers."""
    try:
        cfg = _load_council_config(force=True)
        if not cfg:
            return _json_error("council_config.json missing or invalid")
        roster_labels = _council_roster_labels(cfg)
        retired_labels = _council_retired_labels(cfg)
        drift = []
        for path in _council_doc_targets():
            if not path.exists():
                drift.append({"target": path.name, "status": "missing_file", "detail": str(path)})
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.name == _COUNCIL_CORE_RULE_TARGET:
                ok = _council_core_rule_pointer_ok(text)
                drift.append({"target": path.name, "status": "in_sync" if ok else "drift", "detail": "SOT pointer present" if ok else "missing SOT pointer"})
                continue
            low = text.casefold()
            missing = [label for label in roster_labels if label.casefold() not in low]
            # A half-finished retirement used to be INVISIBLE here. This asked only whether
            # every ENABLED label was present, so council_rule_leader.md kept a full route
            # row, a suspended-defaults cell and a greeting line for two retired workers
            # while this tool answered "0 drift / 3 doc targets" (measured 2026-09-20).
            # Word boundary, not substring: the label "pic" sits inside "explicit".
            retired_present = [label for label in retired_labels
                               if re.search(rf"\b{re.escape(label)}\b", low)]
            no_chain = "no fixed chain" in low or "no cost chain" in low or "no fixed cost chain" in low or "do not use a cost chain" in low
            codo_worker = "not default leader" in low or "not the default leader" in low
            clau_exception = ("clauc is excluded from normal routing order" in low
                              or "clauc is outside the normal routing order" in low
                              or "clauc is outside normal routing order" in low)
            detail = []
            if missing: detail.append(f"missing labels: {', '.join(missing)}")
            if retired_present:
                detail.append(f"retired labels still present: {', '.join(retired_present)}")
            if not no_chain: detail.append("missing no-cost-chain marker")
            if not codo_worker: detail.append("missing CodoC-not-default-leader marker")
            if not clau_exception: detail.append("missing ClauC-exception marker")
            drift.append({"target": path.name, "status": "drift" if detail else "in_sync", "detail": "; ".join(detail)})
        n_drift = sum(1 for item in drift if item["status"] != "in_sync")
        return json.dumps({"ok": True, "config_source": str(_council_config_path()),
            "enabled_agents": [{"id": aid, "label": meta.get("label", aid), "can_write_A": bool(meta.get("can_write_A"))} for aid, meta in _council_enabled_agents(cfg)],
            "selection_rule": "anh_chi_dao_benchmark_20260725_suspended", "fixed_cost_chain": False,
            "normal_routing_excludes": ["ClauC"], "roster_labels": roster_labels,
            "retired_labels": retired_labels, "drift": drift,
            "summary": f"{n_drift} drift / {len(drift)} doc targets",
            "next_action": "update canonical derived docs" if n_drift else "all docs in sync"}, ensure_ascii=False)
    except Exception as exc:
        return _json_error(str(exc))


async def council_config_sync(project_path: str = "", run_sync_rule: bool = True) -> str:
    """Verify SOT-derived docs and optionally sync the canonical core rule to all CAs. No cost chain is generated."""
    try:
        cfg = _load_council_config(force=True)
        if not cfg:
            return _json_error("council_config.json missing or invalid")
        diff_result = json.loads(await council_config_diff(project_path=project_path))
        sync_rule_result = None
        if run_sync_rule:
            try:
                sync_rule_result = json.loads(await manage_agent(action="sync_rule", target="all"))
            except Exception as exc:
                sync_rule_result = {"ok": False, "error": str(exc)}
        return json.dumps({"ok": bool(diff_result.get("ok")), "config_source": str(_council_config_path()),
            "selection_rule": "anh_chi_dao_benchmark_20260725_suspended", "fixed_cost_chain": False,
            "normal_routing_excludes": ["ClauC"], "drift": diff_result.get("drift", []),
            "sync_rule": ({"ran": True, "ok": bool(sync_rule_result and sync_rule_result.get("ok"))} if run_sync_rule else {"ran": False}),
            "note": "convene_council reads council_config.json live; Python restart refreshes this tool contract."}, ensure_ascii=False)
    except Exception as exc:
        return _json_error(str(exc))


async def council_route_scorecard(project_path: str = "", min_samples: int = 5, limit: int = 0, write_artifacts: bool = True) -> str:
    """T1 route measurement scorecard. Reads normalized evidence; never fabricates capacity."""
    try:
        root = Path(project_path).resolve() if project_path else Path(_COUNCIL_TRACKER_ROOT)
        council_dir = root / ".council"
        rows = _route_load_measurements(council_dir / "route_measurements.jsonl", limit=max(0, int(limit)))
        scorecard = _route_build_scorecard(rows, min_samples=max(1, int(min_samples)))
        json_path = council_dir / "route_scorecard.json"
        markdown_path = council_dir / "route_scorecard.md"
        if write_artifacts:
            _route_write_scorecard_files(scorecard, json_path, markdown_path)
        return json.dumps({"ok": True, "measurement_path": str(council_dir / "route_measurements.jsonl"),
            "scorecard_json": str(json_path), "scorecard_markdown": str(markdown_path),
            "artifacts_written": bool(write_artifacts), **scorecard}, ensure_ascii=False)
    except Exception as exc:
        return _json_error(str(exc), error_name="COUNCIL_ROUTE_SCORECARD_FAILED")


# -- Council live-log tracker (read/collect) --
_COUNCIL_TRACKER_ROOT = r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator"
_COUNCIL_TRACKER_SCRIPT = os.path.join(_COUNCIL_TRACKER_ROOT, "COUNCIL_TRACKER.py")
_COUNCIL_TRACKER_OUT_DIR = os.path.join(_COUNCIL_TRACKER_ROOT, ".council", "Live_council")
_COUNCIL_TRACKER_OUT_FILE = os.path.join(_COUNCIL_TRACKER_OUT_DIR, "council_live.txt")
_COUNCIL_TRACKER_ARCHIVE_DIR = os.path.join(_COUNCIL_TRACKER_OUT_DIR, "archives")


async def council_tracker(open_file: bool = False, tail_lines: int = 80,
                          return_full: bool = False, save_output: bool = True,
                          diagnostic_full_prompts: bool = False) -> str:
    """READ/COLLECT (non-source-mutating). Run the council live-log collector and
    return the path to the snapshot so the CA/worker can read full council data.

    What it collects into one file (council_live.txt): broker dispatches + full
    prompts, full raw PiC ARGV/output, council_events, line deltas, inbox/outbox
    tails, runtime_state and the latest cross-agent replies. Use this FIRST when
    investigating council / broker / worker-dispatch / prompt / reply issues instead
    of hand-scraping `.council` logs.

    Behavior:
      - save_output=true (default): fresh collection — the previous council_live.txt
        is zipped into archives/ then deleted, and a new snapshot is written.
      - save_output=false: skip collection; just read the current council_live.txt.
      - Auto-open is DISABLED: `open_file` is accepted but ignored (no-op); the tool
        returns `output_path` only and never launches a desktop window.

    Returns: ok, output_path, archive_dir, archive_created (zip path or 'none'),
    bytes, line_count, and `excerpt` (tail) or `content` (when return_full=true)."""
    import sys as _sys
    try:
        tail_lines = int(tail_lines)
    except (TypeError, ValueError):
        tail_lines = 80
    if tail_lines < 0:
        tail_lines = 0

    archive_created = "none"
    if save_output:
        if not os.path.isfile(_COUNCIL_TRACKER_SCRIPT):
            return _json_error(
                f"COUNCIL_TRACKER.py not found at {_COUNCIL_TRACKER_SCRIPT}",
                error_name="COUNCIL_TRACKER_SCRIPT_MISSING",
            )
        before = set()
        if os.path.isdir(_COUNCIL_TRACKER_ARCHIVE_DIR):
            try:
                before = set(os.listdir(_COUNCIL_TRACKER_ARCHIVE_DIR))
            except OSError:
                before = set()
        env = dict(os.environ)
        env["COUNCIL_TRACKER_NO_OPEN"] = "1"   # headless: never auto-open for tool use
        env.pop("COUNCIL_TRACKER_OPEN", None)
        try:
            proc = subprocess.run(
                [_sys.executable, _COUNCIL_TRACKER_SCRIPT],
                cwd=_COUNCIL_TRACKER_ROOT, env=env,
                capture_output=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            return _json_error("council tracker run timed out after 120s",
                               error_name="COUNCIL_TRACKER_TIMEOUT")
        except Exception as exc:
            return _json_error(f"failed to run council tracker: {exc}",
                               error_name="COUNCIL_TRACKER_RUN_FAILED")
        if proc.returncode != 0:
            stderr_tail = (proc.stderr or b"").decode("utf-8", errors="replace")[-800:]
            return _json_error(
                f"council tracker exited with code {proc.returncode}",
                error_name="COUNCIL_TRACKER_NONZERO",
                returncode=proc.returncode, stderr=stderr_tail,
            )
        after = set()
        if os.path.isdir(_COUNCIL_TRACKER_ARCHIVE_DIR):
            try:
                after = set(os.listdir(_COUNCIL_TRACKER_ARCHIVE_DIR))
            except OSError:
                after = set()
        new_files = sorted(after - before)
        if new_files:
            archive_created = os.path.join(_COUNCIL_TRACKER_ARCHIVE_DIR, new_files[-1])

    if not os.path.isfile(_COUNCIL_TRACKER_OUT_FILE):
        return _json_error(
            f"council_live.txt not found at {_COUNCIL_TRACKER_OUT_FILE}",
            error_name="COUNCIL_LIVE_MISSING",
            hint="Call council_tracker with save_output=true to generate a fresh snapshot.",
        )
    try:
        with open(_COUNCIL_TRACKER_OUT_FILE, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as exc:
        return _json_error(f"failed to read council_live.txt: {exc}",
                           error_name="COUNCIL_LIVE_READ_FAILED")

    all_lines = content.splitlines()
    line_count = len(all_lines)
    try:
        byte_count = os.path.getsize(_COUNCIL_TRACKER_OUT_FILE)
    except OSError:
        byte_count = len(content.encode("utf-8", errors="replace"))

    result = {
        "ok": True,
        "output_path": _COUNCIL_TRACKER_OUT_FILE,
        "archive_dir": _COUNCIL_TRACKER_ARCHIVE_DIR,
        "archive_created": archive_created,
        "bytes": byte_count,
        "line_count": line_count,
        "collected": bool(save_output),
        "open_file": False,  # auto-open intentionally disabled for tool use
        "historical": True,
        "hint": (f"Historical council snapshot at {_COUNCIL_TRACKER_OUT_FILE}. "
                 "Use the bounded excerpt by default; set "
                 "diagnostic_full_prompts=true only for explicit prompt debugging."),
    }
    if return_full and diagnostic_full_prompts:
        result["content"] = content
        result["diagnostic_full_prompts"] = True
    else:
        bounded_tail = max(0, min(tail_lines, 120))
        result["excerpt"] = "\n".join(all_lines[-bounded_tail:]) if bounded_tail else ""
        result["full_content_available"] = bool(content)
    return json.dumps(result, ensure_ascii=False)


TOOL_MAP = build_tool_map(BASE_TOOL_MAP_SPECS, globals())

# V15 R8 — Register Round B new tools from tools_v15.py
try:
    import tools_v15 as _v15
    _V15_NEW_TOOLS = build_tool_map(V15_NEW_TOOL_SPECS, {**globals(), "_v15": _v15})
    TOOL_MAP.update(_V15_NEW_TOOLS)
    _write_transaction_v15 = _v15.write_transaction

    async def write_transaction(operations: list, project_path: str = "",
                                backup: bool = True,
                                structural_check: bool = True,
                                skip_verify: bool = False) -> str:
        """Dispatch the atomic transaction implementation."""
        if skip_verify:
            return _json_error(
                "skip_verify is unsupported for atomic transactions; verification and rollback cannot be disabled.",
                error_name="SKIP_VERIFY_UNSUPPORTED",
                ca_next_action="retry_with_skip_verify_false",
            )
        raw = await _write_transaction_v15(
            operations=operations,
            project_path=project_path,
            backup=backup,
            structural_check=structural_check,
        )
        result = json.loads(raw)
        return json.dumps(result, ensure_ascii=False)

    TOOL_MAP["write_transaction"] = write_transaction

    # Register tier + schema for each new tool
    _V15_TIERS = dict(V15_TIERS)
    TOOL_PHASES.update(_V15_TIERS)
    PHASE_LABELS["T4"] = "Runtime / DOM / Browser / Server lifecycle"

    # Mark mutating T4 / T2 tools as protected (token required)
    PROTECTED_TOOL_NAMES.update({
        "write_transaction", "click", "fill", "evaluate_js",
        "desktop_click", "desktop_app_focus", "desktop_window_state",
        "open_browser_page",  # launches process
        # CAN-053: session_audit_summary is now pure T1 read (unprotected);
        # the mutating half moved to record_session_audit_checkpoint.
        "record_session_audit_checkpoint",
    })

    # Append minimal schemas for /api/tools discovery
    # V15 R40: see_also map — cross-tool references for edge cases / workarounds.
    # Surface in /api/tools and /api/tool_inspector so CA discovers alternatives
    # without reading skills. Each entry: {alt_tool: when/use_case description}.
    _R40_SEE_ALSO_MAP = {
        # Write pipeline
        "write_fix": {
            "write_transaction": "Multi-hunk same file (sort ops bottom-up to avoid line shift)",
            "search_code": "If you don't know line numbers, find anchor with search_code(mode='literal') first",
            "read_file_range": "Always read target range BEFORE write_fix to confirm content",
        },
        "write_transaction": {
            "write_fix": "Single-hunk patch (use this if only 1 change)",
            "backup_project": "Optional pre-batch full backup (write_transaction auto-creates .bak per op)",
        },
        "write_new_file": {
            "write_transaction": "Mixed batch (create new + edit existing) — set op.tool per entry",
        },
        # Folder dialog
        "fill_folder_dialog": {
            "folder_dialog_snapshot": "MANDATORY: discover dialog candidates BEFORE fill (verifies owner_pid + class)",
            "click_and_handle_folder_dialog": "Atomic click+fill combo if you have click_selector",
            "cancel_native_dialog": "Close dialog without filling (cleanup)",
            "invoke_app_function": "Fallback when native picker fails — call app handler directly",
        },
        "click_and_handle_folder_dialog": {
            "folder_dialog_snapshot": "If NEW_DIALOG_NOT_FOUND, snapshot without filter to see actual dialog",
            "invoke_app_function": "Fallback for custom Electron dialogs (no native picker)",
        },
        # Search / locate
        "search_code": {
            "find_symbol": "If looking for function/class definition (not arbitrary text)",
            "find_callers": "If looking for usage sites of a known symbol",
            "regex_search": "Alias for search_code(mode='regex') with flags param",
            "write_fix": "After finding anchor line, use write_fix(start_line, end_line) to patch",
        },
        # Idle gate / completion
        "assert_target_idle": {
            "classify_get_footage_outcome": "Domain-specific outcome (Rawcut copy flows)",
            "workflow_submit_step": "If in workflow, submit_step auto-calls assert_target_idle when [completion_signal: ...] tag present",
        },
        # Workflow
        "workflow_submit_step": {
            "workflow_get_current": "Re-read step without state change",
            "workflow_retry_step": "On fail, User decides retry",
            "workflow_skip_step": "On fail, User decides skip",
            "assert_target_idle": "Manual idle check before submit done (auto-fired if step has completion_signal)",
        },
        "workflow_start": {
            "workflow_list": "Discover available workflows first",
            "workflow_status": "Already started? Status returns current step body",
            "workflow_reset": "Fresh start (archives old state)",
        },
        # Session
        "open_session": {
            "list_sessions": "Check if session exists for project_path before opening",
            "close_session": "End session cleanly when done",
        },
        # Token/protected
        "restart_orchestrator_via_task": {
            "list_cdp_targets": "Verify port reachable after restart",
        },
        # V15 R41 — new entries
        "verify_active_electron_source": {
            "list_cdp_targets": "Get page_id first if not known",
            "read_console_errors": "If verify shows wrong file loaded, check console for clue",
            "read_file_range": "After verify, read the ACTUAL loaded file (not source mirror)",
        },
        "electron_devtools_log": {
            # see_also values are short action hints, not the deprecation banner
            # itself — the banner lives in the schema description and the
            # `deprecated: true` flag (D-07). Keep these hints strictly about
            # which live tool replaces which use case.
            "list_cdp_targets": "Drop-in replacement for target enumeration (same cdp_port arg)",
            "read_console_errors": "For console messages (attach first via cdp_attach_target)",
            "evaluate_js": "For arbitrary in-page log inspection",
        },
        "process_tree_inspect": {
            "list_cdp_targets": "If looking for Electron process, list_cdp_targets gives debugger URL",
            "desktop_window_list": "If looking for window/process visibility",
        },
        "list_cdp_targets": {
            "verify_active_electron_source": "After getting target_id, verify file actually loaded",
            "cdp_attach_target": "Attach to chosen target by id",
            "launch_electron_with_cdp": "If no targets found, launch app first",
        },
    }

    # V15 R41: deprecated tools — machine-readable flag so clients can detect
    # deprecation programmatically, not just from description text.
    _DEPRECATED_TOOLS = {
        "electron_devtools_log",  # R41 — use list_cdp_targets / read_console_errors / evaluate_js
    }

    def _v15_schema(name: str, description: str, params: dict, required: list = None) -> dict:
        # V15 R40: attach see_also if defined
        schema_function = {
            "name": name, "description": description,
            "parameters": {"type": "object", "properties": params,
                            "required": required or []},
        }
        if name in _R40_SEE_ALSO_MAP:
            schema_function["see_also"] = _R40_SEE_ALSO_MAP[name]
        # V15 R41: attach deprecated flag if defined
        if name in _DEPRECATED_TOOLS:
            schema_function["deprecated"] = True
        return {"type": "function", "function": schema_function}

    _V15_R8_NEW_SCHEMAS = [
        _v15_schema(name, description, properties, required)
        for name, description, properties, required in V15_R8_NEW_SCHEMA_SPECS
    ]
    TOOL_SCHEMAS = TOOL_SCHEMAS + _V15_R8_NEW_SCHEMAS
    for _schema in TOOL_SCHEMAS:
        _fn = _schema.get("function", {})
        _params = (_fn.get("parameters") or {}).get("properties") or {}
        if _fn.get("name") == "write_transaction":
            _params.setdefault("skip_verify", {"type": "boolean", "default": False,
                "description": "Skip the post-write verify-or-REVERT pass. Default false: "
                "every write is re-validated and, when validation fails, the batch is rolled "
                "back from its backup and the outcome comes back in `batch_verify`. True "
                "instead stamps `batch_verify_skipped` and LEAVES a broken write in place, so "
                "set it only for a deliberately mid-refactor write whose transient syntax "
                "error is expected -- then run validate_file yourself."})
        elif _fn.get("name") == "write_fix":
            _params.setdefault("skip_symbol_check", {"type": "boolean", "default": False,
                "description": "Pass true ONLY for an INTENDED rename or removal of a top-level "
                "symbol. By default write_fix re-parses the file after writing and, when a "
                "top-level def/class has disappeared, answers SYMBOL_DRIFT_AUTO_REVERT and "
                "REVERTS the write -- that is a guard, not a bug, so a deliberate rename must "
                "carry this flag in the SAME call. It also owns most of the wall clock on a huge "
                "file (measured 27.5s of a 70.6s write on 9.9MB), the other legitimate reason "
                "to set it."})
        elif _fn.get("name") == "apply_after_dry_run":
            _params.setdefault("skip_verify", {"type": "boolean", "default": False,
                "description": "Skip the post-write verify-or-REVERT pass on the write this "
                "applies. Same contract as write_transaction.skip_verify: default false "
                "re-validates and can roll back, true stamps `batch_verify_skipped`."})

except Exception as _v15_exc:
    import logging
    logging.warning(f"V15 R8 tools_v15 not loaded: {_v15_exc}")

# V15 R44 (P4) - professional huge-file & data inspection tools (read-only, T1).
# Source module tools_upgrade_p4.py: query_sqlite / file_profile / sample_data_file /
# read_chunks. Own try/except so a P4 import failure degrades ONLY P4, never V15.
try:
    import tools_upgrade_p4 as _p4
    _P4_FUNCS = {
        "query_sqlite": _p4.query_sqlite,
        "file_profile": _p4.file_profile,
        "sample_data_file": _p4.sample_data_file,
        "read_chunks": _p4.read_chunks,
    }
    TOOL_MAP.update(_P4_FUNCS)
    TOOL_PHASES.update({_p4_name: "T1" for _p4_name in _P4_FUNCS})
    _P4_SCHEMAS = [
        {"type": "function", "function": {
            "name": _p4_n, "description": _p4_d,
            "parameters": {"type": "object", "properties": _p4_props,
                           "required": _p4_req},
        }}
        for _p4_n, _p4_d, _p4_props, _p4_req in _p4.UPGRADE_P4_SCHEMA_SPECS
    ]
    TOOL_SCHEMAS = TOOL_SCHEMAS + _P4_SCHEMAS
except Exception as _p4_exc:
    import logging as _logging_p4
    _logging_p4.warning(f"P4 soft-data tools not loaded: {_p4_exc}")

# B5 (2026-08-16) - restore Phase 5 app-lifecycle / runtime-verification tools that
# were live on 2026-08-07 (OPE_REALTEST_A_TOOLS_20260807) but lost their registration
# block in the 0071641 squash snapshot. Source module tools_upgrade_p5.py: start_app /
# stop_app / http_check / browser_smoke_test. Phase T2 for all four (the module
# docstrings + schema descriptions declare T2, incl. "T2 READ/PROBE (CDP)" for
# browser_smoke_test). Own try/except so a P5 import failure degrades ONLY P5.
# SECURITY: gating lives in PROTECTED_TOOL_NAMES / TOKEN_REQUIRED_TOOLS /
# _MUTATION_SCOPE_MAP above (start_app -> run_project_command, stop_app ->
# kill_process are in-process calls that bypass the HTTP token gate, so the wrappers
# inherit the gate of what they wrap; browser_smoke_test follows open_browser_page).
try:
    import tools_upgrade_p5 as _p5
    _P5_FUNCS = {
        "start_app": _p5.start_app,
        "stop_app": _p5.stop_app,
        "http_check": _p5.http_check,
        "file_sha256": _p5.file_sha256,
        "browser_smoke_test": _p5.browser_smoke_test,
    }
    TOOL_MAP.update(_P5_FUNCS)
    TOOL_PHASES.update({_p5_name: "T2" for _p5_name in _P5_FUNCS})
    TOOL_PHASES["file_sha256"] = "T1"
    _P5_SCHEMAS = [
        {"type": "function", "function": {
            "name": _p5_n, "description": _p5_d,
            "parameters": {"type": "object", "properties": _p5_props,
                           "required": _p5_req},
        }}
        for _p5_n, _p5_d, _p5_props, _p5_req in (_p5.UPGRADE_P5_SCHEMA_SPECS + _p5.READ_EVIDENCE_SCHEMA_SPECS)
    ]
    TOOL_SCHEMAS = TOOL_SCHEMAS + _P5_SCHEMAS
except Exception as _p5_exc:
    import logging as _logging_p5
    _logging_p5.warning(f"P5 app-lifecycle tools not loaded: {_p5_exc}")

# V15 R47 (2026-08-12) - soft_data_reconcile as a native T1 read-only tool. Same engine
# as scripts/soft_data_reconcile.py: cross-layer tool-surface overlap/bloat/conflict report
# on [A]'s OWN surface. build() imports orchestrator_server (heavy late-registrations) so it
# MUST run OFF the event loop (asyncio.to_thread) - the on-loop-hang lesson. Own try/except
# so a load failure degrades only this tool. Default payload is compact (drops the ~240-row
# per-tool matrix); pass full=true for the whole matrix.
try:
    import asyncio as _asyncio_sdr
    from pathlib import Path as _Path_sdr

    async def soft_data_reconcile(project_path: str = "", full: bool = False) -> str:
        """T1 READ-ONLY: cross-layer tool-surface overlap/bloat/conflict report for [A]
        itself (schema/param + alias + see_also + skill + function + usage). ADVISORY - finds
        PARAM_CONCEPT_CONFLICT / AMBIGUOUS_PARAM_NAME / CONFUSABLE_PAIRS / DESC_BLOAT and
        DEFECTS (dangling see_also / broken alias target). Never edits anything."""
        _root = project_path or str(_Path_sdr(__file__).resolve().parent)

        def _build():
            import sys as _s
            _sd = str(_Path_sdr(__file__).resolve().parent / "scripts")
            if _sd not in _s.path:
                _s.path.insert(0, _sd)
            import soft_data_reconcile as _sdr_mod
            return _sdr_mod.build(_Path_sdr(_root))

        _report = await _asyncio_sdr.to_thread(_build)
        if not full:
            _report = {_k: _v for _k, _v in _report.items() if _k != "records"}
        return json.dumps(_report, ensure_ascii=False)

    TOOL_MAP["soft_data_reconcile"] = soft_data_reconcile
    TOOL_PHASES["soft_data_reconcile"] = "T1"
    TOOL_SCHEMAS = TOOL_SCHEMAS + [{
        "type": "function", "function": {
            "name": "soft_data_reconcile",
            "description": ("T1 READ-ONLY: cross-layer tool-surface overlap/bloat/conflict "
                            "report for [A] (schema/param + alias + see_also + skill + function "
                            "+ usage). ADVISORY, never edits. Engine = scripts/soft_data_reconcile.py."),
            "parameters": {"type": "object", "properties": {
                "project_path": {"type": "string", "default": "",
                                 "description": "Optional [A] root; defaults to this install."},
                "full": {"type": "boolean", "default": False,
                         "description": "Include the full ~240-row per-tool matrix (default compact)."},
            }, "required": []},
        },
    }]
except Exception as _sdr_exc:
    import logging as _logging_sdr
    _logging_sdr.warning(f"soft_data_reconcile tool not loaded: {_sdr_exc}")
for _schema in TOOL_SCHEMAS:
    _fn = _schema.get("function", {})
    _name = _fn.get("name")
    if not _name:
        continue
    _phase = tool_phase(_name)
    _fn["x_phase"] = _phase
    _fn["x_phase_label"] = PHASE_LABELS.get(_phase, PHASE_LABELS["T3"])

# CASE-1809-E: declare the R101 ``fields=`` projection on every literal schema above, which
# includes the four ``TOOL_SCHEMAS = TOOL_SCHEMAS + [...]`` concatenations (V15/R8, P4, P5,
# soft_data_reconcile) that an injection inside build_tool_schemas() would silently miss.
# This loop is NOT sufficient on its own: orchestrator_server and code_tools itself keep
# calling register_tool BELOW this point, so orchestrator.core.registry stamps there too --
# exactly how x_phase already has to be stamped in both places. The count is kept so a guard
# can prove this hook ran instead of inferring it.
# Nested import, like the soft_data_reconcile block above: a bare mid-file import would add a new
# E402 here, and ruff_fix on code_tools.py would rewrite 30 unrelated pre-existing rows. Keep the
# import line under 88 chars too, or isort answers with I001 and wants it wrapped.
try:
    from orchestrator.core.fields_param import stamp_all_fields_parameters
    _R101_FIELDS_STAMPED = stamp_all_fields_parameters(TOOL_SCHEMAS)
except Exception as _r101_exc:  # fail-open: boot must survive one malformed schema row
    import logging as _logging_r101
    _logging_r101.warning(f"R101 fields= declaration not stamped: {_r101_exc}")
    _R101_FIELDS_STAMPED = 0


# ── Tools manifest → [Q] Z2 (2026-07-12, CSLt tool-surface minimization) ──
# One intent-language line per tool, generated from the LIVE TOOL_SCHEMAS at
# boot and pushed to [Q] /ingest_path (zone Z2) only when content changed —
# fixes the stale-Z2 problem (Z2 held 07-07 code chunks while [A] evolved).
# QNight distills/embeds it, so "viec X dung tool nao" queries in [Q] answer
# from a fresh catalog. Fail-open: [Q] down = file still written, no error.
_TOOLS_MANIFEST_PATH = Path(__file__).parent / ".council" / "tools_manifest.md"


def _tools_manifest_text() -> str:
    lines = [
        "# [A] TOOLS MANIFEST (auto-generated from live TOOL_SCHEMAS - do not hand-edit)",
        "# Muc dich: tra loi 'viec X dung tool nao trong [A]' / which [A] tool for task X.",
        "# Moi dong: TOOL <ten> | tier | required fields | mo ta. Schema day du: GET /api/tools/<ten>/schema.",
        "",
    ]
    for _s in TOOL_SCHEMAS:
        _f = _s.get("function", {})
        n = _f.get("name")
        if not n:
            continue
        desc = re.sub(r"\s+", " ", str(_f.get("description") or "")).strip()[:280]
        req = ", ".join((_f.get("parameters") or {}).get("required") or []) or "-"
        lines.append(f"TOOL {n} | {tool_phase(n)} | required: {req} | {desc}")
    return "\n".join(lines) + "\n"


def _tools_manifest_sync() -> dict:
    try:
        text = _tools_manifest_text()
        new_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        old_sha = ""
        if _TOOLS_MANIFEST_PATH.exists():
            old_sha = hashlib.sha256(_TOOLS_MANIFEST_PATH.read_bytes()).hexdigest()
        if new_sha == old_sha:
            return {"ok": True, "changed": False, "sha": new_sha[:12]}
        _TOOLS_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TOOLS_MANIFEST_PATH.write_text(text, encoding="utf-8")
        import urllib.request as _ur
        body = json.dumps({
            "source_type": "manual_text", "zone": "Z2",
            "session_id": "a-tools-manifest", "project": "A-tools",
            "source_path": f"A:tools_manifest:{new_sha[:12]}",
            "content": text,
        }).encode("utf-8")
        req = _ur.Request("http://127.0.0.1:8082/ingest_path", data=body,
                          headers={"Content-Type": "application/json"})
        resp = json.loads(_ur.urlopen(req, timeout=15).read().decode("utf-8", "replace"))
        return {"ok": True, "changed": True, "sha": new_sha[:12],
                "q_ingest": {k: resp.get(k) for k in ("ok", "inserted", "skipped_dup")}}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


try:
    import threading as _th_manifest
    _t_manifest = _th_manifest.Timer(45.0, _tools_manifest_sync)
    _t_manifest.daemon = True
    _t_manifest.start()
except Exception:
    pass


# Rule↔code drift auditor — registered via the single pipeline (keeps map/schema/phase
# in lockstep; runs after all maps + the x_phase stamp loop above are populated).
try:
    from orchestrator.core.registry import register_tool as _register_rule_drift
    _register_rule_drift(
        name="rule_drift_check",
        handler=rule_drift_check,
        phase="T1",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "T1 audit: detect DRIFT between rule files (CORE_RULE/SLIM_RULE/"
                           "SLIM_RULE_INLINE) and live code (TOOL_MAP + enforcement sets + "
                           "council_config). Read-only. Surfaces rule refs to missing tools, "
                           "agent-list mismatches, and behavior-set inconsistencies "
                           "(phantom/orphan/coverage). Use after editing a rule to find related "
                           "code/rule parts needing update.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Project root (optional; checks operate on [A]'s own rule/code)."},
                    "checks": {"type": "string", "description": "Optional comma list to limit: tool_names,agent_list,behavior_sets (default = all)."},
                },
                "required": [],
            },
        },
    )
except Exception as _rdc_exc:
    import logging as _rdc_log
    _rdc_log.warning(f"rule_drift_check registration failed: {_rdc_exc}")

# ── CBM graph T1 tools (Phase 1 Step 11): additive registration via register_tool ──
try:
    from orchestrator.core.registry import register_tool as _register_cbm
    from orchestrator.graph.tools import (
        cbm_graph_status as _cbm_graph_status_handler,
    )
    from orchestrator.graph.tools import (
        cbm_index as _cbm_index_handler,
    )
    from orchestrator.graph.tools import (
        cbm_trace_path as _cbm_trace_path_handler,
    )

    _register_cbm(
        name="cbm_index",
        handler=_cbm_index_handler,
        phase="T2",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": ("T2 derived-index write: index project scope into graph.db. Source files remain read-only. "
                            "Explicit indexing has NO wall-clock deadline: it is governed by the progress watchdog and "
                            "cooperative cancel, so a repo-scale run may exceed the client HTTP timeout. That is not a "
                            "failure — poll write_status with the returned operation_id (or project_path) until "
                            "commit_state is terminal, and use /api/cancel to stop it; never resend while in_flight."),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root to index."},
                    "scope": {"type": "string", "description": ("core (orchestrator package only) or full (all eligible source). "
                                                                "Default: core. Duration scales with repo size and is watchdog-governed, "
                                                                "not capped — the old '<45s / <150s' ceilings no longer truncate a run.")},
                },
                "required": ["project_path"],
            },
        },
    )

    _register_cbm(
        name="cbm_graph_status",
        handler=_cbm_graph_status_handler,
        phase="T1",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "T1 read-only: report graph freshness, node/edge/file counts, last index run, stale file count, WAL state, extractor version. Fail-open (disabled/missing DB returns enabled=False payload).",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root."},
                },
                "required": ["project_path"],
            },
        },
    )

    _register_cbm(
        name="cbm_trace_path",
        handler=_cbm_trace_path_handler,
        phase="T1",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "T1 read-only: bounded graph traversal (BFS) from a start node. direction=out|in|both, depth<=5, node cap 50, timeout 2s, max_files=50, max_symbols=200. Cycle-safe. On limit hit returns truncated=true + truncation_reason. Fail-open on timeout/stale/missing, but an unresolvable start_node_id is REFUSED with ok:false + error_name=START_NODE_NOT_FOUND (B023): a bare symbol name is not a node id, and answering it with an empty ok:true trace was indistinguishable from a symbol that genuinely has no callers. Get a real id from cbm_template_query(template='callers', name='<symbol>'). The payload also carries start_node_resolved: true|false|null (null = never looked, e.g. GRAPH_ENABLED=0).",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root."},
                    "start_node_id": {"type": "string", "description": "Graph node id to traverse from (e.g. 'file.py:function:mod.fn:LINE')."},
                    "direction": {"type": "string", "description": "out | in | both. Default: out."},
                    "depth": {"type": "integer", "description": "Max BFS depth (clamped to 5). Default: 5."},
                },
                "required": ["project_path", "start_node_id"],
            },
        },
    )
except Exception as _cbm_exc:
    import logging as _cbm_log
    _cbm_log.warning(f"CBM graph tool registration failed: {_cbm_exc}")

# ── CBM graph P1 tools (Phase 3 Steps 14-17): additive registration ──
try:
    from orchestrator.core.registry import register_tool as _register_cbm_p1
    from orchestrator.graph.tools import (
        cbm_council_context as _cbm_council_context_handler,
    )
    from orchestrator.graph.tools import (
        cbm_cycle_point as _cbm_cycle_point_handler,
    )
    from orchestrator.graph.tools import (
        cbm_dead_code as _cbm_dead_code_handler,
    )
    from orchestrator.graph.tools import (
        cbm_get_architecture as _cbm_get_architecture_handler,
    )
    from orchestrator.graph.tools import (
        cbm_incremental_index as _cbm_incremental_index_handler,
    )
    from orchestrator.graph.tools import (
        cbm_template_query as _cbm_template_query_handler,
    )
    from orchestrator.graph.tools import (
        ruff_fix as _ruff_fix_handler,
    )

    _register_cbm_p1(
        name="cbm_template_query",
        handler=_cbm_template_query_handler,
        phase="T1",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "T1 read-only: named graph queries without Cypher. template=callers|callees|imports|imported_by|orphan_symbols|module_deps|entry_points|impact_scope. Resolve symbol via name= or node_id=; imports/imported_by take module=. Fail-open; unsupported template returns a clear error.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root."},
                    "template": {"type": "string", "description": "One of: callers, callees, imports, imported_by, orphan_symbols, module_deps, entry_points, impact_scope."},
                    "name": {"type": "string", "description": "Symbol name (resolved via graph lookup)."},
                    "node_id": {"type": "string", "description": "Exact graph node id (wins over name)."},
                    "module": {"type": "string", "description": "Module qualified_name for imports/imported_by."},
                    "depth": {"type": "integer", "description": "BFS depth for impact_scope (clamped to 5). Default: 2."},
                    "limit": {"type": "integer", "description": "Max results (clamped to 500). Default: 50."},
                    "offset": {"type": "integer", "default": 0, "description": "Pagination offset; truncated is derived as offset+count < total_count."},
                },
                "required": ["project_path", "template"],
            },
        },
    )

    _register_cbm_p1(
        name="cbm_get_architecture",
        handler=_cbm_get_architecture_handler,
        phase="T2",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "T2 derived-index-aware architecture overview. May incrementally update graph.db before returning bounded module/dependency data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root."},
                    "max_files": {"type": "integer", "description": "Optional module cap override; 0 or omitted keeps the config default GRAPH_CONTEXT_MAX_FILES=50."},
                    "max_symbols": {"type": "integer", "description": "Optional symbol cap override; 0 or omitted keeps the config default GRAPH_CONTEXT_MAX_SYMBOLS=200."},
                    "timeout_s": {"type": "number", "description": "Optional traversal timeout override in seconds; 0 or omitted keeps the config default GRAPH_CONTEXT_TIMEOUT_S=2.0."},
                },
                "required": ["project_path"],
            },
        },
    )

    _register_cbm_p1(
        name="cbm_dead_code",
        handler=_cbm_dead_code_handler,
        phase="T2",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "T2 derived-index-aware health scan. May update graph.db; never edits source. Findings remain advisory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root."},
                    "limit": {"type": "integer", "description": "Max candidates (clamped to 200). Default: 50."},
                    "scope": {"type": "string", "enum": ["full", "core"], "default": "full", "description": "Scan scope. core restricts to orchestrator/. Unlike every other cbm_* tool this is a SUBPROCESS SCAN OF REAL FILES, not a graph-index query, so scope decides how much work runs -- not just how much answer comes back. Narrow it before calling, and combine with file_filter for any other subtree."},
                    "file_filter": {"type": "string", "default": "", "description": "Optional glob relative to scan scope (recursive ** supported), e.g. 'src/**/*.js'. Restricts BOTH engines, and restricts the SCAN itself, not merely the findings. The response echoes file_filter, file_filter_applied and files_matched, so a glob that matched nothing is visible as files_matched:0 rather than looking like a full scan."},
                },
                "required": ["project_path"],
            },
        },
    )

    _register_cbm_p1(
        name="ruff_fix",
        handler=_ruff_fix_handler,
        phase="T2",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "T2 Ruff auto-fix. Defaults to preview: returns Ruff unified diff and does not edit. Set apply=true to apply safe fixes; unsafe_fixes is opt-in.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root."},
                    "path": {"type": "string", "default": ".", "description": "Project-relative file or folder. Must stay inside project_path."},
                    "apply": {"type": "boolean", "default": False, "description": "False previews the fix diff; true applies fixes."},
                    "unsafe_fixes": {"type": "boolean", "default": False, "description": "Opt in to Ruff unsafe fixes."},
                    "preserve_hash": {"type": "boolean", "default": False, "description": "Declare a hold/hash-pinned artifact. Refuses apply=true; preview remains allowed. All fixes including I001/F401 may break a pin."},
                },
                "required": ["project_path"],
            },
        },
    )
    _register_cbm_p1(
        name="cbm_incremental_index",
        handler=_cbm_incremental_index_handler,
        phase="T2",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "T2 derived-index write: incrementally update graph.db from source hashes. Source files remain read-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root."},
                    "scope": {"type": "string", "description": "core (orchestrator/ only) or full (all .py). Default: full."},
                },
                "required": ["project_path"],
            },
        },
    )

    _register_cbm_p1(
        name="cbm_council_context",
        handler=_cbm_council_context_handler,
        phase="T1",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "T1 read-only: bounded advisory graph context pack for council dispatch (Step 21). Leader embeds the block in worker prompts explicitly — never auto-injected, fail-open, never blocks dispatch. name= -> symbol + inbound blast radius; file_path= (name empty) -> all symbols in file + direct callers; omit both -> changed-files pack (detect_changes + direct callers). Bounds COUNCIL_CONTEXT_MAX_FILES=10 / COUNCIL_CONTEXT_MAX_SYMBOLS=50; truncated + truncation_reason list every cap hit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root."},
                    "name": {"type": "string", "description": "Optional symbol name; omitted = file_path or changed-files mode."},
                    "file_path": {"type": "string", "description": "Optional file path (relative to project root); when name is empty, returns all symbols in the file + direct callers. Takes precedence over changed-files mode."},
                    "depth": {"type": "integer", "description": "Impact BFS depth for symbol mode (clamped to 5). Default: 2."},
                },
                "required": ["project_path"],
            },
        },
    )

    _register_cbm_p1(
        name="cbm_cycle_point",
        handler=_cbm_cycle_point_handler,
        phase="T2",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "T2 derived-index-aware architectural health: refresh graph.db if needed, then compute cycles, levelization, depth and quality signals. Never edits source.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root holding .orchestrator_cache/graph.db (index it first via cbm_index or cbm_incremental_index)."},
                },
                "required": ["project_path"],
            },
        },
    )
except Exception as _cbm_p1_exc:
    import logging as _cbm_p1_log
    _cbm_p1_log.warning(f"CBM graph P1 tool registration failed: {_cbm_p1_exc}")

# --- A30-P5: OCR (open-code-review) delegation tools ---
# Its OWN try/except island on purpose. The CBM P1 block above ends at the line
# right before this comment; folding OCR into that block would mean one missing
# npm install could take every CBM graph tool offline with it.
# phase="T2" follows the ruff_fix template even though delegate mode mutates
# nothing (it only reads git diff): ocr_scan with preview_only=false is the one
# call in this family that reaches an external provider, so the three tools below
# are gated at the tier of that most expensive member. ocr_map is deliberately
# outside that gate and states its reason at its own registration.
try:
    from orchestrator.core.registry import register_tool as _register_ocr_p5
    from orchestrator.tools.ocr_review import (
        ocr_map as _ocr_map_handler,
    )
    from orchestrator.tools.ocr_review import (
        ocr_review as _ocr_review_handler,
    )
    from orchestrator.tools.ocr_review import (
        ocr_rule as _ocr_rule_handler,
    )
    from orchestrator.tools.ocr_review import (
        ocr_scan as _ocr_scan_handler,
    )

    _register_ocr_p5(
        name="ocr_review",
        handler=_ocr_review_handler,
        phase="T2",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "Semantic review layer beside ruff: open-code-review delegate preview lists which changed files are worth reviewing. Needs no LLM, costs 0 tokens, mutates nothing. git_stage and git_commit ALREADY run it by default (ocr:true) at two different scopes - git_stage returns mode=workspace (the WHOLE worktree, so unstaged churn and scratch files appear in reviewable_files even though they are not staged) while git_commit returns mode=commit (only the committed set) - so reading the stage-time list as 'what I am about to commit' is a misread. Call this tool directly only for an explicit from_ref/to_ref or commit range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute repo root to review."},
                    "from_ref": {"type": "string", "default": "", "description": "Git ref to diff from. Mutually exclusive with commit."},
                    "to_ref": {"type": "string", "default": "", "description": "Git ref to diff to. Mutually exclusive with commit."},
                    "commit": {"type": "string", "default": "", "description": "Single commit to review instead of a from/to range."},
                    "exclude": {"type": "string", "default": "", "description": "Comma-separated gitignore-style patterns to skip."},
                    "include_background": {"type": "boolean", "default": False, "description": "False reports the background field as a byte count; it inlines the whole commit message (~2KB measured)."},
                    "timeout": {"type": "integer", "default": 120, "description": "Seconds for the OCR subprocess ONLY -- it does NOT bound the call. Lane admission, the git diff that feeds OCR and result assembly all sit outside it, so wall clock can exceed this number; size a client timeout off measured wall clock, never off this value. Kept under the MCP proxy ceiling."},
                },
                "required": ["project_path"],
            },
        },
    )

    _register_ocr_p5(
        name="ocr_rule",
        handler=_ocr_rule_handler,
        phase="T2",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "open-code-review delegate rule: which review rule applies to given files. Needs no LLM. Large rule bodies are reported as byte counts unless include_rule_text=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute repo root."},
                    "paths": {"type": "array", "items": {"type": "string"}, "description": "Repo-relative files. A comma-separated string is also accepted. Must stay inside project_path."},
                    "commit": {"type": "string", "default": "", "description": "Single commit context. Mutually exclusive with from_ref/to_ref."},
                    "from_ref": {"type": "string", "default": "", "description": "Git ref to diff from."},
                    "to_ref": {"type": "string", "default": "", "description": "Git ref to diff to."},
                    "include_rule_text": {"type": "boolean", "default": False, "description": "False omits rule bodies over 512 bytes (one .py measured 7886 bytes) and reports rule_bytes instead."},
                    "compact": {"type": "boolean", "default": False, "description": "True replaces the system Python rule body with the 7-group checklist it condenses to (~100 tokens against ~1900), declaring verbatim_ocr_text=false. Groups that are not the system Python rule keep their byte count. Mutually exclusive with include_rule_text."},
                    "timeout": {"type": "integer", "default": 120, "description": "Seconds for the OCR subprocess ONLY -- it does NOT bound the call. Lane admission, rule lookup and result assembly all sit outside it, so wall clock can exceed this number; size a client timeout off measured wall clock, never off this value. Kept under the MCP proxy ceiling."},
                },
                "required": ["project_path", "paths"],
            },
        },
    )

    _register_ocr_p5(
        name="ocr_scan",
        handler=_ocr_scan_handler,
        phase="T2",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "open-code-review scan. preview_only=true (default) plans the review with no LLM and no token cost. preview_only=false needs a configured provider and refuses with OCR_LLM_NOT_CONFIGURED when none is set, instead of spending a call to find out. Scope is the WHOLE repo unless you pass path=, so on a large repo narrow it FIRST - ocr_map for a diff, cbm_dsm / cbm_get_architecture to pick the module worth reviewing - and read ca_hint_scope, which fires only when the returned list really was capped.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute repo root."},
                    "path": {"type": "string", "default": "", "description": "Repo-relative dir or file to limit the scan to. Must stay inside project_path."},
                    "exclude": {"type": "string", "default": "", "description": "Comma-separated gitignore-style patterns to skip."},
                    "preview_only": {"type": "boolean", "default": True, "description": "True passes --preview: planning only, no LLM, no token cost."},
                    "audience": {"type": "string", "default": "agent", "description": "human or agent."},
                    "output_format": {"type": "string", "default": "json", "description": "text, json or sarif. Maps to the CLI --format flag."},
                    "provider": {"type": "string", "default": "", "description": "Override the configured LLM provider. Ignored while preview_only=true."},
                    "model": {"type": "string", "default": "", "description": "Override the configured model. Ignored while preview_only=true."},
                    "timeout": {"type": "integer", "default": 150, "description": "Seconds for the OCR subprocess ONLY -- it does NOT bound the call. Lane admission, repo walk and result assembly all sit outside it, so wall clock can exceed this number; size a client timeout off measured wall clock, never off this value. Kept under the MCP proxy ceiling."},
                },
                "required": ["project_path"],
            },
        },
    )

    # A32-1.3: ocr_map registers T1, NOT T2 like the three siblings above, and the
    # tier sentence at the head of this island was narrowed to say so. That rule
    # ("gated at the tier of its most expensive member") exists for a tool that CAN
    # escalate: ocr_scan takes preview_only, so one argument decides whether a
    # provider is reached. ocr_map has no such parameter and no branch that can
    # spawn anything but `delegate preview` -- the same zero-LLM subprocess
    # ocr_review already runs -- so gating it T2 would charge a session to a tool
    # that cannot spend one. phase= is per-tool in register_tool, so this is a
    # declaration rather than a loophole: if ocr_map ever grows an argument that
    # reaches a provider, it moves to T2 in that same commit.
    _register_ocr_p5(
        name="ocr_map",
        handler=_ocr_map_handler,
        phase="T1",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "Navigation map of a change, cbm_graph-shaped: which files a diff actually lives in, sorted by changed-line count, with every dropped file counted by reason. Same zero-LLM subprocess as ocr_review. Read-only, 0 tokens, no session. Call it BEFORE reading a diff: it names the few files that carry the change, so the follow-up read is one bounded range instead of a git_diff dump - then cbm_what_if / cbm_dsm on those same files gives blast radius before any edit. git_stage and git_commit already attach ocr_review by default, so this is pre-commit navigation, not a duplicate review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute repo root to map."},
                    "from_ref": {"type": "string", "default": "", "description": "Git ref to diff from. Mutually exclusive with commit."},
                    "to_ref": {"type": "string", "default": "", "description": "Git ref to diff to. Mutually exclusive with commit."},
                    "commit": {"type": "string", "default": "", "description": "Single commit to map instead of a from/to range."},
                    "exclude": {"type": "string", "default": "", "description": "Comma-separated gitignore-style patterns, handed to OCR itself."},
                    "min_changed_lines": {"type": "integer", "default": 10, "description": "A file with fewer insertions+deletions than this is dropped as below_min_changed_lines and counted in excluded_summary. 0 keeps every file: a 6-line edit can be a real production change, so the threshold is declared and overridable rather than silent."},
                    "include_excluded": {"type": "boolean", "default": False, "description": "True adds excluded_files[] with a path and reason per drop. False still reports excluded_summary counts, so no drop is invisible."},
                    "timeout": {"type": "integer", "default": 120, "description": "Seconds for the OCR subprocess ONLY -- it does NOT bound the call. Lane admission, the git diff that feeds OCR and result assembly all sit outside it, so wall clock can exceed this number; size a client timeout off measured wall clock, never off this value. Kept under the MCP proxy ceiling."},
                },
                "required": ["project_path"],
            },
        },
    )
except Exception as _ocr_p5_exc:
    import logging as _ocr_p5_log
    _ocr_p5_log.warning(f"OCR P5 tool registration failed: {_ocr_p5_exc}")

# ── CAN-053 P2: raw graph browse tools (codo: no public API to browse nodes) ──
try:
    from orchestrator.core.registry import register_tool as _register_cbm_p2
    from orchestrator.graph.tools import (
        cbm_edges_query as _cbm_edges_query_handler,
    )
    from orchestrator.graph.tools import (
        cbm_nodes_query as _cbm_nodes_query_handler,
    )

    _register_cbm_p2(
        name="cbm_nodes_query",
        handler=_cbm_nodes_query_handler,
        phase="T1",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "CAN-053 T1 read-only: browse/filter raw CBM graph nodes with a cursor. Filters: kind (module|class|function|method), name_prefix, file_contains. Returns results+total+next_offset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root."},
                    "kind": {"type": "string", "default": "", "description": "Node kind filter: module | class | function | method."},
                    "name_prefix": {"type": "string", "default": "", "description": "Prefix match on node name."},
                    "file_contains": {"type": "string", "default": "", "description": "Substring match on file_path."},
                    "limit": {"type": "integer", "default": 100, "description": "Page size (clamped to 500)."},
                    "offset": {"type": "integer", "default": 0, "description": "Cursor offset; response returns next_offset."},
                },
            },
        },
    )

    _register_cbm_p2(
        name="cbm_edges_query",
        handler=_cbm_edges_query_handler,
        phase="T1",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "CAN-053 T1 read-only: browse/filter raw CBM graph edges with a cursor. Filters: kind (calls|imports_module|...), source_contains, target_contains. Returns results+total+next_offset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root."},
                    "kind": {"type": "string", "default": "", "description": "Edge kind filter, e.g. calls | imports_module."},
                    "source_contains": {"type": "string", "default": "", "description": "Substring match on source qualified_name or file."},
                    "target_contains": {"type": "string", "default": "", "description": "Substring match on target qualified_name or file."},
                    "limit": {"type": "integer", "default": 100, "description": "Page size (clamped to 500)."},
                    "offset": {"type": "integer", "default": 0, "description": "Cursor offset; response returns next_offset."},
                },
            },
        },
    )
except Exception as _cbm_p2_exc:
    import logging as _cbm_log_p2
    _cbm_log_p2.warning(f"CBM graph P2 tool registration failed: {_cbm_p2_exc}")

# ── CBM campaign P3–P6 tools: additive fourth block (do not move the three above) ──
try:
    from orchestrator.core.registry import register_tool as _register_cbm_p3
    from orchestrator.graph.tools import (
        cbm_dsm as _cbm_dsm_handler,
    )
    from orchestrator.graph.tools import (
        cbm_evolution as _cbm_evolution_handler,
    )
    from orchestrator.graph.tools import (
        cbm_quality_gate as _cbm_quality_gate_handler,
    )
    from orchestrator.graph.tools import (
        cbm_test_gap as _cbm_test_gap_handler,
    )
    from orchestrator.graph.tools import (
        cbm_what_if as _cbm_what_if_handler,
    )

    _register_cbm_p3(
        name="cbm_test_gap",
        handler=_cbm_test_gap_handler,
        phase="T2",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "T2 derived-index-aware static import-reach test-gap advisory. May update graph.db; never edits source. Not executed coverage. VERDICT-FREE BY CONTRACT: pass is ALWAYS false and score ALWAYS null on every path - that means 'no verdict computed', NEVER 'coverage failed'; rank coverage_ratio + counts + uncovered_files + isolated_uncovered + gaps instead, and read the response key pass_semantics. Cheapest way to see which production files no test even imports, without reading or running anything.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root holding .orchestrator_cache/graph.db."},
                    "included_files": {"type": "array", "items": {"type": "string"}, "description": "Optional production file list (slash paths). Omitted = view default included set."},
                    "test_files": {"type": "array", "items": {"type": "string"}, "description": "Explicit test seeds. Empty => unavailable:no_tests."},
                    "include_architecture": {"type": "boolean", "default": False, "description": "Opt-in stability + entry_reach on the same file universe. Default false. Not a legacy include_extensions flag."},
                    "per_test_maps": {"type": "boolean", "default": False, "description": "Optional bounded per-test cover maps."},
                },
                "required": ["project_path"],
            },
        },
    )
    _register_cbm_p3(
        name="cbm_quality_gate",
        handler=_cbm_quality_gate_handler,
        phase="T1",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "T1 read-only quality gate. mode=rules evaluates 13 TOML keys (unknown key = invalid_config; max_cc/no_god_files always unavailable v1). mode=compare is readonly vs a caller-chosen baseline (delta .02; missing value unavailable). mode=snapshot RETURNS JSON only and never writes; persist only via later write_new_file overwrite=false. Generic quality still routes to cbm_cycle_point. Not include_extensions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root."},
                    "mode": {"type": "string", "enum": ["rules", "compare", "snapshot"], "default": "rules", "description": "rules | compare | snapshot. snapshot never writes."},
                    "rules_toml": {"type": "string", "description": "TOML document for mode=rules. 64KiB / 128 rules cap."},
                    "baseline": {"type": "string", "description": "Caller-chosen baseline JSON for mode=compare. Not auto-loaded."},
                    "included_files": {"type": "array", "items": {"type": "string"}, "description": "Optional production file list."},
                    "test_files": {"type": "array", "items": {"type": "string"}, "description": "Optional test seeds excluded from production layers."},
                    "metrics": {"type": "object", "description": "Optional quality_factors / analysis_quality_v1 / file_line_counts / fn_spans / labelled proxies."},
                },
                "required": ["project_path"],
            },
        },
    )
    _register_cbm_p3(
        name="cbm_what_if",
        handler=_cbm_what_if_handler,
        phase="T2",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "T2 derived-index-aware RAM what-if. May refresh graph.db before cloning the analysis view; simulation itself never edits source/DB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root holding .orchestrator_cache/graph.db."},
                    "ops": {"type": "array", "items": {"type": "object"}, "description": "Sequence of {op, ...} objects. Invalid/unknown op rejects the whole request."},
                    "included_files": {"type": "array", "items": {"type": "string"}, "description": "Optional production file list (slash paths). Omitted = view default included set."},
                    "test_files": {"type": "array", "items": {"type": "string"}, "description": "Optional test seeds excluded from production layers."},
                    "edge_kind": {"type": "string", "enum": ["observed", "strict"], "default": "observed", "description": "Edge list to mutate/score. observed (default) or strict."},
                },
                "required": ["project_path"],
            },
        },
    )
    _register_cbm_p3(
        name="cbm_evolution",
        handler=_cbm_evolution_handler,
        phase="T1",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "T1 read-only git evolution. argv-only `git log` (no shell/network/git config). 90d/1000 commits; pair cap 50 files/commit (1225 pairs) with dropped count. Jaccard=co/(a+b-co). Author id = SHA256(trim(lower(email))) full 64 hex; missing email unknown, never hashed name. Rename v1 canonical = new path, rename_history_partial. No git => unavailable:not_git_repository. Shallow => partial/history_truncated. Missing CC never invents hotspot. Named cbm_evolution is H1 after registration; generic quality still cbm_cycle_point. No git_stage/git_diff feed. Not include_extensions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root (git work tree)."},
                    "included_files": {"type": "array", "items": {"type": "string"}, "description": "Optional file filter (slash paths). Omitted = all files seen in the walk."},
                    "lookback_days": {"type": "integer", "default": 90, "description": "UTC lookback window. Default 90. Capped at 3650."},
                    "complexity_map": {"type": "object", "description": "Optional {path: cyclomatic_int}. Missing CC => no fake hotspot."},
                },
                "required": ["project_path"],
            },
        },
    )
    _register_cbm_p3(
        name="cbm_dsm",
        handler=_cbm_dsm_handler,
        phase="T1",
        tool_map=TOOL_MAP,
        tool_schemas=TOOL_SCHEMAS,
        tool_phases=TOOL_PHASES,
        phase_labels=PHASE_LABELS,
        schema={
            "description": "T1 read-only DSM. Level = longest path in SCC condensation toward sinks; order by level then lexical. Exact propagation only for N<=500 (avg_reach/(N-1)); 501+ deterministic sample; 2001 truncated keep lowest+highest 1000. Page <=100x100 sparse cells only — never a dense matrix. Mutual-edge clusters (A↔B). UTF-8 labels intact. Named cbm_dsm is H1 after registration; generic quality still cbm_cycle_point. Never writes source/DB. Not include_extensions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute project root holding .orchestrator_cache/graph.db."},
                    "included_files": {"type": "array", "items": {"type": "string"}, "description": "Optional production file list (slash paths). Omitted = view default included set."},
                    "test_files": {"type": "array", "items": {"type": "string"}, "description": "Optional test seeds excluded from production layers."},
                    "row_start": {"type": "integer", "default": 0, "description": "Page row offset."},
                    "col_start": {"type": "integer", "default": 0, "description": "Page column offset."},
                    "page_rows": {"type": "integer", "default": 100, "description": "Page height, capped at 100."},
                    "page_cols": {"type": "integer", "default": 100, "description": "Page width, capped at 100."},
                    "edge_kind": {"type": "string", "enum": ["observed", "strict"], "default": "observed", "description": "Edge list to score. observed (default) or strict."},
                },
                "required": ["project_path"],
            },
        },
    )
except Exception as _cbm_p3_exc:
    import logging as _cbm_log_p3
    _cbm_log_p3.warning(f"CBM graph P3 tool registration failed: {_cbm_p3_exc}")
