from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from cli.models import ParsedAskCommand
from storage.atomic import atomic_write_json

from .ask import submit_ask
from .loop_ask_first import release_ask_first_execution_round, run_ask_first_execution_round
from .loop_run_once import loop_run_once
from .plan_tasks import find_first_actionable_task, plan_task
from .questions import question_refs

_ORCHESTRATOR_ROUTES = ('direct_execution', 'needs_detail', 'macro_adjustment_request', 'blocked', 'partial_completion')
_ROUND_REVIEWER_FIELD = 'ccb_round_reviewer'
_LEGACY_ROUND_CHECKER_FIELD = 'round_checker'


def loop_runner_once(context, command, services=None) -> dict[str, object]:
    if bool(getattr(command, 'consume_role_output', False)):
        return _consume_role_output_disabled(context)
    deps = _deps(services)
    task = find_first_actionable_task(context)
    if task is None:
        return {
            'schema_version': 1,
            'record_type': 'ccb_loop_runner_once',
            'loop_runner_status': 'idle',
            'project_id': context.project.project_id,
            'project_root': str(context.project.project_root),
            'action': 'none',
            'reason': 'no_actionable_task',
        }

    runner_action = str(task.get('runner_action') or '')
    if runner_action == 'activate_orchestrator':
        return _activate_orchestrator(context, command, deps, task)
    if runner_action == 'ask_first_execute':
        return _run_ask_first_execution_round(context, command, deps, task)
    if runner_action == 'ask_first_execution_not_ready':
        record = task['record']
        current_loop = str(record.get('current_loop') or '').strip()
        return _phase4_not_ready(
            context,
            task,
            reason=str(task.get('runner_reason') or 'phase4_not_ready'),
            loop_id=current_loop or None,
        )
    if runner_action == 'execute':
        return _run_execution_round(context, command, deps, task)
    if runner_action == 'activate_planner':
        return _activate_planner(context, command, deps, task)
    if runner_action == 'activate_task_detailer':
        return _activate_task_detailer(context, command, deps, task)
    if runner_action == 'activate_plan_reviewer':
        return _activate_plan_reviewer(context, command, deps, task)
    return _stop_without_activation(context, task)


def _run_ask_first_execution_round(context, command, deps, task: dict[str, object]) -> dict[str, object]:
    record = task['record']
    task_id = str(record.get('task_id') or '')
    current_loop = str(record.get('current_loop') or '').strip()
    if current_loop:
        return _phase4_not_ready(
            context,
            task,
            loop_id=current_loop,
            reason='running_task_bound_to_loop',
        )
    loop_id = f'lp{uuid4().hex[:6]}'
    bind = deps.plan_task(
        context,
        SimpleNamespace(action='task-bind-loop', task_id=task_id, loop_id=loop_id),
    )
    round_payload = deps.ask_first_execution(
        context,
        SimpleNamespace(
            kind='loop-ask-first-execution',
            project=None,
            loop_id=loop_id,
            task_id=task_id,
            timeout_s=getattr(command, 'timeout_s', None),
            json_output=True,
        ),
        deps.services,
    )
    round_result, round_result_source = _round_result(round_payload)
    report_path = _round_report_path(round_payload)
    imported = deps.plan_task(
        context,
        SimpleNamespace(
            action='task-import-round',
            task_id=task_id,
            loop_id=loop_id,
            result=round_result,
            file_path=report_path,
            actor_source='loop_runner',
            actor='loop_runner',
            job_id=str(_first_job_id(round_payload) or ''),
        ),
    )
    release = deps.ask_first_release(context, round_payload, deps.services)
    _record_round_import(round_payload, imported=imported, release=release)
    return {
        'schema_version': 1,
        'record_type': 'ccb_loop_runner_once',
        'loop_runner_status': 'ok',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'action': 'ran_one_round',
        'dispatch_source': round_payload.get('dispatch_source') or 'ask_first_mount_topology',
        'execution_mode': 'ask_first_direct_execution',
        'task_id': task_id,
        'loop_id': loop_id,
        'round_result': round_result,
        'round_result_source': round_result_source,
        'task_status': imported.get('status'),
        'bind': _compact_plan_payload(bind),
        'round': {
            'loop_run_status': round_payload.get('loop_run_status'),
            'round_path': report_path,
            'round_json_path': _round_json_path(round_payload),
        },
        'topology': _compact_topology_payload(round_payload.get('topology')),
        'release': _compact_release_payload(release),
        'import': _compact_plan_payload(imported),
        'next_activation': _next_activation(imported.get('status')),
    }


