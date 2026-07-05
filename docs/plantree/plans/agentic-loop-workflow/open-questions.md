# Agentic Loop Workflow Open Questions

Date: 2026-07-04

## Product Questions

1. Resolved V1 direction: the first user-visible automatic runner is
   `ccb loop runner --once`, which scans for one ready task, executes one round,
   imports the result, and exits. Long-running daemon or frontdesk-triggered
   automatic activation remains deferred.
2. Should a blank CCB project include this workflow by default, or should it be
   an optional advanced mode?
3. Resolved V1 direction: the smallest acceptable automatic workflow path now
   includes planner-owned task documents, orchestrator triage, and one
   ask-first execution node. Phase 6A remains limited to a bounded single-round
   program-matrix claim.
4. Should users see loop state in the sidebar/rich panel in v1, or only through
   CLI diagnostics?

## State And Authority Questions

1. Resolved direction: loop runner owns role activation and stop decisions by
   reading task/loop state, while scripts write authoritative status. See
   [decisions/009-loop-runner-activates-planner-and-stops.md](decisions/009-loop-runner-activates-planner-and-stops.md).
2. Resolved V1 direction: task-level read-modify-write protection starts as a
   per-task lock in the `ccb plan` command service used by
   `task-bind-loop`/`task-import-round`. The longer-term owner for loop-wide
   locks across ccbd, an external runner, or a separate helper remains open.
3. Resolved V1 direction: plan stewardship is a planner work mode plus
   deterministic `ccb plan` command authority, not a separate required
   mainline Role. Scripts write authority; planner may audit and summarize but
   cannot bypass scripts.
4. Should agents be allowed to request transitions directly, or must every
   transition pass through explicit script validation and planner review when
   macro state changes?
5. What fields are required for a transition to be accepted: phase, owner,
   artifact refs, verification refs, parent job id, and lease id?
6. Resolved for V1: `ccb plan task-*` should be implemented as first-class CLI
   commands for task packet creation, artifact import, status, show/list, and
   breadcrumb. See
   [topics/plan-update-script-landing.md](topics/plan-update-script-landing.md).
7. Resolved next slice: round checker output is not enough by itself. The
   result must be imported through `task-import-round`, validated against the
   current loop binding, converted to a first-class round artifact, and only
   then used by loop runner for the next activation decision.
8. What stale-lease reset policy is safe enough for V1 after a runner crashes
   while a task is `running`?

## Planner Questions

1. Resolved design boundary: planner is inside the workflow loop but outside
   execution rounds. It is activated for `draft`, resolved clarification,
   `partial`, `replan_required`, resolved blockers, or changed user scope. See
   [topics/complete-workflow-design.md](topics/complete-workflow-design.md).
2. Should `agentroles.planner` be published as a standalone Agent Roles catalog
   role immediately, or should V1 keep planner as a project-local configured
   role until the `ccb plan` script surface lands?
3. Should `plan_reviewer` be a separate role id or a mode/profile of planner
   for V1?
4. Should `ready` require a semantic `review.md` artifact in every case, or
   can low-risk tasks be marked ready by deterministic required-field checks
   only?
5. What maximum size should a planner handoff have before broker/orchestrator
   must receive artifact links instead of inline content?
6. Resolved direction: planner cycling must have its own stop limits, including
   no new artifact/decision evidence, repeated same failure signature, repeated
   script validation failure, and excessive scope churn. See
   [topics/planner-role-design.md](topics/planner-role-design.md).

## Clarification Questions

1. Should the deterministic broker router live in the same helper as
   `ccb loop`, or as a separate `ccb question` command namespace?
2. What is the default per-phase user-question budget: one question, up to
   three questions, or configurable by workflow spec?
3. Should `frontdesk` be allowed to read only `user_questions.md` display
   artifacts, or may it inspect broker review metadata when the user asks why
   a question is being asked?
4. What confidence threshold should force a second clarification instead of
   letting broker normalize a vague user answer?

## Runtime Questions

1. Resolved V1 direction: the workflow has graduated from fixed configured
   `coder`/`checker` agents to loop-runner-mediated dynamic
   `worker + code_reviewer` capacity for the one-shot execution round. The next
   graduation target is mount-topology-driven dynamic agent placement plus
   ask-first orchestration from document anchors.
