# Agentic Loop Workflow Plan

Date: 2026-06-24

## Purpose

Design a CCB-native multi-agent workflow loop that further reduces the burden
on the `frontdesk` group. In this model, `frontdesk` remains the user-facing boundary and
escalation surface, while planning, execution-document maintenance, task
decomposition, dynamic worker-team activation, loop monitoring, recovery, and
plan-tree synchronization are handled by separate roles under a scripted state
machine.

The basic design premise is to combine a simple, stable program kernel with
flexible agent intelligence. Scripts enforce hard constraints, identity,
state, locks, indexes, and commit rules. Agents provide semantic understanding,
planning, review, diagnosis, and human-readable documents.

The direction borrows Trellis' durable-state idea, where workflow truth lives
outside the model conversation and is advanced through scripts rather than
free-form memory. It also borrows the Team Builder idea from AutoGen Studio:
teams, roles, handoffs, and termination conditions should be declared as
workflow objects rather than improvised in every run.

CCB should not copy Trellis' implicit provider-native subagent model. Because
CCB already has visible agents, `ask`, callbacks, panes, runtime status, and
daemon-owned communication state, this plan favors explicit, inspectable,
recoverable workflow loops.

## File Map

- [roadmap.md](roadmap.md): planning sequence, current design status, and
  readiness gates.
- [implementation-status.md](implementation-status.md): current operational
  handoff, latest landed workflow slice, active TODO, blockers, and last
  verification.
- [open-questions.md](open-questions.md): unresolved product, safety, and
  implementation questions.
- [goals/orchestrator-dynamic-capacity-goal.md](goals/orchestrator-dynamic-capacity-goal.md):
  historical implementation and real-test goal for the `loop.role_profiles`
  and `ccb loop capacity` substrate, now superseded as the
  orchestrator-facing path by topology proposal and reconciliation.
- [goals/planner-plan-script-goal.md](goals/planner-plan-script-goal.md):
  implementation and source-test goal for the first planner role boundary and
  `ccb plan` task-packet command surface.
- [goals/loop-runner-bridge-goal.md](goals/loop-runner-bridge-goal.md):
  next implementation goal for removing the manual bridge between a ready task
  packet and one script-owned execution round, while preserving the
  simple-kernel/flexible-agent boundary.
- [goals/workflow-rolepack-landing-goal.md](goals/workflow-rolepack-landing-goal.md):
  landing goal for the first CCB workflow RolePack draft set, including common
  authority rule, shared templates, P0 planner/reviewer/broker/orchestrator/
  round checker roles, simplified P1 frontdesk/worker/checker roles, and
  targeted manifest/projection tests.
- [goals/workflow-runner-state-router-goal.md](goals/workflow-runner-state-router-goal.md):
  follow-up goal for extending the one-shot runner into a minimal workflow
  state router that activates planner for planning states, execution for ready
  states, and stops on paused or terminal states without moving semantic
  judgment into scripts.
- [goals/clarification-planner-followthrough-goal.md](goals/clarification-planner-followthrough-goal.md):
  next implementation goal for adding the V1 `ccb question` artifact surface,
  macro broker/frontdesk clarification loop, normalized answers, planner
  artifact import, plan-reviewer gate, optional task-detailer refinement, and
  script-owned transition to `ready`.
- [goals/workflow-rolepack-external-spec-handoff-goal.md](goals/workflow-rolepack-external-spec-handoff-goal.md):
  external handoff goal for promoting the workflow Role drafts into
  `/home/bfly/yunwei/agent-roles-spec`, installing them through CCB's Role
  store, and proving planner/task-detailer/broker/frontdesk/reviewer/
  orchestrator artifact collaboration.
- [goals/workflow-closure-smoke-goal.md](goals/workflow-closure-smoke-goal.md):
  repeatable source-wrapper smoke for the complete fake-provider workflow
  closure, including macro planner/broker/frontdesk/reviewer transitions,
  optional task-detailer refinement, ready execution, dynamic worker/checker
  release with `--policy auto`, and the current explicit-windows follow-up
  finding.
