import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart' show ScrollDirection;
import 'package:file_picker/file_picker.dart';
import 'package:mime/mime.dart';
import 'package:path_provider/path_provider.dart';
import 'package:open_filex/open_filex.dart';
import 'package:path/path.dart' as p;

import '../../l10n/ccb_mobile_localizations.dart';
import '../../models/ccb_agent.dart';
import '../../models/ccb_agent_conversation.dart';
import '../../models/ccb_conversation_item.dart';
import '../../models/ccb_project_view.dart';
import '../../repository/mobile_ccb_repository.dart';
import '../../transport/terminal_transport.dart';
import 'agent_chat_controller.dart';
import 'agent_chat_ui_controller_store.dart';
import 'agent_conversation_refresh_coordinator.dart';
import 'agent_local_message_store.dart';
import 'agent_message_submit_coordinator.dart';
import 'agent_pane_event_coordinator.dart';
import 'agent_pane_message_submitter.dart';
import 'conversation_timeline.dart';
import 'conversation_refresh_scheduler.dart';
import 'pane_chat_controller.dart';
import 'selected_agent_workspace_model.dart';
import 'selected_agent_workspace_view.dart';

const agentMessageMaxAttachments = 5;
const agentMessageMaxAttachmentBytes = 25 * 1024 * 1024;
const selectedAgentTabKeyBytes = [9];
const selectedAgentEscapeKeyBytes = [27];
const selectedAgentExpandScrollDuration = Duration(milliseconds: 220);

class SelectedAgentWorkspace extends StatefulWidget {
  const SelectedAgentWorkspace({
    required this.repository,
    required this.terminalTransport,
    required this.usePaneInputForMessages,
    required this.view,
    required this.agent,
    required this.enableComposerCollapse,
    required this.onRefreshView,
    this.onUserScrollDirectionChanged,
    this.localMessageStore,
    this.controller,
  });

  final MobileCcbRepository repository;
  final TerminalTransport? terminalTransport;
  final bool usePaneInputForMessages;
  final CcbProjectView view;
  final CcbAgent? agent;
  final bool enableComposerCollapse;
  final Future<CcbProjectView?> Function()? onRefreshView;
  final ValueChanged<ScrollDirection>? onUserScrollDirectionChanged;
  final AgentLocalMessageStore? localMessageStore;
  final SelectedAgentWorkspaceController? controller;

  @override
  State<SelectedAgentWorkspace> createState() => _SelectedAgentWorkspaceState();
}

class SelectedAgentWorkspaceController {
  VoidCallback? _refreshLatest;

  void refreshLatest() {
    _refreshLatest?.call();
  }

  void _attachRefreshLatest(VoidCallback refreshLatest) {
    _refreshLatest = refreshLatest;
  }

  void _detachRefreshLatest() {
    _refreshLatest = null;
  }

  void dispose() {
    _refreshLatest = null;
  }
}

