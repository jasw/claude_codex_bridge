# Context Purity

Date: 2026-06-24

## Principle

The purpose of the multi-agent workflow is not simply to run more agents. Its
purpose is to keep context clean and long-lived by cutting work into efficient
role and granularity boundaries.

Short-term, fast-changing detail should be pushed to the role that needs it,
used there, converted into a compact artifact or evidence record, and then
cleared from the long-lived control path. `frontdesk` should stay focused on user
intent and macro decisions. Long-term plan-tree should stay focused on durable
plans, decisions, blockers, and evidence.

## Context Classes

| Class | Examples | Owner | Retention |
| :--- | :--- | :--- | :--- |
| Macro intent | User goal, scope, non-goals, risk tolerance | `frontdesk`, planner group | Durable when accepted |
| Planning truth | PRD, design notes, acceptance criteria, readiness decision | planner, planner stewardship mode | Durable |
| Execution noise | command output, exploratory notes, transient errors, raw logs | execution node, runtime artifacts | Short-lived |
| Review evidence | findings, fixed issues, test results, changed files | checker, round checker, planner stewardship mode | Durable summary only |
| Recovery evidence | root cause, failed attempts, final fix path | recovery node, monitor | Durable if it changes plan or risk |
| Loop health | job ids, callback ids, heartbeats, timeout counters, pane evidence | deterministic monitor | Runtime-local |

## Frontdesk Context Budget

`frontdesk` may receive:

- Current macro objective.
- User-facing scope choices.
- High-risk or destructive-action decisions.
- Final result summary.
- Unrecoverable escalation summary.
- Compact breadcrumb with phase, owner, next action, blocker, and evidence refs.

`frontdesk` should not receive:

- Raw worker logs.
- Full implementation diffs unless the user asks.
- Repeated checker/recovery attempts.
- Full plan-tree dumps.
- Node heartbeats or ask job internals.
- Low-level product/technical questions unless they change scope or risk.

## Worker Context Policy

Execution nodes may load noisy, detailed context because they are short-lived.
They should:

- Read only the task artifacts, files, logs, and specs needed for their work
  item.
- Produce a compact structured result before termination.
- Avoid writing durable plan-tree state directly.
- Leave raw logs in runtime artifacts when needed for inspection.
- Let the loop runner or monitor clear temporary state after the loop reaches a
  terminal or archived state.

## Planner Context Policy

Planner group may carry more detail than `frontdesk`, but less noise than execution
nodes. It should:

- Investigate repository-answerable questions before asking the user.
- Produce planning artifacts and open questions.
- Send detailed user questions through the clarification broker rather than
  turning `frontdesk` into a detailed planning assistant.
- Escalate only macro scope, risk, or goal changes to `frontdesk`.

## Runtime Artifact Policy

Runtime artifacts are the scratch space for fast-changing detail:

```text
.ccb/runtime/loops/<loop-id>/artifacts/
```

They may contain:

- Raw command logs.
- Worker notes.
- Intermediate diffs.
- Detailed checker findings.
- Recovery traces.
- Monitor evidence packages.

They should be summarized into durable plan-tree only when they become:

- Accepted evidence.
- A plan-changing blocker.
- A durable decision.
- A final completion summary.

## Cleanup Policy

Short-lived context should be cleared or archived when:

- The loop finishes successfully.
- The loop is blocked and escalated.
- The user cancels the objective.
- A new loop round replaces the old orchestrator and execution nodes.
- A retention limit is reached.

The cleanup operation must preserve compact evidence refs needed to understand
what happened without preserving every intermediate token of execution noise.

## Design Test

For any proposed new role, artifact, or handoff, ask:

1. Does this reduce long-lived context load, or just add another participant?
2. Is the detail needed by `frontdesk`, planner, worker, monitor, or only by a
   temporary artifact?
3. Can the role return a compact result instead of forwarding raw context?
4. Is the retained output durable evidence, or just execution noise?
5. Can the next loop start without inheriting irrelevant old detail?
