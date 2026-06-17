from __future__ import annotations

from dataclasses import dataclass
import shlex
from pathlib import Path
from typing import Callable

from agents.models import AgentSpec
from cli.context import CliContext
from cli.models import ParsedStartCommand
from provider_core.caller_env import (
    caller_context_env,
    export_env_clause,
    join_env_prefix,
    provider_user_session_env,
)
from provider_core.contracts import ProviderRuntimeLauncher
from provider_core.runtime_shared import apply_provider_command_template, provider_start_parts
from workspace.models import WorkspacePlan


VisibleArgsBuilder = Callable[[dict[str, object]], tuple[str, ...]]
VisibleEnvBuilder = Callable[[dict[str, object]], dict[str, str]]


@dataclass(frozen=True)
class NativeCliLaunchConfig:
    provider: str
    home_env: str | None = None
    visible_args: tuple[str, ...] = ()
    visible_args_builder: VisibleArgsBuilder | None = None
    visible_env_builder: VisibleEnvBuilder | None = None


def build_native_cli_runtime_launcher(config: NativeCliLaunchConfig) -> ProviderRuntimeLauncher:
    provider = _provider(config)
    return ProviderRuntimeLauncher(
        provider=provider,
        launch_mode="simple_tmux",
        prepare_launch_context=lambda context, spec, plan, runtime_dir, prepared_state: prepare_launch_context(
            config,
            context,
            spec,
            plan,
            runtime_dir,
            prepared_state,
        ),
        build_start_cmd=lambda command, spec, runtime_dir, launch_session_id, prepared_state=None: build_start_cmd(
            config,
            command,
            spec,
            runtime_dir,
            launch_session_id,
            prepared_state=prepared_state,
        ),
        build_session_payload=lambda context, spec, plan, runtime_dir, run_cwd, pane_id, pane_title_marker, start_cmd, launch_session_id, prepared_state: build_session_payload(
            config,
            context,
            spec,
            plan,
            runtime_dir,
            run_cwd,
            pane_id,
            pane_title_marker,
            start_cmd,
            launch_session_id,
            prepared_state,
        ),
    )


def prepare_launch_context(
    config: NativeCliLaunchConfig,
    context: CliContext,
    spec: AgentSpec,
    plan: WorkspacePlan,
    runtime_dir: Path,
    prepared_state: dict[str, object],
) -> dict[str, object]:
    del runtime_dir
    provider = _provider(config)
    state_dir = context.paths.agent_provider_state_dir(spec.name, provider)
    payload = dict(prepared_state or {})
    payload["agent_name"] = spec.name
    payload["project_root"] = str(context.project.project_root)
    payload["workspace_path"] = str(payload.get("run_cwd") or plan.workspace_path)
    payload["agent_events_path"] = str(context.paths.agent_events_path(spec.name))
    payload[f"{provider}_state_dir"] = str(state_dir)
    payload[f"{provider}_home"] = str(state_dir / "home")
    payload[f"{provider}_data_dir"] = str(state_dir / "data")
    return payload


def build_start_cmd(
    config: NativeCliLaunchConfig,
    command: ParsedStartCommand,
    spec: AgentSpec,
    runtime_dir,
    launch_session_id: str,
    *,
    prepared_state: dict[str, object] | None = None,
) -> str:
    del command
    provider = _provider(config)
    runtime_dir = Path(runtime_dir)
    launch_context = prepared_state or {}
    state_dir = _path_or_none(launch_context.get(f"{provider}_state_dir"))
    home_dir = _path_or_none(launch_context.get(f"{provider}_home"))
    if state_dir is None or home_dir is None:
        raise RuntimeError(f"{provider} launch requires prepare_launch_context before build_start_cmd")
    state_dir.mkdir(parents=True, exist_ok=True)
    home_dir.mkdir(parents=True, exist_ok=True)

    cmd_parts = [
        *provider_start_parts(provider),
        *config.visible_args,
        *_dynamic_visible_args(config, launch_context),
        *spec.startup_args,
    ]
    cmd = " ".join(shlex.quote(str(part)) for part in cmd_parts)
    cmd = apply_provider_command_template(cmd, spec.provider_command_template)
    env_prefix = join_env_prefix(
        export_env_clause(provider_user_session_env()),
        export_env_clause(spec.env),
        export_env_clause(_provider_home_env(config, home_dir)),
        export_env_clause(_dynamic_visible_env(config, launch_context)),
        export_env_clause(
            caller_context_env(actor=spec.name, runtime_dir=runtime_dir, launch_session_id=launch_session_id)
        ),
    )
    if env_prefix:
        return f"{env_prefix}; {cmd}"
    return cmd


def build_session_payload(
    config: NativeCliLaunchConfig,
    context: CliContext,
    spec: AgentSpec,
    plan: WorkspacePlan,
    runtime_dir,
    run_cwd,
    pane_id: str,
    pane_title_marker: str,
    start_cmd: str,
    launch_session_id: str,
    prepared_state: dict[str, object],
) -> dict[str, object]:
    provider = _provider(config)
    state_dir = str(prepared_state.get(f"{provider}_state_dir") or "")
    home_dir = str(prepared_state.get(f"{provider}_home") or "")
    data_dir = str(prepared_state.get(f"{provider}_data_dir") or "")
    return {
        "ccb_session_id": launch_session_id,
        f"{provider}_session_id": launch_session_id,
        "agent_name": spec.name,
        "provider": provider,
        "ccb_project_id": context.project.project_id,
        "runtime_dir": str(runtime_dir),
        "completion_artifact_dir": str(runtime_dir / "completion"),
        "terminal": "tmux",
        "tmux_session": pane_id,
        "pane_id": pane_id,
        "pane_title_marker": pane_title_marker,
        "workspace_path": str(plan.workspace_path),
        "work_dir": str(run_cwd),
        "start_dir": str(context.project.project_root),
        "start_cmd": start_cmd,
        f"{provider}_state_dir": state_dir,
        f"{provider}_home": home_dir,
        f"{provider}_data_dir": data_dir,
    }


def _provider(config: NativeCliLaunchConfig) -> str:
    provider = str(config.provider or "").strip().lower()
    if not provider:
        raise RuntimeError("native CLI provider cannot be empty")
    return provider


def _provider_home_env(config: NativeCliLaunchConfig, home_dir: Path) -> dict[str, str]:
    if not config.home_env:
        return {}
    return {config.home_env: str(home_dir)}


def _dynamic_visible_args(config: NativeCliLaunchConfig, launch_context: dict[str, object]) -> tuple[str, ...]:
    if config.visible_args_builder is None:
        return ()
    return tuple(str(part) for part in config.visible_args_builder(launch_context))


def _dynamic_visible_env(config: NativeCliLaunchConfig, launch_context: dict[str, object]) -> dict[str, str]:
    if config.visible_env_builder is None:
        return {}
    return {str(key): str(value) for key, value in config.visible_env_builder(launch_context).items()}


def _path_or_none(value: object) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser()
    except Exception:
        return None


__all__ = [
    "NativeCliLaunchConfig",
    "build_native_cli_runtime_launcher",
    "build_session_payload",
    "build_start_cmd",
    "prepare_launch_context",
]
