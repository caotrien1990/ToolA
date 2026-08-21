"""B023 -- `cbm_trace_path` answers `ok:true` with an empty trace when the start
node does not resolve, so "you passed a symbol name instead of a node id" is
indistinguishable from "this symbol genuinely has no callers".

Measured against the live daemon at generation 177faa8e5a45, over HTTP:

    cbm_trace_path(start_node_id="_cache_delta_sync", direction="out", depth=1)
      -> ok:true, edges:[], nodes_seen:0, truncated:false, error:null,
         truncation_reason:"start_node_not_found"

Every field an ordinary caller guards on says success. `ok` is true, `error` is
null, `truncated` is false. The only signal is `truncation_reason` -- a field
named for truncation, carried on a response that says it was not truncated --
and nothing in the documented guard set names it.

The clinching evidence that this is a defect and not the declared fail-open
policy is that the SAME wrapper already answers the SAME class of caller error
the opposite way. `orchestrator/graph/tools.py` refuses an EMPTY start_node_id
with `ok:false, error_name:"SCHEMA_VIOLATION"`. An empty id and an unresolvable
id are both "the caller did not pass a usable node id", and the tool answered
one with a refusal and the other with a clean empty success.

Fail-open is the right default for this tool and is kept. The tool description
promises "Fail-open on timeout/stale/missing", and that is about a graph that
could not answer -- a stale index, a 2s budget, a missing DB. A start node that
does not exist is not the graph failing to answer; it is the question being
unanswerable as asked. B018 and B024 are the same contract in the other two
directions: the guard field must track the outcome.

Four things are pinned, and the last two are what keep the fix from becoming
"fail closed on everything":

  1. an unresolvable start node answers `ok:false` + `error_name`, over the
     wrapper that HTTP actually serves;
  2. it says where a real node id comes from, so the answer is actionable;
  3. `GRAPH_ENABLED=0` still answers the byte-identical degraded payload with
     `ok:true` -- "we never looked" is not "we looked and it is absent", and
     conflating them would break the canary that guards the whole graph layer;
  4. a resolvable start node is untouched -- still `ok:true`, still traces.

Run: ./venv312/Scripts/python.exe -m pytest tests/test_trace_path_start_node_b023.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["GRAPH_ENABLED"] = "1"  # must precede orchestrator.graph.config import

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import tools as graph_tools  # noqa: E402
from orchestrator.graph.indexer import index_project  # noqa: E402
from orchestrator.graph.query import cbm_trace_path, graph_find_symbol  # noqa: E402

MOD = """def leaf_b023():
    return 1


def caller_b023():
    return leaf_b023()
"""


class _Fixture(unittest.TestCase):
    """A two-symbol graph: caller_b023 -> leaf_b023."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="b023_trace_")).resolve()
        (cls.root / "mod_b023.py").write_text(MOD, encoding="utf-8")
        cls.proj = str(cls.root).replace("\\", "/")
        index_project(cls.proj, scope="full")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def _real_node_id(self) -> str:
        hits = graph_find_symbol(self.proj, "leaf_b023")["results"]
        self.assertTrue(hits, "fixture symbol leaf_b023 not indexed")
        return hits[0]["id"]

    def _trace(self, start_node_id: str, **kw) -> dict:
        """Call the wrapper HTTP actually serves, not the inner function."""
        raw = asyncio.run(graph_tools.cbm_trace_path(
            project_path=self.proj, start_node_id=start_node_id, **kw))
        return json.loads(raw)


