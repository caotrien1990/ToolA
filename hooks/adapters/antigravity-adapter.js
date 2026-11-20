// Antigravity PreToolUse & PreInvocation adapter for Tool [A] SOT gates.
// Adapts Antigravity's lifecycle hooks contract to ToolA's native-write-gate and read-gate.
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const HOOKS_DIR = __dirname;
const WRITE_GATE = path.join(HOOKS_DIR, 'native-write-gate.js');
const READ_GATE = path.join(HOOKS_DIR, 'read-gate.js');
const GATE_TIMEOUT_MS = Number(process.env.TOOLA_GATE_TIMEOUT_MS) || 8000;

function foldPrompt(text) {
  return String(text || '')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[đĐ]/g, 'd')
    .toLowerCase().replace(/\s+/g, ' ').trim()
    .replace(/[.!\s]+$/, '');
}

function runGate(gatePath, payload, isPrompt) {
  const args = [gatePath];
  if (isPrompt) args.push('--prompt');
  let r;
  try {
    r = spawnSync(process.execPath, args, {
      input: JSON.stringify(payload),
      encoding: 'utf8',
      timeout: GATE_TIMEOUT_MS,
      env: process.env,
    });
  } catch (e) {
    return { out: '', failed: true, error: e };
  }
  const failed = !!r.error || r.status === null || r.status !== 0;
  return { out: (r.stdout || '').trim(), failed };
}

function isDeny(out) {
  return /"permissionDecision"\s*:\s*"deny"/.test(out);
}

function extractDenyReason(out, defaultReason) {
  try {
    const j = JSON.parse(out);
    if (j.hookSpecificOutput && j.hookSpecificOutput.permissionDecisionReason) {
      return j.hookSpecificOutput.permissionDecisionReason;
    }
  } catch (e) {}
  return defaultReason;
}

function handlePreInvocation(input) {
  const prompt = String(input.lastUserInput || '').trim();
  if (!prompt) {
    process.stdout.write(JSON.stringify({}));
    return;
  }
  const said = foldPrompt(prompt);
  let msg = '';
  if (said === 'tat gate' || said === 'bat gate') {
    const res = runGate(WRITE_GATE, { prompt, session_id: input.conversationId }, true);
    msg = res.out;
  } else if (said === 'tat read gate' || said === 'bat read gate') {
    const res = runGate(READ_GATE, { prompt, session_id: input.conversationId }, true);
    msg = res.out;
  }

  if (msg) {
    process.stdout.write(JSON.stringify({
      injectSteps: [
        {
          systemMessage: {
            systemMessage: msg,
          },
        },
      ],
    }));
  } else {
    process.stdout.write(JSON.stringify({}));
  }
}

function handlePreToolUse(input) {
  const toolCall = input.toolCall || {};
  const toolName = String(toolCall.name || '').toLowerCase();
  const args = toolCall.args || {};
  const cwd = input.cwd ? String(input.cwd) : process.cwd();
  const sessionId = input.conversationId || null;

  // 1. Native write tools: write_file, edit_file
  if (toolName === 'write_file' || toolName === 'edit_file') {
    const target = String(args.Path || args.path || args.file_path || '').trim();
    const payload = {
      tool_name: 'Write',
      tool_input: { file_path: target },
      cwd,
      session_id: sessionId,
    };
    const res = runGate(WRITE_GATE, payload, false);
    if (res.failed || isDeny(res.out)) {
      const reason = isDeny(res.out)
        ? extractDenyReason(res.out, `Native ${toolName} denied on ${target}. Write through Tool [A] (write_fix / write_new_file).`)
        : `[native-write-gate] Gate check failed (fail closed): native ${toolName} denied. Write through Tool [A].`;
      process.stdout.write(JSON.stringify({ decision: 'deny', reason }));
      return;
    }
    process.stdout.write(JSON.stringify({ decision: 'allow' }));
    return;
  }

  // 2. Native read tools: read_file
  if (toolName === 'read_file') {
    const target = String(args.Path || args.path || '').trim();
    let limit;
    if (typeof args.StartLine === 'number' && typeof args.EndLine === 'number') {
      limit = Math.max(1, args.EndLine - args.StartLine + 1);
    }
    const payload = {
      tool_name: 'Read',
      tool_input: { file_path: target, limit },
      cwd,
      session_id: sessionId,
    };
    const res = runGate(READ_GATE, payload, false);
    if (!res.failed && isDeny(res.out)) {
      const reason = extractDenyReason(res.out, `Native read_file denied on ${target}. Use Tool [A] read_file_range / find_symbol.`);
      process.stdout.write(JSON.stringify({ decision: 'deny', reason }));
      return;
    }
    process.stdout.write(JSON.stringify({ decision: 'allow' }));
    return;
  }

  // 3. Command tool: run_command
  if (toolName === 'run_command') {
    const cmd = String(args.CommandLine || '').trim();
    const payload = {
      tool_name: 'Bash',
      tool_input: { command: cmd },
      cwd,
      session_id: sessionId,
    };
    const res = runGate(READ_GATE, payload, false);
    if (!res.failed && isDeny(res.out)) {
      const reason = extractDenyReason(res.out, `Native command denied. Follow Tool [A] read/search routing.`);
      process.stdout.write(JSON.stringify({ decision: 'deny', reason }));
      return;
    }
    process.stdout.write(JSON.stringify({ decision: 'allow' }));
    return;
  }

  // All other tools allowed
  process.stdout.write(JSON.stringify({ decision: 'allow' }));
}

function main() {
  const isPrompt = process.argv.includes('--prompt');
  let raw = '';
  try {
    raw = fs.readFileSync(0, 'utf8').replace(/^\uFEFF/, '');
  } catch (e) {
    raw = '';
  }
  let input = {};
  try {
    input = raw ? JSON.parse(raw) : {};
  } catch (e) {
    input = {};
  }

  if (isPrompt || input.hookEventName === 'PreInvocation') {
    handlePreInvocation(input);
  } else {
    handlePreToolUse(input);
  }
}

main();