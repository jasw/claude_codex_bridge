from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from cli.context import CliContextBuilder
from cli.models import ParsedMaintenanceCommand
import cli.services.maintenance as maintenance_service
from cli.phase2 import maybe_handle_phase2
from cli.parser import CliParser
from cli.render import render_maintenance
from cli.services.maintenance import maintenance_status
from maintenance_heartbeat import (
    MaintenanceHeartbeatActivation,
    MaintenanceHeartbeatLock,
    MaintenanceHeartbeatRunner,
    MaintenanceHeartbeatSchedule,
    MaintenanceHeartbeatStatus,
    MaintenanceHeartbeatStore,
)
from storage.paths import PathLayout

NOW = '2026-06-10T12:00:00Z'


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _enabled_config(agent: str = 'demo') -> str:
    return f"""{agent}:codex

[maintenance.heartbeat]
enabled = true
assessor = "{agent}"
interval_s = 900
min_interval_s = 90
unknown_streak_cap = 3
escalation_policy = "report_only"
startup_ensure = true
"""


class _ProjectViewClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def project_view(self, *, schema_version: int) -> dict:
        assert schema_version == 1
        return self.payload


class _SubmitClient:
    def __init__(self, seen: dict[str, object]) -> None:
        self.seen = seen

    def submit(self, request):
        self.seen['request'] = request
        return {
            'job_id': 'job_self_activation',
            'agent_name': request.to_agent,
            'status': 'queued',
        }


