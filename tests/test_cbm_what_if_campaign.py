"""AC-P5-S1: a<->b break removes cycle; invalid ID no partial; lexical tie; original unchanged."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.whatif import (
    MAX_OPS,
    choose_break_edge,
    choose_cycle_group,
    simulate_sequence,
)
from orchestrator.graph.rules import cycle_group_count


def _view(files, edges=None, **extra):
    payload = {
        "status": "ok",
        "freshness": "fresh",
        "included_files": list(files),
        "test_files": extra.pop("test_files", []),
        "observed_edges": [
            {"src": s, "dst": d, "confidence": 1.0} for s, d in (edges or [])
        ],
        "strict_edges": [
            {"src": s, "dst": d, "confidence": 1.0} for s, d in (edges or [])
        ],
        "isolated_files": [],
        "truncated": False,
        "provenance": {"root": "", "included_set_hash": "fix"},
    }
    payload.update(extra)
    return payload


def _nodes_pairs(view):
    nodes = [p.replace("\\", "/").strip("/").casefold() for p in view["included_files"]]
    pairs = []
    seen = set()
    for e in view["observed_edges"]:
        item = (e["src"].casefold(), e["dst"].casefold())
        if item not in seen:
            seen.add(item)
            pairs.append(item)
    return nodes, pairs


class TestBreakCycleRemovesCycle:
    def test_ab_edge_removal_removes_cycle(self):
        view = _view(["a.py", "b.py"], [("a.py", "b.py"), ("b.py", "a.py")])
        before_nodes, before_pairs = _nodes_pairs(view)
        assert cycle_group_count(before_nodes, before_pairs) == 1
        out = simulate_sequence(view, [{"op": "break_cycle"}])
        assert out["status"] == "ok"
        assert out["applied"] is True
        assert out["before"]["cycles"] == 1
        assert out["after"]["cycles"] == 0
        step = out["steps"][0]
        assert step["op"] == "break_cycle"
        assert {step["from"], step["to"]} == {"a.py", "b.py"}
        clone_pairs = [
            (e["src"].casefold(), e["dst"].casefold())
            for e in out["clone"]["observed_edges"]
        ]
        assert cycle_group_count(before_nodes, clone_pairs) == 0


class TestInvalidNoPartial:
    def test_invalid_id_rejects_whole_sequence(self):
        view = _view(
            ["a.py", "b.py", "c.py"],
            [("a.py", "b.py"), ("b.py", "a.py")],
        )
        snapshot = copy.deepcopy(view)
        out = simulate_sequence(
            view,
            [
                {"op": "remove_edge", "from": "a.py", "to": "b.py"},
                {"op": "remove_file", "path": "missing.py"},
            ],
        )
        assert out["status"] == "unavailable"
        assert out["reason"] == "invalid_id"
        assert out["applied"] is False
        assert out["ops_applied"] == 0
        assert out["after"] is None
        assert view == snapshot

    def test_unknown_op_rejects(self):
        view = _view(["a.py"], [])
        out = simulate_sequence(view, [{"op": "explode"}])
        assert out["status"] == "unavailable"
        assert out["reason"] == "unknown_op"
        assert out["applied"] is False


class TestLexicalTie:
    def test_break_cycle_tie_is_min_fan_in_then_src_then_dst(self):
        # a->b, b->c, c->a plus extra inbound to b and c so a has lowest fan-in (1).
        # Remaining candidates into a is only c->a, so that edge is chosen.
        view = _view(
            ["a.py", "b.py", "c.py", "x.py"],
            [
                ("a.py", "b.py"),
                ("b.py", "c.py"),
                ("c.py", "a.py"),
                ("x.py", "b.py"),
                ("x.py", "c.py"),
            ],
        )
        nodes, pairs = _nodes_pairs(view)
        group = choose_cycle_group(nodes, pairs, None)
        assert group == ["a.py", "b.py", "c.py"]
        picked = choose_break_edge(nodes, pairs, group)
        assert picked == ("c.py", "a.py")
        out = simulate_sequence(view, [{"op": "break_cycle"}])
        assert out["status"] == "ok"
        assert out["steps"][0]["from"] == "c.py"
        assert out["steps"][0]["to"] == "a.py"
        assert out["after"]["cycles"] == 0

    def test_equal_fan_in_breaks_on_source_then_target(self):
        view = _view(
            ["a.py", "b.py"],
            [("a.py", "b.py"), ("b.py", "a.py")],
        )
        nodes, pairs = _nodes_pairs(view)
        group = choose_cycle_group(nodes, pairs, None)
        picked = choose_break_edge(nodes, pairs, group)
        assert picked == ("a.py", "b.py")


class TestOriginalUnchanged:
    def test_success_leaves_input_view_untouched(self):
        view = _view(["a.py", "b.py"], [("a.py", "b.py"), ("b.py", "a.py")])
        snapshot = copy.deepcopy(view)
        out = simulate_sequence(view, [{"op": "break_cycle"}])
        assert out["status"] == "ok"
        assert out["original_unchanged"] is True
        assert view == snapshot

    def test_deadline_and_invalid_leave_input_untouched(self):
        view = _view(["a.py"], [])
        snapshot = copy.deepcopy(view)
        bad = simulate_sequence(view, [{"op": "remove_file", "path": "nope.py"}])
        assert bad["original_unchanged"] is True
        assert view == snapshot

    def test_cap_ops_unavailable(self):
        view = _view(["a.py", "b.py"], [])
        ops = [{"op": "add_edge", "from": "a.py", "to": "b.py"}] * (MAX_OPS + 1)
        out = simulate_sequence(view, ops)
        assert out["status"] == "unavailable"
        assert out["reason"] == "cap_ops"
        assert out["applied"] is False
