# ADR — Complete Python Goon projects

**Status:** Implemented, 2026-10-25.
**Scope:** Atomic Goons carrying their own Python implementation, from local
packages or explicit pinned Git selections.

## Decision

A Python Goon directory is a complete standalone Python project. A developer
can enter it, use native uv, run its tests and invoke its installed command.
Oh-Goon installs the same captured implementation and runs the same entry point
through its existing Fleet/Step lifecycle.

The Plan remains small:

```yaml
execution:
  purpose: cicd

goons:
- ml/iris-train
```

The Goon owns typed public inputs, ordered Steps and artifacts.
`pyproject.toml` owns runtime dependencies, development tools, build
configuration and installed entry points. `uv.lock` remains native Python
dependency authority. `run.yml` supplies caller values and invocation selection.
`goon.lock` freezes execution identity and explicit Git source pins.

This project contract applies to Atomic Goons carrying implementation.
Composite Goons and Goons invoking tools in a selected target project need no
dummy Python projects. Plans still order Goons; Atomic Goons own Steps;
Composite Goons own Goons. There is no additional runner or execution graph.

## Complete project ownership

The canonical Iris project is [templates/goons/ml/iris-train/](../../templates/goons/ml/iris-train/README.md):

| Member | Responsibility |
| --- | --- |
| `goon.yml` | Typed public inputs, ordered Steps and declared artifacts. |
| `README.md` | Purpose, local development, command usage, inputs and outputs. |
| `pyproject.toml` | Runtime dependencies, development groups, installed command and build backend. |
| `uv.lock` | Committed native uv dependency resolution. |
| `src/iris_train/` | Installed command and training implementation. |
| `tests/` | Training, validation and output regressions. |

`goon.yml`, `README.md`, `pyproject.toml`, `uv.lock` and the implementation and
resources needed to install the command are required. Native build
configuration owns valid Python layouts. The README retains the exact catalog
heading, such as `# ml/iris-train`. Plan, Composite Goon and tool-invoking Atomic
Goon packages retain their two-file YAML/README layout.

The package installs after relocation without a certification fixture,
Oh-Goon backend, cookbook checkout or parent source. Local dependencies must
remain inside the project; escaping paths and reliance on an ancestor uv
workspace are outside this standalone model. Native uv owns dependency
resolution, build/install semantics and stale-lock refusal.

The existing source-snapshot and Hashmarks owners capture authored package
contents. Generated environments/caches, declared output locations and
`goon.lock` are excluded. Local sync, tests and training therefore do not add
those generated files to package identity. There is no separately authored
file manifest or second package-content hash engine.

## Explicit inputs and installed commands

The Iris Atomic Goon selects its own project:

```yaml
runtime: python
inputs:
  random_seed:
    type: integer
    default: 42
    min: 0
    max: 2147483647
  test_size_percent:
    type: integer
    default: 20
    min: 10
    max: 40
  minimum_accuracy_percent:
    type: integer
    default: 90
    min: 80
    max: 100
  max_iterations:
    type: integer
    default: 300
    min: 100
    max: 2000
steps:
- run: >-
    iris-train
    --random-seed ${{ inputs.random_seed }}
    --test-size-percent ${{ inputs.test_size_percent }}
    --minimum-accuracy-percent ${{ inputs.minimum_accuracy_percent }}
    --max-iterations ${{ inputs.max_iterations }}
  artifacts:
  - path: ml-output/iris-training-metrics.json
    retention: evidence
  - path: ml-output/iris-model-parameters.json
    retention: content_store
```

`runtime: python` selects the project beside `goon.yml`, independently of the
target repository. Commands resolve in its prepared environment and execute as
argv; there is no implicit shell or ambient executable fallback. `uv.project`
cannot redirect a package-owned runtime. Use `uv: {group: dev}` only when the
workload needs that dependency group.

The existing binder applies types, defaults and constraints before workload
execution. Plans expose unique unbound inputs and keep explicit `with:`/`pass:`
routing. A caller changes values in `run.yml` without repeating the schema:

