import asyncio
import hashlib
import inspect
import json
import shutil
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import code_tools
import orchestrator_server as srv
from orchestrator.server import mcp_stdio
from orchestrator.tools.read import adaptive


class AdaptiveReadContractTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="adaptive_read_contract_"))
        with adaptive._CACHE_COND:
            adaptive._CACHE_BY_PATH.clear()
            adaptive._CACHE_BY_HASH.clear()
            adaptive._INFLIGHT.clear()
            adaptive._CACHE_BYTES = 0

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)
        with adaptive._CACHE_COND:
            adaptive._CACHE_BY_PATH.clear()
            adaptive._CACHE_BY_HASH.clear()
            adaptive._INFLIGHT.clear()
            adaptive._CACHE_BYTES = 0

    def _write(self, name, content):
        path = self.tempdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _read(self, file_path, **kwargs):
        return json.loads(asyncio.run(code_tools.read_file_range(
            file_path, project_path=str(self.tempdir), **kwargs,
        )))

    def test_runtime_registry_imports_all_adaptive_read_tools(self):
        for name in ("read_file_range", "read_context_batch", "architecture_context_pack"):
            self.assertIn(name, code_tools.TOOL_MAP)
            self.assertTrue(callable(code_tools.TOOL_MAP[name]))

    def test_adaptive_read_schemas_match_runtime_required_fields(self):
        schemas = {
            item["function"]["name"]: item["function"]
            for item in code_tools.TOOL_SCHEMAS
        }
        self.assertEqual(schemas["read_file_range"]["parameters"]["required"], ["file_path"])
        self.assertEqual(
            schemas["read_context_batch"]["parameters"]["required"],
            ["project_path", "requests"],
        )
        self.assertEqual(
            schemas["architecture_context_pack"]["parameters"]["required"],
            ["project_path"],
        )
        read_parameters = inspect.signature(code_tools.read_file_range).parameters
        for field in ("scope", "symbol", "section", "token_budget", "content_hash", "force_refresh"):
            self.assertIn(field, read_parameters)

    def test_list_structure_is_readonly_by_default(self):
        schema = next(
            item["function"] for item in code_tools.TOOL_SCHEMAS
            if item["function"]["name"] == "list_structure"
        )
        self.assertFalse(schema["parameters"]["properties"]["save_output"]["default"])
        with tempfile.TemporaryDirectory() as folder:
            result = json.loads(asyncio.run(code_tools.list_structure(project_path=folder)))
            self.assertTrue(result.get("ok"), msg=result)
            self.assertFalse((Path(folder) / "structure_output.txt").exists())

    def test_server_admits_new_tools_through_read_semaphore(self):
        self.assertEqual(srv._TOOL_SEM_CATEGORY["read_context_batch"], "read")
        self.assertEqual(srv._TOOL_SEM_CATEGORY["architecture_context_pack"], "read")

    def test_mcp_surface_exposes_adaptive_parameters_and_tools(self):
        source = inspect.getsource(mcp_stdio.run_mcp_stdio)
        self.assertIn("def read_context_batch(", source)
        self.assertIn("def architecture_context_pack(", source)
        self.assertIn("scope: str = \"range\"", source)
        self.assertIn("force_refresh: bool = False", source)

    def test_bootstrap_catalog_includes_new_read_tools(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = json.loads(asyncio.run(code_tools.bootstrap_session(
                folder, session_id="adaptive-read-test", agent_id="test-agent",
                owner_claim_id="adaptive-read-0123456789abcdef0123456789abcdef",
            )))
        tools = {item["name"]: item for item in payload["key_tools_compact"]}
        self.assertEqual(tools["read_file_range"]["required"], ["file_path"])
        self.assertIn("read_context_batch", tools)
        self.assertIn("architecture_context_pack", tools)

    def test_read_file_range_auto_reads_a_small_file_in_full(self):
        self._write("small.py", "first = 1\nsecond = 2\nthird = 3\n")

        result = self._read("small.py")

        self.assertTrue(result.get("ok"), msg=result)
        self.assertTrue(result["auto_full_file"])
        self.assertEqual(result["shown"], "1-3")
        self.assertEqual(result["result"], "1: first = 1\n2: second = 2\n3: third = 3")
        self.assertFalse(result["truncated"])

    def test_read_file_range_explicit_range_keeps_legacy_inclusive_semantics(self):
        self._write("legacy.txt", "one\ntwo\nthree\nfour\n")

        result = self._read("legacy.txt", start_line=2, end_line=3)

        self.assertTrue(result.get("ok"), msg=result)
        self.assertFalse(result["auto_full_file"])
        self.assertEqual(result["scope_resolved"], "range")
        self.assertEqual(result["shown"], "2-3")
        self.assertEqual(result["result"], "2: two\n3: three")
        self.assertFalse(result["truncated"])

    def test_read_file_range_selects_a_qualified_function_structure(self):
        self._write(
            "structure.py",
            "class Worker:\n"
            "    def run(self):\n"
            "        value = 41\n"
            "        return value + 1\n"
            "\n"
            "def unrelated():\n"
            "    return 0\n",
        )

        result = self._read("structure.py", scope="function", symbol="Worker.run")

        self.assertTrue(result.get("ok"), msg=result)
        self.assertEqual(result["scope_resolved"], "function")
        self.assertEqual(result["selection"]["qualified_name"], "Worker.run")
        self.assertEqual(result["shown"], "2-4")
        self.assertIn("def run", result["result"])
        self.assertNotIn("unrelated", result["result"])

    def test_read_file_range_reports_hash_and_reuses_cached_document(self):
        content = "alpha\nbeta\n"
        self._write("cached.txt", content)
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        first = self._read("cached.txt")
        second = self._read("cached.txt", content_hash=expected_hash)
        stale = self._read("cached.txt", content_hash="0" * 64)

        self.assertFalse(first["cache_hit"])
        self.assertEqual(first["content_hash"], expected_hash)
        self.assertTrue(second["cache_hit"])
        self.assertEqual(second["content_hash"], expected_hash)
        self.assertNotIn("content_hash_mismatch", second)
        self.assertFalse(stale["cache_hit"])
        self.assertTrue(stale["content_hash_mismatch"])
        self.assertEqual(stale["warn"], "supplied_content_hash_stale")

    def test_read_context_batch_enforces_one_total_token_budget(self):
        self._write("first.txt", "\n".join(f"first line {i}" for i in range(30)) + "\n")
        self._write("second.txt", "\n".join(f"second line {i}" for i in range(30)) + "\n")

        result = json.loads(asyncio.run(code_tools.read_context_batch(
            str(self.tempdir),
            [{"file_path": "first.txt"}, {"file_path": "second.txt"}],
            token_budget=64,
        )))

        self.assertTrue(result.get("ok"), msg=result)
        self.assertEqual(result["token_budget"], 64)
        self.assertEqual(result["count"], 2)
        self.assertEqual(
            result["used_tokens"],
            sum(item.get("estimated_tokens", 0) for item in result["items"]),
        )
        self.assertLessEqual(result["used_tokens"], result["token_budget"])
        self.assertEqual(
            result["remaining_tokens"],
            result["token_budget"] - result["used_tokens"],
        )
        self.assertTrue(result["within_budget"])

    def test_architecture_context_pack_file_fallback_is_readonly_and_does_not_reindex(self):
        source = self._write("fallback.py", "def fallback():\n    return True\n")
        before = {path.relative_to(self.tempdir): path.read_bytes()
                  for path in self.tempdir.rglob("*") if path.is_file()}

        result = json.loads(asyncio.run(code_tools.architecture_context_pack(
            str(self.tempdir), file_path=source.name, token_budget=256,
        )))

        after = {path.relative_to(self.tempdir): path.read_bytes()
                 for path in self.tempdir.rglob("*") if path.is_file()}
        self.assertTrue(result.get("ok"), msg=result)
        self.assertFalse(result["graph_used"])
        self.assertFalse(result["provenance"]["graph_auto_reindex"])
        self.assertFalse(result["derived_state_touched"])
        self.assertEqual(result["ranked_files"], ["fallback.py"])
        self.assertEqual(before, after)
        self.assertFalse((self.tempdir / ".orchestrator_cache").exists())

    def test_get_document_singleflight_performs_one_delayed_physical_read(self):
        source = self._write("singleflight.txt", "shared content\n")
        original_read_text = Path.read_text
        read_started = threading.Event()
        release_read = threading.Event()
        call_count = 0
        count_lock = threading.Lock()
        worker_barrier = threading.Barrier(3)

        def delayed_read_text(path, *args, **kwargs):
            nonlocal call_count
            if path.resolve() == source.resolve():
                with count_lock:
                    call_count += 1
                read_started.set()
                self.assertTrue(release_read.wait(1.0))
            return original_read_text(path, *args, **kwargs)

        def load_document():
            worker_barrier.wait()
            return adaptive.get_document(source, str(self.tempdir))

        with mock.patch.object(Path, "read_text", autospec=True,
                               side_effect=delayed_read_text):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(load_document) for _ in range(2)]
                worker_barrier.wait()
                self.assertTrue(read_started.wait(1.0))
                time.sleep(0.05)
                with count_lock:
                    self.assertEqual(call_count, 1)
                self.assertTrue(any(not future.done() for future in futures))
                release_read.set()
                results = [future.result(timeout=1.0) for future in futures]

        self.assertEqual(call_count, 1)
        self.assertEqual([meta["cache_hit"] for _doc, meta in results].count(False), 1)
        self.assertEqual([meta["cache_hit"] for _doc, meta in results].count(True), 1)
        self.assertTrue(any(meta["cache_waited"] for _doc, meta in results))
        self.assertEqual({doc.text for doc, _meta in results}, {"shared content\n"})


    def test_read_file_range_past_eof_is_loud(self):
        self._write("short.txt", "only\nthree\nlines\n")
        result = self._read("short.txt", start_line=3754, end_line=3760)
        self.assertFalse(result.get("ok"), msg=result)
        self.assertEqual(result.get("error_name"), "RANGE_PAST_EOF")
        self.assertEqual(result.get("total_lines"), 3)
        self.assertNotIn("3754-3753", str(result.get("shown", "")))
        self.assertFalse((result.get("result") or "").strip())

    def test_read_file_range_last_line_still_ok(self):
        self._write("short.txt", "only\nthree\nlines\n")
        result = self._read("short.txt", start_line=3, end_line=3)
        self.assertTrue(result.get("ok"), msg=result)
        self.assertEqual(result["shown"], "3-3")
        self.assertIn("lines", result["result"])


if __name__ == "__main__":
    unittest.main()
