from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Callable
from urllib.error import URLError
from urllib.request import urlopen

from ccbd.system import utc_now
from cli.kill_runtime.processes import is_pid_alive, terminate_pid_tree
from cli.services.mobile import prepare_server_mobile_gateway
from mobile_gateway import mobile_host_state_dir
from storage.atomic import atomic_write_json


MOBILE_HOST_SERVE_COMMAND = '__mobile-host-serve'
MOBILE_HOST_SERVICE_RECORD_TYPE = 'ccb_mobile_host_service'
MOBILE_HOST_SERVICE_SCHEMA_VERSION = 1
MOBILE_HOST_LOCK_TTL_S = 120.0
MOBILE_HOST_STOP_TIMEOUT_S = 2.0
MOBILE_HOST_PORT_RELEASE_TIMEOUT_S = 3.0
MOBILE_HOST_HEALTH_TIMEOUT_S = 5.0


class MobileHostServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class PortOwner:
    pid: int
    command: str = ''


@dataclass(frozen=True)
class MobileHostServicePaths:
    state_dir: Path
    state_path: Path
    lock_path: Path
    log_path: Path


@dataclass(frozen=True)
class MobileHostServiceResult:
    status: str
    pid: int
    generation: int
    listen: str
    gateway_url: str | None
    local_gateway_url: str
    route_provider: str
    state_dir: Path
    state_path: Path
    log_path: Path
    replaced_pid: int | None = None

    def to_record(self) -> dict[str, object]:
        record: dict[str, object] = {
            'mobile_status': 'serving',
            'service_status': self.status,
            'pid': self.pid,
            'generation': self.generation,
            'listen': self.listen,
            'local_gateway_url': self.local_gateway_url,
            'gateway_url': self.gateway_url or self.local_gateway_url,
            'route_provider': self.route_provider,
            'mobile_state_dir': str(self.state_dir),
            'service_state_path': str(self.state_path),
            'service_log_path': str(self.log_path),
        }
        if self.replaced_pid is not None:
            record['replaced_pid'] = self.replaced_pid
        return record


