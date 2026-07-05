from __future__ import annotations

from datetime import datetime, timezone
import filecmp
from io import StringIO
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

from agents.config_loader import load_project_config
from cli.models import ParsedAskCommand
from storage.atomic import atomic_write_json, atomic_write_text

from .ask import submit_ask, watch_ask_job
from .loop_topology import loop_topology
from .plan_tasks import plan_task, task_execution_text

WORKER_PROFILE = 'coder'
REVIEWER_PROFILE = 'code_reviewer'
ORCHESTRATOR_TARGET = 'orchestrator'
ROUND_REVIEWER_TARGET = 'ccb_round_reviewer'
ROUND_REVIEWER_FIELD = 'ccb_round_reviewer'
LEGACY_ROUND_CHECKER_FIELD = 'round_checker'
RUNNER_ASK_SENDER = 'system'
MAX_PROMOTED_WORKSPACE_FILES = 50
TEST_COMMAND_PREFIXES = (
    'test_command:',
    'test command:',
    'verification_command:',
    'verification command:',
)
ALLOWED_CHANGE_PATH_PREFIXES = (
    'allowed_change_paths:',
    'allowed change paths:',
    'allowed_change_path:',
    'allowed change path:',
    'changed_files:',
    'changed files:',
)


class _AskSubmissionError(RuntimeError):
    def __init__(self, *, target: str, purpose: str, stage: str, error: str) -> None:
        super().__init__(error)
        self.target = target
        self.purpose = purpose
        self.stage = stage


class _AskWatchError(RuntimeError):
    def __init__(self, *, target: str, purpose: str, stage: str, job_id: str, error: str) -> None:
        super().__init__(error)
        self.target = target
        self.purpose = purpose
        self.stage = stage
        self.job_id = job_id


def run_ask_first_execution_round(context, command, services=None) -> dict[str, object]:
    deps = _deps(services)
    loop_id = str(getattr(command, 'loop_id', None) or '').strip()
    task_id = str(getattr(command, 'task_id', None) or '').strip()
    if not loop_id:
        raise ValueError('ask-first execution requires loop_id')
    if not task_id:
        raise ValueError('ask-first execution requires task_id')
    timeout = getattr(command, 'timeout_s', None)
    loop_dir = _loop_dir(context, loop_id)
    _ensure_loop_dirs(loop_dir)
    task_text = task_execution_text(context, task_id)
    task_record = _task_record(deps.plan_task(context, SimpleNamespace(action='task-show', task_id=task_id)))
    artifact_refs = _artifact_refs(task_record)
    project_root_authority_required = _requires_project_root_authority(task_record, task_text)
    worker_agent = f'loop-{loop_id}-{WORKER_PROFILE}-1'
    reviewer_agent = f'loop-{loop_id}-{REVIEWER_PROFILE}-1'
    started_at = _utc_now()
    _append_event(loop_dir, loop_id=loop_id, kind='ask_first_round_started', payload={'task_id': task_id})
    topology = _apply_mount_topology(
        context,
        deps,
        loop_dir=loop_dir,
        loop_id=loop_id,
        worker_agent=worker_agent,
        reviewer_agent=reviewer_agent,
    )
    topology_failure = _topology_blocker(topology)
    if topology_failure is not None:
        return _write_round_payload(
            context,
            loop_dir=loop_dir,
            loop_id=loop_id,
            task_id=task_id,
            started_at=started_at,
            status='blocked',
            worker_agent=worker_agent,
            reviewer_agent=reviewer_agent,
            artifact_refs=artifact_refs,
            topology=topology,
            worker={},
            reviewer={},
            rework={},
            orchestrator={},
            round_reviewer={},
            round_result='blocked',
            round_result_source=str(topology_failure.get('source') or 'topology_not_ready'),
            failure=topology_failure,
        )

    worker: dict[str, object] = {}
    reviewer: dict[str, object] = {}
    rework: dict[str, dict[str, object]] = {}
    orchestrator: dict[str, object] = {}
    round_reviewer: dict[str, object] = {}
    authority_update: dict[str, object] | None = None
    project_root_test: dict[str, object] | None = None
    stage = 'worker_ask'
    try:
        worker = _submit_and_watch(
            context,
            deps,
            loop_dir=loop_dir,
            loop_id=loop_id,
            target=worker_agent,
            sender=ORCHESTRATOR_TARGET,
            purpose='worker',
            task_id=f'{loop_id}-worker',
            message=_worker_message(
                loop_id=loop_id,
                task_id=task_id,
                task_text=task_text,
                artifact_refs=artifact_refs,
            ),
            timeout=timeout,
        )
        worker_status_failure = _round_status_failure(worker)
        if worker_status_failure is not None:
            return _write_round_payload(
                context,
                loop_dir=loop_dir,
                loop_id=loop_id,
                task_id=task_id,
                started_at=started_at,
                status='blocked',
                worker_agent=worker_agent,
                reviewer_agent=reviewer_agent,
                artifact_refs=artifact_refs,
                topology=topology,
                worker=worker,
                reviewer=reviewer,
                rework=rework,
                orchestrator=orchestrator,
                round_reviewer=round_reviewer,
                round_result='blocked',
                round_result_source=str(worker_status_failure.get('source') or 'ask_job_incomplete'),
                failure=worker_status_failure,
            )
        if project_root_authority_required:
            promoted, authority_failure = _promote_project_root_authority(
                context,
                round_result='pass',
                worker_agent=worker_agent,
                task_text=task_text,
            )
            if authority_failure is not None:
                return _write_round_payload(
                    context,
                    loop_dir=loop_dir,
                    loop_id=loop_id,
                    task_id=task_id,
                    started_at=started_at,
                    status='blocked',
                    worker_agent=worker_agent,
                    reviewer_agent=reviewer_agent,
                    artifact_refs=artifact_refs,
                    topology=topology,
                    worker=worker,
                    reviewer=reviewer,
                    rework=rework,
                    orchestrator=orchestrator,
                    round_reviewer=round_reviewer,
                    round_result='blocked',
                    round_result_source=str(authority_failure.get('source') or 'round_authority'),
                    failure=authority_failure,
                )
            authority_update = _merge_authority_updates(authority_update, promoted)
        stage = 'reviewer_ask'
        reviewer = _submit_and_watch(
            context,
            deps,
            loop_dir=loop_dir,
            loop_id=loop_id,
            target=reviewer_agent,
            sender=worker_agent,
            purpose='reviewer',
            task_id=f'{loop_id}-reviewer',
            message=_reviewer_message(
                loop_id=loop_id,
                task_id=task_id,
                task_text=task_text,
                artifact_refs=artifact_refs,
                worker=worker,
                authority_update=authority_update,
            ),
            timeout=timeout,
        )
        if _reviewer_requires_rework(reviewer):
            stage = 'worker_rework_ask'
            worker_rework = _submit_and_watch(
                context,
                deps,
                loop_dir=loop_dir,
                loop_id=loop_id,
                target=worker_agent,
                sender=reviewer_agent,
                purpose='worker_rework',
                task_id=f'{loop_id}-worker-rework',
                message=_worker_rework_message(
                    loop_id=loop_id,
                    task_id=task_id,
                    task_text=task_text,
                    artifact_refs=artifact_refs,
                    worker=worker,
                    reviewer=reviewer,
                ),
                timeout=timeout,
            )
            rework['worker_rework'] = worker_rework
            worker_rework_status_failure = _round_status_failure(worker_rework)
            if worker_rework_status_failure is not None:
                _restore_authority_update(
                    context,
                    authority_update,
                    worker_rework_status_failure,
                    reason='worker_rework_not_completed',
                )
                return _write_round_payload(
                    context,
                    loop_dir=loop_dir,
                    loop_id=loop_id,
                    task_id=task_id,
                    started_at=started_at,
                    status='blocked',
                    worker_agent=worker_agent,
                    reviewer_agent=reviewer_agent,
                    artifact_refs=artifact_refs,
                    topology=topology,
                    worker=worker,
                    reviewer=reviewer,
                    rework=rework,
                    orchestrator=orchestrator,
                    round_reviewer=round_reviewer,
                    round_result='blocked',
                    round_result_source=str(worker_rework_status_failure.get('source') or 'ask_job_incomplete'),
                    failure=worker_rework_status_failure,
                    authority_update=_public_authority_update(authority_update),
                )
            if project_root_authority_required:
                promoted, authority_failure = _promote_project_root_authority(
                    context,
                    round_result='pass',
                    worker_agent=worker_agent,
                    task_text=task_text,
                    allow_noop_verified=authority_update is not None,
                )
                if authority_failure is not None:
                    _restore_authority_update(
                        context,
                        authority_update,
                        authority_failure,
                        reason='worker_rework_promotion_failed',
                    )
                    return _write_round_payload(
                        context,
                        loop_dir=loop_dir,
                        loop_id=loop_id,
                        task_id=task_id,
                        started_at=started_at,
                        status='blocked',
                        worker_agent=worker_agent,
                        reviewer_agent=reviewer_agent,
                        artifact_refs=artifact_refs,
                        topology=topology,
                        worker=worker,
                        reviewer=reviewer,
                        rework=rework,
                        orchestrator=orchestrator,
                        round_reviewer=round_reviewer,
                        round_result='blocked',
                        round_result_source=str(authority_failure.get('source') or 'round_authority'),
                        failure=authority_failure,
                        authority_update=_public_authority_update(authority_update),
                    )
                authority_update = _merge_authority_updates(authority_update, promoted)
            stage = 'reviewer_recheck_ask'
            reviewer_recheck = _submit_and_watch(
                context,
                deps,
                loop_dir=loop_dir,
                loop_id=loop_id,
                target=reviewer_agent,
                sender=worker_agent,
                purpose='reviewer_recheck',
                task_id=f'{loop_id}-reviewer-recheck',
                message=_reviewer_recheck_message(
                    loop_id=loop_id,
                    task_id=task_id,
                    task_text=task_text,
                    artifact_refs=artifact_refs,
                    worker=worker,
                    reviewer=reviewer,
                    worker_rework=worker_rework,
                    authority_update=authority_update,
                ),
                timeout=timeout,
            )
            rework['reviewer_recheck'] = reviewer_recheck
        stage = 'orchestrator_ask'
        orchestrator = _submit_and_watch(
            context,
            deps,
            loop_dir=loop_dir,
            loop_id=loop_id,
            target=ORCHESTRATOR_TARGET,
            sender='system',
            purpose='orchestrator',
            task_id=f'{loop_id}-orchestrator',
            message=_orchestrator_message(
                loop_id=loop_id,
                task_id=task_id,
                task_text=task_text,
                artifact_refs=artifact_refs,
                worker=worker,
                reviewer=reviewer,
                rework=rework,
                authority_update=authority_update,
            ),
            timeout=timeout,
        )
        stage = 'ccb_round_reviewer_ask'
        round_reviewer = _submit_and_watch(
            context,
            deps,
            loop_dir=loop_dir,
            loop_id=loop_id,
            target=ROUND_REVIEWER_TARGET,
            sender='system',
            purpose=ROUND_REVIEWER_FIELD,
            task_id=f'{loop_id}-round-reviewer',
            message=_round_reviewer_message(
                loop_id=loop_id,
                task_id=task_id,
                task_text=task_text,
                artifact_refs=artifact_refs,
                worker=worker,
                reviewer=reviewer,
                rework=rework,
                orchestrator=orchestrator,
                authority_update=authority_update,
            ),
            timeout=timeout,
        )
    except Exception as exc:
        failure = _ask_failure_record(exc, default_stage=stage)
        _restore_authority_update(context, authority_update, failure, reason='ask_failure_after_promotion')
        return _write_round_payload(
            context,
            loop_dir=loop_dir,
            loop_id=loop_id,
            task_id=task_id,
            started_at=started_at,
            status='blocked',
            worker_agent=worker_agent,
            reviewer_agent=reviewer_agent,
            artifact_refs=artifact_refs,
            topology=topology,
            worker=worker,
            reviewer=reviewer,
            rework=rework,
            orchestrator=orchestrator,
            round_reviewer=round_reviewer,
            round_result='blocked',
            round_result_source=str(failure.get('source') or 'ask_failure'),
            failure=failure,
            authority_update=_public_authority_update(authority_update),
        )

    status_items = [worker, reviewer, *rework.values(), orchestrator, round_reviewer]
    status = _round_status(*status_items)
    status_failure = _round_status_failure(*status_items)
    if status_failure is not None:
        _restore_authority_update(context, authority_update, status_failure, reason='ask_job_incomplete_after_promotion')
        return _write_round_payload(
            context,
            loop_dir=loop_dir,
            loop_id=loop_id,
            task_id=task_id,
            started_at=started_at,
            status='blocked',
            worker_agent=worker_agent,
            reviewer_agent=reviewer_agent,
            artifact_refs=artifact_refs,
            topology=topology,
            worker=worker,
            reviewer=reviewer,
            rework=rework,
            orchestrator=orchestrator,
            round_reviewer=round_reviewer,
            round_result='blocked',
            round_result_source=str(status_failure.get('source') or 'ask_job_incomplete'),
            failure=status_failure,
            authority_update=_public_authority_update(authority_update),
        )
    round_result, round_result_source, failure = _round_result(
        {'loop_run_status': status, ROUND_REVIEWER_FIELD: round_reviewer}
    )
    if failure is not None:
        _restore_authority_update(context, authority_update, failure, reason='round_result_failure_after_promotion')
        status = 'blocked'
    elif round_result != 'pass':
        _restore_authority_update(
            context,
            authority_update,
            None,
            reason=f'non_pass_round_result:{round_result}',
        )
    if failure is None and round_result == 'pass':
        project_root_test, test_failure = _project_root_test_authority(
            context,
            loop_dir=loop_dir,
            task_text=task_text,
        )
        if test_failure is not None:
            _restore_authority_update(
                context,
                authority_update,
                test_failure,
                reason='project_root_test_failed',
            )
            status = 'blocked'
            round_result = 'blocked'
            round_result_source = str(test_failure['source'])
            failure = test_failure
    authority_update = _public_authority_update(authority_update)
    return _write_round_payload(
        context,
        loop_dir=loop_dir,
        loop_id=loop_id,
        task_id=task_id,
        started_at=started_at,
        status=status,
        worker_agent=worker_agent,
        reviewer_agent=reviewer_agent,
        artifact_refs=artifact_refs,
        topology=topology,
        worker=worker,
        reviewer=reviewer,
        rework=rework,
        orchestrator=orchestrator,
        round_reviewer=round_reviewer,
        round_result=round_result,
        round_result_source=round_result_source,
        failure=failure,
        authority_update=authority_update,
        project_root_test=project_root_test,
    )


