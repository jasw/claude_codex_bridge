from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from agents.config_loader import load_project_config
from agents.models import AgentValidationError, LoopCapacityConfig, normalize_agent_name
from storage.atomic import atomic_write_json

from .agent_lifecycle import add_lifecycle_agents, agent_lifecycle
from .layout import layout_command

TOPOLOGY_SCHEMA = 'ccb.loop.agent_topology.v1'
PROPOSAL_SCHEMA = 'ccb.loop.agent_topology.proposal.v1'
OBSERVED_SCHEMA = 'ccb.loop.agent_topology.observed.v1'
DESIRED_STATES = frozenset({'present', 'hidden', 'parked', 'absent'})
ACTIVE_DESIRED_STATES = frozenset({'present', 'hidden', 'parked'})
RELEASE_POLICIES = frozenset({'auto', 'hide', 'park', 'unload'})
SUPPORTED_EDGE_TYPES = frozenset({'ask', 'ask_after', 'artifact_read', 'handoff', 'status_report', 'group_ready', 'release_gate', 'release_when'})
LEGACY_TOPOLOGY_PROFILES = frozenset({
    'worker',
    'checker',
    'round_checker',
    'planner',
    'orchestrator',
    'task_detailer',
    'round_reviewer',
    'ccb_worker',
    'ccb_checker',
    'ccb_round_checker',
})
USER_INTERACTION_WINDOW = 'ccb-user'
PLANNING_WINDOW = 'ccb-plan'
EXECUTION_WINDOW_PREFIX = 'ccb-exec'
TOPOLOGY_PROFILE_WINDOWS = {
    'ccb_frontdesk': USER_INTERACTION_WINDOW,
    'ccb_task_detailer': USER_INTERACTION_WINDOW,
    'ccb_planner': PLANNING_WINDOW,
    'ccb_orchestrator': PLANNING_WINDOW,
    'ccb_round_reviewer': PLANNING_WINDOW,
}
EXECUTION_PROFILES = frozenset({'coder', 'code_reviewer'})
MAX_TOPOLOGY_PANES_PER_WINDOW = 6


def loop_topology(context, command) -> dict[str, object]:
    action = str(getattr(command, 'action', '') or '').strip().lower()
    if action == 'propose':
        return _propose(context, command)
    if action == 'validate':
        return _validate_command(context, command)
    if action == 'commit':
        return _commit(context, command)
    if action == 'reconcile':
        return _reconcile(context, command)
    if action == 'status':
        return _status(context, command)
    if action == 'release':
        return _release(context, command)
    raise ValueError(f'unsupported loop topology action: {action}')


def _propose(context, command) -> dict[str, object]:
    loop_id = _normalize_loop_id(getattr(command, 'loop_id', None))
    source_path = Path(str(getattr(command, 'from_path', '') or '')).expanduser()
    if not source_path.is_absolute():
        source_path = context.project.project_root / source_path
    proposal = _load_json_object(source_path)
    proposal_id = _proposal_id(getattr(command, 'proposal_id', None), proposal)
    proposal = _normalize_proposal(proposal, loop_id=loop_id, proposal_id=proposal_id, source_path=source_path)
    validation = _validate_topology(context, proposal, loop_id=loop_id)
    path = _proposal_path(context, loop_id, proposal_id)
    atomic_write_json(path, proposal)
    _append_event(context, loop_id, {'event': 'proposal', 'proposal_id': proposal_id, 'agent_count': validation['agent_count']})
    return {
        'loop_topology_status': 'proposed',
        'loop_id': loop_id,
        'proposal_id': proposal_id,
        'proposal_path': str(path),
        'source_path': str(source_path),
        'validation': validation,
    }


def _validate_command(context, command) -> dict[str, object]:
    loop_id = _normalize_loop_id(getattr(command, 'loop_id', None))
    proposal_id = _normalize_proposal_id(getattr(command, 'proposal_id', None))
    proposal = _load_json_object(_proposal_path(context, loop_id, proposal_id))
    validation = _validate_topology(context, proposal, loop_id=loop_id)
    return {
        'loop_topology_status': 'valid',
        'loop_id': loop_id,
        'proposal_id': proposal_id,
        'proposal_path': str(_proposal_path(context, loop_id, proposal_id)),
        'validation': validation,
    }


