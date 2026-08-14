#!/usr/bin/env python3
import json
import os
import time
import urllib.request
import base64

BASE = "http://localhost:8083/api/call"
PROJ = "D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-orchestrator"
OUT_DIR = os.path.join(".council", "scan_outbox")
OUT_MD = os.path.join(OUT_DIR, "CLUSTER_W1_C6.md")

FILES = [
    "tools_v15.py",
    "orchestrator/tools/write/core.py",
    "orchestrator/tools/search.py",
    "orchestrator/tools/read/fs.py",
    "orchestrator/tools/read/symbols.py",
    "orchestrator/tools/write/copy_file.py",
    "orchestrator/tools/__init__.py",
    "orchestrator/tools/read/__init__.py",
    "orchestrator/tools/write/__init__.py",
]

log = []
saved = {}


def call(tool, body, label=None):
    url = f"{BASE}/{tool}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = resp.read().decode("utf-8")
            status = resp.status
    except Exception as e:
        data = json.dumps({"error": str(e)})
        status = 0
    elapsed = (time.time() - t0) * 1000
    try:
        parsed = json.loads(data)
    except Exception:
        parsed = None
    # count results
    count = None
    if isinstance(parsed, dict):
        for key in ("symbols", "results", "edges", "modules", "dead_code", "orphan_symbols", "callers", "matches", "nodes"):
            if key in parsed:
                val = parsed[key]
                count = len(val) if isinstance(val, list) else (1 if val is not None else 0)
                break
        if count is None:
            count = len(parsed)
    elif isinstance(parsed, list):
        count = len(parsed)
    else:
        count = 0
    entry = {
        "tool": tool,
        "label": label or tool,
        "params": body,
        "status": status,
        "elapsed_ms": round(elapsed, 1),
        "result_count": count,
        "raw": data,
    }
    log.append(entry)
    saved[label or tool] = parsed
    return parsed


# Phase A structure
print("Phase A...")
call("cbm_get_architecture", {"project_path": PROJ}, "cbm_get_architecture")
call("import_graph", {"project_path": PROJ}, "import_graph")
file_symbols = {}
for f in FILES:
    call("get_file_symbols", {"project_path": PROJ, "file_path": f}, f"get_file_symbols:{f}")

# Phase B patterns
print("Phase B...")
call("cbm_dead_code", {"project_path": PROJ, "scope": "full", "limit": 50}, "cbm_dead_code")
call("ast_query", {"pattern": "try_bare_except", "project_path": PROJ, "file_pattern": "*.py", "max_results": 50}, "ast_query:try_bare_except")
call("ast_query", {"pattern": "class_inheriting", "project_path": PROJ, "file_pattern": "*.py", "max_results": 50}, "ast_query:class_inheriting")
call("ast_query", {"pattern": "function_returning_dict", "project_path": PROJ, "file_pattern": "*.py", "max_results": 50}, "ast_query:function_returning_dict")
call("ast_query", {"pattern": "async_function", "project_path": PROJ, "file_pattern": "*.py", "max_results": 50}, "ast_query:async_function")
call("cbm_template_query", {"project_path": PROJ, "template": "orphan_symbols", "limit": 50}, "cbm_template_query:orphan_symbols")

# Collect suspicious symbols from A+B
suspicious = []


def add_suspicious(name, file=None, line=None, reason=""):
    if name and name not in [s["name"] for s in suspicious]:
        suspicious.append({"name": name, "file": file, "line": line, "reason": reason})


# dead_code
for label in ["cbm_dead_code", "cbm_template_query:orphan_symbols"]:
    res = saved.get(label)
    if not isinstance(res, dict):
        continue
    candidates = res.get("dead_code") or res.get("orphan_symbols") or res.get("results") or []
    for item in candidates:
        if isinstance(item, dict):
            name = item.get("name") or item.get("symbol")
            file = item.get("file")
            line = item.get("line")
            if name:
                add_suspicious(name, file, line, label)
        elif isinstance(item, str):
            add_suspicious(item, reason=label)

# symbols with names starting underscore from cluster files
for f in FILES:
    res = saved.get(f"get_file_symbols:{f}")
    if isinstance(res, dict):
        syms = res.get("symbols", [])
        for sym in syms:
            if isinstance(sym, dict):
                name = sym.get("name")
                kind = sym.get("kind", "")
                line = sym.get("line")
                if name and name.startswith("_") and kind in ("function", "method", "class"):
                    add_suspicious(name, f, line, "private_symbol")

# Cap suspicious symbols
suspicious = suspicious[:15]

# Phase C cross-reference
print("Phase C...")
for sym in suspicious:
    call("search_code", {"project_path": PROJ, "mode": "callers", "query": sym["name"], "max_results": 50}, f"search_code:{sym['name']}")
for sym in suspicious:
    call("find_symbol", {"project_path": PROJ, "name": sym["name"]}, f"find_symbol:{sym['name']}")

# Phase D deep reads: overlap region and for each finding with 0 callers
print("Phase D...")
read_rows = []
# overlap
ov_start, ov_end = 3700, 4000
ov = call("read_file_range", {"project_path": PROJ, "file_path": "tools_v15.py", "start_line": ov_start, "end_line": ov_end}, "read_file_range:overlap")
read_rows.append({"region": f"tools_v15.py L{ov_start}-{ov_end}", "called": True, "lines": f"L{ov_start}-{ov_end}"})

