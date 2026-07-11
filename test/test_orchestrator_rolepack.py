from __future__ import annotations

import json
from pathlib import Path
import re
import shutil

from provider_profiles.codex_home_config import materialize_codex_home_config
from cli.services.role_command_policy import load_role_command_policy
from rolepacks.manifest import load_role_manifest


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DRAFTS = (
    REPO_ROOT
    / 'docs'
    / 'plantree'
    / 'plans'
    / 'agentic-loop-workflow'
    / 'drafts'
)
ORCHESTRATOR_ROLE = (
    WORKFLOW_DRAFTS
    / 'agentroles.ccb_orchestrator'
)
ROLE_EXPECTATIONS = {
    'agentroles.ccb_frontdesk': {
        'default': 'frontdesk',
        'skill': 'skills/frontdesk-intake',
        'templates': ('templates/macro-task-request.md',),
        'providers': ('codex', 'claude'),
    },
    'agentroles.ccb_planner': {
        'default': 'planner',
        'skill': 'skills/planner-task-packet',
        'templates': (
            'templates/task-packet.md',
            'templates/readiness.json',
            'templates/candidate-questions.jsonl',
        ),
    },
    'agentroles.ccb_plan_reviewer': {
        'default': 'plan_reviewer',
        'skill': 'skills/plan-readiness-review',
        'templates': ('templates/planner-review.md',),
    },
    'agentroles.ccb_clarification_broker': {
        'default': 'clarification_broker',
        'skill': 'skills/clarification-broker',
        'templates': ('templates/user-questions.md', 'templates/normalized-answers.jsonl'),
    },
    'agentroles.ccb_orchestrator': {
        'default': 'orchestrator',
        'skill': 'skills/orchestration-bundle-candidate',
        'templates': (
            'templates/orchestration-bundle-candidate.md',
            'templates/capacity-request.json',
            'templates/worker-ask.md',
            'templates/checker-ask.md',
            'templates/round-aggregation.md',
        ),
    },
    'agentroles.ccb_task_detailer': {
        'default': 'ccb_task_detailer',
        'skill': 'skills/task-detail-packet',
        'templates': ('templates/detail-packet.md',),
    },
    'agentroles.coder': {
        'default': 'coder',
        'skill': 'skills/bounded-work-item',
        'templates': ('templates/node-work-result.md',),
    },
    'agentroles.code_reviewer': {
        'default': 'code_reviewer',
        'skill': 'skills/node-check',
        'templates': ('templates/node-check-result.md',),
    },
    'agentroles.ccb_round_reviewer': {
        'default': 'ccb_round_reviewer',
        'skill': 'skills/round-verification',
        'templates': ('templates/round-result.md',),
    },
    'agentroles.ccb_worker': {
        'default': 'worker',
        'skill': 'skills/bounded-work-item',
        'templates': ('templates/node-work-result.md',),
    },
    'agentroles.ccb_checker': {
        'default': 'code_reviewer',
        'skill': 'skills/node-check',
        'templates': ('templates/node-check-result.md',),
    },
    'agentroles.ccb_round_checker': {
        'default': 'round_checker',
        'skill': 'skills/round-verification',
        'templates': ('templates/round-result.md',),
    },
}


def role_root(role_id: str) -> Path:
    return WORKFLOW_DRAFTS / role_id


def test_orchestrator_rolepack_translates_ccb_skills() -> None:
    manifest = load_role_manifest(ORCHESTRATOR_ROLE)

    assert manifest.id == 'agentroles.ccb_orchestrator'
    assert manifest.default_agent_name == 'orchestrator'
    assert {'codex', 'claude', 'qwen', 'zai'} <= set(manifest.providers)
    assert manifest.manifest['memory']['files'] == ['memory.md', 'adapters/ccb/memory.md']
    assert manifest.manifest['skills']['codex'] == ['skills/orchestration-bundle-candidate']
    assert manifest.manifest['skills']['qwen'] == ['skills/orchestration-bundle-candidate']


