"""bin/a.ps1 -- the thin HTTP client CAs dot-source instead of hand-rolling one.

Each hand-rolled copy re-hit the same traps. The ones pinned here are the ones that
corrupt data or hide an answer WITHOUT an error. A plain JSON write turned Vietnamese
text and box-drawing characters into '?' under ok:true. A client that trusted the
response headers could decode UTF-8 as ANSI. A 4xx from [A] surfaced as a PowerShell
exception, which hid the error_name the CA needs. The client runs under the real
Windows PowerShell 5.1 against a local stub, so what is pinned is the bytes on the wire.
"""
import base64
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLIENT = REPO / "bin" / "a.ps1"
POWERSHELL = shutil.which("powershell.exe")

# Non-ASCII, a backslash, box drawing and CRLF: each one breaks a different wrong client.
TEXT = "Tiếng Việt ─ C:\\a\r\nđiều kiện\n"
ANSWER = "Kết quả ─ C:\\b"


class _Stub(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, format, *args):
        pass

    def _answer(self, status, payload, content_type="application/json"):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        url = urllib.parse.urlsplit(self.path)
        _Stub.seen.append({"path": url.path, "query": urllib.parse.parse_qs(url.query),
                           "body": json.loads(raw.decode("utf-8"))})
        if url.path == "/api/call/bad_tool":
            self._answer(400, {"ok": False, "error_name": "SCHEMA_VIOLATION", "error": ANSWER})
        elif url.path == "/api/call/crash_tool":
            # aiohttp's own body for an exception that escapes a handler.
            body = b"500 Internal Server Error\n\nServer got itself in trouble"
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/api/call/empty_tool":
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            # No charset on purpose: the client must not fall back to a code page.
            self._answer(200, {"ok": True, "result": ANSWER})

    def do_GET(self):
        _Stub.seen.append({"path": urllib.parse.urlsplit(self.path).path})
        self._answer(200, {"tool": {"function": {"name": "write_fix"}}})


DRIVER = r"""
$ErrorActionPreference = 'Stop'
. '{client}'
$AProject = 'C:\proj'
$utf8 = New-Object Text.UTF8Encoding $false
$text = [IO.File]::ReadAllText('{text_file}', $utf8)
$fix = WF 'src/x.py' 3 4 -Text $text
$new = WN 'docs/n.md' -SourceFile '{text_file}'
$bad = Call 'bad_tool' @{{}} 'ok,error_name'
$search = Call 'search_code' @{{ query = $text }} 'ok'
$null = & {{ $AProject = 'C:\scoped'; RFR 'a.py' 1 1 }}
$schema = Schema 'write_fix'
$crash = Call 'crash_tool' @{{}} 'ok'
$empty = Call 'empty_tool' @{{}} 'ok'
$out = @{{ fix = $fix.result; new_ok = $new.ok; bad = $bad.error_name; bad_error = $bad.error
          schema = $schema.tool.function.name
          crash_ok = $crash.ok; crash_name = $crash.error_name; crash_status = $crash.http_status
          crash_error = $crash.error; empty_name = $empty.error_name
          empty_status = $empty.http_status; empty_error = $empty.error }}
[IO.File]::WriteAllText('{out_file}', (ConvertTo-Json -InputObject $out -Compress), $utf8)
"""


@unittest.skipUnless(POWERSHELL, "bin/a.ps1 targets Windows PowerShell")
class ThinClientOnTheWireTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _Stub.seen = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        (tmp / "text.txt").write_bytes(TEXT.encode("utf-8"))
        driver = tmp / "driver.ps1"
        driver.write_text(DRIVER.format(client=CLIENT, text_file=tmp / "text.txt",
                                        out_file=tmp / "out.json"), encoding="utf-8-sig")
        env = dict(os.environ,
                   ORCHESTRATOR_BASE_URL="http://127.0.0.1:%d/" % cls.server.server_port)
        cls.proc = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(driver)], capture_output=True, env=env, timeout=120)
        out = tmp / "out.json"
        cls.out = json.loads(out.read_bytes().decode("utf-8-sig")) if out.exists() else None

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr.decode("utf-8", "replace"))
        self.assertIsNotNone(self.out, "the driver never wrote its answers")

    def _call(self, tool):
        return next(c for c in _Stub.seen if c["path"] == "/api/call/" + tool)

    def test_write_fix_sends_the_exact_text_as_base64(self):
        body = self._call("write_fix")["body"]

        self.assertEqual(body["content_encoding"], "base64")
        self.assertEqual(base64.b64decode(body["new_content"]).decode("utf-8"), TEXT)
        self.assertEqual((body["file_path"], body["start_line"], body["end_line"],
                          body["project_path"]), ("src/x.py", 3, 4, "C:\\proj"))

    def test_write_new_file_reads_the_source_file_as_utf8(self):
        body = self._call("write_new_file")["body"]

        self.assertEqual(base64.b64decode(body["new_content"]).decode("utf-8"), TEXT)
        self.assertEqual(body["path"], "docs/n.md")
        self.assertNotIn("overwrite", body)

    def test_the_answer_is_decoded_as_utf8_without_a_charset(self):
        self.assertEqual(self.out["fix"], ANSWER)

    def test_a_plain_call_sends_its_text_as_utf8(self):
        """ConvertTo-Json in 5.1 leaves non-ASCII unescaped, so only the body's byte
        encoding decides whether a Vietnamese query reaches [A] intact."""
        self.assertEqual(self._call("search_code")["body"]["query"], TEXT)

    def test_the_callers_own_project_wins_over_the_global_default(self):
        """A script run with & sets $AProject in its own scope after dot-sourcing. A
        default read from $global:AProject ignored it and sent every call to the cwd."""
        self.assertEqual(self._call("read_file_range")["body"]["project_path"], "C:\\scoped")

    def test_an_http_error_returns_the_structured_answer(self):
        self.assertEqual((self.out["bad"], self.out["bad_error"]), ("SCHEMA_VIOLATION", ANSWER))
        self.assertEqual(self._call("bad_tool")["query"], {"fields": ["ok,error_name"]})

    def test_schema_is_a_get_on_the_tool_path(self):
        self.assertEqual(self.out["schema"], "write_fix")
        self.assertIn({"path": "/api/tools/write_fix/schema"}, _Stub.seen)

    def test_a_non_json_answer_is_named_not_null(self):
        """2026-09-29: aiohttp answered a dispatcher crash with a bare text/plain 500.
        ConvertFrom-Json turned it into $null, and a $null git_status answer read as an
        EMPTY staged_hash. Under $ErrorActionPreference='Stop' (this driver) the old
        client threw instead and the whole script died."""
        self.assertEqual((self.out["crash_ok"], self.out["crash_name"], self.out["crash_status"]),
                         (False, "NON_JSON_RESPONSE", 500))
        self.assertIn("Server got itself in trouble", self.out["crash_error"])
        self.assertEqual((self.out["empty_name"], self.out["empty_status"]),
                         ("NON_JSON_RESPONSE", 502))
        self.assertIn("<empty body>", self.out["empty_error"])


class ThinClientFileTests(unittest.TestCase):

    def test_the_client_is_pure_ascii(self):
        """PowerShell 5.1 reads a BOM-less script as the ANSI code page."""
        data = CLIENT.read_bytes()
        self.assertEqual([i for i, b in enumerate(data) if b > 127][:5], [])


if __name__ == "__main__":
    unittest.main()