- [goals/minimum-production-candidate-goal.md](goals/minimum-production-candidate-goal.md):
  narrow production-candidate gate for one scripted workflow closure using
  `ccb plan`, `ccb question`, `ccb loop runner --once`, dynamic worker/checker
  capacity, round-result import, and auto-release cleanup.
- [topics/architecture.md](topics/architecture.md): proposed role topology,
  loop lifecycle, handoff rules, and failure escalation model.
- [topics/complete-workflow-design.md](topics/complete-workflow-design.md):
  end-to-end workflow loop design, planner activation rules, result routing,
  stop conditions, script authority, and current V1 implementation status.
- [topics/agentic-workflow-scheme.zh.md](topics/agentic-workflow-scheme.zh.md):
  Chinese system design that reorganizes the workflow around a simple stable
  kernel, flexible semantic agents, state projection, role groups, script
  capabilities, lifecycle, V1 loop closure, and deferred items.
- [topics/planner-role-design.md](topics/planner-role-design.md): planner
  authority, readiness rules, clarification boundaries, script authority, and
  orchestrator triage handoff.
- [topics/planner-plan-tree-brief-and-detail-boundary.md](topics/planner-plan-tree-brief-and-detail-boundary.md):
  planner-owned plan brief shape and boundary with task-detailer-owned detail
  docs and per-task execution refinement.
- [topics/task-detailer-role-design.md](topics/task-detailer-role-design.md):
  short-lived task refinement role that reads macro task refs, plan-tree/source
  evidence, performs detailed self-research, owns task-local clarification, and
  emits detailed execution packets without becoming the long-lived planner.
- [topics/state-and-script-contract.md](topics/state-and-script-contract.md):
  proposed short-term progress store, script entrypoints, state transitions,
  artifact requirements, and plan-tree synchronization rules.
- [topics/plan-and-runtime-list-structure.md](topics/plan-and-runtime-list-structure.md):
  durable plan packet layout, runtime loop list layout, and script-owned write
  surfaces inspired by Trellis but adapted for visible CCB agents.
- [topics/plan-update-script-landing.md](topics/plan-update-script-landing.md):
  V1 landing plan for `ccb plan task-*` scripts, task packet layout, authority
  rules, and test targets.
- [topics/orchestrator-role-capability.md](topics/orchestrator-role-capability.md):
  orchestrator role capability boundary, ask activation model, 1-4 node
  complexity slicing, runtime-agent request semantics, and task-dispatch
  constraints.
- [topics/runtime-workflow-graph-and-reconciler.md](topics/runtime-workflow-graph-and-reconciler.md):
  desired-state runtime workflow graph design, including agent topology,
  information-flow edges, call order, artifact handoffs, lifecycle gates,
  topology proposal/commit commands, and reconciler load/release behavior.
- [topics/orchestrator-rolepack-blueprint.md](topics/orchestrator-rolepack-blueprint.md):
  reviewed `mother` design for the `agentroles.ccb_orchestrator` RolePack,
  including identity, memory, skills, templates, package shape, and validation
  gates.
- [topics/role-catalog-and-boundaries.md](topics/role-catalog-and-boundaries.md):
  V1/V2 role catalog and role boundaries for converting the workflow design
  into Agent Roles specs, including frontdesk, planner, plan reviewer, broker,
  orchestrator, worker, checker, round checker, monitor, recovery, and plan
  steward boundaries.
- [topics/role-class-naming-and-hierarchy.md](topics/role-class-naming-and-hierarchy.md):
  current flat Role naming and Role Collection direction, including replacement
  mapping from experimental `agentroles.ccb_*` roles and recommended
  `agentroles.collections.*` bundles.
- [topics/role-profiles-and-capacity-skill.md](topics/role-profiles-and-capacity-skill.md):
  lower-level design for `loop.role_profiles` config and `ccb loop capacity`;
  retained as the capacity substrate that topology reconciliation may use,
  rather than the preferred orchestrator-facing contract.
