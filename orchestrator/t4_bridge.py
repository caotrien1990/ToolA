"""T4-over-HTTP bridge core (Phase 2 Sprint 1).

Gives the daemon's stateful-T4 execution a permanent home: one dedicated
event loop on its own thread (created at daemon boot, D-S4), one
single-driver asyncio lock, per-tool deadlines, and the page-entry table
with the stamps of plan section 3.2 (owner_agent, generation, loop_epoch,
suspect, call_seq). The invoke seam in orchestrator_server.py routes a
call here only when the tool is in BRIDGED_TOOLS *and* the dispatch
transport is "api" (D-S1) -- MCP dispatch semantics are untouched.

Filed plan: T4_HTTP_PLAN_20261110.md sections 2, 3.2-3.4, 5.1, 6.
Later sprints own: grammar unification, attach binding, the reaper,
close_browser_page / t4_host_status tools, the dialog census, CDP
offload, and the discovery clause. Interfaces they need are declared
here (entry table, quarantine fence, status snapshot).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
import threading
import time
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# HTTP tools executed on the dedicated T4 loop, including page cleanup
# and DOM candidate enumeration. t4_host_status reads the bridge snapshot.
# --------------------------------------------------------------------------
BRIDGED_TOOLS = frozenset({
    # Class P -- page-stateful (section 4)
    "open_browser_page", "cdp_attach_target", "list_cdp_targets",
    "read_page", "query_selector", "click", "fill", "evaluate_js",
    "wait_for_selector", "read_console_errors", "read_network_errors",
    "screenshot_page", "compare_dom_snapshot", "describe_page",
    # Class P-new
    "close_browser_page", "cleanup_test_session", "query_dom_candidates",
    # Class D -- desktop (section 4)
    "desktop_window_list", "desktop_screenshot", "check_window_visibility",
    "desktop_click", "desktop_app_focus", "desktop_send_text",
    "desktop_window_state",
    # Class G -- native dialog (section 4)
    "fill_folder_dialog", "cancel_native_dialog",
    # Section 2 utilities: routed, not newly advertised
    "fill_value", "read_value", "dom_state_diff", "ui_state_summary",
    "extract_log_section", "read_all_logs", "save_log_snapshot",
    "verify_active_electron_source",
})

# HTTP discovery includes the supported T4 handlers and host status.
# Existing page-state utilities retain their established discovery behavior.
DISCOVERY_TOOLS = frozenset({
    "open_browser_page", "cdp_attach_target", "list_cdp_targets",
    "read_page", "query_selector", "click", "fill", "evaluate_js",
    "wait_for_selector", "read_console_errors", "read_network_errors",
    "screenshot_page", "compare_dom_snapshot", "describe_page",
    "close_browser_page", "cleanup_test_session", "query_dom_candidates",
    "desktop_window_list", "desktop_screenshot", "check_window_visibility",
    "desktop_click", "desktop_app_focus", "desktop_send_text",
    "desktop_window_state",
    "fill_folder_dialog", "cancel_native_dialog",
    "t4_host_status",
})

# Page-state consumers supported only by the stateful MCP host.
NON_ROUTED_CONSUMERS = frozenset({
    "click_and_handle_folder_dialog",
    "invoke_app_function", "click_selector", "checkpoint_loop_dom_state",
    "click_and_checkpoint_loop", "run_script", "record_ui_flow",
    "assert_text_visible", "assert_button_click_changes_state",
    "assert_console_no_error", "launch_electron_with_cdp",
})

# Every excluded page-state consumer is guarded on HTTP.
GUARD_EXEMPT_TOOLS = frozenset()

# The HTTP guard covers every excluded consumer.
GUARDED_TOOLS = NON_ROUTED_CONSUMERS - GUARD_EXEMPT_TOOLS

# Section 5.1: state-changing / page-bound input calls take the driver
# lock. Everything else in BRIDGED_TOOLS is a lock-exempt read that still
# runs on the T4 loop.
DRIVER_LOCK_TOOLS = frozenset({
    "open_browser_page", "cdp_attach_target", "close_browser_page", "cleanup_test_session",
    "click", "fill", "fill_value", "evaluate_js",
    "desktop_click", "desktop_send_text", "desktop_window_state",
    "desktop_app_focus", "fill_folder_dialog", "cancel_native_dialog",
})

# Section 2 shape (d): class-G dialog bodies are zero-await coroutines
# whose synchronous Win32 work must never run on the T4 loop; the bridge
# drives them on a dedicated executor instead.
CLASS_G_TOOLS = frozenset({"fill_folder_dialog", "cancel_native_dialog"})

MINT_TOOLS = {"open_browser_page": "launched", "cdp_attach_target": "attached"}

# Section 5.1: only an in-page evaluate round-trip read clears suspect.
SUSPECT_CLEARING_READS = frozenset({"read_value", "describe_page"})

# D-S3: a call may reference two pages (compare_dom_snapshot takes
# page_id_a + page_id_b). Resolution scans these argument names in this
# order; per-entry checks run in argument order, first failure wins.
PAGE_ID_ARG_KEYS = (
    "page_id", "attach_id", "page_id_a", "page_id_b",
    "other_page_id", "source_page_id", "target_page_id",
)

LOCK_GRACE_S = 5.0
GLOBAL_CEILING_S = 180.0

# Section 3.4 TTLs (constants, tunable via the T4Bridge constructor).
IDLE_TTL_S = 1800.0  # 30 min
ABS_TTL_S = 14400.0  # 4 h
REAPER_PERIOD_S = 60.0

# Section 2 deadline map -- fixed rows.
_FIXED_DEADLINES_S = {}
for _t in ("read_page", "describe_page", "query_selector",
           "read_console_errors", "read_network_errors",
           "compare_dom_snapshot", "read_value", "dom_state_diff",
           "ui_state_summary", "extract_log_section", "read_all_logs",
           "save_log_snapshot", "list_cdp_targets", "desktop_window_list",
           "check_window_visibility", "verify_active_electron_source"):
    _FIXED_DEADLINES_S[_t] = 60.0
_FIXED_DEADLINES_S["cdp_attach_target"] = 90.0
for _t in ("click", "fill", "fill_value", "screenshot_page",
           "desktop_click", "desktop_send_text", "desktop_app_focus",
           "desktop_window_state", "fill_folder_dialog",
           "cancel_native_dialog", "desktop_screenshot"):
    _FIXED_DEADLINES_S[_t] = 30.0
_FIXED_DEADLINES_S["close_browser_page"] = 15.0

# Dynamic rows: deadline = handler timeout parameter (declared default
# when omitted) + 30 s, capped at 120 s.
_DYNAMIC_DEADLINE_DEFAULT_MS = {
    "open_browser_page": 20000,
    "evaluate_js": 8000,
    "wait_for_selector": 5000,
}


def deadline_for(tool_name, body=None):
    """Per-tool bridge deadline in seconds (plan section 2 map)."""
    if tool_name in _DYNAMIC_DEADLINE_DEFAULT_MS:
        ms = _DYNAMIC_DEADLINE_DEFAULT_MS[tool_name]
        if isinstance(body, dict):
            raw = body.get("timeout_ms")
            if isinstance(raw, (int, float)) and raw > 0:
                ms = raw
        return min(120.0, ms / 1000.0 + 30.0)
    return _FIXED_DEADLINES_S.get(tool_name, 60.0)


def should_bridge(tool_name, transport):
    """D-S1 seam predicate: bridge only HTTP-originated dispatch."""
    return transport == "api" and tool_name in BRIDGED_TOOLS


def resolve_page_ids(body):
    """All page-id arguments of a call, in argument order (D-S3)."""
    ids = []
    if isinstance(body, dict):
        for key in PAGE_ID_ARG_KEYS:
            value = body.get(key)
            if isinstance(value, str) and value and value not in ids:
                ids.append(value)
    return ids


def _error_payload(error_name, **extra):
    payload = {"ok": False, "error_name": error_name, "error": error_name}
    payload.update(extra)
    return payload


# Unified page-id grammar (plan section 3.1): both families, exactly the
# shape tools_v15._PAGE_ID_RE and the dispatcher's W7 gate accept.
_PAGE_ID_GRAMMAR_RE = re.compile(r"(?:pg|cdp)-[A-Za-z0-9][A-Za-z0-9_-]*")


def is_well_formed_page_id(value):
    return isinstance(value, str) and bool(_PAGE_ID_GRAMMAR_RE.fullmatch(value))


def guard_non_routed_http_call(tool_name, body, generation=""):
    """Return an actionable transport error before touching another loop's page state."""
    if tool_name not in NON_ROUTED_CONSUMERS:
        return None
    for key in PAGE_ID_ARG_KEYS:
        if isinstance(body, dict) and key in body and body[key] is not None:
            if not is_well_formed_page_id(body[key]):
                return _error_payload("PAGE_ID_INVALID", tool=tool_name, page_id=body[key])
    action = ("start_app_with_remote_debugging_then_list_cdp_targets"
              if tool_name == "launch_electron_with_cdp"
              else "use_a_bridged_tool_such_as_describe_page_or_query_selector")
    return _error_payload("T4_NOT_ROUTED", tool=tool_name, generation=generation,
                          ca_next_action=action)


