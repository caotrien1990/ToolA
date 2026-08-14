import asyncio
import concurrent.futures
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from aiohttp import StreamReader
from aiohttp.test_utils import make_mocked_request

import orchestrator_server as srv


class CouncilRoomTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = mock.patch.object(srv, "_HERE", self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_register_meta_token_and_expiry(self):
        result = srv._room_register("room.alpha", ttl_sec=1)
        meta = (self.root / ".council" / "rooms" / "room.alpha" / "room_meta.json").read_text()
        self.assertIn(result["room_token"], meta)
        self.assertEqual(srv._room_validate_token("room.alpha", result["room_token"])["room_id"], "room.alpha")
        with self.assertRaisesRegex(PermissionError, "ROOM_TOKEN_INVALID"):
            srv._room_validate_token("room.alpha", result["room_token"], now=result["expires_at"])

    def test_resolve_rejects_traversal(self):
        for value in ("../escape", "room/child", "room\\child", "", ".", ".."):
            with self.subTest(value=value), self.assertRaises(ValueError):
                srv._room_resolve_dir(value)

    def test_duplicate_claim_race_has_one_winner(self):
        registered = srv._room_register("race-room")
        def claim():
            return srv._room_check_duplicate("race-room", "ope", "task-1", "sha-a", room_token=registered["room_token"])
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(sum(result["dispatched"] for result in results), 1)
        duplicate = next(result for result in results if result["duplicate"])
        self.assertEqual(duplicate["record"]["task_id"], "task-1")
        terminal = srv._room_check_duplicate("race-room", "ope", "task-1", "sha-b", status="terminal-error", room_token=registered["room_token"])
        retry = srv._room_check_duplicate("race-room", "ope", "task-1", "sha-c", room_token=registered["room_token"])
        self.assertTrue(retry["dispatched"])
        self.assertEqual(retry["record"]["attempt_no"], terminal["record"]["attempt_no"] + 1)
        self.assertEqual(retry["record"]["text_sha"], "sha-c")

    def test_pid_alive_never_calls_os_kill(self):
        with mock.patch.object(srv.os, "kill", side_effect=AssertionError("os.kill forbidden")):
            with mock.patch("ctypes.windll.kernel32.OpenProcess", return_value=1), mock.patch("ctypes.windll.kernel32.CloseHandle"):
                self.assertTrue(srv._pid_alive(123))

    def test_gc_skips_active_and_worker_alive_archives_expired(self):
        root = self.root / "rooms"
        for name, status, alive, expiry in (("active", "active", False, 0), ("worker", "done", True, 0), ("expired", "done", False, 0)):
            room = root / name
            room.mkdir(parents=True)
            (room / "room_meta.json").write_text('{"expires_at": 0}')
            (room / "status.json").write_text('{"status": "%s", "worker_alive": %s}' % (status, str(alive).lower()))
        result = srv._room_garbage_collect(root, now=100)
        self.assertEqual(result["archived"], ["expired"])
        self.assertEqual(set(result["skipped"]), {"active", "worker"})
        self.assertTrue((root / "archive" / "expired-100").exists())

    def test_append_and_status_are_room_files(self):
        registered = srv._room_register("files")
        srv._room_append_event("files", {"kind": "started"}, registered["room_token"])
        status = srv._room_update_status("files", {"task_id": "t1", "artifact_path": "a.txt", "last_output_at": time.time() - 10000}, registered["room_token"])
        room = self.root / ".council" / "rooms" / "files"
        self.assertEqual(len((room / "events.jsonl").read_text().splitlines()), 1)
        self.assertEqual(status["status"]["task_id"], "t1")
        self.assertTrue(status["status"]["suspected_stalled"])

    def test_wrong_token_and_metadata_cannot_override_identity(self):
        result = srv._room_register(
            "secure-room",
            room_token="caller-token",
            metadata={"room_id": "other", "room_token": "forged", "expires_at": 9999999999},
        )
        meta = srv._room_read_json(self.root / ".council" / "rooms" / "secure-room" / "room_meta.json", {})
        self.assertEqual(meta["room_id"], "secure-room")
        self.assertEqual(meta["room_token"], result["room_token"])
        with self.assertRaisesRegex(PermissionError, "ROOM_TOKEN_INVALID"):
            srv._room_append_event("secure-room", {"kind": "bad"}, "wrong-token")

    def test_text_sha_is_secondary_diagnostic_only(self):
        registered = srv._room_register("sha-room")
        first = srv._room_check_duplicate("sha-room", "ope", "task-1", "sha-a", room_token=registered["room_token"])
        duplicate = srv._room_check_duplicate("sha-room", "ope", "task-1", "sha-b", room_token=registered["room_token"])
        self.assertTrue(first["dispatched"])
        self.assertFalse(duplicate["dispatched"])
        self.assertTrue(duplicate["duplicate"])
        self.assertTrue(duplicate["text_sha_mismatch"])
        self.assertEqual(duplicate["record"]["text_sha"], "sha-a")

    def test_status_defaults_and_worker_liveness(self):
        registered = srv._room_register("status-room")
        with mock.patch.object(srv, "_pid_alive", return_value=True) as alive:
            result = srv._room_update_status(
                "status-room", {"worker_pid": 321, "task_id": "t1"}, registered["room_token"]
            )["status"]
        alive.assert_called_once_with(321)
        self.assertTrue(result["worker_alive"])
        self.assertIsNone(result["artifact_path"])
        self.assertIsNone(result["last_output_at"])
        self.assertIsNone(result["silent_for_sec"])
        self.assertFalse(result["suspected_stalled"])
def _json_request(method, path, body=None, headers=None, match_info=None):
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    kwargs = {"headers": hdrs}
    if body is not None:
        loop = asyncio.new_event_loop()
        try:
            payload = StreamReader(protocol=mock.Mock(), limit=2 ** 16, loop=loop)
            payload.feed_data(json.dumps(body).encode("utf-8"))
            payload.feed_eof()
        finally:
            loop.close()
        kwargs["payload"] = payload
    if match_info is not None:
        kwargs["match_info"] = match_info
    return make_mocked_request(method, path, **kwargs)


class CouncilRoomHttpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = mock.patch.object(srv, "_HERE", self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    async def _register(self, room_id="http-room", ttl_sec=3600):
        request = _json_request("POST", "/api/council/room/register",
                                {"room_id": room_id, "ttl_sec": ttl_sec})
        response = await srv.handle_council_room_register(request)
        return response, json.loads(response.text)

    async def test_register_happy_returns_id_and_token(self):
        response, data = await self._register()
        self.assertEqual(response.status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["room_id"], "http-room")
        self.assertTrue(data["room_token"])
        self.assertGreater(data["expires_at"], int(time.time()))
        srv._room_validate_token("http-room", data["room_token"])

    async def test_register_then_status_returns_state(self):
        _, data = await self._register("state-room")
        token = data["room_token"]
        srv._room_update_status("state-room", {"state": "running", "task_id": "t-9"}, token)
        request = make_mocked_request(
            "GET", "/api/council/room/state-room/status",
            headers={"X-Room-Token": token},
            match_info={"rid": "state-room"})
        response = await srv.handle_council_room_status(request)
        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["state"], "running")
        self.assertEqual(payload["task_id"], "t-9")
        self.assertFalse(payload["worker_alive"])
        self.assertIsNone(payload["artifact_path"])

    async def test_status_wrong_token_403(self):
        await self._register("secure-http")
        request = make_mocked_request(
            "GET", "/api/council/room/secure-http/status?token=forged-token",
            match_info={"rid": "secure-http"})
        response = await srv.handle_council_room_status(request)
        payload = json.loads(response.text)
        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error_name"], "ROOM_TOKEN_INVALID")

    async def test_status_traversal_400(self):
        for rid in ("../escape", "..", "a/b", "room\\child"):
            with self.subTest(rid=rid):
                request = make_mocked_request(
                    "GET", "/api/council/room/x/status",
                    match_info={"rid": rid})
                response = await srv.handle_council_room_status(request)
                payload = json.loads(response.text)
                self.assertEqual(response.status, 400)
                self.assertEqual(payload["error_name"], "ROOM_PATH_INVALID")

    async def test_status_unknown_room_404(self):
        request = make_mocked_request(
            "GET", "/api/council/room/ghost/status?token=whatever",
            match_info={"rid": "ghost"})
        response = await srv.handle_council_room_status(request)
        payload = json.loads(response.text)
        self.assertEqual(response.status, 404)
        self.assertEqual(payload["error_name"], "ROOM_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
