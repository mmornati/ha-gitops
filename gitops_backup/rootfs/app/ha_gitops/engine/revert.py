# SPDX-License-Identifier: Apache-2.0
"""Pull requests that take commits back out of the base branch."""

import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

from ha_gitops.adapters.git import GitError
from ha_gitops.adapters.github import GitHubError
from ha_gitops.engine.context import RunContext
from ha_gitops.settings import SHA_RE


class RevertConflictError(Exception):
    """A commit does not revert cleanly."""


class PushFailedError(Exception):
    """The revert branch could not be pushed."""


def revert_onto(ctx: RunContext, onto: str, branch: str, shas: Sequence[str]) -> None:
    """Pushes a branch that reverts the commits, newest first.

    Works in a separate worktree, so the live /config is never touched.
    """
    git, settings = ctx.git, ctx.settings
    parent = Path(tempfile.mkdtemp(prefix="gitops-revert-"))
    tree = parent / "wt"
    try:
        git.run("worktree", "add", "-q", "--detach", str(tree), onto)
        work = git.at(tree)
        for sha in shas:
            merge = len(git.out("rev-list", "--parents", "-n", "1", sha).split()) > 2
            args = ["--no-edit", "-m", "1"] if merge else ["--no-edit"]
            if not work.ok("revert", *args, sha):
                work.run("revert", "--abort", check=False)
                raise RevertConflictError
            if settings.signoff:
                work.run("commit", "-q", "--amend", "--no-edit", "-s")
        if not settings.dry_run and not work.ok(
            "push", "-q", "origin", f"HEAD:refs/heads/{branch}"
        ):
            raise PushFailedError
    finally:
        git.run("worktree", "remove", "--force", str(tree), check=False)
        shutil.rmtree(parent, ignore_errors=True)
        git.run("worktree", "prune", check=False)


def revert_commit(ctx: RunContext) -> None:
    """Opens a PR that reverts ``ctx.revert_sha`` on the base branch."""
    git, settings = ctx.git, ctx.settings
    base = settings.base_branch
    if not SHA_RE.match(ctx.revert_sha):
        ctx.status("error", "revert", "invalid commit id")
        return
    git.run("fetch", "-q", "origin", base)
    onto = git.out("rev-parse", "FETCH_HEAD")
    sha = git.rev(f"{ctx.revert_sha}^{{commit}}")
    if not sha:
        ctx.status("error", "revert", f"unknown commit {ctx.revert_sha}")
        return
    if not git.ok("merge-base", "--is-ancestor", sha, onto):
        ctx.status("error", "revert", f"{sha[:7]} is not on {base}")
        return
    subject = git.out("log", "-1", "--format=%s", sha)
    branch = f"{settings.branch_prefix}/revert-{sha[:7]}"
    try:
        revert_onto(ctx, onto, branch, [sha])
    except RevertConflictError:
        ctx.status("error", "revert", f"{sha[:7]} does not revert cleanly")
        return
    except PushFailedError, GitError:
        ctx.status("error", "revert", f"could not push {branch}")
        return
    if settings.dry_run:
        ctx.status("success", "dry_run", f"would open revert PR for {sha[:7]}")
        return
    try:
        ctx.report.pr = ctx.github.create_pull(
            branch,
            base,
            f'Revert "{subject}"',
            f"Reverts {sha} on `{base}`. Opened from the HA GitOps panel; merge to roll the "
            "change back on the box.",
        )
    except GitHubError:
        ctx.status("warning", "branch_pushed", branch)
        return
    ctx.status("success", "revert_pr", ctx.report.pr.url)
    ctx.hass.fire_event("pr_opened", {"url": ctx.report.pr.url, "kind": "revert"})


def revert_invalid_pull(ctx: RunContext, pre: str, post: str) -> None:
    """``on_invalid_config: revert_pr``: a PR that reverts what was just pulled.

    The pulled commits failed the HA config check; this opens a PR that takes
    them back out, for the user to merge.
    """
    git, settings = ctx.git, ctx.settings
    base = settings.base_branch
    branch = f"{settings.branch_prefix}/revert-{post[:7]}"
    if settings.dry_run:
        return
    if git.ok("ls-remote", "--exit-code", "origin", f"refs/heads/{branch}"):
        ctx.log(f"Revert branch {branch} already exists; not opening another PR")
        return
    shas = git.out("rev-list", "--first-parent", f"{pre}..{post}").split()
    if not shas:
        return
    try:
        revert_onto(ctx, post, branch, shas)
    except RevertConflictError, PushFailedError, GitError:
        ctx.log(f"⚠ Could not prepare a revert PR for {pre[:7]}..{post[:7]}; revert by hand")
        return
    try:
        pull = ctx.github.create_pull(
            branch,
            base,
            f"Revert {pre[:7]}..{post[:7]}: config check failed",
            "The Home Assistant config check failed after these changes were pulled to the box, "
            f"so they were not applied. This PR takes them back out of `{base}`. Merge it to put "
            "the box back on a valid configuration, or fix the configuration in another PR and "
            "close this one.",
        )
    except GitHubError:
        ctx.log(f"⚠ Revert branch {branch} pushed, but the PR could not be opened")
        return
    ctx.log(f"Opened a revert PR for the invalid configuration: {pull.url}")
    ctx.hass.fire_event(
        "pr_opened", {"url": pull.url, "kind": "revert", "reason": "config_invalid"}
    )
    ctx.hass.notify(
        "HA GitOps: revert PR opened",
        "The pulled changes failed the configuration check. A PR that reverts them is ready "
        f"to merge: {pull.url}",
    )
