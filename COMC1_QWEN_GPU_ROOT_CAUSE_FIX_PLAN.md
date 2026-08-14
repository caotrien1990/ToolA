# COMC1 Qwen3-Coder GPU Root Cause Fix Plan

**Date**: 2026-06-22
**Task**: BROKER TASK ROOT CAUSE REASONING — ComC1
**Scope**: Why Qwen3-Coder-30B GPU direct benchmark PASS (103.5 t/s, valid JSON) but candidate runner FAIL (KeyboardInterrupt at ~11s, 0 findings).

---

## 1. Root Cause Ranking

### RC1 — CRITICAL: Missing `-c` context-size limit → excessive KV-cache allocation → VRAM OOM during prompt eval
**Confidence**: 0.88

**Mechanism**:
- Model loads to 15,175 MB / 16,303 MB at -ngl 40, leaving only **1,128 MB free** (92.7% full)
- `run_llama()` (line 268-276) builds cmd list with NO `-c` flag, so llama.cpp uses the model's `n_ctx_train` default
- Qwen3-Coder-30B has `n_ctx_train=131072` — at Q3_K_M with MoE, even a moderate default (4096) can allocate 1-3+ GB for KV-cache
- Combined: model weights (13.1 GB) + KV-cache (1-3 GB) = **exceed 16 GB** → CUDA OOM / illegal memory access on Blackwell
- Benchmark PASSED because `--single-turn` triggers minimal KV-cache pre-allocation (exact inference-time fallback), keeping peak under limit
- VRAM delta (+13,769 MB) + GPU util 28% at crash + 11.1s elapsed = crash occurred **during MoE prompt eval when temp buffers were first allocated**

**Evidence**:
- `vram_log.jsonl`: after_round `memory_used_mb=15175` out of 16303 total
- Free headroom: 1,128 MB (insufficient for KV-cache + MoE scratch buffers)
- Benchmark report explicitly: "Peak VRAM not captured" — hiding the same pressure
- `run_llama()` line 268-276: no `-c` arg in cmd

### RC2 — HIGH: Missing `--no-conversation --single-turn` → llama-cli enters interactive REPL → indefinite hang
**Confidence**: 0.82

**Mechanism**:
- Without these flags, llama-cli stays in REPL after processing `-f` prompt
- In REPL mode, the model receives no termination signal after generation
- If generation produces garbage (wrong template → wrong tokens), llama-cli waits for next input indefinitely
- The 600s timeout never fires at 11s — crash is faster than timeout
- This creates a **secondary failure mode**: even if inference completes, llama-cli never exits → tool appears hung

**Evidence**:
- Benchmark PASS report: "Flags: --no-conversation --single-turn (from batch file)"
- `run_llama()` line 268-276: these flags are absent
- No `--no-display-prompt` either (would suppress escape codes that might interfere with JSON parsing)

### RC3 — HIGH: Prompt template mismatch (Gemma `<start_of_turn>` on Qwen ChatML model)
**Confidence**: 0.80

**Mechanism**:
- `build_prompt()` (line 113-131) hardcodes Gemma4-style `<start_of_turn>user` / `<start_of_turn>model` tokens
- Qwen3-Coder-Instruct uses **ChatML** format (`<|im_start|>`, `<|im_end|>`)
- The Qwen tokenizer has NO special tokens for `<start_of_turn>` (14 chars) or `<end_of_turn>` (12 chars)
- Each marker splats into ~15 byte-level tokens → model sees ~45 garbage tokens instead of 3 meaningful control tokens
- The model receives no valid turn-boundary signal → unpredictable generation
- **This is a direct differential**: benchmark PASS used ChatML prompt, candidate FAIL used Gemma prompt

**Evidence**:
- Benchmark prompt artifact (`prompt.txt`) confirmed as ChatML by HermC1 investigation
- Candidate `build_prompt()` hardcodes Gemma format (lines 113-131)
- `PROMPT_CONTROL_TOKEN_REPLACEMENTS` (lines 40-43) proves awareness of token format sensitivity

### RC4 — MEDIUM: KeyboardInterrupt masking in `run_llama` poll loop hides real crash signal
**Confidence**: 0.95 (as a diagnostic issue; 0.0 as crash root cause)

