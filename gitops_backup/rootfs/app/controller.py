# SPDX-License-Identifier: Apache-2.0
"""HA GitOps controller.

Decides when the git engine (gitops_backup.sh) runs, and lets Home Assistant
see and drive it:

* triggers: schedule, remote poll, GitHub webhook, file watcher, commands
* commands: stdin (hassio.addon_stdin), MQTT buttons/switches, ingress panel
* state: MQTT discovery entities (or one REST sensor without MQTT)

The engine keeps doing all git work and holds its own lock; the controller
only queues runs, one at a time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

import gitops_core as core

try:
    import paho.mqtt.client as paho
except ImportError:  # MQTT is optional
    paho = None

APP_DIR = Path(__file__).resolve().parent
ENGINE = APP_DIR / "gitops_backup.sh"
PANEL = APP_DIR / "panel" / "index.html"
OPTIONS_PATH = Path(os.environ.get("OPTIONS", "/data/options.json"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/homeassistant"))
STATE_PATH = DATA_DIR / "controller_state.json"
STATUS_PATH = DATA_DIR / "status.json"
OPEN_PR_PATH = DATA_DIR / "open_pr"
LOG_PATH = DATA_DIR / "gitops_backup.log"
SUPERVISOR = "http://supervisor"
GITHUB_API = os.environ.get("GITHUB_API", "https://api.github.com")
INGRESS_PORT = 8099
WEBHOOK_PORT = 8098
INGRESS_PEERS = {"172.30.32.2", "127.0.0.1", "::1"}
MQTT_BASE = "ha_gitops"
VERSION = os.environ.get("BUILD_VERSION", "dev")

log = logging.getLogger("ha_gitops")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


class Controller:
    def __init__(self, opts: dict):
        self.opts = opts
        self.base = opts.get("base_branch") or "main"
        self.sync_mode = opts.get("sync_mode") or "both"
        self.state = {"paused": False, "auto_apply": True}
        self.state.update(load_json(STATE_PATH, {}))
        self.jobs: dict[str, dict] = {}
        self.reverts: list[str] = []
        self.wakeup = asyncio.Event()
        self.running = False
        self.last_run_end = 0.0
        self.remote_head = ""
        self.pr_checks = "none"
        self.pr_info: dict = {}
        self.pending = 0
        self.mqtt = None
        self.loop = asyncio.get_running_loop()
        self.git_ignore_cache: dict[str, bool] = {}

    # ---- helpers -----------------------------------------------------------

    def git_env(self) -> dict:
        env = dict(os.environ)
        env["GIT_ASKPASS"] = str(APP_DIR / "git_askpass.sh")
        env["GITOPS_TOKEN"] = self.opts.get("github_token", "")
        env["GIT_TERMINAL_PROMPT"] = "0"
        # /config is owned by another uid than the app: trust it explicitly,
        # also before the engine's first run has set safe.directory.
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "safe.directory"
        env["GIT_CONFIG_VALUE_0"] = str(CONFIG_DIR)
        return env

    async def git(self, *args: str, timeout: float = 60) -> tuple[int, str]:
        if not (CONFIG_DIR / ".git").exists():
            return 1, ""
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(CONFIG_DIR), *args, env=self.git_env(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 1, ""
        return proc.returncode, out.decode(errors="replace")

    def save_state(self) -> None:
        STATE_PATH.write_text(json.dumps(self.state))

    # ---- job queue ---------------------------------------------------------

    def request(self, mode: str, trigger: str, **env) -> None:
        """Queues an engine run. A queued full run also covers a sync."""
        if mode == "revert":
            self.reverts.append(env["REVERT_SHA"])
        else:
            self.jobs.setdefault(mode, {"trigger": trigger, "env": env})
        self.wakeup.set()

    def next_job(self):
        if self.reverts:
            return "revert", "panel", {"REVERT_SHA": self.reverts.pop(0)}
        for mode in ("dry_run", "full", "sync"):
            if mode in self.jobs:
                job = self.jobs.pop(mode)
                if mode == "full":
                    self.jobs.pop("sync", None)
                return mode, job["trigger"], job["env"]
        return None

    async def worker(self) -> None:
        while True:
            await self.wakeup.wait()
            self.wakeup.clear()
            while (job := self.next_job()) is not None:
                await self.run_engine(*job)

    async def run_engine(self, mode: str, trigger: str, extra: dict) -> None:
        env = self.git_env()
        env.update({k: str(v) for k, v in extra.items()})
        env["GITOPS_TRIGGER"] = trigger
        env["GITOPS_MODE"] = "full" if mode == "dry_run" else mode
        if mode == "dry_run":
            env["DRY_RUN_OVERRIDE"] = "true"
        if not self.state.get("auto_apply", True):
            env["APPLY_OVERRIDE"] = "off"
        log.info("Run: %s (trigger: %s)", mode, trigger)
        self.running = True
        await self.publish()
        proc = await asyncio.create_subprocess_exec(
            "bash", str(ENGINE), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        async for line in proc.stdout:
            sys.stdout.write(line.decode(errors="replace"))
        rc = await proc.wait()
        if rc != 0:
            log.warning("Engine exited with %s", rc)
        self.running = False
        self.last_run_end = time.monotonic()
        _, head = await self.git("rev-parse", f"refs/heads/{self.base}")
        if head.strip() and not self.remote_head:
            self.remote_head = head.strip()
        await self.refresh_pr()
        await self.publish()

    # ---- commands ----------------------------------------------------------

    async def command(self, raw, source: str) -> str:
        parsed = core.parse_command(raw)
        if parsed is None:
            log.warning("Ignored unknown command from %s: %r", source, raw)
            return "unknown command"
        cmd, arg = parsed
        log.info("Command from %s: %s %s", source, cmd, arg)
        if cmd == "sync":
            self.request("sync", source)
        elif cmd == "backup":
            self.request("full", source)
        elif cmd == "dry_run":
            self.request("dry_run", source)
        elif cmd == "revert":
            self.request("revert", source, REVERT_SHA=arg)
        elif cmd in ("pause", "resume"):
            self.state["paused"] = cmd == "pause"
            self.save_state()
        elif cmd in ("auto_apply_on", "auto_apply_off"):
            self.state["auto_apply"] = cmd == "auto_apply_on"
            self.save_state()
        elif cmd == "refresh":
            await self.refresh_pr()
        await self.publish()
        return "ok"

    def stdin_reader(self) -> None:
        for line in sys.stdin:
            asyncio.run_coroutine_threadsafe(self.command(line, "stdin"), self.loop)

    # ---- triggers ----------------------------------------------------------

    async def scheduler(self) -> None:
        hours = int(self.opts.get("interval_hours") or 24)
        if self.opts.get("run_at_start"):
            self.request("full", "start")
        while True:
            await asyncio.sleep(hours * 3600)
            if not self.state["paused"]:
                self.request("full", "schedule")

    async def poller(self) -> None:
        seconds = int(self.opts.get("poll_seconds") or 0)
        pull = self.sync_mode in ("pull", "both")
        interval = seconds if (pull and seconds > 0) else 300
        last_pr_refresh = 0.0
        while True:
            await asyncio.sleep(interval)
            rc, out = await self.git("ls-remote", "origin", f"refs/heads/{self.base}", timeout=30)
            remote = out.split()[0] if rc == 0 and out.strip() else ""
            if remote and remote != self.remote_head:
                self.remote_head = remote
                await self.publish()
            if pull and seconds > 0 and remote and not self.state["paused"] and not self.running:
                _, local = await self.git("rev-parse", f"refs/heads/{self.base}")
                local = local.strip()
                if local and remote != local:
                    rc, _ = await self.git("merge-base", "--is-ancestor", remote, local)
                    if rc != 0:  # unknown locally, or not yet merged in
                        self.request("sync", "poll")
            if time.monotonic() - last_pr_refresh > 300:
                last_pr_refresh = time.monotonic()
                await self.refresh_pr()
                await self.publish()

    async def is_git_ignored(self, rel: str) -> bool:
        if rel in self.git_ignore_cache:
            return self.git_ignore_cache[rel]
        rc, _ = await self.git("check-ignore", "-q", "--", rel)
        ignored = rc == 0
        if len(self.git_ignore_cache) > 5000:
            self.git_ignore_cache.clear()
        self.git_ignore_cache[rel] = ignored
        return ignored

    async def watcher(self) -> None:
        if self.sync_mode not in ("push", "both"):
            return
        if not shutil.which("inotifywait"):
            log.warning("inotifywait not found; file watching is off")
            return
        quiet = int(self.opts.get("debounce_seconds") or 120)
        debounce = core.Debounce(quiet, max_wait=max(quiet * 5, 600))

        async def fire():
            while True:
                await asyncio.sleep(1)
                if debounce.due(time.monotonic()):
                    debounce.reset()
                    if not self.state["paused"]:
                        self.request("full", "watcher")

        asyncio.create_task(fire())
        prefix = str(CONFIG_DIR).rstrip("/") + "/"
        while True:
            proc = await asyncio.create_subprocess_exec(
                "inotifywait", "-m", "-r", "-q",
                "-e", "close_write,create,delete,moved_to,moved_from",
                "--format", "%w%f", "--exclude", core.INOTIFY_EXCLUDE, str(CONFIG_DIR),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            log.info("Watching %s for changes (debounce %ss)", CONFIG_DIR, quiet)
            async for raw in proc.stdout:
                path = raw.decode(errors="replace").rstrip("\n")
                rel = path[len(prefix):] if path.startswith(prefix) else path
                # The engine itself rewrites files (stash/pull/pop): ignore those
                if self.running or time.monotonic() - self.last_run_end < 5:
                    continue
                if rel.endswith(".gitignore"):
                    self.git_ignore_cache.clear()
                if core.watcher_ignores(rel) or await self.is_git_ignored(rel):
                    continue
                debounce.touch(time.monotonic())
            err = (await proc.stderr.read()).decode(errors="replace").strip()
            log.warning("File watcher stopped (%s); restarting in 60s", err or proc.returncode)
            await asyncio.sleep(60)

    # ---- GitHub PR info ----------------------------------------------------

    async def refresh_pr(self) -> None:
        line = OPEN_PR_PATH.read_text().strip() if OPEN_PR_PATH.exists() else ""
        fields = (line.split("\t") + ["", "", ""])[:3]
        branch, url, number = fields
        self.pr_info = {"branch": branch, "url": url, "number": number}
        if not branch:
            self.pr_checks = "none"
            return
        repo = self.opts.get("github_repo", "")
        headers = {"Authorization": f"token {self.opts.get('github_token', '')}",
                   "Accept": "application/vnd.github+json"}
        try:
            async with ClientSession(timeout=ClientTimeout(total=20), headers=headers) as s:
                async with s.get(f"{GITHUB_API}/repos/{repo}/commits/{branch}/check-runs") as r:
                    runs = await r.json() if r.status == 200 else None
                async with s.get(f"{GITHUB_API}/repos/{repo}/commits/{branch}/status") as r:
                    status = await r.json() if r.status == 200 else None
            self.pr_checks = core.summarize_checks(runs, status)
        except Exception as err:  # noqa: BLE001 — network is best effort
            log.debug("PR check refresh failed: %s", err)
            self.pr_checks = "unknown"

    # ---- state publishing --------------------------------------------------

    async def build_state(self) -> dict:
        status = load_json(STATUS_PATH, {})
        rc, porcelain = await self.git("status", "--porcelain")
        self.pending = len([ln for ln in porcelain.splitlines() if ln.strip()]) if rc == 0 else 0
        _, local = await self.git("rev-parse", f"refs/heads/{self.base}")
        state = "running" if self.running else status.get("state", "unknown")
        return {
            "state": state,
            "detail": status.get("detail", ""),
            "extra": status.get("extra", ""),
            "last_run": status.get("time"),
            "trigger": status.get("trigger", ""),
            "applied": status.get("applied", ""),
            "local_head": local.strip(),
            "remote_head": self.remote_head or local.strip(),
            "pending": self.pending,
            "pr_number": self.pr_info.get("number", ""),
            "pr_url": self.pr_info.get("url", ""),
            "pr_branch": self.pr_info.get("branch", ""),
            "pr_checks": self.pr_checks,
            "paused": self.state["paused"],
            "auto_apply": self.state.get("auto_apply", True),
            "running": self.running,
            "sync_mode": self.sync_mode,
            "webhook": bool(self.opts.get("webhook_secret")),
            "version": VERSION,
        }

    async def publish(self) -> None:
        state = await self.build_state()
        if self.mqtt is not None:
            self.mqtt.publish(f"{MQTT_BASE}/state", json.dumps(state), retain=True)
            self.mqtt.publish(f"{MQTT_BASE}/pr", json.dumps(
                {"url": state["pr_url"], "branch": state["pr_branch"],
                 "checks": state["pr_checks"]}), retain=True)
            self.mqtt.publish(f"{MQTT_BASE}/update", json.dumps({
                "installed_version": (state["local_head"] or "unknown")[:7],
                "latest_version": (state["remote_head"] or "unknown")[:7],
                "release_url": state["pr_url"] or None,
            }), retain=True)
        else:
            await self.publish_rest(state)

    async def publish_rest(self, state: dict) -> None:
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            return
        body = {"state": state["state"], "attributes": dict(
            state, friendly_name="HA GitOps status", icon="mdi:source-branch-check")}
        try:
            async with ClientSession(timeout=ClientTimeout(total=10)) as s:
                await s.post(f"{SUPERVISOR}/core/api/states/sensor.ha_gitops_status",
                             json=body, headers={"Authorization": f"Bearer {token}"})
        except Exception as err:  # noqa: BLE001
            log.debug("REST state publish failed: %s", err)

    async def setup_mqtt(self) -> None:
        if not self.opts.get("mqtt_discovery", True):
            return
        if paho is None:
            log.warning("paho-mqtt missing; using the REST sensor instead")
            return
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            return
        try:
            async with ClientSession(timeout=ClientTimeout(total=10)) as s:
                async with s.get(f"{SUPERVISOR}/services/mqtt",
                                 headers={"Authorization": f"Bearer {token}"}) as r:
                    if r.status != 200:
                        log.info("No MQTT broker available; using the REST sensor")
                        return
                    cfg = (await r.json()).get("data", {})
        except Exception as err:  # noqa: BLE001
            log.info("MQTT service lookup failed (%s); using the REST sensor", err)
            return
        try:
            client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="ha_gitops")
        except AttributeError:  # paho-mqtt 1.x
            client = paho.Client(client_id="ha_gitops")
        if cfg.get("username"):
            client.username_pw_set(cfg["username"], cfg.get("password"))
        if cfg.get("ssl"):
            client.tls_set()
        client.will_set(f"{MQTT_BASE}/availability", "offline", retain=True)
        connected = threading.Event()

        def on_connect(c, *_args):
            c.publish(f"{MQTT_BASE}/availability", "online", retain=True)
            for topic, payload in core.discovery_configs("homeassistant", MQTT_BASE, VERSION):
                c.publish(topic, json.dumps(payload), retain=True)
            c.subscribe(f"{MQTT_BASE}/cmd")
            c.subscribe("homeassistant/status")
            connected.set()

        def on_message(c, _userdata, msg):
            if msg.topic == "homeassistant/status":
                if msg.payload == b"online":  # HA restarted: republish state
                    asyncio.run_coroutine_threadsafe(self.publish(), self.loop)
                return
            asyncio.run_coroutine_threadsafe(self.command(msg.payload, "mqtt"), self.loop)

        client.on_connect = on_connect
        client.on_message = on_message
        try:
            client.connect(cfg.get("host", "core-mosquitto"), int(cfg.get("port", 1883)))
        except OSError as err:
            log.warning("MQTT connect failed (%s); using the REST sensor", err)
            return
        client.loop_start()
        await self.loop.run_in_executor(None, connected.wait, 15)
        self.mqtt = client
        log.info("MQTT discovery published (device: HA GitOps)")

    # ---- HTTP: webhook + ingress panel -------------------------------------

    async def handle_webhook(self, request: web.Request) -> web.Response:
        body = await request.read()
        if not core.verify_signature(self.opts.get("webhook_secret", ""), body,
                                     request.headers.get("X-Hub-Signature-256")):
            log.warning("Webhook with a bad signature from %s", request.remote)
            return web.Response(status=401, text="bad signature")
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            return web.Response(status=400, text="bad json")
        action = core.webhook_action(request.headers.get("X-GitHub-Event"), payload, self.base)
        if action == "ping":
            return web.Response(text="pong")
        if action == "sync" and self.sync_mode in ("pull", "both") and not self.state["paused"]:
            self.request("sync", "webhook")
            return web.Response(status=202, text="sync queued")
        return web.Response(status=202, text="ignored")

    @web.middleware
    async def ingress_only(self, request: web.Request, handler):
        if request.remote not in INGRESS_PEERS:
            return web.Response(status=403, text="ingress only")
        if request.method == "POST" and request.headers.get("X-Requested-With") != "ha-gitops":
            return web.Response(status=403, text="missing header")
        return await handler(request)

    async def panel_index(self, _request):
        return web.FileResponse(PANEL)

    async def api_state(self, _request):
        return web.json_response(await self.build_state())

    async def api_diff(self, _request):
        _, porcelain = await self.git("status", "--porcelain")
        _, diff = await self.git("diff", "--no-color", "HEAD")
        if len(diff) > 400_000:
            diff = diff[:400_000] + "\n… diff truncated …\n"
        return web.json_response({"files": porcelain.splitlines(), "diff": diff})

    async def api_history(self, _request):
        _, out = await self.git("log", "--first-parent", "-n", "30", "--date=iso-strict",
                                "--format=%H%x09%an%x09%ad%x09%s", f"refs/heads/{self.base}")
        commits = []
        for line in out.splitlines():
            sha, author, date, subject = (line.split("\t", 3) + ["", "", "", ""])[:4]
            commits.append({"sha": sha, "author": author, "date": date, "subject": subject})
        repo = self.opts.get("github_repo", "")
        return web.json_response({"commits": commits, "repo_url": f"https://github.com/{repo}"})

    async def api_log(self, _request):
        try:
            lines = LOG_PATH.read_text(errors="replace").splitlines()[-300:]
        except OSError:
            lines = []
        return web.json_response({"lines": lines})

    async def api_command(self, request: web.Request):
        try:
            data = await request.json()
        except ValueError:
            return web.json_response({"result": "bad json"}, status=400)
        result = await self.command(data, "panel")
        return web.json_response({"result": result}, status=200 if result == "ok" else 400)

    async def start_http(self) -> None:
        panel = web.Application(middlewares=[self.ingress_only])
        panel.add_routes([
            web.get("/", self.panel_index),
            web.get("/api/state", self.api_state),
            web.get("/api/diff", self.api_diff),
            web.get("/api/history", self.api_history),
            web.get("/api/log", self.api_log),
            web.post("/api/command", self.api_command),
        ])
        runner = web.AppRunner(panel, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", INGRESS_PORT).start()
        log.info("Panel listening on ingress port %s", INGRESS_PORT)

        if self.opts.get("webhook_secret"):
            hook = web.Application(client_max_size=5 * 1024 * 1024)
            hook.add_routes([web.post("/webhook", self.handle_webhook)])
            hrunner = web.AppRunner(hook, access_log=None)
            await hrunner.setup()
            await web.TCPSite(hrunner, "0.0.0.0", WEBHOOK_PORT).start()
            log.info("GitHub webhook listening on port %s (POST /webhook)", WEBHOOK_PORT)
        else:
            log.info("No webhook_secret set; GitHub webhook is off")

    # ---- main --------------------------------------------------------------

    async def run(self) -> None:
        log.info("HA GitOps controller %s starting (sync_mode=%s, poll=%ss, every %sh)",
                 VERSION, self.sync_mode, self.opts.get("poll_seconds"),
                 self.opts.get("interval_hours"))
        await self.setup_mqtt()
        await self.start_http()
        await self.refresh_pr()
        await self.publish()
        threading.Thread(target=self.stdin_reader, daemon=True).start()
        await asyncio.gather(self.worker(), self.scheduler(), self.poller(), self.watcher())


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout)
    opts = load_json(OPTIONS_PATH, {})
    controller = Controller(opts)
    await controller.run()


if __name__ == "__main__":
    asyncio.run(main())
