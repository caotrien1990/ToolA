"""Async tool wrappers for the CBM graph — bridges graph modules to code_tools' HTTP/JSON-string contract (Phase 1 Step 11).

Each wrapper is async, returns a JSON-encoded string (the code_tools convention),
and is fail-open: graph disabled / DB missing / any error returns a structured
payload rather than raising. These are registered as T1 tools via register_tool().
"""
import asyncio
import glob
import json as _json
import os
import re
import subprocess
import shutil
import tempfile
import time

from . import analysis_service as _analysis_service
from . import cycle_point as _cycle_point
from . import indexer as _indexer
from . import status as _status
from . import query as _query
from . import testgap as _testgap
from . import stability as _stability
from . import attack as _attack
from . import rules as _rules
from . import baseline as _baseline
from . import whatif as _whatif
from . import evolution as _evolution
from . import dsm as _dsm
from .extractors.vulture_ruff import (
    _console_python,
    _merge_dedup,
    scan_ruff,
    scan_vulture,
)
from orchestrator.skip_rules import should_skip_dir as _should_skip_dir


def _ok(payload: dict) -> str:
    return _json.dumps({"ok": True, "result": payload}, ensure_ascii=False)


def _err(_name: str, exc: Exception) -> str:
    return _json.dumps({"ok": False, "error_name": type(exc).__name__,
                        "error": str(exc)}, ensure_ascii=False)


def _clean_project_path(project_path: str) -> tuple[str, str | None]:
    """Normalize a project path at the tool boundary (RISK-3 guard).

    A backslash Windows path can arrive with an embedded NUL when an upstream
    layer unescapes sequences like "D:\\0..." — the original character is
    unrecoverable, so reject with a clear error instead of crashing in
    os.makedirs. Backslashes are normalized to forward slashes.
    """
    if "\x00" in project_path:
        return "", ("project_path contains an embedded NUL character "
                    "(mangled backslash escape) — resend using forward slashes, e.g. D:/path/to/project")
    return project_path.replace("\\", "/"), None


# ─── Auto-chain: graph freshness helper (Task 3 2026-09-10) ───────────────────
# CBM tools that analyse the graph must not silently use stale data.
# _ensure_graph_fresh() checks staleness and auto-indexes when needed.

# ISS-04: reasons that mean "the graph's freshness is NOT established". A bounded
# CBM caller must refuse on any of these instead of analysing the graph anyway.
# Before this set existed, a budget-truncated scan reported stale_files=0 with
# complete=false, `stale is None` was False, and the helper answered
# reason="fresh" -- so cbm_dead_code/dsm/what_if/quality_gate happily analysed a
# graph nobody had verified, while analysis_view refused the very same state with
# status=unavailable. One contract, two answers.
_FRESHNESS_NOT_ESTABLISHED = {
    "freshness_unknown", "check_failed", "stale_scan_incomplete",
}


def _freshness_refusal(auto_idx: dict) -> dict | None:
    """Return the refusal body when freshness was not established, else None."""
    reason = str((auto_idx or {}).get("reason") or "")
    if reason not in _FRESHNESS_NOT_ESTABLISHED:
        return None
    return {
        "enabled": True,
        "status": "unavailable",
        "error": "graph freshness unavailable",
        "freshness": "unknown",
        "reason": f"freshness:{reason}",
        "auto_index_result": auto_idx,
        "ca_next_action": "run_cbm_incremental_index_then_retry",
        "hint": ("Freshness could not be established within the interactive "
                 "response budget. Run cbm_incremental_index explicitly, poll "
                 "write_status with its operation_id until terminal, then retry."),
    }


async def _ensure_graph_fresh(project_path: str) -> dict:
    """Check graph freshness; auto-index if stale or missing. Returns metadata dict."""
    try:
        async with _graph_refresh_lock(project_path):
            status = await asyncio.to_thread(_status.graph_status, project_path)
            stale = status.get("stale_files")
            freshness = str(status.get("freshness") or "")
            has_run = bool(status.get("last_run"))
            scan = status.get("stale_scan") or {}
            # A truncated scan carries a PARTIAL count, not None: trusting the
            # number here is how a 25-of-424 sample became "fresh". Refuse, and
            # do NOT start an unbounded index from inside a bounded call.
            if scan and not scan.get("complete"):
                return {"auto_indexed": False, "reason": "stale_scan_incomplete",
                        "stale_scan": scan, "freshness": freshness or "unknown"}
            if stale is None and freshness not in {"not_indexed"}:
                return {"auto_indexed": False, "reason": "freshness_unknown"}
            if int(stale or 0) > 0 or not has_run:
                async with _graph_write_lock(project_path):
                    result = await asyncio.to_thread(
                        _indexer.incremental_index,
                        project_path,
                        scope="full",
                        project_root=project_path,
                    )
                return {
                    "auto_indexed": True,
                    "reason": "stale" if int(stale or 0) > 0 else "no_prior_run",
                    "index_result": result,
                }
            return {"auto_indexed": False, "reason": "fresh"}
    except Exception as exc:
        return {"auto_indexed": False, "reason": "check_failed", "error": type(exc).__name__}


# ─── §3.2 freshness attachment (D8) ─────────────────────────────────────────
# Every graph-consuming cbm answer carries the freshness sample it was produced under
# (v12 §3.2, carried BY REFERENCE by plan r8 D8; the 8 derivation
# steps, the outcome mapping, the auto-indexed exclusion, the boundary
# rule and the per-tier tests are v12 §3.2's). SAMPLING IS HANDLER-SIDE:
# Scope note (implementer clarification, 2026-11-20): the v12 §3.2 sentence is a blanket statement ("every cbm response"); it does not scope D8 to graph-consuming tools, and no sentence in §3.2 (or in r8, which carries §3.2 by reference) states such a scope. cbm_evolution reads git history live, not the graph, so no freshness sample exists for it; attaching a graph-freshness label would misdescribe the data. 13 of 17 cbm_* tools are wired; cbm_evolution is deliberately not wired.
# gated tools reuse the gate/view sample already computed in the call
# path (zero marginal scan); raw-query (tier-3) tools pay one
# floor-budgeted `graph_status` read (the L693 floor budget,
# `CBM_STATUS_SCAN_MIN_BUDGET_S`, inside `graph_status` itself).
#
# OUTCOME MAPPING (STEP 4): helper `fresh` -> fresh; the three
# refusal-set reasons (`stale_scan_incomplete` / `freshness_unknown` /
# `check_failed`) -> `unknown`; the auto-indexed branch (reason `stale`
# OR `no_prior_run`) is EXCLUDED from the most-conservative comparison
# on dual-gated tools (the operative label comes from the post-index
# view sample alone) and labels `unknown` + the `auto_indexed` flag on
# helper-only tools. View values are already §3.3-domain values and
# pass through unchanged. The `stale_scan_incomplete` carried
# `freshness` value is RECORDED (in the verbatim helper sample), never
# compared. The comparison uses `value` ONLY, never the reason string.
#
# KEYS + MERGE: the ONLY new keys are `freshness_label`,
# `status_error`, `stale_scan` + the sample keys `helper_sample`,
# `view_sample`, `samples_disagree`. `freshness_label` is always
# attached under its distinct key (never `freshness` — the view
# success payload and the refusal body already carry `freshness`);
# every key merges attach-if-absent (setdefault), so a payload that
# already carries a key keeps its own value. `helper_sample` attaches
# on every HELPER-gated tool (dual-gated and helper-only alike);
# `view_sample` + `samples_disagree` only where a view sample exists;
# `samples_disagree` is a BOOLEAN on MAPPED values, computed ONLY when
# both samples exist AND the helper sample is not auto-indexed-excluded
# (when excluded it carries `False` — "no comparison", NOT "samples
# agree"); when only ONE sample exists the key does NOT attach. Tier-3
# tools attach the three label keys only (no helper/view sample exists
# there). `status_error` is the reason carrier: the refusal reason on
# refusal legs, `"no_sample"` where no sample exists at all;
# `stale_scan` is the scan dict where the operative sample carries one,
# else None. Retained gates (v12 §3.1) are untouched: the label is a
# disclosure, never a refusal — verdict STATEMENTS are withheld by the
# seat, never by refusing a call.
_FRESHNESS_CONSERVATIVENESS = {  # §3.3 order, most conservative first
    "corrupt": 0, "error": 0, "disabled": 0,
    "not_indexed": 1, "stale": 2, "unknown": 3, "indexing": 4, "fresh": 5,
}

