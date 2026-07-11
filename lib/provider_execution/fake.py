from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import ast
import hashlib
import json
from pathlib import Path

from ccbd.api_models import JobRecord
from ccbd.system import parse_utc_timestamp
from completion.models import (
    CompletionConfidence,
    CompletionDecision,
    CompletionCursor,
    CompletionItem,
    CompletionItemKind,
    CompletionSourceKind,
    CompletionStatus,
)
from project.ids import compute_project_id

from .base import ProviderPollResult, ProviderSubmission
from .fake_runtime import (
    DEFAULT_LATENCY_SECONDS,
    FakeDirective,
    FakeScriptEvent,
    TERMINAL_KINDS,
    build_terminal_decision,
    default_script,
    first_text,
    materialize_payload,
    normalize_script_event,
    parse_directive,
)


class FakeProviderAdapter:
    def __init__(
        self,
        *,
        provider: str = 'fake',
        source_kind: CompletionSourceKind = CompletionSourceKind.STRUCTURED_RESULT_STREAM,
        script_mode: str = 'structured_result',
        latency_seconds: float = DEFAULT_LATENCY_SECONDS,
    ) -> None:
        self.provider = provider
        self._source_kind = source_kind
        self._script_mode = script_mode
        self._latency_seconds = latency_seconds

    def start(self, job: JobRecord, *, context, now: str) -> ProviderSubmission:
        directive = parse_directive(job.request.task_id, default_latency_seconds=self._latency_seconds)
        effective_body = _effective_request_body(job)
        g5_contract = _g5_contract_for_job(job, context=context, body=effective_body)
        directive = _g5_scenario_directive(
            directive,
            contract=g5_contract,
            body=effective_body,
        )
        events = tuple(normalize_script_event(raw) for raw in (directive.script or default_script(directive, mode=self._script_mode)))
        max_delay_ms = max((event.at_ms for event in events), default=0)
        ready_at = parse_utc_timestamp(now) + timedelta(milliseconds=max_delay_ms)
        reply, attachments = _reply_for_body(job, context=context, g5_contract=g5_contract)
        return ProviderSubmission(
            job_id=job.job_id,
            agent_name=job.agent_name,
            provider=self.provider,
            accepted_at=now,
            ready_at=ready_at.isoformat().replace('+00:00', 'Z'),
            source_kind=self._source_kind,
            reply=reply,
            status=directive.status,
            reason=directive.reason,
            confidence=directive.confidence,
            diagnostics={'provider': self.provider, 'task_id': job.request.task_id},
            runtime_state={
                'events': [
                    {
                        'at_ms': event.at_ms,
                        'kind': event.kind.value if event.kind is not None else None,
                        'payload': dict(event.payload),
                    }
                    for event in events
                ],
                'next_index': 0,
                'next_seq': 1,
                'reply_buffer': '',
                'attachments': attachments,
            },
        )

    def poll(self, submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
        state = dict(submission.runtime_state)
        events = list(state.get('events', []))
        next_index = int(state.get('next_index', 0))
        next_seq = int(state.get('next_seq', 1))
        reply_buffer = str(state.get('reply_buffer', ''))
        elapsed_ms = max(
            0,
            int((parse_utc_timestamp(now) - parse_utc_timestamp(submission.accepted_at)).total_seconds() * 1000),
        )

        emitted: list[CompletionItem] = []
        decision: CompletionDecision | None = None
        accepted_at = parse_utc_timestamp(submission.accepted_at)

        while next_index < len(events):
            raw_event = events[next_index]
            at_ms = int(raw_event['at_ms'])
            if at_ms > elapsed_ms:
                break

            kind_name = raw_event.get('kind')
            payload = dict(raw_event.get('payload') or {})
            next_index += 1
            if kind_name is None:
                continue

            kind = CompletionItemKind(str(kind_name))
            timestamp = (accepted_at + timedelta(milliseconds=at_ms)).isoformat().replace('+00:00', 'Z')
            payload, reply_buffer = materialize_payload(
                kind,
                payload,
                reply_buffer=reply_buffer,
                default_reply=submission.reply,
                turn_ref=submission.job_id,
                terminal_reason=submission.reason,
            )
            cursor = CompletionCursor(
                source_kind=submission.source_kind,
                event_seq=next_seq,
                updated_at=timestamp,
            )
            item = CompletionItem(
                kind=kind,
                timestamp=timestamp,
                cursor=cursor,
                provider=submission.provider,
                agent_name=submission.agent_name,
                req_id=submission.job_id,
                payload=payload,
            )
            emitted.append(item)
            next_seq += 1

            if kind in TERMINAL_KINDS:
                decision = build_terminal_decision(
                    submission,
                    payload=payload,
                    cursor=cursor,
                    finished_at=timestamp,
                    reply=reply_buffer or submission.reply,
                )
                break

        if not emitted and decision is None:
            return None

        updated = replace(
            submission,
            runtime_state={
                **state,
                'next_index': next_index,
                'next_seq': next_seq,
                'reply_buffer': reply_buffer,
            },
        )
        return ProviderPollResult(submission=updated, items=tuple(emitted), decision=decision)

    def export_runtime_state(self, submission: ProviderSubmission) -> dict[str, object]:
        return dict(submission.runtime_state)

    def resume(self, job: JobRecord, submission: ProviderSubmission, *, context, persisted_state, now: str) -> ProviderSubmission | None:
        del context, persisted_state, now
        if submission.job_id != job.job_id or submission.provider != job.provider:
            return None
        return submission


_build_terminal_decision = build_terminal_decision
_default_script = default_script
_first_text = first_text
_materialize_payload = materialize_payload
_normalize_script_event = normalize_script_event
_parse_directive = parse_directive


def _reply_for_body(
    job: JobRecord,
    *,
    context=None,
    g5_contract: dict[str, object] | None = None,
) -> tuple[str, list[dict[str, object]]]:
    agent_name = job.agent_name
    body = job.request.body
    marker = body.strip()
    effective_body = _effective_request_body(job)
    workflow_reply = _workflow_role_bundle_reply(agent_name=agent_name, body=effective_body)
    if workflow_reply is None:
        workflow_reply = _workflow_multi_workgroup_orchestrator_reply(
            agent_name=agent_name,
            body=effective_body,
            g5_contract=g5_contract,
        )
    if workflow_reply is None:
        workflow_reply = _workflow_execution_reply(
            job=job,
            context=context,
            agent_name=agent_name,
            body=effective_body,
            g5_contract=g5_contract,
        )
    if workflow_reply is None:
        workflow_reply = _workflow_round_checker_reply(
            agent_name=agent_name,
            body=effective_body,
            g5_contract=g5_contract,
        )
    if workflow_reply is not None:
        return (workflow_reply, [])

    prefix = 'ccb-local-md:'
    if marker.startswith(prefix):
        ident = marker[len(prefix) :].strip() or 'sample'
        return (
            f'# CCB Local Markdown {ident}\n\n'
            f'- reply marker: `ccb-local-reply:{ident}`\n'
            '- rendered list item from the real local backend\n\n'
            '```text\n'
            f'agent={agent_name}\n'
            'route=local-loopback\n'
            '```\n\n'
            f'[blocked local link](https://example.invalid/ccb-local-md/{ident})',
            []
        )

    artifact_prefix = 'ccb-local-artifact:'
    if marker.startswith(artifact_prefix):
        ident = marker[len(artifact_prefix) :].strip() or 'sample'

        import uuid
        import json
        import hashlib
        from pathlib import Path
        from datetime import datetime, timezone

        project_id = job.request.project_id
        route_options = dict(getattr(job.request, 'route_options', None) or {})
        mobile_files_dir_text = str(route_options.get('mobile_files_dir') or '').strip()
        if mobile_files_dir_text:
            mobile_files_dir = Path(mobile_files_dir_text)
        else:
            workspace_path = job.workspace_path
            if not workspace_path:
                return (f"FAKE_ERROR: no mobile file store to generate artifact for {ident}", [])
            project_root = Path(workspace_path).parents[4]
            mobile_files_dir = project_root / '.ccb' / 'ccbd' / 'mobile' / 'files'

        attachments = []

        txt_file_id = f'mobile-file-{uuid.uuid4().hex[:16]}'
        txt_body = f'Generated text artifact for {ident}'.encode('utf-8')
        txt_digest = hashlib.sha256(txt_body).hexdigest()
        txt_dir = mobile_files_dir / project_id / agent_name / txt_file_id
        txt_dir.mkdir(parents=True, exist_ok=True)
        (txt_dir / 'content.bin').write_bytes(txt_body)
        txt_meta = {
            'schema_version': 1,
            'file_id': txt_file_id,
            'project_id': project_id,
            'agent': agent_name,
            'device_id': 'backend-agent',
            'file_name': f'artifact-{ident}.txt',
            'mime_type': 'text/plain',
            'size_bytes': len(txt_body),
            'sha256': txt_digest,
            'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        }
        (txt_dir / 'metadata.json').write_text(json.dumps(txt_meta, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        attachments.append({
            'file_id': txt_file_id,
            'file_name': f'artifact-{ident}.txt',
            'mime_type': 'text/plain',
            'size_bytes': len(txt_body),
            'kind': 'document',
        })

        png_file_id = f'mobile-file-{uuid.uuid4().hex[:16]}'
        png_body = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfc\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82'
        png_digest = hashlib.sha256(png_body).hexdigest()
        png_dir = mobile_files_dir / project_id / agent_name / png_file_id
        png_dir.mkdir(parents=True, exist_ok=True)
        (png_dir / 'content.bin').write_bytes(png_body)
        png_meta = {
            'schema_version': 1,
            'file_id': png_file_id,
            'project_id': project_id,
            'agent': agent_name,
            'device_id': 'backend-agent',
            'file_name': f'image-{ident}.png',
            'mime_type': 'image/png',
            'size_bytes': len(png_body),
            'sha256': png_digest,
            'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        }
        (png_dir / 'metadata.json').write_text(json.dumps(png_meta, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        attachments.append({
            'file_id': png_file_id,
            'file_name': f'image-{ident}.png',
            'mime_type': 'image/png',
            'size_bytes': len(png_body),
            'kind': 'image',
        })

        return (
            f'# CCB Local Artifacts {ident}\n\n'
            f'- generated artifact: [{txt_meta["file_name"]}](ccb-artifact://{txt_file_id})\n'
            f'- generated image: [{png_meta["file_name"]}](ccb-artifact://{png_file_id})\n',
            attachments
        )

    return (f'FAKE[{agent_name}] {body}', [])


def _effective_request_body(job: JobRecord) -> str:
    artifact = job.request.body_artifact if isinstance(job.request.body_artifact, dict) else {}
    path_text = str(artifact.get('path') or '').strip()
    if path_text:
        try:
            from pathlib import Path

            return Path(path_text).read_text(encoding='utf-8')
        except OSError:
            pass
    preview = str(artifact.get('preview') or '').strip()
    if preview:
        return preview
    return job.request.body


def _workflow_role_bundle_reply(*, agent_name: str, body: str) -> str | None:
    task_id = _loop_activation_task_id(body)
    if agent_name == 'planner' and 'ccb.loop.planner_artifact_bundle/v1' in body:
        return json.dumps(
            {
                'schema': 'ccb.loop.planner_artifact_bundle/v1',
                'task_id': task_id,
                'role_id': 'agentroles.ccb_planner',
                'artifacts': {
                    'brief': '# Plan Brief\n\nFake planner brief for deterministic workflow smoke.\n',
                    'requirements': '# Requirements\n\nFake planner requirements for deterministic workflow smoke.\n',
                    'acceptance': '# Acceptance\n\nFake planner acceptance criteria for deterministic workflow smoke.\n',
                    'verification': '# Verification\n\nFake planner verification contract for deterministic workflow smoke.\n',
                    'handoff': '# Handoff\n\nFake planner handoff for deterministic workflow smoke.\n',
                },
                'readiness': {'status': 'ready_for_review'},
            },
            ensure_ascii=False,
        )
    if agent_name == 'task_detailer' and 'ccb.loop.task_detailer_artifact_bundle/v1' in body:
        return json.dumps(
            {
                'schema': 'ccb.loop.task_detailer_artifact_bundle/v1',
                'task_id': task_id,
                'role_id': 'agentroles.task_detailer',
                'artifacts': {
                    'detail_design': '# Detail Design\n\nFake task_detailer detail design for deterministic workflow smoke.\n',
                    'detail_summary': '# Brief Update Summary\n\nFake task_detailer stable summary backfill for deterministic workflow smoke.\n',
                    'detail_packet': json.dumps(
                        {
                            'schema': 'ccb.loop.detail_packet_manifest/v1',
                            'task_id': task_id,
                            'source': 'fake_provider',
                            'status': 'ready_for_review',
                            'detail_design_ref': 'details/task-detail-design.md',
                            'brief_update_summary_ref': 'details/brief-update-summary.md',
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + '\n',
                },
                'readiness': {'status': 'detail_ready'},
            },
            ensure_ascii=False,
        )
    if agent_name == 'plan_reviewer' and 'ccb.loop.plan_reviewer_artifact_bundle/v1' in body:
        return json.dumps(
            {
                'schema': 'ccb.loop.plan_reviewer_artifact_bundle/v1',
                'task_id': task_id,
                'role_id': 'agentroles.ccb_plan_reviewer',
                'artifacts': {
                    'review': '# Review\n\nFake plan reviewer marks the deterministic workflow smoke ready.\n',
                },
                'readiness': {'status': 'ready'},
            },
            ensure_ascii=False,
        )
    return None


def _workflow_execution_reply(
    *,
    job: JobRecord,
    context,
    agent_name: str,
    body: str,
    g5_contract: dict[str, object] | None = None,
) -> str | None:
    g5_smoke = g5_contract or _g5_smoke_contract(body)
    scheduler_purpose = _scheduler_purpose(body) if g5_smoke is not None else ''
    if 'Role: worker' in body or scheduler_purpose in {'worker', 'worker_rework'}:
        status = 'done'
        changed_files = _materialize_fake_worker_changes(job, context, body)
        changed_files_text = ', '.join(changed_files) if changed_files else 'none'
        if 'Purpose: bounded_rework' in body:
            return (
                f'status: {status}\n'
                'work summary: addressed the bounded reviewer rejection evidence\n'
                f'changed_files: {changed_files_text}\n'
                'verification: fake provider deterministic rework verification passed\n'
                'evidence refs: task_packet execution_contract reviewer_rejection\n'
                'hidden degradation audit: no hidden fallback or scope shrink\n'
            )
        return (
            f'status: {status}\n'
            'work summary: fake provider deterministic execution completed\n'
            f'changed_files: {changed_files_text}\n'
            'verification: fake provider deterministic execution verification passed\n'
            'evidence refs: task_packet execution_contract\n'
            'hidden degradation audit: no hidden fallback or scope shrink\n'
        )
    if 'Role: code_reviewer' not in body and scheduler_purpose not in {'reviewer', 'reviewer_recheck'}:
        return None
    recheck = (
        scheduler_purpose == 'reviewer_recheck'
        or 'Purpose: bounded_rework_recheck' in body
    )
    if (
        g5_smoke is not None
        and str(g5_smoke.get('scenario')) in {
            'reviewer_rework_pass',
            'reviewer_rework_exhausted_blocked',
        }
        and _scheduler_node_id(body) == str(g5_smoke.get('selected_node'))
        and (
            not recheck
            or str(g5_smoke.get('scenario')) == 'reviewer_rework_exhausted_blocked'
        )
    ):
        return (
            'status: rework_required\n'
            'execution_contract audit: exact selected-node rework request\n'
            'verification checks performed: deterministic G5 initial reviewer rejection\n'
            'risk notes: one bounded rework required\n'
        )
    scenario = _phase6_scenario(body)
    if scenario == 'reviewer_reject_rework' and not recheck:
        return (
            'status: rework_required\n'
            'execution_contract audit: fail with evidence refs\n'
            'verification checks performed: initial rejection for bounded rework smoke\n'
            'risk notes: one bounded rework required\n'
        )
    if scenario == 'reviewer_cannot_accept':
        return (
            'status: rework_required\n'
            'execution_contract audit: fail with evidence refs\n'
            'verification checks performed: reviewer still cannot accept after bounded rework\n'
            'risk notes: replan required; do not mark done\n'
        )
    return (
        'status: pass\n'
        'execution_contract audit: pass with evidence refs\n'
        'verification checks performed: fake provider deterministic review\n'
        'risk notes: none for deterministic smoke\n'
    )


def _workflow_round_checker_reply(
    *,
    agent_name: str,
    body: str,
    g5_contract: dict[str, object] | None = None,
) -> str | None:
    normalized_agent = agent_name.replace('-', '_')
    multi_workgroup_review = (
        'Role: ccb_round_reviewer' in body
        and 'Review script-owned multi-workgroup evidence.' in body
    )
    is_round_reviewer = (
        agent_name == 'round_checker'
        or 'round_reviewer' in normalized_agent
        or multi_workgroup_review
    )
    has_round_role = 'Role: round_checker' in body or 'Role: ccb_round_reviewer' in body
    if not is_round_reviewer or not has_round_role:
        return None
    if multi_workgroup_review:
        result = (
            'blocked'
            if g5_contract is not None
            and str(g5_contract.get('scenario')) == 'round_reviewer_blocked'
            else 'pass'
        )
        return (
            f'round_result: {result}\n'
            'verification performed: fake provider deterministic multi-workgroup smoke\n'
            'hidden degradation audit: no degradation requested\n'
            'evidence refs: scheduler state, reviewed commits, integration state, release evidence\n'
            'recommended next owner: multi_workgroup_scheduler\n'
        )
    scenario = _phase6_scenario(body)
    result = 'pass'
    if scenario == 'partial_completion':
        result = 'partial'
    elif scenario == 'reviewer_cannot_accept':
        result = 'replan_required'
    return (
        f'round result: {result}\n'
        'verification performed: fake provider deterministic workflow smoke\n'
        'hidden degradation audit: no degradation requested\n'
        'evidence refs: fake-provider smoke artifacts\n'
        'recommended next owner: loop_runner\n'
    )


def _phase6_scenario(body: str) -> str:
    for raw_line in str(body or '').splitlines():
        line = raw_line.strip()
        if line.startswith('phase6_scenario:'):
            return line.split(':', 1)[1].strip().lower()
    return ''


def _materialize_fake_worker_changes(job: JobRecord, context, body: str) -> list[str]:
    context_workspace_path = str(getattr(context, 'workspace_path', '') or '').strip()
    workspace_path = str(job.workspace_path or context_workspace_path).strip()
    if not workspace_path:
        return []
    changed: list[str] = []
    workspace = Path(workspace_path)
    allowed_paths = _scheduler_allowed_change_paths(body) or _fake_allowed_change_paths(body)
    for relative in allowed_paths:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '# Fake Workflow Smoke Output\n\n'
            f'- task_id: {job.request.task_id or ""}\n'
            f'- agent: {job.agent_name}\n'
            '- verification: deterministic fake worker wrote declared project-root evidence\n',
            encoding='utf-8',
        )
        changed.append(relative.as_posix())
    return changed


def _fake_allowed_change_paths(body: str) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for raw_line in str(body or '').splitlines():
        line = raw_line.strip().lstrip('-*').strip()
        if not line.lower().startswith('allowed_change_paths:'):
            continue
        tail = line.split(':', 1)[1]
        for raw_path in tail.replace(';', ',').split(','):
            candidate = raw_path.strip().strip('`"\'').rstrip('.,')
            if not candidate:
                continue
            relative = _safe_fake_relative_path(candidate)
            if relative is None:
                continue
            key = relative.as_posix()
            if key in seen:
                continue
            paths.append(relative)
            seen.add(key)
    return paths


def _scheduler_allowed_change_paths(body: str) -> list[Path]:
    for raw_line in str(body or '').splitlines():
        line = raw_line.strip()
        if not line.startswith('Allowed paths:'):
            continue
        try:
            values = json.loads(line.split(':', 1)[1].strip())
        except json.JSONDecodeError:
            return []
        if not isinstance(values, list):
            return []
        paths = []
        for value in values:
            relative = _safe_fake_relative_path(str(value or '').strip())
            if relative is not None:
                paths.append(relative)
        return paths
    return []


def _workflow_multi_workgroup_orchestrator_reply(
    *,
    agent_name: str,
    body: str,
    g5_contract: dict[str, object] | None = None,
) -> str | None:
    if agent_name != 'orchestrator' and 'Role: ccb_orchestrator' not in body:
        return None
    contract = g5_contract or _g5_smoke_contract(body)
    if contract is None:
        return None
    candidate = _g5_orchestration_candidate(body, contract=contract)
    return (
        'route: direct_execution\n\n'
        'orchestration_notes: deterministic G5 source/fake multi-workgroup contract; '
        'task_packet and execution_contract remain script-owned authority.\n\n'
        'orchestration_bundle:\n'
        '```json\n'
        f'{json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True)}\n'
        '```\n'
    )


def _g5_orchestration_candidate(body: str, *, contract: dict[str, object]) -> dict[str, object]:
    task_id = _loop_activation_task_id(body)
    count = int(contract['count'])
    requested_shape = str(contract['shape'])
    if count == 1:
        execution_shape = 'single_unit'
    elif requested_shape == 'parallel':
        execution_shape = 'parallel'
    else:
        execution_shape = 'mixed_dag'
    artifact_refs = _literal_mapping_line(body, 'Artifact refs:')
    task_packet_ref = str(artifact_refs.get('task_packet') or '').strip()
    execution_contract_ref = str(artifact_refs.get('execution_contract') or '').strip()
    if not task_id or not task_packet_ref or not execution_contract_ref:
        raise ValueError('G5 smoke orchestrator activation is missing task/artifact refs')
    allowed_paths = contract['allowed_paths']
    nodes = []
    for index in range(1, count + 1):
        node_id = f'node-{index:03d}'
        depends_on = ['node-001'] if execution_shape == 'mixed_dag' and index == 3 else []
        nodes.append(
            {
                'node_id': node_id,
                'workgroup_id': f'wg-{index:03d}',
                'worker_profile': 'coder',
                'reviewer_profile': 'code_reviewer',
                'depends_on': depends_on,
                'parallel_group': 'wave-2' if depends_on else 'wave-1',
                'work_packet': (
                    f'G5 deterministic node {node_id}: write only {allowed_paths[index - 1]} '
                    'and return verification evidence.'
                ),
                'allowed_paths': [allowed_paths[index - 1]],
                'acceptance_refs': [task_packet_ref, execution_contract_ref],
                'verification_refs': [execution_contract_ref],
                'integration_order': index * 10,
            }
        )
    return {
        'schema': 'ccb.loop.orchestration_bundle_candidate.v1',
        'task_id': task_id,
        'bundle_revision': _expected_bundle_revision(body),
        'selection': {
            'workgroup_count': count,
            'complexity': 'atomic' if count == 1 else ('bounded' if count == 2 else 'complex'),
            'cutability': 'none' if count == 1 else 'high',
            'execution_shape': execution_shape,
            'rationale': 'Deterministic G5 smoke contract requests independently reviewable node scopes.',
        },
        'nodes': nodes,
        'integration': {
            'verification_refs': [task_packet_ref],
            'project_root_verification_refs': [execution_contract_ref],
        },
        'policy': {
            'max_node_rework_rounds': 1,
            'on_required_node_failure': 'partial_or_blocked',
            'on_structural_failure': 'replan_required',
        },
    }


def _g5_smoke_contract(body: str) -> dict[str, object] | None:
    compact = _literal_mapping_line(body, 'Compact artifacts:')
    texts = [str(body or '')]
    for value in compact.values():
        if isinstance(value, dict):
            texts.append(str(value.get('content') or ''))
    marker = 'g5_multi_workgroup_smoke:'
    contracts: list[dict[str, object]] = []
    for text in texts:
        for raw_line in text.splitlines():
            line = raw_line.strip().lstrip('-*').strip()
            if not line.startswith(marker):
                continue
            try:
                payload = json.loads(line[len(marker) :].strip())
            except json.JSONDecodeError:
                continue
            contract = _normalize_g5_smoke_contract(payload)
            if contract is not None and contract not in contracts:
                contracts.append(contract)
    if len(contracts) > 1:
        raise ValueError('conflicting G5 smoke contracts')
    return contracts[0] if contracts else None


def _normalize_g5_smoke_contract(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    required_keys = {
        'schema',
        'task_id',
        'scenario',
        'count',
        'shape',
        'selected_node',
        'restart_latency_ms',
    }
    if set(payload) != required_keys:
        return None
    if payload.get('schema') != 'ccb.g5.source_fake_runtime_scenario.v1':
        return None
    if payload.get('task_id') != 'g5-multi-workgroup-task':
        return None
    scenario = str(payload.get('scenario') or '')
    if scenario not in {
        'pass',
        'reviewer_rework_pass',
        'reviewer_rework_exhausted_blocked',
        'worker_failure_partial',
        'all_workers_failed_blocked',
        'reviewer_provider_failure',
        'round_reviewer_blocked',
        'integration_verification_failure',
        'root_verification_failure',
        'restart_replay_pass',
    }:
        return None
    count = payload.get('count')
    shape = str(payload.get('shape') or '')
    selected_node = str(payload.get('selected_node') or '')
    restart_latency_ms = payload.get('restart_latency_ms')
    if isinstance(count, bool) or not isinstance(count, int) or count not in {1, 2, 3, 4}:
        return None
    if shape not in {'parallel', 'mixed_dag'}:
        return None
    if shape == 'mixed_dag' and count < 3:
        return None
    if selected_node not in {f'node-{index:03d}' for index in range(1, count + 1)}:
        return None
    if (
        isinstance(restart_latency_ms, bool)
        or not isinstance(restart_latency_ms, int)
        or restart_latency_ms < 0
        or (scenario == 'restart_replay_pass' and restart_latency_ms == 0)
    ):
        return None
    return {
        'count': count,
        'shape': shape,
        'schema': payload['schema'],
        'task_id': payload['task_id'],
        'scenario': scenario,
        'selected_node': selected_node,
        'allowed_paths': [
            f'g5_outputs/node-{index:03d}.txt'
            for index in range(1, count + 1)
        ],
        'restart_latency_ms': restart_latency_ms,
    }


def _g5_contract_for_job(
    job: JobRecord,
    *,
    context,
    body: str,
) -> dict[str, object] | None:
    contracts: list[dict[str, object]] = []
    contract = _g5_smoke_contract(body)
    if contract is not None:
        contracts.append(contract)
    if (
        not contracts
        and job.request.task_id != 'g5-multi-workgroup-task'
        and _loop_activation_task_id(body) != 'g5-multi-workgroup-task'
    ):
        return None
    for root in _g5_explicit_project_roots(job, context=context):
        for path in sorted(
            root.glob('docs/plantree/plans/*/tasks/g5-multi-workgroup-task/task_packet.md')
        ):
            try:
                recovered = _g5_smoke_contract(path.read_text(encoding='utf-8'))
            except OSError:
                continue
            if recovered is not None and recovered not in contracts:
                contracts.append(recovered)
    if len(contracts) > 1:
        raise ValueError('conflicting G5 smoke contracts')
    return contracts[0] if contracts else None


def _g5_explicit_project_roots(job: JobRecord, *, context) -> tuple[Path, ...]:
    roots: list[Path] = []
    artifact = job.request.body_artifact if isinstance(job.request.body_artifact, dict) else {}
    if artifact:
        artifact_path = Path(str(artifact.get('path') or '')).expanduser()
        try:
            artifact_path = artifact_path.resolve(strict=True)
        except OSError as exc:
            raise ValueError('G5 smoke request artifact is unavailable') from exc
        project_root = _project_root_from_ccb_path(artifact_path)
        artifact_kind = str(artifact.get('kind') or '').strip()
        artifact_subdir = {
            'ask-request': 'ask-request',
            'result-chain-continuation': 'result-chain-continuation',
        }.get(artifact_kind)
        expected_dir = (
            project_root / '.ccb/ccbd/artifacts/text' / artifact_subdir
            if project_root is not None and artifact_subdir is not None
            else None
        )
        if (
            project_root is None
            or artifact_subdir is None
            or artifact_path.parent != expected_dir
            or compute_project_id(project_root) != job.request.project_id
        ):
            raise ValueError('G5 smoke request artifact is outside the explicit project')
        data = artifact_path.read_bytes()
        expected_size = artifact.get('bytes')
        expected_digest = str(artifact.get('sha256') or '').strip()
        if expected_size is not None and int(expected_size) != len(data):
            raise ValueError('G5 smoke request artifact byte size mismatch')
        if expected_digest and hashlib.sha256(data).hexdigest() != expected_digest:
            raise ValueError('G5 smoke request artifact sha256 mismatch')
        roots.append(project_root)
    for raw_path in (
        str(getattr(context, 'workspace_path', '') or ''),
        str(job.workspace_path or ''),
    ):
        if not raw_path:
            continue
        path = Path(raw_path).expanduser().resolve(strict=False)
        project_root = _project_root_from_ccb_path(path)
        if project_root is None and (path / '.ccb').is_dir():
            project_root = path
        if (
            project_root is not None
            and compute_project_id(project_root) == job.request.project_id
            and project_root not in roots
        ):
            roots.append(project_root)
    return tuple(roots)


def _project_root_from_ccb_path(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if candidate.name == '.ccb':
            return candidate.parent
    return None


def _g5_scenario_directive(
    directive: FakeDirective,
    *,
    contract: dict[str, object] | None,
    body: str,
) -> FakeDirective:
    if contract is None:
        return directive
    scenario = str(contract['scenario'])
    purpose = _scheduler_purpose(body)
    node_id = _scheduler_node_id(body)
    selected = str(contract['selected_node'])
    provider_failure = _g5_terminal_provider_failure(contract, body=body)
    if provider_failure:
        return replace(
            directive,
            status=CompletionStatus.FAILED,
            reason='g5_scenario_terminal_provider_failure',
            confidence=CompletionConfidence.EXACT,
            script=(),
        )
    if purpose == 'worker':
        # G5 drives the real CCB ask --chain boundary from outside the fake
        # provider. Keep the Worker active long enough for that tool action;
        # real providers execute it within their own turn.
        return replace(
            directive,
            latency_seconds=max(1.0, directive.latency_seconds),
            script=(),
        )
    if (
        scenario == 'restart_replay_pass'
        and purpose == 'reviewer'
        and node_id == selected
    ):
        return replace(
            directive,
            latency_seconds=int(contract['restart_latency_ms']) / 1000.0,
            script=(),
        )
    return directive


def _g5_terminal_provider_failure(
    contract: dict[str, object] | None,
    *,
    body: str,
) -> bool:
    if contract is None:
        return False
    scenario = str(contract['scenario'])
    purpose = _scheduler_purpose(body)
    node_id = _scheduler_node_id(body)
    selected = str(contract['selected_node'])
    return (
        scenario == 'all_workers_failed_blocked' and purpose == 'worker'
    ) or (
        scenario == 'worker_failure_partial'
        and purpose == 'worker'
        and node_id == selected
    ) or (
        scenario == 'reviewer_provider_failure'
        and purpose == 'reviewer'
        and node_id == selected
    )


def _scheduler_node_id(body: str) -> str:
    for raw_line in str(body or '').splitlines():
        line = raw_line.strip()
        if line.startswith('Node:'):
            return line.split(':', 1)[1].strip()
    return ''


def _literal_mapping_line(body: str, label: str) -> dict[str, object]:
    for raw_line in str(body or '').splitlines():
        line = raw_line.strip()
        if not line.startswith(label):
            continue
        try:
            payload = ast.literal_eval(line[len(label) :].strip())
        except (SyntaxError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _expected_bundle_revision(body: str) -> int:
    for raw_line in str(body or '').splitlines():
        line = raw_line.strip()
        if line.startswith('Expected bundle revision:'):
            try:
                value = int(line.split(':', 1)[1].strip())
            except ValueError:
                break
            if value > 0:
                return value
    raise ValueError('G5 smoke orchestrator activation is missing bundle revision')


def _scheduler_purpose(body: str) -> str:
    for raw_line in str(body or '').splitlines():
        line = raw_line.strip()
        if line.startswith('Purpose:'):
            return line.split(':', 1)[1].strip().lower()
    return ''


def _safe_fake_relative_path(value: str) -> Path | None:
    path = Path(value)
    if path.is_absolute():
        return None
    parts = path.parts
    if not parts or any(part in {'', '.', '..'} for part in parts):
        return None
    if not path.suffix:
        return None
    return path


def _loop_activation_task_id(body: str) -> str:
    for raw_line in str(body or '').splitlines():
        line = raw_line.strip()
        if line.startswith('Task:'):
            value = line.split(':', 1)[1].strip()
            if value:
                return value
    return ''


__all__ = [
    'FakeDirective',
    'FakeProviderAdapter',
    'FakeScriptEvent',
]
