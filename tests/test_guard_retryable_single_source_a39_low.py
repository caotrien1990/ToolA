"""A39 leftover: one question, one answer -- per-guard `retryable` must not
contradict the top-level flag the caller emits.

The defect, measured on the live helper before the fix:

    describe_blocking_guards(["BUSY_SNAPSHOT_STALE", "IN_FLIGHT_WORKERS_ACTIVE"])
      -> BUSY_SNAPSHOT_STALE.retryable   == True     <-- "go ahead, retry"
    is_retryable_stale_block(same reasons) == False  <-- "no, a worker is live"

`retryable` was literally `name in _STALE_READING_REASONS`, i.e. a byte-for-byte
duplicate of the `is_stale` field sitting right next to it, under a name that
promises something else entirely. On a single-reason set the two questions happen
to coincide, which is why every existing test passed. They diverge exactly on the
MIXED set -- and that is the dangerous direction: a client trusting the per-guard
flag retries a restart that a live in-flight worker is legitimately holding, which
is the one outcome the guard exists to prevent.

"Should I retry?" is a property of the whole reason set. `is_stale` keeps the
per-reason information, so nothing is lost by making `retryable` mean what it says.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from orchestrator.lifecycle.restart_guard import (  # noqa: E402
    describe_blocking_guards,
    is_retryable_stale_block,
)

STALE = "BUSY_SNAPSHOT_STALE"
LIVE = "IN_FLIGHT_WORKERS_ACTIVE"

# Every set below must satisfy: per-guard retryable == top-level retryable.
CASES = [
    [STALE],
    [LIVE],
    ["COUNCIL_SLOT_ACTIVE"],
    [STALE, LIVE],
    [LIVE, STALE],                      # order must not change the verdict
    [STALE, "REQUESTS_IN_FLIGHT", "EXECUTOR_IN_FLIGHT"],
    ["BUSY_SNAPSHOT_MISSING", STALE],   # two stale readings, still retryable
    [],
]


class GuardRetryableSingleSourceTests(unittest.TestCase):

    def test_per_guard_never_contradicts_top_level(self):
        for reasons in CASES:
            with self.subTest(reasons=reasons):
                top = is_retryable_stale_block(reasons)
                for guard in describe_blocking_guards(reasons):
                    self.assertEqual(
                        guard["retryable"], top,
                        f"{guard['name']} says retryable={guard['retryable']} "
                        f"but the caller emits {top} for {reasons}")

    def test_the_mixed_set_is_the_regression_that_bit_us(self):
        """Single-reason sets hid this for the whole life of the defect."""
        guards = {g["name"]: g for g in describe_blocking_guards([STALE, LIVE])}
        self.assertFalse(guards[STALE]["retryable"],
                         "a live worker is holding the restart; retrying is wrong")
        self.assertFalse(guards[LIVE]["retryable"])

    def test_is_stale_still_carries_the_per_reason_truth(self):
        """The fix must not flatten away information, only rename its meaning."""
        guards = {g["name"]: g for g in describe_blocking_guards([STALE, LIVE])}
        self.assertTrue(guards[STALE]["is_stale"])
        self.assertFalse(guards[LIVE]["is_stale"])

    def test_retryable_is_no_longer_a_duplicate_of_is_stale(self):
        """If these two fields are always equal again, the defect is back."""
        guards = describe_blocking_guards([STALE, LIVE])
        self.assertTrue(
            any(g["is_stale"] != g["retryable"] for g in guards),
            "retryable collapsed back into a copy of is_stale")

    def test_stale_only_still_advertises_retry(self):
        """The A39 win must survive: a lone stale reading is retryable."""
        stale = {g["name"]: g for g in describe_blocking_guards([STALE])}[STALE]
        self.assertTrue(stale["retryable"])
        self.assertIn("retry", stale["how_to_clear"].lower())

    def test_payload_shape_is_unchanged(self):
        """Downstream asserts an exact key set; adding/removing a key breaks them."""
        for guard in describe_blocking_guards([STALE, LIVE]):
            self.assertEqual(
                set(guard),
                {"name", "held_by", "is_stale", "retryable", "how_to_clear"})

    def test_duplicate_reasons_collapse_once(self):
        names = [g["name"] for g in describe_blocking_guards([STALE, STALE, LIVE])]
        self.assertEqual(names, [STALE, LIVE])


if __name__ == "__main__":
    unittest.main()
