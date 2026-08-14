# Q Protocol Reference

Use this only when implementing or debugging the Q gate.

Base URL: `http://127.0.0.1:8082`

Endpoints:
- `POST /pre_search`
- `POST /query`
- `GET /help`

Never probe endpoints with empty GET/POST. Use `/help` for schema discovery, then call `/pre_search` or `/query` once with valid JSON and `Content-Type: application/json; charset=utf-8`.

Valid `/pre_search` payload example:

```json
{
  "prompt": "search old council loop memory before clarify",
  "planned_action": "search memory before clarify",
  "agent_id": "codo",
  "top_k": 2
}
```

Valid `/query` payload example:

```json
{
  "q": "old council loop memory before clarify",
  "agent_id": "codo",
  "top_k": 2
}
```

Default flow:
1. Build a short intent.
2. Call `/pre_search`.
3. If result is `hint` and `auto_queried: true`, consume the returned `hits` directly. Do not call `/query` again for the same search decision.
4. Rerank/drop weak results.
5. Inject only bounded relevant context.
6. If `no`, error, or timeout, continue without retrying Q.
7. A leader must not replace failed/no Q with leader-side filesystem/session/history scanning. Report Q status and dispatch bounded data gathering to a worker.

Direct query flow:
- Keep `/query` for explicit Anh requests such as "query ..." or for debugging/direct lookup.
- Use `/query` when the caller already knows it wants memory results and does not need a gate.

Drop a Q result when it is stale, duplicate, low-relevance, noisy, truncated, context-free, or unrelated to the current task.

Use `top_k` 1-3 for `/pre_search`. Increase `/query` beyond 3 only for explicit broad audit/history tasks, capped at 5.
