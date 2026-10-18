# ADR — SKILL-3B, author effects once, transport as prework demands only

**Decision:** One typed `AgentStepRequestedEffects` is authored on the existing `AgentPlanStep`, bound by the original Resolver to `FrozenAgentStepHandoff`, and delivered through the original AG-02 packet. `PlanStepCapabilities` owns tool and network vocabularies, avoiding parallel Agent-specific registries. The original SKILL-3A adapter is a read-only projection.

**Identity and compatibility:** On non-default requested effects, `requested_effects` is added to the handoff's domain-separated identity payload and the exact step packet. The decoder verifies the full original re-encoding, preventing tool/recipient/write scope swaps. Absent/default demand retains the old canonical packet bytes and stable v1 identity. These are demands, never authoritative execution permits.

**Owners:** Resolver interprets and captures, Enforcer admits independently, LACE enforces physical restrictions, original Step supervisor executes, journal/Game Tape record the authoritative outcome. No new model egress client, tool launcher, trust signer, policy-redecision point, or runtime. A model output cannot grant its own requested effects.

**Deferred:** exact original Run/Step tool/write/recipient grant matching, controlled recipient routing, contained agent harness process execution, journaled effect receipts, cancellation/restart and native qualification. Until each original gate is proven, production Agent Steps remain preview-only.