def _run_execution_round(context, command, deps, task: dict[str, object]) -> dict[str, object]:
    record = task['record']
    task_id = str(record.get('task_id') or '')
    current_loop = str(record.get('current_loop') or '').strip()
    if current_loop:
        return _phase4_not_ready(
            context,
            task,
            loop_id=current_loop,
            reason='running_task_bound_to_loop',
        )
    else:
        loop_id = f'lp{uuid4().hex[:6]}'
        bind = deps.plan_task(
            context,
            SimpleNamespace(action='task-bind-loop', task_id=task_id, loop_id=loop_id),
        )
        round_payload = deps.loop_run_once(
            context,
            SimpleNamespace(
                kind='loop-run-once',
                project=None,
                loop_id=loop_id,
                task=None,
                task_id=task_id,
                worker_profile='worker',
                reviewer_profile='code_reviewer',
                orchestrator='orchestrator',
                round_checker='round_checker',
                timeout_s=getattr(command, 'timeout_s', None),
                json_output=True,
            ),
            deps.services,
        )
    round_result, round_result_source = _round_result(round_payload)
    report_path = _round_report_path(round_payload)
    imported = deps.plan_task(
        context,
        SimpleNamespace(
            action='task-import-round',
            task_id=task_id,
            loop_id=loop_id,
            result=round_result,
            file_path=report_path,
            actor_source='loop_runner',
            actor='loop_runner',
            job_id=str(_first_job_id(round_payload) or ''),
        ),
    )
    return {
        'schema_version': 1,
        'record_type': 'ccb_loop_runner_once',
        'loop_runner_status': 'ok',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'action': 'ran_one_round',
        'dispatch_source': round_payload.get('dispatch_source') or 'fixed_worker_reviewer',
        'task_id': task_id,
        'loop_id': loop_id,
        'round_result': round_result,
        'round_result_source': round_result_source,
        'task_status': imported.get('status'),
        'bind': _compact_plan_payload(bind),
        'round': {
            'loop_run_status': round_payload.get('loop_run_status'),
            'round_path': report_path,
        },
        'import': _compact_plan_payload(imported),
        'next_activation': _next_activation(imported.get('status')),
    }


def _phase4_not_ready(
    context,
    task: dict[str, object],
    *,
    reason: str,
    loop_id: str | None = None,
) -> dict[str, object]:
    record = dict(task['record'])
    resolved_loop_id = loop_id or str(record.get('current_loop') or '').strip() or None
    return {
        'schema_version': 1,
        'record_type': 'ccb_loop_runner_once',
        'loop_runner_status': 'paused',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'action': 'ask_first_execution_not_ready',
        'reason': (
            f'{reason}; Phase 4 ask-first execution can only start from an unbound '
            'direct_execution task, and topology dispatch is legacy/disabled for loop runner mainline'
        ),
        'task_id': record.get('task_id'),
        'loop_id': resolved_loop_id,
        'task_status': record.get('status'),
        'next_owner': record.get('next_owner') or task.get('next_owner'),
        'next_activation': 'phase4_ask_first_runner_required',
    }


def _activate_orchestrator(context, command, deps, task: dict[str, object]) -> dict[str, object]:
    record = dict(task['record'])
    task_id = str(record.get('task_id') or '')
    activation_id = f'act-{uuid4().hex[:12]}'
    activation = _orchestrator_activation_packet(
        context,
        record,
        activation_id=activation_id,
        reason=str(task.get('runner_reason') or 'ready_for_orchestration'),
    )
    activation_path = _activation_path(context, activation_id)
    atomic_write_json(activation_path, activation)
    summary = deps.submit_ask(
        context,
        ParsedAskCommand(
            project=None,
            target='orchestrator',
            sender='system',
            message=_orchestrator_message(activation),
            task_id=activation_id,
            compact=True,
            artifact_request=True,
        ),
    )
    job = _single_job(summary.jobs, target='orchestrator')
    activation['ask'] = {
        'target': 'orchestrator',
        'job_id': str(job['job_id']),
        'status': job.get('status'),
    }
    atomic_write_json(activation_path, activation)
    return {
        'schema_version': 1,
        'record_type': 'ccb_loop_runner_once',
        'loop_runner_status': 'ok',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'action': 'activated_orchestrator',
        'reason': activation['reason_for_activation'],
        'task_id': task_id,
        'task_status': record.get('status'),
        'next_owner': 'orchestrator',
        'activation_id': activation_id,
        'activation_path': str(activation_path),
        'ask': activation['ask'],
        'next_activation': 'stop_after_one_activation',
    }


