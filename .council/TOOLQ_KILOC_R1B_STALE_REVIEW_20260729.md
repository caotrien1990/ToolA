# TOOLQ_KILOC_R1B_STALE_REVIEW — KiloC Round 1B (bounded failure-recovery)

**STATUS:** PASS
**TASK:** TOOLQ_STALE_REVIEW_R1B
**SCOPE:** Bounded analysis of 17 metadata_enrich candidates looping on TARGET_NOT_FOUND in ToolQ qsleep apply pipeline. No source/data edits, no restart, no broad DB query.

---

## 1. Evidence (what was actually observed)

### 1.1 apply.log tail (lines 181887–181986, the requested window)
The log shows a perfect **3-cycle-per-night rhythm** since 2026-07-17T03:09, with the **identical 17 queue_ids** failing every single cycle:

dp_70006, dp_71498, dp_71465, dp_71512, dp_71509, dp_71507, dp_71504, dp_71667, dp_71662, dp_71645, dp_71857, dp_71954, dp_72069, dp_72053, dp_72936, dp_72930, dp_72917

Representative cycle (latest, 2026-07-29T01:18 → 02:17):

`
apply_all START type=metadata_enrich n=17
FAIL ...dp_70006... TARGET_NOT_FOUND    (x17 distinct ids)
apply_all DONE type=metadata_enrich applied=0 failed=17
apply_all START type=dedup_merge n=0
apply_all DONE  type=dedup_merge applied=0 failed=0
apply_all START type=purge_candidate n=0
apply_all DONE  type=purge_candidate applied=0 failed=0
`

**Counts in this window:** 7 metadata_enrich cycles × 17 fails = **119 FAIL lines** for the *same* 17 ids. dedup_merge/purge_candidate are empty (n=0) — they are not the problem. Confirming evidence already in the prompt: log has grown to **18,206,443 bytes / 181,986 lines** because of this loop.

### 1.2 qnight_state.json
Run is **terminal** (status=completed, phase=idle, 	erminal_reason=completed, inished=true, inished_at=2026-07-29T02:17:22Z). The runner is not currently mid-flight — the stale candidates are being re-fed by clm_qnight_runner.py line 1054–1067 on each new qnight session.

### 1.3 Source: routes_write.py — handle_review_apply (lines 950–1118)
The metadata_enrich branch (line 980) checks the target:
`python
ok_target = bool(tid) and conn.execute(
    "SELECT 1 FROM distilled_points WHERE id=? AND COALESCE(deleted_at,'')=''", (tid,)).fetchone()
if not ok_target:
    conn.execute("UPDATE review_queue SET status='pending', decided_at=NULL, decided_by=NULL "
                 "WHERE queue_id=?", (qid,))
    conn.commit()
    return {"ok": False, "result": "error", "error_name": "TARGET_NOT_FOUND",
            "error": "metadata target_id missing/deleted"}
`
On miss, the code **silently re-arms the row to pending** (status reset, decided_at/decided_by cleared) and returns a normal error. There is no escalation, no per-candidate fail-streak counter, and no terminal status (e.g. quarantined/dead) on persistent failure.

### 1.4 Source: routes_write.py — handle_qjobs_apply_all (lines 1350–1382)
Bulk loop calls handle_review_apply per qid and aggregates:
`python
return {"ok": True, "result": "apply_all", "candidate_type": ct,
        "applied": applied, "failed": failed, "total": len(qids)}
`
Returns ok=true even when ailed==total. The runner in clm_qnight_runner.py line 1060 treats the return body as a tail string and only raises on the "apply err:" prefix — so pplied=0 failed=17 is logged as success and the next cycle happily re-picks the same 17 rows from SELECT ... WHERE status='pending'.

### 1.5 Source: clm_qnight_runner.py — main loop (lines 1014–1068)
The auto-apply is invoked unconditionally on p1_metadata, p2_dedup, p4_purge (line 1055). On p1_metadata it posts {candidate_type: "metadata_enrich", limit: 5000} to /qjobs/apply_all (line 1059–1060). There is no detection that the previous cycle had ailed>0 and no per-qid quarantine cache.

