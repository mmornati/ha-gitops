# SPDX-License-Identifier: Apache-2.0
"""HTTP: the ingress panel and the GitHub webhook, on two separate ports.

The panel port is only reachable through Home Assistant's ingress proxy (and
so behind its login). The webhook port is the one that may be exposed, and it
serves nothing but ``POST /webhook``.
"""

import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiohttp import web

from ha_gitops.controller.app import Controller
from ha_gitops.rules.text import log_safe
from ha_gitops.rules.webhook import verify_signature, webhook_action

log = logging.getLogger("ha_gitops")
PANEL = Path(__file__).resolve().parent.parent.parent / "panel" / "index.html"
INGRESS_PORT = 8099
WEBHOOK_PORT = 8098
INGRESS_PEERS = frozenset({"172.30.32.2", "127.0.0.1", "::1"})
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "ha-gitops"
WEBHOOK_MAX_BYTES = 5 * 1024 * 1024
COMMAND_MAX_BYTES = 64 * 1024
HISTORY_COMMITS = 30
LOG_LINES = 300

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
CONTROLLER: web.AppKey[Controller] = web.AppKey("controller")


@web.middleware
async def ingress_only(request: web.Request, handler: Handler) -> web.StreamResponse:
    if request.remote not in INGRESS_PEERS:
        return web.Response(status=403, text="ingress only")
    if request.method != "GET" and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        return web.Response(status=403, text="missing header")
    response = await handler(request)
    # Without this the browser keeps a copy of the panel for days (it guesses
    # from the file's age) and, after an update, runs the old page against the
    # new API. The page is revalidated on every load; API answers are never
    # stored.
    response.headers["Cache-Control"] = (
        "no-store" if request.path.startswith("/api/") else "no-cache"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def panel_index(_request: web.Request) -> web.StreamResponse:
    return web.FileResponse(PANEL)


async def api_state(request: web.Request) -> web.StreamResponse:
    return web.json_response(await request.app[CONTROLLER].build_state())


async def api_diff(request: web.Request) -> web.StreamResponse:
    return web.json_response(await request.app[CONTROLLER].snapshot())


async def api_history(request: web.Request) -> web.StreamResponse:
    ctl = request.app[CONTROLLER]
    _, out = await ctl.git.run(
        "log",
        "--first-parent",
        "-n",
        str(HISTORY_COMMITS),
        "--date=iso-strict",
        "--format=%H%x09%an%x09%ad%x09%s",
        ctl.settings.base_ref,
    )
    commits = []
    for line in out.splitlines():
        sha, author, date, subject = ([*line.split("\t", 3), "", "", "", ""])[:4]
        commits.append({"sha": sha, "author": author, "date": date, "subject": subject})
    return web.json_response(
        {"commits": commits, "repo_url": f"https://github.com/{ctl.settings.github_repo}"}
    )


async def api_log(request: web.Request) -> web.StreamResponse:
    return web.json_response({"lines": request.app[CONTROLLER].store.tail_log(LOG_LINES)})


async def api_command(request: web.Request) -> web.StreamResponse:
    try:
        data = await request.json()
    except ValueError:
        return web.json_response({"result": "bad json"}, status=400)
    result = await request.app[CONTROLLER].command(data, "panel")
    return web.json_response({"result": result}, status=200 if result == "ok" else 400)


async def handle_webhook(request: web.Request) -> web.StreamResponse:
    ctl = request.app[CONTROLLER]
    body = await request.read()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_signature(ctl.settings.webhook_secret, body, signature):
        # Valid deliveries are never limited; bad ones stop being logged after
        # 10 a minute per address, so they cannot flood the log.
        peer = log_safe(request.remote or "?")
        limiter = ctl.hook_limiter
        count = limiter.hit(peer, time.monotonic())
        if count <= limiter.limit:
            log.warning("Webhook with a bad signature from %s", peer)
            return web.Response(status=401, text="bad signature")
        if count == limiter.limit + 1:
            log.warning("More bad webhook signatures from %s; not logging them for a minute", peer)
        return web.Response(status=429, text="too many bad signatures")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return web.Response(status=400, text="bad json")
    if not isinstance(payload, dict):
        return web.Response(status=400, text="bad json")
    action = webhook_action(
        request.headers.get("X-GitHub-Event"), payload, ctl.settings.base_branch
    )
    if action == "ping":
        return web.Response(text="pong")
    if action == "sync" and ctl.pulls_enabled and not ctl.paused:
        ctl.request("sync", "webhook")
        return web.Response(status=202, text="sync queued")
    return web.Response(status=202, text="ignored")


def panel_app(ctl: Controller) -> web.Application:
    app = web.Application(middlewares=[ingress_only], client_max_size=COMMAND_MAX_BYTES)
    app[CONTROLLER] = ctl
    app.add_routes(
        [
            web.get("/", panel_index),
            web.get("/api/state", api_state),
            web.get("/api/diff", api_diff),
            web.get("/api/history", api_history),
            web.get("/api/log", api_log),
            web.post("/api/command", api_command),
        ]
    )
    return app


def webhook_app(ctl: Controller) -> web.Application:
    app = web.Application(client_max_size=WEBHOOK_MAX_BYTES)
    app[CONTROLLER] = ctl
    app.add_routes([web.post("/webhook", handle_webhook)])
    return app


async def _serve(app: web.Application, port: int) -> web.AppRunner:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()  # noqa: S104 - a container port
    return runner


async def start(ctl: Controller) -> list[web.AppRunner]:
    runners = [await _serve(panel_app(ctl), INGRESS_PORT)]
    log.info("Panel listening on ingress port %s", INGRESS_PORT)
    if ctl.settings.webhook_secret:
        runners.append(await _serve(webhook_app(ctl), WEBHOOK_PORT))
        log.info("GitHub webhook listening on port %s (POST /webhook)", WEBHOOK_PORT)
    else:
        log.info("No webhook_secret set; GitHub webhook is off")
    return runners
