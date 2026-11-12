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
                                try_modern_picker: bool = True,
                                expected_hwnd: int = 0) -> str:
    """V15 R27.1 / R31 T4: control Windows native Select Folder dialog with rich
    owner/PID filtering, ambiguity detection, modern picker fallback, dry-run.

    R31 enhancements (per Round 12 testgf folder dialog audit):
    - `owner_pid`: only match windows owned by this PID (eliminates CapCut/browser false-match)
    - `parent_hwnd`: match dialogs whose parent/owner hwnd is this app window
    - `class_name_pattern`: restrict by class regex (e.g. "#32770|.*Dialog.*")
    - `exclude_titles`: reject windows whose title contains any of these strings
    - `launched_after_ts`: only windows whose process was launched after this ISO timestamp
    - `dry_run`: return candidate list WITHOUT mutating (read-only discovery)
    - `try_modern_picker`: expose an address Edit with Ctrl+L, set and read back its path

    Flow:
    1. Wait up to wait_for_dialog_ms for a visible dialog matching the filters
    2. Reject ambiguous targets
    3. Locate a visible Edit and optional Select/OK button
    4. Set the path and verify the Edit text before Enter or Select
    5. Observe dialog closure; application selection requires separate verification
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
            "ca_next_action": "call_folder_dialog_snapshot_to_inspect",
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
    # T4-over-HTTP 5.2(g) bound re-check (bridge-set expected_hwnd only):
    # immediately before the first mutating step, re-enumerate with this
    # handler's own predicate conjoined with the dialog-class test (and,
    # on the owner_pid form, the ownership test). Unless the candidate
    # set is still exactly the bound hwnd, refuse -- nothing is typed.
    if expected_hwnd:
        _recheck_hwnds = []
        for (_rp, _rreq) in patterns:
            for (_rh, _rt, _rm, _rpat, _rrej) in _collect_candidates(
                    _rp, require_dialog_class=_rreq):
                if _rrej or _rh in _recheck_hwnds:
                    continue
                _rcls = _rm.get("class") or ""
                if not (_rcls == "#32770" or "dialog" in _rcls.lower()):
                    continue
                if owner_pid and not (_rm.get("owner") or 0):
                    continue
                _recheck_hwnds.append(_rh)
        if _recheck_hwnds != [expected_hwnd] or target_hwnd != expected_hwnd:
            return json.dumps({
                "ok": False,
                "error_name": "DIALOG_TARGET_AMBIGUOUS",
                "reason": "dialog_target_changed",
                "expected_hwnd": expected_hwnd,
                "candidates": _recheck_hwnds[:10],
                "ca_next_action": "enumerate_windows_and_pin_a_real_dialog_then_retry",
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
            if "edit" in cls.lower() and win32gui.IsWindowVisible(hwnd) and not edit_hwnd:
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
    from orchestrator.runtime.desktop_input import edit_text, send_chord

    if not folder_path:
        return json.dumps({"ok": False, "error_name": "MISSING_REQUIRED_FIELD", "field": "folder_path"})
    # Windows reports UTF-16 code units; one extra character detects truncation.
    read_capacity = len(folder_path.encode("utf-16-le")) // 2 + 2
    picker_route = "none"
    folder_path_set = False
    # V15 R32: Flow 1 fix — after SETTEXT or paste, MUST commit navigation via Enter
    # before clicking Select Folder. Original R31 code skipped Enter → button clicked
    # on stale dialog state → dialog_closed=false.
    if edit_hwnd:
        try:
            win32gui.SendMessage(edit_hwnd, win32con.WM_SETTEXT, 0, str(folder_path))
            folder_path_set = edit_text(edit_hwnd, read_capacity) == folder_path
            if not folder_path_set:
                raise ValueError("Edit control did not retain the requested path")
            picker_route = "classic_settext_enter_then_select"
            # R32: commit path via Enter on edit (navigates dialog to that folder)
            VK_RETURN = 0x0D
            win32api.PostMessage(edit_hwnd, win32con.WM_KEYDOWN, VK_RETURN, 0)
            win32api.PostMessage(edit_hwnd, win32con.WM_KEYUP, VK_RETURN, 0)
            _t.sleep(0.45)  # wait for navigation to complete
        except Exception as exc:
            return json.dumps({"ok": False,
                                "error_name": "DIALOG_EDIT_SETTEXT_FAILED",
                                "error": str(exc),
                                "hwnd_used": target_hwnd, "matched_title": matched_title})
    elif try_modern_picker:
        # Ctrl+L exposes the address Edit in Explorer-style pickers.
        try:
            win32gui.SetForegroundWindow(target_hwnd)
            send_chord(target_hwnd, win32con.VK_CONTROL, ord("L"))
            _t.sleep(0.2)
            edits = []

            def address_edit(child, _):
                if (win32gui.GetClassName(child).lower() == "edit"
                        and win32gui.IsWindowVisible(child)):
                    edits.append(child)
                return True

            win32gui.EnumChildWindows(target_hwnd, address_edit, None)
            if len(edits) != 1:
                raise RuntimeError("Could not identify a single visible address Edit")
            edit_hwnd = edits[0]
            win32gui.SendMessage(edit_hwnd, win32con.WM_SETTEXT, 0, folder_path)
            folder_path_set = edit_text(edit_hwnd, read_capacity) == folder_path
            if not folder_path_set:
                raise ValueError("Address Edit did not retain the requested folder")
            send_chord(target_hwnd, win32con.VK_RETURN)
            picker_route = "modern_address_edit_enter"
            _t.sleep(0.45)
        except Exception as exc:
            return json.dumps({"ok": False,
                                "error_name": "MODERN_PICKER_KEYBOARD_FAILED",
                                "error": str(exc),
                                "hwnd_used": target_hwnd, "matched_title": matched_title,
                                "hint": "Inspect the pinned dialog controls with desktop_window_list before choosing the next action."})
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
    if folder_path_set and button_hwnd and win32gui.IsWindow(target_hwnd) and win32gui.IsWindowVisible(target_hwnd):
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
            dialog_closed = False
            break
        _t.sleep(0.2)
    fallback_hint = ""
    fallback_recipe = ""
    if not folder_path_set:
        fallback_hint = "Inspect the dialog controls with desktop_window_list before retrying."
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
    if folder_path_set and dialog_closed:
        verdict = "DIALOG_CLOSED_PATH_ENTERED"
    elif folder_path_set and button_clicked and nearby_error_modals:
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
            {"tool": "desktop_window_list", "args_hint": {"include_child_controls": True},
             "use_case": "Inspect the current dialog and its controls"},
            {"tool": "cancel_native_dialog",
             "args_hint": {"owner_pid": owner_pid, "parent_hwnd": parent_hwnd,
                           "title_pattern": matched_title},
             "use_case": "Dismiss the pinned dialog if the operation is abandoned"},
        ]
    return json.dumps({
        "ok": bool(folder_path_set and dialog_closed),
        "selection_verified": False,
        "verdict": verdict,
        "verdict_enum": [
            "DIALOG_CLOSED_PATH_ENTERED",
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
