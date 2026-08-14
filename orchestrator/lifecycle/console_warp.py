"""Own one hidden console for an [A] spawn tree.

Children inherit that console only when spawned without CREATE_NO_WINDOW,
CREATE_NEW_CONSOLE, or DETACHED_PROCESS. CREATE_NO_WINDOW remains the
fallback when this process has no console.
"""
from __future__ import annotations

import os
import subprocess

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0
ATTACH_PARENT_PROCESS = 0xFFFFFFFF
_INHERIT_STRIP = CREATE_NO_WINDOW | CREATE_NEW_CONSOLE | DETACHED_PROCESS


def has_console() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return False


def ensure_hidden_console() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        if not kernel32.GetConsoleWindow():
            attached = kernel32.AttachConsole(ATTACH_PARENT_PROCESS)
            if not attached and not kernel32.AllocConsole():
                return False
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, SW_HIDE)
            return True
    except Exception:
        return False
    return False


def child_creationflags(extra: int = 0) -> int:
    extra = int(extra or 0)
    if os.name != "nt":
        return extra
    if has_console():
        return extra & ~_INHERIT_STRIP
    flags = extra & ~DETACHED_PROCESS
    return flags | CREATE_NO_WINDOW


def child_startupinfo():
    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= STARTF_USESHOWWINDOW
    info.wShowWindow = SW_HIDE
    return info
