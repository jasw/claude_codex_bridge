import 'dart:async';
import 'dart:math';

import '../../pairing/gateway_pairing.dart';
import '../../repository/gateway_mobile_ccb_repository.dart';
import '../../transport/http_gateway_transport.dart';

/// App-lifetime authority for route/auth state. Pages keep their cached data;
/// they report request outcomes here instead of independently clearing profiles
/// or scheduling retries.
enum MobileConnectionState {
  offline,
  connecting,
  online,
  degraded,
  reconnecting,
  authenticationRequired,
  stopped,
}

class MobileConnectionSnapshot {
  const MobileConnectionSnapshot(this.state, {this.retryIn});

  final MobileConnectionState state;
  final Duration? retryIn;
}

typedef MobileConnectionStateListener =
    void Function(MobileConnectionSnapshot snapshot);

class MobileConnectionSupervisor {
  MobileConnectionSupervisor({
    required MobileConnectionStateListener onChanged,
    this.initialDelay = const Duration(seconds: 1),
    this.maxDelay = const Duration(seconds: 30),
    Random? random,
  }) : _onChanged = onChanged,
       _random = random ?? Random();

  final MobileConnectionStateListener _onChanged;
  final Duration initialDelay;
  final Duration maxDelay;
  final Random _random;
  Timer? _retryTimer;
  GatewayPairedHost? _profile;
  MobileGatewayProfileHealthProbe? _probe;
  Duration? _nextDelay;
  var _disposed = false;
  var _probing = false;

  MobileConnectionSnapshot _snapshot = const MobileConnectionSnapshot(
    MobileConnectionState.stopped,
  );
  MobileConnectionSnapshot get snapshot => _snapshot;

  void start({
    required GatewayPairedHost profile,
    MobileGatewayProfileHealthProbe? probe,
  }) {
    _profile = profile;
    _probe = probe;
    _nextDelay = initialDelay;
    _retryTimer?.cancel();
    _probeNow();
  }

  void reportSuccess() {
    if (_profile == null || _disposed) return;
    _retryTimer?.cancel();
    _nextDelay = initialDelay;
    _emit(const MobileConnectionSnapshot(MobileConnectionState.online));
  }

  void reportFailure(Object error) {
    if (_profile == null || _disposed) return;
    if (_isAuthoritativeAuthFailure(error)) {
      _retryTimer?.cancel();
      _emit(
        const MobileConnectionSnapshot(
          MobileConnectionState.authenticationRequired,
        ),
      );
      return;
    }
    _scheduleRetry();
  }

  void foregroundResume() {
    if (_profile == null || _disposed) return;
    _retryTimer?.cancel();
    _probeNow();
  }

  void retryNow() => foregroundResume();

  void stop() {
    _retryTimer?.cancel();
    _retryTimer = null;
    _profile = null;
    _probe = null;
    _emit(const MobileConnectionSnapshot(MobileConnectionState.stopped));
  }

  void dispose() {
    _disposed = true;
    stop();
  }

  Future<void> _probeNow() async {
    if (_probing || _profile == null || _disposed) return;
    _probing = true;
    _emit(const MobileConnectionSnapshot(MobileConnectionState.connecting));
    try {
      final probe = _probe;
      if (probe != null) {
        final health = await probe.health().timeout(const Duration(seconds: 4));
        if (health.status.toLowerCase() != 'ok') {
          throw GatewayHttpException(Uri(), 503, 'gateway health is degraded');
        }
        final device = await probe.device().timeout(const Duration(seconds: 4));
        if (device.revoked) {
          throw GatewayHttpException(Uri(), 401, 'device revoked');
        }
      }
      reportSuccess();
    } catch (error) {
      reportFailure(error);
    } finally {
      _probing = false;
    }
  }

  void _scheduleRetry() {
    if (_retryTimer != null || _disposed) return;
    final base = _nextDelay ?? initialDelay;
    final jitter = 0.9 + (_random.nextDouble() * 0.2);
    final delay = Duration(
      milliseconds: max(1, (base.inMilliseconds * jitter).round()),
    );
    _emit(
      MobileConnectionSnapshot(
        MobileConnectionState.reconnecting,
        retryIn: delay,
      ),
    );
    final doubled = Duration(milliseconds: base.inMilliseconds * 2);
    _nextDelay = doubled > maxDelay ? maxDelay : doubled;
    _retryTimer = Timer(delay, () {
      _retryTimer = null;
      _probeNow();
    });
  }

  bool _isAuthoritativeAuthFailure(Object error) =>
      error is GatewayHttpException &&
      (error.statusCode == 401 || error.statusCode == 403);

  void _emit(MobileConnectionSnapshot value) {
    _snapshot = value;
    if (!_disposed) _onChanged(value);
  }
}
