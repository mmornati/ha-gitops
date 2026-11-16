#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Anil Belur
##############################################################################
# GitOps Config Backup — PR-gated GitHub backup for Home Assistant config
#
# This is the git engine. The controller (controller.py) decides WHEN to run
# it; the engine decides WHAT to do. GITOPS_MODE selects the job:
#   full   (default) sync upstream, apply it, then back up local drift as a PR
#   sync   sync upstream and apply it; leave local drift alone
#   revert open a PR that reverts REVERT_SHA on the base branch
#   discard put /config back to the base branch, dropping local drift
#
# Workflow (never pushes to the base branch after bootstrap):
#   1. Stash any local changes
#   2. pull --rebase from origin/<base> to sync upstream changes (merged PRs)
#   3. Re-apply stashed changes; apply what came down to the running HA
#   4. If the live config drifted, commit it to the open backup PR's branch
#      (or a new dated branch + PR when none is open)
#
# Status: ${DATA_DIR}/status.json (read by the controller) plus the legacy
# one-line ${CONFIG_DIR}/.gitops_backup_status: <status>:<detail>:<extra>
##############################################################################

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPTIONS="${OPTIONS:-/data/options.json}"
CONFIG_DIR="${CONFIG_DIR:-/homeassistant}"
DATA_DIR="${DATA_DIR:-/data}"
LOG_FILE="${DATA_DIR}/gitops_backup.log"
MAX_LOG_LINES=500
STATUS_FILE="${CONFIG_DIR}/.gitops_backup_status"
STATUS_JSON="${DATA_DIR}/status.json"
PR_STATE="${DATA_DIR}/open_pr"
LOCK_FILE="${DATA_DIR}/gitops.lock"
LOCK_WAIT="${LOCK_WAIT:-900}"
APPLIED_STATE="${DATA_DIR}/applied_head"   # last base commit applied to HA
INVALID_STATE="${DATA_DIR}/invalid_head"   # base commit whose config check failed
HELD_STATE="${DATA_DIR}/held"              # files the last backup held back
GITHUB_API="${GITHUB_API:-https://api.github.com}"
HA_API="${HA_API:-http://supervisor/core/api}"
GITOPS_MODE="${GITOPS_MODE:-full}"
GITOPS_TRIGGER="${GITOPS_TRIGGER:-manual}"

opt() { jq -r "$1 // empty" "$OPTIONS"; }

GITHUB_REPO="$(opt .github_repo)"
GITHUB_TOKEN="$(opt .github_token)"
BASE_BRANCH="$(opt .base_branch)"
BRANCH_PREFIX="$(opt .branch_prefix)"
DRY_RUN="${DRY_RUN_OVERRIDE:-$(opt .dry_run)}"
COMMIT_NAME="$(opt .commit_name)"
COMMIT_EMAIL="$(opt .commit_email)"
SIGNOFF="$(opt .signoff)"
APPLY_AFTER_PULL="${APPLY_OVERRIDE:-$(opt .apply_after_pull)}"
AUTO_RESTART="$(opt .auto_restart)"
ON_INVALID="$(opt .on_invalid_config)"
: "${APPLY_AFTER_PULL:=auto}"
: "${ON_INVALID:=notify}"

# The token never touches disk: git gets it from GIT_ASKPASS, which reads it
# from the environment. The remote URL in /config/.git/config stays clean.
REMOTE_URL="https://github.com/${GITHUB_REPO}.git"
export GITOPS_TOKEN="$GITHUB_TOKEN"
export GIT_ASKPASS="${HERE}/git_askpass.sh"
export GIT_TERMINAL_PROMPT=0
# A stalled transfer (under 1 kB/s for a minute) fails instead of hanging the run
export GIT_HTTP_LOW_SPEED_LIMIT=1000
export GIT_HTTP_LOW_SPEED_TIME=60
API="${GITHUB_API}/repos/${GITHUB_REPO}"

SIGNOFF_ARGS=()
[ "$SIGNOFF" = "true" ] && SIGNOFF_ARGS=(-s)

# Filled in as the run goes, written to status.json
PR_URL="" PR_NUMBER="" PR_BRANCH="" CHANGED_COUNT=0 APPLIED=""
HELD=()

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') - $*" | tee -a "$LOG_FILE"; }

write_status() {
    echo "$1:$2:${3:-}" > "$STATUS_FILE"
    local head=""
    head=$(git -C "$CONFIG_DIR" rev-parse "refs/heads/${BASE_BRANCH}" 2>/dev/null || true)
    local held="[]"
    if [ -s "$HELD_STATE" ]; then
        held=$(jq -Rn '[inputs | select(length > 0)]' "$HELD_STATE")
    fi
    jq -n --arg state "$1" --arg detail "$2" --arg extra "${3:-}" \
        --arg time "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" --arg mode "$GITOPS_MODE" \
        --arg trigger "$GITOPS_TRIGGER" --arg head "$head" \
        --arg pr_url "$PR_URL" --arg pr_number "$PR_NUMBER" \
        --arg pr_branch "$PR_BRANCH" --argjson changed "${CHANGED_COUNT:-0}" \
        --arg applied "$APPLIED" --argjson held "$held" \
        '{state:$state, detail:$detail, extra:$extra, time:$time, mode:$mode,
          trigger:$trigger, local_head:$head, pr_url:$pr_url,
          pr_number:$pr_number, pr_branch:$pr_branch, changed:$changed,
          applied:$applied, held:$held}' > "${STATUS_JSON}.tmp" && mv "${STATUS_JSON}.tmp" "$STATUS_JSON"
    log "Status: $1:$2:${3:-}"
}

rotate_log() {
    [ -f "$LOG_FILE" ] && tail -n "$MAX_LOG_LINES" "$LOG_FILE" > "${LOG_FILE}.tmp" \
        && mv "${LOG_FILE}.tmp" "$LOG_FILE"
}

