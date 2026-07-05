from __future__ import annotations

from io import StringIO
import json
from pathlib import Path

import pytest

from cli.models import ParsedPlanTaskCommand
from cli.parser import CliParser
from cli.phase2 import maybe_handle_phase2


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _project_with_plan(tmp_path: Path) -> Path:
    project_root = tmp_path / 'repo-plan-tasks'
    (project_root / '.ccb').mkdir(parents=True)
    _write(project_root / 'docs' / 'plantree' / 'plans' / 'demo-plan' / 'README.md', '# Demo Plan\n')
    return project_root


def _make_ready_task(project_root: Path, *, task_id: str = 'task-001') -> None:
    drafts = project_root / 'drafts'
    for name in ('task_packet', 'execution_contract'):
        _write(drafts / f'{name}.md', f'{name}\n')
    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-create',
            '--plan',
            'demo-plan',
            '--title',
            'Bridge task',
            '--task-id',
            task_id,
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    for kind in ('task_packet', 'execution_contract'):
        code, _payload, _out, err = _run_phase2(
            [
                'plan',
                'task-artifact',
                '--task',
                task_id,
                '--kind',
                kind,
                '--file',
                str(drafts / f'{kind}.md'),
                '--json',
            ],
            cwd=project_root,
        )
        assert code == 0, err
    code, payload, _out, err = _run_phase2(
        ['plan', 'task-status', '--task', task_id, '--status', 'ready_for_orchestration', '--json'],
        cwd=project_root,
    )
    assert code == 0, err
    assert payload['status'] == 'ready_for_orchestration'
    assert payload['task']['next_owner'] == 'orchestrator'


