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
: "${APPLY_AFTER_PULL:=auto}"

# The token never touches disk: git gets it from GIT_ASKPASS, which reads it
# from the environment. The remote URL in /config/.git/config stays clean.
REMOTE_URL="https://github.com/${GITHUB_REPO}.git"
export GITOPS_TOKEN="$GITHUB_TOKEN"
export GIT_ASKPASS="${HERE}/git_askpass.sh"
export GIT_TERMINAL_PROMPT=0
API="${GITHUB_API}/repos/${GITHUB_REPO}"

SIGNOFF_ARGS=()
[ "$SIGNOFF" = "true" ] && SIGNOFF_ARGS=(-s)

# Filled in as the run goes, written to status.json
PR_URL="" PR_NUMBER="" PR_BRANCH="" CHANGED_COUNT=0 APPLIED=""

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') - $*" | tee -a "$LOG_FILE"; }

write_status() {
    echo "$1:$2:${3:-}" > "$STATUS_FILE"
    local head=""
    head=$(git -C "$CONFIG_DIR" rev-parse "refs/heads/${BASE_BRANCH}" 2>/dev/null || true)
    jq -n --arg state "$1" --arg detail "$2" --arg extra "${3:-}" \
        --arg time "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" --arg mode "$GITOPS_MODE" \
        --arg trigger "$GITOPS_TRIGGER" --arg head "$head" \
        --arg pr_url "$PR_URL" --arg pr_number "$PR_NUMBER" \
        --arg pr_branch "$PR_BRANCH" --argjson changed "${CHANGED_COUNT:-0}" \
        --arg applied "$APPLIED" \
        '{state:$state, detail:$detail, extra:$extra, time:$time, mode:$mode,
          trigger:$trigger, local_head:$head, pr_url:$pr_url,
          pr_number:$pr_number, pr_branch:$pr_branch, changed:$changed,
          applied:$applied}' > "${STATUS_JSON}.tmp" && mv "${STATUS_JSON}.tmp" "$STATUS_JSON"
    log "Status: $1:$2:${3:-}"
}

rotate_log() {
    [ -f "$LOG_FILE" ] && tail -n "$MAX_LOG_LINES" "$LOG_FILE" > "${LOG_FILE}.tmp" \
        && mv "${LOG_FILE}.tmp" "$LOG_FILE"
}

api_call() { # method path [json-body]
    local method="$1" path="$2" body="${3:-}"
    curl -sf --max-time 20 -X "$method" \
        -H "Authorization: token ${GITHUB_TOKEN}" \
        -H "Accept: application/vnd.github.v3+json" \
        ${body:+-d "$body"} \
        "${API}${path}"
}