api_call() { # method path [json-body]
    local method="$1" path="$2" body="${3:-}"
    # The JSON body goes through stdin, not argv: Linux caps one argument at
    # 128 KiB, which a PR body listing a few thousand files goes over. The
    # token goes through a header file on a pipe, so it never shows up in the
    # process list either.
    if [ -n "$body" ]; then
        printf '%s' "$body" | curl -sf --max-time 20 -X "$method" \
            -H @<(printf 'Authorization: token %s\n' "$GITHUB_TOKEN") \
            -H "Accept: application/vnd.github.v3+json" \
            --data-binary @- "${API}${path}"
    else
        curl -sf --max-time 20 -X "$method" \
            -H @<(printf 'Authorization: token %s\n' "$GITHUB_TOKEN") \
            -H "Accept: application/vnd.github.v3+json" \
            "${API}${path}"
    fi
}

ha_call() { # method path [json-body] — Home Assistant Core API via the Supervisor
    [ -n "${SUPERVISOR_TOKEN:-}" ] || return 1
    local body="${3:-}"
    [ -n "$body" ] || body='{}'
    printf '%s' "$body" | curl -sf --max-time 120 -X "$1" \
        -H @<(printf 'Authorization: Bearer %s\n' "$SUPERVISOR_TOKEN") \
        -H "Content-Type: application/json" \
        --data-binary @- "${HA_API}$2"
}

ha_event() { # name json-data — fires ha_gitops_<name> on the HA event bus
    ha_call POST "/events/ha_gitops_$1" "${2:-}" >/dev/null 2>&1 || true
}

ha_notify() { # title message
    local payload
    payload=$(jq -n --arg t "$1" --arg m "$2" \
        '{title:$t, message:$m, notification_id:"ha_gitops"}')
    ha_call POST /services/persistent_notification/create "$payload" >/dev/null 2>&1 || true
}

