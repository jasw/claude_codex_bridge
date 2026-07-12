from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope
from jobs.store import JobStore
from storage.paths import PathLayout
from cli.services.ask_runtime import AskSummary
from cli.models_start import ParsedLoopRunnerCommand
from cli.services.loop_runner import loop_runner_auto, loop_runner_once
from cli.services.task_set_feedback_runtime import advance_task_set_feedback_runtime, _retry_successor_job
from cli.services.task_set_feedback_runtime import _deps


_EVIDENCE_DIGEST = 'sha256:' + 'a' * 64


def test_runtime_wires_default_transactional_planner_apply() -> None:
    assert _deps(None).apply_planner_feedback.__module__ == 'cli.services.planner_feedback_apply'


def _context(tmp_path: Path):
    return SimpleNamespace(
        project=SimpleNamespace(project_root=tmp_path, project_id='project-test'),
        paths=None,
    )


def _authority(tmp_path: Path, *, revision: int = 1) -> tuple[dict[str, object], dict[str, object]]:
    root = tmp_path / 'docs/plantree/plans/demo/task-sets/set-a'
    root.mkdir(parents=True, exist_ok=True)
    closure = {
        'schema': 'ccb.plan.task_set_closure.v1',
        'task_set_id': 'set-a',
        'task_set_revision': revision,
        'ordered_terminal_evidence_digest': _EVIDENCE_DIGEST,
        'aggregate_result': 'pass',
        'closure_digest': 'sha256:' + 'b' * 64,
    }
    (root / 'closure.json').write_text(json.dumps(closure), encoding='utf-8')
    task_set = {
        'task_set_id': 'set-a',
        'task_set_revision': revision,
        'plan_slug': 'demo',
        'state': 'closure_pending',
        'plan_revision': {'revision': 7, 'digest': 'sha256:' + 'c' * 64},
        'closure': {
            'path': 'docs/plantree/plans/demo/task-sets/set-a/closure.json',
            'closure_digest': closure['closure_digest'],
            'ordered_terminal_evidence_digest': _EVIDENCE_DIGEST,
        },
    }
    task_set_path = root / 'task-set.json'
    task_set_path.write_text(json.dumps(task_set), encoding='utf-8')
    intent = {
        'intent_id': 'intent-a',
        'task_set_id': 'set-a',
        'task_set_revision': revision,
        'ordered_terminal_evidence_digest': _EVIDENCE_DIGEST,
        'closure_digest': closure['closure_digest'],
        'task_set_path': str(task_set_path),
    }
    return intent, task_set


def _planner_reply(*, notify: bool = True) -> str:
    status = {
        'schema': 'ccb.planner.frontdesk_status.v1',
        'notification_identity': 'notice-a',
        'aggregate_result': 'pass',
        'accepted_scope': ['all required children'],
        'unresolved_scope': [],
        'blockers': [],
        'next_milestone': {
            'kind': 'workflow_terminal',
            'ref': 'done',
            'rationale': 'All required children passed.',
        },
        'evidence_refs': ['docs/plantree/plans/demo/task-sets/set-a/closure.json'],
        'user_report_body': 'All required work passed validated closure.',
    }
    proposal = {
        'schema': 'ccb.planner.backfill_proposal.v1',
        'mode': 'task_set_closure',
        'expected_plan_revision': 'sha256:' + 'c' * 64,
        'task_or_task_set_id': 'set-a',
        'task_or_task_set_revision': 1,
        'closure_evidence_digest': _EVIDENCE_DIGEST,
        'aggregate_result': 'pass',
        'result': 'closure_complete',
        'brief_summary': 'All required work passed.',
        'roadmap_transitions': [],
        'todo_transitions': [],
        'decision_refs': [],
        'open_question_refs': [],
        'evidence_refs': ['docs/plantree/plans/demo/task-sets/set-a/closure.json'],
        'accepted_scope': ['all required children'],
        'unresolved_scope': [],
        'blockers': [],
        'replan_inputs': [],
        'next_milestone': status['next_milestone'],
        'frontdesk_notification_required': notify,
        'frontdesk_status': status,
    }
    return '**planner-backfill.json**\n```json\n' + json.dumps(proposal) + '\n```'