def _run_phase2(argv: list[str], *, cwd: Path) -> tuple[int, dict[str, object], str, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = maybe_handle_phase2(argv, cwd=cwd, stdout=stdout, stderr=stderr)
    out_text = stdout.getvalue()
    payload = json.loads(out_text) if out_text.strip().startswith('{') else {}
    return code, payload, out_text, stderr.getvalue()


def test_plan_parser_supports_v1_task_commands() -> None:
    parser = CliParser()

    assert parser.parse(
        ['plan', 'task-create', '--plan', 'demo-plan', '--title', 'Ship slice', '--task-id', 'task-001', '--json']
    ) == ParsedPlanTaskCommand(
        project=None,
        action='task-create',
        plan_slug='demo-plan',
        title='Ship slice',
        task_id='task-001',
        json_output=True,
    )
    assert parser.parse(
        [
            'plan',
            'task-artifact',
            '--task',
            'task-001',
            '--kind',
            'orchestration_notes',
            '--file',
            'drafts/notes.md',
            '--route',
            'direct_execution',
            '--json',
        ]
    ) == ParsedPlanTaskCommand(
        project=None,
        action='task-artifact',
        task_id='task-001',
        artifact_kind='orchestration_notes',
        file_path='drafts/notes.md',
        route='direct_execution',
        json_output=True,
    )
    assert parser.parse(
        [
            'plan',
            'task-status',
            '--task',
            'task-001',
            '--status',
            'ready_for_orchestration',
            '--next-owner',
            'orchestrator',
            '--activation-reason',
            'contract_imported',
            '--json',
        ]
    ) == ParsedPlanTaskCommand(
        project=None,
        action='task-status',
        task_id='task-001',
        status='ready_for_orchestration',
        next_owner='orchestrator',
        activation_reason='contract_imported',
        json_output=True,
    )
    assert parser.parse(['plan', 'task-show', '--task', 'task-001', '--json']) == ParsedPlanTaskCommand(
        project=None,
        action='task-show',
        task_id='task-001',
        json_output=True,
    )
    assert parser.parse(['plan', 'task-list', '--plan', 'demo-plan', '--json']) == ParsedPlanTaskCommand(
        project=None,
        action='task-list',
        plan_slug='demo-plan',
        json_output=True,
    )
    assert parser.parse(['plan', 'breadcrumb', '--task', 'task-001']) == ParsedPlanTaskCommand(
        project=None,
        action='breadcrumb',
        task_id='task-001',
    )
    assert parser.parse(
        ['plan', 'task-bind-loop', '--task', 'task-001', '--loop', 'loop-a', '--json']
    ) == ParsedPlanTaskCommand(
        project=None,
        action='task-bind-loop',
        task_id='task-001',
        loop_id='loop-a',
        json_output=True,
    )
    assert parser.parse(
        [
            'plan',
            'task-import-round',
            '--task',
            'task-001',
            '--loop',
            'loop-a',
            '--result',
            'pass',
            '--report',
            'round.json',
            '--json',
        ]
    ) == ParsedPlanTaskCommand(
        project=None,
        action='task-import-round',
        task_id='task-001',
        loop_id='loop-a',
        result='pass',
        file_path='round.json',
        json_output=True,
    )


def test_plan_task_packet_flow_enforces_review_before_ready(tmp_path: Path) -> None:
    project_root = _project_with_plan(tmp_path)
    drafts = project_root / 'drafts'
    _write(drafts / 'requirements.md', 'requirements\n')
    _write(drafts / 'acceptance.md', 'acceptance\n')
    _write(drafts / 'verification.md', 'verification\n')
    _write(drafts / 'handoff.md', 'handoff\n')
    _write(drafts / 'review.md', 'review\n')

    code, payload, _out, err = _run_phase2(
        [
            'plan',
            'task-create',
            '--plan',
            'demo-plan',
            '--title',
            'Ship planner slice',
            '--task-id',
            'task-001',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    assert payload['plan_task_status'] == 'ok'
    assert payload['task_id'] == 'task-001'
    assert payload['status'] == 'draft'

    for kind, file_name in (
        ('requirements', 'requirements.md'),
        ('acceptance', 'acceptance.md'),
        ('verification', 'verification.md'),
        ('handoff', 'handoff.md'),
    ):
        code, payload, _out, err = _run_phase2(
            [
                'plan',
                'task-artifact',
                '--task',
                'task-001',
                '--kind',
                kind,
                '--file',
                str(drafts / file_name),
                '--json',
            ],
            cwd=project_root,
        )
        assert code == 0, err
        assert payload['artifact']['kind'] == kind
        assert payload['artifact']['path'].startswith('docs/plantree/plans/demo-plan/tasks/task-001/')

    code, _payload, _out, err = _run_phase2(
        ['plan', 'task-status', '--task', 'task-001', '--status', 'ready', '--json'],
        cwd=project_root,
    )
    assert code == 1
    assert 'ready requires artifacts: review' in err

    code, payload, _out, err = _run_phase2(
        [
            'plan',
            'task-artifact',
            '--task',
            'task-001',
            '--kind',
            'review',
            '--file',
            str(drafts / 'review.md'),
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    assert payload['artifact']['sha256']

    code, payload, _out, err = _run_phase2(
        ['plan', 'task-status', '--task', 'task-001', '--status', 'ready', '--json'],
        cwd=project_root,
    )
    assert code == 0, err
    assert payload['status'] == 'ready'
    assert payload['task']['owner'] == 'loop_runner'

    code, payload, _out, err = _run_phase2(['plan', 'task-show', '--task', 'task-001', '--json'], cwd=project_root)
    assert code == 0, err
    assert payload['task']['status'] == 'ready'
    assert set(payload['task']['artifacts']) == {'acceptance', 'handoff', 'requirements', 'review', 'verification'}

    code, payload, _out, err = _run_phase2(['plan', 'task-list', '--plan', 'demo-plan', '--json'], cwd=project_root)
    assert code == 0, err
    assert payload['task_count'] == 1
    assert payload['tasks'][0]['task_id'] == 'task-001'

    code, _payload, out, err = _run_phase2(['plan', 'breadcrumb', '--task', 'task-001'], cwd=project_root)
    assert code == 0, err
    assert 'Task: task-001' in out
    assert 'Status: ready' in out
    assert 'Artifacts: acceptance, handoff, requirements, review, verification' in out

    assert not (project_root / '.ccb' / 'runtime').exists()


def test_plan_task_phase2_anchors_gate_ready_for_orchestration(tmp_path: Path) -> None:
    project_root = _project_with_plan(tmp_path)
    drafts = project_root / 'drafts'
    _write(drafts / 'task_packet.md', '# Task Packet\n')
    _write(drafts / 'execution_contract.md', '# Execution Contract\n')
    _write(drafts / 'orchestration_notes.md', '# Orchestration Notes\n')

    code, payload, _out, err = _run_phase2(
        [
            'plan',
            'task-create',
            '--plan',
            'demo-plan',
            '--title',
            'Phase 2 anchors',
            '--task-id',
            'task-phase2',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    assert payload['status'] == 'draft'
    assert payload['task']['next_owner'] == 'planner'
    assert payload['task']['activation_reason'] == 'task_created'

    code, payload, _out, err = _run_phase2(
        [
            'plan',
            'task-artifact',
            '--task',
            'task-phase2',
            '--kind',
            'task_packet',
            '--file',
            str(drafts / 'task_packet.md'),
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    assert payload['artifact']['artifact_kind'] == 'task_packet'
    assert payload['artifact']['artifact_path'].endswith('/task_packet.md')

    code, _payload, _out, err = _run_phase2(
        ['plan', 'task-status', '--task', 'task-phase2', '--status', 'ready_for_orchestration', '--json'],
        cwd=project_root,
    )
    assert code == 1
    assert 'ready_for_orchestration requires artifacts: execution_contract' in err

    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-artifact',
            '--task',
            'task-phase2',
            '--kind',
            'orchestration_notes',
            '--file',
            str(drafts / 'orchestration_notes.md'),
            '--route',
            'direct_execution',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err

    code, shown, _out, err = _run_phase2(['plan', 'task-show', '--task', 'task-phase2', '--json'], cwd=project_root)
    assert code == 0, err
    assert shown['task']['status'] == 'draft'
    assert shown['task']['next_owner'] == 'planner'
    assert shown['task']['artifacts']['orchestration_notes']['orchestrator_route'] == 'direct_execution'

    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-artifact',
            '--task',
            'task-phase2',
            '--kind',
            'execution_contract',
            '--file',
            str(drafts / 'execution_contract.md'),
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err

    code, ready, _out, err = _run_phase2(
        [
            'plan',
            'task-status',
            '--task',
            'task-phase2',
            '--status',
            'ready_for_orchestration',
            '--next-owner',
            'orchestrator',
            '--activation-reason',
            'contract_imported',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    assert ready['status'] == 'ready_for_orchestration'
    assert ready['task']['next_owner'] == 'orchestrator'
    assert ready['task']['activation_reason'] == 'contract_imported'
    assert ready['task']['artifacts']['task_packet']['path'].endswith('/task_packet.md')
    assert ready['task']['artifacts']['execution_contract']['path'].endswith('/execution_contract.md')


def test_plan_task_phase2_rejects_unknown_machine_values(tmp_path: Path) -> None:
    project_root = _project_with_plan(tmp_path)
    drafts = project_root / 'drafts'
    _write(drafts / 'task_packet.md', '# Task Packet\n')
    _write(drafts / 'execution_contract.md', '# Execution Contract\n')
    _write(drafts / 'orchestration_notes.md', '# Orchestration Notes\n')

    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-create',
            '--plan',
            'demo-plan',
            '--title',
            'Reject invalid machine fields',
            '--task-id',
            'task-invalid-machine',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err

    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-artifact',
            '--task',
            'task-invalid-machine',
            '--kind',
            'orchestration_notes',
            '--file',
            str(drafts / 'orchestration_notes.md'),
            '--route',
            'teleport',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 1
    assert 'unknown orchestrator route' in err

    for kind in ('task_packet', 'execution_contract'):
        code, _payload, _out, err = _run_phase2(
            [
                'plan',
                'task-artifact',
                '--task',
                'task-invalid-machine',
                '--kind',
                kind,
                '--file',
                str(drafts / f'{kind}.md'),
                '--json',
            ],
            cwd=project_root,
        )
        assert code == 0, err

    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-status',
            '--task',
            'task-invalid-machine',
            '--status',
            'ready_for_orchestration',
            '--next-owner',
            'worker',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 1
    assert 'unknown plan task next_owner' in err

    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-status',
            '--task',
            'task-invalid-machine',
            '--status',
            'ready_for_orchestration',
            '--next-owner',
            'planner',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 1
    assert 'requires next_owner orchestrator' in err


def test_plan_task_artifact_records_actor_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = _project_with_plan(tmp_path)
    artifact = project_root / 'drafts' / 'requirements.md'
    _write(artifact, 'requirements\n')
    monkeypatch.setenv('CCB_CALLER_ACTOR', 'planner')
    monkeypatch.setenv('CCB_ACTOR_ROLE', 'agentroles.ccb_planner')
    monkeypatch.setenv('CCB_JOB_ID', 'job_planner123')

    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-create',
            '--plan',
            'demo-plan',
            '--title',
            'Actor metadata',
            '--task-id',
            'task-actor',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err

    code, payload, _out, err = _run_phase2(
        [
            'plan',
            'task-artifact',
            '--task',
            'task-actor',
            '--kind',
            'requirements',
            '--file',
            str(artifact),
            '--json',
        ],
        cwd=project_root,
    )

    assert code == 0, err
    assert payload['artifact']['actor'] == {
        'source': 'cli',
        'actor': 'planner',
        'role': 'agentroles.ccb_planner',
        'job_id': 'job_planner123',
    }
    assert payload['task']['artifacts']['requirements']['actor']['job_id'] == 'job_planner123'


def test_plan_task_artifact_imports_plan_brief_and_task_detail_docs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_plan(tmp_path)
    monkeypatch.setenv('CCB_CALLER_ACTOR', 'plan_script')
    monkeypatch.setenv('CCB_ACTOR_ROLE', 'ccb.plan')
    monkeypatch.setenv('CCB_JOB_ID', 'job_compact123')
    drafts = project_root / 'drafts'
    _write(drafts / 'brief.md', '# Brief\n\nPlanner-owned macro summary.\n')
    _write(drafts / 'detail-design.md', '# Detail Design\n\nTask-scoped detail body.\n')
    _write(drafts / 'detail-summary.md', '# Detail Summary\n\nStable summary backfill.\n')
    _write(drafts / 'step-1.md', '# Step 1\n\nInspect.\n')
    _write(drafts / 'step-2.md', '# Step 2\n\nExecute.\n')
    _write(
        drafts / 'detail-packet.json',
        '{"schema":"ccb.loop.detail_packet_manifest/v1","status":"ready_for_review"}\n',
    )
    _write(
        drafts / 'macro-adjustment-request.json',
        '{"schema":"ccb.loop.macro_adjustment_request/v1","reason":"macro assumption changed"}\n',
    )
    _write(drafts / 'blocker-evidence.md', '# Blocker Evidence\n\nBlocked on frontdesk input.\n')

    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-create',
            '--plan',
            'demo-plan',
            '--title',
            'Brief and detail docs',
            '--task-id',
            'task-detail',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err

    for kind, file_name in (
        ('brief', 'brief.md'),
        ('detail_design', 'detail-design.md'),
        ('detail_summary', 'detail-summary.md'),
    ):
        code, payload, _out, err = _run_phase2(
            [
                'plan',
                'task-artifact',
                '--task',
                'task-detail',
                '--kind',
                kind,
                '--file',
                str(drafts / file_name),
                '--json',
            ],
            cwd=project_root,
        )
        assert code == 0, err
        assert payload['artifact']['kind'] == kind

    code, _payload, _out, err = _run_phase2(
        ['plan', 'task-status', '--task', 'task-detail', '--status', 'detail_ready', '--json'],
        cwd=project_root,
    )
    assert code == 1
    assert 'detail_ready requires artifacts' in err

    for kind, file_name in (
        ('detail_packet', 'detail-packet.json'),
        ('detail_step_1', 'step-1.md'),
        ('detail_step_2', 'step-2.md'),
        ('macro_adjustment_request', 'macro-adjustment-request.json'),
        ('blocker_evidence', 'blocker-evidence.md'),
    ):
        code, payload, _out, err = _run_phase2(
            [
                'plan',
                'task-artifact',
                '--task',
                'task-detail',
                '--kind',
                kind,
                '--file',
                str(drafts / file_name),
                '--json',
            ],
            cwd=project_root,
        )
        assert code == 0, err
        assert payload['artifact']['kind'] == kind

    code, payload, _out, err = _run_phase2(['plan', 'task-show', '--task', 'task-detail', '--json'], cwd=project_root)
    assert code == 0, err
    artifacts = payload['task']['artifacts']
    assert artifacts['brief']['scope'] == 'plan'
    assert artifacts['brief']['path'] == 'docs/plantree/plans/demo-plan/brief.md'
    assert artifacts['detail_design']['scope'] == 'task'
    assert artifacts['detail_design']['path'].endswith('/tasks/task-detail/details/task-detail-design.md')
    assert artifacts['detail_summary']['path'].endswith('/tasks/task-detail/details/brief-update-summary.md')
    assert artifacts['detail_summary']['planner_compact_import']['authority'] == 'artifact_only_no_plan_mutation'
    assert artifacts['detail_summary']['planner_compact_import']['planner_action'] == 'review_for_brief_or_task_refs'
    assert 'source_evidence_map' in artifacts['detail_summary']['planner_compact_import']['forbidden_updates']
    assert artifacts['detail_summary']['sha256']
    assert artifacts['detail_summary']['imported_at']
    assert artifacts['detail_summary']['actor']['job_id'] == 'job_compact123'
    assert artifacts['detail_packet']['path'].endswith('/tasks/task-detail/details/detail-packet.manifest.json')
    assert artifacts['detail_step_1']['path'].endswith('/tasks/task-detail/details/steps/step-1.md')
    assert artifacts['detail_step_2']['path'].endswith('/tasks/task-detail/details/steps/step-2.md')
    assert artifacts['macro_adjustment_request']['path'].endswith('/tasks/task-detail/details/macro-adjustment-request.json')
    assert artifacts['macro_adjustment_request']['planner_compact_import']['authority'] == 'request_only_no_auto_mutation'
    assert artifacts['macro_adjustment_request']['planner_compact_import']['planner_action'] == 'review_before_plan_update'
    assert 'automatic_status_mutation' in artifacts['macro_adjustment_request']['planner_compact_import']['forbidden_updates']
    assert artifacts['macro_adjustment_request']['sha256']
    assert artifacts['macro_adjustment_request']['imported_at']
    assert artifacts['macro_adjustment_request']['actor']['job_id'] == 'job_compact123'
    assert artifacts['blocker_evidence']['path'].endswith('/tasks/task-detail/blocker-evidence.md')
    assert payload['task']['status'] == 'draft'
    assert payload['task']['next_owner'] == 'planner'
    assert payload['task']['current_loop'] is None
    assert (project_root / 'docs' / 'plantree' / 'plans' / 'demo-plan' / 'brief.md').read_text(encoding='utf-8').startswith('# Brief')

    code, _payload, _out, err = _run_phase2(
        ['plan', 'task-status', '--task', 'task-detail', '--status', 'detail_ready', '--json'],
        cwd=project_root,
    )
    assert code == 0, err
    code, _payload, _out, err = _run_phase2(
        ['plan', 'task-status', '--task', 'task-detail', '--status', 'ready', '--json'],
        cwd=project_root,
    )
    assert code == 1
    assert 'ready requires artifacts' in err


def test_plan_task_needs_detail_sequence_can_reach_detail_ready_from_orchestration_ready(
    tmp_path: Path,
) -> None:
    project_root = _project_with_plan(tmp_path)
    task_id = 'phase6b-l3-needs-detail-source-inspection'
    _make_ready_task(project_root, task_id=task_id)
    drafts = project_root / 'drafts'
    _write(drafts / 'orchestration_notes.md', '# Orchestration Notes\n\nroute: needs_detail\n')
    _write(drafts / 'detail-design.md', '# Detail Design\n\nInspect source shape.\n')
    _write(drafts / 'detail-summary.md', '# Detail Summary\n\nReady for bounded import.\n')
    _write(
        drafts / 'detail-packet.json',
        '{"schema":"ccb.loop.detail_packet_manifest/v1","status":"detail_ready"}\n',
    )

    code, payload, _out, err = _run_phase2(
        [
            'plan',
            'task-artifact',
            '--task',
            task_id,
            '--kind',
            'orchestration_notes',
            '--file',
            str(drafts / 'orchestration_notes.md'),
            '--route',
            'needs_detail',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    assert payload['task']['status'] == 'ready_for_orchestration'
    assert payload['task']['artifacts']['orchestration_notes']['orchestrator_route'] == 'needs_detail'

    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-artifact',
            '--task',
            task_id,
            '--kind',
            'detail_design',
            '--file',
            str(drafts / 'detail-design.md'),
            '--route',
            'needs_detail',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 1
    assert 'plan task artifact --route is only valid for orchestration_notes' in err

    for kind, file_name in (
        ('detail_design', 'detail-design.md'),
        ('detail_summary', 'detail-summary.md'),
    ):
        code, payload, _out, err = _run_phase2(
            [
                'plan',
                'task-artifact',
                '--task',
                task_id,
                '--kind',
                kind,
                '--file',
                str(drafts / file_name),
                '--json',
            ],
            cwd=project_root,
        )
        assert code == 0, err
        assert 'orchestrator_route' not in payload['artifact']

    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-status',
            '--task',
            task_id,
            '--status',
            'detail_ready',
            '--activation-reason',
            'phase6b_l1_l4_sequence11_detail_ready',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 1
    assert 'detail_ready requires artifacts: detail_packet' in err
    assert 'invalid plan task status transition' not in err

    code, payload, _out, err = _run_phase2(
        [
            'plan',
            'task-artifact',
            '--task',
            task_id,
            '--kind',
            'detail_packet',
            '--file',
            str(drafts / 'detail-packet.json'),
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    assert 'orchestrator_route' not in payload['artifact']

    code, payload, _out, err = _run_phase2(
        [
            'plan',
            'task-status',
            '--task',
            task_id,
            '--status',
            'detail_ready',
            '--activation-reason',
            'phase6b_l1_l4_sequence11_detail_ready',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    assert payload['status'] == 'detail_ready'
    assert payload['next_owner'] == 'planner'
    assert payload['current_loop'] is None
    artifacts = payload['task']['artifacts']
    assert {'detail_design', 'detail_summary', 'detail_packet'} <= set(artifacts)


def test_plan_task_macro_and_blocked_routes_can_reach_terminal_states_from_orchestration_ready(
    tmp_path: Path,
) -> None:
    project_root = _project_with_plan(tmp_path)
    drafts = project_root / 'drafts'

    macro_task = 'phase6b-l4-macro-adjustment-request'
    _make_ready_task(project_root, task_id=macro_task)
    _write(drafts / 'macro-notes.md', '# Orchestration Notes\n\nroute: macro_adjustment_request\n')
    _write(
        drafts / 'macro-adjustment-request.md',
        '# Macro Adjustment Request\n\nAccepted workflow authority conflicts with requested topology DSL.\n',
    )
    code, payload, _out, err = _run_phase2(
        [
            'plan',
            'task-artifact',
            '--task',
            macro_task,
            '--kind',
            'orchestration_notes',
            '--file',
            str(drafts / 'macro-notes.md'),
            '--route',
            'macro_adjustment_request',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    assert payload['task']['status'] == 'ready_for_orchestration'
    code, payload, _out, err = _run_phase2(
        [
            'plan',
            'task-artifact',
            '--task',
            macro_task,
            '--kind',
            'macro_adjustment_request',
            '--file',
            str(drafts / 'macro-adjustment-request.md'),
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    code, payload, _out, err = _run_phase2(
        [
            'plan',
            'task-status',
            '--task',
            macro_task,
            '--status',
            'replan_required',
            '--next-owner',
            'planner',
            '--activation-reason',
            'phase6b_l1_l4_sequence12_macro',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    assert payload['status'] == 'replan_required'
    assert payload['next_owner'] == 'planner'

    blocked_task = 'phase6b-l4-blocked-missing-secret'
    _make_ready_task(project_root, task_id=blocked_task)
    _write(drafts / 'blocked-notes.md', '# Orchestration Notes\n\nroute: blocked\n')
    _write(drafts / 'blocker-evidence.md', '# Blocker Evidence\n\nMissing PHASE6B_LAB_PRIVATE_API_TOKEN.\n')
    code, payload, _out, err = _run_phase2(
        [
            'plan',
            'task-artifact',
            '--task',
            blocked_task,
            '--kind',
            'orchestration_notes',
            '--file',
            str(drafts / 'blocked-notes.md'),
            '--route',
            'blocked',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    assert payload['task']['status'] == 'ready_for_orchestration'
    code, payload, _out, err = _run_phase2(
        [
            'plan',
            'task-artifact',
            '--task',
            blocked_task,
            '--kind',
            'blocker_evidence',
            '--file',
            str(drafts / 'blocker-evidence.md'),
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    code, payload, _out, err = _run_phase2(
        [
            'plan',
            'task-status',
            '--task',
            blocked_task,
            '--status',
            'blocked',
            '--activation-reason',
            'phase6b_l1_l4_sequence12_blocked',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    assert payload['status'] == 'blocked'
    assert payload['next_owner'] == 'terminal'
    artifacts = payload['task']['artifacts']
    assert 'round_summary' not in artifacts
    assert 'last_round' not in payload['task']


def test_plan_task_artifact_rejects_project_external_file(tmp_path: Path) -> None:
    project_root = _project_with_plan(tmp_path)
    outside = tmp_path / 'outside.md'
    outside.write_text('outside\n', encoding='utf-8')

    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-create',
            '--plan',
            'demo-plan',
            '--title',
            'Unsafe artifact',
            '--task-id',
            'task-unsafe',
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err

    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-artifact',
            '--task',
            'task-unsafe',
            '--kind',
            'requirements',
            '--file',
            str(outside),
            '--json',
        ],
        cwd=project_root,
    )

    assert code == 1
    assert 'must be inside project root' in err


def test_plan_task_rejects_invalid_existing_index(tmp_path: Path) -> None:
    project_root = _project_with_plan(tmp_path)
    _write(project_root / 'docs' / 'plantree' / 'plans' / 'demo-plan' / 'tasks' / 'index.json', '{broken')

    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-create',
            '--plan',
            'demo-plan',
            '--title',
            'Do not overwrite corrupt index',
            '--task-id',
            'task-corrupt',
            '--json',
        ],
        cwd=project_root,
    )

    assert code == 1
    assert 'plan task index is invalid JSON' in err


def test_plan_task_artifact_rejects_direct_round_summary_import(tmp_path: Path) -> None:
    project_root = _project_with_plan(tmp_path)
    _make_ready_task(project_root, task_id='task-round-artifact')
    report = project_root / 'rounds' / 'pass.md'
    _write(report, 'round result: pass\n')

    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-artifact',
            '--task',
            'task-round-artifact',
            '--kind',
            'round_summary',
            '--file',
            str(report),
            '--json',
        ],
        cwd=project_root,
    )

    assert code == 1
    assert 'cannot import round_summary directly' in err


def test_plan_task_bind_loop_and_import_round_are_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _project_with_plan(tmp_path)
    monkeypatch.setenv('CCB_CALLER_ACTOR', 'plan_script')
    monkeypatch.setenv('CCB_ACTOR_ROLE', 'ccb.plan')
    monkeypatch.setenv('CCB_JOB_ID', 'job_round123')
    _make_ready_task(project_root, task_id='task-bridge')

    code, payload, _out, err = _run_phase2(
        ['plan', 'task-bind-loop', '--task', 'task-bridge', '--loop', 'loop-a', '--json'],
        cwd=project_root,
    )

    assert code == 0, err
    assert payload['status'] == 'running'
    assert payload['task']['current_loop'] == 'loop-a'
    assert payload['task']['next_owner'] == 'orchestrator'
    assert payload['task']['loop_lease']['status'] == 'active'

    code, retry, _out, err = _run_phase2(
        ['plan', 'task-bind-loop', '--task', 'task-bridge', '--loop', 'loop-a', '--json'],
        cwd=project_root,
    )
    assert code == 0, err
    assert retry['task']['loop_lease']['lease_id'] == payload['task']['loop_lease']['lease_id']

    code, _payload, _out, err = _run_phase2(
        ['plan', 'task-bind-loop', '--task', 'task-bridge', '--loop', 'loop-b', '--json'],
        cwd=project_root,
    )
    assert code == 1
    assert 'already bound to loop: loop-a' in err

    report = project_root / 'rounds' / 'pass.md'
    _write(report, 'round result: pass\n')
    code, imported, _out, err = _run_phase2(
        [
            'plan',
            'task-import-round',
            '--task',
            'task-bridge',
            '--loop',
            'loop-a',
            '--result',
            'pass',
            '--report',
            str(report),
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    assert imported['status'] == 'done'
    assert imported['round_result'] == 'pass'
    assert imported['idempotent'] is False
    assert imported['artifact']['kind'] == 'round_summary'
    assert imported['artifact']['path'].endswith('/round_summary.md')
    assert imported['artifact']['planner_compact_import']['authority'] == 'script_owned_task_import_round'
    assert imported['artifact']['planner_compact_import']['planner_action'] == 'rehydrate_for_brief_or_next_task_planning'
    assert imported['artifact']['sha256']
    assert imported['artifact']['imported_at']
    assert imported['artifact']['actor']['job_id'] == 'job_round123'
    assert imported['legacy_artifact']['kind'] == 'round_pass'
    assert imported['task']['current_loop'] is None
    assert imported['task']['next_owner'] == 'terminal'
    assert imported['task']['round_counters'] == {'pass': 1}

    code, repeated, _out, err = _run_phase2(
        [
            'plan',
            'task-import-round',
            '--task',
            'task-bridge',
            '--loop',
            'loop-a',
            '--result',
            'pass',
            '--report',
            str(report),
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 0, err
    assert repeated['idempotent'] is True
    assert repeated['task']['round_counters'] == {'pass': 1}

    conflicting = project_root / 'rounds' / 'conflicting.md'
    _write(conflicting, 'round result: pass\nchanged\n')
    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-import-round',
            '--task',
            'task-bridge',
            '--loop',
            'loop-a',
            '--result',
            'pass',
            '--report',
            str(conflicting),
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 1
    assert 'conflicts with existing round_summary artifact' in err


@pytest.mark.parametrize(
    ('result', 'expected_status', 'expected_legacy_kind'),
    (
        ('partial', 'partial', 'round_partial'),
        ('replan_required', 'replan_required', 'round_replan'),
        ('blocked', 'blocked', 'round_blocker'),
    ),
)
def test_plan_task_import_round_maps_non_pass_results(
    tmp_path: Path,
    result: str,
    expected_status: str,
    expected_legacy_kind: str,
) -> None:
    project_root = _project_with_plan(tmp_path)
    _make_ready_task(project_root, task_id='task-bridge')

    code, _payload, _out, err = _run_phase2(
        ['plan', 'task-bind-loop', '--task', 'task-bridge', '--loop', 'loop-a', '--json'],
        cwd=project_root,
    )
    assert code == 0, err

    report = project_root / 'rounds' / f'{result}.md'
    _write(report, f'round result: {result}\n')
    code, imported, _out, err = _run_phase2(
        [
            'plan',
            'task-import-round',
            '--task',
            'task-bridge',
            '--loop',
            'loop-a',
            '--result',
            result,
            '--report',
            str(report),
            '--json',
        ],
        cwd=project_root,
    )

    assert code == 0, err
    assert imported['status'] == expected_status
    assert imported['artifact']['kind'] == 'round_summary'
    assert imported['legacy_artifact']['kind'] == expected_legacy_kind
    assert imported['task']['current_loop'] is None
    assert imported['task']['next_owner'] == ('terminal' if result == 'blocked' else 'planner')
    assert imported['task']['last_round']['result'] == result
    assert imported['task']['last_round']['artifact_kind'] == 'round_summary'
    assert imported['task']['last_round']['legacy_artifact_kind'] == expected_legacy_kind
    assert imported['task']['round_counters'] == {result: 1}


def test_plan_task_import_round_rejects_wrong_loop_and_missing_report(tmp_path: Path) -> None:
    project_root = _project_with_plan(tmp_path)
    _make_ready_task(project_root, task_id='task-bridge')

    code, _payload, _out, err = _run_phase2(
        ['plan', 'task-bind-loop', '--task', 'task-bridge', '--loop', 'loop-a', '--json'],
        cwd=project_root,
    )
    assert code == 0, err

    report = project_root / 'rounds' / 'blocked.md'
    _write(report, 'round result: blocked\n')
    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-import-round',
            '--task',
            'task-bridge',
            '--loop',
            'loop-b',
            '--result',
            'blocked',
            '--report',
            str(report),
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 1
    assert 'requires current_loop=loop-b; current_loop is loop-a' in err

    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-import-round',
            '--task',
            'task-bridge',
            '--loop',
            'loop-a',
            '--result',
            'mystery',
            '--report',
            str(report),
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 1
    assert 'unknown round result' in err

    missing = project_root / 'rounds' / 'missing.md'
    code, _payload, _out, err = _run_phase2(
        [
            'plan',
            'task-import-round',
            '--task',
            'task-bridge',
            '--loop',
            'loop-a',
            '--result',
            'blocked',
            '--report',
            str(missing),
            '--json',
        ],
        cwd=project_root,
    )
    assert code == 1
    assert 'plan artifact file not found' in err
