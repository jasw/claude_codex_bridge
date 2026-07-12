from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli.services.planner_feedback import parse_planner_feedback_reply, planner_feedback_digest
from cli.services.planner_feedback_apply import apply_planner_feedback


def _digest(char: str) -> str:
    return 'sha256:' + char * 64


def _context(tmp_path: Path):
    root = tmp_path / 'project'
    plan = root / 'docs/plantree/plans/demo'
    plan.mkdir(parents=True)
    (plan / 'README.md').write_text('# Demo\n', encoding='utf-8')
    return SimpleNamespace(project=SimpleNamespace(project_root=root, project_id='p'))


def _proposal(plan_revision: str, *, identity_revision: int = 1):
    evidence = ['docs/plantree/plans/demo/task-sets/set-a/closure.json']
    status = {
        'schema': 'ccb.planner.frontdesk_status.v1', 'notification_identity': 'set-a-r1',
        'aggregate_result': 'pass', 'accepted_scope': ['landed'], 'unresolved_scope': [],
        'blockers': [], 'next_milestone': {'kind': 'workflow_terminal', 'ref': 'done', 'rationale': 'Done.'},
        'evidence_refs': evidence, 'user_report_body': 'Done.',
    }
    payload = {
        'schema': 'ccb.planner.backfill_proposal.v1', 'mode': 'task_set_closure',
        'expected_plan_revision': plan_revision, 'task_or_task_set_id': 'set-a',
        'task_or_task_set_revision': identity_revision, 'closure_evidence_digest': _digest('a'),
        'aggregate_result': 'pass', 'result': 'closure_complete', 'brief_summary': 'Closed.',
        'roadmap_transitions': [{'id': 'm1', 'status': 'done', 'summary': 'Landed.', 'evidence_refs': evidence}],
        'todo_transitions': [{'id': 't1', 'status': 'done', 'summary': 'Checked.', 'evidence_refs': evidence}],
        'decision_refs': ['decisions/029.md'], 'open_question_refs': [], 'evidence_refs': evidence,
        'accepted_scope': ['landed'], 'unresolved_scope': [], 'blockers': [], 'replan_inputs': [],
        'next_milestone': status['next_milestone'], 'frontdesk_notification_required': True,
        'frontdesk_status': status,
    }
    return parse_planner_feedback_reply('**planner-backfill.json**\n```json\n' + json.dumps(payload) + '\n```\n')


def _authority_files(context, authority: dict[str, object]) -> None:
    root = Path(context.project.project_root) / 'docs/plantree/plans/demo/task-sets/set-a'
    root.mkdir(parents=True)
    (root / 'task-set.json').write_text(json.dumps({
        'task_set_id': 'set-a', 'task_set_revision': 1, 'state': 'closure_pending',
        'plan_slug': 'demo',
        'plan_revision': {'digest': authority['expected_plan_revision']},
    }), encoding='utf-8')
    (root / 'closure.json').write_text(json.dumps({
        'task_set_id': 'set-a', 'task_set_revision': 1,
        'closure_digest': authority['closure_digest'],
        'ordered_terminal_evidence_digest': authority['ordered_terminal_evidence_digest'],
        'aggregate_result': 'pass',
    }), encoding='utf-8')


def test_apply_is_revision_fenced_persisted_and_idempotent(tmp_path: Path) -> None:
    context = _context(tmp_path)
    from cli.services.planner_feedback_apply import current_plan_revision
    revision = current_plan_revision(context, 'demo')
    proposal = _proposal(revision)
    authority = {
        'task_set_id': 'set-a', 'task_set_revision': 1, 'closure_intent_id': 'tsi-a',
        'closure_digest': _digest('c'), 'ordered_terminal_evidence_digest': _digest('a'),
        'expected_plan_revision': revision, 'planner_job_id': 'job-planner',
        'planner_feedback_digest': planner_feedback_digest(proposal), 'plan_slug': 'demo',
    }
    _authority_files(context, authority)

    first = apply_planner_feedback(context, proposal, authority)
    replay = apply_planner_feedback(context, proposal, authority)

    assert first['status'] == replay['status'] == 'imported'
    assert replay['idempotent'] is True
    backfill = Path(first['planner_backfill_path'])
    assert backfill.is_file()
    assert json.loads(backfill.read_text())['authority']['planner_job_id'] == 'job-planner'
    assert '<!-- ccb-planner-backfill:set-a:r1:brief:start -->' in (
        Path(context.project.project_root) / 'docs/plantree/plans/demo/README.md'
    ).read_text()


