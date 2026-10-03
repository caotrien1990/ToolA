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

import fnmatch
import functools

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
    # Runtime output is derived state, not project source. In ToolQ, /ask rewrites
    # logs/supervisor_state_*.json after every successful rebuild, making the next
    # zero-hit search correctly report a stale INDEX.
    #
    # B027j 2026-09-22: `%temp%toolq_tests` and `pytest_tmp_lintfix` were retired
    # from this line. They were ONE project's directory names carried as every
    # project's global cost -- the D2 defect itself -- and the `%temp%*` /
    # `pytest_tmp*` families in SKIP_DIR_GLOBS cover them for every project.
    "logs",
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


# ---------------------------------------------------------------------------
# One answer to "is this file a write backup?" (2026-09-24).
#
# write_fix and its siblings leave <file>.bak next to every file they touch --
# the auto-revert source. Five producers spelled that name inline and three
# consumers re-asked the question with their own `endswith(".bak")`: the D4
# retention sweep, cep_deploy's curated set and analysis_view's production
# filter. manage_agent sync_skill asked nothing at all, so its `copytree` shipped
# `.ToolA_Skill/skills/<name>/SKILL.md.bak` into every agent's skill folder. The
# producers build the name from `write_backup_path` and every consumer asks
# `is_write_backup`, so the two can only move together.
# ---------------------------------------------------------------------------
WRITE_BACKUP_SUFFIX = ".bak"


def write_backup_path(fp):
    """The sibling backup a write leaves for ``fp`` (a ``Path``): ``<name>.bak``."""
    return fp.with_suffix(fp.suffix + WRITE_BACKUP_SUFFIX)


def is_write_backup(name: str) -> bool:
    """True when a file NAME is a write backup: a rollback copy, never content."""
    return str(name).endswith(WRITE_BACKUP_SUFFIX)

# ---------------------------------------------------------------------------
# B027i / D2 2026-09-22: the GLOBAL default layer gets glob patterns too.
#
# B027h gave the per-project overlay globs and left the global set above
# exact-membership-only in all three gates. That asymmetry IS the D2 defect:
# [A]'s own defaults can name an instance of a junk tree but never the FAMILY it
# belongs to. Two consequences, both still readable in this file and the next:
#
#   * ToolQ-specific instance names had to be written into SKIP_DIRS above --
#     `%temp%toolq_tests`, `pytest_tmp_lintfix` -- because the families they
#     belong to (`%temp%*`, `pytest_tmp*`) were unsayable. One project's
#     directory names became every project's cost, the thing B027 exists to stop.
#   * `graph/analysis_view.py` grew a SECOND private authority on the same
#     question, carrying `_backup`, `_iso_campaign/` and `%temp%` under the
#     comment "(indexer SKIP_DIRS does not know them)". It existed only because
#     this layer could not express them; D2 retires it.
#
# Measured on ToolQ 2026-09-22, which ships NO `.orchestrator_ignore` at all:
# `search_code` still answered out of `_iso_campaign/fix277/*.py` (304 .py files
# in that one tree), so every filter B027/B027h built was inert there. An overlay
# only helps a project that ships one; a default helps every project.
#
# The bar for adding a pattern here is HIGHER than for an overlay entry, because
# widening a global skip drops directories from the index under a success exit
# code -- the CASE-2008 shape. Each entry below is a derived-state family whose
# name cannot plausibly be project source. ToolQ's dated `backup_*t*z` tree is
# deliberately NOT here: measured, it holds 0 .py files, so it costs the index
# nothing and stays an overlay concern. A project that disagrees with any pattern
# writes `!name` in its overlay -- un-skip is checked FIRST in every gate, so it
# beats this layer exactly as it already beats SKIP_DIRS and the dot rule.
#
# This is an ADDITIONAL layer, not a replacement: the exact set above stays the
# fast path and is still tested first in every gate.
#
# B027j 2026-09-22 closed the condition this block used to record. Four consumers
# read SKIP_DIRS directly and tested it with a raw `in` -- the `code_tools`
# backup-zip walk, size walk and py_compile rglob fallback, plus the
# `orchestrator_server` magika walk -- so none of them saw an overlay OR a glob,
# and the two ToolQ instance names had to STAY in the exact set to keep those four
# walks correct. All four now go through a gate (`effective_skip_dir_names` +
# `name_matches_ignore`, `ignored_directory_name`, `should_skip_dir`), so
# `%temp%toolq_tests` and `pytest_tmp_lintfix` are retired from SKIP_DIRS above and
# covered by `%temp%*` / `pytest_tmp*` below.
#
# A NEW consumer must call a gate. Reading SKIP_DIRS raw is what let one defect
# ship three times (B027c, B027d, B027i/D2): every walk that re-derives the
# precedence locally is a walk that drifts, and the symptom is always two answers
# that disagree with `ok:true` on both sides.
# ---------------------------------------------------------------------------
SKIP_DIR_GLOBS = frozenset({
    # Isolation-campaign work trees: full source COPIES, so every symbol inside is
    # a duplicate hit on the real file. 304 .py under ToolQ's `_iso_campaign`.
    # Narrower than `_iso_*` on purpose -- that would reach a name like
    # `_iso_utils`, which could well be source.
    "_iso_campaign*",
    # `_backup_stabilize_*` and its siblings: a fresh name per run, so listing
    # them was never possible. Same coverage as the `_backup` prefix retired from
    # `analysis_view`, which is what lets that band-aid go.
    "_backup*",
    # A pytest temp root written with `%TEMP%` left unexpanded. A literal `%`
    # cannot begin a real source directory, so this family is unambiguous.
    "%temp%*",
    # Leaked pytest temp roots (`pytest_tmp_lintfix`, ...). `.pytest_tmp` keeps
    # its exact entry above: a leading dot is not matched by this pattern.
    "pytest_tmp*",
})


