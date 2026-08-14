"""Phase B regression: _r43_project_switch_context must reuse the thread-local
read connection instead of opening a fresh sqlite3.connect per call.

Why: live measurement (2026-08-11) showed open_session's project-switch-context
helper called `sqlite3.connect(_SESSIONS_DB)` directly. Under 20 concurrent
open_session calls, that is 20 fresh connection establishments against a
WAL-mode DB the SessionWriter is actively committing to — connection setup,
page-cache warmup, and busy-handler interaction with the writer's BEGIN
IMMEDIATE window. Reusing the thread-local read connection (query_only=ON,
busy_timeout, mmap/cache tuned) keeps this read cheap and side-effect-free.

The invariant this test protects: the helper does not call module-level
`code_tools.sqlite3.connect`; it asks the session store for the thread-local
read conn. (The session store itself may legitimately call sqlite3.connect
internally on first use per thread — that is the connection being reused,
not a per-call open.)
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import code_tools


class TestR43ProjectSwitchContextConnectionReuse(unittest.TestCase):
    def test_does_not_call_module_level_sqlite3_connect(self) -> None:
        """The helper must not invoke `sqlite3.connect` directly.

        We spy on `orchestrator.session.store.sqlite3.connect` (the attribute
        the store module itself would call) and on the helper's own
        `_session_store_module._session_read_connection`. If the helper
        regressed to a direct `sqlite3.connect(...)`, it would go through
        code_tools' own module reference, but BOTH module references share
        the same `sqlite3` module object — so the cleanest assertion is:
        (a) the helper goes through `_session_read_connection` (separate
        test), and (b) the helper body itself contains no direct
        `sqlite3.connect(` call. We assert (b) by source inspection, which
        is the same invariant the regression would violate.
        """
        import inspect
        src = inspect.getsource(code_tools._r43_project_switch_context)
        self.assertNotIn(
            "sqlite3.connect(",
            src,
            "_r43_project_switch_context source contains a direct sqlite3.connect( call; "
            "must route through _session_store_module._session_read_connection",
        )
        self.assertIn(
            "_session_read_connection(",
            src,
            "_r43_project_switch_context must call _session_read_connection",
        )

    def test_uses_session_store_read_connection(self) -> None:
        """Verify the helper asks the session store for its thread-local read conn."""
        called = {"n": 0}
        real_read_conn = code_tools._session_store_module._session_read_connection

        def spy_read_conn():
            called["n"] += 1
            return real_read_conn()

        with patch.object(
            code_tools._session_store_module, "_session_read_connection", spy_read_conn
        ):
            code_tools._r43_project_switch_context("D:/nonexistent/project")

        self.assertGreaterEqual(
            called["n"], 1,
            "_r43_project_switch_context must call _session_read_connection at least once",
        )


if __name__ == "__main__":
    unittest.main()
