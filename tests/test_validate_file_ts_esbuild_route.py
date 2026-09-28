"""Residual-1/2 (2026-09-28): explicit lang="ts" must ride the ONE esbuild route,
and the published contract must stop claiming "JS/TS use node --check".

Measured before the fix: commit 65208dc routed AUTO-detected .ts/.tsx to esbuild,
but an explicit lang="ts" still reached the `npx tsc --noEmit` branch -- broken
twice on Windows (bare "npx" cannot launch npx.cmd via CreateProcess, so every
call answered "npx/tsc not found in PATH"; and tsc ignores tsconfig.json and
type-checks a half-written project). The branch is now gone and "ts" folds to
"esbuild" right after alias normalization / UNSUPPORTED_LANG refusal, so "ts"
stays an ACCEPTED value. The contract text the daemon serves comes from
code_tools._ENGLISH_TOOL_DESCRIPTIONS (validate_file is NOT in
_REGISTRY_DESCRIPTION_WINS, so the override wins) plus the `lang` param text in
registry_data.py -- both pinned here against a rerun of the stale claim.

Subprocess mocking follows tests/test_validate_file_ansi_strip.py exactly.
"""
import asyncio
import inspect
import json
from unittest import mock

import code_tools as ct
from orchestrator.core import registry_data


class _Fake:
    def __init__(self, stderr="", stdout="", rc=0):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


def _validate_capturing_argv(fp, lang, rc=0):
    """Run validate_file with subprocess.run mocked; return (result, argv list)."""
    argvs = []

    def _fake_run(argv, *a, **k):
        argvs.append(list(argv))
        return _Fake(rc=rc, stderr="" if rc == 0 else "X [ERROR] parse failed")

    with mock.patch.object(ct.subprocess, "run", side_effect=_fake_run), \
         mock.patch.object(ct.shutil, "which", return_value=r"C:\npm\esbuild.cmd"):
        out = asyncio.run(ct.validate_file(fp, lang=lang))
    return json.loads(out), argvs


