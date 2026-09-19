"""A37: the bootstrap purpose push, the first-use schema push, and HTTP<->MCP fields= parity.

WHY this file exists, measured 2026-09-19 on the live daemon: one CA session fetched ~18
tool schemas, `read_file_range` alone 6 times, because nothing ever told it which read
tools exist or what they are FOR. Every one of those fetches was S and t spent to re-learn
what [A] already knew. The mechanism under test moves that knowledge to the ONE call every
CA makes before any project work (`get_rule_core_text`) and then pushes each read tool's
schema exactly once, on its first use, where it is actionable.

Every assertion here is DERIVED from the live registry or from the producer's own source.
A hand-written list of tool names or a magic byte count would pass while the thing it
names rots -- which is the failure this campaign is closing, not one to reproduce.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import pathlib

import code_tools as ct
import orchestrator_server as srv
from orchestrator.core.active_chain import ACTIVE_CHAIN_TARGETS
from orchestrator.core.fields_param import prune_unknown_fields
from orchestrator.core.registry_data import CURATED_CORE_TOOLS
from orchestrator.server import mcp_stdio


def _server_source() -> str:
    return pathlib.Path(str(srv.__file__)).read_text(encoding="utf-8", errors="replace")


def _mcp_source() -> str:
    return pathlib.Path(str(mcp_stdio.__file__)).read_text(encoding="utf-8", errors="replace")


def _registry_descriptions() -> dict:
    out = {}
    for entry in getattr(ct, "TOOL_SCHEMAS", ()) or ():
        fn = entry.get("function") if isinstance(entry, dict) else None
        if isinstance(fn, dict) and fn.get("name"):
            out[str(fn["name"])] = str(fn.get("description") or "")
    return out


# --- the epoch: what makes "first call since bootstrap" a fact instead of a guess ------

def test_no_epoch_means_no_push():
    """A caller that never bootstrapped through [A] must get NOTHING pushed.

    False here is the safe default, not a miss: pushing a schema to a caller who never
    asked for the rule is pure noise, and it would also spend the mark so the real
    bootstrap could never deliver it.
    """
    ctx = "pytest-a37-never-opened"
    assert ct.bootstrap_epoch_of(ctx) == 0
    assert ct.bootstrap_first_use(ctx, "read_file_range") is False


def test_first_use_is_true_exactly_once():
    ctx = "pytest-a37-once"
    ct.open_bootstrap_epoch(ctx)
    assert ct.bootstrap_first_use(ctx, "read_file_range") is True
    assert ct.bootstrap_first_use(ctx, "read_file_range") is False
    assert ct.bootstrap_first_use(ctx, "read_file_range") is False
    # a DIFFERENT tool is still armed -- the mark is per tool, not per epoch
    assert ct.bootstrap_first_use(ctx, "search_code") is True


def test_error_rearms_exactly_that_tool():
    """An error IS the evidence the CA does not hold the schema, so the push comes back.

    bootstrap_forget returns False when there was nothing to forget: a re-arm must be
    observable, or the dispatcher could not tell a real re-arm from a no-op and would
    stamp schema_rearmed on every error.
    """
    ctx = "pytest-a37-rearm"
    ct.open_bootstrap_epoch(ctx)
    assert ct.bootstrap_forget(ctx, "read_file_range") is False  # never used yet
    assert ct.bootstrap_first_use(ctx, "read_file_range") is True
    assert ct.bootstrap_forget(ctx, "read_file_range") is True
    assert ct.bootstrap_first_use(ctx, "read_file_range") is True  # armed again
    assert ct.bootstrap_first_use(ctx, "search_code") is True     # untouched by the forget


def test_reopening_increments_and_clears():
    ctx = "pytest-a37-reopen"
    first = ct.open_bootstrap_epoch(ctx)
    assert ct.bootstrap_first_use(ctx, "read_file_range") is True
    second = ct.open_bootstrap_epoch(ctx)
    assert second == first + 1
    assert ct.bootstrap_epoch_of(ctx) == second
    assert ct.bootstrap_first_use(ctx, "read_file_range") is True


def test_two_agents_do_not_share_marks():
    a, b = "pytest-a37-agent-a", "pytest-a37-agent-b"
    ct.open_bootstrap_epoch(a)
    ct.open_bootstrap_epoch(b)
    assert ct.bootstrap_first_use(a, "read_file_range") is True
    assert ct.bootstrap_first_use(b, "read_file_range") is True, \
        "agent B's push was spent by agent A -- the epoch is not keyed per agent"


def test_epoch_table_is_bounded():
    """A daemon that runs for weeks must not grow this map forever.

    Eviction costs the evicted CA one extra schema push; an unbounded dict costs memory
    with no ceiling. The bound is read from the module so raising it cannot leave a stale
    number asserting here.
    """
    for i in range(ct._BOOTSTRAP_EPOCH_MAX + 8):
        ct.open_bootstrap_epoch("pytest-a37-bound-%d" % i)
    assert len(ct._BOOTSTRAP_EPOCHS) <= ct._BOOTSTRAP_EPOCH_MAX


def test_ctx_id_reuses_the_rule_core_key():
    """One identity, not two. A second identity would have to be declared in the registry
    schema or the dispatcher refuses it as UNSUPPORTED_FIELD."""
    assert ct.bootstrap_ctx_id("claultnai") == ct._rule_core_agent_key("claultnai")
    assert ct.bootstrap_ctx_id("") == ct._rule_core_agent_key("")


# --- the purpose map: derived from the live registry, purpose only, never usage --------

def test_purposes_are_one_capped_sentence():
    purposes = ct.bootstrap_tool_purposes()
    assert purposes, "the purpose map is empty -- Tang 0 would push nothing at bootstrap"
    for name, text in purposes.items():
        assert text.strip() == text and text
        assert "\n" not in text, "%s carries a multi-line hint; a purpose is ONE sentence" % name
        assert len(text) <= ct._BOOTSTRAP_PURPOSE_MAX_CHARS, \
            "%s purpose is %d chars, over the cap that makes Tang 0 a net saving" % (
                name, len(text))


def test_purposes_are_derived_from_the_live_registry():
    """Not a hand-written table: each purpose must be the opening of that tool's OWN
    registry description, so a description fix reaches CAs without touching this code."""
    descriptions = _registry_descriptions()
    purposes = ct.bootstrap_tool_purposes()
    for name, text in purposes.items():
        assert name in descriptions, "%s is not in the live registry" % name
        normalized = " ".join(descriptions[name].split())
        head = text[:-1] if text.endswith("…") else text
        assert normalized.startswith(head[:40]), \
            "%s purpose does not come from its registry description" % name


def test_write_tools_are_excluded_by_design():
    """Tang 2 is deliberate, not an oversight: write tools keep fetching their schema on
    EVERY call. That costs S and t and we accept it, because a wrong write costs
    Correctness, which outranks both."""
    purposes = ct.bootstrap_tool_purposes()
    for name in ("write_fix", "write_new_file", "write_transaction"):
        assert name not in purposes, "%s must never be schema-pushed" % name


def test_cbm_and_ocr_families_are_in_the_map():
    """These two are the surfaces measured never to be discovered, and cbm_get_architecture
    is the specific trap: its mutation_scope is derived_index, not none, so a none-only
    filter drops the whole cbm family right back out of view."""
    purposes = ct.bootstrap_tool_purposes()
    assert any(n.startswith("cbm_") for n in purposes)
    assert any(n.startswith("ocr_") for n in purposes)
    assert "cbm_get_architecture" in purposes
    assert ct.tool_mutation_scope("cbm_get_architecture") in ct.NON_SOURCE_MUTATION_SCOPES


def test_push_set_is_exactly_the_purpose_keys():
    """ONE membership answer from ONE place. A second hand-maintained list in the
    dispatcher would be free to drift from the map the CA was actually given."""
    assert ct.bootstrap_push_tools() == frozenset(ct.bootstrap_tool_purposes())


def test_purpose_payload_stays_bounded():
    """The bound is derived from the cap and the live count, so adding a read tool cannot
    fail this test while shrinking the cap tightens it automatically."""
    purposes = ct.bootstrap_tool_purposes()
    size = len(json.dumps(purposes, ensure_ascii=False).encode("utf-8"))
    ceiling = len(purposes) * (ct._BOOTSTRAP_PURPOSE_MAX_CHARS + 72) + 64
    assert size <= ceiling, "purpose map is %d bytes, over the derived ceiling %d" % (
        size, ceiling)


# --- bootstrap wiring: the FULL branch opens the epoch, the stub must not --------------

def test_full_fetch_pushes_purposes_and_opens_the_epoch():
    agent = "pytest-a37-bootstrap"
    ct._RULE_CORE_FETCH_CACHE.pop(ct._rule_core_agent_key(agent), None)
    payload = json.loads(asyncio.run(ct.get_rule_core_text(agent_id=agent, force=True)))
    assert payload["already_fetched"] is False
    assert payload["text"], "the full branch must carry the rule text"
    assert payload["ctx_id"] == ct.bootstrap_ctx_id(agent)
    assert payload["bootstrap_epoch"] >= 1
    assert payload["tool_purposes"] == ct.bootstrap_tool_purposes()
    note = payload["tool_purposes_note"]
    assert "pushed_schema" in note, "the note must name the response key the push arrives under"
    assert "FOR" in note and "how" in note, \
        "the note must say purpose-not-usage; the schema owns the how"


def test_the_already_fetched_stub_names_its_omission_and_rearms_nothing():
    """A37 #1a plus the epoch rule in one behaviour test.

    The stub must (a) say `text` is absent BY DESIGN -- a CA read the missing key as
    staleness and paid ~9KB to be told the same thing -- and (b) NOT re-open the epoch,
    or "first read since bootstrap" would silently mean "first read since I last asked".
    """
    agent = "pytest-a37-stub"
    ctx = ct.bootstrap_ctx_id(agent)
    ct._RULE_CORE_FETCH_CACHE.pop(ctx, None)
    asyncio.run(ct.get_rule_core_text(agent_id=agent, force=True))
    epoch_after_bootstrap = ct.bootstrap_epoch_of(ctx)
    assert ct.bootstrap_first_use(ctx, "read_file_range") is True  # spend the mark

    stub = json.loads(asyncio.run(ct.get_rule_core_text(agent_id=agent)))
    assert stub["already_fetched"] is True
    assert "text" not in stub
    assert stub["text_omitted"] is True
    assert stub["text_omitted_reason"]
    assert "compaction" in stub["ca_next_action"].lower()
    assert stub["bootstrap_epoch"] == epoch_after_bootstrap, "the stub opened a new epoch"
    assert ct.bootstrap_first_use(ctx, "read_file_range") is False, \
        "the stub re-armed a push; an already_fetched answer is not a bootstrap"


# --- dispatcher placement: the push has to survive the projection ---------------------

def test_push_is_stamped_after_the_r101_filter():
    """Placement IS the contract here, and getting it wrong fails silently.

    _r101_filter prunes any key the caller did not project, and a CA cannot project for
    a key it does not yet know exists. Stamped before the filter, the epoch would be
    SPENT on a call that delivered no schema -- exactly once, unrecoverably, per tool.
    """
    source = _server_source()
    filter_at = source.index("_result = _r101_filter(_result")
    hint_at = source.index("_result = _r101_size_hint(_result")
    push_at = source.index('_result["pushed_schema"] = _pushed_schema')
    assert filter_at < hint_at < push_at, \
        "the #0 push must be stamped after _r101_filter and _r101_size_hint"


def test_an_error_rearms_but_never_pushes():
    """An error payload already hands back retry_sequence + payload_example; a schema on
    top of that is noise. The schema belongs on the corrected retry, where it is
    actionable -- so the re-arm has to come FIRST and carry no schema."""
    source = _server_source()
    block_start = source.index('A37 #0 "Lan 1"')
    block = source[block_start:source.index("CSLt 2026-07-16", block_start)]
    rearm_at = block.index('_result["schema_rearmed"] = True')
    push_at = block.index('_result["pushed_schema"] = _pushed_schema')
    assert rearm_at < push_at, "the error branch must be the first branch"
    error_branch = block[block.index('if _status == "error":'):rearm_at]
    assert "pushed_schema" not in error_branch, "the error branch pushes a schema"


def test_the_push_cannot_fail_the_call():
    """A disclosure must never break a call that already worked."""
    source = _server_source()
    start = source.index('A37 #0 "Lan 1"')
    block = source[start:source.index("CSLt 2026-07-16", start)]
    assert "except Exception:" in block and "pass" in block


def test_the_pushed_schema_is_the_same_builder_tool_schema_serves():
    """One schema builder, two consumers. If the push built its own shape, a CA holding a
    pushed schema and a CA that fetched one would be reading two different contracts."""
    built = srv._enriched_tool_schema("read_file_range")
    assert isinstance(built, dict) and built.get("function")
    describe = inspect.getsource(srv.handle_tool_describe)
    assert "_enriched_tool_schema(" in describe
    source = _server_source()
    assert source.count("_pushed_schema = _enriched_tool_schema(tool_name)") == 1
    assert srv._enriched_tool_schema("no_such_tool_a37") is None


# --- #4: a key the caller DOES receive must not be reported as unknown ----------------

def test_late_stamped_key_is_pruned_and_disclosed():
    payload = {"ok": True, "generation": "de8e97b7896e",
               "fields_unknown": ["generation"],
               "fields_unknown_hint": "check spelling"}
    assert prune_unknown_fields(payload) == ["generation"]
    assert "fields_unknown" not in payload, "a false 'you typoed it' survived"
    assert "fields_unknown_hint" not in payload
    assert payload["fields_late_stamped"] == ["generation"]


def test_a_real_typo_is_still_reported():
    """The positive control: pruning must not launder a genuine mistake into silence."""
    payload = {"ok": True, "generation": "g", "fields_unknown": ["nope", "generation"]}
    assert prune_unknown_fields(payload) == ["generation"]
    assert payload["fields_unknown"] == ["nope"]


def test_dotted_specs_are_left_alone():
    """`a.b` names a key INSIDE a nested dict; the late stamp only ever lands at top level."""
    payload = {"ok": True, "result": {"files": []}, "fields_unknown": ["result.nope"]}
    assert prune_unknown_fields(payload) == []
    assert payload["fields_unknown"] == ["result.nope"]


def test_prune_is_idempotent_and_shape_safe():
    payload = {"ok": True, "generation": "g", "fields_unknown": ["generation"]}
    prune_unknown_fields(payload)
    assert prune_unknown_fields(payload) == []
    assert prune_unknown_fields(None) == []
    assert prune_unknown_fields({"ok": True}) == []
    assert prune_unknown_fields({"fields_unknown": "generation"}) == []


def test_both_transports_prune():
    """HTTP and MCP have to answer the same way. The MCP host adds stamps of its OWN after
    the daemon's projection, so pruning on one side only reopens the hole."""
    assert "prune_unknown_fields" in _server_source()
    mcp_source = _mcp_source()
    assert "prune_unknown_fields" in mcp_source
    assert mcp_source.count("return _mcp_dump(payload)") >= 3, \
        "an exit of _mcp_add_generation bypasses the pruning dump"


