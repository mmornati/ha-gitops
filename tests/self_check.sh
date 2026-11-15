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
echo "api_key: abcdef1234567890" > "$TMP/config/rest.yaml"  # must not reach the base branch
run
if grep -q "^rest.yaml$" <<< "$(git -C "$TMP/config" ls-files)"; then
  echo "FAIL: a file with an inline API key was in the initial import"; exit 1
fi
rm -f "$TMP/config/rest.yaml"
grep -q "initial" <<< "$(git -C "$TMP/config" log --oneline -1)" || { echo "FAIL: no bootstrap commit"; exit 1; }
git -c safe.bareRepository=all -C "$TMP/origin.git" rev-parse main >/dev/null || { echo "FAIL: main not pushed"; exit 1; }
# secrets must not be committed
if grep -q secrets.yaml <<< "$(git -C "$TMP/config" ls-files)"; then
  echo "FAIL: secrets.yaml committed"; exit 1
fi

# 3. drift -> backup branch (PR API unreachable => warning:branch_pushed)
echo "changed" >> "$TMP/config/configuration.yaml"
echo "new file" > "$TMP/config/new_automation.yaml"
run
grep -q "auto-backup/" <<< "$(git -c safe.bareRepository=all -C "$TMP/origin.git" branch)" || { echo "FAIL: no backup branch pushed"; exit 1; }
status | grep -q "warning:branch_pushed" || { echo "FAIL: drift status = $(status)"; exit 1; }
grep -q "^main$" <<< "$(git -C "$TMP/config" branch --show-current)" || { echo "FAIL: not back on main"; exit 1; }
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

# 3b-1. Backups that add, rename or delete files. The live tree keeps the drift
#       after the backup, so the sync that pulls the merged PR stashes files the
#       pull brings back: `git stash pop` fails with "already exists, no
#       checkout" and, for a moved file, a rename/delete conflict. Nothing
#       differs, so this must not be reported as a conflict. Sync mode is the
#       one that shows it: in full mode the backup step overwrites the status.
merge_backup() {
  git -C "$TMP/merge" pull -q origin main
  git -C "$TMP/merge" fetch -q origin
  git -C "$TMP/merge" -c user.name=T -c user.email=t@localhost \
    merge -q --no-ff -m "Merge backup PR" "origin/$(cut -f1 "$TMP/data/open_pr")"
  git -C "$TMP/merge" push -q origin main
}
assert_settled() { # context
  [ -z "$(git -C "$TMP/config" stash list)" ] || { echo "FAIL: $1: stash left behind"; exit 1; }
  [ -z "$(git -C "$TMP/config" ls-files -u)" ] || { echo "FAIL: $1: unmerged index entries"; exit 1; }
}
mkdir -p "$TMP/config/helpers"
seq 1 200 > "$TMP/config/helpers/moved.py"
echo "old" > "$TMP/config/gone.yaml"
sleep 1
run
merge_backup
GITOPS_MODE=sync run
status | grep -q "^success:synced" \
  || { echo "FAIL: merged backup with new files reported = $(status)"; exit 1; }
[ -z "$(git -C "$TMP/config" status --porcelain)" ] \
  || { echo "FAIL: drift left after syncing the merged backup"; exit 1; }
assert_settled "new files"

mkdir -p "$TMP/config/client"
{ seq 1 200; echo "edited"; } > "$TMP/config/client/moved.py"
rm "$TMP/config/helpers/moved.py" "$TMP/config/gone.yaml"
echo "brand new" > "$TMP/config/brand_new.yaml"
sleep 1
run
merge_backup
warnings=$(grep -c "stash_conflict" "$TMP/data/gitops_backup.log" || true)
GITOPS_MODE=sync run
status | grep -q "^success:synced" \
  || { echo "FAIL: merged backup with a rename reported = $(status)"; exit 1; }
[ "$(grep -c "stash_conflict" "$TMP/data/gitops_backup.log" || true)" = "$warnings" ] \
  || { echo "FAIL: false stash_conflict logged for a merged rename"; exit 1; }
