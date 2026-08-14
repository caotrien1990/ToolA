"""console_warp: inherit vs CREATE_NO_WINDOW fallback. No live AllocConsole."""
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

    def test_ensure_hidden_console_shows_sw_hide(self):
        kernel32 = mock.Mock()
        user32 = mock.Mock()
        kernel32.GetConsoleWindow.side_effect = [0, 123]
        kernel32.AttachConsole.return_value = 0
        kernel32.AllocConsole.return_value = 1
        user32.ShowWindow.return_value = 1
        fake_ctypes = mock.Mock(windll=mock.Mock(kernel32=kernel32, user32=user32))
        with mock.patch.dict("sys.modules", {"ctypes": fake_ctypes}):
            with mock.patch.object(console_warp.os, "name", "nt"):
                ok = console_warp.ensure_hidden_console()
        self.assertTrue(ok)
        user32.ShowWindow.assert_called_once_with(123, console_warp.SW_HIDE)
        kernel32.AllocConsole.assert_called_once()

    def test_module_has_create_no_window_constant(self):
        self.assertEqual(console_warp.CREATE_NO_WINDOW, 0x08000000)
        self.assertEqual(console_warp.SW_HIDE, 0)


if __name__ == "__main__":
    unittest.main()
