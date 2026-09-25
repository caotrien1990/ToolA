"""Python cross-module call-edge resolver prototype (Phase 6 Step 24).

Uses jedi (MIT) to re-check `calls` edges that the ast extractor emitted with
confidence < 1.0 (inferred 0.9 / dynamic 0.7). Two modes:

* REPORT-ONLY (``resolve_benchmark``): the graph DB is opened read-only and
  never written — per the P2 guardrail, no experiment touches live data
  without a benchmark win. Output is a benchmark dict: confirmed / refuted /
  unresolved counts + per-confidence breakdown, i.e. the false-positive rate
  and resolution gain the plan asks for.
* APPLY (``resolve_apply``): after a benchmark run is accepted, promotes
  confirmed edges to confidence=1.0 (cbm_label='lsp') and optionally refutes
  class-0.7 edges. Defaults to ``dry_run=True`` so the first call is still
  side-effect free; pass ``dry_run=False`` to write.

Rollback: this module is additive; not imported by the indexer. Disabling it
is simply not running it. Apply-mode changes can be reverted by re-running the
AST extractor (``cbm_incremental_index``), which re-emits original confidence.
"""
import json
import re
import os
import sqlite3
import time
from collections import defaultdict

from ..text_lines import split_lines
from .config import graph_db_path, CBM_EXACT

_DEFAULT_SAMPLE = 300


def _get_ro_conn(project_path: str) -> sqlite3.Connection:
    path = graph_db_path(project_path)
    uri = f"file:{path.replace(os.sep, '/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _get_rw_conn(project_path: str) -> sqlite3.Connection:
    """Read-write connection for apply mode (drops the ?mode=ro guard)."""
    path = graph_db_path(project_path)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _load_candidates(conn: sqlite3.Connection, limit: int) -> list[dict]:
    """Internal call edges below EXACT confidence, joined with node metadata.

    Deterministic order (edge id) so repeated benchmark runs sample the same
    set (P5 determinism lesson).
    """
    rows = conn.execute(
        "SELECT e.rowid AS erid, e.source, e.target, e.line, e.confidence, "
        "       s.file_path AS src_file, s.name AS src_name, "
        "       t.file_path AS tgt_file, t.start_line AS tgt_line, "
        "       t.name AS tgt_name, t.kind AS tgt_kind, "
        "       t.qualified_name AS tgt_qname "
        "FROM edges e "
        "JOIN nodes s ON s.id = e.source "
        "JOIN nodes t ON t.id = e.target "
        "WHERE e.kind = 'calls' AND e.confidence < 1.0 "
        "ORDER BY e.rowid LIMIT ?;", (limit,)).fetchall()
    return [dict(r) for r in rows]


def _callee_column(line_text: str, callee: str) -> int | None:
    """Best-effort column of the callee name on its call line (0-based).

    Word-boundary matched (OpeC7 BUG-1): a bare find() would hit substrings
    like "log" inside "logger.info(" and hand jedi a wrong column.
    """
    m = re.search(rf"(?<![\w$]){re.escape(callee)}\s*\(", line_text)
    if m is None:
        m = re.search(rf"(?<![\w$]){re.escape(callee)}(?![\w$])", line_text)
    return m.start() if m else None


