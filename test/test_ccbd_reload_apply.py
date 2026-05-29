from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.config_loader import load_project_config
from ccbd.app import CcbdApp
from ccbd.models import CcbdStartupAgentResult
from ccbd.reload_apply import run_additive_reload_apply
from ccbd.reload_runtime_mount import AdditiveRuntimeMountResult
from ccbd.services.lifecycle import build_lifecycle
from ccbd.services.project_namespace_runtime import NamespacePatchApplyResult
from ccbd.services.project_namespace_state import ProjectNamespaceState, ProjectNamespaceStateStore
from ccbd.start_flow_runtime import StartFlowSummary


BASE_CONFIG = """version = 2
entry_window = "main"

[windows]
main = "agent1:codex, agent2:claude"

[ui.sidebar]
mode = "every_window"
width = "15%"
bottom_height = 20
"""

VIEW_CONFIG = BASE_CONFIG + """
[ui.sidebar.view]
comms_limit = 4
"""

VIEW_CONFIG_CHANGED = BASE_CONFIG + """
[ui.sidebar.view]
comms_limit = 5
"""

ADD_AGENT_CONFIG = BASE_CONFIG.replace(
    'agent1:codex, agent2:claude',
    'agent1:codex, (agent2:claude; agent3:codex)',
)

ADD_WINDOW_CONFIG = """version = 2
entry_window = "main"

[windows]
main = "agent1:codex, agent2:claude"
review = "agent3:codex"

[ui.sidebar]
mode = "every_window"
width = "15%"
bottom_height = 20
"""

REMOVE_AGENT_CONFIG = BASE_CONFIG.replace(
    'agent1:codex, agent2:claude',
    'agent1:codex',
)

NOW = '2026-05-29T00:00:00Z'


def test_additive_reload_apply_view_only_publishes_without_namespace_or_runtime_mutation(
    tmp_path: Path,
) -> None:
    app = _started_app(tmp_path / 'repo-view', VIEW_CONFIG)
    old_graph = app.service_graph
    new_config = _load_config(app.project_root, VIEW_CONFIG_CHANGED)
    calls: list[str] = []

    result = run_additive_reload_apply(
        app,
        new_config,
        current_namespace=_namespace(app),
        apply_namespace_patch_fn=_fail_with('view-only must skip namespace patch'),
        run_runtime_mount_fn=lambda *_args, **_kwargs: (
            calls.append('runtime') or AdditiveRuntimeMountResult(status='noop')
        ),
    )

    assert result.status == 'published'
    assert result.plan_class == 'view_only_change'
    assert result.namespace_patch['status'] == 'applied'
    assert result.namespace_patch['diagnostics']['reason'] == 'view_only_change'
    assert result.runtime_mount['status'] == 'noop'
    assert calls == ['runtime']
    assert app.service_graph is not old_graph
    assert app.service_graph.version == 2
    assert app.config is app.service_graph.config
    assert app.config.sidebar_view.comms_limit == 5
    assert app.config_identity == app.service_graph.config_identity
    assert app.mount_manager.load_state().config_signature == app.config_identity['config_signature']
    assert app.lifecycle_store.load().config_signature == app.config_identity['config_signature']
    assert result.diagnostics['graph_published'] is True
    assert result.diagnostics['config_watch_started'] is False
    assert result.diagnostics['unload_or_replace_executed'] is False


def test_additive_reload_apply_add_agent_success_mounts_then_publishes(tmp_path: Path) -> None:
    app = _started_app(tmp_path / 'repo-add-agent', BASE_CONFIG)
    old_graph = app.service_graph
    new_config = _load_config(app.project_root, ADD_AGENT_CONFIG)
    calls: list[dict[str, object]] = []

    result = run_additive_reload_apply(
        app,
        new_config,
        current_namespace=_namespace(app),
        apply_namespace_patch_fn=lambda **_kwargs: _namespace_patch_result(agent_panes={'agent3': '%3'}),
        run_start_flow_fn=_mounting_start_flow(app, calls),
    )

    assert result.status == 'published'
    assert result.plan_class == 'add_agent'
    assert result.namespace_patch['agent_panes'] == {'agent3': '%3'}
    assert result.runtime_mount['status'] == 'mounted'
    assert result.runtime_mount['runtime_authority_written_agents'] == ['agent3']
    assert app.service_graph is not old_graph
    assert app.service_graph.version == 2
    assert app.config_identity == app.service_graph.config_identity
    assert app.service_graph.registry.get('agent3').pane_id == '%3'
    assert app.service_graph.registry.get('agent1') is None
    assert app.mount_manager.load_state().config_signature == app.config_identity['config_signature']
    assert app.lifecycle_store.load().config_signature == app.config_identity['config_signature']
    assert calls[0]['requested_agents'] == ('agent3',)
    assert calls[0]['cleanup_tmux_orphans'] is False
    assert calls[0]['namespace_agent_panes'] == {'agent3': '%3'}


