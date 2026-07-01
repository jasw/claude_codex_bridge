from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest

from cli.services import mobile_host
from cli.services.mobile_host import (
    MOBILE_HOST_SERVE_COMMAND,
    MOBILE_HOST_SERVICE_RECORD_TYPE,
    MobileHostServiceError,
    PortOwner,
    detect_loopback_port_owner,
    mobile_host_service_paths,
    run_mobile_host_serve_command,
    start_or_replace_mobile_host_service,
    write_mobile_host_service_state,
)


class _FakeProcess:
    def __init__(self, pid: int, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


def test_mobile_host_service_clears_stale_state_and_starts(tmp_path: Path) -> None:
    state_dir = tmp_path / 'mobile'
    paths = mobile_host_service_paths(state_dir)
    write_mobile_host_service_state(
        paths.state_path,
        {
            'schema_version': 1,
            'record_type': MOBILE_HOST_SERVICE_RECORD_TYPE,
            'pid': 111,
            'generation': 4,
            'command_kind': 'ccb_mobile_host_serve',
        },
    )
    spawned: list[dict[str, object]] = []

    def _spawn(command, **kwargs):
        spawned.append({'command': command, **kwargs})
        return _FakeProcess(222)

    result = start_or_replace_mobile_host_service(
        script_root=tmp_path / 'source',
        listen='127.0.0.1:8787',
        public_url='https://desktop.tailnet.ts.net:8787',
        route_provider='tailnet',
        state_dir=state_dir,
        process_exists_fn=lambda pid: False,
        port_owner_fn=lambda _listen: None,
        spawn_fn=_spawn,
        health_check_fn=lambda _url: True,
    )

    assert result.status == 'started'
    assert result.pid == 222
    assert result.generation == 1
    assert spawned
    assert spawned[0]['command'][2] == MOBILE_HOST_SERVE_COMMAND
    assert spawned[0]['env']['CCB_MOBILE_HOST_STATE_HOME'] == str(state_dir)
    assert not paths.state_path.exists()


def test_mobile_host_service_replaces_live_managed_process(tmp_path: Path) -> None:
    state_dir = tmp_path / 'mobile'
    paths = mobile_host_service_paths(state_dir)
    write_mobile_host_service_state(
        paths.state_path,
        {
            'schema_version': 1,
            'record_type': MOBILE_HOST_SERVICE_RECORD_TYPE,
            'pid': 111,
            'generation': 4,
            'command_kind': 'ccb_mobile_host_serve',
            'state_dir': str(state_dir),
        },
    )
    alive = {111}
    terminated: list[int] = []

    def _terminate(pid: int, **_kwargs) -> bool:
        terminated.append(pid)
        alive.discard(pid)
        return True

    result = start_or_replace_mobile_host_service(
        script_root=tmp_path / 'source',
        listen='127.0.0.1:8787',
        public_url='https://desktop.tailnet.ts.net:8787',
        route_provider='tailnet',
        state_dir=state_dir,
        process_exists_fn=lambda pid: pid in alive,
        process_cmdline_fn=lambda pid: f'python ccb.py {MOBILE_HOST_SERVE_COMMAND} --state-dir {state_dir}' if pid == 111 else '',
        terminate_pid_tree_fn=_terminate,
        port_owner_fn=lambda _listen: None,
        spawn_fn=lambda *_args, **_kwargs: _FakeProcess(222),
        health_check_fn=lambda _url: True,
    )

    assert terminated == [111]
    assert result.status == 'replaced'
    assert result.replaced_pid == 111
    assert result.generation == 5


def test_mobile_host_service_refuses_external_port_owner(tmp_path: Path) -> None:
    killed: list[int] = []

    with pytest.raises(MobileHostServiceError, match='non-CCB process') as excinfo:
        start_or_replace_mobile_host_service(
            script_root=tmp_path / 'source',
            listen='127.0.0.1:8787',
            public_url='https://desktop.tailnet.ts.net:8787',
            route_provider='tailnet',
            state_dir=tmp_path / 'mobile',
            process_exists_fn=lambda _pid: True,
            process_cmdline_fn=lambda _pid: 'python /tmp/external_gateway.py',
            terminate_pid_tree_fn=lambda pid, **_kwargs: killed.append(pid) or True,
            port_owner_fn=lambda _listen: PortOwner(pid=333, command='python /tmp/external_gateway.py'),
            spawn_fn=lambda *_args, **_kwargs: _FakeProcess(222),
            health_check_fn=lambda _url: True,
        )

    assert killed == []
    assert 'pid=333' in str(excinfo.value)


def test_mobile_host_service_refuses_truncated_managed_command_from_other_state_dir(tmp_path: Path) -> None:
    state_dir = tmp_path / 'mobile'
    other_state_dir = tmp_path / 'other-mobile'
    paths = mobile_host_service_paths(state_dir)
    write_mobile_host_service_state(
        paths.state_path,
        {
            'schema_version': 1,
            'record_type': MOBILE_HOST_SERVICE_RECORD_TYPE,
            'pid': 111,
            'generation': 4,
            'command_kind': 'ccb_mobile_host_serve',
            'state_dir': str(other_state_dir),
        },
    )
    killed: list[int] = []
    spawned: list[int] = []

    with pytest.raises(MobileHostServiceError, match='non-CCB process'):
        start_or_replace_mobile_host_service(
            script_root=tmp_path / 'source',
            listen='127.0.0.1:8787',
            public_url='https://desktop.tailnet.ts.net:8787',
            route_provider='tailnet',
            state_dir=state_dir,
            process_exists_fn=lambda pid: pid == 111,
            process_cmdline_fn=lambda pid: f'python ccb.py {MOBILE_HOST_SERVE_COMMAND}' if pid == 111 else '',
            terminate_pid_tree_fn=lambda pid, **_kwargs: killed.append(pid) or True,
            port_owner_fn=lambda _listen: PortOwner(pid=111, command='python ccb.py __mobile-host-serve'),
            spawn_fn=lambda *_args, **_kwargs: spawned.append(1) or _FakeProcess(222),
            health_check_fn=lambda _url: True,
        )

    assert killed == []
    assert spawned == []


def test_mobile_host_service_lock_blocks_concurrent_update(tmp_path: Path) -> None:
    paths = mobile_host_service_paths(tmp_path / 'mobile')
    paths.state_dir.mkdir(parents=True)
    paths.lock_path.write_text(json.dumps({'pid': 999}) + '\n', encoding='utf-8')

    with pytest.raises(MobileHostServiceError, match='already in progress'):
        start_or_replace_mobile_host_service(
            script_root=tmp_path / 'source',
            listen='127.0.0.1:8787',
            public_url='https://desktop.tailnet.ts.net:8787',
            route_provider='tailnet',
            state_dir=paths.state_dir,
            process_exists_fn=lambda pid: pid == 999,
            port_owner_fn=lambda _listen: None,
            spawn_fn=lambda *_args, **_kwargs: _FakeProcess(222),
            health_check_fn=lambda _url: True,
        )


def test_mobile_host_service_terminates_spawned_process_when_health_never_ready(tmp_path: Path) -> None:
    ticks = iter([0.0, 0.0, 0.0, 10.0])
    terminated: list[int] = []

    with pytest.raises(MobileHostServiceError, match='did not become healthy'):
        start_or_replace_mobile_host_service(
            script_root=tmp_path / 'source',
            listen='127.0.0.1:8787',
            public_url='https://desktop.tailnet.ts.net:8787',
            route_provider='tailnet',
            state_dir=tmp_path / 'mobile',
            process_exists_fn=lambda _pid: False,
            terminate_pid_tree_fn=lambda pid, **_kwargs: terminated.append(pid) or True,
            port_owner_fn=lambda _listen: None,
            spawn_fn=lambda *_args, **_kwargs: _FakeProcess(222),
            health_check_fn=lambda _url: False,
            sleep_fn=lambda _seconds: None,
            monotonic_fn=lambda: next(ticks),
        )

    assert terminated == [222]


def test_detect_loopback_port_owner_uses_lsof_when_ss_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _which(name: str) -> str | None:
        return None if name == 'ss' else '/usr/sbin/lsof'

    def _run(command, **_kwargs):
        assert command[0] == 'lsof'
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                'COMMAND   PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n'
                'python3   444 user 3u IPv4 0t0 TCP 127.0.0.1:8787 (LISTEN)\n'
            ),
            stderr='',
        )

    monkeypatch.setattr(mobile_host.shutil, 'which', _which)
    monkeypatch.setattr(mobile_host.subprocess, 'run', _run)
    monkeypatch.setattr(mobile_host, '_process_cmdline', lambda pid: 'python gateway.py' if pid == 444 else '')

    owner = detect_loopback_port_owner('127.0.0.1:8787')

    assert owner == PortOwner(pid=444, command='python gateway.py')


