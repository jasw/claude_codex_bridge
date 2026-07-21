# Managed Provider And Job Integrity Status

Date: 2026-07-21

## Current Phase

R5 Claude queued-prompt activation is verified on the serial closure branch.
No repair slice is currently `in_progress`; R6 is the next eligible row and
every later row remains locked.

## Next Target

Freeze R6's exact Kimi session ownership, missing-session behavior, and native
capability matrix before changing launcher code.

## Last Landed

R5 verified commit selector: `Repair-Slice: R5` (`fix: bind Claude queued
prompts to activation`).

## Active TODO

1. Refresh PR258 and current-main preflight state without advancing the queue.
2. Resolve which CCB-owned record stores Kimi's exact native session ID and
   what missing/corrupt identity does.
3. Freeze first-launch, explicit-flag, same-workdir isolation, restart, and
   clear/reset counterexamples before R6 production edits.

## Blocked By

No current blocker. Copilot still requires its later queue row to freeze an
authoritative entry-level ownership schema and offline/no-login fixture; that
work is pending, not skipped.

## Last Verified

- R5 preserved counterexample gate: `7 failed` before the runtime repair.
- Claude-specific tests: `98 passed`; integrated Claude/execution/dispatcher
  gate: `260 passed`; cumulative R11/R3/R4 gate: `555 passed`.
- The complete Python run reached `5269 passed`, `2 skipped`, and the one
  adjudicated socket-race deselection before an unrelated restart-replay smoke
  timing miss. That exact smoke case passed alone in `33.09s`; the complete
  remainder then passed `5269` tests with `2 skipped` and `2 deselected`.
- Real Claude Code 2.1.206 (`DeepSeek-V4-pro`) project
  `/home/bfly/yunwei/test_ccb2/r5-claude-queue-runtime-20260721-RyGaHI`
  queued `job_aadf1ff01a30` behind a running 25-second tool turn. It emitted
  one exact activation anchor and one reply, `NEW_QUEUED_SENTINEL_2`, with no
  old-turn sentinel.
- Candidate tracked-diff and untracked-set hashes were identical before and
  after the mounted run. Candidate `ccb_test kill` left the project
  `unmounted`; both sockets and the recorded keeper, daemon, and Claude PIDs
  were absent.
- R5 evidence:
  [history/reviewed-repair-queue-evidence.md](history/reviewed-repair-queue-evidence.md#r5-claude-queued-prompt-activation).

- R4 preserved counterexample gate: `5 failed, 1 passed` before the runtime
  repair.
- R4 dispatcher integration: `83 passed`; ProjectView: `82 passed`; cumulative
  R11/R3 gate: `300 passed`.
- Complete Python remainder: `5261 passed`, `2 skipped`, `1 deselected`. The
  sole deselection was the lifecycle-stopping socket race reproduced at the
  same line on current `origin/main`.
- External fake-provider project
  `/home/bfly/yunwei/test_ccb2/r4-cancel-runtime-20260721-fkyoH9` proved a
  cancelled chain child reached one `done` callback edge and parent
  continuation without restart, while an empty ordinary cancel kept caller
  depth and pending replies at zero.
- Candidate diff and untracked-set hashes were identical before and after the
  external run. Candidate `ccb_test kill` left the project `unmounted`; both
  sockets and the recorded keeper/daemon PIDs were absent.
- R4 evidence:
  [history/reviewed-repair-queue-evidence.md](history/reviewed-repair-queue-evidence.md#r4-cancellation-and-callback-terminalization).

Prior R11 evidence remains:

- Focused provider-profile, hook, and launcher files: `282 passed`.
- Full Python suite reached `4181 passed`, `15 skipped` before the unchanged
  baseline shutdown race failed; the exact test reproduced independently.
- Complete suite excluding that one adjudicated baseline test: `5389 passed`,
  `15 skipped`, `1 deselected`.
- External project
  `/home/bfly/yunwei/test_ccb2/provider-extension-inheritance-bootstrap-20260720-pV3QUQ`:
  clean-home Claude Code 2.1.206 loaded the fixture skill on its first CCB pane
  and returned `ccb-fixture-plugin-loaded` without reload.
- Follow-up external project
  `/home/bfly/yunwei/test_ccb2/provider-extension-local-path-20260720` repeated
  that first-pane load with the installed plugin path rebased into the current
  agent-local cache; source plugin SHA256 remained unchanged.
- Real Gemini CLI listed the managed fixture extension as active; real Droid
  CLI listed the active plugin from an agent-local projection with rebased
  install paths in the system-source integration check.
- Claude, Gemini, and Droid source asset comparisons remained unchanged.
- Candidate `ccb_test kill` left both projects unmounted; the follow-up project
  reported `unmounted/stopped` with no project or tmux socket.
- Python compilation and `git diff --check`: passed.

Full evidence:
[history/r11-provider-extension-validation-2026-07-20.md](history/r11-provider-extension-validation-2026-07-20.md).
