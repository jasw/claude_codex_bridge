# Agentic Loop Workflow Implementation Status

Date: 2026-07-02

## Current Phase

The workflow kernel is now beyond pure planning. The current source tree has a
working one-shot candidate path, but it has not yet implemented the revised
orchestrator-triage-before-detailer runtime:

```text
draft task
  -> planner role activation and explicit bundle import
  -> ccb_task_detailer bundle import and detail_ready gate
  -> plan reviewer activation and review import
  -> script-validated ready
  -> one execution round
  -> dynamic worker + code_reviewer capacity
  -> round result import
  -> auto release
```

This is still an opt-in candidate path, not a default project workflow daemon.

Target next runtime shape:

```text
draft task
  -> planner role activation and explicit bundle import
  -> orchestrator triage
      -> direct execution
      -> needs_detail -> ccb_task_detailer -> orchestrator
      -> macro_adjustment_request -> planner
  -> worker/reviewer execution round
```

## Last Landed

- V1 planner brief + task_detailer detail-packet import slice:
  `ccb plan task-artifact` now accepts `brief`, `detail_design`,
  `detail_summary`, `detail_packet`, and `macro_adjustment_request`; planner
  bundles may import compact `brief.md` and macro task-packet artifacts only;
  task_detailer bundles may import task-scoped detail docs, a detail packet
  manifest, and optional macro adjustment requests.
- `detail_ready` now requires the task_detailer three-piece packet:
  `detail_design`, `detail_summary`, and `detail_packet`. A
  `macro_adjustment_request` is preserved as an artifact/ref and does not apply
  planner authority or advance status by itself.
- `ccb loop runner --once --consume-role-output` consumes explicit planner and
  plan-reviewer JSON bundles from ask/watch replies; it now also consumes
  task_detailer bundles and advances through the script-owned `detail_ready`
  gate.
- Planner bundles are committed through `ccb plan task-artifact`; accepted
  artifact kinds are `requirements`, `acceptance`, `verification`, `risk`, and
  `handoff`.
- Plan-reviewer bundles import `review` and may request `ready`, but the state
  transition still goes through existing `ccb plan task-status` validation.
- The default `ccb loop runner --once` remains submit-only for planner and
  plan-reviewer activations.
- The fake provider now supports deterministic workflow replies for planner,
  ccb_task_detailer, plan-reviewer, and round-checker `round result: pass`
  smoke.
- `scripts/workflow_closure_smoke.py` now includes the current pre-triage
  ccb_task_detailer stage in the official fake-provider closure smoke.
- `ccb loop topology` now applies the CCB workflow window contract by default:
  V1 resident `ccb_frontdesk` and `ccb_task_detailer` land in `ccb-user`;
  V1 resident `ccb_planner` and `ccb_orchestrator` land in `ccb-plan`;
  on-demand `ccb_round_reviewer` also lands in `ccb-plan` when round-review
  topology needs it; active `coder + code_reviewer` agents pack six panes per
  `ccb-exec` page and compact overflow pages during reconcile.
- Topology reconcile stages missing desired agents as one lifecycle add batch
  before mounted reload, releases absent agents before compaction moves, and
  dynamic overlay-created runtime windows now use append-compatible layout
  specs so an existing `ccb-exec` page can grow from one work pair to two
  without forcing a context-losing pane rebuild.
- Follow-up audit fixed the shrink/compaction observed-state summary: when a
  later desired topology omits a previously mounted execution pair, reconcile
  now reports the removed agents through `released_count` and
  `released_agents` instead of only compacting the config.
- Worker2 dispatch-contract review identified topology-driven dispatch as a
  release blocker and added contract coverage. Follow-up validation now rejects
  unsupported topology edge types and legacy workflow profile aliases such as
  `worker`, `checker`, `round_checker`, `ccb_worker`, `ccb_checker`, and bare
  planner/orchestrator/detailer aliases before runtime reconciliation.
