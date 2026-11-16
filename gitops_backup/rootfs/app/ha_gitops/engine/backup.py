# SPDX-License-Identifier: Apache-2.0
"""Backs local drift up as a pull request, never as a push to the base branch."""

import contextlib

from ha_gitops.adapters.github import GitHubError, PullRequest
from ha_gitops.engine import hygiene
from ha_gitops.engine.context import RunContext
from ha_gitops.engine.pulls import describe, find_open_pr
from ha_gitops.engine.scan import ScanError, hold_back_secrets, report_held


def _new_branch(ctx: RunContext) -> str:
    name = f"{ctx.settings.branch_prefix}/{ctx.now():%Y-%m-%d-%H%M%S}"
    candidate, n = name, 1
    while ctx.git.rev(f"refs/heads/{candidate}"):  # two backups within one second
        n += 1
        candidate = f"{name}-{n}"
    return candidate


def backup_drift(ctx: RunContext) -> None:
    git, settings, state, report = ctx.git, ctx.settings, ctx.state, ctx.report
    base = settings.base_ref
    drift = git.out("status", "--porcelain")
    if not drift:
        ctx.status("success", "no_changes", "")
        ctx.log("No changes to back up")
        return
    report.changed = len(drift.splitlines())
    if settings.dry_run:
        ctx.log(f"DRY RUN: {report.changed} changed file(s) would be backed up:")
        ctx.log_raw(drift)
        ctx.status("success", "dry_run", f"{report.changed} files")
        return

    pull = report.pr = find_open_pr(ctx)
    rolling = pull is not None and git.ok("fetch", "-q", "origin", f"refs/heads/{pull.branch}")
    try:
        if pull is not None and rolling:
            # Add the drift on top of the open PR: point HEAD at its tip and
            # stage the live tree. The working tree is not touched, and the
            # push is a plain fast-forward.
            branch = pull.branch
            git.run("update-ref", f"refs/heads/{branch}", "FETCH_HEAD")
            git.run("symbolic-ref", "HEAD", f"refs/heads/{branch}")
            git.run("reset", "-q")
            hygiene.untrack_secrets(ctx)
            git.run("add", "-A")
            held = hold_back_secrets(ctx)
            if git.ok("diff", "--cached", "--quiet"):
                if held:
                    ctx.status("warning", "secrets_held", f"{len(held)} files")
                    report_held(ctx)
                else:
                    ctx.status("success", "pr_unchanged", pull.url or branch)
                ctx.log("Drift is already in the open backup PR")
                return
        else:
            # Stage and scan before creating the branch, so a run that holds
            # everything back leaves no empty branch behind.
            git.run("add", "-A")
            held = hold_back_secrets(ctx)
            if git.ok("diff", "--cached", "--quiet"):
                ctx.status("warning", "secrets_held", f"{len(held)} files")
                ctx.log("Nothing left to back up after holding back secret-like files")
                report_held(ctx)
                return
            branch = _new_branch(ctx)
            git.run("checkout", "-q", "-b", branch)
    except ScanError:
        ctx.status("error", "secret_scan", "the secret scan could not run")
        return

    step = len(git.records("diff", "--cached", "--name-only", "-z"))
    message = f"chore: auto backup {ctx.now():%Y-%m-%d %H:%M} ({step} files)"
    if rolling and not git.ok("merge-base", "--is-ancestor", base, "HEAD"):
        # The base branch moved on since this PR was opened. A plain commit
        # would show those merged changes in the PR as if they were drift:
        # record the base as a second parent, so the PR stays limited to drift.
        if settings.signoff:
            message += f"\n\nSigned-off-by: {settings.commit_name} <{settings.commit_email}>"
        tree = git.out("write-tree")
        commit = git.out("commit-tree", tree, "-p", "HEAD", "-p", base, "-m", message)
        git.run("update-ref", "HEAD", commit)
    else:
        git.run("commit", "-q", *(["-s"] if settings.signoff else []), "-m", message)
    git.run("push", "-q", "-u", "origin", branch)
    pull = report.pr = PullRequest(branch, pull.url if pull else "", pull.number if pull else "")
    state.set_open_pr(pull)

    total = report.changed = len(git.records("diff", "--name-only", "-z", base, "HEAD"))
    title = f"chore: config backup ({total} files)"
    body = describe(ctx, base, "HEAD")
    report_held(ctx)
    if rolling and pull.number:
        # If this fails the commits are pushed all the same; only the description is stale
        with contextlib.suppress(GitHubError, ValueError):
            ctx.github.update_pull(pull.number, title, body)
        ctx.status("success", "pr_updated", pull.url)
        ctx.log(f"✅ Backup added to the open PR: {pull.url}")
        ctx.hass.fire_event("pr_updated", {"url": pull.url, "files": total})
        return
    try:
        pull = report.pr = ctx.github.create_pull(branch, settings.base_branch, title, body)
    except GitHubError:
        ctx.status("warning", "branch_pushed", branch)
        ctx.log("WARNING: branch pushed but PR creation failed")
        return
    state.set_open_pr(pull)
    ctx.status("success", "pr_created", pull.url)
    ctx.log(f"✅ Backup complete — PR opened: {pull.url}")
    ctx.hass.fire_event("pr_opened", {"url": pull.url, "files": total})
