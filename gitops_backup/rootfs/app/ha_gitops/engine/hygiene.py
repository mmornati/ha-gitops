# SPDX-License-Identifier: Apache-2.0
"""Keeps the repository in /config in a state every run can start from."""

import contextlib
import tempfile
from pathlib import Path

from ha_gitops.adapters.git import GitError
from ha_gitops.engine.context import RunContext
from ha_gitops.rules import ignore


def seed_gitignore(ctx: RunContext) -> None:
    path = ctx.git.repo / ".gitignore"
    if not path.exists():
        ctx.log("Seeding recommended Home Assistant .gitignore")
        path.write_text(ignore.seed_gitignore(), encoding="utf-8")


def write_secret_excludes(ctx: RunContext) -> None:
    """Writes the never-commit patterns to ``.git/info/exclude``.

    That is git's local, un-shared ignore file, so secrets, credentials and
    the app's own metadata stay out of backups even in a repository that
    already has its own ``.gitignore``. Called before the stash, so untracked
    secrets are never swept in. Idempotent: the managed block is replaced.
    """
    info = ctx.git.repo / ".git" / "info"
    if not (ctx.git.repo / ".git").is_dir():
        return
    info.mkdir(parents=True, exist_ok=True)
    exclude = info / "exclude"
    existing = (
        exclude.read_text(encoding="utf-8", errors="surrogateescape") if (exclude.exists()) else ""
    )
    exclude.write_text(ignore.managed_exclude(existing), encoding="utf-8", errors="surrogateescape")


def untrack_secrets(ctx: RunContext) -> None:
    """Stops tracking a secret or credential that an earlier run committed.

    Called after the upstream sync (a staged deletion would be lost in the
    stash), so the removal counts as drift and goes out through the next
    backup PR for review. Uses git's own ignore matcher, so the ``!``
    negations behave exactly as they do in ``.gitignore``.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".exclude", encoding="utf-8") as patterns:
        patterns.write("\n".join(ignore.NEVER_COMMIT) + "\n")
        patterns.flush()
        tracked = ctx.git.records("ls-files", "-z", "-c", "-i", f"--exclude-from={patterns.name}")
    for path in tracked:
        ctx.log(f"⚠ Untracking previously-committed sensitive path: {path}")
    ctx.git.with_paths("rm", "-q", "--cached", paths=tracked)


def restore_base_head(ctx: RunContext) -> None:
    """Points HEAD back at the base branch without touching the working tree.

    A plain ``git checkout`` would rewrite /config to the pre-backup base
    branch, rolling the live config back until the next run pulls the merged
    PR (and losing the drift for good if the PR is never merged). The drift
    stays as uncommitted changes and is reconciled by the next run. Also the
    repair for a run that was killed with HEAD on a backup branch.
    """
    with contextlib.suppress(GitError):
        current = ctx.git.out("branch", "--show-current", check=False)
        if current and current != ctx.settings.base_branch:
            ctx.git.run("symbolic-ref", "HEAD", ctx.settings.base_ref)
            ctx.git.run("reset", "-q")


def clear_unmerged(ctx: RunContext) -> None:
    """Drops unmerged index entries left by an older version.

    They make every git write fail ("needs merge"). The working tree (the
    live config) is left untouched.
    """
    if ctx.git.out("ls-files", "-u"):
        ctx.log("Clearing unmerged index entries left by an earlier stash conflict")
        ctx.git.run("reset", "-q")


def point_origin(ctx: RunContext) -> None:
    """Sets the remote URL; also scrubs a token left there before 0.4.0."""
    verb = "set-url" if ctx.git.ok("remote", "get-url", "origin") else "add"
    ctx.git.run("remote", verb, "origin", ctx.settings.remote_url)


def is_repository(path: Path) -> bool:
    return (path / ".git").is_dir()
