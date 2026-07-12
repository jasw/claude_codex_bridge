from __future__ import annotations

import json
import hashlib
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

try:  # pragma: no cover - version shim
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib

from agents.models import AgentSpec, PermissionMode, ProviderProfileSpec, QueuePolicy, RestoreMode, RuntimeMode, WorkspaceMode
from cli.services.provider_hooks import prepare_provider_workspace
from provider_core.caller_env import caller_context_env
import provider_core.source_home as source_home_module
from provider_hooks.settings import build_hook_command, install_workspace_activity_hooks, install_workspace_completion_hooks
from storage.paths import PathLayout


REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTDESK_ROLE_ROOT = (
    REPO_ROOT
    / 'docs'
    / 'plantree'
    / 'plans'
    / 'agentic-loop-workflow'
    / 'drafts'
    / 'agentroles.ccb_frontdesk'
)
TASK_DETAILER_ROLE_ROOT = FRONTDESK_ROLE_ROOT.parent / 'agentroles.ccb_task_detailer'


def _spec(
    name: str,
    provider: str = "claude",
    *,
    role: str | None = None,
    provider_profile: ProviderProfileSpec | None = None,
) -> AgentSpec:
    return AgentSpec(
        name=name,
        provider=provider,
        role=role,
        target='.',
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.AUTO,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
        provider_profile=provider_profile or ProviderProfileSpec(),
    )


def _write_project_memory(project_root: Path, text: str) -> None:
    path = project_root / '.ccb' / 'ccb_memory.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _install_frontdesk_role(tmp_path: Path, monkeypatch) -> None:
    role_store = tmp_path / '.roles'
    monkeypatch.setenv('AGENT_ROLES_STORE', str(role_store))
    installed = role_store / 'installed' / 'agentroles.ccb_frontdesk' / 'current'
    installed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FRONTDESK_ROLE_ROOT, installed)


def _install_task_detailer_role(tmp_path: Path, monkeypatch) -> None:
    role_store = tmp_path / '.roles'
    monkeypatch.setenv('AGENT_ROLES_STORE', str(role_store))
    installed = role_store / 'installed' / 'agentroles.ccb_task_detailer' / 'current'
    installed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TASK_DETAILER_ROLE_ROOT, installed)


def test_build_hook_command_includes_completion_dir_and_workspace(tmp_path: Path) -> None:
    command = build_hook_command(
        provider='claude',
        script_path=tmp_path / 'bin' / 'ccb-provider-finish-hook',
        python_executable='/usr/bin/python3',
        completion_dir=tmp_path / 'completion',
        agent_name='agent1',
        workspace_path=tmp_path / 'workspace',
    )

    assert '--provider claude' in command
    assert '--agent-name agent1' in command
    assert '--completion-dir' in command
    assert '--workspace' in command


def test_install_claude_hooks_writes_managed_home_settings_only(tmp_path: Path) -> None:
    workspace = tmp_path / 'workspace'
    home_root = tmp_path / 'claude-home'
    command = '/usr/bin/python3 /tmp/ccb-provider-finish-hook --provider claude'

    settings_path = install_workspace_completion_hooks(
        provider='claude',
        workspace_path=workspace,
        home_root=home_root,
        command=command,
    )

    assert settings_path == home_root / '.claude' / 'settings.json'
    data = json.loads(settings_path.read_text(encoding='utf-8'))
    assert data['hooks']['Stop'][0]['hooks'][0]['command'] == command
    assert not (workspace / '.claude').exists()


