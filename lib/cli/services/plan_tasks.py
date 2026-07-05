from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from storage.atomic import atomic_write_json, atomic_write_text
from storage.locks import file_lock


_SEGMENT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$')
_SLUG_RE = re.compile(r'[^A-Za-z0-9_-]+')
_ARTIFACT_FILES = {
    'brief': 'brief.md',
    'task_packet': 'task_packet.md',
    'execution_contract': 'execution_contract.md',
    'orchestration_notes': 'orchestration_notes.md',
    'round_summary': 'round_summary.md',
    'requirements': 'requirements.md',
    'acceptance': 'acceptance-criteria.md',
    'verification': 'verification-contract.md',
    'risk': 'risk-notes.md',
    'handoff': 'handoff.md',
    'detail_design': 'details/task-detail-design.md',
    'detail_summary': 'details/brief-update-summary.md',
    'detail_packet': 'details/detail-packet.manifest.json',
    'detail_step_1': 'details/steps/step-1.md',
    'detail_step_2': 'details/steps/step-2.md',
    'macro_adjustment_request': 'details/macro-adjustment-request.json',
    'blocker_evidence': 'blocker-evidence.md',
    'review': 'review.md',
    'completion': 'completion.md',
    'round_pass': 'round-pass.md',
    'round_partial': 'round-partial.md',
    'round_replan': 'round-replan.md',
    'round_blocker': 'round-blocker.md',
}
_PLAN_ROOT_ARTIFACTS = frozenset({'brief'})
_DETAIL_READY_REQUIRED = frozenset({'detail_design', 'detail_summary', 'detail_packet'})
_ORCHESTRATION_READY_REQUIRED = frozenset({'task_packet', 'execution_contract'})
_READY_REQUIRED = frozenset({'requirements', 'acceptance', 'verification', 'handoff', 'review'})
_PLAN_REVIEW_REQUIRED = frozenset({'requirements', 'acceptance', 'verification', 'handoff'})
_TERMINAL_STATUSES = frozenset({'done', 'blocked'})
_VALID_NEXT_OWNERS = frozenset({'planner', 'orchestrator', 'frontdesk', 'terminal'})
_VALID_ORCHESTRATOR_ROUTES = frozenset(
    {'direct_execution', 'needs_detail', 'macro_adjustment_request', 'blocked', 'partial_completion'}
)
_ROUND_RESULT_MAP = {
    'pass': ('round_summary', 'done', 'round_pass'),
    'partial': ('round_summary', 'partial', 'round_partial'),
    'replan_required': ('round_summary', 'replan_required', 'round_replan'),
    'blocked': ('round_summary', 'blocked', 'round_blocker'),
}
_PLANNER_COMPACT_IMPORT_POLICIES = {
    'detail_summary': {
        'policy': 'planner_compact_import',
        'authority': 'artifact_only_no_plan_mutation',
        'planner_action': 'review_for_brief_or_task_refs',
        'allowed_updates': ['brief', 'roadmap_status_handoff', 'decision_links', 'open_question_links', 'task_refs'],
        'forbidden_updates': ['detail_design_body', 'source_evidence_map', 'task_local_clarification', 'worker_handoff_detail'],
    },
    'macro_adjustment_request': {
        'policy': 'planner_compact_import',
        'authority': 'request_only_no_auto_mutation',
        'planner_action': 'review_before_plan_update',
        'allowed_updates': ['brief', 'roadmap_status_handoff', 'decision_links', 'open_question_links', 'task_refs'],
        'forbidden_updates': ['automatic_roadmap_mutation', 'automatic_decision_mutation', 'automatic_status_mutation'],
    },
    'round_summary': {
        'policy': 'planner_compact_import',
        'authority': 'script_owned_task_import_round',
        'planner_action': 'rehydrate_for_brief_or_next_task_planning',
        'allowed_updates': ['brief', 'roadmap_status_handoff', 'decision_links', 'open_question_links', 'task_refs'],
        'forbidden_updates': ['provider_reply_authority', 'worker_handoff_detail', 'source_evidence_map'],
    },
}
_MAINLINE_STATUSES = frozenset(
    {
        'draft',
        'ready_for_orchestration',
        'running',
        'partial',
        'replan_required',
        'done',
        'blocked',
    }
)
_LEGACY_STATUSES = frozenset({'needs_clarification', 'detail_ready', 'ready'})
_VALID_STATUSES = _MAINLINE_STATUSES | _LEGACY_STATUSES
_STATUS_EDGES = {
    'draft': {'draft', 'needs_clarification', 'detail_ready', 'ready', 'ready_for_orchestration'},
    'needs_clarification': {'needs_clarification', 'draft'},
    'detail_ready': {'detail_ready', 'ready'},
    'ready': {'ready', 'running'},
    'ready_for_orchestration': {
        'ready_for_orchestration',
        'detail_ready',
        'running',
        'replan_required',
        'blocked',
    },
    'running': {'running', 'partial', 'replan_required', 'done', 'blocked'},
    'partial': {'partial', 'replan_required', 'done'},
    'replan_required': {'replan_required', 'draft'},
    'blocked': {'blocked', 'draft'},
    'done': {'done'},
}


