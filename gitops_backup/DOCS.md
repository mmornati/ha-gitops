# GitOps Config Backup

PR-gated GitHub backup for your Home Assistant config.

## What you need

- A **GitHub account** (free is fine)
- A **repository** for your config — private recommended
- A **fine-grained personal access token (PAT)** scoped to that one repo

## Step-by-step setup

### 1. Create the repository

github.com → **+** → **New repository** → name it (e.g. `my-ha-config`) →
**Private** → Create. Don't add a README — the app bootstraps the content.

### 2. Create the token

1. github.com → your avatar → **Settings** → **Developer settings** →
   **Personal access tokens** → **Fine-grained tokens** → **Generate new token**
2. **Token name**: `ha-gitops` · **Expiration**: 1 year (set a calendar
   reminder — the app status will show `error` when it expires)
3. **Repository access**: *Only select repositories* → pick the repo from step 1
4. **Permissions → Repository permissions**:
   - **Contents**: Read and write
   - **Pull requests**: Read and write
   - **Commit statuses**: Read-only (optional: shows the PR's CI result in HA)
   - everything else: No access
5. Generate, copy the `github_pat_…` value — you won't see it again

> The token can only touch that single repo. Even if it leaked, your other
> repos and account are untouchable — that's why fine-grained beats classic PATs.

### 3. Configure the app

Settings → Apps → GitOps Config Backup → **Configuration**:

```yaml
github_repo: yourname/my-ha-config
github_token: github_pat_XXXXXXXX
dry_run: true        # first run: log only, push nothing
```

Start the app, then check its **Log** tab (or the **GitOps** panel) to see
what *would* be committed. Happy? Set `dry_run: false` and restart. The first
real run pushes the initial import; every run after that only opens PRs.

The token is stored in the app options by the Supervisor. It never goes
into git and is never written to `/config/.git/config` (git gets it through
an askpass helper, so it does not end up in HA backups either), and the seeded `.gitignore` keeps `secrets.yaml` and other sensitive
files out of the repo entirely.

### 4. Add the CI gate (recommended)

Copy `template/workflows/validate-ha-config.yaml` from this repository into
`.github/workflows/` of **your config repo**, and add a stub line for every
`!secret` key you use (CI never sees real secrets). Then protect `main`:
repo → Settings → Branches → require the *HA Config Validation* and
*HA Smoke Boot* checks.

## What is backed up where (best practice)

This app is **change tracking for your config**, not disaster recovery.
Run both layers:

| Data | This app (GitHub) | HA Backup + Google Drive |
|------|----------------------|--------------------------|
| Config YAML (automations, dashboards, …) | ✅ versioned, reviewable diffs | ✅ inside the archive |
| `secrets.yaml` | ❌ **never** (gitignored) | ✅ inside the **encrypted** archive |
| `zigbee2mqtt/secret.yaml` (Z2M MQTT login, network key) | ❌ **never** | ✅ inside the **encrypted** archive |
| `.storage/` — UI-added **integrations**, **logins/tokens**, **device/entity/area registries**, **UI (Lovelace) dashboards**, helpers | ❌ never (holds secrets) | ✅ **most restore-critical item** |
| Credentials: `*.token`, `.google.token`, `*.key`, `*.pem`, `*-key.bin`, `.cloud/`, `esphome/.device-builder*` | ❌ never | ✅ |
| Copies of secrets: `secrets.yaml.*` (except `.example`, `.sample`, `.template`, `.dist`), `*.bak`, `*.orig`, `*.secret` | ❌ never | ✅ |
| Any other file whose content looks like a secret (key block, API token, inline `password: literal`) | ❌ held back, listed in the PR and in a notification | ✅ |
| Databases, history, logs | ❌ never | ✅ (recorder DB, if selected) |
| App configs/data, TLS certs (`/ssl`) — live **outside** `/config` | ❌ app can't see them | ✅ |
| Media, camera recordings | ❌ | usually excluded — external drive |

> **The 100% guarantee: config repo + encrypted full backup. Neither alone is
> enough.** Restore from the git repo *only* and you'd get your YAML back but
> have to re-do by hand every UI-added integration, all logins/tokens, device
> and entity names, UI dashboards, `secrets.yaml`, and every app — because
> those live in `.storage/`, `secrets.yaml`, or outside `/config`. That's why a
> full backup (Pillar 2) is mandatory, not optional.

**Recommended stack:**

```text
GitHub (this app)      → config changes: who/what/when, PR review, CI gate
HA auto backup (daily)    → full system state, encrypted, on the box
 └─ Google Drive Backup   → off-site copies of those archives
Password manager          → the three keys to the kingdom (below)
```

### Where secrets belong

- `secrets.yaml` lives **only on the box** — referenced via `!secret` in your
  config, excluded from git by the seeded `.gitignore`, and recovered from the
  encrypted HA backup, never from GitHub.
- Store in your **password manager** (never in the repo, never in a README):
  1. the HA **backup encryption password** — without it your off-site backups
     are unrestorable,
  2. the **GitHub PAT** for this app,
  3. your HA admin credentials.
- CI validates PRs with **stub** secrets (step 4) — real values never leave
  the box.

**Disaster recovery order:** reinstall HAOS → restore the encrypted HA backup
(brings back `secrets.yaml`, `.storage`, apps) → your config repo is then
already live on the box and git history simply resumes.

## Real-time sync

Since 0.4.0 the app reacts to changes instead of waiting for the next
`interval_hours` run. The scheduled run stays as a safety net.

**Merged PRs come down within seconds (webhook) or a minute (poll).**

- **Webhook (instant).** Set `webhook_secret` to a long random string, map
  port `8098` in the app's **Network** section, and expose it through your
  reverse proxy or tunnel (for example `https://gitops.example.com/webhook` →
  `http://<ha-host>:8098/webhook`). In the config repo: **Settings →
  Webhooks → Add webhook**, payload URL = that URL, content type
  `application/json`, secret = `webhook_secret`, events: *Just the push
  event* (or also *Pull requests*). Every delivery's `X-Hub-Signature-256` is
  checked; anything unsigned gets a 401, and only pushes to `base_branch`
  trigger a sync.
- **No port to open?** Use a Home Assistant webhook instead: an automation
  with a `webhook` trigger whose action is
  `hassio.addon_stdin` with `addon: <this app's slug>` and `input: sync`.
  HA cannot check GitHub's signature, so the webhook id is the secret.
- **Poll (safety net).** Every `poll_seconds` (default 60) the app runs
  `git ls-remote` and syncs only when the base branch moved. This is a git
  protocol call, not a REST API call, so it does **not** use the 5,000/hour
  API quota. With the webhook set up, 300 is plenty; `0` turns it off.

**Local edits become a PR within minutes.** A file watcher (inotify) sees
edits in `/config` (UI-edited automations, scripts, HACS updates…). Once
nothing has changed for `debounce_seconds` (default 120), the app commits.
Paths git ignores (`.storage/`, databases, logs, Zigbee2MQTT state…) never
trigger anything.

**One rolling PR.** New drift is added as a commit to the open backup PR
(a normal fast-forward push, never a force-push) and the PR description is
refreshed with the changed files grouped by kind. Merge it when you are
happy; the next change opens a new PR. If `main` moves on while the PR is
open, the next backup is a merge commit, so the PR keeps showing only your
drift.

Closing the PR does not undo anything on the box: the changes are still
there, and the next backup opens a new PR with them. To drop them, use
**Discard…** in the panel (see Control from Home Assistant).

**When runs fail.** After a failed pull or backup the poller backs off (1
minute, then 2, 4… up to an hour) instead of retrying every interval. A run
that hangs is stopped after 30 minutes (`error:timeout`), and a stalled
git transfer fails after a minute under 1 kB/s. An edit made while a run is
in progress is picked up by a follow-up backup.

`sync_mode` chooses the triggers: `both` (default), `pull` (merged PRs
only), `push` (local edits only) or `schedule` (only `interval_hours`, the
pre-0.4.0 behaviour).

## Applying merged changes

When a sync pulls new commits, the app first runs the Home Assistant config
check. If it fails, nothing is applied: you get a persistent notification
with the errors and an `ha_gitops_error` event, once per pulled commit. The
files stay on disk, so **do not restart Home Assistant** until the config is
fixed. Every later run checks again and applies as soon as the check passes.
With `on_invalid_config: revert_pr` the app also opens a pull request that
reverts the pulled commits, ready for you to merge.

What is applied is everything since the last commit that was applied, not
only what the current run pulled. Changes pulled by a dry run, or while the
config check was failing, are applied by the next run that can.

With `apply_after_pull: auto` (default) it then runs only what the changed
files need:

| Changed path | Action |
|---|---|
| `automations.yaml`, `automations/`, `blueprints/automation/` | `automation.reload` |
| `scripts.yaml`, `scripts/`, `blueprints/script/` | `script.reload` |
| `scenes.yaml`, `scenes/` | `scene.reload` |
| `templates/`, `groups.yaml`, `customize.yaml`, `themes/`, `python_scripts/` | the matching reload |
| `configuration.yaml`, `packages/`, `custom_components/`, other YAML | restart required |
| `dashboards/*.yaml`, `ui-lovelace*.yaml` | open browser tabs are told to reload the dashboard |
| docs, `www/`, `esphome/`, `zigbee2mqtt/`, `.github/` | nothing |

Home Assistant re-reads a YAML dashboard only when a browser asks for it, so
without that refresh an open tab keeps the old cards. Dashboards edited in
the UI live in `.storage/` and are out of scope.

A restart is never done behind your back: you get a "restart required"
notification and an `ha_gitops_restart_required` event (use it for an
actionable phone notification), unless `auto_restart: true`.

## Control from Home Assistant

**Entities (MQTT).** With an MQTT broker set up in HA (Mosquitto, or any
broker the MQTT integration uses) the app creates an **HA GitOps** device:

| Entity | What it shows or does |
|---|---|
| Status | `success` / `warning` / `error` / `running`, all details as attributes |
| Pending changes | files changed on the box and not yet in git |
| Held back files | files kept out of the backup because they look like a secret |
| Restart required | on when merged changes need a Home Assistant restart |
| Open PR | PR number (URL and checks as attributes) |
| PR checks | `success` / `pending` / `failure` / `none` |
| Last run, Last apply, Local / Remote commit | diagnostics |
| Sync now, Back up now, Dry run | buttons |
| Pause automatic sync, Apply merged changes | switches |
| Config (update entity) | "update available" when the remote is ahead; Install = sync |

Without MQTT the app publishes a single `sensor.ha_gitops_status` with the
same data as attributes (it reappears on the next run after an HA restart).

Any device with a login on your MQTT broker can press those buttons. Set
`mqtt_commands: false` to keep the sensors and remove the buttons, switches
and update entity. Retained command messages are ignored, and `discard`,
`merge` and `restart` are never accepted over MQTT.

**Commands.** `hassio.addon_stdin` accepts `sync`, `backup`, `dry_run`,
`pause`, `resume`, `auto_apply_on`, `auto_apply_off`, `revert <sha>`,
`discard`, `merge` and `restart`:

```yaml
action: hassio.addon_stdin
data:
  addon: <this app's slug, shown in its URL>
  input: sync
```

**Events.** `ha_gitops_pulled`, `ha_gitops_applied`,
`ha_gitops_restart_required`, `ha_gitops_pr_opened`, `ha_gitops_pr_updated`,
`ha_gitops_secrets_held`, `ha_gitops_discarded` and `ha_gitops_error`, with
details in the event data.

**Panel.** The **GitOps** sidebar entry shows where the box and GitHub stand
and sorts every changed file into a bay:

- **Not backed up**: on the box, not on GitHub yet. *Back up now* pushes
  them; *Discard…* drops them.
- **In review**: already in the open backup PR, with its checks. *Merge*
  merges the PR on GitHub (when its checks pass, or when the repository has
  none) and pulls it straight back; `merge_method` picks merge, squash or
  rebase.
- **Held back**: files that look like they contain a secret. Their content
  is never shown in the panel.
- **Landed**: the history of the base branch. *Revert…* opens a PR that
  reverts a commit: nothing changes on the box until you merge it.

The panel wears the Home Assistant theme in use (light, dark or custom).
Each file opens in place into its diff, new files included. When merged
changes need a restart, the panel offers **Restart Home Assistant**, which
runs the config check first and refuses on an invalid configuration.

**Discard…** puts `/config` back to the base branch: local edits are
reverted, new files removed, deleted files restored, and the open backup PR
is closed. Files git ignores (`secrets.yaml`, `.storage/`…) and held-back
files are left alone. A safety copy stays on the box: the last five are kept
under `refs/gitops/discarded/`, and `git stash apply <ref>` in `/config`
brings one back.

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `github_repo` | — | `owner/repo` to back up into (required) |
| `github_token` | — | Fine-grained PAT (required) |
| `base_branch` | `main` | Branch PRs target |
| `branch_prefix` | `auto-backup` | Prefix for backup branches |
| `interval_hours` | `24` | Hours between scheduled runs (1–168) |
| `run_at_start` | `false` | Also run when the app starts |
| `dry_run` | `false` | Log what would change; push nothing |
| `commit_name` / `commit_email` | see defaults | Commit author identity |
| `signoff` | `true` | Add `Signed-off-by` (DCO) to commits |
| `apply_after_pull` | `auto` | `auto` (targeted reloads, restart on request), `reload` (`homeassistant.reload_all`), `restart`, or `off`. Always after a passing config check, never in `dry_run` |
| `auto_restart` | `false` | In `auto` mode, restart HA by itself when merged changes need it |
| `on_invalid_config` | `notify` | `notify`, or `revert_pr` to also open a PR that reverts pulled changes failing the config check |
| `merge_method` | `merge` | How the panel's Merge button merges the backup PR: `merge`, `squash` or `rebase` |
| `sync_mode` | `both` | `both`, `pull`, `push` or `schedule` (see Real-time sync) |
| `poll_seconds` | `60` | Remote poll interval; `0` = off |
| `debounce_seconds` | `120` | Quiet time after local edits before committing |
| `webhook_secret` | — | Turns on the GitHub webhook on port 8098 |
| `mqtt_discovery` | `true` | Create the MQTT device and entities |
| `mqtt_commands` | `true` | Accept commands over MQTT; `false` keeps only the sensors |

## What is committed

Everything in your config folder **except** the seeded `.gitignore` exclusions:
`secrets.yaml`, `.storage/`, `.cloud/`, databases, logs, `*.token`,
`.google.token`, `known_devices.yaml`, `ip_bans.yaml`, and the app's own
status/log files.

## Status and logs

The panel and the entities above replace the old `command_line` sensor. The
run log is in the app's **Log** tab and the panel (stored in the app's own
`/data`, no longer in `/config`). The one-line `/config/.gitops_backup_status`
file is still written for existing `command_line` sensors.

Values: `success:pr_created:<url>` · `success:pr_updated:<url>` ·
`success:pr_unchanged:<url>` · `success:no_changes:` · `success:synced:<sha>` ·
`success:discarded:<n files>` · `success:dry_run:<n files>` ·
`error:timeout:…` · `warning:secrets_held:<n files>` (only files
that look like they hold a secret were left, see the notification) ·
`warning:…` · `error:…`
