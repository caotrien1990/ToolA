"""A39 Step 4 sabotage harness (NOT a pytest test).

Mutates the A39 fix ONE LINE AT A TIME by index (never str.replace; L-o), reruns
the A39 test suite for each mutant, reports RED (caught) / GREEN (missed), then
restores the file byte-for-byte. Aimed at the round-2 seams:
  * guard staleness predicate polarity / always-true / always-false
  * _refresh_busy_snapshot freshest-reading selection (PH-1) — flipping this back
    toward "keep the stale reading" must be caught by acceptance condition #1
  * _snapshot_publisher_alive thread-liveness (PH-5): alive-check removed/inverted,
    generation-match dropped/flipped, livez typecheck removed
  * build_guard: skipping the fresh substitution, forcing the /livez probe
  * is_retryable_stale_block flag
  * CLI helper rc=4 branch

Run: ./venv312/Scripts/python.exe tests/_a39_sabotage.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "venv312" / "Scripts" / "python.exe"
TEST = "tests/test_restart_snapshot_liveness_a39.py"

GUARD = ROOT / "orchestrator" / "lifecycle" / "restart_guard.py"
SERVICE = ROOT / "orchestrator" / "lifecycle" / "restart_control_service.py"
CLI = ROOT / "orchestrator" / "lifecycle" / "request_orchestrator_restart.py"
PROTO = ROOT / "orchestrator" / "lifecycle" / "restart_protocol.py"


def find(lines: list[str], pattern: str) -> int:
    rx = re.compile(pattern)
    hits = [i for i, ln in enumerate(lines) if rx.search(ln)]
    if len(hits) != 1:
        raise SystemExit(f"anchor {pattern!r} matched {len(hits)} lines (need exactly 1)")
    return hits[0]


def indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


# (name, file, anchor_regex, replacement_builder(old_line)->new_line)
MUTANTS = [
    # --- guard staleness gate (restart_guard.py) ---
    ("guard_predicate_polarity_flip", GUARD,
     r"if snapshot_publisher_alive is not True:",
     lambda old: indent(old) + "if snapshot_publisher_alive is True:\n"),
    ("guard_never_blocks_stale", GUARD,
     r"if snapshot_publisher_alive is not True:",
     lambda old: indent(old) + "if False:\n"),
    ("guard_always_blocks_stale", GUARD,
     r"if snapshot_publisher_alive is not True:",
     lambda old: indent(old) + "if True:\n"),
    ("retryable_always_true", GUARD,
     r"return seen == \{_RETRYABLE_STALE_ONLY_REASON\}",
     lambda old: indent(old) + "return True\n"),
    ("retryable_always_false", GUARD,
     r"return seen == \{_RETRYABLE_STALE_ONLY_REASON\}",
     lambda old: indent(old) + "return False\n"),
    # --- freshest-reading selection (PH-1) ---
    ("refresh_returns_stale_first_on_advance", SERVICE,
     r"return second if isinstance\(second, Mapping\) else first",
     lambda old: indent(old) + "return first\n"),
    ("refresh_advance_predicate_flip", SERVICE,
     r"if before is None or after > before:",
     lambda old: indent(old) + "if before is None or after < before:\n"),
    # --- thread liveness via /livez (PH-5) ---
    ("liveness_alive_check_removed", SERVICE,
     r'if livez\.get\("restart_busy_snapshot_alive"\) is not True:',
     lambda old: indent(old) + "if False:\n"),
    ("liveness_alive_check_inverted", SERVICE,
     r'if livez\.get\("restart_busy_snapshot_alive"\) is not True:',
     lambda old: indent(old) + 'if livez.get("restart_busy_snapshot_alive") is True:\n'),
    ("liveness_generation_match_dropped", SERVICE,
     r"return live_generation == expected_generation",
     lambda old: indent(old) + "return True\n"),
    ("liveness_generation_match_flip", SERVICE,
     r"return live_generation == expected_generation",
     lambda old: indent(old) + "return live_generation != expected_generation\n"),
    ("livez_probe_forced_none", SERVICE,
     r"livez = _probe_livez\(livez_url\)",
     lambda old: indent(old) + "livez = {'restart_busy_snapshot_alive': True, 'generation': expected_generation}\n"),
    # --- build_guard wiring ---
    ("build_guard_skip_fresh_substitution", SERVICE,
     r"snapshot = _refresh_busy_snapshot\(snapshot_path, snapshot\)",
     lambda old: indent(old) + "pass\n"),
    ("liveness_livez_typecheck_removed", SERVICE,
     r"if not isinstance\(livez, Mapping\):",
     lambda old: indent(old) + "if False:\n"),
    # --- CLI rc=4 branch (PH-4) ---
    ("cli_rc4_becomes_rc3", CLI,
     r"            return 4",
     lambda old: indent(old) + "return 3\n"),
    ("cli_stale_predicate_always_false", CLI,
     r"if isinstance\(reasons, list\) and is_retryable_stale_block\(reasons\):",
     lambda old: indent(old) + "if False:\n"),
]


def run_tests() -> bool:
    proc = subprocess.run(
        [str(PY), "-m", "pytest", TEST, "-q", "--no-header"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    return proc.returncode == 0


def main() -> int:
    if not run_tests():
        print("BASELINE FAILED — aborting sabotage")
        return 1
    print("baseline GREEN\n")

    results: list[tuple[str, str]] = []
    for name, path, anchor, build in MUTANTS:
        original = path.read_bytes()
        lines = original.decode("utf-8").splitlines(keepends=True)
        idx = find(lines, anchor)
        mutated = lines.copy()
        mutated[idx] = build(lines[idx])
        path.write_bytes("".join(mutated).encode("utf-8"))
        try:
            passed = run_tests()
        finally:
            path.write_bytes(original)  # restore byte-for-byte
        verdict = "GREEN (MISSED)" if passed else "RED (caught)"
        results.append((name, verdict))
        print(f"{verdict:16s} {name}")

    print("\n=== SABOTAGE MATRIX ===")
    for name, verdict in results:
        print(f"  {name:38s} {verdict}")
    greens = [n for n, v in results if v.startswith("GREEN")]
    print(f"\n{len(results)-len(greens)}/{len(results)} caught; GREEN: {greens or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
