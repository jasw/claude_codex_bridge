# Ordered Repair Slices

Date: 2026-07-20

Role: implementation plan and acceptance matrix
Status: active planning authority
Domain: managed provider and job integrity
Read when: starting, reviewing, or accepting R1-R12
Related: [roadmap](../roadmap.md), [open questions](../open-questions.md)

## Review Evidence Baseline

The 2026-07-19 review ran focused tests on each contributor head and again
after merging the reviewed `main`:

| Change | Head evidence | Merged-main evidence | Acceptance result |
| :--- | :--- | :--- | :--- |
| PR257 | 113 provider-profile tests passed | Already merged | Follow-up required |
| PR258 | 9 native CLI tests passed | 11 passed | Rejected by first-launch and alias counterexamples |
| PR259 | 22 Claude tests passed | 27 passed | Rejected by cross-turn replay |
| PR264 | 17 documentation/static tests passed | 17 passed | Minor wording changes required |
| PR265 | 120 ProjectView tests passed | 120 passed | Partial diagnostic slice only |
| PR266 | 78 cancellation tests passed | 78 passed | Rejected by chain-cancel counterexample |

The open-PR CI matrix was not green at review time. Some observed macOS/WSL
failures matched an existing shutdown race, but every candidate must still
obtain a current green or explicitly adjudicated gate after revision.

## R1: Codex Plugin Projection Safety

Status: implementation complete in the combined R1/R2 candidate; validation in
progress.

Finding:

- Merged PR257 routes `plugins/cache` and `.tmp/marketplaces` with
  `allow_unmarked_replace=True`.
- A missing source cache deletes an existing managed local plugin cache.
- A present source can become a shared mutable symlink target for multiple
  managed agents.
- The authoritative Codex plugin projection contract still describes the old
  startup authority.

Correction boundary:

- Classify each path as immutable startup authority or agent-owned writable
  state before changing code.
- Never delete or replace unmarked agent-owned state because a source path is
  missing.
- Replace only marker-owned projections and keep writable runtime cache local
  to the managed home.

Frozen decision (2026-07-20):

- `.tmp/plugins/` plus `.tmp/plugins.sha` remains immutable whole-bundle
  authority and may use content-addressed shared storage.
- `.tmp/marketplaces/` and `plugins/cache/` are per-agent writable state seeded
  by staged copies. They must not resolve to the source home or another agent.
- Missing source preserves writable local state. Source changes refresh only a
  valid marker-owned seed, and failed replacement restores the previous tree.
- Unmarked and foreign-marker targets are never replaced or claimed.

Required evidence:

- Missing source preserves managed local cache.
- Source present does not permit managed sessions to mutate source authority.
- Two Codex agents do not share writable cache state.
- Upgrade, repeated refresh, stale marker, and rollback cases pass.
- Codex plugin and provider-state storage contracts are updated.

Exit gate: land with R2 in one main-based PR after merged-main and external
source-runtime evidence passes.

## R2: Claude Plugin Seed Support

Status: implementation complete in the combined R1/R2 candidate; real inherited
plugin evidence remains environment-dependent.

Finding:

- Managed Claude homes inherit `enabledPlugins` but not marketplace
  registration, marketplace sources, or plugin cache content.
- A plugin such as Ponytail can therefore be enabled in settings but absent
  from the first isolated Claude session.

Correction boundary:

- Use Claude's supported plugin seed boundary for inherited read-only plugin
  authority rather than sharing a mutable cache between agents.
- Keep each managed Claude home's writable provider state independent.
- Forward seed paths correctly when Windows Claude is launched through WSL.

Frozen decision (2026-07-20):

- Plugin inheritance follows `inherit_config` plus the existing hard-role
  inherited-assets gate; no new profile field is added in this slice.
- A usable source `.claude/plugins/` is exposed through the provider-supported
  read-only `CLAUDE_CODE_PLUGIN_SEED_DIR` boundary.