[ -f "$TMP/config/client/moved.py" ] || { echo "FAIL: moved file missing after a merged rename"; exit 1; }
[ ! -e "$TMP/config/helpers/moved.py" ] || { echo "FAIL: old path back after a merged rename"; exit 1; }
[ ! -e "$TMP/config/gone.yaml" ] || { echo "FAIL: deleted file back after a merged backup"; exit 1; }
grep -q "^brand new$" "$TMP/config/brand_new.yaml" || { echo "FAIL: new file wrong after a merged backup"; exit 1; }
[ -z "$(git -C "$TMP/config" status --porcelain)" ] \
  || { echo "FAIL: drift left after syncing a merged rename"; exit 1; }
assert_settled "rename"

# 3b-2. A new file edited between the backup and the merge really differs from
#       upstream. The edit must survive and be reported; other drift in the
#       same stash must survive too.
echo "v1" > "$TMP/config/window.yaml"
sleep 1
run
merge_backup
echo "v2 live edit" > "$TMP/config/window.yaml"
echo "unrelated" > "$TMP/config/unrelated.yaml"
echo "tracked drift" > "$TMP/config/brand_new.yaml"
GITOPS_MODE=sync run
status | grep -q "^warning:stash_conflict" \
  || { echo "FAIL: edited new file not reported = $(status)"; exit 1; }
grep -q "^v2 live edit$" "$TMP/config/window.yaml" \
  || { echo "FAIL: live edit lost on an edited new file"; exit 1; }
grep -q "^unrelated$" "$TMP/config/unrelated.yaml" \
  || { echo "FAIL: unrelated new file lost"; exit 1; }
grep -q "^tracked drift$" "$TMP/config/brand_new.yaml" \
  || { echo "FAIL: tracked drift lost"; exit 1; }
assert_settled "edited new file"
sleep 1
run
merge_backup
GITOPS_MODE=sync run
status | grep -q "^success:synced" || { echo "FAIL: not settled after 3b-2 = $(status)"; exit 1; }

# 3b-3. A live file that cannot be restored stays in the stash: dropping it
#       would lose the only copy. Make its restore fail with a git wrapper.
echo "v1" > "$TMP/config/stuck.yaml"
sleep 1
run
merge_backup
echo "v2 only copy" > "$TMP/config/stuck.yaml"
mkdir -p "$TMP/gitshim"
cat > "$TMP/gitshim/git" <<EOF
#!/bin/sh
case "\$*" in *checkout*"-- stuck.yaml") echo "simulated failure" >&2; exit 1 ;; esac
exec "$(command -v git)" "\$@"
EOF
chmod +x "$TMP/gitshim/git"
PATH="$TMP/gitshim:$PATH" GITOPS_MODE=sync run
status | grep -q "^warning:stash_conflict:could not restore" \
  || { echo "FAIL: unrestored file not reported = $(status)"; exit 1; }
[ "$(git -C "$TMP/config" show 'stash@{0}^3:stuck.yaml')" = "v2 only copy" ] \
  || { echo "FAIL: stash does not hold the unrestored file"; exit 1; }
[ -z "$(git -C "$TMP/config" ls-files -u)" ] \
  || { echo "FAIL: unmerged entries after an unrestored file"; exit 1; }
git -C "$TMP/config" stash clear
[ -z "$(git -C "$TMP/config" status --porcelain)" ] \
  || { echo "FAIL: test setup, tree not clean after 3b-3"; exit 1; }

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

# 4b. Backup/editor copies of secrets.yaml are the easiest way to leak a real
#     secret (a `secrets.yaml.bak-<date>` left by a manual edit), and ESPHome
#     Device Builder keeps a key under esphome/. None of them may be pushed.
cp "$TMP/config/secrets.yaml" "$TMP/config/secrets.yaml.bak-20260101"
cp "$TMP/config/secrets.yaml" "$TMP/config/secrets.yaml.bak"
echo "secret: old" > "$TMP/config/automations.yaml.orig"
echo "registry" > "$TMP/config/entity_registry.bak.1793051273"
mkdir -p "$TMP/config/esphome"
head -c 32 /dev/urandom > "$TMP/config/esphome/.device-builder-peer-link-key.bin"
echo "esphome: {name: plug}" > "$TMP/config/esphome/plug.yaml"
# A tracked template must NOT be treated as a secret copy
echo "api_key: CHANGE_ME" > "$TMP/config/secrets.yaml.example"
HOME="$TMP" git -C "$TMP/config" add secrets.yaml.example
HOME="$TMP" git -C "$TMP/config" commit -q -m "add secrets template"
echo "bak drift" >> "$TMP/config/configuration.yaml"
run
if grep -q "Untracking.*secrets.yaml.example" "$TMP/data/gitops_backup.log"; then
  echo "FAIL: secrets.yaml.example template was untracked as a secret"; exit 1
