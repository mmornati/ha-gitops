# SPDX-License-Identifier: Apache-2.0
"""Runs the engine as a child process, and stops it when it hangs.

A separate process keeps a run that crashes or stalls (a push that never
ends) away from the service that serves the panel, and lets it be killed as a
whole.
"""

import asyncio
import contextlib
import logging
import os
import signal
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from ha_gitops.adapters.state import StateStore

log = logging.getLogger("ha_gitops")
APP_ROOT = Path(__file__).resolve().parent.parent.parent
EXIT_TIMEOUT = 124


class EngineRunner:
    def __init__(self, store: StateStore, timeout: float) -> None:
        self.store = store
        self.timeout = timeout
        self.command: Sequence[str] = (sys.executable, "-m", "ha_gitops.engine")

    async def run(self, mode: str, trigger: str, extra: Mapping[str, str]) -> int:
        env = dict(os.environ)
        env.update(extra)
        env["GITOPS_TRIGGER"] = trigger
        env["GITOPS_MODE"] = "full" if mode == "dry_run" else mode
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(APP_ROOT), env.get("PYTHONPATH", "")])
        )
        env["PYTHONUNBUFFERED"] = "1"
        if mode == "dry_run":
            env["DRY_RUN_OVERRIDE"] = "true"
        # Its own process group, so a run that hangs can be stopped as a whole
        proc = await asyncio.create_subprocess_exec(
            *self.command,
            env=env,
            cwd=APP_ROOT,
            start_new_session=True,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async def pump() -> int:
            if proc.stdout is not None:
                async for line in proc.stdout:
                    sys.stdout.write(line.decode(errors="replace"))
            return await proc.wait()

        try:
            async with asyncio.timeout(self.timeout):
                return await pump()
        except TimeoutError:
            log.error("Run %s still going after %ss; stopping it", mode, self.timeout)
            await self._stop(proc)
            self._record_timeout(mode, trigger)
            return proc.returncode or EXIT_TIMEOUT
        except asyncio.CancelledError:
            await self._stop(proc)  # the service is shutting down
            raise

    @staticmethod
    async def _stop(proc: asyncio.subprocess.Process) -> None:
        for sig, grace in ((signal.SIGTERM, 15), (signal.SIGKILL, 5)):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                return
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(grace):
                    await proc.wait()
                return

    def _record_timeout(self, mode: str, trigger: str) -> None:
        status = self.store.read_status()
        status.update(
            state="error",
            detail="timeout",
            extra=f"run stopped after {self.timeout:g}s",
            mode=mode,
            trigger=trigger,
            time=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        try:
            self.store.write_status(status)
        except OSError as err:
            log.warning("Could not record the timeout: %s", err)