```yaml
inputs:
  random_seed: 7
  minimum_accuracy_percent: 95
```

The Iris CLI requires explicit arguments, so workflow defaults stay in the
Goon. Python owns application validation and training logic. Secrets retain
their existing explicit environment/secret authority boundary.

## One dependency definition, distinct selections

The Iris project declares scikit-learn under `[project].dependencies`,
`iris-train` under `[project.scripts]`, pytest/Ruff under the `dev` dependency
group and its build backend under `[build-system]`. It needs no empty `ml` or
`runtime` group. Its native lock can include development tools without installing
them for training.

| Use | Selection |
| --- | --- |
| Local development | Project, runtime dependencies and default `dev` group. |
| Package runtime with no `uv.group` | Project and base runtime dependencies; no default groups. |
| Explicit `uv: {group: dev}` | Project, base dependencies and the requested development group. |

Local development uses the same installed command:

```sh
uv sync --locked
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync iris-train --random-seed 42 --test-size-percent 20 --minimum-accuracy-percent 90 --max-iterations 300
```

Package preparation has native semantics of
`uv sync --locked --no-default-groups --no-editable`, adding `--group` only
when selected. The original execution owners prepare and consume that
selection; authors add no dependency-install Step. Base-only selection is
also supported for existing target-project `uv` workloads. Execution does not
run an unqualified `uv run` that could restore default groups, and does not use
`--only-group`, which would omit the application project.