# The view's refusal reasons that carry a sample WITHOUT a `freshness`
# key: the four early-refusal paths (§3.2 SHAPE check). A `reason`
# outside this set on a freshness-less unavailable payload is a
# service-level `run_analysis_view` unavailable (`unavailable:*`,
# e.g. busy/deadline/stopped) — the view never ran, so no view sample
# exists.
_VIEW_REFUSAL_REASONS = ("scope_blocked:", "store:", "freshness:")
_VIEW_REFUSAL_BARE = {"missing_db", "missing_schema", "corrupt"}


def _freshness_rank(value: str) -> int:
    """Conservativeness rank: lower = more conservative (§3.3 order).
    An unmapped value ranks as `unknown` (never silently fresh)."""
    return _FRESHNESS_CONSERVATIVENESS.get(str(value or ""), 3)


def _map_helper_sample(helper_sample: dict, *, dual_gated: bool) -> tuple:
    """STEP 4 outcome mapping for a `_ensure_graph_fresh` return dict.
    Returns (mapped_value, excluded): `mapped_value` is None exactly
    when the auto-indexed branch is EXCLUDED from the comparison
    (dual-gated only); on helper-only tools the same branch maps to
    `unknown` + the flag (the post-index state is unsampled — an honest
    unknown, not a stale or fresh claim)."""
    reason = str((helper_sample or {}).get("reason") or "")
    if bool((helper_sample or {}).get("auto_indexed")):
        if dual_gated:
            return None, True
        return "unknown", False
    if reason == "fresh":
        return "fresh", False
    return "unknown", False


def _view_sample_from_payload(view_payload: dict) -> tuple:
    """STEP 2 view sample from a `build_analysis_view` payload.
    Returns (sample, status_error, stale_scan); sample is None when the
    payload carries no view sample (a service-level `run_analysis_view`
    unavailable — reason `unavailable:*` — never ran the view).
    Success: {value} only (`value` in {fresh, indexing}); refusal:
    {value, reason} (`reason` VERBATIM; `value` is the flattened
    payload's `freshness` where the refusal carries one, else
    `unknown` for the four early-refusal paths)."""
    if not isinstance(view_payload, dict):
        return None, None, None
    reason = view_payload.get("reason")
    freshness = view_payload.get("freshness")
    status = view_payload.get("status")
    status_error = view_payload.get("status_error")
    stale_scan = view_payload.get("stale_scan")
    if freshness is not None:
        if status == "unavailable":
            return ({"value": str(freshness), "reason": reason},
                    status_error, stale_scan)
        return {"value": str(freshness)}, status_error, stale_scan
    if status == "unavailable":
        text = str(reason or "")
        if (text in _VIEW_REFUSAL_BARE
                or any(text.startswith(p) for p in _VIEW_REFUSAL_REASONS)):
            return ({"value": "unknown", "reason": reason},
                    status_error, stale_scan)
    return None, None, None


def _freshness_attachment(*, helper_sample=None, view=None,
                          status_sample=None) -> dict:
    """STEP 3–6 + KEYS: compute the §3.2 attachment dict. `view` is the
    triple from `_view_sample_from_payload` (or None when no view sample
    exists). `status_sample` is a `graph_status` payload (tier-3 only;
    tiers are exclusive). The dict carries ONLY the §3.2 keys; the
    caller merges attach-if-absent."""
    if view is not None:
        view_sample, view_status_error, view_stale_scan = view
    else:
        view_sample, view_status_error, view_stale_scan = None, None, None
    helper_reason = ""
    helper_mapped = None
    helper_excluded = False
    if helper_sample is not None:
        helper_reason = str(helper_sample.get("reason") or "")
        helper_mapped, helper_excluded = _map_helper_sample(
            helper_sample, dual_gated=view_sample is not None)
    if status_sample is not None:
        label = str(status_sample.get("freshness") or "unknown")
        status_error = status_sample.get("error")
        stale_scan = status_sample.get("stale_scan")
    elif helper_sample is not None and view_sample is not None:
        candidates = [v for v in (helper_mapped, view_sample.get("value"))
                      if v is not None]
        label = min(candidates, key=_freshness_rank) if candidates else "unknown"
        status_error = view_status_error
        stale_scan = view_stale_scan
    elif helper_sample is not None:
        label = helper_mapped if helper_mapped is not None else "unknown"
        if helper_reason in _FRESHNESS_NOT_ESTABLISHED:
            status_error = helper_reason
            stale_scan = helper_sample.get("stale_scan")
        else:
            status_error = None
            stale_scan = None
    elif view_sample is not None:
        label = str(view_sample.get("value") or "unknown")
        status_error = view_status_error
        stale_scan = view_stale_scan
    else:
        label = "unknown"
        status_error = "no_sample"
        stale_scan = None
    attach = {"freshness_label": label, "status_error": status_error,
              "stale_scan": stale_scan}
    if helper_sample is not None:
        attach["helper_sample"] = helper_sample
    if view_sample is not None:
        attach["view_sample"] = view_sample
        if helper_sample is not None:
            if helper_excluded:
                attach["samples_disagree"] = False
            else:
                attach["samples_disagree"] = (
                    helper_mapped != view_sample.get("value"))
    return attach


def _attach_freshness(payload, *, helper_sample=None, view=None,
                      status_sample=None):
    """Merge the §3.2 attachment onto a result payload dict,
    attach-if-absent (setdefault) for every key. Returns the payload.
    Non-dict payloads pass through unchanged."""
    if not isinstance(payload, dict):
        return payload
    for key, value in _freshness_attachment(
            helper_sample=helper_sample, view=view,
            status_sample=status_sample).items():
        payload.setdefault(key, value)
    return payload


def _err_freshness(name, exc, *, helper_sample=None, view=None,
                   status_sample=None):
    """HANDLER-ERROR leg: the handler's error return path (`_err`) with
    the §3.2 label attached — the sampled value where the handler has a
    sample, `unknown` / `no_sample` where no sample exists."""
    envelope = {"ok": False, "error_name": type(exc).__name__,
                "error": str(exc)}
    envelope.update(_freshness_attachment(
        helper_sample=helper_sample, view=view,
        status_sample=status_sample))
    return _json.dumps(envelope, ensure_ascii=False)


_GRAPH_WRITE_LOCKS: dict = {}
_GRAPH_REFRESH_LOCKS: dict = {}

