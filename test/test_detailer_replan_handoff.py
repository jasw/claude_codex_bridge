from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.models import (
    AgentRuntime,
    AgentSpec,
    AgentState,
    PermissionMode,
    ProjectConfig,
    QueuePolicy,
    RestoreMode,
    RuntimeMode,
    WorkspaceMode,
)
from ccbd.api_models import DeliveryScope, MessageEnvelope
from ccbd.services.dispatcher import JobDispatcher
from ccbd.services.dispatcher_runtime.detailer_replan_handoff import (
    recover_detailer_replan_handoffs,
    submit_detailer_replan_handoff,
)
import ccbd.services.dispatcher_runtime.detailer_replan_handoff as replan_handoff_module
from ccbd.services.registry import AgentRegistry
from cli.services.plan_tasks import plan_task
from cli.services.plan_tasks import find_first_actionable_task
from cli.services.loop_orchestration_bundle import load_task_orchestration_bundle
from cli.services.role_output_import import _parse_task_detailer_reply, consume_explicit_role_output
from project.ids import compute_project_id
from storage.paths import PathLayout


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return 'sha256:' + hashlib.sha256(encoded).hexdigest()


def _request_body(
    *,
    task_id: str,
    task_revision: int,
    source_job_id: str,
    macro_summary: str = 'Public API and acceptance must change.',
) -> str:
    detail = {
        'summary': 'Source inspection found the accepted task boundary is incorrect.',
        'artifact_refs': ['docs/plantree/plans/demo/tasks/task-a/detail/brief-update-summary.md'],
        'clarification_refs': [],
    }
    macro_impact = {
        'categories': ['public_interface', 'acceptance'],
        'summary': macro_summary,
        'preserved_facts': ['The standard-library constraint remains accepted.'],
        'proposed_changes': ['Planner must revise the command surface and acceptance criteria.'],
        'acceptance_impacts': ['Add the newly required command behavior.'],
        'dependency_impacts': [],
        'roadmap_impacts': ['Re-open the current task before execution.'],
    }
    detail_digest = _canonical_digest(detail)
    macro_impact_digest = _canonical_digest(macro_impact)
    identity = _canonical_digest(
        {'task_id': task_id, 'task_revision': task_revision, 'detail_digest': detail_digest}
    )
    payload = {
        'schema': 'ccb.detailer.replan_request.v1',
        'request_identity': identity,
        'task_id': task_id,
        'task_revision': task_revision,
        'source_detailer_job_id': source_job_id,
        'source_role': 'task_detailer',
        'target_role': 'planner',
        'silence': True,
        'detail': detail,
        'detail_digest': detail_digest,
        'macro_impact': macro_impact,
        'macro_impact_digest': macro_impact_digest,
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def _config() -> ProjectConfig:
    specs = {
        name: AgentSpec(
            name=name,
            provider='fake',
            target='.',
            role=f'agentroles.ccb_{name}' if name == 'task_detailer' else 'agentroles.ccb_planner',
            workspace_mode=WorkspaceMode.GIT_WORKTREE,
            workspace_root=None,
            runtime_mode=RuntimeMode.PANE_BACKED,
            restore_default=RestoreMode.AUTO,
            permission_default=PermissionMode.MANUAL,
            queue_policy=QueuePolicy.SERIAL_PER_AGENT,
        )
        for name in ('task_detailer', 'planner')
    }
    return ProjectConfig(version=2, default_agents=tuple(specs), agents=specs)


def _runtime(name: str, *, project_id: str, layout: PathLayout, pid: int) -> AgentRuntime:
    return AgentRuntime(
        agent_name=name,
        state=AgentState.IDLE,
        pid=pid,
        started_at='2026-07-12T00:00:00Z',
        last_seen_at='2026-07-12T00:00:00Z',
        runtime_ref=f'{name}-runtime',
        session_ref=f'{name}-session',
        workspace_path=str(layout.workspace_path(name)),
        project_id=project_id,
        backend_type='tmux',
        queue_depth=0,
        socket_path=None,
        health='healthy',
    )


def _context(project_root: Path):
    return SimpleNamespace(
        project=SimpleNamespace(
            project_root=project_root,
            project_id=compute_project_id(project_root),
        )
    )


def _ready_task(project_root: Path) -> None:
    plan_root = project_root / 'docs' / 'plantree' / 'plans' / 'demo'
    plan_root.mkdir(parents=True)
    context = _context(project_root)
    created = plan_task(
        context,
        SimpleNamespace(action='task-create', plan_slug='demo', title='Task A', task_id='task-a'),
    )
    revision = created['task']['task_revision']
    for kind, content in (
        ('task_packet', '# Task A\n\nImplement the accepted behavior.\n'),
        ('execution_contract', '# Contract\n\nVerify the accepted behavior.\n'),
    ):
        path = project_root / f'{kind}.md'
        path.write_text(content, encoding='utf-8')
        imported = plan_task(
            context,
            SimpleNamespace(
                action='task-artifact',
                task_id='task-a',
                artifact_kind=kind,
                file_path=str(path),
                actor_source='test',
                actor='loop_runner',
                job_id='job-seed',
                expected_task_revision=revision,
                route=None,
            ),
        )
        revision = imported['task']['task_revision']
    plan_task(
        context,
        SimpleNamespace(
            action='task-status',
            task_id='task-a',
            status='ready_for_orchestration',
            next_owner='orchestrator',
            activation_reason='test-ready',
            expected_task_revision=revision,
        ),
    )
    index_path = plan_root / 'tasks' / 'index.json'
    index = json.loads(index_path.read_text(encoding='utf-8'))
    task = index['tasks'][0]
    bundle_path = plan_root / 'tasks' / 'task-a' / 'orchestration_bundle.json'
    bundle_path.write_text('{}\n', encoding='utf-8')
    task['artifacts']['orchestration_notes'] = {
        'path': 'docs/plantree/plans/demo/tasks/task-a/orchestration_notes.md',
        'sha256': 'old-notes',
        'task_revision': 1,
    }
    task['artifacts']['orchestration_bundle'] = {
        'path': 'docs/plantree/plans/demo/tasks/task-a/orchestration_bundle.json',
        'sha256': hashlib.sha256(b'{}\n').hexdigest(),
        'task_revision': 1,
        'bundle_revision': 1,
    }
    index_path.write_text(json.dumps(index, indent=2) + '\n', encoding='utf-8')


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project_root = tmp_path / 'repo'
    (project_root / '.ccb').mkdir(parents=True)
    (project_root / '.ccb' / 'ccb.config').write_text('task_detailer:fake; planner:fake\n', encoding='utf-8')
    _ready_task(project_root)
    layout = PathLayout(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('task_detailer', project_id=layout.project_id, layout=layout, pid=101))
    registry.upsert(_runtime('planner', project_id=layout.project_id, layout=layout, pid=102))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: '2026-07-12T00:00:00Z')
    source = dispatcher.submit(
        MessageEnvelope(
            project_id=layout.project_id,
            to_agent='task_detailer',
            from_actor='system',
            body='Refine task-a.',
            task_id='act-detailer-task-a',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    dispatcher.tick()
    source_job_id = source.jobs[0].job_id
    activation_path = project_root / '.ccb' / 'runtime' / 'loops' / 'activations' / 'act-detailer-task-a.json'
    activation_path.parent.mkdir(parents=True)
    activation_path.write_text(
        json.dumps(
            {
                'record_type': 'ccb_loop_managed_activation',
                'activation_id': 'act-detailer-task-a',
                'target': 'task_detailer',
                'task_id': 'task-a',
                'task_revision': 1,
                'ask': {'target': 'task_detailer', 'job_id': source_job_id},
            }
        )
        + '\n',
        encoding='utf-8',
    )
    runner_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        'cli.services.frontdesk_intake._start_auto_runner',
        lambda context, *, activation_id, wait_job_id: runner_calls.append((activation_id, wait_job_id))
        or {'status': 'started'},
    )
    return project_root, dispatcher, source_job_id, runner_calls


def _envelope(dispatcher, source_job_id: str, *, body: str | None = None, silence: bool = True, target: str = 'planner'):
    request_body = body or _request_body(task_id='task-a', task_revision=1, source_job_id=source_job_id)
    identity = json.loads(request_body)['request_identity'].removeprefix('sha256:')
    return MessageEnvelope(
        project_id=dispatcher._layout.project_id,
        to_agent=target,
        from_actor='task_detailer',
        body=request_body,
        task_id=f'detailer-replan-{identity[:32]}',
        reply_to=None,
        message_type='ask',
        delivery_scope=DeliveryScope.SINGLE,
        silence_on_success=silence,
    )


def test_detailer_replan_is_exact_once_and_fences_task_authority(tmp_path: Path, monkeypatch) -> None:
    project_root, dispatcher, source_job_id, runner_calls = _setup(tmp_path, monkeypatch)
    request = _envelope(dispatcher, source_job_id)

    first = dispatcher.submit(request)
    second = dispatcher.submit(request)

    assert second.jobs[0].job_id == first.jobs[0].job_id
    assert len({job.job_id for job in dispatcher._job_store.list_agent('planner')}) == 1
    shown = plan_task(_context(project_root), SimpleNamespace(action='task-show', task_id='task-a'))['task']
    assert shown['status'] == 'replan_required'
    assert shown['next_owner'] == 'planner'
    assert shown['task_revision'] == 2
    assert shown['replan_feedback']['source_detailer_job_id'] == source_job_id
    assert shown['replan_feedback']['planner_job_id'] == first.jobs[0].job_id
    assert shown['replan_feedback']['request_identity'].startswith('sha256:')
    assert shown['artifacts']['orchestration_notes']['authority_status'] == 'superseded'
    assert shown['artifacts']['orchestration_bundle']['authority_status'] == 'superseded'
    with pytest.raises(ValueError, match='superseded.*cannot dispatch workers'):
        load_task_orchestration_bundle(project_root, shown, capacity_snapshot={})
    actionable = find_first_actionable_task(_context(project_root), task_id='task-a')
    assert actionable is not None
    assert actionable['runner_action'] == 'activate_planner'
    intent = json.loads(
        next((project_root / '.ccb' / 'runtime' / 'detailer-replan').glob('*.json')).read_text(encoding='utf-8')
    )
    assert intent['status'] == 'planner_submitted'
    assert 'timeout' not in intent
    assert 'deadline' not in intent
    assert runner_calls == [
        (f'act-detailer-replan-{json.loads(request.body)["request_identity"][7:39]}', first.jobs[0].job_id),
        (f'act-detailer-replan-{json.loads(request.body)["request_identity"][7:39]}', first.jobs[0].job_id),
    ]


def test_detailer_replan_conflict_and_negative_shapes_do_not_submit(tmp_path: Path, monkeypatch) -> None:
    _project_root, dispatcher, source_job_id, _runner_calls = _setup(tmp_path, monkeypatch)
    request = _envelope(dispatcher, source_job_id)
    dispatcher.submit(request)

    conflicting_body = _request_body(
        task_id='task-a',
        task_revision=1,
        source_job_id=source_job_id,
        macro_summary='A different macro impact under the same request identity.',
    )
    with pytest.raises(RuntimeError, match='request identity conflict'):
        dispatcher.submit(_envelope(dispatcher, source_job_id, body=conflicting_body))
    bad_digest = json.loads(request.body)
    bad_digest['detail']['summary'] = 'Tampered detail evidence.'
    with pytest.raises(RuntimeError, match='detail_digest does not match'):
        dispatcher.submit(
            _envelope(
                dispatcher,
                source_job_id,
                body=json.dumps(bad_digest, sort_keys=True, separators=(',', ':')),
            )
        )
    for invalid in (
        _envelope(dispatcher, source_job_id, silence=False),
        _envelope(dispatcher, source_job_id, target='task_detailer'),
        MessageEnvelope(
            project_id=request.project_id,
            to_agent='planner',
            from_actor='task_detailer',
            body=request.body,
            task_id=request.task_id,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
            silence_on_success=True,
            route_options={'chain': True},
        ),
    ):
        with pytest.raises(RuntimeError):
            dispatcher.submit(invalid)
    assert len({job.job_id for job in dispatcher._job_store.list_agent('planner')}) == 1


def test_valid_revised_planner_authority_reopens_fresh_orchestrator(tmp_path: Path, monkeypatch) -> None:
    project_root, dispatcher, source_job_id, _runner_calls = _setup(tmp_path, monkeypatch)
    planner = dispatcher.submit(_envelope(dispatcher, source_job_id))
    context = _context(project_root)
    planner_job_id = planner.jobs[0].job_id
    snapshot_path = project_root / '.ccb' / 'ccbd' / 'snapshots' / f'{planner_job_id}.json'
    snapshot_path.write_text(
        json.dumps(
            {
                'job_id': planner_job_id,
                'agent_name': 'planner',
                'state': {'terminal': True},
                'latest_decision': {
                    'terminal': True,
                    'status': 'completed',
                    'reply': '''**task-packet.md**
```markdown
# Task: Revised Task A
Route: direct_execution
## Goal
Use the corrected public interface.
## Acceptance Criteria
- The corrected behavior is verified.
Allowed paths:
- src/
Verification:
- python -m unittest
```

**readiness.json**
```json
{"readiness":"ready","route":"direct_execution","blockers":[],"allowed_paths":["src/"],"verification":["python -m unittest"]}
```
''',
                },
            }
        )
        + '\n',
        encoding='utf-8',
    )
    imported = consume_explicit_role_output(
        context,
        SimpleNamespace(role_job_id=planner_job_id, task_id='task-a'),
        services=SimpleNamespace(plan_task=plan_task),
    )
    assert imported['action'] == 'imported_planner_task_authority'
    assert imported['task_status'] == 'ready_for_orchestration'
    ready = plan_task(context, SimpleNamespace(action='task-show', task_id='task-a'))
    assert ready['task']['task_revision'] > 2
    assert ready['status'] == 'ready_for_orchestration'
    actionable = find_first_actionable_task(context, task_id='task-a')
    assert actionable is not None
    assert actionable['runner_action'] == 'activate_orchestrator'


def test_task_detailer_replan_reply_settles_only_against_accepted_direct_intent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, dispatcher, source_job_id, _runner_calls = _setup(tmp_path, monkeypatch)
    planner = dispatcher.submit(_envelope(dispatcher, source_job_id))
    snapshot_path = project_root / '.ccb' / 'ccbd' / 'snapshots' / f'{source_job_id}.json'
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(
            {
                'job_id': source_job_id,
                'agent_name': 'task_detailer',
                'state': {'terminal': True},
                'latest_decision': {
                    'terminal': True,
                    'status': 'completed',
                    'reply': '''## task-detail-design.md
Source evidence proves the public interface must change.

## brief-update-summary.md
Global impact: macro. Planner replan request submitted.

detail-packet.manifest.json:
```json
{
  "schema": "ccb.detail_packet_manifest.v1",
  "detail_result": "planner_replan_required",
  "readiness": "planner_replan_required",
  "global_impact": "macro"
}
```
''',
                },
            }
        )
        + '\n',
        encoding='utf-8',
    )

    imported = consume_explicit_role_output(
        _context(project_root),
        SimpleNamespace(role_job_id=source_job_id, task_id='task-a'),
        services=SimpleNamespace(plan_task=plan_task),
    )

    assert imported['action'] == 'imported_task_detailer_replan_feedback', imported
    assert imported['task_status'] == 'replan_required'
    assert imported['next_owner'] == 'planner'
    assert imported['planner_job_id'] == planner.jobs[0].job_id
    assert imported['next_activation'] == 'planner'


