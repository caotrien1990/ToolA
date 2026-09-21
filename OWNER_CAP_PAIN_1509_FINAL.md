# Owner bootstrap / HOLD-room friction — 2026-09-15

Owner: Ope. Scope: Anh authorized native source fixes, leader blocker first.
Deployment: **source verified; live not applied**. No daemon restart was performed.
Initial pass observed generation: `73f17418dd69`. Residual follow-up observed
`9597684e1cf9` through task/tool responses; that change occurred outside Ope's work.
The residual source edits below have not been deployed or live-verified by Ope.

## Residual follow-up — source-only

Request: `tiến hành xử lý residual, source-only`.

- **HTTP self-call:** reproduced timeout with a real ephemeral HTTP server whose probe and health handlers share one event loop. Moved the entire blocking urllib request/read/error-close lifecycle to `asyncio.to_thread`; same test now returns 200. Duration uses monotonic time.
- **Health fields:** `uptime_s` derives from process-start monotonic time. `mcp_generation` is recognized but returns null with `field_notes`: the daemon does not own a single MCP-process generation. Read the specific MCP response's `mcp_generation`. Field notes survive explicit field filtering. No false alias to daemon generation.
- **Launch wrappers:** guards now cover direct, quoted `-Command "-File ..."`, nested cmd wrapper, `pwsh -c`, and newline before -File. Valid direct -File and explicit nested PowerShell invocation remain accepted. Schema explains direct -File and already-healthy exit-0 behavior; mocked exit-0 readiness never claims restart verification.
- **Banner:** reproduced line-7 replacement through section 0/0b/1. Guard also catches trailing blank lines and a replacement banner formatted as a heading. It protects the one-nonempty-line replacement shape, not arbitrary multi-line document rewrites.
- **Room self-pin:** examine the whole proposed document, including retained section 9. Explicit filename/self-labelled historical hashes warn even when editing another line; warnings are returned in dry-run as well as write responses. External artifact hashes are not flagged by the explicit-self-label rule. This is a warning, not automatic repinning or proof of the live room digest.
- **TA-1/4/6 and TA-7:** remain unverified/open; no runtime load/timeout closure is inferred.

Evidence under `.orchestrator_cache/test_scripts/`:

| Run | Result | Artifact |
|---|---|---|
| Residual reproduction before fixes | 7 failed, 7 passed | `ope-residual-red_86048db56431fa65.py` |
| First correction | 81 passed, 1 failed, 4 subtests passed (dry-run warning visibility) | `ope-residual-green_fb899c5401619c6f.py` |
| Final focused + health/write/owner regression | **153 passed, 4 subtests passed; py_compile 4/4** | `ope-residual-final_df0e175734531c3b.py` |

Final `git diff --check` passed. Compared normalized source against the pre-task
backup to isolate this turn's changes from existing work. Backup:
`_orchestrator_backups/project-orchestrator_backup_20260915_015135_507605.zip`
under the ToolA root. Native patch audits accepted as `upgrade_orchestrator`.
Changed source: `tools_upgrade_p5.py`, `orchestrator_server.py`,
`orchestrator/tools/write/core.py`; added `tests/test_residual_source_1509.py`.

The sections below retain the initial pass's evidence; the follow-up above
supersedes its narrower launch/banner/self-pin coverage descriptions.

## Leader blocker

- Live baseline confirmed capability appeared after the discovery catalog.
- Bootstrap now defaults to terse; capability and original claim precede discovery and occur within the first 2048 JSON characters in the regression fixture.
- `verbosity=full` / `include_discovery=true` retain discovery as opt-in.
- Cached-claim helper checks durable ownership before generating a shortcut claim. Empty RAM plus durable ownership refuses; matching claims recover the same HMAC capability.
- Prepare rejects missing durable claims before project preparation; existing open-session failure/evidence semantics are retained.
- MCP prepare accepts/forwards `owner_claim_id`; bootstrap uses the durable-aware helper. MCP open_session exposes the existing title options.
- begin_task ownership errors include accepted fields and placeholder-only payload examples.
- HMAC, compare_digest, immutable owner and checkpoint redaction remain covered by the existing owner tests.

## Friction disposition

