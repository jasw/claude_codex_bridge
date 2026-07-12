## task-detail-design.md

# Task Detail Design

task id: <task-id>
detailer: <agent-name>
detail result: local_detail_ready|planner_replan_required|needs_clarification|blocked
detail readiness recommendation: detail_ready|planner_replan_required|needs_clarification|blocked

### Source Evidence

- <source path, test path, decision, or plan ref>

### Detailed Acceptance

- <acceptance item>

### Detailed Verification

- <verification command or deterministic check>

## brief-update-summary.md

# Brief Update Summary

global impact: none|bounded|macro
global impact rationale: <compact rationale>
planner backfill evidence: <none or compact invariant/dependency/decision update>
planner action recommendation: <none|record_bounded_summary|replan_before_execution>

## detail-packet.md

# Detail Packet

task id: <task-id>
declared refs: <task, contract, decision, and source refs>
allowed paths: <project-relative paths>
non-goals: <excluded work>
dependency evidence: <refs or none>
acceptance refs: <refs>
verification refs: <refs>
bounded worker handoff: <implementation packet for later controller dispatch>

This reply is evidence only. The task detailer never dispatches workers,
writes import files, or mutates task authority. Its sole downstream action is
the versioned direct silent Planner ask for `planner_replan_required`.
