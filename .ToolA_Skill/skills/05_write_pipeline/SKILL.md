---
name: write_pipeline
description: |
  Use for all [T2] write operations: write_fix, write_new_file, write_fix_html, multi-file
  batches, base64 encoding, dry-run + apply, post-write validation, auto-rollback. Activate
  when task touches code/text mutations, especially auth/token/multi-file, Unicode content,
  or rollback-required scenarios.
trigger:
  - task_pattern: "edit | modify | write | refactor | rename | add function | change config"
  - error_name: "FILE_CHANGED_OUTSIDE_ORCHESTRATOR | BINARY_FILE_UNSUPPORTED | INVALID_BASE64"
  - tool: "write_fix | write_new_file | write_fix_html | apply_after_dry_run | ruff_fix"
preconditions:
  - "EXECUTE mode (trigger 'tiến hành' present) OR post-write validation exception"
  - "open_session done"
  - "generate_project_map ran (INDEX present)"
related_skills:
  - 06_token_auth
  - 07_error_schema_hints
  - 03_path_session
risk_level: high
---

# 05 — Write Pipeline

## V15 R39 — "Light app fix" recipe (minimal patch pipeline)

Khi User report bug nhỏ trong app target (1-2 dòng, 1 module):

```
1. inspect_project_usage(project_path) — get app profile + key buttons/handlers
2. find_symbol(name=<function>, project_path) — locate function
3. read_file_range(file_path, start, end) — read context
4. write_fix(file_path, start_line, end_line, new_content, dry_run=true)
5. Review diff_preview + validation
6. apply_after_dry_run (hoặc write_fix dry_run=false)
7. validate_file + (post-write auto: cache_delta)
8. [Nếu có workflow active] workflow_submit_step status='done' với evidence file:line
9. Report: file changed + validation OK + cache_delta done
```

Anti-pattern: skip step 1-3, đoán line number → fail rồi loop FILE_CHANGED.

Multi-file/multi-hunk variant → dùng `write_transaction` (xem section R39 multi-hunk).

## V15 R39 — Multi-hunk same file recipe + FILE_CHANGED recovery

**Khi cần patch nhiều đoạn cùng 1 file** (vd refactor 3-5 chỗ trong 1 module): KHÔNG gọi `write_fix` lặp lại — mỗi lần fix sẽ shift line, lần sau lệch và báo `FILE_CHANGED_OUTSIDE_ORCHESTRATOR`. Cách đúng:

```jsonc
write_transaction({
  "project_path": "<root>",
  "operations": [
    // SORT BOTTOM-UP (highest start_line FIRST) để tránh line-shift cascade
    {"tool": "write_fix", "file_path": "x.py", "start_line": 250, "end_line": 252, "new_content": "..."},
    {"tool": "write_fix", "file_path": "x.py", "start_line": 100, "end_line": 105, "new_content": "..."},
    {"tool": "write_fix", "file_path": "x.py", "start_line": 30,  "end_line": 30,  "new_content": "..."}
  ]
})
```

Pipeline: dry_run all → backup_project → apply (mỗi op atomic) → batch validate once. Nếu 1 op fail → ABORT toàn batch, leave .bak cho rollback manual.

**Khi nào write_transaction KHÔNG đủ**:
- Cần fuzzy-match anchor text (không biết line number) → CHƯA có (`write_fix_by_anchor` defer R40+). Workaround: `search_code(query="anchor text", mode="literal")` → lấy line, rồi `write_fix(start_line, end_line)`.

**Recovery FILE_CHANGED_OUTSIDE_ORCHESTRATOR (R39 explicit)**:
1. `refresh_project_map(project_path)` — refresh hash cache
2. `read_file_range(file_path, start, end)` — re-seed hash for that file
3. Retry SINGLE `write_fix` HOẶC switch sang `write_transaction` nếu có ≥2 hunks

KHÔNG retry blind cùng payload — read first.

## When to use

| Situation | Tool | Notes |
|-----------|------|-------|
| 1-2 lines, existing file, low risk | `write_fix` | Host forbids native for `write_existing_file`. SOT `examples/CORE_RULE.txt` is also `write_fix`, then `manage_agent sync_rule` distributes. Native only if [A] down / write_fix impossible / HYBRID [A] Python. |
| Multi-file batch (≥3 files) | `write_fix` per file OR `write_transaction` (V15 P1, R13.4 multi-tool) | Auto-rollback |
| Content has Unicode/quotes/backslash | `write_fix` with `content_encoding=base64` | Encoding-safe |
| Mixed batch (edit existing + create new) | `write_transaction` với `op.tool=write_fix\|write_new_file` (R13.4) | One atomic batch |
| HTML file | `write_fix_html` (delimiter check) | For .html only |
| New file | `write_new_file` | Set `overwrite=false` default |
| Replace exact line range | `write_fix(start_line, end_line, new_content)` | Re-read before write |
| Ruff safe auto-fix | `ruff_fix(project_path, apply=false)` then review; only set `apply=true` in EXECUTE | Preview-before-apply; `unsafe_fixes` stays opt-in |