def test_apply_rejects_revision_conflict_before_write(tmp_path: Path) -> None:
    context = _context(tmp_path)
    proposal = _proposal(_digest('f'))
    authority = {
        'task_set_id': 'set-a', 'task_set_revision': 1, 'closure_intent_id': 'tsi-a',
        'closure_digest': _digest('c'), 'ordered_terminal_evidence_digest': _digest('a'),
        'expected_plan_revision': _digest('f'), 'planner_job_id': 'job-planner',
        'planner_feedback_digest': planner_feedback_digest(proposal), 'plan_slug': 'demo',
    }
    _authority_files(context, authority)
    with pytest.raises(ValueError, match='revision conflict'):
        apply_planner_feedback(context, proposal, authority)
    assert not list(Path(context.project.project_root).rglob('planner-backfill.json'))


def test_apply_recovers_partial_target_writes_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    from cli.services import planner_feedback_apply as service
    revision = service.current_plan_revision(context, 'demo')
    proposal = _proposal(revision)
    authority = {
        'task_set_id': 'set-a', 'task_set_revision': 1, 'closure_intent_id': 'tsi-a',
        'closure_digest': _digest('c'), 'ordered_terminal_evidence_digest': _digest('a'),
        'expected_plan_revision': revision, 'planner_job_id': 'job-planner',
        'planner_feedback_digest': planner_feedback_digest(proposal), 'plan_slug': 'demo',
    }
    _authority_files(context, authority)
    original = service.atomic_write_text
    writes = 0

    def crash_after_first(path, text):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError('injected crash')
        return original(path, text)

    monkeypatch.setattr(service, 'atomic_write_text', crash_after_first)
    with pytest.raises(RuntimeError, match='injected crash'):
        service.apply_planner_feedback(context, proposal, authority)
    monkeypatch.setattr(service, 'atomic_write_text', original)

    recovered = service.apply_planner_feedback(context, proposal, authority)
    assert recovered['status'] == 'imported'
    assert Path(recovered['planner_backfill_path']).is_file()


def test_persisted_transaction_rejects_arbitrary_target_and_tampering(tmp_path: Path) -> None:
    context = _context(tmp_path)
    from cli.services import planner_feedback_apply as service
    revision = service.current_plan_revision(context, 'demo')
    proposal = _proposal(revision)
    authority = {
        'task_set_id': 'set-a', 'task_set_revision': 1, 'closure_intent_id': 'tsi-a',
        'closure_digest': _digest('c'), 'ordered_terminal_evidence_digest': _digest('a'),
        'expected_plan_revision': revision, 'planner_job_id': 'job-planner',
        'planner_feedback_digest': planner_feedback_digest(proposal), 'plan_slug': 'demo',
    }
    _authority_files(context, authority)
    transaction = service._derive_transaction(
        context,
        Path(context.project.project_root) / 'docs/plantree/plans/demo',
        proposal,
        authority,
        persisted=None,
    )
    transaction['targets'][0]['path'] = 'unrelated-user-file.txt'
    transaction['transaction_digest'] = service._semantic_digest(transaction, omit='transaction_digest')
    path = Path(context.project.project_root) / 'docs/plantree/plans/demo/task-sets/set-a/planner-backfill-r1.transaction.json'
    path.write_text(json.dumps(transaction), encoding='utf-8')

    with pytest.raises(ValueError, match='target path authority'):
        service.apply_planner_feedback(context, proposal, authority)
    assert not (Path(context.project.project_root) / 'unrelated-user-file.txt').exists()


