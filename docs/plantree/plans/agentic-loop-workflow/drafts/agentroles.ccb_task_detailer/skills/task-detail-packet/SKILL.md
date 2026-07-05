---
name: task-detail-packet
description: Produce task-scoped detail artifacts and a detail packet for script-owned workflow import.
---

# Task Detail Packet

Use this skill when a macro task packet needs task-local execution detail before
orchestrator dispatch.

## Workflow

1. Read macro task refs, plan-tree refs, accepted decisions, source files,
   tests, and prior evidence.
2. Draft task-scoped detail design and source-evidence map.
3. Produce detailed acceptance, verification, and worker handoff notes.
4. Return a detail packet suitable for script import.
5. If detail is blocked, return clarification or macro-adjustment evidence.

## Boundaries

- Do not rewrite roadmap or accepted decisions directly.
- Do not lower acceptance criteria.
- Do not dispatch runtime agents.
- Do not directly edit authoritative CCB state or runtime files.
