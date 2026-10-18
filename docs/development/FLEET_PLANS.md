# Plans and Goons

Oh Goon has one deliberately small authoring model:

```text
Repository
  -> Plan
  -> Goon
  -> Step
  -> Execution / Verification / Evidence
```

A Plan selects ordered Goons. An Atomic Goon owns ordered Steps. A Composite Goon selects ordered Goons. A Goon never contains both `steps:` and `goons:`. There is no DAG, inheritance, patching, YAML macro system, schema inheritance, or alternative dependency model.

## Shipped template identity

The catalog grammar is exact:

```text
templates/goons/<category>/<name>/{goon.yml,README.md}
templates/plans/<category>/<name>/{plan.yml,README.md}
```

`name` and `description` are not Plan/Goon authoring fields. Shipped packages get identity from `category/name`; Shared Plans get identity from `plan_key`; repository-root execution keeps repository/source provenance separately. `README.md` or the surrounding UI owns human description rather than execution YAML.

## Normal Atomic Goon

```yaml
version: 1
uv:
  group: dev
steps:
- run: uv run pytest
```

`run:` is parsed into an exact argv by the backend and never executes through a shell. Shell operators, redirects, command substitution, multiline shell programs and inline `python -c`/`sh -c` programs are rejected. If a command is complicated, expose a real project command or reusable tool and invoke that.

A normal command Step does not author a timeout, cache key, dependency list, lock check, executor-capacity defaults, or Step ID. The backend owns those execution facts. `id:` is used only when a stable semantic name is useful or another declaration must refer to that Step.

## UV means one locked Python environment

```yaml
version: 1
uv:
  project: backend
  group: dev
steps:
- run: uv run ruff check .
```

`uv:` means the Step consumes one repository-relative locked UV project. `project`
defaults to `.`; use it when the actual `pyproject.toml`/`uv.lock` live in a subdirectory
such as `backend`. A group selects how the same native lock is used; it does not declare
packages. Oh Goon does not parse `uv.lock` to recreate uv validity, locate console
scripts, copy a live `.venv`, or author cache chains. Native uv validates the project,
prepares each isolated strict subject, and executes the command; Oh Goon owns only the
source/sandbox/cache/evidence boundaries around that native lifecycle.

## Composite Goon

```yaml
version: 1
goons:
- python/ruff
- python/test
```

Composition preserves written order and duplicates. Inputs use unique named projection or explicit import-local `pass:`/`with:` bindings on both Plans and Composite Goons. There is no implicit parent-input propagation.

## Plan

```yaml
version: 1
goons:
- python/quality
- security/cve-combined
```

A Plan is an execution/admission boundary and capability roster, not a second implementation schema. It never owns executable Steps, requirements, environment, outputs, dependency lists or copied Goon input schemas.

A plain Plan exposes unique unbound public inputs automatically. Use `with:` at the import to show a fixed override:

```yaml
version: 1
goons:
- import: python/quality
  with:
    typechecker: ty
```

The selected Goon remains the sole owner of type, default and validation. The Plan repeats neither its inputs nor its internal forwarding. Fixed bindings are excluded from automatic caller inputs; shared names need explicit `pass:`/`with:` recipient routing before caller overrides. Optional Plan `inputs: [name, ...]` restricts its public interface. Composite Goons declare their public names and project a unique owner or bind compatible recipients explicitly.

## Explicit dataflow

Normal dataflow has only the concepts that need to cross a boundary:

- Goon inputs are referenced directly.
- Host environment values must be declared and explicitly imported by the Step that needs them.
- Step outputs are referenced directly by later Steps.
- Outputs are declared only when later work consumes them or they are a real published result.

There is no `variables` namespace, `coalesce()` expression language, or Step-output-to-env alias path.

## Requirements

A Goon declares a requirement only when it adds a capability fact that the execution target does not already provide. Empty `requirements: []` is invalid ceremony. Imported-Goon requirements remain local applicability predicates: a missing fact skips that import and its owned Steps rather than becoming a hidden Plan-wide blocker. Plan-owned requirements remain hard admission requirements. Repository validity, Git-root topology, executor-capacity inheritance and command deadlines belong to backend execution/admission boundaries rather than being repeated in Goon YAML. Requirements may refer only to canonical source paths that survive source sanitization, never generated or operational paths such as `.git`, virtual environments, dependency installs, caches, or generated output.

## Typed capabilities

Typed Git, pre-commit, OCI and Kubernetes Steps keep fields that express real capability, credential, mutation, provenance or security boundaries. They are not forced into the simple command shape. Their explicit deadlines may remain part of capability semantics.

OCI/Kubernetes detail is therefore not the introductory authoring model. `python/test`, `python/ruff` and similar command Goons define the normal complexity level.

## Resolution is exact

Authoring simplicity does not weaken execution identity. Backend resolution produces stable Step identities, exact argv, resolved inputs, requirements, provenance, execution environment and artifact digests. `goon.lock`, admission, execution and Game Tape consume that resolved truth; the browser renders it rather than inventing another model.
