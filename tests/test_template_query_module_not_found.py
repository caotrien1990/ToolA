"""Regression tests for P2 (2026-10-07).

cbm_template_query used to answer count:0 SILENTLY when the requested module
did not exist, making "module missing" indistinguishable from "module has
zero imports". The imports/imported_by branches now fail loud with
module_not_found, mirroring the node_not_found pattern of callers/callees
(query.py L428-432).

Encoded contract:
  - unknown module  -> payload["error"] startswith "module_not_found"
  - missing module param -> same module_not_found (not silent count:0)
  - known module    -> old behaviour preserved (error None, real results)
"""
import unittest
from unittest import mock

from orchestrator.graph import query as q


class _FakeCursor:
    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


class _FakeConn:
    """Routes canned results by SQL shape: module-existence probe,
    COUNT(*) population, or row SELECT. Records close() for hygiene."""

    def __init__(self, module_exists, rows, total):
        self._module_exists = module_exists
        self._rows = rows
        self._total = total
        self.closed = False

    def execute(self, sql, params=()):
        if sql.lstrip().startswith("SELECT id FROM nodes WHERE kind='module'"):
            return _FakeCursor(one={"id": "m1"} if self._module_exists else None)
        if "COUNT(*)" in sql:
            return _FakeCursor(one=(self._total,))
        return _FakeCursor(rows=self._rows)

    def close(self):
        self.closed = True


_IMPORT_ROW = {"id": "i1", "kind": "import", "name": "os",
               "qualified_name": "os", "file_path": "a.py", "start_line": 1}
_IMPORTED_BY_ROW = {"file_path": "b.py", "qualified_name": "os", "line": 3}


def _run(template, module_exists, rows, total, **kwargs):
    conn = _FakeConn(module_exists, rows, total)
    with mock.patch.object(q, "graph_enabled", return_value=True), \
            mock.patch.object(q._store, "get_db", return_value=conn):
        payload = q.template_query("/fake/project", template, **kwargs)
    assert conn.closed, "connection was not closed"
    return payload


class TestTemplateQueryModuleNotFound(unittest.TestCase):
    def test_imports_unknown_module_reports_module_not_found(self):
        payload = _run("imports", False, [], 0, module="no_such_module_xyz")
        self.assertIsNotNone(payload["error"], msg=payload)
        self.assertTrue(payload["error"].startswith("module_not_found"),
                        msg=payload["error"])

    def test_imported_by_unknown_module_reports_module_not_found(self):
        payload = _run("imported_by", False, [], 0, module="no_such_module_xyz")
        self.assertIsNotNone(payload["error"], msg=payload)
        self.assertTrue(payload["error"].startswith("module_not_found"),
                        msg=payload["error"])

    def test_imports_missing_module_param_is_loud_not_silent_zero(self):
        # Neither module= nor name= passed: must not answer a quiet count:0.
        payload = _run("imports", False, [], 0)
        self.assertIsNotNone(payload["error"], msg=payload)
        self.assertTrue(payload["error"].startswith("module_not_found"),
                        msg=payload["error"])

    def test_imports_known_module_behaves_as_before(self):
        payload = _run("imports", True, [_IMPORT_ROW], 1, module="a")
        self.assertIsNone(payload["error"], msg=payload)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["total_count"], 1)
        self.assertEqual(payload["results"][0]["id"], "i1")

    def test_imported_by_known_module_behaves_as_before(self):
        payload = _run("imported_by", True, [_IMPORTED_BY_ROW], 1, module="os")
        self.assertIsNone(payload["error"], msg=payload)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["file_path"], "b.py")


if __name__ == "__main__":
    unittest.main()
