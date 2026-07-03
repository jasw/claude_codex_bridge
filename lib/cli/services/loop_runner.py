from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import uuid4

from cli.models import ParsedAskCommand
from storage.atomic import atomic_write_json, atomic_write_text

from .ask import submit_ask, watch_ask_job
from .loop_run_once import loop_run_once
from .plan_tasks import _runner_action_for_record, find_first_actionable_task, plan_task
from .questions import question_refs
from .topology_dispatch import find_first_topology_dispatch_task, maybe_run_topology_dispatch

_PLANNER_BUNDLE_SCHEMA = 'ccb.loop.planner_artifact_bundle/v1'
_TASK_DETAILER_BUNDLE_SCHEMA = 'ccb.loop.task_detailer_artifact_bundle/v1'
_PLAN_REVIEWER_BUNDLE_SCHEMA = 'ccb.loop.plan_reviewer_artifact_bundle/v1'
_PLANNER_ARTIFACT_KINDS = frozenset({'brief', 'requirements', 'acceptance', 'verification', 'risk', 'handoff'})
_TASK_DETAILER_ARTIFACT_KINDS = frozenset(
    {'detail_design', 'detail_summary', 'detail_packet', 'macro_adjustment_request'}
)
_PLAN_REVIEWER_ARTIFACT_KINDS = frozenset({'review'})
_ARTIFACT_KIND_ALIASES = {
    'plan_brief': 'brief',
    'plan-brief': 'brief',
    'acceptance_criteria': 'acceptance',
    'acceptance-criteria': 'acceptance',
    'verification_contract': 'verification',
    'verification-contract': 'verification',
    'risk_notes': 'risk',
    'risk-notes': 'risk',
    'task_detail_design': 'detail_design',
    'task-detail-design': 'detail_design',
    'detail-design': 'detail_design',
    'brief_update_summary': 'detail_summary',
    'brief-update-summary': 'detail_summary',
    'detail-summary': 'detail_summary',
    'detail-packet': 'detail_packet',
    'detail_packet_manifest': 'detail_packet',
    'detail-packet-manifest': 'detail_packet',
    'macro_adjustment': 'macro_adjustment_request',
    'macro-adjustment': 'macro_adjustment_request',
    'macro-adjustment-request': 'macro_adjustment_request',
}


def loop_runner_once(context, command, services=None) -> dict[str, object]:
    deps = _deps(services)
    task = deps.find_topology_dispatch_task(context)
    if task is None:
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
    if runner_action == 'execute':
        return _run_execution_round(context, command, deps, task)
    if runner_action == 'activate_planner':
        return _activate_planner(context, command, deps, task)
    if runner_action == 'activate_task_detailer':
        return _activate_task_detailer(context, command, deps, task)
    if runner_action == 'activate_plan_reviewer':
        return _activate_plan_reviewer(context, command, deps, task)
    return _stop_without_activation(context, task)


def _run_execution_round(context, command, deps, task: dict[str, object]) -> dict[str, object]:
    record = task['record']
    task_id = str(record.get('task_id') or '')
    current_loop = str(record.get('current_loop') or '').strip()
    if current_loop:
        loop_id = current_loop
        bind = {'action': 'task-bind-loop', 'task_id': task_id, 'status': record.get('status'), 'idempotent': True}
        round_payload = deps.topology_dispatch(context, command, deps, task=task, loop_id=loop_id)
        if round_payload is None:
            raise RuntimeError(
                f'loop runner task {task_id} is bound to loop {loop_id}, but no topology dispatch graph exists'
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
            message=_planner_message(
                activation,
                include_import_bundle=bool(getattr(command, 'consume_role_output', False)),
            ),
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
    if bool(getattr(command, 'consume_role_output', False)):
        imported = _consume_role_output(
            context,
            command,
            deps,
            activation=activation,
            activation_path=activation_path,
            task_id=task_id,
            target='planner',
            expected_schema=_PLANNER_BUNDLE_SCHEMA,
            allowed_artifacts=_PLANNER_ARTIFACT_KINDS,
            role_action='imported_planner_output',
        )
        if imported is not None:
            return imported
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
            message=_plan_reviewer_message(
                activation,
                include_import_bundle=bool(getattr(command, 'consume_role_output', False)),
            ),
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
    if bool(getattr(command, 'consume_role_output', False)):
        imported = _consume_role_output(
            context,
            command,
            deps,
            activation=activation,
            activation_path=activation_path,
            task_id=task_id,
            target='plan_reviewer',
            expected_schema=_PLAN_REVIEWER_BUNDLE_SCHEMA,
            allowed_artifacts=_PLAN_REVIEWER_ARTIFACT_KINDS,
            role_action='imported_plan_reviewer_output',
        )
        if imported is not None:
            return imported
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
        'next_owner': 'plan_reviewer',
        'activation_id': activation_id,
        'activation_path': str(activation_path),
        'ask': activation['ask'],
        'next_activation': 'stop_after_one_activation',
    }


