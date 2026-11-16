# SPDX-License-Identifier: Apache-2.0
"""The HA GitOps controller.

Decides when the engine runs, and lets Home Assistant see and drive it:

* triggers: schedule, remote poll, GitHub webhook, file watcher, commands
* commands: stdin (hassio.addon_stdin), MQTT buttons/switches, ingress panel
* state: MQTT discovery entities (or one REST sensor without MQTT)

The engine does all git work and holds its own lock; the controller only
queues runs, one at a time.
"""

import asyncio
import contextlib
import logging
import sys
import threading
import time
from typing import Any

from ha_gitops.adapters.git import git_env
from ha_gitops.adapters.github import GitHub
from ha_gitops.adapters.hass import HassError, HomeAssistant
from ha_gitops.adapters.state import StateStore
from ha_gitops.controller import triggers, web
from ha_gitops.controller.drift import DriftService
from ha_gitops.controller.engine_runner import EngineRunner
from ha_gitops.controller.gitio import AsyncGit
from ha_gitops.controller.jobs import Job, JobQueue
from ha_gitops.controller.mqtt import MqttBridge
from ha_gitops.controller.pulls import PullRequestMonitor
from ha_gitops.rules.commands import PANEL_ONLY, parse_command
from ha_gitops.rules.text import log_safe
from ha_gitops.rules.timing import Debounce, RateLimiter, backoff_seconds
from ha_gitops.settings import Settings

log = logging.getLogger("ha_gitops")
STATUS_ENTITY = "sensor.ha_gitops_status"
MAX_RETRIGGERS = 3  # never loop on something a run cannot back up


