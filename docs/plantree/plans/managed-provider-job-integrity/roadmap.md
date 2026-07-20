# Managed Provider And Job Integrity Roadmap

Date: 2026-07-20

## Status Summary

- Current status: In progress; R1/R2 landed and the R11 provider-extension
  candidate is committed on its qualified branch.
- Work mode: execute-ready.
- Review baseline: PR257 is merged; PR258, PR259, PR264, PR265, and PR266 are
  open and reported `UNSTABLE`; Issues 260-263 remain open as of 2026-07-20.
- Last verified: R1/R2 landed as `06e1a46a` through merge `aed27abf`. R11 passed
  `282` focused tests, `5389` tests with one adjudicated baseline shutdown test
  excluded, and real clean-home Claude, Gemini, and Droid checks. The external
  project was cleanly unmounted. See the
  [R11 validation record](history/r11-provider-extension-validation-2026-07-20.md).
- Next target: integrate the committed branch after an explicit push/merge
  instruction.

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

## In Progress

- **R11 provider extension inheritance:** harden Claude's first interactive
  plugin load, seed Gemini and Qwen extensions locally, and project Droid
  plugins plus only their enabling settings without copying provider homes.
- Keep Copilot deferred until its mixed auth/config/plugin state can be split
  without overwriting credentials, sessions, permissions, or local plugin data.
- Hold PR258, PR259, PR265, and PR266 from merge until their owning later
  roadmap slices pass the negative cases below.

## Next

1. **R3: Inbound completion routing documentation.** Correct PR264 wording so
   rematerialization is followed by provider restart/new session when required,
   distinguish registered-agent callers from direct CLI callers, then land the
   documentation-only slice.
2. **R4: Cancellation and callback terminalization.** Revise PR266 so ordinary
   empty cancellation does not occupy the mailbox and cancelling a chain child
   resolves its callback edge immediately without waiting for daemon restart.
3. **R5: Claude queued-prompt activation.** Replace PR259's enqueue-time
   synthetic anchor with explicit queued, activated, and anchored phases; prove
   old-turn output cannot complete the new job.
4. **R6: Kimi exact-session resume.** Replace PR258's default `--continue`
   behavior with CCB-owned exact session identity, fresh first launch, version
   tolerant flags, and same-workdir multi-agent isolation.
5. **R7: Correlated execution-state model.** Redesign PR265 around an agreed
   phase vocabulary, contradictory-evidence `unknown`, attempt/inbound/lease/
   provider correlation, and structured queue, CLI, sidebar, and mobile output.
6. **R8: Stuck inbound detection.** Implement Issue260 on top of R7 using
   correlated running-job, active-attempt, provider-idle, and missing-terminal
   evidence. Ship diagnostics first; keep automatic recovery disabled.
7. **R9: Active-job correction capability.** Design Issue261 only after R4 and
   R7 establish terminal and phase authority. Target the exact job, preserve
   lineage, define provider capability/refusal, and cover completion races.
8. **R10: Integrated qualification and release decision.** Run focused,
    full Python/Rust/client, clean merged-main, external source-runtime, and
    real Codex/Claude project gates; close only the issues whose complete
    acceptance criteria are demonstrated.
9. **R12: Generic projected-asset ownership hardening.** Inventory remaining
    `allow_unmarked_replace=True` call sites and migrate them to marker-first
    ownership without breaking packaged CCB skill upgrades.

## Deferred

- Automatic restart, resend, or terminalization from stuck-job suspicion.
- Sharing mutable plugin caches between managed agents.
- A provider-independent resume abstraction beyond the Kimi evidence needed
  by R6.
- UI workflow redesign beyond exposing the R7 structured state.
- Closing Issue262 from ProjectView-only heuristic output.
- Copilot plugin/config projection until exact entry ownership and secret/session
  preservation rules are frozen and a real CLI fixture is available.

## Advancement Gate

Only one runtime slice may be `In Progress`. Before advancing, update this file
with the landed commit and link durable evidence from history or the owning
topic. A PR's own tests passing is necessary but not sufficient; its negative
counterexample, contract update, merged-main tests, and applicable real runtime
test must also pass.
