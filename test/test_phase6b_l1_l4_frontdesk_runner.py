from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = PROJECT_ROOT / 'scripts' / 'phase6b_l1_l4_frontdesk_runner.py'


def _load_runner():
    spec = importlib.util.spec_from_file_location('phase6b_l1_l4_frontdesk_runner', RUNNER_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _materialize(tmp_path: Path, *, label: str = 'sequence19-worker1-20260707150000') -> tuple[Path, dict[str, object]]:
    root = tmp_path / f'deploy-l1-l4-frontdesk-{label}'
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER_PATH),
            'materialize',
            '--root',
            str(root),
            '--label',
            label,
            '--project-name',
            'l1-l4-frontdesk-real-provider-lab',
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    manifest_path = Path(result.stdout.strip())
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    return root, manifest


def _command_log_records(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _canonical_digest(value: object, *, prefixed: bool = False) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    digest = hashlib.sha256(encoded).hexdigest()
    return f'sha256:{digest}' if prefixed else digest


def _frontdesk_activation_digest(activation: dict[str, object]) -> str:
    source_job = activation['source_job']
    source_request = activation['source_request']
    assert isinstance(source_job, dict)
    assert isinstance(source_request, dict)
    mechanical = {
        key: activation[key]
        for key in (
            'schema_version', 'record_type', 'activation_id', 'project_id',
            'project_root', 'action', 'source', 'plan_slug', 'request_id',
            'intake_sha256', 'source_intake', 'planner_contract',
            'required_next_output', 'script_write_rules', 'expected_task_ids',
            'source_task_id', 'direct_ask',
        )
        if key in activation
    }
    mechanical['source_job'] = {
        key: source_job[key]
        for key in ('job_id', 'agent_name', 'reply_sha256')
        if key in source_job
    }
    mechanical['source_request'] = {
        key: source_request[key]
        for key in (
            'source_job_id', 'agent_name', 'project_id', 'to_agent',
            'from_actor', 'message_type', 'text', 'bytes', 'sha256',
        )
        if key in source_request
    }
    return _canonical_digest(mechanical, prefixed=True)


def _resident_ps(state: str = 'idle') -> str:
    return '\n'.join(
        [
            'project_id: test',
            'ccbd_state: mounted',
            f'agent: name=ccb_round_reviewer state={state} provider=claude queue=0',
            f'agent: name=frontdesk state={state} provider=codex queue=0',
            f'agent: name=orchestrator state={state} provider=codex queue=0',
            f'agent: name=planner state={state} provider=codex queue=0',
            f'agent: name=task_detailer state={state} provider=codex queue=0',
            '',
        ]
    )


def test_materializer_emits_fresh_root_manifest_and_current_label_consistently(tmp_path: Path) -> None:
    label = 'sequence19-worker1-20260707150000'
    root, manifest = _materialize(tmp_path, label=label)
    script = Path(str(manifest['script']))
    manifest_path = Path(str(manifest['manifest']))
    active_text = json.dumps(manifest, sort_keys=True) + script.read_text(encoding='utf-8')

    assert manifest['schema'] == 'ccb.phase6b_l1_l4.frontdesk_runner_manifest.v1'
    assert manifest['label'] == label
    assert manifest['root'] == str(root)
    assert manifest['project'] == str(root / 'l1-l4-frontdesk-real-provider-lab')
    assert manifest['script'] == str(root / f'run_l1_l4_frontdesk_{label}.sh')
    assert manifest['b7'] == str(root / f'phase6b-real-provider-l1-l4-{label}-b7.md')
    assert manifest['rows'] == str(root / 'rows' / f'phase6b_l1_l4_{label}_evidence_rows.jsonl')
    assert manifest['command_log'] == str(root / f'phase6b_l1_l4_{label}_command_log.jsonl')
    assert manifest['role_store'] == str(root / 'roles')
    assert manifest_path.is_file()
    assert script.is_file()
    assert str(root) in active_text
    assert label in active_text
    assert 'sequence14' not in active_text
    assert 'sequence17' not in active_text

    commands = manifest['command_sequence']
    assert isinstance(commands, list)
    assert commands[0]['step'] == 'init'
    assert commands[1]['step'] == 'frontdesk-entry'
    assert all(str(command['label']).startswith(label + '__') for command in commands)
    assert all(str(root) in ' '.join(command['argv']) for command in commands)


def test_write_fixtures_make_lab_tests_importable_by_module_name(tmp_path: Path) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))

    runner.write_fixtures(manifest)
    test_module = project / 'tests' / 'test_l2_readiness.py'
    test_module.write_text(
        'import unittest\n\n'
        'class ReadinessImportTest(unittest.TestCase):\n'
        '    def test_imports_from_local_tests_package(self):\n'
        '        self.assertTrue(True)\n'
        '\n',
        encoding='utf-8',
    )
    result = subprocess.run(
        [sys.executable, '-m', 'unittest', 'tests.test_l2_readiness'],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )

    assert (project / 'tests' / '__init__.py').is_file()
    assert result.returncode == 0, result.stderr


def test_materializer_rejects_existing_root_and_stale_sequence_fragments(tmp_path: Path) -> None:
    existing = tmp_path / 'deploy-l1-l4-frontdesk-sequence19-existing'
    existing.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER_PATH),
            'materialize',
            '--root',
            str(existing),
            '--label',
            'sequence19-existing',
            '--project-name',
            'l1-l4-frontdesk-real-provider-lab',
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert 'fresh root already exists' in result.stderr

    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER_PATH),
            'materialize',
            '--root',
            str(tmp_path / 'deploy-l1-l4-frontdesk-sequence17-old'),
            '--label',
            'sequence19-new',
            '--project-name',
            'l1-l4-frontdesk-real-provider-lab',
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert 'stale sequence marker' in result.stderr


def test_provider_loop_runner_commands_are_unbounded(tmp_path: Path) -> None:
    _root, manifest = _materialize(tmp_path)
    source_text = RUNNER_PATH.read_text(encoding='utf-8')
    script_text = Path(str(manifest['script'])).read_text(encoding='utf-8')
    active_text = json.dumps(manifest, sort_keys=True) + script_text
    provider_invocations = manifest['provider_loop_invocations']

    assert 'timeout --preserve-status' not in source_text
    assert 'timeout --preserve-status' not in active_text
    assert provider_invocations
    for invocation in provider_invocations:
        argv = invocation['argv']
        assert invocation['unbounded'] is True
        assert argv[-4:] == ['loop', 'runner', '--once', '--json']
        assert '--timeout' not in argv
        assert 'timeout' not in argv


def test_frontdesk_request_is_natural_language_with_current_intake_contract(tmp_path: Path) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)

    request_path = runner.write_frontdesk_request(manifest)
    request = request_path.read_text(encoding='utf-8')

    assert request.startswith('Please start ')
    assert not request.startswith('**Intake Evidence**')
    assert 'User Request' in request
    assert 'Macro request' in request
    assert 'Execution Contract' in request
    assert 'Acceptance Criteria' in request
    assert 'Scope' in request
    assert 'Constraints' in request
    assert '**Intake Evidence**' in request
    assert 'silent handoff to planner' in request.lower()
    assert 'must not directly implement' in request.lower()
    for task in runner.TASKS:
        assert task['task_id'] in request
        assert task['expected_route'] in request


def test_generated_config_mounts_required_resident_ask_targets_not_only_role_profiles(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))

    runner.write_config(manifest)
    config_text = (project / '.ccb' / 'ccb.config').read_text(encoding='utf-8')
    resident_targets = set(runner.resident_targets_from_config_text(config_text))

    assert '[windows]' in config_text
    assert set(manifest['resident_ask_targets']) == {
        'frontdesk',
        'planner',
        'orchestrator',
        'task_detailer',
        'ccb_round_reviewer',
    }
    assert resident_targets >= set(manifest['resident_ask_targets'])
    assert 'frontdesk:codex' in config_text
    assert 'planner:codex' in config_text
    assert 'orchestrator:codex' in config_text
    assert 'task_detailer:codex' in config_text
    assert 'ccb_round_reviewer:claude' in config_text
    assert 'coder' not in resident_targets
    assert 'code_reviewer' not in resident_targets
    assert '[loop.role_profiles.coder]' in config_text
    assert '[loop.role_profiles.code_reviewer]' in config_text

    worker3_broken_config = """version = 2
entry_window = "main"

[windows]
ccb-user = "bootstrap:codex"

[loop.role_profiles.frontdesk]
role = "agentroles.ccb_frontdesk"
provider = "codex"

[loop.role_profiles.planner]
role = "agentroles.ccb_planner"
provider = "codex"

[loop.role_profiles.orchestrator]
role = "agentroles.ccb_orchestrator"
provider = "codex"

[loop.role_profiles.task_detailer]
role = "agentroles.ccb_task_detailer"
provider = "codex"

[loop.role_profiles.ccb_round_reviewer]
role = "agentroles.ccb_round_reviewer"
provider = "claude"
"""

    assert runner.resident_targets_from_config_text(worker3_broken_config) == ['bootstrap']
    with pytest.raises(runner.HarnessError, match='frontdesk'):
        runner.validate_config_mounts_resident_targets(worker3_broken_config)


