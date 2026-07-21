# Managed Provider And Job Integrity Open Questions

Date: 2026-07-21

These questions do not block recording the roadmap. Each must be resolved
before production implementation of its owning slice starts.

1. **R7:** What is the minimal stable `execution_phase` vocabulary, and does
   adding it remain a schema-v1 optional extension or require a schema version
   transition for all clients?
2. **R9:** Which exact Codex and Claude native mechanisms qualify as safe
   active-turn correction, and what capability response should providers
   return when only cancel-and-resubmit is safe?
3. **R11:** For Copilot, which entry-level ownership model can project installed
   plugins and marketplaces from its mixed config without copying or
   overwriting credentials, sessions, permissions, and local plugin data?
