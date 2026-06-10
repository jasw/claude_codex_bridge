# Semantic Supervision Loop

Date: 2026-06-10

## Boundary

The heartbeat is a CCB feature with a `ccb_self` semantic assessor.

CCB owns:

- scheduler or external tick entrypoint;
- project-scoped heartbeat enablement and cadence policy;
- next-run state and locking;
- runtime snapshot collection;
- validation of any schedule update or repair command;
- diagnostics records and user-visible status.

`ccb_self` owns:

- the running-supervision skill;
- semantic interpretation of CCB evidence;
- structured advice about health, confidence, suspected failure domain, and
  next useful cadence.

`ccb_self` must not own:

- keeper lifecycle;
- ccbd generation or lease authority;
- configured-agent runtime authority;
- direct file writes to schedule or authority records;
- raw tmux mutation;
- project-wide shutdown or destructive repair.

## Why Programmatic Checks Are Not Enough

Programmatic checks can answer whether a queue is empty, a pane exists, a job
has a heartbeat timeout, or a mailbox summary is missing. They cannot reliably
answer all execution-quality questions.

Semantic supervision is useful for cases such as:

- an agent says a task is done but no expected evidence, tests, or artifacts are
  present;
- a reply exists but appears to be a refusal, prompt confusion, or partial
  answer rather than the requested result;
- a callback chain is technically terminal but the parent task cannot resume
  from the result;
- logs show repeated provider/tool errors that are not yet expressed as a
  single terminal CCB failure;
- an agent is idle but the current plan or user request still implies unfinished
  work;
- diagnostics are degraded enough that CCB cannot decide whether the state is
  healthy.

The design should use cheap programmatic filters first and reserve `ccb_self`
provider work for suspicious or ambiguous states.

## Proposed Tick Flow

1. An external scheduler or CCB timer invokes a project-scoped maintenance tick.
2. The tick acquires a heartbeat lock and reads CCB-owned schedule policy.
3. If heartbeat is disabled, too early, or another maintenance tick is active,
   it exits.
4. CCB collects a bounded snapshot: `ps`, queue summaries, pending inbox/reply
   counts, active job ages, recent terminal failures, fault rules, degraded
   diagnostics, and relevant heartbeat timeout evidence.
5. If the snapshot is clearly idle and healthy, CCB records `last_ok`, advances
   `next_run_at` using the normal interval, and exits without waking
   `ccb_self`.
6. If the snapshot is clearly hard-failed, CCB can either record the failure and
   notify the user or wake `ccb_self` with a focused diagnostic task, depending
   on policy.
7. If the snapshot is suspicious or ambiguous, CCB wakes `ccb_self` with the
   running-supervision skill and the bounded snapshot.
8. `ccb_self` analyzes trace, artifacts, logs, and current plan/task context as
   needed, then returns or applies a validated schedule recommendation through a
   CCB control-plane surface.
9. CCB persists the accepted next cadence and diagnostics. The provider turn
   ends; there is no provider-side infinite loop.

## Running-Supervision Skill Result

The skill should produce a compact structured result:

- `health`: `healthy`, `concern`, `failing`, or `unknown`
- `confidence`: `high`, `medium`, or `low`
- `domains`: queue, inbox, callback, provider, pane, config, task semantics,
  diagnostics, or unknown
- `evidence`: short references to command outputs, traces, artifacts, or plan
  files
- `recommended_action`: report only, ask a target agent for a small status
  check, run a lineage repair, clear/restart one agent, or request user
  confirmation
- `next_heartbeat_after`: desired delay with reason
- `needs_user`: true when the result is unsafe to repair autonomously

The CCB control plane must validate this result before changing schedule or
running any command.

## Cadence Policy

Suggested first policy:

- `healthy`: use a long normal interval.
- `concern`: shorten interval moderately and optionally wake `ccb_self` again
  only if the same concern persists.
- `failing`: record and surface the failure; run only policy-allowed repairs.
- `unknown`: shorten interval briefly, cap repeated unknowns, then escalate to
  the user or back off.

The "unknown means check sooner" behavior matches the user's intent, but it
needs caps so bad diagnostics or provider confusion cannot create an infinite
self-wakeup loop.
