"""Desktop dialog control helpers extracted from code_tools."""
from __future__ import annotations

import json
from pathlib import Path

from .desktop_dialogs import fill_folder_dialog


async def folder_exists(path: str) -> str:
    """Runtime-local folder existence probe used by click_and_handle_folder_dialog."""
    try:
        p = Path(path).resolve()
        if not p.exists():
            return json.dumps({"ok": True, "path": str(p), "exists": False})
        return json.dumps({
            "ok": True,
            "path": str(p),
            "exists": True,
            "is_dir": p.is_dir(),
            "is_file": p.is_file(),
            "is_symlink": p.is_symlink(),
        })
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc), "error_name": "FOLDER_EXISTS_FAILED"})


async def folder_dialog_snapshot(owner_pid: int = 0,
                                    parent_hwnd: int = 0,
                                    title_pattern: str = "",
                                    launched_after_ts: str = "",
                                    include_child_controls: bool = True) -> str:
    """V15 R31 T1: READ-ONLY discovery of open folder/file dialog candidates.

    Use BEFORE fill_folder_dialog to verify which window CA will target.
    Returns candidate dialogs with full metadata (hwnd, pid, process, class, parent,
    owner, child Edit/Button/ComboBox handles) so CA can disambiguate.

    Filters:
      - owner_pid: only windows owned by this PID (recommended for app-specific dialogs)
      - parent_hwnd: only windows whose parent/owner is this hwnd
      - title_pattern: regex (default common: Select Folder|Open|Browse|Choose|Mở|Chọn)
      - launched_after_ts: ISO timestamp; only windows whose process started after

    Pure read — does NOT mutate. Pair with fill_folder_dialog(owner_pid=...) after.
    """
    try:
        pass
    except Exception:
        return json.dumps({"ok": False, "error_name": "PYWIN32_MISSING",
                            "remediation": "pip install pywin32"})
    import re as _re_s
    common_re = title_pattern or r"(Select|Browse|Choose|Open|Mở|Chọn).*?(Folder|thư mục|File)|^Open$|^Save As$"
    # R31: desktop_window_list lives in tools_v15 (T4 layer) — import it.
    import tools_v15 as _v15_dws
    raw = await _v15_dws.desktop_window_list(
        title_filter="", owner_pid=owner_pid,
        include_child_controls=include_child_controls,
        include_invisible=False,
    )
    try:
        wl = json.loads(raw)
        wins = wl.get("windows", []) or []
    except Exception:
        wins = []
    pat = _re_s.compile(common_re, _re_s.IGNORECASE)
    candidates = []  # R32: ONLY real dialog candidates (class #32770 or known picker class)
    other_windows = []  # R32: title-match only (CapCut/Notepad noise) — separate
    rejected = []
    launched_after_dt = None
    if launched_after_ts:
        try:
            from datetime import datetime as _dt
            launched_after_dt = _dt.fromisoformat(launched_after_ts)
        except Exception:
            pass
    # R32: known modern picker class patterns
    _PICKER_CLASS_RE = _re_s.compile(
        r"^#32770$|FileDialog|DirectUIHWND|Browse.*Folder|CtrlNotifySink",
        _re_s.IGNORECASE,
    )
    for w in wins:
        t = w.get("title", "") or ""
        cls = w.get("class_name", "") or ""
        # R32: strict — must match real dialog class first
        title_match = bool(t and pat.search(t))
        is_dlg_class = bool(_PICKER_CLASS_RE.search(cls))
        if not (title_match or is_dlg_class):
            continue
        # Apply parent_hwnd filter
        if parent_hwnd and w.get("parent_hwnd") != parent_hwnd and w.get("owner_hwnd") != parent_hwnd:
            rejected.append({**w, "reject_reason": "parent_hwnd_mismatch"})
            continue
        # Apply launched_after filter — need create_time, fetch via psutil
        if launched_after_dt:
            try:
                import psutil as _ps
                ct = _ps.Process(w.get("pid", 0)).create_time()
                from datetime import datetime as _dt
                if _dt.fromtimestamp(ct) < launched_after_dt:
                    rejected.append({**w, "reject_reason": "process_predates_launched_after_ts"})
                    continue
            except Exception:
                pass
        # Classify children
        children = w.get("child_controls", []) or []
        edit_count = sum(1 for c in children if "edit" in (c.get("class", "") or "").lower())
        button_count = sum(1 for c in children if "button" in (c.get("class", "") or "").lower())
        entry = {
            **w,
            "is_dialog_class": is_dlg_class,
            "title_match": title_match,
            "edit_count": edit_count,
            "button_count": button_count,
            "picker_kind": (
                "classic_win32_with_edit" if (is_dlg_class and edit_count > 0) else
                "modern_ifiledialog_no_edit" if is_dlg_class else
                "non_dialog_class_match"
            ),
        }
        # R32: strict split — only real dialog classes go into candidates
        if is_dlg_class:
            candidates.append(entry)
        else:
            # title matched but not dialog class → noise (CapCut window titled "Select")
            other_windows.append({**entry, "noise_reason": "title_matched_but_not_dialog_class"})
    return json.dumps({
        "ok": True,
        "candidates_count": len(candidates),
        "candidates": candidates[:10],
        "other_windows_count": len(other_windows),
        "other_windows": other_windows[:5],  # R32: separated from candidates
        "rejected_count": len(rejected),
        "rejected": rejected[:10],
        "title_pattern_used": common_re,
        "filters": {"owner_pid": owner_pid, "parent_hwnd": parent_hwnd,
                     "launched_after_ts": launched_after_ts},
        "ca_next_action": (
            "single_candidate_safe_to_fill_folder_dialog"
            if len(candidates) == 1 else
            "ambiguous_pass_owner_pid_or_parent_hwnd_to_fill_folder_dialog"
            if len(candidates) > 1 else
            "no_dialog_found_verify_app_state_or_button_click"
        ),
    }, ensure_ascii=False)


