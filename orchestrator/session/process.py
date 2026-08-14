"""Process lifecycle tools extracted from code_tools."""

from __future__ import annotations

import json


def _json_error(message: str, **extra) -> str:
    data = {"ok": False, "error": str(message or "unexpected internal error")}
    data.update(extra)
    data.setdefault("error_name", "INTERNAL_ERROR")
    return json.dumps(data, ensure_ascii=False)


async def kill_process(pid: int = 0, process_name: str = "", force: bool = True) -> str:
    """Terminate a process by PID or by process name. Protected T2 operation."""
    if not pid and not process_name:
        return _json_error(
            "pid or process_name required",
            error_name="MISSING_REQUIRED_FIELD",
            accepted_fields=["pid", "process_name", "force"],
            payload_example={"pid": 1234, "force": True},
        )
    killed = []
    failed = []
    try:
        import psutil
        if pid:
            candidates = [psutil.Process(int(pid))]
        else:
            needle = (process_name or "").lower()
            candidates = [
                proc for proc in psutil.process_iter(["pid", "name"])
                if needle and needle in (proc.info.get("name") or "").lower()
            ]
        for proc in candidates:
            info = {"pid": proc.pid, "name": proc.name()}
            try:
                if force:
                    proc.kill()
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=5)
                except psutil.TimeoutExpired:
                    action = "kill" if force else "terminate"
                    failed.append({
                        **info,
                        "error": f"timeout after 5 seconds waiting for process to exit after {action}",
                    })
                    continue
                killed.append(info)
            except Exception as exc:
                failed.append({**info, "error": str(exc)})
    except Exception as exc:
        return _json_error(
            f"kill_process failed: {exc}",
            error_name="PROCESS_KILL_FAILED",
            pid=pid,
            process_name=process_name,
            force=force,
        )
    finally:
        if pid:
            try:
                import code_tools
                code_tools._reap_background_process(
                    int(pid),
                    timeout=0,
                    kill_if_running=force,
                )
            except Exception:
                pass
    ok = bool(killed) and not failed
    return json.dumps({
        "ok": ok,
        "pid": pid,
        "process_name": process_name,
        "force": force,
        "killed": killed,
        "failed": failed,
        "killed_count": len(killed),
        "failed_count": len(failed),
        "error_name": "" if ok else ("PROCESS_NOT_FOUND" if not killed and not failed else "PROCESS_KILL_PARTIAL_FAILURE"),
    }, ensure_ascii=False)