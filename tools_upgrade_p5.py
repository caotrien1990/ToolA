"""2026-08-07 Upgrade — Phase 5: app lifecycle & runtime verification tools (T2/T4).

Tools: start_app, stop_app, http_check, browser_smoke_test.

- start_app / stop_app reuse [A]'s existing run_project_command (keep_alive
  background launch + health/port verification) and kill_process infra.
- http_check is a pure bounded HTTP probe (stdlib) with optional expected status.
- browser_smoke_test reuses tools_v15 open_browser_page/read_page/_close_browser_page
  (Playwright/Chromium CDP infra) to load a URL and report title + body text.
Registered into code_tools.TOOL_MAP via the UP5 block; mutating/process tools are
added to PROTECTED_TOOL_NAMES to match run_project_command/kill_process.
"""
from __future__ import annotations

import json


async def file_sha256(file_path: str, project_path: str = "", expected_sha256: str = "") -> str:
    """Hash raw file bytes off-loop; never normalize text or seed edit authority."""
    import asyncio
    import hashlib
    import hmac
    import os
    import re
    from pathlib import Path

    if expected_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        return _err("expected_sha256 must be a full SHA-256 digest", error_name="INVALID_SHA256")
    path = Path(file_path)
    if not path.is_absolute():
        if not project_path or not Path(project_path).is_absolute():
            return _err("Relative file_path requires absolute project_path", error_name="PROJECT_PATH_REQUIRED")
        path = Path(project_path) / path
    path = path.resolve()

    def hash_bytes():
        digest = hashlib.sha256()
        count = 0
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                count += len(block)
            after = os.fstat(stream.fileno())
        current = path.stat()
        signature = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if signature(before) != signature(after) or signature(after) != signature(current) or count != after.st_size:
            return _err("File changed while hashing", error_name="FILE_CHANGED_DURING_HASH",
                        ca_next_action="retry_hash_after_writer_finishes")
        sha = digest.hexdigest()
        result = {"file_path": str(path), "sha256": sha, "bytes": count,
                  "hash_domain": "raw_file_bytes", "mtime_ns": after.st_mtime_ns,
                  "read_token_seeded": False}
        if expected_sha256:
            result["matches_expected"] = hmac.compare_digest(sha, expected_sha256.lower())
        return _ok(**result)
    try:
        return await asyncio.to_thread(hash_bytes)
    except OSError as exc:
        return _err(str(exc), error_name="FILE_HASH_FAILED", file_path=str(path))


def _err(msg: str, **extra) -> str:
    d = {"ok": False, "result": "error", "error": msg}
    d.update(extra)
    return json.dumps(d, ensure_ascii=False)


def _ok(**extra) -> str:
    d = {"ok": True, "result": "ok"}
    d.update(extra)
    return json.dumps(d, ensure_ascii=False)


async def start_app(app: str = "", command: str = "", project_path: str = "",
                    health_url: str = "", expected_port: int = 0,
                    expect_status: int = 200, timeout: int = 60,
                    wait_ms: int = 5000, shell_mode: str = "auto",
                    keep_alive: bool = True, **_kw) -> str:
    """T2 MUTATING (process): launch an app as a detached background process and
    verify readiness (HTTP health_url and/or expected_port). Reuses
    run_project_command keep_alive infra. `app` may be a name or path; `command` is
    a raw shell command taking precedence over app. Returns spawned_pid,
    readiness evidence, command_source and app_ignored; existing result fields survive."""
    try:
        import code_tools as ct
        if not app and not command:
            return _err("app or command is required", error_name="MISSING_REQUIRED_FIELD",
                        accepted_fields=["app", "command", "project_path", "health_url",
                                         "expected_port", "timeout"])
        command_ = command or app
        import re
        if re.search(
                r"\b(?:powershell|pwsh)(?:\.exe)?\b[^\r\n]*?"
                r"-(?:Command|c)\b\s+[\"']?\s*-File\b", command_, re.I):
            return _err("PowerShell script launch must use -File directly, not -Command -File",
                        error_name="INVALID_POWERSHELL_FILE_LAUNCH",
                        ca_next_action="use_powershell_File_directly",
                        payload_example={"project_path": project_path,
                                         "command": 'powershell.exe -NoProfile -File "D:/path/runner.ps1"'})
        raw = await ct.run_project_command(
            project_path=project_path or "",
            command=command_,
            timeout=max(10, int(timeout or 60)),
            shell_mode=shell_mode,
            keep_alive=bool(keep_alive),
            background=bool(keep_alive),
            expected_port=int(expected_port or 0),
            health_url=health_url or "",
            expect_status=int(expect_status or 200),
            verify_after_launch_ms=max(0, int(wait_ms or 5000)),
        )
        result = json.loads(raw)
        result["app"] = app or ""
        result["command"] = command_
        result["command_source"] = "command" if command else "app"
        result["app_ignored"] = bool(app and command)
        result["restart_verified"] = False
        result["lifecycle_note"] = "Launch/readiness does not prove replacement; an already-healthy runner may exit 0. Verify owner PID/generation before and after an authorized recycle."
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}", error_name="START_APP_ERROR")


