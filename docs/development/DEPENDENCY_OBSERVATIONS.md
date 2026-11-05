# Dependency observations and targeted Shared Runs

Oh Goon uses dependency observations to answer a bounded fleet question without turning repository discovery into a package manager, CMDB, vulnerability database, or second execution engine.

```text
canonical Run / Step evidence
          ↓
qualified lock observation and explicit evidence state
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

The shipped `python/dependency-inventory` Goon is read-only and execution-free.
The Step reads its explicitly selected bounded lock input and delegates interpretation and qualification
to Hashmarks' ecosystem adapter and dependency APIs. Python/uv remains the default;
npm requires an explicit ecosystem and defaults to `package-lock.json`. The
optional `lock_path` selects another exact input; no lock discovery is performed.
It does not invoke package managers, install packages,
parse declarations or inspect the installed environment. Native uv still owns
resolution and lock authoring.

The journal retains the original adapter observation, qualified producer packet,
loaded producer implementation identity, exact member revisions, Run/Goon/Step
provenance, observation time and the searchable package projection. Native roots,
relationships, groups and markers remain intact; application roots are not inventory
members. Unsupported locks and failed observations publish unavailable or unsupported
evidence, never an empty complete inventory.

The SQLite index remains a rebuildable projection. Search uses recorded package
facts. Evidence views and Fleet materialization ask Hashmarks to revalidate the
retained source claims once per repository in that request. Git HEAD/dirty state
and observation age do not establish source freshness. A missing or changed lock
cannot support absence; a manifest-only edit does not change what the lock contains.

Hashmarks' `dependencies.assessment()` validates the retained producer packet and
reobserves its explicit input paths through the native Source owner. It reports
selected-input freshness, producer source equivalence and repository-wide index
freshness separately. Oh Goon translates the producer's selected-input facts and
qualified inventory absence; it does not derive freshness from source equivalence
or recompute completeness from whichever coverage rows happen to be present.
Every producer-declared context must have complete, untruncated inventory coverage
before absence can be used. Missing revision claims and unavailable input evidence
remain unproven even when today's lock can be read.

## Freshness, completeness and availability

These are independent facts. `current`, `stale` and `unknown` describe the selected
lock source; completeness and availability travel separately. Observation time is
shown as history, without a seven-day freshness rule. A current lock inventory does
not establish that declarations are synchronized with it, or that the installed
environment matches it.

Every repository match can be explained back to the exact source, repository revision, Run, Goon and Step that produced it.

Coverage itself is also observable at fleet level. Overview may show only two compact facts: repositories with a current complete dependency snapshot, and repositories that need current dependency evidence. This is navigation evidence, not repository Health. Package/version/advisory detail remains in the Dependencies surface.

The package view exposes two non-matching server-owned populations explicitly:

- **Absent from lock** (`absent_from_lock`) — producer-qualified complete, current, untruncated inventory evidence establishes absence within the lock scope.
- **No current dependency evidence** — the repository lacks usable current complete evidence, including unsupported, unavailable or missing observations. It cannot be classified as absent from the lock.

Both populations are paged by the backend and can be materialized into exact repository IDs. For insufficient evidence, the operator may deliberately choose the existing `python/dependency-inventory` Plan in Shared Runs to refresh those exact repositories. Oh Goon never starts that refresh automatically and never lets the observation query remain a dynamic execution selector.

## Filtering and selection are different

Filtering answers which repositories match the observed facts now. Selection answers which exact repositories the operator has chosen to act on.

`Select all matching` is server-owned and materializes the entire filtered population, not only the currently loaded page. The materialization stores an immutable sorted repository-ID set plus filter provenance. If newer observations later change the query population, an already materialized selection does not silently change.

Shared Runs consume the explicit repository IDs. There is no dependency-specific Run, remediation workflow, or dependency-specific executor.

## Advisory matching

Oh Goon does not own a CVE database. The dependency surface combines an explicitly
supplied advisory identity and affected version range with observed resolved
dependency versions. Hashmarks owns version-predicate observations using native
PEP 440 or npm semver semantics for the selected ecosystem. Unsupported predicates
or versions remain unclassified, never silently safe.

The advisory identity/range remains selection provenance. The dependency observation remains the evidence proving why a repository matched.

## Ecosystem identity and retained history

Every query, package selection and materialized filter carries ecosystem and
package name together. Hashmarks supplies package query identity; Oh Goon does
not apply Python normalization to npm names. Opaque producer component,
resolution-node and context identities survive persistence independently of the
searchable package name. Retained evidence is requalified by the producer rather
than reconstructed from the searchable rows.

Package responses retain the original requested spelling, canonical query name,
native semantics/engine and producer identity together. Search keeps the caller's
query text and qualifies it separately for each selected ecosystem. For PyPI,
`MLFlow` matches `mlflow` while `ML_Flow` matches `ml-flow`; npm and scoped npm
names retain exact native spelling. Materialized filters use the canonical name
returned by the backend, so a qualified alias and its canonical spelling identify
the same population without rewriting retained component or resolution-node IDs.

Native inventory completeness and bounded package projection completeness are
separate. The original observation and omission evidence remain retained when a
package exceeds the projection limit. Its returned rows remain positive evidence;
they cannot establish a complete filtered target population. Repository matching
and target materialization then report an unverified population, including when
the omitted resolutions would otherwise make an indexed result appear empty.
Advisory classification keeps truncated predicate evidence unclassified rather
than counting omitted resolutions as unaffected.

Local database schema 93 stores ecosystem-qualified components. The existing
exact-version durable-state policy refuses older physical databases before
initialization writes; it does not migrate, reset or rewrite their history.
Use the matching build to inspect retained older state, and a separately selected
current database for this build after the original runtime has been quiesced;
selecting a new database does not prove an old Run has stopped. Historical
dependency Snapshot 2 payloads remain
readable as historical evidence without invented component IDs or current
qualification; fresh observation is required for current use. Resolved Plan
contract 48 includes the explicit dependency-input authority; older executable
Plans are not silently reinterpreted.

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

For a 5,000-repository fleet, population accounting, repository paging and target materialization remain server-side and bounded. React never downloads all observations or all repositories to filter locally. Native lock-source revalidation currently opens a CodeMap context per repository; fleet-wide filesystem revalidation latency is separate from indexed population performance and is not qualified by the population scale test. A narrower producer-owned source-binding operation is the appropriate upstream optimization.

The protected population-accounting case is the MLflow maintenance flow: 5,000 repositories → 137 observed MLflow users → 92 affected by the selected range → explicit 92-repository target set → existing Shared Plan → only failed/not-started repositories require investigation.

Do not extend this into generic observation kinds, full dependency graphs, license inventory, secret storage, automatic remediation, repository Health/risk, or Plan maturity until real fleet usage demonstrates that need.
