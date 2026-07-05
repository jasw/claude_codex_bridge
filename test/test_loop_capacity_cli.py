from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from agents.config_loader import load_project_config
from ccbd.reload_plan import build_reload_dry_run_plan
from cli.context import CliContextBuilder
from cli.models import ParsedLoopCapacityCommand, ParsedLoopRunOnceCommand, ParsedLoopRunnerCommand
from cli.parser import CliParser
from cli.phase2 import maybe_handle_phase2
from cli.phase2_runtime.handlers_ops import handle_loop_run_once
from cli.services import ask as ask_service
from cli.services import loop_ask_first as loop_ask_first_module
from cli.services.ask_runtime import AskSummary
from cli.services.loop_run_once import loop_run_once
from cli.services.loop_runner import loop_runner_once
from cli.services.plan_tasks import plan_task
from cli.services.watch import WatchEventBatch
import cli.services.loop_capacity as loop_capacity_module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _write_json(path: Path, payload: dict[str, object]) -> None:
    _write(path, json.dumps(payload, ensure_ascii=False, indent=2) + '\n')


def _seed_copy_workspace_binding(context, project_root: Path, target: str) -> Path:
    workspace = project_root / '.ccb' / 'workspaces' / target
    for path in sorted(project_root.rglob('*')):
        try:
            relative = path.relative_to(project_root)
        except ValueError:
            continue
        if relative.parts and relative.parts[0] == '.ccb':
            continue
        if path.is_dir():
            continue
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    _write_json(
        workspace / '.ccb-workspace.json',
        {
            'agent_name': target,
            'workspace_mode': 'copy',
            'workspace_path': str(workspace),
            'target_project': str(project_root),
            'project_id': context.project.project_id,
        },
    )
    return workspace


def _write_installed_role(store_root: Path, role_id: str, *, default_agent_name: str) -> None:
    _write(
        store_root / 'installed' / role_id / 'current' / 'role.toml',
        f'''id = "{role_id}"
version = "0.1.0"

[identity]
default_agent_name = "{default_agent_name}"
''',
    )


def _project_with_loop_capacity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project_root = tmp_path / 'repo-loop-capacity'
    role_store = tmp_path / 'roles'
    _write_installed_role(role_store, 'agentroles.coder', default_agent_name='coder')
    _write_installed_role(role_store, 'agentroles.code_reviewer', default_agent_name='code_reviewer')
    monkeypatch.setenv('AGENT_ROLES_STORE', str(role_store))
    _write(
        project_root / '.ccb' / 'ccb.config',
        """cmd; orchestrator:codex

[loop.capacity]
enabled = true
max_nodes = 3
default_lifetime = "current_round"
name_template = "loop-{loop_id}-{profile}-{index}"
reuse = "prefer_idle"

[loop.role_profiles.worker]
role = "agentroles.coder"
provider = "codex"
thinking = "high"
workspace_mode = "git-worktree"
workspace_group = "worker_pool"
max_instances = 2
reuse = "prefer_idle"

[loop.role_profiles.code_reviewer]
role = "agentroles.code_reviewer"
provider = "codex"
thinking = "medium"
workspace_mode = "git-worktree"
workspace_group = "review_pool"
max_instances = 1
""",
    )
    return project_root


