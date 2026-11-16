# SPDX-License-Identifier: Apache-2.0
"""Typed settings: the app options plus the environment, read once."""

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# A branch name that is safe as a git argument and inside a ref or a URL.
REF_RE = re.compile(r"^(?!-)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]+(?<![/.])$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")

APPLY_MODES = ("auto", "reload", "restart", "off")
SYNC_MODES = ("both", "pull", "push", "schedule")
MERGE_METHODS = ("merge", "squash", "rebase")


def _truthy(value: object) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _choice(value: object, allowed: tuple[str, ...], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _int(value: object, default: int, low: int, high: int) -> int:
    try:
        number = int(str(value))
    except TypeError, ValueError:
        return default
    return min(high, max(low, number))


def load_json(path: Path, default: Any) -> Any:
    """The JSON document in ``path``, or ``default`` when it cannot be read."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return default


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything a run needs to know. Secrets are kept out of ``repr``."""

    config_dir: Path
    data_dir: Path
    github_repo: str = ""
    github_token: str = field(default="", repr=False)
    base_branch: str = "main"
    branch_prefix: str = "auto-backup"
    dry_run: bool = False
    commit_name: str = "HA GitOps Backup"
    commit_email: str = "gitops-backup@users.noreply.github.com"
    signoff: bool = True
    apply_after_pull: str = "auto"
    auto_restart: bool = False
    on_invalid_config: str = "notify"
    merge_method: str = "merge"
    sync_mode: str = "both"
    interval_hours: int = 24
    run_at_start: bool = False
    poll_seconds: int = 60
    debounce_seconds: int = 120
    webhook_secret: str = field(default="", repr=False)
    mqtt_discovery: bool = True
    mqtt_commands: bool = True
    github_api: str = "https://api.github.com"
    supervisor_url: str = "http://supervisor"
    supervisor_token: str = field(default="", repr=False)
    lock_wait: int = 900
    engine_timeout: int = 1800
    version: str = "dev"

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None) -> Self:
        """Reads the options file named by the environment."""
        env = os.environ if env is None else env
        opts = load_json(Path(env.get("OPTIONS", "/data/options.json")), {})
        if not isinstance(opts, dict):
            opts = {}
        return cls.from_options(opts, env)

    @classmethod
    def from_options(cls, opts: Mapping[str, Any], env: Mapping[str, str]) -> Self:
        def text(key: str, default: str = "") -> str:
            value = opts.get(key)
            return value.strip() if isinstance(value, str) and value.strip() else default

        return cls(
            config_dir=Path(env.get("CONFIG_DIR", "/homeassistant")),
            data_dir=Path(env.get("DATA_DIR", "/data")),
            github_repo=text("github_repo"),
            github_token=text("github_token"),
            base_branch=text("base_branch", "main"),
            branch_prefix=text("branch_prefix", "auto-backup"),
            dry_run=_truthy(env.get("DRY_RUN_OVERRIDE") or opts.get("dry_run")),
            commit_name=text("commit_name", "HA GitOps Backup"),
            commit_email=text("commit_email", "gitops-backup@users.noreply.github.com"),
            signoff=_truthy(opts.get("signoff", True)),
            apply_after_pull=_choice(
                env.get("APPLY_OVERRIDE") or opts.get("apply_after_pull"), APPLY_MODES, "auto"
            ),
            auto_restart=_truthy(opts.get("auto_restart")),
            on_invalid_config=_choice(
                opts.get("on_invalid_config"), ("notify", "revert_pr"), "notify"
            ),
            merge_method=_choice(opts.get("merge_method"), MERGE_METHODS, "merge"),
            sync_mode=_choice(opts.get("sync_mode"), SYNC_MODES, "both"),
            interval_hours=_int(opts.get("interval_hours"), 24, 1, 168),
            run_at_start=_truthy(opts.get("run_at_start")),
            poll_seconds=_int(opts.get("poll_seconds"), 0, 0, 86400),
            debounce_seconds=_int(opts.get("debounce_seconds"), 120, 1, 3600),
            webhook_secret=text("webhook_secret"),
            mqtt_discovery=_truthy(opts.get("mqtt_discovery", True)),
            mqtt_commands=_truthy(opts.get("mqtt_commands", True)),
            github_api=env.get("GITHUB_API", "https://api.github.com").rstrip("/"),
            supervisor_url=env.get("SUPERVISOR_URL", "http://supervisor").rstrip("/"),
            supervisor_token=env.get("SUPERVISOR_TOKEN", ""),
            lock_wait=_int(env.get("LOCK_WAIT"), 900, 0, 86400),
            engine_timeout=_int(env.get("ENGINE_TIMEOUT"), 1800, 1, 86400),
            version=env.get("BUILD_VERSION", "dev"),
        )

    def problem(self) -> str | None:
        """Why these settings cannot be used for a run, if they cannot."""
        if not self.github_repo or not self.github_token:
            return "github_repo and github_token are required"
        if not REPO_RE.match(self.github_repo):
            return "github_repo must look like owner/name"
        if not REF_RE.match(self.base_branch) or not REF_RE.match(self.branch_prefix):
            return "base_branch and branch_prefix must be plain branch names"
        return None

    @property
    def remote_url(self) -> str:
        """Never carries the token: git gets that from GIT_ASKPASS."""
        return f"https://github.com/{self.github_repo}.git"

    @property
    def base_ref(self) -> str:
        return f"refs/heads/{self.base_branch}"

    @property
    def ha_api(self) -> str:
        return f"{self.supervisor_url}/core/api"

    @property
    def ha_websocket(self) -> str:
        scheme = "wss" if self.supervisor_url.startswith("https://") else "ws"
        return f"{scheme}://{self.supervisor_url.split('://', 1)[-1]}/core/websocket"