- Minimal topology-driven dispatch is landed for `ccb loop runner --once`:
  when a task is already bound to a loop with a committed topology graph, the
  runner validates fresh drift-free observed topology and dispatches supported
  `ask` / `ask_after` edges sequentially by `order` and `after`, before the
  legacy fixed `coder + code_reviewer` fallback is considered.
- The topology dispatcher writes structured runtime evidence under the loop:
  `topology_dispatch.json`, `topology_dispatch.events.jsonl`, per-edge reply
  artifacts, and a round-compatible `round.json` that is imported through
  existing `plan task-import-round` authority.
- Runner-side graph guards now reject unknown edge types, missing source/target
  agents, stale observed revisions, observed drift, non-ready agents, missing
  dependencies, and dependency cycles before ask submission.
- Follow-up audit tightened topology ask dispatch readiness: only observed
  `present` agents with visible lifecycle may receive `ask` / `ask_after`
  edges. Hidden, parked, absent, retained, stale, or drifted agents are
  rejected before ask submission.
- The fake provider now recognizes `ccb_round_reviewer` topology asks and
  returns deterministic `round result: pass` evidence for topology dispatch
  smokes.

Evidence:

- [history/workflow-role-output-import-2026-07-02.md](history/workflow-role-output-import-2026-07-02.md)
- [goals/minimum-production-candidate-goal.md](goals/minimum-production-candidate-goal.md)

## Active TODO

1. Add or import a formal `agentroles.ccb_task_detailer` RolePack. The current
   closure smoke mounts `ccb_task_detailer` as a distinct agent name but reuses
   the planner draft rolepack as a temporary deterministic test placeholder.
2. Implement the revised runtime flow from
   [decisions/019-orchestrator-triage-before-task-detailer.md](decisions/019-orchestrator-triage-before-task-detailer.md):
   planner output must enter orchestrator triage first; `ccb_task_detailer`
   is resident in the V1 topology but should receive task work only when
   triage returns `needs_detail`, and detail output must return to orchestrator
   before worker/reviewer dispatch.
3. Define the planner-side review/merge policy for `detail_summary` and
   `macro_adjustment_request` so plan brief updates remain compact and
   macro-only.
4. Broaden the topology dispatcher beyond the minimal V1 sequential executor:
   add explicit release-gate execution, richer artifact import policy,
   conditional/rework handling, and runner-owned reconcile calls before and
   after dispatch.
5. Align the external Agent Roles spec with the current CCB workflow names:
   `agentroles.ccb_frontdesk`, `agentroles.ccb_planner`,
   `agentroles.ccb_orchestrator`, `agentroles.ccb_task_detailer`,
   `agentroles.ccb_round_reviewer`, `agentroles.coder`, and
   `agentroles.code_reviewer`. Do not reintroduce `ccb_worker`,
   `ccb_checker`, `planner-task`, or bare workflow role aliases in the CCB
   topology contract.
6. Update or retire historical fixed-capacity workflow closure smoke paths that
   still mount `agentroles.ccb_worker`, `agentroles.ccb_checker`, or
   `agentroles.ccb_round_checker`, so release gates do not preserve rejected
   role aliases outside the topology contract.
7. Define the first plan-brief import/update path so planner can maintain a
   compact `brief.md` from `ccb_task_detailer` stable summaries without owning
   detail design documents. Initial `brief`/`detail_summary` artifact import
   is landed; stable-summary review and merge policy remains.
8. Define the next opt-in real-provider gate for Codex/Claude without making
   real provider auth mandatory for source CI.

## Blocked By

The minimal topology dispatch slice is no longer blocked on agent/window
reconciliation. Remaining blockers are the revised orchestrator-triage flow,
release-gate automation, default enablement policy, real-provider gates, and
user-facing workflow UI.

## Last Verified

- `python -m py_compile lib/cli/services/loop_topology.py
  lib/cli/services/agent_lifecycle.py
  lib/agents/config_loader_runtime/dynamic_agent_overlays.py
  lib/agents/config_loader_runtime/loop_overlays.py
  test/test_loop_topology_cli.py`
  -> passed.
