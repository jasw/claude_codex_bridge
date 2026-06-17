# Inter-Agent Communication Reliability Roadmap

Date: 2026-06-14

## Status Summary

- Current status: Planning only.
- Branch policy: no source implementation or main-branch promotion from this
  pass.
- Last analysis: PR226 improves low-probability Linux/macOS/WSL transport
  races, but it should not be treated as a completed stability boundary without
  follow-up guards.
- Last verified: targeted PR226-adjacent tests passed in a clean
  `origin/main` worktree:
  `python -m pytest -q test/test_bridge_fifo_persistent_reader.py test/test_fifo_delivery.py test/test_transport_parity.py test/test_cancel_flags.py`
  -> `26 passed`.

## Done

- Recorded that the current user decision is plan-tree only: do not land new
  source changes or promote follow-up work into main.
- Classified PR226 benefits for Linux, macOS, and WSL:
  - reduces FIFO no-reader windows with a persistent reader
  - bounds sender waits when the bridge is unavailable
  - adds read-level ACK evidence
  - uses spool files for large FIFO payloads
  - adds communication-path logs for previously silent failures
- Recorded current risk analysis in
  [topics/pr226-risk-and-adoption-note.md](topics/pr226-risk-and-adoption-note.md).

## Next

1. Decide whether CCB wants PR226-style transport hardening as a release goal
   or only as a diagnostic/stress-mode hardening track.
2. If promoted later, add focused tests for ACK semantics, marker uniqueness,
   spool path constraints, and cancel prompt injection before source changes.
3. Keep Linux/macOS/WSL as the only supported target set for this plan slice.

## Deferred

- Implementing or merging any follow-up source changes.
- Changing shipped ACK wording or sender result semantics.
- Changing marker generation.
- Enforcing spool path restrictions.
- Changing cancel prompt injection behavior.
- Native Windows transport support.
