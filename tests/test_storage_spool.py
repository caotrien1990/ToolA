"""Spool primitive: the one mechanism two writers use instead of dropping work.

Every case here is a failure mode one of those two call sites actually hits. The
spool is called from inside ``except`` blocks, so "never raises" and "one torn
line does not strand the good lines" are correctness requirements, not polish.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.storage.spool import (
    DEFAULT_MAX_RECORDS,
    append_spool,
    drain_spool,
    read_spool,
    spool_depth,
)


class StorageSpoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.path = self.root / "nested" / "audit_spool.jsonl"

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_append_creates_parent_and_roundtrips_in_order(self) -> None:
        # The parent directory may not exist: the audit spool sits next to a
        # database whose cache directory is created lazily.
        self.assertFalse(self.path.parent.exists())
        for index in range(3):
            self.assertTrue(append_spool(self.path, {"i": index}))

        records, corrupt = read_spool(self.path)
        self.assertEqual(corrupt, 0)
        # Order matters: replay must re-stage oldest first, or an audit trail
        # comes back out of sequence.
        self.assertEqual([record["i"] for record in records], [0, 1, 2])
        self.assertEqual(spool_depth(self.path), 3)

    def test_append_returns_false_instead_of_raising(self) -> None:
        # A directory where the file should be is the cheapest portable stand-in
        # for "the filesystem said no". The caller is an except block: it needs a
        # False, not a second exception on top of the one it is handling.
        blocked = self.root / "as_a_directory"
        blocked.mkdir()
        self.assertFalse(append_spool(blocked, {"audit_id": "a1"}))
        self.assertEqual(spool_depth(blocked), 0)

    def test_non_serializable_payload_is_stringified_not_dropped(self) -> None:
        # Audit payloads carry arbitrary tool arguments, including objects json
        # cannot encode. Losing the record would defeat the spool, so default=str
        # degrades the field instead.
        self.assertTrue(append_spool(self.path, {"payload": {"obj": object()}}))
        records, corrupt = read_spool(self.path)
        self.assertEqual(corrupt, 0)
        self.assertEqual(len(records), 1)
        self.assertIsInstance(records[0]["payload"]["obj"], str)

    def test_torn_line_does_not_strand_good_records(self) -> None:
        # A process killed mid-append leaves a partial last line. Every record
        # written before it is still valid and must still replay.
        append_spool(self.path, {"i": 0})
        with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write('{"i": 1, "trunc')
        append_spool(self.path, {"i": 2})

        records, corrupt = read_spool(self.path)
        self.assertEqual(corrupt, 1)
        self.assertEqual([record["i"] for record in records], [0, 2])

    def test_non_dict_json_line_counts_as_corrupt(self) -> None:
        # A bare scalar is syntactically valid JSON but cannot be a record, and a
        # handler indexing it would raise inside the drain loop.
        append_spool(self.path, {"i": 0})
        with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("[1, 2, 3]\n")

        records, corrupt = read_spool(self.path)
        self.assertEqual(corrupt, 1)
        self.assertEqual(len(records), 1)

    def test_missing_file_reads_empty_rather_than_raising(self) -> None:
        # Normal steady state: nothing has ever been parked. Health publication
        # calls spool_depth on every metrics read, so this is the hot path.
        self.assertEqual(read_spool(self.root / "never_written.jsonl"), ([], 0))
        self.assertEqual(spool_depth(self.root / "never_written.jsonl"), 0)

    def test_drain_consumes_and_removes_the_file(self) -> None:
        for index in range(3):
            append_spool(self.path, {"i": index})
        seen: list[int] = []

        result = drain_spool(self.path, lambda record: bool(seen.append(record["i"])) or True)

        self.assertEqual(result, {"replayed": 3, "kept": 0, "corrupt": 0})
        self.assertEqual(seen, [0, 1, 2])
        # Removed, not left as an empty file: a zero-byte spool would keep
        # reappearing in health output as a file that exists but holds nothing.
        self.assertFalse(self.path.exists())

    def test_refused_records_are_kept_for_the_next_drain(self) -> None:
        # drain runs at writer start, while the real sink may still be down.
        for index in range(3):
            append_spool(self.path, {"i": index})

        result = drain_spool(self.path, lambda record: record["i"] == 0)

        self.assertEqual(result, {"replayed": 1, "kept": 2, "corrupt": 0})
        records, _ = read_spool(self.path)
        self.assertEqual([record["i"] for record in records], [1, 2])

    def test_raising_handler_keeps_the_record(self) -> None:
        # A handler that throws is the same situation as one that returns False:
        # the record did not reach its sink, so it must survive.
        append_spool(self.path, {"i": 0})

        def boom(record: dict) -> bool:
            raise RuntimeError("sink still down")

        result = drain_spool(self.path, boom)

        self.assertEqual(result, {"replayed": 0, "kept": 1, "corrupt": 0})
        self.assertEqual(spool_depth(self.path), 1)

    def test_drain_reports_corrupt_lines_and_drops_them(self) -> None:
        append_spool(self.path, {"i": 0})
        with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("not json at all\n")

        result = drain_spool(self.path, lambda record: True)

        self.assertEqual(result, {"replayed": 1, "kept": 0, "corrupt": 1})
        # Reported, not silently swallowed: the count is how an operator learns a
        # process died mid-append.
        self.assertFalse(self.path.exists())

    def test_drain_of_empty_spool_is_a_cheap_noop(self) -> None:
        self.assertEqual(
            drain_spool(self.root / "absent.jsonl", lambda record: True),
            {"replayed": 0, "kept": 0, "corrupt": 0},
        )

    def test_cap_drops_oldest_and_keeps_the_newest(self) -> None:
        # An outage lasting hours must not fill the disk. Newest wins because the
        # most recent audit rows are the ones a post-incident read needs.
        for index in range(6):
            append_spool(self.path, {"i": index}, max_records=4)

        records, _ = read_spool(self.path)
        self.assertEqual([record["i"] for record in records], [2, 3, 4, 5])

    def test_cap_default_is_bounded(self) -> None:
        # Guards against someone "simplifying" the default to None/0, which would
        # turn the spool back into an unbounded disk risk.
        self.assertGreater(DEFAULT_MAX_RECORDS, 0)
        self.assertLessEqual(DEFAULT_MAX_RECORDS, 100_000)

    def test_trim_rewrite_leaves_valid_jsonl(self) -> None:
        # The trim path rewrites the whole file; a bug there would corrupt every
        # remaining record at once, so assert the bytes still parse line by line.
        for index in range(5):
            append_spool(self.path, {"i": index}, max_records=3)

        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual([json.loads(line)["i"] for line in lines], [2, 3, 4])


if __name__ == "__main__":
    unittest.main()
