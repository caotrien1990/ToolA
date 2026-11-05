"""Upgrade 14 experiment. This module is never registered as a live tool."""

READ_TOOLS = frozenset({"read_file_range", "find_symbol", "search_code", "git_status"})


async def run_batch(calls, dispatch):
    """Run a bounded independent read set through the caller's normal dispatcher."""
    if not isinstance(calls, list) or not 1 <= len(calls) <= 20:
        raise ValueError("batch must contain 1..20 calls")
    for call in calls:
        if not isinstance(call, dict) or set(call) != {"tool", "payload"}:
            raise ValueError("each item requires tool and payload")
        if call["tool"] not in READ_TOOLS or not isinstance(call["payload"], dict):
            raise ValueError("only the pilot read tools are accepted")
    # Sequential execution preserves read order and avoids sharing mutable
    # dispatcher context between concurrently scheduled jobs during the pilot.
    results = []
    for call in calls:
        results.append(await dispatch(call["tool"], call["payload"]))
    return {"results": results}
