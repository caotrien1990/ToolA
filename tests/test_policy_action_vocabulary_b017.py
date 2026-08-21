"""B017 -- an `action_type` [A]'s own tools emit must not classify as `unknown`.

Found while logging the audit trail for the live-answer doctor: creating two new
files inside [A]'s own tree and logging them the standard way answered

    ok: false   audit_status: logged_and_rejected
    task_class: "unknown"   native_policy: "forbid"
    host_gate: "deny_native_forbid"   ca_next_action: "stop_native_use_a_tool"

for `action_type="create_file"`, while the very same call with
`action_type="write_existing_file"` on the same tree answered `ok:true`. So a
native edit inside [A] was authorised and a native *create* inside [A] was
refused -- not by any rule, but because `create_file` had no row in
`_ACTION_TO_CLASS` and fell through to `unknown`.

This is F021's defect a second time, in the same table. F021's own comment says
it: "the log_native_event schema documents these action_type values, but they had
no row here and fell through to task_class='unknown'". F021 added rows for the
seven values the schema's description happens to list
(`read | write | execute | validate | cache | backup | workflow`) plus a few
synonyms. `create_file` is not in that description -- yet `_TOOL_TO_CLASS` maps
the tool `write_new_file` to the class `create_project_file`, so the CLASS is
real and only the action word was missing.

It is also the same shape as B012 (`validate_file` and its `lang`): the
vocabulary a caller must use is documented in one place and enforced in another,
and the two disagree. B012's answer was to normalise aliases and refuse loudly.
This is the normalisation half of that, for actions.

Two things are pinned, and the second matters more than the first:

  1. every `<verb>_file` / `<verb>_files` spelling of an action that already has
     a bare-verb row classifies the same as the bare verb; and
  2. an unknown action word STILL classifies as `unknown`/forbid. Normalising a
     suffix must not become "accept anything", or the gate stops gating.

Run: ./venv312/Scripts/python.exe -m pytest tests/test_policy_action_vocabulary_b017.py -v
"""

import unittest

from orchestrator.core.ca_policy_matrix import classify_action

A_ROOT = r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator"
USER_PROJ = r"D:\userproj"


class SuffixedActionWordsAreUnderstood(unittest.TestCase):
    """`create_file` is `create`. The suffix is noise, not a different action."""

    def _c(self, action, target, tool="native_edit"):
        return classify_action(tool_name=tool, action_type=action,
                               transport="native", target=target,
                               project_path=A_ROOT)

    def test_create_file_is_not_unknown(self):
        """The exact measured failure: a new file inside [A]'s own tree."""
        got = self._c("create_file", A_ROOT + r"\scripts\live_answer_doctor.py")
        self.assertNotEqual(
            got["task_class"], "unknown",
            "create_file fell through to task_class=unknown, so creating a "
            "file inside [A] is refused (%s) while EDITING one is allowed -- "
            "a distinction no rule makes" % got.get("host_gate"))

    def test_create_file_inside_a_is_allowed_like_an_edit(self):
        """The standing rule is 'native edit inside [A] + log_native_event'.

        A create is the same authorised path; if these two disagree the audit
        trail for a new file cannot be written at all.
        """
        created = self._c("create_file", A_ROOT + r"\scripts\new_probe.py")
        edited = self._c("write_existing_file", A_ROOT + r"\scripts\new_probe.py")
        self.assertEqual(created["native_policy"], edited["native_policy"],
                         "create vs edit inside [A]: %r vs %r"
                         % (created["native_policy"], edited["native_policy"]))
        self.assertFalse(created["is_forbidden_native"], created)

    def test_the_documented_bare_verbs_still_work(self):
        """F021's rows are the regression risk of any change to this table."""
        for verb in ("read", "write", "execute", "validate", "cache",
                     "backup", "workflow", "edit", "create", "patch"):
            got = self._c(verb, USER_PROJ + r"\a.py")
            self.assertNotEqual(got["task_class"], "unknown",
                                "bare verb %r regressed to unknown" % verb)

    def test_suffixed_spellings_match_their_bare_verb(self):
        """Same action, two spellings, one classification.

        `read_file`/`write_file`/`delete_file` are what a CA actually types --
        they read as the natural name of the thing being logged.
        """
        for verb in ("read", "write", "create", "edit", "delete", "patch",
                     "validate", "backup"):
            bare = self._c(verb, USER_PROJ + r"\a.py")
            for spelling in ("%s_file" % verb, "%s_files" % verb):
                got = self._c(spelling, USER_PROJ + r"\a.py")
                self.assertEqual(
                    got["task_class"], bare["task_class"],
                    "%r classified as %r but %r as %r"
                    % (spelling, got["task_class"], verb, bare["task_class"]))

    def test_case_and_whitespace_do_not_matter(self):
        """B012 again: `JSON` named a language the tool implemented."""
        for spelling in ("Create_File", "  create_file  ", "CREATE_FILE"):
            got = self._c(spelling, USER_PROJ + r"\a.py")
            self.assertEqual(got["task_class"], "create_project_file",
                             "%r -> %r" % (spelling, got["task_class"]))


class NormalisationMustNotBecomeAcceptAnything(unittest.TestCase):
    """The gate has to keep gating. This is the half that bounds the fix."""

    def _c(self, action, target=USER_PROJ + r"\a.py"):
        return classify_action(tool_name="", action_type=action,
                               transport="native", target=target,
                               project_path=USER_PROJ)

    def test_an_unknown_action_word_is_still_unknown(self):
        for action in ("frobnicate", "frobnicate_file", "teleport_files", ""):
            got = self._c(action)
            self.assertEqual(
                got["task_class"], "unknown",
                "%r was accepted as %r -- stripping a suffix must not accept "
                "any word that ends in _file" % (action, got["task_class"]))

    def test_a_suffix_cannot_mint_a_privileged_class(self):
        """F021's own guard, restated: a caller must not self-declare its way
        into `upgrade_orchestrator`. That class is derived from the target path,
        and a `_file` suffix must not become a second door to it."""
        for action in ("upgrade_orchestrator_file",
                       "a_unreachable_mid_task_file"):
            got = self._c(action, USER_PROJ + r"\ordinary.py")
            self.assertNotIn(
                got["task_class"],
                ("upgrade_orchestrator", "a_unreachable_mid_task"),
                "%r minted %r on a path outside [A]"
                % (action, got["task_class"]))

    def test_a_delete_is_not_widened(self):
        """A delete stays a delete. Whatever policy `delete_or_copy_project_file`
        carries, `delete_file` must carry the identical one -- the suffix must
        never be a route around the destructive gate."""
        bare = self._c("delete")
        suffixed = self._c("delete_file")
        self.assertEqual(suffixed["task_class"], bare["task_class"])
        self.assertEqual(suffixed["native_policy"], bare["native_policy"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
