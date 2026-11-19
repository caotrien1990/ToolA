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

    def test_valid_marker_bearing_vietnamese_passes_inline(self):
        # P16 §3.4 / §5.2-3: innocent Vietnamese carrying a probe marker
        # ("ĐÃ", "Â", "á»") must pass both council gates.
        for text in ("ĐÃ", "Â", "Brief ĐÃ gửi", "nói quá»"):
            with self.subTest(text=text):
                with mock.patch.object(srv, "_council_append_event"):
                    result = json.loads(asyncio.run(srv.council_message(sender="codo", to="user", text=text)))
                self.assertTrue(result["ok"], msg=result)

    def test_valid_marker_bearing_vietnamese_passes_dispatch(self):
        # §5.2: the dispatch gate (text leg) must pass too, not just the resolver.
        for text in ("ĐÃ", "Â"):
            with self.subTest(text=text):
                with mock.patch.object(srv, "_council_append_event"):
                    result = asyncio.run(srv._council_dispatch_message("codo", "user", text, queue=False))
                self.assertTrue(result["ok"], msg=result)

    def test_valid_marker_bearing_vietnamese_passes_body_file(self):
        # §5.2 body_file leg; the clean marker-bearing first line is the proof
        # file's own shape (§5.1).
        for name, text in (("da.md", "ĐÃ FILE\nNội dung brief nguyên vẹn."), ("a_circ.txt", "Â\nDòng thứ hai.")):
            with self.subTest(name=name):
                path = self.root / ".council" / "briefs" / name
                path.write_text(text, encoding="utf-8")
                resolved, error = srv._council_message_source(None, ".council/briefs/" + name)
                self.assertIsNone(error, msg=error)
                self.assertEqual(resolved, text)

    def test_plain_ascii_still_passes(self):
        # §5.8: ASCII inline + body_file unaffected.
        with mock.patch.object(srv, "_council_append_event"):
            result = json.loads(asyncio.run(srv.council_message(sender="codo", to="user", text="plain ascii brief")))
        self.assertTrue(result["ok"], msg=result)
        path = self.root / ".council" / "briefs" / "ascii.md"
        path.write_text("plain ascii brief", encoding="utf-8")
        resolved, error = srv._council_message_source(None, ".council/briefs/ascii.md")
        self.assertIsNone(error, msg=error)
        self.assertEqual(resolved, "plain ascii brief")

    def test_body_file_genuine_mojibake_still_rejected(self):
        # §5.4: canonical corrupted + lone corrupted à-word via body_file. The
        # lone à-word pins the body_file read strip as ASCII-only: a Unicode
        # .strip() severs the trailing U+00A0 and "vÃ" alone no longer confirms.
        canonical = "tiến hành kiểm tra tiếng Việt"
        cases = (("corrupted.md", canonical.encode("utf-8").decode("latin-1")), ("lone_va.md", "và".encode("utf-8").decode("latin-1")))
        for name, text in cases:
            with self.subTest(name=name):
                path = self.root / ".council" / "briefs" / name
                path.write_text(text, encoding="utf-8")
                _, error = srv._council_message_source(None, ".council/briefs/" + name)
                self.assertEqual(error["error_name"], "SUSPECTED_MOJIBAKE")

    def test_mixed_text_genuine_mojibake_still_rejected(self):
        # §5.5: corrupted canonical + clean tail (Chinese / "ĐÃ") — whole-text
        # confirmation fails these; per-token ANY confirmation rejects them.
        corrupted = "tiến hành kiểm tra tiếng Việt".encode("utf-8").decode("latin-1")
        for tail in (" — 你好", " ĐÃ"):
            with self.subTest(tail=tail):
                result = json.loads(asyncio.run(srv.council_message(to="user", text=corrupted + tail)))
                self.assertEqual(result["error_name"], "SUSPECTED_MOJIBAKE")

    def test_corrupted_a_words_inline_still_rejected(self):
        # §5.10: lone à-words, ASCII lead/tail, and join-then-corrupt NBSP text.
        # Bare str.split() would sever U+00A0 mid-sequence and miss these.
        def corr(s):
            return s.encode("utf-8").decode("latin-1")
        cases = (corr("và"), corr("hành"), corr("và") + " plain ascii", corr("hello và"), ("tiến\xa0hành").encode("utf-8").decode("latin-1"))
        for text in cases:
            with self.subTest(text=text):
                result = json.loads(asyncio.run(srv.council_message(to="user", text=text)))
                self.assertEqual(result["error_name"], "SUSPECTED_MOJIBAKE")

    def test_clean_lead_genuine_mojibake_still_rejected(self):
        # §5.11: corruption behind clean lead words/phrases — first-segment-only
        # confirmation would admit these.
        corrupted = "tiến hành kiểm tra tiếng Việt".encode("utf-8").decode("latin-1")
        for lead in ("ĐÃ ", "Hello ", "ĐÃ FILE BRIEF ", "tiếng Việt — 你好 "):
            with self.subTest(lead=lead):
                result = json.loads(asyncio.run(srv.council_message(to="user", text=lead + corrupted)))
                self.assertEqual(result["error_name"], "SUSPECTED_MOJIBAKE")

    def test_multiline_genuine_mojibake_still_rejected(self):
        # §5.12: corruption on first/middle/last lines and paragraphs, inline +
        # body_file (variant (a) is the proof file's own shape).
        corrupted = "tiến hành kiểm tra tiếng Việt".encode("utf-8").decode("latin-1")
        cases = (
            "ĐÃ FILE\n" + corrupted,
            "ĐÃ FILE\nSecond clean line\nThird clean line\n" + corrupted,
            "ĐÃ FILE\n\n" + corrupted,
            corrupted + "\nClean second line\nThird clean line",
            corrupted + "\n\nClean para",
            "ĐÃ FILE\nSecond clean line\nThird clean line\nFourth clean line\n" + corrupted,
            corrupted + "\nClean second line\nThird clean line\nFourth clean line\nFifth clean line",
            "Clean paragraph one\n\nClean paragraph two\n\n" + corrupted,
            corrupted + "\n\nClean paragraph two\n\nClean paragraph three",
            corrupted + "\nĐÃ FILE",
            corrupted + "\n\nĐÃ FILE",
            "ĐÃ FILE\nSecond clean line\n" + corrupted + "\nFourth clean line",
            "Hello first paragraph\n\n" + corrupted + "\n\nClean third paragraph",
        )
        for text in cases:
            with self.subTest(text=text[:40]):
                result = json.loads(asyncio.run(srv.council_message(to="user", text=text)))
                self.assertEqual(result["error_name"], "SUSPECTED_MOJIBAKE")
        path = self.root / ".council" / "briefs" / "proof_shape.md"
        path.write_text("ĐÃ FILE\n" + corrupted, encoding="utf-8")
        _, error = srv._council_message_source(None, ".council/briefs/proof_shape.md")
        self.assertEqual(error["error_name"], "SUSPECTED_MOJIBAKE")


if __name__ == "__main__":
    unittest.main()
