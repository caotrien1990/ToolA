"""P24 Part B: read-gate.js checkBash must expand ~ and the four known env vars
in `cd` targets and cat/grep path operands, and must treat an unexpandable
$VAR cd target as UNKNOWN cwd (fail OPEN, never fall back to the hook cwd).

Measured live 2026-10-04: `cd ~/.zcode/cli/plugins/x; grep -rn -i "inline" .`
resolved ~ AGAINST THE HOOK CWD (a project) and the gate false-denied an
out-of-project search.

The fixture layout per test run sits at a marker-free drive root (see setUp for
why %TEMP% cannot host it on this box):

  <tmp>/proj/            fixture PROJECT (.git marker) with a >200-line big.py
  <tmp>/profile/         fake USERPROFILE/HOME -- NOT a project itself
  <tmp>/profile/.zcode/x/    plain dir, no project marker (harness-name exempt
                             does NOT cover .zcode -- HARNESS_DIR_RE matches
                             only .claude*/.codex*)
  <tmp>/profile/proj/    a PROJECT inside the fake profile (.git + big.py)
  <tmp>/state/           NATIVE_READ_GATE_DIR (state seam; gate stays ON)

Every case pipes a Claude-shaped PreToolUse payload into the REAL gate via
node; allow = empty stdout, deny = permissionDecision "deny".
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "hooks" / "read-gate.js"
SYMBOL = "someSymbolName"  # BARE_IDENT_RE-shaped: denied in the control cases


def _big_py(path: Path) -> None:
    # >200 lines AND >12KB: the gate's lineCount fast-paths <=12KB to 0
    # ("small enough"), so a thin 210-line file would read as small.
    path.write_text("# fixture\n" + ("value_of_the_fixture = 'padding padding padding'\n" * 400),
                    encoding="utf-8")


class TestReadGateShellPaths(unittest.TestCase):
    def setUp(self):
        # The fixtures must NOT sit under the real user profile: this box has
        # C:\Users\ADMIN\.orchestrator_cache, and the gate's project-root walk
        # only skips the profile root named by USERPROFILE -- with USERPROFILE
        # re-pointed at the fake profile, the REAL profile stays a valid root
        # candidate and every marker-less %TEMP% fixture would resolve INTO it
        # (measured while building this test: a fake ~/.zcode/x under %TEMP%
        # denied with project root C:\Users\ADMIN). Base the tree at the
        # marker-free drive root of the repo instead.
        drive = Path(REPO.drive + "\\") if REPO.drive else Path(tempfile.gettempdir())
        self.tempdir = tempfile.TemporaryDirectory(dir=str(drive))
        self.addCleanup(self.tempdir.cleanup)
        tmp = Path(self.tempdir.name)
        self.proj = tmp / "proj"
        (self.proj / ".git").mkdir(parents=True)
        _big_py(self.proj / "big.py")
        self.profile = tmp / "profile"
        (self.profile / ".zcode" / "x").mkdir(parents=True)
        _big_py(self.profile / ".zcode" / "big.py")
        self.inner_proj = self.profile / "proj"
        (self.inner_proj / ".git").mkdir(parents=True)
        _big_py(self.inner_proj / "big.py")
        self.state = tmp / "state"
        self.state.mkdir()
        self.outside = tmp / "outside"
        self.outside.mkdir()  # no marker anywhere up-tree from tmp except proj dirs

    def _run_gate(self, command: str, cwd: Path) -> str:
        env = dict(os.environ)
        env["USERPROFILE"] = str(self.profile)
        env["HOME"] = str(self.profile)
        env["NATIVE_READ_GATE_NO_TEMP_EXEMPT"] = "1"
        env["NATIVE_READ_GATE_DIR"] = str(self.state)
        payload = {"tool_name": "Bash", "tool_input": {"command": command},
                   "cwd": str(cwd), "hook_event_name": "PreToolUse",
                   "session_id": "p24-test"}
        r = subprocess.run(["node", str(GATE)], input=json.dumps(payload),
                           env=env, capture_output=True, text=True, timeout=25)
        return (r.stdout or "").strip()

    def _denied(self, out: str) -> bool:
        return bool(out) and '"permissionDecision"' in out and '"deny"' in out

    def test_cd_tilde_zcode_search_is_allowed(self):
        out = self._run_gate(f"cd ~/.zcode/x; grep -rn {SYMBOL} .", cwd=self.proj)
        self.assertFalse(self._denied(out), out)

    def test_cd_home_var_search_is_allowed(self):
        out = self._run_gate(f'cd "$HOME/.zcode/x"; grep -rn {SYMBOL} .', cwd=self.proj)
        self.assertFalse(self._denied(out), out)

    def test_cd_unknown_var_means_unknown_cwd_fail_open(self):
        out = self._run_gate(f"cd \"$UNKNOWNVAR\"; grep -rn {SYMBOL} .", cwd=self.proj)
        self.assertFalse(self._denied(out), out)

    def test_control_plain_search_in_project_is_denied(self):
        out = self._run_gate(f"grep -rn {SYMBOL} .", cwd=self.proj)
        self.assertTrue(self._denied(out), out)

    def test_control_cd_into_project_from_outside_is_denied(self):
        cd_target = str(self.proj).replace("\\", "/")  # msys-ish slashes fine too
        out = self._run_gate(f"cd {cd_target}; grep -rn {SYMBOL} .", cwd=self.outside)
        self.assertTrue(self._denied(out), out)

    def test_cat_tilde_into_project_under_profile_is_denied(self):
        out = self._run_gate("cat ~/proj/big.py", cwd=self.outside)
        self.assertTrue(self._denied(out), out)


if __name__ == "__main__":
    unittest.main()
