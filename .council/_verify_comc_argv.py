"""Targeted ARGV verification for the ComC broker fix (ClauC).
Run from the project-orchestrator root:
    python .council/_verify_comc_argv.py
Asserts: commandcode print mode places the prompt immediately after -p,
then -m <model>, and the prompt is NOT a trailing positional.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import orchestrator_server as srv

PROMPT = "BROKER TASK: verify argv order \\breakdown safe"
argv = srv._council_agent_command("comc", PROMPT, mode="broker")
print("ARGV:", argv)

# 1) -p must be immediately followed by the prompt value.
i = argv.index("-p")
if argv[i + 1] != PROMPT:
    raise RuntimeError(f"prompt not right after -p: {argv[i+1]!r}")
# 2) -m must come AFTER the prompt (not directly after -p).
if not ("-m" in argv and argv.index("-m") > i + 1):
    raise RuntimeError("-m not after prompt")
# 3) prompt must NOT be the trailing positional anymore.
if argv[-1] == PROMPT:
    raise RuntimeError("prompt is still the trailing positional")
# 4) required flags preserved.
for flag in ("--trust", "--auto-accept", "--yolo", "--skip-onboarding", "--max-turns", "--add-dir"):
    if flag not in argv:
        raise RuntimeError(f"missing flag {flag}")

# 5) context mode with resume still appends --resume after the core args,
#    and prompt still sits right after -p.
argv_ctx = srv._council_agent_command("comc", PROMPT, mode="context", resume_id="sess-123")
print("ARGV(context):", argv_ctx)
j = argv_ctx.index("-p")
if argv_ctx[j + 1] != PROMPT:
    raise RuntimeError("context: prompt not right after -p")
if not ("--resume" in argv_ctx and argv_ctx[argv_ctx.index("--resume") + 1] == "sess-123"):
    raise RuntimeError("context: --resume sess-123 missing")

print("VERIFY_COMC_ARGV_OK")