# ---------------------------------------------------------------------------
# B027h 2026-09-22: glob patterns in `.orchestrator_ignore`.
#
# The overlay could only ever name a directory EXACTLY -- the parser stored a
# lowercased literal and all three gates asked `name in patterns` -- so the most
# common junk shape was unsayable: a dated backup tree. Measured on ToolQ,
# `q_agent/state/backup_prebaseurl_20260804T032924Z` and
# `qsleep/ledger/backup_modelpath_20260804T033537Z` sit under two different
# parents, plus the `_backup_stabilize_*` family. Every one of those names is
# fresh on every run, so a project could never keep up by listing them, and the
# only lever left was writing them into the GLOBAL SKIP_DIRS -- exactly what B027
# exists to stop. The B027g note further down already writes that tree as the glob
# `_backup_*T*Z`, so the design intended globs; only the matcher was missing.
#
# Matching lives in ONE function that all three gates call, for the reason B027c
# records below: a precedence each gate re-derives is a precedence that drifts.
#
# NON-WIDENING BY CONSTRUCTION, which is a stronger promise than "exact membership
# is tested first". `_GLOB_META` holds only `*` and `?`, the two characters Windows
# FORBIDS in a file or directory name. A pattern carrying one of them therefore
# cannot be the literal name of anything on disk, so reading it as a glob can never
# shadow a real directory -- and an overlay with no metacharacter at all is still
# resolved purely by set membership, byte-for-byte as before B027h.
#
# `[` is deliberately NOT in that set even though fnmatch honours it. `[` is LEGAL
# in a Windows name, so it is ambiguous, and the ambiguity resolves the wrong way:
# with `[` classified as a metacharacter the entry `foo[1]` ALSO skipped `foo1` --
# measured 2026-09-22, it failed test_a_bracket_in_a_name_is_still_a_name. Skipping
# MORE than the project declared is the fail-shut direction the FAILS OPEN note
# below rejects: a directory dropped from the index under a success exit code, the
# CASE-2008 shape. A project that genuinely wants a character class writes one next
# to a `*` (`backup_[0-9]*`), which is unambiguous because of the `*`.
# ---------------------------------------------------------------------------
_GLOB_META = ("*", "?")


def _pattern_has_glob(pattern: str) -> bool:
    return any(ch in pattern for ch in _GLOB_META)


@functools.lru_cache(maxsize=256)
def _glob_patterns(patterns: "frozenset[str]") -> "tuple[str, ...]":
    """The glob-bearing subset of one overlay set, computed once per set.

    A walk asks the gates tens of thousands of times; re-filtering the set on every
    question would spend exactly the cost the (root, mtime, size) parse cache exists
    to avoid. frozensets are hashable and `load_project_ignore` hands back the same
    cached object every time, so this hits.
    """
    return tuple(sorted(p for p in patterns if _pattern_has_glob(p)))


