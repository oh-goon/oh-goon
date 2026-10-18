# Read path

Read truth starts from one transactionally pinned durable cut:

```text
SQLite transaction
  -> LocalStateReadView
  -> JournalReadView
       -> verify canonical Journal hash chain when a new cut is required
       -> build private repository/run indexes
       -> fold each run once
       -> optionally reuse this verified immutable view while the cache key is unchanged
  -> core/projections/*
  -> FleetSnapshot / product read model
  -> API
  -> frontend ReadResource
```

`JournalReadView` is the only contributor-facing Journal read abstraction. The former public `JournalProjectionContext` was removed because it added a conceptual hop without separate authority.

Operation-current identity is explicit. Use `current_capability(repository, capability_id, target)` when a prerequisite or product decision refers to one exact durable intent; `target=None` matches only non-targeted work. Use `current_family(repository, action)` only when the product deliberately asks for the newest result across an action family, such as the Fleet overview. FleetRun reads use `latest_run_exact(capability_id, target)` versus `latest_run_family(action)` with the same distinction. There is no ambiguous `current()`/`latest_run()` compatibility path. Prerequisites are declared as exact capability requirements, so readers never infer "some PLAN" from an UPDATE action.

Result freshness is also identity-explicit. A later report stales an earlier result only when its durable `CapabilitySnapshot` names the earlier `capability_id` in `invalidates_capabilities`, or when the later capability deliberately records `invalidates_all`. `OperationAction` is never an invalidation key; adding a new PLAN/UPDATE-family capability therefore requires an explicit invalidation decision instead of silently inheriting action-family behavior.

The process-local read cache is **not** another source of truth. It is keyed by journal revision/head hash plus database/WAL file signature and is invalidated by every Journal append. Any detected file change causes a full decode/hash verification before another view can be cached. The cached `JournalIndex` is structurally read-only (tuples + mapping proxies), so consumers share one verified cut instead of receiving deep copies of already-immutable typed projections. `OperationReport` remains copied at Journal boundaries only because recipe/security structured output can contain opaque mutable JSON leaves.

Do not:

- query the mutable Journal repeatedly while assembling one response;
- persist readiness/attention/FleetSnapshot as a second truth source;
- let a projection mutate durable state;
- add persistent checkpoints before profiling real replay cost.

Measure current cold/warm behavior with:

```bash
uv run --offline --frozen --no-sync --project backend oh-goon dev projection-profile --samples 10
```


## Plan read ownership

Plan inspection/authoring is not exposed as a second HTTP workspace. Browser Plan authoring is owned by the saved-Plan boundaries under `/league/shared-plans`; Shared Runs and Schedules consume those saved Plans. Backend `FleetPlanService`/`FleetPlanResolver` remain the execution/tooling owners for resolving Plan YAML when an internal command supplies a source path.

