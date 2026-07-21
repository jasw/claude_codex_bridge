# Managed Provider And Job Integrity Roadmap

Date: 2026-07-21

## Status Summary

- Current status: In progress; R1/R2 landed and the R11 provider-extension
  candidate is committed on its qualified branch, the strict serial closure
  goal is active, and R3 is a verified atomic commit.
- Work mode: execute-ready.
- Review baseline: PR257 is merged; PR258, PR259, PR264, PR265, and PR266 are
  open and reported `UNSTABLE`; Issues 260-263 remain open as of 2026-07-21.
- Last verified: R1/R2 landed as `06e1a46a` through merge `aed27abf`. R11 passed
  `282` focused tests, `5389` tests with one adjudicated baseline shutdown test
  excluded, and real clean-home Claude, Gemini, and Droid checks. The external
  project was cleanly unmounted. See the
  [R11 validation record](history/r11-provider-extension-validation-2026-07-20.md).
  R3 then passed `18` focused static/materialization tests and the cumulative
  `282` R11 provider-projection tests; see the
  [queue evidence](history/reviewed-repair-queue-evidence.md#r3-inbound-completion-routing-documentation).
- Next target: resolve R4's callback-cancellation disposition and reproduce
  the chain-child terminalization counterexample. Every later row remains
  locked.

## Done

- Completed a code, contract, CI, and merged-main review of
  [PR257](https://github.com/SeemSeam/claude_codex_bridge/pull/257),
  [PR258](https://github.com/SeemSeam/claude_codex_bridge/pull/258),
  [PR259](https://github.com/SeemSeam/claude_codex_bridge/pull/259),
  [PR264](https://github.com/SeemSeam/claude_codex_bridge/pull/264),
  [PR265](https://github.com/SeemSeam/claude_codex_bridge/pull/265), and
  [PR266](https://github.com/SeemSeam/claude_codex_bridge/pull/266).
- Confirmed that entries 260-263 are open issues rather than pull requests and
  mapped each issue to the candidate PR or missing implementation.
- Preserved the highest-risk counterexamples and acceptance boundaries in the
  slice topic instead of treating passing contributor tests as acceptance.
- Registered this cross-cutting plan without changing the authority of the
  existing provider, communication, callback, diagnostics, or storage plans.
- Landed R1/R2 in PR269 (`06e1a46a`, merge `aed27abf`) with the preserved
  [validation record](history/r1-r2-validation-2026-07-20.md).
- Qualified the R11 provider-extension candidate as local commit `5c1ff83a`.
- Activated the serial closure goal in `0d145aa3` after refreshing `origin/main`,
  all reviewed PR heads, and Issues260-263.
- Verified R3 as the atomic commit selected by `Repair-Slice: R3`: registered
  agent results retain existing lineage, direct CLI callers use control
  output, and non-hot-reloading provider sessions adopt rematerialized memory
  only after restart or a new session.

## In Progress

No repair slice is currently `in_progress`. R4 is the only next eligible row;
PR258, PR259, PR265, and PR266 remain held from merge.

## Next

1. **R4: Cancellation and callback terminalization.** Revise PR266 so ordinary
   empty cancellation does not occupy the mailbox and cancelling a chain child
   resolves its callback edge immediately without waiting for daemon restart.
2. **R5: Claude queued-prompt activation.** Replace PR259's enqueue-time
   synthetic anchor with explicit queued, activated, and anchored phases; prove
   old-turn output cannot complete the new job.
3. **R6: Kimi exact-session resume.** Replace PR258's default `--continue`
   behavior with CCB-owned exact session identity, fresh first launch, version
   tolerant flags, and same-workdir multi-agent isolation.
4. **R7: Correlated execution-state model.** Redesign PR265 around an agreed
   phase vocabulary, contradictory-evidence `unknown`, attempt/inbound/lease/
   provider correlation, and structured queue, CLI, sidebar, and mobile output.
5. **R8: Stuck inbound detection.** Implement Issue260 on top of R7 using
   correlated running-job, active-attempt, provider-idle, and missing-terminal
   evidence. Ship diagnostics first; keep automatic recovery disabled.
6. **R9: Active-job correction capability.** Design Issue261 only after R4 and
   R7 establish terminal and phase authority. Target the exact job, preserve
   lineage, define provider capability/refusal, and cover completion races.
7. **R12: Generic projected-asset ownership hardening.** Inventory remaining
   `allow_unmarked_replace=True` call sites and migrate them to marker-first
   ownership without breaking packaged CCB skill upgrades.
8. **R11-C: Copilot plugin/config projection.** Freeze an entry-level ownership
   schema and offline/no-login fixture, then project only owned plugin metadata
   while preserving credentials, sessions, permissions, cache, and local data.
9. **R10: Integrated qualification and release decision.** Run focused,
   full Python/Rust/client, clean current-main, external source-runtime, and
   real Codex/Claude project gates; prepare evidence-backed upstream
   dispositions without pushing, merging, closing, publishing, or releasing.

## Deferred

- Automatic restart, resend, or terminalization from stuck-job suspicion.
- Sharing mutable plugin caches between managed agents.
- A provider-independent resume abstraction beyond the Kimi evidence needed
  by R6.
- UI workflow redesign beyond exposing the R7 structured state.
- Closing Issue262 from ProjectView-only heuristic output.

## Advancement Gate

Only one repair slice may be `In Progress`. When the closure goal is active,
advance only after the current row has a verified atomic commit and linked
durable evidence; defer/block does not count as completion. A PR's own tests
passing is necessary but not sufficient: its negative counterexample, contract
update, current-main tests, and applicable real runtime test must also pass.