- `CLAUDE_CODE_PLUGIN_CACHE_DIR` points to the full agent-local
  `<managed-home>/.claude/plugins/` root, not its `cache/` child.
- A source directory containing only `blocklist.json` is not treated as a seed.

Required evidence:

- First managed SessionStart sees a seeded plugin skill/hook.
- No-source and plugin-inheritance-disabled paths remain clean.
- Two concurrent Claude agents can load the plugin without source mutation or
  shared writable state.
- Linux, macOS, and applicable WSL path handling are covered.
- Claude isolation and provider-state storage contracts are updated.

Exit gate: land with R1 after fresh external project validation. When the test
account has no installed source plugin, record that limitation and retain the
first-process environment, two-agent isolation, and no-source evidence rather
than fabricating a real-plugin pass.

## R3: Inbound Completion Routing Documentation

Finding:

- PR264 correctly requires an inbound agent to finish its current continuation
  directly instead of sending another ask to the original caller.
- Its rematerialization wording can imply that a running provider session
  rereads changed memory without restart.

Correction boundary:

- State that rematerialized memory is adopted by a restarted or new provider
  session when the provider does not hot-reload it.
- Scope direct finalization to registered agent callers; direct CLI users read
  trace/watch/control output.

Required evidence: static template assertions for every projected ask skill
and generated runtime-memory variant touched by the PR.

Exit gate: land as a documentation-only change without coupling it to R4.

## R4: Cancellation And Callback Terminalization

Finding:

- PR266 correctly avoids putting an empty cancelled reply at mailbox head.
- Cancellation calls terminal recording directly and bypasses normal callback
  continuation finalization.
- Cancelling a chain child can leave its callback edge pending and its parent
  message running until a dispatcher restart repairs the edge.

Correction boundary:

- Route cancellation through the same idempotent terminal callback authority
  as normal completion, or explicitly terminalize the edge in that authority.
- Preserve partial provider output as normal reply content.
- Define trace/ProjectView visibility for consumed-from-birth control notices.

Required evidence:

- Ordinary empty cancel leaves no caller mailbox depth.
- Non-empty partial cancel remains deliverable exactly once.
- Chain child cancel resolves parent continuation according to the frozen
  policy without dispatcher restart.
- Repeated cancel, completion race, persistence/restart, and pre-existing
  caller mailbox cases pass.

Exit gate: revised PR266 or a clean replacement PR passes callback, mailbox,
trace, and external source-runtime tests.

## R5: Claude Queued-Prompt Activation

Finding:

- PR259 treats `queue-operation/enqueue` as a synthetic user anchor.
- Real Claude logs show the old assistant may emit `end_turn` before dequeue
  and before the queued prompt becomes active.
- Replay proved old-turn output can be captured as the new job reply.

Correction boundary:

- Represent `enqueued`, `activated/dequeued`, and `anchored` separately.
- Do not accept assistant content or terminal evidence for the new job until
  activation is correlated to that prompt.
- Fence pre-anchor assistant UUID and subagent events from the new turn.

Required evidence:

- Old busy turn plus one queued prompt.
- Multiple queued prompts with FIFO activation.
- Tool-only old turn, subagent records, restart/catch-up, and session rotation.
- No old assistant content or terminal event enters the new reply.

Exit gate: replace, rather than incrementally patch, PR259's enqueue-time
anchor model.

## R6: Kimi Exact-Session Resume

Finding:

- PR258 adds `--continue` whenever generic `restore` is true, including an
  ordinary first launch.
- Kimi 1.47.0 exits when no prior workdir session exists.
- Flag aliases vary by Kimi version, and workdir-global latest-session lookup
  can resume another CCB agent's session.

Correction boundary:

- Persist the exact native Kimi session identity observed for the managed
  agent and resume only that session.
- Start fresh when no CCB-owned session exists or reset was requested.
- Prefer stable long options and capability-aware parsing; recognize explicit
  user session/resume flags without adding a conflicting flag.

