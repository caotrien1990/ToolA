# EXEC_CODOC_H1H4

Project: D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-orchestrator
File edited: orchestrator_server.py

## Fix summary

- H1 Popen resource leak
  - Before: original orchestrator_server.py lines 4422-4437 created proc and called _council_communicate_idle without try/finally cleanup.
  - After: current lines 4425-4456 keep proc creation, wrap request_id registration and _council_communicate_idle in try/finally, kill/wait on communicate exception, and close proc stdout/stderr in finally.

- H2 silent async dispatch failure
  - Before: original lines 6525-6535 had async _bg() with except Exception: pass.
  - After: current lines 6544-6553 catch Exception as exc and logger.error with exc_info=True while preserving the outer ok/dispatched response behavior.

- H3 async dispatch create_task reference leak
  - Before: original line 6535 used bare asyncio.create_task(_bg()).
  - After: current lines 6554-6556 store the task in _COUNCIL_PENDING_TASKS and discard it via done callback.

- H4 queue drain create_task reference leak
  - Before: original line 2588 used bare asyncio.create_task(_council_queue_drain(agent)).
  - After: current lines 2589-2591 store the drain task in _COUNCIL_PENDING_TASKS and discard it via done callback.

- Shared support for H3/H4
  - Before: no module-level pending task set near _COUNCIL_AGENT_QUEUE_DRAINING.
  - After: current line 2206 defines _COUNCIL_PENDING_TASKS: set[asyncio.Task] = set().

## Verification

- After each write_fix: validate_file ok and validate_symbols ok.
- Final command: python -m py_compile orchestrator_server.py
- Final py_compile result: exit_code 0, stdout empty, stderr empty.
- No orchestrator restart performed.

END_EXEC_CODOC_H1H4