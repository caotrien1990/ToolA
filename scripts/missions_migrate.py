#!/usr/bin/env python3
"""Missions DB migration CLI.

Usage:
    python scripts/missions_migrate.py <db_path>            # forward v0 -> v1
    python scripts/missions_migrate.py <db_path> --rollback # v1   -> v0 (rename to .rollback_bak)

Forward migration = run ``orchestrator.mission.store.init_db`` (idempotent
CREATE TABLE IF NOT EXISTS + meta(schema_version=1)).

Rollback = rename the db file to ``<db_path>.rollback_bak`` (no data loss,
no table drops). A second forward run will re-create a fresh v1 db.

Prints a JSON result on stdout. Exit code 0 on success, 1 on error.

GR-13: schema changes to store.py require a matching step here.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Allow running as a script: add repo root to sys.path so the
# ``orchestrator`` package is importable.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from orchestrator.mission.store import (  # noqa: E402
    SCHEMA_VERSION,
    get_schema_version,
    init_db,
)


def _forward(db_path: Path) -> dict:
    before = get_schema_version(db_path)
    init_db(db_path)
    after = get_schema_version(db_path)
    return {
        "action": "forward",
        "db_path": str(db_path),
        "schema_version_before": before,
        "schema_version_after": after,
        "target_version": SCHEMA_VERSION,
        "ok": after == SCHEMA_VERSION,
    }


def _rollback(db_path: Path) -> dict:
    before = get_schema_version(db_path)
    if not db_path.exists():
        return {
            "action": "rollback",
            "db_path": str(db_path),
            "ok": False,
            "error": "db file does not exist",
        }
    bak = db_path.with_suffix(db_path.suffix + ".rollback_bak")
    # If a .rollback_bak already exists, don't clobber it.
    if bak.exists():
        return {
            "action": "rollback",
            "db_path": str(db_path),
            "ok": False,
            "error": f"rollback backup already exists: {bak}",
            "schema_version_before": before,
        }
    db_path.rename(bak)
    return {
        "action": "rollback",
        "db_path": str(db_path),
        "renamed_to": str(bak),
        "schema_version_before": before,
        "ok": True,
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rollback = False
    positional: list[str] = []
    for a in argv:
        if a == "--rollback":
            rollback = True
        else:
            positional.append(a)
    if len(positional) != 1:
        print(json.dumps({"ok": False, "error": "usage: missions_migrate.py <db_path> [--rollback]"}))
        return 1
    db_path = Path(positional[0]).resolve()
    try:
        if rollback:
            result = _rollback(db_path)
        else:
            result = _forward(db_path)
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "db_path": str(db_path)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