class Harness:
    def __init__(self, intent: dict[str, object], *, notify: bool = True):
        self.intent = intent
        self.notify = notify
        self.terminals: dict[str, dict[str, object]] = {}
        self.persisted: dict[str, str] = {}
        self.submissions: list[object] = []
        self.imports: list[dict[str, object]] = []
        self.settlements: list[dict[str, object]] = []
        self.successors: dict[str, str] = {}
        self.next_job = 1

    def services(self):
        return SimpleNamespace(
            discover_task_set_closures=self.discover,
            plan_task=lambda *_args, **_kwargs: None,
            submit_ask=self.submit,
            persisted_terminal_watch=lambda _context, job_id: self.terminals.get(job_id),
            find_task_set_transport_job=self.find,
            find_task_set_retry_successor=(
                lambda _context, source_job_id, **_kwargs: self.successors.get(source_job_id)
            ),
            apply_planner_feedback=self.apply,
            settle_task_set_feedback=self.settle,
            resolve_plan_revision=lambda *_args, **_kwargs: 'sha256:' + 'c' * 64,
        )

    def discover(self, _context, **_kwargs):
        return {'evaluated': [], 'pending': [self.intent]}

    def submit(self, _context, command):
        self.submissions.append(command)
        job_id = f'job_{self.next_job}'
        self.next_job += 1
        self.persisted[command.task_id] = job_id
        return AskSummary('project-test', 'submission-a', ({'agent_name': command.target, 'job_id': job_id},))

    def find(self, _context, *, task_id: str, **_kwargs):
        return self.persisted.get(task_id)

    def apply(self, _context, _proposal, authority):
        self.imports.append(authority)
        return {'status': 'imported', 'import_id': 'import-a'}

    def settle(self, _context, **authority):
        self.settlements.append(authority)
        return {'status': 'feedback_closed'}


def test_pending_planner_then_frontdesk_then_exact_once_close(tmp_path: Path) -> None:
    intent, _ = _authority(tmp_path)
    harness = Harness(intent)
    context = _context(tmp_path)

    planner_pending = advance_task_set_feedback_runtime(context, harness.services())
    assert planner_pending['action'] == 'task_set_planner_backfill_pending'
    assert planner_pending['pending_job_ids'] == ['job_1']
    assert harness.submissions[0].target == 'planner'
    assert harness.submissions[0].silence is True
    planner_envelope = json.loads(
        harness.submissions[0].message.split('```json\n', 1)[1].rsplit('\n```', 1)[0]
    )
    assert planner_envelope['closure_ref'] == {
        'path': 'docs/plantree/plans/demo/task-sets/set-a/closure.json',
        'closure_digest': 'sha256:' + 'b' * 64,
        'ordered_terminal_evidence_digest': _EVIDENCE_DIGEST,
    }

    harness.terminals['job_1'] = {'status': 'completed', 'reply': _planner_reply()}
    frontdesk_pending = advance_task_set_feedback_runtime(context, harness.services())
    assert frontdesk_pending['action'] == 'task_set_frontdesk_status_pending'
    assert frontdesk_pending['pending_job_ids'] == ['job_2']
    assert harness.submissions[1].target == 'frontdesk'
    assert harness.submissions[1].silence is False
    assert len(harness.imports) == 1

    harness.terminals['job_2'] = {'status': 'completed', 'reply': 'delivered'}
    closed = advance_task_set_feedback_runtime(context, harness.services())
    replay = advance_task_set_feedback_runtime(context, harness.services())
    assert closed['action'] == replay['action'] == 'task_set_feedback_closed'
    assert len(harness.submissions) == 2
    assert len(harness.imports) == 1
    assert len(harness.settlements) == 2


def test_planner_proposal_must_echo_exact_transport_closure_ref(tmp_path: Path) -> None:
    intent, _ = _authority(tmp_path)
    harness = Harness(intent)
    context = _context(tmp_path)
    advance_task_set_feedback_runtime(context, harness.services())
    reply = _planner_reply()
    payload = json.loads(reply.split('```json\n', 1)[1].rsplit('\n```', 1)[0])
    payload['evidence_refs'] = ['tasks/child-a/round_summary.md']
    payload['frontdesk_status']['evidence_refs'] = payload['evidence_refs']
    harness.terminals['job_1'] = {
        'status': 'completed',
        'reply': '**planner-backfill.json**\n```json\n' + json.dumps(payload) + '\n```\n',
    }

    with pytest.raises(ValueError, match='omits required evidence refs'):
        advance_task_set_feedback_runtime(context, harness.services())


