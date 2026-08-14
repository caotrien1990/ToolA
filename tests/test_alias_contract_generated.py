import json

import orchestrator_server as srv

from scripts.gen_alias_contract import JSON_NAME, write_contract


def _sorted_aliases() -> dict[str, dict[str, str]]:
    return {
        tool: {alias: srv._ARG_ALIASES[tool][alias] for alias in sorted(srv._ARG_ALIASES[tool])}
        for tool in sorted(srv._ARG_ALIASES)
    }


def test_generated_alias_contract_json_matches_runtime(tmp_path):
    write_contract(tmp_path)

    generated = json.loads((tmp_path / JSON_NAME).read_text(encoding="utf-8"))

    assert generated == _sorted_aliases(), (
        "Generated alias contract JSON does not match runtime _ARG_ALIASES; "
        "run scripts/gen_alias_contract.py"
    )
