# SPDX-License-Identifier: Apache-2.0
"""A thin, explicit wrapper around the git command line.

Paths are decoded with ``surrogateescape``, so a file name that is not valid
UTF-8 survives a round trip through this module unchanged.
"""

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from ha_gitops.settings import Settings

ASKPASS = Path(__file__).resolve().parent.parent / "git_askpass.sh"


class GitError(Exception):
    """A git command exited with a status the caller did not expect."""

    def __init__(self, args: Sequence[str], returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr.strip()
        # Only the subcommand: arguments can hold paths or refs worth keeping short
        self.command = next((a for a in args if not a.startswith("-")), "git")
        super().__init__(f"git {self.command} failed ({returncode}): {self.stderr[-300:]}")


def git_env(settings: Settings, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment every git call runs in.

    The token never touches disk or a command line: git asks GIT_ASKPASS,
    which reads it from the environment. Identity and ``safe.directory`` are
    passed as per-process config, so nothing is written to a global gitconfig.
    """
    env = dict(os.environ if base is None else base)
    env["GIT_ASKPASS"] = str(ASKPASS)
    env["GITOPS_TOKEN"] = settings.github_token
    env["GIT_TERMINAL_PROMPT"] = "0"
    # A stalled transfer (under 1 kB/s for a minute) fails instead of hanging
    env["GIT_HTTP_LOW_SPEED_LIMIT"] = "1000"
    env["GIT_HTTP_LOW_SPEED_TIME"] = "60"
    config = {
        # /config is owned by another uid than the app: trust it explicitly
        "safe.directory": str(settings.config_dir),
        "user.name": settings.commit_name,
        "user.email": settings.commit_email,
        "core.quotePath": "false",
        "advice.detachedHead": "false",
    }
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        count = 0
    for key, value in config.items():
        env[f"GIT_CONFIG_KEY_{count}"] = key
        env[f"GIT_CONFIG_VALUE_{count}"] = value
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    return env


class Git:
    """Runs git in one repository."""

    def __init__(self, repo: Path, env: Mapping[str, str]) -> None:
        self.repo = repo
        self.env = dict(env)

    def at(self, repo: Path) -> Git:
        """The same environment, in another work tree."""
        return Git(repo, self.env)

    def run(
        self,
        *args: str,
        check: bool = True,
        stdin: bytes | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.repo,
            env=self.env,
            input=stdin,
            stdin=None if stdin is not None else subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        if check and proc.returncode != 0:
            raise GitError(args, proc.returncode, proc.stderr.decode(errors="replace"))
        return proc

    def out(self, *args: str, check: bool = True) -> str:
        """Standard output, without the trailing newline."""
        raw = self.run(*args, check=check).stdout
        return raw.decode(errors="surrogateescape").rstrip("\n")

    def ok(self, *args: str) -> bool:
        return self.run(*args, check=False).returncode == 0

    def records(self, *args: str) -> list[str]:
        """The NUL-separated records of a ``-z`` command."""
        raw = self.run(*args).stdout.decode(errors="surrogateescape")
        return [r for r in raw.split("\0") if r]

    def rev(self, ref: str) -> str:
        """The object id ``ref`` names, or "" when there is none."""
        return self.out("rev-parse", "-q", "--verify", ref, check=False)

    def popen(self, *args: str) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            ["git", *args],
            cwd=self.repo,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def with_paths(self, *args: str, paths: Sequence[str]) -> None:
        """Runs a command on many literal paths in one process.

        The paths go through stdin, so there is no argument-length limit and
        none of them is read as an option or a pathspec pattern.
        """
        if not paths:
            return
        data = b"".join(p.encode(errors="surrogateescape") + b"\0" for p in paths)
        self.run(
            "--literal-pathspecs",
            *args,
            "--pathspec-from-file=-",
            "--pathspec-file-nul",
            stdin=data,
        )
