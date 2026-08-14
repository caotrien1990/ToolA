# ComC Solar RAG Scan Prompt

You are ComC, model `MiniMaxAI/MiniMax-M3-FREE`, doing a read-only deep scan.

Produce a RAG-style "solar system" map of [A]'s rule, tool, skill, workflow, council/broker, and related Python implementation system.

Output must be Markdown and must start:
`# RAG Solar System Rule Map`

Output must end:
`END_RAG_SOLAR_SYSTEM_RULE_MAP`

Required structure:

1. `## Scan Scope And Evidence`
   - list inspected path groups
   - list any unknown/unreadable areas

2. `## Solar System Overview`
   - "Sun" = core rule/bootstrap/transport
   - inner orbit = council/broker/tool matrix
   - middle orbit = skills/workflows/BR output plans
   - outer orbit = tests/research/cache/support artifacts

3. `## Node Catalog`
   For each node include:
   - `node_id`
   - `node_type`: `rule | tool | skill | workflow | py_file | config | test | artifact | guardrail`
   - `path`
   - `purpose`
   - `current_strength`
   - `protected_behavior`
   - `depends_on`
   - `used_by`
   - `related_tools`
   - `related_skills`
   - `risk_if_changed`
   - `allowed_change_type`
   - `forbidden_change_type`
   - `evidence_paths`

4. `## Relationship Graph`
   - compact arrows, for example `core_rule -> transport_policy -> council_message -> broker_filtered.jsonl`

5. `## Strong Behaviors To Preserve`
   - what [A] already does well and should not be weakened by future plans

6. `## Fragile Seams And Known Edge Cases`
   - include ComC broker D:/BR workspace read/write issue
   - include same-identity Codo/CodoC `sender=user` edge case
   - include full-path/no-hidden-context dispatch gate
   - include council tracker canonical log investigation

7. `## Guardrail Seeds`
   - draft guardrail statements only; do not finalize the guardrail

8. `## RAG Retrieval Index`
   - retrieval queries future agents can ask
   - answer should point to nodes and evidence paths

Rules:
- Deep-scan [A] read-only.
- Prefer evidence paths over speculation.
- Do not paste long code.
- Do not edit files.
- Do not restart [A].
- Do not write output files; return the artifact inline for Codo to save.
- If a source is unknown or unreadable, say `UNKNOWN / not verified`.

