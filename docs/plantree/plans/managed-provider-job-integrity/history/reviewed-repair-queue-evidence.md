# Reviewed Repair Queue Evidence

Date: 2026-07-21
Goal: [Reviewed Repair Queue Closure](../goals/reviewed-repair-queue-closure-goal.md)

This compact ledger records one accepted evidence block per verified repair
slice. Large or runtime-specific artifacts stay outside active PlanTree files
and are linked here when created.

## R3: Inbound Completion Routing Documentation

Slice: R3 inbound completion routing documentation

Commit selector / hash: commit subject `docs: correct inbound completion
routing` with trailer `Repair-Slice: R3`

Upstream items: PR264 at unchanged reviewed head `1d4d1deb`

Baseline: `0d145aa3` on `origin/main` `aed27abf`; PR264 remained open and
`unstable` during the 2026-07-21 preflight.

Counterexample: with only the corrected static assertions added, the focused
gate reported `4 failed, 14 passed`. The failures proved that projected ask
templates and generated runtime memory did not distinguish registered-agent
lineage from direct CLI control output, and the user guide did not require
rematerialization followed by restart/new session for providers without hot
reload.

Focused tests: `test/test_ask_skill_templates.py` plus
`test/test_project_memory.py`: `18 passed`.

Full/client tests: no runtime logic, schema, Rust, sidebar, mobile, or client
surface changed. The cumulative R11 provider-hook/profile/launcher gate passed
`282` tests. Changed Python files compiled successfully and
`git diff --check` passed.

Real project evidence: not required for this documentation/materialization-only
slice.

Source immutability: no provider source was read or projected by the R3 gate.

Cleanup: no CCB runtime project, socket, provider process, or tmux pane was
opened.

Remaining risk: already-running provider sessions retain cached instructions
until runtime memory is rematerialized and the provider hot-reloads it or is
restarted/reopened as documented.

Next unlocked row: R4 cancellation and callback terminalization.
