# Agentic Loop Workflow Implementation Status

Date: 2026-07-13
Status: In progress — G6C root14 repairs landed, rework smoke stability blocker active
Branch: `workflow/g6c-integration`
Worktree: `/home/bfly/yunwei/ccb_worktrees/g6c-integration`
Current HEAD before this status update: `3a4b41da`

## Current Phase

The active release target is single-lane production closure: one visible
Frontdesk-started lane with one semantic orchestration bundle and one to four
reviewed `Worker + Reviewer` workgroups. G0-G5, Decision 027, and Decision 028
are accepted. Decision 029 P0-P4 source implementation is integrated; P5
direct acceptance is active.

Root14 passed fresh project startup, the Frontdesk capability handoff, exact
five-task Planner import, repo-independent verification, and an L1 real
Worker/Reviewer/Round Reviewer `done/pass`. L2 then exposed Orchestrator
RolePack fence-language drift: the provider used the bundle schema as the code
fence language, and the importer correctly failed closed because literal
fenced JSON was absent. Root14 was rejected and safely unmounted. A separate
harness replay defect was also confirmed after a failed precondition consumed
a fixed read-only observation label. The RolePack, recovery, and exact
controller-boundary regressions have now landed and their focused gates pass.
Root15 remains forbidden because the complete single-lane fake-runtime smoke
twice exposed non-deterministic rework exact-once and cleanup failures that do
not reproduce in a single targeted rerun.

## Authority

- Release goal: [single-lane-multi-workgroup-release-goal.md](goals/single-lane-multi-workgroup-release-goal.md)
- Decision 029: [029-planner-feedback-and-task-set-closure.md](decisions/029-planner-feedback-and-task-set-closure.md)
- P0-P5 plan: [planner-feedback-and-task-set-closure-plan.md](topics/planner-feedback-and-task-set-closure-plan.md)
- Current checkpoint: [g6c-decision029-integration-and-root8-diagnostic-20260712.md](history/g6c-decision029-integration-and-root8-diagnostic-20260712.md)

Provider replies remain evidence only. Scripts own task, task-set, revision,
closure, integration, topology, round, release, and delivery authority.

## Last Landed

- `4f166209` through `43847d18`: Decision 029 schemas, parent authority,
  Detailer feedback, closure aggregation, Planner backfill, Frontdesk status,
  and source/fake protocol corpus.
- `50874729` through `a9f1e26e`: transport, retry lineage, transaction fencing,
  durable journals, and admission hardening.
- `4f80bc94` through `8faa6fa4`: activation-sidecar and parent-authority harness
  repairs discovered by fresh real runs.
- `d941fa2e` through `c37c4ac4`: shared stop-contract corpus, canonical
  Detailer provenance, monotonic state fencing, SHA-verified normal stops,
  fail-closed task scope, convergent semantic revisions, idempotent post-state,
  and auto-runner reconciliation semantics.
- `f09cb211` through `ec8fee16`: public three-layer architecture document,
  Mermaid flows, and reviewed SVG/PNG promotional graphic.
- `c4dbeed1` and `3a5e1810`: Frontdesk/Planner Git-capability contract and
  harness request injection; root13 proved the capability survives the real
  direct handoff with `controller_rewrote_body=false`.
- `c6bd0235` through `77c54a98`: bounded terminal constraint and settlement,
  Planner RolePack alignment, strict B7 closure evidence, real authority and
  restart/idempotence regressions, and mixed-terminal feedback closure.
- `82a3a622`: Gemini session observation now resumes safely after ccbd restart
  through an adapter-specific opt-in; the prior four full-suite failures are
  covered by durable terminal, session-cursor, rotation, and mutation evidence.
- `2e54d2df`: Orchestrator RolePack requires a literal `json` fence and keeps
  the bundle schema only in the JSON object's top-level `schema` field.
- `f7390fd0` and `3a4b41da`: read-only harness observation gains bounded
  recovery labels while mutating labels stay fail-closed, and the exact
  root14 schema-as-fence controller boundary is regression-covered.

Earlier accepted R1 authority/runtime evidence remains indexed at
[history/single-lane-r1-authority-runtime-closure-20260711.md](history/single-lane-r1-authority-runtime-closure-20260711.md).

## Active TODO

1. Reproduce and close the single-lane fake-runtime rework exact-once and
   release/zero-residue instability without sleeps or weaker checks.
2. Pass the complete single-lane smoke repeatedly and the current full source
   suite.
3. Run fresh visible root15 through
   all five routes, closure, B7, release, shutdown, and zero residue.
4. Complete remaining G6 three/four-workgroup, restart, busy-retain, and
   provider-profile rows from fresh opened projects.
5. Run G7 package/install/update/rollback gates and one visible installed-
   candidate workflow; keep G8 publication separately authorized.