def _commit(context, command) -> dict[str, object]:
    loop_id = _normalize_loop_id(getattr(command, 'loop_id', None))
    proposal_id = _normalize_proposal_id(getattr(command, 'proposal_id', None))
    proposal = _load_json_object(_proposal_path(context, loop_id, proposal_id))
    validation = _validate_topology(context, proposal, loop_id=loop_id)
    previous = _load_json_optional(_desired_path(context, loop_id))
    previous_revision = int((previous or {}).get('revision') or 0)
    base_revision = proposal.get('base_revision')
    if base_revision is not None and int(base_revision) != previous_revision:
        raise ValueError(
            f'topology proposal base_revision={base_revision} does not match desired revision={previous_revision}'
        )
    revision = previous_revision + 1
    desired = {
        'schema': TOPOLOGY_SCHEMA,
        'record_type': 'ccb_loop_agent_topology_desired',
        'topology_status': 'committed',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'loop_id': loop_id,
        'revision': revision,
        'base_revision': previous_revision,
        'proposal_id': proposal_id,
        'committed_at': _utc_now(),
        'nodes': proposal.get('nodes') or [],
        'edges': proposal.get('edges') or [],
        'artifacts': proposal.get('artifacts') or {},
        'gates': proposal.get('gates') or [],
        'release_policy': proposal.get('release_policy') or {'policy': 'auto', 'idle_only': True},
        'validation': validation,
    }
    atomic_write_json(_desired_path(context, loop_id), desired)
    _append_event(
        context,
        loop_id,
        {'event': 'commit', 'proposal_id': proposal_id, 'revision': revision, 'agent_count': validation['agent_count']},
    )
    payload: dict[str, object] = {
        'loop_topology_status': 'committed',
        'loop_id': loop_id,
        'proposal_id': proposal_id,
        'revision': revision,
        'desired_path': str(_desired_path(context, loop_id)),
        'validation': validation,
    }
    if bool(getattr(command, 'apply', False)):
        payload['reconcile'] = _reconcile_desired(context, loop_id, desired=desired)
    return payload


def _reconcile(context, command) -> dict[str, object]:
    loop_id = _normalize_loop_id(getattr(command, 'loop_id', None))
    desired = _load_json_optional(_desired_path(context, loop_id))
    if desired is None:
        return {
            'loop_topology_status': 'missing',
            'loop_id': loop_id,
            'desired_path': str(_desired_path(context, loop_id)),
            'observed_path': str(_observed_path(context, loop_id)),
            'actions': [],
            'drift': {'missing_desired': True},
        }
    return _reconcile_desired(context, loop_id, desired=desired)


def _status(context, command) -> dict[str, object]:
    loop_id = _normalize_loop_id(getattr(command, 'loop_id', None))
    desired = _load_json_optional(_desired_path(context, loop_id))
    observed = _load_json_optional(_observed_path(context, loop_id))
    proposals = [
        path.stem
        for path in sorted(_proposal_dir(context, loop_id).glob('*.json'))
        if path.is_file()
    ]
    status = 'missing' if desired is None else 'ready'
    if desired is not None and observed is None:
        status = 'desired_pending'
    elif desired is not None and observed is not None:
        desired_revision = int(desired.get('revision') or 0)
        observed_revision = int(observed.get('desired_revision') or 0)
        observed_status = str(observed.get('last_reconcile_status') or '')
        if observed_revision == desired_revision and observed_status == 'failed':
            status = 'failed'
        elif observed_revision < desired_revision:
            status = 'drift'
        elif observed_status == 'retained_busy':
            status = 'retained_busy'
    return {
        'loop_topology_status': status,
        'loop_id': loop_id,
        'desired_path': str(_desired_path(context, loop_id)),
        'observed_path': str(_observed_path(context, loop_id)),
        'proposal_dir': str(_proposal_dir(context, loop_id)),
        'proposal_ids': proposals,
        'desired': _status_summary(desired),
        'observed': _status_summary(observed),
    }


def _release(context, command) -> dict[str, object]:
    loop_id = _normalize_loop_id(getattr(command, 'loop_id', None))
    desired = _load_json_optional(_desired_path(context, loop_id))
    if desired is None:
        desired = {
            'schema': TOPOLOGY_SCHEMA,
            'record_type': 'ccb_loop_agent_topology_desired',
            'topology_status': 'released',
            'project_id': context.project.project_id,
            'project_root': str(context.project.project_root),
            'loop_id': loop_id,
            'revision': 1,
            'base_revision': 0,
            'proposal_id': None,
            'committed_at': _utc_now(),
            'nodes': [],
            'edges': [],
            'artifacts': {},
            'gates': [],
            'release_policy': _release_policy_record(command),
            'validation': {'agent_count': 0, 'present_agent_count': 0, 'profile_counts': {}, 'edge_count': 0},
        }
    else:
        desired = json.loads(json.dumps(desired))
        desired['revision'] = int(desired.get('revision') or 0) + 1
        desired['base_revision'] = int(desired.get('revision') or 1) - 1
        desired['topology_status'] = 'released'
        desired['committed_at'] = _utc_now()
        desired['release_policy'] = _release_policy_record(command)
        _mark_all_agents_absent(desired)
    atomic_write_json(_desired_path(context, loop_id), desired)
    payload = _reconcile_desired(context, loop_id, desired=desired)
    payload['loop_topology_status'] = 'released' if payload.get('retained_count', 0) == 0 else 'retained_busy'
    _append_event(
        context,
        loop_id,
        {
            'event': 'release',
            'revision': desired.get('revision'),
            'retained_count': payload.get('retained_count', 0),
            'released_count': payload.get('released_count', 0),
        },
    )
    return payload


