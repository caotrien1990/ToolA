"""A30-P5: open-code-review (OCR) as a semantic layer beside ruff.

OCR is a Go binary shipped through npm. [A] owns its install directory --
`venv_ocr/`, a sibling of `venv312/` -- for the same reason venv312 owns ruff: an
upgrade is a one-line pin bump plus one `npm install --prefix`, and nothing
outside that folder can change which binary [A] runs. The experimental harness
folder and the global npm prefix are deliberately NOT in the resolve order: the
harness can be moved or deleted at any time, and a global prefix is shared with
every other tool on the machine, so an `npm update` elsewhere could silently
swap the reviewer under [A].

Delegation mode only (`delegate preview` / `delegate rule`): it needs no LLM, so
it costs zero tokens and cannot ship the repo to a provider. `scan` without
--preview does need a provider; `ocr config --help` (measured) shows OCR reads
its provider config from ~/.opencodereview and documents NO env override, and
that directory currently holds only `last-update-check`. So LLM mode refuses up
front with OCR_LLM_NOT_CONFIGURED instead of burning a call to discover it.

Subprocess rules inherited from A19/A24b, not re-derived:
- Call `opencodereview.exe` directly, never `ocr.cmd`/`ocr.ps1`. Over HTTP the
  daemon runs pythonw.exe (GUI subsystem, no stdout handle); every shell shim in
  between is one more place stdout can come back empty while rc != 0, and the
  caller then reads "no findings". capture_output gives explicit pipes.
- `--color never` on every invocation from day one. The daemon env carries
  FORCE_COLOR=3; B029a proved a survey that lists one call site misses the
  second one, so the flag is applied in the single spawn helper below.
- An empty stdout on rc 0 is reported as an anomaly, never laundered into clean.
"""
from __future__ import annotations

import asyncio
import glob as _glob
import json as _json
import os
import shutil
import subprocess
import time

_OCR_PACKAGE = "@alibaba-group/open-code-review"
_OCR_PINNED_VERSION = "1.12.4"
_OCR_VENV_DIRNAME = "venv_ocr"
# Measured install layout (npm install --prefix venv_ocr, v1.12.4 windows/amd64):
# venv_ocr\node_modules\@alibaba-group\ocr-win32-x64\bin\opencodereview.exe
_OCR_BIN_EXACT = os.path.join(
    "node_modules", "@alibaba-group", "ocr-win32-x64", "bin", "opencodereview.exe",
)
_OCR_BIN_GLOB = os.path.join(
    "node_modules", "@alibaba-group", "ocr-*", "bin", "opencodereview*",
)
# Delegate mode is a git diff walk: fast. Kept well under the ~180s MCP proxy
# ceiling on purpose -- a tool that can only answer after the proxy gives up is
# not a tool. `scan --preview` is planning only (still no LLM) but touches more
# files, so it gets a larger budget that is still inside the ceiling.
_OCR_TIMEOUT_DELEGATE = 120
_OCR_TIMEOUT_SCAN = 150
_OCR_LARGE_STRING_BYTES = 512


