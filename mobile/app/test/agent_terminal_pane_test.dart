import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ccb_mobile/ccb_mobile.dart';

void main() {
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
}