def release_ask_first_execution_round(context, round_payload: dict[str, object], services=None) -> dict[str, object]:
    deps = _deps(services)
    loop_id = str(round_payload.get('loop_id') or '').strip()
    if not loop_id:
        raise ValueError('ask-first release requires loop_id')
    release = deps.loop_topology(
        context,
        SimpleNamespace(action='release', loop_id=loop_id, policy='auto', idle_only=True, json_output=True),
    )
    round_payload.setdefault('topology', {})
    topology = round_payload['topology']
    if isinstance(topology, dict):
        topology['release'] = release
    round_path = _round_json_path(round_payload)
    if round_path is not None:
        atomic_write_json(round_path, round_payload)
    return release


def _deps(services):
    services = services or SimpleNamespace()
    return SimpleNamespace(
        loop_topology=getattr(services, 'loop_topology', loop_topology),
        plan_task=getattr(services, 'plan_task', plan_task),
        submit_ask=getattr(services, 'submit_ask', submit_ask),
        watch_ask_job=getattr(services, 'watch_ask_job', watch_ask_job),
    )


def _apply_mount_topology(
    context,
    deps,
    *,
    loop_dir: Path,
    loop_id: str,
    worker_agent: str,
    reviewer_agent: str,
) -> dict[str, object]:
    proposal_path = loop_dir / 'ask_first_mount_topology.proposal.json'
    proposal = {
        'schema': 'ccb.loop.agent_mount_topology.v1',
        'release_policy': {'policy': 'auto', 'idle_only': True},
        'windows': [
            {
                'name': 'ccb-exec',
                'class': 'execution',
                'max_panes': 6,
                'layout_policy': 'append-or-create-window',
            }
        ],
        'agents': [
            {
                'id': worker_agent,
                'profile': WORKER_PROFILE,
                'desired_state': 'present',
                'window_name': 'ccb-exec',
                'pane_order': 0,
                'lifecycle': 'ephemeral',
                'release_policy': 'auto',
            },
            {
                'id': reviewer_agent,
                'profile': REVIEWER_PROFILE,
                'desired_state': 'present',
                'window_name': 'ccb-exec',
                'pane_order': 1,
                'lifecycle': 'ephemeral',
                'release_policy': 'auto',
            },
        ],
    }
    atomic_write_json(proposal_path, proposal)
    proposed: dict[str, object] = {}
    committed: dict[str, object] = {}
    try:
        proposed = deps.loop_topology(
            context,
            SimpleNamespace(
                action='propose',
                loop_id=loop_id,
                from_path=str(proposal_path),
                proposal_id='ask-first-execution',
                json_output=True,
            ),
        )
        committed = deps.loop_topology(
            context,
            SimpleNamespace(
                action='commit',
                loop_id=loop_id,
                proposal_id='ask-first-execution',
                apply=True,
                json_output=True,
            ),
        )
        status = deps.loop_topology(context, SimpleNamespace(action='status', loop_id=loop_id, json_output=True))
    except Exception as exc:
        status = _topology_status_after_failure(context, deps, loop_id=loop_id)
        return {
            'proposal_source_path': str(proposal_path),
            'propose': proposed,
            'commit': committed,
            'status': status,
            'failure': _failure_record(source='topology_apply_failed', stage='topology', exc=exc),
        }
    return {
        'proposal_source_path': str(proposal_path),
        'propose': proposed,
        'commit': committed,
        'status': status,
    }


