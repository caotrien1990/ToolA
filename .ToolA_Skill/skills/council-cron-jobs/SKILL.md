---
name: council-cron-jobs
description: Create, update, inspect, trigger, and delete durable council schedules that pin a worker/model and optional narrator/model. Use for council cron, scheduled workers, scheduled narrators, and recurring council jobs.
---

# Council Cron Jobs

## Mandatory Measurement Closeout

Inherit the leader Route Measurement Gate. Every dispatch prompt must carry TASK_ID, OBJECTIVE_CLASS, RISK_LEVEL, DEV_LEVEL, LOAD_BUDGET_SOURCE, INDEPENDENT_QUESTIONS, FILES_SUBSYSTEMS, EXPECTED_TOOL_CALLS, EXPECTED_WRITES, DEPENDENCY_DEPTH, and EXPECTED_END_MARKER. After each reply/timeout, verify artifact+marker and refresh/read `council_route_scorecard`. Transport `ok` is not artifact/quality PASS; insufficient homogeneous samples keep capacity `unknown/not verified`.
Who may use it
Desktop CA, temporary council leader, and HTTP-only council workers may call these tools through [A] HTTP. A worker does not become leader by creating a schedule. Scheduled jobs remain broker-only and use the configured worker/model pair.

## Tools
- council_cron_job_list(job_id?, limit?): read jobs and run history.
- council_cron_job_save(...): create/update. Requires name, worker, model, prompt, schedule_type, schedule_value. Optional narrator fields pin narrator worker/model.
- council_cron_job_trigger(job_id): run now; overlap-safe.
- council_cron_job_delete(job_id): soft delete; run history remains.

## Routing contract
1. Read council_worker_model schema/list and choose a model from the worker's curated catalog.
2. Save both worker + model. Provider is derived when omitted; pass explicit provider only when live mapping requires it.
3. For narration, save narrator_enabled=true plus narrator_worker + narrator_model.
4. Schedules: schedule_type=interval with 60s..7d, or schedule_type=cron with five UTC fields.
5. Prompt must be self-contained, use full paths, include stop conditions, and never assume prior worker memory.
6. Create/update/trigger/delete need EXECUTE authorization when initiated by a CA. A user-authored active schedule may fire autonomously afterward.
7. Classify `RISK_LEVEL` and `DEV_LEVEL` before saving the schedule. High-risk scheduled mutations require an encoded human/mission gate and independent high-end review; no cheap route may be sole authority. Choose worker/model by the live route table: PiC+DeepSeek for bounded scan, PiC+Qwen for architecture/logic, KiloC+MiniMax for routine implementation/recovery, OpeC+Qwen for operational review, CodoC+GPT-5.6 Sol for high-end judgement. ClauC is exceptional-only; Kimi K3 is default-off.
8. A recurring job is one bounded acceptance objective with one output artifact. The current benchmark has no validated per-worker capacity limit, so split multi-stage or multi-subsystem schedules into dependency-ordered jobs and serialize overlapping writers.

## Safety
- One active run per job; duplicate overlap returns CRON_JOB_OVERLAP.
- Startup marks interrupted runs and resumes future schedules.
- Invalid workers/models fail loudly; never silently reroute.
- Scheduled mutation/destructive prompts still require an approved mission/human gate encoded in the task; cron is not an authorization bypass.
