# SPDX-License-Identifier: Apache-2.0
"""First run: turns /config into a repository and pushes the initial import."""

import shutil

from ha_gitops.engine import hygiene
from ha_gitops.engine.context import RunContext
from ha_gitops.engine.scan import ScanError, hold_back_secrets, report_held

SCOPE_NOTE = (
    "⚠ This app versions DECLARATIVE config only. It does NOT back up secrets.yaml, "
    ".storage/ (UI-configured integrations, logins, device/entity registries, dashboards), "
    "the database, or app data — those are excluded on purpose. For a 100% restore you MUST "
    "also run HA full backups (e.g. the Google Drive Backup app). See this app's DOCS → "
    "'What is backed up where'."
)


def bootstrap(ctx: RunContext) -> None:
    """The only time anything is pushed to the base branch; later it is all PRs."""
    settings, git = ctx.settings, ctx.git
    ctx.log(f"No git repo in {settings.config_dir} — bootstrapping")
    ctx.log(SCOPE_NOTE)
    if settings.dry_run:
        hygiene.seed_gitignore(ctx)
        ctx.log(f"DRY RUN: would git init, commit initial import, push to {settings.base_branch}")
        ctx.status("success", "dry_run", "bootstrap")
        return
    try:
        git.run("init", "-q", "-b", settings.base_branch)
        hygiene.seed_gitignore(ctx)
        hygiene.write_secret_excludes(ctx)
        git.run("add", "-A")
        hold_back_secrets(ctx)
        signoff = ["-s"] if settings.signoff else []
        git.run("commit", "-q", *signoff, "-m", "chore: initial Home Assistant config import")
        git.run("remote", "add", "origin", settings.remote_url)
        git.run("push", "-q", "-u", "origin", settings.base_branch)
    except ScanError:
        # Leave no half-made repository: the next run bootstraps again
        shutil.rmtree(git.repo / ".git", ignore_errors=True)
        ctx.status("error", "secret_scan", "the secret scan could not run")
        return
    except Exception:
        shutil.rmtree(git.repo / ".git", ignore_errors=True)
        raise
    ctx.status("success", "bootstrap", f"initial import pushed to {settings.base_branch}")
    report_held(ctx)
    ctx.log("Bootstrap complete")
