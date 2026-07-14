# Agentic Loop Workflow Implementation Status

Date: 2026-07-14
Status: In progress - current-main source gate is open
Branch: `workflow/g6c-current-main-candidate-20260714-125142`
Worktree: `/home/bfly/yunwei/ccb_worktrees/g6c-current-main-candidate-20260714-125142`
Current candidate HEAD before this status update: `fbb2dda3`

## Current Phase

The active release target is single-lane production closure: one visible
Frontdesk-started lane, one semantic orchestration bundle, and one to four
reviewed `Worker + Reviewer` workgroups. G0-G5, Decisions 027-028, and
Decision 029 P0-P4 remain accepted. The current-main integration source gate
must pass before Decision 029 P5 and the remaining visible G6 matrix resume.

The earlier `b14c66ef` source gate remains valid for its historical branch,
but it does not qualify the current-main candidate. The corrected hermetic
gate at `72b8e568` produced `26 failed, 4761 passed, 2 skipped`: one stale
PlanTree status assertion, twelve unanchored source-test review-chain failures,
and thirteen provider registry/Claude launch/restart failures. The review-chain
defect is fixed in `fbb2dda3`; provider repair and the new full gate remain
open.

## Authority

- Release goal: [single-lane-multi-workgroup-release-goal.md](goals/single-lane-multi-workgroup-release-goal.md)
- Decision 029: [029-planner-feedback-and-task-set-closure.md](decisions/029-planner-feedback-and-task-set-closure.md)
- Single-lane authority baseline: [single-lane-r1-authority-runtime-closure-20260711.md](history/single-lane-r1-authority-runtime-closure-20260711.md)
- Current source-gate history: [g6c-source-gate-and-root15-readiness-20260714.md](history/g6c-source-gate-and-root15-readiness-20260714.md)
- Historical bounded Phase 1-6 claim: [phase1-6-acceptance-report-20260705.md](history/phase1-6-acceptance-report-20260705.md)

The historical report preserves the initial real-provider Phase 6B claim and
its production/default-enablement boundary. It does not qualify this candidate.
Scripts continue to own task, task-set, revision, closure, integration,
topology, round, release, and delivery authority; provider replies are evidence.

## Last Landed

- `7903599e` merges current main `ccac2034` with accepted G6C integration
  `7e562ae5`.
- `e964f228` through `72b8e568` restore ask route options, RolePack authority,
  strict scheduler terminal parsing, and external-cwd ask-test resolution.
- `fbb2dda3` allows only tightly gated source-test explicit-project asks from
  an unanchored allowed root; ordinary unanchored and cross-project asks still
  fail closed.

## Active TODO

1. Close the thirteen provider registry, Claude launch, and Codex/Claude
   restart-recovery failures; rerun the exact failed-node batch.
2. Run the corrected hermetic full source suite serially and complete scoped
   process/socket/FIFO/worktree cleanup audit.
3. Run fresh visible Codex-primary and Claude-secondary G6 acceptance,
   including three/four workgroups, restart, busy-retain, and sidebar pressure.
4. Repeat exact available weaker-model profiles at least five times per
   provider/model/RolePack digest; do not invent `5.6` or Luna ids.
5. Complete G7 build, install, update, rollback, and installed-candidate
   visible acceptance; keep G8 publication/tagging separately authorized.

## Blocked By

Current-main source acceptance is blocked by the provider regression block and
a fresh full-suite proof. Visible G6 and package G7 cannot qualify an unaccepted
source candidate. Exact weaker-model ids and credentials must be read from the
live Codex/Claude configuration before claims are made.

## Acceptance Ownership

Workers implement and run bounded source/fake or opened-project blocks;
`mother` reviews Role boundaries and RolePack changes. `talk2` owns task
publication, dependency ordering, raw evidence/diff review, acceptance, and
next-step routing. Source runtime validation uses the candidate's explicit
`ccb_test` from `/home/bfly/yunwei/test_ccb2`. Final real-provider acceptance
uses only Codex as primary and Claude as secondary, inherited provider state,
and a lab-local `AGENT_ROLES_STORE`.

## Last Verified

- Accepted historical source gate at `b14c66ef`: `4674 passed, 2 skipped`.
- Candidate RolePack projection gate: all seven local packs compiled and the
  projection worktree remained clean.
- Candidate runtime file at `72b8e568`: `46 passed in 304.16s` in the anchored
  external root; the corrected `/tmp` full gate exposed the unanchored defect.
- Ask repair at `fbb2dda3`: ask service `41 passed`; one-node unanchored
  source/fake smoke reported `status: pass`, accepted the Reviewer chain, and
  completed project-scoped unmount with no test-owned socket/FIFO/worktree
  residue.
- Corrected current-main full gate evidence:
  `/home/bfly/yunwei/test_ccb2/gate-72b8e568-corrected-20260714T060254Z`.

## Non-Claims

The branch is not production-ready. It is not yet a source-accepted packaged
candidate, a visible G6 qualification, or a production/default-enablement
claim. Root8, root13, root14, and the current `26 failed` gate remain rejected
diagnostic evidence until superseded by fresh accepted gates.