def plan_task(context, command) -> dict[str, object]:
    action = str(command.action or '').strip().lower()
    if action == 'task-create':
        return _task_create(context, command)
    if action == 'task-artifact':
        return _task_artifact(context, command)
    if action == 'task-status':
        return _task_status(context, command)
    if action == 'task-bind-loop':
        return _task_bind_loop(context, command)
    if action == 'task-import-round':
        return _task_import_round(context, command)
    if action == 'task-show':
        return _task_show(context, command)
    if action == 'task-list':
        return _task_list(context, command)
    if action == 'breadcrumb':
        payload = _task_show(context, command)
        payload['breadcrumb'] = _breadcrumb_text(payload)
        return payload
    raise ValueError(f'unsupported plan task action: {action}')


def _task_create(context, command) -> dict[str, object]:
    plan_slug = _normalize_segment(command.plan_slug, label='plan')
    title = str(command.title or '').strip()
    if not title:
        raise ValueError('plan task-create requires --title')
    plan_root = _plan_root(context, plan_slug)
    if not plan_root.is_dir():
        raise ValueError(f'unknown plan slug {plan_slug!r}; expected {plan_root}')
    tasks_root = plan_root / 'tasks'
    index = _load_index(tasks_root, plan_slug=plan_slug, plan_root=plan_root)
    task_id = _normalize_task_id(command.task_id) if command.task_id else _new_task_id(title=title, tasks_root=tasks_root)
    if _find_task(context, task_id) is not None:
        raise ValueError(f'plan task already exists: {task_id}')
    now = _utc_now()
    task_root = tasks_root / task_id
    task_root.mkdir(parents=True, exist_ok=False)
    record = {
        'task_id': task_id,
        'title': title,
        'plan_slug': plan_slug,
        'plan_root': str(plan_root.relative_to(context.project.project_root)),
        'status': 'draft',
        'current_loop': None,
        'owner': 'planner',
        'next_owner': 'planner',
        'activation_reason': 'task_created',
        'created_at': now,
        'updated_at': now,
        'task_root': str(task_root.relative_to(context.project.project_root)),
        'artifacts': {},
    }
    index['tasks'].append(record)
    index['updated_at'] = now
    _write_index(tasks_root, index)
    _write_task_readme(context, record)
    return _payload(context, action='task-create', record=record)


def _task_artifact(context, command) -> dict[str, object]:
    task = _require_task(context, command.task_id)
    record = dict(task['record'])
    artifact_kind = str(command.artifact_kind or '').strip().lower()
    if artifact_kind not in _ARTIFACT_FILES:
        known = ', '.join(sorted(_ARTIFACT_FILES))
        raise ValueError(f'unknown plan artifact kind {artifact_kind!r}; expected one of: {known}')
    if artifact_kind == 'round_summary':
        raise ValueError('plan task-artifact cannot import round_summary directly; use plan task-import-round')
    extra: dict[str, object] = _planner_compact_import_extra(artifact_kind)
    route = _optional_orchestrator_route(command)
    if route:
        if artifact_kind != 'orchestration_notes':
            raise ValueError('plan task artifact --route is only valid for orchestration_notes')
        extra['orchestrator_route'] = route
    record, artifact = _import_text_artifact(
        context,
        record,
        artifact_kind=artifact_kind,
        file_path=command.file_path,
        extra=extra,
        actor=_artifact_actor_metadata(context, command),
    )
    now = str(artifact['imported_at'])
    record['updated_at'] = now
    _replace_record(task['tasks_root'], task['index'], record)
    _write_task_readme(context, record)
    payload = _payload(context, action='task-artifact', record=record)
    payload['artifact'] = artifact
    return payload


def _task_status(context, command) -> dict[str, object]:
    task = _require_task(context, command.task_id)
    record = dict(task['record'])
    status = str(command.status or '').strip().lower()
    if status not in _VALID_STATUSES:
        known = ', '.join(sorted(_VALID_STATUSES))
        raise ValueError(f'unknown plan task status {status!r}; expected one of: {known}')
    current = str(record.get('status') or 'draft')
    if status not in _STATUS_EDGES.get(current, ()):
        raise ValueError(f'invalid plan task status transition: {current} -> {status}')
    _validate_status_requirements(record, status)
    next_owner = _next_owner_for_status_command(command, status=status)
    now = _utc_now()
    record['status'] = status
    record['owner'] = _owner_for_status(status)
    record['next_owner'] = next_owner
    record['activation_reason'] = _activation_reason_for_command(
        command,
        default=f'status:{current}->{status}',
    )
    record['updated_at'] = now
    _replace_record(task['tasks_root'], task['index'], record)
    _write_task_readme(context, record)
    return _payload(context, action='task-status', record=record)


def _task_bind_loop(context, command) -> dict[str, object]:
    loop_id = _normalize_segment(getattr(command, 'loop_id', None), label='loop_id')
    task = _require_task(context, command.task_id)
    with file_lock(_task_lock_path(context, task['record'])):
        task = _require_task(context, command.task_id)
        record = dict(task['record'])
        current_status = str(record.get('status') or 'draft')
        current_loop = str(record.get('current_loop') or '').strip()
        if current_status in _TERMINAL_STATUSES:
            raise ValueError(f'plan task cannot bind terminal status: {current_status}')
        if current_status == 'running':
            if current_loop == loop_id:
                return _payload(context, action='task-bind-loop', record=record)
            raise ValueError(f'plan task already bound to loop: {current_loop or "unknown"}')
        if current_status not in {'ready_for_orchestration', 'ready'}:
            raise ValueError(
                'plan task bind requires status ready_for_orchestration '
                f'or legacy ready; current status is {current_status}'
            )
        _validate_status_requirements(record, current_status)
        if current_loop and current_loop != loop_id:
            raise ValueError(f'plan task already bound to loop: {current_loop}')
        now = _utc_now()
        record['status'] = 'running'
        record['owner'] = 'loop_runner'
        record['next_owner'] = 'orchestrator'
        record['activation_reason'] = f'loop_bound:{loop_id}'
        record['current_loop'] = loop_id
        record['loop_lease'] = {
            'loop_id': loop_id,
            'lease_id': f'lease-{uuid4().hex[:12]}',
            'status': 'active',
            'owner': 'loop_runner',
            'acquired_at': now,
        }
        record['updated_at'] = now
        _replace_record(task['tasks_root'], task['index'], record)
        _write_task_readme(context, record)
        return _payload(context, action='task-bind-loop', record=record)