async def stop_app(pid: int = 0, process_name: str = "", force: bool = True,
                   include_children: bool = True, **_kw) -> str:
    """T2 MUTATING (process): stop an app started with start_app by PID or process
    name, INCLUDING its descendants. Reuses kill_process infra. Refuses when
    neither pid nor process_name is given.

    B5 live real-test (2026-08-16): start_app launches through `cmd /c <command>`,
    so the `spawned_pid` it hands back is the cmd.exe wrapper, never the app. Plain
    kill_process kills exactly that one PID -- measured: stop_app answered
    ok:true / killed:[cmd.exe] while the server it was asked to stop kept serving
    200 on its port. So the documented start_app -> stop_app pair silently failed
    for EVERY app, and the false ok is worse than an error: a caller reading it
    stops looking. Killing the descendants first (children before parent, so an
    orphan cannot be re-parented away mid-sweep) is what makes the contract true.
    """
    try:
        if not pid and not process_name:
            return _err("pid or process_name is required", error_name="MISSING_REQUIRED_FIELD",
                        accepted_fields=["pid", "process_name", "force", "include_children"],
                        payload_example={"pid": 1234, "force": True})
        from orchestrator.session.process import kill_process as _kp

        descendants: list[int] = []
        if include_children and pid:
            try:
                import psutil
                descendants = [child.pid
                               for child in psutil.Process(int(pid)).children(recursive=True)]
                # Deepest first: kill leaves before their parents.
                descendants.reverse()
            except Exception:
                descendants = []

        children_result = []
        for child_pid in descendants:
            try:
                children_result.append(json.loads(
                    await _kp(pid=int(child_pid), process_name="", force=bool(force))))
            except Exception as exc:
                children_result.append({"ok": False, "pid": child_pid, "error": str(exc)})

        parent = json.loads(
            await _kp(pid=int(pid or 0), process_name=process_name or "", force=bool(force)))
        if not children_result:
            return json.dumps(parent, ensure_ascii=False)

        # A PID that is already gone is the OUTCOME WE WANTED, not a failure. Killing
        # a tree races with it: measured live, killing the python child made cmd.exe
        # exit on its own, so the next kill in the sweep answered PROCESS_KILL_FAILED
        # "PID not found" and the whole call reported ok:false even though the app was
        # verifiably stopped (port closed). Counting that as a failure would train a
        # caller to ignore ok, which is how the original silent-success bug survived.
        def _split(entries):
            real, vanished = [], []
            for item in entries or []:
                text = str((item or {}).get("error") or "")
                (vanished if "not found" in text.lower() else real).append(item)
            return real, vanished

        killed = list(parent.get("killed") or [])
        failed, vanished = _split(parent.get("failed"))
        # kill_process reports a missing PID as a TOP-LEVEL error, not as a `failed`
        # entry (psutil.NoSuchProcess escapes to its outer except), so both the
        # parent and each child need this second shape handled -- the live run hit
        # it on the parent, which is why `failed` alone was not enough.
        for res in (parent, *children_result):
            if res is not parent:
                killed.extend(res.get("killed") or [])
                child_failed, child_vanished = _split(res.get("failed"))
                failed.extend(child_failed)
                vanished.extend(child_vanished)
            if not res.get("ok") and not (res.get("killed") or res.get("failed")):
                entry = {"pid": res.get("pid"), "error": res.get("error") or "kill failed"}
                (vanished if "not found" in str(entry["error"]).lower()
                 else failed).append(entry)
        parent.update({
            "ok": bool(killed or vanished) and not failed,
            "killed": killed, "failed": failed,
            "killed_count": len(killed), "failed_count": len(failed),
            "already_exited": vanished, "already_exited_count": len(vanished),
            "children_killed": len(descendants),
            "include_children": True,
        })
        if not failed:
            parent["error_name"] = ""
            parent.pop("error", None)
        return json.dumps(parent, ensure_ascii=False)
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}", error_name="STOP_APP_ERROR")


async def http_check(url: str, expect_status: int = 200, timeout: int = 10,
                     method: str = "GET", text_contains: str = "",
                     json_body: dict | None = None,
                     **_kw) -> str:
    """Keep all blocking socket I/O off the server loop, including self-probes."""
    import asyncio
    return await asyncio.to_thread(
        _http_check_sync, url, expect_status, timeout, method, text_contains, json_body)