def _topology_status_after_failure(context, deps, *, loop_id: str) -> dict[str, object]:
    try:
        status = deps.loop_topology(context, SimpleNamespace(action='status', loop_id=loop_id, json_output=True))
    except Exception as exc:
        return {
            'loop_topology_status': 'unknown',
            'status_failure': _failure_record(source='topology_status_failed', stage='topology', exc=exc),
        }
    return status if isinstance(status, dict) else {}


def _topology_blocker(topology: dict[str, object]) -> dict[str, object] | None:
    failure = topology.get('failure') if isinstance(topology.get('failure'), dict) else None
    if failure is not None:
        return dict(failure)
    status_payload = topology.get('status') if isinstance(topology.get('status'), dict) else {}
    status = str(status_payload.get('loop_topology_status') or '').strip()
    if status == 'ready':
        return None
    return {
        'source': 'topology_not_ready',
        'stage': 'topology',
        'reason': f'mount topology status {status or "missing"}; expected ready',
        'error': f'mount topology status {status or "missing"}; expected ready',
        'loop_topology_status': status or None,
        'desired_path': status_payload.get('desired_path'),
        'observed_path': status_payload.get('observed_path'),
        'topology_status': status_payload,
        'topology_drift': _topology_status_drift(status_payload),
        'retained': _topology_status_retained(status_payload),
    }


def _failure_record(*, source: str, stage: str, exc: Exception) -> dict[str, object]:
    return {
        'source': source,
        'stage': stage,
        'error_type': exc.__class__.__name__,
        'error': str(exc),
        'reason': str(exc),
    }


def _topology_status_drift(status_payload: dict[str, object]) -> object:
    observed = status_payload.get('observed') if isinstance(status_payload.get('observed'), dict) else {}
    return observed.get('drift')


def _topology_status_retained(status_payload: dict[str, object]) -> object:
    observed = status_payload.get('observed') if isinstance(status_payload.get('observed'), dict) else {}
    return observed.get('retained') or observed.get('retained_count')


def _ask_failure_record(exc: Exception, *, default_stage: str) -> dict[str, object]:
    source = 'ask_failure'
    if isinstance(exc, _AskSubmissionError):
        source = 'ask_submission_failed'
    elif isinstance(exc, _AskWatchError):
        source = 'watch_failed'
    stage = str(getattr(exc, 'stage', '') or default_stage)
    failure = _failure_record(source=source, stage=stage, exc=exc)
    target = getattr(exc, 'target', None)
    job_id = getattr(exc, 'job_id', None)
    purpose = getattr(exc, 'purpose', None)
    if target:
        failure['target'] = str(target)
    if job_id:
        failure['job_id'] = str(job_id)
    if purpose:
        failure['purpose'] = str(purpose)
    return failure


def _write_round_payload(
    context,
    *,
    loop_dir: Path,
    loop_id: str,
    task_id: str,
    started_at: str,
    status: str,
    worker_agent: str,
    reviewer_agent: str,
    artifact_refs: dict[str, str],
    topology: dict[str, object],
    worker: dict[str, object],
    reviewer: dict[str, object],
    rework: dict[str, dict[str, object]],
    orchestrator: dict[str, object],
    round_reviewer: dict[str, object],
    round_result: str,
    round_result_source: str,
    failure: dict[str, object] | None = None,
    authority_update: dict[str, object] | None = None,
    project_root_test: dict[str, object] | None = None,
) -> dict[str, object]:
    round_summary_path = loop_dir / 'round_summary.md'
    atomic_write_text(
        round_summary_path,
        _round_summary_text(
            loop_id=loop_id,
            task_id=task_id,
            result=round_result,
            result_source=round_result_source,
            artifact_refs=artifact_refs,
            topology=topology,
            worker=worker,
            reviewer=reviewer,
            rework=rework or {},
            orchestrator=orchestrator,
            round_reviewer=round_reviewer,
            failure=failure,
            authority_update=authority_update,
            project_root_test=project_root_test,
        ),
    )
    finished_at = _utc_now()
    payload = {
        'schema_version': 1,
        'record_type': 'ccb_loop_ask_first_execution_round',
        'loop_run_status': status,
        'dispatch_source': 'ask_first_mount_topology',
        'loop_id': loop_id,
        'task_id': task_id,
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'started_at': started_at,
        'finished_at': finished_at,
        'profiles': {
            'worker': WORKER_PROFILE,
            'reviewer': REVIEWER_PROFILE,
        },
        'agents': {
            'worker': worker_agent,
            'reviewer': reviewer_agent,
            'orchestrator': ORCHESTRATOR_TARGET,
            ROUND_REVIEWER_FIELD: ROUND_REVIEWER_TARGET,
        },
        'legacy_aliases': {
            LEGACY_ROUND_CHECKER_FIELD: {
                'field': ROUND_REVIEWER_FIELD,
                'target': ROUND_REVIEWER_TARGET,
                'purpose': 'compatibility_only',
            }
        },
        'artifact_refs': artifact_refs,
        'topology': topology,
        'worker': worker,
        'reviewer': reviewer,
        'rework': rework or {},
        'orchestrator': orchestrator,
        ROUND_REVIEWER_FIELD: round_reviewer,
        'round_result': round_result,
        'round_result_source': round_result_source,
        'paths': {
            'round': str(round_summary_path),
            'round_json': str(loop_dir / 'round.json'),
            'asks': str(loop_dir / 'asks.jsonl'),
            'events': str(loop_dir / 'events.jsonl'),
            'artifacts': str(loop_dir / 'artifacts'),
        },
    }
    if failure is not None:
        payload['failure'] = failure
    if authority_update is not None:
        payload['authority_update'] = authority_update
    if project_root_test is not None:
        payload['project_root_test'] = project_root_test
    atomic_write_json(loop_dir / 'round.json', payload)
    event_payload = {'task_id': task_id, 'status': status, 'round_result': round_result}
    if failure is not None:
        event_payload['round_result_source'] = round_result_source
        event_payload['failure_stage'] = failure.get('stage')
    if authority_update is not None:
        event_payload['authority_update_source'] = authority_update.get('source')
    _append_event(loop_dir, loop_id=loop_id, kind='ask_first_round_finished', payload=event_payload)
    return payload


def _submit_and_watch(
    context,
    deps,
    *,
    loop_dir: Path,
    loop_id: str,
    target: str,
    sender: str,
    purpose: str,
    task_id: str,
    message: str,
    timeout: float | None,
) -> dict[str, object]:
    stage = f'{purpose}_ask'
    try:
        summary = deps.submit_ask(
            context,
            ParsedAskCommand(
                project=None,
                target=target,
                sender=RUNNER_ASK_SENDER,
                message=message,
                task_id=task_id,
            ),
        )
    except Exception as exc:
        raise _AskSubmissionError(target=target, purpose=purpose, stage=stage, error=str(exc)) from exc
    job = _single_job(summary.jobs, target=target)
    job_id = str(job['job_id'])
    _append_ask(loop_dir, loop_id=loop_id, target=target, sender=RUNNER_ASK_SENDER, purpose=purpose, job_id=job_id)
    try:
        batch = deps.watch_ask_job(context, job_id, StringIO(), timeout=timeout, emit_output=False)
    except Exception as exc:
        raise _AskWatchError(target=target, purpose=purpose, stage=stage, job_id=job_id, error=str(exc)) from exc
    result = {
        'target': target,
        'sender': RUNNER_ASK_SENDER,
        'logical_sender': sender,
        'purpose': purpose,
        'job_id': job_id,
        'status': batch.status,
        'reply': batch.reply,
        'terminal': bool(batch.terminal),
    }
    artifact_path = loop_dir / 'artifacts' / f'{purpose}-reply.md'
    atomic_write_text(artifact_path, batch.reply or '')
    result['artifact'] = str(artifact_path)
    _append_event(
        loop_dir,
        loop_id=loop_id,
        kind='ask_terminal',
        payload={'purpose': purpose, 'target': target, 'job_id': job_id, 'status': batch.status},
    )
    return result


