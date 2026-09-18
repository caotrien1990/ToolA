"""Canonical normalizer for CA-supplied "comma list" parameters.

WHY this module exists (A34 / CASE-1809-B1): several tools declare a
parameter as `str`, document it as "comma list", then call
`value.split(",")` on it directly. A CA that sends the same value as a JSON
array -- the natural shape for a list, and the shape every JSON transport
encourages -- did not get a structured refusal and did not get the right
answer either: the tool died on a raw AttributeError from deep inside its
body. Measured on 2026-09-18 before this fix, against a live daemon:

    rule_drift_check(checks=["tool_names"])  -> TOOL_ERROR
                                                "'list' object has no attribute 'split'"
    import_graph(file_pattern=["*.py"])      -> TOOL_ERROR (same)
    ast_query(file_pattern=["*.py"])         -> TOOL_ERROR (same)

while the csv-string form of each answered ok:true. Two spellings of one
value must not disagree, and a shape a caller can plausibly send must never
surface as an internal traceback (7-RULE #7 fail loud: the failure was loud
but named the wrong thing -- it accused `list` of missing `split` instead of
telling the caller which field it disliked).

The same coercion already existed twice in this tree before A34
(`orchestrator_server._r101_parse_fields`, `orchestrator.graph.tools._as_path_list`).
Those two keep their own copies -- neither is broken, and rewriting a working
normalizer buys nothing -- but every NEW call site shares this one so a third
divergent copy cannot appear.

Placement: stdlib-only, under `orchestrator.core`, so `tools_v15.py` may
import it at module level without breaking its import-topology rule
(tools_v15 must never import code_tools / orchestrator_server). Measured:
`orchestrator/__init__.py` is a docstring and `orchestrator/core/__init__.py`
is empty, so this import pulls in nothing else.
"""

from __future__ import annotations

import json

__all__ = ["csv_param"]


def _clean(items, lower: bool, keep_empty: bool) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if lower:
            text = text.lower()
        if text or keep_empty:
            out.append(text)
    return out


def csv_param(value, *, lower: bool = False, keep_empty: bool = False) -> list[str]:
    """Coerce a "comma list" parameter into list[str], whatever shape it arrived in.

    Accepted shapes, all of which a CA can and does send:
      None / ""            -> []
      "a,b"                -> ["a", "b"]        (surrounding whitespace stripped)
      '["a","b"]'          -> ["a", "b"]        (JSON array that arrived as a string)
      ["a", "b"] / tuple   -> ["a", "b"]        (order preserved)
      {"b", "a"} / frozenset -> ["a", "b"]      (SORTED: a set has no order, and a
                                                 caller-visible result must be stable)
      7 / Path(...)        -> ["7"] / ["..."]   (lone scalar = a one-item list)

    `lower` lowercases each item (for case-insensitive selectors like
    rule_drift_check's `checks`). `keep_empty` retains empty items instead of
    dropping them -- off by default because every current caller treats an
    empty item as noise.

    Never raises for these shapes. A caller that wants to REJECT a shape must
    test for it explicitly and return its own structured error; relying on an
    AttributeError to do that job is what A34 fixed.
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                return _clean(parsed, lower, keep_empty)
        return _clean(text.split(","), lower, keep_empty)
    if isinstance(value, (set, frozenset)):
        return sorted(_clean(value, lower, keep_empty))
    if isinstance(value, (list, tuple)):
        return _clean(value, lower, keep_empty)
    return _clean([value], lower, keep_empty)
