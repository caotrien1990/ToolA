"use strict";
// OpenCode gates plugin (P22) - CJS SOT for OpenCode >= 1.18.29 (binary 1.19.5).
// sync_hook installs this file VERBATIM as toola-gates.js in the host plugins
// dir, generates the one-line toola-gates.mjs ESM twin beside it, and copies the
// repo gate CLIs into ../gates/*.cjs. The plugins-dir {"type":"commonjs"} marker
// pins this .js to CJS under any ancestor package.json "type"; the .mjs twin is
// explicit ESM for npm config registration and the install probe.
//
// V1 exported a bare async function returning a hook map; V2 rejects that shape
// ("Plugin must export a default definition with an id and an effect or setup
// function"), which silently left every gate unenforced after the upgrade.
// V2 default-exports { id, setup(ctx) } and registers hooks on the owning domain:
//   tool.execute.before -> ctx.tool.hook("execute.before", event)  (throw = block)
//   chat.message        -> ctx.session.hook("prompt", event)       (event.prompt.text)
// CJS on purpose: module.exports is the default export through standard CJS/ESM
// interop, so the .mjs twin and any ESM importer see the same object. Desktop
// cli 2.x directory-discovery only loads .js (measured: zero .mjs loads in the
// desktop log); npm opencode-ai 1.x -- the museroom member's host -- loads both.
//
// The gate CLIs in ../gates/ stay the SOT: spawned with a translated Claude
// PreToolUse payload, never imported in-process (their main() consumes stdin
// and calls process.exit). Spawn the .cjs twins so the gate module format is
// explicit regardless of any package.json type above gates/.
// Spawn with an explicit node binary: inside the OpenCode server process
// process.execPath is the compiled opencode executable, NOT node - spawning it
// with a gate path yields empty stdout, which enforce() would read as allow
// (silent fail-open). TOOLA_GATE_NODE overrides the interpreter for probes.
//
// Tool mapping (OpenCode lowercase event.tool -> the gate's Claude matcher):
//   write/edit/multiedit/notebookedit -> Write/Edit/MultiEdit/NotebookEdit,
//   apply_patch -> ApplyPatch (write gate; see WRITE_NAMES below)
//   read/grep/glob/bash/shell         -> Read/Grep/Glob/Bash (read gate)
// Translation fixes vs the V1 relay: the grep target is ti.path (not file_path),
// OpenCode's `include` filter maps to the gate's `glob`, and `shell` is the
// desktop name of the Bash tool.
// The Anh-only break-glass toggle arrives on the prompt hook: "tat gate"/"bat gate"
// and "tat read gate"/"bat read gate" (diacritic-insensitive) are forwarded to the
// matching gate's --prompt handler, which owns the state file.
const { spawnSync } = require("node:child_process");
const path = require("node:path");

const NODE_BIN = process.env.TOOLA_GATE_NODE || "node";
const GATES = path.join(__dirname, "..", "gates");
const WRITE_GATE = path.join(GATES, "native-write-gate.cjs");
const READ_GATE = path.join(GATES, "read-gate.cjs");

