# Inter-Agent Communication Reliability

Date: 2026-06-14

## Purpose

Track CCB inter-agent message transport reliability proposals before they are
allowed into the main development path.

This plan currently records analysis of PR226-style changes:

- persistent Codex FIFO reader
- bounded non-blocking FIFO writes
- read ACK files
- large payload spool pointers
- cancel flag visibility
- communication-path logging

## Current Decision

Do not implement new source changes, merge local follow-ups, or promote this
work into the main branch from this planning pass.

The useful direction is accepted for further study on Linux, macOS, and WSL,
but adoption requires a tighter reliability boundary and regression coverage.
Native Windows support is explicitly out of scope for this plan slice.

## Authority

Runtime contracts still live in the product documents under `docs/`.
This plan root records candidate transport changes, risks, and readiness gates;
it does not override shipped behavior.

Related authority and context:

- [../../../managed-provider-completion-reliability-plan.md](../../../managed-provider-completion-reliability-plan.md)
- [../../baseline/README.md](../../baseline/README.md)
- [../../../../plans/ccb-communication-test-plan.md](../../../../plans/ccb-communication-test-plan.md)

## File Map

- [roadmap.md](roadmap.md): current planning state, deferred gates, and next
  review targets.
- [topics/pr226-risk-and-adoption-note.md](topics/pr226-risk-and-adoption-note.md):
  PR226 risk analysis and adoption criteria.

## Scope

In scope:

- Linux, macOS, and WSL CCB inter-agent communication reliability.
- Codex bridge FIFO lifecycle and send-path behavior.
- ACK semantics, large request transport, marker uniqueness, and failure
  diagnosability.
- Cancel visibility at the CCB-to-agent prompt boundary.

Out of scope for this slice:

- Native Windows transport, startup, health, or mux behavior.
- Provider completion terminalization; that remains in
  [../managed-provider-completion-reliability/README.md](../managed-provider-completion-reliability/README.md).
- Immediate source implementation or release promotion.
