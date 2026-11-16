<!-- SPDX-License-Identifier: Apache-2.0 -->
# Security policy

## Supported versions

Only the latest release in the App store is supported. Update via
Settings → Apps → HA GitOps.

## Reporting a vulnerability

Use GitHub's private reporting:
<https://github.com/mmornati/ha-gitops/security/advisories/new>. Please do
not open a public issue for anything that could expose a user's GitHub
token or `secrets.yaml`. Problems in the backup engine that also exist
upstream are best reported to <https://github.com/askb/ha-gitops> as well.

## What the app protects

- The GitHub token is read from Supervisor-encrypted options and handed to
  git through an askpass helper and to `curl` through a header on a pipe.
  It is never written to `.git/config`, the log or a command line.
- `secrets.yaml` (and copies), `.storage/`, `.cloud/`, keys, tokens and
  the ESPHome Device Builder state are excluded through
  `.git/info/exclude` on every run and untracked if already committed.
- Files whose staged content looks like a secret are held back from every
  backup, the initial import included.
- The panel is reachable only through Home Assistant ingress. Discard,
  merge and restart are accepted from the panel only, never over MQTT.
- The webhook only acts on deliveries signed with `webhook_secret`.

See `gitops_backup/DOCS.md` for the full list.

## Automated checks

CI runs under harden-runner with SHA-pinned actions; zizmor audits the
workflows on every PR, CodeQL scans the Python and workflow code, Trivy
scans the built image, and OpenSSF Scorecard runs weekly. Results are in
the repository's Security tab.
