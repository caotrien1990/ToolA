const http = require("http");

function call(tool, body, fields) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const path = "/api/call/" + tool + (fields ? "?fields=" + fields : "");
    const req = http.request(
      {
        hostname: "localhost",
        port: 8083,
        path,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(payload),
        },
      },
      (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => resolve({ status: res.statusCode, data }));
      }
    );
    req.on("error", reject);
    req.write(payload);
    req.end();
  });
}

function b64(text) {
  return Buffer.from(text, "utf8").toString("base64");
}

const artifact = `# CLR2_RESID_VECT_CACHE — residual HIGH fix report

**Task ID:** CLR2-P2A-RESID-VECT-CACHE
**Worker:** Gro / GroC
**Date:** 2026-08-28
**OBJECTIVE_CLASS:** bug-fix
**RISK_LEVEL:** high
**DEV_LEVEL:** mid

No process restart. Production \`librarian.db\` was not opened or written.

---

## BUG 3 — vector runner select/write mismatch — FIXED

**File:** \`D:\\\\0. TRIEN\\\\1. DATA\\\\DATA\\\\5. VIBE APP\\\\1. APP\\\\21. ToolQ\\\\bin\\\\clm_qwen_verify_runner.py\`
**Function:** \`write_decisions\` (CAS guard)

### Root cause
\`select_candidates()\` (widened by CLR2-P2A-VECT-RUNNER) selects every live \`vector_eligible=0\` row whose legacy \`verify_status\` is not \`rejected\`/\`quarantine\`. That includes rows whose \`qwen_verify_status\` is already set (example: \`quarantine\`).

\`write_decisions()\` still skipped any row with \`qwen_verify_status is not None\`. The model scored the row; the writer dropped it. No status change, no score row, wasted model calls.

### Fix
CAS now matches \`select_candidates\`:
- keep \`vector_eligible == 0\`
- skip only when \`verify_status\` is \`rejected\` or \`quarantine\`
- remove the \`qwen_verify_status is not None\` skip

A prior Qwen status such as \`quarantine\` is overwriteable when \`vector_eligible\` is still 0.

### Test
\`bin/clm_qwen_verify_runner_test.py\` → \`test_quarantine_qwen_status_reverify_writes\`
- fixture: \`qwen_verify_status='quarantine'\`, \`vector_eligible=0\`, \`verify_status='ok'\`
- \`select_candidates\` picks id 50
- \`write_decisions\` applies \`semantic_verified\` / \`vector_eligible=1\` (does not skip)

A33 updated to the new re-verify contract: second apply still sees remaining \`ve=0\` rows and writes new score/transition rows.

---

## BUG 4 — semantic TTL cache returns tombstoned rows — FIXED

**File:** \`D:\\\\0. TRIEN\\\\1. DATA\\\\DATA\\\\5. VIBE APP\\\\1. APP\\\\21. ToolQ\\\\bin\\\\clm_semantic_embed.py\`
**Functions:** \`invalidate_live_rows_cache\`, \`select_live_semantic_rows\`

### Root cause
Cache key was only \`model_version\`. Soft-deleting a point inside the 120s TTL still returned the cached live row. That violates Q-3: never let a tombstoned chain surface.

### Fix
- module-level \`_LIVE_ROWS_EPOCH\`
- \`invalidate_live_rows_cache()\` increments epoch and clears \`_LIVE_ROWS_CACHE\`
- cache key is \`(model_version, _LIVE_ROWS_EPOCH)\`
- \`select_live_semantic_rows(..., force_refresh=True)\` invalidates then reloads

Writer hooks (tombstone paths):
- \`clm_lib/schema.py\` \`soft_delete_subtree\` — primary API / distill / zone delete path
- \`clm_semantic_embed.index\` near-dup INSERT that sets \`deleted_at\`
- \`clm_folder_watcher.reconcile_orphans\` orphan tombstone sweep

\`merge_into_existing\` does not tombstone (increments \`observation_count\` only); no hook there.

### Test
\`tests/test_semantic_ttl_cache_tombstone.py\`
- insert 1 live semantic row
- \`select_live_semantic_rows\` caches it (TTL=120)
- \`soft_delete_subtree(point_ids=[pid])\`
- second \`select_live_semantic_rows\` returns 0 rows

---

## Validation

| Check | Result |
|---|---|
| \`validate_file\` runner/embed/schema/tests | ok, python, checked |
| pyright on runner + embed + schema + folder_watcher + new cache test | **0 errors, 0 warnings** |
| \`clm_qwen_verify_runner_test.py\` | **pass=92 fail=0** (includes BUG3) |
| \`tests/test_semantic_ttl_cache_tombstone.py\` | **PASS** |
| librarian.db production | not touched |
| process restart | none |

Pre-existing ruff I001 on \`clm_qwen_verify_runner.py\` and I001/E402/F841 on \`clm_folder_watcher.py\` were not introduced by this patch. Test-file \`Args\` vs \`Namespace\` pyright noise is pre-existing; the new BUG3 call is \`type: ignore[arg-type]\`.

---

## Files changed

1. \`D:\\\\0. TRIEN\\\\1. DATA\\\\DATA\\\\5. VIBE APP\\\\1. APP\\\\21. ToolQ\\\\bin\\\\clm_qwen_verify_runner.py\`
2. \`D:\\\\0. TRIEN\\\\1. DATA\\\\DATA\\\\5. VIBE APP\\\\1. APP\\\\21. ToolQ\\\\bin\\\\clm_semantic_embed.py\`
3. \`D:\\\\0. TRIEN\\\\1. DATA\\\\DATA\\\\5. VIBE APP\\\\1. APP\\\\21. ToolQ\\\\bin\\\\clm_lib\\\\schema.py\`
4. \`D:\\\\0. TRIEN\\\\1. DATA\\\\DATA\\\\5. VIBE APP\\\\1. APP\\\\21. ToolQ\\\\bin\\\\clm_folder_watcher.py\`
5. \`D:\\\\0. TRIEN\\\\1. DATA\\\\DATA\\\\5. VIBE APP\\\\1. APP\\\\21. ToolQ\\\\bin\\\\clm_qwen_verify_runner_test.py\`
6. \`D:\\\\0. TRIEN\\\\1. DATA\\\\DATA\\\\5. VIBE APP\\\\1. APP\\\\21. ToolQ\\\\tests\\\\test_semantic_ttl_cache_tombstone.py\` (new)

Worktree left dirty. No commit. No restart.

END_GRO_R1_RESID_VECTCACHE
`;