def _as_raw(result):
    if isinstance(result, str):
        return result
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False)
    return str(result)


def _parse_result(raw):
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else None
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None

# ---------------------------------------------------------------------------
# Dialog census (plan 5.2(g)): bridge-side re-implementation of both class-G
# collectors over desktop_window_list enumeration entries (the handlers'
# own collectors are nested closures and are not callable from outside).
# The dialog-class test reads the entry's class_name with fill's
# _is_dialog_class semantics (#32770, or 'dialog' in the class name) --
# never the enumeration's is_dialog_class flag (tools_v15), which is
# #32770-only and narrower than the conjoined semantics. On the owner_pid
# pin form the conjunction gains the ownership test: the candidate's
# owner_hwnd (owner_pid form) must be non-zero. Guarantee as filed: a
# pattern matching only an UNOWNED dialog-class main yields zero
# candidates; an OWNED dialog-class main passes exactly as a summoned
# dialog does (plan section 10 residual, asserted by the companion
# fixture). The fill census applies the handler's collector filters
# (owner/title/class/exclude); launched_after_ts needs a process
# create-time the enumeration entry does not carry, so it is enforced
# only by the handler's bound re-check, never widened here.
# ---------------------------------------------------------------------------

def _is_dialog_class_name(class_name):
    cls = class_name or ""
    return cls == "#32770" or "dialog" in cls.lower()