def _project_with_workflow_topology(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project_root = tmp_path / 'repo-loop-workflow-dispatch'
    role_store = tmp_path / 'roles-workflow-dispatch'
    for role_id, default_agent_name in (
        ('agentroles.ccb_frontdesk', 'ccb_frontdesk'),
        ('agentroles.ccb_task_detailer', 'ccb_task_detailer'),
        ('agentroles.ccb_planner', 'ccb_planner'),
        ('agentroles.ccb_orchestrator', 'ccb_orchestrator'),
        ('agentroles.ccb_round_reviewer', 'ccb_round_reviewer'),
        ('agentroles.coder', 'coder'),
        ('agentroles.code_reviewer', 'code_reviewer'),
    ):
        _write_installed_role(role_store, role_id, default_agent_name=default_agent_name)
    monkeypatch.setenv('AGENT_ROLES_STORE', str(role_store))
    _write(
        project_root / '.ccb' / 'ccb.config',
        """version = 2
entry_window = "ccb-user"

[windows]
ccb-user = "bootstrap:codex"

[loop.capacity]
enabled = true
max_nodes = 8
default_lifetime = "current_loop"
name_template = "loop-{loop_id}-{profile}-{index}"
reuse = "prefer_idle"

[loop.role_profiles.ccb_frontdesk]
role = "agentroles.ccb_frontdesk"
provider = "codex"
workspace_mode = "inplace"
max_instances = 1

[loop.role_profiles.ccb_task_detailer]
role = "agentroles.ccb_task_detailer"
provider = "codex"
workspace_mode = "inplace"
max_instances = 1

[loop.role_profiles.ccb_planner]
role = "agentroles.ccb_planner"
provider = "codex"
workspace_mode = "inplace"
max_instances = 1

[loop.role_profiles.ccb_orchestrator]
role = "agentroles.ccb_orchestrator"
provider = "codex"
workspace_mode = "inplace"
max_instances = 1

[loop.role_profiles.ccb_round_reviewer]
role = "agentroles.ccb_round_reviewer"
provider = "codex"
workspace_mode = "inplace"
max_instances = 1

[loop.role_profiles.coder]
role = "agentroles.coder"
provider = "codex"
workspace_mode = "git-worktree"
max_instances = 2

[loop.role_profiles.code_reviewer]
role = "agentroles.code_reviewer"
provider = "codex"
workspace_mode = "git-worktree"
max_instances = 2
""",
    )
    return project_root


def _add_ready_plan_task(
    project_root: Path,
    *,
    task_id: str = 'task-001',
    task_packet_text: str = 'task packet text\n',
    execution_contract_text: str = 'execution contract text\n',
) -> None:
    plan_root = project_root / 'docs' / 'plantree' / 'plans' / 'demo-plan'
    task_root = plan_root / 'tasks' / task_id
    _write(plan_root / 'README.md', '# Demo Plan\n')
    artifacts: dict[str, dict[str, object]] = {}
    for kind, filename, text in (
        ('task_packet', 'task_packet.md', task_packet_text),
        ('execution_contract', 'execution_contract.md', execution_contract_text),
    ):
        path = task_root / filename
        _write(path, text)
        artifacts[kind] = {
            'kind': kind,
            'artifact_kind': kind,
            'path': str(path.relative_to(project_root)),
            'artifact_path': str(path.relative_to(project_root)),
            'source_path': str(path.relative_to(project_root)),
            'sha256': 'test',
            'bytes': len(text.encode('utf-8')),
            'imported_at': '2026-06-27T00:00:00Z',
        }
    record = {
        'task_id': task_id,
        'title': 'Task id bridge',
        'plan_slug': 'demo-plan',
        'plan_root': 'docs/plantree/plans/demo-plan',
        'status': 'ready_for_orchestration',
        'current_loop': None,
        'owner': 'loop_runner',
        'next_owner': 'orchestrator',
        'activation_reason': 'test_ready_for_orchestration',
        'created_at': '2026-06-27T00:00:00Z',
        'updated_at': '2026-06-27T00:00:00Z',
        'task_root': str(task_root.relative_to(project_root)),
        'artifacts': artifacts,
    }
    _write(
        plan_root / 'tasks' / 'index.json',
        json.dumps(
            {
                'schema_version': 1,
                'record_type': 'ccb_plan_task_index',
                'plan_slug': 'demo-plan',
                'plan_root': str(plan_root),
                'updated_at': '2026-06-27T00:00:00Z',
                'tasks': [record],
            },
            ensure_ascii=False,
            indent=2,
        )
        + '\n',
    )


def _add_legacy_ready_plan_task(project_root: Path, *, task_id: str = 'task-legacy-ready') -> None:
    plan_root = project_root / 'docs' / 'plantree' / 'plans' / 'demo-plan'
    task_root = plan_root / 'tasks' / task_id
    _write(plan_root / 'README.md', '# Demo Plan\n')
    artifacts: dict[str, dict[str, object]] = {}
    for kind, filename, text in (
        ('requirements', 'requirements.md', 'requirements text\n'),
        ('acceptance', 'acceptance-criteria.md', 'acceptance text\n'),
        ('verification', 'verification-contract.md', 'verification text\n'),
        ('handoff', 'handoff.md', 'handoff text\n'),
        ('review', 'review.md', 'review text\n'),
    ):
        path = task_root / filename
        _write(path, text)
        artifacts[kind] = {
            'kind': kind,
            'artifact_kind': kind,
            'path': str(path.relative_to(project_root)),
            'artifact_path': str(path.relative_to(project_root)),
            'source_path': str(path.relative_to(project_root)),
            'sha256': 'test',
            'bytes': len(text.encode('utf-8')),
            'imported_at': '2026-06-27T00:00:00Z',
        }
    record = {
        'task_id': task_id,
        'title': 'Legacy ready task',
        'plan_slug': 'demo-plan',
        'plan_root': 'docs/plantree/plans/demo-plan',
        'status': 'ready',
        'current_loop': None,
        'owner': 'loop_runner',
        'created_at': '2026-06-27T00:00:00Z',
        'updated_at': '2026-06-27T00:00:00Z',
        'task_root': str(task_root.relative_to(project_root)),
        'artifacts': artifacts,
    }
    _write(
        plan_root / 'tasks' / 'index.json',
        json.dumps(
            {
                'schema_version': 1,
                'record_type': 'ccb_plan_task_index',
                'plan_slug': 'demo-plan',
                'plan_root': str(plan_root),
                'updated_at': '2026-06-27T00:00:00Z',
                'tasks': [record],
            },
            ensure_ascii=False,
            indent=2,
        )
        + '\n',
    )


def _add_plan_task_record(
    project_root: Path,
    *,
    task_id: str,
    status: str,
    artifacts: dict[str, dict[str, object]] | None = None,
    current_loop: str | None = None,
    next_owner: str | None = None,
    activation_reason: str | None = None,
) -> None:
    plan_root = project_root / 'docs' / 'plantree' / 'plans' / 'demo-plan'
    task_root = plan_root / 'tasks' / task_id
    _write(plan_root / 'README.md', '# Demo Plan\n')
    record = {
        'task_id': task_id,
        'title': f'{status} task',
        'plan_slug': 'demo-plan',
        'plan_root': 'docs/plantree/plans/demo-plan',
        'status': status,
        'current_loop': current_loop,
        'owner': 'planner' if status in {'draft', 'partial', 'replan_required'} else 'frontdesk',
        'created_at': '2026-06-27T00:00:00Z',
        'updated_at': '2026-06-27T00:00:00Z',
        'task_root': str(task_root.relative_to(project_root)),
        'artifacts': artifacts or {},
    }
    if next_owner is not None:
        record['next_owner'] = next_owner
    if activation_reason is not None:
        record['activation_reason'] = activation_reason
    index_path = plan_root / 'tasks' / 'index.json'
    try:
        index = json.loads(index_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        index = {
            'schema_version': 1,
            'record_type': 'ccb_plan_task_index',
            'plan_slug': 'demo-plan',
            'plan_root': str(plan_root),
            'updated_at': '2026-06-27T00:00:00Z',
            'tasks': [],
        }
    index['tasks'].append(record)
    _write(index_path, json.dumps(index, ensure_ascii=False, indent=2) + '\n')


def _import_orchestration_notes(
    context,
    project_root: Path,
    *,
    task_id: str,
    route: str,
    text: str | None = None,
) -> dict[str, object]:
    notes = project_root / 'drafts' / f'{task_id}-{route}-orchestration-notes.md'
    _write(notes, text or f'route: {route}\n')
    return plan_task(
        context,
        SimpleNamespace(
            action='task-artifact',
            task_id=task_id,
            artifact_kind='orchestration_notes',
            file_path=str(notes),
            route=route,
        ),
    )


def _run_phase2(argv: list[str], *, cwd: Path) -> tuple[int, dict[str, object], str]:
    stdout = StringIO()
    stderr = StringIO()
    result = maybe_handle_phase2(argv, cwd=cwd, stdout=stdout, stderr=stderr)
    payload = json.loads(stdout.getvalue()) if stdout.getvalue().strip() else {}
    return result, payload, stderr.getvalue()


def _workflow_dispatch_proposal() -> dict[str, object]:
    return {
        'dispatch_compatibility': 'legacy',
        'nodes': [
            {
                'id': 'plan',
                'agents': [
                    {'id': 'wf-ccb-orchestrator', 'profile': 'ccb_orchestrator', 'desired_state': 'present'},
                    {'id': 'wf-ccb-round-reviewer', 'profile': 'ccb_round_reviewer', 'desired_state': 'present'},
                ],
            },
            {
                'id': 'work-1',
                'agents': [
                    {'id': 'wf-coder-1', 'profile': 'coder', 'desired_state': 'present'},
                    {'id': 'wf-code-reviewer-1', 'profile': 'code_reviewer', 'desired_state': 'present'},
                ],
            },
        ],
        'edges': [
            {
                'id': 'coder-ask',
                'from': 'wf-ccb-orchestrator',
                'to': 'wf-coder-1',
                'type': 'ask',
                'order': 10,
                'output_artifact': 'coder.md',
            },
            {
                'id': 'reviewer-ask',
                'from': 'wf-coder-1',
                'to': 'wf-code-reviewer-1',
                'type': 'ask_after',
                'after': ['coder-ask'],
                'order': 20,
                'input_artifact': 'coder.md',
                'output_artifact': 'review.md',
            },
            {
                'id': 'round-review',
                'from': 'wf-code-reviewer-1',
                'to': 'wf-ccb-round-reviewer',
                'type': 'ask_after',
                'after': ['reviewer-ask'],
                'order': 30,
                'input_artifact': 'review.md',
                'output_artifact': 'round.md',
            },
        ],
        'artifacts': {'round': 'round.md'},
    }


def _manual_dispatch_desired(*, loop_id: str, edges: list[dict[str, object]], revision: int = 1) -> dict[str, object]:
    return {
        'schema': 'ccb.loop.agent_topology.v1',
        'record_type': 'ccb_loop_agent_topology_desired',
        'topology_status': 'committed',
        'loop_id': loop_id,
        'revision': revision,
        'nodes': [],
        'edges': edges,
        'artifacts': {},
    }


def _manual_dispatch_observed(
    *,
    loop_id: str,
    desired_revision: int = 1,
    coder_state: str = 'present',
) -> dict[str, object]:
    coder_lifecycle = {'present': 'visible', 'hidden': 'hidden'}.get(coder_state, 'parked')
    agents = [
        ('wf-ccb-orchestrator', 'ccb_orchestrator', 'present', 'visible'),
        ('wf-coder-1', 'coder', coder_state, coder_lifecycle),
        ('wf-code-reviewer-1', 'code_reviewer', 'present', 'visible'),
        ('wf-ccb-round-reviewer', 'ccb_round_reviewer', 'present', 'visible'),
    ]
    return {
        'schema': 'ccb.loop.agent_topology.observed.v1',
        'record_type': 'ccb_loop_agent_topology_observed',
        'last_reconcile_status': 'reconciled',
        'loop_id': loop_id,
        'desired_revision': desired_revision,
        'agents': [
            {
                'id': agent_id,
                'profile': profile,
                'desired_state': 'present',
                'observed_state': observed_state,
                'lifecycle_state': lifecycle_state,
                'ask_target': agent_id,
            }
            for agent_id, profile, observed_state, lifecycle_state in agents
        ],
        'edges': [],
        'drift': {'mismatched_agents': [], 'agent_count': len(agents)},
    }


def _namespace(project_id: str):
    return SimpleNamespace(
        project_id=project_id,
        namespace_epoch=1,
        tmux_socket_path='/tmp/ccb-test-tmux.sock',
        tmux_session_name='ccb-test-session',
        workspace_window_name='main',
        workspace_window_id='@main',
        workspace_epoch=1,
        ui_attachable=True,
    )


def test_loop_capacity_parser_supports_scriptable_json_commands() -> None:
    parser = CliParser()

    assert parser.parse(
        [
            'loop',
            'capacity',
            'ensure',
            '--loop-id',
            'round1',
            '--profile',
            'worker=2',
            '--profile',
            'code_reviewer',
            '--json',
        ]
    ) == ParsedLoopCapacityCommand(
        project=None,
        action='ensure',
        loop_id='round1',
        profile_counts=(('worker', 2), ('code_reviewer', 1)),
        json_output=True,
    )
    assert parser.parse(
        ['loop', 'capacity', 'status', '--loop-id', 'round1', '--json']
    ) == ParsedLoopCapacityCommand(project=None, action='status', loop_id='round1', json_output=True)
    assert parser.parse(
        ['loop', 'capacity', 'release', '--loop-id', 'round1', '--idle-only', '--json']
    ) == ParsedLoopCapacityCommand(project=None, action='release', loop_id='round1', idle_only=True, json_output=True)
    assert parser.parse(
        ['loop', 'capacity', 'release', '--loop-id', 'round1', '--policy', 'auto', '--json']
    ) == ParsedLoopCapacityCommand(project=None, action='release', loop_id='round1', policy='auto', json_output=True)
    assert parser.parse(
        [
            'loop',
            'run-once',
            '--loop-id',
            'round1',
            '--task',
            'ship the slice',
            '--worker-profile',
            'worker',
            '--reviewer-profile',
            'code_reviewer',
            '--orchestrator',
            'orchestrator',
            '--round-checker',
            'round_checker',
            '--timeout',
            '5',
            '--json',
        ]
    ) == ParsedLoopRunOnceCommand(
        project=None,
        loop_id='round1',
        task='ship the slice',
        worker_profile='worker',
        reviewer_profile='code_reviewer',
        orchestrator='orchestrator',
        round_checker='round_checker',
        timeout_s=5.0,
        json_output=True,
    )
    assert parser.parse(
        ['loop', 'run-once', '--task-id', 'task-001', '--json']
    ) == ParsedLoopRunOnceCommand(project=None, task_id='task-001', json_output=True)
    assert parser.parse(
        ['loop', 'runner', '--once', '--timeout', '5', '--json']
    ) == ParsedLoopRunnerCommand(project=None, once=True, timeout_s=5.0, json_output=True)
    assert parser.parse(
        ['loop', 'runner', '--once', '--consume-role-output', '--timeout', '5', '--json']
    ) == ParsedLoopRunnerCommand(
        project=None,
        once=True,
        timeout_s=5.0,
        consume_role_output=True,
        json_output=True,
    )


def test_loop_capacity_ensure_places_worker_and_reviewer_in_execution_node_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    _write(
        project_root / '.ccb' / 'ccb.config',
        """version = 2
entry_window = "main"

[windows]
main = "orchestrator:codex"

[loop.capacity]
enabled = true
max_nodes = 3
default_lifetime = "current_round"
name_template = "loop-{loop_id}-{profile}-{index}"
reuse = "prefer_idle"

[loop.role_profiles.worker]
role = "agentroles.coder"
provider = "codex"
thinking = "high"
workspace_mode = "git-worktree"
workspace_group = "worker_pool"
max_instances = 2
reuse = "prefer_idle"

[loop.role_profiles.code_reviewer]
role = "agentroles.code_reviewer"
provider = "codex"
thinking = "medium"
workspace_mode = "git-worktree"
workspace_group = "review_pool"
max_instances = 1
""",
    )
    current = load_project_config(project_root, include_loop_overlays=False).config

    result, payload, stderr = _run_phase2(
        [
            'loop',
            'capacity',
            'ensure',
            '--loop-id',
            'round1',
            '--profile',
            'worker=1',
            '--profile',
            'code_reviewer=1',
            '--json',
        ],
        cwd=project_root,
    )

    assert result == 0, stderr
    assert [(agent['name'], agent['node_id'], agent['placement']['window_name']) for agent in payload['agents']] == [
        ('loop-round1-worker-1', 'node1', 'node-round1-node1'),
        ('loop-round1-code_reviewer-1', 'node1', 'node-round1-node1'),
    ]
    loaded = load_project_config(project_root).config
    assert [(window.name, window.agent_names, window.layout_spec) for window in loaded.windows] == [
        ('main', ('orchestrator',), 'orchestrator:codex'),
        (
            'node-round1-node1',
            ('loop-round1-worker-1', 'loop-round1-code_reviewer-1'),
            'loop-round1-worker-1:codex(worktree); loop-round1-code_reviewer-1:codex(worktree)',
        ),
    ]
    plan = build_reload_dry_run_plan(current, loaded, project_id='proj-1', current_namespace=_namespace('proj-1'))
    assert plan['plan_class'] == 'add_window'
    assert [step['action'] for step in plan['namespace_patch_plan']['steps']] == [
        'create_window',
        'create_sidebar_pane',
        'create_agent_pane',
        'create_agent_pane',
    ]


def test_loop_capacity_ensure_places_multiple_nodes_in_separate_windows_and_release_cleans_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo-loop-capacity-explicit-multi-node'
    role_store = tmp_path / 'roles'
    _write_installed_role(role_store, 'agentroles.coder', default_agent_name='coder')
    _write_installed_role(role_store, 'agentroles.code_reviewer', default_agent_name='code_reviewer')
    monkeypatch.setenv('AGENT_ROLES_STORE', str(role_store))
    _write(
        project_root / '.ccb' / 'ccb.config',
        """version = 2
entry_window = "main"

[windows]
main = "orchestrator:codex"

[loop.capacity]
enabled = true
max_nodes = 4
default_lifetime = "current_round"
name_template = "loop-{loop_id}-{profile}-{index}"
reuse = "prefer_idle"

[loop.role_profiles.worker]
role = "agentroles.coder"
provider = "codex"
thinking = "high"
workspace_mode = "git-worktree"
workspace_group = "worker_pool"
max_instances = 2
reuse = "prefer_idle"

[loop.role_profiles.code_reviewer]
role = "agentroles.code_reviewer"
provider = "codex"
thinking = "medium"
workspace_mode = "git-worktree"
workspace_group = "review_pool"
max_instances = 2
""",
    )
    current = load_project_config(project_root, include_loop_overlays=False).config

    result, payload, stderr = _run_phase2(
        [
            'loop',
            'capacity',
            'ensure',
            '--loop-id',
            'round2',
            '--profile',
            'worker=2',
            '--profile',
            'code_reviewer=2',
            '--json',
        ],
        cwd=project_root,
    )

    assert result == 0, stderr
    assert [(agent['name'], agent['node_id'], agent['created_sequence']) for agent in payload['agents']] == [
        ('loop-round2-worker-1', 'node1', 1),
        ('loop-round2-worker-2', 'node2', 2),
        ('loop-round2-code_reviewer-1', 'node1', 3),
        ('loop-round2-code_reviewer-2', 'node2', 4),
    ]
    loaded = load_project_config(project_root).config
    assert [(window.name, window.agent_names) for window in loaded.windows] == [
        ('main', ('orchestrator',)),
        ('node-round2-node1', ('loop-round2-worker-1', 'loop-round2-code_reviewer-1')),
        ('node-round2-node2', ('loop-round2-worker-2', 'loop-round2-code_reviewer-2')),
    ]
    plan = build_reload_dry_run_plan(current, loaded, project_id='proj-1', current_namespace=_namespace('proj-1'))
    assert plan['plan_class'] == 'add_window'
    assert [step['action'] for step in plan['namespace_patch_plan']['steps']] == [
        'create_window',
        'create_sidebar_pane',
        'create_agent_pane',
        'create_agent_pane',
        'create_window',
        'create_sidebar_pane',
        'create_agent_pane',
        'create_agent_pane',
    ]

    result, status_payload, stderr = _run_phase2(['layout', 'status', '--json'], cwd=project_root)

    assert result == 0, stderr
    assert status_payload['loop_agent_count'] == 4
    windows = {window['name']: window for window in status_payload['windows']}
    assert windows['node-round2-node1']['agent_names'] == [
        'loop-round2-worker-1',
        'loop-round2-code_reviewer-1',
    ]
    assert windows['node-round2-node2']['agent_names'] == [
        'loop-round2-worker-2',
        'loop-round2-code_reviewer-2',
    ]
    assert {agent['source'] for agent in windows['node-round2-node1']['agents']} == {'loop'}
    assert {agent['source'] for agent in windows['node-round2-node2']['agents']} == {'loop'}

    result, release_payload, stderr = _run_phase2(
        ['loop', 'capacity', 'release', '--loop-id', 'round2', '--idle-only', '--json'],
        cwd=project_root,
    )

    assert result == 0, stderr
    assert release_payload['released_count'] == 4
    released = load_project_config(project_root).config
    assert [(window.name, window.agent_names) for window in released.windows] == [
        ('main', ('orchestrator',)),
    ]
    result, released_status, stderr = _run_phase2(['layout', 'status', '--json'], cwd=project_root)
    assert result == 0, stderr
    assert released_status['loop_agent_count'] == 0
    assert [(window['name'], window['agent_names']) for window in released_status['windows']] == [
        ('main', ['orchestrator']),
    ]


def test_loop_run_once_writes_round_artifacts_and_releases_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    command = ParsedLoopRunOnceCommand(project=None, loop_id='round1', task='ship the slice', timeout_s=7.0)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    calls: list[tuple[str, object]] = []

    def fake_loop_capacity(_context, capacity_command):
        calls.append(('capacity', capacity_command.action))
        if capacity_command.action == 'ensure':
            assert capacity_command.profile_counts == (('worker', 1), ('code_reviewer', 1))
            return {
                'loop_capacity_status': 'ensured',
                'loop_id': capacity_command.loop_id,
                'project_id': context.project.project_id,
                'agent_count': 2,
                'agents': [
                    {'name': 'loop-round1-worker-1', 'profile': 'worker', 'state': 'planned'},
                    {'name': 'loop-round1-code_reviewer-1', 'profile': 'code_reviewer', 'state': 'planned'},
                ],
                'apply': {'apply_status': 'applied', 'action': 'add_agent'},
            }
        if capacity_command.action == 'release':
            assert capacity_command.policy == 'auto'
            assert capacity_command.idle_only is False
            return {
                'loop_capacity_status': 'released',
                'loop_id': capacity_command.loop_id,
                'project_id': context.project.project_id,
                'agent_count': 2,
                'released_count': 2,
                'retained_count': 0,
                'release_policy': 'auto',
                'idle_only': True,
                'agents': [
                    {'name': 'loop-round1-worker-1', 'profile': 'worker', 'state': 'released'},
                    {'name': 'loop-round1-code_reviewer-1', 'profile': 'code_reviewer', 'state': 'released'},
                ],
                'apply': {'apply_status': 'applied', 'action': 'remove_agent'},
            }
        raise AssertionError(f'unexpected capacity action {capacity_command.action}')

    def fake_submit_ask(_context, ask_command):
        calls.append(('ask', ask_command.target))
        if ask_command.target == 'round_checker':
            assert ask_command.sender == 'system'
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{ask_command.target}',
            jobs=(
                {
                    'job_id': f'job_{ask_command.target}',
                    'agent_name': ask_command.target,
                    'status': 'accepted',
                },
            ),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        calls.append(('watch', job_id))
        assert timeout == 7.0
        assert emit_output is False
        target = str(job_id).removeprefix('job_')
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=f'reply from {target}',
            events=(),
        )

    payload = loop_run_once(
        context,
        command,
        services=SimpleNamespace(
            loop_capacity=fake_loop_capacity,
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
        ),
    )

    assert payload['loop_run_status'] == 'ok'
    assert payload['agents'] == {
        'worker': 'loop-round1-worker-1',
        'reviewer': 'loop-round1-code_reviewer-1',
        'orchestrator': 'orchestrator',
        'round_checker': 'round_checker',
    }
    assert calls == [
        ('capacity', 'ensure'),
        ('ask', 'loop-round1-worker-1'),
        ('watch', 'job_loop-round1-worker-1'),
        ('ask', 'loop-round1-code_reviewer-1'),
        ('watch', 'job_loop-round1-code_reviewer-1'),
        ('ask', 'orchestrator'),
        ('watch', 'job_orchestrator'),
        ('ask', 'round_checker'),
        ('watch', 'job_round_checker'),
        ('capacity', 'release'),
    ]

    loop_dir = project_root / '.ccb' / 'runtime' / 'loops' / 'round1'
    round_payload = json.loads((loop_dir / 'round.json').read_text(encoding='utf-8'))
    asks = [json.loads(line) for line in (loop_dir / 'asks.jsonl').read_text(encoding='utf-8').splitlines()]
    events = [json.loads(line) for line in (loop_dir / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert round_payload['loop_run_status'] == 'ok'
    assert round_payload['capacity']['release']['release_policy'] == 'auto'
    assert round_payload['capacity']['release']['idle_only'] is True
    assert [ask['purpose'] for ask in asks] == ['worker', 'reviewer', 'aggregate', 'round_checker']
    assert [event['kind'] for event in events] == [
        'loop_run_started',
        'ask_terminal',
        'ask_terminal',
        'ask_terminal',
        'ask_terminal',
        'loop_run_finished',
    ]
    assert (loop_dir / 'breadcrumb.md').read_text(encoding='utf-8').startswith('Loop: round1\n')
    assert (loop_dir / 'artifacts' / 'worker-reply.md').read_text(encoding='utf-8') == 'reply from loop-round1-worker-1'
    assert (loop_dir / 'artifacts' / 'reviewer-reply.md').read_text(encoding='utf-8') == 'reply from loop-round1-code_reviewer-1'
    assert (loop_dir / 'artifacts' / 'aggregate-reply.md').read_text(encoding='utf-8') == 'reply from orchestrator'
    assert (loop_dir / 'artifacts' / 'round_checker-reply.md').read_text(encoding='utf-8') == 'reply from round_checker'


def test_loop_run_once_task_id_binds_ready_task_and_reads_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    _add_ready_plan_task(project_root, task_id='task-bridge')
    command = ParsedLoopRunOnceCommand(project=None, loop_id='loop-a', task_id='task-bridge', timeout_s=7.0)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    worker_messages: list[str] = []

    def fake_loop_capacity(_context, capacity_command):
        if capacity_command.action == 'ensure':
            return {
                'loop_capacity_status': 'ensured',
                'loop_id': capacity_command.loop_id,
                'project_id': context.project.project_id,
                'agent_count': 2,
                'agents': [
                    {'name': 'loop-loop-a-worker-1', 'profile': 'worker', 'state': 'planned'},
                    {'name': 'loop-loop-a-code_reviewer-1', 'profile': 'code_reviewer', 'state': 'planned'},
                ],
                'apply': {'apply_status': 'applied', 'action': 'add_agent'},
            }
        if capacity_command.action == 'release':
            return {
                'loop_capacity_status': 'released',
                'loop_id': capacity_command.loop_id,
                'project_id': context.project.project_id,
                'agent_count': 2,
                'released_count': 2,
                'retained_count': 0,
                'release_policy': 'auto',
                'idle_only': True,
                'agents': [],
                'apply': {'apply_status': 'applied', 'action': 'remove_agent'},
            }
        raise AssertionError(f'unexpected capacity action {capacity_command.action}')

    def fake_submit_ask(_context, ask_command):
        if ask_command.target == 'loop-loop-a-worker-1':
            worker_messages.append(ask_command.message)
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{ask_command.target}',
            jobs=(
                {
                    'job_id': f'job_{ask_command.target}',
                    'agent_name': ask_command.target,
                    'status': 'accepted',
                },
            ),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        target = str(job_id).removeprefix('job_')
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply='round result: pass' if target == 'round_checker' else f'reply from {target}',
            events=(),
        )

    payload = loop_run_once(
        context,
        command,
        services=SimpleNamespace(
            loop_capacity=fake_loop_capacity,
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
        ),
    )

    assert payload['loop_run_status'] == 'ok'
    assert payload['task_id'] == 'task-bridge'
    assert worker_messages
    assert 'Task Packet:\ntask packet text' in worker_messages[0]
    assert 'Execution Contract:\nexecution contract text' in worker_messages[0]
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-bridge'))
    assert shown['task']['status'] == 'running'
    assert shown['task']['current_loop'] == 'loop-a'
    breadcrumb = (project_root / '.ccb' / 'runtime' / 'loops' / 'loop-a' / 'breadcrumb.md').read_text(encoding='utf-8')
    assert 'Task: task-bridge\n' in breadcrumb


def test_loop_runner_once_ready_for_orchestration_activates_orchestrator_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    _add_ready_plan_task(project_root, task_id='task-runner')
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    ready = plan_task(context, SimpleNamespace(action='task-show', task_id='task-runner'))
    assert ready['task']['status'] == 'ready_for_orchestration'
    assert ready['task']['next_owner'] == 'orchestrator'
    seen: dict[str, object] = {}

    def fake_submit_ask(_context, ask_command):
        seen['calls'] = int(seen.get('calls') or 0) + 1
        seen['target'] = ask_command.target
        seen['sender'] = ask_command.sender
        seen['task_id'] = ask_command.task_id
        seen['artifact_request'] = ask_command.artifact_request
        seen['message'] = ask_command.message
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=None,
            jobs=(
                {
                    'job_id': 'job_orchestrator',
                    'agent_name': 'orchestrator',
                    'status': 'completed',
                    'reply': 'route: blocked\nstatus: done\nround result: pass\n',
                },
            ),
        )

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('ready_for_orchestration must not run the fixed execution bridge')

    def forbidden_watch_ask_job(*_args, **_kwargs):
        raise AssertionError('orchestrator route/status must not be parsed from provider replies')

    def forbidden_plan_task(*_args, **_kwargs):
        raise AssertionError('orchestrator activation must not bind loops or import status artifacts')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=forbidden_watch_ask_job,
            loop_run_once=forbidden_loop_run_once,
            plan_task=forbidden_plan_task,
        ),
    )

    assert payload['loop_runner_status'] == 'ok'
    assert payload['action'] == 'activated_orchestrator'
    assert payload['task_id'] == 'task-runner'
    assert payload['task_status'] == 'ready_for_orchestration'
    assert payload['next_owner'] == 'orchestrator'
    assert payload['ask'] == {'target': 'orchestrator', 'job_id': 'job_orchestrator', 'status': 'completed'}
    assert payload['next_activation'] == 'stop_after_one_activation'
    assert seen['target'] == 'orchestrator'
    assert seen['sender'] == 'system'
    assert seen['artifact_request'] is True
    assert seen['calls'] == 1
    message = str(seen['message'])
    assert 'Allowed routes: direct_execution, needs_detail, macro_adjustment_request, blocked, partial_completion' in message
    assert (
        'route: <one of direct_execution|needs_detail|macro_adjustment_request|blocked|partial_completion>'
        in message
    )
    assert 'Reply only; do not run ccb, ccb_test, artifact import commands, or wrapper commands.' in message
    assert (
        'Supervisor/script-owned import will record orchestration_notes with the selected route after reviewing this reply.'
        in message
    )
    assert 'do not rely on provider reply text' in message
    assert 'do not start task_detailer, worker, reviewer, loop_run_once, or topology dispatch' in message
    assert 'ccb plan task-artifact' not in message
    assert 'plan task-artifact' not in message
    assert 'route_import_command' not in message
    assert 'import the stable decision' not in message
    assert 'use CCB plan commands or host-provided wrappers for authoritative writes' not in message
    activation = json.loads(Path(str(payload['activation_path'])).read_text(encoding='utf-8'))
    assert activation['record_type'] == 'ccb_loop_orchestrator_activation'
    assert activation['task_id'] == 'task-runner'
    assert 'route_import_command' not in activation
    assert (
        activation['required_next_output']
        == 'reply-only route decision and compact orchestration notes for supervisor-owned import'
    )
    assert activation['artifact_refs'] == {
        'execution_contract': 'docs/plantree/plans/demo-plan/tasks/task-runner/execution_contract.md',
        'task_packet': 'docs/plantree/plans/demo-plan/tasks/task-runner/task_packet.md',
    }
    assert activation['compact_artifacts']['task_packet']['content'] == 'task packet text'
    assert activation['compact_artifacts']['execution_contract']['content'] == 'execution contract text'
    assert activation['allowed_routes'] == [
        'direct_execution',
        'needs_detail',
        'macro_adjustment_request',
        'blocked',
        'partial_completion',
    ]
    script_write_rules = '\n'.join(str(rule) for rule in activation['script_write_rules'])
    assert 'Reply only; do not run ccb, ccb_test, artifact import commands, or wrapper commands.' in script_write_rules
    assert (
        'Supervisor/script-owned import will record orchestration_notes with the selected route after reviewing this reply.'
        in script_write_rules
    )
    assert 'ccb plan task-artifact' not in script_write_rules
    assert 'plan task-artifact' not in script_write_rules
    assert 'Import the stable route' not in script_write_rules
    assert 'authoritative writes' not in script_write_rules
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-runner'))
    assert shown['task']['status'] == 'ready_for_orchestration'
    assert shown['task']['current_loop'] is None
    assert shown['task']['next_owner'] == 'orchestrator'
    assert set(shown['task']['artifacts']) == {'task_packet', 'execution_contract'}


