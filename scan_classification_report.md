# 📂 Phân loại cấu trúc Project Orchestrator

**Project:** ToolA Project Orchestrator (multi-agent orchestration system)  
**Path:** `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\`  
**Git root:** Không (no .git ở root)  
**Scan date:** 2026-06-10  

---

## 🔴 CỐT LÕI — Đang active dùng

### Root files
| File | Size | Ghi chú |
|------|------|---------|
| `orchestrator_server.py` | 356K | Main MCP server, active development |
| `code_tools.py` | 764K | Core tool implementation, active (Jun 10) |
| `orchestrator.log` | 164M | Live runtime log (đang append) |
| `orchestrator_ui.py` | 5.6K | UI component, active |
| `surgical_mapper.py` | 35K | Tool mapping utility, active |
| `restart_orchestrator.py` | 6.3K | Restart orchestrator, active |
| `start_orchestrator.ps1` / `.bat` | 11K/3.3K | Launch scripts, active |
| `agents_registry.json` | 2.7K | Agent config (6 agents: clau, codo, clin, copi, opencode, comc) |
| `skills-lock.json` | 3.3K | Skill registry mapping taste-skill sources → local paths |
| `AGENT_BOOTSTRAP_PROMPT.md` | 15K | Agent bootstrap prompt, active |
| `AGENT_WORKFLOW.md` | 5.3K | Workflow doc, active |
| `WORKFLOW_T1.md`–`T4.md` | 4x~2-3K | Tiered workflows, active |
| `PROJECT_CONTEXT.md` | 5.5K | Project context, active |
| `PROJECT_MAP.md` | 1.6K | Project map, active |
| `TOOL_REFERENCE.md` | 4.8K | Tool reference, active |
| `KNOWN_BUGS.md` | 31K | Active bug tracking |
| `requirements.txt` | 60B | Python deps |
| `rule_core.py` | 753B | Core rules |
| `path_runtime.py` | 1.3K | Path utilities |
| `mcp_client_manager.py` | 1.2K | MCP client manager |
| `council_send.js` | 2.6K | Council dispatch script |
| `a_call.js` | 3.9K | Agent call script |
| `install_orchestrator_admin_task.ps1` | 7K | Install script |
| `test_all_tools.py` | 20K | Active test utility |
| `tools_v15.py` | 363K | V15 tools (active, Jun 4) |
| `tool_test_results.json` | 158K | Test results (Jun 5, valuable reference) |

### `.council/` — Multi-agent council system (ACTIVE)
| File | Ghi chú |
|------|---------|
| `PROTOCOL.md` | Council protocol v1 (clau > codo > ope) |
| `council_rule_leader.md` | Leader agent rules (Jun 5) |
| `global_rule_worker.md` | Worker agent rules (Jun 5) |
| `broker_filtered.jsonl` | Live broker transcript (22 lines, Jun 10 22:03) |
| `council_events.jsonl` | Events log (22 lines, Jun 10) |
| `worker_models.json` | Worker model config (clin=xiaomi/mimo-v2.5, ope=deepseek-v4-flash-free) |
| `_p_comc.json` | Active council state |
| `context_registry.json` | Registry |
| `dispatch.js` | Dispatch logic |
| `guide_clinC.md` | Guide for clinC agent |
| `utf8_runner.bat` / `.ps1` | UTF-8 runners |
| `STATE.md` | Empty file — dùng làm pointer/state token |
| `inbox_*`, `outbox_*` (12 files) | IPC mailboxes (empty, active) |

### `.ToolA_Skill/` — Skill router system (ACTIVE)
| Subdirectory | Ghi chú |
|-------------|---------|
| `skills/00_–26_` | 23 skill folders, mỗi folder 1 SKILL.md — active router |
| `24_council_tr.md` | Council trigger procedure |
| `SKILLS.md` | Routing table (R43.3, 24/25 skills populated) |
| `README.md` | Skill system doc |
| `references/` | `core_rule_section_map.md` + `tool_surface_current.md` |
| `workflows/` | 4 active workflow files (MCP_175_TOOL_TEST_WORKFLOW.md, rawcut_full_r1.md) |
| `workflow_state/` | Active state tracking (6 state.json files) |
| `harness/` | 2 test harness files (R1_footage_test_EU_handoff.md, R1_footage_test_HU.md) |

### `.agents/skills/` — Resolved skill cache (ACTIVE)
- **13 skill subdirs**: brandkit, design-taste-frontend, design-taste-frontend-v1, full-output-enforcement, gpt-taste, high-end-visual-design, imagegen-frontend-mobile, imagegen-frontend-web, image-to-code, industrial-brutalist-ui, minimalist-ui, redesign-existing-projects, stitch-design-taste
- Mỗi skill có SKILL.md — được agent system dùng trực tiếp
- Nguồn: `skills-lock.json` map từ `Leonxlnx/taste-skill` GitHub

### `taste-skill/` — Source repo clone (ACTIVE)
- Standalone git clone của `https://github.com/leonxlnx/taste-skill.git`
- 13 skill directories trong `skills/`
- `CHANGELOG.md`, `README.md`, `LICENSE`, `skill.sh`
- `research/` — laziness research
- `examples/`
- Nguồn gốc cho `.agents/skills/`

