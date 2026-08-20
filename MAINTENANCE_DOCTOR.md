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

---

## When a write times out on the client

This is the single most-misread failure in [A], and it is not a failure. `write_fix` / `write_transaction` on a multi-megabyte file routinely run past the caller's socket timeout: on a 9.9MB file a normal `write_fix` measured **70.6s**, and the wall clock is owned by the post-write symbol check (27.5s with `skip_symbol_check=true`), not by the write or the backup — `backup_override` saved ~0.6s of that, and `backup_project` already skips when today's backup exists.

The server does not care that the client left. So:

1. `GET /api/write_status?operation_id=<id>` if you got a response carrying one. If you never got a response, look the operation up by target instead: `?project_path=<root>&file_path=<file>`.
2. Read `commit_state`, not `ok`: `in_flight` means keep polling, `committed` means it landed, `not_committed` means it is safe to retry. `safe_to_retry` and `ca_next_action` say the same thing in one field.
3. Only resend when `commit_state` is `not_committed`. A resend during `in_flight` is a second full write of the same file.

Measured end to end against the live daemon (10MB file, deliberate 4s client timeout): the client raised `TimeoutError` at 4025ms, the target lookup answered `in_flight` / `safe_to_retry: false` at t+4573ms, and flipped to `committed` at t+53961ms with `bytes_written: 10337814` and `validation_ok: true`. The recovery path was complete the whole time — what was missing, until 2026-08-20, was any document telling a CA it existed. If you are tempted to make the write path asynchronous to "fix" this, measure first: the answer is already there.

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
