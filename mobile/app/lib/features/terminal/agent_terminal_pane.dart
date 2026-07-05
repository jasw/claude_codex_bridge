import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:xterm/xterm.dart';

import '../../models/ccb_project_view.dart';
import '../../models/ccb_terminal_target.dart';
import '../../tmux/tmux_command_builder.dart';
import '../../transport/gateway_terminal_transport.dart';
import '../../transport/terminal_transport.dart';

class AgentTerminalPane extends StatefulWidget {
  const AgentTerminalPane({
    required this.view,
    required this.target,
    required this.terminalTransport,
    this.gatewayTerminal = false,
    this.showHeader = true,
    super.key,
  });

  final CcbProjectView view;
  final CcbTerminalTarget target;
  final TerminalTransport? terminalTransport;
  final bool gatewayTerminal;
  final bool showHeader;

  @override
  State<AgentTerminalPane> createState() => _AgentTerminalPaneState();
}

class _AgentTerminalPaneState extends State<AgentTerminalPane> {
  @override
  Widget build(BuildContext context) {
    final model = AgentTerminalPaneModel.fromViewAndTarget(
      view: widget.view,
      target: widget.target,
    );
    final transport = widget.terminalTransport;
    if (transport == null) {
      return _FakeTerminalPane(model: model, showHeader: widget.showHeader);
    }
    return _LiveTerminalPane(
      model: model,
      transport: transport,
      gatewayTerminal: widget.gatewayTerminal,
      showHeader: widget.showHeader,
    );
  }
}

class AgentTerminalPaneModel {
  const AgentTerminalPaneModel({
    required this.view,
    required this.target,
    required this.attachCommand,
  });

  final CcbProjectView view;
  final CcbTerminalTarget target;
  final String attachCommand;

  factory AgentTerminalPaneModel.fromViewAndTarget({
    required CcbProjectView view,
    required CcbTerminalTarget target,
  }) {
    final attachCommand =
        target.hasDirectTmuxAttachEvidence
            ? TmuxCommandBuilder.shellCommand(
              TmuxCommandBuilder.forTarget(target).attachSession(),
            )
            : 'gateway terminal stream ${target.projectId}/${target.agent ?? target.window ?? 'terminal'}';
    return AgentTerminalPaneModel(
      view: view,
      target: target,
      attachCommand: attachCommand,
    );
  }

  String get title {
    return '${view.project.displayName} / ${target.agent ?? target.window ?? 'terminal'}';
  }
}

class _FakeTerminalPane extends StatefulWidget {
  const _FakeTerminalPane({required this.model, required this.showHeader});

  final AgentTerminalPaneModel model;
  final bool showHeader;

  @override
  State<_FakeTerminalPane> createState() => _FakeTerminalPaneState();
}

class _FakeTerminalPaneState extends State<_FakeTerminalPane> {
  late final Terminal _terminal;

  @override
  void initState() {
    super.initState();
    _terminal = Terminal(maxLines: 2000);
    _writeTranscript();
  }

  void _writeTranscript() {
    final target = widget.model.target;
    _terminal.write('\x1b[32mCCB Mobile fake terminal\x1b[0m\r\n');
    _terminal.write('project: ${target.projectId}\r\n');
    _terminal.write('agent: ${target.agent ?? ''}\r\n');
    _terminal.write('window: ${target.window ?? ''}\r\n');
    _terminal.write('pane evidence: ${target.paneId ?? ''}\r\n');
    _terminal.write('namespace epoch: ${target.namespaceEpoch}\r\n');
    _terminal.write('\r\n');
    _terminal.write('\$ ${widget.model.attachCommand}\r\n');
    _terminal.write('\r\n');
    _terminal.write('fake transport only; live PTY is not connected yet\r\n');
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        if (widget.showHeader)
          AgentTerminalHeader(
            title: widget.model.title,
            subtitle: widget.model.attachCommand,
            trailing: 'Fake',
          ),
        Expanded(
          child: TerminalView(
            _terminal,
            key: const ValueKey('ccb-terminal-view'),
            autofocus: true,
            readOnly: true,
          ),
        ),
      ],
    );
  }
}