@pytest.mark.parametrize(
    'reply',
    (
        'Detail readiness recommendation: planner_replan_required\n\n## detail-packet.md\nLegacy packet.',
        '''## task-detail-design.md
Design.

## brief-update-summary.md
Summary.

detail-packet.manifest.json:
```json
{"schema":"wrong","detail_result":"planner_replan_required","readiness":"planner_replan_required","global_impact":"macro"}
```''',
    ),
)
def test_stale_detailer_replan_requires_strict_manifest_before_bypass(
    tmp_path: Path, monkeypatch, reply: str,
) -> None:
    project_root, dispatcher, source_job_id, _runner_calls = _setup(tmp_path, monkeypatch)
    dispatcher.submit(_envelope(dispatcher, source_job_id))
    before = plan_task(_context(project_root), SimpleNamespace(action='task-show', task_id='task-a'))['task']
    snapshot = project_root / '.ccb' / 'ccbd' / 'snapshots' / f'{source_job_id}.json'
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps({'job_id': source_job_id, 'agent_name': 'task_detailer', 'state': {'terminal': True}, 'latest_decision': {'terminal': True, 'status': 'completed', 'reply': reply}}) + '\n', encoding='utf-8')

    blocked = consume_explicit_role_output(_context(project_root), SimpleNamespace(role_job_id=source_job_id, task_id='task-a'), services=SimpleNamespace(plan_task=plan_task))

    assert blocked['action'] == 'role_output_import_blocked'
    assert blocked['reason'] == 'stale_managed_activation_task_revision'
    after = plan_task(_context(project_root), SimpleNamespace(action='task-show', task_id='task-a'))['task']
    assert {key: after.get(key) for key in ('task_revision', 'status', 'replan_feedback')} == {key: before.get(key) for key in ('task_revision', 'status', 'replan_feedback')}