def _patch_project_view(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(
        maintenance_service,
        'connect_mounted_daemon',
        lambda context, *, allow_restart_stale: SimpleNamespace(client=_ProjectViewClient(payload)),
    )


def _patch_submit(monkeypatch, seen: dict[str, object]) -> None:
    monkeypatch.setattr(
        maintenance_service,
        'invoke_mounted_daemon',
        lambda context, *, allow_restart_stale, request_fn: request_fn(_SubmitClient(seen)),
    )


def _project_view_payload(*, agent_state: str = 'idle', agent_reason: str = 'pane_alive', comms=()) -> dict:
    return {
        'view': {
            'ccbd': {'state': 'mounted', 'health': 'healthy', 'generation': 1},
            'agents': [
                {
                    'name': 'demo',
                    'activity_state': agent_state,
                    'activity_reason': agent_reason,
                    'activity_source': 'pane_liveness',
                    'queue_depth': 0,
                }
            ],
            'comms': list(comms),
        },
        'cache': {'generated_at': NOW},
    }


def test_maintenance_heartbeat_paths_use_dedicated_namespace(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / 'repo')

    assert layout.ccbd_maintenance_heartbeat_dir == layout.ccbd_dir / 'maintenance-heartbeat'
    assert layout.ccbd_maintenance_heartbeat_schedule_path.name == 'schedule.json'
    assert layout.ccbd_maintenance_heartbeat_status_path.name == 'status.json'
    assert layout.ccbd_maintenance_heartbeat_runner_path.name == 'runner.json'
    assert layout.ccbd_maintenance_heartbeat_lock_path.name == 'lock.json'
    assert layout.ccbd_maintenance_heartbeat_activations_path.name == 'activations.jsonl'
    assert '/heartbeats/' not in str(layout.ccbd_maintenance_heartbeat_schedule_path)


def test_maintenance_heartbeat_store_round_trips_and_reports_missing(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / 'repo-store')
    store = MaintenanceHeartbeatStore(layout, project_id=layout.project_id)

    assert store.load_schedule().state == 'missing'
    assert store.load_status().state == 'missing'
    assert store.load_runner().state == 'missing'

    store.save_schedule(
        MaintenanceHeartbeatSchedule(
            project_id=layout.project_id,
            next_run_at='2026-06-10T12:00:00Z',
            reason='manual_test',
            updated_at='2026-06-10T11:00:00Z',
            updated_by='test',
        )
    )
    store.save_status(
        MaintenanceHeartbeatStatus(
            project_id=layout.project_id,
            last_tick_status='idle',
            last_tick_at='2026-06-10T11:00:00Z',
            last_ok_at='2026-06-10T11:00:00Z',
            unknown_streak=0,
            updated_at='2026-06-10T11:00:00Z',
        )
    )
    store.save_runner(
        MaintenanceHeartbeatRunner(
            project_id=layout.project_id,
            runner_id='runner_1',
            pid=123,
            state='running',
            source='test',
            started_at='2026-06-10T11:00:00Z',
            last_seen_at='2026-06-10T11:00:01Z',
        )
    )
    store.append_activation(
        MaintenanceHeartbeatActivation(
            project_id=layout.project_id,
            activation_id='act_1',
            status='submitted',
            condition_kind='heartbeat_state_check',
            trigger_kind='state_check',
            source='project_view',
            observed_at='2026-06-10T11:00:00Z',
            target_agent='demo',
            delivery_mode='ask_silence',
            payload_kind='maintenance_diagnostic',
            dedup_key='maintenance:test',
            reason='provider_prompt_idle',
            job_id='job_1',
            submitted_at='2026-06-10T11:00:00Z',
        )
    )

    schedule = store.load_schedule()
    status = store.load_status()
    runner = store.load_runner()

    assert schedule.state == 'ok'
    assert schedule.value is not None
    assert schedule.value.next_run_at == '2026-06-10T12:00:00Z'
    assert status.state == 'ok'
    assert status.value is not None
    assert status.value.last_tick_status == 'idle'
    assert runner.state == 'ok'
    assert runner.value is not None
    assert runner.value.runner_id == 'runner_1'
    assert runner.value.pid == 123
    activations = store.load_activation_tail(5)
    assert len(activations) == 1
    assert activations[0].job_id == 'job_1'


def test_maintenance_heartbeat_store_reports_corrupt_files(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / 'repo-corrupt')
    _write(layout.ccbd_maintenance_heartbeat_schedule_path, '{not json}\n')
    store = MaintenanceHeartbeatStore(layout, project_id=layout.project_id)

    result = store.load_schedule()

    assert result.state == 'corrupt'
    assert result.value is None
    assert result.error


def test_maintenance_parser_accepts_status_and_reserves_mutating_actions() -> None:
    parser = CliParser()

    assert parser.parse(['maintenance']) == ParsedMaintenanceCommand(project=None, action='status')
    assert parser.parse(['maintenance', 'status']) == ParsedMaintenanceCommand(project=None, action='status')
    assert parser.parse(['maintenance', 'schedule', '--after', '5m']) == ParsedMaintenanceCommand(
        project=None,
        action='schedule',
        args=('--after', '5m'),
    )
    assert parser.parse(['maintenance', 'runner', '--max-iterations', '1']) == ParsedMaintenanceCommand(
        project=None,
        action='runner',
        args=('--max-iterations', '1'),
    )


def test_maintenance_status_reads_config_and_missing_state(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-cli-status'
    _write(
        project_root / '.ccb' / 'ccb.config',
        """demo:codex

[maintenance.heartbeat]
enabled = true
assessor = "demo"
interval_s = 900
min_interval_s = 90
unknown_streak_cap = 3
escalation_policy = "report_only"
startup_ensure = true
""",
    )
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='status'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )

    payload = maintenance_status(context, ParsedMaintenanceCommand(project=None, action='status'))

    assert payload['maintenance_status'] == 'ok'
    assert payload['enabled'] is True
    assert payload['assessor'] == 'demo'
    assert payload['assessor_present'] is True
    assert payload['schedule']['state'] == 'missing'
    assert payload['last_status']['state'] == 'missing'
    assert payload['runner']['state'] == 'missing'


def test_phase2_maintenance_status_outputs_read_only_status(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-phase2-status'
    _write(project_root / '.ccb' / 'ccb.config', 'demo:codex\n')
    stdout = StringIO()
    stderr = StringIO()

    code = maybe_handle_phase2(['maintenance', 'status'], cwd=project_root, stdout=stdout, stderr=stderr)

    assert code == 0
    assert 'maintenance_status: ok' in stdout.getvalue()
    assert 'heartbeat_enabled: false' in stdout.getvalue()
    assert 'schedule_state: missing' in stdout.getvalue()
    assert stderr.getvalue() == ''


def test_maintenance_tick_disabled_does_not_write_status_or_schedule(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance_service, 'utc_now', lambda: NOW)
    project_root = tmp_path / 'repo-tick-disabled'
    _write(project_root / '.ccb' / 'ccb.config', 'demo:codex\n')
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='tick'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )

    payload = maintenance_status(context, ParsedMaintenanceCommand(project=None, action='tick'))

    assert payload['maintenance_status'] == 'ok'
    assert payload['tick_status'] == 'disabled'
    assert payload['status_written'] is False
    assert payload['schedule_written'] is False
    assert not context.paths.ccbd_maintenance_heartbeat_status_path.exists()
    assert not context.paths.ccbd_maintenance_heartbeat_schedule_path.exists()


def test_maintenance_tick_healthy_project_view_writes_status_and_schedule(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance_service, 'utc_now', lambda: NOW)
    _patch_project_view(monkeypatch, _project_view_payload())
    project_root = tmp_path / 'repo-tick-healthy'
    _write(project_root / '.ccb' / 'ccb.config', _enabled_config())
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='tick'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )

    payload = maintenance_status(context, ParsedMaintenanceCommand(project=None, action='tick'))
    store = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id)
    status = store.load_status()
    schedule = store.load_schedule()

    assert payload['tick_status'] == 'healthy'
    assert payload['tick_recommended_action'] == 'none'
    assert payload['tick_activation_status'] is None
    assert payload['tick_next_heartbeat_after_s'] == 900
    assert status.state == 'ok'
    assert status.value is not None
    assert status.value.last_tick_status == 'healthy'
    assert status.value.last_ok_at == NOW
    assert status.value.source_kind == 'project_view'
    assert status.value.summary['agent_count'] == 1
    assert schedule.state == 'ok'
    assert schedule.value is not None
    assert schedule.value.next_run_at == '2026-06-10T12:15:00Z'
    assert schedule.value.reason == 'healthy_tick'


