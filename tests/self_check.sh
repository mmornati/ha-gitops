#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Anil Belur
##############################################################################
# Self-check for gitops_backup.sh — runs entirely locally (no GitHub calls):
#   1. bootstrap dry-run seeds .gitignore and reports status
#   2. real bootstrap pushes the initial import to a local bare "origin"
#   3. drift on a later run produces a backup branch (PR call fails soft,
#      status = warning:branch_pushed)
#   6+ token kept off disk, rolling PR branch, sign-off option, sync-only
#      mode, revert PRs, run lock, apply classification
##############################################################################
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${HERE}/../gitops_backup/rootfs/app/gitops_backup.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/config" "$TMP/data"
git init -q --bare "$TMP/origin.git"

options() { # dry_run [signoff]
  cat > "$TMP/data/options.json" <<EOF
{"github_repo":"local/test","github_token":"test-token","base_branch":"main",
 "branch_prefix":"auto-backup","dry_run":$1,
 "commit_name":"Test","commit_email":"test@localhost","signoff":${2:-true}}
EOF
}

# GITHUB_API points at a closed local port: PR calls fail fast and soft.
run() {
  OPTIONS="$TMP/data/options.json" CONFIG_DIR="$TMP/config" DATA_DIR="$TMP/data" \
    HOME="$TMP" GITHUB_API="http://127.0.0.1:9" LOCK_WAIT="${LOCK_WAIT:-30}" \
    bash "$SCRIPT" >/dev/null 2>&1 || true
}

backup_branches() {
  git -c safe.bareRepository=all -C "$TMP/origin.git" branch | grep -c "auto-backup/" || true
}

status() { cat "$TMP/config/.gitops_backup_status"; }

echo "config file content" > "$TMP/config/configuration.yaml"
echo "secret: real" > "$TMP/config/secrets.yaml"

# 1. dry-run bootstrap
options true
run
[ -f "$TMP/config/.gitignore" ] || { echo "FAIL: .gitignore not seeded"; exit 1; }
grep -q "^secrets.yaml$" "$TMP/config/.gitignore" || { echo "FAIL: secrets.yaml not ignored"; exit 1; }
[ "$(status)" = "success:dry_run:bootstrap" ] || { echo "FAIL: dry-run status = $(status)"; exit 1; }

# 2. real bootstrap against local bare origin (remote URL rewritten via insteadOf)
options false
HOME="$TMP" git config --global "url.$TMP/origin.git.insteadOf" \
  "https://github.com/local/test.git"
run
git -C "$TMP/config" log --oneline -1 | grep -q "initial" || { echo "FAIL: no bootstrap commit"; exit 1; }
git -c safe.bareRepository=all -C "$TMP/origin.git" rev-parse main >/dev/null || { echo "FAIL: main not pushed"; exit 1; }
# secrets must not be committed
if git -C "$TMP/config" ls-files | grep -q secrets.yaml; then
  echo "FAIL: secrets.yaml committed"; exit 1
fi

# 3. drift -> backup branch (PR API unreachable => warning:branch_pushed)
echo "changed" >> "$TMP/config/configuration.yaml"
echo "new file" > "$TMP/config/new_automation.yaml"
run
git -c safe.bareRepository=all -C "$TMP/origin.git" branch | grep -q "auto-backup/" || { echo "FAIL: no backup branch pushed"; exit 1; }
status | grep -q "warning:branch_pushed" || { echo "FAIL: drift status = $(status)"; exit 1; }
git -C "$TMP/config" branch --show-current | grep -q "^main$" || { echo "FAIL: not back on main"; exit 1; }
# Returning to main must not roll the live config back to the old main
grep -q "^changed$" "$TMP/config/configuration.yaml" \
  || { echo "FAIL: live config rolled back after backup"; exit 1; }
[ -f "$TMP/config/new_automation.yaml" ] \
  || { echo "FAIL: new live file deleted after backup"; exit 1; }

# 3b. The backup PR gets merged upstream; the next run pulls it and must find
#     no drift, no conflict, and the live files unchanged.
backup=$(git -c safe.bareRepository=all -C "$TMP/origin.git" branch \
  | grep -o 'auto-backup/[^ ]*' | tail -1)
