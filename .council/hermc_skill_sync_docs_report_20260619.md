# HermC Skill Sync Docs Research Report
Date: 2026-06-19
Agent: HermC (deepseek-v4-flash-free)
Task: Research skill/instruction load location and CLI invocation for CA/tools on Windows

## Summary Table

| Agent/Tool | Supports SKILL.md folder? | Correct skill/instruction path on Windows | Visible CLI invocation pattern | Evidence URL/path | Confidence | Sync recommendation |
|------------|---------------------------|-------------------------------------------|--------------------------------|-------------------|------------|---------------------|
| Claude Code | Yes (via CLAUDE.md convention) | `C:\Users\ADMIN\CLAUDE.md` (project root) or `.claude/` directory | `claude` in terminal (from project directory) | `C:\Users\ADMIN\addyosmani-agent-skills\CLAUDE.md` shows SKILL.md convention | Medium | sync_rule only (CLAUDE.md) |
| Claude Desktop | Unknown | N/A (GUI app, not CLI) | N/A (desktop app) | `C:\Users\ADMIN\AppData\Roaming\Claude\claude_desktop_config.json` | Low | not_verified |
| Codex Desktop/CLI | Unknown | N/A (CLI uses ChatGPT login) | `codex` in terminal | `C:\Users\ADMIN\AppData\Roaming\npm\node_modules\@openai\codex\README.md` | Medium | not_verified |
| CommandCode | Yes (has skills/ in package) | `C:\Users\ADMIN\AppData\Roaming\npm\node_modules\command-code\skills\` (built-in) | `commandcode` or `cmdc` in terminal | `C:\Users\ADMIN\AppData\Roaming\npm\node_modules\command-code\package.json` (files: ["skills"]) | High | sync_skill folder |
| Hermes | Yes (has skills directory) | `C:\Users\ADMIN\AppData\Local\hermes\skills\` | `hermes` in terminal | `C:\Users\ADMIN\AppData\Local\hermes\skills\` directory structure | High | sync_skill folder |
| OpenCode | Unknown | N/A (CLI uses API key) | `opencode` in terminal | `C:\Users\ADMIN\AppData\Roaming\npm\node_modules\opencode-ai\` | Low | not_verified |
| Cline | Yes (has Rules directory) | `C:\Users\ADMIN\Documents\Cline\Rules\` | N/A (VS Code extension) | `C:\Users\ADMIN\Documents\Cline\Rules\00-orchestrator.md` | Medium | sync_skill folder |
| VS Code Copilot | Unknown | N/A (VS Code extension) | N/A (extension) | VS Code extension marketplace | Low | not_verified |

## Detailed Findings

### 1. Claude Code
- **Skill loading**: Uses CLAUDE.md files at project root or .claude/ directory
- **Evidence**: `C:\Users\ADMIN\addyosmani-agent-skills\CLAUDE.md` shows SKILL.md convention with skills/ directory
- **CLI**: `claude` command in terminal from project directory
- **Confidence**: Medium (based on community project structure, not official docs)
- **Recommendation**: sync_rule only - write to CLAUDE.md at project root

### 2. Claude Desktop
- **Skill loading**: Unknown (GUI app, config at AppData\Roaming\Claude\claude_desktop_config.json)
- **CLI**: Not applicable (desktop app)
- **Confidence**: Low (no evidence of skill loading mechanism)
- **Recommendation**: not_verified

### 3. Codex Desktop/CLI
- **Skill loading**: Unknown (CLI uses ChatGPT login, no local skill mechanism visible)
- **CLI**: `codex` command in terminal
- **Evidence**: README shows installation via npm or curl, no skill directory mentioned
- **Confidence**: Medium (CLI exists, but skill mechanism unclear)
- **Recommendation**: not_verified

### 4. CommandCode
- **Skill loading**: Has built-in skills/ directory in npm package
- **Evidence**: package.json shows `"files": ["dist", "vsix", "skills"]`
- **CLI**: `commandcode` or `cmdc` command in terminal
- **Confidence**: High (direct evidence from package structure)
- **Recommendation**: sync_skill folder - copy to package's skills/ directory

### 5. Hermes
- **Skill loading**: Has dedicated skills directory at AppData\Local\hermes\skills\
- **Evidence**: Directory contains SKILL.md files in subdirectories (council-agent-operation, taaq-protocol, etc.)
- **CLI**: `hermes` command in terminal
- **Confidence**: High (direct observation of skill structure)
- **Recommendation**: sync_skill folder - copy to hermes\skills\ directory

### 6. OpenCode
- **Skill loading**: Unknown (CLI uses API key, no skill directory visible)
- **CLI**: `opencode` command in terminal
- **Evidence**: npm package exists, but no skill mechanism in package structure
- **Confidence**: Low (no evidence of skill loading)
- **Recommendation**: not_verified

### 7. Cline
- **Skill loading**: Has Rules directory at Documents\Cline\Rules\
- **Evidence**: `C:\Users\ADMIN\Documents\Cline\Rules\00-orchestrator.md` exists
- **CLI**: Not applicable (VS Code extension)
- **Confidence**: Medium (directory exists, but mechanism unclear)
- **Recommendation**: sync_skill folder - copy to Cline\Rules\ directory

### 8. VS Code Copilot
- **Skill loading**: Unknown (VS Code extension, no local skill mechanism visible)
- **CLI**: Not applicable (extension)
- **Confidence**: Low (no evidence of skill loading)
- **Recommendation**: not_verified

## Strongest Findings

1. **Hermes** has clear skill directory structure at `C:\Users\ADMIN\AppData\Local\hermes\skills\` with SKILL.md files
2. **CommandCode** includes skills/ in its npm package distribution
3. **Claude Code** uses CLAUDE.md convention (evidenced by community projects)
4. **Cline** has Rules directory at `C:\Users\ADMIN\Documents\Cline\Rules\`

## Recommendations by Priority

1. **High confidence sync**: Hermes, CommandCode - sync_skill folder
2. **Medium confidence**: Claude Code (CLAUDE.md), Cline (Rules/) - sync_rule or sync_skill
3. **Low confidence/unknown**: Claude Desktop, Codex, OpenCode, VS Code Copilot - not_verified

Report path: D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council\hermc_skill_sync_docs_report_20260619.md

END_HERMC_SKILL_SYNC_DOCS_R1