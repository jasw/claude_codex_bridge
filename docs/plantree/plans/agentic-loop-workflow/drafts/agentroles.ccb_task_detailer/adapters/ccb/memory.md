# CCB Adapter Notes For Task Detailer

Produce task-local detail artifacts for `ccb plan task-artifact` import. Do not
edit task status, task indexes, `current_loop`, runtime topology, provider
state, or tmux state directly.

If detail needs macro plan changes, produce a macro-adjustment request as an
artifact; scripts and planner decide whether to apply it.