class AnUnresolvableStartNodeIsNotASuccess(_Fixture):

    def test_a_symbol_name_instead_of_a_node_id_is_refused(self) -> None:
        """The exact measured failure: `ok:true` on a question nobody answered."""
        env = self._trace("leaf_b023")  # a bare symbol name, not a node id
        self.assertIs(
            env.get("ok"), False,
            "an unresolvable start node answered ok=%r, so the ordinary guard "
            "reads it as a real empty trace: %s"
            % (env.get("ok"), json.dumps(env)[:400]))
        self.assertEqual(env.get("error_name"), "START_NODE_NOT_FOUND", env)

    def test_the_refusal_says_where_a_node_id_comes_from(self) -> None:
        """A refusal a caller cannot act on just moves the dead end one field over."""
        env = self._trace("no/such.py:function:x.y:1")
        blob = json.dumps(env)
        self.assertIn("cbm_template_query", blob,
                      "the refusal must name the tool that returns real node ids: %s"
                      % blob[:400])
        self.assertTrue(env.get("ca_next_action"), env)

    def test_the_empty_id_and_the_bad_id_agree(self) -> None:
        """Both are "no usable node id"; answering them oppositely was the bug."""
        empty = self._trace("")
        bad = self._trace("definitely-not-a-node-id")
        self.assertIs(empty.get("ok"), False, empty)
        self.assertIs(bad.get("ok"), False, bad)


class FailOpenIsKeptWhereItWasPromised(_Fixture):

    def test_a_resolvable_start_node_is_untouched(self) -> None:
        env = self._trace(self._real_node_id(), direction="in", depth=2)
        self.assertIs(env.get("ok"), True, env)
        result = env.get("result") or {}
        self.assertTrue(result.get("edges"),
                        "the fixture caller edge disappeared: %s"
                        % json.dumps(env)[:400])
        self.assertIs(result.get("start_node_resolved"), True, result)

    def test_graph_disabled_still_answers_the_degraded_payload(self) -> None:
        """"We never looked" must not be reported as "we looked and it is absent".

        GRAPH_ENABLED=0 returns before any lookup. If the fix keyed off a falsy
        `start_node_resolved` it would flip this path to ok:false and break the
        canary that guards every graph tool's disabled-mode shape.
        """
        saved = os.environ.get("GRAPH_ENABLED")
        os.environ["GRAPH_ENABLED"] = "0"
        try:
            env = self._trace("anything-at-all")
            inner = cbm_trace_path(self.proj, "anything-at-all")
        finally:
            if saved is None:
                os.environ.pop("GRAPH_ENABLED", None)
            else:
                os.environ["GRAPH_ENABLED"] = saved
        self.assertIs(env.get("ok"), True,
                      "GRAPH_ENABLED=0 must stay fail-open: %s"
                      % json.dumps(env)[:300])
        self.assertIs(inner["enabled"], False, inner)
        self.assertEqual(inner["edges"], [], inner)
        self.assertIsNone(inner["error"], inner)
        self.assertIsNone(inner.get("start_node_resolved"),
                          "disabled means 'did not look', which is None -- not "
                          "False: %r" % inner.get("start_node_resolved"))


class TheInnerFunctionStaysFailOpenForItsCallers(_Fixture):
    """`query.py` is called directly by three consumers that read a dict.

    orchestrator_server.py:12849 (council_graph_context),
    orchestrator/tools/read/context.py:399 (architecture_context_pack), and
    template_query's own impact_scope branch all call the inner function and
    read `edges`/`truncated`/`error`. Flipping the inner function to raise, or
    setting its `error`, would turn council_graph_context's graph_status from
    "ok" to "error" -- so the refusal lives at the wrapper boundary and the
    inner payload only gains a field.
    """

    def test_inner_function_still_returns_a_payload_not_a_raise(self) -> None:
        p = cbm_trace_path(self.proj, "not-a-node")
        self.assertEqual(p["edges"], [], p)
        self.assertIsNone(p["error"],
                          "setting inner `error` would flip council_graph_context "
                          "to graph_status='error': %r" % p["error"])
        self.assertIs(p["start_node_resolved"], False, p)

    def test_the_server_signal_still_reads(self) -> None:
        """orchestrator_server.py:12849 branches on this exact string."""
        p = cbm_trace_path(self.proj, "not-a-node")
        self.assertEqual(p["truncation_reason"], "start_node_not_found", p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
