# T4 return decisions — 2026-11-13

Applies to the follow-up to commit `b794f63b79845c2bb0afb565e6fb43b3097ce2e5`.
Codo verified the six gaps in `CODO_T4_RETURN_NOTE_20261112.md` against that commit.

## Cleanup contract — D-P1b

The amendment in the return note is accepted. `cleanup_test_session` releases an explicitly identified, owned page. It closes launched resources and detaches an attached application. A non-empty `processes` argument or `close_session_after=true` produces `CLEANUP_SCOPE_UNSUPPORTED` before a release is attempted.

D-P1b is a contract amendment. The original leg (b), which requested PID cleanup and session closure, has been removed from the commissioned capability. Its delivery status is **removed from scope**, and must not be counted as a passing capability.

The wrapper identity repair and a demonstrated recurring process-cleanup need remain prerequisites to reconsidering a PID ledger. A future ledger design would also have to establish PID reuse protection and restart semantics. Session closure can be requested separately by exact session ID.

## HTTP schema authority — G3 / NP5

The served HTTP schema and bridge preflight define the public HTTP contract. Python defaults also support internal calls. The schema keeps the following explicit requirements.

| Tool | HTTP-required arguments beyond the Python signature | Reason |
|---|---|---|
| `cdp_attach_target` | `target_id` | Bridge preflight requires a named target and a non-empty expected title or URL before connecting. A WebSocket-only HTTP request is refused. The direct handler retains its WebSocket-only form for internal callers. Browser-level fallback over HTTP requires `allow_browser_level_fallback=true`. |
| `desktop_click` | `x`, `y` | Coordinate mouse events are sent even when `target_selector` directs the optional DOM click. Both coordinates must be deliberate caller input. |
| `fill_folder_dialog` | `folder_path` | HTTP callers name the requested path. The runtime also refuses an empty path before setting the Edit control. |
| `cleanup_test_session` | `page_id` | Release operates on the caller's identified page. |
| `open_browser_page`, `desktop_send_text`, `save_log_snapshot` | `project_path` | Preserve the public project/session context contract for browser creation, desktop input and log output. |

The public handler for `fill_folder_dialog` is `code_tools.TOOL_MAP["fill_folder_dialog"]`, the `code_tools.fill_folder_dialog` wrapper. It forwards to `orchestrator.runtime.desktop_dialogs.fill_folder_dialog`. Their parameter names and defaults are checked for equality. The wrapper and runtime both default `folder_path` to an empty string, so that default alone does not justify relaxing the HTTP requirement.

`query_dom_candidates` is a dispatcher alias through `search_code(mode="dom_candidates")`. Schema parity is measured against its final `tools_v15.query_dom_candidates` handler. The existing bridge-through-registry test covers this forwarding path.

The parity gate covers the union of `BRIDGED_TOOLS` and `DISCOVERY_TOOLS`, currently 35 tools. It checks exact parameter-set equality after removing transport-only `fields` projections and `definition_version`. Required arguments must equal the Python-required set plus the explicitly recorded exceptions above. It will fail if either side drifts.

## Return gap disposition

| Gap | Verified change |
|---|---|
| G1 / P7 + NP3 | Four `see_also` edges and the task/error-specific alternatives now lead to HTTP-routable tools. The catalog, session kickstart and Electron documentation identify the applicable transport. Additional page-ID descriptions now point to HTTP page producers. A test exercises the actual `/api/tools` handler and CA guidance. |
| G2 / NP4 | The external findings register states the distinction in both passages. `describe_page` directed HTTP callers into the blocked `query_dom_candidates` flow. `click_selector` belonged to the excluded macro flow. The final CDP count also distinguishes an unrouted launch tool from an attach tool lacking a running target in that measurement. |
| G3 / NP5 | The deliberate HTTP requirements and handler authority are recorded above and checked across the T4 family. |
| G4 / K6 | Failed `desktop_send_text` responses retain the resolved window title, including when the window disappears after title resolution. Mock tests cover focus refusal and Win32 failure. |
| G5 / K10 | Deprecated-field and unsupported-field duplicate responses reuse the same message as the first error. Dispatcher tests compare the two responses and preserve original error identity and correction fields. |
| G6 / L3 | The compatibility helper documents its `agent\|session\|task_epoch` test key and the live `session:agent` key. |

G2's findings register is outside this Git repository at
`D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\T4_REALTEST_FINDINGS_20261110.md`.
Its post-edit SHA-256 is `f27c317ebc08e62efc20cab9aab32e656889bf7b12676c6bb6755e2b2dbd3084`.

## Other accepted return follow-ups

- Cleanup recipes use the owned page ID. The result carries `already_unavailable` through from the close handler.
- Tests pin `CLEANUP_SCOPE_UNSUPPORTED`, the idle-guard proceed path, 45 ms key cadence, and title-based focus diagnostics.
- Reaper and status docstrings describe their current behavior. Inventory comments refer to the runtime registry.
- The P3b removal enumeration is four sites at the handed-over commit, including post-result synchronization.
- `ensure_single_electron_instance` still contains image-wide process termination. Its description now states that scope accurately. The separately proposed redesign remains pending.

## Verification boundary

The return is checked with local code and mocked desktop/Playwright behavior. Full-suite follow-up belongs to Muse. Runtime confirmation, merge and daemon restart are separate work in the return note. P8 and PC-40 require runtime evidence. NP7/K7 two-identity probes depend on the wrapper identity repair.
