const http = require("http");

function call(tool, body, fields) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const path =
      "/api/call/" + tool + (fields ? "?fields=" + fields : "");
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

const PP = "D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ";
const FIELDS =
  "ok,error_name,ca_next_action,validation,diff_preview,new_hash,backup_path,message,commit_state";

const runnerPatch = `            current = con.execute(
                "SELECT qwen_verify_status, vector_eligible, verify_status FROM distilled_points WHERE id=?",
                (pid,),
            ).fetchone()
            # CAS matches select_candidates: ve=0 and verify_status not terminal.
            if (
                not current
                or int(current["vector_eligible"] or 0) != 0
                or (current["verify_status"] or "") in ("rejected", "quarantine")
            ):
                continue
`;

const embedPatch = `_LIVE_ROWS_CACHE: dict = {"key": None, "ts": 0.0, "rows": ()}
_LIVE_ROWS_LOCK = threading.Lock()
_LIVE_ROWS_EPOCH = 0


def invalidate_live_rows_cache() -> None:
    """Bump generation so a tombstoned chain cannot be served from TTL cache."""
    global _LIVE_ROWS_EPOCH
    with _LIVE_ROWS_LOCK:
        _LIVE_ROWS_EPOCH += 1
        _LIVE_ROWS_CACHE["key"] = None
        _LIVE_ROWS_CACHE["ts"] = 0.0
        _LIVE_ROWS_CACHE["rows"] = ()


def _live_rows_ttl_sec() -> int:
    try:
        return max(0, int(os.environ.get("CLM_SEMANTIC_ROWS_TTL", "120")))
    except (TypeError, ValueError):
        return 120


def select_live_semantic_rows(
    conn: sqlite3.Connection,
    model_version: str = MODEL_VERSION,
    force_refresh: bool = False,
) -> list[sqlite3.Row]:
    """Return semantic-vector rows whose entire live chain is intact.

    Q-3 (FixA2507): defense-in-depth — a cascade miss in soft_delete_subtree must
    never let a tombstoned chunk/point/turn surface in retrieval. This is the
    single source of truth for liveness on the vector read path.

    Q-PERF (2026-08-25): results are cached in-process for
    CLM_SEMANTIC_ROWS_TTL seconds (default 120; 0 disables). New ingests become
    visible to semantic retrieval at most one TTL window late. Cache key is
    (model_version, _LIVE_ROWS_EPOCH); writers that tombstone must call
    invalidate_live_rows_cache() so the epoch changes inside the TTL window.
    """
    if force_refresh:
        invalidate_live_rows_cache()
    ttl = _live_rows_ttl_sec()
    now = time.time()
    cache_key = (model_version, _LIVE_ROWS_EPOCH)
    if ttl > 0:
        with _LIVE_ROWS_LOCK:
            if _LIVE_ROWS_CACHE["key"] == cache_key and (now - _LIVE_ROWS_CACHE["ts"]) < ttl:
                return list(_LIVE_ROWS_CACHE["rows"])
    rows = conn.execute(
        """
        SELECT vcs.chunk_id, vcs.point_id, vcs.embedding
        FROM vector_chunks_semantic vcs
        JOIN rag_chunks rc ON rc.id = vcs.chunk_id
        JOIN distilled_points dp ON dp.id = vcs.point_id
        JOIN raw_turns rt ON rt.id = rc.raw_turn_id
        WHERE vcs.model_version = ?
          AND vcs.deleted_at IS NULL
          AND rc.deleted_at IS NULL
          AND dp.deleted_at IS NULL
          AND rt.deleted_at IS NULL
        """,
        (model_version,),
    ).fetchall()
    if ttl > 0:
        with _LIVE_ROWS_LOCK:
            _LIVE_ROWS_CACHE["key"] = (model_version, _LIVE_ROWS_EPOCH)
            _LIVE_ROWS_CACHE["ts"] = time.time()
            _LIVE_ROWS_CACHE["rows"] = rows
    return rows
`;

const schemaPatch = `    if raw_ids:
        ph = ",".join("?" for _ in raw_ids); conn.execute(f"DELETE FROM zone_index WHERE raw_turn_id IN ({ph})", raw_ids)
    counts["total"] = sum(counts.values())
    if int(counts.get("total") or 0) > 0:
        try:
            from clm_semantic_embed import invalidate_live_rows_cache
            invalidate_live_rows_cache()
        except Exception:
            pass
    return counts
`;

const a33Patch = `        check("A33 first apply sees 3", first["candidates"] == 3, str(first))
        # ve=0 rows remain selectable after first apply (re-verify contract).
        check("A33 second apply still sees ve=0 rows", second["candidates"] == 2, str(second))
        check("A33 score rows include reverify", score_rows == 5, str(score_rows))
        check("A33 transition rows include reverify", transitions == 5, str(transitions))
`;

