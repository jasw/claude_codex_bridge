import '../../models/ccb_agent.dart';
import '../../models/ccb_content_item.dart';
import '../../models/ccb_conversation_item.dart';
import '../../models/ccb_project_view.dart';
import '../../models/readable_terminal_history.dart';
import 'agent_chat_controller.dart';
import 'agent_chat_timeline_items.dart';
import 'agent_execution_status.dart';

export 'agent_execution_status.dart';

class SelectedAgentWorkspaceModel {
  const SelectedAgentWorkspaceModel({
    required this.agent,
    required this.contentItems,
    required this.initialHistory,
    required this.timelineItems,
    required this.commsItems,
    required this.isLoadingConversation,
    required this.hasOlderConversation,
    required this.expandedItemIds,
    required this.hasNewMessages,
    required this.isSending,
    required this.isAwaitingAgentResponse,
    required this.isComposerCollapsed,
    required this.executionStatus,
    this.workingReplyItemId,
  });

  final CcbAgent agent;
  final List<CcbContentItem> contentItems;
  final ReadableTerminalHistory? initialHistory;
  final List<CcbConversationItem> timelineItems;
  final List<CcbConversationItem> commsItems;
  final bool isLoadingConversation;
  final bool hasOlderConversation;
  final Set<String> expandedItemIds;
  final bool hasNewMessages;
  final bool isSending;
  final bool isAwaitingAgentResponse;
  final bool isComposerCollapsed;
  final AgentExecutionStatus? executionStatus;
  final String? workingReplyItemId;
}

SelectedAgentWorkspaceModel selectedAgentWorkspaceModel({
  required CcbProjectView view,
  required CcbAgent agent,
  required AgentChatController chatController,
  required bool isAwaitingAgentResponse,
  bool hasLocalExecutionException = false,
}) {
  final contentItems = view.contentForAgent(agent.name);
  final refreshedTerminalHistory = chatController.refreshedTerminalHistoryFor(
    agent.name,
  );
  final terminalHistory =
      refreshedTerminalHistory ?? view.terminalHistoryForAgent(agent.name);
  final remoteConversation = chatController.remoteConversationFor(agent.name);
  final isLoadingConversation = chatController.isLoadingConversation(
    agent.name,
  );
  final allTimelineItems = selectedAgentTimelineItems(
    view: view,
    agent: agent,
    contentItems: contentItems,
    terminalHistory: terminalHistory,
    remoteConversation: remoteConversation,
    localMessages: chatController.localMessagesFor(agent.name),
    preferSupplementalTerminalHistoryAtEnd: refreshedTerminalHistory != null,
    isLoadingConversation: isLoadingConversation,
  );
  final timelineItems = [
    for (final item in allTimelineItems)
      if (item.kind != CcbConversationItemKind.commsItem) item,
  ];
  final executionStatus = agentExecutionStatus(
    agent: agent,
    isAwaitingAgentResponse: isAwaitingAgentResponse,
    isLoadingConversation: isLoadingConversation,
    hasLocalExecutionException: hasLocalExecutionException,
  );
  final workingReplyItemId =
      executionStatus.state == 'working'
          ? selectedAgentWorkingReplyItemId(timelineItems)
          : null;
  final visibleTimelineItems =
      workingReplyItemId == null && executionStatus.state == 'working'
          ? [
            ...timelineItems,
            syntheticAgentWorkingConversationItem(agent.name),
          ]
          : timelineItems;
  final visibleWorkingReplyItemId =
      workingReplyItemId ??
      (executionStatus.state == 'working'
          ? syntheticAgentWorkingConversationItemId(agent.name)
          : null);
  return SelectedAgentWorkspaceModel(
    agent: agent,
    contentItems: contentItems,
    initialHistory: terminalHistory,
    timelineItems: visibleTimelineItems,
    commsItems: [
      for (final item in allTimelineItems)
        if (item.kind == CcbConversationItemKind.commsItem) item,
    ],
    isLoadingConversation: isLoadingConversation,
    hasOlderConversation: chatController.hasOlderConversation(agent.name),
    expandedItemIds: chatController.expandedItemIds(agent.name),
    hasNewMessages: chatController.hasNewMessages(agent.name),
    isSending: chatController.isSubmitting(agent.name),
    isAwaitingAgentResponse: isAwaitingAgentResponse,
    isComposerCollapsed: chatController.isComposerCollapsed(agent.name),
    executionStatus: executionStatus,
    workingReplyItemId: visibleWorkingReplyItemId,
  );
}

String syntheticAgentWorkingConversationItemId(String agentName) =>
    'synthetic-working-reply-$agentName';

CcbConversationItem syntheticAgentWorkingConversationItem(String agentName) {
  return CcbConversationItem(
    id: syntheticAgentWorkingConversationItemId(agentName),
    agentName: agentName,
    kind: CcbConversationItemKind.agentReply,
    title: 'Agent reply',
    body: 'Working...',
    source: 'project_view',
  );
}

String? selectedAgentWorkingReplyItemId(List<CcbConversationItem> items) {
  CcbConversationItem? latestUser;
  CcbConversationItem? latestReply;
  for (final item in items) {
    if (item.kind == CcbConversationItemKind.userMessage) {
      latestUser = item;
    } else if (item.kind == CcbConversationItemKind.agentReply) {
      latestReply = item;
    }
  }
  if (latestReply == null) {
    return null;
  }
  if (latestReply.completedAt != null) {
    return null;
  }
  final replyStartedAt = latestReply.startedAt ?? latestReply.sentAt;
  final userSentAt = latestUser?.sentAt;
  if (replyStartedAt == null) {
    if (userSentAt != null) {
      return null;
    }
    return _isTerminalDerivedConversationItem(latestReply)
        ? latestReply.id
        : null;
  }
  if (userSentAt != null && replyStartedAt.isBefore(userSentAt)) {
    return null;
  }
  return latestReply.id;
}

bool _isTerminalDerivedConversationItem(CcbConversationItem item) {
  final source = item.source ?? '';
  return item.kind == CcbConversationItemKind.terminalHistoryBlock ||
      source.startsWith('tmux output /') ||
      source.startsWith('terminal ');
}