# Deep reads for zero-caller suspicious symbols
for sym in suspicious:
    caller_res = saved.get(f"search_code:{sym['name']}")
    caller_count = 0
    if isinstance(caller_res, dict):
        callers = caller_res.get("callers") or caller_res.get("results") or []
        caller_count = len(callers)
    if caller_count == 0 and sym["file"] and sym["line"]:
        start = max(1, sym["line"] - 2)
        end = sym["line"] + 23
        call("read_file_range", {"project_path": PROJ, "file_path": sym["file"], "start_line": start, "end_line": end}, f"read_file_range:{sym['name']}")

# Build artifact
os.makedirs(OUT_DIR, exist_ok=True)


def esc(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


md = []
md.append("# CLUSTER SCAN — W1_C6 (hermes/kimi-k2.7-code)")
md.append("")
md.append("## Tool Call Log (MANDATORY — all 11+ tools must appear)")
md.append("| # | Tool | Params summary | Result count | Elapsed ms | Status |")
md.append("|---|------|---------------|-------------|------------|--------|")
for i, entry in enumerate(log, 1):
    params = json.dumps(entry["params"])[:80]
    md.append(f"| {i} | {entry['label']} | {esc(params)} | {entry['result_count']} | {entry['elapsed_ms']} | {entry['status']} |")

md.append("")
md.append("## Findings (ONLY from tool results — cite tool in EVERY row)")
md.append("| # | Tool cited | file:line | severity | type | Evidence (tool output quote) |")
md.append("|---|-----------|-----------|----------|------|------------------------------|")
findings = []
# dead code / orphan
for label in ["cbm_dead_code", "cbm_template_query:orphan_symbols"]:
    res = saved.get(label)
    if isinstance(res, dict):
        items = res.get("dead_code") or res.get("orphan_symbols") or res.get("results") or []
        for item in items:
            if isinstance(item, dict):
                name = item.get("name") or item.get("symbol")
                file = item.get("file", "")
                line = item.get("line", "")
                findings.append({
                    "tool": label,
                    "loc": f"{file}:{line}" if file else "",
                    "severity": "MEDIUM",
                    "type": label.replace("cbm_", "").replace("cbm_template_query:", ""),
                    "evidence": json.dumps(item)[:200],
                })
            elif isinstance(item, str):
                findings.append({
                    "tool": label,
                    "loc": "",
                    "severity": "MEDIUM",
                    "type": label.replace("cbm_", ""),
                    "evidence": item[:200],
                })

# try bare except
for label in ["ast_query:try_bare_except"]:
    res = saved.get(label)
    if isinstance(res, dict):
        for item in res.get("results", []):
            if isinstance(item, dict):
                file = item.get("file", "")
                line = item.get("line", "")
                findings.append({
                    "tool": label,
                    "loc": f"{file}:{line}",
                    "severity": "MEDIUM",
                    "type": "bare_except",
                    "evidence": json.dumps(item)[:200],
                })

# zero caller symbols
for sym in suspicious:
    caller_res = saved.get(f"search_code:{sym['name']}")
    caller_count = 0
    if isinstance(caller_res, dict):
        callers = caller_res.get("callers") or caller_res.get("results") or []
        caller_count = len(callers)
    if caller_count == 0:
        findings.append({
            "tool": "search_code",
            "loc": f"{sym['file']}:{sym['line']}" if sym.get("file") else "",
            "severity": "HIGH" if sym.get("reason") != "private_symbol" else "LOW",
            "type": "zero_callers",
            "evidence": f"search_code callers=0 for symbol '{sym['name']}' (reason={sym.get('reason')})",
        })

for i, f in enumerate(findings, 1):
    md.append(f"| {i} | {f['tool']} | {esc(f['loc'])} | {f['severity']} | {f['type']} | {esc(f['evidence'])} |")

md.append("")
md.append("## Overlap Verification")
md.append("| Overlap region | read_file_range called? | Lines read |")
md.append("|----------------|------------------------|------------|")
for r in read_rows:
    md.append(f"| {r['region']} | {r['called']} | {r['lines']} |")

md.append("")
md.append("## Coverage")
md.append("| File | get_file_symbols | read_file_range | search_code |")
md.append("|------|-----------------|-----------------|-------------|")
for f in FILES:
    has_sym = "yes" if saved.get(f"get_file_symbols:{f}") else "no"
    has_read = "yes" if f == "tools_v15.py" else "no"
    has_search = "yes" if any(s["file"] == f for s in suspicious) else "no"
    md.append(f"| {f} | {has_sym} | {has_read} | {has_search} |")

md.append("")
md.append("END_CLUSTER_W1_C6")

md_text = "\n".join(md)
with open(OUT_MD, "w", encoding="utf-8") as f:
    f.write(md_text)
print(f"Wrote {OUT_MD}")

# Also write raw JSON log next to it for audit
log_path = os.path.join(OUT_DIR, "CLUSTER_W1_C6_log.json")
with open(log_path, "w", encoding="utf-8") as f:
    json.dump(log, f, indent=2)
print(f"Wrote {log_path}")
