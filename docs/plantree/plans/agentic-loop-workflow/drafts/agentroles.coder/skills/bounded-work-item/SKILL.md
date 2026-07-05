---
name: bounded-work-item
description: Execute one scoped implementation or investigation item and return evidence without changing workflow authority.
---

# Bounded Work Item

Use this skill when the orchestrator assigns a single work item with explicit
scope, non-goals, acceptance criteria, and verification expectations.

## Workflow

1. Read the task packet, execution contract, and assigned scope.
2. Inspect relevant files before editing.
3. Make the smallest change that satisfies the scoped item.
4. Run focused verification when possible.
5. Return files changed, evidence, blockers, and the result:
   `done`, `blocked`, or `needs_rework`.

## Boundaries

- Do not lower acceptance criteria.
- Do not silently substitute fallback behavior.
- Do not claim whole-round success.
- Do not directly edit authoritative CCB state or runtime files.