def _a_repo_root() -> str:
    """[A]'s own repo root -- where venv312 and venv_ocr live.

    Deliberately NOT the project under review: the OCR install belongs to [A],
    while project_path is whatever repo the caller points at.
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ocr_install_dir() -> str:
    return os.path.join(_a_repo_root(), _OCR_VENV_DIRNAME)


def _ocr_install_command() -> str:
    return 'npm install --prefix "%s"' % _ocr_install_dir()


def _ocr_binary() -> dict:
    """Resolve the OCR executable. The order is policy, not taste.

    1. venv_ocr/ -- the install [A] owns and upgrades.
    2. env OCR_BINARY -- a manual override, for a machine that keeps the binary
       somewhere else on purpose.
    PATH is consulted only to REPORT an unmanaged copy; it is never executed,
    because a binary [A] did not install is a binary [A] cannot pin.
    """
    install_dir = _ocr_install_dir()
    owned = os.path.join(install_dir, _OCR_BIN_EXACT)
    if os.path.isfile(owned):
        return {"ok": True, "path": owned, "source": "venv_ocr",
                "install_dir": install_dir, "pinned_version": _OCR_PINNED_VERSION}
    for hit in sorted(_glob.glob(os.path.join(install_dir, _OCR_BIN_GLOB))):
        if os.path.isfile(hit):
            return {"ok": True, "path": hit, "source": "venv_ocr_glob",
                    "install_dir": install_dir, "pinned_version": _OCR_PINNED_VERSION}
    override = (os.environ.get("OCR_BINARY") or "").strip().strip('"')
    if override and os.path.isfile(override):
        return {"ok": True, "path": override, "source": "env_OCR_BINARY",
                "install_dir": install_dir, "pinned_version": None}
    unmanaged = shutil.which("opencodereview") or shutil.which("ocr")
    return {
        "ok": False,
        "error_name": "OCR_BINARY_NOT_FOUND",
        "error": "open-code-review is not installed in the directory [A] owns",
        "install_dir": install_dir,
        "expected_path": owned,
        "package": _OCR_PACKAGE,
        "pinned_version": _OCR_PINNED_VERSION,
        "install_command": _ocr_install_command(),
        "unmanaged_on_path": unmanaged,
        "unmanaged_note": (
            "seen on PATH but NOT used: an install [A] does not own can be changed "
            "by any other npm operation on this machine"
        ) if unmanaged else None,
        "ca_next_action": "install_ocr_into_venv_ocr",
    }


def _ocr_child_env() -> dict:
    env = dict(os.environ)
    # --color never already wins, but FORCE_COLOR in the daemon env is exactly
    # the kind of inherited surprise that produced A24's escape-code noise.
    env.pop("FORCE_COLOR", None)
    return env


def _clean_root(project_path: str):
    if not project_path or not str(project_path).strip():
        return None, "project_path required"
    root = os.path.realpath(str(project_path).strip().strip('"'))
    if not os.path.isdir(root):
        return None, "project_path not found or not a directory: %s" % root
    return root, None


def _rel_inside(root: str, candidate: str):
    """Return candidate as a repo-relative posix path, or (None, error)."""
    raw = str(candidate or "").strip().strip('"')
    if not raw:
        return None, "empty path"
    target = os.path.realpath(os.path.join(root, raw))
    try:
        if os.path.commonpath([root, target]) != root:
            raise ValueError
    except ValueError:
        return None, "path must stay inside project_path: %s" % raw
    rel = os.path.relpath(target, root).replace(os.sep, "/")
    return ("." if rel == "." else rel), None


def _as_csv(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip()


def _ocr_cost_key(args) -> str:
    """Cost bucket for one OCR spawn: keyed by SUBCOMMAND, not by tool name.

    `map`, `scan` and `review` differ by more than an order of magnitude, so one
    bucket per module would floor the cheap subcommand with the expensive one's
    number. Keying on `args[0]` also means the floor is measured at the SAME place
    it is enforced -- the dispatcher's per-tool timings include [A]'s own overhead,
    while `timeout` bounds this subprocess and nothing else.
    """
    head = ""
    try:
        for a in args:
            text = str(a)
            if text and not text.startswith("-"):
                head = text
                break
    except Exception:
        head = ""
    return "ocr_spawn:%s" % (head or "?")


async def _ocr_run(args, root: str, timeout: int) -> dict:
    """The ONLY place this module spawns OCR. One spawn site, one flag policy."""
    resolved = _ocr_binary()
    if not resolved.get("ok"):
        return resolved
    command = [resolved["path"], *[str(a) for a in args], "--color", "never"]
    child_env = _ocr_child_env()

    # A41 tier 2a: refuse a timeout this repo has already been MEASURED to outrun.
    #
    # Measured 2026-09-21: `ocr_map timeout=45` failed while the real cost of the
    # same work was 23-42s -- the caller picked a number blind, and the only thing
    # [A] could say afterwards was "it timed out". D7's lesson applies here without
    # change: do not add a field explaining the mistake after it happens, narrow the
    # contract so the wrong call cannot be made. The floor is never hardcoded; it
    # comes from >=3 real samples of THIS subcommand on THIS repo, so it is silent
    # until [A] has actually measured something, and it moves when the repo does.
    cost_key = _ocr_cost_key(args)
    cost = None
    try:
        from orchestrator.core import call_cache as _cc
        cost = _cc.measured_cost(cost_key, root)
    except Exception:
        cost = None
    if cost and float(timeout) < float(cost["p95_s"]):
        floor = float(cost["p95_s"])
        need = int(floor) + (1 if floor > int(floor) else 0)
        return {
            "ok": False,
            "error_name": "TIMEOUT_BELOW_MEASURED_FLOOR",
            "error": ("timeout=%ss is below the measured p95 of %.1fs for this "
                      "subcommand on this repo (%d samples: min %.1fs, median %.1fs, "
                      "max %.1fs)" % (timeout, floor, cost["samples"], cost["min_s"],
                                      cost["median_s"], cost["max_s"])),
            "measured_cost": cost,
            "min_timeout_s": need,
            "stopped_by": "caller_parameter_gate",
            "spawned": False,
            "ca_next_action": "resend_with_timeout_at_or_above_min_timeout_s",
            "retry_sequence": [{"timeout": need,
                                "note": "the measured p95 rounded up; not a blind increase"}],
            "hint": ("Nothing was spawned, so no work was wasted. This is not a blind "
                     "widening: min_timeout_s is this repo's own measured p95. If that "
                     "number is larger than the budget you have, narrow the scope "
                     "instead -- a smaller file set is the only other direction."),
        }

    started = time.time()
    try:
        completed = await asyncio.to_thread(
            subprocess.run, command, cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        # D3: a deadline hit is a CALLER-PARAMETER problem (timeout too small for
        # this repo), not a broken binary. Folding it into OCR_EXEC_FAILED handed
        # the CA the generic "retry the same payload" advice, which times out
        # again; the distinct name is what lets _ca_next_action say raise-timeout.
        #
        # A41 tier 2b: split the two causes the single name used to merge. A timeout
        # that cleared the p95 gate but lost to a run this repo has ALREADY produced
        # is a caller number that is too low (the work is fine); with no measurement
        # at all, the honest name stays the neutral OCR_TIMEOUT.
        #
        # DORMANT UNTIL ~12 SAMPLES, and that is arithmetic, not a bug: `measured_cost`
        # takes p95 as `samples[round(0.95*(n-1))]`, which equals `max_s` for every
        # n <= 11. While the two coincide the pre-flight gate above has already
        # refused anything below max, so this branch cannot be reached -- a CA reading
        # it as dead code would be measuring the sample count, not the code path.
        _too_low = bool(cost and float(timeout) < float(cost["max_s"]))
        out = {
            "ok": False,
            "error_name": "CALLER_TIMEOUT_TOO_LOW" if _too_low else "OCR_TIMEOUT",
            "error": "ocr did not finish within timeout=%ss" % timeout,
            "binary": resolved["path"],
            "binary_source": resolved["source"],
            "command": command,
            "timeout": timeout,
            "timeout_exceeded": True,
            "stopped_by": "caller_timeout",
            "duration_s": round(time.time() - started, 3),
        }
        if cost:
            out["measured_cost"] = cost
            _need = float(cost["max_s"])
            out["min_timeout_s"] = int(_need) + (1 if _need > int(_need) else 0)
        return out
    except Exception as exc:
        return {
            "ok": False,
            "error_name": "OCR_EXEC_FAILED",
            "error": "%s: %s" % (type(exc).__name__, exc),
            "binary": resolved["path"],
            "binary_source": resolved["source"],
            "command": command,
            "timeout": timeout,
            "duration_s": round(time.time() - started, 3),
        }
    if completed.returncode == 0:
        try:
            from orchestrator.core import call_cache as _cc_obs
            _cc_obs.observe(cost_key, root, time.time() - started)
        except Exception:
            pass  # the floor is an optimisation; never fail a good OCR run over it
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    out = {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr[:4000],
        "stdout_bytes": len(stdout.encode("utf-8", "replace")),
        "stderr_bytes": len(stderr.encode("utf-8", "replace")),
        "stdout_empty": not stdout.strip(),
        "binary": resolved["path"],
        "binary_source": resolved["source"],
        "command": command,
        "color_flag": "never",
        # Derived from the env dict actually handed to the child, not restated
        # from intent: A24's `config: "isolated"` stayed green after --isolated
        # was removed precisely because the disclosure was a literal.
        "force_color_scrubbed": ("FORCE_COLOR" in os.environ
                                 and "FORCE_COLOR" not in child_env),
        "duration_s": round(time.time() - started, 3),
    }
    if completed.returncode != 0:
        out["error_name"] = "OCR_EXEC_FAILED"
        out["error"] = "ocr exited %s" % completed.returncode
    elif out["stdout_empty"]:
        # Never launder an empty capture into "nothing to report".
        out["anomaly"] = "empty_stdout_on_success"
    return out


def _fail(run: dict, extra: dict = None) -> str:
    payload = dict(run)
    payload["ok"] = False
    payload.setdefault("error_name", "OCR_EXEC_FAILED")
    payload.pop("stdout", None)
    if extra:
        payload.update(extra)
    return _json.dumps(payload, ensure_ascii=False)


def _parse_json(run: dict) -> tuple:
    if run.get("stdout_empty"):
        return None, {
            "error_name": "OCR_EMPTY_OUTPUT",
            "error": "ocr exited 0 but produced no stdout; a --format json call must "
                     "produce json, so this is an anomaly, not a clean result",
            "ca_next_action": "rerun_with_format_text_to_inspect",
        }
    text = run.get("stdout") or ""
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        return None, {"error_name": "OCR_OUTPUT_NOT_JSON",
                      "error": "no json object found in stdout",
                      "stdout_head": text[:400]}
    try:
        return _json.loads(text[start:]), None
    except Exception as exc:
        return None, {"error_name": "OCR_OUTPUT_NOT_JSON",
                      "error": "%s: %s" % (type(exc).__name__, exc),
                      "stdout_head": text[:400]}


def _shrink(value, keep_large: bool, _depth: int = 0):
    """Replace fat strings with a byte count unless the caller asked for them.

    Key-name agnostic on purpose: `delegate rule` returns 7886 bytes of rule
    text for one .py file and `delegate preview` inlines the whole commit
    message. Gating by shape rather than by a guessed key name means a renamed
    field cannot quietly re-inflate the response.
    """
    if keep_large or _depth > 6:
        return value
    if isinstance(value, str):
        raw = len(value.encode("utf-8", "replace"))
        if raw > _OCR_LARGE_STRING_BYTES:
            return {"omitted": True, "bytes": raw, "head": value[:160]}
        return value
    if isinstance(value, dict):
        return {k: _shrink(v, keep_large, _depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_shrink(v, keep_large, _depth + 1) for v in value]
    return value


def _ocr_findings_to_common(items) -> list:
    """Normalize OCR findings onto the shape ruff findings already use, so both
    linters can sit in one list instead of forcing the caller to learn two."""
    common = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        loc = item.get("location") if isinstance(item.get("location"), dict) else {}
        line = (item.get("line") or item.get("start_line") or item.get("startLine")
                or loc.get("line") or loc.get("start_line"))
        try:
            line = int(line)
        except (TypeError, ValueError):
            line = None
        common.append({
            "file": item.get("file") or item.get("path") or item.get("file_path")
                    or loc.get("file") or loc.get("path"),
            "line": line,
            "severity": (item.get("severity") or item.get
                         ("level") or item.get("confidence") or "unknown"),
            "rule": item.get("rule") or item.get("rule_id") or item.get("group_id")
                    or item.get("category"),
            "message": item.get("message") or item.get("title") or item.get("description"),
        })
    return common


def _base_meta(run: dict) -> dict:
    return {
        "binary": run.get("binary"),
        "binary_source": run.get("binary_source"),
        "exit_code": run.get("exit_code"),
        "stdout_bytes": run.get("stdout_bytes"),
        "stderr_bytes": run.get("stderr_bytes"),
        "stdout_empty": run.get("stdout_empty"),
        "duration_s": run.get("duration_s"),
        # Carried onto SUCCESS payloads too, not just failures: _fail() copies the
        # whole run dict, so dropping these here meant the colour policy was only
        # visible when something had already gone wrong. A guarantee nobody can
        # read is a guarantee nobody notices losing (B029a).
        "color_flag": run.get("color_flag"),
        "force_color_scrubbed": run.get("force_color_scrubbed"),
        "llm_used": False,
        "token_cost": 0,
    }


def _range_flags(commit: str, from_ref: str, to_ref: str):
    commit = str(commit or "").strip()
    from_ref = str(from_ref or "").strip()
    to_ref = str(to_ref or "").strip()
    if commit and (from_ref or to_ref):
        return None, ("commit and from_ref/to_ref select the same thing two different "
                      "ways; pass one or the other, not both")
    flags = []
    if commit:
        flags += ["--commit", commit]
    if from_ref:
        flags += ["--from", from_ref]
    if to_ref:
        flags += ["--to", to_ref]
    return flags, None


async def ocr_review(project_path: str = "", from_ref: str = "", to_ref: str = "",
                    commit: str = "", exclude="", include_background: bool = False,
                    timeout: int = _OCR_TIMEOUT_DELEGATE,
                    **aliases) -> str:  # noqa: ARG001
    """delegate preview: which changed files are worth reviewing. Zero LLM."""
    root, err = _clean_root(project_path)
    if err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION", "error": err},
                           ensure_ascii=False)
    range_flags, err = _range_flags(commit, from_ref, to_ref)
    if err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION", "error": err},
                           ensure_ascii=False)
    args = ["delegate", "preview", "--repo", root, "--format", "json", *range_flags]
    excl = _as_csv(exclude)
    if excl:
        args += ["--exclude", excl]
    run = await _ocr_run(args, root, int(timeout or _OCR_TIMEOUT_DELEGATE))
    if not run.get("ok"):
        return _fail(run)
    data, perr = _parse_json(run)
    if perr:
        return _fail(run, perr)
    payload = {"ok": True, "mode": "delegate_preview", "repository": root}
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "background" and not include_background:
                raw = len(_json.dumps(value, ensure_ascii=False).encode("utf-8", "replace"))
                payload["background"] = {"omitted": True, "bytes": raw,
                                         "hint": "include_background=true to fetch it"}
                continue
            payload[key] = _shrink(value, include_background)
    else:
        payload["raw"] = _shrink(data, include_background)
    payload.update(_base_meta(run))
    payload["ca_next_action"] = "ocr_rule"
    payload["ca_hint_rule"] = ("ocr_rule(paths=[...]) returns the review rule that "
                               "applies to those files")
    return _json.dumps(payload, ensure_ascii=False)


# A32-1.3: the MAP, not the review. `delegate preview` is the right subprocess to
# navigate by -- it already knows which changed files a reviewer would open -- but
# its raw answer is per-file rows and most of them are not landmarks. Measured on
# this worktree: 19 previewed files, 11 of which OCR itself refuses as
# `unsupported_ext`, and two of the eight it DOES offer are .council/ runtime json
# the daemon rewrites every 45s (2 and 4 changed lines). ocr_map renders that same
# call as a navigation surface, cbm_graph-shaped: keep the files a change actually
# lives in, and say OUT LOUD what was dropped and why. An exclusion that does not
# name itself reads as "nothing there" -- the same laundering this module already
# refuses for an empty capture and for a rule it did not author.
_OCR_MAP_MIN_CHANGED_LINES = 10
_OCR_MAP_STATE_DIRS = (".council/", ".orchestrator_cache/")
# A .json survives only when its NAME says it configures something. That is what
# separates package.json / a schema file from runtime_state.json and
# worker_models.json, and it is a name test on purpose: reading the file to decide
# would make a read-only map tool open every changed json in the diff.
_OCR_MAP_CONFIG_JSON_HINTS = ("schema", "config", "package", "tsconfig",
                              "requirements", "manifest", "lock")


def _ocr_map_drop_reason(path: str, changed: int, min_changed: int):
    """Why a previewed file is not a navigation landmark. None = keep it.

    Order matters: a .council/*.json must report the DIRECTORY reason, not the
    json one, so the count in excluded_summary names the cause a CA can act on.
    Note the leading-"./" strip is a slice, NOT lstrip("./") -- lstrip takes a
    CHARACTER SET, so it would eat the dot of ".council/" and silently defeat the
    very first rule below.
    """
    posix = str(path or "").replace("\\", "/")
    if posix.startswith("./"):
        posix = posix[2:]
    low = posix.lower()
    for state_dir in _OCR_MAP_STATE_DIRS:
        if low.startswith(state_dir) or ("/" + state_dir) in low:
            return "runtime_state_dir"
    base = low.rsplit("/", 1)[-1]
    if base.endswith("_state.json"):
        return "state_snapshot"
    if low.endswith(".json") and not any(h in base for h in _OCR_MAP_CONFIG_JSON_HINTS):
        return "data_json"
    if changed < min_changed:
        return "below_min_changed_lines"
    return None


async def ocr_map(project_path: str = "", from_ref: str = "", to_ref: str = "",
                  commit: str = "", exclude="",
                  min_changed_lines: int = _OCR_MAP_MIN_CHANGED_LINES,
                  include_excluded: bool = False,
                  timeout: int = _OCR_TIMEOUT_DELEGATE,
                  **aliases) -> str:  # noqa: ARG001
    """delegate preview as a navigation map: where a change lives. Zero LLM.

    Same subprocess as ocr_review, a different question. ocr_review answers "what
    would a reviewer open"; ocr_map answers "which files is this change actually
    in", sorted by weight, with every dropped file accounted for by reason. It
    reads no LLM config and can reach no provider -- like cbm_graph, it is a map.
    """
    root, err = _clean_root(project_path)
    if err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION", "error": err},
                           ensure_ascii=False)
    commit_s = str(commit or "").strip()
    from_s = str(from_ref or "").strip()
    to_s = str(to_ref or "").strip()
    range_flags, err = _range_flags(commit_s, from_s, to_s)
    if err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION", "error": err},
                           ensure_ascii=False)
    try:
        min_changed = int(min_changed_lines)
    except (TypeError, ValueError):
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "min_changed_lines must be an integer"},
                           ensure_ascii=False)
    if min_changed < 0:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "min_changed_lines must be >= 0 (0 keeps every file)"},
                           ensure_ascii=False)
    args = ["delegate", "preview", "--repo", root, "--format", "json", *range_flags]
    excl = _as_csv(exclude)
    if excl:
        args += ["--exclude", excl]
    run = await _ocr_run(args, root, int(timeout or _OCR_TIMEOUT_DELEGATE))
    if not run.get("ok"):
        return _fail(run)
    data, perr = _parse_json(run)
    if perr:
        return _fail(run, perr)
    if not isinstance(data, dict):
        return _fail(run, {"error_name": "OCR_OUTPUT_NOT_JSON",
                           "error": "delegate preview did not return a JSON object"})

    kept, dropped_by_a = [], []
    for row in data.get("reviewable_files") or []:
        if not isinstance(row, dict):
            continue
        changed = int(row.get("insertions") or 0) + int(row.get("deletions") or 0)
        entry = {"path": row.get("path") or "", "status": row.get("status"),
                 "changed": changed}
        reason = _ocr_map_drop_reason(entry["path"], changed, min_changed)
        if reason:
            entry["exclude_reason"] = reason
            dropped_by_a.append(entry)
        else:
            kept.append(entry)
    dropped_by_ocr = []
    for row in data.get("excluded_files") or []:
        if not isinstance(row, dict):
            continue
        dropped_by_ocr.append({
            "path": row.get("path") or "", "status": row.get("status"),
            "changed": int(row.get("insertions") or 0) + int(row.get("deletions") or 0),
            "exclude_reason": row.get("exclude_reason") or "excluded_by_ocr",
        })
    kept.sort(key=lambda item: (-item["changed"], item["path"]))
    summary = {}
    for entry in (*dropped_by_a, *dropped_by_ocr):
        reason = entry["exclude_reason"]
        summary[reason] = summary.get(reason, 0) + 1

    payload = {
        "ok": True, "mode": "delegate_map", "repository": root,
        "ref_metadata": {
            "ocr_mode": data.get("mode"),
            "schema_version": data.get("schema_version"),
            "commit": data.get("commit") or commit_s or None,
            "from_ref": from_s or None,
            "to_ref": to_s or None,
            # Named, and answered honestly: measured on BOTH mode=workspace and
            # mode=commit, delegate preview emits no merge base at all. Computing
            # one here and shipping it inside ref_metadata would be [A] inventing
            # a number and attributing it to OCR -- same refusal as pinned_version
            # being None for a binary [A] did not install.
            "merge_base": None,
            "merge_base_source": "not_emitted_by_ocr_preview",
        },
        "files": kept,
        "counts": {"previewed": int(data.get("total_files") or 0),
                   "mapped": len(kept), "dropped_by_ocr": len(dropped_by_ocr),
                   "dropped_by_a": len(dropped_by_a)},
        "totals": {"insertions": int(data.get("total_insertions") or 0),
                   "deletions": int(data.get("total_deletions") or 0)},
        "min_changed_lines": min_changed,
        "excluded_summary": summary,
    }
    if include_excluded:
        payload["excluded_files"] = [*dropped_by_a, *dropped_by_ocr]
    payload.update(_base_meta(run))
    payload["ca_next_action"] = "ocr_rule" if kept else "none"
    payload["ca_hint_map"] = (
        "files[] is sorted by changed-line count, so the first entry is where the "
        "change lives. excluded_summary counts every dropped file by reason; lower "
        "min_changed_lines or pass include_excluded=true to see them.")
    return _json.dumps(payload, ensure_ascii=False)


# A32-1.1: the compact answer to "what should I look for in these files". `delegate
# rule` returns a 7598-byte body for one .py; a CA that only needs the checklist pays
# ~1900 tokens for prose it already knows. These 7 lines are that body's own structure,
# condensed -- and they are NOT OCR's wording, so substituting them for a rule OCR did
# not author would be fabrication. _ocr_checklist_applies() is the gate that refuses to.
_OCR_PY_CHECKLIST = (
    "Khi review Python, kiểm tra 7 nhóm:",
    "1. Boundary: input rỗng, off-by-one, None propagation, float ==",
    "2. Mutable: def f(x=[]), class-level shared state, closure loop var",
    "3. Errors: bare except, silent pass, traceback loss, broad try",
    "4. Resources: file/socket/lock không dùng with, try-finally thiếu",
    "5. Performance: += trong loop, list vs set lookup, f-string trong logging",
    "6. Concurrency: sync I/O trong async, task không await, shared state",
    "7. Security: eval/exec, shell=True, pickle, yaml.load, SQL concat, weak crypto",
)


def _ocr_checklist_applies(group) -> bool:
    """True only when _OCR_PY_CHECKLIST really is a condensation of THIS group.

    Both conditions are read off the live payload: source "system" (the rule OCR
    ships, which the checklist was written from) and a pattern that covers Python.
    A repo-authored rule, or a system rule for another language, keeps its byte
    count -- a condensation of someone else's text is not a summary, it is a guess.
    """
    if not isinstance(group, dict):
        return False
    if str(group.get("source") or "").strip().lower() != "system":
        return False
    pattern = str(group.get("pattern") or "").lower()
    for ch in "{},*/.":
        pattern = pattern.replace(ch, " ")
    return "py" in pattern.split()


