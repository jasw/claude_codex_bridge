<div align="center">

# CCB - Mobile Has Arrived!

**Designed around decentralized multi-agent collaboration**  
**A visible, controllable multi-agent TUI workspace**

<p>
  <img src="https://img.shields.io/badge/version-8.0.15-orange.svg" alt="version">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20WSL-lightgrey.svg" alt="platform">
  <img src="https://img.shields.io/badge/providers-15%20CLI%20families-0B7285.svg" alt="providers">
</p>

<p>
  <img src="https://img.shields.io/badge/Codex-111111?style=flat-square&logo=openai&logoColor=white" alt="Codex">
  <img src="https://img.shields.io/badge/Claude-D97757?style=flat-square&logo=anthropic&logoColor=white" alt="Claude">
  <img src="https://img.shields.io/badge/Gemini-4285F4?style=flat-square&logo=googlegemini&logoColor=white" alt="Gemini">
  <img src="https://img.shields.io/badge/Kimi-111111?style=flat-square&logo=moonshotai&logoColor=white" alt="Kimi">
  <img src="https://img.shields.io/badge/MiMo-FF6900?style=flat-square&logo=xiaomi&logoColor=white" alt="MiMo">
  <img src="https://img.shields.io/badge/Qwen-6A5CFF?style=flat-square" alt="Qwen">
  <img src="https://img.shields.io/badge/Cursor-111111?style=flat-square" alt="Cursor">
  <img src="https://img.shields.io/badge/Copilot-111111?style=flat-square&logo=githubcopilot&logoColor=white" alt="GitHub Copilot">
  <img src="https://img.shields.io/badge/Crush-FF5A5F?style=flat-square" alt="Crush">
  <img src="https://img.shields.io/badge/Kiro-6D5EF6?style=flat-square" alt="Kiro">
  <img src="https://img.shields.io/badge/Pi-111111?style=flat-square" alt="Pi">
  <img src="https://img.shields.io/badge/Z.ai-111111?style=flat-square" alt="Z.ai">
  <img src="https://img.shields.io/badge/OpenCode-111111?style=flat-square" alt="OpenCode">
  <img src="https://img.shields.io/badge/Antigravity-6D5EF6?style=flat-square&logo=google&logoColor=white" alt="Antigravity">
  <img src="https://img.shields.io/badge/Droid-3DDC84?style=flat-square&logo=android&logoColor=white" alt="Droid">
</p>

[中文](README.md) | **English** | [日本語](readme_ja.md) | [Français](readme_fr.md) | [Deutsch](readme_de.md) | [العربية](readme_ar.md) | [Español](readme_es.md) | [Português](readme_pt.md) | [한국어](readme_ko.md) | [Русский](readme_ru.md)

