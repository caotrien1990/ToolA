# pyright: reportAttributeAccessIssue=false
"""V15 F2/F3 (D1/D2) — council codex worker reply fallback contract tests.

Why: a codex council worker killed by a gateway 400 before writing its
--output-last-message file previously fell back to raw stdout with no codex
adapter, surfacing transport noise as the "reply". D1 forbids promoting
transcript text to a final answer; D2 defines the machine-testable noise
filter; F2 emits a structured COUNCIL_WORKER_INCOMPLETE marker instead.

Purpose fixtures:
(a) rc=0 + valid file                    → happy path intact
(b) rc=1 + empty file + noisy transcript → structured incomplete
(c) rc=1 + intermediate transcript text  → still incomplete (no promotion)
(d) rc=0 + empty file + natural text     → incomplete per D1
(e) multiline JSON block never leaks into tail_excerpt
(f) "executive summary …" content line survives the word-boundary filter
(g) results stay under the broker reply cap (1500)
"""
import unittest

import orchestrator_server as srv

_PROMPT = "PROMPT_SENTINEL_1234567890_ABCDEF"
_OUTPUT_PATH = "D:/tmp/council_codo_fake_output.txt"
_REPLY_CAP = 1500  # mirrors _council_build_dispatch_reply `_reply_cap`


def _simulate_dispatch_reply(reply_source: str, file_reply: str, returncode,
                             output_path=_OUTPUT_PATH, agent: str = "codo") -> str:
    """Mirror orchestrator_server.py council dispatch reply flow (F2 call site)
    1:1 so fixtures exercise the exact production decision chain."""
    adapter = "claude" if agent == "clau" else ("hermes" if agent == "hermes" else agent)
    reply, _reasoning, _noise = srv._council_parse_reply(reply_source, _PROMPT, adapter)
    file_clean = ""
    if file_reply:
        file_clean, _fr, _fn = srv._council_parse_reply(file_reply, _PROMPT, agent)
        reply = file_clean or reply
    reply = srv._council_apply_incomplete_fallback(
        reply, file_clean, file_reply, returncode, output_path, reply_source)
    return reply or "[no reply]"


class TestCodexExecNoiseFilter(unittest.TestCase):
    """F3 / D2 — _council_extract_codex_exec noise contract."""

    def test_f_executive_summary_not_swallowed(self):
        text = (
            "exec\n"
            "succeeded in 1234ms:\n"
            "codex\n"
            "executive summary: all 8 tests pass\n"
            "tokens used\n"
            "12,345\n"
        )
        out = srv._council_extract_codex_exec(text)
        self.assertIn("executive summary: all 8 tests pass", out)
        self.assertNotIn("tokens used", out)
        self.assertNotIn("12,345", out)
        self.assertNotIn("succeeded in", out)
        # bare section markers dropped
        self.assertNotRegex(out, r"(?m)^\s*(exec|codex)\s*$")

    def test_r2_f3_1_prefixed_sentences_kept(self):
        # F3-1: content lines merely STARTING with exec/codex are real content,
        # not transport markers — the old `^\s*(?:exec|codex)\b` ate them.
        out = srv._council_extract_codex_exec("codex apps are useful\n")
        self.assertIn("codex apps are useful", out)
        out2 = srv._council_extract_codex_exec("exec plan is valid content\n")
        self.assertIn("exec plan is valid content", out2)

    def test_r2_f3_1_bare_markers_dropped_case_insensitive(self):
        # F3-1: the true transport marker is a BARE line holding exactly
        # "exec"/"codex" (any case) — still filtered.
        for marker in ("exec", "codex", "Codex", "EXEC"):
            with self.subTest(marker=marker):
                self.assertEqual(srv._council_extract_codex_exec(marker + "\n"), "")
        # bare markers inside a transcript are dropped; content survives
        out = srv._council_extract_codex_exec("exec\nCODEX\nreal answer line\n")
        self.assertEqual(out, "real answer line")

    def test_e_multiline_json_block_filtered(self):
        text = (
            "ERROR: {\n"
            '  "error": {\n'
            '    "message": "bad request",\n'
            '    "type": "invalid_request_error"\n'
            "  }\n"
            "}\n"
            "codex\n"
            "final answer line\n"
        )
        out = srv._council_extract_codex_exec(text)
        self.assertIn("final answer line", out)
        self.assertNotIn('"error"', out)
        self.assertNotIn("bad request", out)
        self.assertNotIn("ERROR", out)

    def test_single_line_json_object_filtered(self):
        text = '{"type":"step_start","sessionID":"ses_1"}\ncodex\nreal reply\n'
        out = srv._council_extract_codex_exec(text)
        self.assertEqual(out, "real reply")

    def test_all_noise_returns_empty_string(self):
        text = "exec\nsucceeded in 5ms:\ntokens used\n42\n"
        self.assertEqual(srv._council_extract_codex_exec(text), "")

    def test_meaningful_requires_non_noise_line(self):
        self.assertEqual(srv._council_extract_codex_exec("codex\n\nexec\n"), "")
        self.assertEqual(
            srv._council_extract_codex_exec("codex\nreal content here\n"),
            "real content here",
        )

    def test_empty_input_returns_empty(self):
        self.assertEqual(srv._council_extract_codex_exec(""), "")
        self.assertEqual(srv._council_extract_codex_exec(None), "")

    def test_codex_jsonl_mode_delegates_to_log_extractor(self):
        # `codex exec --json` wire format must keep the golden JSONL behavior
        jsonl = (
            '{"type":"thread.started","thread_id":"abc"}\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"JSONL_OK"}}\n'
        )
        reply, _r, _n = srv._council_parse_reply(jsonl, _PROMPT, "codo")
        self.assertEqual(reply, "JSONL_OK")
        # plain stream must NOT take the JSONL path
        self.assertFalse(srv._council_looks_like_codex_jsonl("exec\ncodex\nplain\n"))
        self.assertTrue(srv._council_looks_like_codex_jsonl(jsonl))


