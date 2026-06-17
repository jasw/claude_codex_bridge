# CCB Runtime Performance Plan

Date: 2026-06-16

## Purpose

Track startup, high-load, and interactive-latency performance work for CCB
across the full lifecycle. This plan is broader than the Rust helper plan: it
attributes cost across CCB core, shell/tmux orchestration, provider processes,
and UI/pane switching paths before selecting implementation work.

## Scope

In scope:

- Startup CPU, wall time, and process count attribution.
- Sustained high-load ask/queue behavior and provider mix attribution.
- Pane switching, sidebar/project-view refresh, and click-to-focus latency.
- Provider mount, idle, and lifecycle policies when they dominate CPU or
  memory.
- Shell/tmux subprocess and polling overhead inside CCB orchestration.
- Cross-reference to Rust helper evidence where it changes a measured hot path.

Out of scope for the first phase:

- A full rewrite of `ccbd` or the Python control plane.
- Replacing provider CLI internals.
- Optimizing based only on synthetic microbenchmarks without lifecycle
  attribution.

## Current Finding

The first real lifecycle profile shows CCB core is not the dominant CPU cost:

- Startup: CCB core `16.5%`, provider `24.1%`, shell/tmux/system `56.0%`.
- High load: CCB core `17.3%`, provider `9.3%`, shell/tmux/system `72.6%`.

This points first to shell/tmux/subprocess overhead, provider lifecycle policy,
and interactive refresh isolation rather than broad CCB-core rewrites.

## Reading Path

1. [roadmap.md](roadmap.md)
2. [implementation-status.md](implementation-status.md)
3. [history/real-lifecycle-cpu-profile-2026-06-16.md](history/real-lifecycle-cpu-profile-2026-06-16.md)
4. [history/shell-system-bucket-split-2026-06-16.md](history/shell-system-bucket-split-2026-06-16.md)
5. [topics/startup-and-runtime-low-latency-plan.md](topics/startup-and-runtime-low-latency-plan.md)
6. [topics/candidate-commit-scope-2026-06-16.md](topics/candidate-commit-scope-2026-06-16.md)
7. [open-questions.md](open-questions.md)

## Related Plans

- [python-rust-hybrid-performance](../python-rust-hybrid-performance/README.md)
  covers Rust helper hot-path replacements. This plan uses those results as
  local evidence but owns lifecycle-level prioritization.
- [managed-tool-windows](../managed-tool-windows/README.md) and
  [windows-wezterm-native](../windows-wezterm-native/README.md) may affect
  terminal/UI latency and non-tmux backend choices.
