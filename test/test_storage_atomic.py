from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from storage import atomic


def test_atomic_write_text_orders_file_and_directory_sync(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / 'state.txt'
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    class TrackingHandle:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, text):
            events.append('write')
            return self.handle.write(text)

        def flush(self):
            events.append('flush')
            return self.handle.flush()

        def fileno(self):
            return self.handle.fileno()

    real_fdopen = os.fdopen

    def tracking_fdopen(*args, **kwargs):
        return TrackingHandle(real_fdopen(*args, **kwargs))

    def tracking_fsync(fd):
        events.append('dir-fsync' if os.path.isdir(f'/proc/self/fd/{fd}') else 'file-fsync')
        return real_fsync(fd)

    def tracking_replace(*args, **kwargs):
        events.append('replace')
        return real_replace(*args, **kwargs)

    monkeypatch.setattr(os, 'fdopen', tracking_fdopen)
    monkeypatch.setattr(os, 'fsync', tracking_fsync)
    monkeypatch.setattr(os, 'replace', tracking_replace)

    atomic.atomic_write_text(target, 'new')

    assert target.read_text() == 'new'
    assert events == ['write', 'flush', 'file-fsync', 'replace', 'dir-fsync']


def test_failure_before_replace_preserves_old_target(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / 'state.txt'
    target.write_text('old')
    real_fsync = os.fsync

    def fail_file_fsync(fd):
        if not os.path.isdir(f'/proc/self/fd/{fd}'):
            raise OSError('file fsync failed')
        return real_fsync(fd)

    monkeypatch.setattr(os, 'fsync', fail_file_fsync)

    with pytest.raises(OSError, match='file fsync failed'):
        atomic.atomic_write_text(target, 'new')

    assert target.read_text() == 'old'
    assert list(tmp_path.glob('.state.txt.*.tmp')) == []


def test_directory_fsync_failure_surfaces_after_complete_replace(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / 'state.json'
    target.write_text('{"version": "old"}\n')
    real_fsync = os.fsync

    def fail_directory_fsync(fd):
        if os.path.isdir(f'/proc/self/fd/{fd}'):
            raise OSError('directory fsync failed')
        return real_fsync(fd)

    monkeypatch.setattr(os, 'fsync', fail_directory_fsync)

    with pytest.raises(OSError, match='directory fsync failed'):
        atomic.atomic_write_json(target, {'version': 'new'})

    assert json.loads(target.read_text()) == {'version': 'new'}


def test_nested_parent_entries_are_synced(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / 'one' / 'two' / 'state.txt'
    synced_directories: list[Path] = []
    real_fsync = os.fsync

    def tracking_fsync(fd):
        link = Path(f'/proc/self/fd/{fd}')
        if link.is_dir():
            synced_directories.append(link.resolve())
        return real_fsync(fd)

    monkeypatch.setattr(os, 'fsync', tracking_fsync)

    atomic.atomic_write_text(target, 'value')

    assert target.read_text() == 'value'
    assert synced_directories == [tmp_path, tmp_path / 'one', tmp_path / 'one' / 'two']


def test_temp_cleanup_does_not_replace_original_failure(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / 'state.txt'
    target.write_text('old')
    real_unlink = os.unlink

    def fail_replace(*args, **kwargs):
        raise OSError('replace failed')

    def fail_cleanup(path, *args, **kwargs):
        if str(path).endswith('.tmp'):
            raise OSError('cleanup failed')
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, 'replace', fail_replace)
    monkeypatch.setattr(os, 'unlink', fail_cleanup)

    with pytest.raises(OSError, match='replace failed'):
        atomic.atomic_write_text(target, 'new')

    assert target.read_text() == 'old'
    assert len(list(tmp_path.glob('.state.txt.*.tmp'))) == 1


def test_directory_close_failure_is_surfaced(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / 'state.txt'
    real_close = os.close

    def fail_directory_close(fd):
        if os.path.isdir(f'/proc/self/fd/{fd}'):
            real_close(fd)
            raise OSError('directory close failed')
        return real_close(fd)

    monkeypatch.setattr(os, 'close', fail_directory_close)

    with pytest.raises(OSError, match='directory close failed'):
        atomic.atomic_write_text(target, 'new')

    assert target.read_text() == 'new'


def test_directory_close_failure_does_not_replace_original_failure(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / 'state.txt'
    target.write_text('old')
    real_close = os.close

    def fail_replace(*args, **kwargs):
        raise OSError('replace failed')

    def fail_directory_close(fd):
        if os.path.isdir(f'/proc/self/fd/{fd}'):
            real_close(fd)
            raise OSError('directory close failed')
        return real_close(fd)

    monkeypatch.setattr(os, 'replace', fail_replace)
    monkeypatch.setattr(os, 'close', fail_directory_close)

    with pytest.raises(OSError, match='replace failed'):
        atomic.atomic_write_text(target, 'new')

    assert target.read_text() == 'old'


def test_parent_replacement_is_detected(monkeypatch, tmp_path: Path) -> None:
    parent = tmp_path / 'parent'
    parent.mkdir()
    target = parent / 'state.txt'
    target.write_text('old')
    moved_parent = tmp_path / 'moved-parent'
    real_replace = os.replace

    def replace_after_parent_move(*args, **kwargs):
        parent.rename(moved_parent)
        parent.mkdir()
        (parent / 'state.txt').write_text('canonical-old')
        return real_replace(*args, **kwargs)

    monkeypatch.setattr(os, 'replace', replace_after_parent_move)

    with pytest.raises(RuntimeError, match='parent directory was replaced'):
        atomic.atomic_write_text(target, 'new')

    assert target.read_text() == 'canonical-old'
    assert (moved_parent / 'state.txt').read_text() == 'new'


def test_atomic_write_json_format_is_unchanged(tmp_path: Path) -> None:
    target = tmp_path / 'state.json'

    atomic.atomic_write_json(target, {'z': '雪', 'a': 1})

    assert target.read_text() == '{\n  "z": "雪",\n  "a": 1\n}\n'
