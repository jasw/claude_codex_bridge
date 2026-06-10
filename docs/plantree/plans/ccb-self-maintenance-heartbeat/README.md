# CCB Self Maintenance Heartbeat Plan

Date: 2026-06-10

## Purpose

Plan a CCB-owned maintenance heartbeat for periodically waking `ccb_self` to
perform semantic runtime supervision.

The feature belongs to CCB, not to the `agentroles.ccb_self` Role Pack. CCB
owns the scheduler, project policy, wakeup state, locks, diagnostics snapshot,
and next-run cadence. `ccb_self` owns the semantic assessment skill that can
look at CCB evidence and decide whether execution appears healthy, suspicious,
failed, or too ambiguous to judge.

## File Map

- [roadmap.md](roadmap.md): planning sequence and current status.
- [open-questions.md](open-questions.md): unresolved product, safety, and
  implementation questions.
- [topics/semantic-supervision-loop.md](topics/semantic-supervision-loop.md):
  proposed wake, assess, and reschedule model.

## Related Sources

- [../ccb-self-role/README.md](../ccb-self-role/README.md)
- [../../../ccbd-startup-supervision-contract.md](../../../ccbd-startup-supervision-contract.md)
- [../../../ccbd-lifecycle-stability-plan.md](../../../ccbd-lifecycle-stability-plan.md)
- [../../../ccbd-diagnostics-contract.md](../../../ccbd-diagnostics-contract.md)
- [../../../managed-provider-completion-reliability-plan.md](../../../managed-provider-completion-reliability-plan.md)

## Scope

In scope:

- A project-scoped external maintenance tick owned by CCB.
- Programmatic CCB runtime snapshots used as evidence.
- A `ccb_self` running-supervision skill for semantic health assessment.
- A controlled schedule update surface for the next heartbeat time.
- Idle exit behavior so the heartbeat does not keep provider context alive.
- Conservative ambiguity handling that can temporarily shorten the next
  heartbeat interval without running destructive repair.

Out of scope:

- Making `ccb_self` a daemon lifecycle, keeper, or runtime supervision
  authority.
- A provider-side infinite loop inside the `ccb_self` conversation.
- Replacing ccbd's normal configured-agent supervision.
- Fully autonomous project-wide shutdown, force cleanup, restart-all, or broad
  repair.
- Continuing the original business task as `ccb_self` after another agent
  fails.