def _activate_planner(context, command, deps, task: dict[str, object]) -> dict[str, object]:
    record = dict(task['record'])
    task_id = str(record.get('task_id') or '')
    activation_id = f'act-{uuid4().hex[:12]}'
    activation = _planner_activation_packet(
        context,
        record,
        activation_id=activation_id,
        action=str(task.get('runner_action') or 'activate_planner'),
        reason=str(task.get('runner_reason') or 'planner_state'),
    )
    activation_path = _activation_path(context, activation_id)
    atomic_write_json(activation_path, activation)
    summary = deps.submit_ask(
        context,
        ParsedAskCommand(
            project=None,
            target='planner',
            sender='system',
            message=_planner_message(activation),
            task_id=activation_id,
            compact=True,
            artifact_request=True,
        ),
    )
    job = _single_job(summary.jobs, target='planner')
    activation['ask'] = {
        'target': 'planner',
        'job_id': str(job['job_id']),
        'status': job.get('status'),
    }
    atomic_write_json(activation_path, activation)
    return {
        'schema_version': 1,
        'record_type': 'ccb_loop_runner_once',
        'loop_runner_status': 'ok',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'action': 'activated_planner',
        'reason': activation['reason_for_activation'],
        'task_id': task_id,
        'task_status': record.get('status'),
        'next_owner': 'planner',
        'activation_id': activation_id,
        'activation_path': str(activation_path),
        'ask': activation['ask'],
        'next_activation': 'stop_after_one_activation',
    }


def _activate_plan_reviewer(context, command, deps, task: dict[str, object]) -> dict[str, object]:
    record = dict(task['record'])
    task_id = str(record.get('task_id') or '')
    activation_id = f'act-{uuid4().hex[:12]}'
    activation = _plan_reviewer_activation_packet(
        context,
        record,
        activation_id=activation_id,
        reason=str(task.get('runner_reason') or 'review_required'),
    )
    activation_path = _activation_path(context, activation_id)
    atomic_write_json(activation_path, activation)
    summary = deps.submit_ask(
        context,
        ParsedAskCommand(
            project=None,
            target='plan_reviewer',
            sender='system',
            message=_plan_reviewer_message(activation),
            task_id=activation_id,
            compact=True,
            artifact_request=True,
        ),
    )
    job = _single_job(summary.jobs, target='plan_reviewer')
    activation['ask'] = {
        'target': 'plan_reviewer',
        'job_id': str(job['job_id']),
        'status': job.get('status'),
    }
    atomic_write_json(activation_path, activation)
    return {
        'schema_version': 1,
        'record_type': 'ccb_loop_runner_once',
        'loop_runner_status': 'ok',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'action': 'activated_plan_reviewer',
        'reason': activation['reason_for_activation'],
        'task_id': task_id,
        'task_status': record.get('status'),
        'next_owner': 'planner',
        'activation_id': activation_id,
        'activation_path': str(activation_path),
        'ask': activation['ask'],
        'next_activation': 'stop_after_one_activation',
    }


def _activate_task_detailer(context, command, deps, task: dict[str, object]) -> dict[str, object]:
    record = dict(task['record'])
    task_id = str(record.get('task_id') or '')
    next_owner = str(task.get('next_owner') or record.get('next_owner') or 'planner')
    activation_id = f'act-{uuid4().hex[:12]}'
    activation = _task_detailer_activation_packet(
        context,
        record,
        activation_id=activation_id,
        reason=str(task.get('runner_reason') or 'detail_required'),
    )
    activation_path = _activation_path(context, activation_id)
    atomic_write_json(activation_path, activation)
    summary = deps.submit_ask(
        context,
        ParsedAskCommand(
            project=None,
            target='task_detailer',
            sender='system',
            message=_task_detailer_message(activation),
            task_id=activation_id,
            compact=True,
            artifact_request=True,
        ),
    )
    job = _single_job(summary.jobs, target='task_detailer')
    activation['ask'] = {
        'target': 'task_detailer',
        'job_id': str(job['job_id']),
        'status': job.get('status'),
    }
    atomic_write_json(activation_path, activation)
    return {
        'schema_version': 1,
        'record_type': 'ccb_loop_runner_once',
        'loop_runner_status': 'ok',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'action': 'activated_task_detailer',
        'reason': activation['reason_for_activation'],
        'task_id': task_id,
        'task_status': record.get('status'),
        'next_owner': next_owner,
        'activation_id': activation_id,
        'activation_path': str(activation_path),
        'ask': activation['ask'],
        'next_activation': 'stop_after_one_activation',
    }


