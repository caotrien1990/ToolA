#!/usr/bin/env python3
"""Real query benchmark for ToolQ librarian hybrid search (V1).

Creates a golden set of real Vietnamese natural-language queries mapped to
known distilled_points, runs hybrid search (FTS + vector_chunks_v2 via /query API),
and computes recall@k / mrr@k with per-qtype breakdown + miss analysis.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

TOOLQ_BIN = Path(__file__).resolve().parent
TOOLQ_ROOT = TOOLQ_BIN.parent
sys.path.insert(0, str(TOOLQ_BIN))

REAL_TABLE = "real_golden_queries"
DEFAULT_API = "http://127.0.0.1:8082"
OUTPUT_FILE = TOOLQ_ROOT / "real_query_benchmark_v1.json"

VIET_STOPWORDS = {
    "với", "của", "có", "đã", "không", "các", "nhưng", "trong", "được",
    "một", "là", "vào", "này", "cũng", "cho", "thì", "sẽ", "tôi", "anh",
    "bạn", "nó", "họ", "chúng", "đó", "kia", "này", "ấy", "đây", "nên",
    "vì", "khi", "rằng", "nếu", "hay", "còn", "mà", "những", "cái", "để",
    "rất", "lên", "ra", "về", "qua", "lại", "đi", "ở", "đang", "the",
    "and", "for", "this", "that", "with", "from", "have", "has", "was",
    "were", "are", "will", "would", "could", "should", "been", "being",
    "can", "not", "but", "all", "any", "each", "every", "both", "few",
    "more", "most", "other", "some", "such", "only", "own", "same", "so",
    "than", "too", "very", "just", "now", "then", "here", "there", "when",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_table(conn: sqlite3.Connection) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {REAL_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_text TEXT NOT NULL,
            expected_point_ids TEXT NOT NULL,
            qtype TEXT NOT NULL,
            source_point_id INTEGER NOT NULL,
            built_at TEXT NOT NULL,
            batch_id TEXT NOT NULL
        )
    """)
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_rgq_qtype ON {REAL_TABLE}(qtype)")
    conn.commit()


def extract_vn_keywords(text: str, n: int = 6) -> list[str]:
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'[#*`_~\[\](){}\\|]', ' ', text)
    text = re.sub(r'[^\w\sđĐàáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựýỳỷỹỵ]', ' ', text, flags=re.IGNORECASE)
    text = text.lower()
    words = re.findall(r'[\w]{3,}', text, flags=re.UNICODE)
    words = [w for w in words if w not in VIET_STOPWORDS]
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    scored = [(w, freq[w] * len(w)) for w in set(words)]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [w for w, _ in scored[:n]]