## Blocked By

Blocked from root15 by a source/fake rework exact-once and cleanup stability
failure: complete smoke runs passed only `37/39` and `38/39`, while a targeted
two-case rerun passed. Production readiness also remains gated by fresh real
root15 acceptance, the remaining G6 matrix, and G7 package/install/update/
rollback acceptance.

## Acceptance Ownership

Workers may review and implement bounded source repairs. `talk2` directly
runs, observes, and audits real opened-project acceptance under
`/home/bfly/yunwei/test_ccb2` using this worktree's explicit `ccb_test`,
inherited provider environment, and a root-local `AGENT_ROLES_STORE`. RolePack
changes go through `mother`; source/runtime diagnostics may use `ccb_self`.
When a worker needs a reviewer result to finish its current task, it must use
`ask --chain` or leave reviewer submission to `talk2`; `--silence` is only for
independent work whose successful result is not needed upstream.

## Last Verified

- Fourth-round independent detail-ready authority review: PASS at
  `c37c4ac4`; `344 passed`, `compileall` and `git diff --check` passed, with no
  High/Medium finding. Completion snapshot:
  `/home/bfly/yunwei/ccb_source/.ccb/ccbd/snapshots/job_2ccb4102700d.json`,
  SHA-256 `1d920d45af105b2ec1e9f1e8b455e2fc8ba133dcde5603e3b78f589dbd9b20b0`.
- Parent-authority harness checkpoint: `78 passed`.
- Current-HEAD full non-provider-blackbox gate: `4739 passed, 2 skipped,
  21 deselected in 732.49s`; `compileall`, `pyflakes`, and `git diff --check`
  passed after the one-line unused-import cleanup at `2d897845`.
- Root8: L1/L2 `done/pass`, L4 macro `replan_required`, L4 blocked `blocked`;
  L3 rejected after repeated Orchestrator activation and runner step limit.
- Root13: capability handoff and initial five-task import passed; L1/L2 and
  both L4 routes reached expected terminals. L3 reached `detail_ready`, then
  `job_564275f23588` was imported as `ready_for_orchestration` despite
  `status_recommendation=detail_ready`; the project was rejected, unmounted,
  and auto-runner pid `4118860` terminated with evidence preserved.
- Harness and RolePack gates at `3a5e1810`: `79 passed` and `24 passed`;
  `pyflakes`, `compileall`, `git diff --check`, and clean-worktree checks
  passed before root13.
- Bounded-terminal integration gates through `77c54a98`: harness `94 passed`,
  workflow core `351 passed`, RolePack/plan documents `56 passed`, with real
  no-monkeypatch closure and mixed-terminal feedback-chain coverage.
- Gemini restart recovery at `82a3a622`: the four prior failures passed
  independently, non-Gemini conservative restore boundaries passed `2`, and
  the complete Phase 2 entrypoint passed `77`.
- Current-HEAD full source suite: `4792 passed, 2 skipped in 732.93s`;
  `compileall`, `git diff --check`, changed-file `pyflakes`, and clean-worktree
  checks passed before this plan-only update.
- Root14: Frontdesk exact completion, committed direct Planner handoff with
  `controller_rewrote_body=false`, exact five-child task set, and L1
  `done/pass` all passed. L2 import failed closed with
  `orchestrator_reply_bundle_requires_fenced_json`; no L2 Worker was submitted.
  Pending guard passed, cleanup returned `state: unmounted`, all resident
  agents stopped, and no project process remained.
- Post-root14 focused gates at `3a4b41da`: Orchestrator RolePack `26 passed`,
  RolePack projection `68 passed`, Phase 6B harness `96 passed`, launch docs
  `30 passed`, loop controller `256 passed`, and Orchestrator/bundle selector
  `19 passed`.
- Complete single-lane smoke is not accepted: first run `37 passed, 2 failed`,
  second run `38 passed, 1 failed`. Failures involve
  `reviewer_rework_pass` and/or `reviewer_rework_exhausted_blocked`, including
  `rework_exactly_once` and on the second run release/dynamic/worktree residue.
  A targeted two-case rerun passed, so the instability remains unclosed rather
  than waived as flaky.

## Non-Claims

The branch is not production-ready. It is not yet a packaged candidate or
production/default-enablement claim. Root8 and root13 are diagnostic evidence,
not passes; root14 is also rejected evidence, not a pass. A green source suite
does not replace root15 or G6/G7 acceptance.
Three/four-workgroup, restart, busy-retain, provider qualification, G7
packaging, and G8 publication remain outside the current accepted claim.

The bounded earlier Phase 1-6 claim remains archived in
[history/phase1-6-acceptance-report-20260705.md](history/phase1-6-acceptance-report-20260705.md).
