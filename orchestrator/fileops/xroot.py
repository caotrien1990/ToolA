"""Out-of-project write policy and ledger helpers."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WHITELIST = ("C:/Users/ADMIN/.claude",)
LEDGER_NAME = "xroot_writes.jsonl"
WHITELIST_NAME = "xroot_whitelist.json"

LIVE_FILE_TOOLS = {
    "sessions.db": "session tools",
    "graph.db": "graph tools",
    "missions.db": "mission tools",
    "librarian.db": "librarian tools",
    ".council/council_config.json": "council_config_sync",
    ".council/inbox_*.md": "council_message",
    ".council/outbox_*.md": "council_message",
}
# SOT rule files (examples/CORE_RULE.txt, SLIM_RULE.txt, SLIM_RULE_INLINE.txt)
# are edited with write_fix / write_new_file. manage_agent sync_rule only
# distributes copies to CA rule files — it does not write the SOT.


def _resolve_loose(value: str | Path) -> Path:
    p = Path(str(value))
    try:
        return p.resolve()
    except Exception:
        return p.absolute()


def _norm(path: str | Path) -> str:
    return str(_resolve_loose(path)).replace("\\", "/").rstrip("/").lower()


def _is_prefix(path: Path, prefix: str | Path) -> bool:
    path_norm = _norm(path)
    prefix_norm = _norm(prefix)
    return path_norm == prefix_norm or path_norm.startswith(prefix_norm + "/")


def _cache_dir() -> Path:
    return PROJECT_ROOT / ".orchestrator_cache"


def _read_whitelist() -> list[str]:
    path = _cache_dir() / WHITELIST_NAME
    prefixes = list(DEFAULT_WHITELIST)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                prefixes.extend(str(item) for item in data if str(item).strip())
            elif isinstance(data, dict):
                values = data.get("paths") or data.get("prefixes") or []
                if isinstance(values, list):
                    prefixes.extend(str(item) for item in values if str(item).strip())
    except Exception:
        pass
    return prefixes


def _is_whitelisted(path: Path) -> bool:
    return any(_is_prefix(path, prefix) for prefix in _read_whitelist())


def _system_roots() -> list[str]:
    roots = [
        "C:/Windows",
        "C:/Program Files",
        "C:/Program Files (x86)",
        "C:/ProgramData",
    ]
    system_root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT")
    if system_root:
        roots.append(system_root)
    return roots


def _user_dotfile_roots() -> list[Path]:
    home = os.environ.get("USERPROFILE") or str(Path.home())
    return [Path(home) / name for name in (".ssh", ".aws", ".azure")]


def _live_file_match(path: Path) -> tuple[str, str] | None:
    resolved = _resolve_loose(path)
    name = resolved.name.lower()
    for db_name, tool in (
        ("sessions.db", "session tools"),
        ("graph.db", "graph tools"),
        ("missions.db", "mission tools"),
        ("librarian.db", "librarian tools"),
    ):
        if name.startswith(db_name):
            return "live_files", f"{db_name}* must be modified through {tool}"

    parts = [part.lower() for part in resolved.parts]
    joined = "/".join(parts)
    if joined.endswith("/.council/council_config.json"):
        return "live_files", (".council/council_config.json is the hand-edited SOT: edit it in place, then run council_config_sync to propagate it to the derived rule docs. council_config_sync reads this file and writes those docs; it cannot edit this file, so it is not a way to make the change you just attempted.")
    if ".council" in parts and (name.startswith("inbox_") or name.startswith("outbox_")) and name.endswith(".md"):
        return "live_files", ".council/inbox_*.md and outbox_*.md must be modified through council_message"
    return None


def _deny_group(path: Path) -> tuple[str, str] | None:
    live = _live_file_match(path)
    if live:
        return live

    for root in _system_roots():
        if _is_prefix(path, root):
            return "system_dir", f"writes under system directory are denied: {root}"

    # #35 residual, kept on purpose: a file OR folder whose parent is the anchor
    # (``C:\x``, ``\\host\share\x``) is refused. Measured 2026-09-25: no ledger or
    # log under .orchestrator_cache records one drive_root refusal, so the "may
    # over-block" note had no case behind it; the reason now names what it refuses.
    try:
        if path.is_absolute() and path.parent == Path(path.anchor):
            return "drive_root", "creating a file or folder directly at a drive or share root is denied"
    except Exception:
        pass

    for root in _user_dotfile_roots():
        if _is_prefix(path, root) and not _is_whitelisted(path):
            return "user_dotfiles", f"writes under user credential directory are denied: {root}"
    return None


def _sha256_file(path: Path) -> str | None:
    try:
        if path.exists() and path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return None
    return None


def _byte_count(path: Path) -> int | None:
    try:
        if path.exists() and path.is_file():
            return path.stat().st_size
    except Exception:
        return None
    return None


def ledger_append(
    *,
    phase: str,
    path: str | Path,
    caller_tool: str,
    session_id: str = "",
    bytes_written: int | None = None,
    sha256: str | None = None,
    ok: bool | None = None,
) -> None:
    cache_dir = _cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = _resolve_loose(path)
    row: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phase": phase,
        "path": str(p),
        "caller_tool": caller_tool,
        "session": session_id or "",
        "bytes": bytes_written,
        "sha256": sha256,
        "ok": ok,
    }
    with (cache_dir / LEDGER_NAME).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def log_intent(path: str | Path, caller_tool: str, session_id: str = "") -> None:
    ledger_append(phase="intent", path=path, caller_tool=caller_tool, session_id=session_id, ok=None)


def log_result(path: str | Path, caller_tool: str, session_id: str = "", ok: bool = True) -> None:
    p = _resolve_loose(path)
    ledger_append(
        phase="result",
        path=p,
        caller_tool=caller_tool,
        session_id=session_id,
        bytes_written=_byte_count(p),
        sha256=_sha256_file(p),
        ok=ok,
    )


def is_outside_project_root(target_path: str | Path, project_root: str | Path | None = None) -> bool:
    p = _resolve_loose(target_path)
    root = _resolve_loose(project_root or PROJECT_ROOT)
    try:
        p.relative_to(root)
        return False
    except ValueError:
        return True


def xroot_check(target_path: str | Path, _caller_tool: str, _session_id: str = "") -> dict[str, Any]:
    """Return {allowed, group, reason} for absolute writes outside the project root."""
    p = _resolve_loose(target_path)
    policy_off = os.environ.get("ORCHESTRATOR_XROOT_POLICY", "").strip().lower() == "off"
    if policy_off:
        return {"allowed": True, "group": "policy_off", "reason": "ORCHESTRATOR_XROOT_POLICY=off"}

    denied = _deny_group(p)
    if denied:
        group, reason = denied
        return {"allowed": False, "group": group, "reason": reason}

    if _is_whitelisted(p):
        return {"allowed": True, "group": "whitelist", "reason": "path prefix is whitelisted"}

    return {"allowed": True, "group": "external", "reason": "absolute path outside project root allowed by xroot policy"}


# The body fields that name a file a mutating tool writes, moves or deletes.
# ``src`` is not one: copy_file only reads it. A read tool takes these same names
# for the file it READS, so they are write targets only on a tool that mutates.
WRITE_TARGET_FIELDS = ("path", "file_path", "dst", "target_file", "destination")
# The body fields that name where a tool puts its OUTPUT -- a screenshot, a backup
# zip, an MCP pack tree (``out_dir`` is ``out_root``'s alias) -- on EVERY tool that
# takes them, mutating or not. The tool opens them as spelled, so a relative one
# lands under the daemon's cwd, not under project_path.
OUTPUT_TARGET_FIELDS = ("save_path", "output_path", "backup_dir", "out_root", "out_dir")

def write_targets(body: dict[str, Any]) -> list[Path]:
    """Every write target one call names, joined to its project_path the way the tool joins it.

    #35: the dispatcher read only the FIRST of these fields, so move_file's
    ``destination`` never met the deny list. A relative target with no project_path
    is left out: resolving it here would measure it against the daemon's cwd, not
    the root the tool resolves it under -- the tool's own check reads that one.
    """
    base = str(body.get("project_path") or "")
    pairs: list[tuple[Any, str]] = [(body.get(field), base) for field in WRITE_TARGET_FIELDS]
    paths = body.get("paths")
    if isinstance(paths, list):
        pairs.extend((value, base) for value in paths)
    operations = body.get("operations")
    if isinstance(operations, list):
        pairs.extend(
            (op.get("file_path") or op.get("path"), str(op.get("project_path") or base))
            for op in operations
            if isinstance(op, dict)
        )
    targets: list[Path] = []
    for value, root in pairs:
        if isinstance(value, str) and value.strip():
            p = Path(value)
            if p.is_absolute():
                targets.append(p)
            elif root:
                targets.append(Path(root) / p)
    return targets


def output_targets(body: dict[str, Any]) -> list[Path]:
    """Every output location one call names, resolved the way the tool resolves it.

    #35 (measured 2026-09-25): only mutating tools met the deny list, and only
    through WRITE_TARGET_FIELDS, so desktop_screenshot / screenshot_page wrote
    ``save_path`` and backup_project wrote its zip under ``backup_dir`` into a
    credential directory with no verdict at all.
    """
    return [
        _resolve_loose(value)
        for value in (body.get(field) for field in OUTPUT_TARGET_FIELDS)
        if isinstance(value, str) and value.strip()
    ]

def classify_write_target(
    target_path: str | Path,
    project_root: str | Path | None = None,
    caller_tool: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    """The one verdict every write gate reads for a RESOLVED target.

    #35 (measured 2026-09-25, sandbox): each gate used to consult the deny list only
    when it judged the target cross-root, and judged that from the SPELLING -- an
    absolute path, measured against [A]'s tree by the dispatcher and against
    project_path by the write tools. With project_path set to the home directory,
    ``.ssh/x`` relative, ``<home>/.ssh/x`` absolute, copy_file's relative dst and
    write_fix's relative path all landed in the credential directory; only the
    absolute spelling with no project_path was refused. The same file now gets the
    same verdict, however it is spelled:

    * ``allowed`` -- the deny list, for EVERY target. Being inside the project grants
      nothing: the project root is whatever the caller says it is.
    * ``cross_root`` -- outside ``project_root`` (default: [A]'s tree, the default the
      write tools use). It picks only the audit route: a cross-root write needs no
      session and is logged to the xroot ledger; an in-project write takes the
      project's session and backup gates, whatever its spelling.
    * ``managed`` -- cross_root or a live file: what goes to the xroot ledger.
    """
    policy = xroot_check(target_path, caller_tool, session_id)
    cross_root = is_outside_project_root(target_path, project_root)
    return {
        "allowed": bool(policy.get("allowed")),
        "cross_root": cross_root,
        "managed": cross_root or policy.get("group") == "live_files",
        "policy": policy,
    }
