import json

import orchestrator_server as srv

from scripts.gen_alias_contract import DEFAULT_DOCS_DIR, JSON_NAME, MD_NAME, write_contract


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


# ------------------------------------------------------------ A32-1.3 / A31-Q1 B2
# The test above writes into tmp_path and compares tmp against runtime: it verifies the
# GENERATOR. Nothing in this suite ever opened the file in docs/, which is exactly why
# that file drifted three separate times while the suite stayed green -- committed
# 67 tools / 199 aliases against a runtime of 72/221, the gap carrying `list_structure`
# x4, `ruff_fix` x2 and `search_code` x2 from campaigns that never re-ran the generator.
# The two tests below anchor the COMMITTED bytes. Measured 2026-09-17 before they
# existed: docs said 72/221 while runtime said 73/224, and both of them failed on that
# stale file -- a gate that has only ever seen green is not yet a gate.


def test_committed_alias_contract_json_anchors_the_runtime_registry():
    committed = json.loads((DEFAULT_DOCS_DIR / JSON_NAME).read_text(encoding="utf-8"))

    assert committed == _sorted_aliases(), (
        "docs/%s is stale against runtime _ARG_ALIASES; run "
        "`python scripts/gen_alias_contract.py`" % JSON_NAME
    )


def test_committed_alias_contract_md_matches_what_the_generator_produces(tmp_path):
    # Line-by-line, NOT read_bytes(). Measured on this machine: `core.autocrlf` is
    # `true` and the file carries no `text` attribute, so a fresh checkout hands the
    # working tree CRLF while the generator writes LF explicitly (newline="\n").
    # The copy on disk is LF today only because the generator last wrote it. A byte
    # compare would therefore fail on a clean clone for a reason that has nothing to do
    # with alias drift -- the gate would be reporting the checkout, not the contract.
    write_contract(tmp_path)

    committed = (DEFAULT_DOCS_DIR / MD_NAME).read_text(encoding="utf-8").splitlines()
    regenerated = (tmp_path / MD_NAME).read_text(encoding="utf-8").splitlines()

    assert committed == regenerated, (
        "docs/%s differs from generator output; run "
        "`python scripts/gen_alias_contract.py`. This also anchors the two COUNTS in "
        "the header prose (`Tools with aliases:` / `Total aliases:`), which a "
        "JSON-only check would let rot while the table below them stayed correct"
        % MD_NAME
    )