def test_detect_loopback_port_owner_reports_unknown_when_tools_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mobile_host.shutil, 'which', lambda _name: None)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(('127.0.0.1', 0))
        server.listen()
        port = server.getsockname()[1]

        owner = detect_loopback_port_owner(f'127.0.0.1:{port}')

    assert owner == PortOwner(pid=0, command='unknown loopback listener')


def test_mobile_host_serve_removes_current_state_after_server_exits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / 'mobile'
    paths = mobile_host_service_paths(state_dir)
    closed: list[bool] = []

    class _Handle:
        summary = {
            'host_id': 'desktop',
            'listen': '127.0.0.1:8787',
            'local_gateway_url': 'http://127.0.0.1:8787',
            'gateway_url': 'http://127.0.0.1:8787',
            'route_provider': 'tailnet',
        }

        def serve_forever(self) -> None:
            assert paths.state_path.exists()

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(mobile_host, 'prepare_server_mobile_gateway', lambda *_args, **_kwargs: _Handle())

    code = run_mobile_host_serve_command(
        SimpleNamespace(
            listen='127.0.0.1:8787',
            public_url=None,
            route_provider='tailnet',
            state_dir=str(state_dir),
            generation=7,
            host_id='desktop',
        ),
        script_root=tmp_path / 'source',
    )

    assert code == 0
    assert closed == [True]
    assert not paths.state_path.exists()


def test_mobile_host_serve_reports_state_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    closed: list[bool] = []

    class _Handle:
        summary = {'listen': '127.0.0.1:8787'}

        def serve_forever(self) -> None:
            raise AssertionError('serve_forever should not run when state write fails')

        def close(self) -> None:
            closed.append(True)

    def _fail_write(_path, _payload) -> None:
        raise OSError('disk full')

    monkeypatch.setattr(mobile_host, 'prepare_server_mobile_gateway', lambda *_args, **_kwargs: _Handle())
    monkeypatch.setattr(mobile_host, 'write_mobile_host_service_state', _fail_write)

    code = run_mobile_host_serve_command(
        SimpleNamespace(
            listen='127.0.0.1:8787',
            public_url=None,
            route_provider='tailnet',
            state_dir=str(tmp_path / 'mobile'),
            generation=7,
            host_id=None,
        ),
        script_root=tmp_path / 'source',
    )

    assert code == 1
    assert closed == [True]
    assert 'could not write state: OSError: disk full' in capsys.readouterr().err
