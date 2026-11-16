# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

People who run Home Assistant at home and keep its YAML configuration in a
GitHub repository. They are the administrator of their own box. They open the
panel in two situations: on a phone, in the Home Assistant app, right after a
notification (a backup PR was opened, a restart is required, a run failed), and
sometimes on a desktop while editing the configuration next to GitHub.

## Product Purpose

HA GitOps is a Home Assistant app that keeps `/config` and a GitHub repository
in sync through pull requests. Edits made on the box become a backup PR; a PR
merged on GitHub is pulled to the box and applied to the running Home
Assistant. Success: the user always knows whether the box and GitHub agree,
what is waiting for review, and whether anything needs them.

## Positioning

Nothing reaches the base branch without a pull request, and CI validates the
configuration (including a real Home Assistant boot) before merge. It is change
tracking with review, not a disaster-recovery backup.

## Operating Context

- Runs as a Home Assistant app; the panel is served through Home Assistant
  ingress, inside the Home Assistant frame, to administrators only.
- The other half of the workflow happens on GitHub (reviewing and merging PRs).
- Home Assistant entities, notifications and events mirror the same state.

## Capabilities and Constraints

- The panel is one self-contained HTML file: no build step, no external
  requests (fonts, scripts, images), vanilla JavaScript.
- Commands: sync, back up, dry run, pause/resume, auto-apply on/off, revert a
  commit (opens a PR), discard local changes, merge the backup PR, restart
  Home Assistant.
- State it shows: last run and result, box commit vs GitHub commit, drift
  file by file (in the open PR, not backed up yet, held back as secret-like),
  the open PR and its checks, restart required, history, log.
- The content of a held-back file is never sent to the panel.
- Terminology: "the box" (the Home Assistant machine), "backup PR", "base
  branch", "drift", "held back".

## Brand Commitments

- Name: HA GitOps. This is a fork (mmornati/ha-gitops) of askb/ha-gitops.
- The panel has its own identity rather than imitating Home Assistant cards,
  while staying calm enough to live inside the Home Assistant frame
  (confirmed by the owner, 2026-11-15).
- No logo or brand assets exist.

## Evidence on Hand

Real state comes from the app's own API. There are no screenshots, testimonials
or marketing assets, and none should be invented.

## Product Principles

- The box's live configuration is never rolled back or overwritten silently.
- Secrets never leave the box.
- Every destructive or outward action is explicit and confirmed.
- Say what happened and what is needed next, in plain words.

## Accessibility & Inclusion

Usable by keyboard and screen reader, readable in light and dark, and usable
one-handed on a phone.