def test_loop_runner_once_explicit_project_from_outer_cwd_submits_orchestrator_ask(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    outer_project = tmp_path / 'outer-ccb-project'
    _write(outer_project / '.ccb' / 'ccb.config', 'cmd; outer:codex\n')
    _add_ready_plan_task(project_root, task_id='task-runner')
    command = ParsedLoopRunnerCommand(project=str(project_root), once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=outer_project, bootstrap_if_missing=False)
    captured: dict[str, object] = {}

    class _FakeClient:
        def submit(self, envelope) -> dict:
            captured['project_id'] = envelope.project_id
            captured['to_agent'] = envelope.to_agent
            captured['from_actor'] = envelope.from_actor
            captured['task_id'] = envelope.task_id
            captured['route_options'] = envelope.route_options
            captured['body'] = envelope.body
            captured['body_artifact'] = envelope.body_artifact
            return {
                'job_id': 'job_orchestrator',
                'agent_name': envelope.to_agent,
                'target_name': envelope.to_agent,
                'status': 'submitted',
            }

    monkeypatch.setattr(
        ask_service,
        'invoke_mounted_daemon',
        lambda context, allow_restart_stale, request_fn: request_fn(_FakeClient()),
    )

    payload = loop_runner_once(context, command, services=SimpleNamespace(submit_ask=ask_service.submit_ask))

    assert context.project.project_root == project_root.resolve()
    assert context.project.source == 'explicit'
    assert context.cwd == outer_project
    assert payload['loop_runner_status'] == 'ok'
    assert payload['action'] == 'activated_orchestrator'
    assert payload['ask'] == {'target': 'orchestrator', 'job_id': 'job_orchestrator', 'status': 'submitted'}
    assert captured['project_id'] == context.project.project_id
    assert captured['to_agent'] == 'orchestrator'
    assert captured['from_actor'] == 'system'
    assert captured['route_options'] == {'artifact_request': True}
    assert str(captured['task_id']).startswith('act-')
    artifact = captured['body_artifact']
    assert isinstance(artifact, dict)
    message = Path(str(artifact['path'])).read_text(encoding='utf-8')
    assert 'Required reply-only output:' in message
    assert 'ccb plan task-artifact' not in message
    assert 'plan task-artifact' not in message


def test_loop_runner_once_selects_ready_task_despite_committed_legacy_topology(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _add_ready_plan_task(project_root, task_id='task-runner')
    proposal_path = project_root / 'workflow-dispatch.json'
    _write_json(proposal_path, _workflow_dispatch_proposal())

    result, _proposed, stderr = _run_phase2(
        [
            'loop',
            'topology',
            'propose',
            '--loop-id',
            'wf1',
            '--from',
            str(proposal_path),
            '--proposal-id',
            'dispatch1',
            '--json',
        ],
        cwd=project_root,
    )
    assert result == 0, stderr
    result, committed, stderr = _run_phase2(
        ['loop', 'topology', 'commit', '--loop-id', 'wf1', '--proposal', 'dispatch1', '--apply', '--json'],
        cwd=project_root,
    )
    assert result == 0, stderr
    assert committed['reconcile']['loop_topology_status'] == 'reconciled'

    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    seen: dict[str, object] = {}

    def forbidden_topology_task(*_args, **_kwargs):
        raise AssertionError('runner mainline must not consult topology dispatch task discovery')

    def forbidden_topology_dispatch(*_args, **_kwargs):
        raise AssertionError('runner mainline must not execute topology dispatch')

    def fake_submit_ask(_context, ask_command):
        seen['target'] = ask_command.target
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=None,
            jobs=({'job_id': 'job_orchestrator_topology', 'agent_name': 'orchestrator', 'status': 'submitted'},),
        )

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('new ready_for_orchestration task must not run the fixed bridge')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            find_topology_dispatch_task=forbidden_topology_task,
            topology_dispatch=forbidden_topology_dispatch,
            loop_run_once=forbidden_loop_run_once,
            submit_ask=fake_submit_ask,
            plan_task=plan_task,
        ),
    )

    assert payload['loop_runner_status'] == 'ok'
    assert payload['action'] == 'activated_orchestrator'
    assert payload['task_id'] == 'task-runner'
    assert seen == {'target': 'orchestrator'}
    dispatch_path = project_root / '.ccb' / 'runtime' / 'loops' / 'wf1' / 'topology_dispatch.json'
    assert not dispatch_path.exists()


def test_loop_runner_once_pauses_bound_topology_loop_without_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _add_ready_plan_task(project_root, task_id='task-topology')
    proposal_path = project_root / 'workflow-dispatch.json'
    _write_json(proposal_path, _workflow_dispatch_proposal())

    result, _proposed, stderr = _run_phase2(
        [
            'loop',
            'topology',
            'propose',
            '--loop-id',
            'wf1',
            '--from',
            str(proposal_path),
            '--proposal-id',
            'dispatch1',
            '--json',
        ],
        cwd=project_root,
    )
    assert result == 0, stderr
    result, committed, stderr = _run_phase2(
        ['loop', 'topology', 'commit', '--loop-id', 'wf1', '--proposal', 'dispatch1', '--apply', '--json'],
        cwd=project_root,
    )
    assert result == 0, stderr
    assert committed['reconcile']['loop_topology_status'] == 'reconciled'

    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    plan_task(context, SimpleNamespace(action='task-bind-loop', task_id='task-topology', loop_id='wf1'))

    def forbidden_submit_ask(*_args, **_kwargs):
        raise AssertionError('bound topology loop must not submit topology dispatch asks')

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('bound topology loop must not fall back to fixed runner')

    def forbidden_topology_dispatch(*_args, **_kwargs):
        raise AssertionError('topology dispatch is legacy/disabled for loop runner mainline')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=forbidden_submit_ask,
            loop_run_once=forbidden_loop_run_once,
            topology_dispatch=forbidden_topology_dispatch,
            plan_task=plan_task,
        ),
    )

    assert payload['loop_runner_status'] == 'paused'
    assert payload['action'] == 'ask_first_execution_not_ready'
    assert 'Phase 4 ask-first execution can only start from an unbound direct_execution task' in payload['reason']
    assert payload['loop_id'] == 'wf1'
    assert payload['task_id'] == 'task-topology'
    assert payload['task_status'] == 'running'
    assert payload['next_owner'] == 'orchestrator'
    assert payload['next_activation'] == 'phase4_ask_first_runner_required'
    dispatch_path = project_root / '.ccb' / 'runtime' / 'loops' / 'wf1' / 'topology_dispatch.json'
    assert not dispatch_path.exists()
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-topology'))
    assert shown['task']['status'] == 'running'
    assert shown['task']['current_loop'] == 'wf1'
    assert 'round_summary' not in shown['task']['artifacts']


