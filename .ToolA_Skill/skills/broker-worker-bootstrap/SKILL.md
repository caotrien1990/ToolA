---
name: broker-worker-bootstrap
description: Use inside a broker-worker task when the prompt says "If this task must write outside cwd, bootstrap [A] and use [A] write tools." Keeps worker writes outside cwd routed through [A] instead of failing or silently using native filesystem writes.
---

# Broker Worker Bootstrap

## Trigger

Use this skill when a broker task requires reading or writing outside the worker `cwd`, or when the prompt includes:

`If this task must write outside cwd, bootstrap [A] and use [A] write tools.`

## Rule

- Use HTTP `[A:API]` only: `http://localhost:8083/api/call/<tool>`.
- Do not use MCP.
- For artifact/source writes outside `cwd`, open/verify the project root with `open_session`, then use `write_new_file` or `write_fix`.
- Use `content_encoding="base64"` for multiline text, JSON, Markdown, code, paths, backslashes, quotes, or Vietnamese.
- If `[A]` is down, stop and report `unknown/not verified`; do not silently native-write outside `cwd`.
- If a native fallback is explicitly unavoidable and allowed by the prompt, call `log_native_event` with the reason, target, scope, and failed [A] payload.

## Minimal Flow

1. `GET http://localhost:8083/api/tools/<tool>/schema` if a payload field is unclear.
2. `POST /api/call/open_session` with `project_path` set to the target root.
3. `POST /api/call/write_new_file` or `write_fix`.
4. Validate if the prompt asks for verification.
5. Reply with verdict, exact path, tool used, and any skipped check.

## Stop

Stop before deleting files, changing unrelated paths, writing without a clear project root, or retrying the same failed payload unchanged.
