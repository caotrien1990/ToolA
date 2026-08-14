"""JSONL append/tail helpers hardened after 37 broker race incidents."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any, Iterator


@contextlib.contextmanager
def _exclusive_file_lock(handle: Any) -> Iterator[None]:
    if os.name != "nt":
        yield
        return

    import msvcrt

    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    try:
        yield
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def append_jsonl(path: str | os.PathLike[str], obj: Any) -> None:
    """Append one strict JSON line under lock because broker appends have raced."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, allow_nan=False) + "\n"
    with target.open("a+", encoding="utf-8", newline="\n") as handle:
        with _exclusive_file_lock(handle):
            handle.seek(0, os.SEEK_END)
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())


def read_tail(path: str | os.PathLike[str], limit: int = 100) -> list[Any]:
    """Return the last JSONL objects in file order without reading huge logs fully."""
    if limit <= 0:
        return []

    target = Path(path)
    try:
        with target.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            chunks: list[bytes] = []
            newline_count = 0
            chunk_size = 8192

            while position > 0 and newline_count <= limit:
                read_size = min(chunk_size, position)
                position -= read_size
                handle.seek(position)
                chunk = handle.read(read_size)
                chunks.append(chunk)
                newline_count += chunk.count(b"\n")

        data = b"".join(reversed(chunks))
    except FileNotFoundError:
        return []

    lines = [line for line in data.splitlines() if line.strip()]
    return [json.loads(line.decode("utf-8")) for line in lines[-limit:]]
