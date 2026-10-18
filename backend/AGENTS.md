# Backend agent guide

Read root `../AGENTS.md` first for repository-wide rules, then `README.md` for the backend owner map and `src/oh_goon/README.md` when choosing an internal implementation owner. This file adds only backend-local ownership details.

`backend/` owns Python product behavior: domain models, API, execution/runtime semantics, repository safety, persistence coordination, validation, CLI, and backend-only integration tests.

## Work here when

- changing API routes or backend response models;
- changing Goon/Plan resolution or execution semantics;
- changing admission, repository safety, isolation, persistence, recovery, Fleet, Game Tape backend facts, or CLI behavior;
- adding/changing Python capabilities or integrations;
- adding backend unit/API integration tests.

## Layout

```text
backend/
├── src/oh_goon/       # Python package
├── tests/unit/<owner>/ # focused behavior mirroring source ownership
├── tests/integration/<owner>/ # backend integration by api/engine/services
├── tests/factories/
├── tests/fakes/
└── pyproject.toml      # backend package + Python tooling authority
```

Keep imports as `from oh_goon...`; never import `backend.src...`.

## Backend boundaries

- Runtime code must not depend on frontend implementation.
- Frontend contract facts originate in backend API/OpenAPI, never the reverse.
- Do not read `generated/frontend/` from runtime code.
- Shipped template content belongs in `templates/`, not backend source.
- Production-template validation belongs in repository architecture/tests; backend runtime must not infer a hidden maturity tier from YAML. Catalog classification is a path-derived backend projection: python/security/oci/kubernetes are production, advanced/general/ml are teaching examples, and oh-goon is internal self-hosting. Unknown shipped categories fail classification; execution YAML carries no maturity field.
- Repository architecture/generation/release tooling uses `scripts/` launchers and `scripts/tests/`; its Python implementation belongs in `backend/src/oh_goon/repository_tooling/` through named `pyproject.toml` entrypoints. Runtime owners must not depend on repository tooling.
- Plan/Goon YAML is the authoring boundary. Read/parse once, reject duplicate keys, and reuse that syntax tree for compilation + source positions. Shipped package path owns identity, README owns human description, and shipped Goon YAML must not repeat either field; `CompiledExecutionTemplate`/`CompiledGoonImportStep` stay internal and runtime consumes `ResolvedFleetPlan`.
- Shared Runs own explicit multi-repository Plan execution. Do not move repeated repository scope or `all_repositories` into Schedule definitions.
- `ScheduleService` owns the public Schedule model: an explicit repository target set, one resolved Plan and an active/paused wall-clock interval. Each target keeps independent Run truth. `ScheduleScheduler` owns only periodic invocation; process startup is not a trigger.
- Schedule persistence is Schedule-specific (`ScheduleDefinitionStore`, `ScheduleExecutionStore`, and structural Schedule states). Do not recreate `Workflow*` models/stores, a generic workflow API/composer, scheduler steps, or a second execution graph; executable behavior remains Plan → Goon → Step and Shared Runs remain the many-repository path.

## API changes

1. Change the backend route/model authority.
2. Run `make generate` from repository root.
3. Update authored frontend only if public-contract consumption changes.
4. Run `make check-generated` and the relevant owner checks.

Never manually edit `generated/openapi/` or `generated/frontend/`.

## Backend tests

Backend unit tests mirror implementation ownership under `backend/tests/unit/<owner>/`. Backend-only HTTP/FastAPI integration remains under `backend/tests/integration/api/`.
Use root `tests/integration/` only when behavior genuinely crosses top-level owners.
Repository-tooling tests belong in `scripts/tests/`.

## Commands

From `backend/`, the ordinary local loop is:

```text
uv run --offline --frozen --no-sync pytest tests/unit/<owner>/<focused_test>.py
uv run --offline --frozen --no-sync ruff check src tests
```

Before finishing backend work, run `make -C .. backend-check`. Use `make -C .. architecture` when repository ownership/contracts changed.

## Backend-specific safety

- Repository/path isolation is security behavior, not a convenience check.
- Local API authorization is owned once by `api/local_access.py`; IDs are never credentials, services must bind child/object IDs to canonical parent/repository authority, and the local-access capability must never enter execution subprocess environments.
- Keep physical execution authority explicit.
- A revision-bound safety proof is valid only for the exact durable revision it observed.
- Treat a revision mismatch as a stale proof; never silently reuse it.
- Read one runtime safety cut and bind admission to that exact observation rather than rereading independent tables.
- Authored repository paths must use current locations such as `backend/src/oh_goon/...`; package-internal resources remain package-relative.

Finish with the six-field **Completion evidence** report defined by root `AGENTS.md`; incomplete or timed-out checks stay `unverified`, never `passed`.
