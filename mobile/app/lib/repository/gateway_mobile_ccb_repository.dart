import 'dart:io';

import '../models/ccb_agent_conversation.dart';
import '../models/ccb_project.dart';
import '../models/ccb_project_lifecycle.dart';
import '../models/ccb_project_view.dart';
import '../models/readable_terminal_history.dart';
import '../transport/gateway_transport.dart';
import 'mobile_ccb_repository.dart';

abstract interface class MobileGatewayProfileHealthProbe {
  Future<GatewayHealth> health();

  Future<GatewayDevice> device();
}

class GatewayMobileCcbRepository
    implements
        MobileCcbRepository,
        MobileCcbRepositoryFileUploader,
        MobileGatewayProfileHealthProbe {
  const GatewayMobileCcbRepository({required GatewayTransport transport})
    : _transport = transport;

  final GatewayTransport _transport;

  @override
  Future<List<CcbProject>> listProjects() {
    return _transport.listProjects();
  }

  @override
  Future<GatewayHealth> health() {
    return _transport.health();
  }

  @override
  Future<GatewayDevice> device() {
    return _transport.device();
  }

  @override
  Future<CcbProjectView> getProjectView(String projectId) {
    return _transport.getProjectView(projectId);
  }

  @override
  Future<CcbProjectView> focusAgent({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
  }) {
    return _transport.focusAgent(
      projectId: projectId,
      agent: agent,
      namespaceEpoch: namespaceEpoch,
    );
  }

  @override
  Future<CcbProjectView> focusWindow({
    required String projectId,
    required String window,
    required int namespaceEpoch,
  }) {
    return _transport.focusWindow(
      projectId: projectId,
      window: window,
      namespaceEpoch: namespaceEpoch,
    );
  }

  @override
  Future<ReadableTerminalHistory?> getReadableTerminalHistory({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int maxLines = 200,
  }) {
    return _transport.getReadableTerminalHistory(
      projectId: projectId,
      agent: agent,
      namespaceEpoch: namespaceEpoch,
      maxLines: maxLines,
    );
  }

  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  }) {
    return _transport.getAgentConversation(
      projectId: projectId,
      agent: agent,
      namespaceEpoch: namespaceEpoch,
      limit: limit,
      cursor: cursor,
    );
  }

  @override
  Future<CcbAgentMessageSubmitResult> submitAgentMessage(
    CcbAgentMessageSubmitRequest request,
  ) {
    return _transport.submitAgentMessage(request);
  }

  @override
  Future<CcbProjectLifecycleResult> requestLifecycle({
    required String projectId,
    required CcbLifecycleAction action,
  }) {
    return _transport.requestLifecycle(projectId: projectId, action: action);
  }

  @override
  Future<GatewayFileUploadResult> uploadFile({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required List<int> bytes,
  }) {
    return _transport.uploadFile(
      projectId: projectId,
      agentName: agentName,
      fileName: fileName,
      mimeType: mimeType,
      bytes: bytes,
    );
  }

  @override
  Future<GatewayFileUploadResult> uploadFileFromPath({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required String path,
  }) {
    final transport = _transport;
    if (transport is GatewayFilePathUploader) {
      return (transport as GatewayFilePathUploader).uploadFileFromPath(
        projectId: projectId,
        agentName: agentName,
        fileName: fileName,
        mimeType: mimeType,
        path: path,
      );
    }
    return File(path).readAsBytes().then(
      (bytes) => uploadFile(
        projectId: projectId,
        agentName: agentName,
        fileName: fileName,
        mimeType: mimeType,
        bytes: bytes,
      ),
    );
  }

  @override
  Future<List<int>> downloadFile({
    required String projectId,
    required String agentName,
    required String fileId,
  }) {
    return _transport.downloadFile(
      projectId: projectId,
      agentName: agentName,
      fileId: fileId,
    );
  }
}
