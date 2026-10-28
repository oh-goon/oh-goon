# Templates and first runs

Choose an existing Plan for your repository path, edit one `run.yml`, lock it and run it. The Plan/Goon graph owns the work; this file selects the target and caller choices.

Use the [CLI-only setup](../README.md#cli-first--no-frontend-setup). Source-checkout users run `make backend-install` and use the prepared command described there; no frontend build is required. When working outside that checkout, give its command prefix an absolute `--project /path/to/oh-goon/backend`. Built releases use the installed `oh-goon` command. Register the workload directory once:

```sh
oh-goon league add-folder /absolute/path/to/project
```

Use `oh-goon templates list --kind plan` to browse workflows. Catalog usage labels distinguish runnable workflows with prerequisites, teaching examples and internal owner commands. Listing a template does not execute it or check prerequisite availability; human and JSON output report those unverified states explicitly.

Initialization selects the registered repository with `--repository PATH` and fills its canonical identity. No scan or manual ID copying is needed. Registration remains explicit; `run.yml` does not register a directory.

## First local Python run

Your trusted workload needs a committed `pyproject.toml` and native `uv.lock`. Its selected group must provide Ruff, pytest and your chosen Pyright or ty checker. Prepare that group with native uv in the selected project, then initialize a new workflow directory:

```sh
uv sync --frozen --group dev
oh-goon templates init python/quality --kind plan --output ./workflow --repository /absolute/path/to/project
```

Run native uv from the workload project. The initializer refuses an existing destination. It publishes `run.yml` only after every generated companion is complete. An interrupted output without `run.yml` is incomplete; initialization never overwrites it or removes user files. Choose a fresh output directory after a failed generation. Keep the generated target and edit only the input values you need in `workflow/run.yml`, for example:

```yaml
inputs:
  typechecker: ty
```

This example changes the checker to ty; the default project and group remain `.` and `dev`. A project under `backend` uses `inputs.project: backend`. Keep credentials outside this file.

```sh
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

Run waits for completion and returns a nonzero exit code when the declared work fails. Locking and running invoke the existing validation owners. Optional `oh-goon config inspect ./workflow/run.yml` shows input names, defaults and constraints; `oh-goon config validate ./workflow/run.yml` checks caller values without starting a Run. Neither proves installed tools or successful execution.

Lock freezes the selected source/inputs and captured Goon/Plan and Skill identities. After changing `run.yml`, execution policy, Goon behavior, or captured application code and its metadata/native lock, review the change and run `oh-goon lock ./workflow/run.yml` again. Ordinary relocking retains existing Git Plan/Goon, application and Skill commits. Refresh them only when intended with `--update-goons`, `--update-applications` or `--update-skills`. See [lock diagnostics and evidence limits](../docs/development/TEMPLATE_INPUT_INSPECTION.md#locking-and-actionable-refusals).

Ordinary verification uses the trusted local path. Explicit CI/CD templates retain their declared strict executor and content-store requirements; see the [execution contract](../docs/CI_CD_CONTRACT.md). A generated config never grants write authority, network access or certification.

Successful quality execution records the native checks and `junit.xml` in the selected project. Completion prints the outcome, failed Steps when present, artifact paths and evidence, and commands for inspecting that Run. Use its returned Run ID to reopen recorded results:

```sh
oh-goon plan status RUN_ID
oh-goon plan steps RUN_ID
oh-goon plan outputs RUN_ID
oh-goon plan handoff RUN_ID
```

These commands reopen durable Run and Game Tape evidence after the execution process exits. `plan outputs` shows output hashes, sizes and retained references when present; it does not download files. `oh-goon plan logs RUN_ID` reads logs only while their transient retention remains available. An `evidence` artifact declaration records a file's identity without retaining its bytes; see the [retention contract](../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).

## Change values without editing the Goon

Edit only `run.yml` for caller choices. Atomic Goons own input types, defaults, choices and constraints. A required plain string can be written as `label:`; a default string as `label: example`. Use a mapping when a type, description or constraint adds information. Plans expose unambiguous unbound direct inputs, while fixed `with:` bindings remain fixed. Shared names need explicit recipients in the Plan/Composite; `config inspect` shows the selected contract.

Native configuration stays native. Put pytest discovery/coverage in `pyproject.toml`; Ruff, Pyright and ty read their own project settings. Pi/OpenCode/Codex retain model window, reasoning, compaction, tools, authentication and personal configuration. Provider connections and OH-Goon profiles belong in the provider catalog; remote Skill sources belong in `.goonskills.yml`.

## Append an existing capability

Keep unchanged catalog Plans selected directly; no redundant copy is required. For a different roster, create `workflow/plan.yml` using ordinary Plan syntax:

```yaml
goons:
- import: python/quality
  pass: [project, dependency_group]
- import: python/profile
  pass: [project, dependency_group]
```

Then select that file in `workflow/run.yml`:

```yaml
version: 1
source: {kind: file-plan, path: plan.yml}
target: {repository_id: REPOSITORY_ID}
inputs:
  project: backend
  typechecker: ty
```

Retain the target block filled by initialization. The contained Plan path is relative to the workflow root. `pass:` deliberately routes the shared names to both Goons. `with:` binds fixed values, renamed caller values or prior producer outputs; it does not repeat the child schema. Refresh the lock and run the same selected file; inspect or validate it separately when useful.

## Give repeated imports independent values

`as:` names one occurrence. This Plan keeps two project choices fixed and exposes independently typed checker choices:

```yaml
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

Select it with `source: {kind: file-plan, path: plan.yml}` and set `inputs.api_checker: ty` and `inputs.tooling_checker: pyright` in `run.yml`. Written duplicate imports and order are preserved. Route a shared dependency group explicitly if the caller should change it.

## Change the order of work

Move the Plan’s `goons:` entries. To change the order inside a Composite, select its Atomic Goons in your own Plan:

```yaml
goons:
- import: python/test
  pass: [project, dependency_group]
- import: python/ruff
  pass: [project, dependency_group]
- import: python/typecheck
  pass: [project, dependency_group]
```

Keep producers before consumers of their outputs/workspaces. Invalid ordering fails validation. The compiler preserves the order you wrote.

## Append your own capability

Create `workflow/goons/general/project-check/{goon.yml,README.md}`. The README heading is `# general/project-check`; the Atomic Goon can be:

```yaml
inputs:
  project:
    non_empty: true
    default: .
uv:
  project: ${{ inputs.project }}
  group: dev
steps:
- run: uv run python -m compileall -q src
```

Append an explicit local import to the contained Plan:

```yaml
- import: general/project-check
  from: ./goons
  with:
    project: backend
```

`from:` selects a catalog directory within the root authoring workspace. Local packages never shadow shipped imports. Shared Plan storage owns only its Plan file, so use a file-backed Plan for local packages. Commands execute as argv without an implicit shell; keep program logic in real project commands.

## Carry a reusable Python implementation

For an Atomic Goon that owns its application, put a complete Python project
beside `goon.yml`: `pyproject.toml`, committed native `uv.lock`, implementation,
resources and tests. Declare its installed command in `[project.scripts]`:

```yaml
runtime: python
inputs:
  label: example
steps:
- run: project-check --label ${{ inputs.label }}
```

Develop the package directly with `uv sync`, `uv run pytest` and
`uv run project-check --label example`. During execution, native uv installs
the captured project and its runtime dependencies with no default dependency
groups in an owned environment. Select `uv: {group: dev}` only when the workload
requires that group. Package runtime rejects `uv.project`; the selected target
repository does not supply its implementation. Execution leaves the developer's
`.venv` alone and writes relative outputs into attempt-owned working state.
Source, project metadata, native lock and resources bind the execution lock;
generated environments, caches and declared output locations do not.
The [Iris Goon](goons/ml/iris-train/README.md) is a complete shipped example.
For an application maintained separately from its Goon, select one literal
`application` local path or exact Git source. The existing captured-project and
Step owners deliver it without requiring Goon files in the application. Native
environment selection stays explicit; the actual registered effect target stays
separate. See the [independent application walkthrough](../examples/independent-application/README.md).
An explicit strict `trust_repository_code: false` runs native installation and
package builds inside the selected Step isolation boundary. Captured source
stays read-only; a verified private build copy and non-editable environment live
in attempt-owned cache state. The Step's environment, network and resource
policy applies during preparation. Untrusted attempts use an empty owned uv
cache. The current remote provider requires a deny-all VM, so native uv must
find its dependencies within the captured project or its own bundled backend;
host caches and ambient credentials are not inherited. Missing isolation
prerequisites or unavailable dependencies fail without a host-build fallback.
Ordinary trusted execution keeps its existing preparation behavior.

## Select pinned Git source

Import one exact package into a local Plan:

```yaml
goons:
- import: general/project-check
  from:
    git: https://github.com/your-org/workflows.git
    ref: main
    path: goons/general/project-check
```

`path` contains `goon.yml` and ends in its exact `category/name`. To use a
complete example, select its authoring root and Plan in `run.yml`:

```yaml
version: 1
source:
  kind: git-plan
  git: https://github.com/your-org/workflows.git
  ref: main
  path: examples/orders
  plan: plan.yml
target: {repository_id: REPOSITORY_ID}
```

The Plan's contained local imports come from that same revision. `lock` acquires
source and pins the commit; ordinary lock regeneration and runs retain it,
including after cache loss. Use `oh-goon lock run.yml --update-goons` to refresh
Goon/Plan pins explicitly. Application refresh uses `--update-applications`;
Skill refresh remains `--update-skills`. Inspection
uses only cached source and reports acquisition is required when unavailable.
Acquisition does not execute project code or install dependencies.

## Select a different source or domain

A selected source is exactly one of `catalog-plan` with `name`, `file-plan` with contained `path`, `repository-goon` with neither, or `git-plan` with explicit `git`, `ref`, authoring `path` and contained `plan`. Repository mode selects the exact `goon.yml` at the registered repository root and never merges it with a Plan. There are no layered values files, implicit environment substitution or alternate normal launch flags.

| Start here | Meaning and prerequisites |
| --- | --- |
| [Python inventory](plans/python/dependency-inventory/README.md) | Observe manifests; no dependency installation. |
| [Python quality](plans/python/quality/README.md) | Trusted local native checks and JUnit evidence. |
| [Python release candidate](plans/python/release-candidate/README.md) | Project metadata/packaging commands and retained artifacts. |
| [OCI image](plans/oci/image/README.md) | Build and retain; base-pull registry only. |
| [OCI publish](plans/oci/publish-image/README.md) | Separate destination and declared credential bindings. |
| [OCI deployment](plans/oci/release-deploy/README.md) | Publish exact digest, mutate and verify exact generation. |
| [Kubernetes mutation](plans/kubernetes/set-image/README.md) | Digest-qualified update; no standalone readiness claim. |
| [Vulnerability scan](plans/security/cve-combined/README.md) | Native scanners, declared egress and report retention. |
| [Iris training](plans/ml/iris-training/README.md) | Trusted-local package training with retained metrics/model export; strict execution remains explicit. |
| [Isolated dataflow](plans/advanced/isolated-dataflow/README.md) | Teaching workflow requiring explicit project scripts/token. |
| [Native review](plans/agents/native-review/README.md) | Installed native agent, compatible profile and captured Skill. |
| [Review–repair](../examples/agentic-review-repair-loop/README.md) | Ordered reviews and explicitly bounded admitted repairs. |

`oh-goon/*` packages are internal verification/release capabilities. Use their owning Make/certification routes. The [example index](../examples/README.md) separates runnable workflows, external prerequisites, previews and integration fixtures.

## Package and execution contracts

Plan/Goon source omits the default version, verification purpose and string type. A selected `run.yml` retains its own required version. Keep explicit policies when they change execution, such as `purpose: cicd`, and explicit bindings when several children consume the same caller choice.

Each shipped Plan, Composite Goon and tool-invoking Atomic Goon contains exactly `README.md` plus `goon.yml` or `plan.yml`, under `<kind>/<category>/<name>/`. An Atomic Goon with `runtime: python` also owns its complete standalone Python project. Identity is `category/name`. Plans own ordered imports; an Atomic Goon owns `steps:`, a Composite Goon owns `goons:`, never both. YAML owns machine facts; README explains them.

See [Plan composition](plans/README.md), [Goon capabilities](goons/README.md), [Goon authoring](../docs/development/GOON_AUTHORING.md), [input resolution](../docs/development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md), [input inspection](../docs/development/TEMPLATE_INPUT_INSPECTION.md), [retention](../docs/development/TEMPLATE_ARTIFACT_RETENTION.md), [network authority](../docs/development/TEMPLATE_NETWORK_AUTHORITY.md) and [catalog identity](../docs/development/TEMPLATE_CATALOG_DETERMINISM.md).
