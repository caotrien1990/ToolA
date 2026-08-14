# CHANGELOG

## 2026-07-08 — AQ_UPGRADE_PLAN_FINAL execution

- Executed the Project-Orchestrator upgrade wave across Phase 1, 3A, 3B, 3Q, and algorithm follow-up work, with final tested baseline at 385 passed / 0 failed / 1 skipped.
- Split the former monolithic registry/tool surface using the strangler sequence S1-S5a-micro: registry extraction, T1 read/fileops extraction, session/log/proc extraction, T4/interfaces extraction, T2/write package work, and MCP stdio extraction to orchestrator/server/mcp_stdio.py.
- Reduced code_tools.py from 14386 to 11377 LOC and moved active behavior into orchestrator/ packages; orchestrator_server.py reduced from 9407 to 9052 LOC in the mcp split wave.
- Consolidated SEARCH_TOOL from 9 tools to 1 canonical search_code implementation plus legacy aliases; added mode and alias compatibility tests.
- Built the council worker dispatch gate: worker dispatch without council_worker_model SET+GET now returns structured WORKER_MODEL_NOT_SET instead of silently falling back; tests cover no-model, model-set, and no-default-fallback paths.
- Completed honest dead-code batches with 12 functions / 138 LOC removed and 0 FP; original >=30 target was rescoped by Anh D10 after strong-dead supply was exhausted and blind spots were documented.
- Applied algorithm upgrades: callable-ref extraction for args/kwargs/hasattr/getattr (+1605 callable-ref edges), builtin-noise filtering, ext placeholder nodes, self-loop tagging, orphan_symbols offset/total_count/limit500, and qremer cap 5000->3000.
- Rebuilt graph.db cleanly: edges 36967->26328 after builtin-noise removal, ext placeholder nodes=1307, ORPHANED-JOIN 0, self-loops tagged=263, raw 0-inbound 1736->1426.
- Applied LSP 0.9-bucket promotions: 1907 edges promoted to confidence=1.0 with cbm_label='lsp', integrity OK.
- Verified GRAPH/JS extraction live: CBM_EXTRACT_JS=1, 336 JS/TS files, 9418 JS nodes post-reindex, JS extractor tests passing.
- Added lifecycle support for estart_orchestrator_via_bat; first real use restarted [A] successfully with health under 20s and 194 tools live.
- Added documentation/control artifacts including SOT_MAPPING.md, ALIAS_CONTRACT.md, AC_CHECKLIST_FINAL.md, and this CHANGELOG.md; alias contract checks reached 104/104 earlier and 152/152 in the final dashboard probe.
- Q-side work: BOM strip, QNight idle reset, manifest gzip rotation, Q .bak cleanup, BM25 sync, qremer packet cap, FTS incremental probe/formal test, and Q split slice 1 (clm_lib/config.py + store.py) completed. Q retire v1/v2 was rolled back after A/B top-1 6/10 and rescoped by Anh D5 to keep v1/v2 forever.
- Known remaining/deferred items from the final audit: Q split slices 2-5, Q endpoint/Q7 batch verification, S25 benchmark, lint CI, and final README/semver evidence were not present in the required SOT docs for this CI task.