def _task_import_round(context, command) -> dict[str, object]:
    loop_id = _normalize_segment(getattr(command, 'loop_id', None), label='loop_id')
    result = str(getattr(command, 'result', None) or '').strip().lower()
    if result not in _ROUND_RESULT_MAP:
        known = ', '.join(sorted(_ROUND_RESULT_MAP))
        raise ValueError(f'unknown round result {result!r}; expected one of: {known}')
    artifact_kind, target_status, legacy_artifact_kind = _ROUND_RESULT_MAP[result]
    source_path = _safe_project_file(context.project.project_root, command.file_path)
    text = _read_utf8_artifact(source_path)
    text_sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
    task = _require_task(context, command.task_id)
    with file_lock(_task_lock_path(context, task['record'])):
        task = _require_task(context, command.task_id)
        record = dict(task['record'])
        existing = _existing_round_import(record, loop_id=loop_id, result=result, sha256=text_sha)
        if existing is not None:
            payload = _payload(context, action='task-import-round', record=record)
            payload['artifact'] = existing
            payload['legacy_artifact'] = _legacy_artifact_for_round(record, legacy_artifact_kind)
            payload['round_result'] = result
            payload['idempotent'] = True
            return payload
        current_loop = str(record.get('current_loop') or '').strip()
        if current_loop != loop_id:
            raise ValueError(
                f'plan task round import requires current_loop={loop_id}; '
                f'current_loop is {current_loop or "none"}'
            )
        current_status = str(record.get('status') or 'draft')
        if current_status != 'running':
            raise ValueError(f'plan task round import requires status running; current status is {current_status}')
        record, artifact = _import_text_artifact(
            context,
            record,
            artifact_kind=artifact_kind,
            file_path=command.file_path,
            text=text,
            extra={
                'loop_id': loop_id,
                'round_result': result,
                'legacy_artifact_kind': legacy_artifact_kind,
                **_planner_compact_import_extra(artifact_kind),
            },
            actor=_artifact_actor_metadata(context, command),
        )
        record = _add_round_legacy_alias(record, artifact=artifact, legacy_artifact_kind=legacy_artifact_kind)
        now = str(artifact['imported_at'])
        counters = dict(record.get('round_counters') or {})
        counters[result] = int(counters.get(result) or 0) + 1
        record['round_counters'] = counters
        record['last_round'] = {
            'loop_id': loop_id,
            'result': result,
            'artifact_kind': artifact_kind,
            'legacy_artifact_kind': legacy_artifact_kind,
            'artifact_path': artifact.get('path'),
            'sha256': artifact.get('sha256'),
            'imported_at': artifact.get('imported_at'),
        }
        record['status'] = target_status
        record['owner'] = _owner_for_status(target_status)
        record['next_owner'] = _default_next_owner_for_status(target_status)
        record['activation_reason'] = f'round_summary:{result}'
        record['current_loop'] = None
        lease = dict(record.get('loop_lease') or {})
        if lease:
            lease['status'] = 'imported'
            lease['released_at'] = now
            record['loop_lease'] = lease
        record['updated_at'] = now
        _replace_record(task['tasks_root'], task['index'], record)
        _write_task_readme(context, record)
        payload = _payload(context, action='task-import-round', record=record)
        payload['artifact'] = artifact
        payload['legacy_artifact'] = _legacy_artifact_for_round(record, legacy_artifact_kind)
        payload['round_result'] = result
        payload['idempotent'] = False
        return payload


def _task_show(context, command) -> dict[str, object]:
    task = _require_task(context, command.task_id)
    return _payload(context, action='task-show', record=task['record'])


def _task_list(context, command) -> dict[str, object]:
    plan_slug = _normalize_segment(command.plan_slug, label='plan')
    plan_root = _plan_root(context, plan_slug)
    if not plan_root.is_dir():
        raise ValueError(f'unknown plan slug {plan_slug!r}; expected {plan_root}')
    tasks_root = plan_root / 'tasks'
    index = _load_index(tasks_root, plan_slug=plan_slug, plan_root=plan_root)
    for record in tuple(index.get('tasks') or ()):
        if isinstance(record, dict):
            _validate_task_record(record)
    return {
        'plan_task_status': 'ok',
        'action': 'task-list',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'plan_slug': plan_slug,
        'plan_root': str(plan_root.relative_to(context.project.project_root)),
        'tasks_root': str(tasks_root.relative_to(context.project.project_root)),
        'task_count': len(tuple(index.get('tasks') or ())),
        'tasks': tuple(index.get('tasks') or ()),
    }