def test_orchestrator_rolepack_is_reply_only_for_capacity_and_lifecycle() -> None:
    skill = (
        ORCHESTRATOR_ROLE
        / 'adapters'
        / 'ccb'
        / 'skills'
        / 'orchestrator-capacity'
        / 'SKILL.md'
    ).read_text(encoding='utf-8')

    role_memory = (ORCHESTRATOR_ROLE / 'memory.md').read_text(encoding='utf-8')
    adapter_memory = (ORCHESTRATOR_ROLE / 'adapters' / 'ccb' / 'memory.md').read_text(encoding='utf-8')
    adapter = (ORCHESTRATOR_ROLE / 'adapters' / 'ccb' / 'adapter.toml').read_text(encoding='utf-8')
    dynamic_skill = (
        ORCHESTRATOR_ROLE
        / 'adapters'
        / 'ccb'
        / 'skills'
        / 'dynamic-agent-lifecycle'
        / 'SKILL.md'
    ).read_text(encoding='utf-8')
    capacity_reference = (
        ORCHESTRATOR_ROLE / 'references' / 'capacity-boundary.md'
    ).read_text(encoding='utf-8')

    assert 'skills = []' in adapter
    for text in (role_memory, adapter_memory, skill, dynamic_skill, capacity_reference):
        assert (
            'Do not run CCB commands' in text
            or 'Do not run `ccb plan`' in text
            or 'Never run `ccb plan`' in text
        )
        assert 'runner owns' in text.lower() or 'supervisor/runner owns' in text.lower()
        assert 'evidence only' in text
    forbidden = (
        'ccb loop capacity ensure --loop-id',
        'ccb loop capacity status --loop-id',
        'ccb loop capacity release --loop-id',
        'ccb agent add <name>',
        'ccb agent release <agent>',
        'command ask --chain',
        'Allowed commands:',
        'Use only CCB-owned loop capacity commands',
        'ccb layout status --json',
    )
    for text in (role_memory, adapter_memory, skill, dynamic_skill, capacity_reference):
        for item in forbidden:
            assert item not in text


def test_dynamic_agent_lifecycle_skill_declares_non_loop_command_boundary() -> None:
    skill = (
        ORCHESTRATOR_ROLE
        / 'adapters'
        / 'ccb'
        / 'skills'
        / 'dynamic-agent-lifecycle'
        / 'SKILL.md'
    ).read_text(encoding='utf-8')

    assert 'not projected' in skill
    assert 'active provider skill' in skill
    assert 'Do not run CCB commands' in skill
    assert 'runner owns command execution' in skill.lower()
    for forbidden in (
        'ccb agent status --json',
        'ccb agent show <agent> --json',
        'ccb layout resolve <agent>',
        'ccb agent add <name>:<provider>',
        'ccb agent park <agent>',
        'ccb agent resume <agent>',
        'ccb agent release <agent>',
        'ccb layout status --json',
        'remove --policy kill',
    ):
        assert forbidden not in skill


def test_orchestrator_capacity_template_keeps_placement_ccb_owned() -> None:
    template = json.loads(
        (ORCHESTRATOR_ROLE / 'templates' / 'capacity-request.json').read_text(
            encoding='utf-8'
        )
    )

    assert template['placement_policy'] == 'ccb_runtime_layout_manager'
    assert template['forbidden_placement_overrides'] == [
        'window_name',
        'window_class',
        'pane_id',
    ]
    assert 'window_name' not in template
    assert 'window_class' not in template
    assert 'pane_id' not in template


