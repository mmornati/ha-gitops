# SPDX-License-Identifier: Apache-2.0
"""Puts /config back to the base branch, dropping local drift."""

from ha_gitops.adapters.github import GitHubError
from ha_gitops.engine.apply import apply_changes
from ha_gitops.engine.context import RunContext
from ha_gitops.engine.pulls import find_open_pr
from ha_gitops.engine.scan import ScanError, hold_back_secrets

SAFETY_REFS = "refs/gitops/discarded"
SAFETY_COPIES = 5


def discard_drift(ctx: RunContext) -> None:
    """Files git ignores (secrets.yaml, .storage/, databases…) and files held
    back as secret-like are left alone. What is dropped stays recoverable
    under ``refs/gitops/discarded/``.
    """
    git, settings, state = ctx.git, ctx.settings, ctx.state
    git.run("add", "-A")
    try:
        hold_back_secrets(ctx)
    except ScanError:
        git.run("reset", "-q", check=False)
        ctx.status("error", "secret_scan", "the secret scan could not run")
        return
    staged = git.records("diff", "--cached", "--name-only", "-z")
    if not staged:
        git.run("reset", "-q")
        ctx.status("success", "no_changes", "nothing to discard")
        return
    count = ctx.report.changed = len(staged)
    if settings.dry_run:
        ctx.log(f"DRY RUN: {count} changed file(s) would be discarded:")
        ctx.log_raw(git.out("diff", "--cached", "--name-status"))
        git.run("reset", "-q")
        ctx.status("success", "dry_run", f"would discard {count} files")
        return
    now = ctx.now()
    git.run(
        "stash", "push", "--staged", "-q", "-m", f"discarded by HA GitOps {now:%Y-%m-%d %H:%M:%S}"
    )
    stash = git.out("rev-parse", "refs/stash")
    ref = f"{SAFETY_REFS}/{now:%Y%m%d-%H%M%S}"
    git.run("update-ref", ref, stash)
    git.run("stash", "drop", "-q")
    git.run("reset", "-q")
    # Keep the most recent safety copies only
    refs = git.out("for-each-ref", "--sort=-refname", "--format=%(refname)", SAFETY_REFS).split()
    for old in refs[SAFETY_COPIES:]:
        git.run("update-ref", "-d", old)
    ctx.log(f"Discarded {count} local change(s); safety copy: git stash apply {ref}")

    # The open backup PR holds exactly what was just dropped: close it
    pull = find_open_pr(ctx)
    if pull is not None:
        if pull.number:
            try:
                ctx.github.close_pull(pull.number)
            except GitHubError, ValueError:
                pass
            else:
                ctx.log(f"Closed backup PR #{pull.number}")
        git.run("push", "-q", "origin", "--delete", f"refs/heads/{pull.branch}", check=False)
        git.run("branch", "-q", "-D", pull.branch, check=False)
    state.set_open_pr(None)
    ctx.report.pr = None

    # Home Assistant still runs the discarded config: load what is on disk now
    apply_changes(ctx, stash, "HEAD")
    ctx.status("success", "discarded", f"{count} files")
    ctx.hass.fire_event("discarded", {"files": count, "ref": ref})
