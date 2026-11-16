# SPDX-License-Identifier: Apache-2.0
"""The queue of engine runs: one at a time, the most important first."""

import asyncio
from dataclasses import dataclass, field

# A queued full run also covers a sync; a discard goes before either.
PRIORITY = ("discard", "dry_run", "full", "sync")
MAX_REVERTS = 20


@dataclass(frozen=True, slots=True)
class Job:
    mode: str
    trigger: str
    env: dict[str, str] = field(default_factory=dict)


class JobQueue:
    def __init__(self) -> None:
        self.pending: dict[str, Job] = {}
        self.reverts: list[str] = []
        self._wakeup = asyncio.Event()

    def request(self, mode: str, trigger: str, revert_sha: str = "") -> None:
        """Queues a run. Asking twice for the same mode queues it once."""
        if mode == "revert":
            if revert_sha not in self.reverts and len(self.reverts) < MAX_REVERTS:
                self.reverts.append(revert_sha)
        else:
            self.pending.setdefault(mode, Job(mode, trigger))
        self._wakeup.set()

    def next(self) -> Job | None:
        if self.reverts:
            return Job("revert", "panel", {"REVERT_SHA": self.reverts.pop(0)})
        for mode in PRIORITY:
            if mode in self.pending:
                job = self.pending.pop(mode)
                if mode == "full":
                    self.pending.pop("sync", None)
                return job
        return None

    def __contains__(self, mode: str) -> bool:
        return mode in self.pending or (mode == "revert" and bool(self.reverts))

    def __len__(self) -> int:
        return len(self.pending) + len(self.reverts)

    async def wait(self) -> None:
        await self._wakeup.wait()
        self._wakeup.clear()
