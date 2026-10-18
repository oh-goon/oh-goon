# Run, configure and extend a Plan

Start with [python/quality](plans/python/quality/README.md): format checking, linting, one selected type checker and pytest. Keep project policy in native tool configuration; use a Plan to choose and order reusable work.

```text
Plan → Goons → Steps → native tools → Run and Game Tape
```

An Atomic Goon contains `steps:`. A Composite Goon contains `goons:`. A Plan contains an ordered `goons:` list. Add work to a Plan by importing a Goon; add commands inside an Atomic Goon. Written order and repeated imports are preserved.

## First local Python run

Use an initialized Oh-Goon installation. Source-checkout users should use the prepared CLI described in the [backend guide](../backend/README.md). Run the following commands from your workload repository root.

Use an existing Git repository. Your project needs a committed `pyproject.toml` and native `uv.lock`. Its selected dependency group must provide pytest, Ruff and the chosen type checker. Choose `pyright` or `ty`; the template does not require both. Prepare that group with native uv:

```sh
uv sync --locked --group dev
oh-goon templates list --kind plan
oh-goon templates show python/quality --kind plan > plan.yml
```

Add the following beside `version:` in your copied Plan to explicitly select local verification:

```yaml
execution:
  purpose: verification
```

The shipped Plan's default is CI/CD. `--standard-local` explicitly executes a verification Plan on your host; it does not provide sandbox isolation. Infrastructure and retained-content workflows have their own execution requirements described in their package READMEs.

Inspect the available inputs, then validate and author the execution lock:

```sh
oh-goon plan inputs plan.yml
oh-goon plan validate plan.yml
oh-goon lock plan.yml
```

Register the folder containing your repository and read its repository ID from `teams[].repository.repository_id` in the scan:

```sh
oh-goon league add-folder ..
oh-goon league scan --json
oh-goon plan run plan.yml --repository-id REPOSITORY_ID --standard-local --wait
```

Replace `REPOSITORY_ID` with the exact ID returned for this repository. A separate Plan does not require a repository-owned `goon.yml`.

A successful run executes the checks and writes `junit.xml` in the selected Python project. A failed native command remains a failed Step with its exit code and logs. Use the returned Run ID to inspect it:

```sh
oh-goon plan status RUN_ID
oh-goon plan steps RUN_ID
oh-goon plan outputs RUN_ID
```

## Change values without editing the Goon

Use a reusable Goon with its defaults by importing it directly. The Plan does not repeat its input declarations or internal forwarding:

```yaml
version: 1
goons:
- python/quality
```

For a fixed Plan-level override, make the change visible at the import:

```yaml
version: 1
goons:
- import: python/quality
  with:
    typechecker: ty
```

Atomic Goons own input types, descriptions, defaults and validation. Plans automatically expose unambiguous, unbound public inputs from their direct imports to `--input` and `--inputs-file`. A fixed `with:` binding is already selected and is not exposed automatically. Composite Goons keep their own public interface and internal forwarding. `plan inputs` shows the effective caller contract. For a project under `backend`, create a `values/` directory and save this as `values/dev.yml`:

```yaml
inputs:
  project: backend
  dependency_group: dev
  typechecker: pyright
```

Select one values file and optionally override individual values on the command line:

```sh
oh-goon plan validate plan.yml --inputs-file values/dev.yml --input typechecker=ty
oh-goon lock plan.yml --inputs-file values/dev.yml --input typechecker=ty
oh-goon plan run plan.yml --inputs-file values/dev.yml --input typechecker=ty \
  --repository-id REPOSITORY_ID --standard-local --wait
```

The explicit CLI value wins over the same name in the file. Unspecified inputs keep the Goon's defaults. Duplicate keys or repeated CLI names are errors. Files contain scalar values under one `inputs:` mapping; keep credentials in explicitly declared secret/environment bindings. There are no layered values files, inheritance or implicit environment overrides.