class _SelectedAgentWorkspaceState extends State<SelectedAgentWorkspace>
    with WidgetsBindingObserver {
  final AgentChatController _chatController = AgentChatController();
  final AgentChatUiControllerStore _uiControllers =
      AgentChatUiControllerStore();
  late final AgentLocalMessageStore _localMessageStore =
      widget.localMessageStore ?? AgentLocalMessageStore();
  late final AgentConversationRefreshCoordinator
  _conversationRefreshCoordinator = AgentConversationRefreshCoordinator(
    chatController: _chatController,
    isMounted: () => mounted,
    mutateState: _mutateChatState,
    isTimelineNearEnd: _isTimelineNearEnd,
    scrollTimelineToEnd: _scrollTimelineToEnd,
  );
  late final AgentPaneEventCoordinator _paneEventCoordinator =
      AgentPaneEventCoordinator(
        chatController: _chatController,
        isMounted: () => mounted,
        mutateState: _mutateChatState,
        isTimelineNearEnd: _isTimelineNearEnd,
        scrollTimelineToEnd: _scrollTimelineToEnd,
      );
  late final AgentPaneMessageSubmitter _paneMessageSubmitter =
      AgentPaneMessageSubmitter(onEvent: _handlePaneChatEvent);
  late final AgentMessageSubmitCoordinator _messageSubmitCoordinator =
      AgentMessageSubmitCoordinator(
        chatController: _chatController,
        isMounted: () => mounted,
        mutateState: _mutateChatState,
        isTimelineNearEnd: _isTimelineNearEnd,
        scrollTimelineToEnd: _scrollTimelineToEnd,
        loadConversation: _loadConversation,
        scheduleConversationRefresh: _scheduleConversationRefresh,
        paneSubmitter: _paneMessageSubmitter,
      );
  late final ConversationRefreshScheduler _conversationRefreshScheduler =
      ConversationRefreshScheduler(
        onRefresh: _refreshScheduledConversation,
        isActive: (agentName) => mounted && widget.agent?.name == agentName,
        onStateChanged: _handleRefreshScheduleChanged,
      );
  final Set<String> _downloadingAttachmentIds = {};
  final Map<String, String> _downloadedAttachmentPaths = {};
  final Map<String, double> _preExpansionTimelineOffsets = {};
  final Set<String> _awaitingPaneResponseAgentNames = {};
  final Map<String, _AwaitingReplyBaseline> _awaitingReplyBaselines = {};
  final Set<String> _sourceWorkingAgentNames = {};
  final Set<String> _localExceptionStatusAgentNames = {};
  final Map<String, String> _recentPaneOutputText = {};
  final Set<String> _pendingClearNewMessageAgents = {};
  FocusNode? _observedDraftFocusNode;
  String? _observedDraftFocusAgentName;
  var _nextDraftAttachmentIndex = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    widget.controller?._attachRefreshLatest(_refreshSelectedAgentLatest);
    _restoreLocalMessagesForSelectedAgent();
    _loadSelectedAgentConversation();
  }

  @override
  void didUpdateWidget(covariant SelectedAgentWorkspace oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller) {
      oldWidget.controller?._detachRefreshLatest();
      widget.controller?._attachRefreshLatest(_refreshSelectedAgentLatest);
    }
    final projectOrAgentChanged =
        oldWidget.view.project.id != widget.view.project.id ||
        oldWidget.agent?.name != widget.agent?.name;
    if (oldWidget.repository != widget.repository ||
        oldWidget.terminalTransport != widget.terminalTransport ||
        oldWidget.view.project.id != widget.view.project.id ||
        oldWidget.view.namespaceEpoch != widget.view.namespaceEpoch ||
        oldWidget.agent?.name != widget.agent?.name) {
      if (oldWidget.terminalTransport != widget.terminalTransport ||
          oldWidget.view.project.id != widget.view.project.id ||
          oldWidget.view.namespaceEpoch != widget.view.namespaceEpoch) {
        unawaited(_paneMessageSubmitter.closeSessions());
        _chatController.clearRefreshedTerminalHistories();
      }
      if (projectOrAgentChanged) {
        _restoreLocalMessagesForSelectedAgent();
      }
      _loadSelectedAgentConversation();
    } else if (_selectedAgentActivitySignature(oldWidget.agent) !=
        _selectedAgentActivitySignature(widget.agent)) {
      final agent = widget.agent;
      if (agent != null) {
        _syncLocalExecutionStateFromView(
          view: widget.view,
          agentName: agent.name,
        );
        unawaited(
          _refreshSelectedAgentConversation(agent, viewOverride: widget.view),
        );
      }
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _observedDraftFocusNode?.removeListener(_handleDraftFocusChanged);
    widget.controller?._detachRefreshLatest();
    unawaited(_paneMessageSubmitter.closeSessions());
    _uiControllers.dispose();
    _conversationRefreshScheduler.cancelAll(notify: false);
    super.dispose();
  }

  @override
  void didChangeMetrics() {
    super.didChangeMetrics();
    final agentName = widget.agent?.name;
    if (agentName != null) {
      _keepLatestVisibleAfterComposerChange(agentName);
    }
  }

  TextEditingController _draftController(String agentName) {
    return _uiControllers.draftController(agentName);
  }

  FocusNode _draftFocusNode(String agentName) {
    return _uiControllers.draftFocusNode(agentName);
  }

  void _observeDraftFocusNode(String agentName, FocusNode node) {
    if (_observedDraftFocusNode == node &&
        _observedDraftFocusAgentName == agentName) {
      return;
    }
    _observedDraftFocusNode?.removeListener(_handleDraftFocusChanged);
    _observedDraftFocusNode = node;
    _observedDraftFocusAgentName = agentName;
    node.addListener(_handleDraftFocusChanged);
  }

  void _handleDraftFocusChanged() {
    final agentName = _observedDraftFocusAgentName;
    if (agentName == null || !mounted || widget.agent?.name != agentName) {
      return;
    }
    _keepLatestVisibleAfterComposerChange(agentName);
  }

  List<CcbMessageAttachment> _draftAttachments(String agentName) {
    return _uiControllers.draftAttachments(agentName);
  }

  void _mutateChatState(void Function() update) {
    if (!mounted) {
      return;
    }
    setState(update);
    final agentName = widget.agent?.name;
    if (agentName != null) {
      unawaited(_persistLocalMessages(agentName));
    }
  }

  Future<void> _restoreLocalMessagesForSelectedAgent() async {
    final agentName = widget.agent?.name;
    if (agentName == null) {
      return;
    }
    final projectId = widget.view.project.id;
    final List<CcbConversationItem> messages;
    try {
      messages = await _localMessageStore.load(
        projectId: projectId,
        agentName: agentName,
      );
    } catch (_) {
      return;
    }
    if (!mounted ||
        widget.view.project.id != projectId ||
        widget.agent?.name != agentName) {
      return;
    }
    setState(() {
      _chatController.restoreLocalMessages(agentName, messages);
    });
  }

  Future<void> _persistLocalMessages(String agentName) async {
    try {
      await _localMessageStore.save(
        projectId: widget.view.project.id,
        agentName: agentName,
        messages: _chatController.localMessagesFor(agentName),
      );
    } catch (_) {
      // Local retry state is best-effort; chat flow should continue.
    }
  }

  void _addAttachments(
    String agentName,
    List<CcbMessageAttachment> attachments,
  ) {
    setState(() {
      _uiControllers.addDraftAttachments(agentName, attachments);
    });
  }

  void _removeAttachment(String agentName, String localId) {
    setState(() {
      _uiControllers.removeDraftAttachment(agentName, localId);
    });
  }

  ScrollController _scrollController(String agentName) {
    return _uiControllers.timelineScrollController(agentName);
  }

  void _toggleExpandedItem(String agentName, String itemId) {
    final shouldReveal =
        !_chatController.expandedItemIds(agentName).contains(itemId);
    final restoreOffset =
        shouldReveal
            ? null
            : _preExpansionTimelineOffsets.remove(
              _expandedTimelineOffsetKey(agentName, itemId),
            );
    if (shouldReveal) {
      final controller = _scrollController(agentName);
      if (controller.hasClients) {
        _preExpansionTimelineOffsets[_expandedTimelineOffsetKey(
              agentName,
              itemId,
            )] =
            controller.position.pixels;
      }
    }
    setState(() {
      _chatController.toggleExpandedItem(agentName, itemId);
    });
    if (shouldReveal) {
      _scrollExpandedItemToTop(itemId);
    } else if (restoreOffset != null) {
      _restoreTimelineOffset(agentName, restoreOffset);
    }
  }

  String _expandedTimelineOffsetKey(String agentName, String itemId) {
    return '$agentName:$itemId';
  }

  void _scrollExpandedItemToTop(String itemId) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) {
        return;
      }
      final itemContext = conversationTimelineItemKey(itemId).currentContext;
      if (itemContext == null) {
        return;
      }
      Scrollable.ensureVisible(
        itemContext,
        alignment: 0,
        duration: selectedAgentExpandScrollDuration,
        curve: Curves.easeOutCubic,
      );
    });
  }

  void _restoreTimelineOffset(String agentName, double offset) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || widget.agent?.name != agentName) {
        return;
      }
      final controller = _scrollController(agentName);
      if (!controller.hasClients) {
        return;
      }
      final target = offset.clamp(
        controller.position.minScrollExtent,
        controller.position.maxScrollExtent,
      );
      controller.jumpTo(target.toDouble());
    });
  }

  void _loadSelectedAgentConversation() {
    if (!mounted) {
      return;
    }
    final agent = widget.agent;
    if (agent == null) {
      return;
    }
    unawaited(_refreshSelectedAgentConversation(agent));
  }

  Future<void> _refreshSelectedAgentConversation(
    CcbAgent agent, {
    CcbProjectView? viewOverride,
  }) async {
    if (!mounted || widget.agent?.name != agent.name) {
      return;
    }
    final view = viewOverride ?? widget.view;
    await _loadConversation(agent.name, viewOverride: view);
  }

  void _collapseComposer(String agentName) {
    if (!widget.enableComposerCollapse) {
      return;
    }
    final wasNearLatest = _isTimelineNearEnd(agentName);
    _draftFocusNode(agentName).unfocus();
    setState(() {
      _chatController.collapseComposer(agentName);
    });
    if (wasNearLatest) {
      _scrollTimelineToEnd(agentName);
    }
  }

  void _expandComposer(String agentName) {
    final wasNearLatest = _isTimelineNearEnd(agentName);
    setState(() {
      _chatController.expandComposer(agentName);
    });
    _draftFocusNode(agentName).requestFocus();
    if (wasNearLatest) {
      _scrollTimelineToEnd(agentName);
    }
  }

  Future<void> _loadConversation(
    String agentName, {
    CcbProjectView? viewOverride,
  }) async {
    await _conversationRefreshCoordinator.load(
      repository: widget.repository,
      view: viewOverride ?? widget.view,
      agentName: agentName,
      refreshView: widget.onRefreshView,
    );
  }

  Future<void> _sendMessage(CcbAgent agent) async {
    final controller = _draftController(agent.name);
    final attachments = _draftAttachments(agent.name);
    var acceptedPaneMessage = false;

    await _messageSubmitCoordinator.send(
      agent: agent,
      body: controller.text,
      attachments: attachments,
      view: widget.view,
      repository: widget.repository,
      terminalTransport: widget.terminalTransport,
      usePaneInput: widget.usePaneInputForMessages,
      refreshView: widget.onRefreshView,
      onAccepted: () {
        controller.clear();
        _uiControllers.clearDraftAttachments(agent.name);
        _localExceptionStatusAgentNames.remove(agent.name);
        _recentPaneOutputText.remove(agent.name);
        if (widget.usePaneInputForMessages) {
          acceptedPaneMessage = true;
          _markAwaitingPaneResponse(agent.name);
        }
      },
    );
    if (!acceptedPaneMessage ||
        !mounted ||
        widget.agent?.name != agent.name ||
        _conversationRefreshScheduler.isPending(agent.name)) {
      return;
    }
    setState(() {
      _awaitingPaneResponseAgentNames.remove(agent.name);
    });
  }

  Future<void> _sendPaneKey(
    CcbAgent agent, {
    required List<int> bytes,
    required String label,
  }) async {
    final outcome = await _paneMessageSubmitter.sendKey(
      transport: widget.terminalTransport,
      agent: agent,
      view: widget.view,
      refreshView: widget.onRefreshView,
      bytes: bytes,
    );
    if (!mounted || widget.agent?.name != agent.name) {
      return;
    }
    if (!outcome.sent) {
      _showSnack('Could not send $label: ${outcome.error}');
      return;
    }
    _refreshLatest(agent.name);
    _scheduleConversationRefresh(agent.name);
  }

  Future<void> _sendDraftThenPaneKey(
    CcbAgent agent, {
    required List<int> bytes,
    required String label,
  }) async {
    final controller = _draftController(agent.name);
    final body = controller.text;
    if (body.isEmpty) {
      await _sendPaneKey(agent, bytes: bytes, label: label);
      return;
    }
    final outcome = await _paneMessageSubmitter.sendTextThenKey(
      transport: widget.terminalTransport,
      agent: agent,
      view: widget.view,
      refreshView: widget.onRefreshView,
      body: body,
      bytes: bytes,
    );
    if (!mounted || widget.agent?.name != agent.name) {
      return;
    }
    if (!outcome.sent) {
      _showSnack('Could not send $label: ${outcome.error}');
      return;
    }
    setState(() {
      controller.clear();
      _markAwaitingPaneResponse(agent.name);
    });
    _refreshLatest(agent.name);
    _scheduleConversationRefresh(agent.name);
  }

  Future<void> _pickAttachments({
    required String agentName,
    required FileType type,
  }) async {
    try {
      final result = await FilePicker.pickFiles(
        allowMultiple: true,
        type: type,
        allowedExtensions:
            type == FileType.custom
                ? const ['pdf', 'txt', 'md', 'doc', 'docx']
                : null,
      );
      if (result == null || result.files.isEmpty) {
        return;
      }
      final current = _draftAttachments(agentName);
      final remainingSlots = agentMessageMaxAttachments - current.length;
      if (remainingSlots <= 0) {
        _showSnack('Attach up to $agentMessageMaxAttachments files');
        return;
      }
      final accepted = <CcbMessageAttachment>[];
      for (final file in result.files.take(remainingSlots)) {
        final path = file.path;
        if (path == null || path.isEmpty) {
          continue;
        }
        final size = file.size;
        if (size > agentMessageMaxAttachmentBytes) {
          _showSnack('${file.name} is larger than 25 MB');
          continue;
        }
        final fileName = file.name.isEmpty ? p.basename(path) : file.name;
        final extension = _attachmentExtension(
          pickerExtension: file.extension,
          fileName: fileName,
          path: path,
        );
        final mimeType =
            lookupMimeType(path) ??
            lookupMimeType(fileName) ??
            _mimeTypeForExtension(extension) ??
            'application/octet-stream';
        if (!_isSupportedAttachment(
          type: type,
          extension: extension,
          mimeType: mimeType,
        )) {
          _showSnack('$fileName is not a supported attachment type');
          continue;
        }
        final localId = 'draft-$agentName-${_nextDraftAttachmentIndex++}';
        final storedPath = await _copyDraftAttachmentFile(
          sourcePath: path,
          localId: localId,
          fileName: fileName,
        );
        accepted.add(
          CcbMessageAttachment(
            fileId: localId,
            fileName: fileName,
            mimeType: mimeType,
            sizeBytes: size,
            localPath: storedPath,
            kind:
                mimeType.startsWith('image/')
                    ? CcbMessageAttachmentKind.image
                    : CcbMessageAttachmentKind.document,
            state: CcbMessageAttachmentState.queued,
          ),
        );
      }
      if (accepted.isNotEmpty) {
        _addAttachments(agentName, accepted);
        _focusComposer(agentName);
      }
      if (result.files.length > remainingSlots) {
        _showSnack('Attach up to $agentMessageMaxAttachments files');
      }
    } catch (error) {
      _showSnack('Could not pick attachment: $error');
    }
  }

  Future<String> _copyDraftAttachmentFile({
    required String sourcePath,
    required String localId,
    required String fileName,
  }) async {
    if (!Platform.isAndroid && !Platform.isIOS) {
      return sourcePath;
    }
    try {
      final dir = await getApplicationDocumentsDirectory();
      final draftDir = Directory(p.join(dir.path, 'draft_attachments'));
      await draftDir.create(recursive: true);
      final safeName = _safeFileName(fileName);
      final target = File(
        p.join(draftDir.path, '${_safeFileName(localId)}-$safeName'),
      );
      return (await File(sourcePath).copy(target.path)).path;
    } catch (_) {
      return sourcePath;
    }
  }

  Future<String?> _downloadAttachment(
    CcbAgent agent,
    CcbMessageAttachment attachment, {
    required String projectId,
    required bool openAfterDownload,
  }) async {
    if (!_isCurrentAgentSelection(projectId: projectId, agent: agent)) {
      return null;
    }
    try {
      final localPath = attachment.localPath;
      if (localPath != null && localPath.isNotEmpty) {
        if (openAfterDownload) {
          await _openAttachmentFile(localPath, mimeType: attachment.mimeType);
        }
        return localPath;
      }
      final downloadedPath = _downloadedAttachmentPaths[attachment.fileId];
      if (downloadedPath != null) {
        if (openAfterDownload) {
          await _openAttachmentFile(
            downloadedPath,
            mimeType: attachment.mimeType,
          );
        }
        return downloadedPath;
      }
      if (attachment.sizeBytes > agentMessageMaxAttachmentBytes) {
        _showSnack('${attachment.fileName} is larger than 25 MB');
        return null;
      }
      if (_downloadingAttachmentIds.contains(attachment.fileId)) {
        return null;
      }
      setState(() {
        _downloadingAttachmentIds.add(attachment.fileId);
      });
      final bytes = await widget.repository.downloadFile(
        projectId: projectId,
        agentName: agent.name,
        fileId: attachment.fileId,
      );
      if (bytes.length > agentMessageMaxAttachmentBytes) {
        if (_isCurrentAgentSelection(projectId: projectId, agent: agent)) {
          _showSnack('${attachment.fileName} is larger than 25 MB');
        }
        return null;
      }
      final dir = await getApplicationDocumentsDirectory();
      final file = File(p.join(dir.path, _safeFileName(attachment.fileName)));
      await file.writeAsBytes(bytes);
      if (!_isCurrentAgentSelection(projectId: projectId, agent: agent)) {
        return null;
      }
      setState(() {
        _downloadedAttachmentPaths[attachment.fileId] = file.path;
      });
      _showSnack('Saved ${attachment.fileName}');
      if (openAfterDownload) {
        await _openAttachmentFile(file.path, mimeType: attachment.mimeType);
      }
      return file.path;
    } catch (error) {
      if (_isCurrentAgentSelection(projectId: projectId, agent: agent)) {
        _showSnack('Failed to open file: $error');
      }
      return null;
    } finally {
      if (mounted) {
        setState(() {
          _downloadingAttachmentIds.remove(attachment.fileId);
        });
      }
    }
  }

  Future<void> _confirmAndOpenAttachment(
    CcbAgent agent,
    CcbMessageAttachment attachment,
    String projectId,
  ) async {
    final strings = CcbMobileLocalizations.of(context);
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) {
        return AlertDialog(
          title: Text(strings.openAttachment),
          content: Text(strings.openAttachmentQuestion(attachment.fileName)),
          actions: [
            TextButton(
              key: const ValueKey('open-attachment-cancel-action'),
              onPressed: () => Navigator.of(context).pop(false),
              child: Text(strings.cancel),
            ),
            FilledButton(
              key: const ValueKey('open-attachment-confirm-action'),
              onPressed: () => Navigator.of(context).pop(true),
              child: Text(strings.open),
            ),
          ],
        );
      },
    );
    if (confirmed != true ||
        !_isCurrentAgentSelection(projectId: projectId, agent: agent)) {
      return;
    }
    await _downloadAttachment(
      agent,
      attachment,
      projectId: projectId,
      openAfterDownload: true,
    );
  }

  Future<void> _openAttachmentFile(String path, {String? mimeType}) async {
    await OpenFilex.open(path, type: mimeType);
  }

  bool _isCurrentAgentSelection({
    required String projectId,
    required CcbAgent agent,
  }) {
    return mounted &&
        widget.view.project.id == projectId &&
        widget.agent?.name == agent.name;
  }

  void _showSnack(String message) {
    if (!mounted) {
      return;
    }
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  String _appendRecentPaneOutput({
    required String agentName,
    required String output,
  }) {
    final previous = _recentPaneOutputText[agentName];
    final combined =
        previous == null || previous.isEmpty
            ? output
            : '$previous${_paneOutputJoiner(previous, output)}$output';
    final start = combined.length > 1000 ? combined.length - 1000 : 0;
    final recent = combined.substring(start);
    _recentPaneOutputText[agentName] = recent;
    return recent;
  }

  Future<void> _retryMessage(CcbConversationItem item) async {
    await _messageSubmitCoordinator.retry(
      item: item,
      view: widget.view,
      repository: widget.repository,
      terminalTransport: widget.terminalTransport,
      usePaneInput: widget.usePaneInputForMessages,
      refreshView: widget.onRefreshView,
    );
  }

  void _deleteFailedMessage(CcbConversationItem item) {
    if (item.state != CcbConversationDeliveryState.failed) {
      return;
    }
    _mutateChatState(() {
      _chatController.removeLocalMessage(item.agentName, item.id);
    });
  }

  void _handlePaneChatEvent(PaneChatEvent event) {
    if (event.kind == PaneChatEventKind.output) {
      if (!mounted) {
        return;
      }
      setState(() {
        final recentOutput = _appendRecentPaneOutput(
          agentName: event.agentName,
          output: event.body,
        );
        if (_paneOutputHasTerminalException(recentOutput)) {
          _recentPaneOutputText.remove(event.agentName);
          _clearAwaitingPaneResponse(event.agentName);
          _localExceptionStatusAgentNames.add(event.agentName);
        } else {
          _localExceptionStatusAgentNames.remove(event.agentName);
          _markAwaitingPaneResponse(event.agentName);
        }
      });
      return;
    }
    final wasAwaiting = _awaitingPaneResponseAgentNames.contains(
      event.agentName,
    );
    _clearAwaitingPaneResponse(event.agentName);
    final changed = _paneEventCoordinator.apply(event);
    if (wasAwaiting && mounted && !changed) {
      setState(() {});
    }
  }

  void _scheduleConversationRefresh(String agentName) {
    _localExceptionStatusAgentNames.remove(agentName);
    _recentPaneOutputText.remove(agentName);
    _markAwaitingPaneResponse(agentName);
    _conversationRefreshScheduler.schedule(agentName);
  }

  Future<void> _refreshScheduledConversation(String agentName) async {
    final agent = widget.agent;
    if (agent == null || agent.name != agentName) {
      return;
    }
    final wasNearLatest = _isTimelineNearEnd(agent.name);
    final result = await _refreshExecutionStatusForAgent(agent);
    if (!result.settled || !mounted || widget.agent?.name != agent.name) {
      if (mounted &&
          widget.agent?.name == agent.name &&
          _shouldRefreshConversationWhileAwaiting(agent.name)) {
        await _refreshSelectedAgentConversation(agent);
      }
      if (wasNearLatest && mounted && widget.agent?.name == agent.name) {
        _scrollTimelineToEnd(agent.name);
      }
      return;
    }
    _conversationRefreshScheduler.cancelAll();
    await _refreshLatestForAgent(agent, refreshViewFirst: false);
  }

  bool _shouldRefreshConversationWhileAwaiting(String _) => true;

  void _handleRefreshScheduleChanged() {
    if (!mounted) {
      return;
    }
    final selectedAgentName = widget.agent?.name;
    if (selectedAgentName != null &&
        !_conversationRefreshScheduler.isPending(selectedAgentName)) {
      _clearAwaitingPaneResponse(selectedAgentName);
    }
    setState(() {});
  }

  void _refreshLatest(String agentName) {
    final agent = widget.agent;
    if (agent == null || agent.name != agentName) {
      return;
    }
    unawaited(_refreshLatestForAgent(agent, refreshViewFirst: true));
  }

  Future<void> _refreshLatestForAgent(
    CcbAgent agent, {
    required bool refreshViewFirst,
  }) async {
    var view = widget.view;
    if (refreshViewFirst) {
      final refreshed = await widget.onRefreshView?.call();
      if (!mounted || widget.agent?.name != agent.name) {
        return;
      }
      if (refreshed != null) {
        view = refreshed;
        _syncLocalExecutionStateFromView(
          view: refreshed,
          agentName: agent.name,
        );
      }
    }
    await _refreshSelectedAgentConversation(agent, viewOverride: view);
  }

  Future<_ExecutionSyncResult> _refreshExecutionStatusForAgent(
    CcbAgent agent,
  ) async {
    final refreshed = await widget.onRefreshView?.call();
    if (!mounted || widget.agent?.name != agent.name || refreshed == null) {
      return _ExecutionSyncResult.pending;
    }
    return _syncLocalExecutionStateFromView(
      view: refreshed,
      agentName: agent.name,
    );
  }

  _ExecutionSyncResult _syncLocalExecutionStateFromView({
    required CcbProjectView view,
    required String agentName,
  }) {
    final refreshedAgent = view.agentByName(agentName);
    if (refreshedAgent == null) {
      return _ExecutionSyncResult.pending;
    }
    final status = agentExecutionStatus(
      agent: refreshedAgent,
      isAwaitingAgentResponse: false,
      isLoadingConversation: false,
    );
    if (status.state == 'working') {
      _sourceWorkingAgentNames.add(agentName);
      _localExceptionStatusAgentNames.remove(agentName);
      return _ExecutionSyncResult.pending;
    }
    if (status.state == 'idle') {
      final wasAwaiting = _awaitingPaneResponseAgentNames.contains(agentName);
      final observedWorking = _sourceWorkingAgentNames.contains(agentName);
      final replyProgress = _awaitingReplyProgress(agentName);
      if (wasAwaiting) {
        if (!replyProgress.hasReplyProgress) {
          return _ExecutionSyncResult.pending;
        }
        if (replyProgress.hasRunningReply) {
          return _ExecutionSyncResult.pending;
        }
      }
      _clearAwaitingPaneResponse(agentName);
      _sourceWorkingAgentNames.remove(agentName);
      _localExceptionStatusAgentNames.remove(agentName);
      if (wasAwaiting && observedWorking) {
        _showSnack(
          CcbMobileLocalizations.of(context).agentCompleted(agentName),
        );
        return _ExecutionSyncResult.completed;
      }
      return _ExecutionSyncResult.settledIdle;
    }
    return _ExecutionSyncResult.pending;
  }

  void _markAwaitingPaneResponse(String agentName) {
    _awaitingPaneResponseAgentNames.add(agentName);
    _awaitingReplyBaselines.putIfAbsent(
      agentName,
      () => _awaitingReplyBaselineFor(
        _chatController.remoteConversationFor(agentName),
      ),
    );
  }

  void _clearAwaitingPaneResponse(String agentName) {
    _awaitingPaneResponseAgentNames.remove(agentName);
    _awaitingReplyBaselines.remove(agentName);
  }

  _AwaitingReplyProgress _awaitingReplyProgress(String agentName) {
    final baseline = _awaitingReplyBaselines[agentName];
    if (baseline == null) {
      return const _AwaitingReplyProgress(
        hasReplyProgress: false,
        hasRunningReply: false,
      );
    }
    final current = _awaitingReplySnapshotFor(
      _chatController.remoteConversationFor(agentName),
    );
    final hasReplyProgress =
        current.replyCount > baseline.replyCount ||
        (current.latestReplySignature != null &&
            current.latestReplySignature != baseline.latestReplySignature);
    return _AwaitingReplyProgress(
      hasReplyProgress: hasReplyProgress,
      hasRunningReply: hasReplyProgress && current.latestReplyRunning,
    );
  }

  bool _isTimelineNearEnd(String agentName) {
    return _uiControllers.isTimelineNearEnd(agentName);
  }

  void _keepLatestVisibleAfterComposerChange(String agentName) {
    if (!_isTimelineNearEnd(agentName)) {
      return;
    }
    _scrollTimelineToEnd(agentName);
  }

  void _handleTimelineUserScrollDirection(
    String agentName,
    ScrollDirection direction,
  ) {
    _uiControllers.cancelTimelineAutoFollow(agentName);
    widget.onUserScrollDirectionChanged?.call(direction);
  }

  Future<void> _loadOlderConversation(String agentName) async {
    if (!_chatController.hasOlderConversation(agentName) ||
        _chatController.isLoadingConversation(agentName)) {
      return;
    }
    final controller = _scrollController(agentName);
    final beforeMax =
        controller.hasClients ? controller.position.maxScrollExtent : null;
    final beforePixels =
        controller.hasClients ? controller.position.pixels : null;
    final changed = await _conversationRefreshCoordinator.loadOlder(
      repository: widget.repository,
      view: widget.view,
      agentName: agentName,
      refreshView: widget.onRefreshView,
    );
    if (!changed || beforeMax == null || beforePixels == null) {
      return;
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted ||
          widget.agent?.name != agentName ||
          !controller.hasClients) {
        return;
      }
      final delta = controller.position.maxScrollExtent - beforeMax;
      final target = (beforePixels + delta).clamp(
        controller.position.minScrollExtent,
        controller.position.maxScrollExtent,
      );
      controller.jumpTo(target);
    });
  }

  void _clearNewMessageFlag(String agentName) {
    if (!_chatController.hasNewMessages(agentName)) {
      return;
    }
    if (!_pendingClearNewMessageAgents.add(agentName)) {
      return;
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _pendingClearNewMessageAgents.remove(agentName);
      if (!mounted ||
          widget.agent?.name != agentName ||
          !_chatController.hasNewMessages(agentName)) {
        return;
      }
      setState(() {
        _chatController.clearNewMessageFlag(agentName);
      });
    });
  }

  void _jumpToLatest(String agentName) {
    setState(() {
      _chatController.clearNewMessageFlag(agentName);
    });
    _scrollTimelineToEnd(agentName);
  }

  void _refreshSelectedAgentLatest() {
    final agentName = widget.agent?.name;
    if (agentName == null) {
      return;
    }
    _refreshLatest(agentName);
  }

  void _scrollTimelineToEnd(String agentName, {int attempt = 0}) {
    _clearNewMessageFlag(agentName);
    _uiControllers.scrollTimelineToEnd(
      agentName,
      isActive: (agentName) => mounted && widget.agent?.name == agentName,
      targetItemId: _latestTimelineItemId(agentName),
      attempt: attempt,
    );
  }

  String? _latestTimelineItemId(String agentName) {
    final selectedAgent = widget.agent;
    if (selectedAgent == null || selectedAgent.name != agentName) {
      return null;
    }
    final model = selectedAgentWorkspaceModel(
      view: widget.view,
      agent: selectedAgent,
      chatController: _chatController,
      isAwaitingAgentResponse: _awaitingPaneResponseAgentNames.contains(
        selectedAgent.name,
      ),
      hasLocalExecutionException: _localExceptionStatusAgentNames.contains(
        selectedAgent.name,
      ),
    );
    return model.timelineItems.isEmpty ? null : model.timelineItems.last.id;
  }

  @override
  Widget build(BuildContext context) {
    final selectedAgent = widget.agent;
    if (selectedAgent == null) {
      return const NoSelectedAgentWorkspaceView();
    }
    final model = selectedAgentWorkspaceModel(
      view: widget.view,
      agent: selectedAgent,
      chatController: _chatController,
      isAwaitingAgentResponse: _awaitingPaneResponseAgentNames.contains(
        selectedAgent.name,
      ),
      hasLocalExecutionException: _localExceptionStatusAgentNames.contains(
        selectedAgent.name,
      ),
    );
    final draftFocusNode = _draftFocusNode(selectedAgent.name);
    _observeDraftFocusNode(selectedAgent.name, draftFocusNode);
    return SelectedAgentWorkspaceView(
      repository: widget.repository,
      view: widget.view,
      model: model,
      timelineController: _scrollController(selectedAgent.name),
      draftController: _draftController(selectedAgent.name),
      draftFocusNode: draftFocusNode,
      enableComposerCollapse: widget.enableComposerCollapse,
      draftAttachments: _draftAttachments(selectedAgent.name),
      downloadingAttachmentIds: _downloadingAttachmentIds,
      downloadedAttachmentIds: _downloadedAttachmentPaths.keys.toSet(),
      onPickImageAttachment: () {
        _pickAttachments(agentName: selectedAgent.name, type: FileType.image);
      },
      onPickFileAttachment: () {
        _pickAttachments(agentName: selectedAgent.name, type: FileType.custom);
      },
      onRemoveAttachment: (localId) {
        _removeAttachment(selectedAgent.name, localId);
      },
      onDownloadAttachment: (attachment) {
        final projectId = widget.view.project.id;
        _downloadAttachment(
          selectedAgent,
          attachment,
          projectId: projectId,
          openAfterDownload: false,
        );
      },
      onOpenAttachment: (attachment) {
        final projectId = widget.view.project.id;
        _confirmAndOpenAttachment(selectedAgent, attachment, projectId);
      },
      onRetry: _retryMessage,
      onDeleteFailedMessage: _deleteFailedMessage,
      onToggleExpanded: (itemId) {
        _toggleExpandedItem(selectedAgent.name, itemId);
      },
      onRefreshLatest: () {
        _refreshLatest(selectedAgent.name);
      },
      showInlineRefreshAction: widget.controller == null,
      onNearEnd: () {
        _clearNewMessageFlag(selectedAgent.name);
      },
      onUserNearEnd: () {
        _clearNewMessageFlag(selectedAgent.name);
      },
      onUserScrollDirectionChanged: (direction) {
        _handleTimelineUserScrollDirection(selectedAgent.name, direction);
      },
      onNearStart: () {
        _loadOlderConversation(selectedAgent.name);
      },
      onJumpToLatest: () {
        _jumpToLatest(selectedAgent.name);
      },
      onCollapseComposer: () {
        _collapseComposer(selectedAgent.name);
      },
      onExpandComposer: () {
        _expandComposer(selectedAgent.name);
      },
      onSend: () {
        _sendMessage(selectedAgent);
      },
      onSendTab: () {
        _sendDraftThenPaneKey(
          selectedAgent,
          bytes: selectedAgentTabKeyBytes,
          label: 'Tab',
        );
      },
      onSendEscape: () {
        _sendPaneKey(
          selectedAgent,
          bytes: selectedAgentEscapeKeyBytes,
          label: 'Esc',
        );
      },
    );
  }

  void _focusComposer(String agentName) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || widget.agent?.name != agentName) {
        return;
      }
      _draftFocusNode(agentName).requestFocus();
    });
  }
}

