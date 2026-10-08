"""P24 regression: soft_data_reconcile must not silently audit the wrong root.

Bug: _load_registry did `sys.path.insert(0, project_root)` then
`import code_tools` — but when code_tools was already in sys.modules
(the running server), the import returned the server's own module no
matter what project_path was passed. The report did not echo which root
was audited, so a wrong path produced a plausible-looking report for the
wrong root.

Guards under test (scripts/soft_data_reconcile.py):
  1. build() rejects a non-directory project_path upfront
     -> PROJECT_PATH_NOT_FOUND (no fake audit runs).
  2. _load_registry refuses when the loaded code_tools.__file__ is not
     under project_root -> SystemExit("refusing to audit...").
  3. build() echoes audited_root in every report (success and error).
"""
import sys
from pathlib import Path
from types import ModuleType

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TESTS_DIR.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import soft_data_reconcile as sdr  # noqa: E402


def test_p24_build_bad_path_fails_loud():
    report = sdr.build(_PROJECT_ROOT / "khong" / "tontai_xyz")
    assert report.get("ok") is False
    assert report.get("error_name") == "PROJECT_PATH_NOT_FOUND"
    assert "not a directory" in report.get("error", "")
    # even the error echoes which root was attempted
    assert report.get("audited_root") == str(
        (_PROJECT_ROOT / "khong" / "tontai_xyz").resolve())
    # no fake "successful" report keys
    assert "summary" not in report


def test_p24_load_registry_rejects_foreign_module(monkeypatch, tmp_path):
    # Simulate the production P24 scenario: code_tools already in
    # sys.modules from a DIFFERENT root (the running server) while the
    # caller asks to audit _PROJECT_ROOT.
    fake = ModuleType("code_tools")
    fake.__file__ = str(tmp_path / "elsewhere" / "code_tools.py")
    monkeypatch.setitem(sys.modules, "code_tools", fake)
    with pytest.raises(SystemExit) as ei:
        sdr._load_registry(_PROJECT_ROOT)
    assert "refusing to audit" in str(ei.value.code)
    assert "is not under project_root" in str(ei.value.code)


def test_p24_build_good_path_echoes_audited_root():
    # End-to-end: real audit of the real root; report must name its root.
    report = sdr.build(_PROJECT_ROOT)
    assert report.get("ok", True) is True
    assert report.get("audited_root") == str(_PROJECT_ROOT.resolve())
    assert "summary" in report
    assert report["summary"]["total_tools"] > 0
