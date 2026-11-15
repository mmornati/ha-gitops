# SPDX-License-Identifier: Apache-2.0
"""Pure helpers for the HA GitOps controller.

Nothing in here does I/O or imports third-party packages, so it can be unit
tested anywhere (tests/test_core.py) without Home Assistant, MQTT or aiohttp.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re

COMMANDS = {"sync", "backup", "dry_run", "pause", "resume", "auto_apply_on",
            "auto_apply_off", "revert", "refresh", "discard", "merge", "restart"}
# These change the box or the repository for good. They are taken from the
# panel (behind Home Assistant's login) and from stdin, never from MQTT, where
# any device with broker credentials could send them.
PANEL_ONLY = {"discard", "merge", "restart"}
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")

# Paths the file watcher never reacts to. The final say is `git check-ignore`;
# this is a cheap first filter for the noisiest writers.
IGNORED_DIRS = {".git", ".storage", ".cloud", "deps", "tts", "backups",
                "__pycache__", "node_modules", ".cache"}
IGNORED_SUFFIXES = (".db", ".db-shm", ".db-wal", ".db-journal", ".log",
                    ".tmp", ".swp", "~")
IGNORED_NAMES = {".gitops_backup_status", "gitops_backup.log", ".ha_run.lock",
                 ".uuid", "ip_bans.yaml"}
INOTIFY_EXCLUDE = (r"(/\.git/|/\.git$|/\.storage/|/\.cloud/|/deps/|/tts/|"
                   r"/backups/|/__pycache__/|/node_modules/|\.db(-shm|-wal|-journal)?$|"
                   r"\.log(\.[0-9]+)?$)")


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Checks GitHub's X-Hub-Signature-256 header against the shared secret."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def webhook_action(event: str | None, payload: dict, base_branch: str) -> str | None:
    """What a verified GitHub delivery asks for: "ping", "sync" or nothing."""
    if event == "ping":
        return "ping"
    if event == "push" and payload.get("ref") == f"refs/heads/{base_branch}":
        return "sync"
    if (event == "pull_request" and payload.get("action") == "closed"
            and (payload.get("pull_request") or {}).get("merged")
            and ((payload.get("pull_request") or {}).get("base") or {}).get("ref") == base_branch):
        return "sync"
    return None


def parse_command(raw) -> tuple[str, str] | None:
    """Parses a command from stdin, MQTT or the panel.

    hassio.addon_stdin JSON-encodes its input, so `"sync"` and
    `{"command": "revert", "arg": "abc1234"}` are both accepted, as is the
    plain text `revert abc1234`.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode(errors="replace")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            raw = json.loads(text)
        except ValueError:
            raw = text
    if isinstance(raw, dict):
        cmd = str(raw.get("command", "")).strip().lower()
        arg = str(raw.get("arg", "") or "").strip()
    elif isinstance(raw, str):
        parts = raw.strip().split(None, 1)
        if not parts:
            return None
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
    else:
        return None
    if cmd not in COMMANDS:
        return None
    if cmd == "revert":
        arg = arg.lower()
        if not SHA_RE.match(arg):
            return None
    return cmd, arg


def watcher_ignores(rel_path: str) -> bool:
    """True when a changed path (relative to /config) can never be backed up."""
    parts = [p for p in rel_path.split("/") if p]
    if not parts:
        return True
    if any(p in IGNORED_DIRS for p in parts[:-1]) or parts[0] in IGNORED_DIRS:
        return True
    name = parts[-1]
    if name in IGNORED_NAMES or name.startswith("home-assistant_v2."):
        return True
    if re.search(r"\.log\.[0-9]+$", name):
        return True
    return name.endswith(IGNORED_SUFFIXES)


class Debounce:
    """Fires once nothing has changed for `quiet` seconds.

    `max_wait` caps the delay, so a file rewritten every few seconds still
    gets backed up eventually.
    """

    def __init__(self, quiet: float, max_wait: float | None = None):
        self.quiet = quiet
        self.max_wait = max_wait if max_wait is not None else quiet * 10
        self.first = None
        self.last = None

    def touch(self, now: float) -> None:
        if self.first is None:
            self.first = now
        self.last = now

    def due(self, now: float) -> bool:
        if self.last is None:
            return False
        return now - self.last >= self.quiet or now - self.first >= self.max_wait

    def reset(self) -> None:
        self.first = self.last = None


def backoff_seconds(failures: int, base: float = 60, cap: float = 3600) -> float:
    """How long to wait before the poller retries after `failures` failed runs."""
    if failures <= 0:
        return 0
    return min(cap, base * 2 ** (failures - 1))


class RateLimiter:
    """Counts events per key inside a sliding window."""

    def __init__(self, limit: int = 10, window: float = 60, max_keys: int = 1000):
        self.limit = limit
        self.window = window
        self.max_keys = max_keys
        self.events: dict[str, list[float]] = {}

    def hit(self, key: str, now: float) -> int:
        """Records one event and returns how many the key has in the window."""
        if len(self.events) >= self.max_keys and key not in self.events:
            self.events.clear()
        recent = [t for t in self.events.get(key, []) if now - t < self.window]
        recent.append(now)
        self.events[key] = recent
        return len(recent)


def parse_name_status(raw: str) -> list[dict]:
    """Parses `git diff --name-status -z` into path/status/old records."""
    names = {"A": "added", "D": "deleted", "R": "renamed", "C": "added"}
    tokens = [t for t in raw.split("\0")]
    out, i = [], 0
    while i < len(tokens) and tokens[i]:
        code = tokens[i][:1]
        if code in ("R", "C") and i + 2 < len(tokens):
            old, path = tokens[i + 1], tokens[i + 2]
            i += 3
        elif i + 1 < len(tokens):
            old, path = "", tokens[i + 1]
            i += 2
        else:
            break
        out.append({"path": path, "status": names.get(code, "modified"),
                    "old": old if code == "R" else ""})
    return out


