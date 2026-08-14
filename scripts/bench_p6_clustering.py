#!/usr/bin/env python3
"""Phase 6 Step 25 - Clustering benchmark: Louvain (networkx, BSD) vs Leiden (GPL, blocked).

Read-only on graph.db (sqlite3 mode=ro URI). Writes nothing to the DB.
Builds an undirected networkx Graph from edges with kind IN
('calls','imports','defines','inherits'), dropping external targets (target LIKE 'ext:%')
and self-loops, then runs nx.community.louvain_communities(seed=42).

Usage:
    python bench_p6_clustering.py
    python bench_p6_clustering.py --sample 5
"""
import argparse
import json
import sqlite3
import time
from collections import Counter

DB_URI = (
    "file:D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/"
    "project-orchestrator/.orchestrator_cache/graph.db?mode=ro"
)
EDGE_KINDS = ("calls", "imports", "defines", "inherits")


def build_graph():
    """Open graph.db read-only and build an undirected nx.Graph from filtered edges."""
    import networkx as nx

    conn = sqlite3.connect(DB_URI, uri=True)
    cur = conn.cursor()

    # node metadata (id -> file_path/name/kind) for --sample alignment reporting
    meta = {}
    for nid, file_path, name, kind in cur.execute(
        "SELECT id, file_path, name, kind FROM nodes"
    ):
        meta[nid] = {"file_path": file_path, "name": name, "kind": kind}

    placeholders = ",".join("?" for _ in EDGE_KINDS)
    sql = (
        f"SELECT source, target, confidence FROM edges "
        f"WHERE kind IN ({placeholders}) "
        f"AND target NOT LIKE 'ext:%' "
        f"AND source NOT LIKE 'ext:%' "
        f"ORDER BY source, target, confidence, kind"
    )
    rows = cur.execute(sql, EDGE_KINDS).fetchall()
    conn.close()

    G = nx.Graph()
    skipped_self_loops = 0
    for s, t, conf in rows:
        if s == t:
            skipped_self_loops += 1
            continue  # self-loops do not affect undirected community structure
        w = float(conf) if conf is not None else 1.0
        if G.has_edge(s, t):
            if w > G[s][t].get("weight", 0.0):
                G[s][t]["weight"] = w
        else:
            G.add_edge(s, t, weight=w)
    return G, meta, skipped_self_loops


def main():
    ap = argparse.ArgumentParser(description="Phase 6 Step 25 Louvain clustering benchmark")
    ap.add_argument(
        "--sample",
        type=int,
        default=0,
        help="print N nodes per community for the 3 largest communities (module alignment)",
    )
    args = ap.parse_args()

    import networkx as nx

    G, meta, skipped_self_loops = build_graph()

    t0 = time.perf_counter()
    communities = nx.community.louvain_communities(G, seed=42, weight="weight")
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    modularity = nx.community.modularity(G, communities, weight="weight")

    sizes = sorted((len(cm) for cm in communities), reverse=True)

    out = {
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "skipped_self_loops": skipped_self_loops,
        "num_communities": len(communities),
        "modularity": round(modularity, 6),
        "top10_community_sizes": sizes[:10],
        "elapsed_ms": elapsed_ms,
    }

    if args.sample > 0:
        cms_sorted = sorted(communities, key=len, reverse=True)
        sample_out = []
        for i, cm in enumerate(cms_sorted[:3]):
            nodes_sample = list(cm)[: args.sample]
            sample_out.append(
                {
                    "community_rank": i + 1,
                    "size": len(cm),
                    "nodes": [
                        {
                            "id": nid,
                            "file_path": meta.get(nid, {}).get("file_path"),
                            "name": meta.get(nid, {}).get("name"),
                            "kind": meta.get(nid, {}).get("kind"),
                        }
                        for nid in nodes_sample
                    ],
                }
            )
        out["sample_top3_communities"] = sample_out

    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