def start_or_replace_mobile_host_service(
    *,
    script_root: Path,
    listen: str,
    public_url: str | None,
    route_provider: str,
    state_dir: Path | None = None,
    host_id: str | None = None,
    process_exists_fn: Callable[[int], bool] = is_pid_alive,
    process_cmdline_fn: Callable[[int], str] | None = None,
    terminate_pid_tree_fn: Callable[..., bool] = terminate_pid_tree,
    port_owner_fn: Callable[[str], PortOwner | None] | None = None,
    spawn_fn: Callable[..., object] | None = None,
    health_check_fn: Callable[[str], bool] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> MobileHostServiceResult:
    paths = mobile_host_service_paths(state_dir)
    lock_fd = _acquire_mobile_host_lock(paths.lock_path, process_exists_fn=process_exists_fn)
    if lock_fd is None:
        raise MobileHostServiceError(f'mobile host service update already in progress: {paths.lock_path}')
    replaced_pid: int | None = None
    try:
        state = read_mobile_host_service_state(paths.state_path)
        old_pid = _state_pid(state)
        if old_pid and not process_exists_fn(old_pid):
            remove_mobile_host_service_state(paths.state_path)
            state = None
            old_pid = None
        if old_pid:
            if process_exists_fn(old_pid) and _managed_mobile_host_process(
                old_pid,
                state,
                state_dir=paths.state_dir,
                process_cmdline_fn=process_cmdline_fn,
            ):
                replaced_pid = old_pid
                _terminate_managed_mobile_host(
                    old_pid,
                    terminate_pid_tree_fn=terminate_pid_tree_fn,
                )
                if not _wait_until(
                    lambda: not process_exists_fn(old_pid),
                    timeout_s=MOBILE_HOST_STOP_TIMEOUT_S,
                    sleep_fn=sleep_fn,
                    monotonic_fn=monotonic_fn,
                ):
                    raise MobileHostServiceError(f'mobile host service did not stop: pid={old_pid}')
            else:
                remove_mobile_host_service_state(paths.state_path)
                state = None
                old_pid = None
        generation = _next_generation(state)
        owner = (port_owner_fn or detect_loopback_port_owner)(listen)
        if owner is not None:
            if not _managed_mobile_host_process(
                owner.pid,
                state,
                state_dir=paths.state_dir,
                process_cmdline_fn=process_cmdline_fn,
            ):
                detail = f'pid={owner.pid}'
                if owner.command:
                    detail += f' command={owner.command}'
                raise MobileHostServiceError(f'mobile gateway listen port is already owned by a non-CCB process: {detail}')
            replaced_pid = owner.pid
            _terminate_managed_mobile_host(owner.pid, terminate_pid_tree_fn=terminate_pid_tree_fn)
        _wait_until(
            lambda: (port_owner_fn or detect_loopback_port_owner)(listen) is None,
            timeout_s=MOBILE_HOST_PORT_RELEASE_TIMEOUT_S,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
        )
        if (port_owner_fn or detect_loopback_port_owner)(listen) is not None:
            raise MobileHostServiceError(f'mobile gateway listen port did not release: {listen}')

        process = _spawn_mobile_host_service(
            script_root=script_root,
            paths=paths,
            listen=listen,
            public_url=public_url,
            route_provider=route_provider,
            generation=generation,
            host_id=host_id,
            spawn_fn=spawn_fn,
        )
        pid = int(getattr(process, 'pid', 0) or 0)
        local_gateway_url = _local_gateway_url(listen)
        try:
            _wait_for_mobile_host_health(
                local_gateway_url,
                process=process,
                health_check_fn=health_check_fn,
                timeout_s=MOBILE_HOST_HEALTH_TIMEOUT_S,
                sleep_fn=sleep_fn,
                monotonic_fn=monotonic_fn,
            )
        except Exception:
            if pid > 0:
                _terminate_managed_mobile_host(pid, terminate_pid_tree_fn=terminate_pid_tree_fn)
            raise
        return MobileHostServiceResult(
            status='replaced' if replaced_pid is not None else 'started',
            pid=pid,
            generation=generation,
            listen=listen,
            gateway_url=public_url or local_gateway_url,
            local_gateway_url=local_gateway_url,
            route_provider=route_provider,
            state_dir=paths.state_dir,
            state_path=paths.state_path,
            log_path=paths.log_path,
            replaced_pid=replaced_pid,
        )
    finally:
        _release_mobile_host_lock(paths.lock_path, lock_fd)


def maybe_handle_mobile_host_serve_command(tokens: list[str], *, script_root: Path) -> int | None:
    if list(tokens[:1]) != [MOBILE_HOST_SERVE_COMMAND]:
        return None
    parser = argparse.ArgumentParser(prog=f'ccb {MOBILE_HOST_SERVE_COMMAND}', add_help=False)
    parser.add_argument('--listen', required=True)
    parser.add_argument('--public-url', default=None)
    parser.add_argument('--route-provider', default='tailnet')
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--generation', type=int, required=True)
    parser.add_argument('--host-id', default=None)
    namespace = parser.parse_args(tokens[1:])
    return run_mobile_host_serve_command(namespace, script_root=script_root)


def run_mobile_host_serve_command(args, *, script_root: Path) -> int:
    del script_root
    state_dir = Path(args.state_dir).expanduser()
    os.environ['CCB_MOBILE_HOST_STATE_HOME'] = str(state_dir)
    handle = prepare_server_mobile_gateway(
        SimpleNamespace(
            listen=str(args.listen),
            public_url=str(args.public_url).strip() if args.public_url else None,
            route_provider=str(args.route_provider or 'tailnet'),
        ),
        host_id=str(args.host_id or '').strip() or None,
    )
    summary = dict(handle.summary)
    paths = mobile_host_service_paths(state_dir)
    generation = int(args.generation)
    try:
        write_mobile_host_service_state(
            paths.state_path,
            {
                'schema_version': MOBILE_HOST_SERVICE_SCHEMA_VERSION,
                'record_type': MOBILE_HOST_SERVICE_RECORD_TYPE,
                'pid': os.getpid(),
                'process_group_id': os.getpgrp() if hasattr(os, 'getpgrp') else os.getpid(),
                'generation': generation,
                'host_id': str(summary.get('host_id') or args.host_id or ''),
                'listen': str(summary.get('listen') or args.listen),
                'local_gateway_url': str(summary.get('local_gateway_url') or _local_gateway_url(str(args.listen))),
                'gateway_url': str(summary.get('gateway_url') or summary.get('local_gateway_url') or ''),
                'route_provider': str(summary.get('route_provider') or args.route_provider or 'tailnet'),
                'state_dir': str(paths.state_dir),
                'started_at': utc_now(),
                'command_kind': 'ccb_mobile_host_serve',
                'entrypoint': sys.argv[0] if sys.argv else '',
            },
        )
    except Exception as exc:
        print(f'mobile host service could not write state: {type(exc).__name__}: {exc}', file=sys.stderr)
        handle.close()
        return 1
    try:
        handle.serve_forever()
    finally:
        _remove_mobile_host_service_state_if_current(
            paths.state_path,
            pid=os.getpid(),
            generation=generation,
        )
        handle.close()
    return 0


def mobile_host_service_paths(state_dir: Path | None = None) -> MobileHostServicePaths:
    root = Path(state_dir or mobile_host_state_dir()).expanduser()
    return MobileHostServicePaths(
        state_dir=root,
        state_path=root / 'service.json',
        lock_path=root / 'service.lock',
        log_path=root / 'service.log',
    )


def read_mobile_host_service_state(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise MobileHostServiceError(f'invalid mobile host service state: {path}') from exc
    if not isinstance(payload, dict):
        raise MobileHostServiceError(f'invalid mobile host service state: {path}')
    if payload.get('record_type') != MOBILE_HOST_SERVICE_RECORD_TYPE:
        raise MobileHostServiceError(f'unexpected mobile host service record: {path}')
    return payload


def write_mobile_host_service_state(path: Path, payload: dict[str, object]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(Path(path), payload)


def remove_mobile_host_service_state(path: Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def detect_loopback_port_owner(listen: str) -> PortOwner | None:
    host, port = _split_listen(listen)
    if host not in {'127.0.0.1', 'localhost', '::1'}:
        raise MobileHostServiceError('mobile gateway only supports loopback listen addresses')
    owner = _detect_loopback_port_owner_ss(host=host, port=port)
    if owner is not None:
        return owner
    owner = _detect_loopback_port_owner_lsof(host=host, port=port)
    if owner is not None:
        return owner
    if _loopback_port_accepts_connection(host=host, port=port):
        return PortOwner(pid=0, command='unknown loopback listener')
    return None


def _detect_loopback_port_owner_ss(*, host: str, port: int) -> PortOwner | None:
    if shutil.which('ss') is None:
        return None
    try:
        result = subprocess.run(
            ['ss', '-ltnp', f'sport = :{port}'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    for line in (result.stdout or '').splitlines():
        if f':{port}' not in line:
            continue
        fields = line.split()
        if len(fields) < 4:
            continue
        local_address = fields[3]
        if not _listen_address_matches(local_address, host=host, port=port):
            continue
        pid = _pid_from_ss_line(line)
        if pid is None:
            return None
        return PortOwner(pid=pid, command=_process_cmdline(pid))
    return None


def _detect_loopback_port_owner_lsof(*, host: str, port: int) -> PortOwner | None:
    if shutil.which('lsof') is None:
        return None
    try:
        result = subprocess.run(
            ['lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    for line in (result.stdout or '').splitlines()[1:]:
        if f':{port}' not in line or 'TCP ' not in line:
            continue
        tcp_name = line.split('TCP ', 1)[1]
        if not _lsof_tcp_name_matches(tcp_name, host=host, port=port):
            continue
        fields = line.split(None, 2)
        if len(fields) < 2:
            return PortOwner(pid=0, command='unknown loopback listener')
        try:
            pid = int(fields[1])
        except ValueError:
            return PortOwner(pid=0, command='unknown loopback listener')
        return PortOwner(pid=pid, command=_process_cmdline(pid) or fields[0])
    return None


def _loopback_port_accepts_connection(*, host: str, port: int) -> bool:
    connect_host = '127.0.0.1' if host == 'localhost' else host
    try:
        with socket.create_connection((connect_host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _spawn_mobile_host_service(
    *,
    script_root: Path,
    paths: MobileHostServicePaths,
    listen: str,
    public_url: str | None,
    route_provider: str,
    generation: int,
    host_id: str | None,
    spawn_fn: Callable[..., object] | None,
) -> object:
    command = [
        sys.executable,
        str(Path(script_root) / 'ccb.py'),
        MOBILE_HOST_SERVE_COMMAND,
        '--listen',
        listen,
        '--route-provider',
        route_provider,
        '--state-dir',
        str(paths.state_dir),
        '--generation',
        str(generation),
    ]
    if public_url:
        command.extend(['--public-url', public_url])
    if host_id:
        command.extend(['--host-id', host_id])
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env['CCB_MOBILE_HOST_STATE_HOME'] = str(paths.state_dir)
    env['CCB_SKIP_STARTUP_UPDATE_CHECK'] = '1'
    log = paths.log_path.open('ab')
    try:
        spawner = spawn_fn or subprocess.Popen
        process = spawner(
            command,
            cwd=str(paths.state_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        return process
    except Exception:
        raise
    finally:
        log.close()


def _wait_for_mobile_host_health(
    local_gateway_url: str,
    *,
    process: object,
    health_check_fn: Callable[[str], bool] | None,
    timeout_s: float,
    sleep_fn: Callable[[float], None],
    monotonic_fn: Callable[[], float],
) -> None:
    deadline = monotonic_fn() + max(0.0, timeout_s)
    checker = health_check_fn or _http_health_check
    while monotonic_fn() < deadline:
        poll = getattr(process, 'poll', None)
        if callable(poll) and poll() is not None:
            raise MobileHostServiceError('mobile host service exited before becoming healthy')
        if checker(local_gateway_url):
            return
        sleep_fn(0.1)
    raise MobileHostServiceError(f'mobile host service did not become healthy: {local_gateway_url}/v1/health')


def _http_health_check(local_gateway_url: str) -> bool:
    try:
        with urlopen(f'{local_gateway_url}/v1/health', timeout=0.5) as response:
            return 200 <= int(response.status) < 300
    except (OSError, URLError):
        return False


def _terminate_managed_mobile_host(pid: int, *, terminate_pid_tree_fn: Callable[..., bool]) -> None:
    terminate_pid_tree_fn(pid, timeout_s=MOBILE_HOST_STOP_TIMEOUT_S)


def _managed_mobile_host_process(
    pid: int,
    state: dict[str, object] | None,
    *,
    state_dir: Path,
    process_cmdline_fn: Callable[[int], str] | None,
) -> bool:
    cmdline = (process_cmdline_fn or _process_cmdline)(pid)
    if MOBILE_HOST_SERVE_COMMAND not in cmdline:
        return False
    if str(state_dir) in cmdline:
        return True
    if (
        state is not None
        and str(state.get('command_kind') or '') == 'ccb_mobile_host_serve'
        and _state_matches_mobile_host_state_dir(state, state_dir=state_dir)
    ):
        return True
    return False


def _process_cmdline(pid: int) -> str:
    if pid <= 0:
        return ''
    proc_cmdline = Path('/proc') / str(pid) / 'cmdline'
    try:
        text = proc_cmdline.read_bytes().replace(b'\x00', b' ').decode('utf-8', errors='replace').strip()
        if text:
            return text
    except Exception:
        pass
    try:
        result = subprocess.run(
            ['ps', '-p', str(pid), '-o', 'command='],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
        )
    except Exception:
        return ''
    return (result.stdout or '').strip()


def _acquire_mobile_host_lock(
    lock_path: Path,
    *,
    process_exists_fn: Callable[[int], bool],
) -> int | None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _clear_stale_lock(lock_path, process_exists_fn=process_exists_fn)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None
    payload = {'pid': os.getpid(), 'created_at': utc_now()}
    os.write(fd, (json.dumps(payload) + '\n').encode('utf-8'))
    return fd


def _release_mobile_host_lock(lock_path: Path, fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def _clear_stale_lock(lock_path: Path, *, process_exists_fn: Callable[[int], bool]) -> None:
    try:
        payload = json.loads(lock_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return
    except Exception:
        return
    pid = _coerce_positive_int(payload.get('pid') if isinstance(payload, dict) else None)
    try:
        age_s = max(0.0, time.time() - lock_path.stat().st_mtime)
    except OSError:
        return
    if (pid is not None and not process_exists_fn(pid)) or age_s > MOBILE_HOST_LOCK_TTL_S:
        try:
            lock_path.unlink()
        except OSError:
            pass


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
    sleep_fn: Callable[[float], None],
    monotonic_fn: Callable[[], float],
) -> bool:
    deadline = monotonic_fn() + max(0.0, timeout_s)
    while monotonic_fn() < deadline:
        if predicate():
            return True
        sleep_fn(0.05)
    return predicate()


def _next_generation(state: dict[str, object] | None) -> int:
    if state is None:
        return 1
    return max(0, int(state.get('generation') or 0)) + 1


def _state_pid(state: dict[str, object] | None) -> int | None:
    if state is None:
        return None
    return _coerce_positive_int(state.get('pid'))


def _coerce_positive_int(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _state_matches_mobile_host_state_dir(state: dict[str, object], *, state_dir: Path) -> bool:
    recorded = str(state.get('state_dir') or '').strip()
    if not recorded:
        return False
    try:
        return str(Path(recorded).expanduser()) == str(state_dir)
    except Exception:
        return False


def _remove_mobile_host_service_state_if_current(path: Path, *, pid: int, generation: int) -> None:
    try:
        state = read_mobile_host_service_state(path)
    except MobileHostServiceError:
        return
    if _state_pid(state) != pid:
        return
    try:
        state_generation = int((state or {}).get('generation') or 0)
    except (TypeError, ValueError):
        return
    if state_generation == generation:
        remove_mobile_host_service_state(path)


def _split_listen(listen: str) -> tuple[str, int]:
    text = str(listen or '').strip()
    if ':' not in text:
        raise MobileHostServiceError('mobile gateway listen must be host:port')
    host, port_text = text.rsplit(':', 1)
    try:
        port = int(port_text)
    except ValueError as exc:
        raise MobileHostServiceError('mobile gateway listen port must be an integer') from exc
    if port <= 0 or port > 65535:
        raise MobileHostServiceError('mobile gateway listen port must be between 1 and 65535')
    return host, port


def _local_gateway_url(listen: str) -> str:
    host, port = _split_listen(listen)
    return f'http://{host}:{port}'


def _listen_address_matches(value: str, *, host: str, port: int) -> bool:
    normalized = value.strip()
    if not normalized.endswith(f':{port}'):
        return False
    bind_host = normalized.rsplit(':', 1)[0]
    if bind_host in {host, f'[{host}]'}:
        return True
    if bind_host in {'*', '0.0.0.0', '[::]', '::'}:
        return True
    if host == 'localhost':
        return bind_host in {'127.0.0.1', '[::1]', '::1'}
    return False


def _lsof_tcp_name_matches(value: str, *, host: str, port: int) -> bool:
    local_name = value.split(None, 1)[0].strip()
    return _listen_address_matches(local_name, host=host, port=port)


def _pid_from_ss_line(line: str) -> int | None:
    marker = 'pid='
    if marker not in line:
        return None
    suffix = line.split(marker, 1)[1]
    digits = []
    for ch in suffix:
        if ch.isdigit():
            digits.append(ch)
        else:
            break
    if not digits:
        return None
    return int(''.join(digits))


__all__ = [
    'MOBILE_HOST_SERVE_COMMAND',
    'MobileHostServiceError',
    'MobileHostServiceResult',
    'PortOwner',
    'detect_loopback_port_owner',
    'maybe_handle_mobile_host_serve_command',
    'mobile_host_service_paths',
    'read_mobile_host_service_state',
    'remove_mobile_host_service_state',
    'run_mobile_host_serve_command',
    'start_or_replace_mobile_host_service',
    'write_mobile_host_service_state',
]
