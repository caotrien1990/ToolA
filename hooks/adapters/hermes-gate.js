// Native write gate for Hermes (shell-hook adapter). Policy SOT = [A] /api/native_preflight;
// this file holds NO policy copy and RELAYS the verdict verbatim, like hooks/native-write-gate.js.
// Hermes wire: stdin {hook_event_name, tool_name, tool_input, session_id, cwd, profile, extra};
// stdout {"action":"block","message":...} denies a pre_tool_call; {"context":...} injects into the
// turn; empty stdout + exit 0 allows. Registered via config.yaml hooks: (see hermes hooks list).
//
// pre_tool_call (matcher write_file|patch, fail_closed:true): FAIL CLOSED - allow ONLY when [A]
// answers decision=allow; every other answer (incl. deny_outside_project_mutate) is a block.
// pre_llm_call (--prompt): the break-glass toggle. Only Anh's own prompt starts a turn, so only
// Anh can flip it: exactly `tat gate` / `bat gate` (diacritics folded). State + offlog live next
// to this file; both are PROTECTED against tool writes. Missing/unreadable state = ON.
'use strict';
const fs = require('fs');
const http = require('http');
const path = require('path');

const BASE = (process.env.ORCHESTRATOR_BASE_URL || 'http://localhost:8083').replace(/\/+$/, '');
const TIMEOUT_MS = 4000;
// NATIVE_WRITE_GATE_DIR is a test seam: a probe must not flip the real state.
const STATE_DIR = process.env.NATIVE_WRITE_GATE_DIR || __dirname;
const STATE_FILE = path.join(STATE_DIR, 'native-write-gate.state.json');
const OFF_LOG = path.join(STATE_DIR, 'native-write-gate.offlog.jsonl');
const KEYWORD_OFF = 'tat gate';
const KEYWORD_ON = 'bat gate';
const AGENT_ID = resolveAgentId();

// env ORCHESTRATOR_AGENT_ID wins, else sibling hook-agent.json, else this host's registry id.
function resolveAgentId() {
  const envId = (process.env.ORCHESTRATOR_AGENT_ID || '').trim();
  if (envId) return envId;
  try {
    const j = JSON.parse(fs.readFileSync(path.join(__dirname, 'hook-agent.json'), 'utf8').replace(/^\uFEFF/, ''));
    const id = String(j && j.agent_id || '').trim();
    if (id) return id;
  } catch (e) { /* absent binding -> host default */ }
  return 'hermes';
}

function block(message) {
  process.stdout.write(JSON.stringify({ action: 'block', message }));
  process.exit(0);
}
function failClosed(why) {
  block(`[native-write-gate] ${why}. [A] preflight could not rule on this write, so it is blocked ` +
    `(fail closed). [A] refused -> STOP and report to Anh; do not retry or work around it. ` +
    `Only Anh can open break-glass, by typing \`tắt gate\`.`);
}
// Missing or unreadable state = ON. Only an explicit enabled:false opens the gate.
function readState() {
  try {
    const s = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8').replace(/^\uFEFF/, ''));
    return s && s.enabled === false ? s : { enabled: true };
  } catch (e) { return { enabled: true }; }
}
function appendOffLog(entry) {
  fs.appendFileSync(OFF_LOG, JSON.stringify({ ts: new Date().toISOString(), ...entry }) + '\n', 'utf8');
}
function samePath(a, b) {
  const norm = (p) => path.resolve(p).toLowerCase();
  return norm(a) === norm(b);
}
// "Tắt Gate." / "TAT GATE" / NFD input all fold to "tat gate". The WHOLE prompt must be the
// keyword, so a sentence that merely mentions it toggles nothing.
function foldPrompt(text) {
  return String(text || '')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[đĐ]/g, 'd')
    .toLowerCase().replace(/\s+/g, ' ').trim()
    .replace(/[.!\s]+$/, '');
}
function promptText(um) {
  if (typeof um === 'string') return um;
  if (Array.isArray(um)) return um.map(p => typeof p === 'string' ? p : (p && typeof p.text === 'string' ? p.text : '')).join('\n');
  return '';
}
function context(text) { process.stdout.write(JSON.stringify({ context: text })); }