@pytest.mark.parametrize('tamper', ('extra_field', 'target_body', 'transaction_digest', 'duplicate_path'))
def test_persisted_transaction_rejects_semantic_tampering(
    tmp_path: Path, tamper: str
) -> None:
    context = _context(tmp_path)
    from cli.services import planner_feedback_apply as service
    revision = service.current_plan_revision(context, 'demo')
    proposal = _proposal(revision)
    authority = {
        'task_set_id': 'set-a', 'task_set_revision': 1, 'closure_intent_id': 'tsi-a',
        'closure_digest': _digest('c'), 'ordered_terminal_evidence_digest': _digest('a'),
        'expected_plan_revision': revision, 'planner_job_id': 'job-planner',
        'planner_feedback_digest': planner_feedback_digest(proposal), 'plan_slug': 'demo',
    }
    _authority_files(context, authority)
    plan_root = Path(context.project.project_root) / 'docs/plantree/plans/demo'
    tx = service._derive_transaction(context, plan_root, proposal, authority, persisted=None)
    if tamper == 'extra_field':
        tx['unexpected'] = True
    elif tamper == 'target_body':
        tx['targets'][0]['target_text'] += 'attacker\n'
    elif tamper == 'transaction_digest':
        tx['transaction_digest'] = _digest('f')
    else:
        tx['targets'][1]['path'] = tx['targets'][0]['path']
        tx['transaction_digest'] = service._semantic_digest(tx, omit='transaction_digest')
    path = plan_root / 'task-sets/set-a/planner-backfill-r1.transaction.json'
    path.write_text(json.dumps(tx), encoding='utf-8')

    with pytest.raises(ValueError):
        service.apply_planner_feedback(context, proposal, authority)


def test_user_marker_collision_is_not_replaced(tmp_path: Path) -> None:
    context = _context(tmp_path)
    from cli.services import planner_feedback_apply as service
    readme = Path(context.project.project_root) / 'docs/plantree/plans/demo/README.md'
    user_text = '# Demo\n\n<!-- ccb-planner-backfill:set-a:r1:brief:start -->\nUSER OWNED\n<!-- ccb-planner-backfill:set-a:r1:brief:end -->\n'
    readme.write_text(user_text, encoding='utf-8')
    revision = service.current_plan_revision(context, 'demo')
    proposal = _proposal(revision)
    authority = {
        'task_set_id': 'set-a', 'task_set_revision': 1, 'closure_intent_id': 'tsi-a',
        'closure_digest': _digest('c'), 'ordered_terminal_evidence_digest': _digest('a'),
        'expected_plan_revision': revision, 'planner_job_id': 'job-planner',
        'planner_feedback_digest': planner_feedback_digest(proposal), 'plan_slug': 'demo',
    }
    _authority_files(context, authority)
    with pytest.raises(ValueError, match='marker collision'):
        service.apply_planner_feedback(context, proposal, authority)
    assert readme.read_text(encoding='utf-8') == user_text


def test_replay_rejects_projected_file_drift(tmp_path: Path) -> None:
    context = _context(tmp_path)
    from cli.services import planner_feedback_apply as service
    revision = service.current_plan_revision(context, 'demo')
    proposal = _proposal(revision)
    authority = {
        'task_set_id': 'set-a', 'task_set_revision': 1, 'closure_intent_id': 'tsi-a',
        'closure_digest': _digest('c'), 'ordered_terminal_evidence_digest': _digest('a'),
        'expected_plan_revision': revision, 'planner_job_id': 'job-planner',
        'planner_feedback_digest': planner_feedback_digest(proposal), 'plan_slug': 'demo',
    }
    _authority_files(context, authority)
    service.apply_planner_feedback(context, proposal, authority)
    roadmap = Path(context.project.project_root) / 'docs/plantree/plans/demo/Roadmap.md'
    roadmap.write_text(roadmap.read_text() + 'drift\n', encoding='utf-8')
    with pytest.raises(ValueError, match='revision conflict|projected target drift'):
        service.apply_planner_feedback(context, proposal, authority)