fi
newbranch=$(git -c safe.bareRepository=all -C "$TMP/origin.git" branch \
  | grep -o 'auto-backup/[^ ]*' | tail -1)
tree=$(git -c safe.bareRepository=all -C "$TMP/origin.git" ls-tree -r --name-only "$newbranch")
if echo "$tree" | grep -qE 'secrets\.yaml\.bak|\.orig$|\.bak\.[0-9]+$|device-builder'; then
  echo "FAIL: a secrets copy or the ESPHome key was committed to the backup branch"; exit 1
fi
echo "$tree" | grep -q "^secrets.yaml.example$" \
  || { echo "FAIL: secrets.yaml.example template dropped from the backup"; exit 1; }
echo "$tree" | grep -q "^esphome/plug.yaml$" \
  || { echo "FAIL: ordinary ESPHome config not backed up"; exit 1; }
[ -f "$TMP/config/secrets.yaml.bak-20260101" ] \
  || { echo "FAIL: the secrets copy was removed from the box"; exit 1; }

# 4c. Content scan: a file with a literal credential is held back, the rest
#     of the drift still goes out, and the value never reaches the log.
printf 'mqtt:\n  password: SuperSecretPassw0rd\n' > "$TMP/config/mqtt.yaml"
printf 'mqtt:\n  password: !secret mqtt_password\n' > "$TMP/config/mqtt_ok.yaml"
# A name with a tab and a trailing blank must be held back all the same
odd="$TMP/config/$(printf 'odd\tname ').yaml"
echo "api_key: abcdef1234567890" > "$odd"
run
tree=$(git -c safe.bareRepository=all -C "$TMP/origin.git" ls-tree -r --name-only "$newbranch")
if echo "$tree" | grep -q "^mqtt.yaml$"; then
  echo "FAIL: a file with an inline password was pushed"; exit 1
fi
if echo "$tree" | grep -q "odd"; then
  echo "FAIL: a held file with an unusual name was pushed"; exit 1
fi
rm -f "$odd"
echo "$tree" | grep -q "^mqtt_ok.yaml$" \
  || { echo "FAIL: a file using !secret was held back"; exit 1; }
[ -f "$TMP/config/mqtt.yaml" ] || { echo "FAIL: held file removed from the box"; exit 1; }
grep -q "inline credential.*mqtt.yaml.*held back" "$TMP/data/gitops_backup.log" \
  || { echo "FAIL: held file not logged"; exit 1; }
if grep -q "SuperSecretPassw0rd" "$TMP/data/gitops_backup.log"; then
  echo "FAIL: the secret value was written to the log"; exit 1
fi
# Nothing but the held file left to back up
run
[ "$(status)" = "warning:secrets_held:1 files" ] \
  || { echo "FAIL: held-only run status = $(status)"; exit 1; }
rm -f "$TMP/config/mqtt.yaml"

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
grep -q "^rolling.yaml$" <<< "$(git -c safe.bareRepository=all -C "$TMP/origin.git" ls-tree -r --name-only "$open_branch")" \
  || { echo "FAIL: rolling drift missing from branch"; exit 1; }
grep -q "^main$" <<< "$(git -C "$TMP/config" branch --show-current)" || { echo "FAIL: not back on main after rolling"; exit 1; }
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
if grep -q "^upstream.yaml$" <<< "$(git -c safe.bareRepository=all -C "$TMP/origin.git" ls-tree -r --name-only "$rb")"; then
  echo "FAIL: revert branch still has the reverted file"; exit 1
fi
[ -f "$TMP/config/upstream.yaml" ] || { echo "FAIL: revert touched the live config"; exit 1; }
[ -z "$(git -C "$TMP/config" worktree list | sed 1d)" ] || { echo "FAIL: revert worktree left behind"; exit 1; }
GITOPS_MODE=revert REVERT_SHA="not-a-sha" run
status | grep -q "^error:revert" || { echo "FAIL: bad revert sha accepted"; exit 1; }

