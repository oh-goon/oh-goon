# ADR — SKILL-3C2C: independent deployment Agent policy document in the original native compiler

Status: IMPLEMENTED, DEVELOPMENT ONLY. No Agent Step authorization or production qualification.

## Problem

SKILL-3C2B stops step-envelope self-authorized Agent effects but deliberately accepts an independently supplied typed `NativeAgentEffectCeilings` value. Typed source separation alone is not proof that deployment policy originates outside Agent/Plan authoring. A caller could simply manufacture the native policy object from the request and ask the original issuer to sign it.

## Decision and single owner

1. The **existing** `fleet_native_run_contract_compiler.py` owns an immutable `DeploymentAgentPolicySource`. Deployment initialization explicitly enrolls one absolute, separately administered policy document using `DeploymentAgentPolicySource.enroll(path, owner_uid=...)`. The document is a canonical UTF-8 JSON mapping from exact Agent Step IDs to original `AgentStepRequestedEffects` ceiling models, encoded using `canonical_bytes`. There is no second registry, alternate Plan loader, signing service, issuer or journal.
2. The source enrollment captures a bounded, canonical **read-once policy value** and stable leaf identity. `DeploymentRunContractPolicy.freeze(..., agent_effect_policy=..., agent_policy_source=...)` requires that this original policy object exactly match the file. The existing native compiler requires this source for any Agent Step; the previously accepted bare typed ceiling is now insufficient.
3. The original native compiler rechecks the *same* source during enrollment read, contract compilation, and immediately before returning the contract. These observations establish **unchanged** custody, not new policy interpretation or a second execution decision. Any mismatch fails before Run admission. The original conservative journal narrowing still decides whether the Agent request is a subset of independently provisioned limits.
4. Linux file observation opens a single bounded regular file descriptor with `O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK`, checks that the inode and metadata remain unchanged, rejects links, symlinks, inappropriate owner, group/other writes, oversized content, or a changed file. The entire ancestor path must be non-symlink, non-group/world-writable and owned by either root or the explicitly pinned policy administrator, not by the signer. The original Fleet provisioner's unprivileged Linux signer identity observation is reused; root/privileged signers cannot claim this split.
5. All existing Run/Step signed fields, the original prework event, signer roots, Fleet lease, Enforcer journal, physical LACE decision and Game Tape remain in their original owners. The new source identity is a *provenance observation*, not a capability or runtime grant. An Agent Step remains refused by original Resolver and LACE until separate runtime qualification.

## Security and compatibility boundaries

- There is no Plan-driven, AG-02-driven, environment-derived or default deployment policy document selection. Deployment control configures the exact path and administrator UID explicitly; no project-local `.goonskills` or Git checkout may supply authority.
- Command-only Plans keep their original behavior. An Agent Plan with a bare typed ceiling but no independently observed deployment source refuses; this is an intentional fail-closed compatibility change to development-only Agent policy compilation.
- Production OS custody **is not certified by unit tests**. Tests emulate the foreign-user owner split while exercising the real fd byte/metadata recheck; a separate test proves unsafe real temp-directory ancestry is refused. Native Linux/WSL observations, mount topology, restart/failover, full issuer paths, APP PASS and release remain outstanding.
- All source observations are inside the existing preparation/compilation path; source tamper before or during preparation aborts with no Run publication. Native admin-controlled concurrent replacement remains a host qualification concern, not a basis for weakening refusal.
- No Agent Step execution, model disclosure, tools, network, repository writes, environment-dependent fallback or nested strict execution is enabled by this policy document. The original `effect_permission=NONE` remains authoritative.

## Acceptance and next authority boundary

Focused tests prove the original native Run contract binds the exact source ceiling, and refuse missing source, malformed documents, stale file identity, same-byte inode replacement, overwrite, unlink, symlink, hardlink, improper owner/permissions and size, and document mutation while issuers observe the Run. Original Agent architecture and adjacent preparation tests pass. Source archive and member digest readback required.

**Next: SKILL-3C2D.** Introduce an original-Enforcer-owned scoped *effect-time* grant/decision API, bound to exact Run, Step, attempt, epoch, trust and executor generations, following existing owner contracts. The original Step executor, recipient/model gateway, repository write and LACE network/tool cuts must consume it and refuse after cancellation or generation change. Until those cuts and physical containment are qualified, leave Agent execution blocked. Do not create a second policy compiler, signing chain or Agent runner.
