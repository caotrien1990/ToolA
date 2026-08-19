"""W7 (F020) — session-gate reachability contract.

The API session gate (``orchestrator_server`` ~L13512) refuses any mutating
tool it cannot resolve a project root for. Its resolution chain is::

    body["project_path"]
    -> find_project_root(_body_path_hint(body))     # file_path | path | paths[0]
    -> _resolve_project_from_page_id(page_id)
    -> _resolve_project_from_session_id(session_id)

F020 ("browser-mutation schema deadlock") was really this: a CA that builds a
payload from the schema's ``required`` list gets 400 PROJECT_PATH_REQUIRED,
"fixes" it by adding ``project_path``, and gets 400 UNSUPPORTED_FIELD because
the handler signature has no such parameter. Neither error names a way out.

Two invariants keep that from coming back:

1. **Reachable** — every gated tool must declare at least ONE field the chain
   can resolve from. A tool with none can never pass the gate and belongs in
   ``_NO_SESSION_REQUIRED`` instead.
2. **Honest ``required``** — a payload built from ``required`` alone should
   already carry a resolver, so the documented minimal call works. Tools that
   legitimately accept several alternatives (``close_session(session_id=...)``
   is the R64-D flow; forcing ``project_path`` into ``required`` would break
   it) are exempt via a frozen allowlist -- the gate answers those with
   MISSING_REQUIRED_FIELD + ``required_one_of``. The allowlist is a ratchet:
   fixing a tool means deleting its line, and no new name may be added
   without a matching one-of story.

Run:  ./venv312/Scripts/python.exe -m pytest tests/test_session_gate_required_contract.py -v
"""

import inspect
import unittest

import code_tools

try:
    import orchestrator_server as srv  # late registrations -> full catalog
    _SRV_AVAILABLE = True
    _SRV_REASON = ""
except Exception as _exc:  # pragma: no cover - env-dependent
    srv = None
    _SRV_AVAILABLE = False
    _SRV_REASON = f"orchestrator_server not importable: {type(_exc).__name__}: {_exc}"

# Fields the gate's resolution chain can turn into a project root. The
# _body_path_hint half is PROBED from the live function rather than copied, so
# this contract cannot drift from the implementation it guards.
def _probe_path_hint_fields():
    candidates = ("file_path", "path", "dst", "target_file", "src", "paths")
    found = []
    for key in candidates:
        probe = ["X"] if key == "paths" else "X"
        if srv._body_path_hint({key: probe}) == "X":
            found.append(key)
    return tuple(found)


_PATH_HINT_FIELDS = _probe_path_hint_fields() if _SRV_AVAILABLE else ()
RESOLVER_FIELDS = ("project_path", "page_id", "session_id") + _PATH_HINT_FIELDS

# Tools whose ``required`` does NOT by itself guarantee resolution because the
# project can legitimately come from an alternative id field. RATCHET: entries
# may be REMOVED, never added.
ONE_OF_ALLOWED = {
    "close_session",                    # R64-D: session_id is the documented flow
    "record_session_audit_checkpoint",  # same -- audits an existing session by id
}


@unittest.skipUnless(_SRV_AVAILABLE, _SRV_REASON)
class SessionGateRequiredContract(unittest.TestCase):
    """Every session-gated tool must be callable from its own schema."""

    @classmethod
    def setUpClass(cls):
        cls.gated = sorted(
            name for name in srv._MUTATING_TOOLS
            if name not in srv._NO_SESSION_REQUIRED
            and not cls._rescued_by_default(name)
        )
        cls.params = {}
        for schema in code_tools.TOOL_SCHEMAS:
            fn = schema.get("function") or {}
            name = fn.get("name")
            if name:
                cls.params[name] = fn.get("parameters") or {}

    @staticmethod
    def _rescued_by_default(tool):
        """Mirror the R44 guard at orchestrator_server L2565.

        Membership in _PROJECT_PATH_DEFAULTABLE is NOT enough: the injection
        only happens when the handler SIGNATURE accepts project_path. R43 put
        page-bound T4 tools in the set, then R44 added the signature guard
        because injecting the field tripped UNSUPPORTED_FIELD downstream. Those
        tools are therefore still fully gated and must stay in scope here --
        treating the whole set as rescued would let a real gap through.
        """
        if tool not in srv._PROJECT_PATH_DEFAULTABLE:
            return False
        fn = code_tools.TOOL_MAP.get(tool)
        if fn is None:
            return False
        try:
            return "project_path" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return False

    def _props_required(self, tool):
        params = self.params.get(tool) or {}
        return set(params.get("properties") or {}), set(params.get("required") or [])

    def test_path_hint_probe_found_the_known_fields(self):
        """The probe itself must work -- an empty tuple would void invariant 1."""
        for key in ("file_path", "path", "paths"):
            self.assertIn(key, _PATH_HINT_FIELDS,
                          f"_body_path_hint no longer resolves {key!r}")

    def test_gated_set_is_not_empty(self):
        """Guard against the sets silently emptying and voiding this whole file."""
        self.assertGreater(len(self.gated), 20, "session-gated tool set looks wrong")

    def test_every_gated_tool_declares_a_resolver_field(self):
        """Invariant 1 -- no tool may be permanently unreachable behind the gate."""
        unreachable = []
        for tool in self.gated:
            props, _ = self._props_required(tool)
            if not props:
                continue  # no schema captured -> covered by the parity contract
            if not props.intersection(RESOLVER_FIELDS):
                unreachable.append(tool)
        self.assertEqual(
            unreachable, [],
            "These tools are session-gated but declare no field the gate can "
            "resolve a project from, so every call 400s. Either add "
            f"project_path to the schema or exempt via _NO_SESSION_REQUIRED: {unreachable}",
        )

    def test_required_payload_carries_a_resolver(self):
        """Invariant 2 -- the minimal documented payload must pass the gate."""
        broken = []
        for tool in self.gated:
            if tool in ONE_OF_ALLOWED:
                continue
            props, required = self._props_required(tool)
            if not props:
                continue
            if not required.intersection(RESOLVER_FIELDS):
                broken.append(tool)
        self.assertEqual(
            broken, [],
            "A payload built from these tools' `required` list carries nothing "
            "the session gate can resolve, so the documented minimal call always "
            "returns 400 (F020). Add project_path to `required`, or add the tool "
            f"to ONE_OF_ALLOWED with a genuine alternative resolver: {broken}",
        )

    def test_one_of_allowlist_is_a_ratchet(self):
        """Allowlisted tools must still be real: gated, and offering an alternative."""
        for tool in sorted(ONE_OF_ALLOWED):
            with self.subTest(tool=tool):
                self.assertIn(
                    tool, self.gated,
                    f"{tool} is no longer session-gated -- drop it from ONE_OF_ALLOWED",
                )
                props, required = self._props_required(tool)
                self.assertFalse(
                    required.intersection(RESOLVER_FIELDS),
                    f"{tool} now REQUIRES a resolver -- drop it from ONE_OF_ALLOWED",
                )
                self.assertGreaterEqual(
                    len(props.intersection(RESOLVER_FIELDS)), 2,
                    f"{tool} must offer >=2 resolver fields to justify a one-of contract",
                )


if __name__ == "__main__":
    unittest.main()
