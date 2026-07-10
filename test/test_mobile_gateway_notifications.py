from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from mobile_gateway import (
    MobileGatewayError,
    MobileGatewayProject,
    MobileGatewayProjectRegistry,
    MobileGatewayService,
    build_mobile_gateway_server,
    parse_listen_address,
)
from mobile_gateway.notifications import (
    MobileInvalidationSnapshot,
    MobileNotificationSnapshot,
    MobileNotificationStore,
    encode_sse_event,
)


class _ActivityCcbdClient:
    def __init__(
        self,
        *,
        project_id: str,
        project_root: str,
        display_name: str,
        agent: str = 'mobile',
        activity_state: str = 'active',
        namespace_epoch: int = 7,
    ) -> None:
        self.project_id = project_id
        self.project_root = project_root
        self.display_name = display_name
        self.agent = agent
        self.activity_state = activity_state
        self.namespace_epoch = namespace_epoch
        self.calls: list[tuple[object, ...]] = []

    def project_view(self, *, schema_version: int = 1) -> dict[str, object]:
        self.calls.append(('project_view', schema_version))
        return {
            'view': {
                'project': {
                    'id': self.project_id,
                    'root': self.project_root,
                    'display_name': self.display_name,
                },
                'namespace': {
                    'epoch': self.namespace_epoch,
                    'socket_path': '/tmp/private.sock',
                    'session_name': 'private-session',
                },
                'agents': [
                    {
                        'name': self.agent,
                        'provider': 'codex',
                        'activity_state': self.activity_state,
                        'activity_source': 'codex_runtime',
                        'activity_reason': 'codex_working_status_line',
                    }
                ],
            },
            'cache': {'generated_at': '2026-06-30T01:02:03Z'},
        }

    def ping(self, target: str = 'ccbd') -> dict[str, object]:
        self.calls.append(('ping', target))
        return {
            'project_id': self.project_id,
            'mount_state': 'mounted',
            'health': 'healthy',
        }


def _service(
    client: _ActivityCcbdClient,
    *,
    mobile_dir: Path,
    project_registry: MobileGatewayProjectRegistry | None = None,
) -> MobileGatewayService:
    return MobileGatewayService(
        project_id=client.project_id,
        project_root=Path(client.project_root),
        ccbd_client_factory=lambda: client,
        mobile_dir=mobile_dir,
        project_registry=project_registry,
        clock=lambda: '2026-06-30T01:02:03Z',
    )


def test_notification_store_emits_low_sensitive_task_completed_payload_with_stable_dedupe(tmp_path: Path) -> None:
    store = MobileNotificationStore(tmp_path / 'mobile')
    active = MobileNotificationSnapshot(
        project_id='proj-demo',
        project_short_name='demo',
        namespace_epoch=7,
        agent='worker',
        activity_state='active',
        observed_at='2026-06-30T01:00:00Z',
    )
    completed = MobileNotificationSnapshot(
        project_id='proj-demo',
        project_short_name='demo',
        namespace_epoch=7,
        agent='worker',
        activity_state='idle',
        observed_at='2026-06-30T01:02:03Z',
    )

    assert store.sync_snapshots([active]) == []
    emitted = store.sync_snapshots([completed])
    assert len(emitted) == 1
    assert store.sync_snapshots([completed]) == []

    payload = emitted[0].to_payload()
    assert set(payload) == {
        'id',
        'kind',
        'project_id',
        'project_short_name',
        'agent',
        'completed_at',
        'dedupe_key',
    }
    assert payload == store.events_since(None)[0].to_payload()
    assert store.events_since(str(payload['id'])) == []
    assert payload['kind'] == 'task_completed'
    assert payload['project_id'] == 'proj-demo'
    assert payload['project_short_name'] == 'demo'
    assert payload['agent'] == 'worker'
    assert payload['completed_at'] == '2026-06-30T01:02:03Z'
    assert payload['dedupe_key'] == 'proj-demo:7:worker:1'
    assert 'data: ' in encode_sse_event(payload).decode('utf-8')
    public_json = json.dumps(payload)
    for sensitive in ('prompt', 'reply', 'path', 'output', 'error', '/tmp/private.sock'):
        assert sensitive not in public_json


