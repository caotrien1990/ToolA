"""P16 Sprint 3 (D3): write_fix_html /validation nested demand -- regression tests.

write_fix_html routes non-.html files to write_fix (code_tools.py:1679-1683);
write_fix embeds `validation` on success (orchestrator/tools/write/core.py:2003,
ok:true-gated). The routed per-language shapes lack the HTML-only tag trio
(unclosed_tags, tag_errors, truncated); enrichment (code_tools.py:15159-15193)
adds `checked` + the ruff trio to EVERY result and NEVER adds the tag trio.
The nested /validation `required` therefore demands exactly the always-carried
intersection -- the 7 retained keys, in drop-in-place order -- and the trio
stays in `properties` (types checked when present). The enumeration proving
the intersection (all 12 _VALIDATE_EXTS families, static trace + 14 live
write_fix probes) is filed at records/d3-enumeration.md in the campaign home.

These tests pin the leaf verdicts for the routed shapes against the live
assembled block -- no fixtures, per the F5-FIXTURE decision (PLAN.md
"Regression tests"): the a45 battery pins the committed fixture count at 81,
so regression shapes live here as explicit (verdict, reason, mismatches)
assertions instead of fixture files.

Expected values are verbatim from the Sprint 3 post-fix probe run
(records/sprint3-d3-log.md; the 15 filed fixtures, all matching
expect_post_fix). The warn reason is None: check_returns returns
("warn", None, mismatches) -- see returns_shape.py check_returns.

`lang` values below are the SERVED strings from the enumeration
(records/d3-enumeration.md section 3): "esbuild" for .ts/.tsx/.jsx,
"js" for .js/.mjs/.cjs. The leaf checks `lang`'s type only (no
value/enum check), so the values are inert to the verdicts; they are
written as served so the shapes are the real routed shapes.
"""

from __future__ import annotations

import json
from pathlib import Path

import orchestrator_server as srv  # noqa: F401  (assembles TOOL_SCHEMAS: 238 -> 260)
from orchestrator.core import returns_shape as rs

_TOOL = "write_fix_html"
_REPO_ROOT = Path(srv.__file__).resolve().parent
_BASELINE_FILE = _REPO_ROOT / "orchestrator" / "core" / "returns_baseline.json"
_RETAINED = [
    "ok", "lang", "file", "ruff_fix_applicable",
    "checked", "ruff_rules_matched", "ruff_fix_hint",
]
_VAL_PROPS = [
    "checked", "file", "lang", "note", "ok", "ruff_fix_applicable",
    "ruff_fix_hint", "ruff_rules_matched", "tag_errors", "truncated",
    "unclosed_tags",
]