**Mechanism**:
- `run_llama()` polling loop (lines 278-301) has NO `try/except KeyboardInterrupt`
- When llama-cli crashes with access violation, Windows delivers `CTRL_C_EVENT` to the console process group
- Python's `time.sleep(1.0)` (line 297) catches this as `SIGINT` → `KeyboardInterrupt`
- Exception propagates uncaught from `run_llama` to `run_candidate` line 400: `except KeyboardInterrupt as exc:`
- Status set to `"interrupted"` (not `"error"`), so the `RuntimeError` path (line 299-300: `proc.returncode != 0`) is NEVER reached
- Exit code, stderr, and crash details are all lost
- Line 436: `if row.get("status") == "interrupted": break` — stops all remaining rounds

**Evidence**:
- `rounds.jsonl`: status=`"interrupted"`, `error_name="KeyboardInterrupt"`, not `"error"`
- No `except` in `run_llama` poll loop
- VRAM after model load confirms model was loaded — crash happened during inference

---

## 2. Fix Order (Code Changes)

### Fix #1: Add `--no-conversation --single-turn --no-display-prompt` to `run_llama()` cmd
**File**: `clm_gemma_cluster_candidate.py`, line 268-276
**Why first**: Zero risk, immediate impact. Without this, llama-cli enters REPL and never exits regardless of other fixes.
**Change**: Append to cmd list after `-ngl`:
```python
"--no-conversation",
"--single-turn",
"--no-display-prompt",
```

### Fix #2: Add `-c 2048` context-size flag to `run_llama()` cmd
**File**: `clm_gemma_cluster_candidate.py`, line 268-276
**Why second**: Directly prevents the OOM crash. KV-cache at ctx 2048 is safe (~0.5-1 GB).
**Change**: Append after `--repeat-penalty`:
```python
"-c", "2048",
```
Also add `--no-warmup` to skip pre-allocation:
```python
"--no-warmup",
```

### Fix #3: Wrap `run_llama()` poll loop in `try/except KeyboardInterrupt` that checks child exit
**File**: `clm_gemma_cluster_candidate.py`, lines 278-301
**Why third**: Fixes the diagnostic masking. Without this, ALL crashes are misreported as "interrupted".
**Change**:
```python
try:
    while proc.poll() is None:
        ...
except KeyboardInterrupt:
    # Kill the child first so we can inspect its exit
    proc.kill()
    stdout, stderr = proc.communicate()
    if proc.returncode is not None and proc.returncode != 0:
        raise RuntimeError(
            f"llama-cli crashed (exit {proc.returncode}) after {int(time.perf_counter() - started)}s: "
            f"{stderr[-1200:]}"
        )
    raise  # If child exited 0, this was a real user interrupt
```

### Fix #4: Add model-format-aware prompt builder (Qwen ChatML branch)
**File**: `clm_gemma_cluster_candidate.py`, add `build_prompt_qwen()` function
**Why fourth**: This is REQUIRED for Qwen output quality but was not the direct crash cause. Fix after crash is resolved.
**Change**:
```python
def build_prompt_qwen(cluster: dict[str, Any], round_index: int) -> str:
    payload = json.dumps(safe_cluster_input(cluster), ensure_ascii=False, indent=2)
    return (
        "<|im_start|>system\n"
        "You are a ToolQ offline candidate worker for memory QA.\n"
        "Task: audit this cluster and return ONLY a JSON array of candidate findings.\n"
        "..."
        "<|im_end|>\n"
        "<|im_start|>user\n"
        f"Round: {round_index}\n"
        f"Cluster input:\n{payload}\n"
        "<|im_end|>\n"
        "<|im_start|>assistant\n"
    )
```
Add `--model-family gemma|qwen` CLI arg; use `build_prompt()` for gemma, `build_prompt_qwen()` for qwen.

### Fix #5 (Optional): Add ngl=35 default and `--vram-headroom-mb` safety check
**Why fifth**: Adds robustness. At ngl=35, ~1.5-2 GB would be free instead of 1.1 GB, giving MoE temp buffers room.
**Change**: Reduce `--ngl` default from 20 to 30 for Qwen models; add pre-flight VRAM check warning if free > estimated model load < 1.5 GB.

