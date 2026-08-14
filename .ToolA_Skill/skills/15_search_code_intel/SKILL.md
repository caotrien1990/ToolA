---
name: search_code_intel
description: |
  Use for code search, symbol lookup, callers/usage discovery, regex/text scan, and any
  exploration that maps query → file:line. Activates when CA needs to FIND code, not edit it.
  V15 R2: search_code v2 with smart mode auto-fallback, find_symbol v2 with include=callers|usage|all.
  V15 R12: unified base64 transport via query_encoding / name_encoding fields (mode=base64 REMOVED).
trigger:
  - task_pattern: "find | where is | search | lookup | locate | callers | usage of"
  - tool: "search_code | find_symbol | query_index | get_file_symbols | regex_search | find_callers | find_definition_and_usage | import_graph | ast_query | git_blame_symbol | get_project_summary"
preconditions:
  - "Project indexed (generate_project_map ran) for semantic results"
  - "For non-indexed: live filesystem fallback available"
related_skills:
  - 07_error_schema_hints
  - 03_path_session
risk_level: low
---

# 15 — Search Code Intel

## Decision tree

```
What are you looking for?
├─ Function/class/const definition by name → find_symbol(name)
├─ All callers of symbol X → find_symbol(name, include="callers") (v2)
├─ Definition + usages in one call → find_symbol(name, include="all") (v2)
├─ Exact text/string (single token) → search_code(query, mode="literal")
├─ Multi-word phrase → search_code(query, mode="smart") [V15 R2 default]
├─ Regex pattern → search_code(query, mode="regex")
├─ Unicode/Vietnamese with diacritics → search_code(query=<b64>, query_encoding="base64") [V15 R12]
├─ Symbols in 1 file → get_file_symbols(file_path)
├─ Filter by group/loc/complexity → query_index(project_path, group="source", min_loc=100)
└─ Project overview / file tree → get_project_summary / list_structure
```

## search_code modes (V15 R12)

| Mode | When | Behavior |
|------|------|----------|
| `smart` (default) | Multi-word task description | Single token → literal; multi-word → OR-split with at least 1 token match per result |
| `literal` | Exact phrase | re.escape(query) |
| `regex` | Pattern with groups/alternation | Validated regex, IGNORECASE |

> **mode="base64" REMOVED in V15 R12.** Use `query_encoding="base64"` (transport-level) with any of the
> modes above. The query is decoded via the central `_decode_text_payload` helper, then handed to the
> selected mode. Errors surface as `INVALID_BASE64` / `INVALID_CONTENT_ENCODING`.

```jsonc
// Old (R2-R11, gone):
{ "query": "<b64>", "mode": "base64" }

// New (R12+):
{ "query": "<b64>", "query_encoding": "base64", "mode": "smart" }
```

`find_symbol` uses the same contract via `name_encoding="plain"|"base64"`.

## find_symbol v2 (V15 R2)

```
find_symbol(name, include="def|callers|usage|all", context_lines=5)
```

Returns:
```json
{
  "definition": {"file": "...", "line": N, "body": "..."},
  "callers": [{"file": "...", "line": N, "match": "..."}],
  "usages": [{"file": "...", "line": N, "match": "..."}]
}
```

Replaces 3 separate calls; saves ~5 KB context per investigation.

## Pre-call optimizations

1. **Is target a known symbol name?** → find_symbol (O(1) via INDEX)
2. **Is target free text?** → search_code smart
3. **Need full body excerpt?** → set `context_lines=10` to avoid extra read_file_range
4. **Searching across multiple projects?** → loop project_path; multi-project search planned R6

## Efficiency Notes

- Set `max_context_lines=3` (default) — bigger only when needed
- Set `max_results=20` (default) — bigger only for exhaustive scan
- Skip `drift` block from response when index just refreshed
- Reuse `range_id` from previous result to read_file_range without scan

## Anti-patterns

- ❌ `search_code(query="hidden row")` with literal default → 0 hit (use smart)
- ❌ Multiple search_code calls when find_symbol(include="all") suffices
- ❌ Reading full file when read_file_range with range_id works
- ❌ Native grep when [A] indexed search works (loses governance)

## Edge cases

| Edge | Handling |
|------|----------|
| Symbol name shadowing across files | All results returned; CA chooses |
| Unicode diacritics in query | query_encoding="base64" mandatory (V15 R12) |
| Backslash in path-filter glob | Use forward-slash or escape |
| Class method find_symbol | Searches "def name" pattern; nested methods may need find_symbol on class then get_file_symbols |
| Stale INDEX | refresh_project_map; if returns empty → generate_project_map |

## Harness

- Test 1: search_code multi-word → smart mode returns ≥1 hit per word
- Test 2: find_symbol(include="all") on 3 symbols → all return def+callers+usages
- Test 3: search_code Vietnamese query with query_encoding="base64" → exact match found
- Test 3b: search_code with mode="base64" → must FAIL with SCHEMA_VIOLATION (removed)
- Test 4: regex mode invalid pattern → error_name=INVALID_REGEX (no crash)
