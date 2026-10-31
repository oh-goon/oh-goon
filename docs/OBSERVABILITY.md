# Observability ownership

Oh Goon observability is read-only projection over facts that already have an owner. It is not a second execution model and it does not own execution state.

```text
canonical execution facts        typed domain evidence        optional resource analytics
Fleet Journal / FleetRunDetail   dependency observations      Scout
Game Tape                        future evidence indexes       CPU / memory / frequency
             \                       |                       /
              \________________ read-only views ____________/
                       Overview / filters / drill-down
```

## Owners

### Execution truth

The Fleet Journal, `FleetRunDetail`, and Game Tape own Run, repository, Step, Plan identity and execution status. `GameTapeStatus` is the canonical read-only repository/Step observability vocabulary. Execution Overview may bucket, count, filter and page these facts, but it must not invent another status vocabulary or persist another copy.

An empty time-grid cell means there is no execution observation for that repository and bucket. It is absence of evidence, not a `NO_EXECUTION` status.

### Domain observations

A typed evidence domain may maintain a rebuildable index when querying raw Run evidence repeatedly would be expensive. Dependency observations are the current example. The index owns dependency facts only; Run/Plan/repository identity continues to point back to canonical execution evidence.

### Resource analytics

Scout owns optional analytical resource observations such as CPU, memory and execution frequency. Its sidecar can be rebuilt or removed without changing execution truth or the basic Execution Overview. Scout reuses the canonical repository Run observation projection and adds only resource-specific evidence.

An explicitly budgeted local CLI Run also records its native execution envelope
in the original admission and Run/Game Tape projections. Those facts identify
the systemd scope, invocation, requested limits and observed kernel ceilings;
they establish what contained the Run. They do not create a resource analytics
store. A final native observation reports available CPU time, memory peak, OOM
facts and cleanup convergence separately from the Run's existing outcome.
Missing measurements remain unknown, including accounting lost when systemd
retires a successful scope. An exit signal alone does not establish an OOM.
Missing final observation evidence is not a measured zero or a successful
cleanup claim. See [local Run budgets](development/TEMPLATE_INPUT_INSPECTION.md#limit-a-local-run).

## Future metric rule

For every new observability request, choose the existing owner before adding storage or models:

1. If the fact already exists in Run/Game Tape, add only a read projection or aggregation.
2. If the fact is typed evidence produced by execution and needs fleet querying, add it to the narrow domain observation owner.
3. If the fact is high-volume analytical/resource telemetry, use an explicit optional analytical owner such as Scout.
4. Otherwise wait for a concrete use case instead of creating a generic metrics framework.

Every new metric must define source, unit, aggregation and missing-data semantics. Do not add `GenericMetric`, `GenericObservation`, a central metrics database, frontend status derivation, or an observability write path into execution.

## Execution timeline

`GET /fleet/runs/observations` is a bounded read projection over canonical Run/Game Tape facts. It accepts one common `day | week | month` bucketing path plus optional Plan/repository filters and repository pagination. Cells contain counts of existing `GameTapeStatus` values. The browser does not aggregate raw Runs itself.

Grid drill-down reuses `GET /fleet/runs` with repository/time/Plan filters and then the normal Run → Game Tape → Goon → Step → evidence path. No grid-specific detail model exists.