def _validate_status_requirements(record: dict[str, object], status: str) -> None:
    artifacts = set((record.get('artifacts') or {}).keys()) if isinstance(record.get('artifacts'), dict) else set()
    if status == 'ready_for_orchestration':
        missing = sorted(_ORCHESTRATION_READY_REQUIRED - artifacts)
        if missing:
            raise ValueError(f'plan task ready_for_orchestration requires artifacts: {", ".join(missing)}')
    if status == 'ready':
        missing = sorted(_READY_REQUIRED - artifacts)
        if missing:
            raise ValueError(f'plan task ready requires artifacts: {", ".join(missing)}')
    if status == 'detail_ready':
        missing = sorted(_DETAIL_READY_REQUIRED - artifacts)
        if missing:
            raise ValueError(f'plan task detail_ready requires artifacts: {", ".join(missing)}')
    if status == 'done' and 'completion' not in artifacts and not _has_round_evidence(record, result='pass'):
        raise ValueError('plan task done requires artifact: completion or round_summary')
    if status == 'partial' and not _has_round_evidence(record, result='partial'):
        raise ValueError('plan task partial requires artifact: round_summary')
    if (
        status == 'replan_required'
        and 'macro_adjustment_request' not in artifacts
        and not _has_round_evidence(record, result='replan_required')
    ):
        raise ValueError('plan task replan_required requires artifact: round_summary or macro_adjustment_request')
    if (
        status == 'blocked'
        and 'completion' not in artifacts
        and 'blocker_evidence' not in artifacts
        and not _has_round_evidence(record, result='blocked')
    ):
        raise ValueError('plan task blocked requires artifact: completion, round_summary, or blocker_evidence')


def _has_round_evidence(record: dict[str, object], *, result: str) -> bool:
    artifact_kind, _target_status, legacy_artifact_kind = _ROUND_RESULT_MAP[result]
    artifacts = record.get('artifacts') if isinstance(record.get('artifacts'), dict) else {}
    artifact = artifacts.get(artifact_kind) if isinstance(artifacts, dict) else None
    if isinstance(artifact, dict) and str(artifact.get('round_result') or '') == result:
        return True
    legacy = artifacts.get(legacy_artifact_kind) if isinstance(artifacts, dict) else None
    return isinstance(legacy, dict)


def _owner_for_status(status: str) -> str:
    if status in {'draft', 'needs_clarification', 'replan_required'}:
        return 'planner'
    if status == 'detail_ready':
        return 'plan_reviewer'
    if status in {'ready', 'ready_for_orchestration', 'running'}:
        return 'loop_runner'
    if status == 'partial':
        return 'planner'
    return 'frontdesk'


def _default_next_owner_for_status(status: str) -> str:
    if status in {'draft', 'partial', 'replan_required', 'detail_ready'}:
        return 'planner'
    if status in {'ready_for_orchestration', 'ready', 'running'}:
        return 'orchestrator'
    if status == 'needs_clarification':
        return 'frontdesk'
    return 'terminal'


def _next_owner_for_status_command(command, *, status: str) -> str:
    raw = str(getattr(command, 'next_owner', None) or '').strip().lower()
    next_owner = raw or _default_next_owner_for_status(status)
    if next_owner not in _VALID_NEXT_OWNERS:
        known = ', '.join(sorted(_VALID_NEXT_OWNERS))
        raise ValueError(f'unknown plan task next_owner {next_owner!r}; expected one of: {known}')
    _validate_status_next_owner(status=status, next_owner=next_owner)
    return next_owner


def _validate_status_next_owner(*, status: str, next_owner: str) -> None:
    expected = _default_next_owner_for_status(status)
    if next_owner != expected:
        raise ValueError(f'plan task status {status} requires next_owner {expected}; got {next_owner}')


def _activation_reason_for_command(command, *, default: str) -> str:
    reason = str(getattr(command, 'activation_reason', None) or '').strip()
    reason = reason or default
    _validate_activation_reason_text(reason)
    return reason


def _validate_activation_reason_text(reason: str) -> None:
    if '\n' in reason or '\r' in reason:
        raise ValueError('plan task activation_reason must be a single line')
    if len(reason) > 240:
        raise ValueError('plan task activation_reason must be 240 characters or fewer')


def _optional_orchestrator_route(command) -> str:
    route = str(getattr(command, 'route', None) or '').strip().lower()
    if not route:
        return ''
    if route not in _VALID_ORCHESTRATOR_ROUTES:
        known = ', '.join(sorted(_VALID_ORCHESTRATOR_ROUTES))
        raise ValueError(f'unknown orchestrator route {route!r}; expected one of: {known}')
    return route


def _payload(context, *, action: str, record: dict[str, object]) -> dict[str, object]:
    task_root = Path(context.project.project_root) / str(record.get('task_root') or '')
    _validate_task_record(record)
    return {
        'plan_task_status': 'ok',
        'action': action,
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'task': record,
        'task_id': record.get('task_id'),
        'status': record.get('status'),
        'next_owner': record.get('next_owner'),
        'current_loop': record.get('current_loop'),
        'activation_reason': record.get('activation_reason'),
        'plan_slug': record.get('plan_slug'),
        'task_root': str(task_root.relative_to(context.project.project_root)),
        'readme_path': str((task_root / 'README.md').relative_to(context.project.project_root)),
    }


def find_first_ready_task(context) -> dict[str, object] | None:
    plantree_root = Path(context.project.project_root) / 'docs' / 'plantree' / 'plans'
    if not plantree_root.is_dir():
        return None
    for index_path in sorted(plantree_root.glob('*/tasks/index.json')):
        tasks_root = index_path.parent
        index = _read_json_object(index_path)
        for record in tuple(index.get('tasks') or ()):
            if not isinstance(record, dict):
                continue
            _validate_task_record(record)
            action = _runner_action_for_record(record)
            if (
                action is not None
                and action['action'] == 'execute'
                and not str(record.get('current_loop') or '').strip()
            ):
                return {
                    'record': record,
                    'index': index,
                    'tasks_root': tasks_root,
                    'runner_action': 'execute',
                    'runner_reason': action['reason'],
                    'next_owner': 'orchestrator',
                }
    return None


