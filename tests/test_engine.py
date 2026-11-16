# SPDX-License-Identifier: Apache-2.0
"""The engine against real git repositories: every flow it has to get right.

Ported from the shell self-check of the bash engine, case for case.
"""

import fcntl
import json
from pathlib import Path

import pytest
from ha_gitops.adapters.git import Git, GitError

from tests.conftest import Box, FakeApi

SECRET = "password: SuperSecretPassw0rd\n"


# ---- bootstrap ---------------------------------------------------------------


def test_dry_run_bootstrap_only_seeds_the_gitignore(tmp_path: Path) -> None:
    box = Box(tmp_path, dry_run=True)
    assert box.run() == 0
    assert "secrets.yaml" in box.read(".gitignore").splitlines()
    assert box.line() == "success:dry_run:bootstrap"
    assert not (box.config / ".git").exists()


def test_bootstrap_pushes_the_initial_import_without_secrets(tmp_path: Path) -> None:
    box = Box(tmp_path)
    box.write("rest.yaml", "api_key: abcdef1234567890\n")  # must not reach the base branch
    assert box.run() == 0
    tracked = box.git("ls-files").splitlines()
    assert "configuration.yaml" in tracked
    assert "rest.yaml" not in tracked
    assert "secrets.yaml" not in tracked
    assert "initial" in box.git("log", "--oneline", "-1")
    assert box.ogit("rev-parse", "main") == box.git("rev-parse", "HEAD")
    assert box.status()["held"] == ["rest.yaml"]
    assert box.line().startswith("success:bootstrap:")
    # The token never lands in /config/.git/config
    assert "test-token" not in (box.config / ".git" / "config").read_text()


def test_bootstrap_that_cannot_scan_leaves_no_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = Box(tmp_path)

    def broken(*_args: object) -> list[tuple[str, str]]:
        raise OSError("cat-file died")

    monkeypatch.setattr("ha_gitops.engine.scan.scan_index", broken)
    assert box.run() == 1
    assert box.line() == "error:secret_scan:the secret scan could not run"
    assert not (box.config / ".git").exists()


def test_missing_settings_are_an_error(tmp_path: Path) -> None:
    box = Box(tmp_path, github_token="")
    assert box.run() == 1
    assert box.status()["detail"] == "config"
    box.options.update(github_token="t", base_branch="--upload-pack=x")
    assert box.run() == 1
    assert "plain branch names" in box.status()["extra"]
    box.options.update(base_branch="main", github_repo="not a repo")
    assert box.run() == 1
    assert "owner/name" in box.status()["extra"]


# ---- backup ------------------------------------------------------------------


def test_drift_goes_to_a_backup_branch_and_the_live_config_stays(box: Box) -> None:
    box.write("configuration.yaml", "changed\n")
    box.write("new_automation.yaml", "new file\n")
    assert box.run() == 0
    assert len(box.remote_branches()) == 1
    # The PR API is unreachable: the branch is pushed, the PR is not opened
    assert box.line().startswith("warning:branch_pushed:auto-backup/")
    assert box.git("branch", "--show-current") == "main"
    # Returning to main must not roll the live config back to the old main
    assert box.read("configuration.yaml") == "changed\n"
    assert box.read("new_automation.yaml") == "new file\n"
    status = box.status()
    assert status["state"] == "warning"
    assert len(status["local_head"]) == 40
    assert status["changed"] == 2


def test_no_drift_is_a_quiet_success(box: Box) -> None:
    assert box.run() == 0
    assert box.line() == "success:no_changes:"
    assert box.remote_branches() == []


def test_dry_run_backs_nothing_up(box: Box) -> None:
    box.write("new.yaml", "x: 1\n")
    assert box.run(DRY_RUN_OVERRIDE="true") == 0
    assert box.line() == "success:dry_run:1 files"
    assert box.remote_branches() == []
    assert "?? new.yaml" in box.log()


def test_merged_backup_syncs_clean(box: Box) -> None:
    box.write("configuration.yaml", "changed\n")
    box.run()
    box.merge_backup()
    assert box.run() == 0
    assert box.line() == "success:no_changes:"
    assert box.read("configuration.yaml") == "changed\n"
    assert box.git("rev-parse", "HEAD") == box.dgit("rev-parse", "HEAD")
    box.settled()


