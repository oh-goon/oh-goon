# Glossary

**OperationReport** — immutable canonical result of one repository operation.

**Journal** — append-only, hash-linked durable owner of canonical operation and Fleet orchestration facts.

**JournalReadView** — immutable, integrity-verified read cut pinned to a Journal revision.

**LocalStateReadView** — one SQLite-consistent cut containing scope plus Journal read evidence.

**CapabilityDefinition** — immutable domain policy for capability identity, targeting, requirements, safety and invalidation.

**CapabilitySnapshot** — durable admitted capability policy carried by historical work; historical readers never reinterpret it through today’s registry.

**RepositoryEffectAttempt** — the one durable unresolved repository-effect lifecycle for Direct and Fleet execution. Origin is provenance only.

**RepositoryEffectExecution** — the single production state machine for Direct/Fleet physical repository effects; Fleet scheduling is separate.

**ExecutionCoordinator** — process-facing admission/cancellation owner over the SQLite execution lease.

**ExecutionSession.mutate** — owner-atomic write boundary: verify the exact lease and perform the durable mutation in the same SQLite transaction.

**ExecutionSession.finish** — owner-atomic closure boundary: perform the final durable settlement and release the exact lease in one transaction (Direct settlement or Fleet terminalization).

**FleetRun** — orchestration aggregate: admitted targets/capability, start, cancellation, canonical completions and terminal outcome. It does not own repository-effect recovery state.

**Runtime Safety Cut** — one transactional observation of durable revision, Journal, execution lease, generic effect attempts and active Schedules.

**RuntimeSafetyProof** — revision-bound assessment derived from one Runtime Safety Cut; a later durable mutation makes the older proof stale.

**Projection** — disposable derived state; never another durable authority.

**ApplicationRuntime** — single public process composition/lifecycle root for durable owners, services, recovery and managed background lifecycle; concrete construction wiring is kept in private `_application_*.py` modules.

**ScheduleScheduler** — runtime-owned periodic clock for repository Schedules. It only invokes Schedule ticks; Schedule semantics remain in `ScheduleService` and canonical admission/execution owners.

**ErrorKind** — transport-independent semantic error category carried through backend/API/frontend.
