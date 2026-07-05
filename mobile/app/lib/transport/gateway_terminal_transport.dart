import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'gateway_transport.dart';
import 'terminal_transport.dart';

class GatewayTerminalTransport implements TerminalTransport {
  GatewayTerminalTransport({required GatewayTransport transport})
    : _transport = transport;

  final GatewayTransport _transport;

  @override
  Future<TerminalSession> open(TerminalOpenRequest request) async {
    final handle = await _transport.openTerminal(
      GatewayTerminalOpenRequest.fromCcbTarget(
        request.target,
        geometry: request.geometry,
      ),
    );
    return _GatewayTerminalSession(
      transport: _transport,
      request: request,
      handle: handle,
    );
  }
}

class _GatewayTerminalSession implements TerminalSession {
  _GatewayTerminalSession({
    required GatewayTransport transport,
    required TerminalOpenRequest request,
    required GatewayTerminalHandle handle,
  }) : _transport = transport,
       _request = request,
       _handle = handle,
       _geometry = request.geometry {
    unawaited(
      _connect().catchError((Object error, StackTrace stackTrace) {
        if (!_closed) {
          _output.addError(error, stackTrace);
        }
      }),
    );
  }

  final GatewayTransport _transport;
  final TerminalOpenRequest _request;
  GatewayTerminalHandle _handle;
  TerminalGeometry _geometry;
  final _output = StreamController<Uint8List>.broadcast();
  StreamSubscription<GatewayTerminalFrame>? _subscription;
  Future<void>? _renewal;
  Completer<void>? _connectionReady;
  int _nextInputSequence = 1;
  int _resumeCursor = 0;
  bool _closed = false;

  @override
  String get launchedCommand => _request.attachCommand;

  @override
  Stream<Uint8List> get output => _output.stream;

  @override
  Future<void> writeBytes(List<int> bytes) {
    return _sendSequenced(
      GatewayTerminalFrame.input(
        sequence: _nextInputSequence++,
        bytes: List<int>.from(bytes),
      ),
    );
  }

  @override
  Future<void> paste(String text) {
    return _sendSequenced(
      GatewayTerminalFrame.paste(sequence: _nextInputSequence++, text: text),
    );
  }

  @override
  Future<void> resize(TerminalGeometry geometry) {
    _geometry = geometry;
    return _transport.sendTerminalFrame(
      _handle,
      GatewayTerminalFrame.resize(geometry),
    );
  }

  @override
  Future<void> reconnect() async {
    if (_closed) {
      throw const TerminalTransportException('gateway terminal is closed');
    }
    final renewal = _renewal;
    if (renewal != null) {
      await renewal;
      return;
    }
    await _cancelSubscription();
    await _connect(resumeCursor: _resumeCursor);
  }

  @override
  Future<void> close() async {
    if (_closed) {
      return;
    }
    _closed = true;
    await _transport.sendTerminalFrame(
      _handle,
      GatewayTerminalFrame.closed('client_closed'),
    );
    await _cancelSubscription();
    await _closeOutput();
  }

  Future<void> _sendSequenced(GatewayTerminalFrame frame) async {
    final renewal = _renewal;
    if (renewal != null) {
      await renewal;
    }
    return _transport.sendTerminalFrame(_handle, frame);
  }

  Future<void> _connect({int? resumeCursor}) {
    final ready = Completer<void>();
    _connectionReady = ready;
    _subscription = _transport
        .terminalFrames(_handle, resumeCursor: resumeCursor)
        .listen(
          _handleFrame,
          onError: _handleTransportError,
          onDone: _handleDone,
        );
    return ready.future.timeout(
      const Duration(seconds: 5),
      onTimeout:
          () =>
              throw const TerminalTransportException(
                'terminal stream connect timeout',
              ),
    );
  }