def test_frontdesk_entry_rejects_resident_layout_without_mounted_agent_specs(tmp_path: Path) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    command_log = Path(str(manifest['command_log']))
    runner.write_config(manifest)
    runner._write_json(project / '.ccb' / 'agents' / 'bootstrap' / 'agent.json', {'name': 'bootstrap'})
    command_log.write_text(
        json.dumps({'label': str(manifest['label']) + '__preexisting'}) + '\n',
        encoding='utf-8',
    )

    assert [target for target, _path in runner.missing_resident_agent_specs(manifest)] == [
        'frontdesk',
        'planner',
        'orchestrator',
        'task_detailer',
        'ccb_round_reviewer',
    ]

    result = subprocess.run(
        ['bash', str(manifest['script']), 'frontdesk-entry'],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert 'resident_agents_not_mounted' in result.stderr
    assert 'frontdesk:' in result.stderr
    assert all(
        record.get('label') != str(manifest['label']) + '__frontdesk_entry_ask'
        for record in _command_log_records(command_log)
    )


def test_resident_agent_guard_rejects_mismatched_agent_spec_identity(tmp_path: Path) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    runner.write_config(manifest)
    for target in manifest['resident_agent_targets']:
        name = 'bootstrap' if target == 'frontdesk' else str(target)
        runner._write_json(project / '.ccb' / 'agents' / str(target) / 'agent.json', {'name': name})

    problems = runner.resident_agent_mount_problems(manifest)

    assert any(
        problem['target'] == 'frontdesk'
        and problem['reason'] == 'name_mismatch'
        and problem.get('observed') == 'bootstrap'
        for problem in problems
    )
    with pytest.raises(runner.HarnessError, match='resident_agents_not_mounted.*frontdesk.*bootstrap'):
        runner.assert_resident_agents_mounted(manifest)


def test_frontdesk_entry_allows_ask_path_when_all_resident_agent_specs_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    runner.write_config(manifest)
    for target in manifest['resident_agent_targets']:
        runner._write_json(project / '.ccb' / 'agents' / str(target) / 'agent.json', {'name': target})

    runner.assert_resident_agents_mounted(manifest)

    calls = []

    def fake_run_logged(observed_manifest, label_suffix, argv):
        calls.append((observed_manifest, label_suffix, argv))
        if label_suffix == 'resident_ps_before_frontdesk_entry':
            _label, stdout_path, _stderr_path = runner.command_output_paths(
                observed_manifest,
                label_suffix,
            )
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_path.write_text(_resident_ps(), encoding='utf-8')

    monkeypatch.setattr(runner, 'run_logged', fake_run_logged)

    runner.frontdesk_entry(manifest)

    assert runner.missing_resident_agent_specs(manifest) == []
    assert runner.resident_agent_mount_problems(manifest) == []
    assert [call[1] for call in calls] == [
        'resident_ps_before_frontdesk_entry',
        'frontdesk_entry_ask',
    ]
    assert calls[0][2] == [str(PROJECT_ROOT / 'ccb_test'), '--project', str(project), 'ps']
    observed_manifest, label_suffix, argv = calls[1]
    assert observed_manifest is manifest
    assert label_suffix == 'frontdesk_entry_ask'
    assert argv[:3] == [str(PROJECT_ROOT / 'ccb_test'), '--project', str(project)]
    assert argv[3:6] == ['ask', 'frontdesk', '--']
    assert Path(str(manifest['frontdesk_request'])).is_file()
    request_text = Path(str(manifest['frontdesk_request'])).read_text(encoding='utf-8')
    assert argv[-4:] == ['ask', 'frontdesk', '--', request_text]
    assert 'controller-owned route-mix validation' in request_text
    assert not Path(str(manifest['command_log'])).exists()


def test_resident_readiness_accepts_all_idle_ps_state(tmp_path: Path) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)

    states = runner.parse_resident_ps_states(_resident_ps())

    assert states == {target: 'idle' for target in manifest['resident_agent_targets']}
    assert runner.resident_readiness_problems(manifest, _resident_ps()) == []
    runner.assert_resident_agents_ready_from_ps(manifest, _resident_ps())


