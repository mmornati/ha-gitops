# SPDX-License-Identifier: Apache-2.0
"""``python -m ha_gitops.controller``: the long-running service."""

import asyncio
import contextlib
import logging
import signal
import sys

from ha_gitops.controller import service
from ha_gitops.controller.app import Controller
from ha_gitops.settings import Settings


async def serve() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    if task is not None:
        # s6 stops the service with SIGTERM: stop a run in progress cleanly
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
    with contextlib.suppress(asyncio.CancelledError):
        await service.run(Controller(Settings.load()))


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
