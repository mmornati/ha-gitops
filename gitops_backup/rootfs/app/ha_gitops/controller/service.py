# SPDX-License-Identifier: Apache-2.0
"""Puts the controller to work: its servers, its triggers and its worker."""

import asyncio
import logging
import threading

from ha_gitops.controller import triggers, web
from ha_gitops.controller.app import Controller

log = logging.getLogger("ha_gitops")


async def run(ctl: Controller) -> None:
    """Runs until cancelled, then closes what it opened."""
    settings = ctl.settings
    log.info(
        "HA GitOps controller %s starting (sync_mode=%s, poll=%ss, every %sh)",
        settings.version,
        settings.sync_mode,
        settings.poll_seconds,
        settings.interval_hours,
    )
    if settings.mqtt_discovery:
        await ctl.mqtt.connect()
    runners = await web.start(ctl)
    await ctl.pulls.refresh()
    await ctl.publish()
    threading.Thread(target=ctl.stdin_reader, daemon=True).start()
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(ctl.worker())
            group.create_task(triggers.scheduler(ctl))
            group.create_task(triggers.poller(ctl))
            group.create_task(triggers.watcher(ctl))
    finally:
        ctl.mqtt.close()
        for runner in runners:
            await runner.cleanup()
