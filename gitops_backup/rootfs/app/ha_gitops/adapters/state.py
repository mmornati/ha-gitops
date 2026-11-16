# SPDX-License-Identifier: Apache-2.0
"""Everything the app remembers between runs, in the app's data folder.

The file names and formats are a contract: the controller, the panel and
older versions of the engine read them.
"""

import contextlib
import fcntl
import json
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ha_gitops.adapters.github import PullRequest
from ha_gitops.settings import load_json

MAX_LOG_LINES = 500


def atomic_write(path: Path, text: str) -> None:
    """Replaces ``path`` in one step, so a reader never sees half a file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", errors="surrogateescape")
    tmp.replace(path)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="surrogateescape").strip()
    except OSError:
        return ""


@dataclass(frozen=True, slots=True)
class Paths:
    config_dir: Path
    data_dir: Path

    @property
    def log(self) -> Path:
        return self.data_dir / "gitops_backup.log"

    @property
    def status(self) -> Path:
        return self.data_dir / "status.json"

    @property
    def legacy_status(self) -> Path:
        return self.config_dir / ".gitops_backup_status"

    @property
    def open_pr(self) -> Path:
        return self.data_dir / "open_pr"

    @property
    def lock(self) -> Path:
        return self.data_dir / "gitops.lock"

    @property
    def applied_head(self) -> Path:
        return self.data_dir / "applied_head"

    @property
    def invalid_head(self) -> Path:
        return self.data_dir / "invalid_head"

    @property
    def held(self) -> Path:
        return self.data_dir / "held"

    @property
    def controller_state(self) -> Path:
        return self.data_dir / "controller_state.json"

    @property
    def panel_index(self) -> Path:
        return self.data_dir / "panel.index"


class StateStore:
    def __init__(self, config_dir: Path, data_dir: Path) -> None:
        self.paths = Paths(config_dir, data_dir)

    # ---- last run ----------------------------------------------------------

    def read_status(self) -> dict[str, Any]:
        status = load_json(self.paths.status, {})
        return status if isinstance(status, dict) else {}

    def write_status(self, status: dict[str, Any]) -> None:
        """status.json, plus the legacy one-line ``state:detail:extra`` file."""
        atomic_write(self.paths.status, json.dumps(status))
        line = f"{status.get('state', '')}:{status.get('detail', '')}:{status.get('extra', '')}\n"
        with contextlib.suppress(OSError):
            atomic_write(self.paths.legacy_status, line)

    # ---- the open backup PR ------------------------------------------------

    def open_pr(self) -> PullRequest | None:
        fields = ([*_read(self.paths.open_pr).split("\t"), "", "", ""])[:3]
        return PullRequest(*fields) if fields[0] else None

    def set_open_pr(self, pr: PullRequest | None) -> None:
        if pr is None or not pr.branch:
            self.paths.open_pr.unlink(missing_ok=True)
        else:
            atomic_write(self.paths.open_pr, f"{pr.branch}\t{pr.url}\t{pr.number}\n")

    # ---- markers -----------------------------------------------------------

    def applied_head(self) -> str:
        """The last base commit that was applied to Home Assistant."""
        return _read(self.paths.applied_head)

    def set_applied_head(self, sha: str) -> None:
        atomic_write(self.paths.applied_head, sha + "\n")

    def invalid_head(self) -> str:
        """The base commit whose config check failed, reported once."""
        return _read(self.paths.invalid_head)

    def set_invalid_head(self, sha: str | None) -> None:
        if sha is None:
            self.paths.invalid_head.unlink(missing_ok=True)
        else:
            atomic_write(self.paths.invalid_head, sha + "\n")

    def held(self) -> list[str]:
        """Files the last backup held back as secret-like."""
        return [line for line in _read(self.paths.held).split("\n") if line]

    def set_held(self, paths: Sequence[str]) -> None:
        if paths:
            atomic_write(self.paths.held, "\n".join(paths) + "\n")
        else:
            self.paths.held.unlink(missing_ok=True)

    # ---- controller --------------------------------------------------------

    def controller_state(self) -> dict[str, Any]:
        state = load_json(self.paths.controller_state, {})
        return state if isinstance(state, dict) else {}

    def set_controller_state(self, state: dict[str, Any]) -> None:
        atomic_write(self.paths.controller_state, json.dumps(state))

    # ---- run log -----------------------------------------------------------

    def append_log(self, line: str) -> None:
        with self.paths.log.open("a", encoding="utf-8", errors="surrogateescape") as fh:
            fh.write(line + "\n")

    def tail_log(self, lines: int) -> list[str]:
        try:
            text = self.paths.log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return text.splitlines()[-lines:]

    def rotate_log(self) -> None:
        kept = self.tail_log(MAX_LOG_LINES + 1)
        if len(kept) > MAX_LOG_LINES:
            atomic_write(self.paths.log, "\n".join(kept[-MAX_LOG_LINES:]) + "\n")

    # ---- one run at a time -------------------------------------------------

    @contextlib.contextmanager
    def run_lock(self, wait: float) -> Iterator[bool]:
        """Holds the run lock; yields False when it stayed busy for ``wait`` s.

        ``flock(2)``, the same lock the shell engine of earlier versions took
        on the same file, so an old and a new engine never overlap either.
        """
        with self.paths.lock.open("w") as fh:
            deadline = time.monotonic() + wait
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        yield False
                        return
                    time.sleep(min(1.0, max(0.05, deadline - time.monotonic())))
            try:
                yield True
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