def _as_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _compile_pattern(pattern):
    try:
        return re.compile(pattern or "", re.IGNORECASE)
    except re.error:
        return None


def _census_common(w, owner_pid, parent_hwnd, excludes):
    """Pin + exclude filters shared by both collectors, then the 5.2(g)
    conjunction (dialog-class test; ownership test on the pid form)."""
    if w.get("visible") is False:
        return False
    if owner_pid and _as_int(w.get("pid")) != owner_pid:
        return False
    if parent_hwnd and _as_int(w.get("parent_hwnd")) != parent_hwnd \
            and _as_int(w.get("owner_hwnd")) != parent_hwnd:
        return False
    t_low = (w.get("title") or "").lower()
    if any(ex and ex in t_low for ex in excludes):
        return False
    if not _is_dialog_class_name(w.get("class_name")):
        return False
    if owner_pid and not _as_int(w.get("owner_hwnd")):
        return False
    return True


def fill_census_candidates(windows, body):
    """Fill's collector semantics for the caller pattern (the bridged
    call forces auto_try_common_titles off) over enumeration entries,
    conjoined per _census_common."""
    rx = _compile_pattern(body.get("title_pattern") or "")
    if rx is None:
        return []
    owner_pid = _as_int(body.get("owner_pid"))
    parent_hwnd = _as_int(body.get("parent_hwnd"))
    class_rx = None
    if body.get("class_name_pattern"):
        class_rx = _compile_pattern(body.get("class_name_pattern"))
        if class_rx is None:
            return []
    excludes = [str(s).lower() for s in (body.get("exclude_titles") or [])]
    out = []
    for w in windows or []:
        title = w.get("title") or ""
        if not title or not rx.search(title):
            continue
        if class_rx is not None \
                and not class_rx.search(w.get("class_name") or ""):
            continue
        if _census_common(w, owner_pid, parent_hwnd, excludes):
            out.append(w)
    return out


def cancel_census_candidates(windows, body):
    """Cancel's titled-window matcher (collecting variant of its enum
    proc) over enumeration entries, conjoined per _census_common."""
    pattern = body.get("title_pattern") or ""
    rx = _compile_pattern(pattern) if pattern else None
    if pattern and rx is None:
        return []
    owner_pid = _as_int(body.get("owner_pid"))
    parent_hwnd = _as_int(body.get("parent_hwnd"))
    excludes = [str(s).lower() for s in (body.get("exclude_titles") or [])]
    out = []
    for w in windows or []:
        title = w.get("title") or ""
        if not title:
            continue
        if rx is not None and not rx.search(title):
            continue
        if _census_common(w, owner_pid, parent_hwnd, excludes):
            out.append(w)
    return out


def dialog_pin_error(tool_name, body):
    """Plan 5.2(g) pin rules for bridged class-G calls: an owner pin is
    required for both handlers; the owner_pid form additionally requires
    a non-empty title_pattern (the parent_hwnd form is exempt)."""
    owner_pid = _as_int(body.get("owner_pid"))
    parent_hwnd = _as_int(body.get("parent_hwnd"))
    if not owner_pid and not parent_hwnd:
        return "missing_owner_pin"
    if owner_pid and not parent_hwnd and not str(body.get("title_pattern") or "").strip():
        return "missing_title_pattern"
    return None


def _dialog_refusal(reason, candidates=None):
    actions = {
        "missing_owner_pin": "pass_parent_hwnd_or_both_owner_pid_and_non_empty_title_pattern",
        "missing_title_pattern": "pass_non_empty_title_pattern_then_retry",
        "no_matching_dialog":
            "enumerate_windows_and_pin_a_real_dialog_then_retry",
        "multiple_matches": "narrow_the_pin_or_title_pattern_then_retry",
    }
    payload = _error_payload(
        "DIALOG_TARGET_AMBIGUOUS", reason=reason,
        ca_next_action=actions.get(
            reason, "enumerate_windows_and_pin_a_real_dialog_then_retry"))
    if candidates:
        payload["candidates"] = [{
            "hwnd": _as_int(c.get("hwnd")), "title": c.get("title") or "",
            "pid": _as_int(c.get("pid")),
            "class_name": c.get("class_name") or "",
            "owner_hwnd": _as_int(c.get("owner_hwnd")),
            "parent_hwnd": _as_int(c.get("parent_hwnd")),
        } for c in candidates[:10]]
    return payload


async def _default_window_enumerator():
    """One desktop_window_list enumeration (the 5.2(g) census vehicle:
    the single entry carries hwnd/pid/class/title/owner_hwnd)."""
    try:
        import tools_v15

        raw = await tools_v15.desktop_window_list(
            include_child_controls=False)
        parsed = json.loads(raw) if isinstance(raw, str) else {}
    except Exception:
        return []
    if not parsed.get("ok"):
        return []
    return parsed.get("windows") or []

def _live_pages_dict():
    """The daemon's _BROWSER_PAGES registry (tools_v15), or None when
    the module cannot be imported in this process."""
    try:
        import tools_v15
        return tools_v15._BROWSER_PAGES
    except Exception:
        return None