### `scripts/` — Utility scripts (ACTIVE)
- `sync_tool_matrix.py` (10K) — tool matrix sync
- `mcp_api_parity_check.py` (2.5K) — API parity check
- `install_precommit.bat`, `precommit_check.bat`

### `tests/` — Test files (ACTIVE)
- 5 test `.py` files (test_orchestrator_server_contract.py 89K, test_code_tools_cache.py 38K, etc.)
- 7 output `.txt` files (Jun 1 — test run output logs)

### `venv312/` — Python virtual environment (ACTIVE)
- Python 3.12 venv cho orchestrator

### `.github/workflows/` — CI (ACTIVE)
- `tool-matrix-ci.yml` — GitHub Actions CI

---

## 🟡 STALE — Cũ nhưng từng có giá trị

### Root stale files
| File | Size | Ngày | Ghi chú |
|------|------|------|---------|
| `code_tools.py.bak` | 772K | Jun 4 | Backup before change |
| `orchestrator_server.py.bak` | 349K | Jun 8 | Backup before change |
| `search_http_helpers.txt` | 33K | May 14 | Old search dump |
| `search_result_toola.txt` | 11K | May 14 | Old search dump |
| `fix_coverage_182.md` | 8.8K | Jun 3 | Historical fix doc |
| `final_fix_coverage_182.md` | 8.8K | Jun 3 | Historical fix doc |
| `BENCHMARK_REQUEST.md` | 541B | May 6 | Old benchmark doc |
| `BUILD_CONTEXT.md` | 552B | May 6 | Old build doc |
| `verify_fix.py` | 1K | Apr 25 | Old verification script |
| `test_mcp.bat` | 337B | Apr 20 | Old MCP test |

### `examples/` — Historical reference copies (STALE)
| File | Ngày | Ghi chú |
|------|------|---------|
| `mcp_175_tool_rag.html` | Jun 4 | Stale HTML copy (690K) |
| `mcp_175_tool_rag - Copy.html` | Jun 3 | Duplicate (!) của file trên |
| `mcp_175_tool_rag.html.bak` | Jun 4 | Backup của copy |
| `mcp_175_tool_rag_verify.png` | Jun 3 | Stale screenshot (190K) |
| `CORE_RULE.txt` | Jun 6 | Old CORE_RULE |
| `CORE_RULE.txt.bak` | Jun 4 | Backup |
| `_extract.js` | Jun 7 | Extraction script (lỗi syntax) |
| `PROJECT_CONTEXT.md` | Jun 4 | Old context |
| `PROJECT_MAP.md` | Jun 4 | Old map |
| `reports/` | May 12 | 3 stale report files |
| `tooling/` | May 11-12 | 4 stale tooling docs |
| `api/write_fix_dry_run.ps1` | May 11 | Old API script |

### `release_gate_182/` — Historical release gate (STALE)
- `baseline_before_fix.json` — Jun 3 snapshot (38 tools baseline)
- `report_viewer.html` — Jun 3 report viewer skeleton

### `.ToolA_Skill/` stale files
- `24_council_tr.md.bak` — Backup
- `workflows/rawcut_full_r1.md.bak` (42K) — Backup
- `workflow_state/` 9x `.bak.*` state files

---

## 🗑️ RÁC — Runtime artifacts, backup, temp, duplicate

