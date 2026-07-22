from __future__ import annotations

from types import SimpleNamespace

from ccbd.services.health_assessment.tmux_runtime.state import tmux_pane_state


def test_tmux_pane_state_returns_missing_when_pane_is_absent() -> None:
    backend = SimpleNamespace(pane_exists=lambda pane_id: False)

    assert tmux_pane_state(object(), backend, "%1") == "missing"


def _ownership(state: str) -> SimpleNamespace:
    return SimpleNamespace(state=state, is_owned=(state == "owned"))


def _patch_ownership(monkeypatch, ownership: SimpleNamespace) -> None:
    monkeypatch.setattr(
        "ccbd.services.health_assessment.tmux_runtime.state.inspect_tmux_pane_ownership",
        lambda session, backend, pane_id: ownership,
    )


def test_tmux_pane_state_returns_foreign_when_ownership_mismatches(monkeypatch) -> None:
    backend = SimpleNamespace(pane_exists=lambda pane_id: True, is_tmux_pane_alive=lambda pane_id: False)
    _patch_ownership(monkeypatch, _ownership("foreign"))

    assert tmux_pane_state(object(), backend, "%1") == "foreign"


def test_tmux_pane_state_trusts_alive_pane_when_ownership_marker_is_stale(monkeypatch) -> None:
    # Stale/unrecorded ownership (state 'unknown') on a live pane must not force a
    # respawn loop: the live pane is trusted.
    backend = SimpleNamespace(pane_exists=lambda pane_id: True, is_tmux_pane_alive=lambda pane_id: True)
    _patch_ownership(monkeypatch, _ownership("unknown"))

    assert tmux_pane_state(object(), backend, "%1") == "alive"


def test_tmux_pane_state_rejects_alive_pane_owned_by_another_session(monkeypatch) -> None:
    # A live pane whose OWN recorded options prove it belongs to a different
    # session (state 'foreign') must never be adopted, even though it is alive.
    backend = SimpleNamespace(pane_exists=lambda pane_id: True, is_tmux_pane_alive=lambda pane_id: True)
    _patch_ownership(monkeypatch, _ownership("foreign"))

    assert tmux_pane_state(object(), backend, "%1") == "foreign"


def test_tmux_pane_state_prefers_tmux_alive_method(monkeypatch) -> None:
    backend = SimpleNamespace(
        pane_exists=lambda pane_id: True,
        is_tmux_pane_alive=lambda pane_id: True,
    )
    monkeypatch.setattr(
        "ccbd.services.health_assessment.tmux_runtime.state.inspect_tmux_pane_ownership",
        lambda session, backend, pane_id: SimpleNamespace(is_owned=True),
    )

    assert tmux_pane_state(object(), backend, "%1") == "alive"