git clone -q -b main "$TMP/origin.git" "$TMP/merge"
git -C "$TMP/merge" -c user.name=T -c user.email=t@localhost \
  merge -q --no-ff -m "Merge backup PR" "origin/$backup"
git -C "$TMP/merge" push -q origin main
run
[ "$(status)" = "success:no_changes:" ] || { echo "FAIL: post-merge status = $(status)"; exit 1; }
grep -q "^changed$" "$TMP/config/configuration.yaml" \
  || { echo "FAIL: live config lost after merged-PR sync"; exit 1; }
[ "$(git -C "$TMP/config" rev-parse HEAD)" = "$(git -C "$TMP/merge" rev-parse HEAD)" ] \
  || { echo "FAIL: main not synced to the merged PR"; exit 1; }
[ -z "$(git -C "$TMP/config" stash list)" ] || { echo "FAIL: stash left behind"; exit 1; }

# 3c. Stash conflict: upstream and the live config both changed the same file
#     (e.g. a HACS update on the box while a PR touched it). The run must keep
#     the live version, leave no unmerged index entries, and the next run must
#     not fail with "needs merge" / "could not write index".
git -C "$TMP/merge" pull -q origin main
echo "upstream edit" > "$TMP/merge/configuration.yaml"
git -C "$TMP/merge" -c user.name=T -c user.email=t@localhost \
  commit -q -am "upstream change"
git -C "$TMP/merge" push -q origin main
echo "live edit" > "$TMP/config/configuration.yaml"
run
grep -q "warning:stash_conflict" "$TMP/data/gitops_backup.log" \
  || { echo "FAIL: stash conflict not reported"; exit 1; }
grep -q "^live edit$" "$TMP/config/configuration.yaml" \
  || { echo "FAIL: live config not kept on stash conflict"; exit 1; }
[ -z "$(git -C "$TMP/config" ls-files -u)" ] \
  || { echo "FAIL: unmerged index entries left after stash conflict"; exit 1; }
sleep 1  # distinct auto-backup/<timestamp> branch name
run
status | grep -qE "^(success|warning:branch_pushed)" \
  || { echo "FAIL: run after stash conflict = $(status)"; exit 1; }
grep -q "^live edit$" "$TMP/config/configuration.yaml" \
  || { echo "FAIL: live config lost on the run after a conflict"; exit 1; }

# 3d. A repo already stuck with unmerged entries (left by an older version)
#     heals on the next run instead of failing forever.
blob=$(git -C "$TMP/config" hash-object -w configuration.yaml)
printf '100644 %s 2\tconfiguration.yaml\n100644 %s 3\tconfiguration.yaml\n' "$blob" "$blob" \
  | git -C "$TMP/config" update-index --index-info
[ -n "$(git -C "$TMP/config" ls-files -u)" ] || { echo "FAIL: test setup, no unmerged entry"; exit 1; }
sleep 1
run
[ -z "$(git -C "$TMP/config" ls-files -u)" ] \
  || { echo "FAIL: stuck unmerged index not cleared"; exit 1; }
status | grep -qvE "^error" || { echo "FAIL: stuck repo still errors = $(status)"; exit 1; }

# 4. Migration case: repo already had its own .gitignore lacking the app's
#    entries, and was never excluded. The app's own status file + log AND any
#    secrets/credentials must be kept out of the backup — even an already-
#    committed secret must be untracked (regression test for seed_gitignore
#    skip-when-.gitignore-exists + secret-leak on migrated repos).
sleep 1  # ensure a distinct auto-backup/<timestamp> branch name
rm -f "$TMP/config/.git/info/exclude"                 # never-excluded repo
printf 'secrets.yaml\n' > "$TMP/config/.gitignore"    # user ignore, no app/token entries
HOME="$TMP" git -C "$TMP/config" add .gitignore
HOME="$TMP" git -C "$TMP/config" commit -q -m "user gitignore"
echo "success:no_changes:" > "$TMP/config/.gitops_backup_status"
echo "log line" > "$TMP/config/gitops_backup.log"
echo "ya29.oauth-secret" > "$TMP/config/.google.token"  # untracked secret, not in user ignore
mkdir -p "$TMP/config/zigbee2mqtt"
echo "network_key: [1, 2]" > "$TMP/config/zigbee2mqtt/secret.yaml"  # Z2M secrets, not in user ignore
echo "PRIVATE KEY" > "$TMP/config/leaked.key"           # secret already committed by a prior run
HOME="$TMP" git -C "$TMP/config" add -f leaked.key
HOME="$TMP" git -C "$TMP/config" commit -q -m "oops: committed a key"
echo "more drift" >> "$TMP/config/configuration.yaml"
run
newbranch=$(git -c safe.bareRepository=all -C "$TMP/origin.git" branch \
  | grep -o 'auto-backup/[^ ]*' | tail -1)
