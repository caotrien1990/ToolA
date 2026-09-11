"""One-shot patch: ClauC must dispatch to the claudeltnai harness, not vanilla claude."""
import io
import sys
from pathlib import Path

TARGET = Path(r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\orchestrator_server.py")
src = io.open(TARGET, encoding="utf-8").read()
orig = src

# ---------------------------------------------------------------- Edit A: helpers
ANCHOR_A = '''def _council_prepare_codo_env(env: dict[str, str]) -> None:
    """Use codexltnai's own configuration; only inject its dedicated API key."""
    env["API_KEY"] = _council_codo_api_key(env)
'''

NEW_A = ANCHOR_A + '''

def _council_claudeltnai_target() -> tuple[str, dict[str, str]]:
    """Resolve the claudeltnai harness to (real executable, env overrides).

    `claudeltnai` is a HAND-WRITTEN npm shim, not an npm-generated one: it only
    exports the LTNAI environment and then CALLs claude.cmd. Two consequences bit
    ClauC in production:
      * `_resolve_cmd_shim` cannot parse it (there is no "%dp0%\\...exe" line), so
        the argv kept going through cmd.exe and hit the 8191-char batch limit --
        every real broker prompt died with `The command line is too long.`
      * Resolving it naively to claude.exe DROPS that environment, so ClauC ran as
        vanilla `claude` against the wrong config dir (observed 2026-08-19: hooks
        loaded from ~/.claude instead of ~/.claude-ltnai). Wrong harness, wrong
        account, wrong proxy -- not a cosmetic difference.
    So spawn claude.exe directly AND rebuild the shim's environment in Python.
    Falls back to the shim path with no overrides if the real exe is missing.
    """
    npm_bin = Path.home() / "AppData" / "Roaming" / "npm"
    shim = str(npm_bin / "claudeltnai.cmd")
    exe = npm_bin / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    if not exe.is_file():
        return shim, {}
    return str(exe), {
        "CLAUDE_CONFIG_DIR": str(Path.home() / ".claude-ltnai"),
        "ANTHROPIC_BASE_URL": "https://api.ltnproxy.com",
    }


def _council_ltn_key(env: dict[str, str]) -> str:
    """LTN proxy token, read from the same source the shim uses: LTN_KEY (User)."""
    key = str(env.get("LTN_KEY") or "").strip()
    if key:
        return key
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "[Environment]::GetEnvironmentVariable('LTN_KEY','User')"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return (out.stdout or "").strip()
    except Exception:
        return ""


def _council_prepare_clau_env(env: dict[str, str]) -> None:
    """Recreate claudeltnai.cmd's environment for a direct claude.exe spawn."""
    _, overrides = _council_claudeltnai_target()
    if not overrides:
        return
    env.update(overrides)
    key = _council_ltn_key(env)
    if key:
        env["LTN_KEY"] = key
        env["ANTHROPIC_AUTH_TOKEN"] = key
    # The shim deletes this so the proxy token is the only credential in play;
    # leaving an inherited key here would silently re-target the real Anthropic API.
    env.pop("ANTHROPIC_API_KEY", None)
'''

# ------------------------------------------------------- Edit B: clau argv[0]
ANCHOR_B = '            cli("claudeltnai"), "--print", "--model", _model,'
NEW_B = ('            # Direct exe + _council_prepare_clau_env(): keeps the claudeltnai\n'
         '            # identity while keeping cmd.exe (8191-char limit) out of the argv.\n'
         '            _council_claudeltnai_target()[0], "--print", "--model", _model,')

# --------------------------------------------------------- Edit C: env wiring
ANCHOR_C = '''    if agent == "codo":
        # This helper is live dispatch setup, not dead configuration scaffolding.
        _council_prepare_codo_env(env)
'''
NEW_C = ANCHOR_C + '''    if agent == "clau":
        # ClauC runs the claudeltnai harness (~/.claude-ltnai config + LTN proxy),
        # never vanilla `claude`. argv[0] is claude.exe so cmd.exe stays out of the
        # path; that means the shim's environment has to be injected right here.
        _council_prepare_clau_env(env)
'''

# ------------------------------------------ Edit D: stop masking launcher failure
ANCHOR_D = '''    observed_provider, observed_model, attestation_error = _council_attest_model(raw, request_id, session_id)
    high_risk = str(risk_level).lower() in {"high", "critical"} or str(dev_level).lower() in {"high", "critical", "architecture"}
'''
NEW_D = '''    # A harness that never started emits no attestation at all, which used to be
    # reported as the misleading COUNCIL_MODEL_UNVERIFIED. Name the launcher failure.
    if raw and "command line is too long" in raw.lower():
        return (replace(resolution, resolution_status="unavailable",
                        warnings=resolution.warnings + ({"warning_name": "HARNESS_ARGV_TOO_LONG"},)),
                "COUNCIL_HARNESS_ARGV_TOO_LONG")
''' + ANCHOR_D

EDITS = [("A helpers", ANCHOR_A, NEW_A), ("B argv0", ANCHOR_B, NEW_B),
         ("C env", ANCHOR_C, NEW_C), ("D attest", ANCHOR_D, NEW_D)]

for name, anchor, new in EDITS:
    n = src.count(anchor)
    if n != 1:
        sys.exit(f"ABORT {name}: anchor count={n}, expected 1")
    src = src.replace(anchor, new, 1)

if src == orig:
    sys.exit("ABORT: no change produced")
io.open(TARGET, "w", encoding="utf-8", newline="").write(src)
print("PATCHED", TARGET)
print("delta_chars", len(src) - len(orig))
