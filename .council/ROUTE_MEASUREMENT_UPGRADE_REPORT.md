# ROUTE MEASUREMENT UPGRADE — LEADER UPDATE

Status: PASS for the scoped six-point upgrade (2026-07-25).

## What changed

1. Normalized dispatch telemetry
   - Every new broker run now records request_id, reply_event_id, harness/runtime_key, provider/model, timestamps, duration, completion/error and timeout kind.
   - Canonical log: `.council/route_measurements.jsonl`.

2. Explicit task metadata
   - Every dispatch prompt must include: TASK_ID, OBJECTIVE_CLASS, RISK_LEVEL, DEV_LEVEL, LOAD_BUDGET_SOURCE.
   - Missing metadata remains `unknown`; runtime never guesses risk/dev.

3. Independent load dimensions
   - INDEPENDENT_QUESTIONS, FILES_SUBSYSTEMS, EXPECTED_TOOL_CALLS, EXPECTED_WRITES, DEPENDENCY_DEPTH and measured prompt bytes are stored separately.
   - These fields support later capacity experiments; current numeric capacity remains unknown/not verified.

4. Artifact and response quality
   - Measurement separates `transport_ok`, artifact exists/non-zero/end-marker, DONE marker, tests reported and `quality_verified`.
   - `ok=true` alone is not a quality PASS.

5. Cost and efficiency
   - Records response tokens when available, output bytes, configured cost rank and quality-per-cost proxy.
   - Estimated money cost stays `unknown` unless provider billing evidence is available.

6. Route scorecard
   - New T1 tool: `council_route_scorecard`.
   - Writes `.council/route_scorecard.json` and `.council/route_scorecard.md`.
   - Buckets by exact harness + model + objective + risk + dev, with completion, timeout, artifact validity, p50/p95 duration, quality proxy, confidence and recommendation.
   - Minimum default: five homogeneous samples. Below threshold => `insufficient_evidence`; numeric capacity => `unknown`.

## Leader dispatch procedure

Before dispatch, load `leader-council-skill`, classify/split the task, set+get the exact worker model, and include this envelope:

```text
TASK_ID: <stable id>
OBJECTIVE_CLASS: <bounded class>
RISK_LEVEL: low | mid | high
DEV_LEVEL: low | mid | high
LOAD_BUDGET_SOURCE: benchmark | observed-history | conservative-heuristic
INDEPENDENT_QUESTIONS: <integer>
FILES_SUBSYSTEMS: <integer>
EXPECTED_TOOL_CALLS: <integer or unknown>
EXPECTED_WRITES: <integer>
DEPENDENCY_DEPTH: <integer>
EXPECTED_END_MARKER: END_<WORKER>_R<ROUND>_<TASK>
```

Use one bounded acceptance objective and one exclusive artifact per shard. Keep overlapping writes serialized.

After reply or timeout:

1. Check the artifact first.
2. Require exists + non-zero + exact final end marker.
3. Distinguish transport success from artifact/test/quality evidence.
4. Call/read `council_route_scorecard`.
5. Do not promote/demote a route from a single case or heterogeneous samples.
6. Mid/high-risk route changes require independent review.
7. If measurement is missing, mark `measurement_incomplete`; do not synthesize a benchmark conclusion.

## Interpretation rules

- `confidence=insufficient`: keep existing conservative task-fit route; collect more homogeneous samples.
- `recommendation=preferred|conditional|avoid`: valid only after the homogeneous threshold.
- `numeric_capacity=unknown`: no measured maximum task size exists yet.
- `eligible_for_controlled_estimation`: enough homogeneous samples exist to begin a controlled capacity experiment; it is not itself a numeric capacity.
- Cost rank order encoded for available benchmark models: DeepSeek < MiniMax < Qwen < Kimi < GPT-5.6 Sol.

## Canonical files

- `orchestrator/council/route_metrics.py`
- `orchestrator_server.py`
- `code_tools.py`
- `orchestrator/core/registry_data.py`
- `tests/test_council_route_metrics.py`
- `.ToolA_Skill/skills/leader-council-skill/SKILL.md`
- `.ToolA_Skill/skills/council-brainstorm-loop/SKILL.md`
- `.ToolA_Skill/skills/council-console-loop/SKILL.md`
- `.ToolA_Skill/skills/council-cron-jobs/SKILL.md`
- `.council/council_rule_leader.md`
- `.council/global_rule_worker.md`
- `.council/council_config.json`

## Verification evidence

- Route metric unit tests: 8 passed.
- Integrated council/server contract suite: 204 passed.
- Six-point static audit: PASS, no missing contract fields.
- Tool matrix audit: 230/230 in sync, drift_count=0.
- Runtime restart verified: generation changed from `7a5d208239cb` to `b141f66f0cf4`.
- Live `council_route_scorecard` schema and call verified.
- Initial scorecard correctly reports 0 new normalized measurements and no fabricated capacity.
- Rule synced across 7 CA targets; four council skills synced across all 7 targets.

## Remaining evidence boundary

Historical broker events were not retroactively labeled with guessed objective/risk/dev/load fields. They remain useful qualitative evidence, but numeric capacity and homogeneous route confidence start accumulating from new normalized dispatches.