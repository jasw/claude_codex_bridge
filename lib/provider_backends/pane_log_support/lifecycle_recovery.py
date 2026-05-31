from __future__ import annotations

from pathlib import Path
import re
import shlex
from typing import Callable

from provider_core.tmux_ownership import (
    inspect_tmux_pane_ownership,
    ownership_error_text,
    session_slot_user_option_lookup,
)

from .lifecycle_common import (
    activate_rebound_pane,
    pane_exists,
    persist_crash_log,
)


def tmux_rebound_pane(
    session,
    backend: object,
    pane_id: str,
    *,
    now_str_fn: Callable[[], str],
    attach_pane_log_fn: Callable[[object, object, str], None],
) -> tuple[bool, str] | None:
    start_cmd = session.start_cmd
    respawn = getattr(backend, 'respawn_pane', None)
    create_pane = getattr(backend, 'create_pane', None)
    if not start_cmd or (not callable(respawn) and not callable(create_pane)):
        return None

    last_err = respawn_existing_pane(
        session,
        backend,
        pane_id,
        start_cmd=start_cmd,
        respawn=respawn,
        now_str_fn=now_str_fn,
        attach_pane_log_fn=attach_pane_log_fn,
    )
    if last_err is None:
        return True, str(pane_id)

    created = create_replacement_pane(
        session,
        backend,
        start_cmd=start_cmd,
        create_pane=create_pane,
        now_str_fn=now_str_fn,
        attach_pane_log_fn=attach_pane_log_fn,
    )
    if created is not None:
        return True, created
    return False, f'Pane not alive and respawn failed: {last_err}'


def respawn_existing_pane(
    session,
    backend: object,
    pane_id: str,
    *,
    start_cmd: str,
    respawn,
    now_str_fn: Callable[[], str],
    attach_pane_log_fn: Callable[[object, object, str], None],
) -> str | None:
    if not callable(respawn) or not pane_id or not str(pane_id).startswith('%'):
        return 'respawn unavailable'
    if not pane_exists(backend, str(pane_id)):
        return 'pane target no longer exists'
    ownership = inspect_tmux_pane_ownership(session, backend, str(pane_id))
    if not ownership.is_owned and not can_reclaim_project_slot_pane(session, backend, str(pane_id)):
        return ownership_error_text(ownership, pane_id=str(pane_id))
    try:
        crash_log = persist_crash_log(session, backend, str(pane_id))
        start_cmd = upgraded_claude_resume_cmd(session, crash_log, start_cmd)
        respawn(str(pane_id), cmd=start_cmd, cwd=session.work_dir, remain_on_exit=True)
        if not backend.is_alive(str(pane_id)):
            return 'respawn did not revive pane'
        activate_rebound_pane(
            session,
            backend,
            str(pane_id),
            now_str_fn=now_str_fn,
            attach_pane_log_fn=attach_pane_log_fn,
        )
        return None
    except Exception as exc:
        return f'{exc}'


_CLAUDE_RESUME_ID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


def upgraded_claude_resume_cmd(session, crash_log: Path | None, start_cmd: str) -> str:
    invocation = claude_resume_invocation_from_log(crash_log)
    if not invocation:
        return start_cmd
    upgraded = replace_provider_invocation(start_cmd, invocation)
    if upgraded == start_cmd:
        return start_cmd
    persist_upgraded_claude_resume_cmd(session, upgraded, invocation)
    return upgraded


def claude_resume_invocation_from_log(crash_log: Path | None) -> str | None:
    if crash_log is None:
        return None
    try:
        lines = crash_log.read_text(encoding='utf-8', errors='replace').splitlines()
    except Exception:
        return None
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index].strip()
        if 'claude' not in line or '--resume' not in line:
            continue
        previous = previous_non_empty_line(lines, index)
        if previous != 'Resume this session with:':
            continue
        parts = safe_split(line)
        if not parts or parts[0] != 'claude':
            continue
        if not valid_resume_invocation(parts):
            continue
        return shlex.join(parts)
    return None


def previous_non_empty_line(lines: list[str], index: int) -> str:
    for previous_index in range(index - 1, -1, -1):
        line = lines[previous_index].strip()
        if line:
            return line
    return ''


def replace_provider_invocation(start_cmd: str, invocation: str) -> str:
    raw = str(start_cmd or '').strip()
    if not raw:
        return invocation
    prefix, sep, tail = raw.rpartition(';')
    if sep and safe_split(tail.strip())[:1] == ['claude']:
        return f'{prefix}; {invocation}'
    if safe_split(raw)[:1] == ['claude']:
        return invocation
    return raw


def valid_resume_invocation(parts: list[str]) -> bool:
    try:
        index = parts.index('--resume')
    except ValueError:
        return False
    if index + 1 >= len(parts):
        return False
    return bool(_CLAUDE_RESUME_ID_RE.match(parts[index + 1]))


def persist_upgraded_claude_resume_cmd(session, start_cmd: str, invocation: str) -> None:
    data = getattr(session, 'data', None)
    if not isinstance(data, dict):
        return
    data['start_cmd'] = start_cmd
    data['claude_start_cmd'] = start_cmd
    session_id = resume_id_from_invocation(invocation)
    if session_id:
        data['claude_session_id'] = session_id
    writer = getattr(session, '_write_back', None)
    if callable(writer):
        try:
            writer()
        except Exception:
            pass


def resume_id_from_invocation(invocation: str) -> str | None:
    parts = safe_split(invocation)
    try:
        index = parts.index('--resume')
    except ValueError:
        return None
    if index + 1 >= len(parts):
        return None
    candidate = parts[index + 1]
    return candidate if _CLAUDE_RESUME_ID_RE.match(candidate) else None


def safe_split(value: str) -> list[str]:
    try:
        return shlex.split(str(value or '').strip())
    except Exception:
        return []


def can_reclaim_project_slot_pane(session, backend: object, pane_id: str) -> bool:
    expected = session_slot_user_option_lookup(session)
    if not expected:
        return False
    ownership = inspect_tmux_pane_ownership(_SlotOwnershipSession(expected), backend, pane_id)
    return ownership.is_owned


def create_replacement_pane(
    session,
    backend: object,
    *,
    start_cmd: str,
    create_pane,
    now_str_fn: Callable[[], str],
    attach_pane_log_fn: Callable[[object, object, str], None],
) -> str | None:
    if not callable(create_pane):
        return None
    data = getattr(session, 'data', None)
    parent_pane = None
    if isinstance(data, dict):
        parent_pane_text = str(data.get('ccb_replacement_parent_pane') or '').strip()
        if parent_pane_text.startswith('%'):
            parent_pane = parent_pane_text
    try:
        if parent_pane is not None:
            new_pane = create_pane(start_cmd, session.work_dir, parent_pane=parent_pane)
        else:
            new_pane = create_pane(start_cmd, session.work_dir)
    except Exception:
        return None
    if not new_pane or not backend.is_alive(str(new_pane)):
        return None
    activate_rebound_pane(
        session,
        backend,
        str(new_pane),
        now_str_fn=now_str_fn,
        attach_pane_log_fn=attach_pane_log_fn,
    )
    return str(new_pane)


__all__ = [
    'create_replacement_pane',
    'respawn_existing_pane',
    'tmux_rebound_pane',
]


class _SlotOwnershipSession:
    def __init__(self, expected: dict[str, str]) -> None:
        self._expected = expected

    def user_option_lookup(self) -> dict[str, str]:
        return dict(self._expected)
