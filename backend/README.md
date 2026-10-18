# Backend

Start with `AGENTS.md`. This README is the backend navigation map: it tells you where behavior lives and which local commands are safe to run before you understand the whole repository.

## First minute

From `backend/`:

```bash
make -C .. backend-install
uv run --offline --frozen --no-sync pytest tests/unit/<owner>/<focused_test>.py
uv run --offline --frozen --no-sync ruff check src tests
uv run --offline --frozen --no-sync mypy src tests
```

`backend-install` consumes the committed native uv lock, including the required
Hashmarks dependency. Use root `make init` when working with both backend and
frontend. `make -C .. diagnostic-system-check` is an optional diagnostic after
initialization; it is not a prerequisite for installing the environment.

Before finishing a backend change, run the complete backend owner check from this directory:

```bash
make -C .. backend-check
```

If you add, change, or remove a FastAPI route or public request/response model, return to the repository root and run `make generate`. Never edit generated frontend contract files by hand.

Use this checkout's CLI through its prepared environment:

```bash
uv run --offline --frozen --no-sync oh-goon --help
```

The optional global `cli-install` target builds a standard wheel, which requires
the release owner's generated UI payload. It is not required for backend
development or the browser-first source startup path.

## Source map

```text
src/oh_goon/
├── application.py   public process composition/lifecycle root
├── _application_foundation.py  persistence/execution/safety wiring
├── _application_services.py    Fleet/Schedule/product wiring
├── cli.py           installed CLI entrypoint
├── api/             FastAPI routes, HTTP translation, local web serving
├── core/            domain facts, immutable models, pure policy/validation
├── engine/          native command/git/project execution primitives
├── integrations/    filesystem/process/container/VCS/external effect boundaries
├── runtime/         durable state, lifecycle, scheduling, coordination stores
└── services/        product use-cases that compose lower-level owners
```

The directory name is an ownership clue, not an invitation to add another layer. Put a change in the lowest existing owner that already has the responsibility. For the decision ladder between `core/`, `engine/`, `integrations/`, `runtime/`, `services/` and `api/`, read `src/oh_goon/README.md`.

## Entry points

- `src/oh_goon/cli.py::main` — CLI dispatch.
- `src/oh_goon/api/app.py::create_app` — FastAPI application construction.
- `src/oh_goon/application.py::ApplicationRuntime` — public process/service composition and lifecycle root; private `_application_*.py` modules contain concrete construction wiring.
- `src/oh_goon/engine/runner.py` — native engine action dispatch.

Do not start by reading every service or integration module. Follow the task from the relevant entrypoint or nearest test.

## Tests

```text
tests/unit/<owner>/         focused behavior mirroring the implementation owner
tests/integration/<owner>/  backend integration grouped by api/engine/services
tests/factories/             backend test-data builders
tests/fakes/                 narrow backend test doubles
```

Read `tests/README.md` after choosing a source owner. Root `../tests/integration/` is only for behavior that genuinely crosses top-level owners.

## API contract boundary

The backend owns API semantics. The contract flow is:

```text
backend FastAPI/Pydantic
    ↓
generated/openapi/openapi.json
    ↓
generated/frontend/
    ↓
frontend/
```

Never fix a backend contract bug by hand-editing `../generated/`.
## When to escalate beyond backend-check

Stay backend-local by default. If the public FastAPI/OpenAPI surface changed, run from repository root: `make generate`, `make check-generated`, `make test-integration`, and `make frontend-check`. If repository ownership/layout or its navigation docs changed, also run `make tooling-check` and `make architecture`. See `src/oh_goon/README.md` for the owner-by-owner impact map.


## Golden execution loops

### Backend-internal behavior

```text
edit the existing owner
→ uv run --offline --frozen --no-sync pytest tests/unit/<owner>/<focused_test>.py
→ make -C .. backend-check
→ stop
```

If the focused test fails, fix that failure before running the broad backend gate. A backend-internal change does not regenerate API contracts merely because API code eventually calls it.

### Public API/OpenAPI behavior

```text
edit backend API/model authority
→ uv run --offline --frozen --no-sync pytest tests/unit/api/<focused_test>.py
→ make -C .. backend-check
→ make -C .. generate
→ make -C .. check-generated
→ make -C .. test-integration
→ make -C .. frontend-check
```

Generation comes after the narrow/backend checks are green. Never edit `../generated/` to make this loop pass.

### Deployment-only APP readiness

An integrator that **already owns** a live, enrolled original `FleetPreworkProvisioner`
may call `oh_goon.services.certification_frontdoor.inspect_deployment_certification_readiness`
from the **same Python process**. It returns the existing non-authorizing
`OBSERVED_NOT_ADMITTED` projection, using the same Run/inventory/SQLite context
fences as `certify_deployment_run`. A diagnostic observation must never be
transported as Run admission or reported as APP PASS. The
[certification frontdoor](src/oh_goon/services/certification_frontdoor.py) owns this interface. Ordinary
`make certify` does not accept or acquire a Fleet provisioner.
