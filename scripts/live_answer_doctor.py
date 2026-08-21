#!/usr/bin/env python
"""live_answer_doctor.py -- do the tools' ANSWERS still mean what they say?

Why this exists
---------------
The JS-repo audit of 2026-08-21 found six defects (B011-B016). Five shared one
shape: a tool that could not do something answered as though it had. None of them
errored; every one returned `ok`. The suite was green through all of them,
because a fresh `tmp_path` fixture is the one environment in which every one of
those six answers is honest:

  * B011 -- a full index never deleted the row of a vanished file. No fixture
    file ever vanishes.
  * B012 -- an unrecognised `lang` answered `ok:true, lang:"none"`, nothing run.
    Fixtures spell `lang` correctly.
  * B014 -- `file_filter` was resolved and dropped. A fixture passes no glob.
  * B015 -- the AST engine answered under Ruff's description. A fixture repo is
    Python, so the engine named is the engine that ran.
  * B016 -- an extractor fix never reached an already-indexed repo, because
    `stale_files` keys on content hashes and the bytes had not changed. A fixture
    repo has no prior rows to be stale.

So this script is deliberately NOT a unit test. It asks a **live daemon** about a
**real, already-indexed, non-Python** repo -- the three conditions under which
those answers stop being honest -- and checks each answer against something
independently known. `tests/` pins the mechanisms; this pins the claim that the
mechanisms are reaching production data.

The rule it enforces, in one line:

    "the tests pass" and "the fix reaches production data" are different claims.
    Anything cached, hashed, or memoised sits between them.

Read-only. It indexes (which writes the graph DB, by design) and never edits or
deletes a source file.

Run
---
    venv312\\Scripts\\python.exe scripts\\live_answer_doctor.py --project-path <repo>
    venv312\\Scripts\\python.exe scripts\\live_answer_doctor.py --project-path <repo> --ci
    venv312\\Scripts\\python.exe scripts\\live_answer_doctor.py --project-path <repo> --output json

`--ci` exits 1 on any FAIL (a wrong answer). WARN never blocks: a WARN means the
check could not run here, not that the answer was wrong -- e.g. the repo is
Python, so the engine-honesty check has nothing to catch.

Exit codes: 0 clean (or WARN only), 1 at least one FAIL, 2 could not reach the
daemon / bad arguments.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.environ.get("ORCHESTRATOR_BASE_URL", "http://localhost:8083")
TOKEN_FILE = ROOT / ".orchestrator_cache" / "mutation_token.txt"

# The one string that must never be current again: the extractor that shipped
# BEFORE the JS import shape changed (B013). A repo indexed under it holds
# path-shaped import keys. Any LATER string passes -- this is a guard against
# the specific omission, not against future bumps.
PRE_B013_EXTRACTOR = "ast-p0-v8-all-is-not-a-reference"

# Categories that only Ruff produces. On a JS/TS repo they are empty *by
# construction*, not because the repo is clean (B015, and the gap Anh deferred).
RUFF_ONLY_CATEGORIES = ("bug_risk", "perf", "complexity", "code_smell",
                        "tech_debt", "security")


class Unreachable(RuntimeError):
    """The daemon did not answer. Never guess past this -- stop and report."""


def _token() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def call(tool: str, payload: dict, *, timeout: int = 300) -> dict:
    """POST /api/call/<tool> and unwrap the envelope.

    The HTTP layer wraps most tool payloads in a `result` key; a few answer with
    their fields at the top level. Reading the wrong level yields all-`None`,
    which looks exactly like a broken fix -- so unwrap defensively, always.
    """
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/api/call/{tool}", data=body,
        headers={"Content-Type": "application/json",
                 "X-Orchestrator-Token": _token()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # B019 follow-up: a 400 is a STRUCTURED REFUSAL, not an unreachable
        # daemon. UNSUPPORTED_FIELD / SCHEMA_VIOLATION / MISSING_REQUIRED_FIELD
        # all arrive as 400 with a real JSON body, and treating them as
        # Unreachable aborted the entire doctor on the first check that probed a
        # refusal -- turning "this tool refuses a field it advertises" into
        # "cannot reach [A]", which is precisely the misdiagnosis this whole
        # script exists to prevent. 404 stays fatal: it means the tool name is
        # wrong, so no check written against it can mean anything.
        if exc.code == 404:
            raise Unreachable("%s -> HTTP 404 %s (tool name is wrong)"
                              % (tool, exc.reason)) from exc
        try:
            raw = json.loads(exc.read().decode("utf-8"))
        except Exception:
            raise Unreachable("%s -> HTTP %s %s"
                              % (tool, exc.code, exc.reason)) from exc
        return raw.get("result", raw)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise Unreachable("%s -> %s" % (tool, exc)) from exc
    return raw.get("result", raw)


def fetch_schema(tool: str, *, timeout: int = 60) -> dict:
    """GET /api/tools/<tool>/schema -- the contract the dispatcher enforces.

    B019's third half: `allowed_params` is built from this schema, not from the
    Python signature, so this route is the only honest answer to "will the
    daemon accept this field". Never infer it from source on disk.
    """
    req = urllib.request.Request(f"{BASE_URL}/api/tools/{tool}/schema")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise Unreachable("schema %s -> HTTP %s %s"
                          % (tool, exc.code, exc.reason)) from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise Unreachable("schema %s -> %s" % (tool, exc)) from exc


class Report:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, verdict: str, check: str, detail: str, bug: str = "") -> None:
        self.rows.append({"verdict": verdict, "check": check,
                          "detail": detail, "bug": bug})

    def ok(self, check, detail, bug=""): self.add("PASS", check, detail, bug)
    def bad(self, check, detail, bug=""): self.add("FAIL", check, detail, bug)
    def warn(self, check, detail, bug=""): self.add("WARN", check, detail, bug)

    @property
    def failures(self) -> list[dict]:
        return [r for r in self.rows if r["verdict"] == "FAIL"]

    def counts(self) -> dict:
        return {v: sum(1 for r in self.rows if r["verdict"] == v)
                for v in ("PASS", "FAIL", "WARN")}


# --------------------------------------------------------------------------
# Checks. Each one asks a question whose answer is knowable independently of
# the tool being asked -- that is the whole method. A check that can only be
# verified by the tool it is checking proves nothing.
# --------------------------------------------------------------------------

def _probe_file(repo: str) -> str | None:
    """Pick a small text file *inside* `repo` to aim validate_file at.

    Scope matters more than content here: the file only has to exist inside the
    project, because what is being tested is how the tool answers about `lang`,
    not whether that particular file is valid.
    """
    for name in ("package.json", "README.md", "index.js", "app.js"):
        cand = os.path.join(repo, name)
        if os.path.isfile(cand):
            return cand
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames
                       if d not in {".git", "node_modules", "venv312",
                                    ".orchestrator_cache", "__pycache__"}]
        for fn in filenames:
            if fn.endswith((".json", ".js", ".md", ".py", ".ts")):
                return os.path.join(dirpath, fn)
    return None


def check_extractor_version_is_current(rep: Report, status: dict) -> None:
    """B016 -- the version the rows carry is what makes an extractor fix ship."""
    ev = status.get("extractor_version")
    if not ev:
        rep.warn("extractor_version reported", "graph_status carried no "
                 "extractor_version; cannot tell whether rows are stale", "B016")
        return
    if ev == PRE_B013_EXTRACTOR:
        rep.bad("extractor_version is post-B013",
                "still %r -- every already-indexed repo keeps path-shaped "
                "import keys and unrecorded require() calls, so B013 does not "
                "ship" % ev, "B016")
    else:
        rep.ok("extractor_version is post-B013", ev, "B016")


def check_index_converges(rep: Report, repo: str) -> dict:
    """B011 -- two counters over two populations must agree, and must converge.

    The index walks *disk*; `stale_files` counts the *files table*. Before B011 a
    full index never deleted the row of a vanished file, so the count could not
    reach zero no matter how often you reindexed. Reindexing twice is the check:
    a number that never moves is the tell.
    """
    first = call("cbm_index", {"project_path": repo, "scope": "full"})
    if first.get("status") not in (None, "completed"):
        rep.bad("full index completes", "status=%r error=%r"
                % (first.get("status"), first.get("error")), "B011")
        return first

    status = call("cbm_graph_status", {"project_path": repo})
    stale = status.get("stale_files")
    if stale in (None, 0):
        rep.ok("index converges", "stale_files=%s after a full index" % stale,
               "B011")
    else:
        second = call("cbm_index", {"project_path": repo, "scope": "full"})
        again = call("cbm_graph_status", {"project_path": repo}).get("stale_files")
        if again == stale:
            rep.bad("index converges",
                    "stale_files stuck at %s across two full indexes "
                    "(indexed=%s unchanged=%s pruned=%s) -- a count a reindex "
                    "cannot clear"
                    % (stale, second.get("files_indexed"),
                       second.get("files_unchanged"), second.get("files_pruned")),
                    "B011")
        else:
            rep.warn("index converges",
                     "stale_files %s -> %s; moving, so not the B011 shape"
                     % (stale, again), "B011")

    fresh, stale_now = status.get("freshness"), status.get("stale_files")
    if fresh == "fresh" and stale_now not in (None, 0):
        rep.bad("freshness agrees with stale_files",
                "freshness='fresh' while stale_files=%s -- one field of the "
                "payload contradicts another" % stale_now, "B011")
    else:
        rep.ok("freshness agrees with stale_files",
               "freshness=%r stale_files=%s" % (fresh, stale_now), "B011")
    return first


def check_dependency_edges_exist(rep: Report, repo: str, indexed: dict,
                                 language: str) -> None:
    """B013/B016 -- a repo with callables and zero edges is the reported symptom."""
    edges = indexed.get("dependency_edges")
    # `cbm_template_query` answers `results` + `count`, NOT `rows`. Reading a
    # key the payload does not have yields 0 and reads as "the query is broken"
    # -- the same class of mistake as reading past the `result` envelope.
    q = call("cbm_template_query",
             {"project_path": repo, "template": "module_deps"})
    n_rows = q.get("count")
    if n_rows is None:
        n_rows = len(q.get("results") or [])

    if edges == 0 and language == "javascript":
        rep.bad("dependency edges are built",
                "dependency_edges=0 on a JS/TS repo -- module_deps / imports / "
                "imported_by and every cycle query answer empty", "B013")
    elif edges in (None, 0):
        rep.warn("dependency edges are built",
                 "dependency_edges=%r (language=%s); could be a genuinely "
                 "flat repo" % (edges, language), "B013")
    else:
        rep.ok("dependency edges are built",
               "dependency_edges=%s, module_deps rows=%s" % (edges, n_rows),
               "B013")

    if edges and n_rows == 0:
        rep.bad("edges are queryable",
                "%s edges in the graph but module_deps returns 0 rows -- the "
                "import keys cannot match the module keys the indexer builds "
                "(the path-shaped-key half of B013)" % edges, "B013")


def check_file_filter_is_not_inert(rep: Report, repo: str) -> None:
    """B014 -- the decisive measurement is a BOGUS glob, not a narrow one.

    A narrow glob returning many results is ambiguous: it may be broad. A glob
    that can match nothing returning the unfiltered count is proof the argument
    is discarded. This is the single most reusable trick from the audit: pass a
    deliberately impossible argument and see whether the answer notices.
    """
    unfiltered = call("cbm_dead_code", {"project_path": repo, "limit": 5})
    base = unfiltered.get("count")

    bogus = call("cbm_dead_code",
                 {"project_path": repo, "limit": 5,
                  "file_filter": "no/such/dir/**/*.nope"})
    got, matched = bogus.get("count"), bogus.get("files_matched")

    if base is None or got is None:
        rep.warn("file_filter is honoured",
                 "cbm_dead_code did not report `count` (enabled=%r error=%r)"
                 % (unfiltered.get("enabled"), unfiltered.get("error")), "B014")
        return

    if got == base and base > 0:
        rep.bad("file_filter is honoured",
                "an impossible glob returned the same %s candidates as no "
                "filter -- the argument is inert, not broad" % base, "B014")
    elif got == 0:
        rep.ok("file_filter is honoured",
               "impossible glob -> count=0 files_matched=%s (unfiltered=%s)"
               % (matched, base), "B014")
    else:
        rep.warn("file_filter is honoured",
                 "impossible glob -> count=%s (unfiltered=%s); expected 0"
                 % (got, base), "B014")

    if bogus.get("file_filter_applied") is not True:
        rep.bad("file_filter is echoed",
                "file_filter_applied=%r though a filter was passed -- a glob "
                "that matched nothing is indistinguishable from a full scan"
                % bogus.get("file_filter_applied"), "B014")
    else:
        rep.ok("file_filter is echoed",
               "file_filter_applied=True files_matched=%s" % matched, "B014")


def check_engine_says_what_ran(rep: Report, repo: str, language: str) -> None:
    """B015 -- falling back is legitimate; not saying so is the defect."""
    d = call("cbm_dead_code", {"project_path": repo, "limit": 5})
    engine, reason = d.get("engine"), d.get("engine_reason")
    detected = d.get("language_detected")

    if not engine:
        rep.bad("the answer names its engine",
                "no `engine` field -- the caller cannot tell which analysis "
                "produced this, and the two have different semantics", "B015")
    elif not reason:
        rep.bad("the answer names its engine",
                "engine=%r but no `engine_reason` -- a fallback that does not "
                "say why it fell back" % engine, "B015")
    else:
        rep.ok("the answer names its engine",
               "engine=%s (%s)" % (engine, reason), "B015")

    if detected in (None, "other"):
        rep.bad("language_detected is specific",
                "language_detected=%r -- the detector counted the files and "
                "then threw away what it counted" % detected, "B015")
    else:
        rep.ok("language_detected is specific", str(detected), "B015")

    # The deferred gap, surfaced rather than fixed: an empty Ruff-only category
    # on a JS repo is not good news. Anh: "1 số tool python sẽ không bắt được,
    # nâng cấp sau."
    if language != "python" and engine and "ast" in str(engine):
        rep.warn("Ruff-only categories are empty by construction",
                 "engine=%s on a %s repo: %s are Ruff-only and therefore "
                 "empty here -- NOT evidence the repo is clean"
                 % (engine, language, ", ".join(RUFF_ONLY_CATEGORIES)), "B015")


def check_validate_file_fails_loud(rep: Report, repo: str) -> None:
    """B012 -- `ok:true` must never be ambiguous about whether anything ran.

    Two probes, and the second is the interesting one: a misspelling of a
    language the tool DOES implement. `JSON` and `javascript` both name
    something real under another spelling, which is exactly why they used to
    pass silently.
    """
    # The probe file must live INSIDE `repo`. A path outside the project scope
    # is refused on scope grounds before `lang` is ever considered, so an
    # outside-the-repo probe silently measures nothing. Measured: the same call
    # answers UNSUPPORTED_LANG for a file inside the repo and VALIDATION_FAILED
    # for one outside it.
    probe = _probe_file(repo)
    if probe is None:
        rep.warn("an unknown lang is refused",
                 "found no small text file inside the repo to probe with",
                 "B012")
        return

    nonsense = call("validate_file", {"project_path": repo,
                                      "file_path": probe, "lang": "klingon"})
    if nonsense.get("error_name") == "UNSUPPORTED_LANG":
        langs = nonsense.get("supported_langs")
        rep.ok("an unknown lang is refused",
               "UNSUPPORTED_LANG, supported_langs=%s"
               % (len(langs) if langs else "absent"), "B012")
        if not langs:
            rep.bad("the refusal is learnable",
                    "UNSUPPORTED_LANG carried no supported_langs -- the "
                    "vocabulary is unlearnable except from source", "B012")
    elif nonsense.get("ok") is True:
        rep.bad("an unknown lang is refused",
                "lang='klingon' answered ok:true (lang=%r checked=%r) -- a "
                "broken file passes under any misspelling"
                % (nonsense.get("lang"), nonsense.get("checked")), "B012")
    else:
        rep.warn("an unknown lang is refused",
                 "refused as %r rather than UNSUPPORTED_LANG"
                 % nonsense.get("error_name"), "B012")

    alias = call("validate_file", {"project_path": repo,
                                   "file_path": probe, "lang": "JSON"})
    if alias.get("ok") is True and alias.get("checked") is False:
        rep.bad("a supported lang misspelt does not pass silently",
                "lang='JSON' answered ok:true with checked:false -- nothing "
                "ran, and the file was reported fine", "B012")
    else:
        rep.ok("a supported lang misspelt does not pass silently",
               "lang='JSON' -> ok=%r checked=%r error=%r"
               % (alias.get("ok"), alias.get("checked"),
                  alias.get("error_name")), "B012")


def check_success_answers_ok(rep: Report, repo: str) -> None:
    """B018 -- a field every failure path sets, the success path owes too.

    `search_code` answered `ok:False` when it failed and answered *nothing*
    when it succeeded, so the ordinary caller guard `if not resp.get("ok")`
    read a search with real hits as a failure -- a missing key being falsy in
    exactly the same way False is. Nothing errored, no gate denied, and the
    results were right there in the payload; the caller simply threw them away.

    This is the one shape a fixture can never catch, because a unit test asserts
    on the value of a field it already knows the name of. Only a caller that
    guards the way callers actually guard sees it. So the check is the guard
    itself, run against the live answer.
    """
    hit = call("search_code", {"project_path": repo, "query": "function",
                               "max_results": 3})
    if "ok" not in hit:
        rep.bad("a successful search says ok",
                "search_code answered count=%s with no `ok` field, so "
                "`if not resp.get(\"ok\")` reads a successful search as a "
                "failure" % hit.get("count"), "B018")
    elif hit.get("ok") is not True:
        rep.bad("a successful search says ok",
                "search_code answered ok=%r on a search that ran (count=%s)"
                % (hit.get("ok"), hit.get("count")), "B018")
    else:
        rep.ok("a successful search says ok",
               "ok=True count=%s" % hit.get("count"), "B018")

    # And `ok` must still be able to be False, or the field carries nothing.
    # An empty query is the cheapest failure that does not touch the index.
    bad = call("search_code", {"project_path": repo, "query": ""})
    if bad.get("ok") is False and bad.get("error_name"):
        rep.ok("a failed search still says ok:false",
               "%s" % bad.get("error_name"), "B018")
    else:
        rep.bad("a failed search still says ok:false",
                "an empty query answered ok=%r error_name=%r -- if every "
                "answer is ok, the field stops carrying information"
                % (bad.get("ok"), bad.get("error_name")), "B018")


def check_advertised_fields_are_accepted(rep: Report, repo: str) -> None:
    """B019 -- a refusal must not advise a field the tool refuses.

    `regex_search` answered REGEX_INPUT_BUDGET_EXCEEDED with
    `ca_next_action: narrow_path_or_reduce_regex_input`, and then answered
    UNSUPPORTED_FIELD when the caller passed `path`. Advice in a circle.

    The reason this check has to be LIVE is the whole lesson of B019's third
    half: the dispatcher validates against the registry, not the Python
    signature (orchestrator_server.py, "S4: registry properties are the public
    contract"). The signature was fixed, the suite was green, py_compile was
    clean -- and the live answer was unchanged, because the registry entry still
    had no `path`. A unit test that calls the function directly cannot see that
    gap. Only the HTTP call can.

    Generalised past the one tool: every field a tool advertises as the way
    forward must appear in the schema that same daemon serves.
    """
    schema = fetch_schema("regex_search")
    props = set((((schema.get("tool") or {}).get("function") or {})
                 .get("parameters") or {}).get("properties") or {})
    if not props:
        rep.warn("advertised narrowing fields are accepted",
                 "could not read regex_search's live schema", "B019")
        return

    missing = [f for f in ("path", "file_pattern") if f not in props]
    if missing:
        rep.bad("advertised narrowing fields are accepted",
                "regex_search's budget error tells callers to narrow, but its "
                "live schema does not accept %s -- the dispatcher builds "
                "allowed_params from this schema, so the advice is refused"
                % ", ".join(missing), "B019")
    else:
        rep.ok("advertised narrowing fields are accepted",
               "path and file_pattern both in the live schema", "B019")

    # And the field must be honoured, not merely tolerated: a scoped call has to
    # answer ok. Tolerating an argument that changes nothing is B014's shape.
    scoped = call("regex_search", {"project_path": repo, "path": repo,
                                   "pattern": "function", "max_results": 3})
    if scoped.get("ok") is True:
        rep.ok("a scoped regex_search runs",
               "ok=True count=%s" % scoped.get("count"), "B019")
    else:
        rep.bad("a scoped regex_search runs",
                "regex_search with path= answered ok=%r error_name=%r"
                % (scoped.get("ok"), scoped.get("error_name")), "B019")


def render_text(rep: Report, repo: str, meta: dict) -> str:
    out = ["[A] live-answer doctor",
           "  repo:       %s" % repo,
           "  daemon:     %s  gen=%s" % (BASE_URL, meta.get("generation")),
           "  language:   %s" % meta.get("language"),
           "  extractor:  %s" % meta.get("extractor_version"),
           ""]
    width = max((len(r["check"]) for r in rep.rows), default=10)
    for r in rep.rows:
        mark = {"PASS": "ok  ", "FAIL": "FAIL", "WARN": "warn"}[r["verdict"]]
        tag = (" [%s]" % r["bug"]) if r["bug"] else ""
        out.append("  %s %-*s %s%s" % (mark, width, r["check"], r["detail"], tag))
    c = rep.counts()
    out += ["", "  %s pass, %s fail, %s warn" % (c["PASS"], c["FAIL"], c["WARN"])]
    if rep.failures:
        out.append("  A FAIL means a tool answered as though it had done "
                   "something it did not do. See KNOWN_BUGS.md for the bug id.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Ask a live [A] whether its answers still mean what they say.")
    ap.add_argument("--project-path", required=True,
                    help="A real, already-indexed repo. A non-Python one "
                         "exercises the most checks.")
    ap.add_argument("--ci", action="store_true",
                    help="Exit 1 on any FAIL (WARN never blocks).")
    ap.add_argument("--output", choices=("text", "json"), default="text")
    args = ap.parse_args(argv)

    repo = os.path.abspath(args.project_path)
    if not os.path.isdir(repo):
        print("not a directory: %s" % repo, file=sys.stderr)
        return 2

    rep = Report()
    try:
        health = json.loads(urllib.request.urlopen(
            BASE_URL + "/health", timeout=30).read().decode("utf-8"))
        status = call("cbm_graph_status", {"project_path": repo})
        if status.get("enabled") is False:
            print("graph is disabled for this repo (CBM_GRAPH_ENABLED); "
                  "nothing to measure", file=sys.stderr)
            return 2

        check_extractor_version_is_current(rep, status)
        indexed = check_index_converges(rep, repo)

        dead = call("cbm_dead_code", {"project_path": repo, "limit": 5})
        language = dead.get("language_detected") or "unknown"

        check_dependency_edges_exist(rep, repo, indexed, language)
        check_file_filter_is_not_inert(rep, repo)
        check_engine_says_what_ran(rep, repo, language)
        check_validate_file_fails_loud(rep, repo)
        check_success_answers_ok(rep, repo)
        check_advertised_fields_are_accepted(rep, repo)
    except Unreachable as exc:
        print("cannot reach [A]: %s\nSTOP and report -- do not retry and do "
              "not fall back to native work." % exc, file=sys.stderr)
        return 2

    meta = {"generation": health.get("generation"),
            "language": language,
            "extractor_version": status.get("extractor_version")}
    if args.output == "json":
        print(json.dumps({"repo": repo, "meta": meta, "counts": rep.counts(),
                          "checks": rep.rows}, indent=2))
    else:
        print(render_text(rep, repo, meta))

    return 1 if (args.ci and rep.failures) else 0


if __name__ == "__main__":
    sys.exit(main())
