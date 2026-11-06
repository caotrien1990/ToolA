"""S3 budget-mode tests for the ToolA read path (read-limits campaign).

SOT: PLAN.md (toola-read-limits-20261106) section 3.1 (budget mode /
priority table / single-line cut declaration [OB-08]), section 4 step
S3, and the section 5 rows E1/E2/E4 these tests pin.

Covered:
  * fit_numbered_lines fitting a single read to a token budget (no test
    pinned this before S3) and the prefix-aware single-line cut;
  * the CAP binding at 12,000 tokens, INCLUDING the single-line case
    (fixture E1: one 200,000-char line);
  * the derived empty-line ceiling of 6,004 lines at the CAP (the
    section 3.1 estimator arithmetic, re-derived in the test);
  * precedence: an explicit max_lines clamps the lines DELIVERED inside
    the fit but never rewrites the selection -- selection echo,
    truncated and next_ranges stay on the original selection;
  * max_lines=0 in budget mode behaves as absent; a negative max_lines
    delivers exactly 1 line (legacy coercion);
  * the degenerate E1 outcomes per tiny budget (prefix "1: " = 3 bytes:
    budget 1 -> cut 0 bytes / est 1; 2 -> cut 4 / est 2; 3 -> cut 8 /
    est 3) [RV4-01];
  * the section 3.1 SOT case in-process on the real code_tools.py at
    the project root (23,483 lines at the pin): max_lines=3000 +
    token_budget=100000 -> selection 1-3000 intact, shown 1-907,
    estimated_tokens 11,987;
  * file_summary keeps its own budget semantics (no budget_mode keys);
  * legacy calls carry none of the new keys.

Like the S1 golden file, these run the handler in-process on tmp
fixtures -- never against the live daemon.
"""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

import pytest

import code_tools
from orchestrator.tools.read import adaptive
from orchestrator.tools.read import context as read_context

SUMMARY_TEXT = (
    "import os\nimport sys\n\n\n"
    "def alpha():\n    return 1\n\n\n"
    "def beta(x):\n    return x + 1\n\n\n"
    "class Gamma:\n    def method(self):\n        return 3\n"
)


def _clear_read_cache() -> None:
    with adaptive._CACHE_COND:
        adaptive._CACHE_BY_PATH.clear()
        adaptive._CACHE_BY_HASH.clear()
        adaptive._INFLIGHT.clear()
        adaptive._CACHE_BYTES = 0


@pytest.fixture(autouse=True)
def _deterministic_seed(monkeypatch):
    monkeypatch.setattr(
        code_tools, "_read_token_upsert", lambda *a, **k: True, raising=False)
    _clear_read_cache()
    yield
    _clear_read_cache()


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return path


def _read(tmp_path: Path, name: str, **kwargs) -> dict:
    raw = asyncio.run(code_tools.read_file_range(
        name, project_path=str(tmp_path), **kwargs))
    return json.loads(raw)


def _estimated(lines: list[str], start: int) -> int:
    # Independent re-derivation of the estimator contract:
    # ceil(utf8_bytes / 4) per numbered line, prefix and newline included.
    return sum(
        math.ceil(len(f"{start + i}: {line}\n".encode("utf-8")) / 4.0)
        for i, line in enumerate(lines))


# ---------------------------------------------------------------------------
# fit_numbered_lines unit level
# ---------------------------------------------------------------------------

def test_fit_stops_at_token_budget():
    lines = ["aaaa", "bbbb", "cccc", "dddd"]
    # Each numbered line "N: aaaa\n" is 8 bytes -> 2 tokens.
    selected, used = adaptive.fit_numbered_lines(
        lines, 1, max_lines=10, token_budget=6)
    assert selected == ["aaaa", "bbbb", "cccc"]
    assert used == 6
    # No budget: the line cap alone governs, exactly as legacy.
    selected, used = adaptive.fit_numbered_lines(
        lines, 1, max_lines=10, token_budget=0)
    assert selected == lines
    assert used == 8


def test_fit_single_line_cut_counts_prefix_and_newline():
    line = "a" * 100
    # Prefix "1: " is 3 bytes; the largest cut with
    # ceil((3 + cut + 1) / 4) <= budget is cut = budget * 4 - 4.
    for budget, cut_len, est in [(1, 0, 1), (2, 4, 2), (3, 8, 3)]:
        selected, used = adaptive.fit_numbered_lines(
            [line], 1, max_lines=10, token_budget=budget)
        assert selected == ["a" * cut_len]
        assert used == est