ha_call() { # method path [json-body] — Home Assistant Core API via the Supervisor
    [ -n "${SUPERVISOR_TOKEN:-}" ] || return 1
    local body="${3:-}"
    [ -n "$body" ] || body='{}'
    curl -sf --max-time 120 -X "$1" \
        -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "$body" "${HA_API}$2"
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
        configuration.yaml|packages/*|custom_components/*|integrations/*) echo "restart" ;;
        www/*|docs/*|tests/*|.github/*|*.md|.gitignore|.yamllint|dashboards/*|ui-lovelace*.yaml|*.fake.*) echo "none" ;;
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
    ha_notify "HA GitOps: config check failed" \
        "The merged changes were pulled to /config but NOT applied, because the configuration check failed:\n\n${errors}"
    ha_event error "$(jq -n --arg e "$errors" '{reason:"config_invalid", errors:$e}')"
    return 1
}

ha_apply() { # pre post — apply what changed between the two commits
    local pre="$1" post="$2" mode="$APPLY_AFTER_PULL" services="" restart=false path action
    [ "$mode" = "off" ] && { APPLIED="off"; return 0; }
    if [ -z "${SUPERVISOR_TOKEN:-}" ]; then
        log "⚠ apply_after_pull=${mode} but SUPERVISOR_TOKEN unset; skipping — needs homeassistant_api"
        APPLIED="skipped"
        return 0
    fi
    case "$mode" in
        reload)  services="homeassistant/reload_all" ;;
        restart) restart=true ;;
        *)
            while IFS= read -r path; do
                [ -n "$path" ] || continue
                action=$(classify_apply "$path")
                case "$action" in
                    none) ;;
                    restart) restart=true ;;
                    *) case " $services " in *" $action "*) ;; *) services="$services $action" ;; esac ;;
                esac
            done < <(git diff --name-only "$pre" "$post")
            ;;
    esac
    if [ -z "${services// /}" ] && [ "$restart" = false ]; then
        log "Nothing to apply for the pulled changes"
        APPLIED="none"
        return 0
    fi
    if ! check_config; then
        APPLIED="config_invalid"
        return 1
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
            ha_call POST /services/homeassistant/restart >/dev/null 2>&1 || true
        else
            log "⚠ The pulled changes need a Home Assistant restart to take effect"
            done_list="${done_list:+$done_list,}restart_required"
            ha_notify "HA GitOps: restart required" \
                "Merged changes (${pre:0:7} → ${post:0:7}) touch configuration that only loads on start. The config check passed. Restart Home Assistant to apply them."
            ha_event restart_required "$(jq -n --arg from "$pre" --arg to "$post" '{from:$from, to:$to}')"
        fi
    fi
    APPLIED="$done_list"
    ha_event applied "$(jq -n --arg from "$pre" --arg to "$post" --arg a "$done_list" '{from:$from, to:$to, actions:$a}')"
}

seed_gitignore() {
    if [ ! -f .gitignore ]; then
        log "Seeding recommended Home Assistant .gitignore"
        cat > .gitignore <<'EOF'
# Secrets and credentials — never commit
secrets.yaml
*.pem
*.key
*.token
.google.token
.git-credentials
.cloud/
.storage/

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
    # committed. Shared by the exclude writer and the untracker.
    cat <<'EOF'
secrets.yaml
*.key
*.pem
*.token
.google.token
.git-credentials
.cloud/
.storage/
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
    # next backup PR for review.
    [ -d .git ] || return 0
    never_commit_patterns | while IFS= read -r pat; do
        [ -n "$pat" ] || continue
        if git ls-files --error-unmatch -- "$pat" >/dev/null 2>&1; then
            log "⚠ Untracking previously-committed sensitive path: ${pat}"
            git rm -q --cached -r -- "$pat"
        fi
    done
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
    git add -A
    git commit -q "${SIGNOFF_ARGS[@]}" -m "chore: initial Home Assistant config import"
    git remote add origin "$REMOTE_URL"
    # Push base branch only on bootstrap; every later change goes through a PR
    git push -q -u origin "$BASE_BRANCH"
    write_status success bootstrap "initial import pushed to ${BASE_BRANCH}"
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

pr_body() { # base head — markdown summary of the files a PR changes
    local base="$1" head="$2" st path group
    echo "Automated Home Assistant config backup, opened by the HA GitOps app."
    echo "Last update: $(date '+%Y-%m-%d %H:%M') (trigger: ${GITOPS_TRIGGER}). Review and merge to keep it; close it to drop it."
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
    done | sort | awk -F'\t' '
        $1 != g { g = $1; printf "\n### %s\n\n", g }
        { s = ($2 == "A") ? "added" : ($2 == "D") ? "deleted" : "modified";
          printf "- `%s` (%s)\n", $3, s }'
}

create_pull_request() {
    local branch="$1" title="$2" body="$3"
    local payload
    payload=$(jq -n --arg t "$title" --arg b "$body" \
        --arg h "$branch" --arg base "$BASE_BRANCH" \
        '{title:$t, body:$b, head:$h, base:$base}')
    api_call POST /pulls "$payload" | jq -r '[.html_url, (.number|tostring)] | @tsv'
}

update_pull_request() { # number title body
    local payload
    payload=$(jq -n --arg t "$2" --arg b "$3" '{title:$t, body:$b}')
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

revert_commit() {
    # Opens a PR that reverts REVERT_SHA on the base branch. Works in a
    # separate worktree, so the live /config is never touched.
    local sha="${REVERT_SHA:-}" wt subject branch parents
    if ! [[ "$sha" =~ ^[0-9a-f]{7,40}$ ]]; then
        write_status error revert "invalid commit id"
        return 1
    fi
    git fetch -q origin "$BASE_BRANCH"
    sha=$(git rev-parse --verify -q "${sha}^{commit}") \
        || { write_status error revert "unknown commit ${REVERT_SHA}"; return 1; }
    git merge-base --is-ancestor "$sha" FETCH_HEAD \
        || { write_status error revert "${sha:0:7} is not on ${BASE_BRANCH}"; return 1; }
    subject=$(git log -1 --format=%s "$sha")
    branch="${BRANCH_PREFIX}/revert-${sha:0:7}"
    wt=$(mktemp -d)
    git worktree add -q --detach "$wt" FETCH_HEAD
    parents=$(git rev-list --parents -n 1 "$sha" | wc -w)
    local args=(--no-edit)
    [ "$parents" -gt 2 ] && args+=(-m 1)
    if ! git -C "$wt" revert "${args[@]}" "$sha" >/dev/null 2>&1; then
        git -C "$wt" revert --abort 2>/dev/null || true
        git worktree remove --force "$wt"
        write_status error revert "${sha:0:7} does not revert cleanly"
        return 1
    fi
    [ "$SIGNOFF" = "true" ] && git -C "$wt" commit -q --amend --no-edit -s
    if [ "$DRY_RUN" = "true" ]; then
        git worktree remove --force "$wt"
        write_status success dry_run "would open revert PR for ${sha:0:7}"
        return 0
    fi
    git -C "$wt" push -q origin "HEAD:refs/heads/${branch}"
    git worktree remove --force "$wt"
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
        if git diff --cached --quiet; then
            write_status success pr_unchanged "${PR_URL:-$branch_name}"
            log "Drift is already in the open backup PR"
            return 0
        fi
    else
        branch_name="${BRANCH_PREFIX}/$(date '+%Y-%m-%d-%H%M%S')"
        git checkout -q -b "$branch_name"
        git add -A
    fi
    local step_count
    step_count=$(git diff --cached --name-only | wc -l | tr -d ' ')
    git commit -q "${SIGNOFF_ARGS[@]}" \
        -m "chore: auto backup $(date '+%Y-%m-%d %H:%M') (${step_count} files)"
    git push -q -u origin "$branch_name"
    PR_BRANCH="$branch_name"
    printf '%s\t%s\t%s\n' "$PR_BRANCH" "$PR_URL" "$PR_NUMBER" > "$PR_STATE"

    local total title body out
    total=$(git diff --name-only "refs/heads/${BASE_BRANCH}" HEAD | wc -l | tr -d ' ')
    CHANGED_COUNT="$total"
    title="chore: config backup (${total} files)"
    body=$(pr_body "refs/heads/${BASE_BRANCH}" HEAD)
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
    if ! flock -w "$LOCK_WAIT" 9; then
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
    trap cleanup_branch EXIT

    if [ "$GITOPS_MODE" = "revert" ]; then
        revert_commit
        rotate_log
        return 0
    fi

    # Unmerged index entries left by an older version after a stash conflict
    # make every git write fail ("needs merge" / "could not write index").
    # Drop them; the working tree (the live config) is left untouched.
    if [ -n "$(git ls-files -u)" ]; then
        log "Clearing unmerged index entries left by an earlier stash conflict"
        git reset -q
    fi

    # 1–2. Stash local drift, sync upstream (merged PRs flow back here)
    local stashed=false
    if [ -n "$(git status --porcelain)" ]; then
        git stash -q --include-untracked
        stashed=true
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
    local conflict=false
    if [ "$stashed" = true ] && ! git stash pop -q; then
        git checkout -q --theirs . 2>/dev/null || true
        # checkout --theirs only rewrites the files: the index still holds the
        # conflict, which blocks the next run's stash. Unstage everything so
        # the kept live files are plain drift for the backup below.
        git reset -q
        git stash drop -q 2>/dev/null || true
        conflict=true
        log "⚠ warning:stash_conflict — kept live config where conflicting"
    fi

    # 3b. Upstream advanced (a merged PR came down) → apply it to the running HA.
    # Only on real change and never in dry_run, so we don't reload on no-op runs.
    if [ "$pre_sync" != "$post_sync" ] && [ "$DRY_RUN" != "true" ]; then
        log "Upstream sync: ${pre_sync:0:7} → ${post_sync:0:7} — applying (${APPLY_AFTER_PULL})"
        ha_event pulled "$(jq -n --arg from "$pre_sync" --arg to "$post_sync" '{from:$from, to:$to}')"
        ha_apply "$pre_sync" "$post_sync" || true
    fi

    # If a prior run committed a secret/credential, untrack it now (before the
    # change check) so the removal is captured as drift and goes out via a PR.
    untrack_secrets

    if [ "$conflict" = true ]; then
        write_status warning stash_conflict "kept live config where conflicting"
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
