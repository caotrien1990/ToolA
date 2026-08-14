---
name: git-governance
description: Governed Git status, stale checks, diffs, staging, unstaging, logs, and commits through ToolA.
trigger:
  - tool: "git_status | git_stale_check | git_diff | git_log | git_stage | git_unstage | git_commit"
  - task_pattern: "git status | stale git | stage files | commit changes | git diff"
risk_level: high
---
# Git Governance

## Tools
- git_status: structured porcelain-v2 records, state_hash and staged_hash.
- git_stale_check: dirty/conflict/operation/detached/local-behind/commit-age checks; never fetches.
- git_diff: worktree, staged, or both with bounded output and optional whitespace check.
- git_log: bounded structured local history.
- git_stage / git_unstage: explicit literal relative paths only; allow_all is refused; token + session required.
- git_commit: token + session; requires expected_staged_hash, configured identity, no conflicts, non-empty staged set by default.

## Workflow
1. git_status and git_stale_check.
2. git_diff(scope=both, check_whitespace=true) and tests.
3. git_stage(paths=[...], expected_state_hash=...). Never stage runtime logs, secrets, generated caches, or unrelated user changes.
4. git_status; review staged diff; preserve staged_hash.
5. git_commit(message=..., expected_staged_hash=...). Hooks stay enabled; no_verify only with explicit user approval.
6. git_status + git_log verify new HEAD and remaining worktree.

## Guardrails
- project_path must equal repository top-level; nested parent-repo escape fails GIT_ROOT_MISMATCH.
- Paths reject absolute/traversal/option-like values, Git magic, wildcards, .git, and common secret names.
- State hash mismatches fail GIT_STATE_STALE; re-read rather than retry blind.
- No implicit fetch/pull/push/reset-hard/clean/checkout/rebase.
- Mutating tools require exact EXECUTE authorization and never commit unrelated changes.
