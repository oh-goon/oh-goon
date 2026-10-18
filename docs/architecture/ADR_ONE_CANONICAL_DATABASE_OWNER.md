# ADR — One Canonical Database Owner

**Status:** ACCEPTED ARCHITECTURE; DB0 and DB1A implemented in development; DB1B–DB4 OPEN  
**Parent:** OH-Goon P3B2B source `57cd625376e49e8f8535a7630963d645bb4ede4530164234ec15f2c996927405`  
**Target repository location:** `docs/architecture/ADR_ONE_CANONICAL_DATABASE_OWNER.md`  
**Decision owner:** OH-Goon architecture and runtime/persistence owners

## Context

OH-Goon already uses `runtime.database.LocalDatabase` as the canonical operational SQLite authority, with atomic Run Journal and lease updates, SQLite identity and durability policies, and bounded observation. Optional Enforcer/LACE protocol and signer-epoch classes include transaction-starting helpers and same-connection `*_in` participants. A disposable Scout SQLite sidecar deliberately has an independent analytical write lock and no operational authority. We must close potential future ownership drift without creating a new subsystem or weakening already-proven constraints.

## Decision

> **OH-Goon owns what must be persisted and the transactional guarantees. The database engine owns how those guarantees are implemented. No other component independently decides either.**

1. `LocalDatabase` remains the sole operational database owner and sole begin/commit/rollback/public connection owner. Private SQLite helpers belong to it; do not create another public transaction coordinator.
2. Domain owners define durable facts, events, invariant and concurrency semantics. They can mutate within one explicitly supplied transaction but cannot choose engine mechanics or start another operational transaction.
3. A single frozen operational engine is selected and admitted per deployment generation. The current supported engine is SQLite. Engine mechanism is not business meaning: SQLite owns locking/journaling/crash recovery; OH-Goon declares and verifies durability/atomicity/isolation/identity requirements.
4. The original journal/lease/signer-epoch/causal admission facts requiring one atomic cut use one same-connection transaction. Existing in-transaction participants must be preferred; standalone wrappers are migrated or explicitly prohibited from acting as production runtime authorities.
5. The existing `_sqlite_authority_gate` remains a private host-coordination safety guard and is not a second durable commit engine. Change it only with validated parity tests.
6. Scout's independent physical sidecar is allowed **only** as disposable derived analytics; no unique authoritative facts, Run/fencing decisions, or canonical revisions may originate there. Other caches and Game Tape are similarly non-authoritative.
7. External effects (Bubblewrap, filesystem, Git, processes, remote calls) are outside DB atomicity. Existing Enforcer/LACE pre-effect intent, fencing, reconciliation, and physical evidence gates remain authoritative.
8. If the selected engine cannot satisfy the admitted requirements, **fail before work**. If an external effect already started and commit/teardown cannot be proven, preserve an unresolved outcome, never invent success.

## Rationale

- Avoids dual connection/transaction/revision owner paths.
- Preserves important existing SQLite path, schema, durability, authority gate and lease fences.
- Prevents a premature ORM or multi-engine abstraction from weakening safety contracts.
- Keeps analytical operations from competing for the operational writer lock.
- Separates database durability from independent physical-containment certification.

## Consequences

- Migrate transaction-owning wrappers of the causal Run journal and signer trust epoch toward in-transaction-only public use; preserve legitimate administrative provisioning with canonical ownership.
- Freeze engine selection and safety capabilities at runtime admission; operational SQL mechanism cannot be chosen by service or API callers.
- Add X0 anti-duplication protection and full adversarial database fault matrix.
- Keep separate engine-specific tests and future adapter certification prerequisites.
- Continue the existing non-release Gate A/B/C, P3C, AG-03/04 and APP PASS evidence paths independently.

## Alternatives rejected

- New `DatabaseManager` above `LocalDatabase`: parallel decision surface without proven value.
- Immediate generic `DatabaseEngine` plugins or ORM: abstraction before second provider contract evidence.
- Replacing SQLite transaction guarantees with home-grown file locks or replay logs: duplicated implementation authority.
- Combining disposable Scout into the canonical DB: defeats analytical write isolation.
- Treating SQLite atomic commit as atomicity across physical external effects: incorrect.

## Acceptance

Use DB0–DB4 in `OH_GOON_ONE_CANONICAL_DATABASE_OWNER_PLAN.md`: exact source ownership census; same-transaction admission/fence rollback proofs; engine/identity failure matrix; independent Scout corruption and writer-lock isolation; locked native/runtime integration tests; source-bound proof. **No production promotion on hosted-only tests.**

## DB0DB1A adoption boundary

One persistence authority. One atomic admission transaction per original operation. One recovery truth. The only operational engine in this checkpoint is SQLite, through `LocalDatabase`. Domain APIs expose named atomic operations, not an interchangeable `execute(sql)` API. The old standalone causal/signer transaction wrappers remain **classified pre-live exceptions** pending DB1B migration. Scout remains a separate disposable projection. No engine fallback or physical-effect atomicity is implied.

## Implementation note — DB1B1 (development)

The existing `LocalDatabase` now provides named SQLite-only signer trust and pre-live causal admission operations, reusing the original domain participants and a per-operation transaction-ending guard. The historical standalone `*_transaction` pre-live wrappers are still present and remain unqualified; DB1B2 must migrate their remaining callers before dropping the legacy classification. This note does not authorize a second Fleet admission route or DB4 release certification. See `ADR_DB1B1_NAMED_SQLITE_DOMAIN_OPERATIONS.md`.