def _activate_task_detailer(context, command, deps, task: dict[str, object]) -> dict[str, object]:
    record = dict(task['record'])
    task_id = str(record.get('task_id') or '')
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
            message=_task_detailer_message(
                activation,
                include_import_bundle=bool(getattr(command, 'consume_role_output', False)),
            ),
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
    if bool(getattr(command, 'consume_role_output', False)):
        imported = _consume_role_output(
            context,
            command,
            deps,
            activation=activation,
            activation_path=activation_path,
            task_id=task_id,
            target='task_detailer',
            expected_schema=_TASK_DETAILER_BUNDLE_SCHEMA,
            allowed_artifacts=_TASK_DETAILER_ARTIFACT_KINDS,
            role_action='imported_task_detailer_output',
        )
        if imported is not None:
            return imported
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
        'next_owner': 'task_detailer',
        'activation_id': activation_id,
        'activation_path': str(activation_path),
        'ask': activation['ask'],
        'next_activation': 'stop_after_one_activation',
    }


def _stop_without_activation(context, task: dict[str, object]) -> dict[str, object]:
    record = dict(task['record'])
    action = str(task.get('runner_action') or 'stop')
    payload = {
        'schema_version': 1,
        'record_type': 'ccb_loop_runner_once',
        'loop_runner_status': 'paused' if action == 'paused' else action,
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'action': action,
        'reason': task.get('runner_reason'),
        'task_id': record.get('task_id'),
        'task_status': record.get('status'),
        'next_owner': task.get('next_owner'),
        'next_activation': 'none',
    }
    if action == 'paused' and str(record.get('task_id') or '').strip():
        payload['question_refs'] = question_refs(context, record.get('task_id'))
    return payload


def _deps(services):
    services = services or SimpleNamespace()
    return SimpleNamespace(
        loop_run_once=getattr(services, 'loop_run_once', loop_run_once),
        plan_task=getattr(services, 'plan_task', plan_task),
        submit_ask=getattr(services, 'submit_ask', submit_ask),
        watch_ask_job=getattr(services, 'watch_ask_job', watch_ask_job),
        topology_dispatch=getattr(services, 'topology_dispatch', maybe_run_topology_dispatch),
        find_topology_dispatch_task=getattr(services, 'find_topology_dispatch_task', find_first_topology_dispatch_task),
        services=services,
    )


def _consume_role_output(
    context,
    command,
    deps,
    *,
    activation: dict[str, object],
    activation_path: Path,
    task_id: str,
    target: str,
    expected_schema: str,
    allowed_artifacts: frozenset[str],
    role_action: str,
) -> dict[str, object] | None:
    ask = activation.get('ask') if isinstance(activation.get('ask'), dict) else {}
    job_id = str(ask.get('job_id') or '').strip()
    if not job_id:
        return None
    batch = deps.watch_ask_job(context, job_id, StringIO(), timeout=getattr(command, 'timeout_s', None), emit_output=False)
    role_output = {
        'target': target,
        'job_id': job_id,
        'status': batch.status,
        'terminal': bool(batch.terminal),
    }
    activation['role_output'] = role_output
    bundle = _extract_role_output_bundle(batch.reply, expected_schema=expected_schema)
    if bundle is None:
        role_output['import_status'] = 'no_import_bundle'
        atomic_write_json(activation_path, activation)
        return None

    imported = _import_role_bundle(
        context,
        deps,
        bundle,
        activation_id=str(activation.get('activation_id') or ''),
        task_id=task_id,
        target=target,
        job_id=job_id,
        allowed_artifacts=allowed_artifacts,
    )
    role_output['import_status'] = imported['import_status']
    role_output['imported_artifacts'] = imported['imported_artifacts']
    if imported.get('status_request'):
        role_output['status_request'] = imported['status_request']
    activation['role_output'] = role_output
    atomic_write_json(activation_path, activation)
    return {
        'schema_version': 1,
        'record_type': 'ccb_loop_runner_once',
        'loop_runner_status': 'ok',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'action': role_action,
        'reason': activation.get('reason_for_activation'),
        'task_id': task_id,
        'task_status': imported.get('task_status'),
        'next_owner': imported.get('next_owner'),
        'activation_id': activation.get('activation_id'),
        'activation_path': str(activation_path),
        'ask': activation.get('ask'),
        'role_output': role_output,
        'import': imported,
        'next_activation': imported.get('next_activation'),
    }


