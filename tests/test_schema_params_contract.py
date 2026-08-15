"""Schema-params contract test for the orchestrator tool registry.

Validates the per-tool JSON-schema ``parameters`` contract:

1. Lockstep: ``TOOL_MAP`` == ``TOOL_SCHEMAS`` == ``TOOL_PHASES`` (name sets),
   no duplicate schema names (``validate_registry`` zero drift).
2. Every schema's ``parameters.type == "object"``.
3. ``required`` is a subset of ``properties`` for every schema.
4. Alias canonical targets exist in schema properties; the single known legacy
   collision ``write_new_file.content`` (legacy alias source that is also a
   declared property) is whitelisted — see ALIAS_CONTRACT §5.1.
5. Ratchet: no NEW properties missing ``description`` / ``type`` beyond the
   historically-accepted counts (upper-bound ratchet, less brittle than
   snapshotting the full 278-line list).

``orchestrator_server`` is imported lazily because (a) it owns ``_ARG_ALIASES``
and (b) its import triggers late ``register_tool()`` calls that lift the
registry from 187 -> 194 tools. If it cannot be imported, the whole module
skips via ``skipUnless`` instead of failing collection.

Run:  ./venv312/Scripts/python.exe -m pytest tests/test_schema_params_contract.py -v
"""

import unittest

import code_tools

try:
    import orchestrator_server as srv  # triggers late registrations -> 194 tools
    _SRV_AVAILABLE = True
    _SRV_REASON = ""
except Exception as _exc:  # pragma: no cover - env-dependent
    srv = None
    _SRV_AVAILABLE = False
    _SRV_REASON = f"orchestrator_server not importable: {type(_exc).__name__}: {_exc}"

from orchestrator.core.registry import validate_registry

# Whole-module skip guard: every assertion below depends on the full 194-tool
# registry that only exists after ``orchestrator_server`` late registration.
_SKIP_IF_NO_SRV = unittest.skipUnless(_SRV_AVAILABLE, _SRV_REASON or "orchestrator_server unavailable")


# ── helpers ──────────────────────────────────────────────────────────────────

def _schemas() -> list[dict]:
    """Return the list of inner ``function`` dicts from ``TOOL_SCHEMAS``."""
    out: list[dict] = []
    for item in code_tools.TOOL_SCHEMAS:
        fn = (item.get("function") or {}) if isinstance(item, dict) else {}
        if fn.get("name"):
            out.append(fn)
    return out


def _schema_props() -> dict[str, set[str]]:
    """Build {tool_name: set(property_names)} from ``TOOL_SCHEMAS``."""
    out: dict[str, set[str]] = {}
    for fn in _schemas():
        props = (fn.get("parameters") or {}).get("properties", {})
        out[fn["name"]] = set(props.keys())
    return out


_SCHEMAS = _schemas()
_SCHEMAS_PROPS = _schema_props()


# ── 1. Registry lockstep (map == schemas == phases, no dupes) ────────────────

@_SKIP_IF_NO_SRV
class TestRegistryLockstep(unittest.TestCase):
    """TOOL_MAP / TOOL_SCHEMAS / TOOL_PHASES must stay in lockstep."""

    def test_validate_registry_zero_drift(self):
        issues = validate_registry(
            code_tools.TOOL_MAP, code_tools.TOOL_SCHEMAS, code_tools.TOOL_PHASES)
        self.assertEqual(issues, [], f"Registry drift: {issues}")

    def test_name_sets_equal(self):
        schema_names = {fn["name"] for fn in _SCHEMAS}
        self.assertEqual(set(code_tools.TOOL_MAP), schema_names,
                         "TOOL_MAP names != TOOL_SCHEMAS names")
        self.assertEqual(set(code_tools.TOOL_MAP), set(code_tools.TOOL_PHASES),
                         "TOOL_MAP names != TOOL_PHASES names")

    def test_no_duplicate_schema_names(self):
        names = [fn["name"] for fn in _SCHEMAS]
        dupes = sorted({n for n in names if names.count(n) > 1})
        self.assertEqual(dupes, [], f"Duplicate schema names: {dupes}")


# ── 2. parameters.type == "object" ───────────────────────────────────────────

@_SKIP_IF_NO_SRV
class TestParametersTypeObject(unittest.TestCase):

    def test_every_schema_parameters_type_is_object(self):
        bad = []
        for fn in _SCHEMAS:
            params = fn.get("parameters") or {}
            if params.get("type") != "object":
                bad.append((fn["name"], params.get("type")))
        self.assertEqual(bad, [], f"Schemas with parameters.type != 'object': {bad}")


# ── 3. required ⊆ properties ─────────────────────────────────────────────────

@_SKIP_IF_NO_SRV
class TestRequiredSubsetProperties(unittest.TestCase):

    def test_required_fields_exist_in_properties(self):
        bad = []
        for fn in _SCHEMAS:
            params = fn.get("parameters") or {}
            props = set((params.get("properties") or {}).keys())
            for r in (params.get("required") or []):
                if r not in props:
                    bad.append(f"{fn['name']}: required {r!r} not in properties")
        self.assertEqual(bad, [], f"required not subset of properties: {bad}")