def _worker_message(
    *,
    loop_id: str,
    task_id: str,
    task_text: str,
    artifact_refs: dict[str, str],
) -> str:
    return (
        f'Loop: {loop_id}\n'
        'Role: worker\n'
        f'Task: {task_id}\n'
        f"task_packet: {artifact_refs.get('task_packet')}\n"
        f"execution_contract: {artifact_refs.get('execution_contract')}\n\n"
        'Task packet and execution contract evidence:\n'
        f'{task_text}\n\n'
        'Output requirements:\n'
        '- status: done|blocked|needs_rework\n'
        '- cite task_packet and execution_contract in the work summary\n'
        '- evidence or artifact refs\n'
        '- no hidden fallback or scope shrinkage'
    )


def _reviewer_message(
    *,
    loop_id: str,
    task_id: str,
    task_text: str,
    artifact_refs: dict[str, str],
    worker: dict[str, object],
    authority_update: dict[str, object] | None = None,
) -> str:
    authority_lines = _authority_update_evidence_lines(authority_update)
    return (
        f'Loop: {loop_id}\n'
        'Role: code_reviewer\n'
        f'Task: {task_id}\n'
        f"task_packet: {artifact_refs.get('task_packet')}\n"
        f"execution_contract: {artifact_refs.get('execution_contract')}\n"
        f'Worker job: {worker.get("job_id")}\n'
        f'Worker reply artifact: {worker.get("artifact")}\n\n'
        f'{authority_lines}'
        'Task packet and execution contract evidence:\n'
        f'{task_text}\n\n'
        'Required contract audit:\n'
        '- validate the task result against project-root evidence after script-owned promotion\n'
        '- explicitly check execution_contract before accepting the round\n'
        '- reject hidden fallback, scope shrink, and fake success\n'
        '- reject pass if required evidence is missing or only implied by provider reply text\n\n'
        'Output requirements:\n'
        '- status: pass|rework_required|blocked|non_converged\n'
        '- execution_contract audit: pass|fail with evidence refs\n'
        '- verification checks performed\n'
        '- concise risk notes'
    )


def _reviewer_requires_rework(reviewer: dict[str, object]) -> bool:
    reply = str(reviewer.get('reply') or '').lower()
    for raw_line in reply.splitlines():
        line = raw_line.strip().lstrip('-').strip()
        if not line.startswith('status:'):
            continue
        value = line.split(':', 1)[1].strip().split()[0].strip('`.,;')
        return value == 'rework_required'
    return False


def _worker_rework_message(
    *,
    loop_id: str,
    task_id: str,
    task_text: str,
    artifact_refs: dict[str, str],
    worker: dict[str, object],
    reviewer: dict[str, object],
) -> str:
    return (
        f'Loop: {loop_id}\n'
        'Role: worker\n'
        'Purpose: bounded_rework\n'
        f'Task: {task_id}\n'
        f"task_packet: {artifact_refs.get('task_packet')}\n"
        f"execution_contract: {artifact_refs.get('execution_contract')}\n"
        f'Initial worker job: {worker.get("job_id")} status={worker.get("status")}\n'
        f'Reviewer rejection job: {reviewer.get("job_id")} status={reviewer.get("status")}\n'
        f'Reviewer rejection artifact: {reviewer.get("artifact")}\n\n'
        'Task packet and execution contract evidence:\n'
        f'{task_text}\n\n'
        'Output requirements:\n'
        '- status: done|blocked\n'
        '- address exactly the reviewer rejection evidence\n'
        '- cite task_packet, execution_contract, and reviewer rejection artifact\n'
        '- no hidden fallback or scope shrinkage'
    )


def _reviewer_recheck_message(
    *,
    loop_id: str,
    task_id: str,
    task_text: str,
    artifact_refs: dict[str, str],
    worker: dict[str, object],
    reviewer: dict[str, object],
    worker_rework: dict[str, object],
    authority_update: dict[str, object] | None = None,
) -> str:
    authority_lines = _authority_update_evidence_lines(authority_update)
    return (
        f'Loop: {loop_id}\n'
        'Role: code_reviewer\n'
        'Purpose: bounded_rework_recheck\n'
        f'Task: {task_id}\n'
        f"task_packet: {artifact_refs.get('task_packet')}\n"
        f"execution_contract: {artifact_refs.get('execution_contract')}\n"
        f'Initial worker job: {worker.get("job_id")} status={worker.get("status")}\n'
        f'Initial reviewer job: {reviewer.get("job_id")} status={reviewer.get("status")}\n'
        f'Rework worker job: {worker_rework.get("job_id")} status={worker_rework.get("status")}\n'
        f'Rework artifact: {worker_rework.get("artifact")}\n\n'
        f'{authority_lines}'
        'Task packet and execution contract evidence:\n'
        f'{task_text}\n\n'
        'Output requirements:\n'
        '- status: pass|rework_required|blocked|non_converged\n'
        '- this is the only bounded rework recheck for the round\n'
        '- cite execution_contract and rework evidence before accepting'
    )


def _orchestrator_message(
    *,
    loop_id: str,
    task_id: str,
    task_text: str,
    artifact_refs: dict[str, str],
    worker: dict[str, object],
    reviewer: dict[str, object],
    rework: dict[str, dict[str, object]],
    authority_update: dict[str, object] | None = None,
) -> str:
    rework_lines = _rework_evidence_lines(rework)
    authority_lines = _authority_update_evidence_lines(authority_update)
    return (
        f'Loop: {loop_id}\n'
        'Role: ccb_orchestrator\n'
        f'Task: {task_id}\n'
        f"task_packet: {artifact_refs.get('task_packet')}\n"
        f"execution_contract: {artifact_refs.get('execution_contract')}\n"
        f'Worker job: {worker.get("job_id")} status={worker.get("status")}\n'
        f'Reviewer job: {reviewer.get("job_id")} status={reviewer.get("status")}\n\n'
        f'{rework_lines}'
        f'{authority_lines}'
        'Task packet and execution contract evidence:\n'
        f'{task_text}\n\n'
        'Output requirements:\n'
        '- summarize worker/reviewer evidence without changing task authority\n'
        '- cite task_packet and execution_contract\n'
        '- release readiness for ephemeral execution agents'
    )


def _round_reviewer_message(
    *,
    loop_id: str,
    task_id: str,
    task_text: str,
    artifact_refs: dict[str, str],
    worker: dict[str, object],
    reviewer: dict[str, object],
    rework: dict[str, dict[str, object]],
    orchestrator: dict[str, object],
    authority_update: dict[str, object] | None = None,
) -> str:
    rework_lines = _rework_evidence_lines(rework)
    authority_lines = _authority_update_evidence_lines(authority_update)
    return (
        f'Loop: {loop_id}\n'
        'Role: ccb_round_reviewer\n'
        f'Task: {task_id}\n'
        f"task_packet: {artifact_refs.get('task_packet')}\n"
        f"execution_contract: {artifact_refs.get('execution_contract')}\n"
        f'Worker job: {worker.get("job_id")} status={worker.get("status")}\n'
        f'Reviewer job: {reviewer.get("job_id")} status={reviewer.get("status")}\n'
        f'Orchestrator job: {orchestrator.get("job_id")} status={orchestrator.get("status")}\n\n'
        f'{rework_lines}'
        f'{authority_lines}'
        'Task packet and execution contract evidence:\n'
        f'{task_text}\n\n'
        'Output requirements:\n'
        '- round result: pass|partial|replan_required|blocked\n'
        '- validate final result against project-root evidence, not isolated worker workspace evidence\n'
        '- verification performed against execution_contract\n'
        '- hidden fallback/scope shrink/fake success audit\n'
        '- evidence refs\n'
        '- recommended next owner'
    )


def _rework_evidence_lines(rework: dict[str, dict[str, object]]) -> str:
    if not rework:
        return ''
    lines = ['Bounded rework evidence:']
    for purpose, evidence in rework.items():
        lines.append(
            f'- {purpose}: target={evidence.get("target")} job={evidence.get("job_id")} '
            f'status={evidence.get("status")} artifact={evidence.get("artifact")}'
        )
    lines.append('')
    return '\n'.join(lines)