# campaign-1608 (A24): ruff resolves its config by walking up from the TARGET,
# so linting a file inside a project silently adopts that project's lint settings.
# This repo's own ruff.toml selects only E9/F63/F7, so ruff_fix on any file in it
# returned exit_code 0 with an empty diff -- indistinguishable from "clean" -- for
# a file with a real unused import. ruff_fix is the tool [A] hands a worker to
# audit [A] itself, and validate_file's ruff_fix_hint promises these very rules are
# fixable, so the two tools contradicted each other on the same file.
#
# A narrow `select` was only the first of five ways a config can suppress a rule.
# Measured against each, with an explicit --select already in place:
#
#   narrow select    -> beaten by --select alone
#   lint.ignore      -> beaten by --select alone
#   exclude          -> beaten by --select alone (the target is named explicitly)
#   per-file-ignores -> needs lint.per-file-ignores = {}
#   lint.unfixable   -> needs lint.fixable = ["ALL"]; overriding unfixable to []
#                       does NOT work, because ruff computes fixable minus
#                       unfixable and the project already narrowed fixable.
#
# --isolated was the obvious stronger hammer and is deliberately NOT used. It
# discards [lint.isort] too, so on a project declaring known-first-party for an
# installed internal package, ruff_fix --isolated --fix rewrites correctly sorted
# imports into the order that project calls wrong -- and then WRITES it. Losing a
# finding is a missed hint; corrupting a correct file is damage. So: override what
# to look for and what may be fixed, keep the project config for how to fix.
#
# Scope: the safe-fixable DEFECT rules validate_file already advertises, nothing
# wider -- ruff_fix EDITS code, so a rule that lands here must be one we are
# willing to apply unattended. A32-1.2: this select is therefore deliberately
# NARROWER than code_tools.py's _RUFF_LINT_SELECT, which also carries
# _RUFF_REPORT_ONLY_RULES (B006 = unsafe fix, E722 = no fix at all). Do NOT
# "resync" the two strings back together: the relationship is superset (lint)
# vs fixable subset (fix), and tests/test_a29_size_hint_and_ruff_preview_states.py
# pins exactly that.
# #7 (measured 2026-09-25): RUF100 and I left this string. They are a project's
# POLICY, not a defect (see code_tools._RUFF_POLICY_FIX_RULES), and --select
# REPLACES the project's rule set, so naming them here imposed them: a preview
# of this very file, 0 defect findings, proposed 15 hunks of import and noqa
# rewrites in a repo whose config enables neither. ruff_fix adds the ones the
# project's own config enables (_project_policy_rules) and nothing else.
_RUFF_FIX_SELECT = "F401,F841,E711,E712,E731,E401,E402,F541,ASYNC210,ASYNC220,ASYNC221,ASYNC222,ASYNC250,ASYNC251"  # (c) 2026-10-09: +6 ASYNC rules (audited individually, zero noise verified)
_RUFF_FIX_OVERRIDES = (
    "--config", 'lint.fixable = ["ALL"]',
    "--config", "lint.per-file-ignores = {}",
    "--config", "lint.ignore = []",
    "--config", "force-exclude = false",
    # campaign-1608 (A24): force plain text. The daemon runs with FORCE_COLOR set
    # (measured =3, inherited from the launching process, not User/Machine env),
    # which makes ruff paint the diff with ANSI escapes even into a pipe. A diff
    # this tool returns is machine-read (callers grep it for "-import os"), so the
    # escapes turn a correct finding into an unparseable string. --color never
    # wins over FORCE_COLOR; NO_COLOR does not.
    "--color", "never",
)
# #7: a policy code (code_tools._RUFF_POLICY_FIX_RULES: RUF100 and every I code)
# as ruff prints it inside `linter.rules.enabled`, one "name (CODE)," per line.
# Anchored on the whole code: INT001 and ISC004 start with an I as well.
_RUFF_POLICY_CODE_RE = re.compile(r"\((RUF100|I\d{3})\),?$")


