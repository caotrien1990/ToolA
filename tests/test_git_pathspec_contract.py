"""Git pathspec published-schema contract tests.

Sprint 1 (F5): the git tools' published schema text must tell a caller,
up front, that `paths` entries prefer repo-relative pathspecs, that an
absolute path inside this repo is accepted and normalized server-side to
a relative pathspec, that an absolute path outside this repo is still
rejected (use read_file_range / run_ps1_script for it), that `git_status`
takes no file path, and that `git_log`'s `ref` may be a tracked file path.
The payload examples for git_diff / git_stage / git_unstage must show a
`paths` entry (the generated examples omitted it, so a caller copying the
example never saw the field), and git_diff's per-property `example`
for `paths` must be the same neutral example path.

Sprint 4 pins the absolute-pathspec auto-normalization rule (report
PATHSPEC_AUTONORMALIZE_AUDIT_SCOPE_20261118): normalize only at F1 step 5,
re-enter steps 0-6 on the remainder, strip at most once.

Later sprints extend this file; keep each sprint's tests under its own
section comment.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import orchestrator_server as srv  # noqa: E402
from orchestrator.core.server_schema_metadata import METADATA  # noqa: E402


def _spec_props(tool_name):
    """_GIT_TOOL_SPECS rows, the way the house tests read them
    (tests/test_a31_git_ocr_attach.py): row[0] is the tool name and
    row[7] is its published properties dict."""
    return {row[0]: row[7] for row in srv._GIT_TOOL_SPECS}[tool_name]


# ── Sprint 1: F5 — git pathspec published-schema contract ─────────────────

_OUTSIDE_REPO_SUFFIX = (
    "relative paths are preferred; an absolute path inside this repo is "
    "accepted and normalized to a relative pathspec server-side; an absolute "
    "path outside this repo is still rejected; for a file outside the repo use "
    "read_file_range (content/hash) or run_ps1_script (metadata)"
)


def test_sprint1_git_diff_paths_description():
    assert _spec_props("git_diff")["paths"]["description"] == (
        "Relative pathspec allowlist — " + _OUTSIDE_REPO_SUFFIX
    )


def test_sprint1_git_stage_paths_description():
    assert _spec_props("git_stage")["paths"]["description"] == (
        "Relative paths to stage — " + _OUTSIDE_REPO_SUFFIX
    )


def test_sprint1_git_unstage_paths_description():
    assert _spec_props("git_unstage")["paths"]["description"] == (
        "Relative paths to unstage — " + _OUTSIDE_REPO_SUFFIX
    )


def test_sprint1_git_status_project_path_description():
    assert _spec_props("git_status")["project_path"]["description"] == (
        "Absolute Git repository root — git_status takes no file path; "
        "to check one file use read_file_range or run_ps1_script"
    )


def test_sprint1_git_log_ref_description():
    assert _spec_props("git_log")["ref"]["description"] == (
        "Revision reference — or a tracked file path, "
        "for that file's commit history"
    )


def test_sprint1_git_diff_payload_example():
    example = srv._payload_example_for_tool(
        "git_diff", srv._schema_for_tool("git_diff"))
    assert example["paths"] == ["docs/example.md"]
    assert example == {
        "project_path": "D:/path/to/project",
        "scope": "worktree",
        "paths": ["docs/example.md"],
        "context_lines": 3,
        "max_chars": 50000,
        "check_whitespace": False,
    }


def test_sprint1_git_stage_payload_example():
    example = srv._payload_example_for_tool(
        "git_stage", srv._schema_for_tool("git_stage"))
    assert example["paths"] == ["docs/example.md"]
    assert example == {
        "project_path": "D:/path/to/project",
        "paths": ["docs/example.md"],
        "allow_all": False,
        "ocr": True,
    }


def test_sprint1_git_unstage_payload_example():
    example = srv._payload_example_for_tool(
        "git_unstage", srv._schema_for_tool("git_unstage"))
    assert example["paths"] == ["docs/example.md"]
    assert example == {
        "project_path": "D:/path/to/project",
        "paths": ["docs/example.md"],
        "allow_all": False,
    }


def test_sprint1_git_diff_parameter_example_in_metadata():
    assert METADATA["git_diff"]["parameter_examples"]["paths"] == [
        "docs/example.md"
    ]
    on_disk = json.loads(
        (_ROOT / "orchestrator" / "core" / "server_schema_metadata.json")
        .read_text(encoding="utf-8"))
    assert on_disk["git_diff"]["parameter_examples"]["paths"] == [
        "docs/example.md"
    ]


def test_sprint1_git_diff_served_schema_paths_example():
    props = srv._schema_for_tool("git_diff")["parameters"]["properties"]
    assert props["paths"]["example"] == ["docs/example.md"]
    assert props["paths"]["description"] == (
        "Relative pathspec allowlist — " + _OUTSIDE_REPO_SUFFIX
    )


# -- Sprint 2: F1-F4 -- _pathspecs validation order, absolute-path branching,
# -- shared existence check, and the PATHSPEC_NOT_FOUND hint pair -----------

import subprocess  # noqa: E402
import tempfile  # noqa: E402

from orchestrator import git_tools as g  # noqa: E402


def _s2_run(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, text=True)


def _s2_repo():
    d = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    root = Path(d.name)
    _s2_run(root, "init")
    _s2_run(root, "config", "user.name", "Test")
    _s2_run(root, "config", "user.email", "test@example.com")
    (root / "a.txt").write_text("one\n")
    _s2_run(root, "add", "a.txt")
    _s2_run(root, "commit", "-m", "init")
    return d, root


def _s2_bare_repo():
    d = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    root = Path(d.name)
    _s2_run(root, "init", "--bare")
    return d, root


# F1 -- per-spec validation order and reason codes

def test_sprint2_f1_empty_and_nul_reason():
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=[""])
    assert res["error_name"] == "INVALID_GIT_PATHSPEC"
    assert res["reason_code"] == "empty_or_nul"
    res = g.safe_call(g.diff, str(r), paths=["a\x00b"])
    assert res["error_name"] == "INVALID_GIT_PATHSPEC"
    assert res["reason_code"] == "empty_or_nul"
    d.cleanup()


def test_sprint2_f1_drive_relative_reason():
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=[r.drive + "foo.txt"])
    assert res["error_name"] == "INVALID_GIT_PATHSPEC"
    assert res["reason_code"] == "drive_relative"
    d.cleanup()


def test_sprint2_f1_drive_letter_env_is_drive_relative_not_denylisted():
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=[r.drive + ".env"])
    assert res["error_name"] == "INVALID_GIT_PATHSPEC"
    assert res["reason_code"] == "drive_relative"
    d.cleanup()


def test_sprint2_f1_wildcard_and_magic_reason():
    d, r = _s2_repo()
    for spec in ("*.txt", ":(glob)**"):
        res = g.safe_call(g.diff, str(r), paths=[spec])
        assert res["error_name"] == "INVALID_GIT_PATHSPEC", spec
        assert res["reason_code"] == "wildcard_or_magic", spec
    d.cleanup()


def test_sprint2_f1_leading_dash_or_colon_reason():
    d, r = _s2_repo()
    for spec in ("-rf", ":x"):
        res = g.safe_call(g.diff, str(r), paths=[spec])
        assert res["error_name"] == "INVALID_GIT_PATHSPEC", spec
        assert res["reason_code"] == "leading_dash_or_colon", spec
    d.cleanup()


def test_sprint2_f1_relative_env_denylisted_shape_preserved():
    # Preservation pin: the denylist answer keeps its exact pre-fix shape --
    # message is the bare value and there are no extras at all.
    d, r = _s2_repo()
    (r / ".env").write_text("secret")
    res = g.safe_call(g.diff, str(r), paths=[".env"])
    assert res["error_name"] == "PATHSPEC_DENYLISTED"
    assert res["error"] == ".env"
    assert "reason_code" not in res
    assert "ca_next_action" not in res
    assert "ca_next_tool" not in res
    d.cleanup()


def test_sprint2_f1_sub_env_denylisted():
    # Preservation pin: a nested secret name was already denylisted pre-fix.
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=["sub/.env"])
    assert res["error_name"] == "PATHSPEC_DENYLISTED"
    d.cleanup()


def test_sprint2_f1_absolute_env_denylisted_flip():
    d, r = _s2_repo()
    (r / ".env").write_text("secret")
    res = g.safe_call(g.diff, str(r), paths=[str(r / ".env")])
    assert res["error_name"] == "PATHSPEC_DENYLISTED"
    d.cleanup()


def test_sprint2_f1_absolute_git_config_denylisted_flip():
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=[str(r / ".git" / "config")])
    assert res["error_name"] == "PATHSPEC_DENYLISTED"
    d.cleanup()


def test_sprint2_f1_sub_git_config_not_denylisted():
    # Preservation pin: a non-leading relative .git component is not on the
    # denylist; pre-fix this already surfaced as PATHSPEC_NOT_FOUND through
    # diff's private existence check.
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=["sub/.git/config"])
    assert res["error_name"] == "PATHSPEC_NOT_FOUND"
    d.cleanup()


def test_sprint2_f1_rooted_env_denylisted():
    # Preservation pin: a rooted driveless secret spelling was already
    # denylisted pre-fix (it is not Path.is_absolute() on Windows).
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=["/.env"])
    assert res["error_name"] == "PATHSPEC_DENYLISTED"
    d.cleanup()


def test_sprint2_f1_rooted_plain_reaches_outside_root():
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=["/foo.txt"])
    assert res["error_name"] == "GIT_PATH_OUTSIDE_ROOT"
    assert res["reason_code"] == "escapes_root"
    d.cleanup()


def test_sprint2_f1_dotdot_escape_reason():
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=["../escape.txt"])
    assert res["error_name"] == "GIT_PATH_OUTSIDE_ROOT"
    assert res["reason_code"] == "escapes_root"
    d.cleanup()


def test_sprint2_f1_normal_relative_diff_ok():
    # Preservation pin: an ordinary existing relative path diffs as before.
    d, r = _s2_repo()
    (r / "a.txt").write_text("two\n")
    res = g.diff(str(r), paths=["a.txt"])
    assert res["ok"] and "two" in res["diff"]
    d.cleanup()


# F2 -- situation branching for absolute-path (step 5) rejections only

def test_sprint2_f2_same_repo_absolute():
    # Normalization SUCCESS pin (was: absolute_path rejection). An absolute
    # spelling inside THIS repo normalizes to the relative spec and diffs.
    d, r = _s2_repo()
    (r / "a.txt").write_text("two\n")
    res = g.safe_call(g.diff, str(r), paths=[str(r / "a.txt")])
    assert res["ok"] and "two" in res["diff"]
    assert res["paths"] == ["a.txt"]
    d.cleanup()


def test_sprint2_f2_other_repo_absolute():
    d, r = _s2_repo()
    d2, r2 = _s2_repo()
    (r2 / "b.txt").write_text("b\n")
    top = Path(_s2_run(
        r2, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    res = g.safe_call(g.diff, str(r), paths=[str(r2 / "b.txt")])
    assert res["error_name"] == "INVALID_GIT_PATHSPEC"
    assert res["reason_code"] == "absolute_path"
    assert res["ca_next_tool"] == "git_diff"
    assert "another git top-level" in res["ca_next_action"]
    assert str(top) in res["ca_next_action"]
    assert "paths=['b.txt']" in res["ca_next_action"]
    d.cleanup(); d2.cleanup()


def test_sprint2_f2_outside_any_repo_absolute():
    d, r = _s2_repo()
    plain = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    plain_root = Path(plain.name)
    (plain_root / "plain.txt").write_text("plain\n")
    res = g.safe_call(g.diff, str(r), paths=[str(plain_root / "plain.txt")])
    assert res["error_name"] == "INVALID_GIT_PATHSPEC"
    assert res["reason_code"] == "absolute_path"
    assert res["ca_next_tool"] == "write_status"
    assert "read_file_range" in res["ca_next_action"]
    d.cleanup(); plain.cleanup()


def test_sprint2_f2_nonexistent_absolute():
    # Normalize-then-validate (Override O2): a nonexistent-inside absolute now
    # answers PATHSPEC_NOT_FOUND naming the RELATIVE spec (was absolute_path
    # + F2(c) write_status prose).
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=[str(r / "missing_xyz_abs.txt")])
    assert res["error_name"] == "PATHSPEC_NOT_FOUND"
    assert res["pathspecs"] == ["missing_xyz_abs.txt"]
    assert res["ca_next_tool"] == "git_status"
    d.cleanup()


def test_sprint2_f2_bare_repo_absolute_undetermined():
    d, r = _s2_repo()
    db, rb = _s2_bare_repo()
    assert (rb / "config").is_file()
    res = g.safe_call(g.diff, str(r), paths=[str(rb / "config")])
    assert res["error_name"] == "INVALID_GIT_PATHSPEC"
    assert res["reason_code"] == "absolute_path"
    assert res["ca_next_tool"] == "write_status"
    assert "could not determine" in res["ca_next_action"]
    d.cleanup(); db.cleanup()


def test_sprint2_f2_denylisted_absolute_has_no_branch():
    d, r = _s2_repo()
    (r / ".env").write_text("secret")
    res = g.safe_call(g.diff, str(r), paths=[str(r / ".env")])
    assert res["error_name"] == "PATHSPEC_DENYLISTED"
    assert "ca_next_tool" not in res
    assert "ca_next_action" not in res
    assert "reason_code" not in res
    d.cleanup()


# F3/F4 -- shared opt-in existence check + PATHSPEC_NOT_FOUND hint pair

def test_sprint2_f3_stage_missing_not_found():
    d, r = _s2_repo()
    res = g.safe_call(g.stage, str(r), ["missing_xyz.txt"])
    assert res["error_name"] == "PATHSPEC_NOT_FOUND"
    assert res["pathspecs"] == ["missing_xyz.txt"]
    assert res["ca_next_tool"] == "git_status"
    assert res["ca_next_action"] == (
        "check which paths actually exist with git_status")
    assert g.status(str(r))["counts"]["staged"] == 0
    d.cleanup()


def test_sprint2_f3_stage_two_missing_aggregated():
    d, r = _s2_repo()
    res = g.safe_call(
        g.stage, str(r), ["missing_one_xyz.txt", "missing_two_xyz.txt"])
    assert res["error_name"] == "PATHSPEC_NOT_FOUND"
    assert res["pathspecs"] == ["missing_one_xyz.txt", "missing_two_xyz.txt"]
    d.cleanup()


def test_sprint2_f3_unstage_missing_not_found():
    d, r = _s2_repo()
    res = g.safe_call(g.unstage, str(r), ["missing_xyz.txt"])
    assert res["error_name"] == "PATHSPEC_NOT_FOUND"
    assert res["pathspecs"] == ["missing_xyz.txt"]
    assert res["ca_next_tool"] == "git_status"
    d.cleanup()


def test_sprint2_f3_diff_missing_not_found_f4_extras():
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=["missing_xyz.txt"])
    assert res["error_name"] == "PATHSPEC_NOT_FOUND"
    assert res["pathspecs"] == ["missing_xyz.txt"]
    assert res["ca_next_tool"] == "git_status"
    assert res["ca_next_action"] == (
        "check which paths actually exist with git_status")
    d.cleanup()


def test_sprint2_f3_stage_paths_file_missing_entry():
    d, r = _s2_repo()
    (r / "b.txt").write_text("b")
    path_file = r / "batch.paths"
    path_file.write_text("b.txt\nmissing_xyz.txt\n", encoding="utf-8")
    res = g.safe_call(g.stage, str(r), paths_file=str(path_file))
    assert res["error_name"] == "PATHSPEC_NOT_FOUND"
    assert res["offending_entry"] == "missing_xyz.txt"
    assert res["entry_number"] == 2
    assert res["pathspecs"] == ["missing_xyz.txt"]
    assert res["ca_next_tool"] == "git_status"
    d.cleanup()


def test_sprint2_f3_diff_untracked_existing_ok_empty():
    # Preservation pin: an untracked file that exists on disk is not missing.
    d, r = _s2_repo()
    (r / "u.txt").write_text("u\n")
    res = g.diff(str(r), paths=["u.txt"])
    assert res["ok"] and res["diff"] == ""
    d.cleanup()


def test_sprint2_f3_diff_worktree_deleted_tracked_ok():
    # Preservation pin (the p7 semantics, now through the shared helper):
    # a worktree-deleted tracked file still has a deletion diff.
    d, r = _s2_repo()
    (r / "a.txt").unlink()
    res = g.diff(str(r), paths=["a.txt"])
    assert res["ok"] and "deleted file mode" in res["diff"]
    d.cleanup()

# -- Sprint 3: F6 -- _ca_next_action boundary fallbacks for git errors -----


def test_sprint3_ca_next_action_git_fallbacks():
    assert srv._ca_next_action("INVALID_GIT_PATHSPEC", "git_diff") == (
        "retry_with_relative_paths_inside_the_project_repo")
    assert srv._ca_next_action("GIT_PATH_OUTSIDE_ROOT", "git_diff") == (
        "retry_with_paths_that_stay_inside_the_project_repo")
    assert srv._ca_next_action("PATHSPEC_NOT_FOUND", "git_stage") == (
        "git_status")


def test_sprint3_denylisted_has_no_fallback():
    # Deliberate exclusion: a boundary fallback must never look like a
    # route around the denylist, so PATHSPEC_DENYLISTED keeps the
    # generic default.
    assert srv._ca_next_action("PATHSPEC_DENYLISTED", "git_diff") == (
        "read_error")


# -- Sprint 4: absolute-pathspec auto-normalization (report T1-T27) ---------
# T20 (xroot comment premise) is a recorded doc-check, not a test.

import asyncio  # noqa: E402
import os  # noqa: E402

import pytest  # noqa: E402


def _s2_link(target, link):
    """Real directory-link fixture: a junction on Windows (no privilege needed),
    a symlink elsewhere. Returns True when the link was created."""
    if os.name == "nt":
        p = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
        return p.returncode == 0 and Path(link).exists()
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
        return True
    except OSError:
        return False


def test_t1_diff_same_repo_absolute_succeeds():
    d, r = _s2_repo()
    (r / "a.txt").write_text("changed\n")
    res = g.safe_call(g.diff, str(r), paths=[str(r / "a.txt")])
    assert res["ok"] and "changed" in res["diff"]
    assert res["paths"] == ["a.txt"]
    d.cleanup()


def test_t2_stage_unstage_absolute_index_assertions():
    d, r = _s2_repo()
    (r / "a.txt").write_text("staged\n")
    st = g.safe_call(g.stage, str(r), [str(r / "a.txt")])
    assert st["ok"] and st["paths"] == ["a.txt"]
    assert g.status(str(r))["counts"]["staged"] == 1
    un = g.safe_call(g.unstage, str(r), [str(r / "a.txt")])
    assert un["ok"] and un["paths"] == ["a.txt"]
    assert g.status(str(r))["counts"]["staged"] == 0
    d.cleanup()


def test_t3_backslash_absolute_normalizes():
    d, r = _s2_repo()
    (r / "a.txt").write_text("bs\n")
    backslash = str(r / "a.txt").replace("/", "\\")
    res = g.safe_call(g.diff, str(r), paths=[backslash])
    assert res["ok"] and res["paths"] == ["a.txt"]
    d.cleanup()


def test_t4_lowercase_drive_letter_normalizes():
    d, r = _s2_repo()
    (r / "a.txt").write_text("lc\n")
    abs_path = str(r / "a.txt")
    lowered = abs_path[0].lower() + abs_path[1:]
    res = g.safe_call(g.diff, str(r), paths=[lowered])
    assert res["ok"] and res["paths"] == ["a.txt"]
    d.cleanup()


def test_t5_absolute_directory_normalizes():
    d, r = _s2_repo()
    (r / "sub").mkdir()
    (r / "sub" / "f.txt").write_text("f\n")
    _s2_run(r, "add", "sub/f.txt")
    res_plain = g.safe_call(g.diff, str(r), paths=[str(r / "sub")])
    assert res_plain["ok"] and res_plain["paths"] == ["sub"]
    res_slash = g.safe_call(g.diff, str(r), paths=[str(r / "sub") + os.sep])
    assert res_slash["ok"] and res_slash["paths"] == ["sub/"]
    d.cleanup()


def test_t6_root_itself_rejects_never_dot():
    d, r = _s2_repo()
    for spec in (str(r), str(r) + os.sep):
        res = g.safe_call(g.diff, str(r), paths=[spec])
        assert res["error_name"] == "INVALID_GIT_PATHSPEC", spec
        assert res["reason_code"] == "absolute_path", spec
        assert "paths=['.']" in res["ca_next_action"], spec
    # double trailing slash strips to '/' -> escapes_root (case 19)
    res = g.safe_call(g.diff, str(r), paths=[str(r) + os.sep + os.sep])
    assert res["error_name"] == "GIT_PATH_OUTSIDE_ROOT"
    assert res["reason_code"] == "escapes_root"
    d.cleanup()


def test_t7_absolute_plus_magic_no_f2():
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=[str(r / "*.txt")])
    assert res["error_name"] == "INVALID_GIT_PATHSPEC"
    assert res["reason_code"] == "absolute_path"
    assert "ca_next_tool" not in res
    assert "ca_next_action" not in res
    d.cleanup()


def test_t8_mixed_batch_dedupes():
    d, r = _s2_repo()
    (r / "a.txt").write_text("mix\n")
    res = g.safe_call(g.diff, str(r), paths=["a.txt", str(r / "a.txt")])
    assert res["ok"] and res["paths"] == ["a.txt"]
    d.cleanup()


def test_t9_paths_file_absolute_normalizes_and_reject_wraps():
    d, r = _s2_repo()
    (r / "a.txt").write_text("pf\n")
    batch = r / "batch.paths"
    batch.write_text(str(r / "a.txt") + "\n", encoding="utf-8")
    st = g.safe_call(g.stage, str(r), paths_file=str(batch))
    assert st["ok"] and st["paths"] == ["a.txt"]
    (r / ".env").write_text("secret")
    batch2 = r / "batch2.paths"
    batch2.write_text(str(r / ".env") + "\n", encoding="utf-8")
    res = g.safe_call(g.stage, str(r), paths_file=str(batch2))
    assert res["error_name"] == "PATHSPEC_DENYLISTED"
    assert res["offending_entry"] == str(r / ".env")
    assert res["entry_number"] == 1
    d.cleanup()


def test_t10_stage_file_path_alias_absolute_normalizes():
    d, r = _s2_repo()
    (r / "a.txt").write_text("alias\n")
    async def _sync_lane(name, func, /, *args, **kwargs):
        return func(*args, **kwargs)
    orig = srv.run_in_executor_lane
    srv.run_in_executor_lane = _sync_lane
    try:
        out = json.loads(asyncio.run(srv.git_stage(
            project_path=str(r), file_path=str(r / "a.txt"), ocr=False)))
    finally:
        srv.run_in_executor_lane = orig
    assert out["ok"] and out["paths"] == ["a.txt"]
    assert g.status(str(r))["counts"]["staged"] == 1
    d.cleanup()


def test_t11_symlink_inside_to_inside_link_name_echo():
    d, r = _s2_repo()
    (r / "real").mkdir()
    (r / "real" / "f.txt").write_text("one\n")
    _s2_run(r, "add", "real/f.txt")
    _s2_run(r, "commit", "-m", "real")
    if not _s2_link(r / "real", r / "link"):
        d.cleanup(); pytest.skip("directory-link fixture unavailable")
    res = g.safe_call(g.diff, str(r), paths=[str(r / "link" / "f.txt")])
    assert res["ok"]
    assert res["paths"] == ["link/f.txt"]  # LINK-NAME spelling, not resolved target
    rel = g.safe_call(g.diff, str(r), paths=["link/f.txt"])
    assert res["diff"] == rel["diff"]  # verdict-equivalence invariant
    d.cleanup()


def test_t12_symlink_inside_to_outside_escapes():
    d, r = _s2_repo()
    outside = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    out_root = Path(outside.name)
    (out_root / "o.txt").write_text("out\n")
    if not _s2_link(out_root, r / "escape"):
        d.cleanup(); outside.cleanup(); pytest.skip("directory-link fixture unavailable")
    res = g.safe_call(g.diff, str(r), paths=[str(r / "escape" / "o.txt")])
    assert res["error_name"] == "GIT_PATH_OUTSIDE_ROOT"
    assert res["reason_code"] == "escapes_root"
    d.cleanup(); outside.cleanup()


def test_t13_alias_prefix_rejects_with_a0_guidance():
    d, r = _s2_repo()
    alias = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    alias_root = Path(alias.name)
    if not _s2_link(r, alias_root / "repoalias"):
        d.cleanup(); alias.cleanup(); pytest.skip("directory-link fixture unavailable")
    spelled = str(alias_root / "repoalias" / "a.txt")
    res = g.safe_call(g.diff, str(r), paths=[spelled])
    assert res["error_name"] == "INVALID_GIT_PATHSPEC"
    assert res["reason_code"] == "absolute_path"
    assert res["ca_next_tool"] == "git_diff"
    assert "paths=['a.txt']" in res["ca_next_action"]
    d.cleanup(); alias.cleanup()


def test_t14_unc_spelling_rejects_absolute():
    d, r = _s2_repo()
    # No network fixture: mock F2 so the UNC stat never touches the network.
    orig = g._absolute_branch_extras
    g._absolute_branch_extras = lambda root, value, tool: {}
    try:
        res = g.safe_call(g.diff, str(r), paths=["//server/share/x.py"])
    finally:
        g._absolute_branch_extras = orig
    assert res["error_name"] == "INVALID_GIT_PATHSPEC"
    assert res["reason_code"] == "absolute_path"
    d.cleanup()


def test_t15_dotdot_segments_lexical_normalize():
    d, r = _s2_repo()
    (r / "sub").mkdir()
    (r / "a.txt").write_text("dd\n")
    res = g.safe_call(g.diff, str(r), paths=[str(r / "sub" / ".." / "a.txt")])
    assert res["ok"] and res["paths"] == ["sub/../a.txt"]  # no collapsing
    clean = g.safe_call(g.diff, str(r), paths=["a.txt"])
    assert res["diff"] == clean["diff"]  # C-EXP-1: lexical .. diffs identically
    res2 = g.safe_call(g.diff, str(r), paths=[str(r / "sub" / ".." / ".." / "escape.txt")])
    assert res2["error_name"] == "GIT_PATH_OUTSIDE_ROOT"
    assert res2["reason_code"] == "escapes_root"
    d.cleanup()


def test_t16_case_variant_dir_segment_lexical():
    d, r = _s2_repo()
    (r / "sub").mkdir()
    (r / "sub" / "f.txt").write_text("one\n")
    _s2_run(r, "add", "sub/f.txt")
    _s2_run(r, "commit", "-m", "sub")
    (r / "sub" / "f.txt").write_text("two\n")
    res = g.safe_call(g.diff, str(r), paths=[str(r / "SUB" / "f.txt")])
    rel = g.safe_call(g.diff, str(r), paths=["SUB/f.txt"])
    assert res["ok"] == rel["ok"]
    assert res["diff"] == rel["diff"]
    assert res["paths"] == ["SUB/f.txt"]  # no casing repair
    d.cleanup()


def test_t17_nonexistent_inside_names_relative_spec():
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=[str(r / "missing_xyz_abs.txt")])
    assert res["error_name"] == "PATHSPEC_NOT_FOUND"
    assert res["pathspecs"] == ["missing_xyz_abs.txt"]
    assert res["ca_next_tool"] == "git_status"
    d.cleanup()


def test_t18_worktree_deleted_tracked_absolute_diffs():
    d, r = _s2_repo()
    (r / "a.txt").unlink()
    res = g.safe_call(g.diff, str(r), paths=[str(r / "a.txt")])
    assert res["ok"] and "deleted file mode" in res["diff"]
    d.cleanup()


def test_t19_schema_descriptions_state_new_contract():
    for tool, prefix in (("git_diff", "Relative pathspec allowlist — "),
                         ("git_stage", "Relative paths to stage — "),
                         ("git_unstage", "Relative paths to unstage — ")):
        desc = _spec_props(tool)["paths"]["description"]
        assert desc == prefix + _OUTSIDE_REPO_SUFFIX
        assert "absolute path inside this repo" in desc
        assert "normalized" in desc
        assert "outside this repo is still rejected" in desc
    for tool in ("git_stage", "git_unstage"):
        pf = _spec_props(tool)["paths_file"]["description"]
        assert "absolute entries inside this repo are normalized" in pf


def test_t21_response_echo_relative_no_rewrite_marker():
    d, r = _s2_repo()
    (r / "a.txt").write_text("echo\n")
    res = g.safe_call(g.diff, str(r), paths=[str(r / "a.txt")])
    assert res["paths"] == ["a.txt"]
    assert "normalized_pathspecs" not in res
    assert set(res) == {"ok", "scope", "diff", "truncated", "total_chars",
                        "paths", "whitespace_errors", "exit_code"}
    d.cleanup()


def test_t22_drive_relative_remainder():
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=[str(r) + "/" + r.drive + "foo"])
    assert res["error_name"] == "INVALID_GIT_PATHSPEC"
    assert res["reason_code"] == "drive_relative"
    d.cleanup()


def test_t23_leading_dash_remainder():
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=[str(r) + "/-rf"])
    assert res["error_name"] == "INVALID_GIT_PATHSPEC"
    assert res["reason_code"] == "leading_dash_or_colon"
    d.cleanup()


def test_t24_leading_colon_remainder():
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=[str(r) + "/:x"])
    assert res["error_name"] == "INVALID_GIT_PATHSPEC"
    assert res["reason_code"] == "leading_dash_or_colon"
    d.cleanup()


def test_t25_magic_prefix_remainder():
    d, r = _s2_repo()
    res = g.safe_call(g.diff, str(r), paths=[str(r) + "/:(glob)file"])
    assert res["error_name"] == "INVALID_GIT_PATHSPEC"
    assert res["reason_code"] == "wildcard_or_magic"
    d.cleanup()


def test_t26_interior_whitespace_remainder_restrips():
    d, r = _s2_repo()
    (r / "a.txt").write_text("ws\n")
    res = g.safe_call(g.diff, str(r), paths=[str(r) + "/ a.txt"])
    assert res["ok"] and res["paths"] == ["a.txt"]
    d.cleanup()


def test_t27_absolute_remainder_reject_only_strip_once():
    d, r = _s2_repo()
    # F2 mocked for the UNC remainder (no network); the strip happens once.
    orig = g._absolute_branch_extras
    g._absolute_branch_extras = lambda root, value, tool: {}
    try:
        for spec in (str(r) + "/" + r.drive + "/x",
                     str(r) + "/" + str(r),
                     str(r) + "/" + str(r) + "/file",
                     str(r) + "///server/share/x.py"):
            res = g.safe_call(g.diff, str(r), paths=[spec])
            assert res["error_name"] == "INVALID_GIT_PATHSPEC", spec
            assert res["reason_code"] == "absolute_path", spec
    finally:
        g._absolute_branch_extras = orig
    # F2 computed ON THE REMAINDER: <root>/<root> -> a0 guidance paths=['.']
    res = g.safe_call(g.diff, str(r), paths=[str(r) + "/" + str(r)])
    assert res["reason_code"] == "absolute_path"
    assert "paths=['.']" in res["ca_next_action"]
    # denylist sub-shape: step 4 on the ORIGINAL spelling, before any rewrite
    res = g.safe_call(g.diff, str(r), paths=[str(r) + "/" + r.drive + "/repo/.git/config"])
    assert res["error_name"] == "PATHSPEC_DENYLISTED"
    d.cleanup()