def _reconcile_desired(context, loop_id: str, *, desired: Mapping[str, object]) -> dict[str, object]:
    loaded = load_project_config(context.project.project_root, include_loop_overlays=True)
    desired_agents = _desired_agents(desired, loop_id=loop_id)
    dynamic = _dynamic_records(context)
    dynamic_by_name = {str(record.get('agent') or ''): record for record in dynamic}
    wanted = {
        str(agent['id']): agent
        for agent in desired_agents
        if str(agent.get('desired_state') or 'present') in ACTIVE_DESIRED_STATES
    }
    absent = {str(agent['id']): agent for agent in desired_agents if str(agent.get('desired_state') or '') == 'absent'}
    actions: list[dict[str, object]] = []
    retained: list[dict[str, object]] = []
    default_release_policy = _topology_release_policy(desired)

    try:
        pending_adds: list[tuple[Mapping[str, object], str]] = []
        pending_alignments: list[tuple[str, Mapping[str, object], Mapping[str, object], str]] = []
        for name, agent in wanted.items():
            record = dynamic_by_name.get(name)
            target_state = str(agent.get('desired_state') or 'present')
            if record is None or str(record.get('lifecycle_state') or '') == 'unloaded':
                pending_adds.append((agent, target_state))
            else:
                pending_alignments.append((name, agent, record, target_state))

        if pending_adds:
            actions.extend(_add_agents(context, tuple(pending_adds)))
            for agent, _target_state in pending_adds:
                name = str(agent.get('id') or '')
                dynamic_by_name[name] = _dynamic_record(context, name) or {}

        release_groups: dict[str, list[str]] = {}
        for name, record in sorted(dynamic_by_name.items()):
            if str(record.get('loop_id') or '') != loop_id:
                continue
            if name in wanted:
                continue
            if str(record.get('lifecycle_state') or '') == 'unloaded':
                continue
            target = absent.get(name)
            policy = _agent_release_policy(target, default_policy=default_release_policy)
            release_groups.setdefault(policy, []).append(name)
        for policy, names in sorted(release_groups.items()):
            batch_actions = _release_agents(context, tuple(names), policy=policy)
            actions.extend(batch_actions)
            for action in batch_actions:
                if str(action.get('status') or '') == 'retained_busy':
                    retained.append({'agent': action.get('agent'), 'reason': action.get('reason')})

        for _name, agent, record, target_state in pending_alignments:
            actions.extend(_align_agent(context, agent, record=record, target_state=target_state))

        for window_name in _target_windows(desired_agents):
            actions.append(_arrange_window(context, window_name))
    except Exception as exc:
        _write_observed(
            context,
            loop_id,
            desired=desired,
            desired_agents=desired_agents,
            actions=actions,
            retained=retained,
            loaded=loaded,
            status='failed',
            error=str(exc),
        )
        _append_event(
            context,
            loop_id,
            {
                'event': 'reconcile-failed',
                'revision': int(desired.get('revision') or 0),
                'agent_count': len(desired_agents),
                'retained_count': len(retained),
                'error': str(exc),
            },
        )
        raise

    return _write_observed(
        context,
        loop_id,
        desired=desired,
        desired_agents=desired_agents,
        actions=actions,
        retained=retained,
        loaded=loaded,
        status='retained_busy' if retained else 'reconciled',
    )


def _write_observed(
    context,
    loop_id: str,
    *,
    desired: Mapping[str, object],
    desired_agents: list[dict[str, object]],
    actions: list[dict[str, object]],
    retained: list[dict[str, object]],
    loaded,
    status: str,
    error: str | None = None,
) -> dict[str, object]:
    observed_agents = []
    for agent in desired_agents:
        name = str(agent.get('id') or '')
        record = _dynamic_record(context, name)
        observed_agents.append(_observed_agent(agent, record=record))
    released_agents = _released_agent_ids(observed_agents, actions)
    observed = {
        'schema': OBSERVED_SCHEMA,
        'record_type': 'ccb_loop_agent_topology_observed',
        'last_reconcile_status': status,
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'loop_id': loop_id,
        'desired_revision': int(desired.get('revision') or 0),
        'reconciled_at': _utc_now(),
        'agents': observed_agents,
        'edges': list(desired.get('edges') or []),
        'actions': actions,
        'retained': retained,
        'retained_count': len(retained),
        'released_count': len(released_agents),
        'released_agents': released_agents,
        'drift': _drift_for(desired_agents, observed_agents),
        'config_source_kind': loaded.source_kind,
        'config_source': str(loaded.source_path) if loaded.source_path is not None else None,
    }
    if error is not None:
        observed['error'] = error
    atomic_write_json(_observed_path(context, loop_id), observed)
    if status != 'failed':
        _append_event(
            context,
            loop_id,
            {
                'event': 'reconcile',
                'revision': observed['desired_revision'],
                'agent_count': len(observed_agents),
                'retained_count': len(retained),
            },
        )
    return {
        'loop_topology_status': observed['last_reconcile_status'],
        'loop_id': loop_id,
        'desired_revision': observed['desired_revision'],
        'desired_path': str(_desired_path(context, loop_id)),
        'observed_path': str(_observed_path(context, loop_id)),
        'agent_count': len(observed_agents),
        'retained_count': len(retained),
        'released_count': observed['released_count'],
        'released_agents': observed['released_agents'],
        'actions': actions,
        'observed': observed,
    }


