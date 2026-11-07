"""Per-family listener supervisor: self-healing for the daemon's TCP listeners.

Implements the listener-recovery plan (IPV4_LISTENER_RECOVERY_PLAN_20261106,
sections 4.1-4.13). The daemon historically bound ONE aiohttp TCPSite on the
name 'localhost', which creates ONE asyncio Server holding both the IPv4 and
IPv6 sockets; when the IPv4 accept loop died mid-life (WinError 64 in the
proactor accept task, exception never retrieved), IPv4 stayed dead until a
human restarted the daemon while IPv6 kept serving. This module supervises
per-family sites split at boot from the same 'localhost' resolution:

- layer 1 detection: the event loop's exception handler recognises accept
  failures and marks the affected family suspect;
- layer 2 detection: a per-family self-probe (fresh connection every cycle,
  GET /health, 5 s start-to-start cadence, 2 s timeout) confirms death after
  3 consecutive failures, counted only after the daemon reports ready
  (arming: /health is 503 through the boot sweep, so pre-ready probe
  failures are observational and never counted);
- recovery: only the dead family's site is stopped and recreated, and
  success requires positive evidence (listening socket present AND two
  consecutive fresh-connect HTTP 200 probes at least 1 s apart);
- budgets: at most 3 attempts per family per sliding 10-minute window,
  backoff 5/15/45 s, lifetime cap 10 attempts per family, flap give-up at
  3 confirmed deaths in a trailing 30 minutes; give-up is LATCHED for the
  process lifetime (degrade loud, never silent), while unaided revival
  (3 consecutive passing probes) is honoured from every dead state;
- kill-switch: ORCHESTRATOR_LISTENER_RECOVERY = on (default) / detect
  (detection and logging on, recovery off). No value disables detection or
  logging; an unrecognised value fails safe to detect with an ERROR log.
  The flag is read once at boot and does not undo the site split.

The supervisor never restarts the daemon, never changes pid/generation, and
is layered UNDER the whole-daemon auto-heal controller (plan section 4.10):
it masks nothing from /health, so the controller keeps seeing exactly what
it sees today.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

FLAG_ENV = "ORCHESTRATOR_LISTENER_RECOVERY"

MARKER_SUSPECT = "LISTENER_SUSPECT"
MARKER_DEAD_CONFIRMED = "LISTENER_DEAD_CONFIRMED"
MARKER_RECOVERY_ATTEMPT = "LISTENER_RECOVERY_ATTEMPT"
MARKER_RECOVERED = "LISTENER_RECOVERED"
MARKER_RECOVERY_DEFERRED = "LISTENER_RECOVERY_DEFERRED"
MARKER_RECOVERY_EXHAUSTED = "LISTENER_RECOVERY_EXHAUSTED"
MARKER_REVIVED = "LISTENER_REVIVED"
MARKER_SUPERVISOR_DIED = "LISTENER_SUPERVISOR_DIED"

REASON_ACCEPT_EVENT = "accept_event"
REASON_PROBE = "probe"
REASON_WINDOW_EXHAUSTED = "window_exhausted"
REASON_FLAP = "flap"
REASON_LIFETIME_CAP = "lifetime_cap"

PROBE_USER_AGENT = "ToolA-listener-supervisor"


def _iso(wall_ts: float | None) -> str | None:
    if wall_ts is None:
        return None
    return datetime.fromtimestamp(wall_ts, tz=timezone.utc).isoformat()


def parse_recovery_mode(environ=None, *, log_invalid: bool = True) -> str:
    """Resolve the kill-switch (plan 4.7). Default 'on'; only the exact
    values 'on' and 'detect' are recognised (case-insensitive); anything
    else fails safe to 'detect' and logs an ERROR (once, at boot)."""
    env = os.environ if environ is None else environ
    raw = env.get(FLAG_ENV)
    if raw is None:
        return "on"
    value = str(raw).strip().lower()
    if value in ("on", "detect"):
        return value
    if log_invalid:
        logger.error(
            "%s has unrecognised value %r; failing safe to 'detect' "
            "(detection and logging on, recovery off)",
            FLAG_ENV, raw,
        )
    return "detect"


def derive_listener_addresses(port: int, hostname: str = "localhost"):
    """OBJ-1 precondition (plan 4.1): the managed set is the first resolved
    address per family from getaddrinfo(hostname, port) at boot.

    Returns a list of (family_key, address, address_family) in resolution
    order. Further addresses of an already-represented family are ignored
    here and reported by the caller's boot log via `extra_count`.
    """
    infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    seen: dict[int, str] = {}
    order: list[int] = []
    extras = 0
    for fam, _type, _proto, _canon, sockaddr in infos:
        if fam not in (socket.AF_INET, socket.AF_INET6):
            continue
        if fam in seen:
            extras += 1
            continue
        seen[fam] = str(sockaddr[0])
        order.append(fam)
    result = []
    for fam in order:
        key = "ipv4" if fam == socket.AF_INET else "ipv6"
        result.append((key, seen[fam], fam))
    return result, extras


@dataclass(frozen=True)
class SupervisorParams:
    """Plan 4.3/4.5 constants. Tests may scale the time parameters; the
    ledger-gating switch exists only for the section 4.12 soak gate, whose
    harness disables gating of BOTH ledgers (accounting is still recorded)
    so the prescribed cycle count is reachable in bounded time."""

    probe_interval_s: float = 5.0
    probe_timeout_s: float = 2.0
    confirm_failures: int = 3
    revive_passes: int = 3
    verify_pass_gap_s: float = 1.0
    window_s: float = 600.0
    attempts_per_window: int = 3
    lifetime_cap: int = 10
    backoff_s: tuple = (5.0, 15.0, 45.0)
    flap_window_s: float = 1800.0
    flap_deaths: int = 3
    exhausted_relog_s: float = 300.0
    gate_ledgers: bool = True


@dataclass
class FamilyState:
    key: str
    address: str
    address_family: int
    site: object = None  # live TCPSite for this family, or None
    boot: str = "bound"  # "bound" | "bind_failed" (at boot; historical)
    state: str = "ok"  # ok|suspect|dead_confirmed|recovering|dead_deferred|dead_exhausted
    state_since_wall: float = 0.0
    last_accept_fail_at_wall: float | None = None
    last_error: str | None = None
    last_recovery_result: str | None = None  # success|bind_failed|verify_failed
    # The /health `reason` field (plan 4.8) holds ONLY the state-entry
    # or latch reason: window_exhausted | flap | lifetime_cap | None.
    # The internal cause of a suspect transition lives in suspect_cause
    # and is never exposed in the snapshot.
    reason: str | None = None
    suspect_cause: str | None = None
    latched: bool = False
    latch_reason: str | None = None
    consec_probe_fail: int = 0
    consec_probe_pass: int = 0
    attempts: deque = field(default_factory=deque)  # monotonic timestamps
    attempts_lifetime: int = 0
    deaths: deque = field(default_factory=deque)  # monotonic timestamps
    next_attempt_at: float | None = None
    exhausted_logged_at: float | None = None


def _site_is_listening(site) -> bool:
    """Positive evidence that a started site holds a listening socket.
    Defensive by design (Phase C C-5): enumerating a dead transport socket
    can raise on Windows, so this never calls getsockname and never raises."""
    try:
        if site is None:
            return False
        server = getattr(site, "_server", None)
        if server is None:
            return False
        sockets = getattr(server, "sockets", None)
        return bool(sockets)
    except Exception:
        return False


async def _probe_once(address: str, port: int, timeout_s: float) -> bool:
    """One fresh-connection probe: connect, GET /health, require HTTP 200.
    A new connection is opened (and closed) for every probe; keep-alive is
    never reused (plan 4.3). Never raises."""

    async def _inner() -> bool:
        reader, writer = await asyncio.open_connection(address, port)
        try:
            request = (
                "GET /health HTTP/1.1\r\n"
                f"Host: {address}\r\n"
                f"User-Agent: {PROBE_USER_AGENT}\r\n"
                "Connection: close\r\n\r\n"
            )
            writer.write(request.encode("latin1"))
            await writer.drain()
            status_line = await reader.readline()
            parts = status_line.split(b" ")
            return len(parts) >= 2 and parts[1] == b"200"
        finally:
            try:
                writer.close()
            except Exception:
                pass

    try:
        return await asyncio.wait_for(_inner(), timeout_s)
    except Exception:
        return False


class ListenerSupervisor:
    """Owns the per-family listener state machine (plan 4.6).

    Constructed in _run_server AFTER the per-family sites are started and
    BEFORE boot recovery runs; its task is a named member of the shutdown
    cancel set and is cancelled before runner.cleanup() (plan 4.9).
    """

    def __init__(
        self,
        *,
        runner,
        port: int,
        families,
        mode: str,
        ready_check,
        stopping_check,
        params: SupervisorParams | None = None,
        site_factory=None,
        probe_fn=None,
        monotonic=time.monotonic,
        wall=time.time,
    ):
        self._runner = runner
        self._port = int(port)
        self._mode = mode if mode in ("on", "detect") else "detect"
        self._ready_check = ready_check
        self._stopping_check = stopping_check
        self.params = params or SupervisorParams()
        self._site_factory = site_factory or self._default_site_factory
        self._probe_fn = probe_fn or self._default_probe
        self._mono = monotonic
        self._wall = wall
        self._last_cycle_at_wall: float | None = None
        self._families: dict[str, FamilyState] = {}
        for fam in families:
            self.register_family(fam)

    def register_family(self, fam) -> FamilyState:
        """Add one family to the managed set. The boot wiring starts each
        family's site before the supervisor task exists, so families are
        registered as their boot outcome becomes known (plan 4.9 order:
        handler first, then sites, then the task before boot recovery).
        A family whose bind failed at boot enters the machinery in
        suspect, not as a boot failure (plan 4.1/4.6)."""
        state = FamilyState(
            key=fam["key"],
            address=fam["address"],
            address_family=fam["address_family"],
            site=fam.get("site"),
            boot=fam.get("boot", "bound"),
            state_since_wall=self._wall(),
        )
        if state.boot == "bind_failed":
            state.state = "suspect"
            state.suspect_cause = REASON_PROBE
        self._families[state.key] = state
        return state

    # -- construction helpers -------------------------------------------------

    def _default_site_factory(self, fam: FamilyState):
        from aiohttp import web

        return web.TCPSite(self._runner, fam.address, self._port)

    async def _default_probe(self, fam: FamilyState) -> bool:
        return await _probe_once(fam.address, self._port, self.params.probe_timeout_s)

    # -- read-only views --------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def armed(self) -> bool:
        try:
            return bool(self._ready_check())
        except Exception:
            return False

    def family(self, key: str) -> FamilyState | None:
        return self._families.get(key)

    def _attempts_in_window(self, fam: FamilyState, now: float) -> int:
        cutoff = now - self.params.window_s
        return sum(1 for ts in fam.attempts if ts > cutoff)

    def _deaths_in_window(self, fam: FamilyState, now: float) -> int:
        cutoff = now - self.params.flap_window_s
        return sum(1 for ts in fam.deaths if ts > cutoff)

    def _state_fields(self, fam: FamilyState, now: float) -> str:
        return (
            f"family={fam.key} state={fam.state} attempts_window="
            f"{self._attempts_in_window(fam, now)} attempts_lifetime="
            f"{fam.attempts_lifetime} deaths_window="
            f"{self._deaths_in_window(fam, now)} recovery_latched={fam.latched}"
        )

    def health_snapshot(self) -> dict:
        """The additive /health `listener` object (plan 4.8). Exactly this
        key set; nothing computed here may fail the health handler."""
        now = self._mono()
        snapshot: dict = {
            "recovery": self._mode,
            "armed": self.armed,
            "supervisor": {"last_cycle_at": _iso(self._last_cycle_at_wall)},
        }
        for key in ("ipv4", "ipv6"):
            fam = self._families.get(key)
            if fam is None:
                continue
            try:
                snapshot[key] = {
                    "state": fam.state,
                    "address": fam.address,
                    "since": _iso(fam.state_since_wall),
                    "boot": fam.boot,
                    "last_accept_fail_at": _iso(fam.last_accept_fail_at_wall),
                    "last_error": fam.last_error,
                    "last_recovery_result": fam.last_recovery_result,
                    "reason": fam.reason,
                    "attempts_window": self._attempts_in_window(fam, now),
                    "attempts_lifetime": fam.attempts_lifetime,
                    "deaths_window": self._deaths_in_window(fam, now),
                    "recovery_latched": fam.latched,
                }
            except Exception:
                # The listener object is additive observability (plan
                # 4.8/B-14a): a malformed family entry must never break
                # the snapshot or the /health handler behind it.
                logger.exception(
                    "listener health snapshot failed family=%s", key)
        return snapshot

    # -- layer 1: loop exception handler feed -----------------------------------

    def note_accept_failure(self, family_key: str | None = None, error=None) -> None:
        """Record an accept failure observed by the loop exception handler
        (plan 4.2). Unattributable failures mark BOTH families suspect:
        a false suspect costs probe cycles; a missed one costs the outage.
        An already-dead family only gets the fields updated (its state
        machine is already running)."""
        try:
            self._note_accept_failure(family_key, error)
        except Exception:
            logger.exception("listener supervisor: accept-failure note failed")

    def _note_accept_failure(self, family_key, error) -> None:
        if family_key is not None and family_key in self._families:
            targets = [self._families[family_key]]
        else:
            targets = list(self._families.values())
        now_wall = self._wall()
        for fam in targets:
            fam.last_accept_fail_at_wall = now_wall
            if error is not None:
                fam.last_error = f"{type(error).__name__}: {error}"[:200]
            if fam.state == "ok":
                fam.state = "suspect"
                fam.state_since_wall = now_wall
                fam.suspect_cause = REASON_ACCEPT_EVENT
                fam.consec_probe_pass = 0
                logger.error(
                    "%s family=%s reason=%s error=%s",
                    MARKER_SUSPECT, fam.key, REASON_ACCEPT_EVENT, fam.last_error,
                )

    # -- main loop ---------------------------------------------------------------

    async def run(self) -> None:
        while True:
            try:
                if self._stopping_check():
                    return
            except Exception:
                # A broken stopping check must not spin the loop; stand
                # down instead (the stale heartbeat shows the failure).
                logger.exception(
                    "listener supervisor: stopping check failed; standing down")
                return
            cycle_start = self._mono()
            try:
                await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("listener supervisor cycle failed; continuing")
            try:
                self._last_cycle_at_wall = self._wall()
            except Exception:
                logger.exception("listener supervisor: heartbeat stamp failed")
            elapsed = self._mono() - cycle_start
            wait = max(0.0, self.params.probe_interval_s - elapsed)
            await asyncio.sleep(wait)

    async def _cycle(self) -> None:
        armed = self.armed
        for fam in list(self._families.values()):
            if self._stopping_check():
                return
            try:
                await self._cycle_family(fam, armed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "listener supervisor: family cycle failed family=%s", fam.key
                )

    async def _cycle_family(self, fam: FamilyState, armed: bool) -> None:
        now = self._mono()
        if fam.state in ("ok", "suspect"):
            await self._probe_live_family(fam, armed)
        elif fam.state in ("dead_confirmed", "dead_deferred", "dead_exhausted"):
            await self._cycle_dead_family(fam, armed, now)
        # "recovering" is transient inside _attempt(); a cycle never starts
        # in it because attempts are awaited within the cycle that began them.

    async def _probe_live_family(self, fam: FamilyState, armed: bool) -> None:
        ok = await self._probe_fn(fam)
        if ok:
            fam.consec_probe_fail = 0
            if fam.state == "suspect":
                fam.state = "ok"
                fam.state_since_wall = self._wall()
                fam.consec_probe_pass = 0
                # Only the internal suspect cause clears; the 4.8 reason
                # (a latched give-up reason) is retained untouched.
                fam.suspect_cause = None
                logger.info(
                    "listener family recovered from suspect family=%s", fam.key
                )
            return
        # Failed probe. Plan F-8/4.3: before arming, probe failures are
        # observational and never counted (the boot sweep answers 503).
        if not armed:
            return
        fam.consec_probe_pass = 0
        fam.consec_probe_fail += 1
        if fam.state == "ok":
            fam.state = "suspect"
            fam.state_since_wall = self._wall()
            fam.suspect_cause = REASON_PROBE
            logger.error(
                "%s family=%s reason=%s", MARKER_SUSPECT, fam.key, REASON_PROBE
            )
        if fam.state == "suspect" and fam.consec_probe_fail >= self.params.confirm_failures:
            self._confirm_death(fam)

    async def _cycle_dead_family(self, fam: FamilyState, armed: bool, now: float) -> None:
        if fam.state == "dead_exhausted":
            last = fam.exhausted_logged_at
            if last is None or now - last >= self.params.exhausted_relog_s:
                fam.exhausted_logged_at = now
                logger.error(
                    "%s %s reason=%s",
                    MARKER_RECOVERY_EXHAUSTED,
                    self._state_fields(fam, now),
                    fam.reason,
                )
        ok = await self._probe_fn(fam)
        if ok:
            fam.consec_probe_fail = 0
            fam.consec_probe_pass += 1
            if fam.consec_probe_pass >= self.params.revive_passes:
                self._revive(fam)
            return
        fam.consec_probe_pass = 0
        if not armed or self._mode != "on":
            return
        if fam.state == "dead_deferred":
            # The supervisor re-evaluates the window at least once per
            # probe cycle (plan 4.5); when it frees, the next attempt is
            # scheduled after the shortest backoff step. The family STAYS
            # dead_deferred until the attempt actually starts (plan 4.6:
            # dead_deferred -> recovering via a fresh attempt).
            if self._attempt_allowed(fam, now):
                if fam.next_attempt_at is None:
                    fam.next_attempt_at = now + self.params.backoff_s[0]
                    logger.info(
                        "listener recovery window freed; attempt scheduled %s",
                        self._state_fields(fam, now),
                    )
                elif now >= fam.next_attempt_at:
                    await self._attempt(fam)
            return
        if fam.state == "dead_confirmed":
            if fam.next_attempt_at is None:
                fam.next_attempt_at = now + self._backoff_for(fam, now)
            if now >= fam.next_attempt_at and self._attempt_allowed(fam, now):
                await self._attempt(fam)

    # -- state transitions ---------------------------------------------------------

    def _backoff_for(self, fam: FamilyState, now: float) -> float:
        used = self._attempts_in_window(fam, now)
        idx = min(used, len(self.params.backoff_s) - 1)
        return self.params.backoff_s[idx]

    def _attempt_allowed(self, fam: FamilyState, now: float) -> bool:
        if not self.params.gate_ledgers:
            return True
        if fam.attempts_lifetime >= self.params.lifetime_cap:
            return False
        return self._attempts_in_window(fam, now) < self.params.attempts_per_window

    def _enter_exhausted(self, fam: FamilyState, reason: str, now: float) -> None:
        fam.state = "dead_exhausted"
        fam.state_since_wall = self._wall()
        fam.reason = reason
        fam.latched = True
        fam.latch_reason = reason
        fam.next_attempt_at = None
        fam.exhausted_logged_at = now
        logger.error(
            "%s %s reason=%s",
            MARKER_RECOVERY_EXHAUSTED,
            self._state_fields(fam, now),
            reason,
        )

    def _enter_deferred(self, fam: FamilyState, now: float) -> None:
        fam.state = "dead_deferred"
        fam.state_since_wall = self._wall()
        fam.reason = REASON_WINDOW_EXHAUSTED
        fam.next_attempt_at = None
        logger.error(
            "%s %s reason=%s",
            MARKER_RECOVERY_DEFERRED,
            self._state_fields(fam, now),
            REASON_WINDOW_EXHAUSTED,
        )

    def _confirm_death(self, fam: FamilyState) -> None:
        now = self._mono()
        fam.deaths.append(now)
        fam.consec_probe_fail = 0
        fam.consec_probe_pass = 0
        fam.state = "dead_confirmed"
        fam.state_since_wall = self._wall()
        fam.reason = None
        fam.next_attempt_at = None
        logger.error(
            "%s %s", MARKER_DEAD_CONFIRMED, self._state_fields(fam, now)
        )
        if fam.latched:
            # Re-death after a latched revival: straight back to
            # dead_exhausted with zero attempts (plan 4.6).
            self._enter_exhausted(fam, fam.latch_reason or REASON_FLAP, now)
            return
        if self.params.gate_ledgers and self._mode == "on":
            if self._deaths_in_window(fam, now) >= self.params.flap_deaths:
                self._enter_exhausted(fam, REASON_FLAP, now)
                return
            if fam.attempts_lifetime >= self.params.lifetime_cap:
                self._enter_exhausted(fam, REASON_LIFETIME_CAP, now)
                return
            if self._attempts_in_window(fam, now) >= self.params.attempts_per_window:
                self._enter_deferred(fam, now)
                return
            fam.next_attempt_at = now + self._backoff_for(fam, now)

    def _revive(self, fam: FamilyState) -> None:
        was_latched = fam.latched
        fam.state = "ok"
        fam.state_since_wall = self._wall()
        fam.consec_probe_fail = 0
        fam.consec_probe_pass = 0
        fam.next_attempt_at = None
        if not was_latched:
            fam.reason = None
        # A latched family's latch and reason are RETAINED across an
        # unaided revival (plan 4.6); only a daemon restart clears them.
        logger.error(
            "%s %s latched=%s",
            MARKER_REVIVED,
            self._state_fields(fam, self._mono()),
            was_latched,
        )

    # -- recovery (plan 4.4) -------------------------------------------------------

    async def _attempt(self, fam: FamilyState) -> None:
        # Shutdown/arming interlocks: checked before the attempt and again
        # between the stop and recreate steps (plan 4.4 step 1, 4.9).
        if self._stopping_check() or not self.armed:
            return
        p = self.params
        old_site = fam.site
        if old_site is None:
            # Plan 4.4 step 1: a family with no live site (its boot bind
            # failed) skips the stop step; the skip is logged as part of
            # the attempt.
            logger.error(
                "listener recovery: no live site to stop family=%s "
                "(boot=%s); proceeding to recreate",
                fam.key, fam.boot,
            )
        if old_site is not None:
            try:
                await old_site.stop()
            except Exception:
                logger.exception(
                    "listener recovery: old site stop failed family=%s; "
                    "continuing to recreate",
                    fam.key,
                )
            fam.site = None
        # Between-steps interlock recheck on BOTH paths (plan 4.4 step 1:
        # re-check the shutdown/arming interlocks before the recreate
        # step; an attempt never spans an interlock change). An abort
        # here has consumed no budget: attempts count at bind start.
        if self._stopping_check() or not self.armed:
            return
        if old_site is not None and _site_is_listening(old_site):
            # Evidence integrity (plan 4.4/OBJ-2): the old listener
            # survived its stop, so fresh-connect probes could be
            # answered by IT rather than by a recreated site, and a
            # recreate could dual-bind on Windows. Fail the attempt;
            # the 4.5 backoff governs the next one.
            fam.last_recovery_result = "verify_failed"
            fam.last_error = "old site still listening after stop"
            logger.error(
                "listener recovery aborted family=%s: old site still "
                "listening after stop",
                fam.key,
            )
            self._attempt_failed(fam)
            return
        # Bind start: the attempt is counted HERE (plan 4.5: counted at
        # bind start; bind failures count), the state enters recovering,
        # and the attempt marker is logged.
        fam.attempts.append(self._mono())
        fam.attempts_lifetime += 1
        fam.state = "recovering"
        fam.state_since_wall = self._wall()
        fam.next_attempt_at = None
        logger.error(
            "%s %s", MARKER_RECOVERY_ATTEMPT, self._state_fields(fam, self._mono())
        )
        new_site = None
        try:
            new_site = self._site_factory(fam)
            await new_site.start()
        except Exception as exc:
            fam.last_recovery_result = "bind_failed"
            fam.last_error = f"{type(exc).__name__}: {exc}"[:200]
            logger.error(
                "listener recovery bind failed family=%s error=%s",
                fam.key, fam.last_error,
            )
            self._attempt_failed(fam)
            return
        # Only a successfully started site becomes the family's site
        # (plan 4.4): a failed bind leaves fam.site None, so the next
        # attempt takes the skip-stop path instead of stopping a
        # never-started site.
        fam.site = new_site
        # Evidence standard (OBJ-2): listening socket present AND two
        # consecutive fresh-connect 200 probes at least 1 s apart.
        verified = _site_is_listening(new_site)
        if verified:
            first = await self._probe_fn(fam)
            if first:
                await asyncio.sleep(p.verify_pass_gap_s)
                second = await self._probe_fn(fam)
                verified = bool(second)
            else:
                verified = False
        if verified:
            fam.state = "ok"
            fam.state_since_wall = self._wall()
            fam.last_recovery_result = "success"
            fam.consec_probe_fail = 0
            fam.consec_probe_pass = 0
            if not fam.latched:
                fam.reason = None
            logger.error(
                "%s %s", MARKER_RECOVERED, self._state_fields(fam, self._mono())
            )
            return
        fam.last_recovery_result = "verify_failed"
        logger.error(
            "listener recovery verification failed family=%s", fam.key
        )
        self._attempt_failed(fam)

    def _attempt_failed(self, fam: FamilyState) -> None:
        now = self._mono()
        if self._mode == "on" and self.params.gate_ledgers:
            if fam.attempts_lifetime >= self.params.lifetime_cap:
                self._enter_exhausted(fam, REASON_LIFETIME_CAP, now)
                return
            if self._attempts_in_window(fam, now) >= self.params.attempts_per_window:
                self._enter_deferred(fam, now)
                return
        fam.state = "dead_confirmed"
        fam.state_since_wall = self._wall()
        fam.reason = None
        fam.next_attempt_at = now + self._backoff_for(fam, now)


# -- loop exception handler (plan 4.2) -------------------------------------------


def _looks_like_accept_failure(context) -> bool:
    """Heuristic attribution for the proactor accept death. The supervisor
    accepts suspicion on modest evidence (plan 4.2): an OSError carrying
    WinError 64, an exception or handler message mentioning accept, or a
    failed task whose coroutine name mentions accept."""
    try:
        exc = context.get("exception")
        if isinstance(exc, OSError):
            if getattr(exc, "winerror", None) == 64:
                return True
            if "accept" in str(exc).lower():
                return True
        message = str(context.get("message") or "")
        if "accept" in message.lower():
            return True
        task = context.get("task")
        if task is not None:
            name = ""
            try:
                name = task.get_name() or ""
            except Exception:
                name = ""
            coro = getattr(task, "_coro", None)
            qual = getattr(coro, "__qualname__", "") if coro is not None else ""
            if "accept" in (name + " " + qual).lower():
                return True
    except Exception:
        return False
    return False


def install_loop_exception_handler(loop, supervisor):
    """Register the codebase's first loop exception handler (plan 4.2).
    Only accept failures are intercepted; every other context chains to the
    previous (or default) handler untouched, and a handler-internal error
    can never break the loop — it falls back to chaining."""
    previous = loop.get_exception_handler()

    def _chain(context):
        if previous is not None:
            previous(loop, context)
        else:
            loop.default_exception_handler(context)

    def _handler(_loop, context):
        try:
            if supervisor is not None and _looks_like_accept_failure(context):
                supervisor.note_accept_failure(error=context.get("exception"))
                return
        except Exception:
            pass
        try:
            _chain(context)
        except Exception:
            pass

    loop.set_exception_handler(_handler)
    return _handler


def make_supervisor_died_callback():
    """Done-callback for the supervisor task (registered in the wiring,
    plan 4.9): an unexpected supervisor death is logged as
    LISTENER_SUPERVISOR_DIED and surfaced via the stale heartbeat."""

    def _callback(task) -> None:
        try:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                logger.error("%s error=%r", MARKER_SUPERVISOR_DIED, exc)
        except Exception:
            pass

    return _callback


# -- process-wide active supervisor (for the /health wiring) ----------------------

_ACTIVE_SUPERVISOR: ListenerSupervisor | None = None


def set_active_supervisor(supervisor) -> None:
    global _ACTIVE_SUPERVISOR
    _ACTIVE_SUPERVISOR = supervisor


def get_active_supervisor():
    return _ACTIVE_SUPERVISOR


def health_listener_snapshot() -> dict:
    """The additive /health `listener` object. With no supervisor booted
    (embedders, tests), the object still reports the flag position and an
    un-armed, cycle-less supervisor block, with no family entries (the
    managed set only exists after a real boot derives it)."""
    supervisor = _ACTIVE_SUPERVISOR
    if supervisor is not None:
        return supervisor.health_snapshot()
    return {
        "recovery": parse_recovery_mode(log_invalid=False),
        "armed": False,
        "supervisor": {"last_cycle_at": None},
    }