tree=$(git -c safe.bareRepository=all -C "$TMP/origin.git" ls-tree -r --name-only "$newbranch")
if echo "$tree" | grep -qE '^\.gitops_backup_status$|^gitops_backup\.log$'; then
  echo "FAIL: app runtime files committed to backup branch"; exit 1
fi
if echo "$tree" | grep -qE '^\.google\.token$|^leaked\.key$|^secrets\.yaml$|^zigbee2mqtt/secret\.yaml$'; then
  echo "FAIL: a secret/credential was committed to the backup branch"; exit 1
fi
echo "$tree" | grep -q "^configuration.yaml$" || { echo "FAIL: real drift not backed up"; exit 1; }

# 5. Static wiring checks for apply_after_pull (no HA/Supervisor available here)
grep -q '"homeassistant_api": true' "${HERE}/../gitops_backup/config.json" \
  || { echo "FAIL: homeassistant_api not enabled in config.json"; exit 1; }
grep -q 'apply_after_pull' "${HERE}/../gitops_backup/config.json" \
  || { echo "FAIL: apply_after_pull option missing from config.json"; exit 1; }
grep -q 'homeassistant/reload_all' "$SCRIPT" \
  || { echo "FAIL: reload_all call missing from script"; exit 1; }
grep -q 'config/core/check_config' "$SCRIPT" \
  || { echo "FAIL: config check missing before apply"; exit 1; }

# 6. The token never lands in /config/.git/config, and a token left there by
#    an older version is scrubbed on the next run.
if grep -q "test-token" "$TMP/config/.git/config"; then
  echo "FAIL: token written to .git/config"; exit 1
fi
git -C "$TMP/config" remote set-url origin \
  "https://x-access-token:test-token@github.com/local/test.git"
run
if grep -q "test-token" "$TMP/config/.git/config"; then
  echo "FAIL: legacy token not scrubbed from .git/config"; exit 1
fi
[ -f "$TMP/data/status.json" ] || { echo "FAIL: status.json not written"; exit 1; }
jq -e '.state and .local_head' "$TMP/data/status.json" >/dev/null \
  || { echo "FAIL: status.json incomplete"; exit 1; }

# 7. Rolling PR: new drift goes onto the open backup branch, not a new one.
before=$(backup_branches)
open_branch=$(cut -f1 "$TMP/data/open_pr")
old_tip=$(git -c safe.bareRepository=all -C "$TMP/origin.git" rev-parse "$open_branch")
echo "rolling drift" > "$TMP/config/rolling.yaml"
sleep 1
run
[ "$(backup_branches)" = "$before" ] || { echo "FAIL: drift opened a new branch instead of rolling"; exit 1; }
new_tip=$(git -c safe.bareRepository=all -C "$TMP/origin.git" rev-parse "$open_branch")
[ "$old_tip" != "$new_tip" ] || { echo "FAIL: open backup branch not updated"; exit 1; }
[ "$(git -c safe.bareRepository=all -C "$TMP/origin.git" rev-parse "${new_tip}^")" = "$old_tip" ] \
  || { echo "FAIL: rolling push was not a fast-forward"; exit 1; }
git -c safe.bareRepository=all -C "$TMP/origin.git" ls-tree -r --name-only "$open_branch" \
  | grep -q "^rolling.yaml$" || { echo "FAIL: rolling drift missing from branch"; exit 1; }
git -C "$TMP/config" branch --show-current | grep -q "^main$" || { echo "FAIL: not back on main after rolling"; exit 1; }
# No new drift: nothing pushed, status says the PR already has it
sleep 1
run
[ "$(git -c safe.bareRepository=all -C "$TMP/origin.git" rev-parse "$open_branch")" = "$new_tip" ] \
  || { echo "FAIL: pushed with no new drift"; exit 1; }