def test_loop_runner_direct_execution_route_runs_ask_first_round_without_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _write(project_root / 'lab_docs' / 'direct_execution_note.md', 'status: draft\n')
    _add_ready_plan_task(
        project_root,
        task_id='task-direct',
        execution_contract_text='execution contract text\nallowed_change_paths: lab_docs/direct_execution_note.md\n',
    )
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    submitted: list[object] = []
    targets_by_job: dict[str, str] = {}

    def fake_submit_ask(_context, ask_command):
        next_index = len(submitted) + 1
        if next_index == 2 and ask_command.callback:
            raise RuntimeError('ask --chain requires an active parent job for the sender')
        if next_index == 2 and ask_command.sender != 'system':
            raise RuntimeError('plain ask from an active CCB task requires --chain')
        submitted.append(ask_command)
        job_id = f'job_{len(submitted)}'
        targets_by_job[job_id] = ask_command.target
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': job_id, 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        target = targets_by_job[str(job_id)]
        if target.startswith('loop-') and '-coder-' in target:
            workspace = _seed_copy_workspace_binding(context, project_root, target)
            _write(workspace / 'lab_docs' / 'direct_execution_note.md', 'status: reviewed\n')
        if target == 'ccb_round_reviewer':
            reply = (
                'round result: pass\n'
                'verification performed: direct execution fake review\n'
                'hidden degradation audit: no hidden fallback, scope shrink, or fake success\n'
                'evidence refs: task_packet execution_contract\n'
            )
        else:
            reply = f'reply from {target}'
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=reply,
            events=(),
        )

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not run the legacy fixed bridge')

    def forbidden_topology_dispatch(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not execute topology dispatch')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            loop_run_once=forbidden_loop_run_once,
            topology_dispatch=forbidden_topology_dispatch,
            plan_task=plan_task,
        ),
    )

    assert payload['loop_runner_status'] == 'ok'
    assert payload['action'] == 'ran_one_round'
    assert payload['execution_mode'] == 'ask_first_direct_execution'
    assert payload['dispatch_source'] == 'ask_first_mount_topology'
    assert payload['task_id'] == 'task-direct'
    assert payload['round_result'] == 'pass'
    assert payload['round_result_source'] == 'round_reviewer_reply'
    assert payload['task_status'] == 'done'
    assert payload['next_activation'] == 'stop'
    assert payload['release']['released_count'] == 2
    assert payload['release']['retained_count'] == 0
    assert payload['topology']['status'] == 'ready'
    assert (project_root / 'lab_docs' / 'direct_execution_note.md').read_text(encoding='utf-8') == 'status: reviewed\n'
    targets = [command.target for command in submitted]
    assert len(targets) == 4
    assert targets[0].startswith(f'loop-{payload["loop_id"]}-coder-')
    assert targets[1].startswith(f'loop-{payload["loop_id"]}-code_reviewer-')
    assert targets[2] == 'orchestrator'
    assert targets[3] == 'ccb_round_reviewer'
    assert all(command.sender == 'system' for command in submitted)
    assert all(command.callback is False for command in submitted)
    assert all(command.silence is False for command in submitted)
    assert 'task_detailer' not in targets
    worker_message = submitted[0].message
    reviewer_message = submitted[1].message
    assert 'task_packet:' in worker_message
    assert 'execution_contract:' in worker_message
    assert 'Task Packet:\ntask packet text' in worker_message
    assert 'Execution Contract:\nexecution contract text' in worker_message
    assert 'explicitly check execution_contract' in reviewer_message
    assert 'reject hidden fallback, scope shrink, and fake success' in reviewer_message
    normalized_proposal = json.loads(Path(str(payload['topology']['proposal_path'])).read_text(encoding='utf-8'))
    desired = json.loads(Path(str(payload['topology']['desired_path'])).read_text(encoding='utf-8'))
    observed = json.loads(Path(str(payload['topology']['observed_path'])).read_text(encoding='utf-8'))
    assert [agent['profile'] for agent in normalized_proposal['agents']] == ['coder', 'code_reviewer']
    for persisted in (normalized_proposal, desired, observed):
        assert 'edges' not in persisted
        assert 'artifacts' not in persisted
        assert 'gates' not in persisted
    assert not (project_root / '.ccb' / 'runtime' / 'loops' / payload['loop_id'] / 'topology_dispatch.json').exists()
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['agents']['ccb_round_reviewer'] == 'ccb_round_reviewer'
    assert round_json['ccb_round_reviewer']['target'] == 'ccb_round_reviewer'
    assert 'round_checker' not in round_json['agents']
    assert 'round_checker' not in round_json
    assert round_json['legacy_aliases']['round_checker']['field'] == 'ccb_round_reviewer'
    assert round_json['topology']['release']['released_count'] == 2
    assert round_json['authority_update']['source'] == 'isolated_workspace_changes_promoted'
    assert round_json['authority_update']['changed_files'] == ['lab_docs/direct_execution_note.md']
    assert round_json['authority_update']['allowed_change_paths'] == ['lab_docs/direct_execution_note.md']
    assert round_json['authority_update']['verified_project_root'] is True
    assert round_json['authority_import']['status'] == 'done'
    assert (Path(str(round_json['paths']['artifacts'])) / 'ccb_round_reviewer-reply.md').is_file()
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-direct'))
    assert shown['task']['status'] == 'done'
    assert shown['task']['current_loop'] is None
    assert shown['task']['artifacts']['orchestration_notes']['orchestrator_route'] == 'direct_execution'
    assert shown['task']['artifacts']['round_summary']['round_result'] == 'pass'
    assert shown['task']['artifacts']['round_summary']['actor'] == {
        'source': 'loop_runner',
        'actor': 'loop_runner',
        'job_id': 'job_1',
    }


def test_loop_runner_direct_execution_promotes_isolated_workspace_changes_before_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _write(project_root / 'lab_docs' / 'l1_release_note.md', 'status: draft\n')
    _add_ready_plan_task(
        project_root,
        task_id='task-direct',
        execution_contract_text='execution contract text\nallowed_change_paths: lab_docs/l1_release_note.md\n',
    )
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    submitted: list[object] = []
    targets_by_job: dict[str, str] = {}

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        job_id = f'job_{len(submitted)}'
        targets_by_job[job_id] = ask_command.target
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': job_id, 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        target = targets_by_job[str(job_id)]
        if target.startswith('loop-') and '-coder-' in target:
            workspace = _seed_copy_workspace_binding(context, project_root, target)
            _write(workspace / 'lab_docs' / 'l1_release_note.md', 'status: reviewed\n')
        if target == 'ccb_round_reviewer':
            reply = (
                'round result: pass\n'
                'verification performed: direct execution fake review\n'
                'hidden degradation audit: no hidden fallback, scope shrink, or fake success\n'
                'evidence refs: task_packet execution_contract\n'
            )
        else:
            reply = f'reply from {target}'
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=reply,
            events=(),
        )

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not run the legacy fixed bridge')

    def forbidden_topology_dispatch(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not execute topology dispatch')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            loop_run_once=forbidden_loop_run_once,
            topology_dispatch=forbidden_topology_dispatch,
            plan_task=plan_task,
        ),
    )

    assert payload['loop_runner_status'] == 'ok'
    assert payload['round_result'] == 'pass'
    assert payload['round_result_source'] == 'round_reviewer_reply'
    assert payload['task_status'] == 'done'
    assert payload['release']['released_count'] == 2
    assert (project_root / 'lab_docs' / 'l1_release_note.md').read_text(encoding='utf-8') == 'status: reviewed\n'
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['round_result'] == 'pass'
    assert round_json['authority_update']['source'] == 'isolated_workspace_changes_promoted'
    assert round_json['authority_update']['changed_files'] == ['lab_docs/l1_release_note.md']
    assert round_json['authority_update']['verified_project_root'] is True
    assert round_json['authority_import']['status'] == 'done'
    summary_text = Path(str(round_json['paths']['round'])).read_text(encoding='utf-8')
    assert 'round result: pass' in summary_text
    assert '## Authority Update' in summary_text
    assert '- changed_files: lab_docs/l1_release_note.md' in summary_text
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-direct'))
    assert shown['task']['status'] == 'done'
    assert shown['task']['artifacts']['round_summary']['round_result'] == 'pass'


def test_loop_runner_direct_execution_promotes_before_project_root_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _write(project_root / 'lab_docs' / 'l1_release_note.md', 'status: draft\n')
    _add_ready_plan_task(
        project_root,
        task_id='task-direct',
        execution_contract_text='execution contract text\nallowed_change_paths: lab_docs/l1_release_note.md\n',
    )
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    submitted: list[object] = []
    targets_by_job: dict[str, str] = {}
    reviewer_project_root_seen: list[str] = []
    round_reviewer_project_root_seen: list[str] = []

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        job_id = f'job_{len(submitted)}'
        targets_by_job[job_id] = ask_command.target
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': job_id, 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        target = targets_by_job[str(job_id)]
        if target.startswith('loop-') and '-coder-' in target:
            workspace = _seed_copy_workspace_binding(context, project_root, target)
            _write(workspace / 'lab_docs' / 'l1_release_note.md', 'status: reviewed\n')
            reply = 'status: done\nchanged_files: lab_docs/l1_release_note.md\n'
        elif target.startswith('loop-') and '-code_reviewer-' in target:
            content = (project_root / 'lab_docs' / 'l1_release_note.md').read_text(encoding='utf-8')
            reviewer_project_root_seen.append(content)
            reply = (
                'status: pass\n'
                f'project-root evidence: lab_docs/l1_release_note.md -> {content.strip()}\n'
            )
        elif target == 'ccb_round_reviewer':
            content = (project_root / 'lab_docs' / 'l1_release_note.md').read_text(encoding='utf-8')
            round_reviewer_project_root_seen.append(content)
            if content == 'status: reviewed\n':
                reply = (
                    'round result: pass\n'
                    'verification performed: project root contains reviewed release note\n'
                    'hidden degradation audit: no hidden fallback, scope shrink, or fake success\n'
                )
            else:
                reply = (
                    'round result: rework_node\n'
                    'fake success evidence: worker workspace changed but project root is still draft\n'
                )
        else:
            reply = f'reply from {target}'
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=reply,
            events=(),
        )

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not run the legacy fixed bridge')

    def forbidden_topology_dispatch(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not execute topology dispatch')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            loop_run_once=forbidden_loop_run_once,
            topology_dispatch=forbidden_topology_dispatch,
            plan_task=plan_task,
        ),
    )

    assert payload['loop_runner_status'] == 'ok'
    assert payload['round_result'] == 'pass'
    assert payload['round_result_source'] == 'round_reviewer_reply'
    assert payload['task_status'] == 'done'
    assert reviewer_project_root_seen == ['status: reviewed\n']
    assert round_reviewer_project_root_seen == ['status: reviewed\n']
    assert (project_root / 'lab_docs' / 'l1_release_note.md').read_text(encoding='utf-8') == 'status: reviewed\n'
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['authority_update']['source'] == 'isolated_workspace_changes_promoted'
    assert round_json['authority_update']['changed_files'] == ['lab_docs/l1_release_note.md']
    assert round_json['authority_update']['verified_project_root'] is True


def test_loop_runner_direct_execution_blocks_when_workspace_promotion_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _write(project_root / 'lab_docs' / 'l1_release_note.md', 'status: draft\n')
    _add_ready_plan_task(
        project_root,
        task_id='task-direct',
        execution_contract_text='execution contract text\nallowed_change_paths: lab_docs/l1_release_note.md\n',
    )
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    submitted: list[object] = []
    targets_by_job: dict[str, str] = {}

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        job_id = f'job_{len(submitted)}'
        targets_by_job[job_id] = ask_command.target
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': job_id, 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        target = targets_by_job[str(job_id)]
        if target.startswith('loop-') and '-coder-' in target:
            workspace = _seed_copy_workspace_binding(context, project_root, target)
            _write(workspace / 'lab_docs' / 'l1_release_note.md', 'status: reviewed\n')
        if target == 'ccb_round_reviewer':
            reply = (
                'round result: pass\n'
                'verification performed: direct execution fake review\n'
                'hidden degradation audit: no hidden fallback, scope shrink, or fake success\n'
                'evidence refs: task_packet execution_contract\n'
            )
        else:
            reply = f'reply from {target}'
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=reply,
            events=(),
        )

    def blocked_copy(*_args, **_kwargs):
        raise RuntimeError('promotion blocked by test')

    monkeypatch.setattr(loop_ask_first_module, '_copy_workspace_files', blocked_copy)

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not run the legacy fixed bridge')

    def forbidden_topology_dispatch(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not execute topology dispatch')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            loop_run_once=forbidden_loop_run_once,
            topology_dispatch=forbidden_topology_dispatch,
            plan_task=plan_task,
        ),
    )

    assert payload['loop_runner_status'] == 'ok'
    assert payload['round_result'] == 'blocked'
    assert payload['round_result_source'] == 'isolated_workspace_promotion_failed'
    assert payload['task_status'] == 'blocked'
    assert payload['release']['released_count'] == 2
    assert (project_root / 'lab_docs' / 'l1_release_note.md').read_text(encoding='utf-8') == 'status: draft\n'
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['round_result'] == 'blocked'
    assert round_json['failure']['source'] == 'isolated_workspace_promotion_failed'
    assert round_json['failure']['changed_files'] == ['lab_docs/l1_release_note.md']
    assert round_json['authority_import']['status'] == 'blocked'
    summary_text = Path(str(round_json['paths']['round'])).read_text(encoding='utf-8')
    assert 'round result: blocked' in summary_text
    assert '- changed_files: lab_docs/l1_release_note.md' in summary_text
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-direct'))
    assert shown['task']['status'] == 'blocked'


def test_loop_runner_direct_execution_blocks_isolated_workspace_pass_without_project_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _write(project_root / 'lab_docs' / 'l1_release_note.md', 'status: draft\n')
    _add_ready_plan_task(project_root, task_id='task-direct')
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    submitted: list[object] = []
    targets_by_job: dict[str, str] = {}

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        job_id = f'job_{len(submitted)}'
        targets_by_job[job_id] = ask_command.target
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': job_id, 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        target = targets_by_job[str(job_id)]
        if target.startswith('loop-') and '-coder-' in target:
            _seed_copy_workspace_binding(context, project_root, target)
        if target == 'ccb_round_reviewer':
            reply = (
                'round result: pass\n'
                'verification performed: direct execution fake review\n'
                'hidden degradation audit: no hidden fallback, scope shrink, or fake success\n'
                'evidence refs: task_packet execution_contract\n'
            )
        else:
            reply = f'reply from {target}'
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=reply,
            events=(),
        )

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not run the legacy fixed bridge')

    def forbidden_topology_dispatch(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not execute topology dispatch')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            loop_run_once=forbidden_loop_run_once,
            topology_dispatch=forbidden_topology_dispatch,
            plan_task=plan_task,
        ),
    )

    assert payload['round_result'] == 'blocked'
    assert payload['round_result_source'] == 'isolated_workspace_no_project_root_effect'
    assert payload['task_status'] == 'blocked'
    assert (project_root / 'lab_docs' / 'l1_release_note.md').read_text(encoding='utf-8') == 'status: draft\n'
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['failure']['source'] == 'isolated_workspace_no_project_root_effect'
    assert round_json['failure']['changed_files'] == []
    assert 'authority_update' not in round_json
    assert round_json['authority_import']['status'] == 'blocked'
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-direct'))
    assert shown['task']['status'] == 'blocked'


def test_loop_runner_direct_execution_blocks_when_change_scope_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _write(project_root / 'lab_docs' / 'l1_release_note.md', 'status: draft\n')
    _add_ready_plan_task(project_root, task_id='task-direct')
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    submitted: list[object] = []
    targets_by_job: dict[str, str] = {}

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        job_id = f'job_{len(submitted)}'
        targets_by_job[job_id] = ask_command.target
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': job_id, 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        target = targets_by_job[str(job_id)]
        if target.startswith('loop-') and '-coder-' in target:
            workspace = _seed_copy_workspace_binding(context, project_root, target)
            _write(workspace / 'lab_docs' / 'l1_release_note.md', 'status: reviewed\n')
        if target == 'ccb_round_reviewer':
            reply = 'round result: pass\nverification performed: direct execution fake review\n'
        else:
            reply = f'reply from {target}'
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=reply,
            events=(),
        )

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            loop_run_once=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('direct_execution route must not run the legacy fixed bridge')
            ),
            topology_dispatch=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('direct_execution route must not execute topology dispatch')
            ),
            plan_task=plan_task,
        ),
    )

    assert payload['round_result'] == 'blocked'
    assert payload['round_result_source'] == 'isolated_workspace_change_scope_missing'
    assert payload['task_status'] == 'blocked'
    assert (project_root / 'lab_docs' / 'l1_release_note.md').read_text(encoding='utf-8') == 'status: draft\n'
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['failure']['source'] == 'isolated_workspace_change_scope_missing'
    assert round_json['failure']['changed_files'] == ['lab_docs/l1_release_note.md']
    assert round_json['failure']['allowed_change_paths'] == []
    assert 'authority_update' not in round_json


