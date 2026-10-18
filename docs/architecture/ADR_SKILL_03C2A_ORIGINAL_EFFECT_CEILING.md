# ADR — SKILL-3C2A: bound Agent demand by separately supplied Enforcer limits

Status: IMPLEMENTED DEVELOPMENT SCOPE / NON-EXECUTABLE / NO EFFECT GRANT.

## Problem

SKILL-3C1 only correlates an immutable AG-02 author request with the original signed Run/Step envelope. Matching a requested demand to its own signed copy cannot prove that an independent Enforcer policy authorized repository writes, model disclosure, tools or network. Allowing that match to act as a permission would create a dangerous self-authorization escalation.

## Decision and owner

Extend the **existing** `CausalRunJournal.reserve_agent_source_prework_in` original Fleet SQLite write cut. The original issuer-frozen `step_envelopes[step_id].agent_effect_limits` is a distinct, fully typed, canonical `AgentStepRequestedEffects` ceiling. The AG-02 packet **cannot** provide this field; the sole original signed `AdmittedRunContract` provides it. The exact AG-02 request must still equal `agent_requested_effects` from SKILL-3C1, *and* must be a subset of the signed ceiling. Non-default requests without an independently specified ceiling refuse before any journal mutation. The old no-request/no-ceiling format remains bitwise compatible.

The reusable original journal helper performs conservative narrowing of:
- `repository_access` (`read_only` cannot allow writes);
- model `recipients` (requested names must be a subset);
- named tool capabilities (requested programs must be a subset);
- network egress (no ambient unrestricted host; no cross-mode conversion; CIDR subnet/protocol/port and exact hostname/protocol/subset port rules).

The existing `AGENT_SOURCE_PREWORK_RESERVED` event adds a domain-separated `effect_limits_identity` when a ceiling exists, and the original journal readback recomputes it from the authenticated, immutable Run contract. No raw ceiling, separate SQLite table, authority owner, source read, signer, ledger, executor, LLM gateway, or native backend is added.

## Boundary and limitations

This is a **verified policy ceiling**, NOT a runtime grant. The existing event still says `authority=PREWORK_NOT_ADMITTED`, `effect_permission=NONE`. Even a positive match does not enable Agent RESOURCE_INTENT, a tool launch, repository writes, model disclosure or egress. Original `FleetPlanResolver` explicitly refuses Agent execution, and original LACE `RESOURCE_INTENT` retains its Agent-specific refusal. Source prework is not physical containment evidence.

`agent_effect_limits` must eventually be populated by the original native issuer from an independently owned and approved Enforcer policy, NOT echoed from authoring or user YAML. This checkpoint implements its original signed admission comparison only; it does **not** prove actual production issuer policy provisioning. The test fixture uses independently chosen values to prove both positive and negative cases; it is not a production issuer.

## Still required for SKILL-3C2

1. Define and wire the policy owner/provider to the original issuer, and prove separation from user-authored Agent requests.
2. Issue an original Enforcer scoped effect grant bound to exact Run, Step, attempt, control epoch, signer trust generation and executor generation after the proper physical preconditions; do not treat a ceiling identity as a grant.
3. Enforce every repository write, tool invocation, recipient disclosure, and network effect through the existing Step executor and LACE effect cuts; cancel, revoke and restart must invalidate grants; teardown and physical custody must be verified.
4. Qualify on the target Linux/WSL host and retain real APP PASS and release gates.

An untrusted Agent Skill does not inherit trusted-local TL-1 permissions. No Hashmarks runtime sensing or duplicate agent control loop belongs here.
