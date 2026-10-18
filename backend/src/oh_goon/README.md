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
CLI parsing/dispatch only                                  cli*.py
Process/service composition/lifecycle                      application.py
Private construction wiring                                _application_foundation.py / _application_services.py
```

The six directories are the complete top-level implementation owners. Do not add another top-level package directory just to avoid choosing between them.

## Decision ladder

When ownership is unclear, ask these questions in order:

1. **Can the behavior be expressed as a pure fact or decision without I/O?** Put it in `core/` or reuse the existing core owner.
2. **Is it a narrow native repository command/Git/project primitive?** Use `engine/`.
3. **Does it touch the host, filesystem, process, container, VCS or another external boundary?** Use `integrations/`.
4. **Is it durable runtime coordination/state such as leases, lifecycle, journals, executors or scheduling?** Use `runtime/`.
5. **Does it coordinate several owners into a product operation?** Use the existing `services/` use-case owner.
6. **Is it HTTP translation only?** Use `api/`; routes should delegate semantic work rather than own it.

Do not create `common.py`, `utils.py`, another manager/context/service layer, or a new top-level directory because the choice feels difficult. Difficulty choosing an owner is a signal to inspect the nearest existing owner/test first.

## Owner map

### `api/`

FastAPI construction, routes, schemas and HTTP-only translation. Start with `api/app.py::create_app` for application wiring or `api/routes/<capability>.py` for one API surface. API code must not become the owner of product semantics.

### `core/`

Framework-independent facts, immutable models, validation/policy and pure projections. `core/models/` holds durable/public shapes; `core/projections/` derives read models; `core/ports/` contains narrow dependency boundaries. Game Tape semantic facts/projections begin in the `core/game_tape_*` owners, not React or API routes.

`core/models/agent_source.py` owns the immutable Agent source members, captured
trees, Step handoff and canonical delivery codec. The Plan resolver's
`services/agent_authoring_sources.py` remains the single source-capture owner and
the bridge into the original Fleet journal reservation. Execution integrations
consume the same core contract and frozen bytes without importing the authoring
service or reacquiring source paths.

### `engine/`

Native local command/Git/project execution primitives and the focused operation implementations under `engine/operations/`. Start at `engine/runner.py` when tracing native action dispatch.

### `integrations/`

Concrete effect boundaries: filesystem/process/container/isolation/VCS/OCI/Kubernetes/OpenSandbox and other host/external operations. These modules implement effects already admitted/resolved elsewhere; they should not become a second policy or plan-resolution authority.

### `runtime/`

Durable/local process state and coordination: database/journal stores, executor capacity/placement, leases, lifecycle, schedulers, safety cuts and template stores. `runtime/workspaces.py::RepositoryWorkspaceAuthority` owns durable candidate/source workspace identity, lifecycle state, and the one persistent managed-workspace path resolver; `RunPathLayout`/`AttemptPathLayout` continue to own disposable execution scratch. For `READ_SNAPSHOT`, the same owner materializes the canonical clean source snapshot, persists its source-content proof, and re-proves it before the process-isolation layer may expose it; Bubblewrap uses a physical read-only bind and backends without an explicit read-only-workspace guarantee fail closed. Runtime may persist/coordinate canonical decisions but should not reinterpret them independently.

The existing X0 and lifecycle architecture checks always validate current
execution owners, SQLite transaction durability and backup boundaries. These
source checks apply to clean Git checkouts and full working copies equally;
historical checkpoint files do not activate or disable current structural
contracts. Explicit archive and host-qualification commands retain their own
historical-input requirements.

### `services/`

Product use-cases that compose lower-level owners: Fleet, plan resolution/preparation, repository effects, recovery, Schedules, migration, security and related operations. Start from the service named after the user operation, then follow its explicit collaborators downward.

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
## Change impact

After choosing the source owner and matching test owner, use this escalation map. Directory ownership tells you the **default** validation; public-contract changes add the root checks below.

```text
api/          → unit/api + backend integration/api
core/         → unit/core
engine/       → unit/engine + integration/engine when native integration changes
integrations/ → unit/integrations
runtime/      → unit/runtime
services/     → unit/services + integration/services when orchestration boundaries change
cli*.py       → unit/entrypoints
```

Then ask one question: **did the public FastAPI/OpenAPI contract change?** If yes, run `make -C ../../.. generate`, `check-generated`, `test-integration`, and `frontend-check`. If no, keep the change inside backend validation. A core/service change does not trigger generation merely because API code eventually calls it.

CLI syntax/help changes should update the owning user/contributor documentation when command examples change. Repository-layout or owner-map changes are repository-tooling work and must also run root architecture/tooling checks.



## Frozen execution identity

`plan.yml` owns intent, `goon.yml` owns behavior, and the resolver remains the only Plan/Goon interpreter. `goon.lock` records the compact execution fingerprint and exact commits for declared remote Skill sources; ecosystem lockfiles retain dependency truth, Enforcer retains authorization, and Game Tape retains execution evidence. See `docs/goon-lock.md`.