  void _handleFrame(GatewayTerminalFrame frame) {
    switch (frame.type) {
      case GatewayTerminalFrameType.output:
        final sequence = _int(frame.payload['seq']);
        if (sequence > _resumeCursor) {
          _resumeCursor = sequence;
        }
        final bytes = base64Decode(
          (frame.payload['bytes_b64'] ?? '').toString(),
        );
        _output.add(Uint8List.fromList(bytes));
      case GatewayTerminalFrameType.error:
        final code =
            (frame.payload['code'] ?? 'gateway terminal error').toString();
        if (_isRenewableTerminalError(code)) {
          _scheduleRenewTerminalHandle();
          return;
        }
        _completeConnectionError(TerminalTransportException(code));
        _output.addError(TerminalTransportException(code));
      case GatewayTerminalFrameType.closed:
        _completeConnectionError(
          const TerminalTransportException('terminal stream closed'),
        );
        _closeOutput();
      case GatewayTerminalFrameType.open:
        final sequence = _int(frame.payload['last_input_seq']);
        if (sequence >= _nextInputSequence) {
          _nextInputSequence = sequence + 1;
        }
        _completeConnectionReady();
      case GatewayTerminalFrameType.input:
      case GatewayTerminalFrameType.paste:
      case GatewayTerminalFrameType.resize:
    }
  }

  void _handleDone() {
    if (!_closed && !_output.isClosed) {
      const error = TerminalTransportException('terminal stream disconnected');
      _completeConnectionError(error);
      _output.addError(error);
    }
  }

  void _handleTransportError(Object error, StackTrace stackTrace) {
    if (_isRenewableTerminalException(error)) {
      _scheduleRenewTerminalHandle();
      return;
    }
    _completeConnectionError(error, stackTrace);
    _output.addError(error, stackTrace);
  }

  void _scheduleRenewTerminalHandle() {
    final connection = _connectionReady;
    final renewal = _renewTerminalHandle();
    renewal
        .then((_) {
          if (connection != null && !connection.isCompleted) {
            connection.complete();
          }
        })
        .catchError((Object error, StackTrace stackTrace) {
          if (connection != null && !connection.isCompleted) {
            connection.completeError(error, stackTrace);
          }
          if (!_closed) {
            _output.addError(error, stackTrace);
          }
        });
  }

  Future<void> _renewTerminalHandle() {
    if (_closed) {
      return Future<void>.value();
    }
    final existing = _renewal;
    if (existing != null) {
      return existing;
    }
    final renewal = _doRenewTerminalHandle();
    _renewal = renewal;
    return renewal.whenComplete(() {
      if (identical(_renewal, renewal)) {
        _renewal = null;
      }
    });
  }

  Future<void> _doRenewTerminalHandle() async {
    await _cancelSubscription();
    final handle = await _transport.openTerminal(
      GatewayTerminalOpenRequest.fromCcbTarget(
        _request.target,
        geometry: _geometry,
      ),
    );
    if (_closed) {
      return;
    }
    _handle = handle;
    _resumeCursor = 0;
    await _connect();
  }

  void _completeConnectionReady() {
    final connection = _connectionReady;
    if (connection != null && !connection.isCompleted) {
      connection.complete();
    }
  }

  void _completeConnectionError(Object error, [StackTrace? stackTrace]) {
    final connection = _connectionReady;
    if (connection != null && !connection.isCompleted) {
      if (stackTrace == null) {
        connection.completeError(error);
      } else {
        connection.completeError(error, stackTrace);
      }
    }
  }

  Future<void> _closeOutput() async {
    if (!_output.isClosed) {
      await _output.close();
    }
  }

  Future<void> _cancelSubscription() async {
    final subscription = _subscription;
    _subscription = null;
    if (subscription == null) {
      return;
    }
    try {
      await subscription.cancel().timeout(const Duration(seconds: 2));
    } on TimeoutException {
      // Reconnect and renewal must not hang forever on a stalled socket close.
    }
  }
}

bool _isRenewableTerminalException(Object error) {
  if (error is TerminalTransportException) {
    return _isRenewableTerminalError(error.message);
  }
  final text = error.toString().toLowerCase();
  return text.contains('connection closed before full header') ||
      (text.contains('websocket') && text.contains('/v1/terminals/'));
}

bool _isRenewableTerminalError(String code) {
  final normalized = code.trim().toLowerCase();
  return normalized == 'expired' ||
      normalized == 'terminal_token_expired' ||
      normalized == 'token_expired' ||
      normalized == 'stale_resume_cursor' ||
      normalized == 'invalid_token' ||
      normalized == 'terminal_token_denied';
}

int _int(Object? value) {
  if (value is int) {
    return value;
  }
  return int.tryParse((value ?? '').toString()) ?? 0;
}
