# SPDX-License-Identifier: Apache-2.0
"""One engine run, from the lock to the final status.

The controller decides WHEN to run; the engine decides WHAT to do. The mode
selects the job:

* ``full``    sync upstream, apply it, then back up local drift as a PR
* ``sync``    sync upstream and apply it; leave local drift alone
* ``revert``  open a PR that reverts one commit on the base branch
* ``discard`` put /config back to the base branch, dropping local drift

Nothing is ever pushed to the base branch after the first import.
"""

import os
import sys
from collections.abc import Mapping

from ha_gitops.adapters.git import Git, GitError, git_env
from ha_gitops.adapters.github import GitHub
from ha_gitops.adapters.hass import HomeAssistant
from ha_gitops.adapters.state import StateStore
from ha_gitops.engine import hygiene
from ha_gitops.engine.backup import backup_drift
from ha_gitops.engine.bootstrap import bootstrap
from ha_gitops.engine.context import MODES, RunContext
from ha_gitops.engine.discard import discard_drift
from ha_gitops.engine.revert import revert_commit
from ha_gitops.engine.sync import apply_pulled, sync_upstream
from ha_gitops.settings import Settings

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_LOCKED = 75  # EX_TEMPFAIL: another run holds the lock


def build_context(env: Mapping[str, str] | None = None) -> RunContext:
    env = os.environ if env is None else env
    settings = Settings.load(env)
    mode = env.get("GITOPS_MODE", "full")
    return RunContext(
        settings=settings,
        git=Git(settings.config_dir, git_env(settings, env)),
        github=GitHub(settings.github_api, settings.github_repo, settings.github_token),
        hass=HomeAssistant(settings.supervisor_url, settings.supervisor_token),
        state=StateStore(settings.config_dir, settings.data_dir),
        mode=mode if mode in MODES else "full",
        trigger=env.get("GITOPS_TRIGGER", "manual"),
        revert_sha=env.get("REVERT_SHA", "").strip().lower(),
    )


def run(ctx: RunContext) -> int:
    settings = ctx.settings
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # Input validation at the trust boundary
    problem = settings.problem()
    if problem:
        ctx.status("error", "config", problem)
        return EXIT_ERROR
    # One run at a time: the controller, the webhook, the watcher and a manual
    # run must never stash or rebase /config concurrently.
    with ctx.state.run_lock(settings.lock_wait) as locked:
        if not locked:
            ctx.log(f"Another run still holds the lock after {settings.lock_wait}s; giving up")
            return EXIT_LOCKED
        try:
            _run_locked(ctx)
        except GitError as err:
            ctx.log(f"❌ {err}")
            ctx.status("error", "git", f"git {err.command} failed, see the log")
        finally:
            ctx.state.rotate_log()
    return EXIT_ERROR if ctx.report.failed else EXIT_OK


def _run_locked(ctx: RunContext) -> None:
    if not hygiene.is_repository(ctx.git.repo):
        bootstrap(ctx)
        return
    hygiene.point_origin(ctx)
    hygiene.seed_gitignore(ctx)
    hygiene.write_secret_excludes(ctx)
    hygiene.restore_base_head(ctx)
    try:
        hygiene.clear_unmerged(ctx)
        if ctx.mode == "revert":
            revert_commit(ctx)
        elif ctx.mode == "discard":
            discard_drift(ctx)
        else:
            _sync_then_backup(ctx)
    finally:
        hygiene.restore_base_head(ctx)


def _sync_then_backup(ctx: RunContext) -> None:
    synced = sync_upstream(ctx)
    if synced is None:
        return
    apply_pulled(ctx, synced)
    # If a prior run committed a secret, untrack it now (before the change
    # check) so the removal is captured as drift and goes out via a PR.
    hygiene.untrack_secrets(ctx)
    if synced.conflict:
        ctx.status("warning", "stash_conflict", synced.conflict_detail)
    if ctx.mode == "sync":
        ctx.report.changed = len(ctx.git.out("status", "--porcelain").splitlines())
        if not synced.conflict:
            detail = "synced" if synced.pre != synced.post else "up_to_date"
            ctx.status("success", detail, synced.post[:7])
        return
    backup_drift(ctx)


def main() -> int:
    return run(build_context())


if __name__ == "__main__":
    sys.exit(main())