def find_first_actionable_task(context) -> dict[str, object] | None:
    plantree_root = Path(context.project.project_root) / 'docs' / 'plantree' / 'plans'
    if not plantree_root.is_dir():
        return None
    candidates: list[dict[str, object]] = []
    for index_path in sorted(plantree_root.glob('*/tasks/index.json')):
        tasks_root = index_path.parent
        index = _read_json_object(index_path)
        for record in tuple(index.get('tasks') or ()):
            if not isinstance(record, dict):
                continue
            _validate_task_record(record)
            action = _runner_action_for_record(record)
            if action is None:
                continue
            candidates.append({
                'record': record,
                'index': index,
                'tasks_root': tasks_root,
                'runner_action': action['action'],
                'runner_reason': action['reason'],
                'next_owner': action['next_owner'],
            })
    if not candidates:
        return None
    priority = {
        'activate_orchestrator': 0,
        'ask_first_execute': 0,
        'ask_first_execution_not_ready': 0,
        'execute': 0,
        'activate_planner': 1,
        'activate_task_detailer': 2,
        'activate_plan_reviewer': 3,
        'planner_next_action_required': 4,
        'blocker_evidence_required': 4,
        'paused': 4,
        'blocked': 5,
        'terminal': 6,
    }
    return min(candidates, key=lambda item: _runner_candidate_priority(item, priority=priority))
    return None


def _runner_candidate_priority(item: dict[str, object], *, priority: dict[str, int]) -> tuple[int, int]:
    action = str(item.get('runner_action') or '')
    record = item.get('record') if isinstance(item.get('record'), dict) else {}
    current_loop = str(record.get('current_loop') or '').strip()
    bound_execution = 1 if action == 'execute' and current_loop else 0
    return priority.get(action, 99), bound_execution


def _runner_action_for_record(record: dict[str, object]) -> dict[str, str] | None:
    if _has_activation_metadata(record):
        return _activation_runner_action_for_record(record)
    return _legacy_runner_action_for_record(record)


def _activation_runner_action_for_record(record: dict[str, object]) -> dict[str, str] | None:
    current_loop = str(record.get('current_loop') or '').strip()
    status = str(record.get('status') or 'draft').strip().lower()
    next_owner = _validated_record_next_owner(record, status=status)
    if next_owner == 'terminal':
        if status == 'blocked':
            return {'action': 'blocked', 'reason': 'blocked_task', 'next_owner': 'terminal'}
        return {'action': 'terminal', 'reason': f'{status}_task', 'next_owner': 'terminal'}
    if status == 'ready_for_orchestration' and next_owner == 'orchestrator' and not current_loop:
        route = _orchestrator_route_for_record(record)
        if not route:
            return {
                'action': 'activate_orchestrator',
                'reason': 'ready_for_orchestration',
                'next_owner': 'orchestrator',
            }
        if route in {'direct_execution', 'partial_completion'}:
            return {
                'action': 'ask_first_execute',
                'reason': f'orchestrator_route_{route}',
                'next_owner': 'orchestrator',
            }
        if route == 'needs_detail':
            if _detail_packet_ready(record):
                return {
                    'action': 'activate_orchestrator',
                    'reason': 'orchestrator_route_needs_detail_detail_ready',
                    'next_owner': 'orchestrator',
                }
            return {
                'action': 'activate_task_detailer',
                'reason': 'orchestrator_route_needs_detail',
                'next_owner': 'orchestrator',
            }
        if route == 'macro_adjustment_request':
            return {
                'action': 'planner_next_action_required',
                'reason': 'orchestrator_route_macro_adjustment_request',
                'next_owner': 'planner',
            }
        if route == 'blocked':
            return {
                'action': 'blocker_evidence_required',
                'reason': 'orchestrator_route_blocked',
                'next_owner': 'frontdesk',
            }
    if status == 'ready' and next_owner == 'orchestrator' and not current_loop:
        return {'action': 'execute', 'reason': 'ready_task', 'next_owner': 'orchestrator'}
    if status == 'running' and next_owner == 'orchestrator' and current_loop:
        return {
            'action': 'ask_first_execution_not_ready',
            'reason': 'running_task_bound_to_loop',
            'next_owner': 'orchestrator',
        }
    if status in {'draft', 'partial', 'replan_required', 'detail_ready'} and next_owner == 'planner' and not current_loop:
        return {'action': 'activate_planner', 'reason': f'{status}_task', 'next_owner': 'planner'}
    if status == 'needs_clarification' and next_owner == 'frontdesk':
        return {'action': 'paused', 'reason': 'needs_clarification', 'next_owner': 'frontdesk'}
    if next_owner == 'frontdesk':
        return {'action': 'paused', 'reason': f'{status}_task', 'next_owner': 'frontdesk'}
    return None


