# Managed Provider And Job Integrity Open Questions

Date: 2026-07-21

These questions do not block recording the roadmap. Each must be resolved
before production implementation of its owning slice starts.

1. **R11:** For Copilot, which entry-level ownership model can project installed
   plugins and marketplaces from its mixed config without copying or
   overwriting credentials, sessions, permissions, and local plugin data?

## Resolved

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
