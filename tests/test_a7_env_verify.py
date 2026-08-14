"""A7 env-gate verification (P1.14b): CBM_EXTRACT_JS parse contract + indexer wiring.

Validates that the env-gated JS/TS opt-in (orchestrator.graph.config.CBM_EXTRACT_JS)
parses 1/true/yes/on as enabled and 0/false/no/off/unset as disabled, and that when
enabled the JS extractor module exists and indexer.DEFAULT_EXTS widens to include
.js. Self-contained: no live-server dependency. Style mirrors test_p6_js_extractor.py
(env isolation + unittest + subprocess for import-time env flags).
"""
import importlib
import os
import sys
import unittest
import subprocess
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import config as _config

_PROJ_ROOT = str(Path(__file__).resolve().parents[1])
_JS_EXTRACTOR = Path(_PROJ_ROOT) / "orchestrator" / "graph" / "extractors" / "javascript.py"

_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off", "", "  ", "11", "True2", "y", "enable")


class A7EnvVerifySuite(unittest.TestCase):
    """P1.14b - CBM_EXTRACT_JS env-gate parse + indexer DEFAULT_EXTS wiring."""

    def setUp(self):
        self._saved = os.environ.get("CBM_EXTRACT_JS")

    def tearDown(self):
        with mock.patch.dict(os.environ):
            if self._saved is None:
                os.environ.pop("CBM_EXTRACT_JS", None)
            else:
                os.environ["CBM_EXTRACT_JS"] = self._saved
            importlib.reload(_config)

    def _flag_with(self, value):
        """Reload config with CBM_EXTRACT_JS=value and return the parsed flag."""
        with mock.patch.dict(os.environ):
            if value is None:
                os.environ.pop("CBM_EXTRACT_JS", None)
            else:
                os.environ["CBM_EXTRACT_JS"] = value
            importlib.reload(_config)
            return _config.CBM_EXTRACT_JS

    def test_truthy_values_parse_enabled(self):
        for v in _TRUTHY:
            self.assertTrue(self._flag_with(v), f"CBM_EXTRACT_JS={v!r} should parse True")

    def test_falsy_values_parse_disabled(self):
        for v in _FALSY:
            self.assertFalse(self._flag_with(v), f"CBM_EXTRACT_JS={v!r} should parse False")

    def test_unset_falls_back_to_code_default_off(self):
        self.assertFalse(self._flag_with(None),
                         "unset CBM_EXTRACT_JS must default to False (code default '0')")

    def test_javascript_extractor_module_exists(self):
        self.assertTrue(_JS_EXTRACTOR.is_file(), f"JS extractor missing: {_JS_EXTRACTOR}")

    def test_indexer_default_exts_widens_when_flag_on(self):
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys, os; sys.path.insert(0, '.'); "
             "os.environ['CBM_EXTRACT_JS'] = '1'; "
             "from orchestrator.graph import indexer; "
             "print(repr(indexer.DEFAULT_EXTS))"],
            capture_output=True, text=True, cwd=_PROJ_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("'.js'", proc.stdout,
                      f"DEFAULT_EXTS should include .js when flag on; got {proc.stdout!r}")

    def test_indexer_default_exts_python_only_when_flag_off(self):
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys, os; sys.path.insert(0, '.'); "
             "os.environ['CBM_EXTRACT_JS'] = '0'; "
             "from orchestrator.graph import indexer; "
             "print(repr(indexer.DEFAULT_EXTS))"],
            capture_output=True, text=True, cwd=_PROJ_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "('.py',)")


if __name__ == "__main__":
    unittest.main()
