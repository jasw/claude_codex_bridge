---
name: round-verification
description: Verify integrated round evidence and return a machine-readable round result for script import.
---

# Round Verification

Use this skill after a bounded execution round has produced orchestrator,
coder, and code-reviewer evidence.

## Workflow

1. Read the task packet, execution contract, orchestrator summary, coder
   results, and code-reviewer results.
2. Check whether acceptance criteria were satisfied without hidden fallback,
   scope shrinkage, or missing evidence.
3. Return exactly one machine-readable result line.

```text
round result: pass|rework_node|partial|replan_required|global_blocker
```

## Boundaries

- Do not fix code.
- Do not change product scope.
- Do not infer pass without evidence.
- Do not directly edit authoritative CCB state or runtime files.
