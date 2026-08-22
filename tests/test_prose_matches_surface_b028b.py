# -*- coding: utf-8 -*-
"""B028 follow-up: `prose_matches` must reach the CALLER, not just the scanner.

Found by smoke-testing the live daemon after the restart, and it is my own defect
from the first cut of B028. The scan dict gained `prose_matches` and a warning that
says, in so many words, "(see prose_matches)" -- but not one of the six sites that
flatten a scan into a payload copied the key. So the live answer told a caller to
consult a field that did not exist in the response.

That is the same class of bug as B026's unregistered `max_file_bytes` and B-01
before it: **a field nobody can reach is not a field.** It is worse here than a
missing escape hatch, because the warning actively names it -- a caller following
the instruction finds nothing and has to guess whether the guard silently dropped
two real destructive statements or correctly classified two sentences.

The count is the number that must not move, and it already does not (that is
`test_editing_the_prose_does_not_move_the_count`). This file pins the other half:
what was excluded has to be VISIBLE, on the payloads a caller actually receives.
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import code_tools  # noqa: E402


PROSE_BODY = (
    "$msg = @'\n"
    "B027: whitelist only (khong wildcard, khong Remove-Item -Recurse tren thu muc):\n"
    "git rm --cached -> 580/583 VAN NGUYEN TREN DIA (3 file da doi ten)\n"
    "'@\n"
    "git commit -m $msg\n"
)


def _call(coro):
    return asyncio.run(coro)


def _payload(raw):
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


class DryRunPreviewSurfacesProseTest(unittest.TestCase):
    """The dry_run preview is the payload a CA reads before deciding to confirm."""

    def test_run_ps1_script_dry_run_carries_prose_matches(self):
        raw = _call(code_tools.run_ps1_script(
            project_path=str(ROOT), script_content=PROSE_BODY, dry_run=True))
        payload = _payload(raw)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["executed"])
        # The number stays honest...
        self.assertEqual(payload["destructive_count"], 0)
        self.assertEqual(payload["confirm_destructive_required"], 0)
        # ...and what was excluded is visible.
        self.assertIn("prose_matches", payload)
        self.assertEqual(len(payload["prose_matches"]), 2, payload["prose_matches"])
        self.assertEqual(
            {m["kind"] for m in payload["prose_matches"]},
            {"remove_item", "git_rm"})
        for match in payload["prose_matches"]:
            self.assertIn("why_not_counted", match)

    def test_the_warning_names_a_field_that_is_actually_present(self):
        """The exact defect in one assertion: a warning must not point at a field
        the response does not carry."""
        raw = _call(code_tools.run_ps1_script(
            project_path=str(ROOT), script_content=PROSE_BODY, dry_run=True))
        payload = _payload(raw)
        mentions = [w for w in payload.get("warnings", []) if "prose_matches" in w]
        self.assertTrue(mentions, payload.get("warnings"))
        self.assertIn("prose_matches", payload,
                      "a warning told the caller to see prose_matches and it is absent")


class CleanPayloadsStayCleanTest(unittest.TestCase):
    """`prose_matches` is added only when there IS prose. An always-present empty
    list on every payload would be noise on the 99% of scripts that have none --
    the scan dict guarantees the key, the payloads report an event."""

    def test_a_body_with_no_here_string_gets_no_prose_key(self):
        raw = _call(code_tools.run_ps1_script(
            project_path=str(ROOT), script_content="Write-Host 'hello'\n", dry_run=True))
        payload = _payload(raw)
        self.assertNotIn("prose_matches", payload)

    def test_a_real_destructive_body_counts_and_still_reports_its_prose(self):
        body = (
            "$msg = @'\n"
            "note: this mentions Remove-Item in a sentence\n"
            "'@\n"
            "Remove-Item -LiteralPath $env:TEMP\\b028_probe -Recurse -Force\n"
        )
        raw = _call(code_tools.run_ps1_script(
            project_path=str(ROOT), script_content=body, dry_run=True))
        payload = _payload(raw)
        self.assertEqual(payload["destructive_count"], 1, payload.get("destructive_statements"))
        self.assertEqual(payload["confirm_destructive_required"], 1)
        self.assertEqual(len(payload["prose_matches"]), 1)
        # The counted statement is the command, not the sentence.
        self.assertEqual(payload["destructive_statements"][0]["line"], 4)


class RefusalCarriesProseTest(unittest.TestCase):
    """A refusal is the payload a caller has the LEAST other information from, so
    the exclusion has to be legible there too."""

    def test_the_confirmation_refusal_carries_prose_matches(self):
        body = (
            "$msg = @'\n"
            "note: this mentions Remove-Item in a sentence\n"
            "'@\n"
            "Remove-Item -LiteralPath $env:TEMP\\b028_probe2 -Recurse -Force\n"
        )
        scan = code_tools._scan_destructive_script(body, "powershell")
        refusal = code_tools._destructive_refusal(
            scan, surface="run_ps1_script", dry_run=False, confirm_destructive=0)
        self.assertIsNotNone(refusal)
        self.assertEqual(refusal["error_name"], "DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED")
        self.assertEqual(refusal["destructive_count"], 1)
        self.assertIn("prose_matches", refusal)
        self.assertEqual(len(refusal["prose_matches"]), 1)

    def test_a_footgun_refusal_carries_prose_matches(self):
        """A footgun is retryable=False with no confirm path. If prose is what a
        caller has to argue about, they need to see it in the one payload they
        cannot retry past."""
        body = (
            "$msg = @'\n"
            "CASE-2008 was Remove-Item -LiteralPath $d -Include *.tmp -Recurse\n"
            "'@\n"
            "Remove-Item -LiteralPath $env:TEMP\\b028_probe3 -Include *.tmp -Recurse\n"
        )
        scan = code_tools._scan_destructive_script(body, "powershell")
        self.assertEqual(len(scan["footguns"]), 1, scan)
        refusal = code_tools._destructive_refusal(
            scan, surface="run_ps1_script", dry_run=False, confirm_destructive=0)
        self.assertIsNotNone(refusal)
        self.assertEqual(refusal["error_name"], "DESTRUCTIVE_SCRIPT_FOOTGUN")
        self.assertIn("prose_matches", refusal)


class EverySurfaceIsCoveredTest(unittest.TestCase):
    """A source assertion, because the defect was a MISSING copy at one of several
    structurally identical sites -- exactly the shape a behavioural test on one
    tool cannot catch. B018's lesson: a survey only proves things about the sites
    it enumerated, so enumerate them mechanically."""

    def test_every_destructive_statements_site_also_handles_prose_matches(self):
        source = (ROOT / "code_tools.py").read_text(encoding="utf-8")
        lines = source.splitlines()
        sites = [i for i, line in enumerate(lines) if "destructive_statements" in line]
        self.assertGreaterEqual(len(sites), 6, "sites moved; re-check this test")
        missing = []
        for index in sites:
            # A window, not the same line: the dict/kwargs block that carries
            # destructive_statements is where prose_matches has to appear too.
            window = "\n".join(lines[max(0, index - 14):index + 15])
            if "prose_matches" not in window:
                missing.append(index + 1)
        self.assertEqual(missing, [],
                         "these destructive_statements sites do not surface "
                         "prose_matches: %s" % missing)


if __name__ == "__main__":
    unittest.main()
