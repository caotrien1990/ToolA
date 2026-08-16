"""campaign-1608 (ANSI): validate_file must strip ANSI colour escapes out of the
`error` string for the js / esbuild / vue / html branches.

Bug (measured live with FORCE_COLOR=1, not inferred): node --check and esbuild
both emit ANSI SGR escapes in their stderr when a colour-forcing env var is set
(the agent harness / CI exports FORCE_COLOR). Those branches returned the raw
subprocess stderr straight into `error`, so colour bytes (\x1b[90m ... \x1b[39m)
leaked into the payload -- pure bloat + display garbage. The Python branch already
strips ANSI in two layers; these branches did not. Severity is low (validator
still reports pass/fail correctly), but the fix is cheap and the strip is a real
defence: esbuild runs through a node wrapper whose stack trace is coloured even
when esbuild's own --color=false is passed, so a disable-colour flag alone is not
enough here.

These tests mock subprocess.run so they are deterministic and do not depend on
node/esbuild being installed or on the ambient colour env (conftest.py scrubs
FORCE_COLOR for the whole session, so a real child would not colour in-session).
The ANSI is injected by the test itself, exactly like test_ruff_ansi_parse.py.
"""
import asyncio
import json
from unittest import mock

import code_tools as ct


# A realistic coloured node --check stderr: real message text + a coloured stack
# trace (\x1b[90m dim ... \x1b[39m reset), which is what leaks in practice.
_NODE_ANSI_STDERR = (
    "/tmp/bad.js:1\n"
    "const x = ;\n"
    "          ^\n\n"
    "SyntaxError: Unexpected token ';'\n"
    "\x1b[90m    at wrapSafe (node:internal/modules/cjs/loader:1762:18)\x1b[39m\n"
    "\x1b[90m    at checkSyntax (node:internal/main/check_syntax:76:3)\x1b[39m\n"
)

# A realistic coloured esbuild stderr: esbuild's own box-drawing caret output is
# UTF-8 content (must survive), while the wrapping node stack trace is coloured.
_ESBUILD_ANSI_STDERR = (
    "X [ERROR] Unexpected \";\"\n\n"
    "    bad.js:1:10:\n"
    "      1 │ const x = ;\n"       # box-drawing vertical -- CONTENT
    "        ╵          ^\n\n"      # box-drawing up -- CONTENT
    "1 error\n"
    "\x1b[90m    at genericNodeError (node:internal/errors:998:15)\x1b[39m\n"
    "\x1b[4mesbuild\x1b[24m\\bin\\esbuild:222:28\n"
)

# The exact substrings that carry the real diagnostic and MUST NOT be eaten.
_NODE_MUST_KEEP = ("SyntaxError", "Unexpected token", "bad.js:1")
_ESBUILD_MUST_KEEP = ("[ERROR]", "Unexpected", "bad.js:1:10", "│", "╵", "^")


