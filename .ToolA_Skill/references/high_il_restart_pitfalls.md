# High-IL restart pitfalls - verify checklist + admin rebind recipe

Referenced by `16_server_lifecycle` (its Reference section) and by
`01_bootstrap_health` (NO_8083 after schtasks/restart helper). The narrative and
the root cause live in `skills/16_server_lifecycle/SKILL.md` under "High-IL task
short-circuit pitfall"; this file is the operational checklist, so neither skill
has to carry it twice.

## Rule zero: NO_8083 is a diagnosis, not a retry

`schtasks /Run` answering LastResult=1 with port 8083 free means the launch chain
broke. Running it again runs the same broken chain. The skills name three causes;
check them in this order, cheapest check first:

| # | Cause | How to tell | Fix owner |
|---|-------|-------------|-----------|
| 1 | High-IL `&&` short-circuit | task action still contains `restart_orchestrator.py && start ...` | admin rebind (Anh) |
| 2 | Import crash | `orchestrator.log` tail ends in a traceback, no listen line | CA fixes source, then relaunch |
| 3 | Lock race | `.orchestrator_cache/orchestrator.lock*` present while nothing owns the port | CA clears the stale lock only |

Anything outside those three: STOP and report. Do not widen the loop.

## Verify checklist (in order, stop at the first red)

1. **Listen** - is anything on 8083? `port_owner_inspect` names the owning PID,
   or says the port is free. Free port plus LastResult=1 is cause #1 or #2.
2. **Task action** - `Get-ScheduledTask -TaskName ToolA_Orchestrator` Actions.
   `ArgsHasAndAnd=true` is fragile; `ArgsHasWrapper=true` is the healthy shape.
3. **Task result** - `Get-ScheduledTaskInfo` LastTaskResult. 0 together with a
   fresh `.orchestrator_cache/high_il_start.log` is the healthy High-IL path.
4. **Log tail** - `orchestrator.log`. An import-time `NameError` (the classic:
   JSON-style `true`/`false` written into a Python literal) means the process
   never reached its listen call, so no number of restarts will help.
5. **Lock** - `.orchestrator_cache/orchestrator.lock*`. Clear it ONLY when step 1
   proved nothing owns the port. A lock file is not proof of a live owner, and a
   live owner is not proof of a lock file.
6. **New PID and new generation** - the only acceptance evidence. `/health`
   answering `ok` is not enough, because the old process answers `ok` too. The
   generation string must differ from the one recorded before the restart.

Never claim a restart succeeded on `/health ok` alone. Never claim it failed
because the port was briefly refused - poll, do not conclude.

## Admin rebind recipe (Anh runs this, once)

CA cannot do this step: a non-admin `Set-ScheduledTask` answers **Access denied**,
and looping it produces nothing but denials.

1. Open an **elevated** PowerShell. Do not send Anh to right-click the `.ps1`:
   Win11 often offers no admin entry there.
2. `.\install_orchestrator_admin_task.ps1` - repoints the task action at
   `start_orchestrator_high_il.cmd` instead of the `restart && start` chain.
3. `schtasks /Run /TN ToolA_Orchestrator`.
4. Verify with the checklist above: `ArgsHasWrapper=true`, LastResult=0, a fresh
   `high_il_start.log`, and a new generation on `/health`.

The soft-clear fix (`restart_orchestrator.py` exiting 0 when there is nothing to
kill) can make the old `&&` chain work *temporarily*. That is a reprieve, not a
repair - the rebind is what makes it survive a reboot.

## Medium-IL bootstrap (temporary, when Anh is not available)

When the High-IL task ended the old instance and then failed to relaunch it, CA
may bootstrap at medium IL to keep working:

1. Confirm no listener on 8083 and no orchestrator python process; clear a stale
   lock only under that condition.
2. Start detached: `venv312/Scripts/python.exe -u orchestrator_server.py` from
   the project root.
3. Poll `/health` until ok, and record the new generation.
4. Report to Anh anyway - medium-IL lasts until logoff; the admin rebind is what
   lasts across a reboot.

## What this file deliberately does not contain

No count of tasks, wrappers, log files or tools. A population written next to the
thing that owns it always rots; ask the scheduler and ask the daemon.
