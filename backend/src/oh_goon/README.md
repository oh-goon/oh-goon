# Backend source navigation

This is the implementation map for `oh_goon`. Read `../../AGENTS.md` and `../../README.md` first; use this file only to choose the correct Python owner before opening modules.

## Choose the owner

```text
Task / fact                                                Start here
HTTP route, request/response translation, web serving      api/
Pure domain fact, immutable model, policy, validation      core/
Native command/Git/project operation                       engine/
Filesystem/process/container/VCS/external side effect      integrations/
Durable state, lease, lifecycle, scheduler, runtime store  runtime/
Product use-case coordinating existing owners              services/
Read-only cross-run workload/resource observability        scout/
Installed native Fleet authority issuance                  native_issuer/
Repository architecture, generation, release and checks    repository_tooling/
CLI parsing/dispatch only                                  cli*.py
Process/service composition/lifecycle                      application.py
Private construction wiring                                _application_foundation.py / _application_services.py
```

The six general layers coexist with the scoped Scout, native issuer and repository-tooling packages described below. Choose an existing owner before adding a package; a scoped package is not another general layer.

For repository identity work, use the [identity owner map](../../../docs/development/ARCHITECTURE_DECISIONS.md#git-is-optional-repository-native-metadata-not-source-authority):
`core/product_identity.py` owns catalog and filesystem-subject tokens,
`integrations/hashmarks/` owns the repository-evidence/content identity boundary,
`RepositorySourceAuthority` binds the admitted subject and captured source, and
`runtime/workspaces.py::RepositoryWorkspaceAuthority` owns durable workspace allocation.
Fleet/Enforcer admission and LACE/process isolation retain their respective validation
and physical custody authority.

## Decision ladder

For Scout, native issuance or repository tooling, use the scoped owner below. For general product behavior, ask these questions in order:

1. **Can the behavior be expressed as a pure fact or decision without I/O?** Put it in `core/` or reuse the existing core owner.
2. **Is it a narrow native repository command/Git/project primitive?** Use `engine/`.
3. **Does it touch the host, filesystem, process, container, VCS or another external boundary?** Use `integrations/`.
4. **Is it durable runtime coordination/state such as leases, lifecycle, journals, executors or scheduling?** Use `runtime/`.
5. **Does it coordinate several owners into a product operation?** Use the existing `services/` use-case owner.
6. **Is it HTTP translation only?** Use `api/`; routes should delegate semantic work rather than own it.

Do not create `common.py`, `utils.py`, another manager/context/service layer, or a new top-level directory because the choice feels difficult. Difficulty choosing an owner is a signal to inspect the nearest existing owner/test first.

## Owner map

### [api/](api/)

FastAPI construction, routes, schemas and HTTP-only translation. Start with `api/app.py::create_app` for application wiring or `api/routes/<capability>.py` for one API surface. API code must not become the owner of product semantics.

### [core/](core/)

Framework-independent facts, immutable models, validation/policy and pure projections. `core/models/` holds durable/public shapes; `core/projections/` derives read models; `core/ports/` contains narrow dependency boundaries. Game Tape semantic facts/projections begin in the `core/game_tape_*` owners, not React or API routes.

`core/models/agent_source.py` owns the immutable Agent source members, captured
trees, Step handoff and canonical delivery codec. The Plan resolver's
`services/agent_authoring_sources.py` remains the single source-capture owner and
the bridge into the original Fleet journal reservation. Execution integrations
consume the same core contract and frozen bytes without importing the authoring
service or reacquiring source paths.

`core/agent_harness_contract.py` owns passive harness input, capability compatibility
and bounded untrusted submission data. It reuses the original frozen source
contract and remains separate from execution admission and typed role-result
acceptance. The [Agent runtime inventory](../../../docs/development/AGENT_RUNTIME_OWNERS.md)
describes its native consumers and the retained typed result owners.

### [engine/](engine/)

Native local command/Git/project execution primitives and the focused operation implementations under `engine/operations/`. Start at `engine/runner.py` when tracing native action dispatch.

### [integrations/](integrations/)

Concrete effect boundaries: filesystem/process/container/isolation/VCS/OCI/Kubernetes/OpenSandbox and other host/external operations. These modules implement effects already admitted/resolved elsewhere; they should not become a second policy or plan-resolution authority.

Native Agent Steps start in `fleet_plan_native_steps.py`; command/event translation
is in `agent_native_execution.py`. Ordinary isolated profiles reuse
`fleet_plan_process_isolation.py` and the existing Bubblewrap backend. The private
inference broker/relay carry only the selected model protocol. All native processes,
PTY transport and cancellation still use the original engine command owner.

### [runtime/](runtime/)

Durable/local process state and coordination: database/journal stores, executor capacity/placement, leases, lifecycle, schedulers, safety cuts and template stores. `runtime/workspaces.py::RepositoryWorkspaceAuthority` owns durable candidate/source workspace identity, lifecycle state, and the one persistent managed-workspace path resolver; `RunPathLayout`/`AttemptPathLayout` continue to own disposable execution scratch. For `READ_SNAPSHOT`, the same owner materializes the canonical clean source snapshot, persists its source-content proof, and re-proves it before the process-isolation layer may expose it; Bubblewrap uses a physical read-only bind and backends without an explicit read-only-workspace guarantee fail closed. Runtime may persist/coordinate canonical decisions but should not reinterpret them independently.

The existing X0 and lifecycle architecture checks always validate current
execution owners, SQLite transaction durability and backup boundaries. These
source checks apply to clean Git checkouts and full working copies equally;
historical checkpoint files do not activate or disable current structural
contracts. Explicit archive and host-qualification commands retain their own
historical-input requirements.

### [services/](services/)

Product use-cases that compose lower-level owners: Fleet, plan resolution/preparation, repository effects, recovery, Schedules, migration, security and related operations. Start from the service named after the user operation, then follow its explicit collaborators downward.

### [scout/](scout/)

Optional read-only cross-run workload/resource observability over canonical Journal/Fleet evidence. Start at [service.py](scout/service.py); [THE_SCOUT.md](../../../docs/development/THE_SCOUT.md) defines its observer boundary. Execution admission, scheduling, retry, cancellation, placement and recovery must not depend on Scout. Game Tape owns the handoff into one execution's debugging evidence.

### [native_issuer/](native_issuer/)

Installed native Fleet authority issuers and signer custody, used only with explicit deployment authority. Start at [the native issuer suite](native_issuer/enforcer_lace_native_issuer_suite.py). Repository-tooling import compatibility names bind to these same module objects; they do not own separate issuance state or grant another authorization path.

### [repository_tooling/](repository_tooling/)

Repository architecture checks, contract generation, release assembly, development commands and qualification tooling. [backend/pyproject.toml](../../pyproject.toml) registers named command entrypoints; [scripts/README.md](../../../scripts/README.md) owns their contributor navigation. The `scripts/` surface contains launch support and repository-tooling tests. Product runtime owners must not acquire repository-tooling dependencies.

## Plan/Goon compile boundary

The authoring language and execution representation are intentionally different layers:

```text
Plan YAML / Goon YAML
        ↓ strict parse/validation
CompiledExecutionTemplate
  └── CompiledGoonImportStep for ordered Goon edges
        ↓ one source-graph/resolution pass
ResolvedFleetPlan
        ↓ runtime execution
```

`CompiledExecutionTemplate` is **not** another authoring model. `integrations/fleet_plan_compilation.py` parses each source once into `ParsedFleetYamlDocument`; compilation and `fleet_plan_source_snapshot.py` source positions reuse that same syntax tree. A Goon keeps the exact authored `category/name` identity through `CompiledGoonImportStep`, source-graph lookup and `ResolvedImport`—there is no internal prefix/alias form to strip later. `services/fleet_plan_source_graph.py` loads each unique Goon once, runtime acquisition and resolution reuse that exact graph, and runtime code receives only `ResolvedFleetPlan`. Do not reintroduce a second YAML parse, identity normalization pass, graph rediscovery, or generic template/include names below the parser boundary.

## Common task routes

```text
Change an API response shape
→ api route/schema → owning service/core fact → regenerate contract

Change Goon/Plan resolution
→ services/fleet_plan_* resolution owners → core models/policy only when the fact itself changes

Change physical step execution/isolation
→ integrations/fleet_plan_* execution/isolation owner

Change durable executor/lease/scheduler state
→ runtime/ executor/execution/lifecycle owner

Change Game Tape semantic projection
→ core/game_tape_* → service/API only for composition/exposure

Change CLI syntax only
→ cli_parser*.py / cli_runtime*.py → existing service for semantic behavior
```

## Tests

After choosing the source owner, use the matching backend test owner as the second navigation index:

```text
api/          → tests/unit/api/
core/         → tests/unit/core/
engine/       → tests/unit/engine/
integrations/ → tests/unit/integrations/
runtime/      → tests/unit/runtime/
services/     → tests/unit/services/
cli*.py / application.py / _application_*.py → tests/unit/entrypoints/
```

Read `../../tests/README.md` for backend integration placement. Search inside the matching owner before searching all backend tests.

Scoped packages use the existing behavior-based test owners: Scout projections use `tests/unit/core/`, its persistence uses `tests/unit/runtime/`, its public use-cases use `tests/unit/services/`, and HTTP integration uses `tests/integration/api/`. Native issuer behavior uses `tests/unit/services/`; installed-wheel/source qualification and architecture regressions use root `scripts/tests/`. All repository-tooling regressions belong in [scripts/tests/](../../../scripts/tests/).

## Change impact

Use this map to find focused proof. Directory ownership locates a test owner;
it does not automatically select its complete campaign before merge.

```text
api/          → unit/api + backend integration/api
core/         → unit/core
engine/       → unit/engine + integration/engine when native integration changes
integrations/ → unit/integrations
runtime/      → unit/runtime
services/     → unit/services + integration/services when orchestration boundaries change
cli*.py       → unit/entrypoints
```

Public FastAPI/OpenAPI changes add generation/freshness and the smallest affected
consumer proof. Other product changes stay with their affected backend owners.
Repository tooling uses [its focused loop](../../../scripts/README.md).
`make check` admits relevant fast checks; complete owners run daily. A critical
native/lifecycle reproducer remains a named pre-merge obligation.

CLI syntax/help changes update their owning documentation. Layout/owner-map
changes need architecture and focused tooling/protection proof.



## Frozen execution identity

`plan.yml` owns intent, `goon.yml` owns behavior, and the resolver remains the only Plan/Goon interpreter. `goon.lock` records the compact execution fingerprint and exact commits for declared remote Goon/Plan and Skill sources; ecosystem lockfiles retain dependency truth, Enforcer retains authorization, and Game Tape retains execution evidence. See the [execution lock contract](../../../docs/goon-lock.md).