# 11. Only one run at a time. The app image ships BusyBox flock, which has
#     no -w: run with a flock that rejects it, like BusyBox does.
mkdir -p "$TMP/bin"
real_flock=$(command -v flock)
cat > "$TMP/bin/flock" <<EOF
#!/bin/sh
for a in "\$@"; do case "\$a" in -w|--timeout*|-E|--conflict-exit-code*) echo "flock: unrecognized option" >&2; exit 1 ;; esac; done
exec "$real_flock" "\$@"
EOF
chmod +x "$TMP/bin/flock"
exec 8>"$TMP/data/gitops.lock"
flock 8
start=$(date +%s)
PATH="$TMP/bin:$PATH" LOCK_WAIT=2 run
[ $(( $(date +%s) - start )) -ge 2 ] || { echo "FAIL: run did not wait for the lock"; exit 1; }
grep -q "holds the lock after 2s" "$TMP/data/gitops_backup.log" \
  || { echo "FAIL: locked run did not report the lock"; exit 1; }
flock -u 8
# With the lock free, a run under BusyBox-style flock must go through
echo "after lock" > "$TMP/config/afterlock.yaml"
sleep 1
PATH="$TMP/bin:$PATH" run
status | grep -qvE "^error" || { echo "FAIL: run with BusyBox-style flock = $(status)"; exit 1; }
[ "$(stat -c %Y "$TMP/data/status.json")" -ge "$start" ] \
  || { echo "FAIL: run with BusyBox-style flock did not run"; exit 1; }

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
[ "$(classify dashboards/main.yaml)" = "dashboards" ] || { echo "FAIL: classify dashboards"; exit 1; }
[ "$(classify ui-lovelace.yaml)" = "dashboards" ] || { echo "FAIL: classify ui-lovelace"; exit 1; }
[ "$(classify esphome/plug.yaml)" = "none" ] || { echo "FAIL: classify esphome"; exit 1; }
[ "$(classify zigbee2mqtt/configuration.yaml)" = "none" ] || { echo "FAIL: classify zigbee2mqtt"; exit 1; }

# 13. A backup that touches thousands of files (an integration removed or
#     updated) still opens its PR. The body used to go through argv, and a
#     ~200 KB file list failed with "jq: Argument list too long".
big="$TMP/big"
git init -q -b main "$big"
mkdir -p "$big/custom_components/hacs/frontend"
for i in $(seq 1 3000); do
  echo "$i" > "$big/custom_components/hacs/frontend/chunk.$i.long-hashed-name.js.gz"
done
git -C "$big" add -A
git -C "$big" -c user.name=T -c user.email=t@localhost commit -q -m base
git -C "$big" rm -rq custom_components/hacs
echo "x: 1" > "$big/automations.yaml"
git -C "$big" add -A
git -C "$big" -c user.name=T -c user.email=t@localhost commit -q -m drift
mkdir -p "$TMP/curlbin"
printf '#!/bin/sh\ncat > "%s/posted.json"\necho '"'"'{"html_url":"https://example/pr/1","number":1}'"'"'\n' \
  "$TMP" > "$TMP/curlbin/curl"
chmod +x "$TMP/curlbin/curl"
source_run() { # shell code, run in $big with the engine sourced
  (cd "$big" && PATH="$TMP/curlbin:$PATH" GITOPS_SOURCE_ONLY=1 \
    OPTIONS="$TMP/data/options.json" CONFIG_DIR="$TMP/config" DATA_DIR="$TMP/data" \
    bash -c ". '$SCRIPT'; $1")
}
# shellcheck disable=SC2016 # expanded by the inner bash
pr_out=$(source_run 'create_pull_request auto-backup/big "chore: big" "$(pr_body HEAD~1 HEAD)"')
[ "$pr_out" = "$(printf 'https://example/pr/1\t1')" ] \
  || { echo "FAIL: big PR not created (got: $pr_out)"; exit 1; }
blen=$(jq -r '.body | length' "$TMP/posted.json")
[ "$blen" -le 65536 ] || { echo "FAIL: PR body is $blen chars, over the GitHub limit"; exit 1; }
posted_body=$(jq -r .body "$TMP/posted.json")
grep -q "more files, not listed" <<< "$posted_body" \
  || { echo "FAIL: truncated PR body has no 'more files' note"; exit 1; }
