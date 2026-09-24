"""CASE-1809-E: an unknown fields= key must name the keys that DO exist.

A23-P2 already told the caller WHICH keys were ignored (fields_unknown) and
that the result is not empty because of them.  It never said what to type
instead, so the only move left was to repeat the SAME call with fields=
omitted -- the full payload the projection exists to avoid.  Measured
2026-09-18 against the live daemon on read_file_range: `content` instead of
`result`, and `total_lines.deeper` aimed at an int, were both answered with
nothing but \"check spelling\".

Every assertion here is DERIVED from the payload the test itself builds -- no
hand-written key list, so a rename in the producer cannot leave a stale table
passing.  Two controls guard the pair of ways this could go wrong:
test_a_clean_projection_adds_nothing keeps the diagnostics from riding along
on every filtered response (ISS-14: a marker that is always present carries no
information), and test_the_cap_is_real keeps them from becoming the unbounded
key dump that fields= exists to buy off.
"""
from __future__ import annotations

import orchestrator_server as srv


def _payload() -> dict:
    """The measured shape of a read_file_range response, trimmed."""
    return {
        "ok": True,
        "result": "1: x\n",
        "total_lines": 178,
        "content_hash": "a" * 64,
        "selection": {"scope": "range", "start_line": 1, "end_line": 60},
    }


def test_a_clean_projection_adds_nothing():
    """Restraint control: no unknown key -> no diagnostics at all."""
    out = srv._r101_filter(_payload(), {"ok", "total_lines"})
    assert out["ok"] is True
    assert out["total_lines"] == 178
    for noise in ("fields_unknown", "fields_unknown_hint", "fields_available"):
        assert noise not in out, (
            "%s rode along on a projection that honoured every key" % noise)


def test_a_plain_typo_gets_the_top_level_names():
    payload = _payload()
    out = srv._r101_filter(payload, {"ok", "content"})
    assert out["fields_unknown"] == ["content"]
    # Derived from the payload, not from a hand-written list.
    assert out["fields_available"][srv._R101_TOP_NAMESPACE] == sorted(payload)
    assert "result" in out["fields_available"][srv._R101_TOP_NAMESPACE], (
        "the key the caller actually wanted must appear in the answer, or the "
        "retry is still a guess")


def test_a_dotted_head_that_is_a_dict_gets_its_subkeys():
    payload = _payload()
    out = srv._r101_filter(payload, {"ok", "selection.junk_sub"})
    assert out["fields_unknown"] == ["selection.junk_sub"]
    avail = out["fields_available"]
    assert avail["selection"] == sorted(payload["selection"])
    assert srv._R101_TOP_NAMESPACE not in avail, (
        "the head resolved, so the mistake is NOT at the top level; naming the "
        "top level here would aim the retry at the wrong namespace")


def test_a_dotted_head_that_is_a_scalar_reports_its_type():
    payload = _payload()
    out = srv._r101_filter(payload, {"ok", "total_lines.deeper"})
    reported = out["fields_available"]["total_lines"]
    assert len(reported) == 1
    # Derived from the value's own type: an int head is a different mistake
    # from a misspelled key, and the caller has to be able to tell them apart.
    assert type(payload["total_lines"]).__name__ in reported[0]
    assert "cannot descend" in reported[0]


def test_a_dotted_head_that_does_not_exist_is_a_top_level_typo():
    payload = _payload()
    out = srv._r101_filter(payload, {"ok", "selektion.scope"})
    avail = out["fields_available"]
    assert avail[srv._R101_TOP_NAMESPACE] == sorted(payload)
    assert "selektion" not in avail, (
        "a namespace keyed by the typo itself teaches nothing")


def test_the_cap_is_real():
    """Positive control for the bound.

    An unbounded key dump is the very cost fields= buys off, so the
    diagnostics must never become one.  The expected length is computed from
    _R101_AVAILABLE_CAP, so raising the cap cannot silently pass this.
    """
    cap = srv._R101_AVAILABLE_CAP
    wide = {"k%03d" % i: i for i in range(cap * 3)}
    wide["ok"] = True
    out = srv._r101_filter(wide, {"ok", "nope"})
    names = out["fields_available"][srv._R101_TOP_NAMESPACE]
    assert len(names) == cap + 1, "the cap plus exactly one overflow marker"
    assert names[-1] == "...+%d more" % (len(wide) - cap), (
        "truncation has to be disclosed with the real remainder")


def test_the_hint_still_says_the_result_is_not_empty():
    """The ISS-14 pin: enriching the hint must not drop what it already earned."""
    out = srv._r101_filter(_payload(), {"ok", "nope"})
    hint = out["fields_unknown_hint"]
    assert "NOT empty" in hint
    assert "fields_available" in hint, (
        "the hint has to name the new key, or a caller reads straight past it")
    # 2026-09-24: what the miss MEANS, not how to call fields= -- that is the
    # parameter's schema description, fetched once. Spelled out here it was ~390
    # chars on every miss, in the one payload built to be small.
    assert len(hint) <= 200, len(hint)


def test_the_top_namespace_label_is_never_an_empty_key():
    """F1 2026-09-22: a "" key is legal JSON that no PowerShell CA can read.

    PowerShell 5.1 `ConvertFrom-Json` rejects the ENTIRE document with `Cannot
    process argument because the value of argument "name" is not valid` when any
    object carries an empty-string key, because a PSObject property may not have
    an empty name.  So every fields_unknown response -- the one payload built to
    spare the caller a second full call -- was unparseable for a PowerShell CA
    and forced exactly the re-call it exists to prevent.  Measured three times in
    one session before the cause was found.

    The other tests assert THROUGH _R101_TOP_NAMESPACE, so they would keep
    passing if the label went back to "".  The label itself is pinned here.
    """
    assert srv._R101_TOP_NAMESPACE, "an empty namespace key breaks PowerShell CAs"

    out = srv._r101_filter(_payload(), {"ok", "content"})
    assert all(out["fields_available"]), "no namespace key may be empty"

    # Collision-proof by construction: a response key is an identifier, so a
    # label that could itself be one would be ambiguous with a real field.
    assert not srv._R101_TOP_NAMESPACE.isidentifier()

    # And the hint has to spell the label, or the caller cannot map the
    # namespace it just received back onto anything.
    assert srv._R101_TOP_NAMESPACE in out["fields_unknown_hint"]
