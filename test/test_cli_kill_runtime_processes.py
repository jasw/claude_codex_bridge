from __future__ import annotations

import signal
from pathlib import Path

import cli.kill_runtime.processes as processes
from project.resolver import bootstrap_project
from runtime_pid_cleanup import collect_project_authority_pid_candidates, collect_project_process_candidates, list_process_cmdlines


def test_kill_pid_tree_once_uses_taskkill_on_windows(monkeypatch) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr(processes.os, 'name', 'nt')
    monkeypatch.setattr(
        processes.subprocess,
        'run',
        lambda args, capture_output=True: calls.append(list(args)) or None,
    )

    assert processes._kill_pid_tree_once(321, force=True) is True
    assert calls == [["taskkill", "/F", "/T", "/PID", "321"]]


def test_kill_pid_tree_once_prefers_process_group_on_posix(monkeypatch) -> None:
    killed: list[tuple[int, signal.Signals]] = []
    kill_pid_calls: list[tuple[int, bool]] = []

    monkeypatch.setattr(processes.os, 'name', 'posix')
    monkeypatch.setattr(processes, '_safe_getpgid', lambda pid: 900)
    monkeypatch.setattr(processes, '_safe_getpgrp', lambda: 901)
    monkeypatch.setattr(processes.os, 'killpg', lambda pgid, sig: killed.append((pgid, sig)))
    monkeypatch.setattr(processes, 'kill_pid', lambda pid, force=False: kill_pid_calls.append((pid, force)) or True)

    assert processes._kill_pid_tree_once(123, force=False) is True
    assert killed == [(900, signal.SIGTERM)]
    assert kill_pid_calls == []


def test_collect_project_process_candidates_finds_ccbd_project_arg(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-control-plane-scan'
    project_root.mkdir()
    bootstrap_project(project_root)
    proc_root = tmp_path / 'proc'
    (proc_root / '101').mkdir(parents=True)
    (proc_root / '102').mkdir()
    cmdlines = {
        101: f'/usr/bin/python /opt/ccb/lib/ccbd/main.py --project {project_root}',
        102: f'/usr/bin/python /opt/ccb/lib/ccbd/main.py --project {tmp_path / "other"}',
    }

    candidates = collect_project_process_candidates(
        project_root,
        proc_root=proc_root,
        read_proc_cmdline_fn=lambda pid: cmdlines.get(pid, ''),
        current_pid=999,
    )

    assert set(candidates) == {101}
    assert candidates[101] == [project_root / '.ccb' / 'ccbd']


def test_list_process_cmdlines_falls_back_to_ps_when_proc_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    proc_root = tmp_path / 'missing-proc'

    class _Result:
        returncode = 0
        stdout = ' 101 /usr/bin/python /opt/ccb/lib/ccbd/main.py --project /tmp/repo\n 202 helper\n'

    monkeypatch.setattr(
        'runtime_pid_cleanup.procfs.subprocess.run',
        lambda args, check=False, capture_output=True, text=True: _Result(),
    )

    mapping = list_process_cmdlines(proc_root=proc_root, current_pid=202)

    assert mapping == {101: '/usr/bin/python /opt/ccb/lib/ccbd/main.py --project /tmp/repo'}


def test_collect_project_process_candidates_falls_back_to_ps_without_proc(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ps-scan'
    project_root.mkdir()
    bootstrap_project(project_root)

    candidates = collect_project_process_candidates(
        project_root,
        proc_root=tmp_path / 'missing-proc',
        list_process_cmdlines_fn=lambda **kwargs: {
            101: f'/usr/bin/python /opt/ccb/lib/ccbd/main.py --project {project_root}',
            202: f'/usr/bin/python /opt/ccb/lib/ccbd/keeper_main.py --project {project_root}',
            303: '/usr/bin/python unrelated.py',
        },
        current_pid=999,
    )

    assert sorted(candidates) == [101, 202]
    assert candidates[101] == [project_root / '.ccb' / 'ccbd']
    assert candidates[202] == [project_root / '.ccb' / 'ccbd']


def test_collect_project_authority_pid_candidates_reads_lifecycle(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-authority-lifecycle'
    project_root.mkdir()
    bootstrap_project(project_root)
    lifecycle_path = project_root / '.ccb' / 'ccbd' / 'lifecycle.json'
    lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
    lifecycle_path.write_text(
        '{"owner_pid": 321, "keeper_pid": 654}\n',
        encoding='utf-8',
    )

    candidates = collect_project_authority_pid_candidates(project_root)

    assert candidates[321] == [lifecycle_path]
    assert candidates[654] == [lifecycle_path]