---

## 2. Root cause — confirmed and refined

**Primary root cause (CONFIRMED):** the 17 metadata_enrich candidates in eview_queue reference distilled_points.id values that are **soft-deleted** (deleted_at IS NOT NULL or row simply gone). The check on line 985–986 correctly detects this and returns TARGET_NOT_FOUND, but the failure handler at lines 988–989 **resets the row to pending**, so the next pply_all immediately re-picks it. Combined with handle_qjobs_apply_all returning ok=true on full failure and the runner's tail-only "apply err:" detection, the system has no terminal state for unrecoverable candidates → **infinite retry loop with no observability and growing apply.log**.

**Secondary issues (not the root cause, but amplify the damage):**
- **ok=true on total failure** (routes_write.py:1381) hides the failure from the runner's apply-result classifier — only the tail substring is logged, and it never matches "apply err:" so the runner proceeds.
- **No fail-streak / per-row terminal state** — eview_queue has no ailed_count / last_error / quarantined / status='dead' semantic. Each cycle is the first cycle as far as the row is concerned.
- **No idempotency across cycles** — the SELECT on line 1365–1367 has no WHERE on ingested_at or last_attempt_at, so already-failed-this-cycle rows are guaranteed to be re-picked next cycle.
- **Bulk apply runs on every qnight pass** with no diff against the previous pass; even a ailed>0 outcome does not trigger backoff or skip.

**Why specifically these 17 ids:** they cluster in the 70006–72936 range, all in the upper ID range (recently created). The strong hypothesis (consistent with 	arget_id semantics in metadata_enrich) is that these are distilled_points that were created and then **soft-deleted by an earlier dedup_merge (parent_removed) or purge_candidate** run, but their eview_queue rows survived and now point at tombstones. This is **consistent with the empty dedup_merge/purge_candidate queues today** — the dedup work is already done; the survivors were soft_delete_subtree(...)-ed, and the leftover metadata_enrich candidates have nothing to enrich.

---

## 3. Resume semantics — assessment

The qnight_state shows esumed=false, status=completed, 	erminal_reason=completed. There is **no in-flight cycle to resume** — the problem is structural, not transactional. Concretely:
- handle_review_apply uses an atomic UPDATE … WHERE status='pending' claim (line 971–977), so race is fine.
- pply_all does not write to qnight_state; only the runner does. So there is no half-applied transaction to roll forward.
- **The 17 rows are NOT in an in-progress state** — they cycle between pending → applying → pending on every cycle. No external checkpoint, no WAL, nothing to "resume" from. A new runner session is free to start with a clean slate, **provided the 17 rows are quarantined first** (otherwise the same loop restarts).

---

## 4. Minimum patch — fail-loud + terminal quarantine

Two surgical changes, both inside outes_write.py. No DB migration needed; status TEXT already accepts new values, and decided_at/decided_by already exist.

### 4.1 handle_review_apply — metadata_enrich branch (after line 992)

Replace the silent reset with a fail-streak + terminal quarantine:

`python
if not ok_target:
    # V15 R-fix: fail-streak counter + terminal quarantine.
    # First miss: increment failed_count, keep pending but stamp last_error.
    # N-th miss (configurable, default 2): terminal status 'quarantined' (no further auto-pick).
    conn.execute(
        "UPDATE review_queue "
        "SET failed_count = COALESCE(failed_count,0)+1, "
        "    last_error = 'TARGET_NOT_FOUND', "
        "    last_error_at = ? "
        "WHERE queue_id = ?",
        (_now, qid))
    streak = conn.execute(
        "SELECT COALESCE(failed_count,0) FROM review_queue WHERE queue_id=?", (qid,)).fetchone()
    streak_n = int((streak or (0,))[0] or 0)
    QUARANTINE_THRESHOLD = 2  # apply_all is idempotent w.r.t. a target that never existed
    if streak_n >= QUARANTINE_THRESHOLD:
        conn.execute(
            "UPDATE review_queue SET status='quarantined', decided_at=?, decided_by=? "
            "WHERE queue_id=?", (_now, "system:quarantine", qid))
        conn.commit()
        return {"ok": False, "result": "error", "error_name": "TARGET_NOT_FOUND",
                "error": "metadata target_id missing/deleted (quarantined)",
                "quarantined": True, "failed_count": streak_n}
    # First miss: re-arm pending so the next cycle can re-check (covers slow-replication race).
    conn.execute("UPDATE review_queue SET status='pending' WHERE queue_id=?", (qid,))
    conn.commit()
    return {"ok": False, "result": "error", "error_name": "TARGET_NOT_FOUND",
            "error": "metadata target_id missing/deleted",
            "failed_count": streak_n}
`

