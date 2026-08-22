# -*- coding: utf-8 -*-
"""B028c: `Set-Content` / `Out-File` counted as an eval route regardless of target.

Reported by Anh's verification of B028. `_STRING_EVAL_ROUTES` (`code_tools.py:7168`)
holds ONE alternation with two halves that disagree:

    r"(?i)\\bSet-Content\\b|\\bOut-File\\b|>>?\\s*['\\\"]?[^\\r\\n]*\\.(?:ps1|cmd|bat|sh|js|py)\\b"
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^   any target at all      ^^^^ only script extensions

The `>` redirect half asks *where the string lands* -- writing prose to `msg.txt` is
not an eval route, writing it to `run.ps1` is. `Set-Content` / `Out-File` never asked,
so ANY use of them anywhere in the body cancelled the whole here-string-is-data
analysis and the prose came back as destructive. Measured before the fix, same prose
block, only the last line differing:

    (no write)                    -> destructive_count 0   correct
    [IO.File]::WriteAllText(...)  -> 0                     correct
    $msg > m.txt                  -> 0                     correct (extension checked)
    $msg > run.ps1                -> 2                     correct (extension checked)
    $msg | Out-File msg.txt       -> 2                     WRONG
    Set-Content msg.txt $msg      -> 2                     WRONG

The consequence is not cosmetic: `git commit -F msg.txt` written the obvious way is
unrunnable, which is why the commit message had to go through
`[IO.File]::WriteAllText`. The fix gives all three branches the same question, and
the script-extension cases stay caught -- `Out-File run.ps1` IS the slow eval route
the comment describes.

FAIL SAFE stays the rule: a target this parser cannot read (a bare variable, a
computed path, a `-Path` whose value is not a literal) counts as an eval route,
because guessing "that is probably a .txt" is the direction that costs files.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import code_tools as ct  # noqa: E402

PROSE = """$msg = @'
B028 fix
khong Remove-Item -Recurse tren thu muc
rd /s /q cung khong
'@
"""


def _count(tail: str) -> int:
    scan = ct._scan_destructive_script(PROSE + tail, "powershell")
    return int(scan.get("destructive_count") or scan.get("count") or 0)


class DataTargetsAreNotEvalRoutesTest(unittest.TestCase):
    """A prose here-string written to a data file stays prose."""

    def test_out_file_to_a_text_target(self):
        self.assertEqual(_count("$msg | Out-File msg.txt\n"), 0)

    def test_set_content_to_a_text_target(self):
        self.assertEqual(_count("Set-Content msg.txt $msg\n"), 0)

    def test_out_file_with_an_explicit_path_parameter(self):
        self.assertEqual(_count("$msg | Out-File -FilePath msg.txt -Encoding utf8\n"), 0)

    def test_set_content_with_an_explicit_path_parameter(self):
        self.assertEqual(_count("Set-Content -Path msg.txt -Value $msg\n"), 0)

    def test_a_quoted_text_target(self):
        self.assertEqual(_count("$msg | Out-File 'commit msg.txt'\n"), 0)

    def test_the_three_branches_now_agree(self):
        """`>`, Out-File and Set-Content to the SAME target must give the same answer."""
        self.assertEqual(_count("$msg > msg.txt\n"), 0)
        self.assertEqual(_count("$msg | Out-File msg.txt\n"), 0)
        self.assertEqual(_count("Set-Content msg.txt $msg\n"), 0)


class ScriptTargetsAreStillEvalRoutesTest(unittest.TestCase):
    """The teeth: writing the string to something runnable IS the slow eval route."""

    def test_out_file_to_a_ps1(self):
        self.assertGreater(_count("$msg | Out-File run.ps1\n"), 0)

    def test_set_content_to_a_ps1(self):
        self.assertGreater(_count("Set-Content run.ps1 $msg\n"), 0)

    def test_set_content_path_parameter_to_a_ps1(self):
        self.assertGreater(_count("Set-Content -Path run.ps1 -Value $msg\n"), 0)

    def test_redirect_to_a_ps1_still_caught(self):
        self.assertGreater(_count("$msg > run.ps1\n"), 0)

    def test_every_script_extension_is_covered(self):
        for ext in ("ps1", "cmd", "bat", "sh", "js", "py"):
            with self.subTest(ext=ext):
                self.assertGreater(_count(f"$msg | Out-File run.{ext}\n"), 0,
                                   f".{ext} must stay an eval route")


class AnUnreadableTargetFailsSafeTest(unittest.TestCase):
    """Cannot tell where it lands -> treat it as executable. Guessing costs files."""

    def test_a_variable_target(self):
        self.assertGreater(_count("$msg | Out-File $dest\n"), 0)

    def test_a_computed_target(self):
        self.assertGreater(_count("Set-Content (Join-Path $d 'x') $msg\n"), 0)

    def test_no_target_at_all(self):
        self.assertGreater(_count("$msg | Out-File\n"), 0)


class TheRealDeletionIsStillCaughtTest(unittest.TestCase):
    """B028c must not become 'here-strings are always data'."""

    def test_an_eval_route_still_scans_the_here_string(self):
        body = """$s = @'
Remove-Item -Recurse -Force $t
'@
$s | Out-File msg.txt
Invoke-Expression $s
"""
        self.assertGreater(_count_body(body), 0)

    def test_a_here_string_piped_into_powershell_command(self):
        body = """$s = @'
Remove-Item -Recurse -Force $t
'@
$s | Out-File msg.txt
powershell -Command $s
"""
        self.assertGreater(_count_body(body), 0)

    def test_a_plain_deletion_outside_any_here_string(self):
        self.assertGreater(_count_body("Remove-Item -Recurse -Force $t\n"), 0)


def _count_body(body: str) -> int:
    scan = ct._scan_destructive_script(body, "powershell")
    return int(scan.get("destructive_count") or scan.get("count") or 0)


if __name__ == "__main__":
    unittest.main()
