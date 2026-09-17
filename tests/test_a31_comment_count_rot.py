"""A31 Q6 - keep hard-coded population counts out of production comments.

A31's root cause: a number written in prose next to the code that computes it
always rots. Q6 deleted six such digits (registry sizes, skill-folder counts,
response sizes derived from them) and kept the mechanism sentence instead. This
file is the gate that stops the next one from landing.

Scope is the three root modules that carry the registry-population claims:
code_tools.py, orchestrator_server.py, tools_v15.py. The undated measurements
under orchestrator/graph/ ("688 modules", "225 entries", "3653 edges") are a
different class - they explain why an algorithm is correct, a date cannot be
invented for them after the fact, and they are deliberately left in place.

Two allowances, both mechanisms rather than line-number whitelists:
  * a dated claim is a record, not rot ("measured 2026-09-17: ... 94 tools"),
    matching the convention the tree already uses;
  * a cap/default is a contract the code enforces ("rolling 50 entries",
    "128 rules cap").
And two grammar exclusions: "R18 tools", "Core-10 tool", "v3 step 12 rule",
"Task 3 auto-chain rule 6" are names and references, not counts; and "saves 1
tool call" is a per-call rate, which has no population to drift away from.
The count itself may be a single digit and may carry one modifier word
("7 async functions") - both of those hid a rotten count from this gate once.

tokenize (not a line regex) does the reading, because implicit string
concatenation across source lines makes any per-line regex on a literal wrong.
"""

import io
import re
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

SCANNED_FILES = ("code_tools.py", "orchestrator_server.py", "tools_v15.py")

# A count of a live population: "257 tools", "35 skill folders", "221 aliases".
# The lookbehind rejects R19/Core-10/13462. - i.e. a digit that is part of a name.
# One optional modifier word is allowed between the digit and the noun, because
# "7 async functions" rots exactly the way "7 functions" does; and the count may be
# a single digit - "5 function patterns" was a real population that had gone stale.
COUNT_RE = re.compile(
    r"(?<![\w.-])(\d+)\s+(?:[a-z][a-z-]{2,11}\s+)?"
    r"(tools?|entries|entry|skill folders?|aliases|alias|rules?|functions?)\b",
    re.I,
)
DATED_RE = re.compile(r"\b(?:19|20)\d{2}\b")
CAP_RE = re.compile(
    r"\b(cap|capped|caps|max|maximum|limit|limited|default|rolling|up to|at most|first)\b",
    re.I,
)
# "v3 step 12 rule" / "audit Round 12" / "Task 3 auto-chain rule 6" - an ordinal or
# a name, not a size.
REF_RE = re.compile(r"\b(step|round|phase|item|section|line|chapter|task|v\d+)\s*$", re.I)
# "saves 1 tool call", "10 separate tool calls" - a per-call rate, not a population.
CALL_RE = re.compile(r"\s*calls?\b", re.I)


def _sweep(text_lines, tokens):
    """Return [(lineno, matched_text), ...] for unexplained population counts."""
    flagged = []
    for tok_type, tok_text, start, _end, _line in tokens:
        if tok_type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        for m in COUNT_RE.finditer(tok_text):
            if REF_RE.search(tok_text[max(0, m.start() - 14):m.start()]):
                continue
            if CALL_RE.match(tok_text[m.end():m.end() + 12]):
                continue
            lineno = start[0]
            # Context = the whole token (a docstring may date itself on any of
            # its lines) plus the two source lines above it (a comment block
            # carries its date on a neighbouring line).
            ctx = tok_text + "\n" + "\n".join(text_lines[max(0, lineno - 3):lineno - 1])
            if DATED_RE.search(ctx) or CAP_RE.search(ctx):
                continue
            flagged.append((lineno, m.group(0).strip()))
    return flagged


def _sweep_file(path):
    # split("\n"), never splitlines(): orchestrator_server.py contains a form
    # feed, which splitlines() treats as a line break and the tokenizer does not.
    lines = path.read_text(encoding="utf-8").split("\n")
    with io.open(path, "rb") as fh:
        tokens = list(tokenize.tokenize(fh.readline))
    return _sweep(lines, tokens)


def _sweep_source(source):
    lines = source.split("\n")
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    return _sweep(lines, tokens)


# ---------------------------------------------------------------- the gate

def test_no_undated_population_counts_in_production_comments():
    offenders = {}
    for name in SCANNED_FILES:
        hits = _sweep_file(REPO / name)
        if hits:
            offenders[name] = hits
    assert not offenders, (
        "Hard-coded population count(s) in comments - delete the digit and name "
        "the mechanism, or date the claim if it is a measurement: %r" % (offenders,)
    )


