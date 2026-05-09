from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from project.ids import compute_project_id, project_slug

from .atomic import atomic_write_json
from .path_helpers import (
    RuntimeStatePlacement,
    SocketPlacement,
    choose_runtime_state_placement,
    choose_socket_placement,
    read_runtime_root_marker_payload,
    read_runtime_root_ref_payload,
    runtime_root_marker_path,
    runtime_root_ref_path,
    runtime_state_placement_payload,
)
from .paths_agents import (
    AgentMailboxPathMixin,
    AgentRuntimePathMixin,
    WorkspacePathMixin,
)
from .paths_ccbd import (
    CcbdArtifactsPathMixin,
    CcbdMailboxPathMixin,
    CcbdMountPathMixin,
    CcbdOpsPathMixin,
    ProjectAnchorPathMixin,
)
from .paths_targets import TargetPathMixin


@dataclass(frozen=True)
class PathLayout(
    ProjectAnchorPathMixin,
    CcbdMailboxPathMixin,
    CcbdMountPathMixin,
    CcbdOpsPathMixin,
    CcbdArtifactsPathMixin,
    AgentRuntimePathMixin,
    AgentMailboxPathMixin,
    WorkspacePathMixin,
    TargetPathMixin,
):
    project_root: Path

    def __post_init__(self) -> None:
        root = Path(self.project_root).expanduser()
        try:
            root = root.resolve()
        except Exception:
            root = root.absolute()
        object.__setattr__(self, 'project_root', root)
        project_id = compute_project_id(root)
        object.__setattr__(self, '_project_id', project_id)
        placement = choose_runtime_state_placement(
            project_root=root,
            project_id=project_id,
            anchor_path=root / '.ccb',
        )
        object.__setattr__(self, '_runtime_state_placement', placement)
        object.__setattr__(self, '_state_root', placement.effective_path)

    @property
    def project_slug(self) -> str:
        return project_slug(self.project_root)

    @property
    def project_id(self) -> str:
        return self._project_id

    @property
    def project_socket_key(self) -> str:
        return self.project_id[:12]

    @property
    def runtime_state_placement(self) -> RuntimeStatePlacement:
        return self._runtime_state_placement

    @property
    def runtime_state_root(self) -> Path:
        return self._state_root

    @property
    def runtime_root_marker_path(self) -> Path:
        return runtime_root_marker_path(self.runtime_state_root)

    @property
    def runtime_root_ref_path(self) -> Path:
        return runtime_root_ref_path(self.ccb_dir)

    @property
    def runtime_marker_status(self) -> str:
        if self.runtime_state_placement.root_kind == 'project':
            return 'not_required'
        try:
            self._validate_runtime_root_marker()
            self._validate_runtime_root_ref()
            return 'ok'
        except FileNotFoundError:
            return 'missing'
        except Exception:
            return 'mismatch'

    def ensure_runtime_state_root(self, *, created_at: str | None = None) -> None:
        if self.runtime_state_placement.root_kind == 'project':
            return
        self.ccb_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_state_root.mkdir(parents=True, exist_ok=True)
        timestamp = created_at or _utc_now()
        self._validate_runtime_root_marker(allow_missing=True)
        self._validate_runtime_root_ref(allow_missing=True)
        atomic_write_json(self.runtime_root_marker_path, self._runtime_root_marker_payload(created_at=timestamp))
        atomic_write_json(self.runtime_root_ref_path, self._runtime_root_ref_payload(created_at=timestamp))

    def _project_socket_placement(self, stem: str) -> SocketPlacement:
        return choose_socket_placement(
            preferred_path=self.ccbd_dir / f'{stem}.sock',
            project_socket_key=self.project_socket_key,
            preferred_root_kind='runtime' if self.runtime_state_placement.root_kind == 'relocated' else 'project',
        )

    def _project_socket_path(self, stem: str) -> Path:
        return self._project_socket_placement(stem).effective_path

    def runtime_state_payload(self) -> dict[str, object]:
        payload = runtime_state_placement_payload(self.runtime_state_placement)
        payload['runtime_marker_status'] = self.runtime_marker_status
        payload['runtime_root_marker_path'] = str(self.runtime_root_marker_path)
        payload['runtime_root_ref_path'] = str(self.runtime_root_ref_path)
        return payload

    def _runtime_root_marker_payload(self, *, created_at: str) -> dict[str, object]:
        return {
            'schema_version': 1,
            'record_type': 'ccb_runtime_root',
            'project_id': self.project_id,
            'project_root': str(self.project_root),
            'anchor_path': str(self.ccb_dir),
            'runtime_root_path': str(self.runtime_state_root),
            'created_at': created_at,
        }

    def _runtime_root_ref_payload(self, *, created_at: str) -> dict[str, object]:
        return {
            'schema_version': 1,
            'record_type': 'ccb_runtime_root_ref',
            'project_id': self.project_id,
            'runtime_state_root': str(self.runtime_state_root),
            'created_at': created_at,
        }

    def _validate_runtime_root_marker(self, *, allow_missing: bool = False) -> None:
        payload = read_runtime_root_marker_payload(self.runtime_root_marker_path)
        if not payload:
            if allow_missing and not self.runtime_root_marker_path.exists():
                return
            if not self.runtime_root_marker_path.exists():
                raise FileNotFoundError(str(self.runtime_root_marker_path))
            raise RuntimeError(f'{self.runtime_root_marker_path} is invalid')
        expected = {
            'project_id': self.project_id,
            'project_root': str(self.project_root),
            'anchor_path': str(self.ccb_dir),
            'runtime_root_path': str(self.runtime_state_root),
        }
        _validate_expected_fields(payload, expected, label=str(self.runtime_root_marker_path))

    def _validate_runtime_root_ref(self, *, allow_missing: bool = False) -> None:
        payload = read_runtime_root_ref_payload(self.ccb_dir, project_id=self.project_id)
        if not payload:
            if allow_missing and not self.runtime_root_ref_path.exists():
                return
            if not self.runtime_root_ref_path.exists():
                raise FileNotFoundError(str(self.runtime_root_ref_path))
            raise RuntimeError(f'{self.runtime_root_ref_path} is invalid')
        expected = {
            'project_id': self.project_id,
            'runtime_state_root': str(self.runtime_state_root),
        }
        _validate_expected_fields(payload, expected, label=str(self.runtime_root_ref_path))


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise RuntimeError(f'{path} must contain a JSON object')
    return data


def _validate_expected_fields(payload: dict[str, object], expected: dict[str, str], *, label: str) -> None:
    for key, value in expected.items():
        recorded = str(payload.get(key) or '').strip()
        if recorded != value:
            raise RuntimeError(f'{label} field {key} mismatch: expected {value}, found {recorded or "<missing>"}')


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


__all__ = ['PathLayout']
