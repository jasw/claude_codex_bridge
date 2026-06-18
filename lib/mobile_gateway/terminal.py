from __future__ import annotations

from dataclasses import dataclass
import fcntl
import os
import pty
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
    geometry: TerminalGeometry
    target_summary: dict[str, object]

    @property
    def command(self) -> list[str]:
        return ['tmux', '-S', self.socket_path, 'attach-session', '-t', self.session_name]


class TmuxTerminalSession:
    def __init__(self, target: TerminalAttachTarget) -> None:
        self.target = target
        self._master_fd, slave_fd = pty.openpty()
        try:
            self._resize(target.geometry)
            self._process = subprocess.Popen(
                target.command,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
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
        if data:
            os.write(self._master_fd, data)

    def paste(self, text: str) -> None:
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


def _int(value: object, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _positive_int(value: object, fallback: int) -> int:
    return max(1, _int(value, fallback))
