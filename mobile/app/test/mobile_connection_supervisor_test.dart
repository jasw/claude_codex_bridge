import 'dart:async';

import 'package:ccb_mobile/features/project_home/mobile_connection_supervisor.dart';
import 'package:ccb_mobile/pairing/gateway_pairing.dart';
import 'package:ccb_mobile/repository/gateway_mobile_ccb_repository.dart';
import 'package:ccb_mobile/transport/http_gateway_transport.dart';
import 'package:ccb_mobile/transport/gateway_transport.dart';
import 'package:ccb_mobile/transport/route_provider.dart';
import 'package:test/test.dart';

void main() {
  test('temporary route failure keeps profile and retries to online', () async {
    final states = <MobileConnectionState>[];
    final repository = _ProbeRepository()..fail = true;
    final supervisor = MobileConnectionSupervisor(
      onChanged: (value) => states.add(value.state),
      initialDelay: Duration.zero,
      maxDelay: Duration.zero,
    );
    supervisor.start(profile: _profile(), probe: repository);
    await Future<void>.delayed(Duration.zero);
    expect(states, contains(MobileConnectionState.reconnecting));
    repository.fail = false;
    supervisor.foregroundResume();
    await Future<void>.delayed(Duration.zero);
    expect(supervisor.snapshot.state, MobileConnectionState.online);
    supervisor.dispose();
  });

  test('only 401/403 is authentication required', () {
    final supervisor = MobileConnectionSupervisor(onChanged: (_) {});
    supervisor.start(profile: _profile(), probe: _ProbeRepository());
    supervisor.reportFailure(GatewayHttpException(Uri(), 401, 'revoked'));
    expect(
      supervisor.snapshot.state,
      MobileConnectionState.authenticationRequired,
    );
    supervisor.dispose();
  });
}

GatewayPairedHost _profile() => GatewayPairedHost(
  profile: GatewayHostProfile(
    hostId: 'host',
    deviceId: 'device',
    scopes: const {'view'},
    routeProvider: RouteProvider(
      kind: RouteProviderKind.lan,
      gatewayUrl: Uri.parse('http://127.0.0.1'),
    ),
  ),
  deviceToken: 'token',
);

class _ProbeRepository implements MobileGatewayProfileHealthProbe {
  bool fail = false;
  @override
  Future<GatewayHealth> health() async {
    if (fail) throw TimeoutException('route');
    return GatewayHealth(
      status: 'ok',
      serverTime: DateTime.utc(2026),
      capabilities: {},
    );
  }

  @override
  Future<GatewayDevice> device() async => GatewayDevice(
    deviceId: 'd',
    projectId: 'p',
    revoked: false,
    scopes: {},
    routeProvider: RouteProviderKind.lan,
  );
}
