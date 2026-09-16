"""Test toàn bộ tool trong ToolA — kiểm tra 4 Ktr: KtrB, KtrSK, KtrSH, KtrIn.
Chạy từ thư mục project-orchestrator.
"""
import sys
import os
import re
import json
import importlib.util

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# ── Đường dẫn ──
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

SKILL_ROOT = os.path.join(BASE, ".ToolA_Skill")
SKILLS_DIR = os.path.join(SKILL_ROOT, "skills")
TOOL_REFERENCE_PATH = os.path.join(BASE, ".ToolA_Skill", "references", "tool_surface_current.md")
SKILLS_MD_PATH = os.path.join(SKILL_ROOT, "SKILLS.md")

# ── 1. Load TOOL_MAP, TOOL_SCHEMAS, TOOL_PHASES, T1/T2/T3_TOOLS từ code_tools ──
def load_code_tools():
    spec = importlib.util.spec_from_file_location("code_tools", os.path.join(BASE, "code_tools.py"))
    ct = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ct)
    return ct

ct = load_code_tools()

TOOL_MAP = ct.TOOL_MAP
TOOL_SCHEMAS = ct.TOOL_SCHEMAS
TOOL_PHASES = ct.TOOL_PHASES
T1_TOOLS = ct.T1_TOOLS
T2_TOOLS = ct.T2_TOOLS
T3_TOOLS = ct.T3_TOOLS

# ── 2. Load tools_v15 ──
def load_tools_v15():
    spec = importlib.util.spec_from_file_location("tools_v15", os.path.join(BASE, "tools_v15.py"))
    tv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tv)
    return tv

tv15 = load_tools_v15()

# ── 3. Thu thập TẤT CẢ tool names từ mọi nguồn ──
# Từ TOOL_MAP
tool_map_names = set(TOOL_MAP.keys())

# Từ TOOL_SCHEMAS
schema_names = set()
for s in TOOL_SCHEMAS:
    fn = s.get("function", {})
    if fn.get("name"):
        schema_names.add(fn["name"])

# Từ T1/T2/T3 lists
tier_list_names = set(T1_TOOLS + T2_TOOLS + T3_TOOLS)

# Từ tools_v15.py (các async function)
tools_v15_names = set()
for name in dir(tv15):
    obj = getattr(tv15, name)
    if callable(obj) and not name.startswith("_") and name not in {"json", "Path", "re", "ast", "time", "socket", "hashlib", "asyncio", "subprocess", "datetime"}:
        # Check if it's actually an async function defined in this module
        if hasattr(obj, "__code__") and obj.__code__.co_filename.endswith("tools_v15.py"):
            tools_v15_names.add(name)