def test_merged_backup_with_new_moved_and_deleted_files_is_not_a_conflict(box: Box) -> None:
    """The live tree keeps the drift after a backup, so the sync that pulls the
    merged PR stashes files the pull brings back: ``git stash pop`` fails with
    "already exists" and, for a moved file, a rename/delete conflict. Nothing
    differs, so this must not be reported as a conflict.
    """
    body = "".join(f"{n}\n" for n in range(1, 201))
    box.write("helpers/moved.py", body)
    box.write("gone.yaml", "old\n")
    box.run()
    box.merge_backup()
    assert box.run("sync") == 0
    assert box.line().startswith("success:synced")
    assert box.git("status", "--porcelain") == ""
    box.settled()

    box.write("client/moved.py", body + "edited\n")
    (box.config / "helpers/moved.py").unlink()
    (box.config / "gone.yaml").unlink()
    box.write("brand_new.yaml", "brand new\n")
    box.run()
    box.merge_backup()
    before = box.log().count("stash_conflict")
    assert box.run("sync") == 0
    assert box.line().startswith("success:synced")
    assert box.log().count("stash_conflict") == before
    assert (box.config / "client/moved.py").exists()
    assert not (box.config / "helpers/moved.py").exists()
    assert not (box.config / "gone.yaml").exists()
    assert box.read("brand_new.yaml") == "brand new\n"
    assert box.git("status", "--porcelain") == ""
    box.settled()


def test_edit_made_between_backup_and_merge_survives_and_is_reported(box: Box) -> None:
    box.write("window.yaml", "v1\n")
    box.write("tracked.yaml", "t1\n")
    box.run()
    box.merge_backup()
    box.write("window.yaml", "v2 live edit\n")
    box.write("unrelated.yaml", "unrelated\n")
    box.run("sync")  # brings window.yaml v1 down as a tracked file… after the live edit
    assert box.line().startswith("warning:stash_conflict")
    assert box.read("window.yaml") == "v2 live edit\n"
    assert box.read("unrelated.yaml") == "unrelated\n"
    box.settled()
    box.run()
    box.merge_backup()
    box.run("sync")
    assert box.line().startswith("success:synced")


