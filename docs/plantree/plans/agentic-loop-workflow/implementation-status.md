# Agentic Loop Workflow Implementation Status

Date: 2026-07-11
Status: In progress
Branch: `workflow/agentic-loop-topology`
Worktree: `/home/bfly/yunwei/ccb_worktrees/agentic-loop-topology`

## Current Phase

The active release target is single-lane production closure: one visible frontdesk-started task lane with
one semantic orchestration bundle and one to four independently reviewed
`Worker + Reviewer` workgroups. Multi-lane Roadmap scheduling remains out of
scope.

G0-G5 source/fake scope is complete. Wave 3 landed the controller-owned G3
ready-frontier scheduler, real R2 Git transactions, T1 topology authority,
crash recovery, strict release, and runtime accelerator ownership. G5 then
proved the integrated scheduler through one-to-four-workgroup source/fake
runtime flows. The current phase is G6 visible real-provider acceptance. This
is not yet a claim that live multi-workgroup providers, Config V3 opened
projects, packaged candidates, or default enablement pass.

## Authority

- Goal and waves:
  [goals/single-lane-multi-workgroup-release-goal.md](goals/single-lane-multi-workgroup-release-goal.md)
- Detailed contracts and tests:
  [topics/single-lane-multi-workgroup-modification-and-test-plan.md](topics/single-lane-multi-workgroup-modification-and-test-plan.md)
- Accepted boundary:
  [decisions/025-single-lane-multi-workgroup-release-gate.md](decisions/025-single-lane-multi-workgroup-release-gate.md)
- Frozen interfaces:
  [decisions/026-authority-envelope-and-adaptive-workgroup-selection.md](decisions/026-authority-envelope-and-adaptive-workgroup-selection.md)

Provider replies remain evidence only. Scripts own bundle, task, node,
integration, topology, round, and release authority. Mount topology remains
physical placement/lifecycle state, not a semantic dispatch graph.

## Last Landed

- `8d3fc102`: multi-workgroup scheduler and durable node state machine.
- `92da3faf`: full-frontier auto-advance, exact-once/rework/result/release fixes.
- `bca51abd`: scheduler binding to real R2 and raw T1 topology authority.
- `fb4b26c7`: explicit phase2 test-runtime ownership and bounded cleanup.
- `96172d92`: persisted runtime accelerator ownership and safe takeover.
- `94ea6d73`: fail-closed accelerator recovery and corrupt-authority handling.
- `5163ad6f`: G5 source/fake runtime campaign harness and ten-scenario matrix.
- `9fceb5de`: terminal failed workgroup quarantine, restore, and R2 exclusion.
- `b42ec3b2`: project-bound recovery of spilled G5 fake-provider contracts.

Wave 3 evidence:
[history/single-lane-wave3-g3-scheduler-closure-20260711.md](history/single-lane-wave3-g3-scheduler-closure-20260711.md).

G5 evidence:
[history/single-lane-g5-source-fake-acceptance-20260711.md](history/single-lane-g5-source-fake-acceptance-20260711.md).

Earlier accepted checkpoints remain in the G1, R1, Wave 1, and Wave 2 history
records linked from the goal, including
[history/single-lane-r1-authority-runtime-closure-20260711.md](history/single-lane-r1-authority-runtime-closure-20260711.md).

## Next Target

Run G6 from fresh visible opened projects: inherited real Codex/Claude provider
environment, lab-local `AGENT_ROLES_STORE`, frontdesk-started natural tasks,
one-to-four workgroups, visible UI/sidebar evidence, restart/failure semantics,
busy-retain, deterministic integration, release, and raw evidence matching B7.

## Execution Queue

- Waves 0-3, complete: F1, R1, C1/P1, R2/T1/E1, and G3 are integrated.
- G5, complete: direct source/fake full-flow acceptance owned by `talk2`.
- G6, active: visible opened-project Codex/Claude acceptance.
- G7, gated: package/install/update/rollback readiness.
- G8, separate: publication requires explicit user authorization.

## Active TODO

1. Prepare a fresh G6 visible project root under `/home/bfly/yunwei/test_ccb2`
   with explicit source `ccb_test`, inherited provider environment, and
   project-local role store.
2. Run frontdesk-started V0/V1/V2/V3 natural tasks proving one to four
   workgroups, actual overlap where applicable, review order, integration,
   root output, sidebar/window evidence, and zero final dynamic residue.
3. Run a separate restart/failure G6 scenario covering durable intent replay,
   node failure or reviewer rework, rollback/busy-retain behavior, and cleanup.
4. Normalize and audit raw task/job/topology/Git/UI evidence against B7 before
   accepting any real-provider row.
5. After G6 passes, freeze the G7 packed-candidate/install/update/rollback
   gate.

## Blocked By

No external dependency blocks G6 beyond live provider availability. Packaging,
default enablement, and publication remain intentionally gated by G6/G7
evidence and explicit user authorization.

## Validation And Acceptance

Workers may implement bounded repairs, but `talk2` directly runs and audits
acceptance. Visible real validation must use fresh projects under
`/home/bfly/yunwei/test_ccb2`, explicit source `ccb_test`, inherited provider
environment, a project-local `AGENT_ROLES_STORE`, and an inspectable separate
terminal/UI. Script output cannot substitute for opened-project evidence.

## Last Verified

- G3 plus Wave 2 adjacent scheduler/integration gate: `495 passed`.
- Full non-provider-blackbox repository gate: `4210 passed, 2 skipped, 21 deselected`.
- Current-run command-line residue: `0`; cwd-owned runtime residue: `0`.
- Runtime accelerator count was conserved across the full gate: `6 -> 6`.
- Changed-source `py_compile`, `pyflakes`, and `git diff --check`: passed.
- G5 final source/fake campaign:
  `/home/bfly/yunwei/test_ccb2/talk2-g5-final-20260711130355-evidence`,
  campaign status `pass`, row count `10`, B7 SHA256
  `b228c8a550580d4e1f0e2f72339aeb3972102f4efaeefb9313dd95e83f7ff806`.
- Integrated G5 branch checks: `test_single_lane_multi_workgroup_smoke.py`
  `37 passed`; G5 adjacent scheduler/R2/Phase6/campaign suite `126 passed`;
  changed-source `py_compile`, `pyflakes`, `git diff --check`, and narrow
  residue scans passed.
- Post-G5 full non-provider-blackbox gate:
  `4263 passed, 2 skipped, 21 deselected in 569.98s`. No current pytest-root
  runtime process remained; five pytest socket files were not connectable.

## Non-Claims

The branch is not production-ready. G5 source/fake closure does not prove a
visible real multi-workgroup project, Config V3 opened-project behavior,
packaged candidate behavior, production/default enablement, or publication.
Those claims remain behind G6-G8.

The bounded earlier Phase 1-6 claim remains archived in
[history/phase1-6-acceptance-report-20260705.md](history/phase1-6-acceptance-report-20260705.md).