# shellcheck disable=SC2016 # expanded by the inner bash
grep -qF '`automations.yaml` (added)' <<< "$posted_body" \
  || { echo "FAIL: big PR body dropped the automations group"; exit 1; }
[ "$(jq -r .head "$TMP/posted.json")" = "auto-backup/big" ] || { echo "FAIL: big PR head"; exit 1; }
# shellcheck disable=SC2016 # expanded by the inner bash
source_run 'update_pull_request 1 "chore: big" "$(pr_body HEAD~1 HEAD)"'
[ "$(jq -r .title "$TMP/posted.json")" = "chore: big" ] || { echo "FAIL: big PR update"; exit 1; }
if grep -q "more files" <<< "$(source_run 'pr_body HEAD~1 HEAD~1')"; then
  echo "FAIL: empty diff says files were skipped"; exit 1
fi

# ---------------------------------------------------------------------------
# 14+ run in fresh boxes, with a fake curl standing in for Home Assistant and
# the GitHub API. It logs its whole command line, so a token passed as an
# argument would show up in the log.
# ---------------------------------------------------------------------------
FAKEBIN="$TMP/fakebin"
mkdir -p "$FAKEBIN"
cat > "$FAKEBIN/curl" <<'EOF'
#!/bin/bash
method=GET url="" prev="" has_body=false
for a in "$@"; do
  [ "$prev" = "-X" ] && method="$a"
  [ "$a" = "--data-binary" ] && has_body=true
  prev="$a" url="$a"
done
[ "$has_body" = true ] && cat > /dev/null
echo "$method $url :: $*" >> "$FAKE_LOG"
case "$url" in
  */config/core/check_config) echo "{\"result\":\"$(cat "$FAKE_CHECK")\",\"errors\":\"boom\"}" ;;
  */pulls) if [ "$method" = POST ]; then echo '{"html_url":"https://example/pr/1","number":1}'; else exit 22; fi ;;
  */pulls\?*) exit 22 ;;
  *) echo '{}' ;;
esac
EOF
chmod +x "$FAKEBIN/curl"

brun() {
  OPTIONS="$BOX/data/options.json" CONFIG_DIR="$BOX/config" DATA_DIR="$BOX/data" \
    HOME="$BOX/home" PATH="$FAKEBIN:$PATH" FAKE_LOG="$BOX/calls.log" FAKE_CHECK="$BOX/check" \
    SUPERVISOR_TOKEN=sup-token LOCK_WAIT=30 bash "$SCRIPT" >/dev/null 2>&1 || true
}
newbox() { # name [extra options, as JSON members]
  BOX="$TMP/$1"
  mkdir -p "$BOX/config" "$BOX/data" "$BOX/home"
  git init -q --bare -b main "$BOX/origin.git"
  HOME="$BOX/home" git config --global "url.$BOX/origin.git.insteadOf" \
    "https://github.com/local/test.git"
  cat > "$BOX/data/options.json" <<EOF
{"github_repo":"local/test","github_token":"test-token","base_branch":"main",
 "branch_prefix":"auto-backup","dry_run":false,"commit_name":"Test",
 "commit_email":"test@localhost","signoff":true${2:+,$2}}
EOF
  echo valid > "$BOX/check"
  : > "$BOX/calls.log"
  printf 'homeassistant:\n  name: Home\n' > "$BOX/config/configuration.yaml"
  echo "[]" > "$BOX/config/automations.yaml"
  echo "wifi: abc" > "$BOX/config/secrets.yaml"
  brun
  git clone -q "$BOX/origin.git" "$BOX/dev"
}
devpush() { # file content — a change merged on GitHub
  git -C "$BOX/dev" pull -q origin main
  printf '%s\n' "$2" > "$BOX/dev/$1"
  git -C "$BOX/dev" add -A
  git -C "$BOX/dev" -c user.name=Dev -c user.email=dev@localhost commit -q -m "change $1"
  git -C "$BOX/dev" push -q origin main
}
bstatus() { jq -r "$1" "$BOX/data/status.json"; }
ogit() { git -c safe.bareRepository=all -C "$BOX/origin.git" "$@"; }
calls() { grep -c "$1" "$BOX/calls.log" || true; }