String _selectedAgentActivitySignature(CcbAgent? agent) {
  if (agent == null) {
    return '';
  }
  return [
    agent.name,
    agent.active,
    agent.queueDepth,
    agent.runtimeHealth,
    agent.activityState,
    agent.activitySource,
    agent.activityReason,
    agent.activitySymbol,
    agent.activityColor,
  ].join('|');
}

String? _mimeTypeForExtension(String? extension) {
  return switch (extension?.toLowerCase()) {
    'jpg' || 'jpeg' => 'image/jpeg',
    'png' => 'image/png',
    'gif' => 'image/gif',
    'webp' => 'image/webp',
    'heic' => 'image/heic',
    'heif' => 'image/heif',
    'bmp' => 'image/bmp',
    'pdf' => 'application/pdf',
    'txt' => 'text/plain',
    'md' => 'text/markdown',
    'doc' => 'application/msword',
    'docx' =>
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    _ => null,
  };
}

String? _attachmentExtension({
  required String? pickerExtension,
  required String fileName,
  required String path,
}) {
  if (pickerExtension != null && pickerExtension.isNotEmpty) {
    return pickerExtension.toLowerCase();
  }
  final fileNameExtension = p.extension(fileName);
  if (fileNameExtension.isNotEmpty) {
    return fileNameExtension.substring(1).toLowerCase();
  }
  final pathExtension = p.extension(path);
  if (pathExtension.isNotEmpty) {
    return pathExtension.substring(1).toLowerCase();
  }
  return null;
}