classify_apply() {
    # Maps one changed path to what applying it to the running HA needs:
    # a reload service (domain/service), "restart", or "none".
    case "$1" in
        automations.yaml|automations/*)        echo "automation/reload" ;;
        scripts.yaml|scripts/*)                echo "script/reload" ;;
        scenes.yaml|scenes/*)                  echo "scene/reload" ;;
        groups.yaml)                           echo "group/reload" ;;
        templates/*|template.yaml|templates.yaml) echo "template/reload" ;;
        customize.yaml|customize/*)            echo "homeassistant/reload_core_config" ;;
        themes/*)                              echo "frontend/reload_themes" ;;
        blueprints/automation/*)               echo "automation/reload" ;;
        blueprints/script/*)                   echo "script/reload" ;;
        blueprints/*)                          echo "none" ;;
        python_scripts/*)                      echo "python_script/reload" ;;
        input_*.yaml)                          echo "homeassistant/reload_all" ;;
        dashboards/*.yaml|dashboards/*.yml|ui-lovelace*.yaml) echo "dashboards" ;;
        # Read by their own apps, not by Home Assistant Core
        esphome/*|zigbee2mqtt/*)               echo "none" ;;
        configuration.yaml|packages/*|custom_components/*|integrations/*) echo "restart" ;;
        www/*|docs/*|tests/*|.github/*|*.md|.gitignore|.yamllint|dashboards/*|*.fake.*) echo "none" ;;
        *.yaml|*.yml)                          echo "restart" ;;
        *)                                     echo "none" ;;
    esac
}

check_config() {
    # Returns 0 when HA says the config on disk is valid (or cannot be asked).
    local out result
    if ! out=$(ha_call POST /config/core/check_config); then
        log "⚠ Could not run the HA config check; continuing without it"
        return 0
    fi
    result=$(echo "$out" | jq -r '.result // "unknown"')
    if [ "$result" = "valid" ]; then
        log "HA config check: valid"
        return 0
    fi
    local errors
    errors=$(echo "$out" | jq -r '.errors // "unknown error"')
    log "❌ HA config check failed — not applying: ${errors}"
    # Every later run checks again (the config may have been fixed on the box),
    # but the same commit is only reported once.
    if [ "$(cat "$INVALID_STATE" 2>/dev/null)" != "${CHECK_HEAD:-}" ]; then
        ha_notify "HA GitOps: config check failed" \
            "The merged changes were pulled to /config but NOT applied, because the configuration check failed:\n\n${errors}\n\nDo not restart Home Assistant until this is fixed."
        ha_event error "$(jq -n --arg e "$errors" '{reason:"config_invalid", errors:$e}')"
    fi
    return 1
}

ha_apply() { # pre post — apply what changed between the two commits
    local pre="$1" post="$2" mode="$APPLY_AFTER_PULL" services="" restart=false path action
    local refresh=false restarted=false
    CHECK_HEAD="$post"
    [ "$mode" = "off" ] && { APPLIED="off"; return 0; }
    if [ -z "${SUPERVISOR_TOKEN:-}" ]; then
        log "⚠ apply_after_pull=${mode} but SUPERVISOR_TOKEN unset; skipping — needs homeassistant_api"
        APPLIED="skipped"
        return 0
    fi
    case "$mode" in
        reload)
            services="homeassistant/reload_all"
            if git diff --name-only "$pre" "$post" | grep -qE '\.ya?ml$'; then refresh=true; fi
            ;;
        restart) restart=true ;;
        *)
            while IFS= read -r path; do
                [ -n "$path" ] || continue
                action=$(classify_apply "$path")
                case "$action" in
                    none) ;;
                    restart) restart=true ;;
                    dashboards) refresh=true ;;
                    *) case " $services " in *" $action "*) ;; *) services="$services $action" ;; esac ;;
                esac
            done < <(git diff --name-only "$pre" "$post")
            ;;
    esac
    if [ -z "${services// /}" ] && [ "$restart" = false ] && [ "$refresh" = false ]; then
        log "Nothing to apply for the pulled changes"
        APPLIED="none"
        return 0
    fi
    # A dashboard-only change loads no configuration: nothing to check
    if [ -n "${services// /}" ] || [ "$restart" = true ]; then
        if ! check_config; then
            APPLIED="config_invalid"
            return 1
        fi
    fi
    local svc done_list=""
    for svc in $services; do
        if ha_call POST "/services/${svc}" >/dev/null; then
            log "✅ Applied: ${svc/\//.}"
            done_list="${done_list:+$done_list,}${svc/\//.}"
        else
            log "⚠ Failed to call ${svc/\//.}; apply manually in HA"
        fi
    done
    if [ "$restart" = true ]; then
        if [ "$mode" = "restart" ] || [ "$AUTO_RESTART" = "true" ]; then
            log "Restarting Home Assistant to apply the pulled changes"
            done_list="${done_list:+$done_list,}homeassistant.restart"
            restarted=true
            ha_call POST /services/homeassistant/restart >/dev/null 2>&1 || true
        else
            log "⚠ The pulled changes need a Home Assistant restart to take effect"
            done_list="${done_list:+$done_list,}restart_required"
            ha_notify "HA GitOps: restart required" \
                "Merged changes (${pre:0:7} → ${post:0:7}) touch configuration that only loads on start. The config check passed. Restart Home Assistant to apply them."
            ha_event restart_required "$(jq -n --arg from "$pre" --arg to "$post" '{from:$from, to:$to}')"
        fi
    fi
    # Home Assistant re-reads a YAML dashboard only when a client asks for it,
    # so open browser tabs keep the old cards. A restart reconnects them anyway.
    if [ "$refresh" = true ] && [ "$restarted" = false ]; then
        if python3 "${HERE}/refresh_dashboards.py" 2>&1 | tee -a "$LOG_FILE"; then
            done_list="${done_list:+$done_list,}dashboards.refresh"
        else
            log "⚠ Could not refresh open dashboards; reload the browser tab instead"
        fi
    fi
    APPLIED="${done_list:-none}"
    ha_event applied "$(jq -n --arg from "$pre" --arg to "$post" --arg a "$done_list" '{from:$from, to:$to, actions:$a}')"
}

seed_gitignore() {
    if [ ! -f .gitignore ]; then
        log "Seeding recommended Home Assistant .gitignore"
        cat > .gitignore <<'EOF'
# Secrets and credentials — never commit
secrets.yaml
secrets.yaml.*
!secrets.yaml.example
!secrets.yaml.sample
!secrets.yaml.template
!secrets.yaml.dist
*.bak
*.bak-*
*.bak.*
*.orig
*.secret
*.pem
*.key
*.token
*-key.bin
esphome/.device-builder*
.google.token
.git-credentials
.cloud/
.storage/
zigbee2mqtt/secret.yaml

# Runtime state, databases, logs
*.db
*.db-shm
*.db-wal
*.log
*.log.*
ip_bans.yaml
known_devices.yaml
home-assistant_v2.*
OZW_Log.txt
.uuid
.ha_run.lock

# This app's runtime metadata (committing it creates a PR feedback loop)
.gitops_backup_status
gitops_backup.log

# Misc
__pycache__/
deps/
tts/
backups/
EOF
    fi
}

never_commit_patterns() {
    # Secrets, credentials, and the app's own runtime metadata — must never be
    # committed. Shared by the exclude writer and the untracker. Backup and
    # editor copies of secrets.yaml (`secrets.yaml.bak-2026…`, `*.orig`) are
    # the most common way a real secret slips into a PR, so they are covered
    # too; the `!` lines keep committed templates such as secrets.yaml.example.
    cat <<'EOF'
secrets.yaml
secrets.yaml.*
!secrets.yaml.example
!secrets.yaml.sample
!secrets.yaml.template
!secrets.yaml.dist
*.bak
*.bak-*
*.bak.*
*.orig
*.secret
*.pem
*.key
*.token
*-key.bin
esphome/.device-builder*
.google.token
.git-credentials
.cloud/
.storage/
zigbee2mqtt/secret.yaml
.gitops_backup_status
gitops_backup.log
EOF
}

write_secret_excludes() {
    # Write the never-commit patterns to .git/info/exclude (git's local,
    # un-shared ignore) so secrets/credentials + app metadata stay out of
    # backups even when migrating a repo that already has its own .gitignore
    # (seed_gitignore only writes one for a fresh repo). Called before the stash
    # so untracked secrets are never swept in. Idempotent: replace our block.
    [ -d .git ] || return 0
    mkdir -p .git/info
    local exclude=".git/info/exclude"
    if [ -f "$exclude" ]; then
        sed -i \
            '/# >>> gitops-backup managed >>>/,/# <<< gitops-backup managed <<</d' \
            "$exclude"
    fi
    {
        echo "# >>> gitops-backup managed >>>"
        never_commit_patterns
        echo "# <<< gitops-backup managed <<<"
    } >> "$exclude"
}

untrack_secrets() {
    # If a prior run committed a secret/credential, stop tracking it. Called
    # AFTER the upstream sync (not before the stash — a staged deletion would be
    # lost in stash/pop) so the removal counts as drift and flows out through the
    # next backup PR for review. Uses git's own ignore matcher, so the `!`
    # negations behave exactly as they do in .gitignore.
    [ -d .git ] || return 0
    local patterns path
    patterns=$(mktemp)
    never_commit_patterns > "$patterns"
    while IFS= read -r -d '' path; do
        log "⚠ Untracking previously-committed sensitive path: ${path}"
        git --literal-pathspecs rm -q --cached -- "$path"
    done < <(git ls-files -z -c -i --exclude-from="$patterns")
    rm -f "$patterns"
}

scan_staged_for_secrets() {
    # Content-level backstop: unstages files whose staged content (the blob in
    # the index, exactly what would be committed) looks like a secret, see
    # secret_scan.py, and lists them in HELD. Fails closed: if the scanner
    # cannot run or a file cannot be unstaged, the caller must not push.
    HELD=()
    local out label path
    local -a against=()
    # Everything that differs from the base branch is scanned, not only what
    # this run adds: on top of an open backup PR that includes the files the
    # PR already has, which an older version may have pushed unscanned.
    if git rev-parse -q --verify "refs/heads/${BASE_BRANCH}" >/dev/null 2>&1; then
        against=("refs/heads/${BASE_BRANCH}")
    fi
    out=$(mktemp)
    # --no-renames: a file renamed and edited at once must count as added,
    # not as a rename the A/M filter would skip.
    if ! git diff --cached --no-renames --name-only --diff-filter=AM -z ${against[@]+"${against[@]}"} \
        | python3 "${HERE}/secret_scan.py" > "$out"; then
        rm -f "$out"
        log "❌ Secret scan could not run; not backing up"
        return 1
    fi
    # Two NUL-ended fields per record, read without IFS splitting, so a path
    # with tabs, newlines or leading/trailing blanks comes through intact.
    while IFS= read -r -d '' path && IFS= read -r -d '' label; do
        # Only the rule label and the path are logged, never the match
        log "⚠ Secret-like content (${label}) in ${path} — held back from backup"
        # Back to what the base branch has, or out of the index when the base
        # branch does not have the file. On top of an open backup PR this also
        # takes a file out of the PR if an earlier commit put it there.
        if git rev-parse -q --verify "refs/heads/${BASE_BRANCH}:${path}" >/dev/null 2>&1; then
            git --literal-pathspecs reset -q "refs/heads/${BASE_BRANCH}" -- "$path"
        else
            git --literal-pathspecs rm -q --cached --ignore-unmatch -- "$path"
        fi
        HELD+=("$path")
    done < "$out"
    rm -f "$out"
    # A held file that is somehow still staged must never be pushed
    local want have
    for path in ${HELD[@]+"${HELD[@]}"}; do
        want=$(git rev-parse -q --verify "refs/heads/${BASE_BRANCH}:${path}" 2>/dev/null || true)
        have=$(git --literal-pathspecs ls-files -s -- "$path" | awk '{print $2}')
        if [ "$want" != "$have" ]; then
            log "❌ Could not unstage ${path}; not backing up"
            return 1
        fi
    done
    # Remembered for the panel, also across runs that do not scan (sync)
    if [ "${#HELD[@]}" -gt 0 ]; then
        printf '%s\n' "${HELD[@]}" > "$HELD_STATE"
    else
        rm -f "$HELD_STATE"
    fi
}

held_report() {
    # Tells Home Assistant which files a run held back, if any.
    [ "${#HELD[@]}" -gt 0 ] || return 0
    local list
    list=$(printf -- '- %s\n' "${HELD[@]}")
    ha_notify "HA GitOps: files held back" \
        "These files look like they contain a secret, so they were NOT backed up to GitHub:

${list}

Move the value to secrets.yaml and reference it with !secret, or add the file to .gitignore."
    ha_event secrets_held "$(printf '%s\n' "${HELD[@]}" | jq -Rn '{files: [inputs]}')"
}

bootstrap_if_needed() {
    # Returns 0 if bootstrap ran (caller should stop), 1 if repo already exists
    if [ -d .git ]; then return 1; fi
    log "No git repo in ${CONFIG_DIR} — bootstrapping"
    log "⚠ This app versions DECLARATIVE config only. It does NOT back up secrets.yaml, .storage/ (UI-configured integrations, logins, device/entity registries, dashboards), the database, or app data — those are excluded on purpose. For a 100% restore you MUST also run HA full backups (e.g. the Google Drive Backup app). See this app's DOCS → 'What is backed up where'."
    if [ "$DRY_RUN" = "true" ]; then
        seed_gitignore
        log "DRY RUN: would git init, commit initial import, push to ${BASE_BRANCH}"
        write_status success dry_run bootstrap
        return 0
    fi
    git init -q -b "$BASE_BRANCH"
    seed_gitignore
    write_secret_excludes
    git add -A
    if ! scan_staged_for_secrets; then
        rm -rf .git
        write_status error secret_scan "the secret scan could not run"
        exit 1
    fi
    git commit -q "${SIGNOFF_ARGS[@]}" -m "chore: initial Home Assistant config import"
    git remote add origin "$REMOTE_URL"
    # Push base branch only on bootstrap; every later change goes through a PR
    git push -q -u origin "$BASE_BRANCH"
    write_status success bootstrap "initial import pushed to ${BASE_BRANCH}"
    held_report
    log "Bootstrap complete"
}

find_open_pr() {
    # Sets PR_BRANCH/PR_URL/PR_NUMBER to the open backup PR, if there is one,
    # so new drift is added to it instead of opening another PR.
    local json line
    PR_BRANCH="" PR_URL="" PR_NUMBER=""
    if json=$(api_call GET "/pulls?state=open&base=${BASE_BRANCH}&per_page=100"); then
        line=$(echo "$json" | jq -r --arg p "${BRANCH_PREFIX}/" --arg r "$GITHUB_REPO" '
            [ .[] | select(.head.ref | startswith($p))
                  | select(.head.ref | contains("/revert-") | not)
                  | select(.head.repo.full_name == $r) ]
            | sort_by(.created_at) | last
            | select(. != null) | [.head.ref, .html_url, (.number|tostring)] | @tsv')
        if [ -n "$line" ]; then
            IFS=$'\t' read -r PR_BRANCH PR_URL PR_NUMBER <<< "$line"
            printf '%s\t%s\t%s\n' "$PR_BRANCH" "$PR_URL" "$PR_NUMBER" > "$PR_STATE"
        else
            rm -f "$PR_STATE"
        fi
        return 0
    fi
    # GitHub API unreachable: fall back to the branch we last pushed, as long
    # as it still exists and has not been merged into the base branch yet.
    [ -f "$PR_STATE" ] || return 0
    IFS=$'\t' read -r PR_BRANCH PR_URL PR_NUMBER < "$PR_STATE" || true
    if [ -z "$PR_BRANCH" ] || ! git fetch -q origin "$PR_BRANCH" 2>/dev/null \
        || git merge-base --is-ancestor FETCH_HEAD "refs/heads/${BASE_BRANCH}"; then
        PR_BRANCH="" PR_URL="" PR_NUMBER=""
        rm -f "$PR_STATE"
    fi
}

PR_BODY_MAX=${PR_BODY_MAX:-60000} # GitHub rejects PR bodies over 65536 chars

pr_body() { # base head — markdown summary of the files a PR changes
    local base="$1" head="$2" st path group
    echo "Automated Home Assistant config backup, opened by the HA GitOps app."
    echo "Last update: $(date '+%Y-%m-%d %H:%M') (trigger: ${GITOPS_TRIGGER}). Review and merge to keep it. Closing this PR does not undo the changes on the box: use Discard local changes in the HA GitOps panel for that."
    echo
    git diff --name-status "$base" "$head" | while IFS=$'\t' read -r st path _; do
        case "$path" in
            automations.yaml|automations/*) group="Automations" ;;
            scripts.yaml|scripts/*)         group="Scripts" ;;
            scenes.yaml|scenes/*)           group="Scenes" ;;
            dashboards/*|ui-lovelace*|www/*) group="Dashboards and frontend" ;;
            packages/*)                     group="Packages" ;;
            custom_components/*)            group="Custom components" ;;
            blueprints/*)                   group="Blueprints" ;;
            configuration.yaml|customize*|templates/*) group="Core configuration" ;;
            *)                              group="Other" ;;
        esac
        printf '%s\t%s\t%s\n' "$group" "$st" "$path"
    done | sort | awk -F'\t' -v max="$PR_BODY_MAX" '
        # Stop listing once the body would pass max chars (a big integration
        # update touches thousands of files); the PR diff still has them all.
        skipped { skipped++; next }
        {
            s = ($2 == "A") ? "added" : ($2 == "D") ? "deleted" : "modified"
            out = ($1 != g) ? sprintf("\n### %s\n\n", $1) : ""
            out = out sprintf("- `%s` (%s)\n", $3, s)
            if (used + length(out) > max - 200) { skipped = 1; next }
            g = $1; used += length(out); printf "%s", out
        }
        END { if (skipped) printf "\n…and %d more files, not listed here to keep this description under the GitHub limit. See the Files changed tab.\n", skipped }'
}

create_pull_request() {
    local branch="$1" title="$2" body="$3"
    local payload
    # The body goes through stdin: as an argument it can pass the 128 KiB
    # per-argument limit ("jq: Argument list too long").
    payload=$(printf '%s' "$body" | jq -Rs --arg t "$title" \
        --arg h "$branch" --arg base "$BASE_BRANCH" \
        '{title:$t, body:., head:$h, base:$base}')
    api_call POST /pulls "$payload" | jq -r '[.html_url, (.number|tostring)] | @tsv'
}

update_pull_request() { # number title body
    local payload
    payload=$(printf '%s' "$3" | jq -Rs --arg t "$2" '{title:$t, body:.}')
    api_call PATCH "/pulls/$1" "$payload" >/dev/null
}

cleanup_branch() {
    local current
    current=$(git branch --show-current 2>/dev/null || echo "")
    if [ "$current" != "$BASE_BRANCH" ] && [ -n "$current" ]; then
        # Point HEAD back at the base branch WITHOUT touching the working tree.
        # A plain `git checkout` would rewrite /config to the pre-backup base
        # branch, rolling the live config back until the next run pulls the
        # merged PR (and losing the drift for good if the PR is never merged).
        # The drift stays as uncommitted changes and is reconciled by the
        # stash/pull/pop on the next run.
        git symbolic-ref HEAD "refs/heads/${BASE_BRANCH}" 2>/dev/null || true
        git reset -q 2>/dev/null || true
    fi
}

reconcile_stash() { # stash-commit
    # `git stash pop` failed and kept the entry. That usually means nothing is
    # wrong: a merged backup PR brought back the very files that were stashed,
    # so the untracked ones "already exist" and a moved file looks like a
    # rename/delete conflict. Settle what pop left, path by path, with the live
    # (stashed) version winning, and set STASH_DIVERGED when a live file really
    # differs from what upstream has. Files pop merged cleanly are left alone.
    # Returns non-zero if a live file could not be restored: the caller must
    # then keep the stash, the only copy left.
    local stash="$1" untracked="" path src blob head entry previous=""
    STASH_DIVERGED=false
    git rev-parse -q --verify "${stash}^3" >/dev/null && untracked="${stash}^3"

    # Paths from ls-files, not git diff: diff refreshes the index as it exits,
    # and that lock makes the checkout below fail while this loop reads its output.
    # Each unmerged path has one line per stage.
    while IFS= read -r -d '' entry; do
        path="${entry#*$'\t'}"
        [ "$path" != "$previous" ] || continue
        previous="$path"
        src=""
        if git cat-file -e "${stash}:${path}" 2>/dev/null; then
            src="$stash"
        elif [ -n "$untracked" ] && git cat-file -e "${untracked}:${path}" 2>/dev/null; then
            src="$untracked"
        fi
        head=$(git rev-parse -q --verify "HEAD:${path}" 2>/dev/null || true)
        if [ -z "$src" ]; then
            # Deleted on the box
            [ -z "$head" ] || STASH_DIVERGED=true
            rm -f -- "$path" || return 1
        else
            blob=$(git rev-parse "${src}:${path}") || return 1
            [ "$blob" = "$head" ] || STASH_DIVERGED=true
            git --literal-pathspecs checkout -q "$src" -- "$path" || return 1
        fi
    done < <(git ls-files -u -z)

    [ -n "$untracked" ] || return 0
    while IFS= read -r -d '' path; do
        blob=$(git rev-parse "${untracked}:${path}") || return 1
        head=$(git rev-parse -q --verify "HEAD:${path}" 2>/dev/null || true)
        [ "$blob" != "$head" ] || continue
        [ -z "$head" ] || STASH_DIVERGED=true
        git --literal-pathspecs checkout -q "$untracked" -- "$path" || return 1
    done < <(git ls-tree -r -z --name-only "$untracked")
}

revert_onto() { # onto branch sha... — pushes a branch that reverts the commits
    # Commits are reverted in the order given (newest first). Works in a
    # separate worktree, so the live /config is never touched.
    # Returns 0 when pushed (or dry run), 1 when a commit does not revert
    # cleanly, 2 when the push failed.
    local onto="$1" branch="$2" wt sha parents args
    shift 2
    wt=$(mktemp -d)
    git worktree add -q --detach "$wt" "$onto"
    for sha in "$@"; do
        parents=$(git rev-list --parents -n 1 "$sha" | wc -w)
        args=(--no-edit)
        if [ "$parents" -gt 2 ]; then args+=(-m 1); fi
        if ! git -C "$wt" revert "${args[@]}" "$sha" >/dev/null 2>&1; then
            git -C "$wt" revert --abort 2>/dev/null || true
            git worktree remove --force "$wt"
            return 1
        fi
        if [ "$SIGNOFF" = "true" ]; then git -C "$wt" commit -q --amend --no-edit -s; fi
    done
    if [ "$DRY_RUN" != "true" ] && ! git -C "$wt" push -q origin "HEAD:refs/heads/${branch}"; then
        git worktree remove --force "$wt"
        return 2
    fi
    git worktree remove --force "$wt"
}

revert_commit() {
    # Opens a PR that reverts REVERT_SHA on the base branch.
    local sha="${REVERT_SHA:-}" subject branch onto rc=0
    if ! [[ "$sha" =~ ^[0-9a-f]{7,40}$ ]]; then
        write_status error revert "invalid commit id"
        return 1
    fi
    git fetch -q origin "$BASE_BRANCH"
    onto=$(git rev-parse FETCH_HEAD)
    sha=$(git rev-parse --verify -q "${sha}^{commit}") \
        || { write_status error revert "unknown commit ${REVERT_SHA}"; return 1; }
    git merge-base --is-ancestor "$sha" "$onto" \
        || { write_status error revert "${sha:0:7} is not on ${BASE_BRANCH}"; return 1; }
    subject=$(git log -1 --format=%s "$sha")
    branch="${BRANCH_PREFIX}/revert-${sha:0:7}"
    revert_onto "$onto" "$branch" "$sha" || rc=$?
    if [ "$rc" -eq 1 ]; then
        write_status error revert "${sha:0:7} does not revert cleanly"
        return 1
    elif [ "$rc" -ne 0 ]; then
        write_status error revert "could not push ${branch}"
        return 1
    fi
    if [ "$DRY_RUN" = "true" ]; then
        write_status success dry_run "would open revert PR for ${sha:0:7}"
        return 0
    fi
    local out
    if out=$(create_pull_request "$branch" "Revert \"${subject}\"" \
        "Reverts ${sha} on \`${BASE_BRANCH}\`. Opened from the HA GitOps panel; merge to roll the change back on the box.") \
        && [ -n "$out" ]; then
        IFS=$'\t' read -r PR_URL PR_NUMBER <<< "$out"
        write_status success revert_pr "$PR_URL"
        ha_event pr_opened "$(jq -n --arg u "$PR_URL" '{url:$u, kind:"revert"}')"
    else
        write_status warning branch_pushed "$branch"
    fi
}

revert_invalid_pull() { # pre post — PR that reverts what was just pulled
    # on_invalid_config: revert_pr. The pulled commits failed the HA config
    # check; open a PR that takes them back out, for the user to merge.
    local pre="$1" post="$2" branch out url rc=0
    local -a shas
    branch="${BRANCH_PREFIX}/revert-${post:0:7}"
    [ "$DRY_RUN" != "true" ] || return 0
    if git ls-remote --exit-code origin "refs/heads/${branch}" >/dev/null 2>&1; then
        log "Revert branch ${branch} already exists; not opening another PR"
        return 0
    fi
    mapfile -t shas < <(git rev-list --first-parent "${pre}..${post}")
    [ "${#shas[@]}" -gt 0 ] || return 0
    revert_onto "$post" "$branch" "${shas[@]}" || rc=$?
    if [ "$rc" -ne 0 ]; then
        log "⚠ Could not prepare a revert PR for ${pre:0:7}..${post:0:7}; revert by hand"
        return 0
    fi
    if out=$(create_pull_request "$branch" "Revert ${pre:0:7}..${post:0:7}: config check failed" \
        "The Home Assistant config check failed after these changes were pulled to the box, so they were not applied. This PR takes them back out of \`${BASE_BRANCH}\`. Merge it to put the box back on a valid configuration, or fix the configuration in another PR and close this one.") \
        && [ -n "$out" ]; then
        url="${out%%$'\t'*}"
        log "Opened a revert PR for the invalid configuration: ${url}"
        ha_event pr_opened "$(jq -n --arg u "$url" '{url:$u, kind:"revert", reason:"config_invalid"}')"
        ha_notify "HA GitOps: revert PR opened" \
            "The pulled changes failed the configuration check. A PR that reverts them is ready to merge: ${url}"
    else
        log "⚠ Revert branch ${branch} pushed, but the PR could not be opened"
    fi
}

discard_drift() {
    # Puts /config back to the base branch. Files git ignores (secrets.yaml,
    # .storage/, databases…) and files held back as secret-like are left
    # alone. What is dropped stays recoverable under refs/gitops/discarded/.
    local stash ref count
    git add -A
    scan_staged_for_secrets || { git reset -q; write_status error secret_scan "the secret scan could not run"; return 1; }
    if git diff --cached --quiet; then
        git reset -q
        write_status success no_changes "nothing to discard"
        return 0
    fi
    count=$(git diff --cached --name-only | wc -l | tr -d ' ')
    CHANGED_COUNT="$count"
    if [ "$DRY_RUN" = "true" ]; then
        log "DRY RUN: ${count} changed file(s) would be discarded:"
        git diff --cached --name-status | tee -a "$LOG_FILE"
        git reset -q
        write_status success dry_run "would discard ${count} files"
        return 0
    fi
    git stash push --staged -q -m "discarded by HA GitOps $(date '+%Y-%m-%d %H:%M:%S')"
    stash=$(git rev-parse refs/stash)
    ref="refs/gitops/discarded/$(date '+%Y%m%d-%H%M%S')"
    git update-ref "$ref" "$stash"
    git stash drop -q
    git reset -q
    # Keep the five most recent safety copies
    git for-each-ref --sort=-refname --format='%(refname)' refs/gitops/discarded \
        | tail -n +6 | while IFS= read -r old; do git update-ref -d "$old"; done
    log "Discarded ${count} local change(s); safety copy: git stash apply ${ref}"

    # The open backup PR holds exactly what was just dropped: close it
    find_open_pr
    if [ -n "$PR_BRANCH" ]; then
        if [ -n "$PR_NUMBER" ]; then
            if api_call PATCH "/pulls/${PR_NUMBER}" '{"state":"closed"}' >/dev/null; then
                log "Closed backup PR #${PR_NUMBER}"
            fi
        fi
        git push -q origin --delete "$PR_BRANCH" 2>/dev/null || true
        git branch -q -D "$PR_BRANCH" 2>/dev/null || true
    fi
    rm -f "$PR_STATE"
    PR_URL="" PR_NUMBER="" PR_BRANCH=""

    # Home Assistant still runs the discarded config: load what is on disk now
    ha_apply "$stash" HEAD || true
    write_status success discarded "${count} files"
    ha_event discarded "$(jq -n --argjson n "$count" --arg ref "$ref" '{files:$n, ref:$ref}')"
}

backup_drift() {
    # 4. Anything to back up?
    if [ -z "$(git status --porcelain)" ]; then
        write_status success no_changes ""
        log "No changes to back up"
        return 0
    fi

    CHANGED_COUNT=$(git status --porcelain | wc -l | tr -d ' ')

    if [ "$DRY_RUN" = "true" ]; then
        log "DRY RUN: ${CHANGED_COUNT} changed file(s) would be backed up:"
        git status --porcelain | tee -a "$LOG_FILE"
        write_status success dry_run "${CHANGED_COUNT} files"
        return 0
    fi

    find_open_pr
    local branch_name new_pr=true
    if [ -n "$PR_BRANCH" ] && git fetch -q origin "$PR_BRANCH" 2>/dev/null; then
        # Add the drift on top of the open PR: point HEAD at its tip and stage
        # the live tree. The working tree is not touched, and the push is a
        # plain fast-forward.
        branch_name="$PR_BRANCH"
        new_pr=false
        git update-ref "refs/heads/${branch_name}" FETCH_HEAD
        git symbolic-ref HEAD "refs/heads/${branch_name}"
        git reset -q
        untrack_secrets
        git add -A
        scan_staged_for_secrets || { write_status error secret_scan "the secret scan could not run"; return 1; }
        if git diff --cached --quiet; then
            if [ "${#HELD[@]}" -gt 0 ]; then
                write_status warning secrets_held "${#HELD[@]} files"
                held_report
            else
                write_status success pr_unchanged "${PR_URL:-$branch_name}"
            fi
            log "Drift is already in the open backup PR"
            return 0
        fi
    else
        # Stage and scan before creating the branch, so a run that holds
        # everything back leaves no empty branch behind.
        git add -A
        scan_staged_for_secrets || { write_status error secret_scan "the secret scan could not run"; return 1; }
        if git diff --cached --quiet; then
            write_status warning secrets_held "${#HELD[@]} files"
            log "Nothing left to back up after holding back secret-like files"
            held_report
            return 0
        fi
        branch_name="${BRANCH_PREFIX}/$(date '+%Y-%m-%d-%H%M%S')"
        git checkout -q -b "$branch_name"
    fi
    local step_count msg commit
    step_count=$(git diff --cached --name-only | wc -l | tr -d ' ')
    msg="chore: auto backup $(date '+%Y-%m-%d %H:%M') (${step_count} files)"
    if [ "$new_pr" = false ] && ! git merge-base --is-ancestor "refs/heads/${BASE_BRANCH}" HEAD; then
        # The base branch moved on since this PR was opened. A plain commit
        # would show those merged changes in the PR as if they were drift:
        # record the base as a second parent, so the PR stays limited to drift.
        if [ "$SIGNOFF" = "true" ]; then
            msg+=$'\n\n'"Signed-off-by: ${COMMIT_NAME} <${COMMIT_EMAIL}>"
        fi
        commit=$(git commit-tree "$(git write-tree)" -p HEAD -p "refs/heads/${BASE_BRANCH}" -m "$msg")
        git update-ref HEAD "$commit"
    else
        git commit -q "${SIGNOFF_ARGS[@]}" -m "$msg"
    fi
    git push -q -u origin "$branch_name"
    PR_BRANCH="$branch_name"
    printf '%s\t%s\t%s\n' "$PR_BRANCH" "$PR_URL" "$PR_NUMBER" > "$PR_STATE"

    local total title body out
    total=$(git diff --name-only "refs/heads/${BASE_BRANCH}" HEAD | wc -l | tr -d ' ')
    CHANGED_COUNT="$total"
    title="chore: config backup (${total} files)"
    body=$(pr_body "refs/heads/${BASE_BRANCH}" HEAD)
    if [ "${#HELD[@]}" -gt 0 ]; then
        body+=$'\n\n### Held back\n\nNot in this PR, because the content looks like a secret. Move the value to `secrets.yaml` and use `!secret`, or add the file to `.gitignore`.\n\n'
        # shellcheck disable=SC2016 # literal markdown backticks
        body+=$(printf -- '- `%s`\n' "${HELD[@]}")
    fi
    held_report
    if [ "$new_pr" = false ] && [ -n "$PR_NUMBER" ]; then
        update_pull_request "$PR_NUMBER" "$title" "$body" || true
        write_status success pr_updated "$PR_URL"
        log "✅ Backup added to the open PR: ${PR_URL}"
        ha_event pr_updated "$(jq -n --arg u "$PR_URL" --argjson n "$total" '{url:$u, files:$n}')"
    elif out=$(create_pull_request "$branch_name" "$title" "$body") && [ -n "$out" ]; then
        IFS=$'\t' read -r PR_URL PR_NUMBER <<< "$out"
        printf '%s\t%s\t%s\n' "$PR_BRANCH" "$PR_URL" "$PR_NUMBER" > "$PR_STATE"
        write_status success pr_created "$PR_URL"
        log "✅ Backup complete — PR opened: ${PR_URL}"
        ha_event pr_opened "$(jq -n --arg u "$PR_URL" --argjson n "$total" '{url:$u, files:$n}')"
    else
        write_status warning branch_pushed "$branch_name"
        log "WARNING: branch pushed but PR creation failed"
    fi
}

acquire_lock() {
    # Waits up to LOCK_WAIT seconds for the lock on fd 9. BusyBox flock (the
    # one in the app image) has no -w, so poll with -n, which both have.
    local waited=0
    until flock -n 9; do
        [ "$waited" -lt "$LOCK_WAIT" ] || return 1
        sleep 1
        waited=$((waited + 1))
    done
}

main() {
    mkdir -p "$DATA_DIR"
    # Input validation at the trust boundary
    if [ -z "$GITHUB_REPO" ] || [ -z "$GITHUB_TOKEN" ]; then
        write_status error config "github_repo and github_token are required"
        exit 1
    fi

    # One run at a time: the controller, the webhook, the watcher and a manual
    # run must never stash or rebase /config concurrently.
    exec 9>"$LOCK_FILE"
    if ! acquire_lock; then
        log "Another run still holds the lock after ${LOCK_WAIT}s; giving up"
        exit 75
    fi

    cd "$CONFIG_DIR"

    git config --global --add safe.directory "$CONFIG_DIR"
    git config --global user.name "$COMMIT_NAME"
    git config --global user.email "$COMMIT_EMAIL"

    if bootstrap_if_needed; then
        rotate_log
        return 0
    fi
    # Also scrubs the token from remotes written by versions before 0.4.0
    git remote set-url origin "$REMOTE_URL"
    seed_gitignore
    write_secret_excludes
    # A run that was killed (timeout, app restart) can leave HEAD on a backup
    # branch: put it back before anything else. The working tree is untouched.
    cleanup_branch
    trap cleanup_branch EXIT

    # Unmerged index entries left by an older version after a stash conflict
    # make every git write fail ("needs merge" / "could not write index").
    # Drop them; the working tree (the live config) is left untouched.
    if [ -n "$(git ls-files -u)" ]; then
        log "Clearing unmerged index entries left by an earlier stash conflict"
        git reset -q
    fi

    if [ "$GITOPS_MODE" = "revert" ]; then
        revert_commit
        rotate_log
        return 0
    fi
    if [ "$GITOPS_MODE" = "discard" ]; then
        discard_drift
        rotate_log
        return 0
    fi

    # 1–2. Stash local drift, sync upstream (merged PRs flow back here)
    local stashed=false stash_sha=""
    if [ -n "$(git status --porcelain)" ]; then
        git stash -q --include-untracked
        stashed=true
        stash_sha=$(git rev-parse refs/stash)
    fi
    local pre_sync post_sync
    pre_sync=$(git rev-parse HEAD)
    if ! git pull -q --rebase origin "$BASE_BRANCH"; then
        git rebase --abort 2>/dev/null || true
        [ "$stashed" = true ] && git stash pop -q 2>/dev/null
        write_status error pull_rebase_failed "resolve manually in ${CONFIG_DIR}"
        ha_event error '{"reason":"pull_rebase_failed"}'
        exit 1
    fi
    post_sync=$(git rev-parse HEAD)
    # 3. Re-apply drift
    local conflict=false conflict_detail="kept live config where conflicting"
    if [ "$stashed" = true ] && ! git stash pop -q; then
        if reconcile_stash "$stash_sha"; then
            # Unstage everything: an unmerged index blocks the next run's stash,
            # and the kept live files must be plain drift for the backup below.
            git reset -q
            git stash drop -q 2>/dev/null || true
            if [ "$STASH_DIVERGED" = true ]; then
                conflict=true
                log "⚠ warning:stash_conflict — kept live config where conflicting"
            else
                log "Stash pop failed, but no live file differs from upstream; no conflict"
            fi
        else
            git reset -q 2>/dev/null || true
            conflict=true
            conflict_detail="could not restore every live file, see git stash list"
            log "⚠ warning:stash_conflict — ${conflict_detail} (${stash_sha:0:7})"
        fi
    fi

    # 3b. Apply what came down to the running HA. The range starts at the last
    # commit that was applied, not at this run's pull: a dry run (or a run that
    # failed the config check) pulls without applying, and the next real run
    # must still apply those changes.
    local applied_from
    [ -s "$APPLIED_STATE" ] || echo "$pre_sync" > "$APPLIED_STATE"
    applied_from=$(cat "$APPLIED_STATE")
    if ! git merge-base --is-ancestor "$applied_from" "$post_sync" 2>/dev/null; then
        applied_from="$pre_sync"   # history was rewritten: start from this pull
        echo "$pre_sync" > "$APPLIED_STATE"
    fi
    if [ "$DRY_RUN" = "true" ]; then
        :
    elif [ "$applied_from" != "$post_sync" ]; then
        log "Upstream sync: ${applied_from:0:7} → ${post_sync:0:7} — applying (${APPLY_AFTER_PULL})"
        if [ "$pre_sync" != "$post_sync" ]; then
            ha_event pulled "$(jq -n --arg from "$pre_sync" --arg to "$post_sync" '{from:$from, to:$to}')"
        fi
        if ha_apply "$applied_from" "$post_sync"; then
            echo "$post_sync" > "$APPLIED_STATE"
            rm -f "$INVALID_STATE"
        elif [ "$(cat "$INVALID_STATE" 2>/dev/null)" != "$post_sync" ]; then
            echo "$post_sync" > "$INVALID_STATE"
            if [ "$ON_INVALID" = "revert_pr" ]; then
                revert_invalid_pull "$applied_from" "$post_sync"
            fi
        fi
    fi

    # If a prior run committed a secret/credential, untrack it now (before the
    # change check) so the removal is captured as drift and goes out via a PR.
    untrack_secrets

    if [ "$conflict" = true ]; then
        write_status warning stash_conflict "$conflict_detail"
    fi

    if [ "$GITOPS_MODE" = "sync" ]; then
        CHANGED_COUNT=$(git status --porcelain | wc -l | tr -d ' ')
        if [ "$conflict" = false ]; then
            if [ "$pre_sync" != "$post_sync" ]; then
                write_status success synced "${post_sync:0:7}"
            else
                write_status success up_to_date "${post_sync:0:7}"
            fi
        fi
        rotate_log
        return 0
    fi

    backup_drift
    rotate_log
}

if [ "${GITOPS_SOURCE_ONLY:-}" != "1" ]; then
    main "$@"
fi
