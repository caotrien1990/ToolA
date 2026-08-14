"""Live-adapter regression for LIVE-GAP-01 (2026-08-10): the find_callers
alias must route through the dispatcher's post-S4 mode injection and return
isolated callers — not fail with UNSUPPORTED_FIELD.

WHY: 0443b67 injected ``mode='callers'`` inside ``_normalize_body``, which
runs BEFORE S4 (public-schema field validation). Because the find_callers
public schema deliberately has no ``mode`` property (it is internal
delegation metadata consumed by search_code), S4 rejected every
schema-valid find_callers call with ``UNSUPPORTED_FIELD: mode``. Verified
live against the running orchestrator on 2026-08-10. Direct
``search_code(mode='callers')`` worked; only the alias delegation was
broken.

This test exercises the FULL dispatcher pipeline (normalize -> S4 -> post-S4
mode default -> search_code handler) on a real throwaway project, asserting:
  1) The alias returns ``include='callers'`` with isolated callers — NOT
     ``UNSUPPORTED_FIELD``.
  2) A caller-supplied ``mode`` field is still rejected by S4 (the alias's
     public contract does not expose mode).
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path


class TestFindCallersLiveAdapterLiveGap01(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "mod.py").write_text(
            "def target_fn(x):\n"
            "    return x + 1\n",
            encoding="utf-8",
        )
        (self.root / "use_mod.py").write_text(
            "import mod\n"
            "def caller():\n"
            "    return mod.target_fn(42)\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _simulate_dispatcher_pipeline(self, body: dict) -> dict:
        """Run the alias through the same pipeline the live dispatcher uses.

        normalize -> S4 field filter -> post-S4 mode default -> search_code
        handler. We invoke the handler directly (the registry maps
        find_callers to search_code) rather than spinning up aiohttp, but
        the body transformations exactly match the live code path.
        """
        import code_tools as ct
        import orchestrator_server

        # Step 1: normalize (must NOT inject mode after LIVE-GAP-01 fix).
        normalized, applied = orchestrator_server._normalize_body(
            "find_callers", dict(body)
        )

        # Step 2: S4 — filter body against public-schema allowed params,
        # exactly as the dispatcher does. If anything is unsupported, mirror
        # the dispatcher's UNSUPPORTED_FIELD response.
        registry_params = (
            orchestrator_server._schema_for_tool("find_callers").get(
                "parameters"
            )
            or {}
        )
        registry_props = registry_params.get("properties") or {}
        allowed = set(registry_props)
        unsupported = sorted(
            k for k in normalized.keys() if k not in allowed
        )
        if unsupported:
            return {
                "ok": False,
                "error_name": "UNSUPPORTED_FIELD",
                "error": (
                    f"Unsupported field(s) for 'find_callers': "
                    f"{', '.join(unsupported)}"
                ),
                "unsupported_fields": unsupported,
            }

        # Step 3: post-S4 dispatcher-side mode default (LIVE-GAP-01).
        orchestrator_server._apply_alias_mode_default_post_s4(
            "find_callers", normalized, applied
        )

        # Step 4: invoke the same handler the registry maps find_callers to.
        handler = ct.TOOL_MAP["find_callers"]
        raw = asyncio.run(handler(**normalized))
        return json.loads(raw)

    def test_find_callers_alias_returns_isolated_callers_not_unsupported_field(
        self,
    ) -> None:
        data = self._simulate_dispatcher_pipeline(
            {"symbol": "target_fn", "project_path": str(self.root)}
        )
        self.assertNotEqual(
            data.get("error_name"),
            "UNSUPPORTED_FIELD",
            f"LIVE-GAP-01 regression: alias rejected as UNSUPPORTED_FIELD: {data}",
        )
        self.assertIn(
            "callers",
            data,
            f"alias must return isolated callers; got {data}",
        )
        self.assertEqual(data.get("include"), "callers")
        self.assertNotIn(
            "results",
            data,
            f"definition results must not leak into callers response: {data}",
        )
        files = {c["file"] for c in data.get("callers", [])}
        self.assertIn(
            "use_mod.py", files, f"genuine caller missing: files={files}"
        )

    def test_find_callers_alias_rejects_user_supplied_mode(self) -> None:
        """A user-supplied ``mode`` on find_callers must still be rejected
        by S4 — the alias's public contract deliberately does not expose
        mode. This preserves the invariant that user intent cannot silently
        reroute the alias to a different search_code branch.
        """
        data = self._simulate_dispatcher_pipeline(
            {
                "symbol": "target_fn",
                "project_path": str(self.root),
                "mode": "text",  # user-supplied; must be rejected
            }
        )
        self.assertEqual(
            data.get("error_name"),
            "UNSUPPORTED_FIELD",
            f"user-supplied mode must be rejected; got {data}",
        )
        self.assertIn("mode", data.get("unsupported_fields", []))


if __name__ == "__main__":
    unittest.main()