def test_workflow_rolepacks_translate_and_project_role_skills() -> None:
    for role_id, expectation in ROLE_EXPECTATIONS.items():
        manifest = load_role_manifest(role_root(role_id))

        assert manifest.id == role_id
        assert manifest.default_agent_name == expectation['default']
        expected_providers = expectation.get('providers')
        if expected_providers is None:
            assert {'codex', 'claude', 'qwen', 'zai'} <= set(manifest.providers)
            skill_providers = ('codex', 'qwen')
        else:
            assert manifest.providers == tuple(expected_providers)
            skill_providers = tuple(expected_providers)
        assert manifest.manifest['memory']['files'] == ['memory.md', 'adapters/ccb/memory.md']
        expected_skills = tuple(expectation['skills']) if 'skills' in expectation else (expectation['skill'],)
        for provider in skill_providers:
            assert manifest.manifest['skills'][provider] == list(expected_skills)
        for skill in expected_skills:
            assert (manifest.root / skill / 'SKILL.md').is_file()


def test_workflow_rolepacks_include_common_authority_rule_and_templates() -> None:
    shared = (WORKFLOW_DRAFTS / '_shared' / 'authority-rule.md').read_text(encoding='utf-8')
    assert 'You may author semantic artifacts and recommend transitions.' in shared
    assert 'You must not directly edit authoritative state' in shared
    assert 'program kernel should stay simple and stable' in shared

    for role_id, expectation in ROLE_EXPECTATIONS.items():
        root = role_root(role_id)
        memory_files = [root / 'memory.md']
        adapter_memory = root / 'adapters' / 'ccb' / 'memory.md'
        if adapter_memory.is_file():
            memory_files.append(adapter_memory)
        memory = '\n'.join(path.read_text(encoding='utf-8') for path in memory_files)
        assert 'You may author semantic artifacts and recommend transitions.' in memory
        assert 'You must not directly edit authoritative state' in memory
        assert 'hand-edit state files' in memory
        assert 'supervisor/runner' in memory
        assert 'Do not run CCB' in memory or 'Do not use CCB `ask`' in memory or 'Never run `ccb plan task-artifact`' in memory
        for forbidden in (
            'Use CCB-owned commands',
            'host-provided skill wrappers',
            'for authoritative writes',
        ):
            assert forbidden not in memory
        for template in expectation['templates']:
            assert (root / template).is_file(), f'{role_id} missing {template}'


def test_frontdesk_planner_and_task_detailer_are_reply_only_for_authority_and_routing() -> None:
    for role_id in ('agentroles.ccb_frontdesk', 'agentroles.ccb_planner', 'agentroles.ccb_task_detailer'):
        root = role_root(role_id)
        combined = '\n'.join(
            [
                (root / 'memory.md').read_text(encoding='utf-8'),
                (root / 'adapters' / 'ccb' / 'memory.md').read_text(encoding='utf-8'),
            ]
        )

        assert (
            'Return semantic artifacts' in combined
            or 'Return semantic detail artifacts' in combined
            or 'Reply only with macro task requests' in combined
            or 'reply-visible artifacts' in combined
        )
        assert (
            'Do not run CCB authority commands' in combined
            or 'Do not use CCB `ask`' in combined
            or 'Never run `ccb plan task-artifact`' in combined
        )
        assert 'supervisor/runner' in combined
        for forbidden in (
            'Allowed CCB surfaces',
            'Use CCB-owned commands',
            'host-provided skill wrappers',
            'Produce task-local detail artifacts for `ccb plan task-artifact` import',
            'Use CCB `ask` only for macro delegation',
        ):
            assert forbidden not in combined
        if role_id == 'agentroles.ccb_task_detailer':
            assert 'Do not write detail artifacts into the project tree for later self-import' in combined
            assert 'supervisor import files' in combined
            assert 'later self-import' in combined