def test_planner_and_frontdesk_retry_successors_resume_exactly_once(tmp_path: Path) -> None:
    intent, _ = _authority(tmp_path)
    harness = Harness(intent)
    context = _context(tmp_path)
    advance_task_set_feedback_runtime(context, harness.services())
    harness.terminals['job_1'] = {'status': 'failed', 'reply': 'source failed'}
    harness.successors['job_1'] = 'job_1_retry'
    harness.terminals['job_1_retry'] = {'status': 'completed', 'reply': _planner_reply()}

    planner_done = advance_task_set_feedback_runtime(context, harness.services())

    assert planner_done['action'] == 'task_set_frontdesk_status_pending'
    assert harness.imports[0]['planner_source_job_id'] == 'job_1'
    assert harness.imports[0]['planner_effective_job_id'] == 'job_1_retry'
    assert harness.imports[0]['planner_retry_lineage'] == [{
        'retry_source_job_id': 'job_1',
        'retry_successor_job_id': 'job_1_retry',
    }]
    harness.terminals['job_2'] = {'status': 'incomplete', 'reply': 'delivery failed'}
    harness.successors['job_2'] = 'job_2_retry'
    harness.terminals['job_2_retry'] = {'status': 'completed', 'reply': 'delivered'}

    closed = advance_task_set_feedback_runtime(context, harness.services())

    assert closed['action'] == 'task_set_feedback_closed'
    settlement = harness.settlements[0]['transport_ref']
    assert settlement['frontdesk_source_job_id'] == 'job_2'
    assert settlement['frontdesk_effective_job_id'] == 'job_2_retry'
    assert settlement['frontdesk_retry_lineage'] == [{
        'retry_source_job_id': 'job_2',
        'retry_successor_job_id': 'job_2_retry',
    }]
    assert len(harness.imports) == 1


def test_retry_successor_cycle_fails_closed(tmp_path: Path) -> None:
    intent, _ = _authority(tmp_path)
    harness = Harness(intent)
    context = _context(tmp_path)
    advance_task_set_feedback_runtime(context, harness.services())
    harness.terminals['job_1'] = {'status': 'failed'}
    harness.successors['job_1'] = 'job_retry'
    harness.terminals['job_retry'] = {'status': 'failed'}
    harness.successors['job_retry'] = 'job_1'

    with pytest.raises(RuntimeError, match='retry_lineage_cycle'):
        advance_task_set_feedback_runtime(context, harness.services())


@pytest.mark.parametrize('case', ('ambiguous', 'mismatched_task'))
def test_retry_successor_authority_rejects_ambiguous_or_mismatched_jobs(
    tmp_path: Path, case: str
) -> None:
    context = _context(tmp_path)
    store = JobStore(PathLayout(tmp_path))
    message = 'exact retry message'
    count = 2 if case == 'ambiguous' else 1
    for index in range(count):
        request = MessageEnvelope(
            project_id='project-test', to_agent='planner', from_actor='system',
            body=message,
            task_id='wrong-task' if case == 'mismatched_task' else 'task-a',
            reply_to=None, message_type='ask', delivery_scope=DeliveryScope.SINGLE,
        )
        store.append(JobRecord(
            job_id=f'job_retry_{index}', submission_id=None, agent_name='planner',
            provider='codex', request=request, status=JobStatus.QUEUED,
            terminal_decision=None, cancel_requested_at=None,
            created_at=f'2026-07-12T00:00:0{index}Z',
            updated_at=f'2026-07-12T00:00:0{index}Z',
            provider_options={'retry_source_job_id': 'job_source'},
        ))

    with pytest.raises(RuntimeError, match='ambiguous|authority_mismatch'):
        _retry_successor_job(
            context,
            source_job_id='job_source', target='planner', task_id='task-a',
            message=message,
            message_sha256=hashlib.sha256(message.encode()).hexdigest(),
        )


@pytest.mark.parametrize('accepted_before_raise', [False, True])
def test_prepared_submission_crash_recovers_without_duplicate(
    tmp_path: Path,
    accepted_before_raise: bool,
) -> None:
    intent, _ = _authority(tmp_path)
    harness = Harness(intent, notify=False)
    context = _context(tmp_path)
    original = harness.submit
    calls = 0

    def crashing_submit(context, command):
        nonlocal calls
        calls += 1
        if accepted_before_raise:
            original(context, command)
        raise RuntimeError('crash-window')

    services = harness.services()
    services.submit_ask = crashing_submit
    with pytest.raises(RuntimeError, match='crash-window'):
        advance_task_set_feedback_runtime(context, services)

    if accepted_before_raise:
        harness.terminals['job_1'] = {'status': 'completed', 'reply': _planner_reply(notify=False)}
    result = advance_task_set_feedback_runtime(context, harness.services())
    if accepted_before_raise:
        assert result['action'] == 'task_set_feedback_closed'
        assert calls == 1
    else:
        assert result['action'] == 'task_set_planner_backfill_pending'
        assert len(harness.submissions) == 1


