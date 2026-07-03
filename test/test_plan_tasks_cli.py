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
    for name in ('requirements', 'acceptance', 'verification', 'handoff', 'review'):
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
    for kind in ('requirements', 'acceptance', 'verification', 'handoff', 'review'):
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
        ['plan', 'task-status', '--task', task_id, '--status', 'ready', '--json'],
        cwd=project_root,
    )
    assert code == 0, err
    assert payload['status'] == 'ready'


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
        ['plan', 'task-artifact', '--task', 'task-001', '--kind', 'requirements', '--file', 'drafts/req.md', '--json']
    ) == ParsedPlanTaskCommand(
        project=None,
        action='task-artifact',
        task_id='task-001',
        artifact_kind='requirements',
        file_path='drafts/req.md',
        json_output=True,
    )
    assert parser.parse(
        ['plan', 'task-status', '--task', 'task-001', '--status', 'ready', '--json']
    ) == ParsedPlanTaskCommand(project=None, action='task-status', task_id='task-001', status='ready', json_output=True)
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


def test_plan_task_artifact_imports_plan_brief_and_task_detail_docs(tmp_path: Path) -> None:
    project_root = _project_with_plan(tmp_path)
    drafts = project_root / 'drafts'
    _write(drafts / 'brief.md', '# Brief\n\nPlanner-owned macro summary.\n')
    _write(drafts / 'detail-design.md', '# Detail Design\n\nTask-scoped detail body.\n')
    _write(drafts / 'detail-summary.md', '# Detail Summary\n\nStable summary backfill.\n')
    _write(
        drafts / 'detail-packet.json',
        '{"schema":"ccb.loop.detail_packet_manifest/v1","status":"ready_for_review"}\n',
    )
    _write(
        drafts / 'macro-adjustment-request.json',
        '{"schema":"ccb.loop.macro_adjustment_request/v1","reason":"macro assumption changed"}\n',
    )

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
        ('macro_adjustment_request', 'macro-adjustment-request.json'),
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
    assert artifacts['detail_packet']['path'].endswith('/tasks/task-detail/details/detail-packet.manifest.json')
    assert artifacts['macro_adjustment_request']['path'].endswith('/tasks/task-detail/details/macro-adjustment-request.json')
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


def test_plan_task_bind_loop_and_import_round_are_idempotent(tmp_path: Path) -> None:
    project_root = _project_with_plan(tmp_path)
    _make_ready_task(project_root, task_id='task-bridge')

    code, payload, _out, err = _run_phase2(
        ['plan', 'task-bind-loop', '--task', 'task-bridge', '--loop', 'loop-a', '--json'],
        cwd=project_root,
    )

    assert code == 0, err
    assert payload['status'] == 'running'
    assert payload['task']['current_loop'] == 'loop-a'
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
    assert imported['artifact']['kind'] == 'round_pass'
    assert imported['task']['current_loop'] is None
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
    assert 'conflicts with existing round_pass artifact' in err


@pytest.mark.parametrize(
    ('result', 'expected_status', 'expected_kind'),
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
    expected_kind: str,
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
    assert imported['artifact']['kind'] == expected_kind
    assert imported['task']['current_loop'] is None
    assert imported['task']['last_round']['result'] == result
    assert imported['task']['last_round']['artifact_kind'] == expected_kind
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
