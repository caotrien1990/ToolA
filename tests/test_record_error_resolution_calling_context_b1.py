"""B1: the dispatcher injects ``calling_context``, then rejects its own field.

``_dispatch_tool_call_request`` writes ``body["calling_context"]`` for
``record_error_resolution`` once the host checkpoint validates, because the
handler needs a *trusted* identity and a payload-supplied one would be
caller-controlled. A few hundred lines later, S4 compares ``body`` against the
registry properties -- and ``calling_context`` is deliberately NOT one of them
(the schema description says "Transport fields are intentionally excluded from
this payload schema", and a contract test asserts their absence). So the tool
answers 400 UNSUPPORTED_FIELD naming a field the caller never sent and cannot
remove::

    error_name : UNSUPPORTED_FIELD
    error      : Unsupported field(s) for 'record_error_resolution': calling_context
    ca_next_action: remove_unsupported_field

That last line is the P11b shape again: a recovery hint pointing at a dead end.
The caller obeys it, changes nothing, and gets the identical 400 -- until
``_DUPLICATE_FAILURE_STATE`` upgrades it to 409 DUPLICATE_RETRY_BLOCKED.

Two notes on how this was arbitrated, because the first diagnosis was wrong.

1. The room's audit read the two line numbers and concluded 400. The existing
   contract test at ``test_orchestrator_server_contract.py`` actually fails at
   **403 SESSION_OWNERSHIP_REQUIRED**, never reaching S4: it seeded an in-memory
   cache, but ``_task_checkpoint_for_identity`` reads SQLite via
   ``_task_checkpoint_load``, the sole store. So the 400 was a
   plausible-but-unproven claim standing on a red test that is red for an
   unrelated reason.
2. Seeding the AUTHORITATIVE store (``store._task_checkpoint_save``) instead
   satisfies the 403 gate, the request reaches S4, and the 400 reproduces
   exactly. Both defects are real and independent; this file pins the S4 one,
   which is the one that breaks the happy path for every caller.

The fix must keep the security property intact: a caller-supplied
``calling_context`` must still never reach the handler. Stashing the injected
value across S4 (the pattern already used for ``_normalized_paths`` and
``_compat_meta``) preserves that, because the stash happens only on the
server-validated branch.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp.test_utils import make_mocked_request  # noqa: E402

import code_tools  # noqa: E402
import orchestrator_server  # noqa: E402
from orchestrator.session import store  # noqa: E402

_TOOL = "record_error_resolution"
_CAPABILITY = "issued-capability"
_SESSION = "host-session"


def _body_reader(payload: dict):
    data = json.dumps(payload).encode("utf-8")

    async def read():
        return data

    return read


class CallingContextRoundTripTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Isolate the session DB. Setting ORCHESTRATOR_SESSIONS_DB does NOT work:
        # store._SESSIONS_DB is bound at import time, and every entry point calls
        # code_tools._sync_session_db_alias(), which copies code_tools._SESSIONS_DB
        # over the store's module global. So code_tools is the one that must be
        # patched -- test_session_store.py:406 uses the same seam. Measured the hard
        # way: the env-var version of this file wrote a live 'host-session'
        # checkpoint row into the REAL .orchestrator_cache/sessions.db (removed),
        # which then leaked into an unrelated contract test in the same process.
        self._tmpdir = Path(tempfile.mkdtemp(prefix="b1_"))
        db = self._tmpdir / "sessions.db"
        self._patches = [
            mock.patch.object(code_tools, "_SESSIONS_DB", db),
            mock.patch.object(store, "_SESSIONS_DB", db),
        ]
        for patch in self._patches:
            patch.start()
        code_tools._sync_session_db_alias()
        store._init_session_db()

        # The AUTHORITATIVE checkpoint store (SQLite) is the sole source of
        # truth -- see the module docstring.
        store._task_checkpoint_save(_SESSION, {
            "agent_id": "Ope",
            "task_epoch": 4,
            "prompt_hash": "issued-hash",
            "owner_capability_sha256": hashlib.sha256(
                _CAPABILITY.encode("utf-8")).hexdigest(),
            "owner_identity_id": "ident-b1",
        })
        self.seen: list = []

        async def handler(target_session_id, resolves_event_id, resolution_kind,
                          calling_context=None, **_kw):
            self.seen.append(calling_context)
            return json.dumps({"ok": True, "resolution_event_id": 42})

        self._original = code_tools.TOOL_MAP[_TOOL]
        code_tools.TOOL_MAP[_TOOL] = handler

    def tearDown(self) -> None:
        code_tools.TOOL_MAP[_TOOL] = self._original
        orchestrator_server._DUPLICATE_FAILURE_STATE.clear()
        for patch in reversed(self._patches):
            patch.stop()
        # STT-27 (B032): setUp wrote a real checkpoint row into self._tmpdir/
        # sessions.db, which lazily started the process-global SessionWriter.
        # Its drain-loop sqlite conn pins the file, so the rmtree below
        # silently fails on Windows (WinError 32) and leaks 6 b1_* dirs per
        # run. close_writer() also drops thread-local read conns — same
        # contract as _swap_sessions_db in test_orchestrator_server_contract.
        store.close_writer()
        code_tools._sync_session_db_alias()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    async def _dispatch(self, payload: dict, *, headers: dict | None = None):
        request = make_mocked_request(
            "POST", "/api/call/" + _TOOL,
            headers=headers if headers is not None else {
                "X-Session-Id": _SESSION, "X-Agent-Id": "Ope",
                "X-Task-Epoch": "4", "X-Prompt-Hash": "issued-hash",
                "X-Task-Capability": _CAPABILITY,
            },
            match_info={"tool_name": _TOOL},
        )
        request.read = _body_reader(payload)
        response = await orchestrator_server._dispatch_tool_call_request(request)
        return response, json.loads(response.text)

    def _valid_payload(self) -> dict:
        return {"target_session_id": _SESSION, "resolves_event_id": 7,
                "resolution_kind": "recovered"}

    async def test_happy_path_is_not_rejected_for_a_field_the_server_injected(self):
        response, payload = await self._dispatch(self._valid_payload())

        self.assertNotEqual(
            payload.get("error_name"), "UNSUPPORTED_FIELD",
            msg=f"rejects its own injected field: {payload}")
        self.assertEqual(response.status, 200, msg=payload)

    async def test_the_validated_identity_still_reaches_the_handler(self):
        # Weaker-but-necessary companion: a fix that simply stopped injecting
        # would turn the 400 green while silently disarming the ownership check
        # inside the handler, which refuses to write without validated context.
        await self._dispatch(self._valid_payload())

        self.assertEqual(len(self.seen), 1, msg=self.seen)
        self.assertEqual(self.seen[0], {
            "validated": True, "session_id": _SESSION, "agent_id": "Ope",
            "task_epoch": 4, "prompt_hash": "issued-hash",
        }, msg=self.seen)

    async def test_a_caller_supplied_calling_context_is_still_refused(self):
        # The security property. calling_context is not a public field; a client
        # that sends one must not have it honoured, and must not be able to forge
        # `validated: true`.
        response, payload = await self._dispatch({
            **self._valid_payload(),
            "calling_context": {"validated": True, "session_id": "attacker",
                                "agent_id": "Ope", "task_epoch": 4,
                                "prompt_hash": "issued-hash"},
        })

        self.assertEqual(response.status, 400, msg=payload)
        self.assertEqual(payload.get("error_name"), "UNSUPPORTED_FIELD", msg=payload)
        self.assertIn("calling_context", payload.get("unsupported_fields") or [],
                      msg=payload)
        self.assertEqual(self.seen, [], msg="handler must never see a forged context")

    async def test_calling_context_stays_absent_from_the_public_schema(self):
        # Pins the invariant the fix must not "solve" by widening the contract:
        # adding calling_context to the registry properties would make S4 accept
        # a caller-supplied one, which is exactly the test above.
        props = (orchestrator_server._schema_for_tool(_TOOL).get("parameters")
                 or {}).get("properties") or {}

        self.assertNotIn("calling_context", props, msg=sorted(props))

    async def test_an_unrelated_tool_still_dispatches(self):
        # Introduced and caught while fixing B1, and the second time this exact
        # trap has fired in this campaign (see P11b's `reseeded` initializer).
        # The stash is assigned only inside `if tool_name == "record_error_resolution"`,
        # but the re-attach after S4 runs for EVERY tool -- so the first draft raised
        # UnboundLocalError on all 219 others. Every other test in this file targets
        # record_error_resolution, so none of them could see it; the damage showed up
        # as 17 unrelated failures in the server contract suite. One cheap call
        # through a different tool is the guard.
        #
        # The tool has to be one that actually REACHES the re-attach line. The first
        # version of this guard called `list_tools`, which is an HTTP route but not a
        # TOOL_MAP entry, so it answered 404 UNKNOWN_TOOL several hundred lines early
        # and passed happily with the initializer deleted -- a vacuous test that a
        # sabotage run caught. `protected_tools_status` is registered, takes no
        # arguments, is side-effect-free, and returns 200 through the full path.
        request = make_mocked_request(
            "POST", "/api/call/protected_tools_status",
            match_info={"tool_name": "protected_tools_status"})
        request.read = _body_reader({})
        response = await orchestrator_server._dispatch_tool_call_request(request)

        self.assertEqual(response.status, 200, msg=response.text[:400])

    async def test_an_unvalidated_caller_never_reaches_the_handler(self):
        # No checkpoint headers at all: the 403 ownership gate must still fire,
        # ahead of any of this.
        response, payload = await self._dispatch(self._valid_payload(), headers={})

        self.assertEqual(response.status, 403, msg=payload)
        self.assertEqual(payload.get("error_name"), "SESSION_OWNERSHIP_REQUIRED",
                         msg=payload)
        self.assertEqual(self.seen, [], msg=self.seen)


if __name__ == "__main__":
    unittest.main()
