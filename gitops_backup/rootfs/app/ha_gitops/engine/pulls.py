# SPDX-License-Identifier: Apache-2.0
"""The open backup pull request: finding it and writing its description."""

from typing import Any

from ha_gitops.adapters.github import GitHubError, PullRequest
from ha_gitops.engine.context import RunContext
from ha_gitops.rules import pr_body
from ha_gitops.settings import REF_RE


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def find_open_pr(ctx: RunContext) -> PullRequest | None:
    """The open backup PR, so new drift is added to it instead of a new PR."""
    settings, state = ctx.settings, ctx.state
    prefix = f"{settings.branch_prefix}/"
    try:
        pulls = ctx.github.open_pulls(settings.base_branch)
    except GitHubError:
        return _remembered_pr(ctx)
    mine = []
    for pull in pulls:
        head = _mapping(pull.get("head"))
        ref = str(head.get("ref") or "")
        repo = _mapping(head.get("repo"))
        if (
            ref.startswith(prefix)
            and "/revert-" not in ref
            and REF_RE.match(ref)
            and repo.get("full_name") == settings.github_repo
        ):
            mine.append((str(pull.get("created_at") or ""), ref, pull))
    if not mine:
        state.set_open_pr(None)
        return None
    _, ref, pull = max(mine, key=lambda item: item[0])
    found = PullRequest(ref, str(pull.get("html_url") or ""), str(pull.get("number") or ""))
    state.set_open_pr(found)
    return found


def _remembered_pr(ctx: RunContext) -> PullRequest | None:
    """GitHub is unreachable: fall back to the branch that was pushed last.

    Only as long as it still exists and has not been merged into the base
    branch yet.
    """
    known = ctx.state.open_pr()
    if known is None:
        return None
    if (
        not REF_RE.match(known.branch)
        or not ctx.git.ok("fetch", "-q", "origin", f"refs/heads/{known.branch}")
        or ctx.git.ok("merge-base", "--is-ancestor", "FETCH_HEAD", ctx.settings.base_ref)
    ):
        ctx.state.set_open_pr(None)
        return None
    return known


def describe(ctx: RunContext, base: str, head: str) -> str:
    """The PR body for the files that differ between two commits."""
    records = ctx.git.records("diff", "--name-status", "--no-renames", "-z", base, head)
    changes = list(zip(records[0::2], records[1::2], strict=False))
    return pr_body.render(
        changes,
        when=f"{ctx.now():%Y-%m-%d %H:%M}",
        trigger=ctx.trigger,
        held=ctx.report.held,
    )
