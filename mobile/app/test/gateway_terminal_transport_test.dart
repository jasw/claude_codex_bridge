import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:ccb_mobile/ccb_mobile.dart';
import 'package:test/test.dart';

void main() {
  test(
    'opens gateway terminal and forwards frames as terminal session',
    () async {
      final gateway = _FakeGatewayTransport();
      final transport = GatewayTerminalTransport(transport: gateway);

      final session = await transport.open(
        TerminalOpenRequest.gateway(
          target: CcbTerminalTarget.agent(
            projectId: 'proj-demo',
            namespaceEpoch: 4,
            agent: 'mobile',
            window: 'main',
            paneId: '%2',
            scopes: {CcbScope.view, CcbScope.terminalInput},
          ),
          geometry: const TerminalGeometry(columns: 100, rows: 30),
        ),
      );

      expect(gateway.openRequests.single.target.toJson(), {
        'kind': 'agent',
        'agent': 'mobile',
        'window': 'main',
        'pane_id': '%2',
      });
      expect(
        session.launchedCommand,
        'gateway terminal stream proj-demo/mobile',
      );

      final output = <String>[];
      final subscription = session.output.map(utf8.decode).listen(output.add);
      gateway.emit(
        GatewayTerminalFrame.output(sequence: 1, bytes: utf8.encode('hello')),
      );
      await pumpEventQueue();
      expect(output, ['hello']);

      await session.writeBytes([0x61]);
      await session.paste('paste me');
      await session.resize(const TerminalGeometry(columns: 120, rows: 36));
      await session.close();

      expect(gateway.sentFrames.map((frame) => frame.toJson()), [
        {
          'type': 'input',
          'seq': 1,
          'bytes_b64': base64Encode([0x61]),
        },
        {'type': 'paste', 'seq': 2, 'text': 'paste me'},
        {
          'type': 'resize',
          'columns': 120,
          'rows': 36,
          'pixel_width': 0,
          'pixel_height': 0,
        },
        {'type': 'closed', 'reason': 'client_closed'},
      ]);
      await subscription.cancel();
    },
  );

  test('gateway terminal reconnect uses latest output resume cursor', () async {
    final gateway = _FakeGatewayTransport();
    final session = await GatewayTerminalTransport(transport: gateway).open(
      TerminalOpenRequest.gateway(
        target: CcbTerminalTarget.agent(
          projectId: 'proj-demo',
          namespaceEpoch: 4,
          agent: 'mobile',
          scopes: {CcbScope.view, CcbScope.terminalInput},
        ),
      ),
    );

    gateway.emit(
      GatewayTerminalFrame.output(sequence: 7, bytes: utf8.encode('hello')),
    );
    await pumpEventQueue();

    await session.reconnect();

    expect(gateway.resumeCursors, [null, 7]);
  });

  test('gateway terminal reconnects after stream disconnect', () async {
    final gateway = _FakeGatewayTransport();
    final session = await GatewayTerminalTransport(transport: gateway).open(
      TerminalOpenRequest.gateway(
        target: CcbTerminalTarget.agent(
          projectId: 'proj-demo',
          namespaceEpoch: 4,
          agent: 'mobile',
          scopes: {CcbScope.view, CcbScope.terminalInput},
        ),
      ),
    );
    final output = <String>[];
    final errors = <Object>[];
    final subscription = session.output
        .map(utf8.decode)
        .listen(output.add, onError: errors.add);

    gateway.emit(
      GatewayTerminalFrame.output(sequence: 7, bytes: utf8.encode('before')),
    );
    await pumpEventQueue();
    await gateway.closeCurrentStream();
    await pumpEventQueue();

    expect(output, ['before']);
    expect(
      errors,
      contains(
        isA<TerminalTransportException>().having(
          (error) => error.message,
          'message',
          'terminal stream disconnected',
        ),
      ),
    );

    await session.reconnect();
    expect(gateway.resumeCursors, [null, 7]);

    gateway.emit(
      GatewayTerminalFrame.output(sequence: 8, bytes: utf8.encode('after')),
    );
    await pumpEventQueue();

    expect(output, ['before', 'after']);
    await session.writeBytes([0x03]);
    expect(gateway.sentFrames.last.toJson(), {
      'type': 'input',
      'seq': 1,
      'bytes_b64': base64Encode([0x03]),
    });

    await subscription.cancel();
  });

  test('gateway terminal renews handle when token expires', () async {
    final gateway = _FakeGatewayTransport();
    final session = await GatewayTerminalTransport(transport: gateway).open(
      TerminalOpenRequest.gateway(
        target: CcbTerminalTarget.agent(
          projectId: 'proj-demo',
          namespaceEpoch: 4,
          agent: 'mobile',
          scopes: {CcbScope.view, CcbScope.terminalInput},
        ),
      ),
    );
    final output = <String>[];
    final errors = <Object>[];
    final subscription = session.output
        .map(utf8.decode)
        .listen(output.add, onError: errors.add);

    gateway.emit(
      GatewayTerminalFrame.output(sequence: 7, bytes: utf8.encode('before')),
    );
    await pumpEventQueue();
    await session.resize(
      const TerminalGeometry(
        columns: 132,
        rows: 43,
        pixelWidth: 1000,
        pixelHeight: 700,
      ),
    );

    gateway.emit(GatewayTerminalFrame.error('expired'));
    await _waitFor(
      () =>
          gateway.openRequests.length == 2 && gateway.resumeCursors.length == 2,
    );

    expect(gateway.resumeCursors, [null, null]);
    expect(gateway.rejectedResumeCursors, isEmpty);
    expect(gateway.openRequests.last.geometry.columns, 132);
    expect(gateway.openRequests.last.geometry.rows, 43);
    expect(gateway.openRequests.last.geometry.pixelWidth, 1000);
    expect(gateway.openRequests.last.geometry.pixelHeight, 700);

    gateway.emit(
      GatewayTerminalFrame.output(sequence: 1, bytes: utf8.encode('after')),
    );
    await pumpEventQueue();

    expect(output, ['before', 'after']);
    expect(errors, isEmpty);
    await subscription.cancel();
  });

  test('gateway terminal renews handle when resume cursor is stale', () async {
    final gateway = _FakeGatewayTransport();
    final session = await GatewayTerminalTransport(transport: gateway).open(
      TerminalOpenRequest.gateway(
        target: CcbTerminalTarget.agent(
          projectId: 'proj-demo',
          namespaceEpoch: 4,
          agent: 'mobile',
          scopes: {CcbScope.view, CcbScope.terminalInput},
        ),
      ),
    );
    final output = <String>[];
    final errors = <Object>[];
    final subscription = session.output
        .map(utf8.decode)
        .listen(output.add, onError: errors.add);

    gateway.emit(
      GatewayTerminalFrame.output(sequence: 7, bytes: utf8.encode('before')),
    );
    await pumpEventQueue();

    gateway.emit(GatewayTerminalFrame.error('stale_resume_cursor'));
    await _waitFor(
      () =>
          gateway.openRequests.length == 2 && gateway.resumeCursors.length == 2,
    );

    expect(gateway.resumeCursors, [null, null]);
    expect(gateway.rejectedResumeCursors, isEmpty);
    gateway.emit(
      GatewayTerminalFrame.output(sequence: 1, bytes: utf8.encode('after')),
    );
    await pumpEventQueue();

    expect(output, ['before', 'after']);
    expect(errors, isEmpty);
    await subscription.cancel();
  });

  test(
    'gateway terminal renews handle when reconnect token is invalid',
    () async {
      final gateway = _FakeGatewayTransport();
      final session = await GatewayTerminalTransport(transport: gateway).open(
        TerminalOpenRequest.gateway(
          target: CcbTerminalTarget.agent(
            projectId: 'proj-demo',
            namespaceEpoch: 4,
            agent: 'mobile',
            scopes: {CcbScope.view, CcbScope.terminalInput},
          ),
        ),
      );

      gateway.emit(
        GatewayTerminalFrame.output(sequence: 7, bytes: utf8.encode('before')),
      );
      await pumpEventQueue();
      gateway.invalidTerminalIds.add(gateway.frameHandles.last.terminalId);

      await session.reconnect();

      expect(gateway.resumeCursors, [null, 7, null]);
      expect(gateway.openRequests, hasLength(2));
      expect(gateway.frameHandles.last.terminalId, 'term_demo_mobile_2');

      await session.writeBytes([0x7a]);
      expect(gateway.sentFrameHandles.last.terminalId, 'term_demo_mobile_2');
      expect(gateway.sentFrames.last.toJson(), {
        'type': 'input',
        'seq': 1,
        'bytes_b64': base64Encode([0x7a]),
      });
    },
  );

  test(
    'gateway terminal renews handle after websocket handshake closes',
    () async {
      final gateway = _FakeGatewayTransport();
      final session = await GatewayTerminalTransport(transport: gateway).open(
        TerminalOpenRequest.gateway(
          target: CcbTerminalTarget.agent(
            projectId: 'proj-demo',
            namespaceEpoch: 4,
            agent: 'mobile',
            scopes: {CcbScope.view, CcbScope.terminalInput},
          ),
        ),
      );

      gateway.emit(
        GatewayTerminalFrame.output(sequence: 3, bytes: utf8.encode('before')),
      );
      await pumpEventQueue();
      gateway.handshakeClosedTerminalIds.add(
        gateway.frameHandles.last.terminalId,
      );

      await session.reconnect();

      expect(gateway.resumeCursors, [null, 3, null]);
      expect(gateway.openRequests, hasLength(2));
      await session.writeBytes([0x6b]);
      expect(gateway.sentFrameHandles.last.terminalId, 'term_demo_mobile_2');
    },
  );
}

