"""Static guard against livez_protocol drift (Sprint 5 net, plan D5).

Census: ``rg -l --no-ignore --glob '!__pycache__' livez_protocol .`` from
the repo root (the explicit ``.`` path is mandatory: rg without a path
reads stdin when stdin is not a TTY and returns empty).

Every file in the census is classified into one of 6 classes:
predicate / producer / mention / test / docs / archive. The archive class
is PATTERN-based (path ends with ``.bak`` or lives under ``_recycle_bin/``)
and is never frozen by name: backup copies never execute, and new archive
files must not turn this guard red. All other classes are frozen in
FROZEN below, each with its reason.

The guard fails when:
(i) a census file is outside the frozen list and not archive-class
    (SUBSET semantics: extra frozen entries do not fail); or
(ii) a mention-class file matches a code-shaped predicate pattern
    (.get("livez_protocol") <cmp>, "livez_protocol"] == <digit>, or
    livez_protocol == <digit>). Prose such as ``livez_protocol>=2`` in a
    docstring matches none of the patterns by design.

Self-check at freeze time (S5, 2026-10-23): the patterns in (ii) were run
over the only mention-class file, restart_control_service.py, and
produced 0 hits, so this guard is not red on its own list from day one.
"""
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOKEN = "livez_protocol"

# path -> (class, reason). Paths use forward slashes, repo-relative.
FROZEN = {
    "orchestrator/lifecycle/livez_contract.py": (
        "predicate",
        "the single predicate home (plan D2 SSoT); the only file allowed "
        "to compare the protocol value",
    ),
    "orchestrator_server.py": (
        "producer",
        "emits LIVEZ_PROTOCOL and the /livez payload",
    ),
    "orchestrator/lifecycle/restart_control_service.py": (
        "mention",
        "R3 docstring prose only (livez_protocol>=2); the classification "
        "body delegates to livez_contract since S5",
    ),
    "docs/incident_1510/R0-R6_VERIFICATION_REPORT.md": (
        "docs",
        "incident record (historical)",
    ),
    "docs/incident_1510/R7_VERIFICATION_REPORT.md": (
        "docs",
        "incident record (historical)",
    ),
    "tests/test_watchdog_a.py": ("test", "watchdog probe contract fixtures"),
    "tests/test_watchdog_inactivity.py": ("test", "watchdog fixtures"),
    "tests/test_restart_autoheal_boot_grace_r3.py": (
        "test",
        "R3 boot-grace suite against the rcs surface",
    ),
    "tests/test_orchestrator_server_contract.py": (
        "test",
        "producer contract assertions",
    ),
    "tests/test_livez_contract.py": ("test", "S1 contract corpus tests"),
    "tests/test_a2_start_bat_contract.py": (
        "test",
        "bat contract: token presence re-pinned to livez_contract (D5(i))",
    ),
    "tests/test_as_bat_helpers.py": (
        "test",
        "helper contract: token presence re-pinned to livez_contract (D5(i))",
    ),
    "tests/test_livez_parity.py": ("test", "S5 parity net (this sprint)"),
    "tests/test_livez_future_bump.py": ("test", "S5 future-bump net (this sprint)"),
    "tests/test_livez_static_guard.py": (
        "test",
        "S5 static guard itself: carries the token in its patterns and "
        "in this frozen list",
    ),
}

PREDICATE_PATTERNS = [
    re.compile(r"""\.get\(\s*["']livez_protocol["']\s*\)\s*(==|!=|>=|<=|>|<)"""),
    re.compile(r"""["']livez_protocol["']\s*\]?\s*(==|!=)\s*\d"""),
    re.compile(r"""livez_protocol\s*==\s*\d"""),
]


def _is_archive(rel_path: str) -> bool:
    return rel_path.endswith(".bak") or "_recycle_bin/" in rel_path


def _census():
    rg = shutil.which("rg")
    assert rg, "ripgrep (rg) is required for the livez static guard census"
    proc = subprocess.run(
        [rg, "-l", "--no-ignore", "--glob", "!__pycache__", TOKEN, "."],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode in (0, 1), proc.stderr  # 1 = no matches
    found = set()
    for line in proc.stdout.splitlines():
        rel = line.strip().replace("\\", "/")
        if rel.startswith("./"):
            rel = rel[2:]
        if rel:
            found.add(rel)
    return found


def test_no_unfrozen_livez_mentions():
    found = _census()
    offenders = sorted(
        p for p in found if p not in FROZEN and not _is_archive(p)
    )
    assert not offenders, (
        "files mention livez_protocol outside the frozen classification "
        "(classify + freeze them in test_livez_static_guard.FROZEN, or "
        "remove the token): %s" % offenders
    )
    # The predicate class must stay a single home, and it must exist.
    predicate = [p for p, (cls, _r) in FROZEN.items() if cls == "predicate"]
    assert predicate == ["orchestrator/lifecycle/livez_contract.py"]
    assert "orchestrator/lifecycle/livez_contract.py" in found


def test_mention_files_carry_no_predicate():
    for rel, (cls, _reason) in FROZEN.items():
        if cls != "mention":
            continue
        text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for pattern in PREDICATE_PATTERNS:
            hit = pattern.search(text)
            assert hit is None, (
                "%s is classified mention but matches predicate pattern %r: %r"
                % (rel, pattern.pattern, hit.group(0) if hit else "")
            )
