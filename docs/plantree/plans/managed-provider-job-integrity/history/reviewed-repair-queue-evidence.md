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

## R4: Cancellation And Callback Terminalization

Slice: R4 cancellation and callback terminalization

Commit selector / hash: commit subject `fix: terminalize cancelled callback
chains` with trailer `Repair-Slice: R4`

Upstream items: PR266 at unchanged reviewed head `3e11523c`; Issue263 remained
open during the 2026-07-21 preflight.

Baseline: R3 commit `40e412653840cf346bb7e7b191f833318a0bf778` on
`origin/main` `aed27abf`; PR266 remained open and `unstable`.

Counterexample: with the six preserved R4 tests added before the runtime
repair, the focused gate reported `5 failed, 1 passed`. The failures proved
that empty cancellation had no durable control notice, chain-child
cancellation left its callback edge pending, a pre-existing mailbox gained an
empty reply, completion could be overwritten by cancellation, and
trace/ProjectView did not expose the zero-depth notice policy.

Focused tests: message-bureau dispatcher integration `83 passed`; control
queue `15 passed`; ProjectView `82 passed`; dispatcher `61 passed`; cancel
flags `2 passed`; communication recovery `12 passed`. The cumulative R11/R3
provider projection and instruction gate passed `300` tests.

Full/client tests: the complete Python remainder passed `5261` tests with `2`
skipped and the single lifecycle-stopping socket race deselected. The exact
race reproduced on `origin/main` `aed27abf` at the same final socket operation
(`ConnectionResetError` / `CcbdClientError`), satisfying the goal's baseline
adjudication rule. No Rust, Flutter, sidebar, or mobile schema changed.

Real project evidence: external mounted fake-provider project
`/home/bfly/yunwei/test_ccb2/r4-cancel-runtime-20260721-fkyoH9`; result artifact
`r4-runtime-result.json`. Callback edge `cb_6995f5eefed5` reached `done`, its
cancelled child produced one reply and continuation `job_d3476f1ddcd6`, and
ordinary cancelled job `job_c5a7e70dc1e7` produced one consumed completion
notice while caller depth and pending replies remained zero.

Source immutability: the candidate binary diff SHA256 remained
`481874ea96406a0c989032c25edbe9ecd7ea780b84495d24141c26985af5bfe9`
before and after the mounted run; the untracked-set SHA256 remained
`dc61b15cfea4f14edc27267bc5f420115db7db79018491c17034c4cc1a4018ac`.

Cleanup: candidate `ccb_test kill` returned the external project to
`unmounted`; the ccbd and tmux sockets were absent and the recorded keeper and
daemon PIDs no longer existed.

Remaining risk: the pre-existing lifecycle-stopping socket teardown race
remains outside R4. Provider-native cancel is still best-effort, but terminal
lineage, callback continuation, repeat idempotency, and completion-race
authority now fail closed around the first persisted terminal job.

Next unlocked row: R5 Claude queued-prompt activation.

## R5: Claude Queued-Prompt Activation

Slice: R5 Claude queued-prompt activation

Commit selector / hash: commit subject `fix: bind Claude queued prompts to
activation` with trailer `Repair-Slice: R5`

Upstream item: PR259 remained open at unchanged reviewed head `c4bd9427` and
reported `UNSTABLE` during the 2026-07-21 preflight.

Baseline: R4 commit `38645d475ab2283ee88bd11531727d8f2e7319ad` on
`origin/main` `aed27abf`.

Counterexample: with seven preserved R5 tests added before the runtime repair,
the focused gate reported `7 failed`. The failures proved pane dispatch and
enqueue synthesized activation too early, old main/subagent/tool-only output
could bind the queued job, exact multi-prompt replay was not required, queue
state did not survive restart/rotation correctly, and pre-activation hook
output could terminalize the new job.

