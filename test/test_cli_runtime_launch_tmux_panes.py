from __future__ import annotations

import subprocess

from cli.services.runtime_launch_runtime import tmux_panes


class FakeBackend:
    def __init__(self, *, socket_path: str | None = None, socket_name: str | None = None, returncode: int = 0) -> None:
        self.socket_path = socket_path
        self.socket_name = socket_name
        self.returncode = returncode
        self.calls: list[tuple[str, ...]] = []

    def _tmux_run(self, argv, **kwargs):
        del kwargs
        self.calls.append(tuple(argv))
        return subprocess.CompletedProcess(args=argv, returncode=self.returncode, stdout='', stderr='')


def setup_function() -> None:
    tmux_panes._PREPARED_DETACHED_TMUX_SERVER_KEYS.clear()


def test_prepare_detached_tmux_server_reuses_same_socket_and_environment(monkeypatch) -> None:
    monkeypatch.setenv('DISPLAY', ':1')
    backend = FakeBackend(socket_path='/tmp/ccb.sock')

    tmux_panes.prepare_detached_tmux_server(backend)
    first_count = len(backend.calls)
    tmux_panes.prepare_detached_tmux_server(backend)

    assert first_count > 0
    assert len(backend.calls) == first_count


def test_prepare_detached_tmux_server_does_not_share_different_sockets(monkeypatch) -> None:
    monkeypatch.setenv('DISPLAY', ':1')
    first = FakeBackend(socket_path='/tmp/ccb-a.sock')
    second = FakeBackend(socket_path='/tmp/ccb-b.sock')

    tmux_panes.prepare_detached_tmux_server(first)
    tmux_panes.prepare_detached_tmux_server(second)

    assert first.calls
    assert second.calls


def test_prepare_detached_tmux_server_refreshes_when_environment_changes(monkeypatch) -> None:
    backend = FakeBackend(socket_path='/tmp/ccb.sock')
    monkeypatch.setenv('DISPLAY', ':1')
    tmux_panes.prepare_detached_tmux_server(backend)
    first_count = len(backend.calls)

    monkeypatch.setenv('DISPLAY', ':2')
    tmux_panes.prepare_detached_tmux_server(backend)

    assert len(backend.calls) > first_count


def test_prepare_detached_tmux_server_retries_after_failed_prepare(monkeypatch) -> None:
    monkeypatch.setenv('DISPLAY', ':1')
    backend = FakeBackend(socket_path='/tmp/ccb.sock', returncode=1)

    tmux_panes.prepare_detached_tmux_server(backend)
    first_count = len(backend.calls)
    tmux_panes.prepare_detached_tmux_server(backend)

    assert first_count > 0
    assert len(backend.calls) == first_count * 2
