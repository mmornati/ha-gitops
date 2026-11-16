# SPDX-License-Identifier: Apache-2.0
"""git for the controller: read-only questions, asked without blocking the loop."""

import asyncio
from collections.abc import Mapping
from pathlib import Path


class AsyncGit:
    def __init__(self, repo: Path, env: Mapping[str, str]) -> None:
        self.repo = repo
        self.env = dict(env)

    async def run(
        self, *args: str, limit: float = 60, extra_env: Mapping[str, str] | None = None
    ) -> tuple[int, str]:
        """(exit status, stdout). Status 1 and no output when there is no repository."""
        if not (self.repo / ".git").exists():
            return 1, ""
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(self.repo),
            *args,
            env={**self.env, **(extra_env or {})},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(limit):
                out, _ = await proc.communicate()
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return 1, ""
        return proc.returncode or 0, out.decode(errors="replace")

    async def rev(self, ref: str) -> str:
        _, out = await self.run("rev-parse", "-q", "--verify", ref)
        return out.strip()
