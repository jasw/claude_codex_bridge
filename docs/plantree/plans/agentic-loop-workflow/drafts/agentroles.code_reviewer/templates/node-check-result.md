# Node Check Result

node id: <node-id>
workgroup id: <workgroup-id>
code reviewer: <agent-name>
check result: pass|rework_required|blocked|non_converged

## Exact Node Workspace

- workspace identity: <controller-supplied identity>
- base commit: <sha>
- head commit: <sha>
- reviewed tree digest: <digest>
- canonical node work packet: <ref>
- changed paths: <paths>
- allowed paths: <paths>
- acceptance refs: <refs>
- verification refs: <refs>

## Check Plan

- <focused verification>

## Findings

- <finding or none>

## Boundary Checks

- exact workspace/tree matched: yes|no
- scope violations: <none or paths>
- hidden fallback or degradation: <none or finding>
- missing acceptance/verification evidence: <none or refs>
- reviewed tree modified by reviewer: no

## Required Rework

- <specific rework or none>

This evidence is read-only. The reviewer cannot mark the task or round done,
create authority commits, integrate the node, or submit downstream asks.
