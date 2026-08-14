"""Canonical council identity helpers shared by runtime and policy code."""
from __future__ import annotations

from typing import Iterable


def canonical_id(value: object) -> str:
    """Return the one canonical representation for identity-bearing values."""
    return str(value or "").strip().casefold()


def assert_unique_canonical_ids(values: Iterable[object], *, label: str = "identity") -> None:
    """Fail startup when two configured identities collapse to the same key."""
    seen: dict[str, str] = {}
    for value in values:
        raw = str(value or "").strip()
        key = canonical_id(raw)
        if not key:
            continue
        prior = seen.get(key)
        if prior is not None and prior != raw:
            raise AssertionError(
                f"duplicate {label} after canonicalization: {prior!r} and {raw!r} -> {key!r}"
            )
        seen[key] = raw