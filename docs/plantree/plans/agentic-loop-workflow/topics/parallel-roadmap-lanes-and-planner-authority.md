# Parallel Roadmap Lanes And Planner Authority

Date: 2026-07-10
Status: Design accepted; implementation not started

## Purpose

Define how Plan Tree, planner, runtime lanes, worktrees, and integration gates
manage serial and parallel project work without creating multiple competing
plan authorities.

The central rule is:

> Parallelism belongs to the roadmap and lane model first. Planner instance
> count is a later scaling choice.

## Current Baseline

Current source already provides useful lower-level pieces:

- per-task file locks;
- one loop lease per running task;
- dynamic role profiles and bounded instance counts;
- generated loop/agent identities;
- worker Git-worktree support;
- mount topology and dynamic release.

Current source remains serial at project scope:

- `loop_runner_auto` holds one project-wide `auto-runner.lock`;
- `find_first_actionable_task` returns one selected task;
- the planner role design assumes one macro planner;
- isolated worker changes are promoted toward one shared project root;
- no lane registry, ready-frontier scheduler, scope-claim authority, or merge
  queue exists.

These constraints must be treated as implementation gaps, not hidden by
raising planner or worker `max_instances`.

## Three Planning And Execution Graphs

### Roadmap Graph

Durable macro graph under Plan Tree:

```text
goal
  -> milestone-1
      -> feature-a-1 -> feature-a-2 --+
      -> feature-b-1 -> feature-b-2 --+-> integration-ab -> milestone-2
```

It owns:

- macro goals and milestones;
- serial dependencies;
- parallel branch eligibility;
- priority and pause/resume intent;
- semantic scope claims and cross-lane conflicts;
- join nodes and integration acceptance;
- mapping from roadmap node to plan/task refs.

### Orchestration Graph

One accepted macro task may contain a smaller worker DAG. Decision 022 keeps
that graph in one orchestration bundle. It cannot add, remove, or reorder
Roadmap Graph nodes without a planner-owned graph change.

### Git/Worktree Graph

Controller projection of executable roadmap branches into concrete branches,
worktrees, base commits, integration order, and merge evidence. Plan Tree
describes semantic parallelism; controller decides safe physical materialization.

## Durable Representation

Keep human semantics and machine scheduling separate:

```text
docs/plantree/plans/<plan>/
  roadmap.md              # human goals, rationale, phases, tradeoffs
  roadmap.graph.json      # script-owned scheduling graph
  brief.md
  tasks/
```

Suggested graph node:

```json
{
  "id": "feature-a",
  "type": "workstream",
  "status": "ready",
  "priority": 50,
  "depends_on": [],
  "parallel_group": "milestone-1",
  "scope_claim_refs": ["scope-claims/feature-a.json"],
  "join_at": "integration-ab",
  "workspace_policy": "isolated",
  "acceptance_refs": ["acceptance.md#feature-a"],
  "plan_ref": "../feature-a/README.md"
}
```

Initial join semantics should stay simple: all required predecessor nodes must
complete. More general `any` or quorum joins are deferred until a real use case
requires them.

## Workflow Lane

A lane is a runtime binding for one active Roadmap Graph branch:

```text
.ccb/runtime/lanes/<lane-id>/
  lane.json
  planner-lease.json
  scope-claims.json
  dependencies.json
  capacity.json
  integration-contract.json
  runner.lock
```

Minimum identity carried by every ask, callback, import, and evidence record:

```text
project_id
plan_id
lane_id
roadmap_node_id
task_id
loop_id
round_id
activation_id
plan_revision
task_revision
base_commit
lease_fence
```

Missing or mismatched identity rejects the write. A late provider reply cannot
mutate a reassigned lane after its fencing token changes.

## Ready Frontier

The project scheduler computes executable nodes without semantic inference:

```text
ready_frontier = nodes where
  status is ready
  and all dependencies are complete
  and no active scope claim conflicts
  and required capacity is available
  and the lane is not paused
```

The scheduler may start all safe frontier nodes concurrently. It does not
rewrite dependencies, lower acceptance, shrink scope, or serialize a declared
parallel branch silently. Capacity or conflict prevents admission and produces
an explicit waiting reason.

## Serial And Parallel Semantics

| Condition | Scheduling result |
| :--- | :--- |
| `depends_on` predecessor incomplete | Serial wait |
| Independent scope and sufficient capacity | Parallel admission |
| Read/read scope overlap | Parallel admission |
| Read/write overlap | Admit only with pinned revision and stale-read policy |
| Write/write overlap | Serialize, combine, or request planner decision |
| Shared schema, public interface, release metadata, or migration | Exclusive claim or global review |
| Required join predecessors complete | Enter integration gate |

File-path comparison is insufficient. Claims should cover files, modules,
interfaces, schemas, commands, generated outputs, test resources, ports,
databases, and release surfaces.

## Planner Model

### Default: one global planner

One planner can maintain many roadmap branches because it is not active during
normal execution. It creates or revises graph nodes, then controller code
advances them.

Planner activates only for:

- new macro intake;
- branch creation, cancellation, or reprioritization;
- semantic scope conflict;
- partial or replan result;
- global-impact request;
- failed integration gate;
- next-milestone decision.