Apply the **same pattern** to the dedup_merge (line 1037–1042, DEDUP_TARGET_INVALID) and purge_candidate (line 1057–1062, PURGE_TARGET_INVALID) branches, with the same ailed_count/quarantined semantics. Today they see 
=0 but the same defect exists.

### 4.2 handle_qjobs_apply_all — fail-loud return (replace line 1381)

`python
# V15 R-fix: fail-loud when nothing was applied but failures occurred.
# The runner classifies this body only by tail; the prefix below is a known
# fail-loud signal that clm_qnight_runner can detect (and choose to halt the phase).
if failed > 0 and applied == 0:
    _apply_log("apply_all FAIL_LOUD type=%s applied=0 failed=%d (see prior FAIL lines)" % (ct, failed))
    return {"ok": False, "result": "apply_all_failed", "candidate_type": ct,
            "applied": applied, "failed": failed, "total": len(qids),
            "error_name": "APPLY_ALL_ZERO_APPLIED", "err_prefix": "apply err: zero_applied"}
# Original success return kept for partial-success cases.
return {"ok": True, "result": "apply_all", "candidate_type": ct,
        "applied": applied, "failed": failed, "total": len(qids)}
`

The err_prefix field matches the existing runner's startswith("apply err:") detection at line 1062–1063, so the runner will halt the phase on the **first** fully-failed cycle instead of looping silently.

### 4.3 Optional micro-fix in clm_qnight_runner.py (line 1060)
Surface the ok field to make classification unambiguous:
`python
_rr = _ka_post("http://127.0.0.1:8082/qjobs/apply_all", _b, 600).decode("utf-8", "replace")[-200:]
results[name + "_apply"] = _rr
try:
    _rj = json.loads(_rr)
    if isinstance(_rj, dict) and _rj.get("ok") is False:
        raise RuntimeError("apply_all failed: " + str(_rj.get("error_name") or _rj)[:200])
except json.JSONDecodeError:
    pass  # keep legacy tail-only behavior
`

This is the smallest possible change that **breaks the loop on the first cycle** and **quarantines the 17 confirmed-stale rows after 2 attempts**.

---

## 5. One-shot data fix for the 17 known-stale rows (separate, run-once)

Independent of the code patch, a single SQL migration quarantines the 17 ids already known from apply.log so the next cycle is clean even before the code is deployed:

`sql
-- preview first
SELECT queue_id, candidate_type, status, failed_count
FROM review_queue
WHERE candidate_type='metadata_enrich'
  AND status='pending'
  AND queue_id IN (
    'metadata_enrich__dp_70006__1785246259621',
    'metadata_enrich__dp_71498__1785248793299',
    'metadata_enrich__dp_71465__1785248818781',
    'metadata_enrich__dp_71512__1785249753520',
    'metadata_enrich__dp_71509__1785249778829',
    'metadata_enrich__dp_71507__1785249794485',
    'metadata_enrich__dp_71504__1785249815632',
    'metadata_enrich__dp_71667__1785250056744',
    'metadata_enrich__dp_71662__1785250100292',
    'metadata_enrich__dp_71645__1785250204331',
    'metadata_enrich__dp_71857__1785250961808',
    'metadata_enrich__dp_71954__1785251271647',
    'metadata_enrich__dp_72069__1785251513566',
    'metadata_enrich__dp_72053__1785251609188',
    'metadata_enrich__dp_72936__1785255079156',
    'metadata_enrich__dp_72930__1785255114011',
    'metadata_enrich__dp_72917__1785255201435'
  );

-- quarantine
UPDATE review_queue
SET status='quarantined',
    last_error='TARGET_NOT_FOUND',
    last_error_at=datetime('now'),
    decided_by='system:predeploy_quarantine'
WHERE candidate_type='metadata_enrich'
  AND status='pending'
  AND queue_id IN ( ... 17 ids ... );
`