def build_real_set(conn: sqlite3.Connection, n_paraphrase: int = 10, n_fill_blank: int = 5, n_ambiguous: int = 5) -> dict:
    total = n_paraphrase + n_fill_blank + n_ambiguous
    batch_id = f"real_v1_{utc_now()}"
    conn.execute(f"DELETE FROM {REAL_TABLE} WHERE batch_id LIKE 'real_v1_%'")

    rows = conn.execute("""
        SELECT dp.id, dp.subject, dp.point, dp.subtopic
        FROM distilled_points dp
        WHERE dp.qwen_verify_status = 'semantic_verified'
          AND length(coalesce(dp.point, '')) > 150
        ORDER BY random()
        LIMIT ?
    """, (total + 10,)).fetchall()

    if len(rows) < total:
        total = len(rows)
        n_paraphrase = min(n_paraphrase, total)
        n_fill_blank = min(n_fill_blank, max(0, total - n_paraphrase))
        n_ambiguous = min(n_ambiguous, max(0, total - n_paraphrase - n_fill_blank))

    selected = rows[:total]

    from random import Random as _Rng
    rng = _Rng(42)

    built = 0
    for i_local, row in enumerate(selected):
        point_id = row["id"]
        point_text = row["point"] or ""
        subject = (row["subject"] or "").strip()
        subtopic = (row["subtopic"] or "").strip()

        if i_local < n_paraphrase:
            qtype = "paraphrase"
        elif i_local < n_paraphrase + n_fill_blank:
            qtype = "fill_blank"
        else:
            qtype = "ambiguous"

        keywords = extract_vn_keywords(point_text, n=8)
        kw = keywords[:4] if keywords else [subject or subtopic or "hệ thống"]
        kw_phrase = " ".join(kw[:3]) if kw else "hệ thống"

        if qtype == "paraphrase":
            tmpls = [
                "Tôi muốn tìm hiểu về {kw}. Có ghi chú hay phân tích gì không?",
                "Hệ thống có gì liên quan đến {kw} không?",
                "Gần đây có thảo luận hay quyết định gì về {kw}?",
                "Cho tôi xem các điểm phân tích về chủ đề {kw}.",
                "Anh có nhớ gì về {kw} trong dự án không?",
            ]
            query_text = rng.choice(tmpls).format(kw=kw_phrase)
        elif qtype == "fill_blank":
            tmpls = [
                "Vấn đề liên quan đến {kw} đã được giải quyết chưa và kết quả ra sao?",
                "Lỗi {kw} được phát hiện từ khi nào và ai là người xử lý?",
                "Có bao nhiêu điểm đã được phân tích về {kw}?",
            ]
            query_text = rng.choice(tmpls).format(kw=kw_phrase)
        else:
            tmpls = [
                "có gì mới không?",
                "cho tôi xem những vấn đề gần đây",
                "hệ thống đang gặp lỗi gì?",
                "tóm tắt tình hình dự án",
                "cập nhật quan trọng nhất",
            ]
            query_text = rng.choice(tmpls)

        expected_ids = [point_id]
        if subject:
            related = conn.execute(
                "SELECT id FROM distilled_points WHERE subject=? AND id!=? AND qwen_verify_status='semantic_verified' LIMIT 2",
                (subject, point_id)
            ).fetchall()
            for rel in related:
                expected_ids.append(rel["id"])

        conn.execute(
            f"INSERT INTO {REAL_TABLE}(query_text, expected_point_ids, qtype, source_point_id, built_at, batch_id) VALUES (?, ?, ?, ?, ?, ?)",
            (query_text, json.dumps(expected_ids), qtype, point_id, utc_now(), batch_id),
        )
        built += 1

    conn.commit()
    return {"batch_id": batch_id, "built": built, "paraphrase": n_paraphrase, "fill_blank": n_fill_blank, "ambiguous": n_ambiguous}


