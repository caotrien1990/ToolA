// Hermes read gate (shell-hook adapter) -- the Hermes WIRE for the R1/R2 read policy
// whose SOT is hooks/read-gate.js. It holds NO policy copy: it translates a Hermes
// pre_tool_call payload for the two declared reader tools into the Claude shape,
// spawns the co-installed read-gate.js, and relays THAT verdict in the dialect
// Hermes actually reads. One policy, two hosts, so R1/R2 cannot drift between them.
//
// Why a translator instead of shipping hooks/read-gate.js to Hermes (all measured
// 2026-11-23 in C:/Users/ADMIN/AppData/Local/hermes/hermes-agent):
//   OUTPUT DIALECT. agent/shell_hooks.py _PRE_TOOL_DIALECTS (:644) is
//     (("action","message","reason","args"), ("decision","reason","message","tool_input"))
//     and _parse_pre_tool_call (:647-663) blocks when either verb key equals
//     "block". hookSpecificOutput -- the key hooks/read-gate.js emits (:74-80) --
//     has ZERO occurrences across agent/ and hermes_cli/ (findstr rc 1, empty, on
//     both keys), so that file alone would produce no verdict here and the gate
//     would enforce nothing in silence.
//   TOOL NAMES. Hermes sends read_file / search_files (tools/file_tools.py
//     READ_FILE_SCHEMA :1182, SEARCH_FILES_SCHEMA :1325), never Read/Grep/Bash, so
//     read-gate.js:616 early-exits allow on every single Hermes read.
// Spawning rather than require()-ing is what lets read-gate.js ship UNCHANGED to
// the hosts already running it (claude-settings, zcode-plugin, opencode ../gates,
// antigravity): that file calls main() at EOF (:683) and main() consumes stdin, so
// a require() would block this adapter at load time. hooks/adapters/
// opencode-gates.js:20-23 uses the same seam for the same reason.
//
// SCOPE, stated as a hole rather than hidden: Hermes terminal / execute_code can
// still read a file through a shell command, which is read-gate.js R3. This
// adapter never emits tool_name Bash, so R3 does not run here;
// agent/tool_dispatch_helpers.py _PATH_SCOPED_READERS (:50) -- read_file,
// search_files -- is the honest boundary of this gate.
//
// FAIL OPEN, exactly like the Claude read gate (read-gate.js:9-13): a broken READ
// gate bricks every read the CA owns, including the reads needed to fix the gate
// itself, and a wrongly-allowed read costs tokens rather than data -- a read
// mutates nothing. So every exception below exits 0 with empty stdout, which is
// Hermes for ALLOW.
//
// NOT ACTIVE UNTIL ANH REGISTERS IT. hermes-shell-hooks verifies registration and
// never authors it (code_tools.py _ma_hook_verify_hermes), and this task forbids
// writing config.yaml. Activating means adding ONE hooks: entry --
//     pre_tool_call:
//       - matcher: read_file|search_files
//         command: node "<hooks_dir>/hermes-read-gate.js"
//         timeout: 10
//         fail_closed: true
// then consenting with "hermes hooks approve" so the (event, command) pair lands
// in shell-hooks-allowlist.json (agent/shell_hooks.py _consented :319-330 skips an
// unapproved spec). Until then this file sits in hooks_dir inert and sync_hook
// reports read_gate.active = false. There is deliberately NO break-glass prompt
// hook: a second pre_llm_call entry would be another config.yaml write. The escape
// hatch is the state file the spawned gate already owns -- enabled:false in
// read-gate.state.json beside read-gate.js (NATIVE_READ_GATE_DIR relocates it).
'use strict';
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// The policy SOT, copied VERBATIM beside this adapter by sync_hook
// (code_tools.py _MA_HOOK_HERMES_READ_COPIES). Never registered on Hermes by name:
// its Claude dialect is what this file exists to translate.
const GATE = path.join(__dirname, 'read-gate.js');
// Less than the timeout: 10 the hooks: entry asks for, so the gate answers
// before Hermes kills it and fail_closed turns a slow read into a blocked one.
const GATE_TIMEOUT_MS = 8000;

