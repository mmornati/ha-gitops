# Changelog

## 0.4.1

- Fix: every run failed at once with `flock: unrecognized option: w` and
  "Another run still holds the lock", because the app image ships BusyBox
  `flock`, which has no `-w`. The engine now waits for the lock with
  `flock -n` in a loop, which works with both BusyBox and util-linux. The
  self-check runs with a flock that rejects `-w`, and the image smoke test
  checks `flock -n`.

## 0.4.0

Real-time sync and control from Home Assistant.

- **Rolling backup PR**: new drift is pushed (fast-forward) onto the open
  backup PR instead of opening a new PR every run; the PR title and body list
  the changed files by kind.
- **Real-time sync**: signed GitHub webhook on port 8098 (`webhook_secret`),
  `git ls-remote` poll (`poll_seconds`, no REST quota used), inotify file
  watcher with `debounce_seconds`, and `sync_mode` to pick the triggers.
- **Safe apply**: the HA config check runs before any reload or restart;
  new default `apply_after_pull: auto` runs only the reloads the changed
  paths need and notifies when a restart is required (`auto_restart` to do
  it automatically).
- **Control**: MQTT discovery device (status, pending changes, open PR and
  checks, buttons, pause/auto-apply switches, update entity), REST sensor
  fallback, `hassio.addon_stdin` commands, `ha_gitops_*` events.
- **Ingress panel**: status, open PR, pending diff, history with revert PRs,
  log.
- Security: the GitHub token is no longer written into
  `/config/.git/config` (and so into HA backups); existing remotes are
  cleaned on the first run.
- Fix: `signoff: false` was ignored and commits were always signed off.
- One engine run at a time (lock), so triggers never overlap.
- The run log and status JSON moved to the app's `/data`; the one-line
  `/config/.gitops_backup_status` is still written.
- Docs: "add-on" is now "app", following Home Assistant (upstream PR #7).

## 0.3.3

- Never commit `zigbee2mqtt/secret.yaml`. Zigbee2MQTT keeps its MQTT login
  and network key there when its `configuration.yaml` uses `!secret` refs,
  and the name (singular `secret.yaml`) is not matched by the existing
  `secrets.yaml` pattern, so a backup run would have swept it into a PR.
  It is now in the seeded `.gitignore`, the local exclude and the untracker.

## 0.3.2

- Fix: after a `stash_conflict` the add-on kept the live files but left the
  conflict in the git index, so every later run failed with `needs merge` /
  `could not write index` and no backup or upstream sync happened again
  until someone ran `git reset` by hand. The index is now cleared right after
  the conflict, and a repo already stuck that way heals on the next run.
  The working tree (the live config) is never touched.

## 0.3.1

- Fix: after pushing a backup branch the add-on no longer rolls the live
  config back. Returning to the base branch used `git checkout`, which
  rewrote `/config` to the pre-backup base branch: every backed-up change
  (UI edits, HACS updates) vanished from disk until the next run pulled the
  merged PR, edits made in that window hit stale files (the source of
  `stash_conflict` / `pull_rebase_failed`), and if the PR was never merged
  the drift was lost for good. HEAD now moves back without touching the
  working tree, so the drift stays uncommitted and is reconciled on the next
  run.

## 0.3.0

- Feature: `apply_after_pull` — after a merged PR is pulled into `/config`, the
  app now applies it to the running Home Assistant instead of leaving files
  on disk unloaded. Default `reload` calls `homeassistant.reload_all` (no
  restart); `restart` restarts HA (for `configuration.yaml` integration or
  `custom_components/` changes); `off` keeps the previous behaviour. Only fires
  when the sync actually advanced `HEAD`, never in `dry_run`. Requires the
  app's Home Assistant API access (now enabled via `homeassistant_api`).

## 0.2.1

- Security: the never-commit guarantee (secrets, credentials, `.storage/`,
  app runtime files) now holds for **migrated** repos too, not just fresh
  ones. Patterns are enforced via `.git/info/exclude` on every run, and any
  secret a prior run already committed is untracked (the removal flows out
  through the next backup PR). Previously a repo that arrived with its own
  `.gitignore` lacking these entries could leak an OAuth token or key.

## 0.2.0

- Docs: make the two-pillar backup model explicit — config repo (this app)
  + encrypted HA full backup (e.g. Google Drive Backup) = 100% restorable;
  neither alone is enough. Sharpened the "what is backed up where" table
  (names exactly what `.storage/` holds and what lives outside `/config`).
- Security: seed `.gitignore` now also excludes `*.token` / `.google.token`
  so OAuth tokens (e.g. Google Calendar) never land in the repo.
- First bootstrap now logs a disaster-recovery warning: this app versions
  declarative config only; run HA full backups for a complete restore.

## 0.1.1

- Fix: keep the app's own runtime files (`.gitops_backup_status`,
  `gitops_backup.log`) out of backups when migrating a repo that already has a
  `.gitignore` — they were previously swept into backup PRs and risked a
  status-file PR feedback loop. Now excluded via `.git/info/exclude` on every
  run, and untracked if a prior run committed them.

## 0.1.0

- Initial release: PR-gated backup (stash → rebase → drift → branch → PR),
  dry-run mode, DCO sign-off, seeded HA `.gitignore`, status file for a
  `command_line` sensor, template CI workflow for the config repo.
