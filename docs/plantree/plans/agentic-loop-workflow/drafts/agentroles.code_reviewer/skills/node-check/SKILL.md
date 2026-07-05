---
name: node-check
description: Verify one coder node against the assigned execution contract and return pass, rework, blocked, or non-converged evidence.
---

# Node Check

Use this skill when reviewing a bounded coder result.

## Workflow

1. Read the assigned execution contract and coder evidence.
2. Build a focused check plan from acceptance criteria.
3. Run or specify the smallest useful verification.
4. Audit hidden fallback, degradation, scope shrinkage, and missing evidence.
5. Return `pass`, `rework_required`, `blocked`, or `non_converged`.

## Boundaries

- Do not approve contract-free work.
- Do not convert partial work into success.
- Do not directly edit authoritative CCB state or runtime files.