const bug3Test = `
def test_quarantine_qwen_status_reverify_writes() -> None:
    """BUG3: qwen_verify_status='quarantine' + ve=0 is selected and applied."""
    print("[BUG3] quarantine qwen_verify_status select+write")
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "db.sqlite"
        make_db(db)
        with closing(V.connect(db)) as con:
            con.execute(
                """
                INSERT INTO distilled_points(
                    id, raw_turn_id, subject, subtopic, point, evidence_snippet, confidence,
                    verify_status, qwen_verify_status, vector_eligible, verify_source, qwen_score, migration_batch_id
                ) VALUES(50, 50, 's', 'q', 'reverify me', 'e', 0.82, 'ok', 'quarantine', 0, 'qwen_auto', 0.30, 'predevine_v0')
                """
            )
            con.commit()
            selected = V.select_candidates(con, 20)
            ids = [int(r["id"]) for r in selected]
            check("select picks quarantine qwen ve=0", 50 in ids, str(ids))
            row = con.execute("SELECT * FROM distilled_points WHERE id=50").fetchone()
            decisions = V.build_decisions([row], "confidence_mock", args(db))
            check("decision built", len(decisions) == 1, str(decisions))
            V.write_decisions(con, decisions, "reverify_test")
            after = con.execute(
                "SELECT qwen_verify_status, vector_eligible, qwen_score FROM distilled_points WHERE id=50"
            ).fetchone()
            scores = con.execute(
                "SELECT COUNT(*) FROM qwen_semantic_scores WHERE distilled_point_id=50"
            ).fetchone()[0]
            check("write did not skip", after["qwen_verify_status"] == "semantic_verified", str(dict(after)))
            check("vector_eligible promoted", int(after["vector_eligible"]) == 1, str(dict(after)))
            check("score row written", scores == 1, str(scores))


`;

const mainPatch = `def main() -> int:
    test_thresholds()
    test_verify_tier_labels()
    test_verdict_mapping_matches_gate()
    test_verify_threshold_calibration()
    test_verify_latency_budget()
    test_consensus_voting_semantic_verified()
    test_dry_run_selects_only_legacy_candidates()
    test_verify_idempotent()
    test_quarantine_qwen_status_reverify_writes()
    test_apply_writes_scores_and_transitions_on_test_db()
    test_verify_quarantine_quarantined()
    test_mock_apply_blocked_without_flag()
    test_error_isolation_row_fails()
    test_apply_requires_yes()
    test_error_metrics_in_output()
    test_local_qwen_parser_and_fake_llama()
    test_local_qwen_run_guard()
    test_local_qwen_server_fake()
    test_openrouter_never_used_for_qwen_verify()
    test_mojibake_row_override()
    print(f"\\nRESULT: pass={PASS} fail={FAIL}")
    return 0 if FAIL == 0 else 1
`;

const cacheTest = `"""BUG4: semantic TTL cache must not return tombstoned live-chain rows."""
from __future__ import annotations

import os
import sqlite3
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from clm_lib.schema import ensure_schema, soft_delete_subtree


def _seed_live_row(conn: sqlite3.Connection):
    now = "2026-08-28T00:00:00Z"
    conn.execute(
        "INSERT INTO raw_turns(writer,session_id,project,source_path,source_line_start,source_line_end,"
        "turn_index,user_prompt,assistant_response,prompt_sha256,response_sha256,content_hash,created_at,zone) "
        "VALUES('codex','s','p','f',1,2,1,'q','a','p','r','h-cache',?,'Z4')",
        (now,),
    )
    raw_id = int(conn.execute("SELECT id FROM raw_turns").fetchone()[0])
    conn.execute(
        "INSERT INTO distilled_points(raw_turn_id,subject,point,created_at,vector_eligible) "
        "VALUES(?,?,?,?,1)",
        (raw_id, "s", "point", now),
    )
    point_id = int(conn.execute("SELECT id FROM distilled_points").fetchone()[0])
    conn.execute(
        "INSERT INTO rag_chunks(point_id,raw_turn_id,writer,session_id,subject,chunk_text,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (point_id, raw_id, "codex", "s", "s", "ttl_cache_token", now),
    )
    chunk_id = int(conn.execute("SELECT id FROM rag_chunks").fetchone()[0])
    fake_vec = struct.pack("1024f", *([0.0] * 1023 + [1.0]))
    conn.execute(
        "INSERT INTO vector_chunks_semantic(chunk_id,point_id,embedding,dim,model_version,created_at) "
        "VALUES(?,?,?,1024,'bge-m3-q8',?)",
        (chunk_id, point_id, fake_vec, now),
    )
    conn.commit()
    return raw_id, point_id, chunk_id


def test_ttl_cache_drops_tombstoned_point() -> None:
    os.environ["CLM_SEMANTIC_ROWS_TTL"] = "120"
    from clm_semantic_embed import (
        invalidate_live_rows_cache,
        select_live_semantic_rows,
    )

    invalidate_live_rows_cache()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    _raw_id, point_id, _chunk_id = _seed_live_row(conn)

    cached = select_live_semantic_rows(conn, "bge-m3-q8")
    assert len(cached) == 1, f"expected 1 live row before delete, got {len(cached)}"

    soft_delete_subtree(conn, point_ids=[point_id], actor="test_ttl_cache", reason="tombstone")
    after = select_live_semantic_rows(conn, "bge-m3-q8")
    assert after == [], f"cached tombstone leaked: {len(after)} rows"


if __name__ == "__main__":
    test_ttl_cache_drops_tombstoned_point()
    print("OK ttl cache tombstone")
`;