def test_resident_readiness_rejects_degraded_ps_state_before_frontdesk_ask(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    runner.write_config(manifest)
    for target in manifest['resident_agent_targets']:
        runner._write_json(project / '.ccb' / 'agents' / str(target) / 'agent.json', {'name': target})
    worker3_bad_ps = """project_id: 6ee7aaa7
ccbd_state: mounted
agent: name=ccb_round_reviewer state=degraded provider=claude queue=0
agent: name=frontdesk state=degraded provider=codex queue=0
agent: name=orchestrator state=degraded provider=codex queue=0
agent: name=planner state=degraded provider=codex queue=0
agent: name=task_detailer state=degraded provider=codex queue=0
"""

    states = runner.parse_resident_ps_states(worker3_bad_ps)
    assert states['frontdesk'] == 'degraded'
    problems = runner.resident_readiness_problems(manifest, worker3_bad_ps)

    assert {problem['target'] for problem in problems} == set(manifest['resident_agent_targets'])
    assert {problem['reason'] for problem in problems} == {'state_not_ready'}
    with pytest.raises(runner.HarnessError, match='resident_agents_not_ready.*frontdesk.*degraded'):
        runner.assert_resident_agents_ready_from_ps(manifest, worker3_bad_ps)
    with pytest.raises(runner.HarnessError, match='resident_agents_not_ready.*frontdesk.*busy'):
        runner.assert_resident_agents_ready_from_ps(manifest, _resident_ps('busy'))

    calls = []

    def fake_run_logged(observed_manifest, label_suffix, argv):
        calls.append((label_suffix, argv))
        if label_suffix == 'frontdesk_entry_ask':
            pytest.fail('frontdesk ask must not run when resident ps is degraded')
        _label, stdout_path, _stderr_path = runner.command_output_paths(
            observed_manifest,
            label_suffix,
        )
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(worker3_bad_ps, encoding='utf-8')

    monkeypatch.setattr(runner, 'run_logged', fake_run_logged)

    with pytest.raises(runner.HarnessError, match='resident_agents_not_ready.*frontdesk.*degraded'):
        runner.frontdesk_entry(manifest)

    assert calls == [
        (
            'resident_ps_before_frontdesk_entry',
            [str(PROJECT_ROOT / 'ccb_test'), '--project', str(project), 'ps'],
        )
    ]
    assert not Path(str(manifest['frontdesk_request'])).exists()


def test_resident_readiness_retries_transient_empty_ps_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    calls = []

    def fake_run_logged(observed_manifest, label_suffix, argv):
        calls.append((label_suffix, argv))
        _label, stdout_path, _stderr_path = runner.command_output_paths(
            observed_manifest,
            label_suffix,
        )
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        if label_suffix == 'resident_ps_after_start':
            stdout_path.write_text('', encoding='utf-8')
        else:
            stdout_path.write_text(_resident_ps(), encoding='utf-8')

    monkeypatch.setattr(runner, 'run_logged', fake_run_logged)
    monkeypatch.setattr(runner.time, 'sleep', lambda _seconds: None)

    runner.assert_resident_agents_ready(manifest, 'resident_ps_after_start')

    assert calls == [
        (
            'resident_ps_after_start',
            [str(PROJECT_ROOT / 'ccb_test'), '--project', str(project), 'ps'],
        ),
        (
            'resident_ps_after_start_retry_1',
            [str(PROJECT_ROOT / 'ccb_test'), '--project', str(project), 'ps'],
        ),
    ]


def test_resident_readiness_rejects_persistently_empty_ps_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    calls = []

    def fake_run_logged(observed_manifest, label_suffix, argv):
        calls.append((label_suffix, argv))
        _label, stdout_path, _stderr_path = runner.command_output_paths(
            observed_manifest,
            label_suffix,
        )
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text('', encoding='utf-8')

    monkeypatch.setattr(runner, 'run_logged', fake_run_logged)
    monkeypatch.setattr(runner.time, 'sleep', lambda _seconds: None)

    with pytest.raises(runner.HarnessError, match='resident_agents_not_ready.*frontdesk.*state_missing'):
        runner.assert_resident_agents_ready(manifest, 'resident_ps_after_start')

    assert calls == [
        (
            'resident_ps_after_start',
            [str(PROJECT_ROOT / 'ccb_test'), '--project', str(project), 'ps'],
        ),
        (
            'resident_ps_after_start_retry_1',
            [str(PROJECT_ROOT / 'ccb_test'), '--project', str(project), 'ps'],
        ),
        (
            'resident_ps_after_start_retry_2',
            [str(PROJECT_ROOT / 'ccb_test'), '--project', str(project), 'ps'],
        ),
    ]


def test_stale_provider_binding_snapshot_detects_log_path_outside_project(tmp_path: Path) -> None:
    runner = _load_runner()
    project = tmp_path / 'current-project'
    project.mkdir()
    snapshot = {
        'delivery_checked_session_root': str(project),
        'delivery_current_log_path': str(tmp_path / 'old-donor' / '.ccb' / 'codex.log'),
    }

    problems = runner.stale_provider_binding_problems(project, snapshot)

    assert problems == [
        {
            'reason': 'stale_provider_session_log_binding',
            'delivery_current_log_path': str(tmp_path / 'old-donor' / '.ccb' / 'codex.log'),
            'project': str(project),
            'delivery_checked_session_root': str(project),
        }
    ]
    assert runner.stale_provider_binding_problems(
        project,
        {'delivery_current_log_path': str(project / '.ccb' / 'agents' / 'frontdesk' / 'codex.log')},
    ) == []


def test_auto_runner_quiet_wait_blocks_manual_progress_while_lock_is_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    states = [
        {
            'status': 'live',
            'pid': 12345,
            'path': str(Path(str(manifest['project'])) / '.ccb/runtime/loops/auto-runner.lock'),
        },
        {
            'status': 'live',
            'pid': 12345,
            'path': str(Path(str(manifest['project'])) / '.ccb/runtime/loops/auto-runner.lock'),
        },
    ]
    sleeps = []

    monkeypatch.setattr(runner, 'AUTO_RUNNER_QUIET_ATTEMPTS', len(states))
    monkeypatch.setattr(runner, 'auto_runner_lock_state', lambda _manifest: states.pop(0))
    monkeypatch.setattr(runner.time, 'sleep', lambda seconds: sleeps.append(seconds))

    with pytest.raises(
        runner.HarnessBlocker,
        match='runner_resume_and_evidence_integrity.*frontdesk_auto_runner_still_active',
    ):
        runner.wait_for_auto_runner_quiet(manifest, before='start_task_phase6b-l1-doc-direct-execution')

    checkpoint = (
        Path(str(manifest['root']))
        / 'pending-checkpoints'
        / f"{manifest['label']}__auto_runner_active_before_start_task_phase6b-l1-doc-direct-execution.json"
    )
    payload = json.loads(checkpoint.read_text(encoding='utf-8'))
    assert payload['classification'] == 'runner_resume_and_evidence_integrity'
    assert payload['reason'] == 'frontdesk_auto_runner_still_active'
    assert payload['claimable'] is False
    assert payload['auto_runner_lock']['status'] == 'live'
    assert sleeps == [runner.AUTO_RUNNER_QUIET_RETRY_DELAY_SECONDS] * 2


def test_start_task_observes_already_completed_task_before_waiting_for_auto_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    task_id = 'phase6b-l2-code-test-direct-execution'
    calls = []

    monkeypatch.setattr(runner, 'wait_for_planner_task_set_handoff', lambda _manifest, *, before: {})
    monkeypatch.setattr(runner, 'assert_planner_task_set_present', lambda _manifest: {})
    monkeypatch.setattr(runner, 'validate_sequence_task_set_only', lambda _manifest: None)
    monkeypatch.setattr(runner, 'task_record_exists', lambda _manifest, _task_id: True)

    def fail_wait(_manifest, *, before):
        raise AssertionError(f'unexpected auto-runner wait before completed task observation: {before}')

    def fake_run_logged(observed_manifest, label_suffix, argv):
        calls.append((label_suffix, argv))
        assert label_suffix == f'{task_id}__task_observe_existing'
        _label, stdout_path, _stderr_path = runner.command_output_paths(
            observed_manifest,
            label_suffix,
        )
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(
            json.dumps(
                {
                    'task': {
                        'task_id': task_id,
                        'status': 'done',
                        'current_loop': None,
                        'artifacts': {
                            'task_packet': {'path': 'task_packet.md'},
                            'execution_contract': {'path': 'execution_contract.md'},
                            'orchestration_notes': {'orchestrator_route': 'direct_execution'},
                        },
                    },
                }
            ),
            encoding='utf-8',
        )

    monkeypatch.setattr(runner, 'wait_for_auto_runner_quiet', fail_wait)
    monkeypatch.setattr(runner, 'run_logged', fake_run_logged)

    runner.start_task(manifest, task_id)

    assert [call[0] for call in calls] == [f'{task_id}__task_observe_existing']


def test_start_task_waits_for_frontdesk_auto_runner_before_manual_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    task_id = 'phase6b-l1-doc-direct-execution'
    calls = []
    handoff_seen = {'value': False}
    wait_seen = {'value': False}

    def fake_wait_for_planner_task_set_handoff(_manifest, *, before):
        calls.append(('wait_for_planner_task_set_handoff', before))
        handoff_seen['value'] = True
        return {'fenced_task_set_present': True}

    def fake_assert_planner_task_set_present(_manifest):
        assert handoff_seen['value'] is True
        calls.append(('assert_planner_task_set_present', None))
        return {}

    monkeypatch.setattr(runner, 'assert_planner_task_set_present', fake_assert_planner_task_set_present)
    monkeypatch.setattr(runner, 'validate_sequence_task_set_only', lambda _manifest: None)
    monkeypatch.setattr(runner, 'task_record_exists', lambda _manifest, _task_id: True)

    def fake_wait(_manifest, *, before):
        assert handoff_seen['value'] is True
        calls.append(('wait', before))
        wait_seen['value'] = True

    def fake_run_logged(observed_manifest, label_suffix, argv):
        assert handoff_seen['value'] is True
        calls.append(('run_logged', label_suffix))
        if label_suffix == f'{task_id}__task_observe_existing':
            _label, stdout_path, _stderr_path = runner.command_output_paths(
                observed_manifest,
                label_suffix,
            )
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_path.write_text(
                json.dumps(
                    {
                        'task': {
                            'task_id': task_id,
                            'status': 'ready_for_orchestration',
                            'current_loop': None,
                            'artifacts': {
                                'task_packet': {'path': 'task_packet.md'},
                                'execution_contract': {'path': 'execution_contract.md'},
                            },
                        }
                    }
                ),
                encoding='utf-8',
            )

    monkeypatch.setattr(runner, 'wait_for_planner_task_set_handoff', fake_wait_for_planner_task_set_handoff)
    monkeypatch.setattr(runner, 'wait_for_auto_runner_quiet', fake_wait)
    monkeypatch.setattr(runner, 'run_logged', fake_run_logged)

    runner.start_task(manifest, task_id)

    assert calls == [
        ('wait_for_planner_task_set_handoff', f'start_task_{task_id}'),
        ('assert_planner_task_set_present', None),
        ('run_logged', f'{task_id}__task_observe_existing'),
        ('wait', f'start_task_{task_id}'),
        ('run_logged', f'{task_id}__task_observe_existing'),
        ('run_logged', f'{task_id}__activate_orchestrator'),
    ]


def test_planner_task_set_handoff_wait_checkpoints_before_false_missing_task_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    states = [
        {'frontdesk_job_id': 'job_frontdesk', 'frontdesk_job_status': 'running'},
        {'frontdesk_job_id': 'job_frontdesk', 'frontdesk_job_status': 'running'},
    ]
    sleeps = []

    monkeypatch.setattr(runner, 'PLANNER_TASK_SET_WAIT_ATTEMPTS', len(states))
    monkeypatch.setattr(runner, 'planner_task_set_handoff_state', lambda _manifest: states.pop(0))
    monkeypatch.setattr(runner.time, 'sleep', lambda seconds: sleeps.append(seconds))

    with pytest.raises(
        runner.HarnessBlocker,
        match='runner_resume_and_evidence_integrity.*frontdesk_planner_handoff_pending',
    ):
        runner.wait_for_planner_task_set_handoff(
            manifest,
            before='start_task_phase6b-l1-doc-direct-execution',
        )

    checkpoint = (
        Path(str(manifest['root']))
        / 'pending-checkpoints'
        / (
            f"{manifest['label']}__planner_task_set_handoff_pending_before_"
            'start_task_phase6b-l1-doc-direct-execution.json'
        )
    )
    payload = json.loads(checkpoint.read_text(encoding='utf-8'))
    assert payload['classification'] == 'runner_resume_and_evidence_integrity'
    assert payload['reason'] == 'frontdesk_planner_handoff_pending'
    assert payload['claimable'] is False
    assert payload['handoff_state']['frontdesk_job_status'] == 'running'
    assert not Path(str(manifest['rows'])).exists()
    assert sleeps == [runner.PLANNER_TASK_SET_WAIT_RETRY_DELAY_SECONDS] * 2


def test_planner_task_set_handoff_blocks_immediately_when_frontdesk_failed_without_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    snapshot = Path(str(manifest['project'])) / '.ccb/ccbd/snapshots/job_frontdesk.json'
    state = {
        'frontdesk_job_id': 'job_frontdesk',
        'frontdesk_job_status': 'failed',
        'frontdesk_job_reason': 'frontdesk_direct_implementation_boundary_violation',
        'frontdesk_snapshot_path': str(snapshot),
        'planner_job_id': None,
        'activation_path': None,
        'fenced_task_set_present': False,
    }
    sleeps = []

    monkeypatch.setattr(runner, 'planner_task_set_handoff_state', lambda _manifest: state)
    monkeypatch.setattr(runner.time, 'sleep', lambda seconds: sleeps.append(seconds))

    with pytest.raises(
        runner.HarnessBlocker,
        match='runner_resume_and_evidence_integrity.*frontdesk_terminal_without_planner_handoff',
    ):
        runner.wait_for_planner_task_set_handoff(manifest, before='start_task_phase6b-l1-doc-direct-execution')

    checkpoint = (
        Path(str(manifest['root']))
        / 'pending-checkpoints'
        / (
            f"{manifest['label']}__frontdesk_terminal_without_planner_handoff_before_"
            'start_task_phase6b-l1-doc-direct-execution.json'
        )
    )
    payload = json.loads(checkpoint.read_text(encoding='utf-8'))
    assert payload['reason'] == 'frontdesk_terminal_without_planner_handoff'
    assert payload['handoff_state']['frontdesk_job_status'] == 'failed'
    assert payload['handoff_state']['frontdesk_job_reason'] == 'frontdesk_direct_implementation_boundary_violation'
    assert payload['handoff_state']['frontdesk_snapshot_path'] == str(snapshot)
    assert sleeps == []


def test_planner_task_set_handoff_state_preserves_frontdesk_terminal_snapshot(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    job_id = 'job_frontdesk'
    logs = root / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    (logs / 'entry__frontdesk_entry_ask.stdout').write_text(
        f'accepted job={job_id}\n',
        encoding='utf-8',
    )
    snapshot = project / f'.ccb/ccbd/snapshots/{job_id}.json'
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(
        json.dumps(
            {
                'latest_decision': {
                    'status': 'failed',
                    'reason': 'frontdesk_direct_implementation_boundary_violation',
                }
            }
        ),
        encoding='utf-8',
    )

    state = runner.planner_task_set_handoff_state(manifest)

    assert state['frontdesk_job_id'] == job_id
    assert state['frontdesk_job_status'] == 'failed'
    assert state['frontdesk_job_reason'] == 'frontdesk_direct_implementation_boundary_violation'
    assert state['frontdesk_snapshot_path'] == str(snapshot)
    assert state['planner_job_id'] is None
    assert state['activation_path'] is None


@pytest.mark.parametrize(
    'state',
    [
        {'frontdesk_job_status': 'pending'},
        {'frontdesk_job_status': 'running'},
    ],
)
def test_planner_task_set_handoff_keeps_waiting_for_nonterminal_frontdesk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, object],
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    observed = {'frontdesk_job_id': 'job_frontdesk', **state}
    sleeps = []

    monkeypatch.setattr(runner, 'PLANNER_TASK_SET_WAIT_ATTEMPTS', 1)
    monkeypatch.setattr(runner, 'planner_task_set_handoff_state', lambda _manifest: observed)
    monkeypatch.setattr(runner.time, 'sleep', lambda seconds: sleeps.append(seconds))

    with pytest.raises(runner.HarnessBlocker, match='frontdesk_planner_handoff_pending'):
        runner.wait_for_planner_task_set_handoff(manifest, before='start_task')

    assert sleeps == [runner.PLANNER_TASK_SET_WAIT_RETRY_DELAY_SECONDS]


@pytest.mark.parametrize(
    'state',
    [
        {'fenced_task_set_present': True},
        {'planner_job_id': 'job_planner', 'planner_job_status': 'failed'},
    ],
)
def test_planner_task_set_handoff_preserves_existing_terminal_conditions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, object],
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    monkeypatch.setattr(runner, 'planner_task_set_handoff_state', lambda _manifest: state)
    monkeypatch.setattr(
        runner.time,
        'sleep',
        lambda _seconds: (_ for _ in ()).throw(AssertionError('unexpected sleep')),
    )

    assert runner.wait_for_planner_task_set_handoff(manifest, before='start_task') == state


