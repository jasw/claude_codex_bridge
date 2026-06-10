# CCB Self Maintenance Heartbeat Roadmap

Date: 2026-06-10

## Done

- Accepted the boundary correction: heartbeat scheduling, wake policy, and
  next-run state belong to CCB rather than to the `ccb_self` Role Pack.
- Promoted the external heartbeat idea from the general ideas inbox into this
  CCB-level planning root.

## In Progress

- Shape the semantic supervision loop and authority boundary before
  implementation.

## Next

1. Define the project-scoped tick command and schedule policy surface.
2. Define the diagnostics snapshot that CCB passes to `ccb_self`.
3. Define the `ccb_self` running-supervision skill input/output contract.
4. Define cadence rules for healthy, suspicious, failing, and unknown states.
5. Define duplicate-wakeup, busy-agent, and unavailable-`ccb_self` handling.
6. Add tests for idle exit, ambiguous shortening, failure escalation, and
   schedule update validation.

## Deferred

- Automatic mutating repairs beyond explicit low-risk policy.
- Always-on provider-side self loops.
- Project-wide shutdown or force cleanup from heartbeat logic.
- Multiple maintenance roles with arbitration.
