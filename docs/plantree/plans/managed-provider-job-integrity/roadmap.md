# Managed Provider And Job Integrity Roadmap

Date: 2026-07-21

## Status Summary

- Current status: In progress; R1/R2 landed and the R11 provider-extension
  candidate is committed on its qualified branch, the strict serial closure
  goal is active, and R3/R4/R5/R6/R7/R8/R9 are verified atomic commits.
- Work mode: execute-ready.
- Review baseline: PR257 is merged; PR258, PR259, PR264, PR265, and PR266 are
  open and reported `UNSTABLE`; Issues 260-263 remain open as of 2026-07-21.
- Last verified: R1/R2 landed as `06e1a46a` through merge `aed27abf`. R11 passed
  `282` focused tests, `5389` tests with one adjudicated baseline shutdown test
  excluded, and real clean-home Claude, Gemini, and Droid checks. The external
  project was cleanly unmounted. See the
  [R11 validation record](history/r11-provider-extension-validation-2026-07-20.md).
  R3 then passed `18` focused static/materialization tests. R4 passed `83`
  dispatcher integration tests, the `300`-test cumulative R11/R3 gate, and the
  complete Python remainder (`5261 passed`, `2 skipped`, one adjudicated
  baseline race deselected); see the
  [queue evidence](history/reviewed-repair-queue-evidence.md#r4-cancellation-and-callback-terminalization).
  R5 then passed `98` Claude tests, the `555`-test cumulative gate, and the
  complete `5269`-test Python remainder, and a real busy-pane Claude run
  returned only the exactly activated queued job. See the
  [R5 evidence](history/reviewed-repair-queue-evidence.md#r5-claude-queued-prompt-activation).
  R6 then passed the expanded `193`-test launch/runtime integration gate, the
  complete `5455`-test Python remainder, and a real same-workdir two-agent Kimi run in
  which each exact native session retained only its own hidden token. See the
  [R6 evidence](history/reviewed-repair-queue-evidence.md#r6-kimi-exact-session-resume).
  R7 then passed the `334`-test cumulative focused gate, Rust `78`, Flutter
  `659`, the complete `5335`-test Python remainder, and a real busy-pane Claude
  run whose exact ProjectView lineage was `executing` while queue failed closed
  without provider-native identity. See the
  [R7 evidence](history/reviewed-repair-queue-evidence.md#r7-correlated-execution-state-model).
  R8 then passed the `308`-test focused gate, Rust `79`, the complete
  `5340`-test Python remainder, and a real idle-pane Claude run in which the
  first exact observation remained pending-terminal and the unchanged bounded
  observation emitted a read-only `orphaned_active_inbound` envelope. See the
  [R8 evidence](history/reviewed-repair-queue-evidence.md#r8-stuck-inbound-detection).
  R9 then passed exact-job/restart/race/CLI/app-server gates, the final complete
  `5518`-test Python suite, and real Codex/Claude qualification. Exact Codex
  `turn/steer` corrected one active job while Claude refused without pane
  injection; see the
  [R9 evidence](history/reviewed-repair-queue-evidence.md#r9-active-job-correction-capability).
- Commit target: R9 is fully verified and selected by `Repair-Slice: R9`. R12
  is ready only after this atomic commit is created and the worktree is clean.

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
- Verified R4 as the atomic commit selected by `Repair-Slice: R4`: cancelled
  chain children submit one parent continuation without restart, empty
  ordinary cancellations remain visible consumed control notices, partial
  replies stay deliverable, and the first terminal writer wins cancellation
  races.
- Verified R5 as the atomic commit selected by `Repair-Slice: R5`: enqueue and
  bare dequeue remain non-activating, exact queued-command replay or a normal
  top-level user prompt emits the anchor, and old-turn output cannot complete
  the queued job.
- Verified R6 as the atomic commit selected by `Repair-Slice: R6`: first launch
  is fresh, only an exact observed per-agent native session becomes restart
  authority, invalid authority fails fresh, and same-workdir agents cannot
  cross-resume.
- Verified R7 as the atomic commit selected by `Repair-Slice: R7`: one pure
  fail-closed resolver supplies an additive nine-phase vocabulary to
  ProjectView, queue, CLI, Rust sidebar, and mobile consumers without changing
  mailbox or terminal authority or triggering recovery.
- Verified R8 as the atomic commit selected by `Repair-Slice: R8`: only an
  unchanged exact Claude idle lineage surviving the second 30-second
  observation becomes `orphaned_active_inbound`; the same envelope reaches
  ProjectView, maintenance, trace, doctor/CLI, and sidebar with explicit
  manual action and no diagnostic-read mutation.
- Verified R9 as the atomic commit selected by `Repair-Slice: R9`: one durable
  FIFO outbox targets an exact running job; managed Codex may steer only the
  bound expected turn through its shared app-server, unsupported panes refuse,
  ambiguous transport remains pending, and existing terminal/cancel authority
  wins without creating a new job, attempt, mailbox item, or callback.

## Ready

R12 generic projected-asset ownership hardening is the next serial row. R9 is
verified by the current atomic commit selector; `origin/main` remains
`aed27abf`. R12 must inventory remaining unmarked replacement sites and freeze
explicit ownership proof before any production change. PR258, PR259, PR265,
and PR266 remain held from merge.

## Next

1. **R12: Generic projected-asset ownership hardening.** Inventory remaining
   `allow_unmarked_replace=True` call sites and migrate them to marker-first
   ownership without breaking packaged CCB skill upgrades.
2. **R11-C: Copilot plugin/config projection.** Freeze an entry-level ownership
   schema and offline/no-login fixture, then project only owned plugin metadata
   while preserving credentials, sessions, permissions, cache, and local data.
3. **R10: Integrated qualification and release decision.** Run focused,
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
