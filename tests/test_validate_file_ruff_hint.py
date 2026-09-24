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
    assert "F401" in result["ruff_fix_hint"]
    # The hint names what ruff_fix is FOR; how to call it is the schema's job.
    assert "ruff_fix" in result["ruff_fix_hint"]
    assert "apply=" not in result["ruff_fix_hint"]


def test_validate_python_syntax_error_has_no_ruff_fix_hint(tmp_path):
    source = tmp_path / "syntax_error.py"
    source.write_text("def broken(:\n    pass\n", encoding="utf-8")

    result = _validate(source, tmp_path)

    assert result["ok"] is False
    assert result["ruff_fix_applicable"] is False
    assert result["ruff_rules_matched"] == []
    assert result["ruff_fix_hint"] == ""


def test_unsafe_only_finding_promises_no_ruff_fix(tmp_path):
    # F841 prints without ruff's "[*]": its fix is unsafe, so the safe pass
    # ruff_fix runs changes nothing. The finding stays reported; no promise.
    source = tmp_path / "unused_local.py"
    source.write_text("def f():\n    x = 1\n", encoding="utf-8")

    result = _validate(source, tmp_path)

    assert "F841" in result["lint_warning"]
    assert result["ruff_fix_applicable"] is False
    assert result["ruff_fix_hint"] == ""


def test_project_rule_set_is_extended_not_replaced(tmp_path):
    # --select replaced the project's rules; a project selecting only E741 lost
    # E741, and one selecting only "E" got no F821 for an undefined name.
    (tmp_path / "ruff.toml").write_text('[lint]\nselect = ["E741"]\n', encoding="utf-8")
    undefined = tmp_path / "undefined_name.py"
    undefined.write_text("x = pytest.mark\n", encoding="utf-8")
    ambiguous = tmp_path / "ambiguous.py"
    ambiguous.write_text("l = 1\n", encoding="utf-8")

    assert "F821" in _validate(undefined, tmp_path)["lint_warning"]
    assert "E741" in _validate(ambiguous, tmp_path)["lint_warning"]


def test_ruff_config_error_is_unknown_not_clean(tmp_path):
    # ruff exits 2 on a config it cannot load; that is "no lint result", and an
    # answer without lint_warning must not read as "lint clean".
    (tmp_path / "ruff.toml").write_text("[lint]\nselect = 5\n", encoding="utf-8")
    source = tmp_path / "value.py"
    source.write_text("value = 1\n", encoding="utf-8")

    result = _validate(source, tmp_path)

    assert "lint_warning" not in result
    assert "ruff exited 2" in result["lint_skipped_reason"]


def test_toml_is_parsed_not_passed_unchecked(tmp_path):
    # #5: a .toml answered ok with no check while the file no longer parsed.
    broken = tmp_path / "broken.toml"
    broken.write_text("[lint\nselect = 5\n", encoding="utf-8")
    valid = tmp_path / "valid.toml"
    valid.write_text('[lint]\nselect = ["E"]\n', encoding="utf-8")

    bad = _validate(broken, tmp_path)
    good = _validate(valid, tmp_path)

    assert (bad["ok"], bad["lang"]) == (False, "toml")
    assert "line 1" in bad["error"]
    assert (good["ok"], good["lang"]) == (True, "toml")
    assert good.get("checked", True) is True
