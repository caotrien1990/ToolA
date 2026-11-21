"""Regression tests for two 2026-11-21 claims (working-tree code; the daemon
serves old code until a restart, which both claims forbid):

1. SEARCH_CODE_PATH_REQUIREMENT_CLAIM_OPE_20261121 — schema/runtime mismatch:
   the published schema said path/project_path optional (default "") while the
   runtime rejected their absence, and the error carried no recoverable value.
2. WRITE_TRANSACTION_BLIND_ERROR_CLAIM_OPE_20261121 — write_transaction failure
   messages read only sub['error'] and collapsed every other error shape to the
   literal 'unknown'.

WHY these assertions: a contract that invites a call the runtime rejects burns
CA retries; an error that names no cause cannot be fixed without guessing.
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import orchestrator_server as osrv  # noqa: E402
import tools_v15  # noqa: E402
from orchestrator.tools import search as search_mod  # noqa: E402


def _run(coro):
    return json.loads(asyncio.run(coro))


class TestSearchCodePathRequirement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sc_path_req_')
        self.root = Path(self.tmp.name)
        (self.root / 'probe.py').write_text('def target_symbol():\n    return 1\n', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    # ── contract: schema declares the requirement ──
    def test_schema_declares_anyof_requirement(self):
        params = osrv._schema_for_tool('search_code')['parameters']
        anyof = params.get('anyOf')
        self.assertTrue(anyof, msg=params.keys())
        names = {tuple(b['required']) for b in anyof}
        self.assertIn(('project_path',), names)
        self.assertIn(('path',), names)

    def test_schema_no_misleading_empty_default(self):
        props = osrv._schema_for_tool('search_code')['parameters']['properties']
        for f in ('path', 'project_path'):
            self.assertNotIn('default', props[f], msg=f)
            self.assertIn('REQUIRED' if f == 'project_path' else 'anyOf', props[f]['description'])

    def test_payload_example_has_real_path(self):
        ex = osrv._payload_example_for_tool('search_code', osrv._schema_for_tool('search_code'))
        self.assertNotEqual(ex['project_path'], 'D:/path/to/project')
        self.assertTrue(Path(ex['project_path']).is_absolute() or ':' in ex['project_path'], ex)
        self.assertIn('query', ex)

    # ── error actionable in one retry ──
    def test_missing_path_error_is_actionable(self):
        out = _run(search_mod.search_code(query='target_symbol', mode='text'))
        self.assertFalse(out['ok'])
        self.assertEqual(out['error_name'], 'MISSING_REQUIRED_FIELD')
        self.assertIn('required_fields', out)
        self.assertIn('accepted_fields', out)
        self.assertIn('project_path', out['accepted_fields'])
        cp = out.get('corrected_payload')
        self.assertIsInstance(cp, dict)
        self.assertIn('project_path', cp)
        self.assertEqual(cp['query'], 'target_symbol')  # caller intent preserved

    def test_one_retry_with_project_path_succeeds(self):
        out = _run(search_mod.search_code(query='target_symbol', mode='text'))
        cp = dict(out['corrected_payload'])
        cp['project_path'] = str(self.root)
        out2 = _run(search_mod.search_code(**cp))
        self.assertTrue(out2.get('ok'), msg=out2)

    def test_with_project_path_succeeds(self):
        out = _run(search_mod.search_code(query='target_symbol', mode='text', project_path=str(self.root)))
        self.assertTrue(out.get('ok'), msg=out)

    def test_legacy_path_alias_still_works(self):
        out = _run(search_mod.search_code(query='target_symbol', mode='text', path=str(self.root)))
        self.assertTrue(out.get('ok'), msg=out)


class TestWriteTransactionBlindError(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wt_blind_err_')
        self.root = Path(self.tmp.name)
        (self.root / 'a.py').write_text('# one\n# two\n# three\n', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()
    def _txn(self, sub_result, fail_phase='dry_run'):
        # dry_run must pass so the apply phase can be reached when requested.
        async def fake_dispatch(**kwargs):
            if kwargs.get('dry_run') and fail_phase == 'apply':
                return json.dumps({'ok': True, 'preview': {'lines_removed': ['# one'], 'lines_added': ['# x']}})
            return json.dumps(sub_result)
        with mock.patch('code_tools.write_fix', side_effect=fake_dispatch), \
             mock.patch('code_tools.write_new_file', side_effect=fake_dispatch), \
             mock.patch('code_tools.backup_project', return_value=json.dumps({'ok': True})), \
             mock.patch('code_tools.post_write_batch_verify', return_value=json.dumps({'ok': True})):
            return _run(tools_v15.write_transaction(
                [{'file_path': 'a.py', 'start_line': 1, 'end_line': 1, 'new_content': '# x\n'}],
                project_path=str(self.root)))

    # ── helper fallback chain ──
    def test_helper_error_field_wins(self):
        self.assertEqual(tools_v15._sub_error_text({'error': 'boom', 'error_name': 'X'}), 'boom')

    def test_helper_error_name_plus_detail(self):
        t = tools_v15._sub_error_text({'error_name': 'EXPECTED_HASH_MISMATCH', 'reason': 'hash drift'})
        self.assertIn('EXPECTED_HASH_MISMATCH', t)
        self.assertIn('hash drift', t)

    def test_helper_error_name_alone(self):
        self.assertEqual(tools_v15._sub_error_text({'error_name': 'GUARD_DENIED'}), 'GUARD_DENIED')

    def test_helper_no_info_shows_shape(self):
        t = tools_v15._sub_error_text({'ok': False, 'foo': 1})
        self.assertIn('unknown', t)
        self.assertIn('ok=False', t)
        self.assertIn('foo', t)

    # ── dry_run phase: real error_name in message, never bare unknown ──
    def test_dry_run_names_real_error(self):
        out = self._txn({'ok': False, 'error_name': 'EXPECTED_HASH_MISMATCH', 'reason': 'hash drift'})
        self.assertFalse(out['ok'])
        self.assertEqual(out['error_name'], 'DRY_RUN_FAILED')
        self.assertIn('EXPECTED_HASH_MISMATCH', out['error'])
        self.assertIn('hash drift', out['error'])
        self.assertNotIn(': unknown', out['error'])

    def test_dry_run_error_field_preserved(self):
        out = self._txn({'ok': False, 'error': 'range out of bounds'})
        self.assertEqual(out['error_name'], 'DRY_RUN_FAILED')
        self.assertIn('range out of bounds', out['error'])

    # ── apply phase: same chain, error_name stays BATCH_VERIFY_FAILED_AUTO_REVERT ──
    def test_apply_names_real_error(self):
        out = self._txn({'ok': False, 'error_name': 'SYNTAX_ERROR_PRE_WRITE', 'message': 'bad indent'}, fail_phase='apply')
        self.assertFalse(out['ok'])
        self.assertEqual(out['error_name'], 'BATCH_VERIFY_FAILED_AUTO_REVERT')
        self.assertIn('SYNTAX_ERROR_PRE_WRITE', out['error'])
        self.assertIn('bad indent', out['error'])
        self.assertNotIn(': unknown', out['error'])
        self.assertTrue(out.get('auto_reverted'))


if __name__ == '__main__':
    unittest.main()
