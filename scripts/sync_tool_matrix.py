#!/usr/bin/env python
"""V15 R15 — sync_tool_matrix.py

Standalone audit wrapper for the 4-layer architecture sync:
    Backend Code → API Schema → Skill/Docs → Tool Surface Reference

Imports `code_tools._audit_tool_matrix_sync` directly (no HTTP) so it works
even when the orchestrator server is down — suitable for pre-commit hooks
and CI runners.

Modes
-----
--ci             Full audit; exit 1 if drift_count > 0, exit 0 if clean.
                 Compact stderr summary for CI logs.

--summary-only   Lightweight payload (~2-5KB): only core counts. Exit 0 always.
                 Intended for hourly Task Scheduler / cron monitoring.

--check          Full coverage + drift list + suggested_fix per item.
                 Supports `--output json|md` (default json).
                 4 mandatory scenarios (see skill 21_tool_matrix):
                   1. After adding a new T4/T2 mutating tool
                   2. After refactoring tool architecture
                   3. Weekly / pre-release audit
                   4. Debugging suspected drift in CA workflow

Optional flags
--------------
--project-path PATH   Override audit target (default: script's parent
                      project-orchestrator/ folder).
--output {json,md}    Output format for --check (json default).
--update              Reserved for future auto-fix (V15 R15+). Currently a
                      no-op that prints what *would* be fixed.

--generate            Regenerate .ToolA_Skill/references/tool_surface_current.md
                      from the live TOOL_MAP + TOOL_PHASES source of truth
                      (no HTTP needed). Writes the file and prints a summary.
                      Use after adding/removing tools so the reference doc
                      cannot drift (D1 — ghost entries).

Exit codes
----------
0   Clean (drift_count == 0) or monitor mode
1   Drift detected (--ci mode only)
2   Script error (import failure, missing project_path, etc.)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Force UTF-8 stdout/stderr on Windows so we can print Unicode (✅/═) safely.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


def _resolve_project_path(arg: str | None) -> Path:
    if arg:
        p = Path(arg).expanduser().resolve()
    else:
        # Default: script's parent (scripts/ is inside project-orchestrator/)
        p = Path(__file__).resolve().parent.parent
    if not p.exists():
        raise SystemExit(f"[ERR] project_path does not exist: {p}")
    return p


def _import_audit(project_root: Path):
    """Add project_root to sys.path then import code_tools._audit_tool_matrix_sync."""
    sys.path.insert(0, str(project_root))
    try:
        import code_tools  # type: ignore
    except Exception as exc:
        raise SystemExit(f"[ERR] cannot import code_tools from {project_root}: {exc}") from exc
    if not hasattr(code_tools, "_audit_tool_matrix_sync"):
        raise SystemExit("[ERR] code_tools._audit_tool_matrix_sync not found (V15 R13+ required)")
    return code_tools._audit_tool_matrix_sync


def _suggested_fix(drift_item: dict) -> str:
    """Heuristic remediation hint per drift type."""
    t = drift_item.get("type", "")
    tool = drift_item.get("tool", "<unknown>")
    if t == "missing_schema":
        return (f"Add TOOL_SCHEMAS entry for '{tool}' in code_tools.py "
                "(use _v15_schema(name, description, props, required)).")
    if t == "ghost_schema":
        return (f"Either add '{tool}' to TOOL_MAP dispatch dict, "
                f"OR remove its TOOL_SCHEMAS entry.")
    if t == "skill_ref_missing_tool":
        skill_refs = drift_item.get("skill_refs", [])
        return (f"Skill(s) {skill_refs} reference '{tool}' but it is not in TOOL_MAP. "
                "Fix the skill SKILL.md typo, or register the tool.")
    if t == "inventory_outdated":
        return (f"Add `{tool}` to the appropriate tier section in "
            ".ToolA_Skill/references/tool_surface_current.md.")
    if t == "inventory_ghost":
        return (f"Remove `{tool}` from .ToolA_Skill/references/tool_surface_current.md "
                "— it is not registered in TOOL_MAP. Prefer regenerating the file "
                "via scripts/sync_tool_matrix.py --generate.")
    if t == "obsolete_payload_in_harness":
        return (f"Update harness file {drift_item.get('file', '?')}:"
                f"{drift_item.get('line', '?')} — pattern '{drift_item.get('pattern', '?')}' "
                "is deprecated. See skill 15_search_code_intel for the current transport contract.")
    return "Manual review required."


def cmd_ci(audit_fn, project_path: Path) -> int:
    result = audit_fn(str(project_path))
    summary = result.get("summary", {})
    drift = result.get("drift", []) or []
    if not drift:
        print(f"[OK] Tool matrix clean. code_tools={summary.get('code_tools')} "
              f"schema_tools={summary.get('schema_tools')} drift_count=0", file=sys.stderr)
        return 0
    # Drift > 0 → block
    print(f"[FAIL] Tool matrix drift detected: drift_count={len(drift)}", file=sys.stderr)
    print(f"       code_tools={summary.get('code_tools')} schema_tools={summary.get('schema_tools')} "
          f"skill_tools={summary.get('skill_tools')} inventory_tools={summary.get('inventory_tools')}",
          file=sys.stderr)
    print("[FAIL] First 5 drift items:", file=sys.stderr)
    for item in drift[:5]:
        sev = item.get("severity", "?")
        typ = item.get("type", "?")
        tool = item.get("tool", item.get("file", "?"))
        print(f"  - [{sev}] {typ}: {tool}", file=sys.stderr)
    if len(drift) > 5:
        print(f"  ... and {len(drift) - 5} more. Run --check for full list.", file=sys.stderr)
    print("[HINT] Run 'python scripts/sync_tool_matrix.py --check --output md' for full report + suggested fixes.",
          file=sys.stderr)
    return 1


def cmd_summary_only(audit_fn, project_path: Path) -> int:
    result = audit_fn(str(project_path))
    summary = result.get("summary", {})
    payload = {
        "generated_at": result.get("generated_at"),
        "project_path": str(project_path),
        "summary": {
            "code_tools": summary.get("code_tools"),
            "schema_tools": summary.get("schema_tools"),
            "drift_count": summary.get("drift_count"),
            "ok_count": summary.get("ok_count"),
        },
        "ca_next_action": result.get("ca_next_action"),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0  # Monitor mode never blocks


def cmd_check(audit_fn, project_path: Path, output_fmt: str) -> int:
    result = audit_fn(str(project_path))
    # Always include coverage in --check mode
    drift = result.get("drift", []) or []
    # Augment each drift item with suggested_fix
    for item in drift:
        item["suggested_fix"] = _suggested_fix(item)

    payload = {
        "generated_at": result.get("generated_at"),
        "project_path": str(project_path),
        "summary": result.get("summary", {}),
        "drift": drift,
        "coverage": result.get("coverage", {}),
        "router_topics_count": result.get("router_topics_count"),
        "matrix_log_path": result.get("matrix_log_path"),
        "ca_next_action": result.get("ca_next_action"),
    }

    if output_fmt == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:  # md
        s = payload["summary"]
        print("# Tool Matrix Audit — Full Check\n")
        print(f"- generated_at: `{payload['generated_at']}`")
        print(f"- project_path: `{payload['project_path']}`\n")
        print("## Summary\n")
        for k, v in s.items():
            print(f"- **{k}**: {v}")
        print()
        if not drift:
            print("✅ **No drift detected.** All 4 layers are in sync.\n")
        else:
            print(f"## Drift ({len(drift)} items)\n")
            for i, item in enumerate(drift, 1):
                print(f"### {i}. [{item.get('severity', '?')}] {item.get('type', '?')}")
                tool = item.get("tool", item.get("file", "?"))
                print(f"- target: `{tool}`")
                if item.get("detail"):
                    print(f"- detail: {item['detail']}")
                print(f"- **suggested_fix**: {item['suggested_fix']}\n")
        print(f"\n_Matrix log: `{payload['matrix_log_path']}`_")
    # --check exit 0 (informational; CI gating is --ci's job)
    return 0


def cmd_update(audit_fn, project_path: Path) -> int:
    """Reserved scaffold — V15 R15+ will fill in auto-fix logic."""
    result = audit_fn(str(project_path))
    drift = result.get("drift", []) or []
    print(f"[--update SCAFFOLD] Would auto-fix {len(drift)} drift items:", file=sys.stderr)
    for item in drift:
        print(f"  - {_suggested_fix(item)}", file=sys.stderr)
    print("[--update] Not yet implemented. Apply fixes manually then re-run --ci.",
          file=sys.stderr)
    return 0


def cmd_generate(project_path: Path) -> int:
    """D1 (2026-08-11) — regenerate tool_surface_current.md from the live
    TOOL_MAP + TOOL_PHASES source of truth. Closes the 13-ghost drift by
    replacing manual edits with a reproducible generator. Idempotent: two
    consecutive runs against an unchanged registry produce byte-identical
    output (header `Total tools:` is computed, not hard-coded).
    """
    sys.path.insert(0, str(project_path))
    try:
        import code_tools as ct  # type: ignore
    except Exception as exc:
        print(f"[ERR] cannot import code_tools from {project_path}: {exc}", file=sys.stderr)
        return 2

    # D1 (2026-08-11): code_tools.TOOL_MAP alone is 213 tools; the remaining
    # 22 register through orchestrator_server.py's _register_tool callsites
    # (governed git, council cron, restart helpers, etc.). Import the server
    # module so TOOL_MAP reaches the full 235-tool runtime surface — the same
    # source of truth that /api/tools/names serves.
    try:
        import orchestrator_server  # noqa: F401  (side-effect: late registrations)
    except Exception as exc:
        print(f"[ERR] cannot import orchestrator_server for late registrations: {exc}",
              file=sys.stderr)
        return 2

    tiers: dict[str, list[str]] = {"T1": [], "T2": [], "T3": [], "T4": []}
    unknown_tier: list[str] = []
    for name in sorted(ct.TOOL_MAP.keys()):
        tier = (ct.TOOL_PHASES.get(name) or "").strip().upper() or "?"
        if tier in tiers:
            tiers[tier].append(name)
        else:
            unknown_tier.append(name)

    total = sum(len(v) for v in tiers.values()) + len(unknown_tier)
    out_path = project_path / ".ToolA_Skill" / "references" / "tool_surface_current.md"
    lines: list[str] = [
        "# Tool Surface Current",
        "",
        "Live-generated reference for the current public tool surface.",
        "",
        "<!-- GENERATED by scripts/sync_tool_matrix.py --generate — do NOT hand-edit.",
        "     Source of truth: code_tools.TOOL_MAP + code_tools.TOOL_PHASES. -->",
        "",
        f"Total tools: {total}",
        "",
    ]
    for tier in ("T1", "T2", "T3", "T4"):
        lines.append(f"## {tier}")
        lines.append("")
        for name in tiers[tier]:
            lines.append(f"- `{name}`")
        lines.append("")
    if unknown_tier:
        lines.append("## Untiered (missing TOOL_PHASES entry — fix before committing)")
        lines.append("")
        for name in unknown_tier:
            lines.append(f"- `{name}`")
        lines.append("")

    content = "\n".join(lines).rstrip() + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")

    print(f"[OK] wrote {out_path}", file=sys.stderr)
    print(f"     total={total}  T1={len(tiers['T1'])} T2={len(tiers['T2'])} "
          f"T3={len(tiers['T3'])} T4={len(tiers['T4'])} untiered={len(unknown_tier)}",
          file=sys.stderr)
    if unknown_tier:
        print(f"[WARN] {len(unknown_tier)} tools missing TOOL_PHASES entry — review "
              f"the 'Untiered' section before committing.", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sync_tool_matrix",
        description="V15 R15 — 4-layer sync audit (Code/Schema/Skills/Inventory).",
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--ci", action="store_true",
                     help="Full audit; exit 1 if drift; block merge gate.")
    mode.add_argument("--summary-only", action="store_true",
                     help="Lightweight JSON for continuous monitoring; exit 0.")
    mode.add_argument("--check", action="store_true",
                     help="Full coverage + drift list + suggested_fix per item.")
    mode.add_argument("--update", action="store_true",
                     help="(Scaffold) Auto-fix drift — not yet implemented.")
    mode.add_argument("--generate", action="store_true",
                     help="Regenerate tool_surface_current.md from TOOL_MAP/TOOL_PHASES (D1).")

    ap.add_argument("--project-path", default=None,
                    help="Project root (default: scripts/.. → project-orchestrator/)")
    ap.add_argument("--output", choices=["json", "md"], default="json",
                    help="Output format for --check (default: json)")

    args = ap.parse_args(argv)

    try:
        project_root = _resolve_project_path(args.project_path)
        audit_fn = _import_audit(project_root)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 2

    if args.ci:
        return cmd_ci(audit_fn, project_root)
    if args.summary_only:
        return cmd_summary_only(audit_fn, project_root)
    if args.check:
        return cmd_check(audit_fn, project_root, args.output)
    if args.update:
        return cmd_update(audit_fn, project_root)
    if args.generate:
        return cmd_generate(project_root)
    return 2


if __name__ == "__main__":
    sys.exit(main())
