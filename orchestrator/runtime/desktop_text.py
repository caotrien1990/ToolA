"""Native-Edit readback and conservative clipboard ownership for desktop paste."""
import ctypes
import time
from ctypes import wintypes

from orchestrator.runtime.desktop_input import edit_text, send_chord

MAX_TEXT_UNITS = 1_048_576
TEXT_FORMATS = frozenset({1, 7, 13, 16})  # Windows text/synthesized text formats


class ClipboardFailure(RuntimeError):
    def __init__(self, code, reason):
        super().__init__(reason)
        self.code = code


def _formats(clip):
    formats, current = [], 0
    for _ in range(256):
        current = clip.EnumClipboardFormats(current)
        if not current:
            return formats
        if current in formats:
            raise RuntimeError("Clipboard format enumeration repeated an entry")
        formats.append(current)
    raise RuntimeError("Clipboard format enumeration exceeded its bound")


class ClipboardLease:
    """Text-only preservation; unknown formats fail before clipboard destruction."""
    def __init__(self, clip, text):
        self.clip, self.text = clip, text
        self.prior = None
        self.prior_formats = []
        self.sequence = None
        self.report = {"status": "not_started", "scope": "text_only", "attempts": 1}

    def _restore_prior(self):
        self.clip.EmptyClipboard()
        if self.prior_formats:
            self.clip.SetClipboardData(13, self.prior)

    def install(self):
        self.report["status"] = "snapshot_started"
        try:
            self.clip.OpenClipboard()
            try:
                self.prior_formats = _formats(self.clip)
                if set(self.prior_formats) - TEXT_FORMATS:
                    self.report.update(status="unsupported_format", prior_formats=self.prior_formats)
                    raise ClipboardFailure("CLIPBOARD_UNSUPPORTED_FORMAT",
                                           "Unsupported prior formats; clipboard left unchanged")
                if self.prior_formats:
                    self.prior = self.clip.GetClipboardData(13)
                    if not isinstance(self.prior, str):
                        raise RuntimeError("Clipboard text snapshot unavailable")
                # Check guard capability BEFORE the destructive operation.
                self.clip.GetClipboardSequenceNumber()
                self.report["status"] = "install_started"
                try:
                    self.clip.EmptyClipboard()
                    self.clip.SetClipboardData(13, self.text)
                    self.sequence = self.clip.GetClipboardSequenceNumber()
                except Exception:
                    try:
                        self._restore_prior()
                        self.report["status"] = "install_failed_prior_restored"
                    except Exception:
                        self.report["status"] = "install_failed_restore_failed"
                    raise
                self.report.update(status="installed", prior_formats=self.prior_formats)
            finally:
                self.clip.CloseClipboard()
        except ClipboardFailure:
            raise
        except Exception as exc:
            if self.report["status"] == "snapshot_started":
                self.report["status"] = "snapshot_failed"
            raise ClipboardFailure("CLIPBOARD_SNAPSHOT_OR_INSTALL_FAILED",
                                   "Clipboard snapshot/install failed; paste aborted") from exc

    def restore(self):
        try:
            self.clip.OpenClipboard()
            try:
                if (self.clip.GetClipboardSequenceNumber() != self.sequence
                        or set(_formats(self.clip)) - TEXT_FORMATS
                        or self.clip.GetClipboardData(13) != self.text):
                    self.report["status"] = "user_changed_not_restored"
                    return
                self._restore_prior()
                self.report["status"] = "restored"
            finally:
                self.clip.CloseClipboard()
        except Exception:
            self.report.update(status="restore_failed", reason="clipboard_restore_api_failed")


def focused_control(hwnd):
    """Query the target thread's focus; never guess among descendant Edit controls."""
    import win32gui
    import win32process

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                    ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                    ("rcCaret", wintypes.RECT)]

    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(info)
    query = ctypes.windll.user32.GetGUIThreadInfo
    query.argtypes = (wintypes.DWORD, ctypes.POINTER(GUITHREADINFO))
    query.restype = wintypes.BOOL
    tid, _ = win32process.GetWindowThreadProcessId(hwnd)
    if not query(tid, ctypes.byref(info)):
        raise RuntimeError("Focused control unavailable")
    child = int(info.hwndFocus or 0)
    if not child or (child != hwnd and not win32gui.IsChild(hwnd, child)):
        raise RuntimeError("Focused control is outside target window")
    return child


