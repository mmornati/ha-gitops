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
import signal
import sys
import threading
import time
from datetime import datetime, timezone
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
PANEL_INDEX = DATA_DIR / "panel.index"
LEGACY_STATUS = CONFIG_DIR / ".gitops_backup_status"
SUPERVISOR = os.environ.get("SUPERVISOR_URL", "http://supervisor")
ENGINE_TIMEOUT = int(os.environ.get("ENGINE_TIMEOUT", "1800"))  # seconds, one run
DRIFT_TTL = 15  # seconds a drift snapshot is reused
RECHECK_DELAY = 6  # the watcher keeps noting paths for 5s after a run
EMPTY_DRIFT = {"files": [], "diff": "", "truncated": False}
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
        self.state = {"paused": False, "auto_apply": True, "restart_required": False}
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
        self.mqtt_commands = bool(opts.get("mqtt_commands", True))
        self.hook_limiter = core.RateLimiter(limit=10, window=60)
        self.failures = 0        # failed sync/backup runs in a row
        self.retry_at = 0.0      # the poller holds off until then
        self.rev = 0             # bumped whenever the drift may have changed
        self.debounce: core.Debounce | None = None
        self.run_events: set[str] = set()   # paths changed while a run was on
        self.run_overflow = False
        self.retriggers = 0
        self.drift_cache: dict | None = None
        self.drift_rev = -1
        self.drift_time = 0.0
        self.drift_lock = asyncio.Lock()

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

    async def git(self, *args: str, timeout: float = 60,
                  extra_env: dict | None = None) -> tuple[int, str]:
        if not (CONFIG_DIR / ".git").exists():
            return 1, ""
        env = self.git_env()
        env.update(extra_env or {})
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(CONFIG_DIR), *args, env=env,
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
        for mode in ("discard", "dry_run", "full", "sync"):
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
        # Its own process group, so a run that hangs can be stopped as a whole
        proc = await asyncio.create_subprocess_exec(
            "bash", str(ENGINE), env=env, start_new_session=True,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

        async def pump():
            async for line in proc.stdout:
                sys.stdout.write(line.decode(errors="replace"))
            return await proc.wait()

        try:
            rc = await asyncio.wait_for(pump(), ENGINE_TIMEOUT)
        except asyncio.TimeoutError:
            rc = await self.stop_engine(proc, mode, trigger)
        if rc != 0:
            log.warning("Engine exited with %s", rc)
        self.running = False
        self.last_run_end = time.monotonic()
        self.rev += 1
        status = load_json(STATUS_PATH, {})
        if mode in ("sync", "full"):
            if rc != 0 or status.get("state") == "error":
                self.failures += 1
                self.retry_at = time.monotonic() + core.backoff_seconds(self.failures)
            else:
                self.failures, self.retry_at = 0, 0.0
        applied = status.get("applied", "") if rc == 0 else ""
        if "restart_required" in applied:
            self.set_restart_required(True)
        elif "homeassistant.restart" in applied:
            self.set_restart_required(False)
        _, head = await self.git("rev-parse", f"refs/heads/{self.base}")
        if head.strip() and not self.remote_head:
            self.remote_head = head.strip()
        await self.refresh_pr()
        await self.publish()
        asyncio.create_task(self.recheck_drift(mode))

    async def stop_engine(self, proc, mode: str, trigger: str) -> int:
        """Stops a run that went over ENGINE_TIMEOUT and records why."""
        log.error("Run %s still going after %ss; stopping it", mode, ENGINE_TIMEOUT)
        for sig, grace in ((signal.SIGTERM, 15), (signal.SIGKILL, 5)):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                await asyncio.wait_for(proc.wait(), grace)
                break
            except asyncio.TimeoutError:
                continue
        status = load_json(STATUS_PATH, {})
        status.update(state="error", detail="timeout",
                      extra=f"run stopped after {ENGINE_TIMEOUT}s", mode=mode, trigger=trigger,
                      time=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        try:
            STATUS_PATH.write_text(json.dumps(status))
            LEGACY_STATUS.write_text(f"error:timeout:{status['extra']}\n")
        except OSError as err:
            log.warning("Could not record the timeout: %s", err)
        return proc.returncode if proc.returncode else 124

    def set_restart_required(self, value: bool) -> None:
        if self.state.get("restart_required") != value:
            self.state["restart_required"] = value
            self.save_state()

    async def recheck_drift(self, mode: str) -> None:
        """Queues a backup for edits that were made while a run was on.

        The watcher cannot tell those from the files the engine itself
        rewrites (stash, pull, pop), so it only notes the paths. Once the run
        is over, any of them that is still not in the open backup PR was a
        real edit the run missed.
        """
        await asyncio.sleep(RECHECK_DELAY)
        if self.running:
            return  # the next run's check takes these over
        events, overflow = self.run_events, self.run_overflow
        self.run_events, self.run_overflow = set(), False
        if not events and not overflow:
            return
        status = load_json(STATUS_PATH, {})
        if (self.debounce is None or self.state["paused"] or mode == "dry_run"
                or status.get("state") == "error" or status.get("detail") == "dry_run"):
            return
        snap = await self.drift(force=True)
        fresh = {f["path"] for f in snap["files"] if not f["in_pr"] and not f["held"]}
        if not (fresh & events or (overflow and fresh)):
            return
        if self.retriggers >= 3:  # never loop on something a run cannot back up
            log.warning("Edits made during the last runs are still pending; waiting for a new change")
            return
        self.retriggers += 1
        log.info("Files changed during the last run; queuing a backup")
        self.debounce.touch(time.monotonic())

    # ---- commands ----------------------------------------------------------

    async def command(self, raw, source: str) -> str:
        parsed = core.parse_command(raw)
        if parsed is None:
            log.warning("Ignored unknown command from %s: %r", source, raw)
            return "unknown command"
        cmd, arg = parsed
        if cmd in core.PANEL_ONLY and source == "mqtt":
            log.warning("Refused %s over MQTT: only the panel can do that", cmd)
            return "not allowed over MQTT"
        log.info("Command from %s: %s %s", source, cmd, arg)
        result = "ok"
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
        elif cmd == "discard":
            self.request("discard", source)
        elif cmd == "merge":
            result = await self.merge_pr()
        elif cmd == "restart":
            result = await self.restart_ha()
        await self.publish()
        return result

    async def merge_pr(self) -> str:
        """Merges the open backup PR on GitHub, then syncs it down."""
        await self.refresh_pr()
        number = self.pr_info.get("number")
        if not number:
            return "there is no open backup PR"
        if not core.can_merge(number, self.pr_checks, self.running):
            return f"not merging: PR checks are {self.pr_checks}"
        repo = self.opts.get("github_repo", "")
        headers = {"Authorization": f"token {self.opts.get('github_token', '')}",
                   "Accept": "application/vnd.github+json"}
        body = {"merge_method": self.opts.get("merge_method") or "merge"}
        try:
            async with ClientSession(timeout=ClientTimeout(total=30), headers=headers) as s:
                async with s.put(f"{GITHUB_API}/repos/{repo}/pulls/{number}/merge", json=body) as r:
                    if r.status == 200:
                        log.info("Merged backup PR #%s from the panel", number)
                        self.request("sync", "merge")
                        return "ok"
                    try:
                        reason = (await r.json(content_type=None)).get("message", "")
                    except ValueError:
                        reason = ""
                    return f"GitHub refused the merge: {reason or r.status}"
        except Exception as err:  # noqa: BLE001 — network is best effort
            return f"could not reach GitHub: {err}"

    async def restart_ha(self) -> str:
        """Restarts Home Assistant, but only on a configuration that checks out."""
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            return "no access to the Home Assistant API"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with ClientSession(timeout=ClientTimeout(total=130), headers=headers) as s:
                async with s.post(f"{SUPERVISOR}/core/api/config/core/check_config", json={}) as r:
                    check = await r.json(content_type=None) if r.status == 200 else {}
                if check.get("result") != "valid":
                    return f"not restarting: config check failed ({check.get('errors') or 'no answer'})"
                self.set_restart_required(False)
                try:
                    async with s.post(f"{SUPERVISOR}/core/api/services/homeassistant/restart",
                                      json={}):
                        pass
                except Exception:  # noqa: BLE001 — HA drops the call as it goes down
                    pass
        except Exception as err:  # noqa: BLE001
            return f"could not reach Home Assistant: {err}"
        log.info("Home Assistant restart requested from the panel")
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
            # After failed runs the poller backs off (1 min, 2, 4… up to 1 h)
            # instead of retrying the same pull every interval.
            if (pull and seconds > 0 and remote and not self.state["paused"]
                    and not self.running and time.monotonic() >= self.retry_at):
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
        debounce = self.debounce = core.Debounce(quiet, max_wait=max(quiet * 5, 600))

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
                if core.watcher_ignores(rel):
                    continue
                # The engine itself rewrites files (stash/pull/pop). Those
                # cannot be told from real edits here, so they are only noted
                # and looked at once the run is over (recheck_drift).
                if self.running or time.monotonic() - self.last_run_end < 5:
                    if len(self.run_events) < 5000:
                        self.run_events.add(rel)
                    else:
                        self.run_overflow = True
                    continue
                if rel.endswith(".gitignore"):
                    self.git_ignore_cache.clear()
                if await self.is_git_ignored(rel):
                    continue
                self.rev += 1
                self.retriggers = 0
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

    async def drift(self, force: bool = False) -> dict:
        """What differs between /config and the base branch, file by file.

        Built in a scratch index, so the repository's own index and the files
        are not touched. Cached, and never rebuilt while a run is rewriting
        the tree.
        """
        now = time.monotonic()
        fresh = (self.drift_cache is not None and self.drift_rev == self.rev
                 and now - self.drift_time < DRIFT_TTL)
        if self.drift_cache is not None and (self.running or (fresh and not force)):
            return self.drift_cache
        if self.running:
            return EMPTY_DRIFT
        async with self.drift_lock:
            snap = await self.compute_drift()
        if snap is not None:
            self.drift_cache, self.drift_rev, self.drift_time = snap, self.rev, time.monotonic()
        return self.drift_cache or EMPTY_DRIFT

    async def compute_drift(self) -> dict | None:
        git_dir = CONFIG_DIR / ".git"
        if not git_dir.exists():
            return dict(EMPTY_DRIFT)
        try:
            shutil.copyfile(git_dir / "index", PANEL_INDEX)
        except OSError:
            PANEL_INDEX.unlink(missing_ok=True)
        env = {"GIT_INDEX_FILE": str(PANEL_INDEX)}
        try:
            rc, _ = await self.git("add", "-A", timeout=120, extra_env=env)
            if rc != 0:
                return None
            rc, raw = await self.git("diff", "--cached", "--name-status", "-z", "-M", "HEAD",
                                     extra_env=env)
            if rc != 0:
                return None
            files = core.parse_name_status(raw)
            held = set(load_json(STATUS_PATH, {}).get("held") or [])
            # Files the open backup PR already has exactly as they are on the box
            not_in_pr = None
            branch = self.pr_info.get("branch")
            if branch:
                ref = f"refs/heads/{branch}"
                rc, _ = await self.git("rev-parse", "-q", "--verify", ref)
                if rc == 0:
                    rc, names = await self.git("diff", "--cached", "--name-only", "-z",
                                               "--no-renames", ref, extra_env=env)
                    if rc == 0:
                        not_in_pr = set(filter(None, names.split("\0")))
            for f in files:
                f["held"] = f["path"] in held and f["status"] != "deleted"
                f["in_pr"] = (not f["held"] and not_in_pr is not None
                              and f["path"] not in not_in_pr)
            # The content of a held file is a secret: it is never sent to the panel
            exclude = [f":(exclude,literal){f['path']}" for f in files if f["held"]]
            _, diff = await self.git("diff", "--cached", "--no-color", "-M", "HEAD", "--", ".",
                                     *exclude, extra_env=env)
            truncated = len(diff) > 400_000
            return {"files": files, "diff": diff[:400_000], "truncated": truncated}
        finally:
            PANEL_INDEX.unlink(missing_ok=True)

    async def build_state(self) -> dict:
        status = load_json(STATUS_PATH, {})
        snap = await self.drift()
        self.pending = len(snap["files"])
        held = [f["path"] for f in snap["files"] if f["held"]]
        _, local = await self.git("rev-parse", f"refs/heads/{self.base}")
        local = local.strip()
        state = "running" if self.running else status.get("state", "unknown")
        number = self.pr_info.get("number", "")
        return {
            "state": state,
            "detail": status.get("detail", ""),
            "extra": status.get("extra", ""),
            "last_run": status.get("time"),
            "trigger": status.get("trigger", ""),
            "mode": status.get("mode", ""),
            "applied": status.get("applied", ""),
            "local_head": local,
            "remote_head": self.remote_head or local,
            "pending": self.pending,
            "pending_new": sum(1 for f in snap["files"] if not f["in_pr"] and not f["held"]),
            "held": held,
            "pr_number": number,
            "pr_url": self.pr_info.get("url", ""),
            "pr_branch": self.pr_info.get("branch", ""),
            "pr_checks": self.pr_checks,
            "can_merge": core.can_merge(number, self.pr_checks, self.running),
            "paused": self.state["paused"],
            "auto_apply": self.state.get("auto_apply", True),
            "restart_required": bool(self.state.get("restart_required")),
            "running": self.running,
            "retry_in": max(0, round(self.retry_at - time.monotonic())),
            "sync_mode": self.sync_mode,
            "webhook": bool(self.opts.get("webhook_secret")),
            "mqtt": self.mqtt is not None,
            "mqtt_commands": self.mqtt_commands,
            "repo": self.opts.get("github_repo", ""),
            "base": self.base,
            "version": VERSION,
            "rev": self.rev,
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
            for topic, payload in core.discovery_configs(
                    "homeassistant", MQTT_BASE, VERSION, commands=self.mqtt_commands):
                # An empty config removes an entity (mqtt_commands turned off)
                c.publish(topic, json.dumps(payload) if payload is not None else "", retain=True)
            if self.mqtt_commands:
                c.subscribe(f"{MQTT_BASE}/cmd")
            c.subscribe("homeassistant/status")
            connected.set()

        def on_message(c, _userdata, msg):
            if msg.topic == "homeassistant/status":
                if msg.payload == b"online":  # HA restarted: republish state
                    asyncio.run_coroutine_threadsafe(self.ha_started(), self.loop)
                return
            if getattr(msg, "retain", False):
                # A retained command would run again on every reconnect
                log.warning("Ignored a retained MQTT command")
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

    async def ha_started(self) -> None:
        self.set_restart_required(False)
        await self.publish()

    # ---- HTTP: webhook + ingress panel -------------------------------------

    async def handle_webhook(self, request: web.Request) -> web.Response:
        body = await request.read()
        if not core.verify_signature(self.opts.get("webhook_secret", ""), body,
                                     request.headers.get("X-Hub-Signature-256")):
            # Valid deliveries are never limited; bad ones stop being logged
            # after 10 a minute per address, so they cannot flood the log.
            count = self.hook_limiter.hit(request.remote or "?", time.monotonic())
            if count <= self.hook_limiter.limit:
                log.warning("Webhook with a bad signature from %s", request.remote)
                return web.Response(status=401, text="bad signature")
            if count == self.hook_limiter.limit + 1:
                log.warning("More bad webhook signatures from %s; not logging them for a minute",
                            request.remote)
            return web.Response(status=429, text="too many bad signatures")
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
        return web.json_response(await self.drift())

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