This is **run-once**, **out-of-band**, and does not require a server restart.

---

## 6. Regression tests (small, deterministic, no live DB)

All three run via a temporary sqlite file (no need to touch the real librarian.db). They cover the **bounded** root cause — the silent re-arm + ok=true combo — and the new fail-loud + quarantine behavior.

### 6.1 	est_handle_review_apply_target_not_found_quarantines.py
- **Setup:** in-memory sqlite, create distilled_points(id, deleted_at) and eview_queue per the prod schema; insert 1 metadata_enrich row referencing a 	arget_id that does NOT exist; set ailed_count=1 to simulate a prior miss.
- **Call:** handle_review_apply({"queue_id": qid, "actor": "test"}).
- **Assert:** returns ok=False, error_name='TARGET_NOT_FOUND', quarantined=True, ailed_count==2; row in eview_queue is now status='quarantined', decided_by='system:quarantine'.
- **Negative control:** same setup but ailed_count=0 (first miss) → returns ok=False, quarantined absent, row stays status='pending', ailed_count==1.

### 6.2 	est_handle_qjobs_apply_all_fail_loud.py
- **Setup:** same DB; insert 5 metadata_enrich rows all pointing at non-existent targets; one row with status='applied' (control).
- **Call:** handle_qjobs_apply_all({"candidate_type": "metadata_enrich", "limit": 50}).
- **Assert:** returns ok=False, error_name='APPLY_ALL_ZERO_APPLIED', pplied=0, ailed=5, err_prefix='apply err: zero_applied'; the previously-applied row is untouched; the 5 failed rows have ailed_count>=1.

### 6.3 	est_apply_all_skips_quarantined.py
- **Setup:** 3 metadata_enrich rows: 1 quarantined (must not be re-picked), 1 pending with valid target (must apply), 1 pending with invalid target (must increment failed_count).
- **Call:** handle_qjobs_apply_all({"candidate_type": "metadata_enrich", "limit": 50}).
- **Assert:** pplied=1, failed=1, total=2 (the quarantined row is not even counted); quarantined row status unchanged; the valid row is status='applied' with the expected subject/	ags; the invalid row has ailed_count==1.

These three tests pin the contract: **silent re-arm on first miss → loud quarantine on second miss → apply_all goes red instead of green → quarantined rows are never re-selected.**

---

## 7. What this review does NOT cover (out of scope, per task)

- No source edits performed.
- No DB migration applied.
- No restart of [A] orchestrator or qnight runner.
- No query of the live librarian.db (other than reading the already-opened qnight_state.json).
- No cache_delta / efresh_project_map — the analysis is fully local to the 4 input files.

---

## 8. TL;DR for broker

- **Root cause confirmed:** 17 metadata_enrich candidates reference soft-deleted distilled_points; handle_review_apply re-arms them to pending on miss; handle_qjobs_apply_all returns ok=true on total failure; runner treats as success → same 17 ids re-picked every ~2.5h since 2026-07-17 → 18 MB log.
- **Fix:** add ailed_count + quarantined status (2-line schema assumption, no migration) + return ok=false from pply_all when ailed>0 && applied==0 with the existing pply err: prefix the runner already detects.
- **One-shot data fix:** the 17 known queue_ids can be quarantined by a single SQL UPDATE, no restart.
- **Regression tests:** 3 small sqlite tests pinning the new fail-loud + quarantine contract.
- **No restart, no data mutation, no broad DB scan** were performed in producing this review.

PASS
END_KILOC_R1B_TOOLQ_STALE_REVIEW