def hybrid_search(query: str, top_k: int = 5, api: str = DEFAULT_API) -> list[dict] | None:
    url = f"{api}/query"
    payload = json.dumps({"query": query, "top_k": top_k}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("hits", []) if data.get("ok") else None
    except (urllib.error.URLError, ConnectionError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"  [WARN] API failed for '{query[:60]}...': {exc}", file=sys.stderr)
        return None


def recall_at_k(retrieved_ids: list[int], relevant_ids: set[int], k: int) -> float:
    return 1.0 if any(pid in relevant_ids for pid in retrieved_ids[:k]) else 0.0


def mrr_at_k(retrieved_ids: list[int], relevant_ids: set[int], k: int) -> float:
    for rank, pid in enumerate(retrieved_ids[:k], start=1):
        if pid in relevant_ids:
            return 1.0 / rank
    return 0.0


def run_benchmark(conn: sqlite3.Connection, k: int = 5, api: str = DEFAULT_API, use_api: bool = True) -> dict:
    queries = conn.execute(
        f"SELECT id, query_text, expected_point_ids, qtype, source_point_id FROM {REAL_TABLE} ORDER BY id"
    ).fetchall()
    if not queries:
        return {"error": "no queries in real_golden_queries table"}

    per_query: list[dict] = []
    totals = {"recall": 0.0, "mrr": 0.0}
    per_qtype: dict[str, dict] = {}
    misses: list[dict] = []

    for q in queries:
        query_text = q["query_text"]
        expected = set(json.loads(q["expected_point_ids"]))
        qtype = q["qtype"]

        start_ms = int(time.time() * 1000)
        hits = hybrid_search(query_text, top_k=k, api=api) if use_api else None
        elapsed_ms = int(time.time() * 1000) - start_ms
        if hits is None:
            hits = []

        retrieved_ids = []
        for h in hits:
            pid = h.get("point_id")
            if pid and int(pid) != 0:
                retrieved_ids.append(int(pid))
            else:
                chunk_id = int(h.get("id", 0))
                resolved = conn.execute(
                    "SELECT point_id FROM rag_chunks WHERE id=?", (chunk_id,)
                ).fetchone()
                if resolved:
                    retrieved_ids.append(int(resolved["point_id"]))

        r = recall_at_k(retrieved_ids, expected, k)
        m = mrr_at_k(retrieved_ids, expected, k)

        hit_info = [
            {"id": h.get("id"), "score": h.get("score"), "source": h.get("source"),
             "subtopic": str(h.get("subtopic", ""))[:100]}
            for h in hits[:k]
        ]

        result = {
            "query_id": q["id"], "query_text": query_text, "qtype": qtype,
            "expected": sorted(expected), "retrieved_ids": retrieved_ids,
            "retrieved_hits": hit_info,
            "recall_at_5": round(r, 4), "mrr_at_5": round(m, 4),
            "latency_ms": elapsed_ms,
        }
        per_query.append(result)
        totals["recall"] += r
        totals["mrr"] += m
        if r == 0:
            misses.append(result)
        if qtype not in per_qtype:
            per_qtype[qtype] = {"recall": 0.0, "mrr": 0.0, "n": 0, "misses": 0}
        per_qtype[qtype]["recall"] += r
        per_qtype[qtype]["mrr"] += m
        per_qtype[qtype]["n"] += 1
        if r == 0:
            per_qtype[qtype]["misses"] += 1

    n = len(queries)
    for qt in per_qtype:
        per_qtype[qt]["recall"] = round(per_qtype[qt]["recall"] / per_qtype[qt]["n"], 4)
        per_qtype[qt]["mrr"] = round(per_qtype[qt]["mrr"] / per_qtype[qt]["n"], 4)

    misses_detail = []
    for mq in misses[:10]:
        source_id = None
        for qr in queries:
            if qr["id"] == mq["query_id"]:
                source_id = qr["source_point_id"]
                break
        source_info = ""
        if source_id:
            row = conn.execute(
                "SELECT subject, substr(point,1,120) as p FROM distilled_points WHERE id=?", (source_id,)
            ).fetchone()
            if row:
                source_info = f"src_subj='{row['subject']}' src_pt='{row['p'][:80]}'"
        misses_detail.append({
            "query": mq["query_text"][:120],
            "qtype": mq["qtype"],
            "expected": mq["expected"],
            "got": mq["retrieved_ids"],
            "got_hints": [f"id={h['id']} src={h['source']} {h['subtopic'][:60]}" for h in mq.get("retrieved_hits", [])[:3]],
            "source_info": source_info,
        })

    return {
        "benchmark": "real_query_v1",
        "k": k, "n_queries": n,
        "overall": {"recall_at_5": round(totals["recall"] / n, 4), "mrr_at_5": round(totals["mrr"] / n, 4)},
        "per_qtype": per_qtype,
        "per_query": per_query,
        "misses": misses_detail,
        "miss_count": len(misses),
    }


def test_metrics() -> bool:
    """Quick sanity check that metric functions work correctly."""
    assert recall_at_k([1, 2, 3], {3}, 5) == 1.0
    assert recall_at_k([1, 2, 3], {5}, 5) == 0.0
    assert recall_at_k([1, 2], {3}, 5) == 0.0
    assert abs(mrr_at_k([1, 2, 3], {3}, 5) - 1.0 / 3) < 0.01
    assert abs(mrr_at_k([3, 1, 2], {3}, 5) - 1.0) < 0.01
    assert mrr_at_k([1, 2], {3}, 5) == 0.0
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Real Query Benchmark for ToolQ librarian")
    p.add_argument("--db", type=str, default=str(TOOLQ_ROOT / "librarian.db"))
    p.add_argument("--build", action="store_true")
    p.add_argument("--run", action="store_true")
    p.add_argument("--test", action="store_true", help="Run unit tests for metric functions")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--api", type=str, default=DEFAULT_API)
    p.add_argument("--direct", action="store_true")
    p.add_argument("--paraphrase", type=int, default=10)
    p.add_argument("--fill-blank", type=int, default=5)
    p.add_argument("--ambiguous", type=int, default=5)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.test:
        print(json.dumps({"test_metrics": test_metrics()}, ensure_ascii=False))
        return 0

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    init_table(conn)

    if args.build:
        result = build_real_set(conn, n_paraphrase=args.paraphrase, n_fill_blank=args.fill_blank, n_ambiguous=args.ambiguous)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.run:
        result = run_benchmark(conn, k=args.k, api=args.api, use_api=not args.direct)
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if not args.build and not args.run and not args.test:
        cnt = conn.execute(f"SELECT COUNT(*) FROM {REAL_TABLE}").fetchone()[0]
        print(json.dumps({"real_golden_queries": cnt, "message": "Use --build and/or --run"}, ensure_ascii=False, indent=2))

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