def test_loop_runner_direct_execution_blocks_out_of_scope_workspace_delta(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _write(project_root / 'lab_docs' / 'l1_release_note.md', 'status: draft\n')
    _write(project_root / 'lab_docs' / 'unrelated.md', 'status: draft\n')
    _add_ready_plan_task(
        project_root,
        task_id='task-direct',
        execution_contract_text='execution contract text\nallowed_change_paths: lab_docs/l1_release_note.md\n',
    )
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    submitted: list[object] = []
    targets_by_job: dict[str, str] = {}

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        job_id = f'job_{len(submitted)}'
        targets_by_job[job_id] = ask_command.target
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': job_id, 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        target = targets_by_job[str(job_id)]
        if target.startswith('loop-') and '-coder-' in target:
            workspace = _seed_copy_workspace_binding(context, project_root, target)
            _write(workspace / 'lab_docs' / 'l1_release_note.md', 'status: reviewed\n')
            _write(workspace / 'lab_docs' / 'unrelated.md', 'status: reviewed\n')
        if target == 'ccb_round_reviewer':
            reply = 'round result: pass\nverification performed: direct execution fake review\n'
        else:
            reply = f'reply from {target}'
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=reply,
            events=(),
        )

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            loop_run_once=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('direct_execution route must not run the legacy fixed bridge')
            ),
            topology_dispatch=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('direct_execution route must not execute topology dispatch')
            ),
            plan_task=plan_task,
        ),
    )

    assert payload['round_result'] == 'blocked'
    assert payload['round_result_source'] == 'isolated_workspace_change_scope_violation'
    assert payload['task_status'] == 'blocked'
    assert (project_root / 'lab_docs' / 'l1_release_note.md').read_text(encoding='utf-8') == 'status: draft\n'
    assert (project_root / 'lab_docs' / 'unrelated.md').read_text(encoding='utf-8') == 'status: draft\n'
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['failure']['source'] == 'isolated_workspace_change_scope_violation'
    assert round_json['failure']['allowed_change_paths'] == ['lab_docs/l1_release_note.md']
    assert round_json['failure']['out_of_scope_files'] == ['lab_docs/unrelated.md']
    assert 'authority_update' not in round_json


def test_loop_runner_direct_execution_blocks_delete_or_rename_workspace_delta(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _write(project_root / 'lab_docs' / 'l1_release_note.md', 'status: draft\n')
    _add_ready_plan_task(project_root, task_id='task-direct')
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    submitted: list[object] = []
    targets_by_job: dict[str, str] = {}

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        job_id = f'job_{len(submitted)}'
        targets_by_job[job_id] = ask_command.target
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': job_id, 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        target = targets_by_job[str(job_id)]
        if target.startswith('loop-') and '-coder-' in target:
            workspace = _seed_copy_workspace_binding(context, project_root, target)
            (workspace / 'lab_docs' / 'l1_release_note.md').unlink()
        if target == 'ccb_round_reviewer':
            reply = (
                'round result: pass\n'
                'verification performed: direct execution fake review\n'
                'hidden degradation audit: no hidden fallback, scope shrink, or fake success\n'
                'evidence refs: task_packet execution_contract\n'
            )
        else:
            reply = f'reply from {target}'
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=reply,
            events=(),
        )

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not run the legacy fixed bridge')

    def forbidden_topology_dispatch(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not execute topology dispatch')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            loop_run_once=forbidden_loop_run_once,
            topology_dispatch=forbidden_topology_dispatch,
            plan_task=plan_task,
        ),
    )

    assert payload['round_result'] == 'blocked'
    assert payload['round_result_source'] == 'isolated_workspace_deletions_unsupported'
    assert payload['task_status'] == 'blocked'
    assert (project_root / 'lab_docs' / 'l1_release_note.md').is_file()
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['failure']['source'] == 'isolated_workspace_deletions_unsupported'
    assert round_json['failure']['deleted_files'] == ['lab_docs/l1_release_note.md']
    assert 'authority_update' not in round_json
    summary_text = Path(str(round_json['paths']['round'])).read_text(encoding='utf-8')
    assert '- deleted_files: lab_docs/l1_release_note.md' in summary_text
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-direct'))
    assert shown['task']['status'] == 'blocked'


def test_loop_runner_direct_execution_blocks_when_copy_workspace_binding_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _add_ready_plan_task(project_root, task_id='task-direct')
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    submitted: list[object] = []
    targets_by_job: dict[str, str] = {}

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        job_id = f'job_{len(submitted)}'
        targets_by_job[job_id] = ask_command.target
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': job_id, 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        target = targets_by_job[str(job_id)]
        if target == 'ccb_round_reviewer':
            reply = (
                'round result: pass\n'
                'verification performed: direct execution fake review\n'
                'hidden degradation audit: no hidden fallback, scope shrink, or fake success\n'
                'evidence refs: task_packet execution_contract\n'
            )
        else:
            reply = f'reply from {target}'
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=reply,
            events=(),
        )

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            loop_run_once=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('direct_execution route must not run the legacy fixed bridge')
            ),
            topology_dispatch=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('direct_execution route must not execute topology dispatch')
            ),
            plan_task=plan_task,
        ),
    )

    assert payload['round_result'] == 'blocked'
    assert payload['round_result_source'] == 'workspace_binding_missing'
    assert payload['task_status'] == 'blocked'
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['failure']['source'] == 'workspace_binding_missing'
    assert round_json['failure']['workspace_mode_configured'] == 'git-worktree'
    assert round_json['authority_import']['status'] == 'blocked'
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-direct'))
    assert shown['task']['status'] == 'blocked'


def test_loop_runner_direct_execution_blocks_when_workspace_binding_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _add_ready_plan_task(project_root, task_id='task-direct')
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    submitted: list[object] = []
    targets_by_job: dict[str, str] = {}

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        job_id = f'job_{len(submitted)}'
        targets_by_job[job_id] = ask_command.target
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': job_id, 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        target = targets_by_job[str(job_id)]
        if target.startswith('loop-') and '-coder-' in target:
            _write(project_root / '.ccb' / 'workspaces' / target / '.ccb-workspace.json', '{not-json')
        if target == 'ccb_round_reviewer':
            reply = (
                'round result: pass\n'
                'verification performed: direct execution fake review\n'
                'hidden degradation audit: no hidden fallback, scope shrink, or fake success\n'
                'evidence refs: task_packet execution_contract\n'
            )
        else:
            reply = f'reply from {target}'
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=reply,
            events=(),
        )

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            loop_run_once=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('direct_execution route must not run the legacy fixed bridge')
            ),
            topology_dispatch=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('direct_execution route must not execute topology dispatch')
            ),
            plan_task=plan_task,
        ),
    )

    assert payload['round_result'] == 'blocked'
    assert payload['round_result_source'] == 'workspace_binding_invalid'
    assert payload['task_status'] == 'blocked'
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['failure']['source'] == 'workspace_binding_invalid'
    assert round_json['authority_import']['status'] == 'blocked'


def test_loop_runner_direct_execution_blocks_when_project_root_test_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _write(project_root / 'lab_code' / 'calculator.py', 'def add(a, b):\n    return a - b\n')
    _write(
        project_root / 'tests' / 'test_calculator.py',
        'import unittest\n\n'
        'from lab_code.calculator import add\n\n'
        'class CalculatorTest(unittest.TestCase):\n'
        '    def test_add(self):\n'
        '        self.assertEqual(add(2, 3), 5)\n',
    )
    _add_ready_plan_task(
        project_root,
        task_id='task-direct',
        execution_contract_text=(
            'execution contract text\n'
            'allowed_change_paths: lab_code/calculator.py\n'
            'test_command: python -m unittest discover -s tests -p test_calculator.py\n'
        ),
    )
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    submitted: list[object] = []
    targets_by_job: dict[str, str] = {}

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        job_id = f'job_{len(submitted)}'
        targets_by_job[job_id] = ask_command.target
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': job_id, 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        target = targets_by_job[str(job_id)]
        if target.startswith('loop-') and '-coder-' in target:
            workspace = _seed_copy_workspace_binding(context, project_root, target)
            _write(workspace / 'lab_code' / 'calculator.py', 'def add(a, b):\n    return a * b\n')
        if target == 'ccb_round_reviewer':
            reply = (
                'round result: pass\n'
                'verification performed: direct execution fake review\n'
                'hidden degradation audit: no hidden fallback, scope shrink, or fake success\n'
                'evidence refs: task_packet execution_contract\n'
            )
        else:
            reply = f'reply from {target}'
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=reply,
            events=(),
        )

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            loop_run_once=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('direct_execution route must not run the legacy fixed bridge')
            ),
            topology_dispatch=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('direct_execution route must not execute topology dispatch')
            ),
            plan_task=plan_task,
        ),
    )

    assert payload['round_result'] == 'blocked'
    assert payload['round_result_source'] == 'project_root_test_failed'
    assert payload['task_status'] == 'blocked'
    assert (project_root / 'lab_code' / 'calculator.py').read_text(encoding='utf-8') == 'def add(a, b):\n    return a - b\n'
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['failure']['source'] == 'project_root_test_failed'
    assert round_json['failure']['test_result'] == 'fail'
    assert round_json['failure']['test_command'] == 'python -m unittest discover -s tests -p test_calculator.py'
    assert round_json['failure']['test_cwd'] == str(project_root)
    assert round_json['failure']['test_file_resolved_to_lab'] is True
    assert round_json['failure']['test_sys_path_project_first'] is True
    assert round_json['project_root_test']['test_result'] == 'fail'
    assert Path(str(round_json['project_root_test']['test_resolution_path'])).is_file()
    assert round_json['authority_import']['status'] == 'blocked'
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-direct'))
    assert shown['task']['status'] == 'blocked'


def test_loop_runner_direct_execution_pass_requires_verified_project_root_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _write(project_root / 'lab_code' / 'calculator.py', 'def add(a, b):\n    return a - b\n')
    _write(
        project_root / 'tests' / 'test_calculator.py',
        'import unittest\n\n'
        'from lab_code.calculator import add\n\n'
        'class CalculatorTest(unittest.TestCase):\n'
        '    def test_add(self):\n'
        '        self.assertEqual(add(2, 3), 5)\n',
    )
    _add_ready_plan_task(
        project_root,
        task_id='task-direct',
        execution_contract_text=(
            'execution contract text\n'
            'allowed_change_paths: lab_code/calculator.py\n'
            'test_command: python -m unittest discover -s tests -p test_calculator.py\n'
        ),
    )
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    submitted: list[object] = []
    targets_by_job: dict[str, str] = {}

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        job_id = f'job_{len(submitted)}'
        targets_by_job[job_id] = ask_command.target
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': job_id, 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        target = targets_by_job[str(job_id)]
        if target.startswith('loop-') and '-coder-' in target:
            workspace = _seed_copy_workspace_binding(context, project_root, target)
            _write(workspace / 'lab_code' / 'calculator.py', 'def add(a, b):\n    return a + b\n')
        if target == 'ccb_round_reviewer':
            reply = (
                'round result: pass\n'
                'verification performed: direct execution fake review\n'
                'hidden degradation audit: no hidden fallback, scope shrink, or fake success\n'
                'evidence refs: task_packet execution_contract\n'
            )
        else:
            reply = f'reply from {target}'
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=reply,
            events=(),
        )

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            loop_run_once=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('direct_execution route must not run the legacy fixed bridge')
            ),
            topology_dispatch=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('direct_execution route must not execute topology dispatch')
            ),
            plan_task=plan_task,
        ),
    )

    assert payload['round_result'] == 'pass'
    assert payload['round_result_source'] == 'round_reviewer_reply'
    assert payload['task_status'] == 'done'
    assert (project_root / 'lab_code' / 'calculator.py').read_text(encoding='utf-8') == 'def add(a, b):\n    return a + b\n'
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['authority_update']['source'] == 'isolated_workspace_changes_promoted'
    assert round_json['authority_update']['verified_project_root'] is True
    assert round_json['authority_update']['changed_files'] == ['lab_code/calculator.py']
    assert round_json['project_root_test']['test_result'] == 'pass'
    assert round_json['project_root_test']['test_command'] == 'python -m unittest discover -s tests -p test_calculator.py'
    assert round_json['project_root_test']['test_cwd'] == str(project_root)
    assert round_json['project_root_test']['test_file_resolved_to_lab'] is True
    assert round_json['project_root_test']['test_sys_path_project_first'] is True
    assert Path(str(round_json['project_root_test']['test_resolution_path'])).is_file()
    assert round_json['authority_import']['status'] == 'done'
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-direct'))
    assert shown['task']['status'] == 'done'


def test_loop_runner_partial_completion_route_imports_partial_without_done(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _add_ready_plan_task(project_root, task_id='task-partial')
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-partial', route='partial_completion')
    submitted: list[object] = []
    targets_by_job: dict[str, str] = {}

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        job_id = f'job_{len(submitted)}'
        targets_by_job[job_id] = ask_command.target
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': job_id, 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        target = targets_by_job[str(job_id)]
        reply = 'round result: partial\nunfinished step evidence: step-2 open\n' if target == 'ccb_round_reviewer' else f'reply from {target}'
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=reply,
            events=(),
        )

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            plan_task=plan_task,
        ),
    )

    assert payload['loop_runner_status'] == 'ok'
    assert payload['action'] == 'ran_one_round'
    assert payload['round_result'] == 'partial'
    assert payload['task_status'] == 'partial'
    assert payload['next_activation'] == 'planner'
    assert len(submitted) == 4
    assert all(command.sender == 'system' for command in submitted)
    assert all(command.callback is False for command in submitted)
    assert all(command.silence is False for command in submitted)
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-partial'))
    assert shown['task']['status'] == 'partial'
    assert shown['task']['next_owner'] == 'planner'
    assert shown['task']['current_loop'] is None
    assert shown['task']['artifacts']['orchestration_notes']['orchestrator_route'] == 'partial_completion'
    assert shown['task']['artifacts']['round_summary']['round_result'] == 'partial'


