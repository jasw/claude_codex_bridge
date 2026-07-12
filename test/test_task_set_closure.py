from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope
from jobs.store import JobStore
from storage.paths import PathLayout
from cli.services.plan_tasks import find_first_actionable_task, plan_task, settle_task_set_parent
from cli.services.planner_feedback import (
    frontdesk_status_envelope,
    parse_planner_feedback_reply,
    planner_feedback_digest,
)
from cli.services.planner_feedback_apply import apply_planner_feedback
from cli.services.task_set_feedback_runtime import (
    _frontdesk_message,
    _planner_message,
    _prepared_transport,
    _runtime_digest,
)
from cli.services.task_set_closure import (
    create_task_set_authority,
    evaluate_task_set_closure,
    find_pending_task_set_closures,
    revise_task_set_authority,
    settle_task_set_closure_feedback,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _context(tmp_path: Path):
    root = tmp_path / 'project'
    _write(root / 'docs/plantree/plans/demo/README.md', '# Demo Plan\n')
    return SimpleNamespace(
        project=SimpleNamespace(project_root=root, project_id='project-task-set'),
    )


def _create_task(context, task_id: str, *, ready: bool = True) -> dict[str, object]:
    created = plan_task(
        context,
        SimpleNamespace(
            action='task-create',
            plan_slug='demo',
            title=task_id,
            task_id=task_id,
        ),
    )
    if not ready:
        return created['task']
    drafts = Path(context.project.project_root) / 'drafts' / task_id
    revision = created['task']['task_revision']
    for kind in ('task_packet', 'execution_contract'):
        path = drafts / f'{kind}.md'
        _write(path, f'# {kind}\nTask: {task_id}\n')
        imported = plan_task(
            context,
            SimpleNamespace(
                action='task-artifact',
                task_id=task_id,
                artifact_kind=kind,
                file_path=str(path),
                expected_task_revision=revision,
            ),
        )
        revision = imported['task']['task_revision']
    ready_payload = plan_task(
        context,
        SimpleNamespace(
            action='task-status',
            task_id=task_id,
            status='ready_for_orchestration',
            next_owner='orchestrator',
            expected_task_revision=revision,
        ),
    )
    return ready_payload['task']


def _create_set(context, child_specs: list[tuple[str, bool]]) -> dict[str, object]:
    _create_task(context, 'source-intake', ready=False)
    for task_id, _required in child_specs:
        _create_task(context, task_id)
    return create_task_set_authority(
        context,
        plan_slug='demo',
        source_task_id='source-intake',
        source_request={
            'source_job_id': 'job-frontdesk',
            'sha256': hashlib.sha256(b'user request').hexdigest(),
            'bytes': len(b'user request'),
        },
        planner_job={
            'job_id': 'job-planner',
            'reply_sha256': hashlib.sha256(b'planner reply').hexdigest(),
        },
        children=[
            {'task_id': task_id, 'required': required}
            for task_id, required in child_specs
        ],
        plan_task_fn=plan_task,
    )


def _complete(
    context,
    task_id: str,
    result: str,
    *,
    release_clean: bool = True,
    cleanup_complete: bool = True,
    multi: bool = False,
) -> None:
    shown = plan_task(context, SimpleNamespace(action='task-show', task_id=task_id))
    revision = shown['task']['task_revision']
    loop_id = f'loop-{task_id}'
    plan_task(
        context,
        SimpleNamespace(
            action='task-bind-loop',
            task_id=task_id,
            loop_id=loop_id,
            expected_task_revision=revision,
        ),
    )
    summary = Path(context.project.project_root) / 'rounds' / f'{task_id}.md'
    _write(summary, f'round result: {result}\n')
    plan_task(
        context,
        SimpleNamespace(
            action='task-import-round',
            task_id=task_id,
            loop_id=loop_id,
            result=result,
            file_path=str(summary),
            expected_task_revision=revision,
        ),
    )
    release = {
        'loop_topology_status': 'released' if release_clean else 'release_incomplete',
        'released_count': 2 if release_clean else 0,
        'retained_count': 0 if release_clean else 1,
        'release_incomplete_count': 0 if release_clean else 1,
    }
    round_record: dict[str, object] = {
        'schema': 'ccb.loop.round_state.v1',
        'task_id': task_id,
        'loop_id': loop_id,
        'round_result': result,
        'dispatch_source': 'multi_workgroup_scheduler' if multi else 'ask_first_direct_execution',
        'release': release,
    }
    if multi:
        round_record['cleanup'] = (
            {'result': {'status': 'complete'}}
            if cleanup_complete
            else {'readiness': {'eligible': False}}
        )
    path = Path(context.project.project_root) / '.ccb/runtime/loops' / loop_id / 'round.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(round_record, sort_keys=True) + '\n', encoding='utf-8')


def _planner_proposal(
    task_set_id: str,
    closure: dict[str, object],
    plan_revision: str,
    *,
    notification_required: bool = False,
):
    evidence = [f'docs/plantree/plans/demo/task-sets/{task_set_id}/closure.json']
    next_milestone = {'kind': 'workflow_terminal', 'ref': 'done', 'rationale': 'Done.'}
    payload = {
        'schema': 'ccb.planner.backfill_proposal.v1',
        'mode': 'task_set_closure',
        'expected_plan_revision': plan_revision,
        'task_or_task_set_id': task_set_id,
        'task_or_task_set_revision': 1,
        'closure_evidence_digest': closure['ordered_terminal_evidence_digest'],
        'aggregate_result': 'pass',
        'result': 'closure_complete',
        'brief_summary': 'Closed.',
        'roadmap_transitions': [],
        'todo_transitions': [],
        'decision_refs': [],
        'open_question_refs': [],
        'evidence_refs': evidence,
        'accepted_scope': ['landed'],
        'unresolved_scope': [],
        'blockers': [],
        'replan_inputs': [],
        'next_milestone': next_milestone,
        'frontdesk_notification_required': notification_required,
        'frontdesk_status': {
            'schema': 'ccb.planner.frontdesk_status.v1',
            'notification_identity': f'{task_set_id}-r1',
            'aggregate_result': 'pass',
            'accepted_scope': ['landed'],
            'unresolved_scope': [],
            'blockers': [],
            'next_milestone': next_milestone,
            'evidence_refs': evidence,
            'user_report_body': 'Done.',
        },
    }
    reply = '**planner-backfill.json**\n```json\n' + json.dumps(payload) + '\n```\n'
    return parse_planner_feedback_reply(reply)


def _completed_job(context, *, target: str, job_id: str, task_id: str, message: str) -> None:
    request = MessageEnvelope(
        project_id=context.project.project_id,
        to_agent=target,
        from_actor='system',
        body=message,
        task_id=task_id,
        reply_to=None,
        message_type='task',
        delivery_scope=DeliveryScope.SINGLE,
        silence_on_success=target == 'planner',
    )
    JobStore(PathLayout(context.project.project_root)).append(JobRecord(
        job_id=job_id,
        submission_id=None,
        agent_name=target,
        provider='codex',
        request=request,
        status=JobStatus.COMPLETED,
        terminal_decision={'terminal': True, 'status': 'completed'},
        cancel_requested_at=None,
        created_at='2026-07-12T00:00:00Z',
        updated_at='2026-07-12T00:00:01Z',
    ))


def _settlement_fixture(context, *, notification_required: bool = False):
    created = _create_set(context, [('child-a', True)])
    task_set_id = created['task_set']['task_set_id']
    _complete(context, 'child-a', 'pass')
    closed = evaluate_task_set_closure(context, task_set_id=task_set_id, plan_task_fn=plan_task)
    intent = closed['closure_intent']
    root = Path(context.project.project_root)
    task_set_root = root / 'docs/plantree/plans/demo/task-sets' / task_set_id
    task_set = json.loads((task_set_root / 'task-set.json').read_text(encoding='utf-8'))
    proposal = _planner_proposal(
        task_set_id,
        closed['closure'],
        task_set['plan_revision']['digest'],
        notification_required=notification_required,
    )
    feedback_digest = planner_feedback_digest(proposal)
    planner_job_id = 'job_planner'
    imported = apply_planner_feedback(context, proposal, {
        'task_set_id': task_set_id,
        'task_set_revision': 1,
        'closure_intent_id': intent['intent_id'],
        'closure_digest': closed['closure']['closure_digest'],
        'ordered_terminal_evidence_digest': intent['ordered_terminal_evidence_digest'],
        'expected_plan_revision': task_set['plan_revision']['digest'],
        'planner_job_id': planner_job_id,
        'planner_feedback_digest': feedback_digest,
        'plan_slug': 'demo',
    })
    planner_message = _planner_message(closed['closure'], intent)
    planner = _prepared_transport(
        target='planner', purpose='planner_backfill',
        task_id=f'task-set-feedback-{intent["intent_id"]}',
        message=planner_message, silent=True,
    )
    proposal_reply = '**planner-backfill.json**\n```json\n' + json.dumps(proposal.to_record()) + '\n```\n'
    planner.update({'job_id': planner_job_id, 'status': 'completed', 'reply': proposal_reply})
    _completed_job(
        context, target='planner', job_id=planner_job_id,
        task_id=str(planner['task_id']), message=planner_message,
    )
    frontdesk = None
    notification = {'status': 'notification_not_required'}
    frontdesk_job_id = None
    if notification_required:
        frontdesk_job_id = 'job_frontdesk'
        frontdesk_message = _frontdesk_message(frontdesk_status_envelope(proposal))
        frontdesk = _prepared_transport(
            target='frontdesk', purpose='frontdesk_status',
            task_id=f'task-set-status-{intent["intent_id"]}',
            message=frontdesk_message, silent=False,
        )
        frontdesk.update({'job_id': frontdesk_job_id, 'status': 'completed', 'reply': 'delivered'})
        _completed_job(
            context, target='frontdesk', job_id=frontdesk_job_id,
            task_id=str(frontdesk['task_id']), message=frontdesk_message,
        )
        notification = {'status': 'delivered', 'job_id': frontdesk_job_id}
    runtime_path = root / '.ccb/runtime/task-sets' / task_set_id / 'feedback-r1.json'
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime = {
        'schema': 'ccb.plan.task_set_feedback_runtime.v1',
        'schema_version': 1,
        'task_set_id': task_set_id,
        'task_set_revision': 1,
        'closure_intent_id': intent['intent_id'],
        'closure_digest': closed['closure']['closure_digest'],
        'terminal_evidence_digest': intent['ordered_terminal_evidence_digest'],
        'stage': 'closed',
        'planner': planner,
        'frontdesk': frontdesk,
        'backfill_import': {
            'task_set_id': task_set_id,
            'task_set_revision': 1,
            'closure_intent_id': intent['intent_id'],
            'closure_digest': closed['closure']['closure_digest'],
            'ordered_terminal_evidence_digest': intent['ordered_terminal_evidence_digest'],
            'expected_plan_revision': task_set['plan_revision']['digest'],
            'planner_job_id': planner_job_id,
            'planner_feedback_digest': feedback_digest,
            'plan_slug': 'demo',
            **imported,
        },
        'notification': notification,
    }
    runtime['runtime_digest'] = _runtime_digest(runtime)
    runtime_path.write_text(json.dumps(runtime), encoding='utf-8')
    authority = {
        'task_set_id': task_set_id,
        'task_set_revision': 1,
        'intent_id': intent['intent_id'],
        'ordered_terminal_evidence_digest': intent['ordered_terminal_evidence_digest'],
        'transport_ref': {
            'runtime_state_path': str(runtime_path),
            'planner_job_id': planner_job_id,
            'frontdesk_job_id': frontdesk_job_id,
            'planner_backfill_path': imported['planner_backfill_path'],
            'planner_feedback_digest': feedback_digest,
            'notification_status': notification['status'],
            'backfill_digest': imported['backfill_digest'],
        },
    }
    if frontdesk_job_id is None:
        authority['transport_ref'].pop('frontdesk_job_id')
    return authority, runtime_path, Path(imported['planner_backfill_path'])


def test_task_set_parent_is_decomposed_and_children_are_revision_bound(tmp_path: Path) -> None:
    context = _context(tmp_path)

    created = _create_set(context, [('child-a', True), ('child-b', False)])

    parent = plan_task(context, SimpleNamespace(action='task-show', task_id='source-intake'))['task']
    required = plan_task(context, SimpleNamespace(action='task-show', task_id='child-a'))['task']
    optional = plan_task(context, SimpleNamespace(action='task-show', task_id='child-b'))['task']
    assert parent['status'] == 'decomposed'
    assert parent['next_owner'] == 'planner'
    assert 'completion' not in parent['artifacts']
    assert parent['task_set_parent']['task_set_revision'] == 1
    assert required['task_set'] == {
        'schema': 'ccb.plan.task_set_binding.v1',
        'task_set_id': created['task_set']['task_set_id'],
        'task_set_revision': 1,
        'binding_role': 'child',
        'bound_task_revision': 1,
        'required': True,
        'order': 0,
    }
    assert optional['task_set']['required'] is False
    assert created['task_set']['state'] == 'running'
    assert find_first_actionable_task(context, task_id='source-intake') is None


@pytest.mark.parametrize(
    ('aggregate_result', 'expected_status', 'expected_owner'),
    (
        ('pass', 'done', 'frontdesk'),
        ('partial', 'partial', 'planner'),
        ('replan_required', 'replan_required', 'planner'),
        ('blocked', 'blocked', 'frontdesk'),
    ),
)
def test_task_set_parent_settlement_maps_each_aggregate_exactly_once(
    tmp_path: Path,
    aggregate_result: str,
    expected_status: str,
    expected_owner: str,
) -> None:
    context = _context(tmp_path)
    created = _create_set(context, [('child-a', True)])
    task_set_id = created['task_set']['task_set_id']
    authority = dict(
        task_id='source-intake',
        task_set_id=task_set_id,
        task_set_revision=1,
        aggregate_result=aggregate_result,
        closure_digest='sha256:' + 'c' * 64,
        planner_feedback_digest='sha256:' + 'f' * 64,
    )

    first = settle_task_set_parent(context, **authority)
    replay = settle_task_set_parent(context, **authority)

    assert first['idempotent'] is False
    assert replay['idempotent'] is True
    assert replay['task']['status'] == expected_status
    assert replay['task']['owner'] == expected_owner


@pytest.mark.parametrize(
    ('results', 'expected', 'reason'),
    (
        (('pass', 'pass'), 'pass', 'all_required_children_passed'),
        (('pass', 'replan_required'), 'replan_required', 'one_or_more_required_children_require_replan'),
        (('pass', 'partial'), 'partial', 'one_or_more_required_children_are_partial'),
        (('pass', 'blocked'), 'partial', 'required_children_include_pass_and_blocked'),
        (('blocked', 'blocked'), 'blocked', 'all_required_children_are_blocked'),
    ),
)
def test_task_set_aggregate_precedence_rows(
    tmp_path: Path,
    results: tuple[str, str],
    expected: str,
    reason: str,
) -> None:
    context = _context(tmp_path)
    created = _create_set(context, [('child-a', True), ('child-b', True)])
    for task_id, result in zip(('child-a', 'child-b'), results):
        _complete(context, task_id, result)

    closed = evaluate_task_set_closure(
        context,
        task_set_id=created['task_set']['task_set_id'],
        plan_task_fn=plan_task,
    )

    assert closed['status'] == 'closure_pending'
    assert closed['closure']['expected_plan_revision'].startswith('sha256:')
    assert closed['closure']['aggregate_result'] == expected
    assert closed['closure']['reason'] == reason
    assert closed['closure_intent']['status'] == 'pending_planner_backfill'


def test_optional_pending_child_does_not_block_required_closure(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = _create_set(context, [('required-child', True), ('optional-child', False)])
    _complete(context, 'required-child', 'pass')

    closed = evaluate_task_set_closure(
        context,
        task_set_id=created['task_set']['task_set_id'],
        plan_task_fn=plan_task,
    )

    assert closed['closure']['aggregate_result'] == 'pass'
    assert [item['task_id'] for item in closed['closure']['ordered_children']] == ['required-child']


@pytest.mark.parametrize(
    ('release_clean', 'cleanup_complete', 'expected_reason'),
    (
        (False, True, 'terminal_child_release_incomplete'),
        (True, False, 'terminal_child_cleanup_incomplete'),
    ),
)
def test_incomplete_release_or_cleanup_is_system_failure_without_semantic_intent(
    tmp_path: Path,
    release_clean: bool,
    cleanup_complete: bool,
    expected_reason: str,
) -> None:
    context = _context(tmp_path)
    created = _create_set(context, [('child-a', True)])
    _complete(
        context,
        'child-a',
        'pass',
        release_clean=release_clean,
        cleanup_complete=cleanup_complete,
        multi=True,
    )

    failed = evaluate_task_set_closure(
        context,
        task_set_id=created['task_set']['task_set_id'],
        plan_task_fn=plan_task,
    )

    assert failed['status'] == 'system_failure'
    assert failed['closure']['aggregate_result'] is None
    assert failed['failures'][0]['reason'] == expected_reason
    assert failed['planner_intent_created'] is False


def test_last_child_creates_one_exact_once_closure_intent_and_replay_reuses_it(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = _create_set(context, [('child-a', True), ('child-b', True)])
    task_set_id = created['task_set']['task_set_id']
    _complete(context, 'child-a', 'pass')
    pending = evaluate_task_set_closure(context, task_set_id=task_set_id, plan_task_fn=plan_task)
    assert pending['status'] == 'pending'
    assert pending['child_task_ids'] == ['child-b']
    _complete(context, 'child-b', 'pass')

    first = evaluate_task_set_closure(context, task_set_id=task_set_id, plan_task_fn=plan_task)
    replay = evaluate_task_set_closure(context, task_set_id=task_set_id, plan_task_fn=plan_task)
    discovered = find_pending_task_set_closures(context)

    assert first['planner_intent_created'] is True
    assert replay['idempotent'] is True
    assert replay['closure_intent']['intent_id'] == first['closure_intent']['intent_id']
    assert discovered['pending_count'] == 1
    assert discovered['pending'][0]['intent_id'] == first['closure_intent']['intent_id']


def test_feedback_settlement_removes_intent_from_pending_discovery(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = _create_set(context, [('child-a', True)])
    task_set_id = created['task_set']['task_set_id']
    _complete(context, 'child-a', 'pass')
    closed = evaluate_task_set_closure(context, task_set_id=task_set_id, plan_task_fn=plan_task)
    intent = closed['closure_intent']
    root = Path(context.project.project_root)
    task_set_root = root / 'docs/plantree/plans/demo/task-sets' / task_set_id
    planner_job_id = 'job_planner'
    task_set = json.loads((task_set_root / 'task-set.json').read_text(encoding='utf-8'))
    proposal = _planner_proposal(task_set_id, closed['closure'], task_set['plan_revision']['digest'])
    feedback_digest = planner_feedback_digest(proposal)
    imported = apply_planner_feedback(context, proposal, {
        'task_set_id': task_set_id,
        'task_set_revision': 1,
        'closure_intent_id': intent['intent_id'],
        'closure_digest': closed['closure']['closure_digest'],
        'ordered_terminal_evidence_digest': intent['ordered_terminal_evidence_digest'],
        'expected_plan_revision': task_set['plan_revision']['digest'],
        'planner_job_id': planner_job_id,
        'planner_feedback_digest': feedback_digest,
        'plan_slug': 'demo',
    })
    backfill_path = Path(imported['planner_backfill_path'])
    planner_message = _planner_message(closed['closure'], intent)
    planner = _prepared_transport(
        target='planner',
        purpose='planner_backfill',
        task_id=f'task-set-feedback-{intent["intent_id"]}',
        message=planner_message,
        silent=True,
    )
    proposal_reply = '**planner-backfill.json**\n```json\n' + json.dumps(proposal.to_record()) + '\n```\n'
    planner.update({'job_id': planner_job_id, 'status': 'completed', 'reply': proposal_reply})
    _completed_job(
        context,
        target='planner',
        job_id=planner_job_id,
        task_id=str(planner['task_id']),
        message=planner_message,
    )
    runtime_path = root / '.ccb/runtime/task-sets' / task_set_id / 'feedback-r1.json'
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime = {
        'schema': 'ccb.plan.task_set_feedback_runtime.v1',
        'schema_version': 1,
        'task_set_id': task_set_id,
        'task_set_revision': 1,
        'closure_intent_id': intent['intent_id'],
        'closure_digest': closed['closure']['closure_digest'],
        'terminal_evidence_digest': intent['ordered_terminal_evidence_digest'],
        'stage': 'closed',
        'planner': planner,
        'frontdesk': None,
        'backfill_import': {
            'task_set_id': task_set_id,
            'task_set_revision': 1,
            'closure_intent_id': intent['intent_id'],
            'closure_digest': closed['closure']['closure_digest'],
            'ordered_terminal_evidence_digest': intent['ordered_terminal_evidence_digest'],
            'expected_plan_revision': task_set['plan_revision']['digest'],
            'planner_job_id': planner_job_id,
            'planner_feedback_digest': feedback_digest,
            'plan_slug': 'demo',
            **imported,
        },
        'notification': {'status': 'notification_not_required'},
    }
    runtime['runtime_digest'] = _runtime_digest(runtime)
    runtime_path.write_text(json.dumps(runtime), encoding='utf-8')
    authority = dict(
        task_set_id=task_set_id,
        task_set_revision=1,
        intent_id=intent['intent_id'],
        ordered_terminal_evidence_digest=intent['ordered_terminal_evidence_digest'],
        transport_ref={
            'runtime_state_path': str(runtime_path),
            'planner_job_id': planner_job_id,
            'planner_backfill_path': str(backfill_path),
            'planner_feedback_digest': feedback_digest,
            'notification_status': 'notification_not_required',
            'backfill_digest': imported['backfill_digest'],
        },
    )

    with pytest.raises(ValueError, match='intent not found'):
        settle_task_set_closure_feedback(context, **{**authority, 'intent_id': 'tsi-wrong'})
    assert plan_task(
        context, SimpleNamespace(action='task-show', task_id='source-intake')
    )['task']['status'] == 'decomposed'

    first = settle_task_set_closure_feedback(context, **authority)
    replay = settle_task_set_closure_feedback(context, **authority)
    discovered = find_pending_task_set_closures(context)

    assert first['idempotent'] is False
    assert replay['idempotent'] is True
    assert discovered['pending_count'] == 0
    assert first['intent']['status'] == 'feedback_closed'
    assert plan_task(context, SimpleNamespace(action='task-show', task_id='source-intake'))['task']['status'] == 'done'


@pytest.mark.parametrize('authority_path', ('runtime_state_path', 'planner_backfill_path'))
def test_settlement_rejects_external_runtime_or_backfill_authority(
    tmp_path: Path, authority_path: str
) -> None:
    context = _context(tmp_path)
    authority, runtime_path, backfill_path = _settlement_fixture(context)
    source = runtime_path if authority_path == 'runtime_state_path' else backfill_path
    external = tmp_path / f'external-{source.name}'
    external.write_bytes(source.read_bytes())
    authority['transport_ref'][authority_path] = str(external)

    with pytest.raises(ValueError, match='path authority mismatch'):
        settle_task_set_closure_feedback(context, **authority)
    assert plan_task(
        context, SimpleNamespace(action='task-show', task_id='source-intake')
    )['task']['status'] == 'decomposed'


def test_settlement_rejects_notification_required_bypass(tmp_path: Path) -> None:
    context = _context(tmp_path)
    authority, runtime_path, _backfill_path = _settlement_fixture(
        context, notification_required=True
    )
    runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
    runtime['frontdesk'] = None
    runtime['notification'] = {'status': 'notification_not_required'}
    runtime['runtime_digest'] = _runtime_digest(runtime)
    runtime_path.write_text(json.dumps(runtime), encoding='utf-8')
    authority['transport_ref']['notification_status'] = 'notification_not_required'
    authority['transport_ref'].pop('frontdesk_job_id')

    with pytest.raises(ValueError, match='notification authority invalid'):
        settle_task_set_closure_feedback(context, **authority)
    assert plan_task(
        context, SimpleNamespace(action='task-show', task_id='source-intake')
    )['task']['status'] == 'decomposed'


def test_settlement_accepts_completed_required_frontdesk_delivery(tmp_path: Path) -> None:
    context = _context(tmp_path)
    authority, _runtime_path, _backfill_path = _settlement_fixture(
        context, notification_required=True
    )

    settled = settle_task_set_closure_feedback(context, **authority)

    assert settled['status'] == 'feedback_closed'
    assert plan_task(
        context, SimpleNamespace(action='task-show', task_id='source-intake')
    )['task']['status'] == 'done'


@pytest.mark.parametrize('target', ('planner', 'frontdesk'))
def test_settlement_rejects_forged_or_missing_completed_job(
    tmp_path: Path, target: str
) -> None:
    context = _context(tmp_path)
    authority, runtime_path, _backfill_path = _settlement_fixture(
        context, notification_required=target == 'frontdesk'
    )
    runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
    runtime[target]['job_id'] = f'job_forged_{target}'
    runtime['runtime_digest'] = _runtime_digest(runtime)
    runtime_path.write_text(json.dumps(runtime), encoding='utf-8')
    authority['transport_ref'][f'{target}_job_id'] = f'job_forged_{target}'

    with pytest.raises(
        ValueError,
        match='persisted job is not terminal completed|imported backfill authority mismatch',
    ):
        settle_task_set_closure_feedback(context, **authority)


def test_settlement_rejects_mismatched_planner_message_identity(tmp_path: Path) -> None:
    context = _context(tmp_path)
    authority, runtime_path, _backfill_path = _settlement_fixture(context)
    runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
    runtime['planner']['message'] = 'forged message'
    runtime['planner']['message_sha256'] = hashlib.sha256(b'forged message').hexdigest()
    runtime['runtime_digest'] = _runtime_digest(runtime)
    runtime_path.write_text(json.dumps(runtime), encoding='utf-8')

    with pytest.raises(ValueError, match='Planner message authority mismatch'):
        settle_task_set_closure_feedback(context, **authority)


def test_settlement_rejects_nonterminal_planner_job(tmp_path: Path) -> None:
    context = _context(tmp_path)
    authority, runtime_path, _backfill_path = _settlement_fixture(context)
    runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
    planner = runtime['planner']
    request = MessageEnvelope(
        project_id=context.project.project_id,
        to_agent='planner',
        from_actor='system',
        body=planner['message'],
        task_id=planner['task_id'],
        reply_to=None,
        message_type='task',
        delivery_scope=DeliveryScope.SINGLE,
        silence_on_success=True,
    )
    JobStore(PathLayout(context.project.project_root)).append(JobRecord(
        job_id=planner['job_id'], submission_id=None, agent_name='planner',
        provider='codex', request=request, status=JobStatus.RUNNING,
        terminal_decision=None, cancel_requested_at=None,
        created_at='2026-07-12T00:00:00Z', updated_at='2026-07-12T00:00:02Z',
    ))

    with pytest.raises(ValueError, match='persisted job is not terminal completed'):
        settle_task_set_closure_feedback(context, **authority)


def test_same_revision_conflicting_terminal_digest_fails_closed(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = _create_set(context, [('child-a', True)])
    task_set_id = created['task_set']['task_set_id']
    _complete(context, 'child-a', 'pass')
    evaluate_task_set_closure(context, task_set_id=task_set_id, plan_task_fn=plan_task)
    round_path = Path(context.project.project_root) / '.ccb/runtime/loops/loop-child-a/round.json'
    record = json.loads(round_path.read_text(encoding='utf-8'))
    record['diagnostic'] = 'changed-after-closure'
    round_path.write_text(json.dumps(record, sort_keys=True) + '\n', encoding='utf-8')

    with pytest.raises(ValueError, match='conflicts with existing terminal evidence digest'):
        evaluate_task_set_closure(context, task_set_id=task_set_id, plan_task_fn=plan_task)


def test_corrupt_existing_closure_fails_closed(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = _create_set(context, [('child-a', True)])
    task_set_id = created['task_set']['task_set_id']
    _complete(context, 'child-a', 'pass')
    evaluate_task_set_closure(context, task_set_id=task_set_id, plan_task_fn=plan_task)
    closure_path = (
        Path(context.project.project_root)
        / 'docs/plantree/plans/demo/task-sets'
        / task_set_id
        / 'closure.json'
    )
    closure = json.loads(closure_path.read_text(encoding='utf-8'))
    closure['unexpected'] = 'authority-drift'
    closure_path.write_text(json.dumps(closure, sort_keys=True) + '\n', encoding='utf-8')

    with pytest.raises(ValueError, match='invalid task-set closure schema or fields'):
        evaluate_task_set_closure(context, task_set_id=task_set_id, plan_task_fn=plan_task)


def test_revision_race_stales_old_intent_and_requires_new_child(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = _create_set(context, [('child-a', True)])
    task_set_id = created['task_set']['task_set_id']
    _complete(context, 'child-a', 'pass')
    first = evaluate_task_set_closure(context, task_set_id=task_set_id, plan_task_fn=plan_task)
    _create_task(context, 'child-b')

    revised = revise_task_set_authority(
        context,
        task_set_id=task_set_id,
        expected_revision=1,
        children=[
            {'task_id': 'child-a', 'required': True},
            {'task_id': 'child-b', 'required': True},
        ],
        plan_task_fn=plan_task,
    )

    assert revised['task_set']['task_set_revision'] == 2
    with pytest.raises(ValueError, match='stale task-set revision'):
        evaluate_task_set_closure(
            context,
            task_set_id=task_set_id,
            expected_revision=1,
            plan_task_fn=plan_task,
        )
    pending = evaluate_task_set_closure(
        context,
        task_set_id=task_set_id,
        expected_revision=2,
        plan_task_fn=plan_task,
    )
    assert pending['status'] == 'pending'
    assert pending['child_task_ids'] == ['child-b']
    _complete(context, 'child-b', 'pass')
    second = evaluate_task_set_closure(context, task_set_id=task_set_id, plan_task_fn=plan_task)
    store_path = Path(context.project.project_root) / '.ccb/runtime/task-sets' / task_set_id / 'closure-intents.json'
    intents = json.loads(store_path.read_text(encoding='utf-8'))['intents']
    discovered = find_pending_task_set_closures(context)
    assert first['closure_intent']['intent_id'] != second['closure_intent']['intent_id']
    assert [intent['status'] for intent in intents] == ['stale', 'pending_planner_backfill']
    assert second['closure']['task_set_revision'] == 2
    assert discovered['pending_count'] == 1
    assert discovered['stale_count'] == 1


def test_child_revision_drift_is_system_failure_until_task_set_revision_advances(tmp_path: Path) -> None:
    context = _context(tmp_path)
    created = _create_set(context, [('child-a', True)])
    replacement = Path(context.project.project_root) / 'drafts/replacement.md'
    _write(replacement, '# changed task packet\n')
    plan_task(
        context,
        SimpleNamespace(
            action='task-artifact',
            task_id='child-a',
            artifact_kind='task_packet',
            file_path=str(replacement),
            expected_task_revision=1,
        ),
    )

    failed = evaluate_task_set_closure(
        context,
        task_set_id=created['task_set']['task_set_id'],
        plan_task_fn=plan_task,
    )

    assert failed['status'] == 'system_failure'
    assert failed['failures'][0]['reason'] == 'stale_child_revision'