class _LiveTerminalPane extends StatefulWidget {
  const _LiveTerminalPane({
    required this.model,
    required this.transport,
    required this.gatewayTerminal,
    required this.showHeader,
  });

  final AgentTerminalPaneModel model;
  final TerminalTransport transport;
  final bool gatewayTerminal;
  final bool showHeader;

  @override
  State<_LiveTerminalPane> createState() => _LiveTerminalPaneState();
}

class _LiveTerminalPaneState extends State<_LiveTerminalPane>
    with WidgetsBindingObserver {
  late final Terminal _terminal;
  late final Future<TerminalSession> _sessionFuture;
  TerminalSession? _session;
  StreamSubscription<String>? _outputSubscription;
  TerminalGeometry _lastGeometry = const TerminalGeometry(
    columns: 100,
    rows: 30,
    pixelWidth: 960,
    pixelHeight: 640,
  );
  String _controlStatus = 'Connecting';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _terminal = Terminal(
      maxLines: 4000,
      onOutput: (data) {
        _writeTerminalBytes(utf8.encode(data));
      },
      onResize: (width, height, pixelWidth, pixelHeight) {
        final geometry = TerminalGeometry(
          columns: width,
          rows: height,
          pixelWidth: pixelWidth,
          pixelHeight: pixelHeight,
        );
        if (_sameGeometry(_lastGeometry, geometry)) {
          return;
        }
        _lastGeometry = geometry;
        _session?.resize(geometry);
      },
    );
    _sessionFuture = _openSession();
  }

  Future<TerminalSession> _openSession() async {
    final request =
        widget.gatewayTerminal || widget.transport is GatewayTerminalTransport
            ? TerminalOpenRequest.gateway(target: widget.model.target)
            : TerminalOpenRequest(target: widget.model.target);
    final session = await widget.transport.open(request);
    _session = session;
    _setControlStatus('Connected');
    _outputSubscription = session.output
        .map<List<int>>((bytes) => bytes)
        .transform(utf8.decoder)
        .listen(
          _terminal.write,
          onError: (Object error) {
            _terminal.write('\r\n\x1b[31m$error\x1b[0m\r\n');
            _setControlStatus('Stream error');
          },
          onDone: () {
            _setControlStatus('Closed');
          },
        );
    return session;
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _reconnect();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _outputSubscription?.cancel();
    _session?.close().catchError((_) {
      // Best-effort route teardown; the gateway may already have closed.
    });
    super.dispose();
  }

  void _writeTerminalBytes(List<int> bytes) {
    final session = _session;
    if (session == null) {
      return;
    }
    session.writeBytes(bytes).catchError((Object error) {
      // TerminalView can emit control responses while a WebSocket reconnects.
      // Keep those best-effort writes from replacing explicit toolbar status.
    });
  }

  Future<void> _sendKey(List<int> bytes, String status) async {
    final session = _session;
    if (session == null) {
      _setControlStatus('Connecting');
      return;
    }
    try {
      await session.writeBytes(bytes);
      _setControlStatus(status);
    } catch (error) {
      _setControlStatus('Key failed');
      _terminal.write('\r\n\x1b[31m$error\x1b[0m\r\n');
    }
  }

  Future<void> _pasteClipboard() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final text = data?.text ?? '';
    if (text.isEmpty) {
      _setControlStatus('Clipboard empty');
      return;
    }
    final session = _session;
    if (session == null) {
      _setControlStatus('Connecting');
      return;
    }
    try {
      await session.paste(text);
      _setControlStatus('Pasted');
    } catch (error) {
      _setControlStatus('Paste failed');
      _terminal.write('\r\n\x1b[31m$error\x1b[0m\r\n');
    }
  }

  Future<void> _syncSize() async {
    final session = _session;
    if (session == null) {
      _setControlStatus('Connecting');
      return;
    }
    try {
      await session.resize(_lastGeometry);
      _setControlStatus('Size synced');
    } catch (error) {
      _setControlStatus('Resize failed');
      _terminal.write('\r\n\x1b[31m$error\x1b[0m\r\n');
    }
  }

  Future<void> _reconnect() async {
    final session = _session;
    if (session == null) {
      _setControlStatus('Connecting');
      return;
    }
    try {
      _setControlStatus('Reconnecting');
      await session.reconnect();
      _setControlStatus('Reconnected');
    } catch (error) {
      _setControlStatus('Reconnect failed');
      _terminal.write('\r\n\x1b[33m$error\x1b[0m\r\n');
    }
  }

  void _setControlStatus(String status) {
    if (!mounted) {
      return;
    }
    setState(() {
      _controlStatus = status;
    });
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<TerminalSession>(
      future: _sessionFuture,
      builder: (context, snapshot) {
        final connected =
            snapshot.connectionState == ConnectionState.done &&
            snapshot.hasData;
        final status = connected ? _controlStatus : 'Connecting';
        return Column(
          children: [
            if (widget.showHeader)
              AgentTerminalHeader(
                title: widget.model.title,
                subtitle: widget.model.attachCommand,
                trailing: status,
              ),
            TerminalControlToolbar(
              enabled: connected,
              status: status,
              onEscape: () => _sendKey(const [27], 'Esc'),
              onTab: () => _sendKey(const [9], 'Tab'),
              onCtrlC: () => _sendKey(const [3], 'Ctrl-C'),
              onCtrlD: () => _sendKey(const [4], 'Ctrl-D'),
              onCtrlU: () => _sendKey(const [21], 'Ctrl-U'),
              onArrowUp: () => _sendKey(const [27, 91, 65], 'Up'),
              onArrowDown: () => _sendKey(const [27, 91, 66], 'Down'),
              onArrowRight: () => _sendKey(const [27, 91, 67], 'Right'),
              onArrowLeft: () => _sendKey(const [27, 91, 68], 'Left'),
              onPaste: _pasteClipboard,
              onResize: _syncSize,
              onReconnect: _reconnect,
            ),
            Expanded(
              child: TerminalView(
                _terminal,
                key: const ValueKey('ccb-live-terminal-view'),
                autofocus: true,
              ),
            ),
          ],
        );
      },
    );
  }
}