def test_frontdesk_rolepack_is_read_only_intake_not_implementation() -> None:
    root = role_root('agentroles.ccb_frontdesk')
    manifest = load_role_manifest(root)
    combined = '\n'.join(
        [
            (root / 'memory.md').read_text(encoding='utf-8'),
            (root / 'adapters' / 'ccb' / 'memory.md').read_text(encoding='utf-8'),
            (root / 'skills' / 'frontdesk-intake' / 'SKILL.md').read_text(encoding='utf-8'),
        ]
    )

    assert manifest.manifest['permissions']['read_files'] is True
    assert manifest.manifest['permissions']['write_files'] is False
    assert manifest.manifest['compatibility']['providers'] == ['codex', 'claude']
    assert manifest.manifest['adapters']['ccb']['command_surface'] == 'adapters/ccb/command-surface.toml'
    assert 'first non-empty line exactly\n  `**Intake Evidence**`' in combined
    assert 'Every turn, classify the user message first' in combined
    assert 'Every user turn must pass this gate' in combined
    assert 'choose `planner_handoff`' in combined
    assert 'active command surface is closed' in combined
    assert 'controller observes your final reply' in combined
    assert 'ccb frontdesk forward-planner --request-id <stable-request-id> --intake-base64' not in combined
    assert 'ordinary `ccb ask`' in combined
    assert 'heredocs' in combined
    assert '`--file` handoff' in combined
    assert '--intake-base64 <base64-utf8-artifact>' not in combined
    assert "<<'EOF'" not in combined
    assert 'frontdesk_intake_status: ok' not in combined
    assert 'Do not implement the request' in combined or 'Do not perform implementation' in combined
    assert 'Do not create, edit, delete, or format' in combined
    assert 'Do not run tests, builds, linters' in combined
    assert 'Convert implementation requests' in combined
    for forbidden in (
        'write_files = true',
        'Implemented the',
        'Changed:',
        'Verification:',
        'ccb ask planner',
        'command ask planner',
        'Bash(',
    ):
        assert forbidden not in combined


def test_frontdesk_rolepack_declares_closed_observer_handoff_surface() -> None:
    manifest = load_role_manifest(role_root('agentroles.ccb_frontdesk'))
    policy = load_role_command_policy(manifest)

    assert policy is not None
    assert policy.mode == 'deny_all_except'
    assert policy.enforcement == 'required'
    assert policy.if_unsupported == 'fail_mount'
    assert policy.generic_shell is False
    assert policy.generic_ccb is False
    assert policy.supported_providers == ('codex', 'claude')
    assert policy.allowed_effects == ('observer_intake_handoff',)
    assert {'task_create', 'artifact_import', 'status_write', 'runner_start', 'worker_dispatch'} <= set(
        policy.forbidden_effects
    )
    assert policy.allowed == ()


def test_planner_rolepack_is_closed_reply_only_planning_surface() -> None:
    root = role_root('agentroles.ccb_planner')
    manifest = load_role_manifest(root)
    policy = load_role_command_policy(manifest)
    combined = '\n'.join(
        [
            (root / 'memory.md').read_text(encoding='utf-8'),
            (root / 'adapters' / 'ccb' / 'memory.md').read_text(encoding='utf-8'),
            (root / 'skills' / 'planner-task-packet' / 'SKILL.md').read_text(encoding='utf-8'),
        ]
    )

    assert manifest.manifest['permissions']['read_files'] is False
    assert manifest.manifest['permissions']['write_files'] is False
    assert manifest.manifest['adapters']['ccb']['command_surface'] == 'adapters/ccb/command-surface.toml'
    assert policy is not None
    assert policy.mode == 'deny_all_except'
    assert policy.enforcement == 'required'
    assert policy.if_unsupported == 'fail_mount'
    assert policy.generic_shell is False
    assert policy.generic_ccb is False
    assert policy.supported_providers == ('codex', 'claude')
    assert policy.allowed_effects == ('semantic_planning_reply',)
    assert {'shell_exec', 'file_search', 'file_read', 'file_write', 'implementation'} <= set(
        policy.forbidden_effects
    )
    assert policy.allowed == ()
    assert 'Do not run shell commands' in combined
    assert 'file searches' in combined
    assert 'Allowed Change Paths' in combined
    assert 'Use `task_set` only when the controller prompt explicitly says' in combined
    assert 'orchestrator owns implementation-node' in combined
    assert 'do not use it to pre-slice one' in combined
    assert '## Acceptance Criteria' in combined
    assert '## Interface Contracts' in combined
    assert '## Execution Decomposition Inputs' in combined
    assert 'A stable interface is parallelization evidence' in combined


