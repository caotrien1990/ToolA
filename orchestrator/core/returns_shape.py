"""Point 16 (Q13) output-shape checking leaf — addendum UPGRADE_P7916_P16_DECISION_20261103.md sections 3-4.

Design provenance: Point 16 Q13 of the ToolA upgrade campaign (P7+P9+P16),
decision addendum sections 3 (mechanism) and 4 (depth-cap interlock).

Invariants:
- Warn-only: a verdict never alters, blocks, or replaces the tool answer.
- Fail-open everywhere: no function in this module raises into its caller;
  an internal failure is reported as the ``check_error`` verdict (or a
  neutral default for the census / journal helpers), never as an exception.
- The answer itself is never mutated and never copied into any output:
  mismatch strings carry JSON type NAMES only, never answer content.
- Schema trust is earned, not assumed: a ``returns`` block counts only when
  its ``x-audit`` block proves it was audited (canonical SHA match, caps
  respected, only allowed schema keywords, a real ``depth_D`` datum).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime

from orchestrator.core.registry_data import CURATED_CORE_TOOLS
from orchestrator.text_lines import split_lines

_LOG = logging.getLogger(__name__)

VERDICTS = {"pass", "warn", "skipped", "check_error"}
SKIPPED_REASONS = {
    "no_schema", "schema_invalid", "too_large", "node_budget",
    "depth", "disabled", "dispatcher_payload", "non_dict",
}

RAW_CAP_BYTES = 256 * 1024
NODE_BUDGET = 2000
MISMATCH_CAP = 5

SCHEMA_MAX_BYTES = 64 * 1024
SCHEMA_MAX_PROPERTIES = 200
SCHEMA_MAX_DEPTH = 8

ALLOWED_SCHEMA_KEYS = {
    "type", "properties", "required", "additionalProperties",
    "items", "anyOf", "oneOf", "x-dual",
}

# Per-tool disable set, names only. Empty by design: disabling a tool is a
# code change here plus a tranche-note record, never a runtime flag.
DISABLED_TOOLS = frozenset()

# The set in force (38): the 34 curated core tools plus the four additions
# named by the Point 16 decision (D-P16-1 SET-38).
RETURNS_SET_38 = tuple(CURATED_CORE_TOOLS) + (
    "run_ps1_script", "git_status", "git_diff", "audit_tool_matrix",
)

_JOURNAL_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Schema state
# ---------------------------------------------------------------------------

def canonical_schema_sha(returns_block) -> str:
    """SHA-256 hex of the canonical form of a returns block without x-audit."""
    try:
        if isinstance(returns_block, dict):
            block = {k: v for k, v in returns_block.items() if k != "x-audit"}
        else:
            block = returns_block
        raw = json.dumps(
            block, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
    except Exception:
        return ""


def _canonical_bytes(returns_block) -> int:
    try:
        block = {k: v for k, v in returns_block.items() if k != "x-audit"}
        return len(json.dumps(
            block, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8"))
    except Exception:
        return SCHEMA_MAX_BYTES + 1


def _subschemas(node):
    """Yield the direct child subschemas of a schema node (structural walk)."""
    if not isinstance(node, dict):
        return
    props = node.get("properties")
    if isinstance(props, dict):
        for sub in props.values():
            yield sub
    items = node.get("items")
    if isinstance(items, dict):
        yield items
    for key in ("anyOf", "oneOf"):
        branches = node.get(key)
        if isinstance(branches, list):
            for sub in branches:
                yield sub


def _count_properties(node) -> int:
    total = 0
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            total += len(props)
        for sub in _subschemas(node):
            total += _count_properties(sub)
    return total


def _schema_depth(node) -> int:
    if not isinstance(node, dict):
        return 0
    child_depths = [_schema_depth(sub) for sub in _subschemas(node)]
    return 1 + (max(child_depths) if child_depths else 0)


def _schema_keywords_ok(node, is_root: bool) -> bool:
    if not isinstance(node, dict):
        return True
    for key in node:
        if key in ALLOWED_SCHEMA_KEYS:
            continue
        if is_root and key == "x-audit":
            continue
        return False
    for key in ("anyOf", "oneOf"):
        if key in node:
            branches = node.get(key)
            if not isinstance(branches, list) or not (1 <= len(branches) <= 2):
                return False
    for sub in _subschemas(node):
        if not _schema_keywords_ok(sub, False):
            return False
    return True


def schema_state(returns_block) -> str:
    """One of "absent" | "invalid" | "audited" for a returns block."""
    try:
        if not isinstance(returns_block, dict):
            return "absent"
        if "x-audit" not in returns_block:
            return "absent"
        audit = returns_block.get("x-audit")
        if not isinstance(audit, dict):
            return "invalid"
        if audit.get("status") != "audited":
            return "invalid"
        sha = audit.get("schema_sha256")
        if not sha or str(sha) != canonical_schema_sha(returns_block):
            return "invalid"
        depth_d = audit.get("depth_D")
        if isinstance(depth_d, bool) or not isinstance(depth_d, int) or depth_d < 1:
            return "invalid"
        if _canonical_bytes(returns_block) > SCHEMA_MAX_BYTES:
            return "invalid"
        if _count_properties(returns_block) > SCHEMA_MAX_PROPERTIES:
            return "invalid"
        if _schema_depth(returns_block) > SCHEMA_MAX_DEPTH:
            return "invalid"
        if not _schema_keywords_ok(returns_block, True):
            return "invalid"
        return "audited"
    except Exception:
        return "invalid"


def cap_for(block) -> int:
    """Depth cap for an audited returns block: min(32, max(8, depth_D + 4))."""
    try:
        depth_d = int(block.get("x-audit", {}).get("depth_D"))
    except Exception:
        depth_d = 4
    return min(32, max(8, depth_d + 4))


# ---------------------------------------------------------------------------
# Schema resolution
# ---------------------------------------------------------------------------

def resolve_returns(tool_name, schemas=None):
    """First-match returns block for tool_name in the assembled schema list."""
    try:
        if schemas is None:
            import code_tools
            schemas = code_tools.TOOL_SCHEMAS
        for entry in schemas or []:
            if not isinstance(entry, dict):
                continue
            fn = entry.get("function")
            if isinstance(fn, dict):
                if fn.get("name") == tool_name:
                    block = fn.get("returns")
                    return block if isinstance(block, dict) else None
            elif entry.get("name") == tool_name:
                block = entry.get("returns")
                return block if isinstance(block, dict) else None
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Answer measurement
# ---------------------------------------------------------------------------

def depth_of(answer) -> int:
    """Depth of an answer value; the root counts as depth 1, scalars add 0."""
    if not isinstance(answer, (dict, list)):
        return 0
    max_depth = 0
    stack = [(answer, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            max_depth = depth
        children = node.values() if isinstance(node, dict) else node
        for child in children:
            if isinstance(child, (dict, list)):
                stack.append((child, depth + 1))
    return max_depth


def count_nodes(answer, budget) -> int:
    """Count nodes in an answer, exiting early once the count exceeds budget."""
    count = 0
    stack = [answer]
    while stack:
        node = stack.pop()
        count += 1
        if count > budget:
            return count
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return count


# ---------------------------------------------------------------------------
# Validation (JSON-schema subset)
# ---------------------------------------------------------------------------

def _json_type(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return type(value).__name__


def _type_matches(actual: str, want: str) -> bool:
    if want == "number":
        return actual in ("integer", "number")
    return actual == want


def _join(path: str, key) -> str:
    return f"{path}.{key}" if path else str(key)


def _validate(value, subschema, path: str, mismatches: list) -> None:
    if len(mismatches) >= MISMATCH_CAP:
        return
    if not isinstance(subschema, dict):
        return
    # anyOf / oneOf: pass if any branch validates with zero mismatches.
    for branch_key in ("anyOf", "oneOf"):
        branches = subschema.get(branch_key)
        if isinstance(branches, list) and branches:
            for branch in branches:
                probe: list = []
                _validate(value, branch, path, probe)
                if not probe:
                    return
            first = branches[0] if isinstance(branches[0], dict) else {}
            want = first.get("type")
            if want:
                mismatches.append(f"type:{path}={_json_type(value)}!={want}")
            else:
                mismatches.append(f"anyOf:{path}")
            return
    want = subschema.get("type")
    if isinstance(want, str):
        actual = _json_type(value)
        if not _type_matches(actual, want):
            mismatches.append(f"type:{path}={actual}!={want}")
            return
    if isinstance(value, dict):
        props = subschema.get("properties")
        props = props if isinstance(props, dict) else {}
        required = subschema.get("required")
        if isinstance(required, list):
            for key in required:
                if len(mismatches) >= MISMATCH_CAP:
                    return
                if key not in value:
                    mismatches.append(f"missing:{_join(path, key)}")
        for key, sub in props.items():
            if len(mismatches) >= MISMATCH_CAP:
                return
            if key in value:
                _validate(value[key], sub, _join(path, key), mismatches)
        if subschema.get("additionalProperties") is False:
            for key in value:
                if len(mismatches) >= MISMATCH_CAP:
                    return
                if key not in props:
                    mismatches.append(f"additional:{_join(path, key)}")
    elif isinstance(value, list):
        items = subschema.get("items")
        if isinstance(items, dict):
            for element in value:
                if len(mismatches) >= MISMATCH_CAP:
                    return
                _validate(element, items, path, mismatches)


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------

def check_returns(tool_name, answer, raw_len):
    """Return (verdict, reason_or_None, mismatches) for one tool answer.

    Warn-only and fail-open: never raises, never alters the answer, and
    mismatch strings carry type names only, never answer content.
    """
    try:
        if tool_name in DISABLED_TOOLS:
            return ("skipped", "disabled", [])
        if not isinstance(answer, dict):
            return ("skipped", "non_dict", [])
        if answer.get("error_name") == "SERVER_BUDGET_EXCEEDED" or (
            answer.get("partial") is True and "stopped_by" in answer
        ):
            return ("skipped", "dispatcher_payload", [])
        block = resolve_returns(tool_name)
        state = schema_state(block)
        if state == "absent":
            return ("skipped", "no_schema", [])
        if state == "invalid":
            return ("skipped", "schema_invalid", [])
        try:
            raw_size = int(raw_len or 0)
        except Exception:
            raw_size = 0
        if raw_size > RAW_CAP_BYTES:
            return ("skipped", "too_large", [])
        if count_nodes(answer, NODE_BUDGET) > NODE_BUDGET:
            return ("skipped", "node_budget", [])
        if depth_of(answer) > cap_for(block):
            return ("skipped", "depth", [])
        mismatches: list = []
        _validate(answer, block, "", mismatches)
        if mismatches:
            return ("warn", None, mismatches[:MISMATCH_CAP])
        return ("pass", None, [])
    except Exception:
        return ("check_error", None, [])


# ---------------------------------------------------------------------------
# Depth-skip gate + census
# ---------------------------------------------------------------------------

def _rows_for_tool(rows, tool):
    out = []
    for row in rows or []:
        if isinstance(row, dict) and row.get("tool") == tool and row.get("returns_shape_verdict"):
            out.append(row)
    return out


def verdict_counts(rows, tool) -> dict:
    counts: dict = {}
    for row in _rows_for_tool(rows, tool):
        verdict = str(row.get("returns_shape_verdict"))
        counts[verdict] = counts.get(verdict, 0) + 1
    return counts


def depth_skip_gate(rows, tool) -> bool:
    """True iff >=100 verdict rows for the tool and >2% are skipped/depth."""
    try:
        mine = _rows_for_tool(rows, tool)
        total = len(mine)
        if total < 100:
            return False
        depth = sum(1 for r in mine if r.get("returns_shape_verdict") == "skipped/depth")
        return (depth / total) > 0.02
    except Exception:
        return False


def read_usage_rows() -> list:
    """Parse the retained USAGE_LOG as dict rows; fail-open to []."""
    try:
        from orchestrator.session.logs import _usage_log_path
        path = _usage_log_path()
        rows = []
        for line in split_lines(path.read_text(encoding="utf-8", errors="replace")):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows
    except Exception:
        return []


def compute_returns_coverage(schemas=None, usage_rows=None) -> dict:
    """Census over RETURNS_SET_38: covered / depth_suspect / disabled / eligible."""
    try:
        if usage_rows is None:
            usage_rows = read_usage_rows()
        covered = 0
        depth_suspect = 0
        disabled = 0
        for tool in RETURNS_SET_38:
            if tool in DISABLED_TOOLS:
                disabled += 1
                continue
            block = resolve_returns(tool, schemas=schemas)
            if schema_state(block) != "audited":
                continue
            if depth_skip_gate(usage_rows, tool):
                depth_suspect += 1
            else:
                covered += 1
        return {
            "covered": covered,
            "depth_suspect": depth_suspect,
            "disabled": disabled,
            "eligible": len(RETURNS_SET_38),
        }
    except Exception:
        return {"covered": 0, "depth_suspect": 0, "disabled": 0, "eligible": len(RETURNS_SET_38)}


# ---------------------------------------------------------------------------
# Window journal (reconciliation evidence for window-reaching dispatches)
# ---------------------------------------------------------------------------

def journal_path():
    """Path of returns_shape_journal.jsonl, beside the retained USAGE_LOG."""
    from orchestrator.session.logs import _usage_log_path
    return _usage_log_path().parent / "returns_shape_journal.jsonl"


def append_window_journal(accounting_id, tool_name) -> bool:
    """Append one {ts, accounting_id, tool} line; fail-open, never raises."""
    try:
        line = json.dumps(
            {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "accounting_id": str(accounting_id),
                "tool": str(tool_name),
            },
            ensure_ascii=False,
        )
        path = journal_path()
        with _JOURNAL_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return True
    except Exception:
        _LOG.warning("returns_shape journal append failed", exc_info=True)
        return False


def checkpoint_journal(covered_ids: set) -> int:
    """Move journal lines whose accounting_id is covered to the archive file.

    Returns the number of lines moved; fail-open to 0, never raises.
    """
    try:
        covered = {str(x) for x in (covered_ids or set())}
        if not covered:
            return 0
        path = journal_path()
        with _JOURNAL_LOCK:
            if not path.exists():
                return 0
            lines = split_lines(path.read_text(encoding="utf-8", errors="replace"))
            moved = []
            kept = []
            for line in lines:
                if not line.strip():
                    continue
                hit = False
                try:
                    row = json.loads(line)
                    if isinstance(row, dict) and str(row.get("accounting_id")) in covered:
                        hit = True
                except Exception:
                    hit = False
                (moved if hit else kept).append(line)
            if not moved:
                return 0
            archive = path.parent / "returns_shape_journal.archive.jsonl"
            with archive.open("a", encoding="utf-8") as fh:
                for line in moved:
                    fh.write(line + "\n")
            path.write_text(
                ("\n".join(kept) + "\n") if kept else "", encoding="utf-8",
            )
            return len(moved)
    except Exception:
        _LOG.warning("returns_shape journal checkpoint failed", exc_info=True)
        return 0