- `python -m pytest test/test_loop_topology_cli.py
  test/test_agent_lifecycle_cli.py test/test_agent_window_reflow.py
  test/test_loop_capacity_cli.py test/test_pane_growth_layout.py
  test/test_loop_topology_dispatch_contract.py -q`
  -> `87 passed`.
- `python -m pytest test/test_loop_topology_dispatch_contract.py
  test/test_loop_topology_cli.py test/test_loop_capacity_cli.py
  test/test_workflow_closure_smoke_script.py -q`
  -> `50 passed`, with no topology dispatch xfail remaining.
- `git diff --check`
  -> clean.
- Source-wrapper diagnose from `/home/bfly/yunwei/test_ccb2`:
  `HOME=/home/bfly/yunwei/test_ccb2/source_home
  CCB_SOURCE_HOME=/home/bfly/yunwei/test_ccb2/source_home
  /home/bfly/yunwei/ccb_source/ccb_test --diagnose`
  -> wrapper/source checkout and allowed test root verified.
- Source-wrapper topology validator smoke:
  `/home/bfly/yunwei/test_ccb2/topology-validator-smoke-20260702230740`
  rejected unknown edge type `direct_tmux_mutation` and legacy profile
  `worker` with source `ccb_test`, returning `validator_smoke_status=ok`.
- Standalone single-window smoke from `/home/bfly/yunwei/test_ccb2`:
  `/home/bfly/yunwei/ccb_source/ccb_test layout dynamic-smoke --panes 6
  --window-prefix ccb-exec --json`
  -> `smoke_status=ok`, `layout_status=ok`, `dynamic_status=ok`,
  `cleanup_status=ok`, `event_count=11`.
- Live source-wrapper topology smoke:
  `/home/bfly/yunwei/test_ccb2/topology-window-smoke-final-20260702220620`
  with isolated `HOME`/`CCB_SOURCE_HOME` and fake provider roles.
  `config validate` and start passed; the earlier one-pair topology included
  an on-demand round reviewer and reconciled to
  `ccb-user=[bootstrap,wf-ccb-frontdesk,wf-ccb-task-detailer]`,
  `ccb-plan=[wf-ccb-planner,wf-ccb-orchestrator,wf-ccb-round-reviewer]`,
  and `ccb-exec=[wf-coder-1,wf-code-reviewer-1]`.
- Same live smoke then grew `ccb-exec` to two work pairs on the mounted
  runtime. Reconcile returned no drift and applied adds for `wf-coder-2` and
  `wf-code-reviewer-2`; ask smoke completed for both new agents with exact
  fake-provider replies.
- Same live smoke then committed compact topology with the second pair absent.
  Reconcile released `wf-coder-2` and `wf-code-reviewer-2`, retained count was
  `0`, drift was empty, final layout returned to one execution pair, and
  cleanup reached `kill_status: ok`.
- Source-wrapper auto-release smoke:
  `/home/bfly/yunwei/test_ccb2/topology-auto-release-smoke-20260703061442`
  committed a two-work-pair topology and then a one-work-pair desired
  topology. Reconcile returned `released_count=2`,
  `released_agents=[wf-code-reviewer-2,wf-coder-2]`, observed topology status
  was `ready`, both released agents had `lifecycle_state=unloaded`, and
  `.ccb/ccb.config` no longer contained the released agents.
- `python -m pytest test/test_plan_tasks_cli.py test/test_loop_capacity_cli.py
  test/test_workflow_closure_smoke_script.py -q`
  -> `39 passed`.
- `python -m py_compile lib/cli/services/plan_tasks.py
  lib/cli/services/loop_runner.py lib/provider_execution/fake.py
  scripts/workflow_closure_smoke.py`
  -> passed.
- `pytest -q test/test_loop_capacity_cli.py::test_loop_runner_once_dispatches_committed_topology_edges_in_order
  test/test_loop_capacity_cli.py::test_loop_runner_topology_dispatch_rejects_invalid_runtime_graphs`
  -> `7 passed`, including hidden/parked target rejection.