def name_matches_ignore(lowered_name: str, patterns) -> bool:
    """True when one lowercased directory name is covered by one overlay set.

    THE matcher for all three gates below. `fnmatchcase`, not `fnmatch`: both sides
    are already lowercased by the parser, and `fnmatch` would additionally run
    os.path.normcase, which on Windows also rewrites a forward slash into a
    backslash -- making this predicate answer differently per platform for no gain.
    """
    if not patterns:
        return False
    if lowered_name in patterns:
        return True
    if not isinstance(patterns, frozenset):
        patterns = frozenset(patterns)
    for pattern in _glob_patterns(patterns):
        if fnmatch.fnmatchcase(lowered_name, pattern):
            return True
    return False


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
        # nothing and the escape hatch is not one. B027h: a `!` line globs too, so
        # an un-skip can still answer a skip pattern of the same shape.
        if name_matches_ignore(lowered, unskips):
            return False
        if name_matches_ignore(lowered, extra_skips):
            return True
    if lowered in SKIP_DIRS or name_matches_ignore(lowered, SKIP_DIR_GLOBS):
        return True
    return name.startswith(".")


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
        if name_matches_ignore(name, unskips):
            continue
        if name_matches_ignore(name, extra_skips):
            return name
        if name in SKIP_DIRS or name_matches_ignore(name, SKIP_DIR_GLOBS):
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
#     _backup_*t*z       # B027h: glob. Patterns are lowercased, so is the name
#     !build             # UN-skip a global entry, THIS project only
#     !_iso_*            # a `!` line globs too; un-skip still wins
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
# P14 D1: resolve() per load_project_ignore call was one _getfinalpathname
# syscall per SEARCHED RECORD (3900/search on CUA). The root string -> resolved
# path mapping is process-stable (every other graph/snapshot key already keys
# on the raw string), so memoize it; a re-targeted junction mid-process is the
# accepted cost, bounded like _IGNORE_CACHE.
_RESOLVED_ROOT_CACHE: dict = {}


def _resolved_root(project_root):
    from pathlib import Path as _Path
    key = str(project_root)
    hit = _RESOLVED_ROOT_CACHE.get(key)
    if hit is not None:
        return hit
    resolved = _Path(project_root).resolve()
    if len(_RESOLVED_ROOT_CACHE) >= _IGNORE_CACHE_MAX:
        _RESOLVED_ROOT_CACHE.clear()
    _RESOLVED_ROOT_CACHE[key] = resolved
    return resolved


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
        root = _resolved_root(project_root)
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


def effective_skip_dir_names(project_root=None) -> "frozenset[str]":
    """The resolved skip-name SET for one project, same precedence as `should_skip_dir`.

    B027g 2026-09-22. For a caller that needs the NAMES rather than a yes/no per
    directory: a subprocess scanner handed an `--exclude` list does its own walk
    and can never call `should_skip_dir` at all. `vulture_ruff` was exactly that
    caller and imported the raw global `SKIP_DIRS`, so `.orchestrator_ignore` --
    the whole point of B027 -- reached the directory gate and the file gate but
    never the dead-code advisory. Measured on ToolQ: 218 findings from
    `.TENCENTDB_MEMORY` + 174 from `_iso_campaign` + 28 from `_backup_*T*Z` = 73%
    of 577, in three trees that project already declares are not its source, and
    the only lever was writing one project's directory names into this global set.

    Precedence lives HERE rather than in the caller, for the reason B027c already
    records above: two gates that each re-derive it are two gates that drift.

    The leading-dot rule in `should_skip_dir` is deliberately NOT folded in -- it
    is a predicate over unbounded names, not a name set, so a caller that needs
    it has to keep asking per directory.
    """
    # B027i: the global GLOB layer travels as PATTERNS here for the same reason the
    # overlay globs do -- the one consumer of this set feeds each entry to an
    # fnmatch-based subprocess `--exclude`, so `_iso_campaign*` arrives as a working
    # exclusion. A caller that needs exact names only must ask per directory.
    names = set(SKIP_DIRS) | set(SKIP_DIR_GLOBS)
    if project_root is not None:
        extra_skips, unskips = load_project_ignore(project_root)
        # B027h: a glob entry is carried through as a PATTERN, not a name. The one
        # consumer of this set feeds each entry to a subprocess --exclude that is
        # itself fnmatch-based (`vulture_ruff._vulture_exclude` wraps every entry as
        # `*/entry/*` and `*\entry\*`), so `_backup_*t*z` arrives as a working
        # exclusion. A caller that needs exact names only must ask per directory.
        names |= set(extra_skips)
        # Not `names -= set(unskips)`: exact subtraction cannot express
        # `!_backup_*t*z`, and it could never remove a glob-matched GLOBAL name
        # either. The shared matcher keeps this gate's precedence identical to the
        # other two instead of re-deriving it here.
        names = {n for n in names if not name_matches_ignore(n, unskips)}
    # .council is not hideable here either: a project able to exclude the SOT
    # transcript tree would make "the council never spoke" indistinguishable
    # from "the council is not scanned".
    return frozenset(names - DOT_DIRS_SEARCHABLE)
