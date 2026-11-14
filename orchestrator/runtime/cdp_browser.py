"""Browser-socket CDP for Chrome's default-profile remote-debugging switch."""
import asyncio
import json
import os
import re
from pathlib import Path

import aiohttp


def browser_endpoint(port):
    """Match the caller's port against the active profile file, never guess a port."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    path = Path(local) / "Google" / "Chrome" / "User Data" / "DevToolsActivePort"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) < 2 or int(lines[0]) != int(port):
            return None
        socket_path = lines[1].strip()
        if not re.fullmatch(r"/devtools/browser/[A-Za-z0-9-]+", socket_path):
            return None
        return f"ws://127.0.0.1:{int(port)}{socket_path}"
    except (OSError, ValueError):
        return None


async def _request(ws, call_id, method, params, session_id=None):
    request = {"id": call_id, "method": method, "params": params}
    if session_id is not None:
        request["sessionId"] = session_id
    await ws.send_json(request)
    async for message in ws:
        if message.type == aiohttp.WSMsgType.TEXT:
            response = json.loads(message.data)
            if response.get("id") == call_id:
                return response
        elif message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                              aiohttp.WSMsgType.ERROR):
            break
    raise ConnectionError("Browser socket closed before the matching CDP response")


async def browser_targets(endpoint):
    async with asyncio.timeout(8):
        async with aiohttp.ClientSession() as client:
            async with client.ws_connect(endpoint, max_msg_size=4 * 1024 * 1024) as ws:
                response = await _request(ws, 1, "Target.getTargets", {})
                if response.get("error"):
                    raise RuntimeError(response["error"].get("message", "Target.getTargets failed"))
                return [dict(info, id=info["targetId"], browserWebSocketDebuggerUrl=endpoint)
                        for info in response["result"]["targetInfos"]]


async def browser_dispatch(endpoint, target_id, method, params):
    """A flattened session pins every method to the selected target."""
    async with asyncio.timeout(8):
        async with aiohttp.ClientSession() as client:
            async with client.ws_connect(endpoint, max_msg_size=4 * 1024 * 1024) as ws:
                attached = await _request(ws, 1, "Target.attachToTarget",
                                          {"targetId": target_id, "flatten": True})
                if attached.get("error"):
                    return attached
                session_id = attached["result"]["sessionId"]
                # Exiting ws_connect releases the flattened attachment as well.
                # Return the command response without a second command deadline.
                return await _request(ws, 2, method, params, session_id)