def test_maintenance_tick_concern_shortens_next_schedule(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance_service, 'utc_now', lambda: NOW)
    seen: dict[str, object] = {}
    _patch_submit(monkeypatch, seen)
    _patch_project_view(
        monkeypatch,
        _project_view_payload(agent_state='pending', agent_reason='provider_prompt_idle'),
    )
    project_root = tmp_path / 'repo-tick-concern'
    _write(project_root / '.ccb' / 'ccb.config', _enabled_config())
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='tick'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )

    payload = maintenance_status(context, ParsedMaintenanceCommand(project=None, action='tick'))
    store = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id)
    status = store.load_status().value
    schedule = store.load_schedule().value

    assert payload['tick_status'] == 'concern'
    assert payload['tick_recommended_action'] == 'assess_later'
    assert payload['tick_activation_status'] == 'submitted'
    assert payload['tick_activation_job_id'] == 'job_self_activation'
    assert payload['tick_evidence'][0]['reason'] == 'provider_prompt_idle'
    request = seen['request']
    assert request.from_actor == 'maintenance-heartbeat'
    assert request.to_agent == 'demo'
    assert request.silence_on_success is True
    assert status is not None
    assert status.last_tick_status == 'concern'
    assert status.last_error == 'provider_prompt_idle'
    assert status.next_heartbeat_after_s == 90
    assert status.last_activation_status == 'submitted'
    assert status.last_activation_job_id == 'job_self_activation'
    assert schedule is not None
    assert schedule.next_run_at == '2026-06-10T12:01:30Z'
    assert schedule.reason == 'concern_tick'


