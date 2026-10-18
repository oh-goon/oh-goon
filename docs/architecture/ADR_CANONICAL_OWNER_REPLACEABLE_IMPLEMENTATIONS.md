# ADR — Canonical Owner, Replaceable Mechanisms and One Admitted Lifecycle

**Status:** Adopted as the CO-R0/CO-R1 development architecture contract (2026-10-15). **No runtime migration, alternative backend, security certification or release gate promotion.**

**Original campaign context:** exact DB6 source `9e988b7e9f398cc9a2d3db40991f29456f74ab19f8fed81ea00011caee15f35d`, with `OH_GOON_FORWARD_TODO.md` as that archive's sole forward roadmap. The archive roadmap is not required by a GitHub clone. This ADR retains the design constraints; its source-specific findings and qualification status below are historical, not the current work queue. It is not a second owner, scheduler, recovery engine or admission authority. The [owner and ingress inventory](CO_R01_CANONICAL_OWNER_CENSUS.md) records the reviewed boundary.

## Decision

One *semantic owner* owns each complete domain operation: frozen request and caller identity; admission and preconditions; authoritative transition/transaction or effect intent; causal event ordering; outcome/teardown; and idempotent authorized recovery. A replaceable **mechanism** may implement a typed, versioned, admitted capability only under the same owner/orchestrator. No provider may choose policy, reauthorize after admission, sign authoritative events, publish LACE truth, classify Run terminal outcomes, or invent a second recovery route.

**Implementation ports are not a goal.** Reuse the existing class/function and transaction when they already implement a complete operation. Do not wrap `LocalDatabase`, schedule admission, the original Fleet lifecycle, `RepositoryWorkspaceAuthority`, `ExecutionEvidenceAcceptanceAuthority`, the physical LACE owner or repository-effect settlement merely to create a uniform interface. Ports are justified only by a real replaceability axis with capability identity, compatibility and conformance proofs; don't implement hypothetical backends.

## Normative lifecycle contract (effectful domain operations)

The owning module or a small typed companion record must make these fields unambiguous for *every reachable* effectful phase (only fields relevant to its domain are required):

1. `domain_operation`, schema/version, canonical owner, frozen semantic request and entrypoints;
2. Plan/source/Run/Step/attempt/generation/lease/target/backend/capability identity and monotonic authority;
3. exact admission checks and source-bound durable pre-effect intent, transaction owner, CAS/revision ordering;
4. external effects vs SQLite facts, commit-before-ACK/partial-effect/commit-unknown distinctions;
5. cancellation, timeout, crash, revocation, lost ACK and concurrent writer precedence;
6. observation and physical evidence provenance, teardown actor, quiescence standard, unresolved escalation;
7. retry token, identity-bound durable reread, recovery authorization, cross-run custody and terminal-state source;
8. negative X0 bypass mutant, deterministic crash/restart cut and provider-native evidence gate.

**Resolve once; transport once; execute once; report once.** Fail before effects if identity, capability, owner, lineage or frozen selection is missing, incompatible or stale. Authority only narrows within a generation. A fresh generation/attempt requires new explicit admission. SQLite commits do **not** make FS/Git/kernel/network atomic: external mutations require original-owner durable intent, fenced effect, independent observation and authoritative settlement. `UNKNOWN/UNPROVEN` is not success, failure-with-absence or permission to blindly retry.

Don't invent redundant state enum values when the original journal/intent plus evidence already distinguishes the outcome. Semantically relevant cuts may be: `NOT_ADMITTED → INTENT_DURABLE → EFFECT_ATTEMPTED → OBSERVED / UNRESOLVED → SETTLED → TEARDOWN_PROVEN`, with `RECOVERY_REQUIRED` on ambiguous custody. Existing Enforcer terminal precedence is frozen: `CONTAINMENT_FAILED > ENFORCEMENT_TERMINATED > EXECUTION_ABORTED > EXECUTION_FAILED > EXECUTION_COMPLETED`. The Enforcer decides outcomes; independent LACE observes physical facts; Game Tape only projects them. Provider cleanup/ACK does not prove physical absence.

**Timing:** expiry must be read within the authoritative write cut **after** durable row reads, not on a prior wall-clock precheck. Concurrency decisions follow the original CAS and journal event order. Do not build a second schedule transaction, alternate signer, shadow journal or automatic degraded backend.

## Historical review: source boundaries and lifecycle failures

- **CO-R2b first runtime bundle (P0):** `RepositoryWorkspaceAuthority.allocate`, `materialize_read_snapshot`, `materialize_worktree`, `release` currently cross SQLite and FS/Git failure cuts. A postcommit verification exception can trigger cleanup of published READY data; an orphan effect can survive before SQLite commit. Introduce only original-owner staged custody/intent, owned-path proof, recovery and quiescence; never a second filesystem manager. Tests `T-WS-1..9`.
- **CO-R2a (P0):** schedule child admission via `_runs.start_bound` and subsequent occurrence CAS are separate commits. Deterministic child ID and rechecks are safeguards but do not prove cancellation/kill races. Reproduce `T-SCH-1..4` before changing code; use the existing Fleet lifecycle/transaction if a fix is required, no new worker queue.
- **CO-R2c (P1):** `ExecutionEvidenceAcceptanceAuthority.accept` checks durable lease status/identity in its write cut but lacks an independent `expires_at` decision there. Adapt the existing lease timestamp rule; tests `T-EV-1..4`.
- **CO-R3 (P1):** exact backend selection exists; native applied-without-ACK, revoked effect, kernel survivor, changed capability/configuration, remote unknown schema need provider-specific proof `T-BE-1..5`. No physical certification from hosted mocks.
- **CO-R4 (deferred):** allow alternative sandbox implementation only after semantic/physical conformance. PostgreSQL or another queue/storage engine would be a separate entire durability migration; RocksDB/Scout may be disposable indexes, never operational truth. Never silently fall back or dual-write.

## Enforcement and limitations

CO-R01 integrates AST call-edge checks into the **existing** `oh-goon-architecture` X0 checker, protecting original schedule/Fleet, REST ingress, workspace pre-publication verification, evidence acceptance write cut, and admitted process-backend selection. The existing DB0 and physical observer checks continue to own their own rules. Mutation tests must fail if a protected owner is bypassed. This guard is a bounded source topology check, **not** proof that the reviewed lifecycle gaps above are fixed, nor coverage of arbitrarily dynamic Python dispatch. CO-R2/CO-R3 must prove durable post-crash and native outcomes on an exact frozen source.

The original `make qualify-development-wsl`/QENV1 remains the sole native qualifier. DB6 ENOSPC witness remains opt-in. Native Bubblewrap, disk EIO/fsync/power-loss, independent LACE Gate B/P3C, external signer Gate A, Gate C, AG-03/04, APP PASS and release are **BLOCKED/NOT RUN**, regardless of CO-R01 hosted PASS.

**Archive roadmap integrity:** `check_co_r01_roadmap_integrity` remains an explicit check of retained archive metadata. Ordinary repository architecture checks do not invoke it or require historical TODOs. It validates the original campaign record and does not define execution or release authority.
