"""Headless console policy for an [A] spawn tree (WT-flicker fix 2026-11-25).

The daemon and its lifecycle helpers run console-less (pythonw). Every child
is spawned with CREATE_NO_WINDOW via child_creationflags()'s no-console
fallback, which creates no console window at all. A windowed console -- even
one immediately hidden with SW_HIDE -- is handed to the default terminal
(Windows Terminal on this build), which renders a tab and can steal focus;
that was the flicker under load. So no console is ever allocated here.

Children inherit a console only when one already exists AND they are spawned
without CREATE_NO_WINDOW / CREATE_NEW_CONSOLE / DETACHED_PROCESS; otherwise
CREATE_NO_WINDOW is the fallback.
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


def ensure_hidden_console(attach_parent: bool = True) -> bool:
    """Headless keystone: never allocate or attach a console.

    AllocConsole / AttachConsole create a WINDOWED console that the default
    terminal (Windows Terminal) renders even after ShowWindow(SW_HIDE) -- the
    flicker + focus-steal Anh reported. The fix is to stay console-less and
    let child_creationflags() give every child CREATE_NO_WINDOW (no console
    window, nothing for WT to render).

    If this process already owns a console window (inherited from a console
    parent), hide it as a courtesy. Returns whether a console is present so
    callers can log the state; the return is NOT a gate for child spawning.

    attach_parent is kept for call-site compatibility but unused: attaching
    the parent's console would re-import a windowed console.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, SW_HIDE)
            return True
        return False
    except Exception:
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
