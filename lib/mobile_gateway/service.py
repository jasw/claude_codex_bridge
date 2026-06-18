from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from ccbd.socket_client import CcbdClientError

_DEFAULT_HOST = '127.0.0.1'
_DEFAULT_PORT = 8787
_SCHEMA_VERSION = 1
_CAPABILITIES = ('http_json', 'project_view')
_REDACTED_NAMESPACE_KEYS = ('socket_path', 'session_name')


@dataclass(frozen=True)
class ListenAddress:
    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT

    @property
    def text(self) -> str:
        return f'{self.host}:{self.port}'


class MobileGatewayError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = int(status_code)


class MobileGatewayService:
    def __init__(
        self,
        *,
        project_id: str,
        project_root: Path,
        ccbd_client_factory: Callable[[], object],
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._project_id = str(project_id)
        self._project_root = Path(project_root)
        self._ccbd_client_factory = ccbd_client_factory
        self._clock = clock or _utc_now

    @property
    def project_id(self) -> str:
        return self._project_id

    def health_payload(self) -> dict[str, object]:
        try:
            ccbd = self._client().ping('ccbd')
        except Exception as exc:
            return {
                'schema_version': _SCHEMA_VERSION,
                'status': 'degraded',
                'server_time': self._clock(),
                'mode': 'loopback_current_project',
                'project_id': self._project_id,
                'capabilities': list(_CAPABILITIES),
                'ccbd': {
                    'reachable': False,
                    'error': _error_text(exc),
                },
            }
        return {
            'schema_version': _SCHEMA_VERSION,
            'status': 'ok',
            'server_time': self._clock(),
            'mode': 'loopback_current_project',
            'project_id': self._project_id,
            'capabilities': list(_CAPABILITIES),
            'ccbd': _ccbd_health_summary(ccbd),
        }

    def projects_payload(self) -> dict[str, object]:
        ccbd = self._ping_or_unavailable()
        return {
            'schema_version': _SCHEMA_VERSION,
            'projects': [
                {
                    'id': self._project_id,
                    'display_name': self._project_root.name,
                    'health': str(ccbd.get('health') or 'unknown'),
                    'capabilities': list(_CAPABILITIES),
                }
            ],
        }

    def project_view_payload(self, project_id: str) -> dict[str, object]:
        requested = str(project_id or '').strip()
        if requested != self._project_id:
            raise MobileGatewayError('unknown project', status_code=404)
        payload = self._request_project_view()
        return _redact_project_view_payload(payload)

    def dispatch_get(self, path: str) -> tuple[int, dict[str, object]]:
        parsed = urlparse(path)
        route = parsed.path.rstrip('/') or '/'
        if route == '/v1/health':
            status = 200
            payload = self.health_payload()
            if payload.get('status') == 'degraded':
                status = 503
            return status, payload
        if route == '/v1/projects':
            return 200, self.projects_payload()
        prefix = '/v1/projects/'
        suffix = '/view'
        if route.startswith(prefix) and route.endswith(suffix):
            project_id = unquote(route[len(prefix):-len(suffix)].strip('/'))
            return 200, self.project_view_payload(project_id)
        raise MobileGatewayError('not found', status_code=404)

    def _client(self):
        return self._ccbd_client_factory()

    def _ping_or_unavailable(self) -> dict[str, object]:
        try:
            payload = self._client().ping('ccbd')
        except CcbdClientError as exc:
            raise MobileGatewayError(str(exc), status_code=503) from exc
        except Exception as exc:
            raise MobileGatewayError(_error_text(exc), status_code=503) from exc
        return dict(payload or {}) if isinstance(payload, dict) else {}

    def _request_project_view(self) -> dict[str, object]:
        try:
            payload = self._client().project_view(schema_version=1)
        except CcbdClientError as exc:
            raise MobileGatewayError(str(exc), status_code=503) from exc
        except Exception as exc:
            raise MobileGatewayError(_error_text(exc), status_code=503) from exc
        return dict(payload or {}) if isinstance(payload, dict) else {}


def parse_listen_address(value: str | None) -> ListenAddress:
    text = str(value or '').strip()
    if not text:
        return ListenAddress()
    if text.count(':') != 1:
        raise ValueError('listen address must be HOST:PORT')
    host, port_text = (item.strip() for item in text.rsplit(':', 1))
    if not host:
        host = _DEFAULT_HOST
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError('listen port must be an integer') from exc
    if port < 0 or port > 65535:
        raise ValueError('listen port must be between 0 and 65535')
    if not _is_loopback_host(host):
        raise ValueError('G1 mobile gateway only supports loopback listen addresses')
    return ListenAddress(host=host, port=port)


def build_mobile_gateway_server(listen: ListenAddress, service: MobileGatewayService) -> ThreadingHTTPServer:
    class _Handler(BaseHTTPRequestHandler):
        server_version = 'CCBMobileGateway/1'

        def do_GET(self) -> None:  # noqa: N802 - stdlib hook
            try:
                status, payload = service.dispatch_get(self.path)
            except MobileGatewayError as exc:
                status = exc.status_code
                payload = {
                    'schema_version': _SCHEMA_VERSION,
                    'status': 'error',
                    'error': _error_text(exc),
                }
            self._send_json(status, payload)

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook
            self._send_json(
                405,
                {
                    'schema_version': _SCHEMA_VERSION,
                    'status': 'error',
                    'error': 'method not allowed in G1',
                },
            )

        def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
            return

        def _send_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
            self.send_response(status)
            self.send_header('content-type', 'application/json; charset=utf-8')
            self.send_header('content-length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((listen.host, listen.port), _Handler)


def _redact_project_view_payload(payload: dict[str, object]) -> dict[str, object]:
    redacted = json.loads(json.dumps(payload))
    view = redacted.get('view') if isinstance(redacted, dict) else None
    if isinstance(view, dict):
        namespace = view.get('namespace')
        if isinstance(namespace, dict):
            for key in _REDACTED_NAMESPACE_KEYS:
                namespace.pop(key, None)
    return redacted


def _ccbd_health_summary(payload: dict[str, object]) -> dict[str, object]:
    return {
        'reachable': True,
        'project_id': payload.get('project_id'),
        'mount_state': payload.get('mount_state'),
        'health': payload.get('health'),
        'namespace_epoch': payload.get('namespace_epoch'),
        'namespace_ui_attachable': payload.get('namespace_ui_attachable'),
    }


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in {'localhost', '127.0.0.1', '::1'}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _error_text(exc: Exception) -> str:
    return str(exc or '').strip() or type(exc).__name__


__all__ = [
    'ListenAddress',
    'MobileGatewayError',
    'MobileGatewayService',
    'build_mobile_gateway_server',
    'parse_listen_address',
]