def test_canonical_detailer_replan_without_accepted_intent_cannot_import(tmp_path: Path, monkeypatch) -> None:
    project_root, _dispatcher, source_job_id, _runner_calls = _setup(tmp_path, monkeypatch)
    before = plan_task(_context(project_root), SimpleNamespace(action='task-show', task_id='task-a'))['task']
    snapshot = project_root / '.ccb' / 'ccbd' / 'snapshots' / f'{source_job_id}.json'
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps({'job_id': source_job_id, 'agent_name': 'task_detailer', 'state': {'terminal': True}, 'latest_decision': {'terminal': True, 'status': 'completed', 'reply': '''## task-detail-design.md
Design.

## brief-update-summary.md
Summary.

detail-packet.manifest.json:
```json
{"schema":"ccb.detail_packet_manifest.v1","detail_result":"planner_replan_required","readiness":"planner_replan_required","global_impact":"macro"}
```'''}}) + '\n', encoding='utf-8')

    blocked = consume_explicit_role_output(_context(project_root), SimpleNamespace(role_job_id=source_job_id, task_id='task-a'), services=SimpleNamespace(plan_task=plan_task))

    assert blocked['action'] == 'role_output_import_blocked'
    assert blocked['reason'] == 'task_detailer_planner_replan_direct_handoff_missing'
    after = plan_task(_context(project_root), SimpleNamespace(action='task-show', task_id='task-a'))['task']
    assert {key: after.get(key) for key in ('task_revision', 'status', 'replan_feedback')} == {key: before.get(key) for key in ('task_revision', 'status', 'replan_feedback')}


