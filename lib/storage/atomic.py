from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any


def _open_directory(path: Path) -> int:
    if not hasattr(os, 'O_DIRECTORY'):
        raise NotImplementedError('durable atomic writes require directory fsync support')
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_CLOEXEC', 0)
    return os.open(path, flags)


def _fsync_directory(path: Path) -> None:
    fd = _open_directory(path)
    try:
        os.fsync(fd)
    except BaseException:
        _close_directory_after_error(fd)
        raise
    else:
        os.close(fd)


def _close_directory_after_error(fd: int) -> None:
    try:
        os.close(fd)
    except BaseException:
        pass


def _verify_directory_path(fd: int, path: Path) -> None:
    opened = os.fstat(fd)
    current = os.stat(path)
    if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
        raise RuntimeError(f'atomic write parent directory was replaced: {path}')


def _ensure_parent_directory(path: Path) -> None:
    missing: list[Path] = []
    candidate = path
    while not candidate.exists():
        missing.append(candidate)
        if candidate.parent == candidate:
            break
        candidate = candidate.parent

    for directory in reversed(missing):
        try:
            directory.mkdir()
        except FileExistsError:
            if not directory.is_dir():
                raise
        _fsync_directory(directory.parent)


def atomic_write_text(path: Path, text: str, *, encoding: str = 'utf-8') -> None:
    target = Path(path)
    _ensure_parent_directory(target.parent)
    directory_fd = _open_directory(target.parent)
    _verify_directory_path(directory_fd, target.parent)
    tmp_name = f'.{target.name}.{secrets.token_hex(8)}.tmp'
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_CLOEXEC', 0)
    try:
        fd = os.open(tmp_name, flags, 0o600, dir_fd=directory_fd)
    except BaseException:
        _close_directory_after_error(directory_fd)
        raise
    handle_opened = False
    try:
        with os.fdopen(fd, 'w', encoding=encoding) as handle:
            handle_opened = True
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _verify_directory_path(directory_fd, target.parent)
        os.replace(tmp_name, target.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
        _verify_directory_path(directory_fd, target.parent)
    except BaseException:
        if not handle_opened:
            try:
                os.close(fd)
            except BaseException:
                pass
        try:
            os.unlink(tmp_name, dir_fd=directory_fd)
        except BaseException:
            pass
        _close_directory_after_error(directory_fd)
        raise
    else:
        os.close(directory_fd)


def atomic_write_json(path: Path, payload: Any, *, encoding: str = 'utf-8') -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding=encoding)
