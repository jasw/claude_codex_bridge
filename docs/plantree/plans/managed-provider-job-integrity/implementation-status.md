# Managed Provider And Job Integrity Status

Date: 2026-07-21

## Current Phase

R9 active-job correction capability is fully verified in the atomic commit
selected by `Repair-Slice: R9`. R8 remains the clean predecessor at
`e937aa99b2586565a33638818867b3f425cba2f2`; `origin/main` remains
`aed27abf8899bd1d3ce72d08bb9133e3980f19ba` and is its ancestor. R12 is the
next serial row and is ready only after this commit leaves a clean worktree.

## Next Target

Start R12 by inventorying every remaining `allow_unmarked_replace=True` call
site, freezing generic projected-asset ownership proof, and preserving foreign
or user-owned state before changing replacement behavior.

## Last Landed

R9 is selected by commit subject `feat: steer exact active jobs` and trailer
`Repair-Slice: R9`. Durable external evidence is at
`/home/bfly/yunwei/test_ccb2/r9-active-followup-real-20260721/r9-runtime-result.json`.
The clean predecessor is R8 `e937aa99b2586565a33638818867b3f425cba2f2`.

## Active TODO

1. Load the R12 ownership inventory and contracts without starting R11-C.
2. Reproduce unmarked replacement risk and freeze marker-first proof for each
   retained generic replacement.
3. Complete R12 focused/full/real gates and its own atomic commit before
   unlocking Copilot.

## Blocked By

No current blocker. Copilot still requires its later queue row to freeze an
authoritative entry-level ownership schema and offline/no-login fixture; that
work is pending, not skipped.

## Last Verified

- Exact-job, restart replay, FIFO, ambiguity, terminal/cancel race, CLI, trace,
  managed app-server, short-socket, and stop-flow gates passed, including the
  final `41`-test cleanup/follow-up gate. Python compilation and
  `git diff --check` passed.
- The final complete Python run passed `5518` tests with `2` skipped and no
  deselections in `1043.10s`. No Rust/sidebar/mobile schema or consumer changed
  in R9.
- In the external real-provider project, Codex `0.144.6` model
  `gpt-5.6-terra` steered exact job `job_861c7eecd75f` and bound turn
  `019f843d-e252-7591-9656-2072d81bf287` through `turn/steer`; the same single
  job/attempt completed with reply `R9_CORRECTED`. A terminal follow-up returned
  `too_late` and left the provider session hash and line count unchanged.
- Claude `2.1.206` model `deepseek-v4-pro` explicitly rejected correction of
  active job `job_4b8805deeddc` with
  `claude_tui_missing_atomic_active_turn_precondition`; the correction appeared
  in neither its provider session nor pane, and the original job completed
  `CLAUDE_DONE`.
- An overlong Codex app-server socket first reproduced `SUN_LEN` and local
  fallback. The bounded socket fix used a 59-byte owned path. Final project
  stop/kill removed its socket, pid, and remote marker even after forced bridge
  termination; the project was unmounted and the complete candidate digest
  remained `52e6d46bdc2e8bbc899b260a9a965a0c3c306cfe4d09bf2b41fbb0d86c220475`.
- R9 compact evidence:
  [history/reviewed-repair-queue-evidence.md](history/reviewed-repair-queue-evidence.md#r9-active-job-correction-capability).

Prior R3-R6 evidence remains indexed in
[history/reviewed-repair-queue-evidence.md](history/reviewed-repair-queue-evidence.md).
R11 provider-extension qualification remains in
[history/r11-provider-extension-validation-2026-07-20.md](history/r11-provider-extension-validation-2026-07-20.md).