async def ocr_rule(project_path: str = "", paths=None, commit: str = "",
                   from_ref: str = "", to_ref: str = "",
                   include_rule_text: bool = False, compact: bool = False,
                   timeout: int = _OCR_TIMEOUT_DELEGATE,
                   **aliases) -> str:  # noqa: ARG001
    """delegate rule: the rule text that applies to given files. Zero LLM.

    include_rule_text defaults to False because one .py file measured 7886 bytes
    of rule body. That is a token-discipline default, not a missing feature:
    ask for it when the group is actually being reviewed.

    compact=True answers with the condensed 7-group checklist in place of that
    body (~100 tokens against ~1900). It is a substitution, not a truncation, so
    it applies only where the substitution is honest (_ocr_checklist_applies) and
    it always declares verbatim_ocr_text=False. compact and include_rule_text
    contradict each other -- asking for the full text and for a condensation of it
    in one call is refused rather than silently ranked.
    """
    root, err = _clean_root(project_path)
    if err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION", "error": err},
                           ensure_ascii=False)
    if compact and include_rule_text:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": ("compact and include_rule_text are mutually "
                                      "exclusive: one replaces the rule body with a "
                                      "checklist, the other returns it in full")},
                           ensure_ascii=False)
    if isinstance(paths, str):
        paths = [p for p in paths.split(",") if p.strip()]
    if not paths:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "paths required: one or more repo-relative files"},
                           ensure_ascii=False)
    rels = []
    for candidate in paths:
        rel, perr = _rel_inside(root, candidate)
        if perr:
            return _json.dumps({"ok": False, "error_name": "PATH_OUTSIDE_PROJECT",
                                "error": perr}, ensure_ascii=False)
        rels.append(rel)
    range_flags, err = _range_flags(commit, from_ref, to_ref)
    if err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION", "error": err},
                           ensure_ascii=False)
    args = ["delegate", "rule", "--repo", root, "--format", "json", *range_flags, *rels]
    run = await _ocr_run(args, root, int(timeout or _OCR_TIMEOUT_DELEGATE))
    if not run.get("ok"):
        return _fail(run, {"paths": rels})
    data, perr = _parse_json(run)
    if perr:
        return _fail(run, dict(perr, paths=rels))
    payload = {"ok": True, "mode": "delegate_rule", "repository": root, "paths": rels,
               "rule_text_included": bool(include_rule_text)}
    body = data if isinstance(data, dict) else {"raw": data}
    payload["rule_bytes"] = len(
        _json.dumps(body, ensure_ascii=False).encode("utf-8", "replace"))
    for key, value in body.items():
        payload[key] = _shrink(value, include_rule_text)
    payload.update(_base_meta(run))
    if compact:
        payload["compact"] = True
        groups = payload.get("groups")
        if not isinstance(groups, list):
            groups = []
        condensed = 0
        untouched = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            if not _ocr_checklist_applies(group):
                untouched.append(group.get("group_id"))
                continue
            prior = group.get("rule")
            group["rule"] = {
                "condensed": True,
                "verbatim_ocr_text": False,
                "checklist": list(_OCR_PY_CHECKLIST),
                "body_bytes": prior.get("bytes") if isinstance(prior, dict) else None,
            }
            condensed += 1
        payload["groups_condensed"] = condensed
        payload["groups_not_condensed"] = untouched
        payload["ca_hint_rule_text"] = (
            "checklist is ToolA's condensation of the system Python rule, not OCR's own "
            "wording; include_rule_text=true (without compact) returns that text"
            if condensed else
            "no group here is the system Python rule, so nothing was condensed; "
            "include_rule_text=true (without compact) returns the bodies")
    elif not include_rule_text:
        payload["ca_hint_rule_text"] = ("large strings are reported as byte counts; "
                                        "include_rule_text=true returns them in full")
    return _json.dumps(payload, ensure_ascii=False)