Use the same input selection when locking and running. Changed execution choices require an explicitly refreshed lock; execution never silently rewrites it. `plan explain plan.yml --inputs-file values/dev.yml --routes` shows recipient routing without exposing supplied values.

Pytest discovery and coverage policy belong in project configuration. For example, with pytest-cov installed:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--cov=my_package --cov-fail-under=85"
```

Ruff, Pyright and ty likewise consume their native project configuration. The Goon does not duplicate their settings.

## Append an existing capability

Append an import to the copied Plan's `goons:` list. To use both Goons' defaults:

```yaml
version: 1
goons:
- python/quality
- python/profile
```

Both Goons expose `project` and `dependency_group`. Their defaults remain independent, so those shared names require explicit routing before a caller can override them. To select one project/group for both:

```yaml
version: 1
goons:
- import: python/quality
  pass: [project, dependency_group]
- import: python/profile
  pass: [project, dependency_group]
```

`pass:` selects recipients for same-name caller values; it is unnecessary for a unique input. Use `with:` for a renamed caller input, a fixed value or a producer output. The Goon keeps its schema and internal bindings. Optional Plan `inputs: [name, ...]` restricts the public interface when needed.

For repeated imports, `as:` names the occurrence:

```yaml
- import: python/typecheck
  as: second-checker
  with:
    project: backend
    dependency_group: dev
    typechecker: ty
```

Validate, inspect the resolved order, refresh the lock and run again. Appending a Goon does not modify the imported package.

## Append your own capability

Create `goons/general/project-check/goon.yml` and a sibling `README.md` headed `# general/project-check`. The executable file can be:

```yaml
version: 1
inputs:
  project:
    type: string
    non_empty: true
    default: .
uv:
  project: ${{ inputs.project }}
  group: dev
steps:
- run: uv run python -m compileall -q src
```

Append its explicit source and fixed project selection to the Plan:

```yaml
- import: general/project-check
  from: ./goons
  with:
    project: backend
```

`from:` selects the exact catalog directory relative to the declaring file. It stays inside the root authoring workspace, which defaults to the root Plan’s directory. Unqualified imports continue to select shipped Goons; project packages do not shadow them. Local packages use the same two-file layout and compiler. Shared Plan storage currently saves only `plan.yml`, so use a file-backed Plan for project-local imports.

Inside an Atomic Goon, append another `run:` entry to `steps:`. Commands execute as argv without an implicit shell. Put program logic in a real project command; do not embed shell or Python programs in YAML.

## Choose the next existing workflow

- [Python standard](plans/python/standard/README.md): explicit project release metadata and quality.
- [Python release candidate](plans/python/release-candidate/README.md): quality and retained distribution artifacts.
- [Combined vulnerability scan](plans/security/cve-combined/README.md): locked Python dependencies and repository vulnerabilities.
- [OCI release/deploy](plans/oci/release-deploy/README.md): quality, immutable image publication and exact Kubernetes rollout verification.
- [Isolated dataflow](plans/advanced/isolated-dataflow/README.md): teaching example for outputs and explicit environment imports.
- [Iris training](plans/ml/iris-training/README.md): a parameterized native project command and model results.

The [Goon guide](goons/README.md) describes capabilities; the [Plan guide](plans/README.md) describes composition. `oh-goon/` packages are this repository's internal verification and release workloads.

## Package and execution contracts

Each package contains exactly `README.md` plus `goon.yml` or `plan.yml`, under `<kind>/<category>/<name>/`. Its identity is `category/name`; README owns the explanation, YAML owns machine facts. No helper scripts or sidecars belong in shipped packages.

See [Goon authoring](../docs/development/GOON_AUTHORING.md), [input resolution](../docs/development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md), [input inspection](../docs/development/TEMPLATE_INPUT_INSPECTION.md), [artifact retention](../docs/development/TEMPLATE_ARTIFACT_RETENTION.md) and the [CI/CD contract](../docs/CI_CD_CONTRACT.md) for their owning contracts. Native tools retain dependency resolution, configuration and result authority.