| Item | Evidence / change | Limit |
|---|---|---|
| Missing file hash | Live names filter returned 0; added T1 `file_sha256`, streaming raw bytes, full expected digest comparison, concurrent-change detection | Not loaded in daemon yet |
| Missing write_status catalog | Live names filter returned 0; added T1 catalog adapter to the existing HTTP ledger handler | Unknown remains unknown; never authorizes blind replay |
| call_tool UNKNOWN_TOOL | Retained correct 404/UNKNOWN_TOOL, added MCP-dispatcher explanation and executable payload shape | call_tool is not an HTTP catalog operation |
| Wrong hash hint | Added exact read-intent routing ahead of lexical fallback | Explicit campaign routing retains priority |
| Session title/path | Existing title/mode/path disclosure and opt-in session tests pass; MCP parity added | Session root normalization still exists; always send explicit project_path and inspect effective_project_path |
| Self hash stale | Markdown edit warns when inserted digest/prefix matches current file bytes | Detects current full/prefix digest, not every historical digest; use external manifest or returned new_hash |
| MCP reconnect generation | Generation/stamp/fail-loud regressions pass | No live reconnect/long-session proof in this run; TA-5 not declared closed |
| Ruff breaks pins | `preserve_hash=true` refuses apply before spawning/backups; preview explains byte changes including I001/F401 | Pin declaration is explicit, not inferred from arbitrary filenames |
| Banner consumes sections | Single-line Markdown banner spanning subsequent headings is rejected | Use start_line=end_line for one-line banner; CP-039 recipe remains 7/7 |
| POST has no body | `http_check(json_body={})` sends UTF-8 JSON and reports body presence/size | No automatic endpoint/method rewrite |
| PowerShell launch | Rejects `powershell/pwsh -Command -File` before spawn; direct -File recipe returned | start_app readiness does not prove recycle; response states restart_verified=false |
| Python lacks psutil | Existing required_python_modules selector tests pass; integrated run required pytest,aiohttp,psutil | No global Python/package installation |
| WMI empty cmdline/path | Existing lifecycle contracts retained; treat missing fields as unavailable evidence | No claim that OS visibility changed; use runner configuration plus health identity |
| Native/read-token friction | Verified audit HYBRID requires absolute target path; native reads do not create edit tokens | For ordinary write_fix use read_file_range; retain HOST_GATE_DENY elsewhere |
| Search latency/deprecated regex | Retain native inspection route and search_code for indexed semantics | No ToolQ performance benchmark or TA-7 closure in this run |
| Dispatcher-only MCP | Existing architecture retained; new catalog tools are reachable through call_tool after deployment | No invented named MCP tool surface |
| Discovery hints already shipped | Existing discovery/alias/title/lifecycle tests pass | Prior upgrades preserved |

Operational endpoint recipes from Anh's evidence: POST `/observability` and `/ops_progress` with JSON `{}`; GET `/ui/watched`. These ToolQ routes were not re-probed here.

## Verification evidence

Commands executed through run_project_test_script, runtime=python, isolated sessions DB/generation file, unique basetemp under TEMP.

| Suite | Result | Script artifact under `.orchestrator_cache/test_scripts/` |
|---|---|---|
| Original owner baseline | 33 passed | `ope-owner-baseline_5b588242e24f920e.py` |
| Owner + compact/reconnect regressions | 40 passed | `ope-owner-verify_d563bc742c92e37b.py` |
| Pain/title/discovery/MCP generation/hints | 116 passed, 7 subtests passed | `ope-pain-verify_178f28071255c3a5.py` |
| Integrated first attempt | 258 passed, 3 failed, 6 subtests passed | `ope-integrated_3016f53dcec6a456.py` |
| Integrated corrected run | **261 passed, 6 subtests passed** | `ope-integrated-retry_5995556a222cc476.py` |
| Anti-drift + MCP proxy/gate | 17 passed; py_compile 10/10 | `ope-final-checks_750afcefd4da98a0.py` |

Counts overlap; do not sum them. Three first-attempt failures were compatibility regressions in prepare ordering and the legacy P5 schema group; source was corrected, existing tests were not weakened.

`git diff --check` passed for touched code. Source matrix initially found exactly two missing inventory entries for the two added tools; the canonical generator regenerated `tool_surface_current.md` (254 tools).

Native audit: first batch was logged-and-rejected because target was a relative multi-file string. Reissued with the absolute orchestrator root; HYBRID classification accepted. No secret values were put in source, tests, this note, or native audit payloads.

Pre-change backups: `_orchestrator_backups/project-orchestrator_backup_20260915_011349_420396.zip` (seven source files), `project-orchestrator_backup_20260915_012100_716989.zip` (graph tools), relative to the ToolA root. Preserve pre-existing working-tree changes when comparing or recovering individual hunks.

## Remaining acceptance boundary

Live deployment/new-session leader/MCP reconnect verification remains pending. Historical TA-1/4/6 write timeout/replay, SQLite-lock behavior, and TA-7 load/performance were not reproduced and are not declared closed. This note does not close ToolQ G1/G3 or any four-sprint campaign.