def can_merge(pr_number, checks: str, running: bool) -> bool:
    """The open backup PR may be merged from the panel.

    "none" means the repository has no checks at all; "unknown" (GitHub could
    not be asked), "pending" and "failure" all block.
    """
    return bool(pr_number) and not running and checks in ("success", "none")


def summarize_checks(check_runs: dict | None, status: dict | None) -> str:
    """Folds GitHub check runs + commit statuses into one word."""
    states = []
    for run in (check_runs or {}).get("check_runs", []):
        if run.get("status") != "completed":
            states.append("pending")
        elif run.get("conclusion") in ("success", "neutral", "skipped"):
            states.append("success")
        else:
            states.append("failure")
    for st in (status or {}).get("statuses", []):
        states.append({"success": "success", "pending": "pending"}.get(st.get("state"), "failure"))
    if not states:
        return "none"
    if "failure" in states:
        return "failure"
    if "pending" in states:
        return "pending"
    return "success"


def discovery_configs(prefix: str, base: str, version: str,
                      commands: bool = True) -> list[tuple[str, dict | None]]:
    """MQTT discovery payloads for the HA GitOps device.

    With `commands` off, the buttons, switches and the update entity are
    listed with a None payload: publishing an empty config removes them.
    """
    device = {
        "identifiers": ["ha_gitops"],
        "name": "HA GitOps",
        "manufacturer": "mmornati/ha-gitops",
        "model": "GitOps config backup app",
        "sw_version": version,
    }
    common = {
        "device": device,
        "availability_topic": f"{base}/availability",
        "state_topic": f"{base}/state",
    }

    def entity(component, key, name, **extra):
        cfg = dict(common)
        cfg.update({"unique_id": f"ha_gitops_{key}",
                    "object_id": f"ha_gitops_{key}", "name": name})
        cfg.update(extra)
        return f"{prefix}/{component}/ha_gitops/{key}/config", cfg

    attrs = {"json_attributes_topic": f"{base}/state"}
    out = [
        entity("sensor", "status", "Status", value_template="{{ value_json.state }}",
               icon="mdi:source-branch-check", **attrs),
        entity("sensor", "last_run", "Last run", value_template="{{ value_json.last_run }}",
               device_class="timestamp", entity_category="diagnostic"),
        entity("sensor", "local_head", "Local commit",
               value_template="{{ value_json.local_head[:7] if value_json.local_head else 'unknown' }}",
               icon="mdi:source-commit-local", entity_category="diagnostic"),
        entity("sensor", "remote_head", "Remote commit",
               value_template="{{ value_json.remote_head[:7] if value_json.remote_head else 'unknown' }}",
               icon="mdi:source-commit", entity_category="diagnostic"),
        entity("sensor", "pending_changes", "Pending changes",
               value_template="{{ value_json.pending }}", unit_of_measurement="files",
               state_class="measurement", icon="mdi:file-document-edit"),
        entity("sensor", "open_pr", "Open PR",
               value_template="{{ value_json.pr_number or 'none' }}",
               json_attributes_topic=f"{base}/pr", icon="mdi:source-pull"),
        entity("sensor", "pr_checks", "PR checks", value_template="{{ value_json.pr_checks }}",
               icon="mdi:check-decagram"),
        entity("sensor", "last_apply", "Last apply",
               value_template="{{ value_json.applied or 'none' }}", icon="mdi:reload"),
        entity("sensor", "held_files", "Held back files",
               value_template="{{ value_json.held | count }}", unit_of_measurement="files",
               state_class="measurement", icon="mdi:file-lock"),
        entity("binary_sensor", "running", "Running",
               value_template="{{ 'ON' if value_json.running else 'OFF' }}",
               device_class="running", entity_category="diagnostic"),
        entity("binary_sensor", "restart_required", "Restart required",
               value_template="{{ 'ON' if value_json.restart_required else 'OFF' }}",
               icon="mdi:restart-alert"),
        entity("button", "sync", "Sync now", command_topic=f"{base}/cmd",
               payload_press="sync", icon="mdi:cloud-download"),
        entity("button", "backup", "Back up now", command_topic=f"{base}/cmd",
               payload_press="backup", icon="mdi:cloud-upload"),
        entity("button", "dry_run", "Dry run", command_topic=f"{base}/cmd",
               payload_press="dry_run", icon="mdi:test-tube", entity_category="diagnostic"),
        entity("switch", "paused", "Pause automatic sync", command_topic=f"{base}/cmd",
               payload_on="pause", payload_off="resume",
               value_template="{{ 'pause' if value_json.paused else 'resume' }}",
               icon="mdi:pause-circle"),
        entity("switch", "auto_apply", "Apply merged changes", command_topic=f"{base}/cmd",
               payload_on="auto_apply_on", payload_off="auto_apply_off",
               value_template="{{ 'auto_apply_on' if value_json.auto_apply else 'auto_apply_off' }}",
               icon="mdi:auto-fix"),
    ]
    upd = dict(common)
    upd.update({
        "unique_id": "ha_gitops_config", "object_id": "ha_gitops_config",
        "name": "Config", "state_topic": f"{base}/update",
        "command_topic": f"{base}/cmd", "payload_install": "sync",
        "title": "Home Assistant config", "icon": "mdi:source-merge",
    })
    out.append((f"{prefix}/update/ha_gitops/config/config", upd))
    if not commands:
        out = [(topic, None if "command_topic" in cfg else cfg) for topic, cfg in out]
    return out