Frozen authority: [Decision 002](../decisions/002-claude-queued-prompt-activation.md)
separates enqueue, bare dequeue observation, exact activation, and anchoring.
Only a normal top-level user prompt or exact
`attachment/queued_command.prompt` carrying the current outer request ID may
activate; tool-result, meta, subagent, assistant, system, hook, pane-idle, and
API-error records are fenced before that point.

Focused tests: all Claude-specific files passed `98` tests. The combined
Claude/execution-service/message-bureau gate passed `260` tests. The cumulative
R11/R3/R4 provider projection, instruction, dispatcher, control-queue,
ProjectView, cancel, and recovery gate passed all `555` collected tests.

Full/client tests: the first complete run reached `5269 passed`, `2 skipped`,
and the one adjudicated lifecycle-stopping socket-race deselection before the
unrelated `restart_replay_pass` fake-runtime scenario missed terminal scheduler
authority after 96 activations. The exact scenario immediately passed alone
(`1 passed in 33.09s`). A second complete remainder, deselecting that
independently green scenario and the existing socket race, passed `5269`
tests with `2 skipped` and `2 deselected` in `951.69s`. No Rust, Flutter,
sidebar, or mobile consumer changed.

Real project evidence: external opened Claude project
`/home/bfly/yunwei/test_ccb2/r5-claude-queue-runtime-20260721-RyGaHI` used
Claude Code 2.1.206 with displayed model `DeepSeek-V4-pro`. While
`sleep 25; echo OLD_TURN_SENTINEL` was visibly running, CCB submitted
`job_aadf1ff01a30`. Persisted activation state recorded enqueue, exact
activation source UUID `6c43ca6c-6d05-4ccf-a984-8a5c74902706`, and one
anchor. One attempt and one reply completed with observed reason
`assistant_end_turn`; the reply was exactly `NEW_QUEUED_SENTINEL_2` and did
not contain the old sentinel. Compact artifact: `r5-runtime-result.json` in
that external project.

Source immutability: candidate binary-diff SHA256 remained
`7a5f721b34fce861551700524338a90468faba31361777727bf290044aa54df2`
before and after the mounted run; the untracked-set SHA256 remained
`1ccb87128ffec34330cbbf0afa30fa5f2bc91ce31cd923c17e1e90033bbf3b11`.

Cleanup: candidate `ccb_test kill` returned `kill_status: ok` and left the
project `unmounted`. The ccbd and tmux sockets were absent, and recorded keeper
PID 708543, daemon PID 708595, and Claude PID 708960 no longer existed.

Remaining risk: the exact replay contract depends on Claude retaining its
current `attachment/queued_command.prompt` record. Unknown future record
shapes fail closed and leave the job pending rather than accepting old output.
The unrelated restart-replay fake-runtime timing miss remains a test-harness
flakiness signal; its exact isolated pass is recorded rather than hidden.

Next unlocked row: R6 Kimi exact-session resume.

## R6: Kimi Exact-Session Resume

Slice: R6 Kimi exact-session resume

Commit selector / hash: commit subject `fix: resume exact Kimi sessions` with
trailer `Repair-Slice: R6`

Upstream item: PR258 remained open at unchanged reviewed head `d119bf18` and
reported `UNSTABLE` during the 2026-07-21 preflight.

Baseline: R5 commit `bc31832e8b53030f3cfb1750c3fc4ed8b5267e64` on
`origin/main` `aed27abf`. Kimi 1.47.0 exposed exact `--session`/`--resume` and
workdir-global `--continue`; an isolated fresh-share probe reproduced
`--continue` exiting 2 with `No previous session found`.

Counterexample: the three preserved R6 behavior tests failed before production
changes because exact restart selection was absent, explicit-session
precedence had no recorded state, and the session payload did not retain a
native binding. The first live candidate then exposed a deeper restart-path
counterexample: `ccb restart kimi1` reused the original fresh `start_cmd`, the
visible Kimi pane opened a different native UUID, and the CCB record still
claimed the old binding. That external project was stopped cleanly before the
restart-command preparation repair.

