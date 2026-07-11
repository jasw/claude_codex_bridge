#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


REPORT_SCHEMA = 'ccb.g5.source_fake_runtime_report.v1'
CAMPAIGN_SCHEMA = 'ccb.g5.source_fake_runtime_campaign.v1'
ROW_SCHEMA = 'ccb.g5.source_fake_runtime_evidence_row.v1'
REQUIRED_SCENARIOS = (
    'pass',
    'reviewer_rework_pass',
    'worker_failure_partial',
    'all_workers_failed_blocked',
    'reviewer_provider_failure',
    'round_reviewer_blocked',
    'integration_verification_failure',
    'root_verification_failure',
    'restart_replay_pass',
)


class CampaignFailure(RuntimeError):
    pass


def aggregate_reports(
    *,
    report_paths: list[Path],
    output_dir: Path,
    required_scenarios: tuple[str, ...] = REQUIRED_SCENARIOS,
) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve(strict=False)
    if '.ccb' in output_dir.parts:
        raise CampaignFailure('campaign output must be outside project .ccb roots')
    if not report_paths:
        raise CampaignFailure('at least one explicit --report is required')
    rows = []
    seen: set[str] = set()
    for raw_path in report_paths:
        path = raw_path.expanduser().resolve(strict=True)
        before = _sha256_file(path)
        payload = _read_json(path)
        row = _validate_report(path, payload, report_sha256=before)
        scenario = str(row['scenario'])
        if scenario in seen:
            raise CampaignFailure(f'duplicate scenario report: {scenario}')
        seen.add(scenario)
        if _sha256_file(path) != before:
            raise CampaignFailure(f'raw report mutated during aggregation: {path}')
        rows.append(row)
    missing = sorted(set(required_scenarios) - seen)
    extra = sorted(seen - set(required_scenarios))
    if missing or extra:
        raise CampaignFailure(f'scenario set mismatch: missing={missing}, extra={extra}')
    rows.sort(key=lambda item: str(item['scenario']))
    campaign = {
        'schema': CAMPAIGN_SCHEMA,
        'status': 'pass',
        'execution_mode': 'source_fake_runtime',
        'provider': 'fake',
        'coverage': {
            'real_provider': False,
            'live_provider': False,
            'disclaimer': 'Source/fake runtime campaign only; no live or real provider coverage.',
        },
        'required_scenarios': sorted(required_scenarios),
        'row_count': len(rows),
        'rows': rows,
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    _write_json(output_dir / 'campaign.json', campaign)
    (output_dir / 'evidence_rows.jsonl').write_text(
        ''.join(json.dumps(row, sort_keys=True, ensure_ascii=False) + '\n' for row in rows),
        encoding='utf-8',
    )
    (output_dir / 'B7.md').write_text(_render_b7(campaign), encoding='utf-8')
    return campaign


def _validate_report(
    path: Path,
    payload: dict[str, Any],
    *,
    report_sha256: str,
) -> dict[str, Any]:
    if payload.get('schema') != REPORT_SCHEMA:
        raise CampaignFailure(f'unsupported report schema: {path}')
    if payload.get('status') != 'pass':
        raise CampaignFailure(f'report evidence checks did not pass: {path}')
    if payload.get('execution_mode') != 'source_fake_runtime' or payload.get('provider') != 'fake':
        raise CampaignFailure(f'report mode/provider mismatch: {path}')
    coverage = _mapping(payload.get('coverage'))
    if coverage.get('real_provider') is not False or coverage.get('live_provider') is not False:
        raise CampaignFailure(f'report coverage disclaimer missing: {path}')
    scenario = str(payload.get('scenario') or '')
    if scenario not in REQUIRED_SCENARIOS:
        raise CampaignFailure(f'unknown scenario: {scenario!r}')
    expected = _mapping(payload.get('expected'))
    observed = _mapping(payload.get('observed'))
    for expected_key, observed_key in (
        ('classification', 'classification'),
        ('task_status', 'task_status'),
        ('round_result', 'round_result'),
        ('round_source', 'round_source'),
    ):
        if expected.get(expected_key) != observed.get(observed_key):
            raise CampaignFailure(
                f'{scenario} expected/observed mismatch for {expected_key}: '
                f'{expected.get(expected_key)!r} != {observed.get(observed_key)!r}'
            )
    checks = _mapping(payload.get('checks'))
    failed = sorted(key for key, value in checks.items() if value is not True)
    if failed:
        raise CampaignFailure(f'{scenario} report checks failed: {failed}')
    raw_digests = _mapping(payload.get('raw_evidence_sha256'))
    evidence_paths = _mapping(payload.get('paths'))
    path_keys = {
        'bundle': 'bundle',
        'scheduler_state': 'scheduler_state',
        'round': 'round',
        'integration_state': 'integration_state',
        'raw_observed': 'raw_observed',
    }
    for digest_key, path_key in path_keys.items():
        evidence_path = Path(str(evidence_paths.get(path_key) or ''))
        digest = str(raw_digests.get(digest_key) or '')
        if not evidence_path.is_file() or not digest or _sha256_file(evidence_path) != digest:
            raise CampaignFailure(f'{scenario} raw evidence digest mismatch: {digest_key}')
    project_root = Path(str(payload.get('project_root') or '')).resolve(strict=True)
    post_cleanup = _mapping(payload.get('post_cleanup'))
    if any(post_cleanup.get(key) for key in ('owned_processes', 'connectable_sockets', 'child_worktrees')):
        raise CampaignFailure(f'{scenario} cleanup residue is non-empty')
    return {
        'schema': ROW_SCHEMA,
        'scenario': scenario,
        'classification': observed.get('classification'),
        'project_root': str(project_root),
        'task_id': payload.get('task_id'),
        'loop_id': payload.get('loop_id'),
        'task_status': observed.get('task_status'),
        'round_result': observed.get('round_result'),
        'round_source': observed.get('round_source'),
        'node_count': _mapping(payload.get('bundle')).get('node_count'),
        'report_path': str(path),
        'report_sha256': report_sha256,
        'raw_evidence_sha256': raw_digests,
        'release': payload.get('release'),
        'post_cleanup': post_cleanup,
    }


def _render_b7(campaign: dict[str, Any]) -> str:
    lines = [
        '# G5 Source/Fake Runtime Campaign B7',
        '',
        'Status: pass',
        '',
        'Coverage: source/fake runtime only; no live or real provider coverage.',
        '',
        '| Scenario | Classification | Task | Round | Source | Report SHA256 |',
        '|---|---|---|---|---|---|',
    ]
    for row in campaign['rows']:
        lines.append(
            f"| {row['scenario']} | {row['classification']} | {row['task_status']} | "
            f"{row['round_result']} | {row['round_source']} | {row['report_sha256']} |"
        )
    return '\n'.join(lines) + '\n'


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignFailure(f'invalid JSON report {path}: {exc}') from exc
    if not isinstance(payload, dict):
        raise CampaignFailure(f'report must be a JSON object: {path}')
    return payload


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Aggregate explicit G5 source/fake runtime reports.')
    parser.add_argument('--report', action='append', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--required-scenario', action='append', choices=REQUIRED_SCENARIOS)
    parser.add_argument('--json', action='store_true')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    required = tuple(args.required_scenario or REQUIRED_SCENARIOS)
    try:
        campaign = aggregate_reports(
            report_paths=[Path(value) for value in args.report],
            output_dir=Path(args.output_dir),
            required_scenarios=required,
        )
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}, sort_keys=True))
        return 1
    if args.json:
        print(json.dumps(campaign, sort_keys=True, indent=2))
    else:
        print(f"campaign_status: {campaign['status']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