def _legacy_runner_action_for_record(record: dict[str, object]) -> dict[str, str] | None:
    current_loop = str(record.get('current_loop') or '').strip()
    status = str(record.get('status') or 'draft').strip().lower()
    if status == 'ready' and not current_loop:
        return {'action': 'execute', 'reason': 'ready_task', 'next_owner': 'orchestrator'}
    if status == 'draft' and not current_loop:
        if _ready_for_plan_review(record):
            if _needs_task_detail(record):
                return {'action': 'activate_task_detailer', 'reason': 'detail_required', 'next_owner': 'planner'}
            return {'action': 'activate_plan_reviewer', 'reason': 'review_required', 'next_owner': 'planner'}
        return {'action': 'activate_planner', 'reason': f'{status}_task', 'next_owner': 'planner'}
    if status == 'detail_ready' and not current_loop:
        return {'action': 'activate_plan_reviewer', 'reason': 'detail_ready', 'next_owner': 'planner'}
    if status in {'partial', 'replan_required'} and not current_loop:
        return {'action': 'activate_planner', 'reason': f'{status}_task', 'next_owner': 'planner'}
    if status == 'needs_clarification':
        return {'action': 'paused', 'reason': 'needs_clarification', 'next_owner': 'frontdesk'}
    if status == 'blocked':
        return {'action': 'blocked', 'reason': 'blocked_task', 'next_owner': 'terminal'}
    if status in {'done', 'cancelled'}:
        return {'action': 'terminal', 'reason': f'{status}_task', 'next_owner': 'terminal'}
    return None


def _has_activation_metadata(record: dict[str, object]) -> bool:
    return 'next_owner' in record or 'activation_reason' in record


def _validated_record_next_owner(record: dict[str, object], *, status: str) -> str:
    raw = str(record.get('next_owner') or '').strip().lower()
    next_owner = raw or _default_next_owner_for_status(status)
    if next_owner not in _VALID_NEXT_OWNERS:
        known = ', '.join(sorted(_VALID_NEXT_OWNERS))
        raise ValueError(f'unknown plan task next_owner {next_owner!r}; expected one of: {known}')
    _validate_status_next_owner(status=status, next_owner=next_owner)
    return next_owner


def _validate_task_record(record: dict[str, object]) -> None:
    status = str(record.get('status') or 'draft').strip().lower()
    if status not in _VALID_STATUSES:
        known = ', '.join(sorted(_VALID_STATUSES))
        raise ValueError(f'unknown plan task status {status!r}; expected one of: {known}')
    _validated_record_next_owner(record, status=status)
    if 'current_loop' not in record:
        raise ValueError('plan task metadata missing current_loop')
    reason = record.get('activation_reason')
    if reason is not None:
        _validate_activation_reason_text(str(reason))


def _ready_for_plan_review(record: dict[str, object]) -> bool:
    artifacts = set((record.get('artifacts') or {}).keys()) if isinstance(record.get('artifacts'), dict) else set()
    return _PLAN_REVIEW_REQUIRED <= artifacts and 'review' not in artifacts


def _needs_task_detail(record: dict[str, object]) -> bool:
    artifacts = set((record.get('artifacts') or {}).keys()) if isinstance(record.get('artifacts'), dict) else set()
    return 'brief' in artifacts and not _DETAIL_READY_REQUIRED <= artifacts


def _orchestrator_route_for_record(record: dict[str, object]) -> str:
    artifacts = record.get('artifacts') if isinstance(record.get('artifacts'), dict) else {}
    notes = artifacts.get('orchestration_notes') if isinstance(artifacts, dict) else None
    route = str(notes.get('orchestrator_route') or '').strip().lower() if isinstance(notes, dict) else ''
    if route and route not in _VALID_ORCHESTRATOR_ROUTES:
        known = ', '.join(sorted(_VALID_ORCHESTRATOR_ROUTES))
        raise ValueError(f'unknown orchestrator route {route!r}; expected one of: {known}')
    return route


def _detail_packet_ready(record: dict[str, object]) -> bool:
    artifacts = set((record.get('artifacts') or {}).keys()) if isinstance(record.get('artifacts'), dict) else set()
    return _DETAIL_READY_REQUIRED <= artifacts


def task_execution_text(context, task_id: object) -> str:
    task = _require_task(context, task_id)
    record = task['record']
    task_root = Path(context.project.project_root) / str(record.get('task_root') or '')
    payload = _payload(context, action='task-show', record=record)
    sections = [_breadcrumb_text(payload)]
    for title, artifact_kind in (
        ('Task Packet', 'task_packet'),
        ('Execution Contract', 'execution_contract'),
        ('Orchestration Notes', 'orchestration_notes'),
        ('Handoff', 'handoff'),
        ('Acceptance Criteria', 'acceptance'),
        ('Verification Contract', 'verification'),
    ):
        artifact = (record.get('artifacts') or {}).get(artifact_kind) if isinstance(record.get('artifacts'), dict) else None
        path_text = str(artifact.get('path') or '') if isinstance(artifact, dict) else ''
        if not path_text:
            continue
        path = Path(context.project.project_root) / path_text
        try:
            text = path.read_text(encoding='utf-8').strip()
        except FileNotFoundError:
            text = ''
        if text:
            sections.append(f'{title}:\n{text}')
    sections.append(f'Task Root: {task_root.relative_to(context.project.project_root)}')
    return '\n\n'.join(sections)


def _require_task(context, task_id: object) -> dict[str, object]:
    normalized = _normalize_task_id(task_id)
    task = _find_task(context, normalized)
    if task is None:
        raise ValueError(f'plan task not found: {normalized}')
    return task


