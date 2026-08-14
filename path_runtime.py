from pathlib import Path


class PathControlCharacterError(ValueError):
    """Raised before a path containing an embedded C0 control reaches pathlib/OS APIs."""

    def __init__(self, character: str, field: str | None = None):
        self.character = character
        self.codepoint = ord(character)
        self.field = field
        location = f" in field {field!r}" if field else ""
        super().__init__(f"path contains embedded control character U+{self.codepoint:04X}{location}")


def reject_c0_control_characters(value: str, field: str | None = None) -> None:
    """Reject C0 controls without reflecting the raw character in errors or logs."""
    for character in str(value):
        if ord(character) < 0x20:
            raise PathControlCharacterError(character, field)


def _is_orchestrator_dir(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "orchestrator_server.py").is_file()
        and (path / "code_tools.py").is_file()
    )


ORCHESTRATOR_DIR = Path(__file__).resolve().parent
APP_ROOT = ORCHESTRATOR_DIR.parent


def find_orchestrator_dir(candidate: str | Path) -> Path | None:
    # UNC timeout guard: never expanduser/resolve/is_file/iterdir-probe a UNC
    # path during orchestrator-dir auto-inference (dead SMB share = 30-60s hang).
    # Self-contained check to avoid importing code_tools (circular).
    raw = str(candidate).strip()
    if raw.startswith("\\\\") or raw.startswith("//"):
        return None
    path = Path(candidate).expanduser().resolve()
    if path.is_file():
        path = path.parent
    if _is_orchestrator_dir(path):
        return path
    if not path.is_dir():
        return None
    for child in sorted(path.iterdir(), key=lambda item: item.name.lower()):
        if _is_orchestrator_dir(child):
            return child
    return None


def resolve_ui_html(orchestrator_dir: str | Path | None = None) -> Path:
    base_dir = Path(orchestrator_dir).resolve() if orchestrator_dir else ORCHESTRATOR_DIR
    ui_dir = base_dir.parent / "UI"
    preferred = ui_dir / "8. AGENT TOOL - READ WRITE TEST_UI.html"
    if preferred.exists():
        return preferred
    for pattern in ("*_UI.html", "*.html"):
        for match in sorted(ui_dir.glob(pattern), key=lambda item: item.name.lower()):
            if match.is_file():
                return match
    return preferred