"""Wire migration and bounded, identity-scoped observation of consecutive calls."""
from collections import Counter, OrderedDict
from contextvars import ContextVar
from hashlib import sha256
import json
from threading import Lock

# Known polling protocols explicitly require repeating unchanged arguments.
POLL_TOOLS = frozenset({"write_status", "wait_for_selector", "server_lifecycle_status",
                        "owner_heartbeat", "get_task_checkpoint", "get_result_chunk"})
_repeat = ContextVar("soft_repeat", default=False)
_recent = OrderedDict()
_counts = Counter()
_lock = Lock()


def observe(identity, tool, body):
    _repeat.set(False)
    if not all(identity):
        return
    with _lock:
        if tool in POLL_TOOLS:
            _recent.pop(identity, None)
            return
        digest = sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")).encode()).digest()
        previous, count = _recent.pop(identity, (None, 0))
        key = (tool, digest)
        count = count + 1 if previous == key else 1
        _recent[identity] = (key, count)
        if len(_recent) > 2048:
            _recent.popitem(last=False)
    _repeat.set(count >= 3)


def hint_for(payload, registered, repeated):
    # Keep non-text advisory blocks intact inside chain. Existing text-valued
    # 'hint' and dictionary-valued 'summary' remain accessible in legacy views.
    chain = {k: payload[k] for k in registered if payload.get(k) and
             k not in ("ca_hint", "ca_next_tool")}
    confirmation = payload.get("confirm_destructive_required")
    ask = (type(confirmation) is int and confirmation > 0
           or payload.get("error_name") == "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED")
    why = next((value for value in (payload.get("ca_hint"), payload.get("hint"),
                                    payload.get("ca_next_action"))
                if isinstance(value, str) and value), "")
    return {"next": payload.get("ca_next_tool") or "", "why": why, "chain": chain,
            "ask": bool(ask or repeated),
            "ask_reason": ("Confirmation required by the existing destructive-command gate." if ask
                           else "The same call repeated three times. Check for progress." if repeated else "")}


def usage_snapshot():
    with _lock:
        return {"counts": dict(_counts), "scope": "process",
                "unobserved_client_reads": True}


def render(tool, payload, mode, fields, registered, status=200):
    """Build an additive legacy view, or the negotiated v2 view after projection."""
    projected = set(fields or ())
    legacy_requested = bool(projected.intersection((*registered, "hint", "ca_next_action")))
    with _lock:
        _counts["negotiated_v2" if mode in ("2", "dual") else "legacy_calls"] += 1
        if legacy_requested:
            _counts["explicit_legacy_projections"] += 1
        if not projected and mode != "2":
            _counts["unprojected_legacy_reads_unknown"] += 1
    if not isinstance(payload, dict):
        if mode == "2":
            return {"result": payload, "ok": status < 400,
                    "summary": f"{tool} returned a response.",
                    "hint": hint_for({}, registered, _repeat.get())}
        return payload
    repeated = _repeat.get()
    hint = hint_for(payload, registered, repeated)
    if mode not in ("2", "dual"):
        # Respect explicit legacy fields= projections. Soft repeat warnings are
        # advisory only and never turn a successful call into a failed one.
        if repeated or not projected and any(hint.values()):
            return {**payload, ("structured_hint" if "hint" in payload else "hint"): hint}
        return payload
    failed = status >= 400 or payload.get("ok") is False or bool(payload.get("error"))
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary:
        if failed:
            summary = f"{tool} failed ({payload.get('error_name') or status})."
        elif type(payload.get("count")) is int:
            summary = f"{tool} returned {payload['count']} matches."
        else:
            summary = f"{tool} completed."
    # For a flat result, keep all business data. Never hide a sibling field
    # just because a handler happened to name one of its fields 'result'.
    view = {"result": payload, "summary": summary, "hint": hint, "ok": not failed}
    return {**payload, "response_v2": view} if mode == "dual" else view