@pytest.mark.parametrize(
    ('round_reviewer_reply', 'expected_result', 'expected_status', 'reviewer_recheck_status'),
    (
        ('round result: pass\n', 'pass', 'done', 'pass'),
        ('round result: replan_required\n', 'replan_required', 'replan_required', 'rework_required'),
    ),
)
def test_loop_runner_direct_execution_uses_one_bounded_rework_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    round_reviewer_reply: str,
    expected_result: str,
    expected_status: str,
    reviewer_recheck_status: str,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _write(project_root / 'lab_docs' / 'bounded_rework_note.md', 'status: draft\n')
    _add_ready_plan_task(
        project_root,
        task_id='task-rework',
        execution_contract_text='execution contract text\nallowed_change_paths: lab_docs/bounded_rework_note.md\n',
    )
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-rework', route='direct_execution')
    submitted: list[object] = []
    commands_by_job: dict[str, object] = {}

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        job_id = f'job_{len(submitted)}'
        commands_by_job[job_id] = ask_command
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': job_id, 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        ask_command = commands_by_job[str(job_id)]
        target = ask_command.target
        if target.startswith('loop-') and '-coder-' in target:
            workspace = _seed_copy_workspace_binding(context, project_root, target)
            _write(workspace / 'lab_docs' / 'bounded_rework_note.md', 'status: reviewed\n')
        if target == 'ccb_round_reviewer':
            reply = round_reviewer_reply
        elif str(ask_command.task_id).endswith('-reviewer'):
            reply = 'status: rework_required\nexecution_contract audit: fail\n'
        elif str(ask_command.task_id).endswith('-reviewer-recheck'):
            reply = f'status: {reviewer_recheck_status}\nexecution_contract audit: fail\n'
        else:
            reply = f'status: done\nreply from {target}'
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=reply,
            events=(),
        )

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            plan_task=plan_task,
        ),
    )

    assert payload['loop_runner_status'] == 'ok'
    assert payload['round_result'] == expected_result
    assert payload['task_status'] == expected_status
    assert payload['release']['released_count'] == 2
    assert [command.task_id for command in submitted] == [
        f'{payload["loop_id"]}-worker',
        f'{payload["loop_id"]}-reviewer',
        f'{payload["loop_id"]}-worker-rework',
        f'{payload["loop_id"]}-reviewer-recheck',
        f'{payload["loop_id"]}-orchestrator',
        f'{payload["loop_id"]}-round-reviewer',
    ]
    assert all(command.sender == 'system' for command in submitted)
    assert all(command.callback is False for command in submitted)
    assert all(command.silence is False for command in submitted)
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert set(round_json['rework']) == {'worker_rework', 'reviewer_recheck'}
    assert round_json['rework']['worker_rework']['status'] == 'completed'
    assert round_json['rework']['reviewer_recheck']['status'] == 'completed'
    if expected_result == 'pass':
        assert (project_root / 'lab_docs' / 'bounded_rework_note.md').read_text(encoding='utf-8') == 'status: reviewed\n'
        assert round_json['authority_update']['source'] == 'isolated_workspace_changes_promoted'
        assert round_json['authority_update']['changed_files'] == ['lab_docs/bounded_rework_note.md']
        assert round_json['authority_update']['allowed_change_paths'] == ['lab_docs/bounded_rework_note.md']
        assert round_json['authority_update']['verified_project_root'] is True
    else:
        assert (project_root / 'lab_docs' / 'bounded_rework_note.md').read_text(encoding='utf-8') == 'status: draft\n'
        assert round_json['authority_update']['source'] == 'isolated_workspace_changes_promoted'
        assert round_json['authority_update']['changed_files'] == ['lab_docs/bounded_rework_note.md']
        assert round_json['authority_update']['authority_rollback'] == 'restored_project_root'
        assert round_json['authority_update']['authority_rollback_reason'] == 'non_pass_round_result:replan_required'
    asks = [
        json.loads(line)['purpose']
        for line in (project_root / '.ccb' / 'runtime' / 'loops' / payload['loop_id'] / 'asks.jsonl').read_text(encoding='utf-8').splitlines()
    ]
    assert asks == ['worker', 'reviewer', 'worker_rework', 'reviewer_recheck', 'orchestrator', 'ccb_round_reviewer']
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-rework'))
    assert shown['task']['status'] == expected_status
    assert shown['task']['current_loop'] is None


def test_loop_runner_direct_execution_blocks_without_asks_when_topology_not_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    _add_ready_plan_task(project_root, task_id='task-direct')
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    topology_calls: list[str] = []

    def fake_loop_topology(_context, topology_command):
        action = str(topology_command.action)
        topology_calls.append(action)
        loop_id = str(topology_command.loop_id)
        loop_dir = project_root / '.ccb' / 'runtime' / 'loops' / loop_id
        proposal_path = loop_dir / 'topology_proposals' / 'ask-first-execution.json'
        desired_path = loop_dir / 'agent_mount_topology.desired.json'
        observed_path = loop_dir / 'agent_mount_topology.observed.json'
        if action == 'propose':
            _write_json(
                proposal_path,
                {
                    'schema': 'ccb.loop.agent_mount_topology.proposal.v1',
                    'loop_id': loop_id,
                    'agents': [],
                    'windows': [],
                },
            )
            return {
                'loop_topology_status': 'proposed',
                'loop_id': loop_id,
                'proposal_id': 'ask-first-execution',
                'proposal_path': str(proposal_path),
                'validation': {'agent_count': 2},
            }
        if action == 'commit':
            _write_json(
                desired_path,
                {
                    'schema': 'ccb.loop.agent_mount_topology.v1',
                    'loop_id': loop_id,
                    'revision': 1,
                    'agents': [],
                    'windows': [],
                },
            )
            _write_json(
                observed_path,
                {
                    'schema': 'ccb.loop.agent_mount_topology.observed.v1',
                    'loop_id': loop_id,
                    'desired_revision': 1,
                    'last_reconcile_status': 'failed',
                    'agents': [],
                    'windows': [],
                },
            )
            return {
                'loop_topology_status': 'committed',
                'loop_id': loop_id,
                'desired_path': str(desired_path),
                'reconcile': {
                    'loop_topology_status': 'failed',
                    'loop_id': loop_id,
                    'observed_path': str(observed_path),
                    'agent_count': 2,
                    'released_count': 0,
                    'retained_count': 0,
                },
            }
        if action == 'status':
            return {
                'loop_topology_status': 'failed',
                'loop_id': loop_id,
                'desired_path': str(desired_path),
                'observed_path': str(observed_path),
            }
        if action == 'release':
            return {
                'loop_topology_status': 'released',
                'loop_id': loop_id,
                'desired_path': str(desired_path),
                'observed_path': str(observed_path),
                'released_count': 0,
                'retained_count': 0,
                'released_agents': [],
            }
        raise AssertionError(f'unexpected topology action: {action}')

    def forbidden_submit_ask(*_args, **_kwargs):
        raise AssertionError('topology-not-ready must stop before worker/reviewer asks')

    def forbidden_watch_ask_job(*_args, **_kwargs):
        raise AssertionError('topology-not-ready must not watch ask jobs')

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not run the legacy fixed bridge')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            loop_topology=fake_loop_topology,
            submit_ask=forbidden_submit_ask,
            watch_ask_job=forbidden_watch_ask_job,
            loop_run_once=forbidden_loop_run_once,
            plan_task=plan_task,
        ),
    )

    assert payload['loop_runner_status'] == 'ok'
    assert payload['execution_mode'] == 'ask_first_direct_execution'
    assert payload['round_result'] == 'blocked'
    assert payload['round_result_source'] == 'topology_not_ready'
    assert payload['task_status'] == 'blocked'
    assert payload['next_activation'] == 'terminal'
    assert payload['topology']['status'] == 'failed'
    assert payload['release']['released_count'] == 0
    assert payload['release']['retained_count'] == 0
    assert topology_calls == ['propose', 'commit', 'status', 'release']
    loop_dir = project_root / '.ccb' / 'runtime' / 'loops' / payload['loop_id']
    assert not (loop_dir / 'asks.jsonl').exists()
    summary_text = Path(str(payload['round']['round_path'])).read_text(encoding='utf-8')
    assert 'round result: blocked' in summary_text
    assert 'source: topology_not_ready' in summary_text
    assert 'mount topology status failed; expected ready' in summary_text
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['failure']['source'] == 'topology_not_ready'
    assert round_json['failure']['stage'] == 'topology'
    assert round_json['failure']['reason'] == 'mount topology status failed; expected ready'
    assert round_json['failure']['loop_topology_status'] == 'failed'
    assert round_json['failure']['topology_status']['loop_topology_status'] == 'failed'
    assert round_json['topology']['release']['released_count'] == 0
    assert round_json['authority_import']['status'] == 'blocked'
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-direct'))
    assert shown['task']['status'] == 'blocked'
    assert shown['task']['current_loop'] is None
    assert shown['task']['artifacts']['round_summary']['round_result'] == 'blocked'


def test_loop_runner_direct_execution_submit_failure_blocks_and_releases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _add_ready_plan_task(project_root, task_id='task-direct')
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    submitted: list[object] = []

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        raise RuntimeError('submit transport failed')

    def forbidden_watch_ask_job(*_args, **_kwargs):
        raise AssertionError('submit failure must not watch ask jobs')

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not run the legacy fixed bridge')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=forbidden_watch_ask_job,
            loop_run_once=forbidden_loop_run_once,
            plan_task=plan_task,
        ),
    )

    assert payload['loop_runner_status'] == 'ok'
    assert payload['round_result'] == 'blocked'
    assert payload['round_result_source'] == 'ask_submission_failed'
    assert payload['task_status'] == 'blocked'
    assert payload['release']['released_count'] == 2
    assert payload['release']['retained_count'] == 0
    assert len(submitted) == 1
    assert submitted[0].target.startswith(f'loop-{payload["loop_id"]}-coder-')
    assert submitted[0].sender == 'system'
    assert submitted[0].callback is False
    assert submitted[0].silence is False
    loop_dir = project_root / '.ccb' / 'runtime' / 'loops' / payload['loop_id']
    assert not (loop_dir / 'asks.jsonl').exists()
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['failure']['source'] == 'ask_submission_failed'
    assert round_json['failure']['stage'] == 'worker_ask'
    assert round_json['failure']['reason'] == 'submit transport failed'
    assert round_json['topology']['release']['released_count'] == 2
    assert round_json['authority_import']['status'] == 'blocked'
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-direct'))
    assert shown['task']['status'] == 'blocked'
    assert shown['task']['current_loop'] is None


def test_loop_runner_direct_execution_ask_failure_blocks_and_releases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _add_ready_plan_task(project_root, task_id='task-direct')
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    submitted: list[object] = []

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': f'job_{len(submitted)}', 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, _job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        raise RuntimeError('watch transport failed')

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not run the legacy fixed bridge')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            loop_run_once=forbidden_loop_run_once,
            plan_task=plan_task,
        ),
    )

    assert payload['loop_runner_status'] == 'ok'
    assert payload['round_result'] == 'blocked'
    assert payload['round_result_source'] == 'watch_failed'
    assert payload['task_status'] == 'blocked'
    assert payload['release']['released_count'] == 2
    assert payload['release']['retained_count'] == 0
    assert len(submitted) == 1
    assert submitted[0].target.startswith(f'loop-{payload["loop_id"]}-coder-')
    assert submitted[0].sender == 'system'
    assert submitted[0].callback is False
    assert submitted[0].silence is False
    loop_dir = project_root / '.ccb' / 'runtime' / 'loops' / payload['loop_id']
    asks = (loop_dir / 'asks.jsonl').read_text(encoding='utf-8').splitlines()
    assert len(asks) == 1
    summary_text = Path(str(payload['round']['round_path'])).read_text(encoding='utf-8')
    assert 'round result: blocked' in summary_text
    assert 'source: watch_failed' in summary_text
    assert 'watch transport failed' in summary_text
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['failure']['source'] == 'watch_failed'
    assert round_json['failure']['stage'] == 'worker_ask'
    assert round_json['failure']['reason'] == 'watch transport failed'
    assert round_json['topology']['release']['released_count'] == 2
    assert round_json['authority_import']['status'] == 'blocked'
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-direct'))
    assert shown['task']['status'] == 'blocked'
    assert shown['task']['current_loop'] is None


def test_loop_runner_direct_execution_missing_round_result_blocks_before_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    _add_ready_plan_task(project_root, task_id='task-direct')
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    release_seen: list[str] = []

    def fake_ask_first_execution(_context, run_command, _services):
        loop_dir = project_root / '.ccb' / 'runtime' / 'loops' / run_command.loop_id
        loop_dir.mkdir(parents=True, exist_ok=True)
        round_path = loop_dir / 'round_summary.md'
        round_json_path = loop_dir / 'round.json'
        _write(round_path, 'round checker completed without a machine result line\n')
        payload = {
            'schema_version': 1,
            'record_type': 'ccb_loop_ask_first_execution_round',
            'loop_run_status': 'ok',
            'dispatch_source': 'ask_first_mount_topology',
            'loop_id': run_command.loop_id,
            'task_id': run_command.task_id,
            'worker': {'job_id': 'job_worker'},
            'ccb_round_reviewer': {'reply': 'round reviewer completed without a machine result line\n'},
            'paths': {'round': str(round_path), 'round_json': str(round_json_path)},
        }
        _write(round_json_path, json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        return payload

    def fake_ask_first_release(_context, round_payload, _services):
        shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-direct'))
        assert shown['task']['status'] == 'blocked'
        assert shown['task']['current_loop'] is None
        release_seen.append(str(round_payload['loop_id']))
        return {
            'loop_topology_status': 'released',
            'loop_id': round_payload['loop_id'],
            'released_count': 2,
            'retained_count': 0,
            'released_agents': ['loop-x-code_reviewer-1', 'loop-x-coder-1'],
        }

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not run the legacy fixed bridge')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            ask_first_execution=fake_ask_first_execution,
            ask_first_release=fake_ask_first_release,
            loop_run_once=forbidden_loop_run_once,
            plan_task=plan_task,
        ),
    )

    assert payload['loop_runner_status'] == 'ok'
    assert payload['execution_mode'] == 'ask_first_direct_execution'
    assert payload['round_result'] == 'blocked'
    assert payload['round_result_source'] == 'missing_round_reviewer_result'
    assert payload['task_status'] == 'blocked'
    assert payload['release']['released_count'] == 2
    assert release_seen == [payload['loop_id']]
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-direct'))
    assert shown['task']['status'] == 'blocked'
    assert shown['task']['artifacts']['round_summary']['round_result'] == 'blocked'


def test_loop_runner_direct_execution_unknown_round_result_blocks_and_releases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_workflow_topology(tmp_path, monkeypatch)
    _write(project_root / 'lab_docs' / 'unknown_round_note.md', 'status: draft\n')
    _add_ready_plan_task(
        project_root,
        task_id='task-direct',
        execution_contract_text='execution contract text\nallowed_change_paths: lab_docs/unknown_round_note.md\n',
    )
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id='task-direct', route='direct_execution')
    submitted: list[object] = []
    targets_by_job: dict[str, str] = {}

    def fake_submit_ask(_context, ask_command):
        submitted.append(ask_command)
        job_id = f'job_{len(submitted)}'
        targets_by_job[job_id] = ask_command.target
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{len(submitted)}',
            jobs=({'job_id': job_id, 'agent_name': ask_command.target, 'status': 'accepted'},),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        assert timeout == 11.0
        assert emit_output is False
        target = targets_by_job[str(job_id)]
        if target.startswith('loop-') and '-coder-' in target:
            workspace = _seed_copy_workspace_binding(context, project_root, target)
            _write(workspace / 'lab_docs' / 'unknown_round_note.md', 'status: reviewed\n')
        reply = 'round result: mystery\n' if target == 'ccb_round_reviewer' else f'reply from {target}'
        return WatchEventBatch(
            target=job_id,
            job_id=job_id,
            agent_name=target,
            target_kind='job',
            target_name=job_id,
            provider='codex',
            provider_instance=None,
            cursor=1,
            generation=1,
            terminal=True,
            status='completed',
            reply=reply,
            events=(),
        )

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('direct_execution route must not run the legacy fixed bridge')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
            loop_run_once=forbidden_loop_run_once,
            plan_task=plan_task,
        ),
    )

    assert payload['loop_runner_status'] == 'ok'
    assert payload['round_result'] == 'blocked'
    assert payload['round_result_source'] == 'unknown_round_result'
    assert payload['task_status'] == 'blocked'
    assert payload['release']['released_count'] == 2
    assert payload['release']['retained_count'] == 0
    assert len(submitted) == 4
    assert (project_root / 'lab_docs' / 'unknown_round_note.md').read_text(encoding='utf-8') == 'status: draft\n'
    round_json = json.loads(Path(str(payload['round']['round_json_path'])).read_text(encoding='utf-8'))
    assert round_json['failure']['source'] == 'unknown_round_result'
    assert round_json['failure']['reason'] == "unknown round result 'mystery'"
    assert round_json['failure']['unknown_round_result'] == 'mystery'
    assert round_json['failure']['authority_rollback'] == 'restored_project_root'
    assert round_json['authority_update']['authority_rollback'] == 'restored_project_root'
    assert round_json['topology']['release']['released_count'] == 2
    assert round_json['authority_import']['status'] == 'blocked'
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-direct'))
    assert shown['task']['status'] == 'blocked'
    assert shown['task']['current_loop'] is None
    assert shown['task']['artifacts']['round_summary']['round_result'] == 'blocked'


