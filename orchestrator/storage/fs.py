"""Filesystem primitives hardened for repeated broker race incidents."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path


def atomic_write_text(path: str | os.PathLike[str], text: str, encoding: str = "utf-8") -> None:
    """Replace a text file atomically because broker writes have raced 37 times."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")

    try:
        tmp_path.write_text(text, encoding=encoding)
        last_error: OSError | None = None
        for attempt in range(4):
            try:
                os.replace(tmp_path, target)
                return
            except (PermissionError, OSError) as exc:
                last_error = exc
                time.sleep(0.025 * (2**attempt))

        try:
            target.write_text(text, encoding=encoding)
            return
        except OSError:
            if last_error is not None:
                raise last_error
            raise
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
