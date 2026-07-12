---
name: planner-closure-backfill
description: Produce revision-fenced Planner replan or task-set closure proposals and compact Frontdesk status evidence from validated workflow envelopes.
---

# Planner Closure Backfill

Use this skill only when the activation mode is `detailer_replan` or
`task_set_closure`. Initial intake remains owned by `planner-task-packet`.

## Inputs

- expected PlanTree and task/task-set revisions
- original intake and Planner task refs
- validated Detailer macro-impact evidence, or task-set closure envelope
- child round, release, cleanup, and evidence digests
- current Brief/Roadmap/TODO summary supplied by the host

Do not run shell commands, file reads/searches, tests, builds, CCB commands, or
notification commands. Use only the compact authority envelope in the prompt.

## Semantic Decisions

Planner decides:

- whether Detailer evidence changes scope, dependency, acceptance, risk,
  Roadmap ordering, or only local implementation detail;
- which accepted facts and completed child outputs remain valid;
- how partial, blocked, or replan branches change Roadmap/TODO state;
- whether the next milestone is ready, needs clarification, blocked, or the
  macro request is terminal;
- what concise status Frontdesk may report to the user.

Planner does not decide whether child evidence, cleanup, release, identity, or
revision checks passed. Those are script-owned input facts.

## Output

Return exactly this one fenced section and no alternative authority shape:

````markdown
**planner-backfill.json**
```json
{
  "schema": "ccb.planner.backfill_proposal.v1",
  "mode": "detailer_replan|task_set_closure",
  "expected_plan_revision": 1,
  "task_or_task_set_id": "stable-id",
  "task_or_task_set_revision": 1,
  "closure_evidence_digest": "sha256:<64 lowercase hex>",
  "aggregate_result": "pass|partial|replan_required|blocked",
  "result": "closure_complete|closure_partial|task_set_replanned|closure_blocked",
  "brief_summary": "durable compact summary",
  "roadmap_transitions": [],
  "todo_transitions": [],
  "decision_refs": [],
  "open_question_refs": [],
  "evidence_refs": [],
  "accepted_scope": [],
  "unresolved_scope": [],
  "blockers": [],
  "replan_inputs": [],
  "next_milestone": {
    "kind": "selected|workflow_terminal|blocked_none",
    "ref": "stable-milestone-ref",
    "rationale": "semantic reason"
  },
  "frontdesk_notification_required": true,
  "frontdesk_status": {
    "schema": "ccb.planner.frontdesk_status.v1",
    "notification_identity": "stable-id",
    "aggregate_result": "pass|partial|replan_required|blocked",
    "accepted_scope": [],
    "unresolved_scope": [],
    "blockers": [],
    "next_milestone": {
      "kind": "selected|workflow_terminal|blocked_none",
      "ref": "stable-milestone-ref",
      "rationale": "semantic reason"
    },
    "evidence_refs": [],
    "user_report_body": "factual user-facing report"
  }
}
```
````

## Rules

- Preserve the script-owned `aggregate_result` exactly. Map it mechanically to
  `closure_complete`, `closure_partial`, `task_set_replanned`, or
  `closure_blocked`; never output a complete semantic result for non-pass.
- Never omit unresolved required scope from mixed outcomes.
- Multiple replan children produce one coherent macro proposal, not multiple
  independent Planner actions.
- Never overwrite a newer PlanTree revision. Return `revision_conflict` and the
  supplied current revision as a blocker.
- Preserve aggregate result, accepted scope, unresolved scope, blockers, next
  milestone, and evidence refs byte-for-byte in `frontdesk_status`.
- Do not fabricate child evidence, hashes, tests, release, cleanup, or user
  decisions.
- Do not modify PlanTree or send Frontdesk messages from this reply-only
  surface. The host imports the proposal and exposes any restricted delivery
  capability separately.
