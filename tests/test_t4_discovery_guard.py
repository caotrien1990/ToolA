"""Sprint 4 tests: section-4 discovery clause, the section-2 HTTP guard
for non-routed consumers, R-fix-1 and R-fix-2.

Filed plan: T4_HTTP_PLAN_20261110.md sections 2, 4, 6 (row 16), 8, 9.
All registry assertions read the LIVE registries (bridge frozensets,
ct.TOOL_MAP / ct.TOOL_PHASES, call_cache sets, the server discovery
functions) -- no copied registry is trusted as the source of truth; the
plan's name lists appear here only as the EXPECTED side of an equality.
"""
import ast
import importlib
import json
import sys
import urllib.request
from pathlib import Path

import pytest

osrv = importlib.import_module("orchestrator_server")
import tools_v15 as _v15
from orchestrator import t4_bridge
from orchestrator.t4_bridge import (
    BRIDGED_TOOLS, DISCOVERY_TOOLS, DRIVER_LOCK_TOOLS, GUARDED_TOOLS,
    GUARD_EXEMPT_TOOLS, NON_ROUTED_CONSUMERS, deadline_for,
    guard_non_routed_http_call, is_well_formed_page_id,
)
from orchestrator.core import call_cache

EXPECTED_ROUTING = frozenset({
    "open_browser_page", "cdp_attach_target", "list_cdp_targets",
    "read_page", "query_selector", "click", "fill", "evaluate_js",
    "wait_for_selector", "read_console_errors", "read_network_errors",
    "screenshot_page", "compare_dom_snapshot", "describe_page",
    "close_browser_page", "cleanup_test_session", "query_dom_candidates",
    "desktop_window_list", "desktop_screenshot", "check_window_visibility",
    "desktop_click", "desktop_app_focus", "desktop_send_text",
    "desktop_window_state",
    "fill_folder_dialog", "cancel_native_dialog",
    "fill_value", "read_value", "dom_state_diff", "ui_state_summary",
    "extract_log_section", "read_all_logs", "save_log_snapshot",
    "verify_active_electron_source",
    # T4-11/T4-12 admitted (P-SANCTION): routed, not advertised
    "click_selector", "assert_console_no_error",
})
EXPECTED_DISCOVERY = frozenset({
    "open_browser_page", "cdp_attach_target", "list_cdp_targets",
    "read_page", "query_selector", "click", "fill", "evaluate_js",
    "wait_for_selector", "read_console_errors", "read_network_errors",
    "screenshot_page", "compare_dom_snapshot", "describe_page",
    "close_browser_page", "cleanup_test_session", "query_dom_candidates",
    "desktop_window_list", "desktop_screenshot", "check_window_visibility",
    "desktop_click", "desktop_app_focus", "desktop_send_text",
    "desktop_window_state",
    "fill_folder_dialog", "cancel_native_dialog",
    "t4_host_status",
})
ROUTED_UTILITIES = frozenset({
    "fill_value", "read_value", "dom_state_diff", "ui_state_summary",
    "extract_log_section", "read_all_logs", "save_log_snapshot",
    "verify_active_electron_source",
})
EXPECTED_EXCLUSIONS = frozenset({
    "click_and_handle_folder_dialog",
    "invoke_app_function", "checkpoint_loop_dom_state",
    "click_and_checkpoint_loop", "run_script", "record_ui_flow",
    "assert_text_visible", "assert_button_click_changes_state",
    "launch_electron_with_cdp",
})
PAGE_BOUND_GUARDED = EXPECTED_EXCLUSIONS - {"launch_electron_with_cdp"}
WELL_PG = "pg-1730000000000-0-abc123"
WELL_CDP = "cdp-1730000000000-1-ff00aa"


# ---------------------------------------------------------------------------
# Census-guard registry test (plan sections 2 and 9): LIVE registries only.
# ---------------------------------------------------------------------------

