"""Regression test for the council_message_async `job_id` NameError.

Why: the `body_file` refactor in orchestrator/server/mcp_stdio.py dropped the
`job_id = f"mcp_{uuid.uuid4().hex[:12]}"` line but left two `json.dumps` calls
referencing `job_id`. Calling council_message_async raised
`NameError: name 'job_id' is not defined`, breaking the leader non-blocking
council dispatch path.

The test statically parses mcp_stdio.py, locates the `council_message_async`
function and its inner `_dispatch`, and asserts that `job_id` is assigned
*before* any `Name` node that loads it inside the same scope. This fails
loudly if the assignment is ever removed again.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "orchestrator" / "server" / "mcp_stdio.py"


def _find_func(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


class CouncilMessageAsyncJobIdTest(unittest.TestCase):
    def test_job_id_is_assigned_before_use(self):
        src = SRC.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(SRC))

        council_fn = _find_func(tree, "council_message_async")
        self.assertIsNotNone(council_fn, "council_message_async not found in mcp_stdio.py")

        dispatch_fn = _find_func(council_fn, "_dispatch")
        self.assertIsNotNone(dispatch_fn, "_dispatch not nested under council_message_async")

        # Walk _dispatch body in source order and track first-assign vs first-load
        # of `job_id`. The assignment must precede every load.
        first_assign_lineno: int | None = None
        first_load_lineno: int | None = None
        for node in ast.walk(dispatch_fn):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "job_id":
                        if first_assign_lineno is None or node.lineno < first_assign_lineno:
                            first_assign_lineno = node.lineno
            elif isinstance(node, ast.Name) and node.id == "job_id" and isinstance(node.ctx, ast.Load):
                if first_load_lineno is None or node.lineno < first_load_lineno:
                    first_load_lineno = node.lineno

        self.assertIsNotNone(
            first_assign_lineno,
            "job_id must be assigned inside _dispatch (was removed by the body_file refactor)",
        )
        self.assertIsNotNone(
            first_load_lineno,
            "expected at least one Load of job_id inside _dispatch",
        )
        self.assertLess(
            first_assign_lineno,
            first_load_lineno,
            f"job_id assigned at line {first_assign_lineno} but loaded earlier at line "
            f"{first_load_lineno} — assignment must precede every use",
        )

    def test_council_message_async_source_contains_uuid_job_id(self):
        """Direct smoke: the source must still mint a uuid-based job_id so
        get_toolcall_result can correlate the async reply."""
        src = SRC.read_text(encoding="utf-8")
        self.assertIn(
            'job_id = f"mcp_{_uuid.uuid4().hex[:12]}"',
            src,
            "uuid-based job_id mint line missing from mcp_stdio.py",
        )


if __name__ == "__main__":
    unittest.main()
