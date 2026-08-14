"""Focused contract tests for CODOC-A1 — secure TLS defaults in T1 fetch/probe tools.

Covered functions:
  - check_server_path  (code_tools)
  - webfetch            (code_tools)
  - fetch_sheet_csv_summary (code_tools, pass-through)

Pass criteria from CODOC_ACCEPTED_BACKLOG.md:
  1. Monkeypatch urllib.request.urlopen and assert omitted verify_tls
     receives a context with CERT_REQUIRED and check_hostname=True for
     both functions.
  2. A self-signed/invalid-chain simulation returns WEBFETCH_URL_ERROR
     (or a dedicated TLS error) and never ok:true by default.
  3. Explicit verify_tls=false is schema-visible, produces
     tls_verified:false, and does not silently look like a verified
     response.
  4. fetch_sheet_csv_summary inherits secure verification by default.
"""

import asyncio
import json
import ssl
import unittest
from unittest.mock import MagicMock, patch


# ── helpers ──────────────────────────────────────────────────────────────


def _json_load(result: str) -> dict:
    return json.loads(result)


def _ssl_context_has_secure_defaults(ctx) -> bool:
    """Return True if ctx enforces CERT_REQUIRED + check_hostname=True."""
    return (
        ctx.verify_mode == ssl.CERT_REQUIRED
        and ctx.check_hostname is True
    )


def _ssl_context_is_insecure(ctx) -> bool:
    """Return True if ctx uses CERT_NONE + check_hostname=False."""
    return (
        ctx.verify_mode == ssl.CERT_NONE
        and ctx.check_hostname is False
    )


# ── Test 1 ───────────────────────────────────────────────────────────────
# Monkeypatch urlopen and assert the SSL context passed to it has
# CERT_REQUIRED + check_hostname=True when verify_tls is omitted (default).