def _stop_without_activation(context, task: dict[str, object]) -> dict[str, object]:
    record = dict(task['record'])
    action = str(task.get('runner_action') or 'stop')
    paused_actions = {'paused', 'planner_next_action_required', 'blocker_evidence_required'}
    next_activation = 'none'
    if action == 'planner_next_action_required':
        next_activation = 'planner_status_transition_required'
    elif action == 'blocker_evidence_required':
        next_activation = 'blocker_evidence_required'
    payload = {
        'schema_version': 1,
        'record_type': 'ccb_loop_runner_once',
        'loop_runner_status': 'paused' if action in paused_actions else action,
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'action': action,
        'reason': task.get('runner_reason'),
        'task_id': record.get('task_id'),
        'task_status': record.get('status'),
        'next_owner': task.get('next_owner'),
        'next_activation': next_activation,
    }
    if action == 'paused' and str(record.get('task_id') or '').strip():
        payload['question_refs'] = question_refs(context, record.get('task_id'))
    return payload


def _consume_role_output_disabled(context) -> dict[str, object]:
    return {
        'schema_version': 1,
        'record_type': 'ccb_loop_runner_once',
        'loop_runner_status': 'rejected',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'action': 'consume_role_output_disabled',
        'reason': (
            '--consume-role-output is legacy/disabled for the Decision 020 mainline; '
            'use script-owned artifact imports and explicit task status transitions instead'
        ),
        'next_activation': 'none',
    }


def _deps(services):
    services = services or SimpleNamespace()
    return SimpleNamespace(
        ask_first_execution=getattr(services, 'ask_first_execution', run_ask_first_execution_round),
        ask_first_release=getattr(services, 'ask_first_release', release_ask_first_execution_round),
        loop_run_once=getattr(services, 'loop_run_once', loop_run_once),
        plan_task=getattr(services, 'plan_task', plan_task),
        submit_ask=getattr(services, 'submit_ask', submit_ask),
        services=services,
    )


def _round_result(payload: dict[str, object]) -> tuple[str, str]:
    structured = str(payload.get('round_result') or '').strip().lower()
    if structured:
        mapping = {
            'pass': 'pass',
            'partial': 'partial',
            'replan_required': 'replan_required',
            'blocked': 'blocked',
            'global_blocker': 'blocked',
        }
        if structured not in mapping:
            known = ', '.join(('blocked', 'partial', 'pass', 'replan_required'))
            raise RuntimeError(f'unknown round result {structured!r}; expected one of: {known}')
        source = str(payload.get('round_result_source') or '').strip() or 'round_payload'
        return mapping[structured], source
    declared, source_field = _declared_round_result(payload)
    if declared is not None:
        source = 'round_checker_reply' if source_field == _LEGACY_ROUND_CHECKER_FIELD else 'round_reviewer_reply'
        return declared, source
    if str(payload.get('loop_run_status') or '') == 'ok':
        if isinstance(payload.get(_ROUND_REVIEWER_FIELD), dict):
            return 'blocked', 'missing_round_reviewer_result'
        return 'blocked', 'missing_round_checker_result'
    return 'blocked', 'loop_run_status'


def _declared_round_result(payload: dict[str, object]) -> tuple[str | None, str]:
    reviewer = payload.get(_ROUND_REVIEWER_FIELD) if isinstance(payload.get(_ROUND_REVIEWER_FIELD), dict) else {}
    source_field = _ROUND_REVIEWER_FIELD
    if not reviewer and isinstance(payload.get(_LEGACY_ROUND_CHECKER_FIELD), dict):
        reviewer = payload[_LEGACY_ROUND_CHECKER_FIELD]
        source_field = _LEGACY_ROUND_CHECKER_FIELD
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
            known = ', '.join(('blocked', 'partial', 'pass', 'replan_required'))
            raise RuntimeError(f'unknown round result {value!r}; expected one of: {known}')
        return mapping[value], source_field
    return None, source_field


def _round_report_path(payload: dict[str, object]) -> str:
    paths = payload.get('paths') if isinstance(payload.get('paths'), dict) else {}
    path = str(paths.get('round') or '').strip()
    if not path:
        raise RuntimeError('loop runner cannot import round result without round path')
    if not Path(path).is_file():
        raise RuntimeError(f'loop runner round report is missing: {path}')
    return path


