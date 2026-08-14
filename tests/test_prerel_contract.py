import logging

import orchestrator_server


def test_retry_sequence_pre_release_errors_are_actionable():
    for error_name in (
        "UNSUPPORTED_FIELD",
        "WRONG_ARGUMENTS",
        "UNSUPPORTED_DRY_RUN",
        "RUN_TEST_PROFILE_INVALID",
        "SESSION_NOT_FOUND",
        "PROJECT_PATH_REQUIRED",
        "FILE_ACCESS_DENIED",
    ):
        sequence = orchestrator_server._retry_sequence(
            "sample_tool",
            error_name,
            {"project_path": "D:/repo", "dry_run": True},
        )

        assert sequence, error_name


def test_generated_payload_example_uses_schema_types_and_descriptions(monkeypatch):
    tool_name = "prerel_schema_only_tool"
    schema = {
        "function": {
            "name": tool_name,
            "parameters": {
                "required": ["query", "limit", "enabled", "items", "options"],
                "properties": {
                    "query": {"type": "string", "description": "search text"},
                    "limit": {"type": "integer", "description": "max rows"},
                    "enabled": {"type": "boolean", "description": "enable flag"},
                    "items": {"type": "array", "description": "input items"},
                    "options": {"type": "object", "description": "tool options"},
                },
            },
        }
    }
    monkeypatch.setattr(orchestrator_server.ct, "TOOL_SCHEMAS", [*orchestrator_server.ct.TOOL_SCHEMAS, schema])

    example = orchestrator_server._payload_example_for_tool(tool_name, {"parameters": {"properties": {}}})

    assert example["query"] == "<search text>"
    assert example["query"] != "<query>"
    assert example["limit"] == 1
    assert example["enabled"] is False
    assert example["items"] == []
    assert example["options"] == {}


def test_generated_payload_example_empty_schema_points_to_live_schema():
    example = orchestrator_server._payload_example_for_tool(
        "prerel_unknown_empty_tool",
        {"parameters": {"properties": {}}},
    )

    assert example == {"schema_ref": "GET /api/tools/prerel_unknown_empty_tool/schema"}


def test_council_config_load_failure_logs_error(monkeypatch, caplog):
    def _raise_config_error(force=True):
        raise ValueError("broken config")

    monkeypatch.setattr(orchestrator_server.ct, "_load_council_config", _raise_config_error)

    with caplog.at_level(logging.ERROR, logger=orchestrator_server.logger.name):
        agents, labels = orchestrator_server._council_active_from_config()

    assert agents
    assert labels
    assert "council config load failed: broken config" in caplog.text
