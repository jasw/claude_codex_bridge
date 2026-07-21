# Managed Provider And Job Integrity Status

Date: 2026-07-21

## Current Phase

R6 Kimi exact-session resume is a verified atomic commit on the serial closure
branch. R7 is the next unlocked row; it remains `waiting` until its phase and
evidence-precedence decision is frozen.

## Next Target

Freeze R7's additive execution-phase vocabulary, identity joins, contradictory
evidence policy, and client fallback contract before changing runtime schemas.

## Last Landed

R6 verified commit selector: `Repair-Slice: R6` (`fix: resume exact Kimi
sessions`).

## Active TODO

1. Resolve R7's open phase/schema question and record the decision.
2. Preserve contradictory/mismatched job-attempt-inbound-lease fixtures before
   changing PR265's execution-state model.
3. Implement the frozen field once across queue, CLI, ProjectView, sidebar,
   and mobile consumers with backward-compatible fallback.

## Blocked By

No current blocker. Copilot still requires its later queue row to freeze an
authoritative entry-level ownership schema and offline/no-login fixture; that
work is pending, not skipped.

## Last Verified

- R6 preserved behavioral gate: `3 failed` before production changes; exact
  restart selection, explicit-session precedence state, and native binding
  retention were absent.
- Focused Kimi session/launcher tests: `45 passed`; broader Kimi/native/restart
  gate: `120 passed`; expanded launch/runtime-binding integration: `193 passed`.
- Complete Python remainder: `5455 passed`, `2 skipped`, `2 deselected` in
  `936.97s`. The isolated `restart_replay_pass` scenario passed in `31.92s`;
  the other deselection is the already-adjudicated lifecycle-stopping socket
  race from the frozen baseline.
- Real Kimi 1.47.0 (`kimi-for-coding`) project
  `/home/bfly/yunwei/test_ccb2/r6-kimi-exact-runtime2-20260721-9hlXai`
  mounted `kimi1` and `kimi2` in one workdir. First launch was fresh. Their
  distinct native IDs survived CCB-controlled restart, the visible panes
  displayed those exact IDs, and continuation jobs returned only their hidden
  prior tokens: `ALPHA_7A21` and `BETA_9B34`. A later clean full-project
  remount repeated both exact UUIDs with exactly one selector per command and
  returned the same distinct tokens from the final candidate.
- Candidate tracked-diff and untracked-set hashes were identical before and
  after the mounted run. Candidate `ccb_test kill` left the project
  `unmounted`; both sockets and the recorded keeper PID were absent.
- R6 compact evidence:
  [history/reviewed-repair-queue-evidence.md](history/reviewed-repair-queue-evidence.md#r6-kimi-exact-session-resume).

Prior R3-R5 evidence remains indexed in
[history/reviewed-repair-queue-evidence.md](history/reviewed-repair-queue-evidence.md).
R11 provider-extension qualification remains in
[history/r11-provider-extension-validation-2026-07-20.md](history/r11-provider-extension-validation-2026-07-20.md).