def test_coder_rolepack_is_workspace_only_and_reply_only_for_workflow_authority() -> None:
    root = role_root('agentroles.coder')
    combined = '\n'.join(
        [
            (root / 'memory.md').read_text(encoding='utf-8'),
            (root / 'adapters' / 'ccb' / 'memory.md').read_text(encoding='utf-8'),
            (root / 'skills' / 'bounded-work-item' / 'SKILL.md').read_text(encoding='utf-8'),
        ]
    )

    assert 'Do not run CCB commands' in combined
    assert 'supervisor/runner owns' in combined
    assert 'After the final required verification command completes' in combined
    assert 'send the final answer immediately' in combined
    for forbidden in (
        'Use CCB-owned commands',
        'host-provided skill wrappers',
        'for authoritative writes',
        'such as `ccb plan`',
    ):
        assert forbidden not in combined


def _combined_role_contract(role_id: str) -> str:
    root = role_root(role_id)
    paths = [root / 'memory.md', root / 'adapters' / 'ccb' / 'memory.md']
    manifest = load_role_manifest(root)
    for skill in manifest.manifest['skills']['codex']:
        paths.append(root / skill / 'SKILL.md')
    return '\n'.join(path.read_text(encoding='utf-8') for path in paths)


def test_p1_orchestrator_rolepack_declares_adaptive_bundle_contract() -> None:
    manifest = load_role_manifest(ORCHESTRATOR_ROLE)
    activation = manifest.table('activation')
    combined = _combined_role_contract('agentroles.ccb_orchestrator')
    template = (
        ORCHESTRATOR_ROLE / 'templates' / 'orchestration-bundle-candidate.md'
    ).read_text(encoding='utf-8')
    fenced = re.search(r'```json\s*\n(.*?)\n```', template, flags=re.DOTALL)

    assert activation == {
        'context_lifecycle': 'immaculate',
        'context_scope': 'activation',
        'history_reuse': False,
        'rehydration_source': 'controller_supplied_artifact_refs',
        'recommended_workspace_mode': 'inplace',
    }
    assert manifest.manifest['permissions']['write_files'] is False
    assert fenced is not None
    candidate = json.loads(fenced.group(1))
    assert set(candidate) == {
        'schema',
        'task_id',
        'bundle_revision',
        'selection',
        'nodes',
        'integration',
        'policy',
    }
    assert candidate['schema'] == 'ccb.loop.orchestration_bundle_candidate.v1'
    assert set(candidate['selection']) == {
        'workgroup_count',
        'complexity',
        'cutability',
        'execution_shape',
        'rationale',
    }
    assert set(candidate['nodes'][0]) == {
        'node_id',
        'workgroup_id',
        'worker_profile',
        'reviewer_profile',
        'depends_on',
        'parallel_group',
        'work_packet',
        'allowed_paths',
        'acceptance_refs',
        'verification_refs',
        'integration_order',
    }
    assert candidate['nodes'][0]['worker_profile'] == 'coder'
    assert candidate['nodes'][0]['reviewer_profile'] == 'code_reviewer'
    assert 'worker_profile' in combined
    assert 'reviewer_profile' in combined
    combined_single_line = ' '.join(combined.split())
    assert 'Do not emit nested `coder` or `code_reviewer` objects' in combined_single_line
    assert '`work_packet` must be one JSON string' in combined_single_line
    assert '`node_id` and `workgroup_id` must be short agent-name-safe identifiers' in combined_single_line
    assert 'contain at most 32 characters total' in combined_single_line
    assert 'integration` fields are exactly `verification_refs` and `project_root_verification_refs`' in combined_single_line
    assert 'Both integration arrays must be non-empty' in combined_single_line
    assert 'never emit an empty `project_root_verification_refs` list' in combined_single_line
    assert 'do not emit `mode`, `order`' in combined_single_line
    assert 'policy` fields are exactly `max_node_rework_rounds`, `on_required_node_failure`, and `on_structural_failure`' in combined_single_line
    assert '`on_required_node_failure` must be `partial_or_blocked`' in combined_single_line
    assert '`on_structural_failure` must be `replan_required`' in combined_single_line
    assert 'return_failed_node_for_rework' in combined_single_line
    assert 'logical `coder` and' not in combined_single_line
    assert 'exactly one route decision' in combined
    assert 'Config V3' in combined
    assert 'direct_execution' in combined
    assert 'partial_completion' in combined
    assert 'smallest justified workgroup count from 1 to 4' in combined
    assert 'capacity is a ceiling, not a target' in ' '.join(combined.split()).lower()
    assert 'the smallest justified graph includes those units as separate' in combined_single_line
    assert 'A stable public interface, shared final root verification' in combined_single_line
    assert 'emit separate nodes in the same ready parallel group' in combined_single_line
    assert 'Do not invent a dependency merely because one module calls a stable interface' in combined_single_line
    assert 'Emit `serial` or `mixed_dag` edges only when' in combined_single_line
    assert 'final root verification do not justify a dependency edge' in combined_single_line
    assert 'Do not merge the fourth unit merely to avoid execution window overflow' in combined_single_line
    assert 'concrete path overlap or predecessor' in template
    assert 'Structural ambiguity requires `replan_required`' in combined
    assert 'silent serialization' in combined
    assert 'parallel_group is evidence only' in combined
    assert 'Verification Commands' in combined
    assert 'direct argv commands' in combined
    assert 'without a shell' in combined
    assert 'Verification Contract:' in combined
    assert 'do not submit downstream asks' in combined.lower()
    assert 'normal post-worker orchestrator activation' in combined
    assert '"workgroup_count": 1' not in template
    assert 'fill capacity' not in template.lower()