class TestCensusGuard:
    def test_routing_matches_supported_tools(self):
        assert BRIDGED_TOOLS == EXPECTED_ROUTING

    def test_discovery_matches_supported_tools(self):
        assert DISCOVERY_TOOLS == EXPECTED_DISCOVERY
        routed_not_advertised = {"click_selector", "assert_console_no_error"}
        assert DISCOVERY_TOOLS - {"t4_host_status"} == \
            EXPECTED_ROUTING - ROUTED_UTILITIES - routed_not_advertised
        assert "t4_host_status" not in BRIDGED_TOOLS
        assert "t4_host_status" in osrv.ct.TOOL_MAP

    def test_every_excluded_consumer_is_guarded(self):
        assert NON_ROUTED_CONSUMERS == EXPECTED_EXCLUSIONS
        assert BRIDGED_TOOLS & NON_ROUTED_CONSUMERS == frozenset()
        assert not GUARD_EXEMPT_TOOLS
        assert GUARDED_TOOLS == EXPECTED_EXCLUSIONS

    def test_bridged_disjoint_from_cacheable_live(self):
        assert BRIDGED_TOOLS & call_cache.CACHEABLE_TOOLS == frozenset()

    def test_bridged_disjoint_from_budgeted_live(self):
        # The section 7 / filing-note assertion: a future budget addition
        # would silently reroute a bridged tool's invoke branch.
        assert BRIDGED_TOOLS & call_cache.BUDGETED_TOOLS == frozenset()
        for name in BRIDGED_TOOLS:
            assert call_cache.budget_for(name) is None

    def test_every_live_direct_consumer_is_routed_or_excluded(self):
        """AST census of tools_v15: every registered tool whose handler
        calls _get_page or touches _BROWSER_PAGES is in routing union
        exclusions (plan section 2 census rule)."""
        src = Path(_v15.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        consumers = set()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            for sub in ast.walk(node):
                hit = False
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    hit = (isinstance(fn, ast.Name) and fn.id == "_get_page") or \
                        (isinstance(fn, ast.Attribute) and fn.attr == "_get_page")
                elif isinstance(sub, (ast.Name, ast.Attribute)):
                    hit = getattr(sub, "id", None) == "_BROWSER_PAGES" or \
                        getattr(sub, "attr", None) == "_BROWSER_PAGES"
                if hit:
                    consumers.add(node.name)
                    break
        # Sanity: the census actually found the known consumers.
        assert {"read_page", "click_selector", "close_browser_page",
                "launch_electron_with_cdp", "cdp_attach_target"} <= consumers
        registered = consumers & set(osrv.ct.TOOL_MAP)
        assert registered, "census found no registered consumers"
        for name in sorted(registered):
            assert name in BRIDGED_TOOLS or name in NON_ROUTED_CONSUMERS, \
                f"direct consumer {name} is neither routed nor excluded"

    def test_composition_edges_from_routed_tools_land_in_routing_or_exclusions(self):
        edges = {
            "cleanup_test_session": "close_browser_page",
            "verify_active_electron_source": "evaluate_js",
            "desktop_send_text": "desktop_window_list",
        }
        for caller, callee in edges.items():
            assert caller in BRIDGED_TOOLS
            assert callee in BRIDGED_TOOLS or callee in NON_ROUTED_CONSUMERS

    def test_composite_absent_from_routing_and_discovery(self):
        assert "click_and_handle_folder_dialog" not in BRIDGED_TOOLS
        assert "click_and_handle_folder_dialog" not in DISCOVERY_TOOLS


# ---------------------------------------------------------------------------
# Discovery clause (plan section 4) against the live server functions.
# ---------------------------------------------------------------------------

def _live_hidden_count():
    return sum(
        1 for n in osrv.ct.TOOL_MAP
        if osrv._tool_hidden_from_http_discovery(
            n, osrv.ct.TOOL_PHASES.get(n, "")))


class TestDiscoveryClause:
    def test_each_discovery_tool_is_unhidden_live(self):
        for name in sorted(DISCOVERY_TOOLS):
            assert name in osrv.ct.TOOL_MAP, name
            assert osrv._tool_hidden_from_http_discovery(
                name, osrv.ct.TOOL_PHASES.get(name, "")) is False, name

    def test_every_other_t4_tool_stays_hidden_live(self):
        for name, phase in osrv.ct.TOOL_PHASES.items():
            if str(phase).strip().upper() == "T4" and \
                    name not in DISCOVERY_TOOLS:
                assert osrv._tool_hidden_from_http_discovery(
                    name, phase) is True, name

    def test_names_payload_t4_tier_lists_exactly_the_discovery_t4(self):
        payload = osrv._tool_names_payload(tier="T4", limit=1000)
        got = {t["name"] for t in payload["tools"]}
        want = {n for n in DISCOVERY_TOOLS
                if str(osrv.ct.TOOL_PHASES.get(n, "")).strip().upper() == "T4"}
        assert got == want
        assert got, "payload listed no T4 tools at all"

    def test_names_payload_default_view_has_no_other_t4(self):
        payload = osrv._tool_names_payload(limit=1000)
        t4_listed = {t["name"] for t in payload["tools"]
                     if str(t.get("tier") or "").strip().upper() == "T4"}
        assert t4_listed <= DISCOVERY_TOOLS


# ---------------------------------------------------------------------------
# R-fix-2: hidden count is filter-invariant (computed unfiltered).
# ---------------------------------------------------------------------------

class TestRFix2:
    def test_hidden_count_matches_live_predicate(self):
        payload = osrv._tool_names_payload(limit=1000)
        assert payload["hidden_from_discovery"] == _live_hidden_count()
        assert payload["hidden_from_discovery"] > 0

    def test_hidden_count_identical_under_unrelated_name_filter(self):
        base = osrv._tool_names_payload(limit=1000)["hidden_from_discovery"]
        by_name = osrv._tool_names_payload(
            name_contains="git", limit=1000)["hidden_from_discovery"]
        by_tier = osrv._tool_names_payload(
            tier="T1", limit=1000)["hidden_from_discovery"]
        assert by_name == base
        assert by_tier == base

    def test_hidden_count_zero_when_include_hidden(self):
        payload = osrv._tool_names_payload(limit=1000, include_hidden=True)
        assert payload["hidden_from_discovery"] == 0


# ---------------------------------------------------------------------------
# R-fix-1 regression (plan section 8): stubbed names endpoint, live-shaped
# payload pair -- the query must carry include_hidden=1 and the returned
# names must equal the full T4 set of the unfiltered payload.
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture
def as_bridge(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["orchestrator_server.py", "--mcp"])
    osrv._MCP_T4_TIER_CACHE.clear()
    yield
    osrv._MCP_T4_TIER_CACHE.clear()


def _full_t4_names():
    return sorted(n for n in osrv.ct.TOOL_MAP
                  if osrv._mcp_local_t4_phase(n))


class TestRFix1:
    def test_query_carries_include_hidden_and_returns_full_t4_set(
            self, as_bridge, monkeypatch):
        urls = []
        names = _full_t4_names()
        assert len(names) >= 40  # the live T4 phase set (45 at the pin)

        def _fake(url, timeout=None):
            urls.append(str(url))
            return _Resp({"ok": True, "count": len(names),
                          "tools": [{"name": n, "tier": "T4"}
                                    for n in names]})

        monkeypatch.setattr(urllib.request, "urlopen", _fake)
        got = osrv._mcp_daemon_t4_names()
        assert got == set(names)
        assert len(urls) == 1
        assert "include_hidden=1" in urls[0]
        assert "tier=T4" in urls[0]

    def test_empty_answer_still_yields_none(self, as_bridge, monkeypatch):
        def _fake(url, timeout=None):
            return _Resp({"ok": True, "count": 0, "tools": []})

        monkeypatch.setattr(urllib.request, "urlopen", _fake)
        assert osrv._mcp_daemon_t4_names() is None
        assert "names" not in osrv._MCP_T4_TIER_CACHE

    def test_error_answer_still_yields_none(self, as_bridge, monkeypatch):
        def _fake(url, timeout=None):
            raise OSError("refused")

        monkeypatch.setattr(urllib.request, "urlopen", _fake)
        assert osrv._mcp_daemon_t4_names() is None


# ---------------------------------------------------------------------------
# HTTP guard (plan section 2 / section 6 row 16).
# ---------------------------------------------------------------------------

class TestGuardShapes:
    @pytest.mark.parametrize("tool", sorted(PAGE_BOUND_GUARDED))
    def test_well_formed_pg_id_answers_not_routed(self, tool):
        out = guard_non_routed_http_call(
            tool, {"page_id": WELL_PG}, generation="genX")
        assert out["ok"] is False
        assert out["error_name"] == "T4_NOT_ROUTED"
        assert out["tool"] == tool
        assert out["generation"] == "genX"
        assert "query_selector" in out["ca_next_action"]

    @pytest.mark.parametrize("tool", sorted(PAGE_BOUND_GUARDED))
    def test_well_formed_cdp_id_answers_not_routed(self, tool):
        out = guard_non_routed_http_call(tool, {"page_id": WELL_CDP})
        assert out["error_name"] == "T4_NOT_ROUTED"

    @pytest.mark.parametrize("tool", sorted(PAGE_BOUND_GUARDED))
    def test_malformed_id_answers_page_id_invalid(self, tool):
        for bad in ("not-a-page", "", "pg-", "xx-123", "pg- has space"):
            out = guard_non_routed_http_call(tool, {"page_id": bad})
            assert out["ok"] is False, (tool, bad)
            assert out["error_name"] == "PAGE_ID_INVALID", (tool, bad)

    @pytest.mark.parametrize("tool", sorted(PAGE_BOUND_GUARDED))
    def test_missing_id_cannot_bypass_transport_guard(self, tool):
        for body in ({}, {"page_id": None}):
            assert guard_non_routed_http_call(tool, body)["error_name"] == "T4_NOT_ROUTED"

    def test_launch_electron_answers_t4_not_routed_by_name(self):
        for body in ({}, {"launcher_path": "x"}, {"page_id": WELL_PG}):
            out = guard_non_routed_http_call(
                "launch_electron_with_cdp", body, generation="genX")
            assert out["ok"] is False
            assert out["error_name"] == "T4_NOT_ROUTED"
            assert out["tool"] == "launch_electron_with_cdp"

    def test_grammar_edges(self):
        assert is_well_formed_page_id(WELL_PG)
        assert is_well_formed_page_id(WELL_CDP)
        assert is_well_formed_page_id("pg-1730000000000-3")  # legacy shape
        assert not is_well_formed_page_id("pg-")
        assert not is_well_formed_page_id("")
        assert not is_well_formed_page_id(None)
        assert not is_well_formed_page_id(12345)

    def test_non_dict_body_still_refused(self):
        # P-FAILEDGES: the merged guard must refuse a non-dict body for a
        # guarded tool, never fall through to None.
        for body in (None, "junk", 12345, ["page_id"]):
            out = guard_non_routed_http_call("run_script", body)
            assert out["ok"] is False, body
            assert out["error_name"] == "T4_NOT_ROUTED", body


class TestGuardCarveOutAndScope:
    @pytest.mark.parametrize("body", [
        {}, {"page_id": WELL_PG}, {"page_id": WELL_CDP},
        {"page_id": "junk"}, {"page_id": ""},
        {"project_path": "D:/x", "page_id": WELL_PG},
    ])
    def test_cleanup_is_routed_through_ownership_checks(self, body):
        assert "cleanup_test_session" in BRIDGED_TOOLS
        assert guard_non_routed_http_call("cleanup_test_session", body) is None

    def test_routed_and_unrelated_tools_are_not_guarded(self):
        for tool in ("click", "read_page", "fill_value", "read_file_range",
                     "git_status", "open_browser_page"):
            assert guard_non_routed_http_call(
                tool, {"page_id": WELL_PG}) is None, tool
            assert guard_non_routed_http_call(
                tool, {"page_id": "junk"}) is None, tool

    def test_admitted_tools_are_routed_not_guarded(self):
        # T4-11/T4-12 (P-SANCTION): routed, not advertised; guard returns
        # None, click takes the driver lock, assert stays lock-exempt.
        for tool in ("click_selector", "assert_console_no_error"):
            assert tool in BRIDGED_TOOLS
            assert tool not in NON_ROUTED_CONSUMERS
            assert tool not in DISCOVERY_TOOLS
            assert guard_non_routed_http_call(tool, {"page_id": WELL_PG}) is None
            assert guard_non_routed_http_call(tool, {}) is None
        assert "click_selector" in DRIVER_LOCK_TOOLS
        assert "assert_console_no_error" not in DRIVER_LOCK_TOOLS
        assert deadline_for("click_selector") == 30.0
        assert deadline_for("click_selector", {"timeout_ms": 200000}) == 30.0
        assert deadline_for("assert_console_no_error") == 60.0

    def test_seam_consults_guard_only_under_api_transport(self):
        """Source-level pin: the guard call site in the dispatcher sits
        inside the seam's transport == "api" block (MCP never guarded)."""
        src = Path(osrv.__file__).read_text(encoding="utf-8")
        lines = src.splitlines()
        call_idx = next(
            i for i, ln in enumerate(lines)
            if "_t4_seam.guard_non_routed_http_call(" in ln)
        gate_idx = max(
            i for i in range(call_idx)
            if 'if transport == "api":' in lines[i])
        gate_indent = len(lines[gate_idx]) - len(lines[gate_idx].lstrip())
        for ln in lines[gate_idx + 1:call_idx + 1]:
            if not ln.strip() or ln.strip().startswith("#"):
                continue
            indent = len(ln) - len(ln.lstrip())
            assert indent > gate_indent, ln