---

## 3. Probes After Patch

1. **Quick probe** (no inference): Run `llama-cli.exe -m <Qwen3-Coder> -f <small-prompt> -n 256 -c 2048 --temp 0.1 --no-conversation --single-turn --no-warmup -ngl 40` and check VRAM peak stays under 15.5 GB. Confirm valid JSON output with ChatML prompt.

2. **Full candidate run**: Run `clm_gemma_cluster_candidate.py` with Qwen3-Coder, `--rounds 1`, same tiny cluster. Verify `round_statuses=["ok"]`, findings > 0, no KeyboardInterrupt.

3. **VRAM loop monitor**: Run `nvidia-smi --loop=1 --query-gpu=memory.used,utilization.gpu --format=csv,noheader` during probe #2 and capture peak VRAM.

4. **Stderr capture**: Redirect llama-cli stderr to separate file to confirm no CUDA warnings/errors even on success path.

5. **Boundary test**: Repeat at `-c 4096` and `-c 8192` to find the VRAM safety limit for Qwen3-Coder-30B on RTX 5070 Ti.

---

## 4. Prompt Template Fix: Required or Optional?

**REQUIRED for Qwen models.** The Gemma `<start_of_turn>` format is a direct differential between PASS (ChatML) and FAIL (Gemma). While the crash at 11s is primarily a VRAM OOM from missing `-c`, the prompt template mismatch ensures that even if inference survives, the output will be unpredictable garbage:

- `<start_of_turn>` has no special-token mapping in Qwen's tokenizer → ~15 byte tokens per marker
- Model receives no valid turn-boundary signal → no chat structure awareness
- Benchmark used ChatML and produced valid JSON with correct negation reasoning
- A model-format selector (`--model-family`) should route to the correct `build_prompt_*` function

**Optional** only if the user is testing crash-causality and wants to isolate variables — but for any production use case, it is mandatory.

---

## 5. `-c ctx-size` / `--ngl` Reduction: Default or CLI Option?

**`-c 2048` should be DEFAULT.** The candidate generation task processes small cluster inputs (~50-200 tokens). There is no legitimate reason to allocate context larger than 2048 for this workload. Adding `--ctx-size` as a CLI override (default 2048, max 8192 with warning) is the correct pattern.

**`--ngl` reduction should remain a CLI option with tuned defaults.** Context:
- Current default: `--ngl 20` (line 176) — low, safe for Gemma4 16B
- For Qwen3-Coder-30B at Q3_K_M: -ngl 30 leaves ~2 GB headroom, -ngl 40 leaves ~1.1 GB
- The RTX 5070 Ti 16 GB is the constraint, not the model architecture
- Default to -ngl 30 for Qwen, keep -ngl 20 default for Gemma
- Users with 24 GB+ GPUs can override to -ngl 99

**Recommendation**:
```python
# Add to parse_args:
parser.add_argument("--ctx-size", type=int, default=2048, help="llama.cpp context size (default: 2048)")
# --ngl already exists, just change the qwen-specific default
```

---

## Crash Chain Summary

```
build_prompt() uses Gemma format on Qwen model [RC3: contributory]
  → run_llama() spawns without -c, without --single-turn [RC1, RC2: primary]
    → model loads to 15.2 GB / 16.3 GB (92.7% VRAM)
    → prompt eval starts → MoE temp buffers + KV-cache allocation
      → VRAM exhausted → CUDA OOM / illegal memory access [RC1: root crash]
        → llama-cli.exe crashes → Windows CTRL_C_EVENT
          → Python KeyboardInterrupt in time.sleep(1.0)
            → caught at line 400 → status="interrupted" [RC4: masking]
              → line 436 break → all rounds stopped
```

**Fix order stops this chain at three points:**
- Fix #1 (`--single-turn`) + Fix #2 (`-c 2048`) = prevents crash entirely
- Fix #3 (`KeyboardInterrupt` guard) = ensures any future crash is reported as "error" with stderr, not "interrupted"
- Fix #4 (Qwen prompt) = ensures output is valid JSON with correct semantics

---

END_COMC1_QWEN_GPU_ROOT_CAUSE_FIX_PLAN