def test_p1_node_rolepacks_bind_canonical_packet_and_exact_review_tree() -> None:
    coder = load_role_manifest(role_root('agentroles.coder'))
    reviewer = load_role_manifest(role_root('agentroles.code_reviewer'))
    coder_contract = _combined_role_contract('agentroles.coder')
    reviewer_contract = _combined_role_contract('agentroles.code_reviewer')
    coder_template = (
        role_root('agentroles.coder') / 'templates' / 'node-work-result.md'
    ).read_text(encoding='utf-8')
    reviewer_template = (
        role_root('agentroles.code_reviewer') / 'templates' / 'node-check-result.md'
    ).read_text(encoding='utf-8')

    for manifest in (coder, reviewer):
        assert manifest.table('activation')['context_lifecycle'] == 'immaculate'
        assert manifest.table('activation')['history_reuse'] is False
    assert coder.manifest['permissions']['write_files'] is True
    assert reviewer.manifest['permissions']['write_files'] is False
    assert 'status: done|blocked|needs_rework' in coder_template
    assert '\nresult: done|blocked|needs_rework' not in coder_template
    assert 'status: pass|rework_required|blocked|non_converged' in reviewer_template
    assert 'check result:' not in reviewer_template
    for required in (
        'canonical node work packet',
        'declared refs',
        'allowed paths',
        'changed paths',
        'verification evidence',
        'blockers',
        'Do not expand scope',
        'fallback',
    ):
        assert required in coder_contract + coder_template
    for required in (
        'exact node workspace',
        'base commit',
        'head commit',
        'tree digest',
        'read-only',
        'scope violations',
        'acceptance refs',
        'verification refs',
        'cannot mark the task or round done',
    ):
        assert required in reviewer_contract + reviewer_template


