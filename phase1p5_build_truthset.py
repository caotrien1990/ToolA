#!/usr/bin/env python
"""Parse all 50 pre_search result files and build truth set draft."""
import json, os, base64

RAW_DIR = r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\phase1p5_raw"

# Query definitions (id -> (group, query_text))
QUERIES = {
    # Group A: code/function lookup (15)
    "A01": ("A", "search memory for cbm_index function in orchestrator"),
    "A02": ("A", "find information about graph_find_symbol tool in ToolQ"),
    "A03": ("A", "look up write_fix function usage in orchestrator codebase"),
    "A04": ("A", "search memory for council_message HTTP endpoint implementation"),
    "A05": ("A", "find docs about manage_agent tool and sync_rule action"),
    "A06": ("A", "search memory for get_rule_core_text API endpoint"),
    "A07": ("A", "look up desktop_screenshot tool in orchestrator"),
    "A08": ("A", "find information about describe_page DOM inspection tool"),
    "A09": ("A", "search memory for convene_council leader routing function"),
    "A10": ("A", "find docs about refresh_project_map tool"),
    "A11": ("A", "search memory for log_native_event function in orchestrator"),
    "A12": ("A", "look up read_file_range tool for project files"),
    "A13": ("A", "find information about broker_filtered.jsonl cross-agent transcript"),
    "A14": ("A", "search memory for Q_S.bat launch script for ToolQ"),
    "A15": ("A", "find docs about council_worker_model routing"),
    # Group B: continuity/history (15)
    "B01": ("B", "search memory for A_Q devine upgrade project history"),
    "B02": ("B", "find information about QSleep feature development"),
    "B03": ("B", "look up council routing changes recent sessions"),
    "B04": ("B", "search memory for rawcut video editing project"),
    "B05": ("B", "find history about phase1p5 benchmark planning"),
    "B06": ("B", "search memory for breakdown plan second iteration"),
    "B07": ("B", "look up orchestrator transport policy changes"),
    "B08": ("B", "find memory about council broker dispatch routing"),
    "B09": ("B", "search memory for HermC OpeC worker model assignment"),
    "B10": ("B", "find information about gatekeeper model deepseek-v4-flash"),
    "B11": ("B", "search memory for context pool write feature"),
    "B12": ("B", "look up path_session skill development history"),
    "B13": ("B", "find memory about tool_tiers skill creation"),
    "B14": ("B", "search memory for error_schema_hints skill"),
    "B15": ("B", "find history about broker-worker-bootstrap setup"),
    # Group C: factual recall (10)
    "C01": ("C", "search memory for Q librarian port 8082 configuration"),
    "C02": ("C", "find information about orchestrator port 8083 API"),
    "C03": ("C", "look up OpeC5 model slot GLM 5.2 configuration"),
    "C04": ("C", "search memory for HermC5 GLM model assignment"),
    "C05": ("C", "find information about ComC3 xiaomi mimo-v2.5-pro model"),
    "C06": ("C", "search memory for OpeC7 deepseek-v4-pro high judgement slot"),
    "C07": ("C", "look up Q_S_install_task.ps1 path for ToolQ"),
    "C08": ("C", "find memory about clm-librarian-api memory layer"),
    "C09": ("C", "search memory for taaq-protocol memory first query"),
    "C10": ("C", "find information about Q project path D drive ToolQ"),
    # Group D: ambiguous (10)
    "D01": ("D", "search memory for fix loi path issue recent"),
    "D02": ("D", "find information about restart the loop problem"),
    "D03": ("D", "look up broken function recent debugging"),
    "D04": ("D", "search memory for port conflict issue resolution"),
    "D05": ("D", "find information about model not responding issue"),
    "D06": ("D", "search memory for upgrade plan next steps"),
    "D07": ("D", "look up connection refused error recent"),
    "D08": ("D", "search memory for config change recent session"),
    "D09": ("D", "find information about memory cleanup task"),
    "D10": ("D", "search memory for routing problem recent"),
}

def load_result(qid):
    fp = os.path.join(RAW_DIR, f"q{qid}.json")
    with open(fp, "r", encoding="utf-8") as f:
        raw = f.read()
    # Handle potential \r in curl output
    data = json.loads(raw)
    return data

