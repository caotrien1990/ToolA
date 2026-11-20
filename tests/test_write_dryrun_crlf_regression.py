"""P16 §3.1 regression (claim §4.8): dry_run must pass a CRLF .py fixture
whose code contains a backslash-continuation, and validate_file must pass the
same fixture directly.

WHY: the dry-run preview temp file was opened with NamedTemporaryFile(mode='w',
encoding='utf-8') and no newline='', so Windows text translation turned every
'\n' into '\r\n' ON TOP of the fixture's existing CRLF ('\r\r\n'). py_compile
then reported a phantom SyntaxError at 2x the continuation line (C-2/C-5).
The fix adds newline='' at the three preview call sites so the bytes handed to
the checker equal the proposed bytes (claim §4.4; LF files stay LF, §4.5).
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import code_tools

# 2-line CRLF fixture, backslash-continuation IN CODE (line 1).
FIXTURE = b"x = 1 + \\\r\n    2\r\n"


def _run(coro):
    return json.loads(asyncio.run(coro))


def test_dryrun_passes_crlf_continuation(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    target = root / "cont.py"
    target.write_bytes(FIXTURE)

    # No-op dry_run: line 1 re-sent with itself (LF payload; _build normalizes
    # to the file's dominant CRLF). Pre-fix this false-failed at line 2 = 2x1.
    dry = _run(code_tools.write_fix(str(target), 1, 1, "x = 1 + \\\n",
                                    project_path=str(root), dry_run=True))
    assert dry.get("ok") is True, dry
    assert dry.get("dry_run") is True, dry
    val = dry.get("validation") or {}
    assert val.get("ok") is True, dry
    # Side-effect free: original CRLF bytes untouched.
    assert target.read_bytes() == FIXTURE

    # Same fixture through validate_file directly (real bytes, no temp preview).
    direct = _run(code_tools.validate_file(str(target)))
    assert direct.get("ok") is True, direct