def test_p1_round_reviewer_is_immaculate_and_rejects_unproven_integration() -> None:
    root = role_root('agentroles.ccb_round_reviewer')
    manifest = load_role_manifest(root)
    combined = _combined_role_contract('agentroles.ccb_round_reviewer')
    template = (root / 'templates' / 'round-result.md').read_text(encoding='utf-8')

    assert manifest.table('activation')['context_lifecycle'] == 'immaculate'
    assert manifest.table('activation')['context_scope'] == 'activation'
    assert manifest.manifest['permissions']['write_files'] is False
    assert template.startswith('round result: pass|partial|replan_required|blocked')
    normalized_contract = ' '.join((combined + template).split()).lower()
    for required in (
        'compact node-review evidence',
        'integration evidence',
        'project-root verification evidence',
        'missing node review',
        'integration drift',
        'scope violation',
        'hidden fallback',
        'partial promoted delta',
        'unproven cleanup',
        'cannot mark the task or round done',
    ):
        assert required in normalized_contract


def test_p1_task_detailer_returns_global_impact_and_planner_backfill_evidence() -> None:
    root = role_root('agentroles.ccb_task_detailer')
    manifest = load_role_manifest(root)
    combined = _combined_role_contract('agentroles.ccb_task_detailer')
    template = (root / 'templates' / 'detail-packet.md').read_text(encoding='utf-8')

    assert manifest.table('activation')['context_lifecycle'] == 'immaculate'
    assert manifest.manifest['permissions']['write_files'] is False
    assert 'global impact: none|bounded|macro' in combined + template
    assert 'planner backfill' in (combined + template).lower()
    assert 'never dispatch workers' in (combined + template).lower()
    for heading in (
        '## task-detail-design.md',
        '## brief-update-summary.md',
        '## detail-packet.md',
    ):
        assert heading in template


def test_p1_rolepacks_are_provider_neutral_and_keep_project_config_authority() -> None:
    role_ids = (
        'agentroles.ccb_orchestrator',
        'agentroles.coder',
        'agentroles.code_reviewer',
        'agentroles.ccb_round_reviewer',
        'agentroles.ccb_task_detailer',
    )
    for role_id in role_ids:
        manifest = load_role_manifest(role_root(role_id))
        combined = _combined_role_contract(role_id)
        assert {'codex', 'claude', 'gemini', 'opencode', 'kimi', 'mimo', 'qwen', 'zai', 'droid'} <= set(
            manifest.providers
        )
        assert 'Provider and model selection remain project configuration concerns' in combined
        assert 'Codex-only' not in combined


def test_round_reviewer_and_orchestrator_templates_share_result_contract() -> None:
    accepted_round_template = (
        WORKFLOW_DRAFTS
        / 'agentroles.ccb_round_reviewer'
        / 'templates'
        / 'round-result.md'
    ).read_text(encoding='utf-8')
    accepted_round_memory = (
        WORKFLOW_DRAFTS
        / 'agentroles.ccb_round_reviewer'
        / 'memory.md'
    ).read_text(encoding='utf-8')
    accepted_round_adapter = (
        WORKFLOW_DRAFTS
        / 'agentroles.ccb_round_reviewer'
        / 'adapters'
        / 'ccb'
        / 'memory.md'
    ).read_text(encoding='utf-8')
    accepted_round_skill = (
        WORKFLOW_DRAFTS
        / 'agentroles.ccb_round_reviewer'
        / 'skills'
        / 'round-verification'
        / 'SKILL.md'
    ).read_text(encoding='utf-8')
    legacy_round_template = (
        WORKFLOW_DRAFTS
        / 'agentroles.ccb_round_checker'
        / 'templates'
        / 'round-result.md'
    ).read_text(encoding='utf-8')
    aggregation_template = (
        ORCHESTRATOR_ROLE
        / 'templates'
        / 'round-aggregation.md'
    ).read_text(encoding='utf-8')
    checker_ask = (ORCHESTRATOR_ROLE / 'templates' / 'checker-ask.md').read_text(
        encoding='utf-8'
    )

    result_line = 'round result: pass|partial|replan_required|blocked'
    legacy_result_line = 'round result: pass|rework_node|partial|replan_required|global_blocker'
    assert accepted_round_template.startswith(result_line)
    for text in (accepted_round_template, accepted_round_memory, accepted_round_adapter, accepted_round_skill):
        assert result_line in text
        assert 'first non-empty line' in text
        assert 'preamble' in text
        assert 'Markdown fence' in text
        assert 'Do not run tests' in text
        assert legacy_result_line not in text
    assert legacy_result_line in legacy_round_template
    assert 'aggregation result: complete|partial|blocked|replan_required' in aggregation_template
    assert 'pass`, `rework_required`, `blocked`, `non_converged' in checker_ask


