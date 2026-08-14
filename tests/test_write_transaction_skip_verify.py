"""Lock the fail-loud skip_verify compatibility contract."""
import asyncio
import inspect
import json

import code_tools
import tools_v15 as write_base


def test_wrapper_owns_skip_verify_and_defaults_to_verifying():
    wrapper = inspect.signature(code_tools.write_transaction).parameters
    assert "skip_verify" in wrapper, "wrapper lost skip_verify"
    assert wrapper["skip_verify"].default is False, (
        "verification must be the default; an unverified batch has no auto-revert"
    )


def test_base_stays_unaware_and_wrapper_rejects_skip_verify():
    base = inspect.signature(write_base.write_transaction).parameters
    assert "skip_verify" not in base
    wrapper_source = inspect.getsource(code_tools.write_transaction)
    assert "SKIP_VERIFY_UNSUPPORTED" in wrapper_source

    result = json.loads(asyncio.run(code_tools.write_transaction(
        [{"file_path": "unused.txt", "start_line": 1, "end_line": 1, "new_content": "x"}],
        project_path=".",
        skip_verify=True,
    )))
    assert result["ok"] is False
    assert result["error_name"] == "SKIP_VERIFY_UNSUPPORTED"


def test_published_schema_matches_what_the_wrapper_accepts():
    schema = None
    for entry in code_tools.TOOL_SCHEMAS:
        fn = entry.get("function", entry)
        if fn.get("name") == "write_transaction":
            schema = fn
            break
    assert schema is not None, "write_transaction is missing from TOOL_SCHEMAS"
    props = (schema.get("parameters") or {}).get("properties") or {}
    assert props.get("skip_verify", {}).get("type") == "boolean"
    assert props["skip_verify"].get("default") is False
    accepted = set(inspect.signature(code_tools.write_transaction).parameters)
    undocumented = sorted(k for k in props if k not in accepted and not k.startswith("_"))
    assert not undocumented, (
        f"schema advertises parameters the wrapper cannot accept: {undocumented}"
    )