## write_transaction multi-tool dispatch (V15 R13.4)

Each `operations[i]` accepts a `tool` field. Default: `write_fix` (line-range replace). Supported:

| op.tool | Semantics | Required fields per op |
|---------|-----------|------------------------|
| `write_fix` (default) | Replace existing file lines | `file_path, start_line, end_line, new_content` |
| `write_new_file` | Create new file in batch | `file_path, new_content` (optional `overwrite=false`) |

Mixed batch example:
```jsonc
{
  "project_path": "<root>",
  "operations": [
    {"tool": "write_fix",       "file_path": "a.py", "start_line": 1, "end_line": 1, "new_content": "import x\n"},
    {"tool": "write_new_file",  "file_path": "b.py", "new_content": "# new module\n"},
    {"tool": "write_fix",       "file_path": "a.py", "start_line": 5, "end_line": 5, "new_content": "y = 1\n"}
  ]
}
```

Pipeline: dry_run all → backup_project → apply all → batch validate.
If ANY op fails dry_run or apply → entire batch aborts; partial-write `.bak` files left for manual recovery + `backup_path` returned.

Common pitfall: nếu bạn vừa `write_new_file` standalone vào file rồi gọi `write_transaction` với `write_fix` cùng file đó, `write_fix` sẽ reject `FILE_CHANGED_OUTSIDE_ORCHESTRATOR`. Sequence chuẩn:
1. `refresh_project_map(project_path)` — refresh hash cache
2. `read_file_range(file_path)` — seed cache hash
3. `write_transaction(operations=[...])` — proceeds cleanly

## Standard workflow (single file)

```
1. read_file_range(start_line, end_line)  ← confirm current content
2. write_fix(file_path, start_line, end_line, new_content, dry_run=true)
3. Review diff_preview, validation, encoding_check
4. apply_after_dry_run(project_path, dry_run_id) IF available
   OR write_fix(...dry_run=false) IF schema lacks apply_after_dry_run
5. Auto-applied post-write: validate_file, validate_symbols, cache_delta
6. Report: files changed, validation, warnings, native audit
7. DEFAULT mode → 🔍
```

## Multi-file batch workflow

```
1. (Optional but recommended) backup_project(force=true)
2. For each file:
   a. read_file_range
   b. write_fix dry_run=true
   c. Collect dry_run_id
3. Review all diffs together
4. Apply each (apply_after_dry_run preferred)
5. post_write_batch_verify(project_path, changed_files=[...])
6. If any validate fail → manual rollback (restore .bak files)
7. Report consolidated
```

## Base64 rule

Use `content_encoding="base64"` when content contains:
- Multi-line content (`\n`)
- Quotes (`"`, `'`, `` ` ``)
- Backslash (`\` paths)
- HTML/JS/large blocks
- Unicode (Vietnamese, emoji)

Invalid base64 → re-encode original UTF-8 once, retry. Never native fallback for base64 issues.

## Guardrails

- write_fix is line-range replace ONLY, never regex/pattern.
- write_fix_html restricted to .html.
- Binary file → STOP (`BINARY_FILE_UNSUPPORTED`).
- delete_file is permanent → confirm User for non-temp.
- write_new_file overwrite default false; FILE_ALREADY_EXISTS → ASK overwrite.
- After write batch >100 lines OR >5 files → suggest `backup_project` first.

## Error handling

| Error | Action |
|-------|--------|
| FILE_CHANGED_OUTSIDE_ORCHESTRATOR | `read_file_range` confirm + `refresh_project_map` OR `generate_project_map(backup_override=true)` if empty |
| INVALID_BASE64 | Re-encode UTF-8 → base64 once, retry |
| BINARY_FILE_UNSUPPORTED | STOP, report |
| Validation auto-revert | Examine `error` field; do not re-apply same content |

## Auto features (V15 R3+)

Backend will auto-detect content_encoding (base64 if multiline/Unicode), auto-validate, auto-cache_delta. CA prompt simplified to canonical `write_fix(file_path, start_line, end_line, new_content)`.

## Harness

- Test base64 round-trip with: .py, .js, .ts, .html, .css, .json, .md, .ps1, Vietnamese with diacritics
- Test multi-file rollback by intentionally breaking 1 of 5 files
- Test FILE_CHANGED guardrail by external `touch` between read and write

## R56 real-use recovery contract

- `write_fix` and `write_new_file` already open a draft on pre-write or post-write validation failure; the response carries `draft_id` and `failed_parts`.
- For `SYNTAX_ERROR_PRE_WRITE` or line drift, do not retry the original line range blindly. Read the failed part and patch only the draft with `write_draft_patch`.
- The backend may auto-ship only after the draft content is re-audited and clean. It must not invent replacement content or auto-patch an ambiguous line mapping.
- For a rerun of `copy_file`, `DST_EXISTS` is a safety guard: use a new disposable destination, or explicitly retry once with `overwrite=true, backup_existing=true`.
- For a host-generated UTF-8/base64 payload, use a UTF-8 encoder such as PowerShell `[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(...))`; do not assume browser-only `btoa()` exists in the dispatch runtime.