def _compact_plan_payload(payload: dict[str, object]) -> dict[str, object]:
    return {
        'action': payload.get('action'),
        'task_id': payload.get('task_id'),
        'status': payload.get('status'),
        'plan_slug': payload.get('plan_slug'),
        'task_root': payload.get('task_root'),
        'idempotent': payload.get('idempotent'),
    }


def _compact_topology_payload(payload: object) -> dict[str, object]:
    topology = payload if isinstance(payload, dict) else {}
    commit = topology.get('commit') if isinstance(topology.get('commit'), dict) else {}
    reconcile = commit.get('reconcile') if isinstance(commit.get('reconcile'), dict) else {}
    status = topology.get('status') if isinstance(topology.get('status'), dict) else {}
    return {
        'dispatch_source': 'ask_first_mount_topology',
        'proposal_source_path': topology.get('proposal_source_path'),
        'proposal_path': (topology.get('propose') or {}).get('proposal_path') if isinstance(topology.get('propose'), dict) else None,
        'desired_path': commit.get('desired_path'),
        'observed_path': reconcile.get('observed_path') or status.get('observed_path'),
        'status': status.get('loop_topology_status'),
        'agent_count': reconcile.get('agent_count'),
        'released_count': (topology.get('release') or {}).get('released_count') if isinstance(topology.get('release'), dict) else None,
        'retained_count': (topology.get('release') or {}).get('retained_count') if isinstance(topology.get('release'), dict) else None,
    }


def _compact_release_payload(payload: dict[str, object]) -> dict[str, object]:
    return {
        'loop_topology_status': payload.get('loop_topology_status'),
        'loop_id': payload.get('loop_id'),
        'desired_path': payload.get('desired_path'),
        'observed_path': payload.get('observed_path'),
        'released_count': payload.get('released_count'),
        'retained_count': payload.get('retained_count'),
        'released_agents': payload.get('released_agents'),
    }


def _record_round_import(
    round_payload: dict[str, object],
    *,
    imported: dict[str, object],
    release: dict[str, object],
) -> None:
    round_payload['authority_import'] = _compact_plan_payload(imported)
    round_payload['release'] = _compact_release_payload(release)
    path_text = _round_json_path(round_payload)
    if path_text:
        atomic_write_json(Path(path_text), round_payload)


def _round_json_path(payload: dict[str, object]) -> str:
    paths = payload.get('paths') if isinstance(payload.get('paths'), dict) else {}
    return str(paths.get('round_json') or '').strip()


def _next_activation(status: object) -> str:
    value = str(status or '')
    if value == 'done':
        return 'stop'
    if value in {'ready_for_orchestration', 'ready'}:
        return 'orchestrator'
    if value in {'partial', 'replan_required'}:
        return 'planner'
    if value == 'blocked':
        return 'terminal'
    return 'inspect'


def _orchestrator_activation_packet(
    context,
    record: dict[str, object],
    *,
    activation_id: str,
    reason: str,
) -> dict[str, object]:
    artifacts = record.get('artifacts') if isinstance(record.get('artifacts'), dict) else {}
    task_root = Path(context.project.project_root) / str(record.get('task_root') or '')
    refs = {
        kind: artifact.get('path')
        for kind, artifact in sorted(artifacts.items())
        if kind in {'task_packet', 'execution_contract', 'orchestration_notes'}
        and isinstance(artifact, dict)
        and artifact.get('path')
    }
    return {
        'schema_version': 1,
        'record_type': 'ccb_loop_orchestrator_activation',
        'activation_id': activation_id,
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'task_id': record.get('task_id'),
        'task_status': record.get('status'),
        'action': 'activate_orchestrator',
        'reason_for_activation': reason,
        'required_next_output': 'reply-only route decision and compact orchestration notes for supervisor-owned import',
        'task_packet_root': str(task_root.relative_to(context.project.project_root)),
        'artifact_refs': refs,
        'compact_artifacts': _compact_artifacts(context, artifacts, refs.keys()),
        'allowed_routes': _ORCHESTRATOR_ROUTES,
        'script_write_rules': [
            'Reply only; do not run ccb, ccb_test, artifact import commands, or wrapper commands.',
            'Choose exactly one route: direct_execution, needs_detail, macro_adjustment_request, blocked, or partial_completion.',
            'Provide compact orchestration notes with citations to task_packet and execution_contract refs.',
            'Supervisor/script-owned import will record orchestration_notes with the selected route after reviewing this reply.',
            'Do not edit task status, index, current_loop, runtime topology, or task artifacts directly.',
            'Do not rely on provider reply text as durable route/status authority.',
            'Do not start task_detailer, worker, reviewer, loop_run_once, or topology dispatch from this activation.',
        ],
        'stop_limits': [
            'one orchestrator activation per loop runner --once',
            'no recursive detail or execution activation inside orchestrator triage',
            'artifact links preferred over pasted runtime logs',
        ],
    }


