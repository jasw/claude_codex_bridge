# Orchestrator Role Capability

Date: 2026-06-24

## Purpose

`orchestrator` is the loop-internal semantic dispatcher. It is activated by
`loop_runner` through `ask` for one execution round or one orchestration batch.
It turns an execution-ready task packet into bounded work items, selects a small
execution-node topology, dispatches constrained work through `ask`, and returns
structured aggregation for round checking or replanning.

It is not a daemon, not a permanent manager, and not the owner of runtime state.
It proposes runtime topology; CCB scripts commit and reconcile topology.

## Activation Model

```text
loop_runner
  -> ask orchestrator
      inputs: task packet, verification contract, loop state refs, node budget
      outputs: work items, dependency graph, topology proposal, dispatch plan,
               partial summary, round-check handoff
```

`orchestrator` should receive references, not large copied context:

- Durable task packet path.
- Planner handoff path.
- Acceptance criteria path.
- Verification contract path.
- Current loop breadcrumb.
- Existing node/branch status refs.
- Current topology and runtime capacity summary.

`orchestrator` may read the referenced documents, reason semantically, and
produce draft artifacts. It must ask CCB scripts to record authoritative state.

## Core Capabilities

### 1. Task Complexity Assessment

Classify the current execution task before slicing:

| Class | Meaning | Default Node Count |
| :--- | :--- | :--- |
| `single` | One bounded implementation path, low cross-module risk | 1 |
| `split_serial` | Several dependent steps; parallelism would create rework | 1-2 |
| `split_parallel` | Independent work items can run safely in parallel | 2-4 |
| `replan_required` | Task is too vague, too broad, or acceptance criteria are not executable | 0 |

Complexity signals:

- Number of affected modules.
- Cross-module dependency risk.
- Need for domain specialization.
- Test surface breadth.
- Whether work items can be independently verified.
- Whether partial branch completion is useful.
- Whether shared files would create merge or semantic conflicts.

Hard limits:

- V1 node count must be between 1 and 4.
- Prefer 1 node unless parallelism clearly reduces risk or wall time.
- If more than 4 nodes seem necessary, return `replan_required` with a smaller
  task-splitting recommendation.

### 2. Work Item Slicing

Produce work items that are:

- Bounded.
- Testable.
- Independently reviewable.
- Traceable to acceptance criteria.
- Small enough for one `coder + checker` node.

Each work item should include:

```json
{
  "work_item_id": "wi-001",
  "title": "Implement config parser guard",
  "goal": "Make project config parsing fail visibly instead of falling back",
  "scope": ["lib/agents/config_loader_runtime"],
  "non_goals": ["rewrite config grammar"],
  "acceptance_refs": ["acceptance-criteria.md#config-visible-failure"],
  "verification_refs": ["verification-contract.md#real-cli-smoke"],
  "depends_on": [],
  "expected_artifacts": ["summary", "changed_files", "tests", "risk_notes"],
  "assigned_node": "node-001"
}
```

### 3. Dependency Graph And Branch Control

Build a small DAG:

```json
{
  "nodes": ["node-001", "node-002"],
  "edges": [["node-001", "node-002"]],
  "branches": [
    {"branch_id": "branch-config", "root_node": "node-001"}
  ]
}
```

When a node is `non_converged`, orchestrator should:

- Freeze that node.
- Freeze dependent downstream nodes.
- Continue unrelated sibling work when safe.
- Return a partial package for planner when the round drains.

It must not downgrade the branch to success or silently remove it from scope.

### 4. Runtime Workflow Graph Proposal

`orchestrator` may propose the execution-round topology, but it must not
directly mutate runtime state.

Allowed:

- Call the planned `orchestrator-topology` skill, which in turn uses
  `ccb loop topology propose/validate/commit/status`.
- Propose a bounded graph of loop-owned agents by declared profile and count.
- Define information-flow edges, call order, artifact refs, and release gates.
- Use committed topology and observed status as the only dynamic ask targets.
- Report node/window placement as evidence only after the reconciler returns
  it.
- Request release by removing or parking agents in topology intent, not by
  killing or unloading them directly.
- Provide reasons, node count, provider/role preferences, expected lifetime,
  failure policies, and release gates.

Disallowed:

- Editing `.ccb/ccb.config` directly.
- Running `ccb reload` directly from the role.
- Killing panes or agents directly.
- Calling `ccb agent add --window`, `ccb agent add --window-class`,
  `ccb agent remove`, or choosing execution-node window names directly.
- Calling `ccb loop capacity ensure/release` directly in the normal topology
  path after topology commands are available.
- Writing `.ccb/runtime/loops/*` authority files directly.
- Bypassing busy unload or provider replacement guards.

Topology proposal shape:

```json
{
  "request_type": "propose_runtime_workflow_graph",
  "reason": "Need two independent coder/checker nodes for parallel branches",
  "node_count": 2,
  "max_node_count": 4,
  "preferred_profiles": ["worker_coder", "reviewer_code"],
  "lifetime": "current_loop_round",
  "edges": [
    {
      "id": "edge-worker-node1",
      "from": "orchestrator",
      "to": "worker_coder_1",
      "type": "ask",
      "order": 10,
      "output_artifact": "node1.worker-result.md"
    },
    {
      "id": "edge-review-node1",
      "from": "worker_coder_1",
      "to": "reviewer_code_1",
      "type": "ask_after",
      "after": ["edge-worker-node1"],
      "order": 20,
      "input_artifact": "node1.worker-result.md",
      "output_artifact": "node1.review.md"
    }
  ],
  "release_gates": [
    {
      "agents": ["worker_coder_1", "reviewer_code_1"],
      "condition": "artifacts_imported && agents_idle",
      "policy": "auto"
    }
  ],
  "fallback": "return replan_required or run fixed configured worker/reviewer serially only when planner policy allows"
}
```

`ccb loop topology` owns validation and desired-state commit. The topology
reconciler owns translating committed desired topology into capacity records,
guarded reload, lifecycle records, window creation, pane placement, idle
release, or rejection. Current CCB has proven loop-generated worker/checker
placement in `node-<loop-id>-<node-id>` windows for explicit `[windows]`
layouts; the topology reconciler should reuse that substrate. `orchestrator`
may inspect `ccb loop topology status --json` and `ccb layout status --json`
as read-only diagnostic views, but it must not use those views to hand-pick
targets or repair tmux state.

### 5. Ask Dispatch

`orchestrator` dispatches work through `ask`, but every ask must be constrained.

Each worker ask should include:

- Work item id.
- Goal.
- Scope and non-goals.
- Acceptance refs.
- Verification refs.
- Forbidden degradation rules.
- Expected output schema.
- Artifact refs instead of large copied text where possible.
- Time/retry limits inherited from loop state.

Each checker ask should include:

- Work item id.
- Worker result refs.
- Acceptance refs.
- Verification refs.
- Fallback/degradation audit requirement.
- Expected status: `pass`, `rework`, `blocked`, or `non_converged`.

`orchestrator` should record submitted ask refs through script-owned runtime
commands such as `ccb loop ask-record`; it must not hand-edit `asks.jsonl`.

### 6. Aggregation

After node results return, orchestrator produces:

```text
orchestration_summary
  completed_nodes
  rework_nodes
  blocked_nodes
  non_converged_nodes
  frozen_branches
  drained_sibling_work
  dependency_graph
  changed_surfaces
  evidence_refs
  round_checker_handoff
```

If the round is partial, produce:

```text
partial_loop_report
  completed_nodes
  non_converged_nodes
  blocked_downstream_nodes
  skipped_nodes
  failed_assumptions
  recommended_replan_options
```

## Explicit Non-Authority

`orchestrator` must not:

- Modify durable plan-tree state directly.
- Modify runtime authority files directly.
- Start or stop providers directly.
- Confirm user-facing scope changes.
- Lower acceptance criteria.
- Convert partial work into `done`.
- Override checker or round checker quality gates.
- Expand beyond the configured 1-4 node budget.

## V1 Cut

V1 should move to the topology path:

```text
planner -> loop_runner -> ask orchestrator
orchestrator -> ccb loop topology propose/commit
loop_runner/reconciler -> ccb loop topology reconcile
orchestrator or loop_runner -> ask committed worker target
orchestrator or loop_runner -> ask committed reviewer target
orchestrator -> aggregate
loop_runner/reconciler -> ccb loop topology release/reconcile
checker or round_checker -> verify
planner/frontdesk -> receive partial/replan only when needed
```

The existing `ccb loop capacity` path remains useful as the lower-level
substrate and for compatibility/debugging, but it is no longer the preferred
orchestrator contract.

## Role Pack Guidance

The `orchestrator` Role Pack should include:

- Role memory describing purpose, authorities, non-authorities, and V1 limits.
- A dispatch skill for work item slicing and ask payload generation.
- A topology skill for producing structured runtime workflow graph proposals
  without direct runtime mutation or hand-picked placement.
- Templates for work items, dependency graphs, worker asks, checker asks,
  topology proposals, orchestration summaries, and partial loop reports.
- References to:
  - `topics/plan-and-runtime-list-structure.md`
  - `topics/execution-node-and-round-verification.md`
  - `topics/state-and-script-contract.md`
  - `docs/plantree/plans/ccbd-agent-hot-reload/roadmap.md`
