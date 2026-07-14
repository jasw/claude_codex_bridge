"""Revision-fenced Brief/Roadmap/TODO projection for a Detailer replan."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from storage.atomic import atomic_write_json, atomic_write_text
from storage.locks import file_lock

from .planner_feedback import PlannerBackfillProposal, planner_feedback_digest
from .planner_feedback_apply import (
    _plan_revision_from_texts,
    _plan_root,
    _sections,
    _select_target_paths,
    _text_digest,
    current_plan_revision,
)


_TX_SCHEMA = 'ccb.plan.detailer_replan_backfill_transaction.v2'
_BACKFILL_SCHEMA = 'ccb.plan.detailer_replan_backfill.v2'
_TARGET_FIELDS = frozenset({'path', 'preimage_digest', 'preimage_exists', 'preimage_text', 'target_digest', 'target_text'})


def apply_detailer_replan_backfill(context, proposal: PlannerBackfillProposal, *, authority: dict[str, object], planner_job_id: str) -> dict[str, object]:
    _validate_authority(proposal, authority, planner_job_id)
    root = Path(context.project.project_root)
    slug, task_id, revision = str(authority['plan_slug']), str(authority['task_id']), int(authority['task_revision'])
    plan_root = _plan_root(context, slug)
    state_root = plan_root / 'tasks' / task_id / 'planner-replan'
    state_root.mkdir(parents=True, exist_ok=True)
    tx_path = state_root / f'backfill-r{revision}.transaction.json'
    backfill_path = state_root / f'backfill-r{revision}.json'
    with file_lock(state_root / f'backfill-r{revision}.lock'):
        persisted = _read(tx_path) if tx_path.is_file() else None
        transaction = _derive_transaction(context, plan_root, proposal, authority, planner_job_id, persisted=persisted)
        if persisted is None:
            if current_plan_revision(context, slug) != authority['expected_plan_revision']:
                raise ValueError('detailer replan plan revision conflict')
            atomic_write_json(tx_path, transaction)
        elif persisted != transaction:
            raise ValueError('detailer replan transaction authority conflict')
        record = _json_native(_backfill_record(context, proposal, authority, planner_job_id, transaction, tx_path))
        if backfill_path.is_file():
            existing = _read(backfill_path)
            if existing != record:
                raise ValueError('detailer replan backfill conflicts with persisted authority')
            _validate_targets(context, transaction)
            if current_plan_revision(context, slug) != transaction['target_plan_revision']:
                raise ValueError('detailer replan replay target revision conflict')
            return _result(root, backfill_path, tx_path, record, idempotent=True)
        _apply_targets(context, transaction)
        _validate_targets(context, transaction)
        if current_plan_revision(context, slug) != transaction['target_plan_revision']:
            raise ValueError('detailer replan target revision conflict')
        atomic_write_json(backfill_path, record)
        return _result(root, backfill_path, tx_path, record, idempotent=False)


def _validate_authority(proposal, authority, planner_job_id):
    required = {'task_id', 'task_revision', 'plan_slug', 'expected_plan_revision', 'closure_evidence_digest', 'evidence_refs', 'request_identity', 'detail_digest', 'macro_impact_digest'}
    if not isinstance(authority, dict) or set(authority) != required:
        raise ValueError('detailer replan authority fields invalid')
    if not planner_job_id or proposal.mode != 'detailer_replan' or proposal.aggregate_result != 'replan_required' or proposal.result != 'task_set_replanned':
        raise ValueError('detailer replan proposal mode or result invalid')
    expected = (authority['expected_plan_revision'], authority['task_id'], authority['task_revision'], authority['closure_evidence_digest'])
    actual = (proposal.expected_plan_revision, proposal.task_or_task_set_id, proposal.task_or_task_set_revision, proposal.closure_evidence_digest)
    if actual != expected or any(ref not in proposal.evidence_refs for ref in authority['evidence_refs']):
        raise ValueError('detailer replan proposal authority mismatch')


def _derive_transaction(context, plan_root, proposal, authority, planner_job_id, *, persisted):
    targets, projected, preimages, existing_paths = [], {}, {}, set()
    for semantic, path in zip(('brief', 'roadmap', 'todo'), _select_target_paths(context, plan_root)):
        relative = str(path.relative_to(context.project.project_root))
        prior = _persisted_target(persisted, relative)
        current = path.read_text(encoding='utf-8') if path.is_file() else ''
        if prior is None:
            before, exists = current, path.is_file()
        else:
            if _text_digest(current) not in {prior['preimage_digest'], prior['target_digest']} or path.is_file() is not prior['preimage_exists'] and _text_digest(current) == prior['preimage_digest']:
                raise ValueError('detailer replan target file revision conflict')
            before, exists = prior['preimage_text'], prior['preimage_exists']
        after = _append_block(before, str(authority['task_id']), int(authority['task_revision']), semantic, _sections(proposal)[semantic])
        target = {'path': relative, 'preimage_digest': _text_digest(before), 'preimage_exists': exists, 'preimage_text': before, 'target_digest': _text_digest(after), 'target_text': after}
        targets.append(target); projected[relative] = after; preimages[relative] = before
        if exists: existing_paths.add(relative)
    preimage = _plan_revision_from_texts(context, plan_root, preimages, include_paths=existing_paths)
    if preimage != authority['expected_plan_revision']:
        raise ValueError('detailer replan transaction preimage revision conflict')
    tx = {'schema': _TX_SCHEMA, 'schema_version': 2, 'task_id': authority['task_id'], 'task_revision': authority['task_revision'], 'planner_job_id': planner_job_id, 'planner_feedback_digest': planner_feedback_digest(proposal), 'authority': authority, 'preimage_plan_revision': preimage, 'target_plan_revision': _plan_revision_from_texts(context, plan_root, projected), 'targets': targets}
    tx['transaction_digest'] = _digest(tx)
    return tx


def _apply_targets(context, transaction):
    root = Path(context.project.project_root)
    for item in transaction['targets']:
        path = root / item['path']
        current = path.read_text(encoding='utf-8') if path.is_file() else ''
        if _text_digest(current) == item['target_digest']:
            continue
        if _text_digest(current) != item['preimage_digest'] or path.is_file() is not item['preimage_exists']:
            raise ValueError('detailer replan target file revision conflict')
        atomic_write_text(path, item['target_text'])


def _validate_targets(context, transaction):
    root = Path(context.project.project_root)
    for item in transaction['targets']:
        path = root / item['path']
        if not path.is_file() or _text_digest(path.read_text(encoding='utf-8')) != item['target_digest']:
            raise ValueError('detailer replan projected target drift')


def _backfill_record(context, proposal, authority, planner_job_id, tx, tx_path):
    record = {'schema': _BACKFILL_SCHEMA, 'schema_version': 2, 'authority': authority, 'proposal': proposal.to_record(), 'planner_job_id': planner_job_id, 'planner_feedback_digest': planner_feedback_digest(proposal), 'transaction_digest': tx['transaction_digest'], 'target_plan_revision': tx['target_plan_revision'], 'transaction_path': str(tx_path.relative_to(context.project.project_root))}
    record['backfill_digest'] = _digest(record)
    return record


def _append_block(text, task_id, revision, semantic, body):
    start, end = f'<!-- ccb-detailer-replan-backfill:{task_id}:r{revision}:{semantic}:start -->', f'<!-- ccb-detailer-replan-backfill:{task_id}:r{revision}:{semantic}:end -->'
    if start in text or end in text:
        raise ValueError('detailer replan semantic block collision')
    return (text.rstrip() + '\n\n' if text.rstrip() else '') + f'{start}\n{body.rstrip()}\n{end}\n'


def _persisted_target(transaction, path):
    if transaction is None: return None
    matches = [target for target in transaction.get('targets', []) if target.get('path') == path]
    if len(matches) != 1 or set(matches[0]) != _TARGET_FIELDS: raise ValueError('detailer replan persisted target authority invalid')
    return matches[0]


def _result(root, backfill, tx, record, *, idempotent):
    return {'backfill_path': str(backfill.relative_to(root)), 'transaction_path': str(tx.relative_to(root)), 'backfill_digest': record['backfill_digest'], 'target_plan_revision': record['target_plan_revision'], 'idempotent': idempotent}


def _read(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict): raise ValueError('detailer replan durable record must be an object')
    return value


def _digest(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _json_native(value):
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
