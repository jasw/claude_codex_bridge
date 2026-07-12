# CCB Adapter Notes For Planner

Use reply-visible artifacts as the durable boundary. Prefer producing
`task-packet.md`, `readiness.json`, and `candidate-questions.jsonl` sections for
the supervisor/runner to import or review.

The active command surface is closed. Do not run shell commands, file searches,
file reads, tests, builds, or CCB commands from the provider session. Base your
reply on the Frontdesk-authored intake, compact artifacts, and prompt context.
Infer single-task versus task-set output from that intake: use task-set only
for explicit independent deliverables, distinct routes, or route-mix work.

When the prompt declares `detailer_replan` or `task_set_closure`, use the
`planner-closure-backfill` skill instead of the initial task-packet shape.
Return the single parser-stable fenced proposal only. It contains the complete
structured Frontdesk status envelope. The host validates expected
PlanTree revision and performs every file write or Frontdesk delivery.

The supervisor/runner imports only exact fenced sections. Return
`**task-packet.md**` followed by a fenced markdown block and `**readiness.json**`
followed by a fenced JSON object. Do not use alternate section names, unfenced
JSON, or prose-only blocker summaries.

Never run `ccb plan task-create`, `ccb plan task-artifact`, `ccb plan
task-status`, `ccb plan breadcrumb`, `ccb loop`, `ccb ask`, `ccb_test`, or
wrapper commands from the provider session. Those commands mutate or route
authority and are owned by the supervisor/runner script, not planner.

Never edit `.ccb/runtime`, `.ccb/agents`, `current_loop`, lease, socket, pid,
mailbox, pane, provider-state, or tmux state files directly.
