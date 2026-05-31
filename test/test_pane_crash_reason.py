from __future__ import annotations

import json
from types import SimpleNamespace

from provider_backends.pane_log_support.lifecycle_common import (
    classify_crash_reason,
    persist_crash_log,
)

# Real captured text from a codex pane whose isolated OAuth refresh token was
# revoked after a token rotation (the crash that surfaces to the user as a
# generic "stale" pane).
_REVOKED_CRASH = (
    "• Ran sqlite3 -json ingest/index.db ...\n"
    "■ Your access token could not be refreshed because your refresh token was "
    "revoked. Please log out and sign in again.\n"
)


def test_classify_detects_revoked_refresh_token() -> None:
    assert classify_crash_reason(_REVOKED_CRASH) == 'provider_auth_revoked'


def test_classify_is_case_insensitive() -> None:
    assert classify_crash_reason('REFRESH TOKEN WAS REVOKED') == 'provider_auth_revoked'


def test_classify_ignores_ordinary_crash_and_empty() -> None:
    assert classify_crash_reason('') is None
    assert classify_crash_reason(None) is None  # type: ignore[arg-type]
    assert classify_crash_reason('Traceback: KeyError foo\nExit code 1\n') is None
    # A transient network 401 without a re-auth instruction must NOT be classified
    # as revoked auth, so recovery still restarts the pane.
    assert classify_crash_reason('HTTP 401 Unauthorized on /v1/models') is None


def _fake_backend(captured_text: str) -> object:
    def save_crash_log(pane_id, path, *, lines):  # noqa: ARG001
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(captured_text)

    return SimpleNamespace(save_crash_log=save_crash_log)


def test_persist_crash_log_writes_reason_sidecar_for_revoked_auth(tmp_path) -> None:
    session = SimpleNamespace(runtime_dir=tmp_path)

    capture = persist_crash_log(session, _fake_backend(_REVOKED_CRASH), '%4')

    assert capture.reason == 'provider_auth_revoked'
    assert capture.crash_log is not None and capture.crash_log.name.endswith('.log')
    sidecars = list(tmp_path.glob('pane-crash-*.reason.json'))
    assert len(sidecars) == 1
    payload = json.loads(sidecars[0].read_text(encoding='utf-8'))
    assert payload['reason'] == 'provider_auth_revoked'
    assert payload['matched_signature'] == 'refresh token was revoked'
    assert payload['crash_log'].endswith('.log')
    assert 'codex login' in payload['detail']


def test_persist_crash_log_writes_no_sidecar_for_ordinary_crash(tmp_path) -> None:
    session = SimpleNamespace(runtime_dir=tmp_path)

    capture = persist_crash_log(session, _fake_backend('Segmentation fault\n'), '%4')

    assert capture.reason is None
    assert list(tmp_path.glob('pane-crash-*.reason.json')) == []
    # the raw crash log is still captured
    assert list(tmp_path.glob('pane-crash-*.log'))
    assert capture.crash_log is not None


def test_persist_crash_log_noop_without_saver(tmp_path) -> None:
    session = SimpleNamespace(runtime_dir=tmp_path)
    capture = persist_crash_log(session, SimpleNamespace(), '%4')
    assert capture.crash_log is None and capture.reason is None
