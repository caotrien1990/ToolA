# WORKFLOW_T1 - Read / Analyze

T1 is the default mode. It is read-only and should load only the next needed workflow step, schema, hint, and skill.

## Step T1.0 - Health And Session Gate

Goal: prove [A] is reachable and bind the user-mentioned project path to a session before project reads.

Allowed tools: `open_session`, `get_project_summary`, `tool_decision_hint`.

Hint source: tool inspector for the selected tool only.

Schema source: `/api/tool_inspector/{tool}` first, then `/api/tools` entry for the same tool.

Skill route: `00_skill_router` if unclear, otherwise the exact skill named by `tool_decision_hint`.

Guardrail: user-mentioned paths require `open_session` before reads; no native project reads after health OK.

Validation: session id exists or the next response is an ASK.

## Step T1.1 - Summary Routing

Goal: learn the smallest project summary needed to choose the next read.

Allowed tools: `get_project_summary`, `project_map`, `tool_decision_hint`.

Hint source: `get_project_summary` inspector.

Schema source: selected tool schema only.

Skill route: `00_skill_router`.

Guardrail: do not read broad docs or folders unless the summary proves they are relevant.

Validation: name the next exact file, symbol, or search query.

## Step T1.2 - Indexed Search

Goal: find candidate files or symbols without scanning the repo manually.

Allowed tools: `search_code`, `find_symbol`, `regex_search`, `find_callers`, `find_definition_and_usage`, `import_graph`, `ast_query`.

Hint source: search/code-intel inspector for the chosen tool.

Schema source: chosen search tool schema.

Skill route: `15_search_code_intel`.

Guardrail: keep result limits tight and expand one hop only when the current result is insufficient.

Validation: candidate list contains relative paths and reasons.

## Step T1.3 - Exact Read

Goal: read only the relevant ranges needed for the next decision.

Allowed tools: `read_file_range`, `read_many`, `get_file_metadata`.

Hint source: read tool inspector.

Schema source: chosen read tool schema.

Skill route: the skill selected in T1.2, not a new broad skill.

Guardrail: prefer bounded ranges; no T2 action unless the user has explicitly triggered EXECUTE.

Validation: state the observed behavior and the minimal proposed next step.

## Step T1.4 - Matrix Certification Read

Goal: check whether workflow docs, schema phase metadata, and UI affordances are statically aligned.

Allowed tools: `progressive_matrix_check`.

Hint source: `progressive_matrix_check` inspector.

Schema source: `progressive_matrix_check` schema.

Skill route: `18_runtime_dom_T4` for T4/UI rows, otherwise `00_skill_router`.

Guardrail: matrix success is not runtime proof; T4 browser/server behavior still requires T4 harnesses.

Validation: report gaps and classify each as static-only or runtime-required.

## Step T1.5 - Handoff Decision

Goal: end in ANALYZE or move to the single next workflow owner.

Allowed tools: `tool_decision_hint`, `create_build_context`.

Hint source: the chosen owner tool inspector.

Schema source: selected next tool schema.

Skill route: one matching skill only.

Guardrail: do not eager-load all hints, schemas, or skills.

Validation: response ends in ANALYZE unless the user gives an EXECUTE trigger.