The runtime environment is separately owned; execution neither reuses nor
prunes the developer's `.venv`. Build requirements remain in the native project
configuration. `uv.lock` alone is not a claim that every isolated build
requirement is pinned. See native uv [syncing](https://docs.astral.sh/uv/concepts/projects/sync/)
and [project configuration](https://docs.astral.sh/uv/concepts/projects/config/).

## Captured source and separate execution roots

| Root | Meaning |
| --- | --- |
| Package source | Captured code, project metadata, native lock and authored resources. |
| Target repository | Explicit registered workload subject; never inferred from a package cache. |
| Runtime environment | Owned native installation; separate from package source and developer environment. |
| Attempt working/output root | Writable execution state and declared produced artifacts. |

Relative package outputs resolve under attempt working state, not the source
cache or author checkout. Target-project Goons retain their explicit target
semantics. Strict managed execution exposes captured package source read-only.

The original owners perform one lifecycle:

1. Capture the exact selected source, compile the graph and bind caller inputs.
2. Bind complete package identity and dependency selection into resolved Steps
   and existing execution-lock freshness authority.
3. Deliver the bounded captured package through existing run/content storage.
   The receiver verifies that identity instead of reopening live author source.
4. Prepare the native environment and execute the installed command through
   the original Step/process owner, preserving failure, cancellation and cleanup.
5. Retain declared artifacts and report the real result through Game Tape.

Hashmarks retains source-content authority. Catalog `sha256` still hashes YAML
bytes only; it is not redefined as a package digest. Complete-project identity
travels separately in the resolved command contract. Edits to implementation,
resources, metadata or native lock invalidate stale execution even when
`goon.yml` is unchanged. Local uncommitted source is identified by the captured
working files rather than represented as clean Git HEAD.

The Iris Plan keeps `purpose: cicd`, existing artifact retention and strict
execution requirements. Ordinary package execution gains no global strict-host
qualification requirement. See [execution first](ADR_EXECUTION_FIRST_PROPORTIONAL_SECURITY.md).

Package-owned Python runtime currently supports trusted package code. Native
build/install preparation runs on the host before isolated application Steps.
An explicit strict `trust_repository_code: false` therefore refuses during Plan
resolution, with a defensive preparation check, before a package build starts.
Isolated preparation for untrusted Python packages is not implemented. The
default trusted policy and ordinary local execution keep their existing behavior;
successful preparation does not establish APP or strict/native qualification.

## Explicit pinned Git acquisition

One reusable package can be imported into a local Plan:

```yaml
goons:
- import: general/project-check
  from:
    git: https://github.com/your-org/workflows.git
    ref: main
    path: goons/general/project-check
```

`path` is the exact package directory containing `goon.yml` and ends with its
`category/name`. It is not a catalog search root. Omitted `from:` still means
the shipped catalog; local and remote packages do not shadow it.

A complete example selects both its authoring root and exact Plan in `run.yml`:

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

The Plan and its contained local imports come from the same selected revision.
Selecting one Goon never searches for a nearby Plan. The target remains an
explicit registered repository.

Native Git preparation, cache leases and temporary-worktree lifetime are
shared with [Skill sources](../AGENT_SKILLS.md#select-a-skill). Goons remain
Goons; `.goonskills.yml` remains the separate Skill source contract.

- The first `lock` explicitly acquires source and records origin, requested ref,
  selected path and resolved commit in `goon_sources`. Complete examples also
  record their Plan selection.
- Ordinary relocking and runs retain exact commits. A moving branch/tag or
  cache miss never advances a locked selection. Cache recovery may acquire the
  exact pinned commit; unavailable content fails clearly.
- `lock run.yml --update-goons` explicitly refreshes Goon/Plan source pins.
  `--update-skills` refreshes Skill pins independently.
- Inspection and validation use cached source only; unavailable source reports
  acquisition is required without hiding a network fetch.
- Acquisition does not execute project code, install Python dependencies,
  initialize submodules or launch Steps. Build/install stays at the original
  execution boundary.
- Native Git checks indexed attributes before source materialization. Selected
  configured smudge/process filters and required filters refuse acquisition;
  source is never silently replaced with unfiltered bytes. Native credentials
  and unrelated configuration retain their original Git authority.
  Acquisition disables hooks and filesystem-monitor callbacks for its Git commands.
- Capture closes temporary worktrees on success/failure while respecting cache
  leases. Cache paths remain disposable locations rather than source identity.
  Execution consumes captured bytes instead of refetching a selector or reading
  a mutable cache directory.

See [run configuration](../development/TEMPLATE_INPUT_INSPECTION.md),
[Goon authoring](../development/GOON_AUTHORING.md) and [goon.lock](../goon-lock.md)
for the current public contract.

## Verification boundaries

Focused regressions cover standalone package installation, real installed
command execution/readback, base-only versus explicit development groups,
developer-environment preservation, complete-project freshness, repeated
imports, distinct outputs, missing/escaping source and native lock failures.
Git regressions run both a single imported package and a complete example,
including a moved branch, explicit pin refresh and exact-commit cache recovery.
Iris certification/integration consumes the canonical project rather than a
second fixture implementation. The Airflow showcase uses package runtime for
its three installed-command Steps.

These tested boundaries do not establish APP PASS, privileged strict/native
qualification, or an Airflow scheduler/worker deployment. Completing the Iris
`purpose: cicd` Plan requires actual selected strict execution and retained
content-store artifacts; standalone training or an ordinary verification
wrapper cannot substitute for that evidence.

## Existing owners

| Concern | Owner |
| --- | --- |
| Package discovery and authored membership | Template discovery, source snapshots and Hashmarks; [catalog determinism](../development/TEMPLATE_CATALOG_DETERMINISM.md). |
| Compilation and inputs | Existing resolver/compiler; [input resolution](../development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md). |
| Python selection and preparation | Existing managed uv model/runtime; [native tool authority](../development/NATIVE_TOOL_AUTHORITY.md). |
| Git acquisition and source pins | Shared native Git preparation and [execution lock](../goon-lock.md). |
| Execution and results | Existing Fleet/Step/process lifecycle, [artifact retention](../development/TEMPLATE_ARTIFACT_RETENTION.md) and Game Tape. |
| Iris implementation | Canonical `ml/iris-train` project and its native tests. |

Repository instructions, current guides and template READMEs carry this same
contract. Existing focused checks and escalation rules apply; this decision
adds no parallel selector, runner or qualification gate.
