"""A31 Q4 (2026-09-17) - the skill-surface gates inside _audit_tool_matrix_sync.

Why these tests are shaped the way they are: audit_tool_matrix is a REPORTING tool,
so a non-empty drift list is its normal, healthy output. `assert drift == []` would
pin today's tree instead of the mechanism and would need editing every time the tree
legitimately changes. So every gate is proven against a SYNTHETIC .ToolA_Skill tree
carrying exactly one defect, and the real tree is asserted only where the assertion
IS the enforcement (a new skill folder nobody routes to must break the suite).

Regression being locked in: before this campaign the router half of the audit was
dead code. Its regex expected `- topic -> NN_skill` rows and matched 0 lines of the
markdown table SKILLS.md actually is, so routed_folders was permanently empty and
none of these gates could have existed. It also required an NN_ prefix, which alone
would have excluded the 10 skill folders that are named rather than numbered.
"""

import re
from pathlib import Path

import pytest

import code_tools as ct


def _offenders(drift, gate):
    """The subjects a single gate named - skill folder for most, tool for core10."""
    return {d.get("skill") or d.get("tool") for d in drift if d.get("type") == gate}


def _frontmatter(tool=None, body=""):
    lines = ["---", "name: fixture"]
    if tool:
        lines += ["trigger:", '  - tool: "%s"' % tool]
    lines += ["---", ""]
    return "\n".join(lines) + body


def _audit_synthetic(tmp_path, monkeypatch, *, rows, skills,
                     shared_refs=(), beside_refs=None):
    """Point the audit at a synthetic .ToolA_Skill tree and hand back its drift list.

    skill_root is path_runtime.ORCHESTRATOR_DIR / ".ToolA_Skill" and is NOT derived
    from project_path, so ORCHESTRATOR_DIR is the thing that has to move. The rest of
    the audit then reads from tmp too and reports a pile of unrelated drift (there is
    no inventory document there, for one) - which is harmless precisely because these
    tests assert per-gate sets and never a total.
    """
    root = tmp_path / ".ToolA_Skill"
    (root / "skills").mkdir(parents=True)
    table = ["| Task | Skill folder | Confidence |", "|---|---|---|"]
    table += ["| %s | `%s/` | high |" % (topic, folder) for topic, folder in rows]
    (root / "SKILLS.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    for folder, body in skills.items():
        folder_dir = root / "skills" / folder
        folder_dir.mkdir(parents=True, exist_ok=True)
        (folder_dir / "SKILL.md").write_text(body, encoding="utf-8")
    for name in shared_refs:
        shared = root / "references"
        shared.mkdir(exist_ok=True)
        (shared / name).write_text("placeholder\n", encoding="utf-8")
    for folder, names in (beside_refs or {}).items():
        beside = root / "skills" / folder / "references"
        beside.mkdir(parents=True, exist_ok=True)
        for name in names:
            (beside / name).write_text("placeholder\n", encoding="utf-8")
    monkeypatch.setattr(ct.path_runtime, "ORCHESTRATOR_DIR", tmp_path)
    res = ct._audit_tool_matrix_sync(persist=False)
    assert res.get("ok") is True
    return res


@pytest.fixture(scope="module")
def real_audit():
    return ct._audit_tool_matrix_sync(persist=False)


# ---------------------------------------------------------------- the Core-10 binding

def test_core_ten_tools_is_bound_to_core_rule_txt():
    """CORE_TEN_TOOLS must equal the list CORE_RULE.txt publishes, name for name.

    Deliberately a NAMED LIST on both sides and no count anywhere: the same A31
    doctrine that says never write a tool total in prose beside the code that
    computes it. If the rule file is reworded so this line stops parsing, that is a
    failure too - a SOT nothing can read is not a SOT.
    """
    rule = Path(ct.path_runtime.ORCHESTRATOR_DIR) / "examples" / "CORE_RULE.txt"
    text = rule.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"^Core 10 \(.*?\):\s*([a-z0-9_ ,]+)\.", text, re.M)
    assert match, "CORE_RULE.txt no longer carries a parseable 'Core 10 (...): a, b.' line"
    named = tuple(tool.strip() for tool in match.group(1).split(","))
    assert named == ct.CORE_TEN_TOOLS


# ------------------------------------------------------- the dead-regex regression

def test_router_actually_parses_the_real_skills_md(real_audit):
    """router_topics_count > 0, and it equals the rows a second reader finds.

    The old regex made this 0 forever while the audit still reported ok:true. Both
    halves matter: > 0 catches the parser dying again, and the equality catches it
    quietly parsing only some rows.
    """
    assert real_audit.get("router_topics_count", 0) > 0
    md = Path(ct.path_runtime.ORCHESTRATOR_DIR) / ".ToolA_Skill" / "SKILLS.md"
    rows = [
        line for line in md.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.startswith("|") and re.search(r"`[A-Za-z0-9_.\-]+/`", line)
    ]
    assert real_audit["router_topics_count"] == len(rows)


# --------------------------------------------------------- enforcement on the real tree

