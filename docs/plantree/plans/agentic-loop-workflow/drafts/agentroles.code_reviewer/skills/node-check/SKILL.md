---
name: node-check
description: Verify one coder node against the assigned execution contract and return pass, rework, blocked, or non-converged evidence.
---

# Node Check

Use this skill when the assigned Worker supplies a bounded node review packet
for its current node workspace.

## Workflow

1. Bind the review to the supplied node id, workspace identity, base commit,
   and head commit. CCB records the canonical tree digest outside provider
   text. Missing or mismatched visible identity is `blocked`.
2. Read the canonical node work packet, coder result, changed paths, acceptance
   refs, verification refs, and dependency evidence.
3. Check that every changed path is allowed and that no scope violation or
   undeclared dependency exists.
4. Evaluate the supplied verification evidence. Use only read-only checks that
   cannot mutate the reviewed tree; otherwise report the missing proof.
5. Audit hidden fallback, degradation, scope shrinkage, and missing evidence.
6. Return a parser-stable machine verdict as the first non-empty line:
   `status: pass`, `status: rework_required`, `status: blocked`, or
   `status: non_converged`. Put explanatory evidence after that line.

## Boundaries

- Do not approve contract-free work.
- Do not convert partial work into success.
- Do not edit files, apply fixes, create commits, integrate nodes, promote
  project-root state, or submit downstream asks.
- Do not directly edit authoritative CCB state or runtime files.
- Do not run `ccb`, `ccb_test`, or workflow wrappers.
- You cannot mark the task or round done.
- Provider and model selection remain project configuration concerns. This
  RolePack is provider-neutral and must not assume a specific provider.