def test_live_file_that_cannot_be_restored_stays_in_the_stash(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping the stash would lose the only copy of the live file."""
    box.write("stuck.yaml", "v1\n")
    box.run()
    box.merge_backup()
    box.write("stuck.yaml", "v2 only copy\n")
    real = Git.run

    def failing(self: Git, *args: str, **kwargs: object):  # type: ignore[no-untyped-def]
        if "checkout" in args and args[-1] == "stuck.yaml":
            raise GitError(args, 1, "simulated failure")
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Git, "run", failing)
    box.run("sync")
    monkeypatch.undo()
    assert box.line().startswith("warning:stash_conflict:could not restore")
    assert box.git("show", "stash@{0}^3:stuck.yaml") == "v2 only copy"
    assert box.git("ls-files", "-u") == ""


def test_real_conflict_keeps_the_live_version_and_the_next_run_works(box: Box) -> None:
    box.dev_push("configuration.yaml", "upstream edit\n")
    box.write("configuration.yaml", "live edit\n")
    box.run()
    assert "warning:stash_conflict" in box.log()
    assert box.read("configuration.yaml") == "live edit\n"
    assert box.git("ls-files", "-u") == ""
    assert box.run() == 0
    assert box.read("configuration.yaml") == "live edit\n"


def test_repository_stuck_with_unmerged_entries_heals(box: Box) -> None:
    blob = box.git("hash-object", "-w", "configuration.yaml")
    entries = f"100644 {blob} 2\tconfiguration.yaml\n100644 {blob} 3\tconfiguration.yaml\n"
    import subprocess

    subprocess.run(
        ["git", "update-index", "--index-info"],
        cwd=box.config,
        env=box.env,
        input=entries.encode(),
        check=True,
    )
    assert box.git("ls-files", "-u") != ""
    assert box.run() == 0
    assert box.git("ls-files", "-u") == ""
    assert "Clearing unmerged index entries" in box.log()


def test_run_killed_on_a_backup_branch_is_put_back_on_the_base(box: Box) -> None:
    box.git("symbolic-ref", "HEAD", "refs/heads/auto-backup/killed")
    box.run("sync")
    assert box.git("symbolic-ref", "HEAD") == "refs/heads/main"


# ---- secrets -----------------------------------------------------------------


def test_migrated_repository_never_backs_up_secrets_or_app_files(box: Box) -> None:
    """A repository that came with its own .gitignore and was never excluded."""
    (box.config / ".git/info/exclude").unlink()
    box.write(".gitignore", "secrets.yaml\n")
    box.git("add", ".gitignore")
    box.git("commit", "-q", "-m", "user gitignore")
    box.write("gitops_backup.log", "log line\n")
    box.write(".google.token", "ya29.oauth-secret\n")
    box.write("zigbee2mqtt/secret.yaml", "network_key: [1, 2]\n")
    box.write("leaked.key", "PRIVATE KEY\n")  # committed by a prior run
    box.git("add", "-f", "leaked.key")
    box.git("commit", "-q", "-m", "oops: committed a key")
    # Backup and editor copies of secrets.yaml, and the ESPHome builder key
    box.write("secrets.yaml.bak-20260101", "wifi: abc\n")
    box.write("secrets.yaml.bak", "wifi: abc\n")
    box.write("automations.yaml.orig", "secret: old\n")
    box.write("entity_registry.bak.1793051273", "registry\n")
    box.write("esphome/.device-builder-peer-link-key.bin", "key\n")
    box.write("esphome/plug.yaml", "esphome: {name: plug}\n")
    # A tracked template must NOT be treated as a secret copy
    box.write("secrets.yaml.example", "api_key: CHANGE_ME\n")
    box.git("add", "secrets.yaml.example")
    box.git("commit", "-q", "-m", "add secrets template")
    box.write("configuration.yaml", "more drift\n")
    assert box.run() == 0
    tree = box.remote_files(box.open_pr()[0])
    for leaked in (
        ".gitops_backup_status",
        "gitops_backup.log",
        ".google.token",
        "leaked.key",
        "secrets.yaml",
        "zigbee2mqtt/secret.yaml",
        "secrets.yaml.bak",
        "secrets.yaml.bak-20260101",
        "automations.yaml.orig",
        "entity_registry.bak.1793051273",
        "esphome/.device-builder-peer-link-key.bin",
    ):
        assert leaked not in tree, leaked
    for kept in ("configuration.yaml", "secrets.yaml.example", "esphome/plug.yaml"):
        assert kept in tree, kept
    assert "Untracking previously-committed sensitive path: leaked.key" in box.log()
    assert "secrets.yaml.example" not in box.log()
    assert (box.config / "secrets.yaml.bak-20260101").exists()  # still on the box


def test_file_with_a_literal_credential_is_held_back(box: Box) -> None:
    box.write("mqtt.yaml", "mqtt:\n  " + SECRET)
    box.write("mqtt_ok.yaml", "mqtt:\n  password: !secret mqtt_password\n")
    odd = "odd\tname .yaml"  # a tab inside and a blank before the suffix
    box.write(odd, "api_key: abcdef1234567890\n")
    assert box.run() == 0
    tree = box.remote_files(box.open_pr()[0])
    assert "mqtt.yaml" not in tree
    assert not any("odd" in name for name in tree)
    assert "mqtt_ok.yaml" in tree
    assert (box.config / "mqtt.yaml").exists()
    assert "inline credential) in mqtt.yaml — held back" in box.log()
    assert "SuperSecretPassw0rd" not in box.log()
    assert sorted(box.status()["held"]) == ["mqtt.yaml", odd]
    # Nothing but the held files left to back up
    (box.config / odd).unlink()
    assert box.run() == 0
    assert box.line() == "warning:secrets_held:1 files"
    # Held on a first backup too: no empty branch is left behind
    assert len(box.remote_branches()) == 1


def test_only_held_files_open_no_branch(box: Box) -> None:
    box.write("mqtt.yaml", SECRET)
    assert box.run() == 0
    assert box.line() == "warning:secrets_held:1 files"
    assert box.remote_branches() == []
    assert box.git("branch", "--show-current") == "main"


def test_renamed_file_given_a_secret_is_still_scanned(abox: Box) -> None:
    """git reports it as a rename, which an added/modified filter would skip."""
    abox.dev_push("automations.yaml", "[{alias: a long enough line for rename detection}]\n")
    abox.run("sync")
    (abox.config / "automations.yaml").rename(abox.config / "renamed.yaml")
    with (abox.config / "renamed.yaml").open("a") as fh:
        fh.write("password: abcdefgh1234\n")
    abox.run()
    assert abox.status()["held"] == ["renamed.yaml"]
    assert "renamed.yaml" not in abox.remote_files(abox.open_pr()[0])


def test_scan_that_cannot_run_pushes_nothing(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    box.write("new.yaml", "x: 1\n")

    def broken(*_args: object) -> list[tuple[str, str]]:
        raise OSError("cat-file died")

    monkeypatch.setattr("ha_gitops.engine.scan.scan_index", broken)
    assert box.run() == 1
    assert box.line() == "error:secret_scan:the secret scan could not run"
    assert box.remote_branches() == []
    assert box.run("discard") == 1
    assert box.status()["detail"] == "secret_scan"
    assert box.git("diff", "--cached", "--name-only") == ""


def test_legacy_token_is_scrubbed_from_the_remote_url(box: Box) -> None:
    box.git(
        "remote", "set-url", "origin", "https://x-access-token:test-token@github.com/local/test.git"
    )
    box.run()
    assert "test-token" not in (box.config / ".git/config").read_text()
    box.git("remote", "remove", "origin")
    assert box.run() == 0  # a missing remote is put back


# ---- the rolling pull request --------------------------------------------------


def test_new_drift_rolls_onto_the_open_backup_branch(box: Box) -> None:
    box.write("first.yaml", "1\n")
    box.run()
    branch = box.open_pr()[0]
    old_tip = box.ogit("rev-parse", branch)
    box.write("rolling.yaml", "rolling drift\n")
    assert box.run() == 0
    assert box.remote_branches() == [branch]
    new_tip = box.ogit("rev-parse", branch)
    assert new_tip != old_tip
    assert box.ogit("rev-parse", f"{new_tip}^") == old_tip  # a fast-forward
    assert "rolling.yaml" in box.remote_files(branch)
    assert box.git("branch", "--show-current") == "main"
    # No new drift: nothing pushed, and the status says the PR already has it
    assert box.run() == 0
    assert box.ogit("rev-parse", branch) == new_tip
    assert box.line().startswith("success:pr_unchanged")


def test_signoff_option_is_respected(box: Box) -> None:
    box.write("signed.yaml", "1\n")
    box.run()
    branch = box.open_pr()[0]
    assert "Signed-off-by: Test <test@localhost>" in box.ogit("log", "-1", "--format=%B", branch)
    box.options["signoff"] = False
    box.write("nosign.yaml", "1\n")
    box.run()
    assert "Signed-off-by" not in box.ogit("log", "-1", "--format=%B", branch)


def test_pr_is_opened_then_updated_through_the_api(abox: Box, api: FakeApi) -> None:
    api.pulls = [
        # Someone else's PR, a revert PR and a fork's PR are never taken over
        {"head": {"ref": "feature/x", "repo": {"full_name": "local/test"}}, "number": 7},
        {
            "head": {"ref": "auto-backup/revert-abc1234", "repo": {"full_name": "local/test"}},
            "number": 8,
        },
        {"head": {"ref": "auto-backup/fork", "repo": {"full_name": "evil/test"}}, "number": 9},
    ]
    abox.write("d1.yaml", "d1\n")
    assert abox.run() == 0
    status = abox.status()
    assert (status["detail"], status["pr_number"]) == ("pr_created", "1")
    assert status["extra"] == "https://example/pr/1"
    created = api.bodies("POST", "/pulls")[0]
    assert created["base"] == "main"
    assert "`d1.yaml` (added)" in created["body"]
    abox.write("d2.yaml", "d2\n")
    abox.write("mqtt.yaml", SECRET)
    assert abox.run() == 0
    assert abox.status()["detail"] == "pr_updated"
    updated = api.bodies("PATCH", "/pulls/1")[0]
    assert updated["title"] == "chore: config backup (2 files)"
    assert "### Held back" in updated["body"]
    assert "`mqtt.yaml`" in updated["body"]
    assert api.count("POST", "ha_gitops_pr_updated") == 1
    assert api.count("POST", "ha_gitops_secrets_held") == 1
    # Every call carried its token in a header, never in the URL
    assert all("token" not in call["path"] for call in api.calls)
    assert {c["auth"] for c in api.calls} == {"Bearer test-token", "Bearer sup-token"}


def test_base_moving_under_an_open_pr_gives_a_merge_commit(abox: Box, api: FakeApi) -> None:
    """So the PR still shows only the drift, not the changes merged meanwhile."""
    api.pulls = []
    abox.write("d1.yaml", "d1\n")
    abox.run()
    branch = abox.open_pr()[0]
    abox.dev_push("upstream.yaml", "from: github\n")
    abox.write("d2.yaml", "d2\n")
    abox.run()
    assert len(abox.ogit("rev-list", "--parents", "-n", "1", branch).split()) == 3
    assert abox.ogit("diff", "--name-only", f"main...{branch}").split() == ["d1.yaml", "d2.yaml"]
    assert "Signed-off-by: Test <test@localhost>" in abox.ogit("log", "-1", "--format=%B", branch)


def test_secret_already_in_the_open_pr_is_taken_out(abox: Box, api: FakeApi) -> None:
    api.pulls = []
    abox.write("d1.yaml", "d1\n")
    abox.run()
    branch = abox.open_pr()[0]
    # An older version pushed a secret-like file to the PR branch unscanned
    abox.dgit("fetch", "-q", "origin", branch)
    abox.dgit("checkout", "-q", "-b", "pr", "FETCH_HEAD")
    (abox.dev / "mqtt.yaml").write_text(SECRET)
    abox.dgit("add", "-A")
    abox.dgit("commit", "-q", "-m", "old backup with a secret")
    abox.dgit("push", "-q", "origin", f"pr:{branch}")
    abox.dgit("checkout", "-q", "main")
    abox.write("mqtt.yaml", SECRET)
    abox.run()
    assert "mqtt.yaml" not in abox.remote_files(branch)
    assert (abox.config / "mqtt.yaml").exists()
    assert abox.status()["held"] == ["mqtt.yaml"]


# ---- sync and apply ------------------------------------------------------------


def test_sync_pulls_and_leaves_local_drift_alone(box: Box) -> None:
    box.write("first.yaml", "1\n")
    box.run()
    branch = box.open_pr()[0]
    tip = box.ogit("rev-parse", branch)
    box.dev_push("upstream.yaml", "upstream only\n")
    box.write("syncdrift.yaml", "sync drift\n")
    assert box.run("sync") == 0
    assert box.read("upstream.yaml") == "upstream only\n"
    assert box.ogit("rev-parse", branch) == tip
    assert box.read("syncdrift.yaml") == "sync drift\n"
    assert box.line().startswith("success:synced")
    assert box.run("sync") == 0
    assert box.line().startswith("success:up_to_date")


def test_pull_that_cannot_rebase_reports_and_keeps_the_drift(abox: Box, api: FakeApi) -> None:
    # A local commit that conflicts with what is on GitHub
    abox.write("automations.yaml", "local commit\n")
    abox.git("commit", "-qam", "local")
    abox.dev_push("automations.yaml", "upstream\n")
    abox.write("drift.yaml", "kept\n")
    assert abox.run("sync") == 1
    assert abox.status()["detail"] == "pull_rebase_failed"
    assert abox.read("drift.yaml") == "kept\n"
    assert abox.read("automations.yaml") == "local commit\n"
    assert api.count("POST", "ha_gitops_error") == 1


def test_changes_pulled_by_a_dry_run_are_applied_by_the_next_run_once(
    abox: Box, api: FakeApi
) -> None:
    abox.dev_push("automations.yaml", "[{alias: merged}]\n")
    abox.run("sync", DRY_RUN_OVERRIDE="true")
    assert "merged" in abox.read("automations.yaml")
    assert api.count("POST", "services/automation/reload") == 0
    abox.run("sync")
    assert api.count("POST", "services/automation/reload") == 1
    assert abox.status()["applied"] == "automation.reload"
    assert api.count("POST", "ha_gitops_pulled") == 0  # nothing new came down this time
    assert api.count("POST", "ha_gitops_applied") == 1
    abox.run("sync")
    assert api.count("POST", "services/automation/reload") == 1


def test_invalid_config_is_not_applied_and_gets_a_revert_pr(tmp_path: Path, api: FakeApi) -> None:
    box = Box(tmp_path, api, on_invalid_config="revert_pr")
    box.run()
    box.dev_push("configuration.yaml", "homeassistant: {name: Broken}\n")
    api.check = "invalid"
    box.run("sync")
    assert box.status()["applied"] == "config_invalid"
    post = box.git("rev-parse", "main")
    branch = f"auto-backup/revert-{post[:7]}"
    assert "name: Home" in box.ogit("show", f"{branch}:configuration.yaml")
    assert api.count("POST", "/pulls") == 1
    assert "config check failed" in api.bodies("POST", "/pulls")[0]["title"]
    notes = api.count("POST", "persistent_notification")
    assert notes == 2  # the failed check, then the revert PR
    box.run("sync")
    assert api.count("POST", "persistent_notification") == notes  # reported once
    assert api.count("POST", "/pulls") == 1
    api.check = "valid"
    box.run("sync")
    assert "restart_required" in box.status()["applied"]
    assert not (box.data / "invalid_head").exists()
    assert api.count("POST", "ha_gitops_restart_required") == 1


def test_invalid_config_without_revert_pr_only_notifies(abox: Box, api: FakeApi) -> None:
    abox.dev_push("scripts.yaml", "broken\n")
    api.check = "invalid"
    abox.run("sync")
    assert abox.status()["applied"] == "config_invalid"
    assert abox.remote_branches() == []
    assert api.count("POST", "/pulls") == 0


@pytest.mark.parametrize(
    ("options", "pushed", "applied", "service"),
    [
        ({"apply_after_pull": "off"}, "scripts.yaml", "off", None),
        (
            {"apply_after_pull": "reload"},
            "scripts.yaml",
            "homeassistant.reload_all",
            "services/homeassistant/reload_all",
        ),
        (
            {"apply_after_pull": "restart"},
            "README.md",
            "homeassistant.restart",
            "services/homeassistant/restart",
        ),
        (
            {"auto_restart": True},
            "configuration.yaml",
            "homeassistant.restart",
            "services/homeassistant/restart",
        ),
        ({}, "README.md", "none", None),
        ({}, "themes/dark.yaml", "frontend.reload_themes", "services/frontend/reload_themes"),
    ],
)
def test_apply_modes(
    tmp_path: Path,
    api: FakeApi,
    monkeypatch: pytest.MonkeyPatch,
    options: dict[str, object],
    pushed: str,
    applied: str,
    service: str | None,
) -> None:
    monkeypatch.setattr("ha_gitops.adapters.hass_ws.refresh_dashboards_sync", lambda *_a: 2)
    box = Box(tmp_path, api, **options)
    box.run()
    (box.dev / "themes").mkdir()
    box.dev_push(pushed, "changed: true\n")
    assert box.run("sync") == 0
    assert box.status()["applied"].startswith(applied)
    if service:
        assert api.count("POST", service) == 1


def test_dashboard_change_refreshes_open_tabs_without_a_config_check(
    abox: Box, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "ha_gitops.adapters.hass_ws.refresh_dashboards_sync",
        lambda url, token: seen.append((url, token)) or 3,
    )
    abox.dev_push("ui-lovelace.yaml", "views: []\n")
    abox.run("sync")
    assert abox.status()["applied"] == "dashboards.refresh"
    assert seen == [(f"ws://{api.url.removeprefix('http://')}/sup/core/websocket", "sup-token")]
    assert api.count("POST", "check_config") == 0

    def broken(*_args: str) -> int:
        raise OSError("no websocket")

    monkeypatch.setattr("ha_gitops.adapters.hass_ws.refresh_dashboards_sync", broken)
    abox.dev_push("ui-lovelace.yaml", "views: [{}]\n")
    abox.run("sync")
    assert abox.status()["applied"] == "none"
    assert "Could not refresh open dashboards" in abox.log()


def test_apply_survives_home_assistant_being_unreachable(abox: Box, api: FakeApi) -> None:
    api.fail = {"check_config", "services/automation/reload"}
    abox.dev_push("automations.yaml", "[{alias: x}]\n")
    assert abox.run("sync") == 0
    assert "Could not run the HA config check" in abox.log()
    assert "Failed to call automation.reload" in abox.log()
    assert abox.status()["applied"] == "none"


def test_without_a_supervisor_token_nothing_is_applied(box: Box) -> None:
    box.dev_push("automations.yaml", "[{alias: x}]\n")
    assert box.run("sync") == 0
    assert box.status()["applied"] == "skipped"


def test_rewritten_history_restarts_the_apply_range(abox: Box) -> None:
    (abox.data / "applied_head").write_text("0" * 40 + "\n")
    abox.dev_push("automations.yaml", "[{alias: x}]\n")
    assert abox.run("sync") == 0
    assert abox.status()["applied"] == "automation.reload"


# ---- revert ----------------------------------------------------------------


def test_revert_opens_a_branch_from_a_worktree(box: Box) -> None:
    sha = box.dev_push("upstream.yaml", "upstream only\n")
    box.run("sync")
    assert box.run("revert", REVERT_SHA=sha) == 0
    branch = f"auto-backup/revert-{sha[:7]}"
    assert "upstream.yaml" not in box.remote_files(branch)
    assert box.read("upstream.yaml") == "upstream only\n"  # /config is untouched
    assert len(box.git("worktree", "list").splitlines()) == 1
    assert "Signed-off-by" in box.ogit("log", "-1", "--format=%B", branch)
    assert box.line() == f"warning:branch_pushed:{branch}"


def test_revert_pr_through_the_api_and_its_refusals(abox: Box, api: FakeApi) -> None:
    sha = abox.dev_push("upstream.yaml", "upstream only\n")
    abox.run("sync")
    assert abox.run("revert", REVERT_SHA=sha, DRY_RUN_OVERRIDE="true") == 0
    assert abox.status()["detail"] == "dry_run"
    assert abox.remote_branches() == []
    assert abox.run("revert", REVERT_SHA=sha) == 0
    assert abox.status()["detail"] == "revert_pr"
    assert api.bodies("POST", "/pulls")[0]["title"] == 'Revert "change upstream.yaml"'
    assert api.count("POST", "ha_gitops_pr_opened") == 1
    assert abox.run("revert", REVERT_SHA="not-a-sha") == 1
    assert abox.line() == "error:revert:invalid commit id"
    assert abox.run("revert", REVERT_SHA="abcdef1") == 1
    assert "unknown commit" in abox.line()
    # A commit that is not on the base branch
    abox.git("commit", "-q", "--allow-empty", "-m", "local only")
    local = abox.git("rev-parse", "HEAD")
    abox.git("reset", "-q", "--hard", "HEAD~1")
    assert abox.run("revert", REVERT_SHA=local) == 1
    assert "is not on main" in abox.line()


def test_revert_of_a_merge_and_of_a_commit_that_no_longer_reverts(box: Box) -> None:
    box.write("drift.yaml", "1\n")
    box.run()
    box.merge_backup()
    box.run("sync")
    merge = box.git("rev-parse", "HEAD")
    assert box.run("revert", REVERT_SHA=merge) == 0
    assert "drift.yaml" not in box.remote_files(f"auto-backup/revert-{merge[:7]}")
    first = box.dev_push("automations.yaml", "one\n")
    box.dev_push("automations.yaml", "two\n")
    box.run("sync")
    assert box.run("revert", REVERT_SHA=first) == 1
    assert "does not revert cleanly" in box.line()
    assert len(box.git("worktree", "list").splitlines()) == 1


# ---- discard -----------------------------------------------------------------


def test_discard_puts_the_config_back_and_keeps_a_safety_copy(abox: Box, api: FakeApi) -> None:
    api.pulls = []
    abox.dev_push("upstream.yaml", "from: github\n")
    abox.run("sync")
    abox.write("d1.yaml", "d1\n")
    abox.run()
    branch = abox.open_pr()[0]
    abox.write("mqtt.yaml", SECRET)
    abox.write("automations.yaml", "[{alias: local}]\n")
    (abox.config / "upstream.yaml").unlink()

    assert abox.run("discard", DRY_RUN_OVERRIDE="true") == 0
    assert abox.status()["detail"] == "dry_run"
    assert (abox.config / "d1.yaml").exists()

    reloads = api.count("POST", "services/automation/reload")
    assert abox.run("discard") == 0
    assert abox.status()["detail"] == "discarded"
    assert not (abox.config / "d1.yaml").exists()
    assert abox.read("automations.yaml") == "[]\n"
    assert abox.read("upstream.yaml") == "from: github\n"
    assert (abox.config / "mqtt.yaml").exists()  # held files are left alone
    assert (abox.config / "secrets.yaml").exists()  # so are ignored ones
    assert abox.git("status", "--porcelain") == "?? mqtt.yaml"
    safety = abox.git("for-each-ref", "--format=%(refname)", "refs/gitops/discarded").split()[-1]
    assert abox.git("show", f"{safety}:d1.yaml") == "d1"
    assert branch not in abox.remote_branches()
    assert api.bodies("PATCH", "/pulls/1") == [{"state": "closed"}]
    assert not (abox.data / "open_pr").exists()
    assert api.count("POST", "services/automation/reload") == reloads + 1
    assert api.count("POST", "ha_gitops_discarded") == 1
    assert abox.run() == 0
    assert abox.status()["detail"] == "secrets_held"
    assert abox.run("discard") == 0
    assert abox.line() == "success:no_changes:nothing to discard"


def test_discard_keeps_only_the_latest_safety_copies(box: Box) -> None:
    for n in range(7):
        box.write("scratch.yaml", f"{n}\n")
        assert box.run("discard") == 0
    refs = box.git("for-each-ref", "--format=%(refname)", "refs/gitops/discarded").split()
    assert len(refs) == 5


# ---- the lock, the log, and failures -------------------------------------------


def test_only_one_run_at_a_time(box: Box) -> None:
    box.env["LOCK_WAIT"] = "1"
    with (box.data / "gitops.lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert box.run() == 75
        assert "holds the lock after 1s" in box.log()
    box.write("afterlock.yaml", "1\n")
    assert box.run() == 0
    assert box.line().startswith("warning:branch_pushed")


def test_log_is_rotated(box: Box) -> None:
    (box.data / "gitops_backup.log").write_text("old line\n" * 900)
    box.run()
    lines = box.log().splitlines()
    assert len(lines) == 500
    assert lines[-1].endswith("No changes to back up")


def test_git_failure_is_reported_instead_of_a_stale_status(box: Box) -> None:
    box.write("new.yaml", "1\n")
    box.ogit("config", "receive.denyCurrentBranch", "refuse")
    hook = box.origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    assert box.run() == 1
    status = box.status()
    assert (status["state"], status["detail"]) == ("error", "git")
    assert "git push failed" in status["extra"]
    assert box.git("branch", "--show-current") == "main"  # HEAD is put back all the same
    assert box.read("new.yaml") == "1\n"
    hook.unlink()
    assert box.run() == 0


def test_status_json_keeps_the_contract_the_panel_reads(box: Box) -> None:
    box.run()
    assert set(box.status()) == {
        "state",
        "detail",
        "extra",
        "time",
        "mode",
        "trigger",
        "local_head",
        "pr_url",
        "pr_number",
        "pr_branch",
        "changed",
        "applied",
        "held",
    }
    assert json.loads((box.data / "status.json").read_text())["mode"] == "full"


def test_file_deleted_on_the_box_and_edited_upstream_stays_deleted(box: Box) -> None:
    """A modify/delete conflict: the live state (no file) wins, and it is reported."""
    box.dev_push("automations.yaml", "[{alias: edited upstream}]\n")
    (box.config / "automations.yaml").unlink()
    box.write("other.yaml", "kept\n")
    box.run("sync")
    assert box.line().startswith("warning:stash_conflict")
    assert not (box.config / "automations.yaml").exists()
    assert box.read("other.yaml") == "kept\n"
    box.settled()


def test_two_backups_in_the_same_second_get_two_branch_names(box: Box) -> None:
    from datetime import timedelta

    box.write("one.yaml", "1\n")
    box.run()
    first = box.open_pr()[0]
    box.merge_backup()
    box.run("sync")
    box.clock -= timedelta(minutes=2)  # the clock did not move on
    box.write("two.yaml", "2\n")
    box.run()
    assert box.open_pr()[0] == f"{first}-2"


def test_revert_pr_for_an_invalid_config_is_best_effort(tmp_path: Path, api: FakeApi) -> None:
    box = Box(tmp_path, api, on_invalid_config="revert_pr")
    box.run()
    api.check = "invalid"
    api.create_ok = False
    box.dev_push("configuration.yaml", "homeassistant: {name: Broken}\n")
    assert box.run("sync") == 0
    assert "pushed, but the PR could not be opened" in box.log()
    # Asked again for the same commit: the branch is already there
    (box.data / "invalid_head").unlink()
    box.run("sync")
    assert "already exists; not opening another PR" in box.log()
    assert len(box.remote_branches()) == 1
    # A dry run prepares nothing
    (box.data / "invalid_head").unlink()
    box.dev_push("configuration.yaml", "homeassistant: {name: Worse}\n")
    box.options["dry_run"] = False
    box.run("sync", DRY_RUN_OVERRIDE="true")
    assert len(box.remote_branches()) == 1


def test_symlink_replaced_by_a_file_with_a_secret_is_still_scanned(box: Box) -> None:
    """git reports it as a type change, neither an addition nor a modification."""
    (box.config / "link.yaml").symlink_to("configuration.yaml")
    box.run()
    box.merge_backup()
    box.run("sync")
    (box.config / "link.yaml").unlink()
    box.write("link.yaml", SECRET)
    box.write("other.yaml", "1\n")
    assert box.run() == 0
    assert box.status()["held"] == ["link.yaml"]
    tree = box.ogit("ls-tree", "-r", box.open_pr()[0])
    assert "120000" in next(line for line in tree.splitlines() if line.endswith("link.yaml"))
    assert "other.yaml" in box.remote_files(box.open_pr()[0])
