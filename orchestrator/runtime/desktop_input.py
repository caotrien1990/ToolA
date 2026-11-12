"""Win32 keyboard and edit-control operations used by the desktop handlers."""

import ctypes
import time
from ctypes import wintypes


def send_chord(hwnd, *keys):
    """Dispatch one chord, releasing pressed keys even if focus moves mid-chord."""
    import win32api
    import win32con
    import win32gui

    pressed = []
    try:
        for key in keys:
            if win32gui.GetForegroundWindow() != hwnd:
                raise RuntimeError("Target lost foreground before keyboard dispatch")
            scan = win32api.MapVirtualKey(key, 0)
            if not scan:
                raise ValueError(f"No scan code for virtual key {key}")
            flags = win32con.KEYEVENTF_EXTENDEDKEY if key == win32con.VK_INSERT else 0
            win32api.keybd_event(key, scan, flags, 0)
            pressed.append((key, scan, flags))
            time.sleep(0.045)
    finally:
        for key, scan, flags in reversed(pressed):
            win32api.keybd_event(key, scan, flags | win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.045)


def edit_text(hwnd, capacity):
    """Read text from a foreign Edit control with a bounded wait."""
    send = ctypes.windll.user32.SendMessageTimeoutW
    send.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                     wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
    send.restype = wintypes.LPARAM
    buf = ctypes.create_unicode_buffer(capacity)
    copied = ctypes.c_size_t()
    if not send(hwnd, 0x000D, capacity, ctypes.addressof(buf), 2, 500, ctypes.byref(copied)):
        raise RuntimeError("Could not read the target Edit control")
    return buf.value
