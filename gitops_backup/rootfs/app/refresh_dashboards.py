#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Makes Home Assistant tell open browser sessions that YAML dashboards changed.

Home Assistant re-reads a YAML dashboard only when a client asks for it, and
only then fires `lovelace_updated`; `homeassistant.reload_all` does not. Without
this, a merged dashboard change sits on disk while every open tab keeps showing
the old cards. Asking for each YAML dashboard once over the WebSocket API is
enough. Same idea as upstream askb/ha-gitops 0.5.3, on aiohttp.

Exit status 0 when the dashboards were fetched, 1 otherwise (never fatal for
the engine).
"""

from __future__ import annotations

import asyncio
import os
import sys

from aiohttp import ClientSession, ClientTimeout

HA_WS = os.environ.get("HA_WS", "ws://supervisor/core/websocket")


async def call(ws, msg_id: int, **msg):
    """Sends one command and returns its result (None when HA reports failure)."""
    await ws.send_json({"id": msg_id, **msg})
    while True:
        reply = await ws.receive_json()
        if reply.get("id") == msg_id and reply.get("type") == "result":
            return reply.get("result") if reply.get("success") else None


async def refresh(url: str, token: str) -> int:
    """Fetches every YAML dashboard once; returns how many were fetched."""
    async with ClientSession(timeout=ClientTimeout(total=30)) as session:
        async with session.ws_connect(url) as ws:
            await ws.receive_json()  # auth_required
            await ws.send_json({"type": "auth", "access_token": token})
            if (await ws.receive_json()).get("type") != "auth_ok":
                raise OSError("Home Assistant rejected the Supervisor token")
            dashboards = await call(ws, 1, type="lovelace/dashboards/list") or []
            # None is the default dashboard; fetching a storage one is a no-op.
            paths = [None] + [d.get("url_path") for d in dashboards if d.get("mode") == "yaml"]
            for msg_id, url_path in enumerate(paths, 2):
                await call(ws, msg_id, type="lovelace/config", url_path=url_path, force=False)
            return len(paths) - 1


def main() -> int:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return 1
    try:
        count = asyncio.run(refresh(HA_WS, token))
    except Exception as err:  # noqa: BLE001 — best effort, the engine logs it
        print(f"Dashboard refresh failed: {err}")
        return 1
    print(f"Refreshed {count} YAML dashboard(s) for open browser sessions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