def _planner_activation_packet(
    context,
    record: dict[str, object],
    *,
    activation_id: str,
    action: str,
    reason: str,
) -> dict[str, object]:
    artifacts = record.get('artifacts') if isinstance(record.get('artifacts'), dict) else {}
    task_root = Path(context.project.project_root) / str(record.get('task_root') or '')
    round_refs = [
        artifact
        for kind, artifact in sorted(artifacts.items())
        if str(kind).startswith('round_') and isinstance(artifact, dict)
    ]
    return {
        'schema_version': 1,
        'record_type': 'ccb_loop_planner_activation',
        'activation_id': activation_id,
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'task_id': record.get('task_id'),
        'task_status': record.get('status'),
        'action': action,
        'reason_for_activation': reason,
        'required_next_output': 'plan brief, task-packet artifacts, and readiness recommendation',
        'plan_brief_ref': str((Path(context.project.project_root) / str(record.get('plan_root') or '') / 'brief.md').relative_to(context.project.project_root)),
        'task_packet_root': str(task_root.relative_to(context.project.project_root)),
        'artifact_refs': {
            kind: artifact.get('path')
            for kind, artifact in sorted(artifacts.items())
            if isinstance(artifact, dict) and artifact.get('path')
        },
        'round_evidence_refs': tuple(
            {
                'kind': artifact.get('kind'),
                'path': artifact.get('path'),
                'round_result': artifact.get('round_result'),
                'loop_id': artifact.get('loop_id'),
            }
            for artifact in round_refs
        ),
        'open_question_refs': _planner_question_refs(context, record),
        'script_write_rules': [
            'Do not edit task status, index, or current_loop directly.',
            'Use ccb plan task-artifact and ccb plan task-status for authoritative writes.',
            'Planner may import brief and macro task-packet artifacts; detail bodies belong to task_detailer.',
            'Return needs_clarification, blocked, not_ready, or ready instead of lowering acceptance criteria.',
        ],
        'stop_limits': [
            'one planner activation per loop runner --once',
            'no recursive execution inside planner activation',
            'artifact links preferred over pasted runtime logs',
        ],
    }


def _task_detailer_activation_packet(
    context,
    record: dict[str, object],
    *,
    activation_id: str,
    reason: str,
) -> dict[str, object]:
    artifacts = record.get('artifacts') if isinstance(record.get('artifacts'), dict) else {}
    task_root = Path(context.project.project_root) / str(record.get('task_root') or '')
    plan_root = Path(context.project.project_root) / str(record.get('plan_root') or '')
    return {
        'schema_version': 1,
        'record_type': 'ccb_loop_task_detailer_activation',
        'activation_id': activation_id,
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'task_id': record.get('task_id'),
        'task_status': record.get('status'),
        'action': 'activate_task_detailer',
        'reason_for_activation': reason,
        'required_next_output': 'task-scoped detail docs, stable summary backfill, detail packet, and detail readiness',
        'plan_brief_ref': str((plan_root / 'brief.md').relative_to(context.project.project_root)),
        'task_packet_root': str(task_root.relative_to(context.project.project_root)),
        'detail_root': str((task_root / 'details').relative_to(context.project.project_root)),
        'artifact_refs': {
            kind: artifact.get('path')
            for kind, artifact in sorted(artifacts.items())
            if isinstance(artifact, dict) and artifact.get('path')
        },
        'script_write_rules': [
            'Do not edit roadmap, decisions, open questions, task status, index, current_loop, runtime capacity, or tmux state directly.',
            'Return detail_design, detail_summary, and detail_packet artifacts for script import.',
            'Return macro-adjustment-request as an artifact/ref; do not apply it as planner authority.',
            'Do not start worker/checker/orchestrator execution from this activation.',
        ],
        'stop_limits': [
            'one task_detailer activation per loop runner --once',
            'no recursive execution inside detail activation',
            'detail links and stable summary preferred over planner-owned detail body',
        ],
    }


