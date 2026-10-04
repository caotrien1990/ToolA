// Codex PreToolUse adapter (P22). Codex speaks the Claude hook wire
// (hook_event_name / tool_name / tool_input / cwd / session_id, deny via
// hookSpecificOutput.permissionDecision) -- verified in the codex 0.155.1 binary
// (codex-rs/hooks/src/registry.rs). What differs is the TOOL NAMES and the
// SHAPE of tool_input: Codex writes come through apply_patch (a patch TEXT, not
// a file_path) and every read/search is a shell command. This shim translates
// the Codex payload into the Claude-shaped PreToolUse JSON the SOT gates expect,
// pipes it through the real gate, and relays the gate's decision verbatim.
//
// Tool mapping (Codex -> the gate's Claude matcher):
//   apply_patch            -> Write   (write gate; file_path extracted per file)
//   shell/local_shell/
//     unified_exec/exec    -> Bash    (read gate; the gate parses the command)
//
// FAIL POLICY (mirrors the gates): WRITE = fail CLOSED. An apply_patch with no
// recoverable target is handed to the write gate with an empty file_path so the
// gate answers its own fail-closed "carried no file_path" deny; if the gate
// process itself errors / times out / dies non-zero on a write, this adapter
// denies directly. READ = fail OPEN like the read gate: a gate failure on a
// shell/read tool allows, because a wrongly-allowed read mutates nothing.
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const HOOKS_DIR = __dirname; // installed beside the gates
const WRITE_GATE = path.join(HOOKS_DIR, 'native-write-gate.js');
const READ_GATE = path.join(HOOKS_DIR, 'read-gate.js');

// TOOLA_GATE_TIMEOUT_MS is a test seam: a probe must not wait 15s on a sleeper.
const GATE_TIMEOUT_MS = Number(process.env.TOOLA_GATE_TIMEOUT_MS) || 15000;

const WRITE_TOOLS = new Set(['apply_patch']);
const SHELL_TOOLS = new Set(['shell', 'local_shell', 'unified_exec', 'exec', 'container.exec']);

function deny(reason) {
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: 'deny',
      permissionDecisionReason: reason,
    },
  }));
}

// Pull the target file paths out of an apply_patch payload. Codex's freeform
// patch grammar names each touched file in an "*** <verb> File: <path>" line
// (Add/Update/Delete File); the structured form carries {changes:[{path}]}.
// Returns [] when no path can be recovered -- the caller then hands the write
// gate an empty file_path so the gate fail-closes on its own contract.
function patchTargets(toolInput) {
  const out = [];
  const changes = toolInput && Array.isArray(toolInput.changes) ? toolInput.changes : null;
  if (changes) {
    for (const c of changes) {
      const p = c && (c.path || c.file_path || c.filename);
      if (p) out.push(String(p));
    }
  }
  const text = String((toolInput && (toolInput.patch || toolInput.input || toolInput.command)) || '');
  const re = /^\*\*\*\s+(?:Add|Update|Delete)\s+File:\s*(.+?)\s*$/gm;
  let m;
  while ((m = re.exec(text)) !== null) out.push(m[1]);
  return [...new Set(out)];
}

// Spawn a gate. Returns {out, failed}: failed=true means the gate process
// errored, timed out, or exited non-zero (it never got to rule). A clean allow
// is out='' + failed=false (the gate exits 0 silently); a clean deny is out=
// <json> + failed=false.
function runGate(gatePath, payload) {
  let r;
  try {
    r = spawnSync(process.execPath, [gatePath], {
      input: JSON.stringify(payload),
      encoding: 'utf8',
      timeout: GATE_TIMEOUT_MS,
      env: process.env,
    });
  } catch (e) {
    return { out: '', failed: true };
  }
  const failed = !!r.error || r.status === null || r.status !== 0;
  return { out: (r.stdout || '').trim(), failed };
}

function isDeny(out) {
  return /"permissionDecision"\s*:\s*"deny"/.test(out);
}

function main() {
  let raw = '';
  try { raw = fs.readFileSync(0, 'utf8').replace(/^﻿/, ''); } catch (e) { raw = ''; }
  let input = {};
  try { input = raw ? JSON.parse(raw) : {}; } catch (e) { input = {}; }
  const tool = String(input.tool_name || '');
  const ti = input.tool_input || {};
  const cwd = input.cwd ? String(input.cwd) : undefined;

  if (WRITE_TOOLS.has(tool)) {
    const targets = patchTargets(ti);
    if (targets.length === 0) {
      // No recoverable target: the write gate's own fail-closed contract rules
      // on this ("carried no file_path"). Relay its deny; if the gate process
      // itself could not answer, deny directly naming the unparseable patch.
      const g = runGate(WRITE_GATE, { tool_name: 'Write', tool_input: { file_path: '' }, cwd, session_id: input.session_id });
      if (!g.failed && isDeny(g.out)) {
        process.stdout.write(g.out);
        return;
      }
      deny('[codex-adapter] apply_patch carried no recoverable target path and the write gate ' +
        'could not rule on it (gate process failed/timed out), so the write is denied (fail closed). ' +
        'Write through [A]: read_file_range -> write_fix / write_new_file.');
      return;
    }
    for (const fp of targets) {
      const g = runGate(WRITE_GATE, { tool_name: 'Write', tool_input: { file_path: fp }, cwd, session_id: input.session_id });
      if (g.failed) {
        deny(`[codex-adapter] the write gate did not answer for ${fp} (gate process failed/timed ` +
          'out), so the write is denied (fail closed). Write through [A]: read_file_range -> ' +
          'write_fix / write_new_file.');
        return;
      }
      if (isDeny(g.out)) {
        process.stdout.write(g.out);
        return;
      }
      // clean allow for this file -> check the rest
    }
    process.exit(0);
  }
  if (SHELL_TOOLS.has(tool)) {
    const cmd = String(ti.command || ti.cmd || (Array.isArray(ti.command) ? ti.command.join(' ') : '') || '');
    const g = runGate(READ_GATE, { tool_name: 'Bash', tool_input: { command: cmd }, cwd, session_id: input.session_id });
    // READ fails OPEN: a gate error/timeout must not brick every shell command.
    if (!g.failed && isDeny(g.out)) {
      process.stdout.write(g.out);
      return;
    }
    process.exit(0);
  }
  process.exit(0); // unknown tool -> allow
}

main();
