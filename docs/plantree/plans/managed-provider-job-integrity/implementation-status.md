# Managed Provider And Job Integrity Status

Date: 2026-07-20

## Current Phase

R11 provider-extension candidate committed on branch
`fix/unified-provider-extension-inheritance`, based on `origin/main` at
`aed27abf` (merged PR269).

## Next Target

Integrate the qualified branch only after an explicit push/merge instruction.

## Last Landed

R1/R2 landed as `06e1a46a` through merge `aed27abf`.

## Active TODO

1. Push or merge the committed candidate when integration is requested.

## Blocked By

Nothing blocks Claude, Gemini, or Droid qualification. Qwen is source-qualified
only because this host has no Qwen executable. Copilot remains deferred because
its plugin metadata shares config authority with auth/session-sensitive state.

## Last Verified

- Focused provider-profile, hook, and launcher files: `282 passed`.
- Full Python suite reached `4181 passed`, `15 skipped` before the unchanged
  baseline shutdown race failed; the exact test reproduced independently.
- Complete suite excluding that one adjudicated baseline test: `5389 passed`,
  `15 skipped`, `1 deselected`.
- External project
  `/home/bfly/yunwei/test_ccb2/provider-extension-inheritance-bootstrap-20260720-pV3QUQ`:
  clean-home Claude Code 2.1.206 loaded the fixture skill on its first CCB pane
  and returned `ccb-fixture-plugin-loaded` without reload.
- Follow-up external project
  `/home/bfly/yunwei/test_ccb2/provider-extension-local-path-20260720` repeated
  that first-pane load with the installed plugin path rebased into the current
  agent-local cache; source plugin SHA256 remained unchanged.
- Real Gemini CLI listed the managed fixture extension as active; real Droid
  CLI listed the active plugin from an agent-local projection with rebased
  install paths in the system-source integration check.
- Claude, Gemini, and Droid source asset comparisons remained unchanged.
- Candidate `ccb_test kill` left both projects unmounted; the follow-up project
  reported `unmounted/stopped` with no project or tmux socket.
- Python compilation and `git diff --check`: passed.

Full evidence:
[history/r11-provider-extension-validation-2026-07-20.md](history/r11-provider-extension-validation-2026-07-20.md).