class TestWebfetchTlsDefaults(unittest.TestCase):

    def test_webfetch_default_uses_secure_context(self):
        """Omitted verify_tls → CERT_REQUIRED + check_hostname=True."""
        from code_tools import webfetch

        captured_ctx = {}

        def _fake_urlopen(req, timeout=None, context=None):
            captured_ctx["context"] = context
            # Return a minimal mock response.
            resp = MagicMock()
            resp.status = 200
            resp.getcode.return_value = 200
            resp.headers = MagicMock()
            resp.headers.get.return_value = "text/csv"
            resp.read.return_value = b"col1,col2\n1,2\n"
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            return resp

        with patch("urllib.request.urlopen", new=_fake_urlopen):
            result = _json_load(asyncio.run(webfetch("https://example.com/data.csv")))

        self.assertTrue(result["ok"], f"Expected ok=true, got {result}")
        self.assertIn("context", captured_ctx, "urlopen was not called with context=")
        self.assertTrue(
            _ssl_context_has_secure_defaults(captured_ctx["context"]),
            f"Expected CERT_REQUIRED + check_hostname=True, got "
            f"verify_mode={captured_ctx['context'].verify_mode} "
            f"check_hostname={captured_ctx['context'].check_hostname}",
        )

    def test_webfetch_explicit_insecure_produces_warning(self):
        """verify_tls=false → CERT_NONE + tls_verified:false in result."""
        from code_tools import webfetch

        captured_ctx = {}

        def _fake_urlopen(req, timeout=None, context=None):
            captured_ctx["context"] = context
            resp = MagicMock()
            resp.status = 200
            resp.getcode.return_value = 200
            resp.headers = MagicMock()
            resp.headers.get.return_value = "text/csv"
            resp.read.return_value = b"col1,col2\n1,2\n"
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            return resp

        with patch("urllib.request.urlopen", new=_fake_urlopen):
            result = _json_load(
                asyncio.run(webfetch("https://example.com/data.csv", verify_tls=False))
            )

        self.assertTrue(result["ok"], f"Expected ok=true, got {result}")
        self.assertFalse(result.get("tls_verified"), "Expected tls_verified=false")
        self.assertIn("security_warning", result, "Expected security_warning in response")
        self.assertIn("TLS verification DISABLED", result.get("security_warning", ""))
        self.assertTrue(
            _ssl_context_is_insecure(captured_ctx["context"]),
            f"Expected CERT_NONE + check_hostname=False, got "
            f"verify_mode={captured_ctx['context'].verify_mode} "
            f"check_hostname={captured_ctx['context'].check_hostname}",
        )

    # ── Test 2 ───────────────────────────────────────────────────────────
    # SSL error simulation → never ok:true by default.

    def test_webfetch_ssl_error_never_ok_by_default(self):
        """SSL certificate error → not ok, error_name WEBFETCH_URL_ERROR."""
        from code_tools import webfetch

        import urllib.error

        def _fake_urlopen_ssl_error(req, timeout=None, context=None):
            raise urllib.error.URLError("certificate verify failed: self signed certificate")

        with patch("urllib.request.urlopen", new=_fake_urlopen_ssl_error):
            result = _json_load(
                asyncio.run(webfetch("https://self-signed.example.com/data.csv"))
            )

        self.assertFalse(result["ok"], f"Expected ok=false, got {result}")
        self.assertEqual(
            result.get("error_name"),
            "WEBFETCH_URL_ERROR",
            f"Expected WEBFETCH_URL_ERROR, got {result.get('error_name')}",
        )
        self.assertNotEqual(
            result.get("tls_verified"),
            True,
            "tls_verified should not be True on error",
        )

    def test_webfetch_http_url_no_tls_verified_claim(self):
        """http:// URL → no tls_verified field (TLS not used)."""
        from code_tools import webfetch

        def _fake_urlopen(req, timeout=None, context=None):
            resp = MagicMock()
            resp.status = 200
            resp.getcode.return_value = 200
            resp.headers = MagicMock()
            resp.headers.get.return_value = "text/plain"
            resp.read.return_value = b"hello"
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            return resp

        with patch("urllib.request.urlopen", new=_fake_urlopen):
            result = _json_load(
                asyncio.run(webfetch("http://example.com/data.txt"))
            )

        self.assertTrue(result["ok"], f"Expected ok=true, got {result}")
        self.assertNotIn("tls_verified", result, "http:// URL should not set tls_verified")
        self.assertNotIn("security_warning", result, "http:// URL should not set security_warning")

    # ── Test 3 ───────────────────────────────────────────────────────────
    # check_server_path defaults to secure SSL context.

    def test_check_server_path_default_uses_secure_context(self):
        """Omitted verify_tls → CERT_REQUIRED + check_hostname=True."""
        from code_tools import check_server_path

        captured_ctx = {}

        def _fake_urlopen(req, timeout=None, context=None):
            captured_ctx["context"] = context
            resp = MagicMock()
            resp.status = 200
            resp.headers = MagicMock()
            resp.headers.items.return_value = [("Content-Type", "text/html")]
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            return resp

        with patch("urllib.request.urlopen", new=_fake_urlopen):
            result = _json_load(
                asyncio.run(check_server_path("https://example.com", mode="https"))
            )

        self.assertTrue(result["ok"], f"Expected ok=true, got {result}")
        self.assertTrue(result.get("reachable"), "Expected reachable=true")
        self.assertIn("context", captured_ctx, "urlopen not called with context=")
        self.assertTrue(
            _ssl_context_has_secure_defaults(captured_ctx["context"]),
            f"Expected CERT_REQUIRED + check_hostname=True",
        )

    def test_check_server_path_explicit_insecure(self):
        """verify_tls=false → tls_verified:false + security_warning."""
        from code_tools import check_server_path

        captured_ctx = {}

        def _fake_urlopen(req, timeout=None, context=None):
            captured_ctx["context"] = context
            resp = MagicMock()
            resp.status = 200
            resp.headers = MagicMock()
            resp.headers.items.return_value = [("Content-Type", "text/html")]
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            return resp

        with patch("urllib.request.urlopen", new=_fake_urlopen):
            result = _json_load(
                asyncio.run(
                    check_server_path("https://example.com", mode="https", verify_tls=False)
                )
            )

        self.assertTrue(result["ok"], f"Expected ok=true, got {result}")
        self.assertFalse(result.get("tls_verified"), "Expected tls_verified=false")
        self.assertIn("security_warning", result, "Expected security_warning in response")
        self.assertIn("TLS verification DISABLED", result.get("security_warning", ""))
        self.assertTrue(
            _ssl_context_is_insecure(captured_ctx["context"]),
            "Expected CERT_NONE + check_hostname=False",
        )

    def test_check_server_path_http_no_tls_claim(self):
        """http:// mode → no tls_verified or security_warning (TLS not used)."""
        from code_tools import check_server_path

        def _fake_urlopen(req, timeout=None, context=None):
            resp = MagicMock()
            resp.status = 200
            resp.headers = MagicMock()
            resp.headers.items.return_value = [("Content-Type", "text/html")]
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: None
            return resp

        with patch("urllib.request.urlopen", new=_fake_urlopen):
            result = _json_load(
                asyncio.run(check_server_path("http://example.com", mode="http"))
            )

        self.assertTrue(result["ok"], f"Expected ok=true, got {result}")
        self.assertNotIn("tls_verified", result, "http:// URL should not set tls_verified")
        self.assertNotIn("security_warning", result, "http:// URL should not set security_warning")

    # ── Test 4 ───────────────────────────────────────────────────────────
    # fetch_sheet_csv_summary inherits secure verification.

    def test_fetch_sheet_csv_summary_inherits_secure_default(self):
        """Default verify_tls → webfetch receives verify_tls=True."""
        from code_tools import fetch_sheet_csv_summary

        captured_kwargs = {}

        async def _fake_webfetch(**kwargs):
            captured_kwargs.update(kwargs)
            return json.dumps({
                "ok": True,
                "url": kwargs.get("url"),
                "status_code": 200,
                "content_type": "text/csv",
                "content": "col1,col2\n1,2\n",
                "length": 17,
                "line_count": 2,
                "truncated": False,
                "tls_verified": True,
            }, ensure_ascii=False)

        with patch("code_tools.webfetch", new=_fake_webfetch):
            result = _json_load(
                asyncio.run(
                    fetch_sheet_csv_summary("https://example.com/sheet.csv")
                )
            )

        self.assertTrue(result["ok"], f"Expected ok=true, got {result}")
        self.assertTrue(
            captured_kwargs.get("verify_tls"),
            "Expected verify_tls=True passed to webfetch by default",
        )

    def test_fetch_sheet_csv_summary_explicit_insecure(self):
        """Explicit verify_tls=false → passed through to webfetch."""
        from code_tools import fetch_sheet_csv_summary

        captured_kwargs = {}

        async def _fake_webfetch(**kwargs):
            captured_kwargs.update(kwargs)
            return json.dumps({
                "ok": True,
                "url": kwargs.get("url"),
                "status_code": 200,
                "content_type": "text/csv",
                "content": "col1,col2\n1,2\n",
                "length": 17,
                "line_count": 2,
                "truncated": False,
                "tls_verified": False,
                "security_warning": "TLS verification DISABLED (verify_tls=false). Response is NOT authenticated.",
            }, ensure_ascii=False)

        with patch("code_tools.webfetch", new=_fake_webfetch):
            result = _json_load(
                asyncio.run(
                    fetch_sheet_csv_summary(
                        "https://example.com/sheet.csv", verify_tls=False
                    )
                )
            )

        self.assertTrue(result["ok"], f"Expected ok=true, got {result}")
        self.assertFalse(
            captured_kwargs.get("verify_tls"),
            "Expected verify_tls=False passed to webfetch",
        )
        self.assertFalse(result.get("tls_verified"), "Expected tls_verified=false propagated to fetch_sheet_csv_summary output")
        self.assertIn("security_warning", result, "Expected security_warning propagated to fetch_sheet_csv_summary output")
        self.assertIn("TLS verification DISABLED", result.get("security_warning", ""))

    # ── Schema visibility ─────────────────────────────────────────────────
    # verify_tls is present in function signatures and defaults to True.

    def test_schema_visibility(self):
        """verify_tls parameter is present and defaults to True for all
        three target functions."""
        import inspect as _inspect
        from code_tools import check_server_path, webfetch, fetch_sheet_csv_summary

        target_checks = [
            (check_server_path, "check_server_path"),
            (webfetch, "webfetch"),
            (fetch_sheet_csv_summary, "fetch_sheet_csv_summary"),
        ]

        for fn, name in target_checks:
            with self.subTest(tool_name=name):
                sig = _inspect.signature(fn)
                self.assertIn("verify_tls", sig.parameters,
                              f"verify_tls missing from {name} signature")
                param = sig.parameters["verify_tls"]
                self.assertIs(param.default, True,
                              f"{name} verify_tls default is not True")

        # Also verify V15_R8_NEW_SCHEMA_SPECS includes verify_tls with default true.
        from orchestrator.core.registry_data import V15_R8_NEW_SCHEMA_SPECS
        schema_by_name = {}
        for entry in V15_R8_NEW_SCHEMA_SPECS:
            if isinstance(entry, tuple) and len(entry) >= 4:
                name = entry[0]
                props = entry[2] if isinstance(entry[2], dict) else {}
                schema_by_name[name] = {"properties": props}

        registry_targets = ["check_server_path", "webfetch", "fetch_sheet_csv_summary"]
        for tool_name in registry_targets:
            with self.subTest(registry_schema=tool_name):
                self.assertIn(tool_name, schema_by_name,
                              f"{tool_name} missing from V15_R8_NEW_SCHEMA_SPECS")
                props = schema_by_name[tool_name].get("properties", {})
                self.assertIn("verify_tls", props,
                              f"verify_tls missing from {tool_name} V15_R8_NEW_SCHEMA_SPECS schema")
                vt = props.get("verify_tls", {})
                self.assertIs(vt.get("default"), True,
                              f"{tool_name} verify_tls registry default is not True")


if __name__ == "__main__":
    unittest.main()