def _authority_update_evidence_lines(authority_update: dict[str, object] | None) -> str:
    if not isinstance(authority_update, dict):
        return ''
    changed_files = authority_update.get('changed_files') if isinstance(authority_update.get('changed_files'), list) else []
    allowed_change_paths = (
        authority_update.get('allowed_change_paths')
        if isinstance(authority_update.get('allowed_change_paths'), list)
        else []
    )
    lines = [
        'Project-root authority evidence:',
        f'- source: {authority_update.get("source")}',
        f'- operation: {authority_update.get("operation")}',
        f'- worker_workspace: {authority_update.get("workspace_path")}',
        f'- project_root: {authority_update.get("project_root")}',
        f'- verified_project_root: {authority_update.get("verified_project_root")}',
    ]
    if changed_files:
        lines.append(f'- changed_files: {", ".join(str(path) for path in changed_files)}')
    if allowed_change_paths:
        lines.append(f'- allowed_change_paths: {", ".join(str(path) for path in allowed_change_paths)}')
    lines.append('')
    return '\n'.join(lines) + '\n'


def _round_summary_text(
    *,
    loop_id: str,
    task_id: str,
    result: str,
    result_source: str,
    artifact_refs: dict[str, str],
    topology: dict[str, object],
    worker: dict[str, object],
    reviewer: dict[str, object],
    rework: dict[str, dict[str, object]],
    orchestrator: dict[str, object],
    round_reviewer: dict[str, object],
    failure: dict[str, object] | None = None,
    authority_update: dict[str, object] | None = None,
    project_root_test: dict[str, object] | None = None,
) -> str:
    topology_status = topology.get('status') if isinstance(topology.get('status'), dict) else {}
    commit = topology.get('commit') if isinstance(topology.get('commit'), dict) else {}
    reconcile = commit.get('reconcile') if isinstance(commit, dict) and isinstance(commit.get('reconcile'), dict) else {}
    lines = [
        '# Round Summary',
        '',
        f'task_id: {task_id}',
        f'loop_id: {loop_id}',
        f'round result: {result}',
        f'round_result_source: {result_source}',
        f"task_packet: {artifact_refs.get('task_packet')}",
        f"execution_contract: {artifact_refs.get('execution_contract')}",
        '',
        '## Ask Evidence',
        '',
        f"- worker: {worker.get('target')} job={worker.get('job_id')} status={worker.get('status')} artifact={worker.get('artifact')}",
        f"- reviewer: {reviewer.get('target')} job={reviewer.get('job_id')} status={reviewer.get('status')} artifact={reviewer.get('artifact')}",
        f"- orchestrator: {orchestrator.get('target')} job={orchestrator.get('job_id')} status={orchestrator.get('status')} artifact={orchestrator.get('artifact')}",
        f"- ccb_round_reviewer: {round_reviewer.get('target')} job={round_reviewer.get('job_id')} status={round_reviewer.get('status')} artifact={round_reviewer.get('artifact')}",
        '',
        '## Topology Evidence',
        '',
        f"- desired: {commit.get('desired_path')}",
        f"- observed: {reconcile.get('observed_path') or topology_status.get('observed_path')}",
        f"- status: {topology_status.get('loop_topology_status')}",
        f"- release policy: auto after round_summary import",
        '',
        ]
    if rework:
        insertion = 14
        lines[insertion:insertion] = [
            f"- {purpose}: {evidence.get('target')} job={evidence.get('job_id')} "
            f"status={evidence.get('status')} artifact={evidence.get('artifact')}"
            for purpose, evidence in rework.items()
        ]
    if authority_update is not None:
        changed_files = (
            authority_update.get('changed_files')
            if isinstance(authority_update.get('changed_files'), list)
            else []
        )
        allowed_change_paths = (
            authority_update.get('allowed_change_paths')
            if isinstance(authority_update.get('allowed_change_paths'), list)
            else []
        )
        lines.extend(
            [
                '## Authority Update',
                '',
                f"- source: {authority_update.get('source')}",
                f"- stage: {authority_update.get('stage')}",
                f"- operation: {authority_update.get('operation')}",
                f"- worker_agent: {authority_update.get('worker_agent')}",
                f"- workspace_mode: {authority_update.get('workspace_mode')}",
                f"- verified_project_root: {authority_update.get('verified_project_root')}",
            ]
        )
        if changed_files:
            lines.append(f"- changed_files: {', '.join(str(path) for path in changed_files)}")
        if allowed_change_paths:
            lines.append(f"- allowed_change_paths: {', '.join(str(path) for path in allowed_change_paths)}")
        lines.append('')
    if project_root_test is not None:
        lines.extend(
            [
                '## Project Root Test',
                '',
                f"- test_command: {project_root_test.get('test_command')}",
                f"- test_cwd: {project_root_test.get('test_cwd')}",
                f"- test_resolution_path: {project_root_test.get('test_resolution_path')}",
                f"- test_result: {project_root_test.get('test_result')}",
                f"- test_file_resolved_to_lab: {project_root_test.get('test_file_resolved_to_lab')}",
                f"- test_sys_path_project_first: {project_root_test.get('test_sys_path_project_first')}",
                '',
            ]
        )
    if failure is not None:
        changed_files = failure.get('changed_files') if isinstance(failure.get('changed_files'), list) else []
        deleted_files = failure.get('deleted_files') if isinstance(failure.get('deleted_files'), list) else []
        allowed_change_paths = (
            failure.get('allowed_change_paths')
            if isinstance(failure.get('allowed_change_paths'), list)
            else []
        )
        out_of_scope_files = (
            failure.get('out_of_scope_files')
            if isinstance(failure.get('out_of_scope_files'), list)
            else []
        )
        lines.extend(
            [
                '## Blocker Evidence',
                '',
                f"- source: {failure.get('source')}",
                f"- stage: {failure.get('stage')}",
                f"- error_type: {failure.get('error_type')}",
                f"- error: {failure.get('error')}",
                f"- loop_topology_status: {failure.get('loop_topology_status')}",
            ]
        )
        if changed_files:
            lines.append(f"- changed_files: {', '.join(str(path) for path in changed_files)}")
        if allowed_change_paths:
            lines.append(f"- allowed_change_paths: {', '.join(str(path) for path in allowed_change_paths)}")
        if out_of_scope_files:
            lines.append(f"- out_of_scope_files: {', '.join(str(path) for path in out_of_scope_files)}")
        if deleted_files:
            lines.append(f"- deleted_files: {', '.join(str(path) for path in deleted_files)}")
        if failure.get('changed_file_count') is not None:
            lines.append(f"- changed_file_count: {failure.get('changed_file_count')}")
        lines.append('')
    lines.extend(
        [
            '## Contract Audit',
            '',
            '- reviewer was instructed to check execution_contract explicitly',
            '- hidden fallback, scope shrink, and fake success are rejected conditions',
            '',
        ]
    )
    return '\n'.join(lines)


def _round_result(payload: dict[str, object]) -> tuple[str, str, dict[str, object] | None]:
    declared, unknown, source_field = _declared_round_result(payload)
    if declared is not None:
        source = 'round_checker_reply' if source_field == LEGACY_ROUND_CHECKER_FIELD else 'round_reviewer_reply'
        return declared, source, None
    if unknown:
        return (
            'blocked',
            'unknown_round_result',
            {
                'source': 'unknown_round_result',
                'stage': 'round_result',
                'reason': f'unknown round result {unknown!r}',
                'error': f'unknown round result {unknown!r}',
                'unknown_round_result': unknown,
            },
        )
    if str(payload.get('loop_run_status') or '') == 'ok':
        return 'blocked', 'missing_round_reviewer_result', None
    return (
        'blocked',
        'loop_run_status',
        {
            'source': 'loop_run_status',
            'stage': 'round_status',
            'error': f"loop run status {payload.get('loop_run_status') or 'missing'}",
        },
    )