const worker = `# CLR2-P2A-RESID-VECT-CACHE — GroC RES

TASK_ID: CLR2-P2A-RESID-VECT-CACHE
Agent: Gro / GroC
Mode: council worker
Status: FIXED + TESTED
No restart. Production librarian.db not touched.

## BUG 3 — write_decisions CAS aligned with select_candidates

File: D:\\\\0. TRIEN\\\\1. DATA\\\\DATA\\\\5. VIBE APP\\\\1. APP\\\\21. ToolQ\\\\bin\\\\clm_qwen_verify_runner.py

select_candidates already admits all live ve=0 rows except verify_status in (rejected, quarantine), including rows whose qwen_verify_status is already set.

write_decisions previously:
\`if not current or current["qwen_verify_status"] is not None or int(current["vector_eligible"]) != 0: continue\`

Now:
- SELECT also reads verify_status
- skip only when missing / ve!=0 / verify_status in (rejected, quarantine)
- qwen_verify_status is not a skip condition

Test: fixture qwen_verify_status='quarantine', vector_eligible=0 → select picks it, write_decisions writes semantic_verified + ve=1 + score row.

A33 updated: second apply still sees remaining ve=0 rows (re-verify), score/transition counts grow 3→5.

## BUG 4 — live semantic TTL cache no longer serves tombstones

File: D:\\\\0. TRIEN\\\\1. DATA\\\\DATA\\\\5. VIBE APP\\\\1. APP\\\\21. ToolQ\\\\bin\\\\clm_semantic_embed.py

Added:
- _LIVE_ROWS_EPOCH
- invalidate_live_rows_cache()
- cache key (model_version, epoch)
- force_refresh=True on select_live_semantic_rows

Hooks:
- soft_delete_subtree (schema.py) — primary tombstone writer
- index() near-dup tombstone insert
- folder_watcher.reconcile_orphans
- merge_into_existing does not tombstone; no hook

Test: cache 1 live row, soft_delete_subtree the point, select again → 0 rows.

## Evidence

- validate_file: ok on all changed py files
- pyright (runner, embed, schema, folder_watcher, new cache test): 0 errors, 0 warnings
- clm_qwen_verify_runner_test.py: pass=92 fail=0
- tests/test_semantic_ttl_cache_tombstone.py: PASS
- Artifact: D:\\\\0. TRIEN\\\\1. DATA\\\\DATA\\\\5. VIBE APP\\\\1. APP\\\\41. CLAUDELTNAI\\\\FIX_TOOLQ_2508\\\\CLR2_RESID_VECT_CACHE.md

END_GRO_R1_RESID_VECTCACHE
`;

(async () => {
  const claude = "D:\\\\0. TRIEN\\\\1. DATA\\\\DATA\\\\5. VIBE APP\\\\1. APP\\\\41. CLAUDELTNAI";
  const orch = "D:\\\\0. TRIEN\\\\1. DATA\\\\DATA\\\\5. VIBE APP\\\\1. APP\\\\8. ToolA\\\\project-orchestrator";

  const s1 = await call(
    "open_session",
    {
      project_path: claude,
      session_title: "CLR2-P2A-RESID-VECT-CACHE-artifact",
      mode: "T2",
      verbosity: "terse",
      agent_id: "gro",
    },
    "ok,session_id,project_path,error_name"
  );
  console.log("SESSION CLAUDE", s1.data);

  const a1 = await call(
    "write_new_file",
    {
      project_path: claude,
      path: "FIX_TOOLQ_2508/CLR2_RESID_VECT_CACHE.md",
      new_content: b64(artifact),
      content_encoding: "base64",
      overwrite: true,
      dry_run: false,
    },
    "ok,error_name,validation,message"
  );
  console.log("ARTIFACT", a1.data);

  const s2 = await call(
    "open_session",
    {
      project_path: orch,
      session_title: "CLR2-P2A-RESID-VECT-CACHE-outbox",
      mode: "T2",
      verbosity: "terse",
      agent_id: "gro",
    },
    "ok,session_id,project_path,error_name"
  );
  console.log("SESSION ORCH", s2.data);

  const a2 = await call(
    "write_new_file",
    {
      project_path: orch,
      path: ".council/worker_output/20260828_225953_gro_63f52822.md",
      new_content: b64(worker),
      content_encoding: "base64",
      overwrite: true,
      dry_run: false,
    },
    "ok,error_name,validation,message"
  );
  console.log("WORKER", a2.data);
})().catch((err) => {
  console.log("ERR " + err);
  process.exit(1);
});
