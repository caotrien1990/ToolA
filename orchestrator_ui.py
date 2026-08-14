import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import queue
import json
import os
import time
import pyperclip

import path_runtime

CONFIG_FILENAME = "claude_desktop_config.json"
LOG_FILENAME = "orchestrator.log"

class OrchestratorUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Project Orchestrator UI")
        self.queue = queue.Queue()
        self.config_json = {}
        self._build_ui()
        self._start_log_tail()

    def _build_ui(self):
        # Project Path
        frm_top = ttk.Frame(self.root)
        frm_top.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(frm_top, text="Project Path:").pack(side=tk.LEFT)
        self.project_path_var = tk.StringVar()
        self.project_entry = ttk.Entry(frm_top, textvariable=self.project_path_var, width=50)
        self.project_entry.pack(side=tk.LEFT, padx=4)
        ttk.Button(frm_top, text="Browse", command=self._browse_project).pack(side=tk.LEFT)

        # Buttons
        frm_btn = ttk.Frame(self.root)
        frm_btn.pack(fill=tk.X, padx=8, pady=2)
        ttk.Button(frm_btn, text="Start Orchestrator", command=self._start_orchestrator).pack(side=tk.LEFT, padx=2)
        ttk.Button(frm_btn, text="Copy MCP Config", command=self._copy_config).pack(side=tk.LEFT, padx=2)

        # Tabs
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Log Tab
        self.tab_log = ttk.Frame(self.tabs)
        self.tabs.add(self.tab_log, text="Log")
        self._build_log_tab()

    def _browse_project(self):
        path = filedialog.askdirectory()
        if path:
            self.project_path_var.set(path)

    def _start_orchestrator(self):
        project_path = self.project_path_var.get().strip()
        if not project_path:
            messagebox.showerror("Error", "Please enter project path.")
            return
        server_dir = project_path
        if not os.path.exists(os.path.join(server_dir, "orchestrator_server.py")):
            candidate = path_runtime.find_orchestrator_dir(project_path)
            if candidate is not None:
                server_dir = str(candidate)
        config = {
            "mcp_server": {
                "name": "project-orchestrator",
                "path": server_dir,
                "executable": "python orchestrator_server.py"
            },
            "api": {
                "host": "localhost",
                "port": 8083
            }
        }
        try:
            config_path = os.path.join(project_path, CONFIG_FILENAME)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            self.config_json = config
            messagebox.showinfo("Success", f"Config saved to {config_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to write config: {e}")

    def _copy_config(self):
        if not self.config_json:
            messagebox.showwarning("Warning", "No config to copy. Please start orchestrator first.")
            return
        pyperclip.copy(json.dumps(self.config_json, indent=2))
        messagebox.showinfo("Copied", "MCP config copied to clipboard.")

    # R43.3 HIGH#3/#4: removed dead chat-tab methods (_build_chat_tab, _send_prompt,
    # _call_deterministic_notice, _append_chat). _build_chat_tab referenced self.tab_chat
    # which was never created by _build_ui(). _call_deterministic_notice called
    # subprocess.run() without importing subprocess. "Internal AI execution has been
    # removed from Orchestrator" — these methods were all dormant. Chat branch in
    # process_queue() also dropped (no producer remains).

    def _build_log_tab(self):
        self.log_text = tk.Text(self.tab_log, height=20, wrap=tk.NONE, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

    def _start_log_tail(self):
        self.last_log_size = 0
        self._tail_log()

    def _tail_log(self):
        log_path = LOG_FILENAME
        try:
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(self.last_log_size)
                    lines = f.read()
                    if lines:
                        self.queue.put(("log", lines))
                    self.last_log_size = f.tell()
        except Exception:
            pass
        self.root.after(1000, self._tail_log)

    def process_queue(self):
        # R43.3 HIGH#3/#4: dropped "chat" branch (producer methods removed).
        try:
            while True:
                item = self.queue.get_nowait()
                if item[0] == "log":
                    self._append_log(item[1])
        except queue.Empty:
            pass
        self.root.after(200, self.process_queue)

    def _append_log(self, text):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

def main():
    root = tk.Tk()
    app = OrchestratorUI(root)
    root.after(200, app.process_queue)
    root.mainloop()

if __name__ == "__main__":
    main()

# R43.3 HIGH#2: removed trailing demo block (40 lines: free-floating docstring,
# duplicate imports, second `class App` with threading demo, second
# `if __name__ == "__main__"`). It would have run a 2nd Tk window after
# OrchestratorUI's mainloop returned — confusing UX, accidental copy-paste leftover.