def _plan_reviewer_activation_packet(
    context,
    record: dict[str, object],
    *,
    activation_id: str,
    reason: str,
) -> dict[str, object]:
    artifacts = record.get('artifacts') if isinstance(record.get('artifacts'), dict) else {}
    task_root = Path(context.project.project_root) / str(record.get('task_root') or '')
    return {
        'schema_version': 1,
        'record_type': 'ccb_loop_plan_reviewer_activation',
        'activation_id': activation_id,
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'task_id': record.get('task_id'),
        'task_status': record.get('status'),
        'action': 'activate_plan_reviewer',
        'reason_for_activation': reason,
        'required_next_output': 'review artifact and readiness recommendation',
        'task_packet_root': str(task_root.relative_to(context.project.project_root)),
        'artifact_refs': {
            kind: artifact.get('path')
            for kind, artifact in sorted(artifacts.items())
            if isinstance(artifact, dict) and artifact.get('path')
        },
        'detail_refs': {
            kind: artifact.get('path')
            for kind, artifact in sorted(artifacts.items())
            if isinstance(artifact, dict) and str(kind).startswith('detail_') and artifact.get('path')
        },
        'script_write_rules': [
            'Do not edit task status, index, or current_loop directly.',
            'Use ccb plan task-artifact --kind review to import the review.',
            'Use ccb plan task-status --status ready only after review is imported.',
            'Return not_ready, needs_clarification, blocked, or ready without lowering acceptance criteria.',
        ],
        'stop_limits': [
            'one plan_reviewer activation per loop runner --once',
            'no recursive execution inside review activation',
            'artifact links preferred over pasted runtime logs',
        ],
    }


def _question_refs(artifacts: dict[str, object]) -> list[str]:
    refs: list[str] = []
    for kind, artifact in sorted(artifacts.items()):
        if 'question' not in str(kind):
            continue
        if isinstance(artifact, dict) and artifact.get('path'):
            refs.append(str(artifact['path']))
    return refs


def _planner_question_refs(context, record: dict[str, object]) -> dict[str, object] | tuple[str, ...]:
    task_id = str(record.get('task_id') or '').strip()
    if task_id:
        refs = question_refs(context, task_id)
        if int(refs.get('artifact_count') or 0) > 0:
            return refs
    artifacts = record.get('artifacts') if isinstance(record.get('artifacts'), dict) else {}
    return tuple(_question_refs(artifacts))


def _compact_artifacts(context, artifacts: dict[str, object], kinds) -> dict[str, dict[str, object]]:
    root = Path(context.project.project_root)
    compact: dict[str, dict[str, object]] = {}
    for kind in sorted(kinds):
        artifact = artifacts.get(kind) if isinstance(artifacts, dict) else None
        if not isinstance(artifact, dict):
            continue
        relative_path = str(artifact.get('path') or '').strip()
        if not relative_path:
            continue
        item: dict[str, object] = {'path': relative_path}
        try:
            text = (root / relative_path).read_text(encoding='utf-8').strip()
        except FileNotFoundError:
            text = ''
        if text:
            limit = 4000
            item['content'] = text[:limit]
            item['truncated'] = len(text) > limit
        compact[kind] = item
    return compact


def _orchestrator_message(activation: dict[str, object]) -> str:
    return (
        'Role: ccb_orchestrator\n'
        f"Activation id: {activation.get('activation_id')}\n"
        f"Task: {activation.get('task_id')}\n"
        f"Status: {activation.get('task_status')}\n"
        f"Reason: {activation.get('reason_for_activation')}\n"
        f"Task packet root: {activation.get('task_packet_root')}\n"
        f"Artifact refs: {activation.get('artifact_refs')}\n"
        f"Compact artifacts: {activation.get('compact_artifacts')}\n"
        f"Allowed routes: {', '.join(_ORCHESTRATOR_ROUTES)}\n\n"
        'Required reply-only output:\n'
        '- route: <one of direct_execution|needs_detail|macro_adjustment_request|blocked|partial_completion>\n'
        '- orchestration_notes: compact rationale and citations to task_packet and execution_contract refs\n\n'
        'Authority boundary:\n'
        '- Reply only; do not run ccb, ccb_test, artifact import commands, or wrapper commands.\n'
        '- Supervisor/script-owned import will record orchestration_notes with the selected route after reviewing this reply.\n'
        '- do not edit task index, status, current_loop, runtime capacity, topology, or task artifacts directly\n'
        '- do not rely on provider reply text as durable route/status authority\n'
        '- do not start task_detailer, worker, reviewer, loop_run_once, or topology dispatch from this activation'
    )


