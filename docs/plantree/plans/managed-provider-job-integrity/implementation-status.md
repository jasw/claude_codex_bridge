# Managed Provider And Job Integrity Status

Date: 2026-07-21

## Current Phase

R8 stuck inbound detection has completed implementation and verification. This
atomic commit is selected by `Repair-Slice: R8`; no later row is marked active
until the commit exists and the worktree is clean.

## Next Target

After the R8 commit is created and verified clean, refresh the R9 upstream and
baseline, then freeze exact-job active-turn correction capability and refusal
semantics before production edits.

## Last Landed

R8 is selected by `Repair-Slice: R8`, with durable external evidence at
`/home/bfly/yunwei/test_ccb2/r8-orphaned-inbound-runtime-20260721-B1KYSq/r8-runtime-result.json`.
The immediately preceding verified commit is R7 `56f8dcda`
(`Repair-Slice: R7`, `feat: expose correlated execution phases`).

## Active TODO

1. Create the atomic R8 commit with its required selector and upstream trailer.
2. Verify the committed worktree is clean and record the exact hash at the R9
   preflight boundary.
3. Keep R9 waiting until that commit exists; do not infer provider correction
   support before its owning decision is frozen.

## Blocked By

No current blocker. R9's provider-native correction capability is an open
question owned by the next row, not permission to start it inside R8. Copilot
still requires its later queue row to freeze an authoritative entry-level
ownership schema and offline/no-login fixture; that work is pending, not
skipped.

## Last Verified

- R8's focused ProjectView, maintenance, trace, doctor, CLI, recovery, and
  service-graph gate passed `308` tests. Python compilation, static diff
  checks, Rust formatting, and all `79` Rust sidebar tests passed.
- The complete Python run passed `5340` tests with `2` skipped and `2`
  deselected in `975.68s`. The isolated `restart_replay_pass` scenario passed
  in `32.69s`; the other deselection is the adjudicated baseline lifecycle-
  stopping socket race.
- External real-Claude project
  `/home/bfly/yunwei/test_ccb2/r8-orphaned-inbound-runtime-20260721-B1KYSq`
  observed the exact non-terminal active lineage at a real idle prompt. Its
  first observation stayed `provider_idle_pending_terminal`; the unchanged
  second observation emitted `orphaned_active_inbound` with manual recovery
  recommendation and `automatic_action=none`. Trace and doctor rendered the
  same envelope.
- Job, runtime, attempt, execution, lease, inbox, mailbox, message, reply, and
  completion authority hashes were identical across the diagnostic reads.
  Candidate source hashes also matched before/after. Candidate `ccb_test kill`
  left the project unmounted with sockets and recorded processes absent.
- A separate external terminal-race run transitioned from pending-terminal to
  `terminal/hook_stop` before confirmation and emitted zero diagnostics.
- R8 compact evidence:
  [history/reviewed-repair-queue-evidence.md](history/reviewed-repair-queue-evidence.md#r8-stuck-inbound-detection).

Prior R3-R6 evidence remains indexed in
[history/reviewed-repair-queue-evidence.md](history/reviewed-repair-queue-evidence.md).
R11 provider-extension qualification remains in
[history/r11-provider-extension-validation-2026-07-20.md](history/r11-provider-extension-validation-2026-07-20.md).