def _add_agent(context, agent: Mapping[str, object], *, target_state: str) -> dict[str, object]:
    return _add_agents(context, ((agent, target_state),))[0]


def _add_agents(context, agents: tuple[tuple[Mapping[str, object], str], ...]) -> list[dict[str, object]]:
    if not agents:
        return []
    commands = tuple(_add_agent_command(agent, target_state=target_state) for agent, target_state in agents)
    payloads = add_lifecycle_agents(
        context,
        commands,
        action='topology-agent-add-batch' if len(commands) > 1 else 'topology-agent-add',
    )
    results: list[dict[str, object]] = []
    target_states = {str(agent.get('id') or ''): target_state for agent, target_state in agents}
    for payload in payloads:
        name = str(payload.get('agent') or '')
        results.append(
            {
                'action': 'add',
                'agent': name,
                'status': payload.get('agent_lifecycle_status'),
                'target_state': target_states.get(name, 'present'),
                'apply_status': _apply_status(payload),
            }
        )
    return results


def _add_agent_command(agent: Mapping[str, object], *, target_state: str) -> SimpleNamespace:
    name = str(agent.get('id') or '')
    return SimpleNamespace(
        action='add',
        agent_name=name,
        provider=str(agent.get('provider') or ''),
        profile=str(agent.get('profile') or ''),
        role=None,
        model=None,
        thinking=None,
        workspace_mode=None,
        window_name=_optional_text(agent.get('window_name')),
        window_class=_optional_text(agent.get('window_class')),
        loop_id=_optional_text(agent.get('loop_id')),
        node_id=_optional_text(agent.get('node_id')),
        lifetime=str(agent.get('lifetime') or 'current_loop'),
        visibility='parked' if target_state == 'parked' else ('hidden' if target_state == 'hidden' else 'visible'),
        json_output=True,
    )


