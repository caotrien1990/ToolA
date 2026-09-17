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
   historically-accepted counts (an upper-bound count ratchet, less brittle than
   snapshotting the whole gap list).
6. Positive controls: each lockstep predicate is ALSO driven with deliberately
   drifted input, because a gate that has only ever been shown a healthy registry
   has not been shown to be capable of failing -- see ``TestRegistryLockstep``.

``orchestrator_server`` is imported lazily because (a) it owns ``_ARG_ALIASES`` and
(b) its import triggers late ``register_tool()`` calls that add to the registry. How
many tools that ends up being is deliberately NOT written into this docstring: ask
``len(code_tools.TOOL_MAP)``. The figure this text used to carry went stale across
three waves of tool additions while every assertion below stayed green, because the
assertions read the live registry and only the prose was lying. If
``orchestrator_server`` cannot be imported, the whole module skips via
``skipUnless`` instead of failing collection.

Run:  ./venv312/Scripts/python.exe -m pytest tests/test_schema_params_contract.py -v
"""

import unittest

import code_tools

try:
    import orchestrator_server as srv  # import triggers the late register_tool() calls
    _SRV_AVAILABLE = True
    _SRV_REASON = ""
except Exception as _exc:  # pragma: no cover - env-dependent
    srv = None
    _SRV_AVAILABLE = False
    _SRV_REASON = f"orchestrator_server not importable: {type(_exc).__name__}: {_exc}"

from orchestrator.core.registry import validate_registry

# Whole-module skip guard: every assertion below depends on the registry as it exists
# only AFTER ``orchestrator_server`` late registration. How many tools that is stays
# unwritten here on purpose -- ask ``len(code_tools.TOOL_MAP)``.
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

    # -- positive controls ------------------------------------------- A32 / A31-Q1 B1
    # Every assertion above describes a HEALTHY registry, so all three have only ever
    # been seen green. Driven with real drift on 2026-09-17, two of them turned out not
    # to be able to fail in the direction that matters most.

    def test_validate_registry_cannot_see_a_name_missing_from_tool_map(self):
        """Pins a MEASURED one-way gap so nobody reads this validator as lockstep.

        ``validate_registry`` iterates ``for n in tool_map``: a name present in
        TOOL_SCHEMAS or TOOL_PHASES but ABSENT from TOOL_MAP is unreachable by
        construction. Both drifts below were driven for real and both returned ``[]``.

        That is the expensive direction. ``TOOL_MAP`` is built by
        ``build_tool_map(BASE_TOOL_MAP_SPECS, globals())``, which resolves handlers by
        STRING through ``globals()`` -- so a typo'd or renamed handler silently drops
        the TOOL_MAP entry while the schema and the phase stay behind, which is exactly
        the shape this validator is blind to. The gate that actually covers it is
        ``test_name_sets_equal`` below, NOT this function.

        This test is deliberately an assertion of the CURRENT behaviour, not of the
        desired behaviour: it exists so a future reader cannot mistake
        ``validate_registry() == []`` for a lockstep proof, and so that widening the
        validator arrives with a failing test attached instead of as a silent change.
        """
        short_map = dict(code_tools.TOOL_MAP)
        short_map.pop(sorted(short_map)[0])
        self.assertEqual(
            validate_registry(short_map, code_tools.TOOL_SCHEMAS, code_tools.TOOL_PHASES),
            [],
            "validate_registry now reports a TOOL_MAP-side deletion -- that is an "
            "IMPROVEMENT, not a regression: delete this test and say so in the commit")

        ghost_phases = dict(code_tools.TOOL_PHASES)
        ghost_phases["zz_ghost_tool_not_in_tool_map"] = "T1"
        self.assertEqual(
            validate_registry(code_tools.TOOL_MAP, code_tools.TOOL_SCHEMAS, ghost_phases),
            [],
            "same one-way gap, entered from the TOOL_PHASES side")

    def test_validate_registry_does_report_the_drift_it_can_see(self):
        # The other half of the control: the predicate is not simply inert.
        duped = list(code_tools.TOOL_SCHEMAS) + [code_tools.TOOL_SCHEMAS[0]]
        issues = validate_registry(
            code_tools.TOOL_MAP, duped, code_tools.TOOL_PHASES)
        self.assertTrue(any("duplicate schema" in i for i in issues),
                        f"duplicate schema name went unreported: {issues}")

    def test_name_sets_equal_is_what_catches_a_dropped_tool_map_entry(self):
        # Control for test_name_sets_equal: drive it with the two drifts
        # validate_registry cannot see, and show set equality does see both.
        schema_names = {fn["name"] for fn in _SCHEMAS}
        short_map = dict(code_tools.TOOL_MAP)
        short_map.pop(sorted(short_map)[0])
        self.assertNotEqual(set(short_map), schema_names,
                            "a dropped TOOL_MAP entry must break set equality")

        ghost_phases = dict(code_tools.TOOL_PHASES)
        ghost_phases["zz_ghost_tool_not_in_tool_map"] = "T1"
        self.assertNotEqual(set(code_tools.TOOL_MAP), set(ghost_phases),
                            "a ghost TOOL_PHASES entry must break set equality")


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

# Upper-bound ratchet constants (first measured 2026-07-08, OpeC5). These are the
# accepted counts of schema properties that lack a "description" / "type" field,
# measured across every registered tool -- ask the registry how many that is rather
# than writing the number into this comment, where it rots. The assertion is
# `actual <= constant`: existing gaps are tolerated, but any NEW gap (a freshly added
# property without description/type) flips the test red and forces the author to
# either add the field or, if intentional, bump the constant with a reason. A count
# ratchet is deliberately used instead of snapshotting the gap list itself -- it stays
# robust when properties are renamed/reordered/removed, and only alerts on net-new gaps.
#
# A32 2026-09-17: tightened 282 -> 278, which is the measured actual. WHY those 4 units
# of slack existed is NOT measured, and is deliberately not guessed at here; what is
# measured is that they were slack, and slack is the one thing a ratchet cannot carry --
# four new properties with no description could have landed without ever turning this
# red. Bump it again only with a named reason, and re-tighten once that reason is spent.
MISSING_DESCRIPTION_UPPER_BOUND = 278
MISSING_TYPE_UPPER_BOUND = 1  # actual is also 1: already tight, no slack to give back


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
