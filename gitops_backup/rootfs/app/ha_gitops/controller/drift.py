# SPDX-License-Identifier: Apache-2.0
"""What differs between /config and the base branch, file by file."""

import asyncio
import shutil
import time
from typing import Any

from ha_gitops.adapters.state import StateStore
from ha_gitops.controller.gitio import AsyncGit
from ha_gitops.rules.text import parse_name_status

TTL = 15  # seconds a snapshot is reused
MAX_DIFF = 400_000


def empty() -> dict[str, Any]:
    return {"files": [], "diff": "", "truncated": False}


class DriftService:
    """Built in a scratch index, so the repository's own index and the files
    are not touched. Cached, and never rebuilt while a run is rewriting the
    tree.
    """

    def __init__(self, git: AsyncGit, store: StateStore) -> None:
        self.git = git
        self.store = store
        self.rev = 0  # bumped whenever the drift may have changed
        self._cache: dict[str, Any] | None = None
        self._cache_rev = -1
        self._cache_time = 0.0
        self._lock = asyncio.Lock()

    def touch(self) -> None:
        self.rev += 1

    async def snapshot(
        self, *, running: bool, pr_branch: str, force: bool = False
    ) -> dict[str, Any]:
        fresh = (
            self._cache is not None
            and self._cache_rev == self.rev
            and time.monotonic() - self._cache_time < TTL
        )
        if self._cache is not None and (running or (fresh and not force)):
            return self._cache
        if running:
            return empty()
        async with self._lock:
            snap = await self._compute(pr_branch)
        if snap is not None:
            self._cache, self._cache_rev, self._cache_time = snap, self.rev, time.monotonic()
        return self._cache or empty()

    async def _compute(self, pr_branch: str) -> dict[str, Any] | None:
        git, index = self.git, self.store.paths.panel_index
        git_dir = git.repo / ".git"
        if not git_dir.exists():
            return empty()
        try:
            await asyncio.to_thread(shutil.copyfile, git_dir / "index", index)
        except OSError:
            index.unlink(missing_ok=True)
        env = {"GIT_INDEX_FILE": str(index)}
        try:
            rc, _ = await git.run("add", "-A", limit=120, extra_env=env)
            if rc != 0:
                return None
            rc, raw = await git.run(
                "diff", "--cached", "--name-status", "-z", "-M", "HEAD", extra_env=env
            )
            if rc != 0:
                return None
            files: list[dict[str, Any]] = list(parse_name_status(raw))
            held = set(self.store.read_status().get("held") or [])
            # Files the open backup PR already has exactly as they are on the box
            not_in_pr: set[str] | None = None
            if pr_branch and await git.rev(f"refs/heads/{pr_branch}"):
                rc, names = await git.run(
                    "diff",
                    "--cached",
                    "--name-only",
                    "-z",
                    "--no-renames",
                    f"refs/heads/{pr_branch}",
                    extra_env=env,
                )
                if rc == 0:
                    not_in_pr = set(filter(None, names.split("\0")))
            for entry in files:
                entry["held"] = entry["path"] in held and entry["status"] != "deleted"
                entry["in_pr"] = (
                    not entry["held"] and not_in_pr is not None and entry["path"] not in not_in_pr
                )
            # The content of a held file is a secret: it is never sent to the panel
            exclude = [f":(exclude,literal){f['path']}" for f in files if f["held"]]
            _, diff = await git.run(
                "diff",
                "--cached",
                "--no-color",
                "-M",
                "HEAD",
                "--",
                ".",
                *exclude,
                extra_env=env,
            )
            return {"files": files, "diff": diff[:MAX_DIFF], "truncated": len(diff) > MAX_DIFF}
        finally:
            index.unlink(missing_ok=True)