def test_maintenance_tick_falls_back_to_local_ps_when_project_view_unavailable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance_service, 'utc_now', lambda: NOW)
    monkeypatch.setattr(
        maintenance_service,
        'connect_mounted_daemon',
        lambda context, *, allow_restart_stale: (_ for _ in ()).throw(RuntimeError('ccbd unavailable')),
    )
    project_root = tmp_path / 'repo-tick-fallback'
    _write(project_root / '.ccb' / 'ccb.config', _enabled_config())
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='tick'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )
    store = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id)
    store.save_status(
        MaintenanceHeartbeatStatus(
            project_id=context.project.project_id,
            last_tick_status='unknown',
            unknown_streak=2,
        )
    )

    payload = maintenance_status(context, ParsedMaintenanceCommand(project=None, action='tick', args=('--no-dispatch',)))
    status = store.load_status().value

    assert payload['tick_status'] == 'unknown'
    assert payload['tick_source_kind'] == 'local_ps'
    assert payload['tick_activation_status'] == 'suppressed'
    assert status is not None
    assert status.unknown_streak == 3
    assert status.next_heartbeat_after_s == 900
    assert status.needs_user is True
    assert status.summary['fallback_error'] == 'ccbd unavailable'


def test_maintenance_schedule_persists_followup_and_enforces_min_interval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance_service, 'utc_now', lambda: NOW)
    project_root = tmp_path / 'repo-schedule-followup'
    _write(project_root / '.ccb' / 'ccb.config', _enabled_config())
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='schedule'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )

    payload = maintenance_status(
        context,
        ParsedMaintenanceCommand(project=None, action='schedule', args=('--after', '10s', '--reason', 'self_followup')),
    )
    schedule = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id).load_schedule().value

    assert payload['maintenance_status'] == 'ok'
    assert payload['schedule_status'] == 'scheduled'
    assert payload['requested_after_s'] == 10
    assert payload['scheduled_after_s'] == 90
    assert schedule is not None
    assert schedule.next_run_at == '2026-06-10T12:01:30Z'
    assert schedule.reason == 'self_followup'


def test_maintenance_runner_due_tick_materializes_status_and_schedule(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance_service, 'utc_now', lambda: NOW)
    _patch_project_view(monkeypatch, _project_view_payload())
    project_root = tmp_path / 'repo-runner-due'
    _write(project_root / '.ccb' / 'ccb.config', _enabled_config())
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='runner'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )

    payload = maintenance_status(
        context,
        ParsedMaintenanceCommand(
            project=None,
            action='runner',
            args=('--runner-id', 'runner_test', '--max-iterations', '1', '--no-dispatch'),
        ),
    )
    store = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id)
    status = store.load_status().value
    schedule = store.load_schedule().value
    runner = store.load_runner().value

    assert payload['maintenance_status'] == 'ok'
    assert payload['runner_status'] == 'stopped'
    assert payload['runner_exit_reason'] == 'max_iterations'
    assert payload['runner_iterations'] == 1
    assert status is not None
    assert status.last_tick_status == 'healthy'
    assert schedule is not None
    assert schedule.next_run_at == '2026-06-10T12:15:00Z'
    assert runner is not None
    assert runner.runner_id == 'runner_test'
    assert runner.state == 'stopped'
    assert runner.last_tick_status == 'healthy'
    assert runner.exit_reason == 'max_iterations'


def test_maintenance_runner_future_schedule_waits_without_tick(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance_service, 'utc_now', lambda: NOW)
    _patch_project_view(monkeypatch, _project_view_payload(agent_state='pending', agent_reason='provider_prompt_idle'))
    project_root = tmp_path / 'repo-runner-future'
    _write(project_root / '.ccb' / 'ccb.config', _enabled_config())
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='runner'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )
    store = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id)
    store.save_schedule(
        MaintenanceHeartbeatSchedule(
            project_id=context.project.project_id,
            next_run_at='2026-06-10T12:10:00Z',
            reason='future',
            updated_at=NOW,
            updated_by='test',
        )
    )

    payload = maintenance_status(
        context,
        ParsedMaintenanceCommand(
            project=None,
            action='runner',
            args=('--runner-id', 'runner_wait', '--max-iterations', '1', '--sleep-cap', '1s', '--no-dispatch'),
        ),
    )
    runner = store.load_runner().value

    assert payload['runner_exit_reason'] == 'max_iterations'
    assert store.load_status().state == 'missing'
    assert runner is not None
    assert runner.state == 'stopped'
    assert runner.observed_next_run_at == '2026-06-10T12:10:00Z'
    assert runner.last_tick_status is None


