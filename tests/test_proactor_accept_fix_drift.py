"""Drift and type gates for the gh-93821 mirror (plan G-DRIFT / G-TYPE
/ G-SPEC-DIFF):

- G-DRIFT: the AST anchors of the two installed-stdlib functions the
  mirror is copied from must equal the pins carried here AND in the
  module; a different interpreter base fails loudly instead of letting
  the mirror drift silently.
- G-TYPE: install must activate only when the anchors match -- a
  forced mismatch leaves the stock policy in place and logs loudly --
  and a normal install must yield the fixed loop/proactor types.
- G-SPEC-DIFF: the mirror's two copied bodies must equal the installed
  stdlib bodies except exactly the declared PR 161238 touches, the
  plan-4.3 counter increment, and their comment lines. The check is
  positional: the expected mirror sequence is CONSTRUCTED from the
  installed sequence by applying the declared edits at fixed anchors
  (a replacement of the ``ov.getresult()`` line, an insertion after
  the CancelledError branch, an insertion before ``except OSError``),
  and the mirror's line sequence must equal that constructed sequence
  exactly. The multiset differences must additionally equal the
  declared sets in both directions, so a failure names the offending
  lines. Any reordering -- of surviving lines OR of the declared
  blocks themselves (e.g. swapping the except branches, or moving the
  counter increment below the raise) -- therefore fails, as does any
  undeclared line or a missing declared touch.
"""
import ast
import asyncio
import hashlib
import os
import pathlib
import unittest
from collections import Counter
from unittest import mock

import asyncio.proactor_events as proactor_events
import asyncio.windows_events as windows_events

from orchestrator.lifecycle import proactor_accept_fix as paf

EXPECTED_ANCHORS = {
    "BaseProactorEventLoop._start_serving":
        "ab41477dfa4b1a340e060783ed185c83e6b142b406f42bd15d24e32d2b88ec86",
    "IocpProactor.accept":
        "3a0f3acfbc8f81a2705e54ccc7d40d990388202ae35ea53ad6c0e0cc60d5fb49",
}


