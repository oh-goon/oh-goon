# Run, configure and extend a Plan

Start with [python/quality](plans/python/quality/README.md): format checking, linting, one selected type checker and pytest. Keep project policy in native tool configuration; use a Plan to choose and order reusable work.

```text
Plan → Goons → Steps → native tools → Run and Game Tape
```

An Atomic Goon contains `steps:`. A Composite Goon contains `goons:`. A Plan contains an ordered `goons:` list. Add work to a Plan by importing a Goon; add commands inside an Atomic Goon. Written order and repeated imports are preserved.

For bounded agentic work, use [agents/agentic-review-repair-loop](plans/agents/agentic-review-repair-loop/README.md).
Its imports declare `phase:` beside each Goon. The existing repair owner runs
linked native phase Runs with explicit review, write-scope and deadline budgets.
See the [setup and customization example](../examples/agentic-review-repair-loop/README.md).

For a selected native coding agent, [agents/native-coding](plans/agents/native-coding/README.md)
runs the Agent followed by the project's explicit verification script;
[agents/native-review](plans/agents/native-review/README.md) performs one typed
read-only review. Both reuse the existing Plan/lock native selection flags and
require a Bubblewrap profile with managed inference. The native default
`isolated-managed` profile consumes the catalog's explicit `default_model`;
ordinary Plan/Goon local execution defaults remain unchanged. See the
[native configuration guide](../docs/AGENT_PROVIDERS.md#run-a-native-coding-agent).

## First local Python run

Use an initialized Oh-Goon installation. Source-checkout users should use the prepared CLI described in the [backend guide](../backend/README.md). Run the following commands from your workload repository root.

Use an existing Git repository. Your project needs a committed `pyproject.toml` and native `uv.lock`. Its selected dependency group must provide pytest, Ruff and the chosen type checker. Choose `pyright` or `ty`; the template does not require both. Prepare that group with native uv:

```sh
uv sync --locked --group dev
oh-goon templates list --kind plan
oh-goon templates show python/quality --kind plan > plan.yml
```

Plans default to local verification on your host. The CLI waits for completion. Use trusted repositories for this ordinary path. Explicit strict CI/CD and untrusted-code isolation are described in the [execution contract](../docs/CI_CD_CONTRACT.md); workflows that require strict snapshots or retained content declare that policy in their YAML.

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
oh-goon plan run plan.yml --repository-id REPOSITORY_ID
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

That binding fixes `typechecker` to `ty` for this Plan. A CLI or values-file entry cannot replace it. For a per-run choice, keep the plain import or bind the child input to a named caller input as shown below.

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
  --repository-id REPOSITORY_ID
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

`oh-goon plan inputs plan.yml` lists any shared inputs that still need routing separately from the accepted caller inputs. `plan explain plan.yml --routes` shows the resolved recipients after you select the required values.

## Give repeated imports independent values

`as:` names one occurrence. Bind its child inputs to distinct caller names when each occurrence needs a different per-run choice:

```yaml
version: 1
goons:
- import: python/typecheck
  as: api
  with:
    project: backend
    typechecker: ${{ inputs.api_checker }}
- import: python/typecheck
  as: tooling
  with:
    project: tools
    typechecker: ${{ inputs.tooling_checker }}
```

The projects are fixed; `api_checker` and `tooling_checker` inherit their contracts and defaults from the Goon. Select them independently without repeating input schemas:

```sh
oh-goon plan validate plan.yml --input api_checker=ty --input tooling_checker=pyright
oh-goon lock plan.yml --input api_checker=ty --input tooling_checker=pyright
```

Both imports use the native default dependency group. Route `dependency_group` explicitly if callers should change it. Use the same input flags when running. Moving an aliased import keeps its name and bindings together.

## Change the order of work

Move entries in `goons:` to change their execution order. To customize the internal order of a Composite such as `python/quality`, select its Atomic Goons directly. This Plan runs tests before formatting, linting and type checking, with one shared project/group selection:

```yaml
version: 1
goons:
- import: python/test
  pass: [project, dependency_group]
- import: python/ruff
  pass: [project, dependency_group]
- import: python/typecheck
  pass: [project, dependency_group]
```

```sh
oh-goon plan validate plan.yml --input project=backend --input typechecker=ty
oh-goon lock plan.yml --input project=backend --input typechecker=ty
```

The unique `typechecker` input needs no forwarding. For different fixed projects or tool choices, set `with:` on the corresponding import instead of routing that input with `pass:`. Keep each producer before consumers of its outputs or workspace; an invalid order fails validation. Oh-Goon preserves the order you wrote.

Append another import wherever it should run. Validate the new order, inspect routing, refresh the lock and run with the same inputs. The imported packages keep their own definitions.

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

See [Goon authoring](../docs/development/GOON_AUTHORING.md), [input resolution](../docs/development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md), [input inspection](../docs/development/TEMPLATE_INPUT_INSPECTION.md), [artifact retention](../docs/development/TEMPLATE_ARTIFACT_RETENTION.md), [network settings](../docs/development/TEMPLATE_NETWORK_AUTHORITY.md) and the [CI/CD contract](../docs/CI_CD_CONTRACT.md) for their owning contracts. Native tools retain dependency resolution, configuration and result authority.

The [catalog identity guide](../docs/development/TEMPLATE_CATALOG_DETERMINISM.md) explains how native discovery binds template contents and catalogue identity.
