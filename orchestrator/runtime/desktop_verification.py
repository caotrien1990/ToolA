"""Bounded Win32 observations; these are not proofs of application effects."""
import ctypes
import time
from ctypes import wintypes

OBSERVATION_SECONDS = 1.0
SAMPLE_SECONDS = 0.1


def skipped_checks(route, reason):
    return {"route": route, "bounds": {"status": "skipped", "reason": reason},
            "modal": {"status": "skipped", "reason": reason}}


def click_checks(hwnd, x, y):
    import win32api
    import win32gui

    checks = skipped_checks("win32_client", "client_to_screen_unavailable")
    try:
        point = win32gui.ClientToScreen(hwnd, (x, y))
    except Exception:
        return checks
    checks["screen_point"] = list(point)
    try:
        left, top, width, height = [win32api.GetSystemMetrics(i) for i in (76, 77, 78, 79)]
        if width <= 0 or height <= 0:
            raise ValueError("Invalid virtual desktop dimensions")
        inside = left <= point[0] < left + width and top <= point[1] < top + height
        checks["bounds"] = {"status": "passed" if inside else "out_of_bounds",
                            "rect": [left, top, left + width, top + height],
                            "source": "GetSystemMetrics_current_call"}
    except Exception:
        checks["bounds"] = {"status": "skipped", "reason": "virtual_geometry_unavailable"}
    if checks["bounds"]["status"] == "out_of_bounds":
        checks["modal"] = {"status": "skipped", "reason": "bounds_refused"}
        return checks
    try:
        at_point = win32gui.WindowFromPoint(point)
        point_root = win32gui.GetAncestor(at_point, 2) if at_point else 0
        target_root = win32gui.GetAncestor(hwnd, 2)
        root = target_root or hwnd
        if win32gui.IsWindowEnabled(root):
            checks["modal"] = {"status": "passed", "modal_disabled": False}
        else:
            popup = win32gui.GetWindow(root, 6)  # GW_ENABLEDPOPUP
            modal_observed = bool(popup and popup != root)
            checks["modal"] = {"status": "warning", "modal_disabled": True if modal_observed else None,
                               "disabled_root": root, "point_root": point_root,
                               "dialog_hwnd": popup if popup != root else 0,
                               "dialog_title": win32gui.GetWindowText(popup) if popup and popup != root else "",
                               "reason": "target_disabled_by_modal" if modal_observed else "target_disabled_without_popup",
                               "dispatch_policy": "continue"}
    except Exception:
        checks["modal"] = {"status": "skipped", "reason": "modal_check_unavailable"}
    return checks


def is_zoomed(hwnd):
    query = ctypes.windll.user32.IsZoomed
    query.argtypes = (wintypes.HWND,)
    query.restype = wintypes.BOOL
    return bool(query(hwnd))


def window_sample(hwnd):
    import win32gui
    sample = {"timestamp": time.time(), "monotonic": time.monotonic()}
    try:
        if not win32gui.IsWindow(hwnd):
            return dict(sample, exists=False, reason="target_disappeared")
        rect = win32gui.GetWindowRect(hwnd)
        return dict(sample, exists=True, rect={"x": rect[0], "y": rect[1],
                    "w": rect[2] - rect[0], "h": rect[3] - rect[1]},
                    minimized=bool(win32gui.IsIconic(hwnd)),
                    visible=bool(win32gui.IsWindowVisible(hwnd)),
                    maximized=is_zoomed(hwnd))
    except Exception:
        return dict(sample, exists=None, reason="window_sample_unavailable")


def matches_request(sample, state, before):
    if sample.get("exists") is not True:
        return False
    iconic, visible, zoomed = sample["minimized"], sample["visible"], sample["maximized"]
    return {
        "minimize": iconic,
        "maximize": visible and not iconic and zoomed,
        "restore": visible and not iconic and (before.get("minimized") or not zoomed),
        "normal": visible and not iconic and not zoomed,
        "hide": not visible,
        "show": visible,
    }[state]


def observe_window_change(hwnd, state, sw_const):
    import win32gui
    before = window_sample(hwnd)
    if before.get("exists") is not True:
        return {"ok": False, "error_name": "WINDOW_OBSERVATION_FAILED",
                "error": before["reason"], "observation": {"before": before}}
    win32gui.ShowWindow(hwnd, sw_const)
    if state == "maximize":
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass  # Preserve existing maximize behavior, not part of the readback.
    immediate = window_sample(hwnd)
    samples = [immediate]
    deadline = immediate["monotonic"] + OBSERVATION_SECONDS
    while time.monotonic() < deadline and samples[-1].get("exists") is True:
        time.sleep(min(SAMPLE_SECONDS, max(0, deadline - time.monotonic())))
        samples.append(window_sample(hwnd))
    delayed = samples[-1]
    matches = [matches_request(s, state, before) for s in samples]
    transitions = []
    keys = ("exists", "minimized", "visible", "maximized")
    for previous, current, prev_match, curr_match in zip(samples, samples[1:], matches, matches[1:]):
        if any(previous.get(k) != current.get(k) for k in keys):
            transitions.append({"timestamp": current["timestamp"],
                                "from": {k: previous.get(k) for k in keys},
                                "to": {k: current.get(k) for k in keys},
                                "opposes_request": (previous.get("exists") is True
                                                     and current.get("exists") is True
                                                     and prev_match and not curr_match),
                                "actor": "unknown"})
    observation = {
        "before": before, "immediate": immediate, "delayed": delayed,
        "samples": samples, "transitions": transitions, "actor": "unknown",
        "delay_seconds": OBSERVATION_SECONDS,
        "elapsed_seconds": delayed["monotonic"] - immediate["monotonic"],
        "immediate_matches_request": matches[0],
        "delayed_matches_request": matches[-1],
        "held_through_observation": all(matches) and delayed["monotonic"] >= deadline,
        "bounced": any(t["opposes_request"] for t in transitions),
        "sampling_note": "Only sampled states; changes between samples or after the interval are unobserved",
    }
    result = {"ok": True, "method": "win32_show_window", "observation": observation,
              "rect_before": before["rect"], "rect_after": immediate.get("rect"),
              "rect_changed": before["rect"] != immediate.get("rect"),
              "was_minimized": before["minimized"], "was_visible": before["visible"],
              "prev_state": "minimized" if before["minimized"] else "hidden" if not before["visible"] else "shown",
              "is_visible_after": immediate.get("visible"), "is_minimized_after": immediate.get("minimized")}
    if delayed.get("exists") is not True:
        result.update(ok=False, error_name="WINDOW_OBSERVATION_FAILED", error=delayed["reason"])
    return result
