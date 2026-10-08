"""P11/P14/P15/P17/P23/P25 -- silent-ok defects now fail loud (audit 2026-10-07).

Six read tools answered ok:true (or hid the real failure) on bad input:

- P25 state: a bad project_path returned top-level ok:true while sub-branches
  created junk state files. Now answers PROJECT_PATH_NOT_FOUND up front.
- P14 workflow_checkpoint: workflow_get_current's WORKFLOW_NOT_FOUND (ok:false
  JSON, never raised) was swallowed into an empty workflow_health dict with no
  warning. Now surfaced as a WORKFLOW_NOT_FOUND warning (severity error).
- P23 rule_drift_check: a typo in `checks` was silently accepted by csv_param
  coercion -- zero checks ran, drift_count:0. Now answers UNSUPPORTED_CHECK
  with the accepted list.
- P15 backup_status: a bad project_path returned ok:true + status:"missing",
  indistinguishable from "no backup yet". Now answers INVALID_PROJECT_PATH.
- P17 next_recommended_action: a dead path still returned ok:true with a
  concrete recommended action. Now rejected up front.
- P11 workflow_status_registry: a bad path triggered mkdir of junk state
  dirs/files and reported ok. Now answers INVALID_PROJECT_PATH before any
  filesystem mutation.

Run: ./venv312/Scripts/python.exe -m pytest tests/test_p11_p14_p15_p17_p23_p25_fail_loud.py -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import code_tools as ct

BAD_PATH = "D:/khong/tontai_xyz_20261008"


class StateRejectsBadPath(unittest.IsolatedAsyncioTestCase):
    """P25: state must not return top-level ok:true for a nonexistent path."""

    async def test_bad_path_returns_project_path_not_found(self) -> None:
        out = json.loads(await ct.state(project_path=BAD_PATH))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_name"], "PROJECT_PATH_NOT_FOUND")


class WorkflowCheckpointSurfacesMissingWorkflow(unittest.IsolatedAsyncioTestCase):
    """P14: a nonexistent workflow_name must surface WORKFLOW_NOT_FOUND."""

    async def test_missing_workflow_warns_loud(self) -> None:
        out = json.loads(await ct.workflow_checkpoint(
            project_path="D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-orchestrator",
            workflow_name="wf_ma_khong_ton_tai_xyz",
        ))
        kinds = [w.get("kind") for w in out.get("warnings", [])]
        self.assertIn("WORKFLOW_NOT_FOUND", kinds)
        wf_health = out.get("workflow_health") or {}
        self.assertFalse(wf_health.get("ok", True))


class RuleDriftCheckRejectsTypo(unittest.IsolatedAsyncioTestCase):
    """P23: a typo in `checks` must not be silently accepted."""

    async def test_typo_returns_unsupported_check(self) -> None:
        out = json.loads(await ct.rule_drift_check(checks="bogus_xyz_check"))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_name"], "UNSUPPORTED_CHECK")
        self.assertIn("bogus_xyz_check", out["unknown_checks"])
        self.assertIn("tool_names", out["accepted_checks"])

    async def test_valid_check_still_runs(self) -> None:
        out = json.loads(await ct.rule_drift_check(checks="tool_names"))
        self.assertTrue(out["ok"])


class BackupStatusDistinguishesBadPath(unittest.IsolatedAsyncioTestCase):
    """P15: bad path must not look like 'no backup yet'."""

    async def test_bad_path_returns_invalid_project_path(self) -> None:
        out = json.loads(await ct.backup_status(project_path=BAD_PATH))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_name"], "INVALID_PROJECT_PATH")
        self.assertNotEqual(out.get("status"), "missing")


class NextRecommendedActionRejectsDeadPath(unittest.IsolatedAsyncioTestCase):
    """P17: a dead path must not get a concrete recommendation."""

    async def test_dead_path_rejected_upfront(self) -> None:
        out = json.loads(await ct.next_recommended_action(project_path=BAD_PATH))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_name"], "INVALID_PROJECT_PATH")


class WorkflowStatusRegistryNoJunkOnBadPath(unittest.IsolatedAsyncioTestCase):
    """P11: bad path must not create junk state dirs/files."""

    async def test_bad_path_invalid_and_no_junk_created(self) -> None:
        probe = Path(tempfile.gettempdir()) / "p11_nojunk_probe_xyz_20261008"
        self.assertFalse(probe.exists())
        out = json.loads(await ct.workflow_status_registry(project_path=str(probe)))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_name"], "INVALID_PROJECT_PATH")
        self.assertFalse(probe.exists(), "junk directory was created for a bad path")
