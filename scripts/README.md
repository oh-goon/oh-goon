# Repository tooling surface

Repository Python is owned by `backend/pyproject.toml` and lives under
`backend/src/oh_goon/repository_tooling/`. The `scripts/` directory is deliberately
not a second Python package: it contains shell/JavaScript launch support, repository-
tooling tests, and documentation only.

## Canonical Python ownership

```text
backend/pyproject.toml
  -> [project.scripts]
  -> uv run ... <named entrypoint>
  -> backend/src/oh_goon/repository_tooling/...
```

Do not add executable Python modules back under `scripts/`, call `python`/`python3`
directly from Make, shell, Docker, or release tooling, or use `PYTHONPATH` to make a
parallel import surface. If repository Python needs to be executable, add a named
`[project.scripts]` entrypoint and invoke it through `uv`.

The important owners are:

```text
backend/src/oh_goon/repository_tooling/architecture/  structural repository invariants
backend/src/oh_goon/repository_tooling/generation/    OpenAPI/frontend contract generation
backend/src/oh_goon/repository_tooling/release/       release identity and assembly
scripts/tests/                                         repository-tooling regression tests
```

[repository_layout.py](../backend/src/oh_goon/repository_tooling/architecture/repository_layout.py) is the single physical-
layout authority for repository checks. Do not reconstruct top-level ownership paths
independently in individual contracts.

## Golden execution loop

For an architecture/repository-tooling change:

```text
edit the existing pyproject-owned module
-> uv run --offline --frozen --no-sync --project backend pytest -c backend/pyproject.toml scripts/tests/<focused_test>.py
-> make architecture
-> make tooling-check
-> stop
```

Stop on the focused tooling-test failure before running broader gates. If the
generator/schema compiler changed, complete this local loop first, then run
`make generate`, `make check-generated`, `make test-integration`, and
`make frontend-check` because that named contract boundary was crossed.

Do not introduce an agent workflow runner, dependency graph, change manifest, or
second command-classification system. Existing Make targets remain the executable
validation owners. Project Python runs through uv + named pyproject entrypoints; the small
pre-environment repository command surface uses named project entrypoints through `uv run`, never host `python`/`python3`, `PYTHONPATH`, or source-file `uv run --script` launchers.

## Dependency materialization boundary

uv and npm are the dependency owners. Repository setup, release and container surfaces
invoke `uv sync` and `npm ci` directly; Oh-Goon does not mirror their resolution,
interpreter-selection, build-bootstrap, or installation semantics.

Normal dependency materialization invokes native tools once. Oh-Goon does not wrap uv, npm, Playwright, Docker, Kubernetes, Git, or other native tools in a second retry/cache/resolver/lifecycle implementation. Certification may bind and seal the native outputs, but acquisition behavior remains owned by the invoked tool.

Lock authoring remains native: backend uses `uv lock`, frontend uses
`npm install --package-lock-only`, and validation/materialization uses native uv/npm commands.
Oh-Goon does not retain a parallel lock-authoring implementation.

`make test-python-projects` runs the Iris and orders packages' own pytest suites
under their committed native locks with frozen synchronization and explicit development
groups. The orders check selects its ordinary application tests; its optional Airflow
adapter is a separate manual comparison, never an OH-Goon acceptance prerequisite. Disposable environments
stay outside the checkout and are cleaned on completion. The required hosted
integration check and ordinary `make release-check` select this target; canonical
strict integration rings continue to consume their already prepared environment.
