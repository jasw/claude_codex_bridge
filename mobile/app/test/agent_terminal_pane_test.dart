import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ccb_mobile/ccb_mobile.dart';

import 'support/project_home_test_fakes.dart';

void main() {
  final binding = TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('terminal shortcuts stay collapsed under a floating plus', (
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
              onEscape: () => calls.add('esc'),
              onTab: () => calls.add('tab'),
              onCtrlC: () => calls.add('ctrl-c'),
              onArrowUp: () => calls.add('up'),
              onArrowDown: () => calls.add('down'),
            ),
          ),
        ),
      ),
    );

    expect(find.byKey(const ValueKey('terminal-shortcut-surface')), findsOne);
    expect(find.byIcon(Icons.add), findsOneWidget);
    expect(find.byKey(const ValueKey('terminal-key-escape')), findsNothing);

    await _expandTerminalShortcuts(tester);

    expect(find.byKey(const ValueKey('terminal-key-escape')), findsOneWidget);
    expect(find.byKey(const ValueKey('terminal-key-tab')), findsOneWidget);
    expect(find.byKey(const ValueKey('terminal-key-ctrl-c')), findsOneWidget);
    expect(find.byKey(const ValueKey('terminal-key-arrow-up')), findsOneWidget);
    expect(
      find.byKey(const ValueKey('terminal-key-arrow-down')),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('terminal-key-arrow-left')), findsNothing);
    expect(
      find.byKey(const ValueKey('terminal-key-arrow-right')),
      findsNothing,
    );
    expect(find.byKey(const ValueKey('terminal-paste-button')), findsNothing);
    expect(find.byKey(const ValueKey('terminal-resize-button')), findsNothing);
    expect(
      find.byKey(const ValueKey('terminal-reconnect-button')),
      findsNothing,
    );
    expect(find.byKey(const ValueKey('terminal-ctrl-menu')), findsNothing);

    await tester.tap(find.byKey(const ValueKey('terminal-key-escape')));
    await tester.tap(find.byKey(const ValueKey('terminal-key-tab')));
    await tester.tap(find.byKey(const ValueKey('terminal-key-ctrl-c')));
    await tester.pump();

    expect(calls, ['esc', 'tab', 'ctrl-c']);

    await tester.tap(find.byKey(const ValueKey('terminal-shortcuts-toggle')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('terminal-key-escape')), findsNothing);
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
            onEscape: () => called = true,
            onTab: () => called = true,
            onCtrlC: () => called = true,
            onArrowUp: () => called = true,
            onArrowDown: () => called = true,
          ),
        ),
      ),
    );
    await _expandTerminalShortcuts(tester);

    final escape = tester.widget<TextButton>(
      find.descendant(
        of: find.byKey(const ValueKey('terminal-key-escape')),
        matching: find.byType(TextButton),
      ),
    );
    final up = tester.widget<IconButton>(
      find.descendant(
        of: find.byKey(const ValueKey('terminal-key-arrow-up')),
        matching: find.byType(IconButton),
      ),
    );

    expect(escape.onPressed, isNull);
    expect(up.onPressed, isNull);
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
    await _expandTerminalShortcuts(tester);
    await tester.tap(find.byKey(const ValueKey('terminal-key-tab')));
    await tester.tap(find.byKey(const ValueKey('terminal-key-escape')));
    await tester.pump();

    expect(session.written, [
      [9],
      [27],
    ]);
  });

  testWidgets('live terminal pane sends alphabetic and Chinese text', (
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
    binding.testTextInput.enterText('Alpha中文123');
    await binding.idle();

    expect(session.written.map(utf8.decode), contains('Alpha中文123'));
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
    await _expandTerminalShortcuts(tester);

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
    final reconnect = tester.widget<TextButton>(
      find.byKey(const ValueKey('terminal-header-reconnect')),
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
    await _expandTerminalShortcuts(tester);

    final session = transport.sessions.single;
    session.addOutputError(
      const TerminalTransportException('terminal stream disconnected'),
    );
    await tester.pump();
    await tester.pump();

    await tester.tap(find.byKey(const ValueKey('terminal-header-reconnect')));
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
    await _expandTerminalShortcuts(tester);

    await transport.sessions.single.endOutput();
    await tester.pump();

    expect(find.text('Reconnecting'), findsWidgets);
    final ctrlC = tester.widget<TextButton>(
      find.descendant(
        of: find.byKey(const ValueKey('terminal-key-ctrl-c')),
        matching: find.byType(TextButton),
      ),
    );
    final reconnect = tester.widget<TextButton>(
      find.byKey(const ValueKey('terminal-header-reconnect')),
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
    await _expandTerminalShortcuts(tester);

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
    expect(ctrlC.onPressed, isNull);
    expect(
      find.byKey(const ValueKey('terminal-header-reconnect')),
      findsNothing,
    );

    await tester.pump(const Duration(seconds: 9));
    expect(session.reconnectCount, 0);
    expect(transport.sessions, hasLength(1));
  });
}

Future<void> _expandTerminalShortcuts(WidgetTester tester) async {
  if (find
      .byKey(const ValueKey('terminal-shortcuts-panel'))
      .evaluate()
      .isNotEmpty) {
    return;
  }
  await tester.tap(find.byKey(const ValueKey('terminal-shortcuts-toggle')));
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 220));
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
