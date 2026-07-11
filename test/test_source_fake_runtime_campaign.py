from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'source_fake_runtime_campaign.py'


def _load_script():
    spec = importlib.util.spec_from_file_location('source_fake_runtime_campaign', SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(tmp_path: Path, *, scenario: str) -> Path:
    project = tmp_path / f'project-{scenario}'
    project.mkdir()
    evidence_paths = {}
    digests = {}
    for key in ('bundle', 'scheduler_state', 'round', 'integration_state', 'raw_observed'):
        path = project / f'{key}.json'
        path.write_text(json.dumps({'scenario': scenario, 'kind': key}) + '\n', encoding='utf-8')
        evidence_paths[key] = str(path)
        digests[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = {
        'schema': 'ccb.g5.source_fake_runtime_report.v1',
        'status': 'pass',
        'execution_mode': 'source_fake_runtime',
        'provider': 'fake',
        'coverage': {'real_provider': False, 'live_provider': False, 'disclaimer': 'fake only'},
        'scenario': scenario,
        'project_root': str(project),
        'task_id': 'g5-multi-workgroup-task',
        'loop_id': f'loop-{scenario}',
        'expected': {
            'classification': 'pass',
            'task_status': 'done',
            'round_result': 'pass',
            'round_source': 'round_reviewer_reply',
        },
        'observed': {
            'classification': 'pass',
            'task_status': 'done',
            'round_result': 'pass',
            'round_source': 'round_reviewer_reply',
        },
        'checks': {'authority': True, 'release': True},
        'bundle': {'node_count': 1},
        'release': {'released_count': 3, 'retained_count': 0},
        'post_cleanup': {'owned_processes': [], 'connectable_sockets': [], 'child_worktrees': []},
        'paths': evidence_paths,
        'raw_evidence_sha256': digests,
    }
    path = project / 'report.json'
    path.write_text(json.dumps(report, sort_keys=True) + '\n', encoding='utf-8')
    return path


def test_campaign_aggregates_only_explicit_reports_without_mutating_raw(tmp_path: Path) -> None:
    module = _load_script()
    first = _report(tmp_path, scenario='pass')
    second = _report(tmp_path, scenario='restart_replay_pass')
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (first, second)}
    output = tmp_path / 'campaign'

    campaign = module.aggregate_reports(
        report_paths=[second, first],
        output_dir=output,
        required_scenarios=('pass', 'restart_replay_pass'),
    )

    assert campaign['status'] == 'pass'
    assert [row['scenario'] for row in campaign['rows']] == ['pass', 'restart_replay_pass']
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in before.items())
    assert (output / 'campaign.json').is_file()
    assert len((output / 'evidence_rows.jsonl').read_text(encoding='utf-8').splitlines()) == 2
    assert 'no live or real provider coverage' in (output / 'B7.md').read_text(encoding='utf-8')


def test_campaign_rejects_missing_duplicate_and_digest_mismatch(tmp_path: Path) -> None:
    module = _load_script()
    report = _report(tmp_path, scenario='pass')
    with pytest.raises(module.CampaignFailure, match='scenario set mismatch'):
        module.aggregate_reports(
            report_paths=[report],
            output_dir=tmp_path / 'missing',
            required_scenarios=('pass', 'restart_replay_pass'),
        )
    with pytest.raises(module.CampaignFailure, match='duplicate scenario'):
        module.aggregate_reports(
            report_paths=[report, report],
            output_dir=tmp_path / 'duplicate',
            required_scenarios=('pass',),
        )
    payload = json.loads(report.read_text(encoding='utf-8'))
    Path(payload['paths']['round']).write_text('{}\n', encoding='utf-8')
    with pytest.raises(module.CampaignFailure, match='raw evidence digest mismatch'):
        module.aggregate_reports(
            report_paths=[report],
            output_dir=tmp_path / 'digest',
            required_scenarios=('pass',),
        )


def test_campaign_rejects_output_under_ccb_authority(tmp_path: Path) -> None:
    module = _load_script()
    report = _report(tmp_path, scenario='pass')

    with pytest.raises(module.CampaignFailure, match='outside project .ccb'):
        module.aggregate_reports(
            report_paths=[report],
            output_dir=tmp_path / '.ccb' / 'campaign',
            required_scenarios=('pass',),
        )