# --- #1b: fields= parity between HTTP and the named MCP wrappers ---------------------

def _decorate(fn):
    """Register fn through the same decorator the stdio host rebinds onto _mcp.tool."""
    captured = {}

    def raw_tool(f):
        captured["fn"] = f
        return f

    wrapped = mcp_stdio._mcp_tool_fields_decorator(raw_tool)(fn)
    assert captured["fn"] is wrapped
    return wrapped


def test_every_named_wrapper_gains_a_fields_parameter():
    """Anh's requirement for A37: HTTP and MCP usable EQUALLY. Before this, the daemon's
    own "pass fields= next time" hint was advice an MCP caller physically could not take.

    Done in ONE decorator rather than 62 signatures: duplicating one fact across 62 places
    is how this bridge drifted from the daemon before.
    """
    def sample(name: str, limit: int = 0) -> str:
        return ""

    wrapped = _decorate(sample)
    params = inspect.signature(wrapped).parameters
    assert list(params)[:2] == ["name", "limit"], "the original parameters changed"
    assert "fields" in params
    assert params["fields"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["fields"].default == ""
    assert wrapped.__annotations__["fields"] is str
    assert wrapped.__name__ == "sample", "zeromcp names the tool from __name__"


def test_a_tool_that_already_declares_fields_passes_through_untouched():
    def already(fields: str = "") -> str:
        return fields

    assert _decorate(already) is already


def test_the_fields_value_is_scoped_to_the_one_call():
    """A thread-local, not a global: zeromcp may serve concurrent calls and each must send
    its OWN projection. Leaking one caller's fields= into another's answer would be a
    correctness bug, not a token bug -- so the value is restored on the way out."""
    seen = []

    def sample() -> str:
        seen.append(mcp_stdio._mcp_fields_current())
        return ""

    wrapped = _decorate(sample)
    assert mcp_stdio._mcp_fields_current() == ""
    wrapped(fields="ok,result")
    assert seen == ["ok,result"]
    assert mcp_stdio._mcp_fields_current() == "", "the fields= value outlived its call"
    wrapped()
    assert seen[-1] == "", "no fields= must behave exactly like the pre-A37 bridge"


def test_discovery_rides_the_projection_on_the_query_string(monkeypatch):
    """The discovery routes are GETs, so a projection cannot travel in a body -- and
    /api/tools/names and a full schema are among the largest things [A] returns, which is
    exactly where fields= pays."""
    urls = []

    class _Resp:
        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(url, timeout=10):
        urls.append(url)
        return _Resp()

    monkeypatch.setattr(mcp_stdio.urllib.request, "urlopen", _fake_urlopen)

    def probe(category: str = "") -> str:
        return mcp_stdio._mcp_live_discovery("/api/tools/names", {"category": category})

    wrapped = _decorate(probe)
    wrapped(category="cbm", fields="ok,tools")
    assert "fields=ok%2Ctools" in urls[-1] and "category=cbm" in urls[-1]
    wrapped(category="cbm")
    assert "fields=" not in urls[-1], "no fields= must not invent a projection"


def test_an_explicit_projection_wins_over_the_wrapper_argument():
    """call_tool(payload={... 'fields': ...}) is the caller being specific about ONE inner
    call; the wrapper argument must not override it."""
    mcp_source = _mcp_source()
    assert 'if _fields_now and not payload.get("fields"):' in mcp_source
    assert 'if _fields_now and not query.get("fields"):' in mcp_source


def test_no_answer_may_come_from_a_bridge_local_registry():
    """B010, re-pinned here because A37 touched the bridge: only the DAEMON may decide what
    a tool accepts. A bridge that answered from its own registry copy would report
    UNSUPPORTED_FIELD for a field the daemon accepts -- stale by construction, and the
    reason `fields=` parity had to be a forward, not a local filter.
    """
    mcp_source = _mcp_source()
    assert '"local_schema_fallback": False' in mcp_source, \
        "discovery must state it has no local fallback"
    for forbidden in ("build_tool_schemas(", "TOOL_SCHEMAS = ",
                      "from code_tools import TOOL_SCHEMAS"):
        assert forbidden not in mcp_source, \
            "%s in the bridge is a second registry that can drift from the daemon" % forbidden


# --- #2 / #7 / the deprecated-surface guard: narrow the contract, do not pad the reply -

def test_open_session_publishes_only_t1_and_t2():
    """A37 #2 root fix. The published enum invited the wrong call: six values, four of them
    aliases, so a CA reasonably sent mode=T1_EXISTING_APP and later mode="execute". The fix
    narrows what is PUBLISHED -- it does not add a response field to explain the mistake.
    """
    schema = srv._enriched_tool_schema("open_session")
    mode = schema["function"]["parameters"]["properties"]["mode"]
    assert mode["enum"] == ["T1", "T2"]
    assert mode["default"] == "T1"
    for alias in ("T1_FIX", "T1_EXISTING_APP", "T2_BUILD", "T2_NEW_APP"):
        assert alias in mode["description"], \
            "%s must stay documented as an accepted alias" % alias


def test_the_four_aliases_are_still_accepted():
    """Narrowing the published enum must break NO existing caller: the aliases keep folding.
    A narrowed contract that also rejected them would be a breaking change, not a fix."""
    assert ct._canon_mode("T1_EXISTING_APP") == "T1"
    assert ct._canon_mode("T1_FIX") == "T1"
    assert ct._canon_mode("T2_NEW_APP") == "T2"
    assert ct._canon_mode("T2_BUILD") == "T2"
    assert ct._canon_mode("") == "T1"
    assert set(ct._MODE_ALIASES.values()) == ct._VALID_WORKFLOW_MODES
    assert ct._canon_mode("execute") not in ct._VALID_WORKFLOW_MODES


def test_cbm_and_ocr_answer_a_functional_category():
    """A37 #7: the classifier was prefix-only, so category="read" hid every cbm_ and ocr_
    tool -- measured as the reason a CA never discovers the graph surface it is told to
    prefer. read/write comes from the registry mutation_scope, never from the name."""
    arch = srv._r102_tool_categories("cbm_get_architecture")
    assert {"cbm", "read", "derived"} <= arch, \
        "cbm_get_architecture writes [A]'s own index, so it is read AND derived"
    assert "ocr" in srv._r102_tool_categories("ocr_map")
    assert "read" in srv._r102_tool_categories("ocr_map")
    assert "write" in srv._r102_tool_categories("write_fix")
    assert "read" not in srv._r102_tool_categories("write_fix")


def test_no_published_surface_routes_to_a_deprecated_tool():
    """[A] must not offer what its own HARD rule forbids. This closes audit 2 (the active
    chain offered find_callers) and keeps the curated core clean at the same time."""
    deprecated = set(srv._DEPRECATED_TOOLS)
    assert deprecated, "the deprecation map is empty -- this guard would pass on anything"
    assert not (ACTIVE_CHAIN_TARGETS & deprecated), \
        "the active chain routes to a deprecated tool: %s" % sorted(
            ACTIVE_CHAIN_TARGETS & deprecated)
    assert not (set(CURATED_CORE_TOOLS) & deprecated), \
        "the curated core lists a deprecated tool: %s" % sorted(
            set(CURATED_CORE_TOOLS) & deprecated)


def test_write_fix_description_and_param_agree_on_base64():
    """A37 #6: the param said content_encoding='base64' was the way to send text with
    quotes; the tool description never mentioned it, so a CA reading only the description
    hit SYNTAX_ERROR_PRE_WRITE instead. Two descriptions of one contract must agree."""
    fn = srv._enriched_tool_schema("write_fix")["function"]
    props = fn["parameters"]["properties"]
    assert "base64" in props["content_encoding"]["description"].lower()
    assert "base64" in fn["description"].lower(), \
        "write_fix's description must name content_encoding='base64'"


# ---------------------------------------------------------------------------
# A37 #0b -- the two measured holes in the Lan-1 mechanism
# ---------------------------------------------------------------------------

def test_a_schema_gate_rejection_really_rearms_the_push():
    """MEASURED BROKEN before this fix: UNSUPPORTED_FIELD / MISSING_REQUIRED_FIELD return
    ~1300 lines above the success-path push block, so the promise in pushed_schema_note
    ('an error on it re-arms the push') was false for the error class a CA hits most. The
    re-arm now lives in _error_response, the single producer of every dispatcher-level
    error payload -- not at each gate, which is how it rotted in the first place."""
    ctx = ct.bootstrap_ctx_id("a37_gate_probe")
    ct.open_bootstrap_epoch(ctx)
    assert ct.bootstrap_first_use(ctx, "read_file_range") is True
    assert ct.bootstrap_first_use(ctx, "read_file_range") is False, "mark not spent"
    token = srv._A37_AGENT_CTX.set("a37_gate_probe")
    try:
        resp = srv._error_response(
            "read_file_range", "Unsupported field(s) for 'read_file_range': bogus_param",
            400, {}, {"bogus_param": 1}, error_name="UNSUPPORTED_FIELD")
    finally:
        srv._A37_AGENT_CTX.reset(token)
    payload = json.loads(resp.body)
    assert payload["schema_rearmed"] is True, "the gate answered without re-arming"
    assert ct.bootstrap_first_use(ctx, "read_file_range") is True, \
        "schema_rearmed was disclosed but the mark was never cleared"


def test_the_rearm_disclosure_survives_a_projection():
    """Stamped AFTER _r101_filter on purpose: a key added before it is pruned for every
    caller passing fields=, i.e. the disclosure would vanish for the callers who read most
    carefully. Same trap as the #0 push (see test_push_is_stamped_after_the_r101_filter)."""
    ctx = ct.bootstrap_ctx_id("a37_gate_fields")
    ct.open_bootstrap_epoch(ctx)
    ct.bootstrap_first_use(ctx, "search_code")
    agent_tok = srv._A37_AGENT_CTX.set("a37_gate_fields")
    fields_tok = srv._R101_FIELDS.set(["ok", "error_name"])
    try:
        resp = srv._error_response("search_code", "boom", 400, {}, {},
                                   error_name="UNSUPPORTED_FIELD")
    finally:
        srv._R101_FIELDS.reset(fields_tok)
        srv._A37_AGENT_CTX.reset(agent_tok)
    payload = json.loads(resp.body)
    assert payload.get("schema_rearmed") is True, "a projection hid the re-arm"


def test_the_dispatcher_publishes_the_identity_it_resolved():
    """_error_response is called from dozens of sites that never carry an agent id, so the
    re-arm can only work if the dispatcher publishes what it resolved."""
    source = _server_source()
    at = source.index("_task_agent_id = str(")
    assert "_A37_AGENT_CTX.set(_task_agent_id)" in source[at:at + 1500], \
        "_error_response cannot re-arm an identity the dispatcher never published"


def test_every_proxied_mcp_call_carries_the_agent_id(monkeypatch):
    """MEASURED BROKEN before this fix: the bridge forwarded agent_id only in the
    get_rule_core_text payload, so the desktop CA opened its epoch as 'claultnai' and then
    read every file as ctx 'default' -- the Lan-1 push was armed for an identity that never
    called again, i.e. inert for exactly the CA it was built for."""
    seen: dict = {}

    def _capture(req, http_timeout, name, payload, base):
        seen.clear()
        seen.update(req.headers)
        return json.dumps({"ok": True})

    monkeypatch.setattr(mcp_stdio, "_mcp_proxy_wait", _capture)
    monkeypatch.setattr(mcp_stdio, "_mcp_proxy_base_url", lambda _srv: "http://localhost:8083")
    monkeypatch.setattr(mcp_stdio, "_mcp_timeout_budget", lambda *a, **k: 5.0)
    monkeypatch.setitem(mcp_stdio._MCP_AGENT_ID, "v", "")
    monkeypatch.delenv("COUNCIL_AGENT_ID", raising=False)

    def _call(name, payload):
        try:
            mcp_stdio._mcp_proxy_call(name, payload)
        except Exception:
            pass  # the header block already ran; the response shape is not what we measure

    _call("get_rule_core_text", {"agent_id": "zcode"})
    # urllib.request.Request capitalize()s header keys, hence 'X-agent-id'.
    assert seen.get("X-agent-id") == "zcode", "bootstrap did not carry the identity"

    _call("read_file_range", {"file_path": "x.py"})
    assert seen.get("X-agent-id") == "zcode", \
        "a later read landed in ctx 'default' -- the Lan-1 push stays inert over MCP"