def test_maintenance_tick_exits_when_schedule_is_not_due(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance_service, 'utc_now', lambda: NOW)
    _patch_project_view(monkeypatch, _project_view_payload(agent_state='pending', agent_reason='provider_prompt_idle'))
    project_root = tmp_path / 'repo-tick-too-early'
    _write(project_root / '.ccb' / 'ccb.config', _enabled_config())
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='tick'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )
    store = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id)
    store.save_schedule(
        MaintenanceHeartbeatSchedule(
            project_id=context.project.project_id,
            next_run_at='2026-06-10T12:10:00Z',
            reason='future',
            updated_at=NOW,
            updated_by='test',
        )
    )

    payload = maintenance_status(context, ParsedMaintenanceCommand(project=None, action='tick'))

    assert payload['tick_status'] == 'too_early'
    assert payload['status_written'] is False
    assert payload['activation_written'] is False
    assert store.load_status().state == 'missing'


def test_maintenance_tick_force_no_dispatch_bypasses_schedule_without_submit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance_service, 'utc_now', lambda: NOW)
    seen: dict[str, object] = {}
    _patch_submit(monkeypatch, seen)
    _patch_project_view(monkeypatch, _project_view_payload(agent_state='pending', agent_reason='provider_prompt_idle'))
    project_root = tmp_path / 'repo-tick-force-no-dispatch'
    _write(project_root / '.ccb' / 'ccb.config', _enabled_config())
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='tick'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )
    store = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id)
    store.save_schedule(
        MaintenanceHeartbeatSchedule(
            project_id=context.project.project_id,
            next_run_at='2026-06-10T12:10:00Z',
            reason='future',
            updated_at=NOW,
            updated_by='test',
        )
    )

    payload = maintenance_status(
        context,
        ParsedMaintenanceCommand(project=None, action='tick', args=('--force', '--no-dispatch')),
    )
    activations = store.load_activation_tail(10)

    assert payload['tick_status'] == 'concern'
    assert payload['tick_activation_status'] == 'suppressed'
    assert payload['tick_activation_job_id'] is None
    assert payload['activation_written'] is True
    assert activations[-1].suppressed_reason == 'dispatch_disabled'
    assert seen == {}


def test_maintenance_tick_suppresses_recent_duplicate_activation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance_service, 'utc_now', lambda: NOW)
    seen: dict[str, object] = {}
    _patch_submit(monkeypatch, seen)
    _patch_project_view(
        monkeypatch,
        _project_view_payload(agent_state='pending', agent_reason='provider_prompt_idle'),
    )
    project_root = tmp_path / 'repo-tick-duplicate'
    _write(project_root / '.ccb' / 'ccb.config', _enabled_config())
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='tick'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )

    first = maintenance_status(context, ParsedMaintenanceCommand(project=None, action='tick'))
    second = maintenance_status(context, ParsedMaintenanceCommand(project=None, action='tick', args=('--force',)))
    activations = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id).load_activation_tail(10)

    assert first['tick_activation_status'] == 'submitted'
    assert second['tick_activation_status'] == 'suppressed'
    assert activations[-1].suppressed_reason.startswith('recent_duplicate:')


def test_maintenance_tick_reports_lock_busy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance_service, 'utc_now', lambda: NOW)
    project_root = tmp_path / 'repo-tick-locked'
    _write(project_root / '.ccb' / 'ccb.config', _enabled_config())
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='tick'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )

    with MaintenanceHeartbeatLock(
        context.paths.ccbd_maintenance_heartbeat_lock_path,
        payload={
            'schema_version': 1,
            'record_type': 'maintenance_heartbeat_lock',
            'project_id': context.project.project_id,
            'pid': 123,
            'action': 'test',
            'started_at': NOW,
        },
    ):
        payload = maintenance_status(context, ParsedMaintenanceCommand(project=None, action='tick'))

    assert payload['tick_status'] == 'locked'
    assert payload['status_written'] is False