def resolve_benchmark(project_path: str, *, sample: int = _DEFAULT_SAMPLE) -> dict:
    """Benchmark jedi against existing sub-EXACT call edges (read-only).

    Verdict per edge:
      confirmed  — jedi's definition lands on the edge's target node
                   (same file, same start line ±1 for decorators).
      refuted    — jedi resolved the call to a different indexed location.
      unresolved — jedi found nothing / errored / call site not locatable.
    """
    t0 = time.monotonic()
    out: dict = {"project_path": project_path, "sample": 0,
                 "confirmed": 0, "refuted": 0, "unresolved": 0,
                 "by_confidence": {}, "errors": [], "elapsed_ms": 0.0}
    try:
        import jedi
    except ImportError as e:
        out["errors"].append(f"jedi_unavailable: {e}")
        return out
    try:
        conn = _get_ro_conn(project_path)
        try:
            cands = _load_candidates(conn, sample)
        finally:
            conn.close()
        out["sample"] = len(cands)
        by_conf: dict = defaultdict(lambda: {"confirmed": 0, "refuted": 0,
                                             "unresolved": 0})
        project = jedi.Project(project_path)
        by_file: dict = defaultdict(list)
        for c in cands:
            by_file[c["src_file"]].append(c)
        src_cache: dict = {}
        for rel, edges in by_file.items():
            abs_p = os.path.join(project_path, rel.replace("/", os.sep))
            try:
                if rel not in src_cache:
                    with open(abs_p, "r", encoding="utf-8-sig",
                              errors="replace") as f:
                        src_cache[rel] = f.read()
                source = src_cache[rel]
                # ast's line ends (CRLF/CR/LF, see text_lines): edge lines are ast
                # linenos, and a form feed in a comment made splitlines() hand back
                # the line above (#32).
                lines = split_lines(source)
                script = jedi.Script(source, path=abs_p, project=project)
            except Exception as e:
                for c in edges:
                    out["unresolved"] += 1
                    by_conf[c["confidence"]]["unresolved"] += 1
                out["errors"].append(f"script_failed:{rel}: {type(e).__name__}")
                continue
            for c in edges:
                bucket = by_conf[c["confidence"]]
                line_no = c["line"] or 0
                if not (1 <= line_no <= len(lines)):
                    out["unresolved"] += 1
                    bucket["unresolved"] += 1
                    continue
                col = _callee_column(lines[line_no - 1], c["tgt_name"])
                if col is None:
                    out["unresolved"] += 1
                    bucket["unresolved"] += 1
                    continue
                try:
                    defs = script.infer(line_no, col)
                except Exception:
                    defs = []
                if not defs:
                    out["unresolved"] += 1
                    bucket["unresolved"] += 1
                    continue
                hit = False
                miss_indexed = False
                for d in defs:
                    mp = str(d.module_path or "").replace(os.sep, "/")
                    if not (mp == c["tgt_file"] or mp.endswith("/" + c["tgt_file"])):
                        if mp.startswith(project_path.replace(os.sep, "/")):
                            miss_indexed = True
                        continue
                    if d.line is not None and abs(d.line - c["tgt_line"]) <= 1:
                        hit = True
                        break
                    miss_indexed = True
                if hit:
                    out["confirmed"] += 1
                    bucket["confirmed"] += 1
                elif miss_indexed:
                    out["refuted"] += 1
                    bucket["refuted"] += 1
                else:
                    out["unresolved"] += 1
                    bucket["unresolved"] += 1
        out["by_confidence"] = {str(k): v for k, v in sorted(by_conf.items())}
        checked = out["confirmed"] + out["refuted"]
        out["fp_rate_of_checked"] = (round(out["refuted"] / checked, 4)
                                     if checked else None)
        out["resolution_gain"] = (round(out["confirmed"] / out["sample"], 4)
                                  if out["sample"] else None)
    except Exception as e:
        out["errors"].append(f"benchmark_failed: {type(e).__name__}: {e}")
    out["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return out


def resolve_apply(project_path: str, *, sample: int = _DEFAULT_SAMPLE,
                  dry_run: bool = True) -> dict:
    """Apply jedi verdicts to the graph DB.

    Promotes ``confirmed`` call edges to confidence=CBM_EXACT (1.0) with
    cbm_label='lsp' so graph queries treat them as authoritative. Refuted /
    unresolved edges are left untouched (conservative: only positive
    confirmations are written). Defaults to ``dry_run=True`` — the first call
    returns the same benchmark dict as ``resolve_benchmark`` plus an
    ``applied_edges`` list, but performs no DB write. Pass ``dry_run=False``
    to commit.

    Returns the benchmark dict extended with ``dry_run``, ``applied`` (count),
    and ``applied_edges`` (list of edge rowids).
    """
    t0 = time.monotonic()
    out: dict = {"project_path": project_path, "sample": 0,
                 "confirmed": 0, "refuted": 0, "unresolved": 0,
                 "by_confidence": {}, "errors": [], "elapsed_ms": 0.0,
                 "dry_run": dry_run, "applied": 0, "applied_edges": []}
    try:
        import jedi
    except ImportError as e:
        out["errors"].append(f"jedi_unavailable: {e}")
        return out
    try:
        conn = _get_rw_conn(project_path)
        try:
            cands = _load_candidates(conn, sample)
        except Exception:
            conn.close()
            raise
        out["sample"] = len(cands)
        by_conf: dict = defaultdict(lambda: {"confirmed": 0, "refuted": 0,
                                             "unresolved": 0})
        project = jedi.Project(project_path)
        by_file: dict = defaultdict(list)
        confirmed_erids: list[int] = []
        for c in cands:
            by_file[c["src_file"]].append(c)
        src_cache: dict = {}
        for rel, edges in by_file.items():
            abs_p = os.path.join(project_path, rel.replace("/", os.sep))
            try:
                if rel not in src_cache:
                    with open(abs_p, "r", encoding="utf-8-sig",
                              errors="replace") as f:
                        src_cache[rel] = f.read()
                source = src_cache[rel]
                lines = split_lines(source)
                script = jedi.Script(source, path=abs_p, project=project)
            except Exception as e:
                for c in edges:
                    out["unresolved"] += 1
                    by_conf[c["confidence"]]["unresolved"] += 1
                out["errors"].append(f"script_failed:{rel}: {type(e).__name__}")
                continue
            for c in edges:
                bucket = by_conf[c["confidence"]]
                line_no = c["line"] or 0
                if not (1 <= line_no <= len(lines)):
                    out["unresolved"] += 1
                    bucket["unresolved"] += 1
                    continue
                col = _callee_column(lines[line_no - 1], c["tgt_name"])
                if col is None:
                    out["unresolved"] += 1
                    bucket["unresolved"] += 1
                    continue
                try:
                    defs = script.infer(line_no, col)
                except Exception:
                    defs = []
                if not defs:
                    out["unresolved"] += 1
                    bucket["unresolved"] += 1
                    continue
                hit = False
                miss_indexed = False
                for d in defs:
                    mp = str(d.module_path or "").replace(os.sep, "/")
                    if not (mp == c["tgt_file"] or mp.endswith("/" + c["tgt_file"])):
                        if mp.startswith(project_path.replace(os.sep, "/")):
                            miss_indexed = True
                        continue
                    if d.line is not None and abs(d.line - c["tgt_line"]) <= 1:
                        hit = True
                        break
                    miss_indexed = True
                if hit:
                    out["confirmed"] += 1
                    bucket["confirmed"] += 1
                    confirmed_erids.append(c["erid"])
                elif miss_indexed:
                    out["refuted"] += 1
                    bucket["refuted"] += 1
                else:
                    out["unresolved"] += 1
                    bucket["unresolved"] += 1
        out["by_confidence"] = {str(k): v for k, v in sorted(by_conf.items())}
        checked = out["confirmed"] + out["refuted"]
        out["fp_rate_of_checked"] = (round(out["refuted"] / checked, 4)
                                     if checked else None)
        out["resolution_gain"] = (round(out["confirmed"] / out["sample"], 4)
                                  if out["sample"] else None)
        out["applied_edges"] = confirmed_erids
        # Commit confirmed edges to EXACT confidence (only when not dry-run).
        if not dry_run and confirmed_erids:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                "UPDATE edges SET confidence=?, cbm_label=? WHERE rowid=?",
                [(CBM_EXACT, "lsp", erid) for erid in confirmed_erids])
            conn.commit()
            out["applied"] = len(confirmed_erids)
        else:
            out["applied"] = 0 if dry_run else len(confirmed_erids)
        conn.close()
    except Exception as e:
        out["errors"].append(f"apply_failed: {type(e).__name__}: {e}")
    out["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return out


if __name__ == "__main__":  # pragma: no cover — manual benchmark/apply entry
    import argparse
    ap = argparse.ArgumentParser(description="jedi call-edge resolver (benchmark / apply)")
    ap.add_argument("project_path")
    ap.add_argument("--sample", type=int, default=_DEFAULT_SAMPLE)
    ap.add_argument("--apply", action="store_true",
                    help="promote confirmed edges to EXACT (writes DB; "
                         "still dry-run unless --commit)")
    ap.add_argument("--commit", action="store_true",
                    help="with --apply, commit the DB write (default: dry-run)")
    args = ap.parse_args()
    if args.apply:
        print(json.dumps(resolve_apply(args.project_path, sample=args.sample,
                                        dry_run=not args.commit),
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps(resolve_benchmark(args.project_path, sample=args.sample),
                         ensure_ascii=False, indent=2))
