# Cross-protocol tool replay - tracing one `call_id` across a translating provider

Referenced by `11_repo_vs_runtime` ("Provider/protocol boundary failures"). Use it
when a resumed session replays a tool call and the error does not match what the
source says should happen. That mismatch is the signature of source truth and
runtime truth diverging **at a protocol boundary**, not of a bug in either side.

## Why the boundary can lie

[A] is reachable over two transports, and they do not share state the way they
look like they do:

- **HTTP** `/api/call/<tool>` goes straight to the daemon. The daemon owns the
  tool registry, so the daemon is the only party entitled to decide
  `UNSUPPORTED_FIELD`.
- **MCP stdio** goes through a bridge process that loaded its **own** copy of the
  registry at its own import. The bridge mints its own generation string and
  cannot re-mint it without a client reconnect - which is why
  `mcp_generation_matches_daemon:false` is normal rather than stale: every call
  still proxies to the live daemon over HTTP.

A translating provider in front of either transport adds a third registry-shaped
thing: the transcript. On resume it **reconstructs** a request from persisted
history, and reconstruction is where fields get dropped, renamed or re-defaulted.

## Trace one `call_id`, not the whole session

One call is enough, and it is far cheaper. Take the failing `call_id` and pin it
at each boundary in order, writing down the shape of the payload at that point:

1. **Persisted history** - the transcript row. Which arguments were recorded?
2. **Session audit** - `session_audit_summary`, or the events table for that
   session. Was the call classified at all, and against which policy-matrix row?
3. **What the daemon actually received** - the daemon's own log line, or re-issue
   the identical payload by hand over HTTP `/api/call/<tool>`.
4. **The answer** - compare `error_name` and the structured fields
   (`accepted_fields`, `accepted_aliases`, `corrected_payload`) against what the
   transcript says came back.

The first step where the payload changes shape is the boundary that lied. Stop
there. Tracing further only costs tokens.

## Real orphan vs reconstruction loss

These two read identically in the error text and need opposite responses.

| Evidence | Reading |
|---|---|
| id present in persisted history, but the daemon never logged a request | reconstruction loss - the replay never arrived |
| daemon logged the request and answered a structured refusal | not an orphan at all - a payload the current schema rejects |
| id absent from persisted history *and* from the events table | a real orphan - there is nothing to replay |
| alias-shaped mismatch (`from` vs `from_ref`, `repo` vs `project_path`) | translation loss: fix by mapping, never by escalating |

A client-side timeout is evidence of neither. A write that timed out on the client
may well have committed on the daemon:
`GET /api/write_status?operation_id=<id>` answers with `commit_state` +
`safe_to_retry`, and the measured case is a 10MB `write_fix` that committed 54s
after a 4s client timeout. Poll until terminal instead of resending - that habit
is what stops a replay from becoming a double write.

## Designing the one-shot compatibility retry

Non-destructive means: it may re-send, it may not re-decide.

- **Exactly one** corrected retry, built from the structured error's own
  `corrected_payload` / `accepted_aliases` mapping. If that fails, STOP and
  report - a second attempt without new evidence is a guess.
- Never repair a replay by relaxing a gate, widening a pattern, or dropping the
  field the schema refused. The refusal **is** the contract.
- Never repair it by restarting or reconnecting on suspicion. A plain daemon
  restart does not resolve a bridge-registry divergence, and a reconnect is
  warranted only when the bridge code itself changed.
- If the retry mutates anything, it needs its own authorization. A replay
  inherits the transcript's arguments; it does not inherit its approval.

## Unknowns (keep honest)

No incident of this class has been traced end to end and recorded here yet, so the
table above is derived from the documented behaviour of the two transports, not
from a post-mortem. When one is traced, write down the `call_id`, the boundary
that changed the payload, and the answer - and delete this section.