async function main() {
  const step = process.argv[2] || "all";
  const jobs = {
    runner: {
      tool: "write_fix",
      body: {
        project_path: PP,
        file_path: "bin/clm_qwen_verify_runner.py",
        start_line: 553,
        end_line: 558,
        new_content: b64(runnerPatch),
        content_encoding: "base64",
        dry_run: false,
      },
    },
    embed: {
      tool: "write_fix",
      body: {
        project_path: PP,
        file_path: "bin/clm_semantic_embed.py",
        start_line: 656,
        end_line: 707,
        new_content: b64(embedPatch),
        content_encoding: "base64",
        dry_run: false,
      },
    },
    schema: {
      tool: "write_fix",
      body: {
        project_path: PP,
        file_path: "bin/clm_lib/schema.py",
        start_line: 191,
        end_line: 194,
        new_content: b64(schemaPatch),
        content_encoding: "base64",
        dry_run: false,
      },
    },
    a33: {
      tool: "write_fix",
      body: {
        project_path: PP,
        file_path: "bin/clm_qwen_verify_runner_test.py",
        start_line: 231,
        end_line: 235,
        new_content: b64(a33Patch),
        content_encoding: "base64",
        dry_run: false,
      },
    },
    bug3: {
      tool: "write_fix",
      body: {
        project_path: PP,
        file_path: "bin/clm_qwen_verify_runner_test.py",
        start_line: 501,
        end_line: 501,
        new_content: b64(bug3Test + "def main() -> int:\n"),
        content_encoding: "base64",
        dry_run: false,
      },
    },
    main: {
      tool: "write_fix",
      body: {
        project_path: PP,
        file_path: "bin/clm_qwen_verify_runner_test.py",
        start_line: 501,
        end_line: 522,
        new_content: b64(bug3Test + mainPatch),
        content_encoding: "base64",
        expected_hash: "06ab79735ea5020a284d30ce1797c7070472dfd24dd665a66cd1430ef4169715",
        dry_run: false,
      },
    },
    neardup: {
      tool: "write_fix",
      body: {
        project_path: PP,
        file_path: "bin/clm_semantic_embed.py",
        start_line: 572,
        end_line: 574,
        new_content: b64(`                        if int(cur.rowcount > 0):
                            merge_into_existing(conn, int(dup_pid), actor="q7_near_dup_merge")
                            stats["merged"] = int(stats["merged"]) + 1
                            invalidate_live_rows_cache()
`),
        content_encoding: "base64",
        expected_hash: "d2284e0ac570e68a79f9381de89eb9a4b3e30729a325f8b84320b3dcbb10df35",
        dry_run: false,
      },
    },
    pyignore: {
      tool: "write_fix",
      body: {
        project_path: PP,
        file_path: "bin/clm_qwen_verify_runner_test.py",
        start_line: 522,
        end_line: 522,
        new_content: b64(`            decisions = V.build_decisions([row], "confidence_mock", args(db))  # type: ignore[arg-type]\n`),
        content_encoding: "base64",
        dry_run: false,
      },
    },
    watcher: {
      tool: "write_fix",
      body: {
        project_path: PP,
        file_path: "bin/clm_folder_watcher.py",
        start_line: 1397,
        end_line: 1398,
        new_content: b64(`    stats["total"] = sum(v for k, v in stats.items() if k != "total")
    if int(stats.get("total") or 0) > 0:
        try:
            from clm_semantic_embed import invalidate_live_rows_cache
            invalidate_live_rows_cache()
        except Exception:
            pass
    return stats
`),
        content_encoding: "base64",
        expected_hash: "01038d78ab1dcaa1870418322f51f67086c2ee3d884a681d1436e014b5955b10",
        dry_run: false,
      },
    },
    cachetest: {
      tool: "write_new_file",
      body: {
        project_path: PP,
        path: "tests/test_semantic_ttl_cache_tombstone.py",
        new_content: b64(cacheTest),
        content_encoding: "base64",
        overwrite: false,
        dry_run: false,
      },
    },
  };

  const order =
    step === "all"
      ? ["runner", "embed", "schema", "a33", "main", "cachetest"]
      : [step];
  for (const name of order) {
    const job = jobs[name];
    if (!job) {
      console.log("unknown step " + name);
      process.exit(1);
    }
    const result = await call(job.tool, job.body, FIELDS);
    console.log("=== " + name + " status=" + result.status + " ===");
    console.log(result.data);
  }
}

main().catch((err) => {
  console.log("ERR " + err);
  process.exit(1);
});
