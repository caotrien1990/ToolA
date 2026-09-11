"""Desktop dialog runtime helpers extracted from code_tools."""
from __future__ import annotations

import json


async def fill_folder_dialog(title_pattern: str = "", folder_path: str = "",
                               timeout_ms: int = 5000,
                               wait_for_dialog_ms: int = 3000,
                               auto_try_common_titles: bool = True,
                               electron_fallback_hint: dict | None = None,
                               owner_pid: int = 0,
                               parent_hwnd: int = 0,
                               class_name_pattern: str = "",
                               exclude_titles: list | None = None,
                               launched_after_ts: str = "",
                               dry_run: bool = False,
                               try_modern_picker: bool = True) -> str:
    """V15 R27.1 / R31 T4: control Windows native Select Folder dialog with rich
    owner/PID filtering, ambiguity detection, modern picker fallback, dry-run.

    R31 enhancements (per Round 12 testgf folder dialog audit):
    - `owner_pid`: only match windows owned by this PID (eliminates CapCut/browser false-match)
    - `parent_hwnd`: match dialogs whose parent/owner hwnd is this app window
    - `class_name_pattern`: restrict by class regex (e.g. "#32770|.*Dialog.*")
    - `exclude_titles`: reject windows whose title contains any of these strings
    - `launched_after_ts`: only windows whose process was launched after this ISO timestamp
    - `dry_run`: return candidate list WITHOUT mutating (read-only discovery)
    - `try_modern_picker`: fallback for IFileDialog/UIAutomation when no Edit control
      (keyboard route: Ctrl+L → paste path → Enter)

    Flow:
    1. Wait up to wait_for_dialog_ms for visible dialog matching filters
    2. If multiple match → DIALOG_AMBIGUOUS with candidates (no auto-pick when global)
    3. If single match → walk children for Edit + Select/OK button
    4. SetWindowText + click → verify closed
    5. If no Edit + try_modern_picker: keyboard route (focus → paste → Enter)
    6. On failure: rich diagnostic with visible_windows + rejected_windows

    Returns: {ok, hwnd_used, matched_title, candidates (when ambiguous),
              rejected_windows, folder_path_set, button_clicked, dialog_closed,
              picker_route ("classic"|"modern_keyboard"|"none"),
              ca_next_action, electron_fallback_recipe}.
    """
    try:
        import win32gui, win32con, win32api
    except Exception:
        return json.dumps({"ok": False, "error": "pywin32 missing",
                            "error_name": "PYWIN32_MISSING",
                            "remediation": "pip install pywin32"})
    import re as _re_d
    import time as _t

    # V15 R33: Windows folder picker REJECTS forward slashes (`The folder name is not valid.`).
    # Normalize early so CA never has to remember; also collapse mixed separators.
    folder_path_original = folder_path
    folder_path_normalized = False
    if folder_path:
        if "/" in folder_path:
            folder_path = folder_path.replace("/", "\\")
            folder_path_normalized = True
        # collapse `\\` runs (but preserve UNC `\\server\` prefix)
        if folder_path.startswith("\\\\"):
            head, tail = folder_path[:2], folder_path[2:]
            while "\\\\" in tail:
                tail = tail.replace("\\\\", "\\")
            folder_path = head + tail
        else:
            while "\\\\" in folder_path:
                folder_path = folder_path.replace("\\\\", "\\")

    # R27.1: build list of patterns to try. Anchored regex to avoid matching
    # unrelated windows (e.g. "Open" in browser title would false-match).
    # Patterns are tuples: (regex_pattern, require_dialog_class)
    patterns: list = [(title_pattern, False)]
    if auto_try_common_titles:
        anchored_common = [
            (r"^Select Folder", True),
            (r"^Browse [Ff]or Folder", True),
            (r"^Choose Folder", True),
            (r"^Open Folder", True),
            (r"^Open$", True),  # exact "Open" only
            (r"^Mở thư mục", True),
            (r"^Chọn thư mục", True),
            (r"^Folder$", True),
        ]
        for pat, req_dlg in anchored_common:
            if pat.lower() != title_pattern.lower():
                patterns.append((pat, req_dlg))
    tried_patterns = []

    def _is_dialog_class(hwnd) -> bool:
        try:
            cls = win32gui.GetClassName(hwnd) or ""
            return cls == "#32770" or "dialog" in cls.lower()
        except Exception:
            return False

    # R31: PID + process creation time for launched_after_ts filter
    try:
        import win32process
        import psutil as _ps
    except Exception:
        _ps = None
        win32process = None

    _exclude = [s.lower() for s in (exclude_titles or [])]
    class_re = _re_d.compile(class_name_pattern, _re_d.IGNORECASE) if class_name_pattern else None
    launched_after_dt = None
    if launched_after_ts:
        try:
            from datetime import datetime as _dt
            launched_after_dt = _dt.fromisoformat(launched_after_ts)
        except Exception:
            pass

    def _window_meta(hwnd) -> dict:
        meta = {"hwnd": hwnd, "title": "", "class": "", "pid": 0,
                "parent": 0, "owner": 0, "process_name": "", "create_time": None}
        try:
            meta["title"] = win32gui.GetWindowText(hwnd) or ""
            meta["class"] = win32gui.GetClassName(hwnd) or ""
            if win32process:
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    meta["pid"] = pid
                    if _ps:
                        try:
                            proc = _ps.Process(pid)
                            meta["process_name"] = proc.name()
                            meta["create_time"] = proc.create_time()
                        except Exception:
                            pass
                except Exception:
                    pass
            try:
                meta["parent"] = win32gui.GetParent(hwnd) or 0
                meta["owner"] = win32gui.GetWindow(hwnd, 4) or 0
            except Exception:
                pass
        except Exception:
            pass
        return meta

    def _passes_filters(meta: dict) -> tuple[bool, str]:
        """Return (passes, reject_reason)."""
        t_low = (meta.get("title") or "").lower()
        if owner_pid and meta.get("pid") != owner_pid:
            return False, f"owner_pid_mismatch(want={owner_pid},got={meta.get('pid')})"
        if parent_hwnd and meta.get("parent") != parent_hwnd and meta.get("owner") != parent_hwnd:
            return False, f"parent_hwnd_mismatch(want={parent_hwnd})"
        if class_re and not class_re.search(meta.get("class") or ""):
            return False, f"class_name_pattern_mismatch(got={meta.get('class')})"
        for ex in _exclude:
            if ex and ex in t_low:
                return False, f"excluded_title_substr({ex})"
        if launched_after_dt and meta.get("create_time"):
            from datetime import datetime as _dt
            ct = _dt.fromtimestamp(meta["create_time"])
            if ct < launched_after_dt:
                return False, "process_predates_launched_after_ts"
        return True, ""

    def _collect_candidates(pat: str, require_dialog_class: bool):
        """Return list of (hwnd, title, meta, pattern, reject_reason)."""
        results = []
        try_pat = _re_d.compile(pat, _re_d.IGNORECASE)
        def _proc(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                t = win32gui.GetWindowText(hwnd) or ""
                if not t or not try_pat.search(t):
                    return True
                if require_dialog_class and not _is_dialog_class(hwnd):
                    return True
                meta = _window_meta(hwnd)
                passes, reason = _passes_filters(meta)
                results.append((hwnd, t, meta, pat, reason if not passes else ""))
            except Exception:
                pass
            return True
        try:
            win32gui.EnumWindows(_proc, None)
        except Exception:
            pass
        return results

    # R31: wait-for-dialog poll. Collect ALL candidates across patterns; apply filters.
    accepted = []
    rejected = []
    deadline_wait = _t.time() + max(0.0, wait_for_dialog_ms / 1000.0)
    while True:
        accepted.clear()
        rejected.clear()
        for (p, require_dlg) in patterns:
            if p not in tried_patterns:
                tried_patterns.append(p)
            for (hwnd, t, meta, pat_used, reject_reason) in _collect_candidates(p, require_dialog_class=require_dlg):
                # Deduplicate by hwnd across patterns
                if any(a["hwnd"] == hwnd for a in accepted) or any(r["hwnd"] == hwnd for r in rejected):
                    continue
                entry = {**meta, "matched_via_pattern": pat_used}
                if reject_reason:
                    entry["reject_reason"] = reject_reason
                    rejected.append(entry)
                else:
                    accepted.append(entry)
        if accepted or _t.time() >= deadline_wait:
            break
        _t.sleep(0.2)

    target_hwnd = 0
    matched_title = ""
    matched_via_pattern = ""

    # R31: ambiguity check — if multiple accepted, fail loud with candidates
    if len(accepted) > 1 and not (owner_pid or parent_hwnd):
        return json.dumps({
            "ok": False,
            "error_name": "DIALOG_AMBIGUOUS",
            "error": f"Multiple visible windows matched title patterns. Pass owner_pid or parent_hwnd to disambiguate.",
            "candidates": accepted[:10],
            "rejected_windows": rejected[:10],
            "tried_patterns": tried_patterns,
            "ca_next_action": "pass_owner_pid_or_parent_hwnd_to_disambiguate",
            "hint": "Use folder_dialog_snapshot(owner_pid=<your_app_pid>) to see candidates first, then call fill_folder_dialog with explicit filter.",
        }, ensure_ascii=False)

    if accepted:
        # Prefer dialog-class match if mixed
        chosen = next((c for c in accepted if c.get("class") == "#32770"), accepted[0])
        target_hwnd = chosen["hwnd"]
        matched_title = chosen["title"]
        matched_via_pattern = chosen["matched_via_pattern"]

    if not target_hwnd:
        # R31: enumerate visible windows (include rejected for CA diagnostic)
        visible_windows = []
        def _enum_all(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                t = win32gui.GetWindowText(hwnd) or ""
                if not t:
                    return True
                m = _window_meta(hwnd)
                visible_windows.append({
                    "hwnd": hwnd, "title": (m.get("title") or "")[:100],
                    "class": m.get("class"), "pid": m.get("pid"),
                    "process_name": m.get("process_name"),
                })
            except Exception:
                pass
            return True
        try:
            win32gui.EnumWindows(_enum_all, None)
        except Exception:
            pass
        fallback_recipe = ""
        if isinstance(electron_fallback_hint, dict):
            pid = electron_fallback_hint.get("page_id", "")
            handler = electron_fallback_hint.get("suggested_handler", "")
            if pid and handler:
                fallback_recipe = (
                    f"invoke_app_function(page_id='{pid}', "
                    f"function_name='{handler}', args=['{folder_path}'])"
                )
        return json.dumps({
            "ok": False,
            "error_name": "DIALOG_NOT_FOUND",
            "error": f"No visible window matched filters across patterns: {tried_patterns}",
            "tried_patterns": tried_patterns,
            "visible_windows": visible_windows[:30],
            "rejected_windows": rejected[:10],  # R31: surface filtered-out candidates
            "filters_applied": {
                "owner_pid": owner_pid, "parent_hwnd": parent_hwnd,
                "class_name_pattern": class_name_pattern,
                "exclude_titles": exclude_titles or [],
                "launched_after_ts": launched_after_ts,
            },
            "ca_next_action": (
                "use_invoke_app_function_for_electron_custom_dialog"
                if fallback_recipe else
                "call_folder_dialog_snapshot_to_inspect_or_use_invoke_app_function"
            ),
            "electron_fallback_recipe": fallback_recipe,
            "auto_try_common_titles": auto_try_common_titles,
            "wait_for_dialog_ms_elapsed": int(wait_for_dialog_ms),
        }, ensure_ascii=False)

    # R31: dry_run — return candidate metadata without mutating
    if dry_run:
        return json.dumps({
            "ok": True,
            "dry_run": True,
            "matched_hwnd": target_hwnd,
            "matched_title": matched_title,
            "matched_via_pattern": matched_via_pattern,
            "candidates_count": len(accepted),
            "candidates": accepted[:5],
            "rejected_windows": rejected[:5],
            "ca_next_action": "rerun_without_dry_run_when_satisfied",
        }, ensure_ascii=False)

    # Found! Now locate edit + button inside.
    edit_hwnd = 0
    button_hwnd = 0
    button_label = ""
    BUTTON_RE = _re_d.compile(r"^(Select Folder|OK|Open|Choose|Mở|Chọn|Save)", _re_d.IGNORECASE)

    def _walk(hwnd):
        nonlocal edit_hwnd, button_hwnd, button_label
        try:
            cls = win32gui.GetClassName(hwnd) or ""
            txt = win32gui.GetWindowText(hwnd) or ""
            if "edit" in cls.lower() and not edit_hwnd:
                edit_hwnd = hwnd
            elif "button" in cls.lower() and BUTTON_RE.search(txt) and not button_hwnd:
                button_hwnd = hwnd
                button_label = txt
        except Exception:
            pass
        return True

    def _enum_child(hwnd, _):
        _walk(hwnd)
        # Recurse one level deep
        try:
            win32gui.EnumChildWindows(hwnd, lambda c, _2: _walk(c) or True, None)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(target_hwnd, _enum_child, None)
    except Exception:
        pass
    picker_route = "none"
    folder_path_set = False
    # V15 R32: Flow 1 fix — after SETTEXT or paste, MUST commit navigation via Enter
    # before clicking Select Folder. Original R31 code skipped Enter → button clicked
    # on stale dialog state → dialog_closed=false.
    if edit_hwnd:
        try:
            win32gui.SendMessage(edit_hwnd, win32con.WM_SETTEXT, 0, str(folder_path))
            folder_path_set = True
            picker_route = "classic_settext_enter_then_select"
            # R32: commit path via Enter on edit (navigates dialog to that folder)
            VK_RETURN = 0x0D
            try:
                win32api.PostMessage(edit_hwnd, win32con.WM_KEYDOWN, VK_RETURN, 0)
                win32api.PostMessage(edit_hwnd, win32con.WM_KEYUP, VK_RETURN, 0)
            except Exception:
                pass
            _t.sleep(0.45)  # wait for navigation to complete
        except Exception as exc:
            return json.dumps({"ok": False,
                                "error_name": "DIALOG_EDIT_SETTEXT_FAILED",
                                "error": str(exc),
                                "hwnd_used": target_hwnd, "matched_title": matched_title})
    elif try_modern_picker:
        # R31: modern IFileDialog/Vista-style picker has no exposed Edit child.
        # Fallback: focus dialog → send Ctrl+L (address bar) → paste path → Enter.
        try:
            # Focus + foreground
            try:
                win32gui.SetForegroundWindow(target_hwnd)
            except Exception:
                pass
            _t.sleep(0.15)
            # Copy folder_path to clipboard
            try:
                import ctypes
                CF_UNICODETEXT = 13
                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32
                user32.OpenClipboard(0)
                user32.EmptyClipboard()
                text = str(folder_path)
                size = (len(text) + 1) * 2
                hMem = kernel32.GlobalAlloc(0x0042, size)  # GMEM_MOVEABLE|GMEM_ZEROINIT
                if hMem:
                    pMem = kernel32.GlobalLock(hMem)
                    if pMem:
                        ctypes.memmove(pMem, ctypes.c_wchar_p(text), size)
                        kernel32.GlobalUnlock(hMem)
                        user32.SetClipboardData(CF_UNICODETEXT, hMem)
                user32.CloseClipboard()
            except Exception:
                pass
            # Send Ctrl+L to focus address bar / filename field
            VK_CONTROL, VK_L, VK_V, VK_RETURN = 0x11, 0x4C, 0x56, 0x0D
            KEYEVENTF_KEYUP = 0x0002
            import ctypes as _ct
            user32 = _ct.windll.user32
            user32.keybd_event(VK_CONTROL, 0, 0, 0)
            user32.keybd_event(VK_L, 0, 0, 0)
            user32.keybd_event(VK_L, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            _t.sleep(0.2)
            # Ctrl+V to paste
            user32.keybd_event(VK_CONTROL, 0, 0, 0)
            user32.keybd_event(VK_V, 0, 0, 0)
            user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            _t.sleep(0.2)
            # Enter
            user32.keybd_event(VK_RETURN, 0, 0, 0)
            user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
            folder_path_set = True
            picker_route = "modern_keyboard_ctrl_l_paste_enter"
            _t.sleep(0.45)  # R32: wait for address bar navigation to commit
        except Exception as exc:
            return json.dumps({"ok": False,
                                "error_name": "MODERN_PICKER_KEYBOARD_FAILED",
                                "error": str(exc),
                                "hwnd_used": target_hwnd, "matched_title": matched_title,
                                "hint": "Modern picker fallback (Ctrl+L → paste → Enter) failed. App may use custom non-IFileDialog UI — use invoke_app_function for Electron apps."})
    # 3. R32: ALWAYS click Select/OK button after navigation commit.
    # Previously modern route auto-set button_clicked=true, but address bar Enter
    # only NAVIGATES — it doesn't trigger "select folder". Need explicit button click.
    # Re-walk children — dialog state may have updated post-navigation.
    if folder_path_set and not button_hwnd:
        button_hwnd_2 = 0
        button_label_2 = ""
        def _walk2(hwnd):
            nonlocal button_hwnd_2, button_label_2
            try:
                cls = win32gui.GetClassName(hwnd) or ""
                txt = win32gui.GetWindowText(hwnd) or ""
                if "button" in cls.lower() and BUTTON_RE.search(txt) and not button_hwnd_2:
                    button_hwnd_2 = hwnd
                    button_label_2 = txt
            except Exception:
                pass
            return True
        def _enum_child2(hwnd, _):
            _walk2(hwnd)
            try:
                win32gui.EnumChildWindows(hwnd, lambda c, _2: _walk2(c) or True, None)
            except Exception:
                pass
            return True
        try:
            win32gui.EnumChildWindows(target_hwnd, _enum_child2, None)
        except Exception:
            pass
        button_hwnd = button_hwnd_2
        button_label = button_label_2
    button_clicked = False
    if button_hwnd:
        try:
            win32api.PostMessage(button_hwnd, win32con.BM_CLICK, 0, 0)
            button_clicked = True
            _t.sleep(0.2)
        except Exception:
            pass
    # R32: if button click didn't close dialog, fall back to sending Enter
    # directly to the dialog window (BS_DEFPUSHBUTTON handler) as last resort.
    if button_clicked and win32gui.IsWindow(target_hwnd) and win32gui.IsWindowVisible(target_hwnd):
        try:
            VK_RETURN = 0x0D
            win32api.PostMessage(target_hwnd, win32con.WM_KEYDOWN, VK_RETURN, 0)
            win32api.PostMessage(target_hwnd, win32con.WM_KEYUP, VK_RETURN, 0)
        except Exception:
            pass
    # 4. Wait for dialog close
    dialog_closed = False
    deadline = _t.time() + max(0.5, timeout_ms / 1000.0)
    while _t.time() < deadline:
        try:
            if not win32gui.IsWindow(target_hwnd) or not win32gui.IsWindowVisible(target_hwnd):
                dialog_closed = True
                break
        except Exception:
            dialog_closed = True
            break
        _t.sleep(0.2)
    fallback_hint = ""
    fallback_recipe = ""
    if not (folder_path_set and button_clicked):
        fallback_hint = (
            "Native dialog control partial. For Electron custom dialog, try invoke_app_function() "
            "with the IPC handler directly (e.g. runGetFootageToTarget(target))."
        )
        if isinstance(electron_fallback_hint, dict):
            pid = electron_fallback_hint.get("page_id", "")
            handler = electron_fallback_hint.get("suggested_handler", "")
            if pid and handler:
                fallback_recipe = (
                    f"invoke_app_function(page_id='{pid}', "
                    f"function_name='{handler}', args=['{folder_path}'])"
                )
    # V15 R34: compute explicit verdict so CA doesn't have to parse 4 fields
    verdict = "PARTIAL"
    nearby_error_modals: list = []
    try:
        # rescan top-level windows for Error-class dialogs (best-effort)
        def _enum_err(hwnd, acc):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                cls = win32gui.GetClassName(hwnd) or ""
                ttl = win32gui.GetWindowText(hwnd) or ""
                if "#32770" in cls or "dialog" in cls.lower():
                    low = (ttl + " " + cls).lower()
                    if any(k in low for k in ("error", "warning", "cảnh báo", "invalid", "not valid")):
                        acc.append({"hwnd": hwnd, "title": ttl, "class": cls})
            except Exception:
                pass
        try:
            win32gui.EnumWindows(_enum_err, nearby_error_modals)
        except Exception:
            pass
    except Exception:
        pass
    # folder_exists post-check (best effort)
    folder_path_exists_post = False
    try:
        import os as _os_fe
        folder_path_exists_post = bool(folder_path) and _os_fe.path.isdir(folder_path)
    except Exception:
        pass
    # V15 R45 — verdict logic prioritises dialog_closed as the strongest signal
    # of OS-level acceptance. folder_path_exists_post is advisory: it can give
    # FALSE negatives for paths with Vietnamese diacritics or transient UNC
    # latency, so it must NOT downgrade a successfully-closed dialog to
    # DIALOG_PATH_INVALID. Only a CONCURRENT error modal (e.g. "The folder
    # name is not valid.") is treated as authoritative invalid evidence.
    if folder_path_set and button_clicked and dialog_closed:
        if folder_path_exists_post:
            verdict = "DIALOG_SELECTED_FOLDER_CONFIRMED"
        else:
            # Dialog closed via the Select/OK button but post-check could not
            # confirm filesystem existence. Treat as success with caveat — CA
            # can run folder_stats(path) over [A]'s Unicode-safe path to verify.
            verdict = "DIALOG_CLOSED_POST_CHECK_INCONCLUSIVE"
    elif folder_path_set and button_clicked and not dialog_closed and nearby_error_modals:
        verdict = "DIALOG_PATH_INVALID"
    elif folder_path_set and button_clicked and not dialog_closed:
        verdict = "DIALOG_BUTTON_CLICK_NO_CLOSE"
    elif not folder_path_set:
        verdict = "PARTIAL"
    # V15 R42: when verdict=PARTIAL, surface CA fallback options inline.
    # User reported: "đã test xong folder_dialog, cho phép CA fallback tool khác."
    partial_recovery_options = None
    if verdict == "PARTIAL":
        partial_recovery_options = [
            {"tool": "fill_folder_dialog",
             "retry_with": {"auto_try_common_titles": True, "wait_for_dialog_ms": 5000,
                              "title_pattern": matched_title or ""},
             "use_case": "Retry with broader title match + longer wait"},
            {"tool": "click_and_handle_folder_dialog",
             "use_case": "Atomic click+fill macro — recommended if you have click_selector"},
            {"tool": "invoke_app_function",
             "function_name_hint": "pickFolder|setTargetFolder|onFolderChosen|chooseFolder",
             "use_case": "App-side handler — bypass native picker entirely if app exposes JS function"},
            {"tool": "desktop_click",
             "args_hint": {"hwnd": button_hwnd or "<from button_hwnd_found>", "force_win32": True},
             "use_case": "Force Win32 BM_CLICK on Select Folder button by hwnd"},
            {"tool": "cancel_native_dialog",
             "use_case": "Give up + close dialog cleanly; retry whole flow"},
        ]
    return json.dumps({
        # R45: ok stays true when dialog closed via button click, regardless of
        # post-check (Vietnamese/UNC false-negatives no longer downgrade verdict).
        "ok": (folder_path_set and button_clicked and dialog_closed),
        "verdict": verdict,  # R34/R45: DIALOG_SELECTED_FOLDER_CONFIRMED | DIALOG_CLOSED_POST_CHECK_INCONCLUSIVE | DIALOG_PATH_INVALID | DIALOG_BUTTON_CLICK_NO_CLOSE | PARTIAL
        "verdict_enum": [
            "DIALOG_SELECTED_FOLDER_CONFIRMED",
            "DIALOG_CLOSED_POST_CHECK_INCONCLUSIVE",   # R45 — dialog OK, FS check failed (Vietnamese/UNC latency)
            "DIALOG_PATH_INVALID", "DIALOG_BUTTON_CLICK_NO_CLOSE",
            "DIALOG_NOT_FOUND", "DIALOG_AMBIGUOUS", "PARTIAL",
        ],
        "partial_recovery_options": partial_recovery_options,  # R42: CA fallback chain
        "hwnd_used": target_hwnd,
        "matched_title": matched_title,
        "matched_via_pattern": matched_via_pattern,
        "tried_patterns": tried_patterns,
        "edit_hwnd_found": bool(edit_hwnd),
        "button_hwnd_found": bool(button_hwnd),
        "button_label": button_label,
        "folder_path_set": folder_path_set,
        "button_clicked": button_clicked,
        "dialog_closed": dialog_closed,
        "folder_path_exists_post": folder_path_exists_post,  # R34
        "nearby_error_modals": nearby_error_modals[:5],       # R34
        "picker_route": picker_route,  # R31: classic_win32_setwindowtext | modern_keyboard_ctrl_l_paste_enter | none
        "folder_path_normalized": folder_path_normalized,  # R33: True when "/" → "\" applied
        "folder_path_original": folder_path_original,      # R33: what CA passed in
        "folder_path_used": folder_path,                   # R33: normalized form sent to dialog
        "rejected_windows": rejected[:5],
        "fallback_hint": fallback_hint,
        "electron_fallback_recipe": fallback_recipe,
    }, ensure_ascii=False)