def test_fit_single_line_cut_binds_at_cap():
    selected, used = adaptive.fit_numbered_lines(
        ["a" * 200_000], 1, max_lines=10, token_budget=12_000)
    assert selected == ["a" * 47_996]
    assert used == 12_000


def test_cap_constant_single_home():
    assert read_context.READ_TOKEN_BUDGET_CAP == 12_000
    assert read_context.READ_TOKEN_BUDGET_CAP == read_context.BATCH_MAX_TOKENS
    # No second copy of the constant in the fit module (PLAN section 4 S3).
    assert not hasattr(adaptive, "READ_TOKEN_BUDGET_CAP")


# ---------------------------------------------------------------------------
# Budget mode through the handler
# ---------------------------------------------------------------------------

def test_budget_mode_fits_inside_selection(tmp_path):
    lines = [f"line {i:04d} alpha" for i in range(1, 301)]
    _write(tmp_path, "big.txt", "\n".join(lines) + "\n")
    body = _read(tmp_path, "big.txt", token_budget=100)

    assert body["ok"] is True
    assert body["budget_mode"] is True
    assert body["token_budget_effective"] == 100
    assert body["token_budget"] == 100
    assert "line_cut" not in body
    # Selection is the legacy span, untouched by the fit.
    assert body["selection"] == {
        "ok": True, "scope": "range", "name": None,
        "start_line": 1, "end_line": 300, "source": "line_range",
    }
    # The fit delivers the maximal prefix whose cost fits the budget.
    delivered = 0
    spent = 0
    for line in lines:
        cost = math.ceil(len(f"{delivered + 1}: {line}\n".encode()) / 4.0)
        if spent + cost > 100:
            break
        spent += cost
        delivered += 1
    assert delivered > 0
    assert body["shown"] == f"1-{delivered}"
    assert body["estimated_tokens"] == spent == _estimated(lines[:delivered], 1)
    assert body["estimated_tokens"] <= 100
    assert body["result"] == "\n".join(
        f"{i + 1}: {line}" for i, line in enumerate(lines[:delivered]))
    assert body["truncated"] is True
    assert body["auto_full_file"] is False


def test_budget_mode_keys_absent_in_legacy(tmp_path):
    _write(tmp_path, "note.txt", "one\ntwo\nthree\n")
    body = _read(tmp_path, "note.txt")
    assert "budget_mode" not in body
    assert "token_budget_effective" not in body
    assert "line_cut" not in body


def test_budget_mode_disables_auto_full(tmp_path):
    lines = [f"row {i}" for i in range(1, 11)]
    _write(tmp_path, "small.txt", "\n".join(lines) + "\n")
    legacy = _read(tmp_path, "small.txt")
    assert legacy["auto_full_file"] is True
    body = _read(tmp_path, "small.txt", token_budget=100_000)
    assert body["auto_full_file"] is False
    assert body["budget_mode"] is True
    assert body["shown"] == "1-10"
    assert body["truncated"] is False


def test_cap_binds_single_line_e1(tmp_path):
    _write(tmp_path, "e1.txt", "a" * 200_000 + "\n")
    body = _read(tmp_path, "e1.txt", token_budget=100_000)
    assert body["budget_mode"] is True
    assert body["token_budget_effective"] == 12_000
    assert body["estimated_tokens"] <= 12_000
    assert body["estimated_tokens"] == 12_000
    assert body["line_cut"] is True
    assert body["truncated"] is True
    assert body["shown"] == "1-1"
    assert body["result"] == "1: " + "a" * 47_996


def test_empty_line_ceiling_is_6004_at_cap(tmp_path):
    # Section 3.1 derivation, re-derived here from the estimator: a
    # numbered empty line costs ceil(bytes/4) = 1 token on lines 1-9 and
    # 2 tokens from line 10 on; the running total reaches 11,999 tokens
    # at line 6,004 and line 6,005 would cross the 12,000 CAP.
    running = 0
    crossing = None
    for i in range(1, 7001):
        running += math.ceil(len(f"{i}: \n".encode("utf-8")) / 4.0)
        if running > 12_000:
            crossing = i
            break
    assert crossing == 6005
    assert running - math.ceil(len("6005: \n".encode("utf-8")) / 4.0) == 11_999

    _write(tmp_path, "blank.txt", "\n".join([""] * 7000) + "\n")
    body = _read(tmp_path, "blank.txt", token_budget=100_000)
    assert body["budget_mode"] is True
    assert body["token_budget_effective"] == 12_000
    assert body["shown"] == "1-6004"
    assert body["estimated_tokens"] == 11_999
    assert body["truncated"] is True
    assert "line_cut" not in body


