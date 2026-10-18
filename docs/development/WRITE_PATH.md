# Write path

## One repository-effect algorithm

`DirectEffectAdmission` owns Direct command identity, safety/quarantine admission, baseline capture, and atomic direct lease + claim. `RepositoryEffectExecution` is the only production owner of physical Direct/Fleet repository-effect semantics after a claim exists.

For Direct commands, a caller-stable `operation_id` is checked before admission. A matching canonical operation is replayed without effects; a conflicting or unresolved binding fails closed. Once claimed, Direct delegates to `RepositoryEffectExecution.execute_direct_claimed`.

```text
CLAIMED attempt
  -> repository/cancellation fence
  -> ExecutionSession.mutate
       exact lease + EFFECT_STARTED
  -> CapabilityExecutor -> concrete runner
       returned report must match the exact admitted attempt envelope
  -> owner-atomic settlement
       Journal.commit_operation_in(OperationReport)
       + RepositoryEffectAttemptStore.retire_committed_in
```

For a Fleet target, settlement uses `ExecutionSession.mutate` because the outer Fleet lease remains active. For a Direct operation, settlement uses `ExecutionSession.finish` so canonical report + attempt retirement + exact lease release are one transaction.

The critical rule is structural: **runner completion is not commit authority**. There is no check-then-commit wrapper, generic lease cleanup, `abandon()`, or timeout that can steal/release a live admitted owner.

## Fleet repository effect

Fleet admission atomically creates the Fleet execution lease and `RUN_ADMITTED`. `FleetTargetScheduler` chooses serial/parallel scheduling, then delegates every repository to `RepositoryEffectExecution.execute_fleet_target`:

```text
deterministic operation_id
  -> RepositoryEffectAttempt(CLAIMED, origin=FLEET)
  -> shared fence/effect/runner/settlement path
  -> RepositoryCompletedEvent references canonical operation
```

FleetRun itself owns orchestration only.

## Fleet lifecycle mutation

`FleetRunLifecycle` is the one live owner of Fleet lease + Journal mutations. `FleetRunExecution` activates the admitted session before `RUN_STARTED`; begin failures are caught and terminalized through that same owner. Terminal settlement uses `ExecutionSession.finish`, and if the response is ambiguous after commit the lifecycle replays the canonical terminal Fleet state rather than leaving a process-live orphan lease.

If `RunExecutionSupervisor.submit` rejects an already-admitted run, `FleetRunControlPlane` delegates the exact ticket to `FleetRunLifecycle.finish_admitted`, which uses the same `finish` rule to commit `RUN_FAILED + lease release`. Cancellation also stays inside this owner: a live owner gets cancel fact + lease flag atomically, while a proven dead owner is reclaimed and the orphaned run terminalized in the same transaction.

## Crash boundary

SQLite cannot transact Git, uv, subprocess, or filesystem effects. oh-goon therefore guarantees **at-most-once automatic replay**, not exactly-once effects:

- failure before `EFFECT_STARTED` → retire unstarted coordination under owner authority;
- failure after `EFFECT_STARTED` without canonical report → `OUTCOME_UNKNOWN`, reconcile from baseline;
- canonical report committed → success is authoritative; canonical settlement is the final declared fallible effect boundary, with no post-commit fault point or lifecycle mutation.

If the settlement call itself raises after SQLite may have committed, the effect owner resolves the exact `operation_id` from the Journal. A matching canonical report is returned as success; only absence of canonical evidence can enter interruption classification. Concurrent recovery retries use the same canonical-evidence rule.

Startup `RepositoryEffectRecovery` handles both origins through the same lifecycle, but only after proving execution is idle. Every recovery write transaction rechecks idleness so it cannot classify or retire another live process's attempt. Automatic startup recovery stops at operator-required `PARTIALLY_APPLIED`/`INDETERMINATE`; those rows remain stable until an explicit operator command. Repeating reconcile in either operator-required state replays the exact Journal classification without another repository observation or Journal append. Terminal explicit-command retries replay immutable Journal evidence after ambiguous response loss.

## Fleet plan write path

