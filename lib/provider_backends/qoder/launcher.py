from __future__ import annotations

from pathlib import Path

from agents.models import AgentSpec
from cli.context import CliContext
from cli.models import ParsedStartCommand
from provider_backends.native_cli_support.launcher import (
    NativeCliLaunchConfig,
    build_session_payload as native_build_session_payload,
    build_start_cmd as native_build_start_cmd,
    prepare_launch_context as native_prepare_launch_context,
)
from provider_core.contracts import ProviderRuntimeLauncher
from provider_core.runtime_shared import provider_start_parts
from workspace.models import WorkspacePlan


_CONFIG_OPTION = "--config-dir"
_PERMISSION_OPTIONS = {"--dangerously-skip-permissions", "--permission-mode", "--yolo"}


def build_runtime_launcher() -> ProviderRuntimeLauncher:
    return ProviderRuntimeLauncher(
        provider="qoder",
        launch_mode="simple_tmux",
        prepare_launch_context=prepare_launch_context,
        build_start_cmd=build_start_cmd,
        build_session_payload=build_session_payload,
    )


def prepare_launch_context(
    context: CliContext,
    spec: AgentSpec,
    plan: WorkspacePlan,
    runtime_dir: Path,
    prepared_state: dict[str, object],
) -> dict[str, object]:
    return native_prepare_launch_context(
        _QODER_LAUNCH_CONFIG,
        context,
        spec,
        plan,
        runtime_dir,
        prepared_state,
    )


def build_start_cmd(
    command: ParsedStartCommand,
    spec: AgentSpec,
    runtime_dir,
    launch_session_id: str,
    *,
    prepared_state: dict[str, object] | None = None,
) -> str:
    launch_context = prepared_state if prepared_state is not None else {}
    parts = [*provider_start_parts("qoder"), *spec.startup_args]
    explicit_config = _option_value(parts, _CONFIG_OPTION)
    if explicit_config:
        config_dir = Path(explicit_config).expanduser()
        if not config_dir.is_absolute():
            config_dir = Path(str(launch_context.get("workspace_path") or ".")) / config_dir
        launch_context["qoder_config_dir"] = str(config_dir)
        launch_context["qoder_managed_config_arg"] = False
    else:
        launch_context["qoder_config_dir"] = str(
            _path_from_prepared(launch_context, "qoder_home")
        )
        launch_context["qoder_managed_config_arg"] = True
    launch_context["qoder_auto_permission_enabled"] = bool(command.auto_permission)
    launch_context["qoder_managed_permission_arg"] = not any(
        _has_option(parts, option) for option in _PERMISSION_OPTIONS
    )
    explicit_permission_mode = _option_value(parts, "--permission-mode")
    if explicit_permission_mode:
        headless_permission_mode = explicit_permission_mode
    elif _has_option(parts, "--dangerously-skip-permissions") or _has_option(
        parts, "--yolo"
    ):
        headless_permission_mode = "bypass_permissions"
    elif command.auto_permission:
        headless_permission_mode = "auto"
    else:
        headless_permission_mode = "dont_ask"
    launch_context["qoder_headless_permission_mode"] = headless_permission_mode
    return native_build_start_cmd(
        _QODER_LAUNCH_CONFIG,
        command,
        spec,
        runtime_dir,
        launch_session_id,
        prepared_state=launch_context,
    )


def build_session_payload(
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
    payload = native_build_session_payload(
        _QODER_LAUNCH_CONFIG,
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
    )
    payload["qoder_config_dir"] = str(prepared_state.get("qoder_config_dir") or "")
    payload["qoder_auto_permission_enabled"] = bool(
        prepared_state.get("qoder_auto_permission_enabled")
    )
    payload["qoder_headless_permission_mode"] = str(
        prepared_state.get("qoder_headless_permission_mode") or "dont_ask"
    )
    return payload


def _qoder_visible_args(prepared_state: dict[str, object]) -> tuple[str, ...]:
    args: list[str] = []
    if bool(prepared_state.get("qoder_managed_config_arg")):
        config_dir = _path_from_prepared(prepared_state, "qoder_config_dir")
        config_dir.mkdir(parents=True, exist_ok=True)
        args.extend([_CONFIG_OPTION, str(config_dir)])
    if bool(prepared_state.get("qoder_managed_permission_arg")) and bool(
        prepared_state.get("qoder_auto_permission_enabled")
    ):
        args.extend(["--permission-mode", "auto"])
    return tuple(args)


def _option_value(parts: list[str], option: str) -> str | None:
    for index, part in enumerate(parts):
        if part == option:
            return parts[index + 1] if index + 1 < len(parts) else None
        if part.startswith(f"{option}="):
            return part.split("=", 1)[1]
    return None


def _has_option(parts: list[str], option: str) -> bool:
    return any(part == option or part.startswith(f"{option}=") for part in parts)


def _path_from_prepared(prepared_state: dict[str, object], key: str) -> Path:
    raw = str(prepared_state.get(key) or "").strip()
    if not raw:
        raise RuntimeError(f"qoder launch requires {key} in prepared_state")
    return Path(raw).expanduser()


_QODER_LAUNCH_CONFIG = NativeCliLaunchConfig(
    provider="qoder",
    visible_args_builder=_qoder_visible_args,
)


__all__ = [
    "build_runtime_launcher",
    "build_session_payload",
    "build_start_cmd",
    "prepare_launch_context",
]