def test_planner_task_set_handoff_ignores_activation_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    activation_dir = project / '.ccb/runtime/loops/activations'
    canonical_activation = activation_dir / 'act-frontdesk-job_31092c1cac55.json'
    planner_job_id = 'job_planner'
    runner._write_json(
        canonical_activation,
        {
            'record_type': 'ccb_loop_frontdesk_planner_activation',
            'request_id': 'job_31092c1cac55',
            'source_job': {'job_id': 'job_31092c1cac55'},
            'ask': {'target': 'planner', 'job_id': planner_job_id},
        },
    )
    runner._write_json(
        activation_dir / 'act-frontdesk-job_31092c1cac55.direct-handoff.transaction.json',
        {'record_type': 'ccb_loop_frontdesk_direct_handoff_transaction'},
    )
    runner._write_json(
        activation_dir / 'act-frontdesk-job_31092c1cac55.recovery-error.json',
        {'record_type': 'ccb_loop_frontdesk_recovery_error'},
    )
    planner_reply = (
        project
        / '.ccb/ccbd/artifacts/text/completion-reply'
        / f'{planner_job_id}-art_test.txt'
    )
    planner_reply.parent.mkdir(parents=True, exist_ok=True)
    planner_reply.write_text('**task-set.json**\n', encoding='utf-8')
    monkeypatch.setattr(
        runner.time,
        'sleep',
        lambda _seconds: (_ for _ in ()).throw(AssertionError('unexpected wait')),
    )

    state = runner.wait_for_planner_task_set_handoff(manifest, before='start_task')

    assert state['activation_path'] == str(canonical_activation)
    assert state['planner_job_id'] == planner_job_id
    assert state['planner_reply_path'] == str(planner_reply)
    assert state['fenced_task_set_present'] is True


def test_init_writes_config_before_startup_and_validates_mount_after_startup() -> None:
    source = RUNNER_PATH.read_text(encoding='utf-8')
    init_start = source.index('def init_lab(')
    init_end = source.index('\n\ndef frontdesk_entry(', init_start)
    init_body = source[init_start:init_end]

    assert init_body.index('write_config(manifest)') < init_body.index('"start_project"')
    assert init_body.index('"start_project"') < init_body.index('assert_resident_agents_mounted(manifest)')
    assert init_body.index('assert_resident_agents_mounted(manifest)') < init_body.index(
        'assert_resident_agents_ready(manifest, "resident_ps_after_start")'
    )

    frontdesk_start = source.index('def frontdesk_entry(')
    frontdesk_end = source.index('\n\ndef task_record_exists(', frontdesk_start)
    frontdesk_body = source[frontdesk_start:frontdesk_end]

    assert frontdesk_body.index('assert_resident_agents_mounted(manifest)') < frontdesk_body.index(
        'assert_resident_agents_ready(manifest, "resident_ps_before_frontdesk_entry")'
    )
    assert frontdesk_body.index(
        'assert_resident_agents_ready(manifest, "resident_ps_before_frontdesk_entry")'
    ) < frontdesk_body.index('"frontdesk_entry_ask"')