# ── 4. Alias canonical targets exist; known legacy collision whitelisted ──────

@_SKIP_IF_NO_SRV
class TestAliasCanonicalAndLegacyCollision(unittest.TestCase):
    """Every alias canonical field must be a declared schema property.

    Conversely, an alias legacy/source name that is ALSO a declared property is
    a redundant collision. The single known case (``write_new_file.content``) is
    whititelisted: ``content`` is both a schema property and the legacy alias
    source for ``new_content``; ``_normalize_body`` renames it before schema
    validation, so the ``content`` property is effectively dead code
    (ALIAS_CONTRACT §5.1). The subset check tolerates future shrinkage (fixing
    the collision) but fails on any NEW collision.
    """

    # (tool, legacy_alias_source) pairs allowed to also be declared properties.
    # 2026-08-16 (A15): the search-alias canonical flipped to name/symbol/pattern
    # (see orchestrator_server._ARG_ALIASES + registry_data.py, where `query` is a
    # declared property described as "Alias for name/symbol/pattern"). `query` is
    # now the DELIBERATE dual-spelling: it is both an alias source (folded to the
    # canonical) AND a documented schema property so workers reading /api/tools can
    # send either. The collision moved from name/symbol/pattern onto `query`.
    KNOWN_LEGACY_COLLISIONS = {
        ("write_new_file", "content"),
        ("find_symbol", "query"),
        ("find_callers", "query"),
        ("regex_search", "query"),
    }

    def test_alias_canonical_targets_exist_in_properties(self):
        missing = []
        for tool, aliases in srv._ARG_ALIASES.items():
            tprops = _SCHEMAS_PROPS.get(tool, set())
            for legacy, canonical in aliases.items():
                if canonical not in tprops:
                    missing.append(f"{tool}: {legacy}->{canonical}: canonical NOT in properties")
        self.assertEqual(missing, [], f"Canonical fields missing from schema: {missing}")

    def test_no_new_alias_legacy_collisions(self):
        collisions = set()
        for tool, aliases in srv._ARG_ALIASES.items():
            tprops = _SCHEMAS_PROPS.get(tool, set())
            for legacy in aliases:
                if legacy in tprops:
                    collisions.add((tool, legacy))
        self.assertLessEqual(
            collisions, self.KNOWN_LEGACY_COLLISIONS,
            f"New alias legacy/source collisions (legacy name is also a schema "
            f"property): {sorted(collisions - self.KNOWN_LEGACY_COLLISIONS)}. "
            f"Known/whitelisted: {sorted(self.KNOWN_LEGACY_COLLISIONS)} "
            f"(ALIAS_CONTRACT §5.1).",
        )


# ── 5. Ratchet: no NEW properties missing description / type ─────────────────

# Upper-bound ratchet constants (fresh discovery, 2026-07-08, OpeC5). These are
# the historically-accepted counts of schema properties that lack a
# "description" / "type" field, measured across all 194 registered tools. The
# assertion is `actual <= constant`: existing gaps are tolerated, but any NEW
# gap (a freshly added property without description/type) flips the test red and
# forces the author to either add the field or, if intentional, bump the
# constant with a reason. A count ratchet is deliberately used instead of
# snapshotting the full 278-line list — it stays robust when properties are
# renamed/reordered/removed, and only alerts on net-new gaps.
MISSING_DESCRIPTION_UPPER_BOUND = 282  # S4-bump 2026-08-08: +4 new from S4 tools sample_data_file(mode,project_path) + read_chunks(by,project_path)
MISSING_TYPE_UPPER_BOUND = 1


@_SKIP_IF_NO_SRV
class TestSchemaParamsRatchet(unittest.TestCase):

    def _missing_field_count(self, field: str) -> int:
        count = 0
        for fn in _SCHEMAS:
            props = (fn.get("parameters") or {}).get("properties") or {}
            for _pname, pdef in props.items():
                if not isinstance(pdef, dict) or field not in pdef:
                    count += 1
        return count

    def test_no_new_properties_missing_description(self):
        actual = self._missing_field_count("description")
        self.assertLessEqual(
            actual, MISSING_DESCRIPTION_UPPER_BOUND,
            f"Properties missing 'description' grew to {actual} (ratchet was "
            f"{MISSING_DESCRIPTION_UPPER_BOUND}). Add descriptions to the new "
            f"properties, or bump the ratchet with a reason.")

    def test_no_new_properties_missing_type(self):
        actual = self._missing_field_count("type")
        self.assertLessEqual(
            actual, MISSING_TYPE_UPPER_BOUND,
            f"Properties missing 'type' grew to {actual} (ratchet was "
            f"{MISSING_TYPE_UPPER_BOUND}). Add a type to the new property, or "
            f"bump the ratchet with a reason.")


if __name__ == "__main__":
    unittest.main()
