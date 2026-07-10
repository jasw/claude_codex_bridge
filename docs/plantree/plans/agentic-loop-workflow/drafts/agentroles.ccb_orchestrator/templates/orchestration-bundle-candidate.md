route: <direct_execution|needs_detail|macro_adjustment_request|blocked|partial_completion>
orchestration_notes: <compact rationale with supplied task and contract refs>

For Config V3 `direct_execution` or `partial_completion`, emit exactly one
candidate. Config V2 may omit it only for deterministic one-node
compatibility. Repeat the node object once per semantically justified
workgroup; do not use the displayed single object as a node-count preference.
Replace every angle-bracket placeholder before replying. Integer fields must
be JSON integers, not quoted placeholder strings.

orchestration_bundle:
```json
{
  "schema": "ccb.loop.orchestration_bundle_candidate.v1",
  "task_id": "<task-id>",
  "bundle_revision": "<positive-integer-supplied-by-controller>",
  "selection": {
    "workgroup_count": "<integer-1-to-4-equal-to-node-count>",
    "complexity": "<atomic|bounded|complex|very_complex>",
    "cutability": "<none|limited|high>",
    "execution_shape": "<single_unit|parallel|serial|mixed_dag>",
    "rationale": "<short-semantic-reason-for-smallest-justified-count>"
  },
  "nodes": [
    {
      "node_id": "<stable-node-id>",
      "workgroup_id": "<stable-workgroup-id>",
      "worker_profile": "coder",
      "reviewer_profile": "code_reviewer",
      "depends_on": ["<predecessor-node-id-if-any>"],
      "parallel_group": "<evidence-label-only>",
      "work_packet": "Goal: <bounded goal>\nDeclared refs: <task and contract refs>\nScope: <allowed work>\nNon-goals: <excluded work>\nDependencies: <accepted predecessor evidence or none>\nExpected evidence: <changed paths and checks>\nVerification: <bounded obligations>",
      "allowed_paths": ["<project-relative-path>"],
      "acceptance_refs": ["<known-artifact-ref>"],
      "verification_refs": ["<known-artifact-ref>"],
      "integration_order": "<unique-positive-integer>"
    }
  ],
  "integration": {
    "verification_refs": ["<known-artifact-ref>"],
    "project_root_verification_refs": ["<known-artifact-ref>"]
  },
  "policy": {
    "max_node_rework_rounds": "<non-negative-integer-within-policy>",
    "on_required_node_failure": "partial_or_blocked",
    "on_structural_failure": "replan_required"
  }
}
```

Candidate root fields are exactly the seven fields shown. Capacity is a
ceiling, not a target. Structural ambiguity is `replan_required`; never hide it
with serialization, node-count reduction, scope shrinkage, or fallback.
