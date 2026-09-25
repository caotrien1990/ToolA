"""CSLt P3 — MCP pack generator (SOT).

Actions: generate | install_hermes | install_targets | test_pack | list_packs
         | uninstall_hermes | remove_pack | uninstall_targets
Default dry_run=True. Complementary to manage_agent.sync_mcp (does not replace).
Per-CA identity: ca_id → mcp_server_key = <pack>__<ca_id> so concurrent CAs
get isolated MCP processes (no shared tool lock like a shared API socket).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

DEFAULT_OUT_ROOT = Path(r"D:/BR/_CSLt_plan/mcp_packs")
DEFAULT_PYTHON = Path(
    r"D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/"
    r"project-orchestrator/venv312/Scripts/python.exe",
)
DEFAULT_HERMES_CONFIG = Path.home() / "AppData" / "Local" / "hermes" / "config.yaml"

BUILTIN_PACKS: dict[str, dict[str, Any]] = {
    "toolq": {
        "base_url": "http://127.0.0.1:8082",
        "base_url_env": "TOOLQ_BASE_URL",
        "server_name": "toolq",
        "kind": "http_relay",
        "tools": [
            {"name": "q_health", "method": "GET", "path": "/health"},
            {
                "name": "q_pre_search",
                "method": "POST",
                "path": "/pre_search",
                "body_keys": ["q", "planned_action", "prompt"],
            },
            {
                "name": "q_query",
                "method": "POST",
                "path": "/query",
                "body_keys": ["q", "query"],
            },
            {
                "name": "q_ask",
                "method": "POST",
                "path": "/ask",
                "body_keys": ["q", "question"],
            },
            {
                "name": "q_capture",
                "method": "POST",
                "path": "/capture",
                # ToolQ handle_capture: body {session_id, project?, turns: [{user_prompt,
                # assistant_response}]}; a turn carrying neither is skipped, not refused.
                "body_keys": ["session_id", "project", "user_prompt", "assistant_response"],
                "list_body": {"turns": ["user_prompt", "assistant_response"]},
            },
            {
                "name": "q_ingest_path",
                "method": "POST",
                "path": "/ingest_path",
                "body_keys": ["source_type", "path", "content", "zone"],
            },
            {"name": "q_browse", "method": "GET", "path": "/help"},
            {"name": "q_call", "method": "ANY", "path": "*", "escape": True},
        ],
    },
    "council": {
        "base_url": "http://127.0.0.1:8083",
        "base_url_env": "ORCHESTRATOR_BASE_URL",
        "server_name": "orchestrator-council",
        "kind": "orchestrator_call",
        "tools": [
            {"name": n, "via": "call"}
            for n in [
                "get_rule_core_text",
                "open_session",
                "log_native_event",
                "list_sessions",
                "council_message_async",
                "council_worker_model",
                "council_message",
                "convene_council",
                "council_status",
                "leader_bootstrap",
                "mission_status",
                "mission_next",
                "mission_dispatch",
                "mission_gate",
                "mission_create",
                "mission_report",
                "cbm_council_context",
                "declare_mission",
            ]
        ],
    },
    # CSLt 2026-07-16: curated core pack — tool list comes from the L4 SOT
    # (orchestrator.core.registry_data.CURATED_CORE_TOOLS); filled right below so
    # packs can never fork from what /api/tools/names advertises.
    "curated_core": {
        "base_url": "http://127.0.0.1:8083",
        "base_url_env": "ORCHESTRATOR_BASE_URL",
        "server_name": "curated-core",
        "kind": "orchestrator_call",
        "tools": [],
    },
}

try:
    from orchestrator.core.registry_data import CURATED_CORE_TOOLS as _CURATED_CORE_SOT
    BUILTIN_PACKS["curated_core"]["tools"] = [
        {"name": n, "via": "call"} for n in _CURATED_CORE_SOT
    ]
except Exception:  # registry import must never break generation of the other packs
    BUILTIN_PACKS.pop("curated_core", None)


def _ok(**kw: Any) -> dict[str, Any]:
    return {"ok": True, **kw}


def _err(name: str, msg: str, **kw: Any) -> dict[str, Any]:
    return {"ok": False, "error_name": name, "error": msg, **kw}


def _safe_id(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9_\-]+", "_", s)
    return s.strip("_") or "pack"


def _resolve_catalog(
    pack_id: str,
    tools_json: str = "",
    base_url: str = "",
    server_name: str = "",
    catalog_json: str = "",
) -> dict[str, Any]:
    if catalog_json:
        try:
            cat = json.loads(catalog_json)
        except Exception as e:
            return _err("BAD_CATALOG_JSON", str(e))
        if not isinstance(cat, dict):
            return _err("BAD_CATALOG", "catalog must be object")
        cat = dict(cat)
        cat.setdefault("pack_id", _safe_id(pack_id or cat.get("pack_id") or "custom"))
        return cat

    pid = _safe_id(pack_id)
    if pid in BUILTIN_PACKS:
        cat = json.loads(json.dumps(BUILTIN_PACKS[pid]))
    else:
        names: list[str] = []
        if tools_json:
            try:
                parsed = json.loads(tools_json)
                if isinstance(parsed, list):
                    names = [str(x) for x in parsed]
            except Exception as e:
                return _err("BAD_TOOLS_JSON", str(e))
        if not names:
            return _err(
                "UNKNOWN_PACK",
                f"pack_id={pid} not builtin; pass tools_json or catalog_json",
                known=list(BUILTIN_PACKS),
            )
        cat = {
            "base_url": base_url or "http://127.0.0.1:8083",
            "base_url_env": "ORCHESTRATOR_BASE_URL",
            "server_name": server_name or pid,
            "kind": "orchestrator_call",
            "tools": [{"name": n, "via": "call"} for n in names],
        }
    if base_url:
        cat["base_url"] = base_url
    if server_name:
        cat["server_name"] = server_name
    cat["pack_id"] = pid
    return cat


def _server_template_http_relay(cat: dict[str, Any], identity: dict[str, Any]) -> str:
    base_env = cat.get("base_url_env") or "TOOLQ_BASE_URL"
    default_base = cat.get("base_url") or "http://127.0.0.1:8082"
    tools = cat.get("tools") or []
    named = [t for t in tools if not t.get("escape")]
    srv = cat.get("server_name") or cat.get("pack_id") or "pack"
    lines = [
        "#!/usr/bin/env python",
        '"""AUTO-GENERATED MCP pack — regenerate via mcp_pack_generate."""',
        "from __future__ import annotations",
        "import json, os, sys, urllib.error, urllib.request",
        f"BASE = (os.environ.get({base_env!r}) or {default_base!r}).rstrip('/')",
        "TIMEOUT = float(os.environ.get('MCP_PACK_HTTP_TIMEOUT', '60'))",
        f"PACK_ID = {cat.get('pack_id')!r}",
        f"CA_ID = {identity.get('ca_id')!r}",
        "def _try_json(t):",
        "    try:",
        "        return json.loads(t)",
        "    except Exception:",
        "        return t",
        "def _http(method, path, payload=None):",
        "    url = BASE + (path if str(path).startswith('/') else '/' + str(path))",
        "    data = None",
        "    headers = {'Accept': 'application/json'}",
        "    if payload is not None:",
        "        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')",
        "        headers['Content-Type'] = 'application/json'",
        "    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())",
        "    try:",
        "        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:",
        "            body = resp.read().decode('utf-8', 'replace')",
        "            code = resp.getcode()",
        "    except urllib.error.HTTPError as e:",
        "        err = e.read().decode('utf-8', 'replace') if e.fp else ''",
        "        return json.dumps({'ok': False, 'error_name': 'HTTP_ERROR', 'status': e.code, 'path': path, 'body': _try_json(err) if err else None}, ensure_ascii=False)",
        "    except Exception as e:",
        "        return json.dumps({'ok': False, 'error_name': 'CONN_ERROR', 'error': str(e), 'base_url': BASE, 'path': path}, ensure_ascii=False)",
        "    parsed = _try_json(body)",
        "    if isinstance(parsed, dict):",
        "        parsed.setdefault('_meta', {})",
        "        if isinstance(parsed['_meta'], dict):",
        "            parsed['_meta'].update({'http_status': code, 'path': path, 'pack_id': PACK_ID, 'ca_id': CA_ID})",
        "        return json.dumps(parsed, ensure_ascii=False)",
        "    return json.dumps({'ok': True, 'http_status': code, 'path': path, 'body': body}, ensure_ascii=False)",
        "def run():",
        "    try:",
        "        from zeromcp import McpServer",
        "    except ImportError as e:",
        "        sys.stderr.write('zeromcp missing\\n')",
        "        raise SystemExit(2) from e",
        f"    mcp = McpServer({srv!r})",
    ]
    for t in named:
        name = t["name"]
        method = (t.get("method") or "GET").upper()
        path = t.get("path") or "/"
        body_keys = t.get("body_keys") or []
        if method == "GET" and not body_keys:
            lines += [
                "    @mcp.tool",
                f"    def {name}() -> str:",
                f"        \"\"\"{name}: {method} {path}\"\"\"",
                f"        return _http({method!r}, {path!r})",
            ]
        else:
            args = ", ".join(f"{k}: str = ''" for k in body_keys) or "payload_json: str = ''"
            lines += [
                "    @mcp.tool",
                f"    def {name}({args}) -> str:",
                f"        \"\"\"{name}: {method} {path}\"\"\"",
            ]
            if body_keys:
                lines.append("        payload = {}")
                for k in body_keys:
                    lines.append(f"        if {k}:")
                    lines.append(f"            payload[{k!r}] = {k}")
                # a route that reads its fields inside one list item declares them in list_body;
                # the catalog owns the shape, so no tool name is special-cased here
                for list_key, item_keys in (t.get("list_body") or {}).items():
                    lines += [
                        f"        item = {{k: payload.pop(k) for k in {item_keys!r} if k in payload}}",
                        "        if item:",
                        f"            payload[{list_key!r}] = [item]",
                    ]
                lines.append(f"        return _http({method!r}, {path!r}, payload or None)")
            else:
                lines += [
                    "        payload = None",
                    "        if payload_json:",
                    "            payload = json.loads(payload_json)",
                    f"        return _http({method!r}, {path!r}, payload)",
                ]
    lines += [
        "    @mcp.tool",
        "    def pack_call(method: str = 'GET', path: str = '/', payload_json: str = '') -> str:",
        '        """Escape hatch HTTP call."""',
        "        if not path.startswith('/'):",
        "            return json.dumps({'ok': False, 'error_name': 'PATH_MUST_START_WITH_SLASH'})",
        "        payload = None",
        "        if payload_json:",
        "            payload = json.loads(payload_json)",
        "        m = (method or 'GET').upper()",
        "        if m == 'GET':",
        "            return _http('GET', path)",
        "        return _http(m, path, payload if isinstance(payload, dict) else {})",
        "    mcp.stdio()",
        "if __name__ == '__main__':",
        "    run()",
        "",
    ]
    return "\n".join(lines)


def _server_template_orchestrator_call(cat: dict[str, Any], identity: dict[str, Any]) -> str:
    base_env = cat.get("base_url_env") or "ORCHESTRATOR_BASE_URL"
    default_base = cat.get("base_url") or "http://127.0.0.1:8083"
    tools = [t.get("name") for t in (cat.get("tools") or []) if t.get("name")]
    srv = cat.get("server_name") or cat.get("pack_id") or "pack"
    lines = [
        "#!/usr/bin/env python",
        '"""AUTO-GENERATED orchestrator MCP pack — regenerate via mcp_pack_generate."""',
        "from __future__ import annotations",
        "import json, os, sys, urllib.error, urllib.request",
        f"BASE = (os.environ.get({base_env!r}) or {default_base!r}).rstrip('/')",
        "TIMEOUT = float(os.environ.get('MCP_PACK_HTTP_TIMEOUT', '120'))",
        f"PACK_ID = {cat.get('pack_id')!r}",
        f"CA_ID = {identity.get('ca_id')!r}",
        f"TOOL_NAMES = {tools!r}",
        "_TOKEN = {'v': ''}",
        "_TOKEN_ERRORS = ('MUTATION_TOKEN_REQUIRED', 'MUTATION_TOKEN_EXPIRED', 'TOKEN_REQUIRED')",
        "def _fetch_token():",
        "    # CSLt 2026-07-16: token stays inside the pack process — never returned to the CA.",
        "    try:",
        "        with urllib.request.urlopen(BASE + '/api/token', timeout=10) as r:",
        "            _TOKEN['v'] = (json.loads(r.read().decode('utf-8', 'replace')) or {}).get('token') or ''",
        "    except Exception:",
        "        _TOKEN['v'] = ''",
        "    return _TOKEN['v']",
        "def _call(name, payload=None, _retried=False):",
        "    url = BASE + '/api/call/' + name",
        "    data = json.dumps(payload or {}, ensure_ascii=False).encode('utf-8')",
        "    headers = {'Content-Type': 'application/json', 'Accept': 'application/json', 'Origin': BASE}",
        "    if _TOKEN['v']:",
        "        headers['X-Orchestrator-Token'] = _TOKEN['v']",
        "    req = urllib.request.Request(url, data=data, headers=headers, method='POST')",
        "    try:",
        "        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:",
        "            body = resp.read().decode('utf-8', 'replace')",
        "    except urllib.error.HTTPError as e:",
        "        body = e.read().decode('utf-8', 'replace') if e.fp else ''",
        "        try:",
        "            perr = json.loads(body) if body else {}",
        "        except Exception:",
        "            perr = {}",
        "        if (not _retried) and e.code in (401, 403) and str(perr.get('error_name', '')) in _TOKEN_ERRORS:",
        "            if _fetch_token():",
        "                return _call(name, payload, _retried=True)",
        "        if isinstance(perr, dict) and perr:",
        "            perr.setdefault('_meta', {})",
        "            if isinstance(perr.get('_meta'), dict):",
        "                perr['_meta'].update({'pack_id': PACK_ID, 'ca_id': CA_ID, 'tool': name, 'http_status': e.code})",
        "            return json.dumps(perr, ensure_ascii=False)",
        "        return json.dumps({'ok': False, 'error_name': 'HTTP_ERROR', 'status': e.code, 'tool': name, 'body': body[:2000]}, ensure_ascii=False)",
        "    except Exception as e:",
        "        return json.dumps({'ok': False, 'error_name': 'CONN_ERROR', 'tool': name, 'error': str(e), 'base_url': BASE}, ensure_ascii=False)",
        "    try:",
        "        parsed = json.loads(body)",
        "        if isinstance(parsed, dict):",
        "            if (not _retried) and str(parsed.get('error_name', '')) in _TOKEN_ERRORS:",
        "                if _fetch_token():",
        "                    return _call(name, payload, _retried=True)",
        "            parsed.setdefault('_meta', {})",
        "            if isinstance(parsed['_meta'], dict):",
        "                parsed['_meta'].update({'pack_id': PACK_ID, 'ca_id': CA_ID, 'tool': name})",
        "            return json.dumps(parsed, ensure_ascii=False)",
        "    except Exception:",
        "        pass",
        "    return body",
        "def run():",
        "    try:",
        "        from zeromcp import McpServer",
        "    except ImportError as e:",
        "        sys.stderr.write('zeromcp missing\\n')",
        "        raise SystemExit(2) from e",
        f"    mcp = McpServer({srv!r})",
        "    @mcp.tool",
        "    def call_tool(name: str = '', payload_json: str = '{}') -> str:",
        '        """Gateway: call any tool by name with JSON payload."""',
        "        try:",
        "            payload = json.loads(payload_json or '{}')",
        "        except Exception as e:",
        "            return json.dumps({'ok': False, 'error_name': 'BAD_PAYLOAD', 'error': str(e)})",
        "        if not name:",
        "            return json.dumps({'ok': False, 'error_name': 'MISSING_NAME', 'tools': TOOL_NAMES})",
        "        return _call(name, payload if isinstance(payload, dict) else {})",
        "    @mcp.tool",
        "    def list_pack_tools() -> str:",
        '        """List tools included in this pack."""',
        "        return json.dumps({'ok': True, 'pack_id': PACK_ID, 'ca_id': CA_ID, 'tools': TOOL_NAMES}, ensure_ascii=False)",
    ]
    for name in tools:
        if name in ("call_tool", "list_pack_tools"):
            continue
        safe = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        lines += [
            "    @mcp.tool",
            f"    def {safe}(payload_json: str = '{{}}') -> str:",
            f'        """First-class relay for {name}."""',
            "        try:",
            "            payload = json.loads(payload_json or '{}')",
            "        except Exception as e:",
            "            return json.dumps({'ok': False, 'error_name': 'BAD_PAYLOAD', 'error': str(e)})",
            f"        return _call({name!r}, payload if isinstance(payload, dict) else {{}})",
        ]
    lines += [
        "    mcp.stdio()",
        "if __name__ == '__main__':",
        "    run()",
        "",
    ]
    return "\n".join(lines)


def _hermes_fragment(
    *,
    server_key: str,
    python_exe: str,
    server_script: str,
    base_url_env: str,
    base_url: str,
    extra_env: dict[str, str] | None = None,
) -> str:
    env_lines = [f"      {base_url_env}: {base_url}"]
    for k, v in (extra_env or {}).items():
        if v is None:
            continue
        env_lines.append(f"      {k}: {v}")
    env_block = "\n".join(env_lines)
    return (
        f"# generated by mcp_pack_generate — server key: {server_key}\n"
        f"mcp_servers:\n"
        f"  {server_key}:\n"
        f"    command: {python_exe}\n"
        f"    args:\n"
        f"      - {server_script}\n"
        f"    env:\n"
        f"{env_block}\n"
        f"    timeout: 120\n"
        f"    connect_timeout: 60\n"
        f"    enabled: true\n"
    )


def _identity(pack_id: str, ca_id: str = "", server_name: str = "", out_root: str = "") -> dict[str, Any]:
    pid = _safe_id(pack_id)
    cid = _safe_id(ca_id) if ca_id else ""
    # Prefer explicit server_name → existing pack.json → builtin catalog → pack_id
    srv = server_name
    if not srv:
        root = Path(out_root) if out_root else DEFAULT_OUT_ROOT
        candidates = []
        if cid:
            candidates.append(root / pid / cid / "pack.json")
        candidates.append(root / pid / "pack.json")
        for pj in candidates:
            if pj.exists():
                try:
                    data = json.loads(pj.read_text(encoding="utf-8"))
                    # prefer base without __ca suffix
                    sn = str(data.get("server_name") or "")
                    if sn.endswith(f"__{cid}") and cid:
                        sn = sn[: -(len(cid) + 2)]
                    if sn:
                        srv = sn
                        break
                    ci = data.get("ca_identity") or {}
                    key = str(ci.get("mcp_server_key") or "")
                    if key.endswith(f"__{cid}") and cid:
                        srv = key[: -(len(cid) + 2)]
                        break
                except Exception:
                    pass
    if not srv and pid in BUILTIN_PACKS:
        srv = str(BUILTIN_PACKS[pid].get("server_name") or pid)
    base_server = _safe_id(srv) if srv else pid
    if cid:
        mcp_key = f"{base_server}__{cid}"
        out_suffix = f"{pid}/{cid}"
    else:
        mcp_key = base_server
        out_suffix = pid
    return {
        "ca_id": cid or None,
        "pack_id": pid,
        "mcp_server_key": mcp_key,
        "out_suffix": out_suffix,
        "isolation": "per_ca_mcp_process",
    }


def _write_pack_files(
    out_dir: Path,
    cat: dict[str, Any],
    identity: dict[str, Any],
    python_exe: str,
    dry_run: bool,
) -> dict[str, Any]:
    kind = cat.get("kind") or "http_relay"
    if kind == "orchestrator_call":
        src = _server_template_orchestrator_call(cat, identity)
    else:
        src = _server_template_http_relay(cat, identity)
    server_rel = "server/mcp_stdio.py"
    server_path = out_dir / server_rel
    pack_json = {
        "pack_id": cat.get("pack_id"),
        "server_name": cat.get("server_name") or cat.get("pack_id"),
        "kind": kind,
        "base_url": cat.get("base_url"),
        "base_url_env": cat.get("base_url_env"),
        "tools": cat.get("tools"),
        "ca_identity": identity,
        "python": python_exe,
        "server": server_rel,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "content_sha256": hashlib.sha256(src.encode("utf-8")).hexdigest(),
    }
    server_key = identity.get("mcp_server_key") or cat.get("server_name") or cat.get("pack_id")
    fragment = _hermes_fragment(
        server_key=str(server_key),
        python_exe=python_exe,
        server_script=str(server_path).replace("\\", "/"),
        base_url_env=str(cat.get("base_url_env") or "ORCHESTRATOR_BASE_URL"),
        base_url=str(cat.get("base_url") or "http://127.0.0.1:8083"),
        extra_env={"MCP_PACK_CA_ID": identity.get("ca_id") or ""},
    )
    notes = (
        f"# Install notes — pack `{cat.get('pack_id')}` / CA `{identity.get('ca_id') or '-'}`\n\n"
        f"- MCP server key: `{server_key}` (per-CA isolation)\n"
        f"- Python: `{python_exe}` (needs zeromcp)\n"
        f"- Base URL env: `{cat.get('base_url_env')}` = `{cat.get('base_url')}`\n"
        f"- Generate path: `{out_dir}`\n"
        f"- Wire: merge `hermes_fragment.yaml` OR "
        f"`mcp_pack_generate action=install_hermes pack_id=... ca_id=... dry_run=false`\n"
        f"- Rule: 1 registered CA → 1 MCP process/key (avoid concurrent tool contention).\n"
    )
    files = {
        str(server_path): src,
        str(out_dir / "pack.json"): json.dumps(pack_json, ensure_ascii=False, indent=2) + "\n",
        str(out_dir / "hermes_fragment.yaml"): fragment,
        str(out_dir / "ca_install_notes.md"): notes,
    }
    if dry_run:
        return _ok(
            action="generate",
            dry_run=True,
            out_dir=str(out_dir),
            files=list(files),
            pack=pack_json,
            preview={
                "server_bytes": len(src),
                "server_sha256": pack_json["content_sha256"],
                "hermes_fragment": fragment,
            },
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "server").mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        Path(path).write_text(content, encoding="utf-8")
    return _ok(
        action="generate",
        dry_run=False,
        out_dir=str(out_dir),
        files=list(files),
        pack=pack_json,
    )


def action_generate(
    *,
    pack_id: str,
    ca_id: str = "",
    tools_json: str = "",
    base_url: str = "",
    server_name: str = "",
    catalog_json: str = "",
    out_root: str = "",
    python_exe: str = "",
    dry_run: bool = True,
) -> dict[str, Any]:
    cat = _resolve_catalog(pack_id, tools_json, base_url, server_name, catalog_json)
    if cat.get("ok") is False:
        return cat
    ident = _identity(cat.get("pack_id") or pack_id, ca_id, cat.get("server_name") or server_name)
    # per-CA: unique server_name so MCP process identity matches registry CA
    if ca_id:
        cat["server_name"] = ident["mcp_server_key"]
    else:
        cat["server_name"] = cat.get("server_name") or ident["mcp_server_key"]
    root = Path(out_root) if out_root else DEFAULT_OUT_ROOT
    out_dir = root / ident["out_suffix"]
    py = python_exe or str(DEFAULT_PYTHON)
    return _write_pack_files(out_dir, cat, ident, py, dry_run)


def action_list_packs(out_root: str = "") -> dict[str, Any]:
    root = Path(out_root) if out_root else DEFAULT_OUT_ROOT
    if not root.exists():
        return _ok(packs=[], out_root=str(root), note="out_root missing", builtins=list(BUILTIN_PACKS))
    packs = []
    for p in sorted(root.rglob("pack.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            data = {"error": str(e)}
        packs.append({"path": str(p), "pack": data})
    return _ok(packs=packs, count=len(packs), out_root=str(root), builtins=list(BUILTIN_PACKS))


def action_test_pack(
    *,
    pack_id: str,
    ca_id: str = "",
    out_root: str = "",
    python_exe: str = "",
) -> dict[str, Any]:
    ident = _identity(pack_id, ca_id, out_root=out_root)
    root = Path(out_root) if out_root else DEFAULT_OUT_ROOT
    server = root / ident["out_suffix"] / "server" / "mcp_stdio.py"
    if not server.exists():
        return _err("PACK_NOT_FOUND", f"missing {server}", hint="run generate dry_run=false first")
    py = python_exe or str(DEFAULT_PYTHON)
    r1 = subprocess.run([py, "-m", "py_compile", str(server)], capture_output=True, text=True)
    if r1.returncode != 0:
        return _err("PY_COMPILE_FAILED", r1.stderr or r1.stdout, server=str(server))
    code = (
        "import importlib.util, json\n"
        f"spec=importlib.util.spec_from_file_location('pack_mod', r'''{server}''')\n"
        "mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
        "out={'has_run': hasattr(mod,'run'), 'has_http': hasattr(mod,'_http'), 'has_call': hasattr(mod,'_call')}\n"
        "if hasattr(mod,'_http'):\n"
        "    out['probe']=mod._http('GET','/health')[:400]\n"
        "elif hasattr(mod,'_call'):\n"
        # CSLt 2026-07-16: orchestrator_call packs must NOT probe [A] over HTTP from
        # inside test_pack — subprocess.run blocks [A]'s event loop, so the HTTP call
        # back to [A] deadlocks until timeout. Static introspection is enough here;
        # live HTTP is validated by the CA (or manually) outside the [A] process.
        "    out['probe']={'static_only': True, 'tool_count': len(getattr(mod,'TOOL_NAMES',[]))}\n"
        "print(json.dumps(out))\n"
    )
    r2 = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=60)
    if r2.returncode != 0:
        return _err("TEST_IMPORT_FAILED", r2.stderr or r2.stdout, server=str(server))
    probe = None
    try:
        probe = json.loads(r2.stdout.strip().splitlines()[-1])
    except Exception:
        probe = {"raw": r2.stdout[:500]}
    return _ok(action="test_pack", server=str(server), py_compile=True, probe=probe)


def action_install_hermes(
    *,
    pack_id: str,
    ca_id: str = "",
    out_root: str = "",
    hermes_config: str = "",
    dry_run: bool = True,
) -> dict[str, Any]:
    ident = _identity(pack_id, ca_id, out_root=out_root)
    root = Path(out_root) if out_root else DEFAULT_OUT_ROOT
    frag_path = root / ident["out_suffix"] / "hermes_fragment.yaml"
    if not frag_path.exists():
        return _err("FRAGMENT_MISSING", str(frag_path), hint="generate first")
    fragment = frag_path.read_text(encoding="utf-8")
    m = re.search(r"(?ms)^mcp_servers:\s*\n(.*)\Z", fragment)
    block = m.group(1) if m else fragment
    cfg_path = Path(hermes_config) if hermes_config else DEFAULT_HERMES_CONFIG
    if not cfg_path.exists():
        return _err("HERMES_CONFIG_MISSING", str(cfg_path))
    text = cfg_path.read_text(encoding="utf-8")
    key = ident["mcp_server_key"]
    already = re.search(rf"(?m)^\s*{re.escape(str(key))}:\s*$", text) is not None
    if already:
        return _ok(
            action="install_hermes",
            dry_run=dry_run,
            status="already_present",
            key=key,
            config=str(cfg_path),
        )
    if "mcp_servers:" not in text:
        return _err("NO_MCP_SERVERS_KEY", "config missing mcp_servers:")
    if dry_run:
        return _ok(
            action="install_hermes",
            dry_run=True,
            status="would_insert",
            key=key,
            block=block,
            config=str(cfg_path),
        )
    bak = cfg_path.with_suffix(cfg_path.suffix + f".bak_mcp_pack_{int(time.time())}")
    shutil.copy2(cfg_path, bak)
    _insert = block + ("\n" if not block.endswith("\n") else "")
    # CSLt 2026-07-16: replacement via lambda — fragment blocks contain Windows
    # paths (D:\...) whose backslashes are invalid escapes in a re.sub template.
    text2 = re.sub(
        r"(?m)^(mcp_servers:\s*\n)",
        lambda m: m.group(1) + _insert,
        text,
        count=1,
    )
    cfg_path.write_text(text2, encoding="utf-8")
    return _ok(
        action="install_hermes",
        dry_run=False,
        status="inserted",
        key=key,
        backup=str(bak),
        config=str(cfg_path),
    )


def action_install_targets(
    *,
    pack_id: str,
    targets_json: str = "",
    ca_id: str = "",
    out_root: str = "",
    dry_run: bool = True,
) -> dict[str, Any]:
    targets: list[dict[str, str]] = []
    if targets_json:
        try:
            parsed = json.loads(targets_json)
        except Exception as e:
            return _err("BAD_TARGETS_JSON", str(e))
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, str):
                    targets.append({"ca_id": item})
                elif isinstance(item, dict):
                    targets.append(
                        {
                            "ca_id": str(item.get("ca_id") or item.get("agent_id") or ""),
                            "hermes_config": str(
                                item.get("hermes_config") or item.get("config") or "",
                            ),
                        },
                    )
    elif ca_id:
        targets = [{"ca_id": ca_id}]
    else:
        return _err("MISSING_TARGETS", "pass targets_json or ca_id")
    results = []
    for t in targets:
        gen = action_generate(
            pack_id=pack_id,
            ca_id=t.get("ca_id") or "",
            out_root=out_root,
            dry_run=dry_run,
        )
        inst = action_install_hermes(
            pack_id=pack_id,
            ca_id=t.get("ca_id") or "",
            out_root=out_root,
            hermes_config=t.get("hermes_config") or "",
            dry_run=dry_run,
        )
        results.append({"ca_id": t.get("ca_id"), "generate": gen, "install": inst})
    return _ok(action="install_targets", dry_run=dry_run, results=results, count=len(results))

def _remove_mcp_server_key(text: str, key: str) -> tuple[str, bool, str]:
    """Remove a top-level mcp_servers.<key> block (sibling keys at indent=2)."""
    key = str(key)
    # YAML text, not NDJSON: YAML 1.1 itself breaks lines at U+0085/U+2028/U+2029,
    # so splitlines() matches the parser here -- deliberately NOT text_lines.split_lines.
    lines = text.splitlines(keepends=True)
    start = None
    for i, ln in enumerate(lines):
        if re.match(rf"^  {re.escape(key)}:\s*(?:#.*)?\r?\n?$", ln):
            start = i
            break
    if start is None:
        return text, False, ""
    end = start + 1
    while end < len(lines):
        ln = lines[end]
        # next sibling key under mcp_servers
        if re.match(r"^  [A-Za-z0-9_.\-]+:\s*(?:#.*)?\r?\n?$", ln):
            break
        # next top-level key / end section
        if re.match(r"^\S", ln):
            break
        end += 1
    removed = "".join(lines[start:end])
    new = "".join(lines[:start] + lines[end:])
    new = re.sub(r"\n{3,}", "\n\n", new)
    return new, True, removed


PROTECTED_MCP_KEYS = frozenset({"project-orchestrator"})


def action_uninstall_hermes(
    *,
    pack_id: str,
    ca_id: str = "",
    out_root: str = "",
    hermes_config: str = "",
    dry_run: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Unwire pack MCP key from Hermes config (complement of install_hermes)."""
    ident = _identity(pack_id, ca_id, out_root=out_root)
    key = ident["mcp_server_key"]
    if key in PROTECTED_MCP_KEYS and not force:
        return _err(
            "PROTECTED_MCP_KEY",
            f"refusing to uninstall protected key {key!r}",
            key=key,
            hint="pass force=true only if intentional (not recommended)",
        )
    cfg_path = Path(hermes_config) if hermes_config else DEFAULT_HERMES_CONFIG
    if not cfg_path.exists():
        return _err("HERMES_CONFIG_MISSING", str(cfg_path))
    text = cfg_path.read_text(encoding="utf-8")
    if "mcp_servers:" not in text:
        return _err("NO_MCP_SERVERS_KEY", "config missing mcp_servers:")
    new_text, found, removed = _remove_mcp_server_key(text, key)
    if not found:
        return _ok(
            action="uninstall_hermes",
            dry_run=dry_run,
            status="already_absent",
            key=key,
            config=str(cfg_path),
            identity=ident,
        )
    if dry_run:
        return _ok(
            action="uninstall_hermes",
            dry_run=True,
            status="would_remove",
            key=key,
            block_preview=removed[:800],
            config=str(cfg_path),
            identity=ident,
        )
    bak = cfg_path.with_suffix(cfg_path.suffix + f".bak_mcp_unwire_{int(time.time())}")
    shutil.copy2(cfg_path, bak)
    cfg_path.write_text(new_text, encoding="utf-8")
    return _ok(
        action="uninstall_hermes",
        dry_run=False,
        status="removed",
        key=key,
        backup=str(bak),
        config=str(cfg_path),
        identity=ident,
    )


