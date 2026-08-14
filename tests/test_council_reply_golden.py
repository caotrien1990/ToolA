# pyright: reportMissingImports=false
from pathlib import Path

import pytest

import orchestrator_server


GOLDEN = Path(__file__).parent / "golden" / "council_replies"
PROMPT = "PROMPT_LINE_FOR_ECHO_SUBTRACTION"


@pytest.mark.parametrize(
    ("adapter", "expected"),
    [
        ("codex", "CODEX_GOLDEN_OK"),
        ("opencode", "OPENCODE_GOLDEN_OK"),
        ("kilo", "KILO_GOLDEN_OK"),
        ("hermes", "HERMES_GOLDEN_OK"),
    ],
    ids=lambda value: value if isinstance(value, str) and value in {"codex", "opencode", "kilo", "hermes"} else None,
)
def test_cli_reply_golden(adapter, expected):
    raw = (GOLDEN / f"{adapter}.raw.txt").read_text(encoding="utf-8")
    reply, reasoning, transport_noise = orchestrator_server._council_parse_reply(raw, PROMPT, adapter)
    assert reply == expected
    assert PROMPT not in reply
    assert PROMPT in transport_noise
    if adapter == "hermes":
        assert reasoning == "checked the representative sample"
