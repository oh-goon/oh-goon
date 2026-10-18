# ADR — SKILL-3C1 Original signed Agent Step demand correlation

Status: DEVELOPMENT ONLY / no executable Agent Step authorization.

## Problem

SKILL-3B binds typed `requested_effects` to the AG-02 frozen delivery and its handoff identity. Original native Fleet Enforcer admission uses a signed `AdmittedRunContract` with one Step envelope, and the original `CausalRunJournal` reserves AG-02 prework. Without an exact correlation, source demand and a separately signed Run envelope can diverge silently. A signed Run receipt is not authorization for tools, model disclosure, repository mutation or network access.

## Decision

Use only the original `reserve_verified_agent_source_in` service decoder and the existing `CausalRunJournal.reserve_agent_source_prework_in` Fleet journal writer. The verified handoff conveys its immutable non-default `AgentStepRequestedEffects` demand as a canonical JSON tree. Original signed `step_envelopes[step_id]` must carry exactly matching `agent_requested_effects`. If no non-default demand was authored, that envelope field must be absent. Ambiguous or conflicting binding refuses **before** event publication.

The original `AGENT_SOURCE_PREWORK_RESERVED` signed event records `requested_effects_identity` only for a non-default request, using `digest("oh-goon/agent-requested-effects/v1", canonical_bytes(requested_effects))`; it never stores a new grant or the raw request tree. The original `CausalRunJournal.verify` independently compares this digest against the same authenticated immutable Run contract and checks the original handoff identity, including after restart. Signature authentication remains under the **existing** `agent-source-prework` issuer. No new trust root, table, signer, permission registry or executor is created.

A demand is *not* an independent Enforcer grant. The signed prework event retains `authority=PREWORK_NOT_ADMITTED` and `effect_permission=NONE`. The `RESOURCE_INTENT` Agent mount fence, original Resolver's production Agent Step refusal, LACE physical effect/teardown gates, and the Step executor remain unchanged. The historical absent-demand packet and event shapes remain unchanged.

## Testable invariants

- The AG-02 packet is decoded exactly once in its original service. A different frozen source/demand changes the handoff identity; original Step identity, active lease, Run, signer trust and executor generation are still checked at the same Fleet transaction.
- A Run envelope missing or differing in repository scope, recipients, tools or egress refuses before `AGENT_SOURCE_PREWORK_RESERVED` is published; a default-demand packet cannot smuggle a non-default signed envelope.
- `verify` refuses a contradictory event even with a recomputed hash chain and valid *test issuer* event MAC. Only the originally admitted contract determines source-demand consistency.
- No information from these events becomes an effect-time authorization or a new path into host subprocesses, LLMs, workspace writers or egress.

## Still required

SKILL-3C2: separately prove genuine **independently issued** Enforcer effect grants under original Run/Step/epoch/generation scope; require grant-subset-of-request and enforce precise effects at the original Step/LACE physical cut, including revoked/rotated authority, cancellation/restart, teardown, tool executable identity and recipient disclosure. SKILL-3C1 does not qualify real Linux/WSL containment, APP PASS, native Gate A/B/C or production Agent execution. The deterministic trusted-local TL-1 exception never applies to untrusted Agents.
