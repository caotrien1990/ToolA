"""Single source of truth for the /livez contract (campaign livez-drift-20261023).

Every consumer of the daemon's /livez payload (watchdog_a, as_bat_helpers,
restart_control_service) classifies the payload through classify_livez here
instead of carrying its own copy of the acceptance rule -- the drift class
that caused the 2026-10-23 false-restart incident (the server bumped
livez_protocol 1 -> 2 while consumers still demanded == 1).

Pure and stdlib-only: this module never fetches. Fetching (and mapping fetch
errors / timeouts to outcome "error") stays with each consumer's existing
fetch mechanism, unchanged.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

SERVICE_NAME = "project-orchestrator"

READY_PHASES = frozenset({"ready", "ready_degraded"})
NONREADY_PHASES = frozenset({"booting", "boot_write_sweep", "stopping"})

UNREACHABLE = "UNREACHABLE"
INVALID = "INVALID"
ALIVE_LEGACY = "ALIVE_LEGACY"
ALIVE_READY = "ALIVE_READY"
ALIVE_BOOTING = "ALIVE_BOOTING"
ALIVE_STOPPING = "ALIVE_STOPPING"
MISMATCH = "MISMATCH"

ALIVE_STATES = frozenset(
    {ALIVE_LEGACY, ALIVE_READY, ALIVE_BOOTING, ALIVE_STOPPING, MISMATCH}
)


def _env_grace() -> float:
    raw = os.environ.get("ORCH_BOOT_GRACE_S")
    if raw:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 600.0
        if math.isfinite(value):
            return value
    return 600.0


BOOT_GRACE_S = _env_grace()


@dataclass(frozen=True)
class Assessment:
    """Verdict of classify_livez for one /livez fetch result."""

    state: str
    protocol: Any
    phase: str
    boot_age_s: Optional[float]
    booting: bool
    detail: str

    @property
    def is_alive(self) -> bool:
        return self.state in ALIVE_STATES


def fetch_result_ok(payload: Any) -> dict:
    return {"outcome": "ok", "payload": payload}


def fetch_result_error(error: Any) -> dict:
    return {"outcome": "error", "error": str(error)}


def _parse_boot_age(raw: Any) -> Optional[float]:
    """boot_age is valid iff int/float (not bool, finite) or a string that
    float() parses to a finite value (mirrors the historical float() call).
    String "inf"/"nan" parse to non-finite values and are invalid, exactly
    like their numeric forms."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            value = float(raw)
        except OverflowError:
            return None
    elif isinstance(raw, str):
        try:
            value = float(raw)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(value):
        return None
    return value


def classify_livez(
    result: Mapping[str, Any], grace_s: Optional[float] = None
) -> Assessment:
    """Classify one /livez fetch result. Pure: no I/O.

    ``result`` is a FetchResult mapping: {"outcome": "ok", "payload": <parsed
    JSON>} or {"outcome": "error", "error": <description>}. ``grace_s`` is
    accepted for symmetry with the adapter verdicts; the state decision
    itself does not depend on grace.
    """
    if not isinstance(result, Mapping) or result.get("outcome") != "ok":
        return Assessment(UNREACHABLE, None, "", None, False, "")
    payload = result.get("payload")
    if not isinstance(payload, dict):
        return Assessment(INVALID, None, "", None, False, "non_dict")

    protocol = payload.get("livez_protocol", 1)
    phase = str(payload.get("startup_phase") or "")
    boot_age_s = _parse_boot_age(payload.get("boot_age_s"))

    def invalid(detail: str) -> Assessment:
        return Assessment(INVALID, protocol, phase, boot_age_s, False, detail)

    if payload.get("ok") is not True:
        return invalid("ok_not_true")
    if "service" in payload and payload["service"] != SERVICE_NAME:
        return invalid("service_mismatch")
    if isinstance(protocol, bool) or not isinstance(protocol, int) or protocol < 1:
        return invalid("protocol_type")
    if protocol == 1:
        return Assessment(ALIVE_LEGACY, protocol, phase, boot_age_s, False, "")

    phase_raw = payload.get("startup_phase")
    if not isinstance(phase_raw, str):
        state = MISMATCH
    elif phase in READY_PHASES:
        state = ALIVE_READY
    elif phase == "stopping":
        state = ALIVE_STOPPING
    else:
        state = ALIVE_BOOTING
    detail = ""
    if state in (ALIVE_BOOTING, MISMATCH, ALIVE_STOPPING) and boot_age_s is None:
        detail = "boot_age_invalid"
    return Assessment(state, protocol, phase, boot_age_s, state == ALIVE_BOOTING, detail)


def rcs_skip_verdict(
    assessment: Assessment, grace_s: Optional[float] = None
) -> tuple:
    """Adapter: map an Assessment to restart_control_service's (skip, reason).

    Reasons reproduce _should_skip_heal_during_boot verbatim; the single new
    reason is "contract_invalid" for INVALID payloads the historical function
    silently coerced (see plan D6).
    """
    grace = BOOT_GRACE_S if grace_s is None else grace_s
    state = assessment.state
    if state == UNREACHABLE:
        return False, "livez_unreachable"
    if state == INVALID:
        if assessment.detail == "non_dict":
            return False, "invalid_payload"
        return False, "contract_invalid"
    if state == ALIVE_LEGACY:
        return False, "protocol_1_legacy"
    if state == ALIVE_READY:
        return False, "already_ready"
    if state == ALIVE_STOPPING:
        if assessment.boot_age_s is None:
            return False, "invalid_boot_age"
        return False, "boot_grace_expired"
    # ALIVE_BOOTING or MISMATCH share the historical booting rows.
    if assessment.boot_age_s is None:
        return False, "invalid_boot_age"
    if assessment.boot_age_s >= grace:
        return False, "boot_grace_expired"
    return True, f"booting_phase={assessment.phase}_age={assessment.boot_age_s:.1f}s"
