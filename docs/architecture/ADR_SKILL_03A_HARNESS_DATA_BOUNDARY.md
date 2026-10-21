# ADR — SKILL-3A: harness data adaptation is not execution admission

## Current interpretation

This is the retained design record for the original prework adapter. Its passive
implementation has moved once to
[core/agent_harness_contract.py](../../backend/src/oh_goon/core/agent_harness_contract.py)
and its input/submission contract has been extended through A2. There is no
services-layer facade. Use the
[Agent runtime ownership inventory](../development/AGENT_RUNTIME_OWNERS.md#harness-data-contract-a2)
for that contract and its integration limits.

The original services location and runtime-refusal assumptions below are
historical. Built-in standard-local Agent execution remains supported. Native
Agent Steps now consume the extended contract through the original Fleet/Step
lifecycle and retain its typed result acceptors. Neither prework preparation nor a harness submission grants execution,
disclosure or verification authority. Ordinary local execution does not acquire
the historical strict/native prerequisites below.

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
