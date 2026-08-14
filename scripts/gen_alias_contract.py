"""Generate the alias contract from the runtime _ARG_ALIASES source of truth."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_DOCS_DIR = ROOT / "docs"
MD_NAME = "ALIAS_CONTRACT.generated.md"
JSON_NAME = "ALIAS_CONTRACT.generated.json"


def _load_runtime_aliases() -> dict[str, dict[str, str]]:
    """Import the orchestrator runtime so late register_tool aliases are included."""
    srv = importlib.import_module("orchestrator_server")
    aliases = getattr(srv, "_ARG_ALIASES")
    return _sorted_aliases(aliases)


def _sorted_aliases(aliases: Mapping[str, Mapping[str, str]]) -> dict[str, dict[str, str]]:
    return {
        tool: {alias: aliases[tool][alias] for alias in sorted(aliases[tool])}
        for tool in sorted(aliases)
    }


def _alias_count(aliases: Mapping[str, Mapping[str, str]]) -> int:
    return sum(len(tool_aliases) for tool_aliases in aliases.values())


def _markdown(aliases: Mapping[str, Mapping[str, str]]) -> str:
    lines = [
        "# Alias Contract (Generated)",
        "",
        "Generated from runtime `orchestrator_server._ARG_ALIASES`.",
        "",
        f"- Tools with aliases: {len(aliases)}",
        f"- Total aliases: {_alias_count(aliases)}",
        "- Source of truth: `orchestrator_server.py:_ARG_ALIASES` plus runtime `register_tool(aliases=...)` updates",
        "",
        "| Tool | Alias | Canonical |",
        "|---|---|---|",
    ]
    for tool, tool_aliases in aliases.items():
        for alias, canonical in tool_aliases.items():
            lines.append(f"| `{tool}` | `{alias}` | `{canonical}` |")
    lines.append("")
    return "\n".join(lines)


def write_contract(output_dir: Path) -> tuple[Path, Path, dict[str, dict[str, str]]]:
    aliases = _load_runtime_aliases()
    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / MD_NAME
    json_path = output_dir / JSON_NAME
    md_path.write_text(_markdown(aliases), encoding="utf-8", newline="\n")
    json_path.write_text(
        json.dumps(aliases, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return md_path, json_path, aliases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DOCS_DIR,
        help="Directory for generated Markdown and JSON files.",
    )
    args = parser.parse_args(argv)

    md_path, json_path, aliases = write_contract(args.output_dir)
    print(f"wrote {md_path}")
    print(f"wrote {json_path}")
    print(f"tools={len(aliases)} aliases={_alias_count(aliases)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
