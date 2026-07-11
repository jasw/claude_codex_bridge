---
name: assigned-review-chain
description: Submit one node result to the assigned Reviewer and finish only after a bounded pass/rework chain.
---

# Assigned Review Chain

Use this skill only when the node task names one assigned Reviewer.

## Submit

After implementation and verification, run exactly:

```bash
command ask --chain --artifact-reply <assigned-reviewer> <<'EOF'
Node: <node id>
Worktree: <assigned worktree>
Changed paths:
- <path>
Verification:
- <command and result>
Blockers:
- <none or exact blocker>
Review the current node against the supplied work packet and acceptance refs.
Your first non-empty reply line must be exactly one of:
- status: pass
- status: rework_required
- status: blocked
- status: non_converged
Do not put a preamble or code fence before that machine line.
EOF
```

Replace `<assigned-reviewer>` with the literal Reviewer name supplied in the
node task. It is not an environment variable and must not be inferred.
The machine-line requirement is part of every Reviewer request; do not rely on
the Reviewer's remembered role instructions alone.

Then stop. Do not poll, watch, ping, wait, or set a timeout.

## Continuation

- `status: pass`: do not modify files or run tools. Return the final node result
  immediately.
- `status: rework_required`: apply only the requested bounded repair, rerun
  verification, ask the same Reviewer with `--chain` again, then stop.
- `status: blocked` or `status: non_converged`: return a non-pass final result.

## Boundaries

- The assigned Reviewer is the only allowed target.
- Do not use plain ask, `--silence`, multiple targets, another role, or another
  CCB command.
- Do not claim `done` before Reviewer pass.
- Do not create commits, integrate, promote, write task/runtime authority, or
  release agents.