[Quick Start](#quick-start) · [Mobile App](#mobile-app) · [Rich Mode](#rich-mode) · [Configure Agents](#configure-agents) · [User Guide](docs/manuals/user-guide/) · [Developer Guide](docs/manuals/developer-guide/)

<p align="center">
  <img src="assets/readme_v7/ccb-hero-en-light.png" alt="CCB visible multi-agent CLI workspace" width="960">
</p>

</div>

<a id="why-ccb"></a>

## Why CCB?

- Stable inter-agent communication for complex collaboration graphs such as `A -> B -> C`, `A,B -> C`, and `A -> B,C`.
- Every agent is a full native terminal with visible layout control and direct takeover.
- The background daemon keeps project state alive even when the foreground UI is closed.
- Hub capability: run multiple CLI providers concurrently from one command.
- Mobile remote controller: cross-provider voice control, file transfer, and remote terminal access.

<a id="how-to-install"></a>

## How to Install

Install or update with npm:

```bash
npm install -g @seemseam/ccb
```

After CCB is installed, use CCB's updater:

```bash
ccb update
```

<details>
<summary><b>GitHub release package and source install fallbacks</b></summary>

If npm is not convenient in your environment, download the matching package from [Releases](https://github.com/SeemSeam/claude_codex_bridge/releases), unpack it, and install:

```bash
tar -xzf ccb-*.tar.gz
cd ccb-*
./install.sh install
```

Source install is intended only for development or temporary fallback:

```bash
git clone https://github.com/SeemSeam/claude_codex_bridge.git
cd claude_codex_bridge
./install.sh install
```

Source install links global `ccb` / `ask` back to the checkout. Regular users should prefer the npm package.

</details>

<a id="quick-start"></a>

## Quick Start

### 1. Launch

Run this from your working directory:

```bash
ccb
```

If startup reports that `.ccb` cannot be created automatically or that the project anchor is missing, create `.ccb` manually:

```bash
mkdir -p .ccb
```

<a id="configure-agents"></a>

### 2. Create Project Config

Create `.ccb/ccb.config` in the project root. The recommended v2 `[windows]` topology uses `,` and `;` to control vertical stacking and horizontal splits inside each window, so `A,B;C,D` is close to a four-pane layout.

```toml
version = 2

[windows]
main = "main:codex"
work = "worker1:codex(worktree), worker2:claude(worktree)"
review = "reviewer:claude, qa:gemini"

[ui.sidebar]
mode = "every_window"
width = "15%"
bottom_height = 20
agents_height = "50%"
comms_height = "15%"
tips_height = "35%"
comms_limit = 3
```

Validate the config and start the workspace:

```bash
ccb config validate
ccb
```

### 3. Collaborate

You can type directly in any agent pane, or let agents collaborate:

```text
/ask reviewer review the latest parser changes and list blocking issues.
```

Agents can also call `/ask` during workflow orchestration to delegate and hand off work. Use agent memory or the project-wide shared memory file `.ccb/ccb_memory.md` for durable coordination.

<a id="mobile-app"></a>

## Mobile Remote Control (Android)

The recommended way to control CCB from a phone can connect to all CCB projects, control each agent, accept voice input, and transfer files.

```bash
ccb update mobile
```

This command guides installation and configuration.

<p align="center">
  <img src="assets/readme_v7/mobile-control-chat.jpg" alt="CCB Mobile agent chat" width="180">
  <img src="assets/readme_v7/mobile-control-terminal.jpg" alt="CCB Mobile terminal control" width="180">
  <img src="assets/readme_v7/mobile-control-files.jpg" alt="CCB Mobile file transfer" width="180">
  <img src="assets/readme_v7/mobile-control-pairing.jpg" alt="CCB Mobile pairing and connection" width="180">
</p>

<details>
<summary><b>Mobile App details, safety boundary, and source</b></summary>

CCB 8.0.15 includes the Flutter CCB Mobile source in [`mobile/`](mobile/) and publishes the Android APK through GitHub Releases:

- [Download CCB Mobile v8.0.15 APK](https://github.com/bfly123/claude_code_bridge/releases/download/v8.0.15/ccb-mobile-v8.0.15.apk)
- App source: [`mobile/app`](mobile/app)
- Server gateway source: [`lib/mobile_gateway`](lib/mobile_gateway)

The phone app is a remote controller for real CCB projects running on a server. It can discover mounted projects from the server-wide mobile gateway, switch windows and agents, render agent conversation context, send text through pane-native input, open a terminal view, and upload/download images and documents through the authenticated gateway.

Safety boundary:

- The CCB gateway binds only to loopback, for example `127.0.0.1:8787`.
- Remote access uses Tailscale Serve, not Tailscale Funnel.
- CCB does not store Tailscale passwords, OAuth tokens, admin API tokens, or automatically modify tailnet ACLs/grants.
- The phone receives only the scopes authorized by the pairing profile, such as view, content, terminal, file upload, and file download.

</details>

<a id="rich-mode"></a>

## Rich Media Terminal

Browse file trees, open files, edit documents, and preview media inside the terminal.

<p align="center">
  <img src="assets/readme_v7/rich-workbench.png" alt="CCB rich media workbench using Yazi preview in WezTerm" width="860">
</p>

```bash
ccb update rich
```

After rich mode is enabled, plain `ccb` opens the rich WezTerm launcher automatically unless it is already running inside a CCB-managed rich WezTerm session. Run `ccb uninstall rich` to return to normal terminal startup.

<a id="agent-roles"></a>

## Agent Roles Spec And Role Catalog

CCB supports [Agent Roles Spec](https://github.com/SeemSeam/agent-roles-spec), a host-neutral specification for packaging specialist agents. It can bundle skills, memory, and tool dependencies into installable, mountable, and removable Role Packs. That repository also serves as the public role catalog.

| Role | Purpose |
| :--- | :--- |
| `agentroles.ccb_self` | CCB self-maintenance, config help, runtime diagnosis, protected recovery, and workflow orchestration. |
| `agentroles.archi` | Architecture review, boundary checks, coupling analysis, maintainability risks, and follow-up gate advice. |
| `agentroles.frontend_engineer` | Frontend design and implementation, design systems, accessibility, browser QA, and reviewed AGY delegation. |
| `agentroles.mobile_app_engineer` | Mobile design and implementation for iOS, Android, React Native, Expo, Flutter, SwiftUI, Jetpack Compose, and more. |
| `agentroles.mother` | Role creation, role source audit, role research, blueprint design, and Agent Roles spec compliance checks. |
| `agentroles.su_ccb` | SU-CCB workflow operations for requirement analysis, planning, dispatch, review gates, archiving, and recovery. |

<a id="config-memory"></a>

## Config And Shared Memory

If you are not sure how to group windows, how many workers you need, which agents should use worktrees, or which agents need separate models or API routes, ask `ccb_self` in the current workspace. It is CCB's built-in self-agent: it understands CCB commands, config authority, roles, windows, reload boundaries, and common recovery paths, and it can use its private `ccb-config` skill to design a config with you. Blank projects include `ccb_self`; existing custom configs can add it with `ccb roles add agentroles.ccb_self:codex`.

`.ccb/ccb_memory.md` is the project-wide shared memory document. Use it for team collaboration rules, project constraints, long-lived context, and agent handoff conventions. Stable cross-agent information belongs there instead of being copied into several provider-private memory files.

<a id="contact"></a>

## Contact

- Email: `bfly123@126.com`
- [Telegram group & contact / TG 群与联系](https://t.me/+BKn03v8I_ehmYzRk)
- WeChat: `seemseam-com`

<p align="center">
  <img src="assets/weixin.jpg" alt="WeChat group" width="240">
</p>

<a id="community"></a>

## Community And Credits

Thanks to the [Linux.do community](https://linux.do) for testing, feedback, and discussion.

Thanks to [tmux-agent-sidebar](https://github.com/hiroppy/tmux-agent-sidebar) for sidebar ideas and inspiration.

<a id="release-notes"></a>

## Release Notes

<details open>
<summary><b>v8.0.12</b> - Release CI portability and README localization</summary>

- Mobile host registry tests now place temporary Unix sockets under a short `/tmp/ccb-sock-*` path, avoiding `AF_UNIX path too long` failures on macOS CI.
- `ccb update mobile`, README links, package metadata, and the mobile release manifest now point to the 8.0.12 APK.
- The Chinese README is now the GitHub main README; English moved to `readme_en.md`, and Japanese, French, German, Arabic, Spanish, Portuguese, Korean, and Russian variants were added with the same section structure.

</details>

<details>
<summary><b>v8.0.0</b> - CCB Mobile Monorepo release</summary>

- The Flutter CCB Mobile source officially moved into this repository, with the Android APK published through GitHub Releases.
- Added server-wide mobile project discovery, pairing, authenticated gateway routes, pane-native message input, conversation context rendering, terminal access, and image/document upload and download.
- Promoted `ccb update mobile` into the unified Tailscale Tailnet onboarding entrypoint while keeping the gateway loopback-only, avoiding Funnel, not storing tokens, and not automatically modifying ACLs/grants.

</details>

<details>
<summary><b>v7.7.0</b> - Runtime Accelerator release hardening</summary>

- Release artifacts now include the optional Rust `ccb-runtime-accelerator`; installed Codex agents no longer silently fall back to the Python hot path when the sidecar is expected.
- When a project path makes the Unix socket path too long, the accelerator socket automatically moves to a short per-user runtime socket root.
- Hardened callback repair and Codex binding cache invalidation, with recorded regression, long-idle Codex soak, Claude callback, and mixed-provider integration evidence.

</details>

<details>
<summary><b>v7.6.19</b> - Long-running ask default wait policy</summary>

- Regular long-running `ask` calls now continue waiting for real provider/completion results instead of terminalizing as `incomplete/heartbeat_timeout` only because of heartbeat diagnostics.
- Codex, Claude, and Gemini pane-backed no-terminal timeouts are now explicit opt-in by default, while explicit reliability timeout policies remain available.
- A 32-minute source-runtime ask smoke confirmed that a task can remain running for more than 30 minutes, then complete with `result_message`, without `heartbeat_timeout` or `incomplete` evidence.

</details>

See the full history in [CHANGELOG.md](CHANGELOG.md).