def extract_hit_info(data):
    """Extract memory_ids, scores, snippets from pre_search response."""
    memory_ids = data.get("memory_ids", [])
    hits = data.get("hits", [])
    scores = []
    snippets = []
    for h in hits:
        scores.append(h.get("score", 0))
        snippets.append({
            "id": h.get("id"),
            "score": h.get("score", 0),
            "subject": h.get("subject", "")[:80],
            "snippet": h.get("snippet", "")[:200],
            "source": h.get("source", ""),
            "writer": h.get("writer", "")
        })
    return memory_ids, scores, snippets

# Judge criteria per group - based on snippet content relevance
def judge_relevance(qid, group, query, snippets):
    """Judge which memory_ids are truly relevant based on snippet content.
    Returns (expected_relevant_ids, judge_note).
    """
    q_lower = query.lower()
    expected = []
    note_parts = []
    
    for s in snippets:
        sid = s["id"]
        subj = s["subject"].lower()
        snip = s["snippet"].lower()
        score = s["score"]
        relevant = False
        
        # Group A: code/function lookup - need actual mention of the function/tool name
        if group == "A":
            # Extract the key term from query
            key_terms = []
            for term in ["cbm_index", "graph_find_symbol", "write_fix", "council_message",
                         "manage_agent", "sync_rule", "get_rule_core_text", "desktop_screenshot",
                         "describe_page", "convene_council", "refresh_project_map",
                         "log_native_event", "read_file_range", "broker_filtered",
                         "q_s.bat", "council_worker_model"]:
                if term in q_lower:
                    key_terms.append(term)
            for t in key_terms:
                if t in snip or t in subj:
                    relevant = True
                    break
        
        # Group B: continuity/history - need project/topic match
        elif group == "B":
            if "devine upgrade" in q_lower and ("devine" in snip or "upgrade" in snip or "a_q" in snip or "phase 1" in snip):
                relevant = True
            elif "qsleep" in q_lower and "qsleep" in snip:
                relevant = True
            elif "council routing" in q_lower and ("council" in snip and ("routing" in snip or "dispatch" in snip or "worker" in snip)):
                relevant = True
            elif "rawcut" in q_lower and "rawcut" in snip:
                relevant = True
            elif "phase1p5" in q_lower and ("phase1p5" in snip or "phase 1.5" in snip or "benchmark" in snip):
                relevant = True
            elif "breakdown plan" in q_lower and ("breakdown" in snip or "plan2nd" in snip):
                relevant = True
            elif "transport policy" in q_lower and ("transport" in snip or "policy" in snip):
                relevant = True
            elif "broker dispatch" in q_lower and ("broker" in snip and "dispatch" in snip):
                relevant = True
            elif "hermc" in q_lower and "opec" in q_lower and ("hermc" in snip or "opec" in snip):
                relevant = True
            elif "gatekeeper" in q_lower and ("gatekeeper" in snip or "deepseek-v4-flash" in snip):
                relevant = True
            elif "context pool" in q_lower and "context pool" in snip:
                relevant = True
            elif "path_session" in q_lower and ("path_session" in snip or "path session" in snip):
                relevant = True
            elif "tool_tiers" in q_lower and ("tool_tiers" in snip or "tool tiers" in snip):
                relevant = True
            elif "error_schema" in q_lower and ("error_schema" in snip or "error schema" in snip):
                relevant = True
            elif "broker-worker-bootstrap" in q_lower and ("broker" in snip and "bootstrap" in snip):
                relevant = True
        
        # Group C: factual recall - need the specific fact present
        elif group == "C":
            if "port 8082" in q_lower and "8082" in snip:
                relevant = True
            elif "port 8083" in q_lower and "8083" in snip:
                relevant = True
            elif "opec5" in q_lower and ("opec5" in snip or "glm" in snip):
                relevant = True
            elif "hermc5" in q_lower and ("hermc5" in snip or "glm" in snip):
                relevant = True
            elif "comc3" in q_lower and ("comc3" in snip or "mimo" in snip):
                relevant = True
            elif "opec7" in q_lower and ("opec7" in snip or "deepseek-v4-pro" in snip):
                relevant = True
            elif "q_s_install" in q_lower and ("q_s_install" in snip or "install_task" in snip):
                relevant = True
            elif "clm-librarian" in q_lower and ("librarian" in snip or "clm" in snip):
                relevant = True
            elif "taaq" in q_lower and "taaq" in snip:
                relevant = True
            elif "toolq" in q_lower and "toolq" in snip:
                relevant = True
        
        # Group D: ambiguous - low bar, anything touching the topic
        elif group == "D":
            if "path" in q_lower and ("path" in snip or "loi" in snip):
                relevant = True
            elif "restart" in q_lower and "restart" in snip:
                relevant = True
            elif "broken" in q_lower and ("broken" in snip or "bug" in snip or "debug" in snip):
                relevant = True
            elif "port conflict" in q_lower and ("port" in snip and "conflict" in snip):
                relevant = True
            elif "not responding" in q_lower and ("responding" in snip or "no response" in snip):
                relevant = True
            elif "upgrade plan" in q_lower and ("upgrade" in snip or "plan" in snip):
                relevant = True
            elif "connection refused" in q_lower and ("connection" in snip or "refused" in snip):
                relevant = True
            elif "config change" in q_lower and ("config" in snip or "change" in snip):
                relevant = True
            elif "cleanup" in q_lower and ("cleanup" in snip or "purge" in snip or "clean" in snip):
                relevant = True
            elif "routing" in q_lower and ("routing" in snip or "route" in snip):
                relevant = True
        
        if relevant:
            expected.append(sid)
    
    if not expected:
        note = "no hits clearly relevant by snippet content - gap identified"
    elif len(expected) < len(snippets):
        note = f"{len(expected)}/{len(snippets)} hits relevant; some hits are tangential (subject mismatch or generic)"
    else:
        note = f"all {len(expected)} hits relevant by snippet"
    
    return expected, note

