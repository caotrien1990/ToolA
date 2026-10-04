// Read-gate for Claude Code. One file, two hook events:
//
// PreToolUse (Read/Grep/Glob/Bash): force coding agents onto the [A] index
// workflow (cbm_* graph, ocr_map, find_symbol, search_code, read_file_range
// bounded) instead of blind native reading. Decides LOCALLY -- no HTTP to [A]:
// reads must not depend on [A] being up, and a PreToolUse read hook runs on
// every Read/Grep/Bash, so zero network keeps latency out of the critical path.
//
// FAIL OPEN, unlike the write gate (fail closed): a broken READ gate bricks
// every Read/Grep/Bash the CA owns, including the reads needed to fix the gate
// itself, and a wrongly-allowed read costs tokens, not data -- a read mutates
// nothing. A wrongly-allowed WRITE can corrupt a project; a wrongly-allowed
// read cannot. So any exception here -> allow (exit 0).
//
// UserPromptSubmit: Anh-only break-glass `tắt read gate` / `bật read gate`,
// Vietnamese diacritic-insensitive, whole-prompt match like the write gate.
// State file and off-log are refused to native write tools? No -- that
// protection lives in the WRITE gate already (write gate covers Write/Edit);
// this hook only denies its own state file against Read? No: reads of the
// state file are harmless. The state/off-log protection stays with the write
// gate; this gate protects READS only.
'use strict';

const fs = require('fs');
const path = require('path');

// NATIVE_READ_GATE_DIR is a test seam: a probe must not flip the real state.
const STATE_DIR = process.env.NATIVE_READ_GATE_DIR || __dirname;
const STATE_FILE = path.join(STATE_DIR, 'read-gate.state.json');
const OFF_LOG = path.join(STATE_DIR, 'read-gate.offlog.jsonl');
const KEYWORD_OFF = 'tat read gate';
const KEYWORD_ON = 'bat read gate';

const CODE_EXTS = new Set([
  '.py', '.pyi', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.vue', '.svelte',
  '.ps1', '.psm1', '.go', '.rs', '.java', '.kt', '.cs', '.cpp', '.cc', '.c',
  '.h', '.hpp', '.rb', '.php', '.swift', '.lua', '.sh', '.bat', '.cmd', '.sql',
]);
const NONCODE_EXTS = new Set(['.md', '.txt', '.log', '.json', '.jsonl', '.yaml', '.yml', '.toml', '.csv', '.html']);
const MAX_LINES = 200;
const SMALL_BYTES = 12 * 1024; // size <= 12KB fast path: a code file this small is <=200 lines in practice

const IDENT = '[A-Za-z_][A-Za-z0-9_]{2,}';
const SYMBOL_RE = new RegExp(
  '^(?:' +
  IDENT + '|' +                                   // bare identifier
  '(?:def|class|function)\\s+' + IDENT + '|' +    // def X / class X / function X
  IDENT + '\\(|' +                                // X(
  '\\\\b' + IDENT + '\\\\b' +                     // \bX\b
  ')$'
);
// A pattern with regex alternation/anchors/classes is NOT symbol-shaped.
// `\\|`/`\\(`/`\\)` (escaped BRE alternation/group) count too.
const REGEX_META_RE = /[|^\[\]$]|\\[|()]/;

const BARE_IDENT_RE = new RegExp('^' + IDENT + '$');

// TEMP can carry 8.3 short names (e.g. C:\\Users\\ADMIN~1\\...) on some boxes;
// resolve through the long form so prefix matching is stable.
let TEMP_DIR = (process.env.TEMP || 'C:\\Users\\ADMIN\\AppData\\Local\\Temp').replace(/[\\/]+$/, '');
try { TEMP_DIR = fs.realpathSync(TEMP_DIR); } catch (e) { /* keep as-is */ }
const EXEMPT_PREFIXES = [
  TEMP_DIR.toLowerCase(),
];
// Harness config dirs directly under the user profile are exempt by dir-NAME
// glob, not by prefix: `.claude`, `.claude-*`, `.codex`, `.codex-*`. A prefix
// match (`pre + '\\'`) misses `.claude-ltnai` because the next char is '-'.
const HARNESS_DIR_RE = /^\.(claude|codex)(-.*)?$/;
// NATIVE_READ_GATE_NO_TEMP_EXEMPT: test seam -- %TEMP%\p20 fixtures must be
// gateable even though %TEMP% is normally exempt.
const TEMP_EXEMPT_OFF = !!process.env.NATIVE_READ_GATE_NO_TEMP_EXEMPT;