async def cancel_native_dialog(title_pattern: str = "",
                               button_pattern: str = "Cancel|Huy|Close|Dong",
                                 owner_pid: int = 0,
                                 parent_hwnd: int = 0,
                                 exclude_titles: list | None = None) -> str:
    """V15 R27 / R32 T4: close native dialog by clicking Cancel/Close button.

    R32 parity with fill_folder_dialog: adds owner_pid, parent_hwnd, exclude_titles
    so cancel doesn't match wrong app's window.
    """
    try:
        import win32gui, win32con, win32api, win32process
    except Exception:
        return json.dumps({"ok": False, "error_name": "PYWIN32_MISSING"})
    import re as _re_c
    import time as _t

    target_hwnd = 0
    matched_title = ""
    _exclude = [s.lower() for s in (exclude_titles or [])]
    rejected = []

    def _enum_proc(hwnd, _):
        nonlocal target_hwnd, matched_title
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd) or ""
        if not title:
            return True
        if title_pattern and not _re_c.search(title_pattern, title, _re_c.IGNORECASE):
            return True
        # R32: filter by owner_pid / parent_hwnd / exclude_titles
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0
        if owner_pid and pid != owner_pid:
            rejected.append({"hwnd": hwnd, "title": title[:80], "pid": pid, "reason": "owner_pid_mismatch"})
            return True
        if parent_hwnd:
            try:
                pp = win32gui.GetParent(hwnd) or 0
                oo = win32gui.GetWindow(hwnd, 4) or 0
            except Exception:
                pp = oo = 0
            if pp != parent_hwnd and oo != parent_hwnd:
                rejected.append({"hwnd": hwnd, "title": title[:80], "parent": pp, "reason": "parent_hwnd_mismatch"})
                return True
        t_low = title.lower()
        for ex in _exclude:
            if ex and ex in t_low:
                rejected.append({"hwnd": hwnd, "title": title[:80], "reason": f"excluded_title({ex})"})
                return True
        target_hwnd = hwnd
        matched_title = title
        return False

    try:
        win32gui.EnumWindows(_enum_proc, None)
    except Exception:
        pass
    if not target_hwnd:
        return json.dumps({"ok": False,
                            "error_name": "DIALOG_NOT_FOUND",
                            "title_pattern": title_pattern,
                            "owner_pid": owner_pid,
                            "parent_hwnd": parent_hwnd,
                            "rejected_windows": rejected[:5],
                            "ca_next_action": "verify_owner_filter_or_use_folder_dialog_snapshot"})

    button_hwnd = 0
    button_label = ""
    BTN_RE = _re_c.compile(rf"^({button_pattern})", _re_c.IGNORECASE)

    def _walk(hwnd):
        nonlocal button_hwnd, button_label
        try:
            cls = win32gui.GetClassName(hwnd) or ""
            txt = win32gui.GetWindowText(hwnd) or ""
            if "button" in cls.lower() and BTN_RE.search(txt) and not button_hwnd:
                button_hwnd = hwnd
                button_label = txt
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(target_hwnd, lambda c, _: _walk(c) or True, None)
    except Exception:
        pass
    button_clicked = False
    if button_hwnd:
        try:
            win32api.PostMessage(button_hwnd, win32con.BM_CLICK, 0, 0)
            button_clicked = True
        except Exception:
            pass
    else:
        # Fallback: WM_CLOSE
        try:
            win32api.PostMessage(target_hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass
    # Wait for close
    dialog_closed = False
    deadline = _t.time() + 2.0
    while _t.time() < deadline:
        if not win32gui.IsWindow(target_hwnd) or not win32gui.IsWindowVisible(target_hwnd):
            dialog_closed = True
            break
        _t.sleep(0.15)
    return json.dumps({
        "ok": dialog_closed,
        "hwnd": target_hwnd,
        "matched_title": matched_title,
        "button_clicked": button_clicked,
        "button_label": button_label,
        "dialog_closed": dialog_closed,
        "fallback_used_wm_close": (not button_clicked),
    }, ensure_ascii=False)


async def click_and_handle_folder_dialog(page_id: str,
                                            click_selector: str,
                                            folder_path: str,
                                            owner_pid: int = 0,
                                            owner_title: str = "",
                                            wait_for_dialog_ms: int = 5000,
                                            timeout_ms: int = 8000,
                                            try_modern_picker: bool = True,
                                            verify_target_exists: bool = True) -> str:
    """V15 R31 T4: generic atomic click → folder picker → paste path → verify.

    Steps:
      1. (optional) verify_target_exists: ensure folder_path is real (folder_exists)
      2. Snapshot baseline visible windows (used to detect NEW dialog after click)
      3. Click DOM selector via CDP (uses click_selector via _v15.click_selector)
      4. Poll for NEW window that:
         - has dialog class OR title matches folder/file pattern
         - owner_pid matches (if provided) OR owner_title matches app window owner
         - launched AFTER baseline
      5. Call fill_folder_dialog with discovered owner_pid + parent_hwnd
      6. Return aggregated result + diagnostic

    Generic T4 workflow — not app-specific. Use for any Electron/web app that opens
    a native folder picker after a UI click.

    Inputs:
      page_id: CDP page for click
      click_selector: e.g. '#btnStep3'
      folder_path: absolute target folder
      owner_pid: optional explicit PID filter for dialog discovery
      owner_title: if owner_pid not given, find app window by title to derive PID
      verify_target_exists: pre-flight folder_exists check (default True)
    """
    import json as _jl
    import time as _t
    import re as _re_ch
    result: dict = {"ok": False, "phases": {}}

    # Phase 1: verify target folder
    if verify_target_exists:
        try:
            fe_raw = await folder_exists(path=folder_path)
            fe = _jl.loads(fe_raw)
            result["phases"]["folder_exists"] = {"ok": fe.get("ok"),
                                                  "exists": fe.get("exists"),
                                                  "is_dir": fe.get("is_dir")}
            if not (fe.get("ok") and fe.get("exists") and fe.get("is_dir")):
                return _jl.dumps({
                    "ok": False,
                    "error_name": "TARGET_FOLDER_INVALID",
                    "error": f"folder_path does not exist or is not a directory: {folder_path}",
                    "phases": result["phases"],
                    "ca_next_action": "create_folder_first_or_correct_path",
                }, ensure_ascii=False)
        except Exception as exc:
            result["phases"]["folder_exists_error"] = str(exc)

    # Phase 2: snapshot baseline windows + resolve owner_pid from owner_title
    from datetime import datetime as _dt
    baseline_ts = _dt.now().isoformat(timespec="seconds")
    baseline_hwnds: set = set()
    parent_hwnd_resolved = 0
    if not owner_pid and owner_title:
        try:
            import tools_v15 as _v15_dws2
            wl_raw = await _v15_dws2.desktop_window_list(title_filter=owner_title,
                                                  include_invisible=False)
            wl = _jl.loads(wl_raw)
            wins = wl.get("windows", []) or []
            for w in wins:
                if owner_title.lower() in (w.get("title") or "").lower():
                    owner_pid = w.get("pid", 0) or owner_pid
                    parent_hwnd_resolved = w.get("hwnd", 0) or parent_hwnd_resolved
                    if owner_pid:
                        break
        except Exception:
            pass
    try:
        import tools_v15 as _v15_dws3
        wl_pre_raw = await _v15_dws3.desktop_window_list(title_filter="", include_invisible=False)
        wl_pre = _jl.loads(wl_pre_raw)
        for w in wl_pre.get("windows", []) or []:
            baseline_hwnds.add(w.get("hwnd"))
    except Exception:
        pass
    result["phases"]["baseline"] = {
        "captured_at": baseline_ts,
        "baseline_hwnds_count": len(baseline_hwnds),
        "owner_pid": owner_pid,
        "parent_hwnd_resolved": parent_hwnd_resolved,
    }

    # Phase 3: click selector via CDP
    try:
        import tools_v15 as _v15
        click_raw = await _v15.click_selector(
            page_id=page_id, selector=click_selector,
            before_after_state=False, timeout_ms=3000,
        )
        try:
            click_j = _jl.loads(click_raw)
        except Exception:
            click_j = {"raw": click_raw[:300]}
        result["phases"]["click"] = {"ok": click_j.get("ok"),
                                       "clicked": click_j.get("clicked"),
                                       "error_name": click_j.get("error_name")}
        if click_j.get("ok") is False:
            return _jl.dumps({
                "ok": False,
                "error_name": "CLICK_FAILED",
                "error": f"click_selector failed: {click_j.get('error') or click_j.get('error_name')}",
                "phases": result["phases"],
                "ca_next_action": "verify_selector_or_page_id",
            }, ensure_ascii=False)
    except Exception as exc:
        return _jl.dumps({
            "ok": False,
            "error_name": "CLICK_SELECTOR_UNAVAILABLE",
            "error": str(exc),
            "phases": result["phases"],
        }, ensure_ascii=False)

    # Phase 4: poll for NEW dialog
    deadline_disc = _t.time() + max(0.5, wait_for_dialog_ms / 1000.0)
    new_dialog_hwnd = 0
    new_dialog_meta = None
    while _t.time() < deadline_disc:
        try:
            snap_raw = await folder_dialog_snapshot(
                owner_pid=owner_pid,
                parent_hwnd=parent_hwnd_resolved,
                launched_after_ts=baseline_ts,
                include_child_controls=True,
            )
            snap = _jl.loads(snap_raw)
            for c in (snap.get("candidates") or []):
                hw = c.get("hwnd")
                if hw and hw not in baseline_hwnds:
                    new_dialog_hwnd = hw
                    new_dialog_meta = c
                    break
            if new_dialog_hwnd:
                break
        except Exception:
            pass
        _t.sleep(0.25)

    result["phases"]["dialog_discovery"] = {
        "new_dialog_hwnd": new_dialog_hwnd,
        "new_dialog_meta": new_dialog_meta,
        "elapsed_sec": round(_t.time() - (deadline_disc - max(0.5, wait_for_dialog_ms / 1000.0)), 2),
    }

    # R33: fallback re-snapshot WITHOUT owner_pid/parent filter — dialog may have
    # been opened by a child process (common for Electron sub-process pickers).
    relaxed_candidates = []
    all_visible_dialogs = []   # R34
    nearby_error_modals = []   # R34
    # V15 R45 — adopted existing folder-picker dialog (dialog was already open
    # BEFORE the click, so launched_after_ts filter rejected it as "not new").
    # We fall back to this when no NEW dialog appeared after the click.
    adopted_existing_dialog = None
    if not new_dialog_hwnd:
        try:
            snap_raw2 = await folder_dialog_snapshot(
                owner_pid=0, parent_hwnd=0,
                launched_after_ts=baseline_ts,
                include_child_controls=True,
            )
            snap2 = _jl.loads(snap_raw2)
            relaxed_candidates = (snap2.get("candidates") or [])[:5]
            # R34: also grab all visible top-level windows (no filter) for full evidence
            try:
                import tools_v15 as _v15_dws_r34
                wl_full_raw = await _v15_dws_r34.desktop_window_list(title_filter="", include_invisible=False)
                wl_full = _jl.loads(wl_full_raw)
                all_visible_dialogs = (wl_full.get("windows") or [])[:10]
                # filter for error/warning class
                for w in (wl_full.get("windows") or []):
                    title = (w.get("title") or "").lower()
                    cls = (w.get("class") or w.get("class_name") or "").lower()
                    if any(k in (title + " " + cls) for k in ("error", "warning", "cảnh báo", "invalid", "not valid")):
                        nearby_error_modals.append(w)
                nearby_error_modals = nearby_error_modals[:5]
            except Exception:
                pass
        except Exception:
            pass

        # V15 R45 — adopt pre-existing folder-picker dialog as fallback.
        # User report: macro fails with NEW_DIALOG_NOT_FOUND when Select Folder
        # dialog was already visible BEFORE the click (e.g. previous run didn't
        # close cleanly, or click re-focused an already-open picker). Probe ALL
        # visible top-level windows (no launched_after filter) for one matching
        # the folder-picker title/class pattern; if exactly one matches, use it.
        try:
            import win32gui as _w32_r45
            _PICKER_TITLE_RX = _re_ch.compile(
                r"^(Select Folder|Browse [Ff]or Folder|Choose Folder|Open Folder|"
                r"Open$|Folder$|Mở thư mục|Chọn thư mục)",
                _re_ch.IGNORECASE,
            )
            existing_picker_candidates: list = []
            def _enum_existing(hwnd, acc):
                try:
                    if not _w32_r45.IsWindowVisible(hwnd):
                        return
                    cls = _w32_r45.GetClassName(hwnd) or ""
                    ttl = _w32_r45.GetWindowText(hwnd) or ""
                    is_dialog = ("#32770" in cls) or ("dialog" in cls.lower())
                    if is_dialog and _PICKER_TITLE_RX.search(ttl):
                        acc.append({"hwnd": hwnd, "title": ttl, "class": cls})
                except Exception:
                    pass
            try:
                _w32_r45.EnumWindows(_enum_existing, existing_picker_candidates)
            except Exception:
                pass
            # Optional pid filter: if owner_pid known, prefer candidates owned by it
            if owner_pid and existing_picker_candidates:
                try:
                    import win32process as _w32p_r45
                    filt = []
                    for c in existing_picker_candidates:
                        try:
                            _, pid = _w32p_r45.GetWindowThreadProcessId(c["hwnd"])
                            c["pid"] = pid
                            if pid == owner_pid:
                                filt.append(c)
                        except Exception:
                            pass
                    if filt:
                        existing_picker_candidates = filt
                except Exception:
                    pass
            # Adopt only when unambiguous (single candidate). Multiple matches
            # indicate genuine ambiguity — keep returning NEW_DIALOG_NOT_FOUND so
            # CA can disambiguate via folder_dialog_snapshot.
            if len(existing_picker_candidates) == 1:
                adopted_existing_dialog = existing_picker_candidates[0]
                new_dialog_hwnd = adopted_existing_dialog["hwnd"]
                new_dialog_meta = adopted_existing_dialog
                result["phases"]["dialog_discovery_adopted_existing"] = {
                    "adopted_hwnd": new_dialog_hwnd,
                    "title": adopted_existing_dialog.get("title", ""),
                    "class": adopted_existing_dialog.get("class", ""),
                    "reason": "Dialog already open before click — accepted as valid picker.",
                }
        except Exception:
            pass

    if not new_dialog_hwnd:
        return _jl.dumps({
            "ok": False,
            "error_name": "NEW_DIALOG_NOT_FOUND",
            "error": f"No new folder dialog appeared after click within {wait_for_dialog_ms}ms",
            "phases": result["phases"],
            "relaxed_snapshot_candidates": relaxed_candidates,  # R33: dialogs found WITHOUT pid filter
            "all_visible_dialogs": all_visible_dialogs,         # R34: top-10 visible windows (full evidence)
            "nearby_error_modals": nearby_error_modals,         # R34: error/warning class windows (e.g. "The folder name is not valid")
            "hint": (
                "MANDATORY first step: call folder_dialog_snapshot() WITHOUT filters to see "
                "ALL candidate dialogs + their owner_pid/parent_hwnd. Do NOT guess pids. "
                "If relaxed_snapshot_candidates is non-empty, the dialog exists but was filtered "
                "out — retry with the correct owner_pid from that list. If empty, the click did "
                "not open a native dialog (custom Electron UI?) — use invoke_app_function instead."
            ),
            "ca_next_action": (
                "use_relaxed_snapshot_candidates_owner_pid"
                if relaxed_candidates else
                "call_folder_dialog_snapshot_no_filter_then_invoke_app_function_if_empty"
            ),
        }, ensure_ascii=False)

    # Phase 5: fill the dialog with discovered constraints
    # V15 R46 — when the dialog was ADOPTED (R45 fallback for already-open
    # picker), it predates baseline_ts; fill_folder_dialog's time filter
    # would re-reject it as "not new" → DIALOG_NOT_FOUND. Pin via exact
    # escaped title + owner_pid and drop the time filter so the same
    # dialog hwnd is re-located deterministically.
    _adopted = adopted_existing_dialog is not None
    _ff_title_raw = (new_dialog_meta.get("title", "") if new_dialog_meta else "")
    if _adopted and _ff_title_raw:
        _ff_title_pattern = "^" + _re_ch.escape(_ff_title_raw) + "$"
    else:
        _ff_title_pattern = _ff_title_raw
    _ff_launched_after = "" if _adopted else baseline_ts
    try:
        ff_raw = await fill_folder_dialog(
            title_pattern=_ff_title_pattern,
            folder_path=folder_path,
            owner_pid=owner_pid or (new_dialog_meta.get("pid", 0) if new_dialog_meta else 0),
            parent_hwnd=parent_hwnd_resolved,
            launched_after_ts=_ff_launched_after,
            wait_for_dialog_ms=200,  # already found
            timeout_ms=timeout_ms,
            try_modern_picker=try_modern_picker,
            # R46: when adopting, exact-title pin already final — skip fuzzy
            # auto patterns so unrelated dialogs (Error/Save/Open) don't match.
            auto_try_common_titles=not _adopted,
        )
        ff = _jl.loads(ff_raw)
        result["phases"]["fill_dialog"] = {
            "ok": ff.get("ok"),
            "picker_route": ff.get("picker_route"),
            "folder_path_set": ff.get("folder_path_set"),
            "button_clicked": ff.get("button_clicked"),
            "dialog_closed": ff.get("dialog_closed"),
            "verdict": ff.get("verdict"),
            "error_name": ff.get("error_name"),
            "adopted_dialog_pin_applied": _adopted,  # R46 audit
        }
    except Exception as exc:
        result["phases"]["fill_dialog_error"] = str(exc)

    fill = result["phases"].get("fill_dialog", {}) or {}
    overall_ok = bool(fill.get("ok"))
    return _jl.dumps({
        "ok": overall_ok,
        "click_selector": click_selector,
        "folder_path": folder_path,
        "discovered_owner_pid": owner_pid,
        "discovered_parent_hwnd": parent_hwnd_resolved,
        "new_dialog_hwnd": new_dialog_hwnd,
        # R46 — surface verdict + adoption flag so CA doesn't have to dig
        # through phases.fill_dialog to interpret the outcome.
        "verdict": fill.get("verdict"),
        "adopted_existing_dialog": (adopted_existing_dialog or None),
        "picker_route": fill.get("picker_route"),
        "phases": result["phases"],
        "ca_next_action": (
            "verify_target_folder_contents_with_folder_stats_after_app_processing"
            if overall_ok else
            "review_phase_failures_or_try_invoke_app_function_fallback"
        ),
    }, ensure_ascii=False)
