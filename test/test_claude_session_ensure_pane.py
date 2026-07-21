from __future__ import annotations

import json
from pathlib import Path

import pytest

import provider_backends.claude.session as claude_session
import provider_backends.claude.session_runtime.loading as claude_loading
import provider_backends.claude.session_runtime.model as claude_model


class FakeTmuxBackend:
    def __init__(self, crash_log_text: str) -> None:
        self.crash_log_text = crash_log_text
        self.alive: dict[str, bool] = {}
        self.exists: dict[str, bool] = {}
        self.respawned: list[tuple[str, str, str | None]] = []
        self.titles: list[tuple[str, str]] = []
        self.options: list[tuple[str, str, str]] = []

    def is_alive(self, pane_id: str) -> bool:
        return bool(self.alive.get(pane_id, False))

    def pane_exists(self, pane_id: str) -> bool:
        return bool(self.exists.get(pane_id, pane_id in self.alive))

    def describe_pane(self, pane_id: str, *, user_options: tuple[str, ...] = ()) -> dict[str, str]:
        described = {
            'pane_id': pane_id,
            'pane_title': 'claude',
            'pane_dead': '0' if self.is_alive(pane_id) else '1',
        }
        for option in user_options:
            if option == '@ccb_agent':
                described[option] = 'claude'
            elif option == '@ccb_project_id':
                described[option] = 'proj-1'
        return described

    def save_crash_log(self, pane_id: str, crash_log_path: str, *, lines: int = 1000) -> None:
        del pane_id, lines
        Path(crash_log_path).write_text(self.crash_log_text, encoding='utf-8')

    def respawn_pane(
        self,
        pane_id: str,
        *,
        cmd: str,
        cwd: str | None = None,
        stderr_log_path: str | None = None,
        remain_on_exit: bool = True,
    ) -> None:
        del stderr_log_path, remain_on_exit
        self.respawned.append((pane_id, cmd, cwd))
        self.alive[pane_id] = True

    def set_pane_title(self, pane_id: str, title: str) -> None:
        self.titles.append((pane_id, title))

    def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
        self.options.append((pane_id, name, value))


def test_claude_ensure_pane_upgrades_continue_from_crash_log_resume_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = '12345678-1234-1234-1234-123456789abc'
    session_path = tmp_path / '.claude-session'
    original_start_cmd = (
        'export HOME=/tmp/claude-home; '
        'claude --setting-sources user,project,local --permission-mode bypassPermissions --continue'
    )
    session_path.write_text(
        json.dumps(
            {
                'agent_name': 'claude',
                'ccb_project_id': 'proj-1',
                'terminal': 'tmux',
                'pane_id': '%1',
                'tmux_session': '%1',
                'pane_title_marker': 'CCB-claude-proj-1',
                'runtime_dir': str(tmp_path),
                'work_dir': str(tmp_path),
                'active': True,
                'start_cmd': original_start_cmd,
                'claude_start_cmd': original_start_cmd,
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    backend = FakeTmuxBackend(
        'Resume this session with:\n'
        f'claude --worktree projx-engine --resume {session_id}\n'
        'Pane is dead\n'
    )
    backend.alive = {'%1': False}
    backend.exists = {'%1': True}
    monkeypatch.setattr(claude_model, 'get_backend_for_session', lambda data: backend)
    monkeypatch.setattr(
        claude_loading,
        'find_project_session_file',
        lambda work_dir, instance=None: session_path,
    )

    session = claude_session.load_project_session(tmp_path)
    assert session is not None

    ok, pane = session.ensure_pane()

    assert ok is True
    assert pane == '%1'
    expected_cmd = (
        f'export HOME=/tmp/claude-home; '
        f'claude --setting-sources user,project,local --permission-mode bypassPermissions --resume {session_id}'
    )
    assert backend.respawned == [
        (
            '%1',
            expected_cmd,
            str(tmp_path),
        )
    ]
    persisted = json.loads(session_path.read_text(encoding='utf-8'))
    assert persisted['start_cmd'] == expected_cmd
    assert persisted['claude_start_cmd'] == persisted['start_cmd']
    assert persisted['claude_session_id'] == session_id


def test_claude_ensure_pane_does_not_trust_unmarked_resume_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = '12345678-1234-1234-1234-123456789abc'
    session_path = tmp_path / '.claude-session'
    original_start_cmd = 'export HOME=/tmp/claude-home; claude --continue'
    session_path.write_text(
        json.dumps(
            {
                'agent_name': 'claude',
                'ccb_project_id': 'proj-1',
                'terminal': 'tmux',
                'pane_id': '%1',
                'runtime_dir': str(tmp_path),
                'work_dir': str(tmp_path),
                'active': True,
                'start_cmd': original_start_cmd,
                'claude_start_cmd': original_start_cmd,
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    backend = FakeTmuxBackend(
        'A transcript can mention commands too:\n'
        f'claude --worktree projx-engine --resume {session_id}\n'
        'Pane is dead\n'
    )
    backend.alive = {'%1': False}
    backend.exists = {'%1': True}
    monkeypatch.setattr(claude_model, 'get_backend_for_session', lambda data: backend)
    monkeypatch.setattr(
        claude_loading,
        'find_project_session_file',
        lambda work_dir, instance=None: session_path,
    )

    session = claude_session.load_project_session(tmp_path)
    assert session is not None

    ok, _pane = session.ensure_pane()

    assert ok is True
    assert backend.respawned == [('%1', original_start_cmd, str(tmp_path))]
    persisted = json.loads(session_path.read_text(encoding='utf-8'))
    assert persisted['start_cmd'] == original_start_cmd
    assert 'claude_session_id' not in persisted