def edit_length(hwnd):
    send = ctypes.windll.user32.SendMessageTimeoutW
    send.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                     wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
    send.restype = wintypes.LPARAM
    result = ctypes.c_size_t()
    if not send(hwnd, 0x000E, 0, 0, 2, 500, ctypes.byref(result)):
        raise RuntimeError("Edit length unavailable")
    return result.value


def _units(text):
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def _normal(text):
    return text.replace("\r\n", "\n")


def verify_text(hwnd, text, clear_first, original_control):
    import win32gui

    base = {"status": "not_verified", "method": "WM_GETTEXT"}
    if win32gui.GetForegroundWindow() != hwnd:
        return dict(base, reason="foreground_changed")
    try:
        child = focused_control(hwnd)
        if original_control and child != original_control:
            return dict(base, reason="focused_control_changed")
        if not original_control:
            return dict(base, reason="original_focused_control_unavailable")
        if win32gui.GetClassName(child).lower() != "edit":
            return dict(base, reason="no_native_edit")
        # ES_PASSWORD must be checked before WM_GETTEXTLENGTH and WM_GETTEXT.
        if win32gui.GetWindowLong(child, -16) & 0x20:
            return dict(base, reason="password_control")
        capacity = max(edit_length(child), _units(text)) + 2
        for _ in range(3):
            if capacity > MAX_TEXT_UNITS:
                return dict(base, reason="capacity_limit")
            value = edit_text(child, capacity)
            count = _units(value)
            if count < capacity - 1:
                break
            capacity = max(capacity * 2, edit_length(child) + 2)
        else:
            return dict(base, reason="truncated_or_changing_text")
        if win32gui.GetForegroundWindow() != hwnd:
            return dict(base, reason="foreground_changed")
        if focused_control(hwnd) != child:
            return dict(base, reason="focused_control_changed")
        actual, expected = _normal(value), _normal(text)
        matches = actual == expected if clear_first else expected in actual
        return dict(base, status="verified" if matches else "mismatch",
                    reason="exact_match" if matches and clear_first else
                           "substring_match" if matches else "text_mismatch",
                    observed_utf16_units=count, expected_utf16_units=_units(text))
    except Exception:
        return dict(base, reason="native_read_unavailable")


def paste_with_readback(hwnd, text, *, clear_first, paste_shortcut, submit):
    import win32clipboard
    import win32con

    lease = ClipboardLease(win32clipboard, text)
    outcome = {"ok": True, "steps": [], "delivery_verified": False,
               "text_verification": {"status": "not_verified", "reason": "paste_not_completed"}}
    try:
        try:
            original = focused_control(hwnd)
        except Exception:
            original = None
        lease.install()
        try:
            time.sleep(0.05)
            if clear_first:
                send_chord(hwnd, win32con.VK_CONTROL, ord("A"))
                outcome["steps"].append("ctrl_a")
            if paste_shortcut == "ctrl_v":
                send_chord(hwnd, win32con.VK_CONTROL, ord("V"))
                outcome["steps"].append("ctrl_v")
            else:
                send_chord(hwnd, win32con.VK_SHIFT, win32con.VK_INSERT)
                outcome["steps"].append("shift_insert")
            time.sleep(0.05)
            verification = verify_text(hwnd, text, clear_first, original)
            outcome["text_verification"] = verification
            if verification["status"] == "mismatch" or verification["reason"] in {
                    "foreground_changed", "focused_control_changed"}:
                outcome.update(ok=False, error_name="TEXT_VERIFICATION_FAILED",
                               error=verification["reason"])
        finally:
            lease.restore()
        if lease.report["status"] == "restore_failed":
            outcome.update(ok=False, error_name="CLIPBOARD_RESTORE_FAILED",
                           error="Paste finished but clipboard restoration failed")
        if outcome["ok"] and submit:
            time.sleep(0.10)
            send_chord(hwnd, win32con.VK_RETURN)
            outcome["steps"].append("enter")
    except ClipboardFailure as exc:
        outcome.update(ok=False, error_name=exc.code, error=str(exc))
    except Exception:
        outcome.update(ok=False, error_name="KEYBOARD_DISPATCH_FAILED",
                       error="Keyboard dispatch failed; no automatic retry")
    outcome["clipboard"] = lease.report
    return outcome
