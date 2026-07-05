from __future__ import annotations

from dataclasses import dataclass
import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import termios
from typing import Mapping


@dataclass(frozen=True)
class TerminalGeometry:
    columns: int = 80
    rows: int = 24
    pixel_width: int = 0
    pixel_height: int = 0

    @classmethod
    def from_mapping(cls, value: object) -> 'TerminalGeometry':
        payload = value if isinstance(value, Mapping) else {}
        return cls(
            columns=_positive_int(payload.get('columns'), 80),
            rows=_positive_int(payload.get('rows'), 24),
            pixel_width=max(0, _int(payload.get('pixel_width'), 0)),
            pixel_height=max(0, _int(payload.get('pixel_height'), 0)),
        )


@dataclass(frozen=True)
class TerminalAttachTarget:
    terminal_id: str
    socket_path: str
    session_name: str
    pane_id: str | None
    geometry: TerminalGeometry
    target_summary: dict[str, object]

    @property
    def command(self) -> list[str]:
        return ['tmux', '-S', self.socket_path, 'attach-session', '-t', self.session_name]


@dataclass(frozen=True)
class TerminalHistoryTarget:
    project_id: str
    namespace_epoch: int
    agent: str
    window: str
    pane_id: str
    socket_path: str
    session_name: str
    max_lines: int = 200

    @property
    def command(self) -> list[str]:
        return [
            'tmux',
            '-S',
            self.socket_path,
            'capture-pane',
            '-p',
            '-t',
            self.pane_id,
            '-S',
            f'-{max(1, int(self.max_lines))}',
        ]


@dataclass(frozen=True)
class PaneMessageTarget:
    project_id: str
    namespace_epoch: int
    agent: str
    window: str
    pane_id: str
    socket_path: str
    session_name: str


def create_tmux_terminal_history(target: TerminalHistoryTarget) -> dict[str, object]:
    cp = subprocess.run(
        target.command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=2.0,
    )
    if cp.returncode != 0:
        message = (cp.stderr or '').strip() or 'tmux capture-pane failed'
        raise RuntimeError(message)
    text = _strip_ansi(cp.stdout or '')
    return {
        'agent': target.agent,
        'history_scope': 'tmux_scrollback',
        'source_pane_id': target.pane_id,
        'stale': False,
        'blocks': _readable_history_blocks(text),
    }


def send_tmux_pane_message(target: PaneMessageTarget, text: str) -> dict[str, object]:
    message = str(text or '')
    _tmux_run(target, ['send-keys', '-t', target.pane_id, 'C-u'])
    _tmux_run(target, ['send-keys', '-t', target.pane_id, '-l', message])
    _tmux_run(target, ['send-keys', '-t', target.pane_id, 'Enter'])
    return {
        'project_id': target.project_id,
        'agent': target.agent,
        'window': target.window,
        'pane_id': target.pane_id,
        'namespace_epoch': target.namespace_epoch,
    }


def _tmux_run(
    target: PaneMessageTarget,
    args: list[str],
) -> None:
    cp = subprocess.run(
        ['tmux', '-S', target.socket_path, *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=2.0,
    )
    if cp.returncode != 0:
        message = (cp.stderr or '').strip() or 'tmux send failed'
        raise RuntimeError(message)


class TmuxTerminalSession:
    def __init__(self, target: TerminalAttachTarget) -> None:
        self.target = target
        self._master_fd, slave_fd = pty.openpty()
        try:
            self._resize(target.geometry)
            if target.pane_id:
                _select_tmux_terminal_pane(target)
            self._process = subprocess.Popen(
                target.command,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                env=_terminal_client_env(),
            )
        finally:
            os.close(slave_fd)

    def read(self, timeout_seconds: float = 0.1) -> bytes | None:
        ready, _, _ = select.select([self._master_fd], [], [], max(0.0, float(timeout_seconds)))
        if not ready:
            return b''
        try:
            data = os.read(self._master_fd, 65536)
        except OSError:
            return None if self._process.poll() is not None else b''
        if not data and self._process.poll() is not None:
            return None
        return data

    def write(self, data: bytes) -> None:
        if not data:
            return
        if self.target.pane_id:
            _send_tmux_terminal_bytes(self.target, data)
            return
        os.write(self._master_fd, data)

    def paste(self, text: str) -> None:
        if self.target.pane_id:
            _send_tmux_terminal_literal(self.target, str(text or ''))
            return
        self.write(str(text).encode('utf-8'))

    def resize(self, geometry: TerminalGeometry) -> None:
        self._resize(geometry)

    def close(self) -> None:
        try:
            os.close(self._master_fd)
        except OSError:
            pass
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=1)

    def _resize(self, geometry: TerminalGeometry) -> None:
        rows = max(1, int(geometry.rows))
        columns = max(1, int(geometry.columns))
        pixels_y = max(0, int(geometry.pixel_height))
        pixels_x = max(0, int(geometry.pixel_width))
        packed = struct.pack('HHHH', rows, columns, pixels_y, pixels_x)
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, packed)


def create_tmux_terminal_session(target: TerminalAttachTarget) -> TmuxTerminalSession:
    return TmuxTerminalSession(target)


def _terminal_client_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop('TMUX', None)
    if not env.get('TERM') or env.get('TERM') == 'dumb':
        env['TERM'] = 'xterm-256color'
    return env