def test_detailer_replan_pre_submit_and_runner_start_crashes_recover_one_job(tmp_path: Path, monkeypatch) -> None:
    project_root, dispatcher, source_job_id, _runner_calls = _setup(tmp_path, monkeypatch)
    request = _envelope(dispatcher, source_job_id)

    with pytest.raises(RuntimeError, match='submit crash'):
        submit_detailer_replan_handoff(
            dispatcher,
            request,
            accepted_at='2026-07-12T00:00:00Z',
            submit=lambda: (_ for _ in ()).throw(RuntimeError('submit crash')),
        )
    shown = plan_task(_context(project_root), SimpleNamespace(action='task-show', task_id='task-a'))['task']
    assert shown['status'] == 'replan_required'
    assert shown['task_revision'] == 2
    recovered = dispatcher.submit(request)
    planner_job_id = recovered.jobs[0].job_id
    assert len({job.job_id for job in dispatcher._job_store.list_agent('planner')}) == 1
    dispatcher.cancel(source_job_id)
    activation = next(
        (project_root / '.ccb' / 'runtime' / 'loops' / 'activations').glob('act-detailer-replan-*.json')
    )
    activation.unlink()
    recovered_jobs = recover_detailer_replan_handoffs(dispatcher)
    assert recovered_jobs == (planner_job_id,)
    assert activation.is_file()
    assert len({job.job_id for job in dispatcher._job_store.list_agent('planner')}) == 1