def _declared_round_result(payload: dict[str, object]) -> tuple[str | None, str | None, str]:
    reviewer = payload.get(ROUND_REVIEWER_FIELD) if isinstance(payload.get(ROUND_REVIEWER_FIELD), dict) else {}
    source_field = ROUND_REVIEWER_FIELD
    if not reviewer and isinstance(payload.get(LEGACY_ROUND_CHECKER_FIELD), dict):
        reviewer = payload[LEGACY_ROUND_CHECKER_FIELD]
        source_field = LEGACY_ROUND_CHECKER_FIELD
    reply = str(reviewer.get('reply') or '')
    mapping = {
        'pass': 'pass',
        'partial': 'partial',
        'replan_required': 'replan_required',
        'blocked': 'blocked',
        'global_blocker': 'blocked',
    }
    for raw_line in reply.splitlines():
        line = raw_line.strip().lower().lstrip('-').strip()
        if not line.startswith('round result:'):
            continue
        value = line.split(':', 1)[1].strip().split()[0].strip('`.,;')
        if value not in mapping:
            return None, value, source_field
        return mapping[value], None, source_field
    return None, None, source_field


def _round_status(*results: dict[str, object]) -> str:
    if all(str(result.get('status') or '') == 'completed' for result in results):
        return 'ok'
    return 'incomplete'


def _round_status_failure(*results: dict[str, object]) -> dict[str, object] | None:
    for result in results:
        status = str(result.get('status') or '').strip()
        if status == 'completed':
            continue
        purpose = str(result.get('purpose') or 'ask').strip()
        reason = f'{purpose} job status {status or "missing"}; expected completed'
        failure: dict[str, object] = {
            'source': 'ask_job_incomplete',
            'stage': f'{purpose}_ask',
            'reason': reason,
            'error': reason,
            'target': result.get('target'),
            'job_id': result.get('job_id'),
            'job_status': status or None,
        }
        return failure
    return None