def _project_policy_rules(target: str, root: str) -> tuple[list[str], str]:
    """The policy codes the project's own ruff config enables for ``target``.

    Returns ``(codes, "")``, or ``([], reason)`` when that config could not be
    read -- the caller then fixes the defect rules alone, which can miss a
    finding but never rewrites a file into a style the project did not pick.
    ``--show-settings`` resolves the settings of ONE file (a directory answers
    for its first file), and its text is ruff's output rather than a contract,
    so a missing block is a reason, never "enables nothing".
    """
    try:
        completed = subprocess.run(
            [_console_python(), "-m", "ruff", "check", target, "--show-settings"],
            cwd=root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"
    if completed.returncode != 0:
        detail = " ".join((completed.stderr or completed.stdout or "").split())
        return [], f"ruff --show-settings exited {completed.returncode}: {detail[:300]}"
    lines = (completed.stdout or "").splitlines()
    start = next((index for index, line in enumerate(lines)
                  if line.startswith("linter.rules.enabled = [")), None)
    if start is None:
        return [], "ruff --show-settings printed no linter.rules.enabled block"
    if lines[start].rstrip().endswith("[]"):
        return [], ""
    codes = []
    for entry in lines[start + 1:]:
        if entry.strip() == "]":
            return codes, ""
        match = _RUFF_POLICY_CODE_RE.search(entry.strip())
        if match:
            codes.append(match.group(1))
    return [], "ruff --show-settings: the linter.rules.enabled block never closed"


def _graph_write_lock(project_path: str):
    """R50 (2026-08-12): serialize graph WRITES (cbm_index / cbm_incremental_index) per
    project. Concurrent rebuilds contend on the single graph.db writer; under extreme load
    the SQLite busy-timeout is exceeded -> OperationalError ('database is locked', surfaced
    at 100 concurrent workers). One writer at a time (readers stay concurrent via WAL)
    removes the contention. Lazy per-loop asyncio.Lock."""
    lk = _GRAPH_WRITE_LOCKS.get(project_path)
    if lk is None:
        lk = asyncio.Lock()
        _GRAPH_WRITE_LOCKS[project_path] = lk
    return lk


def _graph_refresh_lock(project_path: str):
    key = os.path.normcase(os.path.realpath(project_path))
    lock = _GRAPH_REFRESH_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _GRAPH_REFRESH_LOCKS[key] = lock
    return lock
async def cbm_index(project_path: str = "", scope: str = "core", **aliases) -> str:  # noqa: ARG001
    """T1: index project scope into the graph DB (nodes/edges/FTS5). Read-only on source."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    # P6 (F-01): a nonexistent project root must NOT fall through to the
    # indexer, which would otherwise create a junk .orchestrator_cache/
    # directory and report success.
    if not os.path.isdir(project_path):
        return _json.dumps({"ok": False, "error_name": "INVALID_PROJECT_PATH",
                            "error": f"project_path is not an existing directory: {project_path}"},
                           ensure_ascii=False)
    try:
        async with _graph_write_lock(project_path):
            from orchestrator.progress_watchdog import current_watchdog
            watchdog = current_watchdog()
            result = await asyncio.to_thread(
                _indexer.index_project, project_path, scope=scope, project_root=project_path,
                watchdog=watchdog,
            )
        return _ok(result)
    except Exception as e:
        return _err("cbm_index", e)


async def cbm_graph_status(project_path: str = "", **aliases) -> str:  # noqa: ARG001
    """T1: report graph freshness, counts, WAL state, stale files. Read-only."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    # F-01: a nonexistent project root must NOT fall through to _status.graph_status,
    # which would otherwise report enabled=True + freshness="not_indexed" and trick
    # callers into graph operations under false readiness. Distinguish a missing
    # path (caller bug) from a valid-but-unindexed project (documented fail-open).
    if not os.path.isdir(project_path):
        return _json.dumps({"ok": False, "error_name": "INVALID_PROJECT_PATH",
                            "error": f"project_path is not an existing directory: {project_path}"},
                           ensure_ascii=False)
    try:
        return _ok(await asyncio.to_thread(_status.graph_status, project_path))
    except Exception as e:
        return _err("cbm_graph_status", e)


async def cbm_nodes_query(project_path: str = "", kind: str = "", name_prefix: str = "",
                           file_contains: str = "", limit: int = 100, offset: int = 0,
                           **aliases) -> str:  # noqa: ARG001
    """T1: browse/filter raw graph nodes (kind/name_prefix/file filter + cursor)."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        _status_sample: dict | None = None
        _status_sample = await asyncio.to_thread(_status.graph_status, project_path)
        result = await asyncio.to_thread(
            _query.nodes_query, project_path, kind=kind, name_prefix=name_prefix,
            file_contains=file_contains, limit=limit, offset=offset)
        return _ok(_attach_freshness(result, status_sample=_status_sample))
    except Exception as e:
        return _err_freshness("cbm_nodes_query", e, status_sample=_status_sample)


async def cbm_edges_query(project_path: str = "", kind: str = "", source_contains: str = "",
                          target_contains: str = "", limit: int = 100, offset: int = 0,
                          **aliases) -> str:  # noqa: ARG001
    """T1: browse/filter raw graph edges (kind/source/target filter + cursor)."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        _status_sample: dict | None = None
        _status_sample = await asyncio.to_thread(_status.graph_status, project_path)
        result = await asyncio.to_thread(
            _query.edges_query, project_path, kind=kind, source_contains=source_contains,
            target_contains=target_contains, limit=limit, offset=offset)
        return _ok(_attach_freshness(result, status_sample=_status_sample))
    except Exception as e:
        return _err_freshness("cbm_edges_query", e, status_sample=_status_sample)


async def cbm_trace_path(project_path: str = "", start_node_id: str = "",
                         direction: str = "out", depth: int = 5, **aliases) -> str:  # noqa: ARG001
    """T1: bounded graph traversal from a start node. depth<=5, node cap, timeout 2s. Fail-open."""
    if not project_path or not start_node_id:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path and start_node_id required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        _status_sample: dict | None = None
        _status_sample = await asyncio.to_thread(_status.graph_status, project_path)
        result = await asyncio.to_thread(
            _query.cbm_trace_path, project_path, start_node_id,
            direction=direction, depth=depth,
        )
        # B023: an unresolvable start node used to answer ok:true with edges:[],
        # truncated:false and error:null -- every field a caller guards on said
        # success, and the only signal was `truncation_reason`, a field named
        # for truncation on a response that says it was not truncated. So
        # "you passed a symbol name" read exactly like "this symbol has no
        # callers". This same wrapper already refuses an EMPTY start_node_id
        # with SCHEMA_VIOLATION; an empty id and an absent id are the same
        # class of caller error and must not answer oppositely.
        #
        # `is False` is deliberate, not `not result.get(...)`: None means the
        # lookup never ran (GRAPH_ENABLED=0, or the DB never opened), and that
        # path must stay fail-open exactly as the description promises and the
        # graph-disabled canary pins.
        if result.get("start_node_resolved") is False:
            _b023 = {
                "ok": False,
                "error_name": "START_NODE_NOT_FOUND",
                "error": ("start_node_id %r is not a node in the graph. It must be a "
                          "graph node id (file.py:kind:module.symbol:LINE), not a bare "
                          "symbol name." % start_node_id),
                "ca_next_action": ("call cbm_template_query(template=\"callers\", "
                                   "name=\"<symbol>\") and pass a row's `id` as "
                                   "start_node_id"),
                "hint": ("cbm_template_query returns rows carrying `id` in the required "
                         "shape. find_symbol cannot supply one -- it is a text search "
                         "returning file/line/match."),
                "result": result,
            }
            return _json.dumps(_attach_freshness(
                _b023, status_sample=_status_sample), ensure_ascii=False)
        return _ok(_attach_freshness(result, status_sample=_status_sample))
    except Exception as e:
        return _err_freshness("cbm_trace_path", e, status_sample=_status_sample)


# ─── Phase 3 tools (Steps 15-17) ─────────────────────────────────────────────

async def cbm_template_query(project_path: str = "", template: str = "",
                             name: str = "", node_id: str = "", module: str = "",
                             depth: int = 2, limit: int = 50, offset: int = 0,
                             **aliases) -> str:  # noqa: ARG001
    """T1: named graph queries — callers|callees|imports|imported_by|orphan_symbols|module_deps|entry_points|impact_scope."""
    if not project_path or not template:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path and template required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        _status_sample: dict | None = None
        _status_sample = await asyncio.to_thread(_status.graph_status, project_path)
        result = await asyncio.to_thread(
            _query.template_query, project_path, template,
            name=name, node_id=node_id, module=module,
            depth=int(depth or 2), limit=int(limit or 50),
            offset=int(offset or 0))
        return _ok(_attach_freshness(result, status_sample=_status_sample))
    except Exception as e:
        return _err_freshness("cbm_template_query", e, status_sample=_status_sample)


async def cbm_get_architecture(project_path: str = "", max_files: int = 0,
                               max_symbols: int = 0, timeout_s: float = 0.0,
                               **aliases) -> str:  # noqa: ARG001
    """T2: refresh derived graph state, then return a bounded architecture overview."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        _auto_idx: dict | None = None
        _auto_idx = await _ensure_graph_fresh(project_path)
        _refusal = _freshness_refusal(_auto_idx)
        if _refusal is not None:
            return _ok(_attach_freshness(_refusal, helper_sample=_auto_idx))
        # DOCS_GAP-6: optional bound overrides; 0/unset keeps config defaults.
        # P4 verify fix (STT-17): negative values are invalid — floor to 0 so
        # they keep the config default instead of causing immediate truncation
        # (negative int is truthy in Python and would pass through to query.py).
        kwargs = {}
        if max_files and max_files > 0:
            kwargs["max_files"] = int(max_files)
        if max_symbols and max_symbols > 0:
            kwargs["max_symbols"] = int(max_symbols)
        if timeout_s and timeout_s > 0:
            kwargs["timeout_s"] = float(timeout_s)
        result = await asyncio.to_thread(
            _query.cbm_get_architecture, project_path, **kwargs)
        result["auto_index_result"] = _auto_idx
        return _ok(_attach_freshness(result, helper_sample=_auto_idx))
    except Exception as e:
        return _err_freshness("cbm_get_architecture", e, helper_sample=_auto_idx)


async def cbm_dead_code(project_path: str = "", limit: int = 50,
                        scope: str = "full", file_filter: str = "",
                        **aliases) -> str:  # noqa: ARG001
    """T2: refresh derived graph state, then return conservative advisory findings."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        # Task 3 auto-chain: ensure fresh graph before dead-code analysis
        _auto_idx: dict | None = None
        _auto_idx = await _ensure_graph_fresh(project_path)
        _refusal = _freshness_refusal(_auto_idx)
        if _refusal is not None:
            return _ok(_attach_freshness(_refusal, helper_sample=_auto_idx))
        result = await asyncio.to_thread(_cbm_dead_code_dispatch, project_path,
                                         limit=int(limit or 50), scope=scope,
                                         file_filter=file_filter)
        result["auto_index_result"] = _auto_idx
        # Task 4 auto-chain: hint write_fix when dead code found
        _dead = result.get("dead") or result.get("dead_code") or []
        # A35 (b) -- the graph resolves a call edge only when the receiver is an
        # imported module or alias (graph/indexer.py _resolve_in_module, receiver
        # hop ~L811). A call through an instance / local / attribute receiver --
        # self.fn(), obj.fn(), cfg.helper() -- stays ext:<name> and leaves the
        # definition with ZERO inbound 'calls' edges. So "dead" here means "no
        # RESOLVED reference", and the old bare hint ("write_fix available to
        # remove dead code entries") pointed a CA straight at deleting live code.
        # ca_chain_next stays 'write_fix' -- CORE_RULE documents that chain -- but
        # the precondition is now stated instead of left for the CA to guess.
        result["resolution_limits"] = {
            "not_resolved": ("calls through an instance / local / attribute receiver -- self.fn(), "
                             "obj.fn(), cfg.helper() -- stay ext:<name> and create NO inbound calls edge"),
            "means": "advisory only: 'dead' = no RESOLVED reference in the graph, NOT proof of unused",
            "cross_check": ("search_code(query='<name>', project_path='<abs>', mode='callers') is a "
                            "regex text scan and DOES see self.fn( / obj.fn("),
        }
        if _dead:
            result["ca_chain_hint"] = (
                "write_fix available to remove dead code entries -- ADVISORY: cross-check each "
                "entry with search_code(mode='callers') first; an instance-receiver call resolves "
                "to no graph edge, so a live method can appear in this list")
            result["ca_chain_next"] = "write_fix"
            result["ca_chain_precondition"] = "search_code(mode='callers') per entry returns no hit"
        return _ok(_attach_freshness(result, helper_sample=_auto_idx))
    except Exception as e:
        return _err_freshness("cbm_dead_code", e, helper_sample=_auto_idx)

async def ruff_fix(project_path: str = "", path: str = ".", apply: bool = False,
                   unsafe_fixes: bool = False, preserve_hash: bool = False,
                   **aliases) -> str:  # noqa: ARG001
    """Preview or apply Ruff fixes without accepting arbitrary command arguments."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    root = os.path.realpath(project_path)
    target = os.path.realpath(os.path.join(root, path or "."))
    try:
        if os.path.commonpath([root, target]) != root:
            raise ValueError("path must stay inside project_path")
    except ValueError:
        return _json.dumps({"ok": False, "error_name": "PATH_OUTSIDE_PROJECT",
                            "error": "path must stay inside project_path"}, ensure_ascii=False)
    if not os.path.exists(target):
        return _json.dumps({"ok": False, "error_name": "FILE_NOT_FOUND",
                            "error": f"path not found: {path}"}, ensure_ascii=False)
    if apply and preserve_hash:
        return _json.dumps({"ok": False, "error_name": "HASH_PINNED_TARGET",
                            "error": "Ruff apply would invalidate the pinned artifact hash",
                            "applied": False, "ca_next_action": "use_preview_only_for_pinned_artifact"})

    def _snapshot(candidate: str):
        files = [candidate] if os.path.isfile(candidate) else []
        if not files:
            # F-B2: canonical skip gate instead of a hardcoded set -- case-insensitive,
            # covers SKIP_DIR_GLOBS, and honors per-project .orchestrator_ignore overlays
            # (e.g. Runtime/, node_modules/, dist/ are now skipped like everywhere else).
            for base, dirs, names in os.walk(candidate):
                dirs[:] = [d for d in dirs if not _should_skip_dir(d, root)]
                files.extend(os.path.join(base, name) for name in names if name.endswith(".py"))
        return {file: (stat.st_size, stat.st_mtime_ns) for file in files for stat in [os.stat(file)]}

    before = _snapshot(target) if apply else None
    backup_root = None
    backup_files = {}
    if apply:
        backup_root = tempfile.mkdtemp(prefix="toola-ruff-backup-")
        for source in before:
            rel = os.path.relpath(source, root)
            destination = os.path.join(backup_root, rel)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copy2(source, destination)
            backup_files[source] = destination
    # campaign-1608 (A24b): _console_python(), not sys.executable. Over HTTP the
    # daemon runs pythonw.exe (GUI subsystem, no stdout handle), so ruff.exe has
    # nothing to inherit: measured live as exit_code 1 with stdout, stderr AND
    # diff all empty -- ruff found the error and the answer vanished. scan_ruff
    # already resolves python.exe from the same directory; reuse that helper
    # instead of a third copy.
    # #7: the defect rules always; a policy rule only when the project enables it.
    policy_codes, policy_unresolved = await asyncio.to_thread(
        _project_policy_rules, target, root)
    command = [_console_python(), "-m", "ruff", "check", target, "--fix",
               "--select", ",".join((_RUFF_FIX_SELECT, *policy_codes)),
               *_RUFF_FIX_OVERRIDES]
    if not apply:
        command.append("--diff")
    if unsafe_fixes:
        command.append("--unsafe-fixes")
    try:
        completed = await asyncio.to_thread(
            subprocess.run, command, cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        for source, backup in backup_files.items():
            shutil.copy2(backup, source)
        if backup_root:
            shutil.rmtree(backup_root, ignore_errors=True)
        return _json.dumps({
            "ok": False,
            "error_name": "RUFF_EXEC_FAILED",
            "error": f"{type(exc).__name__}: {exc}",
            "rollback_verified": bool(apply),
        }, ensure_ascii=False)
    output = completed.stdout or ""
    if completed.returncode not in (0, 1):
        for source, backup in backup_files.items():
            shutil.copy2(backup, source)
        if backup_root:
            shutil.rmtree(backup_root, ignore_errors=True)
        return _json.dumps({
            "ok": False,
            "error_name": "RUFF_EXEC_FAILED",
            "error": (completed.stderr or completed.stdout or "ruff failed")[:2000],
            "exit_code": completed.returncode,
            "rollback_verified": bool(apply),
        }, ensure_ascii=False)
    changed = bool(output.strip()) if not apply else before != _snapshot(target)
    # Task 3 auto-chain rule 12: validate files changed by ruff_fix
    post_ruff_validation = None
    if apply and changed:
        try:
            after_snap = _snapshot(target)
            changed_files = [f for f in after_snap if f not in before or after_snap[f] != before.get(f)]
            validations = []
            for cf in changed_files:
                import code_tools as _ct
                val_raw = await _ct.validate_file(cf)
                val = _json.loads(val_raw) if isinstance(val_raw, str) else val_raw
                validations.append({"file": os.path.relpath(cf, root).replace("\\", "/"),
                                    "ok": val.get("ok", False),
                                    "error": val.get("error", "")})
            post_ruff_validation = {"validated": len(validations),
                                    "all_ok": all(v["ok"] for v in validations),
                                    "details": validations}
        except Exception:
            post_ruff_validation = {"validated": 0, "all_ok": False, "error": "validation_failed"}
        if not post_ruff_validation.get("all_ok"):
            for source, backup in backup_files.items():
                shutil.copy2(backup, source)
            if backup_root:
                shutil.rmtree(backup_root, ignore_errors=True)
            return _json.dumps({
                "ok": False,
                "error_name": "RUFF_VALIDATION_FAILED_AUTO_REVERT",
                "error": "Ruff changes failed validation and were reverted",
                "post_ruff_validation": post_ruff_validation,
                "rollback_verified": True,
            }, ensure_ascii=False)
    resp = {
        "mode": "apply" if apply else "preview",
        "applied": bool(apply and completed.returncode in (0, 1)),
        "unsafe_fixes": bool(unsafe_fixes),
        "preserve_hash": bool(preserve_hash),
        "hash_pin_note": "Any applied fix, including I001/F401, can change bytes. For hold/pinned artifacts pass preserve_hash=true and use preview only.",
        "path": os.path.relpath(target, root).replace("\\", "/"),
        "diff": output if not apply else "",
        "stdout": output if apply else "",
        "stderr": completed.stderr or "",
        "exit_code": completed.returncode,
        "changed": changed,
        # campaign-1608 (A24): say which rules produced this answer. Silently
        # swapping the rule set is exactly how the old bug stayed invisible.
        # Derived from the command actually run, not restated -- a hardcoded
        # "isolated" here would keep claiming isolation after the flag drifted.
        "config": "isolated" if "--isolated" in command else "project",
        "select": (command[command.index("--select") + 1]
                   if "--select" in command else "project-default"),
    }
    if policy_unresolved:
        # #7, fail loud: the project's I/RUF100 choice could not be read, so this
        # run fixed the defect rules alone -- say so instead of looking complete.
        resp["policy_rules_unresolved"] = policy_unresolved
    if post_ruff_validation:
        resp["post_ruff_validation"] = post_ruff_validation
    if backup_root:
        shutil.rmtree(backup_root, ignore_errors=True)
    return _ok(resp)


def _detect_majority_language(project_path: str) -> str:
    py_count = 0
    js_ts_count = 0
    ignored_dirs = {".git", ".venv", "venv", "venv312", "__pycache__", "node_modules"}
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for filename in files:
            if filename.endswith(".py"):
                py_count += 1
            elif filename.endswith((".js", ".jsx", ".ts", ".tsx")):
                js_ts_count += 1
    # "other" was enough while this only gated the engine choice, but it is now
    # reported as `language_detected` -- and answering "other" for a repo it just
    # counted 41 JS files in is throwing away what it knows.
    if py_count > js_ts_count:
        return "python"
    return "javascript" if js_ts_count else "other"


def _cbm_dead_code_ast_p0(project_path: str, *, limit: int = 50,
                          rel_files: "set[str] | None" = None,
                          language: str = "") -> dict:
    result = _query.dead_code_candidates(project_path, limit=limit,
                                         rel_files=rel_files)
    # The schema advertises "Ruff-powered ... Vulture supplements", but a
    # non-Python repo silently got this AST engine instead -- a different
    # analysis with different semantics wearing the other one's description.
    # Falling back is fine; not saying so is the defect.
    result["engine"] = "ast-p0-v1"
    result["engine_reason"] = (
        "graph AST engine: majority language is %s, not python — Vulture/Ruff "
        "would have nothing to read" % (language or "not python"))
    return result


def _scanner_health(vulture_status: dict, ruff_status: dict) -> dict:
    """Name what actually ran, not what the engine was designed to run.

    B029b 2026-08-22. The payload hardcoded `engine="vulture+ruff"` and
    `engine_reason="Vulture + Ruff both apply"` with `error=None`, so a run where
    vulture had timed out on every attempt (B029c) looked identical to a healthy
    run that genuinely found nothing. `count=0` is only readable when the caller
    can tell which of those it is.

    `error` is set only when NO scanner survived: one working scanner still
    produces a usable advisory answer, and marking that an error would push
    callers to discard findings that are real.

    An empty status dict means the scanner was never invoked (the `files == []`
    short-circuit). That is neither success nor failure, so it is reported as
    neither -- inventing an outcome for a scan that did not happen is the same
    class of lie this function exists to remove.
    """
    ran = [s for s in (vulture_status, ruff_status) if s]
    failed = [{"scanner": s.get("scanner"), "reason": s.get("reason")}
              for s in ran if not s.get("ok")]
    healthy = [str(s.get("scanner")) for s in ran if s.get("ok")]

    if not ran:
        engine = "vulture+ruff"
        reason = "majority language is python — Vulture + Ruff both apply"
    elif not failed:
        engine = "+".join(healthy)
        reason = ("majority language is python — %s both apply"
                  % " + ".join(name.capitalize() for name in healthy)
                  if len(healthy) > 1 else
                  "majority language is python — %s applied"
                  % healthy[0].capitalize())
    else:
        names = ", ".join("%s (%s)" % (f["scanner"], f["reason"] or "unknown")
                          for f in failed)
        engine = "+".join(healthy) if healthy else "none"
        reason = ("majority language is python, but these scanners did not "
                  "contribute: %s — findings below come from %s only"
                  % (names, "+".join(healthy) if healthy else "no scanner"))
    return {
        "engine": engine,
        "engine_reason": reason,
        "scanners_failed": failed,
        "error": (None if healthy or not failed else
                  "every dead-code scanner failed: %s"
                  % ", ".join(str(f["scanner"]) for f in failed)),
    }


def _cbm_dead_code_dispatch(project_path: str, *, limit: int = 50,
                            scope: str = "full", file_filter: str = "") -> dict:
    scan_root = os.path.join(project_path, "orchestrator") if scope == "core" else project_path
    language = _detect_majority_language(scan_root)

    # `files` used to be resolved here and then dropped on the floor by the
    # non-Python early return, so a bogus glob and no glob at all both returned
    # the full 207 candidates. It was also hard-filtered to `.endswith(".py")`,
    # which made every *.js pattern match zero on the Python path too.
    files: list[str] | None = None
    rel_files: "set[str] | None" = None
    if file_filter:
        pattern = file_filter
        if not os.path.isabs(pattern):
            pattern = os.path.join(scan_root, pattern)
        files = [path for path in glob.glob(pattern, recursive=True)
                 if os.path.isfile(path)]
        rel_files = set()
        for path in files:
            try:
                rel = os.path.relpath(path, scan_root)
            except ValueError:
                continue  # different drive: cannot be a project-relative match
            rel_files.add(rel.replace("\\", "/"))

    def _annotate(result: dict) -> dict:
        result["file_filter"] = file_filter or ""
        result["file_filter_applied"] = bool(file_filter)
        result["files_matched"] = len(files) if files is not None else None
        result["language_detected"] = language
        result["scope"] = scope
        return result

    if language != "python":
        return _annotate(_cbm_dead_code_ast_p0(
            scan_root, limit=limit, rel_files=rel_files, language=language))

    # The Python engines take absolute paths; only the graph engine keys by
    # project-relative path.
    if files is not None:
        files = [p for p in files if p.endswith(".py")]

    t0 = time.monotonic()
    limit = max(1, min(int(limit or 50), 200))
    # B029b: the scanners report their own health here. Left empty when the scan
    # is skipped entirely (`files == []`), because "nothing was asked of it" is
    # not the same claim as "it ran".
    vulture_status: dict = {}
    ruff_status: dict = {}
    if files == []:
        candidates = []
    else:
        v = scan_vulture(scan_root, min_conf=60, files=files,
                         status=vulture_status)
        r = scan_ruff(scan_root, files=files, status=ruff_status)
        candidates = _merge_dedup(v + r)
    candidates.sort(
        key=lambda item: (
            -float(item.get("confidence", 0.0)),
            str(item.get("file_path") or item.get("file") or ""),
            int(item.get("start_line") or item.get("line") or 0),
            str(item.get("symbol") or item.get("name") or ""),
        ),
    )
    categories = {}
    for c in candidates:
        cat = c.get("category", "lint")
        if cat not in categories:
            categories[cat] = {"count": 0, "candidates": []}
        categories[cat]["count"] += 1
        if len(categories[cat]["candidates"]) < limit:
            categories[cat]["candidates"].append(c)
    # F017: `count` used to be min(len(candidates), limit) -- the PAGE SIZE
    # wearing the name of a finding. A project with 900 lint hits and the default
    # limit answered "count: 50". It is now the true population; `returned_count`
    # is the page. Field names match query.dead_code_candidates so both engines
    # answer in the same shape. (categories[cat]["count"] was already a true
    # total -- only its `candidates` list is paged.)
    out = {
        "enabled": True,
        "candidates": candidates[:limit],
        "count": len(candidates),
        "returned_count": min(len(candidates), limit),
        "truncated": len(candidates) > limit,
        "limit_applied": limit,
        "categories": categories,
        "error": None,
        "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
        "disclaimer": "advisory only — Ruff findings require manual verify before action",
    }
    out.update(_scanner_health(vulture_status, ruff_status))
    return _annotate(out)


async def cbm_incremental_index(project_path: str = "", scope: str = "full",
                                **aliases) -> str:  # noqa: ARG001
    """T1: incremental graph update — reindex only changed/added/deleted/renamed files."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        async with _graph_write_lock(project_path):
            from orchestrator.progress_watchdog import current_watchdog
            watchdog = current_watchdog()
            result = await asyncio.to_thread(
                _indexer.incremental_index, project_path, scope=scope,
                project_root=project_path, watchdog=watchdog)
        return _ok(result)
    except Exception as e:
        return _err("cbm_incremental_index", e)


async def cbm_cycle_point(project_path: str = "", **aliases) -> str:  # noqa: ARG001
    """T2: refresh derived graph state, then compute cycle and quality signals."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    # Same F-01 guard as cbm_graph_status: a nonexistent root is a caller bug and
    # must not be reported as a valid-but-unindexed project.
    if not os.path.isdir(project_path):
        return _json.dumps({"ok": False, "error_name": "INVALID_PROJECT_PATH",
                            "error": f"project_path is not an existing directory: {project_path}"},
                           ensure_ascii=False)
    try:
        _auto_idx: dict | None = None
        _auto_idx = await _ensure_graph_fresh(project_path)
        _refusal = _freshness_refusal(_auto_idx)
        if _refusal is not None:
            return _ok(_attach_freshness(_refusal, helper_sample=_auto_idx))
        result = await asyncio.to_thread(_cycle_point.compute_cycle_point, project_path)
        result["auto_index_result"] = _auto_idx
        return _ok(_attach_freshness(result, helper_sample=_auto_idx))
    except Exception as e:
        return _err_freshness("cbm_cycle_point", e, helper_sample=_auto_idx)


async def cbm_analysis_view(
    project_path: str = "",
    included_files: list | None = None,
    test_files: list | None = None,
    finalize_scope: bool = False,
    **aliases,  # noqa: ARG001
) -> str:
    """T1 versioned extension: bounded readonly analysis_view. Does not take the write lock."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    if not os.path.isdir(project_path):
        return _json.dumps({"ok": False, "error_name": "INVALID_PROJECT_PATH",
                            "error": f"project_path is not an existing directory: {project_path}"},
                           ensure_ascii=False)
    try:
        result = await _analysis_service.run_analysis_view(
            project_path,
            included_files=included_files,
            test_files=test_files,
            finalize_scope=bool(finalize_scope),
        )
        if isinstance(result, dict):
            _view = _view_sample_from_payload(result)
            result = _attach_freshness(result, view=_view)
        return _ok(result)
    except Exception as e:
        return _err_freshness("cbm_analysis_view", e)


def _as_path_list(value) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = _json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item) for item in parsed if str(item).strip()]
        return [part.strip() for part in text.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _raise_if_stopped(checkpoint, stop=None, deadline_mono=None) -> None:
    if checkpoint is not None and checkpoint("work"):
        raise InterruptedError("analysis_stop")
    if stop is not None and stop.is_set():
        raise InterruptedError("analysis_stop")
    if deadline_mono is not None and time.monotonic() >= deadline_mono:
        raise InterruptedError("analysis_stop")


async def cbm_test_gap(
    project_path: str = "",
    included_files: list | None = None,
    test_files: list | None = None,
    include_architecture: bool = False,
    per_test_maps: bool = False,
    **aliases,  # noqa: ARG001
) -> str:
    """T2 derived-index-aware static test-gap advisory."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    if not os.path.isdir(project_path):
        return _json.dumps({"ok": False, "error_name": "INVALID_PROJECT_PATH",
                            "error": f"project_path is not an existing directory: {project_path}"},
                           ensure_ascii=False)
    included = _as_path_list(included_files)
    tests = _as_path_list(test_files) or []
    want_arch = bool(include_architecture)
    _view_box: dict = {}

    def _work(*, checkpoint, stop, deadline_mono, project_path, included_files, test_files, finalize_scope):
        del finalize_scope
        _raise_if_stopped(checkpoint, stop, deadline_mono)
        payload = _testgap.build_test_gap(
            project_path,
            included_files=included_files,
            test_files=test_files,
            per_test_maps=bool(per_test_maps),
            checkpoint=checkpoint,
        )
        if want_arch and isinstance(payload, dict):
            from .analysis_view import build_analysis_view
            universe = list(included_files or []) + list(test_files or [])
            view_payload = build_analysis_view(
                project_path,
                included_files=universe or included_files,
                test_files=test_files or [],
                finalize_scope=False,
                checkpoint=checkpoint,
            )
            _view_box["view"] = _view_sample_from_payload(view_payload)
            if isinstance(view_payload, dict) and view_payload.get("status") != "unavailable":
                view_payload = dict(view_payload)
                if included_files is not None:
                    view_payload["included_files"] = list(included_files)
                view_payload["test_files"] = list(test_files or [])
            payload = dict(payload)
            payload["architecture"] = {
                "stability": _stability.compute_stability(view_payload),
                "entry_reach": _attack.compute_entry_reach(
                    view_payload, included_files or [],
                ),
            }
            payload["metric_notes"] = list(payload.get("metric_notes") or []) + [
                "architecture_opt_in_stability_entry",
            ]
        return payload

    try:
        # Task 3 auto-chain: ensure fresh graph before test-gap analysis
        _auto_idx: dict | None = None
        _auto_idx = await _ensure_graph_fresh(project_path)
        _refusal = _freshness_refusal(_auto_idx)
        if _refusal is not None:
            return _ok(_attach_freshness(_refusal, helper_sample=_auto_idx))
        result = await _analysis_service.run_analysis_view(
            project_path,
            included_files=included,
            test_files=tests,
            finalize_scope=False,
            work_fn=_work,
        )
        if isinstance(result, dict):
            result["auto_index_result"] = _auto_idx
            result = _attach_freshness(
                result, helper_sample=_auto_idx, view=_view_box.get("view"))
            # Task 4 auto-chain: hint quality_gate after test_gap
            _gaps = result.get("gaps") or result.get("uncovered") or []
            if _gaps:
                result["ca_chain_hint"] = "cbm_quality_gate available to check coverage threshold"
                result["ca_chain_next"] = "cbm_quality_gate"
        return _ok(result)
    except Exception as e:
        return _err_freshness("cbm_test_gap", e, helper_sample=_auto_idx,
                              view=_view_box.get("view"))


def _as_metrics(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = _json.loads(text)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(value, dict):
        return value
    return {}


def _snapshot_from_view(view: dict, metrics: dict) -> dict:
    from .analysis_view import ALGORITHM_VERSION as _VIEW_ALGO
    from .config import EXTRACTOR_VERSION
    from .schema import SCHEMA_VERSION

    stab = _stability.compute_stability(view)
    included = list(view.get("included_files") or [])
    tests = {_rules._identity_key(p) for p in (view.get("test_files") or [])}
    production = [p for p in included if _rules._identity_key(p) not in tests]
    nodes = [_rules._identity_key(p) for p in production]
    pairs = _rules._pair_edges(view)
    cycles = _rules.cycle_group_count(nodes, pairs)
    depth = _rules.max_depth_of(nodes, pairs)
    quality = metrics.get("analysis_quality_v1")
    prov = view.get("provenance") or {}
    view_status = view.get("status")
    return {
        "version": 1,
        # P4: a partial view must stay "partial" -- never mapped to "ok".
        "status": view_status if view_status in {"ok", "partial"} else str(view_status or "unavailable"),
        "reason": view.get("reason"),
        "unmatched_files": list(view.get("unmatched_files") or []),
        "pass": False,
        "mode": "snapshot",
        "wrote": False,
        "identity": {
            "root": prov.get("root"),
            "included_set_hash": prov.get("included_set_hash"),
            "algorithm_version": prov.get("algorithm_version") or _VIEW_ALGO,
            "extractor_version": prov.get("extractor_version") or EXTRACTOR_VERSION,
            "schema_version": prov.get("schema_version") or SCHEMA_VERSION,
            "confidence_policy": prov.get("confidence_policy"),
        },
        "metrics": {
            "analysis_quality_v1": quality,
            "coupling_score": stab.get("coupling_score"),
            "cycles": cycles,
            "max_depth": depth,
        },
        "stability": {
            "status": stab.get("status"),
            "coupling_score": stab.get("coupling_score"),
        },
        "metric_notes": [
            "snapshot_t1_no_write",
            "no_auto_baseline",
            "no_legacy_score_replace",
        ],
        "provenance": prov,
        "persist_hint": ".orchestrator_cache/cbm/baselines/<scope-hash>/<run-id>.json via write_new_file overwrite=false",
    }


async def cbm_quality_gate(
    project_path: str = "",
    mode: str = "rules",
    rules_toml: str = "",
    baseline: str = "",
    included_files: list | None = None,
    test_files: list | None = None,
    metrics: dict | None = None,
    **aliases,
) -> str:
    """T1: rules / compare / snapshot. Snapshot returns JSON only — never writes."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    if not os.path.isdir(project_path):
        return _json.dumps({"ok": False, "error_name": "INVALID_PROJECT_PATH",
                            "error": f"project_path is not an existing directory: {project_path}"},
                           ensure_ascii=False)
    chosen = str(mode or aliases.get("gate_mode") or "rules").strip().lower()
    if chosen not in {"rules", "compare", "snapshot"}:
        return _json.dumps({
            "ok": True,
            "result": {
                "status": "unavailable",
                "reason": "invalid_mode",
                "pass": False,
                "wrote": False,
                "counts": {"passed": 0, "violated": 0, "unavailable": 0, "evaluated": 0},
            },
        }, ensure_ascii=False)
    included = _as_path_list(included_files)
    tests = _as_path_list(test_files) or []
    extra = _as_metrics(metrics)
    toml_text = rules_toml or aliases.get("toml") or ""
    baseline_raw = baseline if baseline else None

    _view_box: dict = {}

    def _work(*, checkpoint, stop, deadline_mono, project_path, included_files, test_files, finalize_scope):
        del finalize_scope
        _raise_if_stopped(checkpoint, stop, deadline_mono)
        from .analysis_view import build_analysis_view
        view = build_analysis_view(
            project_path,
            included_files=included_files,
            test_files=test_files,
            finalize_scope=False,
            checkpoint=checkpoint,
        )
        _view_box["view"] = _view_sample_from_payload(view)
        if chosen == "snapshot":
            payload = _snapshot_from_view(view, extra)
            payload["wrote"] = False
            return payload
        if chosen == "compare":
            snap = _snapshot_from_view(view, extra)
            compared = _baseline.compare_baseline(snap, baseline_raw)
            compared["mode"] = "compare"
            compared["wrote"] = False
            compared["snapshot"] = snap
            # P4: keep a partial view partial in compare output too --
            # compare_baseline() only looks at metric aggregates.
            if snap.get("status") == "partial":
                compared["status"] = "partial"
                compared["reason"] = snap.get("reason") or "unmatched_included_files"
                compared["pass"] = False
                compared["unmatched_files"] = list(snap.get("unmatched_files") or [])
                _cmp_notes = compared.get("metric_notes") or []
                if "view_partial" not in _cmp_notes:
                    compared["metric_notes"] = _cmp_notes + ["view_partial"]
            return compared
        if not str(toml_text).strip():
            return {
                "status": "unavailable",
                "reason": "missing_rules",
                "pass": False,
                "mode": "rules",
                "wrote": False,
                "aggregate": "unavailable",
                "checks": [],
                "counts": {"passed": 0, "violated": 0, "unavailable": 0, "evaluated": 0},
            }
        evaluated = _rules.evaluate_rules(view, toml_text, metrics=extra)
        evaluated["mode"] = "rules"
        evaluated["wrote"] = False
        return evaluated

    try:
        result = await _analysis_service.run_analysis_view(
            project_path,
            included_files=included,
            test_files=tests,
            finalize_scope=False,
            work_fn=_work,
        )
        if isinstance(result, dict):
            result = dict(result)
            result["wrote"] = False
            result = _attach_freshness(result, view=_view_box.get("view"))
        return _ok(result)
    except Exception as e:
        return _err_freshness("cbm_quality_gate", e, view=_view_box.get("view"))

    except Exception as e:
        return _err_freshness("cbm_quality_gate", e, view=_view_box.get("view"))


def _as_ops(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = _json.loads(text)
        except ValueError:
            parsed = text
        return list(parsed) if isinstance(parsed, list) else [parsed]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


async def cbm_what_if(
    project_path: str = "",
    ops: list | None = None,
    included_files: list | None = None,
    test_files: list | None = None,
    edge_kind: str = "observed",
    **aliases,
) -> str:
    """T2: refresh derived graph state, then run RAM-only what-if simulation."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    if not os.path.isdir(project_path):
        return _json.dumps({"ok": False, "error_name": "INVALID_PROJECT_PATH",
                            "error": f"project_path is not an existing directory: {project_path}"},
                           ensure_ascii=False)
    included = _as_path_list(included_files)
    tests = _as_path_list(test_files) or []
    chosen_kind = str(edge_kind or aliases.get("kind") or "observed").strip().lower()
    if chosen_kind not in {"observed", "strict"}:
        chosen_kind = "observed"
    parsed_ops = _as_ops(ops if ops is not None else aliases.get("operations"))

    _view_box: dict = {}

    def _work(*, checkpoint, stop, deadline_mono, project_path, included_files, test_files, finalize_scope):
        del finalize_scope
        _raise_if_stopped(checkpoint, stop, deadline_mono)
        from .analysis_view import build_analysis_view
        view = build_analysis_view(
            project_path,
            included_files=included_files,
            test_files=test_files,
            finalize_scope=False,
            checkpoint=checkpoint,
        )
        _view_box["view"] = _view_sample_from_payload(view)
        return _whatif.simulate_sequence(view, parsed_ops, edge_kind=chosen_kind)

    try:
        # Task 3 auto-chain: ensure fresh graph before what-if analysis
        _auto_idx: dict | None = None
        _auto_idx = await _ensure_graph_fresh(project_path)
        _refusal = _freshness_refusal(_auto_idx)
        if _refusal is not None:
            return _ok(_attach_freshness(_refusal, helper_sample=_auto_idx))
        result = await _analysis_service.run_analysis_view(
            project_path,
            included_files=included,
            test_files=tests,
            finalize_scope=False,
            work_fn=_work,
        )
        if isinstance(result, dict):
            result = dict(result)
            result["wrote"] = False
            result["auto_index_result"] = _auto_idx
            result = _attach_freshness(
                result, helper_sample=_auto_idx, view=_view_box.get("view"))
        return _ok(result)
    except Exception as e:
        return _err_freshness("cbm_what_if", e, helper_sample=_auto_idx,
                              view=_view_box.get("view"))


def _as_lookback(value, default: int = 90) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        return default
    if days < 1:
        return default
    return min(days, 3650)


def _as_complexity_map(value) -> dict | None:
    raw = _as_metrics(value)
    if not raw:
        return None
    parsed: dict[str, int] = {}
    for key, item in raw.items():
        try:
            cc = int(item)
        except (TypeError, ValueError):
            continue
        if cc > 0 and isinstance(key, str) and key.strip():
            parsed[key] = cc
    return parsed or None


async def cbm_evolution(
    project_path: str = "",
    included_files: list | None = None,
    lookback_days: int = 90,
    complexity_map: dict | None = None,
    **aliases,
) -> str:
    """T1: argv-only git evolution. Never writes. Author IDs hashed by default."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    if not os.path.isdir(project_path):
        return _json.dumps({"ok": False, "error_name": "INVALID_PROJECT_PATH",
                            "error": f"project_path is not an existing directory: {project_path}"},
                           ensure_ascii=False)
    included = _as_path_list(included_files)
    days = _as_lookback(
        lookback_days if lookback_days is not None else aliases.get("days"),
        90,
    )
    cc_map = _as_complexity_map(
        complexity_map if complexity_map is not None else aliases.get("cc_map"),
    )

    def _work():
        return _evolution.compute_evolution(
            project_path,
            known_files=included or None,
            complexity_map=cc_map,
            lookback_days=days,
        )

    try:
        result = await asyncio.to_thread(_work)
        if isinstance(result, dict):
            result = dict(result)
            result["wrote"] = False
        return _ok(result)
    except Exception as e:
        return _err("cbm_evolution", e)


async def cbm_dsm(
    project_path: str = "",
    included_files: list | None = None,
    test_files: list | None = None,
    row_start: int = 0,
    col_start: int = 0,
    page_rows: int = 100,
    page_cols: int = 100,
    edge_kind: str = "observed",
    **aliases,
) -> str:
    """T1: RAM-only DSM sparse page. Never writes source/DB."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    if not os.path.isdir(project_path):
        return _json.dumps({"ok": False, "error_name": "INVALID_PROJECT_PATH",
                            "error": f"project_path is not an existing directory: {project_path}"},
                           ensure_ascii=False)
    included = _as_path_list(included_files)
    tests = _as_path_list(test_files) or []
    chosen_kind = str(edge_kind or aliases.get("kind") or "observed").strip().lower()
    if chosen_kind not in {"observed", "strict"}:
        chosen_kind = "observed"
    rs = row_start if row_start is not None else aliases.get("row", 0)
    cs = col_start if col_start is not None else aliases.get("col", 0)
    pr = page_rows if page_rows is not None else aliases.get("rows", 100)
    pc = page_cols if page_cols is not None else aliases.get("cols", 100)

    _view_box: dict = {}

    def _work(*, checkpoint, stop, deadline_mono, project_path, included_files, test_files, finalize_scope):
        del finalize_scope
        _raise_if_stopped(checkpoint, stop, deadline_mono)
        from .analysis_view import build_analysis_view
        view = build_analysis_view(
            project_path,
            included_files=included_files,
            test_files=test_files,
            finalize_scope=False,
            checkpoint=checkpoint,
        )
        _view_box["view"] = _view_sample_from_payload(view)
        return _dsm.build_dsm(
            view=view,
            row_start=rs,
            col_start=cs,
            page_rows=pr,
            page_cols=pc,
            edge_kind=chosen_kind,
        )

    try:
        result = await _analysis_service.run_analysis_view(
            project_path,
            included_files=included,
            test_files=tests,
            finalize_scope=False,
            work_fn=_work,
        )
        if isinstance(result, dict):
            result = dict(result)
            result["wrote"] = False
            result = _attach_freshness(result, view=_view_box.get("view"))
        return _ok(result)
    except Exception as e:
        return _err_freshness("cbm_dsm", e, view=_view_box.get("view"))


async def cbm_council_context(project_path: str = "", name: str = "",
                              depth: int = 2, file_path: str = "",
                              **aliases) -> str:  # noqa: ARG001
    """T1: bounded advisory council graph context pack (Step 21). Fail-open;
    the leader embeds the block in worker prompts explicitly — never auto-injected.

    STT-2: file_path param adds file-level context mode (all symbols in the file
    + direct callers). Takes precedence over changes mode when name is empty."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        _status_sample: dict | None = None
        _status_sample = await asyncio.to_thread(_status.graph_status, project_path)
        result = await asyncio.to_thread(_query.council_graph_context, project_path,
                                         name=name, depth=int(depth or 2),
                                         file_path=file_path)
        return _ok(_attach_freshness(result, status_sample=_status_sample))
    except Exception as e:
        return _err_freshness("cbm_council_context", e, status_sample=_status_sample)


__all__ = ["cbm_index", "cbm_graph_status", "cbm_trace_path",
           "cbm_template_query", "cbm_get_architecture", "cbm_dead_code",
           "cbm_incremental_index", "cbm_council_context", "cbm_cycle_point",
           "cbm_analysis_view", "cbm_test_gap", "cbm_quality_gate",
           "cbm_what_if", "cbm_evolution", "cbm_dsm"]
