# Dispatch Template

Use full absolute Windows paths. Broker workers have no memory.

```text
Broker one-shot: no previous context. Do not rely on chat history.

You are <WORKER_ID>, equal peer worker for <PROJECT> Round <N>.
Required staged full paths:
- prompt_file: <ABSOLUTE_PATH>
- context_file: <ABSOLUTE_PATH>
- research_file: <ABSOLUTE_PATH>
- own_previous_plan: <ABSOLUTE_PATH_OR_none>
- peer_plan_1: <ABSOLUTE_PATH_OR_none>
- peer_plan_2: <ABSOLUTE_PATH_OR_none>
- official_output_path: <ABSOLUTE_PATH>

Task: <SELF_CONTAINED_TASK_DESCRIPTION>
Round rule: Round 1 has no peer scoring. Round 2+ scores peers /10, extracts strengths, rejects weaknesses, and improves.
End marker: END_<WORKER_ID>_R<N>_ARTIFACT
```

Reject before dispatch if the prompt contains:
- `current loop`
- `existing artifact`
- `failed dispatch`
- `same as before`
- relative paths
- env-only paths
- fixed judge/builder/QA roles
- Codo as a scored peer
