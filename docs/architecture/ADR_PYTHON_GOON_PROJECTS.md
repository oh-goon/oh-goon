# ADR — Complete Python Goon projects

**Status:** Implemented, 2026-10-25.
**Scope:** Atomic Goons carrying their own Python implementation, from local
packages or explicit pinned Git selections; the same captured-project owner also
supports explicitly selected independent application sources.

## Decision

A Python Goon directory is a complete standalone Python project. A developer
can enter it, use native uv, run its tests and invoke its installed command.
Oh-Goon installs the same captured implementation and runs the same entry point
through its existing Fleet/Step lifecycle.

The Plan remains small:

```yaml
execution:
  purpose: verification

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

## Independent application binding

An Atomic Goon may declare `application: {path: /absolute/application}` or
`application: {git: URL, ref: REF, path: exact/subdirectory}`. This selects source
independently of the Goon's authoring source and the actual registered effect
target. It extends the existing captured-project binding, retained delivery and
Command Step lifecycle rather than adding an application runner.

The independent application needs no Goon YAML, catalog identity, README or
Oh-Goon SDK. Native `pyproject.toml`/`uv.lock` requirements apply only when native
uv is selected. `runtime: python` selects managed uv and installed commands;
ordinary explicit commands also support captured applications without Python
metadata. The package-owned form below retains its colocated-project contract.

The source graph captures one complete bounded closure before execution. Exact
bytes and local or pinned-Git provenance travel through the existing project
archive and repository-owned retained-content references. The original Step
owners create a verified private application copy, select the native environment,
execute and collect declared outputs. Application files and acquisition caches
do not become writable target authority. Repeated occurrences and later Runs
retain separate attempt/output identities.
Independent application capture refuses live dotenv state before delivery or execution;
explicit environment/secret authority remains separate from source selection.
The existing source-policy owner permits non-live dotenv templates, without
using Git ignore rules or adding a content secret scanner.

The original lock owner stores application pins in the same execution-source
collection as Plan/Goon pins. `--refresh applications` refreshes applications
explicitly; other source refresh scopes cannot advance them. A repair candidate
can be recaptured through its original owner without changing an independently
captured verifier. Historical readers consume retained facts and never fetch the
current branch.

The existing registered-target route already supports a separately authored Goon
over an unchanged application repository with no Goon files. The new binding is
needed when the selected executable source must remain independent of that
target. A genuine target-free execution subject remains unsupported: Fleet
admission, repository/workspace authority, schedules, campaigns and Game Tape
require a coordinated subject decision. Do not satisfy that gap with a dummy
repository or a parallel execution lane. See
[authoring](../development/GOON_AUTHORING.md#independently-maintained-application-source).

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
resolution and build/install semantics. Frozen lock consumption is valid;
metadata freshness checks belong to an explicitly selected locked mode.
Contained native uv workspace members remain supported. Local dependency,
workspace member/exclusion and virtual lock-source paths must all stay inside
the captured package so relocation preserves their native project authority.

The existing source-snapshot and Hashmarks owners capture authored package
contents. Generated environments/caches, Python distribution metadata
(`*.egg-info`, `*.dist-info` and `.eggs`), declared output locations and
`goon.lock` are excluded by the canonical source-membership policy. Git ignore
files do not select authored membership. Local sync, tests and training therefore
do not add those generated files to package identity. Authored directories named
`build` or `dist` remain source unless an output declaration excludes them.
There is no separately authored file manifest or second package-content hash engine.

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
    retention: content_store
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
uv sync --frozen
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync iris-train --random-seed 42 --test-size-percent 20 --minimum-accuracy-percent 90 --max-iterations 300
```

Package preparation has native semantics of
`uv sync --frozen --no-default-groups --no-editable`, adding `--group` only
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

## Native lock mode and durable cutover

A Goon may select `uv: {lock_mode: frozen}` or `uv: {lock_mode: locked}`.
Omission selects frozen operation, and the compiler materializes that choice in
every resolved UV Step and derived requirement. The Iris and orders reference
Goons select frozen explicitly. Groups are independent of lock mode.

Frozen consumes the existing `uv.lock` without a metadata-freshness check or
rewrite. A pyproject/lock mismatch alone is not an Oh-Goon veto; unavailable
packages, unusable locks and real native build/application failures remain failures.
Locked asks native uv to reject when freshness would require changing the lock.
Neither selection refreshes dependencies or falls back to the other mode.
`goon.lock` still binds the chosen mode and exact captured source bytes, including
`pyproject.toml` and `uv.lock`; changing source requires explicit re-locking.

The selected mode reaches trusted preparation, isolated/remote preparation,
campaign phases, schedules and retained Plan readback unchanged. Already prepared
application execution uses `uv run --no-sync` without another mode flag or sync.
Attempt receipts retain actual native preparation facts: frozen freshness is
`not_checked`, successful settled locked preparation is `checked`, and a native
failure leaves locked freshness `not_established`. A nonzero exit does not by
itself prove a stale lock. Ordinary repository UV execution also owns its attempt
environment rather than reusing the developer's `.venv`.

The selected UV owner controls `UV_FROZEN`, `UV_LOCKED`, `UV_NO_SYNC` and
`UV_NO_PROJECT`; ambient switches cannot change the resolved project/mode, and
explicit imports of those switches are refused for managed UV Steps. Other native
UV configuration, credentials, certificates and transport settings retain their
native ownership.

