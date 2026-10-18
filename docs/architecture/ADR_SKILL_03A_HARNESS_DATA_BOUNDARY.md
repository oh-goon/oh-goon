# ADR — SKILL-3A: harness data adaptation is not execution admission

## Problem

SKILL-2 produces complete, immutable AG-02 prework packets and original Step
identity, but no harness-facing input/result protocol exists. Adapters must not
reopen source paths, create another source transport, or treat an LLM's freeform
text as execution success. Full Agent runtime remains gated.

## Decision

Provide a **pure services-layer adapter**, `agent_harness_contract`, which
consumes AG-02 `FrozenAgentStepHandoff.decode_prework_delivery` and exposes the
original frozen members and typed Step task for later contained harness use.
The original source capture is the sole authority for source bytes and identity.
No repository read, Git fetch or network is performed by the adapter. The
returned data is marked PREWORK_NOT_ADMITTED and must not be disclosed to a
model without the original separately admitted recipient authorization.

A harness submission has a bounded canonical JSON shape, an exact handoff
identity, and a clearly **UNTRUSTED_HARNESS_CLAIM** classification. Its
`CLEAN` string is not a verified terminal result; the original executor,
qualification and Game Tape authorities must independently assess results and
effects. Tool/authority fields are not valid result fields.

## Unchanged authority

Resolver → original Fleet/Enforcer admission → LACE → existing Step executor →
original journal → Game Tape. The resolver's existing Agent Step refusal remains.
A contained backend, per-tool/per-recipient admission and actual execution/teardown
facts must precede real Agent Step enablement. Deterministic trusted-local TL-1
is not a substitute for these requirements.

## Tests

Offline decode after source removal, exact task and skill resource projection,
identity tampering, clean/findings/inconclusive distinctions, forged tool or
permission fields, duplicate JSON keys, over-budget and malformed submissions,
unchanged AG-01/02 and SKILL-1/2 source tests, architecture checks.
