"""Two reported failures on the git surface, both about a payload nobody could read.

Reported from live use: ``git_status`` / ``git_stage`` / ``git_commit`` all came back
past the transport's ~63k ceiling and arrived TRUNCATED, so the response had to be
routed through a file to be read at all. And ``git_commit`` with
``expected_staged_hash="auto"`` was refused as ``GIT_STATE_STALE`` even though the
index had not changed -- the only way through was to copy ``current_staged_hash``
out of the error text and resend.

The two are one causal chain, which is why they are fixed and pinned together:
the truncation is what made ``staged_hash`` unreadable, and an unreadable hash is
why a caller reaches for ``"auto"`` in the first place.

Three properties, asserted rather than described:

* ``records`` was 89% of the payload (measured ~216 chars/record) and the only
  unbounded part, so it is capped -- and the cap is REPORTED, never silent.
* ``counts``/``state_hash``/``staged_hash`` are computed over EVERY record and now
  sit AHEAD of ``records`` in the JSON, so a truncated response still carries an
  exact optimistic lock.
* ``"auto"`` is an explicit opt-out of that lock, distinct from a missing field
  (its own error name) and from a genuine mismatch (still refused).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator import git_tools as g  # noqa: E402


def _repo(tmp_path: Path) -> Path:
    def run(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       stdin=subprocess.DEVNULL, text=True)
    run("init", "-q")
    run("config", "user.name", "Test")
    run("config", "user.email", "test@example.com")
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    run("add", "a.txt")
    run("commit", "-qm", "init")
    return tmp_path


def _dirty(root: Path, count: int) -> None:
    for index in range(count):
        (root / ("f%03d.txt" % index)).write_text("x" * 200, encoding="utf-8")


def test_a_large_dirty_tree_no_longer_blows_the_payload(tmp_path) -> None:
    """The reported symptom, reproduced as a measurement rather than a story.

    120 untracked files is a mid-refactor tree, not a pathological one. Uncapped it
    is what pushed the response past the ceiling; capped it must be a fraction of
    that, and must SAY it was capped.
    """
    root = _repo(tmp_path)
    _dirty(root, 120)

    full = g.status(str(root))
    capped = g.status(str(root), max_records=10)

    assert full["records_total"] == capped["records_total"] >= 120
    assert full["records_truncated"] is False
    assert capped["records_truncated"] is True, "a capped response did not say so"
    assert len(capped["records"]) == 10

    full_chars = len(json.dumps(full, ensure_ascii=False))
    capped_chars = len(json.dumps(capped, ensure_ascii=False))
    assert capped_chars < full_chars / 4, (full_chars, capped_chars)


def test_the_optimistic_lock_survives_truncation(tmp_path) -> None:
    """The property that actually broke: staged_hash must be READABLE.

    It used to sit at the END of the JSON, after an unbounded records list, so a
    response cut at the transport limit lost exactly the field git_commit requires.
    Asserted on position, because that is what truncation destroys.
    """
    root = _repo(tmp_path)
    _dirty(root, 200)
    payload = g.status(str(root), max_records=5)
    blob = json.dumps(payload, ensure_ascii=False)

    assert blob.index("staged_hash") < 1000, (
        "staged_hash is not near the front of the payload: offset %d"
        % blob.index("staged_hash"))
    assert blob.index("staged_hash") < blob.index('"records"')
    assert blob.index("state_hash") < blob.index('"records"')


def test_capping_records_does_not_change_a_single_hash_or_count(tmp_path) -> None:
    """A cap that changed the hash would be worse than the truncation it replaces.

    The whole point is that the DROPPED records still count: the lock and the
    counts are computed over the full set, so a capped read and a full read are
    interchangeable for every decision a caller makes with them.
    """
    root = _repo(tmp_path)
    _dirty(root, 60)
    (root / "staged.txt").write_text("s", encoding="utf-8")
    g.stage(str(root), ["staged.txt"])

    full = g.status(str(root))
    capped = g.status(str(root), max_records=1)

    assert capped["staged_hash"] == full["staged_hash"]
    assert capped["state_hash"] == full["state_hash"]
    assert capped["counts"] == full["counts"]
    assert capped["counts"]["staged"] == 1
    assert capped["clean"] is False, "clean must reflect every record, not the shown ones"


def test_zero_means_uncapped_so_in_process_callers_are_unaffected(tmp_path) -> None:
    """The default must not silently shorten anything.

    ``_orphan_cache_sidecars`` and friends walk ``records``; a cap that applied by
    default would make them quietly incomplete. 0 is the default and means all.
    """
    root = _repo(tmp_path)
    _dirty(root, 30)
    payload = g.status(str(root))
    assert len(payload["records"]) == payload["records_total"] >= 30
    assert payload["records_truncated"] is False
    assert g.status(str(root), max_records=0)["records_truncated"] is False


def test_stage_no_longer_returns_two_whole_statuses(tmp_path) -> None:
    """git_stage was the worst offender: it doubled the payload for no new data.

    ``before`` was a FULL status the caller had just read to get
    expected_state_hash. Keeping only the hashes and counts is what a caller can
    act on; asserted on the SHAPE so a future re-expansion is caught.
    """
    root = _repo(tmp_path)
    _dirty(root, 100)
    (root / "target.txt").write_text("t", encoding="utf-8")

    result = g.stage(str(root), ["target.txt"])

    assert set(result["before"]) == {"state_hash", "staged_hash", "counts"}
    assert "records" not in result["before"], "stage is carrying a full status again"
    assert result["after"]["counts"]["staged"] == 1
    # And the hashes still work as a lock: staging changed the index.
    assert result["before"]["staged_hash"] != result["after"]["staged_hash"]


def test_auto_commits_what_is_staged_right_now(tmp_path) -> None:
    """The second reported failure, stated directly.

    "auto" used to be compared as a literal string against a sha256 and therefore
    always mismatched, reported as GIT_STATE_STALE -- a message that named the
    wrong cause and cost one round-trip to get past.
    """
    root = _repo(tmp_path)
    (root / "b.txt").write_text("b\n", encoding="utf-8")
    g.stage(str(root), ["b.txt"])

    result = g.safe_call(g.commit, str(root), "add b via auto", "auto")

    assert result.get("ok") is True, result
    assert result.get("commit")
    assert result["status"]["counts"]["staged"] == 0


def test_auto_is_not_a_way_to_commit_nothing(tmp_path) -> None:
    """Opting out of the STALENESS check must not opt out of every other check.

    "auto" says "I accept the current index", not "commit regardless". An empty
    index is still NOTHING_STAGED, or the escape hatch would be a way to create
    empty commits by accident.
    """
    root = _repo(tmp_path)
    result = g.safe_call(g.commit, str(root), "nothing here", "auto")
    assert result.get("ok") is not True
    assert result.get("error_name") == "NOTHING_STAGED", result


def test_a_missing_hash_says_so_instead_of_blaming_staleness(tmp_path) -> None:
    """A missing field and a stale field are different bugs with different fixes.

    Both used to raise GIT_STATE_STALE, which sent a caller looking for a race
    that was not there. The error also carries the hash, so the corrected retry
    needs no second call.
    """
    root = _repo(tmp_path)
    (root / "b.txt").write_text("b\n", encoding="utf-8")
    g.stage(str(root), ["b.txt"])

    result = g.safe_call(g.commit, str(root), "x", "")

    assert result.get("error_name") == "EXPECTED_STAGED_HASH_REQUIRED", result
    assert result.get("current_staged_hash"), "the retry hash is not in the error"


def test_a_genuine_mismatch_is_still_refused(tmp_path) -> None:
    """The guard must keep guarding, or accepting "auto" would have removed it.

    This is the case the optimistic lock exists for: a hash read before someone
    else touched the index. It stays refused, and now says it is retryable and how.
    """
    root = _repo(tmp_path)
    (root / "b.txt").write_text("b\n", encoding="utf-8")
    stale = g.status(str(root))["staged_hash"]
    g.stage(str(root), ["b.txt"])

    result = g.safe_call(g.commit, str(root), "x", stale)

    assert result.get("error_name") == "GIT_STATE_STALE", result
    assert result.get("retryable") is True
    assert result.get("current_staged_hash") != stale
    assert result.get("ca_next_action")

    # The hash from the error is the one that works -- the documented recovery.
    fixed = g.safe_call(g.commit, str(root), "x", result["current_staged_hash"])
    assert fixed.get("ok") is True, fixed


def test_an_explicit_correct_hash_still_works(tmp_path) -> None:
    """The normal path, kept honest: the fix must not have made "auto" mandatory."""
    root = _repo(tmp_path)
    (root / "b.txt").write_text("b\n", encoding="utf-8")
    g.stage(str(root), ["b.txt"])
    payload = g.status(str(root))

    result = g.safe_call(g.commit, str(root), "add b", payload["staged_hash"])
    assert result.get("ok") is True, result


def test_the_http_tool_exposes_max_records() -> None:
    """A parameter absent from the registry schema is unreachable over HTTP.

    ``allowed_params`` is built from the schema, so a caller passing max_records
    would get UNSUPPORTED_FIELD and be left with the truncation this fixes.
    """
    import inspect
    import orchestrator_server as srv

    assert "max_records" in inspect.signature(srv.git_status).parameters
    source = inspect.getsource(srv)
    marker = '("git_status",git_status,'
    entry = source[source.index(marker):]
    entry = entry[:entry.index("\n")]
    assert '"max_records"' in entry, "max_records is not in the git_status schema"
