"""F18/F19: per-file cache sidecars are written atomically, and the reindex
prune and build_index answer "is this cache JSON a sidecar?" with one matcher.

F19 was measured on the live ToolA cache: .orchestrator_cache/flag_a33b1.json,
0 bytes, with no extension left once ".json" is stripped. The prune keeps such
a name forever as cache infrastructure, while build_index parsed every *.json,
so the same file was warned about as a corrupt sidecar on every build. F18:
write_file_cache used a plain write_text, so a crash mid-write could leave a
torn sidecar where the previous good one had been.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import surgical_mapper as sm


def _mk(root: Path, rel: str, body: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _sidecar(root: Path, rel: str) -> Path:
    return root / sm.CACHE_DIR_NAME / f"{rel}.json"


def _tmp_leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name.endswith(".tmp"))


def _corrupt_warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "corrupt cache file" in r.getMessage()]


class TestAtomicSidecarWrite:
    def test_sidecar_round_trips_through_the_writer(self, tmp_path):
        src = _mk(tmp_path, "mod.py", "A = 1\n")
        meta = sm.write_file_cache(str(src), str(tmp_path))
        sidecar = _sidecar(tmp_path, "mod.py")
        assert meta["file"] == "mod.py"
        assert json.loads(sidecar.read_text(encoding="utf-8")) == json.loads(
            json.dumps(meta, ensure_ascii=False))
        assert _tmp_leftovers(sidecar.parent) == []

    def test_crash_mid_write_keeps_the_previous_sidecar(self, tmp_path, monkeypatch):
        src = _mk(tmp_path, "mod.py", "A = 1\n")
        sm.write_file_cache(str(src), str(tmp_path))
        sidecar = _sidecar(tmp_path, "mod.py")
        old_bytes = sidecar.read_bytes()
        assert json.loads(old_bytes.decode("utf-8"))["file"] == "mod.py"
        src.write_text("A = 1\n\n\ndef added():\n    return 2\n", encoding="utf-8")
        calls = []

        def torn(path, payload):
            calls.append(Path(path).name)
            Path(path).write_bytes(payload[: len(payload) // 2])
            raise OSError("simulated crash mid-write")

        monkeypatch.setattr(sm.search_snapshots, "_write_fsynced", torn)
        with pytest.raises(OSError, match="simulated crash mid-write"):
            sm.write_file_cache(str(src), str(tmp_path))
        # live-path control: the torn write really ran, and on a temp name,
        # never on the sidecar itself
        assert len(calls) == 1 and calls[0] != sidecar.name
        assert sidecar.read_bytes() == old_bytes
        assert _tmp_leftovers(sidecar.parent) == []


class TestOneSidecarMatcher:
    def test_matcher_shape(self):
        assert sm._is_sidecar_rel("mod.py.json")
        assert sm._is_sidecar_rel("pkg/sub/mod.py.json")
        assert sm._is_sidecar_rel("package.json.json")
        assert sm._is_sidecar_rel(".eslintrc.json.json")
        assert not sm._is_sidecar_rel("INDEX.json")
        assert not sm._is_sidecar_rel("flag_a33b1.json")
        # a dot in a directory name is not an extension of the file
        assert not sm._is_sidecar_rel("pkg.v2/flag.json")

    def test_every_scannable_source_gets_a_sidecar_shaped_name(self):
        # The matcher is exact only because nothing extensionless is scanned.
        assert "" not in sm.SUPPORTED_EXTENSIONS
        for ext in sorted(sm.SUPPORTED_EXTENSIONS):
            assert sm._is_sidecar_rel(f"pkg/mod{ext}.json"), ext

    def test_build_index_skips_non_sidecar_json(self, tmp_path, caplog):
        src = _mk(tmp_path, "mod.py", "A = 1\n")
        sm.write_file_cache(str(src), str(tmp_path))
        flag = tmp_path / sm.CACHE_DIR_NAME / "flag_x.json"
        flag.write_bytes(b"")
        caplog.set_level(logging.WARNING)
        index = sm.build_index(str(tmp_path))
        assert "mod.py" in index["files"]
        assert index["corrupt_cache_files"] == []
        assert _corrupt_warnings(caplog) == []

    def test_build_index_still_reports_a_corrupt_sidecar(self, tmp_path, caplog):
        good = _mk(tmp_path, "good.py", "G = 1\n")
        bad = _mk(tmp_path, "bad.py", "B = 1\n")
        sm.write_file_cache(str(good), str(tmp_path))
        sm.write_file_cache(str(bad), str(tmp_path))
        torn = _sidecar(tmp_path, "bad.py")
        torn.write_bytes(torn.read_bytes()[:7])
        caplog.set_level(logging.WARNING)
        index = sm.build_index(str(tmp_path))
        assert "good.py" in index["files"]
        assert "bad.py" not in index["files"]
        assert index["corrupt_cache_files"] == [str(torn.resolve())]
        assert len(_corrupt_warnings(caplog)) == 1

    def test_reindex_prune_and_build_index_agree(self, tmp_path, caplog):
        _mk(tmp_path, "keep.py", "K = 1\n")
        gone = _mk(tmp_path, "gone.py", "G = 1\n")
        sm._reindex_from_disk(tmp_path)
        cache = tmp_path / sm.CACHE_DIR_NAME
        assert _sidecar(tmp_path, "gone.py").exists()
        flag = cache / "flag_x.json"
        flag.write_bytes(b"")
        gone.unlink()
        caplog.set_level(logging.WARNING)
        index = sm._reindex_from_disk(tmp_path)
        # the prune keeps infrastructure and drops the orphaned sidecar ...
        assert flag.exists()
        assert not _sidecar(tmp_path, "gone.py").exists()
        # ... and build_index does not re-parse what the prune kept
        assert index["corrupt_cache_files"] == []
        assert _corrupt_warnings(caplog) == []
        assert set(index["files"]) == {"keep.py"}

    def test_full_rebuild_heals_a_torn_sidecar(self, tmp_path):
        src = _mk(tmp_path, "mod.py", "A = 1\n")
        sm._reindex_from_disk(tmp_path)
        torn = _sidecar(tmp_path, "mod.py")
        torn.write_bytes(b"{")
        index = sm._reindex_from_disk(tmp_path)
        assert index["corrupt_cache_files"] == []
        assert "mod.py" in index["files"]
        assert json.loads(torn.read_text(encoding="utf-8"))["file"] == "mod.py"
        assert src.exists()