2. Resolved V1 direction: temporary execution agents use generated
   `loop-<loop-id>-<profile>-<index>` names, are scoped by loop capacity or
   topology records, and are released through script-owned idle/evidence gates.
3. What is the hard maximum for per-loop nodes, recovery rounds, and total
   runtime after default per-node rework is bounded separately?
4. Should the orchestrator be released and recreated after each loop round, or
   can it persist across multiple rounds with state rehydration?
5. Should the first `ccb loop capacity` implementation use daemon-side
   transient runtime overlays immediately, or start with a generated config
   block over the existing guarded `ccb reload` transaction?
6. What exact provider adapter mapping should compile
   `thinking = "low|medium|high"` into provider-specific startup arguments or
   model settings?
7. Should generated loop agents appear in the sidebar as normal agents, grouped
   under a loop window, or under a dedicated runtime section?
8. Resolved direction: committed topology should not become the preferred
   communication graph. It should become mount topology; normal collaboration
   uses `ask`, and stable results are imported through task/round artifacts.
   See
   [decisions/020-mount-topology-and-ask-first-orchestration.md](decisions/020-mount-topology-and-ask-first-orchestration.md).
9. How long should legacy topology-dispatch tests and command paths remain
   available after the mount-topology split?

## Phase 6B Real-Provider Launch Questions

Phase 6B remains unclaimed. Historical L0 runs are recorded in
[topics/phase6b-l0-launch-request-20260704.md](topics/phase6b-l0-launch-request-20260704.md)
and current handoff state is in
[implementation-status.md](implementation-status.md). The latest B-only
repeat6 run was approved once by reviewer2 in `job_8c7b404ad63c`, executed
once under `talk2` supervision, and consumed that approval. It reached the
intended B-only resident planning group ask path and produced an L0
runtime-sanity `pass` B7 report:
[history/phase6b-real-provider-l0-b-only-repeat6-b7-20260704.md](history/phase6b-real-provider-l0-b-only-repeat6-b7-20260704.md).
This is not Phase 6B readiness, and any further real-provider command requires
fresh launch-specific reviewer approval.

Resolved owner answers:

- Provider map: `ccb_round_reviewer -> claude`; `ccb_frontdesk`,
  `ccb_planner`, `ccb_orchestrator`, `ccb_task_detailer`, `coder`, and
  `code_reviewer -> codex`.
- Provider home/account policy: inherit the current real provider home, with
  external-root execution under `/home/bfly/yunwei/test_ccb2` and isolated
  `HOME` / `CCB_SOURCE_HOME`.
- RolePack seeding scope: seed only the seven required roles.
- L0 topology scope after user decision "方案 2：只跑 B": mount only the B
  resident planning group `ccb_frontdesk + ccb_planner + ccb_orchestrator +
  ccb_task_detailer`; do not run the historical A minimal orchestrator probe.
- Ask schema for the latest L0 shape: submit-only compact ask to the mounted
  B orchestrator target (`p6bl0b-orchestrator` in repeat6), runtime sanity
  prompt, 600 second timeout.
- B7 normalization owner: `talk2`; provider replies are evidence only.
- Launch reviewer scope: Phase 6B L0 runtime sanity only.

Current open questions before any further launch approval:

Resolved after worker1 `job_d239b74ee4a6` and reviewer2
`job_50ce63ab373b`: parked resident planning-group agents may be pruned from
loop topology authority as `drained_agents` while lifecycle records remain
parked and dispatch-disabled. This is a source-side readiness repair only; no
fresh real-provider L0 run has yet proved that path.

Resolved after reviewer1 `job_ebe46ce6cd8b`: the accepted matrix/report rule is
that `release_incomplete_agents` plus bounded `release_blockers` classifies as
`valid_non_success`, while missing, vague, or unbounded blocker evidence remains
a hard failure. Future B7 normalizers must emit bounded reason text from the
accepted marker vocabulary.

Resolved after reviewer2 `job_8c7b404ad63c` and package approval
`job_c7ebe2d2dade`: repeat6 B-only L0 launch was approved exactly once,
executed exactly once, and produced L0 `pass` evidence. That approval is
consumed.

