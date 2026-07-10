# agentroles.ccb_task_detailer

Draft accepted RolePack for immaculate task-local detail refinement.

The task detailer converts a macro task packet into a detailed execution packet
and supporting evidence. It also returns compact
`global impact: none|bounded|macro` and planner-backfill evidence. It never
dispatches workers, submits asks, mutates authoritative task state, runs CCB
authority commands, writes supervisor import files, or rewrites durable macro
plans directly.
