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
    # Runtime output and leaked pytest roots are derived state, not project source.
    # In ToolQ, /ask rewrites logs/supervisor_state_*.json after every successful
    # rebuild, making the next zero-hit search correctly report a stale INDEX.
    "logs", "%temp%toolq_tests", "pytest_tmp_lintfix",
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


def should_skip_dir(name: str, project_root=None) -> bool:
    """True when a directory named `name` must not be walked.

    `project_root` is OPTIONAL (B027). Pass it and a `.orchestrator_ignore` at that
    root can add names for this project or, with a `!` prefix, un-skip a global
    entry for this project. Omit it -- as nine pre-B027 call sites do -- and the
    answer is exactly the global rule.
    """
    lowered = name.lower()
    if lowered in DOT_DIRS_SEARCHABLE:
        # Checked BEFORE the overlay on purpose: .council is the SOT transcript
        # tree and a project must not be able to hide it from search or index.
        return False
    if project_root is not None:
        extra_skips, unskips = load_project_ignore(project_root)
        # unskip wins over skip. A `!name` has to beat BOTH the global set and the
        # leading-dot rule, otherwise `!.venv_bge_m3` -- a real case -- does
        # nothing and the escape hatch is not one.
        if lowered in unskips:
            return False
        if lowered in extra_skips:
            return True
    return lowered in SKIP_DIRS or name.startswith(".")


def ignored_directory_name(parts, project_root=None) -> "str | None":
    """Return the directory name that makes a FILE under `parts` ignored, else None.

    B027c 2026-08-22. `should_skip_dir` is the DIRECTORY gate: it decides which trees
    a walk descends. This is the FILE gate -- the same question asked about a path
    that has already been walked -- and before B027c it compared against the raw
    `SKIP_DIRS` set, so `!name` opened gate 1 and gate 2 closed again. Both gates now
    read the same overlay from one place; duplicating the precedence rules into the
    caller is exactly how they came to disagree.

    Precedence matches `should_skip_dir` where the two overlap: `.council` can never
    be hidden, then `!name` wins over any skip, then a project-declared skip, then
    the global set.

    NOT implemented as `should_skip_dir` per part on purpose: that one also skips
    every leading-dot directory, which this gate never has -- `.github/x.py` has
    always been scannable -- so reusing it verbatim would drop files that have
    nothing to do with this defect.
    """
    lowered = [str(part).lower() for part in parts]
    extra_skips: "frozenset[str]" = frozenset()
    unskips: "frozenset[str]" = frozenset()
    if project_root is not None:
        extra_skips, unskips = load_project_ignore(project_root)
    for name in lowered:
        if name in DOT_DIRS_SEARCHABLE:
            # SOT transcript tree: a project must not be able to hide it, so it is
            # checked before the overlay here for the same reason it is there.
            continue
        if name in unskips:
            continue
        if name in extra_skips:
            return name
        if name in SKIP_DIRS:
            return name
    return None


# ---------------------------------------------------------------------------
# B027 2026-08-22 (Anh/Clau): per-project ignore overlay.
#
# SKIP_DIRS above is ONE global set applied to every project [A] opens, and it
# was the only lever anybody had. That is how ToolQ's `runs`/`queue`/`archive`
# and `.venv_bge_m3`/`backups`/`staging`/`.codex`/`.pytest_tmp` ended up global:
# each was a measured, justified decision (see R82 above -- 31608 files, 591s of
# analyze, INDEX.json never written) but the cost lands on repos that never had
# those trees. The reverse case had NO answer at all: a JS repo whose real source
# lives in `build/` could not opt out, because removing `build` would un-skip it
# everywhere.
#
# The global set is unchanged. A project may now ship `.orchestrator_ignore` at
# its root with one directory name per line:
#
#     2.working          # skip this tree in THIS project only
#     1.footage/         # trailing slash tolerated
#     !build             # UN-skip a global entry, THIS project only
#     # comments and blank lines ignored
#
# Design constraints that shaped this, each learned from a prior incident:
#
#   * `should_skip_dir(name)` keeps its one-argument form. Nine call sites pass
#     only a name; changing the signature would have been a nine-site edit with
#     nine chances to get it wrong. `project_root` is OPTIONAL and omitting it
#     reproduces the previous behaviour exactly (pinned by
#     test_one_argument_calls_behave_exactly_as_before).
#   * Parsed ONCE per (root, mtime, size), not per directory. A walk calls this
#     tens of thousands of times; re-reading a file each time would spend exactly
#     the cost R82 was fixing.
#   * Keyed on the content stat rather than "seen this root", so editing the file
#     takes effect without restarting [A]. The alternative is the stale-snapshot
#     trap already documented as A03, where a change on disk is invisible until a
#     restart and the agent reads a confident wrong answer.
#   * FAILS OPEN. A malformed or unreadable file degrades to the global set. The
#     other direction -- fail shut, start skipping -- silently drops directories
#     from the index, which is the CASE-2008 shape: quiet data loss with a
#     success exit code.
#   * `.council` is NOT hideable. It is the SOT tree holding every agent's
#     transcript; an ignore file that could hide it would make "the council never
#     spoke" indistinguishable from "the council is not indexed".
# ---------------------------------------------------------------------------

PROJECT_IGNORE_FILENAME = ".orchestrator_ignore"

# (skip, unskip) keyed by (resolved_root, mtime_ns, size). Bounded below.
_IGNORE_CACHE: "dict[tuple[str, int, int], tuple[frozenset[str], frozenset[str]]]" = {}
_IGNORE_CACHE_MAX = 64


def clear_project_ignore_cache() -> None:
    """Drop the parsed-overlay cache. Used by tests and by refresh_project_map."""
    _IGNORE_CACHE.clear()


def _parse_project_ignore(text: str) -> "tuple[frozenset[str], frozenset[str]]":
    skips: set[str] = set()
    unskips: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        # Only '#' starts a comment. ';' is a legal directory name character on
        # Windows and treating it as a comment marker would silently drop an entry.
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:].strip()
        # A trailing separator is how .gitignore spells "directory", so accept it
        # rather than making a CA who typed `dist/` wonder why nothing happened.
        line = line.rstrip("/\\").strip()
        if not line:
            continue
        (unskips if negated else skips).add(line.lower())
    return frozenset(skips), frozenset(unskips)


def load_project_ignore(project_root) -> "tuple[frozenset[str], frozenset[str]]":
    """Return (skip, unskip) name sets for one project root.

    Returns two empty sets when there is no file, when it cannot be read, or when
    the root does not resolve -- see the FAILS OPEN note above.
    """
    if not project_root:
        return frozenset(), frozenset()
    from pathlib import Path as _Path
    try:
        root = _Path(project_root).resolve()
        target = root / PROJECT_IGNORE_FILENAME
        stat_result = target.stat()
    except Exception:
        return frozenset(), frozenset()

    key = (str(root).lower(), stat_result.st_mtime_ns, stat_result.st_size)
    cached = _IGNORE_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        # errors="replace" over "strict": a stray byte must not take the whole
        # overlay down, and the replacement char simply fails to match any name.
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return frozenset(), frozenset()

    parsed = _parse_project_ignore(text)
    if len(_IGNORE_CACHE) >= _IGNORE_CACHE_MAX:
        # Plain clear rather than an LRU: the cache holds at most one entry per
        # project root, 64 is far past how many roots one daemon sees, and a
        # dependency-free leaf module is worth more here than an eviction policy.
        _IGNORE_CACHE.clear()
    _IGNORE_CACHE[key] = parsed
    return parsed
