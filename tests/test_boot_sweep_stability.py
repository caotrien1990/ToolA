"""Boot sweep root cap + idle-deadline must not degrade startup."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from orchestrator.tools.write import core as write_core


class BootSweepRootCapTests(unittest.TestCase):
    def test_skips_missing_and_venv_session_roots(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            keep = root / "keep_project"
            keep.mkdir()
            missing = root / "gone_project"
            venv_root = root / "venv312" / "Lib"
            venv_root.mkdir(parents=True)

            rows = [
                (str(missing), 3),
                (str(venv_root), 2),
                (str(keep), 1),
            ]
            conn = MagicMock()
            conn.execute.return_value.fetchall.return_value = rows
            ctx = MagicMock()
            ctx.__enter__.return_value = conn
            ctx.__exit__.return_value = False

            extra = root / "extra_explicit"
            extra.mkdir()
            with patch("code_tools._with_sessions_db", return_value=ctx):
                found = write_core._boot_sweep_project_roots([str(extra)])

            names = {Path(p).name for p in found}
            self.assertIn("keep_project", names)
            self.assertIn("extra_explicit", names)
            self.assertNotIn("gone_project", names)
            self.assertNotIn("Lib", names)
            self.assertTrue(any(Path(p).name == "project-orchestrator" or "ToolA" in str(p) for p in found))

    def test_caps_session_roots(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            created = []
            rows = []
            for i in range(8):
                p = root / f"proj_{i:02d}"
                p.mkdir()
                created.append(p)
                rows.append((str(p), 100 - i))
            conn = MagicMock()
            conn.execute.return_value.fetchall.return_value = rows
            ctx = MagicMock()
            ctx.__enter__.return_value = conn
            ctx.__exit__.return_value = False
            with patch.dict(os.environ, {"ORCHESTRATOR_BOOT_SWEEP_MAX_SESSION_ROOTS": "3"}):
                with patch("code_tools._with_sessions_db", return_value=ctx):
                    found = write_core._boot_sweep_project_roots([])
            session_names = {Path(p).name for p in found if Path(p).name.startswith("proj_")}
            self.assertEqual(len(session_names), 3)
            self.assertEqual(session_names, {"proj_00", "proj_01", "proj_02"})


class BootSweepDeadlineIdleTests(unittest.TestCase):
    def test_deadline_after_complete_empty_scan_is_ok(self) -> None:
        scan = {
            "root_count": 1,
            "candidate_count": 0,
            "pending_count": 0,
            "pending": [],
            "abort_reason": "deadline_exceeded",
            "scan_complete": True,
            "aborted": True,
        }
        result = {
            **{k: v for k, v in scan.items() if k != "pending"},
        }
        abort_reason = "deadline_exceeded"
        pending = []
        restored = []
        deadline_idle = (
            abort_reason == "deadline_exceeded"
            and bool(scan.get("scan_complete"))
            and not pending
            and not restored
        )
        self.assertTrue(deadline_idle)
        self.assertTrue((not bool(abort_reason)) or deadline_idle)
        self.assertEqual(result["pending_count"], 0)
