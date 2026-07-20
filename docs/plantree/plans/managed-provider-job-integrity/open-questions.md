# Managed Provider And Job Integrity Open Questions

Date: 2026-07-20

These questions do not block recording the roadmap. Each must be resolved
before production implementation of its owning slice starts.

1. **R4:** When a chain child is cancelled, should the parent continuation run
   with a structured cancelled result, or should the callback edge terminalize
   as failed/cancelled without creating a provider continuation job?
2. **R6:** Which persisted Kimi record owns the exact native session ID, and
   what is the user-visible behavior when that ID no longer exists upstream?
3. **R7:** What is the minimal stable `execution_phase` vocabulary, and does
   adding it remain a schema-v1 optional extension or require a schema version
   transition for all clients?
4. **R9:** Which exact Codex and Claude native mechanisms qualify as safe
   active-turn correction, and what capability response should providers
   return when only cancel-and-resubmit is safe?
5. **R11:** For Gemini, Qwen, Copilot, and Droid, which provider-supported
   mechanism separates immutable extension authority from mutable registry,
   plugin data, credentials, and cache without copying the whole user home?