The fixed native pre-admission issuer refuses an untrusted repository-owned UV
selection in locked mode. Native `uv lock --check --offline` can execute PEP 517
metadata/build hooks on the controller host, and this issuer cannot establish
that freshness inside the selected isolation boundary. It refuses before the
native check rather than signing a weaker proof. Trusted locked selections keep
their native freshness check; frozen untrusted selections retain exact byte/mode
identity without one. Package-owned runtimes remain outside repository UV
requirements and perform their selected preparation inside the original Step
boundary. The native issuer restriction is separate from the existing isolated
`uv sync --locked` execution capability.

Resolved Plan contract 46 introduced explicit lock mode; contract 47 separates
captured application source from native environment selection. Execution lock
version 2 uses one `execution_sources` collection for Goon, Plan and application
pins. Database schema 92 refuses older serialized Plans before decoding; no old
source spelling or lock format is accepted as an alias. Incompatible histories
are not corruption and are never reinterpreted as current selections. Quiesce the old writer before
switching runtimes, retain old evidence under its original runtime, and re-resolve
and re-lock authoring with the new runtime before new admission. Creating a new
state directory does not prove that an old process has stopped.

The Python project convention belongs to Python-runtime Goons and maintained
Python examples. Ordinary tool-invoking Goons still execute without fabricated
Python metadata. This change adds no Goon-less application source or implicit
target selection: external workflows remain in the existing Goon family and use
explicit target authority.

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

The Iris Plan selects trusted-local `purpose: verification` and retains its
declared metrics and model files through the existing collector/content store.
The original Run owner binds retained publication to the captured project's
allocated attempt workspace and real registered repository, after verified
process settlement and before scratch disposal. Artifact records describe
stored bytes; they do not add strict execution receipts. The public
`runs export-artifact` command selects the recorded Run/Step artifact and
verifies its retained identity under a read lease before exporting bytes.

Explicit `purpose: cicd` keeps strict preparation, isolation and publication
requirements. Ordinary target-workspace commands do not acquire retained
publication, and retained cache inputs and retries remain strict-only.
See [artifact retention](../development/TEMPLATE_ARTIFACT_RETENTION.md) and
[execution first](ADR_EXECUTION_FIRST_PROPORTIONAL_SECURITY.md).

An explicit strict `trust_repository_code: false` applies the selected isolation
boundary to native dependency installation and package builds as well as the
application command. Run preparation selects uv and its system Python without
running the project's build backend on the host. Native `uv sync` with the resolved frozen/locked mode runs
inside the original isolated Step process owner, with the Step's selected
environment, network and resource authority.

Captured package source stays read-only. Each attempt verifies a private build
copy in its writable cache and installs it non-editably into its own environment;
native build backends can write build state without changing captured source,
the developer's checkout or another attempt. Preparation and application failure,
cancellation and cleanup retain the original Step/Run lifecycle.

The existing untrusted-code isolation requirements still apply: application-kernel
or VM isolation, with VM-only deny-all networking for the current remote provider.
An unavailable backend refuses before build execution. Untrusted attempts start
with an empty owned uv cache and do not inherit the host's cache or credentials.
A deny-all workload must contain the dependencies needed by native uv, or use its
bundled build backend where applicable. Registry-only dependencies fail natively
when they cannot be acquired; execution never retries the build on the host.
Ordinary trusted execution keeps its existing preparation behavior. Successful
package installation does not establish APP or strict/native qualification.

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
version: 2
source:
  kind: git-plan
  git: https://github.com/your-org/workflows.git
  ref: main
  path: examples/orders
  plan: plan.yml
target: {repository: /absolute/path/to/project}
```

The Plan and its contained local imports come from the same selected revision.
Selecting one Goon never searches for a nearby Plan. The target remains an
explicit registered repository.

Native Git preparation, cache leases and temporary-worktree lifetime are
shared with [Skill sources](../AGENT_SKILLS.md#select-a-skill). Goons remain
Goons; `.goonskills.yml` remains the separate Skill source contract.

- The first `lock` explicitly acquires source and records origin, requested ref,
  selected path and resolved commit in `execution_sources`. Complete examples also
  record their Plan selection.
- Ordinary relocking and runs retain exact commits. A moving branch/tag or
  cache miss never advances a locked selection. Cache recovery may acquire the
  exact pinned commit; unavailable content fails clearly.
- All selected paths sharing one origin/ref use one captured commit, regardless
  of import order. Adding a package or selecting a Plan from that same source
  retains its existing pin until explicit source refresh.
- `workflow prepare run.yml --refresh goons` explicitly refreshes Goon/Plan source pins.
  `--refresh skills` refreshes Skill pins independently.
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
  Native Git identifies repository-context environment variables; acquisition
  clears those inherited contexts so an enclosing Git process cannot redirect
  its index/worktree operations. Native configuration and credentials remain selected.
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
Native namespace regressions execute a self-contained PEP 517 backend and its
installed command, proving writable private builds, immutable captured source,
unmounted host-file and loopback isolation, base-only installation and explicit locked freshness
refusal before application launch. Trusted repeated imports also build and run
separate private copies without changing their shared captured source.
Git regressions run both a single imported package and a complete example,
including a moved branch, explicit pin refresh and exact-commit cache recovery.
Iris certification/integration consumes the canonical project rather than a
second fixture implementation. The Airflow showcase uses package runtime for
its three installed-command Steps.

The namespace proof uses a shared host kernel; it does not qualify the stronger
application-kernel or VM boundary required for untrusted strict admission.
These tested boundaries do not establish APP PASS, privileged strict/native
qualification, or an Airflow scheduler/worker deployment. Explicit Iris
`purpose: cicd` execution still requires actual selected strict admission and
retained-content publication. Trusted-local verification establishes its own
recorded execution and retained bytes, and cannot substitute for strict evidence.

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
