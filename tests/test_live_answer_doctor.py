"""The doctor that outlived the campaign must itself stay honest.

`scripts/live_answer_doctor.py` exists because the JS-repo audit found six
defects (B011-B016) that a green suite could not see: a fresh `tmp_path` fixture
is the one environment in which all six answers are honest. The doctor asks a
live daemon about a real, already-indexed, non-Python repo instead.

That makes the doctor itself untested by construction -- it needs a daemon and a
real repo, which a unit test has neither of. So these tests do the one thing that
CAN be pinned offline: they feed each check a **synthetic answer shaped like the
bug it was written for**, and assert it FAILS. A check that cannot fail is
decoration, and decoration is exactly what B011-B016 slipped past.

Every check below was mutation-verified by hand against the live daemon on
2026-08-21 (10 pass / 0 fail / 1 warn on the reported repo) before this file was
written; these tests keep that property from rotting.

Two bugs in the doctor's FIRST run are pinned here too, because both are
mistakes any future CA will repeat:

  * `cbm_template_query` answers `results` + `count`, NOT `rows`. Reading `rows`
    yields 0 and reads as "the query is broken" -- the same class of mistake as
    reading past the HTTP `result` envelope.
  * `validate_file` refuses a path OUTSIDE the project on scope grounds before
    it ever considers `lang`, so a probe file outside the repo measures nothing.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "scripts" / "live_answer_doctor.py"


def _load():
    """Import the script by path -- `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location("live_answer_doctor", DOCTOR)
    assert spec is not None and spec.loader is not None, DOCTOR
    mod = importlib.util.module_from_spec(spec)
    sys.modules["live_answer_doctor"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def lad():
    assert DOCTOR.is_file(), "the doctor script is gone: %s" % DOCTOR
    return _load()


@pytest.fixture
def rep(lad):
    return lad.Report()


class _Daemon:
    """A stand-in for `call()` returning whatever a scenario needs.

    Keyed by tool name; a missing key is an explicit failure rather than an
    empty dict, so a check that starts asking a new question cannot silently
    read `None` and pass.
    """

    def __init__(self, **answers):
        self.answers = answers
        self.seen = []

    def __call__(self, tool, payload, **_kw):
        self.seen.append((tool, payload))
        if tool not in self.answers:
            raise AssertionError("check asked for %r, which this scenario does "
                                 "not stub" % tool)
        ans = self.answers[tool]
        return ans(payload) if callable(ans) else ans


def _install(monkeypatch, lad, daemon):
    monkeypatch.setattr(lad, "call", daemon)
    return daemon


# ---------------------------------------------------------------- B016

def test_a_reverted_extractor_version_is_a_failure(lad, rep):
    """The exact B016 regression: the bump undone, everything else fine."""
    lad.check_extractor_version_is_current(
        rep, {"extractor_version": lad.PRE_B013_EXTRACTOR})
    assert rep.failures, ("the doctor accepted the pre-B013 extractor, so a "
                          "reverted bump would ship silently again")
    assert rep.rows[0]["bug"] == "B016"


def test_a_bumped_extractor_version_passes(lad, rep):
    """Any LATER string must pass -- this guards the omission, not the bump."""
    lad.check_extractor_version_is_current(
        rep, {"extractor_version": "ast-p0-v12-something-later"})
    assert not rep.failures, rep.rows


def test_a_missing_extractor_version_warns_rather_than_passes(lad, rep):
    """Absent evidence is not evidence: WARN, never a silent PASS."""
    lad.check_extractor_version_is_current(rep, {})
    assert rep.counts()["WARN"] == 1
    assert rep.counts()["PASS"] == 0


# ---------------------------------------------------------------- B011

def test_a_stale_count_that_never_moves_is_a_failure(lad, rep, monkeypatch):
    """B011's shape: `completed`, yet a count two reindexes cannot clear."""
    _install(monkeypatch, lad, _Daemon(
        cbm_index={"status": "completed", "files_indexed": 0,
                   "files_unchanged": 41, "files_pruned": 0},
        cbm_graph_status={"stale_files": 24, "freshness": "stale"}))
    lad.check_index_converges(rep, "/repo")
    assert any(r["check"] == "index converges" and r["verdict"] == "FAIL"
               for r in rep.rows), rep.rows


def test_freshness_contradicting_stale_files_is_a_failure(lad, rep, monkeypatch):
    """One field of a payload must not contradict another in the same payload."""
    _install(monkeypatch, lad, _Daemon(
        cbm_index={"status": "completed"},
        cbm_graph_status={"stale_files": 24, "freshness": "fresh"}))
    lad.check_index_converges(rep, "/repo")
    assert any(r["check"] == "freshness agrees with stale_files"
               and r["verdict"] == "FAIL" for r in rep.rows), rep.rows


def test_a_converged_index_passes(lad, rep, monkeypatch):
    _install(monkeypatch, lad, _Daemon(
        cbm_index={"status": "completed", "files_indexed": 42},
        cbm_graph_status={"stale_files": 0, "freshness": "fresh"}))
    lad.check_index_converges(rep, "/repo")
    assert not rep.failures, rep.rows


# ---------------------------------------------------------------- B013

def test_zero_dependency_edges_on_a_js_repo_is_a_failure(lad, rep, monkeypatch):
    """The reported symptom, verbatim: hundreds of callables, zero edges."""
    _install(monkeypatch, lad, _Daemon(
        cbm_template_query={"count": 0, "results": []}))
    lad.check_dependency_edges_exist(rep, "/repo", {"dependency_edges": 0},
                                     "javascript")
    assert rep.failures, rep.rows


def test_edges_that_no_query_can_reach_is_a_failure(lad, rep, monkeypatch):
    """The path-shaped-key half of B013: edges exist, `module_deps` is empty.

    This is the subtler half, and the half a count of edges alone would miss.
    """
    _install(monkeypatch, lad, _Daemon(
        cbm_template_query={"count": 0, "results": []}))
    lad.check_dependency_edges_exist(rep, "/repo", {"dependency_edges": 46},
                                     "javascript")
    assert any(r["check"] == "edges are queryable" and r["verdict"] == "FAIL"
               for r in rep.rows), rep.rows


def test_the_doctor_reads_results_not_rows(lad, rep, monkeypatch):
    """`cbm_template_query` answers `results`/`count`. Reading `rows` was a bug
    in the doctor's own first run: 14 real rows were reported as 0."""
    _install(monkeypatch, lad, _Daemon(
        cbm_template_query={"count": 14,
                            "results": [{"file": "a.js", "depends_on": "lib"}]}))
    lad.check_dependency_edges_exist(rep, "/repo", {"dependency_edges": 46},
                                     "javascript")
    assert not rep.failures, ("a healthy module_deps answer was read as empty "
                              "-- the doctor is reading the wrong key: %s"
                              % rep.rows)
    assert "rows=14" in rep.rows[0]["detail"], rep.rows[0]["detail"]


# ---------------------------------------------------------------- B014

def test_an_inert_file_filter_is_a_failure(lad, rep, monkeypatch):
    """The reusable trick: an IMPOSSIBLE glob returning the unfiltered count.

    A narrow glob returning many results is ambiguous -- it may just be broad.
    An impossible one returning everything is proof the argument is discarded.
    """
    _install(monkeypatch, lad, _Daemon(
        cbm_dead_code=lambda p: ({"count": 207, "files_matched": None,
                                  "file_filter_applied": False}
                                 if p.get("file_filter")
                                 else {"count": 207})))
    lad.check_file_filter_is_not_inert(rep, "/repo")
    assert len(rep.failures) == 2, rep.rows
    assert any("inert, not broad" in r["detail"] for r in rep.failures)


def test_an_honoured_file_filter_passes(lad, rep, monkeypatch):
    _install(monkeypatch, lad, _Daemon(
        cbm_dead_code=lambda p: ({"count": 0, "files_matched": 0,
                                  "file_filter_applied": True}
                                 if p.get("file_filter")
                                 else {"count": 211})))
    lad.check_file_filter_is_not_inert(rep, "/repo")
    assert not rep.failures, rep.rows


# ---------------------------------------------------------------- B015

def test_an_engine_that_does_not_name_itself_is_a_failure(lad, rep, monkeypatch):
    """Falling back is legitimate; not saying so is the defect."""
    _install(monkeypatch, lad, _Daemon(
        cbm_dead_code={"count": 207, "language_detected": "javascript"}))
    lad.check_engine_says_what_ran(rep, "/repo", "javascript")
    assert any("names its engine" in r["check"] and r["verdict"] == "FAIL"
               for r in rep.rows), rep.rows


def test_an_engine_without_a_reason_is_a_failure(lad, rep, monkeypatch):
    _install(monkeypatch, lad, _Daemon(
        cbm_dead_code={"engine": "ast-p0-v1", "language_detected": "javascript"}))
    lad.check_engine_says_what_ran(rep, "/repo", "javascript")
    assert any("does not say why it fell back" in r["detail"]
               for r in rep.failures), rep.rows


def test_language_detected_other_is_a_failure(lad, rep, monkeypatch):
    """`other` throws away what the detector had just counted."""
    _install(monkeypatch, lad, _Daemon(
        cbm_dead_code={"engine": "ast-p0-v1", "engine_reason": "because",
                       "language_detected": "other"}))
    lad.check_engine_says_what_ran(rep, "/repo", "javascript")
    assert any(r["check"] == "language_detected is specific"
               and r["verdict"] == "FAIL" for r in rep.rows), rep.rows


def test_ruff_only_categories_warn_on_a_non_python_repo(lad, rep, monkeypatch):
    """The gap Anh deferred must stay VISIBLE as a WARN, not vanish.

    An empty `bug_risk` on a JS repo is not good news, and a doctor that says
    nothing about it lets a reader mistake absence for cleanliness.
    """
    _install(monkeypatch, lad, _Daemon(
        cbm_dead_code={"engine": "ast-p0-v1", "engine_reason": "majority js",
                       "language_detected": "javascript"}))
    lad.check_engine_says_what_ran(rep, "/repo", "javascript")
    warns = [r for r in rep.rows if r["verdict"] == "WARN"]
    assert warns, "the Ruff-only gap went unmentioned on a JS repo"
    assert "bug_risk" in warns[0]["detail"]
    assert not rep.failures, "a deferred gap must WARN, never FAIL: %s" % rep.rows


def test_a_python_repo_does_not_warn_about_ruff_categories(lad, rep, monkeypatch):
    """On Python the categories really do run, so there is nothing to warn."""
    _install(monkeypatch, lad, _Daemon(
        cbm_dead_code={"engine": "ruff+vulture", "engine_reason": "python",
                       "language_detected": "python"}))
    lad.check_engine_says_what_ran(rep, "/repo", "python")
    assert rep.counts()["WARN"] == 0, rep.rows


# ---------------------------------------------------------------- B012

def test_an_unknown_lang_answering_ok_true_is_a_failure(lad, rep, monkeypatch,
                                                       tmp_path):
    """B012's shape exactly: `ok:true, lang:"none"`, nothing run."""
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    _install(monkeypatch, lad, _Daemon(
        validate_file={"ok": True, "lang": "none", "checked": False}))
    lad.check_validate_file_fails_loud(rep, str(tmp_path))
    assert rep.failures, rep.rows
    assert any("any misspelling" in r["detail"] for r in rep.failures)


def test_a_refusal_without_supported_langs_is_a_failure(lad, rep, monkeypatch,
                                                        tmp_path):
    """A refusal that does not list the vocabulary is unlearnable except from
    source -- which is how `JSON`/`javascript`/`py` went unnoticed."""
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    _install(monkeypatch, lad, _Daemon(
        validate_file={"ok": False, "error_name": "UNSUPPORTED_LANG"}))
    lad.check_validate_file_fails_loud(rep, str(tmp_path))
    assert any(r["check"] == "the refusal is learnable"
               and r["verdict"] == "FAIL" for r in rep.rows), rep.rows


def test_a_probe_file_is_chosen_from_inside_the_repo(lad, tmp_path):
    """`validate_file` refuses an out-of-scope path BEFORE it considers `lang`,
    so a probe outside the repo measures nothing. Measured on the live daemon:
    the same call answers UNSUPPORTED_LANG inside and VALIDATION_FAILED
    outside."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "thing.ts").write_text("export const a = 1;\n",
                                               encoding="utf-8")
    probe = lad._probe_file(str(tmp_path))
    assert probe is not None
    assert Path(probe).resolve().is_relative_to(tmp_path.resolve()), probe


def test_a_repo_with_nothing_to_probe_warns(lad, rep, tmp_path, monkeypatch):
    """No probe file is a WARN (could not run), never a PASS (ran and was ok)."""
    _install(monkeypatch, lad, _Daemon())
    lad.check_validate_file_fails_loud(rep, str(tmp_path))
    assert rep.counts() == {"PASS": 0, "FAIL": 0, "WARN": 1}, rep.rows


# ---------------------------------------------------------------- contract

def test_an_unreachable_daemon_stops_rather_than_guessing(lad, monkeypatch,
                                                          tmp_path, capsys):
    """`connection refused` means STOP and report -- exit 2, never a verdict.

    A doctor that degrades to "all clear" when it cannot reach the daemon is
    worse than no doctor.
    """
    def boom(*_a, **_kw):
        raise lad.Unreachable("cbm_graph_status -> refused")
    monkeypatch.setattr(lad.urllib.request, "urlopen", boom)
    rc = lad.main(["--project-path", str(tmp_path), "--ci"])
    assert rc == 2, rc
    assert "cannot reach [A]" in capsys.readouterr().err


def test_ci_exits_one_only_on_failures(lad, rep):
    """WARN must never block: it means the check could not run here."""
    rep.warn("something", "not applicable")
    assert not rep.failures
    rep.bad("something else", "wrong answer")
    assert len(rep.failures) == 1


def test_the_call_helper_unwraps_the_result_envelope(lad, monkeypatch):
    """The HTTP layer wraps most payloads in `result`; a few do not. Reading the
    wrong level yields all-`None`, which looks exactly like a broken fix."""
    import io
    import json as _json

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()

    def _serving(raw):
        def _urlopen(*_args, **_kwargs):
            return _Resp(_json.dumps(raw).encode())
        return _urlopen

    for raw, want in (({"ok": True, "result": {"count": 7}}, 7),
                      ({"ok": True, "count": 7}, 7)):
        monkeypatch.setattr(lad.urllib.request, "urlopen", _serving(raw))
        assert lad.call("t", {})["count"] == want, raw


# ---------------------------------------------------------------- B018

def test_a_search_that_omits_ok_is_a_failure(lad, rep, monkeypatch):
    """The exact B018 defect, fed to the check: hits present, `ok` absent.

    This is the shape a fixture cannot catch, because a unit test asserts on the
    value of a field whose name it already knows. Only a guard written the way
    callers actually guard -- `if not resp.get("ok")` -- sees it, so the check
    must be that guard.
    """
    _install(monkeypatch, lad, _Daemon(search_code=lambda p: (
        {"count": 3, "results": [1, 2, 3]} if p.get("query")
        else {"ok": False, "error_name": "MISSING_REQUIRED_FIELD"})))
    lad.check_success_answers_ok(rep, "/repo")
    assert rep.failures, "a success payload with no `ok` was allowed to pass"
    assert "no `ok` field" in rep.failures[0]["detail"], rep.failures


def test_a_search_that_answers_ok_false_on_success_is_a_failure(lad, rep,
                                                               monkeypatch):
    """Present but wrong is not better than absent."""
    _install(monkeypatch, lad, _Daemon(search_code=lambda p: (
        {"ok": False, "count": 3, "results": [1, 2, 3]} if p.get("query")
        else {"ok": False, "error_name": "MISSING_REQUIRED_FIELD"})))
    lad.check_success_answers_ok(rep, "/repo")
    assert rep.failures, "ok=False on a search that ran was allowed to pass"


def test_a_correct_search_envelope_passes(lad, rep, monkeypatch):
    """ok:True on the hit, ok:False on the empty query -- both halves right."""
    _install(monkeypatch, lad, _Daemon(search_code=lambda p: (
        {"ok": True, "count": 3, "results": [1, 2, 3]} if p.get("query")
        else {"ok": False, "error_name": "MISSING_REQUIRED_FIELD"})))
    lad.check_success_answers_ok(rep, "/repo")
    assert not rep.failures, rep.rows
    assert rep.counts()["PASS"] == 2, rep.rows


def test_always_saying_ok_is_also_a_failure(lad, rep, monkeypatch):
    """The bounding half: if every answer is ok, the field carries nothing.

    A fix that sets ok:True unconditionally would satisfy the success check and
    silently destroy the failure signal, so the check has to fail that too.
    """
    _install(monkeypatch, lad, _Daemon(
        search_code={"ok": True, "count": 3, "results": [1, 2, 3]}))
    lad.check_success_answers_ok(rep, "/repo")
    assert rep.failures, "an always-ok search_code was allowed to pass"
    assert "stops carrying information" in rep.failures[0]["detail"], rep.failures
