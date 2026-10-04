// OpenCode gates plugin (P22). OpenCode has no PreToolUse hook engine; a
// plugin module's "tool.execute.before" is the equivalent interception point and
// blocks by throwing. This plugin spawns the SOT gates with a translated Claude
// PreToolUse payload and throws Error(reason) when the gate answers deny.
//
// Tool mapping (OpenCode lowercase input.tool -> the gate's Claude matcher):
//   write/edit/apply_patch         -> Write  (write gate)
//   read/grep/glob/bash            -> Read/Grep/Glob/Bash (read gate)
// The Anh-only break-glass toggle arrives on chat.message: "tắt gate"/"bật gate"
// and "tắt read gate"/"bật read gate" are forwarded to the matching gate's
// --prompt handler, which owns the state file. Installed beside the gates.
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WRITE_GATE = path.join(HERE, "native-write-gate.js");
const READ_GATE = path.join(HERE, "read-gate.js");

const WRITE_TOOLS = new Set(["write", "edit", "multiedit", "apply_patch", "notebookedit"]);
const READ_TOOLS = { read: "Read", grep: "Grep", glob: "Glob", bash: "Bash" };

function foldPrompt(text) {
  return String(text || "")
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[đĐ]/g, "d")
    .toLowerCase().replace(/\s+/g, " ").trim()
    .replace(/[.!\s]+$/, "");
}

function runGate(gatePath, payload, promptEvent) {
  const args = [gatePath];
  if (promptEvent) args.push("--prompt");
  const r = spawnSync(process.execPath, args, {
    input: JSON.stringify(payload),
    encoding: "utf8",
    timeout: 15000,
    env: process.env,
  });
  return (r.stdout || "").trim();
}

// A gate prints a deny as hookSpecificOutput.permissionDecision=deny JSON; a
// prompt toggle prints plain text. Throw on deny so OpenCode blocks the tool.
function enforce(gatePath, payload) {
  const out = runGate(gatePath, payload, false);
  if (!out) return; // allow
  const m = out.match(/"permissionDecision"\s*:\s*"deny"/);
  if (m) {
    let reason = "blocked by native gate";
    try {
      const j = JSON.parse(out);
      reason = (j.hookSpecificOutput && j.hookSpecificOutput.permissionDecisionReason) || reason;
    } catch (e) { /* keep default */ }
    throw new Error(reason);
  }
}

export const ToolAGates = async () => ({
  "tool.execute.before": async (input, output) => {
    const tool = String((input && input.tool) || "").toLowerCase();
    const args = (output && output.args) || {};
    const cwd = args.cwd || args.workdir || undefined;
    if (WRITE_TOOLS.has(tool)) {
      const fp = args.file_path || args.filePath || args.notebook_path || args.path || "";
      enforce(WRITE_GATE, { tool_name: "Write", tool_input: { file_path: fp }, cwd, session_id: input.sessionID });
      return;
    }
    if (READ_TOOLS[tool]) {
      const name = READ_TOOLS[tool];
      const ti = { file_path: args.file_path || args.filePath || args.path, pattern: args.pattern, glob: args.glob, type: args.type, command: args.command, limit: args.limit, offset: args.offset };
      enforce(READ_GATE, { tool_name: name, tool_input: ti, cwd, session_id: input.sessionID });
    }
  },
  "chat.message": async (input, output) => {
    const parts = (output && output.parts) || [];
    const prompt = parts.filter((p) => p && p.type === "text").map((p) => p.text || "").join("\n").trim();
    if (!prompt) return;
    const said = foldPrompt(prompt);
    if (said === "tat gate" || said === "bat gate") {
      const msg = runGate(WRITE_GATE, { prompt, session_id: input.sessionID }, true);
      if (msg) output.parts.push({ type: "text", text: msg });
    } else if (said === "tat read gate" || said === "bat read gate") {
      const msg = runGate(READ_GATE, { prompt, session_id: input.sessionID }, true);
      if (msg) output.parts.push({ type: "text", text: msg });
    }
  },
});

export default ToolAGates;
