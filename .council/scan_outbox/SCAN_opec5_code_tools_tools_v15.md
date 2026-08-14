# SCAN REPORT - OpeC5 (openai-compatible/glm-5.2)

- **Scanner:** OpeC5 (council worker, HTTP-only [A] tools)
- **Scope:** `code_tools.py` (11660 lines) + `tools_v15.py` (7790 lines)
- **Tools used:** search_code(regex), read_file_range, get_file_symbols, import_graph(schema)
- **Date:** 2026-07-10
- **Rule sha256:** 75eb0d6c2369ca6460352c1acebdc257a5bf50c6cb43b86c61ed12c02555a101
- **Mode:** READ-ONLY scan. No source file edited.

## Summary

| severity | count |
|----------|-------|
| CRITICAL | 3 |
| HIGH | 1 |
| MEDIUM | 9 |
| LOW | 3 |
| **TOTAL distinct findings** | **16** |

> Note: Fewer than 50 because the codebase is well-disciplined on most static-analysis
> dimensions (see "Clean dimensions" below). Findings are NOT padded with speculation;
> "unknown / not verified" is used where confirmation was not possible without deeper reads.

## Findings (sorted CRITICAL > HIGH > MEDIUM > LOW)

| file:line | severity | type | desc | fix_hint |
|-----------|----------|------|------|----------|
| tools_v15.py:6088 | CRITICAL | bare-except + swallow | `except: pass` on socket `s.close()` in finally. Bare `except` catches `SystemExit`/`KeyboardInterrupt`; silent swallow hides close errors. | Use `except OSError:` (or `except Exception:`) + optional `logging.debug`; never bare `except:`. |
| tools_v15.py:6533 | CRITICAL | bare-except + swallow | `except: pass` on `win32process.AttachThreadInput(...,False)` detach in finally. Bare except + silent. | `except Exception:` minimum; log detach failure at debug level. |
| tools_v15.py:7212 | CRITICAL | bare-except + swallow | `except: pass` on `win32process.AttachThreadInput(...,False)` detach in finally (dup of 6533 pattern, different site). | Same as 6533; consider a shared `_safe_detach()` helper. |
| code_tools.py:756 | HIGH | deadcode (duplicate def) | Top-level `def _sha256_file(path: Path) -> str \| None:` is shadowed by a later top-level redefinition at L9987 (`def _sha256_file(p) -> str:`). The L756 body is unreachable at call time (last def wins). Different signatures/behavior = latent bug if callers relied on `Path`-based variant. | Delete L756-759 (dead) OR rename one; keep the streaming L9987 version (handles large files) and fix its annotation. |
| code_tools.py:9987 | MEDIUM | type-annotation lie | `def _sha256_file(p) -> str:` annotated `-> str` but returns `None` when `os.path.exists(p)` is False. Mypy/callers expecting `str` will get `None`. | Annotate `-> str \| None` (matches L756 intent) or raise on missing file. |
| code_tools.py:2520 | MEDIUM | deadcode (unreachable) | `if False and (...)` block (L2520-2527) is permanently unreachable; the unconditional hash-check at L2528-2530 already supersedes it. Leftover disabled fast-skip. | Remove L2520-2527 dead block. |
| tools_v15.py:5066 | MEDIUM | error-handling gap (swallow) | `except Exception: pass` around `await browser.close()` in CDP fallback-retry. Cleanup swallow hides close/leak errors. | Log at debug; or narrow to `except Exception as exc: _log.debug(...)`. |
| tools_v15.py:5077 | MEDIUM | error-handling gap (swallow) | `except Exception: pass` around `await pw.stop()` before legacy-electron bail. | Log debug; ensure pw instance released. |
| tools_v15.py:5093 | MEDIUM | error-handling gap (swallow) | `except Exception: pass` around `await pw.stop()` when `browser is None`. | Log debug. |
| tools_v15.py:5151 | MEDIUM | error-handling gap (swallow) | `except Exception: pass` around `await pw.stop()` in outer `except Exception as exc:` before `_err` return. | Log debug; the outer exc is returned, so inner swallow is low-risk but still hides stop failures. |
| tools_v15.py:6716 | MEDIUM | error-handling gap (swallow) | `except Exception: pass` on `AttachThreadInput(...,False)` detach in finally (loops over `attached` tids). | `except Exception:` + debug log; ensure all tids attempted even if one fails (current loop continues - OK). |
| code_tools.py (50+) | MEDIUM | broad-except (category) | 50+ (capped) `except Exception:` occurrences across the file. Many wrap and return `_err(...,exc)` (acceptable), but the breadth risks masking specific errors (e.g. `KeyError`/`AttributeError`/`OSError`) as generic failures. | Review individually; narrow to specific exceptions where feasible; ensure each surfaces `error_name`. |
| tools_v15.py (50+) | MEDIUM | broad-except (category) | 50+ (capped) `except Exception:` occurrences. Same risk as above; notable that several reduce to `_err`/dict with `error_name` (good) but some swallow (see 5066-6716). | Review individually; narrow exceptions; verify each returns a structured `error_name`. |
| tools_v15.py:1301 | LOW | unused import (suppressed) | `import playwright  # noqa: F401` - imported but unused (availability probe). Suppressed lint may hide future drift. | Keep if intentional probe; add comment "availability check" or move to a guarded try/except ImportError. |
| tools_v15.py:5452 | LOW | unused import (suppressed) | `import psutil  # noqa: F401` - same pattern. | Same as 1301. |
| tools_v15.py:5679 | LOW | unused import (suppressed) | `import win32gui  # noqa: F401` - same pattern. | Same as 1301. |

