from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Callable, NamedTuple

from provider_core.tmux_ownership import (
    apply_session_tmux_identity,
    inspect_tmux_pane_ownership,
)

# Substrings that mark a pane crash as an *unrecoverable* provider auth failure:
# restarting the pane will not fix it, the provider must be re-authenticated.
# Matched case-insensitively against the captured crash log. Kept deliberately
# specific to avoid classifying transient network 401s as auth revocation.
_AUTH_REVOKED_SIGNATURES = (
    'refresh token was revoked',
    'log out and sign in again',
    'please sign in again',
    'run `codex login`',
    'run codex login',
    'you are not signed in',
)

_CRASH_REASON_DETAIL = {
    'provider_auth_revoked': (
        'Provider authentication was revoked or expired; the captured crash log '
        'matched a re-authentication signature. CCB may refresh changed inherited '
        'Codex auth once; otherwise run `codex login` in the source profile or '
        'repair agent-local auth, then remount.'
    ),
}


class CrashLogCapture(NamedTuple):
    """Result of capturing a pane crash log: the saved log ``crash_log`` (used to
    parse a provider's own resume hint) and the classified ``reason`` code (used
    to gate unrecoverable auth-revoked crashes). Either field may be ``None``."""

    crash_log: Path | None
    reason: str | None


def classify_crash_reason(text: str) -> str | None:
    """Classify a captured pane crash log against known unrecoverable conditions.

    Returns a short reason code (currently only ``'provider_auth_revoked'``) when
    the crash is caused by a provider auth failure that a pane restart cannot
    recover, otherwise ``None``. Pure and side-effect free so it can be unit
    tested without a live pane.
    """
    if not text:
        return None
    haystack = text.lower()
    for signature in _AUTH_REVOKED_SIGNATURES:
        if signature in haystack:
            return 'provider_auth_revoked'
    return None


def attach_pane_log(session, backend: object, pane_id: str) -> None:
    ensure = getattr(backend, 'ensure_pane_log', None)
    if callable(ensure):
        try:
            ensure(str(pane_id))
        except Exception:
            pass


def live_owned_pane(session, backend: object, pane_id: str) -> str | None:
    if not pane_id or not backend.is_alive(pane_id):
        return None
    ownership = inspect_tmux_pane_ownership(session, backend, str(pane_id))
    if not ownership.is_owned:
        return None
    return str(pane_id)


def activate_rebound_pane(
    session,
    backend: object,
    pane_id: str,
    *,
    now_str_fn: Callable[[], str],
    attach_pane_log_fn: Callable[[object, object, str], None],
) -> None:
    bind_session_to_pane(session, pane_id, now_str_fn=now_str_fn)
    apply_session_tmux_identity(session, backend, pane_id)
    attach_pane_log_fn(session, backend, pane_id)


def persist_crash_log(session, backend: object, pane_id: str) -> CrashLogCapture:
    saver = getattr(backend, 'save_crash_log', None)
    if not callable(saver):
        return CrashLogCapture(None, None)
    try:
        runtime = session.runtime_dir
        runtime.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        crash_log = runtime / f'pane-crash-{ts}.log'
        saver(pane_id, str(crash_log), lines=1000)
        return CrashLogCapture(crash_log, _persist_crash_reason(runtime, crash_log, ts))
    except Exception:
        return CrashLogCapture(None, None)


def _persist_crash_reason(runtime, crash_log, ts: int) -> str | None:
    """Classify the freshly captured crash log and, when it matches a known
    unrecoverable condition, drop an actionable ``pane-crash-<ts>.reason.json``
    sidecar next to it. Returns the reason code, or ``None`` when unclassified.

    Best-effort: any failure here must not disrupt crash-log capture or the
    downstream pane respawn, so all errors are swallowed.
    """
    try:
        text = crash_log.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return None
    reason = classify_crash_reason(text)
    if reason is None:
        return None
    payload = {
        'schema_version': 1,
        'record_type': 'pane_crash_reason',
        'reason': reason,
        'detail': _CRASH_REASON_DETAIL.get(reason, ''),
        'matched_signature': _matched_signature(text),
        'crash_log': crash_log.name,
        'detected_at': ts,
    }
    try:
        reason_path = runtime / f'pane-crash-{ts}.reason.json'
        reason_path.write_text(
            json.dumps(payload, ensure_ascii=False) + '\n', encoding='utf-8'
        )
    except Exception:
        pass
    return reason


def _matched_signature(text: str) -> str | None:
    haystack = text.lower()
    for signature in _AUTH_REVOKED_SIGNATURES:
        if signature in haystack:
            return signature
    return None


def pane_exists(backend: object, pane_id: str) -> bool:
    checker = getattr(backend, 'pane_exists', None)
    if not callable(checker):
        return True
    try:
        return bool(checker(pane_id))
    except Exception:
        return True


def bind_session_to_pane(session, pane_id: str, *, now_str_fn: Callable[[], str]) -> None:
    data = getattr(session, 'data', None)
    if not isinstance(data, dict):
        return
    data['pane_id'] = str(pane_id)
    if str(getattr(session, 'terminal', '') or '').strip().lower() == 'tmux':
        data['tmux_session'] = str(pane_id)
    data['updated_at'] = now_str_fn()
    writer = getattr(session, '_write_back', None)
    if callable(writer):
        writer()


__all__ = [
    'activate_rebound_pane',
    'attach_pane_log',
    'bind_session_to_pane',
    'classify_crash_reason',
    'live_owned_pane',
    'pane_exists',
    'persist_crash_log',
]