def _promote_project_root_authority(
    context,
    *,
    round_result: str,
    worker_agent: str,
    task_text: str,
    allow_noop_verified: bool = False,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    if round_result != 'pass':
        return None, None
    binding_path = _workspace_binding_path(context, worker_agent)
    if not binding_path.is_file():
        configured_mode = _configured_workspace_mode(context, WORKER_PROFILE)
        if configured_mode == 'inplace':
            return None, None
        reason = (
            'round reviewer declared pass, but worker workspace binding is missing; '
            'configured non-inplace workers require script-owned project-root promotion evidence'
        )
        return None, {
            'source': 'workspace_binding_missing',
            'stage': 'round_authority',
            'reason': reason,
            'error': reason,
            'worker_agent': worker_agent,
            'workspace_mode_configured': configured_mode,
            'workspace_binding': str(binding_path),
            'project_root': str(context.project.project_root),
            'changed_files': [],
        }
    try:
        binding = json.loads(binding_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        reason = f'workspace binding {binding_path} is not valid JSON: {exc}'
        return None, {
            'source': 'workspace_binding_invalid',
            'stage': 'round_authority',
            'reason': reason,
            'error': reason,
            'worker_agent': worker_agent,
            'workspace_binding': str(binding_path),
        }
    except Exception as exc:
        return None, _failure_record(source='workspace_binding_unreadable', stage='round_authority', exc=exc)
    if not isinstance(binding, dict):
        reason = f'workspace binding {binding_path} is not an object'
        return None, {
            'source': 'workspace_binding_invalid',
            'stage': 'round_authority',
            'error': reason,
            'reason': reason,
            'workspace_binding': str(binding_path),
    }
    workspace_mode = str(binding.get('workspace_mode') or '').strip()
    if not workspace_mode:
        reason = f'workspace binding {binding_path} does not declare workspace_mode'
        return None, {
            'source': 'workspace_binding_invalid',
            'stage': 'round_authority',
            'reason': reason,
            'error': reason,
            'worker_agent': worker_agent,
            'workspace_binding': str(binding_path),
        }
    if workspace_mode == 'inplace':
        return None, None
    workspace_path = _workspace_path_from_binding(context, worker_agent, binding)
    if not str(binding.get('workspace_path') or '').strip():
        reason = f'workspace binding {binding_path} does not declare workspace_path'
        return None, {
            'source': 'workspace_binding_invalid',
            'stage': 'round_authority',
            'reason': reason,
            'error': reason,
            'worker_agent': worker_agent,
            'workspace_mode': workspace_mode,
            'workspace_binding': str(binding_path),
        }
    project_root = Path(context.project.project_root)
    if _same_resolved_path(workspace_path, project_root):
        return None, None
    changed_files = _changed_workspace_files(workspace_path, project_root)
    deleted_files = _deleted_workspace_files(workspace_path, project_root)
    if len(changed_files) > MAX_PROMOTED_WORKSPACE_FILES:
        reason = (
            f'worker workspace changed more than {MAX_PROMOTED_WORKSPACE_FILES} files; '
            'script-owned promotion requires a smaller explicit delta'
        )
        return None, {
            'source': 'isolated_workspace_change_limit_exceeded',
            'stage': 'round_authority',
            'reason': reason,
            'error': reason,
            'worker_agent': worker_agent,
            'workspace_mode': workspace_mode or None,
            'workspace_path': str(workspace_path),
            'project_root': str(project_root),
            'workspace_binding': str(binding_path),
            'changed_files': changed_files[:MAX_PROMOTED_WORKSPACE_FILES],
            'changed_file_count': len(changed_files),
        }
    if deleted_files:
        reason = (
            'worker workspace deleted or renamed project-root files; direct_execution promotion only supports '
            'additions/modifications, so script-owned import cannot verify this pass'
        )
        return None, {
            'source': 'isolated_workspace_deletions_unsupported',
            'stage': 'round_authority',
            'reason': reason,
            'error': reason,
            'worker_agent': worker_agent,
            'workspace_mode': workspace_mode or None,
            'workspace_path': str(workspace_path),
            'project_root': str(project_root),
            'workspace_binding': str(binding_path),
            'changed_files': changed_files,
            'deleted_files': deleted_files,
        }
    if not changed_files:
        if allow_noop_verified:
            return {
                'source': 'isolated_workspace_changes_already_promoted',
                'stage': 'round_authority',
                'operation': 'verify_worker_workspace_matches_project_root',
                'worker_agent': worker_agent,
                'workspace_mode': workspace_mode or None,
                'workspace_path': str(workspace_path),
                'project_root': str(project_root),
                'workspace_binding': str(binding_path),
                'changed_files': [],
                'allowed_change_paths': _declared_allowed_change_paths(task_text),
                'verified_project_root': True,
                '_project_root_rollback': {},
            }, None
        reason = (
            'round reviewer declared pass from an isolated worker workspace, but no project-root effects were '
            'detected for script-owned promotion'
        )
        return None, {
            'source': 'isolated_workspace_no_project_root_effect',
            'stage': 'round_authority',
            'reason': reason,
            'error': reason,
            'worker_agent': worker_agent,
            'workspace_mode': workspace_mode or None,
            'workspace_path': str(workspace_path),
            'project_root': str(project_root),
            'workspace_binding': str(binding_path),
            'changed_files': [],
        }
    allowed_change_paths = _declared_allowed_change_paths(task_text)
    if not allowed_change_paths:
        reason = (
            'worker workspace has project-root deltas, but task packet/execution contract did not declare '
            'allowed_change_paths for script-owned isolated workspace promotion'
        )
        return None, {
            'source': 'isolated_workspace_change_scope_missing',
            'stage': 'round_authority',
            'reason': reason,
            'error': reason,
            'worker_agent': worker_agent,
            'workspace_mode': workspace_mode or None,
            'workspace_path': str(workspace_path),
            'project_root': str(project_root),
            'workspace_binding': str(binding_path),
            'changed_files': changed_files,
            'allowed_change_paths': [],
        }
    out_of_scope = [
        changed_file
        for changed_file in changed_files
        if not _path_allowed_by_scope(changed_file, allowed_change_paths)
    ]
    if out_of_scope:
        reason = 'worker workspace contains changes outside task-declared allowed_change_paths'
        return None, {
            'source': 'isolated_workspace_change_scope_violation',
            'stage': 'round_authority',
            'reason': reason,
            'error': reason,
            'worker_agent': worker_agent,
            'workspace_mode': workspace_mode or None,
            'workspace_path': str(workspace_path),
            'project_root': str(project_root),
            'workspace_binding': str(binding_path),
            'changed_files': changed_files,
            'allowed_change_paths': allowed_change_paths,
            'out_of_scope_files': out_of_scope,
        }
    rollback = _capture_project_files(project_root, changed_files)
    try:
        _copy_workspace_files(workspace_path, project_root, changed_files)
    except Exception as exc:
        _restore_project_files(project_root, rollback)
        failure = _failure_record(source='isolated_workspace_promotion_failed', stage='round_authority', exc=exc)
        failure.update(
            {
                'worker_agent': worker_agent,
                'workspace_mode': workspace_mode or None,
                'workspace_path': str(workspace_path),
                'project_root': str(project_root),
                'workspace_binding': str(binding_path),
                'changed_files': changed_files,
            }
        )
        return None, failure
    unapplied = _unapplied_workspace_files(workspace_path, project_root, changed_files)
    if unapplied:
        _restore_project_files(project_root, rollback)
        reason = (
            'round reviewer declared pass, but worker workspace changes could not be verified in the project root'
        )
        return None, {
            'source': 'isolated_workspace_changes_not_applied',
            'stage': 'round_authority',
            'reason': reason,
            'error': reason,
            'worker_agent': worker_agent,
            'workspace_mode': workspace_mode or None,
            'workspace_path': str(workspace_path),
            'project_root': str(project_root),
            'workspace_binding': str(binding_path),
            'changed_files': unapplied,
        }
    return {
        'source': 'isolated_workspace_changes_promoted',
        'stage': 'round_authority',
        'operation': 'copy_worker_workspace_files_to_project_root',
        'worker_agent': worker_agent,
        'workspace_mode': workspace_mode or None,
        'workspace_path': str(workspace_path),
        'project_root': str(project_root),
        'workspace_binding': str(binding_path),
        'changed_files': changed_files,
        'allowed_change_paths': allowed_change_paths,
        'verified_project_root': True,
        '_project_root_rollback': rollback,
    }, None


def _configured_workspace_mode(context, profile: str) -> str | None:
    try:
        config = load_project_config(Path(context.project.project_root), include_loop_overlays=False).config
    except Exception:
        return None
    loop_capacity = getattr(config, 'loop_capacity', None)
    profiles = getattr(loop_capacity, 'role_profiles', {}) if loop_capacity is not None else {}
    role_profile = profiles.get(profile) if isinstance(profiles, dict) else None
    workspace_mode = getattr(role_profile, 'workspace_mode', None)
    value = getattr(workspace_mode, 'value', workspace_mode)
    text = str(value or '').strip()
    return text or None


def _workspace_binding_path(context, agent_name: str) -> Path:
    workspaces_dir = getattr(context.paths, 'workspaces_dir', None)
    if workspaces_dir is None:
        workspaces_dir = Path(context.project.project_root) / '.ccb' / 'workspaces'
    return Path(workspaces_dir) / agent_name / '.ccb-workspace.json'


def _workspace_path_from_binding(context, agent_name: str, binding: dict[str, object]) -> Path:
    workspace_text = str(binding.get('workspace_path') or '').strip()
    if workspace_text:
        return Path(workspace_text)
    return _workspace_binding_path(context, agent_name).parent


def _changed_workspace_files(workspace_path: Path, project_root: Path) -> list[str]:
    if not workspace_path.is_dir():
        return []
    changed: list[str] = []
    for path in sorted(workspace_path.rglob('*')):
        try:
            relative = path.relative_to(workspace_path)
        except ValueError:
            continue
        if _ignore_workspace_relative(relative):
            continue
        project_path = project_root / relative
        if path.is_dir():
            continue
        if not project_path.is_file() or not filecmp.cmp(path, project_path, shallow=False):
            changed.append(relative.as_posix())
    return changed


def _deleted_workspace_files(workspace_path: Path, project_root: Path) -> list[str]:
    if not workspace_path.is_dir():
        return []
    deleted: list[str] = []
    for path in sorted(project_root.rglob('*')):
        try:
            relative = path.relative_to(project_root)
        except ValueError:
            continue
        if _ignore_workspace_relative(relative):
            continue
        if path.is_dir():
            continue
        if not (workspace_path / relative).exists():
            deleted.append(relative.as_posix())
    return deleted


def _copy_workspace_files(workspace_path: Path, project_root: Path, changed_files: list[str]) -> None:
    for changed_file in changed_files:
        relative = _safe_relative_path(changed_file)
        source = workspace_path / relative
        destination = project_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _declared_allowed_change_paths(task_text: str) -> list[str]:
    declared: list[str] = []
    for raw_line in task_text.splitlines():
        line = raw_line.strip().lstrip('-').strip()
        lower = line.lower()
        for prefix in ALLOWED_CHANGE_PATH_PREFIXES:
            if lower.startswith(prefix):
                declared.extend(_split_declared_paths(line.split(':', 1)[1]))
        for marker in ('update only ', 'fix only ', 'edit only ', 'change only ', 'modify only '):
            marker_index = lower.find(marker)
            if marker_index < 0:
                continue
            tail = line[marker_index + len(marker):]
            first_sentence = tail.split('.', 1)[0]
            declared.extend(_split_declared_paths(first_sentence))
    normalized: list[str] = []
    seen: set[str] = set()
    for path in declared:
        relative = _safe_relative_path(path).as_posix()
        if relative in seen:
            continue
        normalized.append(relative)
        seen.add(relative)
    return normalized


def _split_declared_paths(value: str) -> list[str]:
    paths: list[str] = []
    for raw in value.replace(';', ',').split(','):
        token = raw.strip().strip('`"\'')
        if not token:
            continue
        if ' ' in token:
            token = token.split()[0].strip('`"\'')
        token = token.rstrip('.,')
        if '/' not in token and not token.endswith('/'):
            continue
        paths.append(token)
    return paths


def _path_allowed_by_scope(changed_file: str, allowed_change_paths: list[str]) -> bool:
    changed = _safe_relative_path(changed_file).as_posix()
    for allowed in allowed_change_paths:
        scope = _safe_relative_path(allowed).as_posix()
        if changed == scope:
            return True
        if allowed.endswith('/') and changed.startswith(scope.rstrip('/') + '/'):
            return True
    return False


def _capture_project_files(project_root: Path, changed_files: list[str]) -> dict[str, bytes | None]:
    rollback: dict[str, bytes | None] = {}
    for changed_file in changed_files:
        relative = _safe_relative_path(changed_file)
        destination = project_root / relative
        rollback[relative.as_posix()] = destination.read_bytes() if destination.is_file() else None
    return rollback


def _restore_project_files(project_root: Path, rollback: dict[str, bytes | None]) -> None:
    for changed_file, content in rollback.items():
        relative = _safe_relative_path(changed_file)
        destination = project_root / relative
        if content is None:
            if destination.exists():
                destination.unlink()
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def _authority_update_rollback(authority_update: dict[str, object] | None) -> dict[str, bytes | None] | None:
    if not isinstance(authority_update, dict):
        return None
    rollback = authority_update.get('_project_root_rollback')
    if not isinstance(rollback, dict):
        return None
    return {
        str(key): (bytes(value) if isinstance(value, bytes) else None)
        for key, value in rollback.items()
    }


def _merge_authority_updates(
    previous: dict[str, object] | None,
    current: dict[str, object] | None,
) -> dict[str, object] | None:
    if not isinstance(previous, dict):
        return current
    if not isinstance(current, dict):
        return previous
    current_changed = current.get('changed_files') if isinstance(current.get('changed_files'), list) else []
    if not current_changed:
        return previous
    merged = dict(current)
    previous_changed = previous.get('changed_files') if isinstance(previous.get('changed_files'), list) else []
    merged['changed_files'] = _unique_paths([*previous_changed, *current_changed])
    previous_allowed = (
        previous.get('allowed_change_paths')
        if isinstance(previous.get('allowed_change_paths'), list)
        else []
    )
    current_allowed = (
        current.get('allowed_change_paths')
        if isinstance(current.get('allowed_change_paths'), list)
        else []
    )
    merged['allowed_change_paths'] = _unique_paths([*previous_allowed, *current_allowed])
    previous_rollback = previous.get('_project_root_rollback')
    current_rollback = current.get('_project_root_rollback')
    rollback = dict(current_rollback) if isinstance(current_rollback, dict) else {}
    if isinstance(previous_rollback, dict):
        rollback.update(previous_rollback)
    merged['_project_root_rollback'] = rollback
    previous_count = int(previous.get('promotion_count') or 1)
    current_count = int(current.get('promotion_count') or 1)
    merged['promotion_count'] = previous_count + current_count
    return merged


def _unique_paths(paths: list[object]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        text = str(path or '').strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def _restore_authority_update(
    context,
    authority_update: dict[str, object] | None,
    failure: dict[str, object] | None,
    *,
    reason: str,
) -> bool:
    rollback = _authority_update_rollback(authority_update)
    if not rollback:
        return False
    _restore_project_files(Path(context.project.project_root), rollback)
    if isinstance(authority_update, dict):
        authority_update['authority_rollback'] = 'restored_project_root'
        authority_update['authority_rollback_reason'] = reason
    if isinstance(failure, dict):
        failure['authority_rollback'] = 'restored_project_root'
        failure['authority_rollback_reason'] = reason
    return True


def _public_authority_update(authority_update: dict[str, object] | None) -> dict[str, object] | None:
    if not isinstance(authority_update, dict):
        return None
    public = dict(authority_update)
    public.pop('_project_root_rollback', None)
    return public


def _unapplied_workspace_files(workspace_path: Path, project_root: Path, changed_files: list[str]) -> list[str]:
    unapplied: list[str] = []
    for changed_file in changed_files:
        relative = _safe_relative_path(changed_file)
        source = workspace_path / relative
        destination = project_root / relative
        if not destination.is_file() or not filecmp.cmp(source, destination, shallow=False):
            unapplied.append(relative.as_posix())
    return unapplied


def _safe_relative_path(value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError(f'unsafe workspace relative path {value!r}')
    return relative


def _ignore_workspace_relative(relative: Path) -> bool:
    parts = set(relative.parts)
    if parts.intersection({'.ccb', '.git', '.pytest_cache', '__pycache__'}):
        return True
    if relative.name == '.ccb-workspace.json':
        return True
    return relative.suffix in {'.pyc', '.pyo'}


def _same_resolved_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


def _project_root_test_authority(
    context,
    *,
    loop_dir: Path,
    task_text: str,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    test_command = _declared_project_root_test_command(task_text)
    if test_command is None:
        return None, None
    project_root = Path(context.project.project_root)
    resolution_path = loop_dir / 'project_root_test_resolution.json'
    evidence = _project_root_test_evidence(project_root, test_command, resolution_path)
    atomic_write_json(resolution_path, evidence)
    if not bool(evidence.get('test_file_resolved_to_lab')) or not bool(evidence.get('test_sys_path_project_first')):
        reason = 'project-root test command did not resolve to lab-local test authority'
        failure = {
            'source': 'project_root_test_resolution_failed',
            'stage': 'round_authority',
            'reason': reason,
            'error': reason,
            **evidence,
        }
        return evidence, failure
    if evidence.get('test_result') != 'pass':
        reason = 'project-root test command failed after workspace promotion'
        failure = {
            'source': 'project_root_test_failed',
            'stage': 'round_authority',
            'reason': reason,
            'error': reason,
            **evidence,
        }
        return evidence, failure
    return evidence, None


def _declared_project_root_test_command(task_text: str) -> str | None:
    for raw_line in task_text.splitlines():
        line = raw_line.strip().lstrip('-').strip()
        lower = line.lower()
        for prefix in TEST_COMMAND_PREFIXES:
            if not lower.startswith(prefix):
                continue
            command = line.split(':', 1)[1].strip().strip('`')
            return command or None
    return None


def _project_root_test_evidence(project_root: Path, test_command: str, resolution_path: Path) -> dict[str, object]:
    args = shlex.split(test_command)
    test_file = _resolve_unittest_file(project_root, args)
    test_file_resolved_to_lab = test_file is not None and _path_within(test_file, project_root)
    sys_path_project_first = _sys_path_project_first(project_root)
    returncode = None
    test_result = 'not_run'
    if args and test_file_resolved_to_lab and sys_path_project_first:
        run_args = _python_command_args(args)
        completed = subprocess.run(
            run_args,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        returncode = completed.returncode
        test_result = 'pass' if completed.returncode == 0 else 'fail'
    return {
        'test_command': test_command,
        'test_cwd': str(project_root),
        'test_resolution_path': str(resolution_path),
        'test_result': test_result,
        'test_file_resolved_to_lab': bool(test_file_resolved_to_lab),
        'test_sys_path_project_first': bool(sys_path_project_first),
        'test_file': str(test_file) if test_file is not None else None,
        'returncode': returncode,
    }


def _resolve_unittest_file(project_root: Path, args: list[str]) -> Path | None:
    if '-m' not in args or 'unittest' not in args:
        return None
    start_dir = 'tests'
    pattern = 'test*.py'
    for index, value in enumerate(args):
        if value in {'-s', '--start-directory'} and index + 1 < len(args):
            start_dir = args[index + 1]
        if value in {'-p', '--pattern'} and index + 1 < len(args):
            pattern = args[index + 1]
    candidates = sorted((project_root / start_dir).glob(pattern))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _sys_path_project_first(project_root: Path) -> bool:
    script = (
        'import json,os,sys; '
        'print(json.dumps({"sys_path_0": os.path.abspath(sys.path[0] or os.getcwd())}))'
    )
    try:
        completed = subprocess.run(
            [sys.executable, '-c', script],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception:
        return False
    if completed.returncode != 0:
        return False
    try:
        payload = json.loads(completed.stdout.strip() or '{}')
    except json.JSONDecodeError:
        return False
    return _same_resolved_path(Path(str(payload.get('sys_path_0') or '.')), project_root)


def _python_command_args(args: list[str]) -> list[str]:
    if args and Path(args[0]).name in {'python', 'python3'}:
        return [sys.executable, *args[1:]]
    return args


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _single_job(jobs: tuple[dict, ...], *, target: str) -> dict:
    if len(jobs) != 1:
        raise RuntimeError(f'expected one ask job for {target}; got {len(jobs)}')
    job = dict(jobs[0])
    if not str(job.get('job_id') or ''):
        raise RuntimeError(f'ask job for {target} did not return job_id')
    return job


def _artifact_refs(record: dict[str, object]) -> dict[str, str]:
    artifacts = record.get('artifacts') if isinstance(record.get('artifacts'), dict) else {}
    refs: dict[str, str] = {}
    for kind in ('task_packet', 'execution_contract', 'orchestration_notes'):
        artifact = artifacts.get(kind) if isinstance(artifacts, dict) else None
        if isinstance(artifact, dict) and str(artifact.get('path') or '').strip():
            refs[kind] = str(artifact['path'])
    return refs


def _requires_project_root_authority(record: dict[str, object], task_text: str) -> bool:
    artifacts = record.get('artifacts') if isinstance(record.get('artifacts'), dict) else {}
    orchestration_notes = artifacts.get('orchestration_notes') if isinstance(artifacts, dict) else None
    if isinstance(orchestration_notes, dict):
        route = str(orchestration_notes.get('orchestrator_route') or orchestration_notes.get('route') or '').strip()
        if route:
            return route == 'direct_execution'
    for raw_line in task_text.splitlines():
        line = raw_line.strip().lower().lstrip('-').strip()
        if line.startswith('route:'):
            return line.split(':', 1)[1].strip().split()[0].strip('`.,;') == 'direct_execution'
    return False


def _task_record(payload: dict[str, object]) -> dict[str, object]:
    task = payload.get('task') if isinstance(payload.get('task'), dict) else None
    if task is None:
        raise RuntimeError('plan task-show did not return task record')
    return dict(task)


def _round_json_path(payload: dict[str, object]) -> Path | None:
    paths = payload.get('paths') if isinstance(payload.get('paths'), dict) else {}
    text = str(paths.get('round_json') or '').strip()
    return Path(text) if text else None


def _append_ask(
    loop_dir: Path,
    *,
    loop_id: str,
    target: str,
    sender: str,
    purpose: str,
    job_id: str,
) -> None:
    _append_jsonl(
        loop_dir / 'asks.jsonl',
        {
            'schema_version': 1,
            'record_type': 'ccb_loop_ask_first_ask',
            'ask_id': f'ask-{uuid4().hex[:12]}',
            'ts': _utc_now(),
            'loop_id': loop_id,
            'target': target,
            'sender': sender,
            'purpose': purpose,
            'job_id': job_id,
            'status': 'submitted',
        },
    )


def _append_event(loop_dir: Path, *, loop_id: str, kind: str, payload: dict[str, object]) -> None:
    _append_jsonl(
        loop_dir / 'events.jsonl',
        {
            'schema_version': 1,
            'record_type': 'ccb_loop_ask_first_event',
            'event_id': f'evt-{uuid4().hex[:12]}',
            'ts': _utc_now(),
            'loop_id': loop_id,
            'kind': kind,
            'actor': 'loop_runner',
            **payload,
        },
    )


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write('\n')


def _ensure_loop_dirs(loop_dir: Path) -> None:
    for relative in ('artifacts', 'topology_proposals'):
        (loop_dir / relative).mkdir(parents=True, exist_ok=True)


def _loop_dir(context, loop_id: str) -> Path:
    return Path(context.paths.runtime_state_root) / 'runtime' / 'loops' / loop_id


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


__all__ = ['release_ask_first_execution_round', 'run_ask_first_execution_round']
