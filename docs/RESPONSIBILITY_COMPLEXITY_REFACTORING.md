# Structural refactoring in Oh-Goon

Use agentsCookbook's [finding derivation](https://github.com/Hugloss/agentsCookbook/blob/40be826e6883d819ee6cf5c10946c793f0ef0f5f/skills/codebase-finding-derivation/SKILL.md) and [semantic redecision review](https://github.com/Hugloss/agentsCookbook/blob/40be826e6883d819ee6cf5c10946c793f0ef0f5f/skills/semantic-redecision-review/SKILL.md) for reusable review methodology. This page records the local product constraints that a review must preserve. Cookbook findings are advisory; they do not grant edit, execution or certification authority.

## Local ownership constraints

- Preserve the single Plan/Goon resolver, Fleet admission and Step lifecycle, native process supervisor, `LocalDatabase` transaction boundary, Journal result authority and Game Tape projections.
- Keep an invariant with the owner that can enforce it. A split must not create another source resolver, database transaction service, execution loop, retry owner, mutable result cache or certification decision.
- LOC, fan-in and fan-out are investigation signals. Before changing an owner, record the supported disposition: **KEEP COHESIVE**, **REFACTOR INTERNALLY**, **EXTRACT RESPONSIBILITY**, or **DECOMPOSE MULTIPLE RESPONSIBILITIES**. A metric threshold alone cannot justify moving an execution or durable-state boundary.
- Name extracted responsibilities by the product contract they own. Changes to private topology must preserve public/serialized identities, ordering, cancellation, failure outcomes, state transitions and provenance unless the task explicitly changes that contract.
- Hashmarks owns repository intelligence and verification selection. Oh-Goon consumes its supported evidence contract; refactoring must not introduce a local replacement for producer semantics.
- Native uv, npm, Git and operating-system facilities retain their semantics. A refactor cannot replace a native tool operation with an Oh-Goon resolver, cache or fallback.

## Review and verification

Select the owner using [AGENTS.md](../AGENTS.md) and the [architecture map](ARCHITECTURE.md). State the actual contract changed or preserved, then run the focused owner proof and only the change-impact checks that apply. Private spelling, line count or a moved helper is not an observable product regression.

Keep phase measurements, old rename inventories and candidate qualification results with their historical evidence. They are not a current work queue. Current product decisions live in [Architecture decisions](development/ARCHITECTURE_DECISIONS.md); tests follow the [local test policy](development/TEST_QUALITY.md).