function emit(decision, reason) {
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: decision,
      permissionDecisionReason: reason.slice(0, 1200),
    },
  }));
  process.exit(0);
}

// Missing or unreadable state = ON. Only an explicit enabled:false opens the gate.
function readState() {
  try {
    const s = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8').replace(/^﻿/, ''));
    return s && s.enabled === false ? s : { enabled: true };
  } catch (e) {
    return { enabled: true };
  }
}

function appendOffLog(entry) {
  fs.appendFileSync(OFF_LOG, JSON.stringify({ ts: new Date().toISOString(), ...entry }) + '\n', 'utf8');
}

function foldPrompt(text) {
  return String(text || '')
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[đĐ]/g, 'd')
    .toLowerCase().replace(/\s+/g, ' ').trim()
    .replace(/[.!\s]+$/, '');
}

function normPath(p) {
  return String(p || '').replace(/\//g, '\\').replace(/\\+$/, '');
}

// Git-Bash style paths (/d/foo, /c/Users/...) -> Windows (D:\foo, C:\Users\...).
function fromMsys(p) {
  const s = String(p || '');
  const m = s.match(/^\/([A-Za-z])\/(.*)$/);
  if (m) return m[1].toUpperCase() + ':\\' + m[2].replace(/\//g, '\\');
  return s;
}

// Normalise for equality/cache-key purposes: MSYS form, slashes, case, and the
// 8.3 long-form all collapse to one key.
function canonKey(abs) {
  return normPath(longForm(fromMsys(abs))).toLowerCase();
}

function longForm(p) {
  try { return fs.realpathSync(p); } catch (e) { return p; }
}

function isExemptPath(abs) {
  const low = normPath(longForm(fromMsys(abs))).toLowerCase();
  for (const pre of EXEMPT_PREFIXES) {
    if (TEMP_EXEMPT_OFF && pre === TEMP_DIR.toLowerCase()) continue;
    if (low === pre || low.startsWith(pre + '\\')) return true;
  }
  // Harness config dir directly under the user profile: .claude / .claude-* /
  // .codex / .codex-* . The path may be MSYS-formed (/c/Users/...) at call
  // sites that skipped fromMsys, so normalise through fromMsys first. The
  // profile key goes through canonKey so an 8.3 USERPROFILE matches.
  if (HARNESS_PROFILE_KEY === null) {
    const up = (process.env.USERPROFILE || 'C:\\Users\\ADMIN').replace(/[\\/]+$/, '');
    try { HARNESS_PROFILE_KEY = canonKey(up); } catch (e) { HARNESS_PROFILE_KEY = normPath(up).toLowerCase(); }
  }
  if (low === HARNESS_PROFILE_KEY) return false; // profile root itself is not exempt
  if (low.startsWith(HARNESS_PROFILE_KEY + '\\')) {
    const firstSeg = low.slice(HARNESS_PROFILE_KEY.length + 1).split('\\')[0];
    if (HARNESS_DIR_RE.test(firstSeg)) return true;
  }
  return false;
}
let HARNESS_PROFILE_KEY = null;

// A marker dir only marks the tree that CONTAINS it: the marker path itself,
// or anything inside it (a worktree's .git FILE, a cache dir's contents) is
// not a project root. The walk stops before entering a marker dir.
function hasMarker(dir) {
  try {
    return fs.existsSync(path.join(dir, '.git')) || fs.existsSync(path.join(dir, '.orchestrator_cache'));
  } catch (e) {
    return false;
  }
}
function isUnderMarker(dir) {
  const low = normPath(longForm(dir)).toLowerCase();
  const base = path.basename(low);
  if (base === '.git' || base === '.orchestrator_cache') return true;
  return low.includes('\\.git\\') || low.includes('\\.orchestrator_cache\\');
}
// The .orchestrator_cache tree holds daemon-written LOGS (USAGE_LOG.jsonl,
// index snapshots) -- a grep over those is log inspection, not code reading.
// `...\.orchestrator_cache` anywhere under the target exempts the target.
function isCacheContent(dir) {
  const low = normPath(longForm(dir)).toLowerCase();
  return low.includes('\\.orchestrator_cache');
}
// Bare-token shape guard: $VAR / ${VAR} / $(subshell) are not literal paths,
// and a bare identifier token that is not an existing path is almost always a
// shell variable or a pipe-word (ok/cut/head...) that landed in the operand
// slot of a dangling-quoted pattern. Wildcards and drive/relative path
// characters stay path-like.
const IDENT_ONLY_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
function isPathLike(tok) {
  if (!tok) return false;
  if (/\$\{|\$\(|\$[A-Za-z_]/.test(tok)) return false;
  if (IDENT_ONLY_RE.test(tok) && !/[*?[\]]/.test(tok)) return false;
  return true;
}
// The user profile is NEVER a project root: a tool config dir at its top
// (~/.git, ~/.claude, ~/.codex) must not gate every stray file on the machine.
const USER_PROFILE = (process.env.USERPROFILE || 'C:\\Users\\ADMIN').replace(/[\\/]+$/, '');
let USER_PROFILE_KEY = null;
function isProfileRoot(dir) {
  if (USER_PROFILE_KEY === null) {
    try { USER_PROFILE_KEY = canonKey(USER_PROFILE); } catch (e) { USER_PROFILE_KEY = normPath(USER_PROFILE).toLowerCase(); }
  }
  return canonKey(dir) === USER_PROFILE_KEY;
}

// "In a project" = an ancestor dir has .git or .orchestrator_cache.
// Cache per directory walk so 200 calls on one tree stay fast.
const rootCache = new Map();
function findProjectRoot(absFileOrDir) {
  let dir = normPath(absFileOrDir);
  try { if (fs.existsSync(dir) && fs.statSync(dir).isFile()) dir = path.dirname(dir); } catch (e) { /* keep */ }
  if (rootCache.has(dir.toLowerCase())) return rootCache.get(dir.toLowerCase());
  const start = dir;
  if (isUnderMarker(start)) { rootCache.set(start.toLowerCase(), null); return null; }
  let root = null;
  const rootOf = path.parse(dir).root;
  while (true) {
    if (rootCache.has(dir.toLowerCase())) { root = rootCache.get(dir.toLowerCase()); break; }
    if (hasMarker(dir) && !isProfileRoot(dir)) { root = dir; break; }
    if (dir === rootOf || dir.length <= rootOf.length) break;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  // Cache the OUTCOME for the queried path only; intermediate dirs are cheap
  // to re-walk and caching them all would smear a found root onto siblings.
  rootCache.set(start.toLowerCase(), root);
  return root;
}

function isCodeFile(p) {
  return CODE_EXTS.has(path.extname(String(p)).toLowerCase());
}

function nonCodeOnly(p) {
  return NONCODE_EXTS.has(path.extname(String(p)).toLowerCase());
}

// Cheap line count: size fast path first, else count newlines.
function lineCount(file) {
  const st = fs.statSync(file);
  if (st.size <= SMALL_BYTES) return 0; // small enough to be under the limit
  const buf = fs.readFileSync(file);
  let n = 1;
  for (let i = 0; i < buf.length; i++) if (buf[i] === 10) n++;
  return n;
}

function denyReason(rule, root, extra) {
  return `[read-gate] ${rule}: ${extra} Project root: ${root}`.slice(0, 600);
}

function r1Reason(root) {
  return denyReason('R1', root,
    `Large code file read blocked. Use the index: POST http://localhost:8083/api/call/find_symbol ` +
    `{project_path:'${root}', name:'<symbol>'} or cbm_get_architecture / cbm_nodes_query ` +
    `{project_path:'${root}', name_prefix:'...'}; then read_file_range {project_path:'${root}', file_path, start_line, end_line} ` +
    `(<=200 lines) or Read with offset/limit<=200. Anh-only break-glass: \`tắt read gate\`.`);
}

function r2Reason(root) {
  return denyReason('R2', root,
    `Symbol-shaped grep blocked. Use the index: find_symbol {project_path:'${root}', name:'<symbol>'} (definition); ` +
    `search_code {project_path:'${root}', mode:'callers', query:'<symbol>'} or cbm_nodes_query -> cbm_trace_path ` +
    `{direction:'in'} (who calls); search_code {project_path:'${root}', query:'...'} (text). ` +
    `Anh-only break-glass: \`tắt read gate\`.`);
}

function r3Reason(root) {
  return denyReason('R3', root,
    `Shell read/search of project code blocked. Use the index: find_symbol / search_code {project_path:'${root}'} for symbols, ` +
    `read_file_range (<=200 lines) for code reads; for diffs ocr_map {project_path:'${root}'} names the files that carry ` +
    `the change. Anh-only break-glass: \`tắt read gate\`.`);
}

// ---------- R2: Grep ----------
function checkGrep(ti, cwd) {
  const pattern = String(ti.pattern || '');
  if (!pattern) return null;
  if (REGEX_META_RE.test(pattern)) return null;
  if (!SYMBOL_RE.test(pattern)) return null;
  // Restricted to non-code? glob/type naming md/txt/log/json/yaml/toml/csv/html -> allow.
  const g = String(ti.glob || '') + ' ' + String(ti.type || '');
  if (/md|txt|log|json|ya?ml|toml|csv|html/i.test(g)) return null;
  // Determine the search target dir.
  let target = fromMsys(String(ti.path || cwd || ''));
  if (!target) return null;
  if (!path.isAbsolute(target) && cwd) target = path.resolve(String(cwd), target);
  if (isExemptPath(target)) return null;
  if (isCacheContent(target)) return null;
  if (ti.path) {
    // Explicit target: an existing FILE must be code by extension (FP2:
    // .githooks/pre-commit, *.md, *.log are not code); a directory or a
    // missing path stays gated as a code-wide search.
    let st = null;
    try { st = fs.statSync(target); } catch (e) { /* missing */ }
    if (st && st.isFile()) {
      if (!isCodeFile(target)) return null;
    } else if (!st && nonCodeOnly(target)) {
      return null;
    }
  }
  const root = findProjectRoot(target);
  if (!root) return null;
  return r2Reason(root);
}

// ---------- R1: Read ----------
function checkRead(ti, cwd) {
  let target = fromMsys(String(ti.file_path || '').trim());
  if (!target) return null;
  if (!path.isAbsolute(target) && cwd) target = path.resolve(String(cwd), target);
  if (isExemptPath(target)) return null;
  if (!isCodeFile(target)) return null;
  const root = findProjectRoot(target);
  if (!root) return null;
  // limit<=200 passes regardless of file size.
  const lim = Number(ti.limit);
  if (Number.isFinite(lim) && lim > 0 && lim <= MAX_LINES) return null;
  let lines;
  try {
    lines = lineCount(target);
  } catch (e) {
    return null; // unreadable/missing file: the Read tool itself reports that better than we can
  }
  if (lines === 0) return null; // size fast path
  if (lines <= MAX_LINES) return null;
  return r1Reason(root);
}

// ---------- R3: Bash ----------
function extractShellPaths(cmd) {
  // Split conservatively: quoted strings and bare tokens.
  const tokens = [];
  const re = /"([^"]+)"|'([^']+)'|([^\s|&;<>]+)/g;
  let m;
  while ((m = re.exec(cmd)) !== null) tokens.push(m[1] ?? m[2] ?? m[3]);
  return tokens;
}

function looksSymbolShapedPattern(tok) {
  if (!tok) return false;
  if (REGEX_META_RE.test(tok)) return false; // alternation/anchor/class -> not symbol-shaped
  return SYMBOL_RE.test(tok);
}

// Remove heredoc bodies before any statement parsing: everything between a
// `<<'TAG'` / `<<TAG` / `<<-TAG` marker and the line holding only TAG is
// script TEXT being written, not commands to parse (FP1: `cat > f.py <<'EOF'`
// is a write; a `python - <<'EOF'` body is program text).
function stripHeredocs(cmd) {
  const lines = String(cmd).split('\n');
  const out = [];
  let tag = null;
  for (const line of lines) {
    if (tag !== null) {
      if (line.trim() === tag) tag = null;
      continue; // body line: dropped
    }
    const m = line.match(/<<-?\s*'?"?([A-Za-z_][A-Za-z0-9_]*)'?"?/);
    if (m) {
      tag = m[1];
      out.push(line.slice(0, m.index)); // keep the command before `<<`
      continue;
    }
    out.push(line);
  }
  return out.join('\n');
}

// Quote-aware tokeniser with shell-like dangling-quote recovery: a pattern
// written `"A\|B"` (backslash-escaped inside a double-quoted span) closes at
// the escaped quote, so if the span ends with a lone word (no whitespace
// after the closing quote) re-scan treating that quote as literal text.
function shellTokens(s) {
  const toks = [];
  let i = 0;
  const n = s.length;
  while (i < n) {
    while (i < n && /\s/.test(s[i])) i++;
    if (i >= n) break;
    let tok = '';
    while (i < n && !/\s/.test(s[i])) {
      const ch = s[i];
      if (ch === '"' || ch === "'") {
        const q = ch;
        const start = i;
        i++;
        while (i < n && s[i] !== q) i++;
        if (i < n) {
          const content = s.slice(start + 1, i);
          // clean close: whitespace/end follows -> quoted token content
          if (i + 1 >= n || /\s/.test(s[i + 1])) { tok += content; i++; continue; }
          // suspicious close (escaped quote inside a quoted span) -> literal
          tok += s.slice(start, i + 1);
          i++;
          continue;
        }
        tok += s.slice(start); // unterminated -> literal
        break;
      }
      tok += ch;
      i++;
    }
    toks.push(tok);
  }
  return toks;
}

// Split a compound shell line on ; && || | (not inside quotes, coarsely).
function shellStatements(cmd) {
  const parts = [];
  let cur = '', q = null;
  for (let i = 0; i < cmd.length; i++) {
    const ch = cmd[i];
    if (q) { cur += ch; if (ch === q) q = null; continue; }
    if (ch === '"' || ch === "'") { q = ch; cur += ch; continue; }
    if (ch === '\n' || ch === ';' || (ch === '&' && cmd[i + 1] === '&') || (ch === '|' && cmd[i + 1] === '|')) {
      if (ch !== '\n' && ch !== ';') i++;
      if (cur.trim()) parts.push(cur);
      cur = '';
      continue;
    }
    cur += ch;
  }
  if (cur.trim()) parts.push(cur);
  return parts;
}

function checkBash(ti, cwd) {
  const raw = String(ti.command || '');
  if (!raw) return null;
  const cmd = stripHeredocs(raw); // heredoc bodies are script text, not commands
  const cwdRoot = cwd ? findProjectRoot(path.resolve(String(cwd))) : null;

  // Shape A: cat|type|Get-Content|gc of a single code file >200 lines without
  // limits. One COMMAND only: a pipeline stage `| cat -n` is a pager, and a
  // later command in a compound line is out of scope for an earlier one.
  // `cat > f` / `cat >> f` is a WRITE, never a read (FP1).
  const readCmd = /^\s*(cat|type|Get-Content|\bgc\b)\s+/i;
  for (const stmt of shellStatements(cmd)) {
    const m = stmt.match(readCmd);
    if (!m) continue;
    if (/>>?/.test(stmt)) continue;      // output redirect -> a write/copy, not a read
    if (/-TotalCount\b|-First\b/i.test(stmt)) continue; // bounded read
    const tokens = extractShellPaths(stmt.slice(m[0].length));
    // find first path-like token that exists
    for (const tok of tokens) {
      if (/^-/.test(tok)) continue;      // `cat -n` pager flags
      if (/^\d+$/.test(tok)) continue;
      if (!isPathLike(tok)) continue;    // $VAR / $(..) / bare words are not paths
      let p = fromMsys(tok);
      if (!path.isAbsolute(p) && cwd) p = path.resolve(String(cwd), p);
      let exists = false;
      try { exists = fs.existsSync(p) && fs.statSync(p).isFile(); } catch (e) { /* no */ }
      if (!exists) continue;
      if (isExemptPath(p) || !isCodeFile(p)) break;
      const root = findProjectRoot(p);
      if (!root) break;
      let lines;
      try { lines = lineCount(p); } catch (e) { break; }
      if (lines === 0 || lines <= MAX_LINES) break;
      return r3Reason(root);
    }
    // no concrete existing file in this command -> keep scanning the others
  }

  // Shape B: rg|grep|git grep|findstr|Select-String with a symbol-shaped
  // pattern, not restricted to non-code. Only the FIRST stage of a pipeline
  // is a real search; a later `| grep X` filters the producer's output -> allow.
  const searchCmd = /^\s*(rg|grep|git\s+grep|findstr|Select-String)\b/i;
  // `cd "X" &&` earlier in a compound line changes the cwd for every later
  // segment in that same line, so track it across the loop.
  let lineCwd = cwd;
  for (const stmtFull of shellStatements(cmd)) {
    const cdMatch = stmtFull.match(/(?:^|&&|\|\||;)\s*cd\s+("[^"]+"|'[^']+'|\S+)/i);
    if (cdMatch) {
      let cdT = fromMsys(cdMatch[1].replace(/^["']|["']$/g, '').replace(/\\+$/, ''));
      if (!path.isAbsolute(cdT) && lineCwd) cdT = path.resolve(String(lineCwd), cdT);
      lineCwd = cdT;
    }
    const effCwd = lineCwd;
    const effRoot = effCwd ? findProjectRoot(path.resolve(String(effCwd))) : null;
    const stages = stmtFull.split(/\|(?!\|)/).map(s => s.trim()).filter(Boolean);
    for (let si = 0; si < stages.length; si++) {
      const stmt = stages[si];
      const m = stmt.match(searchCmd);
      if (!m) continue;
      if (si > 0) {
        // Piped grep: gate only when the producer is itself a file search/read;
        // producers like ls/env/ps/git ls-files/tail of a log are list filters.
        const producer = stages[si - 1];
        if (!/^\s*(rg|grep|cat|type|Get-Content|git\s+grep|findstr|Select-String)\b/i.test(producer)) continue;
        // But a producer that reads ONE explicit non-code or exempt file is a
        // bounded log/file read, and this stage only filters its matches:
        // `grep -o X "/c/.../.claude-ltnai/t.jsonl" | grep -i Y` is log
        // inspection, not a project-wide code search -> allow.
        const pm = producer.match(/^\s*(rg|grep|cat|type|Get-Content|git\s+grep|findstr|Select-String)\b/i);
        if (pm) {
          const pOps = shellTokens(producer.slice(pm[0].length).replace(/^(?:\s+--?\S+)+/, ''));
          // operands after the pattern token; cat/type/Get-Content have none.
          const takesPattern = /grep|findstr|Select-String|^rg$/i.test(pm[1]);
          const pTarget = pOps.slice(takesPattern ? 1 : 0).find(tok => isPathLike(tok));
          if (pTarget) {
            let pt = fromMsys(pTarget);
            if (!path.isAbsolute(pt) && effCwd) pt = path.resolve(String(effCwd), pt);
            if (isExemptPath(pt) || isCacheContent(pt) || nonCodeOnly(pt)) continue;
          }
        }
      }
    if (/--(type|glob)\s+[=]?\S*(md|txt|log|json|ya?ml|toml|csv|html)\S*/i.test(stmt)) continue;
    if (/\*\.(md|txt|log|json|ya?ml|toml|csv|html)\b/i.test(stmt)) continue;
    if (/\s-t\s+(md|txt|log|json|yaml|toml|csv|html)\b/i.test(stmt)) continue;
    // Read the RAW text after the search command, not the token stream: the
    // tokeniser strips quotes, so `"A\|B"` would arrive as a bare `A\|B` and
    // look like alternation handled -- checking the raw text keeps `\|`
    // visible and out of the symbol class.
    const rawAfter = stmt.slice(m[0].length);
    // strip leading flags (-n, -E, --include=..., -A3 ...)
    const flagStrip = rawAfter.replace(/^(?:\s+--?\S+)+/, '');
    // Parse pattern + optional path operand with a quote-aware scanner so a
    // quoted path with spaces ("C:\My Project\x.py") stays one token.
    const ops = shellTokens(flagStrip);
    const unq = ops[0] || '';
    let sawPath = ops[1] || null;
    if (sawPath && (/^--?/.test(sawPath) || !isPathLike(sawPath))) sawPath = null;
    if (REGEX_META_RE.test(unq)) continue;
    if (!looksSymbolShapedPattern(unq)) continue;
    let target = sawPath ? fromMsys(sawPath) : null;
    if (target) {
      if (!path.isAbsolute(target) && effCwd) target = path.resolve(String(effCwd), target);
    } else {
      target = effCwd ? path.resolve(String(effCwd)) : null;
    }
    if (!target) continue;
    if (isExemptPath(target)) continue;
    if (isCacheContent(target)) continue; // .orchestrator_cache holds daemon logs
    if (sawPath) {
      // Explicit target: only a LARGE CODE file stays denied. A named file
      // that is extensionless / non-code (FP2: .githooks/pre-commit, *.md,
      // *.log) or <= 200 lines (FP3: grep|head on one small file is a bounded
      // look) -> allow. A directory or repo-wide target is code-wide -> deny.
      // The non-code-extension guard fires by NAME first (FP-new: a stale or
      // exempt .jsonl log under ~/.claude-ltnai may not stat) -- an explicit
      // single-file target with a non-code extension is never R3.
      if (nonCodeOnly(target)) continue;
      let st = null;
      try { st = fs.statSync(target); } catch (e) { /* missing */ }
      if (st && st.isFile()) {
        if (!isCodeFile(target)) continue;
        let lines;
        try { lines = lineCount(target); } catch (e) { continue; }
        if (lines === 0 || lines <= MAX_LINES) continue;
        const rootF = findProjectRoot(target);
        if (!rootF) continue;
        return r3Reason(rootF);
      }
    }
    // A path operand that no longer exists (stale transcript, deleted file)
    // cannot be confirmed to be in a project: fall back to the effective cwd.
    let targetForRoot = target;
    if (sawPath) {
      let exists = false;
      try { exists = fs.existsSync(target); } catch (e) { /* no */ }
      if (!exists && effCwd) targetForRoot = path.resolve(String(effCwd));
    }
    const root = findProjectRoot(targetForRoot) || effRoot;
    if (!root) continue;
    return r3Reason(root);
    }
  }

  // Everything else in Bash -> allow (git status/log/show/diff, builds, tests...).
  return null;
}

function onPreToolUse(input) {
  const tool = String(input.tool_name || '');
  const ti = input.tool_input || {};
  const cwd = input.cwd ? String(input.cwd) : null;

  if (tool === 'Glob') process.exit(0); // R4: cheap listing, always allow
  if (!['Read', 'Grep', 'Bash'].includes(tool)) process.exit(0);

  if (!readState().enabled) {
    try {
      appendOffLog({ event: 'native_read_while_off', tool, target: ti.file_path || ti.pattern || (ti.command || '').slice(0, 120), session_id: input.session_id || null });
    } catch (e) { /* audit best-effort */ }
    process.exit(0);
  }

  let reason = null;
  if (tool === 'Read') reason = checkRead(ti, cwd);
  else if (tool === 'Grep') reason = checkGrep(ti, cwd);
  else if (tool === 'Bash') reason = checkBash(ti, cwd);

  if (reason) return emit('deny', reason);
  process.exit(0);
}

function onUserPrompt(input) {
  const said = foldPrompt(input.prompt);
  if (said === KEYWORD_OFF || said === KEYWORD_ON) {
    const enabled = said === KEYWORD_ON;
    const at = new Date().toISOString();
    try {
      fs.writeFileSync(STATE_FILE, JSON.stringify({ enabled, changed_at: at, session_id: input.session_id || null }, null, 1), 'utf8');
      appendOffLog({ event: enabled ? 'read_gate_on' : 'read_gate_off', session_id: input.session_id || null });
    } catch (e) {
      process.stdout.write(`[read-gate] toggle FAILED, the gate is unchanged: ${e.message}`);
      return;
    }
    process.stdout.write(enabled
      ? '[read-gate] ON. Large/symbol-shaped native reads in a project are refused again; use the [A] index workflow.'
      : `[read-gate] OFF by Anh (break-glass). Native reads pass unchecked and each one is appended to ${OFF_LOG}. ` +
        '`bật read gate` turns it back on.');
    return;
  }
  const state = readState();
  if (!state.enabled) {
    process.stdout.write(`[read-gate] OFF since ${state.changed_at || 'unknown'} (break-glass). \`bật read gate\` turns it back on.`);
  }
}

// settings.json registers the UserPromptSubmit side with `--prompt`.
function main() {
  const promptEvent = process.argv.includes('--prompt');
  let input;
  try {
    const raw = fs.readFileSync(0, 'utf8').replace(/^﻿/, '');
    input = JSON.parse(raw || '{}');
  } catch (e) {
    // FAIL OPEN for reads: malformed input must not brick the CA.
    if (promptEvent) process.stdout.write('[read-gate] prompt hook input was not valid JSON; the gate is unchanged.');
    return process.exit(0);
  }
  if (promptEvent) {
    try { onUserPrompt(input); } catch (e) { process.stdout.write(`[read-gate] ${e.message}`); }
    return process.exit(0);
  }
  // FAIL OPEN: any crash -> allow. A broken read gate must not brick the CA;
  // unlike the write gate, a wrongly-allowed read mutates nothing (see header).
  try {
    onPreToolUse(input);
  } catch (e) {
    process.exit(0);
  }
}

main();
