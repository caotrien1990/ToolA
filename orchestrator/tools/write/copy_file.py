"""Single-file copy write tool."""
from __future__ import annotations


async def copy_file(src: str = "", dst: str = "", project_path: str = "",
                    overwrite: bool = True, backup_existing: bool = True) -> str:
    """R81 (T2) - copy ONE file src->dst with audit and sha256 verification."""
    import code_tools as ct
    import shutil
    import time as _t
    from orchestrator.fileops.xroot import classify_write_target, log_intent, log_result

    if not src or not dst:
        return ct._json_error("src and dst are required", error_name="MISSING_REQUIRED_FIELD")
    _pp = ct.Path(project_path) if project_path else None
    sp = ct.Path(src)
    if not sp.is_absolute():
        if _pp is None:
            return ct._json_error(
                "src is relative and project_path is required to resolve it",
                error_name="PROJECT_PATH_REQUIRED",
                hint="Pass project_path to resolve relative src",
            )
        sp = (_pp / src).resolve()
    if not sp.exists() or not sp.is_file():
        return ct._json_error(f"src not found or not a file: {sp}", error_name="SRC_NOT_FOUND")
    dp = ct.Path(dst)
    if not dp.is_absolute():
        if _pp is None:
            return ct._json_error(
                "dst is relative and project_path is required to resolve it",
                error_name="PROJECT_PATH_REQUIRED",
                hint="Pass project_path to resolve relative dst",
            )
        dp = (_pp / dst).resolve()
    session_id = ""
    if project_path:
        try:
            session_id = ct._get_active_session_id(ct._session_project_key(project_path)) or ""
        except Exception:
            session_id = ""
    # #35: the deny list is read for every dst, however it is spelled; being inside
    # project_path grants nothing (see classify_write_target).
    target = classify_write_target(dp, project_path or None, "copy_file", session_id)
    xroot_managed = target["managed"]
    check = target["policy"]
    if not target["allowed"]:
        return ct._json_error(
            check.get("reason", "xroot policy denied write"),
            error_name="XROOT_DENIED",
            group=check.get("group", ""),
            reason=check.get("reason", ""),
        )
    dst_existed = dp.exists()
    if dst_existed and not overwrite:
        return ct._json_error(
            f"dst exists and overwrite=false: {dp}",
            error_name="DST_EXISTS",
            ca_next_action="resend with overwrite=true",
        )
    backup = None
    try:
        if xroot_managed:
            log_intent(dp, "copy_file", session_id)
        dp.parent.mkdir(parents=True, exist_ok=True)
        if dst_existed and backup_existing:
            backup = str(dp) + ".bak_" + _t.strftime("%Y%m%d_%H%M%S")
            shutil.copy2(str(dp), backup)
        shutil.copy2(str(sp), str(dp))
        src_h = ct._sha256_file(sp)
        dst_h = ct._sha256_file(dp)
        if xroot_managed:
            log_result(dp, "copy_file", session_id, ok=src_h == dst_h)
        return ct.json.dumps({
            "ok": src_h == dst_h,
            "src": str(sp),
            "dst": str(dp),
            "bytes": dp.stat().st_size,
            "sha256": dst_h,
            "hash_verified": src_h == dst_h,
            "dst_existed": dst_existed,
            "backup": backup,
        }, ensure_ascii=False)
    except Exception as e:
        if xroot_managed:
            log_result(dp, "copy_file", session_id, ok=False)
        return ct._json_error(f"copy failed: {e}", error_name="COPY_FAILED", backup=backup)
