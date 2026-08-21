# [A] Maintenance Doctor

Symptom-first triage for the next CA that has to maintain or upgrade [A].

Every entry below was a real incident on this box, and every one of them is here
because it was **misdiagnosed at least once**. The pattern is consistent enough to
state as a rule:

> When [A] looks broken, the first plausible cause is usually wrong. Measure the
> thing you are about to blame before you change anything.

Each entry gives the symptom as it actually appears, the cause that was assumed,
the cause that was real, and the test that now keeps it fixed. If you change code
that one of these tests covers and the test fails, **the test is probably right** —
these are not scaffolding, they were all mutation-verified (see
[Mutation testing](#mutation-testing-the-acceptance-bar)).

---

## Quick triage table

| Symptom | Assumed cause | Real cause | Pinned by |
|---|---|---|---|
| Whole suite dies at collection, `WinError 5` on a temp path | OneDrive syncing `%TEMP%` | Stale unreadable `pytest-current` symlink; `Path.exists()` raises EACCES | `test_conftest_stale_symlink_tolerance.py` |
| Unrelated folders "locked", Explorer hangs | A process holding a handle | `D:` is a HDD, saturated by a pytest run using a `--basetemp` on `D:` | same file, last test |
| `test_append_jsonl_...` flaky in full suite, passes alone | Lock bug in `jsonl_store` | Test `join(timeout=10)` was shorter than the module's own `LOCK_TIMEOUT_SECONDS = 15` | `test_orchestrator_storage.py` |
| Live CA session loses `CA_SESSION_BOOTSTRAP.md` mid-flight | Flaky test / test pollution | Cache reindex pruned scoped session artifacts; name-scoping drift between two modules | `test_scoped_session_artifacts_survive_reindex.py` |
| `git_status`/`git_stage`/`git_commit` truncated at ~63k | Transport limit, unavoidable | `records` was unbounded and `staged_hash` sat *after* it in the JSON | `test_git_status_payload_and_auto_hash.py` |
| `git_commit expected_staged_hash="auto"` → `GIT_STATE_STALE` | A real race | `"auto"` was compared as a literal string against a sha256, so it always mismatched | same file |
| A script/command deleted files nobody named | Authorization gap | Authorization was never missing — it was a mistaken call by an *authorised* caller; no confirmation gate existed | `test_destructive_script_guard_case2008.py`, `test_destructive_command_guard_case2008.py` |
| MCP tools stale for a whole session | Needs restart | Generation seen was never refreshed after a legitimate generation change | see `KNOWN_BUGS.md` (TA-5) |
| `git_commit` over HTTP omitting the hash | B006 regressed | Schema `required` refused first, and that error cannot carry `current_staged_hash` | `test_git_status_payload_and_auto_hash.py` |
| `query_sqlite` answers `QUERY_SQLITE_ERROR: database is locked` | the DB is corrupt or the tool is broken | Neither. Another process holds the write lock; since B009 this answers `SQLITE_DATABASE_LOCKED` with `retryable:true` — wait and resend the same call | `test_p4_soft_data.py::TestQuerySqlite` |
| `write_fix` on a large file client-times-out; nobody knows if it landed | The write was lost; resend it | It almost certainly landed. The ledger opens before the tool runs, so `/api/write_status?project_path=..&file_path=..` answers `in_flight` then `committed` — a blind resend writes the file twice | `test_write_ledger*.py`; measured 10MB: committed at t+54s after a 4s client timeout |
| A tool works over HTTP but answers `UNSUPPORTED_FIELD` over MCP | The payload is wrong | It is not. The MCP bridge is a separate long-lived process; before B010 it screened the payload against the registry it loaded at launch. Restart the MCP client, and if the gate is back, the fix regressed | `test_mcp_gate_uses_live_schema_b010.py` |
| `cbm_status` says `stale_files: N` right after a `completed` full index | The index is broken / flaky | Two counters over two populations: the index walks **disk**, the count reads the **files table**. Before B011 a full index never deleted the row of a vanished file, so it could not converge | `test_graph_index_prunes_deleted_files.py` |
| A JS/TS repo reports `dependency_edges: 0` with hundreds of callables | tree-sitter is not installed / JS is unsupported | It is supported. `require()` is a `call_expression` and never reached the import path, **and** ESM emitted path-shaped keys (`./lib/util.js.helper`) whose prefixes can never match `lib.util` — two gaps, either alone keeps the zero | `test_js_dependency_edges.py` |
| `validate_file` returns `ok: true` for a file you know is broken | The individual checker is broken | Check the `lang` you passed and the `checked` field. Before B012 any unrecognised spelling (`JSON`, `javascript`, `node`, `py`) fell through to `ok:true, lang:"none"` with nothing run; now it refuses with `UNSUPPORTED_LANG`, and `checked:false` marks a genuine pass-through | `test_validate_file_unknown_lang_fails_loud.py` |
| `cbm_dead_code`'s `file_filter` changes nothing | The glob is wrong / too broad | Test it with a deliberately **bogus** glob: identical output proves the argument is inert, not broad. Before B014 the resolved glob was dropped by the non-Python early return, and hard-filtered to `.py` on the Python path. Now `files_matched: 0` is visible | `test_cbm_dead_code_file_filter.py` |
| `cbm_dead_code` on a JS repo returns nothing for `bug_risk` / `perf` / `code_smell` | The repo is clean | Those categories are Ruff-only, so on a JS/TS repo they are empty **by construction**. Read `engine`, `engine_reason` and `language_detected` — since B015 every answer says which engine ran and why | same file, `test_the_engine_name_says_what_actually_ran` |
| A fix to an extractor changes nothing on a real repo; a full index says `files_indexed: 0` | The fix is wrong / the index is cached | The source bytes did not change, so `stale_files` saw nothing stale. Bump `config.EXTRACTOR_VERSION` — an extractor change and a version bump are **one change** (B016, and B013's W5 predecessor before it) | `test_extractor_version_pins_js_import_shape.py` |
| `log_native_event` answers `logged_and_rejected` / `HOST_GATE_DENY` for an ordinary action | the native edit was not authorised | Check the `action_type` spelling. `classify_action` resolves it through a lookup table; a word with no row falls through to `task_class: "unknown"` -> `native_policy: "forbid"`. Before B017, `create_file` was refused inside [A] while `write_existing_file` on the same file was allowed | `test_policy_action_vocabulary_b017.py` |
| A `search_code` call with real hits reads as a failure | the caller guarded on `ok`, which the success path omitted | B018: every failure branch answered `ok:False` and the success branch answered nothing, so a missing key was falsy exactly like `False`. A search that RAN is `ok:True`; "found nothing" is carried by `count`/`stale_index` | `test_search_ok_field_b018.py` |
| `regex_search` refuses `path` -- and its own budget error told you to narrow by path | the alias is the same tool as `search_code(mode='regex')` | B019: it was not. The alias's signature had no `path`, **and neither did its registry entry -- which is the one the dispatcher checks** (`orchestrator_server.py:13422`, "S4: registry properties are the public contract"). Adding the parameter alone left the live answer unchanged. Also the `REGEX_INPUT_BUDGET_EXCEEDED` answer echoed the failing unscoped call back as its `payload_example`. Pass `path` (now accepted) or `file_pattern`; both are listed in `accepted_narrowing_fields` on the refusal itself | `test_regex_search_path_b019.py` |
| You added a parameter to a tool, the suite is green, and HTTP still answers `UNSUPPORTED_FIELD` | the daemon is stale, so restart again | Restarting will not help. **A tool's public contract is its registry entry, not its Python signature** -- `orchestrator_server.py:13422` builds `allowed_params` from `_schema_for_tool(tool)`, so a parameter absent from `orchestrator/core/registry_data.py` is refused before the function is ever called. Add it in both places, and pin it with a test that reads the registry (B019's third half; `B-01` in the same file had already paid for it once) | `test_regex_search_path_b019.py::TheRegistrySchemaIsThePublicContract` |
| A council `room_token` is `ROOM_TOKEN_INVALID` milliseconds after it was issued | the clock or the TTL is wrong | B020: `_room_register` floored the START of the window (`int(time.time())`) while validation also floors the END, so the caller was charged for the fraction of the second already elapsed -- a `ttl_sec=1` token registered at `t=100.95` was dead by `t=101.01`. The lifetime was `ttl_sec - (time.time() % 1)`, which is why it passed 10/10 alone and failed inside the slower full suite | `test_room_token_ttl_b020.py` |
| A timing test fails by a hair (`0.391 >= 0.4`) and passes on rerun | "flaky, rerun it" | `time.sleep(0.4)` on Windows returns in as little as 0.391s — timer granularity. Asserting a caller's wait against the **nominal** sleep measures the clock, not the fix. Have the callback time ITSELF and assert the caller waited at least that long, plus a generous multiple of the budget the regression would have cut it off at. Verify the loosened bound still catches the real regression by restoring it | `test_session_store_writer.py::test_slow_but_progressing_writer_is_not_killed_by_a_short_budget` |
| Six defects shipped green; you need to know whether a fix reached the *real* repo | "the suite passes, so it works" | A fixture repo is the one place all six answers are honest. Run `scripts/live_answer_doctor.py --project-path <a real, already-indexed, non-Python repo>` after every restart that touches an extractor, an index path, a fallback, or a filtering argument | `test_live_answer_doctor.py` |

---

## When a write times out on the client

This is the single most-misread failure in [A], and it is not a failure. `write_fix` / `write_transaction` on a multi-megabyte file routinely run past the caller's socket timeout: on a 9.9MB file a normal `write_fix` measured **70.6s**, and the wall clock is owned by the post-write symbol check (27.5s with `skip_symbol_check=true`), not by the write or the backup — `backup_override` saved ~0.6s of that, and `backup_project` already skips when today's backup exists.

The server does not care that the client left. So:

1. `GET /api/write_status?operation_id=<id>` if you got a response carrying one. If you never got a response, look the operation up by target instead: `?project_path=<root>&file_path=<file>`.
2. Read `commit_state`, not `ok`: `in_flight` means keep polling, `committed` means it landed, `not_committed` means it is safe to retry. `safe_to_retry` and `ca_next_action` say the same thing in one field.
3. Only resend when `commit_state` is `not_committed`. A resend during `in_flight` is a second full write of the same file.

Measured end to end against the live daemon (10MB file, deliberate 4s client timeout): the client raised `TimeoutError` at 4025ms, the target lookup answered `in_flight` / `safe_to_retry: false` at t+4573ms, and flipped to `committed` at t+53961ms with `bytes_written: 10337814` and `validation_ok: true`. The recovery path was complete the whole time — what was missing, until 2026-08-20, was any document telling a CA it existed. If you are tempted to make the write path asynchronous to "fix" this, measure first: the answer is already there.

## When one transport accepts a field and the other refuses it

The MCP stdio bridge is **not** the daemon. It is a separate process that imports
`orchestrator_server` once at launch and keeps that registry for its whole life,
which can be many hours. The daemon restarts; the bridge does not.

So `mcp_generation` in an MCP response is the *bridge's* stamp, not the daemon's.
Seeing it differ from `/health` is normal after a daemon restart — TA-5 made the
bridge re-baseline instead of failing closed, which is why calls keep working.

What that means when triaging:

1. **Trust the daemon's answer, not the bridge's.** Since B010 there is no local
   schema screen: an MCP call proxies to the daemon and the daemon's registry
   decides. A refusal you get over MCP is the daemon's refusal.
2. **If MCP refuses something HTTP accepts, the fix has regressed.** Compare
   `GET /api/tools/<tool>/schema` (daemon, authoritative) against MCP's own
   `tool_schema` (bridge, possibly stale). If the daemon lists the field and MCP
   does not, that difference is expected — but it must no longer change the
   *verdict* on a call.
3. **Restarting the daemon does not refresh a bridge.** A bridge started before a
   registry change keeps the old schema until its own process restarts. That is
   acceptable for `tool_schema` listings; it must never decide a call.

Do not "fix" a stale bridge by re-adding a local screen. Two processes answering
for one tool need one authority, and it is the process that runs the handler.

## Running the test suite

```powershell
venv312\Scripts\python.exe -m pytest tests\ -q
```

**Do not pass `--basetemp` pointing at a fixed path, and never one on `D:`.**

This is the single most important line in this document, because the opposite
advice was in `README.md` for months and did real damage:

* `D:` is a **HDD**. Routing a ~2430-test suite's temp I/O onto it saturated the
  spindle so badly that unrelated directories stopped opening in Explorer, and
  `du -sh` on a sibling folder hung past 120s — then completed in 1s the moment
  pytest stopped. It looked exactly like a locked folder. It was a busy disk.
  Rank a slow-disk suspicion by **% Idle Time / IOPS**, not by bytes.
* A **fixed** basetemp path is also shared mutable state between concurrent runs.
  pytest wipes its basetemp at startup, so two agents running the suite at once
  delete each other's fixtures. `test_graph_enabled_live_flag_a13.py` records this
  as its own past bug.

`--basetemp` as a *flag* is fine and is used deliberately in four places — all of
them resolve through `tempfile.gettempdir()` or `%TEMP%`, i.e. SSD and per-run.
The last test in `test_conftest_stale_symlink_tolerance.py` enforces exactly that
distinction repo-wide: hardcoded drive letters fail, runtime-resolved paths pass.

### The default temp dir is correct

`%TEMP%` / `%LOCALAPPDATA%\Temp` is on SSD and is **never** OneDrive-redirected.
If you see a `WinError 5` under it, you are looking at the stale-symlink bug
below, not at a sync client.

### A single known-flaky exception

`test_council_runtime_status_tracks_terminal_state` reads a shared council-runtime
file. Run the suite with the daemon **idle** — a live dispatch for the same agent
can flip it to an environmental failure unrelated to source state.

---

## Entry 1 — Suite dies at collection with `WinError 5`

**Symptom.** Not one test fails; the whole run aborts before any test executes:

```
PermissionError: [WinError 5] Access is denied:
'C:\Users\ADMIN\AppData\Local\Temp\pytest-of-ADMIN\pytest-current'
```

**Why it is easy to misread.** There is no smaller signal. Nothing gets far enough
to report, so it presents as "pytest is broken" or "the environment is broken".
The path is under `%LOCALAPPDATA%\Temp`, which reads as "OneDrive is syncing my
temp dir" — and that conclusion produced the `--basetemp=D:\...` workaround that
saturated the HDD. `%LOCALAPPDATA%` is not OneDrive-redirected. The symlink was
always the whole cause.

**Real cause.** `pytest-current` is a symlink left behind by a killed run. On
Windows an orphaned symlink can be readable-but-not-stat-able, and
`Path.exists()` **propagates EACCES instead of returning False**.
`_pytest/pathlib.py::cleanup_dead_symlinks` calls `.exists()` in a bare loop, so
pytest's own temp-dir garbage collector takes the session down.

**Fix.** `conftest.py` (marked `A54`) replaces `cleanup_dead_symlinks` with a
version whose fallback loop is **per-entry**. Two deliberate choices:

* Not a single `try/except` around the original call — that stops collecting
  garbage at the first unreadable entry, leaking every dead link behind it.
* Not "delete the symlink and move on" — that is a one-off the next killed run
  recreates. The upstream loop is simply missing an except clause; supply it.

**If you must intervene by hand,** deleting
`%LOCALAPPDATA%\Temp\pytest-of-ADMIN\pytest-current` is safe and unblocks the run
immediately — but that is a symptom fix, and the shim is what makes it not recur.

---

## Entry 2 — A test that only fails in the full suite

**Symptom.** `test_append_jsonl_parallel_process_thread_keeps_all_lines` fails with
`assert not thread.is_alive()` under `pytest tests/`, and passes 3/3 in isolation.

**Why it is easy to misread.** "Passes alone, fails together" is the signature of
test pollution or a genuine concurrency bug, and `jsonl_store` has a documented
history of both. Reaching for the lock implementation is the natural move.

**Real cause.** The test's deadline was tighter than the contract it was testing.
`jsonl_store.LOCK_TIMEOUT_SECONDS = 15.0` — a contended append is *allowed* to
wait 15s — but the test gave up at 10s. On top of that each append pays one
`fsync`, measured at 125ms on this disk, so 50 appends is ~6s of pure platter time
per worker before any contention at all. The deadline could not be met even with
nothing wrong.

**Fix.** The join deadline is now **derived from the module constant**
(`LOCK_TIMEOUT_SECONDS + 50 * 0.5`) rather than hardcoded, so raising the module's
budget can never silently reintroduce a deadline tighter than its own contract.
The `assert not thread.is_alive()` stays: a lost append or a self-deadlock hangs
forever, and the deadline is what catches that. Only the *arbitrary tightness* was
the bug.

**General lesson.** Before blaming a lock, check whether the test's timeout is
larger than the wait the implementation is documented to perform. A timeout is an
assertion too, and it can be wrong.

---

## Entry 3 — A live session loses its own bootstrap

**Symptom.** One "flaky" `FileNotFoundError` on `CA_SESSION_BOOTSTRAP.md` in the
full suite.

**Why it is easy to misread.** It presents as a single flaky test. It was neither
flaky nor a test problem — it was **production data loss**, and the test was the
only thing noticing.

**Real cause.** Name-scoping drift between two modules that never reference each
other. `surgical_mapper.PRESERVED_CACHE_FILES` was written when session artifacts
sat flat in `.orchestrator_cache`, so the preserve check compared the *whole*
relative path. `code_tools._scoped_session_artifact_path` later moved them to
`sessions/<label>-<digest>/`. After that move the exact-match check no longer
recognised them, and `_reindex_from_disk` — which runs on **every**
`generate_project_map` and every periodic refresh — deleted the bootstrap of every
live CA session.

`WORKFLOW_STATE.json` survived, which is why nobody noticed: not because it was
preserved, but because the prune loop skips a name whose stem has no extension
left after stripping `.json`. An accident.

**Three layers, all pinned.** The final name, the *basename at any depth*, and the
in-flight `.<name>.<random>.tmp` that `_atomic_write_bytes` writes beside its
target — preserving only the final name still deletes a file the writer holds a
path to.

**Trap for the next fix.** Two obvious test shapes prove nothing here, and both
were tried:

* "prepare → reindex → prepare again" passes even with the fix reverted, because
  the second prepare simply rewrites the file it lost.
* `generate_project_map` without `force=True` returns a "skipped" payload when the
  cache is fresh — and `prepare_ca_session` has just made it fresh. **A prune that
  never ran proves nothing about pruning.**

---

## Entry 4 — Git tool payloads truncated, and `"auto"` refused

**Symptom.** `git_status` / `git_stage` / `git_commit` all returned past the
transport's ~63k ceiling and arrived TRUNCATED; and `git_commit` with
`expected_staged_hash="auto"` was refused as `GIT_STATE_STALE` even though the
index had not changed.

**Real cause — one causal chain, which is why they were fixed together.**
`records` was 89% of the payload (~216 chars/record) and the only unbounded part.
Worse, `staged_hash` sat *after* it in the JSON, so a response cut at the limit
lost exactly the field `git_commit` requires — and an unreadable hash is why a
caller reaches for `"auto"` in the first place. `"auto"` then failed because it was
compared as a literal string against a sha256.

**Fix, and the invariants that matter.**

* `records` is capped by `max_records`, and the cap is **reported**
  (`records_truncated`), never silent. `0` means uncapped and is the default, so
  in-process callers that walk `records` are unaffected.
* `counts` / `state_hash` / `staged_hash` are computed over **every** record and
  moved **ahead** of `records` in the JSON. A capped read and a full read are
  therefore interchangeable for every decision a caller makes.
* `"auto"` is an explicit opt-out of the staleness check — **not** of every other
  check. An empty index is still `NOTHING_STAGED`, and a genuine mismatch is still
  refused.
* A missing hash now says `EXPECTED_STAGED_HASH_REQUIRED` instead of blaming
  staleness, and carries `current_staged_hash` so the corrected retry needs no
  second call.

**If you add a parameter to a git tool:** it must go in the **registry schema**,
not just the Python signature. `allowed_params` is built from the schema, so a
signature-only parameter is unreachable over HTTP (`UNSUPPORTED_FIELD`). The last
test in the file asserts exactly this.

---

## Entry 5 — CASE-2008: a delete nobody named

**Symptom.** An agent ran a script that deleted files it was never asked to
delete, and the output that would have shown the damage was truncated.

**Why authorization was never the missing part.** The caller *was* authorised. The
mutation token proves authorization, not intent. A token-gated tool with no
confirmation gate is exactly what CASE-2008 was.

**Two entry points, one filesystem.** The incident was fixed in
`run_project_test_script` (which `run_ps1_script` / `run_node_script` wrap, so one
gate covers every script caller). But `run_project_command` is a **separate** entry
point to the same filesystem, and `rd /s /q junk` through it returned `ok` with
exit code 0 and really removed the directory. That gap is the same shape as the
incident's root cause: a safety property on one tool and not its sibling.

**Properties now enforced, and why each exists:**

* **Whitelist, never blacklist.** Delete only what is named. `dry_run=true`
  previews with `executed:false`; `confirm_destructive=<destructive_count>`
  acknowledges; a **stale count is refused** (it is a count, not a boolean, so
  editing the command invalidates the acknowledgement).
* **`DESTRUCTIVE_SCRIPT_FOOTGUN` is not confirmable.** PowerShell discards
  `-Include` when `-LiteralPath` is given, so there is no count worth
  acknowledging — only a command to rewrite.
* **One tool, four interpreters.** `run_project_command` runs cmd, PowerShell,
  `node -e` and `python -c`. Scanning with a single runtime's pattern set was
  *measured* to miss `fs.rmSync`, `fs.unlinkSync`, `shutil.rmtree` and
  `os.remove`. The gate scans the union.
* **Encoding and list form are transport details, not privilege levels.** A
  base64 body is scanned after decoding; a list `command` is scanned as the string
  it becomes.
* **A destructive run never truncates its own output.** Truncation is what hid the
  damage (68,978 chars became 4,000 and the two filenames fell off the end). Note
  the fix could not be "set the cap to 0": on the auto-route paths that value is
  passed on as `stdout_max_chars or 12000`, which would restore a cap. The
  no-truncate intent is a flag, and the test asserts the **outcome**.
* **A failed backup refuses the run.** Proceeding would show backup machinery in
  the response and imply a recoverability that does not exist.
* **The gate is shared, not copied.** `_destructive_refusal` /
  `_destructive_backup_or_refusal` are used by both entry points, and a test
  asserts each error name is raised from exactly **one** place. Two copies with a
  future fix applied to one would be the same bug again.

**Cost check, so the gate is not read as a tax:** 34 real call sites in this repo
were measured against it — **0 flagged**.

---

## Entry 6 — The mutation token carries a mini rule

`GET /api/token` and `/api/token/refresh` return a `mini_rule` field, and so do the
`MUTATION_TOKEN_REQUIRED` / `MUTATION_TOKEN_EXPIRED` refusals. Two sentences:
whitelist-only deletion, and fail loud.

The reasoning: **the token IS the destructive capability.** Every other layer that
says "do not delete what was not named" lives somewhere the agent has to go
looking. This one rides on the handout, so it arrives at the exact moment the power
does — and again on the refusal, which is a CA's most likely first contact with the
destructive tier.

**Bounded on purpose: exactly four call sites, and a test counts them.**
`_error_response` splats `**extra` into the payload, so a fifth `mini_rule=`
would start appearing on unrelated errors and turn the reminder into noise. If you
need it somewhere new, update the count in the test deliberately.

---

### If you add a line to "fix" something, make a mutant delete it

B009's first patch added `PRAGMA busy_timeout=...` next to a
`connect(timeout=...)` that already installed the same busy handler. Every test
passed. The mutant that deleted the new line also passed every test — which is
the definition of a statement that does nothing.

Run the mutation step even when the fix looks obvious, and read a **survivor** as
a question, not a formality: either the test is too weak, or the code is
decoration. Both answers are worth having before the commit.

### If you add or tighten a `required` list

Ask which layer should refuse. The generic validator runs **before** the tool, and it
knows nothing about the tool's domain -- so a field listed as `required` can only ever
produce `MISSING_REQUIRED_FIELD`, never the domain error that carries a usable retry
value. B008 was exactly this: `expected_staged_hash` was schema-required, so
`EXPECTED_STAGED_HASH_REQUIRED` -- with `current_staged_hash` attached, the whole point
of B006's fix -- was unreachable from every HTTP caller. In-process tests could not see
it, because they call below the validator.

Rule of thumb: **`required` is right when the generic error is the best error.** If the
tool can say something more useful, leave the field optional with a falsy default and
let the tool refuse.

And when you fix something on the HTTP surface, **test it on the HTTP surface.** Eleven
in-process tests passed over this bug.

---

## Entry 7 — Auditing [A] against a non-Python repo

Almost every analysis tool in [A] was built and tested against [A] itself — a
Python repo. Point it at a JS/TS repo and a class of defect appears that a
Python-only test suite structurally cannot see. Five were found this way on
2026-08-21 (B011-B015). The pattern behind all five is worth naming:

**A tool that cannot do something answered as though it had.** The engine fell
back without saying so (B015). The glob was resolved and discarded (B014). The
validator passed a file it never opened (B012). The extractor emitted a key that
could never match (B013). The index reported success while leaving rows behind
(B011). None of them errored. Every one of them returned `ok`.

So the audit technique that actually worked was not reading code, it was
**asking each tool to prove it did the thing**:

* Pass a **deliberately impossible argument** and check the answer differs. A
  bogus glob returning the same 207 candidates as no glob is what exposed B014 —
  the "correct" glob returning a plausible number had looked fine for a long
  time.
* Pass a **misspelling of something supported** (`JSON`, `javascript`, `node`).
  B012's terminal `else` had swallowed those for as long as it existed, and three
  of them name languages the tool really implements.
* **Count the same thing two ways.** B011 is nothing but two counters
  disagreeing; the disagreement was visible in one payload for months.
* **Check the number moves at all.** `dependency_edges: 0` is not a small number,
  it is a missing feature — and a repo with 426 callables and zero edges cannot
  both be true.

### If you fix one of these, fix it at extraction time

B013's real lesson: the ESM key was wrong because it was built where the
importing file's directory was still known, and consumed where it no longer was.
Normalising later is impossible, not merely awkward. When a value has to be
canonical for a later join, canonicalise it at the point where you still hold the
context that makes it canonical.

### What is deliberately still not fixed

On a JS/TS repo, `cbm_dead_code`'s `bug_risk` / `perf` / `complexity` /
`code_smell` / `tech_debt` / `security` categories come back **empty**, because
they are Ruff findings and Ruff does not read JS. Since B015 the response says
which engine ran, so this is now visible rather than mistakable for a clean
repo — but it is a gap, not a fix. Anh deferred the JS equivalents explicitly
("1 số tool python sẽ không bắt được, nâng cấp sau"). Do not read an empty
`bug_risk` on a JS repo as good news.

Likewise `unresolved_imports` on a JS repo counts bare package specifiers
(`fs`, `express`) and that is correct: resolving them to same-named local files
would be worse than leaving them unresolved. A test pins that restraint, so if a
future change makes that number drop to zero, check it did not start colliding
node_modules names with project files.

---

## Entry 8 — An extractor change and a version bump are one change

B013 fixed the JS import shape and added `require()` extraction. Its 11 tests
were green, the full suite was green, the commit was clean, the daemon was
restarted. On the actual reported repo the fix did **nothing**:

```
cbm_index(scope="full") -> completed, files_indexed: 0, files_unchanged: 41
cbm_template_query(module_deps) -> 0 rows
```

`store.stale_files` re-extracts on changed content, never-indexed, **or** a
differing `extractor_version`. B013 changed what extraction *produces* from
unchanged bytes — nothing a content hash can see. Every row matched, nothing was
re-extracted, and the graph kept its path-shaped keys. Filed as B016; the bump to
`ast-p0-v9-js-imports-are-modules` is the whole fix.

**This is the second time.** `store.stale_files`'s docstring already recorded the
W5 case: 241 of 250 [A] files frozen at the pre-`name_literal` shape through a
full reindex. A comment did not prevent the recurrence, so it is now a test.

### If you touch an extractor, ask two questions

1. **Can unchanged bytes now produce a different node or edge?** New kind, new
   key shape, new resolution rule — if yes, bump `config.EXTRACTOR_VERSION` in
   the same commit and say why next to it.
2. **Did I verify against a repo that was already indexed?** A fresh `tmp_path`
   fixture passes either way, because it has no rows to be stale. That is
   precisely why 11 green tests coexisted with a live repo at zero edges. The
   cheap version of this check is to index a fixture twice, rewrite
   `files.extractor_version` to an older string, and index again asserting
   `files_indexed > 0` — which is what
   `test_extractor_version_pins_js_import_shape.py` does.

Corollary for reviews: "the tests pass" and "the fix reaches production data"
are different claims. Anything cached, hashed, or memoised sits between them.

---

---

## Entry 9 — A payload that describes its own contents wrongly

Entry 4 capped `git_status`'s `records` and reported the cap honestly. B021 is
the same wound one level down: **the cap made the payload smaller without making
any record smaller.** Every record carried `raw` — the porcelain line — beside
`record_type`/`xy`/`sub`/`path`/`orig_path`, which are the fields parsed *out of*
it. ~71% of a record's bytes were a second copy of the same information, and 270
records came to 126,770 bytes and got cut. Nobody had counted a record's bytes
against the *information* in it.

The second half of B021 was `cbm_template_query(module_deps)` answering
`count=12`, `truncated=True`, `total_count=0` — the two fields a paginating caller
chooses between, contradicting each other, because they were **set
independently**. `total_count` is now the pre-cap population and `truncated` is
**derived** from it. Two fields that must agree should be one number and a
derivation.

### The four questions this entry exists to make you ask

1. **Does any field in this payload restate another?** If a record carries both a
   raw line and the fields parsed from it, one of them is waste on whichever part
   of the payload is unbounded — and that is the part that gets truncated.
2. **Can two fields in this payload disagree?** If yes, derive one from the other
   at a single site. `truncated` and `total_count` disagreed for as long as they
   were two assignments.
3. **Is the tidied value also a value someone holds?** `state_hash` is a sha256
   over `{'branch','records'}` and callers hold it *between* calls as an
   optimistic lock — `git_stage` refuses with `GIT_STATE_STALE`,
   `git_stale_check` reports `state_hash_mismatch`. Hashing the tidied records
   would shift every hash at once and make every held lock read as a concurrent
   modification that never happened. So `raw` is dropped from the **response**
   and kept in the **hash input**. **Tidying a payload must not be a protocol
   change.**
4. **Can the caller actually supply the field the answer is derived from?** B021
   derived `truncated` from `offset + count < total_count` — and `offset` could not
   be passed. It was missing from `cbm_template_query`'s signature *and* from its
   **registered schema**, which is the one the server's unknown-field gate reads
   (`orchestrator_server.py:13482`), so a signature-only fix still answers
   `UNSUPPORTED_FIELD`. Measured live at gen `94fa01936e89`: the same daemon that
   reported `total_count=619` refused `offset=500`, so rows 501-619 of a total it
   had just started reporting honestly were unreachable. Internally six of seven
   branches ignored `offset` too, so they would have answered **page 1 forever**
   while `truncated` said “keep asking”. **An offset-aware derivation whose offset
   cannot leave 0 is not offset-aware.**
   `tests/test_graph_ext_edges.py::test_offset_pagination` had pinned offset paging
   since E3 — at the **function** level. *A test that calls past the layer holding
   the defect cannot see it*, and this is the second campaign to learn it (B019's
   registry schema was the first).

### Probing the fix found the actual severe defect

In `-z` mode a rename/copy record (type `2`) is **two NUL-separated fields** —
the record, then the original path — because a path cannot carry the tab that
separates them in the non-`-z` format. `git-status(1)` documents this. The
splitter treated the second field as a record of its own, so every rename made a
**phantom record** (`{'record_type': 'o', 'raw': 'old.txt'}`) counted in
`counts['changed']`, `orig_path` was **always `''`**, and when the old path began
with `u`, `1`, `2` or `!` the phantom was parsed as an unmerged/ordinary/ignored
record and `rec.split(' ',10)[10]` raised **IndexError**. Measured:
`git mv util.py helper.py` made `git_status` **raise**, and `git_stage` and
`git_commit` both call `status()` first, so all three went down together.

Nobody reported this. It was found by writing a fixture that produced *every*
record type instead of the one the report happened to contain. **A fixture that
covers only the reported case verifies only the reported case.**

### The rule: a fixture too small to distinguish the mutant is a test that measures nothing

B021's first cut was 13 green tests, and **three mutants walked through them**:

| Mutant | Why it survived |
|---|---|
| `total_count` falls back to `0` instead of `len(res)` | Nothing asserted on `impact_scope`, the **only** branch reaching the fallback. `count=1 total_count=0` — the exact reported contradiction — would have shipped in the template the fix deliberately exempted. |
| `COUNT` over raw edges instead of the `DISTINCT` pairs | Every fixture had one import per file, where the two counts agree. A file importing the same module twice gives 2 edges for 1 pair, so the mutant reports a total the LIMIT-ed select can never reach and `truncated` stays true on a **complete** read — a caller looping forever on the same last page. |
| `entry_points` counts `_all_eps`, not `_prod_eps` | The view drops test runners (CAN-053); the fixture had none, so both lists were the same length. |

Each got its own test. **The only way to learn that a fixture cannot see a defect
is to introduce the defect and watch the suite stay green.**

Two operational notes from doing it:

* **A mutant that did not apply is not a mutant that survived.** One patch failed
  on tab escaping and the suite printed `12 passed` — indistinguishable from a
  surviving mutant. Assert the patch applied (`assert s.count(old)==1`) before
  reading the result.
* **`orchestrator/git_tools.py` is CRLF** with 53 pre-existing lone-LF lines. A
  heredoc matching `\n`-joined text fails on it; splice by line index against
  `.split("\r\n")` and write with `newline=""`. Check `crlf`/`lone_lf` counts
  before and after so the fix does not smuggle in a line-ending change.

### And check your own assertions before blaming the fix

Two of B021's tests were wrong, not the code.
`assert "state_hash_mismatch" not in matched["stale"]` raised
`TypeError: argument of type 'bool' is not iterable` — `stale` is a **bool**, the
named causes live in `reasons`; read as a container it would have asserted nothing
on the happy path. And asserting that rewriting an already-`.M` file moves
`state_hash` was **false before the fix too** — porcelain is byte-identical for a
content-only edit, so it measured git's output rather than the lock. When a new
test fails, run it against the **pre-fix** module: if it fails there for the same
reason, the test is the bug.

### A docstring that documents the bug is part of the bug

`template_query`'s docstring said *"total_count is the true population for
paginated templates (orphan_symbols); 0 otherwise."* A reader checking whether
`total_count=0` was a defect would have concluded it was intended and closed the
report. There is now a test asserting the docstring does not promise `0`.

---

## Entry 10 — Two callers of one predicate, and only one of them passed what the predicate needs

B022 looked like a missing ignore-list. The ignore-list was there and correct. The
**caller** omitted the argument the ignore-list needs:

```
code_tools.py:3654      _should_scan_file(path)              # drift check  — NO project_root
surgical_mapper.py:1134 _should_scan_file(fp, project_root)  # indexer      — WITH it
```

`project_root` is what `_skip_reason` needs to compute `_depth_below_root` for the
`data_json_not_source` rule. Without it the depth is `None`, and R82's
**deliberate** conservative default keeps the file: *"depth unknown -> conservative:
index it rather than silently drop it."*

### The question this entry exists to make you ask

**Is the default that makes this predicate safe still safe for THIS caller?**

R82's default is not a bug. It is right for the indexer, where keeping a file means
indexing one file too many. It is **inverted** for the drift check, where keeping a
file means **"there is a new file here"** — a claim a refresh can never satisfy,
because the indexer will refuse the same file again. Measured on [A]'s own repo:
52 files where the two predicates disagree, `total_changed=53 added=52`, **52 of 52
`added` refused by the indexer, 0 genuinely addable**. Drift → refresh → refuse →
drift, forever.

A conservative default is directional. When a shared predicate has one, the
question is never "is the default sound" but "which way does this caller's cost
point". Two callers of the same helper can need the same default in **opposite**
directions, and the one that gets it wrong will not raise — it will report
confidently.

### One cause can wear two error names

The report named `STALE_INDEX_AFTER_REFRESH`. By the time the fix was written the
same call answered `INDEX_QUERY_TIMEOUT` ("budget 12.0s") instead: the phantom
refresh had grown to eat the whole `_query_index_deadline_seconds()` budget. Two
names, one defect. Treating them as two bugs would have sent the timeout
investigation into the scheduler and found nothing wrong there.

And only `query_index` died, because it is the only tool that gates **hard** on
drift — `search_code` measured `ok=True` with `cache_status` unaffected. **The tool
that fails is not the tool that is broken; it is the strictest consumer of what is
broken.** Do not scope the search to the tool that reported.

### Do not fix the same missing argument everywhere it is missing

Auditing the other call sites was the second half of this entry's value. All six
`cache_delta` consumers — `code_tools.py:1794`, `1912`, `3092`, `12599`,
`6113`/`6161` and `orchestrator/tools/search.py:1165` — route through the **same**
`_cache_delta_sync`, so one argument reached all of them. There was exactly one
other no-root call site, `orchestrator/fileops/common.py:41`
(`should_scan_for_text`), and its sign is the **opposite**: measured 1312 admitted
against the indexer's 1260 — 52 search-only files, all `.json`. For a text search,
keeping a file means finding a match; refusing it means silently missing one.

So the same missing argument is a defect in one place and the correct default in
the other. It was left alone, deliberately. **"The other caller is missing it too"
is a reason to measure that caller's direction, not a reason to patch it.**

### A test that asserts the parameter instead of the consequence can be unpassable

The first version of the invariant test asserted
`_should_scan_file(p) == _should_scan_file(p, root)`. That can **never** pass and
should not: those two must still disagree, because R82's no-root default is
deliberate and untouched. The fix was at the caller, not in the mapper. Written
that way the test failed, and the failure was the test's, not the code's — the
third time this campaign that a new test was the bug (see Entry 9's closing
section).

The honest form pins the consequence: **no file the drift check calls `added` may
be a file the indexer refuses.** Reverting `, root` kills **5 of 7** tests. The 2
survivors are by design and say so in their own docstrings — the guard-the-guard
(a fixture with nothing to refuse proves nothing) and the opposite failure (the fix
must not silence real drift). **Name the tests a mutant is supposed to survive, or
the next reader will "fix" them.**

### An entry point that assembles is not an entry point that builds

Six of these tests were green-then-wrong because `sm.build_index()` on a fresh tmp
repo returns **0 files** and writes no INDEX.json — it only *assembles* an index
from cache sidecars that already exist. They were measuring an empty index rather
than the drift rule. `generate_project_map` is the entry point that walks, writes
the sidecars and publishes INDEX.json. Measured: `build_index` → 0 files,
`generate_project_map` → 2 files, `total_changed` 0.

And the invariant test alone was not enough for a subtler reason worth copying: it
asks what drift reports against a **real** index, so it only sees files that happen
to be missing from it. An index that already held every phantom would hide the
defect. `test_the_walk_admits_nothing_the_indexer_refuses` passes
`index_override={"files": {}}` so `added` becomes the walk's complete admit-list —
**measure the predicate against an empty baseline when the baseline is what might
be hiding it.**

### Two suite counts are both correct

`pytest tests\` reports **2592**; a bare `pytest` reports **2619**. The 27-test gap
is three files at the **repo root** (`test_all_tools.py`,
`test_council_reply_fallback.py`, `test_open_session_drift_note.py`), collected by
rootdir discovery and outside the README's `tests\` baseline. Neither number is
wrong, and the collect count moves with which one you ran. Before concluding a
count drifted, check the **scope** — `--collect-only` plus `git stash` of the new
file separates "new tests" from "different set".

---

## Entry 11 — Five "schema traps", measured: four were my own wrong calls, one was a real silent answer

This entry was proposed as "document the 5 schema traps", because the same class of
mistake produced a wrong report twice in one campaign. Measuring them first turned
the entry inside out: **the schemas were right.** Each was checked against
`GET /api/tools/<tool>/schema` and against the live daemon at generation
`177faa8e5a45`, and only one of the five was a defect in [A].

The split is the whole value, so it is recorded honestly rather than as five
warnings. Writing five warnings would have made the docs lie about four working
tools.

### Four were caller error, and [A] already answered loudly

| What I called | Correct | What [A] actually answered |
|---|---|---|
| `write_fix` as find-and-replace | `start_line` / `end_line` / `new_content` | Nothing to answer — the schema already reads *"Replace a line range in one file, create a .bak backup, and sync the cache after writing"*, `start_line`: *"1-based start line."*, `end_line`: *"Inclusive end line."* |
| `copy_file` with `source`/`dest` | `src` / `dst` | HTTP **400**, `UNSUPPORTED_FIELD`, `unsupported_fields: ["dest","source"]`, `accepted_fields: ["backup_existing","dst","overwrite","project_path","src"]`, `ca_next_action: remove_unsupported_field` |
| `read_context_batch` with `reads` | `requests` | HTTP **400**, `UNSUPPORTED_FIELD`, `accepted_fields` naming `requests` |
| `write_new_file` with `path` instead of `file_path` | **either works** | `ok:true` both ways under `dry_run` — measured. There was never a trap here |

A refusal that names the offending field, lists `accepted_fields` and sets
`ca_next_action` is the contract working as designed. **A tool that refuses with
the correct field list is not under-documented — the caller did not read the
refusal.**

Two facts from these four are worth carrying forward anyway, because neither is in
the schema:

* **`expected_hash` is a bypass for chained writes, not a safety latch.** The
  schema says so (*"pass new_hash from previous write_fix response to bypass
  FILE_CHANGED guardrail in sequential same-file writes"*), and
  `orchestrator/tools/write/core.py:590-663` confirms the shape: a matching
  `expected_hash` returns early (`:629`); a wrong one simply falls through to the
  real `read_token` guard, which compares `read_hash`/`read_mtime`/`read_size` and
  returns `_file_changed_error` (`:652`). **Passing a stale hash does not weaken
  the guard, and omitting it does not strengthen it.**
* **`read_file_range` returns the file text in `result`, and there is no
  `content` key.** Measured top-level envelope: 31 keys including `ok`, `file`,
  `result`, `selection`, `shown`, `total_lines`, `next_ranges`, `content_hash`,
  `read_token_seeded`, `range_id`, `truncated` — and `result` is a **`str`**, not
  nested JSON, so `json.loads(resp["result"])` raises
  `JSONDecodeError: Extra data: line 1 column 2 (char 1)`. `read_context_batch`
  nests the same shape once per item under `items[]`. Nothing is broken here, but
  a caller reaching for `content` reads an absent key as an empty file.

### The one real defect: an answer that is `ok:true` and empty

```
cbm_trace_path(start_node_id="_cache_delta_sync")   ->  ok:true, edges:[], nodes_seen:0,
                                                        truncated:false, error:null,
                                                        truncation_reason:"start_node_not_found"
cbm_trace_path(start_node_id="code_tools.py:function:code_tools.cache_delta:3674")
                                                    ->  ok:true, edges:20, nodes_seen:18,
                                                        truncation_reason:None
```

`start_node_id` needs a **graph node id**, not a symbol name, and the schema says
exactly that: *"Graph node id to traverse from (e.g. 'file.py:function:mod.fn:LINE')."*
The requirement is not the defect. The defect is that missing it answers **`ok:true`
with an empty result**, and the only signal is `truncation_reason` — on a response
whose `truncated` is `false` and whose `error` is `null`. A caller guarding on `ok`,
or on `error`, or on `truncated`, reads "this symbol has no callers".

This is **fail-open used where the caller cannot tell**. The tool description
declares the policy up front (*"Fail-open on timeout/stale/missing"*), which is the
right default for a stale-index read — but fail-open only works when the failure
lands in a field somebody guards on. **A field that carries the failure must not be
the field nobody reads.** Compare B018, which was the same asymmetry the other way
round: a *successful* `search_code` omitted `ok` and so read as a failure. Both
break the same contract — the guard field must track the outcome.

Filed, not fixed in this pass: the fix is a contract change (`ok:false` with a named
error, or a `start_node_resolved: false` field callers can guard on) and needs its
own failing test first, plus a decision about which existing callers depend on
fail-open.

Where a real node id comes from: `cbm_template_query(template="callers",
name="<sym>")` returns rows carrying `id` in exactly that shape. The field is
**`name`** — `symbol` and `symbol_name` are both refused — and a *repeat* of the
same wrong payload answers `DUPLICATE_RETRY_BLOCKED` ("Identical deterministic
failure already returned a correction"), which is the dedup guard working, not a
new error. `find_symbol` cannot supply a node id; it is a text search returning
`file`/`line`/`match`.

### `payload_example` is an error field, not a schema field

Half of this entry's original plan was "also fix each tool's `payload_example`".
There is nowhere to put it: measured, `payload_example` is `null` in the schema of
all six tools, because it is built **into error envelopes** at answer time
(`orchestrator/tools/search.py:613-630`, `code_tools.py:1129`, `5247`,
`orchestrator/session/process.py:22`) rather than declared statically. The four
loud refusals already carry `accepted_fields` + `ca_next_action`, which is the same
job done better. `cbm_trace_path` has no error envelope to carry one — which is
precisely the defect above.

### The rule this entry is really about

Before writing "tool X's schema is wrong" in a report, call
`GET /api/tools/X/schema` (the descriptions live at
`tool.function.parameters.properties`, not at the response root) and re-read the
refusal the tool already gave. Four of five findings dissolved on contact with that
one step, and the fifth got sharper: the problem was never the required shape, it
was that violating it answered `ok:true`.

---

## The doctor: `scripts/live_answer_doctor.py`

Two campaigns produced Entries 1-9. This is what they add up to, and it is
runnable rather than written down:

```
venv312\Scripts\python.exe scripts\live_answer_doctor.py --project-path <repo>
venv312\Scripts\python.exe scripts\live_answer_doctor.py --project-path <repo> --ci
```

Run it against a **real, already-indexed, non-Python repo**. Those three
adjectives are the whole design, because they are the three conditions under
which the answers stop being honest, and a `tmp_path` fixture has none of them:

| Condition | What it exposes | Fixture equivalent |
|---|---|---|
| **real** | a file that vanished, a glob that matches part of the tree | nothing vanishes; no glob is passed |
| **already-indexed** | rows written by an older extractor (B016) | no prior rows exist to be stale |
| **non-Python** | an engine answering under another engine's description (B015), JS import keys (B013) | Python repo → the engine named is the engine that ran |

The suite was green through all six of B011-B016. That is not a gap in the suite;
it is what a fixture *is*. So `tests/` pins the mechanisms and the doctor pins the
claim that the mechanisms reach production data — the two are different claims,
and this is the line that separates them:

> **"The tests pass" and "the fix reaches production data" are different claims.
> Anything cached, hashed, or memoised sits between them.**

### Reading the output

* **FAIL** — a tool answered as though it had done something it did not do. Each
  row carries its bug id; go to `KNOWN_BUGS.md` and then to the pinning test.
  `--ci` exits 1 here.
* **WARN** — the check could not run *here*, or a known gap is being surfaced.
  Never blocks. The standing WARN on a JS/TS repo is the deferred Ruff-only gap:
  `bug_risk` / `perf` / `complexity` / `code_smell` / `tech_debt` / `security` are
  empty **by construction**, not because the repo is clean.
* **exit 2** — the daemon did not answer. Not a verdict. STOP and report; a doctor
  that degrades to "all clear" when it cannot measure is worse than no doctor.

Measured on `dreye_redesign` (42 JS files) at generation `cac7187da50d`:
**14 pass / 0 fail / 1 warn**, exit 0. The generation matters: the same doctor
against `7d870da7e301` -- the process that still held the pre-B018 `search.py` --
measured **11 pass / 1 fail / 1 warn**, exit 1, and against `31156af84e36` --
which still held the pre-B019 registry -- **12 pass / 2 fail / 1 warn**, exit 1.
Same disk, same tests, three different answers. The doctor is itself pinned by
`tests/test_live_answer_doctor.py` (36 tests), which feeds each check a synthetic
answer shaped like the bug it was written for and asserts it FAILS — a check that
cannot fail is decoration, and decoration is what B011-B016 walked past.

**It has already earned itself.** The B018 check was added to a doctor running
against a daemon that still held the old `search.py` in memory, and it FAILED
immediately — `search_code answered count=3 with no ok field` — exit 1. That is
the whole design working in one line: the fix was on disk, the tests were green,
and the live answer was still wrong until a restart.

**Twice, in fact.** The B019 check was added the same way and failed the same
way: *"regex_search's budget error tells callers to narrow, but its live schema
does not accept path"* — 12 pass / 2 fail / 1 warn, exit 1, on a daemon whose
9-test fix was already committed and green. The second FAIL is the bounding one:
the field must be *honoured*, not merely declared.

**And then that same second row produced a FALSE verdict, which is worth more
than the true ones.** After the registry fix went live it still said FAIL —
`REGEX_INPUT_BUDGET_EXCEEDED` — against a daemon that was by then correct: the
probe passed `path=repo`, and `path=repo` narrows *nothing*. `dreye_redesign`'s
top level holds `1.footage`, `3.output` and `4.final`, so an unscoped scan
legitimately hits the ceiling. **A probe aimed at the wrong scope measures nothing
about the thing it names** — the identical mistake as the outside-the-repo
`validate_file` probe recorded below, made again two sections later by the same
author. The check now walks to a SUBTREE (`_probe_dir`), and a subtree that is
itself over budget WARNs instead of accusing: the ceiling is not the bug. Three
tests pin it, one of them asserting the exact path the probe scopes to.

**Run it with `--ci` or the exit code is a lie.** Without the flag the script
prints FAIL rows and still exits 0. Every measurement in this file was taken with
`--ci`.

**Writing that check found a defect in the doctor itself.** `call()` raised
`Unreachable` on every `HTTPError`, so the first check that deliberately PROBED a
refusal aborted the whole run with `cannot reach [A]: regex_search -> HTTP 400` —
reporting a perfectly reachable daemon as dead, which is precisely the
misdiagnosis this script exists to prevent. A 400 is a **structured refusal**
carrying a real JSON body (`UNSUPPORTED_FIELD`, `SCHEMA_VIOLATION`,
`MISSING_REQUIRED_FIELD`); it must be unwrapped and returned. 404 stays fatal —
it means the tool name is wrong, so no check written against it can mean
anything. If you add a check that expects a refusal, this is the line it depends
on.

### Its own first run found two bugs — in itself

Both are mistakes the next CA will repeat, so they are pinned:

* `cbm_template_query` answers **`results` + `count`**, not `rows`. Reading `rows`
  reported 14 real rows as 0, which reads exactly like "the query is broken".
  Same family as reading past the HTTP `result` envelope.
* `validate_file` refuses an out-of-scope path **before** it considers `lang`, so
  a probe file outside the repo measures nothing. Measured: the identical call
  answers `UNSUPPORTED_LANG` for a file inside the repo and `VALIDATION_FAILED`
  for one outside it. The doctor now picks its probe from inside.

### Adding a check

The bar is one question: **is the answer knowable independently of the tool being
asked?** A check that can only be verified by the tool it is checking proves
nothing. The four shapes that worked:

1. **Pass a deliberately impossible argument.** A bogus glob returning the
   unfiltered count proves the argument is *inert*; a narrow glob returning many
   results proves nothing, because it may just be broad.
2. **Pass a misspelling of something supported.** `JSON`, `javascript`, `py` each
   name a language the tool implements under another spelling — which is exactly
   why they used to pass silently.
3. **Count the same thing two ways.** The index walks disk; `stale_files` reads
   the files table. Two populations that must agree.
4. **Check the number moves at all.** Do the thing twice. A count that two full
   indexes cannot clear is a different bug from a count that is merely high.

Then add the failing-answer case to `tests/test_live_answer_doctor.py` before the
check itself, and confirm it fails.

### Where it fits in the cadence

`scripts/DIAGNOSTIC_SUITE.md` guards *structure* — drift between code, schema,
skills and inventory — by importing modules with the server down. This doctor
guards *answers*, and needs the server up and a real repo. They do not overlap:
run the diagnostic suite before a commit, and the doctor after a restart, when the
new code is finally the running code.

**Run it after every restart that ships a change to an extractor, an index path, a
fallback, or an argument that filters.** B016 existed for exactly as long as the
gap between "committed, tested, deployed" and "someone asked the live tool".

## Mutation testing: the acceptance bar

A test that passes against the *unfixed* code proves nothing. Every fix in this
campaign was verified by reverting the fix at runtime and confirming the tests
**fail**. The mechanics:

```powershell
# 1. A plugin that reverts the fix, on a path outside the repo
#    (use a pytest hook, not import-time: -p plugins load BEFORE conftest,
#     so an import-time revert gets overwritten by conftest again)
# 2. Run the real test file, in place, so repo-relative paths still resolve
$env:PYTHONPATH="<plugin dir>"
venv312\Scripts\python.exe -m pytest tests\<file>.py -q -p <revert_plugin>
```

Two things this catches that nothing else does:

* **Run the tests in place, not in a scratch copy.** A copied test file resolves
  `_ROOT` to the scratch dir and fails with `FileNotFoundError` on repo files —
  which looks like a passing mutation but is a harness artifact.
* **Legitimate survivors exist.** Static-invariant tests (counting call sites,
  asserting rule-file text) are not touched by a runtime mutation and correctly
  keep passing. Do not "fix" them.

Recorded results: git payload fixes 7/11 fail on revert; `mini_rule` 5/5 wire tests
fail; the A54 shim revert kills the **entire session** with the original
`WinError 5` — the strongest proof in the set.

---

## Editing [A] itself

Reading [A]'s own files is **not** relaxed — use [A] read tools. Writing to them
is an authorized path, not a bypass:

* Native edit, then `log_native_event` with **one absolute path**. The classifier
  maps a native write inside [A]'s tree to `task_class=upgrade_orchestrator` /
  `native_policy=hybrid_upgrade_only` and returns `ok:true` /
  `audit_status:logged`.
* A `HOST_GATE_DENY` / `write_existing_file` answer means the target did not
  resolve inside [A] — usually several paths joined into one string.
* **`examples/CORE_RULE.txt`, `SLIM_RULE.txt` and `SLIM_RULE_INLINE.txt` are SOT
  and must be edited through `write_fix`, not natively** (pinned by
  `test_xroot_core_rule_sot.py`). `sync_rule` only distributes.
* **Line endings are per-file and load-bearing.** `orchestrator_server.py` is pure
  CRLF, the rule files are pure LF, `orchestrator/git_tools.py` is mixed. Native
  writes must use `io.open(..., newline="")` and preserve the file's own
  terminator. Verify with a CRLF/bare-LF count before and after.
* **Patch-script discipline:** put every `assert` before the single `io.open(p,"w")`.
  An `AssertionError` then means nothing was written.

### A rule change reaches CAs through three files, not one

`sync_rule` distributes from three sources, and a change to `CORE_RULE.txt` alone
**silently misses** every agent on the slim template. Always run the dry run
first: `changed=False` for the slim agents is the signal that you edited the wrong
file. `rule_drift_check` reads all three.

---

## Restarting [A]

`restart_orchestrator_via_bat` with **only** `delay_seconds=2` is the only restart
path. Never `expected_generation`, never an invented kill pipeline. Verify a NEW
PID and an incremented `/health` generation afterwards.

**A commit does not change the running process** — the daemon holds its code in
memory, so fixes are not live until a restart. And **open T4 page-sessions are
lost** on restart; say so before doing it. Never restart while a worker dispatch is
in flight.

---

## Test inventory added by campaign-1608

73 tests, all mutation-verified: 70 in six new files, plus 3 added to an
existing one (`test_p4_soft_data.py`, B009).

| File | Tests | Pins |
|---|---|---|
| `test_destructive_script_guard_case2008.py` | 19 | CASE-2008 gate on the script surface |
| `test_destructive_command_guard_case2008.py` | 15 | the same gate on `run_project_command` (the unguarded sibling) |
| `test_git_status_payload_and_auto_hash.py` | 14 | payload cap, hash position, `"auto"` semantics, and which layer refuses a missing hash (B008) |
| `test_scoped_session_artifacts_survive_reindex.py` | 10 | scoped session artifacts survive a full reindex |
| `test_mutation_token_mini_rule.py` | 7 | the mini rule at all 4 token surfaces |
| `test_conftest_stale_symlink_tolerance.py` | 5 | A54 shim + no hardcoded-drive basetemp |

`test_p4_soft_data.py` grew 3 tests for B009: lock contention answers
`SQLITE_DATABASE_LOCKED` with `retryable:true`, the busy-timeout constant
drives the effective wait, and a non-lock `OperationalError` stays a hard
error.

Related pre-existing files worth knowing about: `test_a02_a30_interaction.py` (the
write-ledger pact that `PRESERVED_CACHE_FILES` must **not** widen to cover),
`test_command_transport_c2.py` (`%`/`&` escaping in commands),
`test_antidrift_contract.py`, `test_schema_params_contract.py`.

---

## Test inventory added by the JS-repo audit (2026-08-21)

141 tests, all mutation-verified: every file below was written **before** its fix
and confirmed failing first. Rows 5-7 came from the doctor itself and the defect
(B017) that building it exposed; the last two (B019, B020) came from surveying
the other tools for B018's shape and from the one failure the full suite still had
after it. B019's last 3 rows came from a restart proving its first 9 tests had
verified the wrong layer.
B021's last 4 rows came from mutants walking through 13 green tests -- a
fixture too small to distinguish the mutant is a test that measures nothing.
B022's row came from Anh's real test: the last one is a caller-side default,
not a rule -- two callers of one predicate needed the same conservative
default in opposite directions.

| File | Tests | Pins |
|---|---|---|
| `test_validate_file_unknown_lang_fails_loud.py` | 13 | B012: alias normalisation, `UNSUPPORTED_LANG` with `supported_langs`, the `checked` flag, RFC-8259 rejections |
| `test_js_dependency_edges.py` | 11 | B013: ESM key shape, the three `require()` forms, and that a bare package specifier stays unresolved |
| `test_cbm_dead_code_file_filter.py` | 9 | B014/B015: the bogus-glob case, echoed `files_matched`, and `engine`/`engine_reason`/`language_detected` |
| `test_graph_index_prunes_deleted_files.py` | 6 | B011: rows pruned, nodes dropped, `files_pruned` reported, index/status agreement, convergence, `freshness` |
| `test_live_answer_doctor.py` | 36 | The doctor itself: every check fed an answer shaped like its bug must FAIL; `results` not `rows`; an in-repo probe file; exit 2 on an unreachable daemon; WARN never blocks; an always-`ok` search fails too. The last 9 pin the B019 check (a live schema missing `path` FAILs, a schema-only fix that still refuses the call FAILs, an unreadable schema WARNs rather than accusing the daemon of the doctor's own parsing bug), the `call()` defect that writing it exposed (a **400 is a structured refusal** and must be returned; a 404 stays fatal), and the check's own **false FAIL**: the probe must scope to a subtree, not the repo root, and a subtree over the regex budget WARNs rather than accusing a correct tool |
| `test_search_ok_field_b018.py` | 4 | B018: a hit and a clean zero-hit both answer `ok:True`; an empty query still answers `ok:False` with a named error |
| `test_policy_action_vocabulary_b017.py` | 8 | B017: `<verb>_file` spellings resolve through the same table; an unknown verb stays `unknown`; a suffix cannot mint `upgrade_orchestrator`; `delete_file` keeps `delete`'s policy |
| `test_extractor_version_pins_js_import_shape.py` | 5 | B016: a row records the reported version, a version bump re-extracts unchanged files, the JS import shape travels with it, and the pre-B013 string is refused |
| `test_regex_search_path_b019.py` | 12 | B019: the alias accepts `path` **and forwards it** (a hit found from inside, nothing from outside), the budget error names a field the tool really takes, its `payload_example` is not the failing call, it carries the worker's own message for both ceilings, and the live branch actually routes through the builder -- a mutant that unrouted it survived the first 8 tests. The last 3 pin the **registry** schema and the dispatcher's own `allowed_params` expression: the signature fix was invisible over HTTP, and all 9 earlier tests passed through it |
| `test_room_token_ttl_b020.py` | 4 | B020: a one-second token survives most of that second, the full TTL is honoured wherever in the wall-clock second the register lands (5 subtests), `expires_at` is at least `ttl_sec` away, and a token still expires |
| `test_payload_says_each_thing_once_b021.py` | 22 | B021: no `git_status` record carries `raw` beside the fields parsed out of it, every parsed field survives for all five porcelain types (`1 2 u ? !` -- `u` and `!` driven at `_parse_records`, since a conflicted merge and `--ignored` are awkward to stage and a type-1-only fix would pass a single-type fixture), the payload is measurably smaller (<120 bytes/record), and **`state_hash` is unchanged against an independently recomputed literal** -- it is an optimistic lock callers hold *between* calls, so the tidy is response-only and `stale_check` still detects drift end-to-end. `module_deps` half: the real pre-cap population, the `truncated`/`total_count` invariant at limits 1/4/12/13/50, an uncapped read answering `total_count == count` with `truncated False`, `orphan_symbols` keeping the total it already computed, the **zero case** where `total_count=0` is the truth, and the docstring that documented the bug as intended. The last 4 rows are the mutants' doing: the `DISTINCT` predicate (a file importing the same module twice gives 2 edges for 1 pair), `entry_points` counting the production set it serves rather than the pre-split list, the depth-capped blast radius `impact_scope` must not report as complete, and `impact_scope` still describing its own single row correctly. The **last 6** are the offset follow-up: `offset` present in the signature *and* in the **registered schema** (the layer the server's unknown-field gate reads), page 2 reachable end-to-end through the async wrapper, every paginated branch actually paging (with `checked >= 3`, so a shrinking fixture cannot masquerade as a passing branch), a second fixture asserting the 4/6/3 rows the first was too small to produce, a whole-population page walk whose union holds every row once and none twice, and a negative offset answering page 1 on **both** a SQL branch and the Python branch |
| `test_drift_and_indexer_agree_b022.py` | 7 | B022: no file the drift check calls `added` may be a file the indexer refuses (against a real index, and against an **empty** one via `index_override`, because an index already holding every phantom would hide the defect), a fresh index reports `total_changed=0`, drift converges across two refreshes, and `query_index` answers `ok:true` end-to-end. Two rows are deliberately **mutant-surviving** and say so: the guard-the-guard (the fixture still contains deep data-JSON the rule refuses, and `app.py`/`package.json`/`data.json` stay indexable) and the opposite failure (a genuinely new source file is still reported). Reverting `, root` kills 5 of 7. What the tests deliberately do **not** assert is `_should_scan_file(p) == _should_scan_file(p, root)` -- those must still disagree, since R82's no-root default is intentional and the defect was a caller, not the mapper |
| `test_cache_status_ok_field_b024.py` | 4 | B024: `cache_status` must answer `ok` on **both** paths -- `ok:true` on a report that ran, and `ok:false` + `error_name` when the check itself raises. Pins that `ok` means "the check ran" and **not** "the cache is fresh": a drifted cache is still `ok:true`, with staleness carried by `status`/`drift_warning`. Reverting the fix kills 3 of 4; the survivor (`test_the_rest_of_the_payload_is_untouched`) is **supposed** to survive, because it pins that the fix did not reshape the report. Its first cut asserted `generation`, present on the live repo and absent on a tmp fixture -- a test asserting the environment, now named as deliberately not asserted |

Two harness requirements for anything touching the graph, both learned the hard
way:

* `ORCHESTRATOR_PARALLEL_INDEX=off` in any ad-hoc script or test —
  `_snapshot_files` uses a ProcessPool that is not spawn-safe and raises
  `BrokenProcessPool` otherwise.
* `GRAPH_DB_DIR` per test, or the fixture writes into the real
  `.orchestrator_cache/graph.db`. `CBM_EXTRACT_JS=1` is what admits JS/TS to the
  walk, and `CBM_GRAPH_ENABLED=1` is what lets `dead_code_candidates` run at all
  — a JS test that forgets either one passes vacuously.

Baseline after this audit and the doctor that followed it: **2592 passed, 4
skipped, 1 xfailed, 169 subtests passed, 0 failed** in 292s, measured -- not
predicted. The four deltas this campaign is accountable for are all measured on
the same run order: 2495 -> 2526 for the doctor's 31 tests, 2526 -> 2534 for
B018's 8 (4 pinning the fix, 4 pinning the doctor's new check), 2534 -> 2547 for
B019's 9 plus B020's 4, and 2547 -> 2556 for B019's third half (3 registry tests
plus the 6 that pin the doctor's new check and the 400-handling defect writing it
exposed), 2556 -> 2559 for the 3 that pin the probe-scope false FAIL, and 2559 -> 2575
for B021's 16, and 2575 -> 2581 for the 6 that pin B021's offset follow-up
(4 of whose mutants survived the first cut because the original fixture yields 0/0/1
rows on the branches they touch), 2581 -> 2588 for B022's 7, and 2588 -> 2592 for B024's 4. The 2534
run had one failure, and it was not environmental: it was B020. The 2556 run had
one too, and it was a genuine flake -- a nominal-sleep timing assertion, fixed
and mutation-verified rather than rerun. Reproduce it the way the section above prescribes — plain
`venv312\Scripts\python.exe -m pytest tests\ -q`, letting pytest resolve its own
per-run basetemp under `%TEMP%`. The measurements in this file were taken with an
explicit `--basetemp` on `C:` only to keep several sequential runs separable; a
**hardcoded** basetemp is the thing the earlier section forbids, and it is not
part of the reproduction.
