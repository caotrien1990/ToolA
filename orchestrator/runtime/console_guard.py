"""Console-session lock checks for bridged desktop capture/input."""
import ctypes
from contextvars import ContextVar
from ctypes import wintypes

CAPTURE_GUARD = ContextVar("t4_capture_guard", default=False)
BLOCKED_TOOLS = frozenset({
    "desktop_screenshot", "desktop_click", "desktop_send_text",
    "desktop_app_focus", "desktop_window_state",
    "fill_folder_dialog", "cancel_native_dialog",
})
UNAFFECTED_TOOLS = (
    "open_browser_page", "cdp_attach_target", "list_cdp_targets",
    "read_page", "query_selector", "query_dom_candidates", "click", "fill",
    "fill_value", "read_value", "evaluate_js", "wait_for_selector",
    "read_console_errors", "read_network_errors", "screenshot_page",
    "compare_dom_snapshot", "describe_page", "close_browser_page",
)
# All Step-0 captures (one): grayscale 64x64 variance 1900.4124603271484.
# Synthetic black/white/solid RGB: 0,0,0. Midpoint fixed before implementation.
BLANK_VARIANCE_THRESHOLD = 950.2062301635742


def active_console_session():
    query = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId
    query.argtypes = ()
    query.restype = wintypes.DWORD
    session = query()
    if session == 0xFFFFFFFF:
        raise RuntimeError("No active console session")
    return int(session)


def process_session(pid):
    query = ctypes.windll.kernel32.ProcessIdToSessionId
    query.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    query.restype = wintypes.BOOL
    session = wintypes.DWORD()
    if not query(pid, ctypes.byref(session)):
        raise RuntimeError("Process session unavailable")
    return session.value


def console_state():
    state = {"explorer_present": None, "logonui_present": None,
             "console_locked": None, "console_session_id": None,
             "detector_scope": "active_console_session"}
    try:
        import psutil
        session = active_console_session()
        state["console_session_id"] = session
        names = set()
        uncertain = False
        for proc in psutil.process_iter(["pid", "name"]):
            name = (proc.info.get("name") or "").lower()
            if name not in {"explorer.exe", "logonui.exe"}:
                continue
            try:
                if process_session(proc.info["pid"]) == session:
                    names.add(name)
            except Exception:
                if name == "logonui.exe":
                    uncertain = True
        state["explorer_present"] = "explorer.exe" in names
        if "logonui.exe" in names:
            state.update(logonui_present=True, console_locked=True)
        elif uncertain:
            state["detector_error"] = "logonui_session_unavailable"
        else:
            state.update(logonui_present=False, console_locked=False)
    except Exception:
        state["detector_error"] = "console_state_unavailable"
    return state


def refusal(state, reason="console_locked"):
    locked = state.get("console_locked")
    if locked is False:
        return None
    return {"ok": False,
            "error_name": "T4_SCREEN_LOCKED" if locked is True else "T4_SCREEN_STATE_UNKNOWN",
            "error": "Desktop capture/input refused",
            "reason": reason if locked is True else "console_state_unavailable",
            "blocked_subset": sorted(BLOCKED_TOOLS), "unaffected": list(UNAFFECTED_TOOLS),
            "console_state": state}


def frame_assessment(image):
    from PIL import ImageStat
    variance = ImageStat.Stat(image.convert("L").resize((64, 64))).var[0]
    return {"uniform_frame": variance <= BLANK_VARIANCE_THRESHOLD,
            "frame_variance": variance, "blank_variance_threshold": BLANK_VARIANCE_THRESHOLD}
