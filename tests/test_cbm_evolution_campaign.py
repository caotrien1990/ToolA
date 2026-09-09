"""AC-P6-S1: Jaccard 3/3,4=.75; rename/Unicode/merge/shallow/no-git; missing CC no fake hotspot."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.evolution import (
    MAX_FILES_PER_COMMIT,
    MAX_PAIRS_PER_COMMIT,
    compute_evolution,
    hash_author,
    jaccard,
    parse_git_log_z,
    pair_files,
)


def _sha_email(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _rec(email: str, files: list[str], *, rename: bool = False, date: str = "2026-09-01T00:00:00Z") -> dict:
    return {
        "hash": "x",
        "date": date,
        "email": email,
        "author_id": hash_author(email),
        "files": list(files),
        "rename_in_commit": rename,
    }


def _git(root: Path, *args: str, check: bool = True, extra_env: dict | None = None) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_AUTHOR_NAME"] = "Alice"
    env["GIT_AUTHOR_EMAIL"] = "alice@example.com"
    env["GIT_COMMITTER_NAME"] = "Alice"
    env["GIT_COMMITTER_EMAIL"] = "alice@example.com"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=check,
        env=env,
        shell=False,
    )


def _init_repo(root: Path) -> None:
    _git(root, "init", "-b", "main")
    _git(root, "-c", "user.email=alice@example.com", "-c", "user.name=Alice", "commit", "--allow-empty", "-m", "seed")


class TestJaccardOracle:
    def test_cochange3_counts3_4_is_075(self):
        assert jaccard(3, 3, 4) == 0.75
        records = [
            _rec("a@x.com", ["a.py", "b.py"]),
            _rec("a@x.com", ["a.py", "b.py"]),
            _rec("a@x.com", ["a.py", "b.py"]),
            _rec("a@x.com", ["b.py"]),
        ]
        out = compute_evolution("", records=records)
        assert out["status"] == "ok"
        assert len(out["coupling"]) == 1
        row = out["coupling"][0]
        assert {row["file_a"], row["file_b"]} == {"a.py", "b.py"}
        assert row["co_change_count"] == 3
        assert {row["changes_a"], row["changes_b"]} == {3, 4}
        assert row["jaccard"] == 0.75


class TestPairCap:
    def test_fifty_files_yield_1225_pairs_and_dropped_count(self):
        files = [f"f{i:02d}.py" for i in range(51)]
        capped, dropped = pair_files(files)
        assert dropped == 1
        assert len(capped) == MAX_FILES_PER_COMMIT
        assert MAX_PAIRS_PER_COMMIT == 1225
        out = compute_evolution("", records=[_rec("a@x.com", files)])
        assert out["files_dropped_pair_cap"] == 1
        assert out["pairs_considered"] == 1225
        assert out["truncated"] is True


class TestMissingCcNoFakeHotspot:
    def test_missing_cc_does_not_invent_hotspot(self):
        records = [_rec("a@x.com", ["a.py", "b.py"])] * 5
        out = compute_evolution("", records=records, complexity_map=None)
        assert out["hotspots"] == []
        assert "missing_cc_no_fake_hotspot" in out["metric_notes"]
        with_cc = compute_evolution(
            "", records=records, complexity_map={"a.py": 4}
        )
        files = {h["file"] for h in with_cc["hotspots"]}
        assert "a.py" in files
        assert "b.py" not in files
        assert with_cc["hotspots"][0]["risk_score"] == 5 * 4


class TestAuthorHash:
    def test_full_64_hex_lowercase_email_not_name(self):
        hid = hash_author("  Alice@Example.COM ")
        assert hid == _sha_email("alice@example.com")
        assert len(hid) == 64
        assert hash_author(None) is None
        assert hash_author("   ") is None
        out = compute_evolution("", records=[_rec("  Alice@Example.COM ", ["a.py"])])
        assert out["authors"][0]["primary_author_id"] == hid
        assert "Alice" not in str(out["authors"])


class TestRenameUnicodeMergeParser:
    def test_rename_uses_new_path_and_flags_partial(self):
        blob = (
            b"\x1eaaa\x002026-09-01T00:00:00Z\x00alice@example.com\x00Alice\x00parent\x00"
            b"R100\x00old.py\x00new.py\x00"
        )
        records, stats = parse_git_log_z(blob)
        assert stats["renames"] == 1
        assert records[0]["files"] == ["new.py"]
        assert records[0]["rename_in_commit"] is True
        out = compute_evolution("", records=records)
        assert out["rename_history_partial"] is True
        assert [c["file"] for c in out["churn"]] == ["new.py"]
        assert out["churn"][0]["commit_count"] == 1

    def test_newline_before_status_is_framing_not_path(self):
        blob = (
            b"\x1eccc\x002026-09-01T00:00:00Z\x00a@x.com\x00A\x00p\x00"
            b"\nR100\x00old.py\x00new.py\x00"
            b"\nA\x00caf\xc3\xa9.py\x00"
        )
        records, _ = parse_git_log_z(blob)
        assert records[0]["files"] == ["new.py", "café.py"]
        assert records[0]["rename_in_commit"] is True

    def test_unicode_and_tab_in_path_survive_nul_parser(self):
        blob = (
            b"\x1ebbb\x002026-09-01T00:00:00Z\x00bob@example.com\x00Bob\x00p\x00"
            b"M\x00caf\xc3\xa9.py\x00"
            b"A\x00dir/te\xcc\x87p.py\x00"
            b"M\x00weird\tname.py\x00"
        )
        records, _ = parse_git_log_z(blob)
        files = records[0]["files"]
        assert "café.py" in files
        assert any("tep.py" in f or "tėp.py" in f for f in files)
        assert any("\t" in f for f in files)

    def test_merge_commit_dropped_by_parser(self):
        blob = (
            b"\x1emmm\x002026-09-01T00:00:00Z\x00a@x.com\x00A\x00p1 p2\x00"
            b"M\x00a.py\x00M\x00b.py\x00"
            b"\x1esingle\x002026-09-01T00:00:00Z\x00a@x.com\x00A\x00p1\x00"
            b"M\x00a.py\x00"
        )
        records, stats = parse_git_log_z(blob)
        assert stats["merges_dropped"] == 1
        assert len(records) == 1
        assert records[0]["files"] == ["a.py"]


class TestNoGitShallowLive:
    def test_no_git_is_unavailable_not_zero_hotspot(self, tmp_path):
        out = compute_evolution(str(tmp_path))
        assert out["status"] == "unavailable"
        assert out["reason"] == "not_git_repository"
        assert out["hotspots"] == []
        assert out["coupling"] == []
        assert out["pass"] is False

    def test_rename_unicode_merge_shallow_live_repo(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        _init_repo(src)
        (src / "old.py").write_text("x\n", encoding="utf-8")
        _git(src, "add", "old.py")
        _git(src, "-c", "user.email=alice@example.com", "-c", "user.name=Alice", "commit", "-m", "add old")
        _git(src, "mv", "old.py", "new.py")
        uni_name = "café.py"
        (src / uni_name).write_text("u\n", encoding="utf-8")
        _git(src, "add", "--", uni_name)
        _git(src, "-c", "user.email=alice@example.com", "-c", "user.name=Alice", "commit", "-m", "rename+unicode")
        _git(src, "checkout", "-b", "side")
        (src / "side.py").write_text("s\n", encoding="utf-8")
        _git(src, "add", "side.py")
        _git(src, "-c", "user.email=bob@example.com", "-c", "user.name=Bob", "commit", "-m", "side")
        _git(src, "checkout", "main")
        merge = _git(
            src,
            "-c", "user.email=alice@example.com",
            "-c", "user.name=Alice",
            "merge", "--no-ff", "-m", "merge side", "side",
            check=False,
        )
        assert merge.returncode == 0, merge.stderr.decode("utf-8", errors="replace")
        out = compute_evolution(str(src), complexity_map=None)
        assert out["status"] in {"ok", "partial"}
        files = {c["file"] for c in out["churn"]}
        assert "new.py" in files
        assert "café.py" in files
        # first-parent + no-merges: merge commit itself is not a hotspot source
        assert out["reason"] != "not_git_repository"
        assert out["rename_history_partial"] is True
        assert out["hotspots"] == []

        clone = tmp_path / "shallow"
        subprocess.run(
            ["git", "clone", "--depth", "1", str(src), str(clone)],
            check=True,
            capture_output=True,
            shell=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        injected = compute_evolution(
            str(src), records=[_rec("a@x.com", ["new.py"])], shallow=True, history_truncated=True
        )
        assert injected["status"] == "partial"
        assert injected["shallow"] is True
        assert injected["history_truncated"] is True
        assert "shallow_history" in injected["metric_notes"]
        shallow = compute_evolution(str(clone))
        assert shallow["status"] in {"ok", "partial"}
        assert shallow["reason"] != "not_git_repository"
        if os.path.isfile(clone / ".git" / "shallow") or shallow["shallow"]:
            assert shallow["shallow"] is True
            assert shallow["history_truncated"] is True


class TestDeadline:
    def test_now_zero_forces_deadline(self):
        out = compute_evolution("", records=[], now=0.0)
        assert out["status"] == "unavailable"
        assert out["reason"] == "deadline"
        assert out["hotspots"] == []