- [topics/dynamic-window-pane-agent-maintenance.md](topics/dynamic-window-pane-agent-maintenance.md):
  design for runtime-managed tmux windows and panes, including
  `frontdesk-dialog`, `plan-orchestrate`, per-node execution windows, runtime
  diagnostics, placement rules, release/retain behavior, and V1 layout state.
- [topics/dynamic-agent-lifecycle-and-skills.md](topics/dynamic-agent-lifecycle-and-skills.md):
  design for dynamic agent lifecycle policy, including visible/hidden/parked/
  unloaded states, long-lived role park defaults, profile-based and inline
  role-based `ccb agent add ...`, policy-based `remove`, and the
  `dynamic-agent-lifecycle` skill boundary.
- [goals/dynamic-pane-growth-goal.md](goals/dynamic-pane-growth-goal.md):
  landing target for deterministic 1->6 pane growth, overflow windows, and
  isolated tmux smoke verification before live dynamic agent movement.
- [goals/dynamic-pane-shrink-release-goal.md](goals/dynamic-pane-shrink-release-goal.md):
  planning target for dynamic agent release, 6->1 pane compaction, overflow
  window removal, busy-agent retain behavior, and live-agent safety gates.
- [topics/context-purity.md](topics/context-purity.md): context-purity
  principle, short-lived execution context policy, and role boundaries that
  keep `frontdesk` and long-lived planning roles free of fast-changing noise.
- [topics/clarification-flow.md](topics/clarification-flow.md): staged
  clarification flow where planner emits macro candidate questions, broker
  filters them, `frontdesk` displays curated question references, and
  `task_detailer` separately owns task-local clarification with frontend
  notification.
- [topics/execution-node-and-round-verification.md](topics/execution-node-and-round-verification.md):
  execution-node structure, checker boundaries, non-convergence handling,
  partial branch draining, and round-level verification.
- [topics/round-checker-and-planner-rehydration.md](topics/round-checker-and-planner-rehydration.md):
  round checker separation, planner next-loop rehydration inputs, and result
  routing back to planner or frontdesk.
- [decisions/001-frontdesk-name.md](decisions/001-frontdesk-name.md):
  decision to call the user-facing non-executing role `frontdesk` instead of
  `main`.
- [decisions/002-stage-batched-clarification.md](decisions/002-stage-batched-clarification.md):
  decision to use stage-batched clarification with an artifact-first broker
  instead of direct planner-to-user questioning.
- [decisions/003-flat-execution-node-and-round-checker.md](decisions/003-flat-execution-node-and-round-checker.md):
  decision to make v1 execution nodes flat `worker + checker` units and add a
  separate round-level checker for loop completion.
- [decisions/004-script-owned-plan-and-runtime-lists.md](decisions/004-script-owned-plan-and-runtime-lists.md):
  decision to keep plan packets durable and runtime lists machine-owned, with
  all authoritative writes going through CCB scripts.
- [decisions/005-orchestrator-is-asked-semantic-dispatcher.md](decisions/005-orchestrator-is-asked-semantic-dispatcher.md):
  decision to make orchestrator an ask-activated semantic dispatcher that
  requests, but does not directly perform, runtime agent load/unload.
- [decisions/006-configured-role-profiles-and-capacity-skill.md](decisions/006-configured-role-profiles-and-capacity-skill.md):
  decision to give orchestrator dynamic capacity through config-declared role
  profiles and a narrow `ccb loop capacity` command surface.
- [decisions/007-planner-proposes-scripts-write-plan-state.md](decisions/007-planner-proposes-scripts-write-plan-state.md):
  decision that planner proposes semantic artifacts while CCB scripts write
  authoritative plan state.
- [decisions/008-round-checker-separate-planner-rehydrates.md](decisions/008-round-checker-separate-planner-rehydrates.md):
  decision that round checker remains separate while planner rehydrates next
  loops from durable task and round evidence.
