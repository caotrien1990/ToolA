"""Vulture + Ruff scanners for Python project health (dead-code + maintenance).

The public scan functions are fail-open: scanner failures warn and return an
empty list so cbm_dead_code can keep serving advisory results.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import warnings
from pathlib import Path

from orchestrator.skip_rules import effective_skip_dir_names

# B029c 2026-08-22. Was 30s, against a measured 56s for vulture on ToolA full
# scope -- so every full run timed out, and (before B029b) said nothing. The
# budget is generous rather than tight because the failure mode of "too short" is
# a silent empty result, while "too long" only costs latency on an already-slow
# advisory tool. Overridable so a caller on a huge tree is not stuck with ours.
#
# 2026-09-22: that 56s was the SYMPTOM of the defect below, and raising the
# ceiling only hid it -- until the 25s server budget (CALL_BUDGET_S) made 180s
# unreachable and cbm_dead_code(scope="full") began failing outright.
_SCAN_TIMEOUT_S = float(os.environ.get("CBM_SCAN_TIMEOUT_S", "180"))

# Vulture has NO built-in exclude, so pointing it at a project root walks the
# virtualenv as if it were source: measured 2026-09-22 on ToolA, 66.1s and 1001
# of 2004 findings inside venv312/, against 727ms for orchestrator/ alone. Ruff
# needs no such list -- its own default exclude already drops site-packages,
# which is why only this scanner blew the budget. The skip-name SOT is
# orchestrator/skip_rules.py, so the dead-code advisory now agrees with the
# INDEX instead of contradicting it.
#
# DIRECTORY-ANCHORED on purpose, and both separators are listed:
#   * vulture wraps a bare pattern into *pattern* (core.py:268-271), so the bare
#     SKIP_DIRS entry "dist" also matches orchestrator/distill_points.py and
#     "build" matches build_graph.py -- measured, both silently hidden.
#   * the match is fnmatch, which normcases "/" to "\\" on Windows and leaves it
#     alone on POSIX, so neither separator alone covers both platforms.
# Residual cost is vulture's own unpruned os.walk (it enumerates, then filters):
# full scope lands near 6s, not at the 727ms of a tree with no venv in it.


def _vulture_exclude(project_root=None) -> str:
    """Build `--exclude` for ONE project from its resolved skip-name set.

    B027g 2026-09-22: a FUNCTION, not the module constant it replaces. That
    constant froze the GLOBAL skip set at import time, so `.orchestrator_ignore`
    -- the per-project overlay B027 built, honoured by both the directory gate
    and the file gate -- never reached this scanner. Measured on ToolQ: 218
    findings from `.TENCENTDB_MEMORY` + 174 from `_iso_campaign` + 28 from
    `_backup_*T*Z` = 73% of 577, in three trees that project already declares are
    not its source, and the only lever was writing ToolQ's directory names into
    ToolA's global set.

    Resolved per call on purpose: `load_project_ignore` caches on (root, mtime,
    size), so editing the ignore file takes effect without restarting [A].
    """
    return ",".join(
        pattern
        for name in sorted(effective_skip_dir_names(project_root))
        for pattern in (f"*/{name}/*", f"*\\{name}\\*")
    )

# B029a 2026-08-22. The daemon inherits FORCE_COLOR from whatever launched it
# (measured =3), so ruff painted ANSI even into a pipe and `_RUFF_RE` matched
# none of 5803 lines. `--color never` handles ruff; these are dropped as well
# because NO_COLOR does not beat FORCE_COLOR and other tools read them too.
_COLOUR_ENV_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE", "CLICOLOR", "COLORTERM")

# Strip ANSI SGR sequences before parsing. Belt and braces next to --color never:
# a flag only helps the process we spawn ourselves, and returning zero findings
# because of an escape code is not a failure any caller can diagnose.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _scan_env() -> dict:
    """The parent environment minus anything that forces coloured output."""
    env = dict(os.environ)
    for name in _COLOUR_ENV_VARS:
        env.pop(name, None)
    env["NO_COLOR"] = "1"
    return env

_VULTURE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):\s+"
    r"(?P<message>.*?)\s+\((?P<confidence>\d+)% confidence\)\s*$",
)
_RUFF_RE = re.compile(
    r"^(?P<file>.+):(?P<line>\d+):(?P<col>\d+):\s+"
    r"(?P<rule>[A-Z]+\d+)\s+(?P<body>.+?)\s*$",
)

_RULE_CATEGORIES: dict[str, list[str]] = {
    "dead_code": [
        "F401", "F811", "F841", "F842",
        "ARG001", "ARG002", "ARG003", "ARG004", "ARG005",
        "B007", "ERA001",
        "PLR1711", "PLW0127", "F541", "PLR6301", "PLW0108",
    ],
    "bug_risk": [
        "B002", "B003", "B004", "B005", "B006", "B008", "B009",
        "B010", "B011", "B012", "B013", "B014", "B015", "B016",
        "B017", "B018", "B019", "B020", "B021", "B022", "B023",
        "B024", "B025", "B026", "B027", "B028", "B029", "B030",
        "B031", "B032", "B033", "B034", "B035", "B039", "B043",
        "B901", "B903", "B904", "B905", "B909", "B911", "B912",
        "PLE0100", "PLE0101", "PLE0115", "PLE0116", "PLE0117",
        "PLE0118", "PLE0643", "PLE1132", "PLE1141", "PLE1142",
        "PLE4703",
        "RUF006", "RUF008", "RUF012", "RUF015", "RUF018", "RUF100",
        "PLW0129",
    ],
    "perf": [
        "PERF101", "PERF102", "PERF203",
        "PERF401", "PERF402", "PERF403",
    ],
    "complexity": [
        "C901",
        "PLR0911", "PLR0912", "PLR0913", "PLR0914", "PLR0915",
        "PLR0916", "PLR0917", "PLR1702",
    ],
    "code_smell": [
        "SIM", "PIE", "RET", "RSE", "COM",
        "PLR1704", "PLW0120", "PLW0128",
        "PLR0124", "PLR0133", "PLR1730", "PLR1733", "PLR1736",
    ],
    "tech_debt": [
        "FIX", "TD", "T100", "T201", "T203",
    ],
    "security": [
        "S", "PGH001", "PGH003", "PGH004",
    ],
}

_RULE_TO_CATEGORY: dict[str, str] = {}
for _cat, _rules in _RULE_CATEGORIES.items():
    for _r in _rules:
        _RULE_TO_CATEGORY[_r] = _cat

_ALL_SCAN_RULES: str = ",".join(
    rule for rules in _RULE_CATEGORIES.values() for rule in rules
)


def _normalize_file(path: str) -> str:
    return path.replace("\\", "/")


def _ruff_category(rule: str) -> str:
    if rule in _RULE_TO_CATEGORY:
        return _RULE_TO_CATEGORY[rule]
    prefix = rule.rstrip("0123456789")
    if prefix in _RULE_TO_CATEGORY:
        return _RULE_TO_CATEGORY[prefix]
    return "lint"


def _ruff_kind(rule: str) -> str:
    return {
        "F401": "unused import",
        "F811": "redefinition",
        "F841": "unused variable",
        "F842": "unused annotation",
        "ARG001": "unused argument",
        "ARG002": "unused method argument",
        "ARG003": "unused classmethod argument",
        "ARG004": "unused staticmethod argument",
        "ARG005": "unused lambda argument",
        "B007": "unused loop variable",
        "ERA001": "commented-out code",
        "PLR1711": "useless return",
        "PLW0127": "self-assigning variable",
        "F541": "useless f-string",
        "PLR6301": "no self use",
        "PLW0108": "unnecessary lambda",
    }.get(rule, _ruff_category(rule).replace("_", " "))


def _symbol_from_ruff(rule: str, body: str) -> str:
    quoted = re.search(r"`([^`]+)`", body)
    if quoted:
        return quoted.group(1)
    if rule == "F401":
        match = re.search(r"`?([^`\s]+)`?\s+imported but unused", body)
        if match:
            return match.group(1)
    if rule == "F811":
        match = re.search(r"redefinition of unused [`']?([^`'\s]+)", body)
        if match:
            return match.group(1)
    if rule == "F841":
        match = re.search(r"local variable [`']?([^`'\s]+)[`']?\s+is assigned", body)
        if match:
            return match.group(1)
    if rule.startswith("ARG"):
        match = re.search(r"Unused \w+ argument: [`']?([^`'\s]+)", body)
        if match:
            return match.group(1)
    if rule == "B007":
        match = re.search(r"Loop control variable [`']?([^`'\s]+)", body)
        if match:
            return match.group(1)
    if rule == "ERA001":
        return "<commented-code>"
    if rule == "F541":
        return "<no-placeholders>"
    if rule in ("PLR1711", "PLW0127"):
        return "<self>"
    if rule == "PLR6301":
        return "<no-self-use>"
    if rule == "PLW0108":
        match = re.search(r"Lambda expression called directly", body)
        if match:
            return "<lambda>"
        return "<unnecessary-lambda>"
    return "<" + rule + ">"


def _symbol_from_vulture(message: str, kind: str) -> str:
    quoted = re.search(r"'([^']+)'", message)
    if quoted:
        return quoted.group(1)
    if kind == "unreachable code":
        return "<unreachable>"
    if kind == "unsatisfiable if":
        return "<condition>"
    return "<block>"


def _vulture_kind(message: str) -> str:
    if message.startswith("unreachable code"):
        return "unreachable code"
    if message.startswith("unsatisfiable"):
        return "unsatisfiable if"
    match = re.match(r"(unused\s+\w+)", message)
    if match:
        return match.group(1)
    return message.split("'", 1)[0].strip() or "dead code"


def _parse_vulture_output(output: str, min_conf: int = 60) -> list[dict]:
    candidates: list[dict] = []
    for line in output.splitlines():
        match = _VULTURE_RE.match(line.strip())
        if not match:
            continue
        confidence_pct = int(match.group("confidence"))
        if confidence_pct < min_conf:
            continue
        message = match.group("message").strip()
        kind = _vulture_kind(message)
        candidates.append({
            "symbol": _symbol_from_vulture(message, kind),
            "name": _symbol_from_vulture(message, kind),
            "file": _normalize_file(match.group("file")),
            "file_path": _normalize_file(match.group("file")),
            "line": int(match.group("line")),
            "start_line": int(match.group("line")),
            "kind": kind,
            "category": "dead_code",
            "confidence": confidence_pct / 100.0,
            "source": "vulture",
            "reason": message,
        })
    return candidates


def _parse_ruff_output(output: str) -> list[dict]:
    candidates: list[dict] = []
    for line in output.splitlines():
        # B029a: see _ANSI_RE.
        match = _RUFF_RE.match(_ANSI_RE.sub("", line).strip())
        if not match:
            continue
        rule = match.group("rule")
        body = match.group("body").strip()
        fix_available = body.startswith("[*]")
        if fix_available:
            body = body[3:].lstrip()
        category = _ruff_category(rule)
        symbol = _symbol_from_ruff(rule, body)
        kind = _ruff_kind(rule)
        candidates.append({
            "symbol": symbol,
            "name": symbol,
            "file": _normalize_file(match.group("file")),
            "file_path": _normalize_file(match.group("file")),
            "line": int(match.group("line")),
            "start_line": int(match.group("line")),
            "kind": kind,
            "category": category,
            "confidence": 1.0,
            "source": "ruff",
            "rule": rule,
            "reason": body,
            "fix_available": fix_available,
            "fix_tool": "ruff_fix" if fix_available else None,
        })
    return candidates


def _scanner_warning(scanner: str, exc: BaseException | str) -> None:
    warnings.warn(f"{scanner} scan failed: {exc}", RuntimeWarning, stacklevel=2)


def _record(status: "dict | None", scanner: str, reason: "str | None") -> None:
    """Write a scanner's outcome into the caller's dict, if it asked for one.

    B029b 2026-08-22. `warnings.warn` was the ONLY failure channel, and nothing
    it emits survives the trip to an HTTP caller: `cbm_dead_code` kept answering
    `engine="vulture+ruff"`, `error=None`, `count=0` while vulture had in fact
    timed out on every single run. Same class as B028b -- a payload that
    misdescribes itself is a defect even when the analysis it did run is correct.

    `status` stays optional so every existing call site keeps working unchanged;
    a caller that wants the truth opts in by passing a dict.
    """
    if status is None:
        return
    status.clear()
    status.update({"scanner": scanner, "ok": reason is None, "reason": reason})


def _console_python() -> str:
    """Resolve python.exe (CONSOLE subsystem) from sys.executable.

    When [A] runs via pythonw.exe (GUI, no console), sys.executable points to
    pythonw.exe.  Subprocesses that spawn console binaries (e.g. ruff.exe)
    cannot inherit stdout from a GUI parent.  Resolve python.exe from the
    same directory to ensure pipe capture works.
    """
    exe = Path(sys.executable)
    console = exe.with_name("python.exe")
    return str(console) if console.is_file() else sys.executable


def scan_vulture(project_path: str, min_conf: int = 60,
                 files: list[str] | None = None,
                 status: "dict | None" = None) -> list[dict]:
    """Vulture dead-code scan. Fail-open: returns [] and reports via `status`."""
    targets = [str(Path(p)) for p in files] if files else [str(Path(project_path))]
    # `--exclude` only when vulture is handed a TREE to walk. An explicit `files`
    # list is the caller's own choice of targets, so filtering it here would drop
    # files that were asked for by name and report nothing about the omission.
    exclude = [] if files else ["--exclude", _vulture_exclude(project_path)]
    try:
        completed = subprocess.run(
            [
                _console_python(),
                "-m",
                "vulture",
                *targets,
                "--min-confidence",
                str(int(min_conf)),
                *exclude,
            ],
            capture_output=True,
            text=True,
            timeout=_SCAN_TIMEOUT_S,
            check=False,
            env=_scan_env(),
        )
    except subprocess.TimeoutExpired as exc:
        reason = f"timeout after {_SCAN_TIMEOUT_S:g}s"
        _scanner_warning("vulture", exc)
        _record(status, "vulture", reason)
        return []
    except (OSError, subprocess.SubprocessError) as exc:
        _scanner_warning("vulture", exc)
        _record(status, "vulture", str(exc))
        return []
    if completed.returncode not in (0, 1, 3):
        reason = completed.stderr.strip() or f"exit {completed.returncode}"
        _scanner_warning("vulture", reason)
        _record(status, "vulture", reason)
        return []
    _record(status, "vulture", None)
    return _parse_vulture_output(completed.stdout, min_conf=min_conf)


def scan_ruff(project_path: str, rules: str | None = None,
              files: list[str] | None = None,
              status: "dict | None" = None) -> list[dict]:
    """Ruff lint scan. Fail-open: returns [] and reports via `status`."""
    targets = [str(Path(p)) for p in files] if files else [str(Path(project_path))]
    select_rules = rules if rules else _ALL_SCAN_RULES
    try:
        completed = subprocess.run(
            [
                _console_python(),
                "-m",
                "ruff",
                "check",
                *targets,
                "--select",
                select_rules,
                "--no-cache",
                "--output-format",
                "concise",
                # B029a: wins over FORCE_COLOR, which NO_COLOR does not. Same
                # fix A24 applied to ruff_fix in graph/tools.py; this site was
                # missed because that survey only enumerated ruff_fix.
                "--color",
                "never",
            ],
            capture_output=True,
            text=True,
            timeout=_SCAN_TIMEOUT_S,
            check=False,
            env=_scan_env(),
        )
    except subprocess.TimeoutExpired as exc:
        reason = f"timeout after {_SCAN_TIMEOUT_S:g}s"
        _scanner_warning("ruff", exc)
        _record(status, "ruff", reason)
        return []
    except (OSError, subprocess.SubprocessError) as exc:
        _scanner_warning("ruff", exc)
        _record(status, "ruff", str(exc))
        return []
    if completed.returncode not in (0, 1):
        reason = completed.stderr.strip() or f"exit {completed.returncode}"
        _scanner_warning("ruff", reason)
        _record(status, "ruff", reason)
        return []
    _record(status, "ruff", None)
    return _parse_ruff_output(completed.stdout)


def _dedup_key(candidate: dict) -> tuple[str, str, str]:
    file_path = str(candidate.get("file_path") or candidate.get("file") or "")
    symbol = str(candidate.get("symbol") or candidate.get("name") or "")
    kind = str(candidate.get("kind") or "")
    if kind == "unused import":
        return (_normalize_file(file_path).lower(), symbol.split(".")[-1].lower(), kind)
    return (_normalize_file(file_path).lower(), symbol.lower(), kind)


def _merge_dedup(candidates: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str, str], dict] = {}
    for candidate in candidates:
        key = _dedup_key(candidate)
        existing = merged.get(key)
        if existing is None:
            merged[key] = candidate
            continue
        if float(candidate.get("confidence", 0.0)) > float(existing.get("confidence", 0.0)):
            merged[key] = candidate
    return list(merged.values())


__all__ = [
    "_ALL_SCAN_RULES",
    "_SCAN_TIMEOUT_S",
    "_merge_dedup",
    "_parse_ruff_output",
    "_parse_vulture_output",
    "_ruff_category",
    "_ruff_kind",
    "scan_ruff",
    "scan_vulture",
]
