from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import shutil

from cli.models import ParsedLayoutCommand
from cli.parser import CliParser
from cli.phase2 import maybe_handle_phase2
import pytest


def test_layout_parser_supports_plan_and_smoke() -> None:
    parser = CliParser()

    assert parser.parse(['layout', 'plan', '--panes', '6', '--window-prefix', 'frontdesk', '--json']) == ParsedLayoutCommand(
        project=None,
        action='plan',
        panes=6,
        window_prefix='frontdesk',
        json_output=True,
    )
    assert parser.parse(['layout', 'smoke', '--panes', '7', '--session', 'demo', '--keep', '--json']) == ParsedLayoutCommand(
        project=None,
        action='smoke',
        panes=7,
        session_name='demo',
        cleanup=False,
        json_output=True,
    )
    assert parser.parse(['layout', 'dynamic-smoke', '--panes', '8', '--window-prefix', 'frontdesk', '--json']) == ParsedLayoutCommand(
        project=None,
        action='dynamic-smoke',
        panes=8,
        window_prefix='frontdesk',
        json_output=True,
    )
    assert parser.parse(
        [
            'layout',
            'resolve',
            'planner2',
            '--window-class',
            'plan-orchestrate',
            '--loop-id',
            'round1',
            '--node-id',
            'node1',
            '--json',
        ]
    ) == ParsedLayoutCommand(
        project=None,
        action='resolve',
        agent_name='planner2',
        window_class='plan-orchestrate',
        loop_id='round1',
        node_id='node1',
        json_output=True,
    )


def test_layout_plan_json_reports_one_to_six_and_overflow(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-cli'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    stdout = StringIO()
    stderr = StringIO()

    code = maybe_handle_phase2(
        ['layout', 'plan', '--panes', '7', '--window-prefix', 'frontdesk-dialog', '--json'],
        cwd=project_root,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload['layout_status'] == 'planned'
    assert payload['pane_count'] == 7
    assert [window['name'] for window in payload['windows']] == ['frontdesk-dialog', 'frontdesk-dialog-2']
    assert payload['windows'][0]['layout_spec'] == 'p1, p3, p5; p2, p4, p6'
    assert payload['windows'][1]['layout_spec'] == 'p7'


def test_layout_dynamic_smoke_grows_and_shrinks_pages(tmp_path: Path) -> None:
    if shutil.which('tmux') is None:
        pytest.skip('tmux is not installed')
    project_root = tmp_path / 'repo-layout-dynamic-smoke'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    stdout = StringIO()
    stderr = StringIO()

    code = maybe_handle_phase2(
        ['layout', 'dynamic-smoke', '--panes', '7', '--window-prefix', 'frontdesk-dialog', '--json'],
        cwd=project_root,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload['layout_status'] == 'ok'
    assert payload['dynamic_status'] == 'ok'
    assert payload['cleanup_status'] == 'ok'
    events = payload['dynamic_events']
    assert [event['target_count'] for event in events] == [1, 2, 3, 4, 5, 6, 7, 6, 5, 4, 3, 2, 1]
    assert events[6]['phase'] == 'grow'
    assert events[6]['window_count'] == 2
    assert [window['pane_count'] for window in events[6]['observed_windows']] == [6, 1]
    assert events[7]['phase'] == 'shrink'
    assert events[7]['agent'] == 'p7'
    assert events[7]['window_count'] == 1
    assert [window['pane_count'] for window in events[7]['observed_windows']] == [6]
    assert all(event['all_retained_alive'] for event in events)
