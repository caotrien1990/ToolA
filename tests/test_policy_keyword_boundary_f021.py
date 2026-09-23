"""W7 (F021) -- classify_action keyword heuristics must match on token boundaries.

``classify_action`` builds ``blob = f"{tool} {action} {scope} {target}"`` and ran
its keyword heuristics with plain substring containment (``k in blob``). Because
``blob`` ends with a FILE PATH, an ordinary filename could reclassify the action
and LOOSEN ``native_policy`` away from ``forbid``:

    target ".../my_code_tools_notes.txt"  -> upgrade_orchestrator   hybrid_upgrade_only
    target ".../data download.csv"        -> a_unreachable_mid_task allow_with_log_native_event

The second one is the worse of the two: that branch assigns unconditionally, so
it overrode an already-correct ``write_existing_file`` classification. A user
naming a file "data download.csv" should not widen what a native tool may do.

The fix is a boundary match (``\\bupgrade\\b``) plus keeping the target OUT of
the "[A] is unreachable" blob entirely -- unreachability describes the session,
never a path.

This test pins BOTH directions. The false-positive half is the bug; the
true-positive half is the regression risk of the fix, and matters just as much:
``upgrade_orchestrator`` is what makes the standing "edit [A] natively +
log_native_event" rule answer ``ok:true``, so silently killing it would break
[A] self-upgrade.

Run:  ./venv312/Scripts/python.exe -m pytest tests/test_policy_keyword_boundary_f021.py -v
"""

import unittest

from orchestrator.core.ca_policy_matrix import classify_action

A_ROOT = r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator"
# A user project that does not exist on disk. It is a project only because it is
# DECLARED as project_path: since the 2026-09-23 scope matcher, an undeclared path
# under no project marker is outside_project (as %TEMP% is), so a call that means
# "a file in the user's project" has to say which project that is.
PROJ = r"D:\userproj"


class KeywordBoundaryFalsePositives(unittest.TestCase):
    """A filename must never widen native_policy."""

    def _classify(self, **kw):
        kw.setdefault("tool_name", "Write")
        kw.setdefault("action_type", "write")
        kw.setdefault("scope", "project")
        return classify_action(**kw)

    def test_filename_containing_upgrade_does_not_become_self_upgrade(self):
        for fname in ("upgrade_notes.md", "downgrade_helper.py", "my_code_tools_notes.txt"):
            with self.subTest(fname=fname):
                r = classify_action(
                    tool_name="SomeUnmappedTool", action_type="",
                    scope="project", target=PROJ + "\\" + fname, project_path=PROJ,
                )
                self.assertNotEqual(
                    r["task_class"], "upgrade_orchestrator",
                    f"{fname} is an ordinary project file, not an [A] self-upgrade",
                )
                self.assertEqual(r["native_policy"], "forbid")

    def test_filename_containing_a_down_does_not_override_classification(self):
        """This branch assigns unconditionally -- it clobbered a correct class."""
        for fname in ("data download.csv", "Beta downtime report.md", "a_downtime_log.md"):
            with self.subTest(fname=fname):
                r = self._classify(target=PROJ + "\\" + fname)
                self.assertEqual(
                    r["task_class"], "write_existing_file",
                    f"{fname} must stay a plain project write",
                )
                self.assertEqual(
                    r["native_policy"], "forbid",
                    f"{fname} must NOT gain allow_with_log_native_event",
                )

    def test_code_tools_in_a_user_project_is_not_self_upgrade(self):
        """Same basename as an [A] module, but outside [A] -> still a plain write."""
        r = self._classify(target=PROJ + "\\code_tools.py")
        self.assertEqual(r["task_class"], "write_existing_file")
        self.assertEqual(r["native_policy"], "forbid")

    def test_control_plain_filename(self):
        r = self._classify(target=PROJ + "\\plain.txt")
        self.assertEqual(r["task_class"], "write_existing_file")
        self.assertEqual(r["native_policy"], "forbid")


class KeywordBoundaryTruePositives(unittest.TestCase):
    """The signals the heuristics exist for must keep firing."""

    def test_upgrade_signals_still_classify(self):
        cases = [
            dict(tool_name="Edit", action_type="upgrade", target=A_ROOT + "\\code_tools.py"),
            dict(tool_name="SomeTool", action_type="", scope="hybrid", target=PROJ + "\\y.txt",
                 project_path=PROJ),
            dict(tool_name="SomeTool", action_type="", scope="edit orchestrator",
                 target=PROJ + "\\y.txt", project_path=PROJ),
        ]
        for kw in cases:
            with self.subTest(kw=kw):
                self.assertEqual(classify_action(**kw)["task_class"], "upgrade_orchestrator")

    def test_unreachable_signals_still_classify(self):
        for action in ("a down", "unreachable", "conn-refused", "connection refused"):
            with self.subTest(action=action):
                r = classify_action(
                    tool_name="Write", action_type=action,
                    scope="project", target=PROJ + "\\f.txt",
                )
                self.assertEqual(r["task_class"], "a_unreachable_mid_task")
                self.assertEqual(r["native_policy"], "allow_with_log_native_event")

    def test_web_signals_still_classify(self):
        for kw in (dict(scope="web"), dict(action_type="http fetch")):
            with self.subTest(kw=kw):
                base = dict(tool_name="SomeTool", action_type="", target="")
                base.update(kw)
                self.assertEqual(classify_action(**base)["task_class"], "general_web_research")

    def test_native_self_edit_inside_A_stays_upgrade_orchestrator(self):
        """The standing rule: native edit of [A] + log_native_event must be ok:true."""
        r = classify_action(
            tool_name="Edit", action_type="write", transport="native",
            target=A_ROOT + "\\orchestrator_server.py",
        )
        self.assertEqual(r["task_class"], "upgrade_orchestrator")
        self.assertEqual(r["native_policy"], "hybrid_upgrade_only")


if __name__ == "__main__":
    unittest.main()
