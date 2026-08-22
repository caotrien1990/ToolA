"""B028 regression test: PowerShell here-string PROSE must not inflate the
destructive count.

Reported by Anh 2026-08-22, reproduced. A commit message written inside
``@'...'@`` (single-quoted here-string -- PowerShell does not even expand
variables in it, it is pure data) had two of its SENTENCES classified as
commands:

    line 8  "... wildcard, khong Remove-Item -Recurse tren thu muc):"  -> remove_item
    line 30 "... git rm --cached -> 580/583 VAN NGUYEN TREN DIA ..."   -> git_rm

The practical consequence is the part that matters: `confirm_destructive` is a
number the caller must reproduce, and it is supposed to mean "destructive
statements I reviewed". Prose inflated it, so the more honestly a CA documented a
deletion the higher the number climbed -- and editing a WORD of the message moved
the count and invalidated an acknowledgement that was correct when written. A
guard whose number means something else than it says is a correctness defect, not
a nuisance.

What this fix deliberately does NOT do
--------------------------------------
The report asked to "skip the contents of @'...'@ before scanning". That would
reopen a hole the scanner exists to close: `_DESTRUCTIVE_EMBEDDED_SHELL` is there
because a destructive command handed to an interpreter as a STRING is still a
deletion --

    powershell -Command @'
    Remove-Item -Recurse -Force $target
    '@

-- and blanket-skipping here-strings would wave exactly that through. So a
here-string is treated as data ONLY when the script contains no way to evaluate a
string as code. If any eval route is present (`iex`, `-Command`, `cmd /c`,
`node -e`, `subprocess`, `&`, `Start-Process`, ...) here-strings are scanned as
executable exactly as before. Prose findings are always REPORTED, never dropped
silently -- they move to `prose_matches`, out of `count`.

Footguns get the same treatment for a sharper reason: a footgun is NOT
confirmable (DESTRUCTIVE_SCRIPT_FOOTGUN, retryable=False). A sentence that merely
mentions -Include and -LiteralPath could therefore make a script permanently
unrunnable with no confirm path at all.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import code_tools  # noqa: E402


SCAN = code_tools._scan_destructive_script


class ProseInHereStringTest(unittest.TestCase):
    def test_anhs_exact_reproduction_scores_zero(self):
        """The reported body, reduced to its two offending sentences."""
        body = (
            "git commit -F- <<NOPE\n"
            "$msg = @'\n"
            "B027: whitelist only (khong wildcard, khong Remove-Item -Recurse tren thu muc):\n"
            "git rm --cached -> 580/583 VAN NGUYEN TREN DIA (3 file da doi ten)\n"
            "'@\n"
            "git commit -m $msg\n"
        )
        scan = SCAN(body, "powershell")
        self.assertEqual(scan["count"], 0, scan["findings"])
        self.assertFalse(scan["destructive"])
        # Reported, not dropped.
        self.assertEqual(len(scan["prose_matches"]), 2, scan["prose_matches"])
        kinds = {match["kind"] for match in scan["prose_matches"]}
        self.assertEqual(kinds, {"remove_item", "git_rm"})

    def test_a_real_command_outside_the_here_string_still_counts(self):
        """The guard must keep its teeth for the line that actually deletes."""
        body = (
            "$msg = @'\n"
            "this message mentions Remove-Item in prose\n"
            "'@\n"
            "Remove-Item -LiteralPath $tmp -Recurse -Force\n"
        )
        scan = SCAN(body, "powershell")
        self.assertEqual(scan["count"], 1, scan["findings"])
        self.assertEqual(scan["findings"][0]["line"], 4)
        self.assertEqual(len(scan["prose_matches"]), 1)

    def test_a_double_quoted_here_string_is_also_data(self):
        body = '$msg = @"\nplan: rm -rf the staging copy afterwards\n"@\ngit commit -m $msg\n'
        scan = SCAN(body, "powershell")
        self.assertEqual(scan["count"], 0, scan["findings"])
        self.assertEqual(len(scan["prose_matches"]), 1)

    def test_editing_the_prose_does_not_move_the_count(self):
        """The defect in one assertion: the number must depend on commands only."""
        template = (
            "$msg = @'\n{body}\n'@\n"
            "Remove-Item -Path $tmp\\* -Recurse\n"
        )
        first = SCAN(template.format(body="deleted one file"), "powershell")
        second = SCAN(
            template.format(body="deleted one file with Remove-Item and git rm --cached"),
            "powershell")
        self.assertEqual(first["count"], second["count"], "prose must not move the count")
        self.assertEqual(first["count"], 1)


class TeethArePreservedTest(unittest.TestCase):
    """Each of these is a way to execute a string. A here-string in this company
    is a script, not prose, and must be scanned exactly as before B028."""

    HEREDOC = "@'\nRemove-Item -Recurse -Force $target\n'@\n"

    def test_invoke_expression_makes_a_here_string_executable(self):
        scan = SCAN("$s = " + self.HEREDOC + "iex $s\n", "powershell")
        self.assertGreaterEqual(scan["count"], 1, scan)

    def test_powershell_dash_command_makes_it_executable(self):
        scan = SCAN("powershell -Command " + self.HEREDOC, "powershell")
        self.assertGreaterEqual(scan["count"], 1, scan)

    def test_cmd_slash_c_makes_it_executable(self):
        scan = SCAN("$s = " + self.HEREDOC + "cmd /c $s\n", "powershell")
        self.assertGreaterEqual(scan["count"], 1, scan)

    def test_node_dash_e_makes_it_executable(self):
        scan = SCAN("$s = " + self.HEREDOC + "node -e $s\n", "powershell")
        self.assertGreaterEqual(scan["count"], 1, scan)

    def test_the_call_operator_makes_it_executable(self):
        scan = SCAN("$s = " + self.HEREDOC + "& $s\n", "powershell")
        self.assertGreaterEqual(scan["count"], 1, scan)

    def test_start_process_makes_it_executable(self):
        scan = SCAN("$s = " + self.HEREDOC + "Start-Process powershell $s\n", "powershell")
        self.assertGreaterEqual(scan["count"], 1, scan)

    def test_a_scriptblock_create_makes_it_executable(self):
        scan = SCAN("$s = " + self.HEREDOC + "[scriptblock]::Create($s)\n", "powershell")
        self.assertGreaterEqual(scan["count"], 1, scan)


class FootgunProseTest(unittest.TestCase):
    """A footgun is refused with retryable=False and no confirm path, so a false
    positive on prose makes a script permanently unrunnable."""

    def test_prose_describing_the_case_2008_shape_is_not_a_footgun(self):
        body = (
            "$msg = @'\n"
            "CASE-2008 was Remove-Item -LiteralPath $d -Include *.tmp -Recurse: "
            "PowerShell ignored the filter and removed the whole tree.\n"
            "'@\n"
            "git commit -m $msg\n"
        )
        scan = SCAN(body, "powershell")
        self.assertEqual(scan["footguns"], [], scan["footguns"])

    def test_the_real_footgun_shape_is_still_refused(self):
        body = "Remove-Item -LiteralPath $d -Include *.tmp -Recurse\n"
        scan = SCAN(body, "powershell")
        self.assertEqual(len(scan["footguns"]), 1, scan)
        self.assertEqual(scan["footguns"][0]["kind"], "include_ignored_with_literalpath")

    def test_a_footgun_inside_an_executable_here_string_is_still_refused(self):
        body = ("powershell -Command @'\n"
                "Remove-Item -LiteralPath $d -Include *.tmp -Recurse\n"
                "'@\n")
        scan = SCAN(body, "powershell")
        self.assertEqual(len(scan["footguns"]), 1, scan)


class SpanParsingTest(unittest.TestCase):
    def test_an_unterminated_here_string_is_treated_as_executable(self):
        """Fail SAFE. If the body is malformed we cannot know where data ends, and
        guessing 'it is all prose' is the direction that costs files."""
        body = "$s = @'\nRemove-Item -Recurse $d\n"
        scan = SCAN(body, "powershell")
        self.assertGreaterEqual(scan["count"], 1, scan)

    def test_the_opener_must_end_the_line_like_real_powershell(self):
        """`@'` only opens a here-string when it ENDS the line. `$a = @'x'` is an
        ordinary string, so a command on the next line is a command.

        The body below is built so the assertion can only pass for the right
        reason: there IS a `'@` line, so the unterminated fail-safe cannot carry
        the test. Mutating the opener regex to match mid-line makes the walk see a
        here-string spanning line 2 and excuse the deletion -- measured, that
        mutation left every other test in this file green.
        """
        body = "$a = @'x'\nRemove-Item -Recurse $d\n'@\n"
        scan = SCAN(body, "powershell")
        self.assertEqual(scan["count"], 1, scan["findings"])
        self.assertEqual(scan["findings"][0]["line"], 2)
        self.assertEqual(scan["prose_matches"], [], scan["prose_matches"])

    def test_the_terminator_must_start_the_line(self):
        """An indented `'@` does not close a here-string in PowerShell 5.1 -- it is
        a parse error there, so treating it as a close would let real commands
        after it be read as data."""
        body = "$s = @'\nprose\n    '@\nRemove-Item -Recurse $d\n"
        scan = SCAN(body, "powershell")
        self.assertGreaterEqual(scan["count"], 1, scan)

    def test_two_here_strings_in_one_body(self):
        body = ("$a = @'\nmentions Remove-Item\n'@\n"
                "$b = @'\nmentions git rm\n'@\n"
                "Remove-Item -Path $t\\* -Recurse\n")
        scan = SCAN(body, "powershell")
        self.assertEqual(scan["count"], 1, scan["findings"])
        self.assertEqual(len(scan["prose_matches"]), 2)

    def test_non_powershell_runtimes_are_untouched(self):
        """Here-strings are PowerShell syntax. A node or python body must scan
        exactly as before -- `@'` there is not a data delimiter."""
        body = "// @'\nfs.rmSync(dir)\n// '@\n"
        scan = SCAN(body, "node")
        self.assertGreaterEqual(scan["count"], 1, scan)

    def test_runtime_any_keeps_the_here_string_rule(self):
        """run_project_command passes runtime='any' and reaches powershell, so the
        prose rule has to apply there or Anh's exact case still fails on the tool
        he actually used."""
        body = "$msg = @'\nmentions Remove-Item in prose\n'@\ngit commit -m $msg\n"
        scan = SCAN(body, "any")
        self.assertEqual(scan["count"], 0, scan["findings"])


class BackwardCompatTest(unittest.TestCase):
    def test_a_body_with_no_here_string_is_bit_identical(self):
        for body, runtime, expected in [
            ("Remove-Item -Recurse -Force $d\n", "powershell", 1),
            ("git clean -fdx\n", "powershell", 1),
            ("fs.rmSync(dir, {recursive: true})\n", "node", 1),
            ("shutil.rmtree(d)\n", "python", 1),
            ("Write-Host 'hello'\n", "powershell", 0),
        ]:
            with self.subTest(body=body.strip()):
                scan = SCAN(body, runtime)
                self.assertEqual(scan["count"], expected, scan["findings"])
                self.assertEqual(scan["prose_matches"], [])

    def test_prose_matches_key_always_exists(self):
        """Callers read the dict directly; a key that appears only sometimes is how
        a KeyError reaches production."""
        for runtime in ["powershell", "node", "python", "sh", "any"]:
            with self.subTest(runtime=runtime):
                self.assertIn("prose_matches", SCAN("", runtime))


if __name__ == "__main__":
    unittest.main()
