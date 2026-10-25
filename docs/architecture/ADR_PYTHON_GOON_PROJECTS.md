# ADR — Complete Python Goon projects

**Status:** Proposed implementation contract, 2026-10-25.
**Scope:** Atomic Goons that carry their own Python implementation; local projects first, Git sources later.
**Implementation status:** This document specifies intended behavior. It does not add runtime support. In particular, `runtime: python` and Git Goon selection below are proposals, not currently supported authoring syntax.

## Decision

Make the Goon directory a complete Python project. A developer can enter that
directory, use native uv, run its tests, and run its installed command. OH-Goon
executes the same implementation and entry point through its existing lifecycle.

The Plan stays small:

```yaml
execution:
  purpose: cicd

goons:
  - ml/iris-train
```

The Goon owns its input contract and Steps. Its `pyproject.toml` owns runtime
dependencies, development tools, build configuration and the installed entry
point. `run.yml` supplies caller values and invocation selection. The Plan does
not acquire Python dependency or build-system declarations.

The same package must later be usable from an explicitly selected Git
repository and revision. Acquisition changes how source arrives; it does not
change the package format, input semantics or execution lifecycle.

This requirement applies to Python Atomic Goons carrying implementation.
Composite Goons and Goons invoking tools in a selected target project do not
need dummy Python projects. Plans still order Goons; Atomic Goons own Steps;
Composite Goons own Goons. There is no additional runner or execution graph.

## Why this changes the current Iris example

