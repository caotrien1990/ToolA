"""An unrecognised `lang` must never answer ok:true on a broken file.

The reported symptom was `validate_file` returning `ok: true` for a
syntactically broken JSON. Measured: with `lang="json"` it does NOT -- it returns
a real JSONDecodeError, and `lang="js"` really does run `node --check`. The
branches work. The door the reporter actually walked through is the terminal
`else`:

    else:
        fp.read_text(encoding="utf-8")
        return {"ok": True, "lang": "none",
                "note": "No syntax checker for this file type - UTF-8 readability OK"}

Any lang string the dispatcher does not recognise lands there and is answered
ok:true. Measured accepting a broken file under `lang="JSON"`, `"jsonc"`,
`"javascript"` and `"node"` -- all four are natural things for a caller to send,
and three of them name a language the tool DOES implement under a different
spelling. Worse, the registered schema documented only `python | js | ts | auto`
while the code implements nine, so a caller had no way to learn the real
vocabulary except by reading the source.

That is a silent-accept on a validator: the one failure mode a validator must not
have. A lang it cannot check has to say so with ok:false, and a lang that is
merely a different spelling of a known one has to be normalised, not ignored.

Two RFC 8259 gaps in the json branch are pinned here as well: `json.loads`
accepts NaN/Infinity and silently last-wins duplicate keys, so a file that no
conforming parser will read was called valid.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import code_tools


BROKEN_JSON = '{"a": 1,,}\n'
BROKEN_JS = 'const x = = 1;\n'


def _case(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _validate(p, lang, root):
    return json.loads(
        code_tools.asyncio.run(  # type: ignore[attr-defined]
            code_tools.validate_file(str(p), lang=lang, project_path=str(root))))


@pytest.fixture()
def root(tmp_path):
    return tmp_path


@pytest.mark.parametrize("lang", ["JSON", "Json", "jsonc"])
def test_a_json_spelling_variant_still_catches_broken_json(root, lang):
    """`JSON` and `jsonc` are the same request as `json`; ignoring them is a lie."""
    p = _case(root, "bad.json", BROKEN_JSON)
    res = _validate(p, lang, root)
    assert res["ok"] is False, (
        "lang=%r answered ok:true for a syntactically broken JSON file" % lang)


@pytest.mark.parametrize("lang", ["javascript", "JS", "node"])
def test_a_js_spelling_variant_still_checks_js(root, lang):
    p = _case(root, "bad.js", BROKEN_JS)
    res = _validate(p, lang, root)
    assert res["ok"] is False, (
        "lang=%r answered ok:true for a syntactically broken JS file" % lang)


def test_a_genuinely_unknown_lang_fails_loud(root):
    """Not silently ok:true -- an unsupported request must be refused."""
    p = _case(root, "bad.json", BROKEN_JSON)
    res = _validate(p, "klingon", root)
    assert res["ok"] is False
    assert res.get("error_name") == "UNSUPPORTED_LANG", res
    assert "klingon" in str(res.get("error", ""))
    assert res.get("supported_langs"), "the refusal must name the real vocabulary"


def test_the_refusal_lists_every_lang_the_code_implements(root):
    """The schema documented 4 of the 9 implemented; the tool must self-describe."""
    p = _case(root, "bad.json", BROKEN_JSON)
    res = _validate(p, "klingon", root)
    supported = set(res.get("supported_langs") or [])
    assert {"python", "js", "ts", "esbuild", "html", "vue", "json", "css",
            "auto"} <= supported, supported


def test_auto_on_an_unknown_extension_is_still_lang_none_and_ok(root):
    """`auto` is a request to GUESS, so "no checker for .cfg" stays a pass.

    This is the one case where lang:"none" is honest: the caller did not claim
    the file was JSON, the dispatcher looked at the extension and found nothing
    to run. Keeping it ok:true is deliberate -- write_fix calls validate_file on
    every write and must not fail a .txt.
    """
    p = _case(root, "conf.cfg", BROKEN_JSON)
    res = _validate(p, "auto", root)
    assert res["ok"] is True
    assert res["lang"] == "none"
    assert res.get("checked") is False, (
        "a pass that ran no checker must say so, or a caller reads it as validated")


def test_a_known_lang_that_mismatches_the_file_still_fails_loud(root):
    """Regression guard: the working branches must keep working."""
    p = _case(root, "bad.json", BROKEN_JSON)
    res = _validate(p, "python", root)
    assert res["ok"] is False


def test_a_good_file_still_passes(root):
    p = _case(root, "good.json", '{"a": 1}\n')
    res = _validate(p, "json", root)
    assert res["ok"] is True
    assert res.get("checked") is True


def test_nan_and_infinity_are_not_valid_json(root):
    """json.loads accepts them by extension; RFC 8259 has no such literals.

    A file [A] called valid that json.parse / jq / serde_json all reject is a
    validator reporting the opposite of the truth.
    """
    p = _case(root, "nan.json", '{"a": NaN, "b": Infinity}\n')
    res = _validate(p, "json", root)
    assert res["ok"] is False, "NaN/Infinity accepted as valid JSON"
    assert "NaN" in str(res.get("error", "")) or "Infinity" in str(res.get("error", ""))


def test_duplicate_keys_are_reported(root):
    """Last-wins silently discards data the author wrote."""
    p = _case(root, "dup.json", '{"a": 1, "a": 2}\n')
    res = _validate(p, "json", root)
    assert res["ok"] is False, "duplicate keys silently accepted (last-wins)"
    assert "duplicate" in str(res.get("error", "")).lower()
