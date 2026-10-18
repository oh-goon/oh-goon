# ADR — Trust Authority Minimalism and One Canonical Trust Policy

**Status:** Proposed, design only — pending T0 census and source-backed review  
**Date:** 2026-10-13  
**Parent:** B3B3B4 exact source; this decision does not modify that source or change Gate A/B/C status.  
**Existing ADR target:** `docs/architecture/ADR_TRUST_AUTHORITY_MINIMALISM.md` (do not create a competing trust ADR).

## Decision

**The Trust Authority Registry is the sole normative definition of *which* authorities and signed-event domains OH-Goon accepts. The reviewed Trust Authority Census is a generated/audited view of this registry and externally observed key custody — not another policy document or a runtime authority. The existing canonical Enforcer trust resolver is the only runtime component that turns authenticated trust policy into verification decisions.**

Do not give the census its own signing key, endpoint, approval queue, admission decision, revocation authority, or independent trust validation path. Do not centralize distinct private-key custodians into the verifier process. The source of trust *policy* is single; the cryptographically and operationally independent authorities whose claims it checks remain separate.

> Define trust once. Resolve using one canonical verification path. Enforce through the existing semantic owner. Fail before effects when policy, proof, binding, or custody cannot be established.

## Architecture

```text
Versioned normative Trust Authority Registry (policy; no private keys)
  ├─ reviewed Trust Authority Census (generated projection + deployment evidence)
  ├─ CI X0 authority/domain/entrypoint inventory and negative mutants
  └─ authenticated immutable TrustAuthoritySet (compiled policy snapshot)
        │
        ▼
Existing Enforcer trust resolver / signed-envelope verification owner
        ├─ verify authority / role / domain / signature / binding / epoch
        ├─ verify current revocation / fencing at every effect boundary
        └─ return a typed, policy-bound VerifiedAuthorityContext
                  │
            ┌─────┴───────────────────────────┐
            ▼                                 ▼
       Enforcer                              LACE
  admission, RESOURCE_INTENT,          physical custody,
  outcome, terminal seal               physical facts, teardown
            └────────────────┬──────────────┘
                             ▼
                     Game Tape projection
                (read-only verification, no new authority)
```

The canonical policy store's physical path and serialization are chosen during T0 by inventorying current trust configuration; `trust-authorities.yml` is an *illustrative name*, not a mandate to create a second config. A single policy is published with an immutable digest, schema/version, signed deployment provenance and explicit migration semantics. Bootstrap/provisioning trust is independently anchored: a policy file may not claim authority merely by declaring itself trusted.

## Trust policy record — minimum semantics

Each authority entry contains: stable authority ID; semantic owner; permitted signature domain(s) and statement schema(s); key/verification-root identifiers or references (never private key material); independently controlled custodian/process/UID and externally corroborated custody evidence; compromise boundary and threat rationale; issuing/provisioning chain; policy revision; validity/rotation/revocation reference; allowed epochs and generation fencing; event binding schema; and one authoritative verifier implementation/entrypoint. The registry includes an explicit policy for rejected/unsupported domains.

Every accepted event domain must have **exactly one** legitimate semantic owner; several domains may share one cryptographic signer if its authority genuinely encompasses them. A different event type, Step, Run, Skill, Go-on or agent workload does not by itself justify another key. External provider attestations remain facts about remote resources, never Fleet admission authority.

The runtime compilation into `TrustAuthoritySet` must verify provenance, policy digest, bootstrap trust, deployment material, current revocation source, and role/domain completeness before admitting work. An admitted Run binds the trust policy digest and signer epoch/generation. Trust meaning is not re-resolved by every consumer, but revocation and lease/generation status MUST be rechecked at the relevant authority/effect boundary. Never reuse a stale `verified=true` boolean as a transferable authorization token.

## Shared trust operations versus semantic decisions