def test_pending_guard_blocks_cleanup_for_pending_and_incomplete_authority(tmp_path: Path) -> None:
    runner = _load_runner()
    root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    task_id = 'phase6b-l1-doc-direct-execution'

    runner._write_json(
        project / '.ccb' / 'runtime' / 'loops' / 'lp-pending-round' / 'round.pending.json',
        {'task_id': task_id, 'round_result': 'pending'},
    )
    runner._write_json(
        project / '.ccb' / 'runtime' / 'loops' / 'lp-ask-first' / 'ask_first_stage_state.json',
        {'task_id': task_id, 'status': 'pending'},
    )
    runner._write_json(
        project / '.ccb' / 'runtime' / 'loops' / 'lp-incomplete' / 'round.json',
        {
            'task_id': task_id,
            'round_result_source': 'ask_job_incomplete',
            'worker': {'status': 'incomplete'},
        },
    )

    problems = runner.pending_authority_problems(project)
    reasons = {problem['reason'] for problem in problems}

    assert {'round_pending', 'ask_first_stage_pending', 'ask_job_incomplete'} <= reasons

    result = subprocess.run(
        ['bash', str(manifest['script']), 'cleanup-after-b7'],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert 'round authority pending/incomplete' in result.stderr
    assert not Path(str(manifest['command_log'])).exists()


def test_command_evidence_duplicate_label_is_rejected_before_overwrite(tmp_path: Path) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    label_suffix = 'phase6b-l1-doc-direct-execution__run_direct_execution_round'
    label, stdout_path, stderr_path = runner.command_output_paths(manifest, label_suffix)
    command_log = Path(str(manifest['command_log']))
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text('original stdout evidence\n', encoding='utf-8')
    stderr_path.write_text('original stderr evidence\n', encoding='utf-8')
    command_log.write_text(json.dumps({'label': label, 'stdout': str(stdout_path)}) + '\n', encoding='utf-8')

    with pytest.raises(runner.HarnessError, match='evidence_integrity_duplicate_label'):
        runner.run_logged(manifest, label_suffix, [sys.executable, '-c', 'print("new output")'])

    assert stdout_path.read_text(encoding='utf-8') == 'original stdout evidence\n'
    assert stderr_path.read_text(encoding='utf-8') == 'original stderr evidence\n'


def test_sequence25_pending_reviewer_checkpoint_blocks_b7_and_cleanup(tmp_path: Path) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    task_id = 'phase6b-l1-doc-direct-execution'
    loop_id = 'lp1b2b3a'
    pending_payload = {
        'schema_version': 1,
        'record_type': 'ccb_loop_ask_first_execution_round',
        'loop_run_status': 'pending',
        'loop_id': loop_id,
        'task_id': task_id,
        'pending': {
            'source': 'ask_job_pending',
            'stage': 'reviewer_ask',
            'purpose': 'reviewer',
            'reason': 'reviewer job status running is not terminal',
            'target': f'loop-{loop_id}-code_reviewer-1',
            'job_id': 'job_e9edbc409b48',
            'job_status': 'running',
            'watch_source': 'persisted_terminal',
            'watch_observation': 'not_terminal',
        },
        'reviewer': {
            'target': f'loop-{loop_id}-code_reviewer-1',
            'purpose': 'reviewer',
            'job_id': 'job_e9edbc409b48',
            'status': 'running',
            'terminal': False,
        },
    }
    runner._write_json(
        project / '.ccb' / 'runtime' / 'loops' / loop_id / 'round.pending.json',
        pending_payload,
    )
    runner._write_json(
        project / '.ccb' / 'runtime' / 'loops' / loop_id / 'ask_first_stage_state.json',
        {
            'schema_version': 1,
            'record_type': 'ccb_loop_ask_first_stage_state',
            'status': 'pending',
            'task_id': task_id,
            'loop_id': loop_id,
            'stage': 'reviewer_ask',
            'target': f'loop-{loop_id}-code_reviewer-1',
            'job_id': 'job_e9edbc409b48',
            'purpose': 'reviewer',
            'pending': pending_payload['pending'],
        },
    )

    problems = runner.pending_authority_problems(project, task_id)

    assert {problem['reason'] for problem in problems} == {
        'round_pending',
        'ask_first_stage_pending',
    }
    assert all(problem['loop_id'] == loop_id for problem in problems)
    assert all(problem['task_id'] == task_id for problem in problems)
    assert all(problem['stage'] == 'reviewer_ask' for problem in problems)
    assert all(problem['job_id'] == 'job_e9edbc409b48' for problem in problems)

    for command in ('b7', 'cleanup-after-b7'):
        result = subprocess.run(
            ['bash', str(manifest['script']), command],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 2
        assert 'runner_resume_and_evidence_integrity' in result.stderr
        assert 'ask_first_execution_pending' in result.stderr
        assert 'reviewer_ask' in result.stderr
        assert 'job_e9edbc409b48' in result.stderr

    checkpoint = runner.pending_checkpoint_path(manifest, task_id)
    checkpoint_payload = json.loads(checkpoint.read_text(encoding='utf-8'))
    assert checkpoint_payload['classification'] == 'runner_resume_and_evidence_integrity'
    assert checkpoint_payload['claimable'] is False
    assert checkpoint_payload['resume_command'] == ['bash', str(manifest['script']), 'resume-pending', task_id]
    assert not Path(str(manifest['b7'])).exists()
    assert not Path(str(manifest['rows'])).exists()
    assert not Path(str(manifest['command_log'])).exists()


def test_resume_pending_refuses_nonterminal_job_without_resubmitting(tmp_path: Path) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    task_id = 'phase6b-l1-doc-direct-execution'
    loop_id = 'lp1b2b3a'
    target = f'loop-{loop_id}-code_reviewer-1'
    runner._write_json(
        project / '.ccb' / 'runtime' / 'loops' / loop_id / 'round.pending.json',
        {
            'loop_run_status': 'pending',
            'loop_id': loop_id,
            'task_id': task_id,
            'pending': {
                'stage': 'reviewer_ask',
                'target': target,
                'job_id': 'job_e9edbc409b48',
                'job_status': 'running',
            },
        },
    )
    jobs_path = project / '.ccb' / 'agents' / target / 'jobs.jsonl'
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(
        json.dumps({'job_id': 'job_e9edbc409b48', 'status': 'running'}) + '\n',
        encoding='utf-8',
    )

    result = subprocess.run(
        ['bash', str(manifest['script']), 'resume-pending', task_id],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert 'pending job is not terminal' in result.stderr
    assert 'job_e9edbc409b48' in result.stderr
    assert not Path(str(manifest['command_log'])).exists()


def test_resume_pending_terminal_job_uses_resume_label_without_duplicate_ask(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    task_id = 'phase6b-l1-doc-direct-execution'
    loop_id = 'lp1b2b3a'
    target = f'loop-{loop_id}-code_reviewer-1'
    pending_path = project / '.ccb' / 'runtime' / 'loops' / loop_id / 'round.pending.json'
    stage_path = project / '.ccb' / 'runtime' / 'loops' / loop_id / 'ask_first_stage_state.json'
    runner._write_json(
        pending_path,
        {
            'loop_run_status': 'pending',
            'loop_id': loop_id,
            'task_id': task_id,
            'pending': {
                'stage': 'reviewer_ask',
                'target': target,
                'job_id': 'job_e9edbc409b48',
                'job_status': 'running',
            },
        },
    )
    runner._write_json(
        stage_path,
        {
            'status': 'pending',
            'loop_id': loop_id,
            'task_id': task_id,
            'pending': {
                'stage': 'reviewer_ask',
                'target': target,
                'job_id': 'job_e9edbc409b48',
                'job_status': 'running',
            },
        },
    )
    jobs_path = project / '.ccb' / 'agents' / target / 'jobs.jsonl'
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(
        '\n'.join(
            [
                json.dumps({'job_id': 'job_e9edbc409b48', 'status': 'running'}),
                json.dumps({'job_id': 'job_e9edbc409b48', 'status': 'completed'}),
            ]
        )
        + '\n',
        encoding='utf-8',
    )
    calls = []

    def fake_run_logged(observed_manifest, label_suffix, argv):
        calls.append((label_suffix, argv))
        if label_suffix == f'{task_id}__resume_pending_round':
            pending_path.unlink()
            stage_path.unlink()
            runner._write_json(
                project / '.ccb' / 'runtime' / 'loops' / loop_id / 'round.json',
                {'task_id': task_id, 'loop_id': loop_id, 'round_result': 'pass'},
            )
        elif label_suffix == f'{task_id}__task_show_after_resume':
            _label, stdout_path, _stderr_path = runner.command_output_paths(
                observed_manifest,
                label_suffix,
            )
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_path.write_text(json.dumps({'task': {'task_id': task_id, 'status': 'done'}}), encoding='utf-8')

    monkeypatch.setattr(runner, 'run_logged', fake_run_logged)

    runner.resume_pending_round(manifest, task_id)

    assert calls == [
        (
            f'{task_id}__resume_pending_round',
            [str(PROJECT_ROOT / 'ccb_test'), '--project', str(project), 'loop', 'runner', '--once', '--json'],
        ),
        (
            f'{task_id}__task_show_after_resume',
            [
                str(PROJECT_ROOT / 'ccb_test'),
                '--project',
                str(project),
                'plan',
                'task-show',
                '--task',
                task_id,
                '--json',
            ],
        ),
    ]
    assert all(not label.endswith('__run_direct_execution_round') for label, _argv in calls)


def test_start_task_observes_existing_running_task_without_mutating_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    task_id = 'phase6b-l1-doc-direct-execution'
    calls = []

    monkeypatch.setattr(runner, 'assert_planner_task_set_present', lambda _manifest: {})
    monkeypatch.setattr(runner, 'validate_sequence_task_set_only', lambda _manifest: None)
    monkeypatch.setattr(runner, 'wait_for_planner_task_set_handoff', lambda _manifest, *, before: {})
    monkeypatch.setattr(runner, 'wait_for_auto_runner_quiet', lambda _manifest, *, before: None)
    monkeypatch.setattr(runner, 'task_record_exists', lambda _manifest, _task_id: True)

    def fake_run_logged(observed_manifest, label_suffix, argv):
        calls.append((label_suffix, argv))
        assert label_suffix == f'{task_id}__task_observe_existing'
        _label, stdout_path, _stderr_path = runner.command_output_paths(
            observed_manifest,
            label_suffix,
        )
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(
            json.dumps(
                {
                    'status': 'running',
                    'current_loop': 'lp-sequence21',
                    'task': {
                        'task_id': task_id,
                        'status': 'running',
                        'current_loop': 'lp-sequence21',
                        'artifacts': {
                            'task_packet': {'path': 'task_packet.md'},
                            'execution_contract': {'path': 'execution_contract.md'},
                            'orchestration_notes': {'orchestrator_route': 'direct_execution'},
                        },
                    },
                }
            ),
            encoding='utf-8',
        )

    monkeypatch.setattr(runner, 'run_logged', fake_run_logged)

    runner.start_task(manifest, task_id)

    assert [call[0] for call in calls] == [f'{task_id}__task_observe_existing']
    assert not (Path(str(manifest['project'])) / 'drafts').exists()


def test_start_task_activates_existing_ready_task_without_reimporting_anchors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    task_id = 'phase6b-l2-code-test-direct-execution'
    calls = []

    monkeypatch.setattr(runner, 'assert_planner_task_set_present', lambda _manifest: {})
    monkeypatch.setattr(runner, 'validate_sequence_task_set_only', lambda _manifest: None)
    monkeypatch.setattr(runner, 'wait_for_planner_task_set_handoff', lambda _manifest, *, before: {})
    monkeypatch.setattr(runner, 'wait_for_auto_runner_quiet', lambda _manifest, *, before: None)
    monkeypatch.setattr(runner, 'task_record_exists', lambda _manifest, _task_id: True)

    def fake_run_logged(observed_manifest, label_suffix, argv):
        calls.append((label_suffix, argv))
        if label_suffix == f'{task_id}__task_observe_existing':
            _label, stdout_path, _stderr_path = runner.command_output_paths(
                observed_manifest,
                label_suffix,
            )
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_path.write_text(
                json.dumps(
                    {
                        'task': {
                            'task_id': task_id,
                            'status': 'ready_for_orchestration',
                            'current_loop': None,
                            'artifacts': {
                                'task_packet': {'path': 'task_packet.md'},
                                'execution_contract': {'path': 'execution_contract.md'},
                            },
                        }
                    }
                ),
                encoding='utf-8',
            )
            return
        assert label_suffix == f'{task_id}__activate_orchestrator'
        assert argv[-4:] == ['loop', 'runner', '--once', '--json']

    monkeypatch.setattr(runner, 'run_logged', fake_run_logged)

    runner.start_task(manifest, task_id)

    assert [call[0] for call in calls] == [
        f'{task_id}__task_observe_existing',
        f'{task_id}__task_observe_existing',
        f'{task_id}__activate_orchestrator',
    ]
    assert not any('__artifact_' in call[0] for call in calls)
    assert not any(call[0].endswith('__ready_for_orchestration') for call in calls)


def test_existing_orchestrator_route_allows_continue_without_supervisor_route_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    task_id = 'phase6b-l1-doc-direct-execution'
    calls = []

    def fake_run_logged(observed_manifest, label_suffix, argv):
        calls.append((label_suffix, argv))
        assert label_suffix == f'{task_id}__route_observe_existing'
        _label, stdout_path, _stderr_path = runner.command_output_paths(
            observed_manifest,
            label_suffix,
        )
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(
            json.dumps(
                {
                    'task': {
                        'task_id': task_id,
                        'status': 'running',
                        'artifacts': {
                            'orchestration_notes': {'orchestrator_route': 'direct_execution'}
                        },
                    }
                }
            ),
            encoding='utf-8',
        )

    monkeypatch.setattr(runner, 'run_logged', fake_run_logged)

    runner.import_supervisor_route(manifest, task_id, 'direct_execution')

    assert [call[0] for call in calls] == [f'{task_id}__route_observe_existing']
    assert not (Path(str(manifest['project'])) / 'supervisor_imports' / task_id / 'route.txt').exists()


def test_existing_detail_artifacts_allow_detail_ready_without_supervisor_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    task_id = 'phase6b-l3-needs-detail'
    calls = []

    def fake_run_logged(observed_manifest, label_suffix, argv):
        calls.append((label_suffix, argv))
        if label_suffix == f'{task_id}__detail_observe_existing':
            _label, stdout_path, _stderr_path = runner.command_output_paths(
                observed_manifest,
                label_suffix,
            )
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_path.write_text(
                json.dumps(
                    {
                        'task': {
                            'task_id': task_id,
                            'status': 'ready_for_orchestration',
                            'artifacts': {
                                'detail_design': {'path': 'details/task-detail-design.md'},
                                'detail_summary': {'path': 'details/brief-update-summary.md'},
                                'detail_packet': {'path': 'details/detail-packet.manifest.json'},
                            },
                        }
                    }
                ),
                encoding='utf-8',
            )
            return
        assert label_suffix == f'{task_id}__status_detail_ready'
        assert argv[-5:] == [
            '--status',
            'detail_ready',
            '--activation-reason',
            f"{manifest['label']}_detail_ready",
            '--json',
        ]

    monkeypatch.setattr(runner, 'run_logged', fake_run_logged)

    runner.continue_detail(manifest, task_id)

    assert [call[0] for call in calls] == [
        f'{task_id}__detail_observe_existing',
        f'{task_id}__status_detail_ready',
    ]
    assert not (Path(str(manifest['project'])) / 'supervisor_imports' / task_id).exists()


def test_unexpected_frontdesk_meta_task_writes_invalid_harness_report(tmp_path: Path) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    runner.materialize_plan_root(manifest)
    meta_task_id = 'controller-owned-real-provider-l1-l4-rou-20260707063849'
    runner._write_json(
        project / 'docs' / 'plantree' / 'plans' / runner.PLAN_SLUG / 'tasks' / 'index.json',
        {
            'schema': 'ccb.plan.tasks.v1',
            'tasks': [
                {
                    'task_id': meta_task_id,
                    'status': 'blocked',
                    'next_owner': 'terminal',
                    'authority_trace': {
                        'source_job': {
                            'job_id': 'job_31e0de7cb0fd',
                            'agent_name': 'planner',
                            'terminal_status': 'completed',
                        }
                    },
                }
            ],
        },
    )
    activation_path = (
        project / '.ccb' / 'runtime' / 'loops' / 'activations' / 'act-frontdesk-job_ef437696100b.json'
    )
    runner._write_json(
        activation_path,
        {
            'record_type': 'ccb_loop_frontdesk_planner_activation',
            'request_id': 'job_ef437696100b',
            'source_job': {'job_id': 'job_ef437696100b'},
            'ask': {'target': 'planner', 'job_id': 'job_31e0de7cb0fd'},
            'auto_runner': {'wait_job_id': 'job_31e0de7cb0fd'},
        },
    )
    planner_snapshot_path = project / '.ccb' / 'ccbd' / 'snapshots' / 'job_31e0de7cb0fd.json'
    runner._write_json(
        planner_snapshot_path,
        {'job_id': 'job_31e0de7cb0fd', 'agent_name': 'planner', 'record_type': 'completion_snapshot'},
    )
    planner_reply_path = (
        project
        / '.ccb'
        / 'ccbd'
        / 'artifacts'
        / 'text'
        / 'completion-reply'
        / 'job_31e0de7cb0fd-art_test.txt'
    )
    planner_reply_path.parent.mkdir(parents=True, exist_ok=True)
    planner_reply_path.write_text(
        '**task-set.json**\n```json\n{"tasks":[{"task_id":"phase6b-l1-doc-direct-execution"}]}\n```\n',
        encoding='utf-8',
    )

    with pytest.raises(
        runner.HarnessError,
        match='invalid_harness.*frontdesk_planner_unexpected_meta_task',
    ):
        runner.validate_sequence_task_set_only(manifest)

    rows_path = Path(str(manifest['rows']))
    b7_path = Path(str(manifest['b7']))
    rows = [json.loads(line) for line in rows_path.read_text(encoding='utf-8').splitlines()]

    assert len(rows) == 1
    row = rows[0]
    assert row['case_id'] == 'frontdesk_planner_unexpected_meta_task'
    assert row['classification'] == 'invalid_harness'
    assert row['reason'] == 'frontdesk_planner_unexpected_meta_task'
    assert row['claimable_row'] is False
    assert row['route_mix_rows_claimable'] is False
    assert row['unexpected_plan_tasks'] == [meta_task_id]
    assert row['frontdesk_job_id'] == 'job_ef437696100b'
    assert row['planner_job_id'] == 'job_31e0de7cb0fd'
    assert row['activation_path'] == str(activation_path)
    assert row['planner_snapshot_path'] == str(planner_snapshot_path)
    assert row['planner_reply_path'] == str(planner_reply_path)
    assert row['fenced_task_set_present'] is True
    assert 'route-mix rows were never generated' in row['why_no_route_mix_rows_claimable']
    assert 'Status: invalid_harness' in b7_path.read_text(encoding='utf-8')


def _write_frontdesk_task_set_parent_authority(
    runner,
    manifest: dict[str, object],
    *,
    mutation: str | None = None,
) -> str:
    project = Path(str(manifest['project']))
    source_task_id = 'job_da3510bbfe19'
    activation_id = f'act-frontdesk-{source_task_id}'
    planner_job_id = 'job_31e0de7cb0fd'
    task_set_id = 'ts-017b23211d6230850c98'
    project_id = 'project-root7'
    body = f'CCB_REQ_ID: {source_task_id}\nRoute mix intake\n'
    body_bytes = len(body.encode('utf-8'))
    body_sha256 = hashlib.sha256(body.encode('utf-8')).hexdigest()
    planner_reply = '**task-set.json**\n```json\n{"tasks": []}\n```\n'
    planner_reply_sha256 = hashlib.sha256(planner_reply.encode('utf-8')).hexdigest()
    required_children = [str(case['task_id']) for case in runner.TASKS]
    parent_binding = {
        'schema': 'ccb.plan.task_set_binding.v1',
        'task_set_id': task_set_id,
        'task_set_revision': 1,
        'binding_role': 'parent',
        'bound_task_revision': 2,
    }
    parent = {
        'task_id': source_task_id,
        'task_revision': 2,
        'status': 'decomposed',
        'task_set_parent': parent_binding,
    }
    child_records = []
    task_set_children = []
    import_children = []
    identity_children = []
    for order, task_id in enumerate(required_children):
        binding = {
            'schema': 'ccb.plan.task_set_binding.v1',
            'task_set_id': task_set_id,
            'task_set_revision': 1,
            'binding_role': 'child',
            'bound_task_revision': 3,
            'required': True,
            'order': order,
        }
        child_records.append(
            {
                'task_id': task_id,
                'task_revision': 3,
                'task_set': binding,
            }
        )
        task_set_children.append(
            {'task_id': task_id, 'task_revision': 3, 'required': True, 'order': order}
        )
        import_children.append(
            {'task_id': task_id, 'task_revision': 3, 'task_set': dict(binding)}
        )
        identity_children.append({'task_id': task_id, 'required': True})
    source_request = {
        'status': 'ok',
        'source_job_id': source_task_id,
        'agent_name': 'frontdesk',
        'project_id': project_id,
        'to_agent': 'planner',
        'from_actor': 'frontdesk',
        'message_type': 'ask',
        'text': body,
        'bytes': body_bytes,
        'sha256': body_sha256,
    }
    activation = {
        'schema_version': 1,
        'record_type': 'ccb_loop_frontdesk_planner_activation',
        'activation_id': activation_id,
        'project_id': project_id,
        'project_root': str(project),
        'action': 'activate_planner_from_frontdesk',
        'source': 'frontdesk_direct_silence_ask',
        'plan_slug': runner.PLAN_SLUG,
        'request_id': source_task_id,
        'intake_sha256': body_sha256,
        'source_intake': {
            'sha256': body_sha256,
            'bytes': body_bytes,
            'preview': body.strip()[:400],
        },
        'planner_contract': 'task_set',
        'required_next_output': 'task-set.json',
        'script_write_rules': {'mode': 'reply_only'},
        'expected_task_ids': required_children,
        'source_task_id': source_task_id,
        'source_job': {
            'job_id': source_task_id,
            'agent_name': 'frontdesk',
            'terminal_status': 'forwarded',
            'finished_at': None,
            'reply_sha256': body_sha256,
        },
        'source_request': source_request,
        'direct_ask': {
            'from_actor': 'frontdesk',
            'target': 'planner',
            'silence': True,
            'task_id': activation_id,
            'body_sha256': body_sha256,
            'controller_rewrote_body': False,
        },
        'ask': {'target': 'planner', 'job_id': planner_job_id, 'sender': 'frontdesk'},
    }
    task_set = {
        'schema': 'ccb.plan.task_set.v1',
        'schema_version': 1,
        'task_set_id': task_set_id,
        'task_set_revision': 1,
        'project_id': project_id,
        'plan_slug': runner.PLAN_SLUG,
        'source_task_id': source_task_id,
        'source_request': source_request,
        'planner_job': {'job_id': planner_job_id, 'reply_sha256': planner_reply_sha256},
        'plan_revision': {'revision': 1},
        'children': task_set_children,
        'ordered_required_children': required_children,
        'state': 'running',
        'aggregate_result': None,
        'closure': None,
        'created_at': '2026-07-08T00:00:00+00:00',
        'updated_at': '2026-07-08T00:00:01+00:00',
    }
    request = {
        'project_id': project_id,
        'to_agent': 'planner',
        'from_actor': 'frontdesk',
        'body': body,
        'task_id': activation_id,
        'message_type': 'ask',
    }
    activation_digest = _frontdesk_activation_digest(activation)
    admission_authority = {
        'project_id': project_id,
        'activation_id': activation_id,
        'request_id': source_task_id,
        'plan_slug': runner.PLAN_SLUG,
        'request': request,
        'body_bytes': body_bytes,
        'body_sha256': body_sha256,
        'planner_contract': 'task_set',
        'source_task_id': source_task_id,
        'activation_digest': activation_digest,
    }
    admission = {
        'schema': 'ccb.frontdesk.direct_handoff_admission_transaction.v1',
        'record_type': 'ccb_frontdesk_direct_handoff_admission_transaction',
        'status': 'committed',
        **admission_authority,
        'transaction_digest': _canonical_digest(admission_authority, prefixed=True),
        'activation_record': dict(activation),
    }
    import_identity = {
        'project_id': project_id,
        'plan_slug': runner.PLAN_SLUG,
        'plan_revision': task_set['plan_revision'],
        'activation_id': activation_id,
        'source_task_id': source_task_id,
        'source_request': source_request,
        'source_job': activation['source_job'],
        'planner_job_id': planner_job_id,
        'planner_reply_sha256': planner_reply_sha256,
        'task_set_id': task_set_id,
        'ordered_children': identity_children,
    }
    import_authority = {
        'task_set_id': task_set_id,
        'task_set_revision': 1,
        'task_set': task_set,
        'source_task_id': source_task_id,
        'children': import_children,
    }
    planner_import = {
        'schema': 'ccb.plan.planner_task_set_import_transaction.v1',
        'schema_version': 1,
        'status': 'committed',
        'journal_ref': (
            f'.ccb/runtime/role-output-imports/{planner_job_id}/'
            'planner-task-set-import.transaction.json'
        ),
        'transaction_digest': _canonical_digest(import_identity),
        'identity': import_identity,
        'authority': import_authority,
        'conflicts': [],
    }
    if mutation == 'parent_task_revision':
        parent['task_revision'] = 3
    elif mutation == 'task_set_id':
        parent_binding['task_set_id'] = 'ts-wrong'
    elif mutation == 'revision':
        parent_binding['task_set_revision'] = 2
    elif mutation == 'binding_role':
        parent_binding['binding_role'] = 'child'
    elif mutation == 'status':
        parent['status'] = 'ready_for_orchestration'
    elif mutation == 'source_task_id':
        task_set['source_task_id'] = 'job_other'
    elif mutation == 'planner_contract':
        activation['planner_contract'] = 'legacy_tasks'
    elif mutation == 'to_agent':
        activation['source_request']['to_agent'] = 'orchestrator'
    elif mutation == 'forwarded_flow':
        activation['source_job']['terminal_status'] = 'completed'
    elif mutation == 'admission_schema':
        admission['schema'] = 'ccb.wrong.v1'
    elif mutation == 'admission_status':
        admission['status'] = 'prepared'
    elif mutation == 'admission_activation':
        admission['activation_id'] = 'act-frontdesk-other'
    elif mutation == 'admission_body_digest':
        admission['body_sha256'] = '0' * 64
    elif mutation == 'import_status':
        planner_import['status'] = 'prepared'
    elif mutation == 'import_conflicts':
        planner_import['conflicts'] = [{'reason': 'identity_conflict'}]
    elif mutation == 'import_activation':
        import_identity['activation_id'] = 'act-frontdesk-other'
    elif mutation == 'import_source_task':
        import_identity['source_task_id'] = 'job_other'
    elif mutation == 'import_task_set_id':
        import_identity['task_set_id'] = 'ts-other'
    elif mutation == 'import_revision':
        import_authority['task_set_revision'] = 2
    elif mutation == 'import_reply_digest':
        import_identity['planner_reply_sha256'] = '0' * 64
    elif mutation == 'import_source_digest':
        import_identity['source_request'] = {**source_request, 'sha256': '0' * 64}
    elif mutation == 'task_set_state':
        task_set['state'] = 'closed'
    elif mutation == 'task_set_project':
        task_set['project_id'] = 'other-project'
    elif mutation == 'task_set_plan':
        task_set['plan_slug'] = 'other-plan'
    elif mutation == 'child_revision':
        task_set_children[0]['task_revision'] = 4
    elif mutation == 'child_order':
        task_set_children[0]['order'] = 1
    elif mutation == 'child_binding_revision':
        child_records[0]['task_set']['task_set_revision'] = 2
    elif mutation == 'extra_child':
        task_set_children.append(
            {'task_id': 'extra-child', 'task_revision': 1, 'required': False, 'order': 5}
        )
    runner._write_json(
        project / 'docs' / 'plantree' / 'plans' / runner.PLAN_SLUG / 'tasks' / 'index.json',
        {
            'schema': 'ccb.plan.tasks.v1',
            'tasks': [parent, *child_records, *([dict(parent)] if mutation == 'duplicate_parent' else [])],
        },
    )
    runner._write_json(
        project
        / '.ccb'
        / 'runtime'
        / 'loops'
        / 'activations'
        / f'{activation_id}.json',
        activation,
    )
    if mutation == 'duplicate_activation':
        duplicate = dict(activation)
        duplicate['activation_id'] = f'{activation_id}-duplicate'
        runner._write_json(
            project
            / '.ccb'
            / 'runtime'
            / 'loops'
            / 'activations'
            / f'{activation_id}-duplicate.json',
            duplicate,
        )
    admission_path = (
        project
        / '.ccb'
        / 'runtime'
        / 'loops'
        / 'activations'
        / f'{activation_id}.direct-handoff.transaction.json'
    )
    if mutation == 'bad_admission_json':
        admission_path.parent.mkdir(parents=True, exist_ok=True)
        admission_path.write_text('{', encoding='utf-8')
    else:
        runner._write_json(admission_path, admission)
    runner._write_json(
        project
        / 'docs'
        / 'plantree'
        / 'plans'
        / runner.PLAN_SLUG
        / 'task-sets'
        / task_set_id
        / 'task-set.json',
        task_set,
    )
    reply_path = (
        project
        / '.ccb'
        / 'ccbd'
        / 'artifacts'
        / 'text'
        / 'completion-reply'
        / f'{planner_job_id}-art_root7.txt'
    )
    reply_path.parent.mkdir(parents=True, exist_ok=True)
    reply_path.write_text(planner_reply, encoding='utf-8')
    import_path = (
        project
        / '.ccb'
        / 'runtime'
        / 'role-output-imports'
        / planner_job_id
        / 'planner-task-set-import.transaction.json'
    )
    if mutation == 'bad_import_json':
        import_path.parent.mkdir(parents=True, exist_ok=True)
        import_path.write_text('{', encoding='utf-8')
    else:
        runner._write_json(import_path, planner_import)
    if mutation == 'duplicate_import':
        runner._write_json(
            project
            / '.ccb'
            / 'runtime'
            / 'role-output-imports'
            / 'job_duplicate'
            / 'planner-task-set-import.transaction.json',
            planner_import,
        )
    return source_task_id


def test_authoritative_frontdesk_task_set_source_parent_is_not_unexpected(tmp_path: Path) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    source_task_id = _write_frontdesk_task_set_parent_authority(runner, manifest)

    assert runner.unexpected_plan_task_ids(manifest) == []
    runner.validate_sequence_task_set_only(manifest)
    assert source_task_id not in runner.unexpected_plan_task_ids(manifest)


@pytest.mark.parametrize(
    'mutation',
    (
        'parent_task_revision', 'task_set_id', 'revision', 'binding_role', 'status',
        'source_task_id', 'planner_contract', 'to_agent', 'forwarded_flow',
        'admission_schema', 'admission_status', 'admission_activation',
        'admission_body_digest', 'bad_admission_json', 'import_status',
        'import_conflicts', 'import_activation', 'import_source_task',
        'import_task_set_id', 'import_revision', 'import_reply_digest',
        'import_source_digest', 'bad_import_json', 'duplicate_activation',
        'duplicate_parent', 'duplicate_import', 'task_set_state', 'task_set_project',
        'task_set_plan', 'child_revision', 'child_order', 'child_binding_revision',
        'extra_child',
    ),
)
def test_frontdesk_task_set_source_parent_requires_exact_authority(
    tmp_path: Path,
    mutation: str,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    source_task_id = _write_frontdesk_task_set_parent_authority(
        runner,
        manifest,
        mutation=mutation,
    )

    assert runner.unexpected_plan_task_ids(manifest) == [source_task_id]


def test_planner_route_equivalent_task_ids_are_aliases_not_meta_tasks(tmp_path: Path) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    runner.materialize_plan_root(manifest)
    records = []
    for task_id, route in (
        ('phase6b-l1-doc-direct-execution', 'direct_execution'),
        ('phase6b-l2-code-test-direct-execution', 'direct_execution'),
        ('phase6b-l3-needs-detail-detail-ready', 'needs_detail'),
        ('phase6b-l4-macro-adjustment-replan-required', 'macro_adjustment_request'),
        ('phase6b-l4-blocked-prerequisite', 'blocked'),
    ):
        task_root = project / 'docs' / 'plantree' / 'plans' / runner.PLAN_SLUG / 'tasks' / task_id
        task_root.mkdir(parents=True, exist_ok=True)
        (task_root / 'task_packet.md').write_text(f'# Task\nRoute: {route}\n', encoding='utf-8')
        records.append(
            {
                'task_id': task_id,
                'status': 'ready_for_orchestration',
                'next_owner': 'orchestrator',
                'task_root': str(task_root.relative_to(project)),
                'artifacts': {
                    'task_packet': {
                        'path': str((task_root / 'task_packet.md').relative_to(project)),
                    }
                },
            }
        )
    runner._write_json(
        project / 'docs' / 'plantree' / 'plans' / runner.PLAN_SLUG / 'tasks' / 'index.json',
        {'schema': 'ccb.plan.tasks.v1', 'tasks': records},
    )

    assert runner.sequence_task_aliases(manifest) == {
        'phase6b-l3-needs-detail': 'phase6b-l3-needs-detail-detail-ready',
        'phase6b-l4-macro-adjustment-request': 'phase6b-l4-macro-adjustment-replan-required',
    }
    assert runner.unexpected_plan_task_ids(manifest) == []
    runner.validate_sequence_task_set_only(manifest)


def test_missing_fenced_task_set_blocks_with_explicit_row(tmp_path: Path) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    runner.materialize_plan_root(manifest)
    planner_reply_path = (
        project
        / '.ccb'
        / 'ccbd'
        / 'artifacts'
        / 'text'
        / 'completion-reply'
        / 'job_31e0de7cb0fd-art_test.txt'
    )
    planner_reply_path.parent.mkdir(parents=True, exist_ok=True)
    planner_reply_path.write_text(
        '**task-packet.md**\n```markdown\n# Task\nroute: direct_execution\n```\n',
        encoding='utf-8',
    )
    runner._write_json(
        project / '.ccb' / 'runtime' / 'loops' / 'activations' / 'act-frontdesk-job_ef437696100b.json',
        {
            'record_type': 'ccb_loop_frontdesk_planner_activation',
            'request_id': 'job_ef437696100b',
            'source_job': {'job_id': 'job_ef437696100b'},
            'ask': {'target': 'planner', 'job_id': 'job_31e0de7cb0fd'},
            'auto_runner': {'wait_job_id': 'job_31e0de7cb0fd'},
        },
    )
    runner._write_json(
        project / '.ccb' / 'ccbd' / 'snapshots' / 'job_31e0de7cb0fd.json',
        {'job_id': 'job_31e0de7cb0fd', 'agent_name': 'planner', 'record_type': 'completion_snapshot'},
    )

    with pytest.raises(
        runner.HarnessBlocker,
        match='invalid_harness.*frontdesk_planner_missing_fenced_task_set',
    ):
        runner.assert_planner_task_set_present(manifest)

    rows_path = Path(str(manifest['rows']))
    row = json.loads(rows_path.read_text(encoding='utf-8').strip())
    assert row['case_id'] == 'frontdesk_planner_missing_fenced_task_set'
    assert row['classification'] == 'invalid_harness'
    assert row['planner_reply_path'] == str(planner_reply_path)
    assert row['fenced_task_set_present'] is False
    assert 'fenced task-set.json block' in row['why_no_route_mix_rows_claimable']


def test_b7_uses_script_owned_task_index_routes_and_status_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    _root, manifest = _materialize(tmp_path)
    project = Path(str(manifest['project']))
    tasks_root = project / 'docs' / 'plantree' / 'plans' / runner.PLAN_SLUG / 'tasks'
    records = []

    monkeypatch.setattr(runner, 'assert_no_pending_authority', lambda _manifest: None)
    monkeypatch.setattr(runner, 'planner_task_set_evidence', lambda _manifest: {'fenced_task_set_present': True})
    monkeypatch.setattr(runner, 'unexpected_plan_task_ids', lambda _manifest: [])
    monkeypatch.setattr(runner, 'sequence_task_aliases', lambda _manifest: {})

    for index, case in enumerate(runner.TASKS, start=1):
        task_id = case['task_id']
        task_dir = tasks_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        notes_path = task_dir / 'orchestration_notes.md'
        notes_path.write_text(f"route: {case['expected_route']}\n", encoding='utf-8')
        artifacts = {
            'orchestration_notes': {
                'path': str(notes_path.relative_to(project)),
                'orchestrator_route': case['expected_route'],
                'actor': {'source': 'loop_runner_role_output_import'},
            }
        }
        if case['expected_route'] == 'direct_execution':
            loop_id = f'lp-test-{index}'
            round_dir = project / '.ccb' / 'runtime' / 'loops' / loop_id
            runner._write_json(
                round_dir / 'round.json',
                {
                    'task_id': task_id,
                    'loop_id': loop_id,
                    'round_result': 'pass',
                    'round_result_source': 'round_reviewer_reply',
                    'release': {'released_count': 2, 'retained_count': 0},
                    'observed_topology': {'agents': []},
                },
            )
            round_summary = task_dir / 'round_summary.md'
            round_summary.write_text(
                'round_result: pass\nround_result_source: round_reviewer_reply\n',
                encoding='utf-8',
            )
            artifacts['round_summary'] = {
                'path': str(round_summary.relative_to(project)),
                'actor': {'source': 'loop_runner'},
                'loop_id': loop_id,
                'round_result': 'pass',
            }
        elif case['expected_route'] == 'needs_detail':
            detail_packet = task_dir / 'details' / 'detail-packet.manifest.json'
            detail_packet.parent.mkdir(parents=True, exist_ok=True)
            detail_packet.write_text('{}\n', encoding='utf-8')
            artifacts['detail_packet'] = {
                'path': str(detail_packet.relative_to(project)),
                'actor': {'source': 'loop_runner_role_output_import'},
            }
        elif case['expected_route'] == 'macro_adjustment_request':
            macro = task_dir / 'details' / 'macro-adjustment-request.json'
            macro.parent.mkdir(parents=True, exist_ok=True)
            macro.write_text('{}\n', encoding='utf-8')
            artifacts['macro_adjustment_request'] = {
                'path': str(macro.relative_to(project)),
                'actor': {'source': 'loop_runner/script-owned'},
            }
        elif case['expected_route'] == 'blocked':
            blocker = task_dir / 'blocker-evidence.md'
            blocker.write_text('blocked\n', encoding='utf-8')
            artifacts['blocker_evidence'] = {
                'path': str(blocker.relative_to(project)),
                'actor': {'source': 'loop_runner/script-owned'},
            }
        records.append(
            {
                'task_id': task_id,
                'status': case['expected_final_status'],
                'next_owner': 'terminal' if case['expected_final_status'] in {'done', 'blocked'} else 'planner',
                'artifacts': artifacts,
            }
        )

    runner._write_json(tasks_root / 'index.json', {'schema': 'ccb.plan.tasks.v1', 'tasks': records})

    runner.write_b7_report(manifest)

    rows = [
        json.loads(line)
        for line in Path(str(manifest['rows'])).read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    assert len(rows) == len(runner.TASKS)
    assert all(row['claimable_row'] for row in rows)
    assert all(row['route_decision_correct'] for row in rows)
    assert all(row['script_owned_route_imports'] for row in rows)
    assert all(row['script_owned_round_imports'] for row in rows)
    assert all(row['provider_reply_authority_parsing_absent'] for row in rows)
    assert all(row['runtime_residue'] is False for row in rows)
    assert all(row['dynamic_unload_ok'] is True for row in rows)
    assert [row['observed_route'] for row in rows] == [case['expected_route'] for case in runner.TASKS]
    assert [row['round_result'] for row in rows] == [case['expected_round_result'] for case in runner.TASKS]
    assert rows[0]['cleanup_result'] == 'clean'
    assert rows[1]['cleanup_result'] == 'clean'
    assert rows[0]['release_released_count'] == 2
    assert rows[0]['release_retained_count'] == 0
    assert rows[2]['cleanup_result'] == 'not_applicable'
    assert 'Status: pass' in Path(str(manifest['b7'])).read_text(encoding='utf-8')


def test_manifest_paths_are_inspectable_and_internally_consistent(tmp_path: Path) -> None:
    runner = _load_runner()
    root, manifest = _materialize(tmp_path)

    path_fields = ('project', 'script', 'manifest', 'b7', 'rows', 'command_log', 'role_store')
    for field in path_fields:
        path = Path(str(manifest[field]))
        assert path.is_absolute()
        assert path == root or root in path.parents

    assert Path(str(manifest['project'])) == root / str(manifest['project_name'])
    assert manifest['provider_environment_policy']['inherits_HOME'] is True
    assert manifest['provider_environment_policy']['inherits_CCB_SOURCE_HOME'] is True
    assert manifest['provider_environment_policy']['exports_HOME'] is False
    assert manifest['provider_environment_policy']['exports_CCB_SOURCE_HOME'] is False
    assert manifest['provider_environment_policy']['sets_CCB_SOURCE_RUNTIME_OK'] is False
    assert manifest['provider_environment_policy']['sets_AGENT_ROLES_STORE'] == str(root / 'roles')
    assert manifest['role_install_command_template'] == [
        str(PROJECT_ROOT / 'ccb_test'),
        'roles',
        'install',
        '<role_id>',
        '--skip-tools',
    ]
    assert manifest['controller_owned_authority']['b7'] == manifest['b7']
    assert manifest['controller_owned_authority']['rows'] == manifest['rows']
    assert manifest['resident_agent_targets'] == [
        'frontdesk',
        'planner',
        'orchestrator',
        'task_detailer',
        'ccb_round_reviewer',
    ]
    assert manifest['resident_ask_targets'] == [
        'frontdesk',
        'planner',
        'orchestrator',
        'task_detailer',
        'ccb_round_reviewer',
    ]
    assert manifest['resident_agent_specs'] == {
        target: str(root / 'l1-l4-frontdesk-real-provider-lab' / '.ccb' / 'agents' / target / 'agent.json')
        for target in manifest['resident_ask_targets']
    }
    assert manifest['dynamic_loop_profiles'] == ['coder', 'code_reviewer']

    broken_manifest = dict(manifest)
    broken_manifest['resident_agent_targets'] = ['planner', 'orchestrator']
    broken_manifest['resident_ask_targets'] = ['planner', 'orchestrator']
    with pytest.raises(runner.HarnessError, match='manifest missing resident agent target'):
        runner.validate_manifest(broken_manifest)