def test_later_task_set_revision_uses_distinct_transaction_and_backfill(tmp_path: Path) -> None:
    context = _context(tmp_path)
    from cli.services import planner_feedback_apply as service
    revision1 = service.current_plan_revision(context, 'demo')
    proposal1 = _proposal(revision1)
    authority1 = {
        'task_set_id': 'set-a', 'task_set_revision': 1, 'closure_intent_id': 'tsi-a1',
        'closure_digest': _digest('c'), 'ordered_terminal_evidence_digest': _digest('a'),
        'expected_plan_revision': revision1, 'planner_job_id': 'job-planner-1',
        'planner_feedback_digest': planner_feedback_digest(proposal1), 'plan_slug': 'demo',
    }
    _authority_files(context, authority1)
    first = service.apply_planner_feedback(context, proposal1, authority1)

    task_set_root = Path(context.project.project_root) / 'docs/plantree/plans/demo/task-sets/set-a'
    revision2 = service.current_plan_revision(context, 'demo')
    task_set = json.loads((task_set_root / 'task-set.json').read_text())
    task_set.update({'task_set_revision': 2, 'plan_revision': {'digest': revision2}})
    (task_set_root / 'task-set.json').write_text(json.dumps(task_set), encoding='utf-8')
    closure = json.loads((task_set_root / 'closure.json').read_text())
    closure.update({'task_set_revision': 2})
    (task_set_root / 'closure.json').write_text(json.dumps(closure), encoding='utf-8')
    proposal2 = _proposal(revision2, identity_revision=2)
    authority2 = {
        'task_set_id': 'set-a', 'task_set_revision': 2, 'closure_intent_id': 'tsi-a2',
        'closure_digest': _digest('c'), 'ordered_terminal_evidence_digest': _digest('a'),
        'expected_plan_revision': revision2, 'planner_job_id': 'job-planner-2',
        'planner_feedback_digest': planner_feedback_digest(proposal2), 'plan_slug': 'demo',
    }
    second = service.apply_planner_feedback(context, proposal2, authority2)

    assert first['planner_backfill_path'].endswith('planner-backfill-r1.json')
    assert second['planner_backfill_path'].endswith('planner-backfill-r2.json')
    assert Path(first['planner_backfill_path']).is_file()
    assert Path(second['planner_backfill_path']).is_file()


@pytest.mark.parametrize('tamper', ('extra_field', 'backfill_digest'))
def test_replay_rejects_backfill_schema_or_digest_tampering(
    tmp_path: Path, tamper: str
) -> None:
    context = _context(tmp_path)
    from cli.services import planner_feedback_apply as service
    revision = service.current_plan_revision(context, 'demo')
    proposal = _proposal(revision)
    authority = {
        'task_set_id': 'set-a', 'task_set_revision': 1, 'closure_intent_id': 'tsi-a',
        'closure_digest': _digest('c'), 'ordered_terminal_evidence_digest': _digest('a'),
        'expected_plan_revision': revision, 'planner_job_id': 'job-planner',
        'planner_feedback_digest': planner_feedback_digest(proposal), 'plan_slug': 'demo',
    }
    _authority_files(context, authority)
    imported = service.apply_planner_feedback(context, proposal, authority)
    path = Path(imported['planner_backfill_path'])
    record = json.loads(path.read_text())
    if tamper == 'extra_field':
        record['unexpected'] = True
    else:
        record['backfill_digest'] = _digest('f')
    path.write_text(json.dumps(record), encoding='utf-8')

    with pytest.raises(ValueError, match='persisted authority'):
        service.apply_planner_feedback(context, proposal, authority)