def action_remove_pack(
    *,
    pack_id: str,
    ca_id: str = "",
    out_root: str = "",
    hermes_config: str = "",
    dry_run: bool = True,
    delete_files: bool = False,
    keep_wire: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Retire unused pack: unwire Hermes (+ optional rmtree pack dir).

    Safety defaults: dry_run=True, delete_files=False (unwire-only).
    Refuse paths outside out_root. Protected keys need force=true.
    """
    ident = _identity(pack_id, ca_id, out_root=out_root)
    root = Path(out_root) if out_root else DEFAULT_OUT_ROOT
    out_dir = (root / ident["out_suffix"]).resolve()
    root_res = root.resolve()
    try:
        out_dir.relative_to(root_res)
    except ValueError:
        return _err(
            "PACK_PATH_OUTSIDE_ROOT",
            f"{out_dir} not under {root_res}",
            out_dir=str(out_dir),
            out_root=str(root_res),
        )
    unwire: dict[str, Any] | None = None
    if not keep_wire:
        unwire = action_uninstall_hermes(
            pack_id=pack_id,
            ca_id=ca_id,
            out_root=out_root,
            hermes_config=hermes_config,
            dry_run=dry_run,
            force=force,
        )
        if unwire.get("ok") is False:
            return unwire
    exists = out_dir.exists()
    files: list[str] = []
    if exists:
        files = [str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file()][:50]
    if not delete_files:
        return _ok(
            action="remove_pack",
            dry_run=dry_run,
            status="unwire_only" if not keep_wire else "noop_files",
            pack_dir=str(out_dir),
            pack_exists=exists,
            files_sample=files,
            delete_files=False,
            unwire=unwire,
            identity=ident,
            hint="pass delete_files=true to rmtree pack dir (still respects dry_run)",
        )
    if not exists:
        return _ok(
            action="remove_pack",
            dry_run=dry_run,
            status="pack_dir_already_absent",
            pack_dir=str(out_dir),
            delete_files=True,
            unwire=unwire,
            identity=ident,
        )
    if dry_run:
        return _ok(
            action="remove_pack",
            dry_run=True,
            status="would_delete_pack_dir",
            pack_dir=str(out_dir),
            files_sample=files,
            file_count_capped=len(files),
            delete_files=True,
            unwire=unwire,
            identity=ident,
        )
    shutil.rmtree(out_dir)
    parent = out_dir.parent
    if parent != root_res and parent.exists() and not any(parent.iterdir()):
        try:
            parent.rmdir()
        except OSError:
            pass
    return _ok(
        action="remove_pack",
        dry_run=False,
        status="deleted_pack_dir",
        pack_dir=str(out_dir),
        delete_files=True,
        unwire=unwire,
        identity=ident,
    )


def action_uninstall_targets(
    *,
    pack_id: str,
    targets_json: str = "",
    ca_id: str = "",
    out_root: str = "",
    hermes_config: str = "",
    dry_run: bool = True,
    delete_files: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    targets: list[dict[str, str]] = []
    if targets_json:
        try:
            parsed = json.loads(targets_json)
        except Exception as e:
            return _err("BAD_TARGETS_JSON", str(e))
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, str):
                    targets.append({"ca_id": item})
                elif isinstance(item, dict):
                    targets.append(
                        {
                            "ca_id": str(item.get("ca_id") or item.get("agent_id") or ""),
                            "hermes_config": str(
                                item.get("hermes_config") or item.get("config") or "",
                            ),
                        },
                    )
    elif ca_id:
        targets = [{"ca_id": ca_id}]
    else:
        return _err("MISSING_TARGETS", "pass targets_json or ca_id")
    results = []
    for t in targets:
        res = action_remove_pack(
            pack_id=pack_id,
            ca_id=t.get("ca_id") or "",
            out_root=out_root,
            hermes_config=t.get("hermes_config") or hermes_config or "",
            dry_run=dry_run,
            delete_files=delete_files,
            force=force,
        )
        results.append({"ca_id": t.get("ca_id"), "remove": res})
    return _ok(
        action="uninstall_targets",
        dry_run=dry_run,
        delete_files=delete_files,
        results=results,
        count=len(results),
    )

def mcp_pack_generate(
    action: str = "generate",
    pack_id: str = "toolq",
    ca_id: str = "",
    agent_id: str = "",
    tools_json: str = "",
    catalog_json: str = "",
    base_url: str = "",
    server_name: str = "",
    out_root: str = "",
    out_dir: str = "",
    python_exe: str = "",
    hermes_config: str = "",
    targets_json: str = "",
    dry_run: bool = True,
    delete_files: bool = False,
    keep_wire: bool = False,
    force: bool = False,
) -> str:
    """Entry used by HTTP tool / CLI."""
    act = (action or "generate").strip().lower()
    cid = ca_id or agent_id or ""
    if out_dir and not out_root:
        out_root = out_dir
    try:
        if act in ("generate", "gen", "create"):
            res = action_generate(
                pack_id=pack_id,
                ca_id=cid,
                tools_json=tools_json,
                base_url=base_url,
                server_name=server_name,
                catalog_json=catalog_json,
                out_root=out_root,
                python_exe=python_exe,
                dry_run=bool(dry_run),
            )
        elif act in ("list", "list_packs"):
            res = action_list_packs(out_root=out_root)
        elif act in ("test", "test_pack"):
            res = action_test_pack(
                pack_id=pack_id, ca_id=cid, out_root=out_root, python_exe=python_exe,
            )
        elif act in ("install_hermes", "install"):
            res = action_install_hermes(
                pack_id=pack_id,
                ca_id=cid,
                out_root=out_root,
                hermes_config=hermes_config,
                dry_run=bool(dry_run),
            )
        elif act in ("install_targets", "install_multi"):
            res = action_install_targets(
                pack_id=pack_id,
                targets_json=targets_json,
                ca_id=cid,
                out_root=out_root,
                dry_run=dry_run,
            )
        elif act in ("uninstall_hermes", "unwire", "uninstall"):
            res = action_uninstall_hermes(
                pack_id=pack_id,
                ca_id=cid,
                out_root=out_root,
                hermes_config=hermes_config,
                dry_run=bool(dry_run),
                force=bool(force),
            )
        elif act in ("remove_pack", "remove", "delete_pack"):
            res = action_remove_pack(
                pack_id=pack_id,
                ca_id=cid,
                out_root=out_root,
                hermes_config=hermes_config,
                dry_run=bool(dry_run),
                delete_files=bool(delete_files),
                keep_wire=bool(keep_wire),
                force=bool(force),
            )
        elif act in ("uninstall_targets", "remove_targets", "uninstall_multi"):
            res = action_uninstall_targets(
                pack_id=pack_id,
                targets_json=targets_json,
                ca_id=cid,
                out_root=out_root,
                hermes_config=hermes_config,
                dry_run=bool(dry_run),
                delete_files=bool(delete_files),
                force=bool(force),
            )
        else:
            res = _err(
                "UNKNOWN_ACTION",
                act,
                allowed=[
                    "generate",
                    "list_packs",
                    "test_pack",
                    "install_hermes",
                    "install_targets",
                    "uninstall_hermes",
                    "remove_pack",
                    "uninstall_targets",
                ],
            )
    except Exception as e:
        res = _err("MCP_PACK_GENERATE_FAILED", str(e), action=act)
    return json.dumps(res, ensure_ascii=False)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="mcp_pack_generate CLI")
    ap.add_argument("--action", default="generate")
    ap.add_argument("--pack-id", default="toolq")
    ap.add_argument("--ca-id", default="")
    ap.add_argument("--tools-json", default="")
    ap.add_argument("--catalog-json", default="")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--server-name", default="")
    ap.add_argument("--out-root", default="")
    ap.add_argument("--python-exe", default="")
    ap.add_argument("--hermes-config", default="")
    ap.add_argument("--targets-json", default="")
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--no-dry-run", action="store_true", default=False)
    ap.add_argument("--delete-files", action="store_true", default=False)
    ap.add_argument("--keep-wire", action="store_true", default=False)
    ap.add_argument("--force", action="store_true", default=False)
    args = ap.parse_args()
    dry = True
    if args.no_dry_run:
        dry = False
    elif args.dry_run:
        dry = True
    print(
        mcp_pack_generate(
            action=args.action,
            pack_id=args.pack_id,
            ca_id=args.ca_id,
            tools_json=args.tools_json,
            catalog_json=args.catalog_json,
            base_url=args.base_url,
            server_name=args.server_name,
            out_root=args.out_root,
            python_exe=args.python_exe,
            hermes_config=args.hermes_config,
            targets_json=args.targets_json,
            dry_run=dry,
            delete_files=bool(args.delete_files),
            keep_wire=bool(args.keep_wire),
            force=bool(args.force),
        ),
    )
