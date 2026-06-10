# Ideas Inbox

Date: 2026-05-25

## Promoted

- 2026-05-25: Redesign the public README for the v7 release line with new
  screenshots, demo videos, richer operation docs, and tmux onboarding for
  non-tmux users. Promoted to
  [readme-v7-redesign](../plans/readme-v7-redesign/README.md).
- 2026-06-10: Consider an external `ccb_self` maintenance heartbeat that
  periodically runs a bounded health tick, checks configured-agent task and
  queue status, escalates only when failures or stuck lineage are detected, and
  exits immediately when the project is idle. The heartbeat must remain outside
  `ccb_self` provider context and must not make `ccb_self` a daemon lifecycle
  authority. Promoted to
  [ccb-self-maintenance-heartbeat](../plans/ccb-self-maintenance-heartbeat/README.md).

## Inbox