def _ocr_config_state() -> dict:
    """Where OCR reads its provider config, and whether one exists.

    Measured, not assumed: `ocr config --help` documents subcommands
    (provider/model/set/unset) and exactly one global flag (--color). It names
    NO environment variable for the config location, so [A] does not invent one
    and does not try to redirect it. OCR_CONFIG_DIR below is [A]'s own
    detection override only -- it is never passed to the child process.
    """
    override = (os.environ.get("OCR_CONFIG_DIR") or "").strip().strip('"')
    home = os.path.join(os.path.expanduser("~"), ".opencodereview")
    config_dir = override or home
    entries = []
    if os.path.isdir(config_dir):
        try:
            entries = sorted(os.listdir(config_dir))[:40]
        except OSError:
            entries = []
    configured = [e for e in entries
                  if e.lower().startswith("config")
                  and e.lower().endswith((".json", ".yaml", ".yml", ".toml"))]
    return {
        "config_dir": config_dir,
        "config_dir_source": "env_OCR_CONFIG_DIR" if override else "home",
        "config_dir_exists": os.path.isdir(config_dir),
        "config_files": configured,
        "entries": entries,
        "configured": bool(configured),
        "env_override_documented_by_ocr": False,
    }


async def ocr_scan(project_path: str = "", path="", exclude="",
                   preview_only: bool = True, audience: str = "agent",
                   output_format: str = "json", provider: str = "", model: str = "",
                   timeout: int = _OCR_TIMEOUT_SCAN,
                   **aliases) -> str:  # noqa: ARG001
    """scan --preview: what a full review would cover. Zero LLM while preview_only.

    preview_only=False needs a configured provider. Rather than spend an LLM
    call to find out there is none, the refusal below names the directory that
    was checked and the command that would configure it.
    """
    root, err = _clean_root(project_path)
    if err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION", "error": err},
                           ensure_ascii=False)
    fmt = str(output_format or "json").strip().lower()
    if fmt not in ("text", "json", "sarif"):
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "output_format must be text, json or sarif",
                            "accepted": ["text", "json", "sarif"]}, ensure_ascii=False)
    aud = str(audience or "agent").strip().lower()
    if aud not in ("human", "agent"):
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "audience must be human or agent",
                            "accepted": ["human", "agent"]}, ensure_ascii=False)
    config = _ocr_config_state()
    if not preview_only and not config["configured"]:
        return _json.dumps({
            "ok": False,
            "error_name": "OCR_LLM_NOT_CONFIGURED",
            "error": "ocr scan without --preview needs a provider; none is configured",
            "ca_next_action": "use_ocr_review_delegate_mode",
            "delegate_alternative": "ocr_review / ocr_rule need no provider and cost 0 tokens",
            "configure_hint": ['ocr config set provider <name>',
                               'ocr config set model <id>',
                               'ocr config set providers.<name>.api_key "<key>"'],
            "llm_used": False,
            "token_cost": 0,
            **config,
        }, ensure_ascii=False)
    args = ["scan", "--repo", root, "--format", fmt, "--audience", aud]
    if preview_only:
        args.append("--preview")
    rel_paths = []
    for candidate in ([path] if isinstance(path, str) and path.strip()
                      else list(path or [])):
        rel, perr = _rel_inside(root, candidate)
        if perr:
            return _json.dumps({"ok": False, "error_name": "PATH_OUTSIDE_PROJECT",
                                "error": perr}, ensure_ascii=False)
        rel_paths.append(rel)
    if rel_paths:
        args += ["--path", ",".join(rel_paths)]
    excl = _as_csv(exclude)
    if excl:
        args += ["--exclude", excl]
    if provider:
        args += ["--provider", str(provider).strip()]
    if model:
        args += ["--model", str(model).strip()]
    run = await _ocr_run(args, root, int(timeout or _OCR_TIMEOUT_SCAN))
    if not run.get("ok"):
        return _fail(run, {"preview_only": bool(preview_only), "paths": rel_paths})
    payload = {"ok": True, "mode": "scan_preview" if preview_only else "scan_llm",
               "repository": root, "preview_only": bool(preview_only),
               "audience": aud, "format": fmt, "paths": rel_paths}
    if fmt == "json":
        data, perr = _parse_json(run)
        if perr:
            return _fail(run, dict(perr, preview_only=bool(preview_only)))
        body = data if isinstance(data, dict) else {"raw": data}
        # A30-B: cap every list-valued key. Measured defect -- a whole-repo
        # preview_only=true returned 109_259 bytes and the daemon had to spill it into a
        # result_ref. A tool built for token discipline must not be the thing that blows
        # the budget. The cap is DECLARED per key, never silent: a truncation the caller
        # cannot see reads as "that is all there is", which is the worse failure.
        cap = 50
        omitted = {}
        for key, value in body.items():
            shrunk = _shrink(value, False)
            if isinstance(shrunk, list) and len(shrunk) > cap:
                omitted[key] = len(shrunk) - cap
                shrunk = shrunk[:cap]
            payload[key] = shrunk
        if omitted:
            payload["items_omitted"] = omitted
            payload["max_items"] = cap
            payload["ca_hint_scope"] = ("output capped at %d items per list; narrow with "
                                        "path= or exclude= to see the rest" % cap)
        findings = body.get("findings") or body.get("issues") or body.get("results")
        if isinstance(findings, list):
            payload["findings_common"] = _ocr_findings_to_common(findings[:cap])
    else:
        text = run.get("stdout") or ""
        payload["output_head"] = text[:2000]
        payload["output_truncated"] = len(text) > 2000
    payload.update(_base_meta(run))
    payload["llm_used"] = not preview_only
    if not preview_only:
        payload.pop("token_cost", None)
    return _json.dumps(payload, ensure_ascii=False)
