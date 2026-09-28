"""Target spelling: ONE canonical matcher for the write ledger.

WHY this file exists (measured 2026-09-28): `GET /api/write_status?project_path=..
&file_path=..` keyed on the LITERAL (project_path, file_path) pair. One file
written under a different spelling was invisible: a row stored as
project_path=<parent dir> + file_path="project-orchestrator\\code_tools.py"
(op 74b21aeedd7e5199) was not returned for project_path=<repo root> +
file_path="code_tools.py"; 15 rows inside one repo were unreachable, 14 of them
stored as "" + absolute path. A CA checking a file's provenance concluded
"no ledger row" when one existed.

The fix keeps ONE matcher -- `_write_ledger_target_abs(pp, fp)` =
normcase(normpath(join(pp, fp))), an absolute fp staying itself -- used by the
in-memory index scan AND the SQLite side (exact pair + escaped-basename LIKE
prefilter, canonical filter in Python, dedup by operation_id, newest-first).
Stored rows are NOT migrated: the pair remains the storage key, the canonical
abs is the identity.

Each test names the mutant it kills:
- spelling matrix        -> exact-pair matcher (the shipped defect)
- same basename other dir-> basename-only matcher
- LIKE wildcards         -> unescaped `_`/`%` over-match in the SQL prefilter
- memory/sqlite parity   -> two matchers drifting apart again
- root-scoped ("", fp="")-> canonical collapse of DIFFERENT roots onto one key
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import code_tools
import orchestrator_server as srv


FILE = r"d:\repo\pkg\mod.py"
# The three spellings ONE file was measured under in sessions.db.
SPELLINGS = {
    "root_relative": {"project_path": r"d:\repo", "file_path": r"pkg\mod.py"},
    "parent_subpath": {"project_path": r"d:\repo\pkg", "file_path": "mod.py"},
    "empty_absolute": {"project_path": "", "file_path": FILE},
}


def _rec(op_id: str, body: dict, updated_at: float,
         tool: str = "write_fix", status: str = "committed") -> dict:
    """A ledger record shaped exactly like _write_ledger_begin's."""
    pp, fp = srv._write_ledger_target(body)
    return {
        "operation_id": op_id, "tool": tool, "status": status,
        "project_path": pp, "file_path": fp, "session_id": "sess-t",
        "started_at": updated_at, "finished_at": updated_at,
        "duration_ms": 1, "attempts": 1, "previous_status": None,
        "result": {"ok": True}, "origin_generation": srv.SERVER_GENERATION,
        "updated_at": updated_at,
    }


class CanonicalTargetLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db = code_tools._SESSIONS_DB
        self.old_generation = srv.SERVER_GENERATION
        code_tools._SESSIONS_DB = Path(self.temp_dir.name) / "sessions.db"
        code_tools._sync_session_db_alias()
        code_tools._init_session_db()
        srv.SERVER_GENERATION = "canonical-target-generation"
        srv._WRITE_LEDGER.clear()
        srv._WRITE_LEDGER_BY_TARGET.clear()
        srv._WRITE_LEDGER_LAST_PURGE = 0.0

    def tearDown(self):
        srv._WRITE_LEDGER.clear()
        srv._WRITE_LEDGER_BY_TARGET.clear()
        srv._WRITE_LEDGER_LAST_PURGE = 0.0
        srv.SERVER_GENERATION = self.old_generation
        code_tools._SESSIONS_DB = self.old_db
        code_tools._sync_session_db_alias()
        code_tools._session_store_module.close_writer()
        self.temp_dir.cleanup()

    # -- helpers ---------------------------------------------------------
    def _store_sqlite(self, rec: dict):
        self.assertTrue(srv._write_ledger_db_upsert(rec), "upsert must succeed")

    def _store_memory(self, rec: dict):
        srv._WRITE_LEDGER[rec["operation_id"]] = dict(rec)
        srv._write_ledger_target_remember(
            rec["project_path"], rec["file_path"], rec["operation_id"])

    def _query(self, spelling_body: dict) -> tuple[str, str]:
        return srv._write_ledger_target(spelling_body)

    # -- the canonical matcher itself ------------------------------------
    def test_one_file_three_spellings_one_identity(self):
        ids = {srv._write_ledger_target_abs(*self._query(b))
               for b in SPELLINGS.values()}
        self.assertEqual(ids, {FILE.lower().replace("/", "\\")})

    def test_no_file_means_no_identity(self):
        # Root-scoped operations (file_path="") must NOT collapse onto each
        # other or onto files: "" is the "no canonical identity" answer.
        self.assertEqual(srv._write_ledger_target_abs(r"d:\repo", ""), "")
        self.assertEqual(srv._write_ledger_target_abs("", ""), "")
        # ISSUE 2 (leader audit): a pair that does not resolve to an ABSOLUTE
        # path has no identity either. Otherwise two relative-only rows stored
        # under different (missing) roots -- ("", "pkg\\core.py") twice -- would
        # cross-match through the canonical path. The exact-pair fallback still
        # covers a repeated identical spelling.
        self.assertEqual(srv._write_ledger_target_abs("", r"pkg\core.py"), "")
        self.assertEqual(srv._write_ledger_target_abs("repo", r"pkg\core.py"), "")

    # -- pin 1: every stored spelling found by every query spelling -------
    def test_sqlite_list_returns_every_spelling_for_every_query(self):
        ops = {}
        for i, (name, body) in enumerate(sorted(SPELLINGS.items())):
            op = f"op-{name}"
            ops[name] = op
            self._store_sqlite(_rec(op, body, 100.0 + i))
        for qname, qbody in SPELLINGS.items():
            rows = srv._write_ledger_db_list(self._query(qbody))
            self.assertEqual(
                {r["operation_id"] for r in rows}, set(ops.values()),
                f"query spelling {qname} must see every stored spelling")

    def test_sqlite_get_target_finds_foreign_spelling_newest_first(self):
        self._store_sqlite(_rec("op-old", SPELLINGS["root_relative"], 100.0))
        self._store_sqlite(_rec("op-new", SPELLINGS["empty_absolute"], 200.0))
        for qbody in SPELLINGS.values():
            rec = srv._write_ledger_db_get(target=self._query(qbody))
            self.assertIsNotNone(rec)
            # Newest-first ordering survives the merged-spelling fetch.
            self.assertEqual(rec["operation_id"], "op-new")
        rows = srv._write_ledger_db_list(self._query(SPELLINGS["root_relative"]))
        self.assertEqual([r["operation_id"] for r in rows], ["op-new", "op-old"])

    def test_newer_same_basename_rows_cannot_starve_the_true_row(self):
        # BUG 1 (leader audit, measured): the basename-prefilter SELECT carried
        # LIMIT n BEFORE the Python canonical filter ran. With >= n NEWER
        # same-basename rows in other directories, they filled the LIMIT, the
        # filter rejected all of them, and the true row was never fetched --
        # db_get -> None, db_list -> [], exactly the silent "no ledger row"
        # this change exists to remove. Common basenames (core.py, __init__.py,
        # SKILL.md) make it realistic. The fix streams the basename cursor
        # newest-first and stops once n canonical matches are collected.
        true_body = {"project_path": "", "file_path": r"d:\repo\pkg\core.py"}
        self._store_sqlite(_rec("op-true", true_body, 100.0))
        for i in range(25):
            self._store_sqlite(_rec(
                f"op-noise{i}",
                {"project_path": rf"d:\other{i}", "file_path": "core.py"},
                200.0 + i))
        q = self._query({"project_path": r"d:\repo", "file_path": r"pkg\core.py"})
        rec = srv._write_ledger_db_get(target=q)
        self.assertIsNotNone(
            rec, "db_get must find the true row past 25 NEWER same-basename "
                 "rows in other directories (default limit would starve it)")
        self.assertEqual(rec["operation_id"], "op-true")
        rows = srv._write_ledger_db_list(q)  # default limit=20 < 25 noise rows
        self.assertEqual([r["operation_id"] for r in rows], ["op-true"])

    def test_memory_index_returns_every_spelling_for_every_query(self):
        for i, (name, body) in enumerate(sorted(SPELLINGS.items())):
            self._store_memory(_rec(f"op-{name}", body, 100.0 + i))
        expected = {f"op-{n}" for n in SPELLINGS}
        for qname, qbody in SPELLINGS.items():
            ids = set(srv._write_ledger_target_ids(*self._query(qbody)))
            self.assertEqual(ids, expected, f"memory query spelling {qname}")

    # -- pin 2: same basename, different directory -------------------------
    def test_same_basename_other_directory_is_not_returned(self):
        here = _rec("op-here", SPELLINGS["root_relative"], 100.0)
        there = _rec("op-there", {"project_path": r"d:\other",
                                  "file_path": r"pkg\mod.py"}, 200.0)
        for rec in (here, there):
            self._store_sqlite(rec)
            self._store_memory(rec)
        q = self._query(SPELLINGS["root_relative"])
        self.assertEqual({r["operation_id"]
                          for r in srv._write_ledger_db_list(q)}, {"op-here"})
        self.assertEqual(set(srv._write_ledger_target_ids(*q)), {"op-here"})

    # -- pin 3: LIKE wildcards in the basename -----------------------------
    def test_like_escape_helper_escapes_wildcards_and_itself(self):
        self.assertEqual(srv._write_ledger_like_escape("a_b.py"), "a!_b.py")
        self.assertEqual(srv._write_ledger_like_escape("a%b.py"), "a!%b.py")
        self.assertEqual(srv._write_ledger_like_escape("a!b.py"), "a!!b.py")

    def test_underscore_basename_does_not_overmatch(self):
        # Unescaped, LIKE '%\\a_b.py' would also fetch 'axb.py'; and an
        # over-escaped pattern would stop finding 'a_b.py' itself. Pin BOTH
        # directions so the escape is exactly one '!' per wildcard.
        under = {"project_path": r"d:\repo", "file_path": r"pkg\a_b.py"}
        plain = {"project_path": r"d:\repo", "file_path": r"pkg\axb.py"}
        self._store_sqlite(_rec("op-under", under, 100.0))
        self._store_sqlite(_rec("op-plain", plain, 200.0))
        q_under = self._query(under)
        self.assertEqual({r["operation_id"]
                          for r in srv._write_ledger_db_list(q_under)},
                         {"op-under"})
        q_plain = self._query(plain)
        self.assertEqual({r["operation_id"]
                          for r in srv._write_ledger_db_list(q_plain)},
                         {"op-plain"})

    def test_percent_basename_does_not_overmatch(self):
        pct = {"project_path": r"d:\repo", "file_path": r"pkg\a%b.py"}
        short = {"project_path": r"d:\repo", "file_path": r"pkg\ab.py"}
        self._store_sqlite(_rec("op-pct", pct, 100.0))
        self._store_sqlite(_rec("op-short", short, 200.0))
        self.assertEqual({r["operation_id"]
                          for r in srv._write_ledger_db_list(self._query(pct))},
                         {"op-pct"})
        self.assertEqual({r["operation_id"]
                          for r in srv._write_ledger_db_list(self._query(short))},
                         {"op-short"})

    # -- pin 4: memory and sqlite agree ------------------------------------
    def test_memory_and_sqlite_answer_the_same_question(self):
        recs = [_rec(f"op-{n}", b, 100.0 + i)
                for i, (n, b) in enumerate(sorted(SPELLINGS.items()))]
        recs.append(_rec("op-other-dir", {"project_path": r"d:\other",
                                          "file_path": r"pkg\mod.py"}, 99.0))
        for rec in recs:
            self._store_sqlite(rec)
            self._store_memory(rec)
        for qbody in list(SPELLINGS.values()) + [
                {"project_path": r"d:\repo", "file_path": r"pkg\other.py"}]:
            q = self._query(qbody)
            mem = set(srv._write_ledger_target_ids(*q))
            disk = {r["operation_id"] for r in srv._write_ledger_db_list(q)}
            self.assertEqual(mem, disk, f"parity for query {q}")

    # -- root-scoped keys stay per-root (ISS-01 semantics preserved) -------
    def test_root_scoped_rows_do_not_cross_roots(self):
        a = _rec("op-root-a", {"project_path": r"d:\repo"}, 100.0,
                 tool="cbm_incremental_index")
        b = _rec("op-root-b", {"project_path": r"d:\other"}, 200.0,
                 tool="cbm_incremental_index")
        for rec in (a, b):
            self._store_sqlite(rec)
            self._store_memory(rec)
        qa = self._query({"project_path": r"d:\repo"})
        self.assertEqual({r["operation_id"]
                          for r in srv._write_ledger_db_list(qa)}, {"op-root-a"})
        self.assertEqual(set(srv._write_ledger_target_ids(*qa)), {"op-root-a"})

    # -- end-to-end through the HTTP handler -------------------------------
    def test_write_status_finds_foreign_spelling_from_sqlite_only(self):
        # The measured failure: memory is empty (restart), the row was stored
        # as "" + absolute path, the CA queries root + relative.
        self._store_sqlite(_rec("op-abs", SPELLINGS["empty_absolute"], 100.0))
        srv._WRITE_LEDGER.clear()
        srv._WRITE_LEDGER_BY_TARGET.clear()
        request = SimpleNamespace(headers={}, query={
            "project_path": r"d:\repo", "file_path": r"pkg\mod.py"})
        response = asyncio.run(srv.handle_write_status(request))
        payload = json.loads(response.text)
        self.assertEqual(response.status, 200, payload)
        self.assertEqual(payload["operation_id"], "op-abs")
        self.assertEqual(payload["commit_state"], "committed")
        self.assertEqual(payload["ledger_source"], "sqlite")
        self.assertEqual(payload["lookup"], "target")
        # The compliant-path response shape must not grow new advisory fields.
        self.assertEqual(set(payload), {
            "ok", "lookup", "ledger_source", "commit_state", "operation_state",
            "safe_to_retry", "ca_next_action", "operation_id", "tool", "status",
            "project_path", "file_path", "session_id", "started_at",
            "finished_at", "duration_ms", "attempts", "previous_status",
            "result", "origin_generation", "progress", "last_progress_at",
            "generation", "candidates"})


if __name__ == "__main__":
    unittest.main()