def _http_check_sync(url: str, expect_status: int = 200, timeout: int = 10,
                     method: str = "GET", text_contains: str = "",
                     json_body: dict | None = None) -> str:
    """T2 READ/PROBE: bounded HTTP request against url; returns status, duration,
    content-type and (bounded) body snippet. ok=false unless status matches
    expect_status (and text_contains, when given, appears in the body)."""
    try:
        if not url:
            return _err("url is required", error_name="MISSING_REQUIRED_FIELD",
                        accepted_fields=["url", "expect_status", "timeout", "method", "text_contains"])
        import time
        import urllib.error as _ue
        import urllib.request as _ur
        method = (method or "GET").upper()
        start = time.monotonic()
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8") if json_body is not None else None
        req = _ur.Request(url, method=method, data=data,
                          headers={"Content-Type": "application/json"} if data is not None else {})
        body_text = ""
        content_type = ""
        try:
            with _ur.urlopen(req, timeout=max(1, int(timeout or 10))) as resp:
                status = int(getattr(resp, "status", 200))
                content_type = (resp.headers.get("Content-Type") or "")[:120]
                raw = resp.read(20000)
                body_text = raw.decode("utf-8", "replace")[:5000]
        except _ue.HTTPError as exc:
            status = int(exc.code)
            content_type = (exc.headers.get("Content-Type") or "")[:120] if exc.headers else ""
            try:
                body_text = exc.read(20000).decode("utf-8", "replace")[:5000]
            except Exception:
                body_text = ""
        except Exception as exc:
            return _err(f"request failed: {exc}", error_name="HTTP_CHECK_FAILED",
                        url=url, method=method, timeout=int(timeout or 10))
        elapsed = round(time.monotonic() - start, 3)
        ok = status == int(expect_status or 200)
        if ok and text_contains:
            ok = str(text_contains) in body_text
        return json.dumps({
            "ok": ok,
            "url": url, "method": method, "status": status,
            "request_body_sent": data is not None,
            "request_body_bytes": len(data) if data is not None else 0,
            "expect_status": int(expect_status or 200),
            "elapsed_seconds": elapsed, "content_type": content_type,
            "body_length": len(body_text),
            "body_preview": body_text,
            "text_contains": text_contains or "",
            "text_contains_match": bool(text_contains and str(text_contains) in body_text),
            "error_name": "" if ok else "HTTP_STATUS_MISMATCH",
        }, ensure_ascii=False)
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}", error_name="HTTP_CHECK_ERROR")


async def browser_smoke_test(url: str, expect_text: str = "",
                             viewport_w: int = 1280, viewport_h: int = 800,
                             headless: bool = True, wait_for: str = "load",
                             timeout_ms: int = 20000, **_kw) -> str:
    """T2 READ/PROBE (CDP): load url in a headless Chromium via tools_v15
    open_browser_page, report title + body text, and verify optional expect_text.
    Closes the browser page afterwards. Requires playwright+chromium."""
    try:
        if not url:
            return _err("url is required", error_name="MISSING_REQUIRED_FIELD",
                        accepted_fields=["url", "expect_text", "timeout_ms", "wait_for"])
        import tools_v15 as _tv
        opened = json.loads(await _tv.open_browser_page(
            url, viewport_w=viewport_w, viewport_h=viewport_h,
            headless=headless, wait_for=wait_for, timeout_ms=timeout_ms))
        if not opened.get("ok"):
            return json.dumps(opened, ensure_ascii=False)
        page_id = opened.get("page_id", "")
        try:
            body = json.loads(await _tv.read_page(page_id, selector="body", max_chars=8000))
            title = opened.get("title", "")
            body_text = body.get("text", "") if body.get("ok") else ""
            found = bool(expect_text and str(expect_text) in body_text)
            return json.dumps({
                "ok": (not expect_text) or found,
                "url": url, "page_id": page_id, "title": title,
                "expect_text": expect_text or "",
                "expect_text_match": found,
                "body_length": len(body_text),
                "body_preview": body_text[:3000],
                "error_name": "" if ((not expect_text) or found) else "BROWSER_TEXT_NOT_FOUND",
            }, ensure_ascii=False)
        finally:
            await _tv._close_browser_page(page_id)
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}", error_name="BROWSER_SMOKE_TEST_ERROR")