def _planner_message(activation: dict[str, object]) -> str:
    return (
        'Role: planner\n'
        f"Activation id: {activation.get('activation_id')}\n"
        f"Task: {activation.get('task_id')}\n"
        f"Status: {activation.get('task_status')}\n"
        f"Reason: {activation.get('reason_for_activation')}\n"
        f"Task packet root: {activation.get('task_packet_root')}\n"
        f"Artifact refs: {activation.get('artifact_refs')}\n"
        f"Open question refs: {activation.get('open_question_refs')}\n"
        f"Round evidence refs: {activation.get('round_evidence_refs')}\n\n"
        'Required next output:\n'
        '- draft or update task-packet artifacts\n'
        '- keep brief.md compact; do not include task_detailer detail bodies\n'
        '- readiness recommendation: ready|needs_clarification|blocked|not_ready\n'
        '- candidate questions only when current-phase user input is blocking\n\n'
        'Script write rules:\n'
        '- use CCB plan commands or host-provided wrappers for authoritative writes\n'
        '- do not edit task index, status, current_loop, runtime capacity, or tmux state directly\n'
        '- do not start worker/checker/orchestrator execution from this activation'
    )


def _task_detailer_message(activation: dict[str, object]) -> str:
    return (
        'Role: task_detailer\n'
        f"Activation id: {activation.get('activation_id')}\n"
        f"Task: {activation.get('task_id')}\n"
        f"Status: {activation.get('task_status')}\n"
        f"Reason: {activation.get('reason_for_activation')}\n"
        f"Plan brief ref: {activation.get('plan_brief_ref')}\n"
        f"Task packet root: {activation.get('task_packet_root')}\n"
        f"Detail root: {activation.get('detail_root')}\n"
        f"Artifact refs: {activation.get('artifact_refs')}\n\n"
        'Required next output:\n'
        '- task-scoped detail design, stable brief-update summary, and detail packet manifest\n'
        '- detail readiness recommendation: detail_ready|needs_clarification|blocked|not_ready\n'
        '- macro-adjustment request only as an artifact/ref when macro assumptions need planner review\n\n'
        'Script write rules:\n'
        '- use CCB plan commands or host-provided wrappers for authoritative writes\n'
        '- do not edit roadmap, task index, status, current_loop, runtime capacity, or tmux state directly\n'
        '- do not start worker/checker/orchestrator execution from this activation'
    )


def _plan_reviewer_message(activation: dict[str, object]) -> str:
    return (
        'Role: plan_reviewer\n'
        f"Activation id: {activation.get('activation_id')}\n"
        f"Task: {activation.get('task_id')}\n"
        f"Status: {activation.get('task_status')}\n"
        f"Reason: {activation.get('reason_for_activation')}\n"
        f"Task packet root: {activation.get('task_packet_root')}\n"
        f"Artifact refs: {activation.get('artifact_refs')}\n\n"
        'Required next output:\n'
        '- review artifact covering ambiguity, risk, acceptance, and verification\n'
        '- readiness recommendation: ready|needs_clarification|blocked|not_ready\n\n'
        'Script write rules:\n'
        '- use CCB plan commands or host-provided wrappers for authoritative writes\n'
        '- do not edit task index, status, current_loop, runtime capacity, or tmux state directly\n'
        '- do not start worker/checker/orchestrator execution from this activation'
    )


def _activation_path(context, activation_id: str) -> Path:
    return Path(context.paths.runtime_state_root) / 'runtime' / 'loops' / 'activations' / f'{activation_id}.json'


def _single_job(jobs: tuple[dict, ...], *, target: str) -> dict:
    if len(jobs) != 1:
        raise RuntimeError(f'expected one ask job for {target}; got {len(jobs)}')
    job = dict(jobs[0])
    if not str(job.get('job_id') or ''):
        raise RuntimeError(f'ask job for {target} did not return job_id')
    return job


def _first_job_id(payload: dict[str, object]) -> str:
    for key in ('worker', 'reviewer', 'aggregation', 'round_checker'):
        value = payload.get(key)
        if isinstance(value, dict) and str(value.get('job_id') or '').strip():
            return str(value['job_id'])
    return ''


__all__ = ['loop_runner_once']