def _extract_role_output_bundle(reply: str, *, expected_schema: str) -> dict[str, object] | None:
    for candidate in _json_candidates(reply):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and str(payload.get('schema') or '') == expected_schema:
            return payload
    return None


def _json_candidates(text: str) -> tuple[str, ...]:
    stripped = str(text or '').strip()
    candidates: list[str] = []
    if stripped.startswith('{') and stripped.endswith('}'):
        candidates.append(stripped)
    for match in re.finditer(r'```(?:json)?\s*(.*?)\s*```', stripped, re.DOTALL):
        block = match.group(1).strip()
        if block.startswith('{') and block.endswith('}'):
            candidates.append(block)
    marker = 'CCB_PLAN_IMPORT:'
    if marker in stripped:
        tail = stripped.split(marker, 1)[1].strip()
        if tail.startswith('{') and tail.endswith('}'):
            candidates.append(tail)
    return tuple(candidates)


def _import_role_bundle(
    context,
    deps,
    bundle: dict[str, object],
    *,
    activation_id: str,
    task_id: str,
    target: str,
    job_id: str,
    allowed_artifacts: frozenset[str],
) -> dict[str, object]:
    bundle_task_id = str(bundle.get('task_id') or '').strip()
    if bundle_task_id and bundle_task_id != task_id:
        raise ValueError(f'{target} output task_id mismatch: {bundle_task_id} != {task_id}')
    imported_artifacts: list[dict[str, object]] = []
    import_root = _role_import_root(context, activation_id=activation_id, target=target)
    role_id = str(bundle.get('role_id') or bundle.get('planner_role_id') or '').strip()
    for kind, text in _bundle_artifacts(bundle):
        normalized_kind = _normalize_artifact_kind(kind)
        if normalized_kind not in allowed_artifacts:
            allowed = ', '.join(sorted(allowed_artifacts))
            raise ValueError(f'{target} output artifact kind {kind!r} is not allowed; expected one of: {allowed}')
        artifact_path = import_root / f'{normalized_kind}.md'
        atomic_write_text(artifact_path, text)
        payload = deps.plan_task(
            context,
            SimpleNamespace(
                action='task-artifact',
                task_id=task_id,
                artifact_kind=normalized_kind,
                file_path=str(artifact_path),
                actor_source='loop_runner_role_output',
                actor=target,
                actor_role=role_id,
                job_id=job_id,
            ),
        )
        imported_artifacts.append(_compact_artifact_payload(payload))

    status_request = _status_request(bundle)
    status_payload: dict[str, object] | None = None
    if target == 'plan_reviewer' and status_request == 'ready':
        status_payload = deps.plan_task(
            context,
            SimpleNamespace(action='task-status', task_id=task_id, status='ready'),
        )
    elif target == 'task_detailer' and status_request in {'detail_ready', 'ready_for_review'}:
        status_payload = deps.plan_task(
            context,
            SimpleNamespace(action='task-status', task_id=task_id, status='detail_ready'),
        )
    elif target == 'task_detailer' and status_request in {'ready', 'running', 'done'}:
        raise ValueError(f'task_detailer output cannot request authoritative status: {status_request}')
    show = deps.plan_task(context, SimpleNamespace(action='task-show', task_id=task_id))
    record = show.get('task') if isinstance(show.get('task'), dict) else {}
    next_action = _runner_action_for_record(record)
    return {
        'import_status': 'imported',
        'task_id': task_id,
        'task_status': show.get('status'),
        'status_request': status_request,
        'status': _compact_plan_payload(status_payload or {}) if status_payload is not None else None,
        'imported_artifacts': tuple(imported_artifacts),
        'next_owner': (next_action or {}).get('next_owner'),
        'next_activation': (next_action or {}).get('action') or 'none',
    }


