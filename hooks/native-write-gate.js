// Native write gate for Claude Code. One file, two hook events:
//
// PreToolUse (Write/Edit/MultiEdit/NotebookEdit): a text rule is forgotten in a
// long session; this runs on EVERY native write, before it happens. It does not
// decide scope itself: it asks [A]'s /api/native_preflight, the same Matrix
// classifier log_native_event uses, so there is one matcher and no second copy
// here to drift. Denies only when the target is inside a project
// (host_gate.scope=project) and the classifier does not allow a native write
// there. Outside every project (%TEMP%, the agent's own memory, harness config)
// it steps aside. FAIL CLOSED: if [A] cannot answer, the write is denied -- the
// bootstrap rule already says a refused [A] means STOP and report.
//
// UserPromptSubmit: the break-glass toggle. When [A] itself is broken the gate
// would block the very native writes needed to fix it, so Anh can switch it off
// by typing exactly `tắt gate` and back on with `bật gate`. It lives on
// UserPromptSubmit because only Anh's own prompt fires that event: a CA cannot
// type a prompt, so it cannot open the gate for itself. The state file and the
// off-log are refused to every native write tool for the same reason. A shell
// command can still write them; this guards a CA that forgot the rule, not one
// working against it.
'use strict';

const fs = require('fs');
const http = require('http');
const path = require('path');

const BASE = (process.env.ORCHESTRATOR_BASE_URL || 'http://localhost:8083').replace(/\/+$/, '');
const AGENT_ID = resolveAgentId();
const TIMEOUT_MS = 4000;
// NATIVE_WRITE_GATE_DIR is a test seam: a probe must not flip the real state.
const STATE_DIR = process.env.NATIVE_WRITE_GATE_DIR || __dirname;
const STATE_FILE = path.join(STATE_DIR, 'native-write-gate.state.json');
const OFF_LOG = path.join(STATE_DIR, 'native-write-gate.offlog.jsonl');
const KEYWORD_OFF = 'tat gate';
const KEYWORD_ON = 'bat gate';

// The per-host agent binding (P22): env ORCHESTRATOR_AGENT_ID wins (a host that
// sets env per hook), else the sibling hook-agent.json manage_agent sync_hook
// wrote at install, else 'unknown'. Read once per process -- a hook process
// handles exactly one event. ONE file serves every host; no logic fork.
function resolveAgentId() {
  const envId = (process.env.ORCHESTRATOR_AGENT_ID || '').trim();
  if (envId) return envId;
  try {
    const j = JSON.parse(fs.readFileSync(require('path').join(__dirname, 'hook-agent.json'), 'utf8').replace(/^﻿/, ''));
    const id = String(j && j.agent_id || '').trim();
    if (id) return id;
  } catch (e) { /* absent binding -> unknown */ }
  return 'unknown';
}

function emit(decision, reason) {
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: decision,
      permissionDecisionReason: reason,
    },
  }));
  process.exit(0);
}

function failClosed(why) {
  emit('deny',
    `[native-write-gate] ${why}. [A] preflight could not rule on this write, so it is blocked ` +
    `(fail closed). [A] refused -> STOP and report to Anh; do not retry or work around it. ` +
    `Only Anh can open break-glass, by typing \`tắt gate\`.`);
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

function samePath(a, b) {
  const norm = (p) => path.resolve(p).toLowerCase();
  return norm(a) === norm(b);
}

// "Tắt Gate." / "TAT GATE" / NFD input all fold to "tat gate". The whole prompt
// must be the keyword, so a sentence that merely mentions it toggles nothing.
function foldPrompt(text) {
  return String(text || '')
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[đĐ]/g, 'd')
    .toLowerCase().replace(/\s+/g, ' ').trim()
    .replace(/[.!\s]+$/, '');
}

function onUserPrompt(input) {
  const said = foldPrompt(input.prompt);
  if (said === KEYWORD_OFF || said === KEYWORD_ON) {
    const enabled = said === KEYWORD_ON;
    const at = new Date().toISOString();
    try {
      fs.writeFileSync(STATE_FILE, JSON.stringify({ enabled, changed_at: at, session_id: input.session_id || null }, null, 1), 'utf8');
      appendOffLog({ event: enabled ? 'gate_on' : 'gate_off', session_id: input.session_id || null });
    } catch (e) {
      process.stdout.write(`[native-write-gate] toggle FAILED, the gate is unchanged: ${e.message}`);
      return;
    }
    process.stdout.write(enabled
      ? '[native-write-gate] ON. Native writes into a project are refused again; write through [A].'
      : `[native-write-gate] OFF by Anh (break-glass). Native writes into a project pass unchecked and ` +
        `each one is appended to ${OFF_LOG}. \`bật gate\` turns it back on.`);
    return;
  }
  const state = readState();
  if (!state.enabled) {
    process.stdout.write(`[native-write-gate] OFF since ${state.changed_at || 'unknown'} (break-glass). ` +
      `\`bật gate\` turns it back on.`);
  }
}