- `pytest -q test/test_loop_capacity_cli.py test/test_loop_topology_cli.py
  test/test_workflow_closure_smoke_script.py`
  -> `52 passed`.
- `python -m pytest test/test_loop_capacity_cli.py test/test_loop_topology_cli.py
  test/test_workflow_closure_smoke_script.py test/test_loop_topology_dispatch_contract.py
  test/test_agent_lifecycle_cli.py test/test_agent_window_reflow.py
  test/test_pane_growth_layout.py -q`
  -> `98 passed`.
- `python -m pytest test/test_plan_tasks_cli.py test/test_loop_capacity_cli.py
  test/test_loop_topology_cli.py test/test_loop_topology_dispatch_contract.py
  test/test_workflow_closure_smoke_script.py -q`
  -> `68 passed`.
- `python -m compileall -q lib/cli/services/topology_dispatch.py
  lib/cli/services/loop_runner.py lib/provider_execution/fake.py`
  -> passed.
- Source-wrapper topology dispatch smoke from `/home/bfly/yunwei/test_ccb2`:
  `/home/bfly/yunwei/test_ccb2/topology-dispatch-smoke-20260702231020`.
  With isolated `HOME`, `CCB_SOURCE_HOME`, local fake role store, and
  `/home/bfly/yunwei/ccb_source/ccb_test`, `loop topology propose`, `commit
  --apply`, start, `plan task-bind-loop`, and `loop runner --once` completed.
  Runtime evidence showed `dispatch_status=ok` and ordered completed edges
  `coder-ask -> wf-coder-1`, `reviewer-ask -> wf-code-reviewer-1`, and
  `round-review -> wf-ccb-round-reviewer`; the round reviewer returned
  `round result: pass`, task status became `done`, and cleanup reached
  `kill_status: ok`.
- `python -m pytest test/test_loop_capacity_cli.py test/test_plan_tasks_cli.py test/test_loop_topology_cli.py test/test_workflow_closure_smoke_script.py -q`
  -> `50 passed`.
- Focused bridge/fake-provider verification:
  `test/test_loop_capacity_cli.py`,
  `test/test_v2_execution_service.py::test_execution_service_completes_fake_provider_jobs`,
  `test/test_v2_ccbd_dispatcher.py::test_dispatcher_persists_completion_items_and_state_updates_for_fake_provider`
  -> `24 passed`.
- Source-wrapper bridge smoke from `/home/bfly/yunwei/test_ccb2`:
  `/home/bfly/yunwei/test_ccb2/planner-bridge-smoke-20260702`
  advanced `draft -> imported_planner_output -> imported_plan_reviewer_output
  -> ready -> ran_one_round -> done`, released both dynamic agents, and
  cleaned up with `kill_status: ok`.
- Existing workflow closure smoke regression:
  `/home/bfly/yunwei/test_ccb2/agentic-loop-v1-smoke-20260702162851`
  returned `workflow_smoke_status=ok`, `task_detailer_imported=true`,
  `task_detailer_detail_ready=true`, `final_status=done`,
  `round_result=pass`, `release_status=released`, `retained_count=0`, and
  cleanup reached `kill_status: ok`.

## Handoff Notes

The hard boundary remains script authority. Agents may propose artifacts and
readiness through explicit bundles, but scripts decide whether to import them
and whether status transitions are valid. Do not add Markdown guessing or
direct index mutation as a shortcut for planner convenience.

The design boundary is now stricter, but runtime still needs the corresponding
patch: orchestrator should triage before activating detailer; detailers surface
macro drift through `macro-adjustment-request`; only planner may review the
request and convert it into script-owned plan-tree updates.

Planner should work primarily through a compact plan brief: macro objective,
phase, active roadmap item, constraints, decision/open-question summaries,
detail links, current task entry, readiness, verification summary, and next
owner. Task-scoped detail docs and per-task executable packets should be
maintained by `ccb_task_detailer` only after orchestrator asks for refinement, then
summarized back into the brief or task document by script-owned import.
