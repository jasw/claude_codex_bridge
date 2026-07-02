import 'dart:async';

import 'package:flutter/material.dart';

import '../../models/ccb_conversation_item.dart';
import 'conversation_timeline.dart';

typedef AgentChatAgentIsActive = bool Function(String agentName);

const initialTimelineScrollOffset = 1000000000.0;
const agentChatFollowLatestScrollDuration = Duration(milliseconds: 180);

class AgentChatUiControllerStore {
  final Map<String, TextEditingController> _draftControllers = {};
  final Map<String, FocusNode> _draftFocusNodes = {};
  final Map<String, List<CcbMessageAttachment>> _draftAttachments = {};
  final Map<String, ScrollController> _scrollControllers = {};
  final Map<String, int> _timelineAutoFollowGenerations = {};

  TextEditingController draftController(String agentName) {
    return _draftControllers.putIfAbsent(agentName, TextEditingController.new);
  }

  FocusNode draftFocusNode(String agentName) {
    return _draftFocusNodes.putIfAbsent(agentName, FocusNode.new);
  }

  List<CcbMessageAttachment> draftAttachments(String agentName) {
    return _draftAttachments[agentName] ?? const [];
  }

  void addDraftAttachments(
    String agentName,
    List<CcbMessageAttachment> attachments,
  ) {
    if (attachments.isEmpty) {
      return;
    }
    final current = _draftAttachments[agentName] ?? [];
    _draftAttachments[agentName] = [...current, ...attachments];
  }

  void removeDraftAttachment(String agentName, String localId) {
    final current = _draftAttachments[agentName] ?? [];
    _draftAttachments[agentName] =
        current.where((item) => item.fileId != localId).toList();
  }

  void clearDraftAttachments(String agentName) {
    _draftAttachments.remove(agentName);
  }

  ScrollController timelineScrollController(String agentName) {
    return _scrollControllers.putIfAbsent(
      agentName,
      () => ScrollController(initialScrollOffset: initialTimelineScrollOffset),
    );
  }

  bool isTimelineNearEnd(String agentName) {
    final controller = _scrollControllers[agentName];
    if (controller == null || !controller.hasClients) {
      return true;
    }
    return isScrollMetricsNearEnd(controller.position);
  }

  void cancelTimelineAutoFollow(String agentName) {
    _timelineAutoFollowGenerations[agentName] =
        (_timelineAutoFollowGenerations[agentName] ?? 0) + 1;
  }

  void scrollTimelineToEnd(
    String agentName, {
    required AgentChatAgentIsActive isActive,
    String? targetItemId,
    int attempt = 0,
    int? generation,
  }) {
    generation ??= _timelineAutoFollowGenerations[agentName] ?? 0;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!isActive(agentName) ||
          generation != (_timelineAutoFollowGenerations[agentName] ?? 0)) {
        return;
      }
      final controller = _scrollControllers[agentName];
      if (controller == null || !controller.hasClients) {
        if (attempt < 5) {
          scrollTimelineToEnd(
            agentName,
            isActive: isActive,
            targetItemId: targetItemId,
            attempt: attempt + 1,
            generation: generation,
          );
        }
        return;
      }
      final target = controller.position.maxScrollExtent;
      final current = controller.position.pixels;
      if ((target - current).abs() > 1) {
        unawaited(
          controller.animateTo(
            target,
            duration: agentChatFollowLatestScrollDuration,
            curve: Curves.easeOutCubic,
          ),
        );
      }
      if (attempt < 3) {
        scrollTimelineToEnd(
          agentName,
          isActive: isActive,
          targetItemId: targetItemId,
          attempt: attempt + 1,
          generation: generation,
        );
      }
    });
  }

  void dispose() {
    for (final controller in _draftControllers.values) {
      controller.dispose();
    }
    for (final node in _draftFocusNodes.values) {
      node.dispose();
    }
    for (final controller in _scrollControllers.values) {
      controller.dispose();
    }
  }
}
