# SPDX-License-Identifier: Apache-2.0
"""The open backup PR as the panel sees it: its checks, and merging it."""

import asyncio

from ha_gitops.adapters.github import GitHub, GitHubError, PullRequest
from ha_gitops.adapters.state import StateStore
from ha_gitops.rules.checks import can_merge, summarize_checks


class PullRequestMonitor:
    def __init__(self, github: GitHub, store: StateStore) -> None:
        self.github = github
        self.store = store
        self.pull: PullRequest | None = None
        self.checks = "none"

    async def refresh(self) -> None:
        self.pull = self.store.open_pr()
        if self.pull is None:
            self.checks = "none"
            return
        branch = self.pull.branch
        try:
            runs, status = await asyncio.gather(
                asyncio.to_thread(self.github.check_runs, branch),
                asyncio.to_thread(self.github.combined_status, branch),
            )
        except GitHubError, ValueError:  # the network is best effort
            self.checks = "unknown"
            return
        self.checks = summarize_checks(runs, status)

    def can_merge(self, running: bool) -> bool:
        return can_merge(self.pull.number if self.pull else "", self.checks, running)

    async def merge(self, method: str, running: bool) -> str:
        """Merges the open backup PR on GitHub; "ok" or why not."""
        await self.refresh()
        if self.pull is None or not self.pull.number:
            return "there is no open backup PR"
        if not self.can_merge(running):
            return f"not merging: PR checks are {self.checks}"
        try:
            await asyncio.to_thread(self.github.merge_pull, self.pull.number, method)
        except GitHubError as err:
            if err.status:
                return f"GitHub refused the merge: {err.message or err.status}"
            return f"could not reach GitHub: {err.message}"
        except ValueError:
            return "there is no open backup PR"
        return "ok"