# 14. A dry run pulls merged changes without applying them. The next real run
#     must still apply them, once.
newbox apply
devpush automations.yaml "[{alias: merged}]"
DRY_RUN_OVERRIDE=true GITOPS_MODE=sync brun
grep -q merged "$BOX/config/automations.yaml" || { echo "FAIL: dry run did not pull"; exit 1; }
[ "$(calls services/automation/reload)" = 0 ] || { echo "FAIL: dry run applied changes"; exit 1; }
GITOPS_MODE=sync brun
[ "$(calls services/automation/reload)" = 1 ] \
  || { echo "FAIL: changes pulled by a dry run were never applied"; exit 1; }
[ "$(bstatus .applied)" = "automation.reload" ] || { echo "FAIL: applied = $(bstatus .applied)"; exit 1; }
GITOPS_MODE=sync brun
[ "$(calls services/automation/reload)" = 1 ] || { echo "FAIL: changes applied twice"; exit 1; }
if grep -qE "test-token|sup-token" "$BOX/calls.log"; then
  echo "FAIL: a token was passed to curl on the command line"; exit 1
fi

# 14b. A run that was killed can leave HEAD on a backup branch: the next run
#      puts it back on the base branch.
git -C "$BOX/config" symbolic-ref HEAD refs/heads/auto-backup/killed
GITOPS_MODE=sync brun
[ "$(git -C "$BOX/config" symbolic-ref HEAD)" = "refs/heads/main" ] \
  || { echo "FAIL: HEAD left on a backup branch"; exit 1; }

# 15. on_invalid_config: revert_pr. Pulled changes that fail the config check
#     are not applied, are reported once, and get a PR that reverts them.
newbox invalid '"on_invalid_config":"revert_pr"'
devpush configuration.yaml "homeassistant: {name: Broken}"
echo invalid > "$BOX/check"
GITOPS_MODE=sync brun
[ "$(bstatus .applied)" = "config_invalid" ] || { echo "FAIL: invalid config applied = $(bstatus .applied)"; exit 1; }
post=$(git -C "$BOX/config" rev-parse main)
rb="auto-backup/revert-${post:0:7}"
ogit rev-parse -q --verify "$rb" >/dev/null || { echo "FAIL: no revert branch for the invalid config"; exit 1; }
grep -q "name: Home" <<< "$(ogit show "$rb:configuration.yaml")" \
  || { echo "FAIL: revert branch does not restore the old config"; exit 1; }
[ "$(calls 'POST .*/pulls ')" = 1 ] || { echo "FAIL: revert PR not opened"; exit 1; }
notes=$(calls persistent_notification)
GITOPS_MODE=sync brun
[ "$(calls persistent_notification)" = "$notes" ] || { echo "FAIL: invalid config reported twice"; exit 1; }
[ "$(calls 'POST .*/pulls ')" = 1 ] || { echo "FAIL: second revert PR opened"; exit 1; }
echo valid > "$BOX/check"
GITOPS_MODE=sync brun
grep -q restart_required <<< "$(bstatus .applied)" || { echo "FAIL: fixed config not applied = $(bstatus .applied)"; exit 1; }
[ ! -f "$BOX/data/invalid_head" ] || { echo "FAIL: invalid marker not cleared"; exit 1; }

# 16. The base branch moves on while a backup PR is open: the next backup is
#     a merge commit, so the PR still shows only the drift.
newbox pr
echo "d1" > "$BOX/config/d1.yaml"
brun
[ "$(bstatus .detail)" = "pr_created" ] || { echo "FAIL: PR not created = $(bstatus .detail)"; exit 1; }
prb=$(cut -f1 "$BOX/data/open_pr")
devpush upstream.yaml "from: github"
echo "d2" > "$BOX/config/d2.yaml"
brun
[ "$(ogit rev-list --parents -n 1 "$prb" | wc -w | tr -d ' ')" = 3 ] \
  || { echo "FAIL: backup on a moved base is not a merge commit"; exit 1; }