def test_detailer_replan_runner_start_retry_reuses_persisted_planner_job(tmp_path: Path, monkeypatch) -> None:
    project_root, dispatcher, source_job_id, _runner_calls = _setup(tmp_path, monkeypatch)
    calls: list[str] = []

    def flaky_runner(context, *, activation_id, wait_job_id):
        del context, activation_id
        calls.append(wait_job_id)
        if len(calls) == 1:
            raise RuntimeError('runner start crash')
        return {'status': 'started'}

    monkeypatch.setattr('cli.services.frontdesk_intake._start_auto_runner', flaky_runner)
    request = _envelope(dispatcher, source_job_id)
    with pytest.raises(RuntimeError, match='runner start crash'):
        dispatcher.submit(request)
    jobs = {job.job_id for job in dispatcher._job_store.list_agent('planner')}
    assert len(jobs) == 1
    intent = next((project_root / '.ccb' / 'runtime' / 'detailer-replan').glob('*.json'))
    assert json.loads(intent.read_text(encoding='utf-8'))['status'] == 'planner_submitted_runner_start_failed'

    recovered = dispatcher.submit(request)
    assert recovered.jobs[0].job_id in jobs
    assert calls == [recovered.jobs[0].job_id, recovered.jobs[0].job_id]
    assert len({job.job_id for job in dispatcher._job_store.list_agent('planner')}) == 1


