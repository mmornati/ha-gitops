# SPDX-License-Identifier: Apache-2.0
"""What starts a run by itself: the schedule, the remote poll, the file watcher."""

import asyncio
import logging
import shutil
import time

from ha_gitops.controller.app import Controller
from ha_gitops.rules.ignore import INOTIFY_EXCLUDE, watcher_ignores
from ha_gitops.rules.timing import Debounce

log = logging.getLogger("ha_gitops")
PR_REFRESH_SECONDS = 300
IDLE_POLL_SECONDS = 300
IGNORE_CACHE_MAX = 5000
MAX_RUN_EVENTS = 5000
WATCH_EVENTS = "close_write,create,delete,moved_to,moved_from"


async def scheduler(ctl: Controller) -> None:
    if ctl.settings.run_at_start:
        ctl.request("full", "start")
    while True:
        await asyncio.sleep(ctl.settings.interval_hours * 3600)
        if not ctl.paused:
            ctl.request("full", "schedule")


async def poll_once(ctl: Controller) -> None:
    """Asks the remote where the base branch is; queues a sync when it moved."""
    settings = ctl.settings
    rc, out = await ctl.git.run("ls-remote", "origin", settings.base_ref, limit=30)
    remote = out.split()[0] if rc == 0 and out.strip() else ""
    if remote and remote != ctl.remote_head:
        ctl.remote_head = remote
        await ctl.publish()
    # After failed runs the poller backs off (1 min, 2, 4… up to 1 h) instead
    # of retrying the same pull every interval.
    if (
        ctl.pulls_enabled
        and settings.poll_seconds > 0
        and remote
        and not ctl.paused
        and not ctl.running
        and time.monotonic() >= ctl.retry_at
    ):
        local = await ctl.git.rev(settings.base_ref)
        if local and remote != local:
            rc, _ = await ctl.git.run("merge-base", "--is-ancestor", remote, local)
            if rc != 0:  # unknown locally, or not yet merged in
                ctl.request("sync", "poll")


async def poller(ctl: Controller) -> None:
    seconds = ctl.settings.poll_seconds
    interval = seconds if (ctl.pulls_enabled and seconds > 0) else IDLE_POLL_SECONDS
    last_pr_refresh = 0.0
    while True:
        await asyncio.sleep(interval)
        await poll_once(ctl)
        if time.monotonic() - last_pr_refresh > PR_REFRESH_SECONDS:
            last_pr_refresh = time.monotonic()
            await ctl.pulls.refresh()
            await ctl.publish()


async def is_git_ignored(ctl: Controller, rel: str) -> bool:
    cache = ctl.ignore_cache
    if rel not in cache:
        rc, _ = await ctl.git.run("check-ignore", "-q", "--", rel)
        if len(cache) > IGNORE_CACHE_MAX:
            cache.clear()
        cache[rel] = rc == 0
    return cache[rel]


async def file_changed(ctl: Controller, rel: str) -> None:
    """One path the watcher saw change, relative to /config."""
    if watcher_ignores(rel) or ctl.debounce is None:
        return
    # The engine itself rewrites files (stash/pull/pop). Those cannot be told
    # from real edits here, so they are only noted and looked at once the run
    # is over (Controller.recheck_drift).
    if ctl.running or time.monotonic() - ctl.last_run_end < 5:
        if len(ctl.run_events) < MAX_RUN_EVENTS:
            ctl.run_events.add(rel)
        else:
            ctl.run_overflow = True
        return
    if rel.endswith(".gitignore"):
        ctl.ignore_cache.clear()
    if await is_git_ignored(ctl, rel):
        return
    ctl.drift.touch()
    ctl.retriggers = 0
    ctl.debounce.touch(time.monotonic())


async def debounce_tick(ctl: Controller) -> None:
    debounce = ctl.debounce
    if debounce is not None and debounce.due(time.monotonic()):
        debounce.reset()
        if not ctl.paused:
            ctl.request("full", "watcher")


async def watcher(ctl: Controller) -> None:
    if not ctl.pushes_enabled:
        return
    if not shutil.which("inotifywait"):
        log.warning("inotifywait not found; file watching is off")
        return
    quiet = ctl.settings.debounce_seconds
    ctl.debounce = Debounce(quiet, max_wait=max(quiet * 5, 600))

    async def fire() -> None:
        while True:
            await asyncio.sleep(1)
            await debounce_tick(ctl)

    ctl.spawn(fire())
    config_dir = ctl.settings.config_dir
    prefix = str(config_dir).rstrip("/") + "/"
    while True:
        proc = await asyncio.create_subprocess_exec(
            "inotifywait",
            "-m",
            "-r",
            "-q",
            "-e",
            WATCH_EVENTS,
            "--format",
            "%w%f",
            "--exclude",
            INOTIFY_EXCLUDE,
            str(config_dir),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        log.info("Watching %s for changes (debounce %ss)", config_dir, quiet)
        try:
            if proc.stdout is not None:
                async for raw in proc.stdout:
                    path = raw.decode(errors="replace").rstrip("\n")
                    await file_changed(ctl, path.removeprefix(prefix))
            err = (
                (await proc.stderr.read()).decode(errors="replace").strip() if (proc.stderr) else ""
            )
        finally:
            if proc.returncode is None:
                proc.kill()
        log.warning("File watcher stopped (%s); restarting in 60s", err or proc.returncode)
        await asyncio.sleep(60)