def _select_tmux_terminal_pane(target: TerminalAttachTarget) -> None:
    _tmux_terminal_run(target, ['select-window', '-t', str(target.pane_id)])
    _tmux_terminal_run(target, ['select-pane', '-t', str(target.pane_id)])


def _send_tmux_terminal_literal(target: TerminalAttachTarget, text: str) -> None:
    if not text:
        return
    _tmux_terminal_run(target, ['send-keys', '-t', str(target.pane_id), '-l', text])


def _send_tmux_terminal_bytes(target: TerminalAttachTarget, data: bytes) -> None:
    key_names = {
        b'\r': 'Enter',
        b'\n': 'Enter',
        b'\t': 'Tab',
        b'\x1b': 'Escape',
        b'\x01': 'C-a',
        b'\x03': 'C-c',
        b'\x04': 'C-d',
        b'\x05': 'C-e',
        b'\x0b': 'C-k',
        b'\x0c': 'C-l',
        b'\x12': 'C-r',
        b'\x15': 'C-u',
        b'\x17': 'C-w',
        b'\x1a': 'C-z',
        b'\x7f': 'BSpace',
        b'\b': 'BSpace',
        b'\x1b[A': 'Up',
        b'\x1b[B': 'Down',
        b'\x1b[C': 'Right',
        b'\x1b[D': 'Left',
        b'\x1b[H': 'Home',
        b'\x1b[F': 'End',
        b'\x1bOH': 'Home',
        b'\x1bOF': 'End',
        b'\x1b[1~': 'Home',
        b'\x1b[4~': 'End',
        b'\x1b[3~': 'Delete',
        b'\x1b[5~': 'PageUp',
        b'\x1b[6~': 'PageDown',
    }
    key = key_names.get(data)
    if key is not None:
        _tmux_terminal_run(target, ['send-keys', '-t', str(target.pane_id), key])
        return
    if _is_terminal_protocol_response(data):
        return
    if _has_control_byte(data):
        raise RuntimeError(f'unsupported terminal input bytes for {target.terminal_id}')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        raise RuntimeError(f'unsupported terminal input bytes for {target.terminal_id}')
    _send_tmux_terminal_literal(target, text)


def _has_control_byte(data: bytes) -> bool:
    return any(byte < 0x20 or byte == 0x7F for byte in data)


_TERMINAL_PROTOCOL_RESPONSES = (
    re.compile(r'\x1b\[\?[0-9;]*c'),  # primary device attributes
    re.compile(r'\x1b\[>[0-9;]*c'),  # secondary device attributes
    re.compile(r'\x1bP!\|[0-9A-Fa-f]*\x1b\\'),  # tertiary device attributes
    re.compile(r'\x1b\[0n'),  # operating status
    re.compile(r'\x1b\[[0-9;]*R'),  # cursor position report
    re.compile(r'\x1b\[8;[0-9]+;[0-9]+t'),  # terminal size report
    re.compile(r'\x1b\[[IO]'),  # focus in/out
    re.compile(r'\x1b\[<[0-9;]+[mM]'),  # SGR mouse report
    re.compile(r'\x1b\]1[01];(?:rgb:)?[0-9A-Fa-f/]+(?:\x07|\x1b\\)'),  # OSC colors
)


def _is_terminal_protocol_response(data: bytes) -> bool:
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return False
    index = 0
    while index < len(text):
        for pattern in _TERMINAL_PROTOCOL_RESPONSES:
            match = pattern.match(text, index)
            if match is not None:
                index = match.end()
                break
        else:
            return False
    return bool(text)


def _tmux_terminal_run(
    target: TerminalAttachTarget,
    args: list[str],
) -> None:
    cp = subprocess.run(
        ['tmux', '-S', target.socket_path, *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=2.0,
    )
    if cp.returncode != 0:
        message = (cp.stderr or '').strip() or 'tmux terminal input failed'
        raise RuntimeError(message)


def _readable_history_blocks(text: str) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    current_type = ''
    current: list[str] = []

    def flush() -> None:
        nonlocal current_type, current
        rendered = '\n'.join(line.rstrip() for line in current).strip()
        if not rendered:
            current_type = ''
            current = []
            return
        block_type = current_type or _classify_line(rendered)
        blocks.append(
            {
                'id': f'history-{len(blocks) + 1}',
                'type': block_type,
                'title': _block_title(block_type),
                'text': rendered,
            }
        )
        current_type = ''
        current = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            flush()
            continue
        line_type = _classify_line(line)
        if current and line_type != current_type and line_type in {'command', 'diff', 'error'}:
            flush()
        if not current:
            current_type = line_type
        current.append(line)
    flush()
    return blocks


def _classify_line(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith(('$ ', '> ', '# ')):
        return 'command'
    if stripped.startswith(('Traceback ', 'Error:', 'ERROR:', 'Exception:', 'FAILED')):
        return 'error'
    if stripped.startswith(('diff --git ', '+++ ', '--- ', '+ ', '- ', '@@ ')):
        return 'diff'
    if stripped.startswith(('```', 'def ', 'class ', 'import ', 'from ', 'const ', 'final ', 'Future<')):
        return 'code'
    return 'log'


def _block_title(block_type: str) -> str:
    return {
        'command': 'Command',
        'code': 'Code',
        'diff': 'Diff',
        'error': 'Error',
    }.get(block_type, 'Log')


def _strip_ansi(text: str) -> str:
    return re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)


def _int(value: object, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _positive_int(value: object, fallback: int) -> int:
    return max(1, _int(value, fallback))
