# Observability ownership

Oh Goon observability is read-only projection over facts that already have an owner. It is not a second execution model and it does not own execution state.

```text
canonical execution facts        producer-qualified evidence   optional resource analytics
Fleet lifecycle / Journal        dependency observations       Scout
accepted Step/Attempt results    retained producer qualifiers   CPU / memory / frequency
             \                       |                       /
              \________________ read-only views ____________/
                       Overview / filters / drill-down
```

## Owners

### Execution truth

The original Fleet lifecycle and Journal own durable Run state and its accepted
repository/Step/Attempt evidence. `FleetRunDetail` and Game Tape are read
projections of those facts, not additional state authorities. `GameTapeStatus` is
the shared read-only repository/Step observability vocabulary. Execution Overview
may bucket, count, filter and page these facts, but it must not invent another
status vocabulary or persist another copy.

Worker health, process exit, logs and diagnostic events cannot establish custody
or terminal success. The [execution supervisor](development/EXECUTION_SUPERVISOR.md)
owns fresh-worker process submission and the atomic SQLite claim. Complete
failure/drain and broader lifecycle qualification remain HUG-181/HUG-183 work.
Observability does not create a second Run route.

An empty time-grid cell means there is no execution observation for that repository and bucket. It is absence of evidence, not a `NO_EXECUTION` status.

### Domain observations

A typed evidence domain may maintain a rebuildable index when querying raw Run
evidence repeatedly would be expensive. Dependency observations are the current
example: they retain producer-qualified repository facts and their admission
bindings. Hashmarks owns dependency meaning, freshness, completeness and consumer
relationships; persistence or Fleet aggregation cannot reinterpret those facts.
Run/Plan identity and execution lineage still point to the original Journal.

### Repository meaning

Repository/source correlation, including interpreting a runtime path, traceback,
line or symbol against captured source, belongs to mandatory Hashmarks through the
public `oh_goon.integrations.hashmarks` API. Missing or ambiguous producer facts
remain unavailable or qualified; observability cannot reconstruct them from
ambient Git/filesystem state. Preserve source/producer identity, scope,
freshness, completeness and omissions beside any execution observation. See the
[Hashmarks handoff](development/HASHMARKS_EXECUTION_LAYER.md#runtimesource-correlation).

Repository/content identity describes observed source; execution-custody and
materialization identities describe the bytes admitted and exposed to execution.
Related bytes do not make those different scopes interchangeable. Later
correlation may explain a Run, but cannot change its frozen work or permissions.

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
2. If typed evidence needs Fleet querying, reuse its narrow domain observation owner and preserve producer qualification for repository facts.
3. If the fact is high-volume analytical/resource telemetry, use an explicit optional analytical owner such as Scout.
4. Otherwise wait for a concrete use case instead of creating a generic metrics framework.

Every new metric must define source, unit, aggregation and missing-data semantics. Do not add `GenericMetric`, `GenericObservation`, a central metrics database, frontend status derivation, or an observability write path into execution.

## Execution timeline

`GET /fleet/runs/observations` is a bounded read projection over canonical Run/Game Tape facts. It accepts one common `day | week | month` bucketing path plus optional Plan/repository filters and repository pagination. Cells contain counts of existing `GameTapeStatus` values. The browser does not aggregate raw Runs itself.

Grid drill-down reuses `GET /fleet/runs` with repository/time/Plan filters and then the normal Run → Game Tape → Goon → Step → evidence path. No grid-specific detail model exists.