@pytest.mark.parametrize("gate", [
    "skill_folder_unreachable",
    "route_to_missing_folder",
    "skill_missing_frontmatter",
    "core10_tool_undocumented",
])
def test_real_skill_tree_satisfies_the_fixable_gates(real_audit, gate):
    """These four gates must stay empty on the shipped tree.

    This is the one place a real-tree assertion is right: adding a skill folder with
    no SKILLS.md row, routing at a folder that does not exist, shipping a SKILL.md
    with no frontmatter, or dropping a Core-10 tool's trigger.tool are all mistakes,
    not drift to be tolerated. skill_reference_missing is deliberately NOT asserted
    here: four referenced documents genuinely do not exist yet and are recorded as
    backlog, so pinning it to 0 would either be a lie or force four invented files.
    """
    offenders = _offenders(real_audit.get("drift") or [], gate)
    assert offenders == set(), "%s: %s" % (gate, sorted(offenders))


# ----------------------------------------------------------- gate mechanisms, synthetic

def test_folder_with_no_router_row_is_reported(tmp_path, monkeypatch):
    res = _audit_synthetic(
        tmp_path, monkeypatch,
        rows=[("routed work", "routed")],
        skills={"routed": _frontmatter("read_file_range"),
                "orphan": _frontmatter("read_file_range")},
    )
    drift = res["drift"]
    assert _offenders(drift, "skill_folder_unreachable") == {"orphan"}
    assert _offenders(drift, "route_to_missing_folder") == set()


def test_router_row_pointing_at_no_folder_is_reported(tmp_path, monkeypatch):
    res = _audit_synthetic(
        tmp_path, monkeypatch,
        rows=[("real work", "present"), ("points nowhere", "ghost")],
        skills={"present": _frontmatter("read_file_range")},
    )
    drift = res["drift"]
    assert _offenders(drift, "route_to_missing_folder") == {"ghost"}
    assert _offenders(drift, "skill_folder_unreachable") == set()


def test_named_not_numbered_folder_is_routable(tmp_path, monkeypatch):
    """A folder without an NN_ prefix must route. The old regex required \\d{2}_,
    so 10 of the shipped folders could never have been reached even with the row
    separator fixed - this is that half of the bug.
    """
    res = _audit_synthetic(
        tmp_path, monkeypatch,
        rows=[("named folder work", "leader-council-skill")],
        skills={"leader-council-skill": _frontmatter("read_file_range")},
    )
    drift = res["drift"]
    assert _offenders(drift, "route_to_missing_folder") == set()
    assert _offenders(drift, "skill_folder_unreachable") == set()
    assert res["router_topics_count"] == 1


def test_skill_md_without_frontmatter_is_reported(tmp_path, monkeypatch):
    res = _audit_synthetic(
        tmp_path, monkeypatch,
        rows=[("good", "good"), ("bad", "bare")],
        skills={"good": _frontmatter("read_file_range"),
                "bare": "# no frontmatter at all\n"},
    )
    assert _offenders(res["drift"], "skill_missing_frontmatter") == {"bare"}


def test_tool_named_only_in_body_prose_does_not_count_as_documented(tmp_path, monkeypatch):
    """Coverage is harvested from the YAML frontmatter only.

    Measured on the real tree the day this landed: frontmatter-only and whole-file
    yield the same set of documented tools, so narrowing lost nothing. What it buys
    is this - prose that merely mentions `tool: "write_fix"` can no longer invent a
    route the router would never honour.
    """
    res = _audit_synthetic(
        tmp_path, monkeypatch,
        rows=[("prose only", "prose")],
        skills={"prose": _frontmatter(
            "read_file_range",
            body='Do not be fooled by this line: tool: "write_fix" lives in the body.\n',
        )},
    )
    undocumented = _offenders(res["drift"], "core10_tool_undocumented")
    assert "write_fix" in undocumented
    assert "read_file_range" not in undocumented


def test_core10_tool_documented_in_frontmatter_is_not_reported(tmp_path, monkeypatch):
    res = _audit_synthetic(
        tmp_path, monkeypatch,
        rows=[("reads", "reads")],
        skills={"reads": _frontmatter("read_file_range | search_code")},
    )
    undocumented = _offenders(res["drift"], "core10_tool_undocumented")
    assert {"read_file_range", "search_code"}.isdisjoint(undocumented)
    assert "write_fix" in undocumented


def test_reference_link_resolves_beside_the_skill_or_in_the_shared_dir(tmp_path, monkeypatch):
    """Three references, one absent: only the absent one is reported.

    Both resolution paths are real in the shipped tree, so a gate that knew only one
    of them would fire on files that exist.
    """
    body = (
        "see references/beside_me.md and references/in_shared.md "
        "and references/nowhere.md\n"
    )
    res = _audit_synthetic(
        tmp_path, monkeypatch,
        rows=[("links", "links")],
        skills={"links": _frontmatter("read_file_range", body=body)},
        shared_refs=("in_shared.md",),
        beside_refs={"links": ("beside_me.md",)},
    )
    missing = [d for d in res["drift"] if d.get("type") == "skill_reference_missing"]
    assert [d.get("reference") for d in missing] == ["nowhere.md"]
    assert missing[0].get("skill") == "links"


def test_the_same_dangling_reference_is_reported_once_per_skill(tmp_path, monkeypatch):
    """A skill body that mentions the same missing file twice yields one finding.

    Without the seen-set the audit would report a link as many times as the prose
    happens to name it, which turns one broken document into fake volume.
    """
    body = "references/nowhere.md is mentioned, then references/nowhere.md again\n"
    res = _audit_synthetic(
        tmp_path, monkeypatch,
        rows=[("dupe", "dupe")],
        skills={"dupe": _frontmatter("read_file_range", body=body)},
    )
    missing = [d for d in res["drift"] if d.get("type") == "skill_reference_missing"]
    assert len(missing) == 1