// Hermes reader tool -> the Claude matcher name read-gate.js expects. Argument
// names port intact, which is the finding that makes this a translation and not
// a reimplementation. Measured 2026-11-23 in the live Hermes tree:
// tools/file_tools.py:638 is read_file_tool(path, offset=1, limit=DEFAULT_READ_LIMIT)
// and tools/file_operations_common.py:301 sets DEFAULT_READ_LIMIT = 2000, so path
// IS the gate's file_path and limit IS its limit -- the R1 law (an unbounded read
// of a >200-line code file) needs no adjustment, and a limit that DEFAULTS to 2000
// is precisely the unbounded case the gate exists to catch. SEARCH_FILES_SCHEMA
// (:1325) declares pattern, path, file_glob (:1334, "Content search only") and a
// target enum content|files (:1332), so file_glob maps onto the gate's glob
// (read-gate.js:312 reads ti.glob + ti.type), and target "files" is a name listing
// -> Glob, which read-gate.js:615 always allows: parity with the Claude host.
function translate(msg) {
  const tool = String((msg && msg.tool_name) || '');
  const ti = (msg && msg.tool_input && typeof msg.tool_input === 'object') ? msg.tool_input : {};
  if (tool === 'read_file') {
    const out = { file_path: String(ti.path || '') };
    if (ti.limit !== undefined && ti.limit !== null) out.limit = ti.limit;
    if (ti.offset !== undefined && ti.offset !== null) out.offset = ti.offset;
    return { tool_name: 'Read', tool_input: out };
  }
  if (tool === 'search_files') {
    if (String(ti.target || 'content') === 'files') return { tool_name: 'Glob', tool_input: {} };
    const out = { pattern: String(ti.pattern || '') };
    if (ti.path !== undefined && ti.path !== null) out.path = String(ti.path);
    if (ti.file_glob !== undefined && ti.file_glob !== null) out.glob = String(ti.file_glob);
    return { tool_name: 'Grep', tool_input: out };
  }
  return null; // any other tool -- a writer, terminal, web -- is not this gate's business
}

function main() {
  let raw;
  try {
    raw = fs.readFileSync(0, 'utf8');
  } catch (e) {
    return process.exit(0); // no stdin: fail open
  }
  if (raw.length && raw.charCodeAt(0) === 0xfeff) raw = raw.slice(1);
  let msg;
  try {
    msg = JSON.parse(raw || '{}');
  } catch (e) {
    return process.exit(0); // malformed: fail open
  }
  // hook_event_name is NOT decorative: the write adapter hermes-gate.js (:165-183)
  // dispatches only on pre_tool_call / pre_llm_call, and a payload naming neither
  // falls through to an empty-stdout ALLOW, which would certify a dead gate.
  if (String(msg.hook_event_name || '') !== 'pre_tool_call') return process.exit(0);
  const claude = translate(msg);
  if (!claude) return process.exit(0);
  claude.hook_event_name = 'PreToolUse';
  claude.cwd = msg.cwd ? String(msg.cwd) : undefined;
  claude.session_id = msg.session_id || '_hermes_read_gate';
  let r;
  try {
    r = spawnSync('node', [GATE], {
      input: JSON.stringify(claude),
      encoding: 'utf8',
      timeout: GATE_TIMEOUT_MS,
      windowsHide: true,
    });
  } catch (e) {
    return process.exit(0); // spawn failure: fail open
  }
  if (!r || r.error || typeof r.stdout !== 'string') return process.exit(0);
  const out = r.stdout.trim();
  if (!out) return process.exit(0); // empty stdout is the gate's own ALLOW
  let verdict;
  try {
    verdict = JSON.parse(out);
  } catch (e) {
    return process.exit(0); // not the gate's JSON: fail open
  }
  const hso = verdict && verdict.hookSpecificOutput;
  if (hso && hso.permissionDecision === 'deny') {
    // The ONE shape Hermes blocks on (shell_hooks.py :647-663).
    process.stdout.write(JSON.stringify({
      action: 'block',
      message: String(hso.permissionDecisionReason || 'read gate').slice(0, 1200),
    }));
  }
  process.exit(0);
}

if (require.main === module) {
  try {
    main();
  } catch (e) {
    process.exit(0); // last resort: a crashing read gate must not brick reads
  }
}
module.exports = { translate };
