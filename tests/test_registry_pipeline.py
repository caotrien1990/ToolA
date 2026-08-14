import unittest

import code_tools
import orchestrator_server  # noqa: F401 — triggers late registrations (council_message)

from orchestrator.core.registry import RegistryDriftError, register_tool, validate_registry


class TestRegistryPipeline(unittest.TestCase):
    def test_live_registry_has_zero_drift(self):
        # The gate that caught the original council_message x_phase bug:
        # every TOOL_MAP entry must have a schema whose x_phase matches TOOL_PHASES.
        issues = validate_registry(
            code_tools.TOOL_MAP, code_tools.TOOL_SCHEMAS, code_tools.TOOL_PHASES)
        self.assertEqual(issues, [])

    def test_register_tool_stamps_phase_and_aliases(self):
        tool_map, schemas, phases, arg_aliases = {}, [], {}, {}
        register_tool(
            name="demo_tool", handler=lambda: None, phase="T1",
            tool_map=tool_map, tool_schemas=schemas, tool_phases=phases,
            arg_aliases=arg_aliases, phase_labels=code_tools.PHASE_LABELS,
            aliases={"msg": "text"},
            schema={"description": "d", "parameters": {"type": "object", "properties": {}}},
        )
        fn = schemas[0]["function"]
        self.assertEqual(fn["x_phase"], "T1")
        self.assertEqual(fn["x_phase_label"], code_tools.PHASE_LABELS["T1"])
        self.assertEqual(phases["demo_tool"], "T1")
        self.assertEqual(arg_aliases["demo_tool"], {"msg": "text"})
        self.assertEqual(validate_registry(tool_map, schemas, phases), [])
        # Re-registration with the same handler is idempotent (no dup schema).
        register_tool(
            name="demo_tool", handler=tool_map["demo_tool"], phase="T1",
            tool_map=tool_map, tool_schemas=schemas, tool_phases=phases,
            schema={"description": "d2", "parameters": {"type": "object", "properties": {}}},
        )
        self.assertEqual(len(schemas), 1)

    def test_register_tool_rejects_phaseless_and_conflicts(self):
        tool_map, schemas, phases = {}, [], {}
        with self.assertRaises(RegistryDriftError):
            register_tool(name="x", handler=lambda: None, phase="",
                          tool_map=tool_map, tool_schemas=schemas, tool_phases=phases,
                          schema={"parameters": {}})
        register_tool(name="x", handler=lambda: None, phase="T1",
                      tool_map=tool_map, tool_schemas=schemas, tool_phases=phases,
                      schema={"parameters": {}})
        with self.assertRaises(RegistryDriftError):
            register_tool(name="x", handler=lambda: 1, phase="T1",
                          tool_map=tool_map, tool_schemas=schemas, tool_phases=phases,
                          schema={"parameters": {}})


if __name__ == "__main__":
    unittest.main()