## Clean dimensions (scanned, no issues found)

The following patterns returned 0 real hits (false positives excluded):

- `eval(` / real `exec(` (only docstring mentions)
- `os.system(` / `shell=True` (no shell-injection surface)
- `except BaseException` / mutable default args (`def f(x=[])` / `={}`)
- `tempfile.mktemp` / `pickle.loads` / unsafe `yaml.load(`
- `== None` / `!= None` (PEP8-compliant; uses `is None`)
- `is <literal>` (only string-literal false positives)
- Resource leaks: `= open(` without `with` (0)
- Subprocess hangs: all `subprocess.run`/`Popen` have `timeout=` (Popen L3498 is intentional long-lived launcher)
- Socket hangs: all `socket.create_connection`/`socket(...)` have `settimeout`/`timeout=`
- Blocking-in-async: all `time.sleep` are in sync/`to_thread` contexts (no event-loop blocking)
- `global` statements / bare `exit()`/`quit()`
- `assert` (strippable under `-O`) / double-`return` (unreachable) / `except Exception: -> return None` (silent)
- `# TODO/FIXME/XXX/HACK` markers / `# type: ignore` suppressions (code_tools)
- Duplicate top-level defs: only `_sha256_file` (L756/L9987) is a real duplicate; all other repeated names (`_run`,`_write`,`_probe`,`_scan`,`_do`,`_q`) are valid local closures in different parent scopes (verified by reading def context).

## Not verified / unknown

- Individual `.group()` calls (33 in code_tools, 38 in tools_v15): most are inside `re.finditer` loops (safe) or guarded by `if m:`; a full per-site None-guard audit was not completed - mark unknown.
- Individual `json.loads` (50+ / 32): many operate on controlled internal tool output; a per-site `JSONDecodeError` coverage audit was not completed - mark unknown.

## Top 3 hotspots

1. **tools_v15.py L6080-6720** - win32/CDP interaction block: 3 CRITICAL bare `except: pass` + 5 MEDIUM cleanup swallows cluster here.
2. **code_tools.py L756 / L9987** - duplicate `_sha256_file` top-level definition (deadcode + annotation lie).
3. **Broad `except Exception:` blanket** (50+ per file) - the single largest error-handling-smell surface across both files.

END_OPEC5_R1_SCAN