const WRITE_TOOLS = new Set(["write", "edit", "multiedit", "apply_patch", "notebookedit"]);
// Truthful labels for offlog/preflight: ca_policy_matrix._TOOL_TO_CLASS maps
// Write/Edit/MultiEdit/NotebookEdit/ApplyPatch to write_existing_file, so
// enforcement is unchanged and the audit class matches the real tool.
const WRITE_NAMES = { write: "Write", edit: "Edit", multiedit: "MultiEdit", apply_patch: "ApplyPatch", notebookedit: "NotebookEdit" };
const READ_TOOLS = { read: "Read", grep: "Grep", glob: "Glob", bash: "Bash", shell: "Bash" };

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
  const r = spawnSync(NODE_BIN, args, {
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

function gateCall(event) {
  const tool = String(event.tool || "").toLowerCase();
  const args = event.input || event.args || {};
  const cwd = args.cwd || args.workdir || undefined;
  const session_id = event.sessionID;
  if (WRITE_TOOLS.has(tool)) {
    const fp = args.file_path || args.filePath || args.notebook_path || args.path || "";
    return { gate: WRITE_GATE, payload: { tool_name: WRITE_NAMES[tool] || "Write", tool_input: { file_path: fp }, cwd, session_id } };
  }
  const name = READ_TOOLS[tool];
  if (!name) return null;
  const ti = {
    file_path: args.file_path || args.filePath || args.path,
    path: args.path,
    pattern: args.pattern,
    glob: args.glob || args.include,
    type: args.type,
    command: args.command,
    limit: args.limit,
    offset: args.offset,
  };
  return { gate: READ_GATE, payload: { tool_name: name, tool_input: ti, cwd, session_id } };
}

module.exports = {
  id: "toola-gates",
  async setup(ctx) {
    const log = async (level, message) => {
      try { await ctx.app.log({ body: { service: "toola-gates", level, message } }); } catch (e) { console.error(message); }
    };
    await log("info", "toola-gates V2 setup: registering execute.before + prompt hooks (node=" + NODE_BIN + ")");
    if (!ctx.tool || typeof ctx.tool.hook !== "function") {
      throw new Error("toola-gates: ctx.tool.hook missing; OpenCode plugin API mismatch");
    }
    await ctx.tool.hook("execute.before", (event) => {
      const call = gateCall(event);
      if (call) enforce(call.gate, call.payload);
    });
    // Break-glass is secondary: if the prompt domain is absent on this build,
    // keep tool enforcement alive and log the degradation instead of failing
    // the whole plugin load (which would silently disable BOTH gates).
    try {
      await ctx.session.hook("prompt", (event) => {
        const prompt = String((event.prompt && event.prompt.text) || "").trim();
        if (!prompt) return;
        const said = foldPrompt(prompt);
        let msg = "";
        if (said === "tat gate" || said === "bat gate") {
          msg = runGate(WRITE_GATE, { prompt, session_id: event.sessionID }, true);
        } else if (said === "tat read gate" || said === "bat read gate") {
          msg = runGate(READ_GATE, { prompt, session_id: event.sessionID }, true);
        }
        if (msg && event.prompt) event.prompt.text = prompt + "\n" + msg;
      });
    } catch (e) {
      await log("warn", "toola-gates: prompt hook unavailable (" + (e && e.message) + "); break-glass degraded, tool gates active");
    }
    await log("info", "toola-gates V2 ready: gates enforced via " + READ_GATE + " / " + WRITE_GATE);
  },

  // --- npm opencode-ai 1.x host contract (@opencode-ai/plugin PluginModule) ---
  // The museroom member (ope-watch.ps1) runs npm opencode.exe 1.19.5, and any
  // project whose config registers plugins via a "plugin" array, use a loader
  // that requires a default export { server(input) => Hooks } and rejects the
  // desktop-only { id, setup } shape ("must default export an object with
  // server()"), silently leaving that host gateless. Export BOTH shapes:
  // desktop cli 2.x directory-discovery uses setup(ctx) above; npm 1.x /
  // config-registration uses server(input) here. They share gateCall / enforce
  // / runGate. npm passes tool args in output.args (desktop: event.input) and
  // blocks by throwing, exactly like setup.
  async server(input) {
    return {
      "tool.execute.before": async (inp, out) => {
        const call = gateCall({ tool: inp.tool, input: (out && out.args) || {}, sessionID: inp.sessionID });
        if (call) enforce(call.gate, call.payload);
      },
      // Break-glass parity, best-effort: npm chat.message carries the user text
      // in output.parts (text parts), not event.prompt.text. A shape mismatch
      // must never break tool enforcement, so errors here are swallowed.
      "chat.message": async (inp, out) => {
        try {
          const parts = (out && out.parts) || [];
          const prompt = parts.filter((p) => p && p.type === "text" && typeof p.text === "string").map((p) => p.text).join(" ").trim();
          if (!prompt) return;
          const said = foldPrompt(prompt);
          let msg = "";
          if (said === "tat gate" || said === "bat gate") {
            msg = runGate(WRITE_GATE, { prompt, session_id: inp.sessionID }, true);
          } else if (said === "tat read gate" || said === "bat read gate") {
            msg = runGate(READ_GATE, { prompt, session_id: inp.sessionID }, true);
          }
          if (msg && Array.isArray(out.parts)) out.parts.push({ type: "text", text: msg });
        } catch (e) { /* best-effort break-glass; tool enforcement unaffected */ }
      },
    };
  },
};