At the [reviewed baseline](https://github.com/oh-goon/oh-goon/tree/4e05311913bd1f465968c4ba644989309ef5f964),
the [Iris Goon](../../templates/goons/ml/iris-train/goon.yml) already declares
all four typed inputs, defaults and constraints. Input ownership is correct.

The usability problem is the split package: the Goon and README live in the
catalog, while the Python project lives under
[the certification fixture](../../template-certification/fixtures/ml/iris/pyproject.toml).
The [catalog contract](../development/TEMPLATE_CATALOG_DETERMINISM.md)
currently permits exactly YAML and README. The managed uv contract also
selects a named dependency group. Moving files or changing dependency tables
alone cannot implement this proposal.

Current supported behavior remains documented in
[Goon authoring](../development/GOON_AUTHORING.md) until the implementation
and its owning documentation change together.

## 1. One complete project directory

For the proposed Iris package, the canonical home is
`templates/goons/ml/iris-train/`:

| File or directory | Responsibility |
| --- | --- |
| `goon.yml` | Typed public inputs, ordered Steps and declared artifacts. |
| `README.md` | Purpose, local development, command usage, inputs and outputs. |
| `pyproject.toml` | Python project metadata, runtime dependencies, development groups, entry point and build backend. |
| `uv.lock` | Native uv dependency resolution, committed with the project. |
| `src/iris_train/__init__.py` | Python package. |
| `src/iris_train/cli.py` | Installed `iris-train` command and argument handling. |
| `src/iris_train/training.py` | Training/evaluation logic, if separating it from the CLI improves the implementation. |
| `tests/` | Meaningful tests of training behavior, validation and output contracts. |

The Python package layout is illustrative; native build configuration owns
valid Python layouts. Mandatory package content is `goon.yml`, `README.md`,
`pyproject.toml`, `uv.lock` and the implementation/resources needed to install
and execute the declared command. The shipped Iris project also supplies tests.
The README retains the catalog heading `# ml/iris-train`.

The package must work after relocation without importing a certification
fixture, the OH-Goon backend, a cookbook checkout, parent source, or an
unrelated developer environment. Its internal code and resource dependencies
are contained in the project. External Python dependencies use native project
declarations and uv resolution. Undeclared sibling paths, ancestor uv
workspaces and escaping local dependencies are outside this standalone model.

Local `uv sync`, testing and training produce environments, caches and output
files. Those operations must not invalidate package discovery or add generated
state to source identity. Extend the existing authored-source membership and
Hashmarks boundaries to distinguish authored members from disposable state;
do not create another package hash/filter engine or a manual file manifest.

Move the Iris implementation to this one canonical home when implementing the
proposal. Certification and integration tests consume it there rather than
maintaining a second copy.

## 2. Inputs remain explicit in the Goon

Proposed `goon.yml`:

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

Here `runtime: python` explicitly selects the project in the directory
containing `goon.yml`. It does not select the target repository's project.
The command resolves in the prepared project environment. It is not an
ambient PATH lookup or an implicit shell.

The existing binder applies types, defaults and constraints before workload
execution. Plans expose unique unbound inputs and retain explicit
`with:`/`pass:` routing where needed. The caller may provide this fragment
under the existing `run.yml` input mapping:

```yaml
inputs:
  random_seed: 7
  minimum_accuracy_percent: 95
```

That fragment supplies values, not a second schema. Unknown names, missing
required values and invalid values fail through the existing input owner.
Secrets retain their existing explicit environment/secret boundary.

The Python CLI can require all four arguments, so it does not duplicate the
Goon's workflow defaults. Python still owns application validation and training
logic. Local use and OH-Goon call the same installed CLI with explicit values.
No OH-Goon SDK is needed by the Iris implementation.

## 3. One pyproject.toml, different dependency selections

Proposed Iris project configuration:

```toml
[project]
name = "iris-train"
version = "0.1.0"
readme = "README.md"
requires-python = ">=3.12"
dependencies = ["scikit-learn>=1.6,<2"]

[project.scripts]
iris-train = "iris_train.cli:main"

[dependency-groups]
dev = ["pytest", "ruff"]

[tool.uv]
default-groups = ["dev"]

[build-system]
requires = ["uv_build>=0.10.0,<0.11.0"]
build-backend = "uv_build"
```

Scikit-learn is a runtime dependency of this dedicated application. It does
not need an `ml` group solely to distinguish it from developer tools.
The version constraints illustrate the project shape; native uv authors the
actual lock during implementation.

| Use | Dependency selection |
| --- | --- |
| Local development | Install the project, runtime dependencies and the default `dev` group. |
| OH-Goon Iris training | Install the project and runtime dependencies; select no dependency groups by default. |
| A workload explicitly running tests or lint | Include the tools that workload requests through native dependency-group selection. |

The intended local experience, from this directory, is:

```sh
uv sync
uv run pytest
uv run ruff check .
uv run iris-train --random-seed 42 --test-size-percent 20 --minimum-accuracy-percent 90 --max-iterations 300
```

OH-Goon's runtime preparation has the native semantics of:

```sh
uv sync --locked --no-default-groups --no-editable
```

That command describes preparation inside an owned execution environment;
authors do not add it as a warm-up Step. The existing Python environment model
must support a base-only selection. Do not require an empty group to satisfy
the current named-group model.

Native uv owns resolution and stale-lock refusal. The same `uv.lock` can
contain developer dependencies without installing them for training.
Execution consumes the prepared selection: a later unqualified `uv run`
must not automatically reinstall default groups. Do not use `--only-group`
for base application execution, because it omits the project itself.
These distinctions follow [native uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/).

Use a separately owned runtime environment. Do not reuse or prune the
developer's `.venv` to obtain the minimum runtime. A test workload may
legitimately need pytest in CI; the minimum depends on the selected work,
not on a global rule that CI can never use development tools.

Keep `[build-system]` in the same project file. Building/installing the
project supplies its console entry point in both environments. Build
requirements are distinct from runtime dependencies and developer tools;
they are not removed merely because execution happens in CI. Follow
[native uv project and build configuration](https://docs.astral.sh/uv/concepts/projects/config/).
Do not claim that `uv.lock` alone pins every isolated build requirement.

## 4. One source capture and execution lifecycle

Keep these roots explicit in the resolved contract:

| Root | Meaning |
| --- | --- |
| Goon package source root | Captured implementation, Python metadata, lock and authored resources. |
| Target repository root | Selected workload subject; never inferred from a package cache directory. |
| Attempt working/output root | Owned writable execution state and produced artifacts. |

For Iris, relative output paths resolve under the attempt's writable project
working directory. They do not write into the source cache or original
developer checkout. Target-project Goons retain their explicit target source
semantics; package-owned Python Goons do not silently substitute that project.

The existing owners must perform the following sequence:

1. Resolve the exact Goon selection and capture its authored package contents.
   Bind code, configuration, lock and resources to the same source identity.
2. Compile the existing Plan/Goon graph and bind caller inputs once. Reject
   known invalid source, package and input facts at their responsible boundary.
3. Transport the resolved package and dependency selection through existing
   admission and native environment preparation. Native build/install is
   effectful work and retains the selected workload's policy.
4. Execute the installed command through the original Step/process owner.
   Preserve cancellation, cleanup, exit status and failure reporting.
5. Retain declared artifacts and report through the existing Game Tape.

Reuse Hashmarks for repository/source evidence and the existing execution-lock
owner for execution identity. The catalog's current YAML byte digest is not a
whole-package digest; do not silently change its meaning. Transport complete
package source identity explicitly. Changes to code, project configuration,
resources or the native lock must invalidate stale bound execution through the
existing freshness checks.

Local uncommitted work is identified by the captured working files, not
represented as clean Git HEAD. Generated environments and outputs are not
source identity. Receivers consume the captured source rather than reopening
the live directory or independently rediscovering configuration.

The small Plan's `purpose: cicd`, artifact retention and selected execution
profile keep their existing meanings. This proposal adds no global strict-host
qualification requirement and does not bypass any explicitly selected
guarantee. See [execution first](ADR_EXECUTION_FIRST_PROPORTIONAL_SECURITY.md).

## 5. Later: select the same project from GitHub

The later user experience is: select a GitHub example, inspect its declared
work and inputs, choose values, lock the source and run it through OH-Goon.
The user should not have to manually clone the repository, find a separate
fixture and assemble the example.

Support two explicit intents:

- Import one reusable Goon package into an existing Plan.
- Select a complete example containing a Plan and its local Goon packages.

For the first intent, these are illustrative source coordinates:

```yaml
git: https://github.com/your-org/oh-goon-examples.git
ref: v1.0.0
path: goons/ml/iris-train
```

Here `path` means the exact package directory containing `goon.yml`, not a
catalog root. This is not a supported `run.yml` or `from:` syntax. The later
implementation must define one canonical public selection shape. A complete
example additionally selects its Plan and authoring root explicitly; selecting
one Goon never searches for a nearby Plan.

Reuse the native Git preparation, pinning, cache and temporary-worktree
lifetime established for [Agent Skill sources](../AGENT_SKILLS.md#select-a-skill).
Share acquisition behavior without making a Goon an Agent Skill or routing
its execution through an Agent Step. `.goonskills.yml` remains the Skill
source contract.

Required source behavior:

- Record origin, requested ref, resolved commit and selected location through
  the existing execution-lock owner. `uv.lock` retains Python resolution;
  `goon.lock` retains selected source pins and resolved execution identity.
- Resolve a branch or tag at explicit acquisition/locking. Subsequent locked
  use and cache recovery consume that exact commit. Missing content fails
  clearly; it never selects another revision.
- Refresh source pins only through an explicit user action. Do not silently
  expand the Skill-specific `--update-skills` command to update Goon sources.
- Capture a complete example's Plan and relative Goon imports from the same
  selected revision. Keep exact names and explicit import origins. An omitted
  `from:` continues to mean the shipped catalog; remote/local packages do not
  shadow it.
- Preserve local/read-only inspection. Unavailable source can be reported as
  requiring acquisition; inspection must not hide a network fetch.
- Acquisition does not run project code, install dependencies, initialize
  submodules or execute Steps. Build/install and training occur at their
  existing authorized execution boundaries.
- Close temporary worktrees after capture, including failure/cancellation,
  while respecting active cache leases. Cache locations are disposable
  implementation details, not execution identity.
- Transport captured bytes once. The execution receiver does not refetch a
  branch, reread a mutable cache directory or select a different environment.
- Retain provenance sufficient to identify the package, origin, requested
  ref, resolved commit, selected path, dependency selection and actual outcome.

## Implementation order and acceptance

Implement the local project contract first. Design its source boundary so
Git acquisition can later supply the same captured package representation.
Do not add speculative source registries, duplicate resolvers or alternate
execution front doors.

| Increment | Required observable proof |
| --- | --- |
| Local package | A relocated Iris project supports native uv development, tests and the installed command without the certification fixture or OH-Goon backend source. |
| Local package | The small catalog Plan reaches the real Iris training Step through the same entry point, with validated inputs and genuine exit status. Admission alone is insufficient. |
| Local package | Training retains the declared metrics and parameter artifacts under existing retention rules, including the command's threshold-failure behavior. |
| Local package | Base-only execution installs necessary runtime dependencies, excludes pytest/Ruff unless selected, and leaves the developer environment untouched. |
| Local package | Running local sync/tests/training does not invalidate package discovery or include generated state in captured source. |
| Local package | Missing package files, invalid inputs, escaping source paths and stale locks fail at the responsible owner; OH-Goon does not silently repair or rewrite dependency authority. |
| Local package | Source capture, relocation and repeated imports preserve exact package identity, written order, input bindings, distinct attempt outputs and existing cancellation/cleanup. Changed source cannot reuse stale execution authority. |
| Git acquisition | The unchanged package works from a pinned Git directory through the same compiler, preparation and execution owners as local source. |
| Git acquisition | A complete example selects its Plan and contained packages at one revision without manual copying or implicit package lookup. |
| Git acquisition | Moving a branch/tag or clearing the cache does not advance a locked run; explicit refresh changes a reviewable source pin. |
| Git acquisition | Acquisition/inspection executes no project code, and failure/cancellation releases owned temporary worktrees without disrupting active captures. |

These are acceptance criteria for future implementation, not test results of
this documentation PR. Functional proof must include real command execution
and output verification; mocked preparation and admission-only tests do not
establish the intended experience.

## Owners to change during implementation

Extend existing ownership rather than adding a parallel stack:

| Concern | Existing owner to extend |
| --- | --- |
| Package discovery and authored membership | Template discovery/source membership, [catalog determinism](../development/TEMPLATE_CATALOG_DETERMINISM.md), and Hashmarks source evidence. |
| Authoring, compilation and inputs | [Goon authoring](../development/GOON_AUTHORING.md) and [input resolution/transport](../development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md). |
| Python environment selection and preparation | Existing managed uv model/runtime and [native tool authority](../development/NATIVE_TOOL_AUTHORITY.md). |
| Source acquisition and pins | Existing native Git source preparation, [Skill source contract](../AGENT_SKILLS.md#select-a-skill) and [execution lock](../goon-lock.md). |
| Execution, artifacts and results | Original Fleet/Step/process owners, [artifact retention](../development/TEMPLATE_ARTIFACT_RETENTION.md) and Game Tape. |
| Iris implementation and tests | The canonical Goon project; certification consumes that project without duplicating it. |

When runtime support lands, update the current package rules in `AGENTS.md`,
catalog/authoring guides and template READMEs in the same change. Update
release/source inclusion and generated API contracts if their actual boundaries
change. Use the repository's existing focused checks and escalation rules;
this design does not create another qualification gate.
