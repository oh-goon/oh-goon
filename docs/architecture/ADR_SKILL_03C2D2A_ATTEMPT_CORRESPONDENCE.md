# ADR: SKILL-3C2D2A — Bind native Agent tool inspection to original operational attempt

## Problem

SKILL-3C2D1 inspected a proposed read-only native tool command against an authenticated Run contract, AG-02 demand, separate Enforcer ceiling, and signed source/stage records. However, the staged AG-02 receipt could refer to a different Step attempt from the operational attempt that produced the tool command. The check also accepted older signed stages with no signed mount proposal. Neither gap can be upgraded into a future effect permit.

## Decision

Preserve the ONE existing Fleet execution and journal path. During `OriginalFleetLocalLaceReservation.reserve`, within the existing `ExecutionSession.mutate` transaction, the original `FleetRunLifecycle` Run-event reader validates exactly one `StepAttemptCreatedEvent` for the current Step and attempt number. It verifies canonical operation lineage, the canonical `fleet_step_attempt_id`, attempt-root Step/number ancestry and absence of later terminal events, then returns the domain-separated original Agent attempt digest.

The original causal journal's existing tool inspection now requires (a) this operational attempt digest to exactly equal the AG-02 reservation and stage attempt identities, and (b) the stage's original signed read-only source-mount proposal to point to the exact current attempt-private `agent-source-prework` root and target. The check is *pre-effect*, makes no journal mutation and returns no capability or permit. The existing Agent `RESOURCE_INTENT` denial remains mandatory.

No second Run-event database reader, signing authority, policy registry, tool launcher, or effect-intent writer is created. The Fleet lifecycle supplies a fact-reading callback; the existing Fleet session owns transaction and cancellation, and CausalRunJournal owns authenticated prework interpretation.

## Rejection cases

Absent or ambiguous Step attempt events; invalid canonical operation ID or attempt identity; terminal Attempt/Run; malformed/mismatched attempt root; missing original attempt callback; stale or foreign signed source/stage identity; missing or foreign signed source mount; invalid tool request; revoked signer, cancellation or changed lease. Refuse before native allocation. Old read-only source receipts without mount proposals remain verifiable history but are not executable authority.

## Qualification boundary

This is **not** a positive Enforcer grant and is **not** a physical binding, native mount observation, tool-process containment, model disclosure, network egress or write effect. Those remain mandatory, independently testable follow-on SKILL-3C2D2 gates. Hosted tests cannot prove WSL kernel custody.
