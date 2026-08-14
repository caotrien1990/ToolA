"""JSON helpers that fail closed after 37 broker race incidents."""

from __future__ import annotations

import json
import os
from json import JSONDecodeError
from typing import Any

from orchestrator.storage.fs import atomic_write_text


def load_json(path: str | os.PathLike[str], default: Any = None) -> Any:
    """Return default for missing or corrupt JSON because broker state is recoverable."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, OSError, JSONDecodeError):
        return default


def dump_json(path: str | os.PathLike[str], obj: Any, indent: int | None = None) -> None:
    """Write strict JSON through atomic replace to avoid half-written state."""
    text = json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=indent)
    atomic_write_text(path, text, encoding="utf-8")
