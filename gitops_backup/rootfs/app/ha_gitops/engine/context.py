# SPDX-License-Identifier: Apache-2.0
"""What one engine run carries around: its collaborators and its report."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ha_gitops.adapters.git import Git
from ha_gitops.adapters.github import GitHub, PullRequest
from ha_gitops.adapters.hass import HomeAssistant
from ha_gitops.adapters.state import StateStore
from ha_gitops.settings import Settings

MODES = ("full", "sync", "revert", "discard")


@dataclass(slots=True)
class Report:
    """Filled in as the run goes, written to status.json."""

    pr: PullRequest | None = None
    changed: int = 0
    applied: str = ""
    held: list[str] = field(default_factory=list)
    failed: bool = False


@dataclass(slots=True)
class RunContext:
    settings: Settings
    git: Git
    github: GitHub
    hass: HomeAssistant
    state: StateStore
    mode: str = "full"
    trigger: str = "manual"
    revert_sha: str = ""
    now: Callable[[], datetime] = datetime.now
    echo: Callable[[str], None] = print
    report: Report = field(default_factory=Report)

    def log(self, message: str) -> None:
        line = f"{self.now():%Y-%m-%d %H:%M:%S} - {message}"
        self.echo(line)
        self.state.append_log(line)

    def log_raw(self, text: str) -> None:
        """Command output, copied to the log as it is."""
        for line in text.splitlines():
            self.echo(line)
            self.state.append_log(line)

    def status(self, state: str, detail: str, extra: str = "") -> None:
        pr = self.report.pr
        self.report.failed = self.report.failed or state == "error"
        self.state.write_status(
            {
                "state": state,
                "detail": detail,
                "extra": extra,
                "time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mode": self.mode,
                "trigger": self.trigger,
                "local_head": self.git.rev(self.settings.base_ref),
                "pr_url": pr.url if pr else "",
                "pr_number": pr.number if pr else "",
                "pr_branch": pr.branch if pr else "",
                "changed": self.report.changed,
                "applied": self.report.applied,
                "held": self.state.held(),
            }
        )
        self.log(f"Status: {state}:{detail}:{extra}")
