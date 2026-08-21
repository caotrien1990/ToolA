r"""B021: two payloads that described their own contents wrongly.

Both reported from live use on a mid-refactor tree, measured on the daemon at
generation cac7187da50d.

  git_status(project_path=<repo>)
    -> 126,770 bytes, TRUNCATED by the transport, 270 records
       and EVERY record carried its own content twice:
         {"record_type":"1",
          "raw":"1 .M N... 100644 100644 100644 592f3d11.. 592f3d11.. PROJECT_CONTEXT.md",
          "xy":".M","sub":"N...","path":"PROJECT_CONTEXT.md"}
       `raw` is the porcelain-v2 line the other four fields were PARSED OUT OF.
       Nothing in it is new information; it is 71% of the record's bytes.

  cbm_template_query(project_path=<repo>, template="module_deps", limit=12)
    -> count=12  truncated=True  total_count=0

       So the answer said, in one breath, "there are more than these 12" and
       "the population is 0". A caller that paginates on total_count stops at
       once; a caller that trusts truncated keeps going. The two fields
       contradicted each other, and one of them had to be lying.

These are the same defect wearing two costumes: a payload whose SELF-DESCRIPTION
is wrong. The B-series already fixed the git_status size problem once by capping
records and REPORTING the cap (records_total / records_truncated) -- the honest
shape. The duplication survived that fix because the cap made the payload
smaller without making any record smaller, and nobody had counted a record's
bytes against the information in it.

Pinned here, per issue:

  git_status
    1. no record carries `raw` alongside the fields parsed from it;
    2. every field that WAS parsed out of it is still there, for all five
       porcelain record types (1, 2, u, ?, !) -- dropping the duplicate must
       not drop the data;
    3. `state_hash` is UNCHANGED by the fix. It is a sha256 over
       {'branch','records'} and callers hold it as an optimistic lock across
       calls (git_stage / git_stale_check compare it), so a hash that shifted
       because the response got tidier would break every held lock. This is
       the bounding claim: the internal hash input keeps `raw`.

  module_deps
    4. total_count is the true pre-cap population, not 0;
    5. truncated and total_count agree -- truncated is true iff
       total_count > count;
    6. the templates that already reported a real total_count (orphan_symbols)
       still do, and a small repo answers total_count == count with
       truncated False. Bounding: "fix the 0" must not become "always guess".
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("GRAPH_ENABLED", "1")  # must precede the graph config import

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator import git_tools as g  # noqa: E402


# ─── issue 1: git_status described every file twice ──────────────────────────

def _repo(tmp_path: Path) -> Path:
    def run(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       stdin=subprocess.DEVNULL, text=True)
    run("init", "-q")
    run("config", "user.name", "Test")
    run("config", "user.email", "test@example.com")
    (tmp_path / "tracked.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "renamed_from.txt").write_text("r\n", encoding="utf-8")
    run("add", "tracked.txt", "renamed_from.txt")
    run("commit", "-qm", "init")
    return tmp_path


def _all_record_types(root: Path) -> None:
    """Produce a tree holding porcelain types 1, 2 and ? at once.

    Type `u` (unmerged) needs a conflicted merge and `!` needs --ignored, so
    those two are covered by the parser test below rather than a real repo --
    but 1/2/? are the three a normal dirty tree actually serves, and a fix that
    only handled type 1 would pass a single-type fixture.
    """
    def run(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       stdin=subprocess.DEVNULL, text=True)
    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")      # type 1
    run("mv", "renamed_from.txt", "renamed_to.txt")                       # type 2
    (root / "brand_new.txt").write_text("n\n", encoding="utf-8")          # type ?


def test_no_record_describes_itself_twice(tmp_path) -> None:
    """The reported symptom, as a property of every record rather than a sample.

    `raw` is the porcelain line; record_type/xy/sub/path/orig_path are its parts.
    Shipping both is 71% waste per record on the one unbounded part of the
    payload -- which is exactly why 270 records came to 126,770 bytes and got cut.
    """
    root = _repo(tmp_path)
    _all_record_types(root)

    payload = g.status(str(root))
    assert payload["records"], "fixture produced no records"

    duplicated = [r for r in payload["records"] if "raw" in r]
    assert duplicated == [], (
        "%d of %d records still carry `raw` next to the fields parsed out of it: %r"
        % (len(duplicated), len(payload["records"]), duplicated[:2]))


def test_dropping_the_duplicate_dropped_no_information(tmp_path) -> None:
    """Every field that was parsed OUT of `raw` must survive.

    The cheap wrong fix is to drop `raw` and lose `orig_path`, which only the
    rename record (type 2) has and which no other field carries. Asserted per
    record type, on the fields that type is supposed to have.
    """
    root = _repo(tmp_path)
    _all_record_types(root)

    records = g.status(str(root))["records"]
    by_path = {r.get("path"): r for r in records}

    assert "tracked.txt" in by_path, by_path.keys()
    modified = by_path["tracked.txt"]
    assert modified["record_type"] == "1"
    assert modified["xy"] and modified["sub"], modified

    renamed = by_path.get("renamed_to.txt")
    assert renamed is not None, ("the rename record is missing: %r" % (list(by_path),))
    assert renamed["record_type"] == "2"
    assert renamed["orig_path"] == "renamed_from.txt", (
        "orig_path is the ONLY place the old name lives -- losing it with `raw` "
        "would make a rename unreadable: %r" % (renamed,))

    untracked = by_path.get("brand_new.txt")
    assert untracked is not None, list(by_path)
    assert untracked["record_type"] == "?" and untracked["xy"] == "??", untracked


def test_the_parser_still_fills_every_field_for_every_record_type() -> None:
    """Types `u` and `!` too, driven at the parser instead of through a repo.

    An unmerged record needs a conflicted merge and an ignored record needs
    --ignored=matching; both are awkward to stage and easy to leave untested,
    which is how a fix that special-cased type 1 would ship. `_parse_records`
    is the seam, so the two hard types get the same assertion as the easy ones.
    """
    lines = [
        "1 .M N... 100644 100644 100644 aaaa bbbb one.txt",
        "2 R. N... 100644 100644 100644 aaaa bbbb R100 new.txt\told.txt",
        "u UU N... 100644 100644 100644 100644 aaaa bbbb cccc conflict.txt",
        "? untracked.txt",
        "! ignored.txt",
    ]
    records = g._parse_records(lines)
    assert [r["record_type"] for r in records] == ["1", "2", "u", "?", "!"]
    assert all("raw" not in r for r in records), records

    one, two, unmerged, untracked, ignored = records
    assert (one["xy"], one["sub"], one["path"]) == (".M", "N...", "one.txt")
    assert (two["xy"], two["path"], two["orig_path"]) == ("R.", "new.txt", "old.txt")
    assert (unmerged["xy"], unmerged["path"]) == ("UU", "conflict.txt")
    assert (untracked["xy"], untracked["path"]) == ("??", "untracked.txt")
    assert (ignored["xy"], ignored["path"]) == ("!!", "ignored.txt")


def test_the_payload_actually_got_smaller(tmp_path) -> None:
    """The point of the fix, measured rather than asserted in a comment.

    Reported: 270 records / 126,770 bytes = ~470 bytes per record. `raw` is the
    bulk of that. A fix that removed the key but somehow kept the size would
    have missed the reason the response was truncated.
    """
    root = _repo(tmp_path)
    for index in range(120):
        (root / ("f%03d.txt" % index)).write_text("x", encoding="utf-8")
    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")

    payload = g.status(str(root))
    blob = len(json.dumps(payload, ensure_ascii=False))
    per_record = blob / payload["records_total"]
    assert per_record < 120, (
        "%.0f bytes per record -- the duplicate is still in there somewhere"
        % per_record)


def test_state_hash_did_not_move(tmp_path) -> None:
    """The bounding claim, and the reason `raw` is dropped from the RESPONSE only.

    state_hash is a sha256 over {'branch','records'} and callers hold it BETWEEN
    calls: git_stage refuses on GIT_STATE_STALE and git_stale_check reports
    state_hash_mismatch by comparing it. Hashing the tidied records instead
    would shift every hash at once -- every held lock would read as a
    concurrent modification that never happened, which is a worse bug than the
    one being fixed. So the hash input keeps the full porcelain line.

    Pinned against a literal: a recomputed expectation would move with the code.
    """
    root = _repo(tmp_path)
    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")

    payload = g.status(str(root))
    # Independently recomputed from git's own output the way _porcelain does,
    # INCLUDING raw -- the definition the lock is built on.
    import hashlib
    porcelain = subprocess.run(
        ["git", "-C", str(root), "--literal-pathspecs", "status", "--porcelain=v2",
         "--branch", "--renames", "-z", "--untracked-files=all"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, text=True).stdout
    branch, body = {}, []
    for rec in porcelain.split("\x00"):
        if not rec:
            continue
        if rec.startswith("# "):
            key, _, value = rec[2:].partition(" ")
            branch[key] = value
            continue
        body.append(rec)
    records = g._parse_records(body)
    expected_input = {"branch": branch,
                      "records": [{**r, "raw": rec} for r, rec in zip(records, body)]}
    expected = hashlib.sha256(json.dumps(
        expected_input, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    assert payload["state_hash"] == expected, (
        "state_hash changed shape: every optimistic lock a caller is holding "
        "would now read as a concurrent modification")

    # And it is still a working lock, not a constant. The change has to be one
    # PORCELAIN can see: rewriting a file already reported `.M` leaves the
    # porcelain byte-identical, so a content-only edit legitimately leaves the
    # hash alone -- measured true before this fix too, and asserting otherwise
    # would be measuring git's own output rather than the lock.
    (root / "appeared.txt").write_text("new\n", encoding="utf-8")
    assert g.status(str(root))["state_hash"] != payload["state_hash"]


def test_stale_check_still_detects_drift_through_the_hash(tmp_path) -> None:
    """The lock end to end -- the behaviour the test above protects by value."""
    root = _repo(tmp_path)
    before = g.status(str(root))["state_hash"]
    # `stale` is a BOOL; the named causes live in `reasons`. Reading the bool as
    # a container is how a check looks green while asserting nothing -- `"x" not
    # in True` does not even raise on the happy path of a truthy list.
    matched = g.stale_check(str(root), expected_state_hash=before)
    assert matched["expected_state_changed"] is False, matched
    assert "state_hash_mismatch" not in matched["reasons"], matched

    (root / "drifted.txt").write_text("drift\n", encoding="utf-8")
    drifted = g.stale_check(str(root), expected_state_hash=before)
    assert drifted["expected_state_changed"] is True, drifted
    assert "state_hash_mismatch" in drifted["reasons"], drifted


# ─── issue 2: module_deps said count=12, truncated=True, total_count=0 ───────

def _graph_repo(tmp_path: Path, modules: int) -> str:
    """A repo whose files import each other, so imports_module edges exist."""
    (tmp_path / "util.py").write_text("def helper():\n    return 42\n",
                                      encoding="utf-8")
    for index in range(modules):
        (tmp_path / ("m%03d.py" % index)).write_text(
            "from util import helper\n\n\ndef use%03d():\n    return helper()\n" % index,
            encoding="utf-8")
    project = str(tmp_path).replace("\\", "/")
    from orchestrator.graph.indexer import incremental_index, index_project
    index_project(project, scope="full")
    incremental_index(project)
    return project


def test_module_deps_reports_the_real_population(tmp_path) -> None:
    """The reported contradiction, reproduced and then refused.

    total_count=0 next to truncated=True is not a small inconsistency: it is the
    two fields a paginating caller chooses between, disagreeing. Reading
    total_count says "nothing here"; reading truncated says "keep asking".
    """
    project = _graph_repo(tmp_path, modules=12)
    from orchestrator.graph.query import template_query

    capped = template_query(project, "module_deps", limit=4)
    assert capped["error"] is None, capped
    assert capped["count"] == 4, capped
    assert capped["truncated"] is True, capped
    assert capped["total_count"] >= 12, (
        "total_count=%r alongside count=%d truncated=True -- the payload is "
        "still contradicting itself" % (capped["total_count"], capped["count"]))


def test_truncated_and_total_count_cannot_disagree(tmp_path) -> None:
    """One invariant instead of two independently-set fields.

    This is the property that was actually violated, so it is asserted as a
    property: truncated is true exactly when there is more than was shown.
    Checked at a cap below, at, and above the population -- the boundary is
    where an off-by-one lives.
    """
    project = _graph_repo(tmp_path, modules=12)
    from orchestrator.graph.query import template_query

    for limit in (1, 4, 12, 13, 50):
        payload = template_query(project, "module_deps", limit=limit)
        assert payload["error"] is None, payload
        assert payload["total_count"] >= payload["count"], payload
        assert payload["truncated"] is (payload["total_count"] > payload["count"]), (
            "limit=%d: count=%d total_count=%d truncated=%r"
            % (limit, payload["count"], payload["total_count"], payload["truncated"]))


def test_an_uncapped_read_says_so(tmp_path) -> None:
    """Bounding: "report a total" must not become "always claim truncation".

    The lazy fix for total_count=0 is to set it to count, which makes the
    contradiction disappear by making truncated permanently false -- and then a
    genuinely capped read looks complete. Here the population fits under the
    cap, so the honest answer is total_count == count AND truncated False.
    """
    project = _graph_repo(tmp_path, modules=3)
    from orchestrator.graph.query import template_query

    payload = template_query(project, "module_deps", limit=50)
    assert payload["count"] >= 3, payload
    assert payload["total_count"] == payload["count"], payload
    assert payload["truncated"] is False, payload


def test_the_templates_that_already_counted_still_count(tmp_path) -> None:
    """orphan_symbols computed a real total_count before this fix (E3).

    It reaches the same `truncated`/`total_count` fields by a different branch,
    so a fix applied at the shared tail could quietly overwrite the value it had
    already computed correctly.
    """
    (tmp_path / "lonely.py").write_text(
        "def never_called_public():\n    return 1\n\n\n"
        "def also_never_called():\n    return 2\n\n\n"
        "def third_orphan():\n    return 3\n", encoding="utf-8")
    project = str(tmp_path).replace("\\", "/")
    from orchestrator.graph.indexer import index_project
    from orchestrator.graph.query import template_query
    index_project(project, scope="full")

    page = template_query(project, "orphan_symbols", limit=1)
    assert page["error"] is None, page
    assert page["count"] == 1, page
    assert page["total_count"] >= 3, (
        "orphan_symbols lost its own true population: %r" % (page,))
    assert page["truncated"] is True, page


def test_a_template_with_no_population_is_still_honest(tmp_path) -> None:
    """The empty case, which is where total_count=0 was a CORRECT answer.

    A repo with no imports has no module_deps rows, and 0 is then the truth. The
    fix has to distinguish "the population is zero" from "nobody counted", and
    the only way to see the difference is to assert the zero case too.
    """
    (tmp_path / "solo.py").write_text("def alone():\n    return 1\n",
                                      encoding="utf-8")
    project = str(tmp_path).replace("\\", "/")
    from orchestrator.graph.indexer import index_project
    from orchestrator.graph.query import template_query
    index_project(project, scope="full")

    payload = template_query(project, "module_deps", limit=50)
    assert payload["error"] is None, payload
    assert payload["count"] == 0, payload
    assert payload["total_count"] == 0, payload
    assert payload["truncated"] is False, payload


def test_the_total_counts_what_a_page_can_actually_serve(tmp_path) -> None:
    """The COUNT must share the LIMIT-ed SELECT's predicate -- DISTINCT included.

    module_deps selects DISTINCT (file, module) pairs. A file that imports the
    same module twice -- `from util import helper` and `import util` -- produces
    TWO imports_module edges for ONE pair; measured on this fixture: 2 raw edges,
    1 distinct pair. Counting the edges instead would report total_count=2 for a
    page that can only ever hold 1, so `truncated` would stay true on a COMPLETE
    read and a paginating caller would loop forever on the same last page.

    That is the reported bug pointing the other way, and the first thirteen tests
    could not see it: their fixture had one import per file, where the two counts
    happen to agree, so a COUNT over the raw edges passed all of them.
    """
    (tmp_path / "util.py").write_text(
        "def helper():\n    return 42\n", encoding="utf-8")
    (tmp_path / "dup.py").write_text(
        "from util import helper\nimport util\n\n\n"
        "def use():\n    return util.helper()\n", encoding="utf-8")
    project = str(tmp_path).replace("\\", "/")
    from orchestrator.graph.indexer import index_project
    from orchestrator.graph.query import template_query
    index_project(project, scope="full")

    payload = template_query(project, "module_deps", limit=50)
    assert payload["error"] is None, payload
    assert payload["count"] == 1, ("the DISTINCT select served %r"
                                   % (payload["results"],))
    assert payload["total_count"] == payload["count"], (
        "total_count=%d over count=%d on an UNCAPPED read -- the COUNT is "
        "counting raw edges that the DISTINCT select collapses"
        % (payload["total_count"], payload["count"]))
    assert payload["truncated"] is False, (
        "a complete read reported truncated=True: a paginating caller would ask "
        "again for a page that does not exist -- %r" % (payload,))


def test_entry_points_counts_the_set_it_actually_serves(tmp_path) -> None:
    """entry_points DROPS test runners from its view, so it must not count them.

    CAN-053 split test_* names and test files out of the default view (33 of 50
    of the old output were test runners). The population is therefore the
    PRODUCTION set: counting the pre-split list reports a total no page of this
    template will ever reach, and `truncated` never goes false.

    Measured on this fixture: 2 production entry points, 2 excluded test
    runners -- so counting the wrong list is the difference between total_count
    2 and 4, and between truncated False and True on a full read.
    """
    (tmp_path / "app.py").write_text(
        "def main():\n    return 1\n\n\ndef handle_request():\n    return 2\n",
        encoding="utf-8")
    (tmp_path / "test_suite.py").write_text(
        "def test_one():\n    return 1\n\n\ndef test_two():\n    return 2\n",
        encoding="utf-8")
    project = str(tmp_path).replace("\\", "/")
    from orchestrator.graph.indexer import index_project
    from orchestrator.graph.query import template_query
    index_project(project, scope="full")

    payload = template_query(project, "entry_points", limit=50)
    assert payload["error"] is None, payload
    assert payload["count"] == 2, [b.get("name") for b in payload["results"]]
    assert payload["test_entrypoints_excluded"] == 2, payload
    assert payload["total_count"] == payload["count"], (
        "total_count=%d over count=%d -- the total is counting the test runners "
        "this view deliberately drops" % (payload["total_count"], payload["count"]))
    assert payload["truncated"] is False, payload

    # And it still truncates honestly when the cap really does bite.
    page = template_query(project, "entry_points", limit=1)
    assert (page["count"], page["total_count"], page["truncated"]) == (1, 2, True), page


def test_impact_scope_is_the_exemption_and_still_counts_itself(tmp_path) -> None:
    """The one branch that does NOT count a population -- so the fallback must be honest.

    impact_scope answers ONE synthetic row whose own `truncated` reports the
    TRACE-DEPTH cap, not a row count, so the tail leaves its `truncated` alone.
    That makes it the only template reaching `total is None`, and the fallback
    there is len(res).

    Mutating that fallback to 0 survived the first eleven tests in this file:
    nothing asserted on impact_scope, so `count=1 total_count=0` -- the EXACT
    contradiction this file was written about -- would have shipped in the one
    template the fix deliberately exempted. The exemption is from deriving
    `truncated`; it was never an exemption from describing itself correctly.
    """
    project = _graph_repo(tmp_path, modules=3)
    from orchestrator.graph.query import template_query

    payload = template_query(project, "impact_scope", name="helper", depth=2)
    assert payload["error"] is None, payload
    assert payload["count"] == 1, payload
    assert payload["total_count"] == payload["count"], (
        "impact_scope answered count=%r total_count=%r -- the same self-"
        "contradiction, in the template that was exempted from deriving "
        "truncated" % (payload["count"], payload["total_count"]))
    # And its `truncated` still comes from the trace, not from the row count:
    # 1-of-1 rows would derive False and hide a depth-capped blast radius.
    assert payload["truncated"] is bool(
        payload["results"][0]["truncated"]), payload


def test_a_depth_capped_blast_radius_is_not_reported_as_complete(tmp_path) -> None:
    """Why impact_scope keeps its own `truncated` instead of deriving one.

    impact_scope answers ONE synthetic row, so the derived rule -- truncated iff
    offset+count < total_count -- computes 1 < 1 = False no matter how much the
    BFS actually dropped. Its `truncated` comes from cbm_trace_path, which sets
    it on depth_clamped / max_symbols / max_files / timeout.

    Measured on this fixture: 60 files reach `helper`, over the
    GRAPH_CONTEXT_MAX_FILES=50 soft cap, so the trace answers truncated=True
    reason=max_files. Removing the exemption survived the previous fifteen tests
    because every fixture in this file was small enough to trace completely --
    and the one payload it would have corrupted is the one a caller reads to
    decide the blast radius of a change.
    """
    (tmp_path / "util.py").write_text(
        "def helper():\n    return 42\n", encoding="utf-8")
    for index in range(60):  # > GRAPH_CONTEXT_MAX_FILES (50)
        (tmp_path / ("c%03d.py" % index)).write_text(
            "from util import helper\n\n\ndef use%03d():\n    return helper()\n" % index,
            encoding="utf-8")
    project = str(tmp_path).replace("\\", "/")
    from orchestrator.graph.indexer import index_project
    from orchestrator.graph.query import template_query
    index_project(project, scope="full")

    payload = template_query(project, "impact_scope", name="helper", depth=2)
    assert payload["error"] is None, payload
    row = payload["results"][0]
    assert row["truncated"] is True and row["truncation_reason"] == "max_files", (
        "the fixture no longer trips the file cap, so this test cannot see the "
        "defect it was written for: %r" % ({k: row[k] for k in
                                           ("truncated", "truncation_reason")},))
    assert payload["truncated"] is True, (
        "count=1 total_count=1 derived truncated=False over a trace that "
        "dropped files at the cap -- a partial blast radius reported as "
        "complete is the worst direction for this particular payload to lie")


def test_the_docstring_no_longer_promises_zero() -> None:
    """The contract a CA reads. It SAID 0 was expected, so the bug looked intended.

    template_query's docstring read: "total_count is the true population for
    paginated templates (orphan_symbols); 0 otherwise." A reader checking
    whether total_count=0 was a defect would have concluded it was documented
    behaviour and closed the report. Documentation that describes a bug as a
    feature is part of the bug.
    """
    from orchestrator.graph.query import template_query
    doc = template_query.__doc__ or ""
    assert "0 otherwise" not in doc, (
        "the docstring still documents total_count=0 as correct: " + doc)
    assert "total_count" in doc, doc