def test_loop_runner_needs_detail_route_activates_detailer_only_after_route_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    _add_ready_plan_task(project_root, task_id='task-detail')
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    seen: list[str] = []

    def fake_submit_before_route(_context, ask_command):
        seen.append(ask_command.target)
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=None,
            jobs=({'job_id': 'job_orchestrator_before_route', 'agent_name': ask_command.target, 'status': 'submitted'},),
        )

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('needs_detail task must not execute before route import')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(submit_ask=fake_submit_before_route, loop_run_once=forbidden_loop_run_once),
    )
    assert payload['action'] == 'activated_orchestrator'
    assert seen == ['orchestrator']

    _import_orchestration_notes(context, project_root, task_id='task-detail', route='needs_detail')

    def fake_submit_detailer(_context, ask_command):
        seen.append(ask_command.target)
        assert ask_command.target == 'task_detailer'
        assert 'Artifact refs:' in ask_command.message
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=None,
            jobs=({'job_id': 'job_task_detailer', 'agent_name': 'task_detailer', 'status': 'submitted'},),
        )

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(submit_ask=fake_submit_detailer, loop_run_once=forbidden_loop_run_once),
    )
    assert payload['loop_runner_status'] == 'ok'
    assert payload['action'] == 'activated_task_detailer'
    assert payload['reason'] == 'orchestrator_route_needs_detail'
    assert payload['next_owner'] == 'orchestrator'
    assert seen == ['orchestrator', 'task_detailer']

    for kind, filename in (
        ('detail_design', 'detail-design.md'),
        ('detail_summary', 'detail-summary.md'),
        ('detail_packet', 'detail-packet.json'),
    ):
        source = project_root / 'drafts' / filename
        _write(source, f'{kind}\n')
        plan_task(
            context,
            SimpleNamespace(action='task-artifact', task_id='task-detail', artifact_kind=kind, file_path=str(source)),
        )

    def fake_submit_orchestrator_after_detail(_context, ask_command):
        seen.append(ask_command.target)
        assert ask_command.target == 'orchestrator'
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=None,
            jobs=({'job_id': 'job_orchestrator_after_detail', 'agent_name': 'orchestrator', 'status': 'submitted'},),
        )

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(submit_ask=fake_submit_orchestrator_after_detail, loop_run_once=forbidden_loop_run_once),
    )
    assert payload['action'] == 'activated_orchestrator'
    assert payload['reason'] == 'orchestrator_route_needs_detail_detail_ready'
    assert seen == ['orchestrator', 'task_detailer', 'orchestrator']


@pytest.mark.parametrize(
    ('route', 'expected_action', 'expected_reason', 'expected_owner', 'expected_next_activation'),
    (
        (
            'macro_adjustment_request',
            'planner_next_action_required',
            'orchestrator_route_macro_adjustment_request',
            'planner',
            'planner_status_transition_required',
        ),
        (
            'blocked',
            'blocker_evidence_required',
            'orchestrator_route_blocked',
            'frontdesk',
            'blocker_evidence_required',
        ),
    ),
)
def test_loop_runner_macro_and_blocked_routes_pause_without_mounting_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    expected_action: str,
    expected_reason: str,
    expected_owner: str,
    expected_next_activation: str,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    task_id = f'task-{route.replace("_", "-")}'
    _add_ready_plan_task(project_root, task_id=task_id)
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    _import_orchestration_notes(context, project_root, task_id=task_id, route=route)

    def forbidden_submit_ask(*_args, **_kwargs):
        raise AssertionError(f'{route} route must not ask detailer, worker, or reviewer')

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError(f'{route} route must not run the fixed bridge')

    def forbidden_topology_dispatch(*_args, **_kwargs):
        raise AssertionError(f'{route} route must not execute topology dispatch')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(
            submit_ask=forbidden_submit_ask,
            loop_run_once=forbidden_loop_run_once,
            topology_dispatch=forbidden_topology_dispatch,
            plan_task=plan_task,
        ),
    )

    assert payload['loop_runner_status'] == 'paused'
    assert payload['action'] == expected_action
    assert payload['reason'] == expected_reason
    assert payload['task_status'] == 'ready_for_orchestration'
    assert payload['next_owner'] == expected_owner
    assert payload['next_activation'] == expected_next_activation
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id=task_id))
    assert shown['task']['status'] == 'ready_for_orchestration'
    assert shown['task']['current_loop'] is None
    assert shown['task']['artifacts']['orchestration_notes']['orchestrator_route'] == route
    assert 'round_summary' not in shown['task']['artifacts']


def test_loop_runner_once_does_not_infer_pass_without_round_checker_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    _add_legacy_ready_plan_task(project_root, task_id='task-runner')
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    def fake_loop_run_once(_context, run_command, _services):
        loop_dir = project_root / '.ccb' / 'runtime' / 'loops' / run_command.loop_id
        loop_dir.mkdir(parents=True, exist_ok=True)
        round_path = loop_dir / 'round.json'
        payload = {
            'schema_version': 1,
            'record_type': 'ccb_loop_run_once_round',
            'loop_run_status': 'ok',
            'loop_id': run_command.loop_id,
            'task_id': run_command.task_id,
            'round_checker': {'reply': 'round checker completed without a machine result line\n'},
            'paths': {'round': str(round_path)},
        }
        _write(round_path, json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        return payload

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(loop_run_once=fake_loop_run_once, plan_task=plan_task),
    )

    assert payload['loop_runner_status'] == 'ok'
    assert payload['round_result'] == 'blocked'
    assert payload['round_result_source'] == 'missing_round_checker_result'
    assert payload['task_status'] == 'blocked'
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-runner'))
    assert shown['task']['status'] == 'blocked'
    assert shown['task']['artifacts']['round_summary']['round_result'] == 'blocked'


def test_loop_runner_once_rejects_unknown_round_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    _add_legacy_ready_plan_task(project_root, task_id='task-runner')
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    def fake_loop_run_once(_context, run_command, _services):
        loop_dir = project_root / '.ccb' / 'runtime' / 'loops' / run_command.loop_id
        loop_dir.mkdir(parents=True, exist_ok=True)
        round_path = loop_dir / 'round.json'
        payload = {
            'schema_version': 1,
            'record_type': 'ccb_loop_run_once_round',
            'loop_run_status': 'ok',
            'loop_id': run_command.loop_id,
            'task_id': run_command.task_id,
            'round_checker': {'reply': 'round result: mystery\n'},
            'paths': {'round': str(round_path)},
        }
        _write(round_path, json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        return payload

    with pytest.raises(RuntimeError, match="unknown round result 'mystery'"):
        loop_runner_once(
            context,
            command,
            services=SimpleNamespace(loop_run_once=fake_loop_run_once, plan_task=plan_task),
        )


def test_loop_runner_once_returns_idle_when_no_ready_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    command = ParsedLoopRunnerCommand(project=None, once=True, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    payload = loop_runner_once(context, command, services=SimpleNamespace())

    assert payload['loop_runner_status'] == 'idle'
    assert payload['reason'] == 'no_actionable_task'


def test_loop_runner_once_activates_planner_for_draft_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    _add_plan_task_record(project_root, task_id='task-draft', status='draft')
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    seen: dict[str, object] = {}

    def fake_submit_ask(_context, ask_command):
        seen['target'] = ask_command.target
        seen['sender'] = ask_command.sender
        seen['task_id'] = ask_command.task_id
        seen['artifact_request'] = ask_command.artifact_request
        seen['message'] = ask_command.message
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=None,
            jobs=({'job_id': 'job_planner', 'agent_name': 'planner', 'status': 'submitted'},),
        )

    def forbidden_loop_run_once(*_args, **_kwargs):
        raise AssertionError('draft task must not start execution')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(submit_ask=fake_submit_ask, loop_run_once=forbidden_loop_run_once),
    )

    assert payload['loop_runner_status'] == 'ok'
    assert payload['action'] == 'activated_planner'
    assert payload['reason'] == 'draft_task'
    assert payload['task_id'] == 'task-draft'
    assert payload['next_owner'] == 'planner'
    assert payload['ask']['job_id'] == 'job_planner'
    assert seen['target'] == 'planner'
    assert seen['sender'] == 'system'
    assert seen['artifact_request'] is True
    assert 'Status: draft' in str(seen['message'])
    assert 'Optional machine import bundle' not in str(seen['message'])
    assert 'ccb.loop.planner_artifact_bundle/v1' not in str(seen['message'])
    activation = json.loads(Path(str(payload['activation_path'])).read_text(encoding='utf-8'))
    assert activation['task_id'] == 'task-draft'
    assert activation['ask']['job_id'] == 'job_planner'
    assert activation['script_write_rules']


def test_loop_runner_new_activation_metadata_ignores_legacy_artifact_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    task_root = project_root / 'docs' / 'plantree' / 'plans' / 'demo-plan' / 'tasks' / 'task-new-draft'
    artifacts: dict[str, dict[str, object]] = {}
    for kind, filename, text in (
        ('requirements', 'requirements.md', 'requirements text\n'),
        ('acceptance', 'acceptance-criteria.md', 'acceptance text\n'),
        ('verification', 'verification-contract.md', 'verification text\n'),
        ('handoff', 'handoff.md', 'handoff text\n'),
    ):
        path = task_root / filename
        _write(path, text)
        artifacts[kind] = {
            'kind': kind,
            'path': str(path.relative_to(project_root)),
            'source_path': str(path.relative_to(project_root)),
            'sha256': 'test',
            'bytes': len(text.encode('utf-8')),
            'imported_at': '2026-06-27T00:00:00Z',
        }
    _add_plan_task_record(
        project_root,
        task_id='task-new-draft',
        status='draft',
        artifacts=artifacts,
        next_owner='planner',
        activation_reason='test_new_activation_contract',
    )
    command = ParsedLoopRunnerCommand(project=None, once=True, timeout_s=11.0, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    def fake_submit_ask(_context, ask_command):
        assert ask_command.target == 'planner'
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=None,
            jobs=({'job_id': 'job_planner_new', 'agent_name': 'planner', 'status': 'submitted'},),
        )

    payload = loop_runner_once(context, command, services=SimpleNamespace(submit_ask=fake_submit_ask))

    assert payload['action'] == 'activated_planner'
    assert payload['next_owner'] == 'planner'


def test_loop_runner_once_rejects_consume_role_output_authority_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    _add_plan_task_record(project_root, task_id='task-draft', status='draft')
    command = ParsedLoopRunnerCommand(
        project=None,
        once=True,
        timeout_s=11.0,
        consume_role_output=True,
        json_output=True,
    )
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    submitted: list[str] = []

    def forbidden_submit_ask(_context, ask_command):
        submitted.append(ask_command.target)
        raise AssertionError('--consume-role-output must fail before provider ask submission')

    def forbidden_watch_ask_job(*_args, **_kwargs):
        raise AssertionError('--consume-role-output must not watch provider replies')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(submit_ask=forbidden_submit_ask, watch_ask_job=forbidden_watch_ask_job),
    )

    assert payload['loop_runner_status'] == 'rejected'
    assert payload['action'] == 'consume_role_output_disabled'
    assert '--consume-role-output is legacy/disabled' in payload['reason']
    assert 'script-owned artifact imports' in payload['reason']
    assert payload['next_activation'] == 'none'
    assert submitted == []
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-draft'))
    assert shown['task']['status'] == 'draft'
    assert shown['task'].get('artifacts') == {}

    result, cli_payload, stderr = _run_phase2(
        ['loop', 'runner', '--once', '--consume-role-output', '--json'],
        cwd=project_root,
    )
    assert result == 1, stderr
    assert cli_payload['loop_runner_status'] == 'rejected'
    assert cli_payload['action'] == 'consume_role_output_disabled'
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id='task-draft'))
    assert shown['task']['status'] == 'draft'
    assert shown['task'].get('artifacts') == {}


def test_loop_runner_once_activates_planner_with_round_evidence_for_partial_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    round_path = project_root / 'docs' / 'plantree' / 'plans' / 'demo-plan' / 'tasks' / 'task-partial' / 'round-partial.md'
    _write(round_path, 'round result: partial\n')
    _add_plan_task_record(
        project_root,
        task_id='task-partial',
        status='partial',
        artifacts={
            'round_partial': {
                'kind': 'round_partial',
                'path': str(round_path.relative_to(project_root)),
                'source_path': str(round_path.relative_to(project_root)),
                'sha256': 'test',
                'bytes': 22,
                'imported_at': '2026-06-27T00:00:00Z',
                'loop_id': 'loop-prev',
                'round_result': 'partial',
            }
        },
    )
    command = ParsedLoopRunnerCommand(project=None, once=True, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    def fake_submit_ask(_context, _ask_command):
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=None,
            jobs=({'job_id': 'job_planner_partial', 'agent_name': 'planner', 'status': 'submitted'},),
        )

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(submit_ask=fake_submit_ask),
    )

    assert payload['action'] == 'activated_planner'
    assert payload['reason'] == 'partial_task'
    activation = json.loads(Path(str(payload['activation_path'])).read_text(encoding='utf-8'))
    assert activation['round_evidence_refs'] == [
        {
            'kind': 'round_partial',
            'path': str(round_path.relative_to(project_root)),
            'round_result': 'partial',
            'loop_id': 'loop-prev',
        }
    ]