Resolved after reviewer2 `job_c0fac249749e`: the first L1-L4 frozen request is
`DOC-ONLY ACCEPTED`, not approved to run. It uses L3 route/detail-only
`detail_ready`, materializes fixture paths and hashes, and keeps
reviewer-rework/partial observations as explicit blockers for a Phase 6B claim.
The current claim coverage matrix is
[topics/phase6b-real-provider-claim-coverage-matrix.md](topics/phase6b-real-provider-claim-coverage-matrix.md).

Resolved after worker1 callback `job_307d5f834a1a` and reviewer2
`job_d023a883a62d`: the static L1-L4 B7 normalizer now emits the declared
shared and task-specific fields with conservative placeholders. This does not
approve runtime.

Resolved after worker3 `job_82d723ec0f89` and reviewer2 `job_f20daf37898d`:
the B7 normalizer now has accepted static `authority_checks.*` output and the
L5 tranche has an accepted embedded normalizer shape. This does not approve
runtime.

1. For the next launch-specific request, should talk2 request the bounded
   partial candidate only, or include both the partial and reviewer-rework
   candidates as an ordered L5 observation tranche? Worker2 `job_e6456cf4a072`
   and reviewer2 `job_3824dde8454e` accepted both candidates as plan-only
   readiness, but no runtime approval exists.

## Execution Verification Questions

1. Resolved for V1: round checker is a dedicated semantic role identity. It
   produces post-round evidence, while deterministic scripts write task status.
   See
   [decisions/008-round-checker-separate-planner-rehydrates.md](decisions/008-round-checker-separate-planner-rehydrates.md).
2. What exact schema should represent node status, branch status, and round
   status in `.ccb/runtime/loops/<loop-id>/`?
3. Should `max_node_rework_rounds = 2` and `max_same_failure_signature = 2`
   be global defaults or workflow-spec fields?
4. Which round-check commands should be deterministic shell/test invocations,
   and which may rely on semantic review?
5. Resolved direction: completed sibling work is preserved by importing compact
   partial evidence and letting planner rehydrate from task packet plus round
   evidence for the next loop. The exact branch schema remains open. See
   [topics/round-checker-and-planner-rehydration.md](topics/round-checker-and-planner-rehydration.md).
6. Resolved direction: `rework_node` is only for bounded fixes inside the
   current plan. If the split, dependency graph, acceptance criteria,
   verification contract, or risk model must change, orchestrator/round
   checker should escalate to `partial` or `replan_required`.
7. Resolved V1 direction: `task-import-round` imports explicit round results
   into first-class `round_summary.md` evidence and preserves legacy
   `round_pass`, `round_partial`, `round_replan`, and `round_blocker` aliases
   only for compatibility. The broader branch/node schema remains open.

## Monitoring Questions

1. Which health checks belong to deterministic monitor code versus semantic
   monitor agents?
2. When `ask` or callback state disagrees with pane/provider evidence, which
   state is authoritative for loop escalation?
3. What evidence package should the inner monitor send to `frontdesk` when it cannot
   recover?
4. How should monitor alerts avoid becoming noisy during normal long-running
   provider work?

## Plan-Tree Questions

1. Which loop milestones must sync to `docs/plantree`: accepted plan, ready
   execution task, blocker, completed evidence, or every phase transition?
2. Should durable task packets keep all completed tasks indefinitely under
   `tasks/`, or should old task packets be archived into `history/` after a
   retention threshold?
3. How should a loop produce durable history without turning plan-tree files
   into event logs?
4. For `ccb plan` V1, should `tasks/index.json` be committed durable state or
   treated as generated/machine-owned state that can be rebuilt from task
   directories?
5. Should task ids be time-based, slug-based, or content-hash-assisted to
   balance human readability with collision safety?
6. Resolved V1 default: `execution_contract.md` is mandatory before
   `ready_for_orchestration`. A low-risk synthesized contract is allowed only
   behind an explicit flag and must write provenance.
7. Resolved V1 preference: `orchestration_notes.md` should be imported as
   task evidence, not stored only as loop-local runtime evidence, so planner
   and frontdesk can review semantic route choices from plan-tree.
8. Should `task_packet.md` be an explicit compact artifact beside the current
   task `README.md`, or a generated view over existing imported artifacts?