def _role_import_root(context, *, activation_id: str, target: str) -> Path:
    safe_activation = activation_id or f'act-{uuid4().hex[:12]}'
    root = Path(context.paths.runtime_state_root) / 'runtime' / 'loops' / 'activations' / safe_activation / 'imports' / target
    root.mkdir(parents=True, exist_ok=True)
    return root


def _bundle_artifacts(bundle: dict[str, object]) -> tuple[tuple[str, str], ...]:
    raw = bundle.get('artifacts')
    items: list[tuple[str, str]] = []
    if isinstance(raw, dict):
        for kind, value in raw.items():
            text = _artifact_text(value)
            if text is not None:
                items.append((str(kind), text))
    elif isinstance(raw, list):
        for value in raw:
            if not isinstance(value, dict):
                continue
            kind = str(value.get('kind') or '').strip()
            text = _artifact_text(value)
            if kind and text is not None:
                items.append((kind, text))
    if not items:
        raise ValueError('role output bundle requires at least one artifact')
    return tuple(items)


def _artifact_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ('content', 'text', 'markdown'):
            raw = value.get(key)
            if isinstance(raw, str):
                return raw
    return None


def _normalize_artifact_kind(kind: str) -> str:
    key = str(kind or '').strip().lower()
    return _ARTIFACT_KIND_ALIASES.get(key, key)


def _status_request(bundle: dict[str, object]) -> str:
    raw = bundle.get('status_request')
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    readiness = bundle.get('readiness')
    if isinstance(readiness, dict):
        status = readiness.get('status')
        if isinstance(status, str):
            normalized = status.strip().lower()
            if normalized == 'ready_for_review':
                return 'ready_for_review'
            return normalized
    return ''


def _compact_artifact_payload(payload: dict[str, object]) -> dict[str, object]:
    artifact = payload.get('artifact') if isinstance(payload.get('artifact'), dict) else {}
    return {
        'kind': artifact.get('kind'),
        'path': artifact.get('path'),
        'sha256': artifact.get('sha256'),
        'bytes': artifact.get('bytes'),
        'actor': artifact.get('actor'),
    }


def _round_result(payload: dict[str, object]) -> tuple[str, str]:
    declared = _declared_round_result(payload)
    if declared is not None:
        return declared, 'round_checker_reply'
    if str(payload.get('loop_run_status') or '') == 'ok':
        return 'blocked', 'missing_round_checker_result'
    return 'blocked', 'loop_run_status'


def _declared_round_result(payload: dict[str, object]) -> str | None:
    checker = payload.get('round_checker') if isinstance(payload.get('round_checker'), dict) else {}
    reply = str(checker.get('reply') or '')
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
        return mapping.get(value)
    return None


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


def _next_activation(status: object) -> str:
    value = str(status or '')
    if value == 'done':
        return 'stop'
    if value in {'partial', 'replan_required'}:
        return 'planner'
    if value == 'blocked':
        return 'frontdesk_or_recovery'
    return 'inspect'


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


def _planner_message(activation: dict[str, object], *, include_import_bundle: bool = False) -> str:
    message = (
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
    if not include_import_bundle:
        return message
    return (
        message
        + '\n\n'
        'Optional machine import bundle:\n'
        '- If the host requested role-output consumption, return a single JSON object with schema '
        f'"{_PLANNER_BUNDLE_SCHEMA}", task_id, artifacts, and readiness.\n'
        '- artifacts may include brief, requirements, acceptance, verification, risk, and handoff only.'
    )


def _task_detailer_message(activation: dict[str, object], *, include_import_bundle: bool = False) -> str:
    message = (
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
    if not include_import_bundle:
        return message
    return (
        message
        + '\n\n'
        'Optional machine import bundle:\n'
        '- If the host requested role-output consumption, return a single JSON object with schema '
        f'"{_TASK_DETAILER_BUNDLE_SCHEMA}", task_id, artifacts, and readiness.\n'
        '- artifacts may include detail_design, detail_summary, detail_packet, and macro_adjustment_request only.'
    )


def _plan_reviewer_message(activation: dict[str, object], *, include_import_bundle: bool = False) -> str:
    message = (
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
    if not include_import_bundle:
        return message
    return (
        message
        + '\n\n'
        'Optional machine import bundle:\n'
        '- If the host requested role-output consumption, return a single JSON object with schema '
        f'"{_PLAN_REVIEWER_BUNDLE_SCHEMA}", task_id, artifacts.review, and readiness.status.\n'
        '- The host may import review and commit ready only after script validation succeeds.'
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