def test_install_claude_hooks_preserves_existing_entries_without_duplication(tmp_path: Path) -> None:
    workspace = tmp_path / 'workspace'
    home_root = tmp_path / 'claude-home'
    settings_path = home_root / '.claude' / 'settings.json'
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    command = '/usr/bin/python3 /tmp/ccb-provider-finish-hook --provider claude'
    settings_path.write_text(
        json.dumps(
            {
                'hooks': {
                    'Stop': [
                        {'hooks': [{'type': 'command', 'command': 'echo existing'}]},
                        {'hooks': [{'type': 'command', 'command': command}]},
                    ]
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    install_workspace_completion_hooks(
        provider='claude',
        workspace_path=workspace,
        home_root=home_root,
        command=command,
    )

    data = json.loads(settings_path.read_text(encoding='utf-8'))
    assert len(data['hooks']['Stop']) == 2
    assert not (workspace / '.claude').exists()


def test_install_claude_hooks_prunes_stale_ccb_finish_hooks(tmp_path: Path) -> None:
    workspace = tmp_path / 'workspace'
    home_root = tmp_path / 'claude-home'
    settings_path = home_root / '.claude' / 'settings.json'
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    command = '/usr/bin/python3 /current/bin/ccb-provider-finish-hook --provider claude'
    stale_command = '/usr/bin/python3 /old/bin/ccb-provider-finish-hook --provider claude'
    settings_path.write_text(
        json.dumps(
            {
                'hooks': {
                    'Stop': [
                        {'hooks': [{'type': 'command', 'command': 'echo existing'}]},
                        {'hooks': [{'type': 'command', 'command': stale_command}]},
                    ]
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    install_workspace_completion_hooks(
        provider='claude',
        workspace_path=workspace,
        home_root=home_root,
        command=command,
    )

    data = json.loads(settings_path.read_text(encoding='utf-8'))
    commands = [
        hook['command']
        for group in data['hooks']['Stop']
        for hook in group.get('hooks', [])
        if isinstance(hook, dict)
    ]
    assert commands == ['echo existing', command]


def test_install_claude_hooks_trusts_workspace_in_managed_home(tmp_path: Path) -> None:
    workspace = tmp_path / 'workspace'
    home_root = tmp_path / 'claude-home'
    command = '/usr/bin/python3 /tmp/ccb-provider-finish-hook --provider claude'

    install_workspace_completion_hooks(
        provider='claude',
        workspace_path=workspace,
        home_root=home_root,
        command=command,
    )

    trust_path = home_root / '.claude.json'
    trust_data = json.loads(trust_path.read_text(encoding='utf-8'))
    assert trust_data[str(workspace.resolve())]['hasTrustDialogAccepted'] is True
    assert trust_data['projects'][str(workspace.resolve())]['hasTrustDialogAccepted'] is True
    assert not (workspace / '.claude').exists()


def test_prepare_provider_workspace_materializes_claude_settings_before_hooks(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_token = 'sk-test-abcdefghijklmnopqrstuvwxyz1234567890'
    system_settings = system_home / '.claude' / 'settings.json'
    system_settings.parent.mkdir(parents=True, exist_ok=True)
    system_settings.write_text(
        json.dumps(
            {
                'env': {
                    'ANTHROPIC_AUTH_TOKEN': system_token,
                    'ANTHROPIC_BASE_URL': 'https://claude.example.test',
                },
                'theme': 'light',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('HOME', str(system_home))

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('agent1'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'claude' / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    settings_path = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'claude' / 'home' / '.claude' / 'settings.json'
    payload = json.loads(settings_path.read_text(encoding='utf-8'))
    assert payload['env']['ANTHROPIC_AUTH_TOKEN'] == system_token
    assert payload['env']['ANTHROPIC_API_KEY'] == system_token
    assert payload['env']['ANTHROPIC_BASE_URL'] == 'https://claude.example.test'
    assert payload['theme'] == 'light'
    assert payload['allowedTools'] == []
    assert payload['hooks']['Stop'][0]['hooks'][0]['command']
    trust_path = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'claude' / 'home' / '.claude.json'
    trust_text = trust_path.read_text(encoding='utf-8')
    trust_payload = json.loads(trust_text)
    assert system_token not in trust_text
    assert trust_payload['customApiKeyResponses']['approved'] == [system_token[-20:]]
    assert not (workspace / '.claude').exists()


def test_prepare_provider_workspace_accepts_claude_bypass_permission_prompt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    (system_home / '.claude').mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('HOME', str(system_home))

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('agent1'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'claude' / 'completion',
        agent_name='agent1',
        refresh_profile=True,
        auto_permission=True,
    )

    trust_path = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'claude' / 'home' / '.claude.json'
    payload = json.loads(trust_path.read_text(encoding='utf-8'))
    workspace_key = str(workspace.resolve())
    assert payload['bypassPermissionsModeAccepted'] is True
    assert payload[workspace_key]['hasTrustDialogAccepted'] is True
    assert payload[workspace_key]['allowedTools'] == []
    assert payload['projects'][workspace_key]['hasTrustDialogAccepted'] is True
    assert payload['projects'][workspace_key]['allowedTools'] == []


def test_prepare_provider_workspace_limits_frontdesk_to_planner_silence_ask(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_frontdesk_role(tmp_path, monkeypatch)
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text(
        '\n'.join(
            [
                'version = 2',
                'entry_window = "main"',
                '',
                '[windows]',
                'main = "frontdesk:claude"',
                '',
                '[agents.frontdesk]',
                'role = "agentroles.ccb_frontdesk"',
            ]
        )
        + '\n',
        encoding='utf-8',
    )
    system_home = tmp_path / 'system-home'
    system_claude = system_home / '.claude'
    (system_claude / 'commands').mkdir(parents=True, exist_ok=True)
    (system_claude / 'skills' / 'ask').mkdir(parents=True, exist_ok=True)
    (system_claude / 'commands' / 'unsafe.md').write_text('run arbitrary ccb commands\n', encoding='utf-8')
    (system_claude / 'skills' / 'ask' / 'SKILL.md').write_text('generic ask skill\n', encoding='utf-8')
    (system_claude / 'settings.json').write_text(
        json.dumps(
            {
                'permissions': {
                    'allow': ['Bash(ccb *)', 'Bash(bash *)'],
                    'deny': [],
                },
                'theme': 'light',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('HOME', str(system_home))

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('frontdesk', provider='claude', role='agentroles.ccb_frontdesk'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'frontdesk' / 'provider-runtime' / 'claude' / 'completion',
        agent_name='frontdesk',
        refresh_profile=True,
        auto_permission=True,
    )

    home = project_root / '.ccb' / 'agents' / 'frontdesk' / 'provider-state' / 'claude' / 'home'
    settings_path = home / '.claude' / 'settings.json'
    payload = json.loads(settings_path.read_text(encoding='utf-8'))
    assert payload['permissions'] == {
        'allow': ['Bash(ask --silence --compact --inline-request --task-id *)'],
        'deny': [],
    }
    assert payload['theme'] == 'light'
    assert not (home / '.claude' / 'commands' / 'unsafe.md').exists()
    assert not (home / '.claude' / 'skills' / 'ask' / 'SKILL.md').exists()
    assert (home / '.claude' / 'skills' / 'frontdesk-intake' / 'SKILL.md').is_file()
    assert not (workspace / '.claude').exists()


def test_prepare_provider_workspace_installs_source_test_ccb_shim_for_provider_commands(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_frontdesk_role(tmp_path, monkeypatch)
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    monkeypatch.setenv('CCB_TEST_ENTRYPOINT', '1')

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('frontdesk', provider='claude', role='agentroles.ccb_frontdesk'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'frontdesk' / 'provider-runtime' / 'claude' / 'completion',
        agent_name='frontdesk',
        refresh_profile=True,
    )

    shim = project_root / '.ccb' / 'bin' / 'ccb'
    assert shim.is_file()
    shim_text = shim.read_text(encoding='utf-8')
    assert 'ccb_test' in shim_text
    assert '"$@"' in shim_text
    env = caller_context_env(
        actor='frontdesk',
        runtime_dir=project_root / '.ccb' / 'agents' / 'frontdesk' / 'provider-runtime' / 'claude',
        launch_session_id='session-frontdesk',
    )
    assert str(env['PATH']).split(':', 1)[0] == str(project_root / '.ccb' / 'bin')


def test_prepare_provider_workspace_materializes_frontdesk_codex_with_hard_command_surface(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_frontdesk_role(tmp_path, monkeypatch)
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text(
        '\n'.join(
            [
                'version = 2',
                'entry_window = "main"',
                '',
                '[windows]',
                'main = "frontdesk:codex"',
                '',
                '[agents.frontdesk]',
                'role = "agentroles.ccb_frontdesk"',
            ]
        )
        + '\n',
        encoding='utf-8',
    )
    system_home = tmp_path / 'system-home'
    system_codex = system_home / '.codex'
    (system_codex / 'skills' / 'ask').mkdir(parents=True, exist_ok=True)
    (system_codex / 'commands').mkdir(parents=True, exist_ok=True)
    (system_codex / 'AGENTS.md').write_text('system codex memory\n', encoding='utf-8')
    (system_codex / 'skills' / 'ask' / 'SKILL.md').write_text('generic ask skill\n', encoding='utf-8')
    (system_codex / 'commands' / 'unsafe.md').write_text('run arbitrary ccb commands\n', encoding='utf-8')
    (system_codex / 'config.toml').write_text(
        '[hooks]\n'
        'UserPromptSubmit = [{ command = "echo inherited-hook" }]\n',
        encoding='utf-8',
    )
    stale_home = (
        project_root
        / '.ccb'
        / 'agents'
        / 'frontdesk'
        / 'provider-state'
        / 'codex'
        / 'home'
    )
    stale_skill = stale_home / 'skills' / 'bug-fix-prove-it'
    stale_skill.mkdir(parents=True, exist_ok=True)
    (stale_skill / 'SKILL.md').write_text('stale coder skill\n', encoding='utf-8')
    (stale_home / 'skills' / 'bug-fix-prove-it.ccb-projection.json').write_text(
        json.dumps(
            {
                'schema_version': 1,
                'record_type': 'ccb_projected_asset',
                'label': 'codex-role-skill:agentroles.coder:bug-fix-prove-it',
                'source': '/tmp/stale-coder-skill',
                'mode': 'copy',
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        + '\n',
        encoding='utf-8',
    )
    monkeypatch.setenv('CODEX_HOME', str(system_codex))

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('frontdesk', provider='codex', role='agentroles.ccb_frontdesk'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'frontdesk' / 'provider-runtime' / 'codex' / 'completion',
        agent_name='frontdesk',
        refresh_profile=True,
    )

    home = project_root / '.ccb' / 'agents' / 'frontdesk' / 'provider-state' / 'codex' / 'home'
    assert (home / 'AGENTS.md').is_file()
    assert (home / 'skills' / 'frontdesk-intake' / 'SKILL.md').is_file()
    assert not (home / 'skills' / 'ask' / 'SKILL.md').exists()
    assert not (home / 'skills' / 'bug-fix-prove-it' / 'SKILL.md').exists()
    assert not (home / 'skills' / 'bug-fix-prove-it.ccb-projection.json').exists()
    assert not (home / 'commands' / 'unsafe.md').exists()
    config_text = (home / 'config.toml').read_text(encoding='utf-8')
    assert 'inherited-hook' not in config_text
    config = tomllib.loads(config_text)
    assert config['approval_policy'] == 'never'
    assert config['sandbox_mode'] == 'read-only'
    assert config['features']['apps'] is False
    assert config['features']['multi_agent'] is False
    assert config['features']['shell_tool'] is False
    assert config['features']['unified_exec'] is False
    assert set(config['mcp_servers']) == {'ccb_role_command'}
    role_server = config['mcp_servers']['ccb_role_command']
    assert role_server['command']
    assert role_server['args'][0].endswith('/mcp/ccb-role-command/server.py')
    assert role_server['required'] is True
    assert role_server['enabled_tools'] == ['ccb_frontdesk_ask_planner']
    assert role_server['default_tools_approval_mode'] == 'prompt'
    assert role_server['tools']['ccb_frontdesk_ask_planner']['approval_mode'] == 'approve'
    assert role_server['env']['CCB_CALLER_ACTOR'] == 'frontdesk'
    assert role_server['env']['CCB_CALLER_PROJECT_ROOT'] == str(project_root.resolve())


def test_prepare_provider_workspace_rejects_codex_frontdesk_without_managed_capability(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_frontdesk_role(tmp_path, monkeypatch)
    role_store = tmp_path / '.roles'
    surface = (
        role_store
        / 'installed'
        / 'agentroles.ccb_frontdesk'
        / 'current'
        / 'adapters'
        / 'ccb'
        / 'command-surface.toml'
    )
    text = surface.read_text(encoding='utf-8')
    text = text.replace(
        '[provider_tools]\ncodex = "ccb_frontdesk_ask_planner"\n\n',
        '',
    )
    surface.write_text(text, encoding='utf-8')
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match='requires a managed provider capability'):
        prepare_provider_workspace(
            layout=PathLayout(project_root),
            spec=_spec('frontdesk', provider='codex', role='agentroles.ccb_frontdesk'),
            workspace_path=workspace,
            completion_dir=(
                project_root
                / '.ccb'
                / 'agents'
                / 'frontdesk'
                / 'provider-runtime'
                / 'codex'
                / 'completion'
            ),
            agent_name='frontdesk',
            refresh_profile=True,
        )


def test_prepare_provider_workspace_materializes_task_detailer_codex_replan_capability(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_task_detailer_role(tmp_path, monkeypatch)
    project_root = tmp_path / 'repo-detailer'
    workspace = project_root / 'workspace'
    (project_root / '.ccb').mkdir(parents=True)
    (project_root / '.ccb' / 'ccb.config').write_text(
        'task_detailer:codex; planner:codex\n',
        encoding='utf-8',
    )

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('task_detailer', provider='codex', role='agentroles.ccb_task_detailer'),
        workspace_path=workspace,
        completion_dir=(
            project_root
            / '.ccb'
            / 'agents'
            / 'task_detailer'
            / 'provider-runtime'
            / 'codex'
            / 'completion'
        ),
        agent_name='task_detailer',
        refresh_profile=True,
    )

    home = project_root / '.ccb' / 'agents' / 'task_detailer' / 'provider-state' / 'codex' / 'home'
    config = tomllib.loads((home / 'config.toml').read_text(encoding='utf-8'))
    assert config['approval_policy'] == 'never'
    assert config['sandbox_mode'] == 'read-only'
    role_server = config['mcp_servers']['ccb_role_command']
    assert role_server['enabled_tools'] == ['ccb_task_detailer_replan_planner']
    assert role_server['tools']['ccb_task_detailer_replan_planner']['approval_mode'] == 'approve'
    assert role_server['env']['CCB_CALLER_ACTOR'] == 'task_detailer'
    assert not (home / 'skills' / 'ask' / 'SKILL.md').exists()


def test_prepare_provider_workspace_allows_codex_role_with_empty_command_allowlist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    role_store = tmp_path / '.roles'
    monkeypatch.setenv('AGENT_ROLES_STORE', str(role_store))
    installed = role_store / 'installed' / 'agentroles.empty_surface' / 'current'
    (installed / 'adapters' / 'ccb').mkdir(parents=True)
    (installed / 'role.toml').write_text(
        '\n'.join(
            (
                'id = "agentroles.empty_surface"',
                'name = "Empty Surface"',
                'description = "No command capability."',
                'version = "1.0.0"',
                'role_version = "1.0.0"',
                'schema_version = 1',
                'memory = "memory.md"',
                '',
                '[adapters.ccb]',
                'command_surface = "adapters/ccb/command-surface.toml"',
            )
        )
        + '\n',
        encoding='utf-8',
    )
    (installed / 'memory.md').write_text('No commands.\n', encoding='utf-8')
    (installed / 'adapters' / 'ccb' / 'command-surface.toml').write_text(
        '\n'.join(
            (
                'schema = "ccb-command-surface/v1"',
                'mode = "deny_all_except"',
                'enforcement = "required"',
                'if_unsupported = "fail_mount"',
                'generic_shell = false',
                'generic_ccb = false',
                'supported_providers = ["codex"]',
                'allowed_effects = ["reply_only"]',
                'forbidden_effects = ["shell_exec"]',
            )
        )
        + '\n',
        encoding='utf-8',
    )
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    (project_root / '.ccb').mkdir(parents=True)

    profile = prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('empty', provider='codex', role='agentroles.empty_surface'),
        workspace_path=workspace,
        completion_dir=(
            project_root
            / '.ccb'
            / 'agents'
            / 'empty'
            / 'provider-runtime'
            / 'codex'
            / 'completion'
        ),
        agent_name='empty',
        refresh_profile=True,
    )

    assert profile.provider == 'codex'
    config = tomllib.loads(
        (
            project_root
            / '.ccb'
            / 'agents'
            / 'empty'
            / 'provider-state'
            / 'codex'
            / 'home'
            / 'config.toml'
        ).read_text(encoding='utf-8')
    )
    assert 'ccb_role_command' not in config.get('mcp_servers', {})


def test_prepare_provider_workspace_rejects_fake_frontdesk_hard_command_surface_without_source_test(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_frontdesk_role(tmp_path, monkeypatch)
    monkeypatch.delenv('CCB_TEST_ENTRYPOINT', raising=False)
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match='role command surface requires hard provider enforcement'):
        prepare_provider_workspace(
            layout=PathLayout(project_root),
            spec=_spec('frontdesk', provider='fake', role='agentroles.ccb_frontdesk'),
            workspace_path=workspace,
            completion_dir=project_root / '.ccb' / 'agents' / 'frontdesk' / 'provider-runtime' / 'fake' / 'completion',
            agent_name='frontdesk',
            refresh_profile=True,
        )


def test_prepare_provider_workspace_allows_fake_frontdesk_hard_command_surface_for_source_test(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_frontdesk_role(tmp_path, monkeypatch)
    monkeypatch.setenv('CCB_TEST_ENTRYPOINT', '1')
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)

    profile = prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('frontdesk', provider='fake', role='agentroles.ccb_frontdesk'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'frontdesk' / 'provider-runtime' / 'fake' / 'completion',
        agent_name='frontdesk',
        refresh_profile=True,
    )

    assert profile.provider == 'fake'
    shim = project_root / '.ccb' / 'bin' / 'ccb'
    assert shim.is_file()
    assert 'ccb_test' in shim.read_text(encoding='utf-8')


def test_prepare_provider_workspace_materializes_claude_mcp_from_source_home(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / '.ccb' / 'workspaces' / 'clauder'
    system_home = tmp_path / 'system-home'
    system_settings = system_home / '.claude' / 'settings.json'
    system_trust = system_home / '.claude.json'
    system_settings.parent.mkdir(parents=True, exist_ok=True)
    system_settings.write_text(json.dumps({'theme': 'light'}, ensure_ascii=False, indent=2), encoding='utf-8')
    project_root.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    source_project_key = str(project_root.resolve())
    target_project_key = str(workspace.resolve())
    system_trust.write_text(
        json.dumps(
            {
                'mcpServers': {'global-tool': {'command': 'global-mcp'}},
                'projects': {
                    source_project_key: {
                        'mcpServers': {'project-tool': {'command': 'project-mcp'}},
                        'enabledMcpjsonServers': ['project-tool'],
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('HOME', str(system_home))

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('agent1'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'claude' / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    trust_path = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'claude' / 'home' / '.claude.json'
    payload = json.loads(trust_path.read_text(encoding='utf-8'))
    assert payload['mcpServers']['global-tool']['command'] == 'global-mcp'
    assert payload['projects'][target_project_key]['mcpServers']['project-tool']['command'] == 'project-mcp'
    assert payload['projects'][target_project_key]['enabledMcpjsonServers'] == ['project-tool']
    assert source_project_key not in payload['projects']
    assert not (workspace / '.claude').exists()


def test_prepare_provider_workspace_inherits_claude_hooks_from_source_home(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_settings = system_home / '.claude' / 'settings.json'
    system_settings.parent.mkdir(parents=True, exist_ok=True)
    system_settings.write_text(
        json.dumps(
            {
                'hooks': {
                    'Stop': [
                        {'hooks': [{'type': 'command', 'command': 'echo source-stop-hook'}]},
                    ],
                    'UserPromptSubmit': [
                        {'hooks': [{'type': 'command', 'command': 'echo source-prompt-hook'}]},
                    ],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('HOME', str(system_home))

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('agent1'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'claude' / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    settings_path = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'claude' / 'home' / '.claude' / 'settings.json'
    payload = json.loads(settings_path.read_text(encoding='utf-8'))
    stop_commands = [
        hook['command']
        for group in payload['hooks']['Stop']
        for hook in group.get('hooks', [])
        if isinstance(hook, dict)
    ]
    prompt_commands = [
        hook['command']
        for group in payload['hooks']['UserPromptSubmit']
        for hook in group.get('hooks', [])
        if isinstance(hook, dict)
    ]
    assert 'echo source-stop-hook' in stop_commands
    assert any('ccb-provider-finish-hook' in command for command in stop_commands)
    assert any('ccb-provider-activity-hook' in command for command in stop_commands)
    assert 'echo source-prompt-hook' in prompt_commands
    assert any('ccb-provider-activity-hook' in command for command in prompt_commands)
    assert not (workspace / '.claude').exists()


def test_prepare_provider_workspace_materializes_claude_activity_hooks(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_settings = system_home / '.claude' / 'settings.json'
    system_settings.parent.mkdir(parents=True, exist_ok=True)
    system_settings.write_text(json.dumps({'theme': 'light'}, ensure_ascii=False, indent=2), encoding='utf-8')
    monkeypatch.setenv('HOME', str(system_home))
    layout = PathLayout(project_root)
    runtime_dir = layout.agent_provider_runtime_dir('agent1', 'claude')

    for refresh_profile in (True, False):
        prepare_provider_workspace(
            layout=layout,
            spec=_spec('agent1'),
            workspace_path=workspace,
            completion_dir=runtime_dir / 'completion',
            agent_name='agent1',
            refresh_profile=refresh_profile,
        )

    settings_path = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'claude' / 'home' / '.claude' / 'settings.json'
    payload = json.loads(settings_path.read_text(encoding='utf-8'))
    for event_name in (
        'SessionStart',
        'UserPromptSubmit',
        'PreToolUse',
        'PermissionRequest',
        'Notification',
        'PostToolUse',
        'Stop',
    ):
        commands = [
            hook['command']
            for group in payload['hooks'][event_name]
            for hook in group.get('hooks', [])
            if isinstance(hook, dict)
        ]
        activity_commands = [command for command in commands if 'ccb-provider-activity-hook' in command]
        assert len(activity_commands) == 1
        assert '--provider claude' in activity_commands[0]
        assert '--agent-name agent1' in activity_commands[0]
        assert f'--runtime-dir {runtime_dir}' in activity_commands[0]
        assert f'--workspace {workspace}' in activity_commands[0]
        assert layout.project_id in activity_commands[0]
    stop_commands = [
        hook['command']
        for group in payload['hooks']['Stop']
        for hook in group.get('hooks', [])
        if isinstance(hook, dict)
    ]
    assert any('ccb-provider-finish-hook' in command for command in stop_commands)
    assert not (workspace / '.claude').exists()


def test_install_claude_activity_hooks_prunes_stale_ccb_activity_hooks(tmp_path: Path) -> None:
    workspace = tmp_path / 'workspace'
    home_root = tmp_path / 'claude-home'
    settings_path = home_root / '.claude' / 'settings.json'
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    activity_command = '/usr/bin/python3 /current/bin/ccb-provider-activity-hook --provider claude'
    stale_activity_command = '/usr/bin/python3 /old/bin/ccb-provider-activity-hook --provider claude'
    finish_command = '/usr/bin/python3 /old/bin/ccb-provider-finish-hook --provider claude'
    settings_path.write_text(
        json.dumps(
            {
                'hooks': {
                    'Stop': [
                        {'hooks': [{'type': 'command', 'command': finish_command}]},
                        {'hooks': [{'type': 'command', 'command': stale_activity_command}]},
                    ],
                    'PostToolUse': [
                        {'hooks': [{'type': 'command', 'command': 'echo existing'}]},
                        {'hooks': [{'type': 'command', 'command': stale_activity_command}]},
                    ],
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    install_workspace_activity_hooks(
        provider='claude',
        workspace_path=workspace,
        home_root=home_root,
        command=activity_command,
    )

    data = json.loads(settings_path.read_text(encoding='utf-8'))
    stop_commands = [
        hook['command']
        for group in data['hooks']['Stop']
        for hook in group.get('hooks', [])
        if isinstance(hook, dict)
    ]
    post_tool_commands = [
        hook['command']
        for group in data['hooks']['PostToolUse']
        for hook in group.get('hooks', [])
        if isinstance(hook, dict)
    ]
    assert finish_command in stop_commands
    assert activity_command in stop_commands
    assert stale_activity_command not in stop_commands
    assert post_tool_commands == ['echo existing', activity_command]


def test_prepare_provider_workspace_materializes_claude_memory_bundle_before_hooks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_claude = system_home / '.claude'
    system_claude.mkdir(parents=True, exist_ok=True)
    (system_claude / 'CLAUDE.md').write_text('system claude memory\n', encoding='utf-8')
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ccb memory\n')
    (project_root / 'CLAUDE.md').write_text('project claude memory\n', encoding='utf-8')
    private_memory = project_root / '.ccb' / 'agents' / 'agent1' / 'memory.md'
    private_memory.parent.mkdir(parents=True, exist_ok=True)
    private_memory.write_text('agent private memory\n', encoding='utf-8')
    monkeypatch.setenv('HOME', str(system_home))

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('agent1'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'claude' / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    memory_path = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'claude' / 'home' / '.claude' / 'CLAUDE.md'
    text = memory_path.read_text(encoding='utf-8')
    assert text.startswith('# CCB Managed Agent Memory')
    assert 'system claude memory' in text
    assert 'shared ccb memory' in text
    assert 'project claude memory' not in text
    assert 'agent private memory' in text


def test_prepare_provider_workspace_records_claude_memory_projection_event_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_claude = system_home / '.claude'
    system_claude.mkdir(parents=True, exist_ok=True)
    (system_claude / 'CLAUDE.md').write_text('system claude memory\n', encoding='utf-8')
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ccb memory\n')
    monkeypatch.setenv('HOME', str(system_home))
    layout = PathLayout(project_root)

    for refresh_profile in (True, False):
        prepare_provider_workspace(
            layout=layout,
            spec=_spec('agent1'),
            workspace_path=workspace,
            completion_dir=layout.agent_provider_runtime_dir('agent1', 'claude') / 'completion',
            agent_name='agent1',
            refresh_profile=refresh_profile,
        )

    events = [
        json.loads(line)
        for line in layout.agent_events_path('agent1').read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    memory_events = [event for event in events if str(event.get('event_type', '')).startswith('claude_memory_projection_')]
    assert len(memory_events) == 1
    assert memory_events[0]['event_type'] == 'claude_memory_projection_ok'
    assert memory_events[0]['reason'] == 'written'
    assert memory_events[0]['sha256']
    assert memory_events[0]['source_count'] >= 2
    marker_path = layout.agent_provider_runtime_dir('agent1', 'claude') / 'claude-memory-projection.json'
    marker = json.loads(marker_path.read_text(encoding='utf-8'))
    assert marker['status'] == 'ok'


def test_prepare_provider_workspace_records_codex_memory_projection_event_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_codex = system_home / '.codex'
    system_codex.mkdir(parents=True, exist_ok=True)
    (system_codex / 'AGENTS.md').write_text('system codex memory\n', encoding='utf-8')
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ccb memory\n')
    monkeypatch.setenv('CODEX_HOME', str(system_codex))
    layout = PathLayout(project_root)

    for refresh_profile in (True, False):
        prepare_provider_workspace(
            layout=layout,
            spec=_spec('agent1', provider='codex'),
            workspace_path=workspace,
            completion_dir=layout.agent_provider_runtime_dir('agent1', 'codex') / 'completion',
            agent_name='agent1',
            refresh_profile=refresh_profile,
        )

    memory_path = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'codex' / 'home' / 'AGENTS.md'
    text = memory_path.read_text(encoding='utf-8')
    assert text.startswith('# CCB Managed Agent Memory')
    assert 'system codex memory' in text
    assert 'shared ccb memory' in text

    events = [
        json.loads(line)
        for line in layout.agent_events_path('agent1').read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    memory_events = [event for event in events if str(event.get('event_type', '')).startswith('codex_memory_projection_')]
    assert len(memory_events) == 1
    assert memory_events[0]['event_type'] == 'codex_memory_projection_ok'
    assert memory_events[0]['reason'] == 'written'
    assert memory_events[0]['sha256']
    assert memory_events[0]['source_count'] >= 2
    marker_path = layout.agent_provider_runtime_dir('agent1', 'codex') / 'codex-memory-projection.json'
    marker = json.loads(marker_path.read_text(encoding='utf-8'))
    assert marker['status'] == 'ok'


def test_prepare_provider_workspace_does_not_materialize_codex_activity_hooks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_codex = system_home / '.codex'
    system_codex.mkdir(parents=True, exist_ok=True)
    (system_codex / 'AGENTS.md').write_text('system codex memory\n', encoding='utf-8')
    (system_codex / 'config.toml').write_text(
        '''
model = "gpt-test"

[hooks]
[[hooks.UserPromptSubmit]]
[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = "echo external-hook"
''',
        encoding='utf-8',
    )
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ccb memory\n')
    monkeypatch.setenv('CODEX_HOME', str(system_codex))
    layout = PathLayout(project_root)
    runtime_dir = layout.agent_provider_runtime_dir('agent1', 'codex')

    prepare_provider_workspace(
        layout=layout,
        spec=_spec('agent1', provider='codex'),
        workspace_path=workspace,
        completion_dir=runtime_dir / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    codex_home = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'codex' / 'home'
    hooks_path = codex_home / 'hooks.json'
    hooks_payload = json.loads(hooks_path.read_text(encoding='utf-8'))
    assert hooks_payload == {'hooks': {}}

    config_path = codex_home / 'config.toml'
    config_text = config_path.read_text(encoding='utf-8')
    assert 'external-hook' not in config_text
    config = tomllib.loads(config_text)
    assert config['hooks']['state'] == {}
    assert not (workspace / '.codex').exists()


def test_prepare_provider_workspace_preserves_allowed_codex_hindsight_hooks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_codex = system_home / '.codex'
    hindsight_root = system_home / '.hindsight' / 'codex' / 'scripts'
    system_codex.mkdir(parents=True, exist_ok=True)
    hindsight_root.mkdir(parents=True, exist_ok=True)
    (system_codex / 'AGENTS.md').write_text('system codex memory\n', encoding='utf-8')
    (system_codex / 'config.toml').write_text('model = "gpt-test"\n', encoding='utf-8')
    (system_codex / 'hooks.json').write_text(
        json.dumps(
            {
                'hooks': {
                    'SessionStart': [
                        {'hooks': [{'type': 'command', 'command': f'python3 "{hindsight_root / "session_start.py"}"', 'timeout': 5}]}
                    ],
                    'UserPromptSubmit': [
                        {'hooks': [{'type': 'command', 'command': f'python3 "{hindsight_root / "recall.py"}"', 'timeout': 12}]}
                    ],
                    'Stop': [
                        {'hooks': [{'type': 'command', 'command': f'python3 "{hindsight_root / "retain.py"}"', 'timeout': 30}]}
                    ],
                    'PostToolUse': [
                        {'hooks': [{'type': 'command', 'command': 'echo unmanaged-root-hook'}]}
                    ],
                }
            },
            indent=2,
        ),
        encoding='utf-8',
    )
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ccb memory\n')
    monkeypatch.setenv('CODEX_HOME', str(system_codex))
    layout = PathLayout(project_root)
    runtime_dir = layout.agent_provider_runtime_dir('agent1', 'codex')

    prepare_provider_workspace(
        layout=layout,
        spec=_spec('agent1', provider='codex'),
        workspace_path=workspace,
        completion_dir=runtime_dir / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    codex_home = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'codex' / 'home'
    hooks_payload = json.loads((codex_home / 'hooks.json').read_text(encoding='utf-8'))
    user_prompt_commands = [
        hook['command']
        for group in hooks_payload['hooks']['UserPromptSubmit']
        for hook in group['hooks']
    ]
    assert not any('ccb-provider-activity-hook' in command for command in user_prompt_commands)
    assert any('.hindsight/codex/scripts/recall.py' in command for command in user_prompt_commands)
    assert not any('unmanaged-root-hook' in command for commands in hooks_payload['hooks'].values() for group in commands for command in [group['hooks'][0]['command']])
    session_start_handlers = [
        hook
        for group in hooks_payload['hooks']['SessionStart']
        for hook in group['hooks']
        if '.hindsight/codex/scripts/session_start.py' in str(hook.get('command') or '')
    ]
    stop_handlers = [
        hook
        for group in hooks_payload['hooks']['Stop']
        for hook in group['hooks']
        if '.hindsight/codex/scripts/retain.py' in str(hook.get('command') or '')
    ]
    assert session_start_handlers[0]['timeout'] == 5
    assert stop_handlers[0]['timeout'] == 30

    config = tomllib.loads((codex_home / 'config.toml').read_text(encoding='utf-8'))
    state = config['hooks']['state']
    assert any(key.endswith(':user_prompt_submit:0:0') for key in state)
    assert len(state) == 3


def test_prepare_provider_workspace_preserves_omx_native_codex_hooks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_codex = system_home / '.codex'
    system_codex.mkdir(parents=True, exist_ok=True)
    (system_codex / 'AGENTS.md').write_text('system codex memory\n', encoding='utf-8')
    (system_codex / 'config.toml').write_text('model = "gpt-test"\n', encoding='utf-8')
    omx_command = '"/usr/bin/node" "/usr/lib/node_modules/oh-my-codex/dist/scripts/codex-native-hook.js"'
    (system_codex / 'hooks.json').write_text(
        json.dumps(
            {
                'hooks': {
                    'SessionStart': [{'matcher': 'startup|resume|clear', 'hooks': [{'type': 'command', 'command': omx_command}]}],
                    'UserPromptSubmit': [{'hooks': [{'type': 'command', 'command': omx_command}]}],
                    'PreToolUse': [{'hooks': [{'type': 'command', 'command': omx_command}]}],
                    'PostToolUse': [{'hooks': [{'type': 'command', 'command': omx_command}]}],
                    'PreCompact': [{'hooks': [{'type': 'command', 'command': omx_command}]}],
                    'PostCompact': [{'hooks': [{'type': 'command', 'command': omx_command}]}],
                    'Stop': [{'hooks': [{'type': 'command', 'command': omx_command, 'timeout': 30}]}],
                    'Notification': [{'hooks': [{'type': 'command', 'command': omx_command}]}],
                }
            },
            indent=2,
        ),
        encoding='utf-8',
    )
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ccb memory\n')
    monkeypatch.setenv('CODEX_HOME', str(system_codex))
    layout = PathLayout(project_root)
    runtime_dir = layout.agent_provider_runtime_dir('agent1', 'codex')

    prepare_provider_workspace(
        layout=layout,
        spec=_spec('agent1', provider='codex'),
        workspace_path=workspace,
        completion_dir=runtime_dir / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    codex_home = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'codex' / 'home'
    hooks_payload = json.loads((codex_home / 'hooks.json').read_text(encoding='utf-8'))
    all_commands = [
        str(hook.get('command') or '')
        for commands in hooks_payload['hooks'].values()
        for group in commands
        for hook in group.get('hooks', [])
    ]

    assert not any('ccb-provider-activity-hook' in command for command in all_commands)
    assert sum('codex-native-hook.js' in command for command in all_commands) == 7
    assert 'Notification' not in hooks_payload['hooks']

    config = tomllib.loads((codex_home / 'config.toml').read_text(encoding='utf-8'))
    state = config['hooks']['state']
    hooks_path = codex_home / 'hooks.json'
    assert f'{hooks_path}:session_start:0:0' in state
    assert f'{hooks_path}:user_prompt_submit:0:0' in state
    assert f'{hooks_path}:pre_tool_use:0:0' in state
    assert f'{hooks_path}:post_tool_use:0:0' in state
    assert f'{hooks_path}:pre_compact:0:0' in state
    assert f'{hooks_path}:post_compact:0:0' in state
    assert f'{hooks_path}:stop:0:0' in state
    assert len(state) == 7

    identity = {
        'event_name': 'user_prompt_submit',
        'hooks': [
            {
                'async': False,
                'command': omx_command,
                'timeout': 600,
                'type': 'command',
            }
        ],
    }
    expected_hash = 'sha256:' + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    ).hexdigest()
    assert state[f'{hooks_path}:user_prompt_submit:0:0']['trusted_hash'] == expected_hash


def test_prepare_provider_workspace_preserves_configured_codex_command_hooks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_codex = system_home / '.codex'
    generic_hook_root = tmp_path / 'generic-hooks'
    system_codex.mkdir(parents=True, exist_ok=True)
    generic_hook_root.mkdir(parents=True, exist_ok=True)
    (system_codex / 'AGENTS.md').write_text('system codex memory\n', encoding='utf-8')
    (system_codex / 'config.toml').write_text('model = "gpt-test"\n', encoding='utf-8')
    (system_codex / 'hooks.json').write_text(
        json.dumps(
            {
                'hooks': {
                    'UserPromptSubmit': [
                        {'hooks': [{'type': 'command', 'command': f'{generic_hook_root / "recall.sh"}', 'timeout': 19}]},
                        {'hooks': [{'type': 'command', 'command': 'echo unmanaged-root-hook'}]},
                    ],
                    'Stop': [
                        {'hooks': [{'type': 'command', 'command': f'{generic_hook_root / "retain.sh"}', 'timeout': 31}]}
                    ],
                    'PreToolUse': [
                        {'hooks': [{'type': 'command', 'command': f'{generic_hook_root / "pre.sh"}', 'timeout': 7}]}
                    ],
                }
            },
            indent=2,
        ),
        encoding='utf-8',
    )
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ccb memory\n')
    monkeypatch.setenv('CODEX_HOME', str(system_codex))
    monkeypatch.setenv('CCB_CODEX_INHERITED_COMMAND_HOOK_MARKERS', str(generic_hook_root))
    layout = PathLayout(project_root)
    runtime_dir = layout.agent_provider_runtime_dir('agent1', 'codex')

    prepare_provider_workspace(
        layout=layout,
        spec=_spec('agent1', provider='codex'),
        workspace_path=workspace,
        completion_dir=runtime_dir / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    codex_home = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'codex' / 'home'
    hooks_payload = json.loads((codex_home / 'hooks.json').read_text(encoding='utf-8'))
    user_prompt_handlers = [
        hook
        for group in hooks_payload['hooks']['UserPromptSubmit']
        for hook in group['hooks']
    ]
    stop_handlers = [
        hook
        for group in hooks_payload['hooks']['Stop']
        for hook in group['hooks']
    ]
    all_commands = [
        str(hook.get('command') or '')
        for commands in hooks_payload['hooks'].values()
        for group in commands
        for hook in group.get('hooks', [])
    ]

    assert not any('ccb-provider-activity-hook' in command for command in all_commands)
    assert any(
        str(generic_hook_root / 'recall.sh') == str(hook.get('command') or '') and hook['timeout'] == 19
        for hook in user_prompt_handlers
    )
    assert any(
        str(generic_hook_root / 'retain.sh') == str(hook.get('command') or '') and hook['timeout'] == 31
        for hook in stop_handlers
    )
    assert 'echo unmanaged-root-hook' not in all_commands
    assert str(generic_hook_root / 'pre.sh') not in all_commands

    config = tomllib.loads((codex_home / 'config.toml').read_text(encoding='utf-8'))
    state = config['hooks']['state']
    assert any(key.endswith(':user_prompt_submit:0:0') for key in state)
    assert any(key.endswith(':stop:0:0') for key in state)
    assert len(state) == 2


def test_prepare_provider_workspace_respects_codex_explicit_runtime_home(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    explicit_home = tmp_path / 'explicit-codex-home'
    source_home = tmp_path / 'source-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'AGENTS.md').write_text('system codex memory\n', encoding='utf-8')
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ccb memory\n')
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    layout = PathLayout(project_root)

    prepare_provider_workspace(
        layout=layout,
        spec=_spec(
            'agent1',
            provider='codex',
            provider_profile=ProviderProfileSpec(mode='isolated', home=str(explicit_home)),
        ),
        workspace_path=workspace,
        completion_dir=layout.agent_provider_runtime_dir('agent1', 'codex') / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    assert (explicit_home / 'AGENTS.md').is_file()
    assert not (layout.agent_provider_state_dir('agent1', 'codex') / 'home' / 'AGENTS.md').exists()


def test_prepare_provider_workspace_materializes_opencode_memory_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ccb memory\n')
    (project_root / 'AGENTS.md').write_text('project opencode memory\n', encoding='utf-8')
    (project_root / 'opencode.json').write_text(
        json.dumps({'provider': 'anthropic', 'instructions': ['AGENTS.md']}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    layout = PathLayout(project_root)

    prepare_provider_workspace(
        layout=layout,
        spec=_spec('agent1', provider='opencode'),
        workspace_path=workspace,
        completion_dir=layout.agent_provider_runtime_dir('agent1', 'opencode') / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    config_path = layout.agent_provider_state_dir('agent1', 'opencode') / 'opencode.json'
    bundle_path = project_root / '.ccb' / 'runtime' / 'memory' / 'agent1.md'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    assert config['provider'] == 'anthropic'
    assert config['autoupdate'] is False
    assert config['instructions'] == [
        'AGENTS.md',
        '.ccb/runtime/memory/agent1.md',
        '.ccb/runtime/skills/agent1/opencode/ask.md',
    ]
    assert 'shared ccb memory' in bundle_path.read_text(encoding='utf-8')
    assert 'project opencode memory' not in bundle_path.read_text(encoding='utf-8')
    assert (project_root / '.ccb' / 'runtime' / 'skills' / 'agent1' / 'opencode' / 'ask.md').is_file()


def test_prepare_provider_workspace_materializes_kimi_inherited_skills(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    project_root.mkdir(parents=True, exist_ok=True)
    layout = PathLayout(project_root)

    prepare_provider_workspace(
        layout=layout,
        spec=_spec('agent1', provider='kimi'),
        workspace_path=workspace,
        completion_dir=layout.agent_provider_runtime_dir('agent1', 'kimi') / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    skill_path = layout.agent_provider_state_dir('agent1', 'kimi') / 'inherited-skills' / 'ask' / 'SKILL.md'
    assert skill_path.is_file()
    assert 'command ask "$TARGET"' in skill_path.read_text(encoding='utf-8')


def test_prepare_provider_workspace_materializes_mimo_memory_config(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root
    _write_project_memory(project_root, 'mimo shared memory\n')
    layout = PathLayout(project_root)

    prepare_provider_workspace(
        layout=layout,
        spec=_spec('agent1', provider='mimo'),
        workspace_path=workspace,
        completion_dir=layout.agent_provider_runtime_dir('agent1', 'mimo') / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    config_path = layout.agent_provider_state_dir('agent1', 'mimo') / 'mimocode.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    assert config['instructions'] == [
        '.ccb/runtime/memory/agent1.md',
        '.ccb/runtime/skills/agent1/mimo/ask.md',
    ]
    assert (project_root / '.ccb' / 'runtime' / 'skills' / 'agent1' / 'mimo' / 'ask.md').is_file()


def test_prepare_provider_workspace_records_opencode_config_merge_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ccb memory\n')
    (project_root / 'opencode.json').write_text('{broken json\n', encoding='utf-8')
    layout = PathLayout(project_root)

    prepare_provider_workspace(
        layout=layout,
        spec=_spec('agent1', provider='opencode'),
        workspace_path=workspace,
        completion_dir=layout.agent_provider_runtime_dir('agent1', 'opencode') / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    events = [
        json.loads(line)
        for line in layout.agent_events_path('agent1').read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    assert any(event.get('event_type') == 'opencode_config_merge_failed' for event in events)
    projection = [event for event in events if event.get('event_type') == 'opencode_memory_projection_ok'][-1]
    assert projection['config_merge_status'] == 'failed'
    assert projection['config_merge_reason'] == 'JSONDecodeError'


def test_prepare_provider_workspace_records_claude_binary_cache_drift_once(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    monkeypatch.setenv('HOME', str(system_home))
    layout = PathLayout(project_root)
    versions_dir = layout.agent_provider_state_dir('agent1', 'claude') / 'home' / '.local' / 'share' / 'claude' / 'versions'
    (versions_dir / '2.1.137').mkdir(parents=True, exist_ok=True)
    (versions_dir / '2.1.137' / 'claude').write_text('binary\n', encoding='utf-8')

    monkeypatch.setattr(
        'cli.services.provider_hooks._route_claude_binary_cache_if_possible',
        lambda *, layout, home_root: None,
    )

    for refresh_profile in (True, False):
        prepare_provider_workspace(
            layout=layout,
            spec=_spec('agent1'),
            workspace_path=workspace,
            completion_dir=layout.agent_provider_runtime_dir('agent1', 'claude') / 'completion',
            agent_name='agent1',
            refresh_profile=refresh_profile,
        )

    events = [
        json.loads(line)
        for line in layout.agent_events_path('agent1').read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    drift_events = [event for event in events if event.get('event_type') == 'claude_binary_cache_drift']
    assert len(drift_events) == 1
    assert drift_events[0]['reason'] == 'per_agent_versions_cache_present'
    assert drift_events[0]['version_names'] == ['2.1.137']


def test_prepare_provider_workspace_records_new_claude_binary_cache_signature(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    monkeypatch.setenv('HOME', str(tmp_path / 'system-home'))
    layout = PathLayout(project_root)
    versions_dir = layout.agent_provider_state_dir('agent1', 'claude') / 'home' / '.local' / 'share' / 'claude' / 'versions'
    (versions_dir / '2.1.137').mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        'cli.services.provider_hooks._route_claude_binary_cache_if_possible',
        lambda *, layout, home_root: None,
    )

    prepare_provider_workspace(
        layout=layout,
        spec=_spec('agent1'),
        workspace_path=workspace,
        completion_dir=layout.agent_provider_runtime_dir('agent1', 'claude') / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )
    (versions_dir / '2.1.138').mkdir(parents=True, exist_ok=True)
    prepare_provider_workspace(
        layout=layout,
        spec=_spec('agent1'),
        workspace_path=workspace,
        completion_dir=layout.agent_provider_runtime_dir('agent1', 'claude') / 'completion',
        agent_name='agent1',
        refresh_profile=False,
    )

    events = [
        json.loads(line)
        for line in layout.agent_events_path('agent1').read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    drift_events = [event for event in events if event.get('event_type') == 'claude_binary_cache_drift']
    assert [event['version_names'] for event in drift_events] == [['2.1.137'], ['2.1.137', '2.1.138']]


def test_prepare_provider_workspace_does_not_record_claude_binary_cache_drift_when_absent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    monkeypatch.setenv('HOME', str(tmp_path / 'system-home'))
    layout = PathLayout(project_root)

    prepare_provider_workspace(
        layout=layout,
        spec=_spec('agent1'),
        workspace_path=project_root / 'workspace',
        completion_dir=layout.agent_provider_runtime_dir('agent1', 'claude') / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    events_path = layout.agent_events_path('agent1')
    if not events_path.exists():
        return
    events = [json.loads(line) for line in events_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    assert all(event.get('event_type') != 'claude_binary_cache_drift' for event in events)


def test_prepare_provider_workspace_routes_claude_binary_cache_to_external_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    monkeypatch.setenv('HOME', str(tmp_path / 'system-home'))
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'xdg-cache'))
    layout = PathLayout(project_root)
    first_versions = layout.agent_provider_state_dir('agent1', 'claude') / 'home' / '.local' / 'share' / 'claude' / 'versions'
    second_versions = layout.agent_provider_state_dir('agent2', 'claude') / 'home' / '.local' / 'share' / 'claude' / 'versions'
    first_versions.mkdir(parents=True, exist_ok=True)
    second_versions.mkdir(parents=True, exist_ok=True)
    (first_versions / '2.1.137').write_text('binary\n', encoding='utf-8')
    (second_versions / '2.1.137').write_text('binary\n', encoding='utf-8')

    for agent_name in ('agent1', 'agent2'):
        prepare_provider_workspace(
            layout=layout,
            spec=_spec(agent_name),
            workspace_path=workspace,
            completion_dir=layout.agent_provider_runtime_dir(agent_name, 'claude') / 'completion',
            agent_name=agent_name,
            refresh_profile=True,
        )

    shared_versions = layout.provider_external_cache_dir('claude') / 'versions'
    assert first_versions.is_symlink()
    assert second_versions.is_symlink()
    assert first_versions.resolve() == shared_versions.resolve()
    assert second_versions.resolve() == shared_versions.resolve()
    assert (shared_versions / '2.1.137').read_text(encoding='utf-8') == 'binary\n'
    assert not (layout.shared_cache_dir / 'claude' / 'versions' / '2.1.137').exists()
    assert (first_versions.parent / 'versions.ccb-projection.json').is_file()
    for agent_name in ('agent1', 'agent2'):
        events_path = layout.agent_events_path(agent_name)
        events = [json.loads(line) for line in events_path.read_text(encoding='utf-8').splitlines() if line.strip()]
        assert all(event.get('event_type') != 'claude_binary_cache_drift' for event in events)


def test_prepare_provider_workspace_routes_claude_binary_cache_from_home_active_version(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    active_binary = system_home / '.local' / 'share' / 'claude' / 'versions' / '2.1.141' / 'claude'
    active_binary.parent.mkdir(parents=True, exist_ok=True)
    active_binary.write_text('home active binary\n', encoding='utf-8')
    active_binary.chmod(0o755)
    (system_home / '.local' / 'bin').mkdir(parents=True, exist_ok=True)
    (system_home / '.local' / 'bin' / 'claude').symlink_to(active_binary)
    monkeypatch.setenv('HOME', str(system_home))
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'xdg-cache'))
    layout = PathLayout(project_root)

    prepare_provider_workspace(
        layout=layout,
        spec=_spec('agent1'),
        workspace_path=workspace,
        completion_dir=layout.agent_provider_runtime_dir('agent1', 'claude') / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    managed_home = layout.agent_provider_state_dir('agent1', 'claude') / 'home'
    shared_active = layout.provider_external_cache_dir('claude') / 'versions' / '2.1.141'
    assert (managed_home / '.local' / 'share' / 'claude' / 'versions').resolve() == (
        layout.provider_external_cache_dir('claude') / 'versions'
    ).resolve()
    assert (shared_active / 'claude').read_text(encoding='utf-8') == 'home active binary\n'
    assert (managed_home / '.local' / 'bin' / 'claude').resolve() == (shared_active / 'claude').resolve()


def test_prepare_provider_workspace_keeps_unknown_claude_versions_dir_unmodified(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    monkeypatch.setenv('HOME', str(tmp_path / 'system-home'))
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'xdg-cache'))
    layout = PathLayout(project_root)
    versions_dir = layout.agent_provider_state_dir('agent1', 'claude') / 'home' / '.local' / 'share' / 'claude' / 'versions'
    (versions_dir / '2.1.137').mkdir(parents=True, exist_ok=True)
    (versions_dir / '2.1.137' / 'claude').write_text('binary\n', encoding='utf-8')
    (versions_dir / 'notes.txt').write_text('do not delete\n', encoding='utf-8')

    prepare_provider_workspace(
        layout=layout,
        spec=_spec('agent1'),
        workspace_path=workspace,
        completion_dir=layout.agent_provider_runtime_dir('agent1', 'claude') / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    assert versions_dir.is_dir()
    assert not versions_dir.is_symlink()
    assert (versions_dir / 'notes.txt').read_text(encoding='utf-8') == 'do not delete\n'
    assert not (layout.shared_cache_dir / 'claude' / 'versions' / '2.1.137').exists()
    assert not (layout.provider_external_cache_dir('claude') / 'versions' / '2.1.137').exists()
    events = [
        json.loads(line)
        for line in layout.agent_events_path('agent1').read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    drift_events = [event for event in events if event.get('event_type') == 'claude_binary_cache_drift']
    assert drift_events[-1]['reason'] == 'per_agent_versions_cache_present'


def test_prepare_provider_workspace_uses_account_home_when_current_home_is_managed_claude(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_credentials = system_home / '.claude' / '.credentials.json'
    system_credentials.parent.mkdir(parents=True, exist_ok=True)
    system_credentials.write_text(
        json.dumps({'claudeAiOauth': {'refreshToken': 'system-refresh-token'}}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    managed_current_home = (
        project_root
        / '.ccb'
        / 'agents'
        / 'caller'
        / 'provider-state'
        / 'claude'
        / 'home'
    )
    monkeypatch.setenv('HOME', str(managed_current_home))
    if source_home_module.pwd is not None:
        monkeypatch.setattr(source_home_module.pwd, 'getpwuid', lambda _uid: SimpleNamespace(pw_dir=str(system_home)))

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('agent1'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'claude' / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    managed_credentials = (
        project_root
        / '.ccb'
        / 'agents'
        / 'agent1'
        / 'provider-state'
        / 'claude'
        / 'home'
        / '.claude'
        / '.credentials.json'
    )
    assert json.loads(managed_credentials.read_text(encoding='utf-8'))['claudeAiOauth']['refreshToken'] == 'system-refresh-token'


def test_prepare_provider_workspace_repairs_existing_claude_hook_only_settings(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_settings = system_home / '.claude' / 'settings.json'
    system_settings.parent.mkdir(parents=True, exist_ok=True)
    system_settings.write_text(
        json.dumps(
            {
                'env': {'ANTHROPIC_AUTH_TOKEN': 'system-token'},
                'theme': 'dark',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('HOME', str(system_home))

    managed_settings = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'claude' / 'home' / '.claude' / 'settings.json'
    managed_settings.parent.mkdir(parents=True, exist_ok=True)
    managed_settings.write_text(
        json.dumps(
            {
                'hooks': {
                    'Stop': [
                        {
                            'hooks': [
                                {
                                    'type': 'command',
                                    'command': 'echo legacy-hook',
                                }
                            ]
                        }
                    ]
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('agent1'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'claude' / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    payload = json.loads(managed_settings.read_text(encoding='utf-8'))
    assert payload['env']['ANTHROPIC_AUTH_TOKEN'] == 'system-token'
    assert payload['env']['ANTHROPIC_API_KEY'] == 'system-token'
    assert payload['theme'] == 'dark'
    commands = [hook['command'] for group in payload['hooks']['Stop'] for hook in group.get('hooks', []) if isinstance(hook, dict)]
    assert 'echo legacy-hook' in commands


def test_prepare_provider_workspace_preserves_managed_claude_auth_when_system_home_logged_out(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_settings = system_home / '.claude' / 'settings.json'
    system_settings.parent.mkdir(parents=True, exist_ok=True)
    system_settings.write_text(
        json.dumps(
            {
                'env': {
                    'ANTHROPIC_BASE_URL': 'https://claude.example.test',
                },
                'theme': 'light',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('HOME', str(system_home))

    managed_settings = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'claude' / 'home' / '.claude' / 'settings.json'
    managed_settings.parent.mkdir(parents=True, exist_ok=True)
    managed_settings.write_text(
        json.dumps(
            {
                'env': {
                    'ANTHROPIC_AUTH_TOKEN': 'managed-token',
                    'ANTHROPIC_BASE_URL': 'https://managed.example.test',
                },
                'hooks': {
                    'Stop': [
                        {
                            'hooks': [
                                {
                                    'type': 'command',
                                    'command': 'echo legacy-hook',
                                }
                            ]
                        }
                    ]
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('agent1'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'claude' / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    payload = json.loads(managed_settings.read_text(encoding='utf-8'))
    assert payload['env']['ANTHROPIC_AUTH_TOKEN'] == 'managed-token'
    assert payload['env']['ANTHROPIC_API_KEY'] == 'managed-token'
    assert payload['env']['ANTHROPIC_BASE_URL'] == 'https://claude.example.test'
    assert payload['theme'] == 'light'
    commands = [hook['command'] for group in payload['hooks']['Stop'] for hook in group.get('hooks', []) if isinstance(hook, dict)]
    assert 'echo legacy-hook' in commands


def test_install_gemini_hooks_writes_managed_home_settings_only(tmp_path: Path) -> None:
    workspace = tmp_path / 'workspace'
    home_root = tmp_path / 'gemini-home'
    command = '/usr/bin/python3 /tmp/ccb-provider-finish-hook --provider gemini'

    settings_path = install_workspace_completion_hooks(
        provider='gemini',
        workspace_path=workspace,
        home_root=home_root,
        command=command,
    )

    assert settings_path == home_root / '.gemini' / 'settings.json'
    data = json.loads(settings_path.read_text(encoding='utf-8'))
    assert data['hooks']['AfterAgent'][0]['matcher'] == '*'
    assert data['hooks']['AfterAgent'][0]['hooks'][0]['command'] == command
    assert not (workspace / '.gemini').exists()


def test_install_gemini_hooks_trusts_workspace_in_managed_home(tmp_path: Path) -> None:
    workspace = tmp_path / 'workspace'
    home_root = tmp_path / 'gemini-home'
    command = '/usr/bin/python3 /tmp/ccb-provider-finish-hook --provider gemini'

    install_workspace_completion_hooks(
        provider='gemini',
        workspace_path=workspace,
        home_root=home_root,
        command=command,
    )

    trust_path = home_root / '.gemini' / 'trustedFolders.json'
    data = json.loads(trust_path.read_text(encoding='utf-8'))
    assert data[str(workspace.resolve())] == 'TRUST_FOLDER'
    assert not (workspace / '.gemini').exists()


def test_prepare_provider_workspace_materializes_gemini_memory_bundle_before_hooks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_gemini = system_home / '.gemini'
    system_gemini.mkdir(parents=True, exist_ok=True)
    (system_gemini / 'GEMINI.md').write_text('system gemini memory\n', encoding='utf-8')
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ccb memory\n')
    (project_root / 'GEMINI.md').write_text('project gemini memory\n', encoding='utf-8')
    private_memory = project_root / '.ccb' / 'agents' / 'agent1' / 'memory.md'
    private_memory.parent.mkdir(parents=True, exist_ok=True)
    private_memory.write_text('agent private memory\n', encoding='utf-8')
    monkeypatch.setenv('HOME', str(system_home))

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider='gemini'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'gemini' / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    gemini_dir = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'gemini' / 'home' / '.gemini'
    text = (gemini_dir / 'GEMINI.md').read_text(encoding='utf-8')
    settings = json.loads((gemini_dir / 'settings.json').read_text(encoding='utf-8'))
    assert text.startswith('# CCB Managed Agent Memory')
    assert 'system gemini memory' in text
    assert 'shared ccb memory' in text
    assert 'project gemini memory' in text
    assert 'agent private memory' in text
    assert settings['contextFileName'] == 'GEMINI.md'


def test_prepare_provider_workspace_records_gemini_memory_projection_event_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_gemini = system_home / '.gemini'
    system_gemini.mkdir(parents=True, exist_ok=True)
    (system_gemini / 'GEMINI.md').write_text('system gemini memory\n', encoding='utf-8')
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ccb memory\n')
    monkeypatch.setenv('HOME', str(system_home))
    layout = PathLayout(project_root)

    for refresh_profile in (True, False):
        prepare_provider_workspace(
            layout=layout,
            spec=_spec('agent1', provider='gemini'),
            workspace_path=workspace,
            completion_dir=layout.agent_provider_runtime_dir('agent1', 'gemini') / 'completion',
            agent_name='agent1',
            refresh_profile=refresh_profile,
        )

    events = [
        json.loads(line)
        for line in layout.agent_events_path('agent1').read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    memory_events = [event for event in events if str(event.get('event_type', '')).startswith('gemini_memory_projection_')]
    assert len(memory_events) == 1
    assert memory_events[0]['event_type'] == 'gemini_memory_projection_ok'
    assert memory_events[0]['reason'] == 'written'
    assert memory_events[0]['sha256']
    assert memory_events[0]['source_count'] >= 2
    marker_path = layout.agent_provider_runtime_dir('agent1', 'gemini') / 'gemini-memory-projection.json'
    marker = json.loads(marker_path.read_text(encoding='utf-8'))
    assert marker['status'] == 'ok'


def test_prepare_provider_workspace_materializes_gemini_settings_before_hooks(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_settings = system_home / '.gemini' / 'settings.json'
    system_settings.parent.mkdir(parents=True, exist_ok=True)
    system_settings.write_text(
        json.dumps(
            {
                'env': {
                    'GEMINI_API_KEY': 'system-gemini-key',
                    'GOOGLE_API_KEY': 'system-google-key',
                },
                'theme': 'Default',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('HOME', str(system_home))

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider='gemini'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'gemini' / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    settings_path = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'gemini' / 'home' / '.gemini' / 'settings.json'
    payload = json.loads(settings_path.read_text(encoding='utf-8'))
    assert payload['env']['GEMINI_API_KEY'] == 'system-gemini-key'
    assert payload['env']['GOOGLE_API_KEY'] == 'system-google-key'
    assert payload['theme'] == 'Default'
    assert payload['hooks']['AfterAgent'][0]['hooks'][0]['command']
    assert not (workspace / '.gemini').exists()


def test_prepare_provider_workspace_materializes_gemini_dotenv_api_auth_before_hooks(
    tmp_path: Path, monkeypatch
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_gemini = system_home / '.gemini'
    system_gemini.mkdir(parents=True, exist_ok=True)
    (system_gemini / 'settings.json').write_text(
        json.dumps(
            {
                'security': {
                    'auth': {
                        'selectedType': 'gemini-api-key',
                    }
                },
                'theme': 'Default',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    (system_gemini / '.env').write_text(
        'GEMINI_API_KEY=system-gemini-key\nGOOGLE_GEMINI_BASE_URL=https://gemini.example.test\nOTHER_SECRET=ignored\n',
        encoding='utf-8',
    )
    monkeypatch.setenv('HOME', str(system_home))

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider='gemini'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'gemini' / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    managed_gemini = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'gemini' / 'home' / '.gemini'
    payload = json.loads((managed_gemini / 'settings.json').read_text(encoding='utf-8'))
    dotenv = (managed_gemini / '.env').read_text(encoding='utf-8')
    assert payload['security']['auth']['selectedType'] == 'gemini-api-key'
    assert 'GEMINI_API_KEY="system-gemini-key"' in dotenv
    assert 'GOOGLE_GEMINI_BASE_URL="https://gemini.example.test"' in dotenv
    assert 'OTHER_SECRET' not in dotenv
    assert payload['hooks']['AfterAgent'][0]['hooks'][0]['command']
    assert not (workspace / '.gemini').exists()


def test_prepare_provider_workspace_materializes_gemini_oauth_credentials_when_login_auth_selected(
    tmp_path: Path, monkeypatch
) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_settings = system_home / '.gemini' / 'settings.json'
    system_oauth = system_home / '.gemini' / 'oauth_creds.json'
    system_accounts = system_home / '.gemini' / 'google_accounts.json'
    system_settings.parent.mkdir(parents=True, exist_ok=True)
    system_settings.write_text(
        json.dumps(
            {
                'security': {
                    'auth': {
                        'selectedType': 'oauth-personal',
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    system_oauth.write_text(
        json.dumps({'refresh_token': 'system-refresh-token'}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    system_accounts.write_text(
        json.dumps({'active': 'user@example.test'}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    monkeypatch.setenv('HOME', str(system_home))

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider='gemini'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'gemini' / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    managed_settings = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'gemini' / 'home' / '.gemini' / 'settings.json'
    managed_oauth = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'gemini' / 'home' / '.gemini' / 'oauth_creds.json'
    managed_accounts = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'gemini' / 'home' / '.gemini' / 'google_accounts.json'
    payload = json.loads(managed_settings.read_text(encoding='utf-8'))
    assert payload['security']['auth']['selectedType'] == 'oauth-personal'
    assert json.loads(managed_oauth.read_text(encoding='utf-8'))['refresh_token'] == 'system-refresh-token'
    assert json.loads(managed_accounts.read_text(encoding='utf-8'))['active'] == 'user@example.test'


def test_prepare_provider_workspace_repairs_existing_gemini_hook_only_settings(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_settings = system_home / '.gemini' / 'settings.json'
    system_settings.parent.mkdir(parents=True, exist_ok=True)
    system_settings.write_text(
        json.dumps(
            {
                'env': {'GEMINI_API_KEY': 'system-gemini-key'},
                'theme': 'Atom One',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('HOME', str(system_home))

    managed_settings = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'gemini' / 'home' / '.gemini' / 'settings.json'
    managed_settings.parent.mkdir(parents=True, exist_ok=True)
    managed_settings.write_text(
        json.dumps(
            {
                'hooks': {
                    'AfterAgent': [
                        {
                            'matcher': '*',
                            'hooks': [
                                {
                                    'type': 'command',
                                    'command': 'echo legacy-gemini-hook',
                                }
                            ],
                        }
                    ]
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider='gemini'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'gemini' / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    payload = json.loads(managed_settings.read_text(encoding='utf-8'))
    assert payload['env']['GEMINI_API_KEY'] == 'system-gemini-key'
    assert payload['theme'] == 'Atom One'
    commands = [hook['command'] for group in payload['hooks']['AfterAgent'] for hook in group.get('hooks', []) if isinstance(hook, dict)]
    assert 'echo legacy-gemini-hook' in commands


def test_prepare_provider_workspace_merges_gemini_trusted_folders(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    workspace = project_root / 'workspace'
    system_home = tmp_path / 'system-home'
    system_trust = system_home / '.gemini' / 'trustedFolders.json'
    system_trust.parent.mkdir(parents=True, exist_ok=True)
    system_trust.write_text(
        json.dumps({'/system/project': 'TRUST_FOLDER'}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    managed_trust = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'gemini' / 'home' / '.gemini' / 'trustedFolders.json'
    managed_trust.parent.mkdir(parents=True, exist_ok=True)
    managed_trust.write_text(
        json.dumps({'/managed/project': 'TRUST_FOLDER'}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    monkeypatch.setenv('HOME', str(system_home))

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider='gemini'),
        workspace_path=workspace,
        completion_dir=project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'gemini' / 'completion',
        agent_name='agent1',
        refresh_profile=True,
    )

    payload = json.loads(managed_trust.read_text(encoding='utf-8'))
    assert payload['/system/project'] == 'TRUST_FOLDER'
    assert payload['/managed/project'] == 'TRUST_FOLDER'
    assert payload[str(workspace.resolve())] == 'TRUST_FOLDER'
