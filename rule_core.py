import hashlib
from pathlib import Path

from path_runtime import APP_ROOT, ORCHESTRATOR_DIR

_RULE_CORE_PATH = ORCHESTRATOR_DIR / "examples" / "CORE_RULE.txt"


def get_rule_core_runtime_paths() -> dict[str, str]:
    return {
        "app_root": str(APP_ROOT),
        "orchestrator_dir": str(ORCHESTRATOR_DIR),
        "core_rule_path": str(_RULE_CORE_PATH),
        "start_script": str(ORCHESTRATOR_DIR / "start_orchestrator.bat"),
    }


def get_rule_core_metadata() -> dict[str, str | int]:
    text = get_rule_core()
    stat = _RULE_CORE_PATH.stat()
    return {
        **get_rule_core_runtime_paths(),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "bytes": len(text.encode("utf-8")),
        "mtime_ns": stat.st_mtime_ns,
    }


def get_rule_core() -> str:
    if not _RULE_CORE_PATH.exists():
        raise FileNotFoundError(
            f"CORE_RULE.txt not found at {_RULE_CORE_PATH}. "
            "Ensure examples/CORE_RULE.txt exists in the orchestrator directory."
        )
    return _RULE_CORE_PATH.read_text(encoding="utf-8")