import inspect
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import code_tools as ct
import orchestrator_server as srv


class S2ContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.project = Path(self.tempdir.name) / "repo"
        self.project.mkdir()
        (self.project / "requirements.txt").write_text("", encoding="utf-8")
        self.old_db = ct._SESSIONS_DB
        ct._SESSIONS_DB = Path(self.tempdir.name) / "sessions.db"
        ct._sync_session_db_alias()
        ct._init_session_db()
        ct._ACTIVE_TASK_EPOCHS.clear()
        srv._TASK_EPOCH_STATE.clear()

    async def asyncTearDown(self):
        ct._SESSIONS_DB = self.old_db
        ct._sync_session_db_alias()
        ct._ACTIVE_TASK_EPOCHS.clear()
        srv._TASK_EPOCH_STATE.clear()
        self.tempdir.cleanup()

    async def begin(self, session_id, prompt, max_chars=2000):
        claim = f"claim-{session_id}-0123456789abcdef0123456789abcdef"
        bootstrap = json.loads(await ct.bootstrap_session(
            session_id=session_id, agent_id="cozo", owner_claim_id=claim,
        ))
        return json.loads(await ct.begin_task(
            session_id, prompt, "cozo", max_chars,
            bootstrap["owner_capability"], owner_claim_id=claim,
        ))

    async def test_open_session_vietnamese_round_trip(self):
        title = "Phiên làm việc tiếng Việt nguyên bản"
        with mock.patch.object(ct, "_audit_tool_matrix_sync", return_value={}):
            result = json.loads(await ct.open_session(
                title, str(self.project), agent_id="cozo",
            ))
        with sqlite3.connect(ct._SESSIONS_DB) as database:
            stored = database.execute(
                "select session_title from sessions where session_id=?",
                (result["session_id"],),
            ).fetchone()[0]
        self.assertEqual((result["session_title"], stored), (title, title))

    async def test_prepare_ca_session_vietnamese_round_trip(self):
        idea = "Xây dựng luồng kiểm thử tiếng Việt chính xác"
        with mock.patch.object(ct, "_audit_tool_matrix_sync", return_value={}):
            result = json.loads(await ct.prepare_ca_session(
                str(self.project), "T2", idea, "cozo",
            ))
        state = json.loads(Path(result["workflow_state_path"]).read_text(encoding="utf-8"))
        with sqlite3.connect(ct._SESSIONS_DB) as database:
            title = database.execute(
                "select session_title from sessions where session_id=?",
                (result["session_id"],),
            ).fetchone()[0]
        self.assertIn(idea, result["prompt"])
        self.assertEqual((state["idea"], title), (idea, idea[:60]))

    async def test_full_canonical_hash_and_http_forwarding(self):
        canonical = "tiến hành sửa lỗi tiếng Việt " + "nội dung đầy đủ " * 80
        corrupted = canonical.encode().decode("latin-1")
        result = await self.begin("hash", corrupted, 256)
        self.assertEqual(result["prompt_hash"], ct._task_prompt_hash(canonical))
        self.assertNotEqual(result["prompt_hash"], ct._task_prompt_hash(corrupted))
        self.assertNotEqual(result["prompt_hash"], ct._task_prompt_hash(result["active_task"]))
        srv._TASK_EPOCH_STATE["hash"] = dict(ct._ACTIVE_TASK_EPOCHS["hash"])
        headers = {
            "X-Session-Id": "hash",
            "X-Task-Epoch": str(result["task_epoch"]),
            "X-Prompt-Hash": result["prompt_hash"],
        }
        forwarded = await srv.dispatch_tool_call(
            "workflow_list", {}, "api", request_meta={"headers": headers},
        )
        self.assertNotEqual(forwarded.status, 409)
        stale_headers = dict(headers, **{
            "X-Prompt-Hash": ct._task_prompt_hash(corrupted),
        })
        stale = await srv.dispatch_tool_call(
            "workflow_list", {}, "api", request_meta={"headers": stale_headers},
        )
        stale_payload = json.loads(stale.text)
        self.assertEqual(stale.status, 409)
        self.assertEqual(stale_payload["error_name"], "STALE_TASK_EPOCH")
        self.assertEqual(stale_payload["prompt_hash"], result["prompt_hash"])

    def test_canonicalizer_idempotence_and_false_positive_guards(self):
        canonical = "tiến hành sửa lỗi tiếng Việt"
        corrupted = canonical.encode().decode("latin-1")
        once, _ = ct._repair_latin1_utf8_prompt(corrupted)
        twice, repaired_twice = ct._repair_latin1_utf8_prompt(once)
        self.assertEqual((once, twice, repaired_twice), (canonical, canonical, False))
        fixtures = (
            "plain ASCII", "Café déjà vu © 2026",
            "Literal marker names: Ã and Â are labels",
        )
        for fixture in fixtures:
            self.assertEqual(ct._repair_latin1_utf8_prompt(fixture), (fixture, False))

    def test_repair_is_whitelisted_to_begin_task_prompt(self):
        hits = [
            line.strip() for line in inspect.getsource(ct).splitlines()
            if "_repair_latin1_utf8_prompt(" in line
        ]
        self.assertEqual(len(hits), 2)
        self.assertIn("prompt or", hits[1])

    async def test_bootstrap_documents_opaque_server_hash(self):
        result = json.loads(await ct.bootstrap_session(
            session_id="doc", agent_id="cozo",
            owner_claim_id="doc-0123456789abcdef0123456789abcdef",
        ))
        contract = result["task_contract"]["prompt_hash"]
        for text in ("canonical repaired UTF-8", "server-issued", "opaque", "X-Prompt-Hash"):
            self.assertIn(text, contract)
