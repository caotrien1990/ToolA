"""P-51: teardown closes durable audit before deleting its run-owned directory."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHILD = "P51_AUDIT_TEARDOWN_CHILD"


@pytest.mark.skipif(not os.environ.get(CHILD), reason="subprocess-only teardown probe")
def test_child_keeps_audit_handle_pinned_until_shutdown():
    import orchestrator_server as server
    from orchestrator.server.audit_writer import DurableAuditWriter
    # Deterministic version of the measured race: default idle drop is 2s,
    # conftest cleanup retries total only 1.55s. Make idle drop impossible in
    # that window; explicit shutdown must release the handle instead.
    DurableAuditWriter._IDLE_WAIT_CAP_S = 60
    writer = server._get_durable_audit_writer(start=True)
    deadline = time.monotonic() + 5
    while writer._worker_db is None and time.monotonic() < deadline:
        time.sleep(.01)
    assert writer._worker_db is not None
    if os.environ[CHILD] == "leak":
        import tempfile
        root = Path(tempfile.gettempdir())
        (root / "p51_foreign_file.txt").write_text("keep me", encoding="utf-8")
        (root / "p51_intentional_leak").mkdir()
    # Do not shut down here: conftest owns and must close the global server writer.


@pytest.mark.skipif(bool(os.environ.get(CHILD)), reason="parent-only")
@pytest.mark.parametrize("leak", [False, True])
def test_cleanup_releases_owned_audit_but_preserves_real_leaks(tmp_path, leak):
    env = dict(os.environ, TEMP=str(tmp_path), TMP=str(tmp_path), TMPDIR=str(tmp_path),
               PYTHONIOENCODING="utf-8", ORCHESTRATOR_PARALLEL_INDEX="off")
    env[CHILD] = "leak" if leak else "normal"
    for name in ("ORCHESTRATOR_SESSIONS_DB", "ORCHESTRATOR_GENERATION_FILE",
                 "ORCHESTRATOR_LOG_FILE", "ORCHESTRATOR_ACCESS_LOG_FILE",
                 "ORCHESTRATOR_USAGE_LOG_FILE", "ORCHESTRATOR_COUNCIL_USAGE_LOG_FILE",
                 "ORCHESTRATOR_DURABLE_AUDIT_DB", "ORCHESTRATOR_WRITE_BACKUP_LEDGER_DIR"):
        env.pop(name, None)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly",
         str(Path(__file__).resolve()) + "::test_child_keeps_audit_handle_pinned_until_shutdown"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=45, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    leftovers = sorted(p.name for p in tmp_path.iterdir() if not p.name.startswith("pytest-of-"))
    assert leftovers == (["p51_foreign_file.txt", "p51_intentional_leak"] if leak else [])
