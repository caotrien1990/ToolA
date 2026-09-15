# Long operation watchdog contract

Explicit repository-scale indexing has no wall-clock work deadline. It runs under
a progress watchdog and remains valid while progress advances.

## Progress-watchdog operations

- `cbm_index`, `cbm_incremental_index`
- `generate_project_map`, `refresh_project_map`
- project cache assembly, external reconcile, snapshot walk/capture/publication/GC
- graph extraction and cross-file edge rebuild phases

These operations expose `operation_id`; callers poll `write_status`. Client/MCP
timeouts do not imply failure. Cancellation is cooperative through `/api/cancel`.

## Bounded interactive operations

Status/query/search/analysis calls must not own a full rebuild. They may bound
response latency and return partial, stale, unknown or an explicit-refresh hint.
`cbm_graph_status` returns `stale_scan.complete=false` and `freshness=unknown`
when its response-budget scan is incomplete.

## Valid bounded waits

Timeouts remain valid for queue admission, lock acquisition, network and browser
waits, process readiness, and short advisory analyses with partial/fail-open output.
They are not valid as a repository-size work ceiling.