### Python caches
| Path | Size | Ghi chú |
|------|------|---------|
| `__pycache__/` | ~1.1MB | 5 `.pyc` files (code_tools, path_runtime, rule_core, surgical_mapper, tools_v15) |
| `.pytest_cache/` | ~540B | pytest cache |
| `.council/__pycache__/` | — | Council pycache |

### Runtime caches (`.orchestrator_cache/`)
| Path | Ghi chú |
|------|---------|
| `INDEX.json` (149K) | Auto-generated file index |
| `matrix_log.jsonl` (14K) | Matrix log |
| `sessions.db` (0B) | Empty session DB |
| `mutation_token.txt` | Temp token |
| `BACKUP_MANIFEST.json` | Auto-generated |
| `CA_SESSION_BOOTSTRAP.md` | Old session bootstrap |
| `council_page.html` (51K) | Cached page |
| `*.json` files (50+ files) | Per-file metadata caches (tự sinh, có thể tạo lại) |
| `screenshots/` | **192 PNG files (~40MB)** — desktop screenshots runtime |
| `read_response.ps1` | Temp script |
| `test_clinc_broker.ps1` | Temp script |
| `WORKFLOW_STATE.json` | Runtime workflow state |
| `examples/`, `release_gate_182/`, `scripts/`, `tests/`, `taste-skill/` subdirs | Nested `.orchestrator_cache/` với .json cache files |

### Nested `.orchestrator_cache/` copies (DUPLICATE RUNTIME CACHE)
- `examples/.orchestrator_cache/` — 5 files
- `examples/tooling/.orchestrator_cache/` — 3 files
- `examples/reports/.orchestrator_cache/` — 3 files

### Temp/trash files (Root)
| File | Ghi chú |
|------|---------|
| `_tmp_match.txt` | Temp file |
| `_tmp_rpl.py` | Temp file |
| `out.txt` (0B) | Empty output |
| `_p_comc.json` | Temp state |

### Empty directories
| Path | Ghi chú |
|------|---------|
| `.council/empty_hooks/` | Empty |
| `.ToolA_Skill/scripts/` | Empty |
| `.ToolA_Skill/assets/` | Empty |
| `.ToolA_Skill/skills/.orchestrator_cache.bak.R43.3.misplaced/` | Misplaced empty cache backup |

### Duplicates
| File | Of |
|------|----|
| `examples/mcp_175_tool_rag - Copy.html` | `examples/mcp_175_tool_rag.html` |
| `examples/CORE_RULE.txt.bak` | `examples/CORE_RULE.txt` |
| `examples/mcp_175_tool_rag.html.bak` | `examples/mcp_175_tool_rag.html` |

---

## 📊 Tổng quan

### Dung lượng ước tính
| Category | Ước tính |
|----------|-----------|
| **CỐT LÕI** | ~1.8 GB (gồm orchestrator.log 164M + venv312 ~1.5GB + code) |
| **STALE** | ~2.1 MB |
| **RÁC** | ~45 MB (chủ yếu screenshots 40MB + pycache 1.1MB + cache files ~4MB) |

### Đề xuất dọn dẹp
1. **Ưu tiên cao**: Xóa `.orchestrator_cache/screenshots/` (192 PNG, ~40MB)
2. **Ưu tiên cao**: Dọn `.orchestrator_cache/` cũ (trừ INDEX.json, matrix_log.jsonl nếu còn dùng)
3. **Nên xóa**: Các `.bak` files ở root (code_tools.py.bak, orchestrator_server.py.bak) nếu đã có git history
4. **Nên xóa**: `examples/mcp_175_tool_rag - Copy.html` (duplicate)
5. **Nên xóa**: Các `.bak.*` trong workflow_state/ (9 files)
6. **Có thể xóa**: `search_http_helpers.txt`, `search_result_toola.txt`, `verify_fix.py`, `test_mcp.bat`
7. **Có thể dọn**: `examples/` directory nếu không cần tham khảo lịch sử
8. **Thận trọng**: `taste-skill/` là standalone clone (không phải submodule) — có thể thay bằng submodule nếu cần quản lý version

### Lưu ý đặc biệt
- **`.agents/skills/` vs `taste-skill/skills/`**: Nội dung tương tự nhau. `.agents/skills/` là resolved local cache agent dùng; `taste-skill/` là source repo. Cả 2 đều cần cho hệ thống hiện tại.
- **orchestrator.log** (164M) — đang live append. Cần log rotation.
- **Không có `.git` ở root** — project không được version control. Nên init git repo.