def test_phase2_maintenance_enable_disable_are_config_authority(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-phase2-reserved'
    _write(project_root / '.ccb' / 'ccb.config', 'demo:codex\n')
    stdout = StringIO()
    stderr = StringIO()

    code = maybe_handle_phase2(['maintenance', 'enable'], cwd=project_root, stdout=stdout, stderr=stderr)

    assert code == 2
    assert 'maintenance_status: not_implemented' in stdout.getvalue()
    assert 'action: enable' in stdout.getvalue()
    assert 'config-authority' in stdout.getvalue()
    assert stderr.getvalue() == ''
    assert not (project_root / '.ccb' / 'ccbd' / 'maintenance-heartbeat').exists()


def test_maintenance_status_reports_corrupt_state_as_degraded(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-cli-corrupt'
    _write(project_root / '.ccb' / 'ccb.config', 'demo:codex\n')
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='status'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )
    _write(context.paths.ccbd_maintenance_heartbeat_status_path, '{broken}\n')

    payload = maintenance_status(context, ParsedMaintenanceCommand(project=None, action='status'))

    assert payload['maintenance_status'] == 'degraded'
    assert payload['last_status']['state'] == 'corrupt'
    assert payload['last_status']['error']


def test_maintenance_status_rejects_reserved_mutating_actions(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-not-implemented'
    _write(project_root / '.ccb' / 'ccb.config', 'demo:codex\n')
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='status'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )

    payload = maintenance_status(context, ParsedMaintenanceCommand(project=None, action='enable'))

    assert payload == {
        'maintenance_status': 'not_implemented',
        'action': 'enable',
        'reason': 'heartbeat enablement is config-authority in v1; edit [maintenance.heartbeat].enabled',
    }


def test_startup_ensure_skips_builtin_default_heartbeat(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('HOME', str(tmp_path / 'empty-home'))
    project_root = tmp_path / 'repo-default-disabled'
    (project_root / '.ccb').mkdir(parents=True)
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='status'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )

    def _spawn(*_args, **_kwargs):
        raise AssertionError('disabled maintenance heartbeat must not start a runner')

    monkeypatch.setattr(maintenance_service, '_spawn_maintenance_runner', _spawn)

    payload = maintenance_service.startup_ensure_maintenance_heartbeat(context)

    assert payload is None


def test_startup_ensure_starts_schedule_consumer_runner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance_service, 'utc_now', lambda: NOW)
    project_root = tmp_path / 'repo-startup-runner'
    _write(project_root / '.ccb' / 'ccb.config', _enabled_config())
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='status'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )
    seen: dict[str, object] = {}

    def _spawn(context, *, runner_id, source):
        seen['project'] = str(context.project.project_root)
        seen['runner_id'] = runner_id
        seen['source'] = source
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(maintenance_service, '_spawn_maintenance_runner', _spawn)

    payload = maintenance_service.startup_ensure_maintenance_heartbeat(context)
    runner = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id).load_runner().value

    assert payload is not None
    assert payload['action'] == 'runner-ensure'
    assert payload['runner_status'] == 'started'
    assert payload['runner_started'] is True
    assert payload['runner_pid'] == 4242
    assert seen['source'] == 'startup_ensure'
    assert runner is not None
    assert runner.pid == 4242
    assert runner.state == 'starting'


def test_startup_ensure_reuses_live_schedule_consumer_runner(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-startup-runner-live'
    _write(project_root / '.ccb' / 'ccb.config', _enabled_config())
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='status'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )
    store = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id)
    store.save_runner(
        MaintenanceHeartbeatRunner(
            project_id=context.project.project_id,
            runner_id='runner_live',
            pid=4242,
            state='sleeping',
            source='startup_ensure',
        )
    )
    monkeypatch.setattr(maintenance_service, '_pid_alive', lambda pid: pid == 4242)
    monkeypatch.setattr(
        maintenance_service,
        '_spawn_maintenance_runner',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('should not spawn')),
    )

    payload = maintenance_service.startup_ensure_maintenance_heartbeat(context)

    assert payload is not None
    assert payload['runner_status'] == 'already_running'
    assert payload['runner_started'] is False
    assert payload['runner_pid'] == 4242


