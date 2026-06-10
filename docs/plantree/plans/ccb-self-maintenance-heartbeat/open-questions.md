# CCB Self Maintenance Heartbeat Open Questions

Date: 2026-06-10

## Product

1. Should the heartbeat be disabled by default, opt-in per project, or enabled
   only when `ccb_self` is configured?
2. What default cadence is acceptable for healthy projects, and what minimum
   cadence prevents noisy self-wakeup loops?
3. Should heartbeat results be shown in `ccb doctor`, sidebar status, both, or
   only in diagnostics files at first?

## Authority And Safety

1. Which CCB command is allowed to update `next_run_at`, interval, and reason?
2. Should `ccb_self` be allowed to request schedule changes directly through a
   control-plane command, or should it only return structured advice that CCB
   validates and applies?
3. What actions remain report-only unless the user has separately enabled
   autonomous repair policy?
4. How should CCB avoid duplicate wakeups when `ccb_self` already has a
   maintenance job queued or running?

## Implementation

1. Should the scheduler live in `ccbd`, keeper, an external CLI timer, or a
   hybrid where an external timer invokes a project-scoped CCB tick command?
2. Where should schedule policy live, separate from diagnostics heartbeat
   evidence that is explicitly not lifecycle authority?
3. What snapshot fields are required for semantic assessment without making the
   prompt too large?
4. How should `unknown` results be capped so they can shorten the next interval
   without creating unbounded loops?
5. What is the fallback when `ccb_self` itself is missing, degraded, busy, or
   unable to answer?