def test_additive_reload_apply_add_window_success_mounts_new_window_agent_then_publishes(
    tmp_path: Path,
) -> None:
    app = _started_app(tmp_path / 'repo-add-window', BASE_CONFIG)
    new_config = _load_config(app.project_root, ADD_WINDOW_CONFIG)
    calls: list[dict[str, object]] = []

    result = run_additive_reload_apply(
        app,
        new_config,
        current_namespace=_namespace(app),
        apply_namespace_patch_fn=lambda **_kwargs: _namespace_patch_result(
            created_windows=('review',),
            created_panes=('%3', '%4'),
            agent_panes={'agent3': '%4'},
            sidebar_panes={'review': '%3'},
        ),
        run_start_flow_fn=_mounting_start_flow(app, calls),
    )

    assert result.status == 'published'
    assert result.plan_class == 'add_window'
    assert result.namespace_patch['created_windows'] == ['review']
    assert result.namespace_patch['sidebar_panes'] == {'review': '%3'}
    assert result.runtime_mount['runtime_authority_written_agents'] == ['agent3']
    assert app.service_graph.version == 2
    assert app.config.entry_window == 'main'
    assert app.service_graph.registry.get('agent3').pane_id == '%4'
    assert calls[0]['namespace_agent_panes'] == {'agent3': '%4'}


def test_additive_reload_apply_blocks_non_additive_plan_without_building_target_graph(tmp_path: Path) -> None:
    app = _started_app(tmp_path / 'repo-remove-blocked', BASE_CONFIG)
    old_graph = app.service_graph
    old_lease = app.mount_manager.load_state().to_record()
    old_lifecycle = app.lifecycle_store.load().to_record()
    new_config = _load_config(app.project_root, REMOVE_AGENT_CONFIG)

    result = run_additive_reload_apply(
        app,
        new_config,
        current_namespace=_namespace(app),
        apply_namespace_patch_fn=_fail_with('plan blocked must not patch namespace'),
        run_runtime_mount_fn=_fail_with('plan blocked must not mount runtime'),
        publish_transaction_fn=_fail_with('plan blocked must not publish'),
    )

    assert result.status == 'blocked'
    assert result.stage == 'plan'
    assert result.plan_class == 'remove_agent'
    assert result.diagnostics['reason'] == 'unsupported_plan_class'
    assert result.diagnostics['graph_published'] is False
    assert app.service_graph is old_graph
    assert app.mount_manager.load_state().to_record() == old_lease
    assert app.lifecycle_store.load().to_record() == old_lifecycle


def test_additive_reload_apply_namespace_patch_failure_stops_before_runtime_and_publish(
    tmp_path: Path,
) -> None:
    app = _started_app(tmp_path / 'repo-namespace-fail', BASE_CONFIG)
    old_graph = app.service_graph
    old_lease = app.mount_manager.load_state().to_record()
    old_lifecycle = app.lifecycle_store.load().to_record()
    new_config = _load_config(app.project_root, ADD_WINDOW_CONFIG)

    result = run_additive_reload_apply(
        app,
        new_config,
        current_namespace=_namespace(app),
        apply_namespace_patch_fn=lambda **_kwargs: NamespacePatchApplyResult(
            status='failed',
            created_windows=('review',),
            created_panes=('%3',),
            partial=True,
            rollback_actions=('created_pane:%3',),
            diagnostics={
                'reason': 'namespace_patch_failed',
                'graph_published': False,
                'runtime_authority_written': False,
                'lease_or_lifecycle_written': False,
            },
        ),
        run_runtime_mount_fn=_fail_with('namespace failure must not mount runtime'),
        publish_transaction_fn=_fail_with('namespace failure must not publish'),
    )

    assert result.status == 'failed'
    assert result.stage == 'namespace_patch'
    assert result.diagnostics['namespace_residue']['created_windows'] == ['review']
    assert result.diagnostics['namespace_residue']['created_panes'] == ['%3']
    assert result.diagnostics['graph_published'] is False
    assert result.diagnostics['lease_or_lifecycle_written'] is False
    assert app.service_graph is old_graph
    assert app.mount_manager.load_state().to_record() == old_lease
    assert app.lifecycle_store.load().to_record() == old_lifecycle


