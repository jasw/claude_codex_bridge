---
name: orchestration-bundle-candidate
description: Select one route and return one adaptive one-to-four-workgroup bundle candidate as reply-only evidence.
---

# Orchestration Bundle Candidate

Use this skill once per immaculate orchestrator activation. Consume only the
controller-supplied task packet, execution contract, detail artifacts,
effective-capacity snapshot, and expected bundle revision.

## Reply Contract

1. Return exactly one route decision:
   `direct_execution`, `needs_detail`, `macro_adjustment_request`, `blocked`,
   or `partial_completion`.
2. Return compact `orchestration_notes` citing the supplied task and contract
   refs.
3. For Config V3 `direct_execution` or `partial_completion`, always include
   exactly one fenced JSON candidate with schema
   `ccb.loop.orchestration_bundle_candidate.v1`. Include it even when one node
   is selected.
4. Config V2 may omit the candidate only for deterministic one-node
   compatibility. A decomposed Config V2 route must include it.

Candidate root fields are exactly `schema`, `task_id`, `bundle_revision`,
`selection`, `nodes`, `integration`, and `policy`. Selection fields are exactly
`workgroup_count`, `complexity`, `cutability`, `execution_shape`, and
`rationale`. `workgroup_count` must equal the node count.

## Adaptive Selection

Choose the smallest justified workgroup count from 1 to 4 using task
complexity, cutability, independently reviewable scopes, explicit dependency
needs, and supplied effective-capacity evidence. Capacity is a ceiling, not a
target. Do not target a desired count, split work to fill capacity, or reduce a
semantically required count because dispatch would be inconvenient.

Use one node for atomic or tightly coupled work. Use additional nodes only
when every node has a complete bounded work packet, independently checkable
acceptance and verification refs, safe allowed paths, and explicit
dependencies where outputs interact. Independent nodes require disjoint
allowed paths.

Each node must include `node_id`, `workgroup_id`, logical `coder` and
`code_reviewer` profiles, `depends_on`, `parallel_group`, a complete bounded
`work_packet`, `allowed_paths`, `acceptance_refs`, `verification_refs`, and a
unique deterministic `integration_order`. The work packet must state its goal,
declared refs, scope, non-goals, dependency evidence, expected evidence, and
verification obligations. `parallel_group` is evidence only; it is not a
topology communication edge or dispatch instruction.

Structural ambiguity requires `replan_required` evidence. Do not hide it with
silent serialization, count reduction, overlapping independent scopes, scope
shrinkage, fallback, or degradation.

## Authority Boundary

- Reply only; do not run `ccb`, `ccb_test`, wrappers, provider CLIs, or
  authority commands.
- Do not submit downstream asks or dispatch any role.
- Do not create task artifacts, work packets, tasks, loops, topology, agent
  names, panes, sessions, worktrees, branches, commits, or status transitions.
- The controller validates and imports candidate evidence, binds concrete
  agents, submits asks, integrates reviewed results, promotes or rolls back,
  records authority, and releases dynamic roles.
- Provider and model selection remain project configuration concerns. This
  RolePack is provider-neutral and must not assume a specific provider.
- There is no normal post-worker orchestrator activation. Only structural
  replan starts a new immaculate activation.