def test_precedence_max_lines_clamps_delivery_not_selection(tmp_path):
    lines = ["x=1"] * 300
    _write(tmp_path, "span.txt", "\n".join(lines) + "\n")
    body = _read(tmp_path, "span.txt", start_line=1, end_line=300,
                 max_lines=100, token_budget=100_000)
    assert body["budget_mode"] is True
    assert body["token_budget_effective"] == 12_000
    # At most (here: exactly) 100 lines delivered inside the fit...
    assert body["shown"] == "1-100"
    assert len(body["result"].split("\n")) == 100
    assert body["estimated_tokens"] == _estimated(lines[:100], 1)
    assert body["estimated_tokens"] < 12_000
    # ...while the selection echo stays the original 300-line span,
    # truncated is computed on that span, and next_ranges continue
    # from the original span.
    assert body["selection"]["start_line"] == 1
    assert body["selection"]["end_line"] == 300
    assert body["truncated"] is True
    assert {
        "file_path": str((tmp_path / "span.txt").resolve()),
        "scope": "range", "start_line": 101, "end_line": 300,
        "name": None, "kind": "continuation", "relevance_score": 1.0,
        "reason": "selected_scope_continuation", "source": "line_range",
    } in body["next_ranges"]


def test_max_lines_zero_in_budget_mode_behaves_as_absent(tmp_path):
    lines = [f"row {i}" for i in range(1, 51)]
    _write(tmp_path, "fifty.txt", "\n".join(lines) + "\n")
    absent = _read(tmp_path, "fifty.txt", token_budget=100_000)
    zero = _read(tmp_path, "fifty.txt", max_lines=0, token_budget=100_000)
    assert zero["shown"] == absent["shown"] == "1-50"
    assert zero["result"] == absent["result"]
    assert zero["estimated_tokens"] == absent["estimated_tokens"]
    assert zero["truncated"] is False


def test_max_lines_negative_in_budget_mode_delivers_one_line(tmp_path):
    lines = [f"row {i}" for i in range(1, 51)]
    _write(tmp_path, "fifty.txt", "\n".join(lines) + "\n")
    body = _read(tmp_path, "fifty.txt", max_lines=-3, token_budget=100_000)
    assert body["shown"] == "1-1"
    assert body["result"] == "1: row 1"
    assert body["selection"]["end_line"] == 50
    assert body["truncated"] is True


def test_degenerate_e1_outcomes_per_budget(tmp_path):
    # The first line sits at line 1 and its numbered cost exceeds each
    # budget below, so the single-line cut activates (PLAN section 4 S3).
    _write(tmp_path, "e1.txt", "a" * 100 + "\n")
    for budget, cut_len, est in [(1, 0, 1), (2, 4, 2), (3, 8, 3)]:
        body = _read(tmp_path, "e1.txt", token_budget=budget)
        assert body["budget_mode"] is True
        assert body["token_budget_effective"] == budget
        assert body["line_cut"] is True
        assert body["truncated"] is True
        assert body["shown"] == "1-1"
        assert body["estimated_tokens"] == est
        assert body["result"] == "1: " + "a" * cut_len


def test_file_summary_branch_is_not_budget_mode(tmp_path):
    _write(tmp_path, "summary.py", SUMMARY_TEXT)
    body = _read(tmp_path, "summary.py", scope="file_summary",
                 token_budget=2000)
    assert body["ok"] is True
    assert body["shown"] == "file_summary"
    # The branch keeps its own semantics: the caller budget is echoed
    # as-is (not capped, not re-labelled as budget mode).
    assert body["token_budget"] == 2000
    assert "budget_mode" not in body
    assert "token_budget_effective" not in body
    assert "line_cut" not in body


def test_sot_case_code_tools_at_cap():
    # Section 3.1 SOT case, in-process on the real file at the project
    # root (23,483 lines, clean at the pin): the end_line pins the
    # selection at 1-3000 exactly as section 3.1 states it; the CAP fit
    # then delivers shown 1-907 with estimated_tokens 11,987.
    root = Path(__file__).resolve().parents[1]
    assert (root / "code_tools.py").exists()
    raw = asyncio.run(code_tools.read_file_range(
        "code_tools.py", project_path=str(root),
        start_line=1, end_line=3000, max_lines=3000, token_budget=100_000))
    body = json.loads(raw)
    assert body["ok"] is True
    assert body["total_lines"] == 23_483
    assert body["selection"]["start_line"] == 1
    assert body["selection"]["end_line"] == 3000
    assert body["budget_mode"] is True
    assert body["token_budget_effective"] == 12_000
    assert body["shown"] == "1-907"
    assert body["estimated_tokens"] == 11_987
    assert body["truncated"] is True