[ "$(ogit diff --name-only "main...$prb" | sort | tr '\n' ' ')" = "d1.yaml d2.yaml " ] \
  || { echo "FAIL: PR diff is not limited to the drift: $(ogit diff --name-only "main...$prb" | tr '\n' ' ')"; exit 1; }
grep -q "Signed-off-by: Test <test@localhost>" <<< "$(ogit log -1 --format=%B "$prb")" \
  || { echo "FAIL: merge commit lost the sign-off"; exit 1; }

# 17. A secret-like file that an earlier version already put into the open PR
#     is taken out of it by the next backup.
git -C "$BOX/dev" fetch -q origin "$prb"
git -C "$BOX/dev" checkout -q -b pr FETCH_HEAD
printf 'mqtt:\n  password: SuperSecretPassw0rd\n' > "$BOX/dev/mqtt.yaml"
git -C "$BOX/dev" add -A
git -C "$BOX/dev" -c user.name=Dev -c user.email=dev@localhost commit -q -m "old backup with a secret"
git -C "$BOX/dev" push -q origin "pr:$prb"
git -C "$BOX/dev" checkout -q main
printf 'mqtt:\n  password: SuperSecretPassw0rd\n' > "$BOX/config/mqtt.yaml"
brun
if grep -q "^mqtt.yaml$" <<< "$(ogit ls-tree -r --name-only "$prb")"; then
  echo "FAIL: a held file stayed in the open PR"; exit 1
fi
[ -f "$BOX/config/mqtt.yaml" ] || { echo "FAIL: held file removed from the box"; exit 1; }
[ "$(bstatus '.held[0]')" = "mqtt.yaml" ] || { echo "FAIL: held list = $(bstatus .held)"; exit 1; }

# 18. Discard: /config goes back to the base branch. Ignored and held files
#     stay, a safety copy is kept, and the open backup PR is closed.
echo "[{alias: local}]" > "$BOX/config/automations.yaml"
rm "$BOX/config/upstream.yaml"
DRY_RUN_OVERRIDE=true GITOPS_MODE=discard brun
[ "$(bstatus .detail)" = "dry_run" ] && [ -f "$BOX/config/d1.yaml" ] \
  || { echo "FAIL: dry-run discard changed the box"; exit 1; }
GITOPS_MODE=discard brun
[ "$(bstatus .detail)" = "discarded" ] || { echo "FAIL: discard status = $(bstatus .detail)"; exit 1; }
if [ -f "$BOX/config/d1.yaml" ] || [ -f "$BOX/config/d2.yaml" ]; then
  echo "FAIL: discard left new files behind"; exit 1
fi
[ "$(cat "$BOX/config/automations.yaml")" = "[]" ] || { echo "FAIL: discard kept a local edit"; exit 1; }
[ -f "$BOX/config/upstream.yaml" ] || { echo "FAIL: discard did not restore a deleted file"; exit 1; }
[ -f "$BOX/config/mqtt.yaml" ] || { echo "FAIL: discard removed a held file"; exit 1; }
[ -f "$BOX/config/secrets.yaml" ] || { echo "FAIL: discard removed secrets.yaml"; exit 1; }
[ "$(git -C "$BOX/config" status --porcelain)" = "?? mqtt.yaml" ] \
  || { echo "FAIL: after discard: $(git -C "$BOX/config" status --porcelain)"; exit 1; }
safety=$(git -C "$BOX/config" for-each-ref --format='%(refname)' refs/gitops/discarded | tail -1)
git -C "$BOX/config" cat-file -e "${safety}:d1.yaml" \
  || { echo "FAIL: no safety copy of the discarded files"; exit 1; }
if ogit rev-parse -q --verify "$prb" >/dev/null; then
  echo "FAIL: backup branch still on GitHub after discard"; exit 1
fi
[ "$(calls 'PATCH .*/pulls/1 ')" -ge 1 ] || { echo "FAIL: backup PR not closed"; exit 1; }
[ ! -f "$BOX/data/open_pr" ] || { echo "FAIL: open PR state kept after discard"; exit 1; }
[ "$(calls services/automation/reload)" -ge 1 ] || { echo "FAIL: discard did not reload HA"; exit 1; }
brun
[ "$(bstatus .detail)" = "secrets_held" ] || { echo "FAIL: after discard = $(bstatus .detail)"; exit 1; }

echo "OK: all self-checks passed"