def segment_of(path, qualname):
    """Independent copy of the anchor extraction (AST walk + source
    segment), so the test does not trust the module's own code path."""
    src = pathlib.Path(path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    parts = qualname.split(".")

    def find(nodes, name):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and node.name == name:
                return node
        return None

    node = find(tree.body, parts[0])
    for part in parts[1:]:
        node = find(node.body, part)
    return ast.get_source_segment(src, node)


def normalised_lines(segment):
    return [line for line in
            (raw.rstrip() for raw in segment.splitlines()) if line.strip()]


# The complete declared deviation of FixedIocpProactor.accept from the
# installed IocpProactor.accept, as ORDERED blocks: PR touch 1 (which
# replaces the bare ``ov.getresult()`` line and carries the plan-4.3
# counter increment inside the WinError 64 branch) and PR touch 2.
# Anything else appearing in the diff fails.
ACCEPT_TOUCH1_BLOCK = [
    "            # PR 161238 touch 1: a reset of the pending accept becomes",
    "            # ConnectionResetError instead of a raw OSError, so the",
    "            # serving loop keeps the listening socket.",
    "            try:",
    "                ov.getresult()",
    "            except OSError as exc:",
    "                conn.close()",
    "                # The peer reset the connection before it was accepted.",
    "                if exc.winerror == _overlapped.ERROR_NETNAME_DELETED:",
    "                    _SWALLOWED.increment()",
    "                    raise ConnectionResetError(*exc.args)",
    "                raise",
]
ACCEPT_TOUCH2_BLOCK = [
    "            # PR 161238 touch 2: the reset is reported to the caller",
    "            # through the future; the side task stays silent.",
    "            except ConnectionResetError:",
    "                # Reported to the caller through the future.",
    "                pass",
]
# Edit script over the installed line sequence: touch 1 replaces the
# bare ``ov.getresult()`` line; touch 2 is inserted immediately after
# the CancelledError branch of accept_coro (its three lines, in order).
ACCEPT_EDITS = [
    ("replace_first", "            ov.getresult()", ACCEPT_TOUCH1_BLOCK),
    ("insert_after_seq",
     ("            except exceptions.CancelledError:",
      "                conn.close()",
      "                raise"),
     ACCEPT_TOUCH2_BLOCK),
]
ACCEPT_ALLOWED_ADD = Counter(ACCEPT_TOUCH1_BLOCK + ACCEPT_TOUCH2_BLOCK)
ACCEPT_ALLOWED_REMOVE = Counter({
    "            ov.getresult()": 1,
})

# The complete declared deviation of FixedProactorEventLoop
# ._start_serving: PR touch 3, inserted immediately BEFORE the
# ``except OSError`` branch (order matters: ConnectionResetError is an
# OSError subclass, so the branch must be tried first).
START_SERVING_TOUCH3_BLOCK = [
    "            # PR 161238 touch 3: keep accepting after a reset; the",
    "            # listening socket is still usable.",
    "            except (ConnectionResetError, ConnectionAbortedError):",
    "                # The peer closed the connection before it was accepted.",
    "                # The listening socket is still usable: keep accepting.",
    "                if self._debug:",
    '                    logger.debug("Accept failed on socket %r",',
    "                                 sock, exc_info=True)",
    "                self.call_soon(loop)",
]
START_SERVING_EDITS = [
    ("insert_before", "            except OSError as exc:",
     START_SERVING_TOUCH3_BLOCK),
]
START_SERVING_ALLOWED_ADD = Counter(START_SERVING_TOUCH3_BLOCK)
START_SERVING_ALLOWED_REMOVE = Counter()


def apply_edits(lines, edits):
    """Apply the declared edit script to the installed line sequence,
    producing the one exact sequence the mirror must have. Anchors are
    pinned to surviving stdlib lines, so a declared block sitting
    anywhere else makes the constructed and mirror sequences differ.
    An anchor that is missing or matches more than once raises instead
    of guessing a position."""
    out = list(lines)
    for kind, anchor, block in edits:
        if kind == "insert_after_seq":
            n = len(anchor)
            hits = [i for i in range(len(out) - n + 1)
                    if out[i:i + n] == list(anchor)]
            if len(hits) != 1:
                raise AssertionError(
                    f"spec-diff anchor sequence matched {len(hits)} "
                    f"times (expected exactly 1): {anchor!r}")
            i = hits[0]
            out[i + n:i + n] = list(block)
            continue
        hits = out.count(anchor)
        if hits != 1:
            raise AssertionError(
                f"spec-diff anchor line matched {hits} times "
                f"(expected exactly 1): {anchor!r}")
        idx = out.index(anchor)
        if kind == "replace_first":
            out[idx:idx + 1] = list(block)
        elif kind == "insert_before":
            out[idx:idx] = list(block)
        else:
            raise AssertionError(f"unknown edit kind: {kind!r}")
    return out


class AnchorDriftTests(unittest.TestCase):
    def test_module_and_test_pins_agree(self):
        self.assertEqual(paf.EXPECTED_ANCHORS, EXPECTED_ANCHORS)

    def test_installed_stdlib_matches_pins(self):
        files = {
            "BaseProactorEventLoop._start_serving":
                proactor_events.__file__,
            "IocpProactor.accept": windows_events.__file__,
        }
        for qual, path in files.items():
            seg = segment_of(path, qual)
            digest = hashlib.sha256(seg.encode("utf-8")).hexdigest()
            self.assertEqual(digest, EXPECTED_ANCHORS[qual],
                             f"anchor drift for {qual} in {path}")


class SpecDiffTests(unittest.TestCase):
    def _check(self, installed_path, installed_qual, mirror_qual,
               edits, allowed_add, allowed_remove):
        installed_lines = normalised_lines(
            segment_of(installed_path, installed_qual))
        mirror_lines = normalised_lines(
            segment_of(paf.__file__, mirror_qual))
        installed = Counter(installed_lines)
        mirror = Counter(mirror_lines)
        added, removed = mirror - installed, installed - mirror
        # Multiset equality in both directions first, so a failure
        # names the offending lines: nothing undeclared, and every
        # declared touch actually present.
        self.assertEqual(added - allowed_add, Counter(),
                         f"undeclared additions: {added - allowed_add}")
        self.assertEqual(removed - allowed_remove, Counter(),
                         f"undeclared removals: {removed - allowed_remove}")
        self.assertEqual(allowed_add - added, Counter(),
                         f"declared touches missing: {allowed_add - added}")
        self.assertEqual(allowed_remove - removed, Counter(),
                         f"declared removals missing: "
                         f"{allowed_remove - removed}")
        # Positional lock: the mirror sequence must equal the sequence
        # constructed from the installed body by the declared edits at
        # their fixed anchors -- the order of the surviving lines AND
        # of the declared blocks themselves is pinned.
        expected = apply_edits(installed_lines, edits)
        if mirror_lines != expected:
            for i, (got, want) in enumerate(zip(mirror_lines, expected)):
                if got != want:
                    self.fail(
                        f"mirror sequence diverges at normalised line "
                        f"{i}: mirror has {got!r}, expected {want!r}")
            self.fail(
                f"mirror sequence has {len(mirror_lines)} normalised "
                f"lines, expected {len(expected)}")

    def test_accept_matches_stdlib_plus_declared_touches(self):
        self._check(windows_events.__file__, "IocpProactor.accept",
                    "FixedIocpProactor.accept",
                    ACCEPT_EDITS, ACCEPT_ALLOWED_ADD,
                    ACCEPT_ALLOWED_REMOVE)

    def test_start_serving_matches_stdlib_plus_declared_touch(self):
        self._check(proactor_events.__file__,
                    "BaseProactorEventLoop._start_serving",
                    "FixedProactorEventLoop._start_serving",
                    START_SERVING_EDITS, START_SERVING_ALLOWED_ADD,
                    START_SERVING_ALLOWED_REMOVE)


class TypeGateTests(unittest.TestCase):
    def setUp(self):
        self._policy = asyncio.get_event_loop_policy()
        self._installed = paf._INSTALLED
        self._count = paf._SWALLOWED.count
        self._env = os.environ.get("ORCHESTRATOR_PROACTOR_FIX")

    def tearDown(self):
        asyncio.set_event_loop_policy(self._policy)
        paf._INSTALLED = self._installed
        paf._SWALLOWED.count = self._count
        if self._env is None:
            os.environ.pop("ORCHESTRATOR_PROACTOR_FIX", None)
        else:
            os.environ["ORCHESTRATOR_PROACTOR_FIX"] = self._env

    def test_forced_anchor_mismatch_stays_stock_and_logs(self):
        os.environ["ORCHESTRATOR_PROACTOR_FIX"] = "on"
        broken = dict(paf.EXPECTED_ANCHORS)
        broken["IocpProactor.accept"] = "0" * 64
        with mock.patch.object(paf, "EXPECTED_ANCHORS", broken):
            with self.assertLogs(paf.logger, level="ERROR") as logs:
                result = paf.install_proactor_fix()
        self.assertFalse(result["installed"])
        self.assertEqual(result["reason"], "anchor_mismatch")
        self.assertIs(asyncio.get_event_loop_policy(), self._policy)
        self.assertFalse(paf.proactor_fix_active())
        self.assertTrue(any("anchor mismatch" in line
                            for line in logs.output))

    def test_normal_install_yields_fixed_types(self):
        os.environ["ORCHESTRATOR_PROACTOR_FIX"] = "on"
        result = paf.install_proactor_fix()
        self.assertTrue(result["installed"], result)
        loop = asyncio.get_event_loop_policy().new_event_loop()
        try:
            self.assertIs(type(loop), paf.FixedProactorEventLoop)
            self.assertIs(type(loop._proactor), paf.FixedIocpProactor)
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
