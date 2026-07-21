# Managed Provider And Job Integrity Open Questions

Date: 2026-07-21

These questions do not block recording the roadmap. Each must be resolved
before production implementation of its owning slice starts.

R11-C has no unresolved question and is verified. R10 disposition questions
remain gated on the integrated current-main matrix.

## Resolved

- **R11-C:**
  [Decision 008](decisions/008-copilot-entry-owned-plugin-seed.md) projects
  only allowlisted `installedPlugins` entries and their exact local installed
  trees, with per-entry ownership, rollback, cache isolation, and no auth,
  settings, permission, session, marketplace-cache, or plugin-data copy. The
  offline Copilot CLI fixture discovered both agent-local copies, and local
  tree divergence transferred ownership without source mutation.
- **R12:**
  [Decision 007](decisions/007-marker-first-projected-asset-ownership.md)
  requires a valid same-label schema-v1 CCB marker for replacement or cleanup.
  The sole markerless migration adopts an exact source symlink without
  replacing it. Unmarked directories and foreign/malformed markers are always
  preserved, and the legacy bypass no longer grants authority.
- **R9:** [Decision 006](decisions/006-exact-active-job-followup.md) qualifies
  only a provider primitive that atomically checks the exact active turn and
  accepts a durable idempotency identity. Managed Codex supports it when its
  visible TUI shares CCB's app-server and `turn/steer` uses
  `expectedTurnId`; legacy/local Codex panes and current Claude panes refuse
  explicitly. Pane dispatch, queued-command evidence, cancel-and-resubmit,
  provider substitution, and hidden retries do not qualify.
