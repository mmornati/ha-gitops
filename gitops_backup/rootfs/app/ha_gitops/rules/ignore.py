# SPDX-License-Identifier: Apache-2.0
"""What never goes into a backup, and what the file watcher does not react to.

This is the only place those lists live: the seeded ``.gitignore``, the
managed block in ``.git/info/exclude``, the untracker and the watcher all
read them from here.
"""

import re

# Secrets and credentials. Backup and editor copies of secrets.yaml
# (``secrets.yaml.bak-2026…``, ``*.orig``) are the most common way a real
# secret slips into a PR; the ``!`` lines keep committed templates.
SECRET_PATTERNS: tuple[str, ...] = (
    "secrets.yaml",
    "secrets.yaml.*",
    "!secrets.yaml.example",
    "!secrets.yaml.sample",
    "!secrets.yaml.template",
    "!secrets.yaml.dist",
    "*.bak",
    "*.bak-*",
    "*.bak.*",
    "*.orig",
    "*.secret",
    "*.pem",
    "*.key",
    "*.token",
    "*-key.bin",
    "esphome/.device-builder*",
    ".google.token",
    ".git-credentials",
    ".cloud/",
    ".storage/",
    "zigbee2mqtt/secret.yaml",
)
# The app's own runtime metadata: committing it creates a PR feedback loop.
APP_PATTERNS: tuple[str, ...] = (".gitops_backup_status", "gitops_backup.log")
RUNTIME_PATTERNS: tuple[str, ...] = (
    "*.db",
    "*.db-shm",
    "*.db-wal",
    "*.log",
    "*.log.*",
    "ip_bans.yaml",
    "known_devices.yaml",
    "home-assistant_v2.*",
    "OZW_Log.txt",
    ".uuid",
    ".ha_run.lock",
)
MISC_PATTERNS: tuple[str, ...] = ("__pycache__/", "deps/", "tts/", "backups/")

NEVER_COMMIT: tuple[str, ...] = SECRET_PATTERNS + APP_PATTERNS

EXCLUDE_BEGIN = "# >>> gitops-backup managed >>>"
EXCLUDE_END = "# <<< gitops-backup managed <<<"


def seed_gitignore() -> str:
    """The ``.gitignore`` written into a config that has none."""
    sections = (
        ("Secrets and credentials — never commit", SECRET_PATTERNS),
        ("Runtime state, databases, logs", RUNTIME_PATTERNS),
        ("This app's runtime metadata (committing it creates a PR feedback loop)", APP_PATTERNS),
        ("Misc", MISC_PATTERNS),
    )
    return "\n".join(f"# {title}\n" + "\n".join(lines) + "\n" for title, lines in sections)


def managed_exclude(existing: str) -> str:
    """``.git/info/exclude`` with the managed block replaced by the current one."""
    kept: list[str] = []
    inside = False
    for line in existing.splitlines():
        if line.strip() == EXCLUDE_BEGIN:
            inside = True
        elif line.strip() == EXCLUDE_END:
            inside = False
        elif not inside:
            kept.append(line)
    return "\n".join([*kept, EXCLUDE_BEGIN, *NEVER_COMMIT, EXCLUDE_END]) + "\n"


# --- file watcher -----------------------------------------------------------
# The final say is ``git check-ignore``; this is a cheap first filter for the
# noisiest writers.
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".storage",
        ".cloud",
        "deps",
        "tts",
        "backups",
        "__pycache__",
        "node_modules",
        ".cache",
    }
)
IGNORED_SUFFIXES = (".db", ".db-shm", ".db-wal", ".db-journal", ".log", ".tmp", ".swp", "~")
IGNORED_NAMES = frozenset(
    {".gitops_backup_status", "gitops_backup.log", ".ha_run.lock", ".uuid", "ip_bans.yaml"}
)
INOTIFY_EXCLUDE = (
    r"(/\.git/|/\.git$|/\.storage/|/\.cloud/|/deps/|/tts/|"
    r"/backups/|/__pycache__/|/node_modules/|\.db(-shm|-wal|-journal)?$|"
    r"\.log(\.[0-9]+)?$)"
)
_ROTATED_LOG = re.compile(r"\.log\.[0-9]+$")


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
    if _ROTATED_LOG.search(name):
        return True
    return name.endswith(IGNORED_SUFFIXES)
