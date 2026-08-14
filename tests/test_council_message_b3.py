"""B3 regression tests: preserve UTF-8 and reject unsafe/corrupt council inputs."""
import asyncio
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import code_tools as ct
import orchestrator_server as srv


class CouncilMessageB3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".council" / "briefs").mkdir(parents=True)
        self.here = mock.patch.object(srv, "_HERE", self.root)
        self.here.start()

    def tearDown(self):
        self.here.stop()
        self.tmp.cleanup()

    def test_inline_unicode_reaches_broker_with_code_point_equality(self):
        text = "tiến hành kiểm tra tiếng Việt — 你好"
        broker = self.root / ".council" / "broker_filtered.jsonl"

        def append(event):
            with broker.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")

        with mock.patch.object(srv, "_council_append_event", side_effect=append):
            result = json.loads(asyncio.run(srv.council_message(sender="codo", to="user", text=text)))
        event = json.loads(broker.read_text(encoding="utf-8").splitlines()[-1])
        self.assertTrue(result["ok"])
        self.assertEqual(event["text"], text)
        self.assertEqual([ord(c) for c in event["text"]], [ord(c) for c in text])

    def test_inline_unicode_reaches_worker_prompt_unchanged(self):
        text = "tiến hành giữ nguyên dấu tiếng Việt — 你好"

        async def enqueue(target, sender, prompt, *args, **kwargs):
            self.assertEqual(prompt, text)
            self.assertEqual([ord(c) for c in prompt], [ord(c) for c in text])
            return {"ok": True, "reply_text": "ok"}

        with (
            mock.patch.object(srv, "_council_worker_model_configured", return_value=True),
            mock.patch.object(srv, "_council_enqueue_or_run", side_effect=enqueue),
            mock.patch.object(srv, "_artifact_already_complete", return_value=None),
        ):
            result = asyncio.run(srv._council_dispatch_message("codo", "ope", text, queue=False))
        self.assertTrue(result["ok"])

    def test_mojibake_is_detected_not_repaired(self):
        canonical = "tiến hành kiểm tra tiếng Việt"
        corrupted = canonical.encode("utf-8").decode("latin-1")
        self.assertTrue(ct._r54_detect_mojibake(corrupted)[0])
        result = json.loads(asyncio.run(srv.council_message(to="user", text=corrupted)))
        self.assertEqual(result["error_name"], "SUSPECTED_MOJIBAKE")
        self.assertNotIn(canonical, json.dumps(result, ensure_ascii=False))

    def test_body_file_utf8_round_trip(self):
        text = "Nội dung brief nguyên vẹn — 日本語"
        path = self.root / ".council" / "briefs" / "task.md"
        path.write_text(text, encoding="utf-8")
        resolved, error = srv._council_message_source(None, ".council/briefs/task.md")
        self.assertIsNone(error)
        self.assertEqual(resolved, text)

    def test_body_file_rejections(self):
        outside = self.root / "outside.md"
        outside.write_text("secret", encoding="utf-8")
        cases = [
            (None, "../outside.md", "BODY_FILE_OUTSIDE_ALLOWLIST"),
            (None, str(outside), "BODY_FILE_OUTSIDE_ALLOWLIST"),
        ]
        for text, body_file, expected in cases:
            with self.subTest(body_file=body_file):
                _, error = srv._council_message_source(text, body_file)
                self.assertEqual(error["error_name"], expected)

        oversized = self.root / ".council" / "briefs" / "large.txt"
        oversized.write_bytes(b"x" * (srv._COUNCIL_BODY_FILE_MAX_BYTES + 1))
        self.assertEqual(srv._council_message_source(None, ".council/briefs/large.txt")[1]["error_name"], "BODY_FILE_TOO_LARGE")

        invalid = self.root / ".council" / "briefs" / "invalid.txt"
        invalid.write_bytes(b"\xff\xfe")
        self.assertEqual(srv._council_message_source(None, ".council/briefs/invalid.txt")[1]["error_name"], "BODY_FILE_NOT_UTF8")

        both = srv._council_message_source("inline", ".council/briefs/invalid.txt")[1]
        self.assertEqual(both["error_name"], "MESSAGE_SOURCE_CONFLICT")

    def test_symlink_body_file_is_rejected(self):
        target = self.root / "outside.md"
        target.write_text("secret", encoding="utf-8")
        link = self.root / ".council" / "briefs" / "link.md"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        _, error = srv._council_message_source(None, ".council/briefs/link.md")
        self.assertEqual(error["error_name"], "BODY_FILE_OUTSIDE_ALLOWLIST")

    def test_sync_async_and_mcp_signatures_have_body_file(self):
        self.assertIn("body_file", inspect.signature(srv.council_message).parameters)
        self.assertIn("body_file", inspect.signature(srv.council_message_async).parameters)
        schemas = {entry["function"]["name"]: entry for entry in ct.TOOL_SCHEMAS}
        for name in ("council_message", "council_message_async"):
            params = schemas[name]["function"]["parameters"]
            props = params["properties"]
            self.assertIn("text", props)
            self.assertIn("body_file", props)
            self.assertNotIn("text", params.get("required", []))
        source = Path("orchestrator/server/mcp_stdio.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("body_file: str = \"\""), 2)
        self.assertIn('payload["body_file"] = body_file', source)


if __name__ == "__main__":
    unittest.main()