@pytest.mark.parametrize(
    ('status', 'expected_action', 'expected_runner_status', 'expected_owner'),
    (
        ('needs_clarification', 'paused', 'paused', 'frontdesk'),
        ('blocked', 'blocked', 'blocked', 'terminal'),
        ('done', 'terminal', 'terminal', 'terminal'),
    ),
)
def test_loop_runner_once_stops_without_provider_activation_for_paused_or_terminal_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected_action: str,
    expected_runner_status: str,
    expected_owner: str,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    _add_plan_task_record(project_root, task_id=f'task-{status}', status=status)
    command = ParsedLoopRunnerCommand(project=None, once=True, json_output=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    def forbidden_submit(*_args, **_kwargs):
        raise AssertionError('paused or terminal tasks must not submit asks')

    payload = loop_runner_once(
        context,
        command,
        services=SimpleNamespace(submit_ask=forbidden_submit),
    )

    assert payload['loop_runner_status'] == expected_runner_status
    assert payload['action'] == expected_action
    assert payload['task_id'] == f'task-{status}'
    assert payload['next_owner'] == expected_owner


def test_loop_run_once_records_failure_and_releases_after_watch_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    command = ParsedLoopRunOnceCommand(project=None, loop_id='round1', task='ship the slice', timeout_s=7.0)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    calls: list[tuple[str, object]] = []

    def fake_loop_capacity(_context, capacity_command):
        calls.append(('capacity', capacity_command.action))
        if capacity_command.action == 'ensure':
            return {
                'loop_capacity_status': 'ensured',
                'loop_id': capacity_command.loop_id,
                'project_id': context.project.project_id,
                'agent_count': 2,
                'agents': [
                    {'name': 'loop-round1-worker-1', 'profile': 'worker', 'state': 'planned'},
                    {'name': 'loop-round1-code_reviewer-1', 'profile': 'code_reviewer', 'state': 'planned'},
                ],
                'apply': {'apply_status': 'applied', 'action': 'add_agent'},
            }
        if capacity_command.action == 'release':
            return {
                'loop_capacity_status': 'released',
                'loop_id': capacity_command.loop_id,
                'project_id': context.project.project_id,
                'agent_count': 2,
                'released_count': 2,
                'retained_count': 0,
                'release_policy': 'auto',
                'idle_only': True,
                'agents': [
                    {'name': 'loop-round1-worker-1', 'profile': 'worker', 'state': 'released'},
                    {'name': 'loop-round1-code_reviewer-1', 'profile': 'code_reviewer', 'state': 'released'},
                ],
                'apply': {'apply_status': 'applied', 'action': 'remove_agent'},
            }
        raise AssertionError(f'unexpected capacity action {capacity_command.action}')

    def fake_submit_ask(_context, ask_command):
        calls.append(('ask', ask_command.target))
        return AskSummary(
            project_id=context.project.project_id,
            submission_id=f'sub_{ask_command.target}',
            jobs=(
                {
                    'job_id': f'job_{ask_command.target}',
                    'agent_name': ask_command.target,
                    'status': 'accepted',
                },
            ),
        )

    def fake_watch_ask_job(_context, job_id, _out, *, timeout, emit_output):
        calls.append(('watch', job_id))
        raise RuntimeError('watch transport failed')

    payload = loop_run_once(
        context,
        command,
        services=SimpleNamespace(
            loop_capacity=fake_loop_capacity,
            submit_ask=fake_submit_ask,
            watch_ask_job=fake_watch_ask_job,
        ),
    )

    assert payload['loop_run_status'] == 'failed'
    assert payload['failure'] == {
        'stage': 'execution',
        'error_type': 'RuntimeError',
        'error': 'watch transport failed',
    }
    assert payload['capacity']['release']['loop_capacity_status'] == 'released'
    assert payload['capacity']['release']['release_policy'] == 'auto'
    assert payload['capacity']['release']['idle_only'] is True
    assert calls == [
        ('capacity', 'ensure'),
        ('ask', 'loop-round1-worker-1'),
        ('watch', 'job_loop-round1-worker-1'),
        ('capacity', 'release'),
    ]

    loop_dir = project_root / '.ccb' / 'runtime' / 'loops' / 'round1'
    round_payload = json.loads((loop_dir / 'round.json').read_text(encoding='utf-8'))
    events = [json.loads(line) for line in (loop_dir / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    breadcrumb = (loop_dir / 'breadcrumb.md').read_text(encoding='utf-8')
    assert round_payload['loop_run_status'] == 'failed'
    assert round_payload['failure']['error'] == 'watch transport failed'
    assert [event['kind'] for event in events] == [
        'loop_run_started',
        'loop_run_failed',
        'loop_run_finished',
    ]
    assert 'Phase: blocked\n' in breadcrumb
    assert 'Blocked: failed\n' in breadcrumb


def test_loop_run_once_json_handler_returns_nonzero_for_incomplete_round() -> None:
    out = StringIO()
    command = ParsedLoopRunOnceCommand(project=None, loop_id='round1', task='ship', json_output=True)

    exit_code = handle_loop_run_once(
        SimpleNamespace(),
        command,
        out,
        SimpleNamespace(
            loop_run_once=lambda _context, _command, _services: {'loop_run_status': 'incomplete'},
            render_loop_run_once=lambda _payload: (),
            write_lines=lambda _out, _lines: None,
        ),
    )

    assert exit_code == 1
    assert json.loads(out.getvalue()) == {'loop_run_status': 'incomplete'}


def test_loop_run_once_does_not_bootstrap_missing_project(tmp_path: Path) -> None:
    stdout = StringIO()
    stderr = StringIO()

    result = maybe_handle_phase2(
        ['loop', 'run-once', '--loop-id', 'round1', '--task', 'ship', '--json'],
        cwd=tmp_path,
        stdout=stdout,
        stderr=stderr,
    )

    assert result == 1
    assert stdout.getvalue() == ''
    assert not (tmp_path / '.ccb').exists()
    assert 'command_status: failed' in stderr.getvalue()


def test_loop_capacity_ensure_status_release_json_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)

    result, ensured, err = _run_phase2(
        [
            'loop',
            'capacity',
            'ensure',
            '--loop-id',
            'round1',
            '--profile',
            'worker=1',
            '--profile',
            'code_reviewer=1',
            '--json',
        ],
        cwd=project_root,
    )

    assert result == 0
    assert err == ''
    assert ensured['loop_capacity_status'] == 'ensured'
    assert ensured['loop_id'] == 'round1'
    assert ensured['agent_count'] == 2
    assert [agent['name'] for agent in ensured['agents']] == [
        'loop-round1-worker-1',
        'loop-round1-code_reviewer-1',
    ]
    assert ensured['agents'][0]['role'] == 'agentroles.coder'
    assert ensured['agents'][0]['workspace_group'] == 'worker_pool'
    assert ensured['apply']['apply_status'] == 'deferred_until_start'
    assert Path(str(ensured['state_path'])).is_file()

    validate_out = StringIO()
    validate_err = StringIO()
    validate_result = maybe_handle_phase2(
        ['config', 'validate'],
        cwd=project_root,
        stdout=validate_out,
        stderr=validate_err,
    )
    assert validate_result == 0
    assert validate_err.getvalue() == ''
    assert 'agents: loop-round1-code_reviewer-1, loop-round1-worker-1, orchestrator' in validate_out.getvalue()

    result, status, err = _run_phase2(
        ['loop', 'capacity', 'status', '--loop-id', 'round1', '--json'],
        cwd=project_root,
    )

    assert result == 0
    assert err == ''
    assert status['loop_capacity_status'] == 'ensured'
    assert status['agent_count'] == 2
    assert [agent['state'] for agent in status['agents']] == ['planned', 'planned']

    result, released, err = _run_phase2(
        ['loop', 'capacity', 'release', '--loop-id', 'round1', '--idle-only', '--json'],
        cwd=project_root,
    )

    assert result == 0
    assert err == ''
    assert released['loop_capacity_status'] == 'released'
    assert released['release_policy'] == 'idle-only'
    assert released['idle_only'] is True
    assert released['released_count'] == 2
    assert released['apply']['apply_status'] == 'deferred_until_start'
    assert [agent['state'] for agent in released['agents']] == ['released', 'released']

    result, rerelease, err = _run_phase2(
        ['loop', 'capacity', 'release', '--loop-id', 'round1', '--policy', 'auto', '--json'],
        cwd=project_root,
    )

    assert result == 0
    assert err == ''
    assert rerelease['loop_capacity_status'] == 'released'
    assert rerelease['release_policy'] == 'auto'
    assert rerelease['idle_only'] is True
    assert rerelease['released_count'] == 2

    state = json.loads(Path(str(released['state_path'])).read_text(encoding='utf-8'))
    events = [
        json.loads(line)
        for line in Path(str(released['events_path'])).read_text(encoding='utf-8').splitlines()
    ]
    assert state['loop_capacity_status'] == 'released'
    assert [event['event'] for event in events] == ['ensure', 'release', 'release']

    validate_out = StringIO()
    validate_err = StringIO()
    validate_result = maybe_handle_phase2(
        ['config', 'validate'],
        cwd=project_root,
        stdout=validate_out,
        stderr=validate_err,
    )
    assert validate_result == 0
    assert validate_err.getvalue() == ''
    assert 'agents: orchestrator' in validate_out.getvalue()
    assert 'loop-round1-worker-1' not in validate_out.getvalue()


def test_loop_capacity_ensure_while_mounted_reports_pane_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    worker = 'loop-round1-worker-1'
    reviewer = 'loop-round1-code_reviewer-1'
    monkeypatch.setattr(
        loop_capacity_module,
        'ping_local_state',
        lambda _context: SimpleNamespace(mount_state='mounted', socket_connectable=True, reason=None),
    )
    monkeypatch.setattr(
        loop_capacity_module,
        'reload_config',
        lambda _context, _command: {
            'status': 'published',
            'stage': 'publish_transaction',
            'plan_class': 'add_window',
            'published_graph_version': 7,
            'namespace_patch': {
                'status': 'applied',
                'agent_panes': {worker: '%2', reviewer: '%3'},
                'preserved_before': {'orchestrator': '%1'},
                'preserved_after': {'orchestrator': '%1'},
            },
            'runtime_mount': {
                'status': 'mounted',
                'mounted_agents': [worker, reviewer],
                'runtime_authority_written_agents': [worker, reviewer],
            },
        },
    )

    result, ensured, err = _run_phase2(
        [
            'loop',
            'capacity',
            'ensure',
            '--loop-id',
            'round1',
            '--profile',
            'worker=1',
            '--profile',
            'code_reviewer=1',
            '--json',
        ],
        cwd=project_root,
    )

    assert result == 0
    assert err == ''
    assert ensured['apply']['apply_status'] == 'applied'
    assert ensured['apply']['plan_class'] == 'add_window'
    assert ensured['apply']['namespace_agent_panes'] == {worker: '%2', reviewer: '%3'}
    assert ensured['apply']['namespace_preserved_before'] == {'orchestrator': '%1'}
    assert ensured['apply']['namespace_preserved_after'] == {'orchestrator': '%1'}
    assert ensured['apply']['runtime_mount_status'] == 'mounted'
    assert ensured['apply']['pane_identity_report']['added_agents'] == [
        {'agent': reviewer, 'pane_id': '%3', 'pane_identity_source': 'namespace_agent_panes'},
        {'agent': worker, 'pane_id': '%2', 'pane_identity_source': 'namespace_agent_panes'},
    ]
    assert ensured['apply']['pane_identity_report']['preserved_agents'] == [
        {
            'agent': 'orchestrator',
            'before_pane_id': '%1',
            'after_pane_id': '%1',
            'pane_identity_source': 'namespace_preserved_before_after',
            'changed': False,
        }
    ]
    assert ensured['apply']['pane_identity_report']['mounted_agents'] == [worker, reviewer]


def test_loop_capacity_ensure_rejects_unknown_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)

    stdout = StringIO()
    stderr = StringIO()
    result = maybe_handle_phase2(
        [
            'loop',
            'capacity',
            'ensure',
            '--loop-id',
            'round1',
            '--profile',
            'unknown=1',
            '--json',
        ],
        cwd=project_root,
        stdout=stdout,
        stderr=stderr,
    )

    assert result == 1
    assert stdout.getvalue() == ''
    assert 'command_status: failed' in stderr.getvalue()
    assert "unknown loop role profile 'unknown'" in stderr.getvalue()


def test_loop_capacity_release_retains_busy_agents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_loop_capacity(tmp_path, monkeypatch)
    result, ensured, err = _run_phase2(
        [
            'loop',
            'capacity',
            'ensure',
            '--loop-id',
            'round1',
            '--profile',
            'worker=1',
            '--profile',
            'code_reviewer=1',
            '--json',
        ],
        cwd=project_root,
    )
    assert result == 0
    assert err == ''
    assert ensured['agent_count'] == 2

    class FakeRuntimeStore:
        def __init__(self, _paths):
            pass

        def load_best_effort(self, agent_name):
            if agent_name == 'loop-round1-worker-1':
                return SimpleNamespace(state=SimpleNamespace(value='busy'), queue_depth=0)
            return SimpleNamespace(state=SimpleNamespace(value='idle'), queue_depth=0)

    monkeypatch.setattr(loop_capacity_module, 'AgentRuntimeStore', FakeRuntimeStore)
    monkeypatch.setattr(
        loop_capacity_module,
        'ping_local_state',
        lambda _context: SimpleNamespace(mount_state='mounted', socket_connectable=True, reason=None),
    )
    monkeypatch.setattr(
        loop_capacity_module,
        '_apply_reload_if_mounted',
        lambda _context, *, action: {'apply_status': 'applied', 'action': action, 'reload_status': 'noop'},
    )

    result, released, err = _run_phase2(
        ['loop', 'capacity', 'release', '--loop-id', 'round1', '--policy', 'auto', '--json'],
        cwd=project_root,
    )

    assert result == 0
    assert err == ''
    assert released['loop_capacity_status'] == 'ensured'
    assert released['release_policy'] == 'auto'
    assert released['idle_only'] is True
    assert released['released_count'] == 1
    assert released['retained_count'] == 1
    assert released['retained'] == [
        {
            'name': 'loop-round1-worker-1',
            'queue_depth': 0,
            'reason': 'runtime_state=busy',
            'runtime_state': 'busy',
        }
    ]
    states = {agent['name']: agent['state'] for agent in released['agents']}
    assert states == {
        'loop-round1-worker-1': 'retained',
        'loop-round1-code_reviewer-1': 'released',
    }

    validate_out = StringIO()
    validate_err = StringIO()
    validate_result = maybe_handle_phase2(
        ['config', 'validate'],
        cwd=project_root,
        stdout=validate_out,
        stderr=validate_err,
    )
    assert validate_result == 0
    assert validate_err.getvalue() == ''
    assert 'loop-round1-worker-1' in validate_out.getvalue()
    assert 'loop-round1-code_reviewer-1' not in validate_out.getvalue()

    class IdleRuntimeStore:
        def __init__(self, _paths):
            pass

        def load_best_effort(self, _agent_name):
            return SimpleNamespace(state=SimpleNamespace(value='idle'), queue_depth=0)

    monkeypatch.setattr(loop_capacity_module, 'AgentRuntimeStore', IdleRuntimeStore)
    result, final_release, err = _run_phase2(
        ['loop', 'capacity', 'release', '--loop-id', 'round1', '--policy', 'auto', '--json'],
        cwd=project_root,
    )

    assert result == 0
    assert err == ''
    assert final_release['loop_capacity_status'] == 'released'
    assert final_release['release_policy'] == 'auto'
    assert final_release['retained_count'] == 0
    final_worker = next(agent for agent in final_release['agents'] if agent['name'] == 'loop-round1-worker-1')
    assert final_worker['state'] == 'released'
    assert 'retain_reason' not in final_worker
    assert 'runtime_state' not in final_worker
    assert 'queue_depth' not in final_worker