def test_notification_store_emits_multi_project_transitions(tmp_path: Path) -> None:
    store = MobileNotificationStore(tmp_path / 'mobile')

    store.sync_snapshots(
        [
            MobileNotificationSnapshot('proj-one', 'one', 1, 'agent1', 'active', '2026-06-30T01:00:00Z'),
            MobileNotificationSnapshot('proj-two', 'two', 2, 'agent2', 'active', '2026-06-30T01:00:00Z'),
        ]
    )
    emitted = store.sync_snapshots(
        [
            MobileNotificationSnapshot('proj-one', 'one', 1, 'agent1', 'idle', '2026-06-30T01:01:00Z'),
            MobileNotificationSnapshot('proj-two', 'two', 2, 'agent2', 'failed', '2026-06-30T01:01:00Z'),
        ]
    )

    assert [event.project_id for event in emitted] == ['proj-one', 'proj-two']
    assert [event.dedupe_key for event in emitted] == [
        'proj-one:1:agent1:1',
        'proj-two:2:agent2:1',
    ]


def test_invalidation_store_dedupes_redacts_and_bounds_event_journal(tmp_path: Path) -> None:
    store = MobileNotificationStore(tmp_path / 'mobile', recent_limit=3)
    baseline = MobileInvalidationSnapshot(
        'proj-demo', 'demo', 7, 'worker', 'active', 'fingerprint-one', '2026-06-30T01:00:00Z'
    )
    activity_changed = MobileInvalidationSnapshot(
        'proj-demo', 'demo', 7, 'worker', 'idle', 'fingerprint-one', '2026-06-30T01:01:00Z'
    )
    conversation_changed = MobileInvalidationSnapshot(
        'proj-demo', 'demo', 7, 'worker', 'idle', 'fingerprint-two', '2026-06-30T01:02:00Z'
    )

    assert store.sync_invalidations([baseline]) == []
    activity_events = store.sync_invalidations([activity_changed])
    assert {event.kind for event in activity_events} == {
        'agent_activity_changed',
        'project_summary_changed',
    }
    conversation_events = store.sync_invalidations([conversation_changed])
    assert [event.kind for event in conversation_events] == ['conversation_changed']
    assert store.sync_invalidations([conversation_changed]) == []

    records = store.events_since(None)
    assert len(records) <= 3
    payload = json.dumps([event.to_payload() for event in records])
    assert 'fingerprint-one' not in payload
    assert 'fingerprint-two' not in payload
    assert '/srv/' not in payload