def _make_broken(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


class _Fake:
    def __init__(self, stderr="", stdout="", rc=1):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


def _run_validate(fp, lang, fake_stderr, fake_stdout=""):
    def _fake_run(argv, *a, **k):
        return _Fake(stderr=fake_stderr, stdout=fake_stdout, rc=1)
    # esbuild path also touches shutil.which / tempfile; leave those real.
    with mock.patch.object(ct.subprocess, "run", side_effect=_fake_run):
        out = asyncio.run(ct.validate_file(fp, lang=lang))
    return json.loads(out)


# --------------------------------------------------------------------------- #
# js branch                                                                    #
# --------------------------------------------------------------------------- #
def test_js_strips_ansi_but_keeps_message(tmp_path):
    fp = _make_broken(tmp_path, "bad.js", "const x = ;\n")
    d = _run_validate(fp, "js", _NODE_ANSI_STDERR)
    assert d["ok"] is False
    err = d["error"]
    assert "\x1b" not in err, f"ANSI leaked: {err!r}"
    for keep in _NODE_MUST_KEEP:
        assert keep in err, f"strip ate content {keep!r}: {err!r}"


# --------------------------------------------------------------------------- #
# esbuild branch                                                               #
# --------------------------------------------------------------------------- #
def test_esbuild_strips_ansi_but_keeps_message(tmp_path):
    fp = _make_broken(tmp_path, "bad.ts", "const x = ;\n")
    d = _run_validate(fp, "esbuild", _ESBUILD_ANSI_STDERR)
    assert d["ok"] is False
    err = d["error"]
    assert "\x1b" not in err, f"ANSI leaked: {err!r}"
    for keep in _ESBUILD_MUST_KEEP:
        assert keep in err, f"strip ate content {keep!r}: {err!r}"


# --------------------------------------------------------------------------- #
# vue branch                                                                   #
# --------------------------------------------------------------------------- #
def test_vue_strips_ansi_but_keeps_message(tmp_path):
    fp = _make_broken(
        tmp_path, "bad.vue",
        "<template><div></div></template>\n<script>\nconst x = ;\n</script>\n",
    )
    d = _run_validate(fp, "vue", _ESBUILD_ANSI_STDERR)
    assert d["ok"] is False
    err = d["error"]
    assert "\x1b" not in err, f"ANSI leaked: {err!r}"
    for keep in _ESBUILD_MUST_KEEP:
        assert keep in err, f"strip ate content {keep!r}: {err!r}"


# --------------------------------------------------------------------------- #
# html branch (node --check on extracted <script>)                            #
# --------------------------------------------------------------------------- #
def test_html_strips_ansi_but_keeps_message(tmp_path):
    fp = _make_broken(
        tmp_path, "bad.html",
        "<html><body><script>\nconst x = ;\n</script></body></html>\n",
    )
    d = _run_validate(fp, "html", _NODE_ANSI_STDERR)
    assert d["ok"] is False
    err = d["error"]
    assert "\x1b" not in err, f"ANSI leaked: {err!r}"
    # html wraps it as "script[0] syntax error: ..." -- the message must survive.
    assert "script[0] syntax error" in err, f"html wrapper lost: {err!r}"
    for keep in _NODE_MUST_KEEP:
        assert keep in err, f"strip ate content {keep!r}: {err!r}"


# --------------------------------------------------------------------------- #
# The shared helper directly                                                   #
# --------------------------------------------------------------------------- #
def test_strip_ansi_helper_removes_only_escapes():
    raw = "\x1b[90mdim\x1b[39m keep \x1b[1m\x1b[31mF401\x1b[0m tail"
    got = ct._strip_ansi(raw)
    assert got == "dim keep F401 tail", repr(got)
    assert "\x1b" not in got


def test_strip_ansi_helper_is_noop_on_plain_text():
    plain = "SyntaxError: Unexpected token ';' at bad.js:1:10"
    assert ct._strip_ansi(plain) == plain


def test_strip_ansi_helper_preserves_box_drawing():
    # esbuild's carets are UTF-8 box-drawing, NOT ANSI -- must survive untouched.
    s = "  1 │ const x = ;\n    ╵          ^"
    assert ct._strip_ansi(s) == s


def test_strip_ansi_handles_full_csi_not_only_sgr():
    """ClauLTN verify (V2): the helper must match the FULL CSI grammar, not just
    the colour (SGR, `...m`) subset that today's node/esbuild output happens to
    emit. A narrowed `\\x1b\\[[0-9;]*[A-Za-z]` passes every colour test above yet
    leaks private-mode sequences straight into `error` -- and those are exactly
    what a TTY-ish child emits. Widening is free: these forms carry no text."""
    for raw, want in (
        ("\x1b[?25lhidden", "hidden"),      # hide cursor  (narrow form LEAKS)
        ("\x1b[?25hshown", "shown"),        # show cursor  (narrow form LEAKS)
        ("\x1b[?1049halt", "alt"),          # alt screen   (narrow form LEAKS)
        ("\x1b[2Kerased", "erased"),        # erase line
        ("\x1b[90mdim\x1b[39m", "dim"),     # plain SGR    (both forms strip)
    ):
        got = ct._strip_ansi(raw)
        assert got == want, f"{raw!r} -> {got!r}, expected {want!r}"
        assert "\x1b" not in got


def test_strip_ansi_agrees_with_council_stripper():
    """Two ANSI strippers in one repo must not disagree on what an escape IS --
    that divergence is the two-sources-of-truth shape this campaign keeps hitting.
    Pins code_tools._strip_ansi against the older orchestrator_server one."""
    import orchestrator_server as srv

    for s in (
        "\x1b[90mdim\x1b[39m keep",
        "\x1b[?25lTEXT",
        "\x1b[2KTEXT",
        "  1 │ const x = ;",           # box-drawing content, must survive
        "SyntaxError: Unexpected token ';' at bad.js:1:10",
    ):
        assert ct._strip_ansi(s) == srv._strip_ansi(s), f"strippers disagree on {s!r}"


def test_all_branches_share_one_helper():
    """L-d guard: js/esbuild/vue/html and _py_error_ruff_hint must all route
    through _strip_ansi, so a single helper mutation kills tests in every branch
    (copy-pasted regex is what let a mutant survive earlier in this campaign)."""
    src = ct.__loader__.get_source("code_tools") if hasattr(ct, "__loader__") else None
    if src is None:
        import inspect
        src = inspect.getsource(ct)
    # _py_error_ruff_hint + the 4 subprocess branches = at least 5 call sites.
    assert src.count("_strip_ansi(") >= 5, (
        f"expected >=5 _strip_ansi call sites, found {src.count('_strip_ansi(')}")