Required evidence:

- Empty first launch, normal restart, clear/reset, and explicit session flags.
- Two in-place Kimi agents in the same workdir never resume each other.
- Missing/corrupt prior session fails clearly or starts fresh according to a
  documented decision, without silent fallback to another session.

Exit gate: rewrite PR258 around exact ownership and repeat the established real
Kimi source-runtime matrix.

## R7: Correlated Execution-State Model

Finding:

- PR265 maps nearly every running job to `executing`, classifies orphan risk
  before full attempt/inbound/mailbox/lease correlation, and has no explicit
  contradictory-evidence `unknown` state.
- The new field is not consumed by structured queue, CLI, Rust sidebar, or
  mobile surfaces required by Issue262.

Correction boundary:

- Freeze one additive phase vocabulary and evidence precedence model.
- Derive phase from job, execution runtime, inbound attempt, mailbox, lease,
  provider anchor/activity, and terminal evidence with matching identities.
- Contradictory or incomplete evidence must be `unknown`, not a confident
  orphan/executing label.

Required evidence:

- Injecting, queued, anchored/executing, provider-idle-pending-terminal,
  terminal, orphaned, and unknown fixtures.
- Mismatched job/attempt/lease and stale pane evidence cannot produce a
  confident phase.
- ProjectView, structured queue, CLI, sidebar, and mobile consume the same
  field with backward-compatible fallback.
- Diagnostics and sidebar contracts are updated.

Exit gate: PR265 remains partial until all required consumers and uncertainty
semantics are implemented; Issue262 stays open until then.

## R8: Stuck Inbound Detection

Finding: Issue260 describes a running inbound job that remains mailbox head
after the provider has returned to an idle prompt without terminal evidence.

Correction boundary:

- Build only on R7 correlated identities and phase authority.
- Require running job, active matching inbound/lease, provider-idle evidence,
  and absent terminal evidence over a bounded observation interval.
- Emit diagnostic suspicion first; do not auto-restart, resend, or complete.

Required evidence:

- True stuck fixture and false-positive guards for long reasoning, tool use,
  queued prompt, stale pane snapshot, session rotation, and terminal race.
- Doctor, ProjectView, trace, CLI, and sidebar expose the same reason/evidence.

Exit gate: Issue260 can close only after an external real-provider idle-prompt
reproduction is diagnosed without mutating the job.

## R9: Active-Job Correction Capability

Finding: Issue261 requests correction/follow-up delivery to a job that is
already active; PR264 only documents cancel and resubmit behavior.

Correction boundary:

- Address an exact job and active provider turn, not merely an agent pane.
- Preserve correction lineage and ordering relative to completion/cancel.
- Advertise provider support explicitly and fail closed when unsupported.
- Never substitute another provider or hide failed injection behind retries.

Required evidence:

- Supported provider success, unsupported provider refusal, wrong/stale job,
  multiple queued jobs, completion race, cancellation race, and restart.
- Caller and operator can distinguish accepted, rejected, too-late, and
  terminal outcomes.

Exit gate: product semantics and provider capability matrix are frozen before
implementation; R9 cannot weaken R4 terminal authority or R7 phase authority.

## R10: Integrated Qualification

Required gates:

- Focused regression suites for every R1-R9 counterexample.
- Full Python and Rust suites, plus affected Flutter/sidebar/mobile suites.
- Clean merge with current `main` and current CI adjudication.
- External source-wrapper diagnose and inspectable live project validation.
- Real Codex primary and Claude secondary acceptance where provider behavior is
  involved; no Grok/Gemini/OpenCode credential requirement for this lane.
- Provider source homes remain unmodified, cancellation leaves no unresolved
  chain edge, and completed tests leave no mounted runtime residue.

Release decision: batch only compatible slices. A critical main regression may
ship earlier as a focused hotfix after its own complete gate.
