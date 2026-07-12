# G6C Decision 029 Integration And Root8 Diagnostic

Date: 2026-07-12
Status: implementation integrated; real acceptance pending
Phase: G6C / Decision 029 P5
Read when: resuming the final source and visible real-provider acceptance

## Scope

This checkpoint records the current integration branch after the Decision 029
task-set closure package, transaction hardening, parent-authority harness
repairs, the rejected root8 real-provider run, and the follow-up detail-ready
reconciliation fix. It is not an acceptance report.

## Integrated Source

- Branch: `workflow/g6c-integration`
- Worktree: `/home/bfly/yunwei/ccb_worktrees/g6c-integration`
- Current accepted implementation head: `d941fa2e`
- Decision 029 core starts at `4f166209` and includes task-set parent authority,
  Detailer-to-Planner feedback, deterministic task-set aggregation, Planner
  backfill, Frontdesk notification, revision fencing, exact-once recovery, and
  transaction durability.
- Harness and admission hardening through `8faa6fa4` makes parent selection and
  transaction recovery follow persisted source authority instead of discovery
  order or provider prose.
- `d941fa2e` replaces duplicated detail-ready regexes with one clause-aware
  matcher and adds task-lock-owned, stale-fenced reconciliation from
  `ready_for_orchestration/orchestrator` to `detail_ready/planner` when all
  recorded detail artifacts and the explicit stop contract remain valid.

## Source Evidence

- Current detail-ready matcher, reconciliation, loop-capacity, and PlanTask
  focused gate: `280 passed`.
- Parent-authority harness checkpoint: `78 passed`.
- The most recent full repository gate predates the final harness and
  detail-ready fixes: `4583 passed, 2 skipped, 21 deselected in 675.06s`.
  It is historical evidence only; the full gate must be rerun at current HEAD.
- Current integration worktree was clean at `d941fa2e` before this plan update.

## Root8 Real-Provider Evidence

Preserved root:
`/home/bfly/yunwei/test_ccb2/deploy-g6c-real-talk2-20260712-8`

The project was opened with the source worktree `ccb_test`, inherited real
provider configuration, and a root-local Role store. Script-owned parent
authority and Frontdesk/Planner transaction journals were valid. L1 and L2
reached `done/pass`; the macro-adjustment L4 child reached
`replan_required`; the blocked L4 child reached `blocked`.

L3 produced all three detail artifacts and a valid local-detail result, but
the then-current stop matcher did not recognize the provider's affirmative
phrases `with terminal status detail_ready` and
`Preserve terminal expectation detail_ready`. Planner was activated, L3 was
reset to `ready_for_orchestration`, and Orchestrator repeated until the
auto-runner 24-step limit. The task-set therefore remained running and root8
was rejected. It must remain preserved and must not be reused after repair.

## Review Decision And Repair

The accepted repair has two layers:

1. One fail-closed shared stop-contract matcher used by both activation and
   actionable-task selection. It rejects negation, weak modality, conditions,
   questions, examples, fenced code, blockquotes, other-task text, conflicting
   terminal states, and schema/token enumeration.
2. A root8-shaped recovery action that verifies task state, route, loop
   absence, task revision, timestamp, artifact paths and digests, actor
   presence, and explicit stop authority under the task lock before committing
   `detail_ready/planner`. Same-authority replay is idempotent; stale authority
   fails closed.

## Remaining Acceptance Gates

1. Independent security review of `d941fa2e`, especially cross-statement
   terminal conflicts and artifact-actor authority.
2. Current-HEAD full non-provider-blackbox suite plus static/diff checks.
3. Fresh root9 opened-project L1-L4 run proving L3 reconciliation, task-set
   aggregate closure, Planner backfill, Frontdesk notification, B7, visible
   panes, release, shutdown, and zero residue.
4. Remaining G6 real rows: three/four workgroups, in-flight restart,
   busy-retain, and provider-profile qualification.
5. G7 clean candidate package/install/update/rollback and one visible
   installed-candidate workflow. Publication remains a separate explicit
   authorization gate.

## Acceptance Ownership

Workers may review or implement bounded source repairs. Under the active
project runtime rules, `talk2` directly runs, observes, and audits fresh opened
real-provider projects and owns the final pass/reject decision. RolePack or
role-contract changes are reviewed with `mother`; source/runtime diagnostics
outside that boundary may be assigned to `ccb_self`.

