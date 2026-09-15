# pyright: reportOptionalMemberAccess=false, reportArgumentType=false
"""P3 regression: write_transaction auto-chains expected_hash per file.

Multi-hunk batches on the SAME file used to die with
FILE_CHANGED_OUTSIDE_ORCHESTRATOR because op1 never refreshed the write-gate
read token before op2 ran. The apply loop now feeds each op's new_hash into
the next op on the same resolved file as expected_hash (explicit hashes are
never overridden), so auto-revert only fires on real errors.
"""
import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import code_tools


class TestWriteTransactionHashChain(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix='txn_hash_chain_test_'))
        self.project_path = self.tempdir

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _make_file(self, name, lines):
        fp = self.project_path / name
        fp.write_text(''.join(f'# line {n}\n' for n in range(1, lines + 1)),
                      encoding='utf-8')
        return fp

    def _map_and_read(self, *files):
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))
        for fp in files:
            # Seed the write-gate read token (mirrors CA read-before-write).
            asyncio.run(code_tools.read_file_range(
                str(fp), 1, 200, project_path=str(self.project_path)))

    def test_multi_hunk_same_file_commits_via_auto_chain(self):
        fp = self._make_file('chain.py', 10)
        self._map_and_read(fp)

        result = json.loads(asyncio.run(code_tools.write_transaction([
            {'file_path': 'chain.py', 'start_line': 8, 'end_line': 9,
             'new_content': '# bottom A\n# bottom B\n'},
            {'file_path': 'chain.py', 'start_line': 2, 'end_line': 3,
             'new_content': '# top A\n# top B\n'},
        ], project_path=str(self.project_path))))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertEqual(result.get('applied_count'), 2, msg=result)
        text = fp.read_text(encoding='utf-8')
        self.assertIn('# top A', text)
        self.assertIn('# bottom B', text)
        self.assertNotIn('FILE_CHANGED_OUTSIDE_ORCHESTRATOR', json.dumps(result))

    def test_interleaved_files_chain_independently(self):
        fx = self._make_file('x.py', 8)
        fy = self._make_file('y.py', 8)
        self._map_and_read(fx, fy)

        result = json.loads(asyncio.run(code_tools.write_transaction([
            {'file_path': 'x.py', 'start_line': 7, 'end_line': 8,
             'new_content': '# x bottom\n'},
            {'file_path': 'y.py', 'start_line': 7, 'end_line': 8,
             'new_content': '# y bottom\n'},
            {'file_path': 'x.py', 'start_line': 1, 'end_line': 2,
             'new_content': '# x top\n'},
            {'file_path': 'y.py', 'start_line': 1, 'end_line': 2,
             'new_content': '# y top\n'},
        ], project_path=str(self.project_path))))

        self.assertTrue(result.get('ok'), msg=result)
        self.assertEqual(result.get('applied_count'), 4, msg=result)
        tx = fx.read_text(encoding='utf-8')
        ty = fy.read_text(encoding='utf-8')
        self.assertIn('# x top', tx)
        self.assertIn('# x bottom', tx)
        self.assertNotIn('# y top', tx)
        self.assertNotIn('# y bottom', tx)
        self.assertIn('# y top', ty)
        self.assertIn('# y bottom', ty)
        self.assertNotIn('# x top', ty)
        self.assertNotIn('# x bottom', ty)

    def test_explicit_wrong_expected_hash_still_fails_and_reverts(self):
        fp = self._make_file('guard.py', 8)
        original = fp.read_text(encoding='utf-8')
        self._map_and_read(fp)

        result = json.loads(asyncio.run(code_tools.write_transaction([
            {'file_path': 'guard.py', 'start_line': 7, 'end_line': 8,
             'new_content': '# changed bottom\n'},
            {'file_path': 'guard.py', 'start_line': 1, 'end_line': 2,
             'new_content': '# changed top\n',
             'expected_hash': '0' * 64},
        ], project_path=str(self.project_path))))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'),
                         'BATCH_VERIFY_FAILED_AUTO_REVERT', msg=result)
        self.assertTrue(result.get('auto_reverted'), msg=result)
        inner = (result.get('apply_results') or [{}])[-1]
        self.assertEqual(inner.get('error_name'),
                         'EXPECTED_HASH_MISMATCH', msg=result)
        self.assertEqual(fp.read_text(encoding='utf-8'), original)

    def test_middle_hunk_real_error_restores_pre_transaction_state(self):
        fp = self._make_file('mid.py', 8)
        original = fp.read_text(encoding='utf-8')
        self._map_and_read(fp)

        result = json.loads(asyncio.run(code_tools.write_transaction([
            {'file_path': 'mid.py', 'start_line': 7, 'end_line': 8,
             'new_content': '# changed bottom\n'},
            {'file_path': 'mid.py', 'start_line': 1, 'end_line': 2,
             'new_content': 'from nonexistent_mod_xyz import x\n# changed top\n'},
        ], project_path=str(self.project_path))))

        self.assertFalse(result.get('ok'), msg=result)
        self.assertEqual(result.get('error_name'),
                         'BATCH_VERIFY_FAILED_AUTO_REVERT', msg=result)
        self.assertTrue(result.get('rollback_verified'), msg=result)
        # Failure must come from the REAL import error (the auto-chained hash
        # let op2 through the freshness gate), not from a stale internal hash.
        inner = (result.get('apply_results') or [{}])[-1]
        self.assertEqual(inner.get('error_name'), 'IMPORT_ERROR_PRE_WRITE',
                         msg=result)
        self.assertEqual(fp.read_text(encoding='utf-8'), original)



    def test_top_down_three_hunks_same_file_no_file_changed(self):
        fp = self._make_file('topdown.py', 10)
        self._map_and_read(fp)

        result = json.loads(asyncio.run(code_tools.write_transaction([
            {'file_path': 'topdown.py', 'start_line': 1, 'end_line': 1,
             'new_content': '# T1\n'},
            {'file_path': 'topdown.py', 'start_line': 3, 'end_line': 3,
             'new_content': '# T3\n'},
            {'file_path': 'topdown.py', 'start_line': 5, 'end_line': 5,
             'new_content': '# T5\n'},
        ], project_path=str(self.project_path))))

        dumped = json.dumps(result)
        self.assertNotIn('FILE_CHANGED_OUTSIDE_ORCHESTRATOR', dumped)
        self.assertTrue(result.get('ok'), msg=result)
        self.assertEqual(result.get('applied_count'), 3, msg=result)
        text = fp.read_text(encoding='utf-8')
        self.assertIn('# T1', text)
        self.assertIn('# T3', text)
        self.assertIn('# T5', text)

    def test_http_shaped_no_read_file_range_between_ops(self):
        fp = self._make_file('http_shape.py', 8)
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        result = json.loads(asyncio.run(code_tools.write_transaction([
            {'file_path': 'http_shape.py', 'start_line': 7, 'end_line': 8,
             'new_content': '# bottom A\n# bottom B\n'},
            {'file_path': 'http_shape.py', 'start_line': 1, 'end_line': 2,
             'new_content': '# top A\n# top B\n'},
        ], project_path=str(self.project_path))))

        dumped = json.dumps(result)
        self.assertNotIn('FILE_CHANGED_OUTSIDE_ORCHESTRATOR', dumped)
        self.assertTrue(result.get('ok'), msg=result)
        text = fp.read_text(encoding='utf-8')
        self.assertIn('# top A', text)
        self.assertIn('# bottom B', text)

    def test_explicit_correct_expected_hash_not_overwritten(self):
        fp = self._make_file('keep_hash.py', 8)
        self._map_and_read(fp)
        current = code_tools._sha256_file(fp)

        result = json.loads(asyncio.run(code_tools.write_transaction([
            {'file_path': 'keep_hash.py', 'start_line': 7, 'end_line': 8,
             'new_content': '# kept bottom\n',
             'expected_hash': current},
            {'file_path': 'keep_hash.py', 'start_line': 1, 'end_line': 2,
             'new_content': '# kept top\n'},
        ], project_path=str(self.project_path))))

        dumped = json.dumps(result)
        self.assertNotIn('FILE_CHANGED_OUTSIDE_ORCHESTRATOR', dumped)
        self.assertTrue(result.get('ok'), msg=result)
        text = fp.read_text(encoding='utf-8')
        self.assertIn('# kept top', text)
        self.assertIn('# kept bottom', text)


if __name__ == '__main__':
    unittest.main()
