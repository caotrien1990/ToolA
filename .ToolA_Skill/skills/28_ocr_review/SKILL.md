---
name: ocr_review
description: |
  Use when a change needs a SEMANTIC review (logic, security, cross-language) that ruff
  cannot give, or when a whole tree needs scanning. OCR is a layer BESIDE ruff, never a
  replacement: write_fix/write_new_file give syntax, ruff gives Python lint, ocr_* gives
  semantics, then git_stage/git_commit. Slot 28 because 23/24/25 are retired (see SKILLS.md).
trigger:
  - task_pattern: "semantic review | security review | review this diff | scan the repo | code review before commit"
  - error_name: "OCR_EXEC_FAILED | OCR_LLM_NOT_CONFIGURED"
  - tool: "ocr_review | ocr_rule | ocr_scan"
preconditions:
  - "venv_ocr/ installed by [A] itself: npm install --prefix venv_ocr (never folder 45, never npm global)"
  - "delegate mode only until Anh supplies an LLM endpoint; ~/.opencodereview holds no config.json"
related_skills:
  - 05_write_pipeline
  - 09_git_flow
  - 04_tool_tiers
risk_level: low
---

# OCR semantic review

## Trigger

Use when the question is "is this change *correct*", not "is this file *well formed*".
`validate_file` and `ruff_fix` answer the second and are cheaper; reach for `ocr_*` only
after those are clean, or when the tree is not Python at all.

## The three tools

| tool | maps to | answers |
|---|---|---|
| `ocr_review` | `delegate preview --repo <path> --format json` | which files in this diff are reviewable, and how big is the change |
| `ocr_rule` | `delegate rule --format json` | which review rule/pattern applies to these paths |
| `ocr_scan` | `scan --preview` | what would a whole-tree scan cover |

## Contract facts that are easy to get wrong

- **delegate mode runs no LLM and costs no token.** It locates the work and states the
  rule; it does not answer the question. A real answer needs `ocr_scan(preview_only=false)`,
  which needs an LLM endpoint that does not exist yet -- so it refuses up front with
  `OCR_LLM_NOT_CONFIGURED` + `ca_next_action: use_ocr_review_delegate_mode` instead of
  burning a call to fail.
- **`--repo` exists**, so [A] never changes cwd to review another tree.
- **The payload is a FLAT dict** (`payload["total_files"]`), unlike `ruff_fix` which nests
  under `payload["result"]`.
- **Two fields are gated off by default on purpose**: `background` (`include_background`)
  carries the whole commit message, and `ocr_rule`'s rule body (`include_rule_text`) was
  measured at 7886 bytes for one `.py`. Ask for them only when actually reviewing that group.
- **Every list key caps at 50** and declares `items_omitted`/`max_items`/`ca_hint_scope`
  **only when something was really dropped** -- a truncation notice that fires on clean runs
  teaches the caller to ignore it exactly when it matters.
- **`.md` is `unsupported_ext`.** Rule files, skill files and plans cannot be OCR-reviewed;
  they must be read manually and pinned by a test.
- OCR is read-only on the repo -- proven by identical `git_status` `state_hash` before and
  after a live run, not assumed.

## Anti-patterns

- Calling `ocr.cmd` / `ocr.ps1`: the daemon runs `pythonw.exe` with no console, and every
  shim layer is a place stdout comes back empty while rc != 0, which a caller reads as
  "no findings". Call `opencodereview.exe` directly.
- Running `ocr_scan(preview_only=true)` over the whole repo and pasting the result: that was
  109 259 bytes before the cap landed.
- Treating an empty stdout as clean. `stdout_empty` is reported so an empty capture shows up
  as an anomaly.

## Regression anchor

`ocr_review` on commit `9216cb3` = **4 files, +666/-21**. Any deviation is a real change in
behaviour, not noise.