def test_stop_maintenance_runner_signals_live_pid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance_service, 'utc_now', lambda: NOW)
    project_root = tmp_path / 'repo-stop-runner'
    _write(project_root / '.ccb' / 'ccb.config', _enabled_config())
    context = CliContextBuilder().build(
        ParsedMaintenanceCommand(project=None, action='status'),
        cwd=project_root,
        bootstrap_if_missing=False,
    )
    store = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id)
    store.save_runner(
        MaintenanceHeartbeatRunner(
            project_id=context.project.project_id,
            runner_id='runner_stop',
            pid=5151,
            state='sleeping',
            source='startup_ensure',
        )
    )
    alive = [True, False]
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(maintenance_service, '_pid_alive', lambda pid: alive.pop(0) if alive else False)
    monkeypatch.setattr(maintenance_service.os, 'kill', lambda pid, sig: killed.append((pid, sig)))

    payload = maintenance_service.stop_maintenance_heartbeat_runner(context, reason='kill')
    runner = store.load_runner().value

    assert payload['runner_stop_status'] == 'stopped'
    assert payload['runner_stopped'] is True
    assert killed == [(5151, maintenance_service.signal.SIGTERM)]
    assert runner is not None
    assert runner.state == 'stopped'
    assert runner.exit_reason == 'kill'


def test_render_maintenance_status_includes_config_and_state() -> None:
    lines = render_maintenance(
        {
            'maintenance_status': 'ok',
            'project': '/tmp/repo',
            'project_id': 'project-1',
            'config_source_kind': 'project_config',
            'config_source': '/tmp/repo/.ccb/ccb.config',
            'enabled': True,
            'assessor': 'demo',
            'assessor_present': False,
            'interval_s': 900,
            'min_interval_s': 90,
            'unknown_streak_cap': 3,
            'escalation_policy': 'report_only',
            'startup_ensure': True,
            'schedule': {
                'state': 'ok',
                'path': '/tmp/repo/.ccb/ccbd/maintenance-heartbeat/schedule.json',
                'error': None,
                'record': {
                    'next_run_at': '2026-06-10T12:00:00Z',
                    'reason': 'test',
                    'updated_at': '2026-06-10T11:00:00Z',
                    'updated_by': 'test',
                },
            },
            'last_status': {
                'state': 'missing',
                'path': '/tmp/repo/.ccb/ccbd/maintenance-heartbeat/status.json',
                'error': None,
            },
            'runner': {
                'state': 'ok',
                'path': '/tmp/repo/.ccb/ccbd/maintenance-heartbeat/runner.json',
                'error': None,
                'record': {
                    'runner_id': 'runner_1',
                    'pid': 123,
                    'state': 'sleeping',
                    'source': 'startup_ensure',
                    'started_at': '2026-06-10T11:00:00Z',
                    'last_seen_at': '2026-06-10T11:05:00Z',
                    'observed_next_run_at': '2026-06-10T12:00:00Z',
                    'sleep_until': '2026-06-10T12:00:00Z',
                },
            },
            'last_activation': {
                'state': 'ok',
                'path': '/tmp/repo/.ccb/ccbd/maintenance-heartbeat/activations.jsonl',
                'error': None,
                'record': {
                    'activation_id': 'act_1',
                    'status': 'submitted',
                    'target_agent': 'demo',
                    'delivery_mode': 'ask_silence',
                    'payload_kind': 'maintenance_diagnostic',
                    'dedup_key': 'maintenance:test',
                    'job_id': 'job_1',
                    'submitted_at': '2026-06-10T11:00:00Z',
                },
            },
        }
    )

    assert 'maintenance_status: ok' in lines
    assert 'heartbeat_enabled: true' in lines
    assert 'heartbeat_assessor: demo' in lines
    assert 'heartbeat_assessor_present: false' in lines
    assert 'schedule_state: ok' in lines
    assert 'schedule_next_run_at: 2026-06-10T12:00:00Z' in lines
    assert 'last_status_state: missing' in lines
    assert 'runner_state: ok' in lines
    assert 'runner_runner_id: runner_1' in lines
    assert 'runner_pid: 123' in lines
    assert 'last_activation_state: ok' in lines
    assert 'last_activation_status: submitted' in lines
