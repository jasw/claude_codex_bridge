import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ccb_mobile/ccb_mobile.dart';

import 'support/project_home_test_fakes.dart';

void main() {
  final binding = TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('terminal toolbar exposes direct pane controls on phone width', (
    tester,
  ) async {
    final calls = <String>[];

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 390,
            child: TerminalControlToolbar(
              enabled: true,
              status: 'Connected',
              onEscape: () => calls.add('esc'),
              onTab: () => calls.add('tab'),
              onCtrlC: () => calls.add('ctrl-c'),
              onCtrlD: () => calls.add('ctrl-d'),
              onCtrlU: () => calls.add('ctrl-u'),
              onArrowUp: () => calls.add('up'),
              onArrowDown: () => calls.add('down'),
              onArrowRight: () => calls.add('right'),
              onArrowLeft: () => calls.add('left'),
              onPaste: () => calls.add('paste'),
              onResize: () => calls.add('resize'),
              onReconnect: () => calls.add('reconnect'),
            ),
          ),
        ),
      ),
    );

    expect(
      find.byKey(const ValueKey('terminal-control-status')),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('terminal-key-escape')), findsOneWidget);
    expect(find.byKey(const ValueKey('terminal-key-tab')), findsOneWidget);
    expect(find.byKey(const ValueKey('terminal-key-ctrl-c')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('terminal-key-escape')));
    await tester.tap(find.byKey(const ValueKey('terminal-key-tab')));
    await tester.tap(find.byKey(const ValueKey('terminal-key-ctrl-c')));
    await tester.tap(find.byKey(const ValueKey('terminal-key-arrow-up')));
    await tester.tap(find.byKey(const ValueKey('terminal-key-arrow-down')));
    await tester.tap(find.byKey(const ValueKey('terminal-key-arrow-left')));
    await tester.tap(find.byKey(const ValueKey('terminal-key-arrow-right')));
    await tester.tap(find.byKey(const ValueKey('terminal-paste-button')));
    await tester.tap(find.byKey(const ValueKey('terminal-resize-button')));
    await tester.tap(find.byKey(const ValueKey('terminal-reconnect-button')));
    await tester.pump();

    expect(calls, [
      'esc',
      'tab',
      'ctrl-c',
      'up',
      'down',
      'left',
      'right',
      'paste',
      'resize',
      'reconnect',
    ]);
  });

  testWidgets('terminal toolbar disables controls while disconnected', (
    tester,
  ) async {
    var called = false;

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: TerminalControlToolbar(
            enabled: false,
            status: 'Connecting',
            onEscape: () => called = true,
            onTab: () => called = true,
            onCtrlC: () => called = true,
            onCtrlD: () => called = true,
            onCtrlU: () => called = true,
            onArrowUp: () => called = true,
            onArrowDown: () => called = true,
            onArrowRight: () => called = true,
            onArrowLeft: () => called = true,
            onPaste: () => called = true,
            onResize: () => called = true,
            onReconnect: () => called = true,
          ),
        ),
      ),
    );

    final escape = tester.widget<TextButton>(
      find.descendant(
        of: find.byKey(const ValueKey('terminal-key-escape')),
        matching: find.byType(TextButton),
      ),
    );
    final paste = tester.widget<IconButton>(
      find.byKey(const ValueKey('terminal-paste-button')),
    );

    expect(escape.onPressed, isNull);
    expect(paste.onPressed, isNull);
    expect(called, isFalse);
  });

  testWidgets('live terminal pane does not echo terminal report replies', (
    tester,
  ) async {
    final transport = RecordingTerminalTransport();
    final view = _view(namespaceEpoch: 4);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AgentTerminalPane(
            view: view,
            target: view.terminalTargetForAgent('mobile'),
            terminalTransport: transport,
            gatewayTerminal: true,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final session = transport.sessions.single;
    session.addOutput('\x1b[>c');
    session.addOutput('\x1b[c');
    session.addOutput('\x1b[5n');
    session.addOutput('\x1b[6n');
    await tester.pump();

    expect(session.written, isEmpty);
  });

  testWidgets('live terminal pane still sends explicit terminal controls', (
    tester,
  ) async {
    final transport = RecordingTerminalTransport();
    final view = _view(namespaceEpoch: 4);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AgentTerminalPane(
            view: view,
            target: view.terminalTargetForAgent('mobile'),
            terminalTransport: transport,
            gatewayTerminal: true,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final session = transport.sessions.single;
    await tester.tap(find.byKey(const ValueKey('terminal-key-tab')));
    await tester.tap(find.byKey(const ValueKey('terminal-key-escape')));
    await tester.pump();

    expect(session.written, [
      [9],
      [27],
    ]);
  });

  testWidgets('live terminal pane still sends typed terminal text', (
    tester,
  ) async {
    final transport = RecordingTerminalTransport();
    final view = _view(namespaceEpoch: 4);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AgentTerminalPane(
            view: view,
            target: view.terminalTargetForAgent('mobile'),
            terminalTransport: transport,
            gatewayTerminal: true,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final session = transport.sessions.single;
    await tester.tap(find.byKey(const ValueKey('ccb-live-terminal-view')));
    await tester.pump(const Duration(seconds: 1));
    binding.testTextInput.enterText('plain input');
    await binding.idle();

    expect(session.written.map(utf8.decode), contains('plain input'));
  });

  testWidgets('live terminal pane reopens when target epoch changes', (
    tester,
  ) async {
    final transport = RecordingTerminalTransport();
    var view = _view(namespaceEpoch: 4);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: StatefulBuilder(
            builder: (context, setState) {
              return Column(
                children: [
                  TextButton(
                    key: const ValueKey('advance-epoch'),
                    onPressed: () {
                      setState(() {
                        view = _view(namespaceEpoch: 5);
                      });
                    },
                    child: const Text('advance'),
                  ),
                  Expanded(
                    child: AgentTerminalPane(
                      view: view,
                      target: view.terminalTargetForAgent('mobile'),
                      terminalTransport: transport,
                      gatewayTerminal: true,
                    ),
                  ),
                ],
              );
            },
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(transport.requests, hasLength(1));
    expect(transport.requests.single.target.namespaceEpoch, 4);
    expect(
      find.byKey(const ValueKey('ccb-live-terminal-view')),
      findsOneWidget,
    );

    await tester.tap(find.byKey(const ValueKey('advance-epoch')));
    await tester.pumpAndSettle();

    expect(transport.requests, hasLength(2));
    expect(transport.requests.last.target.namespaceEpoch, 5);
    expect(
      find.byKey(const ValueKey('ccb-live-terminal-view')),
      findsOneWidget,
    );
  });

  testWidgets('live terminal pane auto reconnects after output stream error', (
    tester,
  ) async {
    final transport = RecordingTerminalTransport();
    final view = _view(namespaceEpoch: 4);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AgentTerminalPane(
            view: view,
            target: view.terminalTargetForAgent('mobile'),
            terminalTransport: transport,
            gatewayTerminal: true,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final session = transport.sessions.single;
    session.addOutput('before');
    await tester.pump();
    expect(session.hasOutputListener, isTrue);

    session.addOutputError(
      const TerminalTransportException('terminal stream disconnected'),
    );
    await tester.pump();
    await tester.pump();

    expect(find.text('Reconnecting'), findsWidgets);
    final ctrlC = tester.widget<TextButton>(
      find.descendant(
        of: find.byKey(const ValueKey('terminal-key-ctrl-c')),
        matching: find.byType(TextButton),
      ),
    );
    final reconnect = tester.widget<IconButton>(
      find.byKey(const ValueKey('terminal-reconnect-button')),
    );
    expect(ctrlC.onPressed, isNull);
    expect(reconnect.onPressed, isNotNull);

    await tester.pump(const Duration(seconds: 1));
    await tester.pump();

    expect(session.reconnectCount, 1);
    expect(find.text('Reconnected'), findsWidgets);

    session.addOutput('after');
    await tester.pump();
    expect(session.hasOutputListener, isTrue);
  });

  testWidgets(
    'live terminal pane keeps retrying transient reconnect failures',
    (tester) async {
      final transport = RecordingTerminalTransport();
      final view = _view(namespaceEpoch: 4);

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: AgentTerminalPane(
              view: view,
              target: view.terminalTargetForAgent('mobile'),
              terminalTransport: transport,
              gatewayTerminal: true,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      final session = transport.sessions.single;
      transport.openErrors.addAll([
        const TerminalTransportException('gateway unreachable'),
        const TerminalTransportException('gateway unreachable'),
      ]);
      await session.endOutput();
      await tester.pump();

      expect(find.text('Reconnecting'), findsWidgets);
      await tester.pump(const Duration(seconds: 1));
      await tester.pump();

      expect(transport.requests, hasLength(2));
      expect(find.text('Reconnecting'), findsWidgets);
      expect(find.text('Failed'), findsNothing);

      await tester.pump(const Duration(seconds: 2));
      await tester.pump();

      expect(transport.requests, hasLength(3));
      expect(find.text('Reconnecting'), findsWidgets);
      expect(find.text('Failed'), findsNothing);

      await tester.pump(const Duration(seconds: 4));
      await tester.pumpAndSettle();

      expect(transport.requests, hasLength(4));
      expect(transport.sessions, hasLength(2));
      expect(find.text('Connected'), findsWidgets);
    },
  );

  testWidgets('live terminal pane can still reconnect manually while pending', (
    tester,
  ) async {
    final transport = RecordingTerminalTransport();
    final view = _view(namespaceEpoch: 4);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AgentTerminalPane(
            view: view,
            target: view.terminalTargetForAgent('mobile'),
            terminalTransport: transport,
            gatewayTerminal: true,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final session = transport.sessions.single;
    session.addOutputError(
      const TerminalTransportException('terminal stream disconnected'),
    );
    await tester.pump();

    await tester.tap(find.byKey(const ValueKey('terminal-reconnect-button')));
    await tester.pump();

    expect(session.reconnectCount, 1);
    expect(find.text('Reconnected'), findsWidgets);

    await tester.pump(const Duration(seconds: 2));
    expect(session.reconnectCount, 1);
  });

  testWidgets('live terminal pane auto reopens after output stream closes', (
    tester,
  ) async {
    final transport = RecordingTerminalTransport();
    final view = _view(namespaceEpoch: 4);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AgentTerminalPane(
            view: view,
            target: view.terminalTargetForAgent('mobile'),
            terminalTransport: transport,
            gatewayTerminal: true,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await transport.sessions.single.endOutput();
    await tester.pump();

    expect(find.text('Reconnecting'), findsWidgets);
    final ctrlC = tester.widget<TextButton>(
      find.descendant(
        of: find.byKey(const ValueKey('terminal-key-ctrl-c')),
        matching: find.byType(TextButton),
      ),
    );
    final reconnect = tester.widget<IconButton>(
      find.byKey(const ValueKey('terminal-reconnect-button')),
    );
    expect(ctrlC.onPressed, isNull);
    expect(reconnect.onPressed, isNotNull);

    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();

    expect(transport.sessions, hasLength(2));
    expect(transport.requests, hasLength(2));
    transport.sessions.last.addOutput('after reopen');
    await tester.pump();
    expect(transport.sessions.last.hasOutputListener, isTrue);
  });

  testWidgets('live terminal pane stops reconnecting on stale target errors', (
    tester,
  ) async {
    final transport = RecordingTerminalTransport();
    final view = _view(namespaceEpoch: 4);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AgentTerminalPane(
            view: view,
            target: view.terminalTargetForAgent('mobile'),
            terminalTransport: transport,
            gatewayTerminal: true,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final session = transport.sessions.single;
    session.addOutputError(
      const TerminalTransportException('stale namespace epoch'),
    );
    await tester.pump();
    await tester.pump();

    expect(find.text('Failed'), findsWidgets);
    final ctrlC = tester.widget<TextButton>(
      find.descendant(
        of: find.byKey(const ValueKey('terminal-key-ctrl-c')),
        matching: find.byType(TextButton),
      ),
    );
    final reconnect = tester.widget<IconButton>(
      find.byKey(const ValueKey('terminal-reconnect-button')),
    );
    expect(ctrlC.onPressed, isNull);
    expect(reconnect.onPressed, isNull);

    await tester.pump(const Duration(seconds: 9));
    expect(session.reconnectCount, 0);
    expect(transport.sessions, hasLength(1));
  });
}

CcbProjectView _view({required int namespaceEpoch}) {
  return CcbProjectView(
    project: const CcbProject(
      id: 'proj-demo',
      displayName: 'demo',
      root: '/srv/ccb/demo',
    ),
    namespaceEpoch: namespaceEpoch,
    tmuxSocketPath: '/tmp/ccb-demo/tmux.sock',
    tmuxSessionName: 'ccb-demo',
    activeWindow: 'main',
    activePaneId: '%2',
    windows: const [
      CcbWindow(
        name: 'main',
        label: 'main',
        kind: 'agents',
        order: 0,
        active: true,
        agents: ['mobile'],
      ),
    ],
    agents: const [
      CcbAgent(
        name: 'mobile',
        provider: 'codex',
        window: 'main',
        order: 0,
        active: true,
        queueDepth: 0,
      ),
    ],
    contentItems: const [],
    notifications: const [],
    terminalHistories: const {},
  );
}
