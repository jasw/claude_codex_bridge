# Open Questions

Date: 2026-06-16

1. What are the target p50/p95 budgets for click-to-pane-focus and
   click-to-stable-sidebar-refresh?
2. Which provider mix should define the default performance profile: Codex-only,
   all configured providers mounted idle, or mixed active providers?
3. How much first-use provider latency is acceptable if startup switches from
   eager mount to foreground-first or lazy mount?
4. Which part of the current `shell-system` bucket is actually controllable by
   CCB: tmux commands, ask CLI subprocesses, shell wrappers, terminal frontend,
   or unrelated desktop/system work?
5. Should high-throughput `ask` workloads use a persistent client/forwarder, or
   should the CLI remain process-per-call with lower-level batching only?