Normal worker/reviewer success and dynamic release update script-owned
projections without another planner call.

### Optional: scoped lane planners

Multiple planner instances are justified only when planning queue latency,
independent domains, multiple users, or long-running research become measured
bottlenecks.

Rules:

- one active planner writer per plan root or explicit lane scope;
- one global planner writer for portfolio and shared architecture;
- lane planners write only their own plan root;
- global changes use `portfolio-change-request` artifacts;
- planner conversations remain lane-scoped even when they share one RolePack;
- no planner holds a file lock while a provider is reasoning.

The same `agentroles.ccb_planner` RolePack may support `portfolio` and `lane`
activation modes. A new required role id is not needed until behavior or
permissions prove materially different.

## Plan Authority And Optimistic Writes

Planner reads revision `R`, produces a proposal, and imports it with
`expected_revision=R`. Scripts apply atomically only when the revision and
lease fence still match.

```text
read revision 42
  -> provider reasons without holding a lock
  -> submit update expected_revision=42
  -> apply as revision 43, or reject plan_revision_conflict
```

Global roadmap and architecture have one writer. Lane status dashboards should
be generated from lane authority rather than repeatedly rewritten by every
planner.

## Plan Control And Code Worktrees

Planner instances do not need separate code worktrees. Plan Tree is one durable
control plane with scoped document ownership. A production concurrent setup may
use one shared plan-control checkout or worktree so the user's main checkout
stays clean, but it must not create divergent Plan Tree copies per planner.

Executable lanes use code worktrees:

| Roadmap node | Workspace policy |
| :--- | :--- |
| Planning/document authority only | No code worktree |
| Serial implementation in one lane | Reuse lane worktree when safe |
| Independent concurrent implementation | Separate lane worktree |
| Exclusive shared surface | Serialize or create a joint lane |
| Integration join | Dedicated integration worktree |

Workers receive an immutable execution snapshot containing plan/task revision,
base commit, task envelope, acceptance refs, and digests. A moving global plan
cannot silently alter an active execution round.

## Integration Gate

Lane-local pass means `implementation_done`, not global `done`.

```text
lane A reviewed --+
                  +-> merge queue -> integration worktree -> combined tests
lane B reviewed --+                                      -> global done
```

Integration is intentionally serialized for a shared target branch. The gate
records merge commits, conflict handling, combined verification, accepted plan
revision, and unresolved global impact.

## Project Scheduler And Locks

Replace one long project-wide runner lock with:

- a short project scheduler transaction lock;
- one runner lock per lane;
- existing task locks and loop leases;
- short topology/capacity transactions;
- one integration lock per target branch.

Provider calls never hold the project scheduler lock. One lane may plan while
another executes, reviews, waits for integration, or releases agents.

## UI Projection

Keep role-class windows, but label every dynamic pane and sidebar item with its
lane:

```text
[A] planner
[A] orchestrator
[A] coder-1
[B] planner
[B] coder-1
```

Window 1 remains frontdesk/detail interaction, Window 2 holds planner and
planning-stage immaculate roles, and Window 3+ holds execution roles. Existing
six-pane overflow rules can create additional role-class windows. The sidebar
should group or filter by lane so visibility does not become cross-lane
ambiguity.

## Implementation Sequence

1. Add Roadmap Graph schema, validator, revision, and cycle checks.
2. Add lane registry, identity propagation, planner lease, and fencing tokens.
3. Add scope claims and deterministic conflict admission.
4. Replace first-actionable selection with ready-frontier scheduling.
5. Split project auto-runner lock into scheduler and lane locks.
6. Add lane worktree and immutable execution-snapshot materialization.
7. Add integration queue, integration worktree, and combined verification.
8. Add lane-aware topology names, UI/sidebar projection, and capacity fairness.
9. Prove two disjoint real lanes, then conflict, dependency, crash, stale
   callback, capacity, and integration cases.
10. Measure planner queue latency before enabling multiple planner writers.

## Acceptance Criteria

- Two disjoint ready roadmap branches plan once and execute concurrently.
- One global planner remains responsive while lane execution is active.
- Same-scope write claims cannot run concurrently without an explicit plan.
- No ask, callback, artifact, worktree change, or topology record crosses lane
  identity.
- A stale planner/provider reply is rejected by revision or fencing checks.
- One lane failure does not stop an unrelated lane.
- Parallel lane results cannot become globally done before the join gate and
  combined verification pass.
- Main checkout and Plan Tree authority remain inspectable while code changes
  stay isolated in lane worktrees.
- Dynamic agents release per lane without unloading another lane's active
  agents.

## Related

- [../decisions/023-roadmap-graph-and-workflow-lanes.md](../decisions/023-roadmap-graph-and-workflow-lanes.md)
- [planner-role-design.md](planner-role-design.md)
- [state-and-script-contract.md](state-and-script-contract.md)
- [plan-and-runtime-list-structure.md](plan-and-runtime-list-structure.md)
- [semantic-orchestration-and-controller-boundary.md](semantic-orchestration-and-controller-boundary.md)
