"""A31 (remainder): agents_registry.json has THREE readers; only one handled BOM.

WHY each test exists (behavior, not shape):
- Three code paths decode agents_registry.json with different encodings/error
  policies:
    1) code_tools._load_agents_registry        (fail-safe -> {"agents": {}})
    2) ca_policy_matrix.open_session_policy_payload (fail-loud -> raises)
    3) tools_v15.normalize_ca_registration    (WRITE path, fail-loud -> ERROR)
  Before the fix, #1 and #3 read with encoding="utf-8", so a UTF-8 BOM makes
  json.loads raise "Unexpected UTF-8 BOM". #1 then silently returns an EMPTY
  registry (every agent vanishes); #3 returns INTERNAL_ERROR. Only #2 used
  utf-8-sig and stayed correct.
- These tests pin the *unified* behavior: on a BOM-prefixed file all three read
  paths agree on the same agent set. They MUST also prove the fix does not change
  each caller's ERROR contract (fail-safe stays fail-safe; fail-loud stays loud),
  because #3 is a read-modify-write path and must never silently overwrite a
  corrupt file with an empty registry.
"""

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import code_tools
import orchestrator.core.ca_policy_matrix as capm
import tools_v15

_SAMPLE = {
    "mcp_command": "python.exe",
    "agents": {
        "testx": {
            "id": "testx",
            "route_id": "testx",
            "provider": "claude",
            "mode": "A",
            "transport_policy": "a_first_mcp_t4_ok",
        }
    },
}


def _write(path: Path, obj: dict, *, bom: bool) -> None:
    enc = "utf-8-sig" if bom else "utf-8"
    path.write_text(json.dumps(obj), encoding=enc)


class _PatchRegistryBase(unittest.TestCase):
    bom = True

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "agents_registry.json"
        _write(self.path, _SAMPLE, bom=self.bom)
        # #1 and #3 read via code_tools._AGENTS_REGISTRY_PATH; #2 via
        # ca_policy_matrix._AGENT_REGISTRY_PATH. Patch both to the same file.
        self._p1 = mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", self.path)
        self._p2 = mock.patch.object(capm, "_AGENT_REGISTRY_PATH", self.path)
        self._p1.start()
        self._p2.start()

    def tearDown(self):
        self._p1.stop()
        self._p2.stop()
        self.tmp.cleanup()

    def _read1(self):
        return code_tools._load_agents_registry()

    def _read2(self):
        return capm.open_session_policy_payload(agent_id="testx")

    def _read3(self):
        raw = asyncio.run(tools_v15.normalize_ca_registration(dry_run=True))
        return json.loads(raw)


class TestBomAllThreeAgree(_PatchRegistryBase):
    bom = True

    def test_reader1_load_agents_registry_sees_agent_despite_bom(self):
        reg = self._read1()
        self.assertIn("testx", reg.get("agents", {}),
                      "reader #1 lost the agent on a BOM file (utf-8 decode swallowed)")

    def test_reader2_session_payload_ok_on_bom(self):
        payload = self._read2()
        # zcode-style record resolves a transport policy; testx is mode A desktop.
        self.assertEqual(payload["agent_transport_policy"], "a_first_mcp_t4_ok")

    def test_reader3_normalize_ok_on_bom(self):
        out = self._read3()
        self.assertTrue(out.get("ok"), f"reader #3 failed on BOM file: {out}")
        self.assertGreaterEqual(out.get("agent_count", 0), 1)

    def test_all_three_agree_on_agent_set(self):
        agents1 = set(self._read1().get("agents", {}))
        # #2 does not expose the full set; assert it resolved the record we passed.
        payload = self._read2()
        agents3 = set(self._read3().get("preview", {}))
        self.assertEqual(agents1, {"testx"})
        self.assertEqual(agents3, {"testx"})
        self.assertTrue(payload["agent_transport_policy"])


class TestErrorContractsPreserved(unittest.TestCase):
    """The unification must NOT change each caller's error policy."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "agents_registry.json"
        # deliberately corrupt (valid neither as utf-8 nor utf-8-sig JSON)
        self.path.write_text("{ this is not json", encoding="utf-8")
        self._p1 = mock.patch.object(code_tools, "_AGENTS_REGISTRY_PATH", self.path)
        self._p2 = mock.patch.object(capm, "_AGENT_REGISTRY_PATH", self.path)
        self._p1.start()
        self._p2.start()

    def tearDown(self):
        self._p1.stop()
        self._p2.stop()
        self.tmp.cleanup()

    def test_reader1_stays_fail_safe_on_corrupt(self):
        # #1 must still swallow and return the empty-but-safe shape.
        self.assertEqual(code_tools._load_agents_registry(), {"agents": {}})

    def test_reader2_stays_fail_loud_on_corrupt(self):
        # #2 must still raise (session payload never masked a corrupt registry).
        with self.assertRaises(Exception):
            capm.open_session_policy_payload(agent_id="testx")

    def test_reader3_stays_fail_loud_never_writes_empty(self):
        # #3 is a WRITE path: on corrupt input it must error, NOT normalize {}.
        out = json.loads(asyncio.run(tools_v15.normalize_ca_registration(dry_run=True)))
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error_name"), "INTERNAL_ERROR")


if __name__ == "__main__":
    unittest.main()