bool _isSupportedAttachment({
  required FileType type,
  required String? extension,
  required String mimeType,
}) {
  if (type == FileType.image) {
    return mimeType.startsWith('image/');
  }
  return _mimeTypeForExtension(extension) != null;
}

bool _paneOutputHasTerminalException(String output) {
  final text = output.trim().toLowerCase();
  if (text.isEmpty || text.contains('esc to interrupt')) {
    return false;
  }
  return text.contains('conversation interrupted') ||
      text.contains('request interrupted') ||
      text.contains('interrupted by user') ||
      text.contains('cancelled') ||
      text.contains('canceled') ||
      text.contains('aborted');
}

String _paneOutputJoiner(String previous, String output) {
  if (previous.isEmpty || output.isEmpty) {
    return '';
  }
  final previousEndsWithWhitespace = RegExp(r'\s$').hasMatch(previous);
  final outputStartsWithWhitespace = RegExp(r'^\s').hasMatch(output);
  return previousEndsWithWhitespace || outputStartsWithWhitespace ? '' : ' ';
}

class _ExecutionSyncResult {
  const _ExecutionSyncResult({
    required this.settled,
    required this.isCompleted,
  });

  static const pending = _ExecutionSyncResult(
    settled: false,
    isCompleted: false,
  );
  static const settledIdle = _ExecutionSyncResult(
    settled: true,
    isCompleted: false,
  );
  static const completed = _ExecutionSyncResult(
    settled: true,
    isCompleted: true,
  );