Frozen authority: [Decision 003](../decisions/003-kimi-exact-session-ownership.md)
binds a native Kimi session only after the target agent's exact CCB request is
observed in that session. First launch never guesses `--continue`; restart
validates project, agent, workdir, share root, exact non-symlinked native
layout, persisted command template, and current long-option capability before
selecting the owned ID. Invalid or unsupported authority clears only the
carried binding and starts fresh. Explicit user session controls win.

Implementation: Kimi launch payloads now record the share root, a single
exact-session command-template insertion point, capability command parts,
explicit-control state, and only a validated native binding. Native-log polling
records the observed UUID/path against the matching CCB launch record and
rejects stale executions. Manual and dead-pane restart materialize only the
validated exact selector; missing, malformed, mismatched, symlinked, drifted,
or unsupported authority rewrites the control record to a documented fresh
state without deleting provider data. The CCB pane-launch ID is never treated
as a Kimi session ID.

Focused tests: Kimi session/launcher behavior passed `45` tests. The broader
Kimi/native/restart gate passed `120` tests. Expanded launch, completion,
runtime binding, health, registry, and restart integration passed `193` tests.
Python compilation, Pyflakes, and `git diff --check` passed.

Full/client tests: the complete Python remainder passed `5455` tests with `2`
skipped and `2` deselected in `936.97s`. The independently deselected
`restart_replay_pass` fake-runtime case passed alone (`1 passed` in `31.92s`).
The other deselection is the lifecycle-stopping socket race already reproduced
on frozen `origin/main`. No Rust, Flutter, sidebar, mobile, or serialized client
schema changed in R6.

Real project evidence: external opened project
`/home/bfly/yunwei/test_ccb2/r6-kimi-exact-runtime2-20260721-9hlXai` used Kimi
CLI 1.47.0, displayed model `kimi-for-coding`, and two agents in one in-place
workdir. Both first-launch records were `fresh_no_binding` with no implicit
session selector. Jobs `job_3c43dfe74b89` and `job_46bb9eb26703` produced
distinct observed native UUIDs `cdd2735e-4adc-4e7a-baa7-0e76b66ac9de` and
`d22e3fa2-3331-42b2-b23a-f55c8a78034b`. CCB-controlled restart commands and
visible Kimi pane headers selected those same respective UUIDs. Continuation
prompts did not contain the hidden tokens; jobs `job_130ed2ccd6d3` and
`job_f11970359882` returned only `ALPHA_7A21` and `BETA_9B34`, proving both
continuity and no cross-agent resume. After a clean stop, the exact final
candidate remounted both original UUIDs with one selector per command; jobs
`job_f48cde3fddc9` and `job_4f357e3e3a31` again returned `ALPHA_7A21` and
`BETA_9B34`. Compact artifact: `r6-runtime-result.json` in that external
project. The acceptance operator did not directly inspect Kimi credentials or
native conversation content; CCB's existing completion reader necessarily
consumed the target workdir's native turn log.

Source immutability: candidate binary-diff SHA256 remained
`52131e6a195b59ea29c34b4c73459961fdf51080b0c35a0c33e59ea0e0c9e65b`
before and after the mounted run; the untracked-set SHA256 remained
`96bae5616cba9544958ce825a89cff6729cce1a993f4278241e0d105daa928b2`.

Cleanup: candidate `ccb_test kill` returned `kill_status: ok` and left the
project `unmounted`. The ccbd and tmux sockets were absent, and recorded keeper
PID 4063913 no longer existed. The earlier counterexample project was also
cleanly unmounted.

Remaining risk: exact restart depends on the configured Kimi executable
continuing to expose a stable long selector and the documented share/session
layout. Capability or layout drift fails fresh rather than guessing. Explicit
user session controls can intentionally choose broader native behavior. Kimi
still does not support restoration of an interrupted in-flight CCB job.

Next unlocked row: R7 correlated execution-state model.
