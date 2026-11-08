# Tool definition, retrieval and response contracts

## Definition versions
Every served tool schema carries `function.definition_version`. A caller may send that value as the optional `definition_version` argument. A mismatch returns HTTP 409 / `STALE_TOOL_DEFINITION` with the current version and schema before the handler runs. Omitting it preserves compatibility. The digest includes the registry schema and the eligible bootstrap purpose.

## Retrieval
`tool_decision_hint` retains the existing recommendation and adds `ranked_tools`: up to eight candidates with `tool`, `score` and `why`. Scores are BM25/routing scores, not calibrated probabilities. Existing routing rules boost candidates.

The index uses current registry names, purposes and descriptions plus `triggers` from `orchestrator/core/tool_corpus.json`. Its `samples` are excluded from the index. Malformed or missing corpus data raises an error. Corpus changes invalidate the bounded cache by file mtime/size; registry changes invalidate the document index.

`get_rule_core_text(task_hint="...")` returns purposes for ranked eligible tools plus eligible curated tools. Without the argument the bootstrap behavior is unchanged. HTTP and the MCP shortcut both accept it.

## Response migration
The HTTP header `X-ToolA-Response` selects:
- omitted: legacy business keys retained, with an additive structured hint when relevant;
- `dual`: legacy keys plus `response_v2`;
- `2`: `{ok, result, summary, hint}`.

`result` preserves the full projected handler object, including siblings of an existing `result` key. `fields=` projects legacy handler keys before wrapping. Oversized v2 responses keep the envelope and use the existing lossless result reference/chunk protocol.

The structured hint has `next`, `why`, `chain`, `ask`, `ask_reason`. A pre-existing legacy `hint` value is retained and its structured companion is named `structured_hint`. Destructive confirmations remain enforced by the existing gates. Three identical consecutive calls within a session/agent/task epoch produce an advisory ask; the hard failure retry guard remains active. Explicit polling protocols are exempt.

Unprojected `get_usage_stats` includes process-local response migration counters. Explicit legacy projections are observable; arbitrary client reads of unprojected responses are not. The counters cannot prove that all clients have stopped reading old keys.

## Offline measurement
Run `python bin/evaluate_tool_retrieval.py <audit-tasks.jsonl>`.
Each row requires task text, expected tool names, audit_id and label_source.
The release gate requires 500 unique audit identities/tasks, Recall@8 >= 0.95 and regression <= 0.02. Independent label provenance still requires review.

For diagnostic corpus samples:
`python bin/evaluate_tool_retrieval.py orchestrator/core/tool_corpus.json --curated-corpus`.
Curated data cannot pass the audit release gate or activate the conditional embedding decision.
Exit code 2 means the release gate is not satisfied.

`--sample-hints` selects up to 50 distinct audit responses with registered hint fields by stable provenance hash. Usefulness labels are left for human review.

## Activation
Changes in this branch require normal integration and a daemon restart before they are live. Reload the MCP host for changed shortcut signatures. Read-only batching remains an unregistered experiment under `experiments/upgrade_20261105`.
