#!/bin/bash
# Phase 1.5 B4 - Run 50 pre_search queries against [Q] and capture results
set -u
OUTDIR="/d/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-orchestrator/phase1p5_raw"
mkdir -p "$OUTDIR"

run_query() {
  local id="$1"
  local q="$2"
  local outfile="$OUTDIR/q${id}.json"
  curl -s -X POST http://127.0.0.1:8082/pre_search \
    -H "Content-Type: application/json; charset=utf-8" \
    -d "{\"planned_action\":\"$q\",\"agent_id\":\"hermc5\",\"top_k\":5}" \
    > "$outfile" 2>&1
  echo "done q${id}"
}

# Group A: code/function lookup (15 queries) - phrased as search intent
run_query A01 "search memory for cbm_index function in orchestrator" &
run_query A02 "find information about graph_find_symbol tool in ToolQ" &
run_query A03 "look up write_fix function usage in orchestrator codebase" &
run_query A04 "search memory for council_message HTTP endpoint implementation" &
run_query A05 "find docs about manage_agent tool and sync_rule action" &
run_query A06 "search memory for get_rule_core_text API endpoint" &
run_query A07 "look up desktop_screenshot tool in orchestrator" &
run_query A08 "find information about describe_page DOM inspection tool" &
run_query A09 "search memory for convene_council leader routing function" &
run_query A10 "find docs about refresh_project_map tool" &
run_query A11 "search memory for log_native_event function in orchestrator" &
run_query A12 "look up read_file_range tool for project files" &
run_query A13 "find information about broker_filtered.jsonl cross-agent transcript" &
run_query A14 "search memory for Q_S.bat launch script for ToolQ" &
run_query A15 "find docs about council_worker_model routing" &
wait
echo "=== Group A done ==="

# Group B: continuity/history (15 queries)
run_query B01 "search memory for A_Q devine upgrade project history" &
run_query B02 "find information about QSleep feature development" &
run_query B03 "look up council routing changes recent sessions" &
run_query B04 "search memory for rawcut video editing project" &
run_query B05 "find history about phase1p5 benchmark planning" &
run_query B06 "search memory for breakdown plan second iteration" &
run_query B07 "look up orchestrator transport policy changes" &
run_query B08 "find memory about council broker dispatch routing" &
run_query B09 "search memory for HermC OpeC worker model assignment" &
run_query B10 "find information about gatekeeper model deepseek-v4-flash" &
run_query B11 "search memory for context pool write feature" &
run_query B12 "look up path_session skill development history" &
run_query B13 "find memory about tool_tiers skill creation" &
run_query B14 "search memory for error_schema_hints skill" &
run_query B15 "find history about broker-worker-bootstrap setup" &
wait
echo "=== Group B done ==="

# Group C: factual recall (10 queries)
run_query C01 "search memory for Q librarian port 8082 configuration" &
run_query C02 "find information about orchestrator port 8083 API" &
run_query C03 "look up OpeC5 model slot GLM 5.2 configuration" &
run_query C04 "search memory for HermC5 GLM model assignment" &
run_query C05 "find information about ComC3 xiaomi mimo-v2.5-pro model" &
run_query C06 "search memory for OpeC7 deepseek-v4-pro high judgement slot" &
run_query C07 "look up Q_S_install_task.ps1 path for ToolQ" &
run_query C08 "find memory about clm-librarian-api memory layer" &
run_query C09 "search memory for taaq-protocol memory first query" &
run_query C10 "find information about Q project path D drive ToolQ" &
wait
echo "=== Group C done ==="

# Group D: ambiguous (10 queries)
run_query D01 "search memory for fix loi path issue recent" &
run_query D02 "find information about restart the loop problem" &
run_query D03 "look up broken function recent debugging" &
run_query D04 "search memory for port conflict issue resolution" &
run_query D05 "find information about model not responding issue" &
run_query D06 "search memory for upgrade plan next steps" &
run_query D07 "look up connection refused error recent" &
run_query D08 "search memory for config change recent session" &
run_query D09 "find information about memory cleanup task" &
run_query D10 "search memory for routing problem recent" &
wait
echo "=== Group D done ==="
echo "=== ALL DONE ==="
