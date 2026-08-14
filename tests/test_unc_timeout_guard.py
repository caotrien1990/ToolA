"""UNC auto-inference timeout guard (V15).

Dead/unreachable SMB shares make Path.resolve()/exists/is_dir/stat block for
30-60s. These tests assert that auto-root/session inference NEVER probes a
UNC-like path — using a monkeypatched Path.resolve sentinel that fails loudly
if a UNC string ever reaches it. Intentional UNC capability (absolute file_path
without project_path) is preserved.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import code_tools as ct  # noqa: E402
import path_runtime as pr  # noqa: E402
from pathlib import Path  # noqa: E402

UNC = "\\\\deadserver\\share\\proj\\file.py"          # \\deadserver\share\proj\file.py
UNC_FWD = "//deadserver/share/proj"
UNC_ROOT = "\\\\deadserver\\share\\proj"


@pytest.fixture
def no_unc_resolve(monkeypatch):
    """Fail loudly if any UNC-like path reaches Path.resolve()."""
    orig = Path.resolve

    def guarded(self, *a, **k):
        s = str(self)
        if s.startswith("\\\\") or s.startswith("//"):
            raise AssertionError("UNC path reached Path.resolve(): " + s)
        return orig(self, *a, **k)

    monkeypatch.setattr(Path, "resolve", guarded)


def test_is_unc_path_like():
    assert ct._is_unc_path_like(UNC)
    assert ct._is_unc_path_like(UNC_FWD)
    assert ct._is_unc_path_like("  \\\\srv\\x  ")  # leading/trailing space tolerated
    assert not ct._is_unc_path_like("C:/local/proj")
    assert not ct._is_unc_path_like("relative/path.py")
    assert not ct._is_unc_path_like("")
    assert not ct._is_unc_path_like(None)


def test_find_project_root_unc_returns_none_without_probe(no_unc_resolve):
    assert ct.find_project_root(UNC) is None


def test_resolve_project_path_payload_unc_rejected(no_unc_resolve):
    r = ct._resolve_project_path_payload(UNC)
    assert r["ok"] is False
    assert r["needs_user_confirmation"] is True
    assert r["error_name"] == "UNC_PROJECT_ROOT_UNSUPPORTED"
    assert r["ca_next_action"] == "use_check_server_path_for_unc_or_choose_local_project_root"

    # UNC preferred_root with a local input also rejected
    r2 = ct._resolve_project_path_payload("file.py", preferred_root=UNC_ROOT)
    assert r2["error_name"] == "UNC_PROJECT_ROOT_UNSUPPORTED"


def test_session_project_key_unc_raw_no_probe(no_unc_resolve):
    assert ct._session_project_key(UNC) == UNC.strip()


def test_resolve_project_file_intentional_unc_kept(no_unc_resolve):
    # Absolute UNC file_path WITHOUT project_path = intentional capability, kept as-is.
    got = ct._resolve_project_file(None, UNC)
    assert str(got) == UNC  # NOT resolved


def test_resolve_project_file_unc_project_path_rejected(no_unc_resolve):
    with pytest.raises(ValueError):
        ct._resolve_project_file(UNC_ROOT, "a.py")


def test_resolve_project_file_unc_file_with_project_path_rejected(no_unc_resolve):
    with pytest.raises(ValueError):
        ct._resolve_project_file("C:/local/proj", UNC)


def test_project_cache_dir_unc_rejected(no_unc_resolve):
    with pytest.raises(ValueError):
        ct._project_cache_dir(UNC_ROOT)


def test_find_orchestrator_dir_unc_returns_none(no_unc_resolve):
    assert pr.find_orchestrator_dir(UNC) is None
    assert pr.find_orchestrator_dir(UNC_FWD) is None