def test_additive_reload_apply_runtime_mount_failure_stops_before_publish(tmp_path: Path) -> None:
    app = _started_app(tmp_path / 'repo-runtime-fail', BASE_CONFIG)
    old_graph = app.service_graph
    old_lease = app.mount_manager.load_state().to_record()
    old_lifecycle = app.lifecycle_store.load().to_record()
    new_config = _load_config(app.project_root, ADD_AGENT_CONFIG)

    result = run_additive_reload_apply(
        app,
        new_config,
        current_namespace=_namespace(app),
        apply_namespace_patch_fn=lambda **_kwargs: _namespace_patch_result(agent_panes={'agent3': '%3'}),
        run_runtime_mount_fn=lambda *_args, **_kwargs: AdditiveRuntimeMountResult(
            status='failed',
            requested_agents=('agent3',),
            mounted_agents=('agent3',),
            runtime_authority_written_agents=('agent3',),
            partial=True,
            diagnostics={
                'reason': 'runtime_mount_failed',
                'graph_published': False,
                'lease_or_lifecycle_written': False,
            },
        ),
        publish_transaction_fn=_fail_with('runtime failure must not publish'),
    )

    assert result.status == 'failed'
    assert result.stage == 'runtime_mount'
    assert result.diagnostics['runtime_residue']['runtime_authority_written_agents'] == ['agent3']
    assert result.diagnostics['graph_published'] is False
    assert result.diagnostics['lease_or_lifecycle_written'] is False
    assert app.service_graph is old_graph
    assert app.mount_manager.load_state().to_record() == old_lease
    assert app.lifecycle_store.load().to_record() == old_lifecycle


def test_additive_reload_apply_publish_transaction_failure_keeps_old_graph(tmp_path: Path) -> None:
    app = _started_app(tmp_path / 'repo-publish-fail', BASE_CONFIG)
    old_graph = app.service_graph
    old_lease = app.mount_manager.load_state().to_record()
    old_lifecycle = app.lifecycle_store.load().to_record()
    new_config = _load_config(app.project_root, ADD_AGENT_CONFIG)

    result = run_additive_reload_apply(
        app,
        new_config,
        current_namespace=_namespace(app),
        apply_namespace_patch_fn=lambda **_kwargs: _namespace_patch_result(agent_panes={'agent3': '%3'}),
        run_runtime_mount_fn=lambda *_args, **_kwargs: AdditiveRuntimeMountResult(
            status='mounted',
            requested_agents=('agent3',),
            mounted_agents=('agent3',),
            runtime_authority_written_agents=('agent3',),
            diagnostics={'graph_published': False, 'lease_or_lifecycle_written': False},
        ),
        publish_graph_fn=lambda _graph: (_ for _ in ()).throw(RuntimeError('publish failed')),
    )

    assert result.status == 'failed'
    assert result.stage == 'publish_transaction'
    assert result.diagnostics['reason'] == 'service_graph_publish_failed'
    assert result.diagnostics['graph_published'] is False
    assert result.publish_transaction['diagnostics']['signature_rollback']['complete'] is True
    assert app.service_graph is old_graph
    assert app.mount_manager.load_state().to_record() == old_lease
    assert app.lifecycle_store.load().config_signature == old_lifecycle['config_signature']


def test_project_reload_non_dry_run_still_rejected_after_apply_orchestrator(tmp_path: Path) -> None:
    app = _started_app(tmp_path / 'repo-reject', BASE_CONFIG)

    with pytest.raises(ValueError, match='dry_run=true'):
        app.socket_server._handlers['project_reload_config']({'dry_run': False})

    assert app.service_graph.version == 1
    assert app.control_plane_metrics.last_reload_duration_s is None


def _started_app(project_root: Path, config_text: str) -> CcbdApp:
    app = CcbdApp(_project(project_root, config_text), clock=lambda: NOW, pid=4242)
    _store_namespace(app)
    app.lease = app.mount_manager.mark_mounted(
        project_id=app.project_id,
        pid=app.pid,
        socket_path=app.paths.ccbd_socket_path,
        generation=1,
        started_at=NOW,
        config_signature=app.config_identity['config_signature'],
        keeper_pid=app.keeper_pid,
        daemon_instance_id=app.daemon_instance_id,
    )
    app.lifecycle_store.save(
        build_lifecycle(
            project_id=app.project_id,
            occurred_at=NOW,
            desired_state='running',
            phase='mounted',
            generation=1,
            keeper_pid=app.keeper_pid,
            owner_pid=app.pid,
            owner_daemon_instance_id=app.daemon_instance_id,
            config_signature=app.config_identity['config_signature'],
            socket_path=app.paths.ccbd_socket_path,
            namespace_epoch=3,
        )
    )
    return app