// pre_llm_call fires once per turn with the fresh user message = Anh's prompt (UserPromptSubmit parity).
function onPrompt(input) {
  const said = foldPrompt(promptText((input.extra || {}).user_message));
  if (said === KEYWORD_OFF || said === KEYWORD_ON) {
    const enabled = said === KEYWORD_ON;
    const at = new Date().toISOString();
    try {
      fs.mkdirSync(STATE_DIR, { recursive: true });
      fs.writeFileSync(STATE_FILE, JSON.stringify({ enabled, changed_at: at, session_id: input.session_id || null }, null, 1), 'utf8');
      appendOffLog({ event: enabled ? 'gate_on' : 'gate_off', session_id: input.session_id || null });
    } catch (e) {
      context(`[native-write-gate] toggle FAILED, the gate is unchanged: ${e.message}`);
      return process.exit(0);
    }
    context(enabled
      ? '[native-write-gate] ON. Native writes into a project are refused again; write through [A].'
      : `[native-write-gate] OFF by Anh (break-glass). Native writes pass unchecked and each one is appended to ${OFF_LOG}. \`bật gate\` turns it back on.`);
    return process.exit(0);
  }
  const state = readState();
  if (!state.enabled) {
    context(`[native-write-gate] OFF since ${state.changed_at || 'unknown'} (break-glass). \`bật gate\` turns it back on.`);
  }
  return process.exit(0);
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
        try { resolve({ status: res.statusCode, json: JSON.parse(Buffer.concat(chunks).toString('utf8')) }); }
        catch (e) { reject(new Error(`preflight answered HTTP ${res.statusCode} with a non-JSON body`)); }
      });
    });
    req.on('timeout', () => req.destroy(new Error(`preflight timed out after ${TIMEOUT_MS}ms`)));
    req.on('error', reject);
    req.end(data);
  });
}

async function onTool(input) {
  const tool = String(input.tool_name || '');
  const ti = input.tool_input || {};
  let target = String(ti.path || ti.file_path || ti.notebook_path || '').trim();
  if (!target) return failClosed(`${tool} carried no path`);
  if (!path.isAbsolute(target) && input.cwd) target = path.resolve(String(input.cwd), target);

  if (samePath(target, STATE_FILE) || samePath(target, OFF_LOG)) {
    return block(`[native-write-gate] PROTECTED: ${path.basename(target)} changes only through Anh's own prompt ` +
      `(\`tắt gate\` / \`bật gate\`), never through a tool write.`);
  }
  if (!readState().enabled) {
    try { appendOffLog({ event: 'native_write_while_off', tool, target, session_id: input.session_id || null }); }
    catch (e) { /* audit line best effort; break-glass must not fail on it */ }
    process.exit(0);
  }
  let answer;
  try {
    answer = await preflight({
      tool_name: tool, action_type: 'write', target,
      project_path: String(input.cwd || ''), transport: 'native', agent_id: AGENT_ID,
    });
  } catch (e) {
    const code = e && (e.code || (e.errors && e.errors[0] && e.errors[0].code));
    return failClosed(code ? `${BASE} ${code}` : String(e && e.message || e));
  }
  const pf = answer.json || {};
  const host = pf.host_gate || {};
  if (answer.status !== 200 || pf.ok !== true || !host.scope) {
    return failClosed(`preflight answered HTTP ${answer.status} ${pf.error_name || 'without a host_gate'}`);
  }
  if (pf.decision === 'allow') process.exit(0);
  const via = (pf.preferred_via_a || []).slice(0, 3).join(', ') || 'read_file_range -> write_fix / write_new_file';
  block(`[native-write-gate] ${pf.error_name || host.decision || 'HOST_GATE_DENY'}: native ${tool} denied ` +
    `(scope=${host.scope}, task_class=${pf.task_class}, native_policy=${pf.native_policy}). ` +
    `${host.reason || ''} Write through [A]: read_file_range -> write_fix (existing file) or ` +
    `write_new_file (new file); preferred: ${via}. If [A] is refused, STOP and report to Anh; ` +
    `break-glass \`tắt gate\` is Anh-only.`);
}

function main() {
  const promptEvent = process.argv.includes('--prompt');
  let input;
  try {
    const raw = fs.readFileSync(0, 'utf8').replace(/^\uFEFF/, '');
    input = JSON.parse(raw || '{}');
  } catch (e) {
    if (promptEvent) { context('[native-write-gate] prompt hook input was not valid JSON; the gate is unchanged.'); return process.exit(0); }
    return failClosed('hook input was not valid JSON');
  }
  if (promptEvent || input.hook_event_name === 'pre_llm_call') {
    try { onPrompt(input); } catch (e) { context(`[native-write-gate] ${e.message}`); process.exit(0); }
    return;
  }
  if (input.hook_event_name === 'pre_tool_call') {
    onTool(input).catch((e) => failClosed(`hook crashed: ${e && e.message || e}`));
    return;
  }
  process.exit(0);
}
main();
