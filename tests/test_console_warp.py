"""console_warp: headless keystone + inherit vs CREATE_NO_WINDOW fallback.

WT-flicker fix 2026-11-25: ensure_hidden_console must NEVER AllocConsole or
AttachConsole (a windowed console is rendered by Windows Terminal even when
hidden). It only hides an already-present console window and reports presence.
No live AllocConsole anywhere in this suite.
"""
from __future__ import annotations

import unittest
from unittest import mock

from orchestrator.lifecycle import console_warp


class TestConsoleWarp(unittest.TestCase):
    def test_child_creationflags_fallback_without_console(self):
        with mock.patch.object(console_warp, "has_console", return_value=False):
            self.assertEqual(
                console_warp.child_creationflags(0),
                console_warp.CREATE_NO_WINDOW,
            )

    def test_child_creationflags_inherits_and_strips_hide_bits(self):
        extra = console_warp.CREATE_NO_WINDOW | 0x200
        with mock.patch.object(console_warp, "has_console", return_value=True):
            self.assertEqual(console_warp.child_creationflags(extra), 0x200)

    def test_child_creationflags_strips_detached_when_inheriting(self):
        with mock.patch.object(console_warp, "has_console", return_value=True):
            self.assertEqual(
                console_warp.child_creationflags(console_warp.DETACHED_PROCESS),
                0,
            )

    def test_child_creationflags_never_ors_detached_with_no_window(self):
        with mock.patch.object(console_warp, "has_console", return_value=False):
            flags = console_warp.child_creationflags(console_warp.DETACHED_PROCESS)
            self.assertEqual(flags, console_warp.CREATE_NO_WINDOW)
            self.assertFalse(flags & console_warp.DETACHED_PROCESS)

    def test_module_has_create_no_window_constant(self):
        self.assertEqual(console_warp.CREATE_NO_WINDOW, 0x08000000)
        self.assertEqual(console_warp.SW_HIDE, 0)

    # headless keystone: ensure_hidden_console never allocates a console
    def _ensure(self, kernel32, user32, **kwargs):
        fake_ctypes = mock.Mock(windll=mock.Mock(kernel32=kernel32, user32=user32))
        with mock.patch.dict("sys.modules", {"ctypes": fake_ctypes}):
            with mock.patch.object(console_warp.os, "name", "nt"):
                return console_warp.ensure_hidden_console(**kwargs)

    def test_ensure_no_console_never_allocates_or_attaches(self):
        kernel32 = mock.Mock()
        user32 = mock.Mock()
        kernel32.GetConsoleWindow.return_value = 0
        ok = self._ensure(kernel32, user32)
        self.assertFalse(ok)
        kernel32.AllocConsole.assert_not_called()
        kernel32.AttachConsole.assert_not_called()
        kernel32.FreeConsole.assert_not_called()
        user32.ShowWindow.assert_not_called()

    def test_ensure_hides_inherited_console_without_allocating(self):
        kernel32 = mock.Mock()
        user32 = mock.Mock()
        kernel32.GetConsoleWindow.return_value = 123
        ok = self._ensure(kernel32, user32)
        self.assertTrue(ok)
        user32.ShowWindow.assert_called_once_with(123, console_warp.SW_HIDE)
        kernel32.AllocConsole.assert_not_called()
        kernel32.AttachConsole.assert_not_called()

    def test_ensure_attach_parent_true_still_never_attaches(self):
        kernel32 = mock.Mock()
        user32 = mock.Mock()
        kernel32.GetConsoleWindow.return_value = 0
        ok = self._ensure(kernel32, user32, attach_parent=True)
        self.assertFalse(ok)
        kernel32.AttachConsole.assert_not_called()
        kernel32.AllocConsole.assert_not_called()

    def test_ensure_attach_parent_false_no_console_headless(self):
        kernel32 = mock.Mock()
        user32 = mock.Mock()
        kernel32.GetConsoleWindow.return_value = 0
        ok = self._ensure(kernel32, user32, attach_parent=False)
        self.assertFalse(ok)
        kernel32.AllocConsole.assert_not_called()
        kernel32.FreeConsole.assert_not_called()

    def test_ensure_non_windows_returns_false(self):
        with mock.patch.object(console_warp.os, "name", "posix"):
            self.assertFalse(console_warp.ensure_hidden_console())

    def test_ensure_exception_returns_false(self):
        kernel32 = mock.Mock()
        user32 = mock.Mock()
        kernel32.GetConsoleWindow.side_effect = OSError("boom")
        ok = self._ensure(kernel32, user32)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