class AgentTerminalHeader extends StatelessWidget {
  const AgentTerminalHeader({
    required this.title,
    required this.subtitle,
    required this.trailing,
    super.key,
  });

  final String title;
  final String subtitle;
  final String trailing;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    return Material(
      color: colorScheme.surfaceContainerHighest,
      child: ListTile(
        dense: true,
        leading: const Icon(Icons.terminal),
        title: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text(subtitle, maxLines: 1, overflow: TextOverflow.ellipsis),
        trailing: Text(
          trailing,
          key: const ValueKey('terminal-connection-status'),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
      ),
    );
  }
}

class TerminalControlToolbar extends StatelessWidget {
  const TerminalControlToolbar({
    required this.enabled,
    required this.status,
    required this.onEscape,
    required this.onTab,
    required this.onCtrlC,
    required this.onCtrlD,
    required this.onCtrlU,
    required this.onArrowUp,
    required this.onArrowDown,
    required this.onArrowRight,
    required this.onArrowLeft,
    required this.onPaste,
    required this.onResize,
    required this.onReconnect,
    super.key,
  });

  final bool enabled;
  final String status;
  final VoidCallback onEscape;
  final VoidCallback onTab;
  final VoidCallback onCtrlC;
  final VoidCallback onCtrlD;
  final VoidCallback onCtrlU;
  final VoidCallback onArrowUp;
  final VoidCallback onArrowDown;
  final VoidCallback onArrowRight;
  final VoidCallback onArrowLeft;
  final VoidCallback onPaste;
  final VoidCallback onResize;
  final VoidCallback onReconnect;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    return Material(
      color: colorScheme.surface,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    status,
                    key: const ValueKey('terminal-control-status'),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.labelMedium?.copyWith(
                      color: colorScheme.onSurfaceVariant,
                    ),
                  ),
                ),
                _ToolbarTextButton(
                  key: const ValueKey('terminal-key-escape'),
                  label: 'Esc',
                  enabled: enabled,
                  onPressed: onEscape,
                ),
                _ToolbarTextButton(
                  key: const ValueKey('terminal-key-tab'),
                  label: 'Tab',
                  enabled: enabled,
                  onPressed: onTab,
                ),
                _ToolbarTextButton(
                  key: const ValueKey('terminal-key-ctrl-c'),
                  label: 'C-c',
                  enabled: enabled,
                  onPressed: onCtrlC,
                ),
                PopupMenuButton<VoidCallback>(
                  key: const ValueKey('terminal-ctrl-menu'),
                  tooltip: 'More terminal keys',
                  enabled: enabled,
                  icon: const Icon(Icons.keyboard_command_key),
                  onSelected: (callback) => callback(),
                  itemBuilder:
                      (context) => [
                        PopupMenuItem<VoidCallback>(
                          key: const ValueKey('terminal-key-ctrl-d'),
                          value: onCtrlD,
                          child: const Text('Ctrl-D'),
                        ),
                        PopupMenuItem<VoidCallback>(
                          key: const ValueKey('terminal-key-ctrl-u'),
                          value: onCtrlU,
                          child: const Text('Ctrl-U'),
                        ),
                      ],
                ),
              ],
            ),
            Row(
              children: [
                IconButton(
                  key: const ValueKey('terminal-key-arrow-left'),
                  tooltip: 'Left',
                  visualDensity: VisualDensity.compact,
                  onPressed: enabled ? onArrowLeft : null,
                  icon: const Icon(Icons.keyboard_arrow_left),
                ),
                Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    IconButton(
                      key: const ValueKey('terminal-key-arrow-up'),
                      tooltip: 'Up',
                      visualDensity: VisualDensity.compact,
                      onPressed: enabled ? onArrowUp : null,
                      icon: const Icon(Icons.keyboard_arrow_up),
                    ),
                    IconButton(
                      key: const ValueKey('terminal-key-arrow-down'),
                      tooltip: 'Down',
                      visualDensity: VisualDensity.compact,
                      onPressed: enabled ? onArrowDown : null,
                      icon: const Icon(Icons.keyboard_arrow_down),
                    ),
                  ],
                ),
                IconButton(
                  key: const ValueKey('terminal-key-arrow-right'),
                  tooltip: 'Right',
                  visualDensity: VisualDensity.compact,
                  onPressed: enabled ? onArrowRight : null,
                  icon: const Icon(Icons.keyboard_arrow_right),
                ),
                const Spacer(),
                IconButton(
                  key: const ValueKey('terminal-paste-button'),
                  tooltip: 'Paste clipboard',
                  visualDensity: VisualDensity.compact,
                  onPressed: enabled ? onPaste : null,
                  icon: const Icon(Icons.content_paste_go),
                ),
                IconButton(
                  key: const ValueKey('terminal-resize-button'),
                  tooltip: 'Sync terminal size',
                  visualDensity: VisualDensity.compact,
                  onPressed: enabled ? onResize : null,
                  icon: const Icon(Icons.fit_screen),
                ),
                IconButton(
                  key: const ValueKey('terminal-reconnect-button'),
                  tooltip: 'Reconnect terminal',
                  visualDensity: VisualDensity.compact,
                  onPressed: enabled ? onReconnect : null,
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _ToolbarTextButton extends StatelessWidget {
  const _ToolbarTextButton({
    required this.label,
    required this.enabled,
    required this.onPressed,
    super.key,
  });

  final String label;
  final bool enabled;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return TextButton(
      onPressed: enabled ? onPressed : null,
      style: TextButton.styleFrom(
        minimumSize: const Size(44, 36),
        padding: const EdgeInsets.symmetric(horizontal: 8),
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
      ),
      child: Text(label),
    );
  }
}

bool _sameGeometry(TerminalGeometry a, TerminalGeometry b) {
  return a.columns == b.columns &&
      a.rows == b.rows &&
      a.pixelWidth == b.pixelWidth &&
      a.pixelHeight == b.pixelHeight;
}
