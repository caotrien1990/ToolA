"""One cap for long process output: keep the HEAD and the TAIL, never just the head.

A process writes its conclusion LAST -- pytest's summary and FAILED list, git's
own reason after the hook noise it relays on stderr -- so a head-only cut drops
exactly the line a caller needs (#22, #27). Every process-output cap (the command,
script and test-script streams, git's failure text) routes here, so the class
cannot come back one call site at a time. The marker keeps the house word
``truncated``: callers and tests already look for it.
"""
from __future__ import annotations

_MARKER = "\n...[truncated {n} chars]...\n"


def cap_head_tail(text: str | None, limit: int) -> tuple[str, int]:
    """Return ``(capped, omitted_chars)``; ``capped`` never exceeds ``limit``.

    The tail gets two thirds of the room because the verdict lives there. A
    limit too small to hold the marker keeps the tail alone.
    """
    value = text or ""
    if (limit or 0) <= 0 or len(value) <= limit:
        return value, 0
    room = limit - len(_MARKER.format(n=len(value)))
    if room < 2:
        return value[-limit:], len(value) - limit
    head = room // 3
    tail = room - head
    omitted = len(value) - head - tail
    return value[:head] + _MARKER.format(n=omitted) + value[-tail:], omitted
