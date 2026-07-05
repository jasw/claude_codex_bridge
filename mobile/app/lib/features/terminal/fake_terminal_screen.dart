import 'dart:async';

import 'package:flutter/material.dart';

import '../../models/ccb_project_view.dart';
import '../../models/ccb_terminal_target.dart';
import '../../repository/mobile_ccb_repository.dart';
import '../../transport/terminal_transport.dart';
import 'agent_terminal_pane.dart';

class FakeTerminalScreen extends StatefulWidget {
  const FakeTerminalScreen({
    required this.repository,
    required this.projectId,
    this.agentName,
    this.windowName,
    this.terminalTransport,
    this.gatewayTerminal = false,
    super.key,
  }) : assert(
         (agentName == null) != (windowName == null),
         'Provide exactly one terminal target identity.',
       );

  final MobileCcbRepository repository;
  final String projectId;
  final String? agentName;
  final String? windowName;
  final TerminalTransport? terminalTransport;
  final bool gatewayTerminal;

  @override
  State<FakeTerminalScreen> createState() => _FakeTerminalScreenState();
}

class _FakeTerminalScreenState extends State<FakeTerminalScreen> {
  late final Future<_FakeTerminalModel> _modelFuture;

  @override
  void initState() {
    super.initState();
    _modelFuture = _loadModel();
  }

  Future<_FakeTerminalModel> _loadModel() async {
    final view = await widget.repository.getProjectView(widget.projectId);
    final target =
        widget.agentName != null
            ? view.terminalTargetForAgent(widget.agentName!)
            : view.terminalTargetForWindow(widget.windowName!);
    return _FakeTerminalModel(view: view, target: target);
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<_FakeTerminalModel>(
      future: _modelFuture,
      builder: (context, snapshot) {
        final model = snapshot.data;
        return Scaffold(
          appBar: AppBar(
            title: Text(model?.title ?? widget.agentName ?? widget.windowName!),
          ),
          body:
              model == null
                  ? const Center(child: CircularProgressIndicator())
                  : AgentTerminalPane(
                    view: model.view,
                    target: model.target,
                    terminalTransport: widget.terminalTransport,
                    gatewayTerminal: widget.gatewayTerminal,
                  ),
        );
      },
    );
  }
}

class _FakeTerminalModel {
  const _FakeTerminalModel({required this.view, required this.target});

  final CcbProjectView view;
  final CcbTerminalTarget target;

  String get title {
    return '${view.project.displayName} / ${target.agent ?? target.window ?? 'terminal'}';
  }
}
