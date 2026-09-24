"""A29 (campaign-1608): two disclosure gaps that both cost the caller tokens.

1. `fields=` could always shrink a fat response, but nothing in a fat response
   said WHICH keys were worth naming, so the caller had to pay for the whole
   body once before it could project anything. `_r101_size_hint` names the
   heaviest keys -- and, following the A23-P2 negative cases, ONLY when the
   hint can still change something: no selector was passed and the body really
   is large. The negatives below are the contract; a hint on every response is
   the bug this guards against.

2. `ruff_preview` used to appear only when a safe fix existed, so an absent key
   conflated three states -- clean, unknown (ruff had findings it could not
   report) and crashed. Each state now names itself.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.tools.write.core import _ruff_preview_from_validation
from orchestrator_server import (
    _R101_SIZE_HINT_BYTES,
    _R101_SIZE_HINT_TOP,
    _r101_filter,
    _r101_payload_may_be_fat,
    _r101_project_nested,
    _r101_size_hint,
    _r101_split_specs,
)


def _fat_payload(**extra):
    """A response comfortably over the byte threshold, shaped like a real one."""
    payload = {
        "ok": True,
        "count": 40,
        "elapsed_ms": 17,
        "rows": [{"file": "x" * 600, "n": i} for i in range(40)],
    }
    payload.update(extra)
    return payload


class SizeHintFiresOnlyWhenUseful(unittest.TestCase):

    def test_fat_unprojected_response_names_its_heaviest_keys(self):
        payload = _fat_payload()
        out = _r101_size_hint(payload, None)
        self.assertIn("ca_fields_hint", out)
        self.assertGreaterEqual(out["response_bytes"], _R101_SIZE_HINT_BYTES)
        hint = out["ca_fields_hint"]
        self.assertEqual(hint["heaviest_fields"][0]["field"], "rows")
        self.assertLessEqual(len(hint["heaviest_fields"]), _R101_SIZE_HINT_TOP)
        # The example must be a usable fields= value: cheap keys only, and the
        # heavy key it is meant to exclude must not appear in it.
        self.assertTrue(hint["fields_example"].startswith("ok"))
        self.assertIn("count", hint["fields_example"])
        self.assertNotIn("rows", hint["fields_example"].split(","))
        self.assertEqual(out["ca_next_action"],
                         "repeat_with_fields_filter_when_rereading")

    def test_heaviest_fields_are_ordered_by_cost(self):
        payload = _fat_payload(medium=["y" * 100 for _ in range(20)])
        hint = _r101_size_hint(payload, None)["ca_fields_hint"]
        widths = [f["bytes"] for f in hint["heaviest_fields"]]
        self.assertEqual(widths, sorted(widths, reverse=True))

    def test_caller_payload_is_not_mutated(self):
        payload = _fat_payload()
        _r101_size_hint(payload, None)
        self.assertNotIn("ca_fields_hint", payload)
        self.assertNotIn("response_bytes", payload)

    def test_no_hint_when_the_caller_already_projected(self):
        payload = _fat_payload()
        self.assertIs(_r101_size_hint(payload, {"ok", "count"}), payload)

    def test_no_hint_on_a_small_response(self):
        payload = {"ok": True, "count": 1, "file": "a.py"}
        self.assertIs(_r101_size_hint(payload, None), payload)

    def test_no_hint_when_many_keys_are_still_cheap(self):
        # Passes the structural gate (a 40-item list) but is nowhere near the
        # byte threshold -- the measured pass, not the gate, has the last word.
        payload = {"ok": True, "rows": list(range(40))}
        self.assertTrue(_r101_payload_may_be_fat(payload))
        self.assertIs(_r101_size_hint(payload, None), payload)

    def test_the_tools_own_next_action_wins(self):
        payload = _fat_payload(ca_next_action="use_partial_then_narrow_regex_scope")
        out = _r101_size_hint(payload, None)
        self.assertEqual(out["ca_next_action"],
                         "use_partial_then_narrow_regex_scope")
        self.assertIn("ca_fields_hint", out)

    def test_an_existing_hint_is_left_alone(self):
        payload = _fat_payload(ca_fields_hint={"mine": True})
        out = _r101_size_hint(payload, None)
        self.assertIs(out, payload)
        self.assertEqual(out["ca_fields_hint"], {"mine": True})

    def test_non_dict_and_empty_payloads_pass_through(self):
        rows = [1, 2, 3]
        self.assertIs(_r101_size_hint(rows, None), rows)
        empty = {}
        self.assertIs(_r101_size_hint(empty, None), empty)

    def test_unserialisable_values_do_not_raise(self):
        class Opaque:
            def __repr__(self):
                return "z" * 5000

        payload = {"ok": True, "blob": [Opaque() for _ in range(20)]}
        out = _r101_size_hint(payload, None)
        self.assertIn("ca_fields_hint", out)


class StructuralGateIsCheapButHonest(unittest.TestCase):

    def test_small_payload_is_not_fat(self):
        self.assertFalse(_r101_payload_may_be_fat({"ok": True, "n": 1}))

    def test_long_string_is_fat(self):
        self.assertTrue(_r101_payload_may_be_fat({"text": "x" * 4000}))

    def test_nested_container_is_fat(self):
        self.assertTrue(
            _r101_payload_may_be_fat({"result": {"rows": list(range(20))}}))


class RuffPreviewAlwaysNamesItsState(unittest.TestCase):

    def test_fixable_carries_a_ready_apply_payload(self):
        out = _ruff_preview_from_validation(
            {"ok": True, "ruff_fix_applicable": True,
             "ruff_rules_matched": ["I", "F401"],
             "ruff_fix_hint": "ruff safe-fix available"},
            "orchestrator/x.py")
        self.assertEqual(out["state"], "fixable")
        self.assertTrue(out["applicable"])
        self.assertEqual(out["ca_next_action"], "ruff_fix")
        self.assertEqual(out["apply_payload"]["tool"], "ruff_fix")
        self.assertTrue(out["apply_payload"]["apply"])
        self.assertEqual(out["apply_payload"]["path"], "orchestrator/x.py")
        self.assertIn("RUFF_VALIDATION_FAILED_AUTO_REVERT",
                      out["apply_payload"]["note"])

    def test_lint_skipped_is_unknown_not_clean(self):
        # code_tools.py:12669 -- ruff exited 1 with no parsable output. The old
        # shape dropped the key here, which read exactly like a clean file.
        out = _ruff_preview_from_validation(
            {"ok": True, "ruff_fix_applicable": False,
             "lint_skipped_reason": "ruff exited 1 but produced no output"},
            "orchestrator/x.py")
        self.assertEqual(out["state"], "unknown")
        self.assertFalse(out["checked"])
        self.assertIn("ruff exited 1", out["reason"])

    def test_violations_without_a_safe_fix_are_reported(self):
        out = _ruff_preview_from_validation(
            {"ok": True, "ruff_fix_applicable": False,
             "lint_warning": "x.py:1:1: E999 SyntaxError\nFound 1 error."},
            "orchestrator/x.py")
        self.assertEqual(out["state"], "violations_not_auto_fixable")
        self.assertTrue(out["checked"])
        self.assertIn("E999", out["lint_warning"])

    def test_clean_file_says_clean(self):
        out = _ruff_preview_from_validation(
            {"ok": True, "ruff_fix_applicable": False, "checked": True},
            "orchestrator/x.py")
        self.assertEqual(out["state"], "clean")
        self.assertTrue(out["checked"])
        self.assertFalse(out["applicable"])

    def test_non_dict_validation_is_unknown(self):
        out = _ruff_preview_from_validation("boom", "orchestrator/x.py")
        self.assertEqual(out["state"], "unknown")
        self.assertFalse(out["checked"])

    def test_every_state_is_one_of_the_four(self):
        states = {
            _ruff_preview_from_validation(v, "x.py")["state"]
            for v in (
                {"ruff_fix_applicable": True},
                {"ruff_fix_applicable": False, "lint_skipped_reason": "?"},
                {"ruff_fix_applicable": False, "lint_warning": "e"},
                {"ruff_fix_applicable": False},
                None,
            )
        }
        self.assertEqual(
            states,
            {"fixable", "unknown", "violations_not_auto_fixable", "clean"})


class NestedProjectionMakesFieldsUsable(unittest.TestCase):
    """A29-P3: `fields=result.x` has to slim an _ok()-wrapped body.

    Nearly every [A] tool answers through _ok(), so its whole payload sits
    under one `result` key. With top-level-only projection the caller could
    keep or drop that key and nothing in between -- no saving at all on
    exactly the responses too fat to read twice, which is what made the size
    hint's advice hollow for cbm_what_if.
    """

    def _wrapped(self):
        return {"ok": True,
                "result": {"edge_kind": "call", "impacted_count": 3,
                           "rows": [{"f": "x" * 200} for _ in range(30)]}}

    def test_dotted_key_keeps_only_that_subfield(self):
        out = _r101_filter(self._wrapped(), {"result.edge_kind"})
        self.assertEqual(set(out["result"]), {"edge_kind"})
        self.assertTrue(out["ok"])

    def test_several_dotted_keys_under_one_head(self):
        out = _r101_filter(self._wrapped(),
                           {"result.edge_kind", "result.impacted_count"})
        self.assertEqual(set(out["result"]), {"edge_kind", "impacted_count"})

    def test_projection_is_a_real_saving(self):
        payload = self._wrapped()
        after = len(repr(_r101_filter(payload, {"result.edge_kind"})))
        self.assertLess(after * 10, len(repr(payload)))

    def test_caller_payload_is_not_mutated(self):
        payload = self._wrapped()
        _r101_filter(payload, {"result.edge_kind"})
        self.assertEqual(set(payload["result"]),
                         {"edge_kind", "impacted_count", "rows"})

    def test_unknown_subfield_is_named_not_silently_empty(self):
        out = _r101_filter(self._wrapped(), {"result.edge_kinds"})
        self.assertEqual(out["fields_unknown"], ["result.edge_kinds"])
        self.assertEqual(out["result"], {})

    def test_dotted_head_that_is_not_a_dict_is_reported(self):
        out = _r101_filter({"ok": True, "result": "plain text"},
                           {"result.rows"})
        self.assertEqual(out["fields_unknown"], ["result.rows"])
        self.assertEqual(out["result"], "plain text")

    def test_absent_head_is_reported(self):
        out = _r101_filter({"ok": True, "result": {}}, {"payload.rows"})
        self.assertEqual(out["fields_unknown"], ["payload.rows"])

    def test_whole_branch_wins_over_a_subselector(self):
        out = _r101_filter(self._wrapped(), {"result", "result.edge_kind"})
        self.assertEqual(set(out["result"]),
                         {"edge_kind", "impacted_count", "rows"})
        self.assertNotIn("fields_unknown", out)

    def test_deeper_than_one_level(self):
        payload = {"ok": True,
                   "result": {"graph": {"nodes": 9, "edges": 4,
                                        "big": "x" * 900}}}
        out = _r101_filter(payload, {"result.graph.nodes"})
        self.assertEqual(out["result"], {"graph": {"nodes": 9}})

    def test_safety_keys_are_rescued_inside_a_nested_projection(self):
        payload = {"ok": True,
                   "result": {"hash_domain": "raw_file_bytes",
                              "rows": [1, 2, 3]}}
        out = _r101_filter(payload, {"result.rows"})
        self.assertEqual(out["result"]["hash_domain"], "raw_file_bytes")
        self.assertIn("result.hash_domain", out["fields_force_kept"])
        self.assertEqual(out["response_incomplete"], "RESPONSE_INCOMPLETE")

    def test_plain_only_selector_behaves_exactly_as_before(self):
        out = _r101_filter({"ok": True, "a": 1, "b": 2}, {"a"})
        self.assertEqual(out, {"ok": True, "a": 1})

    def test_split_specs_separates_plain_from_dotted(self):
        plain, nested = _r101_split_specs({"ok", "result.a", "result.b",
                                          "x.y.z"})
        self.assertEqual(plain, {"ok"})
        self.assertEqual(nested, {"result": {"a", "b"}, "x": {"y.z"}})

    def test_split_specs_ignores_a_trailing_dot(self):
        plain, nested = _r101_split_specs({"result."})
        self.assertEqual(plain, {"result."})
        self.assertEqual(nested, {})

    def test_project_nested_keeps_what_was_asked_and_names_the_miss(self):
        out, rescued, unknown = _r101_project_nested(
            {"a": 1, "b": 2, "c": 3}, {"a", "zz"})
        self.assertEqual((out, rescued, unknown), ({"a": 1}, [], ["zz"]))


class SizeHintDescendsIntoTheWrapper(unittest.TestCase):
    """Naming `result` is true but useless: `fields=ok,result` saves nothing."""

    def _wrapped_fat(self):
        return {"ok": True,
                "result": {"edge_kind": "call", "impacted_count": 3,
                           "rows": [{"f": "x" * 700, "n": i}
                                    for i in range(40)]}}

    def test_dotted_names_replace_the_useless_wrapper_name(self):
        hint = _r101_size_hint(self._wrapped_fat(), None)["ca_fields_hint"]
        self.assertTrue(hint["nested_projection"])
        names = [f["field"] for f in hint["heaviest_fields"]]
        self.assertEqual(names[0], "result.rows")
        self.assertNotIn("result", names)

    def test_the_example_it_prints_actually_projects(self):
        payload = self._wrapped_fat()
        hint = _r101_size_hint(payload, None)["ca_fields_hint"]
        selector = set(hint["fields_example"].split(","))
        self.assertIn("result.edge_kind", selector)
        out = _r101_filter(payload, selector)
        self.assertNotIn("fields_unknown", out)
        self.assertLess(len(repr(out)) * 10, len(repr(payload)))

    def test_flat_payload_still_gets_top_level_names(self):
        hint = _r101_size_hint(_fat_payload(), None)["ca_fields_hint"]
        self.assertFalse(hint["nested_projection"])
        self.assertEqual(hint["heaviest_fields"][0]["field"], "rows")

    def test_a_shared_load_is_not_treated_as_dominant(self):
        # Two heavy siblings: no single wrapper dominates, so descending into
        # one of them would hide the other. Stay at the top level.
        big = [{"f": "x" * 700} for _ in range(40)]
        out = _r101_size_hint({"ok": True, "a": {"rows": big},
                               "b": list(big)}, None)
        self.assertFalse(out["ca_fields_hint"]["nested_projection"])

    def test_example_is_never_only_ok(self):
        payload = {"ok": True, "result": {"a": "x" * 9000, "b": "y" * 9000,
                                          "c": "z" * 9000}}
        hint = _r101_size_hint(payload, None)["ca_fields_hint"]
        self.assertNotEqual(hint["fields_example"], "ok")


class RuffRuleSetsMustNotDrift(unittest.TestCase):
    """A29-P4 / A32-1.2: what validate_file LINTS vs what ruff_fix may repair.

    validate_file lints with one --extend-select list and _py_error_ruff_hint decides
    fixability from another. Until A32-1.2 the two were the SAME set, and this
    docstring claimed `violations_not_auto_fixable` was therefore unreachable --
    which was never quite true: E999 is reported by lint and matched by no rule
    in the hint, so test_ruff_preview_states already reaches that branch at :167
    and :197 already pins it as one of the four live states. A32-1.2 makes it
    reachable from ORDINARY lint too, deliberately: B006, E722 and F821 are selected
    so a mutable default argument, a bare except and an undefined name are SEEN, but
    ruff cannot repair them unattended (B006's fix is `unsafe`, E722 and F821 have
    none), so they must never be
    advertised as fixable. The invariant these tests pin is therefore no longer
    equality but containment: _RUFF_LINT_SELECT == defect-fixable + report-only,
    while _RUFF_FIX_SELECT (the third copy, in orchestrator/graph/tools.py -- the
    one handed to --fix) tracks the defect-fixable half alone. #7: the POLICY
    rules (RUF100, I) are fixable but in neither select -- the project's own
    config decides whether they run at all.
    """

    FIXABLE_SELECT = "F401,F841,E711,E712,E731,E401,E402,F541"
    LINT_SELECT = "F401,F841,E711,E712,E731,E401,E402,F541,B006,E722,F821"

    def test_refactor_did_not_change_what_ruff_is_asked_to_check(self):
        import code_tools

        self.assertEqual(code_tools._RUFF_LINT_SELECT, self.LINT_SELECT)

    def test_ruff_fix_selects_only_the_half_it_may_repair(self):
        # The third copy lives in another module. Comparing it here keeps the
        # daemon free of a boot-time import while still failing loudly the day
        # someone widens one list and not the other. It must equal the FIXABLE
        # half: widening it to the lint select would hand `ruff --fix` a rule it
        # can only repair unsafely, which is exactly what A24 refused to allow.
        import code_tools
        from orchestrator.graph import tools as graph_tools

        self.assertEqual(graph_tools._RUFF_FIX_SELECT, self.FIXABLE_SELECT)
        self.assertNotEqual(graph_tools._RUFF_FIX_SELECT,
                            code_tools._RUFF_LINT_SELECT)

    def test_the_lint_select_is_the_defect_set_plus_the_report_only_set(self):
        import code_tools
        from orchestrator.graph import tools as graph_tools

        self.assertEqual(
            code_tools._RUFF_LINT_SELECT,
            ",".join((*code_tools._RUFF_DEFECT_FIX_RULES,
                      *code_tools._RUFF_REPORT_ONLY_RULES)))
        self.assertEqual(code_tools._RUFF_SAFE_FIX_RULES,
                         (*code_tools._RUFF_DEFECT_FIX_RULES,
                          *code_tools._RUFF_POLICY_FIX_RULES))
        for rule in code_tools._RUFF_REPORT_ONLY_RULES:
            self.assertNotIn(rule, code_tools._RUFF_SAFE_FIX_RULES,
                             "a report-only rule in the fixable tuple would make "
                             "_py_error_ruff_hint promise a fix ruff refuses")
        # #7: a policy rule in either select is [A] imposing it on the project.
        for rule in code_tools._RUFF_POLICY_FIX_RULES:
            self.assertNotIn(rule, code_tools._RUFF_LINT_SELECT.split(","))
            self.assertNotIn(rule, graph_tools._RUFF_FIX_SELECT.split(","))

    def test_every_selected_rule_is_recognised_as_fixable(self):
        # #7: the policy rules too -- [A] no longer selects them, but a project
        # that enables one still reports it, and ruff_fix then selects it as well.
        import code_tools

        for rule in code_tools._RUFF_SAFE_FIX_RULES:
            code = "I001" if rule == "I" else rule
            line = f"probe.py:1:1: {code} [*] something ruff dislikes"
            applicable, matched, hint = code_tools._py_error_ruff_hint(line)
            self.assertTrue(applicable, f"{code} not recognised as fixable")
            self.assertTrue(matched, f"{code} matched no rule")
            self.assertIn("ruff_fix", hint)

    def test_a_policy_rule_is_reported_only_where_the_project_enables_it(self):
        """#7, measured 2026-09-25: [A] added I and RUF100 to every project's lint,
        so this repo -- whose own config reports 0 -- got 216 I001 + 169 RUF100
        from it. The same unsorted file is linted under a config without I and
        under one with it: the finding follows the project, not [A]."""
        import asyncio
        import json
        import tempfile

        import code_tools

        source = "import sys\nimport os\n\nprint(os, sys)\n"
        seen = {}
        for name, config in (("without", "[lint]\nselect = ['E9']\n"),
                             ("with", "[lint]\nselect = ['E9', 'I']\n")):
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "ruff.toml").write_text(config, encoding="utf-8")
                probe = Path(tmp) / "probe.py"
                probe.write_text(source, encoding="utf-8")
                raw = asyncio.run(code_tools.validate_file(str(probe)))
                seen[name] = json.loads(raw) if isinstance(raw, str) else raw
        without, with_i = seen["without"], seen["with"]
        self.assertTrue(without.get("ok"), without)
        self.assertNotIn("lint_skipped_reason", without)
        self.assertNotIn("I001", without.get("lint_warning", ""), without)
        # Positive control: the rule is real, and the hint still names it.
        self.assertIn("I001", with_i.get("lint_warning", ""), with_i)
        self.assertEqual(with_i.get("ruff_rules_matched"), ["I"], with_i)

    def test_a_report_only_rule_is_linted_but_never_called_fixable(self):
        # A32-1.2: the live route into `violations_not_auto_fixable`. Measured on
        # ruff 0.16.7 with both controls: a probe with `def f(x=[])` + a bare
        # `except:` fired B006 at 1:9 and E722 at 4:5, and `--select ZZ999` is
        # rejected as an unknown selector, so these codes are real and enabled.
        # `--diff` over the whole repo wrote 0 bytes -- there is nothing safe to
        # apply, so a hint naming ruff_fix would be a promise ToolA cannot keep.
        import code_tools
        from orchestrator.tools.write.core import _ruff_preview_from_validation

        for rule, warning in (
                ("B006", "probe.py:1:9: B006 Do not use mutable data structures"
                         " for argument defaults"),
                ("E722", "probe.py:4:5: E722 Do not use bare `except`")):
            self.assertIn(rule, code_tools._RUFF_LINT_SELECT)
            applicable, matched, hint = code_tools._py_error_ruff_hint(warning)
            self.assertFalse(applicable, f"{rule} must not be called fixable")
            self.assertEqual(matched, [])
            self.assertEqual(hint, "")
            preview = _ruff_preview_from_validation(
                {"ok": True, "lang": "python", "lint_warning": warning,
                 "ruff_fix_applicable": False, "ruff_rules_matched": []},
                "probe.py")
            self.assertEqual(preview["state"], "violations_not_auto_fixable")
            self.assertIn(rule, preview["lint_warning"])

    def test_an_unselected_rule_would_take_the_not_fixable_branch(self):
        # The drift case, proven by construction: E501 is deliberately NOT in
        # the select set, yet a project's own rules still reach lint_warning
        # (--extend-select keeps them), so ruff_preview must report real lint
        # instead of laundering it into "clean".
        import code_tools
        from orchestrator.tools.write.core import _ruff_preview_from_validation

        self.assertNotIn("E501", code_tools._RUFF_LINT_SELECT)
        warning = "probe.py:2:80: E501 Line too long (99 > 79)"
        applicable, matched, _ = code_tools._py_error_ruff_hint(warning)
        self.assertFalse(applicable)
        self.assertEqual(matched, [])
        preview = _ruff_preview_from_validation(
            {"ok": True, "lang": "python", "lint_warning": warning,
             "ruff_fix_applicable": False, "ruff_rules_matched": []},
            "probe.py")
        self.assertEqual(preview["state"], "violations_not_auto_fixable")
        self.assertTrue(preview["checked"])
        self.assertFalse(preview["applicable"])
        self.assertIn("E501", preview["lint_warning"])


class WriteNewFileGetsTheSameAutoChainAsWriteFix(unittest.TestCase):
    """A30-P1: the ruff auto-chain was hooked into write_fix ONLY.

    Probing write_new_file therefore read `ruff_preview: null`, which looks
    exactly like a dead feature rather than an unhooked one -- a measurement
    trap that cost real time in the campaign before this one. These tests pin
    the PARITY, not the implementation: whatever write_fix attaches on a
    successful .py write, write_new_file must attach too -- and the two early
    returns above it (dry_run, validation-failed auto-revert) must still
    attach nothing, because neither leaves a committed file to lint.
    """

    DIRTY = "import os\nimport sys\n\nprint(sys.path)\n"

    def setUp(self):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def _new(self, name, body, **kw):
        import asyncio
        import json

        import code_tools as ct

        return json.loads(asyncio.run(ct.write_new_file(
            name, project_path=self.root, new_content=body, **kw)))

    def test_a_dirty_new_py_file_is_told_it_is_fixable(self):
        out = self._new("dirty.py", self.DIRTY)
        self.assertTrue(out.get("ok"), msg=out)
        preview = out["ruff_preview"]
        self.assertEqual(preview["state"], "fixable")
        self.assertIn("F401", preview["rules"])
        self.assertEqual(preview["ca_next_action"], "ruff_fix")
        self.assertTrue(preview["apply_payload"]["apply"])

    def test_a_clean_new_py_file_says_clean_not_silence(self):
        out = self._new("clean.py", "VALUE = 1\n")
        self.assertTrue(out.get("ok"), msg=out)
        self.assertEqual(out["ruff_preview"]["state"], "clean")
        self.assertTrue(out["ruff_preview"]["checked"])

    def test_a_dry_run_attaches_nothing_because_no_file_exists(self):
        out = self._new("ghost.py", self.DIRTY, dry_run=True)
        self.assertTrue(out.get("ok") and out.get("dry_run"), msg=out)
        self.assertNotIn("ruff_preview", out)
        self.assertNotIn("ca_hint_commit", out)

    def test_a_non_python_file_is_not_given_a_ruff_verdict(self):
        out = self._new("notes.txt", "hello\n")
        self.assertTrue(out.get("ok"), msg=out)
        self.assertNotIn("ruff_preview", out)
        self.assertIn("ca_hint_commit", out)

    def test_an_auto_reverted_write_is_not_given_a_preview(self):
        # The file is unlinked before we reach the auto-chain, so a preview
        # here would describe something that no longer exists.
        import asyncio
        import json
        from unittest import mock

        import code_tools as ct

        with mock.patch.object(ct, "validate_file", new=mock.AsyncMock(
                return_value=json.dumps({"ok": False, "error": "forced"}))):
            out = json.loads(asyncio.run(ct.write_new_file(
                "reverted.py", project_path=self.root,
                new_content="VALUE = 1\n")))
        self.assertFalse(out.get("ok"), msg=out)
        self.assertNotIn("ruff_preview", out)

    def test_write_fix_and_write_new_file_agree_key_for_key(self):
        # The actual contract: the same dirty content must get the same
        # verdict whichever tool put it on disk, with the same commit hint.
        import asyncio
        import json

        import code_tools as ct

        created = self._new("parity.py", "VALUE = 1\n")
        self.assertTrue(created.get("ok"), msg=created)

        async def _edit():
            await ct.read_file_range(
                "parity.py", 1, 1, project_path=self.root)
            return await ct.write_fix(
                "parity.py", 1, 1, self.DIRTY, project_path=self.root)

        edited = json.loads(asyncio.run(_edit()))
        self.assertTrue(edited.get("ok"), msg=edited)

        fresh = self._new("parity2.py", self.DIRTY)
        self.assertEqual(fresh["ruff_preview"]["state"],
                         edited["ruff_preview"]["state"])
        self.assertEqual(fresh["ruff_preview"]["rules"],
                         edited["ruff_preview"]["rules"])
        self.assertEqual(fresh["ca_hint_commit"], edited["ca_hint_commit"])


if __name__ == "__main__":
    unittest.main()
