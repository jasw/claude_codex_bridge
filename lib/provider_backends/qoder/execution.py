from __future__ import annotations

from pathlib import Path

from provider_backends.native_cli_support import (
    NativeCliExecutionConfig,
    NativeCliExecutionRequest,
    NativeCliSubprocessAdapter,
)
from provider_core.runtime_shared import provider_start_parts


def build_execution_adapter() -> NativeCliSubprocessAdapter:
    return NativeCliSubprocessAdapter(
        NativeCliExecutionConfig(
            provider="qoder",
            session_filename=".qoder-session",
            command_builder=_build_command,
            env_builder=_build_env,
            output_kind="jsonl",
            mode="qoder_run",
            start_failed_reason="qoder_run_start_failed",
            failed_reason="qoder_run_failed",
            empty_reason="qoder_empty_reply",
            run_error_reason="qoder_run_error",
            complete_reason="qoder_run_stop",
            process_exit_complete_reason="qoder_run_exit",
            timeout_reason="qoder_run_timeout",
        )
    )


def _build_command(request: NativeCliExecutionRequest) -> list[str]:
    return [
        *provider_start_parts("qoder"),
        "--bare",
        "--output-format",
        "stream-json",
        "--session-id",
        request.job.job_id,
        request.prompt,
    ]


def _build_env(request: NativeCliExecutionRequest) -> dict[str, str]:
    qoder_home = _state_path(request, "qoder_home", fallback="home")
    qoder_home.mkdir(parents=True, exist_ok=True)
    return {"QODER_HOME": str(qoder_home)}


def _state_path(request: NativeCliExecutionRequest, key: str, *, fallback: str) -> Path:
    raw = str(request.session_data.get(key) or "").strip()
    if raw:
        return Path(raw).expanduser()
    state_dir = Path(str(request.session_data.get("qoder_state_dir") or request.work_dir / ".ccb" / "qoder")).expanduser()
    return state_dir / fallback


__all__ = ["build_execution_adapter"]
