from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli.services.plan_tasks import find_first_actionable_task, plan_task
from cli.services.task_set_closure import (
    create_task_set_authority,
    evaluate_task_set_closure,
    find_pending_task_set_closures,
    revise_task_set_authority,
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
