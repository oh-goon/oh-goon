# Dependency observations and targeted Shared Runs

Oh Goon uses dependency observations to answer a bounded fleet question without turning repository discovery into a package manager, CMDB, vulnerability database, or second execution engine.

```text
canonical Run / Step evidence
          ↓
complete typed dependency snapshot
          ↓
derived fleet dependency index
          ↓
server-side search / facets
          ↓
explicit repository IDs
          ↓
existing Shared Run
          ↓
repository Runs → exceptions → Game Tape
```

## Evidence ownership

The shipped `python/dependency-inventory` Goon is read-only. Its native `dependency_inventory` Step parses repository dependency files as data and emits a typed `DependencyObservationSnapshot`. The snapshot binds repository identity/revision, source path and SHA-256, producer Goon/Step, Fleet Run, observation time, coverage and typed dependency observations.

Declared and resolved dependency facts are different evidence. A declaration such as `mlflow >=2` does not replace the resolved `uv.lock` fact `mlflow 2.17.2`. The first implementation supports Python project/workspace declarations and resolved `uv.lock` versions.

A successful complete snapshot atomically replaces the previous current snapshot for that observation scope. If a later complete snapshot no longer contains `mlflow`, the older positive match is no longer current. Failed or interrupted observation never becomes proof of absence.

The SQLite dependency index is a rebuildable read projection of committed canonical evidence. Fleet dependency queries use that projection; they do not reopen thousands of repositories during search.

## Current, stale, and missing evidence

Dependency discovery distinguishes a current positive match from a current complete non-match and from insufficient current evidence. Age is presentation evidence, not a Health score. A repository with no current usable dependency evidence must never be reported as unaffected.

Every repository match can be explained back to the exact source, repository revision, Run, Goon and Step that produced it.

Coverage itself is also observable at fleet level. Overview may show only two compact facts: repositories with a current complete dependency snapshot, and repositories that need current dependency evidence. This is navigation evidence, not repository Health. Package/version/advisory detail remains in the Dependencies surface.

The package view exposes two non-matching server-owned populations explicitly:

- **Current snapshot, no match** — a complete current snapshot exists and the package is absent. This is a proven non-match for that snapshot.
- **No current dependency evidence** — the repository has an aged, stale, or missing complete snapshot. It is neither affected nor unaffected until current evidence exists.

Both populations are paged by the backend and can be materialized into exact repository IDs. For insufficient evidence, the operator may deliberately choose the existing `python/dependency-inventory` Plan in Shared Runs to refresh those exact repositories. Oh Goon never starts that refresh automatically and never lets the observation query remain a dynamic execution selector.

## Filtering and selection are different

Filtering answers which repositories match the observed facts now. Selection answers which exact repositories the operator has chosen to act on.

`Select all matching` is server-owned and materializes the entire filtered population, not only the currently loaded page. The materialization stores an immutable sorted repository-ID set plus filter provenance. If newer observations later change the query population, an already materialized selection does not silently change.

Shared Runs consume the explicit repository IDs. There is no dependency-specific Run, remediation workflow, or dependency-specific executor.

## Advisory matching

Oh Goon does not own a CVE database. The dependency surface may combine an explicitly supplied advisory identity and affected version range with observed resolved dependency versions. The initial range evaluator intentionally accepts only stable numeric comparisons such as `<2.18.0` or `>=2.0,<2.18.0`. Versions that cannot be compared safely are reported as unclassified, never silently safe.

The advisory identity/range remains selection provenance. The dependency observation remains the evidence proving why a repository matched.

## Browser drill-down

The normal investigation path remains top-to-bottom:

```text
Overview
  ↓
Dependencies / observed aggregate
  ↓
package + version/evidence/advisory facets
  ↓
server-filtered repository population
  ↓
why this repository matched
  ↓
explicit target selection or repository inspection
  ↓
Shared Run / Run
  ↓
Game Tape
  ↓
Goon → Step → exact evidence
```

Filters are URL-durable and rendered as simple facets/chips rather than a query language. Repository → Dependencies uses the same read model as Dependency → Repositories.

## Scale and stopping rule

For a 5,000-repository fleet, package search, version/advisory aggregation, evidence coverage, repository paging and target materialization remain server-side and bounded. React never downloads all observations or all repositories in order to filter locally.

The first protected acceptance case is the MLflow maintenance flow: 5,000 repositories → 137 observed MLflow users → 92 affected by the selected range → explicit 92-repository target set → existing Shared Plan → only failed/not-started repositories require investigation.

Do not extend this into generic observation kinds, full dependency graphs, license inventory, secret storage, automatic remediation, repository Health/risk, or Plan maturity until real fleet usage demonstrates that need.
