"""Invalid bounds require intent correction, not an invented/swapped range."""
import json

import orchestrator_server as srv


def test_invalid_range_guidance_agrees_and_does_not_guess_count():
    body = {"project_path": "D:/project", "file_path": "sample.py",
            "start_line": 480, "end_line": 40, "force_reseed": True}
    guidance = srv._ca_guidance("read_file_range", "invalid bounds", body, "INVALID_RANGE")
    sequence = srv._retry_sequence("read_file_range", "INVALID_RANGE", body)
    example = guidance["payload_example"]
    assert example["scope"] == "range"
    assert "symbol" not in example and "end_line" not in example
    assert "force_reseed" not in example
    assert sequence[0]["action"] == "choose_intended_range"
    assert sequence[1]["payload_example"] == example
    assert "payload" not in sequence[1]  # illustrative, not an executable guessed repair
    assert guidance["ca_next_action"] == "correct_range"
    assert body["start_line"] == 480 and body["end_line"] == 40


def test_error_response_uses_same_range_example_at_both_levels():
    response = srv._error_response("read_file_range", "invalid bounds", 400, {},
        {"file_path": "sample.py", "start_line": 200, "end_line": 120},
        error_name="INVALID_RANGE")
    payload = json.loads(response.body)
    assert payload["payload_example"] == payload["ca_guidance"]["payload_example"]
    assert payload["retry_sequence"]
