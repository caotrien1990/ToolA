import json
from pathlib import Path


def test_runtime_state_has_no_empty_terminal_reason():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / ".council" / "runtime_state.json").read_text(encoding="utf-8"))
    invalid = {key: value for key, value in data.items()
               if str(value.get("state", "")).lower() in {"error", "failed", "cancelled"}
               and not str(value.get("error") or "").strip()}
    assert invalid == {}, f"empty terminal reasons: {sorted(invalid)}"
