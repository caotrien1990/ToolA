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

    def test_project_nested_counts_what_it_dropped(self):
        out, dropped, rescued, unknown = _r101_project_nested(
            {"a": 1, "b": 2, "c": 3}, {"a"})
        self.assertEqual((out, dropped, rescued, unknown),
                         ({"a": 1}, 2, [], []))


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
    """A29-P4: why `violations_not_auto_fixable` cannot be produced live.

    validate_file lints with one --select list and _py_error_ruff_hint decides
    fixability from another. While the two lists are the SAME set, a non-empty
    lint_warning always carries a rule the hint recognises, so ruff_preview can
    only ever answer clean/fixable/unknown -- the not-auto-fixable branch is
    correct code guarding a state the current configuration cannot reach. That
    is a property of the two lists, so it is the lists these tests pin.
    """

    HISTORICAL_SELECT = "F401,F841,E711,E712,E731,E401,E402,F541,RUF100,I"

    def test_refactor_did_not_change_what_ruff_is_asked_to_check(self):
        import code_tools

        self.assertEqual(code_tools._RUFF_LINT_SELECT, self.HISTORICAL_SELECT)

    def test_ruff_fix_selects_exactly_what_validate_file_lints(self):
        # The third copy lives in another module. Comparing it here keeps the
        # daemon free of a boot-time import while still failing loudly the day
        # someone widens one list and not the other.
        import code_tools
        from orchestrator.graph import tools as graph_tools

        self.assertEqual(graph_tools._RUFF_FIX_SELECT,
                         code_tools._RUFF_LINT_SELECT)

    def test_every_selected_rule_is_recognised_as_fixable(self):
        import code_tools

        for rule in self.HISTORICAL_SELECT.split(","):
            code = "I001" if rule == "I" else rule
            line = f"probe.py:1:1: {code} something ruff dislikes"
            applicable, matched, hint = code_tools._py_error_ruff_hint(line)
            self.assertTrue(applicable, f"{code} not recognised as fixable")
            self.assertTrue(matched, f"{code} matched no rule")
            self.assertIn("ruff_fix", hint)

    def test_an_unselected_rule_would_take_the_not_fixable_branch(self):
        # The drift case, proven by construction: F821 is deliberately NOT in
        # the select set, so if a future widening let it reach lint_warning
        # without updating the matcher, ruff_preview reports real lint instead
        # of laundering it into "clean".
        import code_tools
        from orchestrator.tools.write.core import _ruff_preview_from_validation

        self.assertNotIn("F821", code_tools._RUFF_LINT_SELECT)
        warning = "probe.py:2:12: F821 Undefined name `nope`"
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
        self.assertIn("F821", preview["lint_warning"])


if __name__ == "__main__":
    unittest.main()
