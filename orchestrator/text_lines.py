"""Line numbering -- leaf module, imports nothing from this project.

#32 2026-09-25: every [A] tool that hands a CA a line number counted lines with
`str.splitlines()`. That method also breaks on \x0b \x0c \x1c \x1d \x1e \x85
    -- git, ripgrep, editors, `read_text()` and Python's own `ast`
linenos do not. Measured on orchestrator_server.py: one form feed in a comment
made splitlines() count 18903 lines against 18902 for git, so every line below
it was off by +1. A CA that took a number from git/grep and passed it to
write_fix edited the line ABOVE the one it meant, with ok:true.

Two questions, two splitters -- keep them apart:

- split_lines answers "which line number does a CA see or send". Line ends are
  "\r\n", "\r" and "\n", nothing else: universal newlines, the rule `ast` and
  `read_text()` follow. F1 2026-09-25: the first cut split on "\n" only, so on a
  file with a lone "\r" read_file_range (read_text() folds that "\r" into "\n")
  and write_fix (raw bytes) disagreed -- read showed a line, write_fix replaced
  the one after it. git and ripgrep keep a lone "\r" inside the line; only such
  files tell the rules apart, and there the reader and the writer must agree.
  NDJSON readers use it too: json.dumps always escapes "\r", so a record never
  holds a raw one, while U+2028/U+0085 stay raw under ensure_ascii=False.
- split_lf answers "what is the "\n"-separated view of a script body" for the
  destructive scanner. A lone "\r" stays inside the line: bash reads it as part
  of a word and _cmd_cr_joined_lines looks for it there. Interpreters that end a
  line at a lone "\r" get it folded to "\n" first (_interpreter_newlines).

Leaf module at the bottom of the dependency graph so surgical_mapper and
search_regex_worker can use it too. Pinned by test_line_numbers_count_newlines_only
and test_line_numbers_agree_on_a_lone_cr in tests/test_code_tools_cache.py.
"""
from __future__ import annotations

import io


def split_lines(text: str, keepends: bool = False) -> list[str]:
    """`str.splitlines()` with "\r\n", "\r" and "\n" as the only line breaks.

    Same shape as splitlines(): no empty last element for a trailing newline,
    and without keepends the whole line end goes.
    """
    lines = io.StringIO(text, newline="").readlines()
    if keepends:
        return lines
    return [line.rstrip("\r\n") for line in lines]


def split_lf(text: str, keepends: bool = False) -> list[str]:
    """`str.splitlines()` with "\n" as the only line break (see the module doc).

    Without keepends a "\r\n" ending loses its "\r" as well; a lone "\r" stays.
    """
    if not text:
        return []
    parts = text.split("\n")
    if keepends:
        lines = [part + "\n" for part in parts[:-1]]
        if parts[-1]:
            lines.append(parts[-1])
        return lines
    if parts[-1] == "":
        parts.pop()
    return [part[:-1] if part.endswith("\r") else part for part in parts]
