# Safety model

oh-goon is local-first but may have overlapping GUI/CLI processes, so safety is cross-process and durable.

## Guards and the race each closes

| Guard | Prevents |
|---|---|
| Runtime Safety Cut | torn observation across Journal, lease, effect-attempt and workflow tables |
| `durable_revision` admission CAS | mutation after proof but before durable admission |
| atomic Direct lease + generic effect claim | owner without recoverable pre-effect intent |
| atomic Fleet lease + `RUN_ADMITTED` | execution owner without a durable FleetRun |
| repository effect fence | worktree/cancellation change between claim and external effect |
| `ExecutionSession.mutate` | stale owner writing `EFFECT_STARTED` or canonical success |
| `FleetRunLifecycle` + `ExecutionSession.finish` | Fleet admission/cancel/terminal Journal facts splitting from lease authority, including lost post-commit responses |
| `RepositoryEffectRecovery` | blind replay after an external effect may already have started |

A **live execution owner is never time-expired**. Ownership is not a TTL. Automatic reclamation requires positive evidence that the recorded process is not alive; ambiguous liveness fails closed.

## One effect lifecycle

Direct and Fleet work use the same `RepositoryEffectAttempt` states. Direct/Fleet is origin provenance, not a persistence or recovery choice. The generic lifecycle is `CLAIMED -> EFFECT_STARTED -> canonical commit/retirement` or, after uncertainty, evidence reconciliation.

Normal success leaves only the canonical `OperationReport`; the effect coordination row is retired in the same owner-checked transaction. Quarantine is derived from unresolved generic attempts, never from a second table or a Fleet-specific attempt projection.

## Recovery rules

- Do not claim exactly-once external effects.
- Do not automatically replay an effect that crossed `EFFECT_STARTED` without canonical evidence.
- Do not automatically rollback uncertain repository state.
- Reconcile from the captured baseline before manual acknowledgement.
- Partial/indeterminate evidence remains quarantined until explicit inspection/acknowledgement.
- Keep `SystemInvariantAuditor` read-only; do not add a generic force-repair engine.

Startup recovery completes before the runtime safety proof is recorded. A **revision-bound safety proof** is bound to one **runtime safety cut**; a **stale proof** must be refreshed before mutation.

## Revision semantics and scheduling

`durable_revision` advances for every write transaction that actually mutates durable rows, not merely for entering a write transaction; there is no production revision-bypass mode. Every non-committed transaction exit explicitly rolls back even for `BaseException`. Managed include/exclude commands bind their eligibility observation to that revision and build their response without post-commit filesystem rediscovery. Scheduled workflow admission captures definition + latest execution + due decision + safety cut together and CASes that exact revision. The workflow scheduler stays alive for the process lifetime; a degraded tick may settle already-completed workflow evidence but returns before definition scheduling or any new effect admission. Operator-required repository-effect quarantine is stable across restart; sequential or concurrent reconcile/acknowledge retries converge on one immutable Journal command result.