class TestCouncilIncompleteFallback(unittest.TestCase):
    """F2 / D1 — structured incomplete marker contract."""

    def test_a_happy_path_rc0_valid_file_intact(self):
        final = "final answer from file"
        out = _simulate_dispatch_reply(
            reply_source="exec\nsucceeded in 8ms:\ntokens used\n9,999\n",
            file_reply=final + "\n",
            returncode=0,
        )
        self.assertEqual(out, final)
        self.assertNotIn("COUNCIL_WORKER_INCOMPLETE", out)

    def test_happy_path_rc1_but_file_valid_kept(self):
        # file channel delivered a trustworthy final → keep it even when rc!=0
        final = "recovered final despite nonzero exit"
        out = _simulate_dispatch_reply(
            reply_source="exec\nsucceeded in 8ms:\n",
            file_reply=final,
            returncode=1,
        )
        self.assertEqual(out, final)

    def test_b_rc1_empty_file_noisy_transcript_incomplete(self):
        raw = (
            "exec\nsucceeded in 9ms:\nERROR: {\n"
            '  "error": {"message": "gateway 400"}\n'
            "}\ntokens used\n1,234\n"
        )
        out = _simulate_dispatch_reply(reply_source=raw, file_reply="", returncode=1)
        self.assertTrue(out.startswith("COUNCIL_WORKER_INCOMPLETE: exit=1"))
        self.assertIn("no final message captured", out)
        self.assertIn("hint: retry with smaller context (verbosity terse, fewer tools)", out)
        self.assertIn("tail_excerpt:", out)
        # noise must not leak into the excerpt
        self.assertNotIn("tokens used", out)
        self.assertNotIn("1,234", out)
        self.assertNotIn("gateway 400", out)
        self.assertNotIn('"error"', out)

    def test_c_rc1_intermediate_text_not_promoted(self):
        raw = "codex\nI will now run the tests\nexec\nsucceeded in 3ms:\n"
        out = _simulate_dispatch_reply(reply_source=raw, file_reply="", returncode=1)
        self.assertTrue(out.startswith("COUNCIL_WORKER_INCOMPLETE: exit=1"))
        # intermediate text is diagnostic-only: allowed inside tail_excerpt,
        # never as the reply body (first line).
        self.assertNotEqual(out.splitlines()[0], "I will now run the tests")

    def test_d_rc0_empty_file_natural_text_still_incomplete(self):
        # D1: rc=0 + missing file_reply → NEVER promote transcript text
        raw = "codex\nHere is the full analysis you requested.\n"
        out = _simulate_dispatch_reply(reply_source=raw, file_reply="", returncode=0)
        self.assertTrue(out.startswith("COUNCIL_WORKER_INCOMPLETE: exit=0"))
        self.assertIn("no final message captured", out)

    def test_non_file_channel_agent_untouched(self):
        # output_path empty (clau/hermes/ope/…): legacy behaviour preserved
        reply = srv._council_apply_incomplete_fallback(
            "stdout derived reply", "", "", 1, "", "raw text")
        self.assertEqual(reply, "stdout derived reply")

    def test_g_results_under_broker_reply_cap(self):
        # happy path short reply stays intact
        short = "ok: 8/8 tests pass"
        out = _simulate_dispatch_reply("noise\n", short, 0)
        self.assertEqual(out, short)
        self.assertLessEqual(len(out), _REPLY_CAP)
        # structured marker + worst-case 200-char excerpt stays under cap
        raw_tail = "codex\n" + ("x" * 500) + "\n"
        out2 = _simulate_dispatch_reply(raw_tail, "", 1)
        self.assertTrue(out2.startswith("COUNCIL_WORKER_INCOMPLETE"))
        self.assertLessEqual(len(out2), _REPLY_CAP)
        excerpt_line = [ln for ln in out2.splitlines() if ln.startswith("tail_excerpt:")][0]
        self.assertLessEqual(len(excerpt_line) - len("tail_excerpt: "), 200)

    def test_r2_error_dump_truncated_tail_bounded(self):
        # DOC fixture (fix phụ — definite decision: bounded diagnostic kept, no
        # logic change). An unparseable "ERROR: {" dump consumes only its own
        # line; following lines (even a gateway body leak) MAY surface inside
        # tail_excerpt — accepted as bounded diagnostic — but the excerpt is
        # ALWAYS capped at <=200 chars. The marker (not the leak) stays the reply.
        raw = "ERROR: {\n  gateway body leaked\nfinal answer"
        out = _simulate_dispatch_reply(reply_source=raw, file_reply="", returncode=1)
        self.assertTrue(out.startswith("COUNCIL_WORKER_INCOMPLETE: exit=1"))
        # the leak is diagnostic-only, never promoted to the reply line
        self.assertNotEqual(out.splitlines()[0], "final answer")
        # excerpt = everything after the prefix (may span lines); hard cap 200
        idx = out.index("tail_excerpt: ")
        excerpt = out[idx + len("tail_excerpt: "):]
        self.assertLessEqual(len(excerpt), 200)


if __name__ == "__main__":
    unittest.main()
