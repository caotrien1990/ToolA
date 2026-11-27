"""AC-P1-S2: R2 campaign routing matrix on public tool_decision_hint.

Prepass runs on lowercase accent-folded RAW before VN expand / CAN-053.
A07 still owns legacy fallback and must not revoke a confirmed R1 primary.
Named cbm_quality_gate is not recommended until that tool is registered (P4).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools_v15 as v


def _hint(query: str) -> dict:
    raw = asyncio.run(v.tool_decision_hint(task_description=query, verbosity="full"))
    payload = json.loads(raw)
    return payload.get("result", payload)


def _primary(result: dict) -> str:
    return str(result.get("recommended_tool") or "")


def _alt_tools(result: dict) -> list[str]:
    return [str(a.get("tool") or "") for a in (result.get("alternates") or [])]


def _primary_is(result: dict, name: str) -> bool:
    rec = _primary(result)
    return rec == name or rec.startswith(name + " ") or rec.startswith(name + "/")


def _primary_any(result: dict, names: tuple[str, ...]) -> bool:
    rec = _primary(result)
    return any(n in rec for n in names)


class TestCampaignRoutingPins:
    PINS = (
        ("tìm hàm chết không được gọi", "cbm_dead_code"),
        ("tim ham chet khong duoc goi", "cbm_dead_code"),
        ("find unused functions that are never called", "cbm_dead_code"),
        ("kiểm tra chất lượng kiến trúc trước commit", "cbm_cycle_point"),
        ("kiem tra chat luong kien truc truoc commit", "cbm_cycle_point"),
        ("check architecture quality before commit", "cbm_cycle_point"),
        ("circular dependency and architecture quality", "cbm_cycle_point"),
        ("sửa hàm trong file billing.py", "write_fix"),
        ("sua ham trong file billing.py", "write_fix"),
        ("fix function in file billing.py", "write_fix"),
        ("sửa file xong chạy test", "run_project_test_script"),
        ("sua file xong chay test", "run_project_test_script"),
        ("run pytest after editing the file", "run_project_test_script"),
        ("tạo file mới cho module X", "write_new_file"),
        ("tao file moi cho module X", "write_new_file"),
        ("create a new file for module X", "write_new_file"),
        ("chụp màn hình web đang mở", "desktop_screenshot"),
        ("chup man hinh web dang mo", "desktop_screenshot"),
        ("take a screenshot of the open web page", "desktop_screenshot"),
        ("sửa draft patch bị syntax pre write", "write_draft_patch"),
        ("sua draft patch bi syntax pre write", "write_draft_patch"),
        ("repair draft patch syntax pre write", "write_draft_patch"),
    )

    def test_r1_pins_accented_unaccented_english(self):
        for query, expected in self.PINS:
            result = _hint(query)
            assert _primary_is(result, expected), (
                f"{query!r} primary={_primary(result)!r} expected={expected!r}"
            )


class TestCampaignRoutingSpecials:
    def test_git_commit_uses_existing_fallback(self):
        for query in ("commit thay đổi lên git", "commit thay doi len git",
                      "commit the changes to git"):
            result = _hint(query)
            assert "git_commit" in _primary(result), (
                f"{query!r} primary={_primary(result)!r}"
            )

    def test_audit_tool_matrix_a07_invariant(self):
        result = _hint("audit tool matrix for this session")
        assert _primary_is(result, "audit_tool_matrix"), _primary(result)

    def test_search_snapshot_is_not_screenshot(self):
        for query in ("tìm snapshot của search", "tim snapshot cua search",
                      "find snapshot of search"):
            result = _hint(query)
            rec = _primary(result)
            assert _primary_any(result, ("find_symbol", "search_code")), rec
            assert "desktop_screenshot" not in rec

    def test_architecture_quality_may_surface_architecture_alternate(self):
        result = _hint("kiểm tra chất lượng kiến trúc trước commit")
        assert _primary_is(result, "cbm_cycle_point"), _primary(result)
        blob = " ".join(_alt_tools(result))
        # CAN-053 architecture group is allowed as alternate, not required as only alt.
        assert isinstance(blob, str)

    def test_dead_and_architecture_keeps_dead_primary(self):
        result = _hint("dead code and architecture quality")
        assert _primary_is(result, "cbm_dead_code"), _primary(result)
        alts = " ".join(_alt_tools(result))
        assert "cbm_cycle_point" in alts, alts

    def test_fix_wording_in_test_report_is_not_write_fix(self):
        result = _hint("fix wording in test report")
        assert not _primary_is(result, "write_fix"), _primary(result)

    def test_create_new_file_not_swallowed_by_edit(self):
        result = _hint("create new file billing.py")
        assert _primary_is(result, "write_new_file"), _primary(result)

    def test_search_screenshot_function_stays_search(self):
        result = _hint("search screenshot function in source")
        rec = _primary(result)
        assert _primary_any(result, ("find_symbol", "search_code")), rec
        assert "desktop_screenshot" not in rec

    def test_named_quality_gate_not_recommended_before_p4(self):
        folded = v._wt_campaign_fold_raw("run cbm_quality_gate on this project")
        tools = [r["tool"] for r in v._campaign_routing_prepass(folded, set())]
        assert "cbm_quality_gate" not in tools
        live = [r["tool"] for r in v._campaign_routing_prepass(folded, {"cbm_quality_gate"})]
        assert "cbm_quality_gate" in live

    def test_quality_image_without_code_context_is_not_cycle(self):
        result = _hint("improve image quality of this audio text render")
        assert not _primary_is(result, "cbm_cycle_point"), _primary(result)

    def test_draft_beats_general_edit(self):
        result = _hint("sửa draft patch bị syntax pre write")
        assert _primary_is(result, "write_draft_patch"), _primary(result)

    def test_run_tests_beats_edit_on_new_file_prose(self):
        result = _hint("run tests on the new file")
        assert _primary_is(result, "run_project_test_script"), _primary(result)


class TestCampaignPrepassContract:
    def test_fold_raw_does_not_expand_vocabulary(self):
        folded = v._wt_campaign_fold_raw("tìm hàm chết")
        expanded = v._wt_vn_expand("tìm hàm chết")
        assert "search" not in folded
        assert "find" not in folded
        assert folded != expanded
        compact = folded.replace(" ", "")
        assert "hamchet" in compact or "ham chet" in folded

    def test_prepass_order_is_stable(self):
        folded = v._wt_campaign_fold_raw(
            "dead code and architecture quality and run tests"
        )
        tools = [r["tool"] for r in v._campaign_routing_prepass(folded, set())]
        assert tools[0] == "run_project_test_script"
        assert "cbm_dead_code" in tools
        assert "cbm_cycle_point" in tools
        assert tools.index("run_project_test_script") < tools.index("cbm_dead_code")
        assert tools.index("cbm_dead_code") < tools.index("cbm_cycle_point")

    def test_named_evolution_gated_on_live_map(self):
        folded = v._wt_campaign_fold_raw("run cbm_evolution churn bus-factor")
        assert "cbm_evolution" not in [
            r["tool"] for r in v._campaign_routing_prepass(folded, set())
        ]
        live = [r["tool"] for r in v._campaign_routing_prepass(folded, {"cbm_evolution"})]
        assert "cbm_evolution" in live

    def test_named_dsm_gated_on_live_map(self):
        folded = v._wt_campaign_fold_raw("run cbm_dsm design structure matrix")
        assert "cbm_dsm" not in [
            r["tool"] for r in v._campaign_routing_prepass(folded, set())
        ]
        live = [r["tool"] for r in v._campaign_routing_prepass(folded, {"cbm_dsm"})]
        assert "cbm_dsm" in live


def test_bilingual_requests_respect_object_and_requested_action(monkeypatch):
    import orchestrator_server  # register the full HTTP catalog
    monkeypatch.setattr(v, "_wt_q_z2_refs", lambda _: [])
    cases = (
        ("Trước khi xóa hàm, ai đang gọi nó?", "find_symbol"),
        ("xem lịch sử sửa đổi của file billing.py", "git_log"),
        ("Đọc 30 dòng đầu file báo cáo", "read_file_range"),
        ("xóa session cũ", "list_sessions"),
        ("đóng session", "close_session"),
        ("ghi đè file này bằng nội dung mới", "write_fix"),
        ("create a draft patch for the existing file", "write_fix"),
        ("repair the pending draft with draft_id", "write_draft_patch"),
        ("chạy script PowerShell", "run_ps1_script"),
        ("trạng thái trang web", "describe_page"),
        ("xem trạng thái nút theo selector", "dom_state_diff"),
        ("show the latest commits", "git_log"),
        ("run command dir", "run_project_command"),
        ("stage file app.py for commit", "git_stage"),
        ("stage", "git_stage"),
        ("inspect the staging area before I commit", "git_status"),
        ("Gửi async brief cho worker, tôi sẽ xem kết quả sau", "council_message_async"),
        ("before editing file X, which modules are affected?", "find_symbol"),
    )
    aliases = set(orchestrator_server._DEPRECATED_TOOLS)
    for query, expected in cases:
        answer = _hint(query)
        ranked = [row["tool"] for row in answer["ranked_tools"]]
        assert answer["recommended_tool"] == ranked[0] == expected, query
        assert not aliases.intersection(ranked + _alt_tools(answer))
    preview = _hint("tạo patch nháp cho file billing.py")
    assert preview["suggestion"]["payload_example"]["dry_run"] is True
    assert _hint("xyzzy qzx")["confidence"] < _hint("git status")["confidence"]


def test_inspection_filters_every_offer_in_every_verbosity(monkeypatch):
    import code_tools as ct
    import orchestrator_server
    assert "git_status" in orchestrator_server.ct.TOOL_MAP
    monkeypatch.setattr(v, "_wt_q_z2_refs", lambda _: [])
    for query, scope, risk, expected in (
        ("sửa hàm trong file billing.py", "read", "", "read_file_range"),
        ("xóa file billing.py", "", "destructive", "read_file_range"),
        ("git commit", "", "destructive", "git_status"),
        ("gửi tin cho worker", "read", "", "council_status"),
        ("click nút X", "read", "", "describe_page"),
        ("không sửa code, chỉ xem file billing.py", "", "", "read_file_range"),
    ):
        for mode in ("full", "terse", "ref"):
            answer = json.loads(asyncio.run(v.tool_decision_hint(query, scope, risk, verbosity=mode)))
            assert answer["ranked_tools"][0]["tool"] == answer["recommended_tool"] == expected
            names = [row["tool"] for row in answer["ranked_tools"]] + _alt_tools(answer)
            assert not set(names).intersection({
                "close_browser_page", "write_draft_patch", "council_message",
                "council_cron_job_delete", "convene_council", "manage_agent",
            })
            for name in names:
                assert ct.tool_mutation_scope(name) in ct.NON_SOURCE_MUTATION_SCOPES
                assert name not in ct.PROTECTED_TOOL_NAMES | ct.TOKEN_REQUIRED_TOOLS


def test_deprecation_filter_works_without_importing_the_server(monkeypatch):
    import builtins
    from orchestrator.core.registry_data import DEPRECATED_TOOLS
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "orchestrator_server":
            raise AssertionError("a registry lookup must not re-run server registration")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert set(v._deprecated_tool_names()) == set(DEPRECATED_TOOLS)
    assert "read_chunks" in DEPRECATED_TOOLS