class _FakeGatewayTransport implements GatewayTransport {
  _FakeGatewayTransport();

  @override
  Future<GatewayFileUploadResult> uploadFile({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required List<int> bytes,
  }) => throw UnimplementedError();

  @override
  Future<List<int>> downloadFile({
    required String projectId,
    required String agentName,
    required String fileId,
  }) => throw UnimplementedError();

  final openRequests = <GatewayTerminalOpenRequest>[];
  final sentFrames = <GatewayTerminalFrame>[];
  final sentFrameHandles = <GatewayTerminalHandle>[];
  final resumeCursors = <int?>[];
  final rejectedResumeCursors = <int>[];
  final invalidTerminalIds = <String>{};
  final handshakeClosedTerminalIds = <String>{};
  final _frameControllers = <StreamController<GatewayTerminalFrame>>[];
  final _frameHandles = <GatewayTerminalHandle>[];
  final _lastOutputByTerminalId = <String, int>{};

  @override
  final GatewayHostProfile profile = GatewayHostProfile(
    hostId: 'proj-demo',
    deviceId: 'dev-demo',
    routeProvider: RouteProvider(
      kind: RouteProviderKind.lan,
      gatewayUrl: Uri.parse('http://127.0.0.1:8787'),
    ),
    scopes: {'view', 'focus', 'terminal_input'},
  );