class Controller:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = StateStore(settings.config_dir, settings.data_dir)
        self.flags: dict[str, Any] = {
            "paused": False,
            "auto_apply": True,
            "restart_required": False,
            **self.store.controller_state(),
        }
        self.loop = asyncio.get_running_loop()
        self.queue = JobQueue()
        self.git = AsyncGit(settings.config_dir, git_env(settings))
        self.hass = HomeAssistant(settings.supervisor_url, settings.supervisor_token)
        self.pulls = PullRequestMonitor(
            GitHub(settings.github_api, settings.github_repo, settings.github_token), self.store
        )
        self.drift = DriftService(self.git, self.store)
        self.runner = EngineRunner(self.store, settings.engine_timeout)
        self.mqtt = MqttBridge(
            self.hass,
            version=settings.version,
            commands=settings.mqtt_commands,
            on_command=lambda raw: self._from_thread(self.command(raw, "mqtt")),
            on_ha_start=lambda: self._from_thread(self.ha_started()),
        )
        self.hook_limiter = RateLimiter(limit=10, window=60)
        self.running = False
        self.last_run_end = 0.0
        self.remote_head = ""
        self.failures = 0  # failed sync/backup runs in a row
        self.retry_at = 0.0  # the poller holds off until then
        self.debounce: Debounce | None = None
        self.run_events: set[str] = set()  # paths changed while a run was on
        self.run_overflow = False
        self.retriggers = 0
        self.recheck_delay = 6.0  # the watcher keeps noting paths for 5s after a run
        self.ignore_cache: dict[str, bool] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    # ---- helpers -----------------------------------------------------------

    def _from_thread(self, coro: Any) -> None:
        asyncio.run_coroutine_threadsafe(coro, self.loop)

    def spawn(self, coro: Any) -> None:
        """A background task that is kept alive until it is done."""
        task = self.loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def save_flags(self) -> None:
        self.store.set_controller_state(self.flags)

    def set_restart_required(self, value: bool) -> None:
        if self.flags.get("restart_required") != value:
            self.flags["restart_required"] = value
            self.save_flags()

    @property
    def paused(self) -> bool:
        return bool(self.flags["paused"])

    @property
    def pulls_enabled(self) -> bool:
        return self.settings.sync_mode in ("pull", "both")

    @property
    def pushes_enabled(self) -> bool:
        return self.settings.sync_mode in ("push", "both")

    def request(self, mode: str, trigger: str, revert_sha: str = "") -> None:
        self.queue.request(mode, trigger, revert_sha)

    # ---- running the engine ------------------------------------------------

    async def worker(self) -> None:
        while True:
            await self.queue.wait()
            while (job := self.queue.next()) is not None:
                await self.run_job(job)

    async def run_job(self, job: Job) -> None:
        mode = job.mode
        extra = dict(job.env)
        if not self.flags.get("auto_apply", True):
            extra["APPLY_OVERRIDE"] = "off"
        log.info("Run: %s (trigger: %s)", mode, job.trigger)
        self.running = True
        try:
            await self.publish()
            rc = await self.runner.run(mode, job.trigger, extra)
        finally:
            self.running = False
            self.last_run_end = time.monotonic()
            self.drift.touch()
        if rc != 0:
            log.warning("Engine exited with %s", rc)
        status = self.store.read_status()
        if mode in ("sync", "full"):
            if rc != 0 or status.get("state") == "error":
                self.failures += 1
                self.retry_at = time.monotonic() + backoff_seconds(self.failures)
            else:
                self.failures, self.retry_at = 0, 0.0
        applied = str(status.get("applied", "")) if rc == 0 else ""
        if "restart_required" in applied:
            self.set_restart_required(True)
        elif "homeassistant.restart" in applied:
            self.set_restart_required(False)
        if not self.remote_head:
            self.remote_head = await self.git.rev(self.settings.base_ref)
        await self.pulls.refresh()
        await self.publish()
        self.spawn(self.recheck_drift(mode))

    async def recheck_drift(self, mode: str) -> None:
        """Queues a backup for edits that were made while a run was on.

        The watcher cannot tell those from the files the engine itself
        rewrites (stash, pull, pop), so it only notes the paths. Once the run
        is over, any of them that is still not in the open backup PR was a
        real edit the run missed.
        """
        await asyncio.sleep(self.recheck_delay)
        if self.running:
            return  # the next run's check takes these over
        events, overflow = self.run_events, self.run_overflow
        self.run_events, self.run_overflow = set(), False
        if not events and not overflow:
            return
        status = self.store.read_status()
        if (
            self.debounce is None
            or self.paused
            or mode == "dry_run"
            or status.get("state") == "error"
            or status.get("detail") == "dry_run"
        ):
            return
        snap = await self.snapshot(force=True)
        fresh = {f["path"] for f in snap["files"] if not f["in_pr"] and not f["held"]}
        if not (fresh & events or (overflow and fresh)):
            return
        if self.retriggers >= MAX_RETRIGGERS:
            log.warning(
                "Edits made during the last runs are still pending; waiting for a new change"
            )
            return
        self.retriggers += 1
        log.info("Files changed during the last run; queuing a backup")
        self.debounce.touch(time.monotonic())

    # ---- commands ----------------------------------------------------------

    async def command(self, raw: object, source: str) -> str:
        parsed = parse_command(raw)
        if parsed is None:
            log.warning("Ignored unknown command from %s: %s", source, log_safe(repr(raw)))
            return "unknown command"
        cmd, arg = parsed
        if cmd in PANEL_ONLY and source == "mqtt":
            log.warning("Refused %s over MQTT: only the panel can do that", log_safe(cmd))
            return "not allowed over MQTT"
        log.info("Command from %s: %s %s", source, log_safe(cmd), log_safe(arg))
        result = "ok"
        if cmd == "sync":
            self.request("sync", source)
        elif cmd == "backup":
            self.request("full", source)
        elif cmd in ("dry_run", "discard"):
            self.request(cmd, source)
        elif cmd == "revert":
            self.request("revert", source, arg)
        elif cmd in ("pause", "resume"):
            self.flags["paused"] = cmd == "pause"
            self.save_flags()
        elif cmd in ("auto_apply_on", "auto_apply_off"):
            self.flags["auto_apply"] = cmd == "auto_apply_on"
            self.save_flags()
        elif cmd == "refresh":
            await self.pulls.refresh()
        elif cmd == "merge":
            result = await self.pulls.merge(self.settings.merge_method, self.running)
            if result == "ok":
                log.info("Merged the backup PR from the panel")
                self.request("sync", "merge")  # pull it down right away
        elif cmd == "restart":
            result = await self.restart_ha()
        await self.publish()
        return result

    async def restart_ha(self) -> str:
        """Restarts Home Assistant, but only on a configuration that checks out."""
        if not self.hass.available:
            return "no access to the Home Assistant API"
        try:
            check = await asyncio.to_thread(self.hass.check_config)
        except HassError as err:
            return f"could not reach Home Assistant: {err}"
        if not check.valid:
            return f"not restarting: config check failed ({check.errors})"
        self.set_restart_required(False)
        with contextlib.suppress(HassError):  # HA drops the call as it goes down
            await asyncio.to_thread(self.hass.restart)
        log.info("Home Assistant restart requested from the panel")
        return "ok"

    async def ha_started(self) -> None:
        self.set_restart_required(False)
        await self.publish()

    def stdin_reader(self) -> None:
        for line in sys.stdin:
            self._from_thread(self.command(line, "stdin"))

    # ---- state -------------------------------------------------------------

    async def snapshot(self, force: bool = False) -> dict[str, Any]:
        branch = self.pulls.pull.branch if self.pulls.pull else ""
        return await self.drift.snapshot(running=self.running, pr_branch=branch, force=force)

    async def build_state(self) -> dict[str, Any]:
        settings, status, pull = self.settings, self.store.read_status(), self.pulls.pull
        snap = await self.snapshot()
        files = snap["files"]
        local = await self.git.rev(settings.base_ref)
        return {
            "state": "running" if self.running else status.get("state", "unknown"),
            "detail": status.get("detail", ""),
            "extra": status.get("extra", ""),
            "last_run": status.get("time"),
            "trigger": status.get("trigger", ""),
            "mode": status.get("mode", ""),
            "applied": status.get("applied", ""),
            "local_head": local,
            "remote_head": self.remote_head or local,
            "pending": len(files),
            "pending_new": sum(1 for f in files if not f["in_pr"] and not f["held"]),
            "held": [f["path"] for f in files if f["held"]],
            "pr_number": pull.number if pull else "",
            "pr_url": pull.url if pull else "",
            "pr_branch": pull.branch if pull else "",
            "pr_checks": self.pulls.checks,
            "can_merge": self.pulls.can_merge(self.running),
            "paused": self.paused,
            "auto_apply": bool(self.flags.get("auto_apply", True)),
            "restart_required": bool(self.flags.get("restart_required")),
            "running": self.running,
            "retry_in": max(0, round(self.retry_at - time.monotonic())),
            "sync_mode": settings.sync_mode,
            "webhook": bool(settings.webhook_secret),
            "mqtt": self.mqtt.connected,
            "mqtt_commands": settings.mqtt_commands,
            "repo": settings.github_repo,
            "base": settings.base_branch,
            "version": settings.version,
            "rev": self.drift.rev,
        }

    async def publish(self) -> None:
        state = await self.build_state()
        if self.mqtt.connected:
            self.mqtt.publish_state(state)
        elif self.hass.available:
            attributes = dict(
                state, friendly_name="HA GitOps status", icon="mdi:source-branch-check"
            )
            try:
                await asyncio.to_thread(
                    self.hass.set_state, STATUS_ENTITY, str(state["state"]), attributes
                )
            except HassError as err:
                log.debug("REST state publish failed: %s", err)

    # ---- main --------------------------------------------------------------

    async def run(self) -> None:
        settings = self.settings
        log.info(
            "HA GitOps controller %s starting (sync_mode=%s, poll=%ss, every %sh)",
            settings.version,
            settings.sync_mode,
            settings.poll_seconds,
            settings.interval_hours,
        )
        if settings.mqtt_discovery:
            await self.mqtt.connect()
        runners = await web.start(self)
        await self.pulls.refresh()
        await self.publish()
        threading.Thread(target=self.stdin_reader, daemon=True).start()
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self.worker())
                group.create_task(triggers.scheduler(self))
                group.create_task(triggers.poller(self))
                group.create_task(triggers.watcher(self))
        finally:
            self.mqtt.close()
            for runner in runners:
                await runner.cleanup()