def test_notification_watcher_is_shared_across_sse_clients(tmp_path: Path) -> None:
    client = _ActivityCcbdClient(project_id='proj-demo', project_root='/srv/demo', display_name='demo')
    service = _service(client, mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': pairing['pairing_code']})
    headers = {'Authorization': f'Bearer {claim["device_token"]}'}

    path = ('/v1/mobile/notifications?watch_project_id=proj-demo&watch_agent=mobile'
            '&watch_namespace_epoch=7&watch_provider=codex')
    service.notification_events_since(path, headers)
    first_scan_count = len([call for call in client.calls if call[0] == 'project_view'])
    service.notification_events_since(path, headers)
    second_scan_count = len([call for call in client.calls if call[0] == 'project_view'])

    assert first_scan_count == 0
    assert second_scan_count == first_scan_count
    audit = service.invalidation_audit_payload()
    assert audit['watch_project_view_calls'] == 0
    assert audit['ccbd_project_view_requests'] == 0
    assert audit['mobile_conversation_requests'] == 0


def test_notification_audit_http_route_exposes_low_sensitive_counters(tmp_path: Path) -> None:
    client = _ActivityCcbdClient(project_id='proj-demo', project_root='/srv/demo', display_name='demo')
    service = _service(client, mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': pairing['pairing_code']})
    headers = {'Authorization': f'Bearer {claim["device_token"]}'}

    service.notification_events_since('/v1/mobile/notifications?once=1', headers)
    status, payload = service.dispatch_get('/v1/mobile/notifications/audit', headers)

    assert status == 200
    assert payload['status'] == 'ok'
    assert payload['audit']['watch_refreshes'] >= 1
    assert 'ccbd_project_view_requests' in payload['audit']


def test_notification_service_requires_notify_scope_and_default_pairing_grants_it(tmp_path: Path) -> None:
    client = _ActivityCcbdClient(project_id='proj-demo', project_root='/srv/demo', display_name='demo')
    service = _service(client, mobile_dir=tmp_path / 'mobile')

    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    assert 'notify' in pairing['scopes']
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': pairing['pairing_code']})
    assert 'notify' in claim['host_profile']['scopes']
    assert service.notification_events_since(
        '/v1/mobile/notifications?once=1',
        {'Authorization': f'Bearer {claim["device_token"]}'},
    ) == []

    view_only = service.create_pairing_payload(
        gateway_url='http://127.0.0.1:8787',
        scopes=('view',),
    )
    _, view_claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': view_only['pairing_code']})
    with pytest.raises(MobileGatewayError) as denied:
        service.notification_events_since(
            '/v1/mobile/notifications?once=1',
            {'Authorization': f'Bearer {view_claim["device_token"]}'},
        )
    assert denied.value.status_code == 403


def test_notification_service_observes_only_explicit_project_views(tmp_path: Path) -> None:
    first = _ActivityCcbdClient(project_id='proj-one', project_root='/srv/one', display_name='one')
    second = _ActivityCcbdClient(project_id='proj-two', project_root='/srv/two', display_name='two')
    registry = MobileGatewayProjectRegistry(
        [
            MobileGatewayProject('proj-one', Path('/srv/one'), lambda: first, display_name='one'),
            MobileGatewayProject('proj-two', Path('/srv/two'), lambda: second, display_name='two'),
        ]
    )
    service = _service(first, mobile_dir=tmp_path / 'mobile', project_registry=registry)
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': pairing['pairing_code']})
    headers = {'Authorization': f'Bearer {claim["device_token"]}'}

    assert service.notification_events_since('/v1/mobile/notifications?once=1', headers) == []
    service.project_view_payload('proj-one')
    service.project_view_payload('proj-two')
    first.activity_state = 'idle'
    second.activity_state = 'idle'
    service.project_view_payload('proj-one')
    service.project_view_payload('proj-two')
    events = service.notification_events_since('/v1/mobile/notifications?once=1', headers)

    completion_events = [event for event in events if event['kind'] == 'task_completed']
    invalidations = [event for event in events if event['kind'] != 'task_completed']
    assert [event['project_id'] for event in completion_events] == ['proj-one', 'proj-two']
    assert [event['dedupe_key'] for event in completion_events] == [
        'proj-one:7:mobile:1',
        'proj-two:7:mobile:1',
    ]
    assert {event['kind'] for event in invalidations} == {
        'agent_activity_changed',
        'project_summary_changed',
    }


def test_notification_completion_records_project_activity(tmp_path: Path) -> None:
    client = _ActivityCcbdClient(
        project_id='proj-demo',
        project_root='/srv/demo',
        display_name='demo',
    )
    service = _service(client, mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post(
        '/v1/pairing/claim',
        {'pairing_code': pairing['pairing_code']},
    )
    headers = {'Authorization': f'Bearer {claim["device_token"]}'}

    assert service.notification_events_since('/v1/mobile/notifications?once=1', headers) == []
    service.project_view_payload('proj-demo')
    client.activity_state = 'idle'
    service.project_view_payload('proj-demo')
    events = service.notification_events_since('/v1/mobile/notifications?once=1', headers)
    projects = service.projects_payload()

    assert any(event['kind'] == 'task_completed' for event in events)
    assert projects['projects'][0]['last_activity_at'] == '2026-06-30T01:02:03Z'


def test_notification_http_sse_once_stream_smoke(tmp_path: Path) -> None:
    client = _ActivityCcbdClient(project_id='proj-demo', project_root='/srv/demo', display_name='demo')
    service = _service(client, mobile_dir=tmp_path / 'mobile')
    pairing = service.create_pairing_payload(gateway_url='http://127.0.0.1:8787')
    _, claim = service.dispatch_post('/v1/pairing/claim', {'pairing_code': pairing['pairing_code']})
    token = str(claim['device_token'])
    headers = {'Authorization': f'Bearer {token}'}
    assert service.notification_events_since('/v1/mobile/notifications?once=1', headers) == []
    service.project_view_payload('proj-demo')
    client.activity_state = 'idle'
    service.project_view_payload('proj-demo')

    server = build_mobile_gateway_server(parse_listen_address('127.0.0.1:0'), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    try:
        thread.start()
        host, port = server.server_address[:2]
        request = Request(
            f'http://{host}:{port}/v1/mobile/notifications?once=1',
            headers={'Authorization': f'Bearer {token}', 'Accept': 'text/event-stream'},
        )
        with urlopen(request, timeout=2) as response:
            body = response.read().decode('utf-8')
            assert response.headers.get_content_type() == 'text/event-stream'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert 'event: task_completed' in body
    assert 'data: ' in body
    assert 'proj-demo:7:mobile:1' in body
    assert '/tmp/private.sock' not in body


def test_completion_survives_more_than_one_hundred_invalidation_updates(tmp_path: Path) -> None:
    store = MobileNotificationStore(tmp_path / 'mobile', recent_limit=8, completion_limit=128)
    active = MobileNotificationSnapshot('p', 'p', 1, 'agent', 'active', '2026-07-10T00:00:00Z')
    done = MobileNotificationSnapshot('p', 'p', 1, 'agent', 'idle', '2026-07-10T00:00:01Z')
    store.sync_snapshots([active])
    completion = store.sync_snapshots([done])[0]
    baseline = MobileInvalidationSnapshot('p', 'p', 1, 'agent', 'idle', 'a', '2026-07-10T00:00:02Z')
    store.sync_invalidations([baseline])
    for index in range(140):
        store.sync_invalidations([
            MobileInvalidationSnapshot(
                'p', 'p', 1, 'agent', 'idle', f'fingerprint-{index}',
                f'2026-07-10T00:03:{index % 60:02d}Z',
            )
        ])
    events = store.events_since('mnotif_000000000000')
    assert any(event.id == completion.id and event.kind == 'task_completed' for event in events)
    assert len(store._invalidation_events()) <= 8


def test_trimmed_cursor_returns_explicit_resync(tmp_path: Path) -> None:
    store = MobileNotificationStore(tmp_path / 'mobile', recent_limit=1, completion_limit=1)
    first = MobileNotificationSnapshot('p', 'p', 1, 'a', 'active', '2026-07-10T00:00:00Z')
    done = MobileNotificationSnapshot('p', 'p', 1, 'a', 'idle', '2026-07-10T00:00:01Z')
    again = MobileNotificationSnapshot('p', 'p', 1, 'a', 'active', '2026-07-10T00:00:02Z')
    final = MobileNotificationSnapshot('p', 'p', 1, 'a', 'idle', '2026-07-10T00:00:03Z')
    store.sync_snapshots([first, done, again, final])
    events = store.events_since('mnotif_000000000001')
    assert events[-1].kind == 'resync_required'
    assert events[-1].id.startswith('mnotif_resync_')
    assert store.events_since(events[-1].id) == []