def test_detailer_replan_post_submit_append_crash_recovers_persisted_job(tmp_path: Path, monkeypatch) -> None:
    _project_root, dispatcher, source_job_id, _runner_calls = _setup(tmp_path, monkeypatch)
    original_finalize = replan_handoff_module._finalize
    calls = 0

    def flaky_finalize(current_dispatcher, prepared, job):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('intent append crash')
        return original_finalize(current_dispatcher, prepared, job)

    monkeypatch.setattr(replan_handoff_module, '_finalize', flaky_finalize)
    request = _envelope(dispatcher, source_job_id)
    with pytest.raises(RuntimeError, match='intent append crash'):
        dispatcher.submit(request)
    jobs = {job.job_id for job in dispatcher._job_store.list_agent('planner')}
    assert len(jobs) == 1

    recovered = dispatcher.submit(request)
    assert recovered.jobs[0].job_id in jobs
    assert calls == 2
    assert len({job.job_id for job in dispatcher._job_store.list_agent('planner')}) == 1


def test_detailer_replan_rejects_stale_revision_and_unrelated_source_job(tmp_path: Path, monkeypatch) -> None:
    _project_root, dispatcher, source_job_id, _runner_calls = _setup(tmp_path, monkeypatch)
    stale_body = _request_body(task_id='task-a', task_revision=2, source_job_id=source_job_id)
    with pytest.raises(RuntimeError, match='stale detailer replan task revision'):
        dispatcher.submit(_envelope(dispatcher, source_job_id, body=stale_body))

    unrelated = dispatcher.submit(
        MessageEnvelope(
            project_id=dispatcher._layout.project_id,
            to_agent='planner',
            from_actor='system',
            body='Unrelated planner request.',
            task_id='unrelated',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    bad_body = _request_body(
        task_id='task-a',
        task_revision=1,
        source_job_id=unrelated.jobs[0].job_id,
    )
    with pytest.raises(RuntimeError, match='source Detailer job'):
        dispatcher.submit(_envelope(dispatcher, source_job_id, body=bad_body))


@pytest.mark.parametrize(
    ('result', 'readiness', 'impact', 'expected_status'),
    (
        ('local_detail_ready', 'detail_ready', 'none', 'ok'),
        ('planner_replan_required', 'planner_replan_required', 'macro', 'ok'),
        ('needs_clarification', 'needs_clarification', 'bounded', 'blocked'),
        ('blocked', 'blocked', 'none', 'blocked'),
    ),
)
def test_task_detailer_result_contract_distinguishes_all_four_results(
    result: str,
    readiness: str,
    impact: str,
    expected_status: str,
) -> None:
    reply = f'''## task-detail-design.md
Source-backed detail design.

## brief-update-summary.md
Global impact: {impact}.

detail-packet.manifest.json:
```json
{{
  "schema": "ccb.detail_packet_manifest.v1",
  "detail_result": "{result}",
  "readiness": "{readiness}",
  "global_impact": "{impact}"
}}
```
'''

    parsed = _parse_task_detailer_reply(reply)

    assert parsed['status'] == expected_status
    if expected_status == 'ok':
        assert parsed['result'] == result
    else:
        assert parsed['readiness'] == readiness


def test_task_detailer_rolepack_template_is_a_terminal_canonical_manifest() -> None:
    template = (
        Path(__file__).resolve().parents[1]
        / 'docs/plantree/plans/agentic-loop-workflow/drafts/agentroles.ccb_task_detailer/templates/detail-packet.md'
    ).read_text(encoding='utf-8')

    parsed = _parse_task_detailer_reply(template)

    assert parsed['status'] == 'ok'
    assert parsed['result'] == 'local_detail_ready'
    assert parsed['readiness'] == 'detail_ready'
    manifest = json.loads(str(parsed['detail_packet']))
    assert manifest['schema'] == 'ccb.detail_packet_manifest.v1'
    assert manifest['global_impact'] == 'none'
    assert template.rstrip().endswith('```')


@pytest.mark.parametrize(
    'manifest',
    (
        '```markdown\n# Detail Packet\n```',
        '```ccb.detail_packet_manifest.v1\n{}\n```',
        '```json\n{"schema": "wrong"}\n```',
        '```json\n{"schema": "ccb.detail_packet_manifest.v1", "detail_result": "local_detail_ready", "readiness": "blocked", "global_impact": "none"}\n```',
        '```json\n{"schema": "ccb.detail_packet_manifest.v1", "detail_result": "local_detail_ready", "readiness": "detail_ready", "global_impact": "none"}\n```\n\n## detail-packet.md\nMarkdown fallback.',
    ),
)
def test_task_detailer_rejects_noncanonical_manifest(manifest: str) -> None:
    reply = f'''## task-detail-design.md
Design.

## brief-update-summary.md
Summary.

detail-packet.manifest.json:
{manifest}
'''

    assert _parse_task_detailer_reply(reply)['status'] == 'blocked'
