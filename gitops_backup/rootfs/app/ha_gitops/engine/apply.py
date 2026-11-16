# SPDX-License-Identifier: Apache-2.0
"""Loads pulled changes into the running Home Assistant."""

import contextlib

from ha_gitops.adapters.hass import HassError
from ha_gitops.engine.context import RunContext
from ha_gitops.rules import apply as rules


def check_config(ctx: RunContext, head: str) -> bool:
    """True when HA says the config on disk is valid (or cannot be asked)."""
    try:
        check = ctx.hass.check_config()
    except HassError:
        ctx.log("⚠ Could not run the HA config check; continuing without it")
        return True
    if check.valid:
        ctx.log("HA config check: valid")
        return True
    ctx.log(f"❌ HA config check failed — not applying: {check.errors}")
    # Every later run checks again (the config may have been fixed on the
    # box), but the same commit is only reported once.
    if ctx.state.invalid_head() != head:
        ctx.hass.notify(
            "HA GitOps: config check failed",
            "The merged changes were pulled to /config but NOT applied, because the "
            f"configuration check failed:\n\n{check.errors}\n\nDo not restart Home Assistant "
            "until this is fixed.",
        )
        ctx.hass.fire_event("error", {"reason": "config_invalid", "errors": check.errors})
    return False


def _refresh_dashboards(ctx: RunContext) -> bool:
    try:
        from ha_gitops.adapters.hass_ws import refresh_dashboards_sync

        count = refresh_dashboards_sync(ctx.settings.ha_websocket, ctx.settings.supervisor_token)
    except Exception as err:
        ctx.log(f"Dashboard refresh failed: {err}")
        ctx.log("⚠ Could not refresh open dashboards; reload the browser tab instead")
        return False
    ctx.log(f"Refreshed {count} YAML dashboard(s) for open browser sessions")
    return True


def apply_changes(ctx: RunContext, pre: str, post: str) -> bool:
    """Applies what changed between two commits. False: the config is invalid."""
    settings, report = ctx.settings, ctx.report
    mode = settings.apply_after_pull
    if mode == "off":
        report.applied = "off"
        return True
    if not ctx.hass.available:
        ctx.log(
            f"⚠ apply_after_pull={mode} but SUPERVISOR_TOKEN unset; skipping — "
            "needs homeassistant_api"
        )
        report.applied = "skipped"
        return True
    todo = rules.plan(mode, ctx.git.records("diff", "--name-only", "-z", pre, post))
    if todo.empty:
        ctx.log("Nothing to apply for the pulled changes")
        report.applied = "none"
        return True
    post_sha = ctx.git.rev(post) or post
    if todo.needs_config_check and not check_config(ctx, post_sha):
        report.applied = "config_invalid"
        return False

    done: list[str] = []
    for service in todo.services:
        name = service.replace("/", ".", 1)
        try:
            ctx.hass.call_service(service)
        except HassError:
            ctx.log(f"⚠ Failed to call {name}; apply manually in HA")
        else:
            ctx.log(f"✅ Applied: {name}")
            done.append(name)
    restarted = False
    if todo.restart:
        if mode == "restart" or settings.auto_restart:
            ctx.log("Restarting Home Assistant to apply the pulled changes")
            done.append("homeassistant.restart")
            restarted = True
            with contextlib.suppress(HassError):  # HA drops the call as it goes down
                ctx.hass.restart()
        else:
            ctx.log("⚠ The pulled changes need a Home Assistant restart to take effect")
            done.append("restart_required")
            ctx.hass.notify(
                "HA GitOps: restart required",
                f"Merged changes ({pre[:7]} → {post_sha[:7]}) touch configuration that only "
                "loads on start. The config check passed. Restart Home Assistant to apply them.",
            )
            ctx.hass.fire_event("restart_required", {"from": pre, "to": post_sha})
    # Home Assistant re-reads a YAML dashboard only when a client asks for it,
    # so open browser tabs keep the old cards. A restart reconnects them anyway.
    if todo.refresh_dashboards and not restarted and _refresh_dashboards(ctx):
        done.append("dashboards.refresh")
    report.applied = ",".join(done) or "none"
    ctx.hass.fire_event("applied", {"from": pre, "to": post_sha, "actions": ",".join(done)})
    return True
