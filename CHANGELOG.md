# Changelog

## 0.0.4 — 2026-10-31

Targets Open WebUI v0.12.5.

### Changes

- Use one OWUI model call before the answer to decide whether to add, replace, remove, or skip persistent memories. No language-specific trigger words or separate extraction API key.
- Share the same user's global memories across root chats; keep each folder isolated. Preserve existing folder/chat tags, with chat tags serving as provenance.
- Apply changes through OWUI's memory operations API, including vector index updates/removal and events. Validate target scope, source quotations, and current-user evidence before mutation.
- Pass actual operation outcomes to the answer model; skip ambiguous mutations and avoid confirming writes that did not occur.
- Disable conflicting native memory injection, tools, and background review for filtered requests while preserving unrelated features.
- Remove long-lived memory caching and automatic orphan deletion. Management edits take effect on the next turn, and deleting a source chat does not erase shared folder memories.
- Show concise English statuses, optional diagnostic stages, and model/write/total timings. Add a bounded decision output budget and a configurable OWUI memory model.

### Upgrade notes

- Replace the previous Filter Function; enable only one instance and apply it globally where isolation is needed.
- Keep OWUI's admin memory service and user memory permission enabled.
- `atomic_memory_enabled`, `raw_save_requires_request`, and external `openai_*` extraction settings are obsolete and ignored. Use `memory_model` for an optional OWUI model override.
- Root chats now share one per-user global pool, including existing root memories. `F_ID` still determines folder isolation; `C_ID` records provenance only. No data migration is required.
- Each turn adds a model decision before the answer. A fast non-reasoning `memory_model` can reduce waiting; actual latency depends on the deployment.
- Temporary/unsaved chats, non-owned shared folders, and custom Pipes/direct/arena models have limitations described in README.md.

### Validation

- 32 local behavior tests passed, using simulated OWUI storage and model decisions.
- `git diff --check` passed.
- User-reported test-environment checks confirmed global cross-chat recall and folder memory recall. These are smoke tests, not exhaustive live integration or model-accuracy validation.
