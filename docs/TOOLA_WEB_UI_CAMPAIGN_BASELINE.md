# ToolA web UI campaign — P0 baseline

This campaign baseline records the pre-integration state observed on 2026-11-21. Monitor and its read API now run in ToolA at :8083.

## Sources and runtime contracts

| Surface | Verified origin and source | Baseline to preserve |
| --- | --- | --- |
| Main | :8083/, orchestrator_server.py::handle_root, path_runtime.py::resolve_ui_html; parent UI/8. AGENT TOOL - READ WRITE TEST_UI.html matches live HTML ignoring newline style | Theme via toolC_theme, folder, tier selection (T1 active after click), sessions, inspector and logs |
| Council | :8083/council, orchestrator_server.py::handle_council_dashboard, orchestrator/council/dashboard.py::render_council_dashboard | Preserve readiness placeholder, local/origin checks and existing Council controls |
| Monitor | :8083/monitor and :8083/api/monitor/*, wire format based on 63. MONITOR/bridge.py | D3 loaded, Graphity storage namespace, traffic/history/detail and offline states |
| Matrix | Parent toolA_function_matrix.html (untracked, original unchanged) | Inventory, chains, gaps, automation and Radial; 260 snapshot identifiers match /api/tools at baseline; D3 is not required by existing SVG graph; Radial search opens with bridge live |

Computed Council tokens (Chromium at 1280x800): bg #08090b, panel #101216, text #f3f4f6, muted #a4aab4, accent #7c6ff2, control radius 7px, Council sans stack starts with Segoe UI Variable and Inter Variable, monospace starts with JetBrains Mono. Earlier CSS declarations were overridden in the live page.

GET :8083/monitor and :8083/matrixtool returned 405 before implementation. Both servers own /api/health. :8099/api/activity returned a full snapshot and a subsequent delta with the same boot ID and nondecreasing sequence. Activity measures calls, not A-to-B dependencies. Source: bridge.py::_activity_payload and the existing Matrix liveCycle function.

Matrix audited snapshot contains 30 cross-links (19 delegates-to, 5 alias-dispatch, 1 alias-shared-handler, 5 recommends), and 16 auto-chain rules, separate from live usage. Keep citation and evidence text. Missing coverage must be explicit; do not count observed calls as edges.

The integrated Monitor serves GET /api/monitor/{activity,health,graph,history,evdetail} and /api/monitor/events/{event_id}. Requests must come from loopback with a local Host and, when supplied, a matching Origin. The query allowlist excludes graph refresh and unrestricted file metadata. Responses are limited to 16 MiB. An expired sequence or changed boot ID returns a full activity snapshot.

ToolA starts one Monitor poller through aiohttp startup and waits for in-flight work before closing the read-only SQLite connections at cleanup. Monitor uses `ORCHESTRATOR_DURABLE_AUDIT_DB` when set. Otherwise it reads `durable_audit.db` next to `ORCHESTRATOR_SESSIONS_DB` or in the default cache. MONITOR_TOOLA_CACHE, MONITOR_TOOLA_REGISTRY, MONITOR_TOOLQ_DB and MONITOR_GRAPH_DIR allow source overrides. An unavailable database produces section errors while other data remains readable. The legacy :8099 service is optional.

## Git checkpoint (read-only)

| project-orchestrator worktree | main-only / worktree-only commits | staged / unstaged / untracked records |
| --- | --- | --- |
| D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator | 0 / 0 | 0 / 4 / 1 |
| C:\Users\ADMIN\AppData\Local\Temp\fb1-g3b-20261113-0015 | 29 / 0 | 0 / 1031 / 0 |
| C:\Users\ADMIN\AppData\Local\Temp\t4wt_deploy_gate_20261110 | 36 / 0 | 0 / 1020 / 0 |
| D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\t4wt_deploy_gate_20261110 | 36 / 0 | 0 / 7 / 5 |
| D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\wt_codo_t4_20261112 | 26 / 0 | 0 / 2 / 2 |
| D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\wt_codo_upgrade_20261105 | 57 / 0 | 1 / 2 / 1 |

Commands: git worktree list --porcelain, git rev-list --left-right --count main...HEAD, git --no-optional-locks status --porcelain --untracked-files=normal. One pytest cache permission warning was observed; ignored files and external leases were not inventoried. No worktree has been approved for removal. Monitor main was clean. ToolA parent has a gitlink to project-orchestrator and pre-existing staged, unstaged and untracked work.

## Provenance caveat (added 2026-11-22, after the blind audit of M-INT)

This baseline is not a fully independent standard for every clause it carries.

- Two clauses were written after the code already behaved that way. `orchestrator/web_views.py` carried `_MAX_BYTES = 16 * 1024 * 1024`, the `len(body) > _MAX_BYTES` refusal, `await self.poller` and `cancel_futures` in the 21:53:41 write (dry-run `dry_8b5d1d223cef4e7d9d834702e5d90514`, target hash `b369d95e36526605`). The sentences "Responses are limited to 16 MiB" and "waits for in-flight work before closing the read-only SQLite connections at cleanup" first appear in the 22:18:29 write to lines 20-22 of this file. The proof is independent of any backup: the 21:59:23 dry-run `dry_082bed7075704c86926a01155e2c0c5d` carries the pre-image hash `fa4b6937a997` and its own replacement text for lines 3-20 contains zero occurrences of "16 MiB", "in-flight" or "waits for", twenty-five minutes after the code acquired them. A pass on those two clauses therefore reads as "code matches text written after the code", not as independent evidence.
- Whether the remaining clauses on lines 20 and 22 (allowlist, loopback, expired-sequence, boot-ID) predate the code is NOT established. The 21:59:23 patch covers lines 3-20 and contains none of those phrases, but the 13:53:53 and pre-22:18 full texts are unrecoverable, so a clause sitting outside the patched range cannot be dated at all. Those verdicts should be read as unproven in either direction, not as confirmed.
- The baseline froze at 22:18:29 while code kept changing: 15 further `write_fix` operations touched `matrix.html`, `matrix.js`, `tests/test_web_ui_campaign.py` and `web_views.py` up to 23:51:39.
- The original text written at 13:53:53 (4033 bytes, hash `fa4b6937a99743f2`) cannot be recovered verbatim. Its existence, timestamp and size are provable from the 21:59:23 dry-run `dry_082bed7075704c86926a01155e2c0c5d`, whose `target_file_hash` is that hash; its individual sentences are not. No pre-image survives in Git (this file is untracked), in `sessions.db`, in `durable_audit.db` (a 115,265-cell sweep returned zero matches), or in the OpenCode transcript, where the recoverable intermediate prints are 4072 and 4152 bytes and match no recorded hash.
- One pre-image was destroyed during this campaign, and the destruction is mine, not the auditor's. `TOOLA_WEB_UI_CAMPAIGN_BASELINE.md.bak` held the pre-22:18 state (4132 bytes, hash `9cdcd1c31da8`) - the evidence the auditor's S1 originally rested on - until the caveat writes of 2026-11-22. `write_fix` keeps exactly one generation of `.bak` and overwrites it on every edit, so that image is unrecoverable; the `.bak` on disk now holds a post-caveat state (9616 bytes, hash `1ec8d999167a`). Two things make the S1 conclusion survive this loss. First, the 21:59:23 dry-run proves the same ordering from a source these writes cannot touch. Second, the original body is intact: the first 4280 bytes of the current file still hash to `6c4f4e981982`, byte-for-byte the pre-caveat baseline, so the caveat appended nothing to and removed nothing from lines 1-35.
- Line 9 states the parent HTML "matches live HTML ignoring newline style". That is true of the bundled `orchestrator/web_ui/main.html` and false of the served page: `decorate_page` adds a nav, a stylesheet link and a `data-toola-page` attribute, so live `/` differs by +3/-1 lines. The clause wording, not the code, is at fault.
- Line 14 computed tokens are only partly reproducible. Measured in Chromium at 1280x800: `.muted` resolves to `#707783`, not `#a4aab4`; four `pre` elements and `#broker-text` do not start with `JetBrains Mono`; `#narrator-prompt` uses a sans stack; three buttons (`#clear-council-session`, `#broker-send`, `.copy-button`) resolve to `Arial`; only 8 of 61 controls begin with the stated sans stack. The claim that earlier CSS declarations were overridden holds for those 8, not for the rest.
- Lines 28-33 git checkpoint and the sentence "Monitor main was clean" are stale. Re-measured 2026-11-22: five of six worktrees are +3 commits ahead of the recorded value, `project-orchestrator` is +4 unstaged and +5 untracked, and its working tree holds 8 modified plus 6 untracked records with 0 staged. The recorded numbers describe 2026-11-21 and cannot be re-observed without a git write command.
- Line 12 marks the parent `toolA_function_matrix.html` "(untracked, original unchanged)", which is correct for the parent (last `write_operations` row 2026-11-18 13:45:56, zero rows after the campaign opened). It does not mean the bundled `orchestrator/web_ui/matrix.js` equals the parent: the bundled file differs by 42 hunks.

## Defects fixed 2026-11-22 (this baseline is not amended to describe them)

- `/assets/toola/{name}` enforced no loopback, Host or Origin gate while every other packaged route did. `_asset` now calls `_require_loopback`. Regression: `test_assets_share_the_page_gate`.
- `_HOST` accepted any port, so `Host: localhost:9999` passed. Host port is now compared against the accepted socket by `host_ok`, which reads the bound port instead of a constant so the ephemeral TestClient port keeps working. `/council` shares the same helper. Regression: `test_host_port_must_be_the_bound_port`.
- A SQLite or serialisation fault inside `dispatch` was returned as 502 `MONITOR_BRIDGE_UNAVAILABLE`, naming a bridge that no longer runs. Internal faults now return 500 `MONITOR_INTERNAL_ERROR`. Regression: `test_store_fault_is_not_reported_as_a_dead_bridge`; `test_db_errors_keep_last_good_data_and_pages_available` was updated to assert the new diagnosis.

Each of the three regressions was mutation-checked: removing the corresponding fix in memory makes its test fail (S2 10 failures, S6 1, S7 1). Suite state after the fix: 20 passed, 44 subtests passed.
