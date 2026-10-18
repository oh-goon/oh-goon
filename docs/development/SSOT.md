# Sources of truth

oh-goon persists facts and unresolved coordination only. Read models are rebuilt from one pinned durable cut.

## Durable authorities

- **Journal** — append-only, hash-linked canonical `OperationReport`, Fleet orchestration, effect reconciliation, and acknowledgement history.
- **RepositoryEffectAttemptStore** — unresolved external-effect lifecycle for Direct/Fleet; origin is provenance only.
- **SQLite execution lease** — exact cross-process execution authority; a live owner is never time-expired.
- **WorkflowDefinitionStore** — saved definitions with immutable generation + monotonic version; replace/delete are generation-and-version-bound replay-safe commands.
- **WorkflowExecutionStore** — structural workflow lifecycle/cursor/CAS; step FleetRun IDs are derived.
- **LocalState** — repository scope/exclusions and durable read-cut boundary.
- **Local configuration files** — `league.yml` owns saved League folders and `plans/<plan-key>/plan.yml` owns saved Shared Plan source bytes. These preferences live outside disposable SQLite; they are not execution evidence.

## Owner-atomic writes

`ExecutionSession.mutate` re-proves the exact lease in the same SQLite transaction as an intermediate ownership-critical mutation such as `EFFECT_STARTED` or Fleet target canonical settlement.

`ExecutionSession.finish` re-proves the exact lease, performs the final durable settlement, and deletes that lease in one transaction. Direct success/interruption and Fleet terminalization use this closure primitive.

`FleetRunLifecycle` is the sole service allowed to coordinate live Fleet lease authority with Journal lifecycle facts. `FleetRunControlPlane` and `FleetRunExecution` delegate rather than forming their own two-owner transaction sequences.

There is no generic `release`/`abandon` cleanup for admitted work. A process crash leaves durable evidence for recovery.

`CapabilityExecutor` performs no durable mutation.

## Journal projection SSOT

`JournalFactStore.read_view(connection)` creates the verified `JournalReadView` / `JournalIndex` used by normal reads **and write-time validation**. `JournalReadCache` stores key + view in one immutable cache entry so concurrent publication cannot tear the verified cut. `JournalIndex` itself publishes tuple-backed sequences and read-only maps, so sharing a cached verified cut cannot let one reader mutate another reader's projection. Do not add a second raw-entry projection module, mutable projection table, or independently mutable cache key/view.

## Immutability ownership

SSOT values are immutable once published: admitted intents, lifecycle states/facts, verified read cuts, product projections, registry bindings, and policy/configuration tables. Private builders and resource/session owners may mutate while constructing or executing work, but that mutable state is not another published authority. Arbitrary external JSON remains an isolated copied leaf; do not create a second frozen-JSON domain hierarchy or remove its Journal copy boundary.

## Derived state

`FleetSnapshot`, Fleet progress, quarantine, health, freshness, explanations, recovery views, and frontend cached reads are projections. They are never durable authority. Fleet progress invalidates canonical `ReadResource` scopes through `ReadController`; do not add a second Fleet-specific refresh revision/context.

`FleetRunState` is structural at the model **and reducer** level; no nullable lifecycle fold or parallel transition table is authoritative.

## Recovery SSOT

`repository_effect_attempts` is coordination state, not history. It contains unresolved work only. Pre-effect claims are retired when proven not started; normal success is retired with canonical operation evidence; recovery classifications/acknowledgements are Journal facts.

## Fleet plan SSOT

A run has exactly one selected source before resolution. A repository-owned run uses that repository's exact root `goon.yml`; a Shared Run uses one saved `plans/<plan-key>/plan.yml` plus explicit current repository IDs. These source modes are mutually exclusive. Shared Run target selection operates on managed Git repository identity and must not read, validate, merge, inherit, or fall back to repository `goon.yml` files. Both sources then enter the same `FleetPlanService` / execution boundary; the admitted run records the selected source kind and SHA-256. Root and canonical shipped YAML use one exact-byte source snapshot so path, decoded text, parsed contract, and digest cannot come from separate filesystem reads. Template README files remain human documentation and never participate in that machine-source identity.

Source YAML and extension TOML are authoring inputs, not runtime state. Browser Plan authoring has one saved-Plan owner under **Plans**. Shared Runs and Schedules only consume saved Plans, while internal path-based Plan tooling resolves source through `FleetPlanService`/`FleetPlanResolver`. There is no second `/plans/*` HTTP editor/workspace owner. `ResolvedFleetPlan` remains the admitted execution SSOT; once admitted, execution never reopens authoring files to reinterpret intent.

### Local Lineups are selection preferences, not Team state

A saved Lineup contains exact local Team roots chosen by the user. It is not authoritative for whether those Teams still exist. On preview/run, roots are resolved again against the current filesystem-derived League; unavailable roots fail visibly. Lineups never contain repository `goon.yml` state or plan bytes and never grant execution authority by themselves.

## Development continuation SSOT

Long-running development/recovery campaigns are not continued from chat history, model memory, `/mnt/data`, or a mutable container/workspace. `RECOVERY_HEAD.json` is the atomic pointer to a publication receipt that digest-binds the immutable source archive, optional structured proof, and immutable machine-readable continuation state. Recovery—not the caller—fills observed source/archive/generation/proof authority into that continuation state. The record owns current authority, completed phases, open phases, proof receipts, qualification state, and next executable phase. A Markdown handoff is generated from verified committed recovery state, is a human projection only, and is stale if it disagrees. See [`DURABLE_CONTINUATION.md`](DURABLE_CONTINUATION.md).