Expose **one existing trust-verifier port** (or extend its existing interface), with narrow behavior such as `verify_envelope`, `verify_event_domain`, `verify_authority_binding`, and `verify_current_epoch`. It returns an immutable typed `VerifiedAuthorityContext` tied to the exact event/Run/Step/attempt/policy digest/epoch/executor generation, with no hidden authority escalation. The API does **not** decide RUN_ADMITTED, CONTAINMENT_FAILED, QUIESCENCE_PROVEN, TEARDOWN_PROVEN or EXECUTION_COMPLETED.

Enforcer remains the only admission / RESOURCE_INTENT / outcome / terminal-seal semantic authority. LACE owns physical containment, original resource claims and observed facts, not admission or terminal classification. The independent physical observer attests observations only if its custody is actually separate. Game Tape and diagnostics consume already owned facts and may verify signatures read-only; they may not create trust, physical facts, or outcomes. Signatures and private-key operations are performed only by their legitimate, separately custodied issuers. No Agent-specific trust verifier, key root or execution chain.

## Transition and migration safety

1. **T0 (design/census):** source-code and deployment inventory, match every accepted root, key and event domain against one machine-readable registry definition. Generate human census and show omissions/drift. Decide KEEP/MERGE/EXTERNAL/REMOVE with four-part justification. Produce the policy schema and this approved ADR, but **do not relax the current six-root gate**.
2. **T1 (exclusive migration):** compile the authenticated registry into the *existing* Enforcer trust-resolution path. Convert old config at a pre-admission migration boundary only; old and new runtime verification cannot both admit Runs. Failure to verify schema, policy provenance or all required root roles is fatal before work. Preserve active/revoked epochs, original signed admission, replay/cancellation fences and durable root custody.
3. **T2 (real Gate A):** independently provision and exercise each *justified* surviving custodian, including revoke, restart, rotated epoch, stale attempts, cross-ingress and mixed-policy attacks. No Gate A PASS solely from policy file, hosted tests or number of roots.

## Permanent anti-drift gate: X0

CI must reject at least these mutations:

- A signer/root or accepted event domain used by code is missing from the canonical registry, or a registry domain is not reachable via the single authorized verifier.
- Two authorities claim the same semantic domain without an explicit, independently justified boundary; an event-specific key is added solely for different field names.
- A module adds independent root lookup, signature policy parsing, environment trust override, local trust cache, or another verifier/Run admission route.
- A policy is changed without schema/version/digest transition; a Run or Step uses a foreign/mixed policy digest, stale signer epoch, or revoked authority.
- Compatibility fallback accepts both old and new trust models at runtime; recovery/diagnostic/Game Tape promotes an unverified fact to authority.
- Test, CLI, CI, agent, local and remote adapters disagree on which signer/domain is accepted, or LACE silently accepts caller-selected authority instead of the original signed event.
- A registry definition claims independent physical custody but deployment evidence shows a shared mutable UID/process and no independent principal.

Positive tests must allow domain-separated envelopes from **one legitimate signer** when their semantics are within its scope. Negative tests must exercise the live call edge and fail-before-work behavior, not merely match symbol names. Compare the generated census against source discovery and real deployment inventory; CI may prove source completeness, while independent deployment evidence is required for Gate A.

## Acceptance / non-goals

**T0 complete** only when every currently accepted signer, root, signature verification entrypoint and event domain is inventoried; the single registry/policy schema and generated reviewed census agree with source and deployment evidence; the minimal set is justified; and X0 guards are enrolled. **T0 does not change runtime trust decisions.**

**T1 complete** only when every supported workload and ingress consumes the one authenticated `TrustAuthoritySet` via the existing verifier and no parallel runtime path remains. **T2 complete** only with independently provisioned native Gate A evidence.

This ADR is not an instruction to collapse private-key custody, make LACE a signer of Enforcer decisions, or turn a YAML file into an authority. It does not authorize Agent effects, bypass native containment, or promote any release gate.