def _fail_with(message: str):
    def _fail(*_args, **_kwargs):
        raise AssertionError(message)

    return _fail


def _namespace_patch_result(
    *,
    created_windows: tuple[str, ...] = (),
    created_panes: tuple[str, ...] = ('%3',),
    agent_panes: dict[str, str],
    sidebar_panes: dict[str, str] | None = None,
) -> NamespacePatchApplyResult:
    return NamespacePatchApplyResult(
        status='applied',
        created_windows=created_windows,
        created_panes=created_panes,
        agent_panes=agent_panes,
        sidebar_panes=sidebar_panes or {},
        preserved_before={'agent1': '%1', 'agent2': '%2'},
        preserved_after={'agent1': '%1', 'agent2': '%2'},
        diagnostics={
            'graph_published': False,
            'runtime_authority_written': False,
            'lease_or_lifecycle_written': False,
        },
    )


def _mounting_start_flow(app: CcbdApp, calls: list[dict[str, object]]):
    def _fake_start_flow(**kwargs):
        calls.append(kwargs)
        namespace_agent_panes = dict(kwargs['namespace_agent_panes'])
        for agent_name, pane_id in namespace_agent_panes.items():
            kwargs['runtime_service'].attach(
                agent_name=agent_name,
                workspace_path=str(app.paths.workspace_path(agent_name)),
                backend_type='pane-backed',
                runtime_ref=f'tmux:{pane_id}',
                session_ref=f'session-{agent_name}',
                health='healthy',
                provider='codex',
                terminal_backend='tmux',
                pane_id=pane_id,
                active_pane_id=pane_id,
                pane_state='alive',
                tmux_socket_path=str(kwargs['tmux_socket_path']),
                tmux_window_name='main',
                slot_key=agent_name,
                window_id=kwargs.get('workspace_window_id'),
                workspace_epoch=kwargs.get('workspace_epoch'),
                lifecycle_state='idle',
                managed_by='ccbd',
                binding_source='provider-session',
            )
        return StartFlowSummary(
            project_root=str(app.project_root),
            project_id=app.project_id,
            started=tuple(namespace_agent_panes),
            socket_path=str(app.paths.ccbd_socket_path),
            actions_taken=tuple(f'launch_runtime:{agent}' for agent in namespace_agent_panes),
            agent_results=tuple(
                CcbdStartupAgentResult(
                    agent_name=agent,
                    provider='codex',
                    action='launched',
                    health='healthy',
                    workspace_path=str(app.paths.workspace_path(agent)),
                    runtime_ref=f'tmux:{pane}',
                    session_ref=f'session-{agent}',
                    lifecycle_state='idle',
                    binding_source='provider-session',
                    terminal_backend='tmux',
                    tmux_socket_path=str(kwargs['tmux_socket_path']),
                    tmux_window_name='main',
                    pane_id=pane,
                    active_pane_id=pane,
                    pane_state='alive',
                )
                for agent, pane in namespace_agent_panes.items()
            ),
        )

    return _fake_start_flow


def _namespace(app: CcbdApp):
    return SimpleNamespace(
        project_id=app.project_id,
        namespace_epoch=3,
        tmux_socket_path=str(app.paths.ccbd_tmux_socket_path),
        tmux_session_name=app.paths.ccbd_tmux_session_name,
        workspace_window_name='main',
        workspace_window_id='@main',
        workspace_epoch=1,
        ui_attachable=True,
    )


def _store_namespace(app: CcbdApp) -> None:
    ProjectNamespaceStateStore(app.paths).save(
        ProjectNamespaceState(
            project_id=app.project_id,
            namespace_epoch=3,
            tmux_socket_path=str(app.paths.ccbd_tmux_socket_path),
            tmux_session_name=app.paths.ccbd_tmux_session_name,
            layout_version=3,
            layout_signature=None,
            control_window_name=app.paths.ccbd_tmux_control_window_name,
            control_window_id='@control',
            workspace_window_name='main',
            workspace_window_id='@main',
            workspace_epoch=1,
            ui_attachable=True,
            last_started_at=NOW,
        )
    )


def _project(project_root: Path, config_text: str) -> Path:
    project_root.mkdir(parents=True, exist_ok=True)
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text, encoding='utf-8')
    return project_root


def _load_config(project_root: Path, config_text: str):
    return load_project_config(_project(project_root, config_text)).config