function preflight(body) {
  return new Promise((resolve, reject) => {
    const data = Buffer.from(JSON.stringify(body), 'utf8');
    const req = http.request(`${BASE}/api/native_preflight`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': data.length },
      timeout: TIMEOUT_MS,
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        try {
          resolve({ status: res.statusCode, json: JSON.parse(Buffer.concat(chunks).toString('utf8')) });
        } catch (e) {
          reject(new Error(`preflight answered HTTP ${res.statusCode} with a non-JSON body`));
        }
      });
    });
    req.on('timeout', () => req.destroy(new Error(`preflight timed out after ${TIMEOUT_MS}ms`)));
    req.on('error', reject);
    req.end(data);
  });
}

async function onPreToolUse(input) {
  const tool = String(input.tool_name || '');
  const ti = input.tool_input || {};
  let target = String(ti.file_path || ti.notebook_path || '').trim();
  if (!target) return failClosed(`${tool} carried no file_path`);
  if (!path.isAbsolute(target) && input.cwd) target = path.resolve(String(input.cwd), target);

  if (samePath(target, STATE_FILE) || samePath(target, OFF_LOG)) {
    return emit('deny',
      `[native-write-gate] PROTECTED: ${path.basename(target)} changes only through Anh's own prompt ` +
      `(\`tắt gate\` / \`bật gate\`), never through a tool write.`);
  }
  if (!readState().enabled) {
    try {
      appendOffLog({ event: 'native_write_while_off', tool, target, session_id: input.session_id || null });
    } catch (e) {
      // The audit line is best effort; break-glass must not fail on it.
    }
    process.exit(0);
  }

  let answer;
  try {
    answer = await preflight({
      tool_name: tool,
      action_type: 'write',
      target,
      transport: 'native',
      agent_id: AGENT_ID,
    });
  } catch (e) {
    // "localhost" tries ::1 and 127.0.0.1; both refusing arrives as an
    // AggregateError with an empty message, so name the socket code instead.
    const code = e && (e.code || (e.errors && e.errors[0] && e.errors[0].code));
    return failClosed(code ? `${BASE} ${code}` : String(e && e.message || e));
  }
  const pf = answer.json || {};
  const host = pf.host_gate || {};
  if (answer.status !== 200 || pf.ok !== true || !host.scope) {
    return failClosed(`preflight answered HTTP ${answer.status} ${pf.error_name || 'without a host_gate'}`);
  }
  if (host.scope !== 'project' || pf.decision === 'allow') process.exit(0);

  const via = (pf.preferred_via_a || []).slice(0, 3).join(', ') || 'read_file_range -> write_fix / write_new_file';
  emit('deny',
    `[native-write-gate] ${pf.error_name || 'HOST_GATE_DENY'}: native ${tool} into a project ` +
    `(task_class=${pf.task_class}, native_policy=${pf.native_policy}). Write through [A]: ` +
    `read_file_range -> write_fix (existing file) or write_new_file (new file); preferred: ${via}. ` +
    `A native project write means the core rule left context (RULE-LOSS): reload ` +
    `get_rule_core_text with force:true before continuing.`);
}

// settings.json registers the UserPromptSubmit side with `--prompt`, so the event
// is known even when stdin is unreadable and a prompt is never answered with a
// PreToolUse deny.
function main() {
  const promptEvent = process.argv.includes('--prompt');
  let input;
  try {
    // A leading BOM is transport noise (Windows PowerShell 5.1 prepends one when
    // it pipes to a native process), not a malformed request.
    const raw = fs.readFileSync(0, 'utf8').replace(/^﻿/, '');
    input = JSON.parse(raw || '{}');
  } catch (e) {
    if (!promptEvent) return failClosed('hook input was not valid JSON');
    process.stdout.write('[native-write-gate] prompt hook input was not valid JSON; the gate is unchanged.');
    return process.exit(0);
  }
  if (promptEvent) {
    // Never block or fail a prompt: a toggle error is reported as text, exit 0.
    try { onUserPrompt(input); } catch (e) { process.stdout.write(`[native-write-gate] ${e.message}`); }
    return process.exit(0);
  }
  onPreToolUse(input).catch((e) => failClosed(`hook crashed: ${e && e.message || e}`));
}

main();