UPGRADE_P5_SCHEMA_SPECS = [
    ("start_app",
     "T2 MUTATING (process): launch an app and verify readiness via health_url / expected_port. command takes precedence over app; returns command_source and app_ignored. PowerShell scripts require -File directly; -Command -File (including quoted wrappers) is rejected. An already-healthy runner can exit 0 without replacing the process: restart_verified=false; inspect PID/generation before and after an authorized recycle. Requires session + token for arbitrary shell.",
     {"app": {"type": "string", "default": "", "description": "App name/path (used as the command when command is empty)."},
      "command": {"type": "string", "default": "", "description": "Raw shell command (takes precedence over app). PowerShell: powershell.exe -NoProfile -File runner.ps1, never -Command -File. Exit 0/readiness does not prove recycle."},
      "project_path": {"type": "string", "default": "", "description": "Working directory / project root."},
      "health_url": {"type": "string", "default": "", "description": "HTTP readiness URL to probe after launch."},
      "expected_port": {"type": "integer", "default": 0, "description": "TCP port to probe after launch."},
      "expect_status": {"type": "integer", "default": 200, "description": "Expected HTTP status for health_url."},
      "timeout": {"type": "integer", "default": 60, "description": "Command timeout (seconds)."},
      "wait_ms": {"type": "integer", "default": 5000, "description": "Readiness verification wait (ms)."},
      "shell_mode": {"type": "string", "default": "auto", "description": "auto|cmd|powershell|exec|posix."},
      "keep_alive": {"type": "boolean", "default": True, "description": "Detach and keep running in background."}},
     ["project_path"]),  # W7 (F020): start_app has mutation_scope "process"; the gate needs a project root and there is no other field to derive one from.
    ("stop_app",
     "T2 MUTATING (process): stop an app by PID or process name, including its descendants. Reuses kill_process. Refuses when neither pid nor process_name is given. Mutating tool.",
     {"pid": {"type": "integer", "default": 0, "description": "Process PID to stop."},
      "process_name": {"type": "string", "default": "", "description": "Process name to stop (all matching)."},
      "force": {"type": "boolean", "default": True, "description": "Force kill (vs graceful terminate)."},
      "include_children": {"type": "boolean", "default": True, "description": "Also kill descendants. Required for apps started by start_app, whose spawned_pid is the cmd.exe wrapper, not the app itself."}},
     []),
    ("http_check",
     "T2 READ/PROBE: bounded HTTP request off the event loop, including self-health calls. Returns status, elapsed, content-type and bounded body snippet. ok=false unless status matches expect_status and text_contains (when given) appears in the body. json_body={} sends an empty JSON object for POST.",
     {"url": {"type": "string", "description": "HTTP URL to probe."},
      "expect_status": {"type": "integer", "default": 200, "description": "Expected HTTP status."},
      "timeout": {"type": "integer", "default": 10, "description": "Request timeout (seconds)."},
      "method": {"type": "string", "default": "GET", "enum": ["GET", "HEAD", "POST"], "description": "HTTP method."},
      "json_body": {"type": "object", "description": "Optional JSON request body. Pass {} for POST endpoints requiring an empty JSON object; omitted sends no body. Endpoint paths/methods are never rewritten."},
      "text_contains": {"type": "string", "default": "", "description": "Optional substring the body must contain."}},
     ["url"]),
    ("browser_smoke_test",
     "T2 READ/PROBE (CDP): load a url in a headless Chromium (Playwright), report title + body text, and verify optional expect_text. Closes the browser afterwards. Requires playwright + chromium installed.",
     {"url": {"type": "string", "description": "URL to load."},
      "expect_text": {"type": "string", "default": "", "description": "Optional text the body must contain."},
      "viewport_w": {"type": "integer", "default": 1280, "description": "Viewport width."},
      "viewport_h": {"type": "integer", "default": 800, "description": "Viewport height."},
      "headless": {"type": "boolean", "default": True, "description": "Run headless (no visible window)."},
      "wait_for": {"type": "string", "default": "load", "enum": ["load", "domcontentloaded", "networkidle", "commit"], "description": "Navigation wait condition."},
      "timeout_ms": {"type": "integer", "default": 20000, "description": "Navigation timeout (ms)."},
      # B5 live real-test (2026-08-16): mutation_scope "process" makes the dispatcher
      # resolve a project root, so a call with no project_path answers 400
      # PROJECT_PATH_REQUIRED -- and the caller could not comply, because the field was
      # not declared here and S4 then rejected it as UNSUPPORTED_FIELD. A dead-end
      # recovery hint, the same shape as B1/P11b. open_browser_page, the tool this one
      # wraps, declares project_path for exactly this reason.
      "project_path": {"type": "string", "description": "Project root for session/scope resolution (required by the process-scope gate)."}},
     ["url", "project_path"]),  # W7 (F020): B5 declared the field but left it out of `required`, so a schema-built payload still 400d.
]

READ_EVIDENCE_SCHEMA_SPECS = [(
    "file_sha256", "T1 read-only streaming SHA-256 of raw file bytes; optional expected digest comparison. Does not seed a write token.",
    {"file_path": {"type": "string", "description": "Absolute file path or path relative to explicit project_path."},
     "project_path": {"type": "string", "default": "", "description": "Absolute base for relative file_path."},
     "expected_sha256": {"type": "string", "default": "", "description": "Optional complete 64-character digest to compare."}},
    ["file_path"],
)]
