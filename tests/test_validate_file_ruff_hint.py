import asyncio
import json
from pathlib import Path

import code_tools


def _validate(path: Path, root: Path) -> dict:
    return json.loads(asyncio.run(code_tools.validate_file(str(path), project_path=str(root))))


def test_validate_python_f401_includes_ruff_preview_hint(tmp_path):
    source = tmp_path / "unused_import.py"
    source.write_text("import os\n\nvalue = 1\n", encoding="utf-8")

    result = _validate(source, tmp_path)

    assert result["ok"] is True  # lint advisory: ruff findings hint the CA, never hard-fail the write
    assert result["ruff_fix_applicable"] is True
    assert "F401" in result["ruff_rules_matched"]
    assert "ruff_fix(path=" in result["ruff_fix_hint"]
    assert "apply=false" in result["ruff_fix_hint"]


def test_validate_python_syntax_error_has_no_ruff_fix_hint(tmp_path):
    source = tmp_path / "syntax_error.py"
    source.write_text("def broken(:\n    pass\n", encoding="utf-8")

    result = _validate(source, tmp_path)

    assert result["ok"] is False
    assert result["ruff_fix_applicable"] is False
    assert result["ruff_rules_matched"] == []
    assert result["ruff_fix_hint"] == ""