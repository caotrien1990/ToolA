from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.core import interfaces
from orchestrator.runtime import desktop_dialog_controls


PROTOCOL_SIGNATURES = {
    interfaces.DesktopDialogRuntime: {
        "fill_folder_dialog": "(self, title_pattern: 'str' = Ellipsis, folder_path: 'str' = Ellipsis, timeout_ms: 'int' = Ellipsis, wait_for_dialog_ms: 'int' = Ellipsis, auto_try_common_titles: 'bool' = Ellipsis, electron_fallback_hint: 'dict | None' = Ellipsis, owner_pid: 'int' = Ellipsis, parent_hwnd: 'int' = Ellipsis, class_name_pattern: 'str' = Ellipsis, exclude_titles: 'list | None' = Ellipsis, launched_after_ts: 'str' = Ellipsis, dry_run: 'bool' = Ellipsis, try_modern_picker: 'bool' = Ellipsis) -> 'str'",
        "folder_dialog_snapshot": "(self, owner_pid: 'int' = Ellipsis, parent_hwnd: 'int' = Ellipsis, title_pattern: 'str' = Ellipsis, launched_after_ts: 'str' = Ellipsis, include_child_controls: 'bool' = Ellipsis) -> 'str'",
        "cancel_native_dialog": "(self, title_pattern: 'str' = Ellipsis, button_pattern: 'str' = Ellipsis, owner_pid: 'int' = Ellipsis, parent_hwnd: 'int' = Ellipsis, exclude_titles: 'list | None' = Ellipsis) -> 'str'",
        "click_and_handle_folder_dialog": "(self, page_id: 'str', click_selector: 'str', folder_path: 'str', owner_pid: 'int' = Ellipsis, owner_title: 'str' = Ellipsis, wait_for_dialog_ms: 'int' = Ellipsis, timeout_ms: 'int' = Ellipsis, try_modern_picker: 'bool' = Ellipsis, verify_target_exists: 'bool' = Ellipsis) -> 'str'",
    },
}


@pytest.mark.parametrize(
    ("protocol", "method_name", "expected"),
    [
        (protocol, method_name, expected)
        for protocol, methods in PROTOCOL_SIGNATURES.items()
        for method_name, expected in methods.items()
    ],
)
def test_protocol_signatures_are_pinned(protocol: type, method_name: str, expected: str) -> None:
    actual = interfaces.protocol_method_signatures(protocol)[method_name]
    assert str(actual) == expected


def test_shipped_implementation_satisfies_runtime_protocol() -> None:
    assert isinstance(desktop_dialog_controls, interfaces.DesktopDialogRuntime)


def test_static_import_graph_has_no_bidirectional_pairs() -> None:
    root = Path(__file__).resolve().parents[1]
    graph = interfaces.build_static_import_graph(root)
    pairs = interfaces.bidirectional_import_pairs(graph)
    assert pairs == []