def _find_task(context, task_id: str) -> dict[str, object] | None:
    plantree_root = Path(context.project.project_root) / 'docs' / 'plantree' / 'plans'
    if not plantree_root.is_dir():
        return None
    matches: list[dict[str, object]] = []
    for index_path in sorted(plantree_root.glob('*/tasks/index.json')):
        tasks_root = index_path.parent
        index = _read_json_object(index_path)
        for record in tuple(index.get('tasks') or ()):
            if isinstance(record, dict) and str(record.get('task_id') or '') == task_id:
                matches.append({'record': record, 'index': index, 'tasks_root': tasks_root})
    if len(matches) > 1:
        raise ValueError(f'plan task id is ambiguous across plans: {task_id}')
    return matches[0] if matches else None


def _load_index(tasks_root: Path, *, plan_slug: str, plan_root: Path) -> dict[str, object]:
    path = tasks_root / 'index.json'
    payload = _read_json_object(path)
    if payload:
        tasks = payload.get('tasks')
        if not isinstance(tasks, list):
            raise ValueError(f'plan task index is invalid: {path}')
        return payload
    return {
        'schema_version': 1,
        'record_type': 'ccb_plan_task_index',
        'plan_slug': plan_slug,
        'plan_root': str(plan_root),
        'updated_at': None,
        'tasks': [],
    }


def _write_index(tasks_root: Path, index: dict[str, object]) -> None:
    atomic_write_json(tasks_root / 'index.json', index)


def _replace_record(tasks_root: Path, index: dict[str, object], record: dict[str, object]) -> None:
    task_id = str(record.get('task_id') or '')
    tasks = []
    replaced = False
    for item in tuple(index.get('tasks') or ()):
        if isinstance(item, dict) and str(item.get('task_id') or '') == task_id:
            tasks.append(record)
            replaced = True
        else:
            tasks.append(item)
    if not replaced:
        raise ValueError(f'plan task not found in index: {task_id}')
    index['tasks'] = tasks
    index['updated_at'] = record.get('updated_at')
    _write_index(tasks_root, index)


def _task_lock_path(context, record: dict[str, object]) -> Path:
    return Path(context.project.project_root) / str(record['task_root']) / 'task.lock'


def _planner_compact_import_extra(artifact_kind: str) -> dict[str, object]:
    policy = _PLANNER_COMPACT_IMPORT_POLICIES.get(artifact_kind)
    if policy is None:
        return {}
    return {'planner_compact_import': dict(policy)}


def _import_text_artifact(
    context,
    record: dict[str, object],
    *,
    artifact_kind: str,
    file_path: object,
    text: str | None = None,
    extra: dict[str, object] | None = None,
    actor: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    source_path = _safe_project_file(context.project.project_root, file_path)
    if text is None:
        text = _read_utf8_artifact(source_path)
    task_root = Path(context.project.project_root) / str(record['task_root'])
    if artifact_kind in _PLAN_ROOT_ARTIFACTS:
        dest = Path(context.project.project_root) / str(record['plan_root']) / _ARTIFACT_FILES[artifact_kind]
    else:
        dest = task_root / _ARTIFACT_FILES[artifact_kind]
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(dest, text)
    encoded = text.encode('utf-8')
    now = _utc_now()
    artifact = {
        'kind': artifact_kind,
        'artifact_kind': artifact_kind,
        'path': str(dest.relative_to(context.project.project_root)),
        'artifact_path': str(dest.relative_to(context.project.project_root)),
        'source_path': str(source_path.relative_to(context.project.project_root)),
        'scope': 'plan' if artifact_kind in _PLAN_ROOT_ARTIFACTS else 'task',
        'sha256': hashlib.sha256(encoded).hexdigest(),
        'bytes': len(encoded),
        'imported_at': now,
        'actor': actor or _artifact_actor_metadata(context, None),
    }
    if extra:
        artifact.update(extra)
    artifacts = dict(record.get('artifacts') or {})
    artifacts[artifact_kind] = artifact
    record = dict(record)
    record['artifacts'] = artifacts
    return record, artifact


def _artifact_actor_metadata(context, command, *, default_source: str = 'cli') -> dict[str, object]:
    source = _first_text(
        getattr(command, 'actor_source', None),
        os.environ.get('CCB_ARTIFACT_SOURCE'),
        default_source,
    )
    actor = _first_text(
        getattr(command, 'actor_agent', None),
        getattr(command, 'actor', None),
        os.environ.get('CCB_CALLER_ACTOR'),
        os.environ.get('CCB_ACTOR'),
        os.environ.get('CCB_AGENT_NAME'),
        _actor_from_runtime_dir(context),
    )
    role = _first_text(
        getattr(command, 'actor_role', None),
        os.environ.get('CCB_CALLER_ROLE'),
        os.environ.get('CCB_ACTOR_ROLE'),
    )
    job_id = _first_text(
        getattr(command, 'job_id', None),
        getattr(command, 'request_id', None),
        os.environ.get('CCB_JOB_ID'),
        os.environ.get('CCB_REQ_ID'),
        os.environ.get('CCB_REQUEST_ID'),
    )
    metadata: dict[str, object] = {
        'source': source,
        'actor': actor or 'user',
    }
    if role:
        metadata['role'] = role
    if job_id:
        metadata['job_id'] = job_id
    return metadata


def _actor_from_runtime_dir(context) -> str:
    raw = _first_text(os.environ.get('CCB_CALLER_RUNTIME_DIR'), os.environ.get('CODEX_RUNTIME_DIR'))
    if not raw:
        return ''
    try:
        runtime_dir = Path(raw).expanduser().resolve()
        agents_dir = Path(context.paths.agents_dir).expanduser().resolve()
        relative = runtime_dir.relative_to(agents_dir)
    except Exception:
        return ''
    return str(relative.parts[0]) if relative.parts else ''


def _first_text(*values: object) -> str:
    for value in values:
        text = str(value or '').strip()
        if text:
            return text
    return ''


def _existing_round_import(
    record: dict[str, object],
    *,
    loop_id: str,
    result: str,
    sha256: str,
) -> dict[str, object] | None:
    artifact_kind, _target_status, legacy_artifact_kind = _ROUND_RESULT_MAP[result]
    artifacts = record.get('artifacts') if isinstance(record.get('artifacts'), dict) else {}
    artifact = artifacts.get(artifact_kind) if isinstance(artifacts, dict) else None
    if not isinstance(artifact, dict):
        artifact = artifacts.get(legacy_artifact_kind) if isinstance(artifacts, dict) else None
    if not isinstance(artifact, dict):
        return None
    if (
        str(artifact.get('loop_id') or '') == loop_id
        and str(artifact.get('round_result') or '') == result
        and str(artifact.get('sha256') or '') == sha256
    ):
        return artifact
    raise ValueError(f'plan task round import conflicts with existing {artifact_kind} artifact')


def _add_round_legacy_alias(
    record: dict[str, object],
    *,
    artifact: dict[str, object],
    legacy_artifact_kind: str,
) -> dict[str, object]:
    artifacts = dict(record.get('artifacts') or {})
    legacy = dict(artifact)
    legacy['kind'] = legacy_artifact_kind
    legacy['artifact_kind'] = legacy_artifact_kind
    legacy['legacy_of'] = 'round_summary'
    artifacts[legacy_artifact_kind] = legacy
    record = dict(record)
    record['artifacts'] = artifacts
    return record


def _legacy_artifact_for_round(record: dict[str, object], legacy_artifact_kind: str) -> dict[str, object] | None:
    artifacts = record.get('artifacts') if isinstance(record.get('artifacts'), dict) else {}
    artifact = artifacts.get(legacy_artifact_kind) if isinstance(artifacts, dict) else None
    return dict(artifact) if isinstance(artifact, dict) else None


def _write_task_readme(context, record: dict[str, object]) -> None:
    task_root = Path(context.project.project_root) / str(record['task_root'])
    lines = [
        f'Task: {record.get("title", "")}',
        f'Task ID: {record.get("task_id", "")}',
        f'Plan Root: {record.get("plan_slug", "")}',
        f'Status: {record.get("status", "")}',
        f'Current Loop: {record.get("current_loop") or "none"}',
        f'Owner: {record.get("owner", "")}',
        f'Next Owner: {record.get("next_owner", "")}',
        f'Activation Reason: {record.get("activation_reason", "")}',
        f'Created: {record.get("created_at", "")}',
        f'Updated: {record.get("updated_at", "")}',
        '',
    ]
    atomic_write_text(task_root / 'README.md', '\n'.join(lines))


def _breadcrumb_text(payload: dict[str, object]) -> str:
    record = payload.get('task') if isinstance(payload.get('task'), dict) else {}
    return '\n'.join(
        [
            f'Task: {record.get("task_id", "")}',
            f'Plan: {record.get("plan_slug", "")}',
            f'Status: {record.get("status", "")}',
            f'Owner: {record.get("owner", "")}',
            f'Next Owner: {record.get("next_owner", "")}',
            f'Current Loop: {record.get("current_loop") or "none"}',
            f'Activation Reason: {record.get("activation_reason", "")}',
            f'Artifacts: {", ".join(sorted((record.get("artifacts") or {}).keys())) if isinstance(record.get("artifacts"), dict) else ""}',
        ]
    )


def _plan_root(context, plan_slug: str) -> Path:
    return Path(context.project.project_root) / 'docs' / 'plantree' / 'plans' / plan_slug


def _safe_project_file(project_root: Path, value: object) -> Path:
    raw = str(value or '').strip()
    if not raw:
        raise ValueError('plan task-artifact requires --file')
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = project_root / path
    try:
        resolved = path.resolve(strict=True)
        root = project_root.resolve()
    except FileNotFoundError as exc:
        raise ValueError(f'plan artifact file not found: {path}') from exc
    if resolved != root and root not in resolved.parents:
        raise ValueError(f'plan artifact file must be inside project root: {resolved}')
    if not resolved.is_file():
        raise ValueError(f'plan artifact path is not a file: {resolved}')
    return resolved


def _read_utf8_artifact(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError(f'plan artifact must be UTF-8 text: {path}') from exc


def _normalize_segment(value: object, *, label: str) -> str:
    text = str(value or '').strip()
    if not _SEGMENT_RE.fullmatch(text):
        raise ValueError(f'{label} must match {_SEGMENT_RE.pattern}: {text!r}')
    return text


def _normalize_task_id(value: object) -> str:
    return _normalize_segment(value, label='task_id')


def _new_task_id(*, title: str, tasks_root: Path) -> str:
    slug = _SLUG_RE.sub('-', title.strip().lower()).strip('-') or 'task'
    slug = slug[:40].strip('-') or 'task'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    base = f'{slug}-{stamp}'
    candidate = base[:80]
    suffix = 1
    while (tasks_root / candidate).exists():
        suffix += 1
        suffix_text = f'-{suffix}'
        candidate = f'{base[:80 - len(suffix_text)]}{suffix_text}'
    return candidate


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ValueError(f'plan task index is invalid JSON: {path}') from exc
    if not isinstance(payload, dict):
        raise ValueError(f'plan task index is invalid: {path}')
    return payload


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


__all__ = ['find_first_actionable_task', 'find_first_ready_task', 'plan_task', 'task_execution_text']
