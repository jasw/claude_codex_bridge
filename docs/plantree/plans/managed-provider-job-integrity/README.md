# Managed Provider And Job Integrity

Date: 2026-07-20

## Purpose

Coordinate the correctness repairs identified while reviewing PR257 through
PR266. The affected behavior crosses provider asset projection, native session
resume, provider-turn binding, job execution diagnostics, cancellation, and
active-job control, so this plan provides one ordered landing path without
replacing the narrower domain plans.

The primary invariant is that CCB must not lose, misattribute, or silently
overwrite provider or job state while trying to improve continuity or
diagnostics.

## Authority

Shipped behavior remains governed by the relevant product contracts:

- [Codex plugin projection](../../../codex-plugin-projection-plan.md)
- [Claude session isolation](../../../claude-session-isolation-contract.md)
- [Provider-state storage boundaries](../../../ccb-provider-state-storage-boundary-plan.md)
- [Managed provider completion](../../../managed-provider-completion-reliability-plan.md)
- [CCBD diagnostics](../../../ccbd-diagnostics-contract.md)
- [Sidebar integration](../../../ccb-agent-sidebar-integration-plan.md)

This plan owns repair ordering and acceptance gates only. A slice that changes
one of those contracts must update it in the same patch.

## Related Plans

- [Managed provider completion reliability](../managed-provider-completion-reliability/README.md)
- [Native CLI providers](../native-cli-providers/README.md)
- [Inter-agent communication reliability](../inter-agent-comm-reliability/README.md)
- [Callback continuation safety](../callback-continuation-safety/README.md)
- [Provider memory ownership](../provider-memory-ownership/README.md)

## Scope

In scope:

- Safe Codex plugin marketplace/cache projection after merged PR257.
- Claude plugin marketplace and cache discovery in managed isolated homes.
- Exact-session Kimi restart behavior without first-launch `--continue`.
- Correct Claude queued-prompt activation and turn attribution.
- Inbound reply-routing instructions from PR264.
- Correlated execution phases, stuck-job evidence, and client visibility.
- Cancellation terminalization, callback continuation, and empty control
  notice behavior.
- Capability-gated correction or follow-up delivery to an active job.
- Unit, replay, merged-main, external source-runtime, and release-gate evidence.

Out of scope:

- A general plugin manager or shared writable cache service.
- Automatic recovery for a merely suspected stuck job in the first slice.
- Provider substitution or retry that hides unsupported resume/correction
  behavior.
- Broad UI redesign beyond consuming the agreed structured job state.

## File Map

- [roadmap.md](roadmap.md): strict repair order and phase state.
- [implementation-status.md](implementation-status.md): current R1/R2 handoff,
  evidence, blockers, and next gate.
- [topics/ordered-repair-slices.md](topics/ordered-repair-slices.md): finding,
  correction boundary, tests, and exit gate for every slice.
- [topics/provider-extension-inheritance-audit.md](topics/provider-extension-inheritance-audit.md):
  official capability/storage evidence, same-defect classification, and
  provider follow-up order.
- [open-questions.md](open-questions.md): decisions that must be frozen before
  their owning slice starts.
- [history/r1-r2-validation-2026-07-20.md](history/r1-r2-validation-2026-07-20.md):
  focused, full-suite, and external real-project evidence for the candidate.

## Execution Rule

R1 and R2 may land in one main-based PR because they share the same plugin
authority/writable-state boundary and the user explicitly requested a
synchronized fix. Later runtime slices are serial. A later slice may be
analyzed while an earlier slice is under review, but production edits start
only after the previous slice has landed or has an explicit defer decision
recorded here. Every slice must:

1. Reproduce the failure or preserve the review counterexample as a test.
2. Freeze ownership and terminal-state semantics before implementation.
3. Update every affected authoritative contract in the same patch.
4. Pass focused tests after merging the latest `main` into the candidate.
5. Run source validation only through `/home/bfly/yunwei/ccb_source/ccb_test`
   from `/home/bfly/yunwei/test_ccb2` when runtime evidence is required.
6. Record landed commit, verification, remaining risk, and next target before
   advancing the roadmap.