- [decisions/009-loop-runner-activates-planner-and-stops.md](decisions/009-loop-runner-activates-planner-and-stops.md):
  decision that planner is inside the workflow loop while loop runner owns
  activation and stop decisions from authoritative state.
- [decisions/010-simple-kernel-flexible-agents.md](decisions/010-simple-kernel-flexible-agents.md):
  decision that the workflow kernel should stay simple and stable while agents
  provide semantic flexibility through artifacts that scripts commit or reject.
- [decisions/011-runtime-layout-manager.md](decisions/011-runtime-layout-manager.md):
  decision that runtime layout manager owns dynamic tmux window/pane placement
  while orchestrator only requests semantic execution capacity.
- [decisions/012-long-lived-roles-park-before-unload.md](decisions/012-long-lived-roles-park-before-unload.md):
  decision that long-lived interactive roles default to hide/park, while
  short-lived execution roles can unload only after idle/evidence gates.
- [decisions/013-role-class-prefix-naming.md](decisions/013-role-class-prefix-naming.md):
  historical decision to replace experimental CCB-prefixed workflow role ids
  with host-neutral names; its source hierarchy direction is superseded by
  Decision 017.
- [decisions/014-runtime-workflow-graph-reconciler.md](decisions/014-runtime-workflow-graph-reconciler.md):
  decision to drive dynamic agent load/release through committed runtime
  workflow graphs and topology reconciliation instead of direct orchestrator
  lifecycle commands.
- [decisions/015-task-detailer-owns-task-refinement-and-clarification.md](decisions/015-task-detailer-owns-task-refinement-and-clarification.md):
  historical decision to keep the long-lived planner macro-level, introduce a
  short-lived `task_detailer` for detailed execution refinement, and keep
  task-local clarification inside that detailer while frontend/frontdesk only
  notifies the user; Decision 019 now adds orchestrator triage before detailer
  activation.
- [decisions/016-agent-groups-and-macro-adjustment-request.md](decisions/016-agent-groups-and-macro-adjustment-request.md):
  decision that `task_detailer` macro-drift findings must flow through
  `macro-adjustment-request` artifacts instead of direct roadmap or decision
  mutation; its source-level reusable group design is superseded by Decision
  017.
- [decisions/017-flat-roles-and-role-collections.md](decisions/017-flat-roles-and-role-collections.md):
  decision to use flat installable Roles plus Role Collections for Agent Roles
  source, while keeping CCB runtime groups in Project Binding or runtime
  topology.
- [decisions/018-planner-uses-plan-brief.md](decisions/018-planner-uses-plan-brief.md):
  decision that planner primarily maintains a compact plan brief, while V1
  task-related detail docs and execution refinement are owned by
  `task_detailer` after orchestrator asks for refinement.
- [decisions/019-orchestrator-triage-before-task-detailer.md](decisions/019-orchestrator-triage-before-task-detailer.md):
  decision that orchestrator triages planner macro packets before activating
  `task_detailer`, can dispatch direct execution, and routes macro adjustment
  requests back to planner.
- [history/review-2026-06-26-loop-runner-readiness.md](history/review-2026-06-26-loop-runner-readiness.md):
  reviewer/coworker readiness review that narrowed the next implementation
  slice to task-loop binding, round-result import, `run-once --task-id`, and
  one-shot `loop runner --once`.
- [history/mother-rolepack-design-2026-06-27.md](history/mother-rolepack-design-2026-06-27.md):
  `mother` RolePack design review that accepted the P0/P1/P2 role priorities,
  common authority rule, host-neutral versus CCB-adapter split, and the first
  external Agent Roles spec landing order.
- [history/runtime-topology-reconciler-2026-06-30.md](history/runtime-topology-reconciler-2026-06-30.md):
  landing evidence for `ccb loop topology
  propose/validate/commit/reconcile/status/release`, desired/observed
  topology files, and add/move/park/release/reflow source-wrapper tests.
