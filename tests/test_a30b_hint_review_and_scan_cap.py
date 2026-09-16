"""A30-B: the ca_hint_review auto-chain, and the ocr_scan output cap.

Two separate promises are pinned here.

The hint: git_stage and git_commit are where a diff first becomes reviewable, so they
carry a pointer to ocr_review. It must stay a STRING -- the moment staging silently
spawned a review, every commit would pay for a review nobody asked for.

The cap: a measured whole-repo `ocr_scan(preview_only=True)` returned 109_259 bytes and
the daemon had to spill it into a result_ref. A tool built for token discipline cannot be
the thing that blows the budget. The cap is only acceptable because it DECLARES what it
dropped: a truncation the caller cannot see reads as "that is all there is".
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

from orchestrator import git_tools as g
from orchestrator.tools import ocr_review as mod

from test_ocr_integration_p5 import _FakeSubprocess, _fake_install


def _git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          stdin=subprocess.DEVNULL, text=True)


def _repo():
    """Same shape as tests/test_git_tools.py:6 -- one commit, clean tree."""
    d = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    root = Path(d.name)
    _git(root, 'init')
    _git(root, 'config', 'user.name', 'Test')
    _git(root, 'config', 'user.email', 'test@example.com')
    (root / 'a.txt').write_text('one\n')
    _git(root, 'add', 'a.txt')
    _git(root, 'commit', '-m', 'init')
    return d, root


# ------------------------------------------------------------------- the hint

def test_stage_carries_a_pointer_to_the_workspace_review():
    d, r = _repo()
    try:
        (r / 'b.txt').write_text('b')
        staged = g.stage(str(r), ['b.txt'])
        hint = staged['ca_hint_review']
        assert 'ocr_review(' in hint
        # "workspace" is the mode ocr_review actually reports for an uncommitted tree
        # (measured: exit 0, 0.19s). Naming it keeps the hint checkable against reality.
        assert 'workspace' in hint
        assert '0 token' in hint
    finally:
        d.cleanup()


def test_commit_hint_names_the_commit_that_just_became_reviewable():
    d, r = _repo()
    try:
        (r / 'b.txt').write_text('b')
        staged = g.stage(str(r), ['b.txt'])
        done = g.commit(str(r), 'add b', staged['after']['staged_hash'])
        assert done['ok']
        # The point of the hint is that it is immediately runnable -- a generic
        # "try ocr_review" would make the caller go find the sha itself.
        assert done['commit'] in done['ca_hint_review']
        assert 'ocr_review(' in done['ca_hint_review']
    finally:
        d.cleanup()


def test_the_hint_is_a_string_and_no_review_was_run():
    """Rule 13 is hint-only. Nothing here may spawn OCR."""
    d, r = _repo()
    try:
        (r / 'b.txt').write_text('b')
        staged = g.stage(str(r), ['b.txt'])
        assert isinstance(staged['ca_hint_review'], str)
        # If staging had actually reviewed, the payload would carry OCR's own result
        # keys. Their absence is the evidence that nothing was executed.
        for leaked in ('total_files', 'reviewable_files', 'reviewable_count', 'llm_used'):
            assert leaked not in staged
    finally:
        d.cleanup()


# -------------------------------------------------------------- the scan cap

def _wire_scan(monkeypatch, tmp_path, stdout):
    home = tmp_path / 'a_home'
    home.mkdir(exist_ok=True)
    _fake_install(home)
    repo = tmp_path / 'repo'
    repo.mkdir(exist_ok=True)
    monkeypatch.setattr(mod, '_a_repo_root', lambda: str(home))
    monkeypatch.setattr(mod, 'subprocess', _FakeSubprocess(stdout=stdout))
    return repo


def _scan(repo):
    return json.loads(asyncio.run(mod.ocr_scan(project_path=str(repo))))


def test_a_long_list_is_capped_and_the_drop_is_declared(monkeypatch, tmp_path):
    body = {'files': [{'path': 'f%d.py' % i} for i in range(120)]}
    repo = _wire_scan(monkeypatch, tmp_path, json.dumps(body))
    out = _scan(repo)
    assert out['ok'] and len(out['files']) == 50
    assert out['max_items'] == 50
    # 120 - 50: the caller learns the exact size of what it is not seeing.
    assert out['items_omitted']['files'] == 70
    assert 'ca_hint_scope' in out


def test_a_short_result_is_untouched_and_claims_no_truncation(monkeypatch, tmp_path):
    body = {'files': [{'path': 'a.py'}, {'path': 'b.py'}]}
    repo = _wire_scan(monkeypatch, tmp_path, json.dumps(body))
    out = _scan(repo)
    assert len(out['files']) == 2
    # A cap that announces itself when it did nothing is noise, and worse, it trains the
    # caller to ignore the announcement on the run where it matters.
    assert 'items_omitted' not in out
    assert 'max_items' not in out


def test_the_normalized_findings_are_capped_too(monkeypatch, tmp_path):
    body = {'findings': [{'file': 'f%d.py' % i, 'line': i, 'message': 'm'}
                         for i in range(120)]}
    repo = _wire_scan(monkeypatch, tmp_path, json.dumps(body))
    out = _scan(repo)
    # findings_common is derived, so capping only the raw key would let the same 120
    # rows back in through the normalized view.
    assert len(out['findings']) == 50
    assert len(out['findings_common']) == 50
    assert out['items_omitted']['findings'] == 70
