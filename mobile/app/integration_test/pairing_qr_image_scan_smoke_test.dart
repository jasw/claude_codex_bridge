import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import 'package:ccb_mobile/pairing/gateway_pairing.dart';
import 'package:ccb_mobile/pairing/gateway_pairing_scanner_screen.dart';

const _qrPngBase64 = String.fromEnvironment('CCB_MOBILE_PAIRING_QR_PNG_BASE64');
const _expectedQrText = String.fromEnvironment('CCB_MOBILE_PAIRING_QR_TEXT');

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('ML Kit image scanner decodes generated pairing QR payload', (
    tester,
  ) async {
    expect(
      _qrPngBase64,
      isNotEmpty,
      reason: 'Pass CCB_MOBILE_PAIRING_QR_PNG_BASE64 for this smoke test.',
    );
    expect(
      _expectedQrText,
      isNotEmpty,
      reason: 'Pass CCB_MOBILE_PAIRING_QR_TEXT for this smoke test.',
    );

    final directory = await Directory.systemTemp.createTemp('ccb-pairing-qr-');
    addTearDown(() => directory.delete(recursive: true));
    final qrFile = File('${directory.path}/pairing.png');
    await qrFile.writeAsBytes(base64Decode(_qrPngBase64), flush: true);

    final scanner = MobileScannerController(autoStart: false);
    addTearDown(scanner.dispose);
    final capture = await scanner.analyzeImage(
      qrFile.path,
      formats: const [BarcodeFormat.qrCode],
    );
    final decoded = gatewayPairingQrTextFromCapture(capture);

    expect(decoded, isNotNull);
    expect(jsonDecode(decoded!), jsonDecode(_expectedQrText));

    final payload = GatewayPairingPayload.fromQrText(decoded);
    expect(payload.pairingCode, isNotEmpty);
    expect(payload.gatewayUrl.scheme, isNotEmpty);
    expect(payload.claimEndpoint.path, '/v1/pairing/claim');
    expect(payload.toJson()['scopes'], isA<List<String>>());
  });
}