- [history/workflow-role-output-import-2026-07-02.md](history/workflow-role-output-import-2026-07-02.md):
  landing evidence for the planner/plan-reviewer role-output import bridge,
  source-wrapper draft-to-done smoke, and existing workflow closure regression.

## Related Sources

- [../ccb-self-role/README.md](../ccb-self-role/README.md)
- [../ccb-maintenance-heartbeat/README.md](../ccb-maintenance-heartbeat/README.md)
- [../managed-provider-completion-reliability/README.md](../managed-provider-completion-reliability/README.md)
- [../inter-agent-comm-reliability/README.md](../inter-agent-comm-reliability/README.md)
- [../callback-continuation-safety/README.md](../callback-continuation-safety/README.md)
- [../../baseline/runtime-flows.md](../../baseline/runtime-flows.md)
- [../../../agent-message-timeout-retry-contract.md](../../../agent-message-timeout-retry-contract.md)
- [../../../ccbd-startup-supervision-contract.md](../../../ccbd-startup-supervision-contract.md)
- [../../../ccbd-diagnostics-contract.md](../../../ccbd-diagnostics-contract.md)

## Scope

In scope:

- A user-facing `frontdesk` group that only handles user discussion, macro-task
  intake, confirmations, final reporting, and unrecoverable escalation.
- A planner that maintains long-term plan-tree state, compact plan brief,
  macro task publication, roadmap/evidence hygiene, and readiness
  recommendations without carrying implementation detail.
- A task detailer role that is resident and visible in the V1 `ccb-user`
  topology, but is semantically activated only when orchestrator triage
  requires detailed execution refinement; it turns macro task refs into
  task-scoped detail docs and a detail packet, then returns normal outputs to
  orchestrator and macro drift back to planner.
- A deterministic loop runner that reads short-term workflow state and starts
  or advances execution loops without relying on one agent's conversation
  memory.
- An orchestrator role that decomposes a ready execution task into bounded
  work items, selects the required execution-node topology, constrains worker
  asks, and proposes a runtime workflow graph that CCB scripts validate,
  commit, and reconcile.
- A dynamic agent lifecycle layer where the V1 default visible baseline is
  four panes, `ccb_frontdesk + ccb_task_detailer` in `ccb-user` and
  `ccb_planner + ccb_orchestrator` in `ccb-plan`, while execution and
  round-review roles can be loaded and released after idle/evidence gates
  through topology or lifecycle reconciliation.
- Dynamic execution nodes, each defaulting to a flat `worker + checker`
  structure. More complex node-internal teams are deferred until the work item
  cannot be safely split by orchestrator.
- Checker as an independent quality gate that designs node-level verification,
  audits worker output against the original design, and rejects hidden
  fallback, degradation, scope shrinkage, or false success.
- Partial execution semantics where non-converged nodes freeze only their
  dependent branch while unrelated sibling work drains to completion.
- A round-level checker that verifies the whole loop round after node work is
  drained, using planner-defined verification contracts and the actual
  orchestrator dependency graph.
- An inner monitor layer that watches loop health, `ask`/callback status,
  node heartbeats, artifacts, timeouts, and communication anomalies.
- A clarification broker path that receives stage-level candidate questions
  from planner group, filters or defaults non-blocking items, and sends only
  curated user-facing question references to `frontdesk`.
- Scripted state transitions so agents can propose progress but CCB-owned
  commands write authority state.
- Script-owned plan packets and runtime lists: durable Markdown records live
  under plan-tree, while high-frequency loop state lives under `.ccb/runtime`
  and is updated only through CCB command surfaces.
- Context-purity boundaries that keep volatile execution detail out of `frontdesk`
  and out of long-term plan-tree files unless it becomes durable evidence,
  decision material, or a blocker.
- Team-spec style declarations for roles, handoffs, termination conditions,
  escalation conditions, and per-loop limits.
