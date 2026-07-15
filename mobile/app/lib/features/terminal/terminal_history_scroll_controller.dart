import 'package:flutter/widgets.dart';

/// Keeps xterm's implicit input/focus scrolls from overriding history reading.
class TerminalHistoryScrollController extends ScrollController {
  TerminalHistoryScrollController({super.debugLabel});

  static const _latestTolerance = 8.0;

  bool _isReadingHistory = false;

  bool get isReadingHistory =>
      _isReadingHistory &&
      hasClients &&
      position.extentAfter > _latestTolerance;

  bool get isAtLatestOutput =>
      hasClients && position.extentAfter <= _latestTolerance;

  @override
  ScrollPosition createScrollPosition(
    ScrollPhysics physics,
    ScrollContext context,
    ScrollPosition? oldPosition,
  ) {
    return _TerminalHistoryScrollPosition(
      owner: this,
      physics: physics,
      context: context,
      initialPixels: initialScrollOffset,
      keepScrollOffset: keepScrollOffset,
      oldPosition: oldPosition,
      debugLabel: debugLabel,
    );
  }

  void jumpToLatestOutput() {
    if (!hasClients) {
      return;
    }
    _isReadingHistory = false;
    (position as _TerminalHistoryScrollPosition).jumpToLatestOutput();
  }

  void _recordUserPosition(ScrollPosition position) {
    _isReadingHistory = position.extentAfter > _latestTolerance;
  }

  bool _shouldSuppressImplicitLatestJump(
    ScrollPosition position,
    double value,
  ) {
    if (!_isReadingHistory) {
      return false;
    }
    if (position.extentAfter <= _latestTolerance) {
      _isReadingHistory = false;
      return false;
    }
    return value >= position.maxScrollExtent - _latestTolerance;
  }
}

class _TerminalHistoryScrollPosition extends ScrollPositionWithSingleContext {
  _TerminalHistoryScrollPosition({
    required this.owner,
    required super.physics,
    required super.context,
    super.initialPixels,
    super.keepScrollOffset,
    super.oldPosition,
    super.debugLabel,
  });

  final TerminalHistoryScrollController owner;

  @override
  void applyUserOffset(double delta) {
    super.applyUserOffset(delta);
    owner._recordUserPosition(this);
  }

  @override
  void jumpTo(double value) {
    if (owner._shouldSuppressImplicitLatestJump(this, value)) {
      return;
    }
    super.jumpTo(value);
  }

  void jumpToLatestOutput() {
    super.jumpTo(maxScrollExtent);
  }
}
