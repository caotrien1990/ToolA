#!/usr/bin/env python
"""soft_data_reconcile.py — read-only tool-surface overlap / bloat / conflict analyzer.

Complements scripts/sync_tool_matrix.py. The matrix audit finds MISSING / GHOST /
STALE references (a tool exists but has no schema, an inventory line points at a
retired tool, a skill references a missing tool). This script finds the *other*
half — the overlap and drag that makes the surface hard for a CA to use and hard
to maintain. It cross-checks EVERY soft-data layer:

  function/description  CONFUSABLE_PAIRS   two tools whose descriptions overlap
                        (IDF-weighted, family-aware) -> CA guesses -> retry.
  schema / params       PARAM_CONCEPT_CONFLICT  the same concept has DIFFERENT
                        canonical param names across tools (path vs file_path) ->
                        CA must memorise which tool wants which name.
  alias                 ALIAS_SHADOW (defect)  an arg-alias key collides with a
                        real param of the same tool -> user input silently remapped.
  hint / see_also       SEE_ALSO_DANGLING (defect)  a hint points at a tool that
                        does not exist;  SEE_ALSO_CONVERGENCE  many tools point at
                        one -> that one is canonical, the sources may be legacy.
  skill                 SKILL_OVERLAP  two skills claim a large shared tool set;
                        SKILL_TOOL_FANOUT  a tool is claimed by many skills.
  cách dùng / usage     usage-log frequency (WEAK, small window) feeds SLIM_CANDIDATE
                        only in intersection with a real structural problem.
  bloat                 DESC_BLOAT / DESC_EMPTY  slow or ambiguous tool selection.

Severity: DEFECTS (dangling see_also, alias shadow) are real bugs -> --ci exits 1.
Everything else is ADVISORY -> --ci still exits 0 (it never cries wolf over style).

ADVISORY findings are *candidates*, never verdicts: curated-core tools, tools on a
documented deprecation sunset, and intentional families/aliases are labeled so they
are not mistaken for junk. The script never edits or removes anything.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

# Boilerplate tokens stripped before description-similarity so that shared
# scaffolding ("T1 READ-ONLY ... project_path ...") does not create false
# redundancy pairs. Only *content* words drive the overlap score.
_STOP = {
    "t1", "t2", "t3", "t4", "read", "only", "readonly", "read-only", "the", "a",
    "an", "of", "for", "to", "and", "or", "with", "without", "that", "this",
    "is", "are", "be", "one", "call", "use", "used", "tool", "when", "if", "not",
    "never", "always", "return", "returns", "returned", "project", "project_path",
    "path", "file", "via", "per", "in", "on", "at", "by", "from", "into", "it",
    "its", "you", "your", "v15", "r8", "r13", "r20", "r27", "r29", "r40", "r41",
    "can", "must", "may", "no", "any", "all", "each", "so", "as", "than",
    "instead", "before", "after", "first", "then", "e", "g", "eg", "ie",
}


def _resolve_project_path(arg: str | None) -> Path:
    if arg:
        p = Path(arg).expanduser().resolve()
    else:
        p = Path(__file__).resolve().parent.parent
    if not p.exists():
        raise SystemExit(f"[ERR] project_path does not exist: {p}")
    return p


def _load_registry(project_root: Path):
    sys.path.insert(0, str(project_root))
    try:
        import code_tools as ct  # type: ignore
    except Exception as exc:
        raise SystemExit(f"[ERR] cannot import code_tools from {project_root}: {exc}")
    try:
        import orchestrator_server  # noqa: F401  (side-effect: late registrations)
    except Exception:
        pass
    return ct


def _content_words(desc: str) -> set:
    toks = re.findall(r"[a-z_][a-z_0-9]+", (desc or "").lower())
    return {t for t in toks if t not in _STOP and len(t) > 2}


def _skill_refs(project_root: Path, known: set) -> dict:
    """tool_name -> [skill_folder] — mirrors the _audit_tool_matrix_sync scan."""
    refs: dict[str, list[str]] = defaultdict(list)
    skills_dir = project_root / ".ToolA_Skill" / "skills"
    if not skills_dir.exists():
        return refs
    for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        md = skill_dir / "SKILL.md"
        if not md.exists():
            continue
        txt = md.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"tool:\s*['\"]?([^'\"\n]+)['\"]?", txt):
            for tn in re.split(r"[|,\s]+", m.group(1)):
                tn = tn.strip().strip('"').strip("'")
                if re.match(r"^[a-z_][a-z_0-9]+$", tn) and tn in known:
                    if skill_dir.name not in refs[tn]:
                        refs[tn].append(skill_dir.name)
    return refs


def _usage(project_root: Path) -> dict:
    """tool -> {count, ok, last} from the tool USAGE_LOG window."""
    stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "ok": 0, "last": ""})
    p = project_root / ".orchestrator_cache" / "USAGE_LOG.jsonl"
    if not p.exists():
        return stats
    # One record per CR / LF only: bytes.splitlines() never breaks at the U+2028 /
    # U+0085 that ensure_ascii=False keeps raw inside a record (str.splitlines did).
    for raw in p.read_bytes().splitlines():
        line = raw.decode("utf-8", errors="ignore").strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        t = r.get("tool")
        if not t:
            continue
        s = stats[t]
        s["count"] += 1
        if r.get("ok"):
            s["ok"] += 1
        ts = r.get("ts") or ""
        if ts and str(ts) > s["last"]:
            s["last"] = str(ts)
    return stats


def _family(name: str) -> str:
    """Intentional tool family = prefix before the first underscore (git_*,
    council_*, cbm_*, desktop_*, workflow_*, assert_*, cep_*). Same-family pairs
    are a designed CRUD/verb split, not redundancy."""
    return name.split("_", 1)[0] if "_" in name else name


def _arg_aliases(srv) -> dict:
    """tool -> {alias_param: canonical_param} from orchestrator_server._ARG_ALIASES."""
    aa = getattr(srv, "_ARG_ALIASES", None) if srv else None
    return dict(aa) if isinstance(aa, dict) else {}


def _param_concepts(arg_aliases: dict, tool_params: dict) -> dict:
    """Two precise views of param-name overlap, both grounded in _ARG_ALIASES:

    AMBIGUOUS_PARAM_NAME — an alias key that maps to >=2 DIFFERENT canonicals across
      tools (e.g. `query` -> name in find_symbol, pattern in regex_search): the same
      word means different things depending on the tool -> a genuine CA trap.

    PARAM_CONCEPT_CONFLICT — a concept (cluster of interchangeable names) whose
      canonical name disagrees across tools (path vs file_path). To avoid the
      transitive over-merge where a generic hub like `q` chains unrelated concepts
      into one blob, ambiguous keys are EXCLUDED as union-find edges (and reported
      separately above).
    """
    key_targets: dict[str, set] = defaultdict(set)
    for amap in arg_aliases.values():
        for alias, canon in amap.items():
            key_targets[alias].add(canon)
    ambiguous = {k: sorted(v) for k, v in key_targets.items() if len(v) >= 2}

    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for amap in arg_aliases.values():
        for alias, canon in amap.items():
            if alias in ambiguous:
                continue  # generic hub — do not merge distinct concepts through it
            union(alias, canon)

    clusters: dict[str, set] = defaultdict(set)
    for nm in list(parent):
        clusters[find(nm)].add(nm)

    conflicts = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        variants: dict[str, list] = defaultdict(list)
        for tname, params in tool_params.items():
            for p in (members & params):
                variants[p].append(tname)
        real = {p: v for p, v in variants.items() if v}
        if len(real) >= 2:
            conflicts.append({
                "concept": sorted(members),
                "canonical_variants": {p: sorted(t)[:6] for p, t in real.items()},
                "tool_count": sum(len(v) for v in real.values()),
            })
    conflicts.sort(key=lambda c: -c["tool_count"])
    return {"conflicts": conflicts, "ambiguous_names": ambiguous}


def _alias_broken_targets(arg_aliases: dict, tool_params: dict) -> list:
    """DEFECT: an arg-alias whose CANONICAL target is not a real param of the tool —
    the remapped value lands on a param the tool does not define, so it is dropped.
    (Only checked for tools whose schema we actually have.)"""
    broken = []
    for tool, amap in arg_aliases.items():
        real = tool_params.get(tool)
        if not real:
            continue
        for alias, canon in amap.items():
            if canon not in real:
                broken.append({"tool": tool, "alias": alias, "canonical": canon})
    return broken


def _see_also_anomalies(see_also_out: dict, known: set) -> dict:
    dangling = []
    convergence = Counter()
    for src, targets in see_also_out.items():
        for tgt in targets:
            if tgt not in known:
                dangling.append({"tool": src, "dangling_target": tgt})
            else:
                convergence[tgt] += 1
    conv = [{"target": t, "referenced_by": n}
            for t, n in convergence.most_common() if n >= 4]
    return {"dangling": dangling, "convergence": conv}


def _skill_index(project_root: Path, known: set) -> dict:
    """Per-skill tool set (filtered to real tools) -> skill-vs-skill overlap +
    tool fan-out across skills."""
    skills_dir = project_root / ".ToolA_Skill" / "skills"
    per_skill: dict[str, set] = {}
    if skills_dir.exists():
        for sd in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            md = sd / "SKILL.md"
            if not md.exists():
                continue
            txt = md.read_text(encoding="utf-8", errors="ignore")
            tools = set()
            for m in re.finditer(r"tool:\s*['\"]?([^'\"\n]+)['\"]?", txt):
                for tn in re.split(r"[|,\s]+", m.group(1)):
                    tn = tn.strip().strip('"').strip("'")
                    if tn in known:
                        tools.add(tn)
            per_skill[sd.name] = tools
    # skill-vs-skill shared tool set
    names = list(per_skill)
    overlaps = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            shared = per_skill[names[i]] & per_skill[names[j]]
            if len(shared) >= 4:
                overlaps.append({"a": names[i], "b": names[j],
                                 "shared": sorted(shared)})
    overlaps.sort(key=lambda o: -len(o["shared"]))
    # tool fan-out
    fanout = Counter()
    for tools in per_skill.values():
        for t in tools:
            fanout[t] += 1
    return {"overlaps": overlaps,
            "tool_fanout": [{"tool": t, "in_skills": n}
                            for t, n in fanout.most_common() if n >= 4]}


def build(project_root: Path) -> dict:
    ct = _load_registry(project_root)
    tool_map = ct.TOOL_MAP
    phases = ct.TOOL_PHASES
    curated = set(getattr(ct, "CURATED_CORE_TOOLS", ()) or ())
    if not curated:
        # CURATED_CORE_TOOLS is defined in registry_data, not re-exported by
        # code_tools — import it directly so curated tools are protected.
        try:
            from orchestrator.core.registry_data import CURATED_CORE_TOOLS as _cc
            curated = set(_cc or ())
        except Exception:
            curated = set()
    deprecated = set()
    # _DEPRECATED_TOOLS lives inside the V15 registration closure; recover it
    # from schema function.deprecated flags instead (authoritative at runtime).
    schemas = ct.TOOL_SCHEMAS

    meta: dict[str, dict] = {}
    see_also_out: dict[str, set] = defaultdict(set)
    see_also_in: dict[str, set] = defaultdict(set)
    for s in schemas:
        fn = s.get("function", {}) if isinstance(s, dict) else {}
        name = fn.get("name")
        if not name:
            continue
        params = (fn.get("parameters") or {}).get("properties") or {}
        required = (fn.get("parameters") or {}).get("required") or []
        desc = fn.get("description") or ""
        dep = bool(fn.get("deprecated"))
        if dep:
            deprecated.add(name)
        sa = fn.get("see_also") or {}
        for alt in sa:
            see_also_out[name].add(alt)
            see_also_in[alt].add(name)
        meta[name] = {
            "tier": phases.get(name, "?"),
            "deprecated": dep,
            "in_curated_core": name in curated,
            "desc": desc,
            "desc_len": len(desc),
            "param_count": len(params),
            "required_count": len(required),
            "words": _content_words(desc),
            "params": set(params),
        }

    known = set(tool_map.keys()) | set(meta.keys())
    skill_refs = _skill_refs(project_root, known)
    usage = _usage(project_root)

    # ---- Cross-layer overlap scans (schema / alias / hint / skill) ----
    srv = sys.modules.get("orchestrator_server")
    arg_aliases = _arg_aliases(srv)
    tool_params = {n: meta[n]["params"] for n in meta}
    param_concepts = _param_concepts(arg_aliases, tool_params)
    alias_broken = _alias_broken_targets(arg_aliases, tool_params)
    see_also_anom = _see_also_anomalies(see_also_out, known)
    skill_overlap = _skill_index(project_root, known)

    # Assemble per-tool record.
    records = {}
    for name in sorted(set(tool_map.keys()) | set(meta.keys())):
        m = meta.get(name, {})
        u = usage.get(name, {"count": 0, "ok": 0, "last": ""})
        records[name] = {
            "name": name,
            "tier": m.get("tier", phases.get(name, "?")),
            "in_code": name in tool_map,
            "has_schema": name in meta,
            "deprecated": m.get("deprecated", False),
            "in_curated_core": m.get("in_curated_core", False),
            "desc_len": m.get("desc_len", 0),
            "param_count": m.get("param_count", 0),
            "skill_refs": skill_refs.get(name, []),
            "see_also_in": sorted(see_also_in.get(name, set())),
            "see_also_out": sorted(see_also_out.get(name, set())),
            "usage_count": u["count"],
            "usage_ok": u["ok"],
            "usage_last": u["last"],
        }

    # ---- Redundancy pairs (IDF-weighted, family-aware) ----
    # Boilerplate ("git", "governed", "shell", "session"...) appears across many
    # tool descriptions, so it drives noise in a raw Jaccard. Weight by document
    # frequency: only DISTINCTIVE shared words (present in few tools) count, and
    # same-family pairs (git_status vs git_commit) are skipped as a designed
    # verb split, not redundancy.
    names = [n for n in meta if len(meta[n]["words"]) >= 3]
    df = Counter()
    for n in names:
        for w in meta[n]["words"]:
            df[w] += 1
    n_docs = len(names)
    distinctive_cap = max(2, round(0.08 * n_docs))  # word in <=8% of tools
    redundancy = []
    for i in range(len(names)):
        ni = names[i]
        wi = meta[ni]["words"]
        di = {w for w in wi if df[w] <= distinctive_cap}
        if len(di) < 2:
            continue
        for j in range(i + 1, len(names)):
            nj = names[j]
            if _family(ni) == _family(nj):
                continue  # intentional family split
            wj = meta[nj]["words"]
            dj = {w for w in wj if df[w] <= distinctive_cap}
            shared = di & dj
            if len(shared) < 2:
                continue
            jac = len(shared) / len(di | dj)
            if jac >= 0.45:
                redundancy.append({
                    "a": ni, "b": nj, "score": round(jac, 2),
                    "shared_distinctive": sorted(shared)[:8],
                    "a_deprecated": meta[ni]["deprecated"],
                    "b_deprecated": meta[nj]["deprecated"],
                })
    redundancy.sort(key=lambda r: (-len(r["shared_distinctive"]), -r["score"]))

    tools_in_redundancy = {n for r in redundancy for n in (r["a"], r["b"])}

    # ---- Flags (calibrated: structural signals, not the tiny usage window) ----
    flags = defaultdict(list)
    for name, r in records.items():
        if r["desc_len"] > 400:
            flags["DESC_BLOAT"].append(name)
        if r["has_schema"] and r["desc_len"] < 20:
            flags["DESC_EMPTY"].append(name)
        if r["deprecated"]:
            flags["DEPRECATED"].append(name)
            if not r["see_also_out"]:
                flags["DEPRECATED_NO_MIGRATION"].append(name)
        # SLIM_CANDIDATE = intersection of FOUR independent signals so the list
        # stays tight + defensible, and healthy tools are never pressured:
        #   unused in the log window   (weak signal, small window)
        #   AND not curated-core       (protected surface)
        #   AND not deprecated         (own sunset governs it)
        #   AND not referenced by any skill  (a skill ref = intentional + discoverable)
        #   AND structurally overlaps another tool (real redundancy, not just a
        #       short self-explanatory description like "click"/"fill").
        # A short description alone is NOT a slim signal — many core tools are
        # self-explanatory. Redundancy is what actually causes CA retry.
        if (r["usage_count"] == 0 and not r["in_curated_core"] and not r["deprecated"]
                and not r["skill_refs"] and name in tools_in_redundancy):
            flags["SLIM_CANDIDATE"].append(name)

    # DEFECTS = real bugs (not style): a hint pointing at a non-existent tool, or
    # an alias whose canonical target does not exist on the tool. These gate --ci.
    defect_count = len(see_also_anom["dangling"]) + len(alias_broken)

    usage_events = sum(r["usage_count"] for r in records.values())
    summary = {
        "total_tools": len(records),
        "by_tier": dict(Counter(r["tier"] for r in records.values())),
        "deprecated": sum(1 for r in records.values() if r["deprecated"]),
        "curated_core": sum(1 for r in records.values() if r["in_curated_core"]),
        "usage_log_events": usage_events,
        "usage_window_note": ("usage_count reflects ONLY the recent USAGE_LOG "
                              "window; count=0 means 'unused recently', NOT "
                              "'never used' — treat as a weak supporting signal"),
        "unused_in_window": sum(1 for r in records.values() if r["usage_count"] == 0),
        "redundancy_pairs": len(redundancy),
        "distinctive_word_cap_df": distinctive_cap,
        "flag_counts": {k: len(v) for k, v in flags.items()},
        "defect_count": defect_count,
        "param_concept_conflicts": len(param_concepts["conflicts"]),
        "ambiguous_param_names": len(param_concepts["ambiguous_names"]),
        "alias_broken_targets": len(alias_broken),
        "see_also_dangling": len(see_also_anom["dangling"]),
        "skill_overlaps": len(skill_overlap["overlaps"]),
        "arg_alias_tools": len(arg_aliases),
    }
    return {
        "summary": summary,
        "flags": {k: sorted(v) for k, v in flags.items()},
        "redundancy": redundancy,
        "param_concepts": param_concepts,
        "alias_broken_targets": alias_broken,
        "see_also": see_also_anom,
        "skills": skill_overlap,
        "records": records,
    }


def digest(report: dict) -> str:
    s = report["summary"]
    out = []
    out.append("# SOFT-DATA RECONCILE — tool-surface slim report (ADVISORY, read-only)")
    out.append(f"total_tools={s['total_tools']}  by_tier={s['by_tier']}")
    out.append(f"deprecated={s['deprecated']}  curated_core={s['curated_core']}  "
               f"usage_log_events={s['usage_log_events']}  "
               f"unused_in_window={s['unused_in_window']}")
    out.append(f"flags={s['flag_counts']}  redundancy_pairs={s['redundancy_pairs']} "
               f"(distinctive_word_cap_df={s['distinctive_word_cap_df']})")
    out.append(f"overlap: defects={s['defect_count']}  "
               f"param_concept_conflicts={s['param_concept_conflicts']}  "
               f"ambiguous_param_names={s['ambiguous_param_names']}  "
               f"alias_broken={s['alias_broken_targets']}  "
               f"see_also_dangling={s['see_also_dangling']}  "
               f"skill_overlaps={s['skill_overlaps']}  arg_alias_tools={s['arg_alias_tools']}")
    out.append(f"NOTE: {s['usage_window_note']}")
    out.append("")

    # DEFECTS first — these are real bugs, not style.
    broken = report.get("alias_broken_targets", [])
    dangling = report.get("see_also", {}).get("dangling", [])
    if broken or dangling:
        out.append(f"## DEFECTS ({len(broken) + len(dangling)}) — fix these")
        for d in dangling:
            out.append(f"  see_also DANGLING: {d['tool']} -> {d['dangling_target']} "
                       f"(target is not a registered tool)")
        for b in broken:
            out.append(f"  ALIAS BROKEN TARGET: {b['tool']} alias '{b['alias']}' "
                       f"-> '{b['canonical']}' which is not a param of the tool")
        out.append("")

    amb = report.get("param_concepts", {}).get("ambiguous_names", {})
    if amb:
        out.append(f"## AMBIGUOUS PARAM NAME — same key means different things per "
                   f"tool (CA trap) [{len(amb)}]")
        for k in sorted(amb, key=lambda k: -len(amb[k]))[:12]:
            out.append(f"  '{k}' -> canonical varies: {amb[k]}")
        out.append("")

    pcc = report.get("param_concepts", {}).get("conflicts", [])
    if pcc:
        out.append(f"## PARAM-NAME CONFLICT — same concept, different canonical "
                   f"name across tools (CA must memorise) [{len(pcc)}]")
        for c in pcc[:12]:
            variants = "  ".join(
                f"{p}={tools}" for p, tools in c["canonical_variants"].items())
            out.append(f"  concept {c['concept']}:")
            out.append(f"      {variants}")
        out.append("")

    if report["redundancy"]:
        out.append(f"## CONFUSABLE PAIRS — cross-family description overlap "
                   f"(the retry driver) [{len(report['redundancy'])}]")
        for r in report["redundancy"][:25]:
            dep = ""
            if r["a_deprecated"] or r["b_deprecated"]:
                dep = "  [one deprecated → likely intended replacement]"
            out.append(f"  {r['score']:.2f}  {r['a']} <> {r['b']}  "
                       f"shared={r['shared_distinctive']}{dep}")
        out.append("")
    for cat, title in (
        ("SLIM_CANDIDATE", "SLIM CANDIDATE — unused-in-window AND (overlaps / no desc); review"),
        ("DEPRECATED_NO_MIGRATION", "DEPRECATED WITHOUT see_also MIGRATION PATH"),
        ("DEPRECATED", "DEPRECATED (informational)"),
        ("DESC_EMPTY", "EMPTY/near-empty DESCRIPTION — CA cannot tell what it does"),
        ("DESC_BLOAT", "BLOATED DESCRIPTION >400 chars — slow tool selection"),
    ):
        names = report["flags"].get(cat, [])
        if names:
            out.append(f"## {title} ({len(names)})")
            out.append("  " + ", ".join(names[:40]))
            if len(names) > 40:
                out.append(f"  ... +{len(names) - 40} more")
            out.append("")

    conv = report.get("see_also", {}).get("convergence", [])
    if conv:
        out.append("## see_also CONVERGENCE (informational — the target is the "
                   "canonical tool; heavy sources may be legacy)")
        for c in conv[:10]:
            out.append(f"  {c['target']}  <- referenced by {c['referenced_by']} tools")
        out.append("")

    skov = report.get("skills", {}).get("overlaps", [])
    if skov:
        out.append(f"## SKILL OVERLAP — skill pairs sharing >=4 tools "
                   f"(merge/clarify?) [{len(skov)}]")
        for o in skov[:10]:
            out.append(f"  {o['a']} <> {o['b']}  shared={o['shared']}")
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="soft_data_reconcile")
    ap.add_argument("--project-path", default=None)
    ap.add_argument("--json-out", default=None,
                    help="Write full report JSON to this path.")
    ap.add_argument("--ci", action="store_true",
                    help="Compact summary; exit 1 only on DEFECTS (dangling "
                         "see_also / alias shadow), 0 otherwise. Advisory findings "
                         "never fail the gate.")
    args = ap.parse_args(argv)
    root = _resolve_project_path(args.project_path)
    report = build(root)
    s = report["summary"]
    if args.ci:
        defects = report.get("alias_broken_targets", []) + \
            report.get("see_also", {}).get("dangling", [])
        if defects:
            print(f"[FAIL] soft-data defects={len(defects)}:", file=sys.stderr)
            for d in defects:
                print(f"  {d}", file=sys.stderr)
        else:
            print(f"[OK] no soft-data defects. total_tools={s['total_tools']} "
                  f"param_concept_conflicts={s['param_concept_conflicts']} "
                  f"redundancy_pairs={s['redundancy_pairs']} (advisory)",
                  file=sys.stderr)
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1 if s["defect_count"] else 0

    print(digest(report))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[json] {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
