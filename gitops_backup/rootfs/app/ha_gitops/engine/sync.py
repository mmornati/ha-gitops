# SPDX-License-Identifier: Apache-2.0
"""Brings merged changes down to /config and applies them, keeping local drift."""

from dataclasses import dataclass

from ha_gitops.adapters.git import GitError
from ha_gitops.engine import stash
from ha_gitops.engine.apply import apply_changes
from ha_gitops.engine.context import RunContext
from ha_gitops.engine.revert import revert_invalid_pull


@dataclass(frozen=True, slots=True)
class Synced:
    pre: str
    post: str
    conflict: bool = False
    conflict_detail: str = ""


def sync_upstream(ctx: RunContext) -> Synced | None:
    """Stash local drift, ``pull --rebase``, put the drift back.

    None when the pull failed: the status is already written and /config is
    as it was.
    """
    git, settings = ctx.git, ctx.settings
    stash_sha = ""
    if git.out("status", "--porcelain"):
        git.run("stash", "-q", "--include-untracked")
        stash_sha = git.out("rev-parse", "refs/stash")
    pre = git.out("rev-parse", "HEAD")
    if not git.ok("pull", "-q", "--rebase", "origin", settings.base_branch):
        git.run("rebase", "--abort", check=False)
        if stash_sha:
            git.run("stash", "pop", "-q", check=False)
        ctx.status("error", "pull_rebase_failed", f"resolve manually in {settings.config_dir}")
        ctx.hass.fire_event("error", {"reason": "pull_rebase_failed"})
        return None
    post = git.out("rev-parse", "HEAD")

    conflict, detail = False, "kept live config where conflicting"
    if stash_sha and not git.ok("stash", "pop", "-q"):
        try:
            diverged = stash.reconcile(git, stash_sha)
        except GitError, OSError:
            git.run("reset", "-q", check=False)
            conflict = True
            detail = "could not restore every live file, see git stash list"
            ctx.log(f"⚠ warning:stash_conflict — {detail} ({stash_sha[:7]})")
        else:
            # Unstage everything: an unmerged index blocks the next run's
            # stash, and the kept live files must be plain drift for the backup.
            git.run("reset", "-q")
            git.run("stash", "drop", "-q", check=False)
            if diverged:
                conflict = True
                ctx.log(f"⚠ warning:stash_conflict — {detail}")
            else:
                ctx.log("Stash pop failed, but no live file differs from upstream; no conflict")
    return Synced(pre, post, conflict, detail)


def apply_pulled(ctx: RunContext, synced: Synced) -> None:
    """Applies what came down to the running Home Assistant.

    The range starts at the last commit that was applied, not at this run's
    pull: a dry run (or a run that failed the config check) pulls without
    applying, and the next real run must still apply those changes.
    """
    state, settings = ctx.state, ctx.settings
    applied_from = state.applied_head()
    if not applied_from or not ctx.git.ok("merge-base", "--is-ancestor", applied_from, synced.post):
        # First run, or history was rewritten: start from this pull
        applied_from = synced.pre
        state.set_applied_head(synced.pre)
    if settings.dry_run or applied_from == synced.post:
        return
    ctx.log(
        f"Upstream sync: {applied_from[:7]} → {synced.post[:7]} — applying "
        f"({settings.apply_after_pull})"
    )
    if synced.pre != synced.post:
        ctx.hass.fire_event("pulled", {"from": synced.pre, "to": synced.post})
    if apply_changes(ctx, applied_from, synced.post):
        state.set_applied_head(synced.post)
        state.set_invalid_head(None)
    elif state.invalid_head() != synced.post:
        state.set_invalid_head(synced.post)
        if settings.on_invalid_config == "revert_pr":
            revert_invalid_pull(ctx, applied_from, synced.post)
