# Native CLI Providers Open Questions

Date: 2026-06-13

## Open

- Should CCB later support `provider = "deepcode"` as an alias for
  `provider = "deepseek"` if user configs naturally follow the binary name?
- Should Kimi get a second execution mode based on `kimi --prompt` after the
  pane-backed mode lands and is stable?
- Should CCB add provider-specific config validation for missing Kimi login or
  missing Deep Code API key, or keep that inside `ccb doctor` only?
- Should CCB add provider-specific config validation for missing MiMo account,
  model, or free-client setup, or keep that inside `ccb doctor` only?
- Can Kiro chat expose stable structured output for normal chat turns, or must
  the first adapter rely on subprocess exit/stdout and pane fallback?
- Should next-wave providers get provider-native skill/instruction projection,
  or should the first release keep inherited ask guidance in prompt wrapping
  only until each native surface is source-confirmed?

## Resolved

- First provider key decision: use `kimi` and `deepseek`; map `deepseek` to
  command `deepcode`.
- First execution decision: keep pane-backed managed runtime for Kimi and
  DeepSeek.
- Completion decision update: replace `CCB_DONE` marker detection for Kimi,
  DeepSeek/DeepCode, and AGY with provider-native session/event log detection.
- MiMo execution decision: keep a managed visible MiMo pane for runtime
  maintenance, but run CCB ask jobs through `mimo run --format json` and
  terminalize from native `step_finish` / `part.reason=stop` result events.
- Next-wave installation decision: install and inspect Qwen, Copilot, Cursor,
  Kiro, and Crush in `/home/bfly/yunwei/test_ccb2/cli-integration-lab`, not in
  the source checkout or global CCB runtime.
- Cursor key decision: use provider key `cursor` and default executable
  `agent`; alias support such as `cursor-agent` is deferred until real user
  configs require it.
- Next-wave first execution decision: use per-job subprocess execution, not
  model-printed `CCB_DONE`.
- Qwen first adapter decision: use one-shot structured subprocess execution;
  dual-output visible pane JSON files are deferred.
- Copilot permission decision: do not inject `--allow-all-tools` by default in
  the first adapter; rely on explicit user startup args/config for broader
  permissions until provider-native policy is validated.
- Crush first adapter decision: use process exit plus stdout and prompt
  wrapping; native skill/config projection is deferred.
- Kiro first adapter decision: use `kiro-cli chat --no-interactive --wrap
  never` with subprocess exit/stdout until stable structured chat output is
  confirmed.