def _canonical_raw_len(answer) -> int:
    """Byte length of the answer's canonical UTF-8 dump (fixture raw_len)."""
    return len(json.dumps(
        answer, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8"))


def _r7(lang, file):
    """A routed validation shape carrying exactly the 7 retained keys."""
    return {
        "ok": True,
        "lang": lang,
        "file": file,
        "ruff_fix_applicable": False,
        "checked": True,
        "ruff_rules_matched": [],
        "ruff_fix_hint": "",
    }


def _answer(validation):
    """A write_fix_html answer carrying the block's required key + validation."""
    return {"ok": True, "validation": validation}


def _check(answer):
    return rs.check_returns(_TOOL, answer, _canonical_raw_len(answer))


def test_block_resolves_audited():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    assert rs.schema_state(block) == "audited", (
        f"{_TOOL}: schema_state is {rs.schema_state(block)!r}, "
        "the D3 fix requires 'audited'"
    )


def test_validation_node_required_is_retained_seven():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    assert block["required"] == ["ok"]
    val = block["properties"]["validation"]
    assert val["type"] == "object"
    assert val["required"] == _RETAINED
    assert sorted(val["properties"].keys()) == _VAL_PROPS
    # Trio keys STAY in properties (types checked when present).
    assert val["properties"]["unclosed_tags"] == {"type": "array"}
    assert val["properties"]["tag_errors"] == {"type": "array"}
    assert val["properties"]["truncated"] == {"type": "boolean"}


def test_top_level_trio_declarations_unchanged():
    # The .html top-level copies (core.py:1953-1956) keep their declarations;
    # the D3 edit touches ONLY the nested /validation required list.
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    props = block["properties"]
    assert props["unclosed_tags"] == {"type": "array"}
    assert props["tag_errors"] == {"type": "array"}
    assert props["truncated"] == {"type": "boolean"}


def test_sha_triple_and_depth_d():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    baseline = json.loads(_BASELINE_FILE.read_text(encoding="utf-8"))
    pin = baseline[_TOOL]
    assert block["x-audit"]["schema_sha256"] == rs.canonical_schema_sha(block)
    assert pin["sha256"] == rs.canonical_schema_sha(block)
    assert block["x-audit"]["depth_D"] == 4
    assert pin["depth_D"] == 4


def test_routed_html_shape_passes():
    assert _check(_answer(_r7("html", "probe.html"))) == ("pass", None, [])


def test_routed_js_shape_passes():
    assert _check(_answer(_r7("js", "probe.js"))) == ("pass", None, [])


def test_routed_ts_shape_passes():
    assert _check(_answer(_r7("esbuild", "probe.ts"))) == ("pass", None, [])


def test_routed_mjs_shape_passes():
    assert _check(_answer(_r7("js", "probe.mjs"))) == ("pass", None, [])


def test_routed_cjs_shape_passes():
    assert _check(_answer(_r7("js", "probe.cjs"))) == ("pass", None, [])


def test_routed_py_shape_passes():
    assert _check(_answer(_r7("python", "probe.py"))) == ("pass", None, [])


def test_routed_jsx_shape_passes():
    assert _check(_answer(_r7("esbuild", "probe.jsx"))) == ("pass", None, [])


def test_routed_tsx_shape_passes():
    assert _check(_answer(_r7("esbuild", "probe.tsx"))) == ("pass", None, [])


def test_routed_json_shape_passes():
    assert _check(_answer(_r7("json", "probe.json"))) == ("pass", None, [])


def test_routed_css_shape_passes():
    assert _check(_answer(_r7("css", "probe.css"))) == ("pass", None, [])


def test_routed_vue_shape_passes():
    assert _check(_answer(_r7("vue", "probe.vue"))) == ("pass", None, [])


def test_routed_toml_shape_passes():
    assert _check(_answer(_r7("toml", "probe.toml"))) == ("pass", None, [])


def test_vue_no_script_shape_passes():
    # Served shape (code_tools.py:15050): R7 + note, WITH file.
    validation = _r7("vue", "probe.vue")
    validation["note"] = "No <script> block found"
    assert _check(_answer(validation)) == ("pass", None, [])


def test_html_full_shape_passes():
    # Served .html shape (code_tools.py:15044): all 10 keys + note.
    validation = _r7("html", "probe.html")
    validation["note"] = (
        "HTML OK: 1 script block(s) checked, full document balanced"
    )
    validation["unclosed_tags"] = []
    validation["tag_errors"] = []
    validation["truncated"] = False
    assert _check(_answer(validation)) == ("pass", None, [])


def test_trio_present_with_wrong_type_warns():
    # Properties type check retained: a string unclosed_tags still warns.
    validation = _r7("html", "probe.html")
    validation["unclosed_tags"] = "x"
    validation["tag_errors"] = []
    validation["truncated"] = False
    assert _check(_answer(validation)) == (
        "warn", None, ["type:validation.unclosed_tags=string!=array"],
    )


def test_missing_checked_warns():
    validation = _r7("python", "probe.py")
    del validation["checked"]
    assert _check(_answer(validation)) == (
        "warn", None, ["missing:validation.checked"],
    )


def test_missing_each_retained_key_warns():
    # The retained set is still enforced key by key: dropping any one
    # retained key warns with exactly that key's missing path. (A future
    # trio re-addition to `required` fails the routed-shape tests above.)
    for key in _RETAINED:
        validation = _r7("python", "probe.py")
        del validation[key]
        assert _check(_answer(validation)) == (
            "warn", None, [f"missing:validation.{key}"],
        ), f"dropping {key!r} must warn"