  final bool settled;
  final bool isCompleted;
}

class _AwaitingReplyBaseline {
  const _AwaitingReplyBaseline({
    required this.replyCount,
    required this.latestReplySignature,
  });

  final int replyCount;
  final String? latestReplySignature;
}

class _AwaitingReplySnapshot {
  const _AwaitingReplySnapshot({
    required this.replyCount,
    required this.latestReplySignature,
    required this.latestReplyRunning,
  });

  final int replyCount;
  final String? latestReplySignature;
  final bool latestReplyRunning;
}

class _AwaitingReplyProgress {
  const _AwaitingReplyProgress({
    required this.hasReplyProgress,
    required this.hasRunningReply,
  });

  final bool hasReplyProgress;
  final bool hasRunningReply;
}

_AwaitingReplyBaseline _awaitingReplyBaselineFor(
  CcbAgentConversation? conversation,
) {
  final snapshot = _awaitingReplySnapshotFor(conversation);
  return _AwaitingReplyBaseline(
    replyCount: snapshot.replyCount,
    latestReplySignature: snapshot.latestReplySignature,
  );
}

_AwaitingReplySnapshot _awaitingReplySnapshotFor(
  CcbAgentConversation? conversation,
) {
  var replyCount = 0;
  CcbConversationItem? latestReply;
  for (final item in conversation?.items ?? const <CcbConversationItem>[]) {
    if (item.kind != CcbConversationItemKind.agentReply) {
      continue;
    }
    replyCount += 1;
    latestReply = item;
  }
  return _AwaitingReplySnapshot(
    replyCount: replyCount,
    latestReplySignature:
        latestReply == null ? null : _awaitingReplySignature(latestReply),
    latestReplyRunning: latestReply != null && latestReply.completedAt == null,
  );
}

String _awaitingReplySignature(CcbConversationItem item) {
  return [
    item.id,
    item.startedAt?.microsecondsSinceEpoch.toString() ?? '',
    item.completedAt?.microsecondsSinceEpoch.toString() ?? '',
    item.body,
    item.attachments.length.toString(),
  ].join('|');
}

String _safeFileName(String fileName) {
  final cleaned = fileName.replaceAll(RegExp(r'[\\/:*?"<>|]+'), '_').trim();
  return cleaned.isEmpty ? 'attachment' : cleaned;
}
