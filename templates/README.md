# Templates and first runs

Initialize an existing Plan for your repository, then run the generated workflow.
The Plan/Goon graph owns executable work; `run.yml` selects source, target and
caller choices. The [configuration guide](../docs/development/TEMPLATE_INPUT_INSPECTION.md)
owns schema, path resolution, effect and failure semantics.

Use [CLI-only setup](../README.md#cli-first--no-frontend-setup). Source-checkout
users run `make backend-install` and use the prepared uv command there; a frontend
build is not needed. Outside that checkout, give uv an absolute
`--project /path/to/oh-goon/backend`. Built releases use `oh-goon` directly.

Browse with `oh-goon templates list --kind plan`. Catalog usage labels distinguish
runnable workflows with prerequisites, teaching examples and internal commands.
Listing does not establish prerequisites or execution.

## First local Python run

The trusted workload needs `pyproject.toml`, native `uv.lock`, and a dependency
group supplying Ruff, pytest and the selected Pyright or ty checker. From that
project, prepare its environment and initialize:

```sh
uv sync --frozen --group dev
oh-goon init --template python/quality --repository /absolute/path/to/project --no-input
oh-goon run ./workflow --no-input
```

Initialization validates inputs, registers only that exact Git root when needed,
and publishes `workflow/run.yml` with its first canonical `goon.lock`. It refuses
any existing destination. `--kind plan` and `--output workflow` are the defaults;
`--kind goon` explicitly selects a single Goon.

Supply nondefault choices during initialization with `--input typechecker=ty`
or `--input project=backend`. Native tool configuration stays in the project.
`--input-null NAME` means explicit null; ordinary `--input NAME=null` remains
literal text for the compiler. JSON/no-input/non-TTY invocations never prompt.

Existing registered filesystem subjects remain usable. Target selection uses
an exact root path, without upward Git search or descendant enrollment. For
incomplete Agent/repair authoring, use explicit `init --draft`; it reports
NOT_READY and omits the lock. Complete required fields and prepare before Run.

After reviewing later selection/source changes:

```sh
oh-goon workflow prepare ./workflow
oh-goon run ./workflow
```

Optional `workflow inspect` and `workflow validate` expose typed contracts and
refusals without execution. Default reads do not register, initialize state,
write locks or acquire remote sources; acquisition requires `--acquire`.
Preparation retains existing exact pins. Refresh only intended kinds with
repeated `--refresh goons`, `--refresh applications` or `--refresh skills`.
A fresh CI host explicitly selects `workflow prepare --register-target`.
Run never registers a target, refreshes a ref or rewrites its lock.

Run waits for the original terminal outcome and returns nonzero for failed work.
Completion shows recorded Step counts, failures, outputs/artifacts and readback
actions. Reopen the exact returned Run ID:

```sh
oh-goon runs show RUN_ID
oh-goon runs steps RUN_ID
oh-goon runs outputs RUN_ID
oh-goon runs handoff RUN_ID
```

These reopen original Journal/Game Tape facts. `runs outputs` reports evidence
and retained references; `runs export-artifact` retrieves retained bytes through
the existing owner. `runs logs` reports transient retention, including unavailable
output. See [retention](../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).

## One Plan over several applications

Repeat `--repository` to initialize one explicit target set. Each selected Git
root can be registered by initialization; filesystem subjects without Git must
already be registered through the existing League owner:

```sh
oh-goon init --template python/quality --repository /absolute/path/to/application-one --repository /absolute/path/to/application-two --no-input
oh-goon run ./workflow --no-input
```

The initializer writes `target.repositories` for the exact selected path set. A
single path uses `target.repository`. These fields are exclusive; the plural
form requires at least two distinct roots. Both resolve relative paths from
the selected `run.yml` directory. Internal repository IDs and instance fences
remain with the original Fleet owners. Each application must satisfy the
same Plan and selected input values; project-relative native configuration is
resolved for each target. The original Fleet scheduler owns parallelism,
failure and cancellation, and completion/readback reports one Run containing
all selected repositories.

Catalog, file and Git Plans support this explicit set. Repository Goons, Agent
and repair workflows retain their single-workspace contract. Duplicate,
unavailable or excluded members refuse; preparation without explicit registration
also refuses an unregistered member. The CLI does not silently reduce the set.
Git-free applications require exact registration of each root. Registration
validates the full selection before its first effect and reports any committed
registrations if a later operation fails. The selected execution policy continues to own resource guarantees;
ordinary verification currently has no aggregate Run CPU/RAM budget.

## Change values without editing the Goon

Edit only `run.yml` for caller choices. Atomic Goons own input types, defaults, choices and constraints. A required plain string can be written as `label:`; a default string as `label: example`. Use a mapping when a type, description or constraint adds information. Plans expose unambiguous unbound direct inputs, while fixed `with:` bindings remain fixed. Shared names need explicit recipients in the Plan/Composite; `workflow inspect` shows the selected contract.

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
version: 2
source: {kind: file-plan, path: plan.yml}
target: {repository: /absolute/path/to/project}
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
version: 2
source:
  kind: git-plan
  git: https://github.com/your-org/workflows.git
  ref: main
  path: examples/orders
  plan: plan.yml
target: {repository: /absolute/path/to/project}
```

The Plan's contained local imports come from that same revision. `workflow prepare` acquires
source and pins the commit; ordinary lock regeneration and runs retain it,
including after cache loss. Use `oh-goon workflow prepare run.yml --refresh goons` to refresh
Goon/Plan pins explicitly. Application refresh uses `--refresh applications`;
Skill refresh remains `--refresh skills`. Inspection/validation use cached source
by default; `--acquire` explicitly permits source acquisition.
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