  void emit(GatewayTerminalFrame frame) {
    final handle = _frameHandles.last;
    if (frame.type == GatewayTerminalFrameType.output) {
      _lastOutputByTerminalId[handle.terminalId] = _jsonInt(
        frame.payload['seq'],
      );
    }
    _frameControllers.last.add(frame);
  }

  List<GatewayTerminalHandle> get frameHandles =>
      List.unmodifiable(_frameHandles);

  Future<void> closeCurrentStream() {
    return _frameControllers.last.close();
  }

  @override
  Future<CcbProjectView> focusAgent({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
  }) {
    throw UnimplementedError();
  }

  @override
  Future<CcbProjectView> focusWindow({
    required String projectId,
    required String window,
    required int namespaceEpoch,
  }) {
    throw UnimplementedError();
  }

  @override
  Future<CcbProjectView> getProjectView(String projectId) {
    throw UnimplementedError();
  }

  @override
  Future<ReadableTerminalHistory?> getReadableTerminalHistory({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int maxLines = 200,
  }) {
    throw UnimplementedError();
  }

  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  }) {
    throw UnimplementedError();
  }

  @override
  Future<CcbAgentMessageSubmitResult> submitAgentMessage(
    CcbAgentMessageSubmitRequest request,
  ) {
    throw UnimplementedError();
  }

  @override
  Future<CcbProjectLifecycleResult> requestLifecycle({
    required String projectId,
    required CcbLifecycleAction action,
  }) {
    throw UnimplementedError();
  }

  @override
  Future<GatewayHealth> health() {
    throw UnimplementedError();
  }

  @override
  Future<GatewayDevice> device() {
    throw UnimplementedError();
  }

  @override
  Future<List<CcbProject>> listProjects() {
    throw UnimplementedError();
  }

  @override
  Future<GatewayTerminalHandle> openTerminal(
    GatewayTerminalOpenRequest request,
  ) async {
    final sequence = openRequests.length + 1;
    openRequests.add(request);
    return GatewayTerminalHandle(
      terminalId: 'term_demo_mobile_$sequence',
      terminalToken: 'terminal-secret-$sequence',
      expiresAt: DateTime.utc(2026, 6, 18, 0, 5),
      websocketUrl: Uri.parse(
        'ws://127.0.0.1:8787/v1/terminals/term_demo_mobile_$sequence',
      ),
      targetEpoch: request.target.namespaceEpoch,
      targetSummary: GatewayTerminalTargetSummary(
        projectId: request.target.projectId,
        agent: request.target.agent,
        window: request.target.window,
      ),
    );
  }

  @override
  Future<void> sendTerminalFrame(
    GatewayTerminalHandle handle,
    GatewayTerminalFrame frame,
  ) async {
    sentFrameHandles.add(handle);
    sentFrames.add(frame);
  }

  @override
  Stream<GatewayTerminalFrame> terminalFrames(
    GatewayTerminalHandle handle, {
    int? resumeCursor,
  }) {
    resumeCursors.add(resumeCursor);
    final controller = StreamController<GatewayTerminalFrame>.broadcast();
    _frameControllers.add(controller);
    _frameHandles.add(handle);
    final lastOutput = _lastOutputByTerminalId[handle.terminalId] ?? 0;
    if (handshakeClosedTerminalIds.contains(handle.terminalId)) {
      scheduleMicrotask(() {
        if (!controller.isClosed) {
          controller.addError(
            HttpException(
              'Connection closed before full header was received',
              uri: Uri.parse(
                'http://127.0.0.1:8787/v1/terminals/${handle.terminalId}',
              ),
            ),
          );
          controller.close();
        }
      });
    } else if (invalidTerminalIds.contains(handle.terminalId)) {
      scheduleMicrotask(() {
        if (!controller.isClosed) {
          controller.add(GatewayTerminalFrame.error('invalid_token'));
          controller.close();
        }
      });
    } else if (resumeCursor != null && resumeCursor > lastOutput) {
      rejectedResumeCursors.add(resumeCursor);
      scheduleMicrotask(() {
        if (!controller.isClosed) {
          controller.add(GatewayTerminalFrame.error('stale_resume_cursor'));
          controller.close();
        }
      });
    } else {
      scheduleMicrotask(() {
        if (!controller.isClosed) {
          controller.add(
            GatewayTerminalFrame.open(
              terminalId: handle.terminalId,
              token: '',
              lastInputSequence: 0,
            ),
          );
        }
      });
    }
    return controller.stream;
  }
}

Future<void> _waitFor(
  bool Function() predicate, {
  Duration timeout = const Duration(seconds: 2),
}) async {
  final deadline = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(deadline)) {
    if (predicate()) {
      return;
    }
    await Future<void>.delayed(const Duration(milliseconds: 10));
  }
  fail('condition was not met within $timeout');
}

int _jsonInt(Object? value) {
  if (value is int) {
    return value;
  }
  return int.tryParse((value ?? '').toString()) ?? 0;
}
