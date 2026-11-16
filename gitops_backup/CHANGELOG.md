# Changelog

## 0.5.2

- Panel: the colours now follow Home Assistant. White or black canvas, cards,
  text and the warning, info, error and success colours come from the theme in
  use, light, dark or custom, and change with it; the grey-green look is gone.
  It follows the theme chosen in your Home Assistant profile, not the one of
  the operating system. Layout and behaviour are unchanged.

## 0.5.1

- Fix: after updating to 0.5.0 the browser could keep showing the old panel
  with "Could not reach the app". The panel page was served without cache
  instructions, so a browser kept its old copy for days and ran it against
  the new API. The page is now revalidated on every load and API answers are
  never stored. **After this update, reload the panel once without the cache**
  (Ctrl+Shift+R, or Cmd+Shift+R on a Mac; in the Home Assistant mobile app:
  Settings → Companion app → Debugging → Reset frontend cache). Later
  updates need nothing.

## 0.5.0

Flow fixes

- Fix: changes pulled by a dry run were never applied. A dry run pulls
  without applying, and the next run saw nothing new. The app now remembers
  the last commit it applied and applies everything since. The same holds
  for changes pulled while the config check was failing.
- Fix: a run that hangs (a stalled push, for example) no longer blocks the
  queue for good. It is stopped after 30 minutes (`error:timeout`), a git
  transfer under 1 kB/s for a minute fails by itself, and a run left
  half-way puts `HEAD` back on the base branch at the next start.
- Fix: after failed runs the poller backs off (1 minute, 2, 4… up to an
  hour) instead of retrying the same pull every interval.
- Fix: an edit made while a run was in progress was dropped by the file
  watcher. It is now picked up by a follow-up backup.
- Fix: a failed config check is reported once per pulled commit, not on
  every run.
- Change: `esphome/` and `zigbee2mqtt/` files no longer raise "restart
  required"; Home Assistant Core does not read them.
- Change: the backup PR no longer says "close it to drop it", which was not
  true: closing the PR leaves the changes on the box. Use Discard (below).

From upstream askb/ha-gitops

- A merged dashboard change reaches browsers that already have the dashboard
  open (0.5.3).
- When the base branch moves on while a backup PR is open, the next backup
  is a merge commit, so the PR still shows only the drift (0.4.0).
- Default `commit_email` is `gitops-backup@users.noreply.github.com`; the
  `@localhost` default fails the DCO GitHub App. Existing installs keep
  their saved value (0.5.0).
- Image: Home Assistant base `3.24` and `apk upgrade` at build time (0.5.2).
- CI: harden-runner, Trivy image scan, zizmor, CodeQL, Scorecard, Dependabot
  (0.5.2). Template: required HA gate bumped to 2026.11.

Security

- MQTT: `mqtt_commands: false` keeps the sensors and removes the buttons,
  switches and update entity. Retained command messages are ignored.
  `discard`, `merge` and `restart` are never accepted over MQTT.
- The GitHub and Supervisor tokens are no longer passed to `curl` on the
  command line.
- Webhook: bad signatures from one address stop being logged after 10 a
  minute. Signed deliveries are never limited.
- A secret-like file that an earlier version already pushed into the open
  backup PR is taken out of it by the next backup: the scan now covers
  everything that differs from the base branch. It stays in that branch's
  history, so rotate the secret.
- Values that come from outside (a command, its argument, a caller's
  address) are stripped of line breaks before they are logged, and only
  `revert` keeps its argument.
- A file renamed and edited in the same change is scanned too (git reported
  it as a rename, which the scan skipped).

New

- **Discard local changes** (panel, or the `discard` command): `/config`
  goes back to the base branch and the open backup PR is closed. Ignored and
  held-back files stay; a safety copy is kept on the box.
- **Merge from the panel** when the PR's checks pass, then pull it straight
  back. `merge_method`: `merge`, `squash` or `rebase`.
- **Restart Home Assistant** from the panel when merged changes need it,
  only after a passing config check.
- `on_invalid_config: revert_pr` opens a PR that reverts pulled changes
  that fail the config check.
- Entities: "Held back files" and "Restart required". Events:
  `ha_gitops_secrets_held`, `ha_gitops_discarded`.

Panel

- Redesigned. A head rule shows the box's commit and GitHub's and whether
  they agree; one sentence says what is waiting; every changed file is a
  strip in a bay: not backed up, in review, held back, landed.
- Files open in place into their diff, new files included. The content of a
  held-back file is never sent to the panel.
- Failures, an invalid configuration, a required restart, held files and a
  paused sync are called out with the action that resolves them.
- No more redrawing every five seconds (scroll and selection are kept), no
  sideways scrolling on a phone, keyboard and screen-reader support.

## 0.4.4

Ported from upstream askb/ha-gitops (0.4.0 to 0.5.2).

- Security: backup and editor copies of `secrets.yaml` (`secrets.yaml.*`,
  `*.bak`, `*.bak-*`, `*.bak.*`, `*.orig`, `*.secret`), `*-key.bin` and
  `esphome/.device-builder*` (ESPHome Device Builder state, which holds a
  key) are now on the never-commit list. With the file watcher on, a
  `secrets.yaml.bak` left by a manual edit reached GitHub about two minutes
  later. Templates named `secrets.yaml.{example,sample,template,dist}` are
  still backed up. Existing installs get the patterns through
  `.git/info/exclude` on the next run, and files already tracked are
  untracked. A file that is already in a pushed branch or a merged PR stays
  in the GitHub history: rotate that secret.
- Security: staged files are scanned for secret-like **content** (private
  key blocks, GitHub/AWS/Slack/Google tokens, JWTs, inline
  `password: literal`) and held back from the backup commit, including the
  initial import. The scan reads what is staged (exactly what would be
  committed), covers files of any size, and still looks for key blocks and
  tokens in binary files. Held files stay on the box, are listed in the PR
  body and in a Home Assistant notification, and fire
  `ha_gitops_secrets_held`. A run with nothing else to back up ends
  `warning:secrets_held`. A file whose staged content cannot be read is held
  back too, and if the scan cannot run at all nothing is pushed
  (`error:secret_scan`).
- The initial import now also applies the never-commit list when `/config`
  already has its own `.gitignore`.

## 0.4.3

- Fix: a backup touching a few thousand files (for example a removed or
  updated integration such as HACS) pushed its branch but never opened a PR,
  with `jq: Argument list too long` and `warning:branch_pushed` on every run.
  The PR body was passed to `jq` and `curl` as a command-line argument, and
  Linux caps a single argument at 128 KiB. Payloads now go through stdin.
- The PR description lists at most about 60,000 characters of files (GitHub
  rejects bodies over 65,536) and ends with "…and N more files" when the list
  is cut. The PR diff still has every file.

## 0.4.2

- Fix: a merged backup PR that added, renamed or deleted a file raised a false
  `warning:stash_conflict` on the next sync. The live tree still held those
  files as drift, so `git stash pop` failed on files the pull had just brought
  back ("already exists, no checkout"). When the stashed files match upstream
  the run now drops the stash and reports success.
- Fix: "kept live config" was not true after a failed pop. The stash was
  dropped without restoring its untracked files, so a new file edited between
  the backup and the merge went back to the PR version. The live version now
  wins, and the warning is kept for files that really differ. If a file cannot
  be restored, the stash is kept and the status says so.

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