# ------------------------------------------- positive controls on the detector
# Without these, a regex that matched nothing would make the gate above pass
# vacuously - the exact failure mode of the 417-line test_all_tools.py.

def test_detector_flags_a_bare_population_count():
    hits = _sweep_source("# the protected set (60 tools) is the union\nx = 1\n")
    assert [h[1] for h in hits] == ["60 tools"]


def test_detector_flags_a_count_inside_a_docstring():
    src = 'def f():\n    """Returns pairs. 116 tools -> a small response."""\n    return 1\n'
    assert [h[1] for h in _sweep_source(src)] == ["116 tools"]


def test_detector_allows_a_dated_measurement():
    src = "# measured 2026-09-17: the same 94 tools appear in both registries\nx = 1\n"
    assert _sweep_source(src) == []


def test_detector_allows_a_date_on_the_line_above():
    src = "# D5 (2026-08-11): inventory snapshot below.\n# 213 tools at that time.\nx = 1\n"
    assert _sweep_source(src) == []


def test_detector_allows_a_declared_cap():
    src = '"""Cache keyed by sha256[:16]; rolling 50 entries."""\n'
    assert _sweep_source(src) == []


def test_detector_ignores_r_numbers_and_named_groups():
    src = (
        "# V15 R19 - alias contract for R16/R17/R18 tools\n"
        "# a Core-10 tool with no trigger.tool route\n"
        "# picked per v3 step 12 rule\n"
        "x = 1\n"
    )
    assert _sweep_source(src) == []


def test_detector_flags_a_count_with_a_modifier_word():
    """The false negative this gate shipped with: an adjective in between.

    code_tools.py:3 read "7 async functions" for a module that now defines far
    more than seven. The old pattern required the noun to touch the digit, so a
    single modifier word was enough to hide a rotten count from its own gate.
    """
    src = '"""Code Intelligence Tools - 7 async functions shared by HTTP API."""\n'
    assert [h[1] for h in _sweep_source(src)] == ["7 async functions"]


def test_detector_flags_a_single_digit_count():
    """The same miss, second cause: a one-digit population is still a count."""
    src = "# the router parses JS with 5 function patterns + source evidence\nx = 1\n"
    assert [h[1] for h in _sweep_source(src)] == ["5 function"]


def test_detector_ignores_a_per_call_rate():
    """"1 tool call" is a rate, not a population - nothing there can rot."""
    src = (
        '"""R38: expose the body so CA saves 1 tool call per status query."""\n'
        "# aggregates metrics so audits do not need 10 separate tool calls\n"
        "x = 1\n"
    )
    assert _sweep_source(src) == []


def test_detector_ignores_a_task_number_before_a_rule_name():
    src = "# Task 3 auto-chain rule 6: git_commit post-hook\nx = 1\n"
    assert _sweep_source(src) == []


# ------------------------------------------------- the six Q6 fixes, by phrase
# Pinned as phrases, not line numbers: the line will move, the claim will not
# come back by accident.

def test_the_q6_rot_phrases_are_gone():
    ct_src = (REPO / "code_tools.py").read_text(encoding="utf-8")
    os_src = (REPO / "orchestrator_server.py").read_text(encoding="utf-8")
    v15_src = (REPO / "tools_v15.py").read_text(encoding="utf-8")

    for needle in ("34 skill folders", "TOKEN_REQUIRED, 60 tools",
                   "process). 17 entries", "7 async functions"):
        assert needle not in ct_src, needle
    for needle in ("116 tools", "13 tools per audit Round 12"):
        assert needle not in os_src, needle
    for needle in ("× 23 entries", "5 function patterns"):
        assert needle not in v15_src, needle


def test_the_mechanism_sentences_that_replaced_them_are_present():
    ct_src = (REPO / "code_tools.py").read_text(encoding="utf-8")
    os_src = (REPO / "orchestrator_server.py").read_text(encoding="utf-8")
    v15_src = (REPO / "tools_v15.py").read_text(encoding="utf-8")

    assert "some skill folders are named rather" in ct_src
    assert "ask the daemon (GET /api/tools/names), never this docstring" in ct_src
    assert "magnitude smaller than the full /api/tools payload" in os_src
    assert "strip per-entry path metadata from every entry" in v15_src
    assert "the pattern count is the length of that list" in v15_src


def test_the_dated_records_were_not_swept_away_with_them():
    """A dated claim is a record. Deleting one would also be a regression."""
    ct_src = (REPO / "code_tools.py").read_text(encoding="utf-8")
    os_src = (REPO / "orchestrator_server.py").read_text(encoding="utf-8")
    assert "2026-09-17: frontmatter-only and whole-file both yield the same 94 tools" in ct_src
    assert 'hard-code "all 174 tools"' in os_src