A complete resolved Fleet plan is one repository effect, not a sequence of independently journaled mini-effects. `FleetPlanService` resolves declarative input. `FleetAdmissionPlanner` is the one owner of every effect-free repository/Plan fact that decides whether execution may begin: imported host-environment declarations, contained working/artifact/condition/output paths, import applicability, and comparable repository-resolvable validity close before `RUN_ADMITTED` for every execution purpose. The admitted run durably carries one compact repository/Plan preview identity per selected repository, bound to the exact filesystem subject instance. `RepositoryEffectExecution` owns claim/fence/effect-start/canonical commit/recovery only after that decision exists; runner preparation may compare current facts with the admitted identity to detect staleness, but it is never the first resolver of those facts. Inputs/defaults/templates/bounded matrices compile into one flat step sequence before admission; no matrix/default object participates in runtime scheduling. Extension acquisition, locked extension-environment preparation, and required built-in tool preparation happen only in explicit `plan prepare`, before plan admission. Strict CI/CD adds its isolated-source, repository-requirement, executor/placement/resource proofs and then binds one frozen repository snapshot, one host-environment capture, content-identified host tools, and Fleet-owned read-only capability snapshots for the run; every attempt derives from those bound inputs rather than rereading mutable host/repository state between steps. Once `EFFECT_STARTED` commits, extension execution uses `uv run --no-sync` and pre-commit invokes its already-prepared executable, so dependency/tool synchronization cannot create an avoidable post-effect failure. A false typed condition creates a canonical skipped fact. Locally-contained subprocess timeouts become known failed attempts with reserved code 124 after the process group is terminated/reaped; missing/invalid declared outputs become a known contract failure with reserved code 125. Neither code may be declared successful, though either may be explicitly retryable. Every completed attempt records repository before/after provenance. Declared artifacts add only present hash/size or missing evidence. Public scalar outputs may bind later whole argv values in process memory; canonical evidence stores only output name/hash/size and preserves the declarative reference marker. After a known `STOP` failure, later normal steps are explicit skips and the trailing finalizer suffix may run; cancellation/fatal/ambiguous loss aborts without claiming finalizer completion. Ambiguous crashes/cancellation still use normal quarantine semantics and are never automatically retried. Concrete completed/skipped/failed step evidence avoids step-level commit/recovery authority. Native Git steps use the same boundary and may perform remote Git effects; ambiguous loss after such an effect remains conservatively handled by the plan capability's existing opaque-mutation recovery semantics.


## Plan source writes versus candidate analysis

Saved Plan browser writes use the `/league/shared-plans` compare-and-swap boundary. Shared Runs and Schedules never gain Plan-source write authority. Local/internal Plan-path tooling resolves YAML through `FleetPlanService`/`FleetPlanResolver`; the removed `/plans/*` workspace no longer provides a parallel HTTP authoring path.

`change_set_id`, base/candidate workspace revisions, revision manifests, per-operation/final patch hashes, impact/dependency lists, `base`/`candidate`/`diverged` verification results, review receipt IDs, and promotion completion IDs are not write capabilities. ChangeSet v1 export/import is a read-only evidence path; content hashes are not signatures and an approved ChangeSet that verifies as exact `candidate` still cannot apply YAML. These values improve review and post-application evidence only. Cross-file application remains intentionally absent; the sole plan write path is still one source file through revision CAS + source lock + atomic replace.

### Candidate confirmation is not a write path
Promotion completion for an approved exact candidate does not call `commit_plan_source`, does not acquire mutation authority, and does not authorize sequential multi-file replacement. YAML writes remain single-file revision-CAS + atomic replace only.

### Portable audit bundles are not a write path

Audit receipt v3 and audit bundle v2 are read-only provenance. The bundle binds a semantic trust-policy identity and may carry the normalized public-key snapshot used for historical report authentication. That embedded snapshot is never an independent trust root; optional current-policy revalidation must use a separately supplied local policy. `bundle_id`, `trust_policy_id`, captured/current authentication states, and audit-receipt identity are evidence only and cannot call plan source CAS, sequential multi-file replacement, repository effects, or execution admission.