async def _stop_pw(state):
    """Detach semantics for one registry state: Playwright stop only."""
    pw = state.get("pw") if isinstance(state, dict) else None
    if pw is None:
        return
    try:
        result = pw.stop()
        if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
            await result
    except Exception:
        pass


@dataclass
class EntryRecord:
    """One page entry's bridge-side stamps (plan section 3.2)."""

    page_id: str
    owner_agent: str = ""
    owner_session: str = ""
    generation: str = ""
    loop_epoch: int = 0
    kind: str = ""
    target_fingerprint: dict = field(default_factory=dict)
    suspect: dict | None = None
    call_seq: int = 0
    in_flight: set = field(default_factory=set)
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    reaping: bool = False


class T4Bridge:
    """Loop lifecycle + submission path + entry table + lock fence."""

    def __init__(self, grace_s=LOCK_GRACE_S, watchdog_tick_s=5.0,
                 deadline_overrides=None, idle_ttl_s=IDLE_TTL_S,
                 abs_ttl_s=ABS_TTL_S, reaper_period_s=REAPER_PERIOD_S,
                  reaper_enabled=True, window_enumerator=None):
        self.grace_s = grace_s
        self.watchdog_tick_s = watchdog_tick_s
        self.idle_ttl_s = idle_ttl_s
        self.abs_ttl_s = abs_ttl_s
        self.reaper_period_s = reaper_period_s
        self.reaper_enabled = reaper_enabled
        self.deadline_overrides = dict(deadline_overrides or {})
        self.window_enumerator = window_enumerator
        self._state_lock = threading.RLock()
        self._loop = None
        self._loop_thread = None
        self.loop_epoch = 0
        self._driver_lock = None  # asyncio.Lock, created on the T4 loop
        self._g_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="t4-bridge-g")
        self.entries = {}
        self._holder = None  # {token, agent, tool, acquired_at, completing}
        self._completing = {}  # token -> holder record past its deadline
        self._token_counter = 0  # bridge-global; never rebased (section 3.4)
        self.quarantine_decisions = []
        self.quarantine_records = []  # applied / quarantine_stale records
        self._watchdog_stop = threading.Event()
        self._watchdog_thread = None
        self._started = False

    # -- loop lifecycle ----------------------------------------------------

    def start(self, watchdog=True):
        self.ensure_loop()
        if watchdog and (self._watchdog_thread is None
                         or not self._watchdog_thread.is_alive()):
            self._watchdog_stop.clear()
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_run, name="t4-bridge-watchdog",
                daemon=True)
            self._watchdog_thread.start()
        self._started = True
        return self._loop

    def loop_alive(self):
        return (self._loop is not None and not self._loop.is_closed()
                and self._loop_thread is not None
                and self._loop_thread.is_alive())

    def ensure_loop(self):
        """Return the live T4 loop, (re)creating it (new epoch) if dead."""
        with self._state_lock:
            if self.loop_alive():
                return self._loop
            loop = asyncio.new_event_loop()

            def _run():
                asyncio.set_event_loop(loop)
                loop.run_forever()

            thread = threading.Thread(
                target=_run, name="t4-bridge-loop", daemon=True)
            thread.start()
            self._loop = loop
            self._loop_thread = thread
            self.loop_epoch += 1
            # Section 3.4: completing records and holder state die with
            # the loop; entries survive as records and answer per the
            # epoch check (section 3.6).
            self._holder = None
            self._completing = {}
            self._driver_lock = None
            ready = threading.Event()

            async def _setup():
                self._driver_lock = asyncio.Lock()
                if self.reaper_enabled:
                    asyncio.create_task(self._reaper_loop())
                ready.set()

            asyncio.run_coroutine_threadsafe(_setup(), loop)
            ready.wait(timeout=5.0)
            return loop

    def _watchdog_run(self):
        while not self._watchdog_stop.wait(self.watchdog_tick_s):
            try:
                if self._started and not self.loop_alive():
                    self.ensure_loop()
            except Exception:
                pass

    def stop(self):
        self._watchdog_stop.set()
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=5.0)
        self._g_executor.shutdown(wait=False)
        self._started = False

    # -- entry table --------------------------------------------------------

    def register_entry(self, page_id, owner_agent="", owner_session="",
                       generation="", kind="", target_fingerprint=None):
        with self._state_lock:
            existing = self.entries.get(page_id)
            if existing is not None:
                # Re-stamp of a live entry (e.g. open_browser_page
                # reusing an attached page): refresh identity fields
                # only -- lifecycle state (created_at, call_seq,
                # in-flight) is never reset by a re-stamp.
                if owner_agent:
                    existing.owner_agent = owner_agent
                if owner_session:
                    existing.owner_session = owner_session
                if generation:
                    existing.generation = generation
                if kind:
                    existing.kind = kind
                if target_fingerprint:
                    existing.target_fingerprint = dict(target_fingerprint)
                return existing
            record = EntryRecord(
                page_id=page_id, owner_agent=owner_agent,
                owner_session=owner_session, generation=generation,
                loop_epoch=self.loop_epoch, kind=kind,
                target_fingerprint=dict(target_fingerprint or {}))
            self.entries[page_id] = record
            return record

    def get_entry(self, page_id):
        with self._state_lock:
            return self.entries.get(page_id)

    def remove_entry(self, page_id):
        with self._state_lock:
            return self.entries.pop(page_id, None)

    # -- reaper (plan 3.4) --------------------------------------------------

    async def _reaper_loop(self):
        while True:
            await asyncio.sleep(self.reaper_period_s)
            try:
                await self.reap_once()
            except Exception:
                pass

    async def reap_once(self, now=None):
        """Release missing, unavailable or expired pages on the T4 loop.

        Skip in-flight and already-reaping entries. A failed release stays
        registered for a later tick.
        """
        now = time.time() if now is None else now
        pages = _live_pages_dict()
        if pages is None:
            return []
        with self._state_lock:
            candidates = [
                entry for entry in self.entries.values()
                if not entry.in_flight and (
                    entry.page_id not in pages or pages[entry.page_id].get("unavailable")
                    or now - entry.last_used_at > self.idle_ttl_s
                    or now - entry.created_at > self.abs_ttl_s)]
        reaped = []
        for entry in candidates:
            with self._state_lock:
                if entry.in_flight or entry.reaping or self.entries.get(entry.page_id) is not entry:
                    continue
                entry.reaping = True
            try:
                if await self._close_entry_state(entry):
                    self.remove_entry(entry.page_id)
                    reaped.append(entry.page_id)
            finally:
                entry.reaping = False
        return reaped

    async def _close_entry_state(self, entry):
        """Close (launched) or detach-only (attached) the registry
        state behind a bridge entry; runs on the T4 loop. Attached
        pages are detached with pw.stop() only -- the user's app is
        never closed (plan 3.4/5.5). D-S7: _DOM_SNAPSHOTS is keyed by
        snapshot_id, not page_id, so there are no per-page snapshots
        to clear here."""
        import tools_v15

        state = tools_v15._BROWSER_PAGES.get(entry.page_id)
        if state is None:
            return True
        state.setdefault("kind", entry.kind)
        result = _parse_result(await tools_v15.close_browser_page(entry.page_id))
        return bool(result and result.get("ok"))

    # -- quarantine fence (section 3.4, whole-unit effectuation) -----------

    def _next_token(self):
        with self._state_lock:
            self._token_counter += 1
            return self._token_counter

    def record_quarantine_decision(self, decision):
        with self._state_lock:
            self.quarantine_decisions.append(decision)
            return decision

    async def effectuate_quarantine(self, decision):
        """Apply a quarantine decision as ONE unit. Runs on the T4 loop.

        Entry case: applies iff the decision token (when carried) still
        equals the current holder token AND the entry call_seq still
        equals the recorded value AND the quarantined call is still in
        flight. Holder case: token equality alone. Epoch-scoped; a
        missing entry makes the whole unit a quarantine_stale no-op.
        """
        with self._state_lock:
            stale = dict(decision)
            stale["state"] = "quarantine_stale"
            if decision.get("loop_epoch") != self.loop_epoch:
                self.quarantine_records.append(stale)
                return "quarantine_stale"
            token = decision.get("token")
            page_id = decision.get("page_id")
            holder_token = self._holder.get("token") if self._holder else None
            token_ok = token is None or token == holder_token
            if page_id is None:
                # Holder case: lock release alone; condition (ii) n/a.
                if token is not None and token_ok and self._holder:
                    await self._release_lock(token)
                    applied = dict(decision)
                    applied["state"] = "quarantined"
                    applied["late_action_possible"] = True
                    self.quarantine_records.append(applied)
                    return "quarantined"
                self.quarantine_records.append(stale)
                return "quarantine_stale"
            entry = self.entries.get(page_id)
            if entry is None:
                self.quarantine_records.append(stale)
                return "quarantine_stale"
            seq_ok = entry.call_seq == decision.get("entry_call_seq")
            flight_ok = decision.get("quarantined_seq") in entry.in_flight
            if not (token_ok and seq_ok and flight_ok):
                self.quarantine_records.append(stale)
                return "quarantine_stale"
            self.entries.pop(page_id, None)
            entry.in_flight.discard(decision.get("quarantined_seq"))
            if token is not None and self._holder:
                await self._release_lock(token)
            applied = dict(decision)
            applied["state"] = "quarantined"
            applied["late_action_possible"] = True
            self.quarantine_records.append(applied)
            return "quarantined"

    async def _release_lock(self, token):
        """Token-fenced release; executed only on the T4 loop."""
        with self._state_lock:
            if not self._holder or self._holder.get("token") != token:
                return False
            self._holder = None
            self._completing.pop(token, None)
            lock = self._driver_lock
        if lock is not None and lock.locked():
            lock.release()
        return True

    # -- submission ----------------------------------------------------------

    def _deadline(self, tool_name, body):
        if tool_name in self.deadline_overrides:
            return self.deadline_overrides[tool_name]
        return min(GLOBAL_CEILING_S, deadline_for(tool_name, body))

    def _preflight_entries(self, tool_name, page_ids, agent_id):
        """Per-entry checks in argument order; first failure wins."""
        mutating = tool_name in DRIVER_LOCK_TOOLS
        for page_id in page_ids:
            entry = self.get_entry(page_id)
            if entry is None:
                if (page_id in (_live_pages_dict() or {})
                        or tool_name in {"cleanup_test_session", "close_browser_page", "open_browser_page"}):
                    return _error_payload("PAGE_NOT_FOUND", page_id=page_id)
                continue
            if entry.reaping:
                return _error_payload("PAGE_NOT_FOUND", page_id=page_id)
            if entry.loop_epoch != self.loop_epoch:
                return _error_payload(
                    "PAGE_NOT_FOUND", page_id=page_id,
                    reason="t4_host_loop_recycled",
                    generation=entry.generation,
                    ca_next_action="reopen_page_or_reattach")
            if ((entry.owner_agent and entry.owner_agent != agent_id)
                    or (tool_name in {"cleanup_test_session", "close_browser_page", "open_browser_page"}
                        and not entry.owner_agent)):
                return _error_payload(
                    "PAGE_NOT_FOUND", page_id=page_id,
                    generation=entry.generation,
                    ca_next_action="reopen_page_or_reattach")
            if mutating and entry.suspect and tool_name not in {"cleanup_test_session", "close_browser_page"}:
                return _error_payload(
                    "PAGE_SUSPECT", page_id=page_id,
                    reason=entry.suspect.get("reason", ""),
                    ca_next_action="verify_with_read_then_retry_or_reopen")
        return None

    def _mark_suspect(self, page_ids, reason):
        with self._state_lock:
            for page_id in page_ids:
                entry = self.entries.get(page_id)
                if entry is not None:
                    entry.suspect = {"reason": reason, "at": time.time()}

    def _post_result(self, tool_name, raw, page_ids, agent_id, generation):
        parsed = _parse_result(raw)
        if parsed is None:
            return
        if tool_name == "evaluate_js" \
                and parsed.get("error_name") == "EVAL_TIMEOUT":
            self._mark_suspect(page_ids, "eval_timeout")
        if tool_name in SUSPECT_CLEARING_READS and parsed.get("ok"):
            with self._state_lock:
                for page_id in page_ids:
                    entry = self.entries.get(page_id)
                    if entry is not None:
                        entry.suspect = None
        if parsed.get("error_name") == "PAGE_NOT_FOUND":
            pages = _live_pages_dict()
            for page_id in page_ids:
                if pages is not None and page_id not in pages:
                    self.remove_entry(page_id)
        kind = MINT_TOOLS.get(tool_name)
        if parsed.get("reused_cdp_attach"):
            kind = "attached"
        if kind and parsed.get("ok") and parsed.get("page_id"):
            fingerprint = {}
            if kind == "attached":
                fingerprint = {
                    k: parsed.get(k) for k in ("target_id", "title", "url")
                    if parsed.get(k) is not None}
            self.register_entry(
                parsed["page_id"], owner_agent=agent_id,
                generation=generation, kind=kind,
                target_fingerprint=fingerprint)
        if tool_name in {"close_browser_page", "cleanup_test_session"} and (
                parsed.get("ok") or parsed.get("error_name") == "PAGE_NOT_FOUND"):
            for page_id in page_ids:
                self.remove_entry(page_id)

    async def _runner(self, tool_name, fn, body, agent_id, page_ids,
                      sub_info=None):
        """The call's life on the T4 loop: bookkeeping, lock, execution.

        In-flight and the driver lock are held until this coroutine is
        OBSERVED finished -- never until the caller is answered.
        """
        seqs = {}
        with self._state_lock:
            for page_id in page_ids:
                entry = self.entries.get(page_id)
                if entry is not None:
                    entry.call_seq += 1
                    entry.in_flight.add(entry.call_seq)
                    seqs[page_id] = entry.call_seq
        token = None
        try:
            if tool_name in DRIVER_LOCK_TOOLS:
                try:
                    await asyncio.wait_for(
                        self._driver_lock.acquire(), timeout=self.grace_s)
                except (asyncio.TimeoutError, TimeoutError):
                    with self._state_lock:
                        holder = self._public_holder(agent_id)
                    return _as_raw(_error_payload(
                        "T4_BUSY", holder=holder, retryable=True,
                        retry_after_seconds=2,
                        ca_next_action="wait_for_retry_after_then_retry"))
                token = self._next_token()
                if sub_info is not None:
                    sub_info["token"] = token
                with self._state_lock:
                    self._holder = {
                        "token": token, "agent": agent_id, "tool": tool_name,
                        "acquired_at": time.time(), "completing": False}
            failure = self._preflight_entries(tool_name, page_ids, agent_id)
            if failure:
                return _as_raw(failure)
            if tool_name in CLASS_G_TOOLS:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    self._g_executor, _run_coro_sync,
                    lambda: self._class_g_unit(tool_name, fn, body))
            else:
                result = fn(**body)
                if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                    result = await result
            return _as_raw(result)
        finally:
            with self._state_lock:
                for page_id, seq in seqs.items():
                    entry = self.entries.get(page_id)
                    if entry is not None:
                        entry.in_flight.discard(seq)
                        entry.last_used_at = time.time()
            if token is not None:
                await self._release_lock(token)

    # -- attach binding (plan 5.2(a)/(b)/(c)/(f)) -------------------------

    @staticmethod
    def _attach_preflight(body):
        """Pre-connect refusals for bridged cdp_attach_target, in the
        plan's precedence order: a missing target_id parameter answers
        target_id_required (5.2(a)) first; then a missing or vacuous
        expectation answers expectation_required (5.2(b)); snapshot
        absence is checked inside the handler after this passes."""
        target_id = str(body.get("target_id") or "").strip()
        if not target_id:
            return _error_payload(
                "CDP_TARGET_AMBIGUOUS", reason="target_id_required",
                ca_next_action="call_list_cdp_targets_then_retry_with_target_id")
        exp_title = str(body.get("expected_title_substr") or "").strip()
        exp_url = str(body.get("expected_url_substr") or "").strip()
        if not exp_title and not exp_url:
            return _error_payload(
                "CDP_TARGET_AMBIGUOUS", reason="expectation_required",
                ca_next_action="declare_expectation_then_retry")
        return None

    async def _detach_registered_page(self, page_id):
        """Detach-only removal of a registered page (attached
        semantics: pw.stop(), never close the user's app). Runs on
        the T4 loop."""
        self.remove_entry(page_id)
        pages = _live_pages_dict()
        if pages is None:
            return
        state = pages.pop(page_id, None)
        if state:
            await _stop_pw(state)

    def _attach_timeout_cleanup(self, future):
        """Plan 5.2(c) connect-phase deadline disposition: when a
        timed-out attach eventually completes, detach + stop its
        browser from the completion path and leave nothing
        registered."""
        try:
            raw = future.result()
        except Exception:
            return
        parsed = _parse_result(raw)
        if parsed and parsed.get("ok") and parsed.get("page_id"):
            try:
                asyncio.run_coroutine_threadsafe(
                    self._detach_registered_page(parsed["page_id"]),
                    self._loop)
            except Exception:
                pass

    # -- dialog census + class-G unit (plan 5.2(g)) -------------------------

    async def _class_g_unit(self, tool_name, fn, body):
        """Census + handler as ONE thread-shaped unit (plan 5.2(g)).

        Runs on a dedicated class-G worker thread (driven by
        _run_coro_sync), never on the T4 loop. The bridge census runs
        first, inside the same unit and inside the call's deadline; only
        an exactly-one census proceeds, bound by a bridge-set
        expected_hwnd (any caller-supplied value is overwritten). The
        handler's own bound re-check is the safety property; the census
        is the fast path.
        """
        unit_body = dict(body or {})
        pin_error = dialog_pin_error(tool_name, unit_body)
        if pin_error is not None:
            return _as_raw(_dialog_refusal(pin_error))
        if tool_name == "fill_folder_dialog":
            # Plan 5.2(g): forced off for bridged calls, so the census,
            # the re-check and the handler all see the caller pattern only.
            unit_body["auto_try_common_titles"] = False
        enumerator = self.window_enumerator or _default_window_enumerator

        async def _enumerate():
            try:
                return await enumerator()
            except Exception:
                return []

        if tool_name == "fill_folder_dialog":
            raw_wait = unit_body.get("wait_for_dialog_ms", 3000)
            wait_s = (max(0.0, float(raw_wait)) / 1000.0
                      if isinstance(raw_wait, (int, float)) else 3.0)
            deadline = time.monotonic() + wait_s
            while True:
                candidates = fill_census_candidates(
                    await _enumerate(), unit_body)
                if candidates or time.monotonic() >= deadline:
                    break
                await asyncio.sleep(
                    min(0.2, max(0.0, deadline - time.monotonic())))
        else:
            candidates = cancel_census_candidates(
                await _enumerate(), unit_body)
        if not candidates:
            return _as_raw(_dialog_refusal("no_matching_dialog"))
        if len(candidates) > 1:
            return _as_raw(_dialog_refusal("multiple_matches", candidates))
        unit_body["expected_hwnd"] = int(candidates[0].get("hwnd") or 0)
        result = fn(**unit_body)
        if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
            result = await result
        return result

    async def submit(self, tool_name, fn, body, agent_id="",
                     transport="api", generation=""):
        """Route one dispatch through the bridge.

        Returns the handler's raw result (a JSON string, exactly what
        direct execution returns), an error-payload JSON string, or None
        when the bridge does not apply (D-S1: non-api transport or a
        non-bridged tool) and the caller must execute directly.
        """
        if not should_bridge(tool_name, transport):
            return None
        if not self.loop_alive():
            return _as_raw(_error_payload(
                "T4_HOST_UNAVAILABLE", generation=generation,
                retryable=True,
                ca_next_action="retry_after_bridge_recovers"))
        body = body if isinstance(body, dict) else {}
        page_ids = resolve_page_ids(body)
        failure = self._preflight_entries(tool_name, page_ids, agent_id)
        if failure is not None:
            return _as_raw(failure)
        if tool_name == "cdp_attach_target":
            # Plan 5.2(a)/(b) pre-connect refusals, in precedence order:
            # target_id_required, then expectation_required; the
            # snapshot-absence check runs inside the handler after this.
            failure = self._attach_preflight(body)
            if failure is not None:
                return _as_raw(failure)
        deadline = self._deadline(tool_name, body)
        started = time.time()
        sub_info = {"token": None}
        future = asyncio.run_coroutine_threadsafe(
            self._runner(tool_name, fn, body, agent_id, page_ids, sub_info),
            self._loop)
        wrapped = asyncio.wrap_future(future)
        try:
            raw = await asyncio.wait_for(
                asyncio.shield(wrapped), timeout=deadline)
        except (asyncio.TimeoutError, TimeoutError):
            # Rider F-1 (Sprint 1 G3b): mark completing ONLY when the
            # timed-out submission is the holder's own call (token
            # match) -- never for a lock-exempt read or a driver still
            # waiting out its grace period on another caller's lock.
            timed_out_token = sub_info.get("token")
            with self._state_lock:
                if (timed_out_token is not None
                        and self._holder is not None
                        and self._holder.get("token") == timed_out_token):
                    self._holder["completing"] = True
                    self._completing[timed_out_token] = self._holder
            if tool_name in DRIVER_LOCK_TOOLS:
                self._mark_suspect(page_ids, "call_timeout")
            if tool_name == "cdp_attach_target":
                # Plan 5.2(c): deadline during connect/enumeration --
                # the completion path detaches + stops the browser
                # (shielded cleanup) and nothing stays registered.
                future.add_done_callback(self._attach_timeout_cleanup)
            return _as_raw(_error_payload(
                "T4_CALL_TIMEOUT", tool=tool_name,
                deadline_ms=int(deadline * 1000),
                elapsed_ms=int((time.time() - started) * 1000),
                retryable=True,
                ca_next_action="verify_state_before_retry"))
        self._post_result(tool_name, raw, page_ids, agent_id, generation)
        if tool_name == "cdp_attach_target":
            parsed = _parse_result(raw)
            if (parsed and parsed.get("ok")
                    and parsed.get("attach_via") == "browser_level_fallback"
                    and not body.get("allow_browser_level_fallback")):
                # Plan 5.2(f): browser-level fallback is refused by
                # default for bridged attach -- detach, register
                # nothing. With the opt-in, the handler's unique-live-
                # match enumeration already verified the bound page
                # against the expectation, so the result stands.
                if parsed.get("page_id"):
                    detach = asyncio.run_coroutine_threadsafe(
                        self._detach_registered_page(parsed["page_id"]),
                        self._loop)
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(asyncio.wrap_future(detach)),
                            timeout=10.0)
                    except Exception:
                        pass
                return _as_raw(_error_payload(
                    "CDP_ATTACH_FALLBACK_REFUSED",
                    page_id=parsed.get("page_id"),
                    attach_via="browser_level_fallback",
                    ca_next_action="retry_with_allow_browser_level_fallback_true_if_intended"))
        return raw

    # -- status --------------------------------------------------------------

    def _public_holder(self, agent_id):
        if not self._holder:
            return None
        holder = {k: self._holder[k] for k in ("tool", "completing") if k in self._holder}
        if agent_id and agent_id == self._holder.get("agent"):
            holder["agent"] = agent_id
        holder["held_ms"] = int((time.time() - self._holder["acquired_at"]) * 1000)
        return holder

    def status_snapshot(self, agent_id=""):
        """Bridge state for t4_host_status (Sprint 2 tool) and tests.

        Redaction per plan section 2: the caller's own pages in full,
        other owners' pages as counts only.
        """
        with self._state_lock:
            own, others = [], 0
            pages = _live_pages_dict() or {}
            for entry in self.entries.values():
                state = pages.get(entry.page_id)
                if state is None or state.get("unavailable") or entry.reaping:
                    continue
                if agent_id and entry.owner_agent == agent_id:
                    own.append({
                        "page_id": entry.page_id,
                        "owner_agent": entry.owner_agent,
                        "generation": entry.generation,
                        "loop_epoch": entry.loop_epoch,
                        "kind": entry.kind,
                        "target_fingerprint": dict(entry.target_fingerprint),
                        "suspect": entry.suspect,
                        "call_seq": entry.call_seq,
                        "in_flight": len(entry.in_flight),
                        "created_at": entry.created_at,
                        "last_used_at": entry.last_used_at,
                        "age_s": int(time.time() - entry.created_at),
                    })
                else:
                    others += 1
            holder = self._public_holder(agent_id)
            return {
                "loop_alive": self.loop_alive(),
                "loop_epoch": self.loop_epoch,
                "holder": holder,
                "pages": own,
                "other_owners_page_count": others,
                "quarantine_records": [
                    {k: record[k] for k in ("state", "late_action_possible", "reason")
                     if k in record} for record in self.quarantine_records],
            }


def _run_coro_sync(factory):
    """Drive a zero-await class-G coroutine body on a worker thread."""
    return asyncio.run(factory())


_BRIDGE = None
_BRIDGE_LOCK = threading.Lock()


def get_bridge():
    global _BRIDGE
    with _BRIDGE_LOCK:
        if _BRIDGE is None:
            _BRIDGE = T4Bridge()
        return _BRIDGE


async def submit_bridged(tool_name, fn, body, agent_id="", transport="api",
                         generation=""):
    """Seam entry point used by orchestrator_server's invoke site."""
    return await get_bridge().submit(
        tool_name, fn, body, agent_id=agent_id, transport=transport,
        generation=generation)