# Từ tool_surface_current.md
inventory_names = set()
if os.path.exists(TOOL_REFERENCE_PATH):
    with open(TOOL_REFERENCE_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    # Tìm các tool name trong backtick
    for m in re.finditer(r"`([a-z_][a-z_0-9]+)`", content):
        inventory_names.add(m.group(1))

# Từ SKILLS.md router
router_tool_mentions = set()
if os.path.exists(SKILLS_MD_PATH):
    with open(SKILLS_MD_PATH, "r", encoding="utf-8") as f:
        for line in f:
            # Match tool names in backticks or after arrows
            for m in re.finditer(r"`([a-z_][a-z_0-9_]+)`", line):
                router_tool_mentions.add(m.group(1))

# ── 4. Build master tool list ──
# Canonical public-tool surface. Inventory and implementation helpers are
# reference inputs for KtrIn/KtrB, not sources of additional public tools.
all_sources = tool_map_names | schema_names | tier_list_names

# Lọc: chỉ giữ những tên trông giống tool name (snake_case, không phải từ tiếng Anh thông thường)
def looks_like_tool_name(name):
    if len(name) < 3:
        return False
    if not re.match(r"^[a-z][a-z0-9_]+$", name):
        return False
    # Loại các từ tiếng Anh thông thường không phải tool
    common_words = {"the", "and", "for", "not", "with", "from", "this", "that", "will", "have",
                    "been", "were", "they", "their", "what", "when", "where", "which", "while",
                    "true", "false", "none", "null", "auto", "all", "any", "each", "some",
                    "ok", "yes", "no", "new", "old", "run", "get", "set", "put", "log",
                    "test", "type", "file", "path", "mode", "name", "size", "time", "data",
                    "root", "base", "main", "core", "full", "text", "list", "find", "read",
                    "write", "open", "close", "save", "load", "show", "hide", "start", "stop",
                    "end", "top", "bottom", "left", "right", "first", "last", "next", "prev",
                    "back", "front", "high", "low", "max", "min", "avg", "sum", "count",
                    "total", "partial", "full", "empty", "valid", "invalid", "error", "warn",
                    "info", "debug", "trace", "level", "state", "status", "result", "output",
                    "input", "source", "target", "key", "value", "item", "index", "group",
                    "class", "function", "method", "property", "field", "param", "arg",
                    "return", "yield", "break", "continue", "pass", "raise", "try", "catch",
                    "finally", "throw", "await", "async", "sync", "wait", "sleep", "delay",
                    "timeout", "interval", "period", "duration", "rate", "speed", "limit",
                    "range", "scope", "context", "session", "token", "auth", "user", "admin",
                    "role", "perm", "access", "grant", "deny", "allow", "block", "skip",
                    "keep", "drop", "add", "remove", "delete", "create", "update", "patch",
                    "merge", "split", "join", "sort", "filter", "map", "reduce", "fold",
                    "scan", "zip", "unzip", "compress", "decompress", "encode", "decode",
                    "parse", "format", "render", "draw", "paint", "color", "style", "theme",
                    "layout", "grid", "flex", "block", "inline", "table", "row", "col",
                    "cell", "header", "footer", "nav", "menu", "tab", "panel", "dialog",
                    "modal", "popup", "tooltip", "badge", "icon", "image", "video", "audio",
                    "media", "link", "url", "uri", "route", "page", "view", "screen",
                    "window", "frame", "canvas", "chart", "graph", "plot", "map", "tree",
                    "node", "edge", "vertex", "path", "cycle", "loop", "branch", "leaf",
                    "root", "parent", "child", "sibling", "ancestor", "descendant",
                    }
    if name.lower() in common_words:
        return False
    return True

master_tools = sorted([
    n for n in all_sources
    if n in tool_map_names or n in schema_names or looks_like_tool_name(n)
])
source_counts = {
    "tool_map": len(tool_map_names),
    "schema": len(schema_names),
    "tier_lists": len(tier_list_names),
    "tools_v15_functions_reference_only": len(tools_v15_names),
    "inventory_reference_only": len(inventory_names),
    "canonical_total": len(master_tools),
}

# ── 5. Kiểm tra 4 Ktr cho mỗi tool ──

def check_ktrb(name):
    """KtrB: Backend — tool có implementation trong code_tools.py hoặc tools_v15.py không?"""
    if name in TOOL_MAP:
        return "PASS"
    # Check tools_v15
    if hasattr(tv15, name) and callable(getattr(tv15, name)):
        return "PASS"
    # Check code_tools module directly
    if hasattr(ct, name) and callable(getattr(ct, name)):
        return "PASS"
    return "MISSING"

def check_ktrsk(name):
    """KtrSK: Skill — tool được đề cập trong skill nào không?"""
    found_skills = []
    if not os.path.exists(SKILLS_DIR):
        return "MISSING", []
    for skill_dir in sorted(os.listdir(SKILLS_DIR)):
        skill_path = os.path.join(SKILLS_DIR, skill_dir, "SKILL.md")
        if not os.path.exists(skill_path):
            continue
        with open(skill_path, "r", encoding="utf-8") as f:
            content = f.read().lower()
        # Tìm tool name trong content
        if re.search(rf"\b{re.escape(name)}\b", content):
            found_skills.append(skill_dir)
    # Also check SKILLS.md router
    if os.path.exists(SKILLS_MD_PATH):
        with open(SKILLS_MD_PATH, "r", encoding="utf-8") as f:
            router_content = f.read().lower()
        if re.search(rf"\b{re.escape(name)}\b", router_content):
            if "SKILLS.md_router" not in found_skills:
                found_skills.append("SKILLS.md_router")
    if found_skills:
        return "PASS", found_skills
    return "MISSING", []

def check_ktrsh(name):
    """KtrSH: Schema + Hint — tool có TOOL_SCHEMAS entry với required fields không?"""
    if name not in schema_names:
        return "MISSING", None
    # Tìm schema
    schema = None
    for s in TOOL_SCHEMAS:
        fn = s.get("function", {})
        if fn.get("name") == name:
            schema = fn
            break
    if not schema:
        return "MISSING", None
    # Kiểm tra có description không
    has_desc = bool(schema.get("description", "").strip())
    # Kiểm tra có parameters không
    params = schema.get("parameters", {})
    props = params.get("properties", {})
    required = params.get("required", [])
    has_param_object = params.get("type") == "object"
    has_params = len(props) > 0
    has_required = len(required) > 0
    # Kiểm tra có x_phase không
    has_phase = bool(schema.get("x_phase"))
    # Đánh giá
    if has_desc and (has_params or has_param_object):
        return "PASS", schema
    else:
        return "WEAK", schema

def check_ktrin(name):
    """KtrIn: Tool reference coverage — tool có trong tool_surface_current.md không?"""
    if name in inventory_names:
        return "PASS"
    return "MISSING"

def get_tool_phase(name):
    return TOOL_PHASES.get(name, "?")

def get_tool_description(name):
    for s in TOOL_SCHEMAS:
        fn = s.get("function", {})
        if fn.get("name") == name:
            return fn.get("description", "")[:100]
    return ""

# ── 6. Tạo test case cho mỗi tool ──
def generate_test_case(name, phase, ktrb, ktrsk, ktrsh, ktrin):
    """Tạo test case giả lập workflow cho tool."""
    test_cases = []
    
    # Test case cơ bản dựa trên phase
    if phase == "T1":
        test_cases.append({
            "name": "TC_BASIC_READ",
            "desc": f"Gọi {name} với project_path hợp lệ, kiểm tra response ok=True",
            "workflow": "open_session → generate_project_map → {name}",
            "expected": "ok=True, có dữ liệu trả về"
        })
        test_cases.append({
            "name": "TC_MISSING_PROJECT",
            "desc": f"Gọi {name} بدون project_path (nếu required), kiểm tra error",
            "workflow": "{name} (không truyền project_path)",
            "expected": "error_name=MISSING_REQUIRED_FIELD hoặc PATH_NOT_FOUND"
        })
    elif phase == "T2":
        test_cases.append({
            "name": "TC_DRY_RUN",
            "desc": f"Gọi {name} với dry_run=True, kiểm tra không ghi file",
            "workflow": "open_session → generate_project_map → {name}(dry_run=True)",
            "expected": "ok=True, dry_run=True, có diff preview"
        })
        test_cases.append({
            "name": "TC_WRITE_AND_VALIDATE",
            "desc": f"Gọi {name} với dry_run=False → validate_file",
            "workflow": "open_session → generate_project_map → {name}(dry_run=False) → validate_file",
            "expected": "ok=True, validation ok, cache_synced=True"
        })
    elif phase == "T3":
        test_cases.append({
            "name": "TC_SESSION_LIFECYCLE",
            "desc": f"Kiểm tra {name} trong lifecycle session đầy đủ",
            "workflow": "open_session → {name} → close_session",
            "expected": "ok=True, session event được log"
        })
    elif phase == "T4":
        test_cases.append({
            "name": "TC_RUNTIME",
            "desc": f"Kiểm tra {name} với runtime environment",
            "workflow": "open_session → inspect_project_runtime → {name}",
            "expected": "ok=True, có dữ liệu runtime"
        })
    
    # Test case đặc biệt cho Ktr
    if ktrb == "MISSING":
        test_cases.append({
            "name": "TC_KTRB_FAIL",
            "desc": f"Tool {name} không có backend implementation",
            "workflow": "Gọi trực tiếp {name} qua API",
            "expected": "UNKNOWN_TOOL hoặc error"
        })
    if ktrsk == "MISSING":
        test_cases.append({
            "name": "TC_KTRSK_FAIL",
            "desc": f"Tool {name} không được đề cập trong skill nào",
            "workflow": "CA search skill để tìm cách dùng {name}",
            "expected": "Không tìm thấy skill đề cập đến tool này"
        })
    if ktrsh == "MISSING":
        test_cases.append({
            "name": "TC_KTRSH_FAIL",
            "desc": f"Tool {name} không có schema trong TOOL_SCHEMAS",
            "workflow": "GET /api/tools?name_contains={name}",
            "expected": "Không có schema trả về"
        })
    if ktrin == "MISSING":
        test_cases.append({
            "name": "TC_KTRIN_FAIL",
            "desc": f"Tool {name} không có trong tool reference",
            "workflow": "audit_tool_matrix → kiểm tra coverage",
            "expected": "reference coverage drift"
        })
    
    return test_cases

# ── 7. Chạy kiểm tra ──
results = []
for name in master_tools:
    phase = get_tool_phase(name)
    ktrb = check_ktrb(name)
    ktrsk, ktrsk_skills = check_ktrsk(name)
    ktrsh, ktrsh_schema = check_ktrsh(name)
    ktrin = check_ktrin(name)
    desc = get_tool_description(name)
    test_cases = generate_test_case(name, phase, ktrb, ktrsk, ktrsh, ktrin)
    
    results.append({
        "name": name,
        "phase": phase,
        "ktrb": ktrb,
        "ktrsk": ktrsk,
        "ktrsh": ktrsh,
        "ktrin": ktrin,
        "ktrsk_skills": ktrsk_skills,
        "description": desc,
        "test_cases": test_cases,
    })

# ── 8. Phân nhóm theo vấn đề ──
groups = {
    "THIEU_KTRSK": [r for r in results if r["ktrsk"] == "MISSING"],
    "THIEU_KTRSH": [r for r in results if r["ktrsh"] in ("MISSING", "WEAK")],
    "THIEU_KTRB": [r for r in results if r["ktrb"] == "MISSING"],
    "THIEU_KTRIN": [r for r in results if r["ktrin"] == "MISSING"],
    "FULL_PASS": [r for r in results if r["ktrb"] == "PASS" and r["ktrsk"] == "PASS" and r["ktrsh"] == "PASS" and r["ktrin"] == "PASS"],
}

# ── 9. In báo cáo ──
print("=" * 80)
print("BÁO CÁO KIỂM TRA TOÀN BỘ TOOL — 4 KTR")
print(f"Tổng số tool: {len(master_tools)}")
print(f"  KtrB PASS: {sum(1 for r in results if r['ktrb'] == 'PASS')}")
print(f"  KtrSK PASS: {sum(1 for r in results if r['ktrsk'] == 'PASS')}")
print(f"  KtrSH PASS: {sum(1 for r in results if r['ktrsh'] == 'PASS')}")
print(f"  KtrIn PASS: {sum(1 for r in results if r['ktrin'] == 'PASS')}")
print(f"  FULL PASS (cả 4): {len(groups['FULL_PASS'])}")
print("=" * 80)

# Nhóm 1: Thiếu KtrSK
print(f"\n--- NHÓM 1: THIẾU KtrSK (không có skill nào đề cập đến tool) ---")
print(f"Số lượng: {len(groups['THIEU_KTRSK'])} tool\n")
for i, r in enumerate(groups['THIEU_KTRSK'], 1):
    print(f" {i:3d}. {r['name']}")
    print(f"      Phase: {r['phase']} | KtrB: {r['ktrb']} | KtrSK: {r['ktrsk']} | KtrSH: {r['ktrsh']} | KtrIn: {r['ktrin']}")
    print(f"      Mô tả: {r['description'][:80] if r['description'] else '(không có description)'}")
    print(f"      Vấn đề: Không được đề cập trong bất kỳ skill nào.")
    print(f"      Test cases:")
    for tc in r['test_cases']:
        print(f"        - {tc['name']}: {tc['desc']}")
        print(f"          Workflow: {tc['workflow']}")
        print(f"          Expected: {tc['expected']}")
    print()

# Nhóm 2: Thiếu KtrSH
print(f"\n--- NHÓM 2: THIẾU/WEAK KtrSH (không có schema hoặc schema không đầy đủ) ---")
print(f"Số lượng: {len(groups['THIEU_KTRSH'])} tool\n")
for i, r in enumerate(groups['THIEU_KTRSH'], 1):
    print(f" {i:3d}. {r['name']}")
    print(f"      Phase: {r['phase']} | KtrB: {r['ktrb']} | KtrSK: {r['ktrsk']} | KtrSH: {r['ktrsh']} | KtrIn: {r['ktrin']}")
    print(f"      Mô tả: {r['description'][:80] if r['description'] else '(không có description)'}")
    if r['ktrsh'] == 'MISSING':
        print(f"      Vấn đề: Không có entry trong TOOL_SCHEMAS.")
    else:
        print(f"      Vấn đề: Có schema nhưng thiếu required fields hoặc description.")
    print(f"      Test cases:")
    for tc in r['test_cases']:
        print(f"        - {tc['name']}: {tc['desc']}")
        print(f"          Workflow: {tc['workflow']}")
        print(f"          Expected: {tc['expected']}")
    print()

# Nhóm 3: Thiếu KtrB
print(f"\n--- NHÓM 3: THIẾU KtrB (không có backend implementation) ---")
print(f"Số lượng: {len(groups['THIEU_KTRB'])} tool\n")
for i, r in enumerate(groups['THIEU_KTRB'], 1):
    print(f" {i:3d}. {r['name']}")
    print(f"      Phase: {r['phase']} | KtrB: {r['ktrb']} | KtrSK: {r['ktrsk']} | KtrSH: {r['ktrsh']} | KtrIn: {r['ktrin']}")
    print(f"      Mô tả: {r['description'][:80] if r['description'] else '(không có description)'}")
    print(f"      Vấn đề: Không tìm thấy function implementation trong code_tools.py hoặc tools_v15.py.")
    print(f"      Test cases:")
    for tc in r['test_cases']:
        print(f"        - {tc['name']}: {tc['desc']}")
        print(f"          Workflow: {tc['workflow']}")
        print(f"          Expected: {tc['expected']}")
    print()

# Nhóm 4: Thiếu KtrIn
print(f"\n--- NHÓM 4: THIẾU KtrIn (không có trong tool reference) ---")
print(f"Số lượng: {len(groups['THIEU_KTRIN'])} tool\n")
for i, r in enumerate(groups['THIEU_KTRIN'], 1):
    print(f" {i:3d}. {r['name']}")
    print(f"      Phase: {r['phase']} | KtrB: {r['ktrb']} | KtrSK: {r['ktrsk']} | KtrSH: {r['ktrsh']} | KtrIn: {r['ktrin']}")
    print(f"      Mô tả: {r['description'][:80] if r['description'] else '(không có description)'}")
    print(f"      Vấn đề: Không có trong .ToolA_Skill/references/tool_surface_current.md.")
    print(f"      Test cases:")
    for tc in r['test_cases']:
        print(f"        - {tc['name']}: {tc['desc']}")
        print(f"          Workflow: {tc['workflow']}")
        print(f"          Expected: {tc['expected']}")
    print()

# Nhóm 5: FULL PASS
print(f"\n--- NHÓM 5: FULL PASS (đầy đủ cả 4 Ktr) ---")
print(f"Số lượng: {len(groups['FULL_PASS'])} tool\n")
for i, r in enumerate(groups['FULL_PASS'], 1):
    skills_str = ", ".join(r['ktrsk_skills']) if r['ktrsk_skills'] else ""
    print(f" {i:3d}. {r['name']} (Phase: {r['phase']}, Skills: {skills_str})")

# ── 10. Summary ──
print(f"\n{'=' * 80}")
print("TÓM TẮT")
print(f"{'=' * 80}")
print(f"Tổng tool đã kiểm tra: {len(results)}")
print(f"  ✅ FULL PASS (4/4 Ktr): {len(groups['FULL_PASS'])}")
print(f"  ❌ Thiếu KtrSK: {len(groups['THIEU_KTRSK'])}")
print(f"  ❌ Thiếu/WEAK KtrSH: {len(groups['THIEU_KTRSH'])}")
print(f"  ❌ Thiếu KtrB: {len(groups['THIEU_KTRB'])}")
print(f"  ❌ Thiếu KtrIn: {len(groups['THIEU_KTRIN'])}")

# Lưu kết quả ra file JSON
output_path = os.path.join(BASE, "tool_test_results.json")
with open(output_path, "w", encoding="utf-8") as f:
    json.dump({
        "total_tools": len(results),
        "source_counts": source_counts,
        "source_policy": "canonical public tools = TOOL_MAP ∪ TOOL_SCHEMAS ∪ TOOL_PHASE lists; inventory/tools_v15 helper names are reference-only",
        "summary": {
            "full_pass": len(groups['FULL_PASS']),
            "missing_ktrsk": len(groups['THIEU_KTRSK']),
            "missing_ktrsh": len(groups['THIEU_KTRSH']),
            "missing_ktrb": len(groups['THIEU_KTRB']),
            "missing_ktrin": len(groups['THIEU_KTRIN']),
        },
        "results": results,
    }, f, ensure_ascii=False, indent=2)
print(f"\nKết quả chi tiết đã lưu vào: {output_path}")