def _align_agent(
    context,
    agent: Mapping[str, object],
    *,
    record: Mapping[str, object],
    target_state: str,
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    name = str(agent.get('id') or '')
    current_window = _optional_text(record.get('resolved_window_name')) or _optional_text(record.get('window_name'))
    target_window = _optional_text(agent.get('window_name'))
    if target_window and current_window != target_window:
        command = SimpleNamespace(
            action='move',
            agent_name=name,
            agent_names=(),
            window_name=target_window,
            window_class=_optional_text(agent.get('window_class')),
            loop_id=_optional_text(agent.get('loop_id')),
            node_id=_optional_text(agent.get('node_id')),
            reason='topology reconcile',
            json_output=True,
        )
        payload = agent_lifecycle(context, command)
        actions.append(
            {
                'action': 'move',
                'agent': name,
                'status': payload.get('agent_lifecycle_status'),
                'target_window': target_window,
                'apply_status': _apply_status(payload),
            }
        )
    current_state = str(record.get('lifecycle_state') or '')
    desired_action = _transition_action(current_state, target_state)
    if desired_action is not None:
        command = SimpleNamespace(
            action=desired_action,
            agent_name=name,
            agent_names=(),
            visibility='visible' if target_state == 'present' else 'hidden',
            reason='topology reconcile',
            json_output=True,
        )
        payload = agent_lifecycle(context, command)
        actions.append(
            {
                'action': desired_action,
                'agent': name,
                'status': payload.get('agent_lifecycle_status'),
                'target_state': target_state,
                'apply_status': _apply_status(payload),
            }
        )
    if not actions:
        actions.append({'action': 'noop', 'agent': name, 'status': 'ok', 'target_state': target_state})
    return actions


def _release_agent(context, name: str, *, policy: str) -> dict[str, object]:
    if policy not in RELEASE_POLICIES:
        raise ValueError(f'unsupported topology release policy: {policy}')
    command = SimpleNamespace(
        action='release',
        agent_name=name,
        agent_names=(),
        policy=policy,
        idle_only=True,
        summary_policy=None,
        reason='topology release',
        json_output=True,
    )
    payload = agent_lifecycle(context, command)
    status = str(payload.get('agent_lifecycle_status') or '')
    return {
        'action': 'release',
        'agent': name,
        'status': status,
        'policy': command.policy,
        'reason': payload.get('retain_reason') or payload.get('reason'),
        'apply_status': _apply_status(payload),
    }


def _release_agents(context, names: tuple[str, ...], *, policy: str) -> list[dict[str, object]]:
    if policy not in RELEASE_POLICIES:
        raise ValueError(f'unsupported topology release policy: {policy}')
    command = SimpleNamespace(
        action='release',
        agent_name=None,
        agent_names=names,
        policy=policy,
        idle_only=True,
        summary_policy=None,
        reason='topology release',
        json_output=True,
    )
    payload = agent_lifecycle(context, command)
    retained_agents = set(str(item) for item in tuple(payload.get('retained_agents') or ()))
    if retained_agents:
        actions: list[dict[str, object]] = [
            {
                'action': 'release',
                'agent': name,
                'status': 'retained_busy',
                'policy': policy,
                'reason': _retained_reason(payload, name),
                'apply_status': _apply_status(payload),
            }
            for name in names
            if name in retained_agents
        ]
        for name in names:
            if name not in retained_agents:
                actions.append(_release_agent(context, name, policy=policy))
        return actions
    records_by_name = {
        str(record.get('agent') or ''): record
        for record in tuple(payload.get('agents') or ())
        if isinstance(record, Mapping)
    }
    actions: list[dict[str, object]] = []
    for name in names:
        record = records_by_name.get(name, {})
        retained = name in retained_agents or bool(record.get('retained_busy'))
        status = 'retained_busy' if retained else str(payload.get('agent_lifecycle_status') or '')
        if not status:
            status = str(record.get('agent_lifecycle_status') or '') or 'ok'
        actions.append(
            {
                'action': 'release',
                'agent': name,
                'status': status,
                'policy': policy,
                'reason': record.get('retain_reason') or payload.get('reason'),
                'apply_status': _apply_status(payload),
            }
        )
    return actions


def _retained_reason(payload: Mapping[str, object], name: str) -> object:
    for record in tuple(payload.get('agents') or ()):
        if not isinstance(record, Mapping):
            continue
        if str(record.get('agent') or '') == name:
            return record.get('retain_reason') or payload.get('reason')
    return payload.get('reason')


def _arrange_window(context, window_name: str) -> dict[str, object]:
    command = SimpleNamespace(action='arrange', window_name=window_name, timeout_s=5.0, json_output=True)
    payload = layout_command(context, command)
    return {
        'action': 'reflow',
        'window_name': window_name,
        'status': payload.get('arrange_status') or payload.get('layout_status'),
        'reason': payload.get('reason'),
    }


def _transition_action(current_state: str, target_state: str) -> str | None:
    if target_state == 'present':
        return None if current_state == 'visible' else 'resume'
    if target_state == 'hidden':
        return None if current_state == 'hidden' else 'hide'
    if target_state == 'parked':
        return None if current_state == 'parked' else 'park'
    return None


def _validate_topology(context, payload: Mapping[str, object], *, loop_id: str) -> dict[str, object]:
    loaded = load_project_config(context.project.project_root, include_loop_overlays=False)
    capacity = loaded.config.loop_capacity or LoopCapacityConfig()
    agents = _desired_agents(payload, validate=True, loop_id=loop_id)
    if agents and not capacity.enabled:
        raise ValueError('loop topology requires [loop.capacity].enabled = true')
    default_release_policy = _topology_release_policy(payload)
    for agent in agents:
        _agent_release_policy(agent, default_policy=default_release_policy)
    profile_counts = Counter(str(agent.get('profile') or '') for agent in agents if str(agent.get('desired_state') or 'present') in ACTIVE_DESIRED_STATES)
    if sum(profile_counts.values()) > capacity.max_nodes:
        raise ValueError(f'loop topology exceeds max_nodes={capacity.max_nodes}: requested {sum(profile_counts.values())}')
    known_profiles = capacity.role_profiles
    for profile, count in sorted(profile_counts.items()):
        spec = known_profiles.get(profile)
        if spec is None:
            known = ', '.join(sorted(known_profiles)) or '<none>'
            raise ValueError(f'unknown loop topology profile {profile!r}; configured profiles: {known}')
        if count > spec.max_instances:
            raise ValueError(f'loop topology profile {profile} exceeds max_instances={spec.max_instances}: requested {count}')
    _validate_edges(payload, agents, configured_agents=set(getattr(loaded.config, 'agents', {}) or {}))
    return {
        'topology_validation_status': 'valid',
        'loop_id': loop_id,
        'agent_count': len(agents),
        'present_agent_count': sum(profile_counts.values()),
        'profile_counts': dict(sorted(profile_counts.items())),
        'edge_count': len(tuple(payload.get('edges') or ())),
        'config_source_kind': loaded.source_kind,
        'config_source': str(loaded.source_path) if loaded.source_path is not None else None,
    }


def _desired_agents(payload: Mapping[str, object], *, validate: bool = False, loop_id: str | None = None) -> list[dict[str, object]]:
    agents: list[dict[str, object]] = []
    seen: set[str] = set()
    seen_nodes: set[str] = set()
    for node_index, node in enumerate(_node_dicts(payload), start=1):
        node_id = _node_id(node, fallback=f'node{node_index}')
        if validate and node_id in seen_nodes:
            raise ValueError(f'duplicate topology node id: {node_id}')
        seen_nodes.add(node_id)
        for agent_index, raw_agent in enumerate(_agent_dicts(node), start=1):
            agent = dict(raw_agent)
            profile = _normalize_named_value(agent.get('profile'), field_name='profile')
            name = agent.get('id') or agent.get('name')
            if name is None:
                if loop_id is None:
                    raise ValueError('topology agent requires id when loop_id is not available')
                name = f'loop-{loop_id}-{profile}-{agent_index}'
            name = _normalize_named_value(name, field_name='agent id')
            if name in seen:
                raise ValueError(f'duplicate topology agent id: {name}')
            seen.add(name)
            state = str(agent.get('desired_state') or agent.get('state') or 'present').strip().lower()
            if state not in DESIRED_STATES:
                raise ValueError(f'topology agent {name} has unsupported desired_state: {state}')
            agent['id'] = name
            agent['profile'] = profile
            agent['desired_state'] = state
            agent['loop_id'] = str(agent.get('loop_id') or loop_id or '')
            agent['node_id'] = node_id
            if validate and _topology_profile_key(agent) in LEGACY_TOPOLOGY_PROFILES:
                raise ValueError(
                    f'legacy workflow profile alias {profile!r} is not supported in topology; '
                    'use ccb_frontdesk, ccb_task_detailer, ccb_planner, ccb_orchestrator, '
                    'ccb_round_reviewer, coder, or code_reviewer'
                )
            explicit_window = _optional_text(agent.get('window_name'))
            if explicit_window is not None:
                agent['window_name'] = explicit_window
            agents.append(agent)
    _assign_default_windows(agents)
    return agents


def _assign_default_windows(agents: list[dict[str, object]]) -> None:
    execution_index = 0
    for agent in agents:
        if _optional_text(agent.get('window_name')) is not None:
            continue
        if _optional_text(agent.get('window_class')) is not None:
            continue
        profile = _topology_profile_key(agent)
        window_name = TOPOLOGY_PROFILE_WINDOWS.get(profile)
        if window_name is not None:
            agent['window_name'] = window_name
            continue
        if profile not in EXECUTION_PROFILES:
            raise ValueError(
                f'topology agent {agent.get("id")} profile {profile!r} requires explicit window_name/window_class '
                'or one of: ccb_frontdesk, ccb_task_detailer, ccb_planner, ccb_orchestrator, '
                'ccb_round_reviewer, coder, code_reviewer'
            )
        agent['window_name'] = _execution_window_name(execution_index)
        if str(agent.get('desired_state') or 'present') in ACTIVE_DESIRED_STATES:
            execution_index += 1


def _topology_profile_key(agent: Mapping[str, object]) -> str:
    return str(agent.get('profile') or '').strip().lower().replace('-', '_')


def _execution_window_name(index: int) -> str:
    page = (max(0, int(index)) // MAX_TOPOLOGY_PANES_PER_WINDOW) + 1
    return EXECUTION_WINDOW_PREFIX if page == 1 else f'{EXECUTION_WINDOW_PREFIX}-{page}'


def _validate_edges(
    payload: Mapping[str, object],
    agents: Iterable[Mapping[str, object]],
    *,
    configured_agents: set[str],
) -> None:
    agent_ids = {str(agent.get('id') or '') for agent in agents}
    known_endpoints = agent_ids | configured_agents | {'user'}
    edge_ids: set[str] = set()
    dependencies: dict[str, set[str]] = {}
    for index, raw_edge in enumerate(tuple(payload.get('edges') or ()), start=1):
        if not isinstance(raw_edge, Mapping):
            raise ValueError(f'topology edge #{index} must be an object')
        edge_id = str(raw_edge.get('id') or f'edge{index}').strip()
        if not edge_id:
            raise ValueError(f'topology edge #{index} id cannot be empty')
        if edge_id in edge_ids:
            raise ValueError(f'duplicate topology edge id: {edge_id}')
        edge_ids.add(edge_id)
        edge_type = str(raw_edge.get('type') or '').strip().lower()
        if not edge_type:
            raise ValueError(f'topology edge {edge_id} requires type')
        if edge_type not in SUPPORTED_EDGE_TYPES:
            supported = ', '.join(sorted(SUPPORTED_EDGE_TYPES))
            raise ValueError(f'unsupported topology edge type {edge_type!r} for edge {edge_id}; supported: {supported}')
        for endpoint in ('from', 'to'):
            value = str(raw_edge.get(endpoint) or '').strip()
            if value and value not in known_endpoints:
                raise ValueError(f'topology edge {edge_id} {endpoint} references unknown agent: {value}')
        dependencies[edge_id] = {str(item).strip() for item in tuple(raw_edge.get('after') or ()) if str(item).strip()}
    for edge_id, after in dependencies.items():
        missing = sorted(item for item in after if item not in edge_ids)
        if missing:
            raise ValueError(f'topology edge {edge_id} depends on unknown edge: {missing[0]}')
        if edge_id in after:
            raise ValueError(f'topology edge {edge_id} cannot depend on itself')
    _assert_acyclic(dependencies)


def _assert_acyclic(dependencies: Mapping[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(edge_id: str) -> None:
        if edge_id in visited:
            return
        if edge_id in visiting:
            raise ValueError(f'topology edge dependency cycle detected at {edge_id}')
        visiting.add(edge_id)
        for parent in dependencies.get(edge_id, set()):
            visit(parent)
        visiting.remove(edge_id)
        visited.add(edge_id)

    for edge_id in dependencies:
        visit(edge_id)


def _normalize_proposal(
    payload: Mapping[str, object],
    *,
    loop_id: str,
    proposal_id: str,
    source_path: Path,
) -> dict[str, object]:
    proposal = dict(payload)
    proposal['schema'] = PROPOSAL_SCHEMA
    proposal['record_type'] = 'ccb_loop_agent_topology_proposal'
    proposal['loop_id'] = loop_id
    proposal['proposal_id'] = proposal_id
    proposal['proposed_at'] = str(proposal.get('proposed_at') or _utc_now())
    proposal['source_path'] = str(source_path)
    proposal.setdefault('nodes', [])
    proposal.setdefault('edges', [])
    proposal.setdefault('artifacts', {})
    proposal.setdefault('gates', [])
    return proposal


def _node_dicts(payload: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    nodes = payload.get('nodes') or []
    if not isinstance(nodes, list):
        raise ValueError('topology nodes must be a list')
    result = []
    for index, raw_node in enumerate(nodes, start=1):
        if not isinstance(raw_node, Mapping):
            raise ValueError(f'topology node #{index} must be an object')
        result.append(dict(raw_node))
    return tuple(result)


def _mark_all_agents_absent(payload: dict[str, object]) -> None:
    nodes = payload.get('nodes') or []
    if not isinstance(nodes, list):
        return
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        agents = raw_node.get('agents') or []
        if not isinstance(agents, list):
            continue
        for raw_agent in agents:
            if isinstance(raw_agent, dict):
                raw_agent['desired_state'] = 'absent'


def _agent_dicts(node: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    agents = node.get('agents') or []
    if not isinstance(agents, list):
        raise ValueError(f'topology node {_node_id(node, fallback="<unknown>")} agents must be a list')
    result = []
    for index, raw_agent in enumerate(agents, start=1):
        if not isinstance(raw_agent, Mapping):
            raise ValueError(f'topology node {_node_id(node, fallback="<unknown>")} agent #{index} must be an object')
        result.append(dict(raw_agent))
    return tuple(result)


def _node_id(node: Mapping[str, object], *, fallback: str) -> str:
    return _normalize_named_value(node.get('id') or fallback, field_name='node id')


def _target_windows(agents: Iterable[Mapping[str, object]]) -> tuple[str, ...]:
    names = []
    seen = set()
    for agent in agents:
        if str(agent.get('desired_state') or '') == 'absent':
            continue
        window = str(agent.get('window_name') or '').strip()
        if window and window not in seen:
            seen.add(window)
            names.append(window)
    return tuple(names)


def _dynamic_records(context) -> tuple[dict[str, object], ...]:
    root = Path(context.paths.runtime_state_root) / 'runtime' / 'agents'
    if not root.is_dir():
        return ()
    records = []
    for path in sorted(root.glob('*/lifecycle.json')):
        payload = _load_json_optional(path)
        if isinstance(payload, dict):
            records.append(payload)
    return tuple(records)


def _dynamic_record(context, name: str) -> dict[str, object] | None:
    return _load_json_optional(Path(context.paths.runtime_state_root) / 'runtime' / 'agents' / name / 'lifecycle.json')


def _observed_agent(agent: Mapping[str, object], *, record: Mapping[str, object] | None) -> dict[str, object]:
    desired_state = str(agent.get('desired_state') or 'present')
    lifecycle_state = str((record or {}).get('lifecycle_state') or '')
    if record is None:
        observed_state = 'released' if desired_state == 'absent' else 'missing'
    elif lifecycle_state == 'unloaded':
        observed_state = 'released'
    elif lifecycle_state == 'parked':
        observed_state = 'parked'
    elif lifecycle_state == 'hidden':
        observed_state = 'hidden'
    else:
        observed_state = 'present'
    return {
        'id': agent.get('id'),
        'profile': agent.get('profile'),
        'node_id': agent.get('node_id'),
        'window_name': agent.get('window_name'),
        'desired_state': desired_state,
        'observed_state': observed_state,
        'lifecycle_state': lifecycle_state or None,
        'ask_target': (record or {}).get('ask_target') or agent.get('id'),
    }


def _drift_for(desired_agents: Iterable[Mapping[str, object]], observed_agents: Iterable[Mapping[str, object]]) -> dict[str, object]:
    mismatched = []
    for observed in observed_agents:
        desired = str(observed.get('desired_state') or '')
        state = str(observed.get('observed_state') or '')
        ok = False
        if desired == 'absent':
            ok = state in {'released', 'parked', 'hidden'}
        elif desired == 'present':
            ok = state == 'present'
        elif desired in {'hidden', 'parked'}:
            ok = state == desired
        if not ok:
            mismatched.append({'agent': observed.get('id'), 'desired_state': desired, 'observed_state': state})
    return {'mismatched_agents': mismatched, 'agent_count': len(tuple(desired_agents))}


def _released_agent_ids(
    observed_agents: Iterable[Mapping[str, object]],
    actions: Iterable[Mapping[str, object]],
) -> list[str]:
    released = {
        str(agent.get('id') or '')
        for agent in observed_agents
        if str(agent.get('observed_state') or '') == 'released'
    }
    released.update(
        str(action.get('agent') or '')
        for action in actions
        if str(action.get('action') or '') == 'release'
        and str(action.get('status') or '') == 'removed'
    )
    return sorted(name for name in released if name)


def _status_summary(payload: Mapping[str, object] | None) -> dict[str, object] | None:
    if payload is None:
        return None
    return {
        'revision': payload.get('revision') or payload.get('desired_revision'),
        'status': payload.get('topology_status') or payload.get('last_reconcile_status'),
        'agent_count': len(tuple(payload.get('agents') or _desired_agents(payload, loop_id=str(payload.get('loop_id') or '')))),
        'updated_at': payload.get('committed_at') or payload.get('reconciled_at'),
    }


def _release_policy_record(command) -> dict[str, object]:
    policy = str(getattr(command, 'policy', None) or 'auto').strip().lower()
    if policy not in {'auto', 'idle-only'}:
        raise ValueError(f'unsupported topology release policy: {policy}')
    return {'policy': policy, 'idle_only': True}


def _topology_release_policy(payload: Mapping[str, object]) -> str:
    return _normalize_release_policy_value(payload.get('release_policy'), default='auto')


def _agent_release_policy(agent: Mapping[str, object] | None, *, default_policy: str) -> str:
    if agent is None:
        return default_policy
    return _normalize_release_policy_value(agent.get('release_policy'), default=default_policy)


def _normalize_release_policy_value(value: object, *, default: str) -> str:
    raw = value
    if isinstance(raw, Mapping):
        raw = raw.get('policy')
    policy = str(raw or default).strip().lower()
    if policy == 'idle-only':
        return 'auto'
    if policy not in RELEASE_POLICIES:
        raise ValueError(f'unsupported topology release policy: {policy}')
    return policy


def _apply_status(payload: Mapping[str, object]) -> str | None:
    apply = payload.get('apply')
    if isinstance(apply, Mapping):
        return str(apply.get('apply_status') or '') or None
    return None


def _proposal_id(value: object, payload: Mapping[str, object]) -> str:
    text = _optional_text(value)
    if text is not None:
        return _normalize_proposal_id(text)
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:12]
    return f'proposal-{digest}'


def _normalize_loop_id(value: object) -> str:
    return _normalize_named_value(value, field_name='loop_id')


def _normalize_proposal_id(value: object) -> str:
    return _normalize_named_value(value, field_name='proposal_id')


def _normalize_named_value(value: object, *, field_name: str) -> str:
    try:
        return normalize_agent_name(str(value or ''))
    except AgentValidationError as exc:
        raise ValueError(f'{field_name} is invalid: {exc}') from exc


def _normalize_optional_name(value: object, *, field_name: str) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    return _normalize_named_value(text, field_name=field_name)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _load_json_object(path: Path) -> dict[str, object]:
    payload = _load_json_optional(path)
    if payload is None:
        raise ValueError(f'{path}: file not found')
    if not isinstance(payload, dict):
        raise ValueError(f'{path}: expected JSON object')
    return payload


def _load_json_optional(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    if not isinstance(payload, dict):
        raise ValueError(f'{path}: expected JSON object')
    return dict(payload)


def _loop_dir(context, loop_id: str) -> Path:
    return Path(context.paths.runtime_state_root) / 'runtime' / 'loops' / loop_id


def _proposal_dir(context, loop_id: str) -> Path:
    return _loop_dir(context, loop_id) / 'topology_proposals'


def _proposal_path(context, loop_id: str, proposal_id: str) -> Path:
    return _proposal_dir(context, loop_id) / f'{proposal_id}.json'


def _desired_path(context, loop_id: str) -> Path:
    return _loop_dir(context, loop_id) / 'agent_topology.desired.json'


def _observed_path(context, loop_id: str) -> Path:
    return _loop_dir(context, loop_id) / 'agent_topology.observed.json'


def _events_path(context, loop_id: str) -> Path:
    return _loop_dir(context, loop_id) / 'agent_topology.events.jsonl'


def _append_event(context, loop_id: str, payload: Mapping[str, object]) -> None:
    event = {
        'schema_version': 1,
        'record_type': 'ccb_loop_agent_topology_event',
        'created_at': _utc_now(),
        'project_id': context.project.project_id,
        **dict(payload),
    }
    path = _events_path(context, loop_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
        handle.write('\n')


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


__all__ = ['loop_topology']