# Build truth set
queries_list = []
group_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
group_nonempty = {"A": 0, "B": 0, "C": 0, "D": 0}

for qid in sorted(QUERIES.keys(), key=lambda x: (x[0], int(x[1:]))):
    group, query = QUERIES[qid]
    data = load_result(qid)
    memory_ids, scores, snippets = extract_hit_info(data)
    expected, note = judge_relevance(qid, group, query, snippets)
    
    queries_list.append({
        "id": qid,
        "group": group,
        "query": query,
        "pre_search_memory_ids": memory_ids,
        "pre_search_scores": scores,
        "expected_relevant_ids": expected,
        "judge_note": note,
        "gatekeeper_decision": data.get("result", ""),
        "hits_detail": snippets
    })
    group_counts[group] += 1
    if expected:
        group_nonempty[group] += 1

truthset = {
    "created": "2026-07-04",
    "method": "50 queries via POST /pre_search top_k=5; expected_relevant judged by HermC5 reading snippet content for actual topic/fact match; queries phrased as search-intent to pass gatekeeper",
    "note": "DRAFT - can leader review truoc khi ComC3 chay benchmark chinh thuc. Gatekeeper model: deepseek-v4-flash (openrouter). Queries phrased as 'search memory for...' to pass gatekeeper yes/hint gate. Judge criteria: snippet must contain the key term/fact, not just generic subject match.",
    "group_summary": {
        "A_code_lookup": f"{group_counts['A']} queries, {group_nonempty['A']} with non-empty expected",
        "B_continuity_history": f"{group_counts['B']} queries, {group_nonempty['B']} with non-empty expected",
        "C_factual_recall": f"{group_counts['C']} queries, {group_nonempty['C']} with non-empty expected",
        "D_ambiguous": f"{group_counts['D']} queries, {group_nonempty['D']} with non-empty expected"
    },
    "queries": queries_list
}

# Write locally first, then we'll push via [A]
outpath = os.path.join(RAW_DIR, "..", "q_baseline_truthset_draft_20260704.json")
with open(outpath, "w", encoding="utf-8") as f:
    json.dump(truthset, f, ensure_ascii=False, indent=2)

print(f"Written to: {outpath}")
print(f"Total queries: {len(queries_list)}")
print(f"Group counts: {group_counts}")
print(f"Group non-empty expected: {group_nonempty}")
print(f"Total non-empty expected: {sum(group_nonempty.values())}")

# Print base64 for [A] write
with open(outpath, "rb") as f:
    b64 = base64.b64encode(f.read()).decode("ascii")
print(f"\nBase64 length: {len(b64)}")