status | grep -q "^success:pr_unchanged" || { echo "FAIL: unchanged status = $(status)"; exit 1; }

# 8. signoff: false really drops the Signed-off-by trailer
options false false
echo "no signoff" > "$TMP/config/nosign.yaml"
sleep 1
run
msg=$(git -c safe.bareRepository=all -C "$TMP/origin.git" log -1 --format=%B "$open_branch")
if echo "$msg" | grep -q "Signed-off-by"; then
  echo "FAIL: signoff=false still signs off"; exit 1
fi
options false

# 9. sync mode pulls upstream but leaves local drift alone
git -C "$TMP/merge" pull -q origin main
echo "upstream only" > "$TMP/merge/upstream.yaml"
git -C "$TMP/merge" add upstream.yaml
git -C "$TMP/merge" -c user.name=T -c user.email=t@localhost commit -q -m "upstream file"
git -C "$TMP/merge" push -q origin main
echo "sync drift" > "$TMP/config/syncdrift.yaml"
tip_before=$(git -c safe.bareRepository=all -C "$TMP/origin.git" rev-parse "$open_branch")
GITOPS_MODE=sync run
[ -f "$TMP/config/upstream.yaml" ] || { echo "FAIL: sync mode did not pull"; exit 1; }
[ "$(git -c safe.bareRepository=all -C "$TMP/origin.git" rev-parse "$open_branch")" = "$tip_before" ] \
  || { echo "FAIL: sync mode pushed drift"; exit 1; }
[ -f "$TMP/config/syncdrift.yaml" ] || { echo "FAIL: sync mode lost local drift"; exit 1; }
status | grep -q "^success:synced" || { echo "FAIL: sync status = $(status)"; exit 1; }

# 10. revert mode opens a revert branch from a worktree, /config untouched
upstream_sha=$(git -C "$TMP/merge" rev-parse HEAD)
GITOPS_MODE=revert REVERT_SHA="$upstream_sha" run
rb="auto-backup/revert-${upstream_sha:0:7}"
git -c safe.bareRepository=all -C "$TMP/origin.git" rev-parse -q --verify "$rb" >/dev/null \
  || { echo "FAIL: revert branch not pushed ($(status))"; exit 1; }
if git -c safe.bareRepository=all -C "$TMP/origin.git" ls-tree -r --name-only "$rb" | grep -q "^upstream.yaml$"; then
  echo "FAIL: revert branch still has the reverted file"; exit 1
fi
[ -f "$TMP/config/upstream.yaml" ] || { echo "FAIL: revert touched the live config"; exit 1; }
[ -z "$(git -C "$TMP/config" worktree list | sed 1d)" ] || { echo "FAIL: revert worktree left behind"; exit 1; }
GITOPS_MODE=revert REVERT_SHA="not-a-sha" run
status | grep -q "^error:revert" || { echo "FAIL: bad revert sha accepted"; exit 1; }

# 11. Only one run at a time
exec 8>"$TMP/data/gitops.lock"
flock 8
start=$(date +%s)
LOCK_WAIT=2 run
[ $(( $(date +%s) - start )) -ge 2 ] || { echo "FAIL: run did not wait for the lock"; exit 1; }
flock -u 8

# 12. Smart apply: changed paths map to the smallest action
classify() {
  GITOPS_SOURCE_ONLY=1 OPTIONS="$TMP/data/options.json" CONFIG_DIR="$TMP/config" \
    DATA_DIR="$TMP/data" bash -c ". '$SCRIPT'; classify_apply '$1'"
}
[ "$(classify automations.yaml)" = "automation/reload" ] || { echo "FAIL: classify automations"; exit 1; }
[ "$(classify scripts.yaml)" = "script/reload" ] || { echo "FAIL: classify scripts"; exit 1; }
[ "$(classify configuration.yaml)" = "restart" ] || { echo "FAIL: classify configuration.yaml"; exit 1; }
[ "$(classify custom_components/x/sensor.py)" = "restart" ] || { echo "FAIL: classify custom_components"; exit 1; }
[ "$(classify README.md)" = "none" ] || { echo "FAIL: classify README"; exit 1; }
[ "$(classify themes/dark.yaml)" = "frontend/reload_themes" ] || { echo "FAIL: classify themes"; exit 1; }

echo "OK: all self-checks passed"