- Plan-tree synchronization only at durable boundaries such as accepted plans,
  decisions, evidence, blockers, and completion.

Out of scope:

- Making `frontdesk` a hidden central executor.
- Letting worker agents freely modify authoritative loop state without a
  scripted transition.
- Replacing ccbd, dispatcher, message bureau, or provider completion authority.
- Treating the inner monitor as a business-task decision maker.
- Keeping high-frequency loop events in committed plan-tree Markdown.
- Allowing unbounded agent chains, unbounded recovery loops, or unbounded
  dynamic node creation.
- Automatically publishing releases, deleting files, or performing destructive
  repair without the existing user-confirmation and release gates.

## Role Summary

| Role | Authority | Non-Authority |
| :--- | :--- | :--- |
| `frontdesk` group | User conversation, scope confirmation, final summary, escalation handling | Direct business implementation or internal loop micromanagement |
| planner | Macro planning artifacts, plan brief, high-level acceptance, readiness recommendation, macro adjustment review | Detail design body maintenance, detailed implementation packet maintenance, runtime worker lifecycle, direct detailer/worker dispatch, or final authority over code correctness |
| `task_detailer` | Task-local refinement, task-scoped detail docs, source evidence, detail packet, stable summary backfill, task-local clarification | Roadmap/status authority, runtime dispatch, worker/reviewer control, or long-term user conversation |
| clarification broker | Candidate-question filtering, user-question artifact, answer normalization | Direct user conversation or execution-loop activation |
| planner stewardship mode / `ccb plan` scripts | Plan-tree consistency, short-term progress state, evidence linking, authoritative task/index/status writes through scripts | Business implementation, provider repair, daemon supervision, or bypassing script validation |
| loop runner | Deterministic state-machine execution and loop start/advance | Semantic product decisions |
| orchestrator | Work decomposition, execution-node selection, result aggregation | Long-term plan authority |
| execution node | Bounded `worker + checker` implementation and node-quality gate | Global task routing, hidden degradation, or durable plan mutation |
| round checker | Whole-round verification plan and execution | Product scope changes, implementation fixes, or authoritative state writes |
| inner monitor | Health observation, timeout/anomaly escalation, communication checks | Product judgment or arbitrary repair |

## Reading Path

Start with [topics/complete-workflow-design.md](topics/complete-workflow-design.md),
then read [topics/architecture.md](topics/architecture.md), then read
[topics/state-and-script-contract.md](topics/state-and-script-contract.md) and
[topics/plan-and-runtime-list-structure.md](topics/plan-and-runtime-list-structure.md),
then [topics/orchestrator-role-capability.md](topics/orchestrator-role-capability.md),
then [topics/runtime-workflow-graph-and-reconciler.md](topics/runtime-workflow-graph-and-reconciler.md),
then [topics/orchestrator-rolepack-blueprint.md](topics/orchestrator-rolepack-blueprint.md),
then [topics/role-profiles-and-capacity-skill.md](topics/role-profiles-and-capacity-skill.md),
then [goals/orchestrator-dynamic-capacity-goal.md](goals/orchestrator-dynamic-capacity-goal.md),
then [topics/planner-role-design.md](topics/planner-role-design.md), then
[topics/planner-plan-tree-brief-and-detail-boundary.md](topics/planner-plan-tree-brief-and-detail-boundary.md), then
[topics/task-detailer-role-design.md](topics/task-detailer-role-design.md), then
[topics/plan-update-script-landing.md](topics/plan-update-script-landing.md),
then [goals/planner-plan-script-goal.md](goals/planner-plan-script-goal.md),
then [topics/clarification-flow.md](topics/clarification-flow.md), then read
[topics/execution-node-and-round-verification.md](topics/execution-node-and-round-verification.md),
then [topics/round-checker-and-planner-rehydration.md](topics/round-checker-and-planner-rehydration.md).
Use [roadmap.md](roadmap.md) for readiness and implementation sequencing.
