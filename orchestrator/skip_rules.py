"""Directory-walk skip rules — leaf module, imports nothing from this project.

W1 2026-08-19: `should_skip_dir` and `SKIP_DIRS` used to live in
`surgical_mapper`, which imports `orchestrator.search_snapshots` at module
level. `search_snapshots` needed `should_skip_dir` for its default walk filter,
so it did a lazy `import surgical_mapper` inside the function with a comment
saying a top-level import would cycle. That was the whole of cycle #2:
a 1299-line module and a 1457-line module bound together by one 3-line
predicate over two frozen name sets.

Both sets are plain data and the predicate is pure, so they belong at the
bottom of the dependency graph. `surgical_mapper` re-exports them, which keeps
`surgical_mapper.SKIP_DIRS is orchestrator.skip_rules.SKIP_DIRS` true and the
CAN-042 single-source contract (`test_orchestrator_server_contract.py`) intact.
"""
from __future__ import annotations

SKIP_DIRS = {
    "node_modules", "venv", "venv312", ".venv", ".git", ".svn",
    ".orchestrator_cache", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".tox", "dist", "build", ".idea", ".vscode",
    # Anh 2026-08-11: backup_project writes a "_orchestrator_backups" tree
    # (root.parent, but live repos also keep in-tree copies). It is not a
    # dotfile, so should_skip_dir did not skip it and search/index scanned the
    # whole backup set -> REGEX_SEARCH_TIMEOUT on narrow patterns. Skip it.
    "_orchestrator_backups",
    # 2026-08-13: live-data repos (e.g. ToolQ) keep large NON-code trees that bloat the
    # index and break regex/symbol search + cause INDEX/manifest scan-vs-walk drift: an
    # embedded venv, backup snapshots, staging clones, codex mission dirs, pytest temp.
    ".venv_bge_m3", "backups", "staging", ".codex", ".pytest_tmp",
    # R82 2026-08-19: runtime/archival DATA trees. Measured on ToolQ: qsleep/runs
    # (19953 files) + qsleep/queue (8289) + archive/ (13470) made generate_project_map
    # scan 31608 files of which 31079 were .json runtime records and only 367 were .py
    # source. At 18.7 ms/file that is ~591s of analyze alone — past every proxy cap, so
    # INDEX.json never got written and write_fix stayed blocked behind "INDEX not found"
    # forever. These names hold produced records, not source, on every project.
    "runs", "queue", "_archive", "archive",
}
# SOT council lives in .council — searchable despite the generic dot-dir skip.
DOT_DIRS_SEARCHABLE = {".council"}


def should_skip_dir(name: str) -> bool:
    lowered = name.lower()
    if lowered in DOT_DIRS_SEARCHABLE:
        return False
    return lowered in SKIP_DIRS or name.startswith(".")