def _broken_ts(tmp_path):
    p = tmp_path / "bad.ts"
    p.write_text("const x: number = ;\n", encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------- #
# Residual 1: routing                                                          #
# --------------------------------------------------------------------------- #
def test_explicit_lang_ts_reaches_esbuild_not_npx_tsc(tmp_path):
    # Pins the fold `if lang == "ts": lang = "esbuild"` (validate_file, right
    # after the UNSUPPORTED_LANG refusal) and the deletion of the tsc elif:
    # the ONLY subprocess spawned must be the esbuild command line.
    fp = _broken_ts(tmp_path)
    res, argvs = _validate_capturing_argv(fp, "ts", rc=1)
    assert argvs, "no checker subprocess ran at all"
    assert len(argvs) == 1, argvs
    argv0 = str(argvs[0][0])
    assert "esbuild" in argv0.lower(), f"lang='ts' did not route to esbuild: {argvs[0]}"
    assert "npx" not in argv0.lower() and "tsc" not in " ".join(map(str, argvs[0])).lower()
    assert res["ok"] is False and res["lang"] == "esbuild"


def test_alias_typescript_rides_the_same_esbuild_route(tmp_path):
    # Pins _VALIDATE_LANG_ALIASES["typescript"] -> "ts" -> the same fold.
    fp = _broken_ts(tmp_path)
    res, argvs = _validate_capturing_argv(fp, "typescript", rc=0)
    assert "esbuild" in str(argvs[0][0]).lower(), argvs
    assert res["ok"] is True and res["lang"] == "esbuild"


def test_explicit_ts_and_auto_ts_spawn_identical_checkers(tmp_path):
    # One route, not two: auto on .ts (commit 65208dc's mapping) and explicit
    # lang="ts" must produce the SAME argv shape.
    fp = _broken_ts(tmp_path)
    _, argv_explicit = _validate_capturing_argv(fp, "ts")
    _, argv_auto = _validate_capturing_argv(fp, "auto")
    assert len(argv_explicit) == 1 and len(argv_auto) == 1
    def _norm(argv):
        a = [str(x) for x in argv]
        # mkstemp randomizes the --outfile temp name per call; compare the flag,
        # not the volatile path, so this pins the ROUTE not the tempfile.
        a = [("--outfile=" if x.startswith("--outfile=") else x) for x in a]
        return [a[0].lower(), *a[1:]]
    assert _norm(argv_explicit[0]) == _norm(argv_auto[0]), (argv_explicit, argv_auto)


def test_ts_stays_an_accepted_lang_value(tmp_path):
    # The fold happens AFTER the UNSUPPORTED_LANG refusal, so "ts" must not be
    # refused -- and it stays in the supported vocabulary the refusal names.
    fp = _broken_ts(tmp_path)
    res, _ = _validate_capturing_argv(fp, "ts")
    assert res.get("error_name") != "UNSUPPORTED_LANG", res
    refusal, _ = _validate_capturing_argv(fp, "klingon")
    assert refusal.get("error_name") == "UNSUPPORTED_LANG"
    assert "ts" in refusal.get("supported_langs", []), refusal
    assert "ts" in ct._VALIDATE_LANGS


def test_esbuild_missing_still_fails_loud_for_explicit_ts(tmp_path):
    # The fail-loud contract survives the fold: no esbuild anywhere ->
    # ok:false "esbuild not found in PATH", never a silent pass.
    fp = _broken_ts(tmp_path)

    def _raise(argv, *a, **k):
        raise FileNotFoundError(argv[0])

    with mock.patch.object(ct.subprocess, "run", side_effect=_raise), \
         mock.patch.object(ct.shutil, "which", return_value=None), \
         mock.patch.dict(ct.os.environ, {"APPDATA": ""}):
        res = json.loads(asyncio.run(ct.validate_file(fp, lang="ts")))
    assert res["ok"] is False
    assert "esbuild not found in PATH" in res["error"], res


# --------------------------------------------------------------------------- #
# Residual 2: the published contract text                                      #
# --------------------------------------------------------------------------- #
def _served_description():
    for schema in ct.TOOL_SCHEMAS:
        fn = schema.get("function", {})
        if fn.get("name") == "validate_file":
            return fn
    raise AssertionError("validate_file not in TOOL_SCHEMAS")


def test_served_description_no_longer_claims_node_check_for_ts():
    # Pins code_tools._ENGLISH_TOOL_DESCRIPTIONS["validate_file"] -- the string
    # the daemon serves, because validate_file is NOT in
    # _REGISTRY_DESCRIPTION_WINS. Stale measured text: "JS/TS use node --check".
    fn = _served_description()
    desc = fn["description"]
    assert "JS/TS use node --check" not in desc and "JS/TS: node --check" not in desc
    assert desc == ct._ENGLISH_TOOL_DESCRIPTIONS["validate_file"], (
        "the served schema description is not the override map entry -- an "
        "override layer regrew it and this pin no longer guards what CAs read")
    assert "esbuild" in desc and "node --check" in desc, desc


def test_registry_lang_param_text_names_the_real_vocabulary():
    # Pins registry_data.py's validate_file row: the lang param used to say
    # "python|js|ts|auto" (4 of 10 implemented values), and the row description
    # said "JS/TS: node --check". The vocabulary is iterated from
    # ct._VALIDATE_LANGS -- ONE source for ONE question, so a newly added lang
    # cannot silently skip the published text.
    row = None
    for schema in registry_data.build_tool_schemas():
        fn = schema.get("function", {})
        if fn.get("name") == "validate_file":
            row = fn
    assert row is not None, "validate_file missing from registry_data.TOOL_SCHEMAS"
    lang_desc = row["parameters"]["properties"]["lang"]["description"]
    for v in sorted(ct._VALIDATE_LANGS):
        assert v in lang_desc, f"lang param text dropped {v!r}: {lang_desc}"
    assert lang_desc != "python|js|ts|auto", "the stale 4-value enum is back"
    assert "JS/TS: node --check" not in row["description"]
    assert "esbuild" in row["description"] or "esbuild" in lang_desc


def test_validate_file_docstring_matches_the_routing():
    # The docstring is the third producer of the claim (read by anyone reading
    # the source). "JS/TS: node --check" must not come back in it.
    doc = inspect.getdoc(ct.validate_file) or ""
    assert "JS/TS: node --check" not in doc
    assert "esbuild" in doc