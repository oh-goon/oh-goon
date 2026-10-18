# The Scout — cross-run workload and resource observability

The Scout is Oh Goon's optional read-only cross-run observability tool. It answers **what is consuming resources, where that consumption comes from, whether it is frequency or per-run intensity, whether measured behavior changed, and which execution should be opened in Game Tape next**.

## Ownership boundary

The ownership model is strict:

```text
Enforcer   may this execute?
Executor   execute it
Game Tape  what happened in this execution?
The Scout  what patterns exist across executions?
```

Scout never executes, schedules, admits, retries, cancels, places, throttles, or modifies work. There is no runtime dependency from those owners to Scout. `backend/src/oh_goon/repository_tooling/architecture/scout_observer_contract.py` enforces that boundary. Scout-owned production modules live under the explicit `oh_goon.scout` package; ownership never comes from `scout_*` filename prefixes. There are no forwarding aliases for the pre-package module locations.

If Scout is disabled, unavailable, broken, slow, upgrading, or removed, Oh Goon execution must behave exactly the same.

## Evidence source

Scout projects facts already owned by the Journal and canonical Fleet/Game Tape evidence. The first implementation uses physical-attempt resource evidence that Oh Goon already records:

- CPU usage (`cpu_usage_usec`), stored/displayed canonically as CPU-seconds;
- peak memory (`memory_peak_bytes`), stored as bytes and formatted as MiB/GiB;
- repository-execution and Step wall duration (`duration_ms`), stored canonically as seconds, plus attempt identity;
- CPU and memory resource limits used in the execution-environment compatibility key;
- stable repository identity, Plan identity, Goon name/source identity, Step identity, run status, and timestamps.

Scout does **not** poll GitLab to reconstruct execution history and does not ingest source code, stdout/stderr payloads, secret values, or resolved environment-variable values.

Execution origin is also intentionally unavailable until canonical execution truth exposes a generic origin field; Scout does not infer `push`, `manual`, `schedule`, or `shared_run` from IDs or naming conventions.

Memory-time is intentionally unavailable in the first implementation because canonical execution evidence does not contain an integrated memory measurement. Scout must never derive or estimate it from peak memory.

## Canonical metric rules

The metric dictionary is backend-owned in `oh_goon.core.models.scout` / `oh_goon.core.scout_policy`; React formats returned evidence and does not invent thresholds.

Core rules:

- wall duration is elapsed time, never CPU time; measurement schema v3 records it independently at repository-execution and Step boundaries;
- average CPU is derived only when complete CPU time and positive wall duration exist: `CPU-seconds / wall-seconds`; the UI labels this explicitly as average CPU cores;
- peak CPU remains unavailable because cumulative CPU time does not contain a reliable instantaneous peak; Scout never derives peak CPU from resource limits or average CPU;
- missing measurements remain missing, never zero;
- totals include resource consumption from successful, failed, attention-required, cancelled, and interrupted attempts; the overview Failure rate itself counts only canonical `FAILED` repository executions and does not rename cancellation/interruption/attention as failure;
- normal per-run/peer performance comparisons use successful completed work;
- a Shared Run counts one repository execution per `(run_id, repository_id)` rather than collapsing the whole Fleet run to one execution;
- peer comparison requires the same Goon source, Step, materially relevant execution environment, and resource envelope;
- CPU peer ratios require at least 20 comparable peer executions across at least 5 peer repositories;
- memory peer ratios require the same sample floor plus at least 90% memory telemetry coverage;
- Goon versions are not silently mixed; regression evidence exposes the actual baseline/current source observation periods and the current, baseline, and overlapping repository populations;
- findings state observed differences, not causes.

The canonical engineering rule is: **same thing, same unit, same boundary, same denominator, same window, same workload — then compare.**

## Read model and API

`ScoutService` depends only on the canonical Journal, the canonical database path supplied by composition, and an enabled flag. Synchronization happens only on the Scout read side: Scout first checks the Journal revision, asks for Fleet runs whose canonical facts changed after Scout's processed revision only when necessary, reprojects only those runs, and refreshes only their derived observations. Execution never calls, waits for, or acknowledges Scout.

Scout owns one optional disposable analytical sidecar beside the canonical database (for example `state.scout.sqlite3`). The sidecar contains only `scout_*` derived observations and Scout metadata; it does not contain Journal facts, leases, admission state, scheduling state, or any other execution authority. Scout never writes through the canonical SQLite coordinator, so analytical materialization cannot hold the canonical database writer lock or advance Oh Goon's durable revision. Deleting the sidecar changes no execution truth: it can be rebuilt from canonical Journal evidence. When Scout is disabled, the sidecar is not created.

The read API is GET-only:

- `/api/scout/overview`
- `/api/scout/findings`
- `/api/scout/repositories/{repository_id}`
- `/api/scout/plans/{plan_id}`
- `/api/scout/goons`
- `/api/scout/goon-leaders`
- `/api/scout/search`
- `/api/scout/health`
- `/api/scout/metrics`

Queries use bounded time windows, deterministic sorting, and bounded result limits. Drill-down responses also return the unsliced population counts, so a bounded first page cannot look like the complete population. The generated OpenAPI/frontend contract is the only browser API contract. Scout health exposes measurement-schema version, canonical/processed Journal revisions and lag, retained observation counts, configured retention horizons, snapshot-cache behavior, and the last uncached analytical snapshot-load latency without becoming another execution-health controller.

## Browser investigation model

The browser follows progressive disclosure rather than rendering thousands of repositories at once:

```text
Scout overview
  → Needs Scouting / Heavy Hitters / Goon Leaders
  → Repository
  → Plan
  → Goon
  → Step
  → Open in Game Tape
```

The overview intentionally starts with a small set of league facts, one selected trend, and focused triage categories. Ranking views open with 20 results and use URL-preserved pagination beyond that initial page rather than silently capping the population. Goon Leaders has its own league-wide paginated destination so shared Goon pressure is discoverable beyond the bounded overview preview. Global Scout search is secondary navigation for operators who already know a Repository, Plan, or Goon name; it uses bounded indexed prefix matching over Scout's derived identity rows and URL-preserved deterministic pagination instead of stopping at the first result page. Repository drill-down preserves Plan identity and lets the user narrow the resource decomposition to one Plan. Plan search results open a cross-repository Plan view, so every searchable identity has a useful drill-down destination. Goon drill-down uses the same one-chart-at-a-time CPU/memory/runs selector to show bounded cross-run resource trends without duplicating Game Tape. Step views expose peer evidence counts and withhold ratios when comparison requirements are not met. Finding and Step evidence details expose the metric/unit, exact window, outcome population, compatibility rule, peer sample counts, telemetry coverage, Goon source identities, and the compatible execution/resource envelope where relevant. The environment summary is intentionally metadata-only: enforced CPU/memory envelopes and opaque isolation/runtime/toolchain identities are visible, while environment-variable values, logs, commands, and secrets remain outside Scout. Route-aware breadcrumbs are derived from the URL-visible investigation state so Repository → Plan and Goon → source navigation remains understandable and browser Back can restore the same Scout context.

Game Tape remains the only owner of one-run logs, detailed timeline, retries, receipts, and debugging. The Scout action is explicitly **Open in Game Tape →**.

## Failure and missing-data behavior

Scout errors are Scout errors, never run failures. The browser copy explicitly states that Oh Goon execution is unaffected.

Trend points carry telemetry coverage. A period containing execution but no CPU or memory observation remains unavailable for that metric rather than being plotted as zero. Empty periods may legitimately show zero executions/CPU consumption.

## Scale and retention

Scout now uses incremental query-side materialization instead of rebuilding all retained Scout evidence whenever the Journal revision changes. A Journal revision change identifies only Fleet runs whose canonical facts changed; Scout reprojects those current run details and updates its derived rows. Repeated Scout reads at the same Journal revision do not rebuild unchanged observations.

Storage is intentionally bounded with two evidence horizons:

- detailed Step/resource observations: **90 days**;
- lightweight repository-run observations: **365 days**.

Retention deletion is maintained from Scout's read side at most once per hour, even when the canonical Journal has not advanced. This keeps the physical Scout tables within their advertised horizons during an idle league without adding any execution-path callback or background requirement.

The longer run-only horizon exists so all current browser windows through 30 days can evaluate the required seven historical periods for Run Spike detection without retaining a year of Step-level resource evidence. Run Spike additionally requires evidence predating the oldest of those seven baseline periods; unknown pre-retention history is never treated as zero activity. A wider analytical window may legitimately report insufficient historical data rather than substituting a different baseline.

The implementation includes 5,000-repository projection and large shared-Plan/Goon drill-down regression tests. Repository/Goon drill-down collections are bounded (100 entities, 200 Steps, 20 representative runs) and disclose their full population counts rather than silently truncating. The next scale evolution, if real deployment evidence requires it, is time-bucket rollups for older aggregate trends—not polling GitLab, retaining unbounded high-frequency samples, or moving Scout into executor/admission/scheduling. Any rollup remains derived, removable, measurement-versioned, and downstream of canonical execution facts.

## Release checks

Scout changes must keep these checks green:

- `scripts/check_architecture.py`, including `OBS-001`;
- generated OpenAPI/frontend contract parity;
- Scout projection/service/API tests;
- production build and theme contrast;
- English/Swedish i18n parity;
- frontend read-controller ownership.

Any new metric or comparison must first define its source fact, unit, boundary, aggregation, population, missing-data behavior, compatibility cohort, sample floor, coverage requirement, UI label, and measurement-version impact.

## Production certification follow-up

`SCOUT_PRODUCTION_CERTIFICATION.md` owns the post-implementation proof status. The repository now carries executable privacy/GitLab-independence guards, a deterministic 5,000-repository / 500-user load probe, a 500-concurrent-reader regression, and Scout-specific Playwright acceptance journeys.

Multi-user authorization remains intentionally outside Scout. The local API is protected by one process-local access capability, but Oh Goon still has no multi-user User/Tenant/repository-visibility owner. Scout must consume that future central product owner; it must never introduce a parallel Scout identity or RBAC model.
