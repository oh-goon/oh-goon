# ADR — AGENT-01B3I source-bound patch proposal

## Context

Prior read-only Agent Skill loops inspect captured source but output only prose.
Making the original Agent Step edit a live checkout would bypass frozen source
identity, workspace ownership and normal sequential Step admission. The product
needs a genuine planning artifact before granting any write effect.

## Decision

Introduce one explicit read-only harness `local-model-proposal` reusing the
original four-action Skill loop's AG-02 packet and Fleet supervisor. The pure
`core.agent_model_selection` owner validates a bounded, exact-schema replacement
for a repository member the Step itself read, bound to captured original bytes
by SHA-256. The integration owner emits one canonical versioned JSON result in
normal stdout/Game Tape evidence; it neither applies patches nor opens live
repository paths. Previous harnesses cannot emit such a receipt. There is no
additional patch engine, artifact store, or success classifier.

## Consequences

A successful Agent Step demonstrates only that a well-formed, source-bound
proposal was durably captured. The next development capability must implement
explicit operator write selection, canonical workspace and sequential apply,
ordinary native tests and independent post-apply evaluation. The executing Step
must recheck source/authorization and never treat the model receipt as authority.
This proposal-only milestone does not add security-qualification requirements
for standard trusted Linux/WSL workloads.
