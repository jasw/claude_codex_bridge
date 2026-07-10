# Agentic Loop Workflow Implementation Status

Date: 2026-07-11
Status: In progress
Branch: `workflow/agentic-loop-topology`
Worktree: `/home/bfly/yunwei/ccb_worktrees/agentic-loop-topology`

## Current Phase

The active release target is single-lane production closure: one visible,
frontdesk-started macro task, one semantic orchestration bundle, and one to
four independently reviewed `Worker + Reviewer` workgroups. Multi-lane
Roadmap scheduling remains out of scope.

G0/F1, R1/G1, C1/P1, and Wave 2 R2/T1/E1 are complete. The branch now has the
controller-owned Git transaction kernel, one-to-four-workgroup physical
topology/capacity compiler, and strict deterministic evidence harness. The
direct engine still pauses multi-node bundles before bind, so the current
phase is Wave 3: one owner must wire the G3 ready-frontier scheduler across
the landed authority, Git, topology, capacity, and recovery interfaces.

## Authority

- Goal and execution waves:
  [goals/single-lane-multi-workgroup-release-goal.md](goals/single-lane-multi-workgroup-release-goal.md)
- Detailed contracts and tests:
  [topics/single-lane-multi-workgroup-modification-and-test-plan.md](topics/single-lane-multi-workgroup-modification-and-test-plan.md)
- Accepted release boundary:
  [decisions/025-single-lane-multi-workgroup-release-gate.md](decisions/025-single-lane-multi-workgroup-release-gate.md)
- Frozen F1 interfaces:
  [decisions/026-authority-envelope-and-adaptive-workgroup-selection.md](decisions/026-authority-envelope-and-adaptive-workgroup-selection.md)

Provider replies remain evidence only. Scripts own bundle, task, node,
integration, topology, round, and release authority. Mount topology remains
physical placement/lifecycle state, not a semantic dispatch graph.

## Last Landed

- `c64ab341`: strict E1 campaign classification, malformed-input handling,
  complete dynamic-role accounting, and real JSON Schema validation.
- `502cc3e1`: deterministic 19-case E1 evidence harness and schemas.
- `912764f6`: bounded generated topology placement and pane-order validation.
- `64f95b1b`: one-to-four-workgroup topology/capacity and lifecycle substrate.
- `bd7bcbd7`: durable Git authority intents, verification quarantine,
  rollback recovery, and idempotent cleanup.
- `f3b6b7a6`: controller-owned node/integration worktrees, reviewed commits,
  deterministic integration, promotion, and rollback.
- `fcf07b3a`: strict RolePack schema/provider compatibility in Config V3.
- `6c2a15ad`: C1 Config V3 core, effective config, diagnostics, and migration preview.
- `95d9a409`: parser-stable coder/reviewer RolePack result fields.
- `615460ec`: P1 adaptive workgroup RolePack contracts and projection tests.
- `0c2f19ef`: R1 authority/runtime closure and generalized one-node kernel.
- `ec01d53a`: F1 Decision 026 and adaptive selection freeze.
- `77ca803a`: production-closure Goal, whole-block worker waves, direct
  acceptance campaign, and separate deployment versus publication gates.
- `5f938559`: G1 foundation evidence and roadmap/status checkpoint.
- `34027943`: orchestration-bundle foundation source and tests.
- `ce4f7590`: single-lane multi-workgroup release plan and test matrix.

Foundation evidence:
[history/single-lane-multi-workgroup-g1-foundation-20260710.md](history/single-lane-multi-workgroup-g1-foundation-20260710.md).

R1 closure evidence:
[history/single-lane-r1-authority-runtime-closure-20260711.md](history/single-lane-r1-authority-runtime-closure-20260711.md).

Wave 1 closure evidence:
[history/single-lane-wave1-config-rolepack-closure-20260711.md](history/single-lane-wave1-config-rolepack-closure-20260711.md).

Wave 2 closure evidence:
[history/single-lane-wave2-git-topology-evidence-closure-20260711.md](history/single-lane-wave2-git-topology-evidence-closure-20260711.md).

## Next Target

Implement the single-owner G3 ready-frontier scheduler. It must consume the
landed R2/T1 APIs directly, use T1-returned compacted agent names, pass the
latest active-workspace set to R2 cleanup, and preserve R1 exact-once intent
and recovery semantics.

## Execution Queue

- Wave 0, complete: F1 is frozen by Decision 026.
- Wave 1, complete: R1, C1, and P1 are integrated and the combined repository
  gate is green.
- Wave 2, complete: R2, T1, and E1 were integrated in that order and passed the
  combined Git/topology/config/workspace/evidence gate.
- Wave 3, active: one owner closes the central ready-frontier scheduler; its
  state transitions and crash windows are not split across workers.
- G5-G7 acceptance: `talk2` directly owns source/fake, visible real-provider,
  UI/lifecycle, package/install/update/rollback, and final readiness decisions.

## Active TODO

1. Freeze the G3 adapter boundary across R1, R2, T1, Config V3, and E1.
2. Implement submit-all-ready, per-node review/rework, dependency unblocking,
   deterministic integration, final round review, and complete release.
3. Run source/fake crash-window and two/three/four-workgroup matrices directly.
4. Keep real 1-4 group roots unopened until the multi-node runtime gate passes.

## Blocked By

No external dependency blocks G3. Internal gates intentionally block real
multi-workgroup execution, Config V3 runtime enablement, package publication,
and multi-lane work until G3 and its direct source/fake acceptance pass. Exact
package version, registry, tag, and publication remain release-time decisions.

## Validation And Acceptance

Workers may implement and self-test coherent packages, but their reports are
supporting evidence only. `talk2` reviews diffs, integrates commits, reruns
tests, and directly owns all acceptance.

Visible real validation must use fresh projects under
`/home/bfly/yunwei/test_ccb2`, the explicit source `ccb_test`, inherited system
provider environment, a project-local `AGENT_ROLES_STORE`, and an inspectable
separate terminal/UI. Required runs are V0 one-group compatibility, V1/V2/V3
real two/three/four-group tasks, V4 restart/failure/rollback/busy-retain, and
V5 packed external-install workflow. Raw task/job/Git/topology/UI evidence must
agree with B7; script output cannot substitute for the opened project.

## Last Verified

- R2+T1 combined integration gate: `180 passed`.
- Wave 2 R2+T1+E1 and adjacent smoke gate: `249 passed`.
- R2 real-Git P0 recovery, rollback, intent, and cleanup suite: `42 passed`;
  adjacent workspace/lifecycle/hygiene suite: `61 passed`.
- Changed-source `py_compile`, `git diff --check`, and clean worktree: passed.
- Wave 1 focused gates: `176`, `270`, and `218` passed.
- Repository non-provider-blackbox gate: `4033 passed, 2 skipped, 21 deselected`.

## Non-Claims And History

The branch is not production-ready: multi-node bundles still pause before
execution because the ready-frontier scheduler is absent, and no
multi-workgroup real-provider or packed-candidate acceptance exists. Wave 2
fixtures prove evidence classification and component contracts, not live
multi-workgroup execution.

The superseded detailed status log is preserved at
[history/implementation-status-through-g1-foundation-20260710.md](history/implementation-status-through-g1-foundation-20260710.md).
Older bounded Phase 1-6 acceptance remains available in
[history/phase1-6-acceptance-report-20260705.md](history/phase1-6-acceptance-report-20260705.md).
