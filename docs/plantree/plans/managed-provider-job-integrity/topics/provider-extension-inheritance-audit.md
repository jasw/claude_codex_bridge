# Provider Extension Inheritance Audit

Date: 2026-07-20

Role: cross-provider capability and storage-boundary audit
Status: R1/R2 implementation input; follow-up queue authority
Related: [roadmap](../roadmap.md), [ordered repair slices](ordered-repair-slices.md)

## First-Principles Contract

A managed provider home is correct only when all five conditions hold:

1. Configuration that enables an extension and the extension authority needed
   to load it are visible in the same managed session.
2. Read-only source authority may be shared only through a provider-supported
   seed boundary or an immutable content-addressed snapshot.
3. Provider-writable marketplace, cache, plugin-data, and runtime state remains
   agent-local.
4. CCB replaces or removes only targets carrying a valid matching CCB ownership
   marker. Path equality and residence below provider-state are not ownership.
5. Inheritance opt-out and hard role policy prevent source capability exposure.

Copying an entire provider home violates secret/session isolation. Symlinking a
writable cache violates source and cross-agent isolation. Projecting only an
`enabledPlugins`-style setting violates startup completeness.

## Audit Matrix

| Provider | Official capability/storage evidence | Current CCB behavior | Result | Follow-up |
| :--- | :--- | :--- | :--- | :--- |
| Codex | Current plugin layouts include immutable bundle authority plus writable marketplace/cache state observed by PR257. | CCB isolates `CODEX_HOME`; this candidate keeps `.tmp/plugins` content-addressed and converts `.tmp/marketplaces` plus `plugins/cache` to marker-owned local seeds. | Fixed in R1 candidate. | Land with rollback, missing-source, upgrade, and two-agent tests. |
| Claude | [Claude environment variables](https://code.claude.com/docs/en/env-vars) define read-only `CLAUDE_CODE_PLUGIN_SEED_DIR`; the misleadingly named `CLAUDE_CODE_PLUGIN_CACHE_DIR` selects the full writable plugins root. | CCB isolates `HOME`; this candidate exposes a usable source seed and assigns each agent its own `.claude/plugins/` root before process start. | Fixed in R2 candidate. | Real inherited-plugin validation remains environment-dependent. |
| Gemini | [Gemini extension reference](https://geminicli.com/docs/extensions/reference/) loads extensions from `<home>/.gemini/extensions`; [Gemini configuration](https://geminicli.com/docs/get-started/configuration-v1/) says `GEMINI_CLI_HOME` replaces the user storage root. | CCB sets isolated `HOME` and `GEMINI_CLI_HOME`, but materializes settings, auth, trust, and memory only. | Confirmed same class of missing-extension defect. | Design an extension snapshot/local-state split; do not copy all `.gemini`. |
| Qwen | [Qwen extensions](https://qwenlm.github.io/qwen-code-docs/en/users/extension/introduction/) load global extensions under `.qwen/extensions`; [Qwen configuration](https://qwenlm.github.io/qwen-code-docs/en/users/configuration/settings/) defines `QWEN_HOME` as the global credentials/settings/skills/state root. | CCB redirects `QWEN_HOME` to an empty agent state root and does not seed extensions or their registry state. | Confirmed same class, with a broader config/auth inheritance gap. | Freeze Qwen-owned registry and writable-state paths before projection. |
| GitHub Copilot | [Copilot config-directory reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference) says changing `COPILOT_HOME` hides existing config, extensions, installed plugins, permissions, and sessions; [plugin reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference) separates installed plugins, marketplace cache, and plugin data. | CCB assigns a fresh per-agent `COPILOT_HOME` without config/plugin projection. | Confirmed same class; directory-only copy would be incomplete. | Design a config-aware seed plus local plugin-data/cache boundary. |
| Droid | [Factory plugin documentation](https://docs.factory.ai/cli/configuration/plugins) stores user-scoped plugins and marketplace settings under `~/.factory/`. | CCB redirects `FACTORY_HOME` and currently projects only `skills/`. | Confirmed capability gap; exact provider-supported seed mechanism is not yet documented. | Reproduce with a user plugin, then design settings/marketplace/plugin-cache projection without copying sessions or secrets. |
| OpenCode | [OpenCode config](https://opencode.ai/docs/config/) merges global config before `OPENCODE_CONFIG`; [OpenCode plugins](https://opencode.ai/docs/plugins/) loads global plugins from `~/.config/opencode/plugins/` and npm cache from `~/.cache/opencode/node_modules/`. | CCB supplies a generated `OPENCODE_CONFIG` but does not replace `HOME` or XDG roots. | Not the same missing-plugin defect. Global mutable cache sharing is a separate isolation/performance risk. | Keep out of R1/R2; audit shared cache only if failures are observed. |
| Kimi | [Kimi skills](https://moonshotai.github.io/kimi-cli/en/customization/skills.html) supports normal discovery plus explicit `--skills-dir`. | CCB does not replace user `HOME` and explicitly appends project, user, inherited, role, and overlay skill roots. | No same skill-inheritance defect under the current launcher. Plugin-specific behavior still needs a separate capability test. | No immediate fix. |
| Grok, Cursor, Kiro, MiMo, Z.ai, AGY, Pi, Crush | No reviewed official source in this audit proves a stable user plugin registry and relocation/seed contract for the CCB-managed version. | Several launchers isolate a home or provider state and inject CCB skills, but storage semantics differ. | Unproven, not cleared. | Require official path semantics plus one installed-extension runtime repro before implementation. |

## Cross-Cutting Ownership Risk

The plugin gap is not the only destructive projection surface. Current helpers
for packaged inherited skills and some Claude/Droid trees still use
`allow_unmarked_replace=True`. That flag can replace an unmarked directory when
the source appears or remove it when inheritance is disabled/source is absent.
This is the same ownership-policy smell, but it is not silently broadened into
R1/R2 because those paths have different compatibility and migration contracts.

Create a later ownership-hardening slice that inventories every call site,
adds marker-first migration tests, and removes the flag provider by provider.

## Ordered Follow-Up

1. Land the combined R1/R2 candidate and prove no source-home mutation.
2. Repair Gemini extension inheritance with the same immutable-seed/local-write
   split.
3. Repair Qwen, Copilot, and Droid only after each provider's registry,
   credentials, plugin data, and cache ownership are frozen independently.
4. Audit remaining isolated-home providers only from official capability and
   runtime evidence; absence of a local test installation is not proof that a
   provider has no extension problem.
5. Run the generic `allow_unmarked_replace` ownership-hardening slice after the
   provider-specific compatibility paths are documented.

## Acceptance Evidence For A Provider Follow-Up

- A source-home installed extension is visible in the first managed session.
- Inheritance-disabled startup does not expose it.
- Two agents load it while writable state remains different and source files do
  not change.
- Missing source and malformed/foreign marker cases preserve local data.
- Source update, rollback, restart, WSL/Windows path handling where applicable,
  and cleanup are covered.
- The provider-specific isolation and storage contracts are updated in the same
  patch.