def test_orchestrator_rolepack_does_not_project_command_skills_to_codex_home(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('AGENT_ROLES_STORE', str(tmp_path / '.roles'))
    installed = tmp_path / '.roles' / 'installed' / 'agentroles.ccb_orchestrator' / 'current'
    installed.parent.mkdir(parents=True)
    shutil.copytree(ORCHESTRATOR_ROLE, installed)

    project = tmp_path / 'project'
    (project / '.ccb').mkdir(parents=True)
    (project / '.ccb' / 'ccb.config').write_text(
        '\n'.join(
            [
                'version = 2',
                'entry_window = "main"',
                '',
                '[windows]',
                'main = "orchestrator:codex"',
                '',
                '[agents.orchestrator]',
                'role = "agentroles.ccb_orchestrator"',
            ]
        )
        + '\n',
        encoding='utf-8',
    )
    target_home = tmp_path / 'managed-codex'
    source_home = tmp_path / 'source-codex'

    materialize_codex_home_config(
        target_home,
        source_home=source_home,
        project_root=project,
        agent_name='orchestrator',
        workspace_path=project,
    )

    assert not (target_home / 'skills' / 'orchestrator-capacity').exists()
    assert not (target_home / 'skills' / 'dynamic-agent-lifecycle').exists()
    projected = target_home / 'skills' / 'orchestration-bundle-candidate' / 'SKILL.md'
    assert projected.is_file()
    assert 'ccb.loop.orchestration_bundle_candidate.v1' in projected.read_text(encoding='utf-8')


def test_planner_rolepack_projects_planner_skill_to_codex_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('AGENT_ROLES_STORE', str(tmp_path / '.roles'))
    installed = tmp_path / '.roles' / 'installed' / 'agentroles.ccb_planner' / 'current'
    installed.parent.mkdir(parents=True)
    shutil.copytree(role_root('agentroles.ccb_planner'), installed)

    project = tmp_path / 'project'
    (project / '.ccb').mkdir(parents=True)
    (project / '.ccb' / 'ccb.config').write_text(
        '\n'.join(
            [
                'version = 2',
                'entry_window = "main"',
                '',
                '[windows]',
                'main = "planner:codex"',
                '',
                '[agents.planner]',
                'role = "agentroles.ccb_planner"',
            ]
        )
        + '\n',
        encoding='utf-8',
    )
    target_home = tmp_path / 'managed-codex'
    source_home = tmp_path / 'source-codex'

    materialize_codex_home_config(
        target_home,
        source_home=source_home,
        project_root=project,
        agent_name='planner',
        workspace_path=project,
    )

    projected = target_home / 'skills' / 'planner-task-packet' / 'SKILL.md'
    assert projected.is_file()
    assert 'readiness recommendations' in projected.read_text(encoding='utf-8')