def test_terminal_before_import_is_consumed_without_resubmit(tmp_path: Path) -> None:
    intent, _ = _authority(tmp_path)
    harness = Harness(intent, notify=False)
    context = _context(tmp_path)
    harness.persisted['task-set-feedback-intent-a'] = 'job_existing'
    harness.terminals['job_existing'] = {'status': 'completed', 'reply': _planner_reply(notify=False)}

    result = advance_task_set_feedback_runtime(context, harness.services())

    assert result['action'] == 'task_set_feedback_closed'
    assert harness.submissions == []
    assert len(harness.imports) == 1


def test_stale_revision_and_terminal_failure_are_visible(tmp_path: Path) -> None:
    intent, task_set = _authority(tmp_path)
    task_set['task_set_revision'] = 2
    Path(intent['task_set_path']).write_text(json.dumps(task_set), encoding='utf-8')
    harness = Harness(intent)
    with pytest.raises(RuntimeError, match='stale_revision'):
        advance_task_set_feedback_runtime(_context(tmp_path), harness.services())

    intent, _ = _authority(tmp_path)
    harness = Harness(intent)
    context = _context(tmp_path)
    advance_task_set_feedback_runtime(context, harness.services())
    harness.terminals['job_1'] = {'status': 'failed', 'reply': 'provider failure'}
    failed = advance_task_set_feedback_runtime(context, harness.services())
    assert failed['loop_runner_status'] == 'blocked'
    assert failed['action'] == 'task_set_planner_backfill_failed'


def test_bound_job_authority_mismatch_fails_closed(tmp_path: Path) -> None:
    intent, _ = _authority(tmp_path)
    harness = Harness(intent)
    context = _context(tmp_path)
    advance_task_set_feedback_runtime(context, harness.services())
    harness.persisted['task-set-feedback-intent-a'] = 'job_lookalike'

    with pytest.raises(RuntimeError, match='bound_job_authority_mismatch'):
        advance_task_set_feedback_runtime(context, harness.services())


def test_frontdesk_terminal_failure_is_visible(tmp_path: Path) -> None:
    intent, _ = _authority(tmp_path)
    harness = Harness(intent)
    context = _context(tmp_path)
    advance_task_set_feedback_runtime(context, harness.services())
    harness.terminals['job_1'] = {'status': 'completed', 'reply': _planner_reply()}
    advance_task_set_feedback_runtime(context, harness.services())
    harness.terminals['job_2'] = {'status': 'cancelled', 'reply': ''}

    failed = advance_task_set_feedback_runtime(context, harness.services())

    assert failed['loop_runner_status'] == 'blocked'
    assert failed['action'] == 'task_set_frontdesk_status_failed'


def test_runner_advances_closure_before_idle(monkeypatch, tmp_path: Path) -> None:
    expected = {
        'loop_runner_status': 'pending',
        'action': 'task_set_planner_backfill_pending',
        'pending_job_ids': ['job_closure'],
    }
    monkeypatch.setattr('cli.services.loop_runner.find_first_actionable_task', lambda *_args, **_kwargs: None)
    services = SimpleNamespace(
        resume_multi_workgroup_scheduler=lambda *_args, **_kwargs: None,
        consume_activation_role_output=lambda *_args, **_kwargs: None,
        task_set_feedback=lambda *_args, **_kwargs: expected,
    )
    command = SimpleNamespace(task_id=None, consume_role_output=False)

    assert loop_runner_once(_context(tmp_path), command, services) == expected


def test_auto_runner_waits_for_closure_transport_and_continues(monkeypatch, tmp_path: Path) -> None:
    steps = iter(
        [
            {
                'loop_runner_status': 'pending',
                'action': 'task_set_planner_backfill_pending',
                'ask': {'target': 'planner', 'job_id': 'job_closure'},
                'pending_job_ids': ['job_closure'],
            },
            {'loop_runner_status': 'ok', 'action': 'task_set_feedback_closed'},
            {'loop_runner_status': 'idle', 'action': 'none', 'reason': 'no_actionable_task'},
        ]
    )
    monkeypatch.setattr('cli.services.loop_runner.loop_runner_once', lambda *_args, **_kwargs: next(steps))
    services = SimpleNamespace(
        persisted_terminal_watch=lambda *_args, **_kwargs: {'status': 'completed'},
    )
    command = ParsedLoopRunnerCommand(
        project=None,
        once=False,
        auto=True,
        poll_interval_s=0,
        max_steps=4,
    )

    result = loop_runner_auto(_context(tmp_path), command, services)

    assert result['action'] == 'auto_runner_finished'
    assert result['step_count'] == 3
    assert result['steps'][0]['pending_job_ids'] == ['job_closure']
