# SPDX-License-Identifier: Apache-2.0
"""Makes Home Assistant tell open browser sessions that YAML dashboards changed.

Home Assistant re-reads a YAML dashboard only when a client asks for it, and
only then fires ``lovelace_updated``; ``homeassistant.reload_all`` does not.
Without this, a merged dashboard change sits on disk while every open tab
keeps showing the old cards. Asking for each YAML dashboard once over the
WebSocket API is enough.

aiohttp is imported here only, so an engine run that has no dashboard to
refresh never pays for loading it.
"""

import asyncio
from typing import Any

from aiohttp import ClientSession, ClientTimeout, ClientWebSocketResponse


async def _call(ws: ClientWebSocketResponse, msg_id: int, **msg: Any) -> Any:
    """Sends one command and returns its result (None when HA reports failure)."""
    await ws.send_json({"id": msg_id, **msg})
    while True:
        reply = await ws.receive_json()
        if reply.get("id") == msg_id and reply.get("type") == "result":
            return reply.get("result") if reply.get("success") else None


async def refresh_dashboards(url: str, token: str, limit: float = 30) -> int:
    """Fetches every YAML dashboard once; returns how many were fetched."""
    async with (
        ClientSession(timeout=ClientTimeout(total=limit)) as session,
        session.ws_connect(url) as ws,
    ):
        await ws.receive_json()  # auth_required
        await ws.send_json({"type": "auth", "access_token": token})
        if (await ws.receive_json()).get("type") != "auth_ok":
            raise OSError("Home Assistant rejected the Supervisor token")
        dashboards = await _call(ws, 1, type="lovelace/dashboards/list") or []
        # None is the default dashboard; fetching a storage one is a no-op.
        paths = [None] + [d.get("url_path") for d in dashboards if d.get("mode") == "yaml"]
        for msg_id, url_path in enumerate(paths, 2):
            await _call(ws, msg_id, type="lovelace/config", url_path=url_path, force=False)
        return len(paths) - 1


def refresh_dashboards_sync(url: str, token: str) -> int:
    return asyncio.run(refresh_dashboards(url, token))
