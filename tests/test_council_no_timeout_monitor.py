import asyncio
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import orchestrator_server

class TestCouncilNoTimeoutMonitor(unittest.IsolatedAsyncioTestCase):
    def test_preserve_partial_artifact_before_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / 'answer.md'
            partial = Path(str(artifact) + '.partial')
            partial.write_text('evidence so far', encoding='utf-8')
            preserved = orchestrator_server._council_preserve_partial_artifact({
                'artifact_path': str(artifact), 'worker_pid': 123,
            })
            self.assertEqual(Path(preserved).read_text(encoding='utf-8'), 'evidence so far')
            self.assertEqual(Path(preserved), Path(str(artifact) + '.stalled.partial'))

    def test_worker_drain_ignores_legacy_timeout_values(self):
        proc = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(1.2); print("DONE")'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, stderr, timed_out, timeout_kind = orchestrator_server._council_communicate_idle(
            proc, None, 1, 1,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn('DONE', stdout)
        self.assertFalse(timed_out)
        self.assertEqual(timeout_kind, '')

    async def test_monitor_reconciles_dead_worker_without_killing_live_worker(self):
        old_here = orchestrator_server._HERE
        old_sleep = asyncio.sleep
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / '.council').mkdir()
                orchestrator_server._HERE = root
                orchestrator_server._council_runtime_persist('ope::model', {
                    'state': 'running', 'worker_pid': 99999999, 'started_at': time.time(),
                })
                calls = 0
                async def fast_sleep(seconds):
                    nonlocal calls
                    calls += 1
                    self.assertGreaterEqual(seconds, 600)
                    if calls > 1:
                        raise asyncio.CancelledError()
                asyncio.sleep = fast_sleep
                with self.assertRaises(asyncio.CancelledError):
                    await orchestrator_server._council_runtime_monitor(600)
                row = json.loads((root / '.council' / 'runtime_state.json').read_text())['ope::model']
                self.assertEqual(row['state'], 'error')
                self.assertEqual(row['error'], 'worker_process_exited')
                self.assertEqual(row['monitor_interval_sec'], 600)
        finally:
            asyncio.sleep = old_sleep
            orchestrator_server._HERE